#!/usr/bin/env python3
"""Compare local arXiv PDF pairs and save one validated JSON per pair."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

from tqdm import tqdm

if __package__:
    from .schema import SCHEMA, validate_result
    from . import api_transport
else:
    from schema import SCHEMA, validate_result
    import api_transport

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STOP = threading.Event()
PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: set[subprocess.Popen] = set()


class RequestStartGate:
    """Space request starts without changing the maximum number of workers."""
    def __init__(self, interval=0):
        self.interval = interval
        self.next_start = 0.0
        self.lock = threading.Lock()

    def wait(self, stop):
        while not stop.is_set():
            with self.lock:
                now = time.monotonic()
                delay = self.next_start - now
                if delay <= 0:
                    self.next_start = now + self.interval
                    return
            if stop.wait(delay):
                break
        raise api_transport.APIError('Interrupted while waiting to start a request')


START_GATE = RequestStartGate()


def retry_delay(attempt_number, metadata, base_delay, max_delay):
    """Bound backoff, and never shorten a valid server minimum wait."""
    hint = metadata.get('retry_after_seconds')
    if not isinstance(hint, (int, float)) or not math.isfinite(hint) or hint < 0:
        hint = None
    if hint is not None and hint > max_delay:
        return None
    ceiling = min(max_delay, base_delay * 2.0 ** min(max(0, attempt_number - 1), 1023))
    delay = random.uniform(ceiling * 0.75, ceiling)
    if hint is not None:
        delay = max(delay, hint)
        delay += random.uniform(0, min(1.0, max_delay - delay))
    return delay


@dataclass(frozen=True)
class Pair:
    folder: Path
    arxiv_id: str
    before: int
    after: int

    @property
    def name(self):
        return f'{self.arxiv_id}/v{self.before}_v{self.after}'

    @property
    def pdfs(self):
        return (self.folder / f'v{self.before}.pdf', self.folder / f'v{self.after}.pdf')

    def output_path(self, output):
        return output / self.arxiv_id / f'diff_v{self.before}_v{self.after}.json'


def load_pairs(input_path, papers=None, limit=None):
    """Read pair directories directly; metadata JSON files are optional."""
    input_path = input_path.resolve()
    if not input_path.is_dir():
        raise ValueError(f'Input directory does not exist: {input_path}')
    pattern = re.compile(r'v([1-9]\d*)_v([1-9]\d*)')
    folders = [input_path] if pattern.fullmatch(input_path.name) else input_path.glob('*/v*_v*')
    pairs = []
    for folder in folders:
        match = pattern.fullmatch(folder.name)
        if not folder.is_dir() or not match:
            continue
        aid = folder.parent.name
        if not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
            raise ValueError(f'Invalid arXiv directory: {folder.parent}')
        before, after = map(int, match.groups())
        if before >= after:
            raise ValueError(f'Expected earlier-to-later versions: {folder}')
        pairs.append(Pair(folder, aid, before, after))
    pairs.sort(key=lambda p: (p.arxiv_id, p.before, p.after))
    if papers:
        missing = set(papers) - {p.arxiv_id for p in pairs}
        if missing:
            raise ValueError(f'Unknown paper IDs: {sorted(missing)}')
        pairs = [p for p in pairs if p.arxiv_id in papers]
    if limit is not None:
        pairs = pairs[:limit]
    if not pairs:
        raise ValueError(f'No PDF pairs found in {input_path}')
    for pair in pairs:
        for pdf in pair.pdfs:
            if not pdf.is_file() or pdf.stat().st_size == 0:
                raise ValueError(f'Missing or empty PDF: {pdf}')
    return pairs


def make_prompt(pair, backend='codex', input_mode='pdf'):
    task = (HERE / 'prompts/compare.md').read_text(encoding='utf-8')
    rubric = (HERE / 'prompts/rubric.md').read_text(encoding='utf-8')
    before, after = pair.pdfs
    task = task.format(arxiv_id=pair.arxiv_id,
                       before_filename=before.name, before_path=before.resolve(),
                       after_filename=after.name, after_path=after.resolve())
    if backend == 'responses':
        for pdf in pair.pdfs:
            task = task.replace(str(pdf.resolve()), pdf.name)
        reading_instruction = 'Read both attached original PDFs completely, including figures, tables, references, and appendices.'
        if input_mode == 'file_ids':
            reading_instruction = (
                f'The first attached file is `{before.name}` (before); the second attached file is `{after.name}` (after). '
                + reading_instruction)
        task = task.replace(
            'Use the available PDF skill to read the two PDFs, choosing the tools and approach that suit the comparison.',
            reading_instruction)
        task = task.replace('The PDF skill supplies reading guidance; this task defines the output format.',
                            'This task defines the output format.')
    return f'{task}\n{rubric}\n'


def build_command(args, workdir):
    return [args.codex, 'exec', '--ignore-user-config', '--ephemeral',
            '--skip-git-repo-check', '--sandbox', 'workspace-write',
            '--model', args.model, '-c', f'model_reasoning_effort="{args.effort}"',
            '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"',
            '--cd', str(workdir), '--color', 'never',
            '--output-schema', str(workdir / 'schema.json'),
            '--output-last-message', str(workdir / 'response.json'), '-']


def terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


def request_stop(signum, frame):
    STOP.set()
    api_transport.cancel_active_requests()
    with PROCESS_LOCK:
        processes = list(ACTIVE_PROCESSES)
    for process in processes:
        terminate(process)


def task_environment(state_dir):
    """Give each CLI its own writable state and expose the installed PDF skill."""
    source_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    state_dir.mkdir(mode=0o700)
    auth = source_home / 'auth.json'
    if auth.is_file():
        (state_dir / 'auth.json').symlink_to(auth.resolve())
    configured = os.environ.get('CHECK_DIFF_PDF_SKILL')
    candidates = [Path(configured)] if configured else [source_home / 'skills/pdf/SKILL.md']
    if not configured:
        candidates.extend(source_home.glob('plugins/cache/*/pdf/*/skills/pdf/SKILL.md'))
    candidates = [p for p in candidates if p.is_file()]
    if candidates:
        skill = max(candidates, key=lambda p: p.stat().st_mtime)
        skills = state_dir / 'skills'
        skills.mkdir()
        (skills / 'pdf').symlink_to(skill.parent.resolve(), target_is_directory=True)
    env = os.environ.copy()
    env['CODEX_HOME'] = str(state_dir)
    for key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE',
                'CODEX_APP_TOOLS_PIPE_PATH'):
        env.pop(key, None)
    return env


def compare_pair(args, pair):
    """The model reads the two PDFs; all scratch files live in a temporary directory."""
    with tempfile.TemporaryDirectory(prefix='check_diff-') as tmp:
        workdir = Path(tmp) / 'work'
        workdir.mkdir()
        env = task_environment(Path(tmp) / 'cli_state')
        (workdir / 'schema.json').write_text(json.dumps(SCHEMA), encoding='utf-8')
        with (Path(tmp) / 'codex.log').open('w+', encoding='utf-8') as log:
            with PROCESS_LOCK:
                if STOP.is_set():
                    raise RuntimeError('Interrupted')
                process = subprocess.Popen(build_command(args, workdir), stdin=subprocess.PIPE,
                                           stdout=log, stderr=log, text=True, start_new_session=True, env=env)
                ACTIVE_PROCESSES.add(process)
            try:
                process.communicate(make_prompt(pair), timeout=args.timeout)
                if STOP.is_set():
                    raise RuntimeError('Interrupted')
                if process.returncode:
                    log.seek(0)
                    detail = log.read()[-3000:].strip()
                    raise RuntimeError(f'Codex exited {process.returncode}: {detail}')
            finally:
                terminate(process)
                with PROCESS_LOCK:
                    ACTIVE_PROCESSES.discard(process)
        result = json.loads((workdir / 'response.json').read_text(encoding='utf-8'))
        validate_result(result)
        return result


def save_result(path, result):
    validate_result(result)
    api_transport.atomic_write(path, {key: result[key] for key in SCHEMA['properties']})


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def pdf_inventory(pair):
    metadata_path = pair.folder / 'pair.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    inventory = []
    for side, version, pdf in zip(('before', 'after'), (pair.before, pair.after), pair.pdfs):
        digest = api_transport.file_sha256(pdf)
        entry = metadata.get(side, {})
        expected = entry.get('sha256')
        if expected and expected != digest:
            raise ValueError(f'PDF checksum differs from pair metadata: {pdf.name}')
        pages = entry.get('pages') if expected == digest else None
        pages = pages if isinstance(pages, int) and not isinstance(pages, bool) and pages > 0 else None
        inventory.append({'side': side, 'version': version, 'filename': pdf.name,
                          'sha256': digest, 'bytes': pdf.stat().st_size,
                          'page_count': pages,
                          'page_count_status': 'verified' if pages is not None else 'unresolved',
                          'page_count_unresolved_reason': None if pages is not None else 'No matching verified page-count metadata'})
    return inventory


def load_uploaded_inputs(manifest_path, pair, inventory, connection):
    """Verify file-ID provenance without uploading, deleting, or querying files."""
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 'check-diff-uploaded-pdfs-1'
            or not isinstance(manifest.get('files'), list)):
        raise ValueError('Unsupported uploaded-input manifest schema')
    base_url = manifest.get('base_url')
    if not isinstance(base_url, str) or base_url.rstrip('/') != connection.base_url.rstrip('/'):
        raise ValueError('Uploaded-input manifest endpoint does not match the API connection')
    entries = []
    for side, version, pdf, original in zip(('before', 'after'), (pair.before, pair.after), pair.pdfs, inventory):
        matches = [entry for entry in manifest['files'] if isinstance(entry, dict)
                   and entry.get('arxiv_id') == pair.arxiv_id and entry.get('version') == version]
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one uploaded PDF entry for {pair.arxiv_id}v{version}')
        entry = matches[0]
        if (entry.get('filename') != pdf.name or entry.get('status') != 'processed'
                or entry.get('sha256') != original['sha256'] or entry.get('bytes') != original['bytes']):
            raise ValueError(f'Uploaded PDF identity, status, or source bytes mismatch: {pair.arxiv_id}v{version}')
        file_id = entry.get('file_id')
        if not isinstance(file_id, str) or not re.fullmatch(r'file-[A-Za-z0-9_-]+', file_id):
            raise ValueError(f'Invalid uploaded PDF file ID: {pair.arxiv_id}v{version}')
        uploaded_at = entry.get('uploaded_at_utc')
        elapsed = entry.get('upload_seconds')
        if (not isinstance(uploaded_at, str) or not uploaded_at.strip() or isinstance(elapsed, bool)
                or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError(f'Missing or invalid upload timing provenance: {pair.arxiv_id}v{version}')
        entries.append({'side': side, **{key: entry[key] for key in (
            'arxiv_id', 'version', 'filename', 'file_id', 'bytes', 'sha256', 'status',
            'uploaded_at_utc', 'upload_seconds')}})
    provenance = {'manifest_path': str(manifest_path), 'manifest_sha256': api_transport.file_sha256(manifest_path),
                  'base_url': base_url.rstrip('/'), 'files': entries}
    return entries, provenance


def run_response_pair(args, pair):
    """Retain the original judgment schema and write API provenance separately."""
    started = time.monotonic()
    input_mode = getattr(args, 'input_mode', 'pdf')
    base_delay = getattr(args, 'retry_base_delay', 2.0)
    max_delay = getattr(args, 'retry_max_delay', 60.0)
    path = pair.output_path(args.output)
    receipt_path = path.with_suffix('.execution.json')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    prefix = path.with_name(f'{path.stem}.{stamp}')
    receipt = {'schema_version': 'check-diff-execution-1',
               'pair_id': f'{pair.arxiv_id}__v{pair.before}_to_v{pair.after}',
               'arxiv_id': pair.arxiv_id, 'versions': {'before': pair.before, 'after': pair.after},
               'backend': 'responses', 'model': args.model, 'reasoning_effort': args.effort,
               'requested_concurrency': args.workers,
               'request_start_interval_seconds': getattr(args, 'start_interval', 0),
               'retry_policy': {'max_retries': args.retries, 'base_delay_seconds': base_delay,
                                'max_delay_seconds': max_delay, 'backoff': 'exponential_with_jitter',
                                'server_delay_policy': 'minimum_or_defer'},
               'input_mode': {'pdf': 'original_pdf_files', 'file_ids': 'uploaded_original_pdf_files'}[input_mode],
               'started_at_utc': utc_now(),
               'ended_at_utc': None, 'elapsed_seconds': None, 'status': 'running',
               'attempts': [], 'result_file': path.name}
    connection = getattr(args, 'api_connection', None)
    try:
        if STOP.is_set():
            receipt['status'] = 'stopped'
            return 'stopped'
        if connection is None:
            connection = api_transport.load_connection(getattr(args, 'connection', None))
        prompt = make_prompt(pair, backend='responses', input_mode=input_mode)
        receipt['prompt_sha256'] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        receipt['schema_sha256'] = hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode('utf-8')).hexdigest()
        receipt['pdfs'] = pdf_inventory(pair)
        uploaded_files = None
        if input_mode == 'file_ids':
            uploaded_files, receipt['uploaded_inputs'] = load_uploaded_inputs(
                args.uploaded_inputs, pair, receipt['pdfs'], connection)
        if path.exists() and receipt_path.exists() and not args.overwrite:
            try:
                validate_result(json.loads(path.read_text(encoding='utf-8')))
                previous = json.loads(receipt_path.read_text(encoding='utf-8'))
                keys: tuple[str, ...] = ('backend', 'model', 'reasoning_effort', 'prompt_sha256', 'schema_sha256', 'pdfs', 'input_mode')
                if input_mode == 'file_ids':
                    keys += ('uploaded_inputs',)
                if previous.get('status') == 'completed' and all(previous.get(key) == receipt[key] for key in keys):
                    return 'skipped'
            except (ValueError, OSError):
                pass
        receipt['prompt_file'] = prefix.name + '.prompt.txt'
        api_transport.atomic_write(Path(str(prefix) + '.prompt.txt'), prompt, text=True)
        payload = api_transport.build_payload(prompt, pair.pdfs, SCHEMA, args.model, args.effort,
                                              uploaded_files=uploaded_files)
        api_transport.atomic_write(receipt_path, receipt)
        for number in range(1, args.retries + 2):
            delay = None
            attempt_started = time.monotonic()
            attempt = {'attempt': number, 'started_at_utc': utc_now(), 'status': 'running',
                       'start_delay_seconds': 0.0, 'request_started_at_utc': None,
                       'request_elapsed_seconds': None, 'retry_delay_seconds': None,
                       'retry_wait_seconds': 0.0, 'retry_stop_reason': None}
            receipt['attempts'].append(attempt)
            try:
                if STOP.is_set():
                    raise api_transport.APIError('Interrupted')
                wait_started = time.monotonic()
                try:
                    START_GATE.wait(STOP)
                finally:
                    attempt['start_delay_seconds'] = round(time.monotonic() - wait_started, 6)
                tqdm.write(f'START {pair.name} {args.model}/{args.effort} ({number}/{args.retries + 1})')
                request_started = time.monotonic()
                attempt['request_started_at_utc'] = utc_now()
                try:
                    result, metadata = api_transport.request_response(
                        connection, payload, args.timeout, STOP, Path(str(prefix) + f'.attempt-{number}'))
                finally:
                    attempt['request_elapsed_seconds'] = round(time.monotonic() - request_started, 6)
                attempt.update(metadata)
                if metadata.get('returned_model') != args.model:
                    raise api_transport.APIError('Responses API returned an unexpected model', metadata)
                if metadata.get('returned_effort') != args.effort:
                    raise api_transport.APIError('Responses API returned an unexpected reasoning effort', metadata)
                try:
                    validate_result(result)
                except ValueError as exc:
                    raise api_transport.APIError(f'Responses API output failed schema validation: {exc}',
                                                 metadata, retryable=True) from None
                if STOP.is_set():
                    raise api_transport.APIError('Interrupted')
                save_result(path, result)
                receipt['result_sha256'] = api_transport.file_sha256(path)
                attempt['status'] = 'completed'
                receipt.update(status='completed', response_id=metadata.get('response_id'),
                               returned_model=metadata.get('returned_model'),
                               returned_effort=metadata.get('returned_effort'), usage=metadata.get('usage'))
            except Exception as exc:
                if isinstance(exc, api_transport.APIError):
                    attempt.update(exc.metadata)
                attempt['retryable'] = isinstance(exc, api_transport.APIError) and exc.retryable
                attempt['status'] = 'stopped' if STOP.is_set() else 'failed'
                attempt['error'] = api_transport.redact(' '.join(str(exc).split()), connection)
                tqdm.write(f'FAILED {pair.name} ({number}/{args.retries + 1}): {attempt["error"]}')
                if STOP.is_set():
                    attempt['retry_stop_reason'] = 'cancelled'
                elif not attempt['retryable']:
                    attempt['retry_stop_reason'] = 'non_retryable_error'
                elif number > args.retries:
                    attempt['retry_stop_reason'] = 'retries_exhausted'
                else:
                    delay = retry_delay(number, attempt, base_delay, max_delay)
                    attempt['retry_delay_seconds'] = delay
                    if delay is None:
                        attempt['retry_stop_reason'] = 'retry_after_exceeds_max_delay'
            finally:
                attempt.update(ended_at_utc=utc_now(), elapsed_seconds=round(time.monotonic() - attempt_started, 6))
                api_transport.atomic_write(receipt_path, receipt)
            if receipt['status'] == 'completed':
                tqdm.write(f'DONE {pair.name} [{time.monotonic() - started:.1f}s]')
                return 'done'
            if STOP.is_set():
                receipt['status'] = 'stopped'
                return 'stopped'
            if delay is None:
                break
            tqdm.write(f'RETRY {pair.name} in {delay:.2f}s')
            wait_started = time.monotonic()
            try:
                if STOP.wait(delay):
                    attempt['retry_stop_reason'] = 'cancelled'
                    receipt['status'] = 'stopped'
                    return 'stopped'
            finally:
                attempt['retry_wait_seconds'] = round(time.monotonic() - wait_started, 6)
                api_transport.atomic_write(receipt_path, receipt)
        receipt['status'] = 'failed'
        return 'failed'
    except Exception as exc:
        receipt['status'] = 'stopped' if STOP.is_set() else 'failed'
        receipt['error'] = api_transport.redact(str(exc), connection) if connection else 'API preparation failed; check connection and input files'
        tqdm.write(f'FAILED {pair.name}: {receipt["error"]}')
        return receipt['status']
    finally:
        # A matching completed record is retained verbatim when resuming.
        if receipt['status'] != 'running' or receipt['attempts']:
            receipt.update(ended_at_utc=utc_now(), elapsed_seconds=round(time.monotonic() - started, 6))
            api_transport.atomic_write(receipt_path, receipt)
            api_transport.atomic_write(Path(str(prefix) + '.execution.json'), receipt)


def run_pair(args, pair):
    if getattr(args, 'backend', 'codex') == 'responses':
        return run_response_pair(args, pair)
    if STOP.is_set():
        return 'stopped'
    path = pair.output_path(args.output)
    if path.exists() and not args.overwrite:
        try:
            validate_result(json.loads(path.read_text(encoding='utf-8')))
            return 'skipped'
        except (ValueError, OSError) as exc:
            tqdm.write(f'REBUILD {pair.name}: {exc}')
    started = time.monotonic()
    for attempt in range(args.retries + 1):
        if STOP.is_set():
            return 'stopped'
        try:
            tqdm.write(f'START {pair.name} {args.model}/{args.effort} ({attempt + 1}/{args.retries + 1})')
            result = compare_pair(args, pair)
            if STOP.is_set():
                return 'stopped'
            save_result(path, result)
            tqdm.write(f'DONE {pair.name} [{time.monotonic() - started:.1f}s]')
            return 'done'
        except Exception as exc:
            if STOP.is_set():
                return 'stopped'
            detail = ' '.join(str(exc).split())
            tqdm.write(f'FAILED {pair.name} ({attempt + 1}/{args.retries + 1}): {detail}')
    return 'failed'


def run(args, pairs):
    global START_GATE
    START_GATE = RequestStartGate(getattr(args, 'start_interval', 0))
    counts = dict(done=0, skipped=0, failed=0, stopped=0)
    with tqdm(total=len(pairs), desc='check_diff', unit='pair') as progress:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            jobs = [pool.submit(run_pair, args, pair) for pair in pairs]
            for job in as_completed(jobs):
                counts[job.result()] += 1
                progress.set_postfix(counts, refresh=False)
                progress.update(1)
        tqdm.write(' / '.join(f'{key}={value}' for key, value in counts.items()))
    return 130 if STOP.is_set() else 1 if counts['failed'] else 0


def start_background(args, argv):
    log_path = args.log_file.resolve()
    pid_path = log_path.with_suffix('.pid')
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            os.kill(pid, 0)
        except (ValueError, ProcessLookupError):
            pass
        else:
            raise ValueError(f'A background run is already active: PID {pid}, log {log_path}')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-u', str(HERE / 'runner.py'),
               *[arg for arg in argv if arg != '--background']]
    with log_path.open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=Path.cwd(), stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, close_fds=True)
    pid_path.write_text(str(process.pid) + '\n', encoding='utf-8')
    print(f'Background run started: PID {process.pid}')
    print(f'Log: {log_path}')
    print(f'PID file: {pid_path}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, nargs='?', default=ROOT / 'input_data',
                        help='input_data directory or one <arxiv_id>/v1_v2 directory')
    parser.add_argument('--output', type=Path, default=ROOT / 'output')
    parser.add_argument('--paper', action='append', help='Select all pairs of an arXiv ID; repeatable')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--backend', default='codex', choices=('codex', 'responses'))
    parser.add_argument('--connection', type=Path, help='API connection JSON; alternatively set CHECK_DIFF_API_CONFIG_JSON')
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--effort', default='medium', choices=('low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--input-mode', choices=('pdf', 'file_ids'), default='pdf',
                        help='Original PDF attachments or previously uploaded original PDFs')
    parser.add_argument('--uploaded-inputs', type=Path, help='Verified uploaded-PDF manifest JSON; required for file_ids mode')
    parser.add_argument('--start-interval', type=float, default=0,
                        help='Responses API only: minimum seconds between request starts; default 0')
    parser.add_argument('--timeout', type=float, default=1800, help='Seconds per comparison attempt')
    parser.add_argument('--retries', type=int, default=None,
                        help='Additional attempts: default 3 for Responses, 1 for Codex; 0 disables retries')
    parser.add_argument('--retry-base-delay', type=float, default=2,
                        help='Responses retry backoff starts at this many seconds before jitter; default 2')
    parser.add_argument('--retry-max-delay', type=float, default=60,
                        help='Responses retry wait cap; longer server waits defer the pair; default 60')
    parser.add_argument('--codex', default=shutil.which('codex') or 'codex')
    parser.add_argument('--overwrite', action='store_true', help='Recompute existing results')
    parser.add_argument('--dry-run', action='store_true', help='List inputs and outputs without making model calls')
    parser.add_argument('--background', action='store_true', help='Detach the batch and write progress to a log')
    parser.add_argument('--log-file', type=Path, default=Path('/tmp') / f'{ROOT.name}_check_diff.log')
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if args.retries is None:
        args.retries = 3 if args.backend == 'responses' else 1
    if (args.workers < 1 or not math.isfinite(args.timeout) or args.timeout <= 0 or args.retries < 0 or
            (args.limit is not None and args.limit < 1)):
        parser.error('workers, timeout and limit must be positive; retries must be nonnegative')
    if (not math.isfinite(args.retry_base_delay) or not math.isfinite(args.retry_max_delay) or
            args.retry_base_delay <= 0 or args.retry_max_delay < args.retry_base_delay):
        parser.error('retry delays must be finite and positive; retry-max-delay must be >= retry-base-delay')
    if not math.isfinite(args.start_interval) or args.start_interval < 0:
        parser.error('start-interval must be finite and nonnegative')
    if args.start_interval and args.backend != 'responses':
        parser.error('start-interval applies only to the responses backend')
    if args.input_mode == 'file_ids' and (args.backend != 'responses' or args.uploaded_inputs is None):
        parser.error('file_ids input mode requires --backend responses and --uploaded-inputs')
    if args.uploaded_inputs is not None and args.input_mode != 'file_ids':
        parser.error('uploaded-inputs requires --input-mode file_ids')
    args.output = args.output.resolve()
    try:
        pairs = load_pairs(args.input, args.paper, args.limit)
        if args.dry_run:
            for pair in pairs:
                print(f'{pair.name} -> {pair.output_path(args.output)}')
            print(f'{len(pairs)} pairs')
            return 0
        if args.background:
            return start_background(args, argv)
        if args.backend == 'responses':
            args.api_connection = api_transport.load_connection(args.connection)
        STOP.clear()
        previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            return run(args, pairs)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())

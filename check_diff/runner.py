#!/usr/bin/env python3
"""Compare local arXiv PDF pairs with Codex and save one JSON per pair."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
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
else:
    from schema import SCHEMA, validate_result

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STOP = threading.Event()
PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES = set()


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


def make_prompt(pair):
    task = (HERE / 'prompts/compare.md').read_text(encoding='utf-8')
    rubric = (HERE / 'prompts/rubric.md').read_text(encoding='utf-8')
    before, after = pair.pdfs
    task = task.format(arxiv_id=pair.arxiv_id,
                       before_filename=before.name, before_path=before.resolve(),
                       after_filename=after.name, after_path=after.resolve())
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
    result = {key: result[key] for key in SCHEMA['properties']}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.stem}-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_pair(args, pair):
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
    parser.add_argument('--model', default='gpt-5.5')
    parser.add_argument('--effort', default='medium', choices=('low', 'medium', 'high', 'xhigh'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=1800, help='Seconds per comparison attempt')
    parser.add_argument('--retries', type=int, default=1)
    parser.add_argument('--codex', default=shutil.which('codex') or 'codex')
    parser.add_argument('--overwrite', action='store_true', help='Recompute existing results')
    parser.add_argument('--dry-run', action='store_true', help='List inputs and outputs without running Codex')
    parser.add_argument('--background', action='store_true', help='Detach the batch and write progress to a log')
    parser.add_argument('--log-file', type=Path, default=Path('/tmp') / f'{ROOT.name}_check_diff.log')
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if (args.workers < 1 or args.timeout <= 0 or args.retries < 0 or
            (args.limit is not None and args.limit < 1)):
        parser.error('workers, timeout and limit must be positive; retries must be nonnegative')
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

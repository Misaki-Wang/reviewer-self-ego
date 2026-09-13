#!/usr/bin/env python3
"""Review deduplicated paper versions with frozen prompts; one result JSON per paper."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
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
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if __package__:
    from . import token_usage
else:
    import token_usage

OA_VALUES = [1, 3, 5, 6, 8, 10]
SCHEMA = {'type': 'object', 'properties': {
    'oa': {'type': 'integer', 'enum': OA_VALUES},
    'strengths': {'type': 'array', 'items': {'type': 'string'}},
    'weaknesses': {'type': 'array', 'items': {'type': 'string'}},
}, 'required': ['oa', 'strengths', 'weaknesses'], 'additionalProperties': False}
STOP = threading.Event()
LOCK = threading.Lock()
PROCESSES = set()


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.stem, delete=False) as f:
        tmp = Path(f.name)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class Version:
    paper_id: str
    number: int
    sources: tuple

    def read(self):
        """One read per unique inode; verify non-hardlinked duplicate copies too."""
        seen = set()
        data = None
        checksum = None
        for path in self.sources:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            current = path.read_bytes()
            current_hash = sha(current)
            if data is None:
                if not current.startswith(b'%PDF-'):
                    raise ValueError(f'Not a PDF: {path}')
                data, checksum = current, current_hash
            elif current_hash != checksum:
                raise ValueError(f'Conflicting copies of {self.paper_id}/v{self.number}: {self.sources}')
        return data, checksum


def discover(root, papers=None, versions=None, limit=None):
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f'Input directory does not exist: {root}')
    pair_re = re.compile(r'v([1-9]\d*)_v([1-9]\d*)')
    paper_re = re.compile(r'\d{4}\.\d{4,5}')
    if pair_re.fullmatch(root.name):
        folders = [root]
    elif paper_re.fullmatch(root.name):
        folders = root.glob('v*_v*')
    else:
        folders = root.glob('*/v*_v*')
    found = {}
    pairs = 0
    for folder in sorted(folders):
        match = pair_re.fullmatch(folder.name)
        if not folder.is_dir() or not match:
            continue
        aid = folder.parent.name
        if papers and aid not in papers:
            continue
        if not paper_re.fullmatch(aid):
            raise ValueError(f'Invalid paper ID: {aid}')
        numbers = tuple(map(int, match.groups()))
        if numbers[0] >= numbers[1]:
            raise ValueError(f'Invalid version pair: {folder}')
        pairs += 1
        for number in numbers:
            path = folder / f'v{number}.pdf'
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'Missing or empty PDF: {path}')
            found.setdefault(aid, {}).setdefault(number, []).append(path)
    if papers and set(papers) - set(found):
        raise ValueError(f'Unknown paper IDs: {sorted(set(papers) - set(found))}')
    if not found:
        raise ValueError(f'No PDF pairs found: {root}')
    result = {}
    for aid in sorted(found)[:limit]:
        if versions and set(versions) - set(found[aid]):
            raise ValueError(f'{aid}: requested versions missing: {sorted(set(versions) - set(found[aid]))}')
        result[aid] = [Version(aid, number, tuple(paths))
                       for number, paths in sorted(found[aid].items())
                       if not versions or number in versions]
    return result, pairs


def load_prompts(folder, experiments=None, conditions=None):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    texts = {}
    for name, expected in manifest['files'].items():
        path = folder / name
        if path.parent.resolve() != folder.resolve():
            raise ValueError('Invalid prompt path')
        data = path.read_bytes()
        if sha(data) != expected:
            raise ValueError(f'Frozen prompt checksum mismatch: {path}')
        texts[name] = data.decode('utf-8')
    available = manifest['conditions']
    if conditions and set(conditions) - {c['id'] for c in available}:
        raise ValueError('Unknown condition ID')
    selected = [c for c in available if (not conditions or c['id'] in conditions)
                and (not experiments or c['experiment'] in experiments)]
    if not selected:
        raise ValueError('No conditions selected')
    return texts[manifest['system_file']], [(c['id'], texts[c['user_file']]) for c in selected]


def validate_result(result):
    if (not isinstance(result, dict) or set(result) != set(SCHEMA['required']) or
            type(result['oa']) is not int or result['oa'] not in OA_VALUES):
        raise ValueError(f'Expected oa, strengths, weaknesses with a valid integer OA: {result!r}')
    for key in ('strengths', 'weaknesses'):
        if not isinstance(result[key], list) or any(not isinstance(x, str) or not x.strip() for x in result[key]):
            raise ValueError(f'{key} must be a list of nonempty text items')
    return result


def render_user(template, other_model, pdf_path):
    if template.count('{PAPER_CONTENT}') != 1:
        raise ValueError('Expected one PAPER_CONTENT placeholder')
    if '{OTHER_MODEL_NAME}' in template:
        if not other_model:
            raise ValueError('--other-model is required for exp2_other_llm')
        template = template.replace('{OTHER_MODEL_NAME}', other_model)
    # No prose is added: a neutral local PDF path replaces the material placeholder.
    return template.replace('{PAPER_CONTENT}', str(pdf_path))


def pdf_skill_path(explicit=None):
    if explicit:
        path = Path(explicit).resolve()
        if path.is_dir():
            path = path / 'SKILL.md'
        if not path.is_file():
            raise ValueError(f'PDF skill not found: {path}')
        return path
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    candidates = [home / 'skills/pdf/SKILL.md',
                  *home.glob('plugins/cache/*/pdf/*/skills/pdf/SKILL.md')]
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        raise ValueError('No installed PDF skill found; provide --pdf-skill PATH')
    return max(candidates, key=lambda p: p.stat().st_mtime).resolve()


def permission_options(work, snapshot, skill):
    """No broad workspace or host read grant. The exact PDF is read-only."""
    filesystem = {':root': 'deny', ':minimal': 'read',
                  str(work.resolve()): 'write', str(snapshot.resolve()): 'read',
                  str(skill.parent.resolve()): 'read'}
    # Explicit interpreter/library exceptions, never the user's home or repository.
    for path in (Path(sys.executable), Path(sys.executable).resolve(), Path(sys.base_prefix),
                 Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'):
        if path.exists():
            filesystem[str(path.absolute())] = 'read'
    table = '{' + ', '.join(f'{json.dumps(path)}={json.dumps(access)}'
                            for path, access in filesystem.items()) + '}'
    return ['-c', 'default_permissions="review_only"',
            '-c', 'permissions.review_only.network.enabled=false',
            '-c', 'permissions.review_only.filesystem=' + table]


def build_command(args, work, system, snapshot):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'
    path = ':'.join(str(p) for p in (runtime / 'bin/override', runtime / 'python/bin',
                                    Path('/usr/bin'), Path('/bin')) if p.is_dir())
    tool_environment = '{' + ', '.join(f'{json.dumps(k)}={json.dumps(v)}' for k, v in
        {'PATH': path, 'TMPDIR': str(work), 'PYTHONNOUSERSITE': '1'}.items()) + '}'
    return [args.codex, 'exec', '--json', '--strict-config', '--ignore-user-config', '--ephemeral',
            '--skip-git-repo-check',
            '--model', args.model, '-c', 'model_reasoning_effort=' + json.dumps(args.effort),
            '-c', 'developer_instructions=' + json.dumps(system),
            '-c', 'web_search="disabled"',
            *permission_options(work, snapshot, args.pdf_skill),
            '-c', 'mcp_servers={}',
            '-c', 'features.apps=false', '-c', 'features.plugins=false',
            '-c', 'features.browser_use=false', '-c', 'features.computer_use=false',
            '-c', 'features.multi_agent=false', '-c', 'features.memories=false',
            '-c', 'features.workspace_dependencies=false', '-c', 'features.hooks=false',
            '-c', 'features.skip_host_skill_discovery=true',
            '-c', 'shell_environment_policy.inherit="none"',
            '-c', 'shell_environment_policy.set=' + tool_environment,
            '-c', 'allow_login_shell=false', '-c', 'features.shell_snapshot=false',
            '-c', 'approval_policy="never"', '-c', 'project_doc_max_bytes=0',
            '--cd', str(work), '--color', 'never', '--output-schema', str(work / 'schema.json'),
            '--output-last-message', str(work / 'response.json'), '-']


def terminate(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def stop(signum, frame):
    STOP.set()
    with LOCK:
        active = list(PROCESSES)
    for process in active:
        terminate(process)


def call_codex(args, system, template, snapshot, work, attempt):
    work.mkdir()
    # Fresh cwd/session per review, with no paper/version labels or other results.
    manuscript = work / 'manuscript.pdf'
    manuscript.symlink_to(snapshot)
    skills = work / '.agents/skills'
    skills.mkdir(parents=True)
    (skills / 'pdf').symlink_to(args.pdf_skill.parent, target_is_directory=True)
    (work / 'schema.json').write_text(json.dumps(SCHEMA))
    user = render_user(template, args.other_model, manuscript)
    attempt['user_message'] = user
    env = os.environ.copy()
    # Tool subprocesses may create scratch files here, not in a shared /tmp.
    env['TMPDIR'] = str(work)
    for key in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE',
                'CODEX_APP_TOOLS_PIPE_PATH'):
        env.pop(key, None)
    process = None
    with (work / 'events.jsonl').open('w+', encoding='utf-8') as events, \
            (work / 'stderr.log').open('w+', encoding='utf-8') as errors:
        try:
            with LOCK:
                if STOP.is_set():
                    raise RuntimeError('Interrupted')
                process = subprocess.Popen(build_command(args, work, system, snapshot), stdin=subprocess.PIPE,
                                           stdout=events, stderr=errors, text=True, env=env,
                                           start_new_session=True)
                PROCESSES.add(process)
            process.communicate(user, timeout=args.timeout)
            if STOP.is_set():
                raise RuntimeError('Interrupted')
            if process.returncode:
                raise RuntimeError(f'Codex exited {process.returncode}')
            return validate_result(json.loads((work / 'response.json').read_text()))
        finally:
            if process:
                terminate(process)
                with LOCK:
                    PROCESSES.discard(process)
                attempt['exit_code'] = process.returncode
            events.flush()
            attempt['token_usage'] = token_usage.read_events(events)
            events.seek(0)
            activity = []
            for line in events:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get('item', {})
                if event.get('type') == 'item.completed' and item.get('type') == 'command_execution':
                    activity.append({k: item[k] for k in ('type', 'command', 'exit_code', 'status') if k in item})
            attempt['command_activity'] = activity
            attempt['permission_profile'] = 'review_only'
            errors.seek(0)
            attempt['stderr_tail'] = errors.read()[-4000:]
            response = work / 'response.json'
            if response.is_file():
                attempt['raw_response'] = response.read_text()


def fingerprint(args, system, template, checksum):
    return sha(json.dumps({'system': system, 'user_template': template,
                           'other_model': args.other_model if '{OTHER_MODEL_NAME}' in template else None,
                           'pdf_sha256': checksum, 'model': args.model, 'effort': args.effort,
                           'codex_executable': str(Path(shutil.which(args.codex) or args.codex).resolve()),
                           'schema': SCHEMA, 'adapter_version': 2,
                           'file_access': 'review_only_minimal_runtime',
                           'pdf_skill_sha256': args.pdf_skill_sha256}, sort_keys=True).encode())


def run_paper(args, aid, versions, system, conditions):
    path = args.output / aid / 'reviews.json'
    document = {'format_version': 1, 'paper_id': aid, 'versions': {}}
    if path.exists():
        document = json.loads(path.read_text())
        if document.get('format_version') != 1 or document.get('paper_id') != aid:
            raise ValueError(f'Incompatible result file: {path}')
    counts = dict(completed=0, skipped=0, failed=0)
    with tempfile.TemporaryDirectory(prefix='review-experiment-') as temp:
        root = Path(temp)
        for version in versions:
            if STOP.is_set():
                break
            data, checksum = version.read()
            snapshot = root / 'snapshot.pdf'
            snapshot.write_bytes(data)
            del data
            record = document['versions'].setdefault(f'v{version.number}', {'reviews': {}})
            if record.get('sha256') not in (None, checksum):
                # Never mix outputs for two different PDFs under the same version.
                record['reviews'] = {}
            record.update(sha256=checksum, source_paths=[str(p) for p in version.sources])
            for condition, template in conditions:
                for replicate in range(1, args.replicates + 1):
                    if STOP.is_set():
                        break
                    key = f'{condition}/r{replicate}'
                    mark = fingerprint(args, system, template, checksum)
                    previous = record['reviews'].get(key, {})
                    if not args.overwrite and previous.get('fingerprint') == mark and previous.get('status') == 'completed':
                        try:
                            validate_result(previous['result'])
                        except (ValueError, KeyError):
                            pass
                        else:
                            counts['skipped'] += 1
                            continue
                    review = dict(condition=condition, replicate=replicate, fingerprint=mark,
                                  model=args.model, effort=args.effort, codex_executable=args.codex,
                                  status='failed', attempts=[])
                    record['reviews'][key] = review
                    for number in range(args.retries + 1):
                        attempt = dict(started_at=now(), status='failed')
                        review['attempts'].append(attempt)
                        work = root / ('call-' + uuid.uuid4().hex)
                        print(f'START {aid}/v{version.number} {key} attempt={number + 1}', flush=True)
                        started = time.monotonic()
                        try:
                            review['result'] = call_codex(args, system, template, snapshot, work, attempt)
                            review['status'] = attempt['status'] = 'completed'
                        except Exception as exc:
                            attempt['error'] = f'{type(exc).__name__}: {exc}'
                            print(f'FAILED {aid}/v{version.number} {key}: {exc}', flush=True)
                        finally:
                            attempt.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
                            review['token_usage'] = token_usage.summarize(review['attempts'])
                            document.update(updated_at=now(), latest_selection_status='in_progress')
                            save_json(path, document)
                            shutil.rmtree(work, ignore_errors=True)
                        if review['status'] == 'completed' or STOP.is_set():
                            break
                    counts['completed' if review['status'] == 'completed' else 'failed'] += 1
        document.update(updated_at=now(), latest_selection_counts=counts,
                        latest_selection_status='stopped' if STOP.is_set() else 'failed' if counts['failed'] else 'completed')
        save_json(path, document)
    print(f'PAPER {aid}: {counts}', flush=True)
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', nargs='?', type=Path, default=ROOT / 'input_data')
    ap.add_argument('--output', type=Path, default=HERE / 'results')
    ap.add_argument('--prompts', type=Path, default=HERE / 'prompts')
    ap.add_argument('--paper', action='append')
    ap.add_argument('--version', type=int, action='append')
    ap.add_argument('--limit-papers', type=int)
    ap.add_argument('--experiment', choices=['1', '2', '3'], action='append')
    ap.add_argument('--condition', action='append')
    ap.add_argument('--other-model', help='Explicit author model name for exp2_other_llm')
    ap.add_argument('--model', default='gpt-5.5')
    ap.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh'])
    ap.add_argument('--codex', default=shutil.which('codex') or 'codex')
    ap.add_argument('--pdf-skill', type=Path)
    ap.add_argument('--workers', type=int, default=1, help='Concurrent papers (each paper runs sequentially)')
    ap.add_argument('--replicates', type=int, default=1)
    ap.add_argument('--timeout', type=float, default=1800)
    ap.add_argument('--retries', type=int, default=1)
    ap.add_argument('--overwrite', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='Inventory only, no PDF reads or model calls')
    args = ap.parse_args(argv)
    if (min(args.workers, args.replicates, args.timeout) <= 0 or args.retries < 0 or
            (args.limit_papers is not None and args.limit_papers < 1) or
            (args.version and min(args.version) < 1)):
        ap.error('Counts/timeout must be positive; retries must be nonnegative')
    args.output = args.output.resolve()
    try:
        papers, pair_count = discover(args.input, args.paper, args.version, args.limit_papers)
        system, conditions = load_prompts(args.prompts, args.experiment, args.condition)
        version_count = sum(map(len, papers.values()))
        inventory = dict(papers=len(papers), discovered_pairs=pair_count, unique_versions=version_count,
                         conditions=[c[0] for c in conditions], replicates=args.replicates,
                         planned_reviews=version_count * len(conditions) * args.replicates,
                         versions_by_paper={aid: [v.number for v in vs] for aid, vs in papers.items()})
        if args.dry_run:
            print(json.dumps(inventory, indent=2))
            return 0
        if any('{OTHER_MODEL_NAME}' in t for _, t in conditions):
            if not args.other_model or args.other_model.casefold() == args.model.casefold():
                raise ValueError('Provide --other-model different from the evaluator model')
        args.pdf_skill = pdf_skill_path(args.pdf_skill)
        args.pdf_skill_sha256 = sha(args.pdf_skill.read_bytes())
        if not shutil.which(args.codex):
            raise ValueError(f'Codex executable not found: {args.codex}')
        args.output.mkdir(parents=True, exist_ok=True)
        with (args.output / '.runner.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another runner is using this output directory')
            STOP.clear()
            handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
            summary = dict(inventory=inventory, started_at=now(), papers={}, errors={})
            try:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    futures = {pool.submit(run_paper, args, aid, vs, system, conditions): aid
                               for aid, vs in papers.items()}
                    for future in as_completed(futures):
                        aid = futures[future]
                        try:
                            summary['papers'][aid] = future.result()
                        except Exception as exc:
                            summary['errors'][aid] = str(exc)
                            print(f'PAPER FAILED {aid}: {exc}', file=sys.stderr)
                        save_json(args.output / 'run_summary.json', summary)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            failed = bool(summary['errors']) or any(c['failed'] for c in summary['papers'].values())
            summary.update(finished_at=now(), status='stopped' if STOP.is_set() else 'failed' if failed else 'completed')
            save_json(args.output / 'run_summary.json', summary)
            return 130 if STOP.is_set() else 1 if failed else 0
    except (ValueError, OSError) as exc:
        ap.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

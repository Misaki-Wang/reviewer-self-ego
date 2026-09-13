#!/usr/bin/env python3
"""Review deduplicated paper versions with frozen prompts; one result JSON per paper."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import importlib.util
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
# Resolve against this source root so frozen runs cannot import a live helper.
_usage_spec = importlib.util.spec_from_file_location('_codex_token_usage', ROOT / 'utility/token_usage.py')
token_usage = importlib.util.module_from_spec(_usage_spec)
_usage_spec.loader.exec_module(token_usage)

if not __package__:
    sys.path.insert(0, str(ROOT))
from review_experiment import source_profiles
if Path(source_profiles.__file__).resolve() != (HERE / 'source_profiles.py').resolve():
    raise ImportError('Source profile helper differs from this runner source root')
load_prompts = source_profiles.load_prompts
resolve_sources = source_profiles.resolve_sources
validate_result = source_profiles.validate_result

STOP = threading.Event()
DRAIN = threading.Event()
RESULT_LOCK = threading.Lock()
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


def permission_options(work, snapshot, skill, codex=None, tool_runtime=None, ripgrep=None):
    """No broad workspace or host read grant. The exact PDF is read-only."""
    filesystem = {':root': 'deny', ':minimal': 'read',
                  str(work.resolve()): 'write', str(snapshot.resolve()): 'read',
                  str(skill.parent.resolve()): 'read'}
    # Explicit interpreter/library exceptions, never the user's home or repository.
    for path in (Path(sys.executable), Path(sys.executable).resolve(), Path(sys.base_prefix),
                 Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'):
        if path.exists():
            filesystem[str(path.absolute())] = 'read'
    executable = shutil.which(codex or 'codex')
    if executable:
        # Linux sandbox setup re-executes this exact binary inside its filesystem.
        filesystem[str(Path(executable).resolve())] = 'read'
    if tool_runtime:
        for name in ('python_vendor', 'ghostscript-9.55.0'):
            filesystem[str((tool_runtime / name).resolve())] = 'read'
    if ripgrep:
        filesystem[str(Path(ripgrep).resolve())] = 'read'
    table = '{' + ', '.join(f'{json.dumps(path)}={json.dumps(access)}'
                            for path, access in filesystem.items()) + '}'
    return ['-c', 'default_permissions="review_only"',
            '-c', 'permissions.review_only.network.enabled=false',
            '-c', 'permissions.review_only.filesystem=' + table]


def build_command(args, work, system, snapshot):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'
    path = ':'.join(str(p) for p in (runtime / 'bin/override', runtime / 'python/bin',
                                    Path('/usr/bin'), Path('/bin')) if p.is_dir())
    environment = {'PATH': path, 'TMPDIR': str(work), 'PYTHONNOUSERSITE': '1'}
    tool_runtime = getattr(args, 'tool_runtime', None)
    if tool_runtime:
        gs = tool_runtime / 'ghostscript-9.55.0'
        environment['PATH'] = str(gs / 'bin') + ':' + path
        environment['PYTHONPATH'] = str(tool_runtime / 'python_vendor')
        environment['GS_LIB'] = ':'.join(str(p) for p in (
            gs / 'share/ghostscript/9.55.0/Resource/Init',
            gs / 'share/ghostscript/9.55.0/lib',
            gs / 'share/ghostscript/9.55.0/Resource/Font'))
    ripgrep = getattr(args, 'ripgrep', None)
    if ripgrep:
        environment['PATH'] = str(ripgrep.parent) + ':' + environment['PATH']
    tool_environment = '{' + ', '.join(f'{json.dumps(k)}={json.dumps(v)}'
                                      for k, v in environment.items()) + '}'
    return [args.codex, 'exec', '--json', '--strict-config', '--ignore-user-config', '--ephemeral',
            '--skip-git-repo-check',
            '--model', args.model, '-c', 'model_reasoning_effort=' + json.dumps(args.effort),
            '-c', 'developer_instructions=' + json.dumps(system),
            '-c', 'web_search="disabled"',
            *permission_options(work, snapshot, args.pdf_skill, args.codex, tool_runtime, ripgrep),
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


def call_codex(args, profile, template, snapshot, work, attempt):
    work.mkdir()
    # Fresh cwd/session per review, with no paper/version labels or other results.
    manuscript = work / 'manuscript.pdf'
    manuscript.symlink_to(snapshot)
    skills = work / '.agents/skills'
    skills.mkdir(parents=True)
    (skills / 'pdf').symlink_to(args.pdf_skill.parent, target_is_directory=True)
    (work / 'schema.json').write_text(json.dumps(profile.schema))
    user = render_user(template, args.other_model, manuscript)
    attempt['user_message'] = user
    attempt['ripgrep'] = getattr(args, 'ripgrep_metadata', None)
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
                process = subprocess.Popen(build_command(args, work, profile.text, snapshot), stdin=subprocess.PIPE,
                                           stdout=events, stderr=errors, text=True, env=env,
                                           start_new_session=True)
                PROCESSES.add(process)
            process.communicate(user, timeout=args.timeout)
            if STOP.is_set():
                raise RuntimeError('Interrupted')
            if process.returncode:
                raise RuntimeError(f'Codex exited {process.returncode}')
            return validate_result(json.loads((work / 'response.json').read_text()), profile.schema)
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


def fingerprint(args, profile, template, checksum):
    return sha(json.dumps({'system': profile.text, 'source_id': profile.source_id,
                           'score_scale': profile.metadata(), 'user_template': template,
                           'other_model': args.other_model if '{OTHER_MODEL_NAME}' in template else None,
                           'pdf_sha256': checksum, 'model': args.model, 'effort': args.effort,
                           'codex_executable': str(Path(shutil.which(args.codex) or args.codex).resolve()),
                           'schema': profile.schema, 'adapter_version': 3,
                           'file_access': 'review_only_minimal_runtime',
                           'tool_runtime': str(args.tool_runtime) if getattr(args, 'tool_runtime', None) else None,
                           'ripgrep': getattr(args, 'ripgrep_metadata', None),
                           'pdf_skill_sha256': args.pdf_skill_sha256}, sort_keys=True).encode())


def read_document(path, aid, source_id=None):
    if not path.exists():
        document = {'format_version': 1, 'paper_id': aid, 'versions': {}}
    else:
        document = json.loads(path.read_text())
        if document.get('format_version') != 1 or document.get('paper_id') != aid:
            raise ValueError(f'Incompatible result file: {path}')
    if source_id is not None:
        saved_source = document.get('source_id')
        has_reviews = any(v.get('reviews') for v in document['versions'].values())
        if saved_source not in (None, source_id) or (saved_source is None and has_reviews):
            raise ValueError(f'Result source is missing or differs for {aid}; use a fresh output directory')
        document['source_id'] = source_id
    return document


def save_review(path, aid, version, review):
    # Each worker owns one cell. Merge against the latest document while locked;
    # never publish another worker's stale paper snapshot.
    with RESULT_LOCK:
        document = read_document(path, aid, review['source_id'])
        document['versions'][f'v{version.number}']['reviews'][
            f"{review['condition']}/r{review['replicate']}"] = review
        document.update(updated_at=now(), latest_selection_status='in_progress')
        save_json(path, document)


def save_selection(path, aid, counts, status):
    with RESULT_LOCK:
        document = read_document(path, aid)
        document.update(updated_at=now(), latest_selection_counts=dict(counts),
                        latest_selection_status=status)
        save_json(path, document)


def drain(signum, frame):
    """Stop new admissions while allowing active calls to finish normally."""
    DRAIN.set()


def run_review(args, aid, version, payload, profile, condition, template, replicate):
    if STOP.is_set():
        return None
    data, checksum = payload
    path = args.output / aid / 'reviews.json'
    key = f'{condition}/r{replicate}'
    review = dict(condition=condition, replicate=replicate,
                  fingerprint=fingerprint(args, profile, template, checksum),
                  source_id=profile.source_id, system_prompt=profile.metadata(),
                  model=args.model, effort=args.effort, codex_executable=args.codex,
                  status='failed', attempts=[])
    with tempfile.TemporaryDirectory(prefix='review-experiment-', dir=args.output) as temp:
        root = Path(temp)
        snapshot = root / 'snapshot.pdf'
        snapshot.write_bytes(data)
        for number in range(args.retries + 1):
            if STOP.is_set():
                break
            attempt = dict(started_at=now(), status='failed')
            review['attempts'].append(attempt)
            work = root / ('call-' + uuid.uuid4().hex)
            print(f'START {aid}/v{version.number} {key} attempt={number + 1}', flush=True)
            started = time.monotonic()
            try:
                review['result'] = call_codex(args, profile, template, snapshot, work, attempt)
                review['status'] = attempt['status'] = 'completed'
            except Exception as exc:
                attempt['error'] = f'{type(exc).__name__}: {exc}'
                print(f'FAILED {aid}/v{version.number} {key}: {exc}', flush=True)
            finally:
                attempt.update(finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 3))
                review['token_usage'] = token_usage.summarize(review['attempts'])
                save_review(path, aid, version, review)
                shutil.rmtree(work, ignore_errors=True)
            if review['status'] == 'completed' or STOP.is_set():
                break
    return review['status'] if review['attempts'] else None


def prepare_reviews(args, papers, profiles, conditions):
    """Read each version once, retain immutable bytes, and skip valid prior cells."""
    queues, counts = [], {}
    documents = {aid: read_document(args.output / aid / 'reviews.json', aid, profiles[aid].source_id)
                 for aid in papers}
    for aid, versions in papers.items():
        path = args.output / aid / 'reviews.json'
        document = documents[aid]
        profile = profiles[aid]
        queue = deque()
        counts[aid] = dict(completed=0, skipped=0, failed=0)
        for version in versions:
            payload = version.read()
            checksum = payload[1]
            record = document['versions'].setdefault(f'v{version.number}', {'reviews': {}})
            if record.get('sha256') not in (None, checksum):
                record['reviews'] = {}
            record.update(sha256=checksum, source_paths=[str(p) for p in version.sources])
            for condition, template in conditions:
                for replicate in range(1, args.replicates + 1):
                    previous = record['reviews'].get(f'{condition}/r{replicate}', {})
                    mark = fingerprint(args, profile, template, checksum)
                    if (not args.overwrite and previous.get('fingerprint') == mark
                            and previous.get('status') == 'completed'):
                        try:
                            validate_result(previous['result'], profile.schema)
                        except (ValueError, KeyError):
                            pass
                        else:
                            counts[aid]['skipped'] += 1
                            continue
                    queue.append((aid, version, payload, profile, condition, template, replicate))
        save_json(path, document)
        queues.append(queue)
    # Keep paper admissions balanced while preserving each paper's queue order.
    pending = deque()
    while any(queues):
        for queue in queues:
            if queue:
                pending.append(queue.popleft())
    return pending, counts


def validate_output(input_root, output, papers=()):
    """Keep resumable result paths separate from inputs and protected project files."""
    output = output.absolute()
    for path in (output, *output.parents):
        if path.is_symlink():
            raise ValueError(f'Output path must not traverse a symlink: {path}')
        if path.exists() and not path.is_dir():
            raise ValueError(f'Output path must be a directory: {path}')
    output = output.resolve()
    input_root = input_root.resolve()
    if output == input_root or output in input_root.parents or input_root in output.parents:
        raise ValueError('Input and output directories must not overlap')
    project = ROOT.resolve()
    protected = [project / name for name in
                 ('review_experiment', 'check_diff', 'utility', 'tests', 'data', 'html', '.git',
                  'res/archive', 'res/.migration')]
    for path in protected:
        if output == path or output in path.parents or path in output.parents:
            raise ValueError(f'Output overlaps a protected project path: {path}')
    targets = [(output / '.runner.lock', False), (output / 'run_summary.json', False)]
    for aid in papers:
        targets.extend([(output / aid, True), (output / aid / 'reviews.json', False)])
    for path, directory in targets:
        if path.is_symlink():
            raise ValueError(f'Output target must not be a symlink: {path}')
        if path.exists() and not (path.is_dir() if directory else path.is_file()):
            raise ValueError(f'Output target must be a {"directory" if directory else "file"}: {path}')
    return output


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('input', type=Path, help='Explicit prepared <paper>/vX_vY PDF tree; not the raw data index/store')
    ap.add_argument('--output', type=Path, required=True, help='New or resumable result directory, normally res/<run-id>')
    ap.add_argument('--prompts', type=Path, default=HERE / 'prompts')
    ap.add_argument('--source', help='Explicit source ID; otherwise resolve each paper from input provenance/catalog')
    ap.add_argument('--source-metadata', type=Path, help='Explicit prepared scope, source shard or source catalog JSON')
    ap.add_argument('--paper', action='append')
    ap.add_argument('--version', type=int, action='append')
    ap.add_argument('--limit-papers', type=int)
    ap.add_argument('--experiment', choices=['1', '2', '3'], action='append')
    ap.add_argument('--condition', action='append')
    ap.add_argument('--other-model', help='Explicit author model name for exp2_other_llm')
    ap.add_argument('--model', default='gpt-5.5')
    ap.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--codex', default=shutil.which('codex') or 'codex')
    bundled_rg = ROOT / 'utility/.runtime/ripgrep/rg'
    ap.add_argument('--ripgrep', type=Path,
                    default=bundled_rg if bundled_rg.is_file() else shutil.which('rg'),
                    help='Exact rg executable; defaults to the project runtime, then PATH')
    ap.add_argument('--pdf-skill', type=Path)
    ap.add_argument('--tool-runtime', type=Path, help='Explicit existing python_vendor and ghostscript-9.55.0 runtime root')
    ap.add_argument('--workers', type=int, default=1, help='Maximum concurrent independent reviews across versions and conditions')
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
    try:
        args.output = validate_output(args.input, args.output)
        papers, pair_count = discover(args.input, args.paper, args.version, args.limit_papers)
        validate_output(args.input, args.output, papers)
        available_profiles, conditions = load_prompts(args.prompts, args.experiment, args.condition)
        source_by_paper, source_provenance = resolve_sources(
            args.input, papers, available_profiles, source=args.source,
            metadata=args.source_metadata, project_root=ROOT)
        profiles = {aid: available_profiles[source] for aid, source in source_by_paper.items()}
        version_count = sum(map(len, papers.values()))
        inventory = dict(papers=len(papers), discovered_pairs=pair_count, unique_versions=version_count,
                         conditions=[c[0] for c in conditions], replicates=args.replicates,
                         planned_reviews=version_count * len(conditions) * args.replicates,
                         versions_by_paper={aid: [v.number for v in vs] for aid, vs in papers.items()},
                         source_by_paper=source_by_paper, source_provenance=source_provenance,
                         system_profiles={source: available_profiles[source].metadata()
                                          for source in sorted(set(source_by_paper.values()))})
        if args.dry_run:
            print(json.dumps(inventory, indent=2))
            return 0
        if any('{OTHER_MODEL_NAME}' in t for _, t in conditions):
            if not args.other_model or args.other_model.casefold() == args.model.casefold():
                raise ValueError('Provide --other-model different from the evaluator model')
        if args.tool_runtime:
            args.tool_runtime = args.tool_runtime.resolve()
            if not all((args.tool_runtime / name).is_dir()
                       for name in ('python_vendor', 'ghostscript-9.55.0')):
                raise ValueError('--tool-runtime requires python_vendor and ghostscript-9.55.0 directories')
        args.ripgrep_metadata = None
        if args.ripgrep:
            args.ripgrep = args.ripgrep.expanduser().resolve()
            if (args.ripgrep.name != 'rg' or not args.ripgrep.is_file() or
                    not os.access(args.ripgrep, os.X_OK)):
                raise ValueError('--ripgrep must name an executable rg file')
            try:
                probe = subprocess.run([str(args.ripgrep), '--version'], check=True,
                                       capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f'Cannot execute ripgrep: {args.ripgrep}') from exc
            version = probe.stdout.splitlines()
            if not version or not version[0].startswith('ripgrep '):
                raise ValueError(f'Unexpected ripgrep version output: {args.ripgrep}')
            args.ripgrep_metadata = {'path': str(args.ripgrep), 'version': version[0],
                                     'sha256': sha(args.ripgrep.read_bytes())}
        args.pdf_skill = pdf_skill_path(args.pdf_skill)
        args.pdf_skill_sha256 = sha(args.pdf_skill.read_bytes())
        if not shutil.which(args.codex):
            raise ValueError(f'Codex executable not found: {args.codex}')
        args.output.mkdir(parents=True, exist_ok=True)
        validate_output(args.input, args.output, papers)
        with (args.output / '.runner.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another runner is using this output directory')
            STOP.clear()
            DRAIN.clear()
            summary = dict(inventory=inventory, started_at=now(), papers={}, errors={},
                           scheduling_unit='review', workers=args.workers, max_observed_concurrency=0)
            pending, counts = prepare_reviews(args, papers, profiles, conditions)
            summary['papers'] = counts
            totals = {aid: len(versions) * len(conditions) * args.replicates
                      for aid, versions in papers.items()}
            summary.update(status='running', pending_reviews=len(pending))
            save_json(args.output / 'run_summary.json', summary)
            handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
            if hasattr(signal, 'SIGUSR1'):
                handlers[signal.SIGUSR1] = signal.signal(signal.SIGUSR1, drain)
            try:
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    active = {}
                    while pending or active:
                        while pending and len(active) < args.workers and not STOP.is_set() and not DRAIN.is_set():
                            cell = pending.popleft()
                            aid, version, payload, profile, condition, template, replicate = cell
                            future = pool.submit(run_review, args, aid, version, payload, profile,
                                                 condition, template, replicate)
                            active[future] = cell
                        summary['max_observed_concurrency'] = max(summary['max_observed_concurrency'], len(active))
                        if not active:
                            break
                        finished, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                        for future in finished:
                            aid, version, _, _, condition, _, replicate = active.pop(future)
                            try:
                                status = future.result()
                                if status is not None:
                                    counts[aid][status] += 1
                            except Exception as exc:
                                summary['errors'][f'{aid}/v{version.number}/{condition}/r{replicate}'] = str(exc)
                                counts[aid]['failed'] += 1
                                print(f'REVIEW FAILED {aid}/v{version.number} {condition}: {exc}', file=sys.stderr)
                            complete = sum(counts[aid].values()) == totals[aid]
                            state = ('failed' if counts[aid]['failed'] else 'completed') if complete else 'in_progress'
                            save_selection(args.output / aid / 'reviews.json', aid, counts[aid], state)
                            summary['pending_reviews'] = len(pending) + len(active)
                            save_json(args.output / 'run_summary.json', summary)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
            failed = bool(summary['errors']) or any(c['failed'] for c in counts.values())
            state = ('stopped' if STOP.is_set() else 'drained' if pending and DRAIN.is_set()
                     else 'failed' if failed else 'completed')
            summary.update(finished_at=now(), status=state,
                           pending_reviews=sum(totals[a] - sum(c.values()) for a, c in counts.items()))
            for aid, count in counts.items():
                complete = sum(count.values()) == totals[aid]
                paper_state = ('failed' if count['failed'] else 'completed') if complete else state
                save_selection(args.output / aid / 'reviews.json', aid, count, paper_state)
                print(f'PAPER {aid}: {count}', flush=True)
            save_json(args.output / 'run_summary.json', summary)
            return 130 if state in ('stopped', 'drained') else 1 if failed else 0
    except (ValueError, OSError) as exc:
        ap.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

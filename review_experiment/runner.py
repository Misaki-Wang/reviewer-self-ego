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
import random
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
# Select one layout in this source tree; a missing helper must fail closed.
HELPERS = ROOT / 'utility/codex'
if not HELPERS.exists():
    HELPERS = ROOT / 'utility'  # Legacy frozen batches keep flat helpers.
_usage_spec = importlib.util.spec_from_file_location('_codex_token_usage', HELPERS / 'token_usage.py')
token_usage = importlib.util.module_from_spec(_usage_spec)
_usage_spec.loader.exec_module(token_usage)
_isolation_spec = importlib.util.spec_from_file_location('_review_codex_isolation', HELPERS / 'codex_isolation.py')
isolation = importlib.util.module_from_spec(_isolation_spec)
_isolation_spec.loader.exec_module(isolation)

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
    version_re = re.compile(r'v([1-9]\d*)\.pdf')
    if pair_re.fullmatch(root.name):
        folders, direct = [root], []
    elif paper_re.fullmatch(root.name):
        folders, direct = root.glob('v*_v*'), root.glob('*.pdf')
    else:
        folders, direct = root.glob('*/v*_v*'), root.glob('*/*.pdf')
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
    for path in sorted(direct):
        aid = path.parent.name
        if papers and aid not in papers:
            continue
        if not paper_re.fullmatch(aid):
            raise ValueError(f'Invalid paper ID: {aid}')
        match = version_re.fullmatch(path.name)
        if not match:
            raise ValueError(f'Invalid version PDF filename: {path}')
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Missing or empty PDF: {path}')
        found.setdefault(aid, {}).setdefault(int(match[1]), []).append(path)
    if papers and set(papers) - set(found):
        raise ValueError(f'Unknown paper IDs: {sorted(set(papers) - set(found))}')
    if not found:
        raise ValueError(f'No PDF versions found: {root}')
    result = {}
    for aid in sorted(found)[:limit]:
        if versions and set(versions) - set(found[aid]):
            raise ValueError(f'{aid}: requested versions missing: {sorted(set(versions) - set(found[aid]))}')
        result[aid] = [Version(aid, number, tuple(paths))
                       for number, paths in sorted(found[aid].items())
                       if not versions or number in versions]
    return result, pairs


def discover_conference_pdfs(root, known_sources, papers=None, conferences=None,
                       versions=None, limit=None):
    """Read the conference PDF tree directly; reuse the existing version reader."""
    root = root.resolve()
    paper_re = re.compile(r'\d{4}\.\d{4,5}')
    if paper_re.fullmatch(root.name) and root.parent.name in known_sources:
        folders = [root]
    elif root.name in known_sources and root.is_dir():
        folders = [p for p in root.iterdir() if paper_re.fullmatch(p.name) and p.is_dir()]
    else:
        folders = [p for p in root.glob('*/*') if paper_re.fullmatch(p.name) and p.is_dir()]
        if not folders:
            return None  # Existing prepared-pair/direct-version input.
    if conferences and set(conferences) - set(known_sources):
        raise ValueError(f'Unknown conferences: {sorted(set(conferences) - set(known_sources))}')
    selected = {}
    for folder in sorted(folders):
        conf, aid = folder.parent.name, folder.name
        if conferences and conf not in conferences or papers and aid not in papers:
            continue
        # Keep one copy per paper, in conference-directory order. --conf picks
        # the desired directory when a paper belongs to several conferences.
        selected.setdefault(aid, folder)
    if papers and set(papers) - set(selected):
        raise ValueError(f'Unknown paper IDs: {sorted(set(papers) - set(selected))}')
    if not selected:
        raise ValueError('No input papers match the requested conferences/papers')
    result, assigned = {}, {}
    for aid in sorted(selected)[:limit]:
        folder = selected[aid]
        conf = folder.parent.name
        if conf not in known_sources:
            raise ValueError(f'Unknown source: {conf}')
        found, _ = discover(folder, versions=versions)
        result[aid] = found[aid]
        assigned[aid] = conf
    return result, assigned


def bind_model_names(template, other_model=None, evaluator_model=None):
    for placeholder, value, option in (
            ('{EVALUATOR_MODEL_NAME}', evaluator_model, '--model'),
            ('{OTHER_MODEL_NAME}', other_model, '--other-model')):
        if placeholder in template:
            if not value:
                raise ValueError(f'{option} is required for {placeholder}')
            template = template.replace(placeholder, value)
    return template


def render_user(template, other_model, pdf_path, evaluator_model=None):
    if template.count('{PAPER_CONTENT}') != 1:
        raise ValueError('Expected one PAPER_CONTENT placeholder')
    template = bind_model_names(template, other_model, evaluator_model)
    # No prose is added: a neutral local PDF path replaces the material placeholder.
    return template.replace('{PAPER_CONTENT}', str(pdf_path))


def pdf_skill_path(explicit=None):
    return isolation.resolve_pdf_skill(explicit)


def permission_options(work, snapshot, skill, codex=None, tool_runtime=None, ripgrep=None):
    """Compatibility wrapper for the OS-only sandbox probe."""
    return isolation.permission_options(work, [snapshot], skill, codex, tool_runtime, ripgrep)


def build_command(args, work, system, snapshot):
    flags, _ = isolation.isolation_options(
        [snapshot], work, codex=args.codex, pdf_skill=args.pdf_skill,
        tool_runtime=getattr(args, 'tool_runtime', None),
        ripgrep=getattr(args, 'ripgrep', None))
    return [args.codex, 'exec', '--json', *flags,
            '--model', args.model, '-c', 'model_reasoning_effort=' + json.dumps(args.effort),
            '-c', 'developer_instructions=' + json.dumps(system),
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
    user = render_user(template, args.other_model, manuscript, args.model)
    attempt['user_message'] = user
    attempt['developer_message'] = profile.text
    # Preserve both exact messages even if the CLI or response validation fails.
    attempt['ripgrep'] = getattr(args, 'ripgrep_metadata', None)
    attempt['isolation_policy'] = isolation.identity()
    attempt['read_files'] = [str(path) for path in isolation.material_paths([snapshot])]
    env = isolation.child_environment(work)
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
            attempt['permission_profile'] = isolation.PROFILE
            errors.seek(0)
            attempt['stderr_tail'] = errors.read()[-4000:]
            response = work / 'response.json'
            if response.is_file():
                attempt['raw_response'] = response.read_text()


def fingerprint(args, profile, template, checksum, condition=None):
    binding = {'system': profile.text, 'source_id': profile.source_id,
                           'score_scale': profile.metadata(), 'user_template': template,
                           'other_model': args.other_model if '{OTHER_MODEL_NAME}' in template else None,
                           'pdf_sha256': checksum, 'model': args.model, 'effort': args.effort,
                           'codex_executable': str(Path(shutil.which(args.codex) or args.codex).resolve()),
                           'schema': profile.schema, 'adapter_version': 3,
                           'isolation_policy': isolation.identity(),
                           'tool_runtime': str(args.tool_runtime) if getattr(args, 'tool_runtime', None) else None,
                           'ripgrep': getattr(args, 'ripgrep_metadata', None),
                           'pdf_skill_sha256': args.pdf_skill_sha256}
    manifest = getattr(args, 'prompt_manifest', {})
    # Leave archived fingerprints intact; bind the new design without changing CLI semantics.
    if manifest.get('design_version') or '{EVALUATOR_MODEL_NAME}' in template:
        binding['design'] = {
            'version': manifest.get('design_version'),
            'user_template_origin': manifest.get('user_template_origin'),
            'condition': condition,
            'metadata': getattr(args, 'condition_metadata', {}).get(condition),
            'resolved_user_template': bind_model_names(template, args.other_model, args.model)}
    return sha(json.dumps(binding, sort_keys=True).encode())


def review_metadata(args, aid, version, condition, replicate, checksum):
    manifest = getattr(args, 'prompt_manifest', {})
    factors = getattr(args, 'condition_metadata', {}).get(condition, {})
    author = factors.get('author_model_name')
    if author is not None:
        author = bind_model_names(author, args.other_model, args.model)
    ownership = factors.get('authorship')
    order = getattr(args, 'condition_orders', {}).get((aid, version.number, replicate))
    return {
        'experiment_id': factors.get('experiment'), 'paper_id': aid,
        'version_ids': [f'v{version.number}'], 'version_pair_id': None,
        'content_hashes': {f'v{version.number}': checksum},
        'author_model_names': [author] if author is not None else None,
        'ownership_by_version': {f'v{version.number}': ownership} if ownership is not None else None,
        'role': ownership, 'user_evaluation': factors.get('user_evaluation'),
        'effort_unit': factors.get('effort_unit'),
        'condition_metadata': factors, 'prompt_version': manifest.get('design_version'),
        'user_template_origin': manifest.get('user_template_origin'),
        'order_seed': getattr(args, 'order_seed', None),
        'display_order': order['conditions'].index(condition) + 1 if order else None,
        'condition_order_ref': f'versions/v{version.number}/condition_orders/r{replicate}' if order else None,
        'gold_quality_relation': None, 'annotation_version': None, 'trajectory_id': None,
        'sampling_config': None, 'reasoning_config': {'effort': args.effort},
        'output_budget': None, 'timestamp': now()}


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
                  fingerprint=fingerprint(args, profile, template, checksum, condition),
                  source_id=profile.source_id, system_prompt=profile.metadata(),
                  model=args.model, effort=args.effort, codex_executable=args.codex,
                  status='failed', attempts=[])
    review.update(review_metadata(args, aid, version, condition, replicate, checksum))
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


def resolve_order_seed(args, documents):
    seeds = [d['order_seed'] for d in documents.values() if 'order_seed' in d]
    summary = args.output / 'run_summary.json'
    if summary.is_file():
        saved = json.loads(summary.read_text())
        if 'order_seed' in saved:
            seeds.append(saved['order_seed'])
    requested = getattr(args, 'order_seed', None)
    if requested is not None:
        seeds.append(requested)
    if any(type(seed) is not int for seed in seeds) or len(set(seeds)) > 1:
        raise ValueError('Condition order seed differs or is invalid; use a fresh output directory')
    args.order_seed = seeds[0] if seeds else random.SystemRandom().getrandbits(64)


def condition_order(args, aid, version, record, replicate, conditions):
    # Shuffle the whole catalog, then filter selected IDs. Widening a selection
    # therefore leaves both completed records and the original randomization intact.
    catalog = sorted(getattr(args, 'condition_metadata', {}) or dict(conditions))
    seed = sha(json.dumps([args.order_seed, aid, version.number, replicate]).encode())
    orders = record.setdefault('condition_orders', {})
    key = f'r{replicate}'
    if not isinstance(orders, dict):
        raise ValueError(f'Invalid saved condition orders: {aid}/v{version.number}')
    if key not in orders:
        order = list(catalog)
        random.Random(seed).shuffle(order)
        orders[key] = {'seed': seed, 'conditions': order}
    order = orders[key]
    if not isinstance(order, dict):
        raise ValueError(f'Invalid saved condition order: {aid}/v{version.number}/{key}')
    ids = order.get('conditions')
    if (order.get('seed') != seed or not isinstance(ids, list) or
            any(not isinstance(cid, str) for cid in ids) or len(ids) != len(set(ids))):
        raise ValueError(f'Invalid saved condition order: {aid}/v{version.number}/{key}')
    additions = sorted(set(catalog) - set(ids))
    random.Random(seed).shuffle(additions)
    ids.extend(additions)
    args.condition_orders[aid, version.number, replicate] = order
    selected = dict(conditions)
    return [(cid, selected[cid]) for cid in ids if cid in selected]


def prepare_reviews(args, papers, profiles, conditions):
    """Read each version once, retain immutable bytes, and skip valid prior cells."""
    queues, counts = [], {}
    documents = {aid: read_document(args.output / aid / 'reviews.json', aid, profiles[aid].source_id)
                 for aid in papers}
    resolve_order_seed(args, documents)
    args.condition_orders = {}
    for aid, versions in papers.items():
        path = args.output / aid / 'reviews.json'
        document = documents[aid]
        document['order_seed'] = args.order_seed
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
            for replicate in range(1, args.replicates + 1):
                ordered = condition_order(args, aid, version, record, replicate, conditions)
                for condition, template in ordered:
                    previous = record['reviews'].get(f'{condition}/r{replicate}', {})
                    mark = fingerprint(args, profile, template, checksum, condition)
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
    if sys.platform == 'darwin' and any(
            output == root or root in output.parents
            for root in (Path('/private/tmp'), Path('/private/var/tmp'))):
        raise ValueError('Output must be outside shared temporary directories on macOS; '
                         'use a dedicated project res/ directory')
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
    ap.add_argument('input', type=Path, nargs='?',
                    help='Defaults to configured clean_data; also accepts a conference/paper directory or historical prepared pair tree')
    ap.add_argument('--output', type=Path, required=True, help='New or resumable result directory, normally res/<run-id>')
    ap.add_argument('--prompts', type=Path, default=HERE / 'prompts')
    ap.add_argument('--source', help='Explicit source ID; otherwise resolve each paper from input provenance/catalog')
    ap.add_argument('--source-metadata', type=Path, help='Explicit prepared scope, source shard or source catalog JSON')
    ap.add_argument('--conf', action='append',
                    help='Select clean-data conferences or saved prepared-input assignments; repeatable')
    ap.add_argument('--paper', action='append')
    version_scope = ap.add_mutually_exclusive_group()
    version_scope.add_argument('--version', type=int, action='append')
    version_scope.add_argument('--latest-only', action='store_true',
                               help='Review only the highest version present for each selected paper in this input')
    ap.add_argument('--limit-papers', type=int)
    ap.add_argument('--experiment', choices=['1', '2', '3', '4'], action='append')
    ap.add_argument('--order-seed', type=int,
                    help='Condition scheduling seed; generated once and reused on resume if omitted')
    ap.add_argument('--condition', action='append')
    ap.add_argument('--other-model', help='Author model name for custom legacy OTHER_MODEL_NAME templates')
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
        if args.input is None:
            config = json.loads((ROOT / 'utility/project.json').read_text())
            args.input = ROOT / config['data']['clean_root']
        args.output = validate_output(args.input, args.output)
        available_profiles, conditions, args.prompt_manifest = load_prompts(
            args.prompts, args.experiment, args.condition, with_manifest=True)
        conference_pdfs = discover_conference_pdfs(args.input, available_profiles, args.paper, args.conf,
                                                  args.version, args.limit_papers)
        if conference_pdfs is not None:
            papers, source_by_paper = conference_pdfs
            pair_count = 0
            if args.source or args.source_metadata:
                source_by_paper, source_provenance = resolve_sources(
                    args.input, papers, available_profiles, source=args.source,
                    metadata=args.source_metadata, project_root=ROOT)
            else:
                source_provenance = {'mode': 'conference_directory', 'source': None,
                                     'metadata_path': None, 'sha256': {}}
        else:
            # Historical prepared trees remain usable; their saved assignment
            # controls source routing, with conference filtering before limits.
            papers, pair_count = discover(args.input, args.paper,
                                          None if args.conf else args.version,
                                          None if args.conf else args.limit_papers)
            source_by_paper, source_provenance = resolve_sources(
                args.input, papers, available_profiles, source=args.source,
                metadata=args.source_metadata, project_root=ROOT)
        validate_output(args.input, args.output, papers)
        args.condition_metadata = {
            item['id']: {key: item.get(key) for key in (
                'id', 'experiment', 'authorship', 'author_model_name',
                'user_evaluation', 'effort_unit', 'shared_baseline_for',
                'user_file', 'source_block', 'source_code_index')}
            for item in args.prompt_manifest['conditions']}
        if args.conf and conference_pdfs is None:
            requested = set(args.conf)
            if requested - set(available_profiles):
                raise ValueError(f'Unknown conferences: {sorted(requested - set(available_profiles))}')
            selected = [aid for aid in papers if source_by_paper[aid] in requested]
            if not selected:
                raise ValueError('No input papers match the requested conferences')
            papers = {aid: papers[aid] for aid in selected[:args.limit_papers]}
            source_by_paper = {aid: source_by_paper[aid] for aid in papers}
            pair_count = len({path.parent for vs in papers.values() for version in vs
                              for path in version.sources
                              if re.fullmatch(r'v[1-9]\d*_v[1-9]\d*', path.parent.name)})
            if args.version:
                for aid, vs in papers.items():
                    missing = set(args.version) - {v.number for v in vs}
                    if missing:
                        raise ValueError(f'{aid}: requested versions missing: {sorted(missing)}')
                    papers[aid] = [v for v in vs if v.number in args.version]
        if args.latest_only:
            papers = {aid: [max(vs, key=lambda version: version.number)] for aid, vs in papers.items()}
        profiles = {aid: available_profiles[source] for aid, source in source_by_paper.items()}
        version_count = sum(map(len, papers.values()))
        inventory = dict(papers=len(papers), discovered_pairs=pair_count, unique_versions=version_count,
                         input_root=str(args.input.resolve()),
                         selection={'conferences': args.conf, 'papers': args.paper,
                                    'versions': args.version, 'latest_only': args.latest_only,
                                    'limit_papers': args.limit_papers},
                         conditions=[c[0] for c in conditions], replicates=args.replicates,
                         design_version=args.prompt_manifest.get('design_version'),
                         user_template_origin=args.prompt_manifest.get('user_template_origin'),
                         condition_metadata={cid: args.condition_metadata[cid] for cid, _ in conditions},
                         order_seed=args.order_seed,
                         condition_order_scope='paper/version/replicate; full catalog, selected IDs only',
                         condition_order_algorithm='sha256(root seed, paper, version, replicate) -> Python Random.shuffle',
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
            summary['order_seed'] = inventory['order_seed'] = args.order_seed
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

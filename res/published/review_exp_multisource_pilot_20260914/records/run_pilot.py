#!/usr/bin/env python3
"""Run-specific global controller. --dry-run is read-only apart from checks.

Default: all frozen conditions. --canary: first exp1_quality cell per source.
Exit codes: 0 completed/dry-run, 2 needs_attention, 130 drained/stopped.
Requires utility/project.json paused=false and run/control.json
pause_dispatch=false for dispatch. Never changes either authorization gate.
"""
import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
LEVELS = [96, 64, 48, 32]
BASELINE = 'exp1_quality'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read_json(path):
    return json.loads(Path(path).read_text())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, immutable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.controller-', delete=False) as f:
        tmp = Path(f.name)
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    try:
        if immutable:
            os.link(tmp, path)  # No overwrites, even across interrupted resumptions.
        else:
            os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def inside(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError(f'Invalid relative path: {relative}')
    target = root / path
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise ValueError(f'Symlink in controlled path: {target}')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes run: {relative}')
    return target


def verify_snapshot(run, revision):
    manifest = read_json(run / 'source_snapshot_manifest.json')
    files = manifest['files']
    if not files or digest(canonical(files)) != manifest['revision'] or manifest['revision'] != revision:
        raise ValueError('Frozen source revision mismatch')
    root = run / 'source_snapshot'
    actual = {str(p.relative_to(root)) for p in root.rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    if actual != set(files):
        raise ValueError('Frozen source file inventory mismatch')
    for relative, expected in files.items():
        data = inside(root, relative).read_bytes()
        if len(data) != expected['bytes'] or digest(data) != expected['sha256']:
            raise ValueError(f'Frozen source bytes changed: {relative}')
    return manifest['revision']


def load_runner(run, revision):
    verify_snapshot(run, revision)
    root = run / 'source_snapshot'
    sys.path.insert(0, str(root))
    runner = importlib.import_module('review_experiment.runner')
    if Path(runner.__file__).resolve() != (root / 'review_experiment/runner.py').resolve():
        raise ValueError('A non-frozen review runner was already imported')
    return runner


def runtime_args(config, runner):
    for name in ('codex', 'python', 'pdf_skill', 'tool_runtime', 'ripgrep'):
        if not Path(config[name]).is_absolute():
            raise ValueError(f'{name} must be absolute')
    args = argparse.Namespace(model=config['model'], effort=config['effort'],
                              other_model=config['other_model'], replicates=config['replicates'],
                              workers=config['workers'], timeout=config['timeout_seconds'],
                              retries=0, overwrite=False, codex=config['codex'],
                              tool_runtime=Path(config['tool_runtime']).resolve())
    if not all((args.tool_runtime / name).is_dir()
               for name in ('python_vendor', 'ghostscript-9.55.0')):
        raise ValueError('Missing existing PDF tool runtime directories')
    for name in ('python', 'codex'):
        if not os.access(config[name], os.X_OK) or not Path(config[name]).is_file():
            raise ValueError(f'Missing executable: {name}')
    if not args.other_model or args.other_model.casefold() == args.model.casefold():
        raise ValueError('other_model must differ from the reviewer')
    # Match runner.main exactly: resolved path, first version line, binary SHA256.
    args.ripgrep = Path(config['ripgrep']).expanduser().resolve()
    if args.ripgrep.name != 'rg' or not args.ripgrep.is_file() or not os.access(args.ripgrep, os.X_OK):
        raise ValueError('--ripgrep must name an executable rg file')
    probe = subprocess.run([str(args.ripgrep), '--version'], check=True,
                           capture_output=True, text=True, timeout=5)
    version = probe.stdout.splitlines()
    if not version or not version[0].startswith('ripgrep '):
        raise ValueError('Unexpected ripgrep version output')
    args.ripgrep_metadata = {'path': str(args.ripgrep), 'version': version[0],
                             'sha256': runner.sha(args.ripgrep.read_bytes())}
    args.pdf_skill = runner.pdf_skill_path(config['pdf_skill'])
    args.pdf_skill_sha256 = runner.sha(args.pdf_skill.read_bytes())
    return args


@dataclass
class Source:
    source_id: str
    args: object
    papers: dict
    profiles: dict
    conditions: list
    inventory: dict


def plan_sources(run, config, runner, canary=False):
    if (config['workers'] != 96 or config['worker_levels'] != LEVELS or
            config['replicates'] != 1 or config['papers_per_source'] != 10 or
            config['timeout_seconds'] <= 0):
        raise ValueError('Config differs from the authorized pilot limits')
    profiles, conditions = runner.load_prompts(run / 'source_snapshot/review_experiment/prompts')
    specs = config['sources']
    if len(specs) != 10 or {s['source_id'] for s in specs} != set(profiles) or len(conditions) != 12:
        raise ValueError('The pilot requires all ten source profiles and twelve conditions')
    base = runtime_args(config, runner)
    sources = []
    for number, spec in enumerate(specs, 1):
        sid = spec['source_id']
        if spec['input'] != f'input_data/{sid}' or spec['output'] != f'reviews/b{number:02d}':
            raise ValueError('Unexpected source input/output shard')
        args = argparse.Namespace(**vars(base))
        args.input = inside(run, spec['input'])
        args.output = inside(run, spec['output'])
        papers, pairs = runner.discover(args.input)
        if len(papers) != 10:
            raise ValueError(f'{sid}: expected exactly 10 prepared papers, got {len(papers)}')
        args.output = runner.validate_output(args.input, args.output, papers)
        mapping, provenance = runner.resolve_sources(args.input, papers, profiles, source=sid)
        assigned = {aid: profiles[value] for aid, value in mapping.items()}
        chosen_conditions = conditions
        full_versions = sum(len(vs) for vs in papers.values())
        if canary:
            aid = next(iter(papers))
            papers = {aid: papers[aid][:1]}
            assigned = {aid: assigned[aid]}
            chosen_conditions = [c for c in conditions if c[0] == BASELINE]
            if len(chosen_conditions) != 1:
                raise ValueError('Missing unique exp1_quality baseline')
        inventory = {'source_id': sid, 'output': spec['output'], 'scope_papers': 10,
                     'scope_unique_versions': full_versions, 'discovered_pairs': pairs,
                     'full_planned': full_versions * len(conditions),
                     'planned': sum(map(len, papers.values())) * len(chosen_conditions),
                     'versions_by_paper': {aid: [v.number for v in vs] for aid, vs in papers.items()},
                     'conditions': [c[0] for c in chosen_conditions], 'source_provenance': provenance,
                     'system_profile': profiles[sid].metadata()}
        sources.append(Source(sid, args, papers, assigned, chosen_conditions, inventory))
    return sources


@dataclass
class Task:
    source: Source
    cell: tuple
    fingerprint: str
    ready_at: float = 0
    dispatch_capacity: int = None

    @property
    def identity(self):
        aid, version, _, _, condition, _, replicate = self.cell
        return [self.source.source_id, aid, version.number, condition, replicate]

    @property
    def key(self):
        return digest(canonical([self.identity, self.fingerprint]))

    @property
    def path(self):
        return self.source.args.output / self.cell[0] / 'reviews.json'


def cell_review(document, identity):
    return document.get('versions', {}).get(f'v{identity[2]}', {}).get('reviews', {}).get(
        f'{identity[3]}/r{identity[4]}')


def attempt_signature(attempt):
    return digest(canonical({k: v for k, v in attempt.items() if k != 'controller_attempt_id'}))


def backoff(failures):
    return min(300, 30 * 2 ** min(max(failures - 1, 0), 4))


def systemic_failure(attempts):
    # Only execution errors, never paper text, generated reviews, or user prompts.
    text = '\n'.join(str(a.get(k, '')) for a in attempts for k in ('error', 'stderr_tail'))
    # Ignore only this transport handshake status; other auth/quota evidence still drains.
    text = re.sub(r'\bfailed to connect to websocket:\s*HTTP error:\s*403 Forbidden\b',
                  '', text, flags=re.I)
    pattern = (r'\b(?:401|403)\b|unauthori[sz]ed|invalid[ _-]*(?:api[ _-]*)?key|'
               r'authentication (?:failed|required)|auth[ _-]*fail(?:ed|ure)?|not (?:logged|signed) in|'
               r'(?:token[ _-]*refresh|refresh[ _-]*token)[^\n]{0,70}(?:fail|invalid|expired|revoked|reused)|'
               r'insufficient[_ ]quota|(?:quota|credits?)[^\n]{0,70}(?:exceeded|exhausted|depleted)|'
               r'(?:usage|spending) limit|billing[_ ]hard[_ ]limit|'
               r'billing[ _-]*(?:error|fail(?:ed|ure)?|required|disabled)|account[^\n]{0,40}suspended')
    return bool(re.search(pattern, text, re.I))


class Journal:
    """Immutable start/finish receipts; canonical results are a recoverable view."""
    def __init__(self, run, runner):
        self.run, self.runner = run, runner
        self.root = inside(run, 'records/attempt_history')
        self.receipts = {}
        self.schemas = {}
        for path in sorted(self.root.glob('*/*.finished.json')):
            receipt = read_json(path)
            self.receipts.setdefault(receipt['key'], []).append(receipt)

    def receipt(self, key, value):
        atomic_json(self.root / key / (value['round_id'] + '.finished.json'), value, immutable=True)
        self.receipts.setdefault(key, []).append(value)

    def merge(self, identity, fingerprint, current, key):
        candidates = [r['review'] for r in self.receipts.get(key, [])]
        if current and current.get('fingerprint') == fingerprint:
            candidates.append(current)
        if not candidates:
            return None
        completed = []
        for candidate in candidates:
            if candidate.get('status') == 'completed':
                try:
                    self.runner.validate_result(candidate['result'], self.schemas[identity[0]])
                except (ValueError, KeyError):
                    continue
                completed.append(candidate)
        result = dict(completed[-1] if completed else candidates[-1])
        if not completed:
            result['status'] = 'failed'
        attempts, known = {}, set()
        for review in candidates:
            for attempt in review.get('attempts', []):
                mark = attempt_signature(attempt)
                identifier = attempt.get('controller_attempt_id')
                if identifier is None and mark in known:
                    continue
                identifier = identifier or ('recovered-' + mark)
                attempts[identifier] = dict(attempt, controller_attempt_id=identifier)
                known.add(mark)
        result['attempts'] = sorted(attempts.values(), key=lambda a: (a.get('started_at', ''), a['controller_attempt_id']))
        result['token_usage'] = self.runner.token_usage.summarize(result['attempts'])
        return result

    def put(self, document, identity, review):
        document['versions'][f'v{identity[2]}']['reviews'][f'{identity[3]}/r{identity[4]}'] = review

    def recover(self, sources):
        outputs = {s.source_id: s.args.output for s in sources}
        self.schemas.update({s.source_id: next(iter(s.profiles.values())).schema for s in sources})
        with self.runner.RESULT_LOCK:
            # An intent without a finish may have saved a raw canonical result.
            for path in sorted(self.root.glob('*/*.started.json')):
                intent = read_json(path)
                final = path.with_name(intent['round_id'] + '.finished.json')
                if final.exists():
                    continue
                identity = intent['identity']
                result_path = outputs[identity[0]] / identity[1] / 'reviews.json'
                current = cell_review(read_json(result_path), identity) if result_path.exists() else None
                fresh = [a for a in (current or {}).get('attempts', [])
                         if 'controller_attempt_id' not in a and a.get('started_at', '') >= intent['started_at']]
                if fresh and current.get('fingerprint') == intent['fingerprint']:
                    review = dict(current, attempts=[dict(a, controller_attempt_id=f"{intent['round_id']}:{i}")
                                                    for i, a in enumerate(fresh)])
                else:
                    review = {'fingerprint': intent['fingerprint'], 'source_id': identity[0],
                              'condition': identity[3], 'replicate': identity[4], 'status': 'failed',
                              'attempts': [{'controller_attempt_id': intent['round_id'] + ':unknown',
                                            'started_at': intent['started_at'], 'finished_at': now(),
                                            'status': 'unknown', 'error': 'Interrupted before durable call outcome; usage unknown'}]}
                self.receipt(intent['key'], dict(intent, review=review, recovered=True, finished_at=now()))
            # Archive any pre-controller/raw canonical attempts before another call can reset them.
            for source in sources:
                for path in sorted(source.args.output.glob('*/reviews.json')):
                    document = read_json(path)
                    if document.get('source_id') != source.source_id:
                        raise ValueError(f'Result source mismatch: {path}')
                    for version, record in document['versions'].items():
                        for review_key, review in list(record['reviews'].items()):
                            if not review.get('attempts'):
                                continue
                            condition, replicate = review_key.rsplit('/r', 1)
                            identity = [source.source_id, document['paper_id'], int(version[1:]), condition, int(replicate)]
                            key = digest(canonical([identity, review['fingerprint']]))
                            merged = self.merge(identity, review['fingerprint'], review, key)
                            saved_ids = {a['controller_attempt_id'] for r in self.receipts.get(key, [])
                                         for a in r['review'].get('attempts', [])}
                            extra = [a for a in merged['attempts'] if a['controller_attempt_id'] not in saved_ids]
                            if extra:
                                rid = 'import-' + digest(canonical(extra))
                                self.receipt(key, {'key': key, 'identity': identity, 'fingerprint': review['fingerprint'],
                                                  'round_id': rid, 'finished_at': now(),
                                                  'review': dict(merged, attempts=extra), 'recovered': True})
                            self.put(document, identity, merged)
                    atomic_json(path, document)

            for source in sources:
                for aid, versions in source.papers.items():
                    path = source.args.output / aid / 'reviews.json'
                    document = self.runner.read_document(path, aid, source.source_id)
                    changed = False
                    for version in versions:
                        _, checksum = version.read()
                        for condition, template in source.conditions:
                            fingerprint = self.runner.fingerprint(source.args, source.profiles[aid], template, checksum)
                            for replicate in range(1, source.args.replicates + 1):
                                identity = [source.source_id, aid, version.number, condition, replicate]
                                key = digest(canonical([identity, fingerprint]))
                                if key not in self.receipts:
                                    continue
                                current = cell_review(document, identity)
                                merged = self.merge(identity, fingerprint, current, key)
                                record = document['versions'].setdefault(f'v{version.number}', {'reviews': {}})
                                if record.get('sha256') not in (None, checksum):
                                    raise ValueError('Cannot restore receipts over different PDF bytes')
                                record.update(sha256=checksum, source_paths=[str(p) for p in version.sources])
                                self.put(document, identity, merged)
                                changed = True
                    if changed:
                        atomic_json(path, document)

    def failure_count(self, key):
        return sum(a.get('status') == 'failed' for r in self.receipts.get(key, [])
                   for a in r['review'].get('attempts', []))

    def execute(self, task, before_call):
        before_call(task)  # Bytes and the exact cached PDF must still match.
        rid = uuid.uuid4().hex
        intent = {'round_id': rid, 'key': task.key, 'identity': task.identity,
                  'fingerprint': task.fingerprint, 'started_at': now(),
                  'pid': os.getpid(), 'hostname': socket.gethostname(),
                  'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
                  'dispatch_capacity': task.dispatch_capacity}
        atomic_json(self.root / task.key / (rid + '.started.json'), intent, immutable=True)
        error = None
        try:
            self.runner.run_review(task.source.args, *task.cell)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        with self.runner.RESULT_LOCK:
            document = read_json(task.path)
            current = cell_review(document, task.identity)
            fresh = [a for a in (current or {}).get('attempts', [])
                     if 'controller_attempt_id' not in a and a.get('started_at', '') >= intent['started_at']]
            if fresh and current.get('fingerprint') == task.fingerprint:
                review = dict(current, attempts=[dict(a, controller_attempt_id=f'{rid}:{i}')
                                                for i, a in enumerate(fresh)])
            else:
                review = {'fingerprint': task.fingerprint, 'source_id': task.identity[0],
                          'condition': task.identity[3], 'replicate': task.identity[4],
                          'status': 'failed', 'attempts': []}
            failed = any(a.get('status') == 'failed' for a in fresh)
            retry_at = time.time() + backoff(self.failure_count(task.key) + 1) if failed else 0
            receipt = dict(intent, review=review, finished_at=now(), exception=error,
                           retry_not_before=retry_at, real_failure=failed)
            self.receipt(task.key, receipt)
            merged = self.merge(task.identity, task.fingerprint, current, task.key)
            self.put(document, task.identity, merged)
            atomic_json(task.path, document)
        return {'status': merged['status'], 'real_failure': failed, 'retry_at': retry_at,
                'systemic': failed and systemic_failure(fresh), 'exception': error,
                'missing_attempt': not fresh, 'round_id': rid,
                'dispatch_capacity': intent['dispatch_capacity']}


def prepare_tasks(sources, runner, journal):
    queues, counts = [], {}
    for source in sources:
        pending, per_paper = runner.prepare_reviews(source.args, source.papers, source.profiles, source.conditions)
        counts[source.source_id] = {'planned': source.inventory['planned'],
                                    'completed': sum(p['skipped'] for p in per_paper.values()),
                                    'skipped': sum(p['skipped'] for p in per_paper.values())}
        queue = deque()
        for cell in pending:
            _, _, payload, profile, _, template, _ = cell
            task = Task(source, cell, runner.fingerprint(source.args, profile, template, payload[1]))
            task.ready_at = max((r.get('retry_not_before', 0) for r in journal.receipts.get(task.key, [])), default=0)
            queue.append(task)
        queues.append(queue)
    pending = deque()
    while any(queues):
        for queue in queues:
            if queue:
                pending.append(queue.popleft())
    return pending, counts


def validate_inventory(run, config, sources, runner, required=True):
    path = run / 'inventory.json'
    if not path.is_file() and not required:
        return None
    inventory = read_json(path)
    specs = inventory['sources']
    if (inventory.get('schema_version') != 'review-pilot-inventory-1' or
            inventory['source_revision'] != config['source_revision'] or
            inventory['scope_revision'] != digest(canonical(specs)) or
            len(specs) != 10 or [s['source_id'] for s in specs] != [s.source_id for s in sources]):
        raise ValueError('Canonical inventory identity/revision mismatch')
    expected_pdfs = {}
    totals = dict(sources=10, papers=0, unique_paper_ids=0, versions=0, reviews=0)
    unique = set()
    for spec, source, cfg in zip(specs, sources, config['sources']):
        # Rediscover full scope even for canary; preparation still uses one cell.
        papers, _ = runner.discover(source.args.input)
        if spec['input'] != cfg['input'] or spec['output'] != cfg['output']:
            raise ValueError('Inventory source shard mismatch')
        all_profiles, conditions = runner.load_prompts(run / 'source_snapshot/review_experiment/prompts')
        if (spec['conditions'] != [c[0] for c in conditions] or spec['replicates'] != 1 or
                spec['system_prompt'] != all_profiles[source.source_id].metadata()):
            raise ValueError('Inventory condition/profile mismatch')
        expected = {p['paper_id']: p for p in spec['papers']}
        if len(expected) != len(spec['papers']) or set(expected) != set(papers):
            raise ValueError('Inventory paper set mismatch')
        version_count = 0
        for aid, versions in papers.items():
            records = {v['number']: v for v in expected[aid]['versions']}
            if len(records) != len(expected[aid]['versions']) or set(records) != {v.number for v in versions}:
                raise ValueError('Inventory version set mismatch')
            for version in versions:
                expected_version = records[version.number]
                paths = [str(p.relative_to(run)) for p in version.sources]
                if sorted(paths) != sorted(expected_version['paths']) or len(set(expected_version['paths'])) != len(paths):
                    raise ValueError('Inventory PDF path mismatch')
                if type(expected_version['bytes']) is not int or expected_version['bytes'] <= 0:
                    raise ValueError('Invalid inventory PDF byte count')
                expected_pdfs[(source.source_id, aid, version.number)] = expected_version
            version_count += len(versions)
        if (spec['paper_count'] != len(papers) or spec['version_count'] != version_count or
                spec['planned_reviews'] != version_count * 12):
            raise ValueError('Inventory source counts mismatch')
        totals['papers'] += len(papers)
        totals['versions'] += version_count
        totals['reviews'] += version_count * 12
        unique.update(papers)
    totals['unique_paper_ids'] = len(unique)
    if inventory['totals'] != totals:
        raise ValueError('Inventory total counts mismatch')
    return inventory, expected_pdfs


class FrozenGate:
    def __init__(self, run, config, runner, sources):
        self.run, self.config = run, config
        self.inventory, self.pdfs = validate_inventory(run, config, sources, runner)
        self.freeze = read_json(run / 'records/freeze_verification.json')
        self.freeze_hash = digest((run / 'records/freeze_verification.json').read_bytes())
        if (self.freeze.get('status') != 'passed' or
                self.freeze.get('source_revision') != config['source_revision'] or
                self.freeze.get('scope_revision') != self.inventory['scope_revision']):
            raise ValueError('Missing/pending/inconsistent freeze verification')
        required = {'pilot_config.json', 'inventory.json', 'selection_manifest.json',
                    'scope_input.json', 'source_snapshot_manifest.json', 'records/run_pilot.py'}
        files = self.freeze.get('bound_files', {})
        if not isinstance(files, dict) or not required <= set(files):
            raise ValueError('Freeze verification requires a bound_files map binding all scope/config/controller files')
        self.verify()

    def verify(self):
        if digest((self.run / 'records/freeze_verification.json').read_bytes()) != self.freeze_hash:
            raise ValueError('Freeze verification changed during execution')
        verify_snapshot(self.run, self.config['source_revision'])
        for relative, expected in self.freeze['bound_files'].items():
            data = inside(self.run, relative).read_bytes()
            if len(data) != expected['bytes'] or digest(data) != expected['sha256']:
                raise ValueError(f'Frozen run bytes changed: {relative}')

    def check_task(self, task):
        expected = self.pdfs[tuple(task.identity[:3])]
        data, checksum = task.cell[2]
        if len(data) != expected['bytes'] or checksum != expected['sha256'] or digest(data) != checksum:
            raise ValueError(f'Prepared PDF differs from canonical inventory: {task.identity[:3]}')

    def __call__(self, task):
        self.verify()
        self.check_task(task)


class Capacity:
    def __init__(self, levels=LEVELS, current=None):
        self.levels = levels
        self.current = current if current in levels else levels[0]

    def fail(self, dispatch_capacity=None):
        before = self.current
        if dispatch_capacity is not None and dispatch_capacity != before:
            return before, before
        self.current = self.levels[min(self.levels.index(before) + 1, len(self.levels) - 1)]
        return before, self.current

    def admits(self, active):
        return active < self.current


def pause_reason(run):
    project = run.parent.parent / 'utility/project.json'
    if not project.is_file() or read_json(project).get('paused') is not False:
        return 'Project dispatch is paused (utility/project.json paused must be false)'
    control = run / 'control.json'
    if not control.is_file() or read_json(control).get('pause_dispatch') is not False:
        return 'Run dispatch is paused (control.json pause_dispatch must be false)'
    return None


class Controller:
    def __init__(self, run, runner, sources, journal, pending, counts, before_call,
                 pause_check=lambda: None, levels=LEVELS, heartbeat_seconds=30, mode='full'):
        self.run, self.runner, self.sources, self.journal = run, runner, sources, journal
        self.pending, self.counts, self.before_call = pending, counts, before_call
        self.pause_check, self.heartbeat_seconds = pause_check, heartbeat_seconds
        old = read_json(run / 'progress.json') if (run / 'progress.json').exists() else {}
        self.capacity = Capacity(levels, old.get('currentworkers'))
        self.maxobserved = old.get('maxobserved', 0)
        self.active = {}
        self.started_at, self.execution_id = now(), uuid.uuid4().hex
        self.monotonic_start = time.monotonic()
        self.mode = mode
        self.study_started_at = old.get('study_started_at', self.started_at)
        self.study_planned = sum(s.inventory.get('full_planned', s.inventory['planned']) for s in sources)
        self.attention = None
        self.pending_signals = []
        self.last_progress = 0
        self.event_path = run / 'records/concurrency_events.jsonl'

    def record_signal(self, signum, frame):
        # Signal context must never acquire Event/process/result/file locks.
        self.pending_signals.append(signum)

    def apply_signals(self):
        while self.pending_signals:
            signum = self.pending_signals.pop(0)
            if signum == signal.SIGUSR1:
                self.runner.drain(signum, None)
                self.event('signal_applied', signal=signum, action='drain')
            else:
                self.runner.stop(signum, None)
                self.event('signal_applied', signal=signum, action='stop')

    def event(self, kind, **data):
        self.event_path.parent.mkdir(parents=True, exist_ok=True)
        value = {'at': now(), 'execution_id': self.execution_id, 'event': kind,
                 'active': len(self.active), 'currentworkers': self.capacity.current, **data}
        with self.event_path.open('a') as f:
            f.write(json.dumps(value, ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def progress(self, status='running'):
        per_source = {sid: dict(values, failedattempts=0, pending=0, active=0)
                      for sid, values in self.counts.items()}
        attempts = []
        # Workers publish receipts under RESULT_LOCK. Avoid iteration races.
        with self.runner.RESULT_LOCK:
            for receipts in self.journal.receipts.values():
                for receipt in receipts:
                    sid = receipt['identity'][0]
                    rows = receipt['review'].get('attempts', [])
                    attempts.extend(rows)
                    per_source[sid]['failedattempts'] += sum(a.get('status') == 'failed' for a in rows)
        for task in self.pending:
            per_source[task.source.source_id]['pending'] += 1
        for task in self.active.values():
            per_source[task.source.source_id]['active'] += 1
        value = {'mode': self.mode, 'study_planned': self.study_planned,
                 'study_started_at': self.study_started_at,
                 'elapsed_seconds': round(time.monotonic() - self.monotonic_start, 3),
                 'status': status, 'execution_id': self.execution_id, 'started_at': self.started_at,
                 'heartbeat_at': now(), 'pid': os.getpid(), 'ppid': os.getppid(),
                 'hostname': socket.gethostname(), 'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
                 'slurm_step_id': os.environ.get('SLURM_STEP_ID'), 'needs_attention': self.attention,
                 'planned': sum(c['planned'] for c in per_source.values()),
                 'completed': sum(c['completed'] for c in per_source.values()),
                 'failedattempts': sum(c['failedattempts'] for c in per_source.values()),
                 'pending': len(self.pending), 'active': len(self.active),
                 'currentworkers': self.capacity.current, 'maxobserved': self.maxobserved,
                 'per_source': per_source, 'token_usage': self.runner.token_usage.summarize(attempts),
                 'unknown_attempt_outcomes': sum(a.get('status') == 'unknown' for a in attempts)}
        # Snapshot only; do not acquire LOCK in the signal-handling main thread.
        value['study_complete'] = (self.mode == 'full' and status == 'completed'
                                   and value['completed'] == self.study_planned)
        value['whole_study_complete'] = value['study_complete']
        value['core_process_ids'] = sorted(p.pid for p in list(self.runner.PROCESSES))
        if status != 'running':
            value['finished_at'] = now()
        atomic_json(self.run / 'progress.json', value)
        self.last_progress = time.monotonic()
        return value

    def run_pool(self):
        self.event('controller_started', pid=os.getpid(), slurm_job_id=os.environ.get('SLURM_JOB_ID'))
        self.progress()
        with ThreadPoolExecutor(max_workers=self.capacity.levels[0]) as pool:
            while self.pending or self.active:
                self.apply_signals()
                reason = self.pause_check()
                if reason and self.attention is None:
                    self.attention = reason
                    self.runner.DRAIN.set()
                    self.event('needs_attention', reason=reason)
                if not self.runner.STOP.is_set() and not self.runner.DRAIN.is_set():
                    # One bounded scan also lets other papers proceed during retry backoff.
                    for _ in range(len(self.pending)):
                        self.apply_signals()
                        if (not self.capacity.admits(len(self.active)) or self.runner.STOP.is_set()
                                or self.runner.DRAIN.is_set()):
                            break
                        task = self.pending.popleft()
                        if task.ready_at > time.time():
                            self.pending.append(task)
                            continue
                        task.dispatch_capacity = self.capacity.current
                        future = pool.submit(self.journal.execute, task, self.before_call)
                        self.active[future] = task
                        self.maxobserved = max(self.maxobserved, len(self.active))
                        self.event('admitted', cell=task.identity, dispatch_capacity=task.dispatch_capacity)
                if not self.active:
                    if self.runner.STOP.is_set() or self.runner.DRAIN.is_set():
                        break
                    self.runner.STOP.wait(0.25)
                    if time.monotonic() - self.last_progress >= self.heartbeat_seconds:
                        self.progress()
                    continue
                finished, _ = wait(self.active, timeout=1, return_when=FIRST_COMPLETED)
                self.apply_signals()
                for future in finished:
                    task = self.active.pop(future)
                    try:
                        outcome = future.result()
                    except Exception as exc:
                        outcome = {'status': 'failed', 'real_failure': False, 'systemic': False,
                                   'exception': f'{type(exc).__name__}: {exc}', 'missing_attempt': True}
                    if outcome['status'] == 'completed':
                        self.counts[task.source.source_id]['completed'] += 1
                    else:
                        if outcome['real_failure']:
                            before, after = self.capacity.fail(task.dispatch_capacity)
                            if after != before:
                                self.event('capacity_reduced', previous=before, next=after, cell=task.identity,
                                           dispatch_capacity=task.dispatch_capacity)
                        task.ready_at = outcome.get('retry_at', time.time() + 30)
                        self.pending.append(task)
                    if (outcome['systemic'] or outcome.get('exception') or outcome['missing_attempt']) and not self.runner.STOP.is_set():
                        self.attention = ('Systemic authentication/quota failure' if outcome['systemic'] else
                                          outcome.get('exception') or 'Core returned without a durable attempt')
                        self.runner.DRAIN.set()
                        self.event('needs_attention', reason=self.attention, cell=task.identity)
                    self.event('result', cell=task.identity, **outcome)
                    self.progress()
                if time.monotonic() - self.last_progress >= self.heartbeat_seconds:
                    self.progress()
        self.apply_signals()
        status = ('needs_attention' if self.attention else 'stopped' if self.runner.STOP.is_set()
                  else 'drained' if self.pending and self.runner.DRAIN.is_set() else 'completed')
        self.event('controller_finished', status=status)
        return self.progress(status)


def acquire_lock(stack, path):
    if path.is_symlink():
        raise ValueError(f'Lock must not be a symlink: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = stack.enter_context(path.open('a+'))
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ValueError(f'Another process owns {path}') from exc
    return stream


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'pilot_config.json')
    parser.add_argument('--canary', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    cli = parser.parse_args(argv)
    run = cli.config.resolve().parent
    checks = {'checked_at': now(), 'mode': 'canary' if cli.canary else 'full', 'model_calls_started': 0}
    try:
        config = read_json(cli.config)
        runner = load_runner(run, config['source_revision'])
        sources = plan_sources(run, config, runner, cli.canary)
        validate_inventory(run, config, sources, runner, required=not cli.dry_run)
        inventory = {'mode': checks['mode'], 'source_revision': config['source_revision'],
                     'planned': sum(s.inventory['planned'] for s in sources),
                     'per_source': {s.source_id: s.inventory for s in sources}}
        checks.update(status='passed', source_revision=config['source_revision'], inventory=inventory,
                      dispatch_pause=pause_reason(run), ripgrep=sources[0].args.ripgrep_metadata)
        atomic_json(run / 'records/runtime-checks.json', checks)
        if cli.dry_run:
            print(json.dumps(inventory, indent=2))
            return 0
        if Path(sys.executable).resolve() != Path(config['python']).resolve():
            raise ValueError('Launch with pilot_config.python to preserve runtime isolation')
        gate = FrozenGate(run, config, runner, sources)
        with ExitStack() as stack:
            acquire_lock(stack, run / 'records/.controller.lock')
            for source in sources:
                runner.validate_output(source.args.input, source.args.output, source.papers)
                acquire_lock(stack, source.args.output / '.runner.lock')
            runner.STOP.clear()
            runner.DRAIN.clear()
            journal = Journal(run, runner)
            journal.recover(sources)
            pending, counts = prepare_tasks(sources, runner, journal)
            for task in pending:
                gate.check_task(task)
            controller = Controller(run, runner, sources, journal, pending, counts,
                                    gate, lambda: pause_reason(run), mode=checks['mode'])
            old_handlers = {sig: signal.signal(sig, controller.record_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
            old_handlers[signal.SIGUSR1] = signal.signal(signal.SIGUSR1, controller.record_signal)
            try:
                result = controller.run_pool()
            finally:
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)
            print(json.dumps(result, indent=2))
            return 0 if result['status'] == 'completed' else 130 if result['status'] in ('drained', 'stopped') else 2
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        checks.update(status='needs_attention', error=f'{type(exc).__name__}: {exc}')
        atomic_json(run / 'records/runtime-checks.json', checks)
        print(checks['error'], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Resume a fixed PDF-comparison batch using the existing local Codex CLI helpers."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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
import threading
import time
from types import SimpleNamespace

if __package__:
    from . import completed_turn as recovery
else:
    import completed_turn as recovery


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def terminal_error(message):
    text = message.lower()
    if any(x in text for x in ('usage limit', 'insufficient_quota', 'quota exceeded',
                               'refresh token', 'authentication failed', 'unauthorized',
                               '401 unauthorized', 'billing limit')):
        return 'account_or_authentication'
    if any(x in text for x in ('context window', 'context_length_exceeded', 'input too long',
                               'refusal', 'content_policy_violation')):
        return 'model_input_or_refusal'
    return None


def attempt_observation(receipt, at):
    """Separate completed judgments from outer CLI failures and transport pressure."""
    text = (json.dumps(receipt.get('event_errors', [])) + ' ' + receipt.get('error', '')
            + ' ' + receipt.get('transport_diagnostics', '')).lower()
    terminal = receipt.get('terminal_error') or terminal_error(text)
    transport = any(word in text for word in (
        'falling back from websocket', 'falling back to https', 'websocket error',
        'websocket i/o error', 'websocket handshake failed', 'stream disconnected',
        'connection reset', 'connection closed',
        'connection refused', 'connection timed out', 'error sending request',
        'http 429', 'status 429', 'too many requests', 'rate limit',
        '502 bad gateway', '503 service unavailable', '504 gateway timeout'))
    resource = any(word in text for word in (
        'out of memory', 'cannot allocate memory', 'oom-kill', 'too many open files',
        'resource temporarily unavailable', 'no space left on device'))
    return {'at': at, 'started_at': receipt.get('started_monotonic', at), 'status': receipt['status'],
            'outer_attempt_failed': receipt['status'] != 'completed' or 'recovery' in receipt,
            'connection_errors': len(receipt.get('event_errors', [])),
            'transport_pressure': not terminal and (transport or bool(receipt.get('timed_out'))),
            'resource_pressure': not terminal and resource}


def memory_observation(proc=Path('/proc')):
    """Sample this process's allocation, including tighter cgroup ancestors on Linux."""
    unknown = {'source': 'unavailable', 'free_percent': None}
    if sys.platform == 'darwin':
        try:
            text = subprocess.check_output(['memory_pressure', '-Q'], text=True, timeout=10)
            return {'source': 'memory_pressure', 'free_percent':
                    int(re.search(r'free percentage:\s*(\d+)%', text).group(1))}
        except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
            return unknown
    if not sys.platform.startswith('linux'):
        return unknown
    try:
        groups = [line.split(':', 2) for line in (proc / 'self/cgroup').read_text().splitlines()]
        mounts = (proc / 'self/mountinfo').read_text().splitlines()
    except OSError:
        return unknown
    samples = []
    for line in mounts:
        try:
            fields, filesystem = line.split(' - ', 1)
            fields, filesystem = fields.split(), filesystem.split()
            kind = filesystem[0]
            if kind not in ('cgroup', 'cgroup2'):
                continue
            if kind == 'cgroup' and 'memory' not in filesystem[2].split(','):
                continue
            # mountinfo escapes whitespace and backslashes as octal sequences.
            mount_root, mount = [Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), p))
                                 for p in fields[3:5]]
            group = next(Path(path) for _, controllers, path in groups
                         if (not controllers if kind == 'cgroup2' else 'memory' in controllers.split(',')))
            relative = group.relative_to(mount_root)
            if '..' in relative.parts:
                continue
            current = mount / relative
            limit_name, used_name = (('memory.max', 'memory.current') if kind == 'cgroup2'
                                     else ('memory.limit_in_bytes', 'memory.usage_in_bytes'))
            while True:
                try:
                    limit = int((current / limit_name).read_text())
                    used = int((current / used_name).read_text())
                    if 0 < limit < 2 ** 60 and used >= 0:
                        samples.append({'source': 'cgroup_v2' if kind == 'cgroup2' else 'cgroup_v1',
                            'path': str(current), 'limit_bytes': limit, 'used_bytes': used,
                            'free_percent': 100 * max(0, limit - used) / limit})
                except (OSError, ValueError):
                    pass  # Unlimited or inaccessible counters are not zero free memory.
                if current == mount:
                    break
                current = current.parent
        except (ValueError, IndexError, StopIteration):
            continue
    # Do not substitute another host's or the whole node's memory for an allocation.
    return min(samples, key=lambda row: row['free_percent']) if samples else unknown


def validate_cached_pdf(row, frozen_version, prepare):
    path = Path(row['local_path'])
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing regular original PDF: {path}')
    if path.stat().st_size != row['expected_bytes'] or sha(path) != row['expected_sha256']:
        raise ValueError(f'Exact-version PDF hash/size mismatch: {path}')
    result = prepare.validate_pdf(path, row['arxiv_id'], frozen_version)
    if row.get('expected_pages') is not None and result['pages'] != row['expected_pages']:
        raise ValueError(f'Physical page count mismatch: {path}')
    return {**result, 'requested_id': row['requested_id'], 'arxiv_id': row['arxiv_id'],
            'version': row['version'], 'mtime_ns': path.stat().st_mtime_ns}


def usable_saved_result(path, signature, expected_inputs, validate):
    receipt_path = path.with_suffix('.execution.json')
    if not path.exists() and not receipt_path.exists():
        return None
    if not path.is_file() or not receipt_path.is_file():
        return None
    receipt = read(receipt_path)
    if receipt.get('status') != 'completed':
        return None
    validate(read(path))
    if receipt.get('execution_signature') != signature:
        raise ValueError(f'Completed output settings do not match: {path}')
    if receipt.get('input_sha256') != expected_inputs or receipt.get('output_sha256') != sha(path):
        raise ValueError(f'Completed output/input provenance does not match: {path}')
    return receipt


def final_status(states, finished):
    """Only completed judgments and proven absent inputs constitute a settled success."""
    statuses = [state['status'] for state in states.values()]
    if not finished or any(s not in ('completed', 'input_unavailable') for s in statuses):
        return 'incomplete'
    return 'completed_with_unavailable_inputs' if 'input_unavailable' in statuses else 'completed'


class Batch:
    def __init__(self, run, retry_failed=False):
        self.run = run
        self.retry_failed = retry_failed
        self.config = read(run / 'run_config.json')
        self.validation_workers = self.config.get('validation_workers', 4)
        if type(self.validation_workers) is not int or not 1 <= self.validation_workers <= 16:
            raise ValueError('validation_workers must be an integer from 1 to 16')
        cooldown = self.config.get('concurrency_cooldown_seconds', 600)
        if type(cooldown) is not int or cooldown < 1:
            raise ValueError('concurrency_cooldown_seconds must be a positive integer')
        if 'concurrency_ladder' in self.config:
            ladder = self.config['concurrency_ladder']
            if (not isinstance(ladder, list) or not ladder
                    or any(type(n) is not int or not 1 <= n <= self.config['max_workers'] for n in ladder)
                    or any(a <= b for a, b in zip(ladder, ladder[1:]))):
                raise ValueError('concurrency_ladder must contain descending positive integers within max_workers')
        self.prepared = Path(self.config['prepared_path'])
        self.plan = read(self.prepared / 'selection_manifest.json')
        self.scope = read(run / 'scope_input.json')
        sys.path.insert(0, str(run / 'source_snapshot'))
        sys.path.insert(0, str(run / 'source_snapshot/check_diff'))
        import runner
        import prepare_source_data
        self.runner, self.tokens, self.prepare = runner, runner.token_usage, prepare_source_data
        self.stop = threading.Event()
        self.process_lock = threading.Lock()
        self.processes = set()
        self.active = {}
        self.pdf_jobs = {}
        self.pdf_states = {}
        self.states = {}
        self.latest_outcomes = []
        self.started = time.monotonic()
        self.started_at = now()
        self.max_active = 0
        previous_progress = run / 'progress.json'
        if previous_progress.exists():
            previous_peak = read(previous_progress).get('max_observed_concurrency', 0)
            if type(previous_peak) is int:
                self.max_active = previous_peak
        self.last_scale = self.started
        if 'concurrency_ladder' in self.config:
            self.last_scale -= cooldown  # New preflighted allocations need no warmup before lowering.
            history = run / 'concurrency_history.jsonl'
            if history.exists():
                for line in reversed(history.read_text().splitlines()):
                    change = json.loads(line)
                    if change.get('policy') == 'concurrency_ladder':
                        age = (datetime.now(timezone.utc) - datetime.fromisoformat(change['at_utc'])).total_seconds()
                        self.last_scale = self.started - max(0, age)
                        break
        self.last_progress = 0
        self.last_totals = 0
        self.blocker = None
        self.inventory = {}
        self.versions = {}
        for row in map(json.loads, (run / 'version_inventory.jsonl').read_text().splitlines()):
            self.inventory[row['requested_id']] = row
            self.versions[row['requested_id']] = row['frozen_version_metadata']
        self.signature = hashlib.sha256(json.dumps({
            'model': self.config['model'], 'effort': self.config['reasoning_effort'],
            'compare': sha(run / 'source_snapshot/check_diff/prompts/compare.md'),
            'rubric': sha(run / 'source_snapshot/check_diff/prompts/rubric.md'),
            'schema': sha(run / 'source_snapshot/check_diff/schema.py'),
            'scope': sha(run / 'scope_input.json')}, sort_keys=True).encode()).hexdigest()
        self.pdfs = {}
        for request in self.plan['requested_pdfs']:
            key = request['requested_id']
            observation = self.inventory[key]['pdf_observation']
            pdf = observation['pdf']
            if pdf['availability_status'] != 'verified':
                self.pdf_states[key] = {'status': 'officially_unavailable', 'evidence': observation}
                continue
            row = {'requested_id': key, 'arxiv_id': request['arxiv_id'], 'version': request['version'],
                   'local_path': str(Path(self.config['pdf_root']) / request['arxiv_id'] / f'{key}.pdf'),
                   'expected_bytes': pdf['bytes'], 'expected_sha256': pdf['sha256'],
                   'expected_pages': observation['page_count']['value']}
            self.pdfs[key] = row
            previous = run / 'pdf_state' / f'{key}.json'
            path = Path(row['local_path'])
            if previous.exists() and path.is_file():
                checked = read(previous)
                if (checked.get('status') == 'verified' and checked.get('sha256') == row['expected_sha256']
                        and checked.get('bytes') == path.stat().st_size == row['expected_bytes']
                        and checked.get('mtime_ns') == path.stat().st_mtime_ns):
                    self.pdf_states[key] = checked
        self.pairs = []
        self.unit_pairs = {}
        for unit in self.plan['selected_units']:
            aid = unit['arxiv_id']
            self.unit_pairs[aid] = []
            for edge in unit['selected_transitions']:
                before, after = edge['from_version'], edge['to_version']
                key = f'{aid}__v{before}_v{after}'
                ids = [f'{aid}v{before}', f'{aid}v{after}']
                pair = {'key': key, 'arxiv_id': aid, 'before': before, 'after': after,
                        'pdf_ids': ids, 'gap_seconds': edge['gap_seconds']}
                if pair['gap_seconds'] <= 2592000:
                    raise ValueError(f'Invalid selected gap: {key}')
                self.pairs.append(pair)
                self.unit_pairs[aid].append(key)
                self.states[key] = {'status': 'pending', 'attempt_count': 0}
                saved = run / 'pair_state' / f'{key}.json'
                if saved.exists():
                    self.states[key] = read(saved)
                    if self.states[key]['status'] in ('running', 'retry_wait', 'interrupted'):
                        self.states[key]['status'] = 'pending'
                    if retry_failed and self.states[key]['status'] in ('failed', 'input_invalid', 'input_missing'):
                        self.states[key]['status'] = 'pending'
                if any(i not in self.pdfs for i in ids):
                    self.states[key] = {'status': 'input_unavailable', 'attempt_count': 0,
                                        'reason': 'A fixed selected endpoint officially has no PDF.'}
                    continue
                receipt = self.saved_result(pair)
                if receipt:
                    self.states[key] = {'status': 'completed', 'attempt_count': len(receipt['attempts']),
                                        'output_sha256': receipt['output_sha256']}
                elif self.states[key]['status'] == 'completed':
                    self.states[key]['status'] = 'needs_artifact_recovery'
        if len(self.pairs) != self.plan['summary']['pair_count']:
            raise ValueError('Pair scope differs from the fixed preparation plan')
        contract = run / 'execution_contract.json'
        if contract.exists() and read(contract).get('execution_signature') != self.signature:
            raise ValueError('Frozen execution contract has changed')
        save(contract, {'execution_signature': self.signature,
             'requested_model': self.config['model'], 'requested_reasoning_effort': self.config['reasoning_effort'],
             'unit_count': len(self.unit_pairs), 'comparison_count': len(self.pairs),
             'source_membership_counts': {s: sum(s in v for v in self.scope['record_memberships'].values())
                                          for s in self.config['source_names']}})

    def recover_unfinished_attempts(self):
        """Stop only verified orphan processes from this run; preserve their usage and artifacts."""
        for pair in self.pairs:
            for root in sorted((self.run / 'attempts' / pair['key']).glob('attempt-*')):
                if (root / 'receipt.json').exists():
                    continue
                number = int(root.name.split('-')[1])
                canonical = self.saved_result(pair)
                embedded = next((a for a in (canonical or {}).get('attempts', [])
                                 if a.get('attempt') == number), None)
                if embedded is not None:
                    missing = []
                    for filename, expected_hash in embedded.get('artifact_sha256', {}).items():
                        if Path(filename).name != filename:
                            raise ValueError(f'Unsafe embedded artifact path: {filename}')
                        if not (root / filename).exists() and embedded.get('status') != 'completed':
                            missing.append(filename)
                            continue
                        if sha(root / filename) != expected_hash:
                            raise ValueError(f'Cannot restore an inconsistent archived receipt: {root}')
                    if embedded.get('status') == 'completed' and sha(root / 'result.json') != embedded.get('result_sha256'):
                        raise ValueError(f'Cannot restore an inconsistent result receipt: {root}')
                    restored = {**embedded, 'recovered_from_consolidated_receipt': True}
                    if missing:
                        restored['missing_archived_artifacts'] = missing
                    save(root / 'receipt.json', restored)
                    continue
                process_file = root / 'process.json'
                previous = read(process_file) if process_file.exists() else {}
                pid = previous.get('pid')
                if type(pid) is int:
                    command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                             capture_output=True, text=True).stdout.strip()
                    if str(root / 'work') in command and 'codex exec' in command:
                        try:
                            if os.getpgid(pid) == pid:
                                os.killpg(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        for _ in range(20):
                            current = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                                     capture_output=True, text=True).stdout.strip()
                            if str(root / 'work') not in current:
                                break
                            time.sleep(0.25)
                        else:
                            raise RuntimeError(f'Orphan process did not stop; preserving its workspace: {pid}')
                raw = root / 'work/response.json'
                if raw.is_file() and not raw.is_symlink():
                    shutil.copyfile(raw, root / 'response.raw.json')
                usage = {'source': 'codex_exec_turn_completed', 'records': [], 'errors': []}
                events = root / 'events.jsonl'
                if events.exists():
                    with events.open() as stream:
                        usage = self.tokens.read_events(stream)
                receipt = {'run_id': self.config['run_id'], 'arxiv_id': pair['arxiv_id'],
                    'pair': [pair['before'], pair['after']], 'attempt': int(root.name.split('-')[1]),
                    'status': 'interrupted', 'started_at_utc': previous.get('started_at_utc'),
                    'finished_at_utc': now(), 'elapsed_seconds': None,
                    'elapsed_time_status': 'unavailable_after_coordinator_interruption',
                    'requested_model': self.config['model'],
                    'requested_reasoning_effort': self.config['reasoning_effort'],
                    'execution_signature': self.signature, 'input_sha256': self.expected_hashes(pair),
                    'token_usage': usage, 'error': 'Previous coordinator ended before archiving this attempt.'}
                receipt['artifact_sha256'] = {p.name: sha(p) for p in root.iterdir() if p.is_file()}
                save(root / 'receipt.json', receipt)
                for scratch in (root / 'work', root / 'cli_state'):
                    if scratch.is_dir() and not scratch.is_symlink():
                        shutil.rmtree(scratch)

    def reconcile_completed_artifacts(self):
        for pair in self.pairs:
            if self.states[pair['key']]['status'] == 'input_unavailable':
                continue
            target = self.output_path(pair)
            current = self.saved_result(pair)
            if current is not None:
                # Never replace a valid canonical result with a historical candidate.
                self.states[pair['key']]['status'] = 'completed'
                continue
            candidates = sorted((self.run / 'attempts' / pair['key']).glob('attempt-*/receipt.json'), reverse=True)
            selected = None
            for receipt_path in candidates:
                receipt = read(receipt_path)
                if receipt.get('status') != 'completed':
                    continue
                if (receipt.get('execution_signature') != self.signature or
                        receipt.get('input_sha256') != self.expected_hashes(pair)):
                    raise ValueError(f'Archived completion has different inputs or settings: {receipt_path}')
                result_path = receipt_path.parent / 'result.json'
                self.runner.validate_result(read(result_path))
                if sha(result_path) != receipt.get('result_sha256'):
                    raise ValueError(f'Archived result hash mismatch: {result_path}')
                for filename, expected_hash in receipt.get('artifact_sha256', {}).items():
                    if Path(filename).name != filename or sha(receipt_path.parent / filename) != expected_hash:
                        raise ValueError(f'Archived artifact mismatch: {receipt_path.parent / filename}')
                if target.exists() and read(target) != read(result_path):
                    raise ValueError(f'Existing output differs from its archive; preserving both: {target}')
                selected = receipt
                break
            if selected is not None:
                self.finish_attempt(pair, selected, observed=False)
            elif recovered := next((value for p in candidates
                                    if (value := self.recover_completed_turn(pair, p.parent))), None):
                self.finish_attempt(pair, recovered, observed=False)
            elif self.states[pair['key']]['status'] == 'needs_artifact_recovery':
                raise ValueError(f'Completed state has no verified output or recoverable archive: {pair["key"]}')
            else:
                previous = self.all_attempt_records(pair)
                if previous and previous[-1].get('status') in ('failed', 'interrupted'):
                    if self.retry_failed:
                        self.states[pair['key']] = {'status': 'pending', 'attempt_count': len(previous),
                            'explicit_retry_authorized': True}
                    else:
                        self.finish_attempt(pair, previous[-1], observed=False)
                        if self.states[pair['key']]['status'] == 'interrupted':
                            self.states[pair['key']]['status'] = 'pending'
                    save(self.run / 'pair_state' / f'{pair["key"]}.json', self.states[pair['key']])

    def saved_result(self, pair):
        receipt = usable_saved_result(self.output_path(pair), self.signature, self.expected_hashes(pair),
                                      self.runner.validate_result)
        if receipt and 'recovery' in receipt:
            root = self.run / 'attempts' / pair['key'] / f'attempt-{receipt["attempt"]:03d}'
            if not (root / 'recovery.json').is_file():
                raise ValueError('Recovered canonical result is missing its recovery evidence')
            proven = self.recover_completed_turn(pair, root)
            derived = {'token_usage', 'elapsed_seconds'}
            if (not proven or any(receipt.get(k) != v for k, v in proven.items() if k not in derived)
                    or read(self.output_path(pair)) != read(root / 'result.json')):
                raise ValueError('Recovered canonical result differs from proven evidence')
        return receipt

    def recover_completed_turn(self, pair, root):
        """Derive a judgment from proven archived evidence; never rewrite its receipt."""
        if self.stop.is_set():
            return None
        marker_path = root / 'recovery.json'
        try:
            def regular(path):
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f'Missing regular recovery artifact: {path}')
                return path.read_bytes()

            original_bytes = regular(root / 'receipt.json')
            original = recovery.decode(original_bytes)
            if not recovery.eligible_failure(original):
                if marker_path.exists():
                    raise ValueError('Recovery marker has no eligible original failure')
                return None
            expected = {'run_id': self.config['run_id'], 'arxiv_id': pair['arxiv_id'],
                'pair': [pair['before'], pair['after']], 'attempt': int(root.name.split('-')[1]),
                'execution_signature': self.signature, 'requested_model': self.config['model'],
                'requested_reasoning_effort': self.config['reasoning_effort'],
                'input_sha256': self.expected_hashes(pair), 'turn_completed_count': 1, 'turn_failed_count': 0}
            if any(original.get(k) != v for k, v in expected.items()):
                raise ValueError('Recovery receipt identity or turn counts mismatch')
            artifacts = original['artifact_sha256']
            required = {'events.jsonl', 'response.raw.json', 'prompt.txt', 'command.json'}
            files = {p.name for p in root.iterdir() if p.is_file() or p.is_symlink()}
            if not required <= artifacts.keys() or files - {'receipt.json', 'recovery.json', 'result.json'} != set(artifacts) - {'result.json'}:
                raise ValueError('Recovery archive file scope mismatch')
            for name, digest in artifacts.items():
                if Path(name).name != name or name in {'receipt.json', 'recovery.json'}:
                    raise ValueError('Unsafe recovery artifact name')
                if hashlib.sha256(regular(root / name)).hexdigest() != digest:
                    raise ValueError('Recovery archived artifact hash mismatch')
            raw = regular(root / 'response.raw.json')
            result = recovery.decode(raw)
            if 'result_label_normalization' in original:
                if original['result_label_normalization'] != 'runner.normalize_result_labels':
                    raise ValueError('Unknown result label normalization')
                result = self.runner.normalize_result_labels(result)
            self.runner.validate_result(result)
            usage = recovery.completed_turn(raw, regular(root / 'events.jsonl'))
            if usage != original.get('token_usage'):
                raise ValueError('Recovery reported usage mismatch')
            folder = self.prepared / pair['arxiv_id'] / f'v{pair["before"]}_v{pair["after"]}'
            endpoint = self.runner.Pair(folder, pair['arxiv_id'], pair['before'], pair['after'])
            prompt_hash = hashlib.sha256(self.runner.make_prompt(endpoint).encode()).hexdigest()
            if sha(root / 'prompt.txt') != prompt_hash or original.get('prompt_sha256') != prompt_hash:
                raise ValueError('Recovery frozen prompt mismatch')
            command = recovery.decode(regular(root / 'command.json'))
            settings = SimpleNamespace(codex=self.config['codex_path'], model=self.config['model'],
                                       effort=self.config['reasoning_effort'])
            if command != {'argv': self.runner.build_command(settings, root / 'work'),
                           'working_directory': str(root / 'work')}:
                raise ValueError('Recovery command settings mismatch')
            inputs = {item['requested_id']: item for item in original['inputs']}
            if len(inputs) != len(original['inputs']) or set(inputs) != set(pair['pdf_ids']):
                raise ValueError('Recovery input scope mismatch')
            for key, pdf in zip(pair['pdf_ids'], endpoint.pdfs):
                row, item = self.pdfs[key], inputs[key]
                identity = {'arxiv_id': row['arxiv_id'], 'version': row['version'],
                            'sha256': row['expected_sha256'], 'bytes': row['expected_bytes'],
                            'path': str(Path(row['local_path']).resolve()), 'pair_path': str(pdf)}
                if any(item.get(k) != v for k, v in identity.items()):
                    raise ValueError('Recovery input identity mismatch')
                for path in (Path(row['local_path']), pdf):
                    # Missing source PDFs do not erase the archived input evidence.
                    if path.is_symlink():
                        raise ValueError(f'Symlink recovery PDF: {path}')
                    try:
                        size = path.stat().st_size
                    except FileNotFoundError:
                        continue
                    if not path.is_file() or size != row['expected_bytes'] or sha(path) != row['expected_sha256']:
                        raise ValueError(f'Recovery source PDF changed: {path}')
            result_path = root / 'result.json'
            if result_path.exists() and recovery.decode(regular(result_path)) != result:
                raise ValueError('Recovery result disagrees with raw response')
            # Use the same serialization as normal finalization; no synthetic attempt.
            result_bytes = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
            evidence = {'kind': recovery.KIND, 'original_receipt_sha256': hashlib.sha256(original_bytes).hexdigest(),
                        'result_sha256': hashlib.sha256(result_bytes).hexdigest(), 'warning': recovery.WARNING}
            if marker_path.exists() and recovery.decode(regular(marker_path)) != evidence:
                raise ValueError('Recovery marker mismatch')
            if self.stop.is_set():
                return None
            if not result_path.exists():
                save(result_path, result)
            if sha(result_path) != evidence['result_sha256']:
                raise ValueError('Recovery result bytes mismatch')
            if not marker_path.exists():
                save(marker_path, evidence)
            return {**original, 'status': 'completed', 'result_sha256': evidence['result_sha256'],
                    'recovery': evidence}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            if marker_path.exists():
                raise  # Existing recovery claims must never be silently trusted or replaced.
            return None

    def output_path(self, pair):
        return self.run / 'output' / pair['arxiv_id'] / f'diff_v{pair["before"]}_v{pair["after"]}.json'

    def expected_hashes(self, pair):
        return {key: self.pdfs[key]['expected_sha256'] for key in pair['pdf_ids']}

    def terminate(self, *_):
        self.stop.set()
        with self.process_lock:
            processes = list(self.processes)
        for process in processes:
            self.runner.terminate(process)

    def prepare_pair(self, pair):
        folder = self.prepared / pair['arxiv_id'] / f'v{pair["before"]}_v{pair["after"]}'
        folder.mkdir(parents=True, exist_ok=True)
        inputs = []
        for key in pair['pdf_ids']:
            row = self.pdfs[key]
            source = Path(row['local_path'])
            if sha(source) != row['expected_sha256']:
                raise ValueError(f'Original PDF changed after validation: {key}')
            target = folder / f'v{row["version"]}.pdf'
            self.prepare.materialize(source, target, row['expected_sha256'])
            inputs.append({**self.pdf_states[key], 'version': row['version'], 'pair_path': str(target)})
        save(folder / 'pair.json', {**pair, 'inputs': inputs,
             'scope': 'Only the selected before and after versions; full inventory is external.'})
        return self.runner.Pair(folder, pair['arxiv_id'], pair['before'], pair['after']), inputs

    def attempt(self, pair, number):
        root = self.run / 'attempts' / pair['key'] / f'attempt-{number:03d}'
        root.mkdir(parents=True, exist_ok=False)
        work = root / 'work'
        work.mkdir()
        receipt = {'run_id': self.config['run_id'], 'arxiv_id': pair['arxiv_id'],
            'pair': [pair['before'], pair['after']], 'attempt': number, 'status': 'failed',
            'started_at_utc': now(), 'requested_model': self.config['model'],
            'requested_reasoning_effort': self.config['reasoning_effort'],
            'execution_signature': self.signature, 'input_sha256': self.expected_hashes(pair)}
        # Mark only new attempts; historical receipts keep their exact raw equality contract.
        if callable(getattr(self.runner, 'normalize_result_labels', None)):
            receipt['result_label_normalization'] = 'runner.normalize_result_labels'
        process = None
        started = time.monotonic()
        receipt['started_monotonic'] = started
        try:
            endpoint_pair, inputs = self.prepare_pair(pair)
            receipt['inputs'] = inputs
            save(work / 'schema.json', self.runner.SCHEMA)
            prompt = self.runner.make_prompt(endpoint_pair)
            (root / 'prompt.txt').write_text(prompt)
            receipt['prompt_sha256'] = sha(root / 'prompt.txt')
            env = self.runner.task_environment(root / 'cli_state')
            settings = SimpleNamespace(codex=self.config['codex_path'], model=self.config['model'],
                                       effort=self.config['reasoning_effort'])
            command = self.runner.build_command(settings, work)
            save(root / 'command.json', {'argv': command, 'working_directory': str(work)})
            with (root / 'events.jsonl').open('w+') as events, (root / 'stderr.log').open('w+') as stderr:
                with self.process_lock:
                    if self.stop.is_set():
                        raise RuntimeError('Run interrupted before model dispatch')
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events, stderr=stderr,
                                               text=True, start_new_session=True, env=env)
                    self.processes.add(process)
                save(root / 'process.json', {'pid': process.pid, 'started_at_utc': receipt['started_at_utc']})
                receipt['pid'] = process.pid
                try:
                    process.communicate(prompt, timeout=self.config['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    receipt['timed_out'] = True
                    self.runner.terminate(process)
                finally:
                    if process.poll() is None:
                        self.runner.terminate(process)
                    events.flush()
                    receipt['exit_code'] = process.returncode
                    receipt['token_usage'] = self.tokens.read_events(events)
                stderr.seek(0)
                diagnostic = stderr.read()[-6000:]
                receipt['transport_diagnostics'] = diagnostic
                events.seek(0)
                event_errors, turn_completed, turn_failed = [], 0, 0
                for line in events:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('type') in ('error', 'turn.failed'):
                        event_errors.append(event)
                    turn_completed += event.get('type') == 'turn.completed'
                    turn_failed += event.get('type') == 'turn.failed'
                receipt.update(event_errors=event_errors, turn_completed_count=turn_completed,
                               turn_failed_count=turn_failed)
                if self.stop.is_set():
                    raise RuntimeError('Run interrupted by its coordinator')
                if receipt.get('timed_out') or process.returncode or not turn_completed or turn_failed:
                    raise RuntimeError(f'Codex execution failed (exit={process.returncode}, '
                        f'timeout={receipt.get("timed_out", False)}): '
                        + json.dumps(event_errors[-2:]) + ' ' + diagnostic)
            response = work / 'response.json'
            if response.is_symlink() or not response.is_file():
                raise ValueError('Missing regular response.json')
            shutil.copyfile(response, root / 'response.raw.json')
            result = read(response)
            if 'result_label_normalization' in receipt:
                result = self.runner.normalize_result_labels(result)
            self.runner.validate_result(result)
            for key, pdf in zip(pair['pdf_ids'], endpoint_pair.pdfs):
                if sha(pdf) != self.pdfs[key]['expected_sha256']:
                    raise ValueError(f'Input PDF changed during comparison: {key}')
            save(root / 'result.json', result)
            receipt['status'] = 'completed'
            receipt['result_sha256'] = sha(root / 'result.json')
        except Exception as exc:
            receipt['error'] = f'{type(exc).__name__}: {exc}'
            receipt['terminal_error'] = terminal_error(receipt['error'])
            if self.stop.is_set():
                receipt['status'] = 'interrupted'
        finally:
            if process is not None:
                self.runner.terminate(process)
                with self.process_lock:
                    self.processes.discard(process)
            raw = work / 'response.json'
            if raw.is_file() and not raw.is_symlink() and not (root / 'response.raw.json').exists():
                shutil.copyfile(raw, root / 'response.raw.json')
            receipt.update(finished_at_utc=now(), elapsed_seconds=round(time.monotonic() - started, 3))
            receipt['artifact_sha256'] = {p.name: sha(p) for p in root.iterdir()
                                          if p.is_file() and p.name != 'receipt.json'}
            save(root / 'receipt.json', receipt)
            # Keep prompts, events, raw results, counters, and receipts; discard only this attempt's scratch.
            for scratch in (work, root / 'cli_state'):
                if scratch.is_dir() and not scratch.is_symlink():
                    try:
                        shutil.rmtree(scratch)
                    except OSError as exc:
                        receipt.setdefault('cleanup_errors', []).append(str(exc))
            if receipt.get('cleanup_errors'):
                save(root / 'receipt.json', receipt)
        return self.recover_completed_turn(pair, root) or receipt

    def all_attempt_records(self, pair):
        records = {}
        canonical = self.output_path(pair).with_suffix('.execution.json')
        if canonical.exists():
            for attempt in read(canonical).get('attempts', []):
                records[attempt['attempt']] = attempt
        for root in sorted((self.run / 'attempts' / pair['key']).glob('attempt-*')):
            number = int(root.name.split('-')[1])
            receipt = root / 'receipt.json'
            if receipt.exists():
                records[number] = read(receipt)
            elif number not in records:
                events = root / 'events.jsonl'
                usage = {'source': 'codex_exec_turn_completed', 'records': [], 'errors': []}
                if events.exists():
                    with events.open() as stream:
                        usage = self.tokens.read_events(stream)
                records[number] = {'attempt': number, 'status': 'awaiting_final_receipt',
                                   'elapsed_seconds': None, 'token_usage': usage}
        return [records[number] for number in sorted(records)]

    def finish_attempt(self, pair, receipt, observed=True):
        key = pair['key']
        attempts = self.all_attempt_records(pair)
        known_seconds = [a['elapsed_seconds'] for a in attempts if a.get('elapsed_seconds') is not None]
        metadata = {**receipt, 'attempts': attempts, 'token_usage': self.tokens.summarize(attempts),
                    'elapsed_seconds': round(sum(known_seconds), 3) if len(known_seconds) == len(attempts) else None,
                    'reported_attempt_seconds': round(sum(known_seconds), 3),
                    'attempts_without_timing': len(attempts) - len(known_seconds)}
        if observed:
            self.latest_outcomes.append(attempt_observation(receipt, time.monotonic()))
        if receipt['status'] == 'completed':
            source = self.run / 'attempts' / key / f'attempt-{receipt["attempt"]:03d}' / 'result.json'
            target = self.output_path(pair)
            save(target, read(source))
            metadata.update(output_path=str(target), output_sha256=sha(target))
            state = {'status': 'completed', 'attempt_count': len(attempts), 'output_sha256': sha(target)}
        elif receipt['status'] == 'interrupted':
            if len(attempts) >= self.config['max_attempts'] and not self.stop.is_set():
                state = {'status': 'failed', 'attempt_count': len(attempts),
                         'error': 'Attempt budget exhausted after interrupted execution'}
                metadata['status'] = 'failed'
            else:
                state = {'status': 'interrupted', 'attempt_count': len(attempts)}
        elif receipt.get('terminal_error') == 'account_or_authentication':
            self.blocker = receipt['error'][-2000:]
            state = {'status': 'retry_wait', 'attempt_count': len(attempts), 'retry_at': time.time() + 300}
            control = read(self.run / 'control.json')
            control.update(pause_dispatch=True, pause_reason='account_or_authentication')
            save(self.run / 'control.json', control)
        elif receipt.get('terminal_error') or len(attempts) >= self.config['max_attempts']:
            state = {'status': 'failed', 'attempt_count': len(attempts), 'error': receipt.get('error')}
        else:
            state = {'status': 'retry_wait', 'attempt_count': len(attempts),
                     'retry_at': time.time() + self.config['retry_base_seconds'] * len(attempts),
                     'error': receipt.get('error')}
        save(self.output_path(pair).with_suffix('.execution.json'), metadata)
        self.states[key] = state
        save(self.run / 'pair_state' / f'{key}.json', state)

    def summary(self):
        counts = Counter(s['status'] for s in self.states.values())
        unit_counts = Counter()
        by_source = {source: {'units': 0, 'completed_units': 0, 'pairs': 0, 'completed_pairs': 0}
                     for source in self.config['source_names']}
        for aid, keys in self.unit_pairs.items():
            statuses = [self.states[k]['status'] for k in keys]
            status = ('completed' if all(s == 'completed' for s in statuses) else
                      'input_unavailable' if 'input_unavailable' in statuses else
                      'failed' if any(s in ('failed', 'input_invalid', 'input_missing') for s in statuses)
                      else 'in_progress')
            unit_counts[status] += 1
            for source in self.scope['record_memberships'][aid]:
                row = by_source[source]
                row['units'] += 1
                row['pairs'] += len(keys)
                row['completed_pairs'] += statuses.count('completed')
                row['completed_units'] += status == 'completed'
        return {'pair_status_counts': dict(counts), 'unit_status_counts': dict(unit_counts),
                'sources': by_source}

    def write_totals(self):
        attempts = [attempt for pair in self.pairs for attempt in self.all_attempt_records(pair)]
        save(self.run / 'token_usage.json', {'run_id': self.config['run_id'], 'updated_at_utc': now(),
             'scope': 'All archived attempts from this batch, including resumed and failed attempts',
             'token_usage': self.tokens.summarize(attempts),
             'active_attempt_count': len(self.active)})
        path = self.run / 'pair_status.jsonl'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(''.join(json.dumps({**pair, **self.states[pair['key']]}) + '\n' for pair in self.pairs))
        tmp.replace(path)

    def scale(self, control):
        if self.stop.is_set() or control.get('pause_dispatch'):
            return control
        if 'concurrency_ladder' in self.config:
            return self.lower_concurrency(control)
        if not control.get('auto_scale', True) or time.monotonic() - self.last_scale < 600:
            return control
        recent = [o for o in self.latest_outcomes if time.monotonic() - o['at'] < 900]
        if len(recent) < 5:
            return control
        free_percent = 0
        try:
            text = subprocess.check_output(['memory_pressure', '-Q'], text=True, timeout=10)
            free_percent = int(re.search(r'free percentage:\s*(\d+)%', text).group(1))
        except Exception:
            pass
        failures = sum(o['status'] != 'completed' for o in recent) / len(recent)
        connection_rate = sum(o['connection_errors'] >= 2 for o in recent) / len(recent)
        previous = control['workers']
        if failures > 0.15 or connection_rate > 0.25 or (free_percent and free_percent < 18):
            control['workers'] = max(8, previous // 2)
        elif free_percent >= 25 and failures <= 0.05 and connection_rate <= 0.10:
            control['workers'] = min(self.config['max_workers'], previous + (12 if previous < 32 else 16))
        self.last_scale = time.monotonic()
        if control['workers'] != previous:
            latest = read(self.run / 'control.json')
            if not latest.get('auto_scale', True) or latest['workers'] != previous:
                return latest
            latest['workers'] = control['workers']
            with (self.run / 'concurrency_history.jsonl').open('a') as stream:
                stream.write(json.dumps({'at_utc': now(), 'from': previous, 'to': control['workers'],
                    'completed_observations': len(recent), 'failure_rate': failures,
                    'connection_retry_rate': connection_rate, 'free_memory_percent': free_percent}) + '\n')
            save(self.run / 'control.json', latest)
            control = latest
        return control

    def lower_concurrency(self, control):
        """Lower admissions only; active futures and their CLI calls are never cancelled."""
        current_time = time.monotonic()
        cooldown = self.config.get('concurrency_cooldown_seconds', 600)
        if not control.get('auto_scale', False) or current_time - self.last_scale < cooldown:
            return control
        previous = control['workers']
        if type(previous) is not int or not 1 <= previous <= self.config['max_workers']:
            raise ValueError('Invalid live comparison concurrency')
        lower = next((n for n in self.config['concurrency_ladder'] if n < previous), None)
        if lower is None:
            return control
        # Observations consumed by an earlier reduction cannot trigger another one.
        recent = [o for o in self.latest_outcomes if self.last_scale < o['started_at'] <= o['at'] <= current_time
                  and current_time - o['at'] < 900]
        self.latest_outcomes = recent
        count = len(recent)
        failure_rate = sum(o['outer_attempt_failed'] for o in recent) / count if count else 0
        transport_rate = sum(o['transport_pressure'] for o in recent) / count if count else 0
        resource_rate = sum(o['resource_pressure'] for o in recent) / count if count else 0
        failed_transport = sum(o['outer_attempt_failed'] and o['transport_pressure'] for o in recent)
        memory = memory_observation()
        reasons = []
        if failed_transport >= 3 or (count >= 5 and transport_rate > 0.25):
            reasons.append('transport_pressure')
        if count >= 5:
            if resource_rate > 0.15:
                reasons.append('resource_failures')
        free = memory['free_percent']
        if free is not None and free < 18:
            reasons.append('allocation_memory_pressure')
        # A fresh read preserves manual pause/auth/worker/budget changes during sampling.
        latest = read(self.run / 'control.json')
        if latest != control or self.stop.is_set():
            return latest
        if reasons:
            latest['workers'] = lower
            save(self.run / 'control.json', latest)
            self.last_scale = current_time
            with (self.run / 'concurrency_history.jsonl').open('a') as stream:
                stream.write(json.dumps({'at_utc': now(), 'policy': 'concurrency_ladder',
                    'from': previous, 'to': lower, 'reasons': reasons,
                    'observations': count, 'outer_failure_rate': failure_rate,
                    'failed_transport_attempts': failed_transport,
                    'transport_pressure_rate': transport_rate, 'resource_failure_rate': resource_rate,
                    'memory': memory, 'cooldown_seconds': cooldown, 'observation_window_seconds': 900,
                    'minimum_failed_transport_attempts': 3, 'minimum_rate_observations': 5,
                    'active_calls_preserved': len(self.active)}) + '\n')
        return latest

    def execute(self):
        self.recover_unfinished_attempts()
        self.reconcile_completed_artifacts()
        run_start = self.run / 'run_started.json'
        if not run_start.exists():
            save(run_start, {'started_at_utc': self.started_at, 'execution_signature': self.signature})
        persistent_start = read(run_start)
        if persistent_start['execution_signature'] != self.signature:
            raise ValueError('A different experiment already used this output directory')
        batch_started = datetime.fromisoformat(persistent_start['started_at_utc'])
        save(self.run / 'process.json', {'pid': os.getpid(), 'started_at_utc': self.started_at,
                                       'argv': sys.argv, 'execution_signature': self.signature})
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.terminate)
        with ThreadPoolExecutor(max_workers=self.validation_workers) as validators, \
                ThreadPoolExecutor(max_workers=self.config['max_workers']) as workers:
            while True:
                for future, key in list(self.pdf_jobs.items()):
                    if not future.done():
                        continue
                    try:
                        state = future.result()
                    except Exception as exc:
                        state = {'status': 'invalid', 'error': str(exc)}
                    self.pdf_states[key] = state
                    save(self.run / 'pdf_state' / f'{key}.json', state)
                    del self.pdf_jobs[future]
                if not self.stop.is_set():
                    inflight = set(self.pdf_jobs.values())
                    for key, row in self.pdfs.items():
                        if len(self.pdf_jobs) >= self.validation_workers:
                            break
                        if key in self.pdf_states or key in inflight:
                            continue
                        path = Path(row['local_path'])
                        if path.is_file() and path.stat().st_size == row['expected_bytes']:
                            self.pdf_jobs[validators.submit(validate_cached_pdf, row, self.versions[key], self.prepare)] = key
                for future, pair in list(self.active.items()):
                    if not future.done():
                        continue
                    try:
                        self.finish_attempt(pair, future.result())
                    except Exception as exc:
                        self.states[pair['key']] = {'status': 'failed', 'error': f'Archival failure: {exc}'}
                        save(self.run / 'pair_state' / f'{pair["key"]}.json', self.states[pair['key']])
                    del self.active[future]
                control = self.scale(read(self.run / 'control.json'))
                limit = control['workers']
                if type(limit) is not int or not 1 <= limit <= self.config['max_workers']:
                    raise ValueError('Invalid live comparison concurrency')
                disk_free = shutil.disk_usage(self.run).free
                disk_blocked = disk_free < control.get('minimum_free_bytes', 12_000_000_000)
                transfer_file = self.run / 'transfer_progress.json'
                transfer = read(transfer_file) if transfer_file.exists() else {'status': 'not_started'}
                active_bytes = sum(self.pdfs[key]['expected_bytes'] for p in self.active.values()
                                   for key in p['pdf_ids'])
                byte_budget = control.get('active_input_byte_budget', 2_000_000_000)
                for pair in self.pairs:
                    state = self.states[pair['key']]
                    if state['status'] == 'retry_wait' and time.time() >= state.get('retry_at', 0):
                        state['status'] = 'pending'
                    if state['status'] != 'pending':
                        continue
                    if any(self.pdf_states.get(k, {}).get('status') == 'invalid' for k in pair['pdf_ids']):
                        self.states[pair['key']] = {'status': 'input_invalid', 'reason': 'Original PDF validation failed'}
                        continue
                    ready = all(self.pdf_states.get(k, {}).get('status') == 'verified' for k in pair['pdf_ids'])
                    if not ready:
                        if transfer.get('status') in ('completed', 'failed') and not self.pdf_jobs:
                            self.states[pair['key']] = {'status': 'input_missing', 'reason': 'Transfer settled without verified endpoints'}
                        continue
                    if (self.stop.is_set() or control.get('pause_dispatch') or disk_blocked
                            or len(self.active) >= limit):
                        continue
                    pair_bytes = sum(self.pdfs[key]['expected_bytes'] for key in pair['pdf_ids'])
                    if self.active and active_bytes + pair_bytes > byte_budget:
                        continue
                    root = self.run / 'attempts' / pair['key']
                    existing = [int(p.name.split('-')[1]) for p in root.glob('attempt-*') if p.is_dir()]
                    number = max(existing, default=0) + 1
                    self.states[pair['key']] = {'status': 'running', 'attempt_count': number,
                                                'started_at_utc': now()}
                    save(self.run / 'pair_state' / f'{pair["key"]}.json', self.states[pair['key']])
                    self.active[workers.submit(self.attempt, pair, number)] = pair
                    active_bytes += pair_bytes
                self.max_active = max(self.max_active, len(self.active))
                summary = self.summary()
                pending = any(summary['pair_status_counts'].get(s, 0) for s in ('pending', 'running', 'retry_wait'))
                finished = not pending and not self.active and not self.pdf_jobs
                if time.monotonic() - self.last_progress >= 15 or finished or self.stop.is_set():
                    progress = {**summary, 'run_id': self.config['run_id'], 'updated_at_utc': now(),
                        'status': 'stopping' if self.stop.is_set() else 'paused' if control.get('pause_dispatch') or disk_blocked else 'running',
                        'workers': limit, 'active_comparisons': [p['key'] for p in self.active.values()],
                        'max_observed_concurrency': self.max_active,
                        'verified_pdf_count': sum(s['status'] == 'verified' for s in self.pdf_states.values()),
                        'invalid_pdf_count': sum(s['status'] == 'invalid' for s in self.pdf_states.values()),
                        'selected_available_pdf_count': len(self.pdfs), 'transfer_status': transfer.get('status'),
                        'disk_free_bytes': disk_free, 'pause_reason': control.get('pause_reason') or ('low_disk_space' if disk_blocked else None),
                        'active_input_bytes': active_bytes, 'active_input_byte_budget': byte_budget,
                        'latest_blocker': self.blocker, 'wall_seconds': round(time.monotonic() - self.started, 3),
                        'batch_wall_seconds': round((datetime.now(timezone.utc) - batch_started).total_seconds(), 3)}
                    save(self.run / 'progress.json', progress)
                    print(json.dumps({k: progress[k] for k in ('updated_at_utc', 'status', 'pair_status_counts',
                         'unit_status_counts', 'workers', 'verified_pdf_count', 'transfer_status')}), flush=True)
                    self.last_progress = time.monotonic()
                if time.monotonic() - self.last_totals >= 60 or finished:
                    self.write_totals()
                    self.last_totals = time.monotonic()
                if finished or (self.stop.is_set() and not self.active):
                    self.write_totals()
                    counts = summary['pair_status_counts']
                    status = final_status(self.states, finished)
                    result = {**progress, **summary, 'status': status, 'finished_at_utc': now(),
                              'token_usage': read(self.run / 'token_usage.json')['token_usage'],
                              'execution_signature': self.signature, 'scientific_assessment_independently_verified': False}
                    save(self.run / 'run_manifest.json', result)
                    save(self.run / 'progress.json', result)
                    return 0 if status.startswith('completed') else 1
                self.stop.wait(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--retry-failed', action='store_true',
                        help='After diagnosing a failure, admit one additional attempt for settled failed pairs')
    args = parser.parse_args()
    run = args.run.resolve()
    with (run / 'batch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('This batch already has a running coordinator')
        try:
            return Batch(run, retry_failed=args.retry_failed).execute()
        except Exception as exc:
            failure = {'status': 'incomplete', 'updated_at_utc': now(),
                       'coordinator_error': f'{type(exc).__name__}: {exc}'}
            save(run / 'coordinator_error.json', failure)
            save(run / 'progress.json', failure)
            print(json.dumps(failure), flush=True)
            return 1


if __name__ == '__main__':
    sys.exit(main())

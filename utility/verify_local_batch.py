#!/usr/bin/env python3
"""Audit a fixed local batch without invoking its runner.

Only completed-turn recovery requires rereading accessible source PDF hashes.

Only publish final_verification.json and REPORT.md after a complete, consistent
audit. Otherwise publish verification_progress.json. Exit codes: 0 for a passed
audit (or an incomplete progress check), 1 for artifact errors, 2 for an
incomplete --require-complete check. Existing experiment evidence is never edited.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from types import ModuleType
from urllib.parse import urlparse

if __package__:
    from . import completed_turn as recovery
else:
    import completed_turn as recovery


FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens')
SUMMARY_FIELDS = ('source', 'status', 'reported_totals', 'attempt_count',
                  'attempts_without_reported_usage', 'reported_turn_count',
                  'field_report_counts', 'errors')
SETTLED = {'completed', 'input_unavailable'}
HASH = re.compile(r'[0-9a-f]{64}')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def decode(data):
    def invalid_constant(value):
        raise ValueError(f'Invalid JSON constant: {value}')
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def stamp(path):
    info = path.stat()
    return info.st_ino, info.st_size, info.st_mtime_ns


class Reader:
    """Remember observed file identities to reject a moving final snapshot."""
    def __init__(self):
        self.observed = {}
        self.hashes = {}
        self.moving = set()

    def blob(self, path):
        path = Path(path)
        if (path.suffix.lower() == '.pdf' or path.absolute() != path.resolve()
                or path.is_symlink() or not path.is_file()):
            raise ValueError(f'Expected a regular, non-PDF artifact: {path}')
        before = stamp(path)
        data = path.read_bytes()
        previous = self.observed.setdefault(path, before)
        if previous != before or before != stamp(path):
            self.moving.add(str(path))
        self.hashes[path] = digest(data)
        return data

    def json(self, path):
        return decode(self.blob(path))

    def rows(self, path):
        return [decode(line) for line in self.blob(path).splitlines() if line.strip()]

    def sha(self, path):
        path = Path(path)
        if path not in self.hashes:
            self.blob(path)
        return self.hashes[path]

    def changes(self):
        for path, before in self.observed.items():
            try:
                if path.is_symlink() or stamp(path) != before:
                    self.moving.add(str(path))
            except OSError:
                self.moving.add(str(path))
        return sorted(self.moving)

    def source_pdf(self, path, expected_bytes, expected_sha256):
        """Recovery alone rechecks accessible PDFs, streaming without parsing them."""
        path = Path(path)
        if path.is_symlink():
            raise ValueError(f'Symlink recovery PDF: {path}')
        try:
            before = stamp(path)
        except FileNotFoundError:
            return False
        if not path.is_file() or before[1] != expected_bytes:
            raise ValueError(f'Recovery PDF size/type mismatch: {path}')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        previous = self.observed.setdefault(path, before)
        if previous != before or before != stamp(path):
            self.moving.add(str(path))
        if actual != expected_sha256:
            raise ValueError(f'Recovery PDF hash mismatch: {path}')
        return True


def parse_events(data):
    """Reconstruct the runner's records independently, retaining unknown fields."""
    records, errors, invalid = [], [], []
    completed = failed = 0
    for number, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = decode(line)
        except ValueError:
            errors.append(f'Invalid event JSON at line {number}')
            invalid.append(errors[-1])
            continue
        if not isinstance(event, dict):
            continue
        failed += event.get('type') == 'turn.failed'
        if event.get('type') != 'turn.completed':
            continue
        completed += 1
        usage = event.get('usage')
        if not isinstance(usage, dict):
            errors.append(f'Missing turn usage at line {number}')
            continue
        bad = [key for key in FIELDS if key in usage and
               (type(usage[key]) is not int or usage[key] < 0)]
        if bad:
            errors.append(f'Invalid token counters at line {number}: {bad}')
            invalid.append(errors[-1])
            continue
        if (usage.get('cached_input_tokens', 0) > usage.get('input_tokens', float('inf')) or
                usage.get('reasoning_output_tokens', 0) > usage.get('output_tokens', float('inf'))):
            errors.append(f'Token subset exceeds its total at line {number}')
            invalid.append(errors[-1])
            continue
        records.append({'event_line': number, 'usage': usage})
    return ({'source': 'codex_exec_turn_completed', 'records': records, 'errors': errors},
            completed, failed, invalid)


def summarize(attempts):
    rows = [r['usage'] for a in attempts for r in a.get('token_usage', {}).get('records', [])]
    coverage = {key: sum(key in row for row in rows) for key in FIELDS}
    totals = {key: sum(row[key] for row in rows if key in row) if coverage[key] else None
              for key in FIELDS}
    complete_fields = bool(rows) and all(coverage[k] == len(rows)
                                         for k in ('input_tokens', 'output_tokens'))
    totals['total_tokens'] = totals['input_tokens'] + totals['output_tokens'] if complete_fields else None
    totals['non_cached_input_tokens'] = (
        totals['input_tokens'] - totals['cached_input_tokens']
        if rows and coverage['input_tokens'] == coverage['cached_input_tokens'] == len(rows) else None)
    if not attempts:
        totals = {key: 0 for key in totals}
    unknown = sum(not a.get('token_usage', {}).get('records') for a in attempts)
    errors = [e for a in attempts for e in a.get('token_usage', {}).get('errors', [])]
    complete = bool(attempts) and not unknown and not errors and complete_fields
    return {'source': 'codex_exec_turn_completed',
            'status': 'not_run' if not attempts else 'complete' if complete else 'partial' if rows else 'unavailable',
            'reported_totals': totals, 'attempt_count': len(attempts),
            'attempts_without_reported_usage': unknown, 'reported_turn_count': len(rows),
            'field_report_counts': coverage, 'errors': errors}


def signature(config, reader, run):
    snapshot = run / 'source_snapshot/check_diff'
    values = {'model': config['model'], 'effort': config['reasoning_effort'],
              'compare': reader.sha(snapshot / 'prompts/compare.md'),
              'rubric': reader.sha(snapshot / 'prompts/rubric.md'),
              'schema': reader.sha(snapshot / 'schema.py'), 'scope': reader.sha(run / 'scope_input.json')}
    return digest(json.dumps(values, sort_keys=True).encode())


def tree_files(root):
    # Never follow directory symlinks or descend into attempt scratch/PDF storage.
    if root.is_symlink():
        return {root}
    return {p for p in root.rglob('*') if p.is_file() or p.is_symlink()}


class Verifier:
    def __init__(self, run):
        self.run = Path(run).expanduser().resolve()
        self.reader = Reader()
        self.errors = []
        self.pending_artifacts = []
        self.attempt_records = {}
        self.recovered_attempts = {}
        self.report = {'schema_version': 'local-batch-verification-1', 'checked_at_utc': now(),
                       'run': str(self.run), 'status': 'in_progress', 'errors': self.errors,
                       'pdf_contents_read': False, 'pairs': []}

    def check(self, condition, code, detail):
        if not condition:
            self.errors.append({'code': code, 'detail': str(detail)})
        return bool(condition)

    def equal(self, actual, expected, code, detail):
        return self.check(actual == expected, code, detail)

    def mapping(self, rows, field, label):
        result = {}
        for row in rows:
            key = row[field]
            if key in result:
                raise ValueError(f'Duplicate {label}: {key}')
            result[key] = row
        return result

    def load_scope(self):
        r, run = self.reader, self.run
        self.config = r.json(run / 'run_config.json')
        self.contract = r.json(run / 'execution_contract.json')
        self.scope = r.json(run / 'scope_input.json')
        self.plan_path = Path(self.config['prepared_path']).expanduser().resolve() / 'selection_manifest.json'
        self.plan = r.json(self.plan_path)
        self.inventory = self.mapping(r.rows(run / 'version_inventory.jsonl'), 'requested_id', 'version')
        self.states = self.mapping(r.rows(run / 'pair_status.jsonl'), 'key', 'pair status')
        records = self.mapping(self.scope['records'], 'arxiv_id', 'scope unit')
        units = self.mapping(self.plan['selected_units'], 'arxiv_id', 'planned unit')
        self.memberships = self.scope['record_memberships']
        self.sources = self.config['source_names']
        self.equal(set(self.sources), set(self.scope['authorized_sources']), 'source_scope', 'authorized sources')
        self.equal(len(self.sources), len(set(self.sources)), 'source_scope', 'duplicate sources')
        self.equal(set(records), set(units), 'unit_scope', 'scope versus selection plan')
        self.equal(set(records), set(self.memberships), 'unit_scope', 'source membership coverage')
        for count in (self.config['unit_count'], self.contract['unit_count'],
                      self.scope['record_count'], self.plan['summary']['paper_count']):
            self.equal(count, len(records), 'unit_count', 'frozen unit counts disagree')
        self.equal(self.plan['source_sha256'], r.sha(run / 'scope_input.json'), 'scope_hash', self.plan_path)
        self.sig = signature(self.config, r, run)
        self.report.update(requested_model=self.config['model'],
                           requested_reasoning_effort=self.config['reasoning_effort'],
                           execution_signature=self.sig)
        self.equal(self.contract['execution_signature'], self.sig, 'execution_signature', 'frozen contract')
        for field, expected in [('requested_model', self.config['model']),
                                ('requested_reasoning_effort', self.config['reasoning_effort'])]:
            self.equal(self.contract.get(field), expected, field, 'execution contract')
        self.pairs, selected_ids, all_ids = {}, set(), set()
        for aid, record in records.items():
            if not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
                raise ValueError(f'Invalid paper ID: {aid}')
            membership = self.memberships[aid]
            self.check(bool(membership) and len(membership) == len(set(membership))
                       and set(membership) <= set(self.sources), 'source_scope', aid)
            versions = sorted(record['versions'], key=lambda v: v['version'])
            self.equal([v['version'] for v in versions], list(range(1, record['frozen_N'] + 1)),
                       'version_history', aid)
            selected, last_date = [], None
            for version in versions:
                number = version['version']
                requested = f'{aid}v{number}'
                all_ids.add(requested)
                inventory = self.inventory[requested]
                self.equal((inventory['arxiv_id'], inventory['version']), (aid, number),
                           'version_identity', requested)
                self.equal(inventory['frozen_version_metadata'], version, 'frozen_version_metadata', requested)
                date = datetime.fromisoformat(version['submitted_at'].replace('Z', '+00:00'))
                if date.tzinfo is None or (last_date is not None and date < last_date):
                    raise ValueError(f'Invalid full-history date ordering: {requested}')
                last_date = date
                if not selected or (date - selected[-1][1]).total_seconds() > 2592000:
                    selected.append((number, date))
            self.check(len(selected) >= 3, 'selected_sequence', aid)
            unit = units[aid]
            self.equal(unit['selected_version_ids'], [v for v, _ in selected], 'selected_sequence', aid)
            transitions = unit['selected_transitions']
            self.equal([(e['from_version'], e['to_version']) for e in transitions],
                       [(a[0], b[0]) for a, b in zip(selected, selected[1:])], 'planned_tuples', aid)
            selected_ids.update(f'{aid}v{number}' for number, _ in selected)
            dates = dict(selected)
            for edge in transitions:
                before, after = edge['from_version'], edge['to_version']
                gap = (dates[after] - dates[before]).total_seconds()
                self.equal(edge['gap_seconds'], gap, 'selected_gap', aid)
                key = f'{aid}__v{before}_v{after}'
                if key in self.pairs:
                    raise ValueError(f'Duplicate planned tuple: {key}')
                self.pairs[key] = {'key': key, 'arxiv_id': aid, 'before': before, 'after': after,
                                   'pdf_ids': [f'{aid}v{before}', f'{aid}v{after}'], 'gap_seconds': gap}
        self.equal(set(self.inventory), all_ids, 'version_inventory_scope', 'complete original history')
        self.equal({k for k, v in self.inventory.items() if v['selected_for_comparison']},
                   selected_ids, 'selected_inventory_scope', 'selected original flags')
        requested = self.mapping(self.plan['requested_pdfs'], 'requested_id', 'requested PDF')
        self.equal(set(requested), selected_ids, 'requested_pdf_scope', 'preparation plan')
        for count in (self.contract['comparison_count'], self.plan['summary']['pair_count']):
            self.equal(count, len(self.pairs), 'pair_count', 'planned pair count')
        self.equal(set(self.states), set(self.pairs), 'pair_status_scope', 'exact planned tuple set')
        self.unavailable, self.hashes = {}, {}
        for requested in sorted(selected_ids):
            row = self.inventory[requested]
            obs = row['pdf_observation']
            self.equal((obs['arxiv_id'], obs['version'], obs['requested_id']),
                       (row['arxiv_id'], row['version'], requested), 'observation_identity', requested)
            pdf = obs['pdf']
            if pdf['availability_status'] == 'verified':
                self.check(isinstance(pdf['sha256'], str) and bool(HASH.fullmatch(pdf['sha256']))
                           and type(pdf['bytes']) is int and pdf['bytes'] > 0, 'frozen_pdf_hash', requested)
                self.hashes[requested] = pdf['sha256']
                original = row['frozen_version_metadata']
                known = {value for value in (original.get('pdf', {}).get('sha256'),
                         original.get('pdf', {}).get('expected_sha256'),
                         original.get('page_count', {}).get('source_pdf_sha256')) if value}
                if known:
                    self.equal(known, {pdf['sha256']}, 'frozen_pdf_hash', requested)
            elif pdf['availability_status'] == 'official_no_pdf':
                evidence = obs.get('official_no_pdf_evidence') or {}
                url = urlparse(evidence.get('official_url', ''))
                valid = (evidence.get('requested_id') == requested and evidence.get('arxiv_id') == row['arxiv_id']
                         and evidence.get('version') == row['version'] and evidence.get('no_pdf_available') is True
                         and evidence.get('availability_status') == 'official_no_pdf'
                         and url.scheme == 'https' and url.netloc in {'arxiv.org', 'export.arxiv.org'}
                         and url.path in {f'/abs/{requested}', f'/pdf/{requested}'}
                         and bool(evidence.get('reason')) and bool(evidence.get('observed_at_utc'))
                         and pdf['sha256'] is None and pdf['bytes'] is None)
                if self.check(valid, 'unavailable_evidence', requested):
                    self.unavailable[requested] = evidence
            else:
                self.check(False, 'unavailable_evidence', f'{requested}: unproven missing input')
        source_counts = {s: sum(s in memberships for memberships in self.memberships.values())
                         for s in self.sources}
        self.equal(self.contract['source_membership_counts'], source_counts, 'source_counts', 'contract')
        if self.errors:
            return
        # Load only the validated saved schema; never import the model dispatcher.
        module = ModuleType('frozen_batch_schema')
        path = run / 'source_snapshot/check_diff/schema.py'
        exec(compile(r.blob(path), str(path), 'exec'), module.__dict__)
        self.validate_result = module.validate_result
        self.normalize_result_labels = getattr(module, 'normalize_result_labels', None)
        self.result_fields = set(module.SCHEMA['properties'])

    def provenance(self, receipt, pair, where):
        expected = {'run_id': self.config['run_id'], 'arxiv_id': pair['arxiv_id'],
                    'pair': [pair['before'], pair['after']], 'execution_signature': self.sig,
                    'requested_model': self.config['model'],
                    'requested_reasoning_effort': self.config['reasoning_effort'],
                    'input_sha256': {key: self.hashes[key] for key in pair['pdf_ids']}}
        for field, value in expected.items():
            self.equal(receipt.get(field), value, 'receipt_' + field, where)

    def summary_agrees(self, actual, expected, where):
        for field in SUMMARY_FIELDS:
            self.equal(json.dumps(actual.get(field), sort_keys=True), json.dumps(expected[field], sort_keys=True),
                       'token_summary', f'{where}: {field}')

    def audit_attempt(self, root, pair):
        r = self.reader
        errors_before = len(self.errors)
        number = int(root.name.split('-')[1])
        receipt_path = root / 'receipt.json'
        if not receipt_path.exists():
            self.pending_artifacts.append(str(receipt_path))
            usage = parse_events(r.blob(root / 'events.jsonl'))[0] if (root / 'events.jsonl').exists() else {
                'source': 'codex_exec_turn_completed', 'records': [], 'errors': []}
            return {'attempt': number, 'status': 'awaiting_final_receipt', 'token_usage': usage}
        receipt = r.json(receipt_path)
        marker_path = root / 'recovery.json'
        marker = r.json(marker_path) if marker_path.exists() else None
        self.provenance(receipt, pair, receipt_path)
        self.equal(receipt.get('attempt'), number, 'attempt_identity', root)
        self.check(receipt.get('status') in {'completed', 'failed', 'interrupted'}, 'attempt_status', root)
        artifacts = receipt.get('artifact_sha256', {})
        files = {p.name for p in root.iterdir() if (p.is_file() or p.is_symlink()) and p.name != 'receipt.json'}
        if marker is not None:
            files -= {'recovery.json'}
            if 'result.json' not in artifacts:
                files -= {'result.json'}
        self.equal(set(artifacts), files, 'archived_file_scope', root)
        for name, expected in artifacts.items():
            if not self.check(Path(name).name == name and name != 'receipt.json', 'artifact_path', root):
                continue
            self.equal(r.sha(root / name), expected, 'artifact_hash', root / name)
        events = root / 'events.jsonl'
        data = r.blob(events) if events.exists() else b''
        usage, completed, failed, invalid = parse_events(data)
        if events.exists() or 'token_usage' in receipt:
            self.equal(receipt.get('token_usage'), usage, 'archived_token_records', root)
        for issue in invalid:
            self.check(False, 'invalid_token_event', f'{root}: {issue}')
        for field, value in [('turn_completed_count', completed), ('turn_failed_count', failed)]:
            if field in receipt or receipt['status'] == 'completed':
                self.equal(receipt.get(field), value, 'turn_counts', root)
        if receipt['status'] == 'completed' or marker is not None:
            if marker is None:
                self.check(type(receipt.get('exit_code')) is int and receipt['exit_code'] == 0
                           and completed > 0 and failed == 0 and not receipt.get('timed_out'), 'successful_exit', root)
            else:
                self.check(recovery.eligible_failure(receipt), 'recovery_original_failure', root)
                self.check({'events.jsonl', 'response.raw.json', 'prompt.txt', 'command.json'} <= artifacts.keys(),
                           'recovery_required_artifacts', root)
                proof_usage = recovery.completed_turn(r.blob(root / 'response.raw.json'), data)
                self.equal(proof_usage, usage, 'recovery_usage', root)
                self.equal(receipt.get('turn_completed_count'), 1, 'recovery_turn_counts', root)
                self.equal(receipt.get('turn_failed_count'), 0, 'recovery_turn_counts', root)
            result_path = root / 'result.json'
            result = r.json(result_path)
            self.validate_result(result)
            if marker is None:
                self.equal(r.sha(result_path), receipt.get('result_sha256'), 'archived_result_hash', root)
            else:
                self.equal(marker, {'kind': recovery.KIND, 'original_receipt_sha256': r.sha(receipt_path),
                    'result_sha256': r.sha(result_path), 'warning': recovery.WARNING}, 'recovery_evidence', root)
            raw_result = r.json(root / 'response.raw.json')
            if 'result_label_normalization' in receipt:
                supported = (receipt['result_label_normalization'] == 'runner.normalize_result_labels'
                             and callable(self.normalize_result_labels))
                if self.check(supported, 'result_label_normalization', root):
                    raw_result = self.normalize_result_labels(raw_result)
            self.equal(raw_result, result, 'raw_result_agreement', root)
            inputs = self.mapping(receipt['inputs'], 'requested_id', 'archived input')
            self.equal(set(inputs), set(pair['pdf_ids']), 'archived_input_scope', root)
            for requested in pair['pdf_ids']:
                item = inputs[requested]
                row = self.inventory[requested]
                pdf = row['pdf_observation']['pdf']
                for field, value in [('sha256', pdf['sha256']), ('bytes', pdf['bytes']),
                                     ('arxiv_id', row['arxiv_id']), ('version', row['version'])]:
                    self.equal(item.get(field), value, 'archived_input_' + field, root)
            argv = r.json(root / 'command.json')['argv']
            model_values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '--model']
            effort_values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == '-c'
                             and argv[i + 1].startswith('model_reasoning_effort=')]
            self.equal(model_values, [self.config['model']], 'command_model', root)
            self.equal(effort_values, [f'model_reasoning_effort="{self.config["reasoning_effort"]}"'],
                       'command_effort', root)
            folder = self.plan_path.parent / pair['arxiv_id'] / f'v{pair["before"]}_v{pair["after"]}'
            before, after = folder / f'v{pair["before"]}.pdf', folder / f'v{pair["after"]}.pdf'
            snapshot = self.run / 'source_snapshot/check_diff/prompts'
            task = r.blob(snapshot / 'compare.md').decode().format(
                arxiv_id=pair['arxiv_id'], before_filename=before.name, before_path=before,
                after_filename=after.name, after_path=after)
            prompt = f'{task}\n{r.blob(snapshot / "rubric.md").decode()}\n'
            self.equal(r.sha(root / 'prompt.txt'), digest(prompt.encode()), 'signed_prompt', root)
            self.equal(receipt.get('prompt_sha256'), r.sha(root / 'prompt.txt'), 'prompt_hash', root)
            if marker is not None:
                work = root / 'work'
                expected_argv = [self.config['codex_path'], 'exec', '--json', '--ignore-user-config',
                    '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                    '--model', self.config['model'], '-c', f'model_reasoning_effort="{self.config["reasoning_effort"]}"',
                    '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"', '--cd', str(work),
                    '--color', 'never', '--output-schema', str(work / 'schema.json'),
                    '--output-last-message', str(work / 'response.json'), '-']
                insertion = expected_argv.index('project_doc_max_bytes=0') - 1
                current_argv = (expected_argv[:insertion] + ['-c', 'web_search="disabled"',
                    '-c', 'sandbox_workspace_write.network_access=false'] + expected_argv[insertion:])
                self.check(r.json(root / 'command.json') in (
                    {'argv': expected_argv, 'working_directory': str(work)},
                    {'argv': current_argv, 'working_directory': str(work)}), 'recovery_command', root)
                for requested, pdf_path in zip(pair['pdf_ids'], (before, after)):
                    item = inputs[requested]
                    source = Path(self.config['pdf_root']) / pair['arxiv_id'] / f'{requested}.pdf'
                    self.equal(item.get('path'), str(source.resolve()), 'recovery_input_path', root)
                    self.equal(item.get('pair_path'), str(pdf_path), 'recovery_pair_path', root)
                    pdf = self.inventory[requested]['pdf_observation']['pdf']
                    for path in (source, pdf_path):
                        if r.source_pdf(path, pdf['bytes'], pdf['sha256']):
                            self.report['pdf_contents_read'] = True
                if len(self.errors) == errors_before:
                    self.recovered_attempts[(pair['key'], number)] = {
                        **receipt, 'status': 'completed', 'result_sha256': r.sha(result_path), 'recovery': marker}
        return receipt

    def audit_pair(self, pair):
        key = pair['key']
        state = self.states[key]
        for field, value in pair.items():
            self.equal(state.get(field), value, 'pair_identity', f'{key}: {field}')
        target = self.run / 'output' / pair['arxiv_id'] / f'diff_v{pair["before"]}_v{pair["after"]}.json'
        execution = target.with_suffix('.execution.json')
        attempt_parent = self.run / 'attempts' / key
        roots = sorted(attempt_parent.iterdir()) if attempt_parent.exists() else []
        for root in roots:
            if root.is_symlink() or not root.is_dir() or not re.fullmatch(r'attempt-\d{3,}', root.name):
                raise ValueError(f'Invalid attempt directory: {root}')
        unavailable = [p for p in pair['pdf_ids'] if p in self.unavailable]
        outcome = {'key': key, 'status': 'in_progress', 'observed_status': state['status']}
        if unavailable:
            self.equal(state['status'], 'input_unavailable', 'unavailable_status', key)
            self.equal(state.get('attempt_count'), 0, 'unavailable_attempts', key)
            self.check(not target.exists() and not execution.exists() and not roots,
                       'fabricated_unavailable_judgment', key)
            forbidden = set(state) & (self.result_fields | {'result', 'judgment', 'A1', 'A2',
                                                           'S2', 'T3', 'output_sha256'})
            self.check(not forbidden, 'fabricated_unavailable_judgment', key)
            outcome.update(status='input_unavailable', unavailable_versions=unavailable,
                           token_usage=summarize([]))
            return outcome, []
        self.check(state['status'] != 'input_unavailable', 'unavailable_evidence', key)
        attempts = []
        for root in roots:
            number = int(root.name.split('-')[1])
            # Even an unreadable receipt represents an attempt, never zero usage.
            self.attempt_records[(key, number)] = {'attempt': number, 'status': 'artifact_error'}
        for root in roots:
            number = int(root.name.split('-')[1])
            attempt = self.audit_attempt(root, pair)
            self.attempt_records[(key, number)] = attempt
            attempts.append(attempt)
        self.equal([a['attempt'] for a in attempts], list(range(1, len(attempts) + 1)), 'attempt_sequence', key)
        usage = summarize(attempts)
        outcome['token_usage'] = usage
        if not target.exists():
            if state['status'] == 'completed':
                self.check(False, 'missing_result', key)
            if execution.exists():
                saved = self.reader.json(execution)
                self.provenance(saved, pair, execution)
                self.check(saved.get('status') != 'completed', 'missing_result', key)
            return outcome, attempts
        result = self.reader.json(target)
        self.validate_result(result)
        if not execution.exists():
            if state['status'] == 'completed':
                self.check(False, 'missing_execution_receipt', key)
            else:
                self.pending_artifacts.append(str(execution))
            return outcome, attempts
        saved = self.reader.json(execution)
        self.provenance(saved, pair, execution)
        self.equal(saved.get('status'), 'completed', 'canonical_status', key)
        self.equal(saved.get('output_sha256'), self.reader.sha(target), 'output_hash', key)
        successful = next((self.recovered_attempts.get((key, a['attempt']), a)
                           for a in attempts if a['attempt'] == saved.get('attempt')
                           and (a['status'] == 'completed' or (key, a['attempt']) in self.recovered_attempts)), None)
        self.equal(saved.get('recovery'), (successful or {}).get('recovery'), 'canonical_recovery', key)
        self.check(successful is not None, 'missing_successful_archive', key)
        if successful:
            archive = self.run / 'attempts' / key / f'attempt-{successful["attempt"]:03d}' / 'result.json'
            self.equal(result, self.reader.json(archive), 'archived_result_agreement', key)
            self.equal(saved.get('result_sha256'), successful.get('result_sha256'), 'result_hash_agreement', key)
            derived = {'token_usage', 'elapsed_seconds', 'recovered_from_consolidated_receipt',
                       'missing_archived_artifacts'}
            for field, value in successful.items():
                if field not in derived:
                    self.equal(saved.get(field), value, 'successful_receipt_agreement', f'{key}: {field}')
        embedded = self.mapping(saved.get('attempts', []), 'attempt', 'embedded attempt')
        self.equal(set(embedded), {a['attempt'] for a in attempts}, 'embedded_attempt_scope', key)
        # Recovery may annotate the physical receipt without rewriting the original embedded copy.
        annotations = {'recovered_from_consolidated_receipt', 'missing_archived_artifacts'}
        for attempt in attempts:
            left = {k: v for k, v in embedded.get(attempt['attempt'], {}).items() if k not in annotations}
            right = {k: v for k, v in attempt.items() if k not in annotations}
            self.equal(left, right, 'embedded_attempt_agreement', key)
        self.summary_agrees(saved.get('token_usage', {}), usage, key)
        if state['status'] == 'completed':
            self.equal(state.get('attempt_count'), len(attempts), 'pair_attempt_count', key)
            self.equal(state.get('output_sha256'), self.reader.sha(target), 'pair_output_hash', key)
            outcome['status'] = 'completed'
        return outcome, attempts

    def coverage(self):
        outcomes = {p['key']: p['status'] for p in self.report['pairs']}
        sources = {source: Counter() for source in self.sources}
        unit_statuses = Counter()
        for aid, memberships in self.memberships.items():
            keys = [key for key, pair in self.pairs.items() if pair['arxiv_id'] == aid]
            states = [outcomes.get(key, 'in_progress') for key in keys]
            settled = bool(states) and all(s in SETTLED for s in states)
            completed = bool(states) and all(s == 'completed' for s in states)
            unit_statuses['settled_units'] += settled
            unit_statuses['completed_units'] += completed
            unit_statuses['units_with_unavailable_inputs'] += 'input_unavailable' in states
            for source in memberships:
                sources[source].update(units=1, pairs=len(keys), completed_units=int(completed),
                                       settled_units=int(settled), completed_pairs=states.count('completed'),
                                       unavailable_pairs=states.count('input_unavailable'))
        counts = Counter(outcomes.values())
        return {'units': len(self.memberships), 'pairs': len(self.pairs),
                'runnable_pairs': sum(not any(i in self.unavailable for i in p['pdf_ids'])
                                      for p in self.pairs.values()),
                'officially_unavailable_pairs': sum(any(i in self.unavailable for i in p['pdf_ids'])
                                                   for p in self.pairs.values()),
                'completed_pairs': counts['completed'], 'settled_pairs': sum(counts[s] for s in SETTLED),
                'pair_status_counts': dict(counts), **dict(unit_statuses),
                'sources': {key: dict(value) for key, value in sources.items()}}

    def verify(self):
        try:
            self.load_scope()
            if not self.errors:
                output_before = tree_files(self.run / 'output')
                allowed = set()
                for pair in self.pairs.values():
                    path = self.run / 'output' / pair['arxiv_id'] / f'diff_v{pair["before"]}_v{pair["after"]}.json'
                    allowed.update((path, path.with_suffix('.execution.json')))
                for path in output_before - allowed:
                    if path.name.endswith('.tmp') and path.with_name(path.name[:-4]) in allowed:
                        self.pending_artifacts.append(str(path))
                    else:
                        self.check(False, 'unexpected_output', path)
                attempts_root = self.run / 'attempts'
                attempt_before = set(attempts_root.glob('*/attempt-*'))
                if attempts_root.exists():
                    for path in attempts_root.iterdir():
                        self.check(path.is_dir() and not path.is_symlink() and path.name in self.pairs,
                                   'unexpected_attempt_scope', path)
                for pair in self.pairs.values():
                    start_errors = len(self.errors)
                    try:
                        outcome, _ = self.audit_pair(pair)
                    except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
                        self.check(False, 'pair_artifact', f'{pair["key"]}: {exc}')
                        outcome = {'key': pair['key'], 'status': 'in_progress'}
                    if len(self.errors) != start_errors:
                        outcome['status'] = 'artifact_error'
                    self.report['pairs'].append(outcome)
                self.report['coverage'] = self.coverage()
                all_attempts = list(self.attempt_records.values())
                total = summarize(all_attempts)
                self.report['token_usage'] = total
                self.report['token_uncertainty'] = {
                    'total_consumption_known': total['status'] in {'complete', 'not_run'}
                                               and not self.errors and not self.pending_artifacts,
                    'unverified_attempt_count': sum(a['status'] == 'artifact_error' for a in all_attempts),
                    'attempts_without_reported_usage': total['attempts_without_reported_usage'],
                    'reported_turns_missing_fields': {k: total['reported_turn_count'] - total['field_report_counts'][k]
                                                     for k in FIELDS},
                    'unrecognized_usage_fields': sorted({k for a in all_attempts
                        for row in a.get('token_usage', {}).get('records', []) for k in row['usage'] if k not in FIELDS}),
                    'note': 'Input includes cached input; output includes reasoning output. Do not add these subsets '
                            'again. Cache-write counters are reported separately. Missing counters and missing usage '
                            'remain unknown; reported sums are not a claim of complete consumption.'}
                settled = all(p['status'] in SETTLED for p in self.report['pairs'])
                total_path = self.run / 'token_usage.json'
                if total_path.exists():
                    batch = self.reader.json(total_path)
                    if settled and not self.pending_artifacts:
                        self.equal(batch.get('run_id'), self.config['run_id'], 'batch_identity', total_path)
                        self.summary_agrees(batch.get('token_usage', {}), total, total_path)
                        self.equal(batch.get('active_attempt_count'), 0, 'active_attempts', total_path)
                elif settled:
                    self.check(False, 'missing_batch_tokens', total_path)
                manifest = self.run / 'run_manifest.json'
                if settled and manifest.exists():
                    final = self.reader.json(manifest)
                    self.summary_agrees(final.get('token_usage', {}), total, manifest)
                if tree_files(self.run / 'output') != output_before or set(attempts_root.glob('*/attempt-*')) != attempt_before:
                    self.pending_artifacts.append('Artifact directories changed during verification')
                self.report['pending_artifacts'] = self.pending_artifacts
                self.report['status'] = 'passed' if settled and not self.pending_artifacts else 'in_progress'
        except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
            self.check(False, 'frozen_input_artifact', exc)
        self.report['changed_during_verification'] = self.reader.changes()
        if self.report['changed_during_verification']:
            self.report['status'] = 'in_progress'
        elif self.errors:
            self.report['status'] = 'failed'
        self.report['error_count'] = len(self.errors)
        self.report['completed_turn_recoveries'] = [
            {'pair': key, 'attempt': number, 'exit_code': receipt['exit_code'],
             'timed_out': receipt.get('timed_out', False), 'warning': recovery.WARNING}
            for (key, number), receipt in sorted(self.recovered_attempts.items())]
        if (self.errors or self.report['changed_during_verification']) and 'token_uncertainty' in self.report:
            self.report['token_uncertainty']['total_consumption_known'] = False
        self.report['artifact_sha256'] = {str(path): value for path, value in self.reader.hashes.items()
                                           if path.name in {'run_config.json', 'execution_contract.json',
                                               'scope_input.json', 'version_inventory.jsonl', 'selection_manifest.json',
                                               'pair_status.jsonl', 'schema.py', 'compare.md', 'rubric.md', 'token_usage.json'}}
        return self.report


def render_report(report):
    coverage, usage = report['coverage'], report['token_usage']
    lines = ['# Final batch verification', '', f"Run: {report['run']}",
             f"Verified at: {report['checked_at_utc']}",
             f"Requested model: {report['requested_model']}; reasoning effort: {report['requested_reasoning_effort']}.", '',
             f"Artifact verification passed for {coverage['units']:,} units and {coverage['pairs']:,} planned pairs: "
             f"{coverage['completed_pairs']:,} completed judgments and {coverage['officially_unavailable_pairs']:,} "
             'pairs with official exact-version no-PDF evidence. No judgment was produced for those unavailable pairs.', '',
             '| Source | Units | Fully judged units | Settled units | Planned pairs | Judged pairs | Unavailable pairs |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for source, row in coverage['sources'].items():
        lines.append('| ' + ' | '.join([source] + [str(row[k]) for k in (
            'units', 'completed_units', 'settled_units', 'pairs', 'completed_pairs', 'unavailable_pairs')]) + ' |')
    lines += ['', 'Source rows retain memberships; they must not be summed to infer unique coverage.', '',
              'Every completed output matched the saved schema, its SHA-256 receipt, the requested model and effort, '
              'the frozen execution signature, its two ledger input hashes, and its verified archived result. '
              'Archived commands and rendered prompts agreed with those settings.', '',
              f"Token accounting status: {usage['status']}. {usage['attempt_count']:,} attempts were counted once, "
              f"including failed and interrupted attempts; {usage['attempts_without_reported_usage']:,} had no reported usage.", '',
              '| Reported counter | Sum | Reporting turns |', '| --- | ---: | ---: |']
    for field, value in usage['reported_totals'].items():
        count = usage['field_report_counts'].get(field)
        lines.append(f"| {field} | {'unknown' if value is None else value} | {'derived' if count is None else count} |")
    lines += ['', report['token_uncertainty']['note'],
              'Reported turns missing counters: ' + ', '.join(
                  f'{k}={v}' for k, v in report['token_uncertainty']['reported_turns_missing_fields'].items()) + '.',
              'Unknown usage fields retained without adding them to totals: ' +
              (', '.join(report['token_uncertainty']['unrecognized_usage_fields']) or 'none') + '.', '',
              'This is an artifact audit, not scientific S2/T3 aggregation or human validation. ' +
              ('Accessible recovery PDF bytes were hashed again without parsing their contents. '
               if report.get('pdf_contents_read') else 'PDF contents were not reread. ') +
              'Input hashes were compared with the frozen per-version ledger; the runner owns live '
              'pre/post PDF checks. Requested settings were verified from receipts and commands, not from an '
              'independently observed provider model identity.', '']
    for item in report.get('completed_turn_recoveries', []):
        lines.append(f"Warning: {item['pair']} attempt {item['attempt']}: {item['warning']} "
                     f"exit_code={item['exit_code']}, timed_out={item['timed_out']}.")
    return '\n'.join(lines)


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args(argv)
    verifier = Verifier(args.run)
    report = verifier.verify()
    try:
        if report['status'] == 'passed':
            atomic_write(verifier.run / 'REPORT.md', render_report(report))
            target = verifier.run / 'final_verification.json'
        else:
            target = verifier.run / 'verification_progress.json'
        atomic_write(target, json.dumps(report, indent=2) + '\n')
    except OSError as exc:
        print(f'Could not write verification report: {type(exc).__name__}')
        return 1
    print(f"Verification: {report['status']}; artifact errors: {report['error_count']}.")
    return 1 if report['errors'] else 2 if args.require_complete and report['status'] != 'passed' else 0


if __name__ == '__main__':
    raise SystemExit(main())

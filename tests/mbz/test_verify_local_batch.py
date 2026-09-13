"""Offline artifact fixtures; no PDFs, subprocesses, network or model calls."""

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from utility import verify_local_batch as verify
from utility import prepare_source_data as preparation


REPO = Path(os.environ.get('SOURCE_CODE_ROOT', Path(__file__).resolve().parents[2])).resolve()
SCHEMA_SOURCE = REPO / 'check_diff/schema.py'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def rows(path, values):
    path.write_text(''.join(json.dumps(value) + '\n' for value in values))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_for(schema):
    if schema['type'] == 'object':
        return {key: result_for(value) for key, value in schema['properties'].items()}
    return schema.get('enum', ['Offline fixture evidence.'])[0]


class VerifierTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.run = self.base / 'run'
        self.run.mkdir()
        self.prepared = self.base / 'prepared'
        self.prepared.mkdir()
        self.aid = '2601.00001'
        self.snapshot = self.run / 'source_snapshot/check_diff'
        (self.snapshot / 'prompts').mkdir(parents=True)
        self.schema_source = SCHEMA_SOURCE.read_text()
        (self.snapshot / 'schema.py').write_text(self.schema_source)
        (self.snapshot / 'prompts/compare.md').write_text(
            'Compare {arxiv_id}: {before_filename} ({before_path}) to {after_filename} ({after_path}).')
        (self.snapshot / 'prompts/rubric.md').write_text('Keep all judgments independent.')
        namespace = {}
        exec(compile(self.schema_source, '<fixture-schema>', 'exec'), namespace)
        self.result = result_for(namespace['SCHEMA'])
        dates = ['2026-01-01T00:00:00Z', '2026-03-01T00:00:00Z', '2026-05-01T00:00:00Z']
        self.versions = [{'version': number, 'submitted_at': date} for number, date in enumerate(dates, 1)]
        self.inventory = []
        for version in self.versions:
            number = version['version']
            requested = f'{self.aid}v{number}'
            self.inventory.append({
                'arxiv_id': self.aid, 'version': number, 'requested_id': requested,
                'selected_for_comparison': True, 'frozen_version_metadata': version,
                'pdf_observation': {'arxiv_id': self.aid, 'version': number, 'requested_id': requested,
                    'pdf': {'availability_status': 'verified', 'bytes': 123,
                            'sha256': hashlib.sha256(requested.encode()).hexdigest()},
                    'page_count': {'value': 1}, 'official_no_pdf_evidence': None}})
        self.config = {'run_id': 'fixture', 'prepared_path': str(self.prepared),
                       'codex_path': '/offline/fake-codex',
                       'pdf_root': str(self.base / 'unread-pdfs'), 'model': 'fixture-model',
                       'reasoning_effort': 'max', 'unit_count': 1, 'source_names': ['source_a']}
        self.scope = {'dataset': 'source_data', 'record_count': 1, 'authorized_sources': ['source_a'],
                      'record_memberships': {self.aid: ['source_a']},
                      'records': [{'arxiv_id': self.aid, 'frozen_N': 3, 'versions': self.versions}]}
        self.pairs = [{'key': f'{self.aid}__v{b}_v{a}', 'arxiv_id': self.aid,
                       'before': b, 'after': a, 'pdf_ids': [f'{self.aid}v{b}', f'{self.aid}v{a}'],
                       'gap_seconds': gap}
                      for b, a, gap in [(1, 2, 5097600), (2, 3, 5270400)]]
        self.states = [{**pair, 'status': 'pending', 'attempt_count': 0} for pair in self.pairs]
        self.plan = {'schema_version': preparation.PLAN_SCHEMA_VERSION, 'source_sha256': '', 'selected_units': [{'arxiv_id': self.aid,
            'selected_version_ids': [1, 2, 3], 'selected_transitions': [
                {'from_version': p['before'], 'to_version': p['after'], 'gap_seconds': p['gap_seconds']}
                for p in self.pairs]}], 'requested_pdfs': [
                    {'requested_id': r['requested_id'], 'arxiv_id': self.aid, 'version': r['version']}
                    for r in self.inventory], 'summary': {'paper_count': 1, 'pair_count': 2}}
        self.usage = {'input_tokens': 100, 'cached_input_tokens': 80, 'cache_write_input_tokens': 5,
                      'output_tokens': 20, 'reasoning_output_tokens': 10}
        self.archived = {}
        self.freeze()
        self.save_states()

    def freeze(self):
        write(self.run / 'run_config.json', self.config)
        write(self.run / 'scope_input.json', self.scope)
        rows(self.run / 'version_inventory.jsonl', self.inventory)
        self.plan['source_sha256'] = sha(self.run / 'scope_input.json')
        write(self.prepared / 'selection_manifest.json', self.plan)
        self.sig = verify.signature(self.config, verify.Reader(), self.run)
        self.contract = {'execution_signature': self.sig, 'requested_model': self.config['model'],
                         'requested_reasoning_effort': self.config['reasoning_effort'], 'unit_count': 1,
                         'comparison_count': 2, 'source_membership_counts': {'source_a': 1}}
        write(self.run / 'execution_contract.json', self.contract)

    def save_states(self):
        rows(self.run / 'pair_status.jsonl', self.states)
        attempts = [a for pair in self.pairs for a in self.archived.get(pair['key'], [])]
        write(self.run / 'token_usage.json', {'run_id': 'fixture', 'token_usage': verify.summarize(attempts),
                                             'active_attempt_count': 0})

    def path(self, index=0):
        pair = self.pairs[index]
        return self.run / 'output' / self.aid / f'diff_v{pair["before"]}_v{pair["after"]}.json'

    def attempt_path(self, index=0, number=1):
        return self.run / 'attempts' / self.pairs[index]['key'] / f'attempt-{number:03d}'

    def create_attempt(self, index, number, usage, completed=True):
        pair = self.pairs[index]
        root = self.attempt_path(index, number)
        root.mkdir(parents=True)
        receipt = {'run_id': 'fixture', 'arxiv_id': self.aid, 'pair': [pair['before'], pair['after']],
            'attempt': number, 'status': 'completed' if completed else 'failed',
            'requested_model': self.config['model'], 'requested_reasoning_effort': self.config['reasoning_effort'],
            'execution_signature': self.sig, 'input_sha256': {
                row['requested_id']: row['pdf_observation']['pdf']['sha256'] for row in self.inventory
                if row['requested_id'] in pair['pdf_ids']}, 'exit_code': 0 if completed else 1,
            'elapsed_seconds': 1.0, 'turn_completed_count': int(completed), 'turn_failed_count': int(not completed)}
        event = {'type': 'turn.completed', 'usage': usage} if completed else {'type': 'turn.failed'}
        (root / 'events.jsonl').write_text(json.dumps(event) + '\n')
        receipt['token_usage'] = verify.parse_events((root / 'events.jsonl').read_bytes())[0]
        write(root / 'command.json', {'argv': ['codex', 'exec', '--model', self.config['model'],
                                              '-c', f'model_reasoning_effort="{self.config["reasoning_effort"]}"']})
        folder = self.prepared / self.aid / f'v{pair["before"]}_v{pair["after"]}'
        before, after = folder / f'v{pair["before"]}.pdf', folder / f'v{pair["after"]}.pdf'
        prompt = (self.snapshot / 'prompts/compare.md').read_text().format(
            arxiv_id=self.aid, before_filename=before.name, before_path=before,
            after_filename=after.name, after_path=after)
        prompt += '\n' + (self.snapshot / 'prompts/rubric.md').read_text() + '\n'
        (root / 'prompt.txt').write_text(prompt)
        receipt['prompt_sha256'] = sha(root / 'prompt.txt')
        if completed:
            receipt['inputs'] = [{'requested_id': row['requested_id'], 'arxiv_id': self.aid,
                                  'version': row['version'], 'sha256': row['pdf_observation']['pdf']['sha256'],
                                  'bytes': row['pdf_observation']['pdf']['bytes']}
                                 for row in self.inventory if row['requested_id'] in pair['pdf_ids']]
            write(root / 'result.json', self.result)
            write(root / 'response.raw.json', self.result)
            receipt['result_sha256'] = sha(root / 'result.json')
        receipt['artifact_sha256'] = {p.name: sha(p) for p in root.iterdir() if p.is_file()}
        write(root / 'receipt.json', receipt)
        return receipt

    def complete(self, index=0, usage='default', retry=False):
        usage = self.usage if usage == 'default' else usage
        attempts = []
        if retry:
            attempts.append(self.create_attempt(index, 1, None, completed=False))
        receipt = self.create_attempt(index, len(attempts) + 1, usage)
        attempts.append(receipt)
        self.archived[self.pairs[index]['key']] = attempts
        write(self.path(index), self.result)
        saved = {**receipt, 'attempts': attempts, 'token_usage': verify.summarize(attempts),
                 'output_sha256': sha(self.path(index)), 'output_path': str(self.path(index))}
        write(self.path(index).with_suffix('.execution.json'), saved)
        self.states[index].update(status='completed', attempt_count=len(attempts), output_sha256=sha(self.path(index)))
        self.save_states()

    def complete_all(self, **kwargs):
        for index in range(2):
            self.complete(index, **kwargs)

    def audit(self):
        return verify.Verifier(self.run).verify()

    def codes(self, report):
        return {error['code'] for error in report['errors']}

    def invoke(self, *extra):
        with redirect_stdout(io.StringIO()):
            return verify.main(['--run', str(self.run), *extra])

    def unavailable(self):
        row = self.inventory[1]
        obs = row['pdf_observation']
        obs['pdf'].update(availability_status='official_no_pdf', sha256=None, bytes=None)
        obs['official_no_pdf_evidence'] = {
            'requested_id': row['requested_id'], 'arxiv_id': self.aid, 'version': 2,
            'official_url': f'https://arxiv.org/abs/{self.aid}v2', 'observed_at_utc': '2026-09-11T00:00:00Z',
            'reason': 'The exact original was withdrawn.', 'no_pdf_available': True,
            'availability_status': 'official_no_pdf'}
        for state in self.states:
            state.update(status='input_unavailable')
        self.freeze()
        self.save_states()

    def test_final_report_exact_coverage_and_no_double_counting(self):
        self.complete_all()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['coverage']['units'], 1)
        self.assertEqual(report['coverage']['pairs'], 2)
        self.assertEqual(report['coverage']['sources']['source_a']['completed_pairs'], 2)
        totals = report['token_usage']['reported_totals']
        self.assertEqual(totals['total_tokens'], 240)
        self.assertEqual(totals['input_tokens'], 200)
        self.assertEqual(totals['non_cached_input_tokens'], 40)
        self.assertEqual(totals['cache_write_input_tokens'], 10)
        self.assertEqual(report['token_usage']['attempt_count'], 2)
        self.assertEqual(self.invoke('--require-complete'), 0)
        self.assertTrue((self.run / 'final_verification.json').exists())
        text = (self.run / 'REPORT.md').read_text()
        self.assertIn('No judgment was produced', text)
        self.assertIn('human validation', text)
        self.assertIn('PDF contents were not reread', text)

    def test_missing_running_outputs_are_in_progress_and_no_final_report(self):
        self.states[0]['status'] = 'running'
        self.save_states()
        self.assertEqual(self.audit()['status'], 'in_progress')
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(self.invoke('--require-complete'), 2)
        self.assertTrue((self.run / 'verification_progress.json').exists())
        self.assertFalse((self.run / 'REPORT.md').exists())
        self.assertFalse((self.run / 'final_verification.json').exists())

    def test_missing_completed_output_is_an_error_and_prior_records_are_preserved(self):
        self.complete_all()
        self.assertEqual(self.invoke('--require-complete'), 0)
        old_report = (self.run / 'REPORT.md').read_bytes()
        old_final = (self.run / 'final_verification.json').read_bytes()
        self.path().unlink()
        self.assertEqual(self.invoke('--require-complete'), 1)
        self.assertEqual((self.run / 'REPORT.md').read_bytes(), old_report)
        self.assertEqual((self.run / 'final_verification.json').read_bytes(), old_final)
        self.assertIn('missing_result', self.codes(self.audit()))

    def test_unexpected_output_and_unexpected_attempt_scope_fail(self):
        self.complete_all()
        extra = self.path().parent / 'diff_v1_v3.json'
        write(extra, self.result)
        self.assertIn('unexpected_output', self.codes(self.audit()))
        extra.unlink()
        (self.run / 'attempts/9999.99999__v1_v2').mkdir()
        self.assertIn('unexpected_attempt_scope', self.codes(self.audit()))

    def test_bad_schema_and_corrupt_bytes_fail(self):
        self.complete_all()
        bad = copy.deepcopy(self.result)
        bad['M1']['change'] = 'Invented category'
        write(self.path(), bad)
        self.assertEqual(self.audit()['status'], 'failed')
        write(self.path(), self.result)
        self.path().write_text(self.path().read_text() + ' ')
        self.assertIn('output_hash', self.codes(self.audit()))

    def test_archived_result_corruption_and_hash_rewrite_cannot_hide_disagreement(self):
        self.complete_all()
        root = self.attempt_path()
        result = copy.deepcopy(self.result)
        result['M1']['justification'] = 'Different archived assessment.'
        write(root / 'result.json', result)
        report = self.audit()
        self.assertIn('artifact_hash', self.codes(report))
        self.assertIn('archived_result_agreement', self.codes(report))

    def rewrite_raw(self, raw, normalization='runner.normalize_result_labels'):
        root = self.attempt_path()
        write(root / 'response.raw.json', raw)
        receipt = json.loads((root / 'receipt.json').read_text())
        if normalization is not None:
            receipt['result_label_normalization'] = normalization
        receipt['artifact_sha256']['response.raw.json'] = sha(root / 'response.raw.json')
        write(root / 'receipt.json', receipt)
        self.archived[self.pairs[0]['key']][0] = receipt
        saved = json.loads(self.path().with_suffix('.execution.json').read_text())
        saved.update({**receipt, 'attempts': [receipt], 'token_usage': verify.summarize([receipt])})
        write(self.path().with_suffix('.execution.json'), saved)

    def test_explicit_new_normalization_accepts_only_frozen_label_mapping(self):
        self.complete_all()
        raw = copy.deepcopy(self.result)
        raw['M1']['change'] = raw['M1']['change'].upper()
        self.rewrite_raw(raw)
        before = {p: p.read_bytes() for p in self.attempt_path().iterdir()}
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))

    def test_normalization_does_not_allow_justification_or_other_judgment_changes(self):
        self.complete_all()
        for field in ('justification', 'change', 'prelim'):
            raw = copy.deepcopy(self.result)
            raw['M1']['change'] = raw['M1']['change'].upper()
            if field == 'prelim':
                raw['prelim']['A2']['before']['value'] = 'no'
            elif field == 'change':
                raw['M1']['change'] = 'MINOR CHANGE'
            else:
                raw['M1']['justification'] = 'Different scientific evidence.'
            self.rewrite_raw(raw)
            self.assertIn('raw_result_agreement', self.codes(self.audit()), field)

    def test_unmarked_historical_receipts_still_require_exact_raw_equality(self):
        self.complete_all()
        raw = copy.deepcopy(self.result)
        raw['M1']['change'] = raw['M1']['change'].upper()
        self.rewrite_raw(raw, normalization=None)
        self.assertIn('raw_result_agreement', self.codes(self.audit()))

    def test_unknown_normalization_or_raw_hash_corruption_fails(self):
        self.complete_all()
        self.rewrite_raw(self.result, normalization='arbitrary_casefold')
        self.assertEqual(self.audit()['status'], 'failed')
        self.rewrite_raw(self.result)
        raw = copy.deepcopy(self.result)
        raw['M1']['change'] = raw['M1']['change'].upper()
        write(self.attempt_path() / 'response.raw.json', raw)
        self.assertIn('artifact_hash', self.codes(self.audit()))

    def test_historical_uppercase_schema_and_receipts_remain_valid(self):
        self.schema_source = self.schema_source.split('\ndef normalize_result_labels', 1)[0]
        for old, historical in [('major change', 'Major Change'), ('minor change', 'Minor Change'),
                                ('no change', 'No Change'), ('maybe', 'Maybe')]:
            self.schema_source = self.schema_source.replace(repr(old), repr(historical))
        (self.snapshot / 'schema.py').write_text(self.schema_source)
        namespace = {}
        exec(self.schema_source, namespace)
        self.result = result_for(namespace['SCHEMA'])
        self.freeze()
        self.complete_all()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(self.result['M1']['change'], 'Major Change')

    def test_normalization_requires_support_from_frozen_schema(self):
        (self.snapshot / 'schema.py').write_text(
            self.schema_source.split('\ndef normalize_result_labels', 1)[0])
        self.freeze()
        self.complete_all()
        self.rewrite_raw(self.result)
        self.assertIn('result_label_normalization', self.codes(self.audit()))

    def test_model_effort_input_hash_and_signature_mismatches_fail(self):
        self.complete_all()
        path = self.path().with_suffix('.execution.json')
        original = json.loads(path.read_text())
        for field, value in [('requested_model', 'wrong-model'), ('requested_reasoning_effort', 'low'),
                             ('execution_signature', '0' * 64), ('input_sha256', {})]:
            with self.subTest(field=field):
                write(path, {**original, field: value})
                self.assertIn('receipt_' + field, self.codes(self.audit()))
        write(path, original)
        original['inputs'][0]['sha256'] = '0' * 64
        write(path, original)
        self.assertIn('successful_receipt_agreement', self.codes(self.audit()))

    def test_contract_and_saved_schema_change_are_detected(self):
        self.complete_all()
        (self.snapshot / 'schema.py').write_text(self.schema_source + '\n# Changed after freeze\n')
        self.assertIn('execution_signature', self.codes(self.audit()))

    def test_pair_and_batch_token_summary_corruption_fail(self):
        self.complete_all()
        path = self.path().with_suffix('.execution.json')
        saved = json.loads(path.read_text())
        saved['token_usage']['reported_totals']['total_tokens'] += 80
        write(path, saved)
        self.assertIn('token_summary', self.codes(self.audit()))
        saved['token_usage']['reported_totals']['total_tokens'] -= 80
        write(path, saved)
        path = self.run / 'token_usage.json'
        batch = json.loads(path.read_text())
        batch['token_usage']['reported_totals']['total_tokens'] += 20
        write(path, batch)
        self.assertIn('token_summary', self.codes(self.audit()))

    def test_retry_usage_counted_once_and_unknown_attempt_is_not_zero(self):
        self.complete_all(retry=True)
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['token_usage']['attempt_count'], 4)
        self.assertEqual(report['token_usage']['attempts_without_reported_usage'], 2)
        self.assertEqual(report['token_usage']['reported_totals']['total_tokens'], 240)
        self.assertEqual(report['token_usage']['status'], 'partial')
        self.assertFalse(report['token_uncertainty']['total_consumption_known'])

    def test_entirely_unknown_and_partly_known_counters_remain_explicit(self):
        self.complete_all(usage={})
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertIsNone(report['token_usage']['reported_totals']['input_tokens'])
        self.assertIsNone(report['token_usage']['reported_totals']['total_tokens'])
        self.assertFalse(report['token_uncertainty']['total_consumption_known'])
        self.assertEqual(report['token_uncertainty']['reported_turns_missing_fields']['output_tokens'], 2)
        self.assertIn('| total_tokens | unknown |', verify.render_report(report))

    def test_missing_usage_and_unrecognized_counters_do_not_become_zero(self):
        self.complete(0, usage=None)
        self.complete(1, usage={'input_tokens': 100, 'new_provider_counter': 900})
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['token_usage']['reported_totals']['input_tokens'], 100)
        self.assertIsNone(report['token_usage']['reported_totals']['output_tokens'])
        self.assertIsNone(report['token_usage']['reported_totals']['total_tokens'])
        self.assertEqual(report['token_usage']['attempts_without_reported_usage'], 1)
        self.assertEqual(report['token_uncertainty']['unrecognized_usage_fields'], ['new_provider_counter'])

    def test_invalid_negative_boolean_and_subset_counters_fail(self):
        for bad in [{'input_tokens': -1}, {'output_tokens': True},
                    {'input_tokens': 5, 'cached_input_tokens': 6},
                    {'output_tokens': 5, 'reasoning_output_tokens': 6}]:
            parsed, _, _, invalid = verify.parse_events((json.dumps({'type': 'turn.completed', 'usage': bad}) + '\n').encode())
            self.assertTrue(invalid)
            self.assertEqual(parsed['records'], [])
        self.complete_all(usage={'input_tokens': -1})
        self.assertIn('invalid_token_event', self.codes(self.audit()))

    def test_official_unavailable_pairs_pass_without_fabricated_results(self):
        self.unavailable()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['coverage']['officially_unavailable_pairs'], 2)
        self.assertEqual(report['coverage']['runnable_pairs'], 0)
        self.assertEqual(report['coverage']['completed_units'], 0)
        self.assertEqual(report['coverage']['settled_units'], 1)
        self.assertEqual(report['token_usage']['status'], 'not_run')
        write(self.path(), self.result)
        self.assertIn('fabricated_unavailable_judgment', self.codes(self.audit()))

    def test_unavailable_claim_requires_exact_official_evidence(self):
        self.unavailable()
        evidence = self.inventory[1]['pdf_observation']['official_no_pdf_evidence']
        for field, value in [('official_url', 'https://example.com/abs/2601.00001v2'),
                             ('requested_id', '2601.00001v3'), ('no_pdf_available', False), ('reason', '')]:
            old = evidence[field]
            evidence[field] = value
            rows(self.run / 'version_inventory.jsonl', self.inventory)
            self.assertIn('unavailable_evidence', self.codes(self.audit()))
            evidence[field] = old
        self.inventory[1]['pdf_observation']['pdf']['availability_status'] = 'missing'
        rows(self.run / 'version_inventory.jsonl', self.inventory)
        self.assertIn('unavailable_evidence', self.codes(self.audit()))

    def test_missing_duplicate_or_substituted_status_tuple_fails(self):
        self.complete_all()
        for states in [self.states[:1], self.states + self.states[:1],
                       [dict(self.states[0], after=3), self.states[1]]]:
            rows(self.run / 'pair_status.jsonl', states)
            self.assertEqual(self.audit()['status'], 'failed')

    def test_plan_cannot_silently_remove_a_pair_or_change_sequence(self):
        self.complete_all()
        self.plan['selected_units'][0]['selected_transitions'].pop()
        self.plan['summary']['pair_count'] = 1
        write(self.prepared / 'selection_manifest.json', self.plan)
        self.assertIn('planned_tuples', self.codes(self.audit()))

    def test_missing_successful_archive_and_receipt_never_pass(self):
        self.complete_all()
        (self.attempt_path() / 'receipt.json').unlink()
        report = self.audit()
        self.assertNotEqual(report['status'], 'passed')
        self.assertIn('missing_successful_archive', self.codes(report))

    def test_unreadable_archived_receipts_retain_unknown_attempt_usage(self):
        self.complete_all(retry=True)
        (self.attempt_path() / 'receipt.json').write_text('{')
        report = self.audit()
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['token_usage']['attempt_count'], 4)
        self.assertEqual(report['token_usage']['attempts_without_reported_usage'], 3)
        self.assertEqual(report['token_uncertainty']['unverified_attempt_count'], 2)
        self.assertFalse(report['token_uncertainty']['total_consumption_known'])

    def test_unavailable_state_cannot_contain_a_change_judgment(self):
        self.unavailable()
        self.states[0]['M1'] = copy.deepcopy(self.result['M1'])
        self.save_states()
        self.assertIn('fabricated_unavailable_judgment', self.codes(self.audit()))

    def test_command_and_signed_prompt_are_checked_beyond_receipt_hashes(self):
        self.complete_all()
        root = self.attempt_path()
        command = json.loads((root / 'command.json').read_text())
        command['argv'][command['argv'].index('--model') + 1] = 'wrong-model'
        write(root / 'command.json', command)
        (root / 'prompt.txt').write_text('A substituted task.')
        receipt = json.loads((root / 'receipt.json').read_text())
        for filename in ('command.json', 'prompt.txt'):
            receipt['artifact_sha256'][filename] = sha(root / filename)
        receipt['prompt_sha256'] = sha(root / 'prompt.txt')
        write(root / 'receipt.json', receipt)
        codes = self.codes(self.audit())
        self.assertIn('command_model', codes)
        self.assertIn('signed_prompt', codes)

    def test_skipped_original_versions_remain_in_inventory_without_creating_extra_pairs(self):
        # Selected sequence 1 -> 3 -> 4; v2 stays in the full ledger.
        for row, number in zip(self.inventory, [1, 3, 4]):
            row['version'] = number
            row['requested_id'] = f'{self.aid}v{number}'
            row['frozen_version_metadata'] = dict(row['frozen_version_metadata'], version=number)
            row['pdf_observation'].update(version=number, requested_id=row['requested_id'])
        skipped = copy.deepcopy(self.inventory[0])
        skipped.update(version=2, requested_id=f'{self.aid}v2', selected_for_comparison=False)
        skipped['frozen_version_metadata'] = {'version': 2, 'submitted_at': '2026-01-02T00:00:00Z'}
        skipped['pdf_observation'].update(version=2, requested_id=skipped['requested_id'])
        self.inventory.insert(1, skipped)
        self.scope['records'][0].update(frozen_N=4, versions=[r['frozen_version_metadata'] for r in self.inventory])
        for pair, (before, after) in zip(self.pairs, [(1, 3), (3, 4)]):
            pair.update(key=f'{self.aid}__v{before}_v{after}', before=before, after=after,
                        pdf_ids=[f'{self.aid}v{before}', f'{self.aid}v{after}'])
        self.states = [{**p, 'status': 'pending', 'attempt_count': 0} for p in self.pairs]
        unit = self.plan['selected_units'][0]
        unit['selected_version_ids'] = [1, 3, 4]
        unit['selected_transitions'] = [{'from_version': p['before'], 'to_version': p['after'],
                                         'gap_seconds': p['gap_seconds']} for p in self.pairs]
        self.plan['requested_pdfs'] = [{'requested_id': r['requested_id'], 'arxiv_id': self.aid,
                                       'version': r['version']} for r in self.inventory if r['selected_for_comparison']]
        self.freeze()
        self.complete_all()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual({p['key'] for p in report['pairs']}, {f'{self.aid}__v1_v3', f'{self.aid}__v3_v4'})

    def test_each_reported_turn_is_summed_once_with_partial_field_coverage(self):
        first = {'type': 'turn.completed', 'usage': self.usage}
        second = {'type': 'turn.completed', 'usage': {'input_tokens': 30, 'output_tokens': 5}}
        records = verify.parse_events(('\n'.join(json.dumps(e) for e in [first, second]) + '\n').encode())[0]
        usage = verify.summarize([{'token_usage': records}])
        self.assertEqual(usage['reported_totals']['total_tokens'], 155)
        self.assertEqual(usage['reported_totals']['cached_input_tokens'], 80)
        self.assertIsNone(usage['reported_totals']['non_cached_input_tokens'])
        self.assertEqual(usage['field_report_counts']['cached_input_tokens'], 1)
        self.assertEqual(usage['reported_turn_count'], 2)

    def test_source_overlap_does_not_double_count_unique_pairs_or_usage(self):
        self.config['source_names'].append('source_b')
        self.scope['authorized_sources'].append('source_b')
        self.scope['record_memberships'][self.aid].append('source_b')
        self.freeze()
        self.contract['source_membership_counts']['source_b'] = 1
        write(self.run / 'execution_contract.json', self.contract)
        self.complete_all()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['coverage']['units'], 1)
        self.assertEqual(report['coverage']['pairs'], 2)
        self.assertEqual(report['coverage']['sources']['source_a']['pairs'], 2)
        self.assertEqual(report['coverage']['sources']['source_b']['pairs'], 2)
        self.assertEqual(report['token_usage']['reported_totals']['total_tokens'], 240)

    def test_malformed_receipt_object_writes_failure_progress_instead_of_crashing(self):
        self.complete_all()
        write(self.path().with_suffix('.execution.json'), [])
        self.assertEqual(self.invoke('--require-complete'), 1)
        self.assertEqual(json.loads((self.run / 'verification_progress.json').read_text())['status'], 'failed')
        self.assertFalse((self.run / 'final_verification.json').exists())

    def test_active_receiptless_attempt_and_temporary_output_remain_in_progress(self):
        root = self.attempt_path()
        root.mkdir(parents=True)
        (root / 'events.jsonl').write_text('{"type":"turn.started"}\n{"type":')
        self.states[0].update(status='running', attempt_count=1)
        self.save_states()
        temporary = self.path().with_name(self.path().name + '.tmp')
        temporary.parent.mkdir(parents=True)
        temporary.write_text('{')
        report = self.audit()
        self.assertEqual(report['status'], 'in_progress', report['errors'])
        self.assertFalse(report['errors'])
        self.assertEqual(report['token_usage']['attempts_without_reported_usage'], 1)

    def test_live_changes_prevent_final_report(self):
        self.complete_all()
        with mock.patch.object(verify.Reader, 'changes', return_value=['pair_status.jsonl']):
            self.assertEqual(self.invoke('--require-complete'), 2)
        self.assertFalse((self.run / 'final_verification.json').exists())
        self.assertFalse((self.run / 'REPORT.md').exists())

    def test_pdf_reads_and_source_modifications_never_occur(self):
        self.complete_all()
        original = {path: path.read_bytes() for path in self.run.rglob('*') if path.is_file()}
        read_bytes = Path.read_bytes

        def guarded(path):
            self.assertNotEqual(path.suffix.lower(), '.pdf', 'Verifier tried to open a PDF')
            return read_bytes(path)

        with mock.patch.object(Path, 'read_bytes', guarded):
            self.assertEqual(self.invoke('--require-complete'), 0)
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertFalse((self.snapshot / '__pycache__').exists())

    def test_duplicate_json_keys_are_rejected(self):
        self.complete_all()
        self.path().write_text('{"overall":{},"overall":{}}')
        self.assertEqual(self.audit()['status'], 'failed')

    def recovered(self):
        """Build recovery evidence independently of the scheduler implementation."""
        self.complete_all()
        root = self.attempt_path()
        original = json.loads((root / 'receipt.json').read_text())
        original.update(status='failed', exit_code=-15, timed_out=True,
                        error='RuntimeError: Codex execution failed (exit=-15, timeout=True)')
        original.pop('result_sha256')
        original['artifact_sha256'].pop('result.json')
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(self.result)}},
                  {'type': 'turn.completed', 'usage': self.usage}]
        rows(root / 'events.jsonl', events)
        original['token_usage'] = verify.parse_events((root / 'events.jsonl').read_bytes())[0]
        work = root / 'work'
        write(root / 'command.json', {'argv': [self.config['codex_path'], 'exec', '--json', '--ignore-user-config',
            '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write', '--model', self.config['model'],
            '-c', 'model_reasoning_effort="max"', '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"',
            '--cd', str(work), '--color', 'never', '--output-schema', str(work / 'schema.json'),
            '--output-last-message', str(work / 'response.json'), '-'], 'working_directory': str(work)})
        for item in original['inputs']:
            item['path'] = str(Path(self.config['pdf_root']) / self.aid / f'{item["requested_id"]}.pdf')
            item['pair_path'] = str(self.prepared / self.aid / 'v1_v2' / f'v{item["version"]}.pdf')
        original['artifact_sha256'] = {name: sha(root / name) for name in original['artifact_sha256']}
        write(root / 'receipt.json', original)
        marker = {'kind': 'completed-model-turn-v1', 'original_receipt_sha256': sha(root / 'receipt.json'),
                  'result_sha256': sha(root / 'result.json'),
                  'warning': 'Recovered a completed model turn from a failed CLI process. Original exit code, '
                             'timeout and error remain in the immutable receipt; this is not a successful CLI exit.'}
        write(root / 'recovery.json', marker)
        self.archived[self.pairs[0]['key']] = [original]
        saved = {**original, 'status': 'completed', 'result_sha256': marker['result_sha256'],
                 'recovery': marker, 'attempts': [original], 'token_usage': verify.summarize([original]),
                 'output_sha256': sha(self.path()), 'output_path': str(self.path())}
        write(self.path().with_suffix('.execution.json'), saved)
        self.save_states()
        return root, saved

    def test_recovery_requires_original_failure_and_warns_without_counting_a_new_attempt(self):
        root, saved = self.recovered()
        original = (root / 'receipt.json').read_bytes()
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertEqual(report['token_usage']['attempt_count'], 2)
        self.assertEqual(report['token_usage']['reported_turn_count'], 2)
        self.assertEqual(report['token_usage']['reported_totals']['total_tokens'], 240)
        self.assertEqual(saved['exit_code'], -15)
        self.assertTrue(saved['timed_out'])
        self.assertEqual(saved['attempts'][0]['status'], 'failed')
        self.assertEqual(self.invoke('--require-complete'), 0)
        self.assertEqual((root / 'receipt.json').read_bytes(), original)
        self.assertIn('not a successful CLI exit', (self.run / 'REPORT.md').read_text())
        self.assertIn('exit_code=-15, timed_out=True', (self.run / 'REPORT.md').read_text())

    def test_recovery_accepts_only_exact_legacy_and_current_command_profiles(self):
        root, saved = self.recovered()
        legacy = json.loads((root / 'command.json').read_text())
        insertion = legacy['argv'].index('project_doc_max_bytes=0') - 1
        search = ['-c', 'web_search="disabled"']
        network = ['-c', 'sandbox_workspace_write.network_access=false']
        cases = [
            ('legacy', [], True),
            ('current', search + network, True),
            ('changed_search', ['-c', 'web_search="live"'] + network, False),
            ('changed_network', search + ['-c', 'sandbox_workspace_write.network_access=true'], False),
            ('missing_search', network, False),
            ('missing_network', search, False),
            ('duplicate_search', search + search + network, False),
            ('duplicate_network', search + network + network, False),
            ('reordered_flags', network + search, False),
        ]
        for case, settings, accepted in cases:
            with self.subTest(case=case):
                command = {**legacy, 'argv': legacy['argv'][:insertion] + settings + legacy['argv'][insertion:]}
                write(root / 'command.json', command)
                original = copy.deepcopy(saved['attempts'][0])
                original['artifact_sha256'] = {name: sha(root / name) for name in original['artifact_sha256']}
                write(root / 'receipt.json', original)
                marker = {**saved['recovery'], 'original_receipt_sha256': sha(root / 'receipt.json')}
                write(root / 'recovery.json', marker)
                rewritten = {**saved, **original, 'status': 'completed', 'recovery': marker,
                             'result_sha256': marker['result_sha256'], 'attempts': [original],
                             'token_usage': verify.summarize([original])}
                write(self.path().with_suffix('.execution.json'), rewritten)
                self.archived[self.pairs[0]['key']] = [original]
                self.save_states()
                report = self.audit()
                if accepted:
                    self.assertEqual(report['status'], 'passed', report['errors'])
                else:
                    self.assertEqual(report['status'], 'failed')
                    self.assertEqual(self.codes(report), {'recovery_command', 'canonical_recovery',
                                                         'missing_successful_archive'}, report['errors'])

    def test_fabricated_recovery_marker_or_success_fields_never_pass(self):
        root, saved = self.recovered()
        marker = json.loads((root / 'recovery.json').read_text())
        for field, value in [('kind', 'trust-me'), ('original_receipt_sha256', '0' * 64),
                             ('result_sha256', '0' * 64), ('warning', '')]:
            with self.subTest(field=field):
                write(root / 'recovery.json', dict(marker, **{field: value}))
                self.assertEqual(self.audit()['status'], 'failed')
        write(root / 'recovery.json', marker)
        for field, value in [('exit_code', 0), ('timed_out', False), ('error', None),
                             ('recovery', None)]:
            with self.subTest(field=field):
                write(self.path().with_suffix('.execution.json'), dict(saved, **{field: value}))
                self.assertEqual(self.audit()['status'], 'failed')
        write(self.path().with_suffix('.execution.json'), saved)
        (root / 'recovery.json').unlink()
        self.assertEqual(self.audit()['status'], 'failed')

    def test_recovery_cannot_hide_invalid_terminal_proof_behind_rehashed_receipts(self):
        root, saved = self.recovered()
        original_files = {p: p.read_bytes() for p in root.iterdir() if p.is_file()}
        cases = ['incomplete', 'wrong_message', 'last_nonterminal', 'multiple_turns', 'failed_turn',
                 'malformed_json', 'missing_usage', 'negative_usage', 'subset_usage', 'bad_schema',
                 'signature', 'input_hash', 'input_identity', 'input_path', 'prompt', 'command',
                 'cancelled', 'interrupted', 'invented_zero_exit']
        for case in cases:
            with self.subTest(case=case):
                for path, data in original_files.items():
                    path.write_bytes(data)
                original = json.loads((root / 'receipt.json').read_text())
                events = [json.loads(line) for line in (root / 'events.jsonl').read_text().splitlines()]
                if case == 'incomplete':
                    events.insert(-1, {'type': 'item.started', 'item': {'type': 'agent_message', 'text': '{}'}})
                elif case == 'wrong_message':
                    events[0]['item']['text'] = '{}'
                elif case == 'last_nonterminal':
                    events.append({'type': 'turn.started'})
                elif case == 'multiple_turns':
                    events.append(events[-1])
                elif case == 'failed_turn':
                    events.insert(0, {'type': 'turn.failed'})
                elif case == 'missing_usage':
                    events[-1]['usage'] = {}
                elif case == 'negative_usage':
                    events[-1]['usage']['input_tokens'] = -1
                elif case == 'subset_usage':
                    events[-1]['usage']['cached_input_tokens'] = 101
                elif case == 'bad_schema':
                    write(root / 'response.raw.json', {})
                    write(root / 'result.json', {})
                    events[0]['item']['text'] = '{}'
                elif case == 'signature':
                    original['execution_signature'] = '0' * 64
                elif case == 'input_hash':
                    original['input_sha256'][self.pairs[0]['pdf_ids'][0]] = '0' * 64
                elif case == 'input_identity':
                    original['inputs'][0]['version'] = 4
                elif case == 'input_path':
                    original['inputs'][0]['path'] = '/not-the-frozen-input.pdf'
                elif case == 'prompt':
                    (root / 'prompt.txt').write_text('Substituted task')
                    original['prompt_sha256'] = sha(root / 'prompt.txt')
                elif case == 'command':
                    command = json.loads((root / 'command.json').read_text())
                    command['argv'][command['argv'].index('--model') + 1] = 'wrong-model'
                    write(root / 'command.json', command)
                elif case == 'cancelled':
                    original['error'] = 'Run interrupted by its coordinator'
                elif case == 'interrupted':
                    original['status'] = 'interrupted'
                elif case == 'invented_zero_exit':
                    original.update(status='completed', exit_code=0, timed_out=False)
                rows(root / 'events.jsonl', events)
                if case == 'malformed_json':
                    with (root / 'events.jsonl').open('a') as stream:
                        stream.write('{')
                original['token_usage'] = verify.parse_events((root / 'events.jsonl').read_bytes())[0]
                original['artifact_sha256'] = {name: sha(root / name) for name in original['artifact_sha256']}
                write(root / 'receipt.json', original)
                marker = json.loads((root / 'recovery.json').read_text())
                marker.update(original_receipt_sha256=sha(root / 'receipt.json'), result_sha256=sha(root / 'result.json'))
                write(root / 'recovery.json', marker)
                rewritten = {**saved, **original, 'status': 'completed', 'recovery': marker,
                             'result_sha256': marker['result_sha256'], 'attempts': [original],
                             'token_usage': verify.summarize([original])}
                write(self.path().with_suffix('.execution.json'), rewritten)
                self.assertEqual(self.audit()['status'], 'failed', case)

    def test_recovery_rechecks_accessible_source_and_pair_pdf_bytes(self):
        # Generated bytes, no real PDFs or scientific content.
        for row in self.inventory:
            content = row['requested_id'].encode()
            row['pdf_observation']['pdf']['bytes'] = len(content)
        self.freeze()
        root, _ = self.recovered()
        original = json.loads((root / 'receipt.json').read_text())
        for item in original['inputs']:
            for field in ('path', 'pair_path'):
                path = Path(item[field])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(item['requested_id'].encode())
        report = self.audit()
        self.assertEqual(report['status'], 'passed', report['errors'])
        self.assertTrue(report['pdf_contents_read'])
        self.assertIn('PDF bytes were hashed again', verify.render_report(report))
        for field in ('path', 'pair_path'):
            path = Path(original['inputs'][0][field])
            old = path.read_bytes()
            path.write_bytes(b'X' * len(old))
            self.assertEqual(self.audit()['status'], 'failed')
            path.write_bytes(old)


if __name__ == '__main__':
    unittest.main()

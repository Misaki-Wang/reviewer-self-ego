"""Small synthetic fixtures for preview integrity; never research judgments."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from check_diff.build_preview import build, percentile, public_execution, receipt_prompt, safe_prompt
from check_diff.schema import DIMENSIONS, SCHEMA


def fixture(base):
    """Create one synthetic pair with a realistic matching API execution receipt."""
    inputs, results = base / 'inputs', base / 'results'
    inputs.mkdir()
    (results / '2511.00001').mkdir(parents=True)
    pair = {
        'arxiv_id': '2511.00001', 'title': 'SYNTHETIC TEST FIXTURE <script>alert(1)</script>',
        'source_ids': ['iclr2026', 'icml2026'], 'assigned_source_id': 'icml2026',
        'selected_version_ids': [1, 3], 'pair_type': 'adjacent_selected_maximum_sequence_versions',
        'before': {'version': 1, 'submitted_at': '2025-01-01T00:00:00Z', 'pages': 8, 'sha256': 'a' * 64},
        'after': {'version': 3, 'submitted_at': '2025-02-11T00:00:00Z', 'pages': None, 'sha256': 'b' * 64},
    }
    (inputs / 'pairs.json').write_text(json.dumps({'pairs': [pair], 'papers': [{
        'arxiv_id': '2511.00001', 'version_count': 3, 'selected_version_count': 2, 'pair_count': 1}], 'pairing_rule': {
        'pair_scope': 'All pairs among selected original versions.', 'operator': '>'}}))
    result = {d: {'change': 'no change', 'justification': 'Synthetic test evidence.'}
              for d in DIMENSIONS}
    result['overall'] = {'A1': {'value': 'comparable', 'justification': 'Synthetic test.'},
                         'A2': {side: {'value': 'uncertain', 'justification': 'Synthetic test.'}
                                for side in ('before', 'after')}}
    stem = results / '2511.00001/diff_v1_v3'
    stem.with_suffix('.json').write_text(json.dumps(result))
    prompt = b'Synthetic test prompt.\r\n'
    prompt_name = 'diff_v1_v3.20260911T091200000000Z.prompt.txt'
    (stem.parent / prompt_name).write_bytes(prompt)
    # This tempting legacy filename must not be displayed instead of the receipt's prompt.
    stem.with_suffix('.prompt.txt').write_text('Wrong old prompt.')
    receipt = {
        'schema_version': 'check-diff-execution-1', 'backend': 'responses',
        'pair_id': '2511.00001__v1_to_v3', 'arxiv_id': '2511.00001',
        'versions': {'before': 1, 'after': 3}, 'result_file': stem.name + '.json',
        'prompt_file': prompt_name, 'prompt_sha256': hashlib.sha256(prompt).hexdigest(),
        'schema_sha256': hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode()).hexdigest(),
        'result_sha256': hashlib.sha256(stem.with_suffix('.json').read_bytes()).hexdigest(),
        'pdfs': [{'side': side, 'version': pair[side]['version'], 'sha256': pair[side]['sha256']}
                 for side in ('before', 'after')],
        'status': 'completed', 'elapsed_seconds': 71, 'model': 'synthetic-test-model',
        'reasoning_effort': 'max', 'returned_model': 'synthetic-test-model',
        'ended_at_utc': '2026-01-01T00:00:00Z',
        'response': {'output': [{'reasoning': 'DO NOT COPY'}]},
    }
    stem.with_suffix('.execution.json').write_text(json.dumps(receipt))
    (results / 'run_manifest.json').write_text(json.dumps({'wall_time_seconds': 60, 'workers': 50}))
    return inputs, results, stem, result, receipt


def silent_build(inputs, results, site):
    with contextlib.redirect_stdout(io.StringIO()):
        return build(inputs, results, site)


class PreviewTests(unittest.TestCase):
    def test_execution_allowlist_excludes_payloads_and_nested_secrets(self):
        safe = public_execution({
            'status': 'failed', 'elapsed_seconds': 42, 'error': 'sensitive gateway body',
            'response': {'reasoning': 'private'}, 'api_key': 'secret',
            'attempts': [{'attempt': 1, 'elapsed_seconds': 40, 'error': 'Bearer secret', 'http_status': 429,
                          'artifacts': {'response': 'private.json'},
                          'usage': {'input_tokens': 99, 'reasoning': 'hidden'}}],
        })
        encoded = json.dumps(safe)
        self.assertNotIn('secret', encoded)
        self.assertNotIn('private', encoded)
        self.assertNotIn('reasoning', encoded)
        self.assertEqual(safe['attempts'][0]['usage'], {'input_tokens': 99})
        self.assertEqual(safe['attempts'][0]['http_status'], 429)
        for invalid in ('429 private gateway text', 600, True, None):
            self.assertNotIn('http_status', public_execution({'http_status': invalid}))

    def test_percentiles_preserve_missing_and_interpolate(self):
        self.assertIsNone(percentile([], .5))
        self.assertEqual(percentile([1, 3, 10, 20], .5), 6.5)
        self.assertAlmostEqual(percentile([1, 3, 10, 20], .95), 18.5)

    def test_encoded_pdf_and_credentials_never_become_prompt_downloads(self):
        for prompt in ('data:application/pdf;base64,JVBER', '"file_data": "base64"',
                       'Bearer abcdefghijklmnopqrstuvwxyz'):
            with self.assertRaises(ValueError):
                safe_prompt(prompt)

    def test_builder_keeps_real_schema_and_exact_selected_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inputs, results, stem, result, receipt = fixture(base)
            site = base / 'site'
            data = silent_build(inputs, results, site)
            saved = data['pairs'][0]
            self.assertEqual(saved['id'], '2511.00001__v1_v3')
            self.assertEqual(saved['gap_seconds'], 41 * 86400)
            self.assertIsNone(saved['after']['pages'])
            self.assertEqual(saved['status'], 'completed')
            self.assertEqual(saved['assigned_source_id'], 'icml2026')
            self.assertEqual(saved['result_availability'], 'current')
            self.assertEqual(saved['unit'], {'selected_version_ids': [1, 3], 'original_version_count': 3,
                                             'selected_version_count': 2, 'pair_count': 1})
            self.assertEqual(data['summary']['wall_time_seconds'], 60)
            self.assertEqual(data['summary']['p50_seconds'], 71)
            self.assertEqual(saved['result'], result)
            self.assertEqual((site / saved['prompt_url']).read_bytes(), b'Synthetic test prompt.\r\n')
            self.assertEqual(saved['saved_prompt_sha256'], receipt['prompt_sha256'])
            self.assertEqual(data['input']['pairing_rule']['pair_scope'], 'All pairs among selected original versions.')
            self.assertNotIn('DO NOT COPY', (site / 'data.json').read_text())
            self.assertFalse(list(site.rglob('*.pdf')))
            self.assertTrue((site / 'app.js').is_file())
            # The same pair with invalid judgments must never appear completed.
            stem.with_suffix('.json').write_text('{"unexpected": true}')
            invalid = silent_build(inputs, results, base / 'invalid-site')
            self.assertEqual(invalid['pairs'][0]['status'], 'unavailable')
            self.assertIsNone(invalid['pairs'][0]['result'])

    def test_latest_failed_running_or_stopped_receipt_never_displays_old_result(self):
        for status in ('failed', 'running', 'stopped'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                inputs, results, stem, _, receipt = fixture(base)
                receipt['status'] = status
                stem.with_suffix('.execution.json').write_text(json.dumps(receipt))
                data = silent_build(inputs, results, base / 'site')
                self.assertEqual(data['pairs'][0]['status'], status)
                self.assertEqual(data['pairs'][0]['result_availability'], 'stale_or_unverified')
                self.assertIsNone(data['pairs'][0]['result'])
                self.assertEqual(data['summary']['completed'], 0)

    def test_mismatched_receipt_provenance_cannot_mark_result_completed(self):
        for field, value in (
            ('pair_id', '2511.00001__v1_to_v2'), ('result_file', 'other.json'),
            ('schema_sha256', 'bad'), ('prompt_sha256', 'bad'), ('pdfs', []),
            ('result_sha256', 'bad'), ('returned_model', 'wrong-model'),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                inputs, results, stem, _, receipt = fixture(base)
                receipt[field] = value
                stem.with_suffix('.execution.json').write_text(json.dumps(receipt))
                data = silent_build(inputs, results, base / 'site')
                self.assertEqual(data['pairs'][0]['status'], 'unavailable')
                self.assertIsNone(data['pairs'][0]['result'])
                if field == 'prompt_sha256':
                    self.assertIsNone(data['pairs'][0]['prompt_url'])

    def test_prompt_path_cannot_escape_receipt_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for path in ('../outside.prompt.txt', '/tmp/outside.prompt.txt', 'child/file.prompt.txt',
                         '..\\outside.prompt.txt'):
                with self.subTest(path=path):
                    raw, error = receipt_prompt(folder, {'prompt_file': path})
                    self.assertIsNone(raw)
                    self.assertIsNotNone(error)
            (folder / 'subdir').mkdir()
            (folder / 'subdir/real.prompt.txt').write_text('Do not follow outside the receipt folder.')
            (folder / 'linked.prompt.txt').symlink_to(folder / 'subdir/real.prompt.txt')
            raw, error = receipt_prompt(folder, {'prompt_file': 'linked.prompt.txt'})
            self.assertIsNone(raw)
            self.assertIsNotNone(error)

    def test_valid_result_without_receipt_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inputs, results, stem, _, _ = fixture(base)
            stem.with_suffix('.execution.json').unlink()
            data = silent_build(inputs, results, base / 'site')
            self.assertEqual(data['pairs'][0]['status'], 'unavailable')
            self.assertIsNone(data['pairs'][0]['result'])
            self.assertIsNone(data['pairs'][0]['prompt_url'])

    def test_full_selected_trajectory_is_preserved_for_every_sibling_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inputs, results, _, _, _ = fixture(base)
            inventory = json.loads((inputs / 'pairs.json').read_text())
            first = inventory['pairs'][0]
            first['selected_version_ids'] = [1, 3, 5, 7]
            inventory['papers'][0].update(version_count=8, selected_version_count=4, pair_count=3)
            for before, after in ((3, 5), (5, 7)):
                inventory['pairs'].append({**first,
                    'before': {**first['before'], 'version': before},
                    'after': {**first['after'], 'version': after}})
            (inputs / 'pairs.json').write_text(json.dumps(inventory))
            data = silent_build(inputs, results, base / 'site')
            self.assertEqual(data['summary']['pairs'], 3)
            self.assertEqual(data['summary']['papers'], 1)
            for pair in data['pairs']:
                self.assertEqual(pair['unit']['selected_version_ids'], [1, 3, 5, 7])
                self.assertEqual(pair['unit']['original_version_count'], 8)
                self.assertEqual(pair['unit']['selected_version_count'], 4)
                self.assertEqual(pair['unit']['pair_count'], 3)


if __name__ == '__main__':
    unittest.main()

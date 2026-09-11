"""Verify file-ID provenance and strict completion identity without network calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import api_transport as api
import runner
from schema import SCHEMA
import test_api_transport as fixtures


class UploadedInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'input' / '2210.12860' / 'v1_v3'
        folder.mkdir(parents=True)
        self.pair = runner.Pair(folder, '2210.12860', 1, 3)
        for number, pdf in enumerate(self.pair.pdfs):
            pdf.write_bytes(b'%PDF-fixture-' + str(number).encode())
        meta = {side: {'sha256': api.file_sha256(pdf), 'pages': 10}
                for side, pdf in zip(('before', 'after'), self.pair.pdfs)}
        (folder / 'pair.json').write_text(json.dumps(meta))
        self.manifest_path = self.root / 'uploaded.json'
        self.manifest = {'schema_version': 'check-diff-uploaded-pdfs-1',
                         'base_url': 'https://example.invalid/v1', 'files': [
            {'arxiv_id': self.pair.arxiv_id, 'version': version, 'filename': pdf.name,
             'file_id': f'file-fixture-v{version}', 'bytes': pdf.stat().st_size,
             'sha256': api.file_sha256(pdf), 'status': 'processed',
             'uploaded_at_utc': '2026-09-11T12:00:00Z', 'upload_seconds': 1.25}
            for version, pdf in zip((1, 3), self.pair.pdfs)]}
        self.write_manifest()
        self.args = SimpleNamespace(output=self.root / 'output', model='gpt-6-astra', effort='max',
            backend='responses', input_mode='file_ids', uploaded_inputs=self.manifest_path,
            api_connection=api.Connection('https://example.invalid/v1', 'fixture-key'),
            timeout=10, retries=1, retry_base_delay=0.001, retry_max_delay=0.01,
            overwrite=False, workers=50, start_interval=0)
        runner.STOP.clear()
        runner.START_GATE = runner.RequestStartGate()
        self.addCleanup(runner.STOP.clear)
        self.addCleanup(setattr, runner, 'START_GATE', runner.RequestStartGate())

    def write_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def load(self):
        return runner.load_uploaded_inputs(self.manifest_path, self.pair,
                                          runner.pdf_inventory(self.pair), self.args.api_connection)

    def execute(self, events=None):
        with patch.object(api, '_open', return_value=fixtures.stream(*(events or [fixtures.completed()]))) as opened, contextlib.redirect_stdout(io.StringIO()):
            status = runner.run_pair(self.args, self.pair)
        return status, opened

    def receipt(self):
        return json.loads(self.pair.output_path(self.args.output).with_suffix('.execution.json').read_text())

    def test_manifest_binds_uploaded_ids_to_original_pdf_identity(self):
        self.manifest['base_url'] += '/'
        self.write_manifest()
        with patch.object(api, '_open') as opened:
            entries, provenance = self.load()
            opened.assert_not_called()
        self.assertEqual([entry['file_id'] for entry in entries], ['file-fixture-v1', 'file-fixture-v3'])
        self.assertEqual([entry['side'] for entry in entries], ['before', 'after'])
        self.assertEqual(provenance['manifest_sha256'], api.file_sha256(self.manifest_path))
        self.assertEqual(provenance['base_url'], self.args.api_connection.base_url)

    def test_payload_uses_only_file_ids_in_attachment_order(self):
        entries, _ = self.load()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Do not inline uploaded PDFs')):
            payload = api.build_payload('Prompt', self.pair.pdfs, SCHEMA, 'gpt-6-astra', 'max', uploaded_files=entries)
        self.assertEqual(payload['input'][0]['content'], [
            {'type': 'input_text', 'text': 'Prompt'},
            {'type': 'input_file', 'file_id': 'file-fixture-v1'},
            {'type': 'input_file', 'file_id': 'file-fixture-v3'}])
        self.assertEqual(payload['text']['format']['schema'], SCHEMA)
        self.assertFalse(payload['store'])
        with self.assertRaises(ValueError):
            api.build_payload('Prompt', self.pair.pdfs, SCHEMA, 'gpt-6-astra', 'max', uploaded_files=entries[:1])

    def test_invalid_manifest_entries_fail_before_request(self):
        baseline = json.loads(json.dumps(self.manifest))
        mutations = [lambda d: d.update(schema_version='other'),
                     lambda d: d.update(base_url='https://other.invalid/v1'),
                     lambda d: d['files'].pop(),
                     lambda d: d['files'].append(dict(d['files'][0])),
                     lambda d: d['files'][0].update(arxiv_id='9999.99999'),
                     lambda d: d['files'][0].update(filename='v2.pdf'),
                     lambda d: d['files'][0].update(sha256='0' * 64),
                     lambda d: d['files'][0].update(bytes=1),
                     lambda d: d['files'][0].update(status='uploaded'),
                     lambda d: d['files'][0].update(file_id=''),
                     lambda d: d['files'][0].update(file_id='file-bad\n'),
                     lambda d: d['files'][0].update(upload_seconds=-1)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.manifest = json.loads(json.dumps(baseline))
                mutate(self.manifest)
                self.write_manifest()
                status, opened = self.execute()
                self.assertEqual(status, 'failed')
                opened.assert_not_called()

    def test_completed_receipt_keeps_upload_provenance_and_resumes(self):
        status, opened = self.execute()
        self.assertEqual(status, 'done')
        opened.assert_called_once()
        receipt = self.receipt()
        self.assertEqual(receipt['input_mode'], 'uploaded_original_pdf_files')
        self.assertEqual(receipt['uploaded_inputs']['files'][0]['file_id'], 'file-fixture-v1')
        self.assertEqual(receipt['pdfs'][0]['sha256'], self.manifest['files'][0]['sha256'])
        self.assertEqual(receipt['returned_model'], 'gpt-6-astra')
        self.assertEqual(receipt['returned_effort'], 'max')
        status, opened = self.execute()
        self.assertEqual(status, 'skipped')
        opened.assert_not_called()
        self.assertEqual(self.receipt(), receipt)
        self.manifest['files'][0]['file_id'] = 'file-reuploaded-v1'
        self.write_manifest()
        status, opened = self.execute()
        self.assertEqual(status, 'done')
        opened.assert_called_once()

    def test_native_completion_is_not_reused_for_uploaded_input_mode(self):
        self.args.input_mode = 'pdf'
        self.assertEqual(self.execute()[0], 'done')
        before = self.receipt()
        self.args.input_mode = 'file_ids'
        status, opened = self.execute()
        self.assertEqual(status, 'done')
        opened.assert_called_once()
        self.assertNotEqual(before['prompt_sha256'], self.receipt()['prompt_sha256'])

    def test_missing_or_mismatched_terminal_model_and_effort_fail_closed(self):
        for values in ({'model': None}, {'model': ''}, {'model': 'other-model'},
                       {'reasoning': None}, {'reasoning': {}}, {'reasoning': {'effort': 'high'}}):
            with self.subTest(values=values):
                self.args.overwrite = True
                early = {'type': 'response.created', 'response': {
                    'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'}, 'status': 'in_progress'}}
                status, opened = self.execute([early, fixtures.completed(**values)])
                self.assertEqual(status, 'failed')
                opened.assert_called_once()
                self.assertFalse(self.receipt()['attempts'][0]['retryable'])
                self.assertFalse(self.pair.output_path(self.args.output).exists())

    def test_nonempty_incomplete_details_cannot_be_completed(self):
        for detail in ({'reason': 'max_output_tokens'}, {'reason': 'unknown'}):
            with self.subTest(detail=detail):
                status, opened = self.execute([fixtures.completed(incomplete_details=detail)])
                self.assertEqual(status, 'failed')
                opened.assert_called_once()
                self.assertEqual(self.receipt()['attempts'][0]['incomplete_details'], detail)
                self.assertFalse(self.pair.output_path(self.args.output).exists())
        self.assertEqual(self.execute([fixtures.completed(incomplete_details={})])[0], 'done')

    def test_prompt_maps_uploaded_attachments_and_preserves_rubric(self):
        prompt = runner.make_prompt(self.pair, backend='responses', input_mode='file_ids')
        self.assertIn('first attached file is `v1.pdf` (before)', prompt)
        self.assertIn('second attached file is `v3.pdf` (after)', prompt)
        self.assertIn((runner.HERE / 'prompts/rubric.md').read_text(), prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertNotIn('file-fixture', prompt)

    def test_cli_requires_explicit_uploaded_input_mode_and_manifest(self):
        for flags in (['--input-mode', 'file_ids'],
                      ['--backend', 'responses', '--input-mode', 'file_ids'],
                      ['--uploaded-inputs', str(self.manifest_path)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                runner.main([str(self.pair.folder), *flags, '--dry-run'])
            self.assertEqual(error.exception.code, 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main([str(self.pair.folder), '--backend', 'responses', '--input-mode', 'file_ids',
                '--uploaded-inputs', str(self.manifest_path), '--model', 'gpt-6-astra', '--effort', 'max',
                '--workers', '50', '--start-interval', '10', '--retries', '2', '--dry-run']), 0)


if __name__ == '__main__':
    unittest.main()

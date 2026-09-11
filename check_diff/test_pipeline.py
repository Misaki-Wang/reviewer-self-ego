"""Exercise the complete controller with fake file-service and model responses."""
import contextlib
import copy
import io
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

import pipeline as controller

api_transport, runner = controller.api_transport, controller.runner


class FakeResponse:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class UploadScopeTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'records': [{'arxiv_id': '2601.01234'}],
                     'selected_units': [{'selected_version_ids': [1, 3, 4]}],
                     'requested_pdfs': [{'arxiv_id': '2601.01234', 'version': v,
                                         'pdf_path': f'/tmp/fixture-v{v}.pdf', 'expected_sha256': 'stale'}
                                        for v in (1, 3, 4)]}
        self.evidence = {'status': 'verified', 'verified_pdfs': {
            f'2601.01234v{v}': {'path': f'/tmp/fixture-v{v}.pdf', 'sha256': str(v) * 64, 'bytes': 100}
            for v in (1, 3, 4)}}

    def test_uploads_use_fresh_verified_hashes(self):
        bound = controller.verified_upload_requests(self.plan, self.evidence)
        self.assertEqual([x['expected_sha256'] for x in bound], [str(v) * 64 for v in (1, 3, 4)])
        self.assertTrue(all(x['expected_bytes'] == 100 for x in bound))
        self.assertEqual(self.plan['requested_pdfs'][0]['expected_sha256'], 'stale')

    def test_extra_duplicate_and_substituted_requests_are_rejected(self):
        for mutation in ('extra', 'duplicate', 'path'):
            plan = copy.deepcopy(self.plan)
            if mutation == 'extra':
                plan['requested_pdfs'].append({'arxiv_id': '2601.99999', 'version': 1, 'pdf_path': '/tmp/other.pdf'})
            elif mutation == 'duplicate':
                plan['requested_pdfs'].append(plan['requested_pdfs'][0])
            else:
                plan['requested_pdfs'][0]['pdf_path'] = '/tmp/unrelated.pdf'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.verified_upload_requests(plan, self.evidence)


class ControllerPipelineTests(unittest.TestCase):
    def test_readiness_immutable_pair_manifests_and_cleanup(self):
        self.exercise_pipeline(upload_only=False)

    def test_upload_only_uses_all_upload_workers_and_never_calls_model(self):
        self.exercise_pipeline(upload_only=True)

    def exercise_pipeline(self, upload_only):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aid = '2601.01234'
            pairs = [(1, 3), (3, 4)]
            for before, after in pairs:
                folder = root / 'input_data' / aid / f'v{before}_v{after}'
                folder.mkdir(parents=True)
                for version in (before, after):
                    (folder / f'v{version}.pdf').write_bytes(f'%PDF-fixture-v{version}'.encode())
            requests = []
            for version in (1, 3, 4):
                pdf = root / f'source_v{version}.pdf'
                pdf.write_bytes(f'%PDF-fixture-v{version}'.encode())
                requests.append({'arxiv_id': aid, 'version': version, 'pdf_path': str(pdf)})
            plan = {'records': [{'arxiv_id': aid}], 'summary': {'paper_count': 1, 'pair_count': 2},
                    'sample_selection': {'method': 'offline_fixture'}, 'requested_pdfs': requests,
                    'selected_units': [{'selected_version_ids': [1, 3, 4], 'selected_transitions': [
                        {'from_version': before, 'to_version': after} for before, after in pairs]}]}
            (root / 'input_data/selection_manifest.json').write_text(json.dumps(plan))
            evidence = {'status': 'verified', 'verified_pdfs': {
                f'{aid}v{item["version"]}': {'path': item['pdf_path'],
                    'sha256': hashlib.sha256(Path(item['pdf_path']).read_bytes()).hexdigest(),
                    'bytes': Path(item['pdf_path']).stat().st_size} for item in requests}}
            (root / 'input_data/materialization_manifest.json').write_text(json.dumps(evidence))
            run = root / 'run'
            connection = {'base_url': 'https://example.invalid/v1', 'api_key': 'offline-fixture'}
            argv = ['--input', str(root / 'input_data'), '--output', str(run), '--workers', '2']
            if upload_only:
                argv.append('--upload-only')
            first_started, second_started, all_done = (threading.Event() for _ in range(3))
            all_uploads_entered = threading.Barrier(len(requests))
            lock = threading.Lock()
            completed, deleted, captured, upload_versions = [], [], [], []
            upload_state = {'active': 0, 'peak': 0}

            def open_request(request, timeout):
                if request.method == 'POST':
                    self.assertTrue(request.full_url.endswith('/files'))
                    number = int(re.search(rb'filename="v(\d+)\.pdf"', request.data).group(1))
                    with lock:
                        upload_state['active'] += 1
                        upload_state['peak'] = max(upload_state['peak'], upload_state['active'])
                        upload_versions.append(number)
                    try:
                        all_uploads_entered.wait(3)
                        if number == 4 and not upload_only:
                            self.assertTrue(first_started.wait(3), 'First pair waited for the final upload')
                        data = (root / f'source_v{number}.pdf').read_bytes()
                        return FakeResponse({'id': f'file-v{number}', 'bytes': len(data),
                                             'filename': f'v{number}.pdf', 'status': 'processed'})
                    finally:
                        with lock:
                            upload_state['active'] -= 1
                self.assertEqual(request.method, 'DELETE')
                with lock:
                    self.assertEqual(upload_state['active'], 0, 'Cleanup started while an upload was active')
                    self.assertEqual(len(upload_versions), 3)
                if not upload_only:
                    self.assertTrue(all_done.is_set(), 'Uploaded input deleted while a comparison was active')
                file_id = request.full_url.rsplit('/', 1)[1]
                deleted.append(file_id)
                return FakeResponse({'id': file_id, 'deleted': True})

            def compare(args, pair):
                raw = args.uploaded_inputs.read_bytes()
                data = json.loads(raw)
                self.assertEqual([x['version'] for x in data['files']], [pair.before, pair.after])
                self.assertEqual(args.start_interval, 0)
                self.assertFalse(upload_only, 'Upload-only scope invoked a model comparison')
                if pair.before == 1:
                    first_started.set()
                    self.assertTrue(second_started.wait(3))
                else:
                    second_started.set()
                self.assertEqual(args.uploaded_inputs.read_bytes(), raw)
                self.assertEqual(len(json.loads((run / 'uploaded_inputs.json').read_text())['files']), 3)
                target = pair.output_path(args.output).with_suffix('.execution.json')
                api_transport.atomic_write(target, {'status': 'completed'})
                with lock:
                    captured.append(args.uploaded_inputs)
                    completed.append((pair.before, pair.after))
                    if len(completed) == 2:
                        all_done.set()
                return 'done'

            with patch.dict('os.environ', {'CHECK_DIFF_API_CONFIG_JSON': json.dumps(connection)}), \
                    patch.object(controller, 'materialize_plan'), \
                    patch.object(api_transport, '_open', side_effect=open_request), \
                    patch.object(runner, 'run_pair', side_effect=compare) as model_call, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(controller.main(argv), 0)
                if upload_only:
                    model_call.assert_not_called()
                else:
                    self.assertEqual(model_call.call_count, 2)
            self.assertCountEqual(upload_versions, [1, 3, 4])
            self.assertEqual(upload_state['peak'], 3)
            self.assertCountEqual(completed, [] if upload_only else pairs)
            self.assertEqual(len(set(captured)), 0 if upload_only else 2)
            self.assertCountEqual(deleted, ['file-v1', 'file-v3', 'file-v4'])
            self.assertEqual(len(json.loads((run / 'uploaded_inputs.json').read_text())['files']), 3)
            manifest = json.loads((run / 'run_manifest.json').read_text())
            self.assertEqual(manifest['phase_durations_overlap'], not upload_only)
            self.assertEqual(manifest['status'], 'completed')
            self.assertTrue(manifest['uploaded_files_cleanup_complete'])
            self.assertEqual(len(manifest['dispatches']), 0 if upload_only else 2)
            self.assertEqual(manifest['upload_workers'], 3)
            self.assertEqual(manifest['model_calls_permitted'], not upload_only)
            if upload_only:
                self.assertEqual(manifest['execution_scope'], 'upload_only')
                self.assertEqual(manifest['expected_pairs'], 0)
                self.assertEqual(manifest['planned_comparison_count'], 2)
                self.assertEqual(manifest['counts']['completed'], 0)
                self.assertEqual(list((run / 'output').rglob('*.execution.json')), [])


if __name__ == '__main__':
    unittest.main()

"""Mocked transport and API runner tests; no network or credentials required."""
import base64
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

import api_transport as api
import runner
from schema import SCHEMA, DIMENSIONS


def annotation():
    return {'overall': {'A1': {'value': 'comparable', 'justification': 'The same study.'},
                        'A2': {side: {'value': 'yes', 'justification': 'Empirical study.'} for side in ('before', 'after')}},
            **{dimension: {'change': 'no change', 'justification': 'The content is unchanged.'} for dimension in DIMENSIONS}}


def completed(output=None, **kwargs):
    output = json.dumps(annotation()) if output is None else output
    response = {'id': 'resp_test', 'model': 'gpt-6-astra', 'reasoning': {'effort': 'max'},
                'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 200},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': output}]}]}
    response.update(kwargs)
    return {'type': 'response.completed', 'response': response}


def stream(*events, done=True):
    text = ''.join('event: ' + event.get('type', 'event') + '\ndata: ' + json.dumps(event) + '\n\n' for event in events)
    return io.BytesIO((text + ('data: [DONE]\n\n' if done else '')).encode())


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.connection = api.Connection('https://example.invalid/v1', 'fixture-key')
        self.stop = threading.Event()
        self.prefix = self.root / 'attempt'

    def call(self, response, timeout=5):
        with patch.object(api, '_open', return_value=response) as opened:
            result = api.request_response(self.connection, {'fixture': True}, timeout, self.stop, self.prefix)
        return result, opened.call_args

    def test_connection_path_and_env_do_not_expose_credentials(self):
        value = json.dumps({'base_url': 'https://example.invalid/v1/', 'api_key': 'fixture-key'})
        path = self.root / 'connection.json'
        path.write_text(value)
        self.assertEqual(api.load_connection(path), self.connection)
        with patch.dict(os.environ, {'CHECK_DIFF_API_CONFIG_JSON': value}):
            self.assertEqual(api.load_connection(), self.connection)
        self.assertNotIn('fixture-key', repr(self.connection))
        for value in ('bad json', '{}', '{"base_url": 2}', '[]', '{"base_url":"http://example.invalid","api_key":"fixture-key"}',
                      '{"base_url":"https://user:fixture-key@example.invalid","api_key":"fixture-key"}'):
            path.write_text(value)
            with self.assertRaises(ValueError) as caught:
                api.load_connection(path)
            self.assertNotIn('fixture-key', str(caught.exception))

    def test_payload_includes_exact_both_pdf_bytes_and_strict_schema(self):
        pdfs = [self.root / 'v1.pdf', self.root / 'v3.pdf']
        for n, pdf in enumerate(pdfs):
            pdf.write_bytes(b'%PDF-' + bytes([n]) * 20)
        payload = api.build_payload('English prompt', pdfs, SCHEMA, 'gpt-6-astra', 'max')
        self.assertEqual(payload['reasoning'], {'effort': 'max'})
        self.assertTrue(payload['stream'])
        self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['schema'], SCHEMA)
        self.assertTrue(payload['text']['format']['strict'])
        for item, pdf in zip(payload['input'][0]['content'][1:], pdfs):
            self.assertEqual(item['filename'], pdf.name)
            self.assertEqual(item['type'], 'input_file')
            self.assertEqual(item['detail'], 'high')
            self.assertEqual(base64.b64decode(item['file_data'].split(',', 1)[1]), pdf.read_bytes())
        self.assertNotIn('api_key', json.dumps(payload))

    def test_oversized_serialized_request_is_rejected_before_network(self):
        payload = {'text': 'α' * 30}
        expected = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        with patch.object(api, 'MAX_REQUEST_BYTES', expected - 1), patch.object(api, '_open') as opened:
            with self.assertRaisesRegex(api.APIError, 'exceeds.*before network') as caught:
                api.request_response(self.connection, payload, 5, self.stop, self.prefix)
            opened.assert_not_called()
        self.assertEqual(caught.exception.metadata['request_body_bytes'], expected)
        self.assertEqual(caught.exception.metadata['request_body_limit_bytes'], expected - 1)
        with patch.object(api, 'MAX_REQUEST_BYTES', expected), patch.object(api, '_open', return_value=stream(completed())):
            result, metadata = api.request_response(self.connection, payload, 5, self.stop, self.prefix)
        self.assertEqual(result, annotation())
        self.assertEqual(metadata['request_body_bytes'], expected)

    def test_completed_stream_and_audit_artifacts(self):
        output = json.dumps(annotation())
        (result, metadata), call = self.call(stream({'type': 'response.output_text.delta', 'delta': output}, completed(output)))
        self.assertEqual(result, annotation())
        self.assertEqual(metadata['returned_effort'], 'max')
        self.assertEqual(metadata['returned_model'], 'gpt-6-astra')
        self.assertEqual(metadata['usage']['input_tokens'], 100)
        request = call.args[0]
        self.assertEqual(request.full_url, 'https://example.invalid/v1/responses')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-key')
        self.assertEqual(request.get_header('User-agent'), 'Reviewer-Self-Ego/1.3')
        self.assertEqual(request.method, 'POST')
        self.assertEqual((self.root / 'attempt.output.txt').read_text(), output)
        self.assertEqual(json.loads((self.root / 'attempt.response.json').read_text())['id'], 'resp_test')
        self.assertIn('response.completed', (self.root / 'attempt.sse').read_text())
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_unicode_stream_archive_preserves_line_endings_and_redacts_credentials(self):
        result = annotation()
        result['M1']['justification'] = 'Preserves α and café.'
        output = json.dumps(result, ensure_ascii=False)
        events = [{'type': 'response.output_text.delta', 'delta': output}, completed(output)]
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                raw = (f': source α fixture-key{newline}' + ''.join(
                    f'event: {event["type"]}{newline}data: {json.dumps(event, ensure_ascii=False)}{newline}{newline}'
                    for event in events) + f'data: [DONE]{newline}{newline}').encode('utf-8')
                (actual, _), _ = self.call(io.BytesIO(raw))
                self.assertEqual(actual, result)
                self.assertEqual((self.root / 'attempt.sse').read_bytes(),
                                 raw.replace(b'fixture-key', b'[REDACTED]'))
                self.assertEqual((self.root / 'attempt.output.txt').read_bytes(), output.encode('utf-8'))

    def test_done_or_eof_without_completed_is_not_success(self):
        for response in (stream(), stream({'type': 'response.output_text.delta', 'delta': '{}'}, done=False)):
            with self.assertRaisesRegex(api.APIError, 'without response.completed'):
                self.call(response)
            self.assertTrue((self.root / 'attempt.sse').exists())

    def test_failed_and_incomplete_are_errors_with_response_id(self):
        for kind in ('error', 'response.failed', 'response.incomplete'):
            with self.assertRaises(api.APIError) as caught:
                self.call(stream({'type': kind, 'response': {'id': 'resp_bad', 'status': 'incomplete'}}))
            self.assertEqual(caught.exception.metadata['response_id'], 'resp_bad')
            self.assertIn(kind, str(caught.exception))
            self.assertTrue((self.root / 'attempt.response.json').exists())

    def test_completed_with_invalid_status_or_error_is_rejected(self):
        for extra in ({'status': 'incomplete'}, {'error': {'message': 'failure'}}):
            with self.assertRaisesRegex(api.APIError, 'completion event is invalid'):
                self.call(stream(completed(**extra)))

    def test_invalid_json_and_delta_mismatch_are_rejected(self):
        with self.assertRaisesRegex(api.APIError, 'not valid JSON'):
            self.call(stream(completed('not json')))
        with self.assertRaisesRegex(api.APIError, 'differs from streamed text'):
            self.call(stream({'type': 'response.output_text.delta', 'delta': '{}'}, completed()))

    def test_truncated_or_malformed_sse_is_rejected(self):
        for raw in (b'data: {"type": "response.completed"}', b'data: broken\n\n', b'{"not": "sse"}\n', b'data: []\n\n'):
            with self.assertRaises(api.APIError):
                self.call(io.BytesIO(raw))

    def test_refusal_is_rejected(self):
        event = completed()
        event['response']['output'] = [{'type': 'message', 'content': [{'type': 'refusal'}]}]
        with self.assertRaisesRegex(api.APIError, 'refused'):
            self.call(stream(event))

    def test_http_errors_do_not_echo_server_or_credentials(self):
        error = urllib.error.HTTPError('https://example.invalid?key=fixture-key', 401, 'fixture-key', {}, io.BytesIO(b'Error: fixture-key'))
        with patch.object(api, '_open', side_effect=error):
            with self.assertRaisesRegex(api.APIError, 'HTTP 401') as caught:
                api.request_response(self.connection, {}, 5, self.stop, self.prefix)
        self.assertNotIn('fixture-key', str(caught.exception))
        self.assertEqual(caught.exception.metadata['http_status'], 401)
        self.assertEqual((self.root / 'attempt.error.txt').read_text(), 'Error: [REDACTED]')

    def test_redirects_are_not_followed(self):
        handler = api.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 307, 'Redirect', {}, 'https://other.invalid'))
        with patch.object(api.urllib.request, 'build_opener') as build:
            api._open('request fixture', timeout=123)
        self.assertIsInstance(build.call_args.args[0], api.NoRedirect)
        build.return_value.open.assert_called_once_with('request fixture', timeout=123)

    def test_remote_output_and_exception_redact_key(self):
        with self.assertRaises(api.APIError) as caught:
            self.call(stream({'type': 'error', 'message': 'fixture-key'}))
        self.assertNotIn('fixture-key', str(caught.exception))
        for artifact in self.root.iterdir():
            self.assertNotIn('fixture-key', artifact.read_text())
        with patch.object(api, '_open', side_effect=OSError('fixture-key')):
            with self.assertRaises(api.APIError) as caught:
                api.request_response(self.connection, {}, 5, self.stop, self.prefix)
        self.assertNotIn('fixture-key', str(caught.exception))

    def test_stop_before_request_avoids_network(self):
        self.stop.set()
        with patch.object(api, '_open') as opened:
            with self.assertRaisesRegex(api.APIError, 'Interrupted'):
                api.request_response(self.connection, {}, 5, self.stop, self.prefix)
            opened.assert_not_called()

    def test_timeout_closes_blocked_stream(self):
        class Blocked:
            def __init__(self):
                self.closed = threading.Event()
            def __iter__(self):
                self.closed.wait(5)
                return iter([])
            def close(self):
                self.closed.set()
        response = Blocked()
        start = time.monotonic()
        with self.assertRaisesRegex(api.APIError, 'timed out'):
            self.call(response, timeout=0.02)
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(response.closed.is_set())


class RequestStartGateTests(unittest.TestCase):
    def test_concurrent_request_starts_are_spaced(self):
        gate = runner.RequestStartGate(0.02)
        stop = threading.Event()
        def admit():
            gate.wait(stop)
            return time.monotonic()
        with ThreadPoolExecutor(max_workers=6) as pool:
            starts = sorted(pool.map(lambda _: admit(), range(6)))
        self.assertTrue(all(after - before >= 0.015 for before, after in zip(starts, starts[1:])))
        self.assertGreaterEqual(starts[-1] - starts[0], 0.09)

    def test_cancellation_interrupts_long_start_wait(self):
        gate = runner.RequestStartGate(5)
        stop = threading.Event()
        gate.wait(stop)
        timer = threading.Timer(0.02, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaisesRegex(api.APIError, 'Interrupted while waiting'):
            gate.wait(stop)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_zero_interval_does_not_wait(self):
        gate = runner.RequestStartGate()
        stop = threading.Event()
        with patch.object(stop, 'wait') as wait:
            for _ in range(10):
                gate.wait(stop)
            wait.assert_not_called()


class APIRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / 'input' / '2401.00001' / 'v1_v3'
        folder.mkdir(parents=True)
        self.pair = runner.Pair(folder, '2401.00001', 1, 3)
        for pdf in self.pair.pdfs:
            pdf.write_bytes(b'%PDF-fixture')
        meta = {side: {'sha256': api.file_sha256(pdf), 'pages': n + 10} for n, (side, pdf) in enumerate(zip(('before', 'after'), self.pair.pdfs))}
        (folder / 'pair.json').write_text(json.dumps(meta))
        self.args = SimpleNamespace(output=self.root / 'out', model='gpt-6-astra', effort='max',
            backend='responses', api_connection=api.Connection('https://example.invalid/v1', 'fixture-key'),
            timeout=5, retries=0, overwrite=False, workers=50)
        runner.STOP.clear()
        runner.START_GATE = runner.RequestStartGate()
        self.addCleanup(runner.STOP.clear)
        self.addCleanup(setattr, runner, 'START_GATE', runner.RequestStartGate())

    def run_pair(self, events=None):
        with patch.object(api, '_open', return_value=stream(*(events or [completed()]))), contextlib.redirect_stdout(io.StringIO()):
            return runner.run_pair(self.args, self.pair)

    def receipt(self):
        return json.loads(self.pair.output_path(self.args.output).with_suffix('.execution.json').read_text())

    def test_success_retains_result_schema_and_separate_timing_receipt(self):
        self.assertEqual(self.run_pair(), 'done')
        self.assertEqual(json.loads(self.pair.output_path(self.args.output).read_text()), annotation())
        receipt = self.receipt()
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['result_sha256'], api.file_sha256(self.pair.output_path(self.args.output)))
        self.assertEqual(receipt['versions'], {'before': 1, 'after': 3})
        self.assertEqual(receipt['pdfs'][0]['page_count'], 10)
        self.assertEqual(receipt['pdfs'][1]['page_count'], 11)
        self.assertEqual(receipt['input_mode'], 'original_pdf_files')
        self.assertGreaterEqual(receipt['elapsed_seconds'], receipt['attempts'][0]['elapsed_seconds'])
        self.assertTrue(receipt['started_at_utc'].endswith('Z'))
        self.assertTrue(receipt['ended_at_utc'].endswith('Z'))
        for path in self.args.output.rglob('*'):
            if path.is_file():
                self.assertNotIn('fixture-key', path.read_text())

    def test_failure_retains_receipt_and_raw_stream_without_judgment(self):
        self.assertEqual(self.run_pair([{'type': 'response.failed', 'response': {'id': 'bad', 'status': 'failed'}}]), 'failed')
        self.assertFalse(self.pair.output_path(self.args.output).exists())
        self.assertEqual(self.receipt()['status'], 'failed')
        self.assertEqual(self.receipt()['attempts'][0]['response_id'], 'bad')
        self.assertGreaterEqual(self.receipt()['elapsed_seconds'], 0)

    def test_invalid_schema_is_failure_and_preserves_output_for_audit(self):
        self.assertEqual(self.run_pair([completed('{}')]), 'failed')
        self.assertFalse(self.pair.output_path(self.args.output).exists())
        self.assertTrue(list(self.args.output.rglob('*.output.txt')))

    def test_resume_requires_matching_execution_and_model(self):
        self.assertEqual(self.run_pair(), 'done')
        old = self.receipt()
        with patch.object(api, '_open') as opened:
            self.assertEqual(runner.run_pair(self.args, self.pair), 'skipped')
            opened.assert_not_called()
        self.assertEqual(self.receipt(), old)
        self.args.effort = 'high'
        self.assertEqual(self.run_pair(), 'failed')
        self.assertIn('unexpected reasoning effort', self.receipt()['attempts'][0]['error'])

    def test_stopped_pair_has_receipt_without_calling_api(self):
        runner.STOP.set()
        with patch.object(api, '_open') as opened:
            self.assertEqual(runner.run_pair(self.args, self.pair), 'stopped')
            opened.assert_not_called()
        self.assertEqual(self.receipt()['status'], 'stopped')

    def test_bad_checksum_fails_before_request(self):
        self.pair.pdfs[0].write_bytes(b'%PDF-changed')
        with patch.object(api, '_open') as opened, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_pair(self.args, self.pair), 'failed')
            opened.assert_not_called()
        self.assertIn('checksum', self.receipt()['error'])

    def test_page_inventory_accepts_only_verified_positive_integer_counts(self):
        path = self.pair.folder / 'pair.json'
        metadata = json.loads(path.read_text())
        for pages in (12, None, True, False, 0, -1, 2.5, '12', [], {}):
            with self.subTest(pages=pages):
                metadata['before']['pages'] = pages
                path.write_text(json.dumps(metadata))
                record = runner.pdf_inventory(self.pair)[0]
                expected = 12 if type(pages) is int and pages == 12 else None
                self.assertEqual(record['page_count'], expected)
                self.assertEqual(record['page_count_status'], 'verified' if expected else 'unresolved')
                self.assertEqual(record['page_count_unresolved_reason'], None if expected else
                                 'No matching verified page-count metadata')

    def test_api_prompt_uses_attachments_and_preserves_rubric(self):
        prompt = runner.make_prompt(self.pair, backend='responses')
        self.assertNotIn(str(self.root), prompt)
        self.assertNotIn('PDF skill', prompt)
        self.assertIn('both attached original PDFs', prompt)
        self.assertIn('v1.pdf -> v3.pdf', prompt)
        self.assertIn((runner.HERE / 'prompts/rubric.md').read_text(), prompt)


    def test_start_wait_is_recorded_outside_request_timeout(self):
        self.args.start_interval = 0.02
        runner.START_GATE = runner.RequestStartGate(self.args.start_interval)
        runner.START_GATE.wait(runner.STOP)
        with patch.object(api, 'request_response', wraps=api.request_response) as request:
            self.assertEqual(self.run_pair(), 'done')
        receipt = self.receipt()
        attempt = receipt['attempts'][0]
        self.assertEqual(receipt['requested_concurrency'], 50)
        self.assertEqual(receipt['request_start_interval_seconds'], 0.02)
        self.assertGreaterEqual(attempt['start_delay_seconds'], 0.01)
        self.assertGreaterEqual(attempt['elapsed_seconds'], attempt['start_delay_seconds'] + attempt['request_elapsed_seconds'])
        self.assertIsNotNone(attempt['request_started_at_utc'])
        self.assertEqual(request.call_args.args[2], self.args.timeout)

    def test_cancelled_start_wait_records_delay_without_request(self):
        self.args.start_interval = 5
        runner.START_GATE = runner.RequestStartGate(5)
        runner.START_GATE.wait(runner.STOP)
        timer = threading.Timer(0.02, runner.STOP.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with patch.object(api, 'request_response') as request, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_pair(self.args, self.pair), 'stopped')
            request.assert_not_called()
        attempt = self.receipt()['attempts'][0]
        self.assertEqual(attempt['status'], 'stopped')
        self.assertGreater(attempt['start_delay_seconds'], 0)
        self.assertIsNone(attempt['request_started_at_utc'])
        self.assertIsNone(attempt['request_elapsed_seconds'])

    def test_gate_is_initialized_fresh_for_each_run(self):
        self.args.start_interval = 5
        with patch.object(runner, 'run_pair', return_value='done'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.run(self.args, [self.pair]), 0)
            first = runner.START_GATE
            self.assertEqual(first.interval, 5)
            self.args.start_interval = 0
            self.assertEqual(runner.run(self.args, [self.pair]), 0)
            self.assertIsNot(first, runner.START_GATE)
            self.assertEqual(runner.START_GATE.interval, 0)

    def test_cli_validates_optional_start_interval(self):
        for value in ('-1', 'nan', 'inf'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                runner.main([str(self.pair.folder), '--backend', 'responses', '--start-interval', value, '--dry-run'])
            self.assertEqual(error.exception.code, 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main([str(self.pair.folder), '--backend', 'responses', '--start-interval', '5', '--dry-run']), 0)

    def test_workers_are_used_by_existing_pool(self):
        self.assertEqual(self.args.workers, 50)
        with patch.object(runner, 'ThreadPoolExecutor', wraps=ThreadPoolExecutor) as pool, \
             patch.object(runner, 'run_pair', return_value='done'), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.run(self.args, [self.pair] * 100), 0)
        pool.assert_called_once_with(max_workers=50)


if __name__ == '__main__':
    unittest.main()

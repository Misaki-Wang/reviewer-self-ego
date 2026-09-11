"""Retry policy regression tests using local fixtures only; no live requests."""
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

import api_transport as api
import runner
import test_api_transport as fixtures


def http_error(status, *, headers=None, body=None):
    content = json.dumps(body or {'error': {'message': 'Mock API failure'}}).encode()
    return urllib.error.HTTPError('https://example.invalid/v1/responses', status,
                                  'Mock API failure', headers or {}, io.BytesIO(content))


class TransportRetryTests(unittest.TestCase):
    def setUp(self):
        fixtures.TransportTests.setUp(self)

    def failure(self, response=None, error=None):
        with patch.object(api, '_open', return_value=response, side_effect=error):
            with self.assertRaises(api.APIError) as caught:
                api.request_response(self.connection, {}, 5, self.stop, self.prefix)
        return caught.exception

    def test_error_default_is_permanent_and_explicit_retry_flag_is_recorded(self):
        for expected in (False, True):
            error = api.APIError('Mock failure', {'response_id': 'resp_retry'}, retryable=expected)
            self.assertIs(error.retryable, expected)
            self.assertIs(error.metadata['retryable'], expected)
            self.assertEqual(error.metadata['response_id'], 'resp_retry')
        self.assertFalse(api.APIError('Permanent failure').retryable)

    def test_http_statuses_distinguish_transient_and_permanent_failures(self):
        for status, retryable in [(408, True), (409, True), (429, True), (500, True),
                                  (502, True), (503, True), (504, True), (400, False),
                                  (401, False), (403, False), (404, False), (413, False)]:
            with self.subTest(status=status):
                error = self.failure(error=http_error(status))
                self.assertIs(error.retryable, retryable)
                self.assertIs(error.metadata['retryable'], retryable)
                self.assertEqual(error.metadata['http_status'], status)

    def test_quota_and_billing_failures_override_retryable_http_status(self):
        for code in ('insufficient_quota', 'billing_hard_limit_reached'):
            with self.subTest(code=code):
                error = self.failure(error=http_error(429, body={'error': {'code': code}}))
                self.assertFalse(error.retryable)

    def test_connection_and_socket_failures_are_retryable(self):
        for cause in (urllib.error.URLError('connection interrupted'),
                      ConnectionResetError('connection reset'), socket.timeout('timed out')):
            with self.subTest(cause=type(cause).__name__):
                self.assertTrue(self.failure(error=cause).retryable)

    def test_empty_truncated_malformed_streams_and_output_json_are_retryable(self):
        responses = [fixtures.stream(), io.BytesIO(b'data: {"type": "response.completed"}'),
                     io.BytesIO(b'data: broken\n\n'), io.BytesIO(b'data: []\n\n'),
                     io.BytesIO(b'{"not": "sse"}\n'), io.BytesIO(b'data: \xff\n\n'),
                     fixtures.stream(fixtures.completed('not json'))]
        for index, response in enumerate(responses):
            with self.subTest(case=index):
                self.assertTrue(self.failure(response=response).retryable)

    def test_failed_sse_server_or_rate_limit_errors_are_retryable(self):
        for code in ('server_error', 'rate_limit_exceeded'):
            for event_type in ('error', 'response.failed'):
                with self.subTest(code=code, event_type=event_type):
                    error_body = {'code': code, 'message': 'Mock failure'}
                    event = ({'type': 'error', **error_body} if event_type == 'error' else
                             {'type': event_type, 'response': {'id': 'resp_failed',
                              'status': 'failed', 'error': error_body}})
                    self.assertTrue(self.failure(response=fixtures.stream(event)).retryable)

    def test_refusal_and_fixed_output_limit_are_permanent(self):
        refusal = fixtures.completed()
        refusal['response']['output'] = [{'type': 'message', 'content': [{'type': 'refusal'}]}]
        limit = {'type': 'response.incomplete', 'response': {'id': 'resp_limit',
                 'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'}}}
        for event in (refusal, limit):
            with self.subTest(event=event['type']):
                self.assertFalse(self.failure(response=fixtures.stream(event)).retryable)

    def test_retry_after_seconds_and_milliseconds_are_preserved(self):
        for headers, expected in [({'Retry-After': '12.5'}, 12.5),
                                  ({'retry-after-ms': '1250'}, 1.25),
                                  ({'Retry-After': '0'}, 0),
                                  ({'retry-after-ms': '2500', 'Retry-After': '9'}, 9),
                                  ({'Retry-After': '120'}, 120.0)]:
            with self.subTest(headers=headers):
                error = self.failure(error=http_error(429, headers=headers))
                self.assertEqual(error.metadata['retry_after_seconds'], expected)

    def test_retry_after_http_date_is_respected(self):
        target = datetime.now(timezone.utc) + timedelta(seconds=120)
        header = format_datetime(target, usegmt=True)
        error = self.failure(error=http_error(503, headers={'Retry-After': header}))
        self.assertGreater(error.metadata['retry_after_seconds'], 118)
        self.assertLessEqual(error.metadata['retry_after_seconds'], 120)

    def test_invalid_retry_hints_do_not_create_unbounded_or_negative_waits(self):
        for name in ('Retry-After', 'retry-after-ms'):
            for value in ('bad', '-1', 'nan', 'inf', '-inf'):
                with self.subTest(name=name, value=value):
                    error = self.failure(error=http_error(429, headers={name: value}))
                    self.assertIsNone(error.metadata.get('retry_after_seconds'))

    def test_response_close_failure_does_not_mask_original_stream_error(self):
        class BrokenClose(io.BytesIO):
            def close(self):
                super().close()
                raise OSError('Mock close failed')

        with patch.object(sys, 'unraisablehook') as unraisable:
            error = self.failure(response=BrokenClose(b'data: [DONE]\n\n'))
        unraisable.assert_not_called()
        self.assertIn('without response.completed', str(error))
        self.assertTrue(error.retryable)


class RetryDelayTests(unittest.TestCase):
    def test_exponential_jitter_stays_inside_configured_cap(self):
        for attempt, upper in ((1, 2), (2, 4), (3, 8), (20, 60)):
            with self.subTest(attempt=attempt):
                values = [runner.retry_delay(attempt, {}, 2, 60) for _ in range(16)]
                self.assertTrue(all(0 <= value <= upper for value in values))
                self.assertGreater(len(set(values)), 1)

    def test_server_minimum_is_never_shortened_and_long_deferral_stops(self):
        for _ in range(8):
            self.assertGreaterEqual(runner.retry_delay(1, {'retry_after_seconds': 20}, 2, 60), 20)
        self.assertIsNone(runner.retry_delay(1, {'retry_after_seconds': 120}, 2, 60))
        self.assertEqual(runner.retry_delay(1, {'retry_after_seconds': 60}, 2, 60), 60)


class RunnerRetryTests(unittest.TestCase):
    def setUp(self):
        fixtures.APIRunnerTests.setUp(self)
        self.args.retries = 2
        self.args.retry_base_delay = 2
        self.args.retry_max_delay = 60

    def receipt(self):
        return fixtures.APIRunnerTests.receipt(self)

    def execute(self, responses, *, wait_effect=None):
        with patch.object(api, '_open', side_effect=responses) as opened, \
                patch.object(runner.STOP, 'wait', return_value=False, side_effect=wait_effect) as wait, \
                contextlib.redirect_stdout(io.StringIO()):
            status = runner.run_pair(self.args, self.pair)
        return status, opened, wait

    def test_transient_failure_retries_then_succeeds_with_auditable_backoff(self):
        cases = [('rate_limited', http_error(429, headers={'Retry-After': '9'}), 9),
                 ('empty_stream', fixtures.stream(), None)]
        for name, failure, retry_after in cases:
            with self.subTest(failure=name):
                self.args.output = self.root / name
                status, opened, wait = self.execute([failure, fixtures.stream(fixtures.completed())])
                self.assertEqual(status, 'done')
                self.assertEqual(opened.call_count, 2)
                wait.assert_called_once()
                attempts = self.receipt()['attempts']
                self.assertEqual([attempt['status'] for attempt in attempts], ['failed', 'completed'])
                self.assertTrue(all(attempt['elapsed_seconds'] >= 0 for attempt in attempts))
                self.assertTrue(attempts[0]['retryable'])
                self.assertEqual(attempts[0].get('retry_after_seconds'), retry_after)
                self.assertGreaterEqual(attempts[0]['retry_delay_seconds'], retry_after or 0)
                self.assertEqual(wait.call_args.args[0], attempts[0]['retry_delay_seconds'])
                self.assertGreaterEqual(attempts[0]['retry_wait_seconds'], 0)
                self.assertEqual(json.loads(self.pair.output_path(self.args.output).read_text()), fixtures.annotation())

    def test_transient_failure_exhaustion_has_no_extra_request_or_final_wait(self):
        status, opened, wait = self.execute([http_error(503) for _ in range(3)])
        self.assertEqual(status, 'failed')
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(wait.call_count, 2)
        attempts = self.receipt()['attempts']
        self.assertTrue(all(attempt['retryable'] for attempt in attempts))
        self.assertEqual(attempts[-1]['retry_stop_reason'], 'retries_exhausted')
        self.assertFalse(self.pair.output_path(self.args.output).exists())

    def test_permanent_http_failure_fails_fast(self):
        status, opened, wait = self.execute([http_error(401)])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        wait.assert_not_called()
        self.assertFalse(self.receipt()['attempts'][0]['retryable'])
        self.assertEqual(self.receipt()['attempts'][0]['retry_stop_reason'], 'non_retryable_error')

    def test_schema_invalid_output_retries_then_succeeds(self):
        status, opened, wait = self.execute([fixtures.stream(fixtures.completed('{}')),
                                           fixtures.stream(fixtures.completed())])
        self.assertEqual(status, 'done')
        self.assertEqual(opened.call_count, 2)
        wait.assert_called_once()
        self.assertTrue(self.receipt()['attempts'][0]['retryable'])

    def test_wrong_model_or_effort_fails_fast(self):
        for extra in ({'model': 'unexpected-model'}, {'reasoning': {'effort': 'low'}}):
            with self.subTest(extra=extra):
                status, opened, wait = self.execute([fixtures.stream(fixtures.completed(**extra))])
                self.assertEqual(status, 'failed')
                opened.assert_called_once()
                wait.assert_not_called()
                self.assertFalse(self.receipt()['attempts'][0]['retryable'])

    def test_local_result_save_failure_never_resubmits_paid_request(self):
        with patch.object(runner, 'save_result', side_effect=OSError('Mock disk full')) as save:
            status, opened, wait = self.execute([fixtures.stream(fixtures.completed())])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        save.assert_called_once()
        wait.assert_not_called()
        attempt = self.receipt()['attempts'][0]
        self.assertFalse(attempt['retryable'])
        self.assertIn('disk full', attempt['error'])

    def test_local_audit_save_failure_never_resubmits_paid_request(self):
        atomic_write = api.atomic_write

        def fail_response_archive(path, value, **kwargs):
            if Path(path).name.endswith('.response.json'):
                raise OSError('Mock response archive disk full')
            return atomic_write(path, value, **kwargs)

        with patch.object(api, 'atomic_write', side_effect=fail_response_archive):
            status, opened, wait = self.execute([fixtures.stream(fixtures.completed())])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        wait.assert_not_called()
        self.assertFalse(self.receipt()['attempts'][0]['retryable'])

    def test_quota_failure_with_unwritable_error_archive_stays_permanent(self):
        atomic_write = api.atomic_write

        def fail_error_archive(path, value, **kwargs):
            if Path(path).name.endswith('.error.txt'):
                raise OSError('Mock error archive disk full')
            return atomic_write(path, value, **kwargs)

        error = http_error(429, body={'error': {'code': 'insufficient_quota'}})
        with patch.object(api, 'atomic_write', side_effect=fail_error_archive):
            status, opened, wait = self.execute([error])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        wait.assert_not_called()
        attempt = self.receipt()['attempts'][0]
        self.assertFalse(attempt['retryable'])
        self.assertEqual(attempt['error_code'], 'insufficient_quota')
        self.assertEqual(attempt['http_status'], 429)

    def test_timeout_close_during_iterator_read_retries_unless_cancelled(self):
        class BlockedRead:
            def __init__(self, cancel):
                self.closed = threading.Event()
                self.cancel = cancel

            def __iter__(self):
                return self

            def __next__(self):
                if not self.closed.wait(1):
                    raise AssertionError('Timeout did not close the blocked stream')
                raise ValueError('I/O operation on closed file')

            def close(self):
                if self.cancel:
                    runner.STOP.set()
                self.closed.set()

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                runner.STOP.clear()
                self.args.output = self.root / ('cancelled' if cancel else 'timed_out')
                self.args.timeout = 0.02
                blocked = BlockedRead(cancel)
                started = time.monotonic()
                status, opened, wait = self.execute([blocked, fixtures.stream(fixtures.completed())])
                self.assertLess(time.monotonic() - started, 1)
                self.assertTrue(blocked.closed.is_set())
                self.assertTrue(self.receipt()['attempts'][0]['retryable'])
                self.assertEqual(status, 'stopped' if cancel else 'done')
                self.assertEqual(opened.call_count, 1 if cancel else 2)
                self.assertEqual(wait.call_count, 0 if cancel else 1)
                if cancel:
                    self.assertEqual(self.receipt()['attempts'][0]['retry_stop_reason'], 'cancelled')

    def test_cancellation_during_backoff_prevents_another_request(self):
        def cancel(delay):
            runner.STOP.set()
            return True

        status, opened, wait = self.execute([http_error(503)], wait_effect=cancel)
        self.assertEqual(status, 'stopped')
        opened.assert_called_once()
        wait.assert_called_once()
        self.assertEqual(self.receipt()['status'], 'stopped')
        self.assertEqual(len(self.receipt()['attempts']), 1)
        self.assertEqual(self.receipt()['attempts'][0]['retry_stop_reason'], 'cancelled')

    def test_long_retry_after_stops_without_shortening_server_minimum(self):
        status, opened, wait = self.execute([http_error(429, headers={'Retry-After': '120'})])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        wait.assert_not_called()
        attempt = self.receipt()['attempts'][0]
        self.assertTrue(attempt['retryable'])
        self.assertEqual(attempt['retry_after_seconds'], 120)
        self.assertEqual(attempt['retry_stop_reason'], 'retry_after_exceeds_max_delay')

    def test_zero_retries_disables_resubmission(self):
        self.args.retries = 0
        status, opened, wait = self.execute([http_error(503)])
        self.assertEqual(status, 'failed')
        opened.assert_called_once()
        wait.assert_not_called()

    def test_completed_resume_preserves_original_receipt_and_does_not_wait(self):
        self.assertEqual(self.execute([fixtures.stream(fixtures.completed())])[0], 'done')
        original = self.receipt()
        status, opened, wait = self.execute([])
        self.assertEqual(status, 'skipped')
        opened.assert_not_called()
        wait.assert_not_called()
        self.assertEqual(self.receipt(), original)

    def test_cli_retry_defaults_are_backend_specific_and_explicit_zero_is_kept(self):
        for backend, flags, expected in [('responses', [], 3), ('codex', [], 1),
                                         ('responses', ['--retries', '0'], 0)]:
            with self.subTest(backend=backend, flags=flags):
                with patch.object(runner, 'run', return_value=0) as run, \
                        patch.object(api, 'load_connection', return_value=self.args.api_connection), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(runner.main([str(self.pair.folder), '--backend', backend, *flags]), 0)
                args = run.call_args.args[0]
                self.assertEqual(args.retries, expected)
                self.assertEqual(args.retry_base_delay, 2)
                self.assertEqual(args.retry_max_delay, 60)

    def test_cli_rejects_nonfinite_nonpositive_or_inverted_retry_timing(self):
        invalid = [[flag, value] for flag in ('--timeout', '--retry-base-delay', '--retry-max-delay')
                   for value in ('0', '-1', 'nan', 'inf', '-inf')]
        invalid.append(['--retry-base-delay', '10', '--retry-max-delay', '2'])
        for flags in invalid:
            with self.subTest(flags=flags):
                with patch.object(api, '_open') as opened, contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit) as caught:
                    runner.main([str(self.pair.folder), '--backend', 'responses', *flags, '--dry-run'])
                self.assertEqual(caught.exception.code, 2)
                opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""Deterministic standalone stop-handler reentry and invocation isolation."""
import signal
import threading
import unittest
from unittest.mock import MagicMock, patch

from check_diff import runner


class StandaloneSignalTests(unittest.TestCase):
    def setUp(self):
        # Replace shared state instead of disturbing another test or run.
        self.event = threading.Event()
        self.stop = MagicMock(wraps=self.event)
        self.lock = MagicMock()
        self.process = MagicMock()
        self.process.pid = 12345
        self.process.poll.return_value = None
        for name, value in (
            ('STOP', self.stop),
            ('PROCESS_LOCK', self.lock),
            ('ACTIVE_PROCESSES', {self.process}),
            ('STOP_REQUESTED', False),
        ):
            patcher = patch.object(runner, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.killpg = self.enterContext(patch.object(runner.os, 'killpg'))

    def assert_stopped_once(self):
        self.assertTrue(self.event.is_set())
        self.stop.set.assert_called_once_with()
        self.lock.__enter__.assert_called_once_with()
        self.lock.__exit__.assert_called_once_with(None, None, None)
        self.killpg.assert_called_once_with(self.process.pid, signal.SIGTERM)
        self.process.wait.assert_called_once_with(timeout=5)

    def test_nested_stop_during_event_set_is_ignored(self):
        def set_with_nested_signal():
            if self.stop.set.call_count == 1:
                runner.request_stop(signal.SIGTERM, None)
            self.event.set()

        self.stop.set.side_effect = set_with_nested_signal
        runner.request_stop(signal.SIGINT, None)
        runner.request_stop(signal.SIGTERM, None)
        self.assert_stopped_once()

    def test_nested_stop_during_process_wait_is_ignored(self):
        def wait_with_nested_signal(timeout):
            if self.process.wait.call_count == 1:
                runner.request_stop(signal.SIGINT, None)
            return 0

        self.process.wait.side_effect = wait_with_nested_signal
        runner.request_stop(signal.SIGTERM, None)
        runner.request_stop(signal.SIGINT, None)
        self.assert_stopped_once()

    def test_consecutive_main_invocations_reset_stop_latch(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def stopped_run(args, pairs):
            self.assertFalse(self.event.is_set())
            self.assertFalse(runner.STOP_REQUESTED)
            runner.request_stop(signal.SIGINT, None)
            self.assertTrue(self.event.is_set())
            return 130

        with patch.object(runner, 'load_pairs', return_value=[object()]), \
                patch.object(runner, 'run', side_effect=stopped_run) as run:
            for _ in range(2):
                self.assertEqual(runner.main(['unused-input', '--output', 'unused-output']), 130)
                for sig, handler in previous.items():
                    self.assertEqual(signal.getsignal(sig), handler)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.stop.clear.call_count, 2)
        self.assertEqual(self.stop.set.call_count, 2)
        self.assertEqual(self.lock.__enter__.call_count, 2)
        self.assertEqual(self.killpg.call_count, 2)
        self.assertEqual(self.process.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()

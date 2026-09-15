"""Bounded real-signal regressions for coordinator cancellation lock safety."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest


class CancellationSignalTests(unittest.TestCase):
    def run_probe(self, code):
        with tempfile.TemporaryDirectory() as scratch:
            env = {**os.environ, 'TMPDIR': scratch}
            result = subprocess.run([sys.executable, '-B', '-c', textwrap.dedent(code)],
                                    capture_output=True, text=True, timeout=8, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repeated_signals_cannot_reenter_termination_locks(self):
        for phase in ('event', 'registry', 'process_wait'):
            with self.subTest(phase=phase):
                self.run_probe('phase = ' + repr(phase) + '\n' + textwrap.dedent("""
                    import os, signal, threading
                    from types import SimpleNamespace
                    from utility.batch.local_batch import Batch
                    b = object.__new__(Batch)
                    b.termination_started = False
                    injected = False
                    calls = []
                    def again():
                        global injected
                        if not injected:
                            injected = True
                            os.kill(os.getpid(), signal.SIGTERM)
                    class Stop(threading.Event):
                        def set(self):
                            with self._cond:
                                if phase == 'event': again()
                                self._flag = True
                                self._cond.notify_all()
                    class Registry:
                        def __init__(self): self.lock = threading.Lock()
                        def __enter__(self):
                            self.lock.acquire()
                            if phase == 'registry': again()
                        def __exit__(self, *args): self.lock.release()
                    wait_lock = threading.Lock()
                    def terminate(process):
                        with wait_lock:
                            if phase == 'process_wait': again()
                            calls.append(process)
                    b.stop = Stop()
                    b.process_lock = Registry()
                    b.processes = {123}
                    b.runner = SimpleNamespace(terminate=terminate)
                    signal.signal(signal.SIGTERM, b.terminate)
                    os.kill(os.getpid(), signal.SIGTERM)
                    os.kill(os.getpid(), signal.SIGTERM)
                    assert injected and b.stop.is_set()
                    assert calls == [123], calls
                """))

    def test_first_signal_during_coordinator_idle_exits_without_dispatch(self):
        self.run_probe("""
            import os, signal
            from tests.mbz.test_local_batch import BatchTests, batch
            fixture = BatchTests()
            fixture.setUp()
            try:
                batch.save(fixture.run / 'control.json', {
                    'workers': 2, 'auto_scale': False, 'pause_dispatch': True,
                    'minimum_free_bytes': 0})
                b = batch.Batch(fixture.run)
                injected = False
                original_sleep = batch.time.sleep
                def idle_sleep(seconds):
                    global injected
                    if seconds == 2 and not injected:
                        injected = True
                        os.kill(os.getpid(), signal.SIGTERM)
                    else:
                        original_sleep(seconds)
                def unsafe_event_wait(timeout):
                    global injected
                    with b.stop._cond:
                        injected = True
                        os.kill(os.getpid(), signal.SIGTERM)
                batch.time.sleep = idle_sleep
                b.stop.wait = unsafe_event_wait
                status = b.execute()
                assert injected and b.stop.is_set()
                assert status != 0
                assert not list((fixture.run / 'attempts').glob('*/attempt-*'))
                assert not list((fixture.run / 'output').glob('*/diff_*.json'))
            finally:
                fixture.doCleanups()
        """)

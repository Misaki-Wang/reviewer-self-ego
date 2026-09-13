"""Verify the existing cell scheduler at 64 workers without model calls."""
import threading
import time
import unittest
from unittest.mock import patch
from review_experiment import runner
from tests.review_experiment import test_concurrency

class Concurrency64Tests(unittest.TestCase):
    def test_64_overlapping_reviews_preserve_every_replicate(self):
        fixture=test_concurrency.ConcurrentReviewTests('test_parallel_replicates_keep_retry_receipts_and_usage')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        lock=threading.Lock();gate=threading.Event()
        active=peak=calls=0
        def call(*args):
            nonlocal active,peak,calls
            with lock:
                active+=1;calls+=1;peak=max(peak,active)
                if active==64:gate.set()
            gate.wait(5)
            try:
                time.sleep(.01)
                return fixture.response(*args)
            finally:
                with lock:active-=1
        timer=threading.Timer(5,gate.set);timer.start()
        try:
            with patch.object(runner,'call_codex',call):
                self.assertEqual(fixture.fixture.run_cli('--workers','64','--replicates','2'),0)
        finally:
            gate.set();timer.cancel()
        self.assertEqual(peak,64)
        self.assertEqual(calls,72)
        doc=fixture.fixture.result()
        self.assertEqual(doc['latest_selection_counts'],{'completed':72,'skipped':0,'failed':0})
        self.assertEqual([len(v['reviews']) for v in doc['versions'].values()],[24,24,24])
        self.assertTrue(all(r['status']=='completed' for v in doc['versions'].values() for r in v['reviews'].values()))

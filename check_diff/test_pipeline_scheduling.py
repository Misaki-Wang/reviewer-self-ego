"""Offline, event-driven checks for upload-ready pair scheduling."""
from collections import Counter
import json
import threading
from types import SimpleNamespace
import unittest

import pipeline as controller


WAIT_SECONDS = 3


class ReadyPairSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.aid = '2210.12860'
        self.versions = (1, 3, 4, 5)
        self.requests = [{'arxiv_id': self.aid, 'version': version} for version in self.versions]
        self.pairs = [SimpleNamespace(arxiv_id=self.aid, before=before, after=after)
                      for before, after in zip(self.versions, self.versions[1:])]
        self.caller = threading.get_ident()

    @staticmethod
    def identity(pair):
        return (pair.arxiv_id, pair.before, pair.after)

    @staticmethod
    def entry(item):
        return {**item, 'status': 'processed', 'file_id': f'file-fixture-v{item["version"]}'}

    def prepare(self, pair, entries):
        self.assertEqual([entry['version'] for entry in entries], [pair.before, pair.after])
        self.assertTrue(all(entry['arxiv_id'] == pair.arxiv_id for entry in entries))
        self.assertTrue(all(entry['status'] == 'processed' for entry in entries))
        return {'pair': self.identity(pair), 'snapshot': json.dumps(entries, sort_keys=True)}

    def run_serial(self, *args, **kwargs):
        return controller.run_ready_pairs(*args, **kwargs, upload_workers=1)

    def launch(self, function):
        outcome = {}
        finished = threading.Event()
        def invoke():
            try:
                outcome['counts'] = function()
            except BaseException as error:
                outcome['error'] = error
            finally:
                finished.set()
        thread = threading.Thread(target=invoke, daemon=True)
        thread.start()
        return thread, finished, outcome

    def assert_observable_failure(self, outcome, message):
        if 'error' in outcome:
            self.assertIsInstance(outcome['error'], RuntimeError)
            self.assertIn(message, str(outcome['error']))
        else:
            self.assertGreaterEqual(outcome['counts']['failed'], 1)

    def test_first_comparison_overlaps_later_uploads_and_callback_precedes_wait(self):
        first_started = threading.Event()
        uploads_finished = threading.Event()
        calls, preparations = [], []
        lock = threading.Lock()

        def upload(item):
            self.assertNotEqual(threading.get_ident(), self.caller)
            if item['version'] == 4:
                self.assertTrue(first_started.wait(WAIT_SECONDS), 'First ready pair was held behind later uploads')
            return self.entry(item)

        def prepare(pair, entries):
            self.assertEqual(threading.get_ident(), self.caller)
            args = self.prepare(pair, entries)
            preparations.append((self.identity(pair), args, args['snapshot']))
            return args

        def compare(args, pair):
            self.assertNotEqual(threading.get_ident(), self.caller)
            self.assertEqual(args['pair'], self.identity(pair))
            with lock:
                calls.append(self.identity(pair))
            if (pair.before, pair.after) == (1, 3):
                first_started.set()
                self.assertTrue(uploads_finished.wait(WAIT_SECONDS), 'Uploader waited for comparison before its callback')
            self.assertEqual([entry['version'] for entry in json.loads(args['snapshot'])], [pair.before, pair.after])
            return 'done'

        def finished():
            self.assertEqual(threading.get_ident(), self.caller)
            self.assertTrue(first_started.is_set())
            uploads_finished.set()

        counts = self.run_serial(self.requests, self.pairs, upload, prepare, compare,
                                            self.stop, 2, on_uploads_finished=finished)
        self.assertEqual(counts, {'done': 3, 'failed': 0, 'stopped': 0, 'skipped': 0})
        self.assertEqual(Counter(calls), Counter(self.identity(pair) for pair in self.pairs))
        self.assertEqual(len({id(args) for _, args, _ in preparations}), 3)
        self.assertTrue(all(args['snapshot'] == original for _, args, original in preparations))

    def test_default_starts_all_unit_uploads_concurrently(self):
        barrier = threading.Barrier(len(self.requests))
        entered = []
        lock = threading.Lock()

        def upload(item):
            self.assertNotEqual(threading.get_ident(), self.caller)
            with lock:
                entered.append(item['version'])
            barrier.wait(WAIT_SECONDS)
            return self.entry(item)

        counts = controller.run_ready_pairs(self.requests, self.pairs, upload, self.prepare,
                                            lambda args, pair: 'done', self.stop, 1)
        self.assertCountEqual(entered, self.versions)
        self.assertEqual(counts['done'], 3)

    def test_out_of_order_ready_pair_starts_before_slow_uploads_finish(self):
        barrier = threading.Barrier(len(self.requests))
        release_slow = threading.Event()
        slow_finished = set()
        comparisons, prepared = [], []
        lock = threading.Lock()

        def upload(item):
            barrier.wait(WAIT_SECONDS)
            if item['version'] in (1, 3):
                self.assertTrue(release_slow.wait(WAIT_SECONDS), 'Ready later pair was held behind slow uploads')
                with lock:
                    slow_finished.add(item['version'])
            return self.entry(item)

        def prepare(pair, entries):
            self.assertEqual(threading.get_ident(), self.caller)
            args = self.prepare(pair, entries)
            prepared.append((args, args['snapshot']))
            return args

        def compare(args, pair):
            self.assertEqual(args['pair'], self.identity(pair))
            with lock:
                comparisons.append((pair.before, pair.after))
            if (pair.before, pair.after) == (4, 5):
                with lock:
                    self.assertEqual(slow_finished, set())
                release_slow.set()
            return 'done'

        counts = controller.run_ready_pairs(self.requests, self.pairs, upload, prepare,
                                            compare, self.stop, 2)
        self.assertEqual(comparisons[0], (4, 5))
        self.assertCountEqual(comparisons, [(1, 3), (3, 4), (4, 5)])
        self.assertTrue(all(args['snapshot'] == original for args, original in prepared))
        self.assertEqual(counts['done'], 3)

    def test_upload_and_comparison_pools_have_separate_caps(self):
        release_uploads, release_comparisons = threading.Event(), threading.Event()
        two_uploads, comparison_started, uploads_finished = (threading.Event() for _ in range(3))
        lock = threading.Lock()
        state = {'uploads': 0, 'upload_peak': 0, 'compares': 0, 'compare_peak': 0, 'upload_entries': 0}

        def upload(item):
            with lock:
                state['uploads'] += 1
                state['upload_entries'] += 1
                state['upload_peak'] = max(state['upload_peak'], state['uploads'])
                if state['uploads'] == 2:
                    two_uploads.set()
            try:
                self.assertTrue(release_uploads.wait(WAIT_SECONDS))
                return self.entry(item)
            finally:
                with lock:
                    state['uploads'] -= 1

        def compare(args, pair):
            with lock:
                state['compares'] += 1
                state['compare_peak'] = max(state['compare_peak'], state['compares'])
            comparison_started.set()
            try:
                self.assertTrue(release_comparisons.wait(WAIT_SECONDS))
                return 'done'
            finally:
                with lock:
                    state['compares'] -= 1

        thread, finished, outcome = self.launch(lambda: controller.run_ready_pairs(
            self.requests, self.pairs, upload, self.prepare, compare, self.stop, 1,
            on_uploads_finished=uploads_finished.set, upload_workers=2))
        try:
            self.assertTrue(two_uploads.wait(WAIT_SECONDS))
            with lock:
                self.assertEqual(state['uploads'], 2)
                self.assertEqual(state['upload_entries'], 2)
            release_uploads.set()
            self.assertTrue(uploads_finished.wait(WAIT_SECONDS))
            self.assertTrue(comparison_started.wait(WAIT_SECONDS))
            with lock:
                self.assertEqual(state['compares'], 1)
                self.assertLessEqual(state['upload_peak'], 2)
                self.assertEqual(state['compare_peak'], 1)
        finally:
            release_uploads.set()
            release_comparisons.set()
            thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', outcome)
        self.assertEqual(outcome['counts']['done'], 3)
        self.assertEqual(state['uploads'], 0)
        self.assertEqual(state['compares'], 0)

    def test_upload_failure_waits_for_other_uploads_and_comparisons(self):
        barrier = threading.Barrier(len(self.requests))
        comparison_started, upload_failed = threading.Event(), threading.Event()
        release_upload, release_compare = threading.Event(), threading.Event()
        upload_finished, compare_finished = threading.Event(), threading.Event()

        def upload(item):
            barrier.wait(WAIT_SECONDS)
            if item['version'] == 4:
                self.assertTrue(comparison_started.wait(WAIT_SECONDS))
                upload_failed.set()
                raise RuntimeError('synthetic concurrent upload failure')
            if item['version'] == 5:
                try:
                    self.assertTrue(release_upload.wait(WAIT_SECONDS))
                finally:
                    upload_finished.set()
            return self.entry(item)

        def compare(args, pair):
            comparison_started.set()
            try:
                self.assertTrue(release_compare.wait(WAIT_SECONDS))
                return 'done'
            finally:
                compare_finished.set()

        thread, finished, outcome = self.launch(lambda: controller.run_ready_pairs(
            self.requests, self.pairs, upload, self.prepare, compare, self.stop, 2))
        try:
            self.assertTrue(upload_failed.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05))
            release_compare.set()
            self.assertTrue(compare_finished.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05), 'Helper exited while another upload was still active')
        finally:
            release_compare.set()
            release_upload.set()
            thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertTrue(upload_finished.is_set())
        self.assertTrue(compare_finished.is_set())
        self.assert_observable_failure(outcome, 'synthetic concurrent upload failure')

    def test_cancellation_settles_all_active_uploads_before_exit(self):
        barrier = threading.Barrier(len(self.requests))
        cancelled, compare_finished = threading.Event(), threading.Event()
        release_uploads, release_compare = threading.Event(), threading.Event()
        finished_uploads, comparisons = [], []
        lock = threading.Lock()

        def upload(item):
            barrier.wait(WAIT_SECONDS)
            if item['version'] in (4, 5):
                self.assertTrue(release_uploads.wait(WAIT_SECONDS))
            with lock:
                finished_uploads.append(item['version'])
            return self.entry(item)

        def compare(args, pair):
            comparisons.append((pair.before, pair.after))
            self.stop.set()
            cancelled.set()
            try:
                self.assertTrue(release_compare.wait(WAIT_SECONDS))
                return 'stopped'
            finally:
                compare_finished.set()

        thread, finished, outcome = self.launch(lambda: controller.run_ready_pairs(
            self.requests, self.pairs, upload, self.prepare, compare, self.stop, 2))
        try:
            self.assertTrue(cancelled.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05))
            release_compare.set()
            self.assertTrue(compare_finished.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05), 'Cleanup could race still-running uploads after cancellation')
        finally:
            release_compare.set()
            release_uploads.set()
            thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertNotIn('error', outcome)
        self.assertCountEqual(finished_uploads, self.versions)
        self.assertEqual(comparisons, [(1, 3)])
        self.assertEqual(outcome['counts']['done'], 0)
        self.assertGreaterEqual(outcome['counts']['stopped'], 1)

    def test_worker_concurrency_never_exceeds_requested_cap(self):
        release = threading.Event()
        two_active = threading.Event()
        lock = threading.Lock()
        state = {'active': 0, 'peak': 0}

        def compare(args, pair):
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                if state['active'] == 2:
                    two_active.set()
            try:
                self.assertTrue(release.wait(WAIT_SECONDS), 'Worker release callback was not reached')
                return 'done'
            finally:
                with lock:
                    state['active'] -= 1

        def finished():
            try:
                self.assertTrue(two_active.wait(WAIT_SECONDS))
                with lock:
                    self.assertEqual(state['active'], 2)
                    self.assertEqual(state['peak'], 2)
            finally:
                release.set()

        counts = self.run_serial(self.requests, self.pairs, self.entry, self.prepare,
                                            compare, self.stop, 2, on_uploads_finished=finished)
        self.assertEqual(counts['done'], 3)
        self.assertEqual(state, {'active': 0, 'peak': 2})

    def test_returned_failure_is_accounted_without_losing_other_comparisons(self):
        def compare(args, pair):
            return 'failed' if pair.before == 3 else 'done'
        counts = self.run_serial(self.requests, self.pairs, self.entry, self.prepare,
                                            compare, self.stop, 2)
        self.assertEqual(counts, {'done': 2, 'failed': 1, 'stopped': 0, 'skipped': 0})

    def test_upload_exception_settles_inflight_worker_before_exit(self):
        started = threading.Event()
        upload_failed = threading.Event()
        release = threading.Event()
        worker_finished = threading.Event()

        def upload(item):
            if item['version'] == 4:
                self.assertTrue(started.wait(WAIT_SECONDS))
                upload_failed.set()
                raise RuntimeError('synthetic upload failure')
            return self.entry(item)

        def compare(args, pair):
            started.set()
            try:
                self.assertTrue(release.wait(WAIT_SECONDS))
                return 'done'
            finally:
                worker_finished.set()

        thread, finished, outcome = self.launch(lambda: self.run_serial(
            self.requests, self.pairs, upload, self.prepare, compare, self.stop, 2))
        try:
            self.assertTrue(upload_failed.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05), 'Helper exited while a worker still used uploaded files')
        finally:
            release.set()
            thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertTrue(worker_finished.is_set())
        self.assert_observable_failure(outcome, 'synthetic upload failure')

    def test_worker_exception_settles_other_inflight_workers_before_exit(self):
        first_started = threading.Event()
        second_failed = threading.Event()
        release = threading.Event()
        first_finished = threading.Event()
        requests = self.requests[:3]
        pairs = self.pairs[:2]

        def compare(args, pair):
            if pair.before == 1:
                first_started.set()
                try:
                    self.assertTrue(release.wait(WAIT_SECONDS))
                    return 'done'
                finally:
                    first_finished.set()
            self.assertTrue(first_started.wait(WAIT_SECONDS))
            second_failed.set()
            raise RuntimeError('synthetic comparison failure')

        thread, finished, outcome = self.launch(lambda: self.run_serial(
            requests, pairs, self.entry, self.prepare, compare, self.stop, 2))
        try:
            self.assertTrue(second_failed.wait(WAIT_SECONDS))
            self.assertFalse(finished.wait(0.05), 'Helper exited before another comparison settled')
        finally:
            release.set()
            thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())
        self.assertTrue(first_finished.is_set())
        self.assert_observable_failure(outcome, 'synthetic comparison failure')

    def test_unprocessed_or_wrong_uploaded_identity_never_reaches_comparison(self):
        def forbidden(*args):
            self.fail('Invalid uploaded endpoint reached pair preparation or comparison')
        for changed in ({'status': 'uploaded'}, {'version': 99}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.run_serial(self.requests, self.pairs,
                    lambda item: {**self.entry(item), **changed}, forbidden, forbidden, self.stop, 2)

    def test_duplicate_or_missing_endpoint_plan_fails_before_any_upload(self):
        def forbidden(*args):
            self.fail('Invalid plan started work')
        cases = [(self.requests + [self.requests[0]], self.pairs),
                 (self.requests, self.pairs + [self.pairs[0]]),
                 (self.requests[:-1], self.pairs)]
        for requests, pairs in cases:
            with self.subTest(request_count=len(requests), pair_count=len(pairs)), self.assertRaises(ValueError):
                self.run_serial(requests, pairs, forbidden, forbidden, forbidden, self.stop, 2)

    def test_stop_before_start_prevents_all_uploads_and_submissions(self):
        self.stop.set()
        def forbidden(*args):
            self.fail('Stopped scheduler started new work')
        counts = self.run_serial(self.requests, self.pairs, forbidden, forbidden,
                                            forbidden, self.stop, 2)
        self.assertEqual(counts['done'], 0)
        self.assertEqual(counts['failed'], 0)

    def test_stop_after_upload_prevents_ready_pair_submission(self):
        uploads = []
        def upload(item):
            uploads.append(item['version'])
            if item['version'] == 3:
                self.stop.set()
            return self.entry(item)
        def forbidden(*args):
            self.fail('Stop must prevent preparing or submitting another pair')
        counts = self.run_serial(self.requests, self.pairs, upload, forbidden,
                                            forbidden, self.stop, 2)
        self.assertEqual(uploads, [1, 3])
        self.assertEqual(counts['done'], 0)

    def test_stop_during_overlap_prevents_later_upload_and_pair(self):
        fourth_upload_started = threading.Event()
        stopped = threading.Event()
        uploads, comparisons = [], []

        def upload(item):
            uploads.append(item['version'])
            if item['version'] == 4:
                self.assertFalse(self.stop.is_set())
                fourth_upload_started.set()
                self.assertTrue(stopped.wait(WAIT_SECONDS))
            return self.entry(item)

        def compare(args, pair):
            comparisons.append((pair.before, pair.after))
            self.assertEqual((pair.before, pair.after), (1, 3))
            self.assertTrue(fourth_upload_started.wait(WAIT_SECONDS))
            self.stop.set()
            stopped.set()
            return 'stopped'

        counts = self.run_serial(self.requests, self.pairs, upload, self.prepare,
                                            compare, self.stop, 2)
        self.assertEqual(uploads, [1, 3, 4])
        self.assertEqual(comparisons, [(1, 3)])
        self.assertEqual(counts['done'], 0)
        self.assertGreaterEqual(counts['stopped'], 1)


if __name__ == '__main__':
    unittest.main()

"""Review-level scheduling regressions; no model service is contacted."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from review_experiment import runner
from . import test_pipeline


class ConcurrentReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pipeline.PipelineTests('test_dry_run_has_no_reads_or_results')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def response(self, args, system, template, snapshot, work, attempt):
        result = {'oa': 6, 'strengths': ['Fixture strength'], 'weaknesses': ['Fixture weakness']}
        attempt['user_message'] = runner.render_user(template, args.other_model, work / 'manuscript.pdf')
        attempt['raw_response'] = json.dumps(result)
        attempt['exit_code'] = 0
        attempt['token_usage'] = {'source': 'codex_exec_turn_completed', 'records': [
            {'event_line': 1, 'usage': {'input_tokens': 100, 'cached_input_tokens': 20,
                                      'output_tokens': 5}}], 'errors': []}
        self.assertTrue(snapshot.read_bytes().startswith(b'%PDF-'))
        return result

    def test_32_calls_within_one_paper_preserve_all_cells_and_read_once(self):
        gate, lock = threading.Event(), threading.Lock()
        active, peak, calls, reads = 0, 0, 0, []
        original = runner.Version.read
        def read(version):
            reads.append((version.paper_id, version.number))
            return original(version)
        def call(*args):
            nonlocal active, peak, calls
            with lock:
                active += 1
                peak = max(peak, active)
                calls += 1
                if active == 32:
                    gate.set()
            gate.wait(5)
            try:
                time.sleep(0.015)
                return self.response(*args)
            finally:
                with lock:
                    active -= 1
        timer = threading.Timer(5, gate.set)
        timer.start()
        try:
            with patch.object(runner.Version, 'read', read), patch.object(runner, 'call_codex', call):
                self.assertEqual(self.fixture.run_cli('--workers', '32'), 0)
        finally:
            gate.set()
            timer.cancel()
        self.assertEqual(peak, 32)
        self.assertEqual(calls, 36)
        self.assertEqual(sorted(reads), [('2406.03862', n) for n in (1, 2, 3)])
        document = self.fixture.result()
        self.assertEqual(document['latest_selection_counts'], {'completed': 36, 'skipped': 0, 'failed': 0})
        for version in document['versions'].values():
            self.assertEqual(len(version['reviews']), 12)
            self.assertTrue(all(r['status'] == 'completed' for r in version['reviews'].values()))

    def test_resume_skips_exact_completed_records_with_parallel_remaining_cells(self):
        self.assertEqual(self.fixture.run_cli('--condition', 'exp1_quality', '--version', '1'), 0)
        original = deepcopy(self.fixture.result()['versions']['v1']['reviews']['exp1_quality/r1'])
        with patch.object(runner, 'call_codex', side_effect=self.response) as call:
            self.assertEqual(self.fixture.run_cli('--workers', '16'), 0)
            self.assertEqual(call.call_count, 35)
        document = self.fixture.result()
        self.assertEqual(document['versions']['v1']['reviews']['exp1_quality/r1'], original)
        reviews = deepcopy(document['versions'])
        with patch.object(runner, 'call_codex', side_effect=AssertionError('completed cell rerun')) as call:
            self.assertEqual(self.fixture.run_cli('--workers', '32'), 0)
            call.assert_not_called()
        self.assertEqual(self.fixture.result()['versions'], reviews)
        self.assertEqual(self.fixture.result()['latest_selection_counts']['skipped'], 36)

    def test_parallel_replicates_keep_retry_receipts_and_usage(self):
        lock = threading.Lock()
        calls = 0
        def call(*args):
            nonlocal calls
            with lock:
                calls += 1
                first = calls == 1
            result = self.response(*args)
            if first:
                raise RuntimeError('fixture transient failure')
            return result
        with patch.object(runner, 'call_codex', call):
            self.assertEqual(self.fixture.run_cli('--workers', '8', '--replicates', '2', '--retries', '1',
                                                 '--condition', 'exp1_quality', '--condition', 'exp3_years'), 0)
        reviews = [r for v in self.fixture.result()['versions'].values() for r in v['reviews'].values()]
        self.assertEqual(len(reviews), 12)
        self.assertEqual(calls, 13)
        self.assertEqual(sum(len(r['attempts']) for r in reviews), 13)
        self.assertEqual(sum(r['token_usage']['reported_totals']['total_tokens'] for r in reviews), 13 * 105)
        self.assertEqual(sum(a['status'] == 'failed' for r in reviews for a in r['attempts']), 1)

    def test_drain_finishes_active_calls_without_terminating_and_resumes(self):
        def call(*args):
            runner.DRAIN.set()
            time.sleep(0.02)
            return self.response(*args)
        with patch.object(runner, 'call_codex', call), patch.object(runner, 'terminate') as terminate:
            self.assertEqual(self.fixture.run_cli('--workers', '4'), 130)
            terminate.assert_not_called()
        before = deepcopy(self.fixture.result()['versions'])
        completed = sum(len(v['reviews']) for v in before.values())
        self.assertGreater(completed, 0)
        self.assertLessEqual(completed, 4)
        summary = json.loads((self.fixture.output / 'run_summary.json').read_text())
        self.assertEqual(summary['status'], 'drained')
        self.assertEqual(summary['pending_reviews'], 36 - completed)
        with patch.object(runner, 'call_codex', side_effect=self.response) as call:
            self.assertEqual(self.fixture.run_cli('--workers', '4'), 0)
            self.assertEqual(call.call_count, 36 - completed)
        after = self.fixture.result()['versions']
        for version, record in before.items():
            for key, value in record['reviews'].items():
                self.assertEqual(after[version]['reviews'][key], value)

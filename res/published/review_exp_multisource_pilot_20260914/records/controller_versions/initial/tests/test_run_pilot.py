"""Offline controller verification: unchanged frozen run_review, fake call_codex."""
import argparse
from collections import deque
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RUN = HERE.parent
spec = importlib.util.spec_from_file_location('pilot_controller', RUN / 'records/run_pilot.py')
ctl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ctl
spec.loader.exec_module(ctl)
TESTED_CONTROLLER_SHA256 = ctl.digest((RUN/'records/run_pilot.py').read_bytes())
CONFIG = ctl.read_json(RUN / 'pilot_config.json')
core = ctl.load_runner(RUN, CONFIG['source_revision'])
PROFILES, CONDITIONS = core.load_prompts(RUN / 'source_snapshot/review_experiment/prompts')


def fixture(root, buckets=2, paper_count=2, version_count=2, condition_count=2):
    sources = []
    base = ctl.runtime_args(CONFIG, core)
    for i, sid in enumerate(list(PROFILES)[:buckets], 1):
        args = argparse.Namespace(**vars(base), input=root / 'input_data' / sid,
                                  output=root / 'reviews' / f'b{i:02d}')
        for n in range(1, paper_count + 1):
            aid = f'2401.{n:05d}'  # Deliberate cross-source duplicate IDs.
            for version in range(1, version_count):
                pair = args.input / aid / f'v{version}_v{version+1}'
                pair.mkdir(parents=True)
                for number in (version, version + 1):
                    (pair / f'v{number}.pdf').write_bytes(f'%PDF-1.4\nfixture v{number}\n'.encode())
        papers, _ = core.discover(args.input)
        conditions = CONDITIONS[:condition_count]
        inventory = {'planned': paper_count * version_count * condition_count,
                     'full_planned': paper_count * version_count * condition_count}
        sources.append(ctl.Source(sid, args, papers, dict.fromkeys(papers, PROFILES[sid]), conditions, inventory))
        args.output.mkdir(parents=True)
    return sources


def fake_call(args, profile, template, snapshot, work, attempt):
    attempt['user_message'] = str(work / 'manuscript.pdf')
    attempt['token_usage'] = {'source': 'codex_exec_turn_completed', 'errors': [], 'records': [
        {'event_line': 1, 'usage': {'input_tokens': 10, 'cached_input_tokens': 3, 'output_tokens': 2}}]}
    return {'oa': profile.oa_values[0], 'strengths': ['Offline fixture'], 'weaknesses': []}


def make_controller(root, sources, levels=(4, 3, 2, 1), mode='full', pause=lambda: None):
    journal = ctl.Journal(root, core)
    journal.recover(sources)
    pending, counts = ctl.prepare_tasks(sources, core, journal)
    controller = ctl.Controller(root, core, sources, journal, pending, counts, lambda task: None,
                                pause_check=pause, levels=list(levels), heartbeat_seconds=.01, mode=mode)
    return controller


def all_reviews(sources):
    return [review for source in sources for path in source.args.output.glob('*/reviews.json')
            for version in ctl.read_json(path)['versions'].values() for review in version['reviews'].values()]


class ControllerTests(unittest.TestCase):
    def setUp(self):
        core.STOP.clear()
        core.DRAIN.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix='.offline-', dir=HERE)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        core.STOP.clear()
        core.DRAIN.clear()
        self.tmp.cleanup()

    def run_silent(self, controller, call=fake_call):
        with patch.object(core, 'call_codex', side_effect=call), redirect_stdout(io.StringIO()):
            return controller.run_pool()

    def test_policy_96_64_48_32_never_kills_active(self):
        cap = ctl.Capacity()
        active = 96
        for before, after in ((96, 64), (64, 48), (48, 32), (32, 32)):
            self.assertEqual(cap.fail(), (before, after))
            self.assertEqual(active, 96)
            self.assertFalse(cap.admits(active))
            self.assertFalse(cap.admits(after))
            self.assertTrue(cap.admits(after - 1))
        self.assertEqual([ctl.backoff(i) for i in range(1, 8)], [30, 60, 120, 240, 300, 300, 300])

    def test_global_cap_round_robin_and_profile_separation(self):
        sources = fixture(self.root)
        controller = make_controller(self.root, sources)
        active = maximum = calls = 0
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        def call(*args):
            nonlocal active, maximum, calls
            with lock:
                active += 1
                calls += 1
                first_wave = calls <= 4
                maximum = max(active, maximum)
            try:
                if first_wave:
                    barrier.wait(timeout=15)
                time.sleep(.03)
                return fake_call(*args)
            finally:
                with lock:
                    active -= 1
        result = self.run_silent(controller, call)
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['study_complete'])
        self.assertEqual(maximum, 4)
        self.assertEqual(result['maxobserved'], 4)
        events = [json.loads(line) for line in controller.event_path.read_text().splitlines()]
        admitted = [e for e in events if e['event'] == 'admitted']
        self.assertEqual([e['cell'][0] for e in admitted[:4]], [s.source_id for s in sources] * 2)
        self.assertEqual([e['cell'][1] for e in admitted[:4]], ['2401.00001']*2 + ['2401.00002']*2)
        self.assertTrue(all(e['active'] <= e['currentworkers'] for e in admitted))
        reviews = all_reviews(sources)
        pair = [r for r in reviews if r['condition'] == CONDITIONS[0][0]]
        self.assertNotEqual(pair[0]['fingerprint'], pair[len(pair)//2]['fingerprint'])
        for review in reviews:
            self.assertEqual(review['token_usage'], core.token_usage.summarize(review['attempts']))
            self.assertNotIn(review['source_id'], review['attempts'][0]['user_message'])

    def test_retries_preserve_every_usage_and_resume_receipts_only(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources, levels=(1,))
        counter = 0
        def call(*args):
            nonlocal counter
            counter += 1
            value = fake_call(*args)
            if counter <= 3:
                raise RuntimeError('Transient offline failure')
            return value
        with patch.object(ctl, 'backoff', return_value=.001):
            result = self.run_silent(controller, call)
        self.assertEqual(result['failedattempts'], 3)
        self.assertEqual(result['token_usage']['attempt_count'], 5)
        self.assertEqual(result['token_usage']['reported_totals']['total_tokens'], 60)
        before = all_reviews(sources)
        self.assertEqual(sum(len(r['attempts']) for r in before), 5)
        for review in before:
            self.assertEqual(sum(a['status'] == 'completed' for a in review['attempts']), 1)
            self.assertEqual(review['token_usage'], core.token_usage.summarize(review['attempts']))
        ids = {a['controller_attempt_id'] for r in before for a in r['attempts']}
        # Lose only canonical results; immutable receipts must restore successful cells.
        for source in sources:
            for path in source.args.output.glob('*/reviews.json'):
                path.unlink()
        for _ in range(2):
            journal = ctl.Journal(self.root, core)
            journal.recover(sources)
            pending, counts = ctl.prepare_tasks(sources, core, journal)
            self.assertFalse(pending)
            self.assertEqual(counts[sources[0].source_id]['completed'], 2)
            after = all_reviews(sources)
            self.assertEqual(ids, {a['controller_attempt_id'] for r in after for a in r['attempts']})
            archived = [a for rs in journal.receipts.values() for r in rs for a in r['review']['attempts']]
            self.assertEqual(len(archived), len(ids))
            self.assertEqual(core.token_usage.summarize(archived)['reported_totals']['total_tokens'], 60)

    def test_crash_after_core_overwrite_recovers_prior_and_new_attempt(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources, levels=(1,))
        task = controller.pending[0]
        def fail(*args):
            fake_call(*args)
            raise RuntimeError('Transient failure')
        with patch.object(core, 'call_codex', side_effect=fail), redirect_stdout(io.StringIO()):
            controller.journal.execute(task, lambda task: None)
        prior = ctl.cell_review(ctl.read_json(task.path), task.identity)['attempts'][0]
        with patch.object(core, 'call_codex', side_effect=fake_call), \
             patch.object(controller.journal, 'receipt', side_effect=OSError('Simulated crash before receipt')), \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                controller.journal.execute(task, lambda task: None)
        # run_review really replaced the canonical list with its single new attempt.
        self.assertEqual(len(ctl.cell_review(ctl.read_json(task.path), task.identity)['attempts']), 1)
        journal = ctl.Journal(self.root, core)
        journal.recover(sources)
        review = ctl.cell_review(ctl.read_json(task.path), task.identity)
        self.assertEqual(review['status'], 'completed')
        self.assertEqual(len(review['attempts']), 2)
        self.assertIn(prior, review['attempts'])
        self.assertEqual(review['token_usage']['reported_totals']['total_tokens'], 24)
        journal.recover(sources)
        self.assertEqual(ctl.cell_review(ctl.read_json(task.path), task.identity), review)
        archived = [a for rs in journal.receipts.values() for r in rs for a in r['review']['attempts']]
        self.assertEqual(len(archived), 2)

    def test_unfinished_intent_retains_unknown_usage(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources)
        task = controller.pending[0]
        intent = {'key': task.key, 'round_id': 'interrupted', 'identity': task.identity,
                  'fingerprint': task.fingerprint, 'started_at': ctl.now()}
        ctl.atomic_json(controller.journal.root / task.key / 'interrupted.started.json', intent, immutable=True)
        recovered = ctl.Journal(self.root, core)
        recovered.recover(sources)
        review = ctl.cell_review(ctl.read_json(task.path), task.identity)
        self.assertEqual(review['token_usage']['attempts_without_reported_usage'], 1)
        self.assertIsNone(review['token_usage']['reported_totals']['total_tokens'])
        self.assertEqual(review['attempts'][0]['status'], 'unknown')

    def test_auth_failure_drains_without_retries(self):
        sources = fixture(self.root)
        controller = make_controller(self.root, sources)
        count = 0
        def call(*args):
            nonlocal count
            count += 1
            value = fake_call(*args)
            if count == 1:
                args[-1]['stderr_tail'] = 'HTTP 401 Unauthorized authentication failed'
                raise RuntimeError('Codex exited 1')
            time.sleep(.04)
            return value
        result = self.run_silent(controller, call)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['active'], 0)
        self.assertGreater(result['pending'], 0)
        self.assertLessEqual(count, 4)
        self.assertEqual(result['failedattempts'], 1)
        self.assertTrue(ctl.systemic_failure([{'stderr_tail': 'insufficient_quota'}]))
        self.assertFalse(ctl.systemic_failure([{'stderr_tail': 'HTTP 429 temporary rate limit'}]))
        self.assertFalse(ctl.systemic_failure([{'raw_response': 'HTTP 401 Unauthorized'}]))

    def test_drain_stops_admission_and_cap_persists(self):
        sources = fixture(self.root)
        controller = make_controller(self.root, sources)
        controller.capacity.current = 2
        count = 0
        def call(*args):
            nonlocal count
            count += 1
            core.drain(None, None)
            return fake_call(*args)
        result = self.run_silent(controller, call)
        self.assertEqual(result['status'], 'drained')
        self.assertEqual(result['active'], 0)
        self.assertLessEqual(count, 2)
        core.DRAIN.clear()
        next_controller = make_controller(self.root, sources)
        self.assertEqual(next_controller.capacity.current, 2)

    def test_pause_and_gate_fail_closed(self):
        sources = fixture(self.root)
        controller = make_controller(self.root, sources, pause=lambda: 'Paused')
        with patch.object(core, 'call_codex', side_effect=AssertionError('must not invoke')):
            result = controller.run_pool()
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(result['completed'], 0)
        self.assertEqual(result['active'], 0)

    def test_unknown_history_then_success_can_complete_study(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources, levels=(1,))
        task = controller.pending[0]
        intent = {'key': task.key, 'round_id': 'lost', 'identity': task.identity,
                  'fingerprint': task.fingerprint, 'started_at': ctl.now()}
        ctl.atomic_json(controller.journal.root / task.key / 'lost.started.json', intent, immutable=True)
        recovered = make_controller(self.root, sources, levels=(1,))
        result = self.run_silent(recovered)
        self.assertTrue(result['study_complete'])
        self.assertTrue(result['whole_study_complete'])
        self.assertEqual(result['unknown_attempt_outcomes'], 1)
        self.assertEqual(result['token_usage']['attempts_without_reported_usage'], 1)
        self.assertEqual(result['completed'], 2)
        canonical_ids = {a['controller_attempt_id'] for r in all_reviews(sources) for a in r['attempts']}
        archived_ids = [a['controller_attempt_id'] for rs in recovered.journal.receipts.values()
                        for r in rs for a in r['review']['attempts']]
        self.assertEqual(canonical_ids, set(archived_ids))
        self.assertEqual(len(canonical_ids), len(archived_ids))

    def test_invalid_completed_cache_is_not_accepted_as_success(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources, levels=(1,))
        task = controller.pending[0]
        with patch.object(core, 'call_codex', side_effect=fake_call), redirect_stdout(io.StringIO()):
            controller.journal.execute(task, lambda task: None)
        # Corrupt an external/pre-controller result; no valid receipt backs this copy.
        shutil.rmtree(controller.journal.root)
        document = ctl.read_json(task.path)
        review = ctl.cell_review(document, task.identity)
        review['result']['oa'] = 'INVALID'
        ctl.atomic_json(task.path, document)
        recovered = make_controller(self.root, sources, levels=(1,))
        self.assertEqual(len(recovered.pending), 2)
        self.assertEqual(ctl.cell_review(ctl.read_json(task.path), task.identity)['status'], 'failed')
        self.assertEqual(self.run_silent(recovered)['status'], 'completed')

    def test_signal_handler_does_not_reenter_event_lock(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        controller = make_controller(self.root, sources, levels=(1,))
        previous = signal.signal(signal.SIGTERM, controller.record_signal)
        try:
            # Deterministically reproduce the dangerous signal delivery context.
            with core.STOP._cond:
                os.kill(os.getpid(), signal.SIGTERM)
                self.assertEqual(controller.pending_signals, [signal.SIGTERM])
                self.assertFalse(core.STOP.is_set())
            controller.apply_signals()
            self.assertTrue(core.STOP.is_set())
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_sigterm_and_usr1_during_backoff_without_calls(self):
        sources = fixture(self.root, buckets=1, paper_count=1, condition_count=1)
        for signum, status in ((signal.SIGTERM, 'stopped'), (signal.SIGUSR1, 'drained')):
            core.STOP.clear()
            core.DRAIN.clear()
            controller = make_controller(self.root, sources, levels=(1,))
            for task in controller.pending:
                task.ready_at = time.time() + 30
            previous = signal.signal(signum, controller.record_signal)
            timer = threading.Timer(.05, os.kill, (os.getpid(), signum))
            try:
                timer.start()
                started = time.monotonic()
                with patch.object(core, 'call_codex', side_effect=AssertionError('must not invoke')):
                    result = controller.run_pool()
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['active'], 0)
                self.assertEqual(result['completed'], 0)
                self.assertEqual(result['pending'], 2)
            finally:
                timer.cancel()
                timer.join()
                signal.signal(signum, previous)

    def test_lock_exclusion(self):
        with ExitStack() as one, ExitStack() as two:
            path = self.root / '.lock'
            ctl.acquire_lock(one, path)
            with self.assertRaises(ValueError):
                ctl.acquire_lock(two, path)


class InventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='.inventory-', dir=HERE)
        cls.root = Path(cls.tmp.name)
        shutil.copytree(RUN / 'source_snapshot', cls.root / 'source_snapshot', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy(RUN / 'source_snapshot_manifest.json', cls.root)
        (cls.root / 'records').mkdir()
        shutil.copy(RUN / 'records/run_pilot.py', cls.root / 'records/run_pilot.py')
        cls.sources = fixture(cls.root, buckets=10, paper_count=10, version_count=3, condition_count=12)
        cls.config = dict(CONFIG, sources=[{'source_id': s.source_id,
                                          'input': str(s.args.input.relative_to(cls.root)),
                                          'output': str(s.args.output.relative_to(cls.root))} for s in cls.sources])
        ctl.atomic_json(cls.root / 'pilot_config.json', cls.config)
        specs = []
        for source in cls.sources:
            papers = []
            for aid, versions in source.papers.items():
                rows = []
                for version in versions:
                    data, checksum = version.read()
                    rows.append({'number': version.number, 'sha256': checksum, 'bytes': len(data),
                                 'paths': [str(p.relative_to(cls.root)) for p in version.sources]})
                papers.append({'paper_id': aid, 'versions': rows})
            specs.append({'source_id': source.source_id, 'input': str(source.args.input.relative_to(cls.root)),
                          'output': str(source.args.output.relative_to(cls.root)), 'papers': papers,
                          'conditions': [c[0] for c in CONDITIONS], 'replicates': 1,
                          'paper_count': 10, 'version_count': 30, 'planned_reviews': 360,
                          'system_prompt': PROFILES[source.source_id].metadata()})
        cls.inventory = {'schema_version': 'review-pilot-inventory-1',
                         'source_revision': CONFIG['source_revision'], 'scope_revision': ctl.digest(ctl.canonical(specs)),
                         'sources': specs, 'totals': {'sources': 10, 'papers': 100, 'unique_paper_ids': 10,
                                                    'versions': 300, 'reviews': 3600}}
        ctl.atomic_json(cls.root / 'inventory.json', cls.inventory)
        for name in ('selection_manifest.json', 'scope_input.json'):
            ctl.atomic_json(cls.root / name, {})
        ctl.atomic_json(cls.root / 'records/controller-checks.json', {'status': 'passed', 'offline': True})
        names = ['records/controller-checks.json', 'pilot_config.json', 'inventory.json', 'selection_manifest.json', 'scope_input.json',
                 'source_snapshot_manifest.json', 'records/run_pilot.py']
        freeze = {'status': 'passed', 'source_revision': CONFIG['source_revision'],
                  'scope_revision': cls.inventory['scope_revision'],
                  'bound_files': {name: {'bytes': (cls.root/name).stat().st_size,
                                   'sha256': ctl.digest((cls.root/name).read_bytes())} for name in names}}
        ctl.atomic_json(cls.root / 'records/freeze_verification.json', freeze)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        core.STOP.clear()
        core.DRAIN.clear()

    def test_01_dryrun_no_mutations_except_checks(self):
        def hashes():
            return {str(p.relative_to(self.root)): ctl.digest(p.read_bytes()) for p in self.root.rglob('*')
                    if p.is_file() and p.name not in ('controller-checks.json', 'runtime-checks.json')}
        before = hashes()
        with patch.object(ctl, 'load_runner', return_value=core), \
             patch.object(core, 'call_codex', side_effect=AssertionError('must not invoke')), \
             redirect_stdout(io.StringIO()):
            status = ctl.main(['--config', str(self.root/'pilot_config.json'), '--dry-run'])
        self.assertEqual(status, 0)
        self.assertEqual(hashes(), before)
        self.assertEqual(ctl.read_json(self.root/'records/runtime-checks.json')['inventory']['planned'], 3600)

    def test_02_inventory_freeze_and_pdf_guards(self):
        gate = ctl.FrozenGate(self.root, self.config, core, self.sources)
        source = self.sources[0]
        journal = ctl.Journal(self.root, core)
        pending, _ = ctl.prepare_tasks([source], core, journal)
        task = pending[0]
        gate(task)
        cell = list(task.cell)
        cell[2] = (b'%PDF-tampered', task.cell[2][1])
        with self.assertRaises(ValueError):
            gate.check_task(ctl.Task(source, tuple(cell), task.fingerprint))
        target = self.root / 'records/run_pilot.py'
        raw = target.read_bytes()
        try:
            target.write_bytes(raw + b'\n# changed\n')
            with self.assertRaises(ValueError):
                gate(task)
        finally:
            target.write_bytes(raw)
        freeze_path = self.root / 'records/freeze_verification.json'
        freeze_bytes = freeze_path.read_bytes()
        try:
            freeze_path.unlink()
            with self.assertRaises(FileNotFoundError):
                ctl.FrozenGate(self.root, self.config, core, self.sources)
        finally:
            freeze_path.write_bytes(freeze_bytes)

    def test_03_canary_10_successes_full_resume_3590_pending(self):
        sources = ctl.plan_sources(self.root, self.config, core, canary=True)
        self.assertEqual(len(sources), 10)
        controller = make_controller(self.root, sources, levels=(96,64,48,32), mode='canary')
        self.assertEqual(len(controller.pending), 10)
        with patch.object(core, 'call_codex', side_effect=fake_call), redirect_stdout(io.StringIO()):
            result = controller.run_pool()
        self.assertEqual(result['completed'], 10)
        self.assertEqual(result['mode'], 'canary')
        self.assertEqual(result['study_planned'], 3600)
        self.assertFalse(result['study_complete'])
        full = ctl.plan_sources(self.root, self.config, core)
        journal = ctl.Journal(self.root, core)
        journal.recover(full)
        pending, counts = ctl.prepare_tasks(full, core, journal)
        self.assertEqual(len(pending), 3590)
        self.assertEqual(sum(c['skipped'] for c in counts.values()), 10)
        self.assertEqual(len({r['fingerprint'] for r in all_reviews(full)}), 10)


    def test_04_frozen_checks_unchanged_across_canary_full_main(self):
        checks = self.root / 'records/controller-checks.json'
        before = checks.read_bytes()
        with patch.object(ctl, 'load_runner', return_value=core), \
             patch.object(ctl, 'pause_reason', return_value=None), \
             patch.object(ctl.Controller, 'run_pool', return_value={'status': 'completed'}), \
             patch.object(core, 'call_codex', side_effect=AssertionError('must not invoke')), \
             redirect_stdout(io.StringIO()):
            for extra in (['--canary'], []):
                status = ctl.main(['--config', str(self.root/'pilot_config.json'), *extra])
                self.assertEqual(status, 0)
                self.assertEqual(checks.read_bytes(), before)
                ctl.FrozenGate(self.root, self.config, core, self.sources).verify()



if __name__ == '__main__':
    program = unittest.main(exit=False, verbosity=2)
    results = program.result
    report = {'status': 'passed' if results.wasSuccessful() else 'failed', 'checked_at': ctl.now(),
              'suite': 'controller offline tests', 'tests_run': results.testsRun,
              'failures': len(results.failures), 'errors': len(results.errors),
              'model_calls_started': 0, 'network_calls': 0, 'gpu_requested': 0,
              'slurm_job_id': os.environ.get('SLURM_JOB_ID'),
              'controller_sha256': TESTED_CONTROLLER_SHA256,
              'source_revision': CONFIG['source_revision']}
    ctl.atomic_json(RUN/'records/controller-checks.json', report)
    raise SystemExit(0 if results.wasSuccessful() else 1)

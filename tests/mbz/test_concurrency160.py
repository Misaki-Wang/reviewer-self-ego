"""Offline continuity and admission guards for an authorized runtime cap amendment."""
import unittest
from unittest import mock
from tests.mbz import test_local_batch as scheduler_fixture
from tests.mbz import test_verify_local_batch as verifier_fixture
from utility import local_batch as batch

LADDER = [160, 128, 96, 80, 64, 48]

class Concurrency160Tests(unittest.TestCase):
    def fixture(self, cls):
        case = cls(methodName='runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_resume_keeps_completed_receipts_results_and_usage(self):
        case = self.fixture(scheduler_fixture.BatchTests)
        config = batch.read(case.run/'run_config.json')
        config.update(initial_workers=96, max_workers=96, concurrency_ladder=[96,80,64,48])
        batch.save(case.run/'run_config.json', config)
        first = case.invoke()
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        saved = {p:p.read_bytes() for folder in ('attempts','output') for p in (case.run/folder).rglob('*') if p.is_file()}
        contract = (case.run/'execution_contract.json').read_bytes()
        usage = batch.read(case.run/'token_usage.json')['token_usage']
        config.update(max_workers=160, concurrency_ladder=LADDER)
        batch.save(case.run/'run_config.json',config)
        control = batch.read(case.run/'control.json'); control['workers']=160
        batch.save(case.run/'control.json',control)
        resumed = case.invoke()
        self.assertEqual(resumed.returncode,0,resumed.stdout+resumed.stderr)
        self.assertEqual((case.run/'execution_contract.json').read_bytes(),contract)
        self.assertEqual(batch.read(case.run/'token_usage.json')['token_usage'],usage)
        self.assertEqual({p:p.read_bytes() for folder in ('attempts','output') for p in (case.run/folder).rglob('*') if p.is_file()},saved)

    def test_independent_verifier_accepts_amendment_with_same_scientific_signature(self):
        case = self.fixture(verifier_fixture.VerifierTests)
        case.config.update(initial_workers=96,max_workers=96,concurrency_ladder=[96,80,64,48])
        case.freeze(); case.complete_all()
        prior = case.audit(); self.assertEqual(prior['status'],'passed',prior['errors'])
        before = {p:p.read_bytes() for p in case.run.rglob('*') if p.is_file() and p.name!='run_config.json'}
        amended = {**case.config,'max_workers':160,'concurrency_ladder':LADDER}
        batch.save(case.run/'run_config.json',amended)
        after = case.audit(); self.assertEqual(after['status'],'passed',after['errors'])
        self.assertEqual(after['token_usage'],prior['token_usage'])
        self.assertEqual(after['coverage'],prior['coverage'])
        self.assertTrue(all(p.read_bytes()==value for p,value in before.items()))

    def test_pressure_ladder_never_cancels_active_calls(self):
        case = self.fixture(scheduler_fixture.BatchTests)
        config=batch.read(case.run/'run_config.json');config.update(max_workers=160,concurrency_ladder=LADDER)
        batch.save(case.run/'run_config.json',config)
        batch.save(case.run/'control.json',{'workers':160,'auto_scale':True,'pause_dispatch':False})
        with mock.patch.object(batch.time,'monotonic',return_value=1000) as clock, mock.patch.object(batch,'memory_observation',return_value={'free_percent':80}):
            controller=batch.Batch(case.run)
            sentinel=mock.Mock();controller.active={sentinel:{}}
            with mock.patch.object(controller.runner,'terminate') as terminate:
                for i,target in enumerate(LADDER[1:],1):
                    at=1000+i*601;clock.return_value=at
                    controller.latest_outcomes=[batch.attempt_observation({'status':'failed','error':'Connection reset by peer','started_monotonic':at-1},at) for _ in range(3)]
                    self.assertEqual(controller.scale(batch.read(case.run/'control.json'))['workers'],target)
                    self.assertEqual(list(controller.active),[sentinel])
                terminate.assert_not_called();sentinel.cancel.assert_not_called()

    def test_admissions_above_config_cap_fail_before_model_call(self):
        case=self.fixture(scheduler_fixture.BatchTests)
        config=batch.read(case.run/'run_config.json');config.update(max_workers=160,concurrency_ladder=LADDER)
        batch.save(case.run/'run_config.json',config)
        control=batch.read(case.run/'control.json');control['workers']=161
        batch.save(case.run/'control.json',control)
        failed=case.invoke()
        self.assertNotEqual(failed.returncode,0)
        self.assertIn('Invalid live comparison concurrency',failed.stdout+failed.stderr)
        self.assertFalse((case.run/'attempts').exists())

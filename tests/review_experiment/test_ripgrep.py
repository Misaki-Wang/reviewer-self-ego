"""Offline ripgrep exposure, preflight and resume tests; the model CLI is fake."""
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from review_experiment import runner
from . import test_pipeline


class RipgrepTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pipeline.PipelineTests('test_dry_run_has_no_reads_or_results')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def rg(self, directory='tools', text='ripgrep fixture-v1', executable=True, status=0):
        path = self.fixture.root / directory / 'rg'
        path.parent.mkdir(exist_ok=True)
        path.write_text(f'#!{sys.executable}\nprint({text!r})\nraise SystemExit({status})\n')
        path.chmod(0o755 if executable else 0o644)
        return path

    def run_cell(self, path, *extra):
        return self.fixture.run_cli('--condition', 'exp1_quality', '--version', '1',
                                    '--ripgrep', str(path), *extra)

    def test_controlled_path_and_exact_file_permission(self):
        rg = self.rg()
        cli = self.fixture.fake_cli()
        work = self.fixture.root / 'work'
        work.mkdir()
        snapshot = self.fixture.root / 'snapshot.pdf'
        snapshot.write_bytes(b'%PDF-fixture')
        args = SimpleNamespace(codex=str(cli), model='fixture', effort='low',
                               pdf_skill=self.fixture.skill, ripgrep=rg)
        command = runner.build_command(args, work, 'fixture', snapshot)
        filesystem = next(s for s in command if s.startswith('permissions.review_only.filesystem='))
        self.assertIn(json.dumps(str(rg.resolve())) + '="read"', filesystem)
        self.assertNotIn(json.dumps(str(rg.parent)) + '="read"', filesystem)
        self.assertIn('":root"="deny"', filesystem)
        settings = next(s for s in command if s.startswith('shell_environment_policy.set='))
        path = json.JSONDecoder().raw_decode(settings.split('"PATH"=', 1)[1])[0]
        self.assertEqual(path.split(':')[0], str(rg.parent))

    def test_tool_hash_is_recorded_and_changes_invalidate_resume(self):
        rg = self.rg()
        self.assertEqual(self.run_cell(rg), 0)
        first = self.fixture.result()['versions']['v1']['reviews']['exp1_quality/r1']
        receipt = first['attempts'][-1]['ripgrep']
        self.assertEqual(receipt['path'], str(rg.resolve()))
        self.assertEqual(receipt['sha256'], hashlib.sha256(rg.read_bytes()).hexdigest())
        self.assertEqual(receipt['version'], 'ripgrep fixture-v1')
        self.assertEqual(self.run_cell(rg), 0)
        self.assertEqual(len(self.fixture.count.read_text().splitlines()), 1)
        rg = self.rg(text='ripgrep fixture-v2')
        self.assertEqual(self.run_cell(rg), 0)
        changed = self.fixture.result()['versions']['v1']['reviews']['exp1_quality/r1']
        self.assertEqual(len(self.fixture.count.read_text().splitlines()), 2)
        self.assertNotEqual(first['fingerprint'], changed['fingerprint'])
        self.assertEqual(changed['attempts'][-1]['ripgrep']['version'], 'ripgrep fixture-v2')

    def test_invalid_explicit_tool_fails_before_model_or_output(self):
        wrong_name = self.rg('wrong').rename(self.fixture.root / 'wrong/not-rg')
        cases = [self.fixture.root / 'missing/rg', wrong_name,
                 self.rg('not-executable', executable=False),
                 self.rg('nonzero', status=1),
                 self.rg('wrong-version', text='another program')]
        for path in cases:
            with self.subTest(path=path), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.run_cell(path)
                self.assertEqual(error.exception.code, 1)
                self.assertFalse(self.fixture.count.exists())
                self.assertFalse(self.fixture.output.exists())

    def test_dry_run_does_not_execute_tool_preflight(self):
        with patch.object(runner.subprocess, 'run', side_effect=AssertionError('Tool executed')):
            self.assertEqual(self.run_cell(self.fixture.root / 'missing/rg', '--dry-run'), 0)
        self.assertFalse(self.fixture.count.exists())
        self.assertFalse(self.fixture.output.exists())


if __name__ == '__main__':
    unittest.main()

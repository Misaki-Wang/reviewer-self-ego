"""Grouped live CLI entrypoints; offline help/import checks only."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from utility import workflow

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = ('data.download_papers', 'data.prepare_source_data', 'data.build_pairs',
           'batch.prepare_batch', 'batch.local_batch', 'batch.verify_local_batch',
           'batch.result_reuse', 'workflow')


class UtilityLayoutTests(unittest.TestCase):
    def test_public_workflow_loads_grouped_live_preparer(self):
        module = workflow.local_module('prepare_batch')
        self.assertEqual(Path(module.__file__).resolve(), ROOT / 'utility/batch/prepare_batch.py')
        self.assertEqual(Path(module.preparation.__file__).resolve(), ROOT / 'utility/data/prepare_source_data.py')

    def test_package_cli_help(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                result = subprocess.run([sys.executable, '-B', '-m', 'utility.' + entry, '--help'],
                                        cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_direct_cli_help_outside_project(self):
        environment = dict(os.environ)
        environment['PYTHONPATH'] = os.pathsep.join(value for value in environment.get('PYTHONPATH', '').split(os.pathsep)
                                                   if value and Path(value).resolve() != ROOT)
        with tempfile.TemporaryDirectory() as outside:
            for entry in ENTRIES:
                with self.subTest(entry=entry):
                    script = ROOT / 'utility' / (entry.replace('.', '/') + '.py')
                    result = subprocess.run([sys.executable, '-B', str(script), '--help'],
                                            cwd=outside, env=environment, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)

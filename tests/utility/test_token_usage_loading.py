"""Shared helper resolution for package, script and immutable snapshot callers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from check_diff import runner as compare
from review_experiment import runner as review
from utility import token_usage

ROOT = Path(__file__).resolve().parents[2]


class SharedTokenLoadingTests(unittest.TestCase):
    def test_both_runners_resolve_the_canonical_shared_file(self):
        target = ROOT / 'utility/token_usage.py'
        for module in (compare.token_usage, review.token_usage, token_usage):
            self.assertEqual(Path(module.__file__).resolve(), target)
            self.assertEqual(module.summarize([]), token_usage.summarize([]))

    def test_direct_script_entrypoints_work_outside_the_project(self):
        with tempfile.TemporaryDirectory() as name:
            for module in ('check_diff', 'review_experiment'):
                result = subprocess.run([sys.executable, '-B', str(ROOT / module / 'runner.py'), '--help'],
                                        cwd=name, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_frozen_runner_ignores_live_packages_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as name:
            snapshot = Path(name).resolve() / 'source_snapshot'
            (snapshot / 'check_diff').mkdir(parents=True)
            (snapshot / 'utility').mkdir()
            for filename in ('runner.py', 'schema.py'):
                shutil.copyfile(ROOT / 'check_diff' / filename, snapshot / 'check_diff' / filename)
            helper = snapshot / 'utility/token_usage.py'
            helper.write_bytes((ROOT / 'utility/token_usage.py').read_bytes() + b"\nORIGIN = 'frozen'\n")
            script = r"""
import importlib.util, pathlib, sys, types
from utility import token_usage as live
live.ORIGIN = 'live'
live.summarize = lambda attempts: {'wrong_live_helper': True}
sys.modules['token_usage'] = live
snapshot = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(snapshot / 'check_diff'))
def load(name):
    spec = importlib.util.spec_from_file_location(name, snapshot / 'check_diff/runner.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
runner = load('frozen_token_runner')
assert runner.token_usage.ORIGIN == 'frozen'
assert runner.token_usage.summarize([])['status'] == 'not_run'
assert live.ORIGIN == 'live'
(snapshot / 'utility/token_usage.py').unlink()
try:
    load('missing_frozen_token_runner')
except FileNotFoundError:
    pass
else:
    raise AssertionError('Missing snapshot helper fell back to a live package')
print('frozen isolation passed')
"""
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([sys.executable, '-B', '-c', script, str(snapshot)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('frozen isolation passed', result.stdout)

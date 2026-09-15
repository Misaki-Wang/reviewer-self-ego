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
from utility.codex import token_usage, codex_isolation

ROOT = Path(__file__).resolve().parents[2]


class SharedTokenLoadingTests(unittest.TestCase):
    def test_both_runners_resolve_the_canonical_shared_file(self):
        target = ROOT / 'utility/codex/token_usage.py'
        for module in (compare.token_usage, review.token_usage, token_usage):
            self.assertEqual(Path(module.__file__).resolve(), target)
            self.assertEqual(module.summarize([]), token_usage.summarize([]))

    def test_both_runners_resolve_canonical_isolation(self):
        target = ROOT / 'utility/codex/codex_isolation.py'
        for module in (compare.isolation, review.isolation, codex_isolation):
            self.assertEqual(Path(module.__file__).resolve(), target)
            self.assertEqual(module.identity(), codex_isolation.identity())

    def test_direct_script_entrypoints_work_outside_the_project(self):
        with tempfile.TemporaryDirectory() as name:
            for module in ('check_diff', 'review_experiment'):
                result = subprocess.run([sys.executable, '-B', str(ROOT / module / 'runner.py'), '--help'],
                                        cwd=name, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_frozen_runners_ignore_live_packages_and_never_fall_back(self):
        for layout in ('utility', 'utility/codex'):
            for runner_name in ('check_diff', 'review_experiment'):
                with self.subTest(layout=layout, runner=runner_name), tempfile.TemporaryDirectory() as name:
                    snapshot = Path(name).resolve() / 'source_snapshot'
                    runner_dir = snapshot / runner_name
                    runner_dir.mkdir(parents=True)
                    helpers = snapshot / layout
                    helpers.mkdir(parents=True)
                    files = ('runner.py', '__init__.py',
                             'schema.py' if runner_name == 'check_diff' else 'source_profiles.py')
                    for filename in files:
                        shutil.copyfile(ROOT / runner_name / filename, runner_dir / filename)
                    for filename in ('codex_isolation.py', 'token_usage.py'):
                        (helpers / filename).write_bytes(
                            (ROOT / 'utility/codex' / filename).read_bytes() + b"\nORIGIN = 'frozen'\n")
                        if layout == 'utility/codex':
                            (snapshot / 'utility' / filename).write_text(
                                "raise AssertionError('Nested helper fell back to flat copy')\n")
                    script = r"""
import importlib.util, pathlib, sys
from utility.codex import token_usage as live, codex_isolation as live_isolation
for name, module in [('token_usage', live), ('codex_isolation', live_isolation)]:
    module.ORIGIN = 'live'
    for prefix in ('', 'utility.', 'utility.codex.'):
        sys.modules[prefix + name] = module
live.summarize = lambda attempts: {'wrong_live_helper': True}
snapshot = pathlib.Path(sys.argv[1])
runner_name, layout = sys.argv[2:]
helpers = snapshot / layout
sys.path.insert(0, str(snapshot / runner_name))
def load(name):
    spec = importlib.util.spec_from_file_location(name, snapshot / runner_name / 'runner.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
runner = load('frozen_token_runner')
assert runner.token_usage.ORIGIN == runner.isolation.ORIGIN == 'frozen'
assert pathlib.Path(runner.token_usage.__file__) == helpers / 'token_usage.py'
assert pathlib.Path(runner.isolation.__file__) == helpers / 'codex_isolation.py'
assert runner.token_usage.summarize([])['status'] == 'not_run'
assert live.ORIGIN == live_isolation.ORIGIN == 'live'
for filename in ('codex_isolation.py', 'token_usage.py'):
    path = helpers / filename
    raw = path.read_bytes()
    path.unlink()
    try:
        load('missing_' + path.stem + '_runner')
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('Missing snapshot helper fell back to another source')
    finally:
        path.write_bytes(raw)
print('frozen isolation passed')
"""
                    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
                    result = subprocess.run([sys.executable, '-B', '-c', script, str(snapshot),
                                             runner_name, layout], cwd=ROOT, env=env,
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('frozen isolation passed', result.stdout)

"""Package layout regressions; CLI help and imports only, never model calls."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from utility.anonymize import anonymize_pdf, anonymize_pdf_rules

ROOT = Path(__file__).resolve().parents[2]


class AnonymizeLayoutTests(unittest.TestCase):
    def test_project_roots_and_shared_helpers_remain_correct(self):
        self.assertEqual(anonymize_pdf.ROOT, ROOT)
        self.assertEqual(anonymize_pdf_rules.ROOT, ROOT)
        self.assertEqual(Path(anonymize_pdf.token_usage.__file__).resolve(),
                         ROOT / 'utility/codex/token_usage.py')
        self.assertEqual(Path(anonymize_pdf.isolation.__file__).resolve(),
                         ROOT / 'utility/codex/codex_isolation.py')

    def test_package_cli_entrypoints(self):
        for name in ('anonymize_pdf', 'anonymize_pdf_rules'):
            result = subprocess.run([sys.executable, '-B', '-m',
                                     'utility.anonymize.' + name, '--help'],
                                    cwd=ROOT, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--output', result.stdout)
            self.assertIn('--dry-run', result.stdout)

    def test_direct_cli_entrypoints_outside_project(self):
        environment = dict(os.environ)
        environment.pop('PYTHONPATH', None)
        with tempfile.TemporaryDirectory() as outside:
            for name in ('anonymize_pdf.py', 'anonymize_pdf_rules.py'):
                result = subprocess.run([sys.executable, '-B',
                                         str(ROOT / 'utility/anonymize' / name), '--help'],
                                        cwd=outside, env=environment, capture_output=True,
                                        text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('--output', result.stdout)

    def test_frozen_helpers_stay_bound_to_snapshot_in_both_layouts(self):
        for layout in ('utility', 'utility/codex'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                snapshot = Path(directory).resolve() / 'source_snapshot'
                package = snapshot / 'utility/anonymize'
                package.mkdir(parents=True)
                shutil.copyfile(ROOT / 'utility/anonymize/anonymize_pdf.py', package / 'anonymize_pdf.py')
                helpers = snapshot / layout
                helpers.mkdir(parents=True, exist_ok=True)
                for filename in ('token_usage.py', 'codex_isolation.py'):
                    (helpers / filename).write_bytes(
                        (ROOT / 'utility/codex' / filename).read_bytes() + b"\nORIGIN = 'frozen'\n")
                    if layout == 'utility/codex':
                        (snapshot / 'utility' / filename).write_text(
                            "raise AssertionError('Nested helper fell back to flat copy')\n")
                code = """
import importlib.util, pathlib, sys, types
snapshot = pathlib.Path(sys.argv[1])
helpers = snapshot / sys.argv[2]
for name in ('token_usage', 'codex_isolation'):
    live = types.ModuleType(name)
    live.ORIGIN = 'live'
    for prefix in ('', 'utility.', 'utility.codex.'):
        sys.modules[prefix + name] = live
def load():
    spec = importlib.util.spec_from_file_location('frozen_anonymizer', snapshot / 'utility/anonymize/anonymize_pdf.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
module = load()
assert module.ROOT == snapshot
assert module.isolation.ORIGIN == module.token_usage.ORIGIN == 'frozen'
assert pathlib.Path(module.isolation.__file__) == helpers / 'codex_isolation.py'
assert pathlib.Path(module.token_usage.__file__) == helpers / 'token_usage.py'
for filename in ('codex_isolation.py', 'token_usage.py'):
    path = helpers / filename
    raw = path.read_bytes()
    path.unlink()
    try:
        load()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('Missing frozen helper fell back to another source')
    finally:
        path.write_bytes(raw)
"""
                result = subprocess.run([sys.executable, '-B', '-c', code, str(snapshot), layout],
                                        cwd=directory, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)

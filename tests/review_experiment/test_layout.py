"""Formal-layout I/O regressions use only temporary files and the existing fake CLI."""
from contextlib import redirect_stderr
import importlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from review_experiment import runner
from . import test_pipeline


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pipeline.PipelineTests('test_dry_run_has_no_reads_or_results')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_cli_requires_explicit_input_and_output(self):
        for argv in (['--dry-run'], [str(self.fixture.input), '--dry-run']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    runner.main(argv)
                self.assertEqual(error.exception.code, 2)

    def assert_output_rejected(self, output, message, *extra, before_discovery=True):
        with redirect_stderr(io.StringIO()) as errors, \
             patch.object(runner, 'call_codex', side_effect=AssertionError('model invoked')):
            if before_discovery:
                with patch.object(runner, 'discover', side_effect=AssertionError('input accessed')):
                    with self.assertRaises(SystemExit) as error:
                        runner.main([str(self.fixture.input), '--output', str(output), *extra])
            else:
                with self.assertRaises(SystemExit) as error:
                    runner.main([str(self.fixture.input), '--output', str(output), *extra])
        self.assertEqual(error.exception.code, 1)
        self.assertIn(message, errors.getvalue())

    def test_protected_outputs_are_rejected_even_with_overwrite(self):
        project = self.fixture.root / 'formal-project'
        archive = project / 'res/archive'
        archive.mkdir(parents=True)
        marker = archive / 'fixture.json'
        marker.write_bytes(b'fake archived evidence')
        protected = [project, project / 'res', archive, archive / 'new-run',
                     project / 'res/.migration', project / 'res/.migration/new-run']
        protected += [project / name / 'new-run'
                      for name in ('review_experiment', 'check_diff', 'utility', 'tests', 'data', 'html', '.git')]
        with patch.object(runner, 'ROOT', project):
            for output in protected:
                for extra in ((), ('--overwrite', '--dry-run')):
                    with self.subTest(output=output, extra=extra):
                        self.assert_output_rejected(output, 'protected', *extra)
        self.assertEqual(marker.read_bytes(), b'fake archived evidence')
        self.assertEqual(list(archive.iterdir()), [marker])
        self.assertFalse((project / 'res/.migration').exists())

    def test_output_symlinks_and_files_are_rejected(self):
        root = self.fixture.root
        directory = root / 'target'
        directory.mkdir()
        file = root / 'file-output'
        file.write_bytes(b'preserve existing file')
        link = root / 'link-output'
        link.symlink_to(directory, target_is_directory=True)
        broken = root / 'broken-output'
        broken.symlink_to(root / 'missing-target')
        for output in (link, broken, link / 'new-run', broken / 'new-run'):
            with self.subTest(output=output):
                self.assert_output_rejected(output, 'symlink', '--dry-run')
        self.assert_output_rejected(file, 'directory', '--dry-run')
        self.assertEqual(file.read_bytes(), b'preserve existing file')
        self.assertTrue(broken.is_symlink())
        self.assertEqual(list(directory.iterdir()), [])

    def test_input_and_output_must_not_overlap(self):
        for output in (self.fixture.input, self.fixture.input / 'nested-output', self.fixture.root):
            with self.subTest(output=output):
                self.assert_output_rejected(output, 'overlap', '--overwrite', '--dry-run')
        link = self.fixture.root / 'input-link'
        link.symlink_to(self.fixture.input, target_is_directory=True)
        with patch.object(self.fixture, 'input', link):
            self.assert_output_rejected(self.fixture.root / 'input_data/nested-output',
                                        'overlap', '--dry-run')

    def test_resumed_write_targets_cannot_be_symlinks(self):
        self.fixture.output.mkdir()
        outside = self.fixture.root / 'untouched'
        outside.mkdir()
        marker = outside / 'fixture.json'
        marker.write_bytes(b'fake protected evidence')
        for relative in ('.runner.lock', 'run_summary.json', '2406.03862', '2406.03862/reviews.json'):
            target = self.fixture.output / relative
            target.parent.mkdir(exist_ok=True)
            is_directory = relative == '2406.03862'
            target.symlink_to(outside if is_directory else marker, target_is_directory=is_directory)
            with self.subTest(relative=relative):
                self.assert_output_rejected(self.fixture.output, 'symlink', '--overwrite',
                                            '--dry-run', before_discovery=False)
            target.unlink()
        self.assertEqual(marker.read_bytes(), b'fake protected evidence')
        self.assertEqual(list(outside.iterdir()), [marker])

    def test_source_and_test_packages_remain_distinct(self):
        source = importlib.import_module('review_experiment')
        tests = importlib.import_module('tests.review_experiment')
        self.assertEqual(Path(source.__file__).resolve().parent, runner.HERE)
        self.assertEqual(Path(tests.__file__).resolve().parent, Path(__file__).resolve().parent)
        self.assertIsNot(source, tests)

    def test_sandbox_probe_import_never_launches_codex(self):
        with patch('subprocess.run', side_effect=AssertionError('real sandbox invoked')):
            probe = importlib.import_module('tests.review_experiment.sandbox_smoke')
        self.assertIs(probe.permission_options, runner.permission_options)

    def test_fake_cli_scratch_is_inside_selected_output(self):
        original = tempfile.TemporaryDirectory
        directories = []
        def record(*args, **kwargs):
            directories.append(kwargs.get('dir'))
            return original(*args, **kwargs)
        with patch.object(runner.tempfile, 'TemporaryDirectory', side_effect=record):
            self.assertEqual(self.fixture.run_cli('--condition', 'exp1_quality', '--version', '1'), 0)
        self.assertEqual(directories, [self.fixture.output])
        self.assertEqual(list(self.fixture.output.glob('review-experiment-*')), [])


if __name__ == '__main__':
    unittest.main()

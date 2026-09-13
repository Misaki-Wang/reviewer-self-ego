"""Explicit comparison I/O paths without invoking a model."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from check_diff import runner

class CLIPathTests(unittest.TestCase):
    def test_missing_input_or_output_is_rejected_before_reading_data(self):
        for argv in (['--dry-run'], ['/fixture/input', '--dry-run']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with mock.patch.object(runner, 'load_pairs', side_effect=AssertionError('Unexpected data read')):
                    with self.assertRaises(SystemExit) as error:
                        runner.main(argv)
                    self.assertEqual(error.exception.code, 2)

    def test_default_background_log_is_inside_explicit_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            with mock.patch.object(runner, 'load_pairs', return_value=[]), mock.patch.object(runner, 'start_background', return_value=0) as start:
                self.assertEqual(runner.main([str(root / 'prepared'), '--output', str(root / 'result'), '--background']), 0)
            self.assertEqual(start.call_args.args[0].log_file, root / 'result/runner.log')

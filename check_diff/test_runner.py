"""Check direct PDF input, simple outputs, CLI execution, resume, and failures."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import runner
from schema import COMPARABILITY, DIMENSIONS, EMPIRICAL_RESULTS, LABELS, validate_result


def annotation():
    return {
        'overall': {
            'A1': {'value': 'comparable', 'justification': 'Both versions are readable and describe the same work.'},
            'A2': {side: {'value': 'yes', 'justification': 'Reports original experimental results.'}
                   for side in ('before', 'after')},
        },
        **{d: {'change': 'no change', 'justification': 'The content is unchanged.'} for d in DIMENSIONS},
    }


class CheckDiffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.input = self.root / 'input_data'
        self.pair = self.make_pair('2406.03862', 1, 2)
        self.args = SimpleNamespace(output=self.root / 'output', model='gpt-5.5', effort='medium',
                                    codex='codex', timeout=5, retries=0, overwrite=False, workers=2)
        runner.STOP.clear()
        self.addCleanup(runner.STOP.clear)

    def make_pair(self, aid, before, after):
        folder = self.input / aid / f'v{before}_v{after}'
        folder.mkdir(parents=True)
        pair = runner.Pair(folder, aid, before, after)
        for pdf in pair.pdfs:
            pdf.write_bytes(b'%PDF-fixture')
        return pair

    def fake_cli(self, response=None, delay=0, returncode=0):
        code = self.root / 'fake_codex'
        code.write_text(f'''#!{sys.executable}
import json, pathlib, sys, time
args = sys.argv[1:]
assert args[0] == 'exec'
assert args[args.index('--model') + 1] == 'gpt-5.5'
assert 'model_reasoning_effort="medium"' in args
assert '--image' not in args
prompt = sys.stdin.read()
assert 'Earlier version: `v1.pdf`' in prompt and 'Later version: `v2.pdf`' in prompt
assert 'Comparison direction: `v1.pdf -> v2.pdf`.' in prompt
work = pathlib.Path(args[args.index('--cd') + 1])
(work / 'model_scratch.txt').write_text('Temporary reading artifact')
time.sleep({delay})
if {returncode}:
    print('Simulated CLI failure', file=sys.stderr)
    sys.exit({returncode})
pathlib.Path(args[args.index('--output-last-message') + 1]).write_text({json.dumps(response or annotation(), ensure_ascii=False)!r})
''')
        code.chmod(0o755)
        self.args.codex = str(code)

    def quiet_run(self, pair=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return runner.run_pair(self.args, pair or self.pair)

    def test_direct_discovery_without_metadata_and_numeric_order(self):
        self.make_pair('2406.03862', 9, 10)
        self.make_pair('2406.03862', 2, 3)
        other = self.make_pair('2501.00001', 1, 2)
        self.assertEqual([(p.before, p.after) for p in runner.load_pairs(self.input, ['2406.03862'])],
                         [(1, 2), (2, 3), (9, 10)])
        self.assertEqual(runner.load_pairs(other.folder), [other])
        self.assertEqual(len(runner.load_pairs(self.input, limit=1)), 1)
        with self.assertRaisesRegex(ValueError, 'Unknown paper'):
            runner.load_pairs(self.input, ['9999.99999'])

    def test_missing_pdf_fails_before_cli(self):
        self.pair.pdfs[1].unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or empty PDF'):
            runner.load_pairs(self.input)

    def test_all_labels_and_short_multisentence_justification(self):
        for label in LABELS:
            result = annotation()
            result['M1'] = {'change': label, 'justification': 'Adds a gating component. The loss is unchanged.'}
            validate_result(result)
        bad = []
        result = annotation(); result.pop('M1'); bad.append(result)
        result = annotation(); result['pair_id'] = 'old'; bad.append(result)
        result = annotation(); result['E1']['change'] = 'changed'; bad.append(result)
        result = annotation(); result['E1']['justification'] = ' '; bad.append(result)
        result = annotation(); result['E1']['evidence'] = []; bad.append(result)
        for result in bad:
            with self.assertRaises(ValueError):
                validate_result(result)

    def test_a1_and_both_a2_results_are_required(self):
        for value in COMPARABILITY:
            result = annotation()
            result['overall']['A1']['value'] = value
            validate_result(result)
        for side in ('before', 'after'):
            for value in EMPIRICAL_RESULTS:
                result = annotation()
                result['overall']['A2'][side]['value'] = value
                validate_result(result)
        bad = []
        result = annotation(); result.pop('overall'); bad.append(result)
        result = annotation(); result['overall']['A1']['value'] = 'Major change'; bad.append(result)
        result = annotation(); result['overall'].pop('A1'); bad.append(result)
        result = annotation(); result['overall'].pop('A2'); bad.append(result)
        result = annotation(); result['overall']['A2'].pop('after'); bad.append(result)
        result = annotation(); result['overall']['A1']['value'] = 'yes'; bad.append(result)
        result = annotation(); result['overall']['A2']['before']['value'] = 'no change'; bad.append(result)
        result = annotation(); result['overall']['A2']['after']['justification'] = ''; bad.append(result)
        for result in bad:
            with self.assertRaises(ValueError):
                validate_result(result)

    def test_cli_creates_only_one_result_and_cleans_scratch(self):
        self.fake_cli()
        scratch = self.root / 'scratch'
        scratch.mkdir()
        with patch.object(tempfile, 'tempdir', str(scratch)):
            self.assertEqual(self.quiet_run(), 'done')
        self.assertEqual(list(scratch.iterdir()), [])
        output = self.pair.output_path(self.args.output)
        self.assertEqual(output.relative_to(self.args.output).as_posix(), '2406.03862/diff_v1_v2.json')
        self.assertEqual(list(p for p in self.args.output.rglob('*') if p.is_file()), [output])
        self.assertEqual(json.loads(output.read_text()), annotation())
        self.assertEqual(next(iter(json.loads(output.read_text()))), 'overall')

    def test_resume_skips_cli_and_overwrite_recomputes(self):
        runner.save_result(self.pair.output_path(self.args.output), annotation())
        with patch.object(runner, 'compare_pair', return_value=annotation()) as call:
            self.assertEqual(self.quiet_run(), 'skipped')
            call.assert_not_called()
            self.args.overwrite = True
            self.assertEqual(self.quiet_run(), 'done')
            call.assert_called_once()

    def test_invalid_response_leaves_no_output_and_retries(self):
        result = annotation(); result['E1']['change'] = 'invalid'
        self.fake_cli(result)
        self.assertEqual(self.quiet_run(), 'failed')
        self.assertFalse(self.args.output.exists())
        self.args.retries = 1
        with patch.object(runner, 'compare_pair', side_effect=[ValueError('bad JSON'), annotation()]) as call:
            self.assertEqual(self.quiet_run(), 'done')
            self.assertEqual(call.call_count, 2)

    def test_invalid_existing_result_is_rebuilt(self):
        path = self.pair.output_path(self.args.output)
        path.parent.mkdir(parents=True)
        path.write_text('{"old": true}')
        self.fake_cli()
        self.assertEqual(self.quiet_run(), 'done')
        validate_result(json.loads(path.read_text()))

    def test_cli_failure_and_timeout_leave_no_output(self):
        for delay, code in [(0, 2), (5, 0)]:
            self.fake_cli(delay=delay, returncode=code)
            self.args.timeout = 0.1
            self.assertEqual(self.quiet_run(), 'failed')
            self.assertFalse(self.args.output.exists())
            self.assertFalse(runner.ACTIVE_PROCESSES)

    def test_stop_keeps_completed_result(self):
        result = annotation()
        path = self.pair.output_path(self.args.output)
        runner.save_result(path, result)
        runner.STOP.set()
        self.args.overwrite = True
        with patch.object(runner, 'compare_pair') as call:
            self.assertEqual(self.quiet_run(), 'stopped')
            call.assert_not_called()
        self.assertEqual(json.loads(path.read_text()), result)

    def test_prompt_passes_pdf_paths_and_independent_rubric(self):
        prompt = runner.make_prompt(self.pair)
        for pdf in self.pair.pdfs:
            self.assertIn(str(pdf), prompt)
        for dimension in ('A1', 'A2', *DIMENSIONS):
            self.assertIn(f'## {dimension} —', prompt)
        self.assertIn('concise English justifications', prompt)
        for removed in ['html', 'Chinese', '30–80', 'coverage records', 'change_ids', 'pypdf', 'pdftoppm']:
            self.assertNotIn(removed, prompt)

    def test_prompt_names_follow_the_selected_pair(self):
        pair = self.make_pair('2406.03862', 2, 3)
        prompt = runner.make_prompt(pair)
        self.assertIn('Earlier version: `v2.pdf`', prompt)
        self.assertIn('Later version: `v3.pdf`', prompt)
        self.assertIn('Comparison direction: `v2.pdf -> v3.pdf`.', prompt)
        self.assertIn('`before` refers to `v2.pdf` and `after` refers to `v3.pdf`', prompt)
        self.assertNotIn('v1.pdf', prompt)
        for pdf in pair.pdfs:
            self.assertIn(str(pdf.resolve()), prompt)
        self.assertNotIn('{before_filename}', prompt)
        self.assertNotIn('{after_path}', prompt)

    def test_task_state_and_pdf_skill_are_isolated(self):
        source = self.root / 'source_cli'
        source.mkdir()
        auth = source / 'auth.json'
        auth.write_text('fixture credential, not a real token')
        skill = source / 'skills/pdf/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('PDF skill fixture')
        original = dict(os.environ)
        with patch.dict(os.environ, {'CODEX_HOME': str(source), 'CODEX_THREAD_ID': 'parent',
                                    'CODEX_SESSION_ID': 'parent', 'CODEX_APP_TOOLS_PIPE_PATH': 'parent'}):
            first = runner.task_environment(self.root / 'state1')
            second = runner.task_environment(self.root / 'state2')
            self.assertEqual(os.environ['CODEX_HOME'], str(source))
        self.assertEqual(dict(os.environ), original)
        self.assertNotEqual(first['CODEX_HOME'], second['CODEX_HOME'])
        for env in (first, second):
            state = Path(env['CODEX_HOME'])
            self.assertEqual((state / 'auth.json').resolve(), auth.resolve())
            self.assertEqual((state / 'skills/pdf/SKILL.md').resolve(), skill.resolve())
            self.assertNotIn('CODEX_THREAD_ID', env)
            self.assertNotIn('CODEX_SESSION_ID', env)
            self.assertNotIn('CODEX_APP_TOOLS_PIPE_PATH', env)

    def test_background_finishes_after_launcher_exits(self):
        self.fake_cli()
        log = self.root / 'batch.log'
        launch = subprocess.run([
            sys.executable, str(runner.HERE / 'runner.py'), str(self.input),
            '--output', str(self.args.output), '--codex', self.args.codex,
            '--workers', '1', '--timeout', '5', '--retries', '0',
            '--background', '--log-file', str(log),
        ], capture_output=True, text=True, timeout=5)
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertTrue(log.with_suffix('.pid').exists())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if log.exists() and 'done=1 / skipped=0 / failed=0 / stopped=0' in log.read_text():
                break
            time.sleep(0.05)
        self.assertIn('done=1 / skipped=0 / failed=0 / stopped=0', log.read_text())
        validate_result(json.loads(self.pair.output_path(self.args.output).read_text()))

    def test_dry_run_does_not_create_outputs(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main([str(self.input), '--output', str(self.args.output), '--dry-run']), 0)
        self.assertFalse(self.args.output.exists())


if __name__ == '__main__':
    unittest.main()

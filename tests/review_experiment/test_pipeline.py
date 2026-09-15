"""End-to-end subprocess tests use a fake CLI, never a model service."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from review_experiment import runner


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'input_data'
        self.output = self.root / 'results'
        self.skill = self.root / 'pdf/SKILL.md'
        self.skill.parent.mkdir()
        self.skill.write_text('---\nname: pdf\ndescription: Read PDF files\n---\nTest skill.\n')
        self.count = self.root / 'calls.jsonl'
        self.make_paper('2406.03862', [1, 2, 3])
        self.profiles, _ = runner.load_prompts(runner.HERE / 'prompts')
        self.schema = self.profiles['iclr2026'].schema
        runner.STOP.clear()

    def make_paper(self, aid, numbers):
        previous = {}
        for first, second in zip(numbers, numbers[1:]):
            folder = self.input / aid / f'v{first}_v{second}'
            folder.mkdir(parents=True)
            for number in (first, second):
                file = folder / f'v{number}.pdf'
                if number in previous:
                    os.link(previous[number], file)
                else:
                    file.write_bytes(f'%PDF-1.4\nfixture version {number}'.encode())
                    previous[number] = file

    def fake_cli(self, mode='valid'):
        cli = self.root / 'fake_codex'
        cli.write_text(f'''#!{__import__('sys').executable}
import json, pathlib, sys, time
args = sys.argv[1:]
assert args[0] == 'exec'
assert 'web_search="disabled"' in args
assert 'permissions.review_only.network.enabled=false' in args
assert 'default_permissions=\"review_only\"' in args
assert any('filesystem=' in a and '\":root\"=\"deny\"' in a for a in args)
assert 'approval_policy="never"' in args
assert '--ephemeral' in args and '--ignore-user-config' in args
assert '--sandbox' not in args
assert 'features.apps=false' in args and 'features.browser_use=false' in args
system = json.loads(next(a.split('=',1)[1] for a in args if a.startswith('developer_instructions=')))
assert system == pathlib.Path({str(runner.HERE / 'prompts/system/iclr2026.txt')!r}).read_text()
user = sys.stdin.read()
work = pathlib.Path(args[args.index('--cd')+1])
assert (work / '.agents/skills/pdf/SKILL.md').is_file()
assert (work / 'manuscript.pdf').read_bytes().startswith(b'%PDF-')
assert '{{PAPER_CONTENT}}' not in user and '{{OTHER_MODEL_NAME}}' not in user
assert '{{EVALUATOR_MODEL_NAME}}' not in user
assert user.endswith(str(work / 'manuscript.pdf'))
assert '2406.03862' not in user and 'exp3_' not in user
count = pathlib.Path({str(self.count)!r})
number = len(count.read_text().splitlines()) if count.exists() else 0
with count.open('a') as f: f.write(json.dumps({{'user': user}}) + '\\n')
mode = {mode!r}
if mode == 'timeout': time.sleep(10)
if mode == 'first_failure' and number == 0: sys.exit(1)
result = {{'oa': 2 if mode == 'invalid' else 6, 'strengths': ['Fixture strength'], 'weaknesses': ['Fixture weakness']}}
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result))
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':100,'cached_input_tokens':20,'output_tokens':5}}}}))
''')
        cli.chmod(0o755)
        return cli

    def run_cli(self, *extra, mode='valid'):
        cli = self.fake_cli(mode)
        argv = [str(self.input), '--output', str(self.output), '--source', 'iclr2026', '--codex', str(cli),
                '--pdf-skill', str(self.skill), '--other-model', 'Other-LLM', '--retries', '0', *extra]
        with redirect_stdout(io.StringIO()):
            return runner.main(argv)

    def result(self):
        return json.loads((self.output / '2406.03862/reviews.json').read_text())

    def test_frozen_prompts_and_condition_templates(self):
        profiles, conditions = runner.load_prompts(runner.HERE / 'prompts')
        self.assertEqual(len(profiles), 10)
        self.assertEqual(profiles['iclr2026'].schema, self.schema)
        self.assertEqual(len(conditions), 29)
        self.assertEqual(len({name for name, _ in conditions}), 29)
        self.assertNotEqual(dict(conditions)['exp1_quality'], dict(conditions)['exp2_self_human'])
        self.assertNotIn('exp3_neutral', dict(conditions))
        self.assertFalse((runner.HERE / 'prompts/exp3_neutral.user.txt').exists())
        self.assertIn('years of hard work', dict(conditions)['exp3_years'])

    def test_numeric_order_hardlink_dedup_single_read(self):
        self.make_paper('2501.12345', [2, 9, 10])
        papers, pairs = runner.discover(self.input)
        self.assertEqual(pairs, 4)
        self.assertEqual([v.number for v in papers['2501.12345']], [2, 9, 10])
        self.assertEqual(len(papers['2406.03862']), 3)
        version = papers['2406.03862'][1]
        original = Path.read_bytes
        calls = []
        def counted(path):
            calls.append(path)
            return original(path)
        with patch.object(Path, 'read_bytes', counted):
            version.read()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(runner.discover(self.input / '2406.03862')[0]['2406.03862']), 3)

    def test_conflicting_duplicate_and_missing_pdf_fail(self):
        duplicate = self.input / '2406.03862/v2_v3/v2.pdf'
        duplicate.unlink()
        duplicate.write_bytes(b'%PDF-different')
        versions = runner.discover(self.input)[0]['2406.03862']
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            versions[1].read()
        duplicate.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            runner.discover(self.input)

    def test_all_conditions_resume_and_changed_settings(self):
        self.assertEqual(self.run_cli('--condition', 'exp1_quality'), 0)
        first = self.result()
        self.assertEqual(len(self.count.read_text().splitlines()), 3)
        self.assertEqual(self.run_cli(), 0)
        document = self.result()
        for version, record in first['versions'].items():
            self.assertEqual(document['versions'][version]['reviews']['exp1_quality/r1'],
                             record['reviews']['exp1_quality/r1'])
        self.assertEqual(list(document['versions']), ['v1', 'v2', 'v3'])
        self.assertEqual(len(self.count.read_text().splitlines()), 87)
        for version in document['versions'].values():
            self.assertEqual(len(version['reviews']), 29)
            for review in version['reviews'].values():
                self.assertEqual(review['result'], {'oa': 6, 'strengths': ['Fixture strength'], 'weaknesses': ['Fixture weakness']})
                self.assertEqual(review['token_usage']['reported_totals']['total_tokens'], 105)
                self.assertFalse(Path(review['attempts'][0]['user_message'].splitlines()[-1]).exists())
        self.assertEqual(self.run_cli(), 0)
        self.assertEqual(len(self.count.read_text().splitlines()), 87)
        self.assertEqual(self.result()['latest_selection_counts']['skipped'], 87)
        self.assertEqual(self.run_cli('--condition', 'exp1_quality', '--version', '1', '--effort', 'high'), 0)
        self.assertEqual(len(self.count.read_text().splitlines()), 88)

    def test_failed_outputs_retry_resume_and_timeout(self):
        opts = ('--condition', 'exp1_quality', '--version', '1')
        self.assertEqual(self.run_cli(*opts, mode='invalid'), 1)
        review = self.result()['versions']['v1']['reviews']['exp1_quality/r1']
        self.assertEqual(review['status'], 'failed')
        self.assertNotIn('result', review)
        self.assertEqual(self.run_cli(*opts), 0)
        self.count.unlink()
        self.assertEqual(self.run_cli(*opts, '--overwrite', '--retries', '1', mode='first_failure'), 0)
        review = self.result()['versions']['v1']['reviews']['exp1_quality/r1']
        self.assertEqual(len(review['attempts']), 2)
        self.assertEqual(self.run_cli(*opts, '--overwrite', '--timeout', '0.1', mode='timeout'), 1)
        self.assertEqual(self.result()['versions']['v1']['reviews']['exp1_quality/r1']['status'], 'failed')

    def test_oa_rejects_bad_values_and_extra_fields(self):
        for bad in [True, 2, 4, 7, 9, 6.0, '6', None]:
            with self.assertRaises(ValueError):
                runner.validate_result({'oa': bad, 'strengths': ['Valid'], 'weaknesses': ['Valid']}, self.schema)
        with self.assertRaises(ValueError):
            runner.validate_result({'oa': 6, 'reason': 'extra'}, self.schema)

    def test_strengths_and_weaknesses_are_required_and_preserved(self):
        good = {'oa': 6, 'strengths': ['Original wording.'], 'weaknesses': ['A concern.']}
        self.assertEqual(runner.validate_result(good, self.schema), good)
        for bad in [{'oa': 6}, {'oa': 6, 'strengths': [], 'weaknesses': 'text'},
                    {'oa': 6, 'strengths': [1], 'weaknesses': []},
                    {'oa': 6, 'strengths': [' '], 'weaknesses': []}]:
            with self.assertRaises(ValueError):
                runner.validate_result(bad, self.schema)

    def test_explicit_max_effort_reaches_cli_and_result(self):
        with patch.object(runner.subprocess, 'Popen', wraps=runner.subprocess.Popen) as launch:
            self.assertEqual(self.run_cli('--condition', 'exp1_quality', '--version', '1',
                                          '--model', 'gpt-6-astra', '--effort', 'max'), 0)
        command = launch.call_args.args[0]
        self.assertIn('model_reasoning_effort="max"', command)
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-astra')
        review = self.result()['versions']['v1']['reviews']['exp1_quality/r1']
        self.assertEqual(review['effort'], 'max')
        self.assertEqual(review['status'], 'completed')
        self.assertEqual(len(self.count.read_text().splitlines()), 1)

    def test_exact_cli_binary_and_explicit_pdf_runtime_are_readable(self):
        from types import SimpleNamespace
        cli = self.fake_cli()
        runtime = self.root / 'tools'
        (runtime / 'python_vendor').mkdir(parents=True)
        (runtime / 'ghostscript-9.55.0').mkdir()
        args = SimpleNamespace(codex=str(cli), model='gpt-6-astra', effort='max',
                               pdf_skill=self.skill, tool_runtime=runtime)
        work = self.root / 'work'
        work.mkdir()
        snapshot = self.root / 'snapshot.pdf'
        snapshot.write_bytes(b'%PDF-test')
        command = runner.build_command(args, work, 'system', snapshot)
        filesystem = next(x for x in command if x.startswith('permissions.review_only.filesystem='))
        self.assertIn(json.dumps(str(cli.resolve())) + '="read"', filesystem)
        self.assertIn(json.dumps(str(runtime / 'python_vendor')) + '="read"', filesystem)
        self.assertIn(json.dumps(str(runtime / 'ghostscript-9.55.0')) + '="read"', filesystem)
        self.assertIn('":root"="deny"', filesystem)
        self.assertNotIn(json.dumps(str(self.root)) + '="read"', filesystem)
        environment = next(x for x in command if x.startswith('shell_environment_policy.set='))
        self.assertIn('"PYTHONPATH"=' + json.dumps(str(runtime / 'python_vendor')), environment)
        self.assertIn(str(runtime / 'ghostscript-9.55.0/bin'), environment)
        self.assertIn('permissions.review_only.network.enabled=false', command)
        self.assertIn('"TMPDIR"=' + json.dumps(str(work)), environment)
        self.assertIn('"TMPPREFIX"=' + json.dumps(str(work / 'zsh')), environment)

    def test_macos_shared_temp_denials_are_explicit_globs(self):
        with patch.object(runner.sys, 'platform', 'darwin'):
            options = runner.permission_options(self.root / 'work', self.root / 'snapshot.pdf',
                                                self.skill)
        filesystem = options[-1]
        for path in ('/tmp', '/private/tmp', '/var/tmp', '/private/var/tmp'):
            self.assertIn(json.dumps(path + '/**') + '="deny"', filesystem)
            self.assertIn(json.dumps(path[:-1] + '[p]') + '="deny"', filesystem)

    def test_macos_temp_rules_do_not_change_linux_permissions(self):
        with patch.object(runner.sys, 'platform', 'linux'):
            options = runner.permission_options(self.root / 'work', self.root / 'snapshot.pdf',
                                                self.skill)
        self.assertIn('\":minimal\"=\"read\"', options[-1])
        self.assertNotIn('/private/tmp/**', options[-1])

    def test_dry_run_has_no_reads_or_results(self):
        with patch.object(runner.Version, 'read', side_effect=AssertionError('PDF read')):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(runner.main([str(self.input), '--output', str(self.output), '--source', 'iclr2026', '--dry-run']), 0)
        self.assertEqual(json.loads(out.getvalue())['planned_reviews'], 87)
        self.assertFalse(self.output.exists())


    def test_experiment_three_selects_the_shared_baseline_once(self):
        folder = runner.HERE / 'prompts'
        _, exp3 = runner.load_prompts(folder, experiments=['3'])
        ids = [name for name, _ in exp3]
        self.assertEqual(ids[0], 'exp1_quality')
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)
        self.assertNotIn('exp3_neutral', ids)
        self.assertEqual(runner.load_prompts(folder, experiments=['1', '3'])[1], exp3)
        self.assertEqual(runner.load_prompts(folder, experiments=['1'])[1], exp3[:1])
        self.assertEqual(runner.load_prompts(folder, experiments=['3'],
                                            conditions=['exp1_quality'])[1], exp3[:1])
        selected = runner.load_prompts(folder, experiments=['3'], conditions=['exp3_years'])[1]
        self.assertEqual([name for name, _ in selected], ['exp3_years'])

    def test_experiment_three_reuses_baseline_and_preserves_legacy_records(self):
        self.assertEqual(self.run_cli('--condition', 'exp1_quality'), 0)
        before = self.result()
        baseline = deepcopy(before['versions'])
        # A historical duplicate is retained as evidence, never selected again.
        legacy = deepcopy(before['versions']['v1']['reviews']['exp1_quality/r1'])
        legacy['condition'] = 'exp3_neutral'
        before['versions']['v1']['reviews']['exp3_neutral/r1'] = legacy
        path = self.output / '2406.03862/reviews.json'
        path.write_text(json.dumps(before))
        self.assertEqual(self.run_cli('--experiment', '3'), 0)
        after = self.result()
        self.assertEqual(after['latest_selection_counts'],
                         {'completed': 21, 'skipped': 3, 'failed': 0})
        self.assertEqual(len(self.count.read_text().splitlines()), 24)
        for version, record in baseline.items():
            self.assertEqual(after['versions'][version]['reviews']['exp1_quality/r1'],
                             record['reviews']['exp1_quality/r1'])
        self.assertEqual(after['versions']['v1']['reviews']['exp3_neutral/r1'], legacy)
        for version in ('v2', 'v3'):
            self.assertNotIn('exp3_neutral/r1', after['versions'][version]['reviews'])
        snapshot = deepcopy(after['versions'])
        self.assertEqual(self.run_cli('--experiment', '1', '--experiment', '3'), 0)
        self.assertEqual(len(self.count.read_text().splitlines()), 24)
        self.assertEqual(self.result()['latest_selection_counts']['skipped'], 24)
        self.assertEqual(self.result()['versions'], snapshot)

    def test_removed_neutral_condition_is_rejected_before_a_call(self):
        with redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as error:
                self.run_cli('--condition', 'exp3_neutral')
        self.assertEqual(error.exception.code, 1)
        self.assertIn('Unknown condition', errors.getvalue())
        self.assertFalse(self.count.exists())

    def test_experiment_three_dry_run_counts_shared_baseline_once(self):
        with patch.object(runner.Version, 'read', side_effect=AssertionError('PDF read')):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(runner.main([str(self.input), '--output', str(self.output), '--source', 'iclr2026',
                                              '--experiment', '3', '--dry-run']), 0)
        inventory = json.loads(out.getvalue())
        self.assertEqual(inventory['planned_reviews'], 24)
        self.assertEqual(inventory['conditions'].count('exp1_quality'), 1)
        self.assertNotIn('exp3_neutral', inventory['conditions'])
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

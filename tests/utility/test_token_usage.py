"""Check real CLI counter shapes, retries, unavailable telemetry, and run boundaries."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from check_diff import runner
from utility import token_usage

from tests.check_diff.fixtures import annotation


def event_usage(input_tokens=100, cached=80, output=20, reasoning=12):
    return {'input_tokens': input_tokens, 'cached_input_tokens': cached,
            'cache_write_input_tokens': 0, 'output_tokens': output,
            'reasoning_output_tokens': reasoning}


def attempt(usage):
    stream = io.StringIO(json.dumps({'type': 'turn.completed', 'usage': usage}) + '\n')
    return {'token_usage': token_usage.read_events(stream)}


class TokenUsageTests(unittest.TestCase):
    def test_cached_and_reasoning_subsets_are_not_double_counted(self):
        summary = token_usage.summarize([attempt(event_usage())])
        self.assertEqual(summary['status'], 'complete')
        totals = summary['reported_totals']
        self.assertEqual(totals['total_tokens'], 120)
        self.assertEqual(totals['non_cached_input_tokens'], 20)
        self.assertEqual(totals['reasoning_output_tokens'], 12)

    def test_retry_usage_includes_failed_attempts_with_reported_turns(self):
        summary = token_usage.summarize([attempt(event_usage()), attempt(event_usage(50, 30, 10, 4))])
        self.assertEqual(summary['reported_totals']['total_tokens'], 180)
        self.assertEqual(summary['reported_turn_count'], 2)

    def test_unreported_failed_attempt_is_unknown_not_zero(self):
        summary = token_usage.summarize([{}, attempt(event_usage())])
        self.assertEqual(summary['status'], 'partial')
        self.assertEqual(summary['attempts_without_reported_usage'], 1)
        self.assertEqual(summary['reported_totals']['total_tokens'], 120)
        self.assertIsNone(token_usage.summarize([{}])['reported_totals']['total_tokens'])

    def test_missing_optional_counters_remain_null(self):
        result = token_usage.summarize([attempt({'input_tokens': 10, 'output_tokens': 3})])
        self.assertEqual(result['reported_totals']['total_tokens'], 13)
        self.assertIsNone(result['reported_totals']['cached_input_tokens'])
        self.assertIsNone(result['reported_totals']['non_cached_input_tokens'])

    def test_missing_counter_in_one_turn_does_not_make_a_partial_total_look_complete(self):
        result = token_usage.summarize([attempt(event_usage()), attempt({'input_tokens': 10})])
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['reported_totals']['total_tokens'])

    def test_zero_calls_differ_from_calls_without_usage(self):
        result = token_usage.summarize([])
        self.assertEqual(result['status'], 'not_run')
        self.assertEqual(result['reported_totals']['total_tokens'], 0)

    def test_invalid_and_nonusage_events_are_not_counted(self):
        for usage in ({'input_tokens': True, 'output_tokens': 2},
                      {'input_tokens': -1, 'output_tokens': 2},
                      {'input_tokens': 1, 'cached_input_tokens': 2, 'output_tokens': 2}):
            report = attempt(usage)['token_usage']
            self.assertEqual(report['records'], [])
            self.assertTrue(report['errors'])
        report = token_usage.read_events(io.StringIO(json.dumps({'type': 'item.completed', 'usage': event_usage()}) + '\n'))
        self.assertEqual(report['records'], [])

    def test_completed_turn_is_counted_once_even_when_other_events_repeat_usage(self):
        stream = io.StringIO('\n'.join(json.dumps({'type': kind, 'usage': event_usage()})
                                      for kind in ('turn.started', 'item.completed', 'turn.completed')))
        report = token_usage.summarize([{'token_usage': token_usage.read_events(stream)}])
        self.assertEqual(report['reported_totals']['total_tokens'], 120)


class TokenPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / 'input/2406.03862/v1_v2'
        folder.mkdir(parents=True)
        self.pair = runner.Pair(folder, '2406.03862', 1, 2)
        for pdf in self.pair.pdfs:
            pdf.write_bytes(b'%PDF-fixture')
        skill = self.root / 'skill/SKILL.md'
        skill.parent.mkdir()
        skill.write_text('Synthetic PDF skill')
        self.args = SimpleNamespace(pdf_skill=skill, output=self.root / 'output', model='fixture', effort='medium',
            codex='must-not-run', timeout=5, retries=1, overwrite=False, workers=2)
        runner.STOP.clear()
        self.addCleanup(runner.STOP.clear)

    def fake_cli(self, *, exit_code=0, usage=None):
        script = self.root / 'fake_codex'
        script.write_text(f'''#!{sys.executable}
import json, pathlib, sys
args = sys.argv[1:]
assert '--json' in args
sys.stdin.read()
print(json.dumps({{'type': 'turn.completed', 'usage': {usage or event_usage()!r}}}))
print('stderr diagnostics are separate from JSON telemetry', file=sys.stderr)
if not {exit_code}:
    pathlib.Path(args[args.index('--output-last-message') + 1]).write_text({json.dumps(annotation())!r})
sys.exit({exit_code})
''')
        script.chmod(0o755)
        self.args.codex = str(script)

    def test_real_subprocess_telemetry_is_saved_with_the_result(self):
        self.fake_cli()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_pair(self.args, self.pair), 'done')
        path = self.pair.output_path(self.args.output)
        self.assertEqual(json.loads(path.read_text()), annotation())
        receipt = json.loads(path.with_suffix('.execution.json').read_text())
        self.assertEqual(receipt['token_usage']['reported_totals']['total_tokens'], 120)
        self.assertEqual(receipt['attempts'][0]['token_usage']['records'][0]['usage'], event_usage())

    def test_failed_subprocess_and_retry_both_keep_reported_consumption(self):
        self.fake_cli(exit_code=2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_pair(self.args, self.pair), 'failed')
        path = self.pair.output_path(self.args.output)
        self.assertFalse(path.exists())
        receipt = json.loads(path.with_suffix('.execution.json').read_text())
        self.assertEqual(len(receipt['attempts']), 2)
        self.assertEqual(receipt['token_usage']['reported_totals']['total_tokens'], 240)

    def test_batch_summary_excludes_skipped_results_from_current_run_consumption(self):
        self.fake_cli()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(self.args, [self.pair]), 0)
            first = json.loads((self.args.output / 'token_usage.json').read_text())
            self.assertEqual(first['token_usage']['reported_totals']['total_tokens'], 120)
            self.assertEqual(runner.run(self.args, [self.pair]), 0)
        second = json.loads((self.args.output / 'token_usage.json').read_text())
        self.assertEqual(second['token_usage']['reported_totals']['total_tokens'], 0)
        self.assertEqual(second['pair_counts']['skipped'], 1)
        self.assertFalse(second['pairs'][0]['executed_in_this_run'])

    def test_retry_with_missing_telemetry_does_not_claim_full_usage_coverage(self):
        def compare(args, pair, record):
            if record['attempt'] == 1:
                raise RuntimeError('Disconnected before usage was emitted')
            record.update(attempt(event_usage()))
            return annotation()
        with patch.object(runner, 'compare_pair', side_effect=compare), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run_pair(self.args, self.pair), 'done')
        receipt = json.loads(self.pair.output_path(self.args.output).with_suffix('.execution.json').read_text())
        self.assertEqual(receipt['token_usage']['status'], 'partial')
        self.assertEqual(receipt['token_usage']['attempts_without_reported_usage'], 1)

    def test_legacy_execution_record_blocks_reuse_and_keeps_unknown_usage(self):
        path = self.pair.output_path(self.args.output)
        runner.save_result(path, annotation())
        path.with_suffix('.execution.json').write_text(json.dumps({'status': 'completed'}))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(self.args, [self.pair]), 1)
        report = json.loads((self.args.output / 'token_usage.json').read_text())
        self.assertEqual(report['token_usage']['reported_totals']['total_tokens'], 0)
        self.assertEqual(report['pairs'][0]['token_usage']['status'], 'unavailable')

    def test_corrupt_old_execution_record_is_reported_without_rerunning_the_judgment(self):
        path = self.pair.output_path(self.args.output)
        runner.save_result(path, annotation())
        path.with_suffix('.execution.json').write_text('{invalid')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.run(self.args, [self.pair]), 1)
        report = json.loads((self.args.output / 'token_usage.json').read_text())
        self.assertEqual(report['pairs_without_execution_records'], 1)
        self.assertEqual(len(report['execution_record_errors']), 1)


if __name__ == '__main__':
    unittest.main()

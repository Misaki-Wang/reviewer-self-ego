"""Source-dependent review integration uses only fake CLI calls and fake PDFs."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import sys
import unittest
from unittest.mock import patch
from review_experiment import runner
from . import test_pipeline


class SourceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_pipeline.PipelineTests('test_dry_run_has_no_reads_or_results')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.log = self.root / 'profile_calls.jsonl'

    def cli(self, invalid_score=None):
        path = self.root / 'profile_cli'
        texts = {key: profile.text for key, profile in self.fixture.profiles.items()}
        path.write_text(f'''#!{sys.executable}
import json,pathlib,sys
args=sys.argv[1:]
system=json.loads(next(a.split('=',1)[1] for a in args if a.startswith('developer_instructions=')))
texts={texts!r}
source=next(k for k,v in texts.items() if v==system)
schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
oa=schema['properties']['oa']
score=3.5 if oa['type']=='number' else oa['enum'][-1]
if {invalid_score!r} is not None:score={invalid_score!r}
user=sys.stdin.read()
assert user.endswith('/manuscript.pdf') and '{{PAPER_CONTENT}}' not in user
result={{'oa':score,'strengths':['Fixture strength'],'weaknesses':['Fixture weakness']}}
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(result))
with pathlib.Path({str(self.log)!r}).open('a') as stream:stream.write(json.dumps({{'source':source,'oa_schema':oa}})+'\\n')
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':100,'cached_input_tokens':20,'output_tokens':5}}}}))
''')
        path.chmod(0o755)
        return path

    def invoke(self, *extra, invalid_score=None):
        args=[str(self.fixture.input),'--output',str(self.fixture.output),
              '--codex',str(self.cli(invalid_score)),'--pdf-skill',str(self.fixture.skill),
              '--condition','exp1_quality','--retries','0',*extra]
        with redirect_stdout(io.StringIO()):
            return runner.main(args)

    def test_mixed_sources_use_independent_systems_schemas_and_records(self):
        self.fixture.make_paper('2501.12345',[1,2])
        self.fixture.make_paper('2502.12345',[1,2])
        sources={'2406.03862':'iclr2026','2501.12345':'acl2026','2502.12345':'tmlr_2025plus'}
        (self.fixture.input/'source_metadata.json').write_text(json.dumps({'source_by_paper':sources}))
        self.assertEqual(self.invoke('--workers','6'),0)
        calls=[json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls),7)
        expected={'iclr2026':10,'acl2026':3.5,'tmlr_2025plus':4}
        for aid,source in sources.items():
            doc=json.loads((self.fixture.output/aid/'reviews.json').read_text())
            self.assertEqual(doc['source_id'],source)
            for version in doc['versions'].values():
                review=version['reviews']['exp1_quality/r1']
                self.assertEqual(review['source_id'],source)
                self.assertEqual(review['result']['oa'],expected[source])
                self.assertEqual(review['system_prompt']['sha256'],self.fixture.profiles[source].sha256)
                self.assertEqual(review['system_prompt']['scale_kind'],'experimental_encoding' if source=='tmlr_2025plus' else 'native')
        summary=json.loads((self.fixture.output/'run_summary.json').read_text())
        self.assertEqual(summary['inventory']['source_by_paper'],sources)
        before={p: p.read_bytes() for p in self.fixture.output.glob('*/reviews.json')}
        self.assertEqual(self.invoke('--workers','4'),0)
        self.assertEqual(len(self.log.read_text().splitlines()),7)
        for p,raw in before.items():
            self.assertEqual(json.loads(p.read_text())['versions'],json.loads(raw)['versions'])

    def test_wrong_venue_score_is_rejected_and_raw_response_preserved(self):
        self.assertEqual(self.invoke('--source','acl2026','--version','1',invalid_score=8),1)
        review=self.fixture.result()['versions']['v1']['reviews']['exp1_quality/r1']
        self.assertEqual(review['status'],'failed')
        self.assertNotIn('result',review)
        self.assertEqual(json.loads(review['attempts'][0]['raw_response'])['oa'],8)

    def test_source_change_cannot_mix_existing_results(self):
        self.assertEqual(self.invoke('--source','iclr2026','--version','1'),0)
        path=self.fixture.output/'2406.03862/reviews.json'
        original=path.read_bytes()
        with patch.object(runner,'call_codex',side_effect=AssertionError('unexpected model call')), redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit):
                self.invoke('--source','icml2026','--version','1','--overwrite')
        self.assertIn('Result source',errors.getvalue())
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(self.log.read_text().splitlines()),1)

    def test_unidentified_legacy_results_require_a_fresh_output(self):
        self.assertEqual(self.invoke('--source','iclr2026','--version','1'),0)
        path=self.fixture.output/'2406.03862/reviews.json'
        doc=json.loads(path.read_text());doc.pop('source_id');path.write_text(json.dumps(doc))
        original=path.read_bytes()
        with redirect_stderr(io.StringIO()) as errors,self.assertRaises(SystemExit):
            self.invoke('--source','iclr2026','--version','1')
        self.assertIn('fresh output',errors.getvalue())
        self.assertEqual(path.read_bytes(),original)

    def test_missing_source_fails_before_output_or_model_call(self):
        with patch.object(runner,'ROOT',self.root/'empty-project'),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(self.log.exists())

    def test_fingerprint_covers_source_even_for_identical_text_and_scores(self):
        from dataclasses import replace
        from types import SimpleNamespace
        first=self.fixture.profiles['iclr2026']
        second=replace(first,source_id='other-source')
        args=SimpleNamespace(other_model='Other',model='model',effort='max',codex=str(self.cli()),tool_runtime=None,pdf_skill_sha256='a'*64)
        a=runner.fingerprint(args,first,'template','b'*64)
        b=runner.fingerprint(args,second,'template','b'*64)
        self.assertNotEqual(a,b)


if __name__=='__main__':
    unittest.main()

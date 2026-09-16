"""Single-PDF anonymization tests: synthetic PDFs and fake Codex, never a model."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import pymupdf as fitz
from utility.anonymize import anonymize_pdf as cleaner


class AnonymizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(patch.stopall)
        patch.object(cleaner, 'ROOT', self.root).start()
        (self.root / 'utility').mkdir()
        (self.root / 'utility/project.json').write_text(json.dumps({'data': {'downloads_root': 'downloads_papers'}}))
        self.source = self.root / 'downloads_papers/iclr2026/2501.12345/v1.pdf'
        self.source.parent.mkdir(parents=True)
        self.target = self.root / 'clean_data/iclr2026/2501.12345/v1.pdf'
        self.report = self.root / 'res/anonymize_pdf/iclr2026/2501.12345/v1.json'
        self.skill = self.root / 'pdf/SKILL.md'
        self.skill.parent.mkdir()
        self.skill.write_text('---\nname: pdf\ndescription: PDF test fixture\n---\nInspect the PDF.\n')
        self.work = self.root / 'work'
        self.work.mkdir()
        with fitz.open() as doc:
            p = doc.new_page()
            lines = [('A Scientific Paper', 60), ('Alice Example', 90), ('Example University', 110),
                     ('alice@example.edu', 130), ('Accepted by ICLR 2026', 150),
                     ('Our scientific result is x = 42.', 220)]
            for text, y in lines:
                p.insert_text((72, y), text)
            p.add_text_annot((20, 20), 'Alice Example private note')
            p.insert_link({'kind': fitz.LINK_URI, 'from': fitz.Rect(72, 117, 200, 135),
                           'uri': 'mailto:alice@example.edu'})
            p = doc.new_page()
            p.insert_text((72, 60), 'Acknowledgments')
            p.insert_text((72, 90), 'We thank Alice Example and Example University.')
            p.insert_text((72, 220), 'References: Smith et al. A useful method.')
            doc.set_metadata({'author': 'Alice Example', 'title': 'A Scientific Paper'})
            doc.set_xml_metadata('<xmpmeta>Alice Example</xmpmeta>')
            doc.embfile_add('private.txt', b'Alice Example')
            doc.set_toc([[1, 'Alice Example', 1]])
            doc.save(self.source)
        self.plan = {'redactions': [], 'forbidden_strings': ['alice@example.edu'], 'uncertain': []}
        with fitz.open(self.source) as doc:
            for number, phrases in ((1, ['Alice Example', 'Example University', 'alice@example.edu',
                                          'Accepted by ICLR 2026']),
                                     (2, ['Acknowledgments', 'We thank Alice Example and Example University.'])):
                for phrase in phrases:
                    rect = doc[number - 1].search_for(phrase)[0] + fitz.Rect(-1, -1, 1, 1)
                    self.plan['redactions'].append({'page': number, 'rect': list(rect),
                                                   'category': 'other_identity', 'text': phrase})
        self.metadata = b'{"authors": ["Alice Example"], "source_ids": ["iclr2026"]}\n'
        (self.source.parent / 'metadata.json').write_bytes(self.metadata)
        self.pages = cleaner.inspect_pdf(self.source, self.work)
        self.calls = self.root / 'calls.jsonl'

    def fake_cli(self, passed=True, failed_audits=0):
        path = self.root / 'fake-codex'
        review = {'passed': passed, 'content_preserved': passed, 'reviewed_pages': [1, 2],
                  'remaining_identifiers': [] if passed else ['a remaining author'], 'notes': 'fixture'}
        path.write_text(f'''#!{sys.executable}
import json,pathlib,sys
argv=sys.argv[1:]
assert argv[0]=='exec' and '--ephemeral' in argv and '--ignore-user-config' in argv
assert 'permissions.isolated_files.network.enabled=false' in argv
assert not any('dangerously' in a for a in argv)
prompt=sys.stdin.read()
assert 'PDF skill' in prompt
assert argv.count('--image') == 2
with pathlib.Path({str(self.root / 'prompts.jsonl')!r}).open('a') as f:f.write(json.dumps(prompt)+'\\n')
response=pathlib.Path(argv[argv.index('--output-last-message')+1])
payload={self.plan!r} if response.name.startswith('identify') else {review!r}
calls=pathlib.Path({str(self.calls)!r})
if response.name.startswith('audit') and (calls.read_text().count('audit-response') if calls.exists() else 0) < {failed_audits}:
 payload={{'passed':False,'content_preserved':True,'reviewed_pages':[1,2],
          'remaining_identifiers':['Remaining institutional logo'], 'notes':'Remove the institutional logo.'}}
response.write_text(json.dumps(payload))
with pathlib.Path({str(self.calls)!r}).open('a') as f:f.write(response.name+'\\n')
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'output_tokens':5}}}}))
''')
        path.chmod(0o755)
        return path

    def run_cleaner(self, *extra, passed=True, failed_audits=0):
        with redirect_stdout(io.StringIO()):
            return cleaner.main([str(self.source), '--pdf-skill', str(self.skill),
                                 '--codex', str(self.fake_cli(passed, failed_audits)), *extra])

    def test_true_redaction_scrubs_hidden_data_and_preserves_science(self):
        candidate = self.work / 'candidate.pdf'
        cleaner.redact_pdf(self.source, candidate, self.plan, self.pages)
        result = cleaner.check_pdf(self.source, candidate, self.plan, self.work)
        self.assertEqual(result['pages'], 2)
        with fitz.open(candidate) as doc:
            text = ''.join(page.get_text() for page in doc)
            self.assertNotIn('Alice', text)
            self.assertNotIn('University', text)
            self.assertNotIn('Acknowledgments', text)
            self.assertNotIn('ICLR', text)
            self.assertIn('Our scientific result is x = 42.', text)
            self.assertIn('References: Smith et al.', text)
            self.assertEqual(doc.embfile_count(), 0)
            self.assertFalse(doc.metadata['author'])
            self.assertFalse(doc.get_xml_metadata())

    def test_full_single_pdf_pipeline_outputs_pdf_only_and_resumes_without_calls(self):
        original = self.source.read_bytes()
        self.assertEqual(self.run_cleaner(), 0)
        self.assertTrue(self.target.is_file())
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse((self.target.parent / 'metadata.json').exists())
        receipt = json.loads(self.report.read_text())
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['token_usage']['reported_totals']['total_tokens'], 30)
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)
        self.assertEqual(self.run_cleaner(), 0)
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_stages_share_only_exact_read_only_materials(self):
        real_popen = cleaner.subprocess.Popen
        scopes = []

        def inspect_command(command, **kwargs):
            work = Path(command[command.index('--cd') + 1])
            value = next(item for item in command
                         if item.startswith('permissions.isolated_files.filesystem='))
            permissions = tomllib.loads(value)['permissions']['isolated_files']['filesystem']
            self.assertEqual(permissions[':root'], 'deny')
            self.assertEqual(permissions[str(work)], 'write')
            self.assertEqual(permissions[str(self.source)], 'read')
            self.assertNotIn(str(self.source.parent), permissions)
            self.assertEqual(kwargs['env']['TMPDIR'], str(work))
            linked = {str(path.relative_to(work)): path.resolve()
                      for path in work.rglob('*') if path.is_symlink() and path.is_file()}
            for target in linked.values():
                self.assertFalse(target.is_relative_to(work))
                self.assertEqual(permissions[str(target)], 'read')
            evidence_root = self.report.parent / (self.report.name + '.evidence')
            for channel, suffix in (('stdout', 'jsonl'), ('stderr', 'stderr')):
                path = Path(kwargs[channel].name)
                self.assertEqual(path.name, f'{work.name}.{suffix}')
                self.assertEqual(path.parent.parent, evidence_root)
                self.assertFalse(path.is_relative_to(work))
                self.assertFalse(any(
                    not scope.startswith(':') and access in ('read', 'write')
                    and path.is_relative_to(Path(scope))
                    for scope, access in permissions.items()))
            scopes.append((work, linked, permissions))
            return real_popen(command, **kwargs)

        with patch.object(cleaner.subprocess, 'Popen', side_effect=inspect_command):
            self.assertEqual(self.run_cleaner(), 0)
        identify, audit = scopes
        self.assertEqual(identify[0].name, 'identify')
        self.assertEqual(audit[0].name, 'audit')
        self.assertEqual(identify[0].parent, audit[0].parent)
        before = {'before/page-0001.png', 'before/page-0002.png'}
        after = {'after/page-0001.png', 'after/page-0002.png'}
        self.assertEqual(set(identify[1]), {'pages.json'} | before)
        self.assertEqual(set(audit[1]), {'pages.json', 'candidate.pdf', 'plan.json'} | before | after)
        self.assertNotIn(str(identify[0]), audit[2])
        self.assertNotIn(str(identify[0].parent), audit[2])
        self.assertFalse(any('identify-response' in path or 'identify.jsonl' in path
                             or 'identify.stderr' in path for path in audit[2]))
        receipt = json.loads(self.report.read_text())
        self.assertEqual(receipt['binding']['isolation_policy'], cleaner.isolation.identity())
        for attempt, (_, links, _) in zip(receipt['attempts'], scopes):
            self.assertEqual(attempt['isolation_policy'], cleaner.isolation.identity())
            self.assertEqual(set(attempt['read_files']), {str(self.source)} | {str(p) for p in links.values()})

    def test_attachments_outside_allowlist_fail_before_subprocess(self):
        outside = self.root / 'other-paper.png'
        outside.write_bytes(b'private test image')
        args = Namespace(codex=str(self.fake_cli()), model='gpt-5.5', effort='high', timeout=5)
        real_command = cleaner.cli_command

        def inject_attachment(args, work, read_files, skill, schema, response, images=()):
            link = work / 'unapproved.png'
            link.symlink_to(outside)
            return real_command(args, work, read_files, skill, schema, response, [link])

        attempts = []
        with patch.object(cleaner, 'cli_command', side_effect=inject_attachment), \
                patch.object(cleaner.subprocess, 'Popen') as popen, self.assertRaises(ValueError):
            cleaner.call_cli(args, self.work, self.source, self.skill, 'identify',
                             cleaner.PLAN_SCHEMA, cleaner.IDENTIFY, attempts)
        popen.assert_not_called()
        self.assertEqual(attempts, [])

    def test_retries_get_fresh_scopes_without_previous_materials_or_logs(self):
        self.assertEqual(self.run_cleaner(failed_audits=1), 0)
        receipt = json.loads(self.report.read_text())
        attempts = receipt['attempts']
        workdirs = [Path(a['workdir']) for a in attempts]
        self.assertEqual(len(set(workdirs)), 4)
        self.assertEqual([p.parent.name for p in workdirs],
                         ['attempt-1', 'attempt-1', 'attempt-2', 'attempt-2'])
        for attempt, work in zip(attempts, workdirs):
            for path in map(Path, attempt['read_files']):
                self.assertTrue(path == self.source or path.parent == work.parent / 'materials'
                                or path.parent.parent == work.parent / 'materials')
                self.assertFalse(path.is_relative_to(work))
            for previous in workdirs:
                if previous != work:
                    self.assertNotIn(str(previous), attempt['read_files'])
        self.assertNotIn('identify-response', attempts[3]['prompt'])
        self.assertNotIn('Temporary connection failure', attempts[3]['prompt'])

    def test_cache_requires_same_isolation_policy_identity(self):
        self.assertEqual(self.run_cleaner(), 0)
        changed = {**cleaner.isolation.identity(), 'sha256': 'changed-policy'}
        with patch.object(cleaner.isolation, 'identity', return_value=changed), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cleaner()
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_failed_audit_never_publishes_clean_pdf(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_cleaner(passed=False)
        self.assertFalse(self.target.exists())
        receipt = json.loads(self.report.read_text())
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(len(receipt['iterations']), 3)
        self.assertEqual(len(self.calls.read_text().splitlines()), 6)

    def test_failed_audit_feedback_reaches_next_attempt_then_stops_on_success(self):
        original = self.source.read_bytes()
        self.assertEqual(self.run_cleaner(failed_audits=1), 0)
        receipt = json.loads(self.report.read_text())
        self.assertEqual([i['status'] for i in receipt['iterations']], ['failed', 'completed'])
        prompts = [json.loads(s) for s in (self.root / 'prompts.jsonl').read_text().splitlines()]
        self.assertEqual(len(prompts), 4)
        self.assertIn('The previous attempt failed', prompts[2])
        self.assertIn('Remaining institutional logo', prompts[2])
        self.assertIn('redactions', prompts[2])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(self.target.is_file())
        self.assertEqual(receipt['token_usage']['reported_totals']['total_tokens'], 60)

    def test_invalid_plan_feedback_reaches_retry_before_any_pdf_is_published(self):
        real_call = cleaner.call_cli
        calls = []
        def identify_once_invalid(*args):
            calls.append(args[6])
            if len(calls) == 1:
                invalid = deepcopy(self.plan)
                invalid['redactions'][0]['rect'] = [-1, 0, 10, 10]
                return invalid
            self.assertFalse(self.target.exists())
            return real_call(*args)
        with patch.object(cleaner, 'call_cli', side_effect=identify_once_invalid):
            self.assertEqual(self.run_cleaner(), 0)
        self.assertIn('outside the page', calls[1])
        self.assertEqual(len(json.loads(self.report.read_text())['iterations']), 2)

    def test_cli_failure_is_retried_with_stderr_feedback(self):
        cli = self.fake_cli()
        marker = self.root / 'failed-once'
        cli.write_text(cli.read_text().replace('prompt=sys.stdin.read()', f'''prompt=sys.stdin.read()
marker=pathlib.Path({str(marker)!r})
if not marker.exists():
 marker.touch()
 sys.stderr.write('Temporary connection failure')
 sys.exit(1)
'''))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cleaner.main([str(self.source), '--pdf-skill', str(self.skill),
                                          '--codex', str(cli)]), 0)
        receipt = json.loads(self.report.read_text())
        self.assertEqual(len(receipt['iterations']), 2)
        self.assertEqual([a['exit_code'] for a in receipt['attempts']], [1, 0, 0])
        self.assertIn('Temporary connection failure', receipt['attempts'][1]['prompt'])

    def test_dry_run_has_no_model_calls_or_pdf_reads_or_output(self):
        with patch.object(cleaner, 'anonymize', side_effect=AssertionError('processing during dry-run')), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cleaner.main([str(self.source), '--dry-run']), 0)
        self.assertEqual(json.loads(out.getvalue())['output'], str(self.target))
        self.assertFalse(self.target.exists())
        self.assertFalse(self.report.exists())

    def test_output_uses_shared_configured_clean_root(self):
        config = self.root / 'utility/project.json'
        config.write_text(json.dumps({'data': {'downloads_root': 'downloads_papers',
                                               'clean_root': 'anonymous-store'}}))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cleaner.main([str(self.source), '--dry-run']), 0)
        self.assertEqual(json.loads(out.getvalue())['output'],
                         str(self.root / 'anonymous-store/iclr2026/2501.12345/v1.pdf'))

    def test_directory_input_and_original_overwrite_are_rejected(self):
        for argv in ([str(self.source.parent)], [str(self.source), '--output', str(self.source), '--overwrite']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cleaner.main(argv)

    def test_uncertain_or_out_of_bounds_plan_is_not_applied(self):
        for mutate in (lambda p: p.update(uncertain=['Unreadable page']),
                       lambda p: p['redactions'][0].update(page=0),
                       lambda p: p['redactions'][0].update(rect=[0, 0, float('nan'), 20]),
                       lambda p: p['redactions'][0].update(rect=[-1, 0, 30, 20])):
            plan = deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(ValueError):
                cleaner.redact_pdf(self.source, self.work / 'invalid.pdf', plan, self.pages)
            self.assertFalse((self.work / 'invalid.pdf').exists())

    def test_leaked_identity_string_fails_validation(self):
        plan = deepcopy(self.plan)
        plan['redactions'] = [r for r in plan['redactions'] if r['text'] != 'alice@example.edu']
        candidate = self.work / 'candidate.pdf'
        cleaner.redact_pdf(self.source, candidate, plan, self.pages)
        with self.assertRaisesRegex(ValueError, 'Identifying text remains'):
            cleaner.check_pdf(self.source, candidate, plan, self.work)

    def test_unrelated_visible_edits_are_rejected(self):
        candidate = self.work / 'candidate.pdf'
        cleaner.redact_pdf(self.source, candidate, self.plan, self.pages)
        changed = self.work / 'changed.pdf'
        with fitz.open(candidate) as doc:
            doc[0].draw_rect(fitz.Rect(70, 205, 400, 225), color=(1, 1, 1), fill=(1, 1, 1))
            doc.save(changed)
        with self.assertRaisesRegex(ValueError, 'outside the redaction'):
            cleaner.check_pdf(self.source, changed, self.plan, self.work)

    def test_whole_identifying_image_is_removed(self):
        image_source = self.work / 'logo-source.pdf'
        with fitz.open() as logo:
            page = logo.new_page(width=200, height=60)
            page.insert_text((10, 30), 'Example University')
            image = page.get_pixmap().tobytes('png')
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_image(fitz.Rect(72, 72, 272, 132), stream=image)
            page.insert_text((72, 220), 'Preserve this scientific content.')
            doc.save(image_source)
        work = self.work / 'image'
        work.mkdir()
        pages = cleaner.inspect_pdf(image_source, work)
        plan = {'redactions': [{'page': 1, 'rect': [71, 71, 273, 133], 'category': 'affiliation', 'text': 'logo'}],
                'forbidden_strings': [], 'uncertain': []}
        candidate = work / 'clean.pdf'
        cleaner.redact_pdf(image_source, candidate, plan, pages)
        cleaner.check_pdf(image_source, candidate, plan, work)
        with fitz.open(candidate) as doc:
            self.assertEqual(doc[0].get_images(), [])
            self.assertIn('scientific content', doc[0].get_text())

    def test_untouched_page_content_stream_is_preserved(self):
        plan = deepcopy(self.plan)
        plan['redactions'] = [r for r in plan['redactions'] if r['page'] == 1]
        candidate = self.work / 'preserve-stream.pdf'
        cleaner.redact_pdf(self.source, candidate, plan, self.pages)
        with fitz.open(self.source) as original, fitz.open(candidate) as clean:
            self.assertEqual(original[1].read_contents(), clean[1].read_contents())

    def test_invisible_author_text_is_removed(self):
        source = self.work / 'hidden.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), 'Hidden Author', render_mode=3)
            page.insert_text((72, 220), 'Scientific content remains.')
            doc.save(source)
        candidate = self.work / 'hidden-clean.pdf'
        plan = {'redactions': [], 'forbidden_strings': ['Hidden Author'], 'uncertain': []}
        cleaner.redact_pdf(source, candidate, plan, self.pages[:1])
        cleaner.check_pdf(source, candidate, plan, self.work)
        with fitz.open(candidate) as doc:
            self.assertNotIn('Hidden Author', doc[0].get_text())
            self.assertIn('Scientific content remains.', doc[0].get_text())


if __name__ == '__main__':
    unittest.main()

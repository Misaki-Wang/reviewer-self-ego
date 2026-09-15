"""Rules-only candidate driver tests; no model or subprocess invocation."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pymupdf as fitz
from utility.anonymize import anonymize_pdf_rules as driver


class RulesDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(driver, 'ROOT', self.root).start()
        self.source = self.root / 'data/pdf/2501.12345/2501.12345v1.pdf'
        self.source.parent.mkdir(parents=True)
        self.output = self.root / 'res/test/candidates/v1.pdf'
        self.report = self.root / 'res/test/reports/v1.json'
        self.paper = {'arxiv_id': '2501.12345', 'title': 'A Scientific Method',
                      'authors': ['Alice Example'], 'versions': [{'version': 1}]}
        with fitz.open() as document:
            page = document.new_page()
            for text, y in [('A Scientific Method', 60), ('Alice Example', 95),
                            ('Example University', 115), ('alice@example.edu', 135),
                            ('Abstract', 190), ('A scientific result remains intact.', 215),
                            ('References', 350), ('Alice Example. An earlier scientific paper.', 375)]:
                page.insert_text((72, y), text)
            document.set_metadata({'author': 'Alice Example'})
            document.save(self.source)
        self.plan = {'redactions': [], 'forbidden_strings': ['alice@example.edu'], 'uncertain': []}
        with fitz.open(self.source) as document:
            for phrase, category in [('Alice Example', 'author'),
                                     ('Example University', 'affiliation'),
                                     ('alice@example.edu', 'contact')]:
                rect = document[0].search_for(phrase)[0]
                self.plan['redactions'].append({'page': 1, 'rect': list(rect),
                                               'category': category, 'text': phrase})

    def execute(self, plan=None):
        with patch.object(driver.rules, 'build_plan', return_value=deepcopy(plan or self.plan)), \
             patch.object(driver.pdf, 'call_cli', side_effect=AssertionError('Codex must not be called')), \
             patch('subprocess.Popen', side_effect=AssertionError('No subprocess allowed')):
            return driver.anonymize(self.source, self.paper, self.output, self.report)

    def test_removes_only_requested_identity_and_preserves_original_and_reference(self):
        before = self.source.read_bytes()
        result = self.execute()
        self.assertEqual(result['status'], 'rules_passed')
        self.assertEqual(result['codex_cli_calls'], 0)
        self.assertEqual(result['model_api_calls'], 0)
        self.assertFalse(result['model_reviewed'])
        self.assertFalse(result['published_to_clean_data'])
        self.assertEqual(before, self.source.read_bytes())
        with fitz.open(self.output) as document:
            text = document[0].get_text()
            self.assertIn('A Scientific Method', text)
            self.assertIn('A scientific result remains intact.', text)
            self.assertIn('Alice Example. An earlier scientific paper.', text)
            self.assertNotIn('alice@example.edu', text)
            self.assertFalse(document.metadata.get('author'))
        self.assertTrue(result['local_validation']['outside_redactions_unchanged'])

    def test_uncertainty_is_retained_and_cached_without_upgrade(self):
        plan = deepcopy(self.plan)
        plan['uncertain'] = ['A potential logo needs inspection']
        result = self.execute(plan)
        self.assertEqual(result['status'], 'needs_manual_review')
        self.assertEqual(result['plan']['uncertain'], plan['uncertain'])
        again = self.execute(plan)
        self.assertTrue(again['cache_hit'])
        self.assertEqual(again['status'], 'needs_manual_review')

    def test_no_reuse_of_codex_or_different_rule_binding(self):
        self.execute()
        previous = json.loads(self.report.read_text())
        previous['binding']['backend'] = 'codex'
        self.report.write_text(json.dumps(previous))
        with self.assertRaises(FileExistsError):
            self.execute()

    def test_local_validation_failure_keeps_candidate_only_in_qa(self):
        with patch.object(driver.pdf, 'check_pdf', side_effect=ValueError('Visible content changed')):
            result = self.execute()
        self.assertEqual(result['status'], 'failed_validation')
        self.assertIn('Visible content changed', result['error'])
        self.assertFalse(self.output.exists())
        self.assertTrue(Path(result['candidate_path']).is_file())

    def test_malformed_uncertainty_is_not_silently_cleared(self):
        plan = deepcopy(self.plan)
        plan['uncertain'] = None
        result = self.execute(plan)
        self.assertEqual(result['status'], 'failed_validation')
        self.assertFalse(self.output.exists())

    def test_mismatched_metadata_rejected_before_writes(self):
        self.paper['arxiv_id'] = '2501.99999'
        with self.assertRaisesRegex(ValueError, 'metadata'):
            self.execute()
        self.assertFalse(self.report.exists())

    def test_original_and_production_output_roots_rejected(self):
        for folder in ('data', 'data_full', 'downloads_papers', 'clean_data'):
            with self.subTest(folder=folder):
                self.output = self.root / folder / 'test.pdf'
                with self.assertRaisesRegex(ValueError, 'original and production'):
                    self.execute()

    def test_output_appearing_during_validation_is_not_overwritten(self):
        original_check = driver.pdf.check_pdf
        def validate_then_create(*args):
            result = original_check(*args)
            self.output.parent.mkdir(parents=True)
            self.output.write_bytes(b'concurrent output')
            return result
        with patch.object(driver.pdf, 'check_pdf', side_effect=validate_then_create):
            result = self.execute()
        self.assertEqual(result['status'], 'failed_validation')
        self.assertEqual(self.output.read_bytes(), b'concurrent output')

    def test_dry_run_makes_no_pdf_or_model_calls(self):
        metadata = self.root / 'paper.json'
        metadata.write_text(json.dumps(self.paper))
        with redirect_stdout(io.StringIO()), \
             patch.object(driver, 'anonymize', side_effect=AssertionError('No authoring')), \
             patch.object(driver.pdf, 'call_cli', side_effect=AssertionError('No Codex')):
            code = driver.main([str(self.source), '--metadata', str(metadata), '--output',
                                str(self.output), '--report', str(self.report), '--dry-run'])
        self.assertEqual(code, 0)
        self.assertFalse(self.report.exists())


if __name__ == '__main__':
    unittest.main()

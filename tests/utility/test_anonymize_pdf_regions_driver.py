"""Regional driver contract: scope, publication, cache, and protected body text."""
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


class RegionalDriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(driver, 'ROOT', self.root).start()
        patch.object(driver.rules, 'REGION_RULES_VERSION', 'anonymize-regions-1', create=True).start()
        patch.object(driver.pdf, 'call_cli', side_effect=AssertionError('No Codex calls')).start()
        patch('subprocess.Popen', side_effect=AssertionError('No subprocess')).start()
        self.source = self.root / 'data/pdf/2501.12345/2501.12345v1.pdf'
        self.source.parent.mkdir(parents=True)
        self.output = self.root / 'res/regions/candidate.pdf'
        self.report = self.root / 'res/regions/report.json'
        self.paper = {'arxiv_id': '2501.12345', 'title': 'A Scientific Method',
                      'authors': ['Alice Example'], 'source_ids': ['iclr2025'],
                      'versions': [{'version': 1}]}
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            for text, y in [('A Scientific Method', 60), ('Alice Example', 90),
                            ('Example University', 110), ('alice@example.edu', 130),
                            ('Abstract', 190), ('Alice Example is cited in this scientific discussion.', 220),
                            ('https://github.com/example/scientific-artifact', 250),
                            ('Acknowledgments', 360), ('Funded by Example Institute.', 385),
                            ('References', 440), ('Alice Example. A prior scientific result.', 465)]:
                page.insert_text((72, y), text, fontsize=11)
            doc.set_metadata({'author': 'Alice Example', 'title': 'A Scientific Method'})
            doc.save(self.source)
        self.plan = {'redactions': [
            {'page': 1, 'rect': [0, 0, 612, 155], 'category': 'other_identity', 'text': ''},
            {'page': 1, 'rect': [65, 345, 510, 405], 'category': 'acknowledgment', 'text': ''}],
            'forbidden_strings': [], 'uncertain': []}
        self.diagnostics = {'regions': [
            {'plan_index': 0, 'region_type': 'title_block', 'source_evidence': {'body_boundary': 178}},
            {'plan_index': 1, 'region_type': 'acknowledgment', 'source_evidence': {'next_heading': 'References'}}],
                            'source_layout_profile': {'source_family': 'iclr', 'variant': 'single_column'},
                            'scope': 'frontmatter-margins-credits-v1', 'unresolved': []}

    def execute(self, **kwargs):
        def planner(source, paper, *, diagnostics):
            diagnostics.update(deepcopy(self.diagnostics))
            return deepcopy(self.plan)
        with patch.object(driver.rules, 'build_region_plan', side_effect=planner, create=True), \
             patch.object(driver.rules, 'build_plan', side_effect=AssertionError('Legacy body planner must not run')):
            return driver.anonymize(self.source, self.paper, self.output, self.report, **kwargs)

    def test_default_clears_regions_and_preserves_body_names_urls_and_references(self):
        before = self.source.read_bytes()
        result = self.execute()
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        self.assertEqual(result['binding']['scope'], 'frontmatter-margins-credits-v1')
        self.assertEqual(result['binding']['redaction_mode'], 'filled-regions-v1')
        self.assertEqual(result['planner_diagnostics'], self.diagnostics)
        self.assertEqual(set(result['plan']), {'redactions', 'forbidden_strings', 'uncertain'})
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(result['published_to_clean_data'])
        with fitz.open(self.output) as doc:
            text = doc[0].get_text()
            self.assertNotIn('A Scientific Method', text)
            self.assertNotIn('alice@example.edu', text)
            self.assertNotIn('Funded by Example Institute', text)
            self.assertIn('Alice Example is cited in this scientific discussion.', text)
            self.assertIn('https://github.com/example/scientific-artifact', text)
            self.assertIn('Alice Example. A prior scientific result.', text)

    def test_cache_preserves_regional_scope_and_diagnostics(self):
        result = self.execute()
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with patch.object(driver.rules, 'build_region_plan', side_effect=AssertionError('No replan'), create=True):
            cached = driver.anonymize(self.source, self.paper, self.output, self.report)
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(cached['planner_diagnostics'], self.diagnostics)
        self.assertEqual(cached['binding'], result['binding'])

    def test_scope_change_cannot_reuse_regional_candidate_as_legacy(self):
        self.assertEqual(self.execute()['status'], 'rules_passed')
        with self.assertRaises(FileExistsError):
            driver.anonymize(self.source, self.paper, self.output, self.report, scope='identifiers')

    def test_unresolved_region_remains_review_required_after_safe_masks(self):
        self.plan['uncertain'] = ['A footer boundary is unresolved']
        self.diagnostics['unresolved'] = [{'page': 1, 'region': 'footer'}]
        result = self.execute()
        self.assertEqual(result['status'], 'needs_manual_review', result.get('error'))
        self.assertEqual(result['plan']['uncertain'], self.plan['uncertain'])
        self.assertEqual(result['planner_diagnostics']['unresolved'], self.diagnostics['unresolved'])
        self.assertTrue(self.output.exists())
        self.assertFalse(result['published_to_clean_data'])

    def test_structured_unresolved_alone_cannot_upgrade_acceptance(self):
        self.diagnostics['unresolved'] = [{'page': 1, 'region': 'title'}]
        result = self.execute()
        self.assertEqual(result['status'], 'needs_manual_review', result.get('error'))

    def test_region_mode_rejects_legacy_object_evidence_before_output_writes(self):
        with self.assertRaises(ValueError):
            self.execute(image_manifest=Path('unused.json'), image_ocr_evidence=[Path('unused-ocr.json')])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report.exists())

    def test_blank_validation_failure_does_not_publish_candidate(self):
        with patch.object(driver.pdf, 'check_pdf', side_effect=ValueError('Region is not blank')):
            result = self.execute()
        self.assertEqual(result['status'], 'failed_validation')
        self.assertIn('Region is not blank', result['error'])
        self.assertFalse(self.output.exists())
        self.assertEqual(result['planner_diagnostics'], self.diagnostics)

    def test_cli_dry_run_defaults_to_regions_without_writes(self):
        metadata = self.root / 'paper.json'
        metadata.write_text(json.dumps(self.paper))
        out = io.StringIO()
        with redirect_stdout(out):
            status = driver.main([str(self.source), '--metadata', str(metadata), '--output', str(self.output),
                                  '--report', str(self.report), '--dry-run'])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out.getvalue())['scope'], 'regions')
        self.assertFalse(self.report.exists())


if __name__ == '__main__':
    unittest.main()

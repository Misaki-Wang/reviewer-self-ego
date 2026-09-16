"""Synthetic rule-plan tests; no corpus mutation and no model or network calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz
from utility.anonymize import anonymize_rules as rules
from utility.anonymize import anonymize_pdf as pdf_helpers


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'source.pdf'
        self.paper = {'title': 'A Scientific Paper', 'authors': ['Alice Example'],
                      'arxiv_id': '2501.12345'}

    def make_pdf(self, extra=(), author_lines=None, title=None, abstract=True):
        title = self.paper['title'] if title is None else title
        author_lines = [('Alice Example', 90), ('Example University', 110),
                        ('alice@example.edu', 130)] if author_lines is None else author_lines
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), title, fontsize=16)
            for text, y in author_lines:
                page.insert_text((72, y), text)
            if abstract:
                page.insert_text((72, 170), 'Abstract', fontsize=12)
            page.insert_text((72, 200), 'Our scientific result is x = 42.')
            for item in extra:
                text, y, *x = item
                page.insert_text((x[0] if x else 72, y), text)
            doc.save(self.source)
        return self.source

    def plan(self):
        plan = rules.build_plan(self.source, self.paper)
        for region in plan['redactions']:
            self.assertIn(region['category'], pdf_helpers.CATEGORIES)
        return plan

    def removed_text(self, plan):
        return '\n'.join(item['text'] for item in plan['redactions'])

    def apply(self, plan):
        with fitz.open(self.source) as doc:
            for item in plan['redactions']:
                doc[item['page'] - 1].add_redact_annot(item['rect'])
            for page in doc:
                page.apply_redactions(images=2, graphics=1, text=0)
            return '\n'.join(page.get_text() for page in doc)

    def test_source_profile_diagnostics_do_not_change_the_plan(self):
        self.paper['source_ids']=['acl2025']
        self.paper['authors']=['Mira Chen']
        self.make_pdf(author_lines=[('Mira Chen',90),('North University',110),('mira@north.edu',130)])
        plain=rules.build_plan(self.source,self.paper)
        explicit_none=rules.build_plan(self.source,self.paper,diagnostics=None)
        diagnostic={'caller_note':'retained'}
        observed=rules.build_plan(self.source,self.paper,diagnostics=diagnostic)
        self.assertEqual(plain,observed)
        self.assertEqual(plain,explicit_none)
        self.assertEqual(set(observed),{'redactions','forbidden_strings','uncertain'})
        self.assertEqual(diagnostic['caller_note'],'retained')
        self.assertEqual(diagnostic['source_layout_profile']['selected_family'],'acl_emnlp')
        self.assertEqual(diagnostic['profile_schema'],'source-layout-1')

    def test_source_profile_diagnostics_replace_stale_caller_evidence(self):
        self.paper['source_ids']=['acl2025']
        self.paper['authors']=['Mira Chen']
        self.make_pdf(author_lines=[('Mira Chen',90),('North University',110),('mira@north.edu',130)])
        diagnostic={}
        rules.build_plan(self.source,self.paper,diagnostics=diagnostic)
        self.paper['source_ids']=['unknown_source']
        rules.build_plan(self.source,self.paper,diagnostics=diagnostic)
        self.assertIsNone(diagnostic['source_layout_profile']['selected_family'])
        self.assertEqual(diagnostic['profile_id'],'conservative')
        with self.assertRaises(ValueError):
            rules.build_plan(self.source,None,diagnostics=diagnostic)
        self.assertNotIn('source_layout_profile',diagnostic)

    def test_dual_source_profile_order_does_not_change_plan(self):
        self.paper['authors']=['Mira Chen']
        self.make_pdf(author_lines=[('Mira Chen',90),('North University',110),('mira@north.edu',130)])
        self.paper['source_ids']=['icml2026','tmlr_2025plus']
        first={}
        plan=rules.build_plan(self.source,self.paper,diagnostics=first)
        self.paper['source_ids'].reverse()
        second={}
        other=rules.build_plan(self.source,self.paper,diagnostics=second)
        self.assertEqual(plan,other)
        self.assertEqual(first,second)
        self.assertIsNone(first['source_layout_profile']['selected_family'])

    def test_bounded_authors_affiliations_and_email(self):
        self.make_pdf()
        before = self.source.read_bytes()
        plan = self.plan()
        self.assertEqual(set(plan), {'redactions', 'forbidden_strings', 'uncertain'})
        self.assertFalse(plan['uncertain'])
        clean = self.apply(plan)
        self.assertNotIn('Alice Example', clean)
        self.assertNotIn('Example University', clean)
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('A Scientific Paper', clean)
        self.assertIn('Abstract', clean)
        self.assertIn('Our scientific result is x = 42.', clean)
        self.assertEqual(before, self.source.read_bytes())
        self.assertIn('alice@example.edu', plan['forbidden_strings'])

    def test_reference_author_and_email_are_preserved(self):
        self.make_pdf([('References', 300),
                       ('1 Alice Example. A useful method. alice@example.edu', 760)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertIn('1 Alice Example. A useful method. alice@example.edu', clean)
        self.assertNotIn('alice@example.edu', plan['forbidden_strings'])
        self.assertNotIn('Alice Example', plan['forbidden_strings'])

    def test_scientific_author_mentions_are_not_globally_removed(self):
        self.make_pdf([('We compare the Alice Example method with our theorem.', 240)])
        clean = self.apply(self.plan())
        self.assertIn('We compare the Alice Example method with our theorem.', clean)

    def test_wrapped_full_name_is_matched(self):
        self.make_pdf(author_lines=[('Alice', 90), ('Example', 105),
                                    ('Example University', 125)])
        plan = self.plan()
        self.assertFalse(plan['uncertain'])
        clean = self.apply(plan)
        self.assertNotIn('Alice', clean)
        self.assertNotIn('Example', clean)
        self.assertIn('A Scientific Paper', clean)

    def test_author_metadata_dict_and_diacritics(self):
        self.paper['authors'] = [{'given': 'Alice', 'family': 'Éxample'}]
        self.make_pdf()
        self.assertIn('Alice Example', self.removed_text(self.plan()))

    def test_author_name_in_title_is_preserved(self):
        self.paper['title'] = 'Alice Example and Scientific Results'
        self.make_pdf()
        clean = self.apply(self.plan())
        self.assertIn(self.paper['title'], clean)
        self.assertEqual(clean.count('Alice Example'), 1)

    def test_stale_title_uses_dominant_structural_title(self):
        self.make_pdf(title='A Different Scientific Title')
        plan = self.plan()
        self.assertFalse(plan['uncertain'])
        self.assertIn('Alice Example', self.removed_text(plan))
        self.assertIn('A Different Scientific Title', self.apply(plan))

    def test_missing_author_metadata_is_explicit(self):
        self.make_pdf()
        self.paper['authors'] = []
        plan = self.plan()
        self.assertTrue(any('Author metadata' in reason for reason in plan['uncertain']))
        self.assertNotIn('Alice Example', self.removed_text(plan))

    def test_unmatched_authors_are_explicit(self):
        self.make_pdf(author_lines=[('Anonymous Authors', 90)])
        plan = self.plan()
        self.assertTrue(any('no metadata author' in reason for reason in plan['uncertain']))

    def test_scanned_or_empty_page_requires_manual_review(self):
        with fitz.open() as doc:
            doc.new_page()
            doc.save(self.source)
        plan = self.plan()
        self.assertTrue(any('no extractable text' in reason for reason in plan['uncertain']))
        self.assertEqual(plan['redactions'], [])

    def test_missing_body_boundary_does_not_guess_affiliation_block(self):
        self.make_pdf(abstract=False)
        plan = self.plan()
        self.assertTrue(any('no reliable abstract' in reason for reason in plan['uncertain']))
        self.assertIn('Our scientific result is x = 42.', self.apply(plan))

    def test_safe_acknowledgments_end_at_references(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank our funding agency.', 285),
                       ('References', 320), ('Alice Example. A useful scientific paper.', 340)])
        clean = self.apply(self.plan())
        self.assertNotIn('Acknowledgments', clean)
        self.assertNotIn('We thank our funding agency.', clean)
        self.assertIn('References', clean)
        self.assertIn('Alice Example. A useful scientific paper.', clean)
        self.assertIn('Our scientific result is x = 42.', clean)

    def test_acknowledgments_without_boundary_remain_unresolved(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank our funding agency.', 285)])
        plan = self.plan()
        self.assertTrue(any('no safe same-page end' in reason for reason in plan['uncertain']))
        self.assertIn('Acknowledgments', self.apply(plan))
        self.assertIn('We thank our funding agency.', self.apply(plan))

    def test_multicolumn_acknowledgments_do_not_delete_neighboring_science(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank the agency.', 285),
                       ('Scientific theorem x = 42.', 285, 350), ('References', 320)])
        plan = self.plan()
        self.assertFalse(any('column ambiguity' in reason for reason in plan['uncertain']))
        self.assertIn('Scientific theorem x = 42.', self.apply(plan))
        self.assertNotIn('We thank the agency.', self.apply(plan))

    def test_arxiv_margin_stamp_removed_but_reference_is_preserved(self):
        self.make_pdf([('arXiv:2501.12345v1 [cs.LG] 1 Jan 2025', 30),
                       ('References', 300), ('Alice Example. arXiv:2501.12345v1', 330)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('[cs.LG]', clean)
        self.assertIn('Alice Example. arXiv:2501.12345v1', clean)
        self.assertNotIn('arXiv:2501.12345v1', plan['forbidden_strings'])

    def test_author_links_and_orcid_removed_only_in_identity_context(self):
        self.make_pdf(author_lines=[('Alice Example', 90),
                                    ('https://github.com/alice/example', 110),
                                    ('ORCID: 0000-0001-2345-6789', 130)],
                      extra=[('References', 300),
                             ('See https://github.com/alice/example for cited data.', 330)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('ORCID:', clean)
        self.assertIn('See https://github.com/alice/example for cited data.', clean)
        self.assertNotIn('https://github.com/alice/example', plan['forbidden_strings'])

    def test_identifier_inside_body_prose_is_not_removed(self):
        self.make_pdf([('The example input string alice@example.edu is part of the dataset.', 260)])
        plan = self.plan()
        self.assertTrue(any('identifier within prose' in reason for reason in plan['uncertain']))
        self.assertIn('The example input string alice@example.edu is part of the dataset.',
                      self.apply(plan))

    def test_unknown_header_line_is_preserved_and_reported(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Unrecognized Identity Group', 110)])
        plan = self.plan()
        self.assertTrue(any('unclassified author-block' in reason for reason in plan['uncertain']))
        self.assertIn('Unrecognized Identity Group', self.apply(plan))

    def test_every_redaction_is_in_bounds_and_schema_compatible(self):
        self.make_pdf([('Published as a conference paper at ICLR 2026', 30)])
        plan = self.plan()
        with fitz.open(self.source) as doc:
            for item in plan['redactions']:
                self.assertEqual(set(item), {'page', 'rect', 'category', 'text'})
                rect = fitz.Rect(item['rect'])
                self.assertTrue(doc[item['page'] - 1].rect.contains(rect))
                self.assertFalse(rect.is_empty)
        self.assertIn('venue', [item['category'] for item in plan['redactions']])

    def test_name_inside_unbounded_body_is_preserved(self):
        self.make_pdf(abstract=False,
                      extra=[('We compare the Alice Example method.', 220)])
        plan = self.plan()
        self.assertIn('We compare the Alice Example method.', self.apply(plan))

    def test_name_in_header_prose_is_not_treated_as_byline(self):
        self.make_pdf(author_lines=[('Inspired by Alice Example and scientific findings.', 90)])
        plan = self.plan()
        self.assertTrue(any('unclassified text' in reason for reason in plan['uncertain']))
        self.assertIn('Inspired by Alice Example and scientific findings.', self.apply(plan))

    def test_attached_numeric_author_markers_are_matched(self):
        self.make_pdf(author_lines=[('Alice Example1', 90), ('Example University', 110)])
        plan = self.plan()
        self.assertFalse(plan['uncertain'])
        self.assertNotIn('Alice Example1', self.apply(plan))

    def test_scientific_margin_phrase_with_author_name_is_preserved(self):
        self.make_pdf(extra=[('Alice Example method remains a scientific baseline.', 750)])
        plan = self.plan()
        self.assertIn('Alice Example method remains a scientific baseline.', self.apply(plan))

    def test_partial_author_metadata_matches_are_reported(self):
        self.make_pdf()
        self.paper['authors'].append('Bob Scientist')
        plan = self.plan()
        self.assertTrue(any('some metadata authors' in reason for reason in plan['uncertain']))

    def test_scientific_email_prose_in_margin_is_preserved(self):
        self.make_pdf(extra=[('The example input alice@example.edu is scientific data.', 750)])
        plan = self.plan()
        self.assertIn('The example input alice@example.edu is scientific data.', self.apply(plan))

    def test_acknowledgments_stop_before_numbered_methods(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank our supporters.', 280),
                       ('2 Methods', 310),
                       ('The central scientific result is preserved by our method.', 330),
                       ('References', 370)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('We thank our supporters.', clean)
        self.assertIn('2 Methods', clean)
        self.assertIn('The central scientific result is preserved by our method.', clean)
        self.assertFalse(any('unclassified possible section heading' in reason
                             for reason in plan['uncertain']))

    def test_acknowledgments_stop_before_unknown_unnumbered_heading(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank our supporters.', 280),
                       ('Experimental details', 310),
                       ('The scientific protocol must remain intact.', 330),
                       ('References', 370)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertIn('Experimental details', clean)
        self.assertIn('The scientific protocol must remain intact.', clean)
        self.assertFalse(any('unclassified possible section heading' in reason
                             for reason in plan['uncertain']))

    def test_email_prefixed_scientific_prose_is_preserved(self):
        self.make_pdf([('2 Methods', 260),
                       ('Email: alice@example.org is the positive training label.', 280),
                       ('The literal field above defines the benchmark.', 310)])
        plan = self.plan()
        self.assertIn('Email: alice@example.org is the positive training label.', self.apply(plan))
        self.assertTrue(any('identifier within prose' in reason for reason in plan['uncertain']))

    def test_standalone_body_email_is_preserved_for_manual_review(self):
        self.make_pdf([('2 Methods', 260), ('alice@example.org', 280)])
        plan = self.plan()
        self.assertIn('alice@example.org', self.apply(plan))
        self.assertTrue(any('identifier within prose' in reason for reason in plan['uncertain']))

    def test_exact_margin_contact_is_removed_without_following_prose(self):
        self.make_pdf([('Email: alice@example.org', 760)])
        self.assertNotIn('alice@example.org', self.apply(self.plan()))

    def test_scientific_github_resources_and_code_examples_are_preserved(self):
        self.make_pdf([('2 Methods', 260),
                       ('Code: https://github.com/third-party/baseline defines the task.', 280),
                       ('https://github.com/third-party/baseline', 310),
                       ('https://github.com/third-party/dataset', 760)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertIn('Code: https://github.com/third-party/baseline defines the task.', clean)
        self.assertIn('https://github.com/third-party/dataset', clean)
        self.assertTrue(any('URL outside the identified author block' in reason
                            for reason in plan['uncertain']))

    def test_unlabelled_margin_email_literal_requires_manual_review(self):
        self.make_pdf([('alice@example.org', 760)])
        plan = self.plan()
        self.assertIn('alice@example.org', self.apply(plan))
        self.assertTrue(any('identifier within prose' in reason for reason in plan['uncertain']))

    def test_acknowledgments_stop_before_unknown_colon_heading(self):
        self.make_pdf([('Acknowledgments', 260), ('We thank our supporters.', 280),
                       ('Methods:', 310), ('The scientific content must remain.', 330),
                       ('References', 370)])
        plan = self.plan()
        self.assertIn('Methods:', self.apply(plan))
        self.assertIn('The scientific content must remain.', self.apply(plan))
        self.assertFalse(any('unclassified possible section heading' in reason
                             for reason in plan['uncertain']))

    def test_marker_only_text_never_satisfies_author_predicate(self):
        for marker in ('', '…', '……', '⋯', '⋯⋯', '...', '1', '42', '*', 'and', 'et al'):
            with self.subTest(marker=marker):
                self.assertFalse(rules._byline_only(marker, ['alice example']))
                self.assertFalse(rules._byline_only(marker, ['']))
        self.assertTrue(rules._byline_only('Alice Example1', ['alice example']))

    def test_preabstract_figure_ellipses_and_numbers_are_preserved(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 110), 'Example University')
            for text, y, font in [('……', 190, 'china-s'), ('…', 230, 'china-s'),
                                  ('...', 270, 'helv'), ('1', 310, 'helv'),
                                  ('42', 350, 'helv')]:
                page.insert_text((260, y), text, fontname=font)
            page.insert_text((72, 400), 'Abstract', fontsize=12)
            page.insert_text((72, 430), 'Our scientific result is x = 42.')
            doc.save(self.source)
        with fitz.open(self.source) as doc:
            figure_lines = [line for block in doc[0].get_text('dict')['blocks']
                            if block['type'] == 0 for line in block['lines']
                            if 150 < line['bbox'][1] < 380]
        self.assertEqual(len(figure_lines), 5)
        plan = self.plan()
        for line in figure_lines:
            self.assertFalse(any(fitz.Rect(item['rect']).intersects(fitz.Rect(line['bbox']))
                                 for item in plan['redactions']))
        self.assertEqual([item['text'] for item in plan['redactions']
                          if item['category'] == 'author'], ['Alice Example'])
        clean = self.apply(plan)
        for line in figure_lines:
            self.assertIn(''.join(span['text'] for span in line['spans']), clean)
        self.assertIn(self.paper['title'], clean)
        self.assertIn('Abstract', clean)
        self.assertIn('Our scientific result is x = 42.', clean)
        self.assertTrue(any('unclassified author-block line' in reason
                            for reason in plan['uncertain']))
        self.assertEqual(rules.RULES_VERSION, 'anonymize-rules-29')

    def test_deterministic_without_process_or_network_calls(self):
        self.make_pdf()
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess permitted')), \
                patch('socket.socket', side_effect=AssertionError('No network permitted')):
            self.assertEqual(self.plan(), self.plan())





class VersionThreeTests(unittest.TestCase):
    setUp = RuleTests.setUp
    make_pdf = RuleTests.make_pdf
    plan = RuleTests.plan
    apply = RuleTests.apply
    def test_structural_title_containing_author_name_is_preserved(self):
        self.make_pdf(title='Alice Example and Bayesian Inference')
        clean = self.apply(self.plan())
        self.assertIn('Alice Example and Bayesian Inference', clean)
        self.assertEqual(clean.count('Alice Example'), 1)

    def test_hyphenated_wrapped_title_and_name_collision(self):
        self.paper['title'] = 'Scientific Generalization by Alice Example'
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), 'Scientific Generali-', fontsize=16)
            page.insert_text((72, 78), 'zation by Alice Example', fontsize=16)
            page.insert_text((72, 110), 'Alice Example')
            page.insert_text((72, 130), 'Example University')
            page.insert_text((72, 170), 'Abstract', fontsize=12)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertIn('Scientific Generali-', clean)
        self.assertIn('zation by Alice Example', clean)
        self.assertEqual(clean.count('Alice Example'), 1)

    def test_added_author_on_anchored_row_is_removed(self):
        self.make_pdf(author_lines=[('Alice Example, Bob Scientist', 90),
                                    ('Example University', 110)])
        clean = self.apply(self.plan())
        self.assertNotIn('Bob Scientist', clean)
        self.assertIn('A Scientific Paper', clean)

    def test_isolated_author_shaped_row_is_preserved_and_flagged(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Bob Scientist', 105),
                                    ('Example University', 125)])
        plan = self.plan()
        self.assertIn('Bob Scientist', self.apply(plan))
        self.assertTrue(any('unclassified author-block' in item for item in plan['uncertain']))

    def test_entirely_different_roster_is_not_guessed(self):
        self.make_pdf(author_lines=[('Bob Scientist', 90), ('Example University', 110)])
        plan = self.plan()
        self.assertIn('Bob Scientist', self.apply(plan))
        self.assertTrue(any('no metadata author' in item for item in plan['uncertain']))

    def test_grouped_email_inside_byline(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('{alice,bob}@example.edu', 130)])
        plan = self.plan()
        self.assertNotIn('{alice,bob}@example.edu', self.apply(plan))
        self.assertIn('{alice,bob}@example.edu', rules.detected_identity_strings(plan))

    def test_grouped_email_body_example_preserved(self):
        self.make_pdf(extra=[('2 Methods', 260), ('{alice,bob}@example.edu', 280),
                             ('Email: {alice,bob}@example.edu is a training example.', 300)])
        clean = self.apply(self.plan())
        self.assertIn('{alice,bob}@example.edu', clean)
        self.assertIn('training example.', clean)

    def test_preabstract_epigraph_stops_affiliation_keyword_expansion(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Inspired by Alice Example', 125),
                                    ('Oxford University', 145)])
        clean = self.apply(self.plan())
        self.assertIn('Inspired by Alice Example', clean)
        self.assertIn('Oxford University', clean)

    def test_preabstract_figure_keyword_after_gap_preserved(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Oxford University', 155)])
        self.assertIn('Oxford University', self.apply(self.plan()))

    def test_corresponding_author_footnote(self):
        self.make_pdf(extra=[('*Corresponding author: Alice Example, alice@example.edu', 720)])
        clean = self.apply(self.plan())
        self.assertNotIn('Corresponding author:', clean)
        self.assertIn('Our scientific result is x = 42.', clean)

    def test_corresponding_author_scientific_footnote_preserved(self):
        self.make_pdf(extra=[('Corresponding author labels are the target variable.', 720)])
        self.assertIn('Corresponding author labels are the target variable.', self.apply(self.plan()))

    def test_equal_contribution_footnote(self):
        self.make_pdf(extra=[('*Equal contribution. Work done during an internship.', 720)])
        self.assertNotIn('Equal contribution.', self.apply(self.plan()))

    def test_footnote_continuation_stops_at_neighbor_science(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(72, 660, 500, 750),
                                '*Corresponding author: Alice Example\nalice@example.edu\n'
                                '2 Scientific theorem x = 42 must survive.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('Scientific theorem x = 42', clean)

    def test_explicit_code_and_project_links(self):
        self.make_pdf(extra=[('Code: https://github.com/alice/project', 260),
                             ('Project page: https://example.edu/project', 280),
                             ('Our code is available at https://github.com/alice/project', 300)])
        clean = self.apply(self.plan())
        # The later explicit release establishes this exact earlier locator.
        self.assertIn('Code:', clean)
        self.assertNotIn('https://github.com/alice/project', clean)
        self.assertIn('Project page: https://example.edu/project', clean)
        self.assertNotIn('Our code is available at https://github.com/alice/project', clean)

    def test_owned_link_wrapped_after_label(self):
        self.make_pdf(extra=[('Our code is available at:', 260),
                             ('https://github.com/alice/project', 275)])
        self.assertNotIn('https://github.com/alice/project', self.apply(self.plan()))

    def test_bibliography_owned_sounding_link_preserved(self):
        self.make_pdf(extra=[('References', 260), ('Code: https://github.com/alice/project', 280)])
        self.assertIn('Code: https://github.com/alice/project', self.apply(self.plan()))

    def test_wrapped_grant_number_is_not_section_boundary(self):
        self.make_pdf(extra=[('Acknowledgments', 260), ('This work was supported by grant', 280),
                             ('123456 and the Example Foundation.', 295),
                             ('and by generous support from', 310),
                             ('Example University.', 325), ('2 Methods', 350),
                             ('The theorem must survive.', 370)])
        clean = self.apply(self.plan())
        self.assertNotIn('123456', clean)
        self.assertNotIn('generous support', clean)
        self.assertIn('2 Methods', clean)
        self.assertIn('The theorem must survive.', clean)

    def test_bold_funding_text_is_not_boundary(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((72, 260), 'Acknowledgments', fontname='hebo')
            page.insert_text((72, 280), 'This work was supported by')
            page.insert_text((72, 295), 'Example Foundation', fontname='hebo')
            page.insert_text((72, 310), 'under grant 123456.')
            page.insert_text((72, 340), 'Experimental details', fontname='hebo')
            page.insert_text((72, 360), 'The science must remain.')
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Example Foundation', clean)
        self.assertNotIn('123456', clean)
        self.assertIn('Experimental details', clean)
        self.assertIn('The science must remain.', clean)

    def test_acknowledgment_page_end_not_unbounded_into_next_page(self):
        self.make_pdf(extra=[('Acknowledgments', 700), ('We thank Example University.', 720)])
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 100), 'Theorem 1. All scientific claims remain intact.')
            doc.save(self.source.with_name('expanded.pdf'))
        self.source = self.source.with_name('expanded.pdf')
        plan = self.plan()
        self.assertIn('Theorem 1.', self.apply(plan))
        self.assertTrue(any('no safe same-page end' in item for item in plan['uncertain']))

    def test_preprint_notice_removed_only_in_margin(self):
        self.make_pdf(extra=[('Preprint. Work in progress.', 30),
                             ('Preprint examples are a category in our dataset.', 260)])
        clean = self.apply(self.plan())
        self.assertNotIn('Preprint. Work in progress.', clean)
        self.assertIn('Preprint examples are a category in our dataset.', clean)

    def test_unknown_ack_boundary_preserves_science(self):
        self.make_pdf(extra=[('Acknowledgments', 260), ('We thank our supporters.', 280),
                             ('Dataset construction', 310),
                             ('The scientific sampling protocol uses x = 42.', 330),
                             ('References', 370)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertIn('Dataset construction', clean)
        self.assertIn('The scientific sampling protocol uses x = 42.', clean)
        self.assertTrue(any('possible section heading' in reason for reason in plan['uncertain']))

    def test_work_done_physics_footnote_is_preserved(self):
        self.make_pdf(extra=[('Work done by gravity is m g h.', 650)])
        self.assertIn('Work done by gravity is m g h.', self.apply(self.plan()))

    def test_titlecase_epigraph_is_not_byline_anchor(self):
        self.make_pdf(author_lines=[('In Memory Of Alice Example', 90)])
        self.assertIn('In Memory Of Alice Example', self.apply(self.plan()))

    def test_bare_code_label_does_not_establish_ownership(self):
        self.make_pdf(extra=[('Code: https://github.com/third-party/baseline', 280)])
        plan = self.plan()
        self.assertIn('Code: https://github.com/third-party/baseline', self.apply(plan))
        self.assertTrue(any('URL outside' in reason for reason in plan['uncertain']))

    def test_following_author_epigraph_is_preserved(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Following Alice Example', 105)])
        self.assertIn('Following Alice Example', self.apply(self.plan()))

    def test_ack_heading_with_article_is_preserved(self):
        self.make_pdf(extra=[('Acknowledgments', 260), ('We thank our supporters.', 280),
                             ('The evaluation benchmark', 310),
                             ('The science must remain.', 330), ('References', 370)])
        clean = self.apply(self.plan())
        self.assertIn('The evaluation benchmark', clean)
        self.assertIn('The science must remain.', clean)

    def test_organization_fragment_requires_identified_block(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_textbox(fitz.Rect(72, 105, 450, 150),
                                'Project EXAMPLE\nExample University', fontsize=11)
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Project EXAMPLE', clean)
        self.assertNotIn('Example University', clean)

    def test_acronym_affiliation_in_identified_block(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Department of Chemistry, Example University', 110),
                                    ('LABS', 125), ('alice@example.edu', 140)])
        self.assertNotIn('LABS', self.apply(self.plan()))

    def test_diagram_project_without_affiliation_evidence_preserved(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Project EXAMPLE', 110),
                                    ('https://example.edu/scientific-model', 130)])
        clean = self.apply(self.plan())
        self.assertIn('Project EXAMPLE', clean)
        self.assertIn('https://example.edu/scientific-model', clean)

    def test_bounded_header_explicit_code_link(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Code: https://github.com/alice/project', 130)])
        self.assertNotIn('https://github.com/alice/project', self.apply(self.plan()))

    def test_correspondence_and_scientific_prose_on_same_line(self):
        self.make_pdf(extra=[('Correspondence to Alice Example <alice@example.edu>. A demo reproduces the results.', 720)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('Correspondence to Alice Example', clean)
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('A demo reproduces the results.', clean)
        self.assertIn('alice@example.edu', plan['forbidden_strings'])

    def test_named_corresponding_author_footnote_with_marker(self):
        self.make_pdf(extra=[('*Alice Example (alice@example.edu) is the corresponding author of the paper.', 720)])
        self.assertNotIn('is the corresponding author', self.apply(self.plan()))

    def test_marker_only_corresponding_role_is_identity(self):
        self.make_pdf(extra=[('*Corresponding author', 720)])
        self.assertNotIn('Corresponding author', self.apply(self.plan()))

    def test_intern_affiliation_note_is_identity(self):
        self.make_pdf(extra=[('*Interns at Example University; corresponding author.', 720)])
        self.assertNotIn('Interns at Example University', self.apply(self.plan()))

    def test_unmarked_scientific_footnote_after_contact_survives(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(72, 660, 500, 750),
                                '*Corresponding author: Alice Example\nalice@example.edu\n'
                                'The theorem x = 42 must survive.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertIn('The theorem x = 42', clean)
        self.assertNotIn('alice@example.edu', clean)

    def test_third_party_margin_identifiers_preserved(self):
        self.make_pdf(extra=[('arXiv:2501.98765v1 cited benchmark', 720),
                             ('DOI: 10.1234/scientific.baseline', 760)])
        clean = self.apply(self.plan())
        self.assertIn('arXiv:2501.98765v1 cited benchmark', clean)
        self.assertIn('DOI: 10.1234/scientific.baseline', clean)

    def test_page_end_ack_before_next_page_references_is_complete(self):
        self.make_pdf(extra=[('Acknowledgments', 690),
                             ('This work was sponsored by Example Foundation.', 710), ('1', 760)])
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 30), 'Published as a conference paper at ICLR 2026')
            page.insert_text((72, 90), 'References', fontsize=12)
            page.insert_text((72, 120), 'Alice Example. A cited scientific method.')
            doc.save(self.source.with_name('expanded.pdf'))
        self.source = self.source.with_name('expanded.pdf')
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('sponsored by Example Foundation', clean)
        self.assertNotIn('Acknowledgments', clean)
        self.assertIn('References', clean)
        self.assertIn('Alice Example. A cited scientific method.', clean)
        self.assertFalse(any('no safe same-page end' in reason for reason in plan['uncertain']))

    def make_release_pdf(self, body, annotations=()):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(72, 230, 540, 650), body, fontsize=11)
            for visible, uri in annotations:
                for rect in page.search_for(visible):
                    page.insert_link({'kind': fitz.LINK_URI, 'from': rect, 'uri': uri})
            doc.save(self.source)

    def test_release_url_only_preserves_scientific_sentence(self):
        self.make_release_pdf('The method improves speed by 13 times. Code is available\n'
                              'at https://github.com/example/project.')
        clean = self.apply(self.plan())
        self.assertIn('The method improves speed by 13 times.', clean)
        self.assertNotIn('https://github.com/example/project', clean)
        self.assertIn('Code is available', clean)

    def test_release_wrapped_url_uses_complete_uri_evidence(self):
        uri = 'https://github.com/example/project'
        self.make_release_pdf('The code is released at https://github.\n'
                              'com/example/project and preserves the scientific result.',
                              [('https://github.', uri), ('com/example/project', uri)])
        clean = self.apply(self.plan())
        self.assertNotIn('https://github.', clean)
        self.assertNotIn('com/example/project', clean)
        self.assertIn('and preserves the scientific result.', clean)

    def test_release_multiple_urls_only_remove_literal_links(self):
        first = 'https://github.com/example/first'
        second = 'https://github.com/example/second'
        self.make_release_pdf('The scientific result is exact. The code is released\n'
                              'at ' + first + ' and is also released as\n'
                              'a part of ' + second + '.', [(first,first),(second,second)])
        clean = self.apply(self.plan())
        self.assertIn('The scientific result is exact.', clean)
        self.assertNotIn(first, clean)
        self.assertNotIn(second, clean)

    def test_demo_for_this_paper_release_link(self):
        uri = 'https://github.com/example/demo'
        self.make_release_pdf('A demo sufficient to reproduce the results in\n'
                              'the paper within minutes is available at: ' + uri,
                              [(uri,uri)])
        clean = self.apply(self.plan())
        self.assertNotIn(uri, clean)
        self.assertIn('A demo sufficient to reproduce the results in', clean)

    def test_linked_method_label_is_never_removed_by_target_uri(self):
        uri = 'https://github.com/example/Method'
        self.make_release_pdf('The code is released at Method.\n'
                              'Method is also a scientific baseline.', [('Method',uri)])
        clean = self.apply(self.plan())
        self.assertEqual(clean.count('Method'), 2)

    def test_third_party_release_description_is_preserved(self):
        uri = 'https://github.com/third-party/baseline'
        self.make_release_pdf('The baseline code is available at ' + uri, [(uri,uri)])
        self.assertIn(uri, self.apply(self.plan()))

    def test_literal_release_example_is_preserved(self):
        uri = 'https://github.com/example/literal'
        self.make_release_pdf('The literal string is: Code is available at ' + uri, [(uri,uri)])
        self.assertIn(uri, self.apply(self.plan()))

    def test_release_does_not_cross_source_text_blocks(self):
        self.make_pdf(extra=[('The code is released at:', 260),
                             ('https://github.com/third-party/other', 350)])
        self.assertIn('https://github.com/third-party/other', self.apply(self.plan()))

    def test_release_does_not_reuse_anchor_after_new_sentence(self):
        own = 'https://github.com/example/own'
        third = 'https://github.com/third-party/data'
        self.make_release_pdf('The code is released at ' + own + '.\n'
                              'We use benchmark data from ' + third + '.',
                              [(own,own),(third,third)])
        clean = self.apply(self.plan())
        self.assertNotIn(own,clean)
        self.assertIn(third,clean)

    def test_incomplete_wrapped_url_without_uri_is_preserved(self):
        self.make_release_pdf('The code is released at https://github.\n'
                              'com/example/project and preserves the scientific result.')
        clean = self.apply(self.plan())
        self.assertIn('https://github.',clean)
        self.assertIn('com/example/project',clean)

    def test_later_linked_method_same_uri_is_preserved(self):
        uri='https://github.com/example/Method'
        self.make_release_pdf('The code is released at ' + uri + '.\n'
                              'Method remains the scientific name of the approach.',
                              [(uri,uri),('Method',uri)])
        clean=self.apply(self.plan())
        self.assertNotIn(uri,clean)
        self.assertIn('Method remains the scientific name',clean)

    def run_production_helpers(self):
        before = self.source.read_bytes()
        plan = self.plan()
        self.assertFalse(plan['uncertain'], plan['uncertain'])
        work = Path(self.temp.name) / 'production-qa'
        work.mkdir()
        candidate = Path(self.temp.name) / 'production-candidate.pdf'
        with patch.object(pdf_helpers, 'call_cli', side_effect=AssertionError('No model calls')), \
                patch('subprocess.Popen', side_effect=AssertionError('No subprocess')), \
                patch('socket.socket', side_effect=AssertionError('No network')):
            pages = pdf_helpers.inspect_pdf(self.source, work)
            pdf_helpers.validate_plan(plan, pages)
            pdf_helpers.redact_pdf(self.source, candidate, plan, pages)
            evidence = pdf_helpers.check_pdf(self.source, candidate, plan, work)
        self.assertTrue(evidence['metadata_removed'])
        self.assertTrue(evidence['outside_redactions_unchanged'])
        self.assertEqual(evidence['pages'], 1)
        self.assertEqual(before, self.source.read_bytes())
        with fitz.open(candidate) as doc:
            return plan, doc[0].get_text()

    def test_production_pipeline_accepts_notes_and_preserves_mixed_science(self):
        self.make_pdf(extra=[('Our code is available at https://github.com/example/project', 260),
                             ('Correspondence to Alice Example <alice@example.edu>. A demo reproduces the results.', 720)])
        plan, clean = self.run_production_helpers()
        self.assertIn('other_identity', {item['category'] for item in plan['redactions']})
        self.assertNotIn('alice@example.edu', clean)
        self.assertNotIn('https://github.com/example/project', clean)
        self.assertIn('A demo reproduces the results.', clean)
        self.assertIn('Our scientific result is x = 42.', clean)

    def test_production_pipeline_accepts_wrapped_release_uri(self):
        uri='https://github.com/example/project'
        self.make_release_pdf('The scientific result remains intact. The code is released at https://github.\n'
                              'com/example/project and preserves the result.',
                              [('https://github.',uri),('com/example/project',uri)])
        _, clean = self.run_production_helpers()
        self.assertIn('The scientific result remains intact.', clean)
        self.assertIn('and preserves the result.', clean)
        self.assertNotIn('https://github.', clean)
        self.assertNotIn('com/example/project', clean)

    def test_digit_prefixed_affiliation_does_not_stop_contact_block(self):
        self.make_pdf(author_lines=[('Alice Example1, Bob Scientist2', 90),
                                    ('1Example University', 105),
                                    ('2University of Science and Technology', 120),
                                    ('{alice,bob}@example.edu', 135),
                                    ('Code: https://github.com/alice/project', 150)])
        plan = self.plan()
        clean = self.apply(plan)
        self.assertNotIn('University of Science and Technology', clean)
        self.assertNotIn('{alice,bob}@example.edu', clean)
        self.assertNotIn('https://github.com/alice/project', clean)

    def make_interleaved_footnote(self, prefix):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example')
            page.insert_text((72,170),'Abstract')
            page.insert_textbox(fitz.Rect(72,690,285,760),
                                prefix+'\nthors (alice@example.edu).',fontsize=9)
            page.insert_text((320,708),'The scientific theorem remains.',fontsize=9)
            page.insert_text((320,720),'Independent column x = 42.',fontsize=9)
            doc.save(self.source)

    def test_interleaved_column_does_not_break_identity_continuation(self):
        self.make_interleaved_footnote('*Interns at Example University; corresponding au-')
        plan=self.plan()
        clean=self.apply(plan)
        self.assertNotIn('alice@example.edu',clean)
        self.assertIn('The scientific theorem remains.',clean)
        self.assertIn('Independent column x = 42.',clean)

    def test_corresponding_author_word_dehyphenates_in_same_block(self):
        self.make_interleaved_footnote('*Corresponding au-')
        plan=self.plan()
        clean=self.apply(plan)
        self.assertNotIn('Corresponding au-',clean)
        self.assertNotIn('alice@example.edu',clean)
        self.assertIn('The scientific theorem remains.',clean)

    def test_own_arxiv_translation_url_preserves_surrounding_prose(self):
        own='https://arxiv.org/abs/'+self.paper['arxiv_id']+'v2'
        self.make_pdf(extra=[('A translation of this paper can be found at '+own+'.',260)])
        clean=self.apply(self.plan())
        self.assertNotIn(own,clean)
        self.assertIn('A translation of this paper can be found at',clean)

    def test_ordinary_own_arxiv_reference_is_preserved(self):
        own='https://arxiv.org/abs/'+self.paper['arxiv_id']+'v2'
        self.make_pdf(extra=[('References',260),('Alice Example. A cited work. '+own,280)])
        self.assertIn(own,self.apply(self.plan()))

    def test_own_arxiv_does_not_match_other_id_with_common_prefix(self):
        other='https://arxiv.org/abs/'+self.paper['arxiv_id']+'9'
        self.make_pdf(extra=[('The comparison benchmark is at '+other+'.',260)])
        self.assertIn(other,self.apply(self.plan()))

    def test_own_arxiv_literal_ignores_oversized_link_annotation(self):
        own='https://arxiv.org/abs/'+self.paper['arxiv_id']+'v2'
        self.make_release_pdf('The scientific result is x = 42.\n'
                              'A translation of this paper is at '+own+'.')
        expanded=self.source.with_name('oversized-link.pdf')
        with fitz.open(self.source) as doc:
            page=doc[0]
            rect=page.search_for(own)[0]
            rect.y0-=18
            page.insert_link({'kind':fitz.LINK_URI,'from':rect,'uri':own})
            doc.save(expanded)
        self.source=expanded
        _,clean=self.run_production_helpers()
        self.assertNotIn(own,clean)
        self.assertIn('The scientific result is x = 42.',clean)

    def test_scientific_note_does_not_inherit_later_contact(self):
        self.make_pdf(extra=[('Corresponding author labels are the target variable.',710),
                             ('*Alice Example (alice@example.edu) is the corresponding author.',725)])
        clean=self.apply(self.plan())
        self.assertIn('Corresponding author labels are the target variable.',clean)
        self.assertNotIn('is the corresponding author.',clean)

    def test_scientific_corresponding_label_email_is_preserved(self):
        self.make_pdf(extra=[('Corresponding author labels use alice@example.edu as example input.',720)])
        clean=self.apply(self.plan())
        self.assertIn('Corresponding author labels use alice@example.edu as example input.',clean)

    def test_implicit_release_after_public_baseline_is_preserved(self):
        uri='https://github.com/third-party/Foo'
        self.make_release_pdf('We evaluate the public Foo baseline. The code is available\n'
                              'at '+uri+'.',[(uri,uri)])
        plan=self.plan()
        clean=self.apply(plan)
        self.assertIn(uri,clean)
        self.assertIn('We evaluate the public Foo baseline.',clean)
        self.assertTrue(any('URL outside' in reason for reason in plan['uncertain']))

    def test_demo_for_cited_method_is_preserved(self):
        uri='https://github.com/third-party/Foo'
        self.make_release_pdf('We compare against the method of Smith et al. A demo for\n'
                              'the method is available at '+uri+'.',[(uri,uri)])
        plan=self.plan()
        clean=self.apply(plan)
        self.assertIn(uri,clean)
        self.assertIn('We compare against the method of Smith et al.',clean)
        self.assertTrue(any('URL outside' in reason for reason in plan['uncertain']))

    def test_explicit_our_release_after_baseline_comparison_still_removed(self):
        uri='https://github.com/example/ours'
        self.make_release_pdf('We compare against a public baseline. Our code is available\n'
                              'at '+uri+'.',[(uri,uri)])
        clean=self.apply(self.plan())
        self.assertNotIn(uri,clean)
        self.assertIn('We compare against a public baseline.',clean)

    def make_repeated_margin_pdf(self, header, reference_lines=()):
        with fitz.open() as doc:
            first=doc.new_page(width=612,height=792)
            first.insert_text((72,30),header)
            first.insert_text((72,60),self.paper['title'],fontsize=16)
            first.insert_text((72,90),'Alice Example')
            first.insert_text((72,170),'Abstract')
            first.insert_text((72,200),'The original scientific result remains.')
            refs=doc.new_page(width=612,height=792)
            refs.insert_text((72,90),'References',fontsize=12)
            refs.insert_text((72,120),'Alice Example. A cited ICLR 2026 paper.')
            third=doc.new_page(width=612,height=792)
            third.insert_text((72,30),header)
            third.insert_text((72,140),'A cited paper in the ICLR 2026 proceedings.')
            for text,y in reference_lines:
                third.insert_text((72,y),text)
            fourth=doc.new_page(width=612,height=792)
            fourth.insert_text((72,30),header)
            fourth.insert_text((72,90),'Appendix',fontsize=12)
            fourth.insert_text((72,140),'Our ICLR 2026 comparison uses the same theorem.')
            fourth.insert_text((72,300),'Published as a conference paper at ICLR 2026')
            doc.save(self.source)

    def test_publication_margin_banners_removed_after_references(self):
        header='Published as a conference paper at ICLR 2026'
        self.make_repeated_margin_pdf(header,
            [('Proceedings of the International Conference on Learning Representations.',750)])
        clean=self.apply(self.plan())
        self.assertEqual(clean.count(header),1)  # The ordinary body quotation remains.
        self.assertIn('A cited ICLR 2026 paper.',clean)
        self.assertIn('A cited paper in the ICLR 2026 proceedings.',clean)
        self.assertIn('Proceedings of the International Conference on Learning Representations.',clean)
        self.assertIn('Our ICLR 2026 comparison uses the same theorem.',clean)

    def test_identified_author_margin_repeats_after_references(self):
        self.make_repeated_margin_pdf('Alice Example',[('Alice Example',750)])
        plan=self.plan()
        self.assertTrue(any(item['page']==3 and item['category']=='author' for item in plan['redactions']))
        clean=self.apply(plan)
        self.assertIn('Alice Example. A cited ICLR 2026 paper.',clean)
        self.assertEqual(clean.count('Alice Example'),2)  # Bibliography name and isolated citation line.

    def test_identified_arxiv_margin_repeats_preserve_self_reference(self):
        header='arXiv:'+self.paper['arxiv_id']+'v1 [cs.LG] 1 Jan 2025'
        citation='arXiv:'+self.paper['arxiv_id']+'v1'
        self.make_repeated_margin_pdf(header,[(citation,750)])
        plan=self.plan()
        self.assertTrue(any(item['page']==3 and item['category']=='identifier' for item in plan['redactions']))
        clean=self.apply(plan)
        self.assertNotIn('[cs.LG]',clean)
        self.assertIn(citation,clean)

    def test_arxiv_margin_stamp_does_not_use_id_prefix(self):
        self.paper['arxiv_id']='2501.1234'
        other='arXiv:2501.12345v1 [cs.LG] 1 Jan 2025'
        self.make_pdf(extra=[(other,30)])
        self.assertIn(other,self.apply(self.plan()))


class MultisourceLayoutRegressions(unittest.TestCase):
    setUp = RuleTests.setUp
    make_pdf = RuleTests.make_pdf
    plan = RuleTests.plan
    removed_text = RuleTests.removed_text
    apply = RuleTests.apply

    def test_variant_name_on_multi_person_row_does_not_stop_known_authors(self):
        self.paper['authors'] = ['Alice Example', 'Robert Scientist', 'Carol Researcher', 'David Scholar']
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            for x, name in [(72, 'Alice Example'), (185, 'Bob Scientist'),
                            (290, 'Carol Researcher'), (410, 'David Scholar')]:
                page.insert_text((x, 100), name, fontsize=11)
            page.insert_text((210, 115), 'Example University', fontsize=11)
            page.insert_text((180, 130), '{alice,bob,carol,david}@example.edu', fontsize=9)
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        plan = self.plan()
        clean = self.apply(plan)
        for value in ['Alice Example', 'Bob Scientist', 'Carol Researcher', 'David Scholar',
                      'Example University', '@example.edu']:
            self.assertNotIn(value, clean)
        self.assertIn(self.paper['title'], clean)
        self.assertTrue(any('roster needs review' in reason for reason in plan['uncertain']))

    def test_unknown_prose_does_not_hide_later_independent_author_anchor(self):
        self.paper['authors'].append('Carol Researcher')
        self.make_pdf(author_lines=[('Alice Example', 88), ('An Unclassified Scientific Diagram', 105),
                                    ('Carol Researcher', 125), ('Example University', 140)])
        clean = self.apply(self.plan())
        self.assertIn('An Unclassified Scientific Diagram', clean)
        self.assertNotIn('Carol Researcher', clean)
        self.assertNotIn('Example University', clean)

    def test_short_institutions_between_byline_and_contacts(self):
        self.paper['authors'].append('Carol Researcher')
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            for x, name, org, email in [(125, 'Alice Example', 'MIT', 'alice@mit.edu'),
                                        (330, 'Carol Researcher', 'UC Santa Barbara', 'carol@ucsb.edu')]:
                page.insert_text((x, 100), name, fontsize=11)
                page.insert_text((x, 115), org, fontsize=11)
                page.insert_text((x, 130), email, fontsize=9)
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        clean = self.apply(self.plan())
        for value in ['MIT', 'UC Santa Barbara', 'alice@mit.edu', 'carol@ucsb.edu']:
            self.assertNotIn(value, clean)

    def test_spacing_accent_marks_in_names_match_metadata(self):
        self.assertEqual(rules._normal('Martin Tak´aˇc'), 'martin takac')
        self.assertEqual(rules._normal('Samuel Horv´ath'), 'samuel horvath')
        self.assertTrue(rules._name_shape('Martin Tak´aˇc'))

    def test_short_institution_without_contact_or_marker_is_not_guessed(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('XYZ Scientific Benchmark', 110)])
        self.assertIn('XYZ Scientific Benchmark', self.apply(self.plan()))

    def test_numbered_affiliation_correspondence_wrap_preserves_neighbor_science(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example 1')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(60, 650, 290, 720),
                                '1Department of Science, Example Univer-\n'
                                'sity. Correspondence to: Alice Example\n'
                                '<alice@example.edu>.', fontsize=9)
            page.insert_text((330, 665), 'The theorem x = 42 remains.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('1Department', clean)
        self.assertNotIn('Correspondence', clean)
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('The theorem x = 42 remains.', clean)

    def test_affiliation_contact_sentence_keeps_scientific_suffix(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example 1')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(60, 650, 300, 730),
                                '1Example University. Correspondence to:\n'
                                'Alice Example <alice@example.edu>. A result\n'
                                'is x = 42.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('A result', clean)
        self.assertIn('is x = 42.', clean)

    def test_scientific_numbered_affiliation_email_example_is_preserved(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(60, 650, 300, 730),
                                '1University is a training label. Correspondence to:\n'
                                'Alice Example <alice@example.edu> is example input.', fontsize=9)
            doc.save(self.source)
        self.assertIn('training label', self.apply(self.plan()))
        self.assertIn('is example input', self.apply(self.plan()))

    def test_marked_affiliation_without_correspondence_is_not_guessed(self):
        self.make_pdf(extra=[('1University benchmark is evaluated on x = 42.', 680)])
        self.assertIn('1University benchmark', self.apply(self.plan()))


    def make_cross_page_ack(self, continuation):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((330, 685), 'Acknowledgements', fontsize=12)
            page.insert_text((330, 710), 'We would like to thank Alice Example and', fontsize=10)
            page.insert_text((330, 724), 'other members of the', fontsize=10)
            page.insert_text((72, 710), 'The scientific theorem x = 42 remains.', fontsize=10)
            page = doc.new_page(width=612, height=792)
            for i, text in enumerate(continuation):
                page.insert_text((72, 80 + i * 14), text, fontsize=10)
            page.insert_text((72, 155), 'References', fontsize=12)
            page.insert_text((72, 180), 'Alice Example. A cited scientific result.', fontsize=10)
            page.insert_text((330, 80), 'Scientific content in another column.', fontsize=10)
            doc.save(self.source)

    def test_ack_continues_from_right_column_to_next_left_column(self):
        self.make_cross_page_ack(['research lab for helpful discussions.',
                                  'Alice Example is funded by grant 123456.'])
        clean = self.apply(self.plan())
        for text in ['Acknowledgements', 'other members', 'research lab', 'grant 123456']:
            self.assertNotIn(text, clean)
        self.assertIn('The scientific theorem x = 42 remains.', clean)
        self.assertIn('Alice Example. A cited scientific result.', clean)
        self.assertIn('Scientific content in another column.', clean)

    def test_ack_does_not_assume_uppercase_next_page_is_continuation(self):
        self.make_cross_page_ack(['Theorem 1. The central result is x = 42.'])
        clean = self.apply(self.plan())
        self.assertIn('Acknowledgements', clean)
        self.assertIn('Theorem 1. The central result is x = 42.', clean)

    def test_ack_continuation_stops_at_scientific_heading(self):
        self.make_cross_page_ack(['research lab for helpful discussions.',
                                  '2 Methods', 'The central scientific result is x = 42.'])
        clean = self.apply(self.plan())
        self.assertNotIn('research lab', clean)
        self.assertIn('2 Methods', clean)
        self.assertIn('The central scientific result is x = 42.', clean)


    def test_bare_project_domain_in_identified_header_is_removed(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('ProjectName.github.io', 130)])
        self.assertNotIn('ProjectName.github.io', self.apply(self.plan()))

    def test_bare_scientific_domain_in_body_is_preserved(self):
        self.make_pdf(extra=[('ProjectName.github.io', 280)])
        self.assertIn('ProjectName.github.io', self.apply(self.plan()))


    def test_column_interleaving_preserves_continued_proceedings_reference(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((330, 260), 'References', fontsize=12)
            page.insert_text((72, 300), '7. Conclusion', fontsize=12)
            page.insert_text((330, 680), 'Alice Example et al. 2017. A useful method.', fontsize=9)
            page.insert_text((330, 695), 'An ordinary scientific citation. In', fontsize=9)
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), 'Proceedings of the Workshop on Scientific Methods', fontsize=9)
            page.insert_text((72, 74), 'pp. 59-63, September 2017. DOI: 10.1234/cited.work.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertIn('Proceedings of the Workshop on Scientific Methods', clean)
        self.assertIn('pp. 59-63, September 2017.', clean)

    def test_identified_header_proceedings_notice_still_removed(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Proceedings of the Conference on Scientific Methods', 130)])
        clean = self.apply(self.plan())
        self.assertNotIn('Proceedings of the Conference', clean)
        self.assertIn(self.paper['title'], clean)

    def test_unanchored_later_proceedings_line_requires_review(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), 'Proceedings of the Workshop on Scientific Methods', fontsize=9)
            doc.save(self.source.with_name('continued.pdf'))
        self.source = self.source.with_name('continued.pdf')
        plan = self.plan()
        self.assertIn('Proceedings of the Workshop', self.apply(plan))
        self.assertTrue(any('Proceedings' in item for item in plan['uncertain']))


    make_release_pdf = VersionThreeTests.make_release_pdf

    def test_preprint_citation_continuation_is_preserved(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), 'Preprint. A cited scientific manuscript, 2024.', fontsize=9)
            page.insert_text((72, 74), 'DOI: 10.1234/cited.work.', fontsize=9)
            doc.save(self.source.with_name('continued.pdf'))
        self.source = self.source.with_name('continued.pdf')
        self.assertIn('Preprint. A cited scientific manuscript, 2024.', self.apply(self.plan()))

    def test_unanchored_later_margin_author_requires_review(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), 'Alice Example', fontsize=9)
            page.insert_text((72, 74), 'A cited scientific manuscript, 2024.', fontsize=9)
            doc.save(self.source.with_name('continued.pdf'))
        self.source = self.source.with_name('continued.pdf')
        self.assertIn('Alice Example', self.apply(self.plan()))

    def test_variant_name_before_first_known_author_on_same_row(self):
        self.paper['authors'] = ['Alice Middle Example', 'Carol Researcher']
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            for x, name in [(110, 'Alice Example*'), (340, 'Carol Researcher*')]:
                page.insert_textbox(fitz.Rect(x, 90, x + 190, 155),
                                    name + '\nExample University\ncontact@example.edu', fontsize=11)
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Alice Example', clean)
        self.assertNotIn('contact@example.edu', clean)

    def test_numbered_research_organization_and_wrapped_group_contact(self):
        self.paper['authors'].append('Carol Researcher')
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example1   Carol Researcher2')
            page.insert_text((72, 108), '1Example University')
            page.insert_text((310, 108), '2Resolve Research')
            page.insert_text((72, 124), '{alice,carol}')
            page.insert_text((72, 138), '@example.edu')
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Resolve Research', clean)
        self.assertNotIn('{alice,carol}', clean)
        self.assertNotIn('@example.edu', clean)

    def test_project_page_release_in_body_removes_only_url(self):
        self.make_release_pdf('Our scientific result is exact. Our project\n'
                              'page: https://project.example.edu.')
        clean = self.apply(self.plan())
        self.assertNotIn('https://project.example.edu', clean)
        self.assertIn('Our scientific result is exact.', clean)
        self.assertIn('page:', clean)

    def test_our_experimental_code_can_be_found_release(self):
        self.make_release_pdf('1Our experimental code can be found\n'
                              'at https://github.com/example/code.')
        self.assertNotIn('https://github.com/example/code', self.apply(self.plan()))

    def test_our_data_release_with_split_uri(self):
        uri = 'https://github.com/example/data'
        self.make_release_pdf('1Our data is available at https://github.com/\n'
                              'example/data.', [('https://github.com/', uri), ('example/data', uri)])
        clean = self.apply(self.plan())
        self.assertNotIn('https://github.com/', clean)
        self.assertNotIn('example/data', clean)

    def test_public_dataset_release_is_not_assumed_owned(self):
        self.make_release_pdf('The dataset is available at https://public.example.edu.')
        self.assertIn('https://public.example.edu', self.apply(self.plan()))

    def test_ack_in_one_column_preserves_other_column_reference(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((72, 560), 'Acknowledgments', fontsize=12)
            page.insert_text((72, 585), 'This work was funded by grant 123456.', fontsize=10)
            page.insert_text((72, 630), 'References', fontsize=12)
            page.insert_text((72, 655), 'Alice Example. A cited method.', fontsize=10)
            page.insert_text((330, 585), 'Another Author. A cited scientific result.', fontsize=10)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('funded by grant', clean)
        self.assertIn('Another Author. A cited scientific result.', clean)
        self.assertIn('Alice Example. A cited method.', clean)


    def test_modified_work_and_correspondence_footnotes(self):
        self.assertTrue(rules._identity_footnote(
            '∗Work was mostly done during the internship at Example University.', ['alice example']))
        self.assertTrue(rules._identity_footnote(
            '♯The corresponding author: alice@example.edu', ['alice example']))
        self.assertFalse(rules._identity_footnote(
            'Work was mostly done by gravity in this experiment.', ['alice example']))

    def test_marked_affiliation_requires_observed_header_marker(self):
        self.assertTrue(rules._identity_footnote(
            '⋄Example Institute of Technology', ['alice example'], {'⋄'}))
        self.assertFalse(rules._identity_footnote(
            '⋄Example Institute of Technology', ['alice example'], set()))
        self.assertFalse(rules._identity_footnote(
            '⋄Example Institute benchmark measures accuracy.', ['alice example'], {'⋄'}))

    def test_fragmented_release_words_are_read_by_baseline(self):
        parts = [('1Our', 72, 650), ('data', 106, 652), ('is', 134, 652),
                 ('available', 146, 652), ('at', 185, 652),
                 ('https://github.com/', 200, 651), ('example/data', 72, 662)]
        lines = [{'text': text, 'rect': [x, y, x + len(text)*4, y + 9], 'block': 1}
                 for text, x, y in parts]
        lines.sort(key=lambda line: (line['rect'][1], line['rect'][0]))
        index = next(i for i, line in enumerate(lines) if line['text'].startswith('https'))
        self.assertTrue(rules._release_context(lines, index, 0))


    def test_project_page_reuses_exact_identified_header_domain(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('project.example.edu', 130)],
                      extra=[('Project page: https://project.example.edu.', 280),
                             ('Project page: https://public.example.edu.', 300)])
        clean = self.apply(self.plan())
        self.assertNotIn('https://project.example.edu', clean)
        self.assertIn('https://public.example.edu', clean)


    def test_single_column_ack_ignores_later_right_side_reference_fragments(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((108, 260), 'Acknowledgments', fontsize=12)
            page.insert_text((108, 285), 'This work was partly supported by Example Foundation under grant 123456.', fontsize=10)
            page.insert_text((108, 325), 'References', fontsize=12)
            page.insert_text((108, 350), 'Alice Example. A cited method.', fontsize=10)
            page.insert_text((350, 370), 'A separate citation fragment.', fontsize=10)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('grant 123456', clean)
        self.assertIn('A separate citation fragment.', clean)
        self.assertIn('Alice Example. A cited method.', clean)

    def test_overlapping_wide_ack_and_other_column_stays_unresolved(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((108, 260), 'Acknowledgments', fontsize=12)
            page.insert_text((108, 285), 'This work was partly supported by Example Foundation under grant 123456.', fontsize=10)
            page.insert_text((350, 285), 'Independent science x = 42.', fontsize=10)
            page.insert_text((108, 325), 'References', fontsize=12)
            doc.save(self.source)
        plan = self.plan()
        self.assertIn('Independent science x = 42.', self.apply(plan))
        self.assertTrue(any('column ambiguity' in item for item in plan['uncertain']))

    def make_late_running_headers(self, header, first_body='The scientific result is x = 42.', y=30, folio=False):
        self.make_pdf(extra=[('References', 280), ('Alice Example. A scientific method, 2024.', 310)])
        with fitz.open(self.source) as doc:
            for number in range(3):
                page = doc.new_page(width=612, height=792)
                page.insert_text((110, y), header, fontsize=9)
                if folio:
                    page.insert_text((72, y), str(page.number + 1), fontsize=9)
                page.insert_text((72, y + 30), first_body, fontsize=10)
                page.insert_text((72, y + 50), 'Additional scientific content remains.', fontsize=10)
            doc.save(self.source.with_name('late-headers.pdf'))
        self.source = self.source.with_name('late-headers.pdf')

    def test_source_repeated_late_full_author_header_is_removed(self):
        self.make_late_running_headers('Alice Example')
        clean = self.apply(self.plan())
        self.assertEqual(clean.count('Alice Example'), 1)
        self.assertEqual(clean.count('The scientific result is x = 42.'), 3)
        self.assertIn('Alice Example. A scientific method, 2024.', clean)

    def test_source_repeated_initial_surname_header_is_removed(self):
        self.make_late_running_headers('A. Example et al.')
        clean = self.apply(self.plan())
        self.assertNotIn('A. Example et al.', clean)
        self.assertEqual(clean.count('Additional scientific content remains.'), 3)

    def test_source_repeated_high_citation_byline_is_preserved(self):
        self.make_late_running_headers('Alice Example', '2024. A cited scientific manuscript.')
        clean = self.apply(self.plan())
        self.assertEqual(clean.count('Alice Example'), 4)

    def test_source_repeated_body_region_byline_is_preserved(self):
        self.make_late_running_headers('Alice Example', y=75)
        self.assertEqual(self.apply(self.plan()).count('Alice Example'), 4)


    def test_inline_acm_author_affiliation_is_an_identity_anchor(self):
        self.paper['authors'].append('Carol Researcher')
        self.make_pdf(author_lines=[
            ('ALICE EXAMPLE, Example University, USA', 90),
            ('CAROL RESEARCHER, Example University and Example Research, USA', 110)])
        clean = self.apply(self.plan())
        self.assertNotIn('ALICE EXAMPLE', clean)
        self.assertNotIn('CAROL RESEARCHER', clean)
        self.assertNotIn('Example University', clean)
        self.assertIn(self.paper['title'], clean)
        self.assertIn('Our scientific result is x = 42.', clean)

    def test_scientific_prose_starting_author_and_affiliation_is_preserved(self):
        self.make_pdf(author_lines=[('Alice Example, Example University benchmark measures accuracy.', 90)])
        self.assertIn('benchmark measures accuracy.', self.apply(self.plan()))

    def test_spacing_breve_and_dot_name_accents_match_metadata(self):
        self.assertEqual(rules._normal('Ege Yi˘git ÇEL˙IK'), 'ege yigit celik')
        self.assertEqual(rules._normal('Selma TEK˙IR'), 'selma tekir')

    def test_numbered_group_in_identified_affiliation_row_is_removed(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example1,2')
            page.insert_text((72, 110), '1Example University')
            page.insert_text((280, 110), '2Example Group')
            page.insert_text((72, 170), 'Abstract')
            doc.save(self.source)
        self.assertNotIn('2Example Group', self.apply(self.plan()))

    def test_stacked_research_card_keeps_location_with_contact(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_textbox(fitz.Rect(110, 90, 330, 240),
                                'Alice Example\nCentre for Frontier AI Research\n'
                                'Agency for Science, Technology and Research\n'
                                'Singapore\nalice@example.edu', fontsize=10)
            page.insert_text((72, 260), 'Abstract')
            page.insert_text((72, 290), 'The scientific result remains.')
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Centre for Frontier', clean)
        self.assertNotIn('Singapore', clean)
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('The scientific result remains.', clean)

    def test_capitalized_scientific_label_without_contact_evidence_is_preserved(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Ground Truth', 130)])
        self.assertIn('Ground Truth', self.apply(self.plan()))


    def test_compact_graphics_presence_preserves_plans_without_rich_allocations(self):
        for with_graphics in (False, True):
            with self.subTest(with_graphics=with_graphics):
                self.make_pdf()
                if with_graphics:
                    with fitz.open(self.source) as doc:
                        doc[0].draw_rect(fitz.Rect(72, 250, 120, 280))
                        doc.save(self.source.with_name('vectors.pdf'))
                    self.source = self.source.with_name('vectors.pdf')
                with fitz.open(self.source) as doc:
                    self.assertEqual(bool(doc[0].get_drawings()),
                                     bool(doc[0].get_cdrawings(extended=False)))
                expected = self.plan()
                with patch.object(fitz.Page, 'get_drawings',
                                  side_effect=AssertionError('Avoid rich graphics allocation for a presence check')):
                    actual = self.plan()
                self.assertEqual(expected, actual)
                self.assertEqual(any('vector graphics' in item for item in actual['uncertain']), with_graphics)
                self.source = Path(self.temp.name) / 'source.pdf'


    def test_corporate_affiliation_does_not_stop_header_or_remove_body_manufacturer(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Acme Inc.', 110)],
                      extra=[('Acme Inc. provides the benchmark component.', 260)])
        clean = self.apply(self.plan())
        self.assertEqual(clean.count('Acme Inc.'), 1)
        self.assertIn('Acme Inc. provides the benchmark component.', clean)

    def test_supplementary_title_page_reuses_known_title_and_byline(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Acme Inc.', 110)])
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 82), 'Supplementary Material', fontsize=16)
            page.insert_text((72, 115), 'Alice Example')
            page.insert_text((72, 135), 'Acme Inc.', fontsize=10)
            page.insert_text((72, 175), 'A Scientific details', fontsize=12)
            page.insert_text((72, 200), 'The scientific method uses the Acme SDK.')
            doc.save(self.source.with_name('supplement.pdf'))
        self.source = self.source.with_name('supplement.pdf')
        clean = self.apply(self.plan())
        self.assertNotIn('Alice Example', clean)
        self.assertNotIn('Acme Inc.', clean)
        self.assertEqual(clean.count(self.paper['title']), 2)
        self.assertIn('Supplementary Material', clean)
        self.assertIn('A Scientific details', clean)
        self.assertIn('The scientific method uses the Acme SDK.', clean)

    def test_supplementary_heading_without_repeated_title_does_not_guess_byline(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 82), 'Supplementary Material', fontsize=16)
            page.insert_text((72, 115), 'Alice Example')
            page.insert_text((72, 135), 'Acme Inc.', fontsize=10)
            page.insert_text((72, 175), 'The example describes an attributed scientific method.')
            doc.save(self.source.with_name('supplement-example.pdf'))
        self.source = self.source.with_name('supplement-example.pdf')
        self.assertIn('Alice Example', self.apply(self.plan()))

    def test_equal_contribution_and_split_affiliation_contact_block(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example1')
            page.insert_text((72, 170), 'Abstract')
            page.insert_text((60, 650), '*Equal contribution', fontsize=9)
            page.insert_text((145, 650), '1Example International Grad-', fontsize=9)
            page.insert_text((60, 662), 'uate School, Example University. Correspon-', fontsize=9)
            page.insert_text((60, 674), 'dence to: Alice Example <alice@example.edu>.', fontsize=9)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('1Example International Grad-', clean)
        self.assertNotIn('Example University', clean)
        self.assertNotIn('alice@example.edu', clean)

    def make_adjacent_preprint_footer(self, notice, correspondence=True):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            if correspondence:
                page.insert_text((60, 680), 'Correspondence to Alice Example <alice@example.edu>.', fontsize=9)
            page.insert_text((60, 710), notice, fontsize=9)
            page.insert_text((330, 710), 'The theorem x = 42 remains.', fontsize=9)
            doc.save(self.source)

    def test_dated_preprint_after_correspondence_is_removed(self):
        self.make_adjacent_preprint_footer('Preprint. April 20, 2026.')
        clean = self.apply(self.plan())
        self.assertNotIn('Preprint. April', clean)
        self.assertIn('The theorem x = 42 remains.', clean)

    def test_preprint_under_review_after_correspondence_is_removed(self):
        self.make_adjacent_preprint_footer('Preprint. Under review.')
        self.assertNotIn('Preprint. Under review.', self.apply(self.plan()))

    def test_unanchored_dated_preprint_footer_is_preserved(self):
        self.make_adjacent_preprint_footer('Preprint. April 20, 2026.', correspondence=False)
        self.assertIn('Preprint. April 20, 2026.', self.apply(self.plan()))

    def test_lower_running_author_header_requires_source_folio(self):
        self.make_late_running_headers('Alice Example', y=70, folio=True)
        clean = self.apply(self.plan())
        self.assertEqual(clean.count('Alice Example'), 1)
        self.assertEqual(clean.count('The scientific result is x = 42.'), 3)


    def test_source_middle_name_variants_restart_stacked_cards(self):
        self.paper['authors'] = ['Alice Example', 'Xianjun Davin Choo', 'Hady Lauw']
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 110), 'Example University')
            page.insert_text((72, 145), 'Davin Choo')
            page.insert_text((320, 145), 'Hady W. Lauw')
            page.insert_text((72, 165), 'Example University')
            page.insert_text((320, 165), 'Example Institute')
            page.insert_text((72, 185), 'davin@example.edu')
            page.insert_text((320, 185), 'hady@example.edu')
            page.insert_text((72, 230), 'Abstract')
            page.insert_text((72, 260), 'The scientific result remains.')
            doc.save(self.source)
        clean = self.apply(self.plan())
        for value in ['Davin Choo', 'Hady W. Lauw', 'davin@example.edu', 'hady@example.edu']:
            self.assertNotIn(value, clean)
        self.assertIn('The scientific result remains.', clean)

    def test_middle_name_matching_does_not_accept_scientific_phrase(self):
        self.make_pdf(author_lines=[('Alice Scientific Result Example', 90)])
        self.assertIn('Alice Scientific Result Example', self.apply(self.plan()))

    def test_greek_author_markers_are_separate_from_names(self):
        self.assertTrue(rules._byline_only('Alice Exampleα, Bob Scientistβ', ['alice example', 'bob scientist']))
        self.assertFalse(rules._byline_only('Our method α improves the scientific result.', ['alice example']))

    def make_author_cover_page(self, known=True):
        self.paper['authors'] = ['Alice Example', 'Bob Scientist', 'Carol Researcher']
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            authors = 'Alice Example1, Bob Scientist2\nCarol Researcher3' if known else 'Unknown Person1'
            page.insert_textbox(fitz.Rect(72, 90, 500, 150), authors, fontsize=11)
            page.insert_textbox(fitz.Rect(72, 155, 500, 235),
                                '1Example University\n2AcmeTech, 3Independent Contributor', fontsize=11)
            page.insert_text((72, 290), 'Figure 1: Scientific architecture remains.')
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 80), 'Abstract', fontsize=12)
            page.insert_text((72, 110), 'The scientific result is x = 42.')
            doc.save(self.source)

    def test_author_cover_page_uses_next_page_abstract_boundary(self):
        self.make_author_cover_page()
        clean = self.apply(self.plan())
        for value in ['Alice Example', 'Bob Scientist', 'Carol Researcher', 'AcmeTech', 'Independent Contributor']:
            self.assertNotIn(value, clean)
        self.assertIn(self.paper['title'], clean)
        self.assertIn('Figure 1: Scientific architecture remains.', clean)
        self.assertIn('The scientific result is x = 42.', clean)

    def test_unmatched_cover_page_is_not_assumed_identity(self):
        self.make_author_cover_page(known=False)
        clean = self.apply(self.plan())
        self.assertIn('Unknown Person', clean)
        self.assertIn('Figure 1: Scientific architecture remains.', clean)

    def test_postal_address_between_institution_and_contact_is_removed(self):
        self.make_pdf(author_lines=[('Alice Example', 90), ('Example University', 110),
                                    ('Cambridge, United Kingdom, CB2 1PZ', 125),
                                    ('alice@example.edu', 145)])
        self.assertNotIn('CB2 1PZ', self.apply(self.plan()))
        self.assertNotIn('alice@example.edu', self.apply(self.plan()))

    def test_ack_funding_sentence_is_not_a_new_heading(self):
        self.make_pdf(extra=[('Acknowledgments', 260),
                             ('This research work is funded by Example Foundation', 285),
                             ('under Grant ABC123.', 300), ('References', 340),
                             ('Alice Example. A cited scientific method.', 365)])
        clean = self.apply(self.plan())
        self.assertNotIn('Example Foundation', clean)
        self.assertNotIn('ABC123', clean)
        self.assertIn('Alice Example. A cited scientific method.', clean)

    def test_unheaded_scientific_supported_by_sentence_is_preserved(self):
        self.make_pdf(extra=[('This result is supported by Figure 2 and Theorem 3.', 260)])
        self.assertIn('supported by Figure 2 and Theorem 3.', self.apply(self.plan()))


    def test_ieee_author_affiliation_email_block_without_correspondence_keyword(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(60, 650, 300, 745),
                                'Alice Example (alice@example.edu) is with Example\n'
                                'University, USA. Bob Scientist is with Sample Institute.\n'
                                'Carol Researcher is an Independent Researcher.', fontsize=8)
            page.insert_text((330, 680), 'The scientific result is x = 42.', fontsize=8)
            doc.save(self.source)
        clean = self.apply(self.plan())
        for value in ['alice@example.edu', 'Bob Scientist', 'Independent Researcher']:
            self.assertNotIn(value, clean)
        self.assertIn('The scientific result is x = 42.', clean)

    def test_author_affiliation_block_stops_before_scientific_sentence(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            page.insert_textbox(fitz.Rect(60, 650, 300, 745),
                                'Alice Example (alice@example.edu) is with Example University.\n'
                                'We evaluate the model on 50 examples.', fontsize=8)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('alice@example.edu', clean)
        self.assertIn('We evaluate the model on 50 examples.', clean)

    def test_scientific_character_affiliation_example_is_preserved(self):
        self.make_pdf(extra=[('The example character Alice Example is with Example University.', 670),
                             ('The literal contact is alice@example.edu.', 685)])
        self.assertIn('The example character Alice Example', self.apply(self.plan()))

    def test_bulleted_acknowledgment_inside_prompt_is_preserved(self):
        self.make_pdf(extra=[('3. Remove the following information in entirety:', 260),
                             ('- Images', 280), ('- References', 300),
                             ('- Acknowledgments', 320), ('- Authorship information', 340),
                             ('U Text Normalization Prompt', 380)])
        clean = self.apply(self.plan())
        self.assertIn('- Acknowledgments', clean)
        self.assertIn('- Authorship information', clean)

    def test_markdown_literal_acknowledgment_is_preserved(self):
        self.make_pdf(extra=[('# Acknowledgments', 260), ('A model output example.', 280),
                             ('References', 320)])
        self.assertIn('# Acknowledgments', self.apply(self.plan()))

    def test_letter_numbered_acknowledgments_end_at_science(self):
        self.make_pdf(extra=[('B.1 Acknowledgments', 260), ('We thank Example University.', 280),
                             ('B.2 Methods', 320), ('The science x = 42 remains.', 340)])
        clean = self.apply(self.plan())
        self.assertNotIn('We thank Example University.', clean)
        self.assertIn('B.2 Methods', clean)
        self.assertIn('The science x = 42 remains.', clean)

    def test_author_contribution_role_headings_are_internal(self):
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            page.insert_text((72, 60), self.paper['title'], fontsize=16)
            page.insert_text((72, 90), 'Alice Example')
            page.insert_text((72, 170), 'Abstract')
            for text, y, size in [('Author Contributions',260,12), ('Project Leadership',285,10),
                                  ('Alice Example conceived the study.',300,10),
                                  ('Data Curation',325,10), ('Bob Scientist curated the data.',340,10),
                                  ('Model and Training',365,10), ('Carol Researcher trained the model.',380,10),
                                  ('References',420,12), ('Alice Example. A cited method.',440,10)]:
                page.insert_text((72,y),text,fontsize=size)
            doc.save(self.source)
        clean = self.apply(self.plan())
        self.assertNotIn('Bob Scientist curated', clean)
        self.assertNotIn('Carol Researcher trained', clean)
        self.assertIn('Alice Example. A cited method.', clean)

    def test_contributions_do_not_remove_preceding_ethics_and_compensation(self):
        self.make_pdf(extra=[('Ethics Statement',250), ('All participants gave informed consent.',270),
                             ('Participants were paid 12 dollars per hour.',290),
                             ('Author Contributions',320), ('Alice Example designed the study.',340),
                             ('References',380)])
        clean = self.apply(self.plan())
        self.assertIn('All participants gave informed consent.',clean)
        self.assertIn('Participants were paid 12 dollars per hour.',clean)
        self.assertNotIn('Alice Example designed',clean)

    def test_declarations_funding_preserves_other_statements(self):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example')
            page.insert_text((72,170),'Abstract')
            page.insert_text((72,260),'Declarations',fontsize=12)
            page.insert_textbox(fitz.Rect(72,280,520,335),
                                'This work was supported by Example Foundation under Grant ABC123.\n'
                                'We paid participants 12 dollars per hour.',fontsize=10)
            page.insert_text((72,360),'The authors declare no competing interests.',fontsize=10)
            doc.save(self.source)
        clean=self.apply(self.plan())
        self.assertNotIn('ABC123',clean)
        self.assertIn('We paid participants 12 dollars per hour.',clean)
        self.assertIn('The authors declare no competing interests.',clean)
        self.assertIn('Declarations',clean)

    def test_scientific_supported_by_statement_is_not_funding(self):
        self.make_pdf(extra=[('This work is supported by Figure 2 and Theorem 3.', 280)])
        self.assertIn('supported by Figure 2 and Theorem 3.', self.apply(self.plan()))

    def test_tmlr_style_publication_and_review_headers(self):
        self.make_pdf(author_lines=[('Alice Example',90),('Example University',110),
                                    ('Reviewed on OpenReview: OpenReview',130)],
                      extra=[('Published in Journal of Example Research (06/2025)',30)])
        clean=self.apply(self.plan())
        self.assertNotIn('Published in Journal',clean)
        self.assertNotIn('Reviewed on OpenReview',clean)
        self.assertIn('A Scientific Paper',clean)

    def test_workshop_footer_requires_correspondence_and_copyright(self):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example')
            page.insert_text((72,170),'Abstract')
            page.insert_text((60,655),'Correspondence to Alice Example <alice@example.edu>.',fontsize=9)
            page.insert_textbox(fitz.Rect(60,675,300,740),
                                'Proceedings of the Workshop on Scientific Models.\n'
                                'Example Conference, 2025. Copyright 2025 by the author(s).',fontsize=9)
            page.insert_text((330,705),'The scientific theorem remains.',fontsize=9)
            doc.save(self.source)
        clean=self.apply(self.plan())
        self.assertNotIn('Copyright 2025 by the author',clean)
        self.assertNotIn('Proceedings of the Workshop',clean)
        self.assertIn('The scientific theorem remains.',clean)

    def test_plain_company_label_is_bound_to_contact_domain(self):
        self.make_pdf(author_lines=[('Alice Example',90),('Apple',110),('alice@apple.com',130)])
        self.assertNotIn('Apple',self.apply(self.plan()))
        self.make_pdf(author_lines=[('Alice Example',90),('Transformer',110),('alice@example.edu',130)])
        self.assertIn('Transformer',self.apply(self.plan()))


    def test_numbered_third_party_release_footnote_is_preserved(self):
        uri='https://github.com/cited-author/code'
        self.make_pdf(extra=[('We train using the code2 associated with the paper (Smith et al., 2021).',650),
                             ('2Code available at '+uri,700)])
        self.assertIn(uri,self.apply(self.plan()))

    def test_copyright_topic_heading_is_not_a_publication_notice(self):
        self.make_pdf(extra=[('Copyright and privacy.',30),
                             ('We provide two licensing options for the dataset.',260)])
        self.assertIn('Copyright and privacy.',self.apply(self.plan()))

    def test_copyright_year_notice_is_removed_from_margin(self):
        self.make_pdf(extra=[('Copyright 2025 by the author(s).',30)])
        self.assertNotIn('Copyright 2025',self.apply(self.plan()))

    def test_parenthesized_plural_header_contacts_after_gap(self):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example')
            page.insert_text((72,110),'Example University')
            page.insert_text((72,150),'*Corresponding author(s). E-mail(s): alice@example.edu;',fontsize=10)
            page.insert_text((72,165),'Contributing authors: bob@example.edu;',fontsize=10)
            page.insert_text((72,180),'carol@example.edu;',fontsize=10)
            page.insert_text((72,220),'Abstract')
            doc.save(self.source)
        clean=self.apply(self.plan())
        for value in ['alice@example.edu','bob@example.edu','carol@example.edu']:
            self.assertNotIn(value,clean)
        self.assertIn('Abstract',clean)

    def test_corresponding_to_contact_variant_and_math_negative(self):
        self.assertTrue(rules._identity_footnote('*Corresponding to: Alice Example (alice@example.edu)', ['alice example']))
        self.assertFalse(rules._identity_footnote('Corresponding to: x = 42 in the equation.', ['alice example']))

    def test_accented_university_correspondence_block(self):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example1')
            page.insert_text((72,170),'Abstract')
            page.insert_textbox(fitz.Rect(60,640,300,725),
                                '1Université de Science, France. Cor-\n'
                                'respondence to: Alice Example\n'
                                '<alice@example.edu>.',fontsize=9)
            doc.save(self.source)
        clean=self.apply(self.plan())
        self.assertNotIn('alice@example.edu',clean)
        self.assertNotIn('Université',clean)

    def test_irb_institution_and_protocol_removed_but_approval_payment_remain(self):
        self.make_release_pdf('Our data collection and human study process was approved by the Institutional Review Board (IRB)\n'
                              'at the University of Example (UE), under protocol IRB-SBS #6932. Participants were compensated\n'
                              'between $15 to $20 per hour.')
        clean=' '.join(self.apply(self.plan()).split())
        self.assertNotIn('University of Example',clean)
        self.assertNotIn('IRB-SBS #6932',clean)
        self.assertIn('approved by the Institutional Review Board (IRB)',clean)
        self.assertIn('Participants were compensated',clean)
        self.assertIn('$15 to $20 per hour',clean)

    def test_ethics_institution_span_preserves_cost_and_generic_committee(self):
        self.make_release_pdf('The validation cost was GBP 163.71. This validation study was approved by\n'
                              'the University of Example School of Informatics\n'
                              'Ethics Committee, Application 825105.')
        clean=' '.join(self.apply(self.plan()).split())
        self.assertNotIn('University of Example',clean)
        self.assertNotIn('825105',clean)
        self.assertIn('GBP 163.71',clean)
        self.assertIn('This validation study was approved by',clean)
        self.assertIn('Ethics Committee',clean)

    def test_generic_ethical_approval_consent_and_cost_remain(self):
        text='Our study was approved by our institutional review board. All participants consented and were paid USD 20.'
        self.make_release_pdf(text)
        self.assertIn(text,self.apply(self.plan()).replace('\n',' ').strip())

    def test_cited_or_prompt_ethics_examples_remain(self):
        self.make_release_pdf('A prior study was approved by the University of Example Ethics Committee, Application 825105.\n'
                              'Example prompt: Our study was approved by the University of Sample Ethics Committee, Application 123456.')
        clean=' '.join(self.apply(self.plan()).split())
        self.assertIn('University of Example',clean)
        self.assertIn('825105',clean)
        self.assertIn('University of Sample',clean)
        self.assertIn('123456',clean)

    def make_contributor_table(self, roster=True):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example')
            page.insert_text((72,170),'Abstract')
            for x,text in [(72,'GitHub'),(180,'First name'),(260,'Last name'),(350,'Affiliations'),(510,'Score')]:
                page.insert_text((x,260),text,fontsize=9)
            for y,values in [(280,['alice-code','Alice','Example','Example University','0.91']),
                             (300,['bob-code','Bob','Scientist','Sample Institute','0.88'])]:
                for x,text in zip([72,180,260,350,510],values):
                    page.insert_text((x,y),text,fontsize=9)
            caption='Table 1: Author overview, affiliations and GitHub handles.' if roster else 'Table 1: Example input records for the benchmark.'
            page.insert_text((72,335),caption,fontsize=10)
            page.insert_text((72,365),'The scientific analysis uses all scores.',fontsize=10)
            doc.save(self.source)

    def test_contributor_identity_cells_preserve_score_columns(self):
        self.make_contributor_table()
        clean=self.apply(self.plan())
        for value in ['alice-code','bob-code','Alice','Example University','Sample Institute']:
            self.assertNotIn(value,clean)
        self.assertIn('Score',clean)
        self.assertIn('0.91',clean)
        self.assertIn('0.88',clean)
        self.assertIn('The scientific analysis uses all scores.',clean)

    def test_scientific_name_table_without_roster_caption_is_preserved(self):
        self.make_contributor_table(roster=False)
        clean=self.apply(self.plan())
        self.assertIn('alice-code',clean)
        self.assertIn('Example University',clean)
        self.assertIn('0.91',clean)


    def make_unlabelled_abstract(self, title=None):
        self.paper['authors']=['Alice Example','Bob Scientist']
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),title or self.paper['title'],fontsize=16)
            page.insert_text((72,90),'Alice Example1, Bob Scientist2')
            page.insert_text((72,110),'1Research at Meta, 2Example University')
            page.insert_text((72,130),'*Work done at Meta')
            page.insert_text((72,165),'The advent of our method enables a new scientific capability.')
            page.insert_text((72,185),'We preserve this complete scientific abstract and its results.')
            doc.save(self.source)

    def test_no_abstract_label_keeps_known_byline_but_preserves_prose(self):
        self.make_unlabelled_abstract()
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example',clean)
        self.assertNotIn('Bob Scientist',clean)
        self.assertNotIn('Work done at Meta',clean)
        self.assertIn('The advent of our method enables a new scientific capability.',clean)
        self.assertIn('We preserve this complete scientific abstract and its results.',clean)

    def test_no_label_unknown_title_does_not_guess_header(self):
        self.make_unlabelled_abstract(title='An Unmatched Source Title')
        # Remove the Introduction label to require the fallback rather than a known boundary.
        with fitz.open(self.source) as doc:
            for rect in doc[0].search_for('1 Introduction'):doc[0].add_redact_annot(rect)
            doc[0].apply_redactions()
            doc.save(self.source.with_name('unknown-title.pdf'))
        self.source=self.source.with_name('unknown-title.pdf')
        self.assertIn('Alice Example',self.apply(self.plan()))

    def test_no_label_quote_is_not_a_byline(self):
        self.paper['authors']=['Alice Example']
        self.make_pdf(abstract=False,author_lines=[('In Memory Of Alice Example',90)],
                      extra=[('The scientific method must be preserved.',240)])
        self.assertIn('In Memory Of Alice Example',self.apply(self.plan()))

    def test_no_label_fallback_stops_at_teaser_before_later_name(self):
        self.paper['authors']=['Alice Example','Bob Scientist']
        self.make_pdf(abstract=False,author_lines=[('Alice Example, Bob Scientist',90),
                     ('Example University',110),('Figure 1: A scientific teaser',140),
                     ('Alice Example',155)])
        clean=self.apply(self.plan())
        self.assertIn('Figure 1: A scientific teaser',clean)
        self.assertIn('Alice Example',clean)

    def test_inverted_group_email_removed_only_from_identified_header(self):
        email='{alice@, bob@lab.}example.edu'
        self.make_pdf(author_lines=[('Alice Example',90),('Example University',110),(email,130)],
                      extra=[('Example input: '+email,260)])
        clean=self.apply(self.plan())
        self.assertEqual(clean.count(email),1)
        self.assertIn('Example input: '+email,clean)

    def test_plain_company_under_multiple_authors_is_bounded(self):
        self.paper['authors']=['Alice Example','Bob Scientist']
        self.make_pdf(author_lines=[('Alice Example, Bob Scientist',90),('Apple',110)])
        self.assertNotIn('Apple',self.apply(self.plan()))


    def make_centered_short_label(self, label, image=False):
        self.paper['authors']=['Alice Example','Bob Scientist']
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            authors='Alice Example, Bob Scientist'
            page.insert_text((72,90),authors,fontsize=11)
            center=72+fitz.get_text_length(authors,fontsize=11)/2
            if image:
                pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,10,10),False)
                pix.clear_with(255)
                page.insert_image(fitz.Rect(70,96,270,150),pixmap=pix)
            page.insert_text((center-fitz.get_text_length(label,fontsize=11)/2,110),label,fontsize=11)
            page.insert_text((72,170),'Abstract')
            doc.save(self.source)

    def test_model_label_under_multiple_authors_is_not_a_company(self):
        self.make_centered_short_label('Transformer')
        self.assertIn('Transformer',self.apply(self.plan()))

    def test_company_word_inside_a_teaser_is_not_an_affiliation(self):
        self.make_centered_short_label('Apple',image=True)
        self.assertIn('Apple',self.apply(self.plan()))

    def test_named_correspondence_after_unlabelled_abstract(self):
        self.make_unlabelled_abstract()
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.insert_text((72,230),'Correspondence: Alice Example at <alice@example.edu>',fontsize=9)
            doc.save(self.source.with_name('correspondence.pdf'))
        self.source=self.source.with_name('correspondence.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('alice@example.edu',clean)
        self.assertIn('The advent of our method',clean)

    def test_location_inherits_source_confirmed_short_organization(self):
        self.make_pdf(author_lines=[('Alice Example',90),('BITS Campus',115),
                                    ('India',128),('alice@bits.example.edu',142)])
        clean=self.apply(self.plan())
        self.assertNotIn('India',clean)
        self.assertNotIn('alice@bits.example.edu',clean)


    def test_pdf_grave_fragments_normalize_to_metadata_byline(self):
        self.paper['authors']=['Emanuele Rodolà']
        grave=chr(96)
        for fragment in ('Rodol'+grave+'a', 'Rodol '+grave+'a', 'Rodol '+grave+' a'):
            with self.subTest(fragment=fragment):
                self.assertEqual(rules._normal('Emanuele '+fragment), 'emanuele rodola')
                self.assertTrue(rules._name_shape('Emanuele '+fragment))
                self.make_pdf(author_lines=[('Emanuele '+fragment,90)],
                              extra=[('We compare Emanuele '+fragment+' models.',240),
                                     ('References',300),
                                     ('Emanuele '+fragment+'. A cited method, 2024.',330)])
                clean=self.apply(self.plan())
                self.assertEqual(clean.count('Emanuele '+fragment),2)
                self.assertIn('A cited method, 2024.',clean)
                self.assertIn('We compare',clean)

    def make_publication_partner_headers(self, partner, same_row=True, body='Scientific result x = 42.'):
        self.paper['authors'].append('Carol Researcher')
        self.make_pdf(extra=[('References',280),('Alice Example. A cited method, 2024.',310)])
        with fitz.open(self.source) as doc:
            for _ in range(3):
                page=doc.new_page(width=612,height=792)
                page.insert_text((50,70),partner,fontsize=7)
                page.insert_text((380,70 if same_row else 86),'Alice Example & Carol Researcher',fontsize=7)
                page.insert_text((60,115),body,fontsize=10)
                page.insert_text((60,140),'Additional scientific content remains.',fontsize=10)
            doc.save(self.source.with_name('partner-headers.pdf'))
        self.source=self.source.with_name('partner-headers.pdf')

    def test_repeated_author_and_publication_notice_header(self):
        self.make_publication_partner_headers('Conference acronym XX, June 03-05, 2024, Town, Country')
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example & Carol Researcher',clean)
        self.assertNotIn('Conference acronym XX',clean)
        self.assertIn('Alice Example. A cited method, 2024.',clean)
        self.assertEqual(clean.count('Scientific result x = 42.'),3)

    def test_scientific_partner_does_not_authorize_lower_author_row(self):
        self.make_publication_partner_headers('Experiment results for condition XX')
        self.assertEqual(self.apply(self.plan()).count('Alice Example & Carol Researcher'),3)

    def test_unaligned_notice_does_not_authorize_lower_author_row(self):
        self.make_publication_partner_headers('Conference acronym XX, June 03-05, 2024, Town, Country',same_row=False)
        self.assertEqual(self.apply(self.plan()).count('Alice Example & Carol Researcher'),3)

    def test_repeated_publication_partner_preserves_citation_continuation(self):
        self.make_publication_partner_headers('Conference acronym XX, June 03-05, 2024, Town, Country',
                                              body='2024. A cited scientific manuscript.')
        self.assertEqual(self.apply(self.plan()).count('Alice Example & Carol Researcher'),3)


    def make_ack_continuation(self, same_page=True, scientific=False):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((55,120),'Scientific table score 54.5 remains.',fontsize=10)
            x=55 if same_page else 330
            heading='Acknowledgement' if same_page else 'Acknowledgements The authors thank Alice Exam-'
            page.insert_text((x,680),heading,fontsize=10)
            text='The work is supported by an Example grant. Funding No.' if same_page else 'ple, Bob Researcher and'
            page.insert_text((x,705),text,fontsize=9)
            if not same_page: page=doc.new_page(width=612,height=792)
            x=330 if same_page else 55
            continuation=('23.4 is the measured score.' if scientific else '123456-789 and a Starting Grant.') if same_page else (
                         'Quantitative results show improvement.' if scientific else 'Carol Researcher for helpful discussions.')
            page.insert_text((x,70),continuation,fontsize=9)
            page.insert_text((x,92),'This work was funded by Example Foundation.',fontsize=9)
            page.insert_text((x,130),'Impact Statement',fontsize=12)
            page.insert_text((x,155),'We paid participants USD 15 per hour.',fontsize=9)
            page.insert_text((x,190),'References',fontsize=12)
            page.insert_text((x,215),'Alice Example. Scientific paper, 2024.',fontsize=9)
            doc.save(self.source.with_name('continuation.pdf'))
        self.source=self.source.with_name('continuation.pdf')

    def test_grant_number_continues_to_next_column(self):
        self.make_ack_continuation()
        clean=self.apply(self.plan())
        self.assertNotIn('123456-789',clean)
        self.assertNotIn('Funding No.',clean)
        self.assertIn('Scientific table score 54.5 remains.',clean)
        self.assertIn('Impact Statement',clean)
        self.assertIn('We paid participants USD 15 per hour.',clean)
        self.assertIn('Alice Example. Scientific paper, 2024.',clean)

    def test_decimal_scientific_result_is_not_grant_continuation(self):
        self.make_ack_continuation(scientific=True)
        self.assertIn('23.4 is the measured score.',self.apply(self.plan()))

    def test_named_thanks_continues_to_next_page(self):
        self.make_ack_continuation(same_page=False)
        clean=self.apply(self.plan())
        self.assertNotIn('Carol Researcher',clean)
        self.assertNotIn('Foundation',clean)
        self.assertIn('We paid participants USD 15 per hour.',clean)

    def test_uppercase_science_is_not_named_thanks_continuation(self):
        self.make_ack_continuation(same_page=False,scientific=True)
        self.assertIn('Quantitative results show improvement.',self.apply(self.plan()))

    def test_release_uses_selected_footnote_number(self):
        self.make_release_pdf('1GEMM stands for General Matrix Multiplication.\n'
                              '2The code is available at https://github.com/example/current.')
        self.assertNotIn('https://github.com/example/current',self.apply(self.plan()))

    def test_own_appendix_details_can_precede_inline_code_release(self):
        self.make_release_pdf('The preceding comparison uses Smith et al. [4]. We now describe our method.\n'
                              'More details are given in appendix (Appendix D.1) and the full code is\n'
                              'available online at https://github.com/example/current.')
        clean=self.apply(self.plan())
        self.assertNotIn('https://github.com/example/current',clean)
        self.assertIn('More details are given in appendix',clean)

    def test_cited_appendix_code_release_remains(self):
        self.make_release_pdf('The cited paper provides more details in its appendix and the full code is\n'
                              'available online at https://github.com/thirdparty/implementation.')
        self.assertIn('https://github.com/thirdparty/implementation',self.apply(self.plan()))


    def add_ack_running_title(self, repeated):
        with fitz.open(self.source) as doc:
            for number in ([1,2] if repeated else [2]):
                doc[number].insert_text((170,40),'Running scientific manuscript title',fontsize=11)
            doc.save(self.source.with_name('running-title.pdf'))
        self.source=self.source.with_name('running-title.pdf')

    def test_repeated_title_does_not_block_named_thanks_continuation(self):
        self.make_ack_continuation(same_page=False)
        self.add_ack_running_title(repeated=True)
        clean=self.apply(self.plan())
        self.assertNotIn('Carol Researcher',clean)
        self.assertEqual(clean.count('Running scientific manuscript title'),2)
        self.assertIn('We paid participants USD 15 per hour.',clean)

    def test_unmatched_spanning_title_keeps_continuation_ambiguous(self):
        self.make_ack_continuation(same_page=False)
        self.add_ack_running_title(repeated=False)
        self.assertIn('Carol Researcher',self.apply(self.plan()))


    def test_observed_numbered_short_affiliations_do_not_stop_later_contacts(self):
        self.paper['authors'].append('Bob Researcher')
        self.make_pdf(author_lines=[('Alice Example1, Bob Researcher2,3',90),
                                    ('1TU Example, Austria',112),
                                    ('2Example University',125),
                                    ('3DeepWorks',138),
                                    ('alice@tu.example.edu, bob@example.edu',150)])
        clean=self.apply(self.plan())
        for text in ['TU Example','Example University','DeepWorks','alice@tu.example.edu']:
            self.assertNotIn(text,clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_unobserved_number_is_not_an_affiliation_marker(self):
        self.make_pdf(author_lines=[('Alice Example1',90),('1Example University',110),
                                    ('9UnidentifiedLabel',135)])
        self.assertIn('9UnidentifiedLabel',self.apply(self.plan()))

    def test_numbered_model_score_is_not_affiliation(self):
        self.make_pdf(author_lines=[('Alice Example1',90),('1CNN Accuracy',115)])
        self.assertIn('1CNN Accuracy',self.apply(self.plan()))

    def test_department_and_international_institution_labels(self):
        for institution in ['Chair of Mechatronics','Dpt of Informatics',
                            'Politecnico di Example','Universiy of Example','Univesity of Example']:
            with self.subTest(institution=institution):
                self.make_pdf(author_lines=[('Alice Example',90),(institution,110),
                                            ('Example University, Germany',125),
                                            ('alice@example.edu',145)])
                clean=self.apply(self.plan())
                self.assertNotIn(institution,clean)
                self.assertNotIn('alice@example.edu',clean)
                self.assertIn('Our scientific result is x = 42.',clean)

    def test_grouped_email_domain_is_contact_only_inside_author_header(self):
        address='{alice,bob}@{campus,cs}.example.edu'
        self.make_pdf(author_lines=[('Alice Example',90),('Example University',110),(address,130)],
                      extra=[('The literal email example is '+address,240)])
        clean=self.apply(self.plan())
        self.assertEqual(clean.count(address),1)
        self.assertIn('The literal email example is',clean)


    def test_leading_name_accents_preserve_word_boundaries(self):
        self.assertEqual(rules._normal('Siniˇsa ˇSegvi´c'),'sinisa segvic')
        self.assertEqual(rules._normal('Özgür ¸Sim¸sek†'),'ozgur simsek')
        self.assertEqual(rules._normal('Emanuele Rodol '+chr(96)+'a'),'emanuele rodola')
        self.assertTrue(rules._name_shape('Siniˇsa ˇSegvi´c'))

    def test_faculty_label_does_not_block_group_domain_contact(self):
        address='{alice,bob}@{campus,cs}.example.edu'
        self.make_pdf(author_lines=[('Alice Example',90),('Faculty of Computer Science',110),
                                    ('Example Institute of Technology',125),(address,145)])
        clean=self.apply(self.plan())
        self.assertNotIn('Faculty of Computer Science',clean)
        self.assertNotIn(address,clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_partial_group_contact_with_interleaved_author_columns(self):
        self.paper['authors'].append('Bob Researcher')
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            for text,x,y,size in [(self.paper['title'],72,60,16),('Alice Example',72,90,11),
                ('Bob Researcher',330,90,11),('Example University',72,110,11),
                ('Example Institute',330,110,11),('Example City',72,125,11),
                ('{bob,',330,125,11),('alice@example.edu',72,140,11),
                ('carol}@example.edu',345,140,11),('Abstract',72,180,12),
                ('Our scientific result is x = 42.',72,210,11)]:
                page.insert_text((x,y),text,fontsize=size)
            doc.save(self.source)
        clean=self.apply(self.plan())
        for text in ['{bob,','carol}@example.edu','alice@example.edu','Example City']:
            self.assertNotIn(text,clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_group_contact_does_not_cross_unrelated_same_column_text(self):
        self.make_pdf(author_lines=[('Alice Example',90),('{alice,',110),
                                    ('Scientific caption',125),('bob}@example.edu',140)])
        clean=self.apply(self.plan())
        self.assertIn('Scientific caption',clean)
        self.assertIn('{alice,',clean)

    def test_named_funding_prose_is_not_a_heading(self):
        self.make_pdf(extra=[('Acknowledgements',300),
                            ('Alice Example was partially funded by Example Corp',340),
                            ('for this project under Grant ABC12345.',354),
                            ('References',400),('Alice Example. A useful method, 2024.',430)])
        clean=self.apply(self.plan())
        self.assertNotIn('funded by Example Corp',clean)
        self.assertNotIn('ABC12345',clean)
        self.assertIn('Alice Example. A useful method, 2024.',clean)

    def test_inline_author_contributions_stop_at_additional_information(self):
        self.make_pdf(extra=[('Author Contributions: Alice Example designed the experiments.',300),
                            ('Bob Researcher curated the data.',315),
                            ('Additional Information',350),
                            ('The authors declare no competing interests.',375)])
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example designed',clean)
        self.assertNotIn('Bob Researcher curated',clean)
        self.assertIn('Additional Information',clean)
        self.assertIn('The authors declare no competing interests.',clean)

    def test_final_ack_credit_block_can_end_at_document_end(self):
        self.make_pdf(extra=[('Acknowledgements',300),
                            ('Alice Example designed the experiments and Bob Researcher',320),
                            ('curated the data. Carol Scholar supervised the project.',335)])
        clean=self.apply(self.plan())
        self.assertNotIn('Carol Scholar',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_patient_acknowledgment_rubric_at_eof_is_science(self):
        self.make_pdf(extra=[('Patient support scoring rubric',270),('Acknowledgment',300),
                            ('Validate the feelings expressed by the patient.',320),
                            ('Scores range from 0 to 5 based on emotional support.',335)])
        clean=self.apply(self.plan())
        self.assertIn('Validate the feelings',clean)
        self.assertIn('Scores range from 0 to 5',clean)

    def test_ack_is_bounded_by_reference_heading_in_next_column(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            for text,x,y,size in [('Scientific result x = 42.',55,120,10),
                ('Acknowledgements',55,500,12),('The authors thank Alice Example and',55,525,10),
                ('Bob Researcher for helpful discussions.',55,540,10),
                ('References',330,72,12),('Alice Example. A cited method, 2024.',330,96,9)]:
                page.insert_text((x,y),text,fontsize=size)
            doc.save(self.source.with_name('ack-next-column.pdf'))
        self.source=self.source.with_name('ack-next-column.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('The authors thank',clean)
        self.assertNotIn('helpful discussions',clean)
        self.assertIn('Alice Example. A cited method, 2024.',clean)

    def test_local_credit_block_does_not_consume_next_page_figure(self):
        self.make_pdf(extra=[('Acknowledgements. We thank Alice Example and Bob Researcher.',650),
                            ('The project received Grant ABC12345.',665)])
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,80),'Figure 9. Scientific accuracy is 85 percent.',fontsize=10)
            page.insert_text((72,350),'References',fontsize=12)
            page.insert_text((72,375),'Alice Example. A cited method, 2024.',fontsize=10)
            doc.save(self.source.with_name('ack-next-figure.pdf'))
        self.source=self.source.with_name('ack-next-figure.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('ABC12345',clean)
        self.assertNotIn('We thank Alice Example',clean)
        self.assertIn('Figure 9. Scientific accuracy is 85 percent.',clean)

    def test_reproducibility_statement_bounds_previous_ack(self):
        self.make_pdf(extra=[('Acknowledgements',650),
                            ('We thank Alice Example for the useful discussions.',670)])
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,80),'REPRODUCIBILITY STATEMENT',fontsize=12)
            page.insert_text((72,105),'The dataset contains 120 samples.',fontsize=10)
            doc.save(self.source.with_name('ack-next-statement.pdf'))
        self.source=self.source.with_name('ack-next-statement.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('We thank Alice Example',clean)
        self.assertIn('The dataset contains 120 samples.',clean)

    def test_connected_same_baseline_fragments_share_column(self):
        def row(text,x0,x1):
            return {'text':text,'rect':[x0,100,x1,110],'block':3,'size':10,'styled_heading':False}
        lines=[row('Left sentence ending with grant.',72,392),
               row('Author initials continue here.',400,540)]
        lefts=rules._column_lefts(lines)
        self.assertEqual(lefts[id(lines[1])],72)

    def test_same_baseline_real_gutter_keeps_columns_distinct(self):
        def row(x0,x1):
            return {'text':'Scientific prose','rect':[x0,100,x1,110],'block':3,'size':10,'styled_heading':False}
        lines=[row(58,294),row(318,554)]
        lefts=rules._column_lefts(lines)
        self.assertEqual(lefts[id(lines[0])],58)
        self.assertEqual(lefts[id(lines[1])],318)

    def test_explicit_owned_release_grammar_variants(self):
        import textwrap
        prefixes=['Our demo, code, and model are available at',
                  'Our method and the implementations of our experiments are available as open source code at',
                  'To support future research, we released our model and code at',
                  'We release our codes in',
                  'We share our code repository at',
                  'The method meets standards.Our code and data are publicly available at']
        uri='https://github.com/example/current'
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.make_release_pdf('\n'.join(textwrap.wrap(prefix+' '+uri,width=75,break_long_words=False)))
                self.assertNotIn(uri,self.apply(self.plan()))

    def test_complete_owned_url_survives_oversized_annotation_textbox(self):
        uri='https://github.com/example/current'
        sentence='Our code is available at '+uri+'.'
        self.make_pdf(extra=[(sentence,240)])
        with fitz.open(self.source) as doc:
            page=doc[0]
            rect=page.search_for(sentence)[0]
            page.insert_link({'kind':fitz.LINK_URI,'from':rect,'uri':uri})
            doc.save(self.source.with_name('wide-link.pdf'))
        self.source=self.source.with_name('wide-link.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn(uri,clean)
        self.assertIn('Our code is available at',clean)

    def test_incomplete_url_prefix_cannot_use_complete_visible_fallback(self):
        visible='https://github.com/example/'
        sentence='Our code is available at '+visible
        self.make_pdf(extra=[(sentence,240)])
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(sentence)[0],
                              'uri':visible+'current'})
            doc.save(self.source.with_name('prefix-link.pdf'))
        self.source=self.source.with_name('prefix-link.pdf')
        self.assertIn(visible,self.apply(self.plan()))

    def test_complete_owned_www_url_without_annotation(self):
        uri='www.example.edu/current'
        self.make_pdf(extra=[('Our code is available at '+uri,240)])
        self.assertNotIn(uri,self.apply(self.plan()))


    def make_ack_category_table(self, clinical=True):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=595,height=842)
            rows=([('Strengths and Achievements',270),
                   ('Acknowledgment',295),('Emotional Resilience',335)] if clinical else
                  [('Protocol States',270),('Acknowledgment',295),('Retransmission',335)])
            for text,y in rows:
                page.insert_text((125,y),text,fontsize=9)
            descriptions=([('Insufficient celebration of the patient efforts',295),
                           ('and accomplishments.',307),
                           ('Support development of coping strategies.',335),
                           ('Recognize achievable goals and self-acceptance.',347)] if clinical else
                          [('We acknowledge receipt of the message.',295),
                           ('The protocol sends confirmation packets.',307),
                           ('Retry messages after a measured timeout.',335),
                           ('The measured latency is 12 milliseconds.',347)])
            for text,y in descriptions:
                page.insert_text((240,y),text,fontsize=9)
            page.insert_text((105,390),'Table 40: Categories and descriptions for evaluation.',fontsize=10)
            doc.save(self.source.with_name('ack-category-table.pdf'))
        self.source=self.source.with_name('ack-category-table.pdf')
        return descriptions

    def test_scientific_table_acknowledgment_category_is_not_a_credit_section(self):
        descriptions=self.make_ack_category_table()
        clean=self.apply(self.plan())
        self.assertIn('Acknowledgment',clean)
        for text,_ in descriptions:
            self.assertIn(text,clean)
        self.assertIn('Table 40: Categories and descriptions',clean)

    def test_protocol_acknowledgment_category_has_no_author_credit_evidence(self):
        descriptions=self.make_ack_category_table(clinical=False)
        clean=self.apply(self.plan())
        for text,_ in descriptions:
            self.assertIn(text,clean)

    def test_real_acknowledgment_before_table_has_positive_author_credit(self):
        self.make_pdf(extra=[('Acknowledgment',300),
                            ('We thank Alice Example for helpful feedback.',320),
                            ('Table 7: Scientific results remain.',370)])
        clean=self.apply(self.plan())
        self.assertNotIn('We thank Alice Example',clean)
        self.assertIn('Table 7: Scientific results remain.',clean)

    def test_eof_named_contributions_need_no_final_period(self):
        self.paper['authors'].append('Bob Researcher')
        self.make_pdf(extra=[('Acknowledgments',300),
                            ('The research and writing of this paper were led by Alice Example.',325),
                            ('Bob Researcher designed the experiments and provided manuscript feedbacks',340)])
        clean=self.apply(self.plan())
        self.assertNotIn('Bob Researcher designed',clean)
        self.assertIn('Our scientific result is x = 42.',clean)


    def test_manuscript_support_is_credit_evidence(self):
        self.assertTrue(rules._credit_text('The work is supported by an Example research grant and the Science Foundation.','acknowledgment',[]))
        self.assertTrue(rules._credit_text('Acknowledgements. This work is supported by the Example Science Foundation (X12345).','acknowledgment',[]))
        self.assertFalse(rules._credit_text('This conclusion is supported by Figure 2.','acknowledgment',[]))

    def test_named_thanks_to_is_credit_evidence(self):
        self.assertTrue(rules._credit_text('Thanks to Alice Example and Bob Scholar for comments on this work.','acknowledgment',[]))
        self.assertFalse(rules._credit_text('Acknowledgment of received packets confirms network delivery.','acknowledgment',[]))

    def test_project_page_after_current_method_clause_is_owned(self):
        uri='https://project.example.edu/current'
        self.make_release_pdf('We introduce a new method. Our results improve accuracy. Project page: '+uri)
        self.assertNotIn(uri,self.apply(self.plan()))

    def test_project_page_after_borrowed_baseline_stays_scientific(self):
        uri='https://public.example.edu/baseline'
        self.make_release_pdf('We use the public baseline described by Smith et al. Project page: '+uri)
        self.assertIn(uri,self.apply(self.plan()))

    def test_exact_short_review_status_in_first_page_margin(self):
        self.make_pdf(extra=[('Conference paper under review',30)])
        self.assertNotIn('Conference paper under review',self.apply(self.plan()))

    def test_repeated_short_review_header_removed_without_body_phrase_loss(self):
        self.make_late_running_headers('Under Review',y=35,folio=True)
        clean=self.apply(self.plan())
        self.assertNotIn('Under Review',clean)
        self.assertIn('The scientific result is x = 42.',clean)

    def test_repeated_preprint_footer_uses_source_folio(self):
        self.make_pdf(extra=[('Preprint.',750)])
        with fitz.open(self.source) as doc:
            for _ in range(3):
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,740),'Preprint.',fontsize=9)
                page.insert_text((115,740),str(page.number+1),fontsize=9)
                page.insert_text((72,110),'Scientific content remains.',fontsize=11)
            doc.save(self.source.with_name('shifted-footer.pdf'))
        self.source=self.source.with_name('shifted-footer.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('Preprint.',clean)
        self.assertEqual(clean.count('Scientific content remains.'),3)

    def test_unanchored_preprint_reference_footer_is_preserved(self):
        self.make_late_running_headers('Preprint.',y=740,folio=False)
        self.assertIn('Preprint.',self.apply(self.plan()))

    def test_isolated_review_locator_in_frontmatter(self):
        self.make_pdf(author_lines=[('Alice Example',90),('Unknown source lab label',110),
                                    ('Reviewed on OpenReview: https: // openreview. net/ forum? id= ABC',140)])
        clean=self.apply(self.plan())
        self.assertNotIn('Reviewed on OpenReview:',clean)
        self.assertIn('Unknown source lab label',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_short_review_title_and_literal_are_preserved(self):
        self.paper['title']='Under Review'
        self.make_pdf(extra=[('The literal phrase Under Review is a training label.',240)])
        clean=self.apply(self.plan())
        self.assertIn('Under Review',clean)
        self.assertIn('The literal phrase Under Review is a training label.',clean)


    def test_repeated_bibliographic_preprint_header_without_folio_is_preserved(self):
        self.make_late_running_headers('Preprint.','2024. A cited scientific manuscript.',y=35,folio=False)
        self.assertEqual(self.apply(self.plan()).count('Preprint.'),3)

    def test_named_author_gratitude_is_positive_credit(self):
        self.assertTrue(rules._credit_text('We would like to express our sincere gratitude to Alice Scholar for helpful experiments.','acknowledgment',[]))
        self.assertTrue(rules._credit_text('The authors express gratitude to Prof. Bob Scholar for useful suggestions.','acknowledgment',[]))
        self.assertFalse(rules._credit_text('Does the model express gratitude to the patient?','acknowledgment',[]))


    def make_repeated_owned_locator(self, uri, body, annotations=()):
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_release_pdf('Our code is available at '+uri+'.')
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_textbox(fitz.Rect(72,100,540,650),body,fontsize=11)
            for visible,target in annotations:
                for rect in page.search_for(visible):
                    page.insert_link({'kind':fitz.LINK_URI,'from':rect,'uri':target})
            doc.save(self.source.with_name('repeat.pdf'))
        self.source=self.source.with_name('repeat.pdf')

    def test_proven_locator_repeated_with_standalone_protocol(self):
        uri='https://huggingface.co/example/CurrentModel'
        self.make_repeated_owned_locator(uri,'1. Code, Data, and Checkpoints: The com-\n'
            'plete resources are available at https://\n'
            'huggingface.co/example/CurrentModel.\n'
            'We retain the reproducibility details and score 42.')
        plan=self.plan();clean=self.apply(plan)
        self.assertNotIn('huggingface.co/example/CurrentModel',clean)
        self.assertIn('We retain the reproducibility details and score 42.',clean)
        self.assertIn(uri,plan['forbidden_strings'])

    def test_proven_locator_preserves_meaningful_wrapped_host_hyphen(self):
        uri='https://example-research.github.io/current'
        self.make_repeated_owned_locator(uri,'We encourage inspection of our visual results and additional visualiza-\n'
            'tions in the accompanying website in https://example-\n'
            'research.github.io/current.\n'
            'A. Ethics Statement\n'
            'All participants provided consent.')
        clean=self.apply(self.plan())
        self.assertNotIn('https://example-',clean)
        self.assertNotIn('research.github.io/current',clean)
        self.assertIn('tions in the accompanying website in',clean)
        self.assertIn('A. Ethics Statement',clean)
        self.assertIn('All participants provided consent.',clean)

    def test_annotation_bound_split_scheme_locator(self):
        uri='https://example.org/release'
        self.make_release_pdf('Our code and data are available at https:\n'
            '//example.org/release. Scientific accuracy remains 93%.',
            [('https:',uri),('//example.org/release',uri)])
        clean=self.apply(self.plan())
        self.assertNotIn('//example.org/release',clean)
        self.assertNotIn('https:',clean)
        self.assertIn('Scientific accuracy remains 93%.',clean)

    def test_annotation_bound_spaced_locator_preserves_path_hyphen(self):
        uri='https://example.org/owner/finetuned-caption'
        self.make_release_pdf('Our model is available at https : / / example . org / owner / finetuned -\n'
            'caption. We preserve the evaluation procedure.',
            [('https : / / example . org / owner / finetuned -',uri),('caption',uri)])
        clean=self.apply(self.plan())
        self.assertNotIn('https :',clean)
        self.assertNotIn('caption.',clean)
        self.assertIn('We preserve the evaluation procedure.',clean)

    def test_proven_locator_in_reference_is_preserved(self):
        uri='https://example.org/current'
        self.make_repeated_owned_locator(uri,'References\n'
            'Alice Scholar. A cited work. https://\nexample.org/current.')
        clean=self.apply(self.plan())
        self.assertIn('example.org/current',clean)
        self.assertIn('Alice Scholar. A cited work.',clean)

    def test_proven_locator_in_literal_and_dependency_context_is_preserved(self):
        uri='https://example.org/current'
        for prefix in ['Example: Our code is available at',
                       'The literal URL is:',
                       'The phrase "our code is available at',
                       'We use the third-party baseline code available at']:
            with self.subTest(prefix=prefix):
                self.make_repeated_owned_locator(uri,prefix+' https://\nexample.org/current.')
                self.assertIn('example.org/current',self.apply(self.plan()))

    def test_annotation_does_not_join_different_source_blocks(self):
        uri='https://example.org/current'
        self.make_pdf(extra=[('Our code is available at https:',240),
                             ('//example.org/current',400)])
        with fitz.open(self.source) as doc:
            page=doc[0]
            for fragment in ('https:','//example.org/current'):
                for rect in page.search_for(fragment):
                    page.insert_link({'kind':fitz.LINK_URI,'from':rect,'uri':uri})
            doc.save(self.source.with_name('separate.pdf'))
        self.source=self.source.with_name('separate.pdf')
        self.assertIn('//example.org/current',self.apply(self.plan()))

    def test_known_uri_prefix_does_not_remove_different_longer_locator(self):
        uri='https://example.org/current'
        self.make_repeated_owned_locator(uri,'Additional material: https://\nexample.org/current-other.')
        self.assertIn('example.org/current-other',self.apply(self.plan()))

    def test_known_uri_does_not_dehyphenate_source_host(self):
        uri='https://exampleresearch.github.io/current'
        self.make_repeated_owned_locator(uri,'Additional material: https://example-\nresearch.github.io/current.')
        clean=self.apply(self.plan())
        self.assertIn('https://example-',clean)
        self.assertIn('research.github.io/current',clean)


    def make_reference_then_supplement(self, matching_title):
        uri='https://example-research.github.io/current'
        self.make_release_pdf('Our code is available at '+uri+'.\nReferences\nA cited work.')
        with fitz.open(self.source) as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'] if matching_title else 'A Cited Different Paper',fontsize=16)
            page.insert_text((72,90),'Supplementary Material',fontsize=14)
            page.insert_textbox(fitz.Rect(72,130,540,220),
                'Inspect our visual results at https://example-\nresearch.github.io/current.',fontsize=11)
            doc.save(self.source.with_name('supplement.pdf'))
        self.source=self.source.with_name('supplement.pdf')

    def test_matching_supplement_title_ends_reference_context(self):
        self.make_reference_then_supplement(True)
        clean=self.apply(self.plan())
        self.assertNotIn('research.github.io/current',clean)
        self.assertIn('Supplementary Material',clean)
        self.assertIn('Inspect our visual results at',clean)

    def test_other_paper_supplement_title_does_not_end_reference_context(self):
        self.make_reference_then_supplement(False)
        self.assertIn('research.github.io/current',self.apply(self.plan()))

    def test_explicit_finetuned_model_release_with_annotation(self):
        uri='https://example.org/owner/finetuned-caption'
        self.make_release_pdf('1To facilitate reproducibility and encourage further research, we\n'
            'made our fine-tuned caption embedding model publicly available\n'
            'at https : / / example . org / owner / finetuned -\ncaption.',
            [('https : / / example . org / owner / finetuned -',uri),('caption.',uri)])
        clean=self.apply(self.plan())
        self.assertNotIn('https :',clean)
        self.assertIn('made our fine-tuned caption embedding model publicly available',clean)

    def test_made_model_dependency_and_quoted_release_are_preserved(self):
        uri='https://example.org/owner/finetuned-caption'
        for prefix in ['We made our model using baseline code available at',
                       'Example: We made our caption model publicly available at']:
            with self.subTest(prefix=prefix):
                self.make_release_pdf(prefix+' https : / / example . org / owner / finetuned -\ncaption.',
                    [('https : / / example . org / owner / finetuned -',uri),('caption.',uri)])
                self.assertIn('https :',self.apply(self.plan()))


    def test_contact_recovered_after_unknown_header_row(self):
        self.make_pdf(author_lines=[('Alice Example',90),('opaque-source-label',110),
                                    ('alice@example.edu',135)])
        clean=self.apply(self.plan())
        self.assertNotIn('alice@example.edu',clean)
        self.assertIn('opaque-source-label',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_numbered_affiliation_recovered_after_header_gap(self):
        self.make_pdf(author_lines=[('Alice Example1',90),('1Harbor University',150)])
        clean=self.apply(self.plan())
        self.assertNotIn('1Harbor University',clean)
        self.assertIn('Abstract',clean)

    def test_science_context_blocks_header_contact_recovery(self):
        for cue in ['Example input:','Figure 1. Teaser','Prompt:','Table 1. Results']:
            with self.subTest(cue=cue):
                self.make_pdf(author_lines=[('Alice Example',90),('opaque-source-label',110),
                                           (cue,130),('alice@example.edu',150)])
                clean=self.apply(self.plan())
                self.assertIn('alice@example.edu',clean)
                self.assertIn(cue,clean)

    def test_source_folio_overlap_with_superscript_byline(self):
        def line(text,rect,block=0):
            return {'text':text,'rect':rect,'block':block,'size':10,'styled_heading':False}
        pages=[[],[],[]]
        for n in [2,3]:
            pages[n-1]=[line('Alice Example*',[110,90,245,102]),
                        line(str(n),[72,91.4,80,100.4]),
                        line('The scientific result is exact.',[72,135,360,147],1)]
        expected={(2,0),(3,0)}
        self.assertEqual(rules._source_author_headers(pages,[(612,792)]*3,['alice example']),expected)
        for rows in pages[1:]:
            rows[-1]['text']='2024. A cited scientific reference.'
        self.assertFalse(rules._source_author_headers(pages,[(612,792)]*3,['alice example']))

    def test_numbered_accent_affiliation_and_numeric_science_guards(self):
        def line(text,y):
            return {'text':text,'rect':[72,y,520,y+10],'block':0,'size':10,'styled_heading':False}
        header=[line('Alice Example2',80),line('2ETH Z¨urich, Vision Group',125),
                line('alice@example.edu',145)]
        self.assertTrue(rules._short_affiliation(header[1],header,{0},['alice example']))
        for text in ['2Example input','2Training accuracy','2Theorem Results']:
            self.assertFalse(rules._short_affiliation({**header[1],'text':text},header,{0},['alice example']))


    def fragmented_note(self):
        def line(text,x0,y,x1):
            return {'text':text,'rect':[x0,y,x1,y+10],'block':8,'size':9,'styled_heading':False}
        return [line('*',72,700,76),line('Department',80,700,128),
                line('of',132,700,140),line('Computer',144,700,188),
                line('Science,',192,700,230),
                line('Harbor University. E-mail:',72,712,205),
                line('alice@harbor.edu',72,724,180)]

    def test_fragmented_author_note_returns_original_word_rectangles(self):
        lines=self.fragmented_note()
        got=rules._fragmented_affiliation_notes(lines,792,{'*'})
        self.assertEqual([id(x) for x in got],[id(x) for x in lines])

    def test_fragmented_author_note_marker_contact_and_column_guards(self):
        import copy
        lines=self.fragmented_note()
        self.assertEqual(rules._fragmented_affiliation_notes(lines,792,set()),[])
        self.assertEqual(rules._fragmented_affiliation_notes(lines[:-1],792,{'*'}),[])
        split=copy.deepcopy(lines);split[3]['rect'][0]+=40;split[3]['rect'][2]+=40
        self.assertEqual(rules._fragmented_affiliation_notes(split,792,{'*'}),[])

    def test_fragmented_note_preserves_science_examples_and_body(self):
        import copy
        for cue in ['Example input','Figure training','Our experiment']:
            lines=self.fragmented_note();lines[2]['text']=cue
            self.assertEqual(rules._fragmented_affiliation_notes(lines,792,{'*'}),[])
        lines=self.fragmented_note()
        for line in lines:line['rect'][1]-=400;line['rect'][3]-=400
        self.assertEqual(rules._fragmented_affiliation_notes(lines,792,{'*'}),[])


    def make_isolated_credit_note(self, text, page_number=1, y=700, label=None, affiliation=None):
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_pdf(author_lines=[('Alice Example*',90)]+([(affiliation,110)] if affiliation else []))
        with fitz.open(self.source) as doc:
            while len(doc)<page_number:
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,100),'Scientific comparison: accuracy is 94 percent.')
            page=doc[page_number-1]
            if label:page.insert_text((72,y-20),label,fontsize=9)
            page.insert_textbox(fitz.Rect(72,y,540,min(y+75,785)),text,fontsize=9)
            doc.save(self.source.with_name('credit-note.pdf'))
        self.source=self.source.with_name('credit-note.pdf')

    def test_isolated_contribution_and_internship_notes(self):
        cases=['*Contribution during internship at Harbor Group.',
               '*This work was conducted during the author\'s internship\nat Harbor Team.',
               '*Work done while interning at Harbor Research.',
               '*This work was finalized during the first author\'s visit to Harbor University.']
        for note in cases:
            with self.subTest(note=note):
                self.make_isolated_credit_note(note)
                clean=self.apply(self.plan())
                self.assertNotIn('Harbor',clean)
                self.assertIn('Our scientific result is x = 42.',clean)

    def test_metadata_backed_joint_work_author_note(self):
        self.paper['authors']=['Alice Example','Bob Scholar']
        self.make_isolated_credit_note('*While joint work between Example and Scholar served\n'
            'as a catalyst for this paper, the core methods proposed herein\n'
            'are attributable to Example, who is the corresponding author.')
        self.assertNotIn('are attributable to Example',self.apply(self.plan()))

    def test_joint_work_note_with_unknown_surnames_is_preserved(self):
        self.make_isolated_credit_note('*While joint work between External and Person served\n'
            'as a catalyst for this paper, the core methods proposed herein\n'
            'are attributable to External, who is the corresponding author.')
        self.assertIn('are attributable to External',self.apply(self.plan()))

    def test_later_named_thanks_footnote_only_removes_credit_sentence(self):
        self.make_isolated_credit_note('3 We thank Cora Scientist for her help capturing these photos.\n'
            'Scientific resolution remains 2048 pixels.',page_number=2)
        clean=self.apply(self.plan())
        self.assertNotIn('Cora Scientist',clean)
        self.assertIn('Scientific resolution remains 2048 pixels.',clean)
        self.assertIn('Scientific comparison: accuracy is 94 percent.',clean)

    def test_verification_thanks_footnote(self):
        self.make_isolated_credit_note('1We thank Cora Scientist for data verification and fact-checking.',page_number=2)
        self.assertNotIn('Cora Scientist',self.apply(self.plan()))

    def test_identical_credit_note_inside_prompt_is_preserved(self):
        self.make_isolated_credit_note('3 We thank Cora Scientist for her help capturing these photos.',
            page_number=2,label='Prompt:')
        self.assertIn('Cora Scientist',self.apply(self.plan()))

    def test_body_credit_and_incomplete_note_are_preserved(self):
        self.make_isolated_credit_note('3 We thank Cora Scientist for useful feedback.',page_number=2,y=300)
        self.assertIn('Cora Scientist',self.apply(self.plan()))
        self.make_isolated_credit_note('*Contribution during internship at Harbor Group')
        self.assertIn('Harbor Group',self.apply(self.plan()))

    def test_third_party_biography_and_borrowed_resource_are_preserved(self):
        for note in ['1Smith completed an internship at Harbor Group before publishing this baseline.',
                     '1We thank Baseline Library for making the model available.',
                     '1The first author hypothesized that the method uses contextual embeddings.']:
            with self.subTest(note=note):
                self.make_isolated_credit_note(note,page_number=2)
                self.assertIn(note,self.apply(self.plan()).replace('\n',' '))

    def test_new_marker_does_not_complete_unfinished_credit_note(self):
        self.make_isolated_credit_note('*This work was conducted during an internship\n'
            '2The scientific study took place at Harbor University.')
        self.assertIn('The scientific study took place',self.apply(self.plan()))


    def test_unpunctuated_employer_note_requires_source_affiliation(self):
        self.make_isolated_credit_note('*Work done while interning at Harbor Research Inc',
            affiliation='Harbor Research Inc.')
        clean=self.apply(self.plan())
        self.assertNotIn('interning at Harbor Research Inc',clean)
        self.assertIn('Our scientific result is x = 42.',clean)
        self.make_isolated_credit_note('*Work done while interning at Harbor Research',
            affiliation='Other University')
        self.assertIn('interning at Harbor Research',self.apply(self.plan()))

    def test_closed_named_thanks_preserves_neighboring_science(self):
        self.make_release_pdf('Our result is 42. (Thank you Cora Scientist for the nudge!)\n'
            'The scientific bound remains tight.')
        clean=self.apply(self.plan())
        self.assertNotIn('Cora Scientist',clean)
        self.assertIn('Our result is 42.',clean)
        self.assertIn('The scientific bound remains tight.',clean)

    def test_verified_coauthor_initial_attribution_is_a_partial_slice(self):
        self.paper['authors']=['Alice Scholar','Bob Scientist']
        self.make_release_pdf('Some co-authors kept wondering, especially AS and BS:\n'
            'What happens to visual tokens after the input?')
        clean=self.apply(self.plan())
        self.assertNotIn('especially AS and BS',clean)
        self.assertIn('Some co-authors kept wondering',clean)
        self.assertIn('What happens to visual tokens after the input?',clean)

    def test_parenthetical_credit_with_known_initial_and_named_person(self):
        self.paper['authors']=['Alice Scholar']
        self.make_release_pdf('We use contextual embeddings instead of static matrices?\n'
            '(Thank you AS and Cora Scientist for the nudge!)')
        clean=self.apply(self.plan())
        self.assertNotIn('Thank you AS',clean)
        self.assertIn('We use contextual embeddings instead of static matrices?',clean)

    def test_ambiguous_and_unknown_coauthor_initials_are_preserved(self):
        self.paper['authors']=['Alice Scholar','Alan Smith','Bob Scientist']
        for initials in ['AS and BS','ZZ and BS']:
            self.make_release_pdf('Some co-authors kept wondering, especially '+initials+':\n'
                'What happens to visual tokens after the input?')
            self.assertIn('especially '+initials,self.apply(self.plan()))

    def test_quoted_and_scientific_credit_stimuli_are_preserved(self):
        samples=['Prompt: "(Thank you Cora Scientist for the nudge!)"',
                 'We evaluate whether (Thank you Cora Scientist for the feedback!) is formal.',
                 'The biography quoted: (Thank you Cora Scientist for the help!)']
        for text in samples:
            with self.subTest(text=text):
                self.make_release_pdf(text)
                self.assertIn('Cora Scientist',self.apply(self.plan()))

    def test_parenthetical_bibliographic_title_is_preserved(self):
        self.make_release_pdf('References\n'
            'A. Scholar. 2024. A Study (Thank you Cora Scientist for the nudge!).')
        self.assertIn('Cora Scientist',self.apply(self.plan()))

    def test_anonymous_parenthetical_gratitude_and_scientific_attribution_are_preserved(self):
        self.make_release_pdf('Our result is exact. (Thank you Anonymous Reviewers for the feedback!)\n'
            'The first author hypothesized that contextual embeddings matter.')
        clean=self.apply(self.plan())
        self.assertIn('Anonymous Reviewers',clean)
        self.assertIn('The first author hypothesized that contextual embeddings matter.',clean)


    def make_publication_note(self, note, page_number=1, y=675, label=None, width=230):
        self.source=Path(self.temp.name)/'source.pdf';self.make_pdf()
        with fitz.open(self.source) as doc:
            while len(doc)<page_number:
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,90),'References',fontsize=12)
            page=doc[page_number-1]
            if label:page.insert_text((72,y-20),label,fontsize=9)
            page.insert_textbox(fitz.Rect(72,y,72+width,760),note,fontsize=8)
            doc.save(self.source.with_name('publisher.pdf'))
        self.source=self.source.with_name('publisher.pdf')

    def test_complete_publisher_notice_needs_no_removed_contact(self):
        note='Proceedings of the International Conference on Science,\n'
        note+='Harbor City. PMLR 123, 2025. Copyright 2025\nby the author(s).'
        self.make_publication_note(note)
        clean=self.apply(self.plan())
        self.assertNotIn('PMLR 123',clean)
        self.assertNotIn('by the author(s)',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_standalone_manuscript_copyright_footer(self):
        self.make_publication_note('Copyright 2025 by the author(s).',y=700)
        self.assertNotIn('Copyright 2025',self.apply(self.plan()))

    def test_metadata_author_copyright_footer(self):
        self.make_publication_note('© A. Example.',y=700)
        self.assertNotIn('© A. Example.',self.apply(self.plan()))

    def test_unmatched_copyright_name_preserved(self):
        self.make_publication_note('© B. External.',y=700)
        self.assertIn('© B. External.',self.apply(self.plan()))

    def test_submission_notice_continuation_is_bounded(self):
        self.make_publication_note('Submitted to the 38th Conference on Scientific Methods (2025)\n'
            'Track on Data and Benchmarks. Do not distribute.',y=700,width=430)
        clean=self.apply(self.plan())
        self.assertNotIn('Do not distribute',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_permission_isbn_doi_publisher_block(self):
        self.make_publication_note('Permission to make digital or hard copies of all or part of this work\n'
            'is granted without fee. Request permissions from permissions@example.org.\n'
            'Copyright held by the owner/author(s). Publication rights licensed to Publisher.\n'
            'Publisher ISBN 978-1-2345-6789-0\nhttps://doi.org/10.1234/example',y=640)
        self.assertNotIn('Publication rights licensed',self.apply(self.plan()))

    def test_cited_and_quoted_publisher_boilerplate_is_preserved(self):
        note='Proceedings of the International Conference. PMLR 123,\n2025. Copyright 2025 by the author(s).'
        self.make_publication_note(note,page_number=2)
        self.assertIn('PMLR 123',self.apply(self.plan()))
        self.make_publication_note(note,label='Quoted example:')
        self.assertIn('PMLR 123',self.apply(self.plan()))

    def test_scientific_copyright_body_is_preserved(self):
        self.make_publication_note('Copyright and privacy. We provide licensing options for the dataset.',y=300)
        clean=self.apply(self.plan())
        self.assertIn('Copyright and privacy.',clean)
        self.assertIn('We provide licensing options for the dataset.',clean.replace('\n',' '))

    def make_metadata_banner(self, text, source_metadata, publication=True, y=30):
        self.paper.update(source_metadata);self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc[0];page.insert_text((72,y),text,fontsize=9)
            if publication:page.insert_text((72,750),'Preprint.',fontsize=9)
            doc.save(self.source.with_name('banner.pdf'))
        self.source=self.source.with_name('banner.pdf')

    def test_source_venue_year_banner_above_exact_title(self):
        self.make_metadata_banner('ScienceConf 2025',{'source_entries':[{'venue':'ScienceConf','venue_year':2025}]})
        clean=self.apply(self.plan())
        self.assertNotIn('ScienceConf 2025',clean)
        self.assertIn('A Scientific Paper',clean)

    def test_metadata_date_banner_requires_notice_evidence(self):
        metadata={'versions':[{'version':2,'submitted_at':'2025-08-24T22:09:57Z'}]}
        self.make_metadata_banner('2025-08-24',metadata)
        self.assertNotIn('2025-08-24',self.apply(self.plan()))
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_metadata_banner('2025-08-24',metadata,publication=False)
        self.assertIn('2025-08-24',self.apply(self.plan()))

    def test_unmatched_body_and_title_banner_values_are_preserved(self):
        self.make_metadata_banner('ScienceConf 2024',{'source_entries':[{'venue':'ScienceConf','venue_year':2025}]})
        self.assertIn('ScienceConf 2024',self.apply(self.plan()))
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_metadata_banner('2025-08-24',{'versions':[{'submitted_at':'2025-08-24T22:09:57Z'}]},y=240)
        self.assertIn('2025-08-24',self.apply(self.plan()))
        self.paper['title']='ScienceConf 2025'
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_metadata_banner('Unrelated banner',{'source_entries':[{'venue':'ScienceConf','venue_year':2025}]})
        self.assertIn('ScienceConf 2025',self.apply(self.plan()))

    def test_wrapped_metadata_name_suffix_in_byline(self):
        self.paper['authors']=['Alice Example','Carol Researcher','Quentin Scholar']
        self.make_pdf(author_lines=[])
        with fitz.open(self.source) as doc:
            page=doc[0]
            row='Alice Example1, Carol Researcher2, and Bob Quentin'
            tail='Scholar3'
            page.insert_text((72,90),row,fontsize=11)
            x=72+(fitz.get_text_length(row,fontsize=11)-fitz.get_text_length(tail,fontsize=11))/2
            page.insert_text((x,108),tail,fontsize=11)
            page.insert_text((72,132),'3Harbor University',fontsize=11)
            doc.save(self.source.with_name('wrapped-card.pdf'))
        self.source=self.source.with_name('wrapped-card.pdf')
        with fitz.open(self.source) as doc:
            blocks=doc[0].get_text('dict')['blocks']
            containing=[i for i,block in enumerate(blocks) if block['type']==0 and
                        any(row in span['text'] or tail in span['text'] for line in block['lines'] for span in line['spans'])]
            self.assertEqual(len(containing),2,'The byline and centered surname must be separate source blocks.')
        clean=self.apply(self.plan())
        self.assertNotIn('Scholar3',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_short_surname_without_metadata_prefix_is_preserved(self):
        self.paper['authors']=['Alice Example','Bob Scholar']
        self.make_pdf(author_lines=[('Alice Example',90),('Scholar2',108)])
        self.assertIn('Scholar2',self.apply(self.plan()))


    def test_generated_and_captioned_gratitude_is_scientific_content(self):
        samples=['The model produced (Thank you Cora Scientist for the help!).',
                 'Figure 1. Conversation data (Thank you Cora Scientist for the help!).']
        for text in samples:
            with self.subTest(text=text):
                self.make_release_pdf(text)
                self.assertIn('Cora Scientist',self.apply(self.plan()))


    def test_annotated_owned_bare_host_locator(self):
        visible='code.example.org/current'
        self.make_release_pdf('Our code is available at '+visible+'. Scientific accuracy is 94%.',
            [(visible,'https://'+visible)])
        clean=self.apply(self.plan())
        self.assertNotIn(visible,clean)
        self.assertIn('Scientific accuracy is 94%.',clean)

    def test_annotated_owned_repository_path(self):
        visible='research-team/current'
        self.make_release_pdf('We release our code at '+visible+'. The proof is unchanged.',
            [(visible,'https://code.example.org/'+visible)])
        clean=self.apply(self.plan())
        self.assertNotIn(visible,clean)
        self.assertIn('The proof is unchanged.',clean)

    def test_bounded_header_code_path_after_unknown_row(self):
        visible='research-team/current'
        self.make_pdf(author_lines=[('Alice Example',90),('opaque-source-label',110),
                                    ('Code: '+visible,135)])
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(visible)[0],
                              'uri':'https://code.example.org/'+visible})
            doc.save(self.source.with_name('bare-code.pdf'))
        self.source=self.source.with_name('bare-code.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn(visible,clean)
        self.assertIn('Code:',clean)
        self.assertIn('opaque-source-label',clean)

    def test_plain_linked_labels_and_method_names_are_preserved(self):
        uri='https://code.example.org/research-team/Method'
        self.make_release_pdf('Our code is available at Method. Home and Code remain visible labels.',
            [('Method',uri),('Home',uri),('Code',uri)])
        clean=self.apply(self.plan())
        self.assertIn('Method',clean)
        self.assertIn('Home and Code remain visible labels.',clean)

    def test_scientific_bare_resource_and_quoted_locator_are_preserved(self):
        visible='research-team/baseline'
        for prefix in ['We use the third-party baseline code at',
                       'Example: Our code is available at']:
            self.make_release_pdf(prefix+' '+visible+'.',
                [(visible,'https://code.example.org/'+visible)])
            self.assertIn(visible,self.apply(self.plan()))

    def test_bare_locator_in_references_is_preserved(self):
        visible='research-team/baseline'
        self.make_release_pdf('References\nA. Scholar. Code available at '+visible+'.',
            [(visible,'https://code.example.org/'+visible)])
        self.assertIn(visible,self.apply(self.plan()))

    def test_incomplete_or_mismatched_annotated_paths_are_preserved(self):
        for visible,target in [('research-team/','https://code.example.org/research-team/current'),
                               ('other-team/current','https://code.example.org/research-team/current')]:
            self.make_release_pdf('Our code is available at '+visible,[(visible,target)])
            self.assertIn(visible,self.apply(self.plan()))

    def test_unannotated_bare_locator_stays_unresolved(self):
        self.make_release_pdf('Our code is available at code.example.org/current.')
        self.assertIn('code.example.org/current',self.apply(self.plan()))

    def test_path_like_method_mention_does_not_inherit_release_ownership(self):
        visible='research-team/Method';uri='https://code.example.org/'+visible
        self.make_release_pdf('Our code is available at '+visible+'.\n'
            +visible+' remains the scientific name of the method.',[(visible,uri)])
        clean=self.apply(self.plan())
        self.assertEqual(clean.count(visible),1)
        self.assertIn(visible+' remains the scientific name of the method.',clean)

    def test_header_example_cue_blocks_artifact_label(self):
        visible='research-team/current'
        self.make_pdf(author_lines=[('Alice Example',90),('Example input:',110),
                                    ('Code: '+visible,135)])
        with fitz.open(self.source) as doc:
            page=doc[0];page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(visible)[0],
                                         'uri':'https://code.example.org/'+visible})
            doc.save(self.source.with_name('example-code.pdf'))
        self.source=self.source.with_name('example-code.pdf')
        self.assertIn(visible,self.apply(self.plan()))


    def make_superscript_credit(self, body):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.insert_text((72,700),'*',fontsize=6)
            page.insert_text((77,704),body,fontsize=9)
            doc.save(self.source.with_name('superscript-credit.pdf'))
        self.source=self.source.with_name('superscript-credit.pdf')

    def test_superscript_partial_credit_has_exact_text_per_rectangle(self):
        self.make_superscript_credit('Contribution during internship at Harbor Group. The scientific bound is 97 percent.')
        plan=self.plan()
        self.assertTrue(any('Contribution' in r['text'] for r in plan['redactions']))
        with fitz.open(self.source) as doc:
            pdf_helpers._text_only_rectangles(doc[0],[r for r in plan['redactions'] if r['page']==1])
        clean=self.apply(plan)
        self.assertNotIn('Harbor Group',clean)
        self.assertIn('The scientific bound is 97 percent.',clean)

    def test_full_identity_line_does_not_duplicate_partial_credit_rectangles(self):
        self.make_superscript_credit("Work done during Alice's internship at Harbor University. Bob Scholar is the project lead.")
        plan=self.plan()
        items=[r for r in plan['redactions'] if 'internship' in r['text']]
        self.assertEqual(len(items),1)
        self.assertIn('Bob Scholar',items[0]['text'])
        with fitz.open(self.source) as doc:
            pdf_helpers._text_only_rectangles(doc[0],[r for r in plan['redactions'] if r['page']==1])

    def test_nonidentifying_single_label_url_placeholder_is_preserved(self):
        self.make_pdf(author_lines=[('Alice Example',90),('Code: https://anonymous',120)])
        self.assertIn('https://anonymous',self.apply(self.plan()))


    def test_split_uri_uses_connected_source_row_envelope(self):
        self.make_pdf()
        uri='https://code.example.org/research-team/study'
        with fitz.open(self.source) as doc:
            page=doc[0]
            x=72
            for word in ['Our','code','is','available','at','https://code.example.org/']:
                page.insert_text((x,700),word,fontsize=9)
                x += fitz.get_text_length(word,fontsize=9)+9
            page.insert_text((72,712),'research-team/study',fontsize=9)
            for target in ['https://code.example.org/','research-team/study']:
                page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(target)[0],'uri':uri})
            doc.save(self.source.with_name('connected-row.pdf'))
        self.source=self.source.with_name('connected-row.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('research-team/study',clean)
        self.assertIn('Our scientific result is x = 42.',clean)

    def test_owned_reproducibility_package_doi_is_a_locator(self):
        uri='https://doi.org/10.1234/reproducibility-data'
        self.make_release_pdf('A reproducibility package containing all data from our experiments '
                              'is available at '+uri+'.',[(uri,uri)])
        self.assertNotIn(uri,self.apply(self.plan()))

    def test_cited_reproducibility_package_doi_is_preserved(self):
        uri='https://doi.org/10.1234/cited-package'
        self.make_release_pdf('We use the cited reproducibility package at '+uri+'.',[(uri,uri)])
        self.assertIn(uri,self.apply(self.plan()))

    def test_numerical_and_both_implementations_are_releases(self):
        uri='https://code.example.org/current'
        for prefix in ['The numerical implementation can be found at',
                       'Both implementations are available at']:
            with self.subTest(prefix=prefix):
                self.make_release_pdf(prefix+' '+uri+'.',[(uri,uri)])
                self.assertNotIn(uri,self.apply(self.plan()))

    def test_footer_callout_requires_owned_release_sentence(self):
        uri='https://code.example.org/current'
        for sentence,remove in [
                ('We release all our code for research purposes1.',True),
                ('We use the code1 associated with the paper (Doe et al., 2021).',False)]:
            self.source=Path(self.temp.name)/'source.pdf'
            self.make_pdf(extra=[(sentence,260)])
            with fitz.open(self.source) as doc:
                page=doc[0];page.insert_text((72,700),'1'+uri,fontsize=9)
                page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(uri)[0],'uri':uri})
                doc.save(self.source.with_name('callout.pdf'))
            self.source=self.source.with_name('callout.pdf')
            clean=self.apply(self.plan())
            self.assertEqual(uri not in clean,remove)


    def test_mixed_ack_support_keeps_procedure_and_payments(self):
        self.make_pdf(extra=[('Acknowledgments',300),
            ('Support for this project was provided by the Science Foundation.',322),
            ('We managed data collection and contributor payments through custom UIs.',345),
            ('References',385)])
        clean=self.apply(self.plan())
        self.assertNotIn('Science Foundation',clean)
        self.assertIn('We managed data collection and contributor payments through custom UIs.',clean)

    def test_mixed_ack_funding_keeps_appendix_update(self):
        self.make_pdf(extra=[('Acknowledgments',300),
            ('We thank the reviewers for their valuable feedback.',320),
            ('To enhance our survey, we have added further discussion in the appendix.',340),
            ('This work was supported by the Research Foundation under grant 12345.',360),
            ('References',390)])
        clean=self.apply(self.plan())
        self.assertNotIn('grant 12345',clean)
        self.assertIn('To enhance our survey, we have added further discussion in the appendix.',clean)


    def test_header_data_code_badge_keeps_its_label(self):
        visible='current-2025.example.org'
        self.make_pdf(author_lines=[('Alice Example',90),('Data & Code: '+visible,130)])
        with fitz.open(self.source) as doc:
            page=doc[0];page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(visible)[0],
                                        'uri':'https://'+visible})
            doc.save(self.source.with_name('data-code-badge.pdf'))
        self.source=self.source.with_name('data-code-badge.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn(visible,clean)
        self.assertIn('Data & Code:',clean)

    def test_named_project_footer_needs_manuscript_and_author_proof(self):
        self.paper['title']='CurrentModel: A Scientific Study'
        for owner,scaffold,remove in [('alice-example','',True),('external-team','',False),
                                     ('alice-example','Example input:\n',False)]:
            self.source=Path(self.temp.name)/'source.pdf'
            self.make_pdf(extra=[('In this paper, we introduce CurrentModel.',240)])
            visible=owner+'.example.org/CurrentModel'
            with fitz.open(self.source) as doc:
                page=doc[0]
                page.insert_textbox(fitz.Rect(72,670,340,740),scaffold+'Project website: '+visible+
                                    '\nCorrespondence to alice@example.edu.',fontsize=8,lineheight=1.4)
                page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(visible)[0],
                                  'uri':'https://'+visible})
                doc.save(self.source.with_name('project-footer.pdf'))
            self.source=self.source.with_name('project-footer.pdf')
            self.assertEqual(visible not in self.apply(self.plan()),remove)


    def test_later_title_in_teaser_does_not_move_author_boundary(self):
        self.make_pdf()
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.draw_rect(fitz.Rect(300,138,550,155),color=(0,0,0))
            page.insert_text((305,150),'Figure 1. A Scientific Paper demonstrates the result.',fontsize=8)
            doc.save(self.source.with_name('repeat-title.pdf'))
        self.source=self.source.with_name('repeat-title.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example',clean)
        self.assertNotIn('alice@example.edu',clean)
        self.assertIn('Figure 1. A Scientific Paper demonstrates the result.',clean)

    def test_wrapped_primary_title_remains_a_contiguous_title(self):
        self.paper['title']='A Scientific Paper With A Wrapped Title'
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),'A Scientific Paper With A',fontsize=16)
            page.insert_text((72,80),'Wrapped Title',fontsize=16)
            page.insert_text((72,110),'Alice Example',fontsize=11)
            page.insert_text((72,130),'Harbor University',fontsize=10)
            page.insert_text((72,150),'alice@harbor.edu',fontsize=9)
            page.insert_text((72,170),'Figure 1. A Scientific Paper With A Wrapped Title illustrates science.',fontsize=8)
            page.insert_text((72,195),'Abstract',fontsize=12)
            page.insert_text((72,220),'Our scientific result is x = 42.')
            doc.save(self.source)
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example',clean)
        self.assertIn('Wrapped Title',clean)
        self.assertIn('Figure 1. A Scientific Paper With A Wrapped Title illustrates science.',clean)

    def test_only_caption_title_match_uses_dominant_structural_title(self):
        self.make_pdf(title='An Earlier Scientific Title')
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.insert_text((305,150),'Figure 1. A Scientific Paper illustrates science.',fontsize=8)
            doc.save(self.source.with_name('changed-title-caption.pdf'))
        self.source=self.source.with_name('changed-title-caption.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example',clean)
        self.assertIn('An Earlier Scientific Title',clean)
        self.assertIn('Figure 1. A Scientific Paper illustrates science.',clean)

    def test_whole_template_header_contact_after_country(self):
        self.make_pdf(author_lines=[('Alice Example',90),('Harbor University',110),
                                    ('Harbor City, Germany',125),('{firstname}.{lastname}@harbor.edu',145)])
        clean=self.apply(self.plan())
        self.assertNotIn('{firstname}.{lastname}@harbor.edu',clean)
        self.assertIn('A Scientific Paper',clean)


    def test_later_caption_institution_words_are_scientific_text(self):
        self.paper['title']='University Admissions with Scientific Models'
        self.make_pdf()
        caption='Figure 1. University Admissions with Scientific Models is the task.'
        with fitz.open(self.source) as doc:
            page=doc[0]
            page.draw_rect(fitz.Rect(300,138,600,155),color=(0,0,0))
            page.insert_text((305,150),caption,fontsize=7)
            doc.save(self.source.with_name('institution-caption.pdf'))
        self.source=self.source.with_name('institution-caption.pdf')
        clean=self.apply(self.plan())
        self.assertNotIn('Alice Example',clean)
        self.assertIn(caption,clean)


    def make_repeated_uri_pdf(self, earlier, release, later=()):
        self.source=Path(self.temp.name)/'source.pdf'
        self.make_pdf()
        with fitz.open(self.source) as doc:
            for title,lines in [('Methods',earlier),('Code Availability',release),*later]:
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,65),title,fontsize=12)
                for i,text in enumerate(lines):
                    page.insert_text((72,150+i*25),text,fontsize=9)
                    for match in rules.URL.finditer(text):
                        uri=match.group().rstrip('.,;)')
                        for rect in page.search_for(uri):
                            page.insert_link({'kind':fitz.LINK_URI,'from':rect,'uri':uri})
            doc.save(self.source.with_name('repeated-uri.pdf'))
        self.source=self.source.with_name('repeated-uri.pdf')

    def apply_page_texts(self,plan):
        with fitz.open(self.source) as doc:
            for item in plan['redactions']:
                doc[item['page']-1].add_redact_annot(item['rect'])
            for page in doc:
                page.apply_redactions(images=2,graphics=1,text=0)
            return [page.get_text() for page in doc]

    def test_later_owned_seed_covers_earlier_uri_without_touching_refs_or_output(self):
        uri='https://github.com/current-team/benchmark'
        self.make_repeated_uri_pdf([uri],['Our full code is available at '+uri+'.'],
            [('References',['A. Scholar. A resource at '+uri+'.']),
             ('Methods',['Output: '+uri,'Example: Our code is available at '+uri+'.'])])
        pages=self.apply_page_texts(self.plan())
        self.assertNotIn(uri,pages[1])
        self.assertNotIn(uri,pages[2])
        self.assertIn(uri,pages[3])
        self.assertEqual(pages[4].count(uri),2)

    def test_owned_repository_descendants_require_a_root_not_a_host_or_child(self):
        root='https://github.com/current-team/benchmark'
        child=root+'/blob/main/docs/tasks.md'
        sibling=root+'-external/blob/main/docs/tasks.md'
        self.make_repeated_uri_pdf([child,sibling],['Our code is available at '+root+'.'])
        pages=self.apply_page_texts(self.plan())
        self.assertNotIn(child,pages[1])
        self.assertIn(sibling,pages[1])
        self.make_repeated_uri_pdf([root],['Our code is available at '+child+'.'])
        self.assertIn(root,self.apply_page_texts(self.plan())[1])

    def test_named_method_release_is_bound_to_exact_own_introduction(self):
        self.paper['title']='BENCHX: An Evaluation Framework'
        uri='https://github.com/current-team/benchmark'
        for intro,remove in [('We introduce BENCHX for this task.',True),
                             ('We evaluate BENCHX from a prior paper.',False)]:
            self.source=Path(self.temp.name)/'source.pdf';self.make_pdf(extra=[(intro,250)])
            with fitz.open(self.source) as doc:
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,150),'1BENCHX comes with open-source code available at '+uri,fontsize=9)
                page.insert_link({'kind':fitz.LINK_URI,'from':page.search_for(uri)[0],'uri':uri})
                doc.save(self.source.with_name('named-release.pdf'))
            self.source=self.source.with_name('named-release.pdf')
            self.assertEqual(uri not in self.apply(self.plan()),remove)


    def test_contribution_names_preserve_roles_and_following_science(self):
        self.paper['authors']=['Alice Example','Bob Scholar']
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,95),'Alice Example and Bob Scholar')
            page.insert_text((72,150),'Abstract',fontsize=12)
            page.insert_text((72,180),'Our measured result is 57.5.')
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,80),'Author Contributions',fontsize=13)
            page.insert_textbox(fitz.Rect(72,95,540,160),
                'Alice Example implemented the model. Bob Scholar analyzed the results.',
                fontsize=10)
            page.insert_text((72,200),'Additional Information',fontsize=13)
            page.insert_text((72,230),'The authors declare no competing interests.')
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,100),'Figure 11: Alice Example implemented the model.',fontsize=10)
            page.insert_text((72,130),'The scientific stimulus and score 74.1 must remain.')
            doc.save(self.source)
        texts=self.apply_page_texts(self.plan())
        self.assertNotIn('Alice Example',texts[1])
        self.assertNotIn('Bob Scholar',texts[1])
        self.assertIn('implemented the model.',texts[1])
        self.assertIn('analyzed the results.',texts[1])
        self.assertIn('declare no competing interests',texts[1])
        self.assertIn('Alice Example implemented the model.',texts[2])
        self.assertIn('74.1',texts[2])



    def test_contribution_scientific_apposition_and_generated_statement_remain(self):
        cases=[(['Yueqi Xie','Yu Xie'],
                'Validation: Y.X.; here Y and X are matrices.'),
               (['Yueqi Xie','Yu Xie'],
                'Methodology: Y.X., the covariance matrix, is estimated from data.'),
               (['Alex Rivera'],
                'The model generated the following statement. Alex Rivera implemented the model.')]
        for names,text in cases:
            self.paper['authors']=names
            self.source=Path(self.temp.name)/'source.pdf'
            with fitz.open() as doc:
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,60),self.paper['title'],fontsize=16)
                page.insert_text((72,95),' and '.join(names))
                page.insert_text((72,150),'Abstract',fontsize=12)
                page.insert_text((72,180),'A scientific evaluation.')
                page=doc.new_page(width=612,height=792)
                page.insert_text((72,80),'Author Contributions',fontsize=13)
                page.insert_textbox(fitz.Rect(72,95,540,165),text,fontsize=10)
                page.insert_text((72,210),'References',fontsize=13)
                doc.save(self.source)
            clean=self.apply_page_texts(self.plan())[1]
            self.assertEqual(' '.join(clean.split()).split('References')[0].strip(),
                             'Author Contributions '+text)



class ReleaseAndFundingRegressions(unittest.TestCase):
 @staticmethod
 def line(text,block=1,y=100):
  return {'text':text,'block':block,'rect':[50,y,550,y+10],'size':10,'styled_heading':False}
 def release(self,text,others=(),known=False):
  rows=[*others,self.line(text+' https://github.com/current/project',block=99)]
  return rules._release_context(rows,len(rows)-1,len(text)+1,known)

 def test_owned_release_variants(self):
  cases=[
   '1The code is available at',
   '2The code is available at',
   'The full code is available online at',
   'The code of all the experiments and the running instructions can be found in',
   'Our code, data, and checkpoints are available at',
   'All code is open-sourced at',
   'Our dataset is publicly available at',
   'Our implementation and compression evaluations can be found at',
   'Code and experimental logs are available in:',
   'A sample implementation of SAMBA is publicly available in',
   'The corresponding benchmark code, configuration files, and evaluation summaries are publicly available at',
   'The project page is available at:',
   'Project homepage and resources are available at',
   'Additional visual results can be found on our anonymous webpage:',
   'This can be observed in video comparisons on our anonymous webpage:',
   'We have open-sourced our benchmark and evaluation results in',
   'We release our datasets and work on:',
   'We provide the complete code base with datasets and configuration files to reproduce the experiments in',
   'All data, annotations, and code are available at',
   'Full source code along with used datasets is available on our GitHub page. Link:',
  ]
  for text in cases:
   with self.subTest(text=text):self.assertTrue(self.release(text),text)
 def test_dependency_and_example_contexts_are_not_ownership(self):
  cases=[
   'Our code uses the third-party implementation, whose code is available at',
   'Our code uses baseline code available at',
   'We compare with X, code available at',
   'We compare with X. Code is available at',
   'The code associated with the paper (Song et al., 2021) is available at',
   'Our previous paper introduced the method. Code is available at',
   'Example: Our code is available at',
   'The literal URL example: Our code is available at',
   'We evaluate the phrase “our code is available at',
  ]
  for text in cases:
   with self.subTest(text=text):self.assertFalse(self.release(text,known=True),text)
  self.assertFalse(self.release('The dataset is publicly available at'))
 def test_song_numbered_dependency_stays_unchanged(self):
  caller=self.line('We train the network using the code2 associated with the paper (Song et al., 2021b).',block=2)
  self.assertFalse(self.release('2Code available at',others=[caller]))
  self.assertFalse(self.release('2The code is available at',others=[caller]))
 def test_explicit_own_numbered_callout(self):
  caller=self.line('We release our code3 for the proposed method.',block=2)
  self.assertTrue(self.release('3Code available at',others=[caller]))
 def test_unanchored_numbered_link_stays_unresolved(self):
  self.assertFalse(self.release('2Code available at'))
 def test_acknowledgment_body_is_not_a_short_heading(self):
  previous=self.line('Acknowledgements',y=50);previous['styled_heading']=True
  for text in ['The work is supported by ETH research grant and Swiss',
               'The MIT Geometric Data Processing Group acknowledges']:
   self.assertFalse(rules._possible_ack_boundary(self.line(text,y=80),previous))
  self.assertTrue(rules._possible_ack_boundary(self.line('Additional Experiments',y=80),previous))
 def test_inline_acknowledgment_and_quotes(self):
  self.assertTrue(rules._ack_heading('Acknowledgements The authors thank Viktoria Schuster'))
  self.assertFalse(rules._ack_heading('Example: Acknowledgements The authors thank Alice'))
  self.assertFalse(rules._ack_heading('“Acknowledgements The authors thank Alice”'))

if __name__ == '__main__':
    unittest.main()

class SupportedCreditRegressions(unittest.TestCase):
    def test_metadata_authors_acknowledge_financial_support(self):
        value='Alice Doe and Bob Roe acknowledge support by the Science Fund, Project F65.'
        self.assertTrue(rules._supported_author_credit(value,['alice doe','bob roe']))
        self.assertFalse(rules._supported_author_credit(value,['someone else']))

    def test_own_project_awards_and_formal_numbers(self):
        for value in ['This project is supported by research awards from Acme Research.',
                      'Our work was partially supported by ABC under No. 62402418.',
                      'This work was supported in part by the Research Council under number 2024Z115.',
                      'Support for this project was provided by the Research Foundation.']:
            with self.subTest(value=value):
                self.assertTrue(rules._supported_author_credit(value,[]))

    def test_scientific_supported_claims_and_literal_credit_stay_unselected(self):
        values=['This work is supported by experiments in Figure 2.',
                'Our study is supported by a theorem under No. 62402418.',
                'This work is supported by MODEL under No. 62402418; the model is evaluated below.',
                'Support for this hypothesis is provided by Figure 2.',
                'Example: This work was partly supported by ABC under No. 62402418.',
                'The model generated: This work was partly supported by ABC under No. 62402418.',
                '"This work was partly supported by ABC under No. 62402418."',
                '[1] Alice Doe. This work is supported by research awards from ABC.',
                'Participants were compensated under ABC Award 12345.']
        for value in values:
            with self.subTest(value=value):
                self.assertFalse(rules._supported_author_credit(value,[]))


"""Portable same-page ACK bridge tests; exact source-shaped positive rows.

Run inside the existing rules test environment. No PDF/Slurm work is performed.
Metadata names are deliberately unrelated to thanked contributors.
"""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from utility.anonymize import anonymize_rules as rules

ACK_BRIDGE_FIXTURE_SHA256 = '9d54f2d845b64a4bac54b769997b3b80203452ca867ac9626d9c41840d270bac'
ACK_BRIDGE_SOURCE_CASES = {
    'named_list': {
        "source_sha256": '55593cba47148fb702a828d910b83a71c52fcc4e3e489a48509ff1dcdbdbfd0e',
        "credit_rows": [
            {'text': 'Acknowledgement', 'rect': [58.5, 649.754150390625, 152.6830596923828, 661.7093505859375], 'size': 11.9552001953125, 'block': 10, 'styled_heading': True},
            {'text': 'This work was granted access to the HPC resources of IDRIS', 'rect': [58.191001892089844, 670.37939453125, 294.7471923828125, 680.2418823242188], 'size': 9.862470626831055, 'block': 11, 'styled_heading': False},
            {'text': 'under the allocations AD011014719 and AD011014286R1', 'rect': [58.5, 682.2245483398438, 295.4922180175781, 692.23193359375], 'size': 10.007330894470215, 'block': 11, 'styled_heading': False},
            {'text': 'made by GENCI. We thank Jordi Inglada, Antoine Labatie,', 'rect': [58.5, 694.194580078125, 295.9945068359375, 704.1820678710938], 'size': 9.987475395202637, 'block': 11, 'styled_heading': False},
            {'text': 'Dimitris Samaras, Yohann Perron, Vincent Lepetit for inspir-', 'rect': [317.25, 74.4834213256836, 555.1536865234375, 84.34589385986328], 'size': 9.862470626831055, 'block': 12, 'styled_heading': False},
            {'text': 'ing discussions and valuable feedback.', 'rect': [317.25, 86.36250305175781, 471.6702880859375, 96.32510375976562], 'size': 9.962599754333496, 'block': 12, 'styled_heading': False},
        ],
        "end_heading": {'text': 'References', 'rect': [317.25, 108.65215301513672, 372.7938537597656, 120.60735321044922], 'size': 11.9552001953125, 'block': 13, 'styled_heading': True},
    },
    'hyphenated_credit': {
        "source_sha256": '227b4152585037064646400df3e6b4e095deaf0172f710b79823aaf9e045b6b2',
        "credit_rows": [
            {'text': 'Acknowledgement', 'rect': [55.439998626708984, 486.3961486816406, 149.62306213378906, 498.3513488769531], 'size': 11.9552001953125, 'block': 5, 'styled_heading': True},
            {'text': 'The authors acknowledge the support by the U.S. Army Re-', 'rect': [55.13100051879883, 507.44256591796875, 291.0922546386719, 517.3351440429688], 'size': 9.89261531829834, 'block': 6, 'styled_heading': False},
            {'text': 'search Office (ARO) under Grant No. W911NF-23-1-0111,', 'rect': [55.439998626708984, 519.3786010742188, 290.682373046875, 529.2962646484375], 'size': 9.9176664352417, 'block': 6, 'styled_heading': False},
            {'text': 'the National Science Foundation (NSF) under the Career', 'rect': [55.439998626708984, 531.2242431640625, 289.6098327636719, 541.2860107421875], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'Award CPS-1453860, MCB-1936775, CNS-1932620 and', 'rect': [55.082000732421875, 543.1792602539062, 289.4449157714844, 553.2410278320312], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'the NSF award No. 2243104 under the Center for Complex', 'rect': [55.439998626708984, 555.2511596679688, 289.6854248046875, 565.1588134765625], 'size': 9.90765380859375, 'block': 6, 'styled_heading': False},
            {'text': 'Particle Systems (COMPASS), the Defense Advanced Re-', 'rect': [55.439998626708984, 567.1079711914062, 291.0909729003906, 577.14501953125], 'size': 10.037040710449219, 'block': 6, 'styled_heading': False},
            {'text': 'search Projects Agency (DARPA) Young Faculty Award and', 'rect': [55.439998626708984, 579.1964111328125, 289.4382019042969, 589.0588989257812], 'size': 9.862470626831055, 'block': 6, 'styled_heading': False},
            {'text': 'DARPA Director Fellowship Award under Grant Number', 'rect': [55.439998626708984, 591.000244140625, 289.6097412109375, 601.06201171875], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'N66001-17-1-4044, Intel faculty awards and a Northrop', 'rect': [55.439998626708984, 602.9552612304688, 289.43701171875, 613.0170288085938], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'Grumman grant. P.B. is also grateful to National Institute of', 'rect': [55.439998626708984, 615.0614013671875, 289.43817138671875, 624.9238891601562], 'size': 9.862470626831055, 'block': 6, 'styled_heading': False},
            {'text': 'Health (NIH) for the grants R01 AG 079957 “Interpretable', 'rect': [55.439998626708984, 626.95556640625, 289.43829345703125, 636.8982543945312], 'size': 9.942654609680176, 'block': 6, 'styled_heading': False},
            {'text': 'machine learning to synergize brain age estimation and neu-', 'rect': [55.439998626708984, 638.9713745117188, 291.0980529785156, 648.8338623046875], 'size': 9.862470626831055, 'block': 6, 'styled_heading': False},
            {'text': 'roimaging genetics” and RF1 AG 082201 “Neurovascular', 'rect': [55.439998626708984, 650.7874755859375, 289.6099548339844, 660.8344116210938], 'size': 10.046924591064453, 'block': 6, 'styled_heading': False},
            {'text': 'calcification and ADRD in two nonindustrial Native Ameri-', 'rect': [55.439998626708984, 662.8595581054688, 291.0967712402344, 672.7521362304688], 'size': 9.89261531829834, 'block': 6, 'styled_heading': False},
            {'text': 'can populations”. It was a wonderful experience designing', 'rect': [55.439998626708984, 674.7576904296875, 289.4361267089844, 684.7252807617188], 'size': 9.96757984161377, 'block': 6, 'styled_heading': False},
            {'text': 'and writing the grant application entitled “Neurovascular', 'rect': [55.439998626708984, 686.6412353515625, 289.6097717285156, 696.7030029296875], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'calcification and ADRD in two nonindustrial Native Ameri-', 'rect': [55.439998626708984, 698.7245483398438, 291.0967712402344, 708.6171264648438], 'size': 9.89261531829834, 'block': 6, 'styled_heading': False},
            {'text': 'can populations” and awarded under RF1 AG 082201. The', 'rect': [307.44000244140625, 69.48260498046875, 541.4380493164062, 79.42526245117188], 'size': 9.942654609680176, 'block': 7, 'styled_heading': False},
            {'text': 'views, opinions, and/or findings in this article are those of', 'rect': [307.1910095214844, 81.35952758789062, 541.4420166015625, 91.40644836425781], 'size': 10.046924591064453, 'block': 7, 'styled_heading': False},
            {'text': 'the authors and should not be interpreted as official views or', 'rect': [307.44000244140625, 93.45442962646484, 541.6138916015625, 103.31690216064453], 'size': 9.862470626831055, 'block': 7, 'styled_heading': False},
            {'text': 'policies of the Department of Defense, the National Institute', 'rect': [307.44000244140625, 105.40938568115234, 541.438232421875, 115.27185821533203], 'size': 9.862470626831055, 'block': 7, 'styled_heading': False},
            {'text': 'of Health or the National Science Foundation.', 'rect': [307.44000244140625, 117.28846740722656, 490.7418212890625, 127.25106811523438], 'size': 9.962599754333496, 'block': 7, 'styled_heading': False},
        ],
        "end_heading": {'text': 'References', 'rect': [307.44000244140625, 144.233154296875, 362.9838562011719, 156.1883544921875], 'size': 11.9552001953125, 'block': 8, 'styled_heading': True},
    },
    'unfinished_lowercase': {
        "source_sha256": '171f4e85be8afbbe2a4caceee4865fe74394e58551d873ca2a705b7e31cacbc4',
        "credit_rows": [
            {'text': 'Acknowledgments', 'rect': [55.439998626708984, 614.7811889648438, 148.9655303955078, 626.7363891601562], 'size': 11.9552001953125, 'block': 20, 'styled_heading': True},
            {'text': 'We acknowledge funding from the National Institutes of', 'rect': [54.97200012207031, 635.6992797851562, 289.43646240234375, 645.7610473632812], 'size': 10.061732292175293, 'block': 21, 'styled_heading': False},
            {'text': 'Health under 5R01-DA054994, the National Science Foun-', 'rect': [55.439998626708984, 647.74462890625, 291.0954284667969, 657.6873168945312], 'size': 9.942654609680176, 'block': 21, 'styled_heading': False},
            {'text': 'dation under award NSF 2147061, and through the Depart-', 'rect': [55.439998626708984, 659.6768798828125, 291.09747314453125, 669.6494750976562], 'size': 9.972557067871094, 'block': 21, 'styled_heading': False},
            {'text': 'ment of Energy under grant DE-SC0023194. Additionally,', 'rect': [55.439998626708984, 671.6168212890625, 290.6852111816406, 681.6093139648438], 'size': 9.992443084716797, 'block': 21, 'styled_heading': False},
            {'text': 'this material is based upon work supported by the National', 'rect': [55.439998626708984, 683.609619140625, 289.4383544921875, 693.5523071289062], 'size': 9.942654609680176, 'block': 21, 'styled_heading': False},
            {'text': 'Science Foundation Graduate Research Fellowship under', 'rect': [55.439998626708984, 695.4752807617188, 289.6097717285156, 705.5370483398438], 'size': 10.061732292175293, 'block': 21, 'styled_heading': False},
            {'text': 'Grant No. DGE 2139754. We thank Xenia Konti for her', 'rect': [55.439998626708984, 707.4302368164062, 289.60986328125, 717.4920043945312], 'size': 10.061732292175293, 'block': 21, 'styled_heading': False},
            {'text': 'helpful conversations in developing Case Study 3.', 'rect': [307.44000244140625, 69.46748352050781, 506.5624084472656, 79.43008422851562], 'size': 9.962599754333496, 'block': 22, 'styled_heading': False},
        ],
        "end_heading": {'text': 'Impact Statement', 'rect': [307.44000244140625, 96.41216278076172, 398.7418518066406, 108.36736297607422], 'size': 11.9552001953125, 'block': 23, 'styled_heading': True},
    },
    'capitalized_funding_continuation': {
        "source_sha256": '7d2a458c2172941aaaab81a0b8f50a03975d008d0ed905ca404dd3b3efc5a7d3',
        "credit_rows": [
            {'text': 'Acknowledgement', 'rect': [55.439998626708984, 640.4901733398438, 149.62306213378906, 652.4453735351562], 'size': 11.9552001953125, 'block': 5, 'styled_heading': True},
            {'text': 'This research is supported by Guangdong Basic and Applied', 'rect': [55.13100051879883, 661.5593872070312, 289.44158935546875, 671.421875], 'size': 9.862470626831055, 'block': 6, 'styled_heading': False},
            {'text': 'Basic Research Foundation (Grant No. 2026A1515011367)', 'rect': [55.439998626708984, 673.4915771484375, 290.1045837402344, 683.3841552734375], 'size': 9.89261531829834, 'block': 6, 'styled_heading': False},
            {'text': 'and the SUSTech-NUS Joint Research Program.', 'rect': [55.439998626708984, 685.3182373046875, 261.98980712890625, 695.3800048828125], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'This', 'rect': [271.36920166015625, 685.3182373046875, 289.4369812011719, 695.3800048828125], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'project is also supported by the Jiangsu Provincial Key', 'rect': [55.439998626708984, 697.2732543945312, 289.79266357421875, 707.3350219726562], 'size': 10.061732292175293, 'block': 6, 'styled_heading': False},
            {'text': 'Discipline Construction Project (Statistics) and open', 'rect': [307.44000244140625, 69.39230346679688, 541.43701171875, 79.45403289794922], 'size': 10.061732292175293, 'block': 7, 'styled_heading': False},
            {'text': 'project of Joint Lab for Statistics and Finance (Grant No.', 'rect': [307.44000244140625, 81.34829711914062, 543.1848754882812, 91.41002655029297], 'size': 10.061732292175293, 'block': 7, 'styled_heading': False},
            {'text': '2025JLSF101). We gratefully acknowledge the support of', 'rect': [307.44000244140625, 93.33705139160156, 541.4371948242188, 103.35429382324219], 'size': 10.017244338989258, 'block': 7, 'styled_heading': False},
            {'text': 'the Center for Computational Science and Engineering at', 'rect': [307.44000244140625, 105.25827026367188, 541.4371948242188, 115.31999969482422], 'size': 10.061732292175293, 'block': 7, 'styled_heading': False},
            {'text': 'the Southern University of Science and Technology for our', 'rect': [307.44000244140625, 117.30738067626953, 541.60986328125, 127.24504089355469], 'size': 9.937662124633789, 'block': 7, 'styled_heading': False},
            {'text': 'research.', 'rect': [307.44000244140625, 129.2434844970703, 343.1160888671875, 139.20608520507812], 'size': 9.962599754333496, 'block': 7, 'styled_heading': False},
        ],
        "end_heading": {'text': 'Impact Statement', 'rect': [307.44000244140625, 156.18817138671875, 398.7418518066406, 168.14337158203125], 'size': 11.9552001953125, 'block': 8, 'styled_heading': True},
    },
    'icon_credit_hyphenation': {
        "source_sha256": '81186ccb8efaffc66932dd32c66ec4fb70abc42dd9ed315fd295269f3cf8f3c6',
        "credit_rows": [
            {'text': 'Acknowledgements', 'rect': [70.86599731445312, 715.18017578125, 169.6996307373047, 727.1353759765625], 'size': 11.9552001953125, 'block': 5, 'styled_heading': True},
            {'text': 'The authors thank Peter Hase for useful pointers', 'rect': [70.52799987792969, 737.38037109375, 289.1346130371094, 748.3980102539062], 'size': 11.017650604248047, 'block': 6, 'styled_heading': False},
            {'text': 'in knowledge editing literature, Rakesh R Menon', 'rect': [70.86599731445312, 750.9457397460938, 289.13623046875, 761.9417724609375], 'size': 10.996026039123535, 'block': 6, 'styled_heading': False},
            {'text': 'for feedback on the paper’s diagrams and All Digi-', 'rect': [70.86599731445312, 764.6062622070312, 290.9420166015625, 775.4552001953125], 'size': 10.848934173583984, 'block': 6, 'styled_heading': False},
            {'text': 'tal, Valery Zanimanski, BomSymbols and Twemoji', 'rect': [306.1419982910156, 73.63481903076172, 524.40771484375, 84.43428039550781], 'size': 10.799457550048828, 'block': 7, 'styled_heading': False},
            {'text': 'team for providing various icons used in Figures', 'rect': [306.1419982910156, 87.01835632324219, 524.4146118164062, 98.03600311279297], 'size': 11.017650604248047, 'block': 7, 'styled_heading': False},
            {'text': '1 and 2. This work was supported in part by NSF', 'rect': [305.3240051269531, 100.56834411621094, 524.7203979492188, 111.58599090576172], 'size': 11.017650604248047, 'block': 7, 'styled_heading': False},
            {'text': 'grant DRL2112635.', 'rect': [306.1419982910156, 114.19967651367188, 394.01483154296875, 125.10877990722656], 'size': 10.909099578857422, 'block': 7, 'styled_heading': False},
        ],
        "end_heading": {'text': 'References', 'rect': [306.1419982910156, 150.65118408203125, 361.68585205078125, 162.60638427734375], 'size': 11.9552001953125, 'block': 8, 'styled_heading': True},
    },
}

class SamePageAcknowledgmentBridgeTests(unittest.TestCase):
    bounds = SimpleNamespace(width=612.0, height=792.0)
    names = ["mira author", "noah researcher"]

    @staticmethod
    def row(text, *, right=False, y=660.0, block=1, heading=False):
        x = 307.44 if right else 55.44
        return {"text": text, "rect": [x, y, x + 234.0, y + (12.0 if heading else 10.0)],
                "size": 12.0 if heading else 10.0, "block": block,
                "styled_heading": heading}

    def bridge(self, rows):
        return rules._cross_page_ack(
            [rows], 1, rows[0], self.bounds,
            kind="acknowledgment", names=list(self.names))

    def assert_source_case(self, name):
        data = deepcopy(ACK_BRIDGE_SOURCE_CASES[name])
        credits, end = data["credit_rows"], data["end_heading"]
        science = self.row(
            "Scientific text, model-v2, reference year 2025 and 74.1% accuracy remain.",
            right=True, y=end["rect"][3] + 10, block=end["block"] + 1)
        result = self.bridge(credits + [end, science])
        self.assertEqual(result, [(1, row) for row in credits])
        self.assertNotIn((1, end), result)
        self.assertNotIn((1, science), result)

    def test_named_list_continues_into_next_column(self):
        self.assert_source_case("named_list")

    def test_hyphenated_credit_and_disclaimer_stop_at_references(self):
        self.assert_source_case("hyphenated_credit")

    def test_unfinished_lowercase_thanks_stop_at_impact(self):
        self.assert_source_case("unfinished_lowercase")

    def test_capitalized_funding_continuation_stops_at_impact(self):
        self.assert_source_case("capitalized_funding_continuation")

    def test_hyphenated_icon_credit_remains_bounded(self):
        self.assert_source_case("icon_credit_hyphenation")

    def synthetic_rows(self, left, right, *, end="References"):
        rows = [self.row("Acknowledgments", y=630, block=0, heading=True)]
        rows.extend(self.row(text, y=654 + 12*i, block=1) for i, text in enumerate(left))
        rows.extend(self.row(text, right=True, y=70 + 12*i, block=2)
                    for i, text in enumerate(right))
        rows.append(self.row(end, right=True, y=85 + 12*len(right), block=3, heading=True))
        return rows

    def test_unrelated_lowercase_science_is_not_a_credit_continuation(self):
        rows = self.synthetic_rows(
            ["We thank Ada Rivera for her"],
            ["we evaluate 32 layers and obtain 74.1% accuracy on the held-out test set."])
        self.assertEqual(self.bridge(rows), [])

    def test_arbitrary_lowercase_column_is_not_a_thanks_continuation(self):
        rows = self.synthetic_rows(
            ["We thank Ada Rivera for her"],
            ["a model is trained on 32 layers and achieves 74.1% accuracy."])
        self.assertEqual(self.bridge(rows), [])

    def test_literal_prompt_with_grant_wording_is_preserved(self):
        rows = self.synthetic_rows(
            ["Example prompt: ‘The authors acknowledge support under grant No."],
            ["123456 project funding is provided.’ This is a quoted study stimulus."])
        self.assertEqual(self.bridge(rows), [])

    def test_scientific_support_is_not_financial_support(self):
        rows = self.synthetic_rows(
            ["Our hypothesis is supported by the convergence theorem and"],
            ["the experiment in Figure 2, which proves the accuracy bound."])
        self.assertEqual(self.bridge(rows), [])

    def test_generated_figure_caption_with_thanks_is_preserved(self):
        rows = self.synthetic_rows(
            ["Figure 3: A generated caption says ‘We thank Ada Rivera,"],
            ["for feedback.’ The caption is a benchmark stimulus."])
        self.assertEqual(self.bridge(rows), [])

    def test_grant_number_dataset_is_preserved(self):
        rows = self.synthetic_rows(
            ["Our benchmark includes a literal field named grant"],
            ["123456 project IDs form the classification dataset."])
        self.assertEqual(self.bridge(rows), [])

    def test_new_section_at_top_of_next_column_is_not_consumed(self):
        rows = self.synthetic_rows(
            ["We thank Ada Rivera for her"],
            ["2 Methods", "We estimate the posterior using Equation 7."])
        rows[2]["styled_heading"] = True
        rows[2]["size"] = 12.0
        self.assertEqual(self.bridge(rows), [])

    def test_missing_end_heading_remains_unresolved(self):
        rows = self.synthetic_rows(
            ["We thank Ada Rivera for her"],
            ["helpful comments and proofreading."])
        self.assertEqual(self.bridge(rows[:-1]), [])

    def test_closed_left_sentence_does_not_absorb_other_column(self):
        rows = self.synthetic_rows(
            ["We thank Ada Rivera for feedback."],
            ["the model is evaluated on 32 tasks using four random seeds."])
        self.assertEqual(self.bridge(rows), [])



"""Append this class to the existing test module, which already imports rules."""
import unittest


class SourceContactHeaderProposalGuards(unittest.TestCase):
    @staticmethod
    def source_line(text, y=700, x=60, width=280, size=9, block=1, styled=False):
        return {"text": text, "rect": [x, y, x + width, y + size], "size": size,
                "block": block, "styled_heading": styled}

    def selected_contacts(self, lines, markers=("*", "1"), names=("alice doe",)):
        slices = rules._own_contact_slices(lines, 600, 800, names, markers)
        return [line["text"][start:end] for line, start, end, *_ in slices]

    def test_contact_proof_does_not_cross_footnotes_or_scientific_sentences(self):
        own = "* Correspondence to Alice Doe <alice@uni.edu>."
        cases = [
            [self.source_line(own), self.source_line(
                "2 Access requests for the corpus go to curator@archive.org.", y=710)],
            [self.source_line(own + " The corpus contains mail from tim.belden@enron.com.")],
            [self.source_line(own + " The analysis counts mail from tim.belden@enron.com.")],
            [self.source_line(own + " 2 Return address: bob@outside.org.")],
            [self.source_line(own + " For permission, email: editor@publisher.org.")],
            [self.source_line(own + " A service is reachable; contact: help@vendor.org.")],
            [self.source_line("* Alice Doe is with Sample University. Email: alice@uni.edu. "
                              "The analysis counts mail from tim.belden@enron.com.")],
        ]
        for lines in cases:
            with self.subTest(lines=lines):
                self.assertEqual(self.selected_contacts(lines), ["alice@uni.edu"])
        self.assertEqual(self.selected_contacts([self.source_line(
            "1 The Enron corpus contains messages from tim.belden@enron.com.")]), [])

    def test_contact_examples_quotes_and_generated_prose_are_preserved(self):
        own = "Correspondence to Alice Doe <alice@uni.edu>."
        for text in ("* Example input: " + own, "* Generated response: " + own,
                     '* "' + own + '"', "* ‘" + own + "’", "[1] " + own):
            with self.subTest(text=text):
                self.assertEqual(self.selected_contacts([self.source_line(text)]), [])

    def test_contact_domain_is_not_used_as_context(self):
        self.assertEqual(self.selected_contacts([self.source_line(
            "* Correspondence to Alice Doe <alice@archive.org>.")]), ["alice@archive.org"])

    def test_contacts_keep_exact_group_and_angle_localpart_fragments(self):
        lines = [self.source_line("* Corresponding author. Emails: {alice, bob,"),
                 self.source_line("carol}@uni.edu, alice.doe@uni.edu.", y=710)]
        self.assertEqual(self.selected_contacts(lines),
                         ["{alice, bob,", "carol}@uni.edu", "alice.doe@uni.edu"])
        self.assertEqual(self.selected_contacts([self.source_line(
            "* Correspondence to Alice Doe <alice doe@uni.edu>.")]), ["alice doe@uni.edu"])

    def test_open_address_cross_block_continuation_requires_unique_same_column(self):
        head = self.source_line("* Correspondence to Alice Doe <alice-")
        tail = self.source_line("doe@uni.edu>.", y=710, block=2)
        self.assertEqual(self.selected_contacts([head, tail]), ["alice-", "doe@uni.edu"])
        wrong = dict(tail, rect=[350, 710, 550, 719])
        self.assertEqual(self.selected_contacts([head, wrong]), [])
        self.assertEqual(self.selected_contacts([head, tail, dict(tail, block=3)]), [])

    def test_plain_author_note_and_scientific_suffix(self):
        self.assertEqual(self.selected_contacts([self.source_line("*alice@uni.edu. MIT.")]),
                         ["alice@uni.edu"])
        text = "* Correspondence to Alice Doe <alice@uni.edu>. Our method improves F1 by 5%."
        self.assertEqual(self.selected_contacts([self.source_line(text)]), ["alice@uni.edu"])
        self.assertEqual(self.selected_contacts([self.source_line("2other@outside.org")], markers=("*",)), [])
        self.assertEqual(self.selected_contacts([self.source_line("*alice@uni.edu", y=200)]), [])
        self.assertEqual(self.selected_contacts([self.source_line("*alice@uni.edu", size=14)]), [])

    def header(self, author="Alice Doe1,2§"):
        return [self.source_line(author, y=150, width=420, size=11, block=0)]

    def institution(self, line, header, known=(), graphic=260):
        return rules._marked_header_institution(line, header, {0}, 140, 260, graphic, known)

    def test_marked_company_and_french_institution_are_bounded(self):
        header = self.header()
        cisco = self.source_line("§Cisco Research", y=180, size=10)
        self.assertTrue(self.institution(cisco, header + [cisco]))
        french = self.source_line("1 LASTIG, Univ Gustave Eiffel, France", y=180, size=10)
        acronym = self.source_line("2 IGN, France", y=180, x=360, width=90, size=10)
        self.assertTrue(self.institution(french, header + [french, acronym]))
        self.assertTrue(self.institution(acronym, header + [french, acronym], {french["text"]}))
        self.assertFalse(self.institution(acronym, header + [french, acronym]))
        self.assertFalse(self.institution(cisco, header + [cisco], graphic=175))

    def test_styled_numbered_research_heading_and_unknown_labels_stay(self):
        header = self.header("Alice Doe1")
        heading = self.source_line("1 Research Questions", y=180, size=12, styled=True)
        self.assertFalse(self.institution(heading, header + [heading]))
        for text in ("1 Model Research", "2 Cisco Research", "1 Ground Truth", "1 Example Research"):
            target = self.source_line(text, y=180, size=10)
            self.assertFalse(self.institution(target, header + [target]))

    def test_location_requires_exact_prior_affiliation_and_boundaries(self):
        header = self.header("Alice Doe1")
        affiliation = self.source_line("1 Sample University", y=180, size=10)
        location = self.source_line("Israel", y=200, x=150, width=40, size=10)
        rows = header + [affiliation, location]
        known = {affiliation["text"]}
        self.assertTrue(rules._affiliation_location(location, rows, {0}, 140, 260, 260, known))
        self.assertFalse(rules._affiliation_location(location, rows, {0}, 140, 260, 260, set()))
        self.assertFalse(rules._affiliation_location(location, rows, {0}, 140, 260, 195, known))
        outside = dict(location, rect=[450, 200, 490, 210])
        self.assertFalse(rules._affiliation_location(outside, rows, {0}, 140, 260, 260, known))
        barrier = self.source_line("Ground Truth", y=192, x=150, width=60, size=7)
        self.assertFalse(rules._affiliation_location(location, rows + [barrier], {0}, 140, 260, 260, known))

    def test_scientific_title_remains_outside_identity_region(self):
        header = self.header()
        title = self.source_line("Boosted Model Probability", y=110, size=14)
        self.assertFalse(self.institution(title, header + [title]))
        self.assertFalse(rules._affiliation_location(title, header + [title], {0}, 140, 260, 260, set()))


class HeaderCardContactRegressions(unittest.TestCase):
    @staticmethod
    def line(text, y, *, block=1, x=70, width=350, size=10):
        return {'text':text,'rect':[x,y,x+width,y+size],'size':size,'block':block,'styled_heading':False}

    def card(self, label='Aurora Research Labs', email='alice@aurora.edu'):
        return [self.line('Alice Doe',100,size=12,block=0),
                self.line(label,125),self.line(email,141)]

    def selected(self, rows, *, graphics=(), known=(), anchors=(0,), graphic_top=50, already=()):
        return rules._header_contact_slices(rows,set(anchors),80,180,set(known),graphic_top,
                                           graphics if graphics is not None else None,set(already))

    def test_complete_template_and_legacy_domains_are_contact_only(self):
        self.assertTrue(rules._contact_only('{firstname}.{lastname}@college.edu'))
        self.assertTrue(rules._contact_only('{alice,bob}@{campus,cs}.college.edu'))
        self.assertTrue(rules._contact_only('ORCID: 0000-0002-1825-0097'))
        self.assertFalse(rules._contact_only('Example input: {firstname}.{lastname}@college.edu'))
        self.assertFalse(rules._contact_only('alice@college.edu is a literal pattern.'))

    def test_graphic_above_proven_author_card_does_not_hide_email(self):
        rows=self.card()
        result=self.selected(rows,graphics=[{'rect':[70,40,400,65]}])
        self.assertEqual([(line['text'][start:end]) for line,start,end in result],['alice@aurora.edu'])
        self.assertFalse(any('Labs' in line['text'][start:end] for line,start,end in result))

    def test_card_graphic_conflicts_and_unknown_inventory_stay_unresolved(self):
        rows=self.card()
        self.assertEqual(self.selected(rows,graphics=[{'rect':[60,120,480,158]}]),[])
        self.assertEqual(self.selected(rows,graphics=None),[])
        self.assertEqual(self.selected(rows,anchors=()),[])

    def test_company_requires_distinct_domain_component(self):
        self.assertEqual(self.selected(self.card(label='Other Research Labs')),[])
        self.assertEqual(self.selected(self.card(label='Edu Research Labs')),[])

    def test_intervening_scientific_example_is_preserved(self):
        rows=self.card()
        rows.append(self.line('Example input:',137,size=8))
        self.assertEqual(self.selected(rows),[])

    def test_template_after_proved_affiliation_needs_no_new_mask_for_old_contacts(self):
        rows=self.card(label='Harbor University',email='{firstname}.{lastname}@harbor.edu')
        known={rows[1]['text']}
        self.assertTrue(self.selected(rows,known=known,graphic_top=180,graphics=None))
        self.assertEqual(self.selected(rows,known=known,graphic_top=180,graphics=None,
                                       already={rows[-1]['text']}),[])

    def test_email_bytes_are_not_scientific_context_words(self):
        rows=self.card(label='Harbor University',email='alice@model.edu')
        self.assertTrue(self.selected(rows,known={rows[1]['text']},graphic_top=180))



from datetime import date
from copy import deepcopy

def _pub_test_line(text, box, block, size=12):
    return {"text": text, "rect": box, "block": block, "size": size}

def _pub_test_header(value="March 4, 2026"):
    return [_pub_test_line("Scientific Paper Title", [120, 90, 480, 110], 0, 18),
            _pub_test_line("A. Researcher", [235, 140, 365, 152], 1),
            _pub_test_line("Example University", [215, 158, 385, 170], 2),
            _pub_test_line(value, [250, 194, 350, 206], 3),
            _pub_test_line("Abstract", [278, 242, 322, 253], 4, 10)]

def _pub_test_select(lines, **overrides):
    arguments = dict(title_indexes={0}, author_indexes={1},
                     header_identity_indexes={1, 2}, abstract_index=4,
                     page_size=(600, 800), graphics=[])
    arguments.update(overrides)
    return rules._publication_frontmatter_lines(lines, **arguments)

class PublicationCalendarTests(unittest.TestCase):
    def test_valid_calendar_forms(self):
        for value in ("February 29, 2024", "Feb. 29 2024", "2024-2-29"):
            with self.subTest(value=value):
                self.assertEqual(rules._publication_calendar_date(value), date(2024, 2, 29))
        self.assertEqual(rules._publication_calendar_date("Sept. 2, 2025"), date(2025, 9, 2))

    def test_invalid_or_embedded_dates(self):
        for value in ("February 29, 2025", "April 31, 2025", "2025-13-1",
                      "2025-1-0", "Septe 1, 2025", "May 2025",
                      "gpt-4o-2024-11-20", "Accessed 2026-04-10.",
                      "May 4, 2026 was the experiment date"):
            with self.subTest(value=value):
                self.assertIsNone(rules._publication_calendar_date(value))

class PublicationLayoutTests(unittest.TestCase):
    def test_isolated_header_date_returns_original_object(self):
        lines = _pub_test_header()
        self.assertEqual(_pub_test_select(lines), [lines[3]])
        self.assertIs(_pub_test_select(lines)[0], lines[3])

    def test_unknown_graphics_fails_closed(self):
        self.assertEqual(_pub_test_select(_pub_test_header(), graphics=None), [])

    def test_graphic_in_header_gap_is_not_ignored(self):
        self.assertEqual(_pub_test_select(_pub_test_header(), graphics=[[240, 182, 360, 215]]), [])
        self.assertEqual(_pub_test_select(_pub_test_header(), graphics=[[170, 214, 430, 234]]), [])

    def test_unrelated_graphic_outside_header_gap_is_allowed(self):
        self.assertEqual(len(_pub_test_select(_pub_test_header(), graphics=[[40, 350, 560, 600]])), 1)

    def test_same_row_table_label_in_separate_block_rejected(self):
        lines = _pub_test_header("April 15, 2025")
        lines.append(_pub_test_line("Released Date", [400, 194, 480, 206], 9))
        self.assertEqual(_pub_test_select(lines), [])

    def test_same_row_model_name_rejected_even_left_of_date(self):
        lines = _pub_test_header("April 15, 2025")
        lines.append(_pub_test_line("Doubao-1.5-Thinking-Pro", [80, 194, 240, 206], 9))
        self.assertEqual(_pub_test_select(lines), [])

    def test_only_source_confirmed_margin_identifier_may_be_ignored(self):
        lines = _pub_test_header()
        lines.append(_pub_test_line("arXiv:2502.13583v4", [10, 110, 30, 600], 9, 20))
        self.assertEqual(_pub_test_select(lines), [])
        self.assertEqual(_pub_test_select(lines, margin_identifier_indexes={5}), [lines[3]])

    def test_caption_and_table_blocks_rejected(self):
        for label in ("Table 1: Model release dates", "Figure 1: Timeline"):
            lines = _pub_test_header()
            lines.append(_pub_test_line(label, [160, 180, 420, 192], 3))
            self.assertEqual(_pub_test_select(lines), [])

    def test_example_prompt_and_reference_contexts_rejected(self):
        for label in ("Example:", "Literal:", "Prompt:", "References", "Bibliography"):
            lines = _pub_test_header()
            lines.append(_pub_test_line(label, [230, 179, 370, 190], 8))
            self.assertEqual(_pub_test_select(lines), [])

    def test_scientific_paragraph_between_header_and_abstract_rejected(self):
        lines = _pub_test_header()
        lines.append(_pub_test_line("Evaluation starts on this date.", [120, 220, 480, 231], 8))
        self.assertEqual(_pub_test_select(lines), [])

    def test_missing_known_authors_or_abstract_rejected(self):
        self.assertEqual(_pub_test_select(_pub_test_header(), author_indexes=set()), [])
        self.assertEqual(_pub_test_select(_pub_test_header(), abstract_index=None), [])

    def test_date_is_not_deleted_when_it_is_the_exact_title(self):
        lines = _pub_test_header()
        lines[0]["text"] = "March 4, 2026"
        lines[3]["text"] = "Scientific body text"
        self.assertEqual(_pub_test_select(lines), [])

    def test_neutral_source_marker_spellings_stay_out_of_new_goals(self):
        for value in ("Preprint.", "Pre-print.", "Under review.",
                      "JOURNAL OF LATEX CLASS FILES, VOL. 14, NO. 8, AUGUST 2021"):
            with self.subTest(value=value):
                self.assertEqual(_pub_test_select(_pub_test_header(value)), [])

    def test_named_venue_banner(self):
        lines = _pub_test_header("Scientific body text")
        banner = _pub_test_line("Published at Latent & Implicit Thinking Workshop @ ICLR 2026",
                      [108, 28, 390, 38], 8, 10)
        lines.append(banner)
        self.assertEqual(_pub_test_select(lines), [banner])

    def test_venue_body_literal_and_title_are_preserved(self):
        text = "Published at Latent & Implicit Thinking Workshop @ ICLR 2026"
        lines = _pub_test_header(text)
        self.assertEqual(_pub_test_select(lines), [])
        lines[0]["text"] = text
        lines[3]["text"] = "Scientific body text"
        self.assertEqual(_pub_test_select(lines), [])

    def test_venue_banner_requires_specific_year_and_named_venue(self):
        for value in ("Published at a workshop", "Published at a Workshop 2026", "Published at 2026",
                      "Published at 10 milliseconds in 2026"):
            lines = _pub_test_header("Scientific body text")
            lines.append(_pub_test_line(value, [108, 28, 390, 38], 8, 10))
            self.assertEqual(_pub_test_select(lines), [])

    def test_opposite_corner_brand_numeric_date(self):
        lines = _pub_test_header("Scientific body text")
        lines[2]["text"] = "Fudan NLP Group"
        brand = _pub_test_line("Fudan NLP Lab", [70, 44, 170, 59], 8, 12)
        stamp = _pub_test_line("2026-4-16", [475, 51, 530, 60], 9, 8)
        lines += [brand, stamp]
        self.assertEqual(_pub_test_select(lines, branding_indexes={5}), [stamp])
        # A simulated logo at the corroborating brand is not the date's graphic.
        self.assertEqual(_pub_test_select(lines, branding_indexes={5},
                                graphics=[[45, 44, 65, 60]]), [stamp])

    def test_unrelated_brand_and_other_same_row_text_rejected(self):
        lines = _pub_test_header("Scientific body text")
        lines += [_pub_test_line("Unrelated NLP Lab", [70, 44, 190, 59], 8, 12),
                  _pub_test_line("2026-4-16", [475, 51, 530, 60], 9, 8)]
        self.assertEqual(_pub_test_select(lines, branding_indexes={5}), [])
        lines[5]["text"] = "Example University"
        lines.append(_pub_test_line("Model Released Date", [260, 51, 400, 60], 10, 8))
        self.assertEqual(_pub_test_select(lines, branding_indexes={5}), [])


class LocatorOwnershipBoundaryTests(unittest.TestCase):
    def test_repository_repeat_preserves_host_owner_sibling_and_traversal(self):
        root='https://github.com/current-team/benchmark'
        self.assertTrue(rules._owned_uri_repeat(root+'/blob/main/docs/tasks.md',{root}))
        self.assertTrue(rules._owned_uri_repeat(root+'/tree/main/results',{root}))
        for uri in (root+'-other',root+'/issues/1',root+'/tree/main/../../other',
                    root+'/tree/main/%2e%2e/other',
                    'https://github.com.evil.example/current-team/benchmark/tree/main/docs',
                    'https://github.com/current-team/other/tree/main/docs'):
            self.assertFalse(rules._owned_uri_repeat(uri,{root}),uri)
        self.assertFalse(rules._owned_uri_repeat(root,{root+'/tree/main/docs'}))
        self.assertFalse(rules._owned_uri_repeat(root+'/tree/main/docs',{'https://github.com/current-team'}))

    def test_repeat_keeps_quoted_borrowed_and_generated_uri_context(self):
        uri='https://github.com/current-team/benchmark'
        for value in ('Our code uses this dependency','Code from Song et al.',
                      'Example URL:','Output:','The model generated this URL',
                      'We compare with this baseline'):
            self.assertFalse(rules._owned_uri_repeat(uri,{uri},value))
        self.assertFalse(rules._owned_uri_repeat(uri,{uri},in_references=True))

    def test_extra_seed_is_specific_to_each_uri_offset(self):
        owned='https://github.com/current-team/benchmark'
        borrowed='https://github.com/third-party/library'
        text='BENCHX comes with open-source code available at '+owned+' and uses '+borrowed
        line={'text':text,'block':0,'rect':[50,100,550,111],'size':10}
        intro=[{'text':'We introduce BENCHX for this task.'}]
        self.assertTrue(rules._additional_seed_proof([line],0,text.index(owned),
                        'BENCHX: An Evaluation Framework',intro))
        self.assertFalse(rules._additional_seed_proof([line],0,text.index(borrowed),
                         'BENCHX: An Evaluation Framework',intro))
        for prefix in ('Example: ', 'The model generated: '):
            self.assertIsNone(rules._own_named_method('BENCHX: An Evaluation Framework',
                              [{'text':prefix+'We introduce BENCHX.'}]))


class ContributionNameBoundaryTests(unittest.TestCase):
    def run_text(self, text, names=("Alex Rivera", "Mira Chen"),
                     heading="Author Contributions", byline=()):
            block = [{"text": line, "page": 1} for line in text.split("\n")]
            result = rules._contribution_name_ranges(
                block, [rules._normal(n) for n in names], heading=heading,
                source_byline=byline, rules_module=rules)
            return result

    def surfaces(self, result):
            return [m["surface"] for m in result["matches"]]

    def test_verified_three_token_alias_is_not_arbitrary_permutation(self):
            names = ["Pattisapu Nikhil Priyatam"]
            good = self.run_text("Nikhil Priyatam Pattisapu implemented the model.",
                                 names, byline=["Nikhil Priyatam Pattisapu"])
            self.assertEqual(self.surfaces(good), ["Nikhil Priyatam Pattisapu"])
            bad = self.run_text("Nikhil Pattisapu Priyatam implemented the model.",
                                names, byline=["Nikhil Pattisapu Priyatam"])
            self.assertFalse(bad["ranges"])

    def test_name_first_credit_fields(self):
            result = self.run_text(
                "Alex Rivera: Conceptualization, Methodology. Mira Chen: Software, Validation.",
                heading="CRediT")
            self.assertEqual(self.surfaces(result), ["Alex Rivera", "Mira Chen"])

    def test_role_first_credit_fields(self):
            result = self.run_text(
                "Conceptualization: Alex Rivera; Writing — original draft: Mira Chen.",
                heading="CRediT authorship contribution statement")
            self.assertEqual(self.surfaces(result), ["Alex Rivera", "Mira Chen"])

    def test_joint_subject_keeps_conjunction_and_roles(self):
            text = "Alex Rivera and Mira Chen analyzed the results."
            result = self.run_text(text)
            self.assertEqual(self.surfaces(result), ["Alex Rivera", "Mira Chen"])
            remaining = text
            for start, end in reversed(result["ranges"]):
                remaining = remaining[:start] + remaining[end:]
            self.assertEqual(remaining, " and  analyzed the results.")

    def test_passive_role_to_name(self):
            result = self.run_text("The research and writing of this paper were led by Alex Rivera.",
                                   heading="Acknowledgments")
            self.assertEqual(self.surfaces(result), ["Alex Rivera"])

    def test_ack_heading_alone_does_not_authorize_name_role(self):
            self.assertFalse(self.run_text("Alex Rivera implemented the model.",
                                           heading="Acknowledgments")["ranges"])

    def test_unicode_combining_accent_offsets(self):
            text = "Jose\u0301 García implemented the software."
            result = self.run_text(text, ["José García"])
            self.assertEqual(result["ranges"], [(0, len("Jose\u0301 García"))])
            self.assertEqual(self.surfaces(result), ["Jose\u0301 García"])

    def test_spacing_accent_offsets(self):
            text = "Leo Sa´nchez implemented the software."
            result = self.run_text(text, ["Léo Sánchez"])
            self.assertEqual(self.surfaces(result), ["Leo Sa´nchez"])

    def test_line_break_hyphenation_keeps_raw_offsets(self):
            text = "Alex Rive-\nra implemented the software."
            result = self.run_text(text, ["Alex Rivera"])
            self.assertEqual(self.surfaces(result), ["Alex Rive-\nra"])

    def test_initials_are_not_matched_in_methods(self):
            result = self.run_text("Y.X. conceived a matrix product.",
                                   ["Yueqi Xie", "Yu Xie"], heading="Methods")
            self.assertFalse(result["ranges"])

    def test_initials_inside_role_method_prose_are_not_subject_slots(self):
            result = self.run_text(
                "The matrix Y.X. implemented the transformation.",
                ["Yueqi Xie", "Yu Xie"])
            self.assertFalse(result["ranges"])

    def test_quoted_contribution_statement_is_preserved(self):
            for text in (
                "The example prompt is “Alex Rivera implemented the model.”",
                "“Alex Rivera implemented the model.”",
                "'Alex Rivera implemented the model.'",
            ):
                with self.subTest(text=text):
                    self.assertFalse(self.run_text(text)["ranges"])

    def test_figure_caption_stops_scope(self):
            text = "Alex Rivera implemented the model.\nFigure 11: Mira Chen analyzed the results."
            self.assertEqual(self.surfaces(self.run_text(text)), ["Alex Rivera"])

    def test_unclosed_quoted_prose_is_preserved_with_uncertainty(self):
            for text in ('"Quoted sentence. Alex Rivera implemented the model.',
                         '“Quoted sentence. Alex Rivera implemented the model.'):
                with self.subTest(text=text):
                    result = self.run_text(text)
                    self.assertFalse(result["ranges"])
                    self.assertTrue(result["uncertain"])

    def test_role_field_terminator_requires_a_real_field_boundary(self):
        for separator in (';', '.', ','):
            value='Validation: Y.X.'+separator+' here Y and X are matrices.'
            self.assertFalse(self.run_text(value,['Yueqi Xie','Yu Xie'])['ranges'])
        good=self.run_text('Validation: Y.X.; Writing: T.Q.',
                           ['Yueqi Xie','Tao Qian'])
        self.assertEqual(self.surfaces(good),['Y.X.','T.Q.'])

    def test_method_variable_after_role_label_is_not_a_name_slot(self):
            result = self.run_text("Methodology: Y.X. denotes the covariance matrix.",
                                   ["Yueqi Xie", "Yu Xie"])
            self.assertFalse(result["ranges"])

    def test_initials_scientific_apposition_after_role_label_is_preserved(self):
            result = self.run_text(
                "Methodology: Y.X., the covariance matrix, is estimated from data.",
                ["Yueqi Xie", "Yu Xie"])
            self.assertFalse(result["ranges"])

    def test_generated_model_statement_is_preserved(self):
            result = self.run_text(
                "The model generated the following statement. Alex Rivera implemented the model.",
                ["Alex Rivera"])
            self.assertFalse(result["ranges"])

    def test_joint_initials_role_field_remains_a_contributor_slot(self):
            result = self.run_text(
                "Methodology: Y.X., R.W.", ["Yueqi Xie", "Yu Xie", "Ryan Whalen"])
            self.assertEqual(self.surfaces(result), ["Y.X.", "R.W."])
            self.assertEqual(set(result["matches"][0]["metadata_candidates"]),
                             {"yueqi xie", "yu xie"})

    def test_apostrophe_name_is_not_a_quotation(self):
            result = self.run_text("Sean O’Neill implemented the software.", ["Sean O’Neill"])
            self.assertEqual(self.surfaces(result), ["Sean O’Neill"])

    def test_references_and_new_section_stop_scope(self):
            for boundary in ("References", "2 Methods", "Additional Information"):
                with self.subTest(boundary=boundary):
                    text = "Alex Rivera implemented the model.\n" + boundary + "\nMira Chen analyzed the results."
                    self.assertEqual(self.surfaces(self.run_text(text)), ["Alex Rivera"])

    def test_multi_page_input_fails_closed(self):
            block = [{"text": "Alex Rivera implemented the model.", "page": 24},
                     {"text": "Mira Chen analyzed the results.", "page": 25}]
            result = rules._contribution_name_ranges(
                block, ["Alex Rivera", "Mira Chen"],
                heading="Author Contributions", rules_module=rules)
            self.assertFalse(result["ranges"])
            self.assertTrue(result["uncertain"])

    def test_citation_after_name_is_preserved(self):
            for text in (
                "Alex Rivera (2025) implemented the cited method.",
                "Writing: Alex Rivera et al. 2025.",
            ):
                with self.subTest(text=text):
                    self.assertFalse(self.run_text(text)["ranges"])

    def test_no_name_count_fallback_without_roles(self):
            self.assertFalse(self.run_text("Alex Rivera, Mira Chen.")["ranges"])

    def test_surname_only_and_single_letter_metadata_are_excluded(self):
            result = self.run_text("Rivera implemented software. S contributed validation.",
                                   ["Alex Rivera", "S"])
            self.assertFalse(result["ranges"])
            self.assertTrue(result["uncertain"])

    def test_credit_topic_is_not_credit_authorship_fields(self):
            self.assertFalse(self.run_text("Alex Rivera implemented the model.",
                                           heading="Credit")["ranges"])
            self.assertFalse(self.run_text("Alex Rivera implemented the model.",
                                           heading="Credit Risk")["ranges"])


import json
_LAYOUT_DIAGNOSTIC_KEYS = {"source_ids", "source_families", "provenance_family", "selected_family",
        "observed_variant", "evidence", "ambiguities"}

def _layout_test_line(text, box, block, size=10):
    return dict(text=text, rect=box, block=block, size=size, styled_heading=False)

def _layout_test_base(source="acl2025"):
    paper = dict(title="Scientific Paper", authors=["Alice Smith", "Bob Jones"], source_ids=[source])
    first = [_layout_test_line("Scientific Paper", [150, 70, 450, 90], 0, 18),
             _layout_test_line("Alice Smith1 and Bob Jones2", [180, 120, 420, 132], 1, 12),
             _layout_test_line("North University, South Institute", [120, 145, 480, 157], 2, 11),
             _layout_test_line("Abstract", [275, 200, 325, 212], 3, 12)]
    body = [_layout_test_line("Ordinary scientific description of the method and results.",
                 [70, 100+i*15, 530, 111+i*15], i, 10) for i in range(12)]
    first += [_layout_test_line("Scientific discussion continues in the actual first-page body.",
                   [70, 230+i*15, 530, 241+i*15], 10+i, 10) for i in range(12)]
    return paper, [first, body], [(600, 800), (600, 800)], dict(title_indexes=[0], author_indexes=[1], body_boundary=200)

def _layout_test_run(paper, pages, sizes, evidence, **kwargs):
    return rules._select_layout_profile(paper, pages, sizes, first_page_evidence=evidence,
                                 **dict({"first_graphics": []}, **kwargs))

class SourceLayoutContractTests(unittest.TestCase):
    def test_six_source_configurations_preserve_year_ids(self):
            for source, family in [("acl2025", "acl_emnlp"), ("emnlp2025", "acl_emnlp"),
                                   ("cvpr2025", "cvpr_iccv"), ("iccv2025", "cvpr_iccv"),
                                   ("icml2026", "icml"), ("iclr2025", "iclr"),
                                   ("neurips2025", "neurips"), ("tmlr_2025plus", "tmlr")]:
                with self.subTest(source=source):
                    result = _layout_test_run(*_layout_test_base(source))
                    self.assertEqual(result["source_ids"], [source])
                    self.assertEqual(result["selected_family"], family)
                    self.assertEqual(result["observed_variant"], "centeredrows")

    def test_missing_source_can_report_geometry_but_selects_nothing(self):
            paper, pages, sizes, ev = _layout_test_base()
            paper.pop("source_ids")
            out = _layout_test_run(paper, pages, sizes, ev)
            self.assertEqual(out["observed_variant"], "centeredrows")
            self.assertIsNone(out["selected_family"])

    def test_source_label_alone_is_not_layout_evidence(self):
            paper, pages, sizes, _ = _layout_test_base("icml2026")
            out = rules._select_layout_profile(paper, pages, sizes, first_graphics=[])
            self.assertEqual(out["observed_variant"], "unresolved")
            self.assertIsNone(out["selected_family"])

    def test_unknown_graphics_does_not_confirm_layout(self):
            out = _layout_test_run(*_layout_test_base(), first_graphics=None)
            self.assertIn("unknown_first_page_graphic_inventory", out["ambiguities"])
            self.assertIsNone(out["selected_family"])

    def test_graphic_intersection_with_author_or_title_is_ambiguous(self):
            out = _layout_test_run(*_layout_test_base(), first_graphics=[{"rect": [150, 110, 450, 150]}])
            self.assertIsNone(out["selected_family"])
            self.assertIn("graphic_overlap_with_primary_title_or_byline", out["ambiguities"])

    def test_full_metadata_name_must_be_within_one_byline_row(self):
            paper, pages, sizes, ev = _layout_test_base()
            pages[0][1]["text"] = "Canada"
            out = _layout_test_run(paper, pages, sizes, ev)
            self.assertEqual(out["evidence"]["verified_author_indexes"], [])
            self.assertIsNone(out["selected_family"])
            pages[0][1]["text"] = "Alice"
            pages[0][2]["text"] = "Smith"
            ev["author_indexes"] = [1, 2]
            self.assertIsNone(_layout_test_run(paper, pages, sizes, ev)["selected_family"])

    def test_known_name_in_scientific_caption_is_not_a_byline(self):
            paper, pages, sizes, ev = _layout_test_base()
            pages[0][1]["text"] = "Figure 1: Alice Smith and Bob Jones method"
            self.assertIsNone(_layout_test_run(paper, pages, sizes, ev)["selected_family"])

    def test_country_row_is_discarded_and_cannot_change_valid_seed_geometry(self):
            paper, pages, sizes, ev = _layout_test_base()
            pages[0].append(_layout_test_line("Canada", [250, 165, 300, 177], 6))
            index = len(pages[0]) - 1
            ev["author_indexes"] = [1, index]
            out = _layout_test_run(paper, pages, sizes, ev)
            self.assertEqual(out["evidence"]["verified_author_indexes"], [1])
            self.assertEqual(out["evidence"]["rejected_author_indexes"], [index])
            self.assertEqual(out["observed_variant"], "centeredrows")

    def test_header_relative_indices_must_be_mapped_to_first_page(self):
            paper, pages, sizes, ev = _layout_test_base()
            pages[0].insert(0, _layout_test_line("arXiv:2501.00001v1", [10, 10, 30, 50], 8))
            bad = _layout_test_run(paper, pages, sizes, ev)
            self.assertIsNone(bad["selected_family"])
            mapped = dict(title_indexes=[1], author_indexes=[2], body_boundary=200)
            self.assertEqual(_layout_test_run(paper, pages, sizes, mapped)["observed_variant"], "centeredrows")

    def test_conflicting_indices_are_conservative(self):
            paper, pages, sizes, ev = _layout_test_base()
            for authors in ([0], [999], [-1], [1.2]):
                ev["author_indexes"] = authors
                self.assertIsNone(_layout_test_run(paper, pages, sizes, ev)["selected_family"])

    def test_multi_source_order_never_selects_a_family(self):
            paper, pages, sizes, ev = _layout_test_base()
            paper["source_ids"] = ["icml2026", "tmlr_2025plus"]
            paper["source_entries"] = [{"source_id": x} for x in reversed(paper["source_ids"])]
            first = _layout_test_run(paper, pages, sizes, ev)
            paper["source_ids"].reverse()
            paper["source_entries"].reverse()
            second = _layout_test_run(paper, pages, sizes, ev)
            self.assertEqual(first, second)
            self.assertIsNone(first["provenance_family"])
            self.assertIsNone(first["selected_family"])
            self.assertIn("multiple_metadata_source_families", first["ambiguities"])

    def test_output_is_plain_finite_json_without_mask_fields(self):
            for mutation in ("none", "boundary_nan", "rect_inf", "graphic_nan"):
                paper, pages, sizes, ev = _layout_test_base()
                graphics = []
                if mutation == "boundary_nan": ev["body_boundary"] = float("nan")
                if mutation == "rect_inf": pages[0][0]["rect"][0] = float("inf")
                if mutation == "graphic_nan": graphics = [[0, 0, float("nan"), 100]]
                result = _layout_test_run(paper, pages, sizes, ev, first_graphics=graphics)
                self.assertEqual(set(result), _LAYOUT_DIAGNOSTIC_KEYS)
                self.assertIsInstance(json.dumps(result, allow_nan=False), str)

    def test_no_input_mutation_or_caller_dictionary_reuse(self):
            args = _layout_test_base()
            before = deepcopy(args)
            first = _layout_test_run(*args)
            first["ambiguities"].append("caller edit")
            self.assertEqual(args, before)
            self.assertNotIn("caller edit", _layout_test_run(*args)["ambiguities"])

class SourceLayoutVariantTests(unittest.TestCase):
    def test_compact_footer_is_variant_not_provenance_rewrite(self):
            paper, pages, sizes, ev = _layout_test_base("neurips2025")
            pages[0][2]["text"] = ""
            pages[0].append(_layout_test_line("1North University 2South Institute", [55, 680, 295, 690], 5, 9))
            out = _layout_test_run(paper, pages, sizes, ev)
            self.assertEqual(out["observed_variant"], "compactbyline_footer")
            self.assertEqual(out["selected_family"], "neurips")

    def test_single_column_geometry_alone_is_not_journal_proof(self):
            out = _layout_test_run(*_layout_test_base("tmlr_2025plus"))
            self.assertTrue(out["evidence"]["body_geometry"]["single_column_observation"])
            self.assertFalse(out["evidence"]["body_geometry"]["official_template_proof"])
            self.assertEqual(out["observed_variant"], "centeredrows")

    def test_named_journal_notice_plus_geometry(self):
            for source in ("acl2025", "cvpr2025", "icml2026", "iclr2025", "neurips2025", "tmlr_2025plus"):
                paper, pages, sizes, ev = _layout_test_base(source)
                pages[0].append(_layout_test_line("Published in Transactions on Machine Learning Research (06/2025)",
                                     [80, 28, 500, 38], 9))
                result = _layout_test_run(paper, pages, sizes, ev)
                self.assertEqual(result["observed_variant"], "singlecolumnjournal")
                self.assertEqual(result["selected_family"], result["provenance_family"])

    def test_first_page_variant_does_not_follow_second_page_columns(self):
            paper, pages, sizes, ev = _layout_test_base()
            self.assertEqual(_layout_test_run(paper, pages, sizes, ev)["observed_variant"], "centeredrows")
            for item in pages[1]:
                item["rect"][2] = 280
            result = _layout_test_run(paper, pages, sizes, ev)
            self.assertTrue(result["evidence"]["body_geometry"]["single_column_observation"])
            self.assertFalse(result["evidence"]["later_page_geometry"]["single_column_observation"])
            self.assertEqual(result["observed_variant"], "centeredrows")

    def test_preprint_and_conflicting_publication_markers(self):
            paper, pages, sizes, ev = _layout_test_base("tmlr_2025plus")
            pages[0].append(_layout_test_line("Pre-print.", [80, 28, 150, 38], 9))
            self.assertEqual(_layout_test_run(paper, pages, sizes, ev)["observed_variant"], "preprint")
            pages[0].append(_layout_test_line("Published in Transactions on Machine Learning Research (06/2025)",
                                 [80, 43, 500, 53], 10))
            out = _layout_test_run(paper, pages, sizes, ev)
            self.assertIsNone(out["selected_family"])
            self.assertIn("conflicting_publication_layout_markers", out["ambiguities"])

    def test_later_page_columns_cannot_enable_journal_or_preprint_recipe(self):
            for notice, expected in [("Preprint.", "preprint"),
                                     ("Published in Transactions on Machine Learning Research (06/2025)", "singlecolumnjournal")]:
                paper, pages, sizes, ev = _layout_test_base("tmlr_2025plus")
                pages[0].append(_layout_test_line(notice, [80, 28, 500, 38], 99))
                before = _layout_test_run(paper, pages, sizes, ev)
                for item in pages[1]: item["rect"][2] = 280
                after = _layout_test_run(paper, pages, sizes, ev)
                self.assertEqual(before["observed_variant"], expected)
                self.assertEqual(after["observed_variant"], expected)
                self.assertEqual(before["selected_family"], after["selected_family"])
                if expected == "singlecolumnjournal":
                    for item in pages[0][4:-1]: item["rect"][2] = 280
                    for item in pages[1]: item["rect"][2] = 530
                    self.assertNotEqual(_layout_test_run(paper, pages, sizes, ev)["observed_variant"], "singlecolumnjournal")

    def test_label_protection_propagates_through_a_long_source_block(self):
            paper, pages, sizes, ev = _layout_test_base("icml2026")
            pages[0][2]["text"] = ""
            pages[0] += [_layout_test_line("Study sites:", [55, 650, 160, 659], 4),
                         _layout_test_line("1North University", [55, 670, 295, 679], 5, 9),
                         _layout_test_line("2South University", [55, 725, 295, 734], 5, 9)]
            self.assertIsNone(_layout_test_run(paper, pages, sizes, ev)["selected_family"])

def _region_test_line(text,y,x=60,right=540,size=10,block=None):
    return {'text':text,'rect':[x,y,right,y+size+2],'size':size,'block':y if block is None else block,'styled_heading':False}

class RegionalLayoutScopeTests(unittest.TestCase):
    def setUp(self):
        self.paper={'title':'A Scientific Paper','authors':['Alice Example','Bob Scholar'],'arxiv_id':'2501.12345','source_ids':['icml2026']}
        self.first=[_region_test_line('A Scientific Paper',50,size=18),_region_test_line('Alice Example1 and Bob Scholar2',92,size=11),
                    _region_test_line('1North University 2South Institute',118),_region_test_line('Abstract',180,size=12),
                    _region_test_line('We evaluate a scientific method and find accuracy 74.1.',210),
                    _region_test_line('Our code is available at https://github.com/lab/body-resource',250)]
    def run_plan(self,pages=None,graphics=None):
        pages=pages or [self.first];gs={i:[] for i in range(1,len(pages)+1)}
        if graphics is not None:gs.update(graphics)
        return rules._propose_frontmatter_and_margins(pages,[(600,800)]*len(pages),paper=self.paper,graphics_by_page=gs,rules_module=rules)
    def touches(self,result,item,page=1):
        return any(x['page']==page and rules._region_overlap(x['rect'],item['rect']) for x in result['regions'])
    def test_title_and_unknown_frontmatter_are_covered_body_is_not(self):
        self.first.insert(0,_region_test_line('North Research Branding',18))
        out=self.run_plan();self.assertFalse(out['unresolved'])
        for i in range(4):self.assertTrue(self.touches(out,self.first[i]))
        for i in range(4,len(self.first)):self.assertFalse(self.touches(out,self.first[i]))
    def test_later_caption_in_shared_block_does_not_swallow_abstract(self):
        for item in self.first:item['block']=0
        self.first.append(_region_test_line('Figure 1. A later scientific caption.',350,block=0))
        out=self.run_plan(graphics={1:[{'rect':[50,300,550,335]}]})
        self.assertTrue(self.touches(out,self.first[0]))
        self.assertFalse(self.touches(out,self.first[3]))
        self.assertFalse(self.touches(out,self.first[-1]))
    def test_title_icon_is_inside_title_region(self):
        out=self.run_plan(graphics={1:[{'rect':[65,48,90,73]}]})
        self.assertTrue(any(rules._region_overlap(x['rect'],[65,48,90,73]) for x in out['regions'] if x['target']=='title_block'))
    def test_teaser_before_abstract_stops_title_region(self):
        self.first[3]['rect']=[60,460,200,474]
        self.first[4]['rect']=[60,490,540,502];self.first[5]['rect']=[60,530,540,542]
        self.first.insert(3,_region_test_line('Figure 1. A Scientific Paper diagram.',415))
        out=self.run_plan(graphics={1:[{'rect':[45,150,555,400]}]})
        self.assertTrue(self.touches(out,self.first[0]));self.assertFalse(self.touches(out,self.first[3]))
        self.assertTrue(all(x['rect'][3]<150 for x in out['regions'] if x['target']=='title_block'))
    def test_unlabelled_abstract_inside_panel_is_not_bridged(self):
        self.first=[x for x in self.first if x['text']!='Abstract']
        out=self.run_plan(graphics={1:[{'rect':[50,160,550,720]}]})
        self.assertTrue(self.touches(out,self.first[0]))
        self.assertFalse(self.touches(out,self.first[3]))
        self.assertTrue(all(x['rect'][3]<160 for x in out['regions'] if x['target']=='title_block'))
    def test_title_named_abstract_is_not_its_own_body_boundary(self):
        self.paper['title']='Abstract Reasoning';self.first[0]['text']='Abstract Reasoning'
        out=self.run_plan();self.assertTrue(self.touches(out,self.first[0]));self.assertFalse(self.touches(out,self.first[3]))
    def test_metadata_title_mentioned_only_in_body_cannot_move_header_boundary(self):
        self.paper['title']='A New Scientific Title'
        self.first[0]['text']='An Earlier Scientific Title'
        self.first[4]['text']='Our experiment considers A New Scientific Title as a scientific example.'
        out=self.run_plan()
        self.assertTrue(self.touches(out,self.first[0]))
        self.assertFalse(self.touches(out,self.first[3]))
        self.assertFalse(self.touches(out,self.first[4]))
    def test_unknown_graphics_abstains_on_broad_title(self):
        out=self.run_plan(graphics={1:None});self.assertTrue(out['unresolved']);self.assertFalse(any(x['target']=='title_block' for x in out['regions']))
    def test_missing_body_boundary_is_not_a_guessed_fraction(self):
        self.first=self.first[:3]
        out=self.run_plan();self.assertTrue(out['unresolved']);self.assertFalse(out['regions'])
    def test_long_title_page_ends_before_next_page_abstract(self):
        self.first=[self.first[0],_region_test_line('Alice Example1',95),_region_test_line('Bob Scholar2',125),_region_test_line('North University',160)]
        out=self.run_plan([self.first,[_region_test_line('Abstract',60),_region_test_line('The scientific abstract remains visible and unchanged.',85)]])
        self.assertTrue(self.touches(out,self.first[-1]));self.assertFalse(any(x['page']==2 for x in out['regions']))
    def test_repeated_author_headers_do_not_mask_bibliography_names(self):
        pages=[self.first]
        for number in (2,3):
            pages.append([_region_test_line('Alice Example and Bob Scholar',35,x=60,right=350,size=9,block=0),
                          _region_test_line(str(number),35,x=570,right=580,size=9,block=0),
                          _region_test_line('Scientific body prose is retained in every paper.',80),
                          _region_test_line('Alice Example. A cited scientific method.',700)])
        out=self.run_plan(pages)
        for number in (2,3):
            self.assertTrue(self.touches(out,pages[number-1][0],number))
            self.assertFalse(self.touches(out,pages[number-1][-1],number))
    def test_publication_words_in_top_body_paragraph_are_preserved(self):
        other=[_region_test_line('Published in a journal, this prior method provides a baseline.',65,block=1),
               _region_test_line('We compare its model with our data under controlled conditions.',79,block=1)]
        out=self.run_plan([self.first,other]);self.assertFalse(any(x['page']==2 for x in out['regions']))
    def test_firstpage_footer_is_column_local(self):
        note=_region_test_line('*Corresponding author: Alice Example <alice@example.edu>.',700,x=45,right=295,size=9)
        science=_region_test_line('Scientific result 57.5 must remain.',700,x=325,right=570,size=10)
        self.first += [note,science]
        out=self.run_plan();self.assertTrue(self.touches(out,note));self.assertFalse(self.touches(out,science))
    def test_ordinary_scientific_note_in_same_block_is_preserved(self):
        note=_region_test_line('*Corresponding author: Alice Example <alice@example.edu>.',700,x=45,right=295,size=9,block=9)
        other=_region_test_line('2 The corpus contains mail from curator@archive.org.',718,x=45,right=295,size=9,block=9)
        self.first += [note,other]
        out=self.run_plan();self.assertTrue(self.touches(out,note));self.assertFalse(self.touches(out,other))
    def test_body_self_reference_arxiv_id_is_not_a_margin_stamp(self):
        cited=_region_test_line('arXiv:2501.12345. A cited scientific reference.',700,x=35,right=300,size=9)
        out=self.run_plan([self.first,[_region_test_line('References',80),cited]])
        self.assertFalse(self.touches(out,cited,2))
    def test_body_urls_remain_on_all_later_pages(self):
        other=[_region_test_line('Methods',60),_region_test_line('Our code is available at https://github.com/lab/body-resource',90)]
        out=self.run_plan([self.first,other]);self.assertFalse(any(x['page']==2 for x in out['regions']))
    def test_page_profile_uses_each_actual_page(self):
        single=[_region_test_line('This is ordinary scientific prose with enough words for the layout.',70+i*15) for i in range(8)]
        columns=[]
        for i in range(5):
            columns += [_region_test_line('This left column contains a scientific paragraph.',70+i*15,x=50,right=280),
                        _region_test_line('The right column has its own scientific paragraph.',70+i*15,x=320,right=550)]
        out=rules._region_page_profiles([single,columns],[(600,800)]*2,{1:[],2:[]})
        self.assertEqual(len(out[1]['column_bands']),1);self.assertEqual(len(out[2]['column_bands']),2)


class RegionalNativePlanTests(unittest.TestCase):
    setUp=RuleTests.setUp
    make_pdf=RuleTests.make_pdf
    apply=RuleTests.apply

    def test_regional_plan_masks_title_but_has_no_body_url_targets(self):
        self.make_pdf(extra=[('Our code is available at https://github.com/lab/body-code',260)])
        before=self.source.read_bytes();diagnostics={}
        plan=rules.build_region_plan(self.source,self.paper,diagnostics=diagnostics)
        self.assertEqual(set(plan),{'redactions','forbidden_strings','uncertain'})
        self.assertEqual(plan['forbidden_strings'],[])
        self.assertTrue(plan['redactions'])
        self.assertTrue(all(item['text']=='' for item in plan['redactions']))
        self.assertEqual(diagnostics['scope'],'frontmatter-margins-credits-v1')
        self.assertTrue(any(item['region_type']=='title_block' for item in diagnostics['regions']))
        cleaned=self.apply(plan)
        self.assertNotIn(self.paper['title'],cleaned)
        self.assertIn('Our scientific result is x = 42.',cleaned)
        self.assertIn('https://github.com/lab/body-code',cleaned)
        self.assertEqual(self.source.read_bytes(),before)

    def test_regional_diagnostics_none_does_not_change_rectangles(self):
        self.make_pdf();evidence={}
        self.assertEqual(rules.build_region_plan(self.source,self.paper),
                         rules.build_region_plan(self.source,self.paper,diagnostics=evidence))

    def test_regional_title_does_not_bridge_a_scientific_teaser(self):
        with fitz.open() as doc:
            page=doc.new_page(width=612,height=792)
            page.insert_text((72,60),self.paper['title'],fontsize=16)
            page.insert_text((72,95),'Alice Example')
            page.insert_text((72,115),'Example University')
            page.draw_rect(fitz.Rect(60,150,550,320),color=(0,0,0),fill=(.3,.4,.5))
            page.insert_text((72,345),'Figure 1. Scientific teaser and metric 57.5.',fontsize=10)
            page.insert_text((72,395),'Abstract',fontsize=12)
            page.insert_text((72,420),'The abstract and score 74.1 remain visible.')
            doc.save(self.source)
        plan=rules.build_region_plan(self.source,self.paper)
        self.assertTrue(all(item['rect'][3]<150 for item in plan['redactions'] if item['page']==1))
        clean=self.apply(plan)
        self.assertIn('Scientific teaser and metric 57.5.',clean)
        self.assertIn('The abstract and score 74.1 remain visible.',clean)

def _region_section_test_line(text,y,*,x=10,right=190,size=10,block=1,style=False,index=0):
    return {"text":text,"rect":[x,y,right,y+size],"size":size,
            "block":block,"styled_heading":style,"source_index":index}

def _region_section_test_profile(columns=((0,200),),protected=(),run_in=()):
    return {"confirmed":True,"source_family":"development_fixture",
            "column_bands":[list(x) for x in columns],"body_font":10,
            "protected_regions":list(protected),
            "layout_evidence":{"body_start_y":0,"run_in_heading_rows":list(run_in)}}

class RegionalSectionScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules=rules

    def run_case(self,rows,prof=None,names=()):
            return rules._propose_credit_regions({1:rows},{1:(200,800)},names=list(names),
                                          source_profiles={1:prof or _region_section_test_profile()},rules_module=self.rules)

    def basic(self,label="Acknowledgments"):
            return [_region_section_test_line(label,100,size=12,block=0,style=True,index=0),
                    _region_section_test_line("We thank the supporting team.",125,block=1,index=1),
                    _region_section_test_line("References",165,size=12,block=2,style=True,index=2),
                    _region_section_test_line("Ordinary citation and body URL https://example.org",190,block=3,index=3)]

    def test_bounded_same_column_section(self):
            result=self.run_case(self.basic())
            self.assertEqual(len(result["regions"]),1)
            self.assertEqual(result["regions"][0]["rect"],[10,100,190,135])
            self.assertFalse(result["unresolved"])

    def test_acknowledge_variant_with_actual_heading_style(self):
            self.assertEqual(len(self.run_case(self.basic("Acknowledge"))["regions"]),1)

    def test_word_fragmented_body_row_is_not_a_heading(self):
            rows=self.basic()
            rows[1:2]=[_region_section_test_line("This",125,x=10,right=30,block=1,index=1),
                       _region_section_test_line("research was supported",125,x=40,right=135,block=1,index=2),
                       _region_section_test_line("by a fund.",125,x=145,right=190,block=1,index=3)]
            rows[-2]["source_index"]=4;rows[-1]["source_index"]=5
            result=self.run_case(rows)
            self.assertEqual(len(result["regions"]),1)
            self.assertEqual(len(result["regions"][0]["source_line_evidence"]),4)

    def test_page1_frontmatter_boundary_required(self):
            p=_region_section_test_profile();p["layout_evidence"]={}
            result=self.run_case(self.basic(),p)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

    def test_page1_none_or_nonfinite_boundary_is_unresolved(self):
            for value in (None, float('nan'), float('inf'), '100'):
                with self.subTest(value=value):
                    p=_region_section_test_profile();p['layout_evidence']['body_start_y']=value
                    result=self.run_case(self.basic(),p)
                    self.assertFalse(result['regions']);self.assertTrue(result['unresolved'])

    def test_styled_scientific_ack_rubric_has_no_positive_credit(self):
            for heading in ('Acknowledgment','Acknowledge'):
                rows=self.basic(heading)
                rows[1]['text']='The theorem follows by induction over the input sequence.'
                result=self.run_case(rows)
                self.assertFalse(result['regions']);self.assertTrue(result['unresolved'])

    def test_technical_contributions_and_our_contributions_stay_visible(self):
            for heading in ('Contributions','Our Contributions','1.2 Our Contributions'):
                rows=self.basic(heading)
                rows[1]['text']='We introduce a sparse method and improve benchmark accuracy.'
                self.assertFalse(self.run_case(rows)['regions'])

    def test_contributor_relation_required_and_target_is_contribution(self):
            rows=self.basic('Author Contributions')
            rows[1]['text']='Ada Rivera implemented the system.'
            result=self.run_case(rows,names=['ada rivera'])
            self.assertEqual([r['target'] for r in result['regions']],['contribution'])
            self.assertFalse(self.run_case(rows)['regions'])

    def test_contribution_matrix_definitions_stay_visible(self):
            for text in ('Methodology: Y.X., the covariance matrix, is estimated from data.',
                         'Validation: Y.X.; here Y and X are matrices.',
                         'Conceptualization: the covariance matrix defines the model.'):
                rows=self.basic('Author Contributions');rows[1]['text']=text
                result=self.run_case(rows,names=['yueqi xie','yu xie'])
                self.assertFalse(result['regions']);self.assertTrue(result['unresolved'])

    def test_genuine_ambiguous_initials_and_joint_contributor_fields(self):
            for text in ('Y.X. implemented the model and coordinated the research project.',
                         'Validation: Y.X.; Writing: Ada Rivera.'):
                rows=self.basic('Author Contributions');rows[1]['text']=text
                result=self.run_case(rows,names=['yueqi xie','yu xie','ada rivera'])
                self.assertEqual([r['target'] for r in result['regions']],['contribution'])

    def test_run_in_ack_semantics_exclude_its_heading(self):
            for body,expected in (('The theorem follows by induction.',0),
                                  ('We thank our colleagues.',1)):
                rows=[_region_section_test_line('Acknowledgments: '+body,100,block=0,index=0),
                      _region_section_test_line('References',140,size=12,block=1,style=True,index=1)]
                result=self.run_case(rows,_region_section_test_profile(run_in=[0]))
                self.assertEqual(len(result['regions']),expected)

    def test_unconfirmed_profile_abstains(self):
            p=_region_section_test_profile();p["confirmed"]=False
            result=self.run_case(self.basic(),p)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

    def test_left_credit_does_not_mask_right_science(self):
            rows=[_region_section_test_line("Acknowledgments",100,x=10,right=90,size=12,block=0,style=True,index=0),
                  _region_section_test_line("We thank our colleagues.",125,x=10,right=90,block=1,index=1),
                  _region_section_test_line("References",160,x=10,right=90,size=12,block=2,style=True,index=2),
                  _region_section_test_line("Scientific results and URL remain.",125,x=110,right=190,block=3,index=3)]
            result=self.run_case(rows,_region_section_test_profile(((0,100),(110,200))))
            self.assertEqual(result["regions"][0]["rect"][2],90)

    def test_real_ack_below_unrelated_table_is_eligible(self):
            rows=[_region_section_test_line("Table 3: Scientific results",30,block=8,index=8)]+self.basic()
            p=_region_section_test_profile(protected=[{"rect":[0,0,200,75],"kind":"table"}])
            self.assertEqual(len(self.run_case(rows,p)["regions"]),1)

    def test_same_row_rubric_peer_is_not_a_section(self):
            rows=[_region_section_test_line("Acknowledgment",100,x=10,right=80,size=12,block=0,style=True,index=0),
                  _region_section_test_line("Describe the patient's strengths.",100,x=100,right=190,block=1,index=1),
                  _region_section_test_line("References",160,size=12,block=2,style=True,index=2)]
            result=self.run_case(rows)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

    def test_prompt_and_generated_content_abstains(self):
            rows=self.basic("Author Contributions")
            rows[1]["text"]="The model generated the following statement. Alex implemented the model."
            result=self.run_case(rows)
            self.assertFalse(result["regions"]);self.assertTrue(result["rejected"])

    def test_procedural_payment_suffix_does_not_get_enveloped(self):
            rows=self.basic()
            rows[1]["text"]="We thank Ada Rivera. Participants were paid ten dollars per hour."
            result=self.run_case(rows)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

    def test_graphic_between_credit_lines_prevents_bridge(self):
            rows=[_region_section_test_line("Acknowledgments",100,size=12,block=0,style=True,index=0),
                  _region_section_test_line("We thank Ada Rivera.",125,block=1,index=1),
                  _region_section_test_line("Further credit.",150,block=2,index=2),
                  _region_section_test_line("References",180,size=12,block=3,style=True,index=3)]
            p=_region_section_test_profile(protected=[{"rect":[5,140,195,165],"kind":"scientific_graphic"}])
            result=self.run_case(rows,p)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

    def test_run_in_heading_requires_leading_label_proof(self):
            rows=[_region_section_test_line("Author Contributions: Ada Rivera implemented the system.",100,block=0,index=0),
                  _region_section_test_line("References",140,size=12,block=1,style=True,index=1)]
            self.assertFalse(self.run_case(rows,names=['ada rivera'])["regions"])
            self.assertEqual(len(self.run_case(rows,_region_section_test_profile(run_in=[0]),names=['ada rivera'])["regions"]),1)

    def test_common_same_page_column_bridge_produces_separate_regions(self):
            rows=[_region_section_test_line("Acknowledgments",650,x=10,right=90,size=12,block=0,style=True,index=0),
                  _region_section_test_line("This work was supported by grant No.",675,x=10,right=90,block=1,index=1),
                  _region_section_test_line("123456 project funding.",70,x=110,right=190,block=2,index=2),
                  _region_section_test_line("References",110,x=110,right=190,size=12,block=3,style=True,index=3)]
            result=self.run_case(rows,_region_section_test_profile(((0,100),(110,200))))
            self.assertEqual(len(result["regions"]),2,result)
            self.assertEqual({r["column_index"] for r in result["regions"]},{0,1})

    def test_single_column_float_interrupted_continuation_remains_unresolved(self):
            pages={1:[_region_section_test_line("Acknowledgments",650,size=12,block=0,style=True,index=0),
                      _region_section_test_line("The work was supported by a",675,block=1,index=1)],
                   2:[_region_section_test_line("Figure 4: Scientific result",50,block=2,index=2),
                      _region_section_test_line("foundation, with further credit.",250,block=3,index=3),
                      _region_section_test_line("Impact Statement",300,size=12,block=4,style=True,index=4)]}
            p1=_region_section_test_profile();p2=_region_section_test_profile(protected=[{"rect":[0,0,200,200],"kind":"scientific_figure"}])
            result=rules._propose_credit_regions(pages,{1:(200,800),2:(200,800)},names=[],
                source_profiles={1:p1,2:p2},rules_module=self.rules)
            self.assertFalse(result["regions"]);self.assertTrue(result["unresolved"])

class RegionalActualFailureRegressionTests(unittest.TestCase):
    def setUp(self):
        self.paper={'title':'A Scientific Paper','authors':['Mira Chen'],'arxiv_id':'2501.12345'}
        self.first=[_region_test_line('A Scientific Paper',50,size=18),
            _region_test_line('Mira Chen1',100,size=11),
            _region_test_line('North University',180,size=10),
            _region_test_line('mira@north.edu',211,size=10),
            _region_test_line('Abstract',250,size=12),
            _region_test_line('Our scientific body and its URL https://example.org remain.',280)]
    def plan(self,pages=None,graphics=None):
        pages=pages or [self.first]
        return rules._propose_frontmatter_and_margins(pages,[(600,800)]*len(pages),
            paper=self.paper,graphics_by_page=graphics or {i:[] for i in range(1,len(pages)+1)},rules_module=rules,
            slice_boxes=lambda number,line,start,end:[[line['rect'][0]+start,line['rect'][1],line['rect'][0]+end,line['rect'][3]]])
    def test_four_field_owned_contact_contract_is_adapted(self):
        note=_region_test_line('1Email: mira@north.edu',700,x=50,right=240,size=9)
        self.first.append(note)
        owned=rules._own_contact_slices(self.first,600,800,['mira chen'],{'1'})
        self.assertTrue(owned)
        self.assertTrue(all(len(item)==4 for item in owned))
        out=self.plan()
        self.assertTrue(any(x['target']=='footer' and x['rect'][1]<705<x['rect'][3] for x in out['regions']))
    def test_lateral_own_stamp_does_not_cut_contact_row(self):
        stamp=_region_test_line('arXiv:2501.12345v1 [cs.LG] 1 Jan 2025',215,x=11,right=38,size=20)
        stamp['rect'][3]=560
        self.first.insert(4,stamp)
        out=self.plan();title=next(x for x in out['regions'] if x['target']=='title_block')
        self.assertGreater(title['rect'][3],self.first[3]['rect'][3])
        self.assertLess(title['rect'][3],250)
        self.assertTrue(any(x['category']=='identifier' and x['rect'][3]>560 for x in out['regions']))
    def test_cut_reaches_fixed_point_for_other_tall_rows(self):
        self.first[3]['rect']=[60,210,540,230]
        tall=_region_test_line('A protected scientific vertical label',225,x=11,right=38,size=10)
        tall['rect'][3]=560;self.first.insert(4,tall)
        out=self.plan();title=next(x for x in out['regions'] if x['target']=='title_block')
        self.assertLess(title['rect'][3],210)
        self.assertFalse(any(x['category']=='identifier' for x in out['regions']))
    def test_graphic_blocked_stamp_cannot_authorize_cut_exemption(self):
        stamp=_region_test_line('arXiv:2501.12345v1 [cs.LG]',215,x=11,right=38,size=20)
        stamp['rect'][3]=560;self.first.insert(4,stamp)
        out=self.plan(graphics={1:[{'rect':[10,210,39,570]}]})
        title=next(x for x in out['regions'] if x['target']=='title_block')
        self.assertLess(title['rect'][3],211)
        self.assertFalse(any(x['category']=='identifier' for x in out['regions']))
    def test_repeated_a_preprint_is_a_margin_label(self):
        pages=[self.first]
        for n in (2,3):
            pages.append([_region_test_line('A PREPRINT',36,x=460,right=525,size=9),
                _region_test_line('Scientific body prose is retained without any identity search.',80)])
        out=self.plan(pages)
        self.assertEqual({x['page'] for x in out['regions'] if x['target']=='header'},{2,3})
    def test_a_preprint_in_body_or_literal_stays_visible(self):
        pages=[self.first,[_region_test_line('Output:',20,x=460,right=525,size=9,block=1),
            _region_test_line('A PREPRINT',36,x=460,right=525,size=9,block=1),
            _region_test_line('A PREPRINT',300,x=460,right=525,size=9),
            _region_test_line('Scientific body prose stays unchanged.',80)],
            [_region_test_line('A PREPRINT',36,x=460,right=525,size=9),
             _region_test_line('Scientific body prose stays unchanged.',80)]]
        out=self.plan(pages)
        self.assertFalse(any(x['page']==2 for x in out['regions']))


class RegionalCompactJournalTitleTests(unittest.TestCase):
    def setUp(self):
        self.paper={'title':'A Different Later Title','authors':['Mira Chen','Bob Park'],
                    'source_ids':['tmlr_2025plus'],'arxiv_id':'2501.12345'}
        self.rows=[_region_test_line('Kernel Generative Networks for Calibration',86.78,x=160,right=440,size=11.3574),
                   _region_test_line('Mira Chen1, Bob Park2',106.31,x=200,right=400,size=10.3636),
                   _region_test_line('Abstract. Our scientific method and its accuracy remain unchanged.',173.42,x=72,right=540,size=8.518),
                   _region_test_line('We evaluate scientific models using a controlled experiment.',185,x=72,right=540,size=8.518)]
        for row in self.rows:row['primary_styles']=[0]
        self.rows[0]['primary_styles']=[16]
        self.rows[2]['spans']=[{'text':'Abstract.','flags':20},{'text':' Our scientific method and its accuracy remain unchanged.','flags':4}]
    def plan(self):
        return rules._propose_frontmatter_and_margins([self.rows],[(600,800)],paper=self.paper,
            graphics_by_page={1:[]},rules_module=rules)
    def title(self):return [x for x in self.plan()['regions'] if x['target']=='title_block']
    def test_styled_small_title_with_stale_metadata_preserves_run_in_abstract(self):
        targets=self.title();self.assertEqual(len(targets),1)
        self.assertGreater(targets[0]['rect'][3],self.rows[1]['rect'][3])
        self.assertLess(targets[0]['rect'][3],self.rows[2]['rect'][1])
    def test_unknown_and_mixed_sources_cannot_enable_fallback(self):
        self.paper['source_ids']=[];self.assertFalse(self.title())
        self.paper['source_ids']=['tmlr_2025plus','icml2026'];self.assertFalse(self.title())
    def test_caption_and_literal_are_not_fallback_titles(self):
        self.rows[0]['text']='Figure 1. Kernel Generative Networks for Calibration';self.assertFalse(self.title())
        self.rows[0]['text']='Output: Kernel Generative Networks for Calibration';self.assertFalse(self.title())
    def test_title_needs_own_style_and_centered_geometry(self):
        self.rows[0]['primary_styles']=[0];self.assertFalse(self.title())
        self.rows[0]['primary_styles']=[16];self.rows[0]['rect'][0]=20;self.rows[0]['rect'][2]=300;self.assertFalse(self.title())
    def test_full_author_name_and_actual_abstract_label_style_required(self):
        self.paper['authors']=['Mira'];self.assertFalse(self.title())
        self.paper['authors']=['Mira Chen','Bob Park'];self.rows[2]['spans'][0]['flags']=4;self.assertFalse(self.title())
    def test_intervening_scientific_row_blocks_fallback(self):
        self.rows.append(_region_test_line('Scientific output from the tested model.',101,x=72,right=540,size=10.3636))
        self.assertFalse(self.title())


class RegionalBibliographyMarginPreservationTests(unittest.TestCase):
    def check_citation(self,text,y,x,right):
        paper={'title':'A Scientific Paper','authors':['Mira Chen'],'arxiv_id':'2501.12345'}
        first=[_region_test_line('A Scientific Paper',50,size=18),_region_test_line('Mira Chen1',100),
               _region_test_line('Abstract',180),_region_test_line('We retain scientific body prose.',210)]
        citation=_region_test_line(text,y,x=x,right=right,size=9)
        later=[_region_test_line('References',72),citation]
        out=rules._propose_frontmatter_and_margins([first,later],[(600,792)]*2,paper=paper,
            graphics_by_page={1:[],2:[]},rules_module=rules)
        self.assertFalse(any(item['page']==2 and rules._region_overlap(item['rect'],citation['rect']) for item in out['regions']))
    def test_top_bibliographic_preprint_is_preserved(self):
        self.check_citation('preprint arXiv:2102.06514, 2021.',95.349,92.879,229.232)
    def test_bottom_bibliographic_preprint_is_preserved(self):
        self.check_citation('preprint arXiv:2205.06938.',764.405,316.351,427.798)
    def test_own_bibliographic_preprint_is_not_a_lateral_stamp(self):
        self.check_citation('preprint arXiv:2501.12345, 2025.',764,316,500)


"""Portable unittest class; integration supplies `rules` with the proposal helper."""
from copy import deepcopy
import unittest


class NativeStrokeEnvelopeTests(unittest.TestCase):
    def fixture(self, kind='fs'):
        d = {'type': kind, 'seqno': 2, 'layer': '', 'rect': [10, 10, 20, 20],
             'items': [['c', [10, 10], [12, 10], [20, 18], [20, 20]],
                       ['c', [20, 20], [18, 20], [10, 12], [10, 10]]]}
        log = [('fill-text', (0, 0, 1, 1), '')] * 2
        log += ([('fill-path', [10, 10, 20, 20], '')] if kind == 'fs' else [])
        log += [('stroke-path', [6, 6, 24, 24], '')]
        return d, log, {2: 1}

    def result(self, *args):
        return rules._region_native_stroke_envelope(*args)

    def test_exact_fill_stroke_pair_uses_native_complete_envelope(self):
        self.assertEqual(self.result(*self.fixture()), (6, 6, 24, 24))

    def test_stroke_only_uses_its_exact_command(self):
        self.assertEqual(self.result(*self.fixture('s')), (6, 6, 24, 24))

    def test_missing_layer_or_layer_mismatch_is_unresolved(self):
        d, log, owners = self.fixture()
        log[3] = ('stroke-path', [6, 6, 24, 24], 'other layer')
        self.assertIsNone(self.result(d, log, owners))
        del d['layer']
        self.assertIsNone(self.result(d, log, owners))

    def test_another_drawing_owns_the_apparent_stroke(self):
        d, log, owners = self.fixture()
        owners[3] = 1
        self.assertIsNone(self.result(d, log, owners))
        owners = {2: 2}
        self.assertIsNone(self.result(d, log, owners))

    def test_nonadjacent_merge_and_wrong_command_types_are_unresolved(self):
        d, log, owners = self.fixture()
        log.insert(3, ('fill-text', [6, 6, 24, 24], ''))
        self.assertIsNone(self.result(d, log, owners))
        d['seqno'] = -1
        self.assertIsNone(self.result(d, log, owners))

    def test_wrong_fill_or_insufficient_stroke_bounds_are_unresolved(self):
        d, log, owners = self.fixture()
        log[2] = ('fill-path', [11, 10, 20, 20], '')
        self.assertIsNone(self.result(d, log, owners))
        d, log, owners = self.fixture()
        log[3] = ('stroke-path', [11, 10, 20, 20], '')
        self.assertIsNone(self.result(d, log, owners))

    def test_sharp_miter_cannot_use_width_only_without_native_proof(self):
        d, log, owners = self.fixture('s')
        d.update(width=1.0, lineJoin=0, items=[['l', [10, 10], [20, 20]], ['l', [20, 20], [10.01, 10]]])
        log[2] = ('fill-path', [9, 9, 21, 21], '')
        self.assertIsNone(self.result(d, log, owners))

    def test_reversal_and_degenerate_paths_cannot_guess_an_envelope(self):
        d, log, owners = self.fixture('s')
        d['items'] = [['l', [10, 10], [20, 20]], ['l', [20, 20], [10, 10]]]
        log[2] = ('stroke-path', [10, 10, 10, 20], '')
        self.assertIsNone(self.result(d, log, owners))
        d, log, owners = self.fixture()
        d['items'] = [['c', [10, 10], [10, 10], [10, 10], [10, 10]]]
        self.assertIsNone(self.result(d, log, owners))

    def test_unsupported_items_and_nonfinite_boxes_are_unresolved(self):
        d, log, owners = self.fixture()
        d['items'] = [['unknown', [10, 10], [20, 20]]]
        self.assertIsNone(self.result(d, log, owners))
        d, log, owners = self.fixture()
        log[3] = ('stroke-path', [6, 6, float('inf'), 24], '')
        self.assertIsNone(self.result(d, log, owners))

    def test_group_or_clip_is_not_a_painted_stroke_record(self):
        for kind in ('group', 'clip', 'f'):
            d, log, owners = self.fixture()
            d['type'] = kind
            self.assertIsNone(self.result(d, log, owners))


"""Copy/import this class into the complete frozen core test environment."""
from copy import deepcopy
import unittest
from utility.anonymize import anonymize_rules as rules


class ActualSectionBoundaryIntegrationTests(unittest.TestCase):
    def row(self,text,y,*,x=309,right=545,size=10,block=0,index=0,style=False,spans=None):
        return dict(text=text,rect=[x,y,right,y+size],size=size,block=block,
                    source_index=index,styled_heading=style,spans=spans or [])

    def profile(self,columns=((50,286),(309,545)),run_in=()):
        return dict(confirmed=True,source_family='actual_layout_fixture',
                    column_bands=[list(x) for x in columns],body_font=10,
                    protected_regions=[],layout_evidence={'run_in_heading_rows':list(run_in)})

    def run_in_case(self,emphasized=True):
        label={'text':'Acknowledgements','flags':20 if emphasized else 4,'size':10.36,
               'font':'NimbusSanL-Bold' if emphasized else 'NimbusSanL-Regu'}
        body={'text':' The authors thank the support of the Research Foundation.',
              'flags':4 if emphasized else 20,'size':10.36,'font':'NimbusSanL-Regu'}
        heading=self.row(label['text']+body['text'],410,x=72,right=540,size=10.36,
                         block=4,index=25,style=True,spans=[label,body])
        tail=self.row('This work was supported by grant 2031985.',424,x=72,right=540,
                      size=10.36,block=4,index=26)
        refs=self.row('References',497,x=72,right=128,size=10.36,block=5,index=30,style=True)
        run_in=[25] if rules._region_credit_run_in_style(heading) else []
        return rules._propose_credit_regions({12:[heading,tail,refs]},{12:(612,792)},
            names=[],source_profiles={12:self.profile(((72,540),),run_in)},rules_module=rules)

    def test_exact_emphasized_run_in_without_colon_is_integrated(self):
        result=self.run_in_case()
        self.assertEqual(len(result['regions']),1,result)
        self.assertEqual(result['regions'][0]['page'],12)
        self.assertLess(result['regions'][0]['rect'][3],497)

    def test_bold_body_cannot_supply_missing_leading_label_style(self):
        self.assertFalse(self.run_in_case(False)['regions'])

    def next_page_case(self,*,first='References',complete=True,confirmed=True,graphic=False):
        heading=self.row('8. Acknowledgments',589,size=12,block=29,index=100,style=True)
        body=self.row('We thank Ada Rivera for helpful discussions'+('.' if complete else ' and'),
                      610,block=30,index=101)
        # This earlier reading-order LEFT-column science may have larger y.
        left=self.row('Scientific results and https://example.org remain visible.',700,
                      x=50,right=286,block=28,index=99)
        end=self.row(first,74,x=50,right=125,size=12,block=0,index=0,style=True)
        citation=self.row('[1] Ordinary scientific bibliography, 2025.',100,
                          x=50,right=286,block=1,index=1)
        profiles={8:self.profile(),9:self.profile()};profiles[9]['confirmed']=confirmed
        if graphic:profiles[9]['protected_regions']=[{'rect':[50,20,280,65],'kind':'scientific_figure'}]
        return rules._propose_credit_regions({8:[left,heading,body],9:[end,citation]},
            {8:(612,792),9:(612,792)},names=[],source_profiles=profiles,rules_module=rules)

    def test_completed_last_column_ack_ends_before_next_page_references(self):
        result=self.next_page_case()
        self.assertEqual(len(result['regions']),1,result)
        region=result['regions'][0]
        self.assertEqual(region['page'],8)
        self.assertEqual(region['end_evidence']['kind'],'next_page_references_without_continuation')
        self.assertGreater(region['rect'][0],300)
        self.assertLess(region['rect'][3],700)

    def test_unfinished_credit_unknown_layout_or_intervening_figure_abstains(self):
        for options in ({'complete':False},{'confirmed':False},{'graphic':True},
                        {'first':'Figure 7: Scientific results'},
                        {'first':'9. Further experiments'},
                        {'first':'We evaluate the method on another dataset.'}):
            with self.subTest(options=options):
                self.assertFalse(self.next_page_case(**options)['regions'])

    def test_unclassified_trailing_right_or_spanning_row_blocks_end_helper(self):
        heading=self.row('8. Acknowledgments',589,size=12,block=29,index=100,style=True)
        body=self.row('We thank Ada Rivera.',610,block=30,index=101)
        refs=self.row('References',74,x=50,right=125,size=12,style=True)
        profiles={8:self.profile(),9:self.profile()}
        for x,right in ((309,545),(50,545)):
            extra=self.row('Unclassified source content.',720,x=x,right=right,block=31,index=102)
            normalized={8:rules._region_credit_rows([heading,body,extra],rules),
                        9:rules._region_credit_rows([refs],rules)}
            selected=[r for r in normalized[8] if r['rect'][1]<700]
            self.assertIsNone(rules._region_credit_next_page_references(
                8,1,selected,normalized,profiles,rules))


if __name__=='__main__':
    unittest.main()


class RegionalSlicePaddingTests(unittest.TestCase):
    def test_padding_cannot_enter_retained_email_brackets(self):
        selected=[[115.093,668.41,194.321,677.376]]
        others=[[110.036,668.41,115.093,677.376],[194.321,668.41,199.378,677.376]]
        box=rules._region_padded_slice_box(selected,others)
        self.assertEqual(box[0],115.093);self.assertEqual(box[2],194.321)
        self.assertFalse(any(rules._region_overlap(box,other) for other in others))
        self.assertTrue(rules._region_contains(box,selected[0]))
    def test_padding_is_retained_when_source_space_is_empty(self):
        self.assertEqual(rules._region_padded_slice_box([[10,20,30,40]],[]),[9.65,19.65,30.35,40.35])
    def test_overlapping_or_zero_advance_neighbor_requires_abstention(self):
        for other in ([29,20,31,40],[30,20,30,40]):
            self.assertIsNone(rules._region_padded_slice_box([[10,20,30,40]],[other]))
    def test_nearby_row_limits_only_safe_vertical_padding(self):
        box=rules._region_padded_slice_box([[10,20,30,40]],[[10,40.1,30,50]])
        self.assertEqual(box[3],40.1);self.assertTrue(rules._region_contains(box,[10,20,30,40]))

class RegionalSingleColumnReferenceBoundaryTests(unittest.TestCase):
    def fixture(self):
        def row(text,y,style=False,block=0):
            return {'text':text,'rect':[72,y,540,y+10],'size':10,'block':block,'styled_heading':style}
        heading=row('Acknowledgments',600,True,1);body=row('We thank Mira Chen for helpful discussions.',630,False,2)
        refs=row('References',75,True,3)
        profiles={p:{'confirmed':True,'column_bands':[[72,540]],'body_font':10,'protected_regions':[],'layout_evidence':{},'source_family':'tmlr'} for p in (10,11)}
        return {10:[heading,body],11:[refs]},profiles
    def test_completed_single_column_credit_ends_at_next_page_references(self):
        pages,profiles=self.fixture()
        out=rules._propose_credit_regions(pages,{10:(612,792),11:(612,792)},names=[],source_profiles=profiles,rules_module=rules)
        self.assertEqual(len(out['regions']),1,out)
        self.assertEqual(out['regions'][0]['end_evidence']['kind'],'next_page_references_without_continuation')
        self.assertEqual(out['regions'][0]['page'],10)
    def test_single_column_float_or_scientific_next_page_stays_unresolved(self):
        for mode in ('graphic','science','unknown'):
            pages,profiles=self.fixture()
            if mode=='graphic':profiles[11]['protected_regions']=[{'rect':[72,20,540,70],'kind':'graphic'}]
            elif mode=='science':pages[11][0]['text']='New scientific experiments'
            else:profiles[11]['confirmed']=False
            out=rules._propose_credit_regions(pages,{10:(612,792),11:(612,792)},names=[],source_profiles=profiles,rules_module=rules)
            self.assertFalse(out['regions'],mode)

class RegionalNumberedResourceCreditTests(unittest.TestCase):
    def fixture(self):
        def row(text,y,style=False,block=0,size=10):
            return {'text':text,'rect':[72,y,540,y+size],'size':size,'block':block,
                    'styled_heading':style,'primary_fonts':['Journal-Bold' if style else 'Journal-Regular']}
        rows=[row('6 Conclusion',375,True,1,12),row('The model achieves its scientific objective.',410,False,2),
              row('7 Acknowledgement',552,True,3,12),
              row('This research has been conducted using the Population Study Resource under Application Number 42826.',577,False,4),
              row('Copyright 2023, Research Service.',590,False,4),row('References',614,True,5,12)]
        profile={'confirmed':True,'column_bands':[[72,540]],'body_font':10,'protected_regions':[],
                 'layout_evidence':{},'source_family':'tmlr'}
        return rows,profile
    def plan(self,rows,profile):
        return rules._propose_credit_regions({9:rows},{9:(612,792)},names=[],source_profiles={9:profile},rules_module=rules)
    def test_numbered_resource_ack_preserves_conclusion_and_references(self):
        rows,profile=self.fixture();out=self.plan(rows,profile)
        self.assertEqual(len(out['regions']),1,out)
        self.assertGreater(out['regions'][0]['rect'][1],rows[1]['rect'][3])
        self.assertLess(out['regions'][0]['rect'][3],rows[-1]['rect'][1])
    def test_hierarchy_font_and_source_profile_are_required(self):
        for mode in ('number','font','profile','boundary'):
            rows,profile=self.fixture()
            if mode=='number':rows[0]['text']='2 Conclusion'
            elif mode=='font':rows[0]['primary_fonts']=['Another-Font']
            elif mode=='profile':profile['source_family']=None
            else:rows[-1]['text']='Methods'
            self.assertFalse(self.plan(rows,profile)['regions'],mode)
    def test_resource_like_scientific_or_generated_content_is_preserved(self):
        for text in ('This research is supported by evidence in Figure 42826.',
                     'The model generated the following statement. This research has been conducted using a Resource under Application Number 42826.'):
            rows,profile=self.fixture();rows[3]['text']=text
            self.assertFalse(self.plan(rows,profile)['regions'],text)


class RegionalCompleteAuthorNoteTests(unittest.TestCase):
    def fixture(self):
        paper={'title':'A Scientific Paper','authors':['Mira Chen','Bob Park'],'arxiv_id':'2501.12345','source_ids':['tmlr_2025plus']}
        rows=[_region_test_line('A Scientific Paper',50,size=18),
              _region_test_line('Mira Chen1 and Bob Park2',100,size=11),
              _region_test_line('Abstract',180,size=12),_region_test_line('Scientific content remains visible.',210),
              _region_test_line('1North University, 2Bright AI',665,x=90,right=260,size=9,block=7),
              _region_test_line('*corresponding author: mira@north.edu',675,x=90,right=260,size=9,block=7)]
        return paper,rows
    def plan(self,paper,rows):
        return rules._propose_frontmatter_and_margins([rows],[(600,800)],paper=paper,
            graphics_by_page={1:[]},rules_module=rules,footer_affiliation_slices=rules._region_footer_affiliation_slices)
    def test_overlapping_complete_affiliation_contact_rows_form_one_region(self):
        paper,rows=self.fixture();out=self.plan(paper,rows)
        notes=[x for x in out['regions'] if x['boundary_evidence']=='complete_same_block_author_affiliation_and_contact_note']
        self.assertEqual(len(notes),1,out)
        self.assertTrue(all(rules._region_contains(notes[0]['rect'],row['rect']) for row in rows[-2:]))
        self.assertFalse(out['unresolved'])
    def test_adjacent_column_science_is_preserved(self):
        paper,rows=self.fixture();science=_region_test_line('Scientific result 74.1.',675,x=320,right=560,size=9,block=8);rows.append(science)
        out=self.plan(paper,rows);self.assertFalse(any(rules._region_overlap(x['rect'],science['rect']) for x in out['regions']))
        self.assertTrue(any(x['boundary_evidence']=='complete_same_block_author_affiliation_and_contact_note' for x in out['regions']))
    def test_undeclared_marker_or_study_site_label_blocks_institution_region(self):
        for mode in ('marker','label'):
            paper,rows=self.fixture()
            if mode=='marker':rows[1]['text']='Mira Chen1 and Bob Park1'
            else:rows.append(_region_test_line('Study sites:',640,x=90,right=260,size=9,block=6))
            out=self.plan(paper,rows)
            self.assertFalse(any(x['boundary_evidence']=='complete_same_block_author_affiliation_and_contact_note' for x in out['regions']))
    def test_mixed_scientific_suffix_cannot_be_absorbed(self):
        paper,rows=self.fixture();science=_region_test_line('We measure model accuracy in the next experiment.',686,x=90,right=280,size=9,block=7);rows.append(science)
        out=self.plan(paper,rows);self.assertFalse(any(rules._region_overlap(x['rect'],science['rect']) for x in out['regions']))

class RegionalSourceVenueFooterTests(unittest.TestCase):
    def fixture(self):
        paper={'title':'A Scientific Paper','authors':['Mira Chen'],'source_ids':['neurips2025']}
        rows=[_region_test_line('A Scientific Paper',50,size=18),_region_test_line('Mira Chen',100),
              _region_test_line('Abstract',180),_region_test_line('The scientific body remains.',210),
              _region_test_line('More scientific discussion.',700),
              _region_test_line('39th Conference on Neural Information Processing Systems (NeurIPS 2025).',745,size=9,block=9)]
        return paper,rows
    def test_source_year_and_isolated_footer_enable_venue_notice(self):
        paper,rows=self.fixture()
        self.assertTrue(rules._region_source_footer_notice(rows,5,(600,800),paper,rules))
        out=rules._propose_frontmatter_and_margins([rows],[(600,800)],paper=paper,graphics_by_page={1:[]},rules_module=rules)
        self.assertTrue(any(x['target']=='footer' and rules._region_contains(x['rect'],rows[-1]['rect']) for x in out['regions']))
    def test_reference_block_wrong_source_year_and_body_positions_reject(self):
        for mode in ('citation','source','year','body'):
            paper,rows=self.fixture()
            if mode=='citation':rows.append(_region_test_line('A cited paper by other authors.',730,size=9,block=9))
            elif mode=='source':paper['source_ids']=['icml2025']
            elif mode=='year':paper['source_ids']=['neurips2026']
            else:rows[-1]['rect']=[60,300,540,311]
            self.assertFalse(rules._region_source_footer_notice(rows,5,(600,800),paper,rules),mode)


class RegionalInlineItalicContinuationTests(unittest.TestCase):
    def span(self,text,italic=False,bold=False,font=None):
        return {'text':text,'flags':4+(2 if italic else 0)+(16 if bold else 0),'size':11.,
                'font':font or ('Body-Italic' if italic else 'Body-Bold' if bold else 'Body-Regular')}
    def row(self,text,y,spans,block=2):
        return {'text':text,'rect':[50,y,286,y+11],'size':11.,'block':block,'spans':spans,
                'styled_heading':any(s['flags']&18 for s in spans),'primary_fonts':sorted({s['font'] for s in spans})}
    def fixture(self):
        previous=self.row('This work was funded by the regional',100,[self.span('This work was funded by the regional')])
        later=self.row('Programme: Project Alpha, grant 1234',113,
                       [self.span('Programme: '),self.span('Project Alpha',italic=True),self.span(', grant 1234')])
        return previous,later
    def test_inline_italics_in_a_continuous_paragraph_are_not_a_heading(self):
        before,later=self.fixture();self.assertTrue(rules._region_inline_italic_continuation(later,before))
    def test_an_italic_font_run_may_wrap_across_the_source_line(self):
        before=self.row('the national project Computing Centre',100,[self.span('the national project '),self.span('Computing Centre',italic=True)])
        later=self.row('for advanced computing (grant 1234)',113,[self.span('for advanced computing',italic=True),self.span(' (grant 1234)')])
        self.assertTrue(rules._region_inline_italic_continuation(later,before))
        later['spans'][0]['font']='Unrelated-Italic';self.assertFalse(rules._region_inline_italic_continuation(later,before))
    def test_bold_or_fully_emphasized_unknown_headings_still_stop(self):
        before,later=self.fixture()
        later['spans']=[self.span('New methods',bold=True),self.span(' and implementation')]
        self.assertFalse(rules._region_inline_italic_continuation(later,before))
        later['spans']=[self.span('New research section',italic=True)]
        self.assertFalse(rules._region_inline_italic_continuation(later,before))
    def test_new_block_sentence_gap_or_font_size_cannot_borrow_continuation(self):
        for change in ('block','sentence','gap','size','missing'):
            before,later=self.fixture()
            if change=='block':later['block']=7
            elif change=='sentence':before['text']+='.'
            elif change=='gap':later['rect'][1]=140
            elif change=='size':later['size']=13
            else:later['spans']=[]
            self.assertFalse(rules._region_inline_italic_continuation(later,before),change)
    def integrated(self,unknown_heading=False):
        before,later=self.fixture()
        if unknown_heading:
            later['text']='New methods and implementation'
            later['spans']=[self.span('New methods',bold=True),self.span(' and implementation')]
        heading=self.row('Acknowledgements',75,[self.span('Acknowledgements',bold=True)],block=1)
        heading['size']=12;heading['rect'][3]=87
        refs=self.row('References',150,[self.span('References',bold=True)],block=3)
        science=self.row('Scientific right-column discussion.',110,[self.span('Scientific right-column discussion.')],block=9);science['rect']=[310,110,545,121]
        profile={'confirmed':True,'source_family':'acl_emnlp','column_bands':[[50,286],[309,545]],'body_font':11,
                 'protected_regions':[],'layout_evidence':{}}
        return rules._propose_credit_regions({9:[heading,before,later,refs,science]}, {9:(612,792)},
            names=[],source_profiles={9:profile},rules_module=rules)
    def test_integrated_ack_ends_before_references_and_preserves_other_column(self):
        out=self.integrated();self.assertEqual(len(out['regions']),1,out)
        self.assertEqual(out['regions'][0]['rect'],[50,75,286,124])
        self.assertEqual(out['regions'][0]['end_evidence']['text'],'References')
    def test_unknown_scientific_heading_is_not_swallowed_by_ack(self):
        self.assertFalse(self.integrated(True)['regions'])


class RegionalProceedingsCitationPreservationTests(unittest.TestCase):
    def page(self,top=74,split=False):
        return [_region_test_line('[4] Daniel Borkan, Lucas Dixon, Jeffrey Sorensen, Nithum Thain, and Lucy Vasserman.',top,x=112,right=504,size=10,block=0),
                _region_test_line('Nuanced metrics for measuring unintended bias. In Companion',top+11,x=129,right=504,size=10,block=1 if split else 0),
                _region_test_line('proceedings of the 2019 world wide web conference, pages 491–500, 2019.',top+22,x=129,right=430,size=10,block=2 if split else 0),
                _region_test_line('[5] A different scientific citation.',top+38,x=112,right=504,size=10,block=3)]
    def plan(self,later,extra=()):
        first=[_region_test_line('A Scientific Paper',50,size=18),_region_test_line('Mira Chen',100),
               _region_test_line('Abstract',180),_region_test_line('Scientific body remains.',210)]
        pages=[first,later,*extra]
        return rules._propose_frontmatter_and_margins(pages,[(612,792)]*len(pages),paper={'title':'A Scientific Paper','authors':['Mira Chen']},graphics_by_page={i:[] for i in range(1,len(pages)+1)},rules_module=rules)
    def test_exact_source_top_reference_continuation_is_preserved(self):
        rows=self.page();out=self.plan(rows)
        self.assertFalse(any(x['page']==2 and rules._region_overlap(x['rect'],rows[2]['rect']) for x in out['regions']))
    def test_same_reference_layout_at_bottom_is_preserved(self):
        rows=self.page(top=732);out=self.plan(rows)
        self.assertFalse(any(x['page']==2 and rules._region_overlap(x['rect'],rows[2]['rect']) for x in out['regions']))
    def test_split_source_blocks_keep_numbered_reference_context(self):
        rows=self.page(split=True);self.assertTrue(rules._region_bibliographic_publication(rows,2))
    def test_author_year_title_and_pagination_context_is_preserved(self):
        rows=self.page();rows[0]['text']='Daniel Borkan et al. 2019.'
        self.assertTrue(rules._region_bibliographic_publication(rows,2))
    def test_actual_isolated_publication_footer_remains_eligible(self):
        rows=[_region_test_line('Scientific final paragraph.',700,x=60,right=280,size=10,block=1),
              _region_test_line('Proceedings of the 42nd International Conference on Machine Learning, PMLR 267, 2025.',748,x=60,right=290,size=9,block=2)]
        out=self.plan(rows);self.assertTrue(any(x['page']==2 and rules._region_contains(x['rect'],rows[-1]['rect']) for x in out['regions']))
    def test_unrelated_other_column_reference_is_not_notice_context(self):
        rows=self.page(split=True)
        rows[0]['rect']=[320,74,560,84];rows[-1]['rect']=[320,112,560,122]
        rows[2]['rect']=[60,96,280,106];rows[1]['block']=8
        self.assertFalse(rules._region_bibliographic_publication(rows,2))
    def test_real_recurring_header_remains_on_reference_pages(self):
        pages=[]
        for n in (2,3):pages.append([_region_test_line('A Scientific Paper',35,x=210,right=400,size=9,block=9),*self.page()])
        out=self.plan(pages[0],[pages[1]])
        for n in (2,3):
            self.assertTrue(any(x['page']==n and rules._region_overlap(x['rect'],pages[n-2][0]['rect']) for x in out['regions']))
            self.assertFalse(any(x['page']==n and rules._region_overlap(x['rect'],pages[n-2][3]['rect']) for x in out['regions']))

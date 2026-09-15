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
        self.assertTrue(any('column ambiguity' in reason for reason in plan['uncertain']))
        self.assertIn('Scientific theorem x = 42.', self.apply(plan))
        self.assertIn('We thank the agency.', self.apply(plan))

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
        self.assertEqual(rules.RULES_VERSION, 'anonymize-rules-6')

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
        self.assertIn('Code: https://github.com/alice/project', clean)
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


if __name__ == '__main__':
    unittest.main()

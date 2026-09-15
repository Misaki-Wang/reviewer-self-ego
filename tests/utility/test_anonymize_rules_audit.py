"""Independent synthetic residual-audit tests, with no model or network calls."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from utility.anonymize import anonymize_rules_audit as audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.candidate = self.root / "candidate.pdf"
        self.author = {"text": "Alice Example", "category": "author", "allow_reference": True}

    def pdf(self, path, pages, metadata=None):
        with pymupdf.open() as doc:
            for lines in pages:
                page = doc.new_page(width=612, height=792)
                for line in lines:
                    text, y, *font = line
                    page.insert_text((72, y), text, fontsize=font[0] if font else 11)
            doc.set_metadata(metadata or {})
            doc.save(path)

    def pair(self, original=None, candidate=None):
        original = original or [[("A Scientific Paper", 60), ("Alice Example", 90),
                                 ("Scientific result x = 42.", 200)]]
        candidate = candidate or [[("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]]
        self.pdf(self.source, original)
        self.pdf(self.candidate, candidate)

    def run_audit(self, terms=None):
        return audit.audit_pdf(self.source, self.candidate,
                               [self.author] if terms is None else terms)

    def test_deliberate_residual_is_found_by_both_parsers(self):
        pages = [[("A Scientific Paper", 60), ("Alice Example", 90),
                  ("Scientific result x = 42.", 200)]]
        self.pair(pages, pages)
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})
        self.assertEqual(report["reference_matches"], [])

    def test_clean_candidate_passes_bounded_checks_without_mutation(self):
        self.pair()
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.source, self.candidate)]
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertFalse(report["ready_for_blind_review"])
        self.assertTrue(report["requires_visual_review"])
        self.assertFalse(report["ocr"]["performed"])
        self.assertEqual(report["codex_cli_calls"], 0)
        self.assertEqual(report["model_api_calls"], 0)
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (self.source, self.candidate)])

    def test_no_external_process_or_network_is_used(self):
        self.pair()
        with patch("subprocess.Popen", side_effect=AssertionError("No external process allowed")), \
             patch("socket.create_connection", side_effect=AssertionError("No network allowed")):
            report = self.run_audit()
        self.assertTrue(report["passed"], report)

    def test_unicode_ligature_whitespace_and_dehyphenation(self):
        self.assertEqual(audit.normalize_text("  JÓSE\u00a0Ofﬁce\nEx-\nample\u200b  "),
                         "jose office example")
        self.assertEqual(audit.normalize_text("alice@\nexample.edu"), "alice@ example.edu")

    def test_wrapped_name_is_found(self):
        pages = [[("A Scientific Paper", 60), ("Alice", 90), ("Example", 105),
                  ("Scientific result x = 42.", 200)]]
        self.pair(pages, pages)
        report = self.run_audit()
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})

    def test_wrapped_email_and_punctuation_spaces_are_found(self):
        pages = [[("A Scientific Paper", 60), ("alice @", 90), ("example . edu", 105),
                  ("Scientific result x = 42.", 200)]]
        self.pair(pages, pages)
        report = self.run_audit([{"text": "alice@example.edu", "category": "email"}])
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})

    def test_dehyphenated_name_is_found(self):
        pages = [[("A Scientific Paper", 60), ("Alice Ex-", 90), ("ample", 105),
                  ("Scientific result x = 42.", 200)]]
        self.pair(pages, pages)
        report = self.run_audit()
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})

    def test_reference_exception_is_explicit(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Alice Example. A useful method. 2025.", 100)]
        self.pair([front + [("Alice Example", 90)], refs], [front, refs])
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["remaining_hits"], [])
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_reference_exception_does_not_hide_first_page_author(self):
        front = [("A Scientific Paper", 60), ("Alice Example", 90),
                 ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Alice Example. A useful method. 2025.", 100)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual({h["page"] for h in report["remaining_hits"]}, {1})
        self.assertEqual({h["page"] for h in report["reference_matches"]}, {2})

    def test_contact_footnote_never_uses_reference_exception(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] A different author. Useful method. 2025.", 100),
                ("Corresponding author: Alice Example", 760, 8)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(report["remaining_hits"])
        self.assertEqual(report["reference_matches"], [])
        self.assertTrue(any(h["reference_classification_ambiguous"] for h in report["remaining_hits"]))

    def test_plain_tiny_footnote_is_conservative(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Different authors. A useful method. 2025.", 100),
                ("Alice Example", 760, 7)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(h["parser"] == "pymupdf" for h in report["remaining_hits"]))

    def test_require_absent_everywhere_overrides_reference_allowance(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Alice Example. A useful method. 2025.", 100)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit([{**self.author, "require_absent_everywhere": True}])
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])

    def test_owned_contact_is_not_exempted_even_if_allow_reference(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Contact alice@example.edu. A useful method. 2025.", 100)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit([{"text": "alice@example.edu", "category": "email",
                                 "allow_reference": True}])
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])

    def test_candidate_cannot_create_its_own_reference_exception(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        for body in ("Alice Example describes this work.",
                     "[1] Alice Example. A plausible citation, 2025."):
            with self.subTest(candidate_body=body):
                original = [("Discussion", 60), (body, 100)]
                candidate = [("References", 60), (body, 100)]
                self.pair([front, original], [front, candidate])
                report = self.run_audit()
                self.assertFalse(report["passed"])
                self.assertEqual(report["reference_matches"], [])
                self.assertEqual({h["parser"] for h in report["remaining_hits"]},
                                 {"pymupdf", "pypdf"})
                self.assertTrue(any(x["reason"] == "candidate_reference_not_supported_by_original"
                                    for x in report["unresolved"]))

    def test_first_page_reference_heading_is_ambiguous(self):
        page = [("References", 60), ("[1] Alice Example. A useful method. 2025.", 100)]
        self.pair([page], [page])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])

    def test_appendix_ends_original_reference_section(self):
        front = [("A Scientific Paper", 60), ("Scientific result x = 42.", 200)]
        refs = [("References", 60), ("[1] Alice Example. A useful method. 2025.", 100)]
        appendix = [("Appendix A", 60), ("Alice Example performed these experiments.", 100)]
        self.pair([front, refs, appendix], [front, refs, appendix])
        report = self.run_audit()
        self.assertEqual({h["page"] for h in report["reference_matches"]}, {2})
        self.assertEqual({h["page"] for h in report["remaining_hits"]}, {3})

    def test_own_arxiv_id_includes_attached_version(self):
        pages = [[("A Scientific Paper", 60), ("arXiv:2502.15805v4", 90),
                  ("Scientific result x = 42.", 200)]]
        self.pair(pages, pages)
        report = self.run_audit([{"text": "2502.15805", "category": "arxiv_id"}])
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})
        self.assertTrue(all(h["matched_text"].endswith("v4") for h in report["remaining_hits"]))

    def test_metadata_vocabulary(self):
        self.pair()
        report = audit.audit(self.source, self.candidate, source_metadata={
            "authors": [{"given": "Alice", "family": "Example"}],
            "affiliations": ["Example University"], "arxiv_id": "2502.15805",
        })
        self.assertTrue(report["passed"], report)
        self.assertEqual({x["category"] for x in report["identity_vocabulary"]},
                         {"author", "affiliation", "arxiv_id"})

    def test_candidate_metadata_requires_review(self):
        self.pdf(self.source, [[("Scientific text", 60)]])
        self.pdf(self.candidate, [[("Scientific text", 60)]], {"author": "Alice Example"})
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["structure"]["candidate"]["metadata_fields"]["author"], "Alice Example")

    def test_xmp_attachments_annotations_links_and_forms_are_reported(self):
        self.pdf(self.source, [[("Scientific text", 60)]])
        with pymupdf.open(self.source) as doc:
            doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'>Alice Example</x:xmpmeta>")
            doc.embfile_add("author.txt", b"Alice Example")
            page = doc[0]
            page.add_text_annot((100, 100), "Alice Example")
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(100, 150, 200, 170),
                              "uri": "https://alice.example.edu"})
            widget = pymupdf.Widget()
            widget.field_name = "author"
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.field_value = "Alice Example"
            widget.rect = pymupdf.Rect(100, 200, 300, 230)
            page.add_widget(widget)
            doc.save(self.candidate)
        report = self.run_audit()
        self.assertFalse(report["passed"])
        structure = report["structure"]["candidate"]
        self.assertTrue(structure["xmp_present"])
        for field in ("attachment_count", "annotation_count", "link_count", "form_field_count"):
            self.assertGreater(structure[field], 0)

    def test_blank_or_unparsed_page_is_unresolved(self):
        self.pair()
        original_read = audit._read_pypdf

        def read(path):
            if Path(path) == self.candidate:
                return [audit._page_record("")], None
            return original_read(path)

        with patch.object(audit, "_read_pypdf", side_effect=read):
            report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(x["reason"] == "page_extraction_incomplete" and x["parser"] == "pypdf"
                            for x in report["unresolved"]))

    def test_parser_exception_is_unresolved(self):
        self.pair()
        with patch("pypdf._page.PageObject.extract_text", side_effect=ValueError("broken stream")):
            report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(x.get("status") == "error" for x in report["unresolved"]))

    def test_physical_page_count_mismatch_is_unresolved(self):
        self.pair(candidate=[[("Scientific text", 60)], [("Extra scientific text", 60)]])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(x["reason"] == "page_count_mismatch" for x in report["unresolved"]))

    def test_parser_page_count_disagreement_is_unresolved(self):
        self.pair()
        original_read = audit._read_pypdf

        def read(path):
            pages, error = original_read(path)
            return pages + [audit._page_record("Extra extracted page")], error

        with patch.object(audit, "_read_pypdf", side_effect=read):
            report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(x["reason"] == "parser_page_count_disagreement"
                            for x in report["unresolved"]))

    def test_no_pages_is_unresolved(self):
        self.pair()
        with patch.object(audit, "_read_pypdf", return_value=([], None)):
            report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertTrue(any(x["reason"] == "no_pages_extracted" for x in report["unresolved"]))

    def test_author_initial_lines_do_not_end_numbered_references(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60),
                ("[4] Long author list continues on later lines.", 90),
                ("H. Zhang, Han Bao, Hanwei Xu, Haocheng Wang, Honghui Ding, Hui", 105),
                ("[12] Hanlin Tang, Yang Lin, Alice Example. RazorAttention, 2024.", 150)]
        continuation = [("[21] Alice Example. ClusterKV, 2024.", 90)]
        self.pair([front, refs, continuation], [front, refs, continuation])
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual({h["page"] for h in report["reference_matches"]}, {2, 3})
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_split_editor_initials_do_not_end_reference_continuation(self):
        front = [("Scientific text", 60)]
        refs = [("REFERENCES", 60), ("Other authors. Conference proceedings, 2024.", 90),
                ("L. Mackey,", 110), ("D. Bel-", 125), ("S. Mohamed,", 140),
                ("A. Agarwal,", 155), ("K.", 170), ("A.", 185)]
        continuation = [("Alice Example. A useful scientific method, 2025.", 90)]
        appendix = [("A", 60), ("RELATED WORK", 75),
                    ("Alice Example conducted these experiments.", 110)]
        self.pair([front, refs, continuation, appendix], [front, refs, continuation, appendix])
        report = self.run_audit()
        self.assertEqual({h["page"] for h in report["reference_matches"]}, {3})
        self.assertEqual({h["page"] for h in report["remaining_hits"]}, {4})

    def test_smallcaps_reference_heading(self):
        front = [("Scientific text", 60)]
        refs = [("R E F E R E N C E S", 60), ("Alice Example. A useful method, 2025.", 100)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_wrapped_reference_heading(self):
        front = [("Scientific text", 60)]
        refs = [("R", 60), ("EFERENCES", 75), ("Alice Example. A useful method, 2025.", 110)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_removing_previous_header_does_not_reuse_source_text_offsets(self):
        front = [("Scientific text", 60)]
        header = [("Long institutional header removed from candidate " * 2, 30)]
        refs = [("REFERENCES", 60), ("Alice Example. A useful method, 2025.", 100)]
        self.pair([front, header + refs], [front, refs])
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_two_column_wrapped_reference_entries(self):
        for path in (self.source, self.candidate):
            with pymupdf.open() as doc:
                doc.new_page().insert_text((72, 60), "Scientific text")
                page = doc.new_page(width=612, height=792)
                page.insert_text((72, 60), "REFERENCES", fontsize=12)
                page.insert_text((72, 100), "[1] Other author.", fontsize=9)
                page.insert_text((72, 112), "Proceedings, 2024.", fontsize=9)
                page.insert_text((330, 100), "[12] Alice", fontsize=9)
                page.insert_text((330, 112), "Example. RazorAttention, 2024.", fontsize=9)
                doc.save(path)
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertEqual({h["parser"] for h in report["reference_matches"]}, {"pymupdf", "pypdf"})

    def test_unknown_section_title_does_not_silently_extend_reference_exception(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Other authors. A useful method, 2025.", 100)]
        ambiguous = [("A Unusual Heading", 60), ("Alice Example worked on this project.", 100)]
        self.pair([front, refs, ambiguous], [front, refs, ambiguous])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])
        self.assertTrue(all(h["reference_classification_ambiguous"] for h in report["remaining_hits"]))

    def test_unheaded_figure_example_is_not_a_reference(self):
        term = {"text": "University of Oxford", "category": "affiliation", "allow_reference": True}
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Alice Example. A useful method, 2024.", 100)]
        example = [("Resulting stegotext from the scientific experiment", 60),
                   ("The University of Oxford is a collection of 38 colleges.", 100),
                   ("Domain: Romanesco Dialect (Trilussa, 1909)", 130),
                   ("Figure 7: Generated scientific examples.", 700)]
        self.pair([front, refs, example], [front, refs, example])
        report = self.run_audit([term])
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})
        self.assertTrue(all(h["reference_classification_ambiguous"] for h in report["remaining_hits"]))

    def test_unheaded_prose_does_not_borrow_previous_citation_year(self):
        term = {"text": "University of Oxford", "category": "affiliation", "allow_reference": True}
        front = [("Scientific text", 60)]
        refs_and_prose = [("References", 60), ("Alice Example. A useful method, 2024.", 100),
                          ("The University of Oxford is used in this generated example.", 180),
                          ("The generated text describes a fictional year 2025.", 200)]
        self.pair([front, refs_and_prose], [front, refs_and_prose])
        report = self.run_audit([term])
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])

    def test_unnumbered_results_heading_requires_review(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Other authors. A useful method, 2024.", 100)]
        results = [("Results", 60), ("Alice Example. Example text dated 2025.", 100)]
        self.pair([front, refs, results], [front, refs, results])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])
        self.assertTrue(all(h["reference_classification_ambiguous"] for h in report["remaining_hits"]))

    def test_decimal_appendix_label_ends_bibliography_with_sentence_case_title(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Other authors. A useful method, 2024.", 100)]
        appendix = [("A.2", 60), ("Impact of LLM model quality.", 75),
                    ("Alice Example evaluated this model.", 110)]
        self.pair([front, refs, appendix], [front, refs, appendix])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])
        self.assertEqual({h["parser"] for h in report["remaining_hits"]}, {"pymupdf", "pypdf"})

    def test_citation_shaped_text_inside_figure_page_needs_review(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Other authors. A useful method, 2024.", 100)]
        figure = [("Alice Example. Synthetic citation-like text, 2025.", 100),
                  ("Figure 7: Synthetic scientific examples.", 700)]
        self.pair([front, refs, figure], [front, refs, figure])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])

    def test_reference_without_local_publication_evidence_stays_ambiguous(self):
        front = [("Scientific text", 60)]
        refs = [("References", 60), ("Alice Example is mentioned without a citation.", 100)]
        self.pair([front, refs], [front, refs])
        report = self.run_audit()
        self.assertFalse(report["passed"])
        self.assertEqual(report["reference_matches"], [])
        self.assertTrue(all(h["reference_classification_ambiguous"] for h in report["remaining_hits"]))

    def test_no_oracle_is_not_a_pass(self):
        self.pair()
        report = self.run_audit([])
        self.assertFalse(report["passed"])
        self.assertTrue(any(x["reason"] == "no_independent_identity_vocabulary"
                            for x in report["unresolved"]))

    def test_graphics_are_counted_without_claiming_image_text_absent(self):
        self.pdf(self.source, [[("Scientific text", 60)]])
        with pymupdf.open(self.source) as doc:
            doc[0].draw_rect(pymupdf.Rect(100, 100, 200, 200))
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10))
            pix.clear_with(120)
            doc[0].insert_image(pymupdf.Rect(210, 100, 220, 110), pixmap=pix)
            doc.save(self.candidate)
        report = self.run_audit()
        self.assertTrue(report["passed"], report)
        self.assertGreater(report["graphics"][0]["image_placements"], 0)
        self.assertGreater(report["graphics"][0]["vector_drawing_objects"], 0)
        self.assertTrue(report["requires_visual_review"])
        self.assertFalse(report["ready_for_blind_review"])


if __name__ == "__main__":
    unittest.main()

"""Rules-only candidate driver tests; no model or subprocess invocation."""
from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
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
            return driver.anonymize(self.source, self.paper, self.output, self.report, scope='identifiers')

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

    def test_rotated_identity_bounds_do_not_remove_neighboring_science(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((25, 300), 'arXiv:2501.12345v1', fontsize=20, rotate=90)
            page.insert_text((30, 260), 'Fig. 1: Scientific result', fontsize=10)
            page.insert_text((30, 275), 'optional contextual photo', fontsize=10)
            page.insert_text((30, 290), 'for editing at full resolution', fontsize=10)
            rect = page.search_for('arXiv:2501.12345v1')[0]
            document.save(self.source)
        plan = {'redactions': [{'page': 1, 'rect': list(rect), 'category': 'identifier',
                                'text': 'arXiv:2501.12345v1'}],
                'forbidden_strings': ['arXiv:2501.12345v1'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            text = document[0].get_text()
        self.assertIn('Fig. 1: Scientific result', text)
        self.assertIn('optional contextual photo', text)
        self.assertIn('for editing at full resolution', text)
        self.assertNotIn('arXiv:', text)

    def test_identity_text_over_raster_and_vector_does_not_erase_science(self):
        with fitz.open() as document:
            page = document.new_page()
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 80))
            pix.clear_with(150)
            page.insert_image(fitz.Rect(60, 50, 180, 170), pixmap=pix)
            page.draw_rect(fitz.Rect(60, 50, 180, 170), color=(1, 0, 0), fill=None)
            page.draw_line((60, 100), (180, 100), color=(0, 0, 1))
            page.insert_text((72, 100), 'Alice Example')
            rect = page.search_for('Alice Example')[0]
            document.save(self.source)
        plan = {'redactions': [{'page': 1, 'rect': list(rect), 'category': 'author',
                                'text': 'Alice Example'}],
                'forbidden_strings': ['Alice Example'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.source) as before, fitz.open(self.output) as after:
            self.assertEqual(len(before[0].get_drawings()), len(after[0].get_drawings()))
            original_image = before.extract_image(before[0].get_images()[0][0])
            clean_image = after.extract_image(after[0].get_images()[0][0])
            self.assertEqual(original_image['image'], clean_image['image'])
            # Pixel directly beneath the removed byline keeps the raster color.
            pix = after[0].get_pixmap()
            self.assertEqual(pix.pixel(80, 94), (150, 150, 150))

    def test_hidden_text_cleanup_preserves_fully_covered_blue_vector_marks(self):
        with fitz.open() as document:
            page = document.new_page()
            page.draw_rect(fitz.Rect(78, 92, 84, 98), color=(0, 0, 1), fill=(0, 0, 1))
            page.insert_text((72, 100), 'HIDDEN_PRIVATE_PAYLOAD', fontsize=12, render_mode=3)
            document.save(self.source)
        plan = {'redactions': [], 'forbidden_strings': ['HIDDEN_PRIVATE_PAYLOAD'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            self.assertEqual(document[0].get_pixmap().pixel(81, 95), (0, 0, 255))
            self.assertFalse(any(kind == 'ignore-text' for kind, _ in document[0].get_bboxlog()))

    def test_hidden_identity_target_is_preserved_until_its_planned_redaction(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 100), 'Alice Example', render_mode=3)
            page.insert_text((72, 160), 'Scientific result remains.')
            rect = list(page.search_for('Alice Example')[0])
            document.save(self.source)
        plan = {'redactions': [{'page': 1, 'rect': rect, 'category': 'author',
                                'text': 'Alice Example'}],
                'forbidden_strings': ['Alice Example'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            self.assertIn('Scientific result remains.', document[0].get_text())
            self.assertNotIn('Alice Example', document[0].get_text())

    def make_ligature_source(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 100), 'modifed identity')
            page.insert_text((72, 160), 'Result equals 42.')
            font = page.get_fonts()[0][0]
            mapping = [
                f'<{code:02x}> <{("00660069" if code == 102 else f"{code:04x}")}>'
                for code in range(32, 127)]
            cmap = ('/CIDInit /ProcSet findresource begin 12 dict begin begincmap '
                    '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def '
                    '/CMapName /TestLigature def /CMapType 2 def '
                    '1 begincodespacerange <00> <ff> endcodespacerange '
                    '95 beginbfchar ' + ' '.join(mapping) + ' endbfchar '
                    'endcmap CMapName currentdict /CMap defineresource pop end end')
            xref = document.get_new_xref()
            document.update_object(xref, '<<>>')
            document.update_stream(xref, cmap.encode())
            document.xref_set_key(font, 'ToUnicode', f'{xref} 0 R')
            document.save(self.source)
        with fitz.open(self.source) as document:
            page = document[0]
            self.assertIn('modified identity', page.get_text())
            self.assertTrue(any(fitz.Rect(c['bbox']).is_empty
                                for line in driver.pdf._text_lines(page) for c in line
                                if c['c'].strip()))
            return list(page.search_for('modified identity')[0])

    def test_complete_ligature_with_zero_width_continuation_is_redacted(self):
        rect = self.make_ligature_source()
        plan = {'redactions': [{'page': 1, 'rect': rect, 'category': 'other_identity',
                                'text': 'modified identity'}],
                'forbidden_strings': ['modified identity'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            self.assertIn('Result equals 42.', document[0].get_text())
            self.assertNotIn('modified identity', document[0].get_text())

    def test_partial_ligature_redaction_cannot_remove_unselected_text(self):
        rect = self.make_ligature_source()
        plan = {'redactions': [{'page': 1, 'rect': rect, 'category': 'other_identity',
                                'text': 'modif'}],
                'forbidden_strings': [], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'failed_validation')
        self.assertFalse(self.output.exists())

    def test_zero_height_hidden_payload_is_removed_without_changing_visible_labels(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 220), 'GE Positive Moment remains', fontsize=12)
            for index in range(36):
                point = fitz.Point(72.137 + .731 * index, 95.329 + 2.731 * index)
                scale = (index % 3 + 2) * .0000001
                page.insert_text(point, 'HIDDEN_PAYLOAD' * 40, fontsize=1, render_mode=3,
                                 morph=(point, fitz.Matrix(scale, scale)))
            document.save(self.source)
        with fitz.open(self.source) as document:
            self.assertTrue(any(kind == 'ignore-text' for kind, _ in document[0].get_bboxlog()))
        plan = {'redactions': [], 'forbidden_strings': ['HIDDEN_PAYLOAD'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            self.assertIn('GE Positive Moment remains', document[0].get_text())
            self.assertFalse(any(kind == 'ignore-text' for kind, _ in document[0].get_bboxlog()))

    def test_inseparable_overprinted_identity_fails_closed(self):
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 100), 'Alice', fontname='cour', fontsize=12)
            page.insert_text((72, 100), 'Other', fontname='cour', fontsize=12)
            rect = page.search_for('Alice')[0]
            document.save(self.source)
        plan = {'redactions': [{'page': 1, 'rect': list(rect), 'category': 'author',
                                'text': 'Alice'}],
                'forbidden_strings': ['Alice'], 'uncertain': []}
        result = self.execute(plan)
        self.assertEqual(result['status'], 'failed_validation')
        self.assertFalse(self.output.exists())
        self.assertIn('inseparable', result['error'].lower())

    def test_text_only_mode_is_bound_to_cache_receipt(self):
        result = self.execute()
        self.assertEqual(result['binding']['redaction_mode'], driver.REDACTION_MODE)
        self.assertEqual(result['binding']['validation_mode'], driver.VALIDATION_MODE)

    def test_unused_private_streams_are_removed_without_resource_deduplication(self):
        secret = b'UNREFERENCED_PRIVATE_IDENTIFIER'
        with fitz.open(self.source) as document:
            xref = document.get_new_xref()
            document.update_object(xref, '<<>>')
            document.update_stream(xref, secret)
            data = document.tobytes(garbage=0)
        self.source.write_bytes(data)
        result = self.execute()
        self.assertEqual(result['status'], 'rules_passed', result.get('error'))
        with fitz.open(self.output) as document:
            for xref in range(1, document.xref_length()):
                self.assertNotIn(secret, document.xref_stream(xref) or b'')

    def test_unmatched_text_plan_fails_without_publishing(self):
        plan = deepcopy(self.plan)
        plan['redactions'][0]['text'] = 'Someone not present'
        result = self.execute(plan)
        self.assertEqual(result['status'], 'failed_validation')
        self.assertFalse(self.output.exists())
        self.assertIn('match', result['error'].lower())

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
        def validate_then_create(*args, **kwargs):
            result = original_check(*args, **kwargs)
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


class DirectTextTests(unittest.TestCase):
    def fixture(self):
        doc = fitz.open()
        self.addCleanup(doc.close)
        page = doc.new_page()
        page.insert_text((72, 100), 'Private author')
        page.insert_text((72, 160), 'Scientific result remains.')
        with fitz.open() as figure:
            fp = figure.new_page(width=150, height=80)
            fp.draw_rect(fitz.Rect(10, 10, 140, 70), color=(0, 0, 1), fill=(0, 0, 1))
            fp.insert_text((15, 45), 'Scientific figure', color=(1, 1, 1))
            page.show_pdf_page(fitz.Rect(72, 200, 222, 280), figure, 0)
        item = {'page': 1, 'rect': list(page.search_for('Private author')[0]),
                'category': 'author', 'text': 'Private author'}
        return doc, page, item

    def test_complete_show_preserves_scientific_form_bytes(self):
        doc, page, item = self.fixture()
        forms = {row[0]: doc.xref_stream(row[0]) for row in page.get_xobjects()}
        _, expected = driver.pdf._text_only_rectangles(page, [item])
        self.assertTrue(driver.pdf._try_direct_text_redaction(page, [item], expected))
        self.assertNotIn('Private author', page.get_text())
        self.assertIn('Scientific result remains.', page.get_text())
        self.assertIn('Scientific figure', page.get_text())
        self.assertEqual(forms, {row[0]: doc.xref_stream(row[0]) for row in page.get_xobjects()})

    def test_ambiguous_repeated_show_falls_back_without_mutation(self):
        doc, page, item = self.fixture()
        page.insert_text((72, 330), 'Private author')
        original = [doc.xref_stream(ref) for ref in page.get_contents()]
        _, expected = driver.pdf._text_only_rectangles(page, [item])
        self.assertFalse(driver.pdf._try_direct_text_redaction(page, [item], expected))
        self.assertEqual(original, [doc.xref_stream(ref) for ref in page.get_contents()])

    def test_failed_glyph_check_rolls_back_stream_references(self):
        doc, page, item = self.fixture()
        original = page.get_contents()
        self.assertFalse(driver.pdf._try_direct_text_redaction(page, [item], 'invalid expected glyphs'))
        self.assertEqual(page.get_contents(), original)
        self.assertIn('Private author', page.get_text())

    def test_following_show_without_position_reset_falls_back(self):
        doc = fitz.open()
        self.addCleanup(doc.close)
        page = doc.new_page()
        page.insert_text((72, 100), 'Private author')
        ref = page.get_contents()[0]
        data = b'BT /helv 11 Tf 72 700 Td (Private author) Tj ( Science follows) Tj ET'
        doc.update_stream(ref, data)
        item = {'page': 1, 'rect': list(page.search_for('Private author')[0]),
                'category': 'author', 'text': 'Private author'}
        _, expected = driver.pdf._text_only_rectangles(page, [item])
        self.assertFalse(driver.pdf._try_direct_text_redaction(page, [item], expected))
        self.assertEqual(doc.xref_stream(ref), data)


    def test_complete_line_split_across_font_shows_preserves_all_other_bytes(self):
        doc, page, item = self.fixture()
        ref = page.get_contents()[0]
        data = b'BT /helv 11 Tf 72 700 Td (Private ) Tj /helv 11 Tf (author) Tj ET'
        doc.update_stream(ref, data)
        item['rect'] = list(page.search_for('Private author')[0])
        _, expected = driver.pdf._text_only_rectangles(page, [item])
        self.assertTrue(driver.pdf._try_direct_text_redaction(page, [item], expected))
        changed = doc.xref_stream(page.get_contents()[0])
        self.assertEqual(changed, data.replace(b'(Private )', b'()').replace(b'(author)', b'()'))
        self.assertIn('Scientific figure', page.get_text())


class HiddenStreamTests(unittest.TestCase):
    def test_only_hidden_payload_bytes_and_accessibility_metadata_change(self):
        prefix = b'q 1.234567891 0 0 1 12.34567891 9 cm 0 0 1 rg 0 0 4 4 re f\n'
        hidden = (b'BT 3 Tr /F1 10 Tf /Span << /MCID 7 /Alt (private owner) '
                  b'/ActualText (private owner) >> BDC '
                  b'[(private) 17 <6f776e6572>] TJ (ET inside a string) Tj EMC ET\n')
        suffix = b'BT 0 Tr /F1 9.87654321 Tf (Scientific result 42) Tj ET Q'
        cleaned, edits = driver.pdf._strip_hidden_text_objects(prefix + hidden + suffix)
        self.assertEqual(edits, 3)
        self.assertTrue(cleaned.startswith(prefix))
        self.assertTrue(cleaned.endswith(suffix))
        self.assertNotIn(b'private', cleaned)
        self.assertNotIn(b'ET inside a string', cleaned)
        self.assertIn(b'/MCID 7', cleaned)

    def test_visible_mixed_clipping_and_unsupported_objects_are_unchanged(self):
        cases = [
            b'BT 0 Tr (Visible) Tj ET',
            b'BT 3 Tr (Hidden) Tj 0 Tr (Visible) Tj ET',
            b'BT 7 Tr (Scientific clipping stencil) Tj ET',
            b'BT 3 Tr q (Unknown restored state) Tj Q ET',
            b'BT 3 Tr (Unterminated) Tj',
            b'BT 3 Tr 42 (Malformed show) Tj ET',
            b'BI /W 1 /H 1 /BPC 8 /CS /RGB ID abc EI BT 3 Tr (Private) Tj ET',
        ]
        for data in cases:
            with self.subTest(data=data):
                self.assertEqual(driver.pdf._strip_hidden_text_objects(data), (data, 0))

    def test_hidden_quote_operators_keep_spacing_and_line_state(self):
        data = b"BT 3 Tr /F1 10 Tf 12 TL (private) ' 2.1234567 3.7654321 (private) \" ET"
        cleaned, edits = driver.pdf._strip_hidden_text_objects(data)
        self.assertEqual(edits, 2)
        self.assertEqual(cleaned, b"BT 3 Tr /F1 10 Tf 12 TL () ' 2.1234567 3.7654321 () \" ET")


class TextRoundingCertificateTests(unittest.TestCase):
    def page(self, *, shift=0., font_data=b'embedded-font', glyph=28, unicode=99,
             color=(0., 0., 0.), nontext=False):
        box = (100. + shift, 90., 106. + shift, 103.)
        span = {'font': 'StableFont', 'type': 0, 'opacity': 1., 'color': color,
                'size': 10., 'dir': (1., 0.), 'bbox': box, 'seqno': 1,
                'chars': [(unicode, glyph, (100. + shift, 100.), box)]}
        page = SimpleNamespace()
        page.parent = SimpleNamespace(extract_font=lambda _: ('font', 'cff', 'Type1', font_data))
        page.get_fonts = lambda full: [(1, 'cff', 'Type1', 'ABCDEF+StableFont', 'F1')]
        page.get_texttrace = lambda: [span]
        page.get_bboxlog = lambda: [('fill-path' if nontext else 'fill-text', box)]
        return page

    def test_evidence_requires_identical_glyph_style_and_embedded_font(self):
        region = fitz.Rect(99, 89, 107, 104)
        evidence = driver.pdf._text_rounding_evidence(self.page(), self.page(shift=.00005), region)
        self.assertIsNotNone(evidence)
        for changed in [self.page(glyph=29), self.page(unicode=100),
                        self.page(color=(1., 0., 0.)), self.page(font_data=b'another-font'),
                        self.page(font_data=b''), self.page(shift=.001), self.page(nontext=True)]:
            with self.subTest(changed=changed):
                self.assertIsNone(driver.pdf._text_rounding_evidence(self.page(), changed, region))

    def test_reordered_glyphs_are_rejected(self):
        before, after = self.page(), self.page()
        first = before.get_texttrace()[0]
        first['chars'].append((105, 34, (104., 100.), (104., 90., 107., 103.)))
        after.get_texttrace()[0]['chars'][:] = list(reversed(first['chars']))
        self.assertIsNone(driver.pdf._text_rounding_evidence(before, after, fitz.Rect(99, 89, 108, 104)))

    def pair(self, root):
        font = fitz.Font('tiro').buffer
        paths = [root / 'source.pdf', root / 'candidate.pdf']
        for path, shift in zip(paths, (0., 1. / 16384)):
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_font(fontname='Embedded', fontbuffer=font)
                page.insert_text((72.583282470703125 + shift, 100.86102294921875),
                                 'ci', fontsize=8.9664, fontname='Embedded')
                doc.save(path, garbage=2, no_new_id=True)
        return paths

    def test_default_validation_remains_strict_and_opt_in_records_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, candidate = self.pair(root)
            plan = {'redactions': [], 'forbidden_strings': [], 'uncertain': []}
            strict = root / 'strict'
            strict.mkdir()
            with self.assertRaisesRegex(ValueError, 'Visible content changed'):
                driver.pdf.check_pdf(source, candidate, plan, strict)
            allowed = root / 'allowed'
            allowed.mkdir()
            result = driver.pdf.check_pdf(source, candidate, plan, allowed, allow_text_rounding=True)
            self.assertEqual(len(result['text_rounding_certificates']), 1)
            certificate = result['text_rounding_certificates'][0]
            self.assertTrue(certificate['original_108dpi_mismatch'])
            self.assertEqual(len(certificate['independent_render_checks']), 4)
            self.assertTrue(all(check['exact_outside_mask'] for check in certificate['independent_render_checks']))

    def test_alternate_render_disagreement_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, candidate = self.pair(root)
            plan = {'redactions': [], 'forbidden_strings': [], 'uncertain': []}
            work = root / 'qa'
            work.mkdir()
            original = driver.pdf.render_page

            def render(page, scale=1.5):
                pix = original(page, scale)
                if scale == 4. and Path(page.parent.name) == candidate:
                    pix.set_pixel(1, 1, (0, 0, 0))
                return pix

            with patch.object(driver.pdf, 'render_page', side_effect=render):
                with self.assertRaisesRegex(ValueError, 'Visible content changed'):
                    driver.pdf.check_pdf(source, candidate, plan, work, allow_text_rounding=True)


class FormPreservationTests(unittest.TestCase):
    def test_reference_lexer_ignores_pdf_strings_hex_and_names(self):
        data = b'<< /Ref 12 0 R /Text (45 0 R \\(inner\\)) /Hex <313220302052> /Name /99 /A [23 0 R] >>'
        self.assertEqual([r[2] for r in driver.pdf._object_references(data)], [12, 23])

    def test_shared_form_copy_keeps_masked_and_unmasked_placements_separate(self):
        with fitz.open() as art, fitz.open() as doc:
            p = art.new_page(width=200, height=100)
            p.insert_text((10, 20), 'Alice Example')
            p.draw_rect((5, 35, 195, 95), color=(0, 0, 1), fill=(.2, .4, .8))
            p.insert_text((10, 70), 'Scientific figure', color=(1, 1, 1))
            page = doc.new_page()
            page.show_pdf_page(fitz.Rect(40, 40, 240, 140), art, 0)
            page.show_pdf_page(fitz.Rect(40, 220, 240, 320), art, 0)
            first, second = page.search_for('Alice Example')
            snapshot = driver.pdf._snapshot_unaffected_forms(page, [first])
            self.assertTrue(any('objects' in item for item in snapshot))
            page.add_redact_annot(first, fill=False)
            page.apply_redactions(images=0, graphics=0, text=0)
            self.assertGreater(driver.pdf._restore_unaffected_forms(page, snapshot), 0)
            self.assertEqual(len(page.search_for('Alice Example')), 1)
            self.assertEqual(page.search_for('Alice Example')[0], second)
            self.assertEqual(page.get_text().count('Scientific figure'), 2)

    def test_closure_clone_keeps_exact_image_stream_and_numeric_form_bytes(self):
        with fitz.open() as art, fitz.open() as doc:
            p = art.new_page(width=200, height=100)
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((10, 20, 230)) * 25, False)
            p.insert_image(fitz.Rect(20.123456, 20, 120, 80), pixmap=pix)
            p.insert_text((5, 15), 'Scientific diagram')
            page = doc.new_page()
            page.show_pdf_page(fitz.Rect(40, 180, 240, 280), art, 0)
            page.insert_text((40, 60), 'Identity')
            before = driver.pdf.render_page(page)
            mask = page.search_for('Identity')[0]
            snapshot = driver.pdf._snapshot_unaffected_forms(page, [mask])
            page.add_redact_annot(mask, fill=False)
            page.apply_redactions(images=0, graphics=0, text=0)
            self.assertGreater(driver.pdf._restore_unaffected_forms(page, snapshot), 0)
            for original in snapshot:
                if 'objects' not in original:
                    continue
                clones = driver.pdf._snapshot_object_closure(doc, driver.pdf._form_calls(page)[0]['xref'])
                self.assertEqual(sorted(s for _, s in original['objects'].values() if s is not None),
                                 sorted(s for _, s in clones.values() if s is not None))
            self.assertIn('Scientific diagram', page.get_text())
            # The image and vector region must render exactly, not merely keep xrefs.
            p_before = before
            after = driver.pdf.render_page(page)
            y = int(180 * 1.5)
            self.assertEqual(p_before.samples[y * p_before.stride:], after.samples[y * after.stride:])


class WordSegmentationCertificateTests(TextRoundingCertificateTests):
    def test_retained_period_word_split_uses_protected_glyphs_only_in_rules_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, candidate = self.pair(root)
            url = 'https://example.org/code'
            rect = None
            for path in (source, candidate):
                with fitz.open(path) as doc:
                    page = doc[0]
                    if path == source:
                        page.insert_text((72, 200), url)
                        rect = list(page.search_for(url)[0])
                    page.insert_text((72 + fitz.get_text_length(url), 200), '.')
                    doc.saveIncr()
            plan = {'redactions': [{'page': 1, 'rect': rect, 'category': 'author_link', 'text': url}],
                    'forbidden_strings': [], 'uncertain': []}
            work = root / 'qa'
            work.mkdir()
            result = driver.pdf.check_pdf(source, candidate, plan, work, allow_text_rounding=True)
            self.assertEqual(len(result['text_rounding_certificates']), 1)
            checks = result['text_rounding_certificates'][0]['independent_render_checks']
            self.assertEqual(len(checks), 4)
            self.assertTrue(all(x['exact_outside_mask'] for x in checks))


class ImageOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.pdf'
        self.rect = [60., 60., 110., 110.]
        with fitz.open() as doc:
            page = doc.new_page()
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((10, 20, 230)) * 25, False)
            ref = page.insert_image(self.rect, pixmap=pix)
            page.insert_image(fitz.Rect(160, 60, 210, 110), xref=ref)
            page.draw_line((60, 90), (110, 90), color=(1, 0, 0))
            page.insert_text((65, 80), 'SCI', fontsize=8)
            page.insert_text((60, 140), 'Scientific body remains.')
            doc.save(self.source)
        with fitz.open(self.source) as doc:
            item = driver.pdf._image_inventory(doc[0])[0]
            self.operation = {'page': 1, 'rect': self.rect, 'source_xref': item['xref'],
                              'image_sha256': item['image_sha256'],
                              'source_pdf_sha256': driver.pdf.digest(self.source),
                              'category': 'affiliation', 'reason': 'bound source-affiliation OCR',
                              'evidence_sha256': 'a' * 64}

    def test_one_repeated_placement_only_preserves_text_and_vector_background(self):
        with fitz.open(self.source) as doc:
            page = doc[0]
            before_text = page.get_text()
            before_second = page.get_pixmap(clip=fitz.Rect(160, 60, 210, 110)).samples
            guards = driver.pdf._validate_image_operations(doc, self.source, [self.operation])
            driver.pdf._erase_image_operations(page, guards)
            self.assertEqual(page.get_text(), before_text)
            self.assertEqual(len(page.get_cdrawings()), 1)
            remaining = driver.pdf._image_inventory(page)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0]['rect'], (160., 60., 210., 110.))
            self.assertEqual(page.get_pixmap(clip=fitz.Rect(160, 60, 210, 110)).samples, before_second)

    def test_fractional_image_transform_matches_mupdf_without_tolerance(self):
        with fitz.open() as doc:
            page = doc.new_page(width=595.28, height=841.89)
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((10, 20, 230)) * 25, False)
            page.insert_image(fitz.Rect(60, 60, 110, 110), pixmap=pix)
            ref = page.get_contents()[0]
            doc.update_stream(ref, b'q 89.6493 0 0 12.04736 65.395 780.84 cm /fzImg0 Do Q')
            inventory = driver.pdf._image_inventory(page)
            calls = driver.pdf._form_calls(page, include_images=True)
            self.assertEqual(calls[0]['rect'], inventory[0]['rect'])
            self.assertTrue(driver.pdf._erase_direct_image_calls(page, inventory, [0]))
            self.assertEqual(driver.pdf._image_inventory(page), [])

    def test_direct_image_erasure_keeps_unrelated_scientific_stream_bytes(self):
        with fitz.open(self.source) as doc:
            page = doc[0]
            streams = [doc.xref_stream(ref) for ref in page.get_contents()]
            guards = driver.pdf._validate_image_operations(doc, self.source, [self.operation])
            driver.pdf._erase_image_operations(page, guards)
            after = [doc.xref_stream(ref) for ref in page.get_contents()]
            self.assertEqual(len(after), len(streams))
            self.assertEqual(after[1:], streams[1:])

    def test_saved_candidate_passes_outside_mask_validation(self):
        work = self.root / 'qa'
        work.mkdir()
        pages = driver.pdf.inspect_pdf(self.source, work)
        plan = {'redactions': [], 'forbidden_strings': [], 'uncertain': []}
        candidate = self.root / 'candidate.pdf'
        driver.pdf.redact_pdf(self.source, candidate, plan, pages, text_only=True,
                              image_operations=[self.operation])
        validation = {**plan, 'redactions': [{'page': 1, 'rect': self.rect,
                                             'category': 'affiliation', 'text': ''}]}
        result = driver.pdf.check_pdf(self.source, candidate, validation, work,
                                      allow_text_rounding=True)
        self.assertTrue(result['outside_redactions_unchanged'])
        with fitz.open(candidate) as doc:
            self.assertEqual(len(driver.pdf._image_inventory(doc[0])), 1)
            self.assertIn('SCI', doc[0].get_text())

    def test_soft_mask_bytes_are_part_of_the_image_guard(self):
        with fitz.open() as doc:
            p = doc.new_page()
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((10, 20, 30, 128)) * 25, True)
            ref = p.insert_image(self.rect, pixmap=pix)
            original = driver.pdf._image_signature(doc, ref)
            kind, value = doc.xref_get_key(ref, 'SMask')
            self.assertEqual(kind, 'xref')
            mask = int(value.split()[0])
            doc.update_stream(mask, bytes([255]) * 25)
            self.assertNotEqual(driver.pdf._image_signature(doc, ref), original)

    def test_changed_source_hash_image_hash_xref_and_rect_fail_closed(self):
        for field, value in [('source_pdf_sha256', 'b' * 64), ('image_sha256', 'b' * 64),
                             ('source_xref', 9999), ('rect', [61., 60., 110., 110.])]:
            with self.subTest(field=field), fitz.open(self.source) as doc:
                with self.assertRaises(ValueError):
                    driver.pdf._validate_image_operations(doc, self.source, [{**self.operation, field: value}])

    def test_other_image_overlap_is_rejected(self):
        with fitz.open(self.source) as doc:
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((230, 20, 10)) * 25, False)
            doc[0].insert_image(fitz.Rect(65, 65, 115, 115), pixmap=pix)
            with self.assertRaisesRegex(ValueError, 'overlaps'):
                driver.pdf._validate_image_operations(doc, self.source, [self.operation])

    def test_changed_image_dictionary_is_rejected_before_erasure(self):
        with fitz.open(self.source) as doc:
            guards = driver.pdf._validate_image_operations(doc, self.source, [self.operation])
            doc.xref_set_key(self.operation['source_xref'], 'Interpolate', 'true')
            with self.assertRaisesRegex(ValueError, 'resource changed'):
                driver.pdf._erase_image_operations(doc[0], guards)

    def test_duplicate_operation_is_rejected(self):
        with fitz.open(self.source) as doc:
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                driver.pdf._validate_image_operations(doc, self.source, [self.operation, self.operation])


class ImageDriverTests(RulesDriverTests):
    def test_missing_paired_image_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'together'):
            driver.anonymize(self.source, self.paper, self.output, self.report,
                             image_manifest=self.root / 'manifest.json', scope='identifiers')

    def test_no_evidence_keeps_image_operations_empty(self):
        result = self.execute()
        self.assertEqual(result['image_plan']['image_operations'], [])
        self.assertIsNone(result['binding']['image_plan_binding'])
        self.assertEqual(result['plan'], result['validation_plan'])

    def test_image_evidence_binding_invalidates_cache(self):
        from utility.anonymize import anonymize_image_rules
        image_plan = {'binding': {'manifest': 'first'}, 'image_operations': [], 'uncertain': []}
        with patch.object(driver.rules, 'build_plan', return_value=deepcopy(self.plan)), \
             patch.object(anonymize_image_rules, 'plan_image_redactions', return_value=image_plan):
            first = driver.anonymize(self.source, self.paper, self.output, self.report,
                                     image_manifest='manifest.json', image_ocr_evidence='ocr.json', scope='identifiers')
            self.assertEqual(first['status'], 'rules_passed')
            image_plan['binding'] = {'manifest': 'changed'}
            with self.assertRaises(FileExistsError):
                driver.anonymize(self.source, self.paper, self.output, self.report,
                                 image_manifest='manifest.json', image_ocr_evidence='ocr.json', scope='identifiers')


class FormOperationTests(unittest.TestCase):
    def fixture(self, shared=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / 'source.pdf'
        with fitz.open() as doc:
            page = doc.new_page()
            page.draw_rect((400, 360, 500, 420), color=None, fill=(.8, .8, .8))
            page.insert_text((60, 80), 'Scientific paragraph stays intact.')
            pix = fitz.Pixmap(fitz.csRGB, 5, 5, bytes((10, 230, 20)) * 25, False)
            page.insert_image(fitz.Rect(60, 180, 160, 280), pixmap=pix)
            form = doc.get_new_xref()
            doc.update_object(form, '<< /Type /XObject /Subtype /Form /BBox [0 0 40 12] /Resources <<>> >>')
            doc.update_stream(form, b'0 0 1 rg 0 0 40 12 re f')
            for number in range(2 if shared else 1):
                page = doc[0] if number == 0 else doc.new_page()
                kind, resources = doc.xref_get_key(page.xref, 'Resources')
                if kind == 'xref':
                    doc.xref_set_key(page.xref, 'Resources', doc.xref_object(int(resources.split()[0])))
                kind, objects = doc.xref_get_key(page.xref, 'Resources/XObject')
                if kind == 'xref':
                    doc.xref_set_key(page.xref, 'Resources/XObject', doc.xref_object(int(objects.split()[0])))
                elif kind == 'null':
                    doc.xref_set_key(page.xref, 'Resources/XObject', '<<>>')
                doc.xref_set_key(page.xref, 'Resources/XObject/Logo', f'{form} 0 R')
                ref = doc.get_new_xref()
                doc.update_object(ref, '<<>>')
                doc.update_stream(ref, b'q 1 0 0 1 420 450 cm /Logo Do Q')
                refs = page.get_contents() + [ref]
                doc.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{x} 0 R' for x in refs) + ']')
            doc.save(source)
        from utility.anonymize import anonymize_image_rules
        inventory = anonymize_image_rules.inspect_source_forms(source)
        entry = next(x for x in inventory['forms'] if x['source_xref'] == form)
        call = entry['invocations'][0]
        op = {'object_type': 'form', 'page': call['page'], 'rect': call['rect'],
              'source_xref': form, 'source_pdf_sha256': driver.pdf.digest(source),
              'form_sha256': entry['form_sha256'],
              'form_dictionary_sha256': entry['form_dictionary_sha256'],
              'form_closure_sha256': entry['form_closure_sha256'],
              'resource_name': call['resource_name'], 'top_level_do_index': call['top_level_do_index'],
              'category': 'affiliation', 'reason': 'bound isolated affiliation wordmark',
              'evidence_sha256': 'a' * 64}
        return root, source, op

    def test_unique_form_erasure_keeps_gray_panel_and_scientific_image(self):
        root, source, op = self.fixture()
        self.assertEqual(op['top_level_do_index'], 1)
        work = root / 'qa'
        work.mkdir()
        original = source.read_bytes()
        pages = driver.pdf.inspect_pdf(source, work)
        plan = {'redactions': [], 'forbidden_strings': [], 'uncertain': []}
        candidate = root / 'candidate.pdf'
        driver.pdf.redact_pdf(source, candidate, plan, pages, text_only=True, form_operations=[op])
        validation = {**plan, 'redactions': [{'page': op['page'], 'rect': op['rect'],
                                             'category': 'affiliation', 'text': ''}]}
        result = driver.pdf.check_pdf(source, candidate, validation, work, allow_text_rounding=True)
        self.assertTrue(result['outside_redactions_unchanged'])
        self.assertEqual(source.read_bytes(), original)
        with fitz.open(candidate) as doc:
            self.assertIn('Scientific paragraph stays intact.', doc[0].get_text())
            self.assertEqual(len(driver.pdf._image_inventory(doc[0])), 1)
            self.assertEqual(len(doc[0].get_xobjects()), 0)
            # Removing a blue mark must expose its existing gray panel, never white paint.
            pixel = doc[0].get_pixmap().pixel(430, 385)
            self.assertEqual(pixel, (204, 204, 204))

    def test_shared_form_is_rejected_without_mutating_source(self):
        _, source, op = self.fixture(shared=True)
        with fitz.open(source) as doc:
            with self.assertRaises(ValueError):
                driver.pdf._validate_form_operations(doc, source, [op])
            self.assertEqual(sum(len(p.get_xobjects()) for p in doc), 2)

    def test_form_closure_placement_and_source_tampering_are_rejected(self):
        _, source, op = self.fixture()
        for field, value in [('source_pdf_sha256', 'b' * 64), ('form_closure_sha256', 'b' * 64),
                             ('form_dictionary_sha256', 'b' * 64), ('form_sha256', 'b' * 64),
                             ('top_level_do_index', 99), ('rect', [0., 0., 1., 1.])]:
            with self.subTest(field=field), fitz.open(source) as doc:
                with self.assertRaises(ValueError):
                    driver.pdf._validate_form_operations(doc, source, [{**op, field: value}])



class AdvanceOnlyTextTests(unittest.TestCase):
    def fixture(self, *, widths=True, operator='TJ', spacing=False):
        doc=fitz.open();page=doc.new_page()
        font=page.insert_font(fontname='cour')
        page.insert_font(fontname='helv')
        if widths:
            doc.xref_set_key(font,'FirstChar','0')
            doc.xref_set_key(font,'LastChar','255')
            doc.xref_set_key(font,'Widths','['+' '.join(['600']*256)+']')
        state=b'1 Tc 2 Tw 80 Tz ' if spacing else b'0 Tc 0 Tw 100 Tz '
        show=b'[(Private ) -25.00 (author)] TJ' if operator=='TJ' else b'(Private author) Tj'
        data=(b'q 1.000001 0 0 1.000001 25.55555 10.123456 cm 10 10 25 35 re S Q\n'
              b'BT /cour 10 Tf '+state+b'72 700 Td '+show+
              b' /helv 10 Tf (.) Tj 0 -40 Td (Scientific result.) Tj ET')
        ref=doc.get_new_xref();doc.update_object(ref,'<<>>');doc.update_stream(ref,data);page.set_contents(ref)
        blob=doc.tobytes(garbage=2);doc.close()
        doc=fitz.open(stream=blob,filetype='pdf');self.addCleanup(doc.close);page=doc[0]
        chars=next(row for row in driver.pdf._text_lines(page) if ''.join(c['c'] for c in row).startswith('Private'))
        stop=next(i for i,c in enumerate(chars) if c['c']=='.')
        target=chars[:stop]
        rect=[min(c['bbox'][0] for c in target),min(c['bbox'][1] for c in target),
              max(c['bbox'][2] for c in target),max(c['bbox'][3] for c in target)]
        item={'page':1,'rect':rect,'category':'author','text':''.join(c['c'] for c in target)}
        return doc,page,item,data
    def test_complete_tj_array_keeps_retained_period_position_and_kerning_bytes(self):
        doc,page,item,data=self.fixture()
        periods=[c['origin'] for row in driver.pdf._text_lines(page) for c in row if c['c']=='.']
        _,expected=driver.pdf._text_only_rectangles(page,[item])
        self.assertTrue(driver.pdf._try_direct_text_redaction(page,[item],expected))
        self.assertEqual(periods,[c['origin'] for row in driver.pdf._text_lines(page) for c in row if c['c']=='.'])
        changed=doc.xref_stream(page.get_contents()[0])
        self.assertIn(b'-25.00',changed)
        self.assertEqual(changed.split(b'BT')[0],data.split(b'BT')[0])
        self.assertNotIn(b'Private',changed)
        self.assertIn('Scientific result.',page.get_text())
    def test_complete_tj_string_and_explicit_spacing_preserve_positions(self):
        for operator,spacing in [('Tj',False),('TJ',True)]:
            with self.subTest(operator=operator,spacing=spacing):
                doc,page,item,_=self.fixture(operator=operator,spacing=spacing)
                periods=[c['origin'] for row in driver.pdf._text_lines(page) for c in row if c['c']=='.']
                _,expected=driver.pdf._text_only_rectangles(page,[item])
                self.assertTrue(driver.pdf._try_direct_text_redaction(page,[item],expected))
                self.assertEqual(periods,[c['origin'] for row in driver.pdf._text_lines(page) for c in row if c['c']=='.'])
    def test_separate_graphics_save_restore_wrappers_keep_known_text_state(self):
        doc,page,item,_=self.fixture()
        refs=page.get_contents()
        wrappers=[]
        for data in [b'q\n',b'Q\n']:
            ref=doc.get_new_xref();doc.update_object(ref,'<<>>');doc.update_stream(ref,data);wrappers.append(ref)
        refs=[wrappers[0],*refs,wrappers[1]]
        doc.xref_set_key(page.xref,'Contents','['+' '.join(f'{r} 0 R' for r in refs)+']')
        _,expected=driver.pdf._text_only_rectangles(page,[item])
        self.assertTrue(driver.pdf._try_direct_text_redaction(page,[item],expected))
        self.assertIn('Scientific result.',page.get_text())

    def test_unknown_state_vertical_encoding_and_non_simple_font_are_rejected(self):
        doc,page,item,_=self.fixture()
        data=doc.xref_stream(page.get_contents()[0])
        op,args=next((op,args) for op,args in driver.pdf._content_operations(data) if op==b'TJ')
        _,start,end=args[0]
        state={'font':'/cour','size':10.,'tc':0.,'tw':0.,'tz':100.,'tr':0.}
        self.assertIsNotNone(driver.pdf._advance_only_replacements(page,data,op,start,end,state,{}))
        for key,value in [('font',None),('size',None),('size',0.),('tc',None),('tw',None),('tz',None),('tz',0.),('tr',3.)]:
            with self.subTest(key=key,value=value):
                self.assertIsNone(driver.pdf._advance_only_replacements(page,data,op,start,end,{**state,key:value},{}))
        ref=next(f[0] for f in page.get_fonts(full=True) if f[4]=='cour')
        original=doc.xref_get_key
        for field,value in [('Subtype',('name','/Type0')),('Encoding',('name','/Identity-V')),('Widths',('null','null'))]:
            with self.subTest(field=field), patch.object(doc,'xref_get_key',
                    side_effect=lambda x,k,_field=field,_value=value: _value if x==ref and k==_field else original(x,k)):
                self.assertIsNone(driver.pdf._advance_only_replacements(page,data,op,start,end,state,{}))
    def test_any_retained_coordinate_shift_rolls_back_the_direct_attempt(self):
        doc,page,item,_=self.fixture()
        refs=page.get_contents();before=[doc.xref_stream(ref) for ref in refs]
        _,expected=driver.pdf._text_only_rectangles(page,[item])
        original=driver.pdf._advance_only_replacements
        def shifted(*args,**kwargs):
            edits=original(*args,**kwargs)
            if edits:
                lo,hi,value=edits[0]
                edits[0]=(lo,hi,value.replace(b'-600',b'-601',1))
            return edits
        with patch.object(driver.pdf,'_advance_only_replacements',side_effect=shifted):
            self.assertFalse(driver.pdf._try_direct_text_redaction(page,[item],expected))
        self.assertEqual(page.get_contents(),refs)
        self.assertEqual([doc.xref_stream(ref) for ref in refs],before)

    def test_missing_widths_and_partial_string_keep_original_streams(self):
        doc,page,item,_=self.fixture(widths=False)
        refs=page.get_contents();data=[doc.xref_stream(x) for x in refs]
        _,expected=driver.pdf._text_only_rectangles(page,[item])
        self.assertFalse(driver.pdf._try_direct_text_redaction(page,[item],expected))
        self.assertEqual(data,[doc.xref_stream(x) for x in refs])
        doc,page,item,_=self.fixture()
        item['text']='Private';item['rect']=list(page.search_for('Private')[0])
        refs=page.get_contents();data=[doc.xref_stream(x) for x in refs]
        _,expected=driver.pdf._text_only_rectangles(page,[item])
        self.assertFalse(driver.pdf._try_direct_text_redaction(page,[item],expected))
        self.assertEqual(data,[doc.xref_stream(x) for x in refs])

if __name__ == '__main__':
    unittest.main()

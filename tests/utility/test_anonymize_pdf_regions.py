"""Opt-in regional erasure: synthetic PDFs, no model or network calls."""
import inspect
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz
from utility.anonymize import anonymize_pdf as cleaner


class RegionRedactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.pdf'
        self.target = self.root / 'candidate.pdf'
        self.work = self.root / 'qa'
        self.work.mkdir()

    def save(self, doc):
        doc.set_metadata({'author': 'Alice Example', 'title': 'Title block'})
        doc.save(self.source)
        doc.close()
        return cleaner.inspect_pdf(self.source, self.work)

    def plan(self, *rectangles):
        return {'redactions': [{'page': number, 'rect': list(rect), 'category': 'other_identity', 'text': ''}
                               for number, rect in rectangles], 'forbidden_strings': [], 'uncertain': []}

    def run_region(self, pages, plan):
        details = cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)
        validation = cleaner.check_pdf(self.source, self.target, plan, self.work, require_blank_regions=True)
        self.assertTrue(validation['outside_redactions_unchanged'])
        self.assertTrue(validation['regions_blank'])
        self.assertTrue(validation['outside_region_text_preserved'])
        self.assertEqual(validation['blank_region_count'], len(plan['redactions']))
        self.assertEqual(details['redaction_mode'], 'regions')
        return details

    def assert_crop_equal(self, rect, page=0):
        with fitz.open(self.source) as before, fitz.open(self.target) as after:
            a = before[page].get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, annots=False)
            b = after[page].get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, annots=False)
            self.assertEqual((a.width, a.height, a.samples), (b.width, b.height, b.samples))

    def test_title_block_text_logo_and_body_boundary(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'A scientific title', fontsize=18)
        page.insert_text((30, 75), 'Alice Example / Example University', fontsize=10)
        page.draw_rect((350, 30, 450, 80), color=(0, 0, 1), fill=(0, 0, 1))
        page.insert_text((30, 145), 'Protected science x = 42', fontsize=11)
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 20, 470, 100))))
        with fitz.open(self.target) as clean:
            self.assertEqual(clean[0].get_text().strip(), 'Protected science x = 42')
        self.assert_crop_equal((15, 115, 480, 175))

    def test_partial_raster_and_vector_keep_scientific_remainder(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=400)
        pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 0)
        pixmap.clear_with(35)
        page.insert_image((30, 30, 230, 130), pixmap=pixmap)
        page.draw_rect((270, 30, 470, 130), color=None, fill=(0.2, 0.6, 0.3))
        page.insert_text((30, 200), 'Scientific caption remains')
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 20, 480, 80))))
        self.assert_crop_equal((20, 85, 480, 230))

    def test_ack_left_column_preserves_right_column_body(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=600)
        page.insert_text((30, 320), 'Acknowledgments', fontsize=14)
        page.insert_text((30, 345), 'Funded by Example Institute', fontsize=10)
        page.insert_text((325, 320), '4 Results', fontsize=14)
        page.insert_text((325, 345), 'Accuracy improves by 12 percent.', fontsize=10)
        page.insert_text((30, 405), 'References: retained scientific citation', fontsize=10)
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 298, 290, 365))))
        self.assert_crop_equal((300, 290, 590, 390))
        self.assert_crop_equal((20, 385, 590, 435))

    def test_shared_form_selected_once_and_science_placement_retained(self):
        stamp = fitz.open()
        p = stamp.new_page(width=200, height=80)
        p.insert_text((10, 35), 'Shared Form text', fontsize=14)
        p.draw_circle((165, 40), 15, fill=(0.1, 0.3, 0.8))
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.show_pdf_page(fitz.Rect(20, 20, 220, 100), stamp, 0)
        page.show_pdf_page(fitz.Rect(250, 250, 450, 330), stamp, 0)
        page = doc.new_page(width=500, height=500)
        page.show_pdf_page(fitz.Rect(50, 100, 250, 180), stamp, 0)
        pages = self.save(doc)
        stamp.close()
        self.run_region(pages, self.plan((1, (10, 10, 230, 110))))
        self.assert_crop_equal((245, 245, 455, 335))
        self.assert_crop_equal((0, 0, 500, 500), page=1)
        with fitz.open(self.target) as clean:
            self.assertEqual(clean[0].get_text().count('Shared Form text'), 1)
            self.assertEqual(clean[1].get_text().count('Shared Form text'), 1)

    def test_rotated_crop_local_regions(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=500)
        page.insert_text((75, 105), 'Identity title')
        page.insert_text((75, 250), 'Protected body')
        page.set_cropbox(fitz.Rect(50, 50, 550, 450))
        page.set_rotation(90)
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (10, 20, 470, 90))))
        with fitz.open(self.target) as clean:
            self.assertEqual(clean[0].rotation, 90)
            self.assertIn('Protected body', clean[0].get_text())
            self.assertNotIn('Identity title', clean[0].get_text())

    def test_original_redact_annotation_cannot_erase_body(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'Title and author')
        page.insert_text((30, 200), 'Must remain scientific text')
        page.add_redact_annot((20, 175, 350, 215), fill=(1, 1, 1))
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 20, 450, 80))))
        self.assert_crop_equal((20, 170, 450, 225))

    def test_invisible_stream_stripping_preserves_visible_figure(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'Title and author')
        page.draw_rect((30, 160, 450, 260), color=None, fill=(0.2, 0.6, 0.9))
        page.insert_text((45, 200), 'Visible scientific plot label')
        page.insert_text((40, 195), 'Private hidden payload', render_mode=3)
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 20, 470, 80))))
        self.assert_crop_equal((20, 150, 470, 270))
        with fitz.open(self.target) as clean:
            self.assertNotIn('Private hidden', clean[0].get_text())
            self.assertIn('Visible scientific', clean[0].get_text())

    def test_blank_validation_rejects_white_overlay_with_hidden_underlying_text(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'Identity retained below paint')
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 450, 80)))
        with fitz.open(self.source) as doc:
            doc[0].draw_rect((20, 20, 450, 80), color=None, fill=(1, 1, 1), overlay=True)
            doc.set_metadata({})
            doc.save(self.target)
        with self.assertRaisesRegex(ValueError, 'Text remains inside regional'):
            cleaner.check_pdf(self.source, self.target, plan, self.work, require_blank_regions=True)

    def test_blank_validation_rejects_nonwhite_interior_even_with_no_text(self):
        doc = fitz.open()
        doc.new_page(width=500, height=500)
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 450, 80)))
        with fitz.open(self.source) as doc:
            doc[0].draw_rect((20, 20, 450, 80), color=None, fill=(0.99, 0.99, 0.99))
            doc.set_metadata({})
            doc.save(self.target)
        with self.assertRaisesRegex(ValueError, 'Nonwhite pixels inside regional'):
            cleaner.check_pdf(self.source, self.target, plan, self.work, require_blank_regions=True)

    def test_region_mode_rejects_mixed_inputs_and_text_targets(self):
        doc = fitz.open()
        doc.new_page(width=500, height=500)
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 450, 80)))
        for kwargs in ({'text_only': True}, {'image_operations': [{}]}, {'form_operations': [{}]}):
            with self.assertRaises(ValueError):
                cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True, **kwargs)
        for field, value in [('text', 'identity')]:
            plan['redactions'][0][field] = value
            with self.assertRaises(ValueError):
                cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)
        plan['redactions'][0]['text'] = ''
        plan['forbidden_strings'] = ['identity']
        with self.assertRaises(ValueError):
            cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)

    def test_blank_regions_do_not_excuse_removed_scientific_text_outside(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'Title and author')
        page.insert_text((30, 200), 'Scientific result')
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 470, 80)))
        cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)
        damaged = self.root / 'damaged.pdf'
        with fitz.open(self.target) as doc:
            doc[0].add_redact_annot((20, 180, 400, 220), fill=(1, 1, 1))
            doc[0].apply_redactions()
            doc.save(damaged)
        with self.assertRaisesRegex(ValueError, 'text changed outside regional'):
            cleaner.check_pdf(self.source, damaged, plan, self.work, require_blank_regions=True)

    def test_tiny_region_cannot_pass_white_check_vacuously(self):
        doc = fitz.open()
        doc.new_page(width=500, height=500)
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 20.1, 20.1)))
        cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)
        with self.assertRaisesRegex(ValueError, 'no testable white interior'):
            cleaner.check_pdf(self.source, self.target, plan, self.work, require_blank_regions=True)

    def test_default_modes_remain_opt_in(self):
        self.assertIs(inspect.signature(cleaner.redact_pdf).parameters['region_mode'].default, False)
        self.assertIs(inspect.signature(cleaner.check_pdf).parameters['require_blank_regions'].default, False)

    def test_zero_extent_glyphs_are_assigned_by_their_actual_position(self):
        region = fitz.Rect(20, 20, 90, 90)
        self.assertTrue(cleaner._region_glyph_intersects((35, 40, 35, 55), region))
        self.assertTrue(cleaner._region_glyph_intersects((35, 40, 55, 40), region))
        self.assertTrue(cleaner._region_glyph_intersects((35, 40, 35, 40), region))
        self.assertFalse(cleaner._region_glyph_intersects((95, 40, 95, 55), region))
        self.assertFalse(cleaner._region_glyph_intersects((35, 95, 55, 95), region))
        self.assertFalse(cleaner._region_glyph_intersects((90, 30, 95, 40), region))

    def _trace_component_fixture(self, inside):
        from unittest.mock import patch
        doc = fitz.open()
        page = doc.new_page(width=500, height=500)
        page.insert_text((30, 45), 'Title and author')
        page.insert_text((30, 200), 'Scientific result')
        pages = self.save(doc)
        plan = self.plan((1, (20, 20, 470, 80)))
        cleaner.redact_pdf(self.source, self.target, plan, pages, region_mode=True)
        real_trace = fitz.Page.get_texttrace
        def trace(page):
            spans = real_trace(page)
            if page.parent.name == str(self.source):
                span = spans[0 if inside else -1]
                component = list(span['chars'][0])
                x0, y0, _, y1 = component[3]
                component[0], component[3] = ord('i'), (x0, y0, x0, y1)
                span['chars'] = (*span['chars'], tuple(component))
            return spans
        with patch.object(fitz.Page, 'get_texttrace', trace):
            return cleaner.check_pdf(self.source, self.target, plan, self.work, require_blank_regions=True)

    def test_masked_zero_width_ligature_component_is_not_outside_science(self):
        self.assertTrue(self._trace_component_fixture(True)['regions_blank'])

    def test_outside_zero_width_ligature_component_must_remain(self):
        with self.assertRaisesRegex(ValueError, 'text changed outside regional'):
            self._trace_component_fixture(False)

    def _transform_fixture(self, nonidentity=False, duplicate=False):
        stamp = fitz.open()
        p = stamp.new_page(width=200, height=80)
        p.insert_text((10, 35), 'Scientific Form')
        p.draw_line((10, 60), (180, 15), color=(0.2, 0.4, 0.8), width=0.7)
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((30, 45), 'Title and author')
        page.show_pdf_page(fitz.Rect(30, 180, 230, 260), stamp, 0)
        top = [item for item in page.get_xobjects() if item[2] == 0][0]
        doc.xref_set_key(top[0], 'Matrix', '[1 0 0 1 0 0]')
        chain = b'1 0 0 1 55.44 607.085 cm\n1 0 0 1 0 -118.851 cm\n1 0 0 1 0 -118.851 cm\n1 0 0 1 0 -118.848 cm\n.56552 0 0 .56552 0 0 cm'
        if nonidentity:
            content = b'1 0 0 1 7 9 cm\nq\n.5 0 0 .5 30 200 cm\n0 g\n0 G\n/' + top[1].encode() + b' Do\nQ'
        else:
            content = b'q\n' + chain + b'\n/' + top[1].encode() + b' Do\nQ'
        doc.update_stream(page.get_contents()[-1], content + (b'\n' + content if duplicate else b''))
        stamp.close()
        return doc, chain

    def test_region_restoration_keeps_original_accumulated_transform_bytes(self):
        doc, chain = self._transform_fixture()
        pages = self.save(doc)
        self.run_region(pages, self.plan((1, (20, 20, 470, 80))))
        with fitz.open(self.target) as clean:
            contents = b'\n'.join(clean.xref_stream(ref) for ref in clean[0].get_contents())
            self.assertIn(chain, contents)

    def test_nonidentity_form_entry_cannot_replay_absolute_source_chain(self):
        doc, _ = self._transform_fixture(nonidentity=True)
        with doc:
            calls = cleaner._form_calls(doc[0])
            self.assertEqual(len(calls), 1)
            self.assertIsNone(calls[0].get('replay_cm'))

    def test_ambiguous_form_mapping_declines_without_resource_or_stream_changes(self):
        doc, _ = self._transform_fixture(duplicate=True)
        with doc:
            page = doc[0]
            original = cleaner._snapshot_unaffected_forms(page, [(20, 20, 470, 80)])
            before = (doc.xref_length(), [doc.xref_stream(ref) for ref in page.get_contents()], doc.xref_object(page.xref))
            self.assertEqual(cleaner._restore_unaffected_forms(page, original, allow_removed=True), 0)
            self.assertEqual(before, (doc.xref_length(), [doc.xref_stream(ref) for ref in page.get_contents()], doc.xref_object(page.xref)))

    def _unpadded_outside_samples(self, page, pixmap, rectangles):
        import math
        samples = bytearray(pixmap.samples)
        for box in rectangles:
            r = fitz.Rect(box) * page.rotation_matrix
            x0,y0=max(0,math.floor(r.x0*1.5)),max(0,math.floor(r.y0*1.5))
            x1,y1=min(pixmap.width,math.ceil(r.x1*1.5)),min(pixmap.height,math.ceil(r.y1*1.5))
            for y in range(y0,y1):
                start,end=y*pixmap.stride+x0*pixmap.n,y*pixmap.stride+x1*pixmap.n
                samples[start:end]=b'\0'*(end-start)
        return samples

    def _fill_fixture(self, *, clip=False, overlap=False):
        doc=fitz.open();p=doc.new_page(width=500,height=500)
        p.draw_rect((0,0,500,500),color=None,fill=(0.2,0.4,0.6),fill_opacity=.7)
        p.insert_text((30,45),'Title and author');p.insert_text((30,200),'Protected science')
        if clip:
            alpha=doc.get_new_xref();doc.update_object(alpha,'<< /Type /ExtGState /ca .3 /CA .3 >>')
            resources=int(doc.xref_get_key(p.xref,'Resources')[1].split()[0])
            doc.xref_set_key(resources,'ExtGState/RegionGlobalAlpha',f'{alpha} 0 R')
            ref=doc.get_new_xref();doc.update_object(ref,'<<>>');doc.update_stream(ref,b'/RegionGlobalAlpha gs\n0 0 200 500 re W n')
            doc.xref_set_key(p.xref,'Contents','['+' '.join(f'{x} 0 R' for x in p.get_contents())+f' {ref} 0 R]')
        pages=self.save(doc)
        rectangles=[(20.24,20.11,450.33,80.42)]
        if overlap:rectangles.append((40.19,30.44,470.21,90.72))
        plan=self.plan(*[(1,r) for r in rectangles]);self.run_region(pages,plan)
        with fitz.open(self.source) as before,fitz.open(self.target) as after:
            a,b=cleaner.render_page(before[0]),cleaner.render_page(after[0])
            self.assertEqual(self._unpadded_outside_samples(before[0],a,rectangles),
                             self._unpadded_outside_samples(after[0],b,rectangles))
            whites=[d for d in after[0].get_cdrawings() if list(d.get('fill') or [])==[1.,1.,1.]]
            self.assertEqual(len(whites),len(rectangles))
            for d in whites:
                self.assertEqual(d['type'],'f')
                self.assertIsNone(d.get('color'))
                self.assertEqual(d['fill_opacity'],1)

    def test_region_fill_has_no_stroke_or_unpadded_background_spill(self):
        self._fill_fixture()

    def test_region_fill_does_not_inherit_source_global_clip_or_opacity(self):
        self._fill_fixture(clip=True)

    def test_overlapping_regions_stay_opaque_white_without_internal_border(self):
        self._fill_fixture(overlap=True)

    def test_legacy_default_retains_its_original_native_fill_behavior(self):
        doc=fitz.open();doc.new_page(width=500,height=500);pages=self.save(doc)
        plan=self.plan((1,(20,20,450,80)))
        cleaner.redact_pdf(self.source,self.target,plan,pages)
        with fitz.open(self.target) as clean:
            whites=[d for d in clean[0].get_cdrawings() if list(d.get('fill') or [])==[1.,1.,1.]]
            self.assertEqual(len(whites),1)
            self.assertEqual(whites[0]['type'],'fs')


if __name__ == '__main__':
    unittest.main()

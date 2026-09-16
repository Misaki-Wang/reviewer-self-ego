"""Synthetic source-image planning tests; no OCR/model invocation."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import pymupdf as fitz
from utility.anonymize import anonymize_image_rules as rules


class ImageRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'original.pdf'
        self.paper = {'title': 'A Scientific Method', 'authors': ['Alice Smith']}

    def make(self, label='Google DeepMind', affiliation='Google DeepMind', rect=(60, 25, 180, 42),
             extra_image=None, contact=False):
        with fitz.open() as logo:
            page = logo.new_page(width=600, height=100)
            page.insert_text((20, 60), label, fontsize=32)
            image = page.get_pixmap(alpha=True).tobytes('png')
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            for text, y in [('A Scientific Method', 80), ('Alice Smith', 115), (affiliation, 135),
                            ('Abstract', 180), ('Scientific model results remain intact.', 215)]:
                page.insert_text((60, y), text, fontsize=11)
            if contact:
                page.insert_text((60, 300), 'Correspondence: alice@meta.com', fontsize=10)
            page.insert_image(fitz.Rect(rect), stream=image)
            if extra_image:
                page.insert_image(fitz.Rect(extra_image), stream=image)
            doc.save(self.source)
        return rules.extract_source_images(self.source, self.paper, self.root / 'images')

    def ocr(self, manifest, text='Google DeepMind', changes=None):
        path = self.root / 'ocr.jsonl'
        runtime, source = self.root / 'runtime', self.root / 'native.swift'
        runtime.write_bytes(b'native synthetic runtime'); source.write_bytes(b'native synthetic source')
        input_manifest = [{'id': image['image_id'], 'path': '/local/' + image['asset_file'], 'sha256': image['asset_sha256']}
                          for image in manifest['images'] + manifest.get('forms', [])]
        manifest_path = self.root / 'ocr.manifest.json'
        manifest_path.write_text(json.dumps(input_manifest))
        (self.root / 'ocr.receipt.json').unlink(missing_ok=True)
        rows = []
        for image in manifest['images'] + manifest.get('forms', []):
            label = text.get(image['width'], '') if isinstance(text, dict) else text
            row = {'schema_version': 'offline-image-vision-ocr-2', 'id': image['image_id'],
                   'path': '/local/' + image['asset_file'], 'expected_png_sha256': image['asset_sha256'],
                   'verified_png_sha256': image['asset_sha256'], 'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                   'helper_executable_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(), 'decoded_pixel_sha256': 'c' * 64,
                   'width': image['width'], 'height': image['height'], 'error': None,
                   'backend': 'Apple Vision VNRecognizeTextRequest', 'recognition_level': 'accurate',
                   'recognition_languages': ['en-US'], 'uses_language_correction': False,
                   'uses_cpu_only': True, 'bounding_box_format': 'x,y,width,height',
                   'bounding_box_origin': 'lower_left', 'image_orientation': 'up',
                   'request_revision': 3, 'os_version': 'test OS', 'text_detected': bool(label),
                   'observation_count': int(bool(label)),
                   'observations': [{'text': label, 'confidence': 1.0,
                                     'bounding_box_normalized': [.05, .2, .9, .6]}] if label else []}
            row.update(changes or {})
            rows.append(row)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        rules.write_ocr_receipt(path, runtime, source)
        return path

    def test_no_evidence_never_removes_images(self):
        manifest = self.make()
        result = rules.plan_image_redactions(self.source, self.paper, manifest)
        self.assertEqual([], result['image_operations'])
        self.assertTrue(result['uncertain'])

    def test_header_wordmark_binds_source_stream_and_placement(self):
        manifest = self.make()
        before = self.source.read_bytes()
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest)])
        self.assertEqual(1, len(result['image_operations']), result)
        op = result['image_operations'][0]
        with fitz.open(self.source) as doc:
            self.assertEqual(hashlib.sha256(doc.xref_stream_raw(op['source_xref'])).hexdigest(), op['image_sha256'])
        self.assertEqual(before, self.source.read_bytes())
        self.assertEqual(manifest['images'][0]['placements'][0]['rect'], op['rect'])
        self.assertFalse(result['ready_for_blind_review'])

    def test_cmu_acronym_matches_explicit_affiliation(self):
        manifest = self.make('CMU', 'Carnegie Mellon University', (125, 106, 149, 116))
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest, 'CMU')])
        self.assertEqual(1, len(result['image_operations']), result)

    def test_meta_contact_panel_is_not_limited_to_pre_title(self):
        manifest = self.make('Meta', 'Reality Labs Research at Meta', (240, 290, 285, 303), contact=True)
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest, 'Meta')])
        self.assertEqual(1, len(result['image_operations']), result)

    def test_unrelated_logo_without_affiliation_is_review_only(self):
        manifest = self.make(affiliation='Example University')
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest)])
        self.assertEqual([], result['image_operations'])
        self.assertTrue(result['uncertain'])

    def test_large_scientific_image_is_preserved(self):
        manifest = self.make(rect=(60, 230, 500, 440))
        self.assertIsNone(manifest['images'][0]['asset_sha256'])
        result = rules.plan_image_redactions(self.source, self.paper, manifest)
        self.assertEqual([], result['image_operations'])

    def test_small_body_image_is_preserved(self):
        manifest = self.make(rect=(60, 440, 180, 457))
        self.assertIsNone(manifest['images'][0]['asset_sha256'])
        result = rules.plan_image_redactions(self.source, self.paper, manifest)
        self.assertEqual([], result['image_operations'])

    def test_scientific_content_inside_logo_sized_asset_is_preserved(self):
        manifest = self.make()
        result = rules.plan_image_redactions(self.source, self.paper, manifest,
                                             [self.ocr(manifest, 'Google DeepMind Model Accuracy 95%')])
        self.assertEqual([], result['image_operations'])

    def test_one_shared_xref_never_authorizes_unrelated_body_placement(self):
        manifest = self.make(extra_image=(60, 440, 180, 457))
        self.assertEqual(2, len(manifest['images'][0]['placements']))
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest)])
        self.assertEqual(1, len(result['image_operations']), result)
        self.assertTrue(result['uncertain'])

    def test_changed_source_or_metadata_is_rejected(self):
        manifest = self.make()
        with self.assertRaises(ValueError):
            rules.plan_image_redactions(self.source, {**self.paper, 'title': 'Changed'}, manifest)
        self.source.write_bytes(self.source.read_bytes() + b'\nchanged')
        with self.assertRaises(ValueError):
            rules.plan_image_redactions(self.source, self.paper, manifest)

    def test_changed_inventory_is_rejected(self):
        manifest = self.make()
        manifest['images'][0]['placements'][0]['rect'][0] += 1
        with self.assertRaises(ValueError):
            rules.plan_image_redactions(self.source, self.paper, manifest)

    def test_ocr_hash_error_and_malformed_fields_are_rejected(self):
        manifest = self.make()
        for changes in ({'verified_png_sha256': '0' * 64}, {'error': 'OCR failed'},
                        {'helper_executable_sha256': ''}, {'width': True}, {'uses_cpu_only': 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                rules.plan_image_redactions(self.source, self.paper, manifest,
                                             [self.ocr(manifest, changes=changes)])

    def test_actual_ocr_sidecars_and_runtime_bytes_are_verified(self):
        manifest = self.make()
        for mode in ('rows', 'manifest', 'runtime', 'source'):
            with self.subTest(mode=mode):
                evidence = self.ocr(manifest)
                receipt = json.loads((self.root / 'ocr.receipt.json').read_text())
                target = {'rows': evidence, 'manifest': self.root / 'ocr.manifest.json',
                          'runtime': self.root / receipt['helper_executable_file'],
                          'source': self.root / receipt['helper_source_file']}[mode]
                original = target.read_bytes()
                target.write_bytes(original + b'changed')
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    rules.plan_image_redactions(self.source, self.paper, manifest, [evidence])
                target.write_bytes(original)

    def test_unknown_ocr_is_review_only(self):
        manifest = self.make()
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest, '')])
        self.assertEqual([], result['image_operations'])
        self.assertTrue(result['uncertain'])

    def logo_pair(self, repeated):
        self.paper['authors'] = ['Alice Smith', 'Robert Jones']
        with fitz.open() as image_doc:
            p = image_doc.new_page(width=600, height=100)
            p.insert_text((10, 60), 'CMU', fontsize=32)
            wordmark = p.get_pixmap().tobytes('png')
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100))
        pix.clear_with(0)
        icon = pix.tobytes('png')
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            for text, y in [('A Scientific Method', 80), ('Alice Smith', 115),
                            ('Robert Jones', 135),
                            ('Carnegie Mellon University - Language Technologies Institute', 155),
                            ('Abstract', 180), ('Scientific results remain.', 210)]:
                page.insert_text((60, y), text, fontsize=10)
            for y in ([105, 125] if repeated else [105]):
                page.insert_image(fitz.Rect(117, y, 135, y + 3), stream=wordmark)
                page.insert_image(fitz.Rect(136, y, 139, y + 3), stream=icon)
            doc.save(self.source)
        return rules.extract_source_images(self.source, self.paper, self.root / 'paired-images')

    def test_repeated_author_row_wordmark_icon_cluster_is_supported(self):
        manifest = self.logo_pair(True)
        evidence = self.ocr(manifest, {600: 'CMU', 100: ''})
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [evidence])
        self.assertEqual(4, len(result['image_operations']), result)
        self.assertEqual(2, sum(op['reason'] == 'repeated_source_affiliation_wordmark_icon_cluster'
                                for op in result['image_operations']))

    def test_single_blank_icon_does_not_inherit_wordmark_identity(self):
        manifest = self.logo_pair(False)
        result = rules.plan_image_redactions(self.source, self.paper, manifest,
                                             [self.ocr(manifest, {600: 'CMU', 100: ''})])
        self.assertEqual(1, len(result['image_operations']), result)
        self.assertTrue(result['uncertain'])

    def test_overlapping_image_placements_are_rejected(self):
        manifest = self.make(extra_image=(60, 25, 180, 42))
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest)])
        self.assertEqual([], result['image_operations'])
        self.assertTrue(result['uncertain'])

    def test_source_affiliation_matching_uses_token_boundaries(self):
        context = {'affiliation_lines': [{'text': 'Smith Research Institute'}]}
        self.assertFalse(rules._institution_label('MIT', context))
        self.assertFalse(rules._institution_label('IT', context))
        self.assertTrue(rules._institution_label('Smith', context))

    def test_same_y_far_body_image_is_not_a_contact_logo(self):
        context = {'title_rects': [[60, 60, 250, 80]],
                   'author_lines': [{'text': 'Alice Smith', 'rect': [60, 105, 160, 116]}],
                   'affiliation_lines': [{'text': 'Meta', 'rect': [60, 125, 250, 136]}],
                   'contact_lines': [{'text': 'Correspondence: alice@meta.com', 'rect': [60, 290, 220, 303]}],
                   'body_boundary_y': 180}
        placement = {'page': 1, 'rect': [460, 291, 490, 301], 'page_bounds': [0, 0, 612, 792]}
        self.assertIsNone(rules._placement_context(placement, context))

    def test_one_icon_between_two_wordmarks_does_not_count_as_repeat(self):
        manifest = self.make()
        context = {'title_rects': [[60, 60, 250, 80]],
                   'author_lines': [{'text': 'Alice Smith', 'rect': [60, 105, 160, 116]}],
                   'affiliation_lines': [{'text': 'Carnegie Mellon University', 'rect': [60, 125, 250, 136]}],
                   'contact_lines': [], 'body_boundary_y': 180}
        def placement(rect):
            return {'page': 1, 'rect': rect, 'page_bounds': [0, 0, 612, 792]}
        word = {**manifest['images'][0], 'image_id': 'word', 'source_xref': 1,
                'placements': [placement([90, 128, 100, 132]), placement([108, 128, 118, 132])]}
        icon = {**manifest['images'][0], 'image_id': 'icon', 'source_xref': 2,
                'placements': [placement([102, 128, 106, 132])]}
        fake = {**manifest, 'source_context': context, 'images': [word, icon]}
        rows = {'word': {'row': {'observations': [{'text': 'CMU', 'confidence': 1, 'bounding_box_normalized': [0, 0, 1, 1]}]}},
                'icon': {'row': {'observations': []}}}
        with patch.object(rules, '_inventory', return_value=(fake, {})), \
             patch.object(rules, '_ocr_rows', return_value=(rows, [])):
            result = rules.plan_image_redactions(self.source, self.paper, fake)
        self.assertEqual(2, len(result['image_operations']), result)
        self.assertFalse(any(op['source_xref'] == 2 for op in result['image_operations']))

    def test_tiny_upper_right_trademark_is_not_an_extra_logo_word(self):
        observations = [{'text': 'CMU', 'confidence': 1,
                         'bounding_box_normalized': [0, .13, .93, .65]},
                        {'text': '™M', 'confidence': 1,
                         'bounding_box_normalized': [.926, .718, .041, .068]}]
        self.assertEqual('CMU', rules._wordmark_text(observations))

    def test_arbitrary_extra_letters_or_large_inscriptions_are_not_ignored(self):
        main = {'text': 'CMU', 'confidence': 1, 'bounding_box_normalized': [0, .13, .93, .65]}
        for text, rect in [('M', [.926, .718, .041, .068]), ('™M', [.2, .2, .4, .3])]:
            result = rules._wordmark_text([main, {'text': text, 'confidence': 1, 'bounding_box_normalized': rect}])
            self.assertNotEqual('CMU', result)

    def test_two_author_fields_on_one_baseline_are_not_two_rows(self):
        manifest = self.make()
        context = {'title_rects': [[60, 60, 250, 80]],
                   'author_lines': [{'text': 'Alice Smith', 'rect': [60, 105, 95, 116]},
                                    {'text': 'Robert Jones', 'rect': [160, 105, 195, 116]}],
                   'affiliation_lines': [{'text': 'Carnegie Mellon University', 'rect': [60, 125, 250, 136]}],
                   'contact_lines': [], 'body_boundary_y': 180}
        def place(rect): return {'page': 1, 'rect': rect, 'page_bounds': [0, 0, 612, 792]}
        word = {**manifest['images'][0], 'image_id': 'word', 'source_xref': 1,
                'placements': [place([99, 105, 109, 110]), place([199, 105, 209, 110])]}
        icon = {**manifest['images'][0], 'image_id': 'icon', 'source_xref': 2,
                'placements': [place([111, 105, 115, 110]), place([211, 105, 215, 110])]}
        fake = {**manifest, 'source_context': context, 'images': [word, icon]}
        rows = {'word': {'row': {'observations': [{'text': 'CMU', 'confidence': 1,
                                                  'bounding_box_normalized': [0, 0, 1, 1]}]}},
                'icon': {'row': {'observations': []}}}
        with patch.object(rules, '_inventory', return_value=(fake, {})), \
             patch.object(rules, '_ocr_rows', return_value=(rows, [])):
            result = rules.plan_image_redactions(self.source, self.paper, fake)
        self.assertEqual(2, len(result['image_operations']), result)
        self.assertFalse(any(op['source_xref'] == 2 for op in result['image_operations']))

    def make_form(self, *, shared=False, nested=False, scientific=False):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 40))
        pix.clear_with(0)
        with fitz.open() as doc:
            page = doc.new_page(width=612, height=792)
            for text, y in [('A Scientific Method', 80), ('Alice Smith', 115),
                            ('Reality Labs Research at Meta', 135), ('Abstract', 180),
                            ('Scientific results remain.', 215), ('Correspondence: alice@meta.com', 300)]:
                page.insert_text((60, y), text, fontsize=10)
            page.draw_rect(fitz.Rect(50, 200, 500, 330), fill=(.9, .9, .9), overlay=False)
            rect = fitz.Rect(60, 390, 420, 462) if scientific else fitz.Rect(240, 290, 285, 299)
            xref = page.insert_image(rect, stream=pix.tobytes('png'))
            if shared:
                page.insert_image(fitz.Rect(240, 310, 285, 319), xref=xref)
            resources, body = '<<>>', b'0 0 1 rg 0 0 0.3 1 re f 0 0 0 rg 0.4 0.2 0.6 0.6 re f'
            if nested:
                child = doc.get_new_xref()
                doc.update_object(child, '<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources <<>> >>')
                doc.update_stream(child, body)
                resources, body = f'<< /XObject << /Nested {child} 0 R >> >>', b'/Nested Do'
            doc.update_object(xref, f'<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Resources {resources} >>')
            doc.update_stream(xref, body)
            doc.save(self.source)
        return rules.extract_source_images(self.source, self.paper, self.root / 'form-assets')

    def test_isolated_form_plan_binds_full_geometry_and_closure(self):
        manifest = self.make_form()
        self.assertEqual(1, len(manifest['forms']))
        form = manifest['forms'][0]
        self.assertTrue(form['supported'], form)
        self.assertIsNotNone(form['asset_sha256'])
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest, 'Meta')])
        self.assertEqual([], result['image_operations'])
        self.assertEqual(1, len(result['form_operations']), result)
        operation = result['form_operations'][0]
        self.assertEqual(form['invocations'][0]['rect'], operation['rect'])
        self.assertEqual(form['form_closure_sha256'], operation['form_closure_sha256'])
        self.assertEqual('form', operation['object_type'])

    def test_shared_form_stays_review(self):
        manifest = self.make_form(shared=True)
        self.assertFalse(manifest['forms'][0]['supported'])
        result = rules.plan_image_redactions(self.source, self.paper, manifest)
        self.assertEqual([], result['form_operations'])
        self.assertTrue(result['uncertain'])

    def test_nested_form_stays_review(self):
        manifest = self.make_form(nested=True)
        self.assertFalse(manifest['forms'][0]['supported'])
        self.assertEqual([], rules.plan_image_redactions(self.source, self.paper, manifest)['form_operations'])

    def test_scientific_form_is_not_exported_or_removed(self):
        manifest = self.make_form(scientific=True)
        self.assertIsNone(manifest['forms'][0]['asset_sha256'])
        self.assertEqual([], rules.plan_image_redactions(self.source, self.paper, manifest)['form_operations'])

    def test_one_placement_row_touching_two_anchor_bands_is_not_a_repeat(self):
        manifest = self.make()
        context = {'title_rects': [[60, 60, 250, 80]],
                   'author_lines': [{'text': 'Alice Smith', 'rect': [60, 105, 250, 116]}],
                   'affiliation_lines': [{'text': 'Carnegie Mellon University', 'rect': [60, 125, 250, 136]}],
                   'contact_lines': [], 'body_boundary_y': 180}
        def place(rect): return {'page': 1, 'rect': rect, 'page_bounds': [0, 0, 612, 792]}
        word = {**manifest['images'][0], 'image_id': 'word', 'source_xref': 1,
                'placements': [place([99, 115, 119, 127]), place([199, 115, 219, 127])]}
        icon = {**manifest['images'][0], 'image_id': 'icon', 'source_xref': 2,
                'placements': [place([120, 116, 126, 126]), place([220, 116, 226, 126])]}
        fake = {**manifest, 'source_context': context, 'images': [word, icon]}
        rows = {'word': {'row': {'observations': [{'text': 'CMU', 'confidence': 1,
                                                  'bounding_box_normalized': [0, 0, 1, 1]}]}},
                'icon': {'row': {'observations': []}}}
        with patch.object(rules, '_inventory', return_value=(fake, {})), \
             patch.object(rules, '_ocr_rows', return_value=(rows, [])):
            result = rules.plan_image_redactions(self.source, self.paper, fake)
        self.assertEqual(2, len(result['image_operations']), result)
        self.assertFalse(any(op['source_xref'] == 2 for op in result['image_operations']))

    def test_weak_metadata_names_do_not_anchor_scientific_body_words(self):
        for author, text in [('Li', 'The likelihood objective can be optimized efficiently.'),
                             ('S Li', 'Sliding windows improve long-context inference.')]:
            with self.subTest(author=author):
                self.assertEqual([], rules._authors({'authors': [author]}))
                self.assertFalse(rules._byline_matches(text, rules._authors({'authors': [author]})))
        self.assertFalse(rules._byline_matches('Alice Smith develops the likelihood objective.',
                                              rules._authors({'authors': ['Alice Smith']})))

    def test_unicode_and_spacing_variants_remain_strong_bylines(self):
        names = rules._authors({'authors': ['Élodie Smith', 'Yu Li']})
        self.assertTrue(rules._byline_matches('Elodie  Smith1 and YuLi2', names))
        self.assertTrue(rules._byline_matches('Élodie Smithα', names))

    def test_middle_initial_variant_remains_a_whole_name_byline(self):
        names = rules._authors({'authors': ['James Bradley Wendt']})
        self.assertTrue(rules._byline_matches('James B. Wendt2', names))
        self.assertFalse(rules._byline_matches('James Bad Wendt', names))
        self.assertFalse(rules._byline_matches('James B. Wendt develops a method.', names))

    def test_logo_sized_mixed_scientific_form_near_contact_is_preserved(self):
        manifest = self.make_form()
        self.assertTrue(manifest['forms'][0]['supported'])
        self.assertIsNotNone(manifest['forms'][0]['asset_sha256'])
        evidence = self.ocr(manifest, 'Meta Model Accuracy 95%')
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [evidence])
        self.assertEqual([], result['form_operations'])
        self.assertTrue(result['uncertain'])

    def test_meta_infinity_ocr_alias_is_form_and_source_contact_specific(self):
        manifest = self.make_form()
        form, context = manifest['forms'][0], manifest['source_context']
        for text in ['00 Meta', 'oo Meta', '∞ Meta']:
            observations = [{'text': text, 'confidence': 1, 'bounding_box_normalized': [0, 0, 1, 1]}]
            self.assertEqual('Meta', rules._form_wordmark_text(form, observations, context))
            self.assertFalse(rules._institution_label(text, context))
        result = rules.plan_image_redactions(self.source, self.paper, manifest, [self.ocr(manifest, '00 Meta')])
        self.assertEqual(1, len(result['form_operations']))
        self.assertIn('meta_infinity', result['form_operations'][0]['reason'])

    def test_other_numeric_or_scientific_form_labels_are_not_normalized(self):
        manifest = self.make_form()
        for text in ['000 Meta', '0 Meta', '00 Meta 57.5', 'Meta 00', '00 Metadata',
                     '00 Meta model results', '00 OtherLab']:
            observations = [{'text': text, 'confidence': 1, 'bounding_box_normalized': [0, 0, 1, 1]}]
            self.assertEqual(text, rules._form_wordmark_text(manifest['forms'][0], observations,
                                                           manifest['source_context']))
        observations = [{'text': '00', 'confidence': 1, 'bounding_box_normalized': [0, 0, .2, 1]},
                        {'text': 'Meta', 'confidence': 1, 'bounding_box_normalized': [.3, 0, .7, 1]}]
        self.assertNotEqual('Meta', rules._form_wordmark_text(manifest['forms'][0], observations,
                                                            manifest['source_context']))

    def test_meta_infinity_alias_rejects_missing_distant_or_wrong_domain_anchors(self):
        manifest = self.make_form()
        import copy
        observations = [{'text': '00 Meta', 'confidence': 1, 'bounding_box_normalized': [0, 0, 1, 1]}]
        for mode in ['missing', 'distant', 'suffix_domain', 'affiliation_substring', 'shared', 'oversized']:
            form, context = copy.deepcopy(manifest['forms'][0]), copy.deepcopy(manifest['source_context'])
            if mode == 'missing': context['contact_lines'] = []
            elif mode == 'distant': context['contact_lines'][0]['rect'] = [0, 0, 50, 10]
            elif mode == 'suffix_domain': context['contact_lines'][0]['text'] = 'Correspondence: alice@meta.com.example.org'
            elif mode == 'affiliation_substring': context['affiliation_lines'] = [{'text': 'Metadata Research', 'rect': [60, 125, 230, 140]}]
            elif mode == 'shared': form['invocations'].append(copy.deepcopy(form['invocations'][0]))
            else: form['invocations'][0]['rect'] = [0, 0, 612, 400]
            with self.subTest(mode=mode):
                self.assertEqual('00 Meta', rules._form_wordmark_text(form, observations, context))

    def test_noneligible_form_is_not_decompressed(self):
        from pypdf.generic import ContentStream
        from pypdf.errors import LimitReachedError
        self.make_form(scientific=True)
        def guarded(stream, reader):
            if hasattr(stream, 'get') and stream.get('/Subtype') == '/Form':
                raise LimitReachedError('synthetic decompression limit')
            return ContentStream(stream, reader)
        with patch('pypdf.generic.ContentStream', side_effect=guarded):
            inventory = rules.inspect_source_forms(self.source)
        self.assertFalse(inventory['forms'][0]['supported'])
        self.assertFalse(inventory['forms'][0]['stream_inspected'])
        self.assertNotIn('stream_error', inventory['forms'][0])

    def test_eligible_form_decode_limit_stays_explicitly_unsupported(self):
        from pypdf.generic import ContentStream
        from pypdf.errors import LimitReachedError
        self.make_form()
        def guarded(stream, reader):
            if hasattr(stream, 'get') and stream.get('/Subtype') == '/Form':
                raise LimitReachedError('synthetic decompression limit')
            return ContentStream(stream, reader)
        with patch('pypdf.generic.ContentStream', side_effect=guarded):
            inventory = rules.inspect_source_forms(self.source)
        form = inventory['forms'][0]
        self.assertFalse(form['supported'])
        self.assertFalse(form['stream_inspected'])
        self.assertIn('unsupported_form_stream', form['unsupported_reasons'])
        self.assertIn('LimitReachedError', form['stream_error'])
        self.assertIsNone(form['form_closure_sha256'])

    def test_form_decode_timeout_is_not_swallowed_as_document_uncertainty(self):
        from pypdf.generic import ContentStream
        self.make_form()
        def guarded(stream, reader):
            if hasattr(stream, 'get') and stream.get('/Subtype') == '/Form':
                raise TimeoutError('processing budget expired')
            return ContentStream(stream, reader)
        with patch('pypdf.generic.ContentStream', side_effect=guarded), self.assertRaises(TimeoutError):
            rules.inspect_source_forms(self.source)

    def test_no_process_or_network_invocation(self):
        manifest = self.make()
        evidence = self.ocr(manifest)
        with patch('subprocess.Popen', side_effect=AssertionError('No subprocess')), \
             patch('socket.socket', side_effect=AssertionError('No network')):
            result = rules.plan_image_redactions(self.source, self.paper, manifest, [evidence])
        self.assertEqual(0, result['model_api_calls'])


if __name__ == '__main__':
    unittest.main()

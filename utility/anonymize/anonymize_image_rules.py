"""Optional source-image logo proposals from pinned offline OCR; no PDF edits.

Only isolated, small images supported by source author/affiliation/contact context
can be proposed. Unknowns remain review items. This module never runs OCR/models.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import unicodedata

VERSION = 'source-image-rules-5'
MANIFEST_SCHEMA = 'source-image-manifest-1'
OCR_SCHEMA = 'offline-image-vision-ocr-2'
SHA = re.compile(r'^[0-9a-f]{64}$')
AFFILIATION = re.compile(r'\b(?:university|institute|laborator(?:y|ies)|labs?|department|school|college|academy|research|google|deepmind|meta|microsoft|nvidia|openai|anthropic|ibm|adobe)\b', re.I)
SCIENCE = re.compile(r'\b(?:figure|table|algorithm|theorem|accuracy|performance|model|training|dataset|benchmark|experiment|gradient|equation|results?)\b', re.I)
EMAIL = re.compile(r'[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})')
GENERIC_LABELS = {'research', 'university', 'institute', 'lab', 'labs', 'laboratory', 'department', 'school', 'college'}
OCR_SETTINGS = ('backend', 'recognition_level', 'recognition_languages', 'uses_language_correction',
                'uses_cpu_only', 'bounding_box_format', 'bounding_box_origin', 'image_orientation',
                'request_revision', 'os_version')


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value):
    return _sha(_encoded(value))


def _compact(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    return ''.join(c for c in value if c.isalpha() and not unicodedata.combining(c))


def _overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def _authors(paper):
    values = paper.get('authors') or []
    if isinstance(values, str):
        values = [values]
    names = []
    for value in values:
        if isinstance(value, dict):
            value = value.get('name') or ' '.join(value[k] for k in ('given', 'family') if isinstance(value.get(k), str))
        if isinstance(value, str) and len(_compact(value)) >= 4:
            names.append(' '.join(_words(value)))
    return names



def _byline_matches(text, names):
    value = unicodedata.normalize('NFKD', text).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r'(?<=[a-z])[αβγδεζηθλμνξπρστυφχψω](?=\d|\W|$)', '', value)
    value = ' '.join(_words(value))
    matched = False
    for name in sorted(names, key=len, reverse=True):
        words = name.split()
        parts = [re.escape(word) if index in (0, len(words) - 1) else
                 '(?:' + re.escape(word) + '|' + re.escape(word[0]) + ')'
                 for index, word in enumerate(words)]
        pattern = r'(?<!\w)' + r'\s*'.join(parts) + r'(?!\w)'
        value, count = re.subn(pattern, '', value)
        matched = matched or bool(count)
    return matched and not re.sub(r'\b(?:and|et|al)\b', '', value).strip()


def _context(page, paper):
    import pymupdf as fitz
    lines = []
    for block in page.get_text('dict', flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                lines.append({'text': text, 'rect': list(line['bbox'])})
    titles = [list(rect) for rect in page.search_for(paper.get('title') or '')] if paper.get('title') else []
    starts = [line['rect'][1] for line in lines if re.match(r'^(?:abstract\b|(?:1[.)]?\s*)?introduction\b)', line['text'], re.I)]
    boundary = min(starts) if starts else page.rect.height * .45
    names = _authors(paper)
    author_lines = [line for line in lines if line['rect'][3] <= boundary
                    and not any(_overlap(line['rect'], title) for title in titles)
                    and _byline_matches(line['text'], names)]
    start = min((line['rect'][1] for line in author_lines), default=boundary)
    affiliations = [line for line in lines if start <= line['rect'][1] and line['rect'][3] <= boundary
                    and AFFILIATION.search(line['text']) and not SCIENCE.search(line['text'])
                    and line not in author_lines]
    contacts = [line for line in lines if EMAIL.search(line['text']) and not SCIENCE.search(line['text'])
                and (start <= line['rect'][1] and line['rect'][3] <= boundary
                     or re.match(r'^[\W\d_]*(?:correspondence|corresponding author|e-?mail)\b', line['text'], re.I))]
    return {'title_rects': titles, 'author_lines': author_lines, 'affiliation_lines': affiliations,
            'contact_lines': contacts, 'body_boundary_y': boundary}



def _matrix_product(a, b):
    aa, ab, ac, ad, ae, af = a
    ba, bb, bc, bd, be, bf = b
    return [aa * ba + ac * bb, ab * ba + ad * bb, aa * bc + ac * bd, ab * bc + ad * bd,
            aa * be + ac * bf + ae, ab * be + ad * bf + af]


def inspect_source_forms(source_pdf):
    """Inspect small unique top-level Forms; shared/nested/ambiguous Forms fail closed.

    Dictionary hashes use UTF-8 xref_object(compressed=True). Closure hashes
    fingerprint sorted xref/generation/object/encoded-stream hash records.
    top_level_do_index counts every top-level Do, including image operations.
    """
    import pymupdf as fitz
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, IndirectObject

    source = Path(source_pdf).resolve(strict=True)
    source_hash = _sha(source.read_bytes())
    reader, forms, resource_uses, records_cache, form_references = PdfReader(source), {}, {}, {}, {}
    with fitz.open(source) as doc:
        nested_xrefs = {entry[0] for page in doc for entry in page.get_xobjects() if entry[2] != 0}

        def closure(reference):
            seen = set()
            def visit(value):
                if isinstance(value, IndirectObject):
                    key = (value.idnum, value.generation)
                    if key in seen:
                        return
                    seen.add(key)
                    if key not in records_cache:
                        stream = doc.xref_stream_raw(value.idnum)
                        records_cache[key] = {'xref': value.idnum, 'generation': value.generation,
                                              'object_sha256': _sha(doc.xref_object(value.idnum, compressed=True).encode()),
                                              'stream_sha256': _sha(stream) if stream is not None else None}
                    visit(value.get_object())
                elif isinstance(value, DictionaryObject):
                    for name in value:
                        visit(value.raw_get(name))
                elif isinstance(value, ArrayObject):
                    for item in value:
                        visit(item)
            visit(reference)
            return [records_cache[key] for key in sorted(seen)]

        for number, page in enumerate(reader.pages, 1):
            resources = page.get('/Resources', {})
            resources = resources.get_object() if hasattr(resources, 'get_object') else resources
            xobjects = resources.get('/XObject', {})
            xobjects = xobjects.get_object() if hasattr(xobjects, 'get_object') else xobjects
            for name in xobjects:
                ref = xobjects.raw_get(name)
                if isinstance(ref, IndirectObject) and ref.get_object().get('/Subtype') == '/Form':
                    resource_uses.setdefault(ref.idnum, []).append([number, str(name)])
            state, stack, do_index = ([1., 0., 0., 1., 0., 0.], False, False), [], 0
            for arguments, operator in ContentStream(page.get_contents(), reader).operations:
                if operator == b'q':
                    stack.append((list(state[0]), state[1], state[2]))
                elif operator == b'Q':
                    state = stack.pop() if stack else (state[0], True, True)
                elif operator == b'cm':
                    state = (_matrix_product(state[0], [float(x) for x in arguments]), state[1], state[2])
                elif operator in (b'W', b'W*'):
                    state = (state[0], True, state[2])
                elif operator == b'gs':
                    state = (state[0], state[1], True)
                elif operator == b'Do':
                    ordinal = do_index
                    do_index += 1
                    if len(arguments) != 1 or arguments[0] not in xobjects:
                        continue
                    ref = xobjects.raw_get(arguments[0])
                    if not isinstance(ref, IndirectObject) or ref.get_object().get('/Subtype') != '/Form':
                        continue
                    obj = ref.get_object()
                    name = str(arguments[0]).removeprefix('/')
                    if ref.idnum not in forms:
                        form_references[ref.idnum] = ref
                        raw = doc.xref_stream_raw(ref.idnum)
                        child_resources = obj.get('/Resources', {})
                        child_resources = child_resources.get_object() if hasattr(child_resources, 'get_object') else child_resources
                        children = child_resources.get('/XObject', {})
                        children = children.get_object() if hasattr(children, 'get_object') else children
                        nested = any(children[key].get_object().get('/Subtype') == '/Form' for key in children)
                        forms[ref.idnum] = {'source_xref': ref.idnum, 'generation': ref.generation,
                                           'form_sha256': _sha(raw),
                                           'form_dictionary_sha256': _sha(doc.xref_object(ref.idnum, compressed=True).encode()),
                                           'form_closure_sha256': None, 'closure_objects': [],
                                           'invocations': [], 'unsupported_reasons': [], 'stream_inspected': False}
                        if nested or ref.idnum in nested_xrefs:
                            forms[ref.idnum]['unsupported_reasons'].append('nested_form_resources_or_invocation')
                    form = forms[ref.idnum]
                    bbox = [float(x) for x in obj.get('/BBox', [])]
                    matrix = [float(x) for x in obj.get('/Matrix', [1, 0, 0, 1, 0, 0])]
                    if len(bbox) != 4 or len(matrix) != 6 or not all(math.isfinite(x) for x in bbox + matrix + state[0]):
                        form['unsupported_reasons'].append('invalid_form_geometry')
                        continue
                    combined = _matrix_product(state[0], matrix)
                    a, b, c, d, tx, ty = combined
                    points = [(a * x + c * y + tx, b * x + d * y + ty)
                              for x, y in ((bbox[0], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[1]), (bbox[2], bbox[3]))]
                    crop = [float(x) for x in page.cropbox]
                    unit = float(page.get('/UserUnit', 1))
                    rect = [(min(x for x, y in points) - crop[0]) * unit,
                            (crop[3] - max(y for x, y in points)) * unit,
                            (max(x for x, y in points) - crop[0]) * unit,
                            (crop[3] - min(y for x, y in points)) * unit]
                    bounds = list(doc[number - 1].rect * doc[number - 1].derotation_matrix)
                    invocation = {'page': number, 'resource_name': name, 'top_level_do_index': ordinal,
                                  'rect': rect, 'ctm': state[0], 'form_matrix': matrix,
                                  'form_bbox': bbox, 'page_bounds': bounds, 'page_rotation': doc[number - 1].rotation}
                    form['invocations'].append(invocation)
                    if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                        form['unsupported_reasons'].append('unsupported_resource_name')
                    if state[1] or state[2]:
                        form['unsupported_reasons'].append('external_clip_or_graphics_state')
                    if abs(b) > 1e-9 or abs(c) > 1e-9 or a <= 0 or d <= 0 or doc[number - 1].rotation or unit != 1:
                        form['unsupported_reasons'].append('rotated_sheared_or_scaled_user_unit')
                    width, height = rect[2] - rect[0], rect[3] - rect[1]
                    if (number != 1 or width <= 0 or height <= 0 or height > 36 or width > (bounds[2] - bounds[0]) * .4
                            or width * height > 6500 or rect[0] < bounds[0] or rect[1] < bounds[1] or rect[2] > bounds[2] or rect[3] > bounds[3]):
                        form['unsupported_reasons'].append('not_small_complete_first_page_form')
        for form in forms.values():
            if len(form['invocations']) != 1 or len(resource_uses.get(form['source_xref'], [])) != 1:
                form['unsupported_reasons'].append('shared_or_nonunique_form')
            form['resource_uses'] = resource_uses.get(form['source_xref'], [])
            # Noncandidate body/large/shared Forms do not need stream decoding.
            # A malformed/over-limit candidate stays unsupported; never raise a
            # global decoder limit or turn an unreadable stream into an approval.
            if not form['unsupported_reasons']:
                try:
                    obj = form_references[form['source_xref']].get_object()
                    form_ops = ContentStream(obj, reader).operations
                    form['stream_inspected'] = True
                    if any(op in (b'BT', b'Tj', b'TJ', b"'", b'"') for _, op in form_ops):
                        form['unsupported_reasons'].append('text_bearing_form_requires_separate_review')
                    else:
                        form['closure_objects'] = closure(form_references[form['source_xref']])
                        form['form_closure_sha256'] = _fingerprint(form['closure_objects'])
                except (PyPdfError, ValueError) as exc:
                    form['unsupported_reasons'].append('unsupported_form_stream')
                    form['stream_error'] = f'{type(exc).__name__}: {exc}'
            form['unsupported_reasons'] = sorted(set(form['unsupported_reasons']))
            form['supported'] = not form['unsupported_reasons']
    if _sha(source.read_bytes()) != source_hash:
        raise ValueError('Source changed during Form inventory')
    return {'source_pdf_sha256': source_hash, 'forms': list(forms.values())}


def _form_assets(source, context):
    import pymupdf as fitz
    result = inspect_source_forms(source)
    assets = {}
    with fitz.open(source) as doc:
        for form in result['forms']:
            form['object_type'] = 'form'
            form['image_id'] = result['source_pdf_sha256'][:16] + '-f' + str(form['source_xref'])
            form['asset_file'] = form['image_id'] + '.png'
            form['asset_sha256'] = None
            form['width'], form['height'] = 0, 0
            form['ocr_candidate'] = False
            if not form['supported']:
                continue
            place = form['invocations'][0]
            if _placement_context(place, context) is None:
                continue
            rect = fitz.Rect(place['rect'])
            page = doc[place['page'] - 1]
            if any(rect.intersects(fitz.Rect(word[:4])) for word in page.get_text('words')):
                form['unsupported_reasons'].append('form_bbox_overlaps_source_text')
                form['supported'] = False
                continue
            scale = min(16., max(4., 64. / rect.height))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=True, annots=False)
            png = pix.tobytes('png')
            form.update(asset_sha256=_sha(png), width=pix.width, height=pix.height, ocr_candidate=True,
                        render_scale=scale, render_clip=place['rect'])
            assets[form['image_id']] = png
    return result['forms'], assets


def _inventory(source, paper, include_forms=True):
    import pymupdf as fitz
    before = _sha(source.read_bytes())
    images, assets, uncertain = {}, {}, []
    with fitz.open(source) as doc:
        if doc.needs_pass or not doc.page_count:
            raise ValueError('Encrypted or empty source PDF is unsupported')
        context = _context(doc[0], paper)
        for number, page in enumerate(doc, 1):
            for info in page.get_image_info(xrefs=True):
                xref = info.get('xref', 0)
                if not xref:
                    uncertain.append(f'Page {number}: inline image cannot be bound to a source xref.')
                    continue
                if xref not in images:
                    raw = doc.xref_stream_raw(xref)
                    if raw is None:
                        raise ValueError('Source image lacks an encoded stream')
                    mask_kind, mask_value = doc.xref_get_key(xref, 'SMask')
                    smask = int(mask_value.split()[0]) if mask_kind == 'xref' else 0
                    image_id = before[:16] + '-x' + str(xref)
                    images[xref] = {'image_id': image_id, 'source_xref': xref,
                                    'image_sha256': _sha(raw), 'smask_xref': smask,
                                    'smask_sha256': _sha(doc.xref_stream_raw(smask)) if smask else None,
                                    'asset_file': image_id + '.png', 'asset_sha256': None,
                                    'width': info['width'], 'height': info['height'], 'placements': []}
                images[xref]['placements'].append({'page': number, 'rect': list(info['bbox']),
                                                  'transform': list(info['transform']),
                                                  'page_bounds': list(page.rect * page.derotation_matrix),
                                                  'page_rotation': page.rotation})
        for image in images.values():
            image['ocr_candidate'] = any(_placement_context(place, context) for place in image['placements'])
            if not image['ocr_candidate']:
                continue
            if image['width'] * image['height'] > 32_000_000:
                uncertain.append(f"Image {image['image_id']}: exceeds the 32-million-pixel OCR asset limit.")
                continue
            pix = fitz.Pixmap(doc, image['source_xref'])
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            if image['smask_xref']:
                pix = fitz.Pixmap(pix, fitz.Pixmap(doc, image['smask_xref']))
            asset = pix.tobytes('png')
            if len(asset) > 256 * 1024 * 1024:
                uncertain.append(f"Image {image['image_id']}: exceeds the 256-MiB OCR asset limit.")
                continue
            image['asset_sha256'] = _sha(asset)
            assets[image['image_id']] = asset
    if _sha(source.read_bytes()) != before:
        raise ValueError('Source PDF changed during image inventory')
    records = list(images.values())
    for image in records:
        image['placements_sha256'] = _fingerprint(image['placements'])
    manifest = {'schema_version': MANIFEST_SCHEMA, 'source_pdf_sha256': before,
                'paper_metadata_sha256': _fingerprint(paper), 'pymupdf_version': fitz.VersionBind,
                'source_context': context, 'images': records, 'uncertain': uncertain}
    if include_forms:
        forms, form_assets = _form_assets(source, context)
        manifest.update(schema_version='source-image-manifest-2', forms=forms)
        assets.update(form_assets)
    return manifest, assets


def extract_source_images(source_pdf, paper, output_dir):
    """Export exact source-derived PNG assets and manifest into a fresh directory."""
    source = Path(source_pdf).resolve(strict=True)
    output = Path(output_dir).absolute()
    if output.exists() or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('Use a fresh image output directory without symlinks')
    manifest, assets = _inventory(source, paper)
    output.mkdir(parents=True)
    for image in manifest['images'] + manifest.get('forms', []):
        if image['image_id'] in assets:
            (output / image['asset_file']).write_bytes(assets[image['image_id']])
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return manifest



def _bundle_path(parent, relative):
    path = Path(relative)
    if path.is_absolute() or not (parent / path).resolve().is_relative_to(parent.resolve()):
        raise ValueError('OCR runtime/source file must be bundle-relative')
    return parent / path


def write_ocr_receipt(jsonl, helper_binary, helper_source):
    """Bind a native OCR output to adjacent manifest and copied runtime; stdlib only."""
    path = Path(jsonl).resolve(strict=True)
    manifest_path = path.with_name(path.stem + '.manifest.json')
    receipt_path = path.with_name(path.stem + '.receipt.json')
    if receipt_path.exists():
        raise ValueError('OCR receipt already exists; use a fresh bundle')
    rows = [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text())
    entries = {item['id']: item for item in manifest}
    if not rows or len(entries) != len(manifest):
        raise ValueError('OCR bundle requires unique manifest IDs and result rows')
    binary_hash = _sha(Path(helper_binary).read_bytes())
    source_hash = _sha(Path(helper_source).read_bytes())
    manifest_hash = _sha(manifest_path.read_bytes())
    settings = {k: rows[0][k] for k in OCR_SETTINGS}
    for row in rows:
        entry = entries.get(row.get('id'))
        if (entry is None or row.get('path') != entry['path']
                or row.get('schema_version') != OCR_SCHEMA or row.get('error') is not None
                or row.get('expected_png_sha256') != entry['sha256']
                or row.get('verified_png_sha256') != entry['sha256']
                or row.get('manifest_sha256') != manifest_hash
                or row.get('helper_executable_sha256') != binary_hash
                or {k: row.get(k) for k in OCR_SETTINGS} != settings):
            raise ValueError('Native OCR row does not match the actual bundle inputs/runtime')
    binary_file, source_file = 'runtime-' + binary_hash[:16], 'source-' + source_hash[:16] + '.swift'
    for original, name, expected in ((helper_binary, binary_file, binary_hash), (helper_source, source_file, source_hash)):
        target = path.parent / name
        if target.exists():
            if _sha(target.read_bytes()) != expected:
                raise ValueError('Existing OCR bundle runtime/source file has different bytes')
        else:
            shutil.copyfile(original, target)
    receipt = {'schema_version': 'source-image-ocr-receipt-1', 'output_sha256': _sha(path.read_bytes()),
               'manifest_sha256': manifest_hash, 'helper_executable_sha256': binary_hash,
               'helper_executable_file': binary_file, 'helper_source_sha256': source_hash,
               'helper_source_file': source_file, 'settings': settings, 'settings_sha256': _fingerprint(settings)}
    with receipt_path.open('x') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    return receipt_path


def _load_ocr_bundle(path):
    raw = path.read_bytes()
    manifest_path, receipt_path = path.with_name(path.stem + '.manifest.json'), path.with_name(path.stem + '.receipt.json')
    manifest, receipt = json.loads(manifest_path.read_text()), json.loads(receipt_path.read_text())
    if (receipt.get('schema_version') != 'source-image-ocr-receipt-1'
            or receipt.get('output_sha256') != _sha(raw)
            or receipt.get('manifest_sha256') != _sha(manifest_path.read_bytes())
            or receipt.get('settings_sha256') != _fingerprint(receipt.get('settings'))):
        raise ValueError('OCR bundle manifest/output/settings binding failed')
    for prefix in ('helper_executable', 'helper_source'):
        actual = _sha(_bundle_path(path.parent, receipt[prefix + '_file']).read_bytes())
        if actual != receipt[prefix + '_sha256']:
            raise ValueError('OCR bundle runtime/source hash mismatch')
    if not isinstance(manifest, list) or any(not isinstance(item, dict) for item in manifest):
        raise ValueError('OCR manifest must be a list of bound input records')
    entries = {item['id']: item for item in manifest}
    if len(entries) != len(manifest):
        raise ValueError('Duplicate OCR manifest image IDs')
    return raw, entries, receipt, _sha(receipt_path.read_bytes())


def _ocr_rows(paths, images):
    rows, files = {}, []
    for value in paths:
        path = Path(value)
        raw, entries, bundle, receipt_hash = _load_ocr_bundle(path)
        file_hash = _sha(raw)
        files.append({'path': str(path.resolve()), 'sha256': file_hash, 'receipt_sha256': receipt_hash,
                      'manifest_sha256': bundle['manifest_sha256'],
                      'helper_executable_sha256': bundle['helper_executable_sha256'],
                      'helper_source_sha256': bundle['helper_source_sha256']})
        for line_number, line in enumerate(raw.splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError('OCR row must be an object')
            key = row.get('id')
            if key not in images:
                continue
            image = images[key]
            entry = entries.get(key)
            if (entry is None or entry.get('sha256') != image['asset_sha256']
                    or row.get('path') != entry.get('path')
                    or row.get('manifest_sha256') != bundle['manifest_sha256']
                    or row.get('helper_executable_sha256') != bundle['helper_executable_sha256']
                    or _fingerprint({k: row.get(k) for k in OCR_SETTINGS}) != bundle['settings_sha256']):
                raise ValueError('OCR row is not bound to the actual source asset/manifest/runtime/settings')
            if row.get('schema_version') != OCR_SCHEMA or 'error' not in row or row['error'] is not None:
                raise ValueError('Unsupported or failed image OCR row')
            for name in ('expected_png_sha256', 'verified_png_sha256', 'manifest_sha256',
                         'helper_executable_sha256', 'decoded_pixel_sha256'):
                if not isinstance(row.get(name), str) or not SHA.fullmatch(row[name]):
                    raise ValueError('Missing image OCR hash: ' + name)
            if row['expected_png_sha256'] != image['asset_sha256'] or row['verified_png_sha256'] != image['asset_sha256']:
                raise ValueError('OCR PNG does not match the original source asset')
            if any(type(row.get(k)) is not int or row[k] != image[k] for k in ('width', 'height')):
                raise ValueError('Image OCR dimensions disagree with the source asset')
            expected = {'backend': 'Apple Vision VNRecognizeTextRequest', 'recognition_level': 'accurate',
                        'recognition_languages': ['en-US'], 'uses_language_correction': False,
                        'uses_cpu_only': True, 'bounding_box_format': 'x,y,width,height',
                        'bounding_box_origin': 'lower_left', 'image_orientation': 'up', 'request_revision': 3}
            if any(type(row.get(k)) is not type(v) or row[k] != v for k, v in expected.items()) or not isinstance(row.get('os_version'), str) or not row['os_version']:
                raise ValueError('Unsupported image OCR settings')
            observations = row.get('observations')
            if not isinstance(observations, list) or type(row.get('observation_count')) is not int or row['observation_count'] != len(observations) or type(row.get('text_detected')) is not bool or row['text_detected'] != bool(observations):
                raise ValueError('Malformed image OCR observation summary')
            for obs in observations:
                if not isinstance(obs, dict) or not isinstance(obs.get('text'), str) or not obs['text']:
                    raise ValueError('Malformed image OCR text')
                confidence, box = obs.get('confidence'), obs.get('bounding_box_normalized')
                if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                    raise ValueError('Malformed image OCR confidence')
                if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) for x in box) or min(box[:2]) < -1e-6 or min(box[2:]) <= 0 or box[0] + box[2] > 1.000001 or box[1] + box[3] > 1.000001:
                    raise ValueError('Malformed image OCR rectangle')
            receipt = {'row': row, 'file_sha256': file_hash, 'line_one_based': line_number,
                       'raw_row_sha256': _sha(line), 'bundle_receipt_sha256': receipt_hash, 'settings_sha256': _fingerprint({k: row[k] for k in OCR_SETTINGS})}
            if key in rows and rows[key]['row'] != row:
                raise ValueError('Conflicting OCR rows for one source image')
            rows[key] = receipt
    return rows, files


def _words(text):
    text = unicodedata.normalize('NFKD', text).casefold()
    return re.findall(r'[^\W\d_]+', ''.join(c for c in text if not unicodedata.combining(c)))



def _wordmark_text(observations):
    if not observations:
        return ''
    main = max(observations, key=lambda obs: obs['bounding_box_normalized'][2] * obs['bounding_box_normalized'][3])
    mx, my, mw, mh = main['bounding_box_normalized']
    kept = []
    for obs in observations:
        x, y, width, height = obs['bounding_box_normalized']
        # A separate tiny, upper-right registered/trademark mark may be read as
        # TM or even a literal trademark glyph plus M. Never drop arbitrary
        # extra letters or an inscription elsewhere in the asset.
        insignia = (obs is not main and re.fullmatch(r'\s*(?:[™®©][TM]?|TM)\s*', obs['text'], re.I)
                    and width * height <= mw * mh * .05 and height <= mh * .25
                    and x >= mx + mw * .85 and y + height / 2 >= my + mh * .75)
        if not insignia:
            kept.append(obs['text'])
    return ' '.join(kept)


def _institution_label(text, context):
    text = re.sub(r'[™®©]|\bTM\b', '', text, flags=re.I).strip()
    label = ''.join(_words(text))
    if not 3 <= len(label) <= 100 or label in GENERIC_LABELS or SCIENCE.search(text) or re.search(r'\d|[%=∞]', text):
        return False
    for line in context['affiliation_lines']:
        words = _words(line['text'])
        # Complete token sequences permit a joined OCR wordmark such as
        # GoogleDeepMind, never a substring such as MIT inside Smith.
        if any(label == ''.join(words[start:end]) for start in range(len(words))
               for end in range(start + 1, len(words) + 1)):
            return True
        for clause in re.split(r'[-,;]|\s+and\s+', line['text'], flags=re.I):
            acronym = ''.join(word[0] for word in _words(clause) if word not in {'of', 'the', 'and', 'at', 'for'})
            if len(acronym) >= 3 and label == acronym:
                return True
    return False


def _near_rows(placement, context):
    rect = placement['rect']
    rows = []
    for kind in ('author_lines', 'affiliation_lines', 'contact_lines'):
        for line in context[kind]:
            box = line['rect']
            horizontal_gap = max(rect[0] - box[2], box[0] - rect[2], 0)
            vertical_gap = max(rect[1] - box[3], box[1] - rect[3], 0)
            if vertical_gap <= 5 and horizontal_gap <= max(12, 2 * (box[3] - box[1])):
                rows.append((kind, line))
    return rows


def _row_anchor_keys(placement, context):
    # Separate text fields on one baseline are one physical row, not evidence
    # of repetition. Merge overlapping vertical text bands across columns.
    groups, mapping = [], {}
    lines = [(kind, line) for kind in ('author_lines', 'affiliation_lines') for line in context[kind]]
    for kind, line in sorted(lines, key=lambda item: item[1]['rect'][1]):
        y0, y1 = line['rect'][1], line['rect'][3]
        if not groups or y0 >= groups[-1][1]:
            groups.append([y0, y1])
        else:
            groups[-1][1] = max(groups[-1][1], y1)
        mapping[_fingerprint({'kind': kind, 'line': line})] = len(groups) - 1
    return {mapping[_fingerprint({'kind': kind, 'line': line})] for kind, line in _near_rows(placement, context)
            if kind in {'author_lines', 'affiliation_lines'}}


def _placement_context(placement, context):
    rect, bounds = placement['rect'], placement['page_bounds']
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if (placement['page'] != 1 or width <= 0 or height <= 0 or height > 36
            or width > (bounds[2] - bounds[0]) * .4 or width * height > 6500
            or rect[0] < bounds[0] or rect[1] < bounds[1] or rect[2] > bounds[2] or rect[3] > bounds[3]
            or not context['author_lines'] or not context['affiliation_lines']):
        return None
    if context['title_rects'] and rect[3] <= min(box[1] for box in context['title_rects']):
        return 'above_source_title'
    near = _near_rows(placement, context)
    return near[0][0] if near else None




def _placement_row_count(placements):
    bands = []
    for place in sorted(placements, key=lambda p: p['rect'][1]):
        y0, y1 = place['rect'][1], place['rect'][3]
        if not bands or y0 >= bands[-1][1]:
            bands.append([y0, y1])
        else:
            bands[-1][1] = max(bands[-1][1], y1)
    return len(bands)


def _paired_icon(icon, wordmark):
    if icon['page'] != wordmark['page']:
        return False
    a, b = icon['rect'], wordmark['rect']
    ah, bh = a[3] - a[1], b[3] - b[1]
    aw, bw = a[2] - a[0], b[2] - b[0]
    gap = max(a[0], b[0]) - min(a[2], b[2])
    return (0 <= gap <= max(ah, bh) * .5 and abs((a[1] + a[3]) - (b[1] + b[3])) <= max(ah, bh)
            and aw * ah <= bw * bh and aw <= 2 * ah and ah <= 1.5 * bh)



def _form_wordmark_text(form, observations, context):
    text = _wordmark_text(observations)
    # The Meta infinity mark can be recognized as "00" or "oo". This is a
    # whole-label, single-observation exception for a bound isolated Form next
    # to its source Meta correspondence, never a general numeric-label rule.
    if (form.get('supported') and len(form.get('invocations', [])) == 1
            and len(observations) == 1 and re.fullmatch(r'(?:00|oo|∞)\s+Meta', text.strip(), re.I)
            and _institution_label('Meta', context)):
        place = form['invocations'][0]
        if _placement_context(place, context) is not None:
            for kind, line in _near_rows(place, context):
                domains = re.findall(r'@([^\s<>,;:()\[\]{}]+)', line['text'])
                if kind == 'contact_lines' and any(domain.rstrip('.').casefold() == 'meta.com' for domain in domains):
                    return 'Meta'
    return text


def plan_image_redactions(source_pdf, paper, manifest, ocr_evidence=()):
    """Propose per-placement image operations; missing/unknown OCR stays uncertain."""
    source = Path(source_pdf).resolve(strict=True)
    if isinstance(manifest, (str, Path)):
        manifest = json.loads(Path(manifest).read_text())
    current, _ = _inventory(source, paper, include_forms=manifest.get('schema_version') == 'source-image-manifest-2')
    if manifest != current:
        raise ValueError('Source image manifest or metadata does not match the original PDF')
    paths = [ocr_evidence] if isinstance(ocr_evidence, (str, Path)) else list(ocr_evidence)
    images = {image['image_id']: image for image in current['images'] + current.get('forms', [])}
    rows, files = _ocr_rows(paths, images)
    operations, uncertain = [], list(current['uncertain'])
    placements = [(image, placement) for image in current['images'] for placement in image['placements']]
    accepted, pending = [], []

    def add(image, placement, row, context, companion=None):
        proof = {'image': image, 'placement': placement, 'ocr': row,
                 'source_context': current['source_context'], 'context_rule': context,
                 'paired_wordmark_evidence': companion}
        operations.append({'page': placement['page'], 'rect': placement['rect'],
                           'source_xref': image['source_xref'], 'image_sha256': image['image_sha256'],
                           'source_pdf_sha256': current['source_pdf_sha256'], 'category': 'affiliation',
                           'reason': context, 'evidence_sha256': _fingerprint(proof)})

    for image, placement in placements:
        prefix = f"Page {placement['page']}, image {image['image_id']}"
        row = rows.get(image['image_id'])
        context = _placement_context(placement, current['source_context'])
        overlap = any(other is not placement and other['page'] == placement['page'] and _overlap(placement['rect'], other['rect']) for _, other in placements)
        if row is None or context is None or overlap:
            uncertain.append(prefix + ': no bound, isolated source-author-region OCR evidence; image preserved.')
            continue
        observations = row['row']['observations']
        text = _wordmark_text(observations)
        if observations and all(obs['confidence'] >= .7 for obs in observations) and _institution_label(text, current['source_context']):
            add(image, placement, row, 'source_affiliation_wordmark:' + context)
            accepted.append((image, placement, row, context))
        else:
            pending.append((image, placement, row, context))
    for image, placement, row, context in pending:
        # A blank icon is accepted only when the same icon/wordmark pair repeats
        # on at least two explicit author/affiliation rows. Scientific body
        # figures and singleton unknown icons never inherit a wordmark's label.
        companion = None
        if not row['row']['observations'] and context in {'author_lines', 'affiliation_lines'}:
            for word_image, word_place, word_row, word_context in accepted:
                if word_context not in {'author_lines', 'affiliation_lines'} or not _paired_icon(placement, word_place):
                    continue
                pairs = [(p, w) for i, p, r, c in pending if i['image_id'] == image['image_id'] and not r['row']['observations'] and c in {'author_lines', 'affiliation_lines'}
                         for wi, w, wr, wc in accepted if wi['image_id'] == word_image['image_id'] and wc in {'author_lines', 'affiliation_lines'} and _paired_icon(p, w) and _row_anchor_keys(p, current['source_context']) & _row_anchor_keys(w, current['source_context'])]
                anchor_rows = set().union(*(_row_anchor_keys(p, current['source_context']) & _row_anchor_keys(w, current['source_context']) for p, w in pairs)) if pairs else set()
                if (len({_fingerprint(p) for p, _ in pairs}) >= 2
                        and len({_fingerprint(w) for _, w in pairs}) >= 2 and len(anchor_rows) >= 2
                        and _placement_row_count([p for p, _ in pairs]) >= 2
                        and _placement_row_count([w for _, w in pairs]) >= 2):
                    companion = {'wordmark_image': word_image, 'wordmark_ocr': word_row, 'repeated_pairs': pairs}
                    break
        if companion:
            add(image, placement, row, 'repeated_source_affiliation_wordmark_icon_cluster', companion)
        else:
            uncertain.append(f"Page {placement['page']}, image {image['image_id']}: no unambiguous source-affiliation logo match; image preserved.")
    form_operations = []
    for form in current.get('forms', []):
        prefix = 'Form ' + form['image_id']
        row = rows.get(form['image_id'])
        if not form['supported'] or not form['asset_sha256'] or row is None:
            uncertain.append(prefix + ': unsupported placement or missing bound Form OCR; preserved.')
            continue
        place = form['invocations'][0]
        observations = row['row']['observations']
        if (not observations or any(obs['confidence'] < .7 for obs in observations)
                or not _institution_label(_form_wordmark_text(form, observations, current['source_context']), current['source_context'])):
            uncertain.append(prefix + ': no unambiguous source-affiliation wordmark; preserved.')
            continue
        if any(other['page'] == place['page'] and _overlap(other['rect'], place['rect']) for _, other in placements):
            uncertain.append(prefix + ': Form overlaps another image placement; preserved.')
            continue
        if any(other is not form and p['page'] == place['page'] and _overlap(p['rect'], place['rect'])
               for other in current.get('forms', []) for p in other['invocations']):
            uncertain.append(prefix + ': Form overlaps another Form; preserved.')
            continue
        form_operations.append({'object_type': 'form', 'page': place['page'], 'rect': place['rect'],
                                'source_xref': form['source_xref'], 'source_pdf_sha256': current['source_pdf_sha256'],
                                'form_sha256': form['form_sha256'], 'form_dictionary_sha256': form['form_dictionary_sha256'],
                                'form_closure_sha256': form['form_closure_sha256'], 'resource_name': place['resource_name'],
                                'top_level_do_index': place['top_level_do_index'], 'category': 'affiliation',
                                'reason': ('source_affiliation_isolated_form:meta_infinity_wordmark'
                                           if _form_wordmark_text(form, observations, current['source_context']) != _wordmark_text(observations)
                                           else 'source_affiliation_isolated_form'),
                                'evidence_sha256': _fingerprint({'form': form, 'ocr': row, 'context': current['source_context']})})
    if _sha(source.read_bytes()) != current['source_pdf_sha256']:
        raise ValueError('Source changed during image planning')
    return {'schema_version': 'source-image-plan-1', 'binding': {
                'source_pdf_sha256': current['source_pdf_sha256'], 'paper_metadata_sha256': current['paper_metadata_sha256'],
                'manifest_sha256': _fingerprint(current), 'planner_sha256': _sha(Path(__file__).read_bytes()),
                'planner_version': VERSION, 'pymupdf_version': current['pymupdf_version'], 'ocr_files': files},
            'image_operations': operations, 'form_operations': form_operations, 'uncertain': uncertain,
            'ready_for_blind_review': False, 'model_api_calls': 0, 'codex_cli_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['extract', 'plan'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--ocr-evidence', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    paper = json.loads(args.metadata.read_text())
    if args.command == 'extract':
        if args.output_dir is None:
            parser.error('extract requires --output-dir')
        result = extract_source_images(args.source, paper, args.output_dir)
        print(json.dumps({'manifest': str(args.output_dir / 'manifest.json'), 'images': len(result['images'])}))
    else:
        if args.manifest is None or args.output is None or args.output.exists():
            parser.error('plan requires --manifest and a fresh --output')
        result = plan_image_redactions(args.source, paper, args.manifest, args.ocr_evidence)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'image_operations': len(result['image_operations']), 'uncertain': len(result['uncertain'])}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

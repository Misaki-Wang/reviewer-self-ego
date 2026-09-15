"""Conservative layout- and metadata-based identity proposals, without model calls.

The plan is a review candidate. Unrecognized layouts and non-text identities are
reported explicitly; absence of a detection is never an anonymity certificate.
"""
from pathlib import Path
import re
import unicodedata

RULES_VERSION = 'anonymize-rules-6'
LIMITATIONS = (
    'Rules are not a visual or semantic anonymity audit.',
    'Images, institutional logos, obfuscated identifiers and self-identifying prose may remain.',
    'Ordinary bibliography entries and scientific body text are intentionally preserved.',
)

EMAIL = re.compile(r'(?:\{[\w.+,@\s-]+\}|[\w.+-]+)@[\w.-]+\.[A-Za-z]{2,}')
URL = re.compile(r'(?:https?://|www\.)[^\s<>]+', re.I)
ORCID = re.compile(r'(?:orcid(?:\.org)?\s*[:/]?\s*)?\b\d{4}-\d{4}-\d{4}-[\dX]{4}\b', re.I)
ARXIV = re.compile(r'\barxiv\s*:\s*(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?', re.I)
DOI = re.compile(r'(?:doi\s*:\s*|https?://doi\.org/)10\.\d{4,9}/\S+', re.I)
AFFILIATION = re.compile(
    r'(?<![^\W\d_])(?:university|universit[aey]|institute|institution|department|laborator(?:y|ies)|'
    r'college|school|research cent(?:er|re)|academy|google|deepmind|microsoft|'
    r'meta ai|nvidia|openai|anthropic|amazon|huawei|bytedance|alibaba)\b', re.I)
VENUE = re.compile(
    r'^(?:published as a (?:conference )?paper at|under review (?:as|at|by)|'
    r'submitted to|accepted (?:by|at|for)|to appear (?:at|in)|'
    r'proceedings of|copyright|©|preprint(?:[.\s]|$))', re.I)
FOOTNOTE = re.compile(
    r'^(?:[\d*†‡§¶♠♣♥♦◊.,\s]+)?(?:equal contributions?\b|'
    r'co[- ]first authors?\b|co[- ](?:senior|last) authors?\b|'
    r'corresponding authors?\s*[:.*†‡]|correspondence\s*(?:to|:)\s*|'
    r'(?:this )?work (?:was )?(?:done|performed)\b|'
    r'(?:project (?:lead|leader)|research intern|internship)\s*[:.]|'
    r'(?:these )?authors contributed equally\b)', re.I)
OWNED_LINK = re.compile(
    r'^(?:[\d*†‡\s]+)?(?:code|project(?: page| website)?|'
    r'(?:our |the )?(?:source )?code(?: and (?:data|models?))?'
    r'(?: (?:is |are )?(?:available|released|can be found)(?: at| on)?)?)\s*[:.]?\s*$', re.I)
SCIENCE_HEADING = {'introduction', 'conclusion', 'conclusions', 'discussion',
                   'limitations', 'ethical considerations', 'methods', 'method',
                   'methodology', 'experiments', 'experimental details', 'results',
                   'related work', 'appendix', 'appendices', 'broader impact'}


def _normal(text):
    text = unicodedata.normalize('NFKD', text).casefold()
    text = re.sub(r'(?<=[^\W\d_])(?=\d)', ' ', text)
    return ' '.join(re.findall(r'[^\W_]+', ''.join(
        c for c in text if not unicodedata.combining(c))))


def _heading(text, names):
    value = re.sub(r'^(?:\d+|[a-z])\s+', '', _normal(text))
    return value in names


def _body_start(text):
    value = _normal(text)
    return value == 'abstract' or value.startswith('abstract ') or _heading(text, {'introduction'})


def _reference_heading(text):
    return _heading(text, {'references', 'bibliography'})


def _ack_heading(text):
    return _heading(text, {'acknowledgments', 'acknowledgements', 'acknowledgment',
                          'acknowledgement', 'funding', 'funding statement',
                          'acknowledgments and disclosure of funding',
                          'acknowledgements and disclosure of funding'})


def _section_heading(line):
    value = line['text'].strip()
    return (_reference_heading(value) or _heading(value, SCIENCE_HEADING) or
            bool(re.fullmatch(r'Appendi(?:x|ces)(?:[.\s:]+[^.!?]{0,100})?', value, re.I)) or
            bool(re.fullmatch(r'(?:\d{1,2}(?:\.\d{1,2})*|[A-Z])[.)]?\s+[A-Z][^.!?;]{0,80}', value)))


def _ack_boundary(line):
    # Numbers, short wrapped prose and italic/bold body alone are not headings.
    return _section_heading(line)


def _possible_ack_boundary(line, previous):
    text = line['text'].strip()
    words = text.split()
    if (not 1 <= len(words) <= 10 or not text[0].isupper() or
            re.search(r'[.!?,;]$', text)):
        return False
    gap = line['rect'][1] - previous['rect'][3]
    return gap >= max(8, line['size'] * .7) or (
        gap >= 3 and line['styled_heading'] and not previous['styled_heading'])


def _identity_footnote(text, names):
    if re.search(r'\b(?:labels?|target variables?|training labels?|literal|example input)\b', text, re.I):
        return False
    stripped = re.sub(r'^[\d*∗†‡§¶♠♣♥♦◊.,\s]+', '', text)
    if (EMAIL.search(text) and any(name in _normal(text) for name in names) and
            re.search(r'\bcorresponding authors?\b|\bcorrespondence\b', text, re.I)):
        return True
    if (stripped != text and re.match(r'corresponding authors?(?:[.:]|\s*[(]|$)', stripped, re.I)
            and (EMAIL.search(stripped) or re.fullmatch(r'corresponding authors?[.]?', stripped, re.I))):
        return True
    if re.match(r'^interns?\s+(?:at|with)\b', stripped, re.I):
        return bool(AFFILIATION.search(text))
    if not FOOTNOTE.search(stripped):
        return False
    if re.search(r'work (?:was )?(?:done|performed)', text, re.I):
        return bool(AFFILIATION.search(text) or
                    re.search(r'\b(?:internship|intern|employment)\b', text, re.I) or
                    any(name in _normal(text) for name in names))
    if re.search(r'corresponding authors?', text, re.I):
        return bool(EMAIL.search(text) or any(name in _normal(text) for name in names))
    return True


def _identity_note_context(lines, index):
    line = lines[index]
    if not re.search(r'\bcorrespond(?:ing)?(?:\s+au(?:thor)?)?[-\u00ad]\s*$', line['text'], re.I):
        return line['text']
    parts = [line['text']]
    previous = line
    for following in (item for item in lines[index + 1:]
                      if item['block'] == line['block'] and
                      abs(item['rect'][0] - line['rect'][0]) <= 18):
        if (len(parts) == 3 or following['rect'][1] - previous['rect'][3] > 5 or
                abs(following['size'] - line['size']) > .5):
            break
        parts.append(following['text'])
        previous = following
    return re.sub(r'(?<=\w)[-\u00ad]\s*\n(?=\w)', '', '\n'.join(parts))


def _own_arxiv_regions(page, line, paper):
    """Only visible URLs for this manuscript, never generic cited arXiv links."""
    from urllib.parse import urlsplit
    import pymupdf as fitz

    identity = str(paper.get('arxiv_id') or '')
    if not identity:
        return []
    regions = []
    for match in URL.finditer(line['text']):
        piece = match.group().rstrip('.,;)')
        parsed = urlsplit(piece)
        if (parsed.hostname not in ('arxiv.org', 'www.arxiv.org') or
                not re.fullmatch(r'/(?:abs|pdf)/' + re.escape(identity) +
                                 r'(?:v\d+)?(?:\.pdf)?/?', parsed.path)):
            continue
        for rect in page.search_for(piece, clip=fitz.Rect(line['rect'])):
            regions.append({'text': piece, 'rect': list(rect)})
    return regions


def _owned_link_line(text):
    matches = list(URL.finditer(text))
    if not matches:
        return False
    prefix = text[:matches[0].start()].strip()
    suffix = URL.sub('', text[matches[0].start():]).strip(' .;()')
    return bool(OWNED_LINK.fullmatch(prefix) and not suffix)


def _release_context(lines, index, offset):
    """Positive manuscript release sentence, bounded to the same text block."""
    line = lines[index]
    previous = [item for item in lines[:index] if item['block'] == line['block']]
    prefix = '\n'.join(item['text'] for item in previous[-4:]) + '\n' + line['text'][:offset]
    prefix = prefix[-700:].strip()
    release = re.compile(
        r'(?:^|[.!?]\s+)(?:(?:our|the)\s+)?(?:source\s+)?code'
        r'(?:\s+and\s+(?:data|models?))?\s+(?:is\s+|are\s+)?(?:available|released)\b'
        r'|(?:^|[.!?]\s+)A demo\b[^.!?]{0,220}\b(?:the|this|our)\s+'
        r'(?:paper|results|method)\b[^.!?]{0,120}\bavailable\b', re.I | re.S)
    anchors = list(release.finditer(prefix))
    if not anchors:
        return False
    anchor = anchors[-1]
    explicit_owner = re.search(r'\bour\b|\b(?:this|the)\s+paper\b', anchor.group(), re.I)
    antecedent = URL.sub('', prefix[:anchor.start()])
    if not explicit_owner and re.search(
            r'\b(?:baseline|third.party|cited|citation|et\s+al)\b', antecedent, re.I):
        return False
    tail = prefix[anchor.end():]
    # A following sentence or a scientific example is a separate context.
    def omit_url(match):
        token = match.group()
        following = tail[match.end():].lstrip()
        domain_wrap = token.endswith('.') and re.match(r'^[a-z]{2,}/', following)
        return token[-1] if token[-1] in '.!?' and not domain_wrap else ''
    prose_tail = URL.sub(omit_url, tail)
    return (len(tail) <= 280 and not re.search(r'[.!?]\s+', prose_tail) and
            not re.search(r'\b(?:example|literal|baseline|third.party|cited|citation)\b', prose_tail, re.I))


def _release_url_regions(page, lines, index):
    """Exact visible URL rectangles; link targets never authorize word deletion."""
    import pymupdf as fitz

    line = lines[index]
    regions = []
    links = [link for link in page.get_links() if link.get('uri')]
    for match in URL.finditer(line['text']):
        if not _release_context(lines, index, match.start()):
            continue
        raw = match.group()
        starts = page.search_for(raw, clip=fitz.Rect(line['rect']))
        if not starts:
            continue
        annotations = [link for link in links
                       if any(fitz.Rect(link['from']).intersects(rect) for rect in starts) and
                       (link['uri'].startswith(raw) or link['uri'] == raw.rstrip('.,;)'))]
        if not annotations:
            # Complete, visible URL without a hyperlink annotation.
            piece = raw.rstrip('.,;)')
            if re.fullmatch(r'https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]\S*)?', piece):
                for rect in page.search_for(piece, clip=fitz.Rect(line['rect'])):
                    regions.append({'text': piece, 'rect': list(rect)})
            continue
        uri = annotations[0]['uri']
        same_block = [item for item in lines[index:] if item['block'] == line['block']][:5]
        pieces = []
        combined = ''
        for link in sorted((link for link in links if link.get('uri') == uri),
                           key=lambda link: (round(link['from'].y0, 1), link['from'].x0)):
            matching_lines = [item for item in same_block
                              if fitz.Rect(item['rect']).intersects(link['from'])]
            if not matching_lines:
                continue
            source_line = max(matching_lines, key=lambda item:
                              (fitz.Rect(item['rect']) & link['from']).get_area())
            visible = page.get_textbox(link['from']).strip()
            if not visible or any(char.isspace() for char in visible):
                continue
            if not combined and not re.match(r'https?://', visible, re.I):
                continue
            variants = (visible, visible.rstrip('.,;)'))
            piece = next((value for value in variants if value and uri.startswith(combined + value)), None)
            if piece is None:
                continue
            # A continuation must begin the wrapped line, not a method-name link
            # embedded in unrelated prose further along that line.
            if combined and not source_line['text'].lstrip().startswith(piece):
                break
            rects = page.search_for(piece, clip=fitz.Rect(source_line['rect']))
            if not rects:
                break
            pieces.extend({'text': piece, 'rect': list(rect)} for rect in rects)
            combined += piece
            if combined == uri:
                regions.extend(pieces)
                break
    return regions


def _affiliation_fragment(line, header):
    # A short organization label inherits only a positively identified source
    # text block, never an arbitrary title-to-abstract rectangle.
    if not (re.fullmatch(r'[A-Z][A-Z0-9&-]{1,15}', line['text']) or
            re.fullmatch(r'Project\s+[A-Z][A-Za-z0-9& -]{1,40}', line['text'])):
        return False
    peers = [item for item in header if item['block'] == line['block']]
    return any(AFFILIATION.search(item['text']) and
               abs(item['size'] - line['size']) < 1 for item in peers)


def _note_prefix_end(text):
    # A correspondence sentence can share its PDF line with scientific prose.
    emails = list(EMAIL.finditer(text))
    if emails:
        end = emails[-1].end()
        split = re.match(r'[>)\]]*\.\s+(?=[A-Z])', text[end:])
        if split:
            return end + split.group().index('.') + 1
    return len(text)


def _contact_only(text):
    remaining = ORCID.sub('', EMAIL.sub('', text))
    remaining = re.sub(r'\b(?:email|emails|e-mail|contact|correspondence|orcid)\b',
                       '', remaining, flags=re.I)
    return not _normal(remaining)


def _matched_lines(lines, phrases):
    """Match normalized full phrases, including PDF line-end hyphenation."""
    found = set()
    for start in range(len(lines)):
        raw = ''
        spans = []
        for index in range(start, min(start + 6, len(lines))):
            other = lines[index]
            if index > start and other['rect'][1] - lines[index - 1]['rect'][3] > 20:
                break
            part = _normal(other['text'])
            separator = '' if index > start and lines[index - 1]['text'].rstrip().endswith(('-', '\u00ad')) else ' '
            raw += separator if raw else ''
            spans.append((len(raw), len(raw) + len(part), index))
            raw += part
            for phrase in phrases:
                if not phrase:
                    continue
                for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', raw):
                    found.update(position for lo, hi, position in spans
                                 if lo < match.end() and hi > match.start())
    return found


def _byline_only(text, names):
    value = _normal(text)
    if not value or not any(name and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', value)
                            for name in names):
        return False
    for name in sorted(names, key=len, reverse=True):
        value = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', '', value)
    return not re.sub(r'\b(?:and|et|al|[0-9]+)\b', '', value).strip()


def _name_shape(text):
    """A weak shape check, used only beside a positively anchored author row."""
    if (EMAIL.search(text) or URL.search(text) or AFFILIATION.search(text) or
            re.search(r'[:!?=]', text) or
            re.search(r'\b(?:inspired|scientific|method|figure|table|group|results|'
                      r'project|abstract|theorem|foundation|funding|memory|praise|tribute)\b', text, re.I)):
        return False
    words = re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", text)
    return (len(words) >= 2 and len(words) <= 28 and
            all(word[0].isupper() or word.lower() in {'and', 'de', 'da', 'van', 'von', 'del', 'al', 'et'}
                for word in words) and sum(word[0].isupper() for word in words) >= 2)


def _author_names(paper):
    names = []
    for author in paper.get('authors') or []:
        if isinstance(author, str):
            name = author
        elif isinstance(author, dict):
            name = author.get('name') or ' '.join(str(author.get(key, ''))
                                                for key in ('given', 'family'))
        else:
            continue
        value = _normal(name)
        if len(value.split()) >= 2:
            names.append(value)
    return names


def _title_indexes(header, title):
    matched = _matched_lines(header, [title]) if title else set()
    if matched:
        return matched
    eligible = [i for i, line in enumerate(header)
                if not (VENUE.search(line['text']) or ARXIV.search(line['text']) or
                        EMAIL.search(line['text']) or not _normal(line['text']))]
    if not eligible:
        return set()
    largest = max(header[i]['size'] for i in eligible)
    # A structural fallback needs a dominant font and an early, continuous block.
    small = min(header[i]['size'] for i in eligible)
    if largest < small * 1.12:
        return set()
    start = next(i for i in eligible if header[i]['size'] >= largest * .95)
    if start > eligible[0] + 2:
        return set()
    result = {start}
    for i in eligible:
        if i <= start:
            continue
        prior = header[max(result)]
        if header[i]['size'] < largest * .9 or header[i]['rect'][1] - prior['rect'][3] > largest:
            break
        result.add(i)
    return result


def detected_identity_strings(plan):
    """Reusable evidence strings; these are detections, not an independent audit."""
    return sorted({match.group().rstrip('.,;)') for item in plan['redactions']
                   for pattern in (EMAIL, URL, ORCID, ARXIV, DOI)
                   for match in pattern.finditer(item['text'])})


def build_plan(source: Path, paper: dict) -> dict:
    """Return the existing PLAN_SCHEMA, retaining all unresolved-case reasons."""
    import pymupdf as fitz

    if not isinstance(paper, dict):
        raise ValueError('Paper metadata must be an object')
    plan = {'redactions': [], 'forbidden_strings': [], 'uncertain': []}
    names = _author_names(paper)
    seen = set()

    def warn(message):
        if message not in plan['uncertain']:
            plan['uncertain'].append(message)

    with fitz.open(source) as doc:
        if doc.needs_pass or not doc.page_count:
            raise ValueError('Encrypted or empty PDF is unsupported')
        pages = []
        for number, page in enumerate(doc, 1):
            lines = []
            for block_id, block in enumerate(page.get_text('dict')['blocks']):
                if block['type'] != 0:
                    continue
                for raw in block['lines']:
                    text = ''.join(span['text'] for span in raw['spans']).strip()
                    if text:
                        spans = [span for span in raw['spans'] if span['text'].strip()]
                        lines.append({'text': text, 'rect': list(raw['bbox']), 'block': block_id,
                                      'size': max(span['size'] for span in spans),
                                      'styled_heading': any(span['flags'] & 18 for span in spans)})
            lines.sort(key=lambda item: (round(item['rect'][1], 1), item['rect'][0]))
            pages.append(lines)
            if not lines:
                warn(f'Page {number}: no extractable text; OCR or manual review is required.')
            if page.get_images():
                warn(f'Page {number}: raster images are not assessed for identifying content.')
            if page.get_drawings():
                warn(f'Page {number}: vector graphics are not assessed for identifying content.')

        def add(number, line, category):
            bounds = doc[number - 1].rect * doc[number - 1].derotation_matrix
            rect = fitz.Rect(line['rect']) & bounds
            if rect.is_empty:
                warn(f'Page {number}: identity text has unusable coordinates.')
                return
            key = (number, *rect)
            if key not in seen:
                seen.add(key)
                plan['redactions'].append({'page': number, 'rect': list(rect),
                                          'category': category, 'text': line['text']})

        def add_prefix(number, line, end, category):
            piece = line['text'][:end]
            rects = doc[number - 1].search_for(piece, clip=fitz.Rect(line['rect']))
            if not rects:
                warn(f'Page {number}: mixed identity/scientific line could not be safely split.')
                return
            for rect in rects:
                add(number, {'text': piece, 'rect': list(rect)}, category)

        def earlier_margin_identity(number, line, category):
            """Reuse a previously identified margin header, never a bare citation."""
            current_bounds = doc[number - 1].rect * doc[number - 1].derotation_matrix
            for item in plan['redactions']:
                if item['page'] >= number or item['category'] != category or item['text'] != line['text']:
                    continue
                bounds = doc[item['page'] - 1].rect * doc[item['page'] - 1].derotation_matrix
                rect = item['rect']
                was_margin = (rect[1] < bounds.height * .09 or rect[3] > bounds.height * .93 or
                              rect[0] < bounds.width * .06 or rect[2] > bounds.width * .94)
                if (was_margin and abs(rect[1] / bounds.height -
                                       line['rect'][1] / current_bounds.height) < .004):
                    return True
            return False

        first = pages[0]
        height = (doc[0].rect * doc[0].derotation_matrix).height
        starts = [line['rect'][1] for line in first
                  if _body_start(line['text']) and line['rect'][1] < height * .8]
        boundary = min(starts) if starts else None
        if boundary is None:
            warn('Page 1: no reliable abstract or introduction boundary for the author block.')
        header = [line for line in first
                  if line['rect'][3] <= (boundary if boundary is not None else height * .4)]
        title_indexes = _title_indexes(header, _normal(paper.get('title') or ''))
        if not title_indexes:
            warn('Page 1: metadata title was not matched and structural title is ambiguous; byline preserved.')
        title_end = max((header[i]['rect'][3] for i in title_indexes), default=height)
        matches = _matched_lines(header, names) - title_indexes
        anchors = {i for i in matches if header[i]['rect'][1] >= title_end and
                   (_byline_only(header[i]['text'], names) or
                    (_name_shape(header[i]['text']) and re.search(r'[,\d*†‡]', header[i]['text'])))}
        # A known full name can itself wrap into two short lines.
        for i in matches:
            nearby = [j for j in matches if abs(j - i) < 3 and header[j]['rect'][1] >= title_end]
            joined = ' '.join(header[j]['text'] for j in sorted(nearby))
            if _byline_only(joined, names):
                anchors.update(nearby)
        if not names:
            warn('Author metadata is missing or unusable; author identification is unresolved.')
        elif not anchors:
            warn('Page 1: no metadata author was matched in the bounded author block.')
            if matches:
                warn('Page 1: matched author lines also contain unclassified text; byline preserved.')
        unmatched = [name for name in names if not (_matched_lines(header, [name]) - title_indexes)]
        if anchors and unmatched:
            warn('Page 1: some metadata authors were not matched: ' + ', '.join(unmatched))
        if boundary is None or not title_indexes:
            anchors = set()
        author_indexes = set(anchors)
        identity_indexes = set()
        if anchors:
            start = min(anchors)
            # Adjacent names may differ from the latest metadata roster.
            while start and header[start - 1]['rect'][1] >= title_end:
                prev, cur = header[start - 1], header[start]
                if (cur['rect'][1] - prev['rect'][3] > 12 or not _name_shape(prev['text']) or
                        prev['block'] != cur['block'] or abs(prev['size'] - cur['size']) > 1):
                    break
                start -= 1
                author_indexes.add(start)
            previous = header[start]
            for index in range(start, len(header)):
                line = header[index]
                text = line['text']
                gap = line['rect'][1] - previous['rect'][3]
                if gap > max(22, previous['size'] * 1.8):
                    warn(f'Page 1: unclassified author-block line after layout gap was preserved: {text[:160]}')
                    break
                if index in author_indexes:
                    category = 'author'
                elif (_name_shape(text) and abs(line['size'] - header[min(anchors)]['size']) <= 1
                      and (any(abs(line['rect'][1] - header[i]['rect'][1]) < 3 for i in anchors) or
                           (line['block'] in {header[i]['block'] for i in anchors}
                            and re.search(r'[\d*†‡]', text)))
                      and index <= max(anchors) + 2
                      and any(AFFILIATION.search(next_line['text']) for next_line in header[index + 1:index + 3])):
                    category = 'author'
                    author_indexes.add(index)
                    warn('Page 1: an adjacent author-shaped line absent from metadata was removed; roster needs review.')
                elif AFFILIATION.search(text) or _affiliation_fragment(line, header):
                    category = 'affiliation'
                elif EMAIL.search(text) and (_contact_only(text) or not re.search(r'\b(?:is|are|the|input|example)\b', text, re.I)):
                    category = 'contact'
                elif ORCID.search(text) or _owned_link_line(text) or (URL.search(text) and not _normal(URL.sub('', text))):
                    category = 'author_link'
                elif _identity_footnote(text, names):
                    category = 'other_identity'
                elif VENUE.search(text):
                    category = 'venue'
                elif ARXIV.search(text):
                    category = 'identifier'
                else:
                    warn(f'Page 1: unclassified author-block line: {text[:160]}')
                    break
                add(1, line, category)
                identity_indexes.add(index)
                previous = line

        in_references = False
        for number, lines in enumerate(pages, 1):
            bounds = doc[number - 1].rect * doc[number - 1].derotation_matrix
            for index, line in enumerate(lines):
                text = line['text']
                if _reference_heading(text):
                    in_references = True
                elif (_section_heading(line) and not _ack_heading(text) and
                      (not in_references or _heading(text, SCIENCE_HEADING) or
                       re.match(r'^Appendi(?:x|ces)\b', text, re.I))):
                    in_references = False
                margin = (line['rect'][1] < bounds.height * .09 or
                          line['rect'][3] > bounds.height * .93 or
                          line['rect'][0] < bounds.width * .06 or
                          line['rect'][2] > bounds.width * .94)
                header_context = number == 1 and any(line is header[i] for i in identity_indexes)
                is_title = number == 1 and any(line is header[i] for i in title_indexes)
                if is_title:
                    continue
                if (ARXIV.match(text) and str(paper.get('arxiv_id') or '') and
                        re.fullmatch(r'arxiv\s*:\s*' + re.escape(str(paper['arxiv_id'])) + r'(?:v\d+)?',
                                     ARXIV.match(text).group(), re.I) and margin and
                        (not in_references or earlier_margin_identity(number, line, 'identifier'))):
                    add(number, line, 'identifier')
                elif (VENUE.search(text) and (margin or header_context) and
                      (not in_references or
                       ((line['rect'][1] < bounds.height * .09 or line['rect'][3] > bounds.height * .93) and
                        not re.match(r'^proceedings of', text, re.I)))):
                    add(number, line, 'venue')
                elif (margin and _byline_only(text, names) and
                      (not in_references or earlier_margin_identity(number, line, 'author'))):
                    add(number, line, 'author')
                elif not in_references and number == 1 and line['rect'][1] > bounds.height * .65 and _identity_footnote(_identity_note_context(lines, index), names):
                    prefix_end = _note_prefix_end(text)
                    if prefix_end < len(text):
                        add_prefix(number, line, prefix_end, 'other_identity')
                        continue
                    add(number, line, 'other_identity')
                    previous = line
                    for following in (item for item in lines[index + 1:]
                      if item['block'] == line['block'] and
                      abs(item['rect'][0] - line['rect'][0]) <= 18):
                        if previous['text'].rstrip().endswith('.') and not _identity_footnote(following['text'], names):
                            break
                        continuation = following['text'].lstrip()
                        if (continuation and continuation[0].isupper() and
                                not (EMAIL.search(continuation) or AFFILIATION.search(continuation) or
                                     _owned_link_line(continuation) or
                                     any(name in _normal(continuation) for name in names))):
                            break
                        if (following['block'] != line['block'] or
                                abs(following['size'] - line['size']) > .5 or
                                following['rect'][1] - previous['rect'][3] > 5 or
                                abs(following['rect'][0] - line['rect'][0]) > 18 or
                                _section_heading(following) or
                                re.match(r'^[\d*∗†‡§¶]+\s*[A-Z]', following['text'])):
                            break
                        prefix_end = _note_prefix_end(following['text'])
                        if prefix_end < len(following['text']):
                            add_prefix(number, following, prefix_end, 'other_identity')
                            break
                        add(number, following, 'other_identity')
                        previous = following
                elif URL.search(text) and not in_references:
                    urls = list(URL.finditer(text))
                    prefix = text[:urls[0].start()].strip()
                    suffix = URL.sub('', text[urls[0].start():]).strip(' .;()')
                    explicit = (OWNED_LINK.fullmatch(prefix) and not suffix and
                                (re.search(r'\bour\b', prefix, re.I) or header_context))
                    if not prefix and index and not suffix:
                        prev = lines[index - 1]
                        if (line['rect'][1] - prev['rect'][3] < 8 and
                                abs(line['rect'][0] - prev['rect'][0]) < 24 and
                                OWNED_LINK.fullmatch(prev['text'].strip()) and
                                (re.search(r'\bour\b', prev['text'], re.I) or header_context)):
                            explicit = True
                            add(number, prev, 'author_link')
                    own_regions = _own_arxiv_regions(doc[number - 1], line, paper)
                    release_regions = [] if header_context else _release_url_regions(doc[number - 1], lines, index)
                    if own_regions or release_regions:
                        for region in own_regions:
                            add(number, region, 'identifier')
                        for region in release_regions:
                            add(number, region, 'author_link')
                    elif header_context or explicit:
                        add(number, line, 'author_link')
                    else:
                        warn(f'Page {number}: URL outside the identified author block was preserved for manual review.')
                elif (EMAIL.search(text) or ORCID.search(text)) and not in_references:
                    margin_contact = ((line['rect'][1] < bounds.height * .09 or
                                       line['rect'][3] > bounds.height * .93) and
                                      bool(re.match(r'^(?:email|e-mail|contact|correspondence|orcid)\s*:', text, re.I)) and
                                      _contact_only(text))
                    if header_context or margin_contact:
                        add(number, line, 'contact')
                    else:
                        warn(f'Page {number}: identifier within prose was preserved for manual review.')
                if not _ack_heading(text):
                    continue
                ends = [later for position, later in enumerate(lines[index + 1:], index + 1)
                        if later['rect'][1] > line['rect'][3] and
                        (_ack_boundary(later) or _possible_ack_boundary(later, lines[position - 1]))]
                if not ends:
                    next_start = []
                    if number < len(pages):
                        next_start = [item for item in pages[number]
                                      if not (VENUE.search(item['text']) or ARXIV.match(item['text']) or
                                              re.fullmatch(r'\d{1,3}', item['text'].strip()))]
                    if not next_start or not _section_heading(next_start[0]):
                        warn(f'Page {number}: acknowledgment section has no safe same-page end boundary.')
                        continue
                    # The next page starts a new section, with no continuation
                    # prose to guess about. Only this page's ack text is removed.
                    end_y = bounds.height
                else:
                    if not _ack_boundary(ends[0]):
                        warn(f'Page {number}: acknowledgment stops before an unclassified possible section heading: '
                             f'{ends[0]["text"][:160]}')
                    end_y = ends[0]['rect'][1]
                section = [item for item in lines
                           if line['rect'][1] <= item['rect'][1] and item['rect'][3] < end_y and
                           _normal(item['text']) and not re.fullmatch(r'\d{1,3}', item['text'].strip())]
                lefts = [item['rect'][0] for item in section]
                if lefts and min(lefts) < bounds.width * .4 and max(lefts) > bounds.width * .5:
                    warn(f'Page {number}: acknowledgment section has a column ambiguity.')
                    continue
                if any(item['rect'][2] - item['rect'][0] > bounds.width * .8 for item in section):
                    warn(f'Page {number}: acknowledgment section bounds require manual review.')
                    continue
                for item in section:
                    add(number, item, 'acknowledgment')

        # Ban a detected identity only when every extracted occurrence is removed.
        kept = []
        for number, lines in enumerate(pages, 1):
            for line in lines:
                text = line['text']
                for item in plan['redactions']:
                    if item['page'] == number and fitz.Rect(item['rect']).intersects(fitz.Rect(line['rect'])):
                        text = text.replace(item['text'], '', 1)
                kept.append(text)
        remaining = '\n'.join(kept)
        for phrase in detected_identity_strings(plan):
            if ''.join(phrase.casefold().split()) not in ''.join(remaining.casefold().split()):
                plan['forbidden_strings'].append(phrase)

    plan['redactions'].sort(key=lambda item: (item['page'], item['rect'][1], item['rect'][0]))
    return plan

"""Conservative layout- and metadata-based identity proposals, without model calls.

The plan is a review candidate. Unrecognized layouts and non-text identities are
reported explicitly; absence of a detection is never an anonymity certificate.
"""
from pathlib import Path
import re
import unicodedata

RULES_VERSION = 'anonymize-rules-29'
LIMITATIONS = (
    'Rules are not a visual or semantic anonymity audit.',
    'Images, institutional logos, obfuscated identifiers and self-identifying prose may remain.',
    'Ordinary bibliography entries and scientific body text are intentionally preserved.',
)

EMAIL = re.compile(
    r'(?:\{[\w.+,@\s-]+\}|[\w.+-]+)@(?:\{[\w.,\s-]+\}\.)?[\w.-]+\.[A-Za-z]{2,}'
    r'|\{(?:[\w.+-]+@[\w.-]*\s*,\s*)*[\w.+-]+@[\w.-]*\}[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
URL = re.compile(r'(?:https?://|www\.)[^\s<>]+', re.I)
URI_START = re.compile(r'https?\s*:|www\s*[.]', re.I)
ORCID = re.compile(r'(?:orcid(?:\.org)?\s*[:/]?\s*)?\b\d{4}-\d{4}-\d{4}-[\dX]{4}\b', re.I)
ARXIV = re.compile(r'\barxiv\s*:\s*(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?', re.I)
DOI = re.compile(r'(?:doi\s*:\s*|https?://doi\.org/)10\.\d{4,9}/\S+', re.I)
AFFILIATION = re.compile(
    r'(?<![^\W\d_])(?:university|universit[aey]|universiy|univesity|institute|institution|department|faculty|dpt[.]? of|chair of|politecnico|laborator(?:y|ies)|'
    r'college|school|research cent(?:er|re)|cent(?:er|re) for|agency for (?:science|technology)|academy|google|deepmind|microsoft|'
    r'meta ai|nvidia|openai|anthropic|amazon|huawei|bytedance|alibaba)\b', re.I)
VENUE = re.compile(
    r'^(?:(?:conference paper )?under review[.]?\s*$|published as a (?:conference )?paper at|published in .+\((?:\d{1,2}/)?\d{4}\)|reviewed on [A-Za-z][\w.-]*\s*:|under review (?:as|at|by)|'
    r'submitted to|accepted (?:by|at|for)|to appear (?:at|in)|'
    r'proceedings of|copyright(?=\s*(?:©|\(c\))?\s*(?:19|20)\d{2}\b|\s+by\b)|©|preprint(?:[.\s]|$))', re.I)
FOOTNOTE = re.compile(
    r'^(?:[\d*†‡§¶♠♣♥♦◊.,\s]+)?(?:equal contributions?\b|'
    r'co[- ]first authors?\b|co[- ](?:senior|last) authors?\b|'
    r'(?:the )?corresponding authors?\s*[:.*†‡]|correspondence\s*(?:to|:)\s*|'
    r'(?:this )?work (?:was )?(?:(?:mostly|primarily|partly|partially) )?(?:done|performed)\b|'
    r'(?:project (?:lead|leader)|research intern|internship)\s*[:.]|'
    r'(?:these )?authors contributed equally\b)', re.I)
OWNED_LINK = re.compile(
    r'^(?:[\d*†‡\s]+)?(?:code|project(?: page| website)?|'
    r'(?:our |the )?(?:source )?code(?: and (?:data|models?))?'
    r'(?: (?:is |are )?(?:available|released|can be found)(?: at| on)?)?)\s*[:.]?\s*$', re.I)
SCIENCE_HEADING = {'introduction', 'conclusion', 'conclusions', 'discussion',
                   'limitations', 'ethical considerations', 'methods', 'method',
                   'methodology', 'experiments', 'experimental details', 'results',
                   'related work', 'appendix', 'appendices', 'broader impact',
                   'ethics statement', 'data availability', 'data availability statement',
                   'code availability', 'competing interests', 'conflict of interest', 'declarations',
                   'impact statement', 'reproducibility statement', 'additional information'}


def _without_spacing_accents(text):
    # A leading accent on the next capitalized name must keep its word boundary.
    marks = '´ˋˊˇˆ˜¨˚˝˘˙¸˛' + chr(96)
    return re.sub(r'(?<=[^\W\d_])(\s*)[' + marks + r']\s*(?=[^\W\d_])',
                  lambda match: ' ' if match.group(1) and text[match.end()].isupper() else '', text)


def _normal(text):
    text = _without_spacing_accents(text)
    text = unicodedata.normalize('NFKD', text).casefold()
    text = re.sub(r'(?<=[a-z])(?=[αβγδεζηθλμνξπρστυφχψω](?:\d|\W|$))', ' ', text)
    text = re.sub(r'(?<=[^\W\d_])(?=\d)', ' ', text)
    return ' '.join(re.findall(r'[^\W_]+', ''.join(
        c for c in text if not unicodedata.combining(c))))


def _heading(text, names):
    value = re.sub(r'^(?:(?:[a-z]|\d+)(?:\s+\d+)*|[ivxlcdm]+)\s+', '', _normal(text))
    return value in names


def _body_start(text):
    value = _normal(text)
    return value == 'abstract' or value.startswith('abstract ') or _heading(text, {'introduction'})


def _reference_heading(text):
    return _heading(text, {'references', 'bibliography'})


def _ack_heading(text):
    if re.match(r'^\s*(?:[-*+•]\s|#{1,6}\s|[>"“‘])', text):
        return False
    if re.match(r'^acknowledg(?:e)?ments?[.:]?\s+(?:the authors|we|this work|our work)\b', text, re.I):
        return True
    return _heading(text, {'acknowledgments', 'acknowledgements', 'acknowledgment',
                          'acknowledgement', 'funding', 'funding statement',
                          'acknowledgments and disclosure of funding',
                          'acknowledgements and disclosure of funding'})


def _identity_section_kind(text):
    if re.match(r'^\s*(?:[-*+•]\s|#{1,6}\s|[>"“‘])', text):
        return None
    if _ack_heading(text):
        return 'acknowledgment'
    if re.match(r'^(?:author(?:s)? contributions?|credit authorship contribution statement)\s*[:.]\s*\S', text, re.I):
        return 'other_identity'
    if _heading(text, {'author contributions', 'authors contributions', 'author contribution',
                       'credit authorship contribution statement'}):
        return 'other_identity'
    return None


def _identity_boundary(line, previous, kind, heading_size):
    if _ack_boundary(line):
        return True
    if kind == 'other_identity':
        if line['size'] < heading_size - .5:
            return False
        if re.search(r'\b(?:conceived|curated|designed|developed|conducted|drafted|supervised|contributed|trained)\b',
                     line['text'], re.I):
            return False
    return _possible_ack_boundary(line, previous)


def _section_heading(line):
    value = line['text'].strip()
    return (_reference_heading(value) or _heading(value, SCIENCE_HEADING) or
            bool(_identity_section_kind(value)) or
            bool(re.fullmatch(r'Appendi(?:x|ces)(?:[.\s:]+[^.!?]{0,100})?', value, re.I)) or
            bool(re.fullmatch(r'(?:\d{1,2}(?:\.\d{1,2})*|[A-Z])[.)]?\s+[A-Z][^.!?;]{0,80}', value)))


def _ack_boundary(line):
    # Numbers, short wrapped prose and italic/bold body alone are not headings.
    return (_section_heading(line) or
            bool(re.match(r'^(?:Figure|Fig[.]|Table)\s+[A-Z]?\d', line['text'], re.I)))


def _possible_ack_boundary(line, previous):
    text = line['text'].strip()
    words = text.split()
    if re.match(r'^(?:Support for (?:this|our) (?:work|research|study|project) '
                r'(?:is|was) provided by|All authors are affiliated with)\b', text, re.I):
        return False
    if (re.search(r'\b(?:funded|financed|sponsored)\b', text, re.I) or
            re.search(r'\bsupported\b.{0,80}\b(?:grants?|foundation|fellowship|scholarship)\b', text, re.I)):
        return False
    if (re.match(r'^(?:we\b|this work\b|our work\b|the authors\b)', text, re.I) or
            re.search(r'^(?:this|our|the)\s+(?:work|research|study|project|material)\b.*'
                      r'\b(?:funded|financed|sponsored|supported)\b', text, re.I) or
            re.search(r'\b(?:acknowledges?|acknowledged|thanks?|thanked)\b', text, re.I)):
        return False
    if (not 1 <= len(words) <= 10 or not text[0].isupper() or
            re.search(r'[.!?,;]$', text)):
        return False
    gap = line['rect'][1] - previous['rect'][3]
    return gap >= max(8, line['size'] * .7) or (
        gap >= 3 and line['styled_heading'] and not previous['styled_heading'])


def _cross_page_ack(pages, number, heading, bounds, kind='acknowledgment', names=()):
    """Resolve an unfinished right-column sentence into the next left column."""
    midpoint = bounds.width * .5 - max(2, heading['size'] * .2)
    right = heading['rect'][0] >= midpoint
    if right and number >= len(pages):
        return []
    def content(item):
        return (bool(_normal(item['text'])) and not VENUE.search(item['text']) and
                not ARXIV.match(item['text']) and not re.fullmatch(r'\d{1,3}', item['text'].strip()))
    current_lefts = _column_lefts(pages[number - 1])
    current = [item for item in pages[number - 1]
               if (current_lefts[id(item)] >= midpoint) == right and
               item['rect'][1] >= heading['rect'][1] and content(item)]
    def repeated_header(item):
        return item['rect'][1] < bounds.height * .09 and any(
            _normal(previous['text']) == _normal(item['text']) and
            abs(previous['rect'][0] - item['rect'][0]) < 2 and
            abs(previous['rect'][1] - item['rect'][1]) < 2
            for previous in pages[number - 1])
    following_page = number + 1 if right else number
    following_lefts = _column_lefts(pages[following_page - 1])
    following = [item for item in pages[following_page - 1]
                 if (item['rect'][0] < bounds.width * .4 and item['rect'][2] < bounds.width * .55
                     if right else following_lefts[id(item)] >= midpoint) and content(item)
                 and not (right and repeated_header(item))]
    if len(current) < 2 or not following:
        return []
    last = current[-1]['text']
    first = following[0]['text']
    grant = bool(re.search(r'\b(?:grant|funding)\b', last, re.I) and
                 re.match(r'^(?:Nos?[.]\s*)?\d{4,}(?:[-/]\w+)*\b', first) and
                 re.search(r'\b(?:grant|funding|project)\b', first, re.I))
    next_sentence = ' '.join(item['text'] for item in following[:4])
    thanks = bool(re.search(r'\b(?:thank|thanks|acknowledge)\b',
                            ' '.join(item['text'] for item in current), re.I) and
                  re.search(r'(?:\band|,)\s*$', last) and
                  ' for ' in next_sentence and _name_shape(next_sentence.split(' for ', 1)[0]))
    current_text = '\n'.join(item['text'] for item in current)
    if (not right and kind == 'acknowledgment' and
            (re.search(r'\b(?:literal|quoted|prompt|stimulus|generated)\b'
                       r'|(?:^|\n)Example\s*(?:[:.]|prompt|input|output)'
                       r'|\b(?:benchmark|dataset)\b.{0,60}\b(?:field|includes|records|contains|classification)\b',
                       current_text, re.I) or
             any(re.match(r'^(?:Figure|Fig[.]|Table|Algorithm|Listing)\b', item['text'], re.I)
                 for item in current[1:]))):
        return []
    last_sentence = list(_sentence_spans(current_text))[-1]
    financial_bridge = bool(not right and _supported_author_credit(
        _flat(current_text[last_sentence[0]:] + '\n' + next_sentence), names))
    word_bridge = bool(not right and last.rstrip().endswith('-') and first[0].isalpha())
    assistance_bridge = bool(not right and
        re.search(r'\bthanks?\b', current_text[last_sentence[0]:], re.I) and
        re.search(r'\bfor(?:\s+(?:his|her|their|the|your))?\s*$', last, re.I) and
        re.match(r'^(?:(?:helpful|valuable|insightful|constructive|useful|technical|generous)\s+)?'
                 r'(?:comments|feedback|suggestions|conversations|discussions|advice|assistance|support)\b',
                 first, re.I))
    same_page_credit = bool(not right and kind == 'acknowledgment' and _ack_heading(heading['text']) and
                            _credit_text(current_text, kind, names, allow_generic=True) and
                            (thanks or financial_bridge or word_bridge or assistance_bridge))
    if (not right and kind == 'acknowledgment' and not (grant or same_page_credit) or
            re.search(r'[.!?]\s*$', last) and not (grant and re.search(r'\bNos?[.]\s*$', last)) or
            not (first[0].islower() or grant or thanks or financial_bridge or word_bridge)):
        return []
    end = next((i for i, item in enumerate(following)
                if _ack_boundary(item) or
                (i and _identity_boundary(item, following[i - 1], kind, heading['size']))), None)
    if end is None or end == 0:
        return []
    if same_page_credit:
        selected = current[1:] + following[:end]
        # A real credit bridge cannot absorb an independent scientific column,
        # a literal stimulus, or a caption before the explicit end heading.
        if (not _ack_boundary(following[end]) or
                any(re.match(r'^(?:Figure|Fig[.]|Table|Algorithm|Listing|Example|Prompt|Input|Output)\b'
                             r'|^(?:We|Our (?:method|model|experiments?))\s+'
                             r'(?:evaluate|propose|train|compare|measure|achieves?|uses?|shows?)\b',
                             item['text'], re.I) for item in selected) or
                not _credit_text('\n'.join(item['text'] for item in selected), kind, names, allow_generic=True)):
            return []
    # A repeated source running title is not part of either text column.
    # Other spanning content still makes the continuation ambiguous.
    if any(item['rect'][0] < bounds.width * .4 and item['rect'][2] >= bounds.width * .55
           and item['rect'][1] < following[end]['rect'][1] and not repeated_header(item)
           for item in pages[following_page - 1]) and right:
        return []
    return ([(number, item) for item in current] +
            [(following_page, item) for item in following[:end]])


def _identity_footnote(text, names, header_markers=(), affiliations=()):
    text = re.sub(r'\(s\)', 's', text, flags=re.I)
    if EMAIL.search(text):
        text = re.sub(r'\bcorresponding\s+to\s*:', 'Correspondence to:', text, flags=re.I)
    if re.search(r'\b(?:labels?|target variables?|training labels?|literal|example input)\b', text, re.I):
        return False
    stripped = re.sub(r'^[\d*∗†‡§¶♠♣♥♦◊⋄♯.,\s]+', '', text)
    marker = re.match(r'^([*∗†‡§¶♠♣♥♦◊⋄♯])', text)
    if (marker and marker.group(1) in header_markers and AFFILIATION.search(stripped) and
            len(stripped.split()) <= 24 and
            not re.search(r'\b(?:benchmark|theorem|measures?|evaluated|accuracy|training)\b', stripped, re.I)):
        return True
    if (EMAIL.search(text) and any(name in _normal(text) for name in names) and
            re.search(r'\bcorresponding authors?\b|\bcorrespondence\b', text, re.I)):
        return True
    if (stripped != text and re.match(r'(?:the )?corresponding authors?(?:[.:]|\s*[(]|$)', stripped, re.I)
            and (EMAIL.search(stripped) or re.fullmatch(r'corresponding authors?[.]?', stripped, re.I))):
        return True
    if re.match(r'^interns?\s+(?:at|with)\b', stripped, re.I):
        return bool(AFFILIATION.search(text))
    if not FOOTNOTE.search(stripped):
        return False
    if re.search(r'work (?:was )?(?:(?:mostly|primarily|partly|partially) )?(?:done|performed)', text, re.I):
        employer = re.search(r'\bat\s+([^.;]+)[.]?$', stripped, re.I)
        known_employer = bool(employer and any(
            re.search(r'(?<!\w)' + re.escape(_normal(employer.group(1))) + r'(?!\w)', _normal(value))
            for value in affiliations))
        return bool(AFFILIATION.search(text) or known_employer or
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
    if not matches or not all(_complete_uri(match.group().rstrip('.,;)')) for match in matches):
        return False
    prefix = text[:matches[0].start()].strip()
    suffix = URL.sub('', text[matches[0].start():]).strip(' .;()')
    return bool(OWNED_LINK.fullmatch(prefix) and not suffix)


def _block_reading_lines(lines, block):
    """Rejoin word-level PDF lines by baseline before reading left to right."""
    rows = []
    for item in sorted((line for line in lines if line['block'] == block),
                       key=lambda line: (line['rect'][3], line['rect'][0])):
        if not rows or abs(item['rect'][3] - rows[-1][0]['rect'][3]) > 3:
            rows.append([item])
        else:
            rows[-1].append(item)
    return [item for row in rows for item in sorted(row, key=lambda line: line['rect'][0])]


def _text_blocks(lines, bounds=None):
    groups = {}
    for line in lines:
        groups.setdefault(line['block'], []).append(line)
    for block_id, block in groups.items():
        if bounds and all(item['rect'][2] - item['rect'][0] < bounds.width * .55 for item in block):
            for right in (False, True):
                column = [item for item in block if (item['rect'][0] >= bounds.width * .5) == right]
                if column:
                    yield _block_reading_lines(column, block_id)
        else:
            yield _block_reading_lines(block, block_id)


def _flat(text):
    return re.sub(r'(?<=\w)[-\u00ad]\s+(?=\w)', '', re.sub(r'\s+', ' ', text)).strip()


def _sentence_spans(text):
    start = 0
    for match in re.finditer(r'[.!?]\s+(?=[A-Z])', text):
        prior = text[:match.start() + 1]
        if re.search(r'(?:\b[A-Z](?:\.[A-Z])*|\b(?:No|Nos|Dr|Prof|St|Inc|Ltd))\.$', prior):
            continue
        yield start, match.start() + 1
        start = match.end()
    if start < len(text):
        yield start, len(text)


def _block_prefix(block, predicate):
    """Keep source offsets while recognizing complete leading identity sentences."""
    text = '\n'.join(line['text'] for line in block)
    end = 0
    for start, stop in _sentence_spans(text):
        if not predicate(_flat(text[start:stop]), start == 0):
            break
        end = stop
    offset = 0
    for line in block:
        length = min(len(line['text']), end - offset)
        if length > 0:
            yield line, length
        offset += len(line['text']) + 1


def _personal_note_ranges(block, lines, bounds, names, affiliations=()):
    """Only a complete, isolated marked credit sentence; return source offsets."""
    if (not block or len(block) > 8 or min(x['rect'][1] for x in block) < bounds.height * .65 or
            max(x['size'] for x in block) > 10.5 or
            max(x['size'] for x in block) - min(x['size'] for x in block) > 1.2 or
            max(x['rect'][3] for x in block) - min(x['rect'][1] for x in block) > bounds.height * .14):
        return []
    if any(b['rect'][1] - a['rect'][3] > max(8, a['size']) for a, b in zip(block, block[1:])):
        return []
    raw = '\n'.join(item['text'] for item in block)
    marker = re.match(r'^(?:\d{1,2}|[*∗†‡§¶♠♣♥♦◊⋄♯])\s*', raw)
    if not marker:
        return []
    start, end = next(_sentence_spans(raw))
    sentence = raw[start:end]
    punctuated = bool(re.search(r'[.!?]\s*$', sentence))
    if ((not punctuated and (len(block) != 1 or end != len(raw))) or
            re.search(r'\n\s*(?:\d{1,2}|[*∗†‡§¶♠♣♥♦◊⋄♯])\s*\S', sentence)):
        return []
    previous = [item['text'] for item in lines if item['block'] != block[0]['block'] and
                0 <= block[0]['rect'][1] - item['rect'][3] < 45 and
                min(item['rect'][2], block[0]['rect'][2]) > max(item['rect'][0], block[0]['rect'][0])]
    if re.search(r'\b(?:prompt|example|literal|quoted|stimulus|input|output)(?:\s+text)?\s*[:=]',
                 ' '.join(previous), re.I):
        return []
    text = _flat(sentence[marker.end():])
    internship = re.fullmatch(
        r'(?:Contribution during internship|(?:This |Our )?work (?:was )?'
        r'(?:(?:mostly|primarily|partly|partially) )?(?:done|performed|conducted|finalized) '
        r'(?:while interning|during [^.!?]{0,90}(?:internship|visit))) '
        r'(?:at|with|to) (?P<employer>[^.!?]{2,120})[.!?]?', text, re.I)
    if not punctuated and not (internship and any(re.search(
            r'(?<![a-z])' + re.escape(_normal(internship.group('employer'))) + r'(?![a-z])',
            _normal(value)) for value in affiliations)):
        return []
    surnames = {name.split()[-1] for name in names if len(name.split()[-1]) >= 4}
    joint = bool(re.match(r'^While joint work between\b', text, re.I) and
                 re.search(r'\bthis paper\b', text, re.I) and
                 re.search(r'\battributable to\b.+\bcorresponding author\b', text, re.I) and
                 sum(bool(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', _normal(text)))
                     for name in surnames) >= 2)
    thanks = re.fullmatch(r'(?:We|The authors) thank (.{2,90}?) for (.+)[.!?]', text)
    named_thanks = bool(thanks and _name_shape(thanks.group(1)) and
        len(thanks.group(1).split()) <= 7 and
        not re.search(r'\b(?:reviewers|participants|volunteers|editors)\b', thanks.group(1), re.I) and
        re.search(r'\b(?:help|feedback|suggestions|verification|fact-checking|assistance|'
                  r'discussions|capturing|experiments|nudge)\b', thanks.group(2), re.I))
    return [(start, end)] if internship or joint or named_thanks else []



def _inline_credit_ranges(text, names):
    """Closed personal credits can be sliced out of surrounding scientific prose."""
    if re.match(r'^\s*(?:Figure|Fig[.]|Table|Algorithm|Listing)\s+[A-Z]?\d', text, re.I):
        return []
    counts = {}
    for name in names:
        initials = ''.join(word[0] for word in name.split()).upper()
        if len(initials) >= 2:
            counts[initials] = counts.get(initials, 0) + 1
    initials = {value for value, count in counts.items() if count == 1}

    def protected(start, end):
        before = text[max(0, start - 180):start]
        after = text[end:end + 70]
        return bool(re.search(
            r'\b(?:prompt|example|literal|quoted|stimulus|input|output)(?:\s+text)?\s*[:=]'
            r'|\b(?:biography|memoir|interview|the phrase|the sentence|the string)\b'
            r'|\b(?:model|assistant|agent|participant|speaker)\b[^.!?\n]{0,60}'
            r'\b(?:produced|produces|generated|generates|said|says|responded|responds|outputs?|answered)\b',
            before, re.I) or
            before.rstrip().endswith(('"', '“', '‘', "'")) or
            re.match(r'^\s*["”’]?\s*(?:is|was|means|matches|contains)\b', after, re.I))

    def person(value):
        value = value.strip()
        if value in initials or _normal(value) in names:
            return True
        return bool(_name_shape(value) and len(value.split()) <= 6 and
                    len([w for w in re.findall(r'[^\W\d_]+', value) if len(w) >= 3]) >= 2 and
                    not re.search(r'\b(?:reviewers|participants|volunteers|editors)\b', value, re.I))

    ranges = []
    for match in re.finditer(r'\(\s*Thank you (.{2,100}?) for ([^()]{1,140}?)\)', text, re.S):
        people = re.split(r'\s+and\s+|,\s*', match.group(1))
        if (all(person(value) for value in people) and
                re.search(r'\b(?:help|feedback|suggestions|verification|fact-checking|assistance|'
                          r'discussions|capturing|experiments|nudge)\b', match.group(2), re.I) and
                not protected(match.start(), match.end())):
            ranges.append(match.span())
    for match in re.finditer(
            r',\s*especially\s+([A-Z]{2,4}(?:(?:\s*,\s*|\s+and\s+)[A-Z]{2,4})+)(?=\s*:)', text):
        values = re.findall(r'[A-Z]{2,4}', match.group(1))
        before = text[max(0, match.start() - 120):match.start()]
        if (all(value in initials for value in values) and
                re.search(r'\bco[- ]authors\b[^.!?\n]*$', before, re.I) and
                not protected(match.start(), match.end())):
            ranges.append(match.span())
    return sorted(ranges)



def _funding_sentence(text, first=False, names=()):
    financial = bool(re.search(r'\b(?:funded|financed|sponsored)\b|\bgrants?\b.{0,45}\d', text, re.I))
    if first:
        if re.match(r'^The work of\b', text, re.I) and not any(name in _normal(text) for name in names):
            return False
        return bool(financial and re.match(
            r'^(?:[•*-]\s*)?(?:Funding[.:]?\s*)?(?:This|Our|The)\s+(?:work|research|study|project|material)\b'
            r'.{0,180}\b(?:funded|financed|sponsored|supported|received)\b', text, re.I))
    return bool(financial or re.match(r'^(?:Any opinions|The views expressed)', text, re.I))



def _column_lefts(lines):
    """Use connected fragments of one source row for column decisions only."""
    result = {}
    blocks = {}
    for line in lines:
        blocks.setdefault(line['block'], []).append(line)
    for block in blocks.values():
        rows = []
        for line in sorted(block, key=lambda item: (item['rect'][3], item['rect'][0])):
            row = next((row for row in reversed(rows[-2:])
                        if abs(row[0]['rect'][3] - line['rect'][3]) <= max(1, line['size'] * .25)), None)
            if row is None:
                rows.append([line])
            else:
                row.append(line)
        for row in rows:
            components = []
            for line in sorted(row, key=lambda item: item['rect'][0]):
                if (components and -.2 <= line['rect'][0] - max(item['rect'][2] for item in components[-1]) <=
                        max(line['size'], components[-1][-1]['size']) * 1.5):
                    components[-1].append(line)
                else:
                    components.append([line])
            for component in components:
                left = min(item['rect'][0] for item in component)
                result.update((id(item), left) for item in component)
    return result


def _supported_author_credit(text, names):
    text = _flat(text)
    if (re.search(r"\b(?:example|literal|quoted|quotation|prompt|input|output|stimulus|"
                  r"synthetic|generated|dataset|benchmark|the phrase|the sentence|"
                  r"the string|et\s+al|participants?|workers?|annotators?|subjects?)\b", text, re.I)
            or any(char in text for char in ('"', '“', '”', chr(96)))
            or re.search(r"(?:^|\s)['‘].+['’]", text)
            or re.match(r"^(?:Figure|Fig[.]|Table|Algorithm|Listing)\b|^\[\d+\]", text, re.I)):
        return False
    text = re.sub(r"^(?:\d+\s+)?acknowledg(?:e)?ments?[:.]?\s+", "", text, flags=re.I)

    def financial_tail(start):
        tail = text[start:start + 500].strip()
        stop = next(_sentence_spans(tail), (0, len(tail)))[1]
        tail = tail[:stop]
        if re.search(r"\b(?:theorems?|lemmas?|propositions?|proofs?|equations?|experiments?|"
                     r"evidence|results?|hypothes[ei]s|predictions?|models?)\b", tail, re.I):
            return False
        if re.search(r"\b(?:grants?|awards?|prize|fund(?:s|ing)?|foundation|scholarship|"
                     r"fellowship|studentship|financial)\b", tail, re.I):
            return True
        # A formal support identifier needs organizational context as well.
        formal_id = re.search(r"\b(?:under|contract|agreement)\s+(?:(?:Nos?|numbers?)\.?\s*)"
                              r"[A-Z0-9-]*\d[A-Z0-9-]{3,}\b", tail, re.I)
        organization = (re.search(r"\b[A-Z]{2,}(?:-[A-Z]+)*\b", tail) or
                        re.search(r"\b(?:program|laborator(?:y|ies)|university|institute|"
                                  r"council|agency|ministry|department)\b", tail, re.I))
        return bool(formal_id and organization)

    support = re.compile(
        r"\b(?:this|our|the)\s+(?:present\s+)?(?:work|research|study|project|paper)\s+"
        r"(?:(?:is|was|has\s+been|had\s+been)\s+)?"
        r"(?:(?:also|partly|partially|financially|generously|jointly)\s+)*"
        r"(?:based\s+upon\s+work\s+)?supported\s+(?:in\s+part\s+)?by\s+", re.I)
    if any(financial_tail(match.end()) for match in support.finditer(text)):
        return True
    inverted = re.match(r"Support for (?:this|our) (?:work|research|study|project) "
                        r"(?:is|was) provided by\s+", text, re.I)
    if inverted and financial_tail(inverted.end()):
        return True
    if (re.match(r"All authors are affiliated with\b", text, re.I) and
            AFFILIATION.search(text) and re.search(r"\b(?:grants?|funded|funding)\b", text, re.I)):
        return True
    for match in re.finditer(r"\backnowledges?\s+(?:financial\s+)?support\s+(?:by|from)\s+", text, re.I):
        subject = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text[:match.start()])[-1].strip()
        if ((_byline_only(subject, names) or
             re.fullmatch(r"we|(?:the\s+)?authors", subject, re.I)) and financial_tail(match.end())):
            return True
    return False



_CONTRIB_ROLE = (
    r"(?:conceived|designed|implemented|analy[sz]ed|curated|supervised|"
    r"managed|built|contributed|anchored|coordinated|oversaw|co-authored|"
    r"took\s+the\s+lead|was\s+responsible|was\s+instrumental|"
    r"supported\s+discussions|provided\s+(?:manuscript\s+)?feedback)"
)
_CONTRIB_LABEL = (
    r"(?:conceptualization|methodology|software|validation|formal\s+analysis|"
    r"investigation|resources|data\s+curation|visualization|supervision|"
    r"project\s+administration|funding\s+acquisition|"
    r"writing(?:\s*[-–—]\s*(?:original\s+draft|review\s*(?:&|and)\s*editing))?)"
)
_CONTRIB_BOUNDARY = re.compile(
    r"^\s*(?:(?:[A-Z]|\d+(?:\.\d+)*)[.)]?\s+)?"
    r"(?:references|bibliography|methods?|experiments?|results|discussion|"
    r"conclusions?|additional information|impact statement)\s*[.:]?\s*$", re.I
)
_CONTRIB_CAPTION = re.compile(r"^\s*(?:Figure|Fig[.]|Table)\s+[A-Z]?\d\b", re.I)
_CONTRIB_LITERAL = re.compile(
    r"\b(?:example\s+(?:prompt|input|answer|text)|quoted\s+(?:prompt|text|example)|"
    r"(?:study|benchmark)\s+stimulus|(?:model|training)\s+(?:input|prompt)|"
    r"prompt\s+example|(?:model|llm|system)\s+(?:generated|produced|returned)\s+"
    r"(?:the\s+)?(?:following\s+)?(?:statement|sentence|text|response|answer|caption))\b", re.I
)
_CONTRIB_MARKS = "´ˋˊˇˆ˜¨˚˝˘˙¸˛" + chr(96)
_CONTRIB_JOIN = re.compile(r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)", re.I)


def _contrib_rules(api):
    if api is not None:
        return api
    from utility.anonymize import anonymize_rules as rules
    return rules


def _contrib_words(text):
    """Raw word intervals, including combining/spacing accents inside names."""
    result, i = [], 0
    while i < len(text):
        starts = text[i].isalpha() or (
            text[i] in _CONTRIB_MARKS and i + 1 < len(text) and text[i + 1].isalpha())
        if not starts:
            i += 1
            continue
        start = i
        i += 1
        while i < len(text):
            c = text[i]
            if c.isalpha() or unicodedata.category(c).startswith("M"):
                i += 1
            elif c in "'’-\u00ad" + _CONTRIB_MARKS and i + 1 < len(text) and text[i + 1].isalpha():
                i += 1
            else:
                break
        result.append((start, i))
    return result


def _contrib_normal(raw, api):
    raw = re.sub(r"(?<=\w)[-\u00ad]\s+(?=\w)", "", raw)
    return api._normal(raw)


def _contrib_full_matches(text, forms, api):
    if not forms:
        return []
    words = _contrib_words(text)
    largest = max(len(form.split()) for form in forms) + 2
    matches = []
    for i, (start, _) in enumerate(words):
        for j in range(i, min(len(words), i + largest)):
            if j > i:
                separator = text[words[j - 1][1]:words[j][0]]
                if not separator.isspace() and not re.fullmatch(r"[-\u00ad]\s+", separator):
                    break
            end = words[j][1]
            normalized = _contrib_normal(text[start:end], api)
            if normalized in forms:
                matches.append((start, end, normalized))
    return matches


def _contrib_quoted_ranges(text):
    pairs = (('"', '"'), ("“", "”"), ("‘", "’"))
    ranges = []
    for left, right in pairs:
        expression = re.escape(left) + r"[^" + re.escape(right) + r"]*" + re.escape(right)
        ranges.extend((m.start(), m.end()) for m in re.finditer(expression, text))
    ranges.extend((m.start(), m.end()) for m in
                  re.finditer(r"(?<!\w)'[^'\n]+'(?!\w)", text))
    return ranges


def _contrib_context(heading, text, api):
    heading = api._normal(heading)
    heading = re.sub(r"^(?:[a-z]|\d+(?:\s+\d+)*)\s+", "", heading)
    explicit = {
        "author contributions", "authors contributions", "author contribution",
        "credit authorship contribution statement", "credit author statement",
        "credit authorship statement", "credit",
    }
    if heading in explicit:
        return "credit_fields" if heading == "credit" else "author_contributions"
    if heading not in {"acknowledgment", "acknowledgments",
                       "acknowledgement", "acknowledgements"}:
        return None
    owned = re.match(
        r"^\s*(?:the|our)\s+(?:research\s+and\s+writing|research|writing)"
        r"\s+(?:of|for)\s+(?:this|our|the\s+present)\s+(?:paper|manuscript|work)"
        r"\s+(?:was|were)\s+(?:led|conducted|carried\s+out)\s+by\b",
        text, re.I,
    )
    return "owned_paper_credit_ack" if owned else None


def _contrib_relation(text, start, end, fields_only=False):
    prefix, suffix = text[:start], text[end:]
    if re.match(r"\s*(?:et\s+al\b|[,;(]?\s*(?:19|20)\d{2}\b)", suffix, re.I):
        return None
    if re.match(r"\s*:\s*" + _CONTRIB_LABEL + r"\b", suffix, re.I):
        return "name_to_role_field"
    field_value_end = (bool(re.fullmatch(r"\s*[;.]?\s*", suffix)) or
                       bool(re.match(r"\s*[;.\n]\s*" + _CONTRIB_LABEL + r"\s*:", suffix, re.I)) or
                       bool(re.match(r"\s*" + _CONTRIB_ROLE + r"\b", suffix, re.I)))
    if field_value_end and re.search(r"(?:^|[;\n.])\s*" + _CONTRIB_LABEL + r"\s*:\s*$", prefix, re.I):
        return "role_field_to_name"
    if fields_only:
        return None
    subject_start = (not prefix.strip() or
                     bool(re.search(r"[.!?;]\s+$", prefix)) or
                     bool(re.search(r"(?:^|\n)\s*[•*–-]\s*$", prefix)))
    if subject_start and re.match(r"\s*(?:,\s*who\s+)?" + _CONTRIB_ROLE + r"\b", suffix, re.I):
        return "name_subject_to_role"
    if re.search(r"\b(?:led|written|drafted|prepared|conducted|coordinated)"
                 r"\s+by\s*$", prefix, re.I):
        return "passive_role_to_name"
    return None


def _contribution_name_ranges(block, names, *, heading, source_byline=(), rules_module=None):
    """Return raw name-only ranges and evidence, never whole-line masks.

    names may already be rules._normal-normalized. source_byline must consist of
    independently verified author lines. A block must not span source pages.
    """
    api = _contrib_rules(rules_module)
    text = "\n".join(line["text"] for line in block)
    result = {"context": None, "ranges": [], "matches": [], "uncertain": []}
    pages = {line["page"] for line in block if "page" in line}
    if len(pages) > 1 or "\f" in text:
        result["uncertain"].append("Contribution input spans source pages; preserved.")
        return result
    context = _contrib_context(heading, text, api)
    if context is None:
        return result
    result["context"] = context
    cutoff, offset = len(text), 0
    for line in block:
        if _CONTRIB_BOUNDARY.fullmatch(line["text"]) or _CONTRIB_CAPTION.match(line["text"]):
            cutoff = offset
            break
        offset += len(line["text"]) + 1
    eligible = text[:cutoff]
    if _CONTRIB_LITERAL.search(eligible):
        result["uncertain"].append("Contribution-like wording occurs in a prompt/example context; preserved.")
        return result
    if eligible.count('"') % 2 or eligible.count("“") != eligible.count("”"):
        result["uncertain"].append("Unclosed quotation in contribution input; preserved.")
        return result
    quoted = _contrib_quoted_ranges(eligible)
    usable = []
    for value in names:
        normalized = api._normal(value) if isinstance(value, str) else ""
        if (len(normalized.split()) >= 2
                and sum(c.isalpha() for c in normalized) >= 4):
            if normalized not in usable:
                usable.append(normalized)
        else:
            result["uncertain"].append("Unusable contribution-author metadata was excluded.")
    forms = {name: [{"name": name, "kind": "full_name"}] for name in usable}
    byline = "\n".join(line["text"] if isinstance(line, dict) else line
                       for line in source_byline)
    for name in usable:
        words = name.split()
        if len(words) != 3:
            continue
        alternatives = {" ".join(words[i:] + words[:i]) for i in (1, 2)}
        confirmed = {value for _, _, value in _contrib_full_matches(byline, alternatives, api)}
        for value in confirmed:
            if value != name:
                forms.setdefault(value, []).append(
                    {"name": name, "kind": "source_byline_cyclic_alias"})
    candidates = []
    for start, end, form in _contrib_full_matches(eligible, forms, api):
        owners = sorted({item["name"] for item in forms[form]})
        kinds = {item["kind"] for item in forms[form]}
        if len(owners) > 1 and "source_byline_cyclic_alias" in kinds:
            result["uncertain"].append("A cyclic name alias has multiple metadata owners; preserved.")
            continue
        candidates.append({"start": start, "end": end, "surface": text[start:end],
                           "kind": "full_name" if "full_name" in kinds else "source_byline_cyclic_alias",
                           "metadata_candidates": owners})
    initials = {}
    for name in usable:
        words = name.split()
        if 2 <= len(words) <= 4:
            letters = tuple(word[0].upper() for word in words)
            initials.setdefault(letters, []).append(name)
    for letters, owners in initials.items():
        pattern = r"(?<![\w.])" + r"\s*".join(re.escape(c) + r"\s*\." for c in letters) + r"(?![\w.])"
        for match in re.finditer(pattern, eligible):
            candidates.append({"start": match.start(), "end": match.end(),
                               "surface": match.group(), "kind": "author_initials",
                               "metadata_candidates": sorted(set(owners))})
    candidates = [item for item in candidates if not any(
        item["start"] < end and start < item["end"] for start, end in quoted)]
    selected = []
    for item in sorted(candidates, key=lambda x: (x["start"], -(x["end"] - x["start"]))):
        if selected and item["start"] < selected[-1]["end"]:
            continue
        selected.append(item)
    groups = []
    for item in selected:
        if groups and _CONTRIB_JOIN.fullmatch(text[groups[-1][-1]["end"]:item["start"]]):
            groups[-1].append(item)
        else:
            groups.append([item])
    for group in groups:
        relation = _contrib_relation(eligible, group[0]["start"], group[-1]["end"],
                                     fields_only=context == "credit_fields")
        if relation is None:
            continue
        for item in group:
            evidence = dict(item)
            evidence.update(
                relation=relation,
                removal_scope="established_contributor_name_slot",
                individual_author_resolution=("unique_metadata_name" if len(item["metadata_candidates"]) == 1
                                              else "ambiguous_within_metadata_author_set"),
                resolved_metadata_name=(item["metadata_candidates"][0]
                                        if len(item["metadata_candidates"]) == 1 else None),
            )
            result["matches"].append(evidence)
    result["matches"].sort(key=lambda x: x["start"])
    result["ranges"] = [(item["start"], item["end"]) for item in result["matches"]]
    return result


def _contribution_name_slices(block, names, *, heading, source_byline=(), rules_module=None):
    """Use existing rules._range_slices, returning slices plus range evidence."""
    api = _contrib_rules(rules_module)
    result = _contribution_name_ranges(
        block, names, heading=heading, source_byline=source_byline, rules_module=api)
    return list(api._range_slices(block, result["ranges"])), result


def _contribution_body(lines, heading, width):
    """A source credit paragraph is bounded to one page and one text column."""
    if not _heading(heading['text'], {
            'author contributions', 'authors contributions', 'author contribution',
            'credit authorship contribution statement', 'credit author statement',
            'credit authorship statement', 'credit', 'acknowledgment', 'acknowledgments',
            'acknowledgement', 'acknowledgements'}):
        return []
    if re.match(r'^\s*(?:[-*+•]\s|#{1,6}\s|[>"“‘])', heading['text']):
        return []
    lefts = _column_lefts(lines)
    right = lefts[id(heading)] >= width * .5
    column = [line for line in lines if (lefts[id(line)] >= width * .5) == right]
    prior = [line for line in column if line['rect'][1] <= heading['rect'][1] and
             (line['block'] == heading['block'] or
              0 <= heading['rect'][1] - line['rect'][3] <= 48)]
    if any(_CONTRIB_LITERAL.search(line['text']) or _CONTRIB_CAPTION.match(line['text']) or
           re.search(r'\b(?:example|literal|quoted|prompt|input|output)\s*:', line['text'], re.I)
           for line in prior):
        return None
    rows = []
    previous = heading
    for line in column:
        if line['rect'][1] <= heading['rect'][1]:
            continue
        if (_CONTRIB_BOUNDARY.fullmatch(line['text']) or
                _CONTRIB_CAPTION.match(line['text']) or
                _identity_section_kind(line['text'])):
            break
        if re.fullmatch(r'\d{1,3}', line['text'].strip()):
            break
        if line['rect'][1] - previous['rect'][3] > max(36, previous['size'] * 3):
            break
        if rows and abs(line['size'] - rows[0]['size']) > 2:
            break
        if line['size'] > heading['size'] + .5:
            break
        rows.append(line)
        previous = line
    return rows


def _credit_text(text, kind, names, allow_generic=False):
    if re.search(r'\b(?:participants?|workers?|annotators?|subjects?)\b.{0,80}\b(?:paid|compensated|consent)\b', text, re.I):
        return False
    if kind == 'acknowledgment' and _supported_author_credit(text, names):
        return True
    if allow_generic and re.search(r'\b(?:we|the authors)\s+(?:(?:would like|wish)\s+to\s+)?thank\b', text, re.I):
        return True
    if (re.search(r'\b(?:this|our|the)\s+(?:present\s+)?(?:work|research|study|project|paper)\b'
                  r'.{0,160}\bsupported\s+by\b', _flat(text), re.I) and
            re.search(r'\b(?:grants?|fund(?:ing)?|foundation|scholarship|studentship|fellowship)\b', text, re.I)):
        return True
    if re.search(r'(?i:\b(?:we|(?:the )?authors)\b[^.!?]{0,100}\bgratitude to)\s+[A-Z]', text):
        return True
    if re.search(r'(?i:\bthanks to)\s+[A-Z][\w.-]+', text):
        return True
    if _funding_sentence(text):
        return True
    if re.search(r'(?i:\b(?:thank|acknowledge|grateful to))\s+(?:the\s+)?[A-Z][\w.-]+', text):
        return True
    roles = r'\b(?:conceptualization|curat(?:ed|ion)|implementation|designed|developed|conducted|drafted|supervised|writing|contributed)\b'
    return bool(re.search(roles, text, re.I) and
                (kind == 'other_identity' or any(name in _normal(text) for name in names)))


def _mixed_acknowledgment_slices(lines, names):
    """Keep procedure/results sentences even inside a genuine credit section."""
    text = '\n'.join(line['text'] for line in lines)
    sentences = list(_sentence_spans(text))
    def scientific(value):
        value = _flat(value)
        return bool(re.search(
            r'\b(?:participants?|workers?|annotators?|subjects?|contributors?)\b.{0,100}'
            r'\b(?:paid|payments?|compensated|consent)\b'
            r'|\bwe\s+(?:have\s+)?(?:added|add|evaluate|train|measure|compare|propose|report|describe|managed|recruited|collected)\b'
            r'|\b(?:our|the)\s+(?:method|model|experiments?|results?)\s+(?:show|achieve|use|compare)\b',
            value, re.I))
    if not any(scientific(text[a:b]) for a, b in sentences):
        return None
    selected = [(a, b) for a, b in sentences if not scientific(text[a:b]) and
                _credit_text(_flat(text[a:b]), 'acknowledgment', names, allow_generic=True)]
    return list(_range_slices(lines, selected))


def _local_credit_lines(column, heading, kind, names, document_end=False):
    """Keep a credit fallback inside positively identified source text blocks."""
    start = next(i for i, item in enumerate(column) if item is heading)
    blocks = []
    for line in column[start:]:
        if line is not heading and _ack_boundary(line):
            break
        if blocks and blocks[-1][0]['block'] == line['block']:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    selected, credited = [], False
    for block in blocks:
        text = ' '.join(item['text'] for item in block)
        if _credit_text(text, kind, names):
            selected.extend(block)
            credited = True
        elif block == [heading]:
            selected.append(heading)
        else:
            break
    complete = bool(selected and re.search(r'[.!?][)\]\"”]*\s*$', selected[-1]['text']) and
                    not re.search(r'\bNos?[.]\s*$', selected[-1]['text']))
    explicit_authors = sum(name in _normal(' '.join(item['text'] for item in selected)) for name in names)
    return selected if credited and (complete or document_end and explicit_authors >= 2) else []



def _ethics_identity_spans(text):
    """Remove only named institutions and protocol IDs in the study's approval."""
    spans = []
    approval = re.compile(
        r'\b(?:our|this)\s+(?:data collection|(?:validation\s+)?study|research|project|work)\b'
        r'.{0,160}?\b(?:was|is|has been)\s+approved\b', re.I | re.S)
    institution = re.compile(
        r'\b(?P<prefix>at|by)\s+(?:the\s+)?'
        r'(?P<name>(?:University|Institute|Hospital|College|School)\b.{0,180}?)'
        r'(?=\s+Ethics\s+Committee\b|,\s*(?:under\s+)?(?:protocol|application)\b|[.;])', re.I | re.S)
    protocol = re.compile(
        r'(?:,\s*)?(?:under\s+)?(?:protocol|application)\s*'
        r'(?:IRB(?:-[A-Za-z]+)*\s*)?(?:No[.]?\s*|ID\s*|#\s*)?'
        r'[A-Z0-9-]*\d[A-Z0-9-]*', re.I)
    for match in approval.finditer(text):
        before = text[max(0, match.start()-80):match.start()]
        if (before.rstrip().endswith(('"', '“', "'", chr(96))) or
                re.search(r'\b(?:example|prompt|template|literal)\b[^\n]{0,30}:\s*$', before, re.I)):
            continue
        start = match.end()
        context = text[start:start+420]
        context = context[:next(_sentence_spans(context), (0,len(context)))[1]]
        if not re.search(r'\b(?:institutional\s+review\s+board|IRB|ethics\s+committee)\b', context, re.I):
            continue
        for item in institution.finditer(context):
            lo = item.start() if item.group('prefix').lower() == 'at' else item.start('name')
            spans.append((start+lo, start+item.end('name')))
        for item in protocol.finditer(context):
            spans.append((start+item.start(), start+item.end()))
    return sorted(set(spans))


def _range_slices(block, ranges):
    offset = 0
    for line in block:
        for start, end in ranges:
            lo, hi = max(0, start-offset), min(len(line['text']), end-offset)
            if lo < hi and line['text'][lo:hi].strip():
                yield line, lo, hi
        offset += len(line['text']) + 1


def _contributor_roster_lines(lines, names):
    """Explicit author rosters can hide identity cells while retaining metrics."""
    labels = {'github', 'github handle', 'handle', 'username', 'email', 'e mail',
              'first name', 'last name', 'full name', 'name', 'author', 'contributor',
              'affiliation', 'affiliations', 'institution'}
    contact_labels = {'github', 'github handle', 'handle', 'username', 'email', 'e mail'}
    captions = [line for line in lines if re.match(r'^Table\s+\d', line['text'], re.I) and
                re.search(r'\b(?:author|contributor)s?\b', line['text'], re.I) and
                re.search(r'\b(?:overview|roster|affiliations?|handles?|github)\b', line['text'], re.I) and
                not re.search(r'\b(?:example|reference|bibliography|baseline|comparison|cited)\b', line['text'], re.I)]
    selected = []
    for caption in captions:
        candidates = [line for line in lines if line['rect'][3] < caption['rect'][1] and _normal(line['text']) in labels]
        for anchor in candidates:
            headers = sorted([line for line in lines if abs(line['rect'][1]-anchor['rect'][1]) < 2],
                             key=lambda line: line['rect'][0])
            kinds = [_normal(line['text']) for line in headers]
            if (not any(kind in contact_labels for kind in kinds) or
                    not ('first name' in kinds and 'last name' in kinds or
                         any(kind in {'full name','name','author','contributor'} for kind in kinds))):
                continue
            top = max(line['rect'][3] for line in headers)
            cells = [line for line in lines if top < line['rect'][1] < caption['rect'][1] and
                     line['rect'][0] >= headers[0]['rect'][0]-3]
            assigned = []
            for line in cells:
                indices = [i for i, header in enumerate(headers) if line['rect'][0] >= header['rect'][0]-3]
                if indices:
                    assigned.append((line, kinds[max(indices)]))
            row_names = []
            for line, kind in assigned:
                if kind not in {'first name','full name','name','author','contributor'}:
                    continue
                value = line['text']
                if kind == 'first name':
                    last = [item['text'] for item, other in assigned if other == 'last name' and
                            abs(item['rect'][3]-line['rect'][3]) < 2]
                    value += ' ' + ' '.join(last)
                row_names.append(_normal(value))
            if not any(name == value for name in names for value in row_names):
                continue
            selected.extend(line for line in headers if _normal(line['text']) in labels)
            selected.extend(line for line, kind in assigned if kind in labels)
            break
    return list({id(line):line for line in selected}.values())


def _wrapped_header_contacts(header):
    found = set()
    for i, line in enumerate(header):
        if (not line['text'].startswith('{') or EMAIL.fullmatch(line['text']) or
                not re.fullmatch(r'[\w.+,@{}\s-]+', line['text'])):
            continue
        joined, selected, previous = line['text'], [i], line
        for j in range(i + 1, len(header)):
            other = header[j]
            if other['rect'][1] - line['rect'][1] > 65:
                break
            overlap = min(previous['rect'][2], other['rect'][2]) > max(previous['rect'][0], other['rect'][0])
            if not overlap:
                continue
            gap = other['rect'][1] - previous['rect'][1]
            if not 0 < gap < 22 or abs(other['size'] - line['size']) > 1:
                continue
            fragment = other['text'].strip()
            if (not re.fullmatch(r'[\w.+,@{}\s-]+', fragment) or
                    not (re.search(r'[,}@.]', fragment) or
                         joined.endswith('.') and re.fullmatch(r'[a-z]{2,6}', fragment))):
                break
            joined += fragment
            selected.append(j)
            if EMAIL.search(joined) and _contact_only(joined):
                found.update(selected)
                break
            if len(selected) == 4:
                break
            previous = other
    return found


def _locator_protected_context(text):
    text = URL.sub('', text)
    return bool(re.search(
        r'\b(?:example|literal|quoted|prompt|input|output|string)(?:\s+(?:url|link|text|sentence))?\s*[:=]'
        r'|\b(?:for example|e[.]g[.]|third[- ]party|baseline|cited|citation|et\s+al)\b'
        r'|\b(?:we|our code)\s+(?:uses?|reuses?|adopts?|follows?|compares?)\b'
        r'|\b(?:model|assistant|agent)\b.{0,40}\b(?:generated|produced|outputs?)\b', text, re.I))


def _locator_source_prefix(lines, index, offset):
    line = lines[index]
    ordered = _block_reading_lines(lines, line['block'])
    position = next(i for i, item in enumerate(ordered) if item is line)
    return _flat('\n'.join(item['text'] for item in ordered[:position]) + '\n' + line['text'][:offset])


def _own_named_method(title, intro_lines):
    label, separator, expansion = title.partition(':')
    label = label.strip()
    if not separator or not re.fullmatch(r'[A-Z][A-Za-z0-9-]{2,30}', label):
        return None
    introduction = _flat(' '.join(line['text'] for line in intro_lines))
    direct = r'\bwe\s+(?:introduce|present|propose|release|develop|build)\s+(?:the\s+)?' + re.escape(label) + r'\b'
    expanded = (r'\bwe\s+(?:introduce|present|propose|release|develop|build)\s+(?:the\s+)?' +
                re.escape(expansion.strip()) + r'\s*\(\s*' + re.escape(label) + r'\s*\)')
    for match in re.finditer(direct + '|' + expanded, introduction, re.I):
        before = introduction[max(0, match.start() - 120):match.start()]
        before = re.split(r'[.!?]\s+', before)[-1]
        if not _locator_protected_context(before) and not before.rstrip().endswith(('"', '“', '‘', "'", chr(96))):
            return label
    return None


def _additional_seed_proof(lines, index, offset, title, intro_lines, in_references=False, named_method=None):
    """Add only explicit named-method, own bullet release, and own-results seeds."""
    if in_references:
        return None
    prefix = _locator_source_prefix(lines, index, offset)
    if _locator_protected_context(prefix):
        return None
    label = named_method if named_method is not None else _own_named_method(title, intro_lines)
    if label:
        value = URL.sub('<URI>', prefix)
        named = (r'^\d{0,2}\s*' + re.escape(label) +
                 r'\s+comes\s+with\s+(?:open[- ]source\s+)?code\s+available\s+at\s*'
                 r'(?:<URI>\s+and\s+(?:a\s+)?public\s+leaderboard\s+available\s+at\s*)?$')
        if re.fullmatch(named, value, re.I):
            return 'metadata_named_method_release'
    ordered = _block_reading_lines(lines, lines[index]['block'])
    if ordered and ordered[0]['text'].lstrip().startswith('•') and re.search(r'\bour\b', prefix, re.I):
        # Classification-only normalization; emitted source slices stay original.
        first = ordered[0]
        stripped = re.sub(r'^\s*•\s*', '', first['text'])
        copied = [dict(line, text=stripped) if line is first else line for line in lines]
        adjusted = offset - (len(first['text']) - len(stripped)) if lines[index] is first else offset
        if _release_context(copied, index, adjusted):
            return 'explicit_own_bullet_release'
    number = re.fullmatch(r'\s*(\d{1,2})\s*', lines[index]['text'][:offset])
    if number:
        callout = re.escape(number.group(1))
        for block in {line['block'] for line in lines if line['block'] != lines[index]['block']}:
            text = _flat(' '.join(line['text'] for line in _block_reading_lines(lines, block)))
            release = re.search(r'\bResults\b.{0,260}\b(?:are|is)\s+(?:publicly\s+)?available\s+in\s+'
                                r'(?:the\s+)?(?:versioned\s+)?results?\s+repository[.\s]*' + callout + r'(?!\d)', text, re.I)
            if release and not _locator_protected_context(text):
                before = text[max(0, release.start() - 300):release.start()]
                if re.search(r'\bwe\s+(?:ran|run|evaluated|trained|measured)\b[^.!?]{0,240}[.]?\s*$', before, re.I):
                    return 'own_experimental_results_callout'
    return None


def _github_uri_parts(uri):
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(uri)
        parts = tuple(parsed.path.strip('/').split('/'))
        if (parsed.scheme not in {'http', 'https'} or parsed.netloc.lower() != 'github.com' or
                any(not part or part in {'.', '..'} or '%' in part for part in parts)):
            return None
        return parts
    except ValueError:
        return None


def _owned_uri_repeat(uri, proven_uris, context='', in_references=False):
    """Exact URI or tree/blob within a separately proven complete repository."""
    if in_references or _locator_protected_context(context):
        return False
    if uri in proven_uris:
        return True
    candidate = _github_uri_parts(uri)
    if (not candidate or len(candidate) < 4 or candidate[2] not in {'tree', 'blob'} or
            candidate[2] == 'blob' and len(candidate) < 5):
        return False
    for seed in proven_uris:
        root = _github_uri_parts(seed)
        if root and len(root) == 2 and candidate[:2] == root:
            return True
    return False


def _release_context(lines, index, offset, known_target=False):
    """Recognize manuscript artifact releases, excluding dependency/example prose."""
    line = lines[index]
    ordered = _block_reading_lines(lines, line['block'])
    position = next(i for i, item in enumerate(ordered) if item is line)
    prefix = _flat('\n'.join(item['text'] for item in ordered[:position]) +
                   '\n' + line['text'][:offset])[-1800:].strip()
    examples = (r'\b(?:example|literal|quoted|prompt|input|output|string)(?:\s+(?:url|link|text|sentence))?\s*[:=]'
                r'|\b(?:for example|e\.g\.|the string)\b'
                r'|\b(?:string|phrase|sentence|prompt)\b.{0,40}["“‘]')
    borrowed = (r'\b(?:baseline|third[- ]party|cited|citation|et\s+al|prior work|previous paper)\b'
                r'|\b(?:uses?|using|reuses?|adopts?|following|compares?|comparison with|associated with)\b')
    if re.search(examples, prefix[-350:], re.I):
        return False
    artifact = (r'(?:(?:our|the|all|both|full|complete|corresponding|sample|a|an)\s+)*'
                r'(?:(?:source|experimental|benchmark|trained|numerical)\s+)?'
                r'(?:codes?|implementation(?:s)?|demos?|models?|methods?|datasets?|data|resources|benchmarks?|'
                r'project\s+(?:page|website|homepage)|reproducibility\s+packages?)')
    release = re.compile(
        r'(?:^|[.!?]\s+)[\d*†‡§\s]*' + artifact +
        r'[^.!?]{0,190}?\b(?:available|accessible|released|open[- ]sourced|can\s+be\s+found)\b'
        r'|(?:^|[.!?]\s+)\d{0,2}\s*(?:to\s+[^,.!?]{1,100},\s*)?we\s+(?:have\s+)?(?:releas(?:e|ed)|publish(?:ed)?|provid(?:e|ed)|share(?:d)?|open-sourc(?:e|ed))\b'
        r'[^.!?]{0,160}\b(?:codes?|data|datasets?|models?|benchmark|resources|implementation)\b'
        r'|\bwe\s+(?:have\s+)?(?:made|make)\s+our\s+[^.!?]{0,90}?'
        r'\b(?:code|data|dataset|model|checkpoint|implementation)s?\b[^.!?]{0,60}\bavailable\b'
        r'|(?<!\w)our\s+' + artifact + r'[^.!?]{0,190}\b(?:available|released|accessible|can\s+be\s+found)\b'
        r'|\bour\s+(?:anonymous\s+|demo\s+)?(?:web\s*page|website|project\s+(?:page|website))\s*:'
        r'|(?:^|[.!?]\s+)(?:source code|project\s+(?:page|website|homepage))\s*:'
        r'|\b(?:more\s+)?details\s+are\s+(?:given|provided)\s+in\s+(?:the\s+|our\s+)?'
        r'(?:appendix|supplement)\b[^!?]{0,100}\band\s+(?:the|our)\s+(?:full\s+|source\s+)?code\b'
        r'[^.!?]{0,80}\b(?:available|released|can\s+be\s+found)\b'
        r'|\bwe\s+showcase\s+(?:some\s+)?examples\b[^.!?]{0,100}\b(?:following\s+URL|available)\b'
        r'|(?:^|[.!?]\s+)A demo\b[^.!?]{0,220}\b(?:the|this|our)\s+'
        r'(?:paper|results|method)\b[^.!?]{0,120}\bavailable\b',
        re.I | re.S)
    anchors = list(release.finditer(prefix))
    if not anchors:
        # A numbered locator may be defined in another source block, provided
        # its exact callout belongs to this manuscript's artifact release.
        footnote = re.fullmatch(r'\s*(\d{1,2})\s*', line['text'][:offset])
        if footnote:
            number = re.escape(footnote.group(1))
            for block_id in {item['block'] for item in lines if item['block'] != line['block']}:
                other = _flat('\n'.join(item['text'] for item in _block_reading_lines(lines, block_id)))
                for sentence_start, sentence_end in _sentence_spans(other):
                    sentence = other[sentence_start:sentence_end]
                    if re.search(examples + '|' + borrowed, sentence, re.I):
                        continue
                    if re.search(r'\b(?:we\s+(?:release|share|publish|provide)\b[^.!?]{0,80}\bour\s+'
                                 r'(?:code|data|datasets?|models?|implementation)\b[^.!?]{0,100}'
                                 r'|\bour\s+(?:code|data|datasets?|models?|implementation)\b'
                                 r'[^.!?]{0,80}\b(?:available|released|shared)\b[^.!?]{0,50})' +
                                 number + r'(?!\d)', sentence, re.I):
                        return True
        return False
    anchor = anchors[-1]
    clause = prefix[anchor.start():].lstrip('.!? \t\r\n')
    context = prefix[max(0, anchor.start() - 180):]
    explicit_owner = bool(re.search(r'(?<![a-z])our\b|\b(?:this|the)\s+paper\b'
                                    r'|(?:^|,\s*)we\s+(?:have\s+)?(?:releas(?:e|ed)|publish(?:ed)?|provid(?:e|ed)|share(?:d)?|open-sourc(?:e|ed))\b',
                                    clause, re.I) or
                          re.match(r'(?:more\s+)?details\s+are\s+(?:given|provided)\s+in\s+', clause, re.I))
    project_label = bool(re.fullmatch(r'project\s+(?:page|website|homepage)\s*:', clause, re.I))
    prior_current_work = bool(project_label and
        re.search(r'\b(?:we|our)\b', prefix[:anchor.start()], re.I) and
        not re.search(borrowed, URL.sub('', prefix[max(0, anchor.start()-220):anchor.start()]), re.I))
    explicit_owner = explicit_owner or prior_current_work
    # A prior comparison does not change an explicit current-manuscript release.
    if (re.search(borrowed, URL.sub('', prefix[anchor.start():]), re.I) or
            (not explicit_owner and re.search(borrowed, URL.sub('', context), re.I))):
        return False
    if (not explicit_owner and not known_target and
            re.match(r'^(?:\d+\s*)?(?:(?:a|an|the|all|full|complete)\s+)*(?:demo|model|method)s?\b',
                     clause, re.I)):
        return False
    if (not explicit_owner and not known_target and
            re.search(r'\b(?:data|datasets?|resources|benchmarks?)\b', anchor.group(), re.I) and
            not re.search(r'\b(?:code|implementation|project)\b', anchor.group(), re.I)):
        return False
    if (not explicit_owner and not known_target and
            re.fullmatch(r'project\s+(?:page|website|homepage)\s*:', clause, re.I)):
        return False
    footnote = re.match(r'^(\d{1,2})\s*', clause)
    if footnote and not explicit_owner and not known_target:
        number = re.escape(footnote.group(1))
        callout = re.compile(r'\b(?:code|implementation)\s*' + number + r'\b', re.I)
        for other in lines:
            if other['block'] == line['block'] or not callout.search(other['text']):
                continue
            peers = _block_reading_lines(lines, other['block'])
            text = _flat(' '.join(item['text'] for item in peers))
            match = callout.search(text)
            local = text[max(0, match.start() - 90):match.end() + 180]
            if re.search(borrowed, local, re.I):
                return False
        declared = re.match(r'^\d{1,2}\s*the\s+(?:(?:source|full)\s+)?code\s+'
                            r'(?:is|can be)\b', clause, re.I)
        own_callout = re.compile(r'\bour\s+(?:source\s+)?(?:code|implementation)\s*' +
                                 number + r'\b', re.I)
        if not declared and not any(item['block'] != line['block'] and
                                    own_callout.search(item['text']) for item in lines):
            return False
    tail = prefix[anchor.end():]
    tail = re.sub(r'[.!?]\s+(?:Link|URL|Repository)\s*:\s*$', '', tail, flags=re.I)
    def omit_url(match):
        token = match.group()
        following = tail[match.end():].lstrip()
        domain_wrap = token.endswith('.') and re.match(r'^[a-z]{2,}/', following)
        return token[-1] if token[-1] in '.!?' and not domain_wrap else ''
    prose_tail = URL.sub(omit_url, tail)
    return len(tail) <= 280 and not re.search(r'[.!?]\s+', prose_tail)


def _complete_uri(value):
    return bool(re.fullmatch(r'(?:https?://|www[.])[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#]\S*)?', value))


def _visible_uri_regions(page, lines, index, start, uri):
    """Bind a complete source URI to exact visible fragments, never link labels."""
    import pymupdf as fitz

    line = lines[index]
    ordered = _block_reading_lines(lines, line['block'])
    position = next(i for i, item in enumerate(ordered) if item is line)
    row_lefts = _column_lefts(ordered)
    def row_bounds(item):
        peers = [other for other in ordered if row_lefts[id(other)] == row_lefts[id(item)] and
                 abs(other['rect'][3] - item['rect'][3]) <= max(1, item['size'] * .25)]
        return row_lefts[id(item)], max(other['rect'][2] for other in peers)
    consumed, regions, previous = 0, [], None
    for source_line in ordered[position:position + 5]:
        if previous is not None:
            if (not 0 <= source_line['rect'][1] - previous['rect'][1] <= max(24, line['size'] * 2.5) or
                    abs(source_line['size'] - line['size']) > 1.2 or
                    min(row_bounds(source_line)[1], row_bounds(previous)[1]) <=
                    max(row_bounds(source_line)[0], row_bounds(previous)[0])):
                return []
        text = source_line['text']
        begin = start if previous is None else 0
        offset = begin
        while offset < len(text) and consumed < len(uri):
            char = text[offset]
            if not char.isspace():
                if char != uri[consumed]:
                    return []
                consumed += 1
            offset += 1
        if consumed == len(uri):
            suffix = text[offset:]
            if suffix and not (suffix[0].isspace() or re.match(r'^[.,;:)\]}]+(?:\s|$)', suffix)):
                return []
        piece = text[begin:offset].strip()
        if not piece:
            return []
        rects = page.search_for(piece, clip=fitz.Rect(source_line['rect']))
        if len(rects) != 1:
            return []
        regions.append({'text': piece, 'rect': list(rects[0]), 'uri': uri})
        if consumed == len(uri):
            return regions
        previous = source_line
    return []


def _release_url_regions(page, lines, index, known_links=(), explicit_owner=False, seed_proof=None, source_links=None):
    """Exact visible URL rectangles; link targets never authorize word deletion."""
    import pymupdf as fitz

    line = lines[index]
    regions = []
    links = ([link for link in page.get_links() if link.get('uri')]
             if source_links is None else source_links)
    known = {uri for value in known_links
             for uri in (value.rstrip('.,;)'), 'https://' + value.rstrip('.,;)'), 'http://' + value.rstrip('.,;)'))
             if _complete_uri(uri)}
    ordered = _block_reading_lines(lines, line['block'])
    position = next(i for i, item in enumerate(ordered) if item is line)
    for match in URI_START.finditer(line['text']):
        prefix = _flat(' '.join(item['text'] for item in ordered[:position]) +
                       ' ' + line['text'][:match.start()])[-500:]
        protected = re.search(
            r'\b(?:example|literal|quoted|prompt|input|output|string)(?:\s+(?:url|link|text|sentence))?\s*[:=]'
            r'|\b(?:for example|e\.g\.|the string|literal URL|third[- ]party|baseline|cited|citation)\b'
            r'|\b(?:we|our code)\s+(?:use|reuse|adopt|follow)\b'
            r'|\b(?:string|phrase|sentence|prompt)\b.{0,40}["“‘]', prefix, re.I)
        candidates = known | {link['uri'].rstrip('.,;)') for link in links
                              if _complete_uri(link['uri'].rstrip('.,;)')) and
                              fitz.Rect(link['from']).intersects(fitz.Rect(line['rect']))}
        for uri in sorted(candidates, key=len, reverse=True):
            repeated = _owned_uri_repeat(uri, known, prefix) and not protected
            seeded = bool(seed_proof and seed_proof(match.start()))
            if not (explicit_owner or seeded or repeated or _release_context(lines, index, match.start(), uri in known)):
                continue
            pieces = _visible_uri_regions(page, lines, index, match.start(), uri)
            if pieces:
                regions.extend(pieces)
                break
        else:
            # Keep the literal complete-URL path for unannotated releases.
            raw_match = URL.match(line['text'], match.start())
            if raw_match is None:
                continue
            raw = raw_match.group()
            piece = raw.rstrip('.,;)')
            if not _complete_uri(piece) or not (explicit_owner or
                    bool(seed_proof and seed_proof(match.start())) or
                    _owned_uri_repeat(piece, known, prefix) or _release_context(
                    lines, index, match.start(), piece in known)):
                continue
            starts = page.search_for(raw, clip=fitz.Rect(line['rect']))
            annotations = [link for link in links
                           if any(fitz.Rect(link['from']).intersects(rect) for rect in starts) and
                           (link['uri'].startswith(raw) or link['uri'] == piece)]
            if annotations and not any(link['uri'].rstrip('.,;)') == piece for link in annotations):
                continue
            for rect in page.search_for(piece, clip=fitz.Rect(line['rect'])):
                regions.append({'text': piece, 'rect': list(rect), 'uri': piece})
    # A source URI annotation can bind a printed host/path or owner/path.
    # Plain labels and truncated paths never supply a locator identity.
    for link in links:
        uri = link['uri'].rstrip('.,;)')
        if not _complete_uri(uri) or not fitz.Rect(link['from']).intersects(fitz.Rect(line['rect'])):
            continue
        host_path = re.sub(r'^https?://', '', uri)
        path = host_path.partition('/')[2]
        forms = [(host_path, True)]
        if path.count('/') >= 1 and not path.endswith('/'):
            forms.append((path, False))
        for visible, host_form in forms:
            first = visible.split('/', 1)[0]
            if not first:
                continue
            for match in re.finditer(re.escape(first), line['text']):
                before = line['text'][:match.start()]
                if (before and re.search(r'[\w@./:-]$', before) and
                        not re.fullmatch(r'\d{1,2}', before)):
                    continue
                local_owner = explicit_owner or bool(seed_proof and seed_proof(match.start())) or _release_context(lines, index, match.start(), uri in known)
                if (not host_form and not explicit_owner and not before.strip() and position and
                        re.search(r'[.!?]\s*$', ordered[position - 1]['text'])):
                    local_owner = False
                context = _flat(' '.join(item['text'] for item in ordered[:position]) + ' ' + before)[-500:]
                if not local_owner and not (host_form and _owned_uri_repeat(uri, known, context) and not re.search(
                        r'\b(?:example|literal|quoted|prompt|input|output|baseline|cited|citation|use|reuse|adopt)\b',
                        context, re.I)):
                    continue
                pieces = _visible_uri_regions(page, lines, index, match.start(), visible)
                for piece in pieces:
                    piece['uri'] = uri
                regions.extend(pieces)
    return regions


def _affiliation_fragment(line, header):
    # A short organization label inherits only a positively identified source
    # text block, never an arbitrary title-to-abstract rectangle.
    if not (re.fullmatch(r'[A-Z][A-Z0-9&-]{1,15}', line['text']) or
            re.fullmatch(r'Project\s+[A-Z][A-Za-z0-9& -]{1,40}', line['text']) or
            re.fullmatch(r'\d+\s*[A-Z][A-Za-z& -]{1,40} (?:Research|Labs?|Group)', line['text']) or
            re.match(r'^\d{1,2}\s*[A-Z].*,\s*\d{1,2}\s*[A-Z]', line['text'])):
        return False
    peers = [item for item in header if item['block'] == line['block']]
    return any(AFFILIATION.search(item['text']) and
               abs(item['size'] - line['size']) < 1 for item in peers)


def _corporate_affiliation(text):
    return bool(re.search(r'\b(?:Inc|Incorporated|Ltd|Limited|LLC|Corp|Corporation)\.?$', text, re.I) and
                _name_shape(text))


def _supplement_headers(pages, sizes, paper):
    """An exact manuscript title anchors a later supplement front page."""
    title = _normal(paper.get('title') or '')
    if not title:
        return
    for number, lines in enumerate(pages[1:], 2):
        header = [line for line in lines if line['rect'][3] < sizes[number - 1][1] * .35]
        titles = _matched_lines(header, [title])
        if not titles:
            continue
        title_end = max(header[i]['rect'][3] for i in titles)
        markers = [line for line in header if line['rect'][1] >= title_end and
                   _normal(line['text']) in {'supplementary material', 'supplementary materials',
                                            'supplemental material', 'supplementary information'}]
        if markers:
            yield number, header, markers


def _supplement_identities(pages, sizes, paper, names):
    """Repeated manuscript title plus explicit supplement title anchors bylines."""
    result = []
    for number, header, markers in _supplement_headers(pages, sizes, paper):
        for marker in markers:
            previous = marker
            found_author = False
            for line in header:
                if line['rect'][1] < marker['rect'][3]:
                    continue
                gap = line['rect'][1] - previous['rect'][3]
                if gap > (24 if found_author else 65):
                    break
                text = line['text']
                if _byline_only(text, names) or _byline_with_affiliation(text, names):
                    category = 'author'
                    found_author = True
                elif found_author and (AFFILIATION.search(text) or _corporate_affiliation(text)):
                    category = 'affiliation'
                elif found_author and EMAIL.search(text) and _contact_only(text):
                    category = 'contact'
                else:
                    break
                result.append((number, line, category))
                previous = line
    return result


def _adjacent_publication_notices(lines, bounds, redactions, names=()):
    months = (r'Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|'
              r'Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?')
    notice = re.compile(r'Preprint\.\s*(?:Under review|Work in progress|(?:' + months +
                        r')\.?\s+\d{1,2},?\s+(?:19|20)\d{2})\.?', re.I)
    contacts = [item['rect'] for item in redactions if item['page'] == 1 and
                item['category'] in {'contact', 'other_identity', 'affiliation'} and
                item['rect'][1] > bounds.height * .65]
    def adjacent(line):
        return any(0 <= line['rect'][1] - rect[3] < 32 and
                   min(line['rect'][2], rect[2]) > max(line['rect'][0], rect[0]) for rect in contacts)
    found = [line for line in lines if line['size'] <= 10 and
             line['rect'][1] > bounds.height * .65 and notice.fullmatch(line['text']) and adjacent(line)]
    aliases = list(names) + [name.split()[0][0] + ' ' + name.split()[-1]
                             for name in names if len(name.split()) >= 2]
    for source_block in _text_blocks(lines, bounds):
        start = next((i for i, line in enumerate(source_block) if
                      re.match(r'^(?:Proceedings of\b|Permission to make\b|Copyright\b|©|Submitted to\b)',
                               line['text'], re.I)), None)
        if start is None:
            continue
        block = source_block[start:]
        if (block[0]['rect'][1] <= bounds.height * .65 or max(line['size'] for line in block) > 10 or
                max(line['rect'][2] for line in block) - min(line['rect'][0] for line in block) > bounds.width * .85):
            continue
        prior = [item['text'] for item in lines
                 if 0 <= block[0]['rect'][1] - item['rect'][3] < 45 and
                 min(item['rect'][2], block[0]['rect'][2]) > max(item['rect'][0], block[0]['rect'][0])]
        if re.search(r'\b(?:example|quoted|literal|prompt)(?:\s+\w+)?\s*:', ' '.join(prior), re.I):
            continue
        text = _flat(' '.join(line['text'] for line in block))
        column = max(line['rect'][2] for line in block) - min(line['rect'][0] for line in block) < bounds.width * .52
        proceedings = bool(column and re.match(r'^Proceedings of\b', text, re.I) and
                           re.search(r'Copyright\s+\d{4}\s+by\s+the\s+author\(s\)[.]?$', text, re.I) and
                           (adjacent(block[0]) or re.search(r'\bPMLR\s*\d+,\s*\d{4}\b', text)))
        copyright_notice = bool(re.fullmatch(r'Copyright\s+\d{4}\s+by\s+the\s+author\(s\)[.]?', text, re.I))
        author_copyright = text.startswith('©') and _byline_only(text[1:].strip(), aliases)
        permissions = bool(re.match(r'^Permission to make digital or hard copies\b', text, re.I) and
                           re.search(r'\bPublication rights licensed to\b', text, re.I) and
                           re.search(r'\bISBN\b', text) and re.search(r'https?://doi[.]org/\S+[.]?$', text))
        submission = bool(re.match(r'^Submitted to\b', text, re.I) and
                          re.search(r'\bConference\b.*\b(?:19|20)\d{2}\b', text, re.I) and
                          re.search(r'Do not distribute[.]?$', text, re.I))
        if proceedings or copyright_notice or author_copyright or permissions or submission:
            found.extend(block)

    return found


_PUB_MONTHS = {name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), 1)}
_PUB_GENERIC = set("the of and for lab labs laboratory laboratories research group "
               "center centre institute university department school faculty nlp ai".split())
_PUB_CAPTION = re.compile(r"^(?:Figure|Fig[.]?|Table|Algorithm|Listing)\s*(?:\d+|[IVXLC]+)\b", re.I)
_PUB_EXAMPLE = re.compile(r"^(?:references|bibliography)\b|\b(?:example|literal|quoted|prompt|sample|input|output)\s*:", re.I)
_PUB_NAMED_VENUE = re.compile(
    r"^Published at\s+(?=.{5,}$).+\b(?:Workshop|Conference|Symposium|Congress)\b"
    r".*\b(?:19|20)\d{2}[.]?$", re.I)


def _publication_calendar_date(text):
    from datetime import date
    """Return a real date for a complete date literal, never a date substring."""
    value = text.strip()
    numeric = re.fullmatch(r"((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", value)
    named = re.fullmatch(r"([A-Za-z]+)[.]?\s+(\d{1,2}),?\s+((?:19|20)\d{2})", value)
    if numeric:
        year, month, day = map(int, numeric.groups())
    elif named:
        month_name, day, year = named.groups()
        choices = [number for name, number in _PUB_MONTHS.items()
                   if month_name.casefold() == name or
                   (len(month_name) == 3 and name.startswith(month_name.casefold())) or
                   (month_name.casefold() == "sept" and number == 9)]
        if len(choices) != 1:
            return None
        month, day, year = choices[0], int(day), int(year)
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _publication_tokens(text):
    text = unicodedata.normalize("NFKD", text).casefold()
    return set(re.findall(r"[^\W\d_]+", "".join(
        c for c in text if not unicodedata.combining(c))))


def _publication_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _publication_frontmatter_lines(lines, *, title_indexes, author_indexes,
                                  abstract_index, page_size, graphics,
                                  header_identity_indexes=(), branding_indexes=(),
                                  excluded_indexes=(), margin_identifier_indexes=()):
    """Return original source lines for three bounded publication formats.

    Additional identity/branding indices must be source-confirmed, not guessed
    from being above Abstract. Supplied rectangles include images and drawings.
    Call only on the first physical page. This does not validate PDF anonymity.
    """
    if graphics is None:
        return []
    valid = set(range(len(lines)))
    title = set(title_indexes)
    authors = set(author_indexes)
    identities = authors | set(header_identity_indexes)
    brands = set(branding_indexes)
    margin_ids = set(margin_identifier_indexes)
    if (not title or not authors or not (title | identities | brands) <= valid or
            not margin_ids <= valid or title & identities or title & brands):
        return []
    if abstract_index is not None and abstract_index not in valid:
        return []
    width, height = page_size
    if width <= 0 or height <= 0:
        return []
    protected = set(excluded_indexes) | title | identities | brands
    starts = {}
    for line in lines:
        if _PUB_CAPTION.match(line["text"]) or _PUB_EXAMPLE.search(line["text"]):
            starts[line["block"]] = min(starts.get(line["block"], float("inf")), line["rect"][1])
    protected |= {i for i, line in enumerate(lines) if
                  line["block"] in starts and line["rect"][1] >= starts[line["block"]]}
    top = min(lines[i]["rect"][1] for i in title)
    bottom = max(lines[i]["rect"][3] for i in title)
    title_size = max(lines[i]["size"] for i in title)
    title_left = min(lines[i]["rect"][0] for i in title)
    title_right = max(lines[i]["rect"][2] for i in title)
    abstract_top = lines[abstract_index]["rect"][1] if abstract_index is not None else None
    source_ids = [i for i in identities if lines[i]["rect"][1] >= bottom and
                  (abstract_top is None or lines[i]["rect"][3] < abstract_top)]
    if not source_ids or not any(i in authors for i in source_ids):
        return []
    identity_tokens = set().union(*(_publication_tokens(lines[i]["text"]) for i in source_ids)) - _PUB_GENERIC
    found = []
    for i, line in enumerate(lines):
        rect, text = line["rect"], line["text"].strip()
        if (i in protected or line["size"] >= title_size * .95 or
                any(_publication_overlap(rect, box) for box in graphics)):
            continue
        # A table cell or caption continuation is not an isolated header block.
        if any(j != i and other["block"] == line["block"] and other["text"].strip()
               for j, other in enumerate(lines)):
            continue
        nearby = [other["text"] for other in lines if other is not line and
                  0 <= rect[1] - other["rect"][3] < 36 and
                  min(rect[2], other["rect"][2]) > max(rect[0], other["rect"][0])]
        if any(_PUB_EXAMPLE.search(value) or _PUB_CAPTION.match(value) for value in nearby):
            continue
        pretitle = rect[3] <= top and rect[1] < height * .18 and top - rect[3] <= 72
        # A model/date table can use separate blocks for each cell. Only known
        # vertical margin identifiers and a corroborating branding row may share
        # a candidate's height; an arbitrary sidebar/table label cannot.
        same_row = [j for j, other in enumerate(lines) if j != i and j not in margin_ids and
                    other["text"].strip() and
                    min(rect[3], other["rect"][3]) - max(rect[1], other["rect"][1]) >=
                    .5 * min(rect[3] - rect[1], other["rect"][3] - other["rect"][1])]
        if any(j not in brands or not pretitle for j in same_row):
            continue
        venue_name = _publication_tokens(text) - {"published", "at", "a", "an", "the", "workshop",
                                     "conference", "symposium", "congress", "on", "in", "of", "and"}
        if pretitle and venue_name and _PUB_NAMED_VENUE.fullmatch(text):
            found.append(line)
            continue
        parsed = _publication_calendar_date(text)
        if parsed is None:
            continue
        if pretitle and re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
            own_brand = any(
                lines[j]["rect"][3] <= top and
                abs(lines[j]["rect"][1] - rect[1]) < 24 and
                (_publication_tokens(lines[j]["text"]) - _PUB_GENERIC) & identity_tokens
                for j in brands)
            if own_brand:
                found.append(line)
            continue
        if abstract_top is None or not re.fullmatch(r"[A-Za-z]+[.]?\s+\d{1,2},?\s+\d{4}", text):
            continue
        last_id = max(lines[j]["rect"][3] for j in source_ids)
        centered = abs((rect[0] + rect[2] - title_left - title_right) / 2) <= 24
        if not (centered and 0 <= rect[1] - last_id <= 48 and
                0 <= abstract_top - rect[3] <= 64):
            continue
        interval = [title_left, last_id, title_right, abstract_top]
        if any(_publication_overlap(interval, box) for box in graphics):
            continue
        if any(j != i and j != abstract_index and j not in margin_ids and other["text"].strip() and
               other["rect"][1] >= last_id and other["rect"][3] <= abstract_top
               for j, other in enumerate(lines)):
            continue
        found.append(line)
    return found


def _publication_banners(lines, paper):
    """Match small pre-title labels to the frozen publication/date metadata."""
    from datetime import date

    title = _normal(paper.get('title') or '')
    titles = _matched_lines(lines, [title]) if title else set()
    if not titles:
        return []
    top = min(lines[i]['rect'][1] for i in titles)
    size = max(lines[i]['size'] for i in titles)
    title_blocks = {lines[i]['block'] for i in titles}
    venues = {_normal(str(entry['venue']) + ' ' + str(entry['venue_year']))
              for entry in paper.get('source_entries') or []
              if isinstance(entry, dict) and entry.get('venue') and entry.get('venue_year')}
    dates = set()
    for version in paper.get('versions') or []:
        value = version.get('submitted_at') if isinstance(version, dict) else None
        if isinstance(value, str):
            try:
                dates.add(date.fromisoformat(value[:10]).isoformat())
            except ValueError:
                pass
    notice = any(i not in titles and VENUE.search(line['text']) for i, line in enumerate(lines))
    return [line for line in lines if line['rect'][3] <= top and line['size'] < size * .95 and
            line['block'] not in title_blocks and
            (_normal(line['text']) in venues or notice and line['text'] in dates)]



def _short_affiliation(line, header, anchors, names=(), title='', graphic_top=float('inf')):
    """An abbreviation needs both a byline anchor and contact/marker evidence."""
    text = line['text']
    label = re.match(r'^(\d{1,2})[*†‡]?\s*([A-Z][\w &.,()/-]{0,100})$',
                     unicodedata.normalize('NFKC', _without_spacing_accents(text)))
    numbers = {value for i in anchors
               for value in re.findall(r'\b\d{1,2}\b', _normal(header[i]['text']))}
    numbered_label = bool(label and label.group(1) in numbers and
        line['rect'][1] < graphic_top and
        any(header[i]['rect'][3] <= line['rect'][1] and
            abs(line['size'] - header[i]['size']) <= 3 for i in anchors) and
        any(AFFILIATION.search(item['text']) or
            EMAIL.search(item['text']) and _contact_only(item['text']) for item in header))
    domain_label = bool(re.fullmatch(r'[A-Z][A-Za-z]+', text) and any(
        text.casefold() in domain.casefold().split('.')
        for item in header for domain in re.findall(r'@([\w.-]+)', item['text'])))
    plain = re.fullmatch(r'\d*\s*([A-Z][A-Za-z]{1,24})', text)
    multi_author_label = bool(plain and plain.group(1).casefold() in {'apple', 'meta'} and
        line['rect'][1] < graphic_top and plain.group(1).casefold() not in _normal(title).split() and any(
        sum(name in _normal(header[i]['text']) for name in names) >= 2 and
        0 < line['rect'][1] - header[i]['rect'][1] < 28 and
        abs(line['size'] - header[i]['size']) <= 1 and
        (abs((line['rect'][0] + line['rect'][2]) - (header[i]['rect'][0] + header[i]['rect'][2])) < 24 or
         abs(line['rect'][0] - header[i]['rect'][0]) < 3)
        for i in anchors))
    if ((not re.search(r'(?<![A-Za-z])[A-Z]{2,}\b', text) and not domain_label and not multi_author_label and not numbered_label) or len(text.split()) > 8 or
            re.search(r'\b(?:figure|table|benchmark|baseline|theorem|example input|training|model|accuracy|score|results|algorithm)\b',
                      label.group(2) if label else text, re.I)):
        return False
    def aligned(other):
        return (min(line['rect'][2], other['rect'][2]) >
                max(line['rect'][0], other['rect'][0]))
    byline = any(0 <= line['rect'][1] - header[i]['rect'][1] <= 28 and
                 line['rect'][1] > header[i]['rect'][1] + 3 and aligned(header[i])
                 for i in anchors)
    contacts = any(0 < item['rect'][1] - line['rect'][1] < 30 and
                   EMAIL.search(item['text']) and _contact_only(item['text']) and aligned(item)
                   for item in header)
    marker = bool(re.search(r'[†‡§¶♠♣♥♦◊⋄♯]', text))
    return numbered_label or byline and (contacts or marker or multi_author_label)


def _known_identity_after_header_break(line, header, anchors, names, title_end,
                                       boundary, graphic_top=float('inf'), title=''):
    """Recover only separately evidenced identities; unknown rows remain intact."""
    if not anchors or boundary is None:
        return None
    limit = min(boundary, graphic_top)
    if not title_end <= line['rect'][1] < line['rect'][3] <= limit:
        return None
    prior = [header[i] for i in anchors if header[i]['rect'][1] <= line['rect'][1]]
    if not prior:
        return None
    start = max(item['rect'][1] for item in prior)
    science = re.compile(r'\b(?:figure|fig[.]|table|caption|teaser|example|literal|prompt|'
                         r'input|output|benchmark|baseline|training|models?|methods?|'
                         r'accuracy|score|results?|algorithm|datasets?|ground truth)\b', re.I)
    for item in header:
        if (start <= item['rect'][1] <= line['rect'][1] and
                not (_byline_only(item['text'], names) or
                     EMAIL.search(item['text']) and _contact_only(item['text']))):
            if science.search(re.sub(r'^[\d*∗†‡§¶♠♣♥♦◊⋄♯⋆.,()\s]+', '', item['text'])):
                return None
    text = line['text']
    if _byline_only(text, names):
        return 'author'
    if EMAIL.search(text) and _contact_only(text):
        return 'contact'
    numbers = {value for item in prior for value in re.findall(r'\b\d{1,2}\b', _normal(item['text']))}
    symbols = set(re.findall(r'[*∗†‡§¶♠♣♥♦◊⋄♯⋆]', ' '.join(item['text'] for item in prior)))
    marker = re.match(r'^\s*(\d{1,2}|[*∗†‡§¶♠♣♥♦◊⋄♯⋆])\s*[^\W\d_]', text)
    same_author_block = any(item['block'] == line['block'] for item in prior)
    if (AFFILIATION.search(_normal(text)) and len(text.split()) <= 22
            and (marker and marker.group(1) in (numbers | symbols) or same_author_block)
            and not re.search(r'\b(?:we|our|this|is|are|has|have|was|were|can|present|propose|show|evaluate)\b',
                              text, re.I)):
        return 'affiliation'
    if _short_affiliation(line, header, anchors, names, title, graphic_top):
        return 'affiliation'
    return None



def _fragmented_affiliation_notes(lines, page_height, header_markers):
    known = {symbol.replace('∗','*').replace('⋆','*') for symbol in header_markers}
    grouped = {}
    for line in lines:
        grouped.setdefault(line['block'], []).append(line)
    accepted = []
    for block in grouped.values():
        if (min(x['rect'][1] for x in block)<page_height*.65 or
            max(x['size'] for x in block)>11 or
            max(x['size'] for x in block)-min(x['size'] for x in block)>1 or
            max(x['rect'][3] for x in block)-min(x['rect'][1] for x in block)>page_height*.1):
            continue
        rows=[]
        for line in sorted(block,key=lambda x:(round(x['rect'][3],1),x['rect'][0])):
            row=next((r for r in rows if
                abs(r[0]['rect'][3]-line['rect'][3])<=max(1,.2*min(r[0]['size'],line['size'])) and
                min(r[0]['rect'][3],line['rect'][3])-max(r[0]['rect'][1],line['rect'][1])>=
                .8*min(r[0]['rect'][3]-r[0]['rect'][1],line['rect'][3]-line['rect'][1])),None)
            if row is None:rows.append([line])
            else:row.append(line)
        rows.sort(key=lambda r:min(x['rect'][1] for x in r))
        for row in rows:row.sort(key=lambda x:x['rect'][0])
        if max(map(len,rows))<3:
            continue
        # Never bridge a column gutter, overlapping labels, or another note.
        if any(b['rect'][0]-a['rect'][2]<-.5 or
               b['rect'][0]-a['rect'][2]>1.5*max(a['size'],b['size'])
               for row in rows for a,b in zip(row,row[1:])):
            continue
        text_rows=[' '.join(x['text'] for x in row) for row in rows]
        first=text_rows[0];marker=re.match(r'^([*∗†‡§¶♠♣♥♦◊⋄♯⋆])\s*',first)
        if not marker or marker.group(1).replace('∗','*').replace('⋆','*') not in known:
            continue
        if not AFFILIATION.match(_normal(first[marker.end():])):
            continue
        if any(re.match(r'^[*∗†‡§¶♠♣♥♦◊⋄♯⋆]',x['text']) for row in rows[1:] for x in row):
            continue
        text=' '.join(text_rows)
        if (not EMAIL.search(text) or not _contact_only(text_rows[-1]) or
            not re.search(r'\be-?mail\s*:',text,re.I) or
            re.search(r'\b(?:literal|example|input|output|figure|table|benchmark|training|model|'
                      r'dataset|theorem|experiment|we|our)\b',text,re.I)):
            continue
        accepted.extend(x for row in rows for x in row)
    return accepted



def _location_contact_fragment(line, header, known_affiliations=()):
    """A short location needs an organization above and a contact below."""
    value = re.sub(r'[´ˋˊˇˆ˜¨˚˝˘˙]', '', line['text'])
    words = re.findall(r'[^\W\d_]+', value)
    postal = ',' in value and bool(re.search(r'\d', value))
    if (not 1 <= len(words) <= (6 if postal else 4) or not all(word[0].isupper() for word in words) or
            re.search(r'\b(?:ground|truth|figure|table|model|method|input|output|code|data)\b', value, re.I)):
        return False
    def overlaps(item):
        return min(line['rect'][2], item['rect'][2]) > max(line['rect'][0], item['rect'][0])
    above = any((AFFILIATION.search(item['text']) or item['text'] in known_affiliations) and overlaps(item) and
                0 < line['rect'][1] - item['rect'][1] < 35 for item in header)
    below = any(EMAIL.search(item['text']) and _contact_only(item['text']) and overlaps(item) and
                0 < item['rect'][1] - line['rect'][1] < 65 for item in header)
    return above and below


def _affiliation_contact_blocks(lines, bounds, names=()):
    """Source-backed author/contact paragraphs and numbered affiliation notes."""
    result = []
    for block in _text_blocks(lines, bounds):
        first = block[0]
        text = re.sub(r'(?<=\w)[-\u00ad]\s+(?=\w)', '', ' '.join(item['text'] for item in block))
        author_lead = any(_normal(text).startswith(name + ' ') for name in names)
        author_relation = re.compile(r'\b(?:is|are)\s+(?:with|affiliated\s+with|an?\s+independent researcher)\b', re.I)
        if (first['rect'][1] > bounds.height * .65 and max(item['size'] for item in block) <= 10 and
                max(item['rect'][2] for item in block) - min(item['rect'][0] for item in block) <= bounds.width * .52 and
                author_lead and EMAIL.search(text) and AFFILIATION.search(text) and author_relation.search(text)):
            result.extend(_block_prefix(block, lambda value, initial: bool(author_relation.search(value))))
            continue
        institution_lead = bool(AFFILIATION.search(_normal(first['text'])) or
                                (_identity_footnote(first['text'], []) and
                                 re.search(r'(?:^|\s)\d{1,2}\s*[A-Z]', text)))
        if (first['rect'][1] < bounds.height * .65 or
                max(item['rect'][3] for item in block) - first['rect'][1] > bounds.height * .18 or
                max(item['rect'][2] for item in block) - min(item['rect'][0] for item in block) > bounds.width * .52 or
                max(item['size'] for item in block) > 11 or
                max(item['size'] for item in block) - min(item['size'] for item in block) > 1 or
                not re.match(r'^[\d*∗†‡§¶♠♣♥♦◊⋄♯]+\s*\w', first['text']) or
                not institution_lead or not AFFILIATION.search(_normal(text)) or
                not re.search(r'\bcorrespondence\s*(?:to|:)', text, re.I) or not EMAIL.search(text) or
                re.search(r'\b(?:training labels?|target variables?|literal|example input|benchmark|theorem)\b', text, re.I)):
            continue
        # Stop at the final contact. A scientific suffix is split by add_prefix.
        last_contact = max(i for i, item in enumerate(block) if EMAIL.search(item['text']))
        result.extend((item, _note_prefix_end(item['text'])) for item in block[:last_contact + 1])
    return result


def _note_prefix_end(text):
    # A correspondence sentence can share its PDF line with scientific prose.
    emails = list(EMAIL.finditer(text))
    if emails:
        end = emails[-1].end()
        split = re.match(r'[>)\]]*\.\s+(?=[A-Z])', text[end:])
        if split:
            return end + split.group().index('.') + 1
    return len(text)


def _merged_contact_spans(text):
    # Keep legacy brace-domain formats as well as the newer localpart parser.
    spans = sorted(set(_contact_spans(text) + [m.span() for m in EMAIL.finditer(text)]))
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
        else:
            merged.append((lo, hi))
    return merged


def _contact_only(text):
    spans = _merged_contact_spans(text)
    remaining = ''.join(char for i, char in enumerate(text) if not any(a <= i < b for a, b in spans))
    remaining = ORCID.sub('', remaining)
    remaining = re.sub(r'\b(?:email|emails|e-mail|contact|correspondence|orcid)\b', '', remaining, flags=re.I)
    return not _normal(remaining)


def _contact_card_intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _header_contact_slices(header, anchors, title_end, boundary, known_affiliations,
                          graphic_top, graphics=None, already_selected=()):
    """Return source slices only; graphic conflicts remain unresolved."""
    if not anchors or boundary is None:
        return []
    science = re.compile(r'\b(?:figure|fig|table|caption|teaser|example|literal|prompt|input|output|'
                         r'benchmark|baseline|models?|training|accuracy|score|results?|algorithm|'
                         r'dataset|corpus|ground truth)\b', re.I)
    selected = []
    for line in header:
        if (line['text'] in already_selected or not _contact_only(line['text']) or
                not title_end <= line['rect'][1] < line['rect'][3] <= boundary):
            continue
        spans = _merged_contact_spans(line['text'])
        domains = {part.casefold() for start, end in spans
                   for domain in re.findall(r'@([\w.-]+)', line['text'][start:end])
                   for part in domain.split('.')[:-1]}
        for institution in header:
            label = re.fullmatch(r'([A-Z][A-Za-z0-9-]+)(?:\s+[A-Z][A-Za-z-]+){0,4}\s+'
                                 r'(?:Labs?|Laborator(?:y|ies)|Research)', institution['text'])
            company = bool(label and label.group(1).casefold() in domains and
                           institution['block'] == line['block'])
            if institution['text'] not in known_affiliations and not company:
                continue
            if (not 0 <= line['rect'][1] - institution['rect'][3] <= 3 * line['size'] or
                    abs(line['size'] - institution['size']) > 2 or
                    min(line['rect'][2], institution['rect'][2]) <= max(line['rect'][0], institution['rect'][0])):
                continue
            bylines = [header[i] for i in anchors if
                       0 <= institution['rect'][1] - header[i]['rect'][3] <= 60 and
                       abs(header[i]['size'] - line['size']) <= 3 and
                       min(header[i]['rect'][2], institution['rect'][2]) >
                       max(header[i]['rect'][0], institution['rect'][0])]
            if not bylines:
                continue
            start = max(item['rect'][3] for item in bylines)
            if any(start <= other['rect'][1] <= line['rect'][1] and
                   other['text'] not in known_affiliations and other is not institution and
                   other not in bylines and not _contact_only(other['text']) and science.search(other['text'])
                   for other in header):
                continue
            card = [min(institution['rect'][0], line['rect'][0]), institution['rect'][1],
                    max(institution['rect'][2], line['rect'][2]), line['rect'][3]]
            if line['rect'][3] > graphic_top:
                if graphics is None or any(_contact_card_intersects(card, item['rect']) for item in graphics):
                    continue
            selected.extend((line, start, end) for start, end in spans)
            break
    return selected


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
    return not re.sub(r'\b(?:and|et|al|[0-9]+|[αβγδεζηθλμνξπρστυφχψω])\b', '', value).strip()


def _byline_with_affiliation(text, names):
    head, separator, tail = text.partition(',')
    return bool(separator and _byline_only(head, names) and AFFILIATION.search(tail) and
                not re.search(r'\b(?:benchmark|method|measures?|evaluated|accuracy|training|theorem)\b', tail, re.I))


def _publication_header_partner(line, other):
    return (line['block'] == other['block'] and
            abs(line['rect'][1] - other['rect'][1]) < 1 and
            bool(VENUE.search(other['text']) or
                 re.match(r'^Conference acronym\b.+\b(?:19|20)\d{2}\b', other['text'], re.I)))



SHORT_STATUS = re.compile(r'^(?:under review[.]?|conference paper under review[.]?|preprint[.]?)$', re.I)


def _source_status_headers(pages, sizes, title):
    """Confirm short publication labels from source recurrence and layout."""
    candidates = []
    for number, lines in enumerate(pages, 1):
        width, height = sizes[number-1]
        folios = [line for line in lines if line['text'].strip() == str(number)]
        for index, line in enumerate(lines):
            if (not SHORT_STATUS.fullmatch(line['text']) or
                    _normal(line['text']) == _normal(title) or line['size'] > 11):
                continue
            top = line['rect'][1] < height * .1
            bottom = line['rect'][3] > height * .9
            if not (top or bottom):
                continue
            folio = any(other['block'] == line['block'] and
                        abs(other['rect'][1] - line['rect'][1]) < 1 for other in folios)
            neighbors = [other for other in lines if other is not line and other not in folios
                         and abs(other['rect'][1] - line['rect'][1]) < 1]
            below = [other for other in lines if other['rect'][1] >= line['rect'][3]]
            isolated_top = top and not neighbors and (not below or min(x['rect'][1] for x in below)-line['rect'][3] >= 5)
            if _normal(line['text']) == 'preprint' and not folio:
                continue
            if not (folio or isolated_top):
                continue
            candidates.append((number,index,_normal(line['text']),line['rect'][0]/width,line['rect'][1]/height))
    return {(number,index) for number,index,text,x,y in candidates
            if any(n != number and t == text and abs(a-x) < .03 and abs(b-y) < .004
                   for n,_,t,a,b in candidates)}



def _source_author_headers(pages, sizes, names):
    """Bootstrap isolated running headers from recurrence in the original PDF."""
    candidates = []
    for number, lines in enumerate(pages, 1):
        width, height = sizes[number - 1]
        folios = [item for item in lines if item['text'].strip() == str(number)]
        for index, line in enumerate(lines):
            if line['rect'][1] >= height * .12 or line['rect'][3] - line['rect'][1] > 25:
                continue
            folio = any(item['block'] == line['block'] and
                        min(item['rect'][3], line['rect'][3]) - max(item['rect'][1], line['rect'][1]) >=
                        .8 * min(item['rect'][3] - item['rect'][1],
                                 line['rect'][3] - line['rect'][1]) for item in folios)
            if (line['rect'][1] >= height * .06 and not folio and
                    not any(_publication_header_partner(line, other) for other in lines)):
                continue
            value = _normal(line['text'])
            short = any(value == name.split()[0][0] + ' ' + name.split()[-1] + ' et al'
                        for name in names if len(name.split()) >= 2)
            if not (_byline_only(line['text'], names) or short):
                continue
            below = [item for item in lines if item['rect'][1] >= line['rect'][3] and
                     not re.fullmatch(r'\d{1,3}', item['text'].strip())]
            if (not below or below[0]['rect'][1] - line['rect'][3] < 5 or
                    re.match(r'^(?:19|20)\d{2}[a-z]?(?:\W|$)', below[0]['text'])):
                continue
            key = re.sub(r'^\d+\s+|\s+\d+$', '', value)
            candidates.append((number, index, key, line['rect'][0] / width, line['rect'][1] / height))
    return {(number, index) for number, index, key, x, y in candidates
            if any(other != number and other_key == key and abs(other_x - x) < .03 and abs(other_y - y) < .004
                   for other, _, other_key, other_x, other_y in candidates)}


def _name_shape(text):
    """A weak shape check, used only beside a positively anchored author row."""
    if (EMAIL.search(text) or URL.search(text) or AFFILIATION.search(text) or
            re.search(r'[:!?=]', text) or
            re.search(r'\b(?:inspired|scientific|method|figure|table|group|results|'
                      r'project|abstract|theorem|foundation|funding|memory|praise|tribute)\b', text, re.I)):
        return False
    text = _without_spacing_accents(text).replace('’', "'").replace('‘', "'")
    text = re.sub(r"(?<=[A-Za-zÀ-ž])[αβγδεζηθλμνξπρστυφχψω](?=\d|\W|$)", "", text)
    words = re.findall(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", text)
    return (len(words) >= 2 and len(words) <= 28 and
            all(word[0].isupper() or word.lower() in {'and', 'de', 'da', 'van', 'von', 'del', 'al', 'et'}
                for word in words) and sum(word[0].isupper() for word in words) >= 2)


_HEADER_COUNTRY = re.compile(r"\b(?:United States(?: of America)?|United Kingdom|Republic of Korea|"
                     r"USA|UK|Canada|France|Germany|Israel|India|China|Japan|Switzerland|"
                     r"Netherlands|Australia|Austria|Belgium|Italy|Spain|Portugal|Ireland|"
                     r"Singapore|Luxembourg|Finland|Sweden|Norway|Denmark|Poland|Brazil|"
                     r"Taiwan|South Korea|New Zealand)\s*[.]?$", re.I)
_HEADER_SCIENCE = re.compile(r"\b(?:figure|fig|table|caption|teaser|example|literal|prompt|input|output|"
                     r"benchmark|baseline|training|models?|methods?|accuracy|score|results?|"
                     r"algorithm|datasets?|ground truth|probability|classification|segmentation|"
                     r"we|our|this|show|evaluate|propose)\b", re.I)
_HEADER_INSTITUTION = re.compile(r"\b(?:univ|universite|universidade|universitat|institut|instituto|"
                         r"ecole|polytechnique|dept|department|university|institute|laboratory|"
                         r"research|labs?|centre|center)\b", re.I)
_HEADER_MARKERS = "*∗†‡§¶♠♣♥♦◊⋄♯⋆"

def _header_guard(line, header, anchors, title_end, boundary, graphic_top):
    if not anchors or boundary is None:
        return False
    if not title_end <= line["rect"][1] < line["rect"][3] <= min(boundary, graphic_top):
        return False
    prior = [header[i] for i in anchors if header[i]["rect"][3] <= line["rect"][3]]
    if not prior:
        return False
    start = max(item["rect"][1] for item in prior)
    return not any(start <= item["rect"][1] <= line["rect"][1]
                   and _HEADER_SCIENCE.search(_normal(item["text"])) for item in header)

def _marked_header_institution(line, header, anchors, title_end, boundary, graphic_top,
                              known_affiliations=()):
    """A source-author marker plus explicit institution wording; no bare label."""
    if not _header_guard(line, header, anchors, title_end, boundary, graphic_top):
        return False
    marker = re.match(r"^\s*(\d{1,2}|[" + _HEADER_MARKERS + r"])[*†‡]?\s*(.+)$", line["text"])
    if not marker:
        return False
    declared = {part for i in anchors
                for part in re.findall(r"\d{1,2}|[" + _HEADER_MARKERS + "]", header[i]["text"])}
    if marker.group(1) not in declared:
        return False
    if (marker.group(1).isdigit() and
            (line.get('styled_heading') or line['size'] >= max(header[i]['size'] for i in anchors) + .5)):
        return False
    value = _normal(marker.group(2))
    if len(value.split()) > 24 or _HEADER_SCIENCE.search(value):
        return False
    before = [header[i] for i in anchors if header[i]["rect"][3] <= line["rect"][3]]
    if not any(abs(line["size"] - item["size"]) <= 4 and
               0 <= line["rect"][1] - item["rect"][3] <= 75 for item in before):
        return False
    if _HEADER_INSTITUTION.search(value):
        return True
    # Acronym+country only when physically paired with an already proved
    # institution on the same row; an isolated model name is not sufficient.
    country = _HEADER_COUNTRY.search(marker.group(2))
    prefix = marker.group(2)[:country.start()].strip(" ,") if country else ""
    paired = any(item["text"] in known_affiliations and
                 abs(item["rect"][3] - line["rect"][3]) <= 1.5 and
                 abs(item["size"] - line["size"]) <= 1 for item in header)
    return bool(country and re.fullmatch(r"[A-Z][A-Z0-9]{1,10}", prefix) and paired)

def _affiliation_location(line, header, anchors, title_end, boundary, graphic_top,
                         known_affiliations=()):
    """Country/postal continuation after an exact, already proved institution."""
    if not _header_guard(line, header, anchors, title_end, boundary, graphic_top):
        return False
    value = _without_spacing_accents(line["text"]).strip()
    words = re.findall(r"[^\W\d_]+", value)
    if (not _HEADER_COUNTRY.search(value) or not 1 <= len(words) <= 8 or len(value) > 85 or
            not all(w[0].isupper() or w in {"of"} for w in words) or
            re.search(r"[:;!?=@]", value) or _HEADER_SCIENCE.search(value)):
        return False
    for above in header:
        if above["text"] not in known_affiliations:
            continue
        gap = line["rect"][1] - above["rect"][3]
        if not (0 <= gap <= 2.5 * max(line["size"], above["size"]) and
                abs(line["size"] - above["size"]) <= 2 and
                above["rect"][0] <= (line["rect"][0] + line["rect"][2])/2 <= above["rect"][2]):
            continue
        blockers = [other for other in header if other is not line and other is not above
                    and above["rect"][3] <= other["rect"][1] < line["rect"][1]
                    and min(line["rect"][2], other["rect"][2]) >
                        max(line["rect"][0], other["rect"][0])]
        if not blockers:
            return True
    return False


_CONTACT_MARKERS = "*∗†‡§¶♠♣♥♦◊⋄♯⋆∥‖✍✉"
_CONTACT_BRACED = r"\{[\w.+,\s-]+\}"
_CONTACT_LOCAL = r"(?:" + _CONTACT_BRACED + r"(?:[.]" + _CONTACT_BRACED + r")*|[\w.+-]+-\n[\w.+-]+|[\w.+-]+)"
_CONTACT_DOMAIN = r"(?:[\w-]+(?:-\n[\w-]+)?[.])+[A-Za-z]{2,63}"
_CONTACT_EMAIL = re.compile(r"(?<![\w.+-])" + _CONTACT_LOCAL + r"@" + _CONTACT_DOMAIN)
_CONTACT_ANGLE_EMAIL = re.compile(r"<(?P<address>[\w.+-]+(?:[ \n][\w.+-]+){1,3}@" + _CONTACT_DOMAIN + r")>")
_CONTACT_EXAMPLE = re.compile(r"\b(?:examples?|literal|quoted|quotation|synthetic|generated|prompt|"
                     r"dataset|corpus|archive|benchmark|training|input|output|transcript|third[- ]party|"
                     r"cited|citation|et\s+al|sampled|dummy|placeholder|string)\b", re.I)
_CONTACT_LABEL = re.compile(r"\b(?:correspondence\s+to|corresponding\s+(?:authors?|e-?mails?)|"
                   r"corr[.]?\s*author|contact(?:\s+to)?)\s*[:.]?", re.I)

def _contact_joined(block):
    text="";ranges=[];previous=None
    for line in block:
        if previous is not None:
            same_row=abs(line["rect"][3]-previous["rect"][3])<=3
            text += " " if same_row else "\n"
        start=len(text);text+=line["text"];ranges.append((start,len(text),line))
        previous=line
    return text,ranges

def _contact_spans(raw):
    matches=[(m.start(),m.end()) for m in _CONTACT_EMAIL.finditer(raw)]
    for m in _CONTACT_ANGLE_EMAIL.finditer(raw):
        lo,hi=m.span("address")
        matches=[(a,b) for a,b in matches if not (lo<=a and b<=hi)]
        matches.append((lo,hi))
    return sorted(set(matches))

def _owned_contact_spans(raw, names, header_markers):
    """Ownership belongs to one contact clause and note, never the whole block."""
    contacts=_contact_spans(raw)
    # Mask punctuation inside an address while locating sentence boundaries.
    masked=list(raw)
    for lo,hi in contacts:
        masked[lo:hi]=["X" if c!="\n" else c for c in raw[lo:hi]]
    masked="".join(masked)
    notes=[0]+[m.start("marker") for m in re.finditer(
        r"(?:^|\n|(?<=[.!?;])\s+)\s*(?P<marker>\d{1,2}|["+_CONTACT_MARKERS+r"])(?=\s|[^\W\d_]|[<{])",raw)]
    notes=sorted(set(notes+[len(raw)]))
    normalize_marker=lambda value:value.replace("∗","*").replace("⋆","*")
    declared={normalize_marker(value) for value in header_markers}
    local_owners={re.sub(r"[^a-z]","",name) for name in names if len(name.split())>=2}
    accepted=[]
    for lo,hi in contacts:
        begin=max(n for n in notes if n<=lo);end=min(n for n in notes if n>lo)
        if hi>end:
            continue
        marker=re.match(r"\s*(\d{1,2}|["+_CONTACT_MARKERS+r"])\s*",raw[begin:end])
        marked=bool(marker and normalize_marker(marker.group(1)) in declared)
        sentences=[(begin+a,begin+b) for a,b in _sentence_spans(masked[begin:end])]
        left,right=next(((a,b) for a,b in sentences if a<=lo<b),(begin,end))
        # A semicolon may separate a contact list or a different scientific claim.
        left=max(left,masked.rfind(";",left,lo)+1)
        semicolon=masked.find(";",hi,right)
        if semicolon>=0:right=semicolon
        clause=_flat(raw[left:right]);prefix=_flat(raw[left:lo])
        note_prefix=_flat(raw[begin:lo])
        if (_CONTACT_EXAMPLE.search(_flat(masked[left:right])) or any(c in clause for c in ('"','“','”',chr(96))) or
                re.search(r"(?:^|\s)['‘].*@.*['’]",clause) or
                re.match(r"\s*\[\d+\]",clause)):
            continue
        # Earlier corpus/example prose is not author evidence for a later
        # generic Email label, even when another address in this note was owned.
        if _CONTACT_EXAMPLE.search(_flat(masked[begin:lo])):
            continue
        value=_normal(note_prefix)
        full_name=any(re.search(r"(?<!\w)"+re.escape(name)+r"(?!\w)",value) for name in names)
        initial_name=False
        for name in names:
            words=name.split()
            if len(words)<2:continue
            initials=re.escape(words[0][0])+"".join(r"(?:\s+"+re.escape(w[0])+r")?" for w in words[1:-1])
            initial_name |= bool(re.search(r"(?<!\w)"+initials+r"\s+"+re.escape(words[-1])+r"(?!\w)",value))
        email_owner=any(re.sub(r"[^a-z]","",local.lower()) in local_owners
                        for local in re.split(r"[,{}]",raw[lo:hi].split("@")[0])
                        if len(re.sub(r"[^a-z]","",local.lower()))>=7)
        own_relation=bool((full_name or initial_name) and
                          re.search(r"\b(?:is|are)\s+(?:also\s+)?with\b",note_prefix,re.I) and
                          (AFFILIATION.search(note_prefix) or re.search(r"\b(?:lab|research)\b",note_prefix,re.I)))
        directive=bool(_CONTACT_LABEL.search(prefix) or re.search(r"\be-?mails?\s*:",prefix,re.I) or
                       re.search(r"\b(?:feel free to|please)\s+(?:e-?mail|contact)\b",prefix,re.I))
        clause_head=re.sub(r"^\s*(?:\d{1,2}|["+_CONTACT_MARKERS+r"])\s*","",prefix)
        clean_prefix=re.sub(r"\b(?:e-?mails?|contact)\b", "",clause_head,flags=re.I)
        bare=not _normal(clean_prefix)
        organization=bool(len(clean_prefix)<450 and
            (AFFILIATION.search(clean_prefix) or re.search(r"\b(?:lab|research|institut|ecole)\b",clean_prefix,re.I) or
             re.fullmatch(r"[A-Z][\w.-]+\.[a-z]{2,}\s*,?\s*",clean_prefix)) and
            not re.search(r"\b(?:contains?|uses?|from|access|requests?|messages?|go|is|are)\b",clean_prefix,re.I))
        personal=bool(re.search(r"\bwork\s+(?:was\s+)?(?:done|performed)\b.{0,120}"
                                r"\b(?:intern|internship|visiting|employment)\b",clean_prefix,re.I))
        previous=next(((a,b) for a,b,_ in reversed(accepted) if begin<=a and b<=lo),None)
        listing=False
        if previous:
            gap=raw[previous[1]:lo]
            remainder=_normal(re.sub(r"\band\b","",gap,flags=re.I))
            listing=bool("." not in gap and not re.search(r"[!?]",gap) and
                         (not remainder or _byline_only(gap,names)))
        if directive:
            clause_name=any(re.search(r"(?<!\w)"+re.escape(name)+r"(?!\w)",_normal(prefix))
                            for name in names)
            role_directive=bool(re.search(r"\b(?:correspondence\s+to|corresponding\s+(?:authors?|e-?mails?)|"
                                          r"corr[.]?\s*author)\b",prefix,re.I))
            affiliation_email=bool(own_relation and re.fullmatch(r"e-?mails?\s*:",
                                         clause_head.strip(" ()[]<>"),re.I))
            pending_author_list=bool(marked and previous is None and
                re.search(r"\bcorresponding\s+author\b",note_prefix,re.I) and
                (re.search(r"\bfeel free to\s+e-?mail\b",prefix,re.I) or
                 re.fullmatch(r"e-?mails?\s*:",clause_head.strip(" ()[]<>"),re.I)))
            if not (clause_name or email_owner or affiliation_email or pending_author_list or listing or
                    marked and (left==begin or role_directive or personal and previous is None)):
                continue
            proof="own_contact_clause"
        elif own_relation and (bare or organization or
             re.search(r"\b(?:is|are)\s+(?:also\s+)?with\b",prefix,re.I)):
            proof="metadata_affiliation_relation"
        elif marked and (bare or organization or personal):proof="declared_author_note"
        elif listing:proof="owned_contact_list_continuation"
        else:continue
        accepted.append((lo,hi,proof))
    return accepted

def _own_contact_slices(lines, width, height, names, header_markers):
    """Only exact email source slices in supported first-page contact footnotes."""
    selected=[]
    grouped={}
    for line in lines:
        grouped.setdefault(line["block"],[]).append(line)
    for block_id,group in grouped.items():
        block=_block_reading_lines(group,block_id)
        last=block[-1]
        # Only a uniquely adjacent continuation of an already open angle
        # address may cross a source block boundary. Never join general prose.
        if re.search(r"<[\w.+-]+-\s*$",last["text"]):
            following=[line for line in lines if line["block"]!=block_id
                       and 0<=line["rect"][1]-last["rect"][3]<=5
                       and abs(line["rect"][0]-last["rect"][0])<=3
                       and abs(line["size"]-last["size"])<=1
                       and re.fullmatch(r"[\w.+-]+@"+_CONTACT_DOMAIN+r">[.]?",line["text"].strip())]
            if len(following)==1:
                block=block+[following[0]]
        top=min(x["rect"][1] for x in block);bottom=max(x["rect"][3] for x in block)
        if (top < height*.60 or bottom-top > height*.25 or
                max(x["size"] for x in block)>11 or
                max(x["rect"][2] for x in block)-min(x["rect"][0] for x in block)>width*.9):
            continue
        if any(b["rect"][1]-a["rect"][3] > max(14,2*a["size"])
               for a,b in zip(block,block[1:])):
            continue
        raw,ranges=_contact_joined(block)
        flat=_flat(raw)
        if re.search(r"(?:^|\n)\s*\[\d+\]",raw) or _reference_heading(flat):
            continue
        for lo,hi,proof in _owned_contact_spans(raw,names,header_markers):
            for start,end,line in ranges:
                left,right=max(start,lo),min(end,hi)
                if left<right:
                    selected.append((line,left-start,right-start,proof))
    # Multiple proof paths must not duplicate source slices.
    seen=set();result=[]
    for line,start,end,proof in selected:
        key=(id(line),start,end)
        if key not in seen:
            seen.add(key);result.append((line,start,end,proof))
    return result



SOURCE_LAYOUT_FAMILIES = (
    ("acl_emnlp", ("acl", "emnlp"), ("centeredrows", "topcards", "compactbyline_footer", "preprint", "singlecolumnjournal")),
    ("cvpr_iccv", ("cvpr", "iccv"), ("topcards", "centeredrows", "compactbyline_footer", "preprint", "singlecolumnjournal")),
    ("icml", ("icml",), ("compactbyline_footer", "centeredrows", "topcards", "preprint", "singlecolumnjournal")),
    ("iclr", ("iclr",), ("centeredrows", "topcards", "preprint", "compactbyline_footer", "singlecolumnjournal")),
    ("neurips", ("neurips",), ("topcards", "centeredrows", "compactbyline_footer", "preprint", "singlecolumnjournal")),
    ("tmlr", ("tmlr",), ("singlecolumnjournal", "centeredrows", "topcards", "preprint", "compactbyline_footer")),
)

_LAYOUT_AFFILIATION = re.compile(r"\b(?:universit\w*|institute|department|dept|dpt|college|school|laboratory|lab|centre|center)\b", re.I)

_LAYOUT_SCIENCE = re.compile(r"\b(?:figure|table|algorithm|theorem|benchmark|dataset|model|method|results|example|prompt)\b", re.I)

def _without_spacing_accents(text):
    marks = '´ˋˊˇˆ˜¨˚˝˘˙¸˛' + chr(96)
    return re.sub(r'(?<=[^\W\d_])(\s*)[' + marks + r']\s*(?=[^\W\d_])',
                  lambda m: ' ' if m.group(1) and text[m.end()].isupper() else '', text)

def _layout_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])

def _layout_nonidentity_label(lines, item):
    # A labelled study/model/site list is not an author affiliation card merely
    # because its entries mention institutions and reuse byline-like numbers.
    peers = [x for x in lines if x["block"] == item["block"] and x["rect"][1] <= item["rect"][1]]
    return any(x is not item and x["text"].strip().endswith(":") and
               not re.fullmatch(r"(?:Authors|Author affiliations)\s*:", x["text"].strip(), re.I) and
               any(0 <= peer["rect"][1] - x["rect"][3] < 36 and
                   min(peer["rect"][2], x["rect"][2]) > max(peer["rect"][0], x["rect"][0])
                   for peer in peers) for x in lines)

def _layout_body_observation(pages, sizes, boundary, page=0):
    width, height = sizes[page]
    lines = [x for x in pages[page] if len(x["text"]) >= 30 and 8 <= x["size"] <= 14 and
             x["rect"][1] >= (height * .1 if page else boundary) and x["rect"][3] < height * .9]
    wide = sum(x["rect"][2] - x["rect"][0] > width * .55 for x in lines)
    return {"page": page + 1, "text_lines": len(lines), "wide_lines": wide,
            "single_column_observation": len(lines) >= 10 and wide >= len(lines) * .75,
            "official_template_proof": False}

def _select_layout_profile(paper, pages, page_sizes, *, first_graphics=None, first_page_evidence=None):
    from math import isfinite
    """Use planner-confirmed indices, then recheck complete metadata name seeds.

    first_page_evidence contains title_indexes/author_indexes into pages[0] and
    body_boundary. Missing anchors are not inferred here. Graphics may be rects
    or {rect: ...} records. All output is caller-owned diagnostic data.
    """
    ids = paper.get("source_ids") or []
    ids = [ids] if isinstance(ids, str) else list(ids)
    ids += [e.get("source_id") for e in paper.get("source_entries") or [] if isinstance(e, dict)]
    ids = sorted({x.lower() for x in ids if isinstance(x, str) and x})
    matches = {s: [name for name, prefixes, _ in SOURCE_LAYOUT_FAMILIES if any(
        re.match(r"^" + prefix + r"(?:\d|_|$)", s) for prefix in prefixes)] for s in ids}
    families = sorted({f for fs in matches.values() for f in fs})
    provenance = families[0] if len(families) == 1 and all(matches.values()) else None
    out = dict(source_ids=ids, source_families=families, provenance_family=provenance,
               selected_family=None, observed_variant="unresolved", evidence={}, ambiguities=[])
    if not ids or not families:
        out["ambiguities"].append("missing_or_unknown_source_provenance")
    if any(not fs for fs in matches.values()):
        out["ambiguities"].append("unmapped_source_id")
    if len(families) > 1:
        out["ambiguities"].append("multiple_metadata_source_families")
    ev = first_page_evidence or {}
    if not pages or not page_sizes or not pages[0] or not ev:
        out["ambiguities"].append("missing_confirmed_first_page_structure")
        return out
    inspected = min(2, len(pages))
    numbers = [v for size in page_sizes[:inspected] for v in size]
    numbers += [v for page in pages[:inspected] for x in page for v in [*x["rect"], x["size"]]]
    if (len(page_sizes) < inspected or any(type(v) not in (int, float) or not isfinite(v) for v in numbers)):
        out["ambiguities"].append("invalid_or_missing_page_geometry")
        return out
    lines, (width, height) = pages[0], page_sizes[0]
    title, supplied = set(ev.get("title_indexes") or []), set(ev.get("author_indexes") or [])
    boundary = ev.get("body_boundary")
    if (width <= 0 or height <= 0 or not title or not supplied or title & supplied or
            any(type(i) is not int or not 0 <= i < len(lines) for i in title | supplied) or
            type(boundary) not in (int, float) or not isfinite(boundary) or not 0 < boundary <= height):
        out["ambiguities"].append("invalid_or_conflicting_first_page_structure")
        return out
    captions = {x["block"] for x in lines if re.match(r"^(?:Figure|Fig[.]?|Table|Algorithm|Listing)\s*(?:\d+|[IVXLC]+)\b", x["text"], re.I)}
    if any(lines[i]["block"] in captions or re.match(r"^arxiv\s*:", lines[i]["text"], re.I) for i in title):
        out["ambiguities"].append("supplied_primary_title_conflicts_with_source_context")
        return out
    title_end = max(lines[i]["rect"][3] for i in title)
    names, authors = _author_names(paper), []
    for i in sorted(supplied):
        line, value = lines[i], _normal(lines[i]["text"])
        whole = any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", value) for name in names)
        words = re.findall(r"[^\W\d_]+", _without_spacing_accents(line["text"]))
        shaped = words and all(w[0].isupper() or w.lower() in {"and", "de", "da", "van", "von", "del", "al", "et"} for w in words)
        if (whole and shaped and line["block"] not in captions and not _LAYOUT_SCIENCE.search(line["text"]) and
                "@" not in line["text"] and not _LAYOUT_AFFILIATION.search(value) and
                line["rect"][1] >= title_end - 1 and line["rect"][3] < boundary):
            authors.append(i)
    out["evidence"].update(title_indexes=sorted(title), verified_author_indexes=authors,
                           rejected_author_indexes=sorted(supplied - set(authors)), body_boundary=boundary)
    if not authors:
        out["ambiguities"].append("no_whole_metadata_name_on_actual_byline")
        return out
    if first_graphics is None:
        out["ambiguities"].append("unknown_first_page_graphic_inventory")
        return out
    graphics = [g.get("rect") if isinstance(g, dict) else g for g in first_graphics]
    if any(not isinstance(g, (list, tuple)) or len(g) != 4 or
           any(type(v) not in (int, float) or not isfinite(v) for v in g) for g in graphics):
        out["ambiguities"].append("invalid_graphic_inventory")
        return out
    if any(_layout_overlap(lines[i]["rect"], g) for i in authors + sorted(title) for g in graphics):
        out["ambiguities"].append("graphic_overlap_with_primary_title_or_byline")
        return out
    support = [i for i, x in enumerate(lines) if i not in title and i not in supplied and x["block"] not in captions and
               title_end <= x["rect"][1] < boundary and not _LAYOUT_SCIENCE.search(x["text"]) and
               not _layout_nonidentity_label(lines, x) and
               ("@" in x["text"] or _LAYOUT_AFFILIATION.search(_normal(x["text"]))) and
               not any(_layout_overlap(x["rect"], g) for g in graphics)]
    cards = []
    for i in authors:
        a = lines[i]
        mates = [j for j in support if lines[j]["rect"][2] - lines[j]["rect"][0] < width * .46 and
                 0 <= lines[j]["rect"][1] - a["rect"][3] <= max(36, a["size"] * 4) and
                 min(a["rect"][2], lines[j]["rect"][2]) > max(a["rect"][0], lines[j]["rect"][0])]
        if mates and a["rect"][2] - a["rect"][0] < width * .65:
            cards.append(i)
    topcards = any(abs(sum(lines[a]["rect"][::2])/2 - sum(lines[b]["rect"][::2])/2) > width * .22
                   for a in cards for b in cards if a != b)
    markers = {v for i in authors for v in re.findall(r"(?<!\d)([1-9]\d?)(?!\d)", lines[i]["text"])}
    footer = [i for i, x in enumerate(lines) if x["rect"][1] > height * .65 and x["size"] <= 11 and
              not _layout_nonidentity_label(lines, x) and
              not _LAYOUT_SCIENCE.search(x["text"]) and re.match(r"^\s*[1-9]\d?\s*[A-Z]", x["text"]) and
              set(re.findall(r"(?<!\d)([1-9]\d?)(?!\d)", x["text"])) & markers and
              not any(_layout_overlap(x["rect"], g) for g in graphics)]
    footer_contacts = [i for i, x in enumerate(lines) if x["rect"][1] > height * .65 and
                       x["size"] <= 11 and "@" in x["text"] and not _LAYOUT_SCIENCE.search(x["text"])]
    compact = bool(footer and not support and
                   (footer_contacts or any(_LAYOUT_AFFILIATION.search(_normal(lines[i]["text"])) for i in footer)))
    center = (min(lines[i]["rect"][0] for i in title) + max(lines[i]["rect"][2] for i in title)) / 2
    author_center = (min(lines[i]["rect"][0] for i in authors) + max(lines[i]["rect"][2] for i in authors)) / 2
    centered = abs(center - author_center) < width * .12
    observation = _layout_body_observation(pages, page_sizes, boundary)
    margin = [i for i, x in enumerate(lines) if x["rect"][3] <= min(lines[j]["rect"][1] for j in title)]
    journal = [i for i in margin if re.match(r"^(?:Published in )?Transactions on Machine Learning Research\b", lines[i]["text"], re.I)]
    preprint = [i for i in margin if re.fullmatch(r"Pre[- ]?print[.]?", lines[i]["text"], re.I)]
    out["evidence"].update(header_support_indexes=support, card_author_indexes=cards,
                           footer_marker_indexes=footer, footer_contact_indexes=footer_contacts,
                           body_geometry=observation, journal_notice_indexes=journal, preprint_notice_indexes=preprint)
    if len(pages) > 1:
        out["evidence"]["later_page_geometry"] = _layout_body_observation(pages, page_sizes, boundary, 1)
    if journal and preprint:
        out["ambiguities"].append("conflicting_publication_layout_markers")
        return out
    variant = ("topcards" if topcards else "compactbyline_footer" if compact else
               "singlecolumnjournal" if journal and centered and observation["single_column_observation"] else
               "preprint" if preprint and centered else
               "centeredrows" if centered and support else "unresolved")
    out["observed_variant"] = variant
    if variant == "unresolved":
        out["ambiguities"].append("insufficient_observed_layout_evidence")
    elif provenance:
        supported = next(variants for family, _, variants in SOURCE_LAYOUT_FAMILIES if family == provenance)
        out["evidence"]["source_recipe_priority"] = list(supported)
        if variant not in supported:
            out["ambiguities"].append("observed_variant_has_no_recipe_in_source_family")
        elif not out["ambiguities"]:
            out["selected_family"] = provenance
    return out

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


def _header_author_names(header, names):
    """Use actual name-shaped header spellings with metadata name endpoints."""
    aliases = set(names)
    for line in header:
        for part in re.split(r',|;|\band\b', line['text'], flags=re.I):
            if not _name_shape(part):
                continue
            words = [word for word in _normal(part).split()
                     if not word.isdigit() and word not in set('αβγδεζηθλμνξπρστυφχψω')]
            if not 2 <= len(words) <= 6:
                continue
            for name in names:
                known = name.split()
                if (words[0] == known[0] and words[-1] == known[-1] or
                        len(words) == 2 and len(known) > 2 and words == known[-2:]):
                    aliases.add(' '.join(words))
                    break
    return names + sorted(aliases.difference(names))


def _unlabelled_body_boundary(lines, paper, names, height):
    """Infer only a prose/figure boundary following an exact title and byline."""
    titles = _matched_lines(lines, [_normal(paper.get('title') or '')])
    if not titles:
        return None
    title_end = max(lines[i]['rect'][3] for i in titles)
    aliases = _header_author_names(lines, names)
    bylines = [line for line in lines if title_end <= line['rect'][1] <= title_end + 180 and
               (_byline_only(line['text'], aliases) or _byline_with_affiliation(line['text'], aliases))]
    known = sum(any(name in _normal(line['text']) for line in bylines) for name in names)
    if not bylines or not (known >= 2 or known and any(AFFILIATION.search(line['text']) for line in lines)):
        return None
    start = min(line['rect'][1] for line in bylines)
    for line in lines:
        text = line['text']
        if line['rect'][1] <= start or line['rect'][1] > height * .85 or _byline_only(text, aliases):
            continue
        affiliation = (AFFILIATION.search(text) and len(text.split()) <= 18 and
                       not re.search(r'\b(?:we|our|this|is|are|has|have|was|were|can|present|propose|show|models?|datasets?|benchmark)\b', text, re.I))
        if re.match(r'^(?:Figure|Fig[.])\s*\d', text, re.I) or (
                len(text.split()) >= 8 and not affiliation and not VENUE.search(text) and not EMAIL.search(text)):
            return line['rect'][1]
    return None


def _caption_indexes(lines):
    """Caption continuations in one source block are scientific frontmatter."""
    starts = {}
    for line in lines:
        if re.match(r'^(?:Figure|Fig[.]?|Table|Algorithm|Listing)\s*(?:\d+|[IVXLC]+)\b',
                    line['text'], re.I):
            starts[line['block']] = min(starts.get(line['block'], float('inf')), line['rect'][1])
    return {i for i, line in enumerate(lines)
            if line['block'] in starts and line['rect'][1] >= starts[line['block']]}


def _title_indexes(header, title):
    captions = _caption_indexes(header)
    eligible = [i for i, line in enumerate(header)
                if i not in captions and
                not (VENUE.search(line['text']) or ARXIV.search(line['text']) or
                     EMAIL.search(line['text']) or not _normal(line['text']))]
    matched = (_matched_lines(header, [title]) & set(eligible)) if title else set()
    if matched:
        first = min(matched)
        result = {first}
        for i in sorted(matched - {first}):
            previous = max(result)
            if (i != previous + 1 or abs(header[i]['size'] - header[first]['size']) >
                    max(1, header[first]['size'] * .2) or
                    header[i]['rect'][1] - header[previous]['rect'][3] > header[first]['size']):
                break
            result.add(i)
        return result
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


def build_plan(source: Path, paper: dict, *, diagnostics=None) -> dict:
    """Return the existing PLAN_SCHEMA, retaining all unresolved-case reasons."""
    import pymupdf as fitz

    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise TypeError('Planner diagnostics must be a dictionary')
    if diagnostics is not None:
        for key in ('source_layout_profile', 'profile_id', 'profile_schema'):
            diagnostics.pop(key, None)
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
        first_graphic_top = float('inf')
        first_graphics = []
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
            images = page.get_images()
            if images:
                warn(f'Page {number}: raster images are not assessed for identifying content.')
                if number == 1:
                    try:
                        for xref in {item[0] for item in images if item[0]}:
                            for rect in page.get_image_rects(xref):
                                if rect.width > 12 and rect.height > 12:
                                    first_graphic_top = min(first_graphic_top, rect.y0)
                                    first_graphics.append({'kind': 'image', 'rect': list(rect)})
                    except RuntimeError:
                        first_graphic_top = 0
                        first_graphics = None
            drawings = page.get_cdrawings(extended=False)
            if drawings:
                warn(f'Page {number}: vector graphics are not assessed for identifying content.')
                if number == 1:
                    for drawing in drawings:
                        rect = drawing.get('rect')
                        if rect and rect[2] - rect[0] > 12 and rect[3] - rect[1] > 12:
                            first_graphic_top = min(first_graphic_top, rect[1])
                            if first_graphics is not None:
                                first_graphics.append({'kind': 'vector', 'rect': list(rect)})

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

        source_char_lines = {}

        def add_prefix(number, line, end, category, start=0):
            piece = line['text'][start:end]
            if not piece.strip():
                return
            # An already authorized full-line operation also covers this slice.
            if any(item['page'] == number and item['text'] == line['text'] and
                   fitz.Rect(item['rect']).contains(fitz.Rect(line['rect']))
                   for item in plan['redactions']):
                return
            page = doc[number - 1]
            rects = page.search_for(piece, clip=fitz.Rect(line['rect']))
            if number not in source_char_lines:
                source_char_lines[number] = [
                    (raw['bbox'], [char for span in raw['spans'] for char in span['chars']])
                    for block in page.get_text('rawdict', flags=fitz.TEXTFLAGS_RAWDICT &
                                               ~fitz.TEXT_PRESERVE_IMAGES)['blocks']
                    if block['type'] == 0 for raw in block['lines']]
            candidates = [chars for bbox, chars in source_char_lines[number]
                          if tuple(bbox) == tuple(line['rect']) and
                          ''.join(char['c'] for char in chars).strip() == line['text']]
            if not rects or len(candidates) != 1:
                warn(f'Page {number}: mixed identity/scientific line could not be safely split.')
                return
            chars = candidates[0]
            raw_text = ''.join(char['c'] for char in chars)
            leading = len(raw_text) - len(raw_text.lstrip())
            lo, hi = leading + start, leading + end
            offset, chosen = 0, []
            for char in chars:
                stop = offset + len(char['c'])
                if offset < hi and stop > lo and char['c'].strip():
                    if offset < lo or stop > hi:
                        warn(f'Page {number}: identity slice cuts a source glyph; preserved.')
                        return
                    box = fitz.Rect(char['bbox'])
                    owners = [i for i, rect in enumerate(rects) if
                              (not box.is_empty and (box & rect).get_area() >= box.get_area() * .999) or
                              (box.is_empty and rect.contains(box))]
                    if len(owners) != 1:
                        warn(f'Page {number}: identity slice has ambiguous source glyph geometry; preserved.')
                        return
                    chosen.append((offset, stop, owners[0]))
                offset = stop
            parts = []
            for i, rect in enumerate(rects):
                indices = [j for j, (_, _, owner) in enumerate(chosen) if owner == i]
                if not indices:
                    continue
                if indices != list(range(indices[0], indices[-1] + 1)):
                    warn(f'Page {number}: identity slice interleaves source glyphs; preserved.')
                    return
                a, b = chosen[indices[0]][0], chosen[indices[-1]][1]
                parts.append((a, raw_text[a:b], rect))
            parts.sort(key=lambda item: item[0])
            if re.sub(r'\s+', '', ''.join(text for _, text, _ in parts)) != re.sub(r'\s+', '', piece):
                warn(f'Page {number}: identity slice source text is incomplete; preserved.')
                return
            for _, text, rect in parts:
                add(number, {'text': text, 'rect': list(rect)}, category)

        def add_slices(number, lines, slices, category):
            selected = {id(line) for line, _, _ in slices}
            rects = [rect for line, start, end in slices
                     for rect in doc[number - 1].search_for(line['text'][start:end], clip=fitz.Rect(line['rect']))]
            if any(id(other) not in selected and any(rect.intersects(fitz.Rect(other['rect'])) for rect in rects)
                   for other in lines):
                warn(f'Page {number}: identity text overlaps unrelated text; region preserved.')
                return
            for line, start, end in slices:
                if start or end < len(line['text']):
                    add_prefix(number, line, end, category, start)
                else:
                    add(number, line, category)

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
        inferred_boundary = False
        if boundary is None and len(pages) > 1:
            next_content = [line for line in pages[1]
                            if not (VENUE.search(line['text']) or ARXIV.match(line['text']) or
                                    re.fullmatch(r'\d{1,3}', line['text'].strip()))]
            first_text = ' '.join(_normal(line['text']) for line in first)
            known_count = sum(bool(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', first_text))
                              for name in names)
            if (next_content and _body_start(next_content[0]['text']) and known_count >= 2 and
                    any(AFFILIATION.search(line['text']) for line in first)):
                boundary = height
        if boundary is None:
            boundary = _unlabelled_body_boundary(first, paper, names, height)
            inferred_boundary = boundary is not None
        if boundary is None:
            warn('Page 1: no reliable abstract or introduction boundary for the author block.')
        header = [line for line in first
                  if line['rect'][3] <= (boundary if boundary is not None else height * .4)]
        title_indexes = _title_indexes(header, _normal(paper.get('title') or ''))
        caption_indexes = _caption_indexes(header)
        if not title_indexes:
            warn('Page 1: metadata title was not matched and structural title is ambiguous; byline preserved.')
        title_end = max((header[i]['rect'][3] for i in title_indexes), default=height)
        byline_names = _header_author_names([line for i, line in enumerate(header) if i not in caption_indexes], names)
        matches = _matched_lines(header, byline_names) - title_indexes - caption_indexes
        anchors = {i for i in matches if header[i]['rect'][1] >= title_end and
                   (_byline_only(header[i]['text'], byline_names) or
                    _byline_with_affiliation(header[i]['text'], byline_names) or
                    (_name_shape(header[i]['text']) and re.search(r'[,\d*†‡]', header[i]['text'])))}
        # A known full name can itself wrap into two short lines.
        for i in matches:
            nearby = [j for j in matches if abs(j - i) < 3 and header[j]['rect'][1] >= title_end]
            joined = ' '.join(header[j]['text'] for j in sorted(nearby))
            if _byline_only(joined, byline_names):
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
        # A complete metadata name may wrap after an already verified byline.
        # The suffix alone is never used as a global short-name rule.
        for index in sorted(matches - title_indexes):
            if not index or index in author_indexes or index - 1 not in author_indexes:
                continue
            previous, line = header[index - 1], header[index]
            centered_suffix = (previous['rect'][0] <= line['rect'][0] and line['rect'][2] <= previous['rect'][2] and
                               abs(sum(previous['rect'][::2]) - sum(line['rect'][::2])) < 6 and
                               line['rect'][2] - line['rect'][0] < (previous['rect'][2] - previous['rect'][0]) * .5)
            if ((previous['block'] != line['block'] and not centered_suffix) or
                    abs(previous['size'] - line['size']) > 1.2 or
                    not 0 <= line['rect'][1] - previous['rect'][3] < 16 or
                    line['rect'][3] >= min(boundary, first_graphic_top)):
                continue
            if any(re.search(r'(?<!\w)' + re.escape(' '.join(name.split()[:cut])) + r'$',
                             _normal(previous['text'])) and
                   _byline_only(line['text'], [' '.join(name.split()[cut:])])
                   for name in byline_names for cut in range(1, len(name.split()))):
                author_indexes.add(index)
        first_index = {id(line): i for i, line in enumerate(first)}
        layout_evidence = {'title_indexes': [first_index[id(header[i])] for i in sorted(title_indexes)],
                           'author_indexes': [first_index[id(header[i])] for i in sorted(author_indexes)],
                           'body_boundary': boundary}
        source_profile = _select_layout_profile(
            paper, pages, [((page.rect * page.derotation_matrix).width,
                            (page.rect * page.derotation_matrix).height) for page in doc],
            first_graphics=first_graphics, first_page_evidence=layout_evidence)
        if diagnostics is not None:
            diagnostics['source_layout_profile'] = source_profile
            diagnostics['profile_id'] = (
                source_profile['selected_family'] + '/' + source_profile['observed_variant']
                if source_profile['selected_family'] else 'conservative')
            diagnostics['profile_schema'] = 'source-layout-1'

        identity_indexes = set()
        wrapped_contacts = _wrapped_header_contacts(header)
        if anchors:
            start = min(anchors)
            # Adjacent names may differ from the latest metadata roster.
            while start and header[start - 1]['rect'][1] >= title_end:
                prev, cur = header[start - 1], header[start]
                if (cur['rect'][1] - prev['rect'][3] > 12 or not _name_shape(prev['text']) or
                        (prev['block'] != cur['block'] and
                         (abs(prev['rect'][1] - cur['rect'][1]) >= 3 or
                          not any(AFFILIATION.search(item['text']) and
                                  item['block'] == prev['block'] for item in header))) or
                        abs(prev['size'] - cur['size']) > 1):
                    break
                start -= 1
                author_indexes.add(start)
                if start not in anchors:
                    warn('Page 1: an adjacent author-shaped line absent from metadata was removed; roster needs review.')
            previous = header[start]
            blocked = False
            for index in range(start, len(header)):
                if index in caption_indexes:
                    blocked = True
                    continue
                line = header[index]
                text = line['text']
                contact_note = bool(EMAIL.search(text) and _identity_footnote(text, names))
                if blocked and index not in author_indexes and not contact_note:
                    recovered = _known_identity_after_header_break(
                        line, header, author_indexes, byline_names, title_end, boundary, first_graphic_top,
                        paper.get('title') or '')
                    if recovered:
                        add(1, line, recovered)
                        identity_indexes.add(index)
                    continue
                if index in author_indexes or contact_note:
                    blocked = False
                    previous = line
                gap = line['rect'][1] - previous['rect'][3]
                gap_limit = max(22, previous['size'] * 1.8)
                label = re.match(r'^(\d{1,2})\s*[A-Z]', text)
                if (boundary == height and label and AFFILIATION.search(text) and
                        any(label.group(1) in re.findall(r'\d{1,2}', header[i]['text']) for i in anchors)):
                    gap_limit = max(gap_limit, 48)
                if gap > gap_limit:
                    recovered = _known_identity_after_header_break(
                        line, header, author_indexes, byline_names, title_end, boundary, first_graphic_top,
                        paper.get('title') or '')
                    if recovered:
                        add(1, line, recovered)
                        identity_indexes.add(index)
                        warn('Page 1: author-block layout gap requires review; a separately verified identity line was removed.')
                    else:
                        warn(f'Page 1: unclassified author-block line after layout gap was preserved: {text[:160]}')
                    blocked = True
                    continue
                if index in author_indexes:
                    category = 'author'
                elif (_name_shape(text) and abs(line['size'] - header[min(anchors)]['size']) <= 1
                      and (any(abs(line['rect'][1] - header[i]['rect'][1]) < 3 for i in anchors) or
                           (line['block'] in {header[i]['block'] for i in anchors}
                            and (index <= max(anchors) or re.search(r'[\d*∗†‡]', text))))
                      and index <= max(anchors) + 2
                      and any((AFFILIATION.search(item['text']) or EMAIL.search(item['text']))
                              and 0 <= item['rect'][1] - line['rect'][1] <= 55
                              for item in header[index + 1:])):
                    category = 'author'
                    author_indexes.add(index)
                    warn('Page 1: an adjacent author-shaped line absent from metadata was removed; roster needs review.')
                elif (AFFILIATION.search(text) or _corporate_affiliation(text) or
                      _affiliation_fragment(line, header) or
                      _short_affiliation(line, header, author_indexes, names, paper.get('title') or '', first_graphic_top) or
                      _location_contact_fragment(line, header, {item['text'] for item in plan['redactions'] if item['page'] == 1 and item['category'] == 'affiliation'})):
                    category = 'affiliation'
                elif index in wrapped_contacts:
                    category = 'contact'
                elif EMAIL.search(text) and (_contact_only(text) or not re.search(r'\b(?:is|are|the|input|example)\b', EMAIL.sub('', text), re.I)):
                    category = 'contact'
                elif (ORCID.search(text) or _owned_link_line(text) or
                      (URL.search(text) and _complete_uri(text.strip().rstrip('.,;)')) and not _normal(URL.sub('', text))) or
                      re.fullmatch(r'(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?', text)):
                    category = 'author_link'
                elif _identity_footnote(text, names, affiliations=[item['text'] for item in plan['redactions'] if item['page'] == 1 and item['category'] == 'affiliation']):
                    category = 'other_identity'
                elif VENUE.search(text):
                    category = 'venue'
                elif ARXIV.search(text):
                    category = 'identifier'
                else:
                    warn(f'Page 1: unclassified author-block line: {text[:160]}')
                    if inferred_boundary:
                        break
                    blocked = True
                    continue
                add(1, line, category)
                identity_indexes.add(index)
                previous = line

        for line, end in _affiliation_contact_blocks(first, doc[0].rect * doc[0].derotation_matrix, names):
            if end < len(line['text']):
                add_prefix(1, line, end, 'other_identity')
            else:
                add(1, line, 'other_identity')

        # Review locators are publication frontmatter even if an unrelated
        # author-card fragment stopped the identity walk.
        if boundary is not None and title_indexes:
            for line in header:
                if (line['rect'][1] >= title_end and line['rect'][3] < min(boundary, first_graphic_top) and
                        re.match(r'^Reviewed on [A-Za-z][\w.-]*\s*:', line['text'], re.I)):
                    add(1, line, 'venue')

        # Recover only declared institutions and geography attached to proved
        # affiliations; an unknown source row still remains unknown.
        known_header_affiliations = {item['text'] for item in plan['redactions']
                                     if item['page'] == 1 and item['category'] == 'affiliation'}
        for _ in range(2):
            for index, line in enumerate(header):
                if _marked_header_institution(line, header, author_indexes, title_end,
                                              boundary, first_graphic_top, known_header_affiliations):
                    add(1, line, 'affiliation')
                    known_header_affiliations.add(line['text'])
                    identity_indexes.add(index)
        for index, line in enumerate(header):
            if _affiliation_location(line, header, author_indexes, title_end, boundary,
                                     first_graphic_top, known_header_affiliations):
                add(1, line, 'affiliation')
                identity_indexes.add(index)
        known_header_affiliations = {item['text'] for item in plan['redactions']
                                     if item['page'] == 1 and item['category'] == 'affiliation'}
        prior_contacts = {item['text'] for item in plan['redactions']
                          if item['page'] == 1 and item['category'] == 'contact'}
        card_contacts = _header_contact_slices(header, author_indexes, title_end, boundary,
                                               known_header_affiliations, first_graphic_top,
                                               first_graphics, prior_contacts)
        add_slices(1, first, card_contacts, 'contact')
        verified_markers = {part for index in author_indexes
                            for part in re.findall(r'\d{1,2}|[' + _CONTACT_MARKERS + ']',
                                                   header[index]['text'])}
        contact_slices = _own_contact_slices(first, (doc[0].rect * doc[0].derotation_matrix).width,
                                             height, names, verified_markers)
        add_slices(1, first, [(line, start, end) for line, start, end, _ in contact_slices], 'contact')

        header_markers = set(re.findall(r'[*∗†‡§¶♠♣♥♦◊⋄♯]',
                                        ' '.join(header[i]['text'] for i in identity_indexes)))
        for line in _fragmented_affiliation_notes(first, height, header_markers):
            add(1, line, 'other_identity')
        known_links = []
        for item in plan['redactions']:
            if item['page'] == 1 and item['category'] == 'author_link':
                known_links.extend(match.group() for match in URL.finditer(item['text']))
                if re.fullmatch(r'(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/\S*)?', item['text']):
                    known_links.append(item['text'])
        page_sizes = [((page.rect * page.derotation_matrix).width,
                       (page.rect * page.derotation_matrix).height) for page in doc]
        source_supplement_headers = {(number, id(marker))
                                     for number, _, markers in _supplement_headers(pages, page_sizes, paper)
                                     for marker in markers}
        source_author_headers = _source_author_headers(pages, page_sizes, names)
        source_status_headers = _source_status_headers(pages, page_sizes, paper.get('title') or '')
        source_venue_headers = {(number, index)
                                for number, author_index in source_author_headers
                                for index, line in enumerate(pages[number - 1])
                                if _publication_header_partner(pages[number - 1][author_index], line)}
        controlled_contributions = set()
        verified_byline = [header[i] for i in sorted(author_indexes)]
        for page_number, source_lines in enumerate(pages, 1):
            page_width = page_sizes[page_number - 1][0]
            for heading in source_lines:
                body = _contribution_body(source_lines, heading, page_width)
                if body is None:
                    controlled_contributions.add((page_number, id(heading)))
                    warn(f'Page {page_number}: credit-like heading occurs in a scientific example; preserved.')
                    continue
                if not body:
                    continue
                # Keep the established bounded role-subsection handler. The
                # name-only path handles prose paragraphs and inline CRediT fields.
                role_labels = {_normal(item['text']) for item in body
                               if re.fullmatch(_CONTRIB_LABEL, item['text'], re.I) or
                               _normal(item['text']) in {'project leadership', 'model and training'}}
                if len(role_labels) >= 2:
                    continue
                slices, evidence = _contribution_name_slices(
                    body, names, heading=heading['text'], source_byline=verified_byline)
                if evidence['context'] is None:
                    continue
                controlled_contributions.add((page_number, id(heading)))
                if slices:
                    add(page_number, heading, 'other_identity')
                    add_slices(page_number, source_lines, slices, 'other_identity')
                else:
                    warn(f'Page {page_number}: contribution context has no safely matched name slots; preserved.')
                if any(match['resolved_metadata_name'] is None for match in evidence['matches']):
                    warn(f'Page {page_number}: contributor initials identify a name slot but not one unique metadata author.')
                for issue in evidence['uncertain']:
                    warn(f'Page {page_number}: {issue}')

        column_maps = {}
        in_references = False
        reference_context = set()
        for number, lines in enumerate(pages, 1):
            bounds = doc[number - 1].rect * doc[number - 1].derotation_matrix
            for index, line in enumerate(lines):
                text = line['text']
                if _reference_heading(text):
                    in_references = True
                elif (number, id(line)) in source_supplement_headers:
                    in_references = False
                elif (_section_heading(line) and not _ack_heading(text) and
                      (not in_references or _heading(text, SCIENCE_HEADING) or
                       re.match(r'^Appendi(?:x|ces)\b', text, re.I))):
                    in_references = False
                if in_references:
                    reference_context.add((number, id(line)))
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
                elif (number, index) in source_status_headers:
                    add(number, line, 'venue')
                elif (number, index) in source_venue_headers:
                    add(number, line, 'venue')
                elif (VENUE.search(text) and (margin or header_context) and
                      (not in_references or
                       ((line['rect'][1] < bounds.height * .09 or line['rect'][3] > bounds.height * .93) and
                        not re.match(r'^proceedings of', text, re.I)))):
                    if (number > 1 and (SHORT_STATUS.fullmatch(text) or
                            re.match(r'^(?:proceedings of|preprint\b|published in|reviewed on)', text, re.I))
                            and not earlier_margin_identity(number, line, 'venue')):
                        warn(f'Page {number}: unanchored Proceedings/Preprint text was preserved for reference review.')
                    else:
                        add(number, line, 'venue')
                elif ((number, index) in source_author_headers or
                      (margin and _byline_only(text, names) and
                       (number == 1 and not in_references or
                        earlier_margin_identity(number, line, 'author')))):
                    add(number, line, 'author')
                elif not in_references and number == 1 and (line['rect'][1] > bounds.height * .65 or
                      (EMAIL.search(text) and any(name in _normal(text) for name in names) and
                       re.match(r'^(?:[\d*∗†‡\s]+)?Correspond', text, re.I))) and _identity_footnote(_identity_note_context(lines, index), names, header_markers):
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
                                re.match(r'^[\d*∗†‡§¶⋄♯]+\s*[A-Z]', following['text'])):
                            break
                        prefix_end = _note_prefix_end(following['text'])
                        if prefix_end < len(following['text']):
                            add_prefix(number, following, prefix_end, 'other_identity')
                            break
                        add(number, following, 'other_identity')
                        previous = following
                elif URI_START.search(text) and not in_references:
                    urls = list(URI_START.finditer(text))
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
                            if header_context:
                                add(number, prev, 'author_link')
                    own_regions = _own_arxiv_regions(doc[number - 1], line, paper)
                    release_regions = [] if header_context else _release_url_regions(doc[number - 1], lines, index, known_links, explicit_owner=bool(explicit))
                    if own_regions or release_regions:
                        for region in own_regions:
                            add(number, region, 'identifier')
                        for region in release_regions:
                            add(number, region, 'author_link')
                            if region.get('uri') and region['uri'] not in known_links:
                                known_links.append(region['uri'])
                    elif header_context:
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
                if (number, id(line)) in controlled_contributions:
                    continue
                section_kind = _identity_section_kind(text)
                if not section_kind:
                    continue
                if number not in column_maps:
                    column_maps[number] = _column_lefts(lines)
                column_lefts = column_maps[number]
                column = [item for item in lines
                          if ((column_lefts[id(item)] >= bounds.width * .5) ==
                              (column_lefts[id(line)] >= bounds.width * .5))]
                position = next(i for i, item in enumerate(column) if item is line)
                ends = [later for offset, later in enumerate(column[position + 1:], position + 1)
                        if later['rect'][1] > line['rect'][3] and
                        _identity_boundary(later, column[offset - 1], section_kind, line['size'])]
                if not ends:
                    continuation = _cross_page_ack(pages, number, line, bounds, section_kind, names)
                    if continuation:
                        if section_kind == 'acknowledgment' and not _credit_text(
                                ' '.join(item['text'] for _, item in continuation), section_kind, names, allow_generic=True):
                            warn(f'Page {number}: acknowledgment-like text has no positive author credit evidence; preserved.')
                            continue
                        mixed = (_mixed_acknowledgment_slices([item for _, item in continuation], names)
                                 if section_kind == 'acknowledgment' else None)
                        if mixed is not None:
                            page_by_line = {id(item): page_number for page_number, item in continuation}
                            for page_number in sorted(set(page_by_line.values())):
                                slices = [(item, start, end) for item, start, end in mixed
                                          if page_by_line[id(item)] == page_number]
                                add_slices(page_number, pages[page_number - 1], slices, section_kind)
                            warn(f'Page {number}: mixed credit and scientific prose; only complete credit sentences removed.')
                            continue
                        for page_number, item in continuation:
                            add(page_number, item, section_kind)
                        continue
                    next_start = []
                    if number < len(pages):
                        next_start = [item for item in pages[number]
                                      if not (VENUE.search(item['text']) or ARXIV.match(item['text']) or
                                              re.fullmatch(r'\d{1,3}', item['text'].strip()))]
                    if not next_start or not _section_heading(next_start[0]):
                        local_credit = _local_credit_lines(column, line, section_kind, names, document_end=number == len(pages))
                        if local_credit:
                            warn(f'Page {number}: no safe same-page end boundary; complete local credit blocks were removed.')
                            mixed = (_mixed_acknowledgment_slices(local_credit, names)
                                     if section_kind == 'acknowledgment' else None)
                            add_slices(number, lines, mixed if mixed is not None else
                                       [(item, 0, len(item['text'])) for item in local_credit], section_kind)
                            continue
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
                section = [item for item in column
                           if line['rect'][1] <= item['rect'][1] and item['rect'][3] < end_y and
                           _normal(item['text']) and not re.fullmatch(r'\d{1,3}', item['text'].strip())]
                if section_kind == 'acknowledgment' and not _credit_text(
                        ' '.join(item['text'] for item in section), section_kind, names, allow_generic=True):
                    warn(f'Page {number}: acknowledgment-like text has no positive author credit evidence; preserved.')
                    continue
                if (any(column_lefts[id(item)] >= bounds.width * .5 and
                        line['rect'][1] <= item['rect'][1] < end_y for item in lines) and
                        any(item['rect'][0] < bounds.width * .4 and
                            item['rect'][2] > bounds.width * .55 for item in section)):
                    warn(f'Page {number}: acknowledgment section has a column ambiguity.')
                    continue
                if any(item['rect'][2] - item['rect'][0] > bounds.width * .8 for item in section):
                    warn(f'Page {number}: acknowledgment section bounds require manual review.')
                    continue
                mixed = (_mixed_acknowledgment_slices(section, names)
                         if section_kind == 'acknowledgment' else None)
                if mixed is not None:
                    add_slices(number, lines, mixed, section_kind)
                    warn(f'Page {number}: mixed credit and scientific prose; only complete credit sentences removed.')
                    continue
                for item in section:
                    add(number, item, section_kind)

        # Bare locators use the same reference exclusions and exact source-URI
        # binding as printed schemes. A header label needs prior byline evidence.
        for number, lines in enumerate(pages, 1):
            page_links = [link for link in doc[number - 1].get_links() if link.get('uri')]
            for index, line in enumerate(lines):
                if (URI_START.search(line['text']) or (number, id(line)) in reference_context or
                        not any(fitz.Rect(link['from']).intersects(fitz.Rect(line['rect'])) for link in page_links)):
                    continue
                explicit = False
                if (number == 1 and anchors and title_indexes and boundary is not None and
                        title_end <= line['rect'][1] < line['rect'][3] < min(boundary, first_graphic_top)):
                    earlier = [item for i, item in enumerate(header)
                               if i not in author_indexes and i not in title_indexes and
                               item is not line and title_end <= item['rect'][1] <= line['rect'][1]]
                    label = re.match(r'^\W{0,3}(?:Code|Data\s*(?:&|and)\s*Code|Project(?: page| website)?|Models?)\s*[:.]?\s+', line['text'], re.I)
                    explicit = bool(label and not any(re.search(
                        r'\b(?:example|literal|quoted|prompt|input|output|caption|figure|table)\b',
                        item['text'], re.I) for item in earlier))
                if (number == 1 and not explicit and line['rect'][1] >= height * .6 and line['size'] <= 11 and
                        re.match(r'^Project (?:website|page)\s*:', line['text'], re.I)):
                    # A project footnote can be tied to the same manuscript by
                    # its source correspondence note and named method/author.
                    peers = [item for item in lines if item['block'] == line['block']]
                    own_contact = any(_identity_footnote(item['text'], names) and
                                      EMAIL.search(item['text']) and
                                      any(mask['page'] == 1 and mask['text'] == item['text']
                                          for mask in plan['redactions']) for item in peers)
                    title_word = (paper.get('title') or '').split(':', 1)[0].strip()
                    introduced = bool(re.fullmatch(r'[A-Z][A-Za-z0-9-]{2,}', title_word) and
                        re.search(r'\bwe\s+(?:introduce|propose|present)\s+' + re.escape(title_word) + r'\b',
                                  _flat('\n'.join(item['text'] for item in lines)), re.I))
                    value = _normal(line['text'])
                    literal = any(re.match(r'^(?:Example|Prompt|Literal|Quoted|Input|Output|Figure|Table)\b'
                                           r'|^The (?:model|assistant) (?:generated|produced|outputs?)\b',
                                           item['text'], re.I) for item in peers)
                    explicit = bool(not literal and own_contact and introduced and _normal(title_word) in value.split() and
                                    any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', value)
                                        for name in names))
                for region in _release_url_regions(doc[number - 1], lines, index, known_links, explicit):
                    add(number, region, 'author_link')
                    if region['uri'] not in known_links:
                        known_links.append(region['uri'])

        # Discover source-owned locators before repeating their exact visible
        # occurrences. The seed may occur after the earliest printed locator.
        intro_lines = [line for number, lines in enumerate(pages, 1) for line in lines
                       if (number, id(line)) not in reference_context]
        named_method = _own_named_method(paper.get('title') or '', intro_lines) or ''
        locator_inputs = []
        for number, lines in enumerate(pages, 1):
            page_links = [link for link in doc[number - 1].get_links() if link.get('uri')]
            for index, line in enumerate(lines):
                if ((number, id(line)) in reference_context or
                        (number == 1 and any(line is header[i] for i in title_indexes)) or
                        not (URI_START.search(line['text']) or any(
                            fitz.Rect(link['from']).intersects(fitz.Rect(line['rect']))
                            for link in page_links))):
                    continue
                locator_inputs.append((number, lines, index, page_links))
                def source_seed(offset):
                    return _additional_seed_proof(
                        lines, index, offset, paper.get('title') or '', (),
                        named_method=named_method)
                for region in _release_url_regions(doc[number - 1], lines, index,
                                                   known_links, seed_proof=source_seed,
                                                   source_links=page_links):
                    add(number, region, 'author_link')
                    if region['uri'] not in known_links:
                        known_links.append(region['uri'])
        for number, lines, index, page_links in locator_inputs:
            for region in _release_url_regions(doc[number - 1], lines, index, known_links,
                                               source_links=page_links):
                add(number, region, 'author_link')

        known_affiliations = [item['text'] for item in plan['redactions']
                              if item['page'] == 1 and item['category'] == 'affiliation']
        for number, lines in enumerate(pages, 1):
            for block in _text_blocks(lines):
                prefixes = [(line, 0, end) for line, end in _block_prefix(block, lambda text, first: _funding_sentence(text, first, names))]
                if prefixes:
                    add_slices(number, lines, prefixes, 'acknowledgment')
                credit_slices = list(_range_slices(block, _personal_note_ranges(
                    block, lines, doc[number - 1].rect * doc[number - 1].derotation_matrix, names, known_affiliations)))
                if credit_slices:
                    add_slices(number, lines, credit_slices, 'other_identity')
                text = '\n'.join(line['text'] for line in block)
                if not any((number, id(line)) in reference_context for line in block):
                    inline_slices = list(_range_slices(block, _inline_credit_ranges(text, names)))
                    if inline_slices:
                        add_slices(number, lines, inline_slices, 'other_identity')
                slices = list(_range_slices(block, _ethics_identity_spans(text)))
                if slices:
                    add_slices(number, lines, slices, 'other_identity')
            roster = _contributor_roster_lines(lines, names)
            if roster:
                add_slices(number, lines, [(line, 0, len(line['text'])) for line in roster], 'other_identity')

        for number, line, category in _supplement_identities(pages, page_sizes, paper, names):
            add(number, line, category)
        for line in _adjacent_publication_notices(first, doc[0].rect * doc[0].derotation_matrix,
                                               plan['redactions'], names):
            add(1, line, 'venue')
        for line in _publication_banners(first, paper):
            add(1, line, 'venue')

        first_lookup = {id(line): i for i, line in enumerate(first)}
        primary_title = ({first_lookup[id(header[i])] for i in title_indexes} &
                         _matched_lines(first, [_normal(paper.get('title') or '')]))
        primary_authors = {first_lookup[id(header[i])] for i in author_indexes}
        confirmed_header = {i for i, line in enumerate(first) if any(
            item['page'] == 1 and item['category'] in {'author', 'affiliation', 'contact'} and
            item['text'] == line['text'] and fitz.Rect(item['rect']).contains(fitz.Rect(line['rect']))
            for item in plan['redactions'])}
        confirmed_org_tokens = set().union(*(
            _publication_tokens(item['text']) for item in plan['redactions']
            if item['page'] == 1 and item['category'] == 'affiliation')) - _PUB_GENERIC
        brands = {i for i, line in enumerate(first) if primary_title and
                  line['rect'][3] < min(first[j]['rect'][1] for j in primary_title) and
                  re.search(r'\b(?:Group|Lab|Labs|Laboratory|Research|University|Institute)\b', line['text']) and
                  (_publication_tokens(line['text']) - _PUB_GENERIC) & confirmed_org_tokens}
        abstract_index = next((i for i, line in enumerate(first)
                               if _normal(line['text']) == 'abstract'), None)
        margin_ids = {i for i, line in enumerate(first) if any(
            item['page'] == 1 and item['category'] == 'identifier' and item['text'] == line['text']
            for item in plan['redactions'])}
        for line in _publication_frontmatter_lines(
                first, title_indexes=primary_title, author_indexes=primary_authors,
                abstract_index=abstract_index, page_size=page_sizes[0],
                graphics=None if first_graphics is None else [item['rect'] for item in first_graphics],
                header_identity_indexes=confirmed_header, branding_indexes=brands,
                excluded_indexes=_caption_indexes(first), margin_identifier_indexes=margin_ids):
            add(1, line, 'venue')

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




# Regional scope is independent of the historical identifier planner.
import math
from collections import Counter

def _region_drawing_rect(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or
            any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value)):
        return None
    try:
        if not all(math.isfinite(x) for x in value):
            return None
    except (OverflowError, TypeError, ValueError):
        return None
    if value[0] > value[2] or value[1] > value[3]:
        return None
    return tuple(value)

def _region_drawing_intersection(a, b):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    return (left, top, right, bottom) if left <= right and top <= bottom else None

def _region_clipped_drawing_rects(drawings, page_bounds):
    """Return conservative path rectangles, or None when hierarchy is unknown.

    Input is Page.get_cdrawings(extended=True), in original encounter order.
    A clip applies only to later entries with a strictly greater level. Groups
    establish hierarchy but are not clipping rectangles or painted paths.
    Only fill-only paths are tightened to clip scissors. Stroke/fill-stroke
    paths remain conservative, including paths outside a recorded clip. A stroke
    box requiring page-boundary trimming makes the inventory unknown.
    """
    page = _region_drawing_rect(page_bounds)
    if page is None or page[0] == page[2] or page[1] == page[3]:
        return None
    if not isinstance(drawings, (list, tuple)):
        return None
    clips, result = [], []
    for drawing in drawings:
        if not isinstance(drawing, dict):
            return None
        level = drawing.get('level')
        if type(level) is not int or level < 0:
            return None
        clips = [(depth, box) for depth, box in clips if depth < level]
        kind = drawing.get('type')
        if kind == 'clip':
            box = _region_drawing_rect(drawing.get('scissor'))
            if box is None:
                return None
            clips.append((level, box))
            continue
        if kind == 'group':
            continue
        if not isinstance(kind, str) or kind not in {'f', 's', 'fs'}:
            return None
        box = _region_drawing_rect(drawing.get('rect'))
        if box is None:
            return None
        if kind in {'s', 'fs'} and (box[0] < page[0] or box[1] < page[1] or
                                     box[2] > page[2] or box[3] > page[3]):
            # Path boxes omit stroke extent. Cropping one can hide stroke ink
            # entering the page, so the layout inventory must remain unknown.
            return None
        box = _region_drawing_intersection(box, page)
        if kind == 'f':
            for _, clip in clips:
                if box is not None:
                    box = _region_drawing_intersection(box, clip)
        if box is not None and (kind != 'f' or box[0] < box[2] and box[1] < box[3]):
            # Keep zero-width/height path boxes: a stroke may have visible ink.
            result.append(box)
    return result

_REGION_FN_MARKERS = re.compile(r"[\s\d*∗†‡§¶♠♣♥♦◊✉.]*")

_REGION_FN_CONNECTOR = re.compile(r"\s*(?:,\s*(?:and\s+)?|and\s+|&\s*)")

_REGION_FN_PREFIX_ROLE = re.compile(
    r"(?:correspondence(?:\s+to)?\s*:|corresponding authors?\s*:|"
    r"project[-\s]+lead(?:er)?\s*:)\s*", re.I)

_REGION_FN_SUFFIX_ROLE = re.compile(
    r"\s+(?:(?:is|are)\s+(?:also\s+)?(?:the\s+|a\s+)?"
    r"(?:corresponding authors?|co[-–—\s]+first authors?)\b|"
    r"contributed\s+equally\b|(?:is|are)\s+with\b)", re.I)

_REGION_FN_PROTECTED = re.compile(
    r"(?:^|\n)\s*(?:\[\d+\]|references\b|bibliography\b|"
    r"(?:figure|table|algorithm|listing)\s*\d+\b)|"
    r"\b(?:example|literal|quoted|prompt|sample|input|output)\s*:", re.I)

_REGION_FN_INSTITUTION = re.compile(
    r"\b(?:university|institute|department|school|college|laboratory|"
    r"research cent(?:er|re)|EECS)\b", re.I)

_REGION_FN_PROCEDURE = re.compile(
    r"\b(?:probability|benchmark|baseline|algorithm|sampler|sample|"
    r"equation|proposition|theorem|assume|let|defines?|implementation|"
    r"examples?|prompts?|annotation|schema|fields?)\b", re.I)

def _region_note_name_patterns(names):
    """Only full metadata names and unique ordered initials plus surname."""
    full, initial_owners = [], {}
    for name in names:
        words = re.findall(r"[^\W\d_]+", name)
        if len(words) < 2:
            continue
        full.append(re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, words)) + r"(?!\w)", re.I))
        for given in (words[:1], words[:-1]):
            initials = [word[0].upper() for word in given]
            prefix = r"[.]?\s*".join(map(re.escape, initials))
            pattern = (r"(?<!\w)" + prefix + r"(?:[.]\s*|\s+)" +
                       "(?i:" + re.escape(words[-1]) + r")(?!\w)")
            initial_owners.setdefault(pattern, set()).add(" ".join(words).casefold())
    return full + [re.compile(pattern) for pattern, owners in initial_owners.items() if len(owners) == 1]

def _region_note_name_list(text, start, patterns):
    names, cursor = [], start
    while True:
        matches = [match for pattern in patterns if (match := pattern.match(text, cursor))]
        if not matches:
            break
        match = max(matches, key=lambda m: m.end())
        names.append((match.start(), match.end()))
        cursor = match.end()
        connector = _REGION_FN_CONNECTOR.match(text, cursor)
        if not connector:
            break
        # Keep the cursor at the last actual name when an unknown name follows.
        if not any(pattern.match(text, connector.end()) for pattern in patterns):
            break
        cursor = connector.end()
    return names, cursor

def _region_note_declaration_names(text, patterns):
    if _REGION_FN_PROTECTED.search(text):
        return []
    spans = set()
    # Initial periods introduce harmless extra starts; no surname-only pattern
    # exists, so they cannot broaden a match into an unrelated name.
    starts = {0} | {match.end() for match in re.finditer(r"[;.!?]\s*", text)}
    # A line wrap alone cannot introduce a declaration: it might be a scientific
    # prompt containing a name. Only a new explicit footnote marker can do so.
    starts |= {match.end() for match in re.finditer(r"\n(?=\s*[\d*∗†‡§¶])", text)}
    for start in sorted(starts):
        start = _REGION_FN_MARKERS.match(text, start).end()
        prefix = _REGION_FN_PREFIX_ROLE.match(text, start)
        if prefix:
            names, end = _region_note_name_list(text, prefix.end(), patterns)
            if names and re.match(r"\s*(?:[.;,<(]|$)", text[end:]):
                spans.update(names)
            continue
        names, end = _region_note_name_list(text, start, patterns)
        if not names:
            continue
        role = _REGION_FN_SUFFIX_ROLE.match(text, end)
        if not role:
            continue
        if re.search(r"\bwith$", role.group(), re.I):
            tail = text[role.end():].lstrip()
            clause = re.split(r"[.;\n]", tail, maxsplit=1)[0]
            if (not clause or not clause[0].isupper() and not clause.startswith("the ") or
                    not _REGION_FN_INSTITUTION.search(clause) or _REGION_FN_PROCEDURE.search(clause)):
                continue
        elif not re.match(r"\s*(?:[.;,)]|$)", text[role.end():]):
            # A declaration used as a field, example or procedural predicate
            # must not become an author target merely through matching words.
            continue
        spans.update(names)
    return sorted(spans)

def _region_footer_author_name_slices(lines, names, *, page_size, body_boundary,
                              page_number=1, excluded_indexes=()):
    """Return (original_line, start, end) tuples for own footer names only.

    Lines have text/rect/size/block fields. The caller supplies the source's
    explicit first-page body boundary. Affiliation, email, funding, role labels,
    third-party names and scientific prose are never included in returned spans.
    """
    if page_number != 1 or body_boundary is None:
        return []
    width, height = page_size
    if width <= 0 or height <= 0:
        return []
    patterns = _region_note_name_patterns(names)
    if not patterns:
        return []
    reference_top = min((line["rect"][1] for line in lines if
                         re.fullmatch(r"\s*(?:references|bibliography)\s*", line["text"], re.I)),
                        default=float("inf"))
    excluded = set(excluded_indexes)
    blocks = {}
    for index, line in enumerate(lines):
        if (index not in excluded and line["size"] <= 10.5 and
                line["rect"][1] >= max(body_boundary, height * .65) and
                line["rect"][3] <= height and line["rect"][1] < reference_top):
            blocks.setdefault(line["block"], []).append((index, line))
    found = []
    for block in blocks.values():
        # Do not reconstruct through excluded or ineligible source lines.
        indexes = [index for index, _ in block]
        if any(lines[i]["block"] == block[0][1]["block"] and i not in indexes
               for i in range(min(indexes), max(indexes) + 1)):
            continue
        text = "\n".join(line["text"] for _, line in block)
        offsets, cursor = [], 0
        for _, line in block:
            offsets.append((line, cursor, cursor + len(line["text"])))
            cursor += len(line["text"]) + 1
        for start, end in _region_note_declaration_names(text, patterns):
            pieces = [(line, max(0, start - left), min(len(line["text"]), end - left))
                      for line, left, right in offsets if start < right and end > left]
            if any(abs(b[0]["rect"][1] - a[0]["rect"][1]) >
                   1.8 * max(a[0]["size"], b[0]["size"])
                   for a, b in zip(pieces, pieces[1:])):
                continue
            found.extend(pieces)
    # Corroborating role scans can select the same source span twice.
    unique = {(id(line), start, end): (line, start, end) for line, start, end in found}
    return sorted(unique.values(), key=lambda item: (item[0]["rect"][1], item[0]["rect"][0], item[1]))

_REGION_FA_MARKER = re.compile(r"(?<![\w./])(\d{1,2})\s*(?=[A-Z])")

_REGION_FA_INSTITUTION = re.compile(
    r"\b(?:universit[yae]|univ[.]?|institute|department|school|college|laborator(?:y|ies)|"
    r"lab|cent(?:er|re)|corporation|gmbh|inc|EECS)\b", re.I)

_REGION_FA_CONTACT = re.compile(r"\b(?:corresponding\s+authors?\s*:|correspon(?:dence|-\s*dence)\s*(?:to|:)|contact\s+to\s*:|"
                      r"project\s+lead(?:er)?\s*:)", re.I)

_REGION_FA_SCIENCE = re.compile(r"\b(?:benchmark|model|models|dataset|algorithm|theorem|lemma|"
                      r"probability|experiment|experiments|evaluate|evaluates|evaluated|"
                      r"trained|training|baseline|sample|samples|accuracy|results|provided|"
                      r"provides|used|uses|we|our|this|these|example|literal|prompt)\b", re.I)

_REGION_FA_SUFFIX = re.compile(r"(?<=[.;])\s+(?=(?:We|Our|This|These|The model|Experiments?|Results?|"
                     r"Code\b|Project\b|For example)\b)")

_REGION_FA_PROTECTED = re.compile(r"\b(?:example|literal|prompt|model list|benchmark sources|"
                        r"institutions evaluated)\s*:|^(?:references|bibliography|"
                        r"(?:Figure|Table|Algorithm|Listing)\s+\d+)\b", re.I)

_REGION_FA_LOWER = set("of for the and in at on de des der la le du et an science technology "
             "university institute department laboratory lab center centre research "
             "engineering learning risk independent".split())

_REGION_FA_CREDIT = set("equal contribution contributions corresponding author authors and "
              "contributed equally is are also a the co first".split())

def _region_footer_reading_rows(items):
    """Cluster aligned source fragments before sorting left-to-right."""
    rows = []
    for item in sorted(items, key=lambda x: (x[1]["rect"][1], x[1]["rect"][0])):
        line = item[1]
        row = next((r for r in rows if abs(r[0][1]["rect"][3] - line["rect"][3]) <= 1.5), None)
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    return [item for row in rows for item in sorted(row, key=lambda x: x[1]["rect"][0])]

def _region_footer_noun_card(text):
    text = re.sub(r"(?<=\w)[-\u00ad]\s+(?=\w)", "", text).strip()
    text = re.sub(r"(?<=\w)[´`](?=\w)", "", text)
    if not text or len(text) > 260 or _REGION_FA_SCIENCE.search(text) or "@" in text or "http" in text.lower():
        return False
    words = re.findall(r"[^\W\d_]+", text)
    return bool(words and all(word[0].isupper() or word.casefold() in _REGION_FA_LOWER for word in words))

def _region_footer_affiliation_slices(lines, *, byline_indexes, body_boundary, page_size,
                              graphics, layout_kind, author_name_slices=(),
                              excluded_indexes=(), page_number=1):
    """Return original-line (line,start,end) slices from verified footer cards.

    layout_kind is 'icml_numbered_note' or 'ieee_author_relation_note'; the
    source venue label is never a deletion condition. Byline indices and author
    name slices must already be metadata-supported. graphics is a source
    inventory of rectangles (None means unknown). No name helper is mutated.
    """
    if (page_number != 1 or layout_kind not in {"icml_numbered_note", "ieee_author_relation_note"} or
            body_boundary is None or graphics is None):
        return []
    width, height = page_size
    if width <= 0 or height <= 0:
        return []
    if not byline_indexes or any(i not in range(len(lines)) for i in byline_indexes):
        return []
    if any(lines[i]["rect"][3] >= body_boundary for i in byline_indexes):
        return []
    markers = {m for i in byline_indexes for m in
               re.findall(r"(?<!\d)([1-9]\d?)(?!\d)", lines[i]["text"])}
    if not markers:
        return []
    excluded = set(excluded_indexes)
    reference_top = min((line["rect"][1] for line in lines if re.fullmatch(
        r"\s*(?:references|bibliography)[.:]?\s*", line["text"], re.I)), default=float("inf"))
    proof = {}
    for line, start, end in author_name_slices:
        proof.setdefault(id(line), []).append((start, end))
    blocks = {}
    for i, line in enumerate(lines):
        if (i not in excluded and line["rect"][1] >= max(body_boundary, height * .65) and
                line["rect"][3] <= height and line["rect"][1] < reference_top and line["size"] <= 11):
            blocks.setdefault(line["block"], []).append((i, line))
    result = []
    for items in blocks.values():
        included = {i for i, _ in items}
        if any(i not in included and line["block"] == items[0][1]["block"]
               for i, line in enumerate(lines)):
            continue
        items = _region_footer_reading_rows(items)
        block_lines = [line for _, line in items]
        left = min(x["rect"][0] for x in block_lines)
        right = max(x["rect"][2] for x in block_lines)
        if (left > width * .3 or right - left > width * .9 or
                max(x["rect"][3] for x in block_lines) - min(x["rect"][1] for x in block_lines) > height * .18 or
                max(x["size"] for x in block_lines) - min(x["size"] for x in block_lines) > 1.5 or
                any(_region_overlap(x["rect"], g) for x in block_lines for g in graphics)):
            continue
        if any(_REGION_FA_PROTECTED.search(x["text"]) for x in block_lines):
            continue
        # A separate preceding label can make a noun-only institution list
        # scientific (e.g. Study sites:). Byline numbers cannot override it.
        if any((_REGION_FA_PROTECTED.search(x["text"]) or
                x["text"].strip().endswith(":") and not re.fullmatch(
                    r"(?:Authors|Author affiliations)\s*:", x["text"].strip(), re.I)) and
               0 <= block_lines[0]["rect"][1] - x["rect"][3] < 36 and
               min(right, x["rect"][2]) > max(left, x["rect"][0]) for x in lines):
            continue
        offsets, cursor = [], 0
        for line in block_lines:
            offsets.append((line, cursor, cursor + len(line["text"])))
            cursor += len(line["text"]) + 1
        text = "\n".join(x["text"] for x in block_lines)
        name_ranges = [(left + start, left + end) for line, left, _ in offsets
                       for start, end in proof.get(id(line), [])]
        stop = _REGION_FA_CONTACT.search(text)
        end = stop.start() if stop else len(text)
        suffix = _REGION_FA_SUFFIX.search(text[:end])
        if suffix:
            end = suffix.start()
        if layout_kind == "ieee_author_relation_note":
            if right - left > width * .52:
                continue
            first = _REGION_FA_MARKER.match(text)
            relation = re.search(r"\b(?:is|are)\s+with\s+", text[:end], re.I)
            if not first or first.group(1) not in markers or not relation:
                continue
            subject = text[first.end():relation.start()]
            for start, finish in sorted(name_ranges, reverse=True):
                if first.end() <= start < finish <= relation.start():
                    subject = subject[:start-first.end()] + " "*(finish-start) + subject[finish-first.end():]
            if re.sub(r"\b(?:and)\b|[\s,.;*&†‡]+", "", subject, flags=re.I):
                continue
            start = relation.end()
            # Contact material starts at a grouped address or local-part token.
            contact = re.search(r"\{|[\w.+-]+\s*@", text[start:end])
            if contact:
                end = start + contact.start()
            if not _REGION_FA_INSTITUTION.search(text[start:end]) or not _region_footer_noun_card(text[start:end]):
                continue
        else:
            declarations = list(_REGION_FA_MARKER.finditer(text[:end]))
            if not declarations or any(m.group(1) not in markers for m in declarations):
                continue
            start = declarations[0].start()
            prefix = text[:start]
            for a, z in sorted(name_ranges, reverse=True):
                if z <= start:
                    prefix = prefix[:a] + " "*(z-a) + prefix[z:]
            if set(re.findall(r"[^\W\d_]+", prefix.casefold())) - _REGION_FA_CREDIT:
                continue
            cards = [text[m.end():declarations[j+1].start() if j+1 < len(declarations) else end]
                     for j, m in enumerate(declarations)]
            if not all(_region_footer_noun_card(card) for card in cards):
                continue
            # A bare numbered university footnote is insufficient. A roster
            # needs at least two distinct byline markers or explicit contact.
            distinct_markers = {m.group(1) for m in declarations}
            if len(distinct_markers) < 2 and (not stop or suffix):
                continue
            if right - left > width * .55 and len(distinct_markers) < 3:
                continue
            # Simple email host labels also corroborate acronym-only rosters.
            hosts = re.findall(r"@([\w.-]+)", text)
            host_tokens = {part.lower() for host in hosts for part in host.split(".") if len(part) > 2}
            words = {w.lower() for w in re.findall(r"[A-Z]{2,}", text[start:end])}
            if not (_REGION_FA_INSTITUTION.search(text[start:end]) or stop and words & host_tokens):
                continue
        while end > start and text[end-1].isspace():
            end -= 1
        for line, left, right in offsets:
            if start < right and end > left:
                a, z = max(0, start-left), min(len(line["text"]), end-left)
                if line["text"][a:z].strip():
                    result.append((line, a, z))
    return result

_REGION_CAPTION = re.compile(r'^(?:Figure|Fig[.]?|Table|Algorithm|Listing)\s*(?:\d+|[IVXLC]+)\b',re.I)

_REGION_LITERAL = re.compile(r'\b(?:example|literal|quoted|prompt|input|output|study sites|benchmark sources)\s*:',re.I)

_REGION_SCIENCE = re.compile(r'\b(?:we|our|this paper|this work)\s+(?:introduce|present|propose|study|evaluate|show|find|train|use|demonstrate)\b',re.I)

def _region_overlap(a,b):
    return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])

def _region_box(lines,padding=.7):
    return [min(x['rect'][0] for x in lines)-padding,min(x['rect'][1] for x in lines)-padding,
            max(x['rect'][2] for x in lines)+padding,max(x['rect'][3] for x in lines)+padding]

def _region_clip(box,size):
    width,height=size
    if (len(box)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in [*box,width,height]) or width<=0 or height<=0):return None
    box=[max(0,box[0]),max(0,box[1]),min(width,box[2]),min(height,box[3])]
    return box if box[0]<box[2] and box[1]<box[3] else None

def _region_graphics(graphics):
    if graphics is None:return None
    try:
        boxes=[g['rect'] if isinstance(g,dict) else g for g in graphics]
        if any(len(box)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in box) for box in boxes):return None
        return boxes
    except (TypeError,ValueError,OverflowError,KeyError):return None

def _region_protected_context(lines,index):
    line=lines[index]
    peers=[x for x in lines if (x['block']==line['block'] and x['rect'][1]<=line['rect'][1]) or
           (0<=line['rect'][1]-x['rect'][3]<=28 and
            min(line['rect'][2],x['rect'][2])>max(line['rect'][0],x['rect'][0]))]
    return any(_REGION_CAPTION.match(x['text']) or _REGION_LITERAL.search(x['text']) for x in peers)

_REGION_MARGIN_STATUS = re.compile(r'^(?:a\s+)?pre[- ]?print[.]?$', re.I)

def _region_contains(outer,inner):
    return outer[0]<=inner[0] and outer[1]<=inner[1] and outer[2]>=inner[2] and outer[3]>=inner[3]

def _region_lateral_identifier(lines,index,size,paper,graphics,api):
    """A separately removable own arXiv stamp cannot define a body cutoff."""
    line=lines[index];width,_=size;identifier=str(paper.get('arxiv_id') or '')
    side=line['rect'][2]<width*.08 or line['rect'][0]>width*.92
    if not (identifier and side and api.ARXIV.search(line['text']) and identifier in line['text']):return False
    if graphics is None or _region_protected_context(lines,index):return False
    if any(x['block']==line['block'] and _REGION_CAPTION.match(x['text']) for x in lines):return False
    box=_region_clip(_region_box([line]),size)
    return box is not None and not any(_region_overlap(box,g) and g[3]-g[1]>max(18,line['size']*2) for g in graphics)

def _region_journal_title_indexes(first,sizes,paper,candidate_end,names,api):
    """Compact journal front matter with a bold title and labelled run-in abstract."""
    provenance=api._select_layout_profile(paper,[first],[sizes]).get('provenance_family')
    if provenance is None:return set()
    width,height=sizes
    abstract=[x for x in first if x['rect'][1]==candidate_end and
              re.match(r'^Abstract[.:]\s+\S',x['text'],re.I)]
    if not abstract:return set()
    lead=next((x for x in abstract[0].get('spans',[]) if x['text'].strip()),None)
    if not lead or not lead.get('flags',0)&16 or not re.fullmatch(r'Abstract[.:]?',lead['text'].strip(),re.I):return set()
    eligible=[i for i,x in enumerate(first) if x['rect'][3]<candidate_end and
              not (api.VENUE.search(x['text']) or api.ARXIV.search(x['text']) or api.EMAIL.search(x['text']))]
    if not eligible:return set()
    index=eligible[0];title=first[index]
    if (not title.get('primary_styles') or any(not flag&16 for flag in title['primary_styles']) or
        _region_protected_context(first,index) or api._body_start(title['text']) or
        not 4<=len(title['text'].split())<=30 or title['rect'][1]>height*.2 or
        abs((title['rect'][0]+title['rect'][2])/2-width/2)>width*.06):return set()
    bylines=[first[i] for i in eligible[1:] if
             0<first[i]['rect'][1]-title['rect'][3]<90 and
             api._name_shape(first[i]['text']) and re.search(r'[,\d*†‡]',first[i]['text']) and
             any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',api._normal(first[i]['text'])) for name in names)]
    if (not bylines or title['size']<max(x['size'] for x in bylines)+.5 or
        any(x.get('primary_styles')==title.get('primary_styles') for x in bylines)):
        return set()
    # No unrelated text may lie between the proposed title and first byline.
    first_byline=min(x['rect'][1] for x in bylines)
    if any(first[i] is not title and first[i]['rect'][1]<first_byline for i in eligible):return set()
    return {index}

def _region_padded_slice_box(selected,others,padding=.35):
    """Pad an exact source slice only through empty glyph space."""
    if not selected:return None
    box=[min(b[0] for b in selected),min(b[1] for b in selected),max(b[2] for b in selected),max(b[3] for b in selected)]
    if box[0]>=box[2] or box[1]>=box[3]:return None
    def touches(a,b):
        def span(lo,hi,left,right):
            return left<=lo<=right if lo==hi else max(lo,left)<min(hi,right)
        return span(b[0],b[2],a[0],a[2]) and span(b[1],b[3],a[1],a[3])
    if any(touches(box,b) for b in others):return None
    padded=[box[0]-padding,box[1]-padding,box[2]+padding,box[3]+padding]
    for b in others:
        if min(padded[3],b[3])>max(padded[1],b[1]):
            if b[2]<=box[0]:padded[0]=max(padded[0],b[2])
            if b[0]>=box[2]:padded[2]=min(padded[2],b[0])
        if min(padded[2],b[2])>max(padded[0],b[0]):
            if b[3]<=box[1]:padded[1]=max(padded[1],b[3])
            if b[1]>=box[3]:padded[3]=min(padded[3],b[1])
    return box if any(touches(padded,b) for b in others) else padded


def _region_full_note_blocks(lines,affiliation_slices,names,markers,size,graphics,api):
    """Join only complete proved institution/contact rows of one footer block."""
    if graphics is None:return [],set()
    full_affiliations={id(line) for line,a,z in affiliation_slices if a==0 and z==len(line['text'])}
    regions=[];absorbed=set()
    for block in {x['block'] for x in lines if id(x) in full_affiliations}:
        members=[(i,line) for i,line in enumerate(lines) if line['block']==block]
        if len(members)<2:continue
        contacts={i for i,line in members if re.match(
            r'^[\s\d*∗†‡§¶.,]*(?:corresponding authors?\s*:|correspondence\s*(?:to|:))',line['text'],re.I)
            and api._identity_footnote(line['text'],names,markers)}
        if not contacts or any(id(line) not in full_affiliations and i not in contacts for i,line in members):continue
        if any(_region_protected_context(lines,i) for i,_ in members):continue
        box=_region_clip(_region_box([line for _,line in members],.35),size)
        if box is None or any(_region_overlap(box,g) for g in graphics):continue
        indexes={i for i,_ in members}
        if any(i not in indexes and _region_overlap(box,line['rect']) for i,line in enumerate(lines)):continue
        regions.append({'page':1,'rect':box,'target':'footer','category':'other_identity',
                        'source_line_evidence':sorted(indexes),'boundary_evidence':'complete_same_block_author_affiliation_and_contact_note'})
        absorbed.update(indexes)
    return regions,absorbed

def _region_source_footer_notice(lines,index,size,paper,api):
    line=lines[index];width,height=size
    if line['rect'][3]<=height*.9 or line['size']>11:return False
    provenance=api._select_layout_profile(paper,[],[])
    if provenance.get('provenance_family')!='neurips':return False
    years={m.group(1) for source in provenance['source_ids'] if (m:=re.match(r'^neurips[_-]?(\d{4})',source))}
    match=re.fullmatch(r'\d+(?:st|nd|rd|th) Conference on Neural Information Processing Systems\s*\(NeurIPS\s*(\d{4})\)[.]?',line['text'].strip(),re.I)
    if not match or match.group(1) not in years:return False
    if any(other is not line and other['block']==line['block'] for other in lines):return False
    above=[other for other in lines if other is not line and other['rect'][3]<=line['rect'][1] and
           min(line['rect'][2],other['rect'][2])>max(line['rect'][0],other['rect'][0])]
    return bool(above and line['rect'][1]-max(other['rect'][3] for other in above)>=8)


def _region_bibliographic_publication(lines,index):
    """A venue continuation inside a numbered reference is not page furniture."""
    line=lines[index];top=line['rect'][1];bottom=line['rect'][3]
    def anchor(other):
        return re.match(r'^\s*\[(\d{1,4})\]\s*\S',other['text'])
    def same_column(other):
        return min(other['rect'][2],line['rect'][2])>max(other['rect'][0],line['rect'][0])
    # Author-year styles can lack brackets. Pagination plus a source title's
    # explicit 'In ...' continuation supplies independent citation evidence.
    if (re.search(r'\b(?:pages|pp?[.])\s+\d+',line['text'],re.I) and
            re.search(r'\b(?:19|20)\d{2}\b',line['text'])):
        peers=[other for other in lines if other is not line and other['block']==line['block'] and
               same_column(other) and other['rect'][1]<top and
               -max(1,line['size']*.15)<=top-other['rect'][3]<=max(24,line['size']*3)]
        if any(re.search(r'\bIn\s+\S',other['text']) and
               not re.match(r'^(?:Published|Proceedings|Copyright|©)\b',other['text'],re.I) for other in peers):return True
    before=[other for other in lines if other is not line and anchor(other) and same_column(other) and
            other['rect'][1]<top and 0<=top-other['rect'][3]<=max(36,line['size']*6)]
    if any(other['block']==line['block'] for other in before):return True
    # Some PDFs split one reference into separate source blocks. Consecutively
    # numbered neighboring entries independently establish the local list.
    after=[other for other in lines if other is not line and anchor(other) and same_column(other) and
           other['rect'][1]>=bottom and other['rect'][1]-bottom<=max(24,line['size']*3)]
    return any(int(anchor(right).group(1))==int(anchor(left).group(1))+1 for left in before for right in after)

def _region_primary_structure(pages,sizes,paper,api):
    first=pages[0];width,height=sizes[0];names=api._author_names(paper)
    metadata_title=api._normal(paper.get('title') or '')
    preliminary=[x['rect'][1] for i,x in enumerate(first) if api._body_start(x['text']) and
                 api._normal(x['text'])!=metadata_title and not _region_protected_context(first,i) and
                 not (len(api._normal(x['text']).split())>=3 and api._normal(x['text']) in metadata_title)]
    candidate_end=min(preliminary) if preliminary else height*.4
    candidates=[x for x in first if x['rect'][3]<=candidate_end]
    selected=api._title_indexes(candidates,api._normal(paper.get('title') or ''))
    if not selected:
        journal_titles=_region_journal_title_indexes(first,sizes[0],paper,candidate_end,names,api)
        title_objects={id(first[i]) for i in journal_titles}
        selected={i for i,x in enumerate(candidates) if id(x) in title_objects}
    title_ids={id(candidates[i]) for i in selected}
    titles={i for i,x in enumerate(first) if id(x) in title_ids}
    if not titles:return {'title_indexes':[],'author_indexes':[],'body_boundary':None,'reason':'primary_title_unresolved'}
    top=min(first[i]['rect'][1] for i in titles);end=max(first[i]['rect'][3] for i in titles)
    aliases=api._header_author_names(first,names)
    author_indexes={i for i,x in enumerate(first) if end<=x['rect'][1]<min(height*.7,end+200) and
                    (api._byline_only(x['text'],aliases) or api._byline_with_affiliation(x['text'],aliases) or
                     api._name_shape(x['text']) and re.search(r'[,\d*†‡]',x['text']) and
                     any(name in api._normal(x['text']) for name in names))}
    exact=bool(api._matched_lines(first,[api._normal(paper.get('title') or '')])&titles)
    if not exact and not author_indexes:
        return {'title_indexes':sorted(titles),'author_indexes':[],'body_boundary':None,'reason':'structural_title_lacks_byline_support'}
    starts=[x['rect'][1] for i,x in enumerate(first) if i not in titles and x['rect'][1]>=end and
            api._body_start(x['text']) and not _region_protected_context(first,i)]
    if starts:boundary=min(starts);reason='explicit_abstract_or_introduction'
    else:
        boundary=None;reason='body_boundary_unresolved'
        if author_indexes:
            start=min(first[i]['rect'][1] for i in author_indexes)
            for i,x in enumerate(first):
                text=x['text'];words=re.findall(r'[^\W\d_]+',text)
                if x['rect'][1]<=start or i in author_indexes or x['rect'][1]>height*.9:continue
                if _REGION_CAPTION.match(text):boundary=x['rect'][1];reason='scientific_caption';break
                affiliation=bool(api.AFFILIATION.search(api._normal(text)) and len(words)<=30 and not re.search(
                    r'\b(?:we|our|this|is|are|has|have|was|were|can|present|propose|show|models?|datasets?|benchmark|appeared|appears|released|uses?|using|developed)\b',text,re.I))
                note=bool(api._identity_footnote(text,names) and x['size']<=11)
                if affiliation or note or api.EMAIL.search(text) or api.VENUE.search(text):continue
                caps=sum(w[0].isupper() for w in words)/max(1,len(words))
                block=[y for y in first if y['block']==x['block'] and y['rect'][1]>=x['rect'][1]]
                prose=(len(words)>=8 and caps<.7 or len(words)>=5 and caps<.6 and
                       len(' '.join(y['text'] for y in block).split())>=18)
                if _REGION_SCIENCE.search(text) or prose:
                    boundary=x['rect'][1];reason='unlabelled_scientific_prose';break
        if boundary is None and len(pages)>1:
            following=[x for x in pages[1] if not(api.VENUE.search(x['text']) or api.ARXIV.search(x['text']) or
                       re.fullmatch(r'\d{1,3}',x['text'].strip()))]
            if (following and api._body_start(following[0]['text']) and len(author_indexes)>=2 and
                    any(api.AFFILIATION.search(api._normal(x['text'])) for x in first)):
                boundary=height;reason='frontmatter_page_before_next_page_abstract'
    return {'title_indexes':sorted(titles),'author_indexes':sorted(author_indexes),
            'body_boundary':boundary,'title_top':top,'title_end':end,'title_exact':exact,'reason':reason}

def _propose_frontmatter_and_margins(pages,sizes,*,paper,graphics_by_page,rules_module,slice_boxes=None,footer_name_slices=None,footer_affiliation_slices=None):
    api=rules_module;regions=[];unresolved=[]
    try:
        valid=(len(pages)==len(sizes) and all(len(size)==2 and all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in size) for size in sizes) and
               all(len(line['rect'])==4 and all(type(v) in (int,float) and math.isfinite(v) for v in [*line['rect'],line['size']]) for page in pages for line in page))
    except (TypeError,ValueError,OverflowError,KeyError):valid=False
    if not valid:return {'regions':[],'unresolved':['Invalid source layout geometry.'],'structure':{}}
    if not pages or not pages[0]:return {'regions':[],'unresolved':['First page has no extractable layout.'],'structure':{}}
    first=pages[0];width,height=sizes[0];structure=_region_primary_structure(pages,sizes,paper,api)
    names=api._author_names(paper);graphics=_region_graphics(graphics_by_page.get(1))
    titles=structure.get('title_indexes',[]);authors=structure.get('author_indexes',[])
    body=structure.get('body_boundary');title_box=None
    lateral_identifiers={i for i in range(len(first)) if _region_lateral_identifier(first,i,sizes[0],paper,graphics,api)}
    if not titles or body is None:
        unresolved.append('First-page title area has no confirmed title-to-body boundary.')
    elif graphics is None:
        unresolved.append('First-page graphic bounds are unresolved; broad title region preserved.')
    else:
        top=structure['title_top'];end=structure['title_end'];upper=0;lower=body-.8
        captions=[x for x in first if _REGION_CAPTION.match(x['text'])]
        above=[x for x in captions if x['rect'][3]<=top]
        if above:upper=max(x['rect'][3] for x in above)+.8
        identity_bottom=max([end]+[first[i]['rect'][3] for i in authors])
        for row in first:
            if row['rect'][1]<identity_bottom or row['rect'][1]>=body:continue
            if row['rect'][1]-identity_bottom>24:break
            if (api.AFFILIATION.search(api._normal(row['text'])) or api.EMAIL.search(row['text']) or
                re.match(r'^(?:code|data code|data and code|project|models?)\b',api._normal(row['text']))):
                identity_bottom=max(identity_bottom,row['rect'][3])
            else:break
        after=[x for x in captions if end<=x['rect'][1]<body]
        for caption in after:
            lower=min(lower,caption['rect'][1]-.8)
            for graphic in graphics:
                if (graphic[1]>=identity_bottom and graphic[3]<=caption['rect'][1] and
                    min(graphic[2],caption['rect'][2])>max(graphic[0],caption['rect'][0])):
                    lower=min(lower,graphic[1]-.8)
        for graphic in graphics:
            large=(graphic[2]-graphic[0]>=width*.2 and graphic[3]-graphic[1]>=18 or
                   (graphic[2]-graphic[0])*(graphic[3]-graphic[1])>=width*height*.015)
            if large and end<=graphic[1]<lower:lower=graphic[1]-.8
        # Repeat because lowering past a later tall row may cut an earlier row.
        # Independently proved lateral stamps get their own complete mask below.
        while True:
            crossings=[line['rect'][1]-.5 for i,line in enumerate(first)
                       if i not in titles and i not in lateral_identifiers and line['rect'][1]<lower<line['rect'][3]]
            if not crossings:break
            lower=min(crossings)
        if lower>=end+.2 and upper<top:
            title_box=_region_clip([0,upper,width,lower],sizes[0])
            regions.append({'page':1,'rect':title_box,'target':'title_block','category':'other_identity',
                'source_line_evidence':[i for i,x in enumerate(first) if _region_overlap(x['rect'],title_box)],
                'boundary_evidence':structure['reason']})
            if any(first[i]['rect'][3]>lower for i in authors):
                unresolved.append('Scientific teaser boundary leaves some confirmed byline rows outside the title region.')
        else:unresolved.append('Primary title touches a protected body/graphic boundary; no broad title mask.')
    # Reuse actual recurring source header evidence; never search all body names.
    author_headers=api._source_author_headers(pages,sizes,names)
    status_headers=api._source_status_headers(pages,sizes,paper.get('title') or '')
    primary=' '.join(api._normal(first[i]['text']) for i in titles)
    repeated={}
    for number,lines in enumerate(pages,1):
        w,h=sizes[number-1]
        for i,line in enumerate(lines):
            value=api._normal(line['text'])
            if line['rect'][1]<h*.13 or line['rect'][3]>h*.9:
                repeated.setdefault(value,[]).append((number,i,line['rect'][0]/w,line['rect'][1]/h))
    for number,lines in enumerate(pages,1):
        w,h=sizes[number-1];page_graphics=_region_graphics(graphics_by_page.get(number))
        for i,line in enumerate(lines):
            if number==1 and title_box and _region_contains(title_box,line['rect']):continue
            text=line['text'];value=api._normal(text);top=line['rect'][1]<h*.13;bottom=line['rect'][3]>h*.9
            if _region_protected_context(lines,i) or any(x['block']==line['block'] and _REGION_CAPTION.match(x['text']) for x in lines):continue
            own_arxiv=_region_lateral_identifier(lines,i,sizes[number-1],paper,page_graphics,api)
            recurrence=any(n!=number and abs(x-line['rect'][0]/w)<.035 and abs(y-line['rect'][1]/h)<.012
                           for n,_,x,y in repeated.get(value,[]))
            below=[x for x in lines if x is not line and x['rect'][1]>=line['rect'][3] and not re.fullmatch(r'\d{1,3}',x['text'].strip())]
            separated=bool(below and min(x['rect'][1] for x in below)-line['rect'][3]>=5)
            title_repeat=bool(primary and len(value.split())>=3 and value in primary and recurrence and top and line['size']<=12 and separated)
            margin_status=bool(_REGION_MARGIN_STATUS.fullmatch(text.strip()) and recurrence and (top or bottom) and line['size']<=11 and (bottom or separated))
            recognized=((number,i) in author_headers or (number,i) in status_headers or title_repeat or own_arxiv or margin_status)
            publication=bool((api.VENUE.search(text) or number==1 and _region_source_footer_notice(lines,i,sizes[0],paper,api)) and not api.ARXIV.search(text) and
                             not _region_bibliographic_publication(lines,i) and (top or bottom) and line['size']<=11 and
                             not re.match(r'^Copyright and privacy\b',text,re.I))
            if not(recognized or publication):continue
            if not(top or bottom or own_arxiv):continue
            box=_region_clip(_region_box([line]),sizes[number-1])
            if page_graphics is None:
                unresolved.append(f'Page {number}: margin identity has unknown graphic bounds.');continue
            if any(_region_overlap(box,g) and g[3]-g[1]>max(18,line['size']*2) for g in page_graphics):
                unresolved.append(f'Page {number}: margin identity overlaps a graphic; preserved.');continue
            regions.append({'page':number,'rect':box,'target':'header' if top else 'footer',
                            'category':'identifier' if own_arxiv else 'venue','source_line_evidence':[i],
                            'boundary_evidence':'source_running_header_or_margin_notice'})
    # First-page notes remain column-local. Only proven author-note prefixes are
    # eligible; ordinary scientific footnotes and mixed suffixes stay outside.
    markers=set(re.findall(r'[\d*∗†‡§¶♠♣♥♦◊⋄♯]+',' '.join(first[i]['text'] for i in authors)))
    contacts=api._own_contact_slices(first,width,height,names,markers)
    slices=[(line,start,end) for line,start,end,_proof in contacts]
    own_names=[];affiliation_slices=[]
    if footer_name_slices is not None:
        own_names=footer_name_slices(first,names,page_size=sizes[0],body_boundary=body)
        slices.extend(own_names)
    if footer_affiliation_slices is not None:
        for layout_kind in ('icml_numbered_note','ieee_author_relation_note'):
            additions=footer_affiliation_slices(first,byline_indexes=authors,body_boundary=body,
                page_size=sizes[0],graphics=graphics,layout_kind=layout_kind,author_name_slices=own_names)
            slices.extend(additions);affiliation_slices.extend(additions)
    for i,line in enumerate(first):
        if body is None or line['rect'][1]<max(body,height*.6) or line['size']>11:continue
        if _region_protected_context(first,i):continue
        if api._identity_footnote(api._identity_note_context(first,i),names,markers):
            end=api._note_prefix_end(line['text']);slices.append((line,0,end))
    complete_notes,absorbed=_region_full_note_blocks(first,affiliation_slices,names,markers,sizes[0],graphics,api)
    regions.extend(complete_notes)
    seen=set()
    for line,start,end in slices:
        i=next(i for i,x in enumerate(first) if x is line)
        if i in absorbed:continue
        if _region_protected_context(first,i):continue
        if start==0 and end==len(line['text']):boxes=[line['rect']]
        elif slice_boxes is not None:
            boxes=slice_boxes(1,line,start,end)
            if not boxes:unresolved.append('A mixed first-page identity note has unresolved source glyph geometry.')
        else:
            unresolved.append('A mixed first-page identity note needs exact source slice geometry.');continue
        for raw in boxes:
            pad=.35 if start==0 and end==len(line['text']) else 0
            box=_region_clip([raw[0]-pad,raw[1]-pad,raw[2]+pad,raw[3]+pad],sizes[0])
            if box is None or tuple(box) in seen:continue
            if graphics is None or any(_region_overlap(box,g) and g[3]-g[1]>max(18,line['size']*2) for g in graphics):
                unresolved.append('A first-page identity note overlaps unknown/protected graphics.');continue
            if any(j!=i and _region_overlap(box,other['rect']) for j,other in enumerate(first)):
                unresolved.append('A first-page identity note overlaps another source text row.');continue
            seen.add(tuple(box));regions.append({'page':1,'rect':box,'target':'footer','category':'contact',
                'source_line_evidence':[i],'boundary_evidence':'source_author_identity_note'})
    from types import SimpleNamespace
    notices=api._adjacent_publication_notices(first,SimpleNamespace(width=width,height=height),
        [{'page':x['page'],'rect':x['rect'],'category':x['category'],'text':''} for x in regions],names)
    for line in notices:
        index=next(i for i,x in enumerate(first) if x is line)
        box=_region_clip(_region_box([line],.35),sizes[0])
        if (_region_protected_context(first,index) or _region_bibliographic_publication(first,index) or api.ARXIV.search(line['text']) or graphics is None or
                any(_region_overlap(box,g) and g[3]-g[1]>max(18,line['size']*2) for g in graphics)):continue
        regions.append({'page':1,'rect':box,'target':'footer','category':'venue',
                        'source_line_evidence':[index],'boundary_evidence':'source_publication_note'})
    return {'regions':regions,'unresolved':list(dict.fromkeys(unresolved)),'structure':structure}

def _region_page_profiles(pages,sizes,graphics_by_page,source_family=None):
    """Describe each page's own prose columns; source family is only a prior."""
    profiles={}
    for number,lines in enumerate(pages,1):
        width,height=sizes[number-1];graphics=_region_graphics(graphics_by_page.get(number))
        captions={}
        for x in lines:
            if _REGION_CAPTION.match(x['text']) or _REGION_LITERAL.search(x['text']):
                captions[x['block']]=min(captions.get(x['block'],float('inf')),x['rect'][1])
        protected=[{'rect':_region_box([x for x in lines if x['block']==block and x['rect'][1]>=top],0),'kind':'caption_or_literal'} for block,top in captions.items()]
        protected += [] if graphics is None else [{'rect':x,'kind':'graphic'} for x in graphics]
        prose=[x for x in lines if len(x['text'].split())>=5 and 7<=x['size']<=14 and
               height*.04<x['rect'][1]<height*.93 and not (x['block'] in captions and x['rect'][1]>=captions[x['block']]) and
               not any(_region_overlap(x['rect'],y['rect']) for y in protected)]
        weighted=Counter()
        for x in prose:weighted[round(x['size']*2)/2]+=len(x['text'])
        body_font=weighted.most_common(1)[0][0] if weighted else None
        prose=[x for x in prose if body_font is not None and abs(x['size']-body_font)<=1.5]
        left=[x for x in prose if x['rect'][2]<width*.58];right=[x for x in prose if x['rect'][0]>width*.42]
        wide=[x for x in prose if x['rect'][2]-x['rect'][0]>width*.55]
        if len(left)>=3 and len(right)>=3 and len(wide)<=max(2,len(prose)*.2):groups=[left,right]
        elif len(wide)>=2:groups=[prose]
        else:groups=[]
        bands=[[min(x['rect'][0] for x in group),max(x['rect'][2] for x in group)] for group in groups]
        profiles[number]={'confirmed':bool(bands) and graphics is not None,'source_family':source_family,
                          'column_bands':bands,'body_font':body_font,'protected_regions':protected,
                          'layout_evidence':{'prose_lines':len(prose),'column_count':len(bands)}}
    return profiles

_REGION_CREDIT_CAPTION = re.compile(r"^\s*(?:Figure|Fig[.]|Table)\s+[A-Z]?\d+\b", re.I)

_REGION_CREDIT_LITERAL = re.compile(
    r"\b(?:preprocessing|normalization|system|user|example)\s+prompt\b|"
    r"\b(?:quoted|generated|synthetic)\s+(?:example|statement|caption|stimulus)\b|"
    r"\b(?:model|llm|system)\s+(?:generated|produced|returned)\s+"
    r"(?:the\s+)?(?:following\s+)?(?:statement|sentence|text|response|answer|caption)\b", re.I)

_REGION_CREDIT_EXTRA_END = re.compile(
    r"^(?:(?:[A-Z]|\d+(?:[.]\d+)*)[.)]?\s+)?"
    r"(?:references|bibliography|appendix|appendices|additional information|"
    r"reproducibility statement|impact statement|ethics statement|author contributions)"
    r"(?:\s*[:.]|\s+[A-Z0-9][.:]?)?$", re.I)

_REGION_CREDIT_RUN_IN = re.compile(
    r"^\s*(?:(?:[A-Z]|\d+)[.)]?\s+)?"
    r"(?:acknowledg(?:e)?ments?|author contributions|"
    r"credit authorship contribution statement)\b", re.I)

def _region_credit_envelope(rows):
    return [min(x["rect"][0] for x in rows), min(x["rect"][1] for x in rows),
            max(x["rect"][2] for x in rows), max(x["rect"][3] for x in rows)]

def _region_credit_protected(rect, profile):
    return any(_region_overlap(rect, p["rect"]) for p in profile.get("protected_regions", []))

def _region_credit_rows(lines, rules):
    """Join connected fragments of one original block/baseline for semantics."""
    anchors = rules._column_lefts(lines)
    output = []
    for index, line in enumerate(lines):
        ref = line.get("source_index", index)
        group = next((g for g in output
                      if g["block"] == line["block"]
                      and abs(g["anchor"] - anchors[id(line)]) <= .1
                      and abs(g["rect"][3] - line["rect"][3]) <= max(1, line["size"] * .25)), None)
        evidence = {"index": ref, "text": line["text"], "rect": list(line["rect"]),
                    "block": line["block"], "size": line["size"]}
        if group is None:
            output.append({**line, "rect": list(line["rect"]), "anchor": anchors[id(line)],
                           "members": [evidence],
                           "styled_heading": bool(line.get("styled_heading"))})
        else:
            group["members"].append(evidence)
            group["members"].sort(key=lambda x: x["rect"][0])
            group["text"] = " ".join(x["text"] for x in group["members"])
            group["rect"] = _region_credit_envelope(group["members"])
            group["size"] = max(group["size"], line["size"])
            group["styled_heading"] = group["styled_heading"] and bool(line.get("styled_heading"))
    return output

def _region_credit_column(row, profile):
    slack = min(2, max(.5, row["size"] * .15))
    matches = [i for i, (left, right) in enumerate(profile.get("column_bands", []))
               if left - slack <= row["rect"][0] and row["rect"][2] <= right + slack]
    return matches[0] if len(matches) == 1 else None

def _region_credit_kind(row, rules):
    value = rules._identity_section_kind(row["text"])
    if value:
        return value
    if re.fullmatch(r"\s*(?:(?:[A-Z]|\d+)[.)]?\s+)?Acknowledge\s*[:.]?\s*", row["text"], re.I):
        return "acknowledgment"
    if _REGION_CREDIT_RUN_IN.match(row["text"]):
        return "other_identity" if re.search(r"author|credit", row["text"], re.I) else "acknowledgment"
    return None

def _region_credit_standalone(row, rules):
    value = rules._normal(row["text"])
    value = re.sub(r"^(?:[a-z]|\d+)\s+", "", value)
    return (value in {"acknowledge", "acknowledgment", "acknowledgments",
                      "acknowledgement", "acknowledgements", "author contributions",
                      "authors contributions", "author contribution",
                      "credit authorship contribution statement"}
            or bool(re.fullmatch(r"acknowledg(?:e)?ments? and disclosure of funding", value)))

def _region_credit_boundary(row, rules):
    return bool(_REGION_CREDIT_CAPTION.match(row["text"]) or _REGION_CREDIT_EXTRA_END.fullmatch(row["text"].strip())
                or rules._section_heading(row))

def _region_credit_folio(row):
    return bool(re.fullmatch(r"\s*\d{1,3}\s*", row["text"]))

def _region_credit_figure_page_boundary(page, rows, profiles):
    profile = profiles.get(page, {})
    if not profile.get("confirmed") or not any(_REGION_CREDIT_CAPTION.match(r["text"]) for r in rows):
        return False
    ordinary = [r for r in rows if not _region_credit_folio(r) and not _REGION_CREDIT_CAPTION.match(r["text"])
                and not _region_credit_protected(r["rect"], profile)]
    return not ordinary and bool(profile.get("protected_regions"))

def _region_credit_run_in_style(line):
    spans=[s for s in line.get('spans',[]) if s['text'].strip()]
    if not spans:return False
    label=r'(?:(?:[A-Z]|\d+)[.)]?\s+)?(?:acknowledg(?:e)?ments?|author(?:s)? contributions?|credit authorship contribution statement)'
    # Preserve existing punctuated-label evidence, then admit an exact separate
    # emphasized label followed by regular source prose without punctuation.
    if re.match(r'^'+label+r'\s*[.:]\s+',line['text'],re.I):return bool(spans[0]['flags']&18)
    return bool(re.fullmatch(label+r'[.:]?',spans[0]['text'].strip(),re.I) and
                spans[0]['flags']&18 and re.match(r'^'+label+r'\s+\S',line['text'],re.I) and
                any(not s['flags']&18 for s in spans[1:]))

def _region_credit_next_page_references(page,column,selected,normalized,profiles,rules):
    current=profiles.get(page,{});following=profiles.get(page+1,{})
    if (not current.get('confirmed') or not following.get('confirmed') or
        len(current.get('column_bands',[])) not in (1,2) or
        len(following.get('column_bands',[]))!=len(current['column_bands']) or column!=len(current['column_bands'])-1 or
        not selected or not re.search(r'[.!?]\s*$',selected[-1]['text'])):return None
    def furniture(row,profile):
        return _region_credit_folio(row) or any(x.get('kind')=='running_margin' and
            _region_contains(x['rect'],row['rect']) for x in profile.get('protected_regions',[]))
    last=selected[-1]
    # Rows outside the recognized column must not hide an intervening item.
    if any(row not in selected and not furniture(row,current) and
           row['rect'][1]>=last['rect'][3] and _region_credit_column(row,current) in (None,column)
           for row in normalized.get(page,[])):return None
    right_left,right_right=current['column_bands'][column]
    if any(x.get('kind')!='running_margin' and x['rect'][1]>=last['rect'][3] and
           min(right_right,x['rect'][2])>max(right_left,x['rect'][0])
           for x in current.get('protected_regions',[])):return None
    rows=[row for row in normalized.get(page+1,[]) if not furniture(row,following)]
    left=[row for row in rows if _region_credit_column(row,following)==0]
    if not left:return None
    first=min(left,key=lambda row:(row['rect'][1],row['rect'][0]))
    if not rules._reference_heading(first['text']) or not first.get('styled_heading'):return None
    if any(row['rect'][1]<first['rect'][1]-.5 for row in rows):return None
    if any(x.get('kind')!='running_margin' and x['rect'][1]<first['rect'][3]
           for x in following.get('protected_regions',[])):return None
    return {'kind':'next_page_references_without_continuation','page':page+1,
            'text':first['text'],'rect':first['rect']}

def _region_numbered_resource_ack(text,heading,end,rows,profile,rules):
    """Recognize resource attribution inside a proven numbered credit section."""
    if profile.get('source_family') not in {x[0] for x in SOURCE_LAYOUT_FAMILIES}:return False
    number=re.fullmatch(r'(\d+)[.]?\s+acknowledg(?:e)?ments?[.]?',heading['text'].strip(),re.I)
    if not number or end.get('kind')!='next_explicit_heading_or_caption' or not rules._reference_heading(end.get('text','')):return False
    # The sentence must explicitly attribute this research to an access-governed
    # resource; scientific support-by-evidence and anonymous rubric labels fail.
    flat=rules._flat(text)
    if not re.match(r'^(?:This|Our) research (?:has been|was) conducted using\b',flat,re.I):return False
    if not re.search(r'\bResource under Application (?:Number|No[.]?)\s+[A-Z0-9-]*\d[A-Z0-9-]*\b',flat,re.I):return False
    column=_region_credit_column(heading,profile)
    candidates=[r for r in rows if r['rect'][3]<heading['rect'][1] and _region_credit_column(r,profile)==column and
                re.fullmatch(r'(\d+)[.]?\s+(?:conclusions?|discussion)',r['text'].strip(),re.I)]
    if not candidates:return False
    prior=max(candidates,key=lambda r:r['rect'][1])
    if int(re.match(r'\d+',prior['text']).group())+1!=int(number.group(1)):return False
    following=next((r for r in rows if r['text']==end['text'] and r['rect']==end['rect']),None)
    if following is None:return False
    triple=[prior,heading,following]
    return bool(all(r.get('styled_heading') and r.get('primary_fonts') for r in triple) and
                len({tuple(r['primary_fonts']) for r in triple})==1 and
                max(r['size'] for r in triple)-min(r['size'] for r in triple)<.25 and
                max(r['rect'][0] for r in triple)-min(r['rect'][0] for r in triple)<2)


def _region_inline_italic_continuation(row,previous):
    """Inline project-name italics do not create a new section in one paragraph."""
    if (row['block']!=previous['block'] or abs(row['size']-previous['size'])>.5 or
        not -1<=row['rect'][1]-previous['rect'][3]<=max(5,row['size']*.5) or
        re.search(r'[.!?]\s*$',previous['text'])):return False
    spans=[s for s in row.get('spans',[]) if s['text'].strip()]
    prior=[s for s in previous.get('spans',[]) if s['text'].strip()]
    if not spans or not prior:return False
    styles={s['flags']&18 for s in spans}
    if styles!={0,2}:return False
    if not spans[0]['flags']&18:return True
    # An italic phrase may wrap across a line. Require its actual source font
    # run to continue; a new bold/italic heading cannot borrow this exception.
    return bool(prior[-1]['flags']&18==2 and
                prior[-1]['font']==spans[0]['font'] and
                abs(prior[-1]['size']-spans[0]['size'])<.5)

def _propose_credit_regions(pages, page_sizes, *, names, source_profiles, rules_module):
    """Return candidate envelopes and evidence; unknown boundaries abstain.

    pages: {1: [source line dicts...]}; page_sizes: {1: (width,height)}.
    profiles: confirmed, source_family, column_bands, body_font,
      protected_regions[{rect,kind}], layout_evidence.
    Optional layout_evidence.run_in_heading_rows verifies actual leading label
    styling. Optional page1 body_start_y excludes confirmed front matter.
    """
    rules = rules_module
    normalized = {p: _region_credit_rows(lines, rules) for p, lines in pages.items()}
    result = {"regions": [], "unresolved": [], "rejected": []}
    used = set()

    def record(bucket, p, row, reason):
        result[bucket].append({"page": p, "heading": row["text"],
                               "source_line_indices": [x["index"] for x in row["members"]],
                               "reason": reason})

    def emit(selected, kind, heading_page, heading, end_evidence):
        # The heading proves layout, not credit semantics. Strip its label from
        # a copied semantic view; preserve every original row for geometry.
        body = []
        for p, row in selected:
            if p == heading_page and row["members"] == heading["members"]:
                if _region_credit_standalone(heading, rules):
                    continue
                label = _REGION_CREDIT_RUN_IN.match(row["text"])
                text = row["text"][label.end():].lstrip(" :.\t") if label else ""
                if text:
                    body.append((p, {**row, "text": text}))
            else:
                body.append((p, row))
        text = "\n".join(row["text"] for _, row in body)
        if _REGION_CREDIT_LITERAL.search(text):
            record("rejected", heading_page, heading, "Generated/literal prompt content is not a credit section.")
            return False
        if kind == "acknowledgment":
            positive = rules._credit_text(text, kind, names, allow_generic=True) or _region_numbered_resource_ack(
                text,heading,end_evidence,normalized[heading_page],source_profiles[heading_page],rules)
        else:
            # Preserve the frozen name-slot proof, including ambiguous author
            # initials. Never infer a contributor from a bare role label.
            page_bodies = {}
            for p, row in body:
                page_bodies.setdefault(p, []).append(row)
            positive = bool(page_bodies) and all(
                rules._contribution_name_ranges(rows, names, heading="Author Contributions",
                                                rules_module=rules)["ranges"]
                for rows in page_bodies.values())
        if not positive:
            record("unresolved", heading_page, heading, "No positive credit or contributor-role proof in the section body.")
            return False
        # Keep every page/column separate. Never bound through a gutter or float.
        grouped = {}
        for p, row in selected:
            profile = source_profiles.get(p, {})
            column = _region_credit_column(row, profile)
            if not profile.get("confirmed") or column is None:
                record("unresolved", heading_page, heading, "Unknown continuation layout.")
                return False
            grouped.setdefault((p, column), []).append(row)
        pending = []
        for (p, col), chosen in grouped.items():
            profile = source_profiles[p]
            rect = _region_credit_envelope(chosen)
            if _region_credit_protected(rect, profile):
                record("unresolved", heading_page, heading, "Credit envelope intersects protected graphics/container.")
                return False
            outside = [r for r in normalized[p] if r not in chosen and _region_overlap(rect, r["rect"])]
            if outside:
                record("unresolved", heading_page, heading, "Credit envelope includes unselected source text.")
                return False
            if rules._mixed_acknowledgment_slices(chosen, names) is not None:
                record("unresolved", heading_page, heading, "Mixed procedural/payment/scientific content needs narrower geometry.")
                return False
            pending.append({"page": p, "rect": rect,
                            "target": "contribution" if kind == "other_identity" else kind,
                            "column_index": col, "source_family": profile.get("source_family"),
                            "heading_page": heading_page, "heading": heading["text"],
                            "source_line_evidence": [m for r in chosen for m in r["members"]],
                            "end_evidence": end_evidence,
                            "geometry_status": "unpadded_source_envelope_pending_native_validation"})
        result["regions"].extend(pending)
        used.update((p, m["index"]) for p, row in selected for m in row["members"])
        return True

    for p in sorted(normalized):
        rows = normalized[p]
        profile = source_profiles.get(p, {})
        for heading in sorted(rows, key=lambda r: (r["rect"][1], r["rect"][0])):
            kind = _region_credit_kind(heading, rules)
            if not kind or any((p, m["index"]) in used for m in heading["members"]):
                continue
            if not profile.get("confirmed"):
                record("unresolved", p, heading, "Actual source layout is unconfirmed.")
                continue
            col = _region_credit_column(heading, profile)
            if col is None:
                record("unresolved", p, heading, "Heading does not belong to one confirmed column.")
                continue
            evidence = profile.get("layout_evidence", {})
            body_start = evidence.get("body_start_y")
            if p == 1 and (not isinstance(body_start, (int, float)) or
                           not math.isfinite(body_start) or heading["rect"][1] < body_start):
                record("unresolved", p, heading, "Page1 front-matter/section boundary is unconfirmed.")
                continue
            if _region_credit_protected(heading["rect"], profile):
                record("rejected", p, heading, "Heading belongs to a protected source container.")
                continue
            if any(r["block"] == heading["block"] and r["rect"][3] < heading["rect"][1] - .5 for r in rows):
                record("rejected", p, heading, "Label is inside a source block, not its section start.")
                continue
            if any(r is not heading and r["block"] != heading["block"] and _region_credit_column(r, profile) == col
                   and abs(r["rect"][1] - heading["rect"][1]) <= 1 for r in rows):
                record("unresolved", p, heading, "Same-row peer text may be a table/rubric cell.")
                continue
            run_in = not _region_credit_standalone(heading, rules)
            verified_run_in = set(evidence.get("run_in_heading_rows", []))
            if run_in and not any(m["index"] in verified_run_in for m in heading["members"]):
                record("unresolved", p, heading, "Run-in heading lacks actual leading-label style evidence.")
                continue
            font = profile.get("body_font")
            if not run_in and not (heading.get("styled_heading") or font and heading["size"] >= 1.04 * font):
                record("unresolved", p, heading, "Credit label has no source heading-style evidence.")
                continue
            column_rows = sorted([r for r in rows if _region_credit_column(r, profile) == col and not _region_credit_folio(r)],
                                 key=lambda r: (r["rect"][1], r["rect"][0]))
            at = next(i for i, r in enumerate(column_rows) if r is heading)
            selected, end, uncertain_stop = [heading], None, False
            previous = heading
            for later in column_rows[at + 1:]:
                if _region_credit_boundary(later, rules) or _region_credit_kind(later, rules):
                    end = {"kind": "next_explicit_heading_or_caption", "page": p,
                           "text": later["text"], "rect": later["rect"]}
                    break
                if _region_credit_protected(later["rect"], profile):
                    uncertain_stop = True
                    break
                if later["rect"][1] - previous["rect"][3] > max(18, 2 * (font or later["size"])):
                    uncertain_stop = True
                    break
                if (later.get("styled_heading") and len(later["text"].split()) <= 10 and
                        not _region_inline_italic_continuation(later,previous)):
                    uncertain_stop = True
                    break
                selected.append(later)
                previous = later
            if uncertain_stop:
                record("unresolved", p, heading, "Unclassified gap, graphic, or heading interrupts the section.")
                continue
            if end is not None:
                if len(selected) == 1 and not run_in:
                    record("unresolved", p, heading, "No bounded credit body after heading.")
                    continue
                emit([(p,r) for r in selected], kind, p, heading, end)
                continue
            reference_end=_region_credit_next_page_references(p,col,selected,normalized,source_profiles,rules)
            if reference_end is not None:
                emit([(p,r) for r in selected],kind,p,heading,reference_end)
                continue
            # Existing proven two-column bridges; no invented coordinate transform.
            width, height = page_sizes[p]
            if len(profile.get("column_bands", [])) == 2:
                following = p if col == 0 else p + 1
                fp = source_profiles.get(following, {})
                if fp.get("confirmed") and len(fp.get("column_bands", [])) == 2:
                    from types import SimpleNamespace
                    page_list = [normalized.get(i, []) for i in range(1, max(normalized) + 1)]
                    continuation = rules._cross_page_ack(
                        page_list, p, heading, SimpleNamespace(width=width,height=height),
                        kind=kind, names=names)
                    if continuation:
                        emit(continuation, kind, p, heading,
                             {"kind": "frozen_helper_bounded_column_or_page_continuation"})
                        continue
            # A confirmed following figure page bounds the current page; it is never covered.
            if p + 1 in normalized and _region_credit_figure_page_boundary(p + 1, normalized[p + 1], source_profiles):
                emit([(p,r) for r in selected], kind, p, heading,
                     {"kind": "confirmed_following_scientific_figure_page", "page": p + 1})
                continue
            record("unresolved", p, heading, "No proven section end; no EOF or float-spanning guess.")
    return result

REGION_RULES_VERSION = 'anonymize-regions-6'
REGION_SCOPE = 'frontmatter-margins-credits-v1'


def _region_extract_lines(page):
    import pymupdf as fitz
    lines=[]
    data=page.get_text('dict',flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    for block_id,block in enumerate(data['blocks']):
        if block['type']!=0:continue
        for raw in block['lines']:
            spans=[span for span in raw['spans'] if span['text'].strip()]
            text=''.join(span['text'] for span in raw['spans']).strip()
            if not text or not spans:continue
            size=max(span['size'] for span in spans)
            primary=[span for span in spans if span['size']>=size-.5 and re.search(r'[^\W\d_]{2}',span['text'])]
            primary=primary or [span for span in spans if span['size']>=size-.5]
            lines.append({'text':text,'rect':list(raw['bbox']),'block':block_id,'size':size,
                'styled_heading':any(span['flags']&18 for span in spans),
                'primary_fonts':sorted({span['font'] for span in primary}),
                'primary_styles':sorted({span['flags']&18 for span in primary}),
                'spans':[{k:span[k] for k in ('text','font','size','flags','bbox')} for span in spans]})
    return sorted(lines,key=lambda x:(round(x['rect'][1],1),x['rect'][0]))


def _region_native_stroke_envelope(drawing, bboxlog, seqno_counts):
    """Return a proved native box, or None; never synthesize a miter limit.

    Inputs must come from the same original page: get_cdrawings(extended=True)
    and get_bboxlog(layers=True). seqno_counts counts painted drawing records.
    Keep the existing clip/group hierarchy validation and intersections outside
    this helper. In particular a transparency group is not a clipping path.
    """
    def rectangle(value):
        if (not isinstance(value, (tuple, list)) or len(value) != 4
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
            return None
        return tuple(value) if value[0] < value[2] and value[1] < value[3] else None

    kind, seqno = drawing.get('type'), drawing.get('seqno')
    if kind not in ('s', 'fs') or type(seqno) is not int or seqno < 0 or seqno_counts.get(seqno) != 1:
        return None
    raw = rectangle(drawing.get('rect'))
    if raw is None or 'layer' not in drawing:
        return None
    # Narrow fallback: arbitrary, malformed or wholly degenerate paths remain
    # unresolved. Existing simple line/rectangle and clipped branches stay intact.
    items = drawing.get('items')
    if not isinstance(items, (list, tuple)) or not items:
        return None
    nondegenerate = False
    for item in items:
        if (not isinstance(item, (tuple, list)) or not item or
                (item[0], len(item)) not in {('l', 3), ('c', 5)}):
            return None
        points = item[1:]
        if any(not isinstance(p, (tuple, list)) or len(p) != 2 or
               any(type(x) not in (int, float) or not math.isfinite(x) for x in p) for p in points):
            return None
        nondegenerate |= any(tuple(p) != tuple(points[0]) for p in points[1:])
    if not nondegenerate:
        return None

    def event(index, expected):
        if index >= len(bboxlog):
            return None
        item = bboxlog[index]
        if not isinstance(item, (tuple, list)) or len(item) != 3 or item[0] != expected or item[2] != drawing['layer']:
            return None
        return rectangle(item[1])

    if kind == 's':
        box = event(seqno, 'stroke-path')
    else:
        # PyMuPDF merges identical consecutive fill/stroke path records into fs,
        # retaining the fill seqno. Refuse nonadjacency or another drawing owner.
        if seqno_counts.get(seqno + 1, 0) != 0 or event(seqno, 'fill-path') != raw:
            return None
        box = event(seqno + 1, 'stroke-path')
    if box is None or not (box[0] <= raw[0] <= raw[2] <= box[2] and box[1] <= raw[1] <= raw[3] <= box[3]):
        return None
    return box

def _region_native_graphics(page,size):
    try:
        images=[{'kind':'image','rect':list(item['bbox'])} for item in page.get_image_info()]
        drawings=page.get_cdrawings(extended=True)
        # Validate hierarchy/types first with the reviewed conservative helper.
        if _region_clipped_drawing_rects(drawings,(0,0,*size)) is None:return None
        clips=[];vectors=[];bounds=(0,0,*size);native_bboxlog=None
        seqno_counts=Counter(x.get('seqno') for x in drawings if x.get('type') in ('s','fs','f'))
        for drawing in drawings:
            level=drawing['level'];clips=[(depth,box) for depth,box in clips if depth<level]
            kind=drawing['type']
            if kind=='clip':clips.append((level,tuple(drawing['scissor'])));continue
            if kind=='group':continue
            raw=tuple(drawing['rect'])
            if kind=='f':box=raw
            else:
                width=drawing.get('width');caps=drawing.get('lineCap');join=drawing.get('lineJoin')
                if (not isinstance(width,(int,float)) or not math.isfinite(width) or width<0 or
                    not isinstance(caps,(tuple,list)) or any(x not in (0,1,2) for x in caps)):
                    return None
                items=drawing.get('items') or []
                simple=len(items)==1 and items[0][0] in ('l','re')
                if simple or join in (1,2):
                    # Full line width bounds round/bevel joins and known caps;
                    # isolated lines and rectangles have no acute miter joins.
                    pad=max(1.0,width)
                    box=(raw[0]-pad,raw[1]-pad,raw[2]+pad,raw[3]+pad)
                elif clips:
                    # An actual clip bounds all visible ink even when a custom
                    # miter envelope is unavailable. Never invent a miter limit.
                    box=bounds
                else:
                    if not hasattr(page,'get_bboxlog'):return None
                    if native_bboxlog is None:native_bboxlog=page.get_bboxlog(layers=True)
                    box=_region_native_stroke_envelope(drawing,native_bboxlog,seqno_counts)
                    if box is None:return None
            box=_region_drawing_intersection(box,bounds)
            for _,clip in clips:
                if box is not None:box=_region_drawing_intersection(box,clip)
            if box is not None and box[0]<box[2] and box[1]<box[3]:
                vectors.append({'kind':'vector','rect':list(box)})
        return images+vectors
    except (RuntimeError,ValueError,TypeError,OverflowError,KeyError,IndexError):
        return None


def build_region_plan(source: Path,paper: dict,*,diagnostics=None) -> dict:
    """Plan only source-confirmed title, margins and explicit credit regions."""
    import pymupdf as fitz
    import sys
    from types import SimpleNamespace
    if not isinstance(paper,dict):raise ValueError('Paper metadata must be an object')
    if diagnostics is not None and not isinstance(diagnostics,dict):raise TypeError('Planner diagnostics must be a dictionary')
    details={} if diagnostics is None else diagnostics
    for key in ('regions','unresolved','scope','planner_version','source_layout_profile','page_layout','rejected_sections','profile_id','profile_schema'):
        details.pop(key,None)
    details.update(scope=REGION_SCOPE,planner_version=REGION_RULES_VERSION,regions=[],unresolved=[])
    plan={'redactions':[],'forbidden_strings':[],'uncertain':[]}
    api=sys.modules.get(__name__) or SimpleNamespace(**globals())
    with fitz.open(source) as document:
        if document.needs_pass or not document.page_count:raise ValueError('Encrypted or empty PDF is unsupported')
        pages=[_region_extract_lines(page) for page in document]
        sizes=[((page.rect*page.derotation_matrix).width,(page.rect*page.derotation_matrix).height) for page in document]
        structure=_region_primary_structure(pages,sizes,paper,api) if pages[0] else {}
        names=_author_names(paper)
        title=' '.join(_normal(pages[0][i]['text']) for i in structure.get('title_indexes',[]))
        drawing_pages={1}
        drawing_pages.update(number for number,_ in _source_author_headers(pages,sizes,names))
        drawing_pages.update(number for number,_ in _source_status_headers(pages,sizes,paper.get('title') or ''))
        for number,lines in enumerate(pages,1):
            width,height=sizes[number-1]
            for line in lines:
                if (_identity_section_kind(line['text']) or _normal(line['text'])=='acknowledge'):
                    drawing_pages.add(number)
                    if number<len(pages):drawing_pages.add(number+1)
                if (line['rect'][1]<height*.14 or line['rect'][3]>height*.87 or line['rect'][2]<width*.08 or line['rect'][0]>width*.92):
                    value=_normal(line['text'])
                    if VENUE.search(line['text']) or _REGION_MARGIN_STATUS.fullmatch(line['text'].strip()) or ARXIV.search(line['text']) or title and len(value.split())>=3 and value in title:
                        drawing_pages.add(number)
        graphics={number:_region_native_graphics(document[number-1],sizes[number-1]) for number in sorted(drawing_pages)}
        profile=_select_layout_profile(paper,pages,sizes,first_graphics=graphics.get(1),first_page_evidence=structure)
        details['source_layout_profile']=profile
        page_profiles=_region_page_profiles(pages,sizes,graphics,profile.get('selected_family'))
        if page_profiles.get(1):page_profiles[1]['layout_evidence']['body_start_y']=structure.get('body_boundary')
        for number,lines in enumerate(pages,1):
            run_in=[i for i,line in enumerate(lines) if _region_credit_run_in_style(line)]
            page_profiles[number]['layout_evidence']['run_in_heading_rows']=run_in
        details['page_layout']=page_profiles
        character_lines={}
        def slice_boxes(number,line,start,end):
            if number not in character_lines:
                raw=document[number-1].get_text('rawdict',flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)
                character_lines[number]=[item for block in raw['blocks'] if block['type']==0 for item in block['lines']]
            candidates=[]
            for raw in character_lines[number]:
                chars=[c for span in raw['spans'] for c in span['chars']]
                text=''.join(c['c'] for c in chars)
                if tuple(raw['bbox'])==tuple(line['rect']) and text.strip()==line['text']:candidates.append((chars,text))
            if len(candidates)!=1:return []
            chars,text=candidates[0];leading=len(text)-len(text.lstrip());lo,hi=start+leading,end+leading
            selected=[];others=[];offset=0
            for char in chars:
                stop=offset+len(char['c'])
                if char['c'].strip():
                    if offset<hi and stop>lo:
                        if offset<lo or stop>hi:return []
                        selected.append(char['bbox'])
                    else:others.append(char['bbox'])
                offset=stop
            if not selected:return []
            box=_region_padded_slice_box(selected,others)
            return [box] if box is not None else []
        fronts=_propose_frontmatter_and_margins(pages,sizes,paper=paper,graphics_by_page=graphics,
            rules_module=api,slice_boxes=slice_boxes,footer_name_slices=_region_footer_author_name_slices,
            footer_affiliation_slices=_region_footer_affiliation_slices)
        for number,layout in page_profiles.items():
            for region in fronts['regions']:
                if region['page']==number and region['target'] in {'header','footer'}:
                    layout['protected_regions'].append({'rect':region['rect'],'kind':'running_margin'})
            if (not layout['confirmed'] and graphics.get(number) is not None and
                    any(_REGION_CAPTION.match(line['text']) for line in pages[number-1])):
                content=[line for line in pages[number-1] if not re.fullmatch(r'\d{1,3}',line['text'].strip())]
                if content and all(any(box['rect'][0]<=line['rect'][0] and box['rect'][1]<=line['rect'][1] and
                                       box['rect'][2]>=line['rect'][2] and box['rect'][3]>=line['rect'][3]
                                       for box in layout['protected_regions']) for line in content):
                    layout['confirmed']=True;layout['layout_evidence']['figure_only']=True
        proposals=list(fronts['regions']);unresolved=list(fronts['unresolved'])
        if '_propose_credit_regions' in globals():
            credits=_propose_credit_regions({i:lines for i,lines in enumerate(pages,1)},
                {i:size for i,size in enumerate(sizes,1)},names=names,source_profiles=page_profiles,rules_module=api)
            proposals.extend(credits['regions']);unresolved.extend(credits['unresolved'])
        else:
            unresolved.append('Regional credit-section component is not yet integrated; candidate is not acceptance-ready.')
        seen=set()
        for proposal in sorted(proposals,key=lambda x:(x['page'],x['rect'][1],x['rect'][0])):
            number=proposal['page'];rect=_region_clip(proposal['rect'],sizes[number-1])
            if rect is None:continue
            target=proposal.get('target');key=(number,tuple(rect))
            if key in seen:continue
            if target not in {'title_block','header','footer','acknowledgment','contribution'}:
                unresolved.append('Unrecognized regional target was preserved.');continue
            seen.add(key);index=len(plan['redactions'])
            category=proposal.get('category') or ('acknowledgment' if target=='acknowledgment' else 'other_identity')
            plan['redactions'].append({'page':number,'rect':rect,'category':category,'text':''})
            details['regions'].append({'plan_index':index,'region_type':target,
                'source_evidence':{k:v for k,v in proposal.items() if k not in {'rect','category','target'}}})
        if not any(x['region_type']=='title_block' for x in details['regions']):
            unresolved.append('Required primary first-page title area was not resolved.')
        if not plan['redactions']:unresolved.append('No safe regional target was selected.')
        details['rejected_sections']=credits.get('rejected',[]) if '_propose_credit_regions' in globals() else []
        details['unresolved']=unresolved
        plan['uncertain']=list(dict.fromkeys(x if isinstance(x,str) else str(x) for x in unresolved))
    return plan

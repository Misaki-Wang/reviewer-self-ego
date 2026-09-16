"""Read-only identity residual audit, independent of redaction plans.

Both parsers inspect every source and candidate page. Identity terms must come
from an independent source oracle or paper metadata, never a redaction plan.
Passing covers text and PDF structure only: raster/vector content still requires
visual review because this module has no OCR backend.
"""
from collections import Counter
from pathlib import Path
import hashlib
import re
import unicodedata

import pymupdf
from pypdf import PdfReader

SCHEMA_VERSION = "anonymize-rules-audit-6"
REFERENCE_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)?(?:references|bibliography|references and notes)\s*$",
    re.I,
)
SECTION_END = re.compile(
    r"^\s*(?:appendi(?:x|ces)\b.*|supplement(?:ary|al)?\b.*|"
    r"acknowledge?ments?|author contributions?|limitations)\s*$", re.I,
)
REFERENCE_TITLES = {"references", "bibliography", "referencesandnotes"}


def _reference_title(text):
    value = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", normalize_text(text))
    return "".join(value.split()) in REFERENCE_TITLES


def _section_title(text):
    # Initials inside author/editor lists are not appendix headings. Restrict
    # letter-labelled endings to all-caps titles or recognizable section titles.
    value = text.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z -]{2,100}", value):
        return False
    return value.isupper() or bool(re.match(
        r"(?i)^(?:related work|proofs?\b|additional\b|experimental\b|experiments?\b|"
        r"implementation\b|theoretical\b|notation\b|hyperparameters?\b|"
        r"training\b|data\b|details\b|results\b)", value,
    ))


def _section_boundary(line, following):
    text = line.strip()
    if SECTION_END.fullmatch(text):
        return "end"
    if re.fullmatch(r"[A-Z]\.\d+(?:\.\d+)*[.)]?(?:\s+.+)?", text):
        return "end"
    if re.fullmatch(r"(?i)(?:results|experiments|examples|scientific examples|"
                    r"additional examples|qualitative results)", text):
        return "ambiguous"
    if re.fullmatch(r"[A-Z](?:\.\d+)*[.)]?", text):
        title = following.strip()
    else:
        match = re.fullmatch(r"[A-Z](?:\.\d+)*[.)]?\s+(.+)", text)
        title = match[1] if match else ""
    if _section_title(title):
        return "end"
    if (len(title.split()) >= 2
            and re.fullmatch(r"[A-Z][A-Za-z -]{3,100}[.]?", title)):
        return "ambiguous"
    return None


CONTACT_CATEGORIES = {"contact", "email", "url", "owned_url", "arxiv", "arxiv_id"}
FOOTNOTE_CUE = re.compile(
    r"correspond(?:ing|ence)?|equal contribution|contact\s*:|e-?mail|"
    r"author affiliation|[†‡]|\b[\w.+-]+\s*@",
    re.I,
)


def _normalized(text):
    chars, positions = [], []
    for offset, char in enumerate(text):
        for piece in unicodedata.normalize("NFKD", char).casefold():
            if unicodedata.category(piece) in {"Mn", "Cf"}:
                continue
            chars.append("-" if piece in "‐‑‒–−" else piece)
            positions.append(offset)
    expanded = "".join(chars)
    removed = set()
    for match in re.finditer(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)", expanded):
        removed.update(range(match.start(), match.end()))
    result, mapping = [], []
    for index, char in enumerate(expanded):
        if index in removed:
            continue
        if char.isspace():
            if result and result[-1] != " ":
                result.append(" ")
                mapping.append(positions[index])
        else:
            result.append(char)
            mapping.append(positions[index])
    if result and result[-1] == " ":
        result.pop()
        mapping.pop()
    return "".join(result), mapping


def normalize_text(text):
    """Fold Unicode, diacritics, ligatures, whitespace and line hyphenation."""
    return _normalized(text)[0]


def metadata_author_names(metadata):
    """Reject unusable global author clues without changing explicit identity terms."""
    authors = (metadata or {}).get("authors", [])
    if not isinstance(authors, (list, tuple)):
        authors = [authors]
    names, unresolved = [], []
    for index, author in enumerate(authors):
        if isinstance(author, dict):
            author = author.get("name") or " ".join(
                author[key] for key in ("given", "family")
                if isinstance(author.get(key), str)
            ).strip()
        if (not isinstance(author, str)
                or sum(char.isalpha() for char in normalize_text(author)) < 2):
            unresolved.append({
                "reason": "unusable_author_metadata", "author_index": index,
                "detail": "Metadata author is not a usable global name; at least two letters are required.",
            })
        elif author not in names:
            names.append(author)
    return names, unresolved


def _vocabulary(identity_terms, source_metadata, unresolved=None):
    if isinstance(identity_terms, dict):
        if any(key in identity_terms for key in ("text", "term", "value")):
            identity_terms = [identity_terms]
        else:
            source_metadata = {**identity_terms, **(source_metadata or {})}
            identity_terms = []
    items = list(identity_terms or ())
    metadata = source_metadata or {}
    authors, author_issues = metadata_author_names(metadata)
    if unresolved is not None:
        unresolved.extend(author_issues)
    for author in authors:
        items.append({"text": author, "category": "author", "allow_reference": True})
    for field, category in (
        ("affiliations", "affiliation"), ("emails", "email"),
        ("owned_urls", "owned_url"), ("identity_terms", "identity"),
    ):
        values = metadata.get(field, metadata.get("terms", []) if field == "identity_terms" else [])
        if isinstance(values, str):
            values = [values]
        for item in values:
            items.append(item if isinstance(item, dict) else {"text": item, "category": category})
    if metadata.get("arxiv_id"):
        items.append({"text": str(metadata["arxiv_id"]), "category": "arxiv_id"})
    terms, seen = [], set()
    for item in items:
        item = {"text": item} if isinstance(item, str) else dict(item)
        text = item.get("text", item.get("term", item.get("value")))
        if not isinstance(text, str) or not normalize_text(text):
            raise ValueError("Identity terms require nonempty text")
        category = str(item.get("category", "identity"))
        allow_reference = bool(item.get("allow_reference", False))
        require_absent = bool(item.get("require_absent_everywhere", not allow_reference))
        if category in CONTACT_CATEGORIES:
            require_absent = True
        term = {
            "text": text, "normalized": normalize_text(text), "category": category,
            "allow_reference": allow_reference,
            "require_absent_everywhere": require_absent,
        }
        key = tuple(term.values())
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _matches(page, term):
    text, offsets = page["normalized"], page["offsets"]
    needle = term["normalized"]
    compact = term["category"] in CONTACT_CATEGORIES or "@" in needle or "://" in needle
    if compact:
        kept = [i for i, char in enumerate(text) if not char.isspace()]
        text, offsets = "".join(text[i] for i in kept), [offsets[i] for i in kept]
        needle = "".join(needle.split())
    suffix = r"(?:v\d+)?" if term["category"] in {"arxiv", "arxiv_id"} else ""
    # Affiliation markers can be attached to author names after PDF extraction
    # (e.g. "1Alice Example2"). Extra letters must still reject partial names.
    boundary = r"[^\W\d_]" if term["category"] == "author" else r"\w"
    pattern = re.compile(re.escape(needle) + suffix if compact else
                         r"(?<!" + boundary + ")" + re.escape(needle) + suffix + r"(?!" + boundary + ")")
    for found in pattern.finditer(text):
        start, end = offsets[found.start()], offsets[found.end() - 1] + 1
        if compact:
            before = page["text"][start - 1:start] if start else ""
            after = page["text"][end:end + 1]
            if (before and re.match(r"\w", before)) or (after and re.match(r"\w", after)):
                continue
        yield start, end


def _page_record(text, segments=(), error=None):
    text = text or ""
    normalized, offsets = _normalized(text)
    return {
        "text": text, "normalized": normalized, "offsets": offsets,
        "segments": list(segments), "error": error,
    }


def _read_pymupdf(path):
    pages = []
    try:
        with pymupdf.open(path) as doc:
            for page in doc:
                try:
                    chunks, segments, offset = [], [], 0
                    for block in page.get_text("dict").get("blocks", []):
                        for line in block.get("lines", []):
                            spans = line.get("spans", [])
                            value = "".join(span.get("text", "") for span in spans) + "\n"
                            chunks.append(value)
                            segments.append({
                                "start": offset, "end": offset + len(value),
                                "bbox": list(line["bbox"]),
                                "size": max((s.get("size", 0) for s in spans), default=0),
                            })
                            offset += len(value)
                    pages.append(_page_record("".join(chunks), segments))
                except Exception as exc:
                    pages.append(_page_record("", error=f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    return pages, None


def _read_pypdf(path):
    pages = []
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            try:
                pages.append(_page_record(page.extract_text() or ""))
            except Exception as exc:
                pages.append(_page_record("", error=f"{type(exc).__name__}: {exc}"))
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    return pages, None


def _reference_spans(pages):
    """Recompute each document's offsets; carry explicit sections across pages."""
    result, active, heading_page, ambiguous = [], False, None, False
    for page_number, page in enumerate(pages, 1):
        spans, start, offset = [], 0 if active else None, 0
        lines = page["text"].splitlines(keepends=True)
        for index, line in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else ""
            heading_end = None
            if _reference_title(line):
                heading_end = offset + len(line)
            elif following and _reference_title(line + following):
                # Small-caps PDF extraction can emit "R" and "EFERENCES" as
                # different lines. The body begins after both pieces.
                heading_end = offset + len(line) + len(following)
            if heading_end is not None:
                if start is not None and start < offset:
                    spans.append((start, offset, heading_page, ambiguous))
                active, start, heading_page, ambiguous = True, heading_end, page_number, False
            elif active:
                boundary = _section_boundary(line, following)
                if boundary:
                    if start is not None and start < offset:
                        spans.append((start, offset, heading_page, ambiguous))
                    active = boundary == "ambiguous"
                    start, ambiguous = offset + len(line) if active else None, active
            offset += len(line)
        if active and start is not None and start < len(page["text"]):
            spans.append((start, len(page["text"]), heading_page, ambiguous))
        result.append(spans)
    return result


def _span_at(spans, start, end):
    return next((span for span in spans if span[0] <= start and end <= span[1]), None)


def _footnote_like(page, start, end):
    line_start = page["text"].rfind("\n", 0, start) + 1
    line_end = page["text"].find("\n", end)
    if line_end < 0:
        line_end = len(page["text"])
    if FOOTNOTE_CUE.search(page["text"][line_start:line_end]):
        return True
    # Tiny type in a references region is ambiguous, so it never gets an exception.
    return any(
        seg["start"] < end and start < seg["end"] and 0 < seg["size"] < 8.5
        for seg in page["segments"]
    )


def _bibliographic_headers(text):
    # Bare middle initials and extracted spacing diacritics occur in real
    # author lists; keep the capitalized-name and citation punctuation anchors.
    name = r"(?:[A-ZÀ-ÖØ-Þ][^\W\d_]*(?:['’\-\u02b0-\u02ff][^\W\d_]+)*|[A-Z]\.)"
    authors = name + r"(?:\s+(?:" + name + r"|de|van|von|der|da|del)){1,5}(?:\s*[,.]|\s+and\b)"
    inverted = name + r",\s+(?:[A-Z]\.\s*){1,4}(?:[,;]|\s|$)"
    return list(re.finditer(r"(?m)^[ \t]*(?:\[\d+\][ \t]*|" + authors + "|" + inverted + r")", text))


def _reference_continuations(pages, spans):
    """Attach at most the next page's same explicit reference region."""
    for index, page in enumerate(pages[:-1]):
        if (not spans[index] or not spans[index + 1]
                or spans[index][-1][1] != len(page["text"])):
            continue
        current, following = spans[index][-1], spans[index + 1][0]
        if (current[3] or following[3] or following[0] != 0
                or current[2] != following[2]):
            continue
        page["reference_continuation"] = {
            "heading_page": current[2],
            "text": pages[index + 1]["text"][following[0]:following[1]],
        }


def _bibliographic_entry(page, start, end, span, *, require_year_after=True):
    """Require source-comparable local citation syntax and publication evidence.

    The default requires a year after the author hit. The optional mode supports
    review of a venue line later in the same entry; it never grants author or
    contact exemptions through this API.
    """
    text = page["text"]
    caption = r"(?mi)^\s*(?:figure|fig\.?|table)\s+\d+\s*[:.]"
    # A figure before a new explicit References heading is outside this span.
    # Figures inside an inherited/active reference span remain ambiguous.
    if re.search(caption, text[span[0]:span[1]]):
        return None
    left = max(span[0], start - 3000)
    right = min(span[1], end + 3000)
    headers = _bibliographic_headers(text[left:right])
    possible = [header for header in headers if left + header.start() <= start]
    if not possible:
        return None
    entry_start = left + possible[-1].start()
    year = re.search(r"\b(?:18|19|20)\d{2}[a-z]?\b", text[entry_start:right])
    if year is None and right == span[1] == len(text):
        continuation = page.get("reference_continuation")
        tail = re.sub(r"\n[ \t]*\d{1,3}\s*$", "", text[entry_start:right]).rstrip()
        later_headers = [h for h in headers if left + h.start() >= end]
        if (continuation and continuation["heading_page"] == span[2]
                and tail and tail[-1] not in ".!?;"
                and not later_headers and not re.search(caption, continuation["text"])):
            extra = continuation["text"]
            # A following citation's year cannot complete the unfinished entry.
            next_headers = _bibliographic_headers(extra)
            if next_headers:
                extra = extra[:next_headers[0].start()]
            extra = extra[:3000]
            text += "\n" + extra
            right = len(text)
            year = re.search(r"\b(?:18|19|20)\d{2}[a-z]?\b", text[entry_start:right])
    if not year or (require_year_after and entry_start + year.start() < end):
        return None
    entry_end = max(end, entry_start + year.end())
    between = text[entry_start:entry_end]
    if re.search(caption, between):
        return None
    return normalize_text(between)


def _is_reference_hit(page_number, page, start, end, term, source_page,
                      source_spans, candidate_spans):
    candidate_span = _span_at(candidate_spans, start, end)
    if not candidate_span:
        return False, None
    if term["require_absent_everywhere"] or not term["allow_reference"]:
        return False, None
    if candidate_span[3]:
        return False, "reference_section_boundary_ambiguous"
    if page_number == 1 or candidate_span[2] == 1 or _footnote_like(page, start, end):
        return False, "reference_position_ambiguous_or_footnote"
    # Source support is a prerequisite, even when candidate citation syntax is
    # incomplete. A candidate cannot create its own reference exemption.
    originals = []
    for src_start, src_end in _matches(source_page, term):
        span = _span_at(source_spans, src_start, src_end)
        if (span and not span[3] and span[2] == candidate_span[2]
                and not _footnote_like(source_page, src_start, src_end)):
            originals.append((src_start, src_end, span))
    if not originals:
        return False, "candidate_reference_not_supported_by_original"
    candidate_entry = _bibliographic_entry(page, start, end, candidate_span)
    if candidate_entry is None:
        return False, "local_bibliographic_entry_not_established"
    if any(_bibliographic_entry(source_page, left, right, span) == candidate_entry
           for left, right, span in originals):
        return True, None
    return False, "candidate_reference_not_supported_by_original"


def _structure(path):
    result = {
        "metadata_fields": {}, "xmp_present": False, "attachment_count": 0,
        "annotation_count": 0, "link_count": 0, "form_field_count": 0,
        "outline_count": 0, "optional_content_group_count": 0,
        "page_metadata_count": 0, "graphics": [],
    }
    with pymupdf.open(path) as doc:
        result["metadata_fields"] = {
            key: value for key, value in doc.metadata.items()
            if key not in {"format", "encryption"} and value
        }
        result["xmp_present"] = bool(doc.get_xml_metadata())
        result["attachment_count"] = doc.embfile_count()
        result["outline_count"] = len(doc.get_toc())
        result["optional_content_group_count"] = len(doc.get_ocgs())
        for number, page in enumerate(doc, 1):
            result["annotation_count"] += len(list(page.annots() or []))
            result["link_count"] += len(page.get_links())
            result["form_field_count"] += len(list(page.widgets() or []))
            kind, _ = doc.xref_get_key(page.xref, "Metadata")
            result["page_metadata_count"] += int(kind != "null")
            result["graphics"].append({
                "page": number,
                "image_placements": len(page.get_image_info()),
                "vector_drawing_objects": len(page.get_cdrawings(extended=False)),
            })
    return result


def audit_pdf(source_pdf, candidate_pdf, identity_terms=(), *, source_metadata=None):
    """Return evidence; never change PDFs or invoke external programs/services."""
    source, candidate = Path(source_pdf).resolve(), Path(candidate_pdf).resolve()
    author_issues = []
    terms = _vocabulary(identity_terms, source_metadata, author_issues)
    result = {
        "schema_version": SCHEMA_VERSION,
        "source": str(source), "candidate": str(candidate),
        "identity_vocabulary": terms, "remaining_hits": [], "reference_matches": [],
        "unresolved": author_issues, "extraction_coverage": {}, "structure": {},
        "graphics": [], "requires_visual_review": True, "ready_for_blind_review": False,
        "ocr": {
            "performed": False, "status": "unavailable_to_this_auditor",
            "limitation": "Raster and vector identity is not ruled out by text or structure checks.",
        },
        "passed": False, "pass_scope": "automated_text_and_structure_only",
        "codex_cli_calls": 0, "model_api_calls": 0,
    }
    for label, path in (("source", source), ("candidate", candidate)):
        try:
            result[label + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            result["unresolved"].append({
                "reason": "pdf_unreadable", "document": label, "detail": str(exc),
            })
    if not terms:
        result["unresolved"].append({"reason": "no_independent_identity_vocabulary"})
    for parser, read in (("pymupdf", _read_pymupdf), ("pypdf", _read_pypdf)):
        originals, original_error = read(source)
        candidates, candidate_error = read(candidate)
        coverage = {"source_pages": [], "candidate_pages": []}
        result["extraction_coverage"][parser] = coverage
        for label, pages, error in (
            ("source", originals, original_error), ("candidate", candidates, candidate_error)
        ):
            if not pages and not error:
                result["unresolved"].append({
                    "reason": "no_pages_extracted", "parser": parser, "document": label,
                })
            if error:
                result["unresolved"].append({
                    "reason": "document_extraction_failed", "parser": parser,
                    "document": label, "detail": error,
                })
            for number, page in enumerate(pages, 1):
                status = "error" if page["error"] else ("text" if page["normalized"] else "empty")
                coverage[label + "_pages"].append({
                    "page": number, "status": status, "characters": len(page["normalized"]),
                })
                if status != "text":
                    result["unresolved"].append({
                        "reason": "page_extraction_incomplete", "parser": parser,
                        "document": label, "page": number, "status": status,
                        "detail": page["error"],
                    })
        if len(originals) != len(candidates):
            result["unresolved"].append({
                "reason": "page_count_mismatch", "parser": parser,
                "source_pages": len(originals), "candidate_pages": len(candidates),
            })
        original_refs, candidate_refs = _reference_spans(originals), _reference_spans(candidates)
        _reference_continuations(originals, original_refs)
        _reference_continuations(candidates, candidate_refs)
        for number, page in enumerate(candidates, 1):
            original = originals[number - 1] if number <= len(originals) else _page_record("")
            spans = original_refs[number - 1] if number <= len(original_refs) else []
            for term in terms:
                for start, end in _matches(page, term):
                    is_reference, ambiguous = _is_reference_hit(
                        number, page, start, end, term, original, spans,
                        candidate_refs[number - 1],
                    )
                    hit = {
                        "parser": parser, "page": number, "term": term["text"],
                        "category": term["category"],
                        "matched_text": page["text"][start:end],
                        "context": page["text"][max(0, start - 90):end + 90],
                        "classification": "reference" if is_reference else "remaining_identity",
                        "reference_basis": "original_section_and_local_citation_entry" if is_reference else None,
                        "reference_classification_ambiguous": bool(ambiguous),
                    }
                    result["reference_matches" if is_reference else "remaining_hits"].append(hit)
                    if ambiguous:
                        result["unresolved"].append({
                            "reason": ambiguous, "parser": parser, "page": number,
                            "term": term["text"],
                        })
    for label in ("source", "candidate"):
        counts = {parser: len(coverage[label + "_pages"])
                  for parser, coverage in result["extraction_coverage"].items()}
        if len(set(counts.values())) > 1:
            result["unresolved"].append({
                "reason": "parser_page_count_disagreement", "document": label,
                "page_counts": counts,
            })
    for label, path in (("source", source), ("candidate", candidate)):
        try:
            structure = _structure(path)
            graphics = structure.pop("graphics")
            result["structure"][label] = structure
            if label == "candidate":
                result["graphics"] = graphics
                for field, value in structure.items():
                    if value:
                        result["unresolved"].append({
                            "reason": "candidate_structure_requires_review",
                            "field": field, "value": value,
                        })
        except Exception as exc:
            result["unresolved"].append({
                "reason": "structure_inspection_failed", "document": label,
                "detail": f"{type(exc).__name__}: {exc}",
            })
    for label, path in (("source", source), ("candidate", candidate)):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != result.get(label + "_sha256"):
                result["unresolved"].append({
                    "reason": "pdf_changed_during_audit", "document": label,
                })
        except OSError as exc:
            result["unresolved"].append({
                "reason": "pdf_unreadable_at_audit_end", "document": label, "detail": str(exc),
            })
    result["summary"] = {
        "remaining_hit_count": len(result["remaining_hits"]),
        "reference_match_count": len(result["reference_matches"]),
        "unresolved_count": len(result["unresolved"]),
        "remaining_hits_by_parser": dict(Counter(hit["parser"] for hit in result["remaining_hits"])),
    }
    result["passed"] = not result["remaining_hits"] and not result["unresolved"]
    return result


audit = audit_pdf

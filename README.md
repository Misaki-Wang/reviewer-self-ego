# Reviewer Self-Ego — v1.1 Metadata Archive

This private repository contains **1,437 papers with at least three arXiv versions that can be selected so that both successive gaps exceed 30 days**. The archive consists of only this README and **[v1.1.json](v1.1.json)**, a single JSON document with full metadata.

**Release naming:** Git tag `v1.1` archives the website view **“Selected: ≥3, >30 days.”** The upstream *screening v1.1* baseline used **>7 days**. This archive is its **>30-day threshold sensitivity view**, preserving `base_screening_release: "v1.1"` and `view_kind: "threshold_sensitivity"`; it does not overwrite the frozen upstream baseline.

This is a **temporal candidate index**. Inclusion does not establish substantive revision, scientific improvement, or a complete peer-review trajectory. No new full-corpus LLM content screening was performed for this export.

## Contents and statistics

`v1.1.json` is 27,557,269 bytes. Its top-level `records` array contains one record per paper; the file is standard JSON, not JSONL.

| Venue | Selected papers |
|---|---:|
| ICLR 2026 | 706 |
| ICML 2026 | 529 |
| ACL 2026 | 202 |
| **Total** | **1,437** |

The file contains **1,437 unique arXiv IDs**, **5,434 original version records**, and **2,874 selected edges**: two edges for one example triple per paper. **670 papers have multiple qualifying triples**; the number of valid triples is retained per paper. PDFs, manuscript text, raw XML/HTML caches, the full upstream census, website assets, and human review notes are not included.

## How the index was selected

1. **Collect official conference frames.** The upstream census covered 14,064 official records: ICLR 2026 (5,353), ICML 2026 (6,415), and ACL 2026 (2,296). ICLR combined 5,351 proceedings papers and two program-only papers, reconciling duplicate Oral/Poster entries. ICML used the main-research directory, including proceedings-only papers, after excluding 213 Position papers. ACL included 2,222 main-conference long papers and 74 short papers. These public frames are not claimed to reconstruct every initial acceptance decision. Each selected record retains its official-source URLs and scope.
2. **Resolve arXiv identities.** Retrieval used normalized titles, followed by rare title words and author surnames for unresolved records. Official identifiers or a DOI could establish identity; title-based matching also required sufficient author overlap. Approximate matches remained candidates until verified. The frozen inventory contained **10,392 matched works and 3,672 unresolved records**. Unknown records were not treated as having no arXiv copy or only one version. Selected records preserve requests, candidates, errors, and matching evidence.
3. **Freeze original versions and collect dates.** The latest recorded `vN` fixed the original range `1…N`. Dates came from verified cached arXiv Atom responses and HTML histories, supplemented by explicit version-ID requests. Atom `published` supplies the initial date; `updated` supplies the date of the exact version identified by that entry. UTC timestamps and per-date source hashes were retained. Date requests were serial, at least 3.2 seconds apart, with at most 40 explicit IDs per batch. A completion pass obtained the remaining 81 dates, yielding **20,676 known dates across 10,392 matched works**, without expanding the frozen inventory or changing prior releases. The audit is embedded in `source_provenance.version_dates_source_audit`.
4. **Apply the temporal rule.** Enumerate original version triples in increasing index order and retain a paper if at least one triple satisfies both strict gap checks below. Store the first qualifying triple in numerical lexicographic order and the total number of qualifying triples. Preserve all original versions, including unselected ones.
5. **Preserve metadata and verify.** Every original field of each selected matching record was retained unchanged; arXiv metadata, dated histories, links, and selection evidence were added. Before archival, an independent check recomputed eligible IDs and triples and checked original metadata, date evidence, source hashes, and cached Atom enrichment. It reported zero errors across 712 cached source files, including 682 XML files. That external validation report and those source files are not bundled; the JSON retains source references and hashes.

## Exact time rule

For original indices `i < j < k` and UTC submission timestamps `t_i`, `t_j`, and `t_k`, **both** conditions must hold:

```text
t_j - t_i > 2,592,000 seconds
t_k - t_j > 2,592,000 seconds
```

A day is exactly 86,400 seconds. Exactly 30 days **fails**. Full timestamps determine eligibility; rounded displayed days and the total first-to-last span do not substitute for the two checks.

Selected versions **may skip original intermediate versions**: `v1 → v3 → v5` can qualify. For a paper with exactly three original versions, both `v1 → v2` and `v2 → v3` must pass. The selected example is the **lexicographically first qualifying original-version triple**, not necessarily the only one.

For context, the full upstream inventory produced the following nested candidate settings. Only the **1,437-paper** subset is included here.

| Minimum gap on each selected edge | ≥2 selected versions | ≥3 selected versions |
|---|---:|---:|
| >7 days | 5,874 | 1,992 |
| >14 days | 5,674 | 1,767 |
| >30 days | 5,383 | **1,437** |

## Preserved metadata

| Field or group | Meaning |
|---|---|
| `paper_id`, `venue`, `title`, `authors`, `doi` | Original conference/matching metadata. |
| `official_url`, `acceptance_source_url`, `source_scope` | Official-source references and frame scope. |
| `status`, `passes`, `requests`, `errors`, `candidates` | Original identity-matching history and evidence. |
| `arxiv_id`, `raw_version_count`, `arxiv_url` | Confirmed identity, frozen version count, and arXiv link. |
| `arxiv_metadata` | Cached Atom title, abstract (`summary`), authors, categories, comments, journal reference, DOI, links, and provenance. |
| `version_history` | All original version dates, completeness/validation status, per-date source evidence, and version-specific arXiv/PDF URLs. |
| `selected_three_version_example` | Selected versions, two measured gaps, and `qualifying_triple_count`. |
| `index_eligibility` | Temporal rule and explicit absence of a substantive-content eligibility claim. |

Source values, names, whitespace, and necessary non-English source fragments are preserved verbatim. Documentation is English; source metadata has not been translated or rewritten. Missing source values remain missing.

## Download and local verification

An authenticated GitHub account with repository access is required. Clone the private repository and select the archive:

```sh
git clone https://github.com/Misaki-Wang/reviewer-self-ego.git
cd reviewer-self-ego
git checkout v1.1
```

`v1.1.json` is an exact byte copy of the original export. Run this standalone Python standard-library check from the repository root; no network or other files are required:

```sh
python3 - <<'PY'
import hashlib, json, math
from collections import Counter
from datetime import datetime
from pathlib import Path

raw = Path("v1.1.json").read_bytes()
assert hashlib.sha256(raw).hexdigest() == "5138a42feda8ae267e6904107fe98efa4ffafd6950c75d230e7f79edf202e042"
index = json.loads(raw)
records = index["records"]
assert len(records) == 1437 == index["record_count"]
assert len({r["arxiv_id"] for r in records}) == 1437
assert Counter(r["venue"] for r in records) == {"ICLR2026": 706, "ICML2026": 529, "ACL2026": 202}
version_count = edge_count = 0
for paper in records:
    assert paper["status"] == "matched"
    history = paper["version_history"]
    assert history["complete_history"] and not history["validation_errors"]
    versions = history["versions"]
    assert [v["version"] for v in versions] == list(range(1, history["frozen_N"] + 1))
    dates = {}
    for version in versions:
        date = datetime.fromisoformat(version["submitted_at"].replace("Z", "+00:00"))
        assert date.tzinfo is not None and date.utcoffset() is not None
        dates[version["version"]] = date
    example = paper["selected_three_version_example"]
    selected = example["selected_versions"]
    assert len(selected) == 3 and selected[0] < selected[1] < selected[2]
    assert len(example["selected_edges"]) == 2
    for (a, b), edge in zip(zip(selected, selected[1:]), example["selected_edges"]):
        gap = (dates[b] - dates[a]).total_seconds()
        assert gap > 2592000
        assert (edge["from_version"], edge["to_version"]) == (a, b)
        assert gap == edge["gap_seconds"]
        assert math.isclose(gap / 86400, edge["gap_days"], rel_tol=0, abs_tol=1e-9)
        assert datetime.fromisoformat(edge["from_submitted_at"].replace("Z", "+00:00")) == dates[a]
        assert datetime.fromisoformat(edge["to_submitted_at"].replace("Z", "+00:00")) == dates[b]
        edge_count += 1
    version_count += len(versions)
assert version_count == 5434 and edge_count == 2874
print("PASS: exact archive bytes, 1437 papers, 5434 versions, 2874 strict >30-day edges.")
PY
```

## Provenance and interpretation limits

Top-level `source_provenance` records the matching-row, completed-date-inventory, and sorted-ID-set checksums. Per-record evidence retains source paths and hashes. These paths are **upstream provenance locators**, not files expected in this two-file archive. Local verification checks the exported data; it cannot recheck absent XML/HTML, establish completeness against the full census, or rerun original identity matching. Full reconstruction requires the original complete inputs, retrieval/matching workflow, and cached evidence. Lowering the threshold on this subset cannot recover papers excluded before export.

Temporal eligibility does not establish scientific changes or reviewer-induced revisions. arXiv versions need not match manuscripts actually seen by reviewers. Metadata does not guarantee current availability of historical PDFs. Multiple pairs/triples from the same paper are dependent observations. Counts are conditional on the resolved identities and chosen conference frames, not estimates of matching recall or content-screening prevalence. This archive does not assign a new license to the underlying papers or source material.

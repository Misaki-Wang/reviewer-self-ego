# Reviewer Self-Ego — v1.3 Preliminary Candidate Archive

[v1.3/](v1.3/) contains the latest dataset in **ten conference/source JSON files and an [index manifest](v1.3/index.json)**, tracked with ordinary Git. Their union preserves **3,724 unique arXiv papers and all 14,086 original versions** from the verified MBZUAI export, created at **`2026-09-11T06:38:05.787685Z`**. **No S2 content assessment, page-count filter or license filter was applied.** Temporal eligibility does not establish substantive revision, scientific improvement or reviewer influence.

## Scope and selection

| Raw-input stage | Count |
| --- | ---: |
| Public source entries across 10 sources | 34,127 |
| Confirmed source-to-arXiv entries | 25,646 |
| Unique confirmed arXiv papers | 25,613 |
| Source entries with unresolved identity | 8,481 |
| Original versions in the confirmed raw union | 51,410 |
| Temporally eligible unique papers | 3,724 |
| Temporally ineligible confirmed papers | 21,889 |
| Original versions of eligible papers | 14,086 |

Sources cover ICLR 2026; ICML and ACL 2025–2026; CVPR, EMNLP, ICCV and NeurIPS 2025; and the TMLR 2025-plus collection. Confirmed identities are deduplicated by arXiv ID, preserving 3,738 eligible source memberships. Unmatched, candidate-only and ambiguous identities remain unknown, not temporal failures. Complete frame processing does not establish exhaustive identity recall. Earlier selected releases are not selection inputs.

Eligibility requires an ordered subsequence of **at least three original versions**, with **every consecutive selected gap strictly greater than 2,592,000 seconds (30 elapsed days)**. For a three-version witness, both gaps must pass. Exactly 30 days fails; intermediates may be skipped (`v1 → v3 → v5`). First-to-last span alone is insufficient. Unknown timestamps must remain null with reasons and cannot establish a passing gap; this frozen confirmed inventory has zero unknown/conflicting dates and zero unresolved temporal outcomes.

## Schema and interpretation

`index.json` uses schema `v1.3-source-package-1`. Its `dataset_metadata` preserves **all original non-record top-level fields unchanged**, including global statistics, provenance and protocol. `source_json` identifies the original combined file and its bytes/hash; `files[]` gives each `source_id`, relative `path`, `record_count`, bytes and SHA-256. `record_order` preserves the original paper order.

Each `<source_id>.json` uses schema `v1.3-source-shard-1`, with `release`, `source_id`, `record_count` and `records`. Every paper object is unchanged, including memberships in other sources. Cross-source duplicates must match exactly: **3,738 file records deduplicate to 3,724 papers**. Use the manifest to reconstruct the original schema `v1.3-raw-derived-preliminary-release-1`; its `stage` is `preliminary_only`, and `S2_assessed`/`content_screening_performed` are false.

| Field within each `records[]` paper | Meaning |
| --- | --- |
| `arxiv_id`, `arxiv_url`, `title`, `authors` | Unique paper identity and descriptive metadata. |
| `source_ids`, `source_entries[]` | Normalized memberships, venue/presentation fields, source and identity locators. Review scores are null, not zero. |
| `publications[]` | Full native publication and matching records; source-specific structures are preserved. |
| `frozen_N`, `versions[]` | Maximum observed original version and complete numerical history, including skipped versions. |
| `version_history_status`, `version_history_validation_errors` | History validation outcome. |
| `temporal_eligibility` | `status`, `witness_versions`, `witness_edges` and `reasons`; one qualifying example, not every possible triple. |
| `license_metadata`, `raw_metadata` | Latest-record license evidence and raw observation locators. |
| `S2_assessed`, `content_screening_status` | False and `not_performed`. |

Each version contains `version`, UTC ISO `submitted_at`, `date_status`, `date_unresolved_reason`, `date_provenance`, exact-version `arxiv_url`/`pdf_url`, `pdf`, `page_count`, `page_metadata_evidence` and `license_metadata`. Witness edges contain `from_version`, `to_version` and `gap_seconds`.

Read physical pages from **`versions[].page_count.value`**, with `page_count.status`. Counts include references and appendices inside that exact PDF. **2,679 are verified; 11,407 are unresolved**, with null values and explicit reasons. Verified counts carry PDF SHA-256 and parser evidence; `pdf` retains byte counts and acquisition provenance. A populated `pdf.path` does not prove availability. These counts describe the frozen checkpoint, not subsequent downloader progress.

Paper-level `license_metadata.license` reports the latest observed work-record license, preserving raw/normalized URIs, name, code, SPDX value where applicable, and observations. Version-level `license_metadata` contains **3,736 latest-record-context observations and 10,350 unknown historical licenses**. Never inherit a latest license into an unknown older version. Manuscript licensing is separate from descriptive-metadata licensing.

## Download and verify

Use an authenticated account with repository access:

```sh
git clone https://github.com/Misaki-Wang/reviewer-self-ego.git
cd reviewer-self-ego
```

The original combined JSON remains on MBZUAI: **130,489,299 bytes**, SHA-256 **`9a908aede8b2b30b29db3668baa0db69095c8550aa8adb78ba48282770751cba`**. This standard-library check verifies every source file, checks identical duplicates, reconstructs the original bytes in memory, and validates the dataset:

```sh
python3 - <<'PYCODE'
import hashlib, json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

base = Path("v1.3")
manifest = json.loads((base / "index.json").read_bytes())
assert manifest["schema_version"] == "v1.3-source-package-1"
assert manifest["release"] == "v1.3" and len(manifest["files"]) == 10
by_id, appearances, source_counts = {}, {}, {}
for entry in manifest["files"]:
    assert Path(entry["path"]).name == entry["path"]
    raw = (base / entry["path"]).read_bytes()
    assert len(raw) == entry["bytes"] < 100 * 1024**2
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    shard = json.loads(raw)
    sid = entry["source_id"]
    assert sid not in source_counts
    assert shard["schema_version"] == "v1.3-source-shard-1"
    assert shard["release"] == "v1.3" and shard["source_id"] == sid
    assert len(shard["records"]) == shard["record_count"] == entry["record_count"]
    assert len({p["arxiv_id"] for p in shard["records"]}) == shard["record_count"]
    source_counts[sid] = shard["record_count"]
    for paper in shard["records"]:
        aid = paper["arxiv_id"]
        assert sid in paper["source_ids"]
        assert aid not in by_id or by_id[aid] == paper
        by_id[aid] = paper
        appearances.setdefault(aid, set()).add(sid)
assert sum(source_counts.values()) == 3738
order = manifest["record_order"]
assert len(order) == len(set(order)) == len(by_id) == 3724
assert set(order) == set(by_id)
assert all(appearances[aid] == set(p["source_ids"]) for aid, p in by_id.items())
d = dict(manifest["dataset_metadata"])
assert "records" not in d
d["records"] = [by_id[aid] for aid in order]
assert source_counts == d["statistics"]["candidate_source_memberships"]
original = manifest["source_json"]
assert original == {"filename": "v1.3.json", "bytes": 130489299,
    "sha256": "9a908aede8b2b30b29db3668baa0db69095c8550aa8adb78ba48282770751cba"}
raw = (json.dumps(d, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":")) + "\n").encode("utf-8")
assert len(raw) == original["bytes"]
assert hashlib.sha256(raw).hexdigest() == original["sha256"]
assert d["schema_version"] == "v1.3-raw-derived-preliminary-release-1"
assert d["stage"] == "preliminary_only"
assert d["S2_assessed"] is d["content_screening_performed"] is False
records = d["records"]
assert len(records) == d["record_count"] == 3724
assert len({r["arxiv_id"] for r in records}) == 3724
funnel = d["raw_filter_funnel"]
for key, value in {"source_entry_count": 34127,
    "confirmed_source_entries": 25646, "unique_confirmed_papers": 25613,
    "unresolved_source_entries": 8481, "original_version_count": 51410,
    "unknown_version_dates": 0, "conflicting_version_dates": 0}.items():
    assert funnel[key] == value
pages, licenses = Counter(), Counter()
versions_total = memberships = 0
for paper in records:
    assert paper["S2_assessed"] is False
    assert paper["content_screening_status"] == "not_performed"
    versions = paper["versions"]
    assert [v["version"] for v in versions] == list(range(1, paper["frozen_N"] + 1))
    versions_total += len(versions)
    memberships += len(paper["source_entries"])
    assert all(s["review_scores"] is None for s in paper["source_entries"])
    dates = {}
    for v in versions:
        assert v["date_status"] == "known" and v["date_unresolved_reason"] is None
        t = datetime.fromisoformat(v["submitted_at"].replace("Z", "+00:00"))
        assert t.utcoffset() == timedelta(0)
        dates[v["version"]] = t
        p = v["page_count"]
        pages[p["status"]] += 1
        if p["status"] == "verified":
            assert type(p["value"]) is int and p["value"] > 0
            assert p["source_pdf_sha256"] == v["pdf"]["sha256"]
            assert p["source_pdf_sha256"] and p["unresolved_reason"] is None
        else:
            assert p["status"] == "unresolved"
            assert p["value"] is None and p["unresolved_reason"]
        license_info = v["license_metadata"]
        licenses[license_info["status"]] += 1
        if license_info["status"] == "unknown_historical_version":
            assert license_info["license"] is None
    witness = paper["temporal_eligibility"]
    chosen, edges = witness["witness_versions"], witness["witness_edges"]
    assert witness["status"] == "eligible"
    assert len(chosen) >= 3 and chosen == sorted(set(chosen))
    assert len(edges) == len(chosen) - 1
    for (a, b), edge in zip(zip(chosen, chosen[1:]), edges):
        gap = (dates[b] - dates[a]).total_seconds()
        assert gap > 2592000 and gap == edge["gap_seconds"]
        assert (a, b) == (edge["from_version"], edge["to_version"])
assert (versions_total, memberships) == (14086, 3738)
assert pages == {"verified": 2679, "unresolved": 11407}
assert licenses == {"reported_in_latest_record_context": 3736,
                    "unknown_historical_version": 10350}
assert d["statistics"]["page_count_filter_applied"] is False
assert d["statistics"]["license_filter_applied"] is False
print("PASS: source files, exact reconstruction, counts, gaps and nulls.")
PYCODE
```

## Provenance and existing workflows

The MBZUAI source is `/fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/releases/candidate_index_v1_3_20260911/v1.3.json`; its source archive is the sibling `candidate_index_v1_3_20260911.tar.gz` under `releases/`. `raw_provenance`, `protocol_snapshot`, `page_metadata_snapshot`, `license_evidence_sources` and `packager_provenance` preserve evidence references, hashes and execution scope. Embedded S2 prompts were not executed for this release.

PDFs, raw JSONL/XML/HTML sources, unresolved queues and full temporal audits remain outside Git. PDF paths resolve under the MBZUAI `AI-reviewer/` directory; source locators resolve under its `raw_v13_20260911/` bundle. Other paths may identify earlier A800 evidence. Rechecking absent sources requires those external inputs.

[v1.2.json](v1.2.json) remains for compatibility; the [v1.1](https://github.com/Misaki-Wang/reviewer-self-ego/tree/v1.1) and [v1.2](https://github.com/Misaki-Wang/reviewer-self-ego/tree/v1.2) tags preserve earlier archives. The existing [preparation utility](utility/README.md) expects the frozen **v1.1 input schema**, recoverable with `git show v1.1:v1.1.json > v1.1.json`; v1.3 is not a direct substitute. The separate [PDF comparison workflow](check_diff/README.md) consumes prepared PDF pairs. Its code does not imply content judgments exist in this dataset.

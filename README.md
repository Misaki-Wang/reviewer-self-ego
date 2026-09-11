# Reviewer Self-Ego — v1.2 Candidate Archive

This private repository archives **3,724 unique arXiv candidates** with at least three selectable versions and **both successive gaps strictly greater than 30 days**. It contains only this README and [v1.2.json](v1.2.json), retaining complete original candidate records and additive manuscript-license metadata.

The union is **1,437 original candidates + 2,301 additional-source candidates − 14 overlaps**: **3,738 source-collection entries** and **14,086 distinct paper/version combinations**. **No license filtering was applied.** This is metadata and temporal screening, not evidence of substantive revision, scientific improvement or reviewer-induced changes.

The unchanged [v1.1 Git tag](https://github.com/Misaki-Wang/reviewer-self-ego/tree/v1.1) preserves the earlier archive. Its 1,437 papers represent the **>30-day sensitivity view** of upstream screening v1.1, whose baseline used **>7 days**. Archive naming does not change either threshold.

## Sources and coverage

Counts are publication entries. The 14 overlapping papers occur in an original venue and TMLR; the JSON counts each arXiv ID once.

| Collection | Source | Qualifying entries |
| --- | --- | ---: |
| Original | ICLR 2026 | 706 |
| Original | ICML 2026 | 529 |
| Original | ACL 2026 | 202 |
| Added | ACL 2025 | 174 |
| Added | CVPR 2025 | 177 |
| Added | EMNLP 2025 | 167 |
| Added | ICCV 2025 | 193 |
| Added | ICML 2025 | 435 |
| Added | NeurIPS 2025 | 673 |
| Added | TMLR, May 2025–September 2026 retrieval | 482 |
| **Total** | **3,724 unique papers** | **3,738** |

The seven new official frames contain **20,063 entries**, with **15,254 confirmed arXiv matches** and **4,809 unresolved identities**. Every frame entry was processed. Complete official-frame coverage does **not** imply exhaustive arXiv matching recall: unresolved identities are unknowns, not temporal failures.

Conference frames cover current public main-research lists, excluding Findings, position, invited-journal and other tracks where applicable. Program/proceedings differences and historical acceptance-count discrepancies remain documented in source metadata. TMLR dates have **month precision**; September is incomplete as of retrieval.

## Collection and selection

1. Preserve the original candidates and matching/date evidence; the [v1.1 README](https://github.com/Misaki-Wang/reviewer-self-ego/blob/v1.1/README.md) describes that pipeline.
2. Collect complete official lists for the seven additional sources and reconcile presentation-event duplicates. Match against **Cornell arXiv metadata snapshot v302**, updated September 5, 2026, using official identifiers/DOIs or conservative title-and-author agreement. Keep ambiguities unresolved. Refresh recent official arXiv API metadata and validate complete version histories.
3. For original indices `i < j < k`, require **both** `t[j] - t[i] > 2,592,000 seconds` and `t[k] - t[j] > 2,592,000 seconds`. Exactly 30 days fails. Selected versions **may skip intermediates**, such as `v1 → v3 → v5`. Preserve all dated versions, the lexicographically first qualifying triple and its qualifying-triple count.
4. Deduplicate by confirmed arXiv ID, preserving every original entry. Keep native oral/poster/spotlight/highlight labels, awards and certifications separate. Unavailable presentation fields remain null. OpenReview review-score requests encountered an access challenge; no scores were inferred.
5. Append license observations from the pinned snapshot and official **OAI-PMH arXivRaw** records from September 4 through continuation-token exhaustion. This scan covered **14,984 records**, retained 21 candidate observations and found **two license-URI changes** since the snapshot. No candidate, version or temporal selection was removed or rewritten.

## Metadata and license scope

Each `records` entry contains `arxiv_id`, `origin_collections`, `source_ids`, `license_metadata` and **`original_candidate_records`**. The latter retains complete `v1_1_index` and/or `new_source_expansion` entries: original metadata, actual source URLs, histories, selections and provenance. New venue metadata is inside each original entry's `publications` array.

`license_metadata.license` preserves the source `raw_value` and URI, with normalized `canonical_uri`, `code`, readable `name` and `spdx_id` where applicable. The arXiv nonexclusive license has no invented SPDX identifier. **Descriptive metadata's CC0 license is separate from each manuscript's reported license.**

All **3,724 candidates** have reported latest-record licenses. Versions can have different licenses: **3,736** retained versions have observations in which that version was the latest-record context; **10,350 remain explicitly unknown**. `exact_version_page_verified` is false for this bulk pass. These annotations neither assess reuse permission nor describe publisher copies, code or datasets.

## Download and verification

Use an authenticated account with repository access and select `git checkout v1.2`. The JSON is an exact copy of the A800 export: **102,321,543 bytes**. From the repository root, run:

```sh
python3 - <<'PYCODE'
import hashlib, json
from collections import Counter
from datetime import datetime
from pathlib import Path

raw = Path("v1.2.json").read_bytes()
assert len(raw) == 102321543
assert hashlib.sha256(raw).hexdigest() == "90dbcce7ffa6013eea194f14d5afc0123fc77d26ff0834b209b69bdc89013f99"
data = json.loads(raw)
assert len(data["records"]) == data["record_count"] == 3724
assert len({r["arxiv_id"] for r in data["records"]}) == 3724
origins, sources = Counter(), Counter()
for paper in data["records"]:
    originals = paper["original_candidate_records"]
    assert set(originals) == set(paper["origin_collections"])
    preserved_sources = set()
    for origin, entry in originals.items():
        origins[origin] += 1
        assert entry["arxiv_id"] == paper["arxiv_id"]
        assert "license_metadata" not in entry
        labels = ([entry["venue"]] if origin == "v1_1_index" else
                  [p["source_id"] for p in entry["publications"]])
        sources.update(labels)
        preserved_sources.update(labels)
        dates = {v["version"]: datetime.fromisoformat(
            (v.get("submitted_at") or v["created_utc"]).replace("Z", "+00:00"))
            for v in entry["version_history"]["versions"]}
        example = entry["selected_three_version_example"]
        chosen = example["selected_versions"]
        assert len(chosen) == 3 and chosen[0] < chosen[1] < chosen[2]
        assert len(example["selected_edges"]) == 2
        for (a, b), edge in zip(zip(chosen, chosen[1:]), example["selected_edges"]):
            assert dates[a].tzinfo and dates[b].tzinfo
            gap = (dates[b] - dates[a]).total_seconds()
            assert gap > 2592000 and gap == edge["gap_seconds"]
            assert (a, b) == (edge["from_version"], edge["to_version"])
    assert preserved_sources == set(paper["source_ids"])
    assert paper["license_metadata"]["license_filter_applied"] is False
assert origins == {"v1_1_index": 1437, "new_source_expansion": 2301}
assert sources == {"ICLR2026": 706, "ICML2026": 529, "ACL2026": 202,
    "acl2025": 174, "cvpr2025": 177, "emnlp2025": 167, "iccv2025": 193,
    "icml2025": 435, "neurips2025": 673, "tmlr_2025plus": 482}
print("PASS: archive integrity, candidate/source counts and strict temporal edges.")
PYCODE
```

This standard-library check validates archive integrity and internal source associations. Upstream independent validation additionally compared every original record with its input and checked license observations against raw evidence. PDFs, full source frames, XML/HTML caches and audit programs are **not bundled**. Embedded paths and hashes locate external provenance under `/workspace/yuhang/AI-reviewer/`; they are not repository files. Complete reconstruction requires those inputs. The two-file archive cannot establish missing-identity recall or independently recheck absent sources.

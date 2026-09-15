# source_data inventory

The non-full catalog contains **11 sources, 4,334 unique papers and 16,457 original versions**. ICLR2025 adds 610 papers and 2,371 original versions under the original three-selected-version rule. All ten previous source shards and their records remain unchanged.

The index uses `source-data-index-1`; shards use `source-data-source-shard-1`. The `source_json` pin describes the reconstructed canonical source_data stream. No duplicate combined corpus file is stored. Existing global record order is retained, followed by the new ICLR2025 subset in its source order.

| Source | Papers | All original versions | Selected pairs | Source JSON |
| --- | ---: | ---: | ---: | --- |
| acl2025 | 174 | 637 | 384 | [acl2025.json](acl2025.json) |
| acl2026 | 202 | 761 | 440 | [acl2026.json](acl2026.json) |
| cvpr2025 | 177 | 662 | 378 | [cvpr2025.json](cvpr2025.json) |
| emnlp2025 | 167 | 621 | 375 | [emnlp2025.json](emnlp2025.json) |
| iccv2025 | 193 | 705 | 417 | [iccv2025.json](iccv2025.json) |
| iclr2025 | 610 | 2,371 | 1,410 | [iclr2025.json](iclr2025.json) |
| iclr2026 | 706 | 2,688 | 1,599 | [iclr2026.json](iclr2026.json) |
| icml2025 | 435 | 1,676 | 1,018 | [icml2025.json](icml2025.json) |
| icml2026 | 529 | 1,985 | 1,186 | [icml2026.json](icml2026.json) |
| neurips2025 | 673 | 2,566 | 1,506 | [neurips2025.json](neurips2025.json) |
| tmlr_2025plus | 482 | 1,845 | 1,161 | [tmlr_2025plus.json](tmlr_2025plus.json) |
| **Membership sum** | **4,348** | **16,517** | **9,874** | |
| **Globally deduplicated** | **4,334** | **16,457** | **9,838** | |

A paper is one `arxiv_id`. Fourteen papers belong to both ICML2026 and TMLR2025+, adding 14 paper, 60 original-version and 36 selected-pair memberships. ICLR2025 has no overlap with previous IDs.

Non-full eligibility requires at least three chronologically ordered selected original versions. Each consecutive selected gap is strictly greater than 2,592,000 seconds (30 days). Intermediate versions may be skipped. The earliest-valid greedy algorithm gives the maximum selected sequence; each adjacent selected transition is a pair. All original versions remain, including skipped versions.

## ICLR2025 provenance

[The extension record](provenance/iclr2025_nonfull_20260915.json) pins the existing 1,900-paper full source, verifies the 610-paper subset independently by dynamic programming, and lists its full selected chains. Another 1,290 records do not meet this stricter temporal rule. The acquisition's 777 unresolved identities remain unresolved, not temporally negative.

The new source preserves the acquired records, including their original eligibility fields. The three-version membership is independently certified by the shard/index rule and [catalog filter](provenance/iclr2025_min3_20260915.json). A historical two-version witness inside a retained record is not the catalog selection rule. The metadata extension makes no changes to these installed records.

The coordinated full catalog uses the original source as an explicit base and a disjoint two-version remainder. Its resolved records and order remain unchanged. This metadata extension preserves that installed representation.

## Historical observations and new unknown rows

[The original build](provenance/build_metadata.json) retains the historical ten-source, 3,724-paper, 14,086-version provenance. Tests continue reconstructing its exact historical bytes independently of the active extension.

The original 14,086-row `metadata/version_metadata.jsonl` is preserved as an exact byte prefix. The 2,371 appended rows declare `catalog_extension_without_pdf_observation`, unresolved availability and null PDF hashes, sizes and physical page counts with explicit reasons. This extension downloads or observes no PDFs.

[The original metadata provenance](provenance/iclr2025_nonfull_20260915.metadata_before.json) is preserved byte-for-byte. `metadata/statistics.json` and `metadata/unavailable_versions.json` also retain their bytes and historical observation scope. The current `metadata/provenance.json` pins the extended ledger and separates the prior audit facts from unobserved additions; historical statistics are not fresh observations.

Formal preparation uses the existing ledger interface without a missing-ledger bypass or runtime code change. It rechecks actual PDF availability and page counts for each new run. Temporal eligibility does not establish scientific change. Catalog maintenance makes no model calls.

See the [preparation workflow](../../utility/README.md).

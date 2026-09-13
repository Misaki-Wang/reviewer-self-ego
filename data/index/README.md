# source_data inventory

Active packaging uses dataset source_data, source-data-index-1 and source-data-source-shard-1.
The index's source_json describes the deterministic reconstructed source_data.json stream;
no second full corpus file is stored. All 3,724 paper records and 14,086 original-version
records, source memberships and ordering match the retained original source bytes.

[provenance/build_metadata.json](provenance/build_metadata.json) preserves the complete
original index metadata and shard headers, including historical protocol, license and page
provenance. Historical active/status labels inside that document describe the old build and
are not current preparation policy. The four files under metadata/ remain byte-identical;
their original observation/schema labels are retained evidence. Newly generated observations
use source-data-observed-original-pdf-metadata-1.


These counts cover **all 10 full source JSON shards** listed in [index.json](index.json), not the 200-paper ACL pilot. The globally deduplicated inventory contains **3,724 paper units and 8,428 planned pairs**. Counts were verified offline on 2026-09-13 against the index's shard SHA-256 hashes, byte sizes, record counts, source memberships and global record order.

| Conference / source | Units | Planned pairs | Source JSON |
| --- | ---: | ---: | --- |
| ACL 2025 | 174 | 384 | [acl2025.json](acl2025.json) |
| ACL 2026 | 202 | 440 | [acl2026.json](acl2026.json) |
| CVPR 2025 | 177 | 378 | [cvpr2025.json](cvpr2025.json) |
| EMNLP 2025 | 167 | 375 | [emnlp2025.json](emnlp2025.json) |
| ICCV 2025 | 193 | 417 | [iccv2025.json](iccv2025.json) |
| ICLR 2026 | 706 | 1,599 | [iclr2026.json](iclr2026.json) |
| ICML 2025 | 435 | 1,018 | [icml2025.json](icml2025.json) |
| ICML 2026 | 529 | 1,186 | [icml2026.json](icml2026.json) |
| NeurIPS 2025 | 673 | 1,506 | [neurips2025.json](neurips2025.json) |
| TMLR 2025+ | 482 | 1,161 | [tmlr_2025plus.json](tmlr_2025plus.json) |
| **Sum of source memberships** | **3,738** | **8,464** | |
| **Globally deduplicated** | **3,724** | **8,428** | |

A **unit** is one distinct `arxiv_id` (paper). Each source counts its own distinct papers. Fourteen papers belong to both ICML 2026 and TMLR 2025+, adding 14 unit memberships and 36 pair memberships to the source-row sums. Global totals count each paper, and each `(arxiv_id, from_version, to_version)` pair, once. Duplicate records across shards have identical metadata.

A **planned pair** is one consecutive transition in the earliest-valid, maximum-cardinality selected sequence. Selection uses every original version through `frozen_N`, ordered by UTC submission time and original version ID. Every selected gap must be **strictly greater than 2,592,000 seconds (30 days)**; intermediate original versions may be skipped. An eligible sequence has `k >= 3` selected versions and contributes exactly `k - 1` pairs. Counts include neither all pairwise combinations nor just the two edges of a three-version eligibility witness. An independent dynamic program reproduced every selected sequence.

All 3,724 units in these shards are temporally eligible and have resolved selection. Their frozen histories retain all 14,086 original versions, including skipped versions. Selected sequence lengths range from 3 to 8. These counts use the supplied frozen metadata without a live arXiv refresh.

Planned pairs may lack PDFs. These are metadata-derived comparison counts, not counts of completed or currently runnable comparisons. No current PDF availability or usability was checked, and temporal eligibility does not establish substantive research change. No downloads or model calls were performed for this count.

For fixed-scope planning and cached-PDF materialization, see the [source_data preparation workflow](../../utility/README.md).

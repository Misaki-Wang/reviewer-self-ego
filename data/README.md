# data: three-version source catalog

This catalog retains an ordered sequence of at least three original versions.
Each adjacent selected interval must be strictly greater than 2,592,000 seconds;
intermediate original versions may be skipped. Every original version remains
in metadata and in the requested PDF inventory.

The original ten source shards and their 3,724 records remain byte-for-byte
unchanged. ICLR2025 adds 610 records meeting the same three-version membership
rule. The catalog contains 4,334 unique papers, 9,838 selected pairs and 16,457
original versions. Source totals include fourteen shared ICML2026/TMLR records.

| Source | Retained papers | Selected pairs | All original versions |
| --- | ---: | ---: | ---: |
| acl2025 | 174 | 384 | 637 |
| acl2026 | 202 | 440 | 761 |
| cvpr2025 | 177 | 378 | 662 |
| emnlp2025 | 167 | 375 | 621 |
| iccv2025 | 193 | 417 | 705 |
| iclr2025 | 610 | 1,410 | 2,371 |
| iclr2026 | 706 | 1,599 | 2,688 |
| icml2025 | 435 | 1,018 | 1,676 |
| icml2026 | 529 | 1,186 | 1,985 |
| neurips2025 | 673 | 1,506 | 2,566 |
| tmlr_2025plus | 482 | 1,161 | 1,845 |
| **Total (unique)** | **4,334** | **9,838** | **16,457** |

## ICLR2025 proof and reuse

The 610 ICLR2025 records are reused unchanged from the existing two-version
catalog. Their historical two-version witnesses remain true observations and
are not selection caps. The stricter three-version membership is independently
proved from complete frozen histories in the
[hash-pinned proof](index/provenance/iclr2025_min3_20260915.json). Both the catalog
index metadata and the ICLR2025 shard pin this proof. It records the complete
selected sequence, all selected edges and a three-version witness for every paper.

The remaining 1,290 ICLR2025 papers qualify only for the two-version catalog.
The full view resolves the shared 610 records directly from
[index/iclr2025.json](index/iclr2025.json), with no duplicate canonical metadata.
Every data record is an identical subset of the resolved data_full catalog.

The ICLR2025 source retains the previously verified frozen source identity and
history scope. Another 777 published-source identities remain unresolved.
EMNLP2026 has no verified current public roster and is not represented as empty.

PDF downloads and physical file availability are audited separately. The original
data/pdf files remain unchanged; new ICLR2025 PDF files may reuse validated
data_full/pdf bytes through hard links. No model calls are authorized by this
catalog update.

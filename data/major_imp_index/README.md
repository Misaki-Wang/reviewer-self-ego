# Major-improvement index by source

Derived from the published data check_diff release data_check_diff_combined_20260916.
Source commit: 4b2444017c431ab42c03daf4bb7d70e9ee04d7b1.

## Selection

Select completed pairs whose result.improvement.change is exactly "major improvement".
The requested identifier major_improvement denotes that stored label.
Selection uses only status and the improvement field. Each result retains all original
A1/A2 observations, fourteen change dimensions, usage, execution signature, hashes,
source memberships and artifact links from the combined published row.

**1,509 unique pairs / 1,357 unique papers / 1,518 source entries.**
Nine pairs have two source memberships and therefore occur in two source files.
Deduplicate by key before computing cross-source totals or usage.
The selection retains 41 A2_before=no and 26 A2_after=no pair observations.

| Source | Major-improvement pairs | Unique papers |
| --- | ---: | ---: |
| acl2025 | 34 | 33 |
| acl2026 | 49 | 48 |
| cvpr2025 | 39 | 34 |
| emnlp2025 | 45 | 43 |
| iccv2025 | 46 | 44 |
| iclr2025 | 250 | 217 |
| iclr2026 | 255 | 229 |
| icml2025 | 148 | 133 |
| icml2026 | 241 | 220 |
| neurips2025 | 204 | 185 |
| tmlr_2025plus | 207 | 180 |

## Files and provenance

Each source.json contains a counts/provenance envelope and the full pair objects in results.
Rows preserve every field value; JSON formatting and grouping are derived.
Original judgment/raw/receipt links, when present, are relative to the originating
res/published/{package}/ directory. The provenance URL template resolves them at
the pinned source commit independently of the local workspace layout.
Source shard hashes and the combined export hash are recorded in every source file.
SHA256SUMS covers these source files and this README.

This is a data-only derived index. Its creation makes no model calls or scientific reassessments.
The original catalogs and published results retain their bytes.

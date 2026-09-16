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
Rows preserve every original field value and add pdfs.before / pdfs.after references.
Original judgment/raw/receipt links, when present, are relative to the originating
res/published/{package}/ directory. The provenance URL template resolves them at
the pinned source commit independently of the local workspace layout.
Source shard hashes and the combined export hash are recorded in every source file.
SHA256SUMS covers these source files and this README.

This is a data-only derived index. Its creation makes no model calls or scientific reassessments.
The original catalogs and published results retain their bytes.

## PDF references and reserved anonymization state (schema v2)

The selected pairs reference **2,882 distinct original PDF versions**.
Both endpoints are indexed; original before/after integer fields remain unchanged.
Each results[].pdfs.before and results[].pdfs.after object contains:

| Field | Meaning |
| --- | --- |
| paper_version_id | Stable arXiv ID with version suffix; deduplicate by this across all pairs and sources. |
| version | Original arXiv version number. |
| original_pdf_path | POSIX path relative to the MBZ AI-reviewer project root, e.g. data/pdf/2202.03482/2202.03482v1.pdf. |
| original_pdf_available | Whether the original file was found at the recorded inventory time. |
| original_pdf_bytes | File size observed during the inventory. |
| is_anonymized | Initially false: no verified anonymized copy is linked by this index. |
| anonymization_status | Initially not_started; allowed values: not_started, in_progress, completed, failed. |
| anonymized_pdf_path | Initially null; fill with the verified anonymized PDF path relative to the project root. |
| anonymization_report_path | Initially null; fill with the matching completed anonymization receipt/report path. |

The original files live on MBZ; Git and the local index mirror do not contain the PDFs.
Resolve original_pdf_path against the MBZ project root or a mirror with the same layout,
not against the index JSON directory. The inventory checks existence, nonzero size and
the %PDF- header; it does not rerun PDF parsing, scientific screening, anonymization or review.
Availability is a timestamped observation. Recheck the file before processing it.

False does not assert that no anonymized copy exists elsewhere: it describes this index's
linked workflow state. Set is_anonymized=true and status=completed only after validating
the anonymized output and its completed receipt against the original input. Keep false
for not_started, in_progress or failed states. Use fresh output/report paths under an
experiment directory outside data/, data_full/ and other original stores, as required by
utility/anonymize; preserve the original PDF bytes. Only the nullable fields are reserved
here; no anonymization or review has been started.

A PDF version may occur in multiple pair rows and source files. Process it once by
paper_version_id, then update every corresponding reference consistently. Failed or
unvalidated outputs must not be marked completed. Regenerate SHA256SUMS after state edits.
The v1 enrichment helper is intentionally one-shot and refuses existing v2 state,
preventing future refreshes from silently resetting completed anonymization work.

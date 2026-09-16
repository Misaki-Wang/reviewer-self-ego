# Major-improvement papers by source

This index supports selecting paper versions, locating their original PDFs, tracking
anonymization, and preparing subsequent reviews. Schema: check-diff-major-improvement-index-3.

## Coverage and selection

Select completed check_diff pairs whose result.improvement.change is exactly
"major improvement". The index contains **1,509 unique pairs from 1,357 papers**,
with **1,518 source memberships and 2,882 distinct original PDF versions**.
Nine pairs belong to two sources. Deduplicate by key when combining pair statistics,
and by paper_version_id before processing PDFs.

| Source | Pairs | Papers |
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

## Retained experimental data

Each source.json includes selection, counts, portable PDF conventions, source-content
SHA-256 hashes, and results. Pair rows retain paper identity/title, before/after versions,
source memberships, completion status, labels and the complete scientific result:
A1, per-version A2, all fourteen independent change dimensions, improvement, and their
justifications. No judgments are reclassified. The selection retains 41 A2_before=no
and 26 A2_after=no observations among unique pairs.

Publication metadata excludes account-bearing run names, raw response and execution
record links, repository-owner URLs, execution identifiers, retry/timing/token telemetry,
server names and server-specific availability observations. Source-content hashes remain
for integrity checking. Original operational records are outside this derived index.
SHA256SUMS covers all source JSON files and this README.

## PDF references and anonymization state

Both results[].pdfs.before and results[].pdfs.after contain:

| Field | Meaning |
| --- | --- |
| paper_version_id | Stable arXiv ID with version suffix; use for PDF deduplication. |
| version | Original arXiv version number. |
| original_pdf_path | Path relative to the configured project root, e.g. data/pdf/2202.03482/2202.03482v1.pdf. |
| original_pdf_bytes | Expected original file size; recheck the file before processing. |
| is_anonymized | Whether this index links a verified anonymized PDF with a completed receipt. |
| anonymization_status | not_started, in_progress, completed or failed. |
| anonymized_pdf_path | Relative path to the verified anonymized PDF; null until available. |
| anonymization_report_path | Relative path to its completed validation receipt; null until available. |

The PDF files are not bundled with this index. Configure the project root containing
data/pdf; resolve paths from that root, not from the index directory. The existing inventory
checked file existence, size and PDF headers. Consumers must recheck current files.

The initialized workflow state is false / not_started / null. False means no verified
copy is linked here; it does not assert that no anonymized copy exists elsewhere.
Preserve workflow state on refresh. Mark completed/true only after verifying the output
and the receipt's binding to its input. Keep false for pending or failed processing.
Use explicit output paths in a fresh experiment directory outside original PDF stores.
Store portable, account-neutral relative paths; keep private execution logs separate
from shareable PDF references. Update every occurrence of a shared paper_version_id
consistently and regenerate SHA256SUMS whenever the index changes.

Paper titles and arXiv identities are intentionally retained for archive lookup. This
is a paper archive, not a blinded reviewer manifest. Prepare a separate blinded manifest
and supply anonymized PDFs when conducting blind reviews.

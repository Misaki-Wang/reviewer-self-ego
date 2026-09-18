# Five-conference experiment data

This directory is the destination for subsequent five-conference experiments.
Current imported sources: ICLR2026, ICML2026, ACL2026. CVPR2026 and ECCV2026
remain empty placeholders, not completed zero-paper catalogs.

## Snapshot

Merged 5,383 paper records and 14,580 original-version records
from data and data_extra without changing paper metadata or scientific judgments.
Copied 14,553 available original PDFs; 27 original-version
records have no available scoped file and remain explicitly missing.
See index/versions.jsonl for current paths, availability, and copied-byte hashes.
Historical record-level PDF paths are retained provenance, not current input paths.

## Layout

- index/index.json: standalone, hash-pinned source catalog.
- index/<source>.json: complete original paper metadata and version histories.
- index/metadata/: current missing-version list and historical observations.
- pdf/<arxiv_id>/<paper_version_id>.pdf: copied original PDF files.
- anonymized/major_imp_index/: merged major-improvement judgments and relocated PDF references.
- anonymized/pdf/<source>/: copied existing anonymous PDFs, including files not yet linked as validated.
- review/<source>/<model>/: exact historical review JSONs; original settings and embedded references remain unchanged.

Historical review outputs do not become medium-effort results through copying.
Original anonymity receipts stay under res/anonymization_records, relative to
the project root. 88 linked receipts were not readable by the merging
OS account; inherited status is preserved, not newly certified. Copied PDFs
were independently SHA-256 verified. File existence does not establish anonymity.

Merge provenance and input snapshots: index/provenance/merge_20260918T073842Z.
No models were called, no PDFs were downloaded, and source files were not edited.

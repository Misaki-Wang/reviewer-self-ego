# v1.4: whole-unit PDF size filter

This release retains 3,692 of 3,724 v1.3 paper units, with 13,965 original versions. It excludes 32 units containing 60 PDFs larger than 50 MB.

- One MB means 1,000,000 bytes. Exclude a whole unit if any original version PDF is strictly larger than 50,000,000 bytes; equality qualifies.
- Check every original version, including versions skipped by temporal selection. Do not delete individual versions, resample, or recompute temporal eligibility.
- Preserve each retained paper object exactly as archived in v1.3, including its original page, PDF, license, source, and temporal metadata. The separately hashed `pdf_metadata.jsonl` contains the newer byte/page observations.
- Officially unavailable PDFs retain null sizes and their exact-version evidence. They do not by themselves exclude a unit. 23 such versions in 22 retained units remain explicitly unavailable; this archive does not claim they are executable inputs. Unexplained missing data or conflicting evidence aborts packaging.
- The inherited temporal requirement remains an ordered subsequence of at least three original versions with each consecutive selected gap strictly greater than 30 days. Intermediate original versions may be skipped.
- No S2/content judgments, license filter, page-count filter, model calls, PDF downloads, or PDF copies were performed by this archival operation. Historical releases are unchanged.

## Files and reconstruction

`v1.4.json` is the combined index. `statistics.json` separates unchanged frozen page observations from the newer PDF metadata supplement. `size_filter_audit.json` covers every parent unit; `excluded_units.json` preserves each excluded paper object and its size evidence. `pdf_metadata.jsonl` covers every original version of each retained unit.

`index.json` contains complete dataset metadata, source shard hashes, supplemental artifact hashes, and `record_order`. To reconstruct the combined index, verify shard hashes, union the records in `sources/*.json` by arXiv ID, reject unequal duplicates, order by `record_order`, and add that list as `records` to `dataset_metadata`. Source membership overlaps are intentional; do not sum shard counts as unique papers. The reconstructed JSON object must equal `v1.4.json`.

The input combined index SHA-256 is `9a908aede8b2b30b29db3668baa0db69095c8550aa8adb78ba48282770751cba`. The input observation ledger SHA-256 is `1901dffeb0ef11a95317fa112dd71dc0937eb6e3a2897472ab0218e018d38422`. Artifact hashes and the packager hash are recorded in the manifests. This is a metadata archive; original PDFs are not included.

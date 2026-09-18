# data_extra: supplement to the original data catalog

`data_extra` contains the globally deduplicated paper-set difference `data_full - data`, using `arxiv_id` as paper identity. It contains 10,997 papers and all 25,488 original versions. Every retained record and source membership is copied exactly from the full catalog. It is disjoint from `data`; their union reconstructs the complete `data_full` records in the full index's order.

## Layout

- `index/index.json`: standalone index without external base references.
- `index/<source_id>.json`: 11 source shards; a paper with multiple memberships appears identically in the relevant shards and is deduplicated by the index loader.
- `index/versions.jsonl`: all expected extra-only original version paths.
- `index/statistics.json`: unique and source-membership counts.
- `index/provenance/`: pinned source catalogs, input hashes and derivation evidence.
- `pdf/<arxiv_id>/<arxiv_id>vN.pdf`: available original PDFs materialized without extra corpus downloads.
- `pdf_inventory.jsonl` and `pdf_summary.json`: availability snapshot and explicit unresolved versions, when materialization completes.

## Reading and preservation

The existing generic source loader can read `data_extra/index/index.json` and filter it by source. The current formal workflow CLI accepts `data` and `data_full`; this directory-only addition does not change its catalog options or execution settings.

Preserve complete original version histories, including versions skipped by temporal selection. Catalog membership does not imply that a PDF has been downloaded or verified. Future downloads do not automatically update this snapshot's availability inventory.

Materialized PDFs may share filesystem storage with validated originals through hardlinks. Treat them as read-only: do not edit their bytes or change their permissions. Downloader atomic replacement leaves a linked snapshot unchanged. No existing catalog, original PDF, execution manifest, run, or model output is modified by this split.

## Final snapshot status

The final completion marker is `COMPLETED.json`. It pins the standalone index, the final per-version PDF inventory and the final summary. Read these artifacts together.

The snapshot at 2026-09-15T16:39:10Z contains 16,090 hash-verified original PDFs. Another 9,391 versions have no frozen download receipt, and seven have failed HTTP 404 receipts. Those 9,398 records are retained and unresolved. All 26 successful receipts with absent page version stamps were accepted under the original validator policy; null stamps and original frozen-SHA verification flags remain unchanged.

Existing original PDF bytes and permissions are preserved. Hardlink creation adds directory entries and link counts, not another copy of the PDF data. Later download progress is outside this fixed snapshot.

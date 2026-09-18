# Five-conference check_diff responses

This release merges existing results for the currently selected ICLR2026, ICML2026, ACL2026, ECCV2026 and CVPR2026 papers. It performs no model calls and changes no scientific judgments.

| Conference | Papers | Pairs | Completed | Other terminal status |
| --- | ---: | ---: | ---: | --- |
| ICLR2026 | 2372 | 3265 | 3252 | 13 input_unavailable |
| ICML2026 | 2239 | 2896 | 2892 | 3 input_unavailable, 1 provider_refused |
| ACL2026 | 772 | 1010 | 1004 | 6 input_unavailable |
| ECCV2026 | 633 | 776 | 775 | 1 blocked_input |
| CVPR2026 | 943 | 1145 | 1143 | 2 blocked_input |

## Layout

- `<conference>/pairs.jsonl` retains every selected historical pair, including unavailable inputs and the provider refusal. Complete results and original source-row fields are retained. New publication metadata is isolated under `publication`.
- `<conference>/responses/<pair-key>.json` and `judgments/<pair-key>.json` are exact accepted raw response and canonical judgment bytes. `publication.artifacts` contains package-relative links, byte hashes and source locators. Original `links` fields in historical rows remain source-relative provenance, not links into this package.
- `<conference>/attempts.jsonl` contains every known selected attempt, uniquely identified by source run, pair key and attempt number, with reported usage preserved exactly. The two ICML refusal attempts remain present with unknown usage.
- `<conference>/papers.jsonl` lists every selected paper and every original version date/page observation. This is an explicitly reduced metadata view; full source catalogs remain Git- and SHA-pinned in `source_manifest.json`.
- `contracts/<source>/` preserves the exact schema, comparison prompt and rubric for each source batch.
- `evidence/` contains only explicitly allowlisted historical audit, accounting, refusal, snapshot and runtime-qualification records. Native PDFs, credentials, caches, and private attempt diagnostics are excluded.
- `verification/` preserves two exact independent source audit reports; `provenance/build_publication.py` is the offline builder used for this release.
- `summary.json` gives status, input-batch and label counts. Counts sum conference memberships, not deduplicated papers across venues. Token totals are not recomputed; retained whole-source accounting artifacts may cover additional conferences.
- `source_manifest.json` identifies source file hashes, source Git commit or remote snapshot pins, and builder hash. `SHA256SUMS` covers every other file in this package.

## Provenance and limitations

The source checkout and five-conference catalog are frozen at `c15ad832d66dbd625a64a303554142b392e18f27`. ICLR2026 merges `iclr2026_full_20260913` and selected `data_extra` rows. ICML2026 and ACL2026 merge selected `check_diff_remaining_sources_20260914` and `data_extra` rows. ECCV2026 and CVPR2026 use their complete frozen September 18 runs.

- ICML2026 pair 2510.17759__v1_v2 has terminal provider_refused status and no scientific judgment. Both known attempts have unknown, unreported consumption.
- The data_extra delivery retains an incomplete runtime process-quiescence proof. Finalized run records establish processing terminality; global process termination is not certified.
- CVPR2026 original input receipts omit arxiv_id. The retained read-only sidecar derives identity from frozen evidence; original receipts remain unchanged and the original strict verifier is not claimed to have passed.
- Historical pair ledgers are authoritative. Temporal witness_versions is not a complete historical pair selection. Every published pair endpoint was checked against the current pinned catalog and has a strict gap above 2592000 seconds.
- Source scientific contracts are retained separately. Contract hashes may differ across base and later batches; this publication does not harmonize prompts or labels.
- Historical attempt receipt projections omit host/account/runtime diagnostics. Base exports retain embedded attempts without standalone original receipt file hashes; execution-file hashes and JSON pointers identify these records.
- Scientific A1, per-version A2, all fourteen independent dimensions and overall improvement labels are unchanged. No A2 filtering or aggregate S2/T3 decision is introduced.

## Validation

The offline builder rejects duplicate or out-of-scope pairs, missing results, mismatched source/copy hashes, missing endpoints, nonqualifying temporal gaps, incomplete attempts, raw/canonical disagreement, schema failures and unexpected coverage counts. ECCV2026/CVPR2026 pairs must exactly match current catalog selected_edges. All known source statuses and all original labels are preserved.

From this directory, verify file integrity with `shasum -a 256 -c SHA256SUMS`.

# ICLR2026 selected experiment results

[Download the self-contained HTML report](../../../html/ICLR2026-check-diff-summary.html) and open it in a browser. It includes all 1,588 validated pair previews, result distributions, timing, reported token usage and the exact comparison prompt/rubric. The GitHub file viewer shows HTML source; use its download button to view the interactive report locally.

## Coverage

- 706 fixed source_data paper units; 1,599 planned adjacent selected-version pairs.
- 1,588 validated judgments and 11 exact-version PDF-unavailable pairs.
- 699 fully judged units and 7 units affected by unavailable inputs; all 706 units accounted for.
- Requested model: gpt-6-astra; reasoning effort: max; native Codex CLI 0.154.0.
- Final independent artifact audit: passed, zero errors.
- Source content revision: ee418c22f5ce435f179a769aa1a3fe7b2be341b9ea5a7f30b5647bcc4bd2e887

## Selected files

| Content | Files | Bytes |
| --- | ---: | ---: |
| Validated judgment JSON | 1,588 | 10,250,194 |
| Per-pair execution records | 1,588 | 15,438,383 |
| Raw final model responses | 1,588 | 10,248,606 |
| Summaries, configuration, verification and frozen prompt/schema | 14 | 1,919,130 |
| Original selected payload | 4,778 | 37,856,313 |

All selected files preserve the original MBZ bytes. SHA256SUMS covers these 4,778 files. EXPORT.json identifies this selection. README.md, EXPORT.json, SHA256SUMS and the HTML are generated publication metadata, additional to the 37.86 MB payload.

## Time and token accounting

Successful attempt elapsed sums to 254.41 hours across parallel calls (median 9 minutes 51 seconds). Failed-attempt durations, queue waits and the authentication outage are excluded from that sum. Attempt elapsed still includes PDF tools and response validation; pure API inference time was not separately instrumented. The comparison stage after authentication recovery spans about 3 hours 29 minutes.

Reported usage is 1,940,142,856 tokens, including 1,744,313,984 cached input tokens. Cached input is already included in input; reasoning output is already included in output. There are 99 failed attempts with no reported usage: 96 initial missing-authentication failures and three capacity failures, all followed by eventual pair success. Their consumption is unknown, not zero.

## Boundaries

This is a selected results export, not a standalone rerun bundle. Full event streams, every individual attempt archive, complete frozen controller/input snapshots and the original PDF store remain on MBZ. Original absolute paths in records are preserved as provenance; they are not portable launch instructions. Scientific judgments have not been checked against human reference labels. No aggregate S2/T3 decision is derived.

See [REPORT.md](REPORT.md), [final_verification.json](final_verification.json) and [operations/independent_completion_check.json](operations/independent_completion_check.json) for recorded checks.

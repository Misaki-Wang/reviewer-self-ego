# ICLR 2026 review pilot release

This release covers the retained five-paper scope of `run-20260913-01`: 18 manuscript versions, 13 original conditions, one replicate, and 234 completed reviews. The evaluator was **gpt-6-astra / max**. **Kimi K3** was an attribution label in the prompt, not a second evaluator.

## Contents

- `results/`: five curated files with complete scores, strengths, weaknesses, condition identities, fingerprints and PDF hashes.
- `raw/<paper-id>/reviews.json`: the five original result files, copied byte for byte. These preserve historical execution fields and server paths; they are archival evidence, not portable launch instructions.
- `execution.jsonl`: 240 deduplicated attempt receipts for the retained five papers, with timing, status and reported token usage.
- `run.json` and `scope.json`: settings, historical source revisions, selected papers and versions.
- `statistics.json`, `analysis.json` and `verification.json`: scope-specific resource totals, descriptive comparisons and original verification summary.
- `prompts/`: the exact historical system and 13 condition prompts with their original manifest. Current production prompts are not substituted.
- `checksums.json`: SHA-256 and byte size for every other release file.
- [Interactive HTML summary](../../../html/review-exp-iclr2026-pilot.html): a standalone offline report. Download the HTML to view it in a browser; GitHub's file view displays its source.

## Accounting and interpretation

The retained scope reports **125,448,678 total tokens** across 234 attempts with reported usage. Six additional attempts have no reported usage; unknown consumption is not zero. Cached input is already part of input, and reasoning output is already part of output, so these subsets must not be added again.

Successful Codex CLI calls sum to **85,829.405 seconds** (23.84 call-hours); all 240 attempts sum to **86,863.950 seconds**. These sums include PDF tool execution, exclude queue/login waiting outside calls, and are neither pure API inference time nor elapsed wall-clock duration. Failed-attempt duration is excluded from the successful-call statistic. Statistics exclude papers removed from the earlier 50/10-paper scopes.

All five papers retained identical OA scores across their versions and conditions (three papers scored 3; two scored 1). All 234 full review results are distinct. This is a descriptive pilot with five papers, one evaluator, one replicate, no expert gold labels, deterministic scheduling and changing concurrency. It cannot establish absence of attribution/context bias. OA is ordinal; paper-weighted score differences are descriptive.

This preserves the original **13-condition experiment**, including separate `exp1_quality` and `exp3_neutral` calls. It is not a rerun of the later 12-condition shared-baseline design or later venue-specific system prompts. Scope was reduced during collection; earlier excluded-paper costs remain in the full MBZ archive and are not assigned to these five papers.

PDFs, runtime dependencies, scratch files, full terminal logs and redundant invocation/history copies remain in the MBZ archive. Raw reviews are intentionally retained in addition to the curated results as requested.

# Multisource review pilot

Run: run-20260914-01. Verified reviews: 4368.
Model: gpt-6-astra; reasoning effort: max; other-model wording: Kimi K3.

`results/<source>/<paper>.json` contains complete parsed reviews and provenance.
`raw/<source>/<paper>/reviews.json` is byte-for-byte identical to the original result JSON, including every retained failed and successful attempt, original raw response strings, and reported usage. No raw result text is rewritten.
`prompts/` contains the exact frozen prompts, manifest, and source notes.
`pilot_config.json`, `inventory.json`, `selection_manifest.json`, `scope_input.json`, `source_snapshot_manifest.json`, and `records/freeze_verification.json` preserve settings, scope, and hashes.
`records/attempt_history/` preserves selected-scope start/finish receipts. `progress.json` and `records/concurrency_events.jsonl` document execution and capacity changes.
`statistics.json` separately reports successful CLI time, all-attempt time, unknown timing/usage, and controller wall intervals. Cached input and reasoning output are subsets and are not added again. Reported totals cover observed usage only; missing usage is unknown, not zero. CLI timings include PDF tools and are not pure API latency.
`analysis.json` contains descriptive per-source distributions (all native categories retained) and paper-equal paired contrasts. Papers are averaged over their selected versions before averaging papers. Incomplete papers never contribute partial-version contrasts. OA scales are ordinal and never pooled across sources. Duplicate papers across sources remain separate assessments.
`review-exp-multisource-pilot-20260914.html` is a self-contained offline report containing full parsed review text and exact prompts. When opened inside this release folder it uses local release-file links. The repository HTML copy resolves the same release through the repository layout.
`SHA256SUMS` covers every other release file. From this folder run `sha256sum -c SHA256SUMS`.

The quality baseline is shared with experiment 3. This twelve-condition run is separate from the historical thirteen-condition pilot. Source-native OA values include ARR half-points; TMLR uses the explicitly chosen experimental 1–4 encoding. No cross-source score mean, causal claim, or absence-of-bias conclusion is supported. One replicate, selected papers, no human validation, and no inferential uncertainty estimates limit interpretation.

Runtime trees, PDFs, temporary CLI workspaces, unrelated outputs, and historical releases are excluded.

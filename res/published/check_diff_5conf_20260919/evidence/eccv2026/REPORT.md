# Final batch verification

Run: /fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/res/check_diff_eccv2026_yao_20260918T0918Z
Verified at: 2026-09-18T11:14:53.767037+00:00
Requested model: gpt-6-astra; reasoning effort: max.

Artifact verification passed for 633 units and 776 planned pairs: 775 completed judgments and 1 pairs with official exact-version no-PDF evidence. No judgment was produced for those unavailable pairs.

| Source | Units | Fully judged units | Settled units | Planned pairs | Judged pairs | Unavailable pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| eccv2026 | 633 | 632 | 633 | 776 | 775 | 1 |

Source rows retain memberships; they must not be summed to infer unique coverage.

Every completed output matched the saved schema, its SHA-256 receipt, the requested model and effort, the frozen execution signature, its two ledger input hashes, and its verified archived result. Archived commands and rendered prompts agreed with those settings.

Token accounting status: complete. 775 attempts were counted once, including failed and interrupted attempts; 0 had no reported usage.

| Reported counter | Sum | Reporting turns |
| --- | ---: | ---: |
| input_tokens | 981795597 | 775 |
| cached_input_tokens | 902469120 | 775 |
| cache_write_input_tokens | 0 | 775 |
| output_tokens | 10666035 | 775 |
| reasoning_output_tokens | 6247681 | 775 |
| total_tokens | 992461632 | derived |
| non_cached_input_tokens | 79326477 | derived |

Input includes cached input; output includes reasoning output. Do not add these subsets again. Cache-write counters are reported separately. Missing counters and missing usage remain unknown; reported sums are not a claim of complete consumption.
Reported turns missing counters: input_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0, output_tokens=0, reasoning_output_tokens=0.
Unknown usage fields retained without adding them to totals: none.

This is an artifact audit, not scientific S2/T3 aggregation or human validation. PDF contents were not reread. Input hashes were compared with the frozen per-version ledger; the runner owns live pre/post PDF checks. Requested settings were verified from receipts and commands, not from an independently observed provider model identity.

File isolation: archived grants and security settings matched the exact pair, private work, and fixed tool resources; policy codex-file-isolation-v1.
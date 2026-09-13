# Final batch verification

Run: /fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/res/iclr2026_full_20260913
Verified at: 2026-09-13T15:32:58.211714+00:00
Requested model: gpt-6-astra; reasoning effort: max.

Artifact verification passed for 706 units and 1,599 planned pairs: 1,588 completed judgments and 11 pairs with official exact-version no-PDF evidence. No judgment was produced for those unavailable pairs.

| Source | Units | Fully judged units | Settled units | Planned pairs | Judged pairs | Unavailable pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| iclr2026 | 706 | 699 | 706 | 1599 | 1588 | 11 |

Source rows retain memberships; they must not be summed to infer unique coverage.

Every completed output matched the saved schema, its SHA-256 receipt, the requested model and effort, the frozen execution signature, its two ledger input hashes, and its verified archived result. Archived commands and rendered prompts agreed with those settings.

Token accounting status: partial. 1,687 attempts were counted once, including failed and interrupted attempts; 99 had no reported usage.

| Reported counter | Sum | Reporting turns |
| --- | ---: | ---: |
| input_tokens | 1917307893 | 1588 |
| cached_input_tokens | 1744313984 | 1588 |
| cache_write_input_tokens | 0 | 1588 |
| output_tokens | 22834963 | 1588 |
| reasoning_output_tokens | 13728062 | 1588 |
| total_tokens | 1940142856 | derived |
| non_cached_input_tokens | 172993909 | derived |

Input includes cached input; output includes reasoning output. Do not add these subsets again. Cache-write counters are reported separately. Missing counters and missing usage remain unknown; reported sums are not a claim of complete consumption.
Reported turns missing counters: input_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0, output_tokens=0, reasoning_output_tokens=0.
Unknown usage fields retained without adding them to totals: none.

This is an artifact audit, not scientific S2/T3 aggregation or human validation. PDF contents were not reread. Input hashes were compared with the frozen per-version ledger; the runner owns live pre/post PDF checks. Requested settings were verified from receipts and commands, not from an independently observed provider model identity.

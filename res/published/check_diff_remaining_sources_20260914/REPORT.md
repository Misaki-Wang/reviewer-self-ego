# Final batch verification

Run: /fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/res/check_diff_remaining_sources_20260914
Verified at: 2026-09-14T18:09:31.999126+00:00
Requested model: gpt-6-astra; reasoning effort: max.

Artifact verification passed for 3,018 units and 6,829 planned pairs: 6,814 completed judgments and 15 pairs with official exact-version no-PDF evidence. No judgment was produced for those unavailable pairs.

| Source | Units | Fully judged units | Settled units | Planned pairs | Judged pairs | Unavailable pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| acl2025 | 174 | 174 | 174 | 384 | 384 | 0 |
| acl2026 | 202 | 199 | 202 | 440 | 434 | 6 |
| cvpr2025 | 177 | 177 | 177 | 378 | 378 | 0 |
| emnlp2025 | 167 | 167 | 167 | 375 | 375 | 0 |
| iccv2025 | 193 | 192 | 193 | 417 | 415 | 2 |
| icml2025 | 435 | 434 | 435 | 1018 | 1016 | 2 |
| icml2026 | 529 | 527 | 529 | 1186 | 1183 | 3 |
| neurips2025 | 673 | 672 | 673 | 1506 | 1504 | 2 |
| tmlr_2025plus | 482 | 482 | 482 | 1161 | 1161 | 0 |

Source rows retain memberships; they must not be summed to infer unique coverage.

Every completed output matched the saved schema, its SHA-256 receipt, the requested model and effort, the frozen execution signature, its two ledger input hashes, and its verified archived result. Archived commands and rendered prompts agreed with those settings.

Token accounting status: partial. 6,963 attempts were counted once, including failed and interrupted attempts; 149 had no reported usage.

| Reported counter | Sum | Reporting turns |
| --- | ---: | ---: |
| input_tokens | 11172272820 | 6814 |
| cached_input_tokens | 10412352512 | 6814 |
| cache_write_input_tokens | 0 | 6814 |
| output_tokens | 112271972 | 6814 |
| reasoning_output_tokens | 69785604 | 6814 |
| total_tokens | 11284544792 | derived |
| non_cached_input_tokens | 759920308 | derived |

Input includes cached input; output includes reasoning output. Do not add these subsets again. Cache-write counters are reported separately. Missing counters and missing usage remain unknown; reported sums are not a claim of complete consumption.
Reported turns missing counters: input_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0, output_tokens=0, reasoning_output_tokens=0.
Unknown usage fields retained without adding them to totals: none.

This is an artifact audit, not scientific S2/T3 aggregation or human validation. PDF contents were not reread. Input hashes were compared with the frozen per-version ledger; the runner owns live pre/post PDF checks. Requested settings were verified from receipts and commands, not from an independently observed provider model identity.

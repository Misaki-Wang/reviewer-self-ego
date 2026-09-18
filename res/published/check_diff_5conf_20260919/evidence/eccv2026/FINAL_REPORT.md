# ECCV2026 check_diff completion

## Material Passport

- Status: completed and audited, with one preserved input-blocked pair.
- Project: `/fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer`.
- Run: `res/check_diff_eccv2026_yao_20260918T0918Z`.
- OS account: `Yaochen.Wang@mbzuai.ac.ae`, UID 2020.
- Actual Codex login: `yaochen.wang@mbzuai.ac.ae`, freshly verified and recorded without copying credentials or the historical Han binding.
- Requested and recorded model: `gpt-6-astra`; reasoning effort: `max`.
- Per-call timeout: 3,600 seconds; attempt limit: two total attempts per pair.
- Slurm job 12575 completed with exit 0:0, from 2026-09-18 09:28:54 UTC to 11:18:15 UTC, elapsed 1:49:21.
- The completion heartbeat is paused.

## Scope and outcomes

The exact frozen catalog contains 633 papers and 776 selected pairs. All 775
runnable pairs completed on their first attempt: 775 accepted judgments,
775 formal assessment calls, zero retries and zero failed pairs. The remaining
pair, `2604.02546__v1_v2`, is `blocked_input` with zero attempts because the
original `2604.02546v2` PDF is officially unavailable. Its null page count and
exact-version absence evidence are retained. No versions or pairs were replaced.

All A1, per-version A2, fourteen independent change dimensions, overall
improvement labels and English justifications are retained. A2-negative results
were not filtered, and no aggregate S2/T3 decision was derived.

| Overall improvement label | Completed pairs |
|---|---:|
| major improvement | 155 |
| minor improvement | 422 |
| no meaningful change | 50 |
| maybe | 107 |
| minor deterioration | 24 |
| major deterioration | 17 |
| Total | 775 |

## Export

The published major-improvement index contains exactly 155 pairs, 151 unique
papers and 306 unique original PDF versions. Every exported row is completed
and has `result.improvement.change == "major improvement"`. Full judgments
are unchanged. Original references use `data_5conf/pdf/`; all anonymous-PDF and
receipt links are null, `is_anonymized` is false, and status is `not_started`.

- Published index: `/fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/data_5conf/anonymized/major_imp_index/eccv2026.json`.
- Index SHA-256: `b429697227d3623648d54b65b9e6eb7a05f12b7483216c4bfdc45fadb7cf2210`.
- Complete consolidated pair results: `/fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/res/check_diff_eccv2026_yao_20260918T0918Z/export_audits/20260918T111803385329Z/all_pairs.json`.
- Complete results SHA-256: `41f922956c79296341e0692d86043a23e79b52cb026f1952e779c71607998974`.
- Raw responses and every execution receipt remain under the run's `attempts/` and `output/` directories.

The shared publication lock was used, previous checksum bytes were retained,
and sibling indexes remained unchanged. No anonymization or review was run.

## Concurrency and usage

The configured range was initial 64, maximum 256. Actual peak concurrency was
128. The reference policy paused new admissions at 64 for native transport
pressure, preserved all healthy calls, recovered through one successful
single-call probe, and later raised admission limits from 64 to 96 to 128.
No attempt budgets, account bindings or scientific settings were reset.

| Reported token counter | Tokens |
|---|---:|
| Input | 981,795,597 |
| Cached input, included above | 902,469,120 |
| Non-cached input | 79,326,477 |
| Output | 10,666,035 |
| Reasoning output, included above | 6,247,681 |
| Cache-write input | 0 |
| Total input plus output | 992,461,632 |

All five raw usage fields were reported for all 775 terminal turns. There are
zero attempts with missing usage and zero unknown dispatch outcomes. These are
reported counters; cached input and reasoning output are not added twice.

## Verification and limits

Native preflight passed 31 tests and the exact-command sandbox probe, including
two-PDF isolation, denied unrelated files/auth/symlink access, disabled network,
private rendering and the selected runtime. The final native verifier and
export audit passed with no errors or pending artifacts.

An independent final audit checked all 775 full schemas and raw-to-result,
canonical-output and receipt linkages, 4,661 terminal input pins, 5,425 receipt
artifact pin checks, 7,777 distinct artifact files, all six submission pins,
and the exact published major subset. All 306 exported original PDFs were
independently rehashed, totaling 4,781,201,092 bytes. The exporter separately
rehashed all 1,408 available selected original PDFs. A separate accounting pass
summed every raw terminal usage field and reproduced the totals above exactly.

The independent final auditor did not reread the other 1,102 selected original
PDFs; their coverage comes from the exporter's full rehash and frozen pins.
This verifies structure, provenance and reported usage, not the scientific
correctness of judgments. Actual CLI commands enforce and record the requested
model and effort; independent backend weight attestation is unavailable.

## Implementation and retained records

Scoped implementation and tests are under
`/Users/misaki/Documents/ChatGPT/AI-reviewer/worktrees/eccv2026-checkdiff-yao-20260918`
and the same relative remote worktree. Added preparation, launcher and export
wrappers reuse the frozen reference. The controller changes are limited to the
strict persistent two-attempt guard and the requested `blocked_input` label.
The prompt, rubric, schema and isolation helper remain unchanged; no dependency
was installed. The separate input-pins candidate remains undeployed.

Local `final/` contains audit/index mirrors, not a replacement for the complete
remote raw archive. Independent closeout records are
`records/final_accounting_verification.json` and
`records/independent_final_verification.json`.

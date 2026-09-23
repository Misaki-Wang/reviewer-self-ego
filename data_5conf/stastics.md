# 5-Conference Index and Official Acceptance Statistics

These figures are transcribed from the two supplied screenshots. The first table compares the paper counts selected by the current `data_5conf/index` with official conference acceptance counts. The second table shows arXiv-confirmed counts against those official totals.

## Index selection versus official accepted papers

| Source | Papers selected by index | Official accepted-paper count | Selected / official |
|---|---:|---:|---:|
| ICLR 2026 | 2,372 | 5,357 | 44.28% |
| ICML 2026 | 2,239 | 6,552 | 34.17%* |
| ACL 2026 | 772 | 2,296 | 33.62% |
| CVPR 2026 | 943 | 4,089 | 23.06% |
| ECCV 2026 | 633 | 2,834 | 22.34% |

## arXiv-confirmed papers versus official accepted papers

| Source | arXiv-confirmed papers | Official accepted-paper count | Confirmed / official |
|---|---:|---:|---:|
| ICLR 2026 | 4,130 | 5,357 | 77.10% |
| ICML 2026 | 4,548 | 6,552 | 69.41%* |
| ACL 2026 | 1,654 | 2,296 | 72.04% |
| CVPR 2026 | 2,949 | 4,089 | 72.12% |
| ECCV 2026 | 1,885 | 2,834 | 66.51% |
| **Total** | **15,166** | **21,128** | **71.78%** |

## Notes

- The index-selected count is based on the current index's temporal-version criterion: a paper must have at least two versions, and selected consecutive versions must be more than 30 days apart. This is not a major-improvement screening count.
- ICML mixes two categories in the official total: 2,239 of its 6,552 papers are main-conference research papers, while 52 are journal-track presentation papers. Therefore, 34.17% is the count ratio using the full official denominator. Against the current official main-conference poster list, the aligned ratio is **2,187 / 6,341 = 34.49%**.
- For the arXiv-confirmed ICML count, the full-denominator ratio is 4,548 / 6,552 = 69.41%. Using the strict main-conference denominator, it is **4,548 / 6,341 = 71.72%**.
- The ACL official denominator includes main-conference long and short papers, but excludes Findings.
- The ECCV official count is aligned with the final proceedings list.
- Total counts are summed across the five sources; they are not deduplicated across conferences.

---

# Five-conference major-improvement review token and cost estimates

## Scope and interpretation

The target `data_5conf/anonymized/major_imp_index/` contains **3,784 deduplicated PDF versions** (`paper_version_id`). The table below estimates **one review per PDF version and model**. To estimate three review conditions per version, multiply the projected totals by three.

Gemini figures are the user's supplied measured totals for 9,355 and 9,357 successful reviews. All other model figures are historical per-review usage averages extrapolated to 3,784 reviews. They are estimates, not new runs or invoices. Reasoning tokens are included in output tokens and are not counted twice. Missing-usage attempts are not treated as zero; model averages use attempts with reported usage.

## Per-model results

| Model | Evidence / status | Successful or usage-reported reviews in evidence | Observed or average tokens per review | Observed or projected total tokens for 3,784 reviews | Observed or estimated total cost (USD) | Estimated cost per review (USD) |
|---|---|---:|---:|---:|---:|---:|
| Gemini 3.8 Flash | User-supplied full-run measurements | 9,355 successful | 15,945 | 149,163,914 observed | $170.53 observed | $0.01823 |
| Gemini 3.5 Flash | User-supplied full-run measurements | 9,357 successful | 16,317 | 152,674,753 observed | $404.61 observed | $0.04324 |
| **Gemini combined** | User-supplied full-run measurements | **18,712 successful** | **16,131 weighted mean** | **301,838,667 observed** | **$575.14 observed** | **$0.03074 weighted mean** |
| Claude Sonnet 5 | Estimate from 3 successful canary reviews; API-equivalent observed cost | 3 | 38,163 | 144,407,531 projected | $633.04 projected | $0.16729 |
| Claude Opus 5 | Estimate from 3 successful canary reviews; API-equivalent observed cost | 3 | 37,827 | 143,137,368 projected | $1,504.29 projected | $0.39754 |
| Claude Fable 5.1 | Estimate from 3 successful canary reviews; API-equivalent observed cost | 3 | 38,665 | 146,309,621 projected | $3,269.74 projected | $0.86410 |
| Claude Haiku 4.5 | No successful canary usage sample; not estimated | 0 successful (3 failed attempts have usage) | Unknown | Unknown | Unknown | Unknown |
| GPT-5.5 | Estimate from ICLR three-model historical review attempts with reported usage; API-equivalent price | 14,067 usage-reported turns | 376,229 | 1,423,651,383 projected | $2,812.39 projected | $0.74323 |
| GPT-5.6 Sol | Estimate from ICLR three-model historical review attempts with reported usage; API-equivalent price | 14,065 usage-reported turns | 706,522 | 2,673,480,631 projected | $3,078.65 projected | $0.81360 |
| GPT-6 Astra | Estimate from ICLR three-model historical review attempts with reported usage; API-equivalent price | 14,184 usage-reported turns | 481,114 | 1,820,535,935 projected | $5,300.02 projected | $1.40064 |

## Method and caveats

- The five-conference target size is 3,784 unique PDF versions. The unit is one model review of one version, not one comparison pair; repeated endpoints are counted once.
- Gemini rows use the supplied actual totals. Per-review averages are calculated from those totals and successful-review counts; the combined mean is weighted by review count.
- Claude averages use the three successful canary receipts per model in `experiments/runs/claude_review_quota_4model_20260920T185806Z/`. Extrapolation assumes the canary PDFs represent the full target. Costs use the receipts' API-equivalent list costs.
- GPT averages use `res/published/iclr2026_three_model_review_20260917/models/<model>/attempts.jsonl.gz`. They divide summed reported input-plus-output tokens by turns with usage, then extrapolate to 3,784 reviews. Usage-reported counts are lower than physical-attempt counts because some attempts lack usage.
- GPT API-equivalent estimates apply current standard input, cached-input, and output rates separately: GPT-5.5 $5/$0.50/$30 per million tokens; GPT-5.6 Sol $4/$0.40/$20; GPT-6 Astra $10/$1/$50. Costs are not Codex subscription charges.
- Claude API-equivalent costs are based on per-receipt native cost metadata. The 3-review sample is small; it is not a confidence interval or a guarantee of full-run consumption.
- The Gemini 3.5/3.8 figures are actual reported costs supplied for this comparison. They are not converted from current public list prices.
- This workspace cannot access `/fsx` because that filesystem is not mounted in the current environment. This file is the local mirror at `data_5conf/stastics.md`; it has not been copied to the remote MBZ path.

## Updated token and API-equivalent cost statistics

This user-supplied table is the latest consolidated estimate for 3,784 reviews per model. The earlier canary-based estimates above are retained as historical context. API-equivalent costs are estimates, not Codex subscription charges.

| Model | Successful reviews in source | Average tokens per review | Average API-equivalent cost per review (USD) | Estimated tokens for 3,784 reviews | Estimated API-equivalent cost for 3,784 reviews (USD) |
| ----------------------------- | ---------------------------: | ------------------------: | -------------------------------------------: | ---------------------------------: | ----------------------------------------------------: |
| Gemini 3.8 Flash | 9,355 | 15,945 | $0.01823 | 60,335,249 | $68.98 |
| Gemini 3.5 Flash | 9,357 | 16,317 | $0.04324 | 61,742,147 | $163.63 |
| Claude Sonnet 5 | 3,105 | 81,097 | $0.31330 | 306,870,444 | $1,185.52 |
| Claude Haiku 4.5 | 1,979 | 71,047 | $0.15226 | 268,841,058 | $576.17 |
| GPT-5.5 | 5,943 | 162,070 | $0.40754 | 613,272,363 | $1,542.13 |
| GPT-5.6 Sol | 6,455 | 313,592 | $0.62078 | 1,186,633,892 | $2,349.05 |
| GPT-6 Astra | 6,454 | 243,001 | $0.88719 | 919,517,292 | $3,357.11 |
| **Total across seven models** | — | — | — | **3,417,212,445** | **$9,242.59** |

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

## Updated token and API-equivalent cost statistics

This user-supplied table is the latest consolidated estimate for 3,784 reviews per model. API-equivalent costs are estimates, not Codex subscription charges.

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

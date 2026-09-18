# Selected experiment publications

Only the experiments listed below are selected for GitHub publication. Full MBZ
runs, input PDFs, caches and historical archives are not included here.

| Experiment | Scope | Selected payload | Report |
| --- | --- | ---: | --- |
| [Five-conference check_diff responses](check_diff_5conf_20260919/README.md) | ICLR2026, ICML2026, ACL2026, ECCV2026, CVPR2026; 6,959 papers; 9,092 pairs; 9,066 responses; 25 unavailable inputs and 1 provider refusal | Exact responses, judgments, merged pair ledgers, and provenance | [Counts and source history](check_diff_5conf_20260919/summary.json) |
| [ICLR2026](iclr2026_full_20260913/README.md) | 706 units; 1,588 judgments; 11 unavailable pairs | 37.86 MB | [Self-contained HTML](../../html/ICLR2026-check-diff-summary.html) |
| [Review experiment pilot](review_exp_iclr2026_pilot/README.md) | Independent review pilot; see its package README | See package | [HTML](../../html/review-exp-iclr2026-pilot.html) |
| [Multisource review pilot](review_exp_multisource_pilot_20260914/README.md) | 10 sources; 100 papers; 364 versions; 4,368 reviews | See package | [HTML](../../html/review-exp-multisource-pilot-20260914.html) |
| [Remaining nine-source check_diff](check_diff_remaining_sources_20260914/README.md) | 3,018 unique units; 6,814 judgments; 15 unavailable pairs | 156.38 MB original; 238.59 MB full package | [Self-contained HTML](../../html/remaining-sources-check-diff-summary-20260914.html) |
| [Latest-version three-model review pilot](review_exp_multisource_3model_latest_pilot_20260914/README.md) | 10 sources; 50 unique papers/latest versions; 3 models; 29 conditions; 4,350 reviews | 98.37 MB raw; 235.56 MB full package | [Self-contained HTML](../../html/review-exp-multisource-3model-latest-pilot-20260914.html) |

Original-payload sizes exclude generated HTML, README and checksum/descriptor files.
Original selected file bytes are covered by each export's SHA256SUMS.

For the remaining-source release, the full package additionally retains derived accounting,
preview and provenance JSON. Its 70.38 MB standalone HTML is separate.

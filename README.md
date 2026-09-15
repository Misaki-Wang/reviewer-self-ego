# Reviewer Self-Ego

MBZ is the primary workspace for editing, testing and running this project.
Use local VS Code through Remote SSH. Current inputs are named source_data;
previous experiment releases and their original records are archived.

## Formal layout

~~~text
check_diff/          PDF-version comparisons, prompts, schema and usage accounting
review_experiment/   Separate paper-review experiment implementation
tests/              Offline regression and explicit sandbox-test tools
utility/             Shared preparation, workflow, verification and configuration
data/
   index/            index.json, conference JSON shards, metadata and provenance
   pdf/              Original PDFs: <paper-id>/<paper-id>vN.pdf
downloads_papers/     Download cache: <conference>/<arxiv_id>/vN.pdf, all versions
clean_data/          Anonymized PDFs: <conference>/<arxiv_id>/vN.pdf
res/                 One directory per new run; historical studies under archive/
html/                Experiment design and result-sharing HTML
.gitignore
README.md
~~~

The code directories are real directories. No dated worktree or old dataset
alias is required. There is one JSON catalog. New downloads use downloads_papers;
the existing data/pdf store remains supported by the formal frozen-run workflow.
The catalog contains 3,724 distinct papers and 14,086 original versions across
ten conference shards. Paper/version values and order are preserved; only the
active package name, paths and packaging hashes changed. The original PDF store
contains 14,063 files; 23 versions have recorded official unavailability.
See [data/index/README.md](data/index/README.md) for counts and source contracts.

Download all catalogued versions, anonymize each required PDF, then construct pairs offline:

```sh
python3.11 -B -m utility.download_papers --conf acl2026
# Run anonymize_pdf once for each required downloaded PDF, for example:
python3.11 -B -m utility.anonymize_pdf downloads_papers/acl2026/2305.14299/v1.pdf
# After all required versions are present in clean_data:
python3.11 -B -m utility.build_pairs --conf acl2026 --all --output res/NEW_RUN_ID/prepared
```

The download root is configured by `data.downloads_root` in
`utility/project.json`. Each paper directory holds `v1.pdf`, `v2.pdf`, etc. and
the original `metadata.json`. Anonymization writes only the PDF to `data.clean_root`
(default `clean_data`). Both `build_pairs` and `review_experiment` read this clean
store by default. Pair selection still uses the source catalog for timestamps;
no `metadata.json` is required in `clean_data`. Missing clean versions fail pair
construction without falling back to original downloads. Pair construction
retains the existing temporal rule and output format.
See [utility/README.md](utility/README.md) for filters, retries and fixed plans.

## One configuration and one main workflow

[utility/project.json](utility/project.json) is the current source of input paths,
output roots, runtime paths and comparison defaults. Preparation freezes that
configuration into each new run. Subsequent execution uses the run's frozen
controllers/configuration rather than silently switching to newer project settings.

From the project root, in Bash:

~~~sh
source utility/env.sh
python3.12 utility/workflow.py paths
~~~

The supported stages are:

| Stage | Reads | Writes |
| --- | --- | --- |
| freeze-sources | Current code and source-data catalog/metadata | utility/source_manifest.json |
| prepare | Configured source catalog, PDF store, verified metadata and source revision | res/<run-id>/ frozen scope, prepared inputs, configuration and controllers |
| run | That run's frozen scope/configuration/controllers | The same run's output/, attempts/, progress and usage |
| verify | That run's original artifacts and frozen verifier | Verification progress or final verification/report in the same run |

Refresh the source manifest explicitly after reviewing code or source-data edits.
Its content revision records actual bytes; it is not presented as a Git commit.
Preparation rejects stale sources rather than silently updating their declaration.

~~~sh
python3.12 utility/workflow.py freeze-sources
python3.12 utility/workflow.py prepare --run-id NEW_RUN --source acl2026 --units 10
python3.12 utility/workflow.py run --run-id NEW_RUN
python3.12 utility/workflow.py verify --run-id NEW_RUN --require-complete
~~~

Use a fresh explicit run ID and scope. Preparation does not start model calls.
The project is paused, and execution also honors the run's pause_dispatch control;
these commands never clear either pause automatically. A new experiment still
requires the user's explicit instruction. archive and migration storage are not
valid destinations for new runs.

Inside each run, scope_input.json and prepared/selection_manifest.json identify
its frozen inputs. run_config.json records model/settings/source revision.
output/ holds judgments and per-pair execution records; attempts/ retains raw
responses and every attempt receipt. token_usage.json is the batch usage summary.
Unknown usage remains unknown, and failed/retried calls keep their reported usage.

## Separate review experiments

[review_experiment/README.md](review_experiment/README.md) documents the review
workflow. Reviews read directly from `clean_data/<conference>/<paper>/vN.pdf`.
Use `--conf`/`--paper`/`--limit-papers` to select papers and `--version` or
`--latest-only` to select available clean versions. No pair-building step is needed
for reviews. Paired `input_data/` remains the input for `check_diff`.

The review runner retains its existing output format and isolation. Supply the
desired output directory; the input defaults to `data.clean_root`:

```sh
python3.11 -B -m review_experiment.runner clean_data \
  --output output/review-sample --conf iclr2026 --latest-only --dry-run
```

## Results, archives and HTML

- New experimental results belong in res/<run-id>/.
- Historical t1.1 and t1.2 are in res/archive/t1.1 and res/archive/t1.2.
  Both retain 200-unit scopes, 430 successful comparisons, six unavailable pairs,
  original responses, usage, receipts and frozen prompts/code/data.
- The earlier pilot and old build/maintenance/layout evidence are also archived.
  Original absolute paths in historical records are preserved as evidence; those
  launchers may require their recorded environment/path layout for reproduction.
  The current workflow does not resume archived studies.
- [html/main_experiment.html](html/main_experiment.html) is the GitHub design page.
- [html/ACL2026-t1.1-vs-t1.2.html](html/ACL2026-t1.1-vs-t1.2.html) is the existing
  self-contained calibration comparison page, preserved without changing its data.

Place future discussion/sync HTML under html. Keep raw result data under res and
source inputs under data. Host-specific dependencies under utility/.runtime and
experiment payloads/PDFs are Git-ignored. No GitHub push is implied by local MBZ edits.

## Review and validation

Shared Codex token accounting lives in [utility/token_usage.py](utility/token_usage.py).
Both experiment runners use that file; the independent batch verifier retains
its separate calculation for cross-checking. New frozen runs keep their own copy
at source_snapshot/utility/token_usage.py, while archived runs remain unchanged.


Start with the source-data preparation contract, then check_diff/prompts and
schema.py, followed by runner.py/token_usage.py and the batch modules in utility.
Tests are organized by check_diff, utility, mbz, data and review_experiment.
Archived fixed-study analysis belongs to its archived evidence, not the current
runtime entry points.

~~~sh
python3.12 -B -m unittest discover -s tests -t . -p 'test_*.py'
~~~

Use a CPU-only Slurm allocation for sustained validation. Tests use synthetic
PDFs/fake CLI calls. The explicit sandbox_smoke tool is outside automatic discovery
and is not a live-model test. No new dependency is required for this layout.

## Published experiment results

- ICLR2026: [interactive HTML report](html/ICLR2026-check-diff-summary.html) and
  [selected result package](res/published/iclr2026_full_20260913/README.md).
  All 706 units are accounted for: 1,588 validated judgments and 11 pairs with
  unavailable original PDFs. The original selected payload is 37.86 MB.
  Download the self-contained HTML to explore it locally.

- Remaining nine-source check_diff: [self-contained HTML report](html/remaining-sources-check-diff-summary-20260914.html) and
  [selected result package](res/published/check_diff_remaining_sources_20260914/README.md).
  All 3,018 unique units are accounted for: 6,814 validated judgments and 15 pairs
  with unavailable original PDFs. The 238.59 MB selected package includes 156.38 MB
  of original selected records plus derived accounting and preview data; the HTML
  is an additional 70.38 MB. Frozen and independent artifact audits passed with zero errors.

- Latest-version three-model review pilot: [self-contained HTML report](html/review-exp-multisource-3model-latest-pilot-20260914.html) and [selected results](res/published/review_exp_multisource_3model_latest_pilot_20260914/README.md). Ten sources, five papers each, one latest indexed original version and 29 canonical conditions yield 4,350 reviews: 1,450 each from gpt-6-astra/max, gpt-5.6-sol/max and gpt-5.5/xhigh. The 235.56 MB package retains 150 original raw result files, prompts, time/token statistics and auditable provenance; the standalone HTML is an additional 19.99 MB. Native venue score scales remain separate.

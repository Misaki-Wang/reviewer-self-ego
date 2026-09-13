# review_experiment

This standalone runner reviews distinct paper versions using three single-manuscript experiments and writes one `reviews.json` per paper. It uses Python 3.9+ standard-library modules, an authenticated Codex CLI supporting the existing permission-profile options, and an installed PDF skill. No new dependency is required by this migration. The upstream CLI request, JSON schema, scientific review logic, model defaults and frozen prompt bytes are preserved; compatibility with the current MBZ Codex runtime has not been tested with a live invocation.

## Formal-layout input boundary

Canonical source metadata and original PDFs live under `data/index/` and `data/pdf/`. This legacy runner **does not read the source index or the raw PDF-store layout**. It requires an explicitly prepared pair tree:

```text
res/<prepared-run>/input_data/
  <paper_id>/
    v1_v2/v1.pdf
    v1_v2/v2.pdf
    v2_v3/v2.pdf
    v2_v3/v3.pdf
```

The directory shown is illustrative; it is not created automatically. Input preparation and paper selection must be separately authorized and retain provenance to `data/index/` and `data/pdf/`. The runner adds no screening, pair-selection or review algorithm. It does not consume `pairs.json`, `pair.json`, or `.pdf_cache`.

`utility/project.json` describes the canonical source data and the separate comparison workflow. Its raw input paths do not satisfy this runner's pair-tree interface, so this runner does not load that configuration, inherit its model settings, or inspect its pause flag. Root workflow documentation must not imply that it automates `review_experiment`. A future live review requires its own explicitly authorized scope and invocation.

Both the positional input and `--output` are required. Relative CLI paths resolve from the current working directory. For example, from the project root, inventory an already prepared input without reading PDF contents or calling any model:

```sh
python3 -B -m review_experiment.runner res/prepared-review/input_data \
  --output res/review-inventory --dry-run
```

The example paths must be replaced with the selected prepared input and an active run path. The output may be new or an existing resumable directory. `--dry-run` creates or changes no output. A paper directory or a single `vX_vY` pair directory is also accepted as input; a pair directory selects its two versions.

`--paper`, `--version`, `--condition`, and `--experiment` may be repeated. `--limit-papers` restricts paper count; `--replicates` controls independent calls per condition. `--workers` runs papers concurrently, while each paper processes versions and conditions sequentially. Per-call `--timeout` defaults to 1800 seconds and `--retries` to one retry.

The existing defaults remain `gpt-5.5` and `medium`; `--model` and `--effort` override them. `--other-model` is an author label, not another model invocation. A live selection containing `exp2_other_llm` requires an explicit label different from the evaluator model; inventory does not require it.

## Version identity and independent reviews

The runner discovers `<paper_id>/vX_vY/vN.pdf`, deduplicates by `(paper_id, version)`, and sorts versions numerically. For example, v2 in both `v1_v2` and `v2_v3` becomes one version. Hardlinks are read once per inode; distinct copies of the same version must have matching hashes. Conflicting contents or missing/empty pair PDFs fail. Different version numbers remain distinct even when their PDF bytes match.

One byte snapshot is reused across that version's conditions. Every condition and replicate still starts its own `codex exec --ephemeral`, with a fresh working directory and no previous reviews or other versions. Experiment 1 and experiment 3's neutral condition run independently even though their prompt text matches. The experiment 3 neutral condition is counted once across 3A/3B.

The model sees the neutral filename `manuscript.pdf`. Temporary filenames use random identifiers, but the full path inherits the caller-selected output directory; choose a neutral run identifier to avoid introducing paper, version or condition labels through that path. Original PDF bytes are unchanged. Authors, dates and other information present inside a PDF are not anonymized.

## Frozen prompts and exports

`prompts/` contains one shared system prompt, 13 user prompts and their SHA-256 manifest. These are maintained source assets, extracted verbatim from the corresponding code blocks in `html/main_experiment.html`, preserving decoded text, punctuation, spacing and paragraphs. The runner reads the frozen files and verifies their hashes; it does not read HTML or automatically refresh prompts.

Verify the source assets without writing:

```sh
python3 -B -m review_experiment.export_prompts --check
```

A generated export must have an explicit, nonexistent destination under the results area:

```sh
python3 -B -m review_experiment.export_prompts --destination res/review-prompt-export
```

Exporting fails if the destination already exists. `--check --destination PATH` verifies an existing export. `--source PATH` selects another HTML source; the default is `html/main_experiment.html`, resolved relative to the project root. No HTML is generated by this runner. Creating an export does not replace the maintained frozen prompts; any future scientific prompt change needs its own review.

The HTML System message is passed verbatim as `developer_instructions`, and the User message through stdin. Codex's built-in system instructions still apply; this is a CLI role mapping rather than a direct system/user API interface. The only runtime substitutions are:

- `{PAPER_CONTENT}` becomes the complete PDF's local path. No truncation or summary replaces it.
- `{OTHER_MODEL_NAME}` becomes the explicit author-model label.

The unchanged output schema is `{"oa": 6, "strengths": ["..."], "weaknesses": ["..."]}`. OA must be an integer in 1, 3, 5, 6, 8, 10. Strengths and weaknesses must be lists of nonempty text items; empty lists are accepted. Extra fields or legacy OA-only records are invalid.

| Experiment | Condition IDs |
| --- | --- |
| 1 | `exp1_quality` |
| 2 | `exp2_self_llm`, `exp2_other_llm`, `exp2_self_human`, `exp2_other_human` |
| 3 neutral | `exp3_neutral` |
| 3A | `exp3_very_positive`, `exp3_positive`, `exp3_negative`, `exp3_very_negative` |
| 3B | `exp3_years`, `exp3_months`, `exp3_days` |

## Existing CLI isolation contract

The PDF skill is found from the existing installation or specified with `--pdf-skill PATH`. Tool access uses the unchanged `review_only` permission profile: the current scratch directory is writable; the exact PDF snapshot, skill and explicit runtime dependencies are readable. The profile denies broad repository, input-tree, history and home-directory access. The runner does not download missing tools or dependencies.

Tool networking and web search are disabled; apps, plugins, MCP servers, browser/computer use, multi-agent features, memories and hooks are disabled. Tool environments are controlled, with login shells and shell snapshots disabled. Strict configuration errors do not trigger a less restrictive fallback. `--ignore-user-config` and `project_doc_max_bytes=0` isolate the experiment from user configuration and project instructions without editing them.

The CLI itself still needs authentication and a model-service connection for a live review; its tool-network setting does not make the review offline. These are preserved upstream settings, not a newly verified MBZ sandbox claim.

## Results and history preservation

Use an active run directory such as `res/<review-run-id>/`:

```text
res/<review-run-id>/
  <paper_id>/reviews.json
  run_summary.json
  .runner.lock
```

Existing active output directories are supported. Repeating a CLI invocation skips completed, valid reviews with matching fingerprints. Selecting additional conditions appends them while preserving other condition records. Failed or changed-fingerprint reviews are recomputed, and `--overwrite` explicitly recomputes selected records. These are the original CLI resume semantics and are tested through `runner.main`, including retries and timeouts.

Input and output directories must not overlap in either direction after resolving the input path. Output paths may not traverse symlinks, and the lock, summary, selected paper directories and their result files may not be symlinks. An output that overlaps project source, utility, tests, data, HTML or Git metadata is refused. `res/archive/` and `res/.migration/` are also protected, including enclosing output directories such as `res/` itself. These protections apply to live execution and `--dry-run`; `--overwrite` cannot bypass them. Use a dedicated active run directory.

Project protection is relative to the runner's project root (the staging root before the final rename). Explicit external input/output paths remain supported with overlap and symlink checks. The runner does not infer a project's archive boundary from arbitrary names in an external path.

Each paper document records versions, condition/replicate reviews, model settings, request fingerprints, PDF hashes and source paths. Attempts retain the exact user message, raw final response, errors, command activity and reported token usage. Missing usage remains unknown, not zero; cached-input and reasoning-output subsets are not added twice.

Writes within the selected run are atomic. The output directory is locked. Scratch snapshots and CLI intermediate files live in temporary subdirectories of that run and are removed after use. `latest_selection_status`, `latest_selection_counts` and `run_summary.json` describe the current selected batch, not completion of all possible conditions. Interruptions stop active subprocesses while preserving completed records.

The unchanged core fingerprint covers PDF and prompt contents, model, effort, author label, CLI path, skill bytes, schema and adapter version. If a version's PDF hash changes, its prior version reviews are reset as in the original runner; completed records for other versions or unselected conditions are otherwise retained. Use a new run directory when a separate experiment should have independent history.

## Offline tests and isolated OS-sandbox probe

Run the maintained tests from the project root:

```sh
python3 -B -m unittest discover -s tests/review_experiment -t . -v
```

The top-level `-t .` keeps the test package named `tests.review_experiment`, distinct from the source `review_experiment`. The suite uses temporary fake PDFs and a fake CLI; fixture scores are pipeline test data. It covers the original eight cases plus formal-layout I/O boundaries, scratch placement, frozen exports and package imports.

`tests/review_experiment/sandbox_smoke.py` is a separate manual OS-sandbox driver. It invokes the real `codex sandbox` executable and a Python fixture process, not `codex exec` or an LLM. It checks allowed PDF reads/scratch writes, denied outside reads/directory enumeration/symlink escapes/PDF writes, and denied binding to a private local socket. It is intentionally not named `test_*.py` and is not executed by the offline suite. Importing it does not launch Codex. If separately authorized, its module is `tests.review_experiment.sandbox_smoke`; invoking it may require a host that allows nested sandboxes and a project-contained `TMPDIR`.

The upstream `smoke_report.json` has been removed from active code and preserved unchanged in the migration's `review-originals/` for parent archival. Its old live-model results, commands and counts are historical evidence only. Neither that probe nor any real review/model call was run during this migration.

## Shared token accounting

Both experiment runners use the single utility/token_usage.py implementation.
It preserves raw reported usage and the existing aggregation contract. New batch
snapshots freeze it at source_snapshot/utility/token_usage.py; historical snapshots
are unchanged. The independent verifier retains its separate arithmetic.

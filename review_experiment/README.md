# review_experiment

This standalone runner reviews distinct paper versions using three single-manuscript experiments and writes one `reviews.json` per paper. It uses Python 3.9+ standard-library modules, an authenticated Codex CLI supporting the existing permission-profile options, and an installed PDF skill. No new dependency is required by this migration. The upstream CLI request, JSON schema, scientific review logic, model defaults and frozen prompt bytes are preserved; compatibility with the current MBZ Codex runtime has not been tested with a live invocation.

## Formal-layout input boundary

Canonical source metadata and original PDFs live under `data/index/` and `data/pdf/`. The PDF loader requires an explicitly prepared pair tree. Source metadata or the canonical index supplies the venue profile; PDF preparation remains a separate step:

```text
res/<prepared-run>/input_data/
  <paper_id>/
    v1_v2/v1.pdf
    v1_v2/v2.pdf
    v2_v3/v2.pdf
    v2_v3/v3.pdf
```

The directory shown is illustrative; it is not created automatically. Input preparation and paper selection must be separately authorized and retain provenance to `data/index/` and `data/pdf/`. The runner retains the supplied paper/version scope and deduplicates versions from this tree. It reads supported source metadata for profile selection; `pair.json` and `.pdf_cache` are not source-profile inputs.

`utility/project.json` describes the canonical source data and the separate comparison workflow. Its raw input paths do not satisfy this runner's pair-tree interface, so this runner does not inherit its comparison model settings or inspect its pause flag. When no prepared provenance or explicit source is supplied, source selection can fall back to the canonical `data/index/index.json` catalog. Root workflow documentation must not imply that it automates `review_experiment`. A future live review requires its own explicitly authorized scope and invocation.

Both the positional input and `--output` are required. Relative CLI paths resolve from the current working directory. For example, from the project root, inventory an already prepared input without reading PDF contents or calling any model:

```sh
python3 -B -m review_experiment.runner res/prepared-review/input_data \
  --output res/review-inventory --source iclr2026 --dry-run
```

The example paths must be replaced with the selected prepared input and an active run path. The output may be new or an existing resumable directory. `--dry-run` creates or changes no output. A paper directory or a single `vX_vY` pair directory is also accepted as input; a pair directory selects its two versions.

`--paper`, `--version`, `--condition`, and `--experiment` may be repeated. `--limit-papers` restricts paper count; `--replicates` controls independent calls per condition. `--workers` limits concurrent independent version/condition/replicate reviews. Each selected version is read once into an immutable byte snapshot, and pending cells are admitted across papers in round-robin order. Per-paper JSON updates are serialized to preserve every completed cell. Per-call `--timeout` defaults to 1800 seconds and `--retries` to one retry.

The existing defaults remain `gpt-5.5` and `medium`; `--model` and `--effort` override them. `--other-model` is an author label, not another model invocation. A live selection containing `exp2_other_llm` requires an explicit label different from the evaluator model; inventory does not require it.

## Version identity and independent reviews

The runner discovers `<paper_id>/vX_vY/vN.pdf`, deduplicates by `(paper_id, version)`, and sorts versions numerically. For example, v2 in both `v1_v2` and `v2_v3` becomes one version. Hardlinks are read once per inode; distinct copies of the same version must have matching hashes. Conflicting contents or missing/empty pair PDFs fail. Different version numbers remain distinct even when their PDF bytes match.

One byte snapshot is reused across that version's conditions. Every condition and replicate still starts its own `codex exec --ephemeral`, with a fresh working directory and no previous reviews or other versions. Experiment 1's `exp1_quality` is the shared neutral baseline for 3A and 3B; there is no separate experiment 3 neutral call. Reuse is restricted to the same version, replicate and matching request fingerprint. Historical duplicate-condition records remain in their original results.

The model sees the neutral filename `manuscript.pdf`. Temporary filenames use random identifiers, but the full path inherits the caller-selected output directory; choose a neutral run identifier to avoid introducing paper, version or condition labels through that path. Original PDF bytes are unchanged. Authors, dates and other information present inside a PDF are not anonymized.

## Source-specific prompt templates

Maintain the 12 user templates directly in `prompts/` and the source-specific system templates in `prompts/system/`. The original ICLR2026 system text is preserved byte-for-byte at `system/iclr2026.txt`. `manifest.json` contains the `systems` registry, each template's SHA-256 in `files`, and the shared condition list. A system profile records its file, OA type, allowed values, labels, scale kind and provenance URLs. Update the matching hash after a text edit. The HTML design is not a runtime input.

| Source IDs | OA values | Interpretation |
| --- | --- | --- |
| iclr2026 | 1, 3, 5, 6, 8, 10 | Preserved user-calibrated ICLR baseline |
| acl2025, acl2026, emnlp2025 | 1 to 5 in 0.5 steps | ARR overall publication recommendation |
| cvpr2025 | 1, 2, 3, 4, 5 | Reject through accept, with one borderline category |
| iccv2025 | 1, 2, 3, 4, 5, 6 | Reject through accept, with separate borderline-reject/accept categories |
| icml2025 | 1, 2, 3, 4, 5 | Reject through strong accept |
| icml2026, neurips2025 | 1, 2, 3, 4, 5, 6 | Native six-point scales; labels remain source-specific |
| tmlr_2025plus | 1, 2, 3, 4 | Experimental encoding: reject, leaning reject, leaning accept, accept |

Sources were checked on 2026-09-13. See the per-profile references in `prompts/manifest.json` and [system/SOURCES.md](prompts/system/SOURCES.md). Extended score descriptions are study wording based on official criteria and labels, not verbatim official forms. These profiles define new experimental reviews; they do not relabel historical human scores. Different venues' numeric OA values must not be pooled as if they meant the same thing. TMLR's numbers are explicitly an experimental ordinal encoding, not an official numeric scale.

The runner resolves one profile per paper. It first uses `--source-metadata` when provided; otherwise it checks `source_metadata.json` or `selection_manifest.json` at the prepared-tree root, then `scope_input.json` next to that root. The metadata may be a per-paper source map, a prepared scope/selection plan, a canonical source shard, or a hash-verified canonical catalog. With no such metadata, `--source SOURCE_ID` explicitly declares the source. Without either, the canonical project catalog is used. Unknown, missing, ambiguous or contradictory source assignments fail before a model call. A paper appearing in both ICML and TMLR needs an unambiguous prepared assignment or explicit source; the runner does not guess from its PDF or directory name.

For prepared inputs with source provenance, omit `--source` to route each paper automatically. An explicit selection can also be checked without model calls:

```sh
python3 -B -m review_experiment.runner res/prepared-review/input_data \
  --output res/review-inventory --source-metadata data/index/acl2026.json --dry-run
```

Each independent call receives its own profile and JSON schema, so integer and half-point scales can run concurrently without a shared mutable global schema. Result records include `source_id` and `system_prompt` metadata with the prompt hash and scale interpretation. The fingerprint includes the selected source/profile/schema. A result directory cannot mix different sources for the same paper; legacy reviews without source identity require a fresh output directory. Already-running experiments keep their frozen prompts and runner.

The `exp1_quality` manifest entry has `shared_baseline_for: ["3"]`. Selecting `--experiment 3` includes this baseline and seven experiment 3 conditions, once each. Selecting experiments 1 and 3 together produces the same eight unique conditions. Explicit `--condition` selection still restricts the set. All experiments together use 12 calls per version and replicate; an existing valid baseline is skipped by normal resume logic.

The shared system template is passed verbatim as `developer_instructions`, and the User message through stdin. Codex's built-in system instructions still apply; this is a CLI role mapping rather than a direct system/user API interface. The only runtime substitutions are:

- `{PAPER_CONTENT}` becomes the complete PDF's local path. No truncation or summary replaces it.
- `{OTHER_MODEL_NAME}` becomes the explicit author-model label.

The output keeps the three fields `oa`, `strengths` and `weaknesses`. OA must match the selected source profile; ARR accepts native half-points while the other configured profiles use integers. Strengths and weaknesses must be lists of nonempty text items; empty lists are accepted. Extra fields or legacy OA-only records are invalid.

| Experiment | Condition IDs |
| --- | --- |
| 1 | `exp1_quality` |
| 2 | `exp2_self_llm`, `exp2_other_llm`, `exp2_self_human`, `exp2_other_human` |
| 3 shared neutral baseline | `exp1_quality` (reused from 1) |
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

Existing source-identified active output directories are supported. Repeating a CLI invocation skips completed, valid reviews with matching fingerprints. Selecting additional conditions appends them while preserving other condition records. Failed or changed-fingerprint reviews are recomputed, and `--overwrite` explicitly recomputes selected records. These are the original CLI resume semantics and are tested through `runner.main`, including retries and timeouts.

Input and output directories must not overlap in either direction after resolving the input path. Output paths may not traverse symlinks, and the lock, summary, selected paper directories and their result files may not be symlinks. An output that overlaps project source, utility, tests, data, HTML or Git metadata is refused. `res/archive/` and `res/.migration/` are also protected, including enclosing output directories such as `res/` itself. These protections apply to live execution and `--dry-run`; `--overwrite` cannot bypass them. Use a dedicated active run directory.

Project protection is relative to the runner's project root (the staging root before the final rename). Explicit external input/output paths remain supported with overlap and symlink checks. The runner does not infer a project's archive boundary from arbitrary names in an external path.

Each paper document records versions, condition/replicate reviews, model settings, request fingerprints, PDF hashes and source paths. Attempts retain the exact user message, raw final response, errors, command activity and reported token usage. Missing usage remains unknown, not zero; cached-input and reasoning-output subsets are not added twice.

Writes within the selected run are atomic. The output directory is locked. Scratch snapshots and CLI intermediate files live in temporary subdirectories of that run and are removed after use. `latest_selection_status`, `latest_selection_counts` and `run_summary.json` describe the current selected batch, not completion of all possible conditions. Interruptions stop active subprocesses while preserving completed records.

The fingerprint covers the source profile and scale metadata, PDF and prompt contents, model, effort, author label, CLI path, skill bytes, schema and adapter version. If a version's PDF hash changes, its prior version reviews are reset as in the original runner; completed records for other versions or unselected conditions are otherwise retained. Use a new run directory when a separate experiment should have independent history.

## Offline tests and isolated OS-sandbox probe

Run the maintained tests from the project root:

```sh
python3 -B -m unittest discover -s tests/review_experiment -t . -v
```

The top-level `-t .` keeps the test package named `tests.review_experiment`, distinct from the source `review_experiment`. The suite uses temporary fake PDFs and a fake CLI; fixture scores are pipeline test data. It covers review execution, prompt loading and hash checks, formal-layout I/O boundaries, scratch placement and package imports.

`tests/review_experiment/sandbox_smoke.py` is a separate manual OS-sandbox driver. It invokes the real `codex sandbox` executable and a Python fixture process, not `codex exec` or an LLM. It checks allowed PDF reads/scratch writes, denied outside reads, hidden outside directory contents, denied symlink escapes/PDF writes, and denied binding to a private local socket. It is intentionally not named `test_*.py` and is not executed by the offline suite. Importing it does not launch Codex. If separately authorized, its module is `tests.review_experiment.sandbox_smoke`; invoking it may require a host that allows nested sandboxes and a project-contained `TMPDIR`.

The upstream `smoke_report.json` has been removed from active code and preserved unchanged in the migration's `review-originals/` for parent archival. Its old live-model results, commands and counts are historical evidence only. Neither that probe nor any real review/model call was run during this migration.

## Shared token accounting

Both experiment runners use the single utility/token_usage.py implementation.
It preserves raw reported usage and the existing aggregation contract. New batch
snapshots freeze it at source_snapshot/utility/token_usage.py; historical snapshots
are unchanged. The independent verifier retains its separate arithmetic.

## MBZ pilot runtime compatibility

The CLI also accepts explicit `--effort max`. Linux sandbox setup receives read access to the exact resolved Codex executable, including home-directory installations. `--tool-runtime PATH` optionally exposes only PATH/python_vendor and PATH/ghostscript-9.55.0, and sets the PDF tool environment to those existing packages and rendering tools. It installs nothing and is recorded in the request fingerprint. The root read denial, read-only manuscript, independent sessions, prompt bytes and result schema remain the same.

## Review-level concurrency and orderly draining

`--workers 32` can run 32 independent review calls even with fewer than 32 papers. Each call retains the existing fresh Codex session, exact prompt/schema, permission profile, retry rules and token accounting. Valid matching-fingerprint cells are skipped; scheduling concurrency does not change their scientific fingerprint. The run summary records the scheduling unit, configured workers and peak admitted concurrency.

On Unix hosts, `SIGUSR1` drains the queue: no new cells are admitted and current calls finish normally, after which the run reports `drained` and exits 130 if work remains. A later invocation resumes those remaining cells. `SIGINT`/`SIGTERM` retain the existing immediate-interruption behavior.

## Project-local ripgrep

The MBZ runtime includes ripgrep 15.2.0 at `utility/.runtime/ripgrep/rg`. Its official release checksum and installed binary hash are recorded in `utility/.runtime/ripgrep/install.json`. Source `utility/env.sh` to use `rg` in a project shell.

The review CLI automatically selects this executable, falling back to an existing `rg` on PATH when the project copy is absent. `--ripgrep PATH` selects an explicit executable named `rg`. Before starting reviews, the runner checks executability and `rg --version`, records the binary hash/version in each attempt and the request fingerprint, and adds its directory to the controlled tool PATH. The sandbox grants read access to the exact executable, not its parent directory. Changing the selected binary changes the fingerprint. Frozen runs should retain the selected binary with their runtime and pass its explicit path.

This tool setup does not change prompts, source profiles, score scales or token accounting. Historical frozen runs keep their original environment. `--dry-run` remains inventory-only and does not execute tool preflight or model calls.

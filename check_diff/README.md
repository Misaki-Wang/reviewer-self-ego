# PDF version comparison

This module compares two original PDF versions with the existing local Codex CLI.
For normal project runs use ../utility/workflow.py and ../utility/project.json;
that entry fixes source data, run paths and the requested model configuration.

## Explicit input and output

The low-level CLI requires a prepared input directory and an output directory.
It never falls back to an input_data or output folder beside source code.

~~~sh
python3 check_diff/runner.py res/RUN_ID/prepared --output res/RUN_ID/output --dry-run
~~~

Prepared inputs contain paper/version-pair directories with vN.pdf endpoints.
A single pair directory is also accepted. --paper and --limit restrict the
explicit input; --workers, --timeout and --retries retain their existing behavior.
Direct CLI defaults remain gpt-5.5 / medium; normal batch runs receive model and
reasoning settings explicitly from the project configuration and freeze them.

--background detaches the low-level runner. Its default log is
<output>/runner.log and its PID file is derived from that log path. --log-file
can select another explicit location. Temporary PDF-reading state follows
TMPDIR; source the project environment to keep scratch under utility/.runtime.
Do not install dependencies during a comparison.

## Scientific contract

Each pair uses the fixed compare.md and rubric.md under prompts/, plus schema.py.
The original PDF paths and version direction are supplied to Codex; the HTML
experiment-design page does not participate in comparison prompting.

The result contains independent preliminary A1 comparability and per-version A2
empirical-content assessments, fourteen independent change dimensions, and one
separate improvement judgment. No aggregate screening decision is derived, no
A2-negative version is removed, and no comparison pair is rebuilt from labels.
Dimension/improvement labels are normalized to lowercase before strict schema
validation; justification text and raw responses are not semantically rewritten.
See the rubric and schema for the precise labels and boundaries.

Each comparison keeps the existing workspace-write sandbox, disabled web search
and disabled tool-network access. PDF contents are evidence, not instructions.
Codex itself still uses its model-service connection. Existing invocation,
retry, stop and output-validation logic is unchanged by the directory layout.

## Results and reported usage

Per-pair judgments and receipts are stored in the explicitly selected output:

~~~text
<output>/<paper-id>/diff_vN_vM.json
<output>/<paper-id>/diff_vN_vM.execution.json
~~~

A standalone invocation also writes <output>/token_usage.json for attempts made
by that invocation. The normal batch coordinator maintains the authoritative
whole-run token_usage.json at res/<run-id>/, alongside attempts/ and run_manifest.json.

Usage comes from unmodified Codex turn.completed usage objects. Input tokens
include cached input; output tokens include reasoning output. These subsets are
not added twice. Failed and retried attempts retain any reported usage. Missing
counters remain null; incomplete telemetry is not treated as zero or as a complete
billing total. Skipped valid results do not count as new consumption.

Repeated execution may skip valid saved results; --overwrite replaces selected
results. Use a fresh run ID for a separate experiment so histories remain distinct.
Old schemas are not silently converted into new scientific judgments.

## Offline tests

~~~sh
python3.12 -B -m unittest discover -s tests/check_diff -t . -p 'test_*.py'
~~~

Tests use fake CLI fixtures and do not request live judgments.

## Shared token accounting

Both experiment runners use the single utility/codex/token_usage.py implementation.
It preserves raw reported usage and the existing aggregation contract. New batch
snapshots freeze it at source_snapshot/utility/token_usage.py; historical snapshots
are unchanged. The independent verifier retains its separate arithmetic.


## File access isolation

Every new comparison uses `utility/codex/codex_isolation.py`: only the exact two input
PDFs and the current private work directory are exposed as experimental material.
The selected PDF skill and required interpreter/PDF runtime are read-only resource
exceptions. Web search, command networking and auxiliary access channels are off;
unsupported configurations fail before a model call. Scratch is created under the
selected output directory, outside shared system temporary directories.

Use `--pdf-skill /absolute/path/to/SKILL.md` or `CHECK_DIFF_PDF_SKILL` for an explicit
skill. The existing project-private `utility/.runtime/pdf/SKILL.md` is the default
fallback when the selected Codex home does not expose a PDF skill. No dependency
installation is performed automatically.

Execution receipts retain the isolation helper identity and selected file paths.
A pre-existing result is reused only with a completed receipt matching the current
policy identity. Legacy or mismatched cached results remain untouched and cause a
clear error; use a fresh output directory or explicitly request `--overwrite`.
Historical frozen runners retain their original policy and must not be described
as upgraded by changing the current source files.

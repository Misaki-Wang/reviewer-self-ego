# PDF version comparison

Compare adjacent selected versions of one paper using original PDFs, a fixed [rubric](prompts/rubric.md), and a validated JSON schema. The formal pipeline ends with saved judgments and execution records. The preview website is an optional inspection tool outside this pipeline.

## Run the one-unit pipeline

Use Python 3.10+ and the existing project requirements:

```sh
python3 -m pip install -r utility/requirements.txt -r check_diff/requirements.txt
```

First [prepare a fixed v1.3 unit](../utility/README.md). Then run:

```sh
python3 check_diff/pipeline.py \
  --input prepared --output run \
  --connection /private/connection.json \
  --model gpt-6-astra --effort max --workers 50
```

The connection JSON contains `base_url` and `api_key`. Alternatively, provide the same JSON through `CHECK_DIFF_API_CONFIG_JSON` and omit `--connection`. Keep connection files, original PDFs and execution artifacts outside Git. The HTTP client uses the standard library; no API SDK is required.

The pipeline enforces **one paper unit** and a fresh output directory:

1. Verify the fixed selection, original PDF hashes and physical page counts. Keep the complete version inventory outside individual assessments.
2. Upload every selected version concurrently to `/files`, once per version. Check its file ID, filename, byte count and `processed` status. Use `--upload-workers N` to limit this separate upload pool; the default starts all selected versions of the unit.
3. As soon as both endpoints of a pair are processed, freeze its two-file manifest and submit that pair to the comparison pool. Other versions continue uploading. `--workers` limits simultaneous comparisons, with no fixed start delay.
4. Submit the pair's two uploaded file IDs and prompt to `/responses`. Check the completed response, model settings and result schema, then save the judgment and execution evidence independently for that pair.
5. Wait for upload and comparison work to settle, then delete the temporary files whose IDs were recorded by this run. Preserve local original PDFs and all run records. Failed comparisons or incomplete cleanup produce a nonzero exit status.

`--upload-only` exercises upload, processing and cleanup without creating any model judgments. An upload failure stops subsequent pair dispatch; already submitted work settles before cleanup. Scientific uncertainty is a valid result and does not itself trigger retry or failure.

## Results and timing

```text
run/
  run_manifest.json
  uploaded_inputs.json
  uploaded_pair_inputs/<arxiv_id>__vX_vY.json
  output/<arxiv_id>/diff_vX_vY.json
  output/<arxiv_id>/diff_vX_vY.execution.json
  ... timestamped receipts, saved prompts and per-attempt artifacts
```

The judgment schema preserves the original comparison workflow:

- **A1:** `comparable`, `partially_comparable` or `unable_to_determine`, with justification.
- **A2:** separate `yes`, `no` or `uncertain` judgments for the before and after versions, with justification.
- **14 dimensions:** M1, E1–E6, T1, C1, C2, D1, R1, P1 and P2. Each contains `change` (`Major change`, `minor change`, `maybe` or `no change`) and an English `justification`.

Use [compare.md](prompts/compare.md) for the task and [rubric.md](prompts/rubric.md) for the scientific criteria. The API prompt replaces local PDF-skill instructions with attachment-reading instructions; the dimensions and schema are unchanged. Dimensions are assessed independently. No aggregate S2 pass/fail or scientific-improvement label is inferred.

Execution receipts retain UTC timestamps, per-pair and per-attempt durations, input/prompt/schema/result hashes, returned model settings, usage, retries and errors. Only complete, schema-valid responses are saved as successful judgments. Summed pair durations are not experiment wall time: upload and comparison phases overlap, and the run manifest records that overlap. Per-pair time includes local preparation, request attempts and retry waits, but excludes executor queue time.

The pipeline defaults to `--timeout 1800 --retries 1`: a per-request timeout of 1,800 seconds and at most one additional attempt. Retries apply to transient connection, timeout, stream and output-validation failures and retryable HTTP statuses. Authentication, permission, billing/quota, oversized input, refusal, incorrect model settings and local preparation/write failures stop without repeated API calls. Backoff respects server minimum waits; waits above the configured cap stop the pair for later recovery. Every attempt has a separate audit trail. A lost connection can follow provider acceptance, so retry is not exactly-once execution. Use `--retries 0` to disable retries; see `--help` for limits.

## Compatibility entry points

The original Codex CLI backend remains available and reads local PDFs through the installed PDF skill:

```sh
python3 check_diff/runner.py prepared --output codex_output \
  --backend codex --model gpt-5.5 --effort medium --workers 4
```

It requires an authenticated Codex CLI and the PDF skill. The lower-level runner also supports `--backend responses` with inline original PDFs (`--input-mode pdf`) or an existing verified upload manifest (`--input-mode file_ids --uploaded-inputs manifest.json`). It does not manage upload or deletion; use `pipeline.py` for that lifecycle. API and Codex reading mechanisms differ even though they share the rubric.

The runner can select `--paper`, accept a single pair directory, list work with `--dry-run`, and use `--overwrite` to recompute results. Responses execution reuses completed results only when their recorded inputs and settings match. The direct Responses runner defaults to three retries; the pipeline explicitly uses one. The pipeline requires a fresh run directory so upload manifests and run history remain immutable.

## Optional inspection website

Build this separately after a run. It is read-only and is **not a completion gate, a model input, or a formal screening stage**:

```sh
python3 check_diff/build_preview.py \
  --input prepared --results run/output \
  --run-manifest run/run_manifest.json --output preview
python3 -m http.server 8767 --bind 127.0.0.1 --directory preview
```

The English site displays individual judgments, source/version metadata, actual saved prompts, timings and arXiv version links. It does not serve manuscript PDFs or API credentials. Private stream and response artifacts are not copied wholesale into the website.

## Validation limits

An earlier live one-unit run completed five comparisons, with a visual-coverage uncertainty in one result. The later six-way PDF upload probe timed out at about 120 seconds; a single-file control succeeded in about 13.8 seconds in a different execution environment. These observations do not isolate concurrency as the cause or establish production upload stability. Local tests cover scheduling and artifact handling, not provider reliability or the scientific accuracy of judgments.

```sh
python3 -m unittest discover -s check_diff -p 'test_*.py'
```

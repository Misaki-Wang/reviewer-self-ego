# Data preparation

## v1.3 preparation

Use `prepare_v13_pilot.py` for the v1.3 index or an individual source shard.
It requires Python 3.11+ and the existing `pypdf` dependency in
`utility/requirements.txt`. It reads cached, original version-specific PDFs;
it does not download papers, install tools, or invoke a model.

Each paper selects the earliest-valid maximum-length sequence from its complete
frozen version history. Every consecutive selected gap is strictly greater than
2,592,000 seconds (30 days); skipped original versions remain in the metadata.
A selected sequence of `k` versions contributes `k - 1` planned pairs.

```sh
# Inspect the complete ACL2026 scope without creating output or reading PDF contents.
python3 utility/prepare_v13_pilot.py --source v1.3/acl2026.json \
  --pdf-root /path/to/arxiv_pdf --all --dry-run

# Freeze a reproducible, source-balanced sample before inspecting PDF contents.
python3 utility/prepare_v13_pilot.py --source v1.3/index.json \
  --pdf-root /path/to/arxiv_pdf --units 200 --seed 20260911 \
  --output input_data_v13 --plan-only

# Materialize exactly that saved plan from the existing PDF store.
python3 utility/prepare_v13_pilot.py \
  --materialize-plan input_data_v13/selection_manifest.json \
  --output input_data_v13 --pdf-root /path/to/arxiv_pdf
```

The PDF store layout is `<arxiv_id>/<arxiv_id>vN.pdf`. `--all` selects the full
eligible, resolved inventory. `--units N` uses the existing deterministic,
source-balanced sampling rule; `--per-source N` assigns that many distinct papers
per source. The default is five papers per source. `--all` cannot be combined with
either sampling option; `--units` overrides `--per-source`. Source membership is preserved even when a paper appears in more
than one shard; each selected paper contributes its pairs once. Sampling does
not depend on local PDF availability.

`--dry-run` prints counts without creating a run. `--plan-only` saves the exact
paper IDs, selected versions, complete original records, source hashes and
requested PDFs in `selection_manifest.json`. Materialization verifies the fixed
plan and PDF identities/hashes, then creates the comparison layout:

```text
input_data_v13/
  selection_manifest.json
  materialization_manifest.json
  pairs.json
  <arxiv_id>/metadata.json
  <arxiv_id>/vN_vM/vN.pdf
  <arxiv_id>/vN_vM/vM.pdf
  <arxiv_id>/vN_vM/pair.json
```

Missing or invalid selected PDFs are recorded and cause a nonzero exit. The
selected papers are not replaced and no alternative version pairs are created.
After resolving input problems, materialize the same plan again. Use a fresh
output directory for a different selection. The [v1.3 inventory](../v1.3/README.md)
reports planned counts, not guaranteed runnable pairs.

The prepared directories can be passed to the existing comparison runner;
preparation itself never starts comparison calls. Full original metadata stays
outside the isolated pair assessment. Source JSON and original PDFs are read-only.

`source_provenance.py` provides a read-only verifier for callers that freeze code
as well as data. Its `verified_sources(project, worktree, sources, source_commit,
extra_files=())` API requires an explicit immutable commit and verifies exported
deployment manifests or matching local Git bytes. It supports both size/hash
records and the versioned hash-only deployment format with separate v1.3 shard
hashes. Invalid manifests do not fall back to Git. MBZ-specific batch orchestration
remains outside this portable preparation utility.

```sh
python3 -m unittest discover -s tests/utility -t . -p 'test_*.py'
```

## Runtime modules

- `prepare_v13_pilot.py`: the current preparation CLI and v1.3 selection logic.
- `prepare_input_data.py`: shared `write_json`, `digest`, `validate_pdf` and
  `materialize` functions. This retained import path is not a second preparation
  CLI; the old v1.1 downloader and sampling workflow are no longer active.
- `source_provenance.py`: read-only source verification for version-frozen callers,
  including the MBZ batch-preparation wrapper.

Current tests live in [tests/utility](../tests/utility). The old v1.1 test is
retained as non-executable historical text under [tests/archive](../tests/archive).
Earlier executable v1.1 code remains in Git history.

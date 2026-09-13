# Reviewer Self-Ego

The current main workflow prepares **v1.3** version pairs and compares their two
local PDFs through Codex. Production code, tests, frozen data and experiment
reports are kept in separate locations.

| Location | Role |
| --- | --- |
| [`utility/`](utility/README.md) | v1.3 planning/materialization, shared PDF/JSON helpers and code/data provenance verification |
| [`check_diff/`](check_diff/README.md) | Pair comparison, current judgment schema, prompts and actual reported token accounting |
| [`tests/`](tests/README.md) | Main-workflow regression tests and frozen-release validation; retired tests are marked separately |
| [`v1.3/`](v1.3/README.md) | Ten source shards, index and per-source unit/pair counts |
| [`main_experiment.html`](main_experiment.html) | Saved experiment report; not an execution dependency |
| `review_experiment/` | Separate later-stage experiment, outside this main-workflow cleanup |

## Current workflow

1. Follow the [v1.3 preparation instructions](utility/README.md#v13-preparation) to
   freeze a selection and materialize the selected pairs from existing PDFs.
2. Run the [comparison entry point](check_diff/README.md), which gives Codex the
   two original-PDF paths and the comparison prompt. It produces `prelim` A1/A2,
   fourteen independent change judgments and a separate `improvement` judgment.
3. Retain the result JSON, execution receipts and actual CLI-reported token
   summaries. Missing usage stays unknown. Comparison tools use only the supplied
   local PDFs; the Codex client connects to the model service for inference.

`utility/prepare_v13_pilot.py` is the preparation CLI. The historical filename
`utility/prepare_input_data.py` now contains only the shared PDF/JSON functions
used by that CLI; its v1.1 sampling/downloading entry point has been retired.
`utility/source_provenance.py` is used by callers such as MBZ batch preparation
that also need to verify and freeze an explicit code version.

## Tests and review

With the existing dependencies from `utility/requirements.txt` and
`check_diff/requirements.txt`, run from the repository root:

```sh
python3 -m pip install -r utility/requirements.txt -r check_diff/requirements.txt
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```

The active suite uses synthetic local PDFs and simulated Codex calls. The release
check additionally reads the frozen v1.3 JSON files. It does not launch model
experiments or execute tests from `review_experiment/`. See [tests/README.md](tests/README.md)
for focused commands and the retired v1.1 test archive.

## Frozen dataset

[v1.3/](v1.3/) contains **3,724 unique arXiv papers and all 14,086 original
versions** from the frozen export created at `2026-09-11T06:38:05.787685Z`.
No S2 content assessment, page-count filter or license filter was applied when
building that candidate inventory. The current comparison workflow and saved
report are separate from those immutable release annotations.

## Scope and selection

| Raw-input stage | Count |
| --- | ---: |
| Public source entries across 10 sources | 34,127 |
| Confirmed source-to-arXiv entries | 25,646 |
| Unique confirmed arXiv papers | 25,613 |
| Source entries with unresolved identity | 8,481 |
| Original versions in the confirmed raw union | 51,410 |
| Temporally eligible unique papers | 3,724 |
| Temporally ineligible confirmed papers | 21,889 |
| Original versions of eligible papers | 14,086 |

Sources cover ICLR 2026; ICML and ACL 2025–2026; CVPR, EMNLP, ICCV and NeurIPS 2025; and the TMLR 2025-plus collection. Confirmed identities are deduplicated by arXiv ID, preserving 3,738 eligible source memberships. Unmatched, candidate-only and ambiguous identities remain unknown, not temporal failures. Complete frame processing does not establish exhaustive identity recall. Earlier selected releases are not selection inputs.

Eligibility requires an ordered subsequence of **at least three original versions**, with **every consecutive selected gap strictly greater than 2,592,000 seconds (30 elapsed days)**. For a three-version witness, both gaps must pass. Exactly 30 days fails; intermediates may be skipped (`v1 → v3 → v5`). First-to-last span alone is insufficient. Unknown timestamps must remain null with reasons and cannot establish a passing gap; this frozen confirmed inventory has zero unknown/conflicting dates and zero unresolved temporal outcomes.

## Schema and interpretation

`index.json` uses schema `v1.3-source-package-1`. Its `dataset_metadata` preserves **all original non-record top-level fields unchanged**, including global statistics, provenance and protocol. `source_json` identifies the original combined file and its bytes/hash; `files[]` gives each `source_id`, relative `path`, `record_count`, bytes and SHA-256. `record_order` preserves the original paper order.

Each `<source_id>.json` uses schema `v1.3-source-shard-1`, with `release`, `source_id`, `record_count` and `records`. Every paper object is unchanged, including memberships in other sources. Cross-source duplicates must match exactly: **3,738 file records deduplicate to 3,724 papers**. Use the manifest to reconstruct the original schema `v1.3-raw-derived-preliminary-release-1`; its `stage` is `preliminary_only`, and `S2_assessed`/`content_screening_performed` are false.

| Field within each `records[]` paper | Meaning |
| --- | --- |
| `arxiv_id`, `arxiv_url`, `title`, `authors` | Unique paper identity and descriptive metadata. |
| `source_ids`, `source_entries[]` | Normalized memberships, venue/presentation fields, source and identity locators. Review scores are null, not zero. |
| `publications[]` | Full native publication and matching records; source-specific structures are preserved. |
| `frozen_N`, `versions[]` | Maximum observed original version and complete numerical history, including skipped versions. |
| `version_history_status`, `version_history_validation_errors` | History validation outcome. |
| `temporal_eligibility` | `status`, `witness_versions`, `witness_edges` and `reasons`; one qualifying example, not every possible triple. |
| `license_metadata`, `raw_metadata` | Latest-record license evidence and raw observation locators. |
| `S2_assessed`, `content_screening_status` | False and `not_performed`. |

Each version contains `version`, UTC ISO `submitted_at`, `date_status`, `date_unresolved_reason`, `date_provenance`, exact-version `arxiv_url`/`pdf_url`, `pdf`, `page_count`, `page_metadata_evidence` and `license_metadata`. Witness edges contain `from_version`, `to_version` and `gap_seconds`.

Read physical pages from **`versions[].page_count.value`**, with `page_count.status`. Counts include references and appendices inside that exact PDF. **2,679 are verified; 11,407 are unresolved**, with null values and explicit reasons. Verified counts carry PDF SHA-256 and parser evidence; `pdf` retains byte counts and acquisition provenance. A populated `pdf.path` does not prove availability. These counts describe the frozen checkpoint, not subsequent downloader progress.

Paper-level `license_metadata.license` reports the latest observed work-record license, preserving raw/normalized URIs, name, code, SPDX value where applicable, and observations. Version-level `license_metadata` contains **3,736 latest-record-context observations and 10,350 unknown historical licenses**. Never inherit a latest license into an unknown older version. Manuscript licensing is separate from descriptive-metadata licensing.

## Dataset provenance

The v1.3 index records the frozen source-file hashes, original combined-JSON hash,
record order and provenance references. The release test reconstructs the combined
JSON in memory and checks those values. Embedded protocol text in the dataset is
historical metadata; it does not configure the current comparison runner.

Original PDFs, external source captures and server-specific runtime state are
outside this repository. Paths retained in the dataset are provenance locators,
not a guarantee that an old file is still available. Historical v1.1/v1.2 releases
remain available through their Git tags.

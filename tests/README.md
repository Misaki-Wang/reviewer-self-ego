# Main experiment tests

Run the active suite from the repository root with Python 3.11+ and the
existing `pypdf` and `tqdm` dependencies:

```sh
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```

The suite contains **127 active tests**:

| Module | Tests | Coverage |
| --- | ---: | --- |
| `check_diff/test_runner.py` | 23 | Pair discovery, result schema, CLI arguments, isolated task state, resume, retries, failures and background completion. |
| `check_diff/test_token_usage.py` | 14 | Reported token counters, unavailable usage, retry receipts and batch boundaries. |
| `utility/test_prepare_v13_pilot.py` | 51 | Strict v1.3 version selection, source integrity, fixed plans, PDF verification, safe materialization and the four retained shared helpers. |
| `utility/test_source_provenance.py` | 38 | Export manifests, pinned local Git sources, hashes, paths, symlinks and read-only verification. |
| `data/test_v13_release.py` | 1 | The real frozen v1.3 release, including exact archive reconstruction. |

Paths in this table are relative to `tests/`. Runtime imports explicitly use
the `check_diff` and `utility` packages. The comparison tests share
`check_diff/fixtures.py` through package-relative imports. Package markers and
standard `unittest` discovery provide the test entry point.

The 126 runtime regressions use temporary fixtures. CLI execution tests run
temporary fake Codex executables or mock comparison calls; they do not call a
model. Preparation fixtures retain their network guards. Provenance tests
create local temporary Git repositories and commits solely as fixtures; they
do not modify the repository under test or access a remote.

## Frozen release check

`data/test_v13_release.py` contains the validation previously embedded in the
root README's `PYCODE` block. This is a **real-data release check**, included in
standard discovery and also runnable separately with only the standard
library:

```sh
python3 -m unittest tests.data.test_v13_release
```

It resolves the repository root from its own file, reads the local `v1.3/`
manifest and ten shards, verifies their sizes and SHA-256 hashes, checks
identical duplicates, and reconstructs the original 130,489,299-byte archive
in memory. It checks 3,724 unique papers, 14,086 original versions, source
memberships, strict temporal witness gaps, page/license nulls and the absence
of content screening or page/license filtering. It writes no release files,
does not require PDFs, and may use several hundred MB of memory. These fixed
counts describe the frozen release, not later experiment results.

To run just the 126 fixture-based runtime regressions:

```sh
python3 -m unittest tests.check_diff.test_runner tests.check_diff.test_token_usage \
  tests.utility.test_prepare_v13_pilot tests.utility.test_source_provenance
```

## Retired tests and excluded scope

[`archive/test_prepare_input_data_v11.py.txt`](archive/test_prepare_input_data_v11.py.txt)
preserves the original v1.1 test file byte for byte. Its four historical tests
are text, excluded by `test_*.py` discovery, and must not be imported against
the current helper module. See the [archive instructions](archive/README.md)
for reproduction from base commit `e3eced0`.

Before cleanup, the main suite contained 129 tests (37 comparison and 92 utility).
The three retired v1.1 workflow checks leave the active suite; the fourth
historical check, `materialize` copy fallback, remains active in the v1.3
helper tests. The extracted release check adds one test: **129 - 3 + 1 = 127**.

`review_experiment/` is excluded from this suite: it is not imported,
discovered or executed. This suite covers only the current main experiment
runtime and its frozen release data.

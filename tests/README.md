# Offline tests

Run from the project root with the existing MBZ Python/dependencies. Use private
temporary output under res (not res/archive or source/dependency directories),
because review output guards intentionally protect those locations.

~~~sh
mkdir -p res/test-scratch
TMPDIR="$PWD/res/test-scratch" python3.12 -B -m unittest discover -s tests -t . -p 'test_*.py'
~~~

Use a CPU-only Slurm allocation for sustained tests. Test PDFs and Codex CLI calls
are synthetic; the explicit sandbox_smoke tool is not part of test discovery.

| Directory | Coverage |
| --- | --- |
| check_diff/ | Comparison runner, I/O, isolated state, resume and errors |
| utility/ | Grouped-module imports, anonymization/audit, downloads, source provenance, workflow and shared accounting |
| mbz/ | Batch preparation, scheduling, recovery and independent verification |
| data/ | Full source-data packaging, original records and metadata preservation |
| review_experiment/ | Source profiles/scales, shared baseline, concurrent review I/O and resume |

The September 2026 utility directory migration passed **785 related tests** across
utility, MBZ batch, comparison, review and benchmark contracts. The data-packaging
suite was excluded; earlier baseline runs timed out and were not complete passes.
This records a scoped verification result, not a current full-discovery count.
The preceding rules v6 anonymization pilot passed 151 rules, driver and independent
audit regressions. These totals describe different runs and must not be added.

The accounting tests are utility/test_token_usage.py and
utility/test_token_usage_loading.py. Both runners share utility/codex/token_usage.py;
new frozen runs retain source_snapshot/utility/token_usage.py. Existing historical
snapshots keep their original modules and paths.

The independent usage reconstruction in utility/batch/verify_local_batch.py remains
separate so it can detect errors in the writer rather than repeat them.
archive/ contains retired historical test text and is not discovered.

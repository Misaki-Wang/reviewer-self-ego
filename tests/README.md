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

| Directory | Tests | Coverage |
| --- | ---: | --- |
| check_diff/ | 25 | Comparison runner, I/O, isolated state, resume and errors |
| utility/ | 143 | Shared token accounting/loading, source data/provenance and workflow |
| mbz/ | 110 | Batch preparation, scheduling, recovery and independent verification |
| data/ | 2 | Full source-data packaging, original records and metadata preservation |
| review_experiment/ | 19 | Explicit review I/O, prompts, fake CLI and resume |

The accounting tests are utility/test_token_usage.py and
utility/test_token_usage_loading.py. Both runners share utility/token_usage.py;
new frozen runs retain source_snapshot/utility/token_usage.py. Existing historical
snapshots keep their original modules and paths.

The independent usage reconstruction in utility/verify_local_batch.py remains
separate so it can detect errors in the writer rather than repeat them.
archive/ contains retired historical test text and is not discovered.

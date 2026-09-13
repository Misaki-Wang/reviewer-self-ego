# Retired v1.1 preparation tests

`test_prepare_input_data_v11.py.txt` is the exact original
`utility/test_prepare_input_data.py` from base commit `e3eced0`. Preserve its
bytes. The `.py.txt` suffix keeps its four historical tests out of active
`unittest` discovery.

The v1.1 sampling/downloading/preparation workflow has been retired. Its tests
must not import the current `utility.prepare_input_data`, which retains only
the shared `write_json`, `digest`, `validate_pdf` and `materialize` helpers.
Current helper behavior is checked in `../utility/test_prepare_v13_pilot.py`,
including the historical hardlink-to-copy fallback case.

To reproduce the retired four-test suite, extract both original files into a
separate temporary directory from the local Git base and run them with Python
3.11+ and the existing `pypdf` dependency:

```sh
archive_dir=$(mktemp -d)
git show e3eced0:utility/prepare_input_data.py > "$archive_dir/prepare_input_data.py"
git show e3eced0:utility/test_prepare_input_data.py > "$archive_dir/test_prepare_input_data.py"
(cd "$archive_dir" && python3 -m unittest test_prepare_input_data)
```

The historical tests simulate the arXiv transport and use temporary PDFs.
These instructions are for historical reproduction only; the retired suite
is not part of the current 127-test command in [../README.md](../README.md).

# Experiment results

New runs use one directory per explicit run ID: res/<run-id>/.
Preparation, comparison and independent verification share that directory.
Keep its frozen inputs/source, raw responses, all attempt receipts and reported
usage together. Missing usage is not zero.

Historical t1.1 and t1.2 are under archive/t1.1 and archive/t1.2. Their original
records and frozen configurations are preserved. They are archived evidence,
not automatically resumable current runs. See archive/README.md for relocation
information. No archive or existing run is overwritten by preparation.

Git ignores experiment payloads and archives. Share selected result artifacts
or the self-contained presentation files under ../html, as appropriate.

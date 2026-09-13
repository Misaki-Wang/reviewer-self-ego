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

## Published review pilot

[published/review_exp_iclr2026_pilot/](published/review_exp_iclr2026_pilot/) contains the retained ICLR 2026 review pilot: five papers, 18 versions and 234 reviews under the original 13 conditions. It includes curated full reviews, five byte-identical raw result files, timing/token records, settings, frozen prompts and checksums. This release is explicitly allowed through the result ignore rules. Other run outputs and archives remain ignored.

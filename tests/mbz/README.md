# Offline MBZ batch regression tests

These tests import the maintained modules from utility at the formal project root. Run discovery from the project root with the existing Python dependency environment:

    python -B -m unittest discover -s tests/mbz -t . -v

SOURCE_CODE_ROOT may explicitly point fixture source reads at a staged formal project. Imports still use the project on PYTHONPATH. Use PYTHONDONTWRITEBYTECODE=1 and a private TMPDIR. Sustained tests on MBZ run in a Slurm allocation with two CPUs and zero GPUs.

The 110 methods cover preparation (28), scheduler/recovery (42), and independent artifact verification (40). Preparation builds generated original-version PDFs, complete page/version metadata, a validated source_data catalog under data/index, one utility/project.json, and a file manifest using utility.source_provenance.write_manifest. Every FROZEN_FILES utility source and both metadata ledger/provenance files are present before pinning; controller, preparation entry and workflow mutations after the pin are rejected before a run is created. Outputs stay under res, including the custom configured-output fixture. Intentional authorized source edits refresh both catalog proofs and the content revision; tamper cases retain their original authorization.

Scheduler tests invoke only their generated fake CLI; preparation rejects subprocess dispatch except for its explicit frozen-bundle import probe. No test launches a real model, fetches data, or pushes Git. Numerical, timeout, retry, recovery, scientific schema and original-version scope assertions remain covered.

The formal-layout migration archives exact originals and records the two obsolete manifest-format replacements under res/.migration/formal_layout_20260913/mbz-tests. The bounded migration pass discovers all methods and runs the verifier plus preparation subset; the full scheduler/timeout suite is reserved for parent integration. The pinning follow-ups run only controller/metadata tamper tests and the existing successful frozen-bundle test, including exact workflow, controller and metadata bytes/provenance. Updating ledger provenance alone never refreshes the authorized source revision.

Executing-source identity is checked with six authorized alternate-project module variants: each refreshed manifest is valid, but loaded bytes must still match it before preparation. Direct API runs below res/archive or res/.migration are rejected even when runs_root is ordinary res. These focused regressions retain the original data-root overlap case.

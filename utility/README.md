# source_data preparation and provenance

The active export root contains check_diff/, utility/, data/index and data/pdf.
Current configuration is utility/project.json. Use its existing runtime settings;
no new Python dependencies are required.

## Dataset packaging and preparation

data/index/index.json declares dataset source_data and schema
source-data-index-1. Each requested source shard declares
source-data-source-shard-1. The combined source_data stream uses
source-data-source-1 and is reconstructed in index record_order; its size and
SHA-256 are recorded in source_json without creating another corpus copy.

Historical release headers, protocol/license/page provenance and their original
hashes remain in data/index/provenance/build_metadata.json. These historical
status strings are evidence of the original build, not current execution policy.
Paper records, original versions, all source memberships and observation ledgers
are preserved exactly.

The maintained preparation module is prepare_source_data.py:

```sh
python3.12 -B -m utility.prepare_source_data \
  --source data/index/index.json --pdf-root data/pdf --all --dry-run
python3.12 -B -m utility.prepare_source_data \
  --source data/index/index.json --pdf-root data/pdf --all \
  --output res/NEW_RUN_ID/prepared --plan-only
python3.12 -B -m utility.prepare_source_data \
  --materialize-plan res/NEW_RUN_ID/prepared/selection_manifest.json \
  --pdf-root data/pdf --output res/NEW_RUN_ID/prepared
```

Replace NEW_RUN_ID with an explicitly selected new run. Dry-run prints metadata
planning statistics without parsing PDFs or writing output. --plan-only freezes
selection; materialization consumes that exact plan without replacement. --all
retains every deduplicated record in source order and fails if any record is
ineligible or unresolved. Explicit --units or --per-source retain the existing
deterministic source-balanced sampling behavior.

Every consecutive selected gap is strictly greater than 2,592,000 seconds.
Intermediate original versions may be skipped. Maximum-sequence selection,
identity checks, physical page counting, PDF hashes, null/unavailable handling,
full original metadata, fixed selection and output safety guards are unchanged.
Planning file presence never determines the sampled population. No preparation
command downloads PDFs, makes model calls or resumes an experiment.

Public shared constants:
- PLAN_SCHEMA_VERSION = source-data-maximum-sequence-plan-1
- OBSERVED_METADATA_SCHEMA_VERSION = source-data-observed-original-pdf-metadata-1

load_source(path) returns records plus provenance whose dataset_metadata.dataset
is source_data. require_source_data checks that active dataset marker.
prepare_input_data.py remains the shared PDF/JSON helper implementation.

## Content manifest API

source_provenance.py is independent of Git commits and worktree layout.

```python
from pathlib import Path
from utility.source_provenance import build_manifest, write_manifest, verified_sources

project = Path.cwd()
preview = build_manifest(project)       # read-only
manifest = write_manifest(project)      # explicit maintenance write
blobs, deployment = verified_sources(
    project, project, ["acl2026"], manifest["revision"],
    extra_files=("utility/prepare_source_data.py",
                 "utility/prepare_input_data.py",
                 "utility/source_provenance.py"),
)
```

code_root must equal project. The manifest at utility/source_manifest.json is:

```json
{"schema_version":"source-manifest-1","revision":"64 hexadecimal characters",
 "files":{"relative/path":{"bytes":123,"sha256":"64 hexadecimal characters"}}}
```

revision is SHA-256 of the UTF-8 encoding of
json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False).
The manifest pins both byte count and digest. It includes current check_diff
sources/prompts/core README/requirements, utility/*.py and data/index JSON plus
metadata/provenance files. It excludes utility/project.json, its own JSON file,
.runtime, caches, tests, res, html and review_experiment. Configuration is proofed
separately to avoid a cyclic content revision.

build_manifest verifies a stable source inventory and returns an object without
writes. write_manifest atomically replaces only utility/source_manifest.json and
returns that object. Parent integration should regenerate it after all source
changes are complete; do not use a historical Git hash as source_revision.

verified_sources is read-only. It verifies the authorized 64-character revision,
the index and every requested shard, declared source identities/counts, pinned
sizes/digests, exact configuration and source-manifest bytes, and requested extra
files. It rejects path traversal, symlinks, nonregular sources, duplicate JSON
keys, malformed manifests and detected changes during reads. Unrequested payload
contents are not implicitly certified. Request metadata/provenance files through
extra_files when their exact bytes must also be verified and frozen.

Returned blob keys are check_diff/<CORE_FILES>, data/index/index.json,
data/index/<requested-source>.json, requested extra paths,
deployment_provenance/project.json and root-level source_manifest.json.
deployment has project and source_manifest keys, each with actual path, bytes
and SHA-256. No fabricated source_commit or Git fallback is used.

## Tests

Current tests live in tests/utility/test_prepare_source_data.py,
tests/utility/test_source_provenance.py and tests/data/test_source_data.py.
Use an owned private-project TMPDIR. The data test reconstructs both the current
combined stream and the historical combined stream to prove complete record
equivalence, and checks unchanged observed-metadata bytes. Original Git/release
transport tests remain in migration backups; current guards exercise the content
manifest interface. Workflow and MBZ batch tests are maintained by their owners.

## Shared token accounting

Both experiment runners use the single utility/token_usage.py implementation.
It preserves raw reported usage and the existing aggregation contract. New batch
snapshots freeze it at source_snapshot/utility/token_usage.py; historical snapshots
are unchanged. The independent verifier retains its separate arithmetic.

## Comparison concurrency

The formal workflow accepts 1 through 96 comparison workers. Optional pressure
fallback ladders are 64/48/32/16 and 96/80/64/48; --workers must match the first
value. Reductions affect new admissions and preserve active Codex calls. Live
run control remains under res/<run-id>/control.json.

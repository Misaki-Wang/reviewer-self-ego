# source_data preparation and provenance

The active export root contains check_diff/, utility/, data/, and data_full/.
Current configuration is utility/project.json. Use its existing runtime settings;
no new Python dependencies are required.

## Download all versions, then build pairs

These are separate stages. `download_papers` downloads every version recorded in
the source catalog (`v1` through `frozen_N`), including intermediate versions that
pair selection skips. It does not apply temporal eligibility or page filters and
does not discover versions newer than the frozen catalog.

The download root is `data.downloads_root` in `utility/project.json`, currently
`downloads_papers`. The layout is:

```text
downloads_papers/
  download_manifest.json
  acl2026/
    2305.14299/
      metadata.json
      v1.pdf
      v2.pdf
      v3.pdf
```

`metadata.json` preserves the complete original paper record. A paper belonging
to multiple conferences is stored under each selected conference; verified bytes
are reused within the invocation to avoid duplicate network requests. Existing
valid files are skipped, and validated files from the configured legacy `data/pdf`
store can also be reused. Downloads use explicit version URLs, rate limiting,
bounded retries, temporary files and PDF/hash validation before replacement.
Failures are recorded in `download_manifest.json` and cause a nonzero exit status;
rerunning retries failed/missing files. This manifest describes the latest
invocation's scope; earlier cached papers and their metadata remain in place.

Download concurrency defaults to one worker. `--workers` accepts 1 through 1,024
concurrent original-version groups; every worker and retry shares the single
`--interval` admission gate. `--validation-workers` independently bounds
simultaneous PDF validation (default: download concurrency).
`--stop-on-retryable-error` drains active work and stops after exhausted transient
file failures so an external controller can recover. These controls do not start
model calls.

The persistent 1024 → 512 → 256 → 128 recovery ladder used by the deployed
download run belongs to its frozen outer controller under
`res/maintenance-download-acceleration-20260915/run-03/`. A standalone downloader
CLI invocation does not implement that ladder or restart itself.

From the project root with Python 3.11+ and the existing pypdf dependency:

```sh
# Inspect the complete download inventory without network requests or writes.
python3.11 -B -m utility.data.download_papers --dry-run

# Download all catalogued versions for a conference (omit --conf for all sources).
python3.11 -B -m utility.data.download_papers --conf acl2026

# Or download all versions of one paper.
python3.11 -B -m utility.data.download_papers --conf acl2026 --arxiv-id 2305.14299

# Anonymize each needed PDF separately before pairing, for example:
python3.11 -B -m utility.anonymize.anonymize_pdf downloads_papers/acl2026/2305.14299/v1.pdf

# Once all required versions are clean, build pairs with the existing pairing rule.
python3.11 -B -m utility.data.build_pairs --conf acl2026 --all \
  --output res/NEW_RUN_ID/prepared
```

`--conf` uses the catalog's source ID, such as `acl2026` or `iclr2026`.
Download filters `--conf` and `--arxiv-id` are repeatable. `--output` changes the
download root. `data.clean_root` configures the anonymous output and downstream
input store, normally `clean_data`; `build_pairs --pdf-root` can select another
clean store. Pair construction defaults to the configured source index and clean root;
`--source` can provide a source shard or combined catalog. Use matching source/conference scopes when downloading and building pairs.
The clean tree needs only PDFs; pairing reads version timestamps from the catalog.
There is no fallback to downloaded originals if a clean version is missing. Downloading a single paper does not narrow
the pair population automatically: pass the same `--arxiv-id` filter to
`build_pairs` when building only its pairs. Explicit paper IDs default to all
papers within that filter, unless a sampling scope is also supplied.

`build_pairs` shares `prepare_source_data`'s selection and materialization code.
It supports `--all`, `--units`, `--per-source`, `--dry-run`, `--plan-only` and
`--materialize-plan`. Without a scope flag it keeps the existing default of five
papers per source. Selected gaps remain strictly greater than 2,592,000 seconds,
and pairs connect adjacent versions in the maximum selected sequence. Missing
PDFs fail the fixed selection; they never cause replacement sampling. Pair
outputs retain the existing `<id>/vX_vY/` and `pairs.json` format for downstream
consumers. `--all` still requires every scoped record to be eligible and resolved.

The existing `prepare_source_data`/formal batch entry points keep their legacy
`data/pdf/<id>/<id>vN.pdf` default. `prepare_source_data --pdf-layout conference`
also accepts the new store. The dedicated `build_pairs` entry point selects that
layout automatically; frozen plans record the layout so they can be resumed or
transported without guessing from file presence.

## Anonymize one PDF for review

`anonymize_pdf.py` operates on **one PDF**, without scanning or processing its
directory. It uses the authenticated Codex CLI and an installed PDF skill. The
existing local Python 3.11 environment includes the required PyMuPDF dependency.

```sh
# Inspect the paths first; this makes no model calls or PDF edits.
python3.11 -B -m utility.anonymize.anonymize_pdf \
  downloads_papers/iclr2026/2301.12412/v1.pdf --dry-run

# Process this single PDF (identify, then audit; retry with feedback if needed).
python3.11 -B -m utility.anonymize.anonymize_pdf \
  downloads_papers/iclr2026/2301.12412/v1.pdf --model gpt-5.5 --effort high
```

The default result is `clean_data/iclr2026/2301.12412/v1.pdf`: the same relative
conference/paper/version path as the download store. Original PDFs are unchanged.
Only the PDF is copied to the clean tree; `metadata.json` is not needed for review
and is not copied. `--output` can select an explicit PDF path, and is required for
input PDFs outside the configured download store. The script rejects directory
inputs. It does not start comparison or review experiments.

The stages are:

1. Render all original pages and extract coordinate-bearing text spans. A Codex
   session using the PDF skill identifies author names, affiliations, contact
   details, acknowledgments, author links, venue/submission/acceptance notices,
   arXiv/DOI stamps and other direct identity clues, including logos/images.
2. Apply actual PyMuPDF content redactions and rewrite the PDF with garbage
   collection. Remove document/XMP metadata, annotations, forms, links, outlines,
   attachments and hidden text. Scientific text, equations, diagrams and ordinary
   reference entries are preserved; the layout is not reflowed.
3. Check the page count/geometry, missing identity strings and hidden metadata.
   Compare rendered pixels outside the specified redaction regions against the
   original. If smoothing alone differs, require an exact second comparison with
   antialiasing disabled and record the affected pages. A second fresh Codex session uses the PDF skill to visually review
   every page. Publish the PDF only if both local checks and that review pass.

Uncertain identification, changes outside the selected rectangles, a failed visual
review or a CLI failure trigger another attempt with the previous error, redaction
plan and audit feedback. Each attempt starts from the original PDF. The default
`--max-attempts 3` allows up to three attempts (at most six CLI calls); processing
stops as soon as an attempt passes. Exhausting the limit produces an error without
publishing a new PDF. Reports retain every attempt and its token usage. All CLI
instructions are in English and state that the user authorized anonymization of
these publicly downloaded papers. Rendered pages are attached to each CLI prompt.
This is automatic removal/review of direct identity clues, not a guarantee that a
paper cannot be identified from its scientific content, title or retained path.
Visually spot-check real outputs before using them in a blinded experiment.

The audit report defaults to `res/anonymize_pdf/<conference>/<paper>/vN.json` and
contains each attempt's plan, CLI responses, validation status, input/output hashes and
reported token usage. It can contain removed identity information and stays
outside `clean_data`. A matching completed output is reused without model calls;
`--overwrite` explicitly regenerates it. `--pdf-skill` chooses a skill, `--codex`
chooses the executable, and `--timeout` bounds each CLI call. The bundled PDF
skill's edit marker requires Node.js (`--node` can select it).

All CLI sessions are ephemeral, use the installed PDF skill, and restrict tools
to the current scratch workspace, the one read-only source PDF and runtime/skill
files. No changes are made to review_experiment's existing isolation, prompts,
output format or model-call implementation.

Implementation references: [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Codex skills](https://learn.chatgpt.com/docs/build-skills),
[PyMuPDF redactions](https://pymupdf.readthedocs.io/en/latest/page.html#Page.apply_redactions).
Offline tests are in `tests/utility/test_anonymize_pdf.py` and use synthetic PDFs
and a fake CLI, including actual text/image redaction and an output/resume test.

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
python3.12 -B -m utility.data.prepare_source_data \
  --source data/index/index.json --pdf-root data/pdf --all --dry-run
python3.12 -B -m utility.data.prepare_source_data \
  --source data/index/index.json --pdf-root data/pdf --all \
  --output res/NEW_RUN_ID/prepared --plan-only
python3.12 -B -m utility.data.prepare_source_data \
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
    extra_files=("utility/data/prepare_source_data.py",
                 "utility/data/prepare_input_data.py",
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
sources/prompts/core README/requirements, utility/*.py, the anonymize/data/batch/codex
packages, and the data/data_full indexes and metadata/provenance files. It excludes utility/project.json, its own JSON file,
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
tests/utility/test_download_papers.py, tests/utility/test_source_provenance.py and
tests/data/test_source_data.py. Download tests use synthetic PDFs and fake network
responses, including an end-to-end clean-store-to-pairs check.
Use an owned private-project TMPDIR. The data test reconstructs both the current
combined stream and the historical combined stream to prove complete record
equivalence, and checks unchanged observed-metadata bytes. Original Git/release
transport tests remain in migration backups; current guards exercise the content
manifest interface. Workflow and MBZ batch tests are maintained by their owners.

## Shared token accounting

Both experiment runners use the single utility/codex/token_usage.py implementation.
It preserves raw reported usage and the existing aggregation contract. New batch
snapshots freeze it at source_snapshot/utility/token_usage.py; historical snapshots
are unchanged. The independent verifier retains its separate arithmetic.

## Comparison concurrency

The deployed formal workflow accepts 1 through 256 comparison workers. Optional
pressure fallback ladders are 64/48/32/16, 96/80/64/48,
160/128/96/80/64/48 and 256/224/192/160/128/96/80/64/48; --workers must match
the first value. These are planning limits, not a guarantee of runtime capacity.
The project configuration and each frozen run retain their selected worker count. Reductions affect new admissions and preserve active Codex calls. Live
run control remains under res/<run-id>/control.json.

## Share original data through data_full

Existing original source shards in `data/` remain immutable. ICLR2025 adds a
separate three-version shard without rewriting those original records. `data_full/index/index.json`
references hash-pinned original conference shards through explicit `bases`
entries and keeps only disjoint additions in local shard files. The shared loader
resolves full records/order and checks every base, addition and index identity.
Only canonical sibling `data/index/<source_id>.json` bases are supported; ordinary
shard path restrictions remain unchanged.

The three-version catalog resolves 4,334 papers and 16,457 original versions;
the two-version catalog resolves 15,331 papers and 41,945 original versions.
ICLR2025's historical `iclr2025.additions.json` contains the complete 1,900-paper
two-version result. Its active overlay references the 610-paper base shard and
the disjoint 1,290-paper `iclr2025.full-only.json` shard. Both local files are
retained; the older complete file is not the active additions payload. Catalog
membership and the availability of original PDFs are audited separately.

```sh
# Resolve the expanded ICLR catalog without downloading or invoking a model.
python3.12 -B -m utility.data.prepare_source_data \
  --source data_full/index/index.json --pdf-root data_full/pdf \
  --fallback-pdf-root data/pdf --all --dry-run

# Freeze a fresh explicit scope using the expanded catalog; default is still data.
python3.12 -B -m utility.workflow prepare --catalog data_full \
  --source iclr2026 --units 10 --run-id NEW_RUN_ID

# Inspect download scope while directly reusing existing original PDF files.
python3.12 -B -m utility.data.download_papers \
  --source data_full/index/index.json --conf iclr2026 \
  --output data_full/pdf --pdf-layout legacy --reference-pdf-root data/pdf --dry-run
```

Existing original PDFs are read-only references, validated on use; reference reuse
does not copy them into the new store. Missing or corrupt selected files do not
trigger replacement sampling. `build_pairs` rejects fallback roots and reads clean
PDFs only. An explicit custom or clean PDF store in formal preparation also does
not automatically fall back to originals. Each run freezes resolved metadata and
all selected catalog dependencies; later live catalog changes cannot alter it.

## Reference compatible historical judgments

`utility.batch.result_reuse` checks frozen pair inputs and execution evidence before
creating a reference to an existing judgment. It does not dispatch models, change
batch state, or import old attempts into a new run. It preserves historical token
usage separately; missing historical usage remains unknown.

```sh
python3.12 -B -m utility.batch.result_reuse check \
  --source-run res/OLD_RUN --target-run res/NEW_RUN --pair ARXIV_ID__v1_v2
python3.12 -B -m utility.batch.result_reuse create \
  --source-run res/OLD_RUN --target-run res/NEW_RUN --pair ARXIV_ID__v1_v2 \
  --output data_full/results/ARXIV_ID__v1_v2.reference.json
python3.12 -B -m utility.batch.result_reuse load \
  data_full/results/ARXIV_ID__v1_v2.reference.json
```

Compatibility requires identical ordered PDF hashes, scientific templates/schema,
execution code/dependencies, model/settings and proven resource/policy identities.
Only the two verified endpoint path parameters may differ between rendered prompts;
the reference records both exact prompt hashes and the versioned equivalence rule.
Changed/deleted referenced artifacts fail revalidation. Legacy artifacts without
archived PDF-skill/resource identity are incompatible; no missing proof is inferred
from currently installed resources. Unknown isolation policies also fail closed.

## Shared Codex file isolation

`codex_isolation.py` supplies the common permission flags and controlled child
environment for comparison, single-paper review, and both anonymization stages.
Each call receives an explicit list of regular input files and a separate writable
work directory. Image attachments must resolve into that same input list before
the host CLI loads them. Tool/runtime resources use a separate narrow read-only
allowlist. Creating or writing a sandbox-only Linux mount ancestor does not grant
access to the corresponding unmounted host files.

Anonymization keeps controller artifacts outside the identify and audit working
directories. Identify reads its original PDF, extracted page metadata and before
images. Audit additionally reads the candidate PDF, plan and after images; it
cannot read identify's raw response or event log. Every retry gets new directories.

New batch snapshots retain the shared helper beside token_usage. The execution
contract pins its identity and approved runtime/skill paths; recovery validates
saved grants without discovering a newer installed runtime. Old snapshots and
results are preserved and retain an explicit legacy-isolation limitation.

Cross-run result references currently reject the new `isolated_files` policy.
The approved runtime path list is not an archive of runtime bytes and must not be
used as a substitute for the stronger historical resource proof required by
`result_reuse`. A missing proof remains incompatible; no legacy result is relabeled
as an isolated result.


## Rules-only anonymization candidates

`utility.anonymize.anonymize_pdf_rules` generates a deterministic redaction plan
from one PDF and its complete original paper metadata, then reuses the Codex
anonymizer's PDF extraction, redaction and local integrity checks. The rules
processing path makes no Codex CLI, external model-service or network calls.
The Codex-assisted processing workflow remains available separately at
`utility.anonymize.anonymize_pdf`.

```sh
python3.12 -B -m utility.anonymize.anonymize_pdf_rules \
  data/pdf/ARXIV_ID/ARXIV_IDv1.pdf --metadata res/SCOPE/metadata/ARXIV_ID.json \
  --output res/NEW_RULE_TEST/candidates/ARXIV_ID/v1.pdf \
  --report res/NEW_RULE_TEST/reports/ARXIV_ID/v1.json
```

The metadata file must contain one complete paper record, not an entire source
shard. Input identity/version must match that metadata. Outputs, reports and QA
artifacts must stay outside original stores and production `clean_data`. The
driver preserves uncertainty and produces review candidates only.

Exit 0 means `rules_passed`; exit 2 means `needs_manual_review` with a candidate;
exit 1 means validation failed or inputs were invalid. `rules_passed` is not a
visual-review verdict. Matching rules receipts retain their original status and
bind the input PDF, metadata, driver, rule version, helper code and PyMuPDF.
Moving the scripts changed code hashes and can invalidate both rules and Codex
cache matches; historical receipts and frozen source paths are not relabeled.

The rules v6 pilot used the fixed original ICLR sample: five papers, first/latest
versions, 10 PDFs and 219 pages. All 10 candidates were accepted after independent
dual-parser checks, local macOS Vision OCR and agent visual review. Three
scientific-content matches were retained with explicit review dispositions.
The processing scripts made zero Codex calls; visual inspection occurred in the
agent session. The 151 rules, driver and audit regressions passed.

Early pilot iterations contained residual identity clues and a figure-ellipsis
false positive; those rejected artifacts remain as history. The v6 sample result
does not establish perfect anonymity for arbitrary papers. The candidate driver,
independent audit and macOS OCR helper are separate components, not a fully
automatic production publishing pipeline. Production `clean_data` was not changed.
Execution evidence is retained on the execution host under
`res/maintenance-anonymize-rules-v3-20260916/` and
`res/iclr2026_rules_pilot_20260915T095243Z/`.

## Utility package layout

| Package | Responsibility |
| --- | --- |
| `utility/data/` | Catalog preparation, PDF helpers, downloads and clean pair construction |
| `utility/batch/` | Batch preparation, execution, completed-turn recovery, verification and result reuse |
| `utility/codex/` | Shared Codex isolation and token accounting |
| `utility/anonymize/` | Codex/rules PDF anonymization, independent audit and native OCR helper |

`utility/workflow.py` remains the public workflow entry point;
`utility/source_provenance.py` remains the global content-manifest API.
Use the grouped module names shown above, or their corresponding direct script
paths. There are no active flat forwarding modules. Current source locations map
to the existing frozen runtime layout: `source_snapshot` and `controller_versions`
paths in completed or running experiments are not moved. Missing helpers in the
selected source tree fail without loading another checkout.

The directory migration passed 785 related offline tests plus syntax, import,
CLI and source-preservation checks. This includes utility, MBZ batch, comparison,
review and benchmark contracts; it excludes the data-packaging suite and is not
a claim that every repository test passed. Earlier time-limited baseline runs
are recorded as incomplete. The migration added no dependencies and did not
launch corpus processing or real model calls.

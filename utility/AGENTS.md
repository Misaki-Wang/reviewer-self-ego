# MBZ implementation rules

MBZ is the primary editing/testing environment; do not modify local code copies.
The user-selected root layout is check_diff, review_experiment, tests, utility,
data, res, html, .gitignore and README.md. Keep current source data unversioned
under data/index and original PDFs under data/pdf. Use utility/project.json as
the single current I/O/runtime configuration. Maintain tests under tests.

Keep Codex invocation semantics, scientific prompts, annotation schema and token
accounting stable unless the user requests their change. Preserve all original
paper/version metadata; selected version gaps remain strictly above 2592000
seconds and intermediate versions may be skipped. Never infer scientific labels
from time/page counts. Missing usage is unknown.

Source manifests pin actual source/data bytes with a content revision; do not
mislabel changed exported code as an unchanged Git commit. Refresh the source
manifest explicitly after reviewed edits. Each run freezes its own inputs,
configuration and controllers. Later project changes must not alter a prior run.

Respect the project pause and per-run pause. Directory cleanup, preparation and
validation do not authorize real model calls. Use only existing dependencies,
private project scratch and CPU-only Slurm allocations for sustained tests.
Historical results/configuration remain immutable under res/archive; archived
absolute paths describe the original environment and may need restoration for
reproduction. Do not execute archived launchers as the current workflow.

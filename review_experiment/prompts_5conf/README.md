# Five-conference review prompt pack

This pack implements the main D/E conditions in paper-trace-story-plan-20260917.html.
It contains exactly five system profiles and three user templates:
exp1_quality, exp3_positive, and exp3_negative. Neutral is the shared experiment 3
baseline. Removed experiments 2 and 4 have no conditions or shared-baseline entries.

The three user templates and the ICLR2026, ICML2026, and ACL2026 system prompts are
byte-identical to the existing pack. Its default 29-condition pack is not modified.
The "Please use sparingly" wording in retained baselines remains unchanged; the
HTML's rubric-sensitivity comparison is a separate proposed experiment.

CVPR2026 uses the user-approved inherited CVPR2025 five-point scale. ECCV2026
uses the user-approved inherited ICCV2025 six-point scale, with 2026 contribution-
type guidance. Both are marked experimental_encoding: the full2026 official
numeric scales could not be publicly verified. See system/SOURCES.md and manifest.json.

Use the existing runner's --prompts option with this directory. For experiment 1,
the selected condition is neutral; experiment 3 includes neutral, positive, and
negative. The three fields returned remain oa, strengths, and weaknesses. Validate
OA against the selected venue's own values; these are not a shared interval scale.

The HTML plans medium effort for the three GPT models. Effort is a runtime setting
and must be frozen in the future run configuration; this prompt pack does not set it.
Use a fresh output directory for a new run and retain the manifest hash. Creating
and validating this pack does not start model calls or alter running experiments.

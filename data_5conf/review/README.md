# Historical review imports

Except for the derived DeepSeek view below, imported JSONs preserve exact historical bytes and model/effort settings.
They are not new medium-effort reviews. The copy manifest records original paths
and hashes. Use a new run-specific directory for subsequent experiments to avoid
overwriting these historical outputs.

The `deepseek-v4.1-flash` directories now contain a derived merge of the historical
DeepSeek records and the OpenRouter high snapshot. At the same paper/version/condition,
the snapshot judgment takes precedence. Exact premerge files and the original snapshot
are archived under `experiments/runs/deepseek_v41_openrouter_high_merge_20260924T145344Z_1ad6a7fe/`.
See `DEEPSEEK_V41_MERGE_20260924.md` for the merged counts and precedence rule.

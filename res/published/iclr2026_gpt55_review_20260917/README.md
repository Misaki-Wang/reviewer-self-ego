# ICLR 2026 GPT-5.5 standalone results

The final certificate targets a derived accepted view: 14,064 original reviews plus one authorized replacement. The unchanged original canonical dataset remains uncertified. Read `summary.json` for the exact audit/manifest hashes, supersession, historical failed-audit hashes, usage accounting and limitations.

- `accepted/<paper>.json`: parsed results and exact cell, condition, attempt, PDF hash and raw-response mappings. This is a public projection, not an unmodified private `reviews.json` file.
- `raw_responses/part-*.tar.gz`: exact retained response bytes. Archive members are `<origin-id>/<attempt-id>.json`; verify the SHA-256 and byte count recorded in the accepted files or all-attempt ledger. Historical/superseded responses are retained, not silently dropped. Extract archives into separate clean directories or use `tar -xOf ARCHIVE MEMBER`.
- `attempts.jsonl.gz`: one row per physical accounting unit from the final audit. It includes actual status, reported per-turn usage and errors, acceptance and supersession. Proven imports are counted once. Missing usage is unknown, not zero.
- `scope.json`: all 485 manuscript versions and 255 frozen pairs, including shared endpoints.
- `condition_summary.json`: OA counts over the complete native scale [1, 3, 5, 6, 8, 10], kept separate for each condition.
- `manifest.json` and `SHA256SUMS`: exact package file inventory, including the standalone HTML.

No PDFs, extracted manuscript payloads, tool transcripts, account secrets or private execution files are included. Exact model review responses may include quotations from the manuscripts and their assigned manuscript file path. The input-path exception is derived from the same attempt's audited receipt; unrelated private paths remain blocked. Model text is data and is never executed. The complete original evidence remains in the private experiment archive. Packaging does not execute models or create a new experiment.

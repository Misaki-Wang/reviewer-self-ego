# Frozen provenance attachments for the combined ICLR2026 review report

This directory accompanies the already accepted three-model review results. It is a metadata and prompt export, not a new scientific audit. No review, model response, attempt ledger, PDF, live runtime, source artifact, or published standalone result was changed or reprocessed.

## Start here

- [Acceptance and lineage](acceptance-and-lineage.json): each model's distinct certificate and historical limitations.
- [Frozen settings](frozen-model-settings.json): exact model/effort/seed, scope and prompt bindings. Seeds are decimal strings so the large GPT-6 seed cannot be rounded by JavaScript.
- [Scoring and conditions](scoring-and-prompt-bindings.json): all 29 canonical conditions, their shared baselines, exact runtime substitutions, and the complete OA vocabulary **1, 3, 5, 6, 8, 10**.
- [Exact prompt manifest](prompts/manifest.json), [exact ICLR system/scoring text](prompts/system/iclr2026.txt), and the 29 exact user templates in `prompts/`.
- `models/gpt55/`, `models/sol/`, `models/han/`: actual frozen manifests/configuration and sanitized owner certificates. `scope/` retains the original shared pair authority.
- [Attachment manifest](manifest.json) records public hashes, original byte hashes, transformations and private-value omission counts. `SHA256SUMS` covers every file except itself.

## What is preserved

The condition templates, selected ICLR system prompt and original prompt manifest are byte-exact. Their hashes match the existing GPT-5.5 initial source manifest, Sol final source manifest and Han final contract. The original prompt manifest also names unused venue systems; only the ICLR system is included because these runs selected ICLR2026. No vocabulary is truncated or renormalized. Experiment baselines are reused within a version/replicate; they are not additional calls.

The source/configuration JSON documents retain their original scientific fields, hash references and history, except private values replaced by explicit hash-marked omission tokens. `models/han/source-pins-from-contract.json` is labelled as a projection from the real contract's source-pin list, not an invented original manifest. Final source manifests do not assert that every historical attempt used the final runtime generation.

GPT-5.5 certifies a **derived accepted view: 14,064 original cells plus one replacement**. Its original canonical dataset remains uncertified, and original failed audits and the superseded response remain historical evidence. The earlier accepted-view manifest's pending-audit status is retained; the final published certificate supplies acceptance.

Sol's original structural audit retains **exit 2 / attention_required=true**. The separately pinned addendum resolves all 175 descriptive read flags, including 154 environment suspects, using 40 Ghostscript, 108 PyMuPDF and 27 helper-reading chains. Its 23 opaque-helper source corroborations are retained. This does not establish full-page/figure coverage or comprehension.

Han's accepted final audit retains **138 corrections (134 read + 4 cleanup)** and **119 invalidated historical attempts excluded from final results**. Original durable failed receipts remain failed. Accepted-cell completion does not relabel raw receipt history.

## Privacy and scope

This public directory contains no private email, account-home path, credential payload or execution/runtime path. Omission markers carry a hash of the original value; each original document's complete byte hash is separately retained. Sanitized documents are derivatives and must not be treated as byte-identical originals. Private source locations and the detailed preparation receipt stay outside this public directory. Original absolute paths occurring as JSON keys are replaced with distinct hash-marked keys to avoid collisions.

Original PDFs, model events/stderr, executable/runtime bundles and complete private audit inventories are not duplicated here. The combined package's exact raw-response shards and attempt ledgers remain separate assets. GPT-5.5's original large private audit is represented by its pinned, already published certificate summary and original audit hash. One referenced private single-cell recovery-evidence file is not locally mirrored; the accepted-view manifest preserves its hash and the final accepted-view certificate supplies its accepted status. This export does not fabricate that missing file or claim a new independent validation of it.

Historical execution-authorization fields may predate the later combined-publication request; they are retained as historical data. Full per-call client ModelInfo/base-instruction identity remains unknown. Reported usage remains partial where attempts or internal provider retries did not report consumption. Missing usage is not zero.

## Integration

Copy this directory as `provenance/` inside the combined publication package. The combined report links to `provenance/README.md`. Include these files in the final package checksum/inventory generation. Do not copy the private preparation record or owner `.private.json` conversion files. The compiler and these attachments do not themselves authorize Git publication or change the already accepted scientific results.

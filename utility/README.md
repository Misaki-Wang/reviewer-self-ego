# Prepare original PDF pairs

## Current v1.3 preparation

`prepare_v13_pilot.py` reads either the combined v1.3 JSON or `v1.3/index.json` with its checksum-verified source shards. It preserves frozen metadata and deduplicates identical arXiv records across sources. Use Python 3.10+ and `utility/requirements.txt`.

A paper unit retains its complete original history in metadata. The adapter selects the **maximum number of chronological original versions** whose consecutive selected gaps are each **strictly greater than 2,592,000 seconds (30 days)**, then prepares only adjacent selected comparisons. Intermediate versions may be skipped. The frozen three-version eligibility witness is not used as a limit on the selected sequence. Missing or inconsistent timestamps leave sequence selection unresolved.

The current execution entry point accepts one unit. Freeze that unit before checking PDF availability:

```sh
python3 utility/prepare_v13_pilot.py \
  --source v1.3/index.json --pdf-root /path/to/arxiv_pdf \
  --output prepared --units 1 --seed 20260911 --plan-only
```

The existing original-PDF store must use `<arxiv_id>/<arxiv_id>vN.pdf`. The adapter records every exact PDF request in `prepared/selection_manifest.json`; it does not download files. Make the requested PDFs available in that store, then materialize the fixed plan:

```sh
python3 utility/prepare_v13_pilot.py \
  --materialize-plan prepared/selection_manifest.json
```

Materialization verifies PDF structure, physical page totals, SHA-256 and available version-specific identity evidence. It checks frozen hashes when present. All pages inside each exact PDF, including references and appendices, count toward its total. Missing or invalid PDFs produce explicit failures and a nonzero exit status; they never cause automatic replacement sampling. Use a new output directory for a new selection.

```text
prepared/
  selection_manifest.json
  materialization_manifest.json
  pairs.json
  <arxiv_id>/
    metadata.json
    v1_v3/
      v1.pdf
      v3.pdf
      pair.json
    ... further adjacent selected pairs
```

PDFs remain read-only originals. Pair folders use hard links when possible and copies otherwise. Complete history and source metadata stay in the inventory; the comparison prompt receives only its two selected versions. This step neither assesses substantive change nor filters by manuscript length or license.

Continue with the [one-unit comparison pipeline](../check_diff/README.md). `--dry-run` prints a proposed sample without writing it. The adapter can prepare larger authorized metadata samples with `--units N`, but that does not expand the current one-unit execution scope.

## Legacy v1.1 preparation

`prepare_input_data.py` preserves the original v1.1 workflow. It defaults to a seeded 15-paper sample (8 ICLR, 5 ICML, 2 ACL), downloads every version recorded in that frozen input, and constructs all adjacent **original** pairs without a time-gap filter. It requires `curl` in addition to the Python requirements.

```sh
git show v1.1:v1.1.json > v1.1.json
python3 utility/prepare_input_data.py --dry-run
python3 utility/prepare_input_data.py --output legacy_input
```

Do not substitute v1.2 or v1.3 JSON for that legacy schema. Repeated runs reuse verified downloads and retry unavailable files. `--arxiv-ids` selects specified papers; all their frozen original versions are still prepared.

```sh
python3 -m unittest discover -s utility -p 'test_*.py'
```

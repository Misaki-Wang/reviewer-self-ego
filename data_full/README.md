# data_full: base catalog plus additions

This separate two-version-rule catalog covers all ten sources from the frozen
original raw inventory, plus the separately acquired ICLR2025 source. All 3,724
original three-version records remain unchanged. The three-version catalog now
also includes 610 qualifying ICLR2025 records. All 4,334 records are reused from
hash-pinned `data/index` shards. Every resolved full record and its order remain
unchanged; ICLR2025 storage is split into the shared base and 1,290 full-only additions.

| Source | Retained papers | Selected pairs | All original versions |
| --- | ---: | ---: | ---: |
| acl2025 | 680 | 890 | 1,818 |
| acl2026 | 772 | 1,010 | 2,091 |
| cvpr2025 | 899 | 1,100 | 2,354 |
| emnlp2025 | 688 | 896 | 1,830 |
| iccv2025 | 890 | 1,114 | 2,301 |
| iclr2025 | 1,900 | 2,700 | 5,378 |
| iclr2026 | 2,372 | 3,265 | 6,559 |
| icml2025 | 1,411 | 1,994 | 3,947 |
| icml2026 | 2,239 | 2,896 | 5,930 |
| neurips2025 | 2,336 | 3,169 | 6,440 |
| tmlr_2025plus | 1,172 | 1,851 | 3,387 |
| **Total (unique)** | **15,331** | **20,835** | **41,945** |

Source memberships exceed unique papers: 28 papers belong to both ICML2026 and
TMLR2025+. Fourteen are reused from the original bases; fourteen newly eligible
papers are stored once in `shared.additions.json`. Source-filtered loading returns
every relevant membership without duplicate papers or PDF files.

A paper qualifies when any two original versions are strictly more than 2,592,000
seconds apart. The earliest-valid greedy selector keeps the maximum sequence and
every adjacent selected transition forms a pair. Exactly 30 days fails. All
original version metadata, including skipped versions, is retained outside the
isolated pair assessments. The resolved view contains 36,166 selected versions
and 5,779 skipped versions, for 41,945 original versions in the PDF inventory.

## Frozen source coverage and unresolved identities

The complete frozen raw inventory contains 25,613 confirmed arXiv identities for
the original ten sources. Applying the two-version rule retains 13,431 unique
papers. Another 8,481 source entries have unresolved arXiv identities and receive
no temporal verdict. Their original records and locators are preserved in the
[raw unresolved ledger](index/provenance/raw_sources_unresolved_20260915.jsonl).
Together with ICLR2025's 777 unresolved identities, 9,258 source identities remain
unresolved; unresolved does not mean temporally ineligible.

The expansion uses the retained raw inventory built on 2026-09-11. It does not
silently refresh source identities or version histories. EMNLP2026 remains absent
until a verified current public main-conference roster is available; it is not
represented by a zero-sized conference shard.

- [All-source filter counts and provenance](index/provenance/all_sources_pair30_20260915.json)
- [Pinned catalog index](index/index.json)

## ICLR2025 acquisition and limits

The official published roster contains 3,703 main-conference papers. Exact title
and author matching against a complete frozen reference snapshot confirms 2,926
arXiv identities with 6,646 original-version timestamps. Of these papers, 1,900 pass
the temporal rule and 1,026 fail it. Another 777 identities remain unresolved
(771 unmatched, 4 unverified hints and 2 ambiguous) and receive no temporal verdict.
Their complete records are retained in the unresolved ledger.

The source is a weekly mirror of Cornell University's arXiv metadata dataset:
`librarian-bots/arxiv-metadata-snapshot`, revision
`aec69ea34085e34808157a0a9c8adeaef91caceb`. Its maximum update date, verified from
all file statistics, is **2026-09-11**. These are frozen mirror observations, not
live arXiv API responses. Every one of its 3,164,528 ID/title reference rows was
searched. Only necessary columns and hit groups were read, transferring about
417.12 MB; no paper PDFs or model calls were used. Whole-file source hashes are
advertised identities; byte-range reads do not independently verify whole files.

- [ICLR2025 full-only additions](index/iclr2025.full-only.json)
- [Shared ICLR2025 three-version base](../data/index/iclr2025.json)
- [Three-version membership proof](../data/index/provenance/iclr2025_min3_20260915.json)
- [Filter counts and provenance](index/provenance/iclr2025_filter_20260915.json)
- [Unresolved identity ledger](index/provenance/iclr2025_unresolved.jsonl)
- [Pinned snapshot manifest](index/provenance/iclr2025_snapshot_manifest.json)
- [Earlier ICLR2026 provenance](index/provenance/iclr2026_pair30_20260915.json)

Missing PDF/page/license evidence remains explicitly unknown. Snapshot title,
authors and license values describe the latest record; they are not assigned to
older versions without evidence. The published roster, acceptance-statistics and
virtual-poster count discrepancy is recorded in the acquisition audit.

## Layout and reuse

```text
data/index/<source>.json                 original 3,724 records, unchanged
data/index/iclr2025.json                 610 shared three-version records
data/pdf/<arxiv_id>/<arxiv_id>vN.pdf       existing originals, read-only

data_full/index/index.json               pinned bases, additions and full order
data_full/index/iclr2026.additions.json   1,666 additions
data_full/index/iclr2025.full-only.json   1,290 full-only additions
data_full/index/<source>.additions.json   new single-source additions
data_full/index/shared.additions.json     14 cross-source additions, stored once
data_full/pdf/<arxiv_id>/<arxiv_id>vN.pdf  new originals when downloaded
```

The index pins raw base/addition bytes and the reconstructed combined JSON stream.
Its bases and additions are disjoint. Identical records shared between original
base shards collapse into one paper; conflicting duplicates fail closed. Additions
are globally disjoint, and the shared addition shard is read by source membership. Existing records and order remain stable.
Each experiment freezes complete selected metadata and source dependencies.

The old `index/iclr2025.additions.json` file is an unreferenced compatibility
copy of all 1,900 ICLR2025 records. It remains unchanged for readers holding
an older index, with an identical archive under
`res/maintenance-both-catalogs-20260915/before/`. The current index does not
load this file; it contributes no duplicate canonical records. This storage
promotion leaves the full canonical JSON SHA-256 unchanged:
`2a96989978d1502086b05a33cd58e47a161a8001a148432e9532eb8d3df4d4be`.

From the MBZ project root, using the existing runtime:

```sh
source utility/env.sh
python3.12 -B -m utility.prepare_source_data \
  --source data_full/index/index.json --conf iclr2025 --pdf-root data_full/pdf \
  --fallback-pdf-root data/pdf --all --dry-run
```

Formal preparation uses `--catalog data_full --source iclr2025`; default paths still
use the original `data` catalog. Actual preparation requires the needed PDFs.
Clean-PDF pair construction uses `clean_data` and never substitutes original PDFs
when a cleaned input is absent. Downloading, anonymization and model execution are
separate tasks. The catalog build is metadata-only; current download observations
and failures are recorded separately under the PDF download manifest.

## Active original-PDF acquisition

The all-source PDF job is Slurm **11677** on MBZ. It covers all **41,945**
original versions, including skipped versions, and writes additions under
`data_full/pdf` while directly reusing validated `data/pdf` originals.
The frozen resume phase is
`res/maintenance-full-catalog-20260915/downloads_resume/`.
Live file progress is `data_full/pdf/download_manifest.json`; download completion
is recorded separately from catalog completion. Existing source records, page
metadata and historical download evidence remain unchanged.

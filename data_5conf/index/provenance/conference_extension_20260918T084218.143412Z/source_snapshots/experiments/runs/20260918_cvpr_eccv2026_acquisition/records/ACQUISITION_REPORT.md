# CVPR 2026 and ECCV 2026 acquisition

Status: catalogs and available original PDFs installed; final audit passed with six explicitly documented official HTTP 404 versions. No invalid files or audit errors.

Remote project: `/fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/`

Run: `experiments/runs/20260918_cvpr_eccv2026_acquisition/`

## Catalog additions

| Source | data_full | data | data_extra | All original versions |
| --- | ---: | ---: | ---: | ---: |
| CVPR 2026 | 943 | 169 | 774 | 2,422 |
| ECCV 2026 | 633 | 119 | 514 | 1,619 |
| Total | 1,576 | 288 | 1,288 | 4,041 |

`data_full` requires at least two selected original versions; `data` requires at least three. Every adjacent selected interval must be strictly greater than 2,592,000 seconds. Intermediate versions may be skipped. `data_extra` is the global arXiv-ID set difference `data_full - data`. All original versions are retained, including skipped versions.

The resulting complete catalog sizes are 16,907 / 4,622 / 12,285 papers for `data_full` / `data` / `data_extra`. Existing paper records and historical source shards remain unchanged. No scientific model calls were made.

## Acquisition evidence and limits

- The initial CVPR roster has 4,042 entries from the [CVF proceedings](https://openaccess.thecvf.com/CVPR2026?day=all). The [official main program](https://media.eventhosts.cc/Conferences/CVPR2026/CVPR_main_conf_2026_15.pdf) supplied ten additional eligible arXiv identities after deduplication: nine program-only candidates and one previously unresolved CVF title variant. Findings and workshops are excluded.
- Official CVPR counts disagree: 4,089 in announcements, 4,072 in program prose, 4,071 enumerated main-program slots, and 4,042 CVF entries. No withdrawal explanation is inferred. Calibri and OVSegDT retain explicit historical program support and absence from the frozen virtual roster.
- The [ECCV accepted list](https://eccv.ecva.net/Conferences/2026/AcceptedPapers) has 2,834 unique official poster IDs; 17 duplicated display rows were collapsed. The list remains preliminary pending publisher checks.
- Histories use the frozen arXiv metadata snapshot through **2026-09-11**, revision `aec69ea34085e34808157a0a9c8adeaef91caceb`. All 3,164,528 reference rows were searched. These are not live 2026-09-18 arXiv histories.
- Identity matching requires exact normalized titles and sufficient author agreement. Unresolved identities remain in separate source-frame ledgers and are not temporal negatives. Overlapping CVF/program frame counts are not pooled.

## PDF acquisition

Both download batches finished: 4,035 successful original PDFs and six HTTP 404 responses. The six exact versions also returned HTTP 404 from the canonical arXiv PDF endpoint:

- `2505.04594v5`
- `2505.11192v2`
- `2509.15695v4`
- `2511.21251v2`
- `2602.20630v2`
- `2604.02546v2`

Their paper and version metadata remain in all applicable catalogs. Available sibling versions are published independently. No newer, older, publisher, or anonymized PDF is substituted.

All 4,035 available original PDFs were published and independently audited. PDF paths use `<catalog>/pdf/<arxiv_id>/<arxiv_id>vN.pdf`. Verified files share storage through hard links; treat their bytes as read-only.

| Catalog | Expected new versions | Verified PDF files | Official 404 |
| --- | ---: | ---: | ---: |
| data_full | 4,041 | 4,035 | 6 |
| data | 1,085 | 1,082 | 3 |
| data_extra | 2,956 | 2,953 | 3 |

CVPR has 2,417 verified PDFs and five official 404 versions; ECCV has 1,618 verified PDFs and one official 404 version. Four verified PDFs have no first-page arXiv version stamp. Their null stamps are preserved; exact-version URL, acquisition receipt and SHA256 evidence remain available. Physical page counts include references and appendices.

The final read-only audit covered every one of the 4,041 original-version records, checked the two intended destinations, SHA256, size, page counts, available version stamps and provenance, and reconfirmed the three-catalog relation and historical preservation. Slurm finalization job `12551` completed with exit code `0:0`.

The full per-version inventory remains on MBZ at `experiments/runs/20260918_cvpr_eccv2026_acquisition/records/final_audit_20260918T082709483229Z.versions.jsonl`.

## Catalog verification

- [Independent final candidate audit](independent_final_candidate_audit.json)
- [Installation receipt](installation-candidate_v2.json)
- [Authorized scope and directory relation](request_scope.json)
- [Final installed-file audit](final_audit_20260918T082709483229Z.summary.json), SHA256 `10212f0ec02013d8f8268f0fe80430cfab9b5e64c8daceadbb8f5bc68d41dd00`

The remote run retains complete source snapshots, native metadata evidence, unresolved ledgers, HTTP receipts, version hashes/page counts, old-file backups, and implementation snapshots. The local directory mirrors only compact evidence.

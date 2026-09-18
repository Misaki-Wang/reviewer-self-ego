# data_full: at least two selected versions

This catalog contains **13 sources, 16,907 unique papers and 45,986 original versions**.

data requires at least three selected original versions; data_full requires at least two. data_extra is the global data_full minus data paper-set difference, using arxiv_id as identity. The two disjoint parts reproduce the exact full-catalog records.

Each consecutive selected interval is strictly greater than 2,592,000 seconds (30 days); equality fails. The earliest-valid greedy selector chooses a maximum sequence and may skip intermediate versions. Every original version remains in metadata and the requested PDF inventory, including skipped versions. Temporal membership does not establish scientific change.

| Source | Papers | All original versions |
| --- | ---: | ---: |
| acl2025 | 680 | 1,818 |
| acl2026 | 772 | 2,091 |
| cvpr2025 | 899 | 2,354 |
| cvpr2026 | 943 | 2,422 |
| eccv2026 | 633 | 1,619 |
| emnlp2025 | 688 | 1,830 |
| iccv2025 | 890 | 2,301 |
| iclr2025 | 1,900 | 5,378 |
| iclr2026 | 2,372 | 6,559 |
| icml2025 | 1,411 | 3,947 |
| icml2026 | 2,239 | 5,930 |
| neurips2025 | 2,336 | 6,440 |
| tmlr_2025plus | 1,172 | 3,387 |
| **Unique total** | **16,907** | **45,986** |

Source membership totals may exceed globally deduplicated totals. Shared records retain all memberships.

## Storage and preservation

Historical paper records, original-version histories, source shard bytes and existing record order are unchanged. New members follow in frozen input order. Historical provenance and statistics retain their original scope; they are not fresh observations of the expanded catalog.

The full index references hash-pinned, read-only data/index base shards and globally disjoint additions. Resolve it through utility.data.prepare_source_data.load_source. Shared additions are stored once and filtered by source membership.

The index pins raw shard bytes, counts and the reconstructed source_data JSON. Combined JSON remains candidate verification evidence; publication does not create duplicate combined corpus files in live catalogs.

## CVPR 2026 and ECCV 2026 acquisition

The frozen metadata cutoff is **2026-09-11**. These are frozen mirror observations, not a claim of current live arXiv histories. Unresolved identities and incomplete histories are unknown, rather than temporal failures.

The following counts describe the initial CVF/official-list matching frame, before the separately documented program supplement.

| Source | Initial roster entries | Initially at least 2 | Initially at least 3 | Initial unresolved |
| --- | ---: | ---: | ---: | ---: |
| cvpr2026 | 4,042 | 933 | 167 | 1,097 |
| eccv2026 | 2,834 | 633 | 119 | 949 |

The separate historical official MAIN program contains 4,071 enumerated source records. Findings are excluded. Matching the complete program against the same frozen snapshot adds 10 distinct eligible identities and 24 original versions beyond the unchanged initial retained cohort. Of these additions, 2 have at least three selected versions. Current CVPR totals are 943 full-catalog and 169 three-selected-version papers.

The independent program frame has 1,122 unresolved identities. This overlaps the initial source frame, so its unresolved count is not added to the initial unresolved count.

Calibri and OVSegDT are retained as historical MAIN-program members; they were not located in the frozen CVF/virtual inventories. Their absence does not establish withdrawal or current publication status. Official announced acceptance counts, enumerated program slots and frozen CVF entries differ; their causes are not inferred. Completeness relative to the 4,089 announced acceptances is not claimed.

[Program supplement summary and provenance](../experiments/runs/20260918_cvpr_eccv2026_acquisition/program_supplement/summary.json) and [final official coverage audit](../experiments/runs/20260918_cvpr_eccv2026_acquisition/records/cvpr2026_coverage_audit_final.json) retain source scope, title/author conflicts, raw program locations and the exact frozen evidence.

Frozen acquisition limitations:

- Histories are frozen at snapshot cutoff 2026-09-11, not live 2026-09-18.
- Only exact normalized title and sufficient author agreement confirms identity.
- Unresolved identities and incomplete histories are unknown, not temporal negatives.
- ECCV list is preliminary pending publisher checks; CVPR roster coverage is the supplied official-source inventory.
- Whole-file advertised SHA256 is recorded; individual byte ranges and native projected rows are verified.
- No original PDF observations, page counts, content judgments or model calls in this stage.
- Official MAIN program membership supports venue scope; Findings rows are excluded.
- Program title/author variants are a separate official source frame; original CVF records and judgments are unchanged.
- All histories are from the pinned snapshot through 2026-09-11, not a current live arXiv history.
- Unmatched/ambiguous identities remain unresolved and are not temporal negatives.
- Program slots with repeated titles but different author rosters remain separate source identities; recorded source conflicts are not silently reconciled.
- Supplement contains only identities absent from the original CVF retained cohort; same identities are external corroboration only.

[Publication manifest and frozen evidence pins](../experiments/runs/20260918_cvpr_eccv2026_acquisition/candidate_v2/publication_manifest.json) identify the matching summary, unresolved ledgers, roster inputs and source receipts at their exact retained run paths. Prior README bytes are archived under this run's records/before_docs directory.

## PDF availability and observations

Membership and version paths do not imply PDF existence, download or verification. New metadata rows are unobserved until separate verification supplies hashes, byte sizes and physical page counts. Unknown values remain null with explicit reasons. Physical page counts include references and appendices.

New-source PDF acquisition and per-version receipts are recorded separately under experiments/runs/20260918_cvpr_eccv2026_acquisition/. Consult those receipts for current new-source availability; earlier observations retain their original scope. This catalog publisher does not download, copy, modify or anonymize PDFs.

No deleted historical completion marker or PDF inventory is recreated. Catalog validation does not assert PDF acquisition completion. Preparation must recheck the required exact-version PDFs. Publication performs no content judgments, annotations or model calls.

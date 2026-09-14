# Independent pilot audit: PASS

Run: /fsx/homes/Yaochen.Wang@mbzuai.ac.ae/AI-reviewer/res/run-20260914-01
Slurm job: 11160; 2 CPUs, 8 GiB, 15 minutes, no GPU.

Coverage: 4368/4368 reviews; 100 distinct papers; 364 versions; 12 conditions; 100 raw files; 8806 receipts.
Protected completed reviews: 71. Protected receipt files: 212.

All arithmetic derived independently from canonical attempt usage.records, deduplicated by (full identity, controller_attempt_id), crosschecked against finished receipts. Input includes cached input; output includes reasoning output. Subsets are not added again. 35 unreported failed attempts remain unknown, not zero; reported totals are incomplete consumption.

Elapsed seconds are measured runner call wall durations, including PDF/tool/orchestration work. Summed parallel attempt durations are not study wall time or pure API time. Timestamp span uses earliest attempt start to latest finish.

| Group | Attempts | Missing usage | Reported total tokens | Sum seconds | Mean seconds | Median seconds | Min seconds | Max seconds |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 4403 | 35 | 2168268823 | 1464777.966 | 332.6772577787872 | 325.009 | 61.773 | 820.964 |
| successful | 4368 | 0 | 2168268823 | 1462428.977 | 334.8051687271062 | 325.7345 | 110.478 | 820.964 |
| failed | 35 | 35 | None | 2348.989 | 67.11397142857143 | 66.524 | 61.773 | 75.576 |

Per-job historical freeze binding is required; initial attempts legitimately precede the later promotion freeze.

| Check | Passed | Failed |
|---|---:|---:|
| source_revision | 1 | 0 |
| scope_revision | 1 | 0 |
| config | 1 | 0 |
| config_sources | 1 | 0 |
| file_stable_during_read | 850 | 0 |
| frozen_source_hash | 31 | 0 |
| initial_archive_hash | 9 | 0 |
| freeze_revision | 2 | 0 |
| freeze_bound_hash_initial | 13 | 0 |
| freeze_bound_hash_transport_recovery | 15 | 0 |
| promotion_chain | 1 | 0 |
| prompt_hash | 22 | 0 |
| conditions_12 | 1 | 0 |
| inventory_source_shape | 10 | 0 |
| source_profile_binding | 10 | 0 |
| source_oa_scale_kind | 10 | 0 |
| original_source_hash | 10 | 0 |
| prepared_metadata_hash | 10 | 0 |
| first_10_original_records | 10 | 0 |
| source_paper_ids | 10 | 0 |
| source_counts | 10 | 0 |
| input_pdf_hash | 528 | 0 |
| tmlr_encoding_1_to_4 | 1 | 0 |
| no_extra_or_missing_input_pdfs | 1 | 0 |
| inventory_totals | 1 | 0 |
| canonical_document_identity | 100 | 0 |
| canonical_unique_cell | 4368 | 0 |
| canonical_version_sha | 4368 | 0 |
| review_config_binding | 4368 | 0 |
| review_completed | 4368 | 0 |
| source_native_result_schema | 4368 | 0 |
| canonical_attempt_id_present | 4403 | 0 |
| rendered_frozen_user_prompt | 4403 | 0 |
| review_fingerprint | 4403 | 0 |
| raw_parsed_equality | 4368 | 0 |
| one_success_per_cell | 4368 | 0 |
| protected_completed_sha | 71 | 0 |
| historical_ws403_failure | 35 | 0 |
| exact_review_coverage | 1 | 0 |
| no_extra_raw_files | 1 | 0 |
| protected_completed_count | 1 | 0 |
| protected_receipt_sha | 212 | 0 |
| protected_receipt_count | 1 | 0 |
| receipt_identity_in_scope | 8806 | 0 |
| receipt_fingerprint_key | 8806 | 0 |
| receipt_job_known | 8806 | 0 |
| receipt_after_own_freeze | 8806 | 0 |
| start_finish_receipt_binding | 4403 | 0 |
| receipt_time_order | 4403 | 0 |
| receipt_review_binding | 4403 | 0 |
| attempt_receipt_id_binding | 4403 | 0 |
| receipt_canonical_attempt_equality | 4403 | 0 |
| attempt_time_inside_receipt | 4403 | 0 |
| attempt_after_own_freeze | 4403 | 0 |
| receipt_pairs_complete | 1 | 0 |
| canonical_receipt_attempt_set_equal | 1 | 0 |
| receipt_file_count | 1 | 0 |
| attempt_status_counts | 1 | 0 |
| usage_record_errors_empty | 4403 | 0 |
| attempt_elapsed_valid | 4403 | 0 |
| elapsed_vs_recorded_timestamps | 4403 | 0 |
| usage_event_line_unique | 4368 | 0 |
| usage_field_shape | 4368 | 0 |
| usage_subsets | 4368 | 0 |
| no_failed_attempt_after_resume64 | 4297 | 0 |
| cell_usage_rollup_matches_independent | 4368 | 0 |
| per_source_exact_condition_coverage | 10 | 0 |
| progress_counts_crosscheck | 1 | 0 |
| progress_usage_crosscheck | 1 | 0 |
| missing_usage_remains_unknown | 1 | 0 |
| audited_file_unchanged_at_finish | 9526 | 0 |

Read-only audit of run artifacts. No source/output/builder edits, Git, model, credential, external network or download calls. Publication/export/browser validation is owned by the parent and is not certified here.

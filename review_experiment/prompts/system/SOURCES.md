# System prompt sources and score semantics

These files define new experimental assessments using source-specific reviewer criteria. They are not copies of full official review forms, and do not relabel historical human scores.

The original ICLR2026 template is retained unchanged. The other templates adapt substantive criteria and native recommendation labels; their expanded per-score explanations are experimental wording. Administrative procedures and rebuttal discussions are not simulated.

| Source | OA values | Basis |
| --- | --- | --- |
| acl2025 | 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 | [source 1](https://2025.aclweb.org/calls/main_conference_papers/); [source 2](https://aclrollingreview.org/reviewform); [source 3](https://aclrollingreview.org/reviewerguidelines); [source 4](https://aclrollingreview.org/acguidelines) |
| acl2026 | 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 | [source 1](https://2026.aclweb.org/calls/main_conference_papers/); [source 2](https://aclrollingreview.org/reviewform); [source 3](https://aclrollingreview.org/reviewerguidelines); [source 4](https://aclrollingreview.org/acguidelines) |
| cvpr2025 | 1, 2, 3, 4, 5 | [source 1](https://cvpr.thecvf.com/Conferences/2025/ReviewerGuidelines); [source 2](https://api2.openreview.net/invitations?id=thecvf.com%2FCVPR%2F2025%2FConference%2FSubmission1%2F-%2FOfficial_Review&expired=true) |
| emnlp2025 | 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 | [source 1](https://2025.emnlp.org/calls/main_conference_papers/); [source 2](https://aclrollingreview.org/reviewform); [source 3](https://aclrollingreview.org/reviewerguidelines); [source 4](https://aclrollingreview.org/acguidelines) |
| iccv2025 | 1, 2, 3, 4, 5, 6 | [source 1](https://iccv.thecvf.com/Conferences/2025/ReviewerGuidelines); [source 2](https://api2.openreview.net/invitations?id=thecvf.com%2FICCV%2F2025%2FConference%2FSubmission9993%2F-%2FOfficial_Review&expired=true); [source 3](https://api2.openreview.net/invitations?id=thecvf.com%2FICCV%2F2025%2FConference%2FSubmission9998%2F-%2FOfficial_Review&expired=true) |
| iclr2026 | 1, 3, 5, 6, 8, 10 | [source 1](https://iclr.cc/Conferences/2026/ReviewerGuide) |
| icml2025 | 1, 2, 3, 4, 5 | [source 1](https://icml.cc/Conferences/2025/ReviewerInstructions) |
| icml2026 | 1, 2, 3, 4, 5, 6 | [source 1](https://icml.cc/Conferences/2026/ReviewerInstructions); [source 2](https://icml.cc/Conferences/2026/AreaChairInstructions) |
| neurips2025 | 1, 2, 3, 4, 5, 6 | [source 1](https://neurips.cc/Conferences/2025/ReviewerGuidelines) |
| tmlr_2025plus | 1, 2, 3, 4 | [source 1](https://jmlr.org/tmlr/acceptance-criteria.html); [source 2](https://jmlr.org/tmlr/reviewer-guide.html) |

Retrieved: 2026-09-13. The provenance URLs and source-specific notes are also stored in manifest.json.

- ACL2025, ACL2026 and EMNLP2025 use the ARR overall-assessment scheme introduced in February2025 for their designated review cycles. Earlier reviews may use earlier forms; soundness/excitement are separate fields, not the OA returned here.
- CVPR2025 and ICCV2025 numeric choices were checked against public conference-signed OpenReview invitation schemas. CVPR has one borderline category; ICCV has separate borderline-reject and borderline-accept categories. ICCV preliminary and final schemas use the same six choices, but this task is a manuscript-only assessment.
- ICML2025 uses the corrected five-point main-track recommendation on its official page (April4,2025 correction). ICML2026 uses six points; the two years are intentionally distinct.
- TMLR uses textual reviewer recommendations. At the user's explicit request, this study encodes reject=1, leaning reject=2, leaning accept=3, accept=4. This is an experimental ordinal encoding, not an official TMLR score, and the distances between values are not asserted to be equal. The 2025+ source bucket uses the criteria retrieved on the date above.
- Numeric scores from different sources have different meanings and must not be treated as a common scale. No acceptance-rate prior was invented for the new profiles.

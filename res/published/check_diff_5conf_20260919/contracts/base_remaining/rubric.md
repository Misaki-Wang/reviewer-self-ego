# Assessment criteria

## A1 — Comparability and material completeness
Assess whether the PDFs describe the same research work and whether their text, figures, tables, equations, and appendices support comparison. Use `comparable` when the material supports a reliable comparison, `partially_comparable` when identifiable gaps limit part of it, and `unable_to_determine` when the work's identity or essential material remains unresolved. A substantial revision can still be comparable. Reading or extraction gaps indicate unavailable evidence, not deleted content; use `maybe` for affected change dimensions while judging readable content normally.

## A2 — Original empirical results
Assess each version separately for original experimental, simulation, user-study, or data-analysis results produced by the work, in either the main text or appendices. Use `yes` when such outcomes are reported, `no` when inspection establishes their absence, and `uncertain` when the available material or attribution leaves this unresolved. Original results retained from an earlier version still qualify. Experimental plans, settings alone, theoretical proofs, and results attributed solely to other work do not qualify.

# Change dimensions

## M1 — Method and model
Changes to the proposed algorithm steps, model architecture, training objectives, losses, inputs and outputs, inference procedures, or key components. Count a newly introduced method component or an adaptation that changes the procedure. Renaming, expanded explanations, newly disclosed implementation details, or testing the same method on another benchmark alone do not establish a method change; assess those aspects under P1, D1, or E1.

## E1 — Dataset and task coverage
Added, removed, or revised datasets, tasks, domains, languages, populations, or test scenarios actually used for training or evaluation. Identify coverage by the represented data and task requirements, not dataset names or the addition of an experiment alone. A distinct test scenario changes task requirements, the represented population, or input-environment conditions. Changes only to splits, sample counts, model configurations, or procedures within retained coverage belong to E3; newly reported results alone belong to E6.

## E2 — Baselines and comparison methods
Added, removed, or substantively revised comparison methods actually evaluated against the proposed method. Check comparator membership within corresponding experiments: adding or removing a comparator there counts even if it appears elsewhere in the paper. Verify participation in the relevant reported comparison; a citation alone is insufficient. Match renamed methods. Changed scores for the same comparator set belong to E6, and changed evaluation settings belong to E3. Extending the same comparator set to an added benchmark alone changes coverage and results, rather than the comparator set.

## E3 — Evaluation protocol and experimental settings
Changes to data splits, preprocessing, evaluation metrics, training budgets, model sizes, controls, sample sizes, or testing procedures. Added experiments can introduce new protocols; identify those separately from revisions to existing protocols. Newly disclosed settings for an existing experiment belong to D1. A substantive revision to a previously stated setting counts as a reported protocol change, without establishing that experiments were rerun. Repeated-run counts, seeds, and uncertainty estimation belong to E5; also count E3 when the evaluation design itself changes.

## E4 — Ablations, robustness and supplementary analyses
Added, removed, or revised ablations, sensitivity analyses, distribution-shift tests, robustness tests, error analyses, or case studies. Identify the question tested and the evidence added or removed. Appendix placement alone does not make a result a supplementary analysis: extra benchmark scores may affect E1/E6 without affecting E4. Explanatory discussion belongs to C2, and redisplaying the same analysis belongs to P2.

## E5 — Statistics and uncertainty reporting
Added, removed, or changed repeated-run or seed information, uncertainty reporting practices, confidence intervals, significance tests, or other statistical estimation procedures. First reporting uncertainty for existing results counts, even when the PDFs do not establish that additional runs were performed. More result rows using the same reporting practice, or different means, error-bar values, and significance outcomes alone, belong to E6. Distinguish newly disclosed run counts from an explicit increase in runs; neither implies the other.

## E6 — Concrete results
Changes in numerical values, rankings, trends, or qualitative findings, plus added or removed result entries. Match the task, method, metric, and experimental conditions. With different conditions, describe replaced or newly reported results rather than a same-setting performance gain or loss. Renumbering, relocation, or equivalent rounding alone does not change results; a corrected value does, even if conclusions remain stable.

## T1 — Theory, assumptions and proofs
Added, removed, or substantively revised mathematical definitions, formal assumptions, propositions, theorems, proofs, derivations, or counterexamples. A new proof or a correction to mathematical reasoning counts even if the method is unchanged. Equivalent notation, typesetting, restating existing mathematics, intuitive explanations, and conceptual motivation alone do not count. Assess changes to explanatory prose under P1 and substantive scientific interpretations under C2. An empirical metric change belongs to E3; also count T1 when it introduces or revises a mathematical definition, reasoning, or formal property. Merely stating the standard formula for an existing metric is additional explanation.

## C1 — Core claims and contributions
Main claims in the abstract, introduction, and conclusion that are added, removed, strengthened, narrowed, or redefined. Compare their meaning and scope. New supporting evidence alone does not change a retained claim, and stronger adjectives or rewritten wording alone do not establish a stronger scientific claim. A changed claim about novelty may also affect R1 when its relationship to prior work changes.

## C2 — Interpretation and limitations
Changes to interpretations of results, mechanism explanations, causal arguments, applicability boundaries, failure conditions, or limitations. Count new substantive explanations or qualifications, including newly stated limitations of unchanged work. Moving an existing limitations paragraph, restating the same interpretation, or adding generic caveats alone is a presentation change. Keep reported observations under E6 distinct from their interpretation here.

## D1 — Technical details and reproducibility information
Added, removed, or revised implementation details, hyperparameters, hardware, training time, data processing instructions, or code and data access information. Count newly disclosed details even when the underlying experiment is unchanged; distinguish disclosure from an actual setting change under E3. Count changes in resource availability or access instructions, while an equivalent URL spelling or relocated disclosure alone is a presentation edit. A new method or benchmark does not automatically change D1 without corresponding technical or access information.

## R1 — Related work and research positioning
Added or removed cited works and substantive changes in the work's relationship to earlier research, claimed novelty, or contribution positioning. Match the same work across title, venue, and preprint-to-publication updates. Citation style, reference numbering, bibliographic metadata corrections, or moving related work alone belong to P1/P2. Distinguish a limited citation addition from a substantial expansion or revision of research positioning.

## P1 — Language and document structure
Language editing, paragraph rewrites, section reordering, expanded explanations, terminology, and narrative structure. Renaming and author/header text edits can be minor here even when scientific content is unchanged. Assess the actual extent of rewriting or reorganization; added scientific content alone does not establish a major language change. Record accompanying changes in scientific meaning or evidence in their corresponding dimensions.

## P2 — Figures, tables and layout
Added, removed, redrawn, merged, or relocated figures and tables, plus changes to axes, legends, captions, table structure, and page layout. Distinguish new content from redisplayed or relocated content. Numerical updates inside an otherwise unchanged table belong to E6; assess P2 when the display or layout also changes. Page count or numbering alone does not establish a major presentation change; assess the actual visual changes.

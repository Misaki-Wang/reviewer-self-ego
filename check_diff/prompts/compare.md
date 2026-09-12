Compare these two PDFs of arXiv {arxiv_id}, including their appendices:

- Earlier version: `{before_filename}` — `{before_path}`
- Later version: `{after_filename}` — `{after_path}`

Comparison direction: `{before_filename} -> {after_filename}`.
In `overall.A2`, `before` refers to `{before_filename}` and `after` refers to `{after_filename}`.
Use the available PDF skill to read the two PDFs, choosing the tools and approach that suit the comparison.

Assess A1 for the pair and A2 separately for each version. Then judge changes along the fourteen dimensions below. For each change dimension, choose one label:
- `major change`: A substantial addition, removal, or revision in the scope or substance of this dimension.
- `minor change`: A limited addition, removal, correction, or refinement within this dimension.
- `maybe`: Incomplete or ambiguous evidence prevents a reliable decision about whether content changed or how substantial the change is. State the specific uncertainty.
- `no change`: The content covered by this dimension is unchanged, or inspection establishes that it is absent from both versions.

Judge each dimension independently. A new experiment may affect several dimensions, but each judgment needs a difference relevant to that dimension. Assess magnitude from what changed, rather than page count, the number of textual edits, or the overall size of the revision. Extensive rewriting or layout changes can be major within P1 or P2 even when the scientific content is stable.

Match content across renaming, renumbering, and moves between the main text and appendices before deciding it was added or removed. Additional detail about an existing method or experiment changes what is documented; describe an actual method or setting change only when the versions support that distinction. Check cited baseline names, numerical values, and findings against the corresponding table, figure, or passage in each PDF. Compare performance directly only under comparable settings.

Start the JSON with an `overall` object containing A1 and A2. A1 contains `value` and `justification`. A2 contains `before` and `after`, each with `value` and `justification`. A1 and A2 use their own assessment labels from the criteria below. The remaining top-level entries are the fourteen change dimensions, each containing `change` and `justification`.

Return the comparison as the specified JSON. Write concise English justifications as plain text. Describe the relevant before-and-after differences and why they warrant the assigned label; use multiple sentences when helpful. Refer to sections, tables, figures, or PDF filenames when useful. Use these plain-text references instead of file-citation markup or local paths in the JSON. The PDF skill supplies reading guidance; this task defines the output format.

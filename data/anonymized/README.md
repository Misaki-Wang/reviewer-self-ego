# Anonymized paper data

- major_imp_index/: source-grouped major-improvement paper/version indexes.
- pdf/: destination for verified anonymized PDFs, initially empty.

Use data/anonymized/pdf/<arxiv_id>/<arxiv_id>v<version>.pdf for future outputs.
Original PDFs remain under data/pdf/. Paths in the index are relative to the
project root, not the index directory. After output validation, update the relevant
anonymized_pdf_path, receipt path and completion state in every matching index row.
Keep private execution logs outside this shareable directory. The .gitkeep file
retains the initially empty PDF directory in Git; no PDF has been generated here.

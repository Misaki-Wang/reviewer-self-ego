#!/usr/bin/env python3
"""Build pairs offline from clean_data/<conference>/<arxiv_id>/vN.pdf.

Reuse the existing maximum-sequence selection, frozen plans and PDF validation.
Download and anonymize PDFs before constructing pairs.
"""
if __package__:
    from .prepare_source_data import main as prepare_main
else:
    from prepare_source_data import main as prepare_main


def main():
    prepare_main(cleaned=True)


if __name__ == '__main__':
    main()

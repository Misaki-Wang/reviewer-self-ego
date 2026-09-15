#!/usr/bin/env python3
"""Produce a rule-checked anonymization candidate without Codex or model calls.

Use explicit output/report paths outside original and production clean stores.
Rules do not replace visual review. Uncertainty remains in the receipt.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

if __package__:
    from . import anonymize_pdf as pdf
    from . import anonymize_rules as rules
else:
    import anonymize_pdf as pdf
    import anonymize_rules as rules

ROOT = Path(__file__).resolve().parents[2]


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def validate_paths(source, paper, output, report, work):
    aid = paper.get('arxiv_id')
    if not isinstance(aid, str) or not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
        raise ValueError('Paper metadata requires a modern arXiv ID')
    match = re.fullmatch(rf'(?:{re.escape(aid)})?v([1-9]\d*)\.pdf', source.name)
    if not match or (source.name.startswith('v') and source.parent.name != aid):
        raise ValueError('PDF filename/directory does not match the supplied paper metadata')
    number = int(match.group(1))
    if paper.get('versions') and number not in {v['version'] for v in paper['versions']}:
        raise ValueError('PDF version is outside the supplied original history')
    if len({source, output, report}) != 3 or output.suffix.lower() != '.pdf' or report.suffix.lower() != '.json':
        raise ValueError('Use distinct original, output PDF and report JSON paths')
    protected = [ROOT / name for name in ('data', 'data_full', 'downloads_papers', 'clean_data')]
    for path in (output, report, work):
        if any(path.is_relative_to(folder) for folder in protected):
            raise ValueError('Rules candidates/reports must stay outside original and production clean stores')
    if source.is_relative_to(work) or output.is_relative_to(work) or report.is_relative_to(work):
        raise ValueError('The QA directory must not contain the input, output or report')
    return number


def anonymize(source, paper, output, report, work=None):
    import pymupdf
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError('Expected one original PDF')
    output = pdf.regular_destination(Path(output))
    report = pdf.regular_destination(Path(report))
    work = Path(work or report.parent / (report.stem + '-qa')).absolute()
    if any(p.is_symlink() for p in (work, *work.parents)):
        raise ValueError('QA directory must not traverse symlinks')
    work = work.resolve()
    version = validate_paths(source, paper, output, report, work)
    binding = {
        'backend': 'rules', 'rules_version': rules.RULES_VERSION,
        'source_sha256': pdf.digest(source),
        'paper_metadata_sha256': hashlib.sha256(encoded(paper)).hexdigest(),
        'rules_source_sha256': pdf.digest(Path(rules.__file__)),
        'pdf_helpers_sha256': pdf.digest(Path(pdf.__file__)),
        'driver_sha256': pdf.digest(Path(__file__)),
        'pymupdf': pymupdf.VersionBind,
    }
    if output.exists():
        previous = json.loads(report.read_text()) if report.exists() else {}
        if (previous.get('status') in ('rules_passed', 'needs_manual_review')
                and previous.get('binding') == binding
                and previous.get('output_sha256') == pdf.digest(output)):
            return {**previous, 'cache_hit': True}
        raise FileExistsError('Output lacks a matching rules receipt; use a fresh destination')
    if report.exists() or work.exists():
        raise FileExistsError('Report or QA directory already exists; use a fresh destination')
    work.mkdir(parents=True)
    started = time.monotonic()
    receipt = {
        'schema_version': 'rules-anonymization-report-1', 'backend': 'rules',
        'status': 'in_progress', 'arxiv_id': paper['arxiv_id'], 'version': version,
        'input': str(source), 'output': str(output), 'report': str(report),
        'qa_directory': str(work), 'binding': binding, 'cache_hit': False,
        'codex_cli_calls': 0, 'model_api_calls': 0, 'human_reviewed': False,
        'model_reviewed': False, 'published_to_clean_data': False,
        'limitations': list(rules.LIMITATIONS),
    }
    pdf.write_json(report, receipt)
    try:
        pages = pdf.inspect_pdf(source, work)
        receipt['physical_pages'] = len(pages)
        plan = rules.build_plan(source, paper)
        if (not isinstance(plan, dict) or not isinstance(plan.get('uncertain'), list)
                or any(not isinstance(item, str) for item in plan['uncertain'])):
            raise ValueError('Rules returned malformed uncertainty metadata')
        receipt['plan'] = plan
        receipt['redaction_count'] = len(plan.get('redactions', []))
        receipt['redaction_categories'] = dict(Counter(r['category'] for r in plan.get('redactions', [])))
        receipt['changed_pages'] = sorted({r['page'] for r in plan.get('redactions', [])})
        # Keep uncertainty in the original plan and receipt. This is a quarantined
        # candidate flow, never the Codex pipeline's completed/clean publication.
        apply_plan = {**plan, 'uncertain': []}
        pdf.validate_plan(apply_plan, pages)
        pdf.write_json(work / 'plan.json', plan)
        if pdf.digest(source) != binding['source_sha256']:
            raise ValueError('Original PDF changed during rule generation')
        candidate = work / 'candidate.pdf'
        pdf.redact_pdf(source, candidate, apply_plan, pages)
        receipt['candidate_path'] = str(candidate)
        receipt['candidate_sha256'] = pdf.digest(candidate)
        receipt['local_validation'] = pdf.check_pdf(source, candidate, apply_plan, work)
        if pdf.digest(source) != binding['source_sha256']:
            raise ValueError('Original PDF changed during validation')
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.rules-', suffix='.pdf', delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copyfile(candidate, temporary)
            if pdf.digest(temporary) != receipt['candidate_sha256']:
                raise ValueError('Candidate changed while copying')
            # Link only the private copied file, never the original PDF; fail if
            # another writer created the output after the initial cache check.
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        receipt['output_sha256'] = receipt['candidate_sha256']
        receipt['status'] = 'needs_manual_review' if plan['uncertain'] else 'rules_passed'
    except Exception as exc:
        receipt.update(status='failed_validation', error=f'{type(exc).__name__}: {exc}')
    finally:
        try:
            receipt['source_unchanged'] = pdf.digest(source) == binding['source_sha256']
        except OSError:
            receipt['source_unchanged'] = False
        if not receipt['source_unchanged']:
            receipt.update(status='failed_validation', error='Original PDF hash changed')
        receipt['elapsed_seconds'] = time.monotonic() - started
        pdf.write_json(report, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--metadata', required=True, type=Path, help='One complete original paper JSON record')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        paper = json.loads(args.metadata.read_text())
        if args.dry_run:
            source = args.input.resolve(strict=True)
            output, report = pdf.regular_destination(args.output), pdf.regular_destination(args.report)
            work = (args.work_dir or report.parent / (report.stem + '-qa')).resolve()
            validate_paths(source, paper, output, report, work)
            print(json.dumps({'backend': 'rules', 'input': str(source), 'output': str(output),
                              'codex_cli_calls': 0, 'model_api_calls': 0, 'writes': False}))
            return 0
        receipt = anonymize(args.input, paper, args.output, args.report, args.work_dir)
        print(json.dumps({k: receipt[k] for k in
              ('status', 'output', 'report', 'codex_cli_calls', 'model_api_calls', 'cache_hit')}))
        return {'rules_passed': 0, 'needs_manual_review': 2, 'failed_validation': 1}[receipt['status']]
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'Rule anonymization failed: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())

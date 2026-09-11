#!/usr/bin/env python3
"""Build an English static review site from prepared pairs and actual results.

The output includes public paper metadata, validated judgments, exact saved prompts,
and an allowlist of execution fields. It never copies PDFs or API response bodies.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

if __package__:
    from .schema import DIMENSIONS, SCHEMA, validate_result
else:
    from schema import DIMENSIONS, SCHEMA, validate_result

HERE = Path(__file__).resolve().parent
SOURCE_NAMES = {
    'acl2025': 'ACL 2025', 'acl2026': 'ACL 2026', 'cvpr2025': 'CVPR 2025',
    'emnlp2025': 'EMNLP 2025', 'iccv2025': 'ICCV 2025', 'iclr2026': 'ICLR 2026',
    'icml2025': 'ICML 2025', 'icml2026': 'ICML 2026', 'neurips2025': 'NeurIPS 2025',
    'tmlr_2025plus': 'TMLR 2025+',
}
EXECUTION_FIELDS = (
    'status', 'backend', 'model', 'reasoning_effort', 'effort', 'started_at_utc',
    'finished_at_utc', 'completed_at_utc', 'ended_at_utc', 'elapsed_seconds', 'duration_seconds',
    'total_seconds', 'api_seconds', 'attempt_count', 'attempts_count', 'request_id',
    'prompt_sha256', 'schema_sha256', 'response_sha256', 'result_sha256', 'usage',
    'input_tokens', 'output_tokens', 'total_tokens', 'error_type', 'error_code',
    'response_id', 'returned_model', 'returned_effort', 'input_mode',
)
RUN_FIELDS = (
    'run_id', 'status', 'backend', 'model', 'reasoning_effort', 'effort', 'workers',
    'concurrency', 'started_at_utc', 'finished_at_utc', 'completed_at_utc', 'ended_at_utc',
    'elapsed_seconds', 'wall_time_seconds', 'wall_seconds', 'duration_seconds',
    'pair_count', 'total_pairs', 'completed', 'failed', 'success_count', 'failure_count',
    'counts', 'sample_selection', 'seed', 'source_release',
)


def read_json(path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def numeric(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def seconds(record):
    for key in ('elapsed_seconds', 'duration_seconds', 'total_seconds'):
        if numeric(record.get(key)):
            return record[key]
    return None


def percentile(values, fraction):
    """Linear interpolation of observed durations; unavailable when empty."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def public_execution(record):
    """Exclude raw gateway errors, response bodies, credentials, and reasoning."""
    record = record if isinstance(record, dict) else {}
    safe = {key: record[key] for key in EXECUTION_FIELDS if key in record
            and isinstance(record[key], (str, int, float, bool, type(None)))}
    attempts = record.get('attempts', [])
    if isinstance(attempts, list):
        safe['attempts'] = [{**public_execution({k: v for k, v in item.items() if k != 'attempts'}),
                             **{key: item[key] for key in ('attempt', 'attempt_number')
                                if numeric(item.get(key))}}
                            for item in attempts if isinstance(item, dict)]
    if isinstance(record.get('usage'), dict):
        safe['usage'] = {k: v for k, v in record['usage'].items() if numeric(v)}
    if type(record.get('http_status')) is int and 100 <= record['http_status'] <= 599:
        safe['http_status'] = record['http_status']
    return safe


def version_number(value):
    value = str(value).removeprefix('v')
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise ValueError('Invalid original version ID in pair metadata')
    return int(value)


def public_version(version, aid):
    number = version_number(version['version'])
    count = version.get('pages')
    if count is None and isinstance(version.get('page_count'), dict):
        count = version['page_count'].get('value')
    return {
        'version': number, 'submitted_at': version.get('submitted_at'),
        'pages': count if numeric(count) else None,
        'sha256': version.get('sha256') or version.get('pdf', {}).get('sha256'),
        'arxiv_url': f'https://arxiv.org/abs/{aid}v{number}',
        'pdf_url': f'https://arxiv.org/pdf/{aid}v{number}',
    }


def gap_seconds(before, after):
    try:
        timestamps = [datetime.fromisoformat(v['submitted_at'].replace('Z', '+00:00'))
                      for v in (before, after)]
        if any(t.tzinfo is None for t in timestamps):
            return None
        return (timestamps[1] - timestamps[0]).total_seconds()
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def safe_prompt(text):
    # Prompts should be instructions, never a serialized PDF/API request.
    if re.search(r'data:[^\s;,]+;base64,|"file_data"\s*:', text, re.I):
        raise ValueError('Refusing to publish an encoded file in a prompt artifact')
    if re.search(r'\bBearer\s+[A-Za-z0-9._~-]{12,}|\bsk-[A-Za-z0-9_-]{16,}', text):
        raise ValueError('Refusing to publish a credential-like prompt artifact')
    return text


def receipt_prompt(folder, receipt):
    """Read only the exact, hash-matching prompt named by this execution."""
    filename = receipt.get('prompt_file')
    if (not isinstance(filename, str) or not filename.endswith('.prompt.txt')
            or Path(filename).name != filename or '/' in filename or '\\' in filename):
        return None, 'The execution does not name a safe saved prompt file.'
    path = folder / filename
    if path.resolve().parent != folder.resolve() or not path.is_file():
        return None, 'The exact saved execution prompt is unavailable.'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt.get('prompt_sha256'):
        return None, 'The saved prompt does not match the execution prompt hash.'
    safe_prompt(raw.decode('utf-8'))
    return raw, None


def provenance_issues(receipt, aid, before, after, result_path, prompt_issue):
    """A valid JSON alone cannot establish a current successful API execution."""
    issues = []
    identity = {'arxiv_id': aid, 'pair_id': f'{aid}__v{before["version"]}_to_v{after["version"]}',
                'versions': {'before': before['version'], 'after': after['version']},
                'result_file': result_path.name, 'schema_version': 'check-diff-execution-1'}
    if any(receipt.get(k) != value for k, value in identity.items()):
        issues.append('Execution identity does not match this prepared pair and result file.')
    if receipt.get('backend') != 'responses':
        issues.append('A matching Responses API execution receipt is unavailable.')
    schema_sha = hashlib.sha256(json.dumps(SCHEMA, sort_keys=True).encode('utf-8')).hexdigest()
    if receipt.get('schema_sha256') != schema_sha:
        issues.append('The execution schema hash does not match the validated judgment schema.')
    pdfs = receipt.get('pdfs', [])
    if not isinstance(pdfs, list) or len(pdfs) != 2:
        issues.append('The execution does not record both input PDFs.')
    else:
        for side, version in (('before', before), ('after', after)):
            found = [p for p in pdfs if isinstance(p, dict) and p.get('side') == side]
            if (len(found) != 1 or found[0].get('version') != version['version']
                    or not version['sha256'] or found[0].get('sha256') != version['sha256']):
                issues.append(f'The {side} PDF provenance does not match the prepared input.')
    if prompt_issue:
        issues.append(prompt_issue)
    if receipt.get('result_sha256') and hashlib.sha256(result_path.read_bytes()).hexdigest() != receipt['result_sha256']:
        issues.append('The result file does not match the execution result hash.')
    for returned, requested in (('returned_model', 'model'), ('returned_effort', 'reasoning_effort')):
        if receipt.get(returned) and receipt.get(returned) != receipt.get(requested):
            issues.append(f'The returned {requested.replace("_", " ")} does not match the request.')
    return issues


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build(input_dir, results_dir, output_dir, run_manifest=None):
    input_dir, results_dir, output_dir = map(Path, (input_dir, results_dir, output_dir))
    if output_dir.resolve() in (input_dir.resolve(), results_dir.resolve(), HERE.resolve()):
        raise ValueError('Preview output must be separate from inputs, results, and source code')
    inventory = read_json(input_dir / 'pairs.json')
    if inventory is None:
        pairs = [read_json(p) for p in sorted(input_dir.glob('*/v*_v*/pair.json'))]
        inventory = {}
    else:
        pairs = inventory if isinstance(inventory, list) else inventory.get('pairs', [])
    if not pairs:
        raise ValueError('No prepared pairs found; provide input_data with pairs.json or pair.json files')
    paper_rows = {row['arxiv_id']: row for row in inventory.get('papers', [])
                  if isinstance(row, dict) and 'arxiv_id' in row} if isinstance(inventory, dict) else {}
    output_dir.mkdir(parents=True, exist_ok=True)
    records, seen = [], set()
    for pair in pairs:
        aid = pair['arxiv_id']
        if not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
            raise ValueError('Invalid arXiv ID in pair metadata')
        before, after = [public_version(pair[side], aid) for side in ('before', 'after')]
        if before['version'] >= after['version']:
            raise ValueError(f'Pair direction is not earlier-to-later: {aid}')
        key = f'{aid}__v{before["version"]}_v{after["version"]}'
        if key in seen:
            raise ValueError(f'Duplicate prepared pair: {key}')
        seen.add(key)
        stem = f'diff_v{before["version"]}_v{after["version"]}'
        result_path = results_dir / aid / (stem + '.json')
        execution_path = result_path.with_suffix('.execution.json')
        receipt = read_json(execution_path, {})
        execution = public_execution(receipt)
        prompt_raw, prompt_issue = receipt_prompt(result_path.parent, receipt)
        result, warning = None, None
        if result_path.is_file():
            try:
                result = read_json(result_path)
                validate_result(result)
            except (ValueError, TypeError):
                result, warning = None, 'The saved result does not match the required judgment schema.'
        availability = 'invalid' if warning else 'absent'
        recorded_status = execution.get('status', '')
        status = {'failed': 'failed', 'error': 'failed', 'invalid': 'failed',
                  'running': 'running', 'started': 'running', 'in_progress': 'running',
                  'stopped': 'stopped'}.get(recorded_status, 'failed' if warning else 'pending')
        if result is not None:
            issues = provenance_issues(receipt, aid, before, after, result_path, prompt_issue)
            if recorded_status != 'completed':
                issues.insert(0, 'The latest execution is not completed; an existing result is not current evidence.')
            if issues:
                result, availability = None, 'stale_or_unverified'
                warning = ' '.join(issues)
                if status == 'pending':
                    status = 'unavailable'
            else:
                status, availability = 'completed', 'current'
        elif recorded_status == 'completed':
            status = 'unavailable'
            warning = warning or 'The completed receipt has no available valid result file.'
        assigned_source = pair.get('assigned_source_id') or pair.get('selected_source') or pair.get('venue')
        sources = pair.get('source_ids') or [assigned_source]
        sources = [s for s in sources if isinstance(s, str) and s]
        source_entries = []
        for source in pair.get('source_entries', []):
            if isinstance(source, dict):
                source_entries.append({k: source[k] for k in
                                       ('source_id', 'venue', 'year', 'presentation', 'presentation_type',
                                        'decision', 'review_scores', 'url') if k in source})
        paper_row = paper_rows.get(aid, {})
        selected_ids = pair.get('selected_version_ids', pair.get('selected_witness_versions'))
        if selected_ids is not None:
            selected_ids = [version_number(v) for v in selected_ids]
            if selected_ids != sorted(set(selected_ids)):
                raise ValueError(f'Selected trajectory must contain increasing original version IDs: {aid}')
            if before['version'] not in selected_ids or after['version'] not in selected_ids:
                raise ValueError(f'Pair endpoints are absent from the selected trajectory: {aid}')
        unit = {
            'selected_version_ids': selected_ids,
            'original_version_count': paper_row.get('version_count'),
            'selected_version_count': len(selected_ids) if selected_ids is not None else paper_row.get('selected_version_count'),
            'pair_count': paper_row.get('pair_count'),
        }
        record = {
            'id': key, 'arxiv_id': aid, 'title': pair.get('title') or aid,
            'source_ids': sources, 'source_entries': source_entries,
            'assigned_source_id': assigned_source, 'selected_source': assigned_source,
            'before': before, 'after': after,
            'unit': unit, 'pair_type': pair.get('pair_type'),
            'gap_seconds': gap_seconds(before, after), 'status': status,
            'result': result, 'execution': execution, 'duration_seconds': seconds(execution),
            'result_availability': availability,
            'warning': warning, 'prompt_verification': prompt_issue or 'verified',
            'prompt_url': None, 'raw_url': f'raw/{key}.json',
        }
        if prompt_raw is not None:
            target = output_dir / 'prompts' / f'{key}.txt'
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(prompt_raw)
            record['prompt_url'] = f'prompts/{key}.txt'
            record['saved_prompt_sha256'] = hashlib.sha256(prompt_raw).hexdigest()
        write_json(output_dir / record['raw_url'], record)
        records.append(record)
    manifest_path = Path(run_manifest) if run_manifest else results_dir / 'run_manifest.json'
    manifest = read_json(manifest_path, {})
    manifest = {key: manifest[key] for key in RUN_FIELDS if key in manifest}
    durations = [r['duration_seconds'] for r in records if numeric(r['duration_seconds'])]
    completed_durations = [r['duration_seconds'] for r in records
                           if r['status'] == 'completed' and numeric(r['duration_seconds'])]
    walltime = next((manifest[k] for k in ('wall_time_seconds', 'wall_seconds', 'elapsed_seconds', 'duration_seconds')
                     if numeric(manifest.get(k))), None)
    stats = {
        'pairs': len(records), 'papers': len({r['arxiv_id'] for r in records}),
        'completed': sum(r['status'] == 'completed' for r in records),
        'failed': sum(r['status'] == 'failed' for r in records),
        'pending': sum(r['status'] == 'pending' for r in records),
        'running': sum(r['status'] == 'running' for r in records),
        'stopped': sum(r['status'] == 'stopped' for r in records),
        'unavailable': sum(r['status'] == 'unavailable' for r in records),
        'timed_pairs': len(durations), 'p50_seconds': percentile(durations, .5),
        'p95_seconds': percentile(durations, .95), 'max_seconds': max(durations, default=None),
        'completed_p50_seconds': percentile(completed_durations, .5),
        'wall_time_seconds': walltime,
    }
    data = {
        'schema': 'pair-review-preview-1', 'language': 'en',
        'built_at_utc': datetime.now(timezone.utc).isoformat(), 'source_release': 'v1.3',
        'dimensions': list(DIMENSIONS), 'source_names': SOURCE_NAMES,
        'input': {key: inventory[key] for key in ('pairing_rule', 'sample_selection', 'summary',
                                                'source_release', 'schema_version')
                  if isinstance(inventory, dict) and key in inventory},
        'summary': stats, 'run': manifest, 'pairs': records,
        'rubric': (HERE / 'prompts/rubric.md').read_text(encoding='utf-8'),
        'comparison_template': (HERE / 'prompts/compare.md').read_text(encoding='utf-8'),
        'notes': [
            'Each record is an earlier-to-later PDF pair, not a complete paper trajectory.',
            'Temporal eligibility and individual change judgments are separate assessments.',
            'Dimension labels have no defined aggregate pass/fail rule in this protocol.',
            'Pair durations include attempts and retry delays when reported by the runner. '
            'Their sum is not experiment wall time.',
            'This preview contains public metadata, judgments, and allowlisted execution fields. '
            'PDF files and raw API responses are not included.',
        ],
    }
    write_json(output_dir / 'data.json', data)
    for name in ('index.html', 'style.css', 'app.js'):
        shutil.copyfile(HERE / 'preview' / name, output_dir / name)
    print(json.dumps({'output': str(output_dir.resolve()), **stats}, indent=2))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Prepared PDF-pair input directory')
    parser.add_argument('--results', required=True, type=Path, help='Runner output directory')
    parser.add_argument('--output', required=True, type=Path, help='Separate, static website output directory')
    parser.add_argument('--run-manifest', type=Path, help='Optional aggregate execution manifest')
    args = parser.parse_args()
    build(args.input, args.results, args.output, args.run_manifest)


if __name__ == '__main__':
    main()

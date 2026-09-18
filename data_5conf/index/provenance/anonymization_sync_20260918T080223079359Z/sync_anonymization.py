#!/usr/bin/env python3
"""Reconcile accepted anonymization publications into the five-conference copy.

No model calls, source writes, or new anonymity judgments are performed. A
completed PDF receipt is per-file evidence, not terminal whole-batch QA.
"""
import argparse
import concurrent.futures
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.dont_write_bytecode = True
from merge_data import copy_verified, sha

SOURCES = ('iclr2026', 'icml2026', 'acl2026')
DEFAULT_RUN = '20260918_all_sources_codex_anonymize_han_fresh'
MUTABLE_FIELDS = ('is_anonymized', 'anonymization_status',
                  'anonymized_pdf_path', 'anonymization_report_path')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def project_path(root, value):
    """Normalize project-relative or project-absolute evidence paths."""
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    require('..' not in path.parts, 'Parent traversal in evidence path')
    path.relative_to(root)
    return path


def relative(root, value):
    return str(project_path(root, value).relative_to(root))


def freeze_events(path):
    """Read one bounded prefix; ignore an in-flight final partial JSONL line."""
    with path.open('rb') as stream:
        observed_size = os.fstat(stream.fileno()).st_size
        raw = stream.read(observed_size)
    end = raw.rfind(b'\n') + 1
    prefix = raw[:end]
    accepted = {}
    for line_number, line in enumerate(prefix.splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        if ((event.get('event') == 'attempt_finished' and event.get('status') == 'completed')
                or event.get('event') == 'publication_recovered'):
            require(isinstance(event.get('key'), str), 'Accepted event has no key')
            # The last accepted event binds the currently published receipt.
            accepted[event['key']] = {'line_number': line_number, 'event': event,
                                      'raw_line': line.decode('utf-8')}
    return prefix, accepted, {'observed_bytes': observed_size,
        'complete_prefix_bytes': len(prefix), 'ignored_tail_bytes': len(raw) - len(prefix),
        'complete_prefix_sha256': digest(prefix), 'complete_line_count': len(prefix.splitlines()),
        'accepted_key_count': len(accepted), 'frozen_at': now()}


def occurrences(document):
    result = {}
    for row in document['results']:
        for pdf in row['pdfs'].values():
            key = pdf['paper_version_id']
            require(re.fullmatch(r'\d{4}\.\d{4,5}v\d+', key) is not None,
                    'Invalid paper version identifier: ' + key)
            aid = key.rsplit('v', 1)[0]
            require(row['arxiv_id'] == aid, 'Paper/version identity mismatch: ' + key)
            result.setdefault(key, []).append(pdf)
    return result


def completed(pdf):
    return pdf.get('is_anonymized') is True and pdf.get('anonymization_status') == 'completed'


def immutable_view(document):
    result = copy.deepcopy(document)
    for row in result['results']:
        for pdf in row['pdfs'].values():
            for field in MUTABLE_FIELDS:
                pdf.pop(field, None)
    return result


def expected_report(root, run_id, key, event):
    if event.get('report'):
        return relative(root, event['report'])
    require(event.get('event') == 'publication_recovered', 'Accepted event has no report')
    require(type(event.get('round')) is int and event['round'] >= 0, 'Invalid recovery round')
    return f'experiments/runs/{run_id}/rounds/round-{event["round"]:03d}/{key}/report.json'


def validate_candidate(root, run_id, source, item, membership, accepted, provenance):
    """Validate one frozen input, final receipt, publication, and destination copy."""
    key = item['key']
    aid = key.rsplit('v', 1)[0]
    receipt_rel = f'res/anonymization_records/receipts/{run_id}/{source}/{key}.json'
    receipt_path = root / receipt_rel
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    # Retain exact readable evidence even if its gates fail.
    save(provenance / 'receipts' / source / (key + '.json'), raw)
    require(receipt.get('schema_version') == 'anonymous-archive-validation-v1', 'Receipt schema mismatch')
    require(receipt.get('status') == 'completed', 'Receipt is not completed')
    require(receipt.get('key') == key, 'Receipt key mismatch')
    require(receipt.get('source') == source, 'Receipt source mismatch')
    require(receipt.get('run_id') == run_id, 'Receipt run mismatch')
    require(receipt.get('original_sha256') == item['input_sha256'], 'Original hash does not match frozen scope')
    require(receipt.get('original_bytes') == item['input_bytes'], 'Original bytes do not match frozen scope')
    require(relative(root, receipt['original_pdf_path']) == relative(root, item['input']), 'Original path does not match frozen scope')
    require(receipt.get('binding', {}).get('source_sha256') == item['input_sha256'], 'Binding source hash mismatch')
    require(relative(root, receipt['output']) == relative(root, membership['output']), 'Output membership mismatch')
    require(relative(root, receipt['native_report_path']) == expected_report(root, run_id, key, accepted['event']),
            'Receipt does not bind accepted report')
    pages = item['pages']
    require(type(pages) is int and pages > 0, 'Invalid frozen page count')
    require(receipt.get('pages') == pages, 'Receipt page count mismatch')
    review = receipt.get('review', {})
    local = receipt.get('local_validation', {})
    require(review.get('passed') is True and review.get('content_preserved') is True,
            'Review did not pass content-preservation gates')
    require(review.get('remaining_identifiers') == [], 'Review reports remaining identifiers')
    reviewed = review.get('reviewed_pages')
    require(isinstance(reviewed, list) and all(type(p) is int for p in reviewed)
            and sorted(reviewed) == list(range(1, pages + 1)), 'Incomplete reviewed pages')
    require(local.get('metadata_removed') is True and local.get('outside_redactions_unchanged') is True,
            'Local validation gates failed')
    require(local.get('pages') == pages, 'Local page count mismatch')
    original = project_path(root, item['input'])
    output = project_path(root, membership['output'])
    require(original.stat().st_size == item['input_bytes'] and sha(original) == item['input_sha256'],
            'Actual original does not match frozen scope')
    copied_original = root / f'data_5conf/pdf/{aid}/{key}.pdf'
    require(copied_original.stat().st_size == item['input_bytes'] and sha(copied_original) == item['input_sha256'],
            'Destination original does not match frozen scope')
    require(output.stat().st_size == receipt['output_bytes'] and sha(output) == receipt['output_sha256'],
            'Actual published output does not match receipt')
    destination = root / f'data_5conf/anonymized/pdf/{source}/{aid}/{key}.pdf'
    if destination.exists():
        require(destination.stat().st_size == receipt['output_bytes'] and sha(destination) == receipt['output_sha256'],
                'Conflicting destination PDF; no overwrite performed')
        copied = {'reused_identical_destination': True, 'bytes': destination.stat().st_size,
                  'sha256': receipt['output_sha256']}
    else:
        copied = copy_verified(output, destination)
    require(copied['sha256'] == receipt['output_sha256'] and copied['bytes'] == receipt['output_bytes'],
            'Published output changed during copy')
    require(sha(receipt_path) == digest(raw), 'Receipt changed during validation')
    return {'source': source, 'key': key, 'receipt_path': receipt_rel,
        'receipt_sha256': digest(raw), 'output_sha256': copied['sha256'], 'output_bytes': copied['bytes'],
        'destination': str(destination.relative_to(root)),
        'copied': not copied['reused_identical_destination'], 'accepted_event': accepted}


def atomic_replace(path, raw, before_hash=None):
    """Preserve permissions and reject a changed destination before replacement."""
    temporary = path.with_name('.' + path.name + '.sync-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        if before_hash is not None:
            require(path.exists() and sha(path) == before_hash, 'Destination changed since snapshot: ' + str(path))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def summarize(root, source, document):
    versions = occurrences(document)
    done = {key for key, rows in versions.items() if all(completed(pdf) for pdf in rows)}
    physical = {key for key in versions if (root / f'data_5conf/anonymized/pdf/{source}/{key.rsplit("v", 1)[0]}/{key}.pdf').is_file()}
    return {'total_versions': len(versions), 'completed': len(done), 'physical': len(physical),
        'completed_but_missing_pdf': sorted(done - physical),
        'physical_without_completed_index': sorted(physical - done),
        'complete_pairs': sum(all(completed(pdf) and pdf['paper_version_id'] in physical
                                   for pdf in row['pdfs'].values()) for row in document['results'])}


def synchronize(project_root, run_id=DEFAULT_RUN, workers=4):
    root = Path(project_root).absolute()
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', run_id) is not None and '..' not in run_id,
            'Invalid run identifier')
    destination = root / 'data_5conf'
    require(destination.is_dir() and not destination.is_symlink(), 'Destination root is missing or a symlink')
    parent = destination / 'index/provenance'
    parent.mkdir(parents=True, exist_ok=True)
    with (parent / '.anonymization_sync.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _synchronize_locked(root, run_id, workers, parent)


def _synchronize_locked(root, run_id, workers, parent):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    provenance = parent / ('anonymization_sync_' + stamp)
    provenance.mkdir()
    run = root / 'experiments/runs' / run_id
    save(provenance / 'sync_anonymization.py', Path(__file__).read_bytes())
    for filename in ('merge_data.py', 'catalog_merge.py'):
        save(provenance / filename, Path(__file__).with_name(filename).read_bytes())
    for name in ('scope.json', 'config.json'):
        save(provenance / name, (run / name).read_bytes())
    scope = json.loads((provenance / 'scope.json').read_bytes())
    items = scope['items']
    require(len({item['key'] for item in items}) == len(items), 'Duplicate frozen scope keys')
    prefix, accepted, cutoff = freeze_events(run / 'events.jsonl')
    save(provenance / 'events.complete-prefix.jsonl', prefix)
    save(provenance / 'event_cutoff.json', json_bytes(cutoff))
    major = root / 'data_5conf/anonymized/major_imp_index'
    originals, documents, versions = {}, {}, {}
    for source in SOURCES:
        raw = (major / (source + '.json')).read_bytes()
        originals[source] = raw
        documents[source] = json.loads(raw)
        versions[source] = occurrences(documents[source])
        save(provenance / 'before-indexes' / (source + '.json'), raw)
    if (major / 'SHA256SUMS').exists():
        save(provenance / 'before-indexes/SHA256SUMS', (major / 'SHA256SUMS').read_bytes())
    before = {s: summarize(root, s, d) for s, d in documents.items()}
    tasks, preserved = [], []
    for item in items:
        if item['key'] not in accepted:
            continue
        for membership in item['memberships']:
            source = membership['source']
            if source not in SOURCES or item['key'] not in versions[source]:
                continue
            if source in ('iclr2026', 'acl2026') and all(completed(p) for p in versions[source][item['key']]):
                preserved.append({'source': source, 'key': item['key'], 'reason': 'Inherited completed baseline preserved unchanged'})
                continue
            tasks.append((source, item, membership, accepted[item['key']]))
    require(len({(t[0], t[1]['key']) for t in tasks}) == len(tasks), 'Duplicate source/key publication task')
    save(provenance / 'plan.json', json_bytes({'started_at': now(), 'sources': SOURCES,
        'run_id': run_id, 'candidate_count': len(tasks), 'event_cutoff': cutoff,
        'before': before, 'policy': 'Only accepted, receipt-bound publications may update per-file completion. Inherited ICLR/ACL completed records stay unchanged. No source writes or model calls. Per-file completion does not establish terminal batch QA.'}))
    results, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        pending = {pool.submit(validate_candidate, root, run_id, source, item, membership, event, provenance):
                   (source, item['key']) for source, item, membership, event in tasks}
        for future in concurrent.futures.as_completed(pending):
            source, key = pending[future]
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({'source': source, 'key': key, 'status': 'blocked_unchanged',
                               'error': type(exc).__name__ + ': ' + str(exc)})
    results.sort(key=lambda r: (r['source'], r['key']))
    errors.sort(key=lambda r: (r['source'], r['key']))
    for result in results:
        for pdf in versions[result['source']][result['key']]:
            pdf.update({'is_anonymized': True, 'anonymization_status': 'completed',
                        'anonymized_pdf_path': result['destination'],
                        'anonymization_report_path': result['receipt_path']})
    save(provenance / 'accepted_publications.json', json_bytes(results))
    save(provenance / 'blocked_items.json', json_bytes(errors))
    save(provenance / 'preserved_baseline.json', json_bytes(preserved))
    changed = {}
    for source, document in documents.items():
        old = json.loads(originals[source])
        require(immutable_view(old) == immutable_view(document), 'Scientific metadata changed: ' + source)
        require(sha(major / (source + '.json')) == digest(originals[source]), 'Index changed since snapshot: ' + source)
        if old != document:
            changed[source] = json_bytes(document)
    for source, raw in changed.items():
        atomic_replace(major / (source + '.json'), raw, digest(originals[source]))
    checksum_path = major / 'SHA256SUMS'
    checksum_before = sha(checksum_path) if checksum_path.exists() else None
    checksums = ''.join(sha(p) + '  ' + p.name + '\n' for p in sorted(major.iterdir())
                        if p.is_file() and p.name != 'SHA256SUMS' and not p.name.startswith('.'))
    if not checksum_path.exists() or checksum_path.read_text() != checksums:
        atomic_replace(checksum_path, checksums.encode(), checksum_before)
    after = {s: summarize(root, s, d) for s, d in documents.items()}
    for source, stats in after.items():
        stats.update({'previous_completed': before[source]['completed'],
            'newly_completed': stats['completed'] - before[source]['completed'],
            'previous_physical': before[source]['physical'],
            'newly_stored': stats['physical'] - before[source]['physical'],
            'copied_files': sum(r['source'] == source and r['copied'] for r in results),
            'reused_files': sum(r['source'] == source and not r['copied'] for r in results),
            'validated_publications': sum(r['source'] == source for r in results),
            'blocked_items': sum(r['source'] == source for r in errors)})
    summary = {'completed_at': now(), 'status': 'completed_with_blocked_items' if errors else 'completed',
        'run_id': run_id, 'provenance': str(provenance.relative_to(root)),
        'sources': after, 'event_cutoff': cutoff, 'changed_source_indexes': sorted(changed),
        'before_index_sha256': {s: digest(raw) for s, raw in originals.items()},
        'after_index_sha256': {s: sha(major / (s + '.json')) for s in SOURCES},
        'errors': errors, 'scientific_metadata_unchanged': True,
        'terminal_batch_qa': 'Not asserted; completed refers only to individually accepted publications.',
        'source_files_modified': False, 'model_calls_performed': 0}
    save(provenance / 'verification.json', json_bytes(summary))
    status_path = parent / 'anonymization_status.json'
    if status_path.exists():
        save(provenance / 'previous_anonymization_status.json', status_path.read_bytes())
    atomic_replace(status_path, json_bytes(summary), sha(status_path) if status_path.exists() else None)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--run-id', default=DEFAULT_RUN)
    parser.add_argument('--workers', default=4, type=int)
    args = parser.parse_args()
    print(json.dumps(synchronize(args.project_root, args.run_id, args.workers), ensure_ascii=False))


if __name__ == '__main__':
    main()

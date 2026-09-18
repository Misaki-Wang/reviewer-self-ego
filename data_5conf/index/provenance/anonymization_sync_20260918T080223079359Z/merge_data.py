#!/usr/bin/env python3
"""Merge authorized conference archives without modifying source files."""
import argparse
import ast
import collections
import concurrent.futures
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

sys.dont_write_bytecode = True
from catalog_merge import build_catalog

SOURCES = ('iclr2026', 'icml2026', 'acl2026')


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode()
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError('Refusing to replace an existing artifact: ' + str(path))
        return
    with path.open('xb') as f:
        f.write(payload)


def signature(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode))


def copy_verified(source, destination):
    """Independent byte copy; exclusive publish; preserve the source mode."""
    source, destination = Path(source), Path(destination)
    before = source.stat()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError('Not a regular input file: ' + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    if any(p.is_symlink() for p in [destination, *destination.parents]):
        raise RuntimeError('Destination contains a symlink: ' + str(destination))
    temporary = destination.with_name('.' + destination.name + '.merge-' + uuid.uuid4().hex)
    h = hashlib.sha256()
    try:
        with source.open('rb') as src, temporary.open('xb') as dst:
            if signature(os.fstat(src.fileno())) != signature(before):
                raise RuntimeError('Source changed before opening: ' + str(source))
            for block in iter(lambda: src.read(8 * 1024 * 1024), b''):
                h.update(block)
                dst.write(block)
        if signature(source.stat()) != signature(before):
            raise RuntimeError('Source changed while copying: ' + str(source))
        digest = h.hexdigest()
        if temporary.stat().st_size != before.st_size or sha(temporary) != digest:
            raise RuntimeError('Copied bytes failed validation: ' + str(source))
        os.chmod(temporary, stat.S_IMODE(before.st_mode))
        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        existed = destination.exists()
        if existed:
            if destination.stat().st_size != before.st_size or sha(destination) != digest:
                raise RuntimeError('Conflicting destination: ' + str(destination))
        else:
            os.link(temporary, destination)  # Only links our independent new temporary file.
        assert destination.stat().st_ino != before.st_ino or destination.stat().st_dev != before.st_dev
        return {'source': str(source), 'destination': str(destination), 'bytes': before.st_size,
                'sha256': digest, 'source_mode': oct(stat.S_IMODE(before.st_mode)),
                'source_signature': list(signature(before)), 'reused_identical_destination': existed}
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project-root', required=True, type=Path)
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()
    root = args.project_root.absolute()
    dest = root / 'data_5conf'
    assert dest.is_dir() and not dest.is_symlink()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    prov = dest / 'index/provenance' / ('merge_' + stamp)
    prov.mkdir(parents=True, exist_ok=False)
    write_json(prov / 'plan.json', {'started_at': now(), 'sources': SOURCES,
        'input_roots': ['data', 'data_extra'], 'destination': 'data_5conf',
        'policy': 'Copy existing files only; preserve records and scientific judgments; no downloads or model calls.',
        'original_pdf_fallback': 'Read indexed data_full originals for the same frozen paper/version when needed.',
        'anonymization': 'Preserve inherited index status; file existence never upgrades validation status.',
        'review': 'Copy historical review JSON bytes unchanged; inherited effort is not medium.'})
    print(json.dumps({'phase': 'catalog', 'provenance': str(prov)}), flush=True)
    catalog = build_catalog(root, dest, SOURCES, prov)
    records = catalog['records']
    paper_ids = {r['arxiv_id'] for r in records}
    expected = {v['paper_version_id']: v for v in catalog['expected_versions']}
    source_snapshots = list(catalog['source_snapshots'])
    major_docs = {}
    authoritative = collections.defaultdict(list)
    anonymous_linked = {}
    receipts = set()
    for source in SOURCES:
        incoming = []
        origin_records = []
        for origin in ('data', 'data_extra'):
            path = root / origin / 'anonymized/major_imp_index' / (source + '.json')
            payload = path.read_bytes()
            snapshot = prov / 'source_snapshots' / origin / 'anonymized/major_imp_index' / path.name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(payload)
            pin = {'path': str(path.relative_to(root)), 'sha256': hashlib.sha256(payload).hexdigest(),
                   'bytes': len(payload), 'snapshot_path': str(snapshot.relative_to(root))}
            source_snapshots.append(pin)
            document = json.loads(payload)
            if major_docs.get(source) is None:
                major_docs[source] = copy.deepcopy(document)
            origin_records.append({'input': pin, 'original_provenance': document['provenance']})
            for row in document['results']:
                assert row['status'] == 'completed' and row['result']['improvement']['change'] == 'major improvement'
                assert row['labels']['improvement'] == 'major improvement'
                assert row['arxiv_id'] in paper_ids and source in row['sources']
                row = copy.deepcopy(row)
                for side in ('before', 'after'):
                    p = row['pdfs'][side]
                    vid = p['paper_version_id']
                    assert vid in expected
                    authoritative[vid].append((p['original_pdf_path'], p['original_pdf_bytes']))
                    p['original_pdf_path'] = f"data_5conf/pdf/{row['arxiv_id']}/{vid}.pdf"
                    if p['anonymized_pdf_path']:
                        old = p['anonymized_pdf_path']
                        new = f"data_5conf/anonymized/pdf/{source}/{row['arxiv_id']}/{vid}.pdf"
                        anonymous_linked[new] = old
                        p['anonymized_pdf_path'] = new
                    if p['anonymization_report_path']:
                        receipts.add(p['anonymization_report_path'])
                incoming.append(row)
        keyed = {}
        for row in incoming:
            if row['key'] in keyed and keyed[row['key']] != row:
                raise RuntimeError('Conflicting major result: ' + row['key'])
            keyed[row['key']] = row
        document = major_docs[source]
        document['results'] = list(keyed.values())
        vids = {p['paper_version_id'] for row in keyed.values() for p in row['pdfs'].values()}
        document['counts'] = {'pairs': len(keyed), 'unique_papers': len({r['arxiv_id'] for r in keyed.values()})}
        document['pdf_index'].update({'unique_pdf_versions': len(vids),
            'original_pdf_path_template': 'data_5conf/pdf/{arxiv_id}/{arxiv_id}v{version}.pdf',
            'anonymized_pdf_root': 'data_5conf/anonymized/pdf/' + source,
            'anonymized_pdf_path_template': 'data_5conf/anonymized/pdf/' + source + '/{arxiv_id}/{arxiv_id}v{version}.pdf',
            'anonymized_output_policy': 'Preserve inherited validation status and original receipts. Copied bytes and relocation mappings are recorded in merge provenance.'})
        document['provenance'] = {'operation': 'merge_existing_source_indexes_without_reclassification',
            'inputs': origin_records, 'merge_record': str(prov.relative_to(root)),
            'receipt_policy': 'Original project-relative receipt paths and content remain unchanged.'}

    tasks = {}
    missing = []
    alternatives = {}
    def add_task(src, relative, category, **metadata):
        target = dest / relative
        key = str(target)
        item = {'source': Path(src), 'destination': target, 'category': category, **metadata}
        if key in tasks and tasks[key]['source'] != item['source']:
            raise RuntimeError('Multiple source paths for destination: ' + key)
        tasks[key] = item

    for vid, version in expected.items():
        candidates = [root / item[0] for item in authoritative.get(vid, [])]
        candidates += [root / p for p in version['original_candidates']]
        candidates = list(dict.fromkeys(p for p in candidates if p.is_file()))
        target = f"pdf/{version['arxiv_id']}/{vid}.pdf"
        if not candidates:
            missing.append({**version, 'destination_path': 'data_5conf/' + target,
                            'availability': 'missing', 'reason': 'No existing original PDF in scoped source stores or indexed canonical fallback.'})
            continue
        chosen = candidates[0]
        sizes = {size for _, size in authoritative.get(vid, [])}
        if sizes and sizes != {chosen.stat().st_size}:
            raise RuntimeError('Major index original size mismatch: ' + vid)
        add_task(chosen, target, 'original_pdf', paper_version_id=vid)
        alternatives[str(dest / target)] = candidates

    # Preserve all existing scoped source PDFs and ancillary files, including any
    # available version outside the frozen metadata; those are explicitly inventoried.
    for origin in ('data', 'data_extra'):
        for aid in sorted(paper_ids):
            folder = root / origin / 'pdf' / aid
            if folder.is_dir():
                for f in sorted(folder.rglob('*')):
                    if f.is_file():
                        relative = 'pdf/' + aid + '/' + str(f.relative_to(folder))
                        key = str(dest / relative)
                        if key in tasks:
                            alternatives.setdefault(key, []).append(f)
                        else:
                            add_task(f, relative, 'original_additional_file')
        for source in SOURCES:
            folder = root / origin / 'anonymized/pdf' / source
            if folder.is_dir():
                for f in sorted(folder.rglob('*')):
                    if f.is_file() and f.suffix.lower() == '.pdf':
                        rel = f.relative_to(folder)
                        if rel.parts[0] not in paper_ids:
                            raise RuntimeError('Anonymous paper outside catalog: ' + str(f))
                        add_task(f, 'anonymized/pdf/' + source + '/' + str(rel), 'anonymous_pdf')
            folder = root / origin / 'review' / source
            if folder.is_dir():
                for f in sorted(folder.rglob('*')):
                    if f.is_file():
                        add_task(f, 'review/' + source + '/' + str(f.relative_to(folder)), 'historical_review')
    for new, old in anonymous_linked.items():
        assert (root / old).is_file(), 'Missing inherited anonymous PDF: ' + old
        target = str(root / new)
        if target not in tasks:
            add_task(root / old, str(Path(new).relative_to('data_5conf')), 'anonymous_pdf')

    size = sum(t['source'].stat().st_size for t in tasks.values())
    write_json(prov / 'copy_plan.json', {'files': len(tasks), 'bytes': size,
        'categories': dict(collections.Counter(t['category'] for t in tasks.values())),
        'missing_original_version_count': len(missing), 'catalog_stats': catalog['stats']})
    print(json.dumps({'phase': 'copy', 'files': len(tasks), 'bytes': size, 'missing_original_versions': len(missing)}), flush=True)
    copied = []
    def perform(task):
        result = copy_verified(task['source'], task['destination'])
        result.update({k: v for k, v in task.items() if k not in ('source', 'destination')})
        candidate_rows = []
        seen = {(task['source'].stat().st_dev, task['source'].stat().st_ino)}
        for other in dict.fromkeys(alternatives.get(str(task['destination']), [])):
            st = other.stat()
            same_inode = (st.st_dev, st.st_ino) in seen
            if not same_inode and (st.st_size != result['bytes'] or sha(other) != result['sha256']):
                raise RuntimeError('Conflicting original copies: ' + str(other))
            seen.add((st.st_dev, st.st_ino))
            candidate_rows.append({'path': str(other.relative_to(root)), 'same_source_inode': same_inode})
        if candidate_rows:
            result['equivalent_source_candidates'] = candidate_rows
        result['source'] = str(Path(result['source']).relative_to(root))
        result['destination'] = str(Path(result['destination']).relative_to(root))
        return result
    with (prov / 'copied_files.jsonl').open('x') as ledger:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(perform, task) for task in tasks.values()]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                copied.append(result)
                ledger.write(json.dumps(result, ensure_ascii=False) + '\n')
                ledger.flush()
                if len(copied) % 200 == 0:
                    print(json.dumps({'phase': 'copy', 'completed': len(copied), 'total': len(tasks)}), flush=True)

    present = {r['paper_version_id']: r for r in copied if r['category'] == 'original_pdf'}
    with (dest / 'index/versions.jsonl').open('x') as stream:
        for vid, v in expected.items():
            c = present.get(vid)
            row = {**v, 'path': f"data_5conf/pdf/{v['arxiv_id']}/{vid}.pdf",
                   'availability': 'present_hash_verified' if c else 'missing',
                   'sha256': c['sha256'] if c else None, 'bytes': c['bytes'] if c else None,
                   'source_path': c['source'] if c else None,
                   'reason': None if c else 'No existing scoped original file; no download attempted.'}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    write_json(dest / 'index/metadata/missing_original_versions.json', missing)

    receipt_rows = []
    for rel in sorted(receipts):
        p = root / rel
        row = {'path': rel, 'exists': p.is_file(), 'content_verification': 'not_attempted'}
        try:
            raw = p.read_bytes()
            json.loads(raw)
            row.update({'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                        'content_verification': 'readable_json_hash_recorded'})
        except PermissionError:
            row['content_verification'] = 'permission_denied; inherited status retained without revalidation'
        receipt_rows.append(row)
    write_json(prov / 'inherited_receipt_observations.json', receipt_rows)
    for source, document in major_docs.items():
        for row in document['results']:
            for p in row['pdfs'].values():
                assert p['paper_version_id'] in present
                if p['anonymized_pdf_path']:
                    assert (root / p['anonymized_pdf_path']).is_file()
        write_json(dest / 'anonymized/major_imp_index' / (source + '.json'), document)

    source_changed = []
    for pin in source_snapshots:
        p = root / pin['path']
        if sha(p) != pin['sha256']:
            source_changed.append(pin['path'])
    modified_file_observations = []
    for item in copied:
        if list(signature((root / item['source']).stat())) != item['source_signature']:
            modified_file_observations.append(item['source'])
    # Concurrent jobs may publish newer source artifacts. The import preserves
    # the pinned index bytes and each stable copied-file snapshot, not a moving
    # latest view. Record later updates without altering or chasing those jobs.
    write_json(prov / 'post_copy_source_observations.json', {
        'metadata_updated_since_snapshot': source_changed,
        'files_updated_since_stable_copy': modified_file_observations,
        'policy': 'Retain this verified snapshot; concurrent later publications are not silently imported.'})

    # Run the unchanged catalog-only functions without importing the optional
    # PDF parser used by unrelated preparation functions in the same module.
    loader_path = root / 'utility/data/prepare_source_data.py'
    loader_raw = loader_path.read_bytes()
    (prov / 'catalog_loader_source.py').write_bytes(loader_raw)
    tree = ast.parse(loader_raw, filename=str(loader_path))
    names = {'require_source_data', 'validate_identity', 'load_source'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    ns = {'Path': Path, 'json': json, 'hashlib': hashlib, 're': re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(loader_path), 'exec'), ns)
    loaded, _ = ns['load_source'](dest / 'index/index.json')
    assert {r['arxiv_id']: r for r in loaded} == {r['arxiv_id']: r for r in records}
    for source in SOURCES:
        filtered, _ = ns['load_source'](dest / 'index/index.json', [source])
        assert {r['arxiv_id'] for r in filtered} == {r['arxiv_id'] for r in catalog['by_source'][source]}
    summary = {'completed_at': now(), 'sources': list(SOURCES), 'catalog': catalog['stats'],
        'expected_original_versions': len(expected), 'copied_expected_original_pdfs': len(present),
        'missing_original_versions': len(missing), 'copied_file_count': len(copied),
        'copied_bytes': sum(r['bytes'] for r in copied),
        'copied_by_category': dict(collections.Counter(r['category'] for r in copied)),
        'major_by_source': {s: {**d['counts'], 'unique_pdf_versions': d['pdf_index']['unique_pdf_versions']} for s, d in major_docs.items()},
        'inherited_receipts': len(receipt_rows),
        'receipt_content_unreadable': sum('permission_denied' in r['content_verification'] for r in receipt_rows),
        'input_metadata_unchanged_since_snapshot': not source_changed,
        'source_file_signatures_unchanged_since_copy': not modified_file_observations,
        'source_files_modified_by_merge': False,
        'generic_catalog_loader_core_verified': True,
        'loader_verification': {'source_sha256': hashlib.sha256(loader_raw).hexdigest(),
            'functions': sorted(names), 'mode': 'Unchanged AST function bodies; unrelated optional PDF dependency not imported.'},
        'model_calls_performed': 0, 'downloads_performed': 0}
    write_json(prov / 'verification.json', summary)
    doc = f'''# Five-conference experiment data\n\nThis directory is the destination for subsequent five-conference experiments.\nCurrent imported sources: ICLR2026, ICML2026, ACL2026. CVPR2026 and ECCV2026\nremain empty placeholders, not completed zero-paper catalogs.\n\n## Snapshot\n\nMerged {len(records):,} paper records and {len(expected):,} original-version records\nfrom data and data_extra without changing paper metadata or scientific judgments.\nCopied {len(present):,} available original PDFs; {len(missing):,} original-version\nrecords have no available scoped file and remain explicitly missing.\nSee index/versions.jsonl for current paths, availability, and copied-byte hashes.\nHistorical record-level PDF paths are retained provenance, not current input paths.\n\n## Layout\n\n- index/index.json: standalone, hash-pinned source catalog.\n- index/<source>.json: complete original paper metadata and version histories.\n- index/metadata/: current missing-version list and historical observations.\n- pdf/<arxiv_id>/<paper_version_id>.pdf: copied original PDF files.\n- anonymized/major_imp_index/: merged major-improvement judgments and relocated PDF references.\n- anonymized/pdf/<source>/: copied existing anonymous PDFs, including files not yet linked as validated.\n- review/<source>/<model>/: exact historical review JSONs; original settings and embedded references remain unchanged.\n\nHistorical review outputs do not become medium-effort results through copying.\nOriginal anonymity receipts stay under res/anonymization_records, relative to\nthe project root. {summary['receipt_content_unreadable']} linked receipts were not readable by the merging\nOS account; inherited status is preserved, not newly certified. Copied PDFs\nwere independently SHA-256 verified. File existence does not establish anonymity.\n\nMerge provenance and input snapshots: {prov.relative_to(dest)}.\nNo models were called, no PDFs were downloaded, and source files were not edited.\n'''
    (dest / 'README.md').write_text(doc)
    (dest / 'anonymized/README.md').write_text('# Anonymized data\n\nSource-grouped PDF copies and merged major-improvement indexes. Preserve inherited\nvalidation state. Original receipts remain at their project-relative historical\npaths. Unlinked PDF copies are not newly certified. See ../README.md and merge provenance.\n')
    (dest / 'review/README.md').write_text('# Historical review imports\n\nAll imported JSONs preserve exact historical bytes and model/effort settings.\nThey are not new medium-effort reviews. The copy manifest records original paths\nand hashes. Use a new run-specific directory for subsequent experiments to avoid\noverwriting these historical outputs.\n')
    major_dir = dest / 'anonymized/major_imp_index'
    (major_dir / 'README.md').write_text('# Merged major-improvement index\n\nSelection remains completed result.improvement.change == "major improvement".\nNo A1/A2 filtering or judgment changes were applied. PDF references point to\ndata_5conf; original receipt references remain unchanged. Deduplicate pairs by\nkey, papers by arxiv_id, and PDFs by paper_version_id. Source input snapshots and\nPDF hashes are in the merge provenance directory.\n')
    (major_dir / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in sorted(major_dir.iterdir()) if p.name != 'SHA256SUMS'))
    write_json(prov / 'source_snapshots.json', source_snapshots)
    write_json(dest / 'MERGE_COMPLETED.json', {'completed_at': summary['completed_at'],
        'verification_path': str((prov / 'verification.json').relative_to(root)),
        'verification_sha256': sha(prov / 'verification.json'),
        'copy_manifest_path': str((prov / 'copied_files.jsonl').relative_to(root)),
        'copy_manifest_sha256': sha(prov / 'copied_files.jsonl')})
    print(json.dumps({'phase': 'complete', 'summary': summary, 'provenance': str(prov)}), flush=True)


if __name__ == '__main__':
    main()

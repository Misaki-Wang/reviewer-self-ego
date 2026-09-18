#!/usr/bin/env python3
"""Import CVPR/ECCV metadata and existing originals into the five-source archive.

Scientific records and historical judgments are immutable inputs. Availability
is separately inventoried; an unperformed content screen is never a zero result.
"""
import argparse
import ast
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

sys.dont_write_bytecode = True
from catalog_merge import select_versions
from merge_data import copy_verified, sha
from sync_anonymization import atomic_replace

OLD_SOURCES = ('iclr2026', 'icml2026', 'acl2026')
NEW_SOURCES = ('cvpr2026', 'eccv2026')
LOADER_FUNCTIONS = {'require_source_data', 'validate_identity', 'load_source'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def save(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def loader_core(path):
    raw = path.read_bytes()
    tree = ast.parse(raw, filename=str(path))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in LOADER_FUNCTIONS]
    require({node.name for node in nodes} == LOADER_FUNCTIONS, 'Missing catalog loader core')
    namespace = {'Path': Path, 'json': json, 'hashlib': hashlib, 're': re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['load_source'], raw


def stats(records):
    result = {'paper_count': len(records), 'pair_count': 0,
              'original_version_count': 0, 'selected_version_count': 0}
    for paper in records:
        selected, pairs = select_versions(paper)
        require(len(selected) >= 2, 'Paper has no eligible selected pair: ' + paper['arxiv_id'])
        result['pair_count'] += len(pairs)
        result['selected_version_count'] += len(selected)
        result['original_version_count'] += len(paper['versions'])
    return result


def compose_index(previous, old_records, new_records, new_entries, build_pin):
    """Preserve the old file entries/order and all records; reject reruns/overlaps."""
    require(previous.get('dataset') == 'source_data' and 'bases' not in previous,
            'Destination must be a standalone source catalog')
    previous_sources = [entry['source_id'] for entry in previous['files']]
    require(set(previous_sources) == set(OLD_SOURCES) and len(previous_sources) == len(OLD_SOURCES),
            'Destination must contain exactly the original three sources; extension already present or unexpected scope')
    old_ids = [paper['arxiv_id'] for paper in old_records]
    require(previous['record_order'] == old_ids, 'Existing record order differs from loader output')
    old_by_id = {paper['arxiv_id']: paper for paper in old_records}
    require(len(old_by_id) == len(old_records), 'Duplicate old paper identity')
    new_by_id = {}
    for paper in new_records:
        aid = paper['arxiv_id']
        require(aid not in new_by_id, 'Duplicate new paper identity: ' + aid)
        if aid in old_by_id:
            require(old_by_id[aid] == paper, 'Conflicting old/new paper identity: ' + aid)
            raise ValueError('Overlapping old/new paper identity requires an explicit reconciliation: ' + aid)
        new_by_id[aid] = paper
    require([entry['source_id'] for entry in new_entries] == list(NEW_SOURCES), 'Wrong new source entries')
    result = copy.deepcopy(previous)
    result['files'] += copy.deepcopy(new_entries)
    result['record_order'] += list(new_by_id)
    result['dataset_metadata'].update({
        'record_count': len(old_records) + len(new_records),
        'source_ids': previous_sources + list(NEW_SOURCES),
        'build_provenance': build_pin})
    return result


def expected_versions(records):
    result = []
    for paper in records:
        stats([paper])  # Validate complete histories before constructing path identities.
        aid = paper['arxiv_id']
        for version in paper['versions']:
            number = version['version']
            key = f'{aid}v{number}'
            result.append({'arxiv_id': aid, 'version': number, 'paper_version_id': key,
                'source_ids': paper['source_ids'], 'catalog_origin': 'data_full',
                'original_candidates': [f'{origin}/pdf/{aid}/{key}.pdf' for origin in ('data_full', 'data')]})
    require(len({row['paper_version_id'] for row in result}) == len(result), 'Duplicate new version identity')
    return result


def copy_version(root, version, evidence=None):
    """Check all existing copies; never rewrite an existing conflicting target."""
    key = version['paper_version_id']
    if evidence is not None:
        require(evidence.get('requested_id') == key and evidence.get('arxiv_id') == version['arxiv_id']
                and evidence.get('version') == version['version'], 'Acquisition identity mismatch: ' + key)
    candidates = [root / name for name in version['original_candidates'] if (root / name).is_file()]
    target = root / f'data_5conf/pdf/{version["arxiv_id"]}/{key}.pdf'
    if not candidates:
        require(not target.exists(), 'Target has an unbound existing PDF with no source: ' + key)
        official = evidence is not None and evidence.get('status') == 'official_unavailable'
        if official:
            require(evidence.get('canonical_recovery', {}).get('http_status') == 404,
                    'Unavailable acquisition row lacks official HTTP 404 evidence: ' + key)
        return {**version, 'path': str(target.relative_to(root)), 'availability': 'missing',
                'sha256': None, 'bytes': None, 'source_path': None,
                'reason': ('Official version-specific endpoints returned HTTP 404 (source acquisition receipt).'
                           if official else 'No existing scoped original file; no download attempted.'),
                'acquisition_status': evidence.get('status') if evidence else 'unknown',
                'physical_page_count': None,
                'physical_page_count_reason': 'Original PDF unavailable.'}, None
    chosen = candidates[0]
    # Reject conflicting alternate bytes before publishing even an independent copy.
    chosen_stat = chosen.stat()
    chosen_sha = sha(chosen)
    if evidence is not None:
        require(evidence.get('status') == 'verified', 'Existing PDF is not verified by acquisition evidence: ' + key)
        validation = evidence['validation']
        require(chosen_sha == validation['sha256'] and chosen_stat.st_size == validation['bytes'],
                'Original differs from final acquisition receipt: ' + key)
        require(type(validation.get('pages')) is int and validation['pages'] > 0
                and validation.get('page_count_unit') == 'physical_pdf_pages'
                and validation.get('includes_references_and_appendices') is True,
                'Incomplete physical page-count evidence: ' + key)
    seen = {(chosen_stat.st_dev, chosen_stat.st_ino)}
    alternatives = []
    for candidate in candidates:
        st = candidate.stat()
        same_inode = (st.st_dev, st.st_ino) in seen
        if not same_inode:
            require(st.st_size == chosen_stat.st_size and sha(candidate) == chosen_sha,
                    'Conflicting original copies: ' + key)
        seen.add((st.st_dev, st.st_ino))
        alternatives.append({'path': str(candidate.relative_to(root)), 'same_source_inode': same_inode})
    copied = copy_verified(chosen, target)
    require(copied['sha256'] == chosen_sha and copied['bytes'] == chosen_stat.st_size,
            'Source changed during candidate validation: ' + key)
    copied.update({'source': str(chosen.relative_to(root)), 'destination': str(target.relative_to(root)),
                   'category': 'original_pdf', 'paper_version_id': key,
                   'equivalent_source_candidates': alternatives})
    row = {**version, 'path': str(target.relative_to(root)), 'availability': 'present_hash_verified',
           'sha256': copied['sha256'], 'bytes': copied['bytes'],
           'source_path': copied['source'], 'reason': None}
    if evidence is not None:
        row.update({'acquisition_status': evidence['status'],
                    'physical_page_count': validation['pages'], 'physical_page_count_reason': None,
                    'physical_page_count_method': validation['page_count_method'],
                    'page_count_unit': validation['page_count_unit'],
                    'includes_references_and_appendices': True})
    return row, copied


def acquisition_evidence(root, snapshot, expected):
    folder = root / 'experiments/runs/20260918_cvpr_eccv2026_acquisition'
    completion_path = folder / 'COMPLETION.json'
    snapshot(completion_path)
    completion = json.loads(completion_path.read_bytes())
    require(completion.get('status') in ('passed', 'passed_with_official_unavailable'),
            'Source acquisition has not passed final audit')

    def read_pinned(reference, base):
        path = Path(reference['path'])
        if not path.is_absolute():
            path = base / path
        pin = snapshot(path)
        require(pin['sha256'] == reference['sha256'] and pin['bytes'] == reference['bytes'],
                'Acquisition final audit pin mismatch: ' + str(path))
        return path.read_bytes(), pin

    summary_raw, _ = read_pinned(completion['final_audit'], folder)
    summary = json.loads(summary_raw)
    ledger_raw, ledger_pin = read_pinned(summary['version_availability_jsonl'], folder)
    if completion.get('report'):
        read_pinned(completion['report'], folder)
    rows = [json.loads(line) for line in ledger_raw.splitlines() if line.strip()]
    by_key = {row['requested_id']: row for row in rows}
    require(len(by_key) == len(rows) and set(by_key) == {row['paper_version_id'] for row in expected},
            'Final acquisition ledger does not exactly cover imported original versions')
    require(completion['expected_original_versions'] == len(rows), 'Acquisition completion version count differs')
    return by_key, ledger_pin


def extend(project_root, workers=8):
    root = Path(project_root).absolute()
    destination = root / 'data_5conf'
    require(workers > 0, 'Workers must be positive')
    require(destination.is_dir() and not destination.is_symlink(), 'Invalid destination root')
    # Reuse one stable lock location across invocations; original assets are untouched.
    with (destination / 'index/.conference_extension.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _extend(root, destination, workers)


def _extend(root, destination, workers):
    loader_path = root / 'utility/data/prepare_source_data.py'
    load, loader_raw = loader_core(loader_path)
    old_index_path = destination / 'index/index.json'
    old_records, old_loaded = load(old_index_path)
    new_records, new_loaded = load(root / 'data_full/index/index.json', list(NEW_SOURCES))
    old_index_raw = old_index_path.read_bytes()
    previous = json.loads(old_index_raw)
    # Run composition preconditions before creating snapshots or copying PDFs.
    compose_index(previous, old_records, new_records,
                  [{'source_id': source} for source in NEW_SOURCES], {})
    for source in NEW_SOURCES:
        require(not (destination / 'index' / (source + '.json')).exists(),
                'New source shard already exists; inspect any interrupted extension before retrying: ' + source)
    require(not (destination / 'CONFERENCE_EXTENSION_COMPLETED.json').exists(),
            'A conference extension completion marker already exists')
    version_plan = expected_versions(new_records)
    old_versions_path = destination / 'index/versions.jsonl'
    old_versions_raw = old_versions_path.read_bytes()
    require(not old_versions_raw or old_versions_raw.endswith(b'\n'), 'Existing inventory lacks a terminal newline')
    old_versions = [json.loads(line) for line in old_versions_raw.splitlines() if line.strip()]
    old_version_ids = {row['paper_version_id'] for row in old_versions}
    require(len(old_version_ids) == len(old_versions), 'Existing inventory has duplicate versions')
    require(old_version_ids == {row['paper_version_id'] for row in expected_versions(old_records)},
            'Existing version inventory does not cover the old catalog exactly')
    missing_path = destination / 'index/metadata/missing_original_versions.json'
    old_missing_raw = missing_path.read_bytes()
    old_missing = json.loads(old_missing_raw)
    require(isinstance(old_missing, list), 'Expected missing-version list')
    require({row['paper_version_id'] for row in old_missing} ==
            {row['paper_version_id'] for row in old_versions if row['availability'] == 'missing'},
            'Existing missing inventory disagrees with version inventory')
    readme_path = destination / 'README.md'
    old_readme = readme_path.read_bytes()
    guards = {old_index_path: digest(old_index_raw), old_versions_path: digest(old_versions_raw),
              missing_path: digest(old_missing_raw), readme_path: digest(old_readme)}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    provenance = destination / 'index/provenance' / ('conference_extension_' + stamp)
    provenance.mkdir(parents=True, exist_ok=False)
    pins = {}

    def snapshot(path):
        path = Path(path).absolute()
        relative = path.relative_to(root)
        require('..' not in relative.parts, 'Unsafe snapshot path')
        if str(relative) in pins:
            return pins[str(relative)]
        raw = path.read_bytes()
        target = provenance / 'source_snapshots' / relative
        save(target, raw)
        entry = {'path': str(relative), 'sha256': digest(raw), 'bytes': len(raw),
                 'snapshot_path': str(target.relative_to(root))}
        pins[str(relative)] = entry
        return entry

    save(provenance / 'catalog_loader_source.py', loader_raw)
    snapshot(root / 'data_full/README.md')
    for path in guards:
        pin = snapshot(path)
        require(pin['sha256'] == guards[path], 'Destination changed during snapshot: ' + str(path))
    for loaded in (old_loaded, new_loaded):
        for entry in loaded['input_files']:
            pin = snapshot(entry['path'])
            require(pin['sha256'] == entry['sha256'] and pin['bytes'] == entry['bytes'],
                    'Loaded catalog changed before snapshot')
    old_provenance = destination / 'index/provenance/catalog_merge.json'
    if old_provenance.is_file():
        snapshot(old_provenance)
    historical_marker = destination / 'MERGE_COMPLETED.json'
    if historical_marker.is_file():
        snapshot(historical_marker)
    # Retain the exact acquisition metadata and complete version ledger together.
    source_provenance = root / 'data_full/index/provenance'
    if source_provenance.is_dir():
        for path in sorted(source_provenance.rglob('*')):
            if path.is_file() and any(source in path.name for source in NEW_SOURCES):
                snapshot(path)
    for reference in new_loaded['unfiltered_dataset_metadata'].get('catalog_extensions', []):
        if not any(source in reference.get('path', '') for source in NEW_SOURCES):
            continue
        referenced = root / 'data_full/index' / reference['path']
        pin = snapshot(referenced)
        require(pin['sha256'] == reference['sha256'] and pin['bytes'] == reference['bytes'],
                'Catalog extension provenance pin mismatch')
        evidence = json.loads(referenced.read_bytes())
        for name in ('input', 'builder', 'baseline_manifest', 'new_version_metadata'):
            reference = evidence.get(name)
            if not reference:
                continue
            path = Path(reference['path'])
            if not path.is_absolute():
                path = root / 'data_full/index' / path
            pin = snapshot(path)
            require(pin['sha256'] == reference['sha256'] and pin['bytes'] == reference['bytes'],
                    'Acquisition provenance dependency pin mismatch: ' + name)
    evidence_by_key, evidence_pin = acquisition_evidence(root, snapshot, version_plan)
    # Existing major indexes are evidence, not inputs to any new scientific decision.
    for path in sorted((destination / 'anonymized/major_imp_index').glob('*')):
        if path.is_file():
            snapshot(path)
    save(provenance / 'plan.json', encode({'started_at': now(), 'new_source_ids': list(NEW_SOURCES),
        'preserved_source_ids': list(OLD_SOURCES), 'policy': 'Copy existing originals; preserve every scientific record and historical judgment; no downloads or model calls.',
        'content_screening_status': {source: 'not_performed' for source in NEW_SOURCES},
        'expected_new_versions': len(version_plan), 'workers': workers}))
    print(json.dumps({'phase': 'copy', 'expected_new_versions': len(version_plan),
                      'provenance': str(provenance)}), flush=True)
    rows_by_id = {}
    copied = []
    with (provenance / 'copied_files.jsonl').open('x') as manifest:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(copy_version, root, version, evidence_by_key[version['paper_version_id']])
                       for version in version_plan]
            for future in concurrent.futures.as_completed(futures):
                row, receipt = future.result()
                row['acquisition_evidence'] = {'path': evidence_pin['path'], 'sha256': evidence_pin['sha256'],
                                               'requested_id': row['paper_version_id']}
                rows_by_id[row['paper_version_id']] = row
                if receipt:
                    copied.append(receipt)
                    manifest.write(json.dumps(receipt, ensure_ascii=False) + '\n')
                    manifest.flush()
                if len(rows_by_id) % 100 == 0:
                    print(json.dumps({'phase': 'copy', 'completed': len(rows_by_id),
                                      'total': len(version_plan), 'present': len(copied)}), flush=True)
    new_versions = [rows_by_id[row['paper_version_id']] for row in version_plan]
    new_missing = [{**row, 'destination_path': row['path']} for row in new_versions if row['availability'] == 'missing']
    all_records = old_records + new_records
    total_stats = stats(all_records)
    by_source = {source: stats([paper for paper in all_records if source in paper['source_ids']])
                 for source in OLD_SOURCES + NEW_SOURCES}
    for source in NEW_SOURCES:
        source_versions = [row for row in new_versions if source in row['source_ids']]
        by_source[source].update({'available_original_pdfs': sum(row['availability'] == 'present_hash_verified' for row in source_versions),
                                 'missing_original_pdfs': sum(row['availability'] == 'missing' for row in source_versions),
                                 'content_screening_status': 'not_performed'})
    # Recheck frozen source/index bytes before publishing aggregate metadata.
    for pin in pins.values():
        require(sha(root / pin['path']) == pin['sha256'], 'Frozen input changed before publication: ' + pin['path'])
    new_loaded_again, _ = load(root / 'data_full/index/index.json', list(NEW_SOURCES))
    require(new_loaded_again == new_records, 'New source catalog changed while copying')
    for row in new_versions:
        if row['availability'] == 'present_hash_verified':
            path = root / row['path']
            require(path.stat().st_size == row['bytes'] and sha(path) == row['sha256'], 'Copied PDF changed before publication')
    build = {'schema_version': 'source-data-conference-extension-1', 'created_at': now(),
        'source_ids': list(OLD_SOURCES + NEW_SOURCES), 'new_source_ids': list(NEW_SOURCES),
        'previous_index': pins[str(old_index_path.relative_to(root))],
        'input_snapshots': list(pins.values()), 'stats': {'total': total_stats, 'per_source': by_source},
        'records_preserved_without_modification': True,
        'pdf_paths_in_paper_records': 'Unmodified historical evidence; operational paths and availability are in index/versions.jsonl.',
        'content_screening_status': {source: 'not_performed' for source in NEW_SOURCES},
        'major_improvement_indexes': 'Only the three previously imported sources; no CVPR/ECCV judgments are inferred.',
        'model_calls_performed': 0, 'downloads_performed': 0}
    build_path = provenance / 'extension.json'
    build_raw = encode(build)
    save(build_path, build_raw)
    build_pin = {'path': str(build_path.relative_to(destination / 'index')),
                 'sha256': digest(build_raw), 'bytes': len(build_raw)}
    entries = []
    for source in NEW_SOURCES:
        papers = [paper for paper in new_records if source in paper['source_ids']]
        shard = {'dataset': 'source_data', 'schema_version': 'source-data-source-shard-1',
                 'source_id': source, 'record_count': len(papers), 'records': papers,
                 'build_provenance': build_pin}
        raw = encode(shard)
        path = destination / 'index' / (source + '.json')
        require(not path.exists(), 'New source shard already exists: ' + str(path))
        save(path, raw)
        entries.append({'source_id': source, 'path': path.name, 'sha256': digest(raw),
                        'bytes': len(raw), 'record_count': len(papers)})
    index = compose_index(previous, old_records, new_records, entries, build_pin)
    versions_raw = old_versions_raw + b''.join((json.dumps(row, ensure_ascii=False) + '\n').encode() for row in new_versions)
    available = sum(row['availability'] == 'present_hash_verified' for row in old_versions + new_versions)
    missing = old_missing + new_missing
    readme = f'''# Five-conference experiment data

The metadata catalog now contains ICLR2026, ICML2026, ACL2026, CVPR2026,
and ECCV2026: {len(all_records):,} papers, {total_stats['pair_count']:,} selected temporal
pairs, and {total_stats['original_version_count']:,} original PDF versions.
There are {available:,} available original PDFs and {len(missing):,} explicitly missing
versions. All {len(copied):,} originals copied for this extension were SHA-256 verified;
the earlier three-source inventory is retained byte for byte as its original prefix.
The {len(new_missing):,} newly missing versions retain the acquisition audit's official
HTTP 404 evidence. Measured physical page counts are retained in the operational
inventory with their pinned final acquisition receipt.

CVPR2026 and ECCV2026 were imported from the pinned data_full overlay, resolving
its readonly data base shards and disjoint additions. Scientific records, unknown
page counts, and historical PDF paths retain their exact original JSON values.
Use index/versions.jsonl for current operational paths and availability, and
index/metadata/missing_original_versions.json for missing versions.

Major-improvement judgments remain available only for the previously imported
ICLR2026, ICML2026, and ACL2026 sources. CVPR2026 and ECCV2026 have
content_screening_status = not_performed; these are not zero-major results.
No new major index, anonymous PDF, or review output is produced by this import.
Existing anonymized assets, receipts, and historical review outputs are retained.
Historical reviews preserve their original model/effort settings.

The prior merge completion marker and catalog_merge.json remain historical evidence.
Extension provenance: {provenance.relative_to(destination)}.
No source file was modified, no PDF was downloaded, and no model was called.
'''.encode()
    # Guard all existing destinations before any aggregate replacement. Index is
    # the final publication point; individual inventories remain auditable if interrupted.
    for path, before in guards.items():
        require(sha(path) == before, 'Destination changed before publication: ' + str(path))
    atomic_replace(old_versions_path, versions_raw, guards[old_versions_path])
    atomic_replace(missing_path, encode(missing), guards[missing_path])
    atomic_replace(readme_path, readme, guards[readme_path])
    atomic_replace(old_index_path, encode(index), guards[old_index_path])
    loaded_final, _ = load(old_index_path)
    require(loaded_final == all_records, 'Final generic loader did not return exact preserved union')
    require(old_versions_path.read_bytes().startswith(old_versions_raw), 'Old inventory byte prefix changed')
    for source in OLD_SOURCES:
        entry = next(item for item in previous['files'] if item['source_id'] == source)
        require(sha(destination / 'index' / entry['path']) == entry['sha256'], 'Old source shard changed')
    for source in OLD_SOURCES + NEW_SOURCES:
        filtered, _ = load(old_index_path, [source])
        require(filtered == [paper for paper in all_records if source in paper['source_ids']], 'Final source filter differs')
    protected = [pin for pin in pins.values() if pin['path'].startswith('data_5conf/')
                 and root / pin['path'] not in guards]
    for pin in protected:
        require(sha(root / pin['path']) == pin['sha256'], 'Protected previous artifact changed')
    verification = {'completed_at': now(), 'source_ids': list(OLD_SOURCES + NEW_SOURCES),
        'new_source_ids': list(NEW_SOURCES), 'catalog': {'total': total_stats, 'per_source': by_source},
        'new_papers': len(new_records), 'new_expected_original_versions': len(new_versions),
        'new_available_original_pdfs': len(copied), 'new_missing_original_versions': len(new_missing),
        'new_copied_bytes': sum(row['bytes'] for row in copied),
        'physically_new_copies': sum(not row['reused_identical_destination'] for row in copied),
        'expected_original_versions': len(old_versions) + len(new_versions),
        'available_original_pdfs': available, 'missing_original_versions': len(missing),
        'new_missing_version_ids': [row['paper_version_id'] for row in new_missing],
        'generic_catalog_loader_exact_union_verified': True, 'old_version_inventory_exact_prefix_preserved': True,
        'old_source_shards_unchanged': True, 'protected_previous_artifacts_unchanged': True,
        'input_metadata_unchanged_at_publication': True, 'loader_sha256': digest(loader_raw),
        'content_screening_status': {source: 'not_performed' for source in NEW_SOURCES},
        'model_calls_performed': 0, 'downloads_performed': 0,
        'output_pins': [{'path': str(path.relative_to(root)), 'sha256': sha(path), 'bytes': path.stat().st_size}
                        for path in (old_index_path, old_versions_path, missing_path, readme_path)]}
    verification_path = provenance / 'verification.json'
    save(verification_path, encode(verification))
    save(provenance / 'source_snapshots.json', encode(list(pins.values())))
    save(destination / 'CONFERENCE_EXTENSION_COMPLETED.json', encode({
        'completed_at': verification['completed_at'], 'new_source_ids': list(NEW_SOURCES),
        'verification_path': str(verification_path.relative_to(root)), 'verification_sha256': sha(verification_path),
        'copy_manifest_path': str((provenance / 'copied_files.jsonl').relative_to(root)),
        'copy_manifest_sha256': sha(provenance / 'copied_files.jsonl')}))
    print(json.dumps({'phase': 'complete', 'summary': verification, 'provenance': str(provenance)}), flush=True)
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    extend(args.project_root, args.workers)


if __name__ == '__main__':
    main()

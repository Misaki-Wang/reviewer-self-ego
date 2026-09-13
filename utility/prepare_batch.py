#!/usr/bin/env python3
r"""Prepare a fixed MBZ local-Codex batch without launching model calls.

Requires an explicit --source-revision. Verified deployed/Git source bytes,
including the current source-data helpers, are frozen into a fresh run. Existing
server batch/audit modules remain external to the public preparation utility.
PDF and metadata roots can be supplied or taken from project.json. The
project and run controls stay paused until a separately authorized launch.
"""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

if __package__:
    from . import prepare_source_data as preparation
    from . import verify_local_batch as audit
    from . import source_provenance
else:
    import prepare_source_data as preparation
    import verify_local_batch as audit
    import source_provenance


CORE_FILES = source_provenance.CORE_FILES
HELPERS = ('prepare_source_data.py', 'prepare_input_data.py', 'source_provenance.py')
BUNDLE = ('local_batch.py', 'completed_turn.py', 'verify_local_batch.py')
FROZEN_FILES = HELPERS + BUNDLE + ('prepare_batch.py', 'workflow.py', 'token_usage.py')
METADATA = 'data/index/metadata'


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def proof(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    preparation.write_json(path, value)


def save_rows(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows),
                    encoding='utf-8')


def under(project, value):
    path = (project / value).resolve()
    if not path.is_relative_to(project) or path == project:
        raise ValueError(f'Path must be inside --project: {value}')
    return path


def verified_sources(project, code_root, sources, source_revision, metadata_files=()):
    return source_provenance.verified_sources(project, code_root, sources, source_revision,
        extra_files=tuple(f'utility/{name}' for name in FROZEN_FILES) + metadata_files)

def freeze(run, code_root, blobs, deployment, source_revision):
    locations = {}
    for name, raw in blobs.items():
        if name.startswith('utility/'):
            basename = Path(name).name
            folder = ('controller_versions/bundle' if basename in BUNDLE else
                      'source_snapshot/utility' if basename == 'token_usage.py' else 'source_snapshot')
            target = run / folder / basename
        else:
            target = run / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        locations[str(target.relative_to(run))] = (
            deployment[Path(name).stem] if name.startswith('deployment_provenance/') or name == 'source_manifest.json' else
            {'path': str(code_root / name), 'source_revision': source_revision, **proof(target)})
    return locations


def select_scope(code_root, sources, units):
    """Freeze first-N source order, not first-N readable or eligible PDFs."""
    records, memberships, parents = {}, {}, []
    for source in sources:
        if not re.fullmatch(r'[a-z0-9_]+', source):
            raise ValueError(f'Invalid source name: {source}')
        path = code_root / 'data/index' / f'{source}.json'
        rows, provenance = preparation.load_source(path)
        parents.append({'source': source, 'path': str(path), **proof(path), 'record_count': len(rows)})
        if provenance['dataset_metadata'].get('dataset') != 'source_data':
            raise ValueError(f'Expected source_data shard: {source}')
        for record in rows:
            aid = record['arxiv_id']
            if aid in records and records[aid] != record:
                raise ValueError(f'Conflicting original record: {aid}')
            records[aid] = record
            memberships.setdefault(aid, []).append(source)
    chosen = list(records)[:units]
    if len(chosen) != units:
        raise ValueError(f'Expected {units} distinct units; found {len(chosen)}')
    # Fail on an unresolved fixed unit, rather than silently substituting another.
    for aid in chosen:
        selected, _ = preparation.select_versions(records[aid])
        if records[aid]['temporal_eligibility']['status'] != 'eligible' or len(selected) < 3:
            raise ValueError(f'Fixed unit lacks a resolved eligible sequence: {aid}')
    return {'schema_version': 'authorized-source-union-1', 'dataset': 'source_data',
            'record_count': units, 'authorized_sources': sources, 'parent_sources': parents,
            'record_memberships': {aid: memberships[aid] for aid in chosen},
            'records': [records[aid] for aid in chosen]}


def make_plan(scope, scope_path, pdf_root):
    units, requests = [], []
    assignment = {aid: sources[0] for aid, sources in scope['record_memberships'].items()}
    for paper in scope['records']:
        aid = paper['arxiv_id']
        selected, edges = preparation.select_versions(paper)
        versions = []
        for version in selected:
            number = version['version']
            path = preparation.pdf_path(pdf_root, aid, number)
            versions.append({'arxiv_id': aid, 'version': number, 'requested_id': f'{aid}v{number}',
                             'pdf_url': version['pdf_url'], 'submitted_at': version['submitted_at'],
                             'pdf_path': str(path), 'present_at_plan': path.is_file(),
                             'expected_sha256': version.get('pdf', {}).get('sha256') or
                                                version.get('pdf', {}).get('expected_sha256')})
        requests.extend(versions)
        units.append({'arxiv_id': aid, 'assigned_source_id': assignment[aid],
                      'sequence_selection_status': 'resolved',
                      'selected_version_ids': [v['version'] for v in selected],
                      'selected_versions': versions, 'selected_version_count': len(selected),
                      'selected_transitions': edges, 'pair_count': len(edges)})
    missing = [r['requested_id'] for r in requests if not r['present_at_plan']]
    return {'schema_version': preparation.PLAN_SCHEMA_VERSION, 'language': 'en', 'status': 'planned',
            'created_at_utc': now(), 'source_file': str(scope_path),
            'source_sha256': proof(scope_path)['sha256'], 'pdf_root': str(pdf_root),
            'input_files': [{'path': str(scope_path), **proof(scope_path)}],
            'sample_selection': {'method': 'first_n_in_authorized_source_file_order',
                'requested_units': len(units), 'source_assignment': assignment,
                'source_quotas': {s: Counter(assignment.values())[s] for s in scope['authorized_sources']},
                'pdf_availability_affects_selection': False,
                'replacement_policy': 'Never replace fixed units for missing or invalid inputs.'},
            'temporal_rule': {'operator': '>', 'gap_seconds': preparation.GAP_SECONDS,
                'sequence_objective': 'maximum_number_of_selected_original_versions',
                'algorithm': 'earliest_valid_greedy',
                'selected_versions_may_skip_intermediate_versions': True,
                'pair_scope': 'Every adjacent selected-version transition; no other pairs.'},
            'scope': 'All original records and versions retained outside isolated endpoint assessments.',
            'records': scope['records'], 'selected_units': units, 'requested_pdfs': requests,
            'missing_pdf_ids': missing, 'excluded_records': [],
            'summary': {'paper_count': len(units), 'pair_count': sum(u['pair_count'] for u in units),
                        'unique_selected_pdf_count': len(requests),
                        'present_pdf_count_at_plan': len(requests) - len(missing),
                        'missing_pdf_count_at_plan': len(missing)}}


def observe(pdf_root, paper, version, prior, ledger_sha):
    """Recheck current bytes/pages without modifying original metadata or PDFs."""
    aid, number = paper['arxiv_id'], version['version']
    key = f'{aid}v{number}'
    if (prior['arxiv_id'], prior['version'], prior['requested_id'], prior['submitted_at']) != (
            aid, number, key, version['submitted_at']):
        raise ValueError(f'Ledger identity/date mismatch: {key}')
    path = preparation.pdf_path(pdf_root, aid, number)
    result = {'schema_version': 'source-data-observed-original-pdf-metadata-1',
              'arxiv_id': aid, 'version': number, 'requested_id': key,
              'submitted_at': version['submitted_at'], 'observed_at_utc': now(),
              'prior_ledger_sha256': ledger_sha, 'official_no_pdf_evidence': None}
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing regular original PDF in the existing store')
        before = path.stat()
        checked = preparation.validate_pdf(path, aid, version)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('PDF changed during validation')
        if prior['pdf']['availability_status'] == 'verified' and (
                checked['sha256'] != prior['pdf']['sha256'] or checked['bytes'] != prior['pdf']['bytes'] or
                checked['pages'] != prior['page_count']['value']):
            raise ValueError('Current PDF differs from the hash-verified metadata ledger')
        result['pdf'] = {'availability_status': 'verified', 'path': str(path),
                         'sha256': checked['sha256'], 'bytes': checked['bytes'],
                         'sha256_recomputed': True, 'unresolved_reason': None}
        result['page_count'] = {'value': checked['pages'], 'status': 'verified',
            'unit': 'physical_pdf_pages', 'includes_references_and_appendices': True,
            'source_pdf_sha256': checked['sha256'], 'method': 'pypdf.PdfReader.pages',
            'page_count_reason': None, 'unresolved_reason': None}
    except Exception as exc:
        official = not path.exists() and prior['pdf']['availability_status'] == 'official_no_pdf'
        result['official_no_pdf_evidence'] = prior.get('official_no_pdf_evidence') if official else None
        result['pdf'] = {'availability_status': 'official_no_pdf' if official else 'unresolved',
                         'path': str(path), 'sha256': None, 'bytes': None, 'unresolved_reason': str(exc)}
        result['page_count'] = {'value': None, 'status': 'unresolved', 'unit': 'physical_pdf_pages',
            'includes_references_and_appendices': True, 'source_pdf_sha256': None,
            'page_count_reason': str(exc), 'unresolved_reason': str(exc)}
    return result


def prepare(args):
    project = args.project.expanduser().resolve()
    code_root, run = project, under(project, args.run)
    active_path = project / 'utility/project.json'
    active = read(active_path)
    defaults = active['comparison']
    for name, key in (('model', 'model'), ('effort', 'reasoning_effort'), ('workers', 'workers'),
                      ('timeout', 'timeout_seconds'), ('validation_workers', 'validation_workers')):
        if getattr(args, name, None) is None:
            setattr(args, name, defaults[key])
    runs_root = under(project, getattr(args, 'runs_root', None) or Path(active['outputs']['runs_root']))
    if not runs_root.is_relative_to(project / 'res') or any(
            runs_root.is_relative_to(project / 'res' / name) or run.is_relative_to(project / 'res' / name)
            for name in ('archive', '.migration')):
        raise ValueError('New runs must remain under res, outside archive and migration storage')
    ladder = getattr(args, 'concurrency_ladder', None)
    validation_workers = getattr(args, 'validation_workers', 4)
    if (type(args.units) is not int or args.units < 1 or
            type(args.workers) is not int or not 1 <= args.workers <= 96 or
            type(args.timeout) is not int or args.timeout < 1 or
            type(validation_workers) is not int or not 1 <= validation_workers <= 16):
        raise ValueError('Require positive integer units/timeout, 1..96 comparison workers, and 1..16 validation workers')
    if ladder is not None and (not isinstance(ladder, list) or
            any(type(n) is not int for n in ladder) or
            ladder not in ([64, 48, 32, 16], [96, 80, 64, 48]) or args.workers != ladder[0]):
        raise ValueError('--concurrency-ladder requires 64 48 32 16 or 96 80 64 48, with --workers matching its first value')
    if run == runs_root or not run.is_relative_to(runs_root):
        raise ValueError(f'--run must be a fresh directory below the configured runs root: {runs_root}')
    if run.exists():
        raise ValueError('Run already exists; use a fresh --run without overwriting evidence')
    if not args.source:
        raise ValueError('At least one explicit source is required')
    sources = list(dict.fromkeys(args.source))
    source_revision = getattr(args, 'source_revision', None)
    if not isinstance(source_revision, str) or not re.fullmatch(r'[0-9a-f]{64}', source_revision):
        raise ValueError('An explicit immutable --source-revision (64 hexadecimal characters) is required')
    metadata = under(project, getattr(args, 'metadata_dir', None) or Path(active['data']['metadata_root']))
    pdf_root = under(project, getattr(args, 'pdf_root', None) or Path(active['data']['pdf_root']))
    metadata_files = tuple(str((metadata / name).relative_to(project))
                           for name in ('version_metadata.jsonl', 'provenance.json'))
    blobs, deployment = verified_sources(project, code_root, sources, source_revision, metadata_files)
    helper_paths = {'prepare_source_data.py': Path(preparation.__file__),
                    'prepare_input_data.py': Path(preparation.materialize.__code__.co_filename),
                    'source_provenance.py': Path(source_provenance.__file__),
                    'prepare_batch.py': Path(__file__),
                    'verify_local_batch.py': Path(audit.__file__),
                    'completed_turn.py': Path(audit.recovery.__file__)}
    for name, path in helper_paths.items():
        if path.read_bytes() != blobs[f'utility/{name}']:
            raise ValueError(f'Loaded preparation helper differs from the verified source: {name}')
    scope = select_scope(code_root, sources, args.units)
    # Compare whole shard bytes, not just IDs, to the verified canonical inputs.
    for parent in scope['parent_sources']:
        raw = blobs[f"data/index/{parent['source']}.json"]
        if (parent['bytes'], parent['sha256']) != (len(raw), hashlib.sha256(raw).hexdigest()):
            raise ValueError(f"Scope source differs from canonical shard: {parent['source']}")
    for source_root in (project / 'check_diff', project / 'utility', project / 'review_experiment', project / 'data/index', metadata, pdf_root):
        if runs_root.is_relative_to(source_root) or source_root.is_relative_to(runs_root):
            raise ValueError(f'Run output root overlaps source inputs: {source_root}')
    ledger_path, provenance_path = metadata / 'version_metadata.jsonl', metadata / 'provenance.json'
    ledger_proof = proof(ledger_path)
    provenance = read(provenance_path)
    if ledger_proof != provenance['files']['version_metadata.jsonl']:
        raise ValueError('Metadata ledger hash/size differs from provenance.json')
    needed = {f"{p['arxiv_id']}v{v['version']}" for p in scope['records'] for v in p['versions']}
    ledger = {}
    with ledger_path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            key = row['requested_id']
            if key in needed:
                if key in ledger:
                    raise ValueError(f'Duplicate selected-unit ledger version: {key}')
                ledger[key] = row
    if set(ledger) != needed:
        raise ValueError(f'Missing original-version ledger rows: {sorted(needed - set(ledger))}')
    run.mkdir(parents=True, exist_ok=False)
    save(run / 'control.json', {'workers': args.workers, 'auto_scale': ladder is not None,
                               'pause_dispatch': True, 'pause_reason': 'awaiting_authorized_leader_launch'})
    locations = freeze(run, code_root, blobs, deployment, source_revision)
    save(run / 'scope_input.json', scope)
    prepared = run / 'prepared'
    plan = make_plan(scope, run / 'scope_input.json', pdf_root)
    save(prepared / 'selection_manifest.json', plan)
    selected_ids = {r['requested_id'] for r in plan['requested_pdfs']}

    def inventory_row(item):
        paper, version = item
        key = f"{paper['arxiv_id']}v{version['version']}"
        return {'arxiv_id': paper['arxiv_id'], 'version': version['version'],
            'requested_id': key, 'selected_for_comparison': key in selected_ids,
            'frozen_version_metadata': version,
            'pdf_observation': observe(pdf_root, paper, version, ledger[key], ledger_proof['sha256'])}

    with ThreadPoolExecutor(max_workers=validation_workers) as validators:
        inventory = list(validators.map(inventory_row,
            ((paper, version) for paper in scope['records'] for version in paper['versions'])))
    save_rows(run / 'version_inventory.jsonl', inventory)
    save_rows(run / 'provenance_version_metadata.jsonl', list(ledger.values()))
    (run / 'metadata_provenance.json').write_bytes(provenance_path.read_bytes())
    config = {'run_id': run.name, 'created_at_utc': now(), 'source_names': sources,
        'unit_count': args.units, 'unit_ids': [p['arxiv_id'] for p in scope['records']],
        'model': args.model, 'reasoning_effort': args.effort, 'initial_workers': args.workers,
        'max_workers': args.workers, 'timeout_seconds': args.timeout, 'max_attempts': 2,
        'validation_workers': validation_workers,
        'retry_base_seconds': 30, 'prepared_path': str(prepared), 'pdf_root': str(pdf_root),
        'metadata_ledger': str(run / 'provenance_version_metadata.jsonl'),
        'source_revision': source_revision, 'previous_judgments_reused': False,
        'codex_path': str(getattr(args, 'codex', None) or active['runtime']['codex']), 'transport': 'default Codex WebSocket; native fallback policy unchanged'}
    if ladder is not None:
        config['concurrency_ladder'] = list(ladder)
    save(run / 'run_config.json', config)
    sig = audit.signature(config, audit.Reader(), run)
    save(run / 'execution_contract.json', {'execution_signature': sig, 'requested_model': args.model,
        'requested_reasoning_effort': args.effort, 'unit_count': args.units,
        'comparison_count': plan['summary']['pair_count'],
        'source_membership_counts': {s: sum(s in ms for ms in scope['record_memberships'].values())
                                     for s in sources}})
    unavailable = {r['requested_id'] for r in inventory
                   if r['pdf_observation']['pdf']['availability_status'] == 'official_no_pdf'}
    states = []
    for unit in plan['selected_units']:
        aid = unit['arxiv_id']
        for edge in unit['selected_transitions']:
            before, after = edge['from_version'], edge['to_version']
            ids = [f'{aid}v{before}', f'{aid}v{after}']
            states.append({'key': f'{aid}__v{before}_v{after}', 'arxiv_id': aid,
                'before': before, 'after': after, 'pdf_ids': ids, 'gap_seconds': edge['gap_seconds'],
                'status': 'input_unavailable' if unavailable.intersection(ids) else 'pending', 'attempt_count': 0})
    save_rows(run / 'pair_status.jsonl', states)
    save(run / 'transfer_progress.json', {'status': 'completed', 'mode': 'existing_store',
        'completed_at_utc': now(), 'pdf_root': str(pdf_root), 'downloads_performed': False})
    save(run / 'token_usage.json', {'run_id': run.name, 'active_attempt_count': 0,
                                  'token_usage': audit.summarize([])})
    verification = audit.Verifier(run).verify()
    save(run / 'preparation_verification.json', verification)
    inputs = scope['parent_sources'] + [
        {'path': str(ledger_path), **ledger_proof}, {'path': str(provenance_path), **proof(provenance_path)}]
    inputs.extend(deployment.values())
    for item in inputs + list(locations.values()):
        if proof(Path(item['path'])) != {k: item[k] for k in ('bytes', 'sha256')}:
            raise ValueError(f'Preparation source changed during freezing: {item["path"]}')
    manifest = {'schema_version': 'mbz-local-codex-preparation-1', 'created_at_utc': now(),
        'status': 'blocked' if verification['errors'] else 'prepared',
        'model_execution_performed': False, 'downloads_performed': False, 'resampling_performed': False,
        'source_revision': source_revision, 'source_file_locations': locations, 'input_files': inputs,
        'deployment_provenance': deployment,
        'execution_signature': sig, 'summary': plan['summary'],
        'controller': 'controller_versions/bundle/local_batch.py',
        'independent_verifier': 'controller_versions/bundle/verify_local_batch.py',
        'launch_note': 'Leader supplies target Python/dependencies, Codex/auth and PDF skill; '
                       'only the leader may authorize and clear pause_dispatch before launch.',
        'artifact_sha256': {str(p.relative_to(run)): proof(p)['sha256']
                            for p in sorted(run.rglob('*')) if p.is_file()}}
    save(run / 'execution_manifest.json', manifest)
    if verification['errors']:
        raise ValueError('Fixed scope preserved but preparation audit failed; see preparation_verification.json')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--runs-root', type=Path, help='Owned storage below res (default: utility/project.json)')
    parser.add_argument('--codex', help='Exact Codex executable to freeze in the run configuration')
    parser.add_argument('--source-revision', required=True, help='Exact authorized source-manifest revision (64 hexadecimal characters)')
    parser.add_argument('--pdf-root', type=Path, help='Existing version-specific PDF store, inside --project')
    parser.add_argument('--metadata-dir', type=Path, help='Verified version_metadata.jsonl and provenance.json directory')
    parser.add_argument('--source', action='append', required=True)
    parser.add_argument('--units', type=int, required=True)
    parser.add_argument('--model', default=None)
    parser.add_argument('--effort', default=None)
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--concurrency-ladder', type=int, nargs='+',
                        help='Opt into downward-only 64 48 32 16 or 96 80 64 48 admissions; --workers must match the first value')
    parser.add_argument('--validation-workers', type=int, default=None,
                        help='Parallel original-PDF checks during preparation and execution (1..16; default: 4)')
    parser.add_argument('--timeout', type=int, default=None)
    args = parser.parse_args(argv)
    try:
        result = prepare(args)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Preparation failed: {exc}\n')
    print(json.dumps({'status': result['status'], **result['summary'], 'model_execution_performed': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan and materialize a source-stratified v1.3 maximum-sequence pilot.

This adapter preserves the legacy input folder layout without changing the
v1.1 preparation utility or mutating any frozen v1.3 paper metadata.
With --units, source_quotas records the actual balanced paper counts and
source_assignment maps each distinct arXiv ID to exactly one quota source.
Each paper contributes all adjacent transitions in its maximum-cardinality
selected sequence. Planning samples metadata independently of PDF availability;
materialization verifies that fixed plan without replacement or resampling.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re


if __package__:
    from .prepare_input_data import materialize, validate_pdf as validate_original_pdf, write_json
else:
    from prepare_input_data import materialize, validate_pdf as validate_original_pdf, write_json


GAP_SECONDS = 2592000


def load_source(path):
    """Read the combined release or hash-verified, deduplicated source shards."""
    path = path.resolve()
    raw = path.read_bytes()
    source = json.loads(raw)
    inputs = [{'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]
    if 'records' in source:
        records = source['records']
        metadata = {k: v for k, v in source.items() if k != 'records'}
    else:
        records = []
        metadata = source['dataset_metadata']
        for entry in source['files']:
            shard_path = (path.parent / entry['path']).resolve()
            if not shard_path.is_relative_to(path.parent):
                raise ValueError(f'Shard path escapes the index directory: {entry["path"]}')
            payload = shard_path.read_bytes()
            sha = hashlib.sha256(payload).hexdigest()
            if sha != entry['sha256'] or len(payload) != entry['bytes']:
                raise ValueError(f'Shard checksum mismatch: {shard_path.name}')
            shard = json.loads(payload)
            if len(shard['records']) != entry['record_count']:
                raise ValueError(f'Shard record count mismatch: {shard_path.name}')
            records.extend(shard['records'])
            inputs.append({'path': str(shard_path), 'bytes': len(payload), 'sha256': sha})
    by_id = {}
    for record in records:
        aid = record['arxiv_id']
        if not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
            raise ValueError(f'Unsupported arXiv ID: {aid}')
        if aid in by_id and by_id[aid] != record:
            raise ValueError(f'Conflicting duplicate paper metadata: {aid}')
        by_id[aid] = record
    if 'record_order' in source:
        order = source['record_order']
        if len(order) != len(set(order)) or set(order) != set(by_id):
            raise ValueError('Index record_order does not match the deduplicated records')
        ordered = [by_id[aid] for aid in order]
    else:
        ordered = list(by_id.values())
    if metadata.get('record_count', len(ordered)) != len(ordered):
        raise ValueError('Dataset record_count does not match deduplicated records')
    return ordered, {'source_file': str(path), 'source_sha256': inputs[0]['sha256'],
                     'input_files': inputs, 'dataset_metadata': metadata,
                     'duplicate_memberships_collapsed': len(records) - len(ordered)}


def parse_timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Missing or invalid timestamp; full-history sequence selection is unresolved')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid timestamp; full-history sequence selection is unresolved') from exc
    if result.tzinfo is None:
        raise ValueError('Timestamp lacks a timezone; full-history sequence selection is unresolved')
    return result.astimezone(timezone.utc)


def select_versions(paper):
    """Earliest-valid greedy maximizes cardinality under a fixed strict gap.

Choosing the earliest feasible next timestamp never removes an option that a
later choice would leave available. Ties use original version IDs. Missing or
invalid full-history timestamps leave selection unresolved, without changing
the frozen paper-level temporal eligibility or its historical proof witness.
"""
    versions = paper['versions']
    numbers = [v['version'] for v in versions]
    if (any(type(v) is not int or v < 1 for v in numbers)
            or sorted(numbers) != list(range(1, paper['frozen_N'] + 1))):
        raise ValueError('Incomplete or invalid original history; sequence selection is unresolved')
    dated = sorted(((parse_timestamp(v.get('submitted_at')), v['version'], v) for v in versions),
                   key=lambda item: (item[0], item[1]))
    if [number for _, number, _ in dated] != sorted(numbers):
        raise ValueError('Timestamps conflict with original version order; sequence selection is unresolved')
    chosen = []
    for date, number, version in dated:
        if not chosen or (date - chosen[-1][0]).total_seconds() > GAP_SECONDS:
            chosen.append((date, number, version))
    selected = [version for _, _, version in chosen]
    edges = []
    for before, after in zip(chosen, chosen[1:]):
        gap = (after[0] - before[0]).total_seconds()
        edges.append({'from_version': before[1], 'to_version': after[1],
                      'from_submitted_at': before[2]['submitted_at'],
                      'to_submitted_at': after[2]['submitted_at'],
                      'gap_seconds': gap, 'gap_days': gap / 86400})
    return selected, edges


def pdf_path(pdf_root, aid, version):
    return pdf_root / aid / f'{aid}v{version}.pdf'


def validate_pdf(path, aid, version):
    """Check the current file, independent of frozen download/page statuses."""
    info = validate_original_pdf(path, aid, version['version'])
    sha = info['sha256']
    frozen = version.get('pdf', {})
    expected = {v for v in (frozen.get('sha256'), frozen.get('expected_sha256'),
                           version.get('page_count', {}).get('source_pdf_sha256')) if v}
    if expected and expected != {sha}:
        raise ValueError('Current PDF does not match the frozen version-specific SHA-256 evidence')
    return {**info, 'status': 'verified', 'path': str(path.resolve()),
            'page_count_unit': 'physical_pdf_pages',
            'includes_references_and_appendices': True, 'page_count_method': 'pypdf.PdfReader.pages',
            'frozen_sha256_verified': bool(expected),
            'verified_at_utc': datetime.now(timezone.utc).isoformat()}


def assign_sources(pools, per_source, seed, units=None):
    """Deterministic bipartite assignment avoids duplicate papers across sources."""
    shuffled = {}
    rng = random.Random(seed)
    for source in sorted(pools):
        shuffled[source] = sorted(pools[source])
        rng.shuffle(shuffled[source])
    paper_slot = {}

    def augment(slot, seen):
        for aid in shuffled[slot[0]]:
            if aid in seen:
                continue
            seen.add(aid)
            if aid not in paper_slot or augment(paper_slot[aid], seen):
                paper_slot[aid] = slot
                return True
        return False

    if units is None:
        for source in sorted(pools, key=lambda s: (len(pools[s]), s)):
            for i in range(per_source):
                if not augment((source, i), set()):
                    sizes = {s: len(pools[s]) for s in sorted(pools)}
                    raise ValueError(f'Cannot assign {per_source} distinct papers per source; available pools: {sizes}')
    else:
        available = len({aid for pool in pools.values() for aid in pool})
        if not 0 < units <= available:
            raise ValueError(f'Cannot assign {units} distinct paper units; {available} available')
        quotas, saturated = Counter(), set()
        while len(paper_slot) < units:
            source = min(set(pools) - saturated, key=lambda s: (quotas[s], s))
            if augment((source, quotas[source]), set()):
                quotas[source] += 1
            else:
                saturated.add(source)
    return {aid: slot[0] for aid, slot in sorted(paper_slot.items())}


def build_plan(args):
    records, provenance = load_source(args.source)
    units = getattr(args, 'units', None)
    pdf_root = args.pdf_root.resolve()
    sources = sorted({s for p in records for s in p['source_ids']})
    counts = {s: Counter(candidate_memberships=0, metadata_temporally_eligible=0,
                         sequence_selection_resolved=0, sequence_selection_unresolved=0,
                         all_selected_pdf_files_present=0, missing_selected_pdf_files=0)
              for s in sources}
    pools, candidates, exclusions = {s: [] for s in sources}, {}, []
    for paper in records:
        aid = paper['arxiv_id']
        for source in paper['source_ids']:
            counts[source]['candidate_memberships'] += 1
        if paper['temporal_eligibility']['status'] != 'eligible':
            exclusions.append({'arxiv_id': aid, 'source_ids': paper['source_ids'],
                               'reason': 'not_frozen_temporally_eligible'})
            continue
        for source in paper['source_ids']:
            counts[source]['metadata_temporally_eligible'] += 1
        try:
            selected, edges = select_versions(paper)
            if len(selected) < 3:
                raise ValueError('Full-history timestamps contradict the frozen eligible status')
        except ValueError as exc:
            exclusions.append({'arxiv_id': aid, 'source_ids': paper['source_ids'],
                               'reason': 'full_history_sequence_selection_unresolved', 'detail': str(exc),
                               'frozen_temporal_eligibility': paper['temporal_eligibility']})
            for source in paper['source_ids']:
                counts[source]['sequence_selection_unresolved'] += 1
            continue
        missing = [v['version'] for v in selected
                   if not pdf_path(pdf_root, aid, v['version']).is_file()]
        for source in paper['source_ids']:
            counts[source]['sequence_selection_resolved'] += 1
            counts[source]['missing_selected_pdf_files' if missing else 'all_selected_pdf_files_present'] += 1
            pools[source].append(aid)
        candidates[aid] = (paper, selected, edges)
    assignment = assign_sources(pools, args.per_source, args.seed, units=units)
    selected_units, requests = [], []
    for aid, source in assignment.items():
        paper, selected, edges = candidates[aid]
        selected_metadata = []
        for version in selected:
            number = version['version']
            path = pdf_path(pdf_root, aid, number)
            requested = {'arxiv_id': aid, 'version': number, 'requested_id': f'{aid}v{number}',
                         'pdf_url': version['pdf_url'], 'submitted_at': version['submitted_at'],
                         'pdf_path': str(path), 'present_at_plan': path.is_file(),
                         'expected_sha256': version.get('pdf', {}).get('sha256') or
                                            version.get('pdf', {}).get('expected_sha256')}
            requests.append(requested)
            selected_metadata.append(requested)
        selected_units.append({'arxiv_id': aid, 'assigned_source_id': source,
                               'sequence_selection_status': 'resolved',
                               'selected_version_ids': [v['version'] for v in selected],
                               'selected_versions': selected_metadata,
                               'selected_version_count': len(selected),
                               'selected_transitions': edges, 'pair_count': len(edges)})
    missing = [entry['requested_id'] for entry in requests if not entry['present_at_plan']]
    plan = {'schema_version': 'v1.3-maximum-sequence-pilot-2', 'language': 'en', 'status': 'planned',
            'created_at_utc': datetime.now(timezone.utc).isoformat(), **provenance,
            'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'pdf_root': str(pdf_root),
            'sample_selection': {'method': 'source_stratified_seeded_bipartite_assignment',
                                 'seed': args.seed, 'papers_per_source': args.per_source if units is None else None,
                                 'requested_units': units,
                                 'source_quotas': {s: list(assignment.values()).count(s) for s in sources},
                                 'source_assignment': assignment,
                                 'source_assignment_note': 'Each arXiv ID has one quota source; all original source memberships remain unchanged.',
                                 'quota_policy': 'Balance distinct paper units across metadata source pools; cap scarce sources and redistribute remaining slots deterministically.',
                                 'sampling_population': 'All frozen temporally eligible papers with resolved full-history sequence selection.',
                                 'pdf_availability_affects_selection': False,
                                 'replacement_policy': 'Never replace selected papers because PDFs are missing or invalid.'},
            'temporal_rule': {'operator': '>', 'gap_seconds': GAP_SECONDS,
                              'sequence_objective': 'maximum_number_of_selected_original_versions',
                              'algorithm': 'earliest_valid_greedy',
                              'tie_break': 'earliest UTC timestamp, then original version ID',
                              'selected_versions_may_skip_intermediate_versions': True,
                              'history_scope': 'Every original version through frozen_N; historical witness_versions is not a selection cap.',
                              'pair_scope': 'Every adjacent selected-version transition; no other pairs.'},
            'scope': 'Full original paper metadata retained outside pair folders; selected-sequence PDFs only.',
            'source_availability': {s: dict(counts[s]) for s in sources},
            'availability_count_note': 'File presence only, observed during planning; no PDF parsing, hashing or availability filtering was performed.',
            'excluded_records': exclusions, 'records': [candidates[aid][0] for aid in assignment],
            'selected_units': selected_units, 'requested_pdfs': requests,
            'missing_pdf_ids': missing,
            'summary': {'paper_count': len(assignment),
                        'pair_count': sum(unit['pair_count'] for unit in selected_units),
                        'unique_selected_pdf_count': len(requests),
                        'present_pdf_count_at_plan': len(requests) - len(missing),
                        'missing_pdf_count_at_plan': len(missing)}}
    return plan


def materialize_plan(manifest_path, output=None, pdf_root=None):
    """Verify and materialize the fixed selected paper IDs; never resample."""
    manifest_path = manifest_path.resolve()
    raw = manifest_path.read_bytes()
    plan = json.loads(raw)
    if plan['schema_version'] != 'v1.3-maximum-sequence-pilot-2':
        raise ValueError('Unsupported plan schema; historical witness experiments must remain unchanged')
    out = output.resolve() if output else manifest_path.parent
    root = pdf_root.resolve() if pdf_root else Path(plan['pdf_root'])
    papers = {p['arxiv_id']: p for p in plan['records']}
    if len(papers) != len(plan['records']) or set(papers) != set(plan['sample_selection']['source_assignment']):
        raise ValueError('Plan paper identities do not match the fixed source assignment')
    if len(plan['selected_units']) != len(papers) or {u['arxiv_id'] for u in plan['selected_units']} != set(papers):
        raise ValueError('Plan selected units do not match its paper identities')
    out.mkdir(parents=True, exist_ok=True)
    local_manifest = out / 'selection_manifest.json'
    if local_manifest.resolve() != manifest_path:
        if local_manifest.exists() and local_manifest.read_bytes() != raw:
            raise ValueError('Output already contains a different fixed selection plan')
        if not local_manifest.exists() and any(out.iterdir()):
            raise ValueError('Output is not empty and has no matching selection plan')
        local_manifest.write_bytes(raw)
    verified, failures = {}, []
    for checked, unit in enumerate(plan['selected_units'], 1):
        aid = unit['arxiv_id']
        selected, edges = select_versions(papers[aid])
        if ([v['version'] for v in selected] != unit['selected_version_ids'] or
                edges != unit['selected_transitions']):
            raise ValueError(f'Fixed sequence plan disagrees with the original history: {aid}')
        for version in selected:
            key = f"{aid}v{version['version']}"
            try:
                verified[key] = validate_pdf(pdf_path(root, aid, version['version']), aid, version)
            except Exception as exc:
                failures.append({'arxiv_id': aid, 'version': version['version'],
                                 'requested_id': key, 'reason': 'selected_pdf_unavailable_or_invalid',
                                 'detail': str(exc)})
        if checked % 50 == 0:
            print(f'PDF validation: {checked} fixed paper units checked; '
                  f'{len(verified)} version PDFs verified; {len(failures)} unresolved.', flush=True)
    receipt = {'schema_version': 'v1.3-pilot-materialization-1', 'language': 'en',
               'selection_manifest_sha256': hashlib.sha256(raw).hexdigest(),
               'created_at_utc': datetime.now(timezone.utc).isoformat(),
               'pdf_root': str(root), 'status': 'unresolved' if failures else 'verified',
               'verified_pdfs': verified, 'failures': failures,
               'sample_unchanged': True, 'resampling_performed': False}
    write_json(out / 'materialization_manifest.json', receipt)
    if failures:
        raise ValueError(f'{len(failures)} selected PDFs remain unavailable or invalid; fixed paper IDs preserved. See materialization_manifest.json.')
    pairs, rows = [], []
    for unit in plan['selected_units']:
        aid, source = unit['arxiv_id'], unit['assigned_source_id']
        paper = papers[aid]
        versions = {v['version']: v for v in paper['versions']}
        folder = out / aid
        folder.mkdir(exist_ok=True)
        write_json(folder / 'metadata.json', paper)
        for edge in unit['selected_transitions']:
            b, a = edge['from_version'], edge['to_version']
            pair_folder = folder / f'v{b}_v{a}'
            pair_folder.mkdir(exist_ok=True)
            pair = {'pair_id': f'{aid}__v{b}_to_v{a}', 'arxiv_id': aid,
                    'arxiv_url': paper['arxiv_url'], 'title': paper['title'],
                    'venue': source, 'assigned_source_id': source, 'source_ids': paper['source_ids'],
                    'source_entries': paper['source_entries'],
                    'pair_type': 'adjacent_selected_maximum_sequence_versions',
                    'path_base': 'input_data', 'paper_metadata_path': f'{aid}/metadata.json',
                    **edge, 'selected_version_ids': unit['selected_version_ids']}
            for side, number in (('before', b), ('after', a)):
                version = versions[number]
                evidence = verified[f'{aid}v{number}']
                target = pair_folder / f'v{number}.pdf'
                materialize(Path(evidence['path']), target, evidence['sha256'])
                pair[side] = {**version, 'pdf_path': str(target.relative_to(out)),
                              'sha256': evidence['sha256'], 'pages': evidence['pages'],
                              'current_pdf_validation': evidence}
            write_json(pair_folder / 'pair.json', pair)
            pairs.append(pair)
        rows.append({'arxiv_id': aid, 'venue': source, 'title': paper['title'],
                     'version_count': len(paper['versions']),
                     'selected_version_count': unit['selected_version_count'],
                     'pair_count': unit['pair_count']})
    if len(pairs) != plan['summary']['pair_count'] or len(verified) != plan['summary']['unique_selected_pdf_count']:
        raise ValueError('Materialized counts disagree with the fixed plan')
    write_json(out / 'pairs.json', {'schema_version': '2.0', 'path_base': '.',
                                   'pairing_rule': plan['temporal_rule'],
                                   'summary': plan['summary'], 'papers': rows, 'pairs': pairs})
    return plan


def prepare(args):
    plan = build_plan(args)
    print(json.dumps({'summary': plan['summary'],
                      'source_quotas': plan['sample_selection']['source_quotas']}), flush=True)
    if args.dry_run:
        return plan
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('Output is not empty; materialize the existing plan or choose a new output directory')
    manifest = out / 'selection_manifest.json'
    write_json(manifest, plan)
    if not getattr(args, 'plan_only', False):
        materialize_plan(manifest)
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--pdf-root', type=Path,
                        help='Existing data/arxiv_pdf directory containing <id>/<id>vN.pdf.')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--per-source', type=int, default=5)
    parser.add_argument('--units', type=int,
                        help='Total distinct paper units; source-balanced metadata sampling. Overrides --per-source.')
    parser.add_argument('--seed', type=int, default=20260911)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--plan-only', action='store_true', help='Freeze paper IDs and exact PDF requests without validation or materialization.')
    parser.add_argument('--materialize-plan', type=Path, help='Materialize a fixed selection_manifest.json without resampling.')
    args = parser.parse_args()
    if args.per_source < 1 or (args.units is not None and args.units < 1):
        parser.error('--per-source and --units must be positive')
    if args.materialize_plan:
        if args.plan_only or args.dry_run or args.source:
            parser.error('--materialize-plan cannot be combined with --source, --plan-only or --dry-run')
    elif not all((args.source, args.pdf_root, args.output)):
        parser.error('Planning requires --source, --pdf-root and --output')
    try:
        if args.materialize_plan:
            materialize_plan(args.materialize_plan, args.output, args.pdf_root)
        else:
            prepare(args)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Preparation failed: {exc}\n')


if __name__ == '__main__':
    main()

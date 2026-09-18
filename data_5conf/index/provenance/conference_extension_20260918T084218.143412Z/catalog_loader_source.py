#!/usr/bin/env python3
"""Plan and materialize a source-stratified source_data maximum-sequence pilot.

This adapter prepares the formal source_data inventory without mutating any
original paper/version metadata. Historical release packaging remains provenance.
With --units, source_quotas records the actual balanced paper counts and
source_assignment maps each distinct arXiv ID to exactly one quota source.
Each paper contributes all adjacent transitions in its maximum-cardinality
selected sequence. Planning samples metadata independently of PDF availability;
materialization verifies that fixed plan without replacement or resampling.

Python 3.11+ and the existing prepare_input_data helpers are required. This
module only reads local metadata/PDFs and writes preparation artifacts; it
never downloads PDFs or calls a model. --all keeps every deduplicated record
in source order without sampling. It fails, listing exclusions, if any record
is temporally ineligible or its full-history selection is unresolved. Missing
PDFs do not exclude records, including in --all mode.
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
PLAN_SCHEMA_VERSION = 'source-data-maximum-sequence-plan-1'
OBSERVED_METADATA_SCHEMA_VERSION = 'source-data-observed-original-pdf-metadata-1'


def require_source_data(data, label):
    if not isinstance(data, dict) or data.get('dataset') != 'source_data':
        raise ValueError(f'{label} must declare dataset source_data; legacy/unlabelled data is unsupported')


def validate_identity(paper):
    """Validate identities used as paths or quota keys, without changing metadata."""
    if not isinstance(paper, dict):
        raise ValueError('Invalid source_data paper metadata')
    aid = paper.get('arxiv_id')
    if not isinstance(aid, str) or not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
        raise ValueError(f'Unsupported arXiv ID: {aid}')
    sources = paper.get('source_ids')
    if (not isinstance(sources, list) or not sources
            or any(not isinstance(s, str) or not s.strip() for s in sources)
            or len(set(sources)) != len(sources)):
        raise ValueError(f'Invalid or duplicate source_ids: {aid}')
    if (not isinstance(paper.get('temporal_eligibility'), dict)
            or 'status' not in paper['temporal_eligibility']
            or not isinstance(paper.get('source_entries'), list)
            or not isinstance(paper.get('title'), str)
            or not isinstance(paper.get('arxiv_url'), str)):
        raise ValueError(f'Incomplete source_data paper metadata: {aid}')


def load_source(path, source_ids=None):
    """Read source_data, optionally composing pinned readonly bases and additions."""
    requested_path = Path(path).absolute()
    path = requested_path.resolve()
    raw = path.read_bytes()
    source = json.loads(raw)
    require_source_data(source, 'Source')
    inputs = [{'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]
    selected_sources = set(source_ids or ())
    overlay = 'bases' in source
    record_origins = []
    if overlay and ('records' in source or not isinstance(source['bases'], list) or not source['bases']):
        raise ValueError('An overlay requires nonempty bases and local source shard files')
    if 'records' in source:
        records = source['records']
        metadata = {k: v for k, v in source.items() if k != 'records'}
    else:
        records = []
        metadata = source.get('dataset_metadata')
        require_source_data(metadata, 'Index dataset metadata')
        if not isinstance(source.get('files'), list) or not source['files']:
            raise ValueError('A source_data index requires source shard files')
        entries = []
        if overlay:
            if (path.parent.name != 'index' or path.parent.parent.name != 'data_full'
                    or requested_path != path):
                raise ValueError('Overlay index must be in the canonical data_full/index directory')
            project = path.parent.parent.parent
            for entry in source['bases']:
                sid = entry.get('source_id')
                if (entry.get('catalog') != 'data' or 'path' in entry
                        or not isinstance(sid, str)
                        or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', sid) or '..' in sid):
                    raise ValueError('Invalid readonly base catalog/source identity')
                base = project / 'data' / 'index' / f'{sid}.json'
                if base.resolve() != base:
                    raise ValueError('Readonly base path escapes the canonical data/index directory')
                if (type(entry.get('bytes')) is not int or entry['bytes'] < 0
                        or type(entry.get('record_count')) is not int or entry['record_count'] < 0
                        or not isinstance(entry.get('sha256'), str)
                        or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
                    raise ValueError('Invalid readonly base checksum/record count')
                entries.append((entry, base, 'Base'))
        for entry in source['files']:
            relative = Path(entry['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError(f'Shard path escapes the index directory: {entry["path"]}')
            shard_path = (path.parent / relative).resolve()
            if not shard_path.is_relative_to(path.parent):
                raise ValueError(f'Shard path escapes the index directory: {entry["path"]}')
            entries.append((entry, shard_path, 'Shard'))
        seen_paths = {path}
        for entry, shard_path, kind in entries:
            if shard_path in seen_paths:
                raise ValueError(f'Duplicate shard path: {shard_path}')
            seen_paths.add(shard_path)
            if selected_sources and entry.get('source_id') not in selected_sources:
                # Unlabelled legacy shards are still read and filtered by membership.
                if 'source_id' in entry:
                    continue
            payload = shard_path.read_bytes()
            sha = hashlib.sha256(payload).hexdigest()
            if sha != entry['sha256'] or len(payload) != entry['bytes']:
                raise ValueError(f'{kind} checksum mismatch: {shard_path.name}')
            shard = json.loads(payload)
            require_source_data(shard, f'{kind} {shard_path.name}')
            if 'bases' in shard:
                raise ValueError('Nested overlay catalogs are unsupported')
            if not isinstance(shard.get('records'), list):
                raise ValueError(f'{kind} records must be a list: {shard_path.name}')
            if (len(shard['records']) != entry['record_count']
                    or shard.get('record_count', len(shard['records'])) != len(shard['records'])):
                raise ValueError(f'{kind} record count mismatch: {shard_path.name}')
            if 'source_id' in entry and (shard.get('source_id') != entry['source_id']
                    or any(entry['source_id'] not in p.get('source_ids', []) for p in shard['records'])):
                raise ValueError(f'{kind} source identity mismatch: {shard_path.name}')
            records.extend(shard['records'])
            if overlay:
                record_origins.extend([shard_path if kind == 'Base' else None] * len(shard['records']))
            inputs.append({'path': str(shard_path), 'bytes': len(payload), 'sha256': sha})
    if not isinstance(records, list) or not records:
        raise ValueError('A source_data source requires a nonempty records list')
    by_id = {}
    origins_by_id = {}
    for position, record in enumerate(records):
        validate_identity(record)
        aid = record['arxiv_id']
        origin = record_origins[position] if overlay else None
        # Only identical memberships across distinct readonly base shards may overlap.
        if aid in by_id and overlay and (origin is None or origins_by_id[aid] is None
                                        or origin == origins_by_id[aid]):
            raise ValueError(f'Overlapping overlay paper identity: {aid}')
        if aid in by_id and by_id[aid] != record:
            raise ValueError(f'Conflicting duplicate paper metadata: {aid}')
        by_id[aid] = record
        origins_by_id[aid] = origin
    if 'record_order' in source:
        order = source['record_order']
        if (not isinstance(order, list) or any(not isinstance(aid, str) for aid in order)
                or len(order) != len(set(order))
                or (not selected_sources and set(order) != set(by_id))
                or (selected_sources and not set(by_id).issubset(order))):
            raise ValueError('Index record_order does not match the deduplicated records')
        ordered = [by_id[aid] for aid in order if aid in by_id]
    else:
        ordered = list(by_id.values())
    if not selected_sources and metadata.get('record_count', len(ordered)) != len(ordered):
        raise ValueError('Dataset record_count does not match deduplicated records')
    provenance = {'source_file': str(path), 'source_sha256': inputs[0]['sha256'],
                  'input_files': inputs, 'dataset_metadata': metadata,
                  'duplicate_memberships_collapsed': len(records) - len(ordered)}
    if selected_sources:
        available = {sid for paper in ordered for sid in paper['source_ids']}
        if selected_sources - available:
            raise ValueError(f'Unknown source IDs: {sorted(selected_sources - available)}')
        ordered = [paper for paper in ordered if selected_sources.intersection(paper['source_ids'])]
        provenance['unfiltered_dataset_metadata'] = metadata
        provenance['dataset_metadata'] = {**metadata, 'record_count': len(ordered)}
        provenance['selection_filter'] = {'source_ids': sorted(selected_sources)}
    return ordered, provenance


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
    versions = paper.get('versions')
    frozen_n = paper.get('frozen_N')
    if (not isinstance(versions, list) or type(frozen_n) is not int or frozen_n < 1
            or any(not isinstance(v, dict) or 'version' not in v for v in versions)):
        raise ValueError('Incomplete or invalid original history; sequence selection is unresolved')
    numbers = [v['version'] for v in versions]
    if (any(type(v) is not int or v < 1 for v in numbers)
            or sorted(numbers) != list(range(1, frozen_n + 1))):
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


def pdf_path(pdf_root, aid, version, source=None):
    if (not isinstance(aid, str) or not re.fullmatch(r'\d{4}\.\d{4,5}', aid)
            or type(version) is not int or version < 1):
        raise ValueError('Invalid PDF identity')
    if source is not None and (not isinstance(source, str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', source) or '..' in source):
        raise ValueError(f'Invalid conference identity: {source}')
    path = (pdf_root / source / aid / f'v{version}.pdf' if source is not None
            else pdf_root / aid / f'{aid}v{version}.pdf')
    if not path.resolve().is_relative_to(pdf_root.resolve()):
        raise ValueError(f'PDF path escapes the local PDF root: {path}')
    return path


def resolve_pdf(pdf_root, aid, version, source=None, fallback_roots=()):
    """Choose an explicit readonly original store first; never skip existing bad bytes."""
    for root in fallback_roots:
        candidate = pdf_path(Path(root), aid, version)
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return pdf_path(Path(pdf_root), aid, version, source)


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
    if ((units is None and (type(per_source) is not int or per_source < 1))
            or (units is not None and (type(units) is not int or units < 1))):
        raise ValueError('--per-source and --units must be positive integers')
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
    units = getattr(args, 'units', None)
    all_records = getattr(args, 'all', False)
    per_source = getattr(args, 'per_source', None)
    if all_records and (units is not None or per_source is not None):
        raise ValueError('--all cannot be combined with --units or explicit --per-source')
    if ((per_source is not None and (type(per_source) is not int or per_source < 1))
            or (units is not None and (type(units) is not int or units < 1))):
        raise ValueError('--per-source and --units must be positive integers')
    per_source = 5 if per_source is None else per_source
    records, provenance = load_source(args.source)
    pdf_root = args.pdf_root.resolve()
    fallback_roots = [Path(root).resolve() for root in (getattr(args, 'fallback_pdf_root', None) or ())]
    layout = getattr(args, 'pdf_layout', 'legacy')
    if layout not in ('legacy', 'conference'):
        raise ValueError(f'Unsupported PDF layout: {layout}')
    conference = getattr(args, 'conf', None)
    arxiv_ids = getattr(args, 'arxiv_id', None) or []
    if conference is not None or arxiv_ids:
        records = [p for p in records if (conference is None or conference in p['source_ids'])
                   and (not arxiv_ids or p['arxiv_id'] in arxiv_ids)]
        missing = set(arxiv_ids) - {p['arxiv_id'] for p in records}
        if not records or missing:
            raise ValueError(f'No matching papers for conference/IDs: {conference}, {sorted(missing)}')
        provenance['unfiltered_dataset_metadata'] = provenance['dataset_metadata']
        provenance['dataset_metadata'] = {**provenance['dataset_metadata'], 'record_count': len(records)}
        provenance['selection_filter'] = {'conference': conference, 'arxiv_ids': arxiv_ids}
    sources = sorted({s for p in records for s in p['source_ids']})
    if conference is not None:
        sources = [conference]
    counts = {s: Counter(candidate_memberships=0, metadata_temporally_eligible=0,
                         sequence_selection_resolved=0, sequence_selection_unresolved=0,
                         all_selected_pdf_files_present=0, missing_selected_pdf_files=0)
              for s in sources}
    pools, candidates, exclusions = {s: [] for s in sources}, {}, []
    for paper in records:
        aid = paper['arxiv_id']
        memberships = [s for s in paper['source_ids'] if s in sources]
        for source in memberships:
            counts[source]['candidate_memberships'] += 1
        if paper['temporal_eligibility']['status'] != 'eligible':
            exclusions.append({'arxiv_id': aid, 'source_ids': paper['source_ids'],
                               'reason': 'not_frozen_temporally_eligible'})
            continue
        for source in memberships:
            counts[source]['metadata_temporally_eligible'] += 1
        try:
            selected, edges = select_versions(paper)
            if len(selected) < 2:
                raise ValueError('Full-history timestamps contradict the frozen eligible status')
        except ValueError as exc:
            exclusions.append({'arxiv_id': aid, 'source_ids': paper['source_ids'],
                               'reason': 'full_history_sequence_selection_unresolved', 'detail': str(exc),
                               'frozen_temporal_eligibility': paper['temporal_eligibility']})
            for source in memberships:
                counts[source]['sequence_selection_unresolved'] += 1
            continue
        for source in memberships:
            missing = [v['version'] for v in selected
                       if not resolve_pdf(pdf_root, aid, v['version'],
                                          source if layout == 'conference' else None, fallback_roots).is_file()]
            counts[source]['sequence_selection_resolved'] += 1
            counts[source]['missing_selected_pdf_files' if missing else 'all_selected_pdf_files_present'] += 1
            pools[source].append(aid)
        candidates[aid] = (paper, selected, edges)
    if all_records:
        # "All" means the entire deduplicated inventory, never an eligible subset.
        # Report every exclusion and stop before writing a misleading full plan.
        if exclusions:
            raise ValueError('--all requires every metadata record to be eligible and resolved; '
                             'excluded_records=' + json.dumps(exclusions, ensure_ascii=False))
        assignment, quotas = {}, Counter()
        for aid, (paper, _, _) in candidates.items():
            source = min((s for s in paper['source_ids'] if s in sources), key=lambda s: (quotas[s], s))
            assignment[aid] = source
            quotas[source] += 1
    else:
        assignment = assign_sources(pools, per_source, args.seed, units=units)
    selected_units, requests = [], []
    for aid, source in assignment.items():
        paper, selected, edges = candidates[aid]
        selected_metadata = []
        for version in selected:
            number = version['version']
            path = resolve_pdf(pdf_root, aid, number, source if layout == 'conference' else None, fallback_roots)
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
    plan = {'schema_version': PLAN_SCHEMA_VERSION, 'language': 'en', 'status': 'planned',
            'created_at_utc': datetime.now(timezone.utc).isoformat(), **provenance,
            'adapter_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'pdf_root': str(pdf_root),
            'pdf_layout': layout,
            **({'fallback_pdf_roots': [str(root) for root in fallback_roots]} if fallback_roots else {}),
            'sample_selection': {'method': 'all_records_in_source_order' if all_records else 'source_stratified_seeded_bipartite_assignment',
                                 'all_records': all_records,
                                 'seed': None if all_records else args.seed,
                                 'papers_per_source': per_source if units is None and not all_records else None,
                                 'requested_units': units,
                                 'source_quotas': {s: list(assignment.values()).count(s) for s in sources},
                                 'source_assignment': assignment,
                                 'source_assignment_note': 'Each arXiv ID has one quota source; all original source memberships remain unchanged.',
                                 'quota_policy': ('No sampling; assign each record in source order to its least-used source, ties by source ID.' if all_records else
                                                  'Balance distinct paper units across metadata source pools; cap scarce sources and redistribute remaining slots deterministically.'),
                                 'sampling_population': ('Every deduplicated record in the supplied metadata inventory; fail if any is ineligible or unresolved.' if all_records else
                                                         'All frozen temporally eligible papers with resolved full-history sequence selection.'),
                                 'pdf_availability_affects_selection': False,
                                 'replacement_policy': 'Never replace selected papers because PDFs are missing or invalid.'},
            'temporal_rule': {'operator': '>', 'gap_seconds': GAP_SECONDS, 'minimum_selected_versions': 2,
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


def validate_plan(plan):
    """Check the frozen selection and all derived identities/counts before writes.

    Source files need not still exist: MBZ Batch can import/freeze this module
    and transport a self-contained manifest to another local PDF root.
    """
    if not isinstance(plan, dict) or plan.get('schema_version') != PLAN_SCHEMA_VERSION:
        raise ValueError('Unsupported plan schema; historical witness experiments must remain unchanged')
    require_source_data(plan.get('dataset_metadata'), 'Plan dataset metadata')
    records = plan.get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('Plan requires nonempty fixed paper records')
    for paper in records:
        validate_identity(paper)
    papers = {p['arxiv_id']: p for p in records}
    assignment = plan['sample_selection']['source_assignment']
    if (not isinstance(assignment, dict) or len(papers) != len(records)
            or set(papers) != set(assignment)):
        raise ValueError('Plan paper identities do not match the fixed source assignment')
    units = plan['selected_units']
    if (len(units) != len(papers)
            or {u['arxiv_id'] for u in units} != set(papers)):
        raise ValueError('Plan selected units do not match its paper identities')
    if not isinstance(plan['pdf_root'], str) or not Path(plan['pdf_root']).is_absolute():
        raise ValueError('Plan pdf_root must be an absolute local path')
    root = Path(plan['pdf_root'])
    fallback_roots = plan.get('fallback_pdf_roots', [])
    if (not isinstance(fallback_roots, list)
            or any(not isinstance(value, str) or not Path(value).is_absolute() for value in fallback_roots)):
        raise ValueError('Plan fallback_pdf_roots must be absolute local paths')
    layout = plan.get('pdf_layout', 'legacy')
    if layout not in ('legacy', 'conference'):
        raise ValueError(f'Unsupported PDF layout: {layout}')
    rule = plan['temporal_rule']
    if rule.get('operator') != '>' or rule.get('gap_seconds') != GAP_SECONDS:
        raise ValueError('Plan temporal rule must use a strict > 2592000 second gap')
    selections, requests, pair_count = {}, [], 0
    for unit in units:
        aid = unit['arxiv_id']
        paper = papers[aid]
        if (assignment[aid] not in paper['source_ids']
                or unit['assigned_source_id'] != assignment[aid]):
            raise ValueError(f'Plan source assignment disagrees with paper membership: {aid}')
        selected, edges = select_versions(paper)
        if (paper['temporal_eligibility']['status'] != 'eligible' or len(selected) < 2
                or [v['version'] for v in selected] != unit['selected_version_ids']
                or edges != unit['selected_transitions']
                or unit['selected_version_count'] != len(selected)
                or unit['pair_count'] != len(edges)):
            raise ValueError(f'Fixed sequence plan disagrees with the original history: {aid}')
        if len(unit['selected_versions']) != len(selected):
            raise ValueError(f'Plan PDF request count disagrees with the fixed sequence: {aid}')
        for version, request in zip(selected, unit['selected_versions']):
            number = version['version']
            # Inspect stored paths lexically. A transported manifest's old root
            # is provenance, not an input to read or a path to resolve/follow.
            expected = {'arxiv_id': aid, 'version': number, 'requested_id': f'{aid}v{number}',
                        'pdf_url': version['pdf_url'], 'submitted_at': version['submitted_at'],
                        'pdf_path': str(root / assignment[aid] / aid / f'v{number}.pdf'
                                        if layout == 'conference' else root / aid / f'{aid}v{number}.pdf'),
                        'expected_sha256': version.get('pdf', {}).get('sha256') or
                                           version.get('pdf', {}).get('expected_sha256')}
            if fallback_roots:
                allowed_paths = {expected['pdf_path'], *(str(Path(value) / aid / f'{aid}v{number}.pdf')
                                                        for value in fallback_roots)}
                if request.get('pdf_path') not in allowed_paths:
                    raise ValueError(f'Plan PDF path is outside the declared stores: {aid}v{number}')
                expected['pdf_path'] = request['pdf_path']
            if (any(request.get(k) != value for k, value in expected.items())
                    or type(request.get('present_at_plan')) is not bool):
                raise ValueError(f'Plan PDF request identity/path disagrees with the fixed sequence: {aid}v{number}')
            requests.append(request)
        pair_count += len(edges)
        selections[aid] = selected
    if plan['requested_pdfs'] != requests:
        raise ValueError('Plan requested_pdfs must match each unique selected PDF exactly once, in order')
    missing = [r['requested_id'] for r in requests if not r['present_at_plan']]
    expected_summary = {'paper_count': len(papers), 'pair_count': pair_count,
                        'unique_selected_pdf_count': len(requests),
                        'present_pdf_count_at_plan': len(requests) - len(missing),
                        'missing_pdf_count_at_plan': len(missing)}
    if (any(plan['summary'].get(k) != value for k, value in expected_summary.items())
            or plan['missing_pdf_ids'] != missing):
        raise ValueError('Plan counts or missing PDF inventory disagree with its fixed selection')
    quotas = plan['sample_selection']['source_quotas']
    if (not isinstance(quotas, dict) or any(type(n) is not int or n < 0 for n in quotas.values())
            or {s: n for s, n in quotas.items() if n} != dict(Counter(assignment.values()))):
        raise ValueError('Plan source quotas disagree with its fixed source assignment')
    if plan['sample_selection'].get('all_records') and (
            plan['excluded_records'] or len(records) != plan['dataset_metadata'].get('record_count', len(records))):
        raise ValueError('An --all plan must contain every metadata record without exclusions')
    return papers, selections


def validate_output_paths(out, plan):
    """Reject redirected destinations and stale temporary files before any write.

    Existing matching plans can be resumed, but nested symlinks must not send
    metadata/PDF writes outside the chosen directory. Shared write_json uses
    sibling .tmp files, so those must be absent even when they are regular files.
    """
    directories, files = {out}, {out / name for name in (
        'selection_manifest.json', 'materialization_manifest.json', 'pairs.json')}
    for unit in plan['selected_units']:
        folder = out / unit['arxiv_id']
        directories.add(folder)
        files.add(folder / 'metadata.json')
        for edge in unit['selected_transitions']:
            b, a = edge['from_version'], edge['to_version']
            pair = folder / f'v{b}_v{a}'
            directories.add(pair)
            files.update((pair / 'pair.json', pair / f'v{b}.pdf', pair / f'v{a}.pdf'))
    for path in directories | files:
        if path.is_symlink() or not path.resolve().is_relative_to(out):
            raise ValueError(f'Unsafe output path (symlink or escape): {path}')
        if path.exists() and (not path.is_dir() if path in directories else not path.is_file()):
            raise ValueError(f'Unexpected output path type: {path}')
    for path in files:
        temporary = path.with_suffix(path.suffix + '.tmp')
        if temporary.exists() or temporary.is_symlink():
            raise ValueError(f'Output temporary path already exists: {temporary}')


def materialize_plan(manifest_path, output=None, pdf_root=None, fallback_pdf_roots=None):
    """Verify and materialize the fixed selected paper IDs; never resample."""
    manifest_path = manifest_path.resolve()
    raw = manifest_path.read_bytes()
    plan = json.loads(raw)
    papers, selections = validate_plan(plan)
    out = output.resolve() if output else manifest_path.parent
    root = (pdf_root if pdf_root else Path(plan['pdf_root'])).resolve()
    stored_roots = plan.get('fallback_pdf_roots', [])
    fallback_roots = ([Path(value).resolve() for value in fallback_pdf_roots]
                      if fallback_pdf_roots is not None else [Path(value) for value in stored_roots])
    if len(fallback_roots) != len(stored_roots):
        raise ValueError('Relocated fallback PDF roots must match the frozen root count')
    requests = {request['requested_id']: request for request in plan['requested_pdfs']}
    def local_pdf(aid, number):
        request = requests[f'{aid}v{number}']
        for frozen_root, local_root in zip(stored_roots, fallback_roots):
            if request['pdf_path'] == str(Path(frozen_root) / aid / f'{aid}v{number}.pdf'):
                return pdf_path(local_root, aid, number)
        source = (plan['sample_selection']['source_assignment'][aid]
                  if plan.get('pdf_layout', 'legacy') == 'conference' else None)
        return pdf_path(root, aid, number, source)
    # Resolve only the current local PDF root, and validate every path before
    # creating even a manifest or a failure receipt.
    for aid, selected in selections.items():
        for version in selected:
            local_pdf(aid, version['version'])
    validate_output_paths(out, plan)
    local_manifest = out / 'selection_manifest.json'
    if local_manifest.resolve() != manifest_path:
        if local_manifest.exists() and local_manifest.read_bytes() != raw:
            raise ValueError('Output already contains a different fixed selection plan')
        if not local_manifest.exists() and out.exists() and any(out.iterdir()):
            raise ValueError('Output is not empty and has no matching selection plan')
    out.mkdir(parents=True, exist_ok=True)
    if local_manifest.resolve() != manifest_path and not local_manifest.exists():
        local_manifest.write_bytes(raw)
    verified, failures = {}, []
    for checked, unit in enumerate(plan['selected_units'], 1):
        aid = unit['arxiv_id']
        for version in selections[aid]:
            key = f"{aid}v{version['version']}"
            try:
                evidence = validate_pdf(local_pdf(aid, version['version']), aid, version)
                verified[key] = evidence
            except Exception as exc:
                failures.append({'arxiv_id': aid, 'version': version['version'],
                                 'requested_id': key, 'reason': 'selected_pdf_unavailable_or_invalid',
                                 'detail': str(exc)})
        if checked % 50 == 0:
            print(f'PDF validation: {checked} fixed paper units checked; '
                  f'{len(verified)} version PDFs verified; {len(failures)} unresolved.', flush=True)
    receipt = {'schema_version': 'source-data-materialization-1', 'language': 'en',
               'selection_manifest_sha256': hashlib.sha256(raw).hexdigest(),
               'created_at_utc': datetime.now(timezone.utc).isoformat(),
               'pdf_root': str(root), 'fallback_pdf_roots': [str(value) for value in fallback_roots],
               'status': 'unresolved' if failures else 'verified',
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
                    'pair_type': 'adjacent_selected_maximum_sequence_versions',
                    'path_base': 'input_data', 'paper_metadata_path': f'{aid}/metadata.json',
                    **edge}
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
    if not args.dry_run and getattr(args, 'output', None) is None:
        raise ValueError('Writes require --output; --dry-run may omit it')
    plan = build_plan(args)
    validate_plan(plan)
    print(json.dumps({'summary': plan['summary'],
                      'source_quotas': plan['sample_selection']['source_quotas']}), flush=True)
    if args.dry_run:
        return plan
    out = args.output.resolve()
    validate_output_paths(out, plan)
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output is not empty; materialize the existing plan or choose a new output directory')
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / 'selection_manifest.json'
    write_json(manifest, plan)
    if not getattr(args, 'plan_only', False):
        materialize_plan(manifest)
    return plan


def main(cleaned=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Local source_data index.json, source shard, or combined source_data.')
    parser.add_argument('--pdf-root', type=Path,
                        help='PDF store; build_pairs defaults to clean_data with layout <conf>/<id>/vN.pdf.')
    parser.add_argument('--pdf-layout', choices=('legacy', 'conference'),
                        default='conference' if cleaned else 'legacy')
    parser.add_argument('--fallback-pdf-root', type=Path, action='append',
                        help='Readonly original <id>/<id>vN.pdf store checked before the primary root; repeatable.')
    parser.add_argument('--conf', help='Only select papers from this conference/source ID.')
    parser.add_argument('--arxiv-id', action='append', default=[],
                        help='Only these paper IDs; repeatable. Defaults to --all within this filter.')
    parser.add_argument('--output', type=Path, help='Required for writes; optional for --dry-run.')
    parser.add_argument('--per-source', type=int, help='Distinct papers per source (default: 5 when no scope option is set).')
    parser.add_argument('--units', type=int,
                        help='Total distinct paper units; source-balanced metadata sampling. Overrides --per-source.')
    parser.add_argument('--all', action='store_true',
                        help='Every deduplicated metadata record, without sampling; fail if any is ineligible/unresolved. Incompatible with --units/--per-source.')
    parser.add_argument('--seed', type=int, help='Sampling seed (default: 20260911).')
    parser.add_argument('--dry-run', action='store_true', help='Print the plan summary without parsing PDFs or writing.')
    parser.add_argument('--plan-only', action='store_true', help='Freeze paper IDs and exact PDF requests without validation or materialization.')
    parser.add_argument('--materialize-plan', type=Path, help='Materialize a fixed selection_manifest.json without resampling.')
    args = parser.parse_args()
    if cleaned and args.fallback_pdf_root:
        parser.error('build_pairs requires its clean PDF store; original fallback roots are unsupported')
    if args.arxiv_id and args.units is None and args.per_source is None:
        args.all = True
    if cleaned and not args.materialize_plan:
        directory = Path(__file__).resolve().parent
        project = (directory.parent.parent if directory.name == 'data'
                   and directory.parent.name == 'utility' else directory.parent)
        config = json.loads((project / 'utility/project.json').read_text())
        args.source = args.source or project / config['data']['index']
        args.pdf_root = args.pdf_root or project / config['data']['clean_root']
    if ((args.per_source is not None and args.per_source < 1)
            or (args.units is not None and args.units < 1)):
        parser.error('--per-source and --units must be positive')
    if args.all and (args.units is not None or args.per_source is not None):
        parser.error('--all cannot be combined with --units or explicit --per-source')
    if args.materialize_plan:
        if (args.plan_only or args.dry_run or args.source or args.all or args.conf or args.arxiv_id
                or args.units is not None or args.per_source is not None or args.seed is not None):
            parser.error('--materialize-plan cannot be combined with source, sampling, --plan-only or --dry-run options')
    elif not all((args.source, args.pdf_root)):
        parser.error('Planning requires --source and --pdf-root')
    if not args.dry_run and args.output is None:
        parser.error('Writes require --output; --dry-run may omit it')
    args.seed = 20260911 if args.seed is None else args.seed
    try:
        if args.materialize_plan:
            materialize_plan(args.materialize_plan, args.output, args.pdf_root, args.fallback_pdf_root)
        else:
            prepare(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'Preparation failed: {exc}\n')


if __name__ == '__main__':
    main()

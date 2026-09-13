"""Frozen venue profiles and bounded, evidence-based source resolution."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re


def _rating(value, kind):
    if kind == 'integer':
        return type(value) is int
    if kind == 'number':
        return type(value) is int or (type(value) is float and math.isfinite(value))
    return kind == 'string' and type(value) is str and bool(value.strip())


def _scale(kind, values):
    if (kind not in ('integer', 'number', 'string') or
            not isinstance(values, (list, tuple)) or not values or
            any(not _rating(v, kind) for v in values) or
            len(set(values)) != len(values)):
        raise ValueError('Invalid OA type or enum values')


def _schema(kind, values):
    _scale(kind, values)
    return {'type': 'object', 'properties': {
        'oa': {'type': kind, 'enum': list(values)},
        'strengths': {'type': 'array', 'items': {'type': 'string'}},
        'weaknesses': {'type': 'array', 'items': {'type': 'string'}},
    }, 'required': ['oa', 'strengths', 'weaknesses'], 'additionalProperties': False}


@dataclass(frozen=True)
class SystemProfile:
    source_id: str
    system_file: str
    text: str
    sha256: str
    oa_type: str
    oa_values: tuple
    scale_kind: str = 'native'
    oa_labels: tuple = ()

    def __post_init__(self):
        _scale(self.oa_type, self.oa_values)
        object.__setattr__(self, 'oa_values', tuple(self.oa_values))
        if self.scale_kind not in ('native', 'experimental_encoding'):
            raise ValueError('Invalid scale_kind')
        if (not isinstance(self.oa_labels, (tuple, list)) or
                (self.oa_labels and len(self.oa_labels) != len(self.oa_values)) or
                any(not isinstance(label, str) or not label.strip() for label in self.oa_labels)):
            raise ValueError('OA labels must be nonempty strings parallel to OA values')
        object.__setattr__(self, 'oa_labels', tuple(self.oa_labels))

    @property
    def schema(self):
        return _schema(self.oa_type, self.oa_values)

    def metadata(self):
        return {'source_id': self.source_id, 'system_file': self.system_file,
                'sha256': self.sha256, 'oa_type': self.oa_type,
                'oa_values': list(self.oa_values), 'scale_kind': self.scale_kind,
                'oa_labels': list(self.oa_labels)}


def _safe_path(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or
            PurePosixPath(name).is_absolute() or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError(f'Invalid relative path: {name!r}')
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f'Missing file or path escape: {name}')
    return path


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _json(data, path):
    try:
        value = json.loads(data.decode('utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f'Invalid UTF-8 JSON: {path}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return value


def _checked_bytes(path, expected):
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f'Invalid SHA256: {path}')
    data = path.read_bytes()
    if _digest(data) != expected:
        raise ValueError(f'Checksum mismatch: {path}')
    return data


def _source(value, known=None):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_]*', value):
        raise ValueError(f'Invalid source ID: {value!r}')
    if known is not None and value not in known:
        raise ValueError(f'Unknown source: {value}')
    return value


def load_prompts(folder: Path, experiments=None, conditions=None):
    folder = Path(folder)
    manifest_path = _safe_path(folder, 'manifest.json')
    manifest = _json(manifest_path.read_bytes(), manifest_path)
    files, systems, available = (manifest.get(k) for k in ('files', 'systems', 'conditions'))
    if not isinstance(files, dict) or not files or not isinstance(systems, dict) or not systems:
        raise ValueError('Manifest requires nonempty files and systems maps')
    texts = {}
    for name, expected in files.items():
        data = _checked_bytes(_safe_path(folder, name), expected)
        try:
            texts[name] = data.decode('utf-8')
        except UnicodeError as exc:
            raise ValueError(f'Invalid UTF-8 prompt: {name}') from exc
    profiles = {}
    for source_id, config in systems.items():
        _source(source_id)
        if not isinstance(config, dict):
            raise ValueError(f'Invalid system profile: {source_id}')
        name = config.get('file')
        if not isinstance(name, str) or name not in texts or not texts[name].strip():
            raise ValueError(f'Missing or empty system prompt: {source_id}')
        _scale(config.get('oa_type'), config.get('oa_values'))
        if 'oa_labels' in config and (not isinstance(config['oa_labels'], list) or
                                      len(config['oa_labels']) != len(config['oa_values'])):
            raise ValueError(f'OA label count mismatch: {source_id}')
        profiles[source_id] = SystemProfile(source_id, name, texts[name], files[name],
                                            config['oa_type'], tuple(config['oa_values']),
                                            config.get('scale_kind', 'native'),
                                            config.get('oa_labels', ()))
    if not isinstance(available, list) or not available:
        raise ValueError('Manifest requires nonempty conditions')
    ids = set()
    for item in available:
        if not isinstance(item, dict):
            raise ValueError('Invalid condition')
        cid, experiment, name = (item.get(k) for k in ('id', 'experiment', 'user_file'))
        if (not isinstance(cid, str) or not cid.strip() or cid in ids or
                not isinstance(experiment, str) or not experiment.strip() or
                not isinstance(name, str) or name not in texts or not texts[name].strip()):
            raise ValueError('Invalid, duplicate, or missing condition')
        shared = item.get('shared_baseline_for', [])
        if not isinstance(shared, list) or any(
                not isinstance(value, str) or not value.strip() for value in shared):
            raise ValueError('shared_baseline_for must be a list of nonempty experiment strings')
        ids.add(cid)
    if conditions and set(conditions) - ids:
        raise ValueError(f'Unknown condition IDs: {sorted(set(conditions) - ids)}')
    selected = [(c['id'], texts[c['user_file']]) for c in available
                if (not conditions or c['id'] in conditions)
                and (not experiments or c['experiment'] in experiments or
                     any(value in experiments for value in c.get('shared_baseline_for', [])))]
    if not selected:
        raise ValueError('No conditions selected')
    return profiles, selected


def validate_result(result, schema):
    try:
        oa = schema['properties']['oa']
        expected = _schema(oa['type'], oa['enum'])
        if schema != expected:
            raise ValueError('Unexpected review schema')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Invalid review schema') from exc
    if (not isinstance(result, dict) or set(result) != {'oa', 'strengths', 'weaknesses'} or
            not _rating(result['oa'], oa['type']) or result['oa'] not in oa['enum']):
        raise ValueError('Expected exactly oa, strengths, weaknesses with a valid OA')
    for key in ('strengths', 'weaknesses'):
        if not isinstance(result[key], list) or any(
                not isinstance(x, str) or not x.strip() for x in result[key]):
            raise ValueError(f'{key} must be a list of nonempty strings')
    return result


def _members(values, known):
    if not isinstance(values, list) or not values:
        raise ValueError('Missing or invalid source memberships')
    return {_source(value, known) for value in values}


def _paper(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing or invalid paper ID: {value!r}')
    return value


def _metadata_memberships(document, known):
    """Return eligible memberships and binding per-paper source assignments."""
    declared = [_source(document[k], known) for k in ('source', 'source_id') if k in document]
    if len(set(declared)) > 1:
        raise ValueError('Conflicting declared source and source_id')
    declared = declared[0] if declared else None
    authorized = (_members(document['authorized_sources'], known)
                  if 'authorized_sources' in document else set(known))
    memberships, assignments = {}, {}

    def add(aid, values, *, full_record=False):
        aid = _paper(aid)
        if aid in memberships:
            # An assignment must fit its scope; a scope must fit the full record.
            if not memberships[aid] <= values:
                raise ValueError(f'Conflicting record memberships: {aid}')
            if full_record:
                return  # Full memberships must not widen a scoped selection.
        memberships[aid] = values

    if 'source_by_paper' in document:
        mapping = document['source_by_paper']
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError('Invalid source_by_paper')
        for aid, value in mapping.items():
            value = _source(value, known)
            add(aid, {value})
            assignments[aid] = value
    if 'record_memberships' in document:
        mapping = document['record_memberships']
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError('Invalid record_memberships')
        for aid, values in mapping.items():
            add(aid, _members(values, known))
    if 'records' in document:
        records = document['records']
        if not isinstance(records, list):
            raise ValueError('Invalid metadata records')
        seen = set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('Invalid metadata record')
            aid = _paper(record.get('arxiv_id'))
            if aid in seen:
                raise ValueError(f'Duplicate metadata record: {aid}')
            seen.add(aid)
            if 'source_ids' in record:
                add(aid, _members(record['source_ids'], known), full_record=True)
            elif aid not in memberships:
                if declared is None:
                    raise ValueError(f'Missing source memberships: {aid}')
                add(aid, {declared})
        if 'record_count' in document and (
                type(document['record_count']) is not int or document['record_count'] != len(records)):
            raise ValueError('Metadata record count mismatch')
    if 'selected_units' in document:
        units = document['selected_units']
        if not isinstance(units, list):
            raise ValueError('Invalid selected_units')
        for unit in units:
            if not isinstance(unit, dict):
                raise ValueError('Invalid selected unit')
            aid = _paper(unit.get('arxiv_id'))
            if 'assigned_source_id' not in unit:
                continue
            value = _source(unit['assigned_source_id'], known)
            if aid in assignments and assignments[aid] != value:
                raise ValueError(f'Conflicting assigned sources: {aid}')
            assignments[aid] = value
    if not memberships:
        raise ValueError('Metadata contains no source memberships')
    if set(assignments) - set(memberships):
        raise ValueError('Assigned source lacks record membership')
    for aid, values in memberships.items():
        eligible = values & authorized
        choices = {v for v in (declared, assignments.get(aid)) if v is not None}
        if len(choices) > 1 or not choices <= eligible:
            raise ValueError(f'Source assignment/membership mismatch: {aid}')
        if choices:
            assignments[aid] = next(iter(choices))
        memberships[aid] = eligible
    return memberships, assignments


def _read_metadata(path, known):
    path = Path(path).resolve()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f'Cannot read source metadata: {path}') from exc
    document = _json(data, path)
    hashes = {str(path): _digest(data)}
    if 'files' not in document:
        memberships, assignments = _metadata_memberships(document, known)
        return memberships, assignments, hashes
    files = document['files']
    if not isinstance(files, list) or not files:
        raise ValueError('Invalid canonical catalog files')
    memberships, seen = {}, set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError('Invalid canonical shard entry')
        source_id = _source(entry.get('source_id'), known)
        shard = _safe_path(path.parent, entry.get('path'))
        if shard in seen:
            raise ValueError(f'Duplicate canonical shard path: {shard}')
        seen.add(shard)
        raw = _checked_bytes(shard, entry.get('sha256'))
        if type(entry.get('bytes')) is not int or entry['bytes'] != len(raw):
            raise ValueError(f'Canonical shard byte size mismatch: {shard}')
        content = _json(raw, shard)
        identity = content.get('source_id', content.get('source'))
        if identity != source_id:
            raise ValueError(f'Canonical shard source identity mismatch: {shard}')
        records = content.get('records')
        if (not isinstance(records, list) or type(entry.get('record_count')) is not int or
                entry['record_count'] != len(records)):
            raise ValueError(f'Canonical shard record count mismatch: {shard}')
        shard_memberships, _ = _metadata_memberships(content, known)
        record_ids = {record['arxiv_id'] for record in records}
        if set(shard_memberships) != record_ids:
            raise ValueError(f'Canonical shard membership IDs must match its records: {shard}')
        for aid in record_ids:
            memberships.setdefault(aid, set()).add(source_id)
        hashes[str(shard)] = _digest(raw)
    return memberships, {}, hashes


def resolve_sources(input_root: Path, paper_ids, known_sources, source=None,
                    metadata=None, project_root=None):
    known = {_source(value) for value in known_sources}
    if not known:
        raise ValueError('No known source profiles')
    papers = list(dict.fromkeys(_paper(aid) for aid in paper_ids))
    if not papers:
        raise ValueError('No selected papers')
    if source is not None:
        source = _source(source.strip().lower() if isinstance(source, str) else source, known)
    origin = 'explicit_metadata'
    path = Path(metadata) if metadata is not None else None
    if path is None:
        prepared = Path(input_root).resolve()
        if re.fullmatch(r'v[1-9]\d*_v[1-9]\d*', prepared.name):
            prepared = prepared.parent.parent
        elif re.fullmatch(r'\d{4}\.\d{4,5}', prepared.name):
            prepared = prepared.parent
        candidates = [prepared / 'source_metadata.json', prepared / 'selection_manifest.json',
                      prepared.parent / 'scope_input.json']
        path = next((p for p in candidates if p.exists() or p.is_symlink()), None)
        origin = 'nearby_metadata'
    if path is None and source is not None:
        return dict.fromkeys(papers, source), {
            'mode': 'explicit_source', 'source': source, 'metadata_path': None, 'sha256': {}}
    if path is None:
        if project_root is None:
            raise ValueError('Missing source metadata; provide --source or --source-metadata')
        path = Path(project_root) / 'data/index/index.json'
        origin = 'canonical_catalog'
    memberships, assignments, hashes = _read_metadata(path, known)
    resolved = {}
    for aid in papers:
        if aid not in memberships:
            raise ValueError(f'Missing source membership for selected paper: {aid}')
        eligible, assigned = memberships[aid], assignments.get(aid)
        if source is not None:
            if source not in eligible or (assigned is not None and source != assigned):
                raise ValueError(f'Explicit source mismatch for paper: {aid}')
            resolved[aid] = source
        elif assigned is not None:
            resolved[aid] = assigned
        elif len(eligible) == 1:
            resolved[aid] = next(iter(eligible))
        else:
            raise ValueError(f'Missing or ambiguous source for paper {aid}: {sorted(eligible)}')
    return resolved, {'mode': origin, 'source': source,
                      'metadata_path': str(path.resolve()), 'sha256': hashes}

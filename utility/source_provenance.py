"""Content-pinned verification for the formal source_data export root.

The caller supplies a SHA-256 content revision, never a Git commit. Verification
is read-only and returns exact requested source bytes plus deployment documents.
The manifest revision is SHA-256 of the sorted, compact UTF-8 JSON files map.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

CORE_FILES = ('runner.py', 'schema.py', 'prompts/compare.md',
              'prompts/rubric.md', 'README.md', 'requirements.txt')
MANIFEST_SCHEMA_VERSION = 'source-manifest-1'
MANIFEST_PATH = 'utility/source_manifest.json'
PROJECT_PATH = 'utility/project.json'
INDEX_PATH = 'data/index/index.json'


def _digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _hex(value, length, label):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{%d}' % length, value):
        raise ValueError(f'{label} must be a full {length}-character hexadecimal ID')
    return value.lower()


def _relative_name(value):
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or any(part in ('', '.', '..', '.git') for part in value.split('/'))):
        raise ValueError(f'Expected a canonical relative file path: {value!r}')
    if value.split('/')[0] == 'deployment_provenance':
        raise ValueError(f'Reserved snapshot path: {value}')
    return value


def _root(value):
    try:
        path = Path(value).absolute()
    except TypeError as exc:
        raise ValueError('Expected a project directory path') from exc
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Symlink or traversal is not allowed: {path}')
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f'Cannot resolve project directory: {path}') from exc
    if not path.is_dir():
        raise ValueError(f'Expected a project directory: {path}')
    return path


def _inside(root, value):
    if not isinstance(value, (str, Path)) or not str(value) or '\\' in str(value) or '\x00' in str(value):
        raise ValueError(f'Invalid path: {value!r}')
    path = Path(value)
    if '..' in path.parts:
        raise ValueError(f'Path traversal is not allowed: {value}')
    path = path if path.is_absolute() else root / path
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ValueError(f'Path must be inside {root}: {value}') from exc
    if not parts:
        raise ValueError(f'Path must be strictly inside {root}: {value}')
    current = root
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ValueError(f'Cannot inspect path: {current}') from exc
        if stat.S_ISLNK(mode):
            raise ValueError(f'Symlink is not allowed: {current}')
    return path


def _read_file(path):
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f'Expected a regular source file: {path}')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read()
            finished = os.fstat(stream.fileno())
        after = path.lstat()
    except OSError as exc:
        raise ValueError(f'Cannot read regular source file: {path}') from exc
    if (any(_identity(info) != _identity(before) for info in (opened, finished, after))
            or len(raw) != before.st_size):
        raise ValueError(f'Source file changed during verification: {path}')
    return raw


def proof(path: Path) -> dict:
    """Return observed bytes/SHA-256 without modifying the source."""
    return _digest(_read_file(Path(path)))


def _json_object(raw, label):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key in {label}: {key}')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f'Invalid JSON constant in {label}: {value}')

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'Malformed JSON in {label}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object in {label}')
    return value


def revision_for_files(files):
    """Return the canonical content revision; timestamps/configuration are excluded."""
    raw = json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _allowed_name(name):
    name = _relative_name(name)
    parts = name.split('/')
    if any(part.startswith('.') or part in ('__pycache__', 'tests') for part in parts):
        raise ValueError(f'Excluded source-manifest path: {name}')
    allowed = (parts[0] == 'check_diff' and len(parts) > 1
               or len(parts) == 2 and parts[0] == 'utility' and parts[-1].endswith('.py')
               or len(parts) == 3 and parts[:2] == ['data', 'index'] and parts[-1].endswith('.json')
               or len(parts) > 3 and parts[:2] == ['data', 'index'] and parts[2] in ('metadata', 'provenance'))
    if not allowed:
        raise ValueError(f'Excluded source-manifest path: {name}')
    return name


def _source_names(project):
    names = set()
    for folder in ('check_diff', 'data/index/metadata', 'data/index/provenance'):
        root = _inside(project, folder)
        if not root.exists():
            if folder == 'check_diff':
                raise ValueError('Missing check_diff source directory')
            continue
        if not root.is_dir():
            raise ValueError(f'Expected a source directory: {root}')
        for path in root.rglob('*'):
            relative = str(path.relative_to(project))
            parts = path.relative_to(root).parts
            if any(p.startswith('.') or p in ('__pycache__', 'tests') for p in parts):
                continue
            _inside(project, relative)
            if not path.is_dir():
                names.add(_allowed_name(relative))
    for folder, pattern in (('utility', '*.py'), ('data/index', '*.json')):
        root = _inside(project, folder)
        if not root.is_dir():
            raise ValueError(f'Expected a source directory: {root}')
        for path in root.glob(pattern):
            if path.name.startswith('.'):
                continue
            names.add(_allowed_name(str(path.relative_to(project))))
    required = {INDEX_PATH, *(f'check_diff/{name}' for name in CORE_FILES)}
    if not required <= names:
        raise ValueError(f'Missing required source files: {sorted(required - names)}')
    return sorted(names)


def _capture(project, name, states):
    path = _inside(project, name)
    try:
        before = _identity(path.lstat())
    except OSError as exc:
        raise ValueError(f'Cannot read regular source file: {path}') from exc
    raw = _read_file(path)
    if _identity(path.lstat()) != before:
        raise ValueError(f'Source file changed during verification: {path}')
    states[name] = before
    return raw


def _stable(project, states):
    for name, before in states.items():
        path = _inside(project, name)
        try:
            after = _identity(path.lstat())
        except OSError as exc:
            raise ValueError(f'Source file changed during verification: {path}') from exc
        if after != before:
            raise ValueError(f'Source file changed during verification: {path}')


def build_manifest(project):
    """Read current formal sources and return a deterministic manifest; do not write."""
    project = _root(project)
    names = _source_names(project)
    states, files = {}, {}
    for name in names:
        files[name] = _digest(_capture(project, name, states))
    _stable(project, states)
    if _source_names(project) != names:
        raise ValueError('Source file inventory changed during manifest construction')
    return {'schema_version': MANIFEST_SCHEMA_VERSION, 'revision': revision_for_files(files), 'files': files}


def write_manifest(project):
    """Explicit maintenance operation: atomically write utility/source_manifest.json."""
    project = _root(project)
    manifest = build_manifest(project)
    target = _inside(project, MANIFEST_PATH)
    if target.exists() and not stat.S_ISREG(target.lstat().st_mode):
        raise ValueError(f'Expected a regular source manifest: {target}')
    descriptor, temporary = tempfile.mkstemp(prefix='.source-manifest-', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
        _inside(project, MANIFEST_PATH)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return manifest


def _file_index(manifest, project):
    if set(manifest) != {'schema_version', 'revision', 'files'} or manifest.get('schema_version') != MANIFEST_SCHEMA_VERSION:
        raise ValueError('Unsupported source manifest schema')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Source manifest files must be a nonempty map')
    indexed = {}
    for name, entry in files.items():
        name = _allowed_name(name)
        _inside(project, name)
        if not isinstance(entry, dict) or set(entry) != {'bytes', 'sha256'}:
            raise ValueError(f'Malformed source manifest entry: {name}')
        if type(entry['bytes']) is not int or entry['bytes'] < 0:
            raise ValueError(f'Invalid pinned byte count for {name}')
        indexed[name] = {'bytes': entry['bytes'], 'sha256': _hex(entry['sha256'], 64, f'sha256 for {name}')}
    revision = _hex(manifest.get('revision'), 64, 'Manifest revision')
    if revision != revision_for_files(files):
        raise ValueError('Manifest revision does not match its canonical files map')
    return indexed, revision


def _source_name(source):
    if not isinstance(source, str) or not re.fullmatch(r'[a-z0-9_]+', source) or source == 'index':
        raise ValueError('Invalid source names; expected [a-z0-9_]+ shard IDs')
    return source


def _index_shards(index, files):
    metadata = index.get('dataset_metadata')
    if (index.get('dataset') != 'source_data' or index.get('schema_version') != 'source-data-index-1'
            or not isinstance(metadata, dict) or metadata.get('dataset') != 'source_data'):
        raise ValueError('Index must declare the source_data dataset and current index schema')
    entries = index.get('files')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Index requires a nonempty shard files list')
    order = index.get('record_order')
    if (not isinstance(order, list) or any(not isinstance(aid, str) for aid in order)
            or len(order) != len(set(order)) or metadata.get('record_count') != len(order)):
        raise ValueError('Invalid source index record_order/count')
    shards = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Malformed index shard entry')
        source = _source_name(entry.get('source_id'))
        if entry.get('path') != f'{source}.json' or source in shards:
            raise ValueError('Invalid or duplicate index shard path/source')
        name = f'data/index/{source}.json'
        pinned = files.get(name)
        if (pinned is None or type(entry.get('bytes')) is not int or entry.get('bytes') != pinned['bytes']
                or _hex(entry.get('sha256'), 64, 'Index shard sha256') != pinned['sha256']):
            raise ValueError(f'Index shard hash/size differs from source manifest: {name}')
        if type(entry.get('record_count')) is not int or entry['record_count'] < 0:
            raise ValueError('Invalid index shard record count')
        shards[source] = entry
    return shards


def verified_sources(project, code_root, sources, source_revision, extra_files=()):
    """Read verified core/index/requested shards/extras and exact deployment proofs.

    The manifest covers all formal source/metadata files; this function verifies
    the requested payloads. Additional metadata/provenance files may be requested
    through extra_files. Unrequested file contents are not implicitly certified.
    """
    source_revision = _hex(source_revision, 64, 'source_revision')
    project = _root(project)
    if _root(code_root) != project:
        raise ValueError('code_root must equal the formal project root')
    if not isinstance(sources, list):
        raise ValueError('sources must be a list')
    sources = [_source_name(source) for source in sources]
    if len(sources) != len(set(sources)) or not isinstance(extra_files, tuple):
        raise ValueError('Duplicate sources or invalid extra_files collection')
    names = [*(f'check_diff/{name}' for name in CORE_FILES), INDEX_PATH,
             *(f'data/index/{source}.json' for source in sources),
             *(_allowed_name(name) for name in extra_files)]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate requested source file paths')
    states = {}
    config_raw = _capture(project, PROJECT_PATH, states)
    config = _json_object(config_raw, 'project.json')
    if config.get('schema_version') != 'reviewer-project-1' or not isinstance(config.get('data'), dict):
        raise ValueError('Invalid formal project configuration')
    if _inside(project, config['data'].get('index')) != project / INDEX_PATH:
        raise ValueError('data.index must identify data/index/index.json')
    manifest_path = _inside(project, config.get('source_manifest'))
    if manifest_path != project / MANIFEST_PATH:
        raise ValueError('source_manifest must identify utility/source_manifest.json')
    manifest_raw = _capture(project, MANIFEST_PATH, states)
    manifest = _json_object(manifest_raw, 'source_manifest.json')
    files, revision = _file_index(manifest, project)
    if revision != source_revision:
        raise ValueError('Manifest revision differs from the authorized source_revision')
    if not set(names) <= files.keys():
        raise ValueError(f'Missing source manifest entries: {sorted(set(names) - files.keys())}')
    blobs = {}
    for name in names:
        raw = _capture(project, name, states)
        if _digest(raw) != files[name]:
            raise ValueError(f'Source manifest hash/size mismatch: {name}')
        blobs[name] = raw
    shards = _index_shards(_json_object(blobs[INDEX_PATH], INDEX_PATH), files)
    for source in sources:
        if source not in shards:
            raise ValueError(f'Requested source is not listed in the index: {source}')
        shard = _json_object(blobs[f'data/index/{source}.json'], source)
        if (shard.get('dataset') != 'source_data' or shard.get('schema_version') != 'source-data-source-shard-1'
                or shard.get('source_id') != source or not isinstance(shard.get('records'), list)
                or len(shard['records']) != shards[source]['record_count']
                or shard.get('record_count') != len(shard['records'])):
            raise ValueError(f'Shard dataset/source/count mismatch: {source}')
    _stable(project, states)
    blobs['deployment_provenance/project.json'] = config_raw
    blobs['source_manifest.json'] = manifest_raw
    deployment = {'project': {'path': str(project / PROJECT_PATH), **_digest(config_raw)},
                  'source_manifest': {'path': str(manifest_path), **_digest(manifest_raw)}}
    return blobs, deployment

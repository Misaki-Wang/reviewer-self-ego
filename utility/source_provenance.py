"""Read-only verification of preparation inputs at a caller-authorized commit.

Legacy manifests contain ``files`` maps or rows with pinned bytes/sha256. The
explicit github-check-diff-sync-1 schema also supports digest strings and joins
v13_json_hashes by bare filename. Hash-only entries never acquire a pinned size:
their digest is verified against the observed bytes. Original manifest bytes
are returned unchanged so that distinction survives snapshotting.

Evidence contains only path/bytes/sha256 proofs for the deployment documents,
compatible with the preparation wrapper's input-proofs loop. Git fallback
returns empty evidence; the caller records the authorized commit when freezing.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess


CORE_FILES = ('runner.py', 'schema.py', 'token_usage.py', 'prompts/compare.md',
              'prompts/rubric.md', 'README.md', 'requirements.txt')
_SYNC_SCHEMA = 'github-check-diff-sync-1'


def _digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def proof(path: Path) -> dict:
    """Return the observed size and digest, in the existing wrapper format."""
    return _digest(_read_file(path))


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


def _inside(root, value):
    """Accept project-relative or absolute paths; reject symlink components."""
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
        raw = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ValueError(f'Cannot read regular source file: {path}') from exc
    identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                             info.st_mtime_ns, info.st_ctime_ns)
    if identity(before) != identity(after) or len(raw) != before.st_size:
        raise ValueError(f'Source file changed during verification: {path}')
    return raw


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


def _file_index(manifest, worktree):
    schema = manifest.get('schema_version')
    if 'schema_version' in manifest and schema != _SYNC_SCHEMA:
        raise ValueError(f'Unsupported deployment manifest schema: {schema!r}')
    files = manifest.get('files')
    if isinstance(files, dict):
        rows = files.items()
    elif isinstance(files, list):
        if any(not isinstance(row, dict) or 'path' not in row for row in files):
            raise ValueError('Malformed deployment manifest file rows')
        rows = ((row['path'], row) for row in files)
    else:
        raise ValueError('Deployment manifest files must be a map or a list')
    indexed = {}
    for name, entry in rows:
        name = _relative_name(name)
        _inside(worktree, name)
        if name in indexed:
            raise ValueError(f'Duplicate deployment manifest file path: {name}')
        if isinstance(entry, str) and schema == _SYNC_SCHEMA:
            digest, size = _hex(entry, 64, f'sha256 for {name}'), None
        elif isinstance(entry, dict):
            if 'path' in entry and entry['path'] != name:
                raise ValueError(f'Conflicting deployment manifest file path: {name}')
            digest = _hex(entry.get('sha256'), 64, f'sha256 for {name}')
            size = entry.get('bytes')
            if type(size) is not int or size < 0:
                raise ValueError(f'Invalid pinned byte count for {name}')
        else:
            raise ValueError(f'Malformed deployment manifest entry: {name}')
        indexed[name] = (digest, size)
    if 'v13_json_hashes' in manifest:
        shards = manifest['v13_json_hashes']
        if schema != _SYNC_SCHEMA or not isinstance(shards, dict):
            raise ValueError('v13_json_hashes requires the explicit sync schema and a map')
        for filename, digest in shards.items():
            if not isinstance(filename, str) or not re.fullmatch(r'[a-z0-9_]+\.json', filename):
                raise ValueError(f'Invalid v1.3 shard filename: {filename!r}')
            name = f'v1.3/{filename}'
            _inside(worktree, name)
            digest = _hex(digest, 64, f'sha256 for {name}')
            if name in indexed and indexed[name][0] != digest:
                raise ValueError(f'Conflicting deployment manifest shard: {name}')
            # Agreeing cross-map entries retain any genuinely pinned size.
            if name not in indexed:
                indexed[name] = (digest, None)
    return indexed


def _git(worktree, *args):
    env = os.environ.copy()
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE'):
        env.pop(key, None)
    env.update(GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    try:
        return subprocess.run(
            ['git', '--no-replace-objects', '--literal-pathspecs', '-c', 'protocol.allow=never',
             '-C', str(worktree), *args], check=True, capture_output=True, env=env).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f'Cannot read pinned local Git source ({args[0]}): {worktree}') from exc


def verified_sources(project: Path, worktree: Path, sources: list[str],
                     source_commit: str, extra_files: tuple[str, ...] = ()) -> tuple[dict, dict]:
    """Verify and read only the requested inputs; never create a run directory.

    The full commit ID is mandatory. An existing active configuration must
    validate successfully; only its absence permits the local Git path. All
    declared commit/target fields must agree with the caller. Legacy records
    can omit active_code.source_commit and optional target fields.
    """
    source_commit = _hex(source_commit, 40, 'source_commit')
    if project.is_symlink():
        raise ValueError(f'Symlink is not allowed: {project}')
    project = project.resolve(strict=True)
    if not project.is_dir():
        raise ValueError(f'Expected a project directory: {project}')
    worktree = _inside(project, worktree)
    if not worktree.is_dir():
        raise ValueError(f'Expected a source worktree directory: {worktree}')
    if not isinstance(sources, list) or any(
            not isinstance(source, str) or not re.fullmatch(r'[a-z0-9_]+', source)
            for source in sources):
        raise ValueError('Invalid source names; expected a list of [a-z0-9_]+ names')
    if not isinstance(extra_files, tuple):
        raise ValueError('extra_files must be a tuple of relative file paths')
    names = [f'check_diff/{name}' for name in CORE_FILES]
    names.extend(f'v1.3/{source}.json' for source in sources)
    names.extend(_relative_name(name) for name in extra_files)
    if len(names) != len(set(names)):
        raise ValueError('Duplicate requested source file paths')
    paths = {name: _inside(worktree, name) for name in names}
    active_path = _inside(project, 'configs/active_code.json')
    if active_path.exists():
        active_raw = _read_file(active_path)
        active = _json_object(active_raw, 'active_code.json')
        manifest_path = _inside(project, active.get('code_manifest'))
        manifest_raw = _read_file(manifest_path)
        manifest = _json_object(manifest_raw, 'code_manifest.json')
        if _hex(manifest.get('source_commit'), 40, 'Manifest source_commit') != source_commit:
            raise ValueError('Deployment manifest source_commit differs from the authorized source_commit')
        if 'source_commit' in active and _hex(active['source_commit'], 40, 'Active source_commit') != source_commit:
            raise ValueError('Active configuration source_commit differs from the authorized source_commit')
        for label, record in (('Active configuration', active), ('Deployment manifest', manifest)):
            for field in ('worktree', 'target_relative'):
                if field in record and _inside(project, record[field]) != worktree:
                    raise ValueError(f'{label} targets a different source worktree')
        indexed = _file_index(manifest, worktree)
        missing = set(names) - indexed.keys()
        if missing:
            raise ValueError(f'Missing deployment manifest source entries: {sorted(missing)}')
        blobs = {}
        for name, path in paths.items():
            raw = _read_file(path)
            digest, pinned_size = indexed[name]
            if hashlib.sha256(raw).hexdigest() != digest or (
                    pinned_size is not None and len(raw) != pinned_size):
                raise ValueError(f'Deployment manifest hash/size mismatch: {name}')
            blobs[name] = raw
        evidence = {}
        for key, path, raw in (('active_code', active_path, active_raw),
                               ('code_manifest', manifest_path, manifest_raw)):
            blobs[f'deployment_provenance/{key}.json'] = raw
            evidence[key] = {'path': str(path), **_digest(raw)}
        return blobs, evidence

    git_marker = _inside(worktree, '.git')
    if not git_marker.exists():
        raise ValueError('Exported source requires configs/active_code.json and a verified code_manifest')
    if _git(worktree, 'cat-file', '-t', source_commit).strip() != b'commit':
        raise ValueError('source_commit must identify a local Git commit object')
    tree = _git(worktree, 'ls-tree', '-rz', source_commit, '--', *names)
    modes = {}
    for row in tree.split(b'\x00'):
        if row:
            metadata, name = row.split(b'\t', 1)
            mode, kind, _ = metadata.split()
            modes[os.fsdecode(name)] = (mode, kind)
    blobs = {}
    for name, path in paths.items():
        if modes.get(name) not in ((b'100644', b'blob'), (b'100755', b'blob')):
            raise ValueError(f'Expected a regular pinned Git source file: {name}')
        raw = _git(worktree, 'show', '--no-ext-diff', '--no-textconv', f'{source_commit}:{name}')
        if _read_file(path) != raw:
            raise ValueError(f'Working source differs from pinned commit: {name}')
        blobs[name] = raw
    return blobs, {}

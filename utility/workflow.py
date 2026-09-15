#!/usr/bin/env python3
"""Configured source-data preparation and frozen-run execution for the formal project."""

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


if not __package__ and Path(__file__).resolve().parent.name == 'utility':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = 'utility'


FROZEN_CONFIG = 'source_snapshot/deployment_provenance/project.json'
FROZEN_SOURCE_MANIFEST = 'source_snapshot/source_manifest.json'
IMMUTABLE_ROOTS = ('source_snapshot', 'controller_versions')
REQUIRED_IMMUTABLE = {
    FROZEN_CONFIG, FROZEN_SOURCE_MANIFEST,
    'source_snapshot/utility/token_usage.py',
    *(f'source_snapshot/check_diff/{name}' for name in (
        'runner.py', 'schema.py', 'prompts/compare.md',
        'prompts/rubric.md', 'README.md', 'requirements.txt')),
    *(f'source_snapshot/{name}' for name in (
        'prepare_source_data.py', 'prepare_input_data.py', 'source_provenance.py',
        'prepare_batch.py', 'workflow.py')),
    *(f'controller_versions/bundle/{name}' for name in (
        'local_batch.py', 'completed_turn.py', 'verify_local_batch.py')),
}


def read_object(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return value


def inside(root, value):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(f'Missing path inside {root}')
    path = (root / value).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'Path must stay inside {root}: {value}')
    return path


def overlaps(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


def identifier(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value)
            or '..' in value or value.lower() in ('archive', '.migration')):
        raise ValueError(f'Unsafe or reserved run ID: {value!r}')
    return value


def runtime_paths(project, runtime):
    result = {}
    for key in ('python', 'codex'):
        path = Path(runtime[key])
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f'runtime.{key} must name an existing absolute executable: {path}')
        result[key] = str(path.resolve())
    entries = runtime['pythonpath']
    if not isinstance(entries, list) or not entries:
        raise ValueError('runtime.pythonpath must be a nonempty list of dependency directories')
    paths = []
    for entry in entries:
        if not isinstance(entry, str) or not entry:
            raise ValueError('Invalid runtime.pythonpath entry')
        path = Path(entry)
        path = path.resolve() if path.is_absolute() else inside(project, path)
        if not path.is_dir():
            raise ValueError(f'Missing runtime.pythonpath directory: {path}')
        paths.append(str(path))
    result['pythonpath'] = paths
    return result


def configuration(project, *, require_inputs=True):
    project = project.expanduser().resolve(strict=True)
    config_path = inside(project, 'utility/project.json')
    raw = read_object(config_path)
    if raw['schema_version'] != 'reviewer-project-1' or type(raw['paused']) is not bool:
        raise ValueError('Expected reviewer-project-1 with an explicit boolean paused flag')
    result = {'project': project, 'config': config_path, 'paused': raw['paused'],
              'comparison': raw['comparison'], 'runtime': raw['runtime']}
    for key in ('model', 'reasoning_effort', 'workers', 'validation_workers', 'timeout_seconds'):
        if key not in result['comparison']:
            raise ValueError(f'Missing comparison.{key}')
    for key in ('index', 'pdf_root', 'metadata_root'):
        result[key] = inside(project, raw['data'][key])
        if require_inputs and not (result[key].is_file() if key == 'index' else result[key].is_dir()):
            raise ValueError(f'Missing configured data path: {result[key]}')
    # prepare_batch and source_provenance share this formal source-index contract.
    if result['index'] != (project / 'data/index/index.json').resolve():
        raise ValueError('Configured index must be data/index/index.json; alternate inputs are unsupported')
    if (project / raw['data']['pdf_root']).is_symlink():
        raise ValueError('Configured PDF root must be a real project directory')
    result['source_manifest'] = inside(project, raw['source_manifest'])
    if result['source_manifest'] != project / 'utility/source_manifest.json':
        raise ValueError('source_manifest must identify utility/source_manifest.json')
    for key in ('runs_root', 'html_root'):
        root = result[key] = inside(project, raw['outputs'][key])
        protected = [project / name for name in ('utility', 'check_diff', 'review_experiment', 'tests', 'data', 'data_full')]
        protected += [result['index'].parent, result['pdf_root'], result['metadata_root']]
        if any(overlaps(root, path.resolve()) for path in protected):
            raise ValueError(f'Output root overlaps source/data: {root}')
        formal_root = project / ('res' if key == 'runs_root' else 'html')
        if not root.is_relative_to(formal_root):
            raise ValueError(f'{key} must stay under {formal_root}')
        if any(root.is_relative_to(project / 'res' / name) for name in ('archive', '.migration')):
            raise ValueError(f'Output root cannot use archived or migration evidence: {root}')
        if root.exists() and not root.is_dir():
            raise ValueError(f'Output root is not a directory: {root}')
    if overlaps(result['runs_root'], result['html_root']):
        raise ValueError('runs_root and html_root must be disjoint')
    return result


def run_path(config, run_id, *, fresh=False, must_exist=True):
    root = config['runs_root']
    run_id = identifier(run_id)
    raw = root / run_id
    run = inside(root, run_id)
    if raw.is_symlink() or run.parent != root:
        raise ValueError('Run ID must name a direct directory, not a link or nested path')
    if fresh and run.exists():
        raise ValueError(f'Run already exists: {run}')
    if must_exist and not fresh and not run.is_dir():
        raise ValueError(f'Run does not exist: {run}')
    return run


def local_module(name):
    if name == 'prepare_batch' and (Path(__file__).resolve().parent / 'batch').is_dir():
        name = 'batch.prepare_batch'
    return importlib.import_module(f'{__package__}.{name}' if __package__ else name)


def emit(**value):
    print(json.dumps(value, default=str, sort_keys=True), flush=True)


def source_revision(manifest):
    revision = manifest['revision']
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{64}', revision):
        raise ValueError('Manifest revision must be a pinned 64-character SHA-256 revision')
    return revision


def prepare_run(args, config):
    run = run_path(config, args.run_id, fresh=True)
    runtime = runtime_paths(config['project'], config['runtime'])
    manifest = read_object(config['source_manifest'])
    revision = source_revision(manifest)
    catalog = getattr(args, 'catalog', 'data')
    if catalog not in ('data', 'data_full'):
        raise ValueError(f'Unsupported source catalog: {catalog}')
    index = inside(config['project'], f'{catalog}/index/index.json')
    if not index.is_file():
        raise ValueError(f'Missing source index: {index}')
    pdf_root = config['pdf_root'] if catalog == 'data' else inside(config['project'], 'data_full/pdf')
    sources = list(dict.fromkeys(args.source))
    for name in sources:
        if not re.fullmatch(r'[a-z0-9_]+', name):
            raise ValueError(f'Invalid source: {name}')
        if catalog == 'data' and not inside(index.parent, f'{name}.json').is_file():
            raise ValueError(f'Missing source shard: {name}')
    for name in ('version_metadata.jsonl', 'provenance.json'):
        if not inside(config['metadata_root'], name).is_file():
            raise ValueError(f'Missing PDF metadata: {name}')
    values = {name: getattr(args, name) if getattr(args, name) is not None else config['comparison'][key]
              for name, key in (('model', 'model'), ('effort', 'reasoning_effort'), ('workers', 'workers'),
                                ('timeout', 'timeout_seconds'), ('validation_workers', 'validation_workers'))}
    if any(not isinstance(values[key], str) or not values[key].strip() for key in ('model', 'effort')):
        raise ValueError('Model and effort must be nonempty strings')
    for key, maximum in (('units', None), ('workers', 256), ('validation_workers', 16), ('timeout', None)):
        value = args.units if key == 'units' else values[key]
        if type(value) is not int or value < 1 or (maximum and value > maximum):
            raise ValueError(f'Invalid positive {key}: {value}')
    if args.concurrency_ladder is not None and (
            not isinstance(args.concurrency_ladder, list) or
            any(type(n) is not int for n in args.concurrency_ladder) or
            args.concurrency_ladder not in ([64, 48, 32, 16], [96, 80, 64, 48], [160, 128, 96, 80, 64, 48], [256, 224, 192, 160, 128, 96, 80, 64, 48]) or
            values['workers'] != args.concurrency_ladder[0]):
        raise ValueError('--concurrency-ladder requires 64 48 32 16, 96 80 64 48, 160 128 96 80 64 48, or 256 224 192 160 128 96 80 64 48, with --workers matching its first value')
    adapter_proof = local_module('source_provenance').proof(Path(__file__).resolve())
    if adapter_proof != manifest['files'].get('utility/workflow.py'):
        raise ValueError('Loaded workflow adapter differs from its source manifest entry')
    request = argparse.Namespace(project=config['project'], source_worktree=config['project'],
        source_revision=revision, run=run, runs_root=config['runs_root'], pdf_root=pdf_root, catalog=catalog,
        metadata_dir=config['metadata_root'], source=sources, units=args.units, codex=runtime['codex'],
        concurrency_ladder=args.concurrency_ladder, **values)
    result = local_module('prepare_batch').prepare(request)
    emit(command='prepare', status=result['status'], source_revision=revision, index=index, catalog=catalog,
         sources=sources,
         pdf_root=pdf_root, metadata_root=config['metadata_root'],
         run=run, prepared=run / 'prepared', output=run / 'output', model_execution_performed=False)
    return 0


def frozen_file(run, relative, manifest):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Frozen artifact paths must be relative to the run')
    path = inside(run, relative)
    if path != run / relative or path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or aliased frozen artifact: {relative}')
    expected = manifest['artifact_sha256'].get(relative)
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f'Missing SHA-256 pin for frozen artifact: {relative}')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError(f'Frozen artifact SHA-256 mismatch: {relative}')
    return path


def immutable_snapshot(run, manifest):
    """Check only the two immutable trees; operational run artifacts may change."""
    hashes = manifest['artifact_sha256']
    if not isinstance(hashes, dict):
        raise ValueError('Expected an immutable artifact hash mapping')
    expected = set()
    for name in hashes:
        relative = Path(name)
        if relative.parts and relative.parts[0] in IMMUTABLE_ROOTS:
            if relative.as_posix() != name or '..' in relative.parts:
                raise ValueError(f'Noncanonical immutable artifact path: {name}')
            expected.add(name)
    catalog = manifest.get('source_catalog', 'data')
    if catalog not in ('data', 'data_full'):
        raise ValueError(f'Unsupported frozen source catalog: {catalog}')
    required = REQUIRED_IMMUTABLE | {f'source_snapshot/{catalog}/index/index.json'}

    config = read_object(inside(run, 'run_config.json'))
    helper = 'source_snapshot/utility/codex_isolation.py'
    runner = run / 'source_snapshot/check_diff/runner.py'
    if ('isolation_policy' in config or helper in expected or
            (runner.is_file() and b'codex_isolation' in runner.read_bytes())):
        required.add(helper)
    if not required <= expected:
        raise ValueError(f'Missing required immutable pins: {sorted(required - expected)}')
    observed, directories = set(), set()
    for name in IMMUTABLE_ROOTS:
        root = run / name
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f'Expected a real immutable directory: {root}')
        for path in root.rglob('*'):
            if path.is_symlink():
                raise ValueError(f'Symlink in immutable snapshot: {path}')
            relative = path.relative_to(run).as_posix()
            if path.is_dir():
                directories.add(relative)
            elif path.is_file():
                observed.add(relative)
            else:
                raise ValueError(f'Nonregular immutable artifact: {path}')
    if observed != expected:
        raise ValueError(f'Immutable inventory mismatch: missing={sorted(expected - observed)}, '
                         f'extra={sorted(observed - expected)}')
    expected_directories = {parent.as_posix() for name in expected for parent in Path(name).parents
                            if parent.as_posix() not in (*IMMUTABLE_ROOTS, '.')}
    if directories != expected_directories:
        raise ValueError(f'Unexpected immutable directories: {sorted(directories - expected_directories)}')
    return {name: frozen_file(run, name, manifest) for name in sorted(expected)}


def require_dispatchable(config, run):
    if config['paused']:
        raise ValueError('Project is paused; workflow never changes authorization or pause')
    if read_object(inside(run, 'control.json')).get('pause_dispatch') is not False:
        raise ValueError('Run is paused; pause_dispatch must already be explicitly false')
    rows = [json.loads(line) for line in inside(run, 'pair_status.jsonl').read_text().splitlines() if line.strip()]
    if not rows:
        raise ValueError('Run has no frozen pair status rows')
    if all(row['status'] in ('completed', 'input_unavailable') for row in rows):
        raise ValueError('Run results are fully settled; rerun refused')


def dispatch(args, config):
    run = run_path(config, args.run_id)
    if args.command == 'run':
        require_dispatchable(config, run)
    manifest = read_object(inside(run, 'execution_manifest.json'))
    immutable = immutable_snapshot(run, manifest)
    frozen_config = immutable[FROZEN_CONFIG]
    frozen_source_manifest = immutable[FROZEN_SOURCE_MANIFEST]
    revision = source_revision(read_object(frozen_source_manifest))
    run_config = read_object(inside(run, 'run_config.json'))
    if revision != manifest['source_revision'] or revision != run_config['source_revision']:
        raise ValueError('Frozen manifest revision differs from the run source_revision')
    if (manifest.get('source_catalog', 'data') != run_config.get('source_catalog', 'data')
            or manifest.get('source_index') != run_config.get('source_index')):
        raise ValueError('Frozen catalog identity differs from the run configuration')
    chosen = manifest.get('source_index')
    if chosen is not None:
        path = immutable[f"source_snapshot/{manifest.get('source_catalog', 'data')}/index/index.json"]
        if ({'bytes': path.stat().st_size, 'sha256': manifest['artifact_sha256'][str(path.relative_to(run))]}
                != {key: chosen[key] for key in ('bytes', 'sha256')}):
            raise ValueError('Frozen source index differs from its recorded catalog identity')
    frozen = read_object(frozen_config)
    if frozen['schema_version'] != 'reviewer-project-1':
        raise ValueError('Unsupported historical run; restore its recorded environment separately')
    runtime = runtime_paths(config['project'], frozen['runtime'])
    key, filename = ('controller', 'local_batch.py') if args.command == 'run' else ('independent_verifier', 'verify_local_batch.py')
    if manifest[key] != f'controller_versions/bundle/{filename}':
        raise ValueError(f'Unsupported frozen entrypoint: {manifest[key]}')
    entry = immutable[manifest[key]]
    temporary = inside(run, 'tmp')
    if temporary != run / 'tmp' or (run / 'tmp').is_symlink():
        raise ValueError('Run scratch must be the real run/tmp directory')
    if temporary.exists() and not temporary.is_dir():
        raise ValueError(f'Run scratch is not a directory: {temporary}')
    argv = [runtime['python'], str(entry), '--run', str(run)]
    if args.command == 'run' and args.retry_failed:
        argv.append('--retry-failed')
    if args.command == 'verify' and args.require_complete:
        argv.append('--require-complete')
    env = os.environ.copy()
    env.update(PYTHONPATH=os.pathsep.join(runtime['pythonpath']), PYTHONDONTWRITEBYTECODE='1',
               TMPDIR=str(temporary), PATH=str(Path(runtime['codex']).parent) + os.pathsep + env.get('PATH', ''))
    if args.command == 'run':
        temporary.mkdir(exist_ok=True)
    emit(command=args.command, run=run, entrypoint=entry, frozen_config=frozen_config,
         frozen_source_manifest=frozen_source_manifest, source_revision=revision,
         runtime=runtime, output=run / 'output')
    return subprocess.run(argv, cwd=config['project'], env=env, check=False).returncode


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    commands = result.add_subparsers(dest='command', required=True)
    paths = commands.add_parser('paths', help='Read resolved project input/output paths')
    paths.add_argument('--run-id')
    commands.add_parser('freeze-sources', help='Explicitly write the source manifest and print its revision')
    prepare = commands.add_parser('prepare', help='Freeze an explicit scope without starting a model run')
    prepare.add_argument('--catalog', choices=('data', 'data_full'), default='data',
                         help='Select the original catalog or the base-plus-additions catalog')
    prepare.add_argument('--source', action='append', required=True)
    prepare.add_argument('--units', type=int, required=True)
    prepare.add_argument('--run-id', required=True)
    for name in ('model', 'effort'):
        prepare.add_argument(f'--{name}')
    prepare.add_argument('--workers', type=int, help='Comparison workers (1..256)')
    for name in ('validation-workers', 'timeout'):
        prepare.add_argument(f'--{name}', type=int)
    prepare.add_argument('--concurrency-ladder', type=int, nargs='+',
                         help='Opt into downward-only 64 48 32 16, 96 80 64 48, 160 128 96 80 64 48, or 256 224 192 160 128 96 80 64 48 admissions; --workers must match the first value')
    run = commands.add_parser('run', help='Use this new formal run frozen controller; respect both pauses')
    run.add_argument('--run-id', required=True)
    run.add_argument('--retry-failed', action='store_true')
    verify = commands.add_parser('verify', help='Use this new formal run SHA-pinned frozen verifier')
    verify.add_argument('--run-id', required=True)
    verify.add_argument('--require-complete', action='store_true')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = configuration(args.project, require_inputs=args.command not in ('run', 'verify'))
        if args.command == 'paths':
            config['runtime'] = runtime_paths(config['project'], config['runtime'])
            paths = {}
            if args.run_id:
                run = run_path(config, args.run_id, must_exist=False)
                paths = {'run': run, 'prepared': run / 'prepared', 'output': run / 'output',
                         'frozen_config': run / FROZEN_CONFIG,
                         'frozen_source_manifest': run / FROZEN_SOURCE_MANIFEST}
            emit(command='paths', **config, **paths)
            return 0
        if args.command == 'freeze-sources':
            runtime_paths(config['project'], config['runtime'])
            manifest = local_module('source_provenance').write_manifest(config['project'])
            emit(command='freeze-sources', source_manifest=config['source_manifest'],
                 source_revision=source_revision(manifest))
            return 0
        if args.command == 'prepare':
            return prepare_run(args, config)
        return dispatch(args, config)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({'command': args.command, 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

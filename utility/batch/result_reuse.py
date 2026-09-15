#!/usr/bin/env python3
"""Create and verify read-only references to compatible frozen pair judgments.

This command never dispatches models, changes batch state, imports old attempts,
or adds historical consumption to a new run. Missing evidence is incompatible.
"""
import argparse
import json
from pathlib import Path

if __package__:
    from . import verify_local_batch as audit
else:
    import verify_local_batch as audit


SCHEMA = 'verified-pair-result-reference-1'
PROMPT_EQUIVALENCE = 'frozen-endpoint-path-parameters-1'
FROZEN_CODE = (
    'source_snapshot/check_diff/runner.py',
    'source_snapshot/check_diff/schema.py',
    'source_snapshot/check_diff/prompts/compare.md',
    'source_snapshot/check_diff/prompts/rubric.md',
    'source_snapshot/prepare_source_data.py',
    'source_snapshot/prepare_input_data.py',
    'source_snapshot/utility/token_usage.py',
    'controller_versions/bundle/local_batch.py',
    'controller_versions/bundle/completed_turn.py',
)


def _command(config, work):
    """The supported isolated invocation; unknown command profiles fail closed."""
    return [config['codex_path'], 'exec', '--json', '--ignore-user-config', '--ephemeral',
            '--skip-git-repo-check', '--sandbox', 'workspace-write',
            '--model', config['model'], '-c', f'model_reasoning_effort="{config["reasoning_effort"]}"',
            '-c', 'web_search="disabled"', '-c', 'sandbox_workspace_write.network_access=false',
            '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"',
            '--cd', str(work), '--color', 'never', '--output-schema', str(work / 'schema.json'),
            '--output-last-message', str(work / 'response.json'), '-']


def _frozen_code(verifier):
    paths = set(FROZEN_CODE)
    for folder in ('source_snapshot', 'controller_versions/bundle'):
        for path in (verifier.run / folder).rglob('*'):
            if path.is_symlink():
                raise ValueError(f'Symlink frozen execution dependency: {path}')
            if path.is_file() and path.suffix in {'.py', '.md'}:
                paths.add(str(path.relative_to(verifier.run)))
    return {name: verifier.reader.sha(verifier.run / name) for name in sorted(paths)}


def _resources(verifier):
    """Require frozen resource bytes; a caller-supplied revision string is insufficient."""
    proof = verifier.config.get('execution_resource_proof')
    if (not isinstance(proof, dict) or set(proof) != {'path', 'sha256', 'bytes'}
            or proof.get('path') != 'execution_resources/manifest.json'):
        raise ValueError('Missing frozen execution resource proof; historical resources cannot be inferred')
    root, reader = verifier.run / 'execution_resources', verifier.reader
    raw = reader.blob(root / 'manifest.json')
    if (type(proof['bytes']) is not int or proof['bytes'] != len(raw)
            or proof['sha256'] != audit.digest(raw)):
        raise ValueError('Execution resource manifest hash or size mismatch')
    manifest = audit.decode(raw)
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'files'}
            or manifest.get('schema_version') != 'frozen-execution-resources-1'
            or not isinstance(manifest.get('files'), dict)):
        raise ValueError('Unsupported frozen execution resource manifest')
    files = manifest['files']
    if not {'runtime-policy.json', 'pdf-skill/SKILL.md'} <= files.keys():
        raise ValueError('Resource proof must include the runtime policy and installed PDF skill')
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Symlink execution resource: {path}')
        if path.is_file() and path != root / 'manifest.json':
            actual.add(str(path.relative_to(root)))
    if actual != set(files):
        raise ValueError('Execution resource archive does not match its complete file inventory')
    for name, expected in files.items():
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != name
                or not isinstance(expected, dict) or set(expected) != {'sha256', 'bytes'}):
            raise ValueError(f'Invalid archived execution resource proof: {name}')
        data = reader.blob(root / relative)
        if (type(expected['bytes']) is not int or expected['bytes'] != len(data)
                or expected['sha256'] != audit.digest(data)):
            raise ValueError(f'Archived execution resource changed: {name}')
    policy = reader.json(root / 'runtime-policy.json')
    if (not isinstance(policy, dict) or set(policy) != {
            'schema_version', 'environment_file', 'codex_executable', 'runtime_dependencies'}
            or policy.get('schema_version') != 'execution-runtime-policy-1'):
        raise ValueError('Missing or unsupported archived runtime policy identity')
    executable, environment, dependencies = policy['codex_executable'], policy['environment_file'], policy['runtime_dependencies']
    if (not isinstance(executable, dict) or set(executable) != {'invoked_path', 'archived_path'}
            or executable['invoked_path'] != verifier.config['codex_path']
            or not isinstance(executable['archived_path'], str) or not executable['archived_path'].startswith('runtime/')
            or executable['archived_path'] not in files or files[executable['archived_path']]['bytes'] < 1
            or not isinstance(environment, str) or not environment.startswith('runtime/') or environment not in files
            or not isinstance(dependencies, list) or not dependencies
            or any(not isinstance(name, str) or not name.startswith('runtime/') for name in dependencies)
            or len(dependencies) != len(set(dependencies))):
        raise ValueError('Resource policy must pin executable bytes, effective environment, and runtime dependencies')
    if environment == executable['archived_path'] or set(dependencies) & {environment, executable['archived_path']}:
        raise ValueError('Runtime evidence roles must identify distinct archived files')
    runtime = {name for name in files if not name.startswith('pdf-skill/') and name != 'runtime-policy.json'}
    if runtime != {executable['archived_path'], environment, *dependencies}:
        raise ValueError('Runtime dependency inventory does not match archived resources')
    environment_values = reader.json(root / environment)
    if (not isinstance(environment_values, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in environment_values.items())):
        raise ValueError('Invalid archived effective runtime environment')
    return proof


def _context(run, pair_key):
    verifier = audit.Verifier(run)
    verifier.load_scope()
    if (any(name.startswith('isolation_') for name in verifier.config)
            or (verifier.run / 'source_snapshot/utility/codex_isolation.py').exists()):
        raise ValueError('Unsupported isolation policy: explicit helper, resource, and command verification is required')
    if verifier.errors:
        raise ValueError(f'Invalid frozen run: {verifier.errors}')
    if pair_key not in verifier.pairs:
        raise ValueError(f'Pair is outside the frozen selected scope: {pair_key}')
    pair, reader = verifier.pairs[pair_key], verifier.reader
    folder = verifier.plan_path.parent / pair['arxiv_id'] / f'v{pair["before"]}_v{pair["after"]}'
    before, after = folder / f'v{pair["before"]}.pdf', folder / f'v{pair["after"]}.pdf'
    prompt_root = verifier.run / 'source_snapshot/check_diff/prompts'
    template = reader.blob(prompt_root / 'compare.md').decode()
    rubric = reader.blob(prompt_root / 'rubric.md').decode()
    context = {'arxiv_id': pair['arxiv_id'], 'before_filename': before.name, 'before_path': before,
               'after_filename': after.name, 'after_path': after}
    prompt = template.format(**context) + '\n' + rubric + '\n'
    # Re-render only the two documented path parameters; never replace text in an archived prompt.
    normalized = template.format(**{**context,
        'before_path': f"<selected-original-pdf:{pair['arxiv_id']}:v{pair['before']}>",
        'after_path': f"<selected-original-pdf:{pair['arxiv_id']}:v{pair['after']}>"}) + '\n' + rubric + '\n'
    inputs = {}
    for requested in pair['pdf_ids']:
        pdf = verifier.inventory[requested]['pdf_observation']['pdf']
        if pdf['availability_status'] != 'verified':
            raise ValueError(f'Exact original PDF hash is not verified: {requested}')
        inputs[requested] = {'sha256': pdf['sha256'], 'bytes': pdf['bytes']}
    fingerprint = {
        'pair': {k: pair[k] for k in ('arxiv_id', 'before', 'after')},
        'input_pdfs': inputs,
        'frozen_code_sha256': _frozen_code(verifier),
        'execution_settings': {key: value for key, value in verifier.config.items() if key not in {
            'run_id', 'created_at_utc', 'source_names', 'unit_count', 'unit_ids', 'prepared_path',
            'pdf_root', 'metadata_ledger', 'source_revision', 'previous_judgments_reused',
            'source_catalog', 'source_index', 'fallback_pdf_roots'}},
        'execution_resource_proof': _resources(verifier),
        'prompt_equivalence': PROMPT_EQUIVALENCE,
        'normalized_prompt_sha256': audit.digest(normalized.encode()),
        'invocation': _command(verifier.config, Path('/__isolated_pair_work__')),
    }
    return verifier, fingerprint, audit.digest(prompt.encode())


def _proof(reader):
    if reader.changes():
        raise ValueError('Frozen evidence changed during verification')
    return {str(path): digest for path, digest in sorted(reader.hashes.items())}


def build_reference(source_run, target_run, pair_key):
    """Verify one source pair and both frozen contexts, without writing anything."""
    source, source_context, source_prompt = _context(source_run, pair_key)
    target, target_context, target_prompt = _context(target_run, pair_key)
    mismatch = [name for name in source_context if source_context[name] != target_context[name]]
    if mismatch:
        raise ValueError('Incompatible pair evidence: ' + ', '.join(mismatch))
    pair = source.pairs[pair_key]
    outcome, _ = source.audit_pair(pair)
    if source.errors or outcome['status'] != 'completed':
        raise ValueError(f'Source pair has no verified completed result: {source.errors or outcome["status"]}')
    output = source.run / 'output' / pair['arxiv_id'] / f'diff_v{pair["before"]}_v{pair["after"]}.json'
    receipt_path = output.with_suffix('.execution.json')
    receipt = source.reader.json(receipt_path)
    attempt = source.run / 'attempts' / pair_key / f'attempt-{receipt["attempt"]:03d}'
    proof = source_context['execution_resource_proof']
    original = source.reader.json(attempt / 'receipt.json')
    if (original.get('execution_resource_proof') != proof
            or original.get('artifact_sha256', {}).get('execution_resources.json') != proof['sha256']
            or source.reader.sha(attempt / 'execution_resources.json') != proof['sha256']):
        raise ValueError('Execution resource proof is not bound to the original successful attempt receipt')
    work = attempt / 'work'
    command = source.reader.json(attempt / 'command.json')
    if command != {'argv': _command(source.config, work), 'working_directory': str(work)}:
        raise ValueError('Source invocation is not the supported isolated command profile')
    if receipt.get('prompt_sha256') != source_prompt:
        raise ValueError('Source receipt does not prove the exact rendered prompt')
    return {
        'schema_version': SCHEMA, 'pair_key': pair_key,
        'fingerprint': source_context,
        'source': {'run': str(source.run), 'run_id': source.config['run_id'],
                   'result_path': str(output), 'receipt_path': str(receipt_path),
                   'rendered_prompt_sha256': source_prompt},
        'target': {'run': str(target.run), 'run_id': target.config['run_id'],
                   'rendered_prompt_sha256': target_prompt},
        'source_artifact_sha256': _proof(source.reader),
        'target_artifact_sha256': _proof(target.reader),
        'historical_token_usage': receipt['token_usage'],
        'reference_operation': {'model_calls': 0, 'new_attempt_count': 0},
    }


def create_reference(source_run, target_run, pair_key, output):
    reference = build_reference(source_run, target_run, pair_key)
    output = Path(output).expanduser().resolve()
    if output.is_relative_to(Path(reference['source']['run'])):
        raise ValueError('Reference output must be outside the immutable source run')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(reference, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return reference


def load_reference(path):
    """Return the original judgment and historical usage only after revalidation."""
    reader = audit.Reader()
    reference = reader.json(Path(path).expanduser().resolve())
    if reference.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported result reference schema')
    for group in ('source_artifact_sha256', 'target_artifact_sha256'):
        for name, expected in reference[group].items():
            if reader.sha(Path(name)) != expected:
                raise ValueError(f'Referenced evidence changed: {name}')
    verified = build_reference(reference['source']['run'], reference['target']['run'], reference['pair_key'])
    if verified != reference:
        raise ValueError('Reference no longer agrees with verified frozen pair evidence')
    result = reader.json(Path(reference['source']['result_path']))
    _proof(reader)
    return {'result': result, 'source': reference['source'],
            'historical_token_usage': reference['historical_token_usage'],
            'reference_operation': reference['reference_operation']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'create'):
        command = commands.add_parser(name)
        command.add_argument('--source-run', required=True, type=Path)
        command.add_argument('--target-run', required=True, type=Path)
        command.add_argument('--pair', required=True)
        if name == 'create':
            command.add_argument('--output', required=True, type=Path)
    command = commands.add_parser('load')
    command.add_argument('reference', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'load':
            result = load_reference(args.reference)
        elif args.command == 'create':
            reference = create_reference(args.source_run, args.target_run, args.pair, args.output)
            result = {'compatible': True, 'reference': str(args.output.resolve()),
                      'reference_operation': reference['reference_operation']}
        else:
            reference = build_reference(args.source_run, args.target_run, args.pair)
            result = {'compatible': True, 'pair_key': reference['pair_key'], 'reasons': []}
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        print(json.dumps({'compatible': False, 'reasons': [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

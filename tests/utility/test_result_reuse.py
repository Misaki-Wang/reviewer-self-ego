"""Offline evidence tests for explicit pair-result references; no model calls."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from tests.mbz import test_verify_local_batch as fixtures
from utility import result_reuse as reuse

# Deliberately freeze a legacy invocation; current runners require modern helper proof.
LEGACY_RUNNER_SOURCE = """def build_command(config, work):
    return [config['codex_path'], 'exec', '--json', '--ignore-user-config', '--ephemeral',
            '--skip-git-repo-check', '--sandbox', 'workspace-write',
            '--model', config['model'], '-c', f'model_reasoning_effort="{config["reasoning_effort"]}"',
            '-c', 'web_search="disabled"', '-c', 'sandbox_workspace_write.network_access=false',
            '-c', 'project_doc_max_bytes=0', '-c', 'approval_policy="never"',
            '--cd', str(work), '--color', 'never', '--output-schema', str(work / 'schema.json'),
            '--output-last-message', str(work / 'response.json'), '-']
"""


class ResultReuseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.VerifierTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.source = f.run
        for name in reuse.FROZEN_CODE:
            path = self.source / name
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # These unexecuted archive dependencies must not inherit current runtime policy.
            path.write_text(LEGACY_RUNNER_SOURCE if name == 'source_snapshot/check_diff/runner.py'
                            else f'# Synthetic frozen legacy dependency: {name}\n')
        self.archive_resources()
        f.complete(usage=None, retry=True)
        self.rewrite_commands()
        self.target = f.base / 'new-run'
        shutil.copytree(self.source, self.target)
        shutil.rmtree(self.target / 'attempts')
        shutil.rmtree(self.target / 'output')
        config = fixtures.verify.decode((self.target / 'run_config.json').read_bytes())
        self.target_prepared = f.base / 'new-prepared'
        shutil.copytree(f.prepared, self.target_prepared)
        config.update(run_id='new-run', source_catalog='data_full', prepared_path=str(self.target_prepared))
        fixtures.write(self.target / 'run_config.json', config)
        scope = json.loads((self.target / 'scope_input.json').read_text())
        scope['catalog_identity'] = 'data_full'
        fixtures.write(self.target / 'scope_input.json', scope)
        plan = json.loads((self.target_prepared / 'selection_manifest.json').read_text())
        plan['source_sha256'] = fixtures.sha(self.target / 'scope_input.json')
        fixtures.write(self.target_prepared / 'selection_manifest.json', plan)
        contract = json.loads((self.target / 'execution_contract.json').read_text())
        contract['execution_signature'] = fixtures.verify.signature(config, fixtures.verify.Reader(), self.target)
        fixtures.write(self.target / 'execution_contract.json', contract)
        self.key = f.pairs[0]['key']
        self.output = self.target / 'references' / f'{self.key}.json'

    def archive_resources(self):
        f = self.fixture
        root = self.source / 'execution_resources'
        (root / 'pdf-skill').mkdir(parents=True)
        (root / 'runtime').mkdir()
        (root / 'pdf-skill/SKILL.md').write_text('Offline fixture PDF reading instructions.\n')
        (root / 'runtime/codex').write_text('Offline fixture executable bytes.\n')
        fixtures.write(root / 'runtime/environment.json', {'PATH': '/offline/runtime'})
        (root / 'runtime/interpreter').write_text('Offline fixture dependency bytes.\n')
        fixtures.write(root / 'runtime-policy.json', {'schema_version': 'execution-runtime-policy-1',
            'environment_file': 'runtime/environment.json',
            'codex_executable': {'invoked_path': f.config['codex_path'], 'archived_path': 'runtime/codex'},
            'runtime_dependencies': ['runtime/interpreter']})
        manifest = {'schema_version': 'frozen-execution-resources-1', 'files': {
            str(path.relative_to(root)): {'bytes': path.stat().st_size, 'sha256': fixtures.sha(path)}
            for path in root.rglob('*') if path.is_file()}}
        fixtures.write(root / 'manifest.json', manifest)
        self.resource_proof = {'path': 'execution_resources/manifest.json',
            'sha256': fixtures.sha(root / 'manifest.json'), 'bytes': (root / 'manifest.json').stat().st_size}
        f.config['execution_resource_proof'] = self.resource_proof
        f.freeze()

    def rewrite_commands(self):
        f = self.fixture
        for receipt in f.archived[f.pairs[0]['key']]:
            root = f.attempt_path(number=receipt['attempt'])
            work = root / 'work'
            legacy = {}
            exec(compile(LEGACY_RUNNER_SOURCE, '<legacy-runner-fixture>', 'exec'), legacy)
            fixtures.write(root / 'command.json', {'argv': legacy['build_command'](f.config, work),
                                                 'working_directory': str(work)})
            receipt['artifact_sha256']['command.json'] = fixtures.sha(root / 'command.json')
            shutil.copyfile(self.source / 'execution_resources/manifest.json', root / 'execution_resources.json')
            receipt['execution_resource_proof'] = self.resource_proof
            receipt['artifact_sha256']['execution_resources.json'] = fixtures.sha(root / 'execution_resources.json')
            fixtures.write(root / 'receipt.json', receipt)
        saved_path = f.path().with_suffix('.execution.json')
        saved = fixtures.verify.decode(saved_path.read_bytes())
        saved.update(f.archived[f.pairs[0]['key']][-1])
        saved['attempts'] = f.archived[f.pairs[0]['key']]
        saved['token_usage'] = fixtures.verify.summarize(saved['attempts'])
        fixtures.write(saved_path, saved)

    def test_reference_load_returns_original_result_without_new_attempts_or_usage(self):
        before = {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Model dispatch is forbidden')):
            reference = reuse.create_reference(self.source, self.target, self.key, self.output)
            loaded = reuse.load_reference(self.output)
        self.assertEqual(loaded['result'], self.fixture.result)
        self.assertNotIn('result', reference)
        self.assertFalse((self.target / 'attempts').exists())
        self.assertFalse((self.target / 'output').exists())
        self.assertEqual(reference['reference_operation'], {'model_calls': 0, 'new_attempt_count': 0})
        self.assertEqual(loaded['historical_token_usage']['attempt_count'], 2)
        self.assertEqual(loaded['historical_token_usage']['status'], 'unavailable')
        self.assertIsNone(loaded['historical_token_usage']['reported_totals']['input_tokens'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()})

    def test_only_proven_endpoint_path_parameters_are_equivalent(self):
        reference = reuse.build_reference(self.source, self.target, self.key)
        self.assertEqual(reference['fingerprint']['prompt_equivalence'], reuse.PROMPT_EQUIVALENCE)
        self.assertNotEqual(reference['source']['rendered_prompt_sha256'], reference['target']['rendered_prompt_sha256'])
        self.assertNotEqual(json.loads((self.source / 'execution_contract.json').read_text())['execution_signature'],
                            json.loads((self.target / 'execution_contract.json').read_text())['execution_signature'])
        template = self.target / 'source_snapshot/check_diff/prompts/compare.md'
        original = template.read_text()
        for context in (f' Literal path context: {self.target_prepared}', ' Additional paper context: 2601.00002.'):
            template.write_text(original + context)
            config = json.loads((self.target / 'run_config.json').read_text())
            contract = json.loads((self.target / 'execution_contract.json').read_text())
            contract['execution_signature'] = fixtures.verify.signature(config, fixtures.verify.Reader(), self.target)
            fixtures.write(self.target / 'execution_contract.json', contract)
            with self.subTest(context=context), self.assertRaisesRegex(ValueError, 'frozen_code|normalized_prompt'):
                reuse.build_reference(self.source, self.target, self.key)

    def test_unexplained_archived_prompt_is_rejected_even_with_rehashed_receipts(self):
        f = self.fixture
        receipt = f.archived[f.pairs[0]['key']][-1]
        root = f.attempt_path(number=receipt['attempt'])
        prompt = root / 'prompt.txt'
        prompt.write_text(prompt.read_text() + ' Extra unproven paper context.')
        receipt['prompt_sha256'] = fixtures.sha(prompt)
        receipt['artifact_sha256']['prompt.txt'] = fixtures.sha(prompt)
        fixtures.write(root / 'receipt.json', receipt)
        saved = json.loads(f.path().with_suffix('.execution.json').read_text())
        saved.update(receipt)
        saved['attempts'] = f.archived[f.pairs[0]['key']]
        saved['token_usage'] = fixtures.verify.summarize(saved['attempts'])
        fixtures.write(f.path().with_suffix('.execution.json'), saved)
        with self.assertRaisesRegex(ValueError, 'signed_prompt'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_source_result_deletion_and_tamper_are_rejected(self):
        reuse.create_reference(self.source, self.target, self.key, self.output)
        path = self.fixture.path()
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaises((OSError, ValueError)):
            reuse.load_reference(self.output)
        path.write_bytes(raw + b' ')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            reuse.load_reference(self.output)

    def test_controller_and_dependency_changes_are_incompatible(self):
        for name in ('controller_versions/bundle/local_batch.py', 'source_snapshot/check_diff/runner.py',
                     'source_snapshot/check_diff/schema.py', 'source_snapshot/check_diff/prompts/rubric.md'):
            path = self.target / name
            old = path.read_bytes()
            path.write_bytes(old + b'\n# Modified dependency\n')
            with self.subTest(name=name), self.assertRaises(ValueError):
                reuse.build_reference(self.source, self.target, self.key)
            path.write_bytes(old)
        extra = self.target / 'source_snapshot/utility/new_execution_helper.py'
        extra.write_text('POLICY = "changed"\n')
        with self.assertRaisesRegex(ValueError, 'frozen_code'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_unknown_isolation_policy_or_missing_helper_proof_is_rejected(self):
        path = self.target / 'run_config.json'
        original = path.read_bytes()
        config = json.loads(original)
        config['isolation_policy'] = {'version': 'codex-file-isolation-v1', 'sha256': '0' * 64}
        fixtures.write(path, config)
        with self.assertRaisesRegex(ValueError, r'Expected a regular.*codex_isolation\.py'):
            reuse.build_reference(self.source, self.target, self.key)
        path.write_bytes(original)
        dependency = self.target / 'source_snapshot/utility/codex_isolation.py'
        dependency.write_text('POLICY_VERSION = "unknown"\n')
        with self.assertRaisesRegex(ValueError, 'Frozen isolation policy identity mismatch'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_missing_or_unbound_resource_evidence_is_rejected(self):
        path = self.target / 'run_config.json'
        original = path.read_bytes()
        config = json.loads(original)
        del config['execution_resource_proof']
        fixtures.write(path, config)
        with self.assertRaisesRegex(ValueError, 'Missing frozen execution resource proof'):
            reuse.build_reference(self.source, self.target, self.key)
        path.write_bytes(original)
        f = self.fixture
        receipt = f.archived[f.pairs[0]['key']][-1]
        root = f.attempt_path(number=receipt['attempt'])
        del receipt['execution_resource_proof']
        fixtures.write(root / 'receipt.json', receipt)
        saved = json.loads(f.path().with_suffix('.execution.json').read_text())
        del saved['execution_resource_proof']
        saved['attempts'] = f.archived[f.pairs[0]['key']]
        fixtures.write(f.path().with_suffix('.execution.json'), saved)
        with self.assertRaisesRegex(ValueError, 'not bound to the original'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_resource_bytes_and_runtime_policy_are_verified(self):
        reuse.create_reference(self.source, self.target, self.key, self.output)
        skill = self.source / 'execution_resources/pdf-skill/SKILL.md'
        original = skill.read_bytes()
        skill.write_bytes(original + b'Mutable installed skill changed.')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            reuse.load_reference(self.output)
        with self.assertRaisesRegex(ValueError, 'execution resource changed'):
            reuse.build_reference(self.source, self.target, self.key)
        skill.write_bytes(original)
        root = self.target / 'execution_resources'
        fixtures.write(root / 'runtime-policy.json', {'schema_version': 'execution-runtime-policy-1'})
        manifest = json.loads((root / 'manifest.json').read_text())
        policy = root / 'runtime-policy.json'
        manifest['files']['runtime-policy.json'] = {'sha256': fixtures.sha(policy), 'bytes': policy.stat().st_size}
        fixtures.write(root / 'manifest.json', manifest)
        config = json.loads((self.target / 'run_config.json').read_text())
        config['execution_resource_proof'].update(sha256=fixtures.sha(root / 'manifest.json'),
                                                 bytes=(root / 'manifest.json').stat().st_size)
        fixtures.write(self.target / 'run_config.json', config)
        with self.assertRaisesRegex(ValueError, 'unsupported archived runtime policy'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_model_and_input_hash_changes_are_incompatible(self):
        for field in ('model', 'reasoning_effort'):
            path = self.target / 'run_config.json'
            old = path.read_bytes()
            config = json.loads(old)
            config[field] = 'different'
            fixtures.write(path, config)
            with self.subTest(field=field), self.assertRaises(ValueError):
                reuse.build_reference(self.source, self.target, self.key)
            path.write_bytes(old)
        path = self.target / 'version_inventory.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]['pdf_observation']['pdf']['sha256'] = '0' * 64
        fixtures.rows(path, rows)
        with self.assertRaisesRegex(ValueError, 'input_pdfs'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_schema_only_output_is_not_reusable(self):
        shutil.rmtree(self.source / 'attempts')
        with self.assertRaisesRegex(ValueError, 'verified completed'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_unexpected_invocation_is_rejected_even_with_rehashed_receipts(self):
        f = self.fixture
        receipt = f.archived[f.pairs[0]['key']][-1]
        root = f.attempt_path(number=receipt['attempt'])
        command = json.loads((root / 'command.json').read_text())
        command['argv'].extend(['--add-dir', '/unrelated-context'])
        fixtures.write(root / 'command.json', command)
        receipt['artifact_sha256']['command.json'] = fixtures.sha(root / 'command.json')
        fixtures.write(root / 'receipt.json', receipt)
        saved = json.loads(f.path().with_suffix('.execution.json').read_text())
        saved.update(receipt)
        saved['attempts'] = f.archived[f.pairs[0]['key']]
        saved['token_usage'] = fixtures.verify.summarize(saved['attempts'])
        fixtures.write(f.path().with_suffix('.execution.json'), saved)
        with self.assertRaisesRegex(ValueError, 'isolated command'):
            reuse.build_reference(self.source, self.target, self.key)

    def test_reference_changes_and_source_run_destination_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside the immutable source'):
            reuse.create_reference(self.source, self.target, self.key, self.source / 'reference.json')
        reuse.create_reference(self.source, self.target, self.key, self.output)
        reference = json.loads(self.output.read_text())
        reference['historical_token_usage']['attempt_count'] = 0
        fixtures.write(self.output, reference)
        with self.assertRaisesRegex(ValueError, 'no longer agrees'):
            reuse.load_reference(self.output)

    def test_cli_check_create_and_load(self):
        args = ['--source-run', str(self.source), '--target-run', str(self.target), '--pair', self.key]
        for argv in (['check', *args], ['create', *args, '--output', str(self.output)], ['load', str(self.output)]):
            with self.subTest(command=argv[0]), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(reuse.main(argv), 0)
                self.assertIsInstance(json.loads(output.getvalue()), dict)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(reuse.main(['check', *args[:-1], 'outside__v1_v2']), 1)
            self.assertIn('outside the frozen', json.loads(output.getvalue())['reasons'][0])


if __name__ == '__main__':
    unittest.main()

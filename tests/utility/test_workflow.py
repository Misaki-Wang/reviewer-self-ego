"""Offline workflow contract tests; all preparation and subprocess calls are mocked."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from utility import source_provenance, workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='formal-workflow-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        for name in ('utility/.runtime/current', 'utility/.runtime/frozen', 'utility/vendor',
                     'data/index/metadata', 'data/pdf', 'check_diff', 'tests', 'review_experiment', 'res', 'html'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for tag in ('current', 'frozen'):
            for name in ('python', 'codex'):
                path = self.root / 'utility/.runtime' / tag / name
                path.write_text('# synthetic executable; never execute\n')
                path.chmod(0o700)
        self.revision = 'a' * 64
        self.config = {
            'schema_version': 'reviewer-project-1',
            'data': {'index': 'data/index/index.json', 'pdf_root': 'data/pdf',
                     'metadata_root': 'data/index/metadata'},
            'outputs': {'runs_root': 'res', 'html_root': 'html'},
            'source_manifest': 'utility/source_manifest.json',
            'runtime': self.runtime('current'),
            'comparison': {'model': 'gpt-6-astra', 'reasoning_effort': 'max', 'workers': 48,
                           'validation_workers': 4, 'timeout_seconds': 3600},
            'paused': True,
        }
        self.save_config()
        self.write('data/index/index.json', {'schema_version': 'source-data-package-1'})
        for name in ('acl2026', 'iclr2026'):
            self.write(f'data/index/{name}.json', {'records': []})
        self.write('data/index/metadata/version_metadata.jsonl', {})
        self.write('data/index/metadata/provenance.json', {})
        (self.root / 'utility/workflow.py').write_bytes(Path(workflow.__file__).read_bytes())
        self.write('utility/source_manifest.json', {'revision': self.revision, 'files': {
            'utility/workflow.py': source_provenance.proof(self.root / 'utility/workflow.py')}})
        self.preparer = SimpleNamespace(prepare=mock.Mock(return_value={'status': 'prepared', 'summary': {}}))
        self.provenance = SimpleNamespace(write_manifest=mock.Mock(return_value={'revision': self.revision}),
                                          proof=source_provenance.proof)
        self.modules = self.enterContext(mock.patch.object(
            workflow, 'local_module', side_effect=lambda name: {
                'prepare_batch': self.preparer, 'source_provenance': self.provenance}[name]))
        self.process = self.enterContext(mock.patch.object(
            workflow.subprocess, 'run', side_effect=AssertionError('Unexpected real dispatch')))

    def runtime(self, tag):
        return {'python': str(self.root / f'utility/.runtime/{tag}/python'),
                'codex': str(self.root / f'utility/.runtime/{tag}/codex'),
                'pythonpath': ['utility/vendor', str(self.root / f'utility/.runtime/{tag}')]}

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def save_config(self):
        self.write('utility/project.json', self.config)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = workflow.main(['--project', str(self.root), *args])
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def prepare(self, *extra):
        return self.invoke('prepare', '--source', 'acl2026', '--units', '7', '--run-id', 'new-run', *extra)

    def tree(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and not p.is_symlink()}

    def make_run(self, run_id='new-run'):
        run = self.root / 'res' / run_id
        run.mkdir()
        self.write(f'res/{run_id}/control.json', {'pause_dispatch': False})
        (run / 'pair_status.jsonl').write_text('{"status":"pending"}\n')
        frozen = copy.deepcopy(self.config)
        frozen['runtime'] = self.runtime('frozen')
        self.write(f'res/{run_id}/source_snapshot/deployment_provenance/project.json', frozen)
        self.write(f'res/{run_id}/source_snapshot/source_manifest.json', {'revision': self.revision, 'files': {}})
        self.write(f'res/{run_id}/run_config.json', {'source_revision': self.revision})
        core = ('runner.py', 'schema.py', 'prompts/compare.md',
                'prompts/rubric.md', 'README.md', 'requirements.txt')
        helpers = ('prepare_source_data.py', 'prepare_input_data.py', 'source_provenance.py',
                   'prepare_batch.py', 'workflow.py')
        for relative in [*(f'source_snapshot/check_diff/{name}' for name in core),
                         *(f'source_snapshot/{name}' for name in helpers),
                         'source_snapshot/data/index/index.json', 'source_snapshot/utility/token_usage.py']:
            path = run / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# frozen synthetic source, never executed\n')
        for name in ('local_batch.py', 'completed_turn.py', 'verify_local_batch.py'):
            path = run / 'controller_versions/bundle' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# frozen synthetic source, never executed\n')
        manifest = {
            'source_revision': self.revision,
            'controller': 'controller_versions/bundle/local_batch.py',
            'independent_verifier': 'controller_versions/bundle/verify_local_batch.py',
            'artifact_sha256': {str(p.relative_to(run)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in run.rglob('*') if p.is_file()},
        }
        self.write(f'res/{run_id}/execution_manifest.json', manifest)
        return run, manifest

    def unpause(self):
        self.config['paused'] = False
        self.save_config()

    def test_paths_uses_config_and_is_independent_of_cwd(self):
        self.config['data']['pdf_root'] = 'data/other-pdfs'
        (self.root / 'data/other-pdfs').mkdir()
        self.save_config()
        before = self.tree()
        old = Path.cwd()
        try:
            os.chdir(self.temp.name)
            code, output, error = self.invoke('paths', '--run-id', 'planned')
        finally:
            os.chdir(old)
        self.assertEqual(code, 0, error)
        data = json.loads(output)
        self.assertEqual(data['pdf_root'], str(self.root / 'data/other-pdfs'))
        self.assertEqual(data['index'], str(self.root / 'data/index/index.json'))
        self.assertEqual(data['run'], str(self.root / 'res/planned'))
        self.assertEqual(data['html_root'], str(self.root / 'html'))
        self.assertEqual(self.tree(), before)
        self.modules.assert_not_called()

    def test_default_project_is_derived_from_file(self):
        with mock.patch.object(workflow, '__file__', str(self.root / 'utility/workflow.py')):
            self.assertEqual(workflow.parser().parse_args(['paths']).project, self.root)

    def test_config_is_mandatory(self):
        (self.root / 'utility/project.json').unlink()
        before = self.tree()
        code, _, _ = self.invoke('paths')
        self.assertNotEqual(code, 0)
        self.assertEqual(self.tree(), before)

    def test_schema_and_missing_config_keys_fail_without_writes(self):
        original = copy.deepcopy(self.config)
        for key in ('data', 'outputs', 'source_manifest', 'runtime', 'comparison', 'paused'):
            with self.subTest(key=key):
                self.config = copy.deepcopy(original)
                self.config.pop(key)
                self.save_config()
                before = self.tree()
                self.assertNotEqual(self.invoke('paths')[0], 0)
                self.assertEqual(self.tree(), before)
        self.config = copy.deepcopy(original)
        self.config['schema_version'] = 'obsolete'
        self.save_config()
        self.assertNotEqual(self.invoke('paths')[0], 0)

    def test_safe_and_reserved_ids_rejected(self):
        before = self.tree()
        for value in ('../escape', 'a/b', '/absolute', '.', '..', 'a..b', 'archive', '.migration'):
            with self.subTest(value=value):
                self.assertNotEqual(self.invoke('paths', '--run-id', value)[0], 0)
        self.assertEqual(self.tree(), before)
        self.modules.assert_not_called()

    def test_output_roots_cannot_overlap_source_data_or_each_other(self):
        original = copy.deepcopy(self.config)
        for runs, html in (('data/pdf', 'html'), ('utility/out', 'html'), ('check_diff', 'html'),
                           ('tests', 'html'), ('res', 'res/site'), ('res/archive', 'html'),
                           ('res/.migration', 'html'), ('other-results', 'html'), ('res', 'other-html'),
                           ('../outside', 'html')):
            with self.subTest(runs=runs, html=html):
                self.config = copy.deepcopy(original)
                self.config['outputs'] = {'runs_root': runs, 'html_root': html}
                self.save_config()
                before = self.tree()
                self.assertNotEqual(self.invoke('paths')[0], 0)
                self.assertEqual(self.tree(), before)

    def test_input_escape_and_temporary_external_pdf_alias_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        self.config['data']['pdf_root'] = str(outside)
        self.save_config()
        self.assertNotEqual(self.prepare()[0], 0)
        self.config['data']['pdf_root'] = 'data/pdf-alias'
        (self.root / 'data/pdf-alias').symlink_to(outside, target_is_directory=True)
        self.save_config()
        self.assertNotEqual(self.prepare()[0], 0)
        self.modules.assert_not_called()

    def test_runtime_validation_happens_before_preparation_writes(self):
        for key, value in (('python', 'relative-python'), ('codex', str(self.root / 'missing')),
                           ('pythonpath', ['../escape']), ('pythonpath', 'old-colon-string')):
            with self.subTest(key=key):
                self.config['runtime'] = self.runtime('current')
                self.config['runtime'][key] = value
                self.save_config()
                before = self.tree()
                self.assertNotEqual(self.prepare()[0], 0)
                self.assertEqual(self.tree(), before)
        self.modules.assert_not_called()

    def test_prepare_requires_all_explicit_scope_arguments(self):
        for args in (('prepare',), ('prepare', '--source', 'acl2026', '--run-id', 'x'),
                     ('prepare', '--units', '7', '--run-id', 'x'),
                     ('prepare', '--source', 'acl2026', '--units', '7')):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(*args)[0], 2)
        self.modules.assert_not_called()

    def test_prepare_passes_exact_scope_paths_revision_codex_and_defaults(self):
        before = self.tree()
        code, output, error = self.prepare('--source', 'iclr2026')
        self.assertEqual(code, 0, error)
        args = self.preparer.prepare.call_args.args[0]
        expected = {'project': self.root, 'source_worktree': self.root, 'source_revision': self.revision,
                    'run': self.root / 'res/new-run', 'runs_root': self.root / 'res',
                    'pdf_root': self.root / 'data/pdf', 'metadata_dir': self.root / 'data/index/metadata',
                    'source': ['acl2026', 'iclr2026'], 'units': 7, 'model': 'gpt-6-astra', 'effort': 'max',
                    'workers': 48, 'validation_workers': 4, 'timeout': 3600,
                    'codex': self.runtime('current')['codex']}
        for key, value in expected.items():
            self.assertEqual(getattr(args, key), value, key)
        self.assertEqual(json.loads(output)['source_revision'], self.revision)
        self.provenance.write_manifest.assert_not_called()
        self.process.assert_not_called()
        self.assertEqual(self.tree(), before)

    def test_prepare_rejects_unpinned_or_different_loaded_adapter(self):
        path = self.root / 'utility/source_manifest.json'
        original = path.read_bytes()
        for change in ('missing-pin', 'different-clone', 'wrong-size'):
            with self.subTest(change=change):
                manifest = json.loads(original)
                if change == 'missing-pin':
                    manifest['files'].pop('utility/workflow.py')
                elif change == 'different-clone':
                    (self.root / 'utility/workflow.py').write_text('# different clone adapter\n')
                    manifest['files']['utility/workflow.py'] = source_provenance.proof(self.root / 'utility/workflow.py')
                else:
                    manifest['files']['utility/workflow.py']['bytes'] += 1
                path.write_text(json.dumps(manifest))
                before = self.tree()
                code, _, error = self.prepare()
                self.assertNotEqual(code, 0)
                self.assertIn('adapter', error.lower())
                self.assertEqual(self.tree(), before)
        self.preparer.prepare.assert_not_called()
        self.process.assert_not_called()

    def test_prepare_uses_configuration_output_root(self):
        self.config['outputs']['runs_root'] = 'res/current'
        self.save_config()
        self.assertEqual(self.prepare()[0], 0)
        args = self.preparer.prepare.call_args.args[0]
        self.assertEqual(args.run, self.root / 'res/current/new-run')
        self.assertEqual(args.runs_root, self.root / 'res/current')

    def test_prepare_explicit_overrides(self):
        code, _, error = self.prepare('--model', 'explicit-model', '--effort', 'high',
            '--workers', '32', '--validation-workers', '2', '--timeout', '90')
        self.assertEqual(code, 0, error)
        args = self.preparer.prepare.call_args.args[0]
        self.assertEqual((args.model, args.effort, args.workers, args.validation_workers, args.timeout),
                         ('explicit-model', 'high', 32, 2, 90))

    def test_prepare_rejects_invalid_numeric_scope_and_defaults(self):
        for flag, value in (('--units', '0'), ('--workers', '65'), ('--validation-workers', '17'), ('--timeout', '0')):
            with self.subTest(flag=flag):
                self.assertNotEqual(self.prepare(flag, value)[0], 0)
        self.preparer.prepare.assert_not_called()

    def test_prepare_requires_fresh_run_even_if_empty(self):
        (self.root / 'res/new-run').mkdir()
        before = self.tree()
        self.assertNotEqual(self.prepare()[0], 0)
        self.assertEqual(self.tree(), before)
        self.preparer.prepare.assert_not_called()

    def test_prepare_rejects_symlink_run_and_source_escape(self):
        (self.root / 'res/other').mkdir()
        (self.root / 'res/new-run').symlink_to(self.root / 'res/other', target_is_directory=True)
        self.assertNotEqual(self.prepare()[0], 0)
        (self.root / 'res/new-run').unlink()
        path = self.root / 'data/index/acl2026.json'
        path.unlink()
        outside = Path(self.temp.name) / 'external.json'
        outside.write_text('{}')
        path.symlink_to(outside)
        self.assertNotEqual(self.prepare()[0], 0)
        self.preparer.prepare.assert_not_called()

    def test_prepare_rejects_missing_or_invalid_revision_without_rebaseline(self):
        for payload in (None, {'revision': 'a' * 40}, {'revision': 'not-a-pin'}, {'source_revision': self.revision}):
            path = self.root / 'utility/source_manifest.json'
            if payload is None:
                path.unlink()
            else:
                self.write('utility/source_manifest.json', payload)
            self.assertNotEqual(self.prepare()[0], 0)
        self.modules.assert_not_called()

    def test_freeze_sources_is_explicit_and_prints_revision(self):
        code, output, error = self.invoke('freeze-sources')
        self.assertEqual(code, 0, error)
        self.provenance.write_manifest.assert_called_once_with(self.root)
        self.assertEqual(json.loads(output)['source_revision'], self.revision)
        self.preparer.prepare.assert_not_called()
        self.process.assert_not_called()

    def test_freeze_sources_only_changes_the_manifest(self):
        replacement = {'revision': 'b' * 64, 'files': {}}

        def write_manifest(project):
            self.assertEqual(project, self.root)
            self.write('utility/source_manifest.json', replacement)
            return replacement

        self.provenance.write_manifest.side_effect = write_manifest
        before = self.tree()
        self.assertEqual(self.invoke('freeze-sources')[0], 0)
        after = self.tree()
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        self.assertEqual(changed, {'utility/source_manifest.json'})
        self.process.assert_not_called()

    def test_freeze_sources_accepts_real_writer_revision_on_synthetic_sources(self):
        from utility import source_provenance

        for name in source_provenance.CORE_FILES:
            path = self.root / 'check_diff' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# synthetic source fixture; never imported\n')
        self.provenance.write_manifest.side_effect = source_provenance.write_manifest
        before = self.tree()
        code, output, error = self.invoke('freeze-sources')
        self.assertEqual(code, 0, error)
        manifest = json.loads((self.root / 'utility/source_manifest.json').read_text())
        self.assertEqual(set(manifest), {'schema_version', 'revision', 'files'})
        self.assertEqual(len(manifest['revision']), 64)
        self.assertEqual(json.loads(output)['source_revision'], manifest['revision'])
        self.assertIn('data/index/index.json', manifest['files'])
        after = self.tree()
        changed = {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
        self.assertEqual(changed, {'utility/source_manifest.json'})
        self.process.assert_not_called()
        self.preparer.prepare.assert_not_called()

    def test_manifest_pointer_cannot_silently_disagree_with_writer(self):
        self.config['source_manifest'] = 'utility/other_manifest.json'
        self.save_config()
        before = self.tree()
        self.assertNotEqual(self.invoke('freeze-sources')[0], 0)
        self.assertEqual(self.tree(), before)
        self.provenance.write_manifest.assert_not_called()

    def test_prepare_error_is_nonzero_and_not_dispatched(self):
        self.preparer.prepare.side_effect = ValueError('Pinned source revision mismatch')
        code, _, error = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn('Pinned source revision mismatch', error)
        self.process.assert_not_called()

    def test_project_pause_prevents_dispatch_and_all_writes(self):
        self.make_run()
        before = self.tree()
        code, _, error = self.invoke('run', '--run-id', 'new-run', '--retry-failed')
        self.assertNotEqual(code, 0)
        self.assertIn('paused', error)
        self.assertEqual(self.tree(), before)
        self.assertFalse((self.root / 'res/new-run/tmp').exists())
        self.process.assert_not_called()

    def test_run_pause_prevents_dispatch_and_all_writes(self):
        self.make_run()
        self.unpause()
        self.write('res/new-run/control.json', {'pause_dispatch': True})
        before = self.tree()
        self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
        self.assertEqual(self.tree(), before)
        self.process.assert_not_called()

    def test_fully_settled_run_is_not_restarted(self):
        run, _ = self.make_run()
        self.unpause()
        (run / 'pair_status.jsonl').write_text('{"status":"completed"}\n{"status":"input_unavailable"}\n')
        self.assertNotEqual(self.invoke('run', '--run-id', 'new-run', '--retry-failed')[0], 0)
        self.process.assert_not_called()

    def test_run_uses_frozen_runtime_and_preserves_exit_code(self):
        run, _ = self.make_run()
        self.unpause()
        self.process.side_effect = None
        self.process.return_value = SimpleNamespace(returncode=7)
        # Mutable operational state may differ from preparation-time hashes.
        self.write('res/new-run/control.json', {'pause_dispatch': False, 'workers': 32})
        (run / 'pair_status.jsonl').write_text('{"status":"pending","attempt_count":2}\n')
        self.write('res/new-run/token_usage.json', {'reported_tokens': 17})
        self.write('res/new-run/output/retained-result.json', {'retained': True})
        (run / 'controller.log').write_text('existing resume diagnostics\n')
        code, output, error = self.invoke('run', '--run-id', 'new-run')
        self.assertEqual(code, 7, error)
        argv = self.process.call_args.args[0]
        self.assertEqual(argv, [self.runtime('frozen')['python'],
            str(run / 'controller_versions/bundle/local_batch.py'), '--run', str(run)])
        env = self.process.call_args.kwargs['env']
        self.assertEqual(env['PYTHONPATH'], os.pathsep.join([
            str(self.root / 'utility/vendor'), str(self.root / 'utility/.runtime/frozen')]))
        self.assertEqual(env['PATH'].split(os.pathsep)[0], str(self.root / 'utility/.runtime/frozen'))
        self.assertEqual(env['TMPDIR'], str(run / 'tmp'))
        self.assertEqual(json.loads(output)['frozen_config'], str(run / 'source_snapshot/deployment_provenance/project.json'))
        self.assertEqual(json.loads(output)['frozen_source_manifest'], str(run / 'source_snapshot/source_manifest.json'))
        self.assertEqual(json.loads(output)['source_revision'], self.revision)

    def test_retry_failed_is_forwarded_only_explicitly(self):
        self.make_run()
        self.unpause()
        self.process.side_effect = None
        self.process.return_value = SimpleNamespace(returncode=0)
        self.assertEqual(self.invoke('run', '--run-id', 'new-run', '--retry-failed')[0], 0)
        self.assertEqual(self.process.call_args.args[0][-1], '--retry-failed')

    def test_verify_uses_only_frozen_verifier_while_paused(self):
        run, _ = self.make_run()
        self.process.side_effect = None
        self.process.return_value = SimpleNamespace(returncode=2)
        before = self.tree()
        code, _, error = self.invoke('verify', '--run-id', 'new-run', '--require-complete')
        self.assertEqual(code, 2, error)
        self.assertEqual(self.process.call_args.args[0],
            [self.runtime('frozen')['python'], str(run / 'controller_versions/bundle/verify_local_batch.py'),
             '--run', str(run), '--require-complete'])
        self.assertEqual(self.tree(), before)
        self.assertFalse((run / 'tmp').exists())
        self.modules.assert_not_called()

    def test_immutable_snapshot_rejects_changed_missing_untracked_or_linked_dependencies(self):
        self.unpause()
        dependencies = (
            'source_snapshot/check_diff/runner.py', 'source_snapshot/check_diff/schema.py',
            'source_snapshot/utility/token_usage.py', 'source_snapshot/check_diff/prompts/compare.md',
            'source_snapshot/check_diff/prompts/rubric.md', 'source_snapshot/prepare_source_data.py',
            'source_snapshot/prepare_input_data.py', 'source_snapshot/source_provenance.py',
            'source_snapshot/prepare_batch.py', 'source_snapshot/workflow.py',
        )
        cases = [('tamper', path) for path in dependencies] + [
            ('missing', 'source_snapshot/prepare_input_data.py'),
            ('omit-file-and-pin', 'source_snapshot/check_diff/schema.py'),
            ('extra', 'source_snapshot/check_diff/unreviewed.py'),
            ('extra', 'controller_versions/bundle/unreviewed.py'),
            ('extra-dir', 'source_snapshot/untracked-empty-directory'),
            ('symlink-file', 'source_snapshot/check_diff/runner.py'),
            ('symlink-dir', 'source_snapshot/check_diff'),
            ('symlink-dir', 'controller_versions'),
        ]
        for change, relative in cases:
            with self.subTest(change=change, path=relative):
                run, manifest = self.make_run()
                try:
                    path = run / relative
                    if change == 'tamper':
                        path.write_text('# changed snapshot dependency\n')
                    elif change in ('missing', 'omit-file-and-pin'):
                        path.unlink()
                        if change == 'omit-file-and-pin':
                            manifest['artifact_sha256'].pop(relative)
                            self.write('res/new-run/execution_manifest.json', manifest)
                    elif change == 'extra':
                        path.write_text('# untracked dependency\n')
                    elif change == 'extra-dir':
                        path.mkdir()
                    else:
                        target = run / 'original-dependency'
                        path.rename(target)
                        path.symlink_to(target, target_is_directory=change == 'symlink-dir')
                    before = self.tree()
                    for command in ('run', 'verify'):
                        code, _, error = self.invoke(command, '--run-id', 'new-run')
                        self.assertNotEqual(code, 0, error)
                    self.assertEqual(self.tree(), before)
                    self.assertFalse((run / 'tmp').exists())
                finally:
                    shutil.rmtree(run)
        self.process.assert_not_called()

    def test_controller_hash_is_required_and_mismatch_fails_closed(self):
        run, manifest = self.make_run()
        self.unpause()
        key = manifest['controller']
        for failure in ('missing-pin', 'wrong-pin'):
            with self.subTest(failure=failure):
                changed = copy.deepcopy(manifest)
                if failure == 'missing-pin':
                    changed['artifact_sha256'].pop(key)
                else:
                    changed['artifact_sha256'][key] = '0' * 64
                self.write('res/new-run/execution_manifest.json', changed)
                before = self.tree()
                self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
                self.assertEqual(self.tree(), before)
                self.assertFalse((run / 'tmp').exists())
        self.process.assert_not_called()

    def test_frozen_runtime_config_and_dependency_hashes_fail_closed(self):
        run, manifest = self.make_run()
        for key in ('source_snapshot/deployment_provenance/project.json',
                    'source_snapshot/source_manifest.json', 'controller_versions/bundle/completed_turn.py'):
            with self.subTest(key=key):
                changed = copy.deepcopy(manifest)
                changed['artifact_sha256'][key] = '0' * 64
                self.write('res/new-run/execution_manifest.json', changed)
                self.assertNotEqual(self.invoke('verify', '--run-id', 'new-run')[0], 0)
        self.process.assert_not_called()

    def test_frozen_manifest_revision_must_match_run_and_execution_records(self):
        self.make_run()
        self.unpause()
        for name in ('run_config.json', 'execution_manifest.json'):
            with self.subTest(name=name):
                path = self.root / 'res/new-run' / name
                original = path.read_bytes()
                data = json.loads(original)
                data['source_revision'] = 'b' * 64
                path.write_text(json.dumps(data))
                before = self.tree()
                self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
                self.assertEqual(self.tree(), before)
                path.write_bytes(original)
        self.process.assert_not_called()

    def test_invalid_frozen_runtime_paths_fail_before_scratch_creation(self):
        run, manifest = self.make_run()
        self.unpause()
        key = 'source_snapshot/deployment_provenance/project.json'
        frozen = json.loads((run / key).read_text())
        frozen['runtime']['python'] = str(self.root / 'missing-python')
        (run / key).write_text(json.dumps(frozen))
        manifest['artifact_sha256'][key] = hashlib.sha256((run / key).read_bytes()).hexdigest()
        self.write('res/new-run/execution_manifest.json', manifest)
        before = self.tree()
        self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
        self.assertEqual(self.tree(), before)
        self.assertFalse((run / 'tmp').exists())
        self.process.assert_not_called()

    def test_scratch_cannot_alias_output_even_inside_the_run(self):
        run, _ = self.make_run()
        self.unpause()
        (run / 'output').mkdir()
        (run / 'tmp').symlink_to(run / 'output', target_is_directory=True)
        before = self.tree()
        self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
        self.assertEqual(self.tree(), before)
        self.process.assert_not_called()

    def test_frozen_entry_cannot_escape_to_current_code(self):
        _, manifest = self.make_run()
        self.unpause()
        manifest['controller'] = '../../utility/local_batch.py'
        self.write('res/new-run/execution_manifest.json', manifest)
        self.assertNotEqual(self.invoke('run', '--run-id', 'new-run')[0], 0)
        self.process.assert_not_called()

    def test_old_or_missing_frozen_configuration_is_unsupported(self):
        run, _ = self.make_run()
        (run / 'source_snapshot/deployment_provenance/project.json').unlink()
        self.assertNotEqual(self.invoke('verify', '--run-id', 'new-run')[0], 0)
        self.process.assert_not_called()

    def test_archives_and_removed_analysis_command_are_not_active(self):
        (self.root / 'res/archive/t1.2').mkdir(parents=True)
        for run_id in ('archive', 'archive/t1.2', '.migration', 't1.2'):
            with self.subTest(run_id=run_id):
                self.assertNotEqual(self.invoke('verify', '--run-id', run_id)[0], 0)
        self.assertEqual(self.invoke('analyze')[0], 2)
        self.process.assert_not_called()


if __name__ == '__main__':
    unittest.main()

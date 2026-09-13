"""Offline provenance regressions; all mutable fixtures and Git repos are temporary."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

if __package__:
    from . import source_provenance as provenance
else:
    import source_provenance as provenance


EXTRAS = ('utility/prepare_v13_pilot.py', 'utility/prepare_input_data.py',
          'utility/source_provenance.py')
NEW_PIN = hashlib.sha1(b'current fixture commit').hexdigest()
OLD_PIN = hashlib.sha1(b'previous fixture commit').hexdigest()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='source-provenance-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.worktree = self.project / 'worktrees/source'
        self.active_path = self.project / 'configs/active_code.json'
        self.manifest_path = self.project / 'evidence/deployment.json'
        names = [f'check_diff/{name}' for name in provenance.CORE_FILES]
        names += ['v1.3/acl2026.json', 'v1.3/iclr2026.json', *EXTRAS]
        self.raw = {name: f'original fixture bytes: {name}\n'.encode() for name in names}
        for name, raw in self.raw.items():
            path = self.worktree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def requested(self, sources=('acl2026',), extras=EXTRAS):
        return [*(f'check_diff/{name}' for name in provenance.CORE_FILES),
                *(f'v1.3/{source}.json' for source in sources), *extras]


class ExportedSourcesTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.active = {'code_manifest': 'evidence/deployment.json',
                       'source_commit': NEW_PIN, 'worktree': str(self.worktree)}
        self.manifest = {'source_commit': NEW_PIN, 'target_relative': 'worktrees/source',
                         'files': {name: {'bytes': len(raw), 'sha256': digest(raw)}
                                   for name, raw in self.raw.items()}}
        self.save()

    def save(self):
        self.write_json(self.active_path, self.active)
        self.write_json(self.manifest_path, self.manifest)

    def current_schema(self):
        self.manifest['schema_version'] = 'github-check-diff-sync-1'
        self.manifest['files'] = {name: digest(raw) for name, raw in self.raw.items()
                                  if not name.startswith('v1.3/')}
        self.manifest['v13_json_hashes'] = {Path(name).name: digest(raw)
                                          for name, raw in self.raw.items()
                                          if name.startswith('v1.3/')}
        self.manifest.pop('target_relative', None)  # The audited sync writer omits this.
        self.save()

    def verify(self, sources=None, source_commit=NEW_PIN, extras=EXTRAS):
        # A bad active record must never silently fall back to Git or any process.
        with mock.patch.object(provenance.subprocess, 'run', side_effect=AssertionError('Unexpected subprocess')):
            return provenance.verified_sources(self.project, self.worktree,
                                               ['acl2026'] if sources is None else sources,
                                               source_commit, extras)

    def assert_verified(self, blobs, evidence, sources=('acl2026',), extras=EXTRAS):
        expected = {name: self.raw[name] for name in self.requested(sources, extras)}
        for key, path in (('active_code', self.active_path), ('code_manifest', self.manifest_path)):
            raw = path.read_bytes()
            expected[f'deployment_provenance/{key}.json'] = raw
            self.assertEqual(evidence[key], {'path': str(path), 'bytes': len(raw), 'sha256': digest(raw)})
        self.assertEqual(blobs, expected)
        self.assertEqual(set(evidence), {'active_code', 'code_manifest'})
        # This is the legacy MBZ input-proofs loop, without metadata exceptions.
        for item in evidence.values():
            self.assertEqual(provenance.proof(Path(item['path'])),
                             {key: item[key] for key in ('bytes', 'sha256')})

    def test_core_file_contract_and_legacy_map(self):
        self.assertEqual(provenance.CORE_FILES,
                         ('runner.py', 'schema.py', 'token_usage.py', 'prompts/compare.md',
                          'prompts/rubric.md', 'README.md', 'requirements.txt'))
        self.assert_verified(*self.verify())

    def test_legacy_list_and_optional_active_fields(self):
        self.active = {'code_manifest': str(self.manifest_path)}
        self.manifest['files'] = [{'path': name, **row, 'mode': 420}
                                  for name, row in self.manifest['files'].items()]
        self.save()
        self.assert_verified(*self.verify())

    def test_current_hash_strings_and_separate_shards(self):
        self.current_schema()
        before = self.manifest_path.read_bytes()
        blobs, evidence = self.verify(sources=['acl2026', 'iclr2026'])
        self.assert_verified(blobs, evidence, sources=('acl2026', 'iclr2026'))
        self.assertEqual(blobs['deployment_provenance/code_manifest.json'], before)
        frozen = json.loads(blobs['deployment_provenance/code_manifest.json'])
        self.assertTrue(all(isinstance(row, str) for row in frozen['files'].values()))
        self.assertEqual(frozen, self.manifest)  # No fabricated pinned byte counts.

    def test_audit_old_pin_fails_and_caller_selected_current_pin_passes(self):
        self.current_schema()
        (self.worktree / '.git').write_text('must not be consulted')
        with self.assertRaisesRegex(ValueError, 'manifest source_commit differs'):
            self.verify(source_commit=OLD_PIN)
        self.assert_verified(*self.verify(source_commit=NEW_PIN))
        # The implementation must not contain its own preferred commit.
        self.active['source_commit'] = self.manifest['source_commit'] = OLD_PIN
        self.save()
        self.assert_verified(*self.verify(source_commit=OLD_PIN))

    def test_altered_core_shard_and_helper_rejected_in_every_format(self):
        for shape in ('map', 'list', 'sync'):
            if shape == 'list':
                self.manifest['files'] = [{'path': name, **row}
                                          for name, row in self.manifest['files'].items()]
                self.save()
            elif shape == 'sync':
                self.current_schema()
            for name in ('check_diff/runner.py', 'v1.3/acl2026.json', *EXTRAS):
                with self.subTest(shape=shape, path=name):
                    path = self.worktree / name
                    path.write_bytes(b'X' + self.raw[name][1:])  # Same size, wrong digest.
                    with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                        self.verify()
                    path.write_bytes(self.raw[name])

    def test_missing_source_core_and_extra_entries_rejected(self):
        original = copy.deepcopy(self.manifest)
        for name in ('check_diff/schema.py', 'v1.3/acl2026.json', *EXTRAS):
            with self.subTest(path=name):
                self.manifest = copy.deepcopy(original)
                del self.manifest['files'][name]
                self.save()
                with self.assertRaisesRegex(ValueError, 'Missing deployment manifest source entries'):
                    self.verify()
        self.current_schema()
        del self.manifest['v13_json_hashes']['acl2026.json']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Missing deployment manifest source entries'):
            self.verify()

    def test_current_schema_without_selected_shards_is_rejected(self):
        self.current_schema()
        del self.manifest['v13_json_hashes']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Missing deployment manifest source entries'):
            self.verify()

    def test_agreeing_shard_overlap_retains_pinned_size_and_rejects_conflict(self):
        self.current_schema()
        name = 'v1.3/acl2026.json'
        self.manifest['files'][name] = {'sha256': digest(self.raw[name]), 'bytes': len(self.raw[name])}
        self.save()
        self.assert_verified(*self.verify())
        self.manifest['files'][name]['bytes'] += 1
        self.save()
        with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
            self.verify()
        self.manifest['files'][name] = digest(b'conflicting bytes')
        self.save()
        with self.assertRaisesRegex(ValueError, 'Conflicting deployment manifest shard'):
            self.verify()

    def test_duplicate_list_rows_even_identical_are_rejected(self):
        rows = [{'path': name, **row} for name, row in self.manifest['files'].items()]
        self.manifest['files'] = rows + [rows[0]]
        self.save()
        with self.assertRaisesRegex(ValueError, 'Duplicate deployment manifest file path'):
            self.verify()

    def test_duplicate_json_keys_rejected_at_every_relevant_level(self):
        for target, raw in (
                (self.active_path, '{"code_manifest":"evidence/deployment.json","code_manifest":"other"}'),
                (self.manifest_path, '{"files":{},"files":{}}'),
                (self.manifest_path, '{"files":{"a":{},"a":{}}}'),
                (self.manifest_path, '{"files":{"a":{"bytes":1,"bytes":1}}}'),
                (self.manifest_path, '{"v13_json_hashes":{"a.json":"a","a.json":"a"}}')):
            with self.subTest(raw=raw):
                self.save()
                target.write_text(raw)
                with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                    self.verify()

    def test_malformed_json_and_non_object_documents_rejected(self):
        for target in (self.active_path, self.manifest_path):
            for raw in (b'{', b'[]', b'null', b'123', b'"text"', b'\xff', b'{"bad":NaN}'):
                with self.subTest(target=target.name, raw=raw):
                    self.save()
                    target.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        self.verify()

    def test_unknown_schema_and_unversioned_digest_strings_rejected(self):
        for schema in ('unknown-2', None, 1):
            with self.subTest(schema=schema):
                self.manifest['schema_version'] = schema
                self.save()
                with self.assertRaisesRegex(ValueError, 'Unsupported deployment manifest schema'):
                    self.verify()
        self.current_schema()
        del self.manifest['schema_version']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Malformed deployment manifest entry'):
            self.verify()

    def test_malformed_file_entries_rejected(self):
        name = 'check_diff/runner.py'
        good = copy.deepcopy(self.manifest)
        invalid = (None, [], 2, True, 'a' * 64, {}, {'bytes': 1},
                   {'sha256': 'a' * 64}, {'sha256': 'invalid', 'bytes': 2},
                   {'sha256': 'a' * 64, 'bytes': True}, {'sha256': 'a' * 64, 'bytes': -1},
                   {'sha256': 'a' * 64, 'bytes': 1.0}, {'sha256': 'a' * 64, 'bytes': '1'},
                   {'sha256': 'a' * 64, 'bytes': 1, 'path': 'other'})
        for entry in invalid:
            with self.subTest(entry=entry):
                self.manifest = copy.deepcopy(good)
                self.manifest['files'][name] = entry
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()
        for files in (None, 1, 'files', [1], [{'bytes': 1}], [{'path': []}]):
            with self.subTest(files=files):
                self.manifest['files'] = files
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_malformed_shard_maps_and_digests_rejected(self):
        self.current_schema()
        for shards in (None, [], 'hashes', {'acl2026.json': None}, {'acl2026.json': {}},
                       {'acl2026.json': 'g' * 64}, {'acl2026.json': 'a' * 63}):
            with self.subTest(shards=shards):
                self.manifest['v13_json_hashes'] = shards
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_source_commit_is_required_and_full_hex(self):
        with self.assertRaises(TypeError):
            provenance.verified_sources(self.project, self.worktree, ['acl2026'])
        for value in (None, 1, '', 'HEAD', 'main', NEW_PIN[:12], NEW_PIN + '0', 'g' * 40,
                      NEW_PIN + '\n', NEW_PIN + ':check_diff/runner.py'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'source_commit'):
                self.verify(source_commit=value)

    def test_mismatched_or_invalid_record_commit_rejected(self):
        for record in (self.active, self.manifest):
            for value in (OLD_PIN, 'HEAD', None):
                with self.subTest(record=record is self.active, value=value):
                    record['source_commit'] = value
                    self.save()
                    with self.assertRaisesRegex(ValueError, 'source_commit'):
                        self.verify()
            record['source_commit'] = NEW_PIN
        del self.manifest['source_commit']
        self.save()
        with self.assertRaisesRegex(ValueError, 'source_commit'):
            self.verify()

    def test_all_declared_targets_must_match(self):
        for record in (self.active, self.manifest):
            for field in ('worktree', 'target_relative'):
                with self.subTest(active=record is self.active, field=field):
                    prior = copy.deepcopy(record)
                    record[field] = 'worktrees/other'
                    self.save()
                    with self.assertRaisesRegex(ValueError, 'different source worktree'):
                        self.verify()
                    record.clear()
                    record.update(prior)

    def test_invalid_manifest_paths_and_path_escape_rejected(self):
        for path in ('../outside.json', str(self.project.parent / 'outside.json'),
                     'evidence/../evidence/deployment.json', '', None, [], str(self.project)):
            with self.subTest(path=path):
                self.active['code_manifest'] = path
                self.save()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_unsafe_names_and_reserved_paths_rejected(self):
        for name in ('../outside', '/absolute', 'utility/../escape', './utility/x',
                     'utility//x', 'utility\\x', 'utility/x\x00', '.git/config',
                     'deployment_provenance/active_code.json'):
            with self.subTest(extra=name), self.assertRaises(ValueError):
                self.verify(extras=(name,))
            # Unrequested manifest entries still must be well formed.
            self.manifest['files'][name] = {'bytes': 0, 'sha256': digest(b'')}
            self.save()
            with self.subTest(manifest=name), self.assertRaises(ValueError):
                self.verify()
            del self.manifest['files'][name]
        self.save()
        for source in ('../acl2026', '/tmp/a', 'a/b', 'a\\b', '.', '', None, 'acl2026.json'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.verify(sources=[source])

    def test_invalid_shard_filenames_rejected(self):
        self.current_schema()
        for name in ('../acl2026.json', 'v1.3/acl2026.json', '/acl2026.json', 'acl2026',
                     './acl2026.json', 'a\\b.json', 'a.json\x00'):
            with self.subTest(name=name):
                self.manifest['v13_json_hashes'][name] = digest(b'')
                self.save()
                with self.assertRaisesRegex(ValueError, 'Invalid v1.3 shard filename'):
                    self.verify()
                del self.manifest['v13_json_hashes'][name]

    def test_duplicate_requests_and_invalid_collections_rejected(self):
        for sources, extras in ((['acl2026', 'acl2026'], EXTRAS),
                                (['acl2026'], ('check_diff/runner.py',)),
                                (['acl2026'], ('v1.3/acl2026.json',)),
                                ('acl2026', EXTRAS), (['acl2026'], list(EXTRAS))):
            with self.subTest(sources=sources, extras=extras), self.assertRaises(ValueError):
                self.verify(sources=sources, extras=extras)

    def test_symlink_files_and_parent_components_rejected(self):
        for path in (self.active_path, self.manifest_path, self.worktree / 'check_diff/runner.py',
                     self.worktree / 'v1.3/acl2026.json', self.worktree / EXTRAS[0],
                     self.worktree / 'check_diff/prompts', self.active_path.parent,
                     self.manifest_path.parent, self.worktree):
            with self.subTest(path=path):
                original = path.with_name(path.name + '.original')
                path.rename(original)
                path.symlink_to(original, target_is_directory=original.is_dir())
                try:
                    with self.assertRaisesRegex(ValueError, 'Symlink'):
                        self.verify()
                finally:
                    path.unlink()
                    original.rename(path)

    def test_broken_active_symlink_never_allows_git_fallback(self):
        self.active_path.unlink()
        self.active_path.symlink_to(self.project / 'missing.json')
        (self.worktree / '.git').write_text('fixture')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.verify()

    def test_missing_or_nonregular_required_files_rejected(self):
        path = self.worktree / 'check_diff/runner.py'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Cannot read regular source file'):
            self.verify()
        path.mkdir()
        with self.assertRaisesRegex(ValueError, 'Expected a regular source file'):
            self.verify()

    def test_read_only_success_and_failure_without_git(self):
        def snapshot():
            return {str(path.relative_to(self.project)): path.read_bytes()
                    for path in self.project.rglob('*') if path.is_file()}

        before = snapshot()
        self.verify(extras=())
        self.assertEqual(snapshot(), before)
        self.assertFalse((self.project / 'experiments').exists())
        self.active_path.unlink()
        before = snapshot()
        with self.assertRaisesRegex(ValueError, 'Exported source requires'):
            self.verify()
        self.assertEqual(snapshot(), before)


@unittest.skipUnless(shutil.which('git'), 'Local Git is required for temporary-repo tests')
class GitSourcesTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.git_env = os.environ.copy()
        for key in list(self.git_env):
            if key.startswith('GIT_'):
                del self.git_env[key]
        self.git_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('commit', '-qm', 'Temporary provenance fixture')
        self.pin = self.git('rev-parse', 'HEAD').decode().strip()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.worktree), '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@example.invalid',
                               '-c', 'commit.gpgsign=false', '-c', f'core.hooksPath={os.devnull}',
                               *args], check=True, capture_output=True, env=self.git_env).stdout

    def verify(self, source_commit=None, extras=EXTRAS):
        return provenance.verified_sources(self.project, self.worktree, ['acl2026'],
                                           self.pin if source_commit is None else source_commit, extras)

    def test_explicit_pin_and_live_bytes_match_read_only(self):
        before = self.git('status', '--porcelain')
        with mock.patch.object(provenance.subprocess, 'run', wraps=subprocess.run) as run:
            blobs, evidence = self.verify()
        self.assertEqual(blobs, {name: self.raw[name] for name in self.requested()})
        self.assertEqual(evidence, {})
        commands = [call.args[0] for call in run.call_args_list]
        refs = [command[-1] for command in commands if 'show' in command]
        self.assertEqual(refs, [f'{self.pin}:{name}' for name in self.requested()])
        for call in run.call_args_list:
            self.assertIn('--no-replace-objects', call.args[0])
            self.assertIn('protocol.allow=never', call.args[0])
            self.assertEqual(call.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
        self.assertEqual(self.git('status', '--porcelain'), before)

    def test_dirty_core_shard_and_helper_rejected(self):
        for name in ('check_diff/runner.py', 'v1.3/acl2026.json', *EXTRAS):
            for staged in (False, True):
                with self.subTest(name=name, staged=staged):
                    path = self.worktree / name
                    path.write_bytes(b'changed working bytes')
                    if staged:
                        self.git('add', name)
                    with self.assertRaisesRegex(ValueError, 'Working source differs from pinned commit'):
                        self.verify()
                    path.write_bytes(self.raw[name])
                    self.git('add', name)

    def test_old_commit_does_not_silently_hide_new_working_bytes(self):
        path = self.worktree / 'check_diff/runner.py'
        path.write_bytes(b'new committed bytes')
        self.git('add', '.')
        self.git('commit', '-qm', 'Temporary second revision')
        current = self.git('rev-parse', 'HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError, 'Working source differs from pinned commit'):
            self.verify(source_commit=self.pin)
        blobs, _ = self.verify(source_commit=current)
        self.assertEqual(blobs['check_diff/runner.py'], b'new committed bytes')

    def test_unrelated_dirty_file_does_not_expand_verification_scope(self):
        (self.worktree / 'unrelated.txt').write_text('concurrent unrelated work')
        self.verify()

    def test_replacement_ref_cannot_substitute_a_different_commit(self):
        path = self.worktree / 'check_diff/runner.py'
        path.write_bytes(b'replacement commit bytes')
        self.git('add', '.')
        self.git('commit', '-qm', 'Temporary replacement revision')
        replacement = self.git('rev-parse', 'HEAD').decode().strip()
        self.git('replace', self.pin, replacement)
        with self.assertRaisesRegex(ValueError, 'Working source differs from pinned commit'):
            self.verify()

    def test_linked_worktree_git_file_is_supported(self):
        linked = self.project / 'worktrees/linked'
        self.git('worktree', 'add', '--detach', str(linked), self.pin)
        self.assertTrue((linked / '.git').is_file())
        blobs, evidence = provenance.verified_sources(self.project, linked, ['acl2026'], self.pin, EXTRAS)
        self.assertEqual(blobs, {name: self.raw[name] for name in self.requested()})
        self.assertEqual(evidence, {})

    def test_literal_extra_filename_is_supported(self):
        name = 'utility/[literal].py'
        (self.worktree / name).write_bytes(b'literal path fixture')
        self.git('add', '.')
        self.git('commit', '-qm', 'Temporary literal path fixture')
        pin = self.git('rev-parse', 'HEAD').decode().strip()
        blobs, _ = self.verify(source_commit=pin, extras=(name,))
        self.assertEqual(blobs[name], b'literal path fixture')

    def test_missing_commit_and_noncommit_object_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Cannot read pinned local Git source'):
            self.verify(source_commit='0' * 40)
        tree = self.git('rev-parse', 'HEAD^{tree}').decode().strip()
        with self.assertRaisesRegex(ValueError, 'must identify a local Git commit object'):
            self.verify(source_commit=tree)

    def test_missing_pinned_extra_rejected(self):
        (self.worktree / 'utility/untracked.py').write_text('untracked helper')
        with self.assertRaisesRegex(ValueError, 'Expected a regular pinned Git source file'):
            self.verify(extras=('utility/untracked.py',))

    def test_bad_active_configuration_never_falls_back(self):
        self.write_json(self.active_path, {'code_manifest': 'missing.json'})
        with mock.patch.object(provenance.subprocess, 'run', side_effect=AssertionError('Unexpected Git')):
            with self.assertRaises(ValueError):
                self.verify()

    def test_committed_symlink_rejected_even_if_live_replaced_by_regular_file(self):
        path = self.worktree / 'utility/link.py'
        path.symlink_to('prepare_input_data.py')
        self.git('add', '.')
        self.git('commit', '-qm', 'Temporary symlink fixture')
        pin = self.git('rev-parse', 'HEAD').decode().strip()
        path.unlink()
        path.write_bytes(b'prepare_input_data.py')
        with self.assertRaisesRegex(ValueError, 'Expected a regular pinned Git source file'):
            self.verify(source_commit=pin, extras=('utility/link.py',))

    def test_live_source_symlink_rejected(self):
        path = self.worktree / 'check_diff/runner.py'
        original = path.with_suffix('.original')
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.verify()

    def test_no_git_marker_cannot_use_ancestor_repository(self):
        nested = self.worktree / 'nested'
        nested.mkdir()
        with mock.patch.object(provenance.subprocess, 'run', side_effect=AssertionError('Unexpected Git')):
            with self.assertRaisesRegex(ValueError, 'Exported source requires'):
                provenance.verified_sources(self.project, nested, ['acl2026'], self.pin)


if __name__ == '__main__':
    unittest.main()

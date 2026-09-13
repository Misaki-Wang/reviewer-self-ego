"""Offline formal-export provenance regressions; every writable fixture is temporary."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from utility import source_provenance as provenance

EXTRAS = ('utility/prepare_source_data.py', 'utility/prepare_input_data.py', 'utility/source_provenance.py')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SourceFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='source-provenance-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.config_path = self.project / 'utility/project.json'
        self.manifest_path = self.project / 'utility/source_manifest.json'
        for name in [*(f'check_diff/{name}' for name in provenance.CORE_FILES), *EXTRAS]:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'# Original fixture source: {name}\n')
        self.index = {'dataset': 'source_data', 'schema_version': 'source-data-index-1',
                      'dataset_metadata': {'dataset': 'source_data', 'schema_version': 'source-data-source-1', 'record_count': 2},
                      'record_order': ['2501.00001', '2501.00002'], 'files': []}
        for number, source in enumerate(('acl2026', 'iclr2026'), 1):
            name = f'data/index/{source}.json'
            shard = {'dataset': 'source_data', 'schema_version': 'source-data-source-shard-1',
                     'source_id': source, 'record_count': 1,
                     'records': [{'arxiv_id': f'2501.{number:05d}', 'source_ids': [source]}]}
            self.write_json(self.project / name, shard)
            raw = (self.project / name).read_bytes()
            self.index['files'].append({'source_id': source, 'path': f'{source}.json', 'record_count': 1,
                                        'bytes': len(raw), 'sha256': digest(raw)})
        self.write_json(self.project / provenance.INDEX_PATH, self.index)
        self.write_json(self.project / 'data/index/provenance/build_metadata.json', {'historical': 'unchanged'})
        self.write_json(self.project / 'data/index/metadata/provenance.json', {'observed': 'unchanged'})
        ledger = self.project / 'data/index/metadata/version_metadata.jsonl'
        ledger.write_text('{"version":1,"page_count":null}\n')
        self.config = {'schema_version': 'reviewer-project-1',
                       'data': {'index': provenance.INDEX_PATH, 'pdf_root': 'data/pdf', 'metadata_root': 'data/index/metadata'},
                       'outputs': {'runs_root': 'res', 'html_root': 'html'}, 'paused': True,
                       'source_manifest': provenance.MANIFEST_PATH}
        self.write_json(self.config_path, self.config)
        self.manifest = provenance.write_manifest(self.project)
        self.initial = self.snapshot()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def snapshot(self):
        return {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}

    def reset(self):
        for name, raw in self.initial.items():
            (self.project / name).write_bytes(raw)
        self.manifest = json.loads(self.manifest_path.read_bytes())

    def seal(self):
        self.manifest['revision'] = provenance.revision_for_files(self.manifest['files'])
        self.write_json(self.manifest_path, self.manifest)

    def repin(self, shard=None):
        if shard is not None:
            index = json.loads((self.project / provenance.INDEX_PATH).read_bytes())
            raw = (self.project / f'data/index/{shard}.json').read_bytes()
            for entry in index['files']:
                if entry['source_id'] == shard:
                    entry.update(bytes=len(raw), sha256=digest(raw))
            self.write_json(self.project / provenance.INDEX_PATH, index)
        self.manifest = provenance.write_manifest(self.project)

    def verify(self, sources=None, revision=None, extras=EXTRAS, code_root=None):
        return provenance.verified_sources(self.project, self.project if code_root is None else code_root,
            ['acl2026'] if sources is None else sources,
            self.manifest['revision'] if revision is None else revision, extras)

    def assert_verified(self, sources=('acl2026',), extras=EXTRAS):
        blobs, deployment = self.verify(list(sources), extras=extras)
        names = [*(f'check_diff/{name}' for name in provenance.CORE_FILES), provenance.INDEX_PATH,
                 *(f'data/index/{source}.json' for source in sources), *extras]
        expected = {name: (self.project / name).read_bytes() for name in names}
        expected['deployment_provenance/project.json'] = self.config_path.read_bytes()
        expected['source_manifest.json'] = self.manifest_path.read_bytes()
        self.assertEqual(blobs, expected)
        for key, path in (('project', self.config_path), ('source_manifest', self.manifest_path)):
            raw = path.read_bytes()
            self.assertEqual(deployment[key], {'path': str(path), 'bytes': len(raw), 'sha256': digest(raw)})
            self.assertEqual(provenance.proof(path), {'bytes': len(raw), 'sha256': digest(raw)})
        self.assertEqual(set(deployment), {'project', 'source_manifest'})


class ExportedSourcesTests(SourceFixture):
    def test_core_file_contract_and_manifest_map(self):
        self.assertEqual(provenance.CORE_FILES, ('runner.py', 'schema.py', 'prompts/compare.md',
                                               'prompts/rubric.md', 'README.md', 'requirements.txt'))
        self.assert_verified()

    def test_raw_manifest_and_config_bytes_preserved(self):
        before = self.snapshot()
        self.assert_verified(sources=('acl2026', 'iclr2026'))
        self.assertEqual(before, self.snapshot())

    def test_revision_pin_is_required_and_caller_selected(self):
        with self.assertRaisesRegex(ValueError, 'authorized source_revision'):
            self.verify(revision='0' * 64)
        old = self.manifest['revision']
        (self.project / EXTRAS[0]).write_text('# explicitly revised helper\n')
        self.repin()
        self.assertNotEqual(self.manifest['revision'], old)
        with self.assertRaises(ValueError):
            self.verify(revision=old)
        self.assert_verified()

    def test_altered_core_shard_and_helper_rejected(self):
        for name in ('check_diff/runner.py', 'data/index/acl2026.json', *EXTRAS):
            with self.subTest(path=name):
                path = self.project / name
                raw = path.read_bytes()
                path.write_bytes(b'X' + raw[1:])
                with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
                    self.verify()
                path.write_bytes(raw)

    def test_missing_source_core_and_extra_entries_rejected(self):
        for name in ('check_diff/schema.py', 'data/index/acl2026.json', *EXTRAS):
            with self.subTest(path=name):
                self.reset()
                del self.manifest['files'][name]
                self.seal()
                with self.assertRaisesRegex(ValueError, 'Missing source manifest entries'):
                    self.verify()

    def test_index_must_be_verified_even_for_single_shard(self):
        path = self.project / provenance.INDEX_PATH
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
            self.verify()
        self.reset()
        del self.manifest['files'][provenance.INDEX_PATH]
        self.seal()
        with self.assertRaisesRegex(ValueError, 'Missing source manifest entries'):
            self.verify()

    def test_index_and_manifest_shard_hashes_agree(self):
        for field, value in (('bytes', 0), ('sha256', '0' * 64)):
            with self.subTest(field=field):
                self.reset()
                index = copy.deepcopy(self.index)
                index['files'][0][field] = value
                self.write_json(self.project / provenance.INDEX_PATH, index)
                self.repin()
                with self.assertRaisesRegex(ValueError, 'Index shard hash/size'):
                    self.verify()

    def test_duplicate_json_keys_rejected_at_every_level(self):
        cases = [(provenance.PROJECT_PATH, '{"schema_version":"a","schema_version":"b"}'),
                 (provenance.MANIFEST_PATH, '{"files":{},"files":{}}'),
                 (provenance.MANIFEST_PATH, '{"files":{"utility/a.py":{},"utility/a.py":{}}}'),
                 (provenance.MANIFEST_PATH, '{"files":{"utility/a.py":{"bytes":1,"bytes":1}}}'),
                 (provenance.INDEX_PATH, '{"files":[],"files":[]}'),
                 ('data/index/acl2026.json', '{"records":[],"records":[]}')]
        for name, raw in cases:
            with self.subTest(path=name):
                self.reset()
                (self.project / name).write_text(raw)
                if name.startswith('data/'):
                    self.repin('acl2026' if name.endswith('acl2026.json') else None)
                with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
                    self.verify()

    def test_malformed_json_and_non_object_documents_rejected(self):
        for name in (provenance.PROJECT_PATH, provenance.MANIFEST_PATH, provenance.INDEX_PATH, 'data/index/acl2026.json'):
            for raw in ('{', '[]', '{"bad":NaN}', '{"bad":Infinity}'):
                with self.subTest(path=name, raw=raw):
                    self.reset()
                    (self.project / name).write_text(raw)
                    if name.startswith('data/'):
                        self.repin('acl2026' if name.endswith('acl2026.json') else None)
                    with self.assertRaises(ValueError):
                        self.verify()

    def test_unknown_schema_and_legacy_formats_rejected(self):
        for change in ({'schema_version': 'github-check-diff-sync-1'}, {'source_commit': '1' * 40},
                       {'files': list(self.manifest['files'].items())},
                       {'files': {name: row['sha256'] for name, row in self.manifest['files'].items()}}):
            with self.subTest(change=change):
                self.reset()
                self.manifest.update(change)
                self.write_json(self.manifest_path, self.manifest)
                with self.assertRaises(ValueError):
                    self.verify()

    def test_malformed_file_entries_rejected(self):
        for entry in (None, '0' * 64, {}, {'bytes': True, 'sha256': '0' * 64},
                      {'bytes': -1, 'sha256': '0' * 64}, {'bytes': 1.5, 'sha256': '0' * 64},
                      {'bytes': 1, 'sha256': 'short'}, {'bytes': 1, 'sha256': None},
                      {'bytes': 1, 'sha256': '0' * 64, 'path': 'other'}):
            with self.subTest(entry=entry):
                self.reset()
                self.manifest['files'][EXTRAS[0]] = entry
                self.seal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_source_revision_is_required_and_full_hex(self):
        for revision in (None, '', '1' * 40, 'g' * 64, 123, True):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                provenance.verified_sources(self.project, self.project, ['acl2026'], revision)

    def test_manifest_revision_canonical_hash_cannot_be_fabricated(self):
        self.manifest['revision'] = '0' * 64
        self.write_json(self.manifest_path, self.manifest)
        with self.assertRaisesRegex(ValueError, 'canonical files map'):
            self.verify(revision='0' * 64)

    def test_code_root_must_equal_project(self):
        nested = self.project / 'worktrees/source'
        nested.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'code_root must equal'):
            self.verify(code_root=nested)
        alias = self.project / 'root-alias'
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.verify(code_root=alias)

    def test_invalid_manifest_paths_and_index_config_rejected(self):
        for field, values in (('source_manifest', ('../outside.json', '/outside.json', provenance.INDEX_PATH)),
                              ('index', ('../outside.json', '/outside.json', provenance.MANIFEST_PATH))):
            for value in values:
                with self.subTest(field=field, value=value):
                    config = copy.deepcopy(self.config)
                    (config['data'] if field == 'index' else config)[field] = value
                    self.write_json(self.config_path, config)
                    with self.assertRaises(ValueError):
                        self.verify()

    def test_unsafe_names_and_reserved_paths_rejected(self):
        for name in ('/absolute.py', '../outside.py', 'utility/../bad.py', 'utility//bad.py',
                     'utility/./bad.py', 'utility\\bad.py', 'utility/a\x00.py',
                     'deployment_provenance/project.json', 'utility/project.json',
                     'utility/source_manifest.json', 'res/log.json', 'html/page.html',
                     'review_experiment/run.py', 'utility/.runtime/helper.py', 'tests/helper.py'):
            with self.subTest(name=name):
                self.reset()
                self.manifest['files'][name] = {'bytes': 0, 'sha256': digest(b'')}
                self.seal()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_invalid_source_ids_and_index_filenames_rejected(self):
        for name in ('../acl2026', '/acl2026', 'acl2026.json', '', 'index', 'UPPER', None):
            with self.subTest(source=name), self.assertRaises(ValueError):
                self.verify(sources=[name])
        for path in ('../acl2026.json', '/acl2026.json', 'other.json'):
            self.reset()
            index = copy.deepcopy(self.index)
            index['files'][0]['path'] = path
            self.write_json(self.project / provenance.INDEX_PATH, index)
            self.repin()
            with self.assertRaises(ValueError):
                self.verify()

    def test_duplicate_requests_and_invalid_collections_rejected(self):
        for sources, extras in ((['acl2026', 'acl2026'], EXTRAS), ('acl2026', EXTRAS),
                                ([], list(EXTRAS)), (['acl2026'], (EXTRAS[0], EXTRAS[0])),
                                (['acl2026'], (provenance.INDEX_PATH,))):
            with self.subTest(sources=sources, extras=extras), self.assertRaises(ValueError):
                self.verify(sources=sources, extras=extras)

    def test_symlink_files_and_parent_components_rejected(self):
        for name in ('check_diff/runner.py', 'data/index/acl2026.json', EXTRAS[0]):
            with self.subTest(path=name):
                path = self.project / name
                raw = path.read_bytes()
                target = self.project / 'outside-source'
                target.write_bytes(raw)
                path.unlink()
                path.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'Symlink'):
                    self.verify()
                path.unlink()
                path.write_bytes(raw)
        prompts = self.project / 'check_diff/prompts'
        relocated = self.project / 'prompt-bytes'
        prompts.rename(relocated)
        prompts.symlink_to(relocated, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.verify()

    def test_broken_configuration_symlink_never_falls_back(self):
        self.config_path.unlink()
        self.config_path.symlink_to(self.project / 'missing.json')
        (self.project / '.git').mkdir()
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('Git fallback')):
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                self.verify()

    def test_missing_or_nonregular_required_files_rejected(self):
        for name in (provenance.PROJECT_PATH, provenance.MANIFEST_PATH, 'check_diff/runner.py', EXTRAS[0]):
            for directory in (False, True):
                with self.subTest(path=name, directory=directory):
                    self.reset()
                    path = self.project / name
                    path.unlink()
                    if directory:
                        path.mkdir()
                    with self.assertRaises(ValueError):
                        self.verify()
                    if directory:
                        path.rmdir()
                    path.write_bytes(self.initial[name])

    def test_read_only_success_and_failure_without_subprocess(self):
        with mock.patch.object(subprocess, 'run', side_effect=AssertionError('Unexpected subprocess')):
            before = self.snapshot()
            self.assert_verified()
            self.assertEqual(before, self.snapshot())
            with self.assertRaises(ValueError):
                self.verify(revision='0' * 64)
            self.assertEqual(before, self.snapshot())

    def test_unrequested_file_bytes_do_not_expand_verification_scope(self):
        (self.project / 'data/index/iclr2026.json').write_text('Unrequested changed bytes')
        self.assert_verified()
        with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
            self.verify(sources=['iclr2026'])

    def test_literal_extra_filename_is_supported(self):
        name = 'utility/[literal]*.py'
        (self.project / name).write_text('# literal filename\n')
        self.repin()
        self.assert_verified(extras=(*EXTRAS, name))

    def test_requested_shard_schema_source_and_count_checked(self):
        for field, value in (('dataset', 'v1.3'), ('schema_version', 'old-schema'), ('source_id', 'other'),
                             ('record_count', 9), ('records', None)):
            with self.subTest(field=field):
                self.reset()
                path = self.project / 'data/index/acl2026.json'
                shard = json.loads(path.read_bytes())
                shard[field] = value
                self.write_json(path, shard)
                self.repin('acl2026')
                with self.assertRaisesRegex(ValueError, 'Shard dataset/source/count'):
                    self.verify()

    def test_index_record_order_and_count_checked(self):
        for change in ({'record_order': ['2501.00001', '2501.00001']}, {'record_order': [None]},
                       {'record_order': []}, {'dataset': 'v1.3'}, {'schema_version': 'old-schema'}):
            with self.subTest(change=change):
                self.reset()
                index = copy.deepcopy(self.index)
                index.update(change)
                self.write_json(self.project / provenance.INDEX_PATH, index)
                self.repin()
                with self.assertRaises(ValueError):
                    self.verify()

    def test_changed_source_after_read_is_rejected(self):
        original = provenance._read_file
        def raced(path):
            raw = original(path)
            if path == self.project / EXTRAS[-1]:
                (self.project / 'check_diff/runner.py').write_text('# Changed after its verified read\n')
            return raw
        with mock.patch.object(provenance, '_read_file', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                self.verify()

    def test_changed_config_or_manifest_after_read_is_rejected(self):
        original = provenance._read_file
        for target in (self.config_path, self.manifest_path):
            self.reset()
            def raced(path):
                raw = original(path)
                if path == self.project / EXTRAS[-1]:
                    target.write_bytes(target.read_bytes() + b'\n')
                return raw
            with mock.patch.object(provenance, '_read_file', side_effect=raced):
                with self.assertRaisesRegex(ValueError, 'changed during verification'):
                    self.verify()

    def test_same_file_changed_during_read_is_rejected(self):
        path = self.project / 'check_diff/runner.py'
        original = provenance.os.fstat
        calls = 0
        def raced(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.write_text('# Changed inside the read\n')
            return original(descriptor)
        with mock.patch.object(provenance.os, 'fstat', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                provenance.proof(path)

    def test_metadata_extra_is_pinned_and_read_only(self):
        name = 'data/index/metadata/version_metadata.jsonl'
        self.assert_verified(extras=(*EXTRAS, name))
        path = self.project / name
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash/size mismatch'):
            self.verify(extras=(*EXTRAS, name))


class ManifestBuilderTests(SourceFixture):
    def test_build_manifest_is_deterministic_read_only_and_config_independent(self):
        before = self.snapshot()
        first = provenance.build_manifest(self.project)
        self.assertEqual(first, provenance.build_manifest(self.project))
        self.assertEqual(before, self.snapshot())
        config = copy.deepcopy(self.config)
        config['runtime'] = {'python': '/explicit/fixture/python'}
        self.write_json(self.config_path, config)
        self.assertEqual(first, provenance.build_manifest(self.project))
        self.assertEqual(first['revision'], digest(json.dumps(first['files'], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode()))

    def test_manifest_includes_source_index_and_metadata_only(self):
        for name in ('res/output.json', 'html/view.html', 'tests/temp.py', 'review_experiment/run.py',
                     'runtime-store/lib.py'):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Excluded fixture')
        (self.project / 'utility/.runtime').symlink_to(self.project / 'runtime-store', target_is_directory=True)
        files = provenance.build_manifest(self.project)['files']
        for name in (provenance.INDEX_PATH, 'data/index/acl2026.json', *EXTRAS,
                     'data/index/metadata/version_metadata.jsonl', 'data/index/provenance/build_metadata.json'):
            self.assertIn(name, files)
        for name in (provenance.PROJECT_PATH, provenance.MANIFEST_PATH, 'res/output.json', 'html/view.html',
                     'tests/temp.py', 'review_experiment/run.py', 'utility/.runtime/lib.py'):
            self.assertNotIn(name, files)

    def test_write_manifest_atomic_repeatable_and_no_temp_leaks(self):
        first = provenance.write_manifest(self.project)
        raw = self.manifest_path.read_bytes()
        self.assertEqual(first, provenance.write_manifest(self.project))
        self.assertEqual(raw, self.manifest_path.read_bytes())
        self.assertEqual(list(self.manifest_path.parent.glob('.source-manifest-*.tmp')), [])

    def test_write_manifest_refuses_symlink_destination(self):
        target = self.project / 'protected.json'
        target.write_text('preserved')
        self.manifest_path.unlink()
        self.manifest_path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            provenance.write_manifest(self.project)
        self.assertEqual(target.read_text(), 'preserved')

    def test_build_manifest_rejects_symlink_source_directory_and_file(self):
        path = self.project / EXTRAS[0]
        raw = path.read_bytes()
        outside = self.project / 'other.py'
        outside.write_bytes(raw)
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            provenance.build_manifest(self.project)
        path.unlink()
        path.write_bytes(raw)
        metadata = self.project / 'data/index/metadata'
        metadata.rename(self.project / 'old-metadata')
        metadata.symlink_to(self.project / 'old-metadata', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            provenance.build_manifest(self.project)

    def test_build_manifest_rejects_missing_and_nonregular_core(self):
        path = self.project / 'check_diff/runner.py'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing required source files'):
            provenance.build_manifest(self.project)
        path.mkdir()
        with self.assertRaisesRegex(ValueError, 'Missing required source files'):
            provenance.build_manifest(self.project)

    def test_build_manifest_rejects_source_inventory_changes(self):
        original = provenance._read_file
        def raced(path):
            raw = original(path)
            if path == self.project / EXTRAS[-1]:
                (self.project / 'utility/new.py').write_text('# Added after enumeration\n')
            return raw
        with mock.patch.object(provenance, '_read_file', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'inventory changed'):
                provenance.build_manifest(self.project)

    def test_build_manifest_rejects_cross_file_races(self):
        original = provenance._read_file
        def raced(path):
            raw = original(path)
            if path == self.project / EXTRAS[-1]:
                (self.project / 'check_diff/runner.py').write_text('# Changed earlier source\n')
            return raw
        with mock.patch.object(provenance, '_read_file', side_effect=raced):
            with self.assertRaisesRegex(ValueError, 'changed during verification'):
                provenance.build_manifest(self.project)


if __name__ == '__main__':
    unittest.main()

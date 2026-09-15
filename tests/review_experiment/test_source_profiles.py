"""Offline fixtures for frozen profiles and bounded source routing."""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from review_experiment.source_profiles import (
    SystemProfile, load_prompts, resolve_sources, validate_result,
)


class SourceProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='source-profiles-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prepared = self.root / 'run/prepared'
        self.prepared.mkdir(parents=True)
        self.known = {'iclr2026', 'acl2025', 'icml2025', 'icml2026', 'tmlr_2025plus'}
        self.a, self.b = '2501.12345', '2502.12345'

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def profile(self, kind='integer', values=(1, 3, 5, 6, 8, 10), **kwargs):
        return SystemProfile('iclr2026', 'system/iclr2026.txt', 'System\n',
                             '0' * 64, kind, values, **kwargs)

    def prompts(self):
        folder = self.root / 'prompts'
        texts = {'system/iclr2026.txt': b'ICLR\r\n',
                 'system/acl2025.txt': b'ARR\n',
                 'system/icml2025.txt': b'ICML 2025\n',
                 'system/icml2026.txt': b'ICML 2026\n',
                 'system/tmlr_2025plus.txt': b'TMLR\n'}
        conditions = []
        for i in range(4):
            name = f'c{i}.user.txt'
            texts[name] = f'  User {i}\r\n{{PAPER_CONTENT}}\n'.encode()
            conditions.append({'id': f'c{i}', 'experiment': str(i % 3 + 1), 'user_file': name})
        for name, data in texts.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        systems = {}
        for source, kind, values in [
            ('iclr2026', 'integer', [1, 3, 5, 6, 8, 10]),
            ('acl2025', 'number', [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5]),
            ('icml2025', 'integer', [1, 2, 3, 4, 5]),
            ('icml2026', 'integer', [1, 2, 3, 4, 5, 6]),
            ('tmlr_2025plus', 'integer', [1, 2, 3, 4]),
        ]:
            systems[source] = {'file': f'system/{source}.txt', 'oa_type': kind,
                               'oa_values': values, 'references': ['ignored']}
        systems['tmlr_2025plus'].update(
            scale_kind='experimental_encoding',
            oa_labels=['reject', 'leaning reject', 'leaning accept', 'accept'])
        manifest = {'files': {k: hashlib.sha256(v).hexdigest() for k, v in texts.items()},
                    'systems': systems, 'conditions': conditions}
        self.write_json(folder / 'manifest.json', manifest)
        return folder, manifest

    def metadata(self, document, name='source_metadata.json'):
        return self.write_json(self.prepared / name, document)

    def resolve(self, papers=None, **kwargs):
        return resolve_sources(kwargs.pop('input_root', self.prepared),
                               [self.a] if papers is None else papers, self.known, **kwargs)

    def catalog(self):
        folder = self.root / 'data/index'
        entries = []
        for source, ids in [('iclr2026', [self.a]), ('acl2025', [self.a, self.b])]:
            shard = self.write_json(folder / f'{source}.json', {
                'source_id': source, 'record_count': len(ids),
                'records': [{'arxiv_id': aid, 'source_ids': ['iclr2026', 'acl2025']} for aid in ids]})
            raw = shard.read_bytes()
            entries.append({'path': shard.name, 'source_id': source,
                            'sha256': hashlib.sha256(raw).hexdigest(),
                            'bytes': len(raw), 'record_count': len(ids)})
        return self.write_json(folder / 'index.json', {'files': entries})

    def test_manifest_scales_and_unchanged_text(self):
        folder, manifest = self.prompts()
        profiles, conditions = load_prompts(folder)
        self.assertEqual(len(conditions), len(manifest['conditions']))
        self.assertEqual(conditions[0], ('c0', '  User 0\r\n{PAPER_CONTENT}\n'))
        self.assertEqual(profiles['iclr2026'].text, 'ICLR\r\n')
        self.assertEqual(profiles['icml2025'].oa_values, (1, 2, 3, 4, 5))
        self.assertEqual(profiles['icml2026'].oa_values, (1, 2, 3, 4, 5, 6))
        self.assertIn(1.5, profiles['acl2025'].oa_values)
        meta = profiles['tmlr_2025plus'].metadata()
        self.assertEqual(meta['scale_kind'], 'experimental_encoding')
        self.assertEqual(meta['oa_labels'], ['reject', 'leaning reject', 'leaning accept', 'accept'])
        self.assertEqual(json.loads(json.dumps(meta)), meta)
        self.assertEqual(profiles['iclr2026'].sha256, manifest['files']['system/iclr2026.txt'])

    def test_optional_manifest_return_preserves_custom_default_contract(self):
        folder, manifest = self.prompts()
        default = load_prompts(folder, conditions=['c0'])
        self.assertEqual(len(default), 2)
        extended = load_prompts(folder, conditions=['c0'], with_manifest=True)
        self.assertEqual(len(extended), 3)
        self.assertEqual(extended[:2], default)
        # Filtering selects calls, while the returned manifest retains full provenance.
        self.assertEqual(extended[2], manifest)
        self.assertEqual(len(extended[2]['conditions']), 4)
        self.assertEqual(len(extended[1]), 1)
        extended[2]['systems']['acl2025']['oa_values'].append(99)
        self.assertNotIn(99, extended[0]['acl2025'].oa_values)
        self.assertEqual(load_prompts(folder, with_manifest=True)[2], manifest)

    def test_profile_is_frozen_and_schema_metadata_are_fresh(self):
        profile = self.profile()
        with self.assertRaises(FrozenInstanceError):
            profile.text = 'changed'
        schema = profile.schema
        self.assertEqual(set(schema['properties']), {'oa', 'strengths', 'weaknesses'})
        self.assertEqual(schema['required'], ['oa', 'strengths', 'weaknesses'])
        self.assertIs(schema['additionalProperties'], False)
        schema['properties']['oa']['enum'].append(2)
        schema['properties']['strengths']['items']['type'] = 'integer'
        metadata = profile.metadata()
        metadata['oa_values'].append(2)
        self.assertNotIn(2, profile.schema['properties']['oa']['enum'])
        self.assertEqual(profile.schema['properties']['strengths']['items']['type'], 'string')
        self.assertNotIn(2, profile.metadata()['oa_values'])

    def test_filters_unknown_and_empty_selection(self):
        folder, _ = self.prompts()
        self.assertEqual(load_prompts(folder, experiments=['1'], conditions=['c0', 'c1'])[1],
                         [('c0', '  User 0\r\n{PAPER_CONTENT}\n')])
        for filters in [{'conditions': ['absent']}, {'experiments': ['9']},
                        {'experiments': ['1'], 'conditions': ['c1']}]:
            with self.subTest(filters=filters), self.assertRaises(ValueError):
                load_prompts(folder, **filters)


    def shared_prompts(self):
        folder, manifest = self.prompts()
        manifest['conditions'][0].update(id='exp1_quality', shared_baseline_for=['3'])
        manifest['conditions'][2]['id'] = 'exp3_years'
        self.write_json(folder / 'manifest.json', manifest)
        return folder, manifest

    def test_shared_baseline_in_experiment_three(self):
        folder, _ = self.shared_prompts()
        selected = load_prompts(folder, experiments=['3'])[1]
        self.assertEqual([cid for cid, _ in selected], ['exp1_quality', 'exp3_years'])
        self.assertEqual(selected[0][1], '  User 0\r\n{PAPER_CONTENT}\n')

    def test_shared_baseline_deduplicates_combined_experiments(self):
        folder, _ = self.shared_prompts()
        selected = load_prompts(folder, experiments=['1', '3'])[1]
        self.assertEqual([cid for cid, _ in selected], ['exp1_quality', 'exp3_years', 'c3'])
        self.assertEqual(sum(cid == 'exp1_quality' for cid, _ in selected), 1)

    def test_shared_baseline_condition_filter_is_intersection(self):
        folder, _ = self.shared_prompts()
        selected = load_prompts(folder, experiments=['3'], conditions=['exp1_quality', 'c1'])[1]
        self.assertEqual([cid for cid, _ in selected], ['exp1_quality'])
        self.assertEqual([cid for cid, _ in load_prompts(folder, conditions=['exp1_quality'])[1]],
                         ['exp1_quality'])
        with self.assertRaisesRegex(ValueError, 'No conditions selected'):
            load_prompts(folder, experiments=['2'], conditions=['exp1_quality'])

    def test_shared_baseline_configuration_validation(self):
        folder, manifest = self.shared_prompts()
        for value in [None, '3', 3, {}, [3], [True], [''], ['  '], ['3', None]]:
            manifest['conditions'][0]['shared_baseline_for'] = value
            self.write_json(folder / 'manifest.json', manifest)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'shared_baseline_for'):
                load_prompts(folder, conditions=['c1'])
        manifest['conditions'][0]['shared_baseline_for'] = []
        self.write_json(folder / 'manifest.json', manifest)
        self.assertEqual([cid for cid, _ in load_prompts(folder, experiments=['3'])[1]],
                         ['exp3_years'])

    def single_shard_catalog(self, content):
        folder = self.root / 'catalog_fixture'
        shard = self.write_json(folder / 'source.json', content)
        raw = shard.read_bytes()
        return self.write_json(folder / 'index.json', {'files': [{
            'source_id': content['source_id'], 'path': shard.name,
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'record_count': len(content['records']),
        }]})

    def test_catalog_rejects_auxiliary_papers_absent_from_records(self):
        for records in ([], [{'arxiv_id': self.a, 'source_ids': ['icml2026']}]):
            for field, mapping in [
                ('source_by_paper', {self.b: 'icml2026'}),
                ('record_memberships', {self.b: ['icml2026']}),
            ]:
                content = {'source_id': 'icml2026', 'record_count': len(records),
                           'records': records, field: mapping,
                           'selected_units': [{'arxiv_id': self.b, 'assigned_source_id': 'icml2026'}]}
                catalog = self.single_shard_catalog(content)
                with self.subTest(records=records, field=field), self.assertRaisesRegex(
                        ValueError, 'Canonical shard membership'):
                    self.resolve(papers=[self.b], metadata=catalog)

    def test_catalog_accepts_scoped_overlap_for_actual_records(self):
        content = {'source_id': 'icml2026', 'authorized_sources': ['icml2026'],
                   'record_memberships': {self.a: ['icml2026']},
                   'source_by_paper': {self.a: 'icml2026'},
                   'records': [{'arxiv_id': self.a,
                                'source_ids': ['icml2026', 'tmlr_2025plus']}]}
        catalog = self.single_shard_catalog(content)
        self.assertEqual(self.resolve(metadata=catalog)[0], {self.a: 'icml2026'})

    def test_catalog_rejects_incomplete_or_conflicting_memberships(self):
        for values in ([], ['tmlr_2025plus']):
            catalog = self.single_shard_catalog({
                'source_id': 'icml2026', 'record_memberships': {self.a: ['icml2026']},
                'records': [{'arxiv_id': self.a, 'source_ids': values}]})
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.resolve(metadata=catalog)
        catalog = self.single_shard_catalog({
            'source_id': 'icml2026', 'source_by_paper': {self.a: 'tmlr_2025plus'},
            'records': [{'arxiv_id': self.a, 'source_ids': ['icml2026', 'tmlr_2025plus']}]})
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(metadata=catalog)

    def test_unselected_prompt_hash_is_also_verified(self):
        folder, manifest = self.prompts()
        (folder / manifest['conditions'][-1]['user_file']).write_text('drift')
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            load_prompts(folder, conditions=['c0'])

    def test_prompt_traversal_absolute_symlink_and_missing(self):
        folder, manifest = self.prompts()
        outside = self.root / 'outside.txt'
        outside.write_bytes(b'outside')
        (folder / 'escape.txt').symlink_to(outside)
        for name in ['../outside.txt', str(outside), 'system/../../outside.txt',
                     'escape.txt', 'missing.txt', './c0.user.txt', 'system\\outside.txt']:
            modified = json.loads(json.dumps(manifest))
            modified['files'][name] = hashlib.sha256(b'outside').hexdigest()
            self.write_json(folder / 'manifest.json', modified)
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_prompts(folder)

    def test_invalid_manifest_profiles_and_conditions(self):
        folder, manifest = self.prompts()
        mutations = [
            lambda d: d.pop('systems'),
            lambda d: d.update(systems={}),
            lambda d: d['systems']['iclr2026'].update(oa_type='boolean'),
            lambda d: d['systems']['iclr2026'].update(oa_values=[]),
            lambda d: d['systems']['iclr2026'].update(oa_values=[True]),
            lambda d: d['systems']['iclr2026'].update(oa_values=[1.0]),
            lambda d: d['systems']['iclr2026'].update(oa_values=[1, 1]),
            lambda d: d['systems']['acl2025'].update(oa_values=[float('inf')]),
            lambda d: d['systems']['iclr2026'].update(file='unhashed.txt'),
            lambda d: d['systems']['iclr2026'].update(scale_kind='unknown'),
            lambda d: d['systems']['iclr2026'].update(scale_kind='study'),
            lambda d: d['systems']['iclr2026'].update(oa_labels=[]),
            lambda d: d['systems']['iclr2026'].update(oa_labels=['one']),
            lambda d: d['systems']['iclr2026'].update(oa_labels=[1] * 6),
            lambda d: d['conditions'].append(d['conditions'][0]),
            lambda d: d['conditions'][0].update(user_file='missing.txt'),
            lambda d: d.update(conditions=[]),
            lambda d: d['files'].update({'c0.user.txt': 'not-a-sha'}),
        ]
        for mutate in mutations:
            modified = json.loads(json.dumps(manifest))
            mutate(modified)
            modified['system_file'] = 'system/iclr2026.txt'
            self.write_json(folder / 'manifest.json', modified)
            with self.subTest(manifest=modified), self.assertRaises(ValueError):
                load_prompts(folder)

    def test_empty_system_and_invalid_utf8(self):
        folder, manifest = self.prompts()
        for raw in [b' \r\n', b'\xff']:
            name = 'system/iclr2026.txt'
            (folder / name).write_bytes(raw)
            manifest['files'][name] = hashlib.sha256(raw).hexdigest()
            self.write_json(folder / 'manifest.json', manifest)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                load_prompts(folder)

    def test_rating_validation_integer_number_string(self):
        cases = [('integer', (1, 3), [1, 3], [True, False, 1.0, 1.5, '1', 2]),
                 ('number', (1, 1.5, 2), [1, 1.0, 1.5, 2.0],
                  [True, False, 1.25, '1.5', float('nan'), float('inf')]),
                 ('string', ('Reject', 'Accept'), ['Reject', 'Accept'], [1, True, 'reject', ''])]
        for kind, values, valid, invalid in cases:
            schema = self.profile(kind, values).schema
            for oa in valid:
                result = {'oa': oa, 'strengths': [], 'weaknesses': ['specific evidence']}
                self.assertIs(validate_result(result, schema), result)
            for oa in invalid:
                with self.subTest(kind=kind, oa=oa), self.assertRaises(ValueError):
                    validate_result({'oa': oa, 'strengths': [], 'weaknesses': []}, schema)

    def test_malformed_schema_and_result(self):
        good = {'oa': 1, 'strengths': [], 'weaknesses': []}
        schemas = [None, {}, {'properties': {'oa': {}}},
                   {'properties': {'oa': {'type': 'integer', 'enum': [True]}}}]
        for field, value in [('required', ['oa']), ('additionalProperties', True),
                             ('type', 'array')]:
            schema = self.profile().schema
            schema[field] = value
            schemas.append(schema)
        for schema in schemas:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                validate_result(good, schema)
        results = [None, [], {'oa': 1}, dict(good, extra=1),
                   dict(good, strengths=['']), dict(good, weaknesses=[' \t']),
                   dict(good, strengths=[1]), dict(good, weaknesses='text')]
        for result in results:
            with self.subTest(result=result), self.assertRaises(ValueError):
                validate_result(result, self.profile().schema)

    def test_explicit_source_external_is_authoritative(self):
        self.catalog()
        resolved, provenance = self.resolve(papers=['external'], source=' ICLR2026 ',
                                            project_root=self.root)
        self.assertEqual(resolved, {'external': 'iclr2026'})
        self.assertEqual(provenance['mode'], 'explicit_source')
        self.assertEqual(provenance['sha256'], {})
        with self.assertRaises(ValueError):
            self.resolve(source='unknown')

    def test_mixed_mapping_and_provenance(self):
        path = self.metadata({'source_by_paper': {self.a: 'iclr2026', self.b: 'acl2025'}})
        resolved, provenance = self.resolve(papers=[self.a, self.b])
        self.assertEqual(resolved, {self.a: 'iclr2026', self.b: 'acl2025'})
        self.assertEqual(provenance['metadata_path'], str(path))
        self.assertEqual(provenance['sha256'][str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(source='acl2025')

    def test_metadata_precedence_and_no_broad_ancestor_search(self):
        self.metadata({'source_by_paper': {self.a: 'iclr2026'}}, 'selection_manifest.json')
        self.write_json(self.prepared.parent / 'scope_input.json',
                        {'source_by_paper': {self.a: 'acl2025'}})
        self.assertEqual(self.resolve()[0][self.a], 'iclr2026')
        self.metadata({'source_by_paper': {self.a: 'acl2025'}})
        self.assertEqual(self.resolve()[0][self.a], 'acl2025')
        explicit = self.write_json(self.root / 'explicit.json',
                                   {'source_by_paper': {self.a: 'icml2025'}})
        self.assertEqual(self.resolve(metadata=explicit)[0][self.a], 'icml2025')
        external = self.root / 'deep/other/prepared'
        external.mkdir(parents=True)
        self.write_json(self.root / 'source_metadata.json',
                        {'source_by_paper': {self.a: 'iclr2026'}})
        with self.assertRaisesRegex(ValueError, 'Missing source metadata'):
            self.resolve(input_root=external)
        self.assertEqual(self.resolve(input_root=external, source='acl2025')[0][self.a], 'acl2025')

    def test_pair_and_paper_input_normalization(self):
        self.metadata({'source_by_paper': {self.a: 'acl2025'}})
        pair = self.prepared / self.a / 'v1_v3'
        pair.mkdir(parents=True)
        for path in [self.prepared, pair.parent, pair]:
            with self.subTest(path=path):
                self.assertEqual(self.resolve(input_root=path)[0][self.a], 'acl2025')

    def test_nearby_metadata_requires_coverage_even_with_explicit_source(self):
        self.metadata({'source_by_paper': {self.b: 'iclr2026'}})
        for kwargs in [{}, {'source': 'iclr2026'}]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'Missing source membership'):
                self.resolve(**kwargs)

    def test_missing_unknown_legacy_and_malformed_metadata(self):
        documents = [
            {'source_by_paper': {self.a: 'unknown'}},
            {'records': [{'arxiv_id': self.a}]},
            {'records': [{'arxiv_id': self.a, 'source_ids': []}]},
            {'records': [{'arxiv_id': self.a, 'source_ids': ['unknown']}]},
            {'source': 'iclr2026', 'records': []},
            {'source': 'iclr2026', 'source_id': 'acl2025', 'records': [{'arxiv_id': self.a}]},
            {'records': 'bad'}, {'source_by_paper': []},
        ]
        for document in documents:
            self.metadata(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.resolve()
        with self.assertRaisesRegex(ValueError, 'Cannot read'):
            self.resolve(metadata=self.root / 'missing.json', source='iclr2026')

    def test_ambiguous_memberships_resolved_by_explicit_source(self):
        self.metadata({'records': [{'arxiv_id': self.a, 'source_ids': ['iclr2026', 'acl2025']}]})
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.resolve()
        self.assertEqual(self.resolve(source='acl2025')[0][self.a], 'acl2025')

    def test_declared_source_and_authorized_scope(self):
        self.metadata({'source': 'acl2025', 'records': [
            {'arxiv_id': self.a, 'source_ids': ['iclr2026', 'acl2025']}]})
        self.assertEqual(self.resolve()[0][self.a], 'acl2025')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(source='iclr2026')
        self.metadata({'authorized_sources': ['iclr2026'],
                       'record_memberships': {self.a: ['iclr2026', 'acl2025']},
                       'records': [{'arxiv_id': self.a}]})
        self.assertEqual(self.resolve()[0][self.a], 'iclr2026')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(source='acl2025')


    def test_scoped_subset_of_full_record_memberships(self):
        papers = [f'2601.{i:05d}' for i in range(1, 15)]
        document = {
            'authorized_sources': ['icml2026'],
            'record_memberships': {aid: ['icml2026'] for aid in papers},
            'records': [{'arxiv_id': aid, 'source_ids': ['icml2026', 'tmlr_2025plus']}
                        for aid in papers],
        }
        self.metadata(document)
        self.assertEqual(self.resolve(papers=papers)[0], dict.fromkeys(papers, 'icml2026'))
        self.assertEqual(self.resolve(papers=papers, source='ICML2026')[0],
                         dict.fromkeys(papers, 'icml2026'))
        # A broader authorization does not widen the scoped paper membership.
        document['authorized_sources'].append('tmlr_2025plus')
        self.metadata(document)
        self.assertEqual(self.resolve(papers=papers)[0], dict.fromkeys(papers, 'icml2026'))
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(papers=papers, source='tmlr_2025plus')

    def test_source_by_paper_selects_and_binds_full_membership(self):
        document = {
            'source_by_paper': {self.a: 'icml2026'},
            'records': [{'arxiv_id': self.a, 'source_ids': ['icml2026', 'tmlr_2025plus']}],
        }
        for include_scope in (False, True):
            if include_scope:
                document['record_memberships'] = {self.a: ['icml2026', 'tmlr_2025plus']}
            self.metadata(document)
            with self.subTest(include_scope=include_scope):
                self.assertEqual(self.resolve()[0][self.a], 'icml2026')
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    self.resolve(source='tmlr_2025plus')
        document['selected_units'] = [
            {'arxiv_id': self.a, 'assigned_source_id': 'tmlr_2025plus'}]
        self.metadata(document)
        with self.assertRaisesRegex(ValueError, 'Conflicting assigned'):
            self.resolve()
        document.pop('selected_units')
        document['source'] = 'tmlr_2025plus'
        self.metadata(document)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve()

    def test_scoped_and_assigned_memberships_must_be_subsets(self):
        full = [{'arxiv_id': self.a, 'source_ids': ['icml2026', 'tmlr_2025plus']}]
        documents = [
            {'record_memberships': {self.a: ['icml2026', 'acl2025']}, 'records': full},
            {'source_by_paper': {self.a: 'acl2025'}, 'records': full},
            {'source_by_paper': {self.a: 'icml2026'},
             'record_memberships': {self.a: ['tmlr_2025plus']}, 'records': full},
            {'source_by_paper': {self.a: 'icml2026'},
             'record_memberships': {self.a: ['icml2026', 'acl2025']}, 'records': full},
        ]
        for document in documents:
            self.metadata(document)
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, 'Conflicting'):
                self.resolve()

    def test_prepared_plan_assignment_consistency(self):
        document = {'records': [{'arxiv_id': self.a, 'source_ids': ['iclr2026', 'acl2025']}],
                    'selected_units': [{'arxiv_id': self.a, 'assigned_source_id': 'acl2025'}]}
        self.metadata(document)
        self.assertEqual(self.resolve()[0][self.a], 'acl2025')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(source='iclr2026')
        document['selected_units'].append(dict(document['selected_units'][0]))
        self.metadata(document)
        self.assertEqual(self.resolve()[0][self.a], 'acl2025')
        document['selected_units'][1]['assigned_source_id'] = 'iclr2026'
        self.metadata(document)
        with self.assertRaisesRegex(ValueError, 'Conflicting assigned'):
            self.resolve()
        document['selected_units'] = [{'arxiv_id': self.a, 'assigned_source_id': 'icml2025'}]
        self.metadata(document)
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve()

    def test_membership_and_declared_mismatches(self):
        documents = [
            {'source': 'iclr2026', 'records': [{'arxiv_id': self.a, 'source_ids': ['acl2025']}]},
            {'record_memberships': {self.a: ['iclr2026']},
             'records': [{'arxiv_id': self.a, 'source_ids': ['acl2025']}]},
            {'source': 'acl2025', 'authorized_sources': ['iclr2026'],
             'records': [{'arxiv_id': self.a, 'source_ids': ['acl2025']}]},
            {'source': 'iclr2026', 'records': [{'arxiv_id': self.a}],
             'selected_units': [{'arxiv_id': self.b, 'assigned_source_id': 'iclr2026'}]},
        ]
        for document in documents:
            self.metadata(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.resolve()

    def test_catalog_fallback_membership_is_containing_shard(self):
        catalog = self.catalog()
        resolved, provenance = self.resolve(papers=[self.b], project_root=self.root)
        self.assertEqual(resolved, {self.b: 'acl2025'})
        self.assertEqual(provenance['mode'], 'canonical_catalog')
        self.assertEqual(len(provenance['sha256']), 3)
        self.assertIn(str(catalog), provenance['sha256'])
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.resolve(project_root=self.root)
        self.assertEqual(self.resolve(metadata=catalog, source='iclr2026')[0][self.a], 'iclr2026')

    def test_explicit_catalog_and_shard(self):
        catalog = self.catalog()
        shard = catalog.parent / 'iclr2026.json'
        self.assertEqual(self.resolve(metadata=shard)[0][self.a], 'iclr2026')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.resolve(metadata=shard, source='acl2025')
        with self.assertRaisesRegex(ValueError, 'Missing source membership'):
            self.resolve(papers=['external'], metadata=catalog, source='iclr2026')

    def test_catalog_integrity_size_count_hash_and_identity(self):
        for field, value in [('bytes', 0), ('record_count', 99), ('sha256', '0' * 64),
                             ('source_id', 'icml2025'), ('source_id', 'unknown')]:
            catalog = self.catalog()
            document = json.loads(catalog.read_text())
            document['files'][0][field] = value
            self.write_json(catalog, document)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.resolve(metadata=catalog, source='iclr2026')

    def test_catalog_unsafe_paths_and_unselected_shard_drift(self):
        for name in ['../outside.json', '/outside.json', 'escape.json']:
            catalog = self.catalog()
            outside = self.write_json(self.root / 'outside.json', {})
            link = catalog.parent / 'escape.json'
            if not link.exists():
                link.symlink_to(outside)
            document = json.loads(catalog.read_text())
            document['files'][0]['path'] = name
            self.write_json(catalog, document)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.resolve(metadata=catalog, source='acl2025')
        catalog = self.catalog()
        (catalog.parent / 'iclr2026.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Checksum'):
            self.resolve(papers=[self.b], metadata=catalog)

    def test_first_metadata_error_is_not_silently_bypassed(self):
        self.metadata({'records': [{'arxiv_id': self.a}]})
        self.metadata({'source_by_paper': {self.a: 'acl2025'}}, 'selection_manifest.json')
        with self.assertRaisesRegex(ValueError, 'Missing source memberships'):
            self.resolve(source='acl2025')


if __name__ == '__main__':
    unittest.main()

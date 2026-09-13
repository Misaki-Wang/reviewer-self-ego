"""Offline scope, evidence, and frozen Batch/Audit compatibility checks."""
import argparse
from contextlib import redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from pypdf import PdfWriter

from utility import prepare_batch as pilot


REPO = Path(os.environ.get('SOURCE_CODE_ROOT', Path(__file__).resolve().parents[2])).resolve()


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = {name: (REPO / 'check_diff' / name).read_bytes() for name in pilot.CORE_FILES}
        cls.frozen_files = {name: (REPO / 'utility' / name).read_bytes() for name in pilot.FROZEN_FILES}
        cls.helpers = {name: cls.frozen_files[name] for name in pilot.HELPERS}

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.code_root = self.project
        self.run = self.project / 'res/pilot'
        self.args = argparse.Namespace(project=self.project,
            run=Path('res/pilot'), source=['acl2026'], units=10,
            model='gpt-6-astra', effort='max', workers=20, timeout=3600, source_revision=None)
        self.make_fixture(11)

    def make_fixture(self, count):
        records, ledger = [], []
        # Deliberately descending IDs: selection must follow metadata, not sort IDs.
        for i in reversed(range(count)):
            aid = f'2501.{i:05d}'
            versions = []
            for number, day in enumerate((0, 30, 31, 61, 62, 93), 1):
                path = self.project / f'data/pdf/{aid}/{aid}v{number}.pdf'
                path.parent.mkdir(parents=True, exist_ok=True)
                writer = PdfWriter()
                writer.add_blank_page(width=612, height=792)
                writer.write(path)
                date = (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()
                versions.append({'version': number, 'submitted_at': date,
                    'pdf_url': f'https://arxiv.org/pdf/{aid}v{number}', 'pdf': pilot.proof(path),
                    'page_count': {'value': 1, 'source_pdf_sha256': pilot.proof(path)['sha256']}})
                ledger.append({'arxiv_id': aid, 'version': number, 'requested_id': f'{aid}v{number}',
                    'submitted_at': date, 'observed_at_utc': '2020-01-01T00:00:00Z',
                    'pdf': {'availability_status': 'verified', **pilot.proof(path)},
                    'page_count': {'value': 1}, 'official_no_pdf_evidence': None})
            records.append({'arxiv_id': aid, 'arxiv_url': f'https://arxiv.org/abs/{aid}',
                'title': 'Unchanged original metadata', 'source_ids': ['acl2026', 'unauthorized_source'],
                'source_entries': [], 'frozen_N': 6, 'versions': versions,
                'extra': {'original': [None, 'α']}, 'temporal_eligibility': {'status': 'eligible'}})
        self.records = copy.deepcopy(records)
        pilot.save(self.code_root / 'data/index/acl2026.json',
                   {'dataset': 'source_data', 'schema_version': 'source-data-source-shard-1',
                    'source_id': 'acl2026', 'record_count': count, 'records': records})
        self.ledger_path = self.project / pilot.METADATA / 'version_metadata.jsonl'
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        pilot.save_rows(self.ledger_path, ledger)
        pilot.save(self.ledger_path.parent / 'provenance.json',
                   {'files': {'version_metadata.jsonl': pilot.proof(self.ledger_path)}})
        for name, raw in self.core.items():
            path = self.code_root / 'check_diff' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        for name, raw in self.frozen_files.items():
            path = self.code_root / 'utility' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.manifest_path = self.project / 'utility/source_manifest.json'
        pilot.save(self.project / 'utility/project.json', {
            'schema_version': 'reviewer-project-1',
            'data': {'index': 'data/index/index.json', 'pdf_root': 'data/pdf',
                     'metadata_root': 'data/index/metadata'},
            'outputs': {'runs_root': 'res', 'html_root': 'html'},
            'source_manifest': 'utility/source_manifest.json',
            'runtime': {'python': sys.executable, 'codex': '/offline/fake-codex', 'pythonpath': []},
            'comparison': {'model': 'gpt-6-astra', 'reasoning_effort': 'max', 'workers': 20,
                           'validation_workers': 4, 'timeout_seconds': 3600},
            'paused': True})
        self.sign_sources()

    def sign_sources(self):
        # Explicitly authorize changed fixture sources, including their catalog hashes.
        shards = sorted((self.project / 'data/index').glob('*.json'))
        files, records = [], {}
        for path in shards:
            if path.name == 'index.json':
                continue
            shard = pilot.read(path)
            files.append({'source_id': shard['source_id'], 'path': path.name, **pilot.proof(path),
                          'record_count': len(shard['records'])})
            for record in shard['records']:
                records.setdefault(record['arxiv_id'], record)
        index = self.project / 'data/index/index.json'
        pilot.save(index, {'dataset': 'source_data', 'schema_version': 'source-data-index-1', 'files': files,
                          'dataset_metadata': {'dataset': 'source_data', 'schema_version': 'source-data-source-1',
                                               'record_count': len(records)},
                          'record_order': list(records)})
        loaded, _ = pilot.preparation.load_source(index)
        self.assertEqual(loaded, list(records.values()))
        manifest = pilot.source_provenance.write_manifest(self.project)
        self.args.source_revision = manifest['revision']
        self.assertRegex(self.args.source_revision, r'^[0-9a-f]{64}$')

    def prepare(self):
        # The exported fixture has no .git and must never launch Git or a model.
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Unexpected dispatch')):
            return pilot.prepare(self.args)

    def test_core_pin_and_frozen_lowercase_scientific_contract(self):
        self.prepare()
        config = pilot.read(self.run / 'run_config.json')
        self.assertEqual(config['source_revision'], self.args.source_revision)
        namespace = {}
        exec(compile((self.run / 'source_snapshot/check_diff/schema.py').read_bytes(),
                     '<frozen-schema>', 'exec'), namespace)
        self.assertEqual(len(namespace['DIMENSIONS']), 14)
        self.assertEqual(namespace['LABELS'], ('major change', 'minor change', 'maybe', 'no change'))
        self.assertEqual(set(namespace['SCHEMA']['properties']['prelim']['properties']), {'A1', 'A2'})
        self.assertIn('improvement', namespace['SCHEMA']['properties'])

    def test_requested_positive_counts_keep_exact_prefix_and_worker_limits(self):
        for units, workers in ((1, 1), (7, 48), (11, 64)):
            with self.subTest(units=units, workers=workers):
                self.args.units, self.args.workers = units, workers
                self.args.run = Path(f'res/units-{units}')
                self.run = self.project / self.args.run
                self.prepare()
                self.assertEqual(pilot.read(self.run / 'scope_input.json')['records'], self.records[:units])
                config = pilot.read(self.run / 'run_config.json')
                self.assertEqual(config['initial_workers'], workers)
                self.assertEqual(config['max_workers'], workers)
                self.assertEqual(config['validation_workers'], 4)
                self.assertNotIn('concurrency_ladder', config)
                self.assertFalse(pilot.read(self.run / 'control.json')['auto_scale'])

    def test_200_units_keep_all_metadata_maximum_sequences_and_every_transition(self):
        self.make_fixture(201)
        self.args.units, self.args.workers = 200, 64
        self.args.concurrency_ladder = [64, 48, 32, 16]
        self.args.validation_workers = 16
        manifest = self.prepare()
        scope = pilot.read(self.run / 'scope_input.json')
        plan = pilot.read(self.run / 'prepared/selection_manifest.json')
        self.assertEqual(plan['schema_version'], pilot.preparation.PLAN_SCHEMA_VERSION)
        config = pilot.read(self.run / 'run_config.json')
        self.assertEqual(scope['records'], self.records[:200])
        self.assertEqual(plan['records'], self.records[:200])
        self.assertEqual(config['unit_ids'], [r['arxiv_id'] for r in self.records[:200]])
        self.assertEqual(scope['parent_sources'][0]['record_count'], 201)
        self.assertEqual(config['concurrency_ladder'], [64, 48, 32, 16])
        self.assertEqual((config['initial_workers'], config['max_workers'], config['validation_workers']),
                         (64, 64, 16))
        self.assertEqual(pilot.read(self.run / 'control.json'), {'workers': 64, 'auto_scale': True,
                         'pause_dispatch': True, 'pause_reason': 'awaiting_authorized_leader_launch'})
        self.assertEqual((manifest['summary']['paper_count'], manifest['summary']['pair_count'],
                          manifest['summary']['unique_selected_pdf_count']), (200, 600, 800))
        self.assertFalse(plan['sample_selection']['pdf_availability_affects_selection'])
        for unit in plan['selected_units']:
            self.assertEqual(unit['selected_version_ids'], [1, 3, 5, 6])
            self.assertEqual([(e['from_version'], e['to_version']) for e in unit['selected_transitions']],
                             [(1, 3), (3, 5), (5, 6)])
        rows = [json.loads(line) for line in (self.run / 'version_inventory.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 1200)
        self.assertEqual(sum(row['selected_for_comparison'] for row in rows), 800)
        self.assertEqual(pilot.read(self.run / 'preparation_verification.json')['errors'], [])
        self.assertFalse((self.run / 'attempts').exists())

    def test_cli_accepts_explicit_200_unit_ladder_and_validation_workers(self):
        with mock.patch.object(pilot, 'prepare', return_value={'status': 'prepared', 'summary': {}}) as prepare, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(pilot.main(['--project', str(self.project), '--source', 'acl2026',
                '--run', 'res/new', '--source-revision', self.args.source_revision, '--units', '200', '--workers', '64',
                '--concurrency-ladder', '64', '48', '32', '16', '--validation-workers', '16']), 0)
        args = prepare.call_args.args[0]
        self.assertEqual((args.units, args.workers, args.validation_workers), (200, 64, 16))
        self.assertEqual(args.concurrency_ladder, [64, 48, 32, 16])

    def test_parallel_observations_keep_original_inventory_order_and_checks(self):
        self.args.units, self.args.validation_workers = 1, 4
        first_four = threading.Barrier(4, timeout=5)
        other_finished = threading.Event()
        lock = threading.Lock()
        completed, thread_ids = [], set()
        original = pilot.observe

        def observe(*args):
            number = args[2]['version']
            with lock:
                thread_ids.add(threading.get_ident())
            if number <= 4:
                first_four.wait()
            if number == 1:
                self.assertTrue(other_finished.wait(timeout=5))
            result = original(*args)
            with lock:
                completed.append(number)
            if number != 1:
                other_finished.set()
            return result

        with mock.patch.object(pilot, 'observe', side_effect=observe) as observed:
            self.prepare()
        self.assertEqual(observed.call_count, 6)
        self.assertEqual(len(thread_ids), 4)
        self.assertNotEqual(completed[0], 1)
        rows = [json.loads(line) for line in (self.run / 'version_inventory.jsonl').read_text().splitlines()]
        self.assertEqual([row['version'] for row in rows], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(row['pdf_observation']['pdf']['sha256_recomputed'] for row in rows))
        self.assertTrue(all(row['pdf_observation']['page_count']['value'] == 1 for row in rows))
        self.assertEqual(pilot.read(self.run / 'preparation_verification.json')['errors'], [])

    def test_200_unit_missing_selected_pdf_blocks_without_replacement(self):
        self.make_fixture(201)
        self.args.units = 200
        aid = self.records[0]['arxiv_id']
        (self.project / f'data/pdf/{aid}/{aid}v3.pdf').unlink()
        with self.assertRaisesRegex(ValueError, 'Fixed scope preserved'):
            self.prepare()
        self.assertEqual(pilot.read(self.run / 'scope_input.json')['records'], self.records[:200])
        self.assertEqual(pilot.read(self.run / 'execution_manifest.json')['status'], 'blocked')
        self.assertTrue(pilot.read(self.run / 'control.json')['pause_dispatch'])

    def test_invalid_counts_and_ladders_fail_before_reading_sources_or_creating_run(self):
        changes = [{'units': value} for value in (0, -1, True, 1.5)]
        changes += [{'workers': value} for value in (0, 65, True, 1.5)]
        changes += [{'timeout': 0}]
        changes += [{'validation_workers': value} for value in (0, -1, 17, 65, True, 1.5)]
        changes += [{'workers': 64, 'concurrency_ladder': value}
                    for value in ([], [64, 32, 48, 16], [64, 48, 32, 32], [64, 48, 32, 0],
                                  [64.0, 48, 32, 16])]
        changes += [{'workers': 20, 'concurrency_ladder': [64, 48, 32, 16]}]
        original = vars(self.args).copy()
        for change in changes:
            with self.subTest(change=change):
                self.args = argparse.Namespace(**{**original, **change})
                with mock.patch.object(pilot, 'select_scope', side_effect=AssertionError('Invalid configuration read sources')):
                    with self.assertRaises(ValueError):
                        self.prepare()
                self.assertFalse(self.run.exists())

    def test_ordered_source_union_deduplicates_without_pdf_sampling(self):
        pilot.save(self.code_root / 'data/index/second.json', {'dataset': 'source_data', 'schema_version': 'source-data-source-shard-1',
                                                     'source_id': 'second', 'record_count': 11,
                                                     'records': list(reversed(self.records))})
        with mock.patch.object(pilot.preparation, 'validate_pdf', side_effect=AssertionError('PDF sampling')):
            scope = pilot.select_scope(self.code_root, ['second', 'acl2026'], 7)
        self.assertEqual(scope['records'], list(reversed(self.records))[:7])
        self.assertTrue(all(m == ['second', 'acl2026'] for m in scope['record_memberships'].values()))

    def test_short_or_unresolved_fixed_scope_is_never_replaced(self):
        self.args.units = 12
        with self.assertRaisesRegex(ValueError, 'Expected 12 distinct units'):
            self.prepare()
        self.assertFalse(self.run.exists())
        self.args.units = 1
        source = self.code_root / 'data/index/acl2026.json'
        rows = pilot.read(source)
        rows['records'][0]['temporal_eligibility']['status'] = 'unresolved'
        pilot.save(source, rows)
        self.sign_sources()
        with self.assertRaisesRegex(ValueError, 'Fixed unit lacks a resolved eligible sequence'):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_configured_run_root_and_codex_are_frozen_without_model_calls(self):
        self.args.runs_root = Path('res/custom')
        self.args.run = Path('res/custom/custom-pilot')
        self.args.codex = '/fixture/toolchain/codex'
        self.run = self.project / self.args.run
        manifest = self.prepare()
        config = pilot.read(self.run / 'run_config.json')
        self.assertEqual(config['codex_path'], self.args.codex)
        self.assertEqual(config['prepared_path'], str(self.run / 'prepared'))
        self.assertEqual(manifest['artifact_sha256']['run_config.json'], pilot.proof(self.run / 'run_config.json')['sha256'])
        self.assertFalse(manifest['model_execution_performed'])
        self.assertFalse((self.project / 'res/pilot').exists())
        self.assertEqual(pilot.read(self.run / 'preparation_verification.json')['errors'], [])

    def test_run_root_cannot_overlap_original_inputs_or_source_code(self):
        for root in (self.project / 'check_diff', self.project / 'utility', self.project / 'data',
                     self.project / 'data/index', self.project / 'data/pdf', self.ledger_path.parent, self.project):
            with self.subTest(root=root):
                args = argparse.Namespace(**vars(self.args))
                args.runs_root = root
                args.run = root / 'new-run'
                with self.assertRaises(ValueError):
                    pilot.prepare(args)
                self.assertFalse(args.run.exists())

    def test_direct_run_rejects_reserved_descendants_under_normal_res_root(self):
        for relative in ('res/archive/new', 'res/.migration/new'):
            with self.subTest(run=relative):
                args = argparse.Namespace(**vars(self.args))
                args.runs_root = Path('res')
                args.run = Path(relative)
                before = sorted(str(path.relative_to(self.project)) for path in self.project.rglob('*'))
                with mock.patch.object(pilot, 'verified_sources', side_effect=AssertionError('Reserved run read sources')):
                    with self.assertRaisesRegex(ValueError, 'outside archive and migration storage'):
                        pilot.prepare(args)
                self.assertFalse((self.project / args.run).exists())
                self.assertEqual(sorted(str(path.relative_to(self.project))
                                        for path in self.project.rglob('*')), before)

    def test_fixed_scope_all_edges_frozen_bundle_and_independent_audit(self):
        manifest = self.prepare()
        self.assertEqual(manifest['status'], 'prepared')
        self.assertFalse(manifest['model_execution_performed'])
        scope = pilot.read(self.run / 'scope_input.json')
        plan = pilot.read(self.run / 'prepared/selection_manifest.json')
        self.assertEqual(plan['schema_version'], pilot.preparation.PLAN_SCHEMA_VERSION)
        self.assertEqual(scope['records'], self.records[:10])
        self.assertEqual(plan['records'], self.records[:10])
        self.assertEqual(set(tuple(v) for v in scope['record_memberships'].values()), {('acl2026',)})
        self.assertEqual(plan['summary']['pair_count'], 30)
        for unit in plan['selected_units']:
            self.assertEqual(unit['selected_version_ids'], [1, 3, 5, 6])
            self.assertEqual([(e['from_version'], e['to_version']) for e in unit['selected_transitions']],
                             [(1, 3), (3, 5), (5, 6)])
            self.assertTrue(all(e['gap_seconds'] > 2592000 for e in unit['selected_transitions']))
        inventory = [json.loads(line) for line in (self.run / 'version_inventory.jsonl').read_text().splitlines()]
        self.assertEqual(len(inventory), 60)
        self.assertEqual(sum(row['selected_for_comparison'] for row in inventory), 40)
        for row in inventory:
            self.assertNotEqual(row['pdf_observation']['observed_at_utc'], '2020-01-01T00:00:00Z')
            self.assertIsNone(row['pdf_observation']['page_count']['page_count_reason'])
        config = pilot.read(self.run / 'run_config.json')
        self.assertNotIn('/Users/', json.dumps(config))
        self.assertEqual((config['model'], config['reasoning_effort'], config['max_attempts'],
                          config['max_workers'], config['timeout_seconds']), ('gpt-6-astra', 'max', 2, 20, 3600))
        self.assertEqual(pilot.read(self.run / 'control.json'), {'workers': 20, 'auto_scale': False,
                         'pause_dispatch': True, 'pause_reason': 'awaiting_authorized_leader_launch'})
        self.assertEqual(pilot.read(self.run / 'transfer_progress.json')['mode'], 'existing_store')
        for path, expected in manifest['artifact_sha256'].items():
            self.assertEqual(pilot.proof(self.run / path)['sha256'], expected)
        for name, payload in self.core.items():
            self.assertEqual((self.run / 'source_snapshot/check_diff' / name).read_bytes(), payload)
        for name in pilot.HELPERS:
            self.assertEqual((self.run / 'source_snapshot' / name).read_bytes(),
                             self.helpers[name])
        for name in pilot.FROZEN_FILES:
            folder = ('controller_versions/bundle' if name in pilot.BUNDLE else
                      'source_snapshot/utility' if name == 'token_usage.py' else 'source_snapshot')
            target = self.run / folder / name
            self.assertEqual(target.read_bytes(), self.frozen_files[name])
            self.assertEqual(manifest['source_file_locations'][str(target.relative_to(self.run))],
                             {'path': str(self.code_root / 'utility' / name),
                              'source_revision': self.args.source_revision,
                              **pilot.proof(self.code_root / 'utility' / name)})
        for source in (self.ledger_path, self.ledger_path.parent / 'provenance.json'):
            target = self.run / 'source_snapshot' / source.relative_to(self.project)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertEqual(manifest['source_file_locations'][str(target.relative_to(self.run))],
                             {'path': str(source), 'source_revision': self.args.source_revision,
                              **pilot.proof(source)})
        # Fresh interpreter proves the frozen bundle imports without project tools.
        check = '''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0, str(Path(sys.argv[1]) / 'controller_versions/bundle'))
from local_batch import Batch
from verify_local_batch import Verifier
with mock.patch('subprocess.Popen', side_effect=AssertionError('Model dispatch')):
    batch = Batch(Path(sys.argv[1]))
    assert len(batch.unit_pairs) == 10 and len(batch.pairs) == 30
    assert batch.summary()['sources']['acl2026']['units'] == 10
    command = batch.runner.build_command(SimpleNamespace(codex='codex', model='gpt-6-astra', effort='max'), batch.run)
    assert 'model_reasoning_effort="max"' in command
    assert 'web_search="disabled"' in command
    assert 'sandbox_workspace_write.network_access=false' in command
    assert command[command.index('--model') + 1] == 'gpt-6-astra'
    report = Verifier(batch.run).verify()
    assert report['errors'] == [], report['errors']
    assert report['status'] == 'in_progress'
    assert report['token_usage']['status'] == 'not_run'
print('Frozen Batch and independent Audit: 10 units, 30 pairs, zero dispatch')
'''
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join(str(Path(p).resolve())
                                          for p in env.get('PYTHONPATH', '').split(os.pathsep) if p)
        result = subprocess.run([sys.executable, '-B', '-c', check, str(self.run)],
                                cwd=self.project, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.run / 'attempts').exists())
        self.assertFalse((self.run / 'output').exists())

    def test_missing_selected_pdf_blocks_without_resampling(self):
        aid = self.records[0]['arxiv_id']
        (self.project / f'data/pdf/{aid}/{aid}v3.pdf').unlink()
        with self.assertRaisesRegex(ValueError, 'Fixed scope preserved'):
            self.prepare()
        self.assertEqual(pilot.read(self.run / 'scope_input.json')['records'], self.records[:10])
        self.assertEqual(pilot.read(self.run / 'execution_manifest.json')['status'], 'blocked')
        self.assertTrue(pilot.read(self.run / 'control.json')['pause_dispatch'])

    def test_missing_skipped_pdf_preserves_unresolved_all_version_metadata(self):
        aid = self.records[0]['arxiv_id']
        (self.project / f'data/pdf/{aid}/{aid}v2.pdf').unlink()
        self.prepare()
        rows = [json.loads(line) for line in (self.run / 'version_inventory.jsonl').read_text().splitlines()]
        row = next(r for r in rows if r['requested_id'] == f'{aid}v2')
        self.assertFalse(row['selected_for_comparison'])
        self.assertEqual(row['frozen_version_metadata'], self.records[0]['versions'][1])
        self.assertIsNone(row['pdf_observation']['page_count']['value'])
        self.assertTrue(row['pdf_observation']['page_count']['page_count_reason'])

    def test_rejects_bad_ledger_and_existing_run(self):
        self.ledger_path.write_bytes(self.ledger_path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash/size'):
            self.prepare()
        self.assertFalse(self.run.exists())
        self.run.mkdir(parents=True)
        sentinel = self.run / 'preserve.txt'
        sentinel.write_text('Existing evidence')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.prepare()
        self.assertEqual(sentinel.read_text(), 'Existing evidence')

    def test_no_git_export_accepts_file_map_manifest_and_freezes_canonical_shard(self):
        self.assertFalse((self.code_root / '.git').exists())
        manifest = pilot.read(self.manifest_path)
        self.assertIsInstance(manifest['files'], dict)
        with mock.patch('subprocess.run', side_effect=AssertionError('Git forbidden')):
            result = self.prepare()
        self.assertEqual(result['source_revision'], self.args.source_revision)
        self.assertEqual(result['status'], 'prepared')
        self.assertTrue(pilot.read(self.run / 'control.json')['pause_dispatch'])
        for name in ('check_diff/runner.py', 'data/index/index.json', 'data/index/acl2026.json'):
            self.assertEqual((self.run / 'source_snapshot' / name).read_bytes(),
                             (self.code_root / name).read_bytes())

    def test_exported_test_fixture_requires_verified_current_manifest(self):
        with mock.patch('subprocess.run', side_effect=AssertionError('Git forbidden')):
            blobs, _ = pilot.verified_sources(self.project, self.code_root, ['acl2026'], self.args.source_revision)
            self.assertEqual({name: blobs['check_diff/' + name] for name in pilot.CORE_FILES}, self.core)
        manifest = pilot.read(self.manifest_path)
        manifest['files']['check_diff/schema.py']['sha256'] = '0' * 64
        pilot.save(self.manifest_path, manifest)
        with self.assertRaises(ValueError):
            pilot.verified_sources(self.project, self.code_root, ['acl2026'], self.args.source_revision)

    def test_bad_manifest_hash_or_commit_fails_before_run_creation(self):
        original = pilot.read(self.manifest_path)
        for field in ('check_diff/runner.py', 'data/index/acl2026.json', 'revision'):
            with self.subTest(field=field):
                manifest = copy.deepcopy(original)
                if field == 'revision':
                    manifest[field] = '0' * 64
                else:
                    manifest['files'][field]['sha256'] = '0' * 64
                pilot.save(self.manifest_path, manifest)
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.run.exists())

    def test_old_commit_manifest_is_rejected_without_git_fallback(self):
        manifest = pilot.read(self.manifest_path)
        manifest['revision'] = 'f' * 64
        pilot.save(self.manifest_path, manifest)
        (self.code_root / '.git').write_text('must not use fallback')
        with mock.patch('subprocess.run', side_effect=AssertionError('Git fallback')):
            with self.assertRaises(ValueError):
                self.prepare()
        self.assertFalse(self.run.exists())

    def test_changes_outside_selected_prefix_still_fail_full_shard_provenance(self):
        self.args.units = 1
        source = self.code_root / 'data/index/acl2026.json'
        rows = pilot.read(source)
        rows['records'][-1]['title'] = 'Changed outside authorized prefix'
        pilot.save(source, rows)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_modified_controller_after_pin_is_rejected_before_run_creation(self):
        pinned_manifest = self.manifest_path.read_bytes()
        pinned_revision = self.args.source_revision
        for name in pilot.BUNDLE + ('prepare_batch.py', 'workflow.py'):
            with self.subTest(source=name):
                path = self.code_root / 'utility' / name
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b'\n# Unauthorized change after the source pin.\n')
                    with mock.patch.object(pilot, 'freeze', side_effect=AssertionError('Unverified source reached freeze')):
                        with self.assertRaisesRegex(ValueError, 'Source manifest hash/size mismatch') as failure:
                            self.prepare()
                    self.assertIn('utility/' + name, str(failure.exception))
                    self.assertFalse(self.run.exists())
                    self.assertEqual(self.args.source_revision, pinned_revision)
                    self.assertEqual(self.manifest_path.read_bytes(), pinned_manifest)
                finally:
                    path.write_bytes(original)

    def test_changed_ledger_with_refreshed_provenance_requires_new_source_pin(self):
        pinned_manifest = self.manifest_path.read_bytes()
        pinned_revision = self.args.source_revision
        ledger = [json.loads(line) for line in self.ledger_path.read_text().splitlines()]
        ledger[0]['observed_at_utc'] = '2026-09-13T00:00:00Z'
        pilot.save_rows(self.ledger_path, ledger)
        provenance_path = self.ledger_path.parent / 'provenance.json'
        provenance = pilot.read(provenance_path)
        provenance['files']['version_metadata.jsonl'] = pilot.proof(self.ledger_path)
        pilot.save(provenance_path, provenance)
        self.assertEqual(pilot.read(provenance_path)['files']['version_metadata.jsonl'],
                         pilot.proof(self.ledger_path))
        relative = str(self.ledger_path.relative_to(self.project))
        self.assertNotEqual(json.loads(pinned_manifest)['files'][relative], pilot.proof(self.ledger_path))
        with mock.patch.object(pilot, 'freeze', side_effect=AssertionError('Unverified metadata reached freeze')):
            with self.assertRaisesRegex(ValueError, 'Source manifest hash/size mismatch') as failure:
                self.prepare()
        self.assertIn(relative, str(failure.exception))
        self.assertFalse(self.run.exists())
        self.assertEqual(self.args.source_revision, pinned_revision)
        self.assertEqual(self.manifest_path.read_bytes(), pinned_manifest)

    def test_authorized_alternate_loaded_module_bytes_are_rejected_before_run_creation(self):
        loaded_paths = {
            'prepare_source_data.py': Path(pilot.preparation.__file__),
            'prepare_input_data.py': Path(pilot.preparation.materialize.__code__.co_filename),
            'source_provenance.py': Path(pilot.source_provenance.__file__),
            'prepare_batch.py': Path(pilot.__file__),
            'verify_local_batch.py': Path(pilot.audit.__file__),
            'completed_turn.py': Path(pilot.audit.recovery.__file__)}
        for name, loaded_path in loaded_paths.items():
            with self.subTest(module=name):
                path = self.code_root / 'utility' / name
                original, loaded_bytes = path.read_bytes(), loaded_path.read_bytes()
                prior_revision = self.args.source_revision
                try:
                    path.write_bytes(original + b'\n# Authorized alternate project source.\n')
                    self.sign_sources()
                    self.assertNotEqual(self.args.source_revision, prior_revision)
                    blobs, _ = pilot.verified_sources(self.project, self.code_root,
                                                     ['acl2026'], self.args.source_revision)
                    self.assertEqual(blobs['utility/' + name], path.read_bytes())
                    self.assertNotEqual(blobs['utility/' + name], loaded_bytes)
                    with mock.patch.object(pilot, 'freeze', side_effect=AssertionError('Wrong executing source reached freeze')):
                        with self.assertRaisesRegex(ValueError, 'Loaded preparation helper differs') as failure:
                            self.prepare()
                    self.assertIn(name, str(failure.exception))
                    self.assertFalse(self.run.exists())
                    self.assertEqual(loaded_path.read_bytes(), loaded_bytes)
                finally:
                    path.write_bytes(original)

    def test_verified_helper_bytes_detect_loaded_mirror_changes(self):
        original_read = Path.read_bytes
        helper_path = Path(pilot.preparation.__file__)
        def changed_helper(path):
            raw = original_read(path)
            return raw + b'\n# unverified helper\n' if path == helper_path else raw
        with mock.patch.object(Path, 'read_bytes', changed_helper):
            with self.assertRaisesRegex(ValueError, 'Loaded preparation helper differs'):
                self.prepare()
        self.assertFalse(self.run.exists())

    def test_legacy_manifest_shapes_are_rejected_before_run_creation(self):
        original = pilot.read(self.manifest_path)
        entries = original['files']
        shapes = {
            'list': [{'path': name, **row} for name, row in entries.items()],
            'hash_only': {name: row['sha256'] for name, row in entries.items()}}
        for label, files in shapes.items():
            with self.subTest(shape=label):
                manifest = copy.deepcopy(original)
                manifest['files'] = files
                pilot.save(self.manifest_path, manifest)
                with mock.patch('subprocess.run', side_effect=AssertionError('Git fallback')):
                    with self.assertRaises(ValueError):
                        self.prepare()
                self.assertFalse(self.run.exists())

    def test_source_commit_is_required_before_creating_a_run(self):
        self.args.source_revision = None
        with self.assertRaisesRegex(ValueError, 'source-revision'):
            self.prepare()
        self.assertFalse(self.run.exists())

    def test_unverified_export_has_no_git_fallback(self):
        self.manifest_path.unlink()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run.exists())


if __name__ == '__main__':
    unittest.main()

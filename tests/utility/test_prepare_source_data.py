"""Regression checks for the bounded source_data preparation adapter."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
from itertools import combinations
import random
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pypdf import PdfWriter

from utility import prepare_source_data as pilot
from utility import prepare_input_data as helpers


def paper(aid='2501.00001', source_ids=None, days=(0, 1, 31, 62)):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    versions = [{'version': i + 1,
                 'submitted_at': (start + timedelta(days=day)).isoformat(),
                 'date_status': 'known', 'pdf_url': f'https://arxiv.org/pdf/{aid}v{i + 1}',
                 'pdf': {'status': 'unresolved', 'sha256': None},
                 'page_count': {'value': None, 'status': 'unresolved'},
                 'license_metadata': {'status': 'unknown_historical_version', 'license': None}}
                for i, day in enumerate(days)]
    sources = source_ids or ['source_a']
    return {'arxiv_id': aid, 'arxiv_url': f'https://arxiv.org/abs/{aid}',
            'title': 'Preserve the original metadata', 'source_ids': sources,
            'source_entries': [{'source_id': s, 'presentation_type': None} for s in sources],
            'frozen_N': len(versions), 'versions': versions, 'extra_metadata': {'untouched': [None, 'α']},
            'temporal_eligibility': {'status': 'eligible', 'witness_versions': [1, 3, 4],
                'witness_edges': [{'from_version': 1, 'to_version': 3,
                                   'gap_seconds': (days[2] - days[0]) * 86400},
                                  {'from_version': 3, 'to_version': 4,
                                   'gap_seconds': (days[3] - days[2]) * 86400}]}}


def write_source(path, records):
    path.write_text(json.dumps({'dataset': 'source_data', 'record_count': len(records),
                                'language': 'en', 'records': records}))


def write_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(path)


class TemporalTests(unittest.TestCase):
    def test_skips_short_unselected_gaps(self):
        selected, edges = pilot.select_versions(paper())
        self.assertEqual([v['version'] for v in selected], [1, 3, 4])
        self.assertEqual([e['gap_seconds'] for e in edges], [31 * 86400, 31 * 86400])

    def test_four_or_more_selected_versions_and_full_history(self):
        item = paper(days=(0, 2, 31, 62, 64, 93, 124))
        selected, edges = pilot.select_versions(item)
        self.assertEqual([v['version'] for v in selected], [1, 3, 4, 6, 7])
        self.assertEqual(len(edges), 4)
        self.assertEqual(item['temporal_eligibility']['witness_versions'], [1, 3, 4])
        for edge in edges:
            self.assertGreater(edge['gap_seconds'], pilot.GAP_SECONDS)
            self.assertIn('from_submitted_at', edge)
            self.assertIn('to_submitted_at', edge)

    def test_exactly_thirty_days_does_not_qualify(self):
        selected, edges = pilot.select_versions(paper(days=(0, 30, 31, 61, 62, 93)))
        self.assertEqual([v['version'] for v in selected], [1, 3, 5, 6])
        self.assertEqual(len(edges), 3)
        self.assertTrue(all(edge['gap_seconds'] > 30 * 86400 for edge in edges))

    def test_one_second_boundary_and_utc_offsets(self):
        item = paper()
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        seconds = (0, pilot.GAP_SECONDS, pilot.GAP_SECONDS + 1, 2 * pilot.GAP_SECONDS + 2)
        for version, elapsed in zip(item['versions'], seconds):
            version['submitted_at'] = (start + timedelta(seconds=elapsed)).astimezone(
                timezone(timedelta(hours=8))).isoformat()
        selected, edges = pilot.select_versions(item)
        self.assertEqual([v['version'] for v in selected], [1, 3, 4])
        self.assertEqual([e['gap_seconds'] for e in edges], [pilot.GAP_SECONDS + 1] * 2)

    def test_invalid_history_numbers_remain_unresolved(self):
        for changes in ({'frozen_N': True}, {'frozen_N': 3}, {'frozen_N': None},
                        {'versions': None}, {'versions': [{'version': 1}, {'version': 1}]},
                        {'versions': [{'version': True}]}, {'versions': [{}]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'unresolved'):
                pilot.select_versions({**paper(), **changes})

    def test_long_first_to_last_span_is_insufficient(self):
        selected, edges = pilot.select_versions(paper(days=(0, 1, 2, 100)))
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(edges), 1)

    def test_missing_even_unselected_date_leaves_maximum_unresolved(self):
        item = paper()
        original_eligibility = copy.deepcopy(item['temporal_eligibility'])
        item['versions'][1]['submitted_at'] = None
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            pilot.select_versions(item)
        self.assertEqual(item['temporal_eligibility'], original_eligibility)

    def test_naive_or_conflicting_timestamp_is_unresolved(self):
        item = paper()
        item['versions'][0]['submitted_at'] = '2025-01-01T00:00:00'
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            pilot.select_versions(item)
        item = paper(days=(0, 40, 31, 62))
        with self.assertRaisesRegex(ValueError, 'original version order'):
            pilot.select_versions(item)

    def test_greedy_matches_brute_force_optimum_and_earliest_tie(self):
        rng = random.Random(43)
        for _ in range(100):
            days = sorted(rng.randrange(250) for _ in range(rng.randrange(4, 10)))
            item = paper(days=days)
            selected, _ = pilot.select_versions(item)
            feasible = []
            for size in range(1, len(days) + 1):
                for indices in combinations(range(len(days)), size):
                    if all(days[b] - days[a] > 30 for a, b in zip(indices, indices[1:])):
                        feasible.append(indices)
            optimum = min(feasible, key=lambda seq: (-len(seq), seq))
            self.assertEqual([v['version'] for v in selected], [i + 1 for i in optimum])


class SourceTests(unittest.TestCase):
    def test_identical_shard_duplicates_preserved_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = paper(source_ids=['a', 'b'])
            files = []
            for name in ('a', 'b'):
                payload = json.dumps({'dataset': 'source_data', 'records': [original]}).encode()
                (root / f'{name}.json').write_bytes(payload)
                files.append({'path': f'{name}.json', 'bytes': len(payload), 'record_count': 1,
                              'sha256': hashlib.sha256(payload).hexdigest()})
            index = {'dataset': 'source_data', 'dataset_metadata': {'dataset': 'source_data', 'record_count': 1},
                     'record_order': [original['arxiv_id']],
                     'files': files}
            path = root / 'index.json'
            path.write_text(json.dumps(index))
            records, metadata = pilot.load_source(path)
            self.assertEqual(records, [original])
            self.assertEqual(metadata['duplicate_memberships_collapsed'], 1)
            self.assertEqual(len(metadata['input_files']), 3)
            (root / 'a.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                pilot.load_source(path)

    def test_conflicting_duplicate_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            one = paper()
            two = copy.deepcopy(one)
            two['title'] = 'Conflicting title'
            path = Path(temp) / 'source.json'
            write_source(path, [one, two])
            with self.assertRaisesRegex(ValueError, 'Conflicting duplicate'):
                pilot.load_source(path)

    def test_assignment_is_unique_deterministic_and_handles_overlap(self):
        pools = {'a': ['shared', 'only_a'], 'b': ['shared']}
        first = pilot.assign_sources(pools, 1, 20260911)
        self.assertEqual(first, pilot.assign_sources(pools, 1, 20260911))
        self.assertEqual(first, {'only_a': 'a', 'shared': 'b'})
        with self.assertRaisesRegex(ValueError, 'Cannot assign'):
            pilot.assign_sources(pools, 2, 20260911)

    def test_total_units_redistributes_capped_source_quotas(self):
        pools = {'a': ['shared'], 'b': ['shared', 'b1', 'b2', 'b3', 'b4'],
                 'c': ['c1', 'c2', 'c3', 'c4']}
        assignment = pilot.assign_sources(pools, 5, 20260911, units=7)
        self.assertEqual(len(assignment), 7)
        self.assertEqual({s: list(assignment.values()).count(s) for s in pools},
                         {'a': 1, 'b': 3, 'c': 3})
        self.assertEqual(assignment, pilot.assign_sources(pools, 5, 20260911, units=7))
        with self.assertRaisesRegex(ValueError, '9 available'):
            pilot.assign_sources(pools, 5, 20260911, units=10)

    def test_total_units_caps_overlapping_sources_by_distinct_assignment(self):
        pools = {'a': ['shared'], 'b': ['shared'], 'c': ['c1', 'c2', 'c3']}
        assignment = pilot.assign_sources(pools, 5, 20260911, units=4)
        self.assertEqual(len(assignment), 4)
        self.assertEqual({s: list(assignment.values()).count(s) for s in pools},
                         {'a': 1, 'b': 0, 'c': 3})


class PreparationTests(unittest.TestCase):
    def test_reuses_python311_helpers_unmodified(self):
        self.assertIs(pilot.materialize, helpers.materialize)
        self.assertIs(pilot.validate_original_pdf, helpers.validate_pdf)
        self.assertIs(pilot.write_json, helpers.write_json)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'payload.bin'
            payload = b'preserve streaming digest behavior\x00' * 70000
            path.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(helpers.digest(path), expected)

    def test_materialize_copy_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source.pdf', Path(temp) / 'target.pdf'
            source.write_bytes(b'PDF fixture bytes')
            with mock.patch.object(helpers.os, 'link', side_effect=OSError('No hardlinks')):
                helpers.materialize(source, target, helpers.digest(source))
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertFalse(target.is_symlink())

    def test_full_metadata_and_pair_layout_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            item = paper()
            original = copy.deepcopy(item)
            source = root / 'source.json'
            write_source(source, [item])
            pdf_root = root / 'pdf'
            for number in (1, 3, 4):
                write_pdf(pilot.pdf_path(pdf_root, item['arxiv_id'], number))
            out = root / 'prepared'
            args = argparse.Namespace(source=source, pdf_root=pdf_root, output=out,
                                      per_source=1, seed=20260911, dry_run=False)
            with redirect_stdout(io.StringIO()):
                manifest = pilot.prepare(args)
            self.assertEqual(manifest['records'], [original])
            restored = json.loads((out / item['arxiv_id'] / 'metadata.json').read_text())
            self.assertEqual(restored, original)
            pairs = json.loads((out / 'pairs.json').read_text())['pairs']
            self.assertEqual(len(pairs), 2)
            self.assertEqual([p['gap_days'] for p in pairs], [31, 31])
            for pair in pairs:
                for key in ('versions', 'selected_version_ids', 'source_entries',
                            'temporal_eligibility', 'raw_metadata', 'extra_metadata'):
                    self.assertNotIn(key, pair)
                for side in ('before', 'after'):
                    endpoint = pair[side]
                    self.assertEqual(endpoint['pages'], 1)
                    self.assertEqual(endpoint['page_count']['value'], None)
                    self.assertTrue((out / endpoint['pdf_path']).is_file())
                    self.assertEqual(endpoint['sha256'], hashlib.sha256(
                        (out / endpoint['pdf_path']).read_bytes()).hexdigest())
            self.assertFalse((out / item['arxiv_id'] / 'v1_v2').exists())
            self.assertEqual(len(restored['versions']), 4)
            self.assertEqual(json.loads(source.read_text())['records'], [original])

    def test_plan_preserves_missing_pdf_papers_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ready, missing = paper(), paper('2501.00002', days=(0, 1, 31, 62, 93))
            source = root / 'source.json'
            write_source(source, [ready, missing])
            pdf_root = root / 'pdf'
            for number in (1, 3, 4):
                write_pdf(pilot.pdf_path(pdf_root, ready['arxiv_id'], number))
            args = argparse.Namespace(source=source, pdf_root=pdf_root, output=root / 'planned',
                                      per_source=1, units=2, seed=20260911, dry_run=False, plan_only=True)
            with redirect_stdout(io.StringIO()), mock.patch.object(pilot, 'validate_pdf') as validator:
                plan = pilot.prepare(args)
            validator.assert_not_called()
            self.assertEqual(plan['summary']['paper_count'], 2)
            self.assertEqual(plan['summary']['pair_count'], 5)
            self.assertEqual(plan['summary']['unique_selected_pdf_count'], 7)
            self.assertEqual(plan['summary']['missing_pdf_count_at_plan'], 4)
            self.assertEqual(plan['excluded_records'], [])
            self.assertEqual(plan['sample_selection']['source_assignment'],
                             {ready['arxiv_id']: 'source_a', missing['arxiv_id']: 'source_a'})
            manifest = args.output / 'selection_manifest.json'
            frozen_bytes = manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, 'fixed paper IDs preserved'):
                pilot.materialize_plan(manifest)
            self.assertEqual(frozen_bytes, manifest.read_bytes())
            for number in (1, 3, 4, 5):
                write_pdf(pilot.pdf_path(pdf_root, missing['arxiv_id'], number))
            pilot.materialize_plan(manifest)
            self.assertEqual(frozen_bytes, manifest.read_bytes())
            pairs = json.loads((args.output / 'pairs.json').read_text())['pairs']
            self.assertEqual(len(pairs), 5)
            self.assertEqual({p['arxiv_id'] for p in pairs}, {ready['arxiv_id'], missing['arxiv_id']})
            self.assertTrue((args.output / missing['arxiv_id'] / 'v4_v5' / 'v5.pdf').is_file())

    def test_planned_sample_is_independent_of_pdf_presence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = [paper(f'2501.{i:05d}') for i in range(1, 8)]
            source = root / 'source.json'
            write_source(source, records)
            args = argparse.Namespace(source=source, pdf_root=root / 'pdf', output=root / 'out',
                                      per_source=1, units=4, seed=20260911, dry_run=True)
            first = pilot.build_plan(args)
            for item in records:
                for number in (1, 3, 4):
                    write_pdf(pilot.pdf_path(args.pdf_root, item['arxiv_id'], number))
            second = pilot.build_plan(args)
            self.assertEqual(first['sample_selection'], second['sample_selection'])
            self.assertEqual(first['records'], second['records'])
            self.assertGreater(first['summary']['missing_pdf_count_at_plan'], 0)
            self.assertEqual(second['summary']['missing_pdf_count_at_plan'], 0)

    def test_live_pdf_must_match_frozen_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'version.pdf'
            write_pdf(path)
            item = paper()
            version = item['versions'][0]
            version['pdf']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                pilot.validate_pdf(path, item['arxiv_id'], version)

    def test_all_frozen_hash_evidence_and_physical_pages_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'version.pdf'
            writer = PdfWriter()
            for _ in range(3):
                writer.add_blank_page(width=612, height=792)
            writer.write(path)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            item = paper()
            version = item['versions'][0]
            version['pdf'].update(sha256=sha, expected_sha256=sha)
            version['page_count']['source_pdf_sha256'] = sha
            original = copy.deepcopy(version)
            info = pilot.validate_pdf(path, item['arxiv_id'], version)
            self.assertEqual(info['pages'], 3)
            self.assertTrue(info['frozen_sha256_verified'])
            self.assertTrue(info['includes_references_and_appendices'])
            self.assertEqual(version, original)
            version['page_count']['source_pdf_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                pilot.validate_pdf(path, item['arxiv_id'], version)


class PrepFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source.json'
        self.pdf_root = self.root / 'pdf'
        self.output = self.root / 'out'
        self.records = [paper(f'2501.{i:05d}') for i in range(1, 8)]
        write_source(self.source, self.records)
        # Every public operation exercised here must stay local.
        for target in ('socket.create_connection', 'urllib.request.urlopen'):
            guard = mock.patch(target, side_effect=AssertionError('Network access forbidden'))
            guard.start()
            self.addCleanup(guard.stop)

    def args(self, **changes):
        values = dict(source=self.source, pdf_root=self.pdf_root, output=self.output,
                      per_source=None, units=None, seed=20260911, dry_run=False, plan_only=True)
        values.update(changes)
        return argparse.Namespace(**values)

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, 'argv', ['prepare_source_data.py', *map(str, arguments)]), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                pilot.main()
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def freeze(self):
        with redirect_stdout(io.StringIO()):
            plan = pilot.prepare(self.args(units=1))
        return plan, self.output / 'selection_manifest.json'

    def write_selected_pdfs(self, plan, root=None):
        root = root or self.pdf_root
        for request in plan['requested_pdfs']:
            write_pdf(pilot.pdf_path(root, request['arxiv_id'], request['version']))


class BoundedInterfaceTests(PrepFixture):
    def test_default_remains_five_per_source(self):
        plan = pilot.build_plan(self.args())
        self.assertEqual(plan['summary']['paper_count'], 5)
        self.assertEqual(plan['sample_selection']['papers_per_source'], 5)
        self.assertEqual(plan['sample_selection']['seed'], 20260911)

    def test_all_keeps_source_order_and_never_samples(self):
        records = [self.records[i] for i in (5, 1, 6, 0, 2, 4, 3)]
        records[0]['source_ids'] = ['source_a', 'source_b']
        write_source(self.source, records)
        with mock.patch.object(pilot, 'assign_sources', side_effect=AssertionError('Sampling forbidden')):
            first = pilot.build_plan(self.args(**{'all': True}))
            second = pilot.build_plan(self.args(seed=123, **{'all': True}))
        self.assertEqual(first['records'], records)
        self.assertEqual(first['selected_units'], second['selected_units'])
        self.assertEqual(first['sample_selection'], second['sample_selection'])
        self.assertEqual(first['summary']['paper_count'], 7)
        self.assertEqual(first['summary']['pair_count'], 14)
        self.assertEqual(first['summary']['missing_pdf_count_at_plan'], 21)
        self.assertTrue(first['sample_selection']['all_records'])
        self.assertIsNone(first['sample_selection']['papers_per_source'])
        self.assertIsNone(first['sample_selection']['requested_units'])
        self.assertEqual(first['excluded_records'], [])
        pilot.validate_plan(first)

    def test_all_reports_every_ineligible_or_unresolved_record_before_writes(self):
        self.records[1]['temporal_eligibility']['status'] = 'ineligible'
        self.records[3]['versions'][1]['submitted_at'] = None
        write_source(self.source, self.records)
        with self.assertRaisesRegex(ValueError, '--all requires every metadata record') as caught:
            pilot.prepare(self.args(**{'all': True}))
        exclusions = json.loads(str(caught.exception).split('excluded_records=', 1)[1])
        self.assertEqual([r['arxiv_id'] for r in exclusions], ['2501.00002', '2501.00004'])
        self.assertFalse(self.output.exists())

    def test_sampled_mode_records_exclusions_without_pdf_resampling(self):
        self.records[0]['temporal_eligibility']['status'] = 'ineligible'
        self.records[1]['versions'][1]['submitted_at'] = None
        write_source(self.source, self.records)
        plan = pilot.build_plan(self.args())
        self.assertEqual(len(plan['excluded_records']), 2)
        self.assertEqual(plan['summary']['paper_count'], 5)
        self.assertEqual(plan['summary']['missing_pdf_count_at_plan'], 15)

    def test_api_rejects_all_and_sampling_controls(self):
        for settings in ({'all': True, 'units': 1}, {'all': True, 'per_source': 5}):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, '--all cannot'):
                pilot.build_plan(self.args(**settings))

    def test_positive_integer_sampling_controls(self):
        for name in ('per_source', 'units'):
            for value in (0, -1, True, 1.5):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, 'positive'):
                    pilot.build_plan(self.args(**{name: value}))

    def test_cli_dry_run_without_output_or_pdf_parsing(self):
        with mock.patch.object(pilot, 'validate_pdf', side_effect=AssertionError('PDF parsing forbidden')):
            code, stdout, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root, '--dry-run')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(json.loads(stdout)['summary']['paper_count'], 5)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.pdf_root.exists())
        self.assertEqual(list(self.root.iterdir()), [self.source])

    def test_cli_all_plan_only_keeps_missing_pdfs(self):
        code, stdout, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                       '--output', self.output, '--all', '--plan-only')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(json.loads(stdout)['summary']['paper_count'], 7)
        self.assertEqual([p.name for p in self.output.iterdir()], ['selection_manifest.json'])
        self.assertFalse(self.pdf_root.exists())

    def test_cli_units_preserves_legacy_override_of_per_source(self):
        code, stdout, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                       '--per-source', 6, '--units', 2, '--dry-run')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(json.loads(stdout)['summary']['paper_count'], 2)

    def test_cli_all_conflicts_even_with_explicit_default_quota(self):
        for option in ('--units', '--per-source'):
            with self.subTest(option=option):
                code, _, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                           '--all', option, 5, '--dry-run')
                self.assertEqual(code, 2)
                self.assertIn('--all cannot', stderr)

    def test_cli_nonpositive_scope_fails(self):
        for option in ('--units', '--per-source'):
            for value in (0, -1):
                with self.subTest(option=option, value=value):
                    code, _, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                               option, value, '--dry-run')
                    self.assertEqual(code, 2)
                    self.assertIn('positive', stderr)

    def test_cli_requires_output_for_every_write_mode(self):
        for flags in ([], ['--plan-only'], ['--materialize-plan', self.root / 'absent.json']):
            arguments = flags if '--materialize-plan' in flags else [
                '--source', self.source, '--pdf-root', self.pdf_root, *flags]
            with self.subTest(flags=flags):
                code, _, stderr = self.cli(*arguments)
                self.assertEqual(code, 2)
                self.assertIn('Writes require --output', stderr)
        with self.assertRaisesRegex(ValueError, 'Writes require --output'):
            pilot.prepare(self.args(output=None))
        self.assertFalse(self.output.exists())

    def test_cli_requires_source_and_pdf_root_for_planning(self):
        for arguments in ([], ['--source', self.source], ['--pdf-root', self.pdf_root]):
            with self.subTest(arguments=arguments):
                code, _, stderr = self.cli(*arguments, '--dry-run')
                self.assertEqual(code, 2)
                self.assertIn('requires --source and --pdf-root', stderr)

    def test_cli_materialize_rejects_ignored_selection_controls(self):
        for flags in (['--all'], ['--units', 1], ['--per-source', 5], ['--seed', 0],
                      ['--source', self.source], ['--plan-only'], ['--dry-run']):
            with self.subTest(flags=flags):
                code, _, stderr = self.cli('--materialize-plan', self.root / 'absent.json',
                                           '--output', self.output, *flags)
                self.assertEqual(code, 2)
                self.assertIn('--materialize-plan cannot be combined', stderr)

    def test_cli_materialize_transported_plan_with_local_root_override(self):
        plan, manifest = self.freeze()
        raw = manifest.read_bytes()
        new_root, new_output = self.root / 'local-cache', self.root / 'materialized'
        self.write_selected_pdfs(plan, new_root)
        self.source.unlink()  # Freezing/importing must not require old metadata paths.
        code, _, stderr = self.cli('--materialize-plan', manifest, '--pdf-root', new_root,
                                   '--output', new_output)
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual((new_output / 'selection_manifest.json').read_bytes(), raw)
        self.assertEqual(manifest.read_bytes(), raw)
        pairs = json.loads((new_output / 'pairs.json').read_text())['pairs']
        self.assertEqual(len(pairs), 2)
        receipt = json.loads((new_output / 'materialization_manifest.json').read_text())
        self.assertTrue(receipt['sample_unchanged'])
        self.assertFalse(receipt['resampling_performed'])

    def test_cli_missing_pdf_failure_has_fixed_receipt_and_no_pairs(self):
        code, _, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                   '--output', self.output, '--units', 1)
        self.assertEqual(code, 1)
        self.assertIn('fixed paper IDs preserved', stderr)
        receipt = json.loads((self.output / 'materialization_manifest.json').read_text())
        self.assertEqual(receipt['status'], 'unresolved')
        self.assertEqual(len(receipt['failures']), 3)
        self.assertFalse((self.output / 'pairs.json').exists())
        self.assertFalse(self.pdf_root.exists())

    def test_cli_rejects_legacy_or_unlabelled_data_clearly(self):
        for release in ('v1', 'v1.1', 'v1.2', 'v1.3', None):
            self.source.write_text(json.dumps({'release': release, 'records': self.records}))
            with self.subTest(release=release):
                code, _, stderr = self.cli('--source', self.source, '--pdf-root', self.pdf_root,
                                           '--output', self.output, '--units', 1, '--plan-only')
                self.assertEqual(code, 1)
                self.assertIn('must declare dataset source_data', stderr)
                self.assertFalse(self.output.exists())

    def test_script_entrypoint_from_another_working_directory(self):
        result = subprocess.run([sys.executable, '-B', str(Path(pilot.__file__).resolve()),
                                 '--source', str(self.source), '--pdf-root', str(self.pdf_root),
                                 '--all', '--dry-run'], cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['paper_count'], 7)


class ManifestSafetyTests(PrepFixture):
    def test_tampered_manifest_rejected_before_any_destination_write(self):
        plan = pilot.build_plan(self.args(units=1))
        mutations = {
            'duplicate_record': lambda p: p['records'].append(copy.deepcopy(p['records'][0])),
            'duplicate_unit': lambda p: p['selected_units'].append(copy.deepcopy(p['selected_units'][0])),
            'duplicate_request': lambda p: p['requested_pdfs'].append(copy.deepcopy(p['requested_pdfs'][0])),
            'wrong_assignment': lambda p: p['sample_selection']['source_assignment'].update(
                {p['records'][0]['arxiv_id']: 'other'}),
            'wrong_unit_source': lambda p: p['selected_units'][0].update(assigned_source_id='other'),
            'wrong_sequence': lambda p: p['selected_units'][0].update(selected_version_ids=[1, 2, 4]),
            'wrong_edge': lambda p: p['selected_units'][0]['selected_transitions'][0].update(to_version=999),
            'wrong_unit_count': lambda p: p['selected_units'][0].update(pair_count=9),
            'wrong_request_path': lambda p: p['selected_units'][0]['selected_versions'][0].update(pdf_path='../escape.pdf'),
            'wrong_request_id': lambda p: p['selected_units'][0]['selected_versions'][0].update(arxiv_id='2501.99999'),
            'wrong_summary': lambda p: p['summary'].update(pair_count=999),
            'wrong_missing': lambda p: p.update(missing_pdf_ids=[]),
            'wrong_quotas': lambda p: p['sample_selection'].update(source_quotas={'source_a': 9}),
            'wrong_rule': lambda p: p['temporal_rule'].update(operator='>='),
            'legacy_schema': lambda p: p.update(schema_version='legacy'),
            'legacy_release': lambda p: p['dataset_metadata'].update(dataset='legacy_source'),
            'relative_pdf_root': lambda p: p.update(pdf_root='../cache'),
            'path_paper_id': lambda p: p['records'][0].update(arxiv_id='../escape'),
            'ineligible': lambda p: p['records'][0]['temporal_eligibility'].update(status='ineligible'),
        }
        manifest = self.root / 'tampered.json'
        for name, mutate in mutations.items():
            changed = copy.deepcopy(plan)
            mutate(changed)
            manifest.write_text(json.dumps(changed))
            with self.subTest(mutation=name), self.assertRaises(ValueError):
                pilot.materialize_plan(manifest, self.output)
            self.assertFalse(self.output.exists(), name)

    def test_symlink_destinations_and_temporary_files_rejected_before_writes(self):
        plan, manifest = self.freeze()
        self.write_selected_pdfs(plan)
        aid = plan['records'][0]['arxiv_id']
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel.json'
        sentinel.write_text('preserved')
        for relative, target in ((aid, outside), (f'{aid}/v1_v3', outside),
                                 ('materialization_manifest.json', sentinel),
                                 ('materialization_manifest.json.tmp', sentinel),
                                 (f'{aid}/metadata.json', sentinel)):
            path = self.output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
            with self.subTest(path=relative), self.assertRaisesRegex(ValueError, 'output path|temporary path'):
                pilot.materialize_plan(manifest)
            path.unlink()
            self.assertEqual(sentinel.read_text(), 'preserved')
            self.assertFalse((self.output / 'pairs.json').exists())
        temporary = self.output / 'materialization_manifest.json.tmp'
        temporary.write_text('unfinished concurrent write')
        with self.assertRaisesRegex(ValueError, 'temporary path'):
            pilot.materialize_plan(manifest)
        self.assertEqual(temporary.read_text(), 'unfinished concurrent write')

    def test_selected_pdf_symlink_escape_rejected_before_writes(self):
        plan, manifest = self.freeze()
        self.write_selected_pdfs(plan)
        request = plan['requested_pdfs'][0]
        path = Path(request['pdf_path'])
        outside = self.root / 'outside.pdf'
        path.rename(outside)
        path.symlink_to(outside)
        destination = self.root / 'materialized'
        with self.assertRaisesRegex(ValueError, 'PDF path escapes'):
            pilot.materialize_plan(manifest, destination)
        self.assertFalse(destination.exists())

    def test_foreign_or_nonempty_output_is_preserved(self):
        plan, manifest = self.freeze()
        self.write_selected_pdfs(plan)
        foreign = self.root / 'foreign'
        foreign.mkdir()
        sentinel = foreign / 'keep.txt'
        sentinel.write_text('preserved')
        with self.assertRaisesRegex(ValueError, 'not empty'):
            pilot.materialize_plan(manifest, foreign)
        self.assertEqual(list(foreign.iterdir()), [sentinel])
        foreign_manifest = foreign / 'selection_manifest.json'
        foreign_manifest.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'different fixed selection'):
            pilot.materialize_plan(manifest, foreign)
        self.assertEqual(foreign_manifest.read_text(), '{}')
        self.assertEqual(sentinel.read_text(), 'preserved')

    def test_invalid_pdf_does_not_replace_fixed_selection(self):
        plan, manifest = self.freeze()
        original = manifest.read_bytes()
        self.write_selected_pdfs(plan)
        request = plan['requested_pdfs'][1]
        Path(request['pdf_path']).write_bytes(b'not a PDF')
        with self.assertRaisesRegex(ValueError, 'fixed paper IDs preserved'):
            pilot.materialize_plan(manifest)
        self.assertEqual(manifest.read_bytes(), original)
        self.assertFalse((self.output / 'pairs.json').exists())
        receipt = json.loads((self.output / 'materialization_manifest.json').read_text())
        self.assertEqual([r['requested_id'] for r in receipt['failures']], [request['requested_id']])
        self.assertFalse(receipt['resampling_performed'])


class SourceIntegrityTests(PrepFixture):
    def index(self):
        one, two = paper(source_ids=['a', 'b']), paper('2501.00002', source_ids=['a'])
        files = []
        for name, records in (('a', [one, two]), ('b', [one])):
            path = self.root / f'{name}.json'
            path.write_text(json.dumps({'dataset': 'source_data', 'source_id': name,
                                       'record_count': len(records), 'records': records}))
            payload = path.read_bytes()
            files.append({'path': path.name, 'bytes': len(payload), 'record_count': len(records),
                          'source_id': name, 'sha256': hashlib.sha256(payload).hexdigest()})
        index = {'dataset': 'source_data', 'dataset_metadata': {'dataset': 'source_data', 'record_count': 2},
                 'files': files, 'record_order': [two['arxiv_id'], one['arxiv_id']]}
        self.source.write_text(json.dumps(index))
        return index, [two, one]

    def test_index_preserves_hashes_order_and_deduplicated_memberships(self):
        index, expected = self.index()
        records, provenance = pilot.load_source(self.source)
        self.assertEqual(records, expected)
        self.assertEqual(provenance['duplicate_memberships_collapsed'], 1)
        self.assertEqual(provenance['dataset_metadata'], index['dataset_metadata'])
        self.assertEqual([Path(p['path']).name for p in provenance['input_files']],
                         ['source.json', 'a.json', 'b.json'])
        for entry in provenance['input_files']:
            raw = Path(entry['path']).read_bytes()
            self.assertEqual(entry['bytes'], len(raw))
            self.assertEqual(entry['sha256'], hashlib.sha256(raw).hexdigest())
        plan = pilot.build_plan(self.args(**{'all': True}))
        self.assertEqual(plan['records'], expected)
        self.assertEqual(plan['summary']['paper_count'], 2)

    def test_standalone_shard_cli_and_full_source_memberships(self):
        self.index()
        records, provenance = pilot.load_source(self.root / 'a.json')
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]['source_ids'], ['a', 'b'])
        self.assertEqual(len(provenance['input_files']), 1)
        code, stdout, stderr = self.cli('--source', self.root / 'a.json', '--pdf-root', self.pdf_root,
                                       '--all', '--dry-run')
        self.assertEqual((code, stderr), (0, ''))
        self.assertEqual(json.loads(stdout)['summary']['paper_count'], 2)

    def test_index_order_counts_and_hashes_are_checked(self):
        original, _ = self.index()
        mutations = {
            'duplicate_shard': lambda p: p['files'].append(copy.deepcopy(p['files'][0])),
            'duplicate_order': lambda p: p['record_order'].append(p['record_order'][0]),
            'missing_order': lambda p: p['record_order'].pop(),
            'foreign_order': lambda p: p['record_order'].append('2501.99999'),
            'dataset_count': lambda p: p['dataset_metadata'].update(record_count=9),
            'shard_count': lambda p: p['files'][0].update(record_count=9),
            'shard_hash': lambda p: p['files'][0].update(sha256='0' * 64),
            'shard_bytes': lambda p: p['files'][0].update(bytes=0),
            'source_identity': lambda p: p['files'][0].update(source_id='other'),
            'legacy_index': lambda p: p.update(dataset='legacy_source'),
            'legacy_metadata': lambda p: p['dataset_metadata'].update(dataset='legacy_source'),
        }
        for name, mutate in mutations.items():
            index = copy.deepcopy(original)
            mutate(index)
            self.source.write_text(json.dumps(index))
            with self.subTest(name=name), self.assertRaises(ValueError):
                pilot.load_source(self.source)

    def test_shard_release_and_declared_count_checked_after_checksum(self):
        original, _ = self.index()
        path = self.root / 'a.json'
        shard = json.loads(path.read_text())
        for changes, message in (({'dataset': 'legacy_source'}, 'dataset source_data'),
                                 ({'record_count': 9}, 'record count'),
                                 ({'source_id': 'wrong'}, 'source identity')):
            changed = {**shard, **changes}
            payload = json.dumps(changed).encode()
            path.write_bytes(payload)
            index = copy.deepcopy(original)
            index['files'][0].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
            self.source.write_text(json.dumps(index))
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                pilot.load_source(self.source)

    def test_shard_paths_cannot_escape_or_alias(self):
        original, _ = self.index()
        outside = self.root.parent / self.root.name / 'a.json'
        for value in ('../elsewhere.json', str(outside), './a.json'):
            index = copy.deepcopy(original)
            if value == './a.json':
                index['files'][1]['path'] = value
            else:
                index['files'][0]['path'] = value
            self.source.write_text(json.dumps(index))
            with self.subTest(path=value), self.assertRaisesRegex(ValueError, 'escapes|Duplicate shard'):
                pilot.load_source(self.source)
        link = self.root / 'escape.json'
        link.symlink_to(self.root.parent / 'outside.json')
        original['files'][0]['path'] = link.name
        self.source.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            pilot.load_source(self.source)

    def test_invalid_record_id_or_duplicate_membership_rejected_before_writes(self):
        for changes in ({'arxiv_id': '../escape'}, {'source_ids': []},
                        {'source_ids': ['a', 'a']}, {'source_ids': ['']}, {'source_ids': [None]}):
            item = {**self.records[0], **changes}
            write_source(self.source, [item])
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                pilot.prepare(self.args(units=1))
            self.assertFalse(self.output.exists())

    def test_empty_inventory_is_not_a_successful_all_plan(self):
        write_source(self.source, [])
        with self.assertRaisesRegex(ValueError, 'nonempty records'):
            pilot.prepare(self.args(**{'all': True}))
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

"""Regression checks for the bounded v1.3 preparation adapter."""
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
from contextlib import redirect_stdout
from unittest import mock

from pypdf import PdfWriter

if __package__:
    from . import prepare_v13_pilot as pilot
    from .prepare_input_data import digest
else:
    import prepare_v13_pilot as pilot
    from prepare_input_data import digest


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
    path.write_text(json.dumps({'release': 'v1.3', 'record_count': len(records),
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
                payload = json.dumps({'records': [original]}).encode()
                (root / f'{name}.json').write_bytes(payload)
                files.append({'path': f'{name}.json', 'bytes': len(payload), 'record_count': 1,
                              'sha256': hashlib.sha256(payload).hexdigest()})
            index = {'dataset_metadata': {'record_count': 1}, 'record_order': [original['arxiv_id']],
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
    def test_digest_without_python311_file_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'payload.bin'
            payload = b'preserve streaming digest behavior\x00' * 70000
            path.write_bytes(payload)
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(digest(path), expected)
            with mock.patch.object(hashlib, 'file_digest', create=True):
                del hashlib.file_digest
                self.assertEqual(digest(path), expected)

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


if __name__ == '__main__':
    unittest.main()

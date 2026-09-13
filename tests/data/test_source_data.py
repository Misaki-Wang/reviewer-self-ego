"""Read-only source_data packaging and historical-record equivalence; no runtime imports."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class SourceDataTests(unittest.TestCase):
    def test_source_data_records_match_historical_build(self):
        """Verify source bytes, exact reconstruction, counts, gaps and nulls."""
        base = ROOT / 'data/index'
        manifest = json.loads((base / 'index.json').read_bytes())
        self.assertEqual(manifest['schema_version'], 'source-data-index-1')
        self.assertTrue(manifest['dataset'] == 'source_data' and len(manifest['files']) == 10)
        by_id, appearances, source_counts = ({}, {}, {})
        for entry in manifest['files']:
            self.assertEqual(Path(entry['path']).name, entry['path'])
            raw = (base / entry['path']).read_bytes()
            self.assertTrue(len(raw) == entry['bytes'] < 100 * 1024 ** 2)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
            shard = json.loads(raw)
            sid = entry['source_id']
            self.assertTrue(sid not in source_counts)
            self.assertEqual(shard['schema_version'], 'source-data-source-shard-1')
            self.assertTrue(shard['dataset'] == 'source_data' and shard['source_id'] == sid)
            self.assertTrue(len(shard['records']) == shard['record_count'] == entry['record_count'])
            self.assertEqual(len({p['arxiv_id'] for p in shard['records']}), shard['record_count'])
            source_counts[sid] = shard['record_count']
            for paper in shard['records']:
                aid = paper['arxiv_id']
                self.assertTrue(sid in paper['source_ids'])
                self.assertTrue(aid not in by_id or by_id[aid] == paper)
                by_id[aid] = paper
                appearances.setdefault(aid, set()).add(sid)
        self.assertEqual(sum(source_counts.values()), 3738)
        order = manifest['record_order']
        self.assertTrue(len(order) == len(set(order)) == len(by_id) == 3724)
        self.assertEqual(set(order), set(by_id))
        self.assertTrue(all(appearances[aid] == set(p['source_ids']) for aid, p in by_id.items()))
        build_raw = (base / 'provenance/build_metadata.json').read_bytes()
        build = json.loads(build_raw)
        self.assertEqual(manifest['dataset_metadata']['build_provenance'], {
            'path': 'provenance/build_metadata.json', 'bytes': len(build_raw),
            'sha256': hashlib.sha256(build_raw).hexdigest()})
        self.assertEqual(build['original_index']['record_order'], order)
        self.assertNotIn('release', manifest)
        self.assertNotIn('release', manifest['dataset_metadata'])
        self.assertNotIn('protocol_snapshot', manifest['dataset_metadata'])
        self.assertEqual(manifest['dataset_metadata']['dataset'], 'source_data')
        self.assertEqual(manifest['dataset_metadata']['schema_version'], 'source-data-source-1')
        # Reconstruct the historical combined release only to prove record equivalence.
        d = dict(build['original_index']['dataset_metadata'])
        self.assertTrue('records' not in d)
        d['records'] = [by_id[aid] for aid in order]
        self.assertEqual(source_counts, d['statistics']['candidate_source_memberships'])
        original = build['original_index']['source_json']
        self.assertEqual(original, {
            'filename': 'v1.3.json', 'bytes': 130489299,
            'sha256': '9a908aede8b2b30b29db3668baa0db69095c8550aa8adb78ba48282770751cba',
        })
        raw = (json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        self.assertEqual(len(raw), original['bytes'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), original['sha256'])
        current = {**manifest['dataset_metadata'], 'records': [by_id[aid] for aid in order]}
        active_raw = (json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        self.assertEqual(manifest['source_json'], {'filename': 'source_data.json',
                         'bytes': len(active_raw), 'sha256': hashlib.sha256(active_raw).hexdigest()})
        self.assertEqual(d['schema_version'], 'v1.3-raw-derived-preliminary-release-1')
        self.assertEqual(d['stage'], 'preliminary_only')
        self.assertTrue(d['S2_assessed'] is d['content_screening_performed'] is False)
        records = d['records']
        self.assertTrue(len(records) == d['record_count'] == 3724)
        self.assertEqual(len({r['arxiv_id'] for r in records}), 3724)
        funnel = d['raw_filter_funnel']
        for key, value in {
            'source_entry_count': 34127, 'confirmed_source_entries': 25646,
            'unique_confirmed_papers': 25613, 'unresolved_source_entries': 8481,
            'original_version_count': 51410, 'unknown_version_dates': 0,
            'conflicting_version_dates': 0,
        }.items():
            self.assertEqual(funnel[key], value)
        pages, licenses = (Counter(), Counter())
        versions_total = memberships = 0
        for paper in records:
            self.assertIs(paper['S2_assessed'], False)
            self.assertEqual(paper['content_screening_status'], 'not_performed')
            versions = paper['versions']
            self.assertEqual([v['version'] for v in versions], list(range(1, paper['frozen_N'] + 1)))
            versions_total += len(versions)
            memberships += len(paper['source_entries'])
            self.assertTrue(all(s['review_scores'] is None for s in paper['source_entries']))
            dates = {}
            for v in versions:
                self.assertTrue(v['date_status'] == 'known' and v['date_unresolved_reason'] is None)
                t = datetime.fromisoformat(v['submitted_at'].replace('Z', '+00:00'))
                self.assertEqual(t.utcoffset(), timedelta(0))
                dates[v['version']] = t
                p = v['page_count']
                pages[p['status']] += 1
                if p['status'] == 'verified':
                    self.assertTrue(type(p['value']) is int and p['value'] > 0)
                    self.assertEqual(p['source_pdf_sha256'], v['pdf']['sha256'])
                    self.assertTrue(p['source_pdf_sha256'] and p['unresolved_reason'] is None)
                else:
                    self.assertEqual(p['status'], 'unresolved')
                    self.assertTrue(p['value'] is None and p['unresolved_reason'])
                license_info = v['license_metadata']
                licenses[license_info['status']] += 1
                if license_info['status'] == 'unknown_historical_version':
                    self.assertIs(license_info['license'], None)
            witness = paper['temporal_eligibility']
            chosen, edges = (witness['witness_versions'], witness['witness_edges'])
            self.assertEqual(witness['status'], 'eligible')
            self.assertTrue(len(chosen) >= 3 and chosen == sorted(set(chosen)))
            self.assertEqual(len(edges), len(chosen) - 1)
            for (a, b), edge in zip(zip(chosen, chosen[1:]), edges):
                gap = (dates[b] - dates[a]).total_seconds()
                self.assertTrue(gap > 2592000 and gap == edge['gap_seconds'])
                self.assertEqual((a, b), (edge['from_version'], edge['to_version']))
        self.assertEqual((versions_total, memberships), (14086, 3738))
        self.assertEqual(pages, {'verified': 2679, 'unresolved': 11407})
        self.assertEqual(licenses, {
            'reported_in_latest_record_context': 3736,
            'unknown_historical_version': 10350,
        })
        self.assertIs(d['statistics']['page_count_filter_applied'], False)
        self.assertIs(d['statistics']['license_filter_applied'], False)



    def test_observed_metadata_files_remain_exact(self):
        base = ROOT / 'data/index'
        build = json.loads((base / 'provenance/build_metadata.json').read_bytes())
        self.assertEqual(set(build['metadata_files']), {'metadata/provenance.json',
                         'metadata/statistics.json', 'metadata/unavailable_versions.json',
                         'metadata/version_metadata.jsonl'})
        for relative, expected in build['metadata_files'].items():
            with self.subTest(path=relative):
                raw = (base / relative).read_bytes()
                self.assertEqual({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, expected)


if __name__ == '__main__':
    unittest.main()

"""Read-only validation of the additive ICLR 2025 three-version catalog."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
GAP_SECONDS = 2592000


def digest(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def dates(record):
    return [datetime.fromisoformat(v['submitted_at'].replace('Z', '+00:00'))
            for v in record['versions']]


def maximum_sequence_length(record):
    # Independent dynamic program, separate from production's greedy selection.
    timestamps = dates(record)
    lengths = []
    for j, current in enumerate(timestamps):
        lengths.append(1 + max((lengths[i] for i in range(j)
                                if (current - timestamps[i]).total_seconds() > GAP_SECONDS),
                               default=0))
    return max(lengths, default=0)


class Iclr2025NonfullTests(unittest.TestCase):
    def test_new_source_is_exact_three_version_subset(self):
        original = ROOT / 'data_full/index/iclr2025.additions.json'
        raw = original.read_bytes()
        self.assertEqual(digest(raw), {'bytes': 37600461,
            'sha256': 'dba79164388cbf6929b2f9a476fdf4148f70fa1dd7c2220e33555eb9c2449c23'})
        full = json.loads(raw)
        self.assertEqual(len(full['records']), 1900)
        self.assertEqual(sum(len(p['versions']) for p in full['records']), 5378)
        expected = [p for p in full['records'] if maximum_sequence_length(p) >= 3]
        shard = json.loads((ROOT / 'data/index/iclr2025.json').read_bytes())
        self.assertEqual(shard['source_id'], 'iclr2025')
        self.assertEqual(shard['record_count'], 610)
        self.assertEqual(shard['temporal_rule']['minimum_selected_versions'], 3)
        self.assertEqual(shard['temporal_rule']['operator'], '>')
        self.assertEqual(shard['temporal_rule']['threshold_seconds'], GAP_SECONDS)
        self.assertEqual(shard['records'], expected)
        self.assertEqual(sum(len(p['versions']) for p in shard['records']), 2371)
        index = json.loads((ROOT / 'data/index/index.json').read_bytes())
        self.assertEqual(index['dataset_metadata']['temporal_rule'], shard['temporal_rule'])
        declared = shard['build_provenance']
        self.assertEqual(index['dataset_metadata']['source_updates']['iclr2025'], declared)
        proof_raw = (ROOT / 'data/index' / declared['path']).read_bytes()
        self.assertEqual(digest(proof_raw), {key: declared[key] for key in ('bytes', 'sha256')})
        report = json.loads(proof_raw)
        self.assertEqual(report['selection_rule'], shard['temporal_rule'])
        proofs = {item['arxiv_id']: item for item in report['proofs']}
        self.assertEqual(len(report['proofs']), 610)
        self.assertEqual(set(proofs), {p['arxiv_id'] for p in expected})
        for before, after in zip(expected, shard['records']):
            with self.subTest(arxiv_id=after['arxiv_id']):
                self.assertEqual(after, before)
                self.assertEqual([v['version'] for v in after['versions']],
                                 list(range(1, after['frozen_N'] + 1)))
                # Historical paper-level witnesses stay untouched; the new filter
                # certificate proves membership under the three-version rule.
                certificate = proofs[after['arxiv_id']]
                chosen = certificate['selected_versions']
                self.assertEqual(certificate['historical_record_witness_preserved'],
                                 before['temporal_eligibility']['witness_versions'])
                self.assertEqual(len(chosen), maximum_sequence_length(after))
                self.assertGreaterEqual(len(chosen), 3)
                self.assertEqual(chosen, sorted(set(chosen)))
                self.assertEqual(certificate['witness_versions'], chosen[:3])
                self.assertEqual(certificate['witness_edges'], certificate['selected_edges'][:2])
                self.assertEqual(len(certificate['selected_edges']), len(chosen) - 1)
                timestamps = dict(zip((v['version'] for v in after['versions']), dates(after)))
                for (a, b), edge in zip(zip(chosen, chosen[1:]), certificate['selected_edges']):
                    gap = (timestamps[b] - timestamps[a]).total_seconds()
                    self.assertGreater(gap, GAP_SECONDS)
                    self.assertEqual(edge['gap_seconds'], gap)
                    self.assertEqual((edge['from_version'], edge['to_version']), (a, b))

    def test_active_inventory_and_combined_digest(self):
        base = ROOT / 'data/index'
        manifest = json.loads((base / 'index.json').read_bytes())
        self.assertEqual(len(manifest['files']), 11)
        records, appearances = {}, {}
        memberships = 0
        for entry in manifest['files']:
            raw = (base / entry['path']).read_bytes()
            self.assertEqual(digest(raw), {key: entry[key] for key in ('bytes', 'sha256')})
            shard = json.loads(raw)
            self.assertEqual(shard['record_count'], entry['record_count'])
            self.assertEqual(len(shard['records']), entry['record_count'])
            memberships += len(shard['records'])
            for paper in shard['records']:
                aid = paper['arxiv_id']
                if aid in records:
                    self.assertEqual(records[aid], paper)
                records[aid] = paper
                appearances.setdefault(aid, set()).add(entry['source_id'])
        self.assertEqual(memberships, 4348)
        self.assertEqual(len(records), 4334)
        self.assertEqual(sum(len(p['versions']) for p in records.values()), 16457)
        order = manifest['record_order']
        self.assertEqual(len(order), len(set(order)))
        self.assertEqual(set(order), set(records))
        self.assertTrue(all(appearances[aid] == set(p['source_ids']) for aid, p in records.items()))
        self.assertEqual(manifest['dataset_metadata']['record_count'], 4334)
        current = {**manifest['dataset_metadata'], 'records': [records[aid] for aid in order]}
        combined = (json.dumps(current, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')) + '\n').encode('utf-8')
        self.assertEqual(manifest['source_json'], {'filename': 'source_data.json', **digest(combined)})
        report = manifest['dataset_metadata']['source_updates']['iclr2025']
        self.assertEqual(digest((base / report['path']).read_bytes()),
                         {key: report[key] for key in ('bytes', 'sha256')})

    def test_ledger_addition_is_explicitly_unobserved_and_complete(self):
        base = ROOT / 'data/index'
        provenance = json.loads((base / 'metadata/provenance.json').read_bytes())
        declared = provenance['catalog_extensions'][0]
        report_raw = (base / declared['path']).read_bytes()
        self.assertEqual(digest(report_raw), {key: declared[key] for key in ('bytes', 'sha256')})
        extension = json.loads(report_raw)
        old = extension['original_ledger_before']
        raw = (base / 'metadata/version_metadata.jsonl').read_bytes()
        self.assertEqual(digest(raw[:old['bytes']]), old)
        appended = [json.loads(line) for line in raw[old['bytes']:].splitlines()]
        self.assertEqual(len(appended), 2371)
        shard = json.loads((base / 'iclr2025.json').read_bytes())
        expected = {f"{p['arxiv_id']}v{v['version']}": (p['arxiv_id'], v)
                    for p in shard['records'] for v in p['versions']}
        self.assertEqual({row['requested_id'] for row in appended}, set(expected))
        previous = {json.loads(line)['requested_id'] for line in raw[:old['bytes']].splitlines()}
        self.assertFalse(previous.intersection(expected))
        for row in appended:
            aid, version = expected[row['requested_id']]
            self.assertEqual((row['arxiv_id'], row['version'], row['submitted_at']),
                             (aid, version['version'], version['submitted_at']))
            self.assertEqual(row['metadata_origin'], 'catalog_extension_without_pdf_observation')
            self.assertEqual(row['pdf']['availability_status'], 'unresolved')
            self.assertIsNone(row['pdf'].get('sha256'))
            self.assertIsNone(row['pdf'].get('bytes'))
            self.assertEqual(row['page_count']['status'], 'unresolved')
            self.assertIsNone(row['page_count']['value'])
            self.assertTrue(row['page_count']['unresolved_reason'])
            self.assertIsNone(row['official_no_pdf_evidence'])
        provenance = json.loads((base / 'metadata/provenance.json').read_bytes())
        self.assertEqual(provenance['files']['version_metadata.jsonl'], digest(raw))
        self.assertEqual(provenance['version_record_count'], 16457)
        self.assertEqual(provenance['unobserved_version_count'], 2371)
        historical = json.loads((base / 'provenance/build_metadata.json').read_bytes())['metadata_files']
        for name in ('statistics.json', 'unavailable_versions.json'):
            self.assertEqual(digest((base / 'metadata' / name).read_bytes()), historical['metadata/' + name])

    def test_full_view_preserves_all_iclr2025_records_and_order(self):
        provenance = json.loads((ROOT / 'data/index/metadata/provenance.json').read_bytes())
        declared = provenance['catalog_extensions'][0]
        extension = json.loads((ROOT / 'data/index' / declared['path']).read_bytes())
        raw = (ROOT / 'data_full/index/index.json').read_bytes()
        self.assertEqual(digest(raw), extension['inputs']['data_full/index/index.json'])
        index = json.loads(raw)
        by_id = {}
        for entry in [*index['bases'], *index['files']]:
            if entry.get('source_id') not in (None, 'iclr2025'):
                continue
            if entry in index['bases']:
                path = ROOT / 'data/index' / (entry['source_id'] + '.json')
            else:
                path = ROOT / 'data_full/index' / entry['path']
            payload = path.read_bytes()
            self.assertEqual(digest(payload), {key: entry[key] for key in ('bytes', 'sha256')})
            for paper in json.loads(payload)['records']:
                if 'iclr2025' not in paper['source_ids']:
                    continue
                self.assertNotIn(paper['arxiv_id'], by_id)
                by_id[paper['arxiv_id']] = paper
        expected = json.loads((ROOT / 'data_full/index/iclr2025.additions.json').read_bytes())['records']
        resolved = [by_id[aid] for aid in index['record_order'] if aid in by_id]
        self.assertEqual(len(resolved), 1900)
        self.assertEqual(resolved, expected)


if __name__ == '__main__':
    unittest.main()

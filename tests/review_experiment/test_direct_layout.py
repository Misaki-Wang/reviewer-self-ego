"""Direct prepared-version inputs: no model service."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from review_experiment import runner


class DirectLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.input = self.root / 'input'
        self.input.mkdir()
        self.aid = '2406.03862'

    def pdf(self, relative, data=b'%PDF-1.4\nfixture'):
        path = self.input / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_single_direct_version_accepts_collection_and_paper_roots(self):
        path = self.pdf(self.aid + '/v7.pdf')
        for root in (self.input, path.parent):
            papers, pairs = runner.discover(root)
            self.assertEqual(pairs, 0)
            self.assertEqual(papers, {self.aid: [runner.Version(self.aid, 7, (path,))]})
            self.assertEqual(papers[self.aid][0].read(),
                             (path.read_bytes(), runner.sha(path.read_bytes())))

    def test_direct_numeric_order_and_existing_paper_version_limit_filters(self):
        for n in (10, 2, 9):
            self.pdf(f'{self.aid}/v{n}.pdf')
        other = '2501.12345'
        self.pdf(other + '/v9.pdf')
        papers, pairs = runner.discover(self.input)
        self.assertEqual([v.number for v in papers[self.aid]], [2, 9, 10])
        self.assertEqual(pairs, 0)
        papers, _ = runner.discover(self.input, papers=[other], versions=[9])
        self.assertEqual(list(papers), [other])
        self.assertEqual([v.number for v in papers[other]], [9])
        papers, _ = runner.discover(self.input, versions=[9], limit=1)
        self.assertEqual(list(papers), [self.aid])
        self.assertEqual([v.number for v in papers[self.aid]], [9])
        with self.assertRaisesRegex(ValueError, 'requested versions missing'):
            runner.discover(self.input, papers=[other], versions=[2])
        with self.assertRaisesRegex(ValueError, 'Unknown paper IDs'):
            runner.discover(self.input, papers=['2502.12345'])

    def test_missing_empty_invalid_direct_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            runner.discover(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'No PDF versions'):
            runner.discover(self.input)
        for name in ('v0.pdf', 'v01.pdf', 'v-1.pdf', 'paper.pdf'):
            path = self.pdf(self.aid + '/' + name)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Invalid version PDF filename'):
                runner.discover(self.input)
            path.unlink()
        path = self.pdf(self.aid + '/v7.pdf', b'')
        with self.assertRaisesRegex(ValueError, 'Missing or empty PDF'):
            runner.discover(self.input)
        path.unlink()
        path.symlink_to(self.root / 'missing.pdf')
        with self.assertRaisesRegex(ValueError, 'Missing or empty PDF'):
            runner.discover(self.input)
        path.unlink()
        path.mkdir()
        with self.assertRaisesRegex(ValueError, 'Missing or empty PDF'):
            runner.discover(self.input)
        path.rmdir()
        self.pdf('invalid-paper/v7.pdf')
        with self.assertRaisesRegex(ValueError, 'Invalid paper ID'):
            runner.discover(self.input)

    def test_pdf_signature_is_checked_at_read_not_discovery(self):
        self.pdf(self.aid + '/v7.pdf', b'not a PDF')
        version = runner.discover(self.input)[0][self.aid][0]
        with self.assertRaisesRegex(ValueError, 'Not a PDF'):
            version.read()

    def test_mixed_pair_direct_copies_are_verified_and_conflicts_rejected(self):
        self.pdf(self.aid + '/v1_v2/v1.pdf', b'%PDF-v1')
        pair = self.pdf(self.aid + '/v1_v2/v2.pdf', b'%PDF-v2')
        direct = self.pdf(self.aid + '/v2.pdf', b'%PDF-v2')
        for root in (self.input, direct.parent):
            papers, pairs = runner.discover(root)
            self.assertEqual(pairs, 1)
            self.assertEqual([v.number for v in papers[self.aid]], [1, 2])
            version = papers[self.aid][1]
            self.assertEqual(set(version.sources), {pair, direct})
            original, reads = Path.read_bytes, []
            def read(path):
                reads.append(path)
                return original(path)
            with patch.object(Path, 'read_bytes', read):
                self.assertEqual(version.read()[0], b'%PDF-v2')
            self.assertEqual(set(reads), {pair, direct})
        direct.write_bytes(b'%PDF-conflicting')
        with self.assertRaisesRegex(ValueError, 'Conflicting copies'):
            runner.discover(self.input)[0][self.aid][1].read()

    def test_direct_copy_cannot_hide_missing_or_invalid_pair(self):
        direct = self.pdf(self.aid + '/v2.pdf')
        self.pdf(self.aid + '/v1_v2/v1.pdf')
        with self.assertRaisesRegex(ValueError, 'Missing or empty PDF'):
            runner.discover(self.input)
        self.pdf(self.aid + '/v1_v2/v2.pdf')
        (direct.parent / 'v2_v1').mkdir()
        with self.assertRaisesRegex(ValueError, 'Invalid version pair'):
            runner.discover(self.input)

    def test_latest_only_fixture_resolves_metadata_and_dry_run_plans_145(self):
        aids = [f'2401.{n:05d}' for n in range(1, 6)]
        for n, aid in enumerate(aids, 7):
            self.pdf(f'{aid}/v{n}.pdf')
        metadata = {'source': 'iclr2026', 'source_by_paper': {a: 'iclr2026' for a in aids}}
        (self.input / 'source_metadata.json').write_text(json.dumps(metadata))
        papers, pairs = runner.discover(self.input)
        self.assertEqual(len(papers), 5)
        self.assertEqual(sum(map(len, papers.values())), 5)
        self.assertEqual(pairs, 0)
        for root, selected in ((self.input, aids), (self.input / aids[0], aids[:1])):
            resolved, provenance = runner.resolve_sources(root, selected, {'iclr2026'})
            self.assertEqual(resolved, {aid: 'iclr2026' for aid in selected})
            self.assertEqual(provenance['mode'], 'nearby_metadata')
        output = self.root / 'output'
        with patch.object(runner.Version, 'read', side_effect=AssertionError('PDF read')), \
             patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('model call')), \
             redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(runner.main([str(self.input), '--output', str(output), '--dry-run']), 0)
        inventory = json.loads(stream.getvalue())
        self.assertEqual(inventory['planned_reviews'], 145)
        self.assertEqual(len(inventory['conditions']), 29)
        self.assertFalse(output.exists())

    def test_paper_filter_excludes_unselected_direct_files(self):
        self.pdf(self.aid + '/v7.pdf')
        self.pdf('2501.12345/v0.pdf')
        papers, pairs = runner.discover(self.input, papers=[self.aid])
        self.assertEqual(list(papers), [self.aid])
        self.assertEqual(pairs, 0)
        with self.assertRaisesRegex(ValueError, 'Invalid version PDF filename'):
            runner.discover(self.input)


if __name__ == '__main__':
    unittest.main()

"""Offline download/store/pair integration; no arXiv or model requests."""
import argparse
import copy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError
from pypdf import PdfWriter

from utility import download_papers as downloader
from utility import prepare_source_data as pairing
from utility import build_pairs
from tests.utility.test_prepare_source_data import paper, write_source, write_pdf


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source.json'
        self.store = self.root / 'downloads_papers'
        self.item = paper(source_ids=['conf_a', 'conf_b'])
        write_source(self.source, [self.item])
        self.urls = []

    def fetch(self, url, target):
        self.urls.append(url)
        write_pdf(target)

    def download(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return downloader.download(self.source, self.store, fetch=self.fetch, **kwargs)

    def test_downloads_all_versions_once_and_keeps_metadata_for_each_conference(self):
        result = self.download()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.urls), 4)  # v2 is downloaded although selection skips it.
        self.assertEqual(result['summary']['file_count'], 8)
        for conf in ('conf_a', 'conf_b'):
            folder = self.store / conf / self.item['arxiv_id']
            self.assertEqual(sorted(p.name for p in folder.glob('*.pdf')), ['v1.pdf', 'v2.pdf', 'v3.pdf', 'v4.pdf'])
            self.assertEqual(json.loads((folder / 'metadata.json').read_text()), self.item)
        self.assertFalse(list(self.store.rglob('pair.json')))
        self.assertFalse(list(self.store.rglob('pairs.json')))
        self.assertEqual(self.urls, [f'https://arxiv.org/pdf/{self.item["arxiv_id"]}v{v}' for v in range(1, 5)])
        self.urls.clear()
        self.assertEqual(self.download()['status'], 'complete')
        self.assertEqual(self.urls, [])

    def test_no_temporal_filter_or_timestamp_requirement_for_downloads(self):
        self.item['temporal_eligibility']['status'] = 'ineligible'
        self.item['versions'][1]['submitted_at'] = None
        write_source(self.source, [self.item])
        self.assertEqual(self.download()['summary']['version_count'], 4)
        self.assertEqual(len(self.urls), 4)

    def test_dry_run_and_filters(self):
        result = self.download(conferences=['conf_b'], arxiv_ids=[self.item['arxiv_id']], dry_run=True)
        self.assertEqual(result['summary']['file_count'], 4)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.urls, [])
        for kwargs in ({'conferences': ['missing']}, {'arxiv_ids': ['2501.99999']}):
            with self.assertRaises(ValueError):
                self.download(**kwargs)

    def test_corrupt_existing_file_is_replaced_only_after_validation(self):
        self.download(conferences=['conf_a'])
        target = self.store / 'conf_a' / self.item['arxiv_id'] / 'v2.pdf'
        target.write_bytes(b'corrupt old bytes')
        self.urls.clear()
        self.download(conferences=['conf_a'])
        self.assertEqual(self.urls, [f'https://arxiv.org/pdf/{self.item["arxiv_id"]}v2'])
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))

    def test_unavailable_and_invalid_versions_are_reported_without_partial_pdf(self):
        def fail(url, target):
            if url.endswith('v2'):
                raise HTTPError(url, 404, 'missing', {}, None)
            if url.endswith('v3'):
                target.write_bytes(b'not a PDF')
            else:
                write_pdf(target)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=fail, retries=0)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['summary']['failed_file_count'], 2)
        folder = self.store / 'conf_a' / self.item['arxiv_id']
        self.assertFalse((folder / 'v2.pdf').exists())
        self.assertFalse((folder / 'v3.pdf').exists())
        self.assertEqual(len(list(folder.glob('*.pdf'))), 2)
        self.assertFalse((self.store / '.download.lock').exists())
        self.assertEqual(json.loads((self.store / 'download_manifest.json').read_text())['status'], 'incomplete')

    def test_retries_transient_failure(self):
        calls = []
        def fetch(url, path):
            calls.append(url)
            if len(calls) == 1:
                raise HTTPError(url, 503, 'busy', {}, None)
            write_pdf(path)
        with mock.patch.object(downloader.time, 'sleep'), redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=fetch)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(calls), 5)

    def test_reuses_legacy_store(self):
        old = self.root / 'old'
        for version in self.item['versions']:
            write_pdf(pairing.pdf_path(old, self.item['arxiv_id'], version['version']))
        self.assertEqual(self.download(reuse_root=old)['status'], 'complete')
        self.assertEqual(self.urls, [])

    def test_rejects_hash_mismatch(self):
        self.item['versions'][1]['pdf']['expected_sha256'] = '0' * 64
        write_source(self.source, [self.item])
        result = self.download(conferences=['conf_a'], retries=0)
        self.assertEqual(result['summary']['failed_file_count'], 1)
        self.assertFalse((self.store / 'conf_a' / self.item['arxiv_id'] / 'v2.pdf').exists())

    def test_rejects_incomplete_history_and_unsafe_paths(self):
        original = copy.deepcopy(self.item)
        self.item['versions'].pop(1)
        write_source(self.source, [self.item])
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            self.download()
        self.item = original
        self.item['source_ids'] = ['../escape']
        write_source(self.source, [self.item])
        with self.assertRaisesRegex(ValueError, 'conference'):
            self.download()
        self.assertFalse(self.store.exists())

    def test_rejects_symlink_and_conflicting_metadata(self):
        self.store.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        (self.store / 'conf_a').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.download()
        self.assertEqual(list(outside.iterdir()), [])
        (self.store / 'conf_a').unlink()
        self.download()
        self.item['title'] = 'Changed metadata'
        write_source(self.source, [self.item])
        with self.assertRaisesRegex(ValueError, 'metadata differs'):
            self.download()

    def test_downloaded_store_builds_existing_pairs_offline(self):
        self.download(conferences=['conf_b'])
        # Another catalog paper has no cached PDFs; an explicit ID must exclude it.
        write_source(self.source, [self.item, paper(aid='2501.00002', source_ids=['conf_b'])])
        output = self.root / 'prepared'
        argv = ['build_pairs', '--source', str(self.source), '--pdf-root', str(self.store),
                '--conf', 'conf_b', '--arxiv-id', self.item['arxiv_id'], '--output', str(output)]
        with mock.patch('sys.argv', argv), redirect_stdout(io.StringIO()), \
                mock.patch.object(downloader, 'urlopen', side_effect=AssertionError('Offline only')):
            build_pairs.main()
        pairs = json.loads((output / 'pairs.json').read_text())['pairs']
        self.assertEqual([(p['from_version'], p['to_version']) for p in pairs], [(1, 3), (3, 4)])
        self.assertTrue(all(p['assigned_source_id'] == 'conf_b' for p in pairs))
        self.assertEqual(json.loads((output / self.item['arxiv_id'] / 'metadata.json').read_text()), self.item)
        for pair in pairs:
            for side in ('before', 'after'):
                self.assertTrue((output / pair[side]['pdf_path']).is_file())

    def test_default_pairs_use_clean_pdf_bytes_without_metadata_or_raw_fallback(self):
        self.download(conferences=['conf_b'])
        clean = self.root / 'anonymous-store'
        for version in (1, 3, 4):
            path = clean / 'conf_b' / self.item['arxiv_id'] / f'v{version}.pdf'
            path.parent.mkdir(parents=True, exist_ok=True)
            writer = PdfWriter()
            writer.add_blank_page(width=600, height=800)
            writer.write(path)
        adapter = self.root / 'utility/prepare_source_data.py'
        adapter.parent.mkdir()
        adapter.write_bytes(Path(pairing.__file__).read_bytes())
        (adapter.parent / 'project.json').write_text(json.dumps({'data': {
            'index': self.source.name, 'clean_root': clean.name, 'downloads_root': self.store.name}}))
        output = self.root / 'clean-pairs'
        argv = ['build_pairs', '--conf', 'conf_b', '--arxiv-id', self.item['arxiv_id'],
                '--output', str(output)]
        with mock.patch.object(pairing, '__file__', str(adapter)), mock.patch('sys.argv', argv), \
                redirect_stdout(io.StringIO()):
            build_pairs.main()
        pairs = json.loads((output / 'pairs.json').read_text())['pairs']
        self.assertEqual([(p['from_version'], p['to_version']) for p in pairs], [(1, 3), (3, 4)])
        for pair in pairs:
            for side, number in [('before', pair['from_version']), ('after', pair['to_version'])]:
                actual = (output / pair[side]['pdf_path']).read_bytes()
                self.assertEqual(actual, (clean / 'conf_b' / self.item['arxiv_id'] / f'v{number}.pdf').read_bytes())
                self.assertNotEqual(actual, (self.store / 'conf_b' / self.item['arxiv_id'] / f'v{number}.pdf').read_bytes())
        (clean / 'conf_b' / self.item['arxiv_id'] / 'v3.pdf').unlink()
        with mock.patch.object(pairing, '__file__', str(adapter)), \
                mock.patch('sys.argv', [*argv[:-1], str(self.root / 'missing-clean')]), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_pairs.main()

    def test_transport_plan_and_missing_pdf_preserve_fixed_selection(self):
        self.download(conferences=['conf_a'])
        args = argparse.Namespace(source=self.source, pdf_root=self.store, pdf_layout='conference',
                                  conf='conf_a', units=1, seed=1, dry_run=False, plan_only=True,
                                  output=self.root / 'planned')
        with redirect_stdout(io.StringIO()):
            plan = pairing.prepare(args)
        self.assertEqual(plan['pdf_layout'], 'conference')
        moved = self.root / 'moved'
        self.store.rename(moved)
        manifest = args.output / 'selection_manifest.json'
        pairing.materialize_plan(manifest, pdf_root=moved)
        (moved / 'conf_a' / self.item['arxiv_id'] / 'v3.pdf').unlink()
        with self.assertRaisesRegex(ValueError, 'fixed paper IDs preserved'):
            pairing.materialize_plan(manifest, self.root / 'missing', moved)
        failure = json.loads((self.root / 'missing' / 'materialization_manifest.json').read_text())
        self.assertTrue(failure['sample_unchanged'])
        self.assertEqual(failure['failures'][0]['version'], 3)


if __name__ == '__main__':
    unittest.main()

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
        self.assertEqual(self.urls, [f'https://export.arxiv.org/pdf/{self.item["arxiv_id"]}v{v}' for v in range(1, 5)])
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
        self.assertEqual(self.urls, [f'https://export.arxiv.org/pdf/{self.item["arxiv_id"]}v2'])
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

    def test_legacy_references_keep_originals_readonly_and_download_only_additions(self):
        base = self.root / 'data/pdf'
        for version in self.item['versions']:
            write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], version['version']))
        original = {path: path.read_bytes() for path in base.rglob('*.pdf')}
        extra = paper(aid='2501.00002', source_ids=['conf_a'])
        write_source(self.source, [self.item, extra])
        result = self.download(pdf_layout='legacy', reference_roots=[base])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['summary']['file_count'], 8)
        self.assertEqual(len(self.urls), 4)
        self.assertTrue(all('2501.00002v' in url for url in self.urls))
        self.assertEqual(sum(entry['status'] == 'referenced' for entry in result['files']), 4)
        self.assertEqual(original, {path: path.read_bytes() for path in base.rglob('*.pdf')})
        self.assertFalse((self.store / self.item['arxiv_id']).exists())
        self.assertEqual(len(list(self.store.rglob('*.pdf'))), 4)
        self.assertFalse(list(self.store.rglob('metadata.json')))

    def test_reference_mismatch_fails_without_download_or_repair(self):
        base = self.root / 'data/pdf'
        for version in self.item['versions']:
            write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], version['version']))
        self.item['versions'][0]['pdf']['expected_sha256'] = '0' * 64
        write_source(self.source, [self.item])
        before = {path: path.read_bytes() for path in base.rglob('*.pdf')}
        result = self.download(pdf_layout='legacy', reference_roots=[base])
        self.assertEqual(result['summary']['failed_file_count'], 1)
        self.assertEqual(self.urls, [])
        self.assertEqual(before, {path: path.read_bytes() for path in base.rglob('*.pdf')})
        self.assertFalse(list(self.store.rglob('*.pdf')))

    def test_reference_store_cannot_be_inside_or_overlap_writable_store(self):
        for base in (self.store, self.store / 'nested', self.root):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, 'disjoint'):
                self.download(pdf_layout='legacy', reference_roots=[base])
        self.assertFalse(self.store.exists())

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

    def test_clean_pair_entrypoint_rejects_original_fallback_roots(self):
        argv = ['build_pairs', '--source', str(self.source), '--pdf-root', str(self.store),
                '--fallback-pdf-root', str(self.root / 'data/pdf'), '--all', '--dry-run']
        stderr = io.StringIO()
        with mock.patch('sys.argv', argv), redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_pairs.main()
        self.assertIn('original fallback roots are unsupported', stderr.getvalue())
        self.assertFalse(self.store.exists())

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

    def test_progress_checkpoint_and_interruption_resume(self):
        calls = []
        def interrupted(url, target):
            calls.append(url)
            progress = json.loads((self.store / 'download_manifest.json').read_text())
            self.assertEqual(progress['status'], 'in_progress')
            if len(calls) == 2:
                raise KeyboardInterrupt()
            write_pdf(target)
        with redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            downloader.download(self.source, self.store, ['conf_a'], fetch=interrupted)
        receipt = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(receipt['status'], 'interrupted')
        self.assertEqual(receipt['summary']['processed_file_count'], 1)
        self.assertEqual(receipt['summary']['pending_file_count'], 3)
        self.assertFalse((self.store / '.download.lock').exists())
        self.assertEqual(self.download(conferences=['conf_a'])['status'], 'complete')
        self.assertEqual(len(self.urls), 3)

    def test_rate_limit_checkpoints_cooldown_and_stops_before_next_version(self):
        calls = []
        def limited(url, target):
            calls.append(url)
            raise HTTPError(url, 429, 'slow down', {'Retry-After': '120'}, None)
        with mock.patch.object(downloader.time, 'sleep') as sleep, redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=limited)
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_args_list, [mock.call(120.0), mock.call(120.0)])
        self.assertEqual(result['summary']['processed_file_count'], 1)
        self.assertEqual(result['summary']['pending_file_count'], 3)
        self.assertEqual(result['files'][0]['http_status'], 429)
        self.assertEqual(result['files'][0]['error_kind'], 'rate_limited')
        self.assertEqual(result['retry_after_seconds'], 120.0)
        self.assertIn('next_retry_at', result)
        saved = (self.store / 'download_manifest.json').read_bytes()
        with redirect_stdout(io.StringIO()):
            retry = downloader.download(self.source, self.store, ['conf_a'], fetch=limited)
        self.assertEqual(retry['status'], 'rate_limited')
        self.assertEqual(len(calls), 3)
        self.assertEqual((self.store / 'download_manifest.json').read_bytes(), saved)

    def test_service_unavailable_uses_global_minimum_cooldown(self):
        def busy(url, target):
            raise HTTPError(url, 503, 'busy', {}, None)
        with mock.patch.object(downloader.time, 'sleep') as sleep, redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=busy, retries=1)
        self.assertEqual(sleep.call_args_list, [mock.call(60.0)])
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(result['files'][0]['error_kind'], 'service_unavailable')
        self.assertEqual(result['retry_after_seconds'], 60.0)

    def test_http_date_retry_after_and_invalid_header(self):
        from datetime import datetime, timezone
        from email.utils import format_datetime
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        future = datetime(2026, 9, 15, 12, 2, tzinfo=timezone.utc)
        self.assertEqual(downloader.retry_delay(
            HTTPError('url', 429, '', {'Retry-After': format_datetime(future)}, None), 0, now=now), 120)
        for value in ('invalid', '-1', 'NaN', '1'):
            self.assertEqual(downloader.retry_delay(
                HTTPError('url', 503, '', {'Retry-After': value}, None), 0, now=now), 60)

    def test_http_unavailable_and_validation_errors_remain_distinct(self):
        def fetch(url, target):
            if url.endswith('v1'):
                raise HTTPError(url, 404, 'missing', {}, None)
            if url.endswith('v2'):
                raise HTTPError(url, 410, 'removed', {}, None)
            if url.endswith('v3'):
                target.write_bytes(b'invalid PDF')
            else:
                write_pdf(target)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=fetch, retries=0)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['summary']['http_unavailable_file_count'], 2)
        self.assertEqual(result['summary']['processed_file_count'], 4)
        self.assertEqual(result['summary']['pending_file_count'], 0)
        self.assertEqual([entry.get('error_kind') for entry in result['files']],
                         ['http_not_found', 'http_gone', 'validation_error', None])

    def test_cooldown_is_checkpointed_before_sleep_and_survives_interruption(self):
        def limited(url, target):
            raise HTTPError(url, 429, 'limited', {}, None)
        def interrupt(delay):
            receipt = json.loads((self.store / 'download_manifest.json').read_text())
            self.assertEqual(receipt['retry_after_seconds'], 60.0)
            self.assertIn('next_retry_at', receipt)
            raise KeyboardInterrupt()
        with mock.patch.object(downloader.time, 'sleep', side_effect=interrupt), \
                redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            downloader.download(self.source, self.store, ['conf_a'], fetch=limited)
        receipt = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(receipt['status'], 'interrupted')
        self.assertEqual(receipt['summary']['pending_file_count'], 4)
        self.assertIn('next_retry_at', receipt)

    def test_periodic_checkpoint_after_twenty_five_files(self):
        items = [paper(aid=f'2501.{number:05d}', source_ids=['conf_a']) for number in range(1, 8)]
        write_source(self.source, items)
        checkpoints = []
        atomic = downloader.atomic_json
        def record(path, value):
            if path.name == 'download_manifest.json':
                checkpoints.append(copy.deepcopy(value))
            atomic(path, value)
        with mock.patch.object(downloader, 'atomic_json', side_effect=record), \
                mock.patch.object(downloader.time, 'monotonic', return_value=1.0):
            result = self.download(pdf_layout='legacy')
        self.assertEqual(result['summary']['processed_file_count'], 28)
        self.assertEqual([(x['status'], x['summary']['processed_file_count']) for x in checkpoints],
                         [('in_progress', 0), ('in_progress', 25), ('complete', 28)])

    def test_malformed_pdf_parser_error_is_validation_error(self):
        def fetch(url, path):
            path.write_bytes(b'%PDF-1.7\n%%EOF')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=fetch, retries=0)
        self.assertEqual({entry['error_kind'] for entry in result['files']}, {'validation_error'})

    def test_cli_rate_limit_and_sigterm_have_nonzero_exit(self):
        import signal
        previous = signal.getsignal(signal.SIGTERM)
        result = {'status': 'rate_limited', 'summary': {'pending_file_count': 4},
                  'next_retry_at': '2026-09-15T12:01:00+00:00'}
        with mock.patch('sys.argv', ['download_papers']), \
                mock.patch.object(downloader, 'download', return_value=result), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as exit:
            downloader.main()
        self.assertEqual(exit.exception.code, 1)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        def terminate(*args, **kwargs):
            signal.raise_signal(signal.SIGTERM)
        with mock.patch('sys.argv', ['download_papers']), \
                mock.patch.object(downloader, 'download', side_effect=terminate), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as exit:
            downloader.main()
        self.assertEqual(exit.exception.code, 130)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_fetcher_uses_canonical_bulk_url_and_unchanged_request_headers(self):
        destination = self.root / 'probe.pdf'
        with mock.patch.object(downloader, 'urlopen', return_value=io.BytesIO(b'PDF bytes')) as opening:
            downloader.Fetcher(interval=0)('https://export.arxiv.org/pdf/2401.04013v1', destination)
        request = opening.call_args.args[0]
        self.assertEqual(request.full_url, 'https://export.arxiv.org/pdf/2401.04013v1')
        self.assertEqual(request.get_header('Accept'), 'application/pdf')
        self.assertEqual(request.get_header('User-agent'), 'reviewer-self-ego/download-papers')
        self.assertEqual(destination.read_bytes(), b'PDF bytes')

    def test_406_stops_globally_after_bounded_retries(self):
        calls = []
        def unacceptable(url, target):
            calls.append(url)
            raise HTTPError(url, 406, 'Not Acceptable', {}, None)
        with mock.patch.object(downloader.time, 'sleep') as sleep, redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'], fetch=unacceptable)
        self.assertEqual(result['status'], 'rate_limited')
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_args_list, [mock.call(60.0), mock.call(60.0)])
        self.assertEqual(result['summary']['processed_file_count'], 1)
        self.assertEqual(result['summary']['pending_file_count'], 3)
        self.assertEqual(result['files'][0]['error_kind'], 'http_not_acceptable')
        self.assertEqual(result['last_http_status'], 406)
        self.assertIn('next_retry_at', result)

    def test_http_error_diagnostics_are_bounded_and_exclude_sensitive_headers(self):
        reads = []
        class Body(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)
        url = f'https://export.arxiv.org/pdf/{self.item["arxiv_id"]}v1'
        headers = {'Content-Type': 'text/html', 'Retry-After': '61', 'Server': 'test-edge',
                   'Set-Cookie': 'private-cookie', 'Authorization': 'private-auth'}
        error = HTTPError(url, 406, 'Not Acceptable', headers, Body(b'X' * 9000))
        with mock.patch.object(downloader, 'urlopen', side_effect=error), redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, ['conf_a'],
                                         fetch=downloader.Fetcher(interval=0), retries=0)
        entry = result['files'][0]
        self.assertEqual(reads, [4096])
        self.assertEqual(entry['request_url'], url)
        self.assertEqual(entry['response_body_bytes'], 4096)
        self.assertEqual(entry['response_body_utf8'], 'X' * 4096)
        self.assertTrue(entry['response_body_capped'])
        self.assertEqual(entry['response_headers'],
                         {'content-type': 'text/html', 'retry-after': '61', 'server': 'test-edge'})
        self.assertNotIn('private-cookie', json.dumps(result))
        self.assertNotIn('private-auth', json.dumps(result))
        self.assertEqual(result['last_http_error']['response_body_sha256'], entry['response_body_sha256'])
        self.assertEqual(result['retry_after_seconds'], 61.0)


if __name__ == '__main__':
    unittest.main()

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

from utility.data import download_papers as downloader
from utility.data import prepare_source_data as pairing
from utility.data import build_pairs
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
        adapter = self.root / 'utility/data/prepare_source_data.py'
        adapter.parent.mkdir(parents=True)
        adapter.write_bytes(Path(pairing.__file__).read_bytes())
        (adapter.parent.parent / 'project.json').write_text(json.dumps({'data': {
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

    def test_cli_defaults_follow_nested_and_flat_snapshot_project_layouts(self):
        source = Path(downloader.__file__).read_bytes()
        config = {'data': {'index': 'catalog/index.json', 'downloads_root': 'downloads',
                           'pdf_root': 'originals'}}
        for location in ('utility/data', 'source_snapshot'):
            with self.subTest(location=location):
                project = self.root / location.replace('/', '-')
                script = project / location / 'download_papers.py'
                script.parent.mkdir(parents=True)
                script.write_bytes(source)
                settings = project / 'utility/project.json'
                settings.parent.mkdir(exist_ok=True)
                settings.write_text(json.dumps(config))
                with mock.patch.object(downloader, '__file__', str(script)), \
                        mock.patch('sys.argv', ['download_papers', '--dry-run']), \
                        mock.patch.object(downloader, 'download',
                                          return_value={'status': 'planned', 'summary': {}}) as run, \
                        redirect_stdout(io.StringIO()):
                    downloader.main()
                self.assertEqual(run.call_args.args[:2],
                                 (project / 'catalog/index.json', project / 'downloads'))
                self.assertEqual(run.call_args.kwargs['reuse_root'], project / 'originals')
                self.assertTrue(run.call_args.kwargs['dry_run'])

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

    def test_worker_bounds_and_parallel_dry_run(self):
        for value in (0, 1025, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.download(workers=value, dry_run=True)
        for workers in (4, 128, 1024):
            with self.subTest(workers=workers):
                receipt = self.download(workers=workers, interval=1, dry_run=True)
                self.assertEqual(receipt['workers'], workers)
                self.assertEqual(receipt['global_request_interval_seconds'], 1)
                self.assertFalse(self.store.exists())

    def test_four_workers_overlap_and_manifest_has_one_writer(self):
        import threading
        import time
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        active = peak = 0
        calls, writers = [], []
        main_thread = threading.get_ident()
        atomic = downloader.atomic_json
        def checkpoint(path, value):
            if path.name == 'download_manifest.json':
                writers.append(threading.get_ident())
            atomic(path, value)
        def fetch(url, target):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(url)
            barrier.wait(timeout=5)
            if url.endswith('v1'):
                time.sleep(0.03)
            write_pdf(target)
            with lock:
                active -= 1
        with mock.patch.object(downloader, 'atomic_json', side_effect=checkpoint), redirect_stdout(io.StringIO()):
            receipt = downloader.download(self.source, self.store, fetch=fetch, workers=4,
                                          interval=0, pdf_layout='legacy')
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(peak, 4)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(url.startswith('https://export.arxiv.org/pdf/') for url in calls))
        self.assertEqual({*writers}, {main_thread})
        self.assertEqual([entry['source_position'] for entry in receipt['files']], [1, 2, 3, 4])
        self.assertEqual([entry['version'] for entry in receipt['files']], [1, 2, 3, 4])

    def test_global_request_gate_includes_retries_from_all_workers(self):
        import threading
        import time
        lock = threading.Lock()
        starts, failed = [], False
        def fetch(url, target):
            nonlocal failed
            with lock:
                starts.append(time.monotonic())
                first = not failed
                failed = True
            if first:
                raise HTTPError(url, 500, 'transient', {}, None)
            write_pdf(target)
        with mock.patch.object(downloader.time, 'sleep'), redirect_stdout(io.StringIO()):
            receipt = downloader.download(self.source, self.store, fetch=fetch, workers=4,
                                          interval=0.025, pdf_layout='legacy')
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(len(starts), 5)
        self.assertTrue(all(after - before >= 0.020 for before, after in zip(starts, starts[1:])))

    def test_parallel_conference_memberships_download_each_original_only_once(self):
        result = self.download(workers=4, interval=0)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.urls), 4)
        self.assertEqual(result['summary']['file_count'], 8)
        self.assertEqual(sum(item['status'] == 'reused' for item in result['files']), 4)
        self.assertEqual(len(list(self.store.rglob('*.pdf'))), 8)

    def test_parallel_original_reference_reuse_is_readonly(self):
        base = self.root / 'data/pdf'
        for version in self.item['versions']:
            write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], version['version']))
        before = {path: path.read_bytes() for path in base.rglob('*.pdf')}
        extra = paper(aid='2501.00002', source_ids=['conf_a'])
        write_source(self.source, [self.item, extra])
        result = self.download(workers=4, interval=0, pdf_layout='legacy', reference_roots=[base])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.urls), 4)
        self.assertTrue(all('2501.00002v' in url for url in self.urls))
        self.assertEqual(before, {path: path.read_bytes() for path in base.rglob('*.pdf')})
        self.assertFalse((self.store / self.item['arxiv_id']).exists())

    def test_parallel_reference_mismatch_never_downloads_or_repairs(self):
        base = self.root / 'data/pdf'
        for version in self.item['versions']:
            write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], version['version']))
        before = {path: path.read_bytes() for path in base.rglob('*.pdf')}
        self.item['versions'][0]['pdf']['expected_sha256'] = '0' * 64
        write_source(self.source, [self.item])
        result = self.download(workers=4, interval=0, pdf_layout='legacy', reference_roots=[base])
        self.assertEqual(result['summary']['failed_file_count'], 1)
        self.assertEqual(self.urls, [])
        self.assertEqual(before, {path: path.read_bytes() for path in base.rglob('*.pdf')})

    def test_parallel_http_circuit_drains_accepted_calls_without_new_admissions(self):
        import threading
        import time
        for status in (406, 429, 503):
            with self.subTest(status=status):
                barrier = threading.Barrier(2)
                calls = []
                output = self.root / f'circuit-{status}'
                def fetch(url, target):
                    calls.append(url)
                    barrier.wait(timeout=5)
                    if url.endswith('v1'):
                        raise HTTPError(url, status, 'pause', {'Retry-After': '120'}, None)
                    time.sleep(0.04)
                    write_pdf(target)
                with redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=fetch, workers=2,
                                                 interval=0, pdf_layout='legacy')
                self.assertEqual(result['status'], 'rate_limited')
                self.assertEqual(len(calls), 2)
                self.assertEqual(result['summary']['processed_file_count'], 2)
                self.assertEqual(result['summary']['pending_file_count'], 2)
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertEqual(result['last_http_status'], status)
                self.assertEqual(result['retry_after_seconds'], 120.0)
                self.assertTrue(pairing.pdf_path(output, self.item['arxiv_id'], 2).is_file())
                saved = (output / 'download_manifest.json').read_bytes()
                with redirect_stdout(io.StringIO()):
                    retry = downloader.download(self.source, output, fetch=fetch, workers=4,
                                                interval=0, pdf_layout='legacy')
                self.assertEqual(retry['status'], 'rate_limited')
                self.assertEqual(len(calls), 2)
                self.assertEqual((output / 'download_manifest.json').read_bytes(), saved)

    def test_global_circuit_cancels_threads_waiting_for_request_gate(self):
        for workers in (4, 128, 1024):
            with self.subTest(workers=workers):
                calls = []
                output = self.root / f'workers-{workers}'
                def limited(url, target):
                    calls.append(url)
                    raise HTTPError(url, 429, 'pause', {}, None)
                with redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=limited, workers=workers,
                                                 interval=0.1, pdf_layout='legacy')
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['status'], 'rate_limited')
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertEqual(result['summary']['pending_file_count'], 3)
                self.assertFalse(list(output.rglob('.v*.pdf')))

    def test_http403_stops_serial_scope_and_remains_blocked_on_resume(self):
        calls = []
        def denied(url, target):
            calls.append(url)
            raise HTTPError(url, 403, 'Access denied', {}, None)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=denied, pdf_layout='legacy')
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'access_denied')
        self.assertTrue(result['requires_access_restoration'])
        self.assertNotIn('next_retry_at', result)
        self.assertEqual(result['summary']['pending_file_count'], 3)
        saved = (self.store / 'download_manifest.json').read_bytes()
        with redirect_stdout(io.StringIO()):
            retried = downloader.download(self.source, self.store, fetch=denied, workers=4, pdf_layout='legacy')
        self.assertEqual(retried['status'], 'access_denied')
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.store / 'download_manifest.json').read_bytes(), saved)

    def test_parallel_403_drain_does_not_clear_access_denial(self):
        import threading
        import time
        barrier = threading.Barrier(2)
        calls = []
        def fetch(url, target):
            calls.append(url)
            barrier.wait(timeout=5)
            if url.endswith('v1'):
                raise HTTPError(url, 403, 'Access denied', {}, None)
            time.sleep(0.04)
            write_pdf(target)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=fetch, workers=2,
                                         interval=0, pdf_layout='legacy')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['status'], 'access_denied')
        self.assertEqual(result['last_http_status'], 403)
        self.assertTrue(result['access_denied'])
        self.assertEqual(result['summary']['processed_file_count'], 2)
        self.assertEqual(result['summary']['pending_file_count'], 2)

    def test_parallel_interruption_preserves_drained_sparse_results_and_resumes(self):
        import threading
        import time
        barrier = threading.Barrier(2)
        calls = []
        def fetch(url, target):
            calls.append(url)
            barrier.wait(timeout=5)
            if url.endswith('v1'):
                raise KeyboardInterrupt()
            time.sleep(0.04)
            write_pdf(target)
        with redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            downloader.download(self.source, self.store, fetch=fetch, workers=2,
                                interval=0, pdf_layout='legacy')
        receipt = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(receipt['status'], 'interrupted')
        self.assertEqual(len(calls), 2)
        self.assertEqual(receipt['summary']['processed_file_count'], 1)
        self.assertEqual(receipt['summary']['pending_file_count'], 3)
        self.assertEqual(receipt['files'][0]['source_position'], 2)
        self.assertFalse((self.store / '.download.lock').exists())
        result = self.download(workers=4, interval=0, pdf_layout='legacy')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.urls), 3)
        self.assertFalse(any(url.endswith('v2') for url in self.urls))

    def test_parallel_circuit_keeps_longest_retry_after_and_denial_priority(self):
        import threading
        from datetime import datetime, timezone
        for first_status in (429, 403):
            with self.subTest(first_status=first_status):
                barrier = threading.Barrier(2)
                output = self.root / f'combined-errors-{first_status}'
                def fetch(url, target):
                    barrier.wait(timeout=5)
                    if url.endswith('v1'):
                        raise HTTPError(url, first_status, 'first', {'Retry-After': '120'}, None)
                    raise HTTPError(url, 503, 'second', {'Retry-After': '600'}, None)
                with redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=fetch, workers=2,
                                                 interval=0, pdf_layout='legacy')
                self.assertEqual(result['summary']['failed_file_count'], 2)
                self.assertEqual(result['summary']['pending_file_count'], 2)
                if first_status == 403:
                    self.assertEqual(result['status'], 'access_denied')
                    self.assertNotIn('next_retry_at', result)
                else:
                    self.assertEqual(result['status'], 'rate_limited')
                    remaining = (datetime.fromisoformat(result['next_retry_at']) -
                                 datetime.now(timezone.utc)).total_seconds()
                    self.assertGreater(remaining, 590)
                    self.assertGreater(result['retry_after_seconds'], 590)

    def test_circuit_closure_at_gate_release_prevents_transport_start(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        released, resume = threading.Event(), threading.Event()
        class PausedExitGate:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                released.set()
                if not resume.wait(5):
                    raise AssertionError('Gate-release probe timed out')
        calls = []
        fetcher = downloader.Fetcher(interval=0, transport=lambda url, path: calls.append(url),
                                     break_on_http_stop=True)
        fetcher.gate = PausedExitGate()
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(fetcher, 'https://export.arxiv.org/pdf/2401.04013v1', self.root / 'unused.pdf')
            self.assertTrue(released.wait(5))
            fetcher.stop_for_http(HTTPError('url', 429, 'pause', {}, None))
            resume.set()
            with self.assertRaises(downloader.DownloadStopped):
                task.result(timeout=5)
        self.assertEqual(calls, [])

    def test_circuit_closure_during_request_construction_prevents_urlopen(self):
        original = downloader.Request
        fetcher = downloader.Fetcher(interval=0)
        def request(*args, **kwargs):
            value = original(*args, **kwargs)
            fetcher.stop_for_http(HTTPError('url', 403, 'denied', {}, None))
            return value
        with mock.patch.object(downloader, 'Request', side_effect=request), \
                mock.patch.object(downloader, 'urlopen') as opening, \
                self.assertRaises(downloader.DownloadStopped):
            fetcher('https://export.arxiv.org/pdf/2401.04013v1', self.root / 'unused.pdf')
        opening.assert_not_called()


    def test_validation_configuration_and_retryable_option_bounds(self):
        for value in (0, 1025, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.download(validation_workers=value, dry_run=True)
        with self.assertRaises(ValueError):
            self.download(stop_on_retryable_error=1, dry_run=True)
        result = self.download(workers=1024, validation_workers=64,
                               stop_on_retryable_error=True, dry_run=True)
        self.assertEqual(result['validation_workers'], 64)
        self.assertTrue(result['stop_on_retryable_error'])
        self.assertEqual(result['workers'], 1024)

    def test_validation_gate_covers_cached_referenced_reused_and_downloaded(self):
        import threading
        local = threading.local()
        original, semaphore = downloader.validate_pdf, threading.BoundedSemaphore
        sizes, calls = [], []
        class Gate:
            def __init__(self, value):
                sizes.append(value)
                self.inner = semaphore(value)
            def acquire(self, **kwargs):
                acquired = self.inner.acquire(**kwargs)
                local.held = acquired
                return acquired
            def release(self):
                local.held = False
                self.inner.release()
        def validate(path, aid, version):
            self.assertTrue(getattr(local, 'held', False))
            calls.append(str(path))
            return original(path, aid, version)
        write_pdf(self.store / 'conf_a' / self.item['arxiv_id'] / 'v1.pdf')
        reuse, reference = self.root / 'reuse', self.root / 'reference'
        write_pdf(pairing.pdf_path(reuse, self.item['arxiv_id'], 2))
        write_pdf(pairing.pdf_path(reference, self.item['arxiv_id'], 3))
        with mock.patch.object(downloader.threading, 'BoundedSemaphore', Gate),                 mock.patch.object(downloader, 'validate_pdf', side_effect=validate):
            result = self.download(workers=4, validation_workers=64, interval=0,
                                   reuse_root=reuse, reference_roots=[reference])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(sizes, [64])
        self.assertEqual({entry['status'] for entry in result['files']},
                         {'cached', 'reused', 'referenced', 'downloaded'})
        self.assertGreater(len(calls), 8)

    def test_64_validation_slots_limit_80_concurrent_cached_tasks(self):
        import threading
        import time
        records = [paper(aid=f'2501.{i:05d}', source_ids=['conf_a']) for i in range(1, 21)]
        write_source(self.source, records)
        for item in records:
            for version in item['versions']:
                write_pdf(pairing.pdf_path(self.store, item['arxiv_id'], version['version']))
        original = downloader.validate_pdf
        active = peak = 0
        lock, ready, release = threading.Lock(), threading.Event(), threading.Event()
        def validate(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 64:
                    ready.set()
            try:
                if not release.wait(8):
                    raise AssertionError('validation gate probe timed out')
                return original(*args)
            finally:
                with lock:
                    active -= 1
        def unblock():
            ready.wait(8)
            time.sleep(0.04)
            release.set()
        helper = threading.Thread(target=unblock)
        helper.start()
        try:
            with mock.patch.object(downloader, 'validate_pdf', side_effect=validate):
                result = self.download(workers=80, validation_workers=64, interval=0, pdf_layout='legacy')
        finally:
            release.set()
            helper.join(10)
        self.assertTrue(ready.is_set())
        self.assertEqual(peak, 64)
        self.assertEqual(result['summary']['processed_file_count'], 80)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(self.urls, [])

    def test_exhausted_transient_errors_stop_opt_in_serial_scope(self):
        import errno
        from urllib.error import URLError
        errors = [HTTPError('mock', code, 'transient', {'Retry-After': '17'}, None)
                  for code in (500, 502, 504, 599)]
        errors += [URLError('disconnected'), TimeoutError('timed out'),
                   OSError(errno.EMFILE, 'descriptor limit'),
                   OSError(errno.ENOMEM, 'memory limit'), OSError(errno.EAGAIN, 'try again')]
        for number, error in enumerate(errors):
            with self.subTest(error=error):
                calls = []
                output = self.root / f'transient-{number}'
                def fetch(url, target):
                    calls.append(url)
                    raise error
                with mock.patch.object(downloader.time, 'sleep'), redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=fetch, retries=1,
                        interval=0, pdf_layout='legacy', stop_on_retryable_error=True)
                self.assertEqual(len(calls), 2)
                self.assertEqual(result['status'], 'retryable_failure')
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertEqual(result['summary']['pending_file_count'], 3)
                self.assertEqual(result['retryable_failure']['version'], 1)
                if isinstance(error, HTTPError):
                    self.assertEqual(result['retryable_failure']['retry_after_seconds'], 17)
                    self.assertIn('next_retry_at', result)
                self.assertFalse((output / '.download.lock').exists())

    def test_default_transient_errors_and_opt_in_per_file_failures_keep_scope(self):
        for enabled, error in ((False, HTTPError('mock', 500, 'transient', {}, None)),
                               (True, HTTPError('mock', 404, 'not found', {}, None)),
                               (True, HTTPError('mock', 410, 'gone', {}, None)),
                               (True, ValueError('deterministic PDF failure'))):
            with self.subTest(enabled=enabled, error=error):
                output = self.root / f'per-file-{enabled}-{type(error).__name__}-{getattr(error, "code", 0)}'
                def fetch(url, target):
                    if url.endswith('v1'):
                        raise error
                    write_pdf(target)
                with redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=fetch, workers=2,
                        interval=0, retries=0, pdf_layout='legacy', stop_on_retryable_error=enabled)
                self.assertEqual(result['status'], 'incomplete')
                self.assertEqual(result['summary']['processed_file_count'], 4)
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertNotIn('retryable_failure', result)

    def test_opt_in_retryable_circuit_drains_successful_peer(self):
        import threading
        import time
        from urllib.error import URLError
        barrier = threading.Barrier(2)
        calls = []
        def fetch(url, target):
            calls.append(url)
            barrier.wait(5)
            if url.endswith('v1'):
                raise URLError('connection reset')
            time.sleep(0.04)
            write_pdf(target)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=fetch, workers=2,
                interval=0, retries=0, pdf_layout='legacy', stop_on_retryable_error=True)
        self.assertEqual(result['status'], 'retryable_failure')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['summary']['processed_file_count'], 2)
        self.assertEqual(result['summary']['pending_file_count'], 2)
        self.assertEqual(result['retryable_failure']['kind'], 'network_error')
        self.assertTrue(pairing.pdf_path(self.store, self.item['arxiv_id'], 2).is_file())
        self.assertFalse((self.store / '.download.lock').exists())

    def test_thread_creation_failure_drains_and_preserves_receipts(self):
        import threading
        original_start = threading.Thread.start
        release, validating = threading.Event(), threading.Event()
        started = 0
        original_validate = downloader.validate_pdf
        def start(thread):
            nonlocal started
            started += 1
            if started == 2:
                self.assertTrue(validating.wait(5))
                release.set()
                raise RuntimeError("can't start new thread")
            return original_start(thread)
        def fetch(url, target):
            write_pdf(target)
        def validate(*args):
            validating.set()
            self.assertTrue(release.wait(5))
            return original_validate(*args)
        with mock.patch.object(threading.Thread, 'start', new=start), \
                mock.patch.object(downloader, 'validate_pdf', side_effect=validate), redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=fetch, workers=1024,
                validation_workers=64, interval=0, pdf_layout='legacy')
        self.assertEqual(result['status'], 'retryable_failure')
        self.assertEqual(result['retryable_failure']['kind'], 'thread_start_error')
        self.assertEqual(result['summary']['processed_file_count'], 1)
        self.assertEqual(result['summary']['pending_file_count'], 3)
        saved = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(saved, result)
        self.assertFalse((self.store / '.download.lock').exists())
        self.assertFalse(list(self.store.rglob('.v*.pdf')))

    def test_pool_resource_errors_are_structured_but_code_errors_raise(self):
        import errno
        for code in (errno.EMFILE, errno.ENOMEM, errno.EAGAIN):
            output = self.root / f'pool-{code}'
            with mock.patch.object(downloader, 'ThreadPoolExecutor', side_effect=OSError(code, 'resource')),                     redirect_stdout(io.StringIO()):
                result = downloader.download(self.source, output, workers=1024, pdf_layout='legacy')
            self.assertEqual(result['status'], 'retryable_failure')
            self.assertEqual(result['retryable_failure']['errno'], code)
            self.assertEqual(result['summary']['pending_file_count'], 4)
            self.assertFalse((output / '.download.lock').exists())
        with mock.patch.object(downloader, 'ThreadPoolExecutor', side_effect=RuntimeError('programming bug')),                 redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'programming bug'):
            downloader.download(self.source, self.store, workers=2, stop_on_retryable_error=True)
        self.assertFalse((self.store / '.download.lock').exists())

    def test_resource_errors_in_all_validation_paths_are_not_pdf_failures(self):
        import errno
        for mode in ('cached', 'referenced', 'reused', 'downloaded'):
            output = self.root / mode
            options = {}
            if mode == 'cached':
                write_pdf(pairing.pdf_path(output, self.item['arxiv_id'], 1))
            elif mode in ('referenced', 'reused'):
                base = self.root / (mode + '-base')
                write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], 1))
                options = {'reference_roots': [base]} if mode == 'referenced' else {'reuse_root': base}
            with mock.patch.object(downloader, 'validate_pdf', side_effect=OSError(errno.EMFILE, 'resource')),                     redirect_stdout(io.StringIO()):
                result = downloader.download(self.source, output, fetch=self.fetch, retries=0,
                    pdf_layout='legacy', validation_workers=64, stop_on_retryable_error=True, **options)
            self.assertEqual(result['status'], 'retryable_failure')
            self.assertEqual(result['retryable_failure']['kind'], 'resource_error')

    def test_retryable_evidence_survives_interruption_during_drain(self):
        import threading
        import time
        from urllib.error import URLError
        barrier, recorded = threading.Barrier(3), threading.Event()
        atomic = downloader.atomic_json
        def checkpoint(path, value):
            atomic(path, value)
            if value.get('retryable_failure'):
                recorded.set()
        def fetch(url, target):
            barrier.wait(5)
            if url.endswith('v1'):
                raise URLError('connection reset')
            if url.endswith('v2'):
                self.assertTrue(recorded.wait(5))
                raise KeyboardInterrupt()
            time.sleep(0.04)
            write_pdf(target)
        with mock.patch.object(downloader, 'atomic_json', side_effect=checkpoint),                 redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            downloader.download(self.source, self.store, fetch=fetch, workers=3, retries=0,
                interval=0, pdf_layout='legacy', stop_on_retryable_error=True)
        saved = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual(saved['retryable_failure']['kind'], 'network_error')
        self.assertEqual(saved['summary']['processed_file_count'], 2)
        self.assertTrue(pairing.pdf_path(self.store, self.item['arxiv_id'], 3).is_file())
        self.assertFalse((self.store / '.download.lock').exists())

    def test_opt_in_generic_transport_bug_still_raises(self):
        def fetch(url, target):
            raise RuntimeError('programming bug')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'programming bug'):
            downloader.download(self.source, self.store, fetch=fetch, workers=2, retries=0,
                interval=0, pdf_layout='legacy', stop_on_retryable_error=True)
        saved = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(saved['status'], 'interrupted')
        self.assertNotIn('retryable_failure', saved)
        self.assertFalse((self.store / '.download.lock').exists())


    def test_stopped_validation_waiters_remain_pending_in_every_storage_mode(self):
        import threading
        for mode in ('cached', 'referenced', 'reused', 'downloaded'):
            with self.subTest(mode=mode):
                output = self.root / ('waiting-' + mode)
                entered, waiting, release = threading.Event(), threading.Event(), threading.Event()
                original, semaphore, atomic = downloader.validate_pdf, threading.BoundedSemaphore, downloader.atomic_json
                class Gate:
                    def __init__(self, value):
                        self.inner = semaphore(value)
                    def acquire(self, **kwargs):
                        if entered.is_set() and not release.is_set():
                            waiting.set()
                        return self.inner.acquire(**kwargs)
                    def release(self):
                        self.inner.release()
                def validate(path, aid, version):
                    if version['version'] == 1:
                        entered.set()
                        self.assertTrue(release.wait(5))
                    return original(path, aid, version)
                def checkpoint(path, value):
                    atomic(path, value)
                    if value.get('access_denied'):
                        release.set()
                options = {}
                write_pdf(pairing.pdf_path(output, self.item['arxiv_id'], 1))
                if mode == 'cached':
                    write_pdf(pairing.pdf_path(output, self.item['arxiv_id'], 2))
                elif mode in ('referenced', 'reused'):
                    base = self.root / ('waiting-base-' + mode)
                    write_pdf(pairing.pdf_path(base, self.item['arxiv_id'], 2))
                    options = {'reference_roots': [base]} if mode == 'referenced' else {'reuse_root': base}
                calls = []
                third_started = threading.Event()
                original_submit, submitted = downloader.ThreadPoolExecutor.submit, 0
                def submit(pool, *args, **kwargs):
                    nonlocal submitted
                    future = original_submit(pool, *args, **kwargs)
                    submitted += 1
                    if submitted == 1:
                        self.assertTrue(entered.wait(5))
                    if submitted == 3:
                        self.assertTrue(third_started.wait(5))
                    return future
                def fetch(url, target):
                    calls.append(url)
                    if url.endswith('v3'):
                        third_started.set()
                        self.assertTrue(entered.wait(5))
                        self.assertTrue(waiting.wait(5))
                        raise HTTPError(url, 403, 'denied', {}, None)
                    write_pdf(target)
                try:
                    with mock.patch.object(downloader.threading, 'BoundedSemaphore', Gate), \
                            mock.patch.object(downloader.ThreadPoolExecutor, 'submit', new=submit),                             mock.patch.object(downloader, 'validate_pdf', side_effect=validate),                             mock.patch.object(downloader, 'atomic_json', side_effect=checkpoint),                             redirect_stdout(io.StringIO()):
                        result = downloader.download(self.source, output, fetch=fetch, workers=4,
                            validation_workers=1, interval=0.1, retries=0, pdf_layout='legacy',
                            stop_on_retryable_error=True, **options)
                finally:
                    release.set()
                self.assertEqual(result['status'], 'access_denied')
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertEqual(result['summary']['processed_file_count'], 3 if mode == 'downloaded' else 2)
                self.assertEqual(result['summary']['pending_file_count'], 1 if mode == 'downloaded' else 2)
                self.assertEqual({row['version'] for row in result['files']}, {1, 2, 3} if mode == 'downloaded' else {1, 3})
                self.assertEqual(sum(url.endswith('v2') for url in calls), 1 if mode == 'downloaded' else 0)
                self.assertFalse((output / '.download.lock').exists())

    def test_access_denial_dominates_simultaneous_retryable_error(self):
        import threading
        barrier = threading.Barrier(2)
        def fetch(url, target):
            barrier.wait(5)
            raise HTTPError(url, 403 if url.endswith('v1') else 502, 'stop', {'Retry-After': '90'}, None)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=fetch, workers=2,
                interval=0, retries=0, pdf_layout='legacy', stop_on_retryable_error=True)
        self.assertEqual(result['status'], 'access_denied')
        self.assertEqual(result['last_http_status'], 403)
        self.assertEqual(result['retryable_failure']['http_status'], 502)
        self.assertNotIn('next_retry_at', result)
        self.assertEqual(result['summary']['processed_file_count'], 2)


    def test_retryable_http_circuit_exposes_longest_top_level_deadline(self):
        import threading
        from datetime import datetime, timezone
        barrier = threading.Barrier(2)
        before = datetime.now(timezone.utc)
        def fetch(url, target):
            barrier.wait(5)
            raise HTTPError(url, 500 if url.endswith('v1') else 502, 'retry',
                            {'Retry-After': '900' if url.endswith('v1') else '1200'}, None)
        with redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=fetch, workers=2,
                interval=0, retries=0, pdf_layout='legacy', stop_on_retryable_error=True)
        self.assertEqual(result['status'], 'retryable_failure')
        deadline = datetime.fromisoformat(result['next_retry_at'])
        self.assertGreaterEqual((deadline - before).total_seconds(), 1200)
        self.assertIn(result['retryable_failure']['retry_after_seconds'], (900, 1200))
        saved = json.loads((self.store / 'download_manifest.json').read_text())
        self.assertEqual(saved['next_retry_at'], result['next_retry_at'])

    def test_rate_circuit_preserves_inflight_download_with_bounded_validation(self):
        import threading
        import time
        for validators in (1, 64):
            with self.subTest(validation_workers=validators):
                output = self.root / f'finishing-{validators}'
                barrier = threading.Barrier(2)
                def fetch(url, target):
                    barrier.wait(5)
                    if url.endswith('v1'):
                        raise HTTPError(url, 429, 'pause', {}, None)
                    time.sleep(0.04)
                    write_pdf(target)
                with redirect_stdout(io.StringIO()):
                    result = downloader.download(self.source, output, fetch=fetch, workers=2,
                        validation_workers=validators, interval=0, retries=0, pdf_layout='legacy')
                self.assertEqual(result['status'], 'rate_limited')
                self.assertEqual(result['summary']['processed_file_count'], 2)
                self.assertEqual(result['summary']['pending_file_count'], 2)
                self.assertEqual(result['summary']['failed_file_count'], 1)
                self.assertEqual(next(r['status'] for r in result['files'] if r['version'] == 2), 'downloaded')
                self.assertTrue(pairing.pdf_path(output, self.item['arxiv_id'], 2).is_file())
                self.assertFalse((output / '.download.lock').exists())

    def test_memory_error_during_validation_is_a_resource_failure(self):
        write_pdf(pairing.pdf_path(self.store, self.item['arxiv_id'], 1))
        with mock.patch.object(downloader, 'validate_pdf', side_effect=MemoryError('allocator exhausted')),                 redirect_stdout(io.StringIO()):
            result = downloader.download(self.source, self.store, fetch=self.fetch, workers=1,
                validation_workers=64, stop_on_retryable_error=True, pdf_layout='legacy')
        self.assertEqual(result['status'], 'retryable_failure')
        self.assertEqual(result['retryable_failure']['kind'], 'resource_error')
        self.assertEqual(result['retryable_failure']['exception_type'], 'MemoryError')
        self.assertEqual(self.urls, [])
        self.assertFalse((self.store / '.download.lock').exists())


if __name__ == '__main__':
    unittest.main()

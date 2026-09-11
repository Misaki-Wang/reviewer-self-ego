"""Exercise preparation with real small PDFs and a simulated arXiv transport."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
import prepare_input_data as prep


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.aid = '2501.00001'
        versions = [{'version': v, 'submitted_at': f'2025-0{v}-01T00:00:00Z',
                     'pdf_url': f'https://arxiv.org/pdf/{self.aid}v{v}'} for v in (1, 2, 3)]
        paper = {'arxiv_id': self.aid, 'title': 'Fixture', 'venue': 'ICLR2026',
                 'raw_version_count': 3, 'version_history': {'complete_history': True,
                 'validation_errors': [], 'frozen_N': 3, 'versions': versions}}
        self.source = self.root / 'source.json'
        self.source.write_text(json.dumps({'records': [paper]}))
        self.args = argparse.Namespace(source=self.source, output=self.root / 'input_data',
                                       arxiv_ids=[self.aid], seed=1, delay=0, dry_run=False)
        self.fail_v2 = False
        self.wrong_filename = False

    def transport(self, command, **kwargs):
        url = command[-1]
        if self.fail_v2 and url.endswith('v2'):
            return subprocess.CompletedProcess(command, 22, '', 'HTTP 503')
        target = Path(command[command.index('--output') + 1])
        writer = PdfWriter()
        for _ in range(int(url[-1])):
            writer.add_blank_page(width=100, height=100)
        writer.write(target)
        headers = Path(command[command.index('--dump-header') + 1])
        name = f'{self.aid}v99' if self.wrong_filename else url.rsplit('/', 1)[1]
        headers.write_text(f'content-disposition: inline; filename="{name}.pdf"\n')
        return subprocess.CompletedProcess(command, 0, url, '')

    def run_prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return prep.prepare(self.args)

    def test_failure_resume_and_pair_files(self):
        self.fail_v2 = True
        with patch.object(prep.subprocess, 'run', side_effect=self.transport):
            self.assertEqual(self.run_prepare(), 1)
        self.assertEqual(list(self.args.output.glob('*/v*_v*')), [])
        self.fail_v2 = False
        with patch.object(prep.subprocess, 'run', side_effect=self.transport) as download:
            self.assertEqual(self.run_prepare(), 0)
            self.assertEqual(download.call_count, 1)
        index = json.loads((self.args.output / 'pairs.json').read_text())
        self.assertEqual(index['summary']['pair_count'], 2)
        for pair in index['pairs']:
            for side in ('before', 'after'):
                endpoint = pair[side]
                path = self.args.output / endpoint['pdf_path']
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertEqual(prep.digest(path), endpoint['sha256'])
                self.assertEqual(endpoint['pages'], endpoint['version'])
        with patch.object(prep.subprocess, 'run', side_effect=AssertionError('Cache miss')):
            self.assertEqual(self.run_prepare(), 0)

    def test_wrong_version_response_rejected(self):
        self.wrong_filename = True
        with patch.object(prep.subprocess, 'run', side_effect=self.transport):
            self.assertEqual(self.run_prepare(), 1)
        index = json.loads((self.args.output / 'pairs.json').read_text())
        self.assertEqual(index['summary']['verified_pdfs'], 0)
        self.assertEqual(index['pairs'], [])
        self.assertEqual(list(self.args.output.rglob('*.part')), [])

    def test_incomplete_history_rejected_before_writing(self):
        source = json.loads(self.source.read_text())
        source['records'][0]['version_history']['versions'].pop(1)
        self.source.write_text(json.dumps(source))
        with self.assertRaisesRegex(ValueError, 'Incomplete version history'):
            self.run_prepare()
        self.assertFalse(self.args.output.exists())

    def test_copy_fallback(self):
        source, target = self.root / 'source.pdf', self.root / 'target.pdf'
        source.write_bytes(b'PDF fixture bytes')
        with patch.object(prep.os, 'link', side_effect=OSError('No hardlinks')):
            prep.materialize(source, target, prep.digest(source))
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertFalse(target.is_symlink())


if __name__ == '__main__':
    unittest.main()

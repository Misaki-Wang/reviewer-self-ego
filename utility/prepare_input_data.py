"""Shared local PDF and JSON helpers for v1.3 preparation.

The preparation entry point is prepare_v13_pilot.py. Historical v1.1 sampling
and downloading are retained in Git history, not in the active workflow.
"""
import hashlib
import json
import os
import re
import shutil

from pypdf import PdfReader


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_pdf(path, aid, number):
    data = path.read_bytes()
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
        raise ValueError('Missing PDF header or EOF marker')
    reader = PdfReader(path)
    if not reader.pages:
        raise ValueError('PDF has no pages')
    for page in reader.pages:
        _ = page.mediabox
    text = reader.pages[0].extract_text() or ''
    stamps = re.findall(r'arXiv:\s*(\d{4}\.\d{4,5})v(\d+)', text)
    own = {(a, int(v)) for a, v in stamps if a == aid}
    if own and own != {(aid, number)}:
        raise ValueError(f'Wrong PDF version: {own}; expected {aid}v{number}')
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'pages': len(reader.pages),
            'first_page_version_stamp': f'{aid}v{number}' if own else None}


def materialize(source, target, sha256):
    if target.is_file() and not target.is_symlink() and digest(target) == sha256:
        return
    temporary = target.with_suffix('.pdf.tmp')
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copyfile(source, temporary)
    temporary.replace(target)
    if target.is_symlink() or digest(target) != sha256:
        raise ValueError(f'Pair PDF verification failed: {target}')


if __name__ == '__main__':
    raise SystemExit('Use utility/prepare_v13_pilot.py to prepare v1.3 inputs.')

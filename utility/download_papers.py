#!/usr/bin/env python3
"""Download every catalogued original version, independently of pair selection.

Files: downloads_papers/<conference>/<arxiv_id>/vN.pdf and metadata.json.
The source_data catalog defines the complete frozen history (v1..frozen_N).
No temporal, page-count or pair eligibility filter is used here.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from .prepare_source_data import load_source, pdf_path, validate_pdf
else:
    from prepare_source_data import load_source, pdf_path, validate_pdf


def safe_path(root, path):
    """Reject symlinks and non-directory parents within the writable store."""
    relative = path.relative_to(root)
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        if current.is_symlink() or not current.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Unsafe download path: {current}')
        if current != path and current.exists() and not current.is_dir():
            raise ValueError(f'Download parent is not a directory: {current}')
    if path.exists() and not path.is_file():
        raise ValueError(f'Download destination is not a file: {path}')
    return path


def atomic_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def inventory(source, root, conferences=(), arxiv_ids=()):
    records, provenance = load_source(source)
    known_conferences = {s for p in records for s in p['source_ids']}
    if set(conferences) - known_conferences:
        raise ValueError(f'Unknown conferences: {sorted(set(conferences) - known_conferences)}')
    selected = [p for p in records if (not conferences or set(conferences) & set(p['source_ids']))
                and (not arxiv_ids or p['arxiv_id'] in arxiv_ids)]
    missing = set(arxiv_ids) - {p['arxiv_id'] for p in selected}
    if missing or not selected:
        raise ValueError(f'No matching papers or IDs outside selected conferences: {sorted(missing)}')
    requests, metadata = [], {}
    for paper in selected:
        aid, versions = paper['arxiv_id'], paper.get('versions')
        count = paper.get('frozen_N')
        if (type(count) is not int or count < 1 or not isinstance(versions, list)
                or any(not isinstance(v, dict) or type(v.get('version')) is not int for v in versions)
                or sorted(v['version'] for v in versions) != list(range(1, count + 1))):
            raise ValueError(f'Incomplete original version history: {aid}')
        for conference in paper['source_ids']:
            if conferences and conference not in conferences:
                continue
            for version in sorted(versions, key=lambda v: v['version']):
                target = safe_path(root, pdf_path(root, aid, version['version'], conference))
                meta = safe_path(root, target.parent / 'metadata.json')
                metadata[meta] = paper
                requests.append((conference, paper, version, target))
    for path, paper in metadata.items():
        if path.exists() and json.loads(path.read_text(encoding='utf-8')) != paper:
            raise ValueError(f'Existing metadata differs from the source catalog: {path}')
    return requests, metadata, provenance


class Fetcher:
    def __init__(self, interval=3.0, timeout=60.0):
        self.interval, self.timeout, self.last_request = interval, timeout, None

    def __call__(self, url, destination):
        if self.last_request is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        request = Request(url, headers={'User-Agent': 'reviewer-self-ego/download-papers',
                                       'Accept': 'application/pdf'})
        with urlopen(request, timeout=self.timeout) as response, destination.open('wb') as stream:
            shutil.copyfileobj(response, stream)


def acquire(target, aid, version, fetch, retries, reusable=None):
    """Resume only validated files; replace a destination only after validation."""
    if target.is_file():
        try:
            return 'cached', validate_pdf(target, aid, version)
        except Exception:
            pass  # Keep the old bytes until a complete replacement is verified.
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f'.v{version["version"]}.',
                                     suffix='.pdf', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        if reusable is not None and reusable.is_file():
            try:
                validate_pdf(reusable, aid, version)
                shutil.copyfile(reusable, temporary)
                info = validate_pdf(temporary, aid, version)
                temporary.replace(target)
                return 'reused', {**info, 'path': str(target.resolve())}
            except Exception:
                pass
        # Always request the explicit original version, never the latest alias.
        url = f'https://arxiv.org/pdf/{aid}v{version["version"]}'
        for attempt in range(retries + 1):
            try:
                fetch(url, temporary)
                info = validate_pdf(temporary, aid, version)
                temporary.replace(target)
                return 'downloaded', {**info, 'path': str(target.resolve())}
            except Exception as exc:
                if attempt == retries or (isinstance(exc, HTTPError) and exc.code in (400, 401, 403, 404, 410)):
                    raise
                delay = min(60, 2 ** attempt)
                if isinstance(exc, HTTPError) and exc.headers:
                    retry_after = exc.headers.get('Retry-After', '')
                    if retry_after.isdigit():
                        delay = max(delay, int(retry_after))
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def download_lock(root):
    path = safe_path(root, root / '.download.lock')
    with path.open('x'):
        try:
            yield
        finally:
            path.unlink()


def download(source, root, conferences=(), arxiv_ids=(), *, retries=2,
             interval=3.0, timeout=60.0, reuse_root=None, dry_run=False, fetch=None):
    if (type(retries) is not int or retries < 0 or not math.isfinite(interval)
            or interval < 0 or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('retries/interval must be nonnegative and timeout must be positive')
    root = Path(root).absolute()
    if root.is_symlink():
        raise ValueError(f'Unsafe download root: {root}')
    root = root.resolve()
    requests, metadata, provenance = inventory(source, root, conferences, arxiv_ids)
    manifest = safe_path(root, root / 'download_manifest.json')
    receipt = {'schema_version': 'download-papers-1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
               **provenance, 'downloads_root': str(root), 'status': 'planned',
               'scope': {'conferences': list(conferences), 'arxiv_ids': list(arxiv_ids),
                         'versions': 'Every original version from v1 through frozen_N'},
               'summary': {'paper_count': len({p['arxiv_id'] for p in metadata.values()}),
                           'version_count': len({(p['arxiv_id'], v['version']) for _, p, v, _ in requests}),
                           'file_count': len(requests)}, 'files': []}
    if dry_run:
        return receipt
    root.mkdir(parents=True, exist_ok=True)
    fetch = fetch or Fetcher(interval, timeout)
    with download_lock(root):
        for path, paper in metadata.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, paper)
        verified = {}
        for conference, paper, version, target in requests:
            aid, number = paper['arxiv_id'], version['version']
            entry = {'conference': conference, 'arxiv_id': aid, 'version': number,
                     'pdf_path': str(target.relative_to(root))}
            reusable = verified.get((aid, number))
            if reusable is None and reuse_root is not None:
                reusable = pdf_path(Path(reuse_root), aid, number)
            try:
                status, evidence = acquire(target, aid, version, fetch, retries, reusable)
                entry.update(status=status, validation=evidence)
                verified[(aid, number)] = target
            except Exception as exc:
                entry.update(status='failed', detail=str(exc))
            receipt['files'].append(entry)
            print(f'{conference}/{aid}/v{number}.pdf: {entry["status"]}', flush=True)
        receipt['summary']['failed_file_count'] = sum(f['status'] == 'failed' for f in receipt['files'])
        receipt['status'] = 'incomplete' if receipt['summary']['failed_file_count'] else 'complete'
        atomic_json(manifest, receipt)
    return receipt


def main():
    project = Path(__file__).resolve().parent.parent
    config = json.loads((project / 'utility/project.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=project / config['data']['index'])
    parser.add_argument('--output', type=Path, default=project / config['data']['downloads_root'])
    parser.add_argument('--conf', action='append', default=[], help='Conference/source ID; repeatable. Default: all.')
    parser.add_argument('--arxiv-id', action='append', default=[], help='Paper ID; repeatable. Default: all.')
    parser.add_argument('--reuse-pdf-root', type=Path, default=project / config['data']['pdf_root'],
                        help='Reuse validated PDFs from the old <id>/<id>vN.pdf store when available.')
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--interval', type=float, default=3.0, help='Minimum seconds between network requests.')
    parser.add_argument('--timeout', type=float, default=60.0)
    parser.add_argument('--dry-run', action='store_true', help='Count all versions; no writes or downloads.')
    args = parser.parse_args()
    try:
        result = download(args.source, args.output, args.conf, args.arxiv_id, retries=args.retries,
                          interval=args.interval, timeout=args.timeout, reuse_root=args.reuse_pdf_root,
                          dry_run=args.dry_run)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'Download failed: {exc}\n')
    print(json.dumps({'status': result['status'], **result['summary']}))
    if result['status'] == 'incomplete':
        parser.exit(1, 'Some versions failed; see download_manifest.json and rerun to retry.\n')


if __name__ == '__main__':
    main()

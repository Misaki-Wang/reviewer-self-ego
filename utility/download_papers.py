#!/usr/bin/env python3
"""Download every catalogued original version, independently of pair selection.

Files: downloads_papers/<conference>/<arxiv_id>/vN.pdf and metadata.json.
The source_data catalog defines the complete frozen history (v1..frozen_N).
No temporal, page-count or pair eligibility filter is used here.
"""
import argparse
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import shutil
import signal
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if __package__:
    from .prepare_source_data import load_source, pdf_path, resolve_pdf, validate_pdf
else:
    from prepare_source_data import load_source, pdf_path, resolve_pdf, validate_pdf


GLOBAL_BACKOFF_HTTP_STATUSES = (406, 429, 503)

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


def inventory(source, root, conferences=(), arxiv_ids=(), pdf_layout='conference'):
    if pdf_layout not in ('legacy', 'conference'):
        raise ValueError(f'Unsupported PDF layout: {pdf_layout}')
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
                # Validate conference identity even when writing one shared legacy path.
                conference_path = pdf_path(root, aid, version['version'], conference)
                target = safe_path(root, conference_path if pdf_layout == 'conference'
                                   else pdf_path(root, aid, version['version']))
                meta = safe_path(root, target.parent / 'metadata.json')
                metadata[meta] = paper
                requests.append((conference, paper, version, target))
            if pdf_layout == 'legacy':
                break  # One original PDF per paper/version, independent of source memberships.
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
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            # Public error diagnostics only: never retain cookies or auth headers.
            allowed = {'content-type', 'content-length', 'retry-after', 'date',
                       'server', 'via', 'x-cache', 'x-served-by', 'x-timer'}
            evidence = {'request_url': url,
                        'response_headers': {key.lower(): value[:1024]
                                             for key, value in (exc.headers.items() if exc.headers else ())
                                             if key.lower() in allowed},
                        'response_observed_at_utc': datetime.now(timezone.utc).isoformat()}
            try:
                body = exc.read(4096)
                evidence.update(response_body_bytes=len(body),
                                response_body_sha256=hashlib.sha256(body).hexdigest(),
                                response_body_utf8=body.decode('utf-8', errors='replace'),
                                response_body_capped=len(body) == 4096)
            except Exception as diagnostic_error:
                evidence['response_body_error'] = type(diagnostic_error).__name__
            finally:
                exc.close()
            exc.download_http_evidence = evidence
            raise
        with response, destination.open('wb') as stream:
            shutil.copyfileobj(response, stream)


def retry_delay(exc, attempt, now=None):
    """Honor Retry-After seconds or HTTP dates; 406/429/503 cool down globally."""
    delay = min(60, 2 ** attempt)
    if isinstance(exc, HTTPError):
        if exc.code in GLOBAL_BACKOFF_HTTP_STATUSES:
            delay = max(60, delay)
        value = exc.headers.get('Retry-After', '') if exc.headers else ''
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 0
        if math.isfinite(seconds):
            delay = max(delay, seconds)
    return float(delay)


def failure_evidence(exc):
    result = {'status': 'failed', 'detail': str(exc)}
    if isinstance(exc, HTTPError):
        result.update(http_status=exc.code, error_kind={
            404: 'http_not_found', 410: 'http_gone', 406: 'http_not_acceptable',
            429: 'rate_limited', 503: 'service_unavailable',
        }.get(exc.code, 'http_error'))
        result.update(getattr(exc, 'download_http_evidence', {}))
    else:
        result['error_kind'] = 'validation_error' if isinstance(exc, ValueError) else 'network_error'
    return result


def acquire(target, aid, version, fetch, retries, reusable=None, on_backoff=None):
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
        # The documented programmatic harvesting host; no host fallback.
        # Always request the explicit original version, never the latest alias.
        url = f'https://export.arxiv.org/pdf/{aid}v{version["version"]}'
        for attempt in range(retries + 1):
            try:
                fetch(url, temporary)
                try:
                    info = validate_pdf(temporary, aid, version)
                except Exception as exc:
                    raise ValueError(f'Invalid original PDF: {exc}') from exc
                temporary.replace(target)
                return 'downloaded', {**info, 'path': str(target.resolve())}
            except Exception as exc:
                delay = retry_delay(exc, attempt)
                if isinstance(exc, HTTPError) and exc.code in GLOBAL_BACKOFF_HTTP_STATUSES and on_backoff:
                    on_backoff(exc, delay)
                if attempt == retries or (isinstance(exc, HTTPError) and exc.code in (400, 401, 403, 404, 410)):
                    raise
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
             interval=3.0, timeout=60.0, reuse_root=None, dry_run=False, fetch=None,
             pdf_layout='conference', reference_roots=()):
    if (type(retries) is not int or retries < 0 or not math.isfinite(interval)
            or interval < 0 or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('retries/interval must be nonnegative and timeout must be positive')
    root = Path(root).absolute()
    if root.is_symlink():
        raise ValueError(f'Unsafe download root: {root}')
    root = root.resolve()
    reference_roots = [Path(value).resolve() for value in reference_roots]
    if any(root.is_relative_to(value) or value.is_relative_to(root) for value in reference_roots):
        raise ValueError('Writable PDF root must be disjoint from readonly reference roots')
    requests, metadata, provenance = inventory(source, root, conferences, arxiv_ids, pdf_layout)
    manifest = safe_path(root, root / 'download_manifest.json')
    receipt = {'schema_version': 'download-papers-1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
               **provenance, 'downloads_root': str(root), 'status': 'planned',
               'pdf_layout': pdf_layout, 'reference_pdf_roots': [str(value) for value in reference_roots],
               'scope': {'conferences': list(conferences), 'arxiv_ids': list(arxiv_ids),
                         'versions': 'Every original version from v1 through frozen_N'},
               'summary': {'paper_count': len({p['arxiv_id'] for p in metadata.values()}),
                           'version_count': len({(p['arxiv_id'], v['version']) for _, p, v, _ in requests}),
                           'file_count': len(requests), 'processed_file_count': 0,
                           'pending_file_count': len(requests), 'failed_file_count': 0,
                           'http_unavailable_file_count': 0}, 'files': []}
    if dry_run:
        return receipt
    root.mkdir(parents=True, exist_ok=True)
    fetch = fetch or Fetcher(interval, timeout)
    last_checkpoint = time.monotonic()

    def checkpoint():
        nonlocal last_checkpoint
        receipt['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        atomic_json(manifest, receipt)
        last_checkpoint = time.monotonic()

    def record_backoff(exc, delay):
        now = datetime.now(timezone.utc)
        receipt.update(next_retry_at=(now + timedelta(seconds=delay)).isoformat(),
                       retry_after_seconds=delay, last_http_status=exc.code,
                       last_http_error=failure_evidence(exc))
        checkpoint()

    with download_lock(root):
        # A restarted process must respect the previous process's server cooldown.
        # Keep its detailed checkpoint intact when no new work can begin yet.
        if manifest.is_file():
            previous = json.loads(manifest.read_text(encoding='utf-8'))
            if previous.get('next_retry_at'):
                deadline = datetime.fromisoformat(previous['next_retry_at'])
                remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
                if remaining > 0:
                    receipt.update(status='rate_limited', next_retry_at=previous['next_retry_at'],
                                   retry_after_seconds=remaining,
                                   last_http_status=previous.get('last_http_status'),
                                   cooldown_manifest=str(manifest))
                    return receipt
        receipt['status'] = 'in_progress'
        checkpoint()
        try:
            for path, paper in (metadata.items() if pdf_layout == 'conference' else ()):
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
                stop = False
                try:
                    candidate = resolve_pdf(root, aid, number, conference if pdf_layout == 'conference' else None,
                                            reference_roots)
                    if candidate != target:
                        # Existing reference bytes are authoritative: validation failures
                        # are reported, never repaired in place or hidden by a download.
                        try:
                            evidence = validate_pdf(candidate, aid, version)
                        except Exception as exc:
                            raise ValueError(f'Invalid readonly original PDF: {exc}') from exc
                        status = 'referenced'
                        entry.update(pdf_path=str(candidate.resolve()), readonly=True)
                    else:
                        status, evidence = acquire(target, aid, version, fetch, retries, reusable,
                                                   on_backoff=record_backoff)
                    entry.update(status=status, validation=evidence)
                    verified[(aid, number)] = Path(evidence['path'])
                    # Successful acquisition has already waited out any transient cooldown.
                    if status == 'downloaded':
                        for key in ('next_retry_at', 'retry_after_seconds', 'last_http_status', 'last_http_error'):
                            receipt.pop(key, None)
                except Exception as exc:
                    entry.update(failure_evidence(exc))
                    stop = isinstance(exc, HTTPError) and exc.code in GLOBAL_BACKOFF_HTTP_STATUSES
                receipt['files'].append(entry)
                summary = receipt['summary']
                summary['processed_file_count'] += 1
                summary['pending_file_count'] -= 1
                summary['failed_file_count'] += entry['status'] == 'failed'
                summary['http_unavailable_file_count'] += entry.get('http_status') in (404, 410)
                print(f'{conference}/{aid}/v{number}.pdf: {entry["status"]}', flush=True)
                if stop:
                    receipt['status'] = 'rate_limited'
                    break
                if len(receipt['files']) % 25 == 0 or time.monotonic() - last_checkpoint >= 30:
                    checkpoint()
            else:
                receipt['status'] = 'incomplete' if receipt['summary']['failed_file_count'] else 'complete'
        except BaseException:
            receipt['status'] = 'interrupted'
            raise
        finally:
            checkpoint()
    return receipt


def main():
    project = Path(__file__).resolve().parent.parent
    config = json.loads((project / 'utility/project.json').read_text(encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=project / config['data']['index'])
    parser.add_argument('--output', type=Path, default=project / config['data']['downloads_root'])
    parser.add_argument('--pdf-layout', choices=('legacy', 'conference'), default='conference',
                        help='Use legacy for a shared <id>/<id>vN.pdf additions store.')
    parser.add_argument('--reference-pdf-root', type=Path, action='append', default=[],
                        help='Readonly legacy PDF store used directly without copying; repeatable.')
    parser.add_argument('--conf', action='append', default=[], help='Conference/source ID; repeatable. Default: all.')
    parser.add_argument('--arxiv-id', action='append', default=[], help='Paper ID; repeatable. Default: all.')
    parser.add_argument('--reuse-pdf-root', type=Path, default=project / config['data']['pdf_root'],
                        help='Reuse validated PDFs from the old <id>/<id>vN.pdf store when available.')
    parser.add_argument('--retries', type=int, default=2)
    parser.add_argument('--interval', type=float, default=3.0, help='Minimum seconds between network requests.')
    parser.add_argument('--timeout', type=float, default=60.0)
    parser.add_argument('--dry-run', action='store_true', help='Count all versions; no writes or downloads.')
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'Interrupted by signal {signum}')

    previous_handler = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = download(args.source, args.output, args.conf, args.arxiv_id, retries=args.retries,
                          interval=args.interval, timeout=args.timeout, reuse_root=args.reuse_pdf_root,
                          dry_run=args.dry_run, pdf_layout=args.pdf_layout,
                          reference_roots=args.reference_pdf_root)
    except KeyboardInterrupt:
        parser.exit(130, 'Download interrupted; progress is saved in download_manifest.json.\n')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f'Download failed: {exc}\n')
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    print(json.dumps({'status': result['status'], **result['summary'],
                      **{key: result[key] for key in ('next_retry_at', 'retry_after_seconds') if key in result}}))
    if result['status'] not in ('planned', 'complete'):
        parser.exit(1, 'Download incomplete; see download_manifest.json and respect next_retry_at before resuming.\n')


if __name__ == '__main__':
    main()

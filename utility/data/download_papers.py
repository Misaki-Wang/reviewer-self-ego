#!/usr/bin/env python3
"""Download every catalogued original version, independently of pair selection.

Files: downloads_papers/<conference>/<arxiv_id>/vN.pdf and metadata.json.
The source_data catalog defines the complete frozen history (v1..frozen_N).
No temporal, page-count or pair eligibility filter is used here.
"""
import argparse
import errno
import hashlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
import queue
from pathlib import Path
import shutil
import signal
import tempfile
import time
import threading
from urllib.error import HTTPError, URLError
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
    def __init__(self, interval=3.0, timeout=60.0, *, stop_event=None, transport=None,
                 break_on_http_stop=False, on_http_stop=None):
        self.interval, self.timeout, self.last_request = interval, timeout, None
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.gate = threading.Lock()
        self.transport, self.break_on_http_stop, self.on_http_stop = transport, break_on_http_stop, on_http_stop

    def stop_for_http(self, exc):
        if exc.code == 403 or (self.break_on_http_stop and exc.code in GLOBAL_BACKOFF_HTTP_STATUSES):
            self.stop_event.set()
            if self.on_http_stop:
                self.on_http_stop(exc, 0.0 if exc.code == 403 else retry_delay(exc, 0))

    def __call__(self, url, destination):
        # One shared admission gate covers all workers and every retry.
        with self.gate:
            if self.stop_event.is_set():
                raise DownloadStopped()
            delay = (max(0, self.interval - (time.monotonic() - self.last_request))
                     if self.last_request is not None else 0)
            if self.stop_event.wait(delay):
                raise DownloadStopped()
            self.last_request = time.monotonic()
        # Recheck after releasing the gate: a worker may have been descheduled.
        if self.stop_event.is_set():
            raise DownloadStopped()
        if self.transport is not None:
            try:
                return self.transport(url, destination)
            except HTTPError as exc:
                self.stop_for_http(exc)
                raise
        request = Request(url, headers={'User-Agent': 'reviewer-self-ego/download-papers',
                                       'Accept': 'application/pdf'})
        if self.stop_event.is_set():
            raise DownloadStopped()
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            self.stop_for_http(exc)
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
            403: 'access_denied', 404: 'http_not_found', 410: 'http_gone', 406: 'http_not_acceptable',
            429: 'rate_limited', 503: 'service_unavailable',
        }.get(exc.code, 'http_error'))
        result.update(getattr(exc, 'download_http_evidence', {}))
    else:
        result['error_kind'] = 'validation_error' if isinstance(exc, ValueError) else 'network_error'
    return result


def retryable_error(exc):
    """Classify transient failures without treating code bugs as network errors."""
    kind = None
    if isinstance(exc, HTTPError):
        if 500 <= exc.code <= 599 and exc.code != 503:
            kind = 'http_error'
    elif isinstance(exc, MemoryError) or (isinstance(exc, OSError) and exc.errno in (
            errno.EMFILE, errno.ENFILE, errno.ENOMEM, errno.EAGAIN)):
        kind = 'resource_error'
    elif isinstance(exc, RuntimeError) and str(exc).lower() in (
            "can't start new thread", 'cannot start new thread'):
        kind = 'thread_start_error'
    elif isinstance(exc, (URLError, TimeoutError, ConnectionError)) or (
            isinstance(exc, OSError) and exc.errno in (
                errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
                errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETDOWN,
                errno.ENETRESET, errno.ENETUNREACH, errno.EPIPE)):
        kind = 'network_error'
    if kind is None:
        return None
    evidence = {'kind': kind, 'exception_type': type(exc).__name__, 'detail': str(exc)}
    if isinstance(exc, OSError) and exc.errno is not None:
        evidence['errno'] = exc.errno
    if isinstance(exc, HTTPError):
        delay = retry_delay(exc, 0)
        evidence.update(http_status=exc.code, retry_after_seconds=delay,
                        next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat())
        evidence.update(getattr(exc, 'download_http_evidence', {}))
    return evidence


def acquire(target, aid, version, fetch, retries, reusable=None, on_backoff=None, stop_event=None,
            validator=None, finish_validator=None, stop_on_retryable_error=False):
    """Resume only validated files; replace a destination only after validation."""
    validator = validator or validate_pdf
    finish_validator = finish_validator or validator
    if target.is_file():
        try:
            return 'cached', validator(target, aid, version)
        except Exception as exc:
            if isinstance(exc, DownloadStopped) or retryable_error(exc):
                raise
            pass  # Keep the old bytes until a complete replacement is verified.
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f'.v{version["version"]}.',
                                     suffix='.pdf', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        if reusable is not None and reusable.is_file():
            try:
                validator(reusable, aid, version)
                shutil.copyfile(reusable, temporary)
                info = validator(temporary, aid, version)
                temporary.replace(target)
                return 'reused', {**info, 'path': str(target.resolve())}
            except Exception as exc:
                if isinstance(exc, DownloadStopped) or retryable_error(exc):
                    raise
                pass
        # The documented programmatic harvesting host; no host fallback.
        # Always request the explicit original version, never the latest alias.
        url = f'https://export.arxiv.org/pdf/{aid}v{version["version"]}'
        for attempt in range(retries + 1):
            try:
                fetch(url, temporary)
                try:
                    info = finish_validator(temporary, aid, version)
                except Exception as exc:
                    if isinstance(exc, DownloadStopped) or retryable_error(exc):
                        raise
                    raise ValueError(f'Invalid original PDF: {exc}') from exc
                temporary.replace(target)
                return 'downloaded', {**info, 'path': str(target.resolve())}
            except DownloadStopped:
                raise
            except Exception as exc:
                if (stop_on_retryable_error and not isinstance(exc, (HTTPError, ValueError))
                        and retryable_error(exc) is None):
                    raise
                delay = 0.0 if isinstance(exc, HTTPError) and exc.code == 403 else retry_delay(exc, attempt)
                if isinstance(exc, HTTPError) and (exc.code == 403 or exc.code in GLOBAL_BACKOFF_HTTP_STATUSES) and on_backoff:
                    on_backoff(exc, delay)
                if ((stop_event is not None and stop_event.is_set()) or attempt == retries
                        or (isinstance(exc, HTTPError) and exc.code in (400, 401, 403, 404, 410))):
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)

class DownloadStopped(Exception):
    """The global circuit closed before this request was admitted."""


def download_entry(request, root, fetch, retries, reuse_root, reference_roots,
                   pdf_layout, reusable=None, on_backoff=None, stop_event=None,
                   validator=None, finish_validator=None, stop_on_retryable_error=False, on_retryable=None):
    validator = validator or validate_pdf
    conference, paper, version, target = request
    aid, number = paper['arxiv_id'], version['version']
    entry = {'conference': conference, 'arxiv_id': aid, 'version': number,
             'pdf_path': str(target.relative_to(root))}
    if reusable is None and reuse_root is not None:
        reusable = pdf_path(Path(reuse_root), aid, number)
    try:
        candidate = resolve_pdf(root, aid, number, conference if pdf_layout == 'conference' else None,
                                reference_roots)
        if candidate != target:
            # An existing readonly original is authoritative, including bad bytes.
            try:
                evidence = validator(candidate, aid, version)
            except Exception as exc:
                if isinstance(exc, DownloadStopped) or retryable_error(exc):
                    raise
                raise ValueError(f'Invalid readonly original PDF: {exc}') from exc
            status = 'referenced'
            entry.update(pdf_path=str(candidate.resolve()), readonly=True)
        else:
            status, evidence = acquire(target, aid, version, fetch, retries, reusable,
                                       on_backoff=on_backoff, stop_event=stop_event,
                                       validator=validator, finish_validator=finish_validator,
                                       stop_on_retryable_error=stop_on_retryable_error)
        entry.update(status=status, validation=evidence)
    except DownloadStopped:
        return None
    except Exception as exc:
        retryable = retryable_error(exc) if stop_on_retryable_error else None
        if (stop_on_retryable_error and retryable is None
                and not isinstance(exc, (HTTPError, ValueError))):
            raise
        entry.update(failure_evidence(exc))
        if retryable is not None:
            retryable.update(stage='file', arxiv_id=aid, version=number, pdf_path=entry['pdf_path'])
            entry['retryable_failure'] = retryable
            if on_retryable:
                on_retryable(retryable)
    return entry


def concurrent_download(requests, root, receipt, workers, interval, timeout, fetch,
                        retries, reuse_root, reference_roots, pdf_layout,
                        checkpoint, record_backoff, record_entry, validator, finish_validator,
                        stop_on_retryable_error, record_retryable, stop_event):
    """Bounded admissions, one global request gate, and one manifest writer."""
    events = queue.SimpleQueue()
    groups = {}
    for position, request in enumerate(requests, 1):
        _, paper, version, _ = request
        groups.setdefault((paper['arxiv_id'], version['version']), []).append((position, request))
    pending = iter(groups.values())

    def stop(exc, delay):
        stop_event.set()
        events.put(('backoff', exc, delay, datetime.now(timezone.utc)))

    def stop_retryable(evidence):
        stop_event.set()
        events.put(('retryable', evidence))

    gated_fetch = Fetcher(interval, timeout, stop_event=stop_event, transport=fetch,
                          break_on_http_stop=True, on_http_stop=stop)

    def process(group):
        reusable = None
        for position, request in group:
            entry = download_entry(request, root, gated_fetch, retries, reuse_root,
                                   reference_roots, pdf_layout, reusable, stop, stop_event,
                                   validator=validator, finish_validator=finish_validator,
                                   stop_on_retryable_error=stop_on_retryable_error,
                                   on_retryable=lambda evidence: stop_retryable(
                                       {**evidence, 'source_position': position}))
            if entry is not None:
                entry['source_position'] = position
                events.put(('entry', entry))
                if entry['status'] != 'failed':
                    reusable = Path(entry['validation']['path'])

    completed_since_checkpoint, last_checkpoint = 0, time.monotonic()
    def drain_events():
        nonlocal completed_since_checkpoint, last_checkpoint
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'backoff':
                record_backoff(event[1], event[2], event[3])
            elif event[0] == 'retryable':
                record_retryable(event[1])
            else:
                record_entry(event[1])
                completed_since_checkpoint += 1
            if completed_since_checkpoint >= 100 or time.monotonic() - last_checkpoint >= 30:
                checkpoint()
                completed_since_checkpoint, last_checkpoint = 0, time.monotonic()

    try:
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='original-pdf')
    except Exception as exc:
        evidence = retryable_error(exc)
        if evidence is None or evidence['kind'] not in ('resource_error', 'thread_start_error'):
            raise
        record_retryable({**evidence, 'stage': 'worker_pool'})
        return
    active, exhausted, interruption = set(), False, None
    try:
        while active or not exhausted:
            try:
                drain_events()
                if stop_event.is_set():
                    exhausted = True
                    for future in tuple(active):
                        if future.cancel():
                            active.remove(future)
                while not exhausted and len(active) < workers and not stop_event.is_set():
                    group = next(pending, None)
                    if group is None:
                        exhausted = True
                        break
                    active.add(pool.submit(process, group))
                if not active:
                    break
                done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    active.remove(future)
                    future.result()
                drain_events()
            except BaseException as exc:
                if interruption is not None:
                    raise
                evidence = retryable_error(exc)
                stop_event.set()
                exhausted = True
                if evidence and (stop_on_retryable_error or evidence['kind'] in (
                        'resource_error', 'thread_start_error')):
                    stop_retryable({**evidence, 'stage': 'worker_pool'})
                else:
                    interruption = exc
                    receipt['status'] = 'interrupted'
                drain_events()
                checkpoint()  # Preserve completed work before draining accepted calls.
        drain_events()
    finally:
        stop_event.set()
        pool.shutdown(wait=True, cancel_futures=True)
        drain_events()
    if interruption is not None:
        raise interruption
    if receipt.get('access_denied'):
        receipt['status'] = 'access_denied'
    elif receipt.get('last_http_status') in GLOBAL_BACKOFF_HTTP_STATUSES:
        receipt['status'] = 'rate_limited'
    elif receipt.get('retryable_failure'):
        receipt['status'] = 'retryable_failure'
    else:
        receipt['status'] = ('incomplete' if receipt['summary']['failed_file_count']
                             or receipt['summary']['pending_file_count'] else 'complete')


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
             pdf_layout='conference', reference_roots=(), workers=1,
             validation_workers=None, stop_on_retryable_error=False):
    if type(workers) is not int or not 1 <= workers <= 1024:
        raise ValueError('workers must be an integer between 1 and 1024')
    if validation_workers is not None and (
            type(validation_workers) is not int or not 1 <= validation_workers <= 1024):
        raise ValueError('validation_workers must be None or an integer between 1 and 1024')
    if type(stop_on_retryable_error) is not bool:
        raise ValueError('stop_on_retryable_error must be a boolean')
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
               'workers': workers, 'global_request_interval_seconds': interval,
               'validation_workers': validation_workers,
               'stop_on_retryable_error': stop_on_retryable_error,
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
    stop_event = threading.Event()
    if workers == 1:
        fetch = fetch or Fetcher(interval, timeout, stop_event=stop_event)
    last_checkpoint = time.monotonic()
    validation_gate = threading.BoundedSemaphore(validation_workers) if validation_workers is not None else None

    def validator(path, aid, version, finish=False):
        if validation_gate is None:
            return validate_pdf(path, aid, version)
        while finish or not stop_event.is_set():
            if validation_gate.acquire(timeout=0.1):
                try:
                    if not finish and stop_event.is_set():
                        raise DownloadStopped()
                    return validate_pdf(path, aid, version)
                finally:
                    validation_gate.release()
        raise DownloadStopped()

    def finish_validator(path, aid, version):
        # Already downloaded bytes must be validated and saved, even after a circuit stop.
        return validator(path, aid, version, finish=True)

    def checkpoint():
        nonlocal last_checkpoint
        receipt['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
        if workers > 1:
            receipt['files'].sort(key=lambda entry: entry['source_position'])
        atomic_json(manifest, receipt)
        last_checkpoint = time.monotonic()

    def record_backoff(exc, delay, observed_at=None):
        observed_at = observed_at or datetime.now(timezone.utc)
        if exc.code == 403:
            receipt.update(status='access_denied', access_denied=True, last_http_status=403,
                           requires_access_restoration=True, last_http_error=failure_evidence(exc))
            receipt.pop('next_retry_at', None)
            receipt.pop('retry_after_seconds', None)
        elif not receipt.get('access_denied'):
            deadline = observed_at + timedelta(seconds=delay)
            previous = receipt.get('next_retry_at')
            if previous:
                deadline = max(deadline, datetime.fromisoformat(previous))
            receipt.update(next_retry_at=deadline.isoformat(),
                           retry_after_seconds=max(delay, (deadline - observed_at).total_seconds()),
                           last_http_status=exc.code, last_http_error=failure_evidence(exc))
        checkpoint()

    def record_retryable(evidence):
        stop_event.set()
        receipt.setdefault('retryable_failure', {
            **evidence, 'observed_at_utc': datetime.now(timezone.utc).isoformat()})
        if not receipt.get('access_denied'):
            if evidence.get('next_retry_at'):
                receipt['next_retry_at'] = max(evidence['next_retry_at'], receipt.get('next_retry_at', ''))
            if receipt.get('last_http_status') not in GLOBAL_BACKOFF_HTTP_STATUSES:
                receipt['status'] = 'retryable_failure'
        try:
            checkpoint()
        except OSError as exc:
            # Accepted workers may still hold descriptors; retry after draining them.
            if (retryable_error(exc) or {}).get('kind') != 'resource_error':
                raise

    def record_entry(entry):
        receipt['files'].append(entry)
        summary = receipt['summary']
        summary['processed_file_count'] += 1
        summary['pending_file_count'] -= 1
        summary['failed_file_count'] += entry['status'] == 'failed'
        summary['http_unavailable_file_count'] += entry.get('http_status') in (404, 410)
        print(f'{entry["conference"]}/{entry["arxiv_id"]}/v{entry["version"]}.pdf: {entry["status"]}', flush=True)

    with download_lock(root):
        # A restarted process must respect the previous process's server cooldown.
        # Keep its detailed checkpoint intact when no new work can begin yet.
        if manifest.is_file():
            previous = json.loads(manifest.read_text(encoding='utf-8'))
            if previous.get('access_denied') or previous.get('status') == 'access_denied':
                receipt.update(status='access_denied', access_denied=True, requires_access_restoration=True,
                               last_http_status=403, last_http_error=previous.get('last_http_error'),
                               denial_manifest=str(manifest))
                return receipt
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
            if workers > 1:
                receipt['partial_files_order'] = 'source_position; unfinished positions may be absent'
                concurrent_download(requests, root, receipt, workers, interval, timeout, fetch,
                                    retries, reuse_root, reference_roots, pdf_layout,
                                    checkpoint, record_backoff, record_entry, validator, finish_validator,
                                    stop_on_retryable_error, record_retryable, stop_event)
            else:
                verified = {}
                for request in requests:
                    _, paper, version, _ = request
                    identity = (paper['arxiv_id'], version['version'])
                    entry = download_entry(request, root, fetch, retries, reuse_root, reference_roots,
                                           pdf_layout, verified.get(identity), record_backoff,
                                           validator=validator, finish_validator=finish_validator,
                                           stop_on_retryable_error=stop_on_retryable_error,
                                           on_retryable=record_retryable)
                    if entry is None:
                        receipt['status'] = 'interrupted'
                        break
                    if entry['status'] != 'failed':
                        verified[identity] = Path(entry['validation']['path'])
                        if entry['status'] == 'downloaded':
                            for key in ('next_retry_at', 'retry_after_seconds', 'last_http_status', 'last_http_error'):
                                receipt.pop(key, None)
                    record_entry(entry)
                    if entry.get('http_status') == 403:
                        receipt['status'] = 'access_denied'
                        break
                    if entry.get('http_status') in GLOBAL_BACKOFF_HTTP_STATUSES:
                        receipt['status'] = 'rate_limited'
                        break
                    if receipt.get('retryable_failure'):
                        receipt['status'] = 'retryable_failure'
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
    directory = Path(__file__).resolve().parent
    project = (directory.parent.parent if directory.name == 'data'
               and directory.parent.name == 'utility' else directory.parent)
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
    parser.add_argument('--workers', type=int, default=1, help='Concurrent original-version groups (1-1024); request interval is global.')
    parser.add_argument('--validation-workers', type=int, default=None,
                        help='Maximum simultaneous PDF validators; default follows download concurrency.')
    parser.add_argument('--stop-on-retryable-error', action='store_true',
                        help='Drain and stop after exhausted transient file failures for controller recovery.')
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
                          reference_roots=args.reference_pdf_root, workers=args.workers,
                          validation_workers=args.validation_workers,
                          stop_on_retryable_error=args.stop_on_retryable_error)
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

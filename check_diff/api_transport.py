"""Streaming Responses API transport with verified manuscript inputs and audit artifacts."""
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
import base64
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class Connection:
    base_url: str
    api_key: str = field(repr=False)


class APIError(RuntimeError):
    def __init__(self, message, metadata=None, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable
        self.metadata = {**(metadata or {}), 'retryable': retryable}


TRANSIENT_ERROR_CODES = frozenset({
    'server_error', 'internal_error', 'internal_server_error', 'server_is_overloaded',
    'rate_limit_exceeded', 'rate_limit_error', 'slow_down', 'request_timeout',
    'timeout', 'temporarily_unavailable', 'service_unavailable',
})
PERMANENT_ERROR_CODES = frozenset({
    'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active',
    'invalid_api_key', 'authentication_error', 'permission_denied',
    'invalid_request_error', 'context_length_exceeded', 'content_filter',
    'safety_violation', 'max_output_tokens', 'model_not_found', 'unsupported_parameter',
})


def error_details(value):
    """Keep structured codes, not error-message guesses, for retry decisions."""
    if not isinstance(value, dict):
        return {}
    error = value.get('error', value)
    if not isinstance(error, dict):
        return {}
    return {f'error_{key}': error[key] for key in ('code', 'type')
            if isinstance(error.get(key), str)}


def retry_after_seconds(headers):
    """Parse server minimum waits without shortening long valid delays."""
    delays = []
    headers = {key.lower(): value for key, value in headers.items()}
    for key, divisor in (('retry-after-ms', 1000), ('retry-after', 1)):
        value = headers.get(key)
        if value is None:
            continue
        try:
            delay = float(value) / divisor
        except (TypeError, ValueError, OverflowError):
            if key != 'retry-after':
                continue
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delay = max(0, date.timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                continue
        if math.isfinite(delay) and delay >= 0:
            delays.append(delay)
    return max(delays) if delays else None


def _connection_error(exc, connection):
    reason = getattr(exc, 'reason', exc)
    return APIError(f'Responses API transport failed: {redact(str(exc), connection)}',
                    retryable=not isinstance(reason, ssl.SSLCertVerificationError))


def _response_lines(response, connection):
    # Catch socket failures here, separately from local audit-file writes.
    iterator = iter(response)
    while True:
        try:
            line = next(iterator)
        except StopIteration:
            return
        except (OSError, ValueError, http.client.HTTPException) as exc:
            raise _connection_error(exc, connection) from None
        yield line


_ACTIVE_LOCK = threading.Lock()
_ACTIVE: set[http.client.HTTPResponse] = set()
MAX_REQUEST_BYTES = 90 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _open(request, timeout):
    # Credentials and manuscript bytes must only reach the configured endpoint.
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def load_connection(path=None):
    """Read credentials without copying them into command lines or run records."""
    try:
        source = Path(path).read_text(encoding='utf-8') if path else os.environ.get('CHECK_DIFF_API_CONFIG_JSON', '')
        data = json.loads(source)
    except (OSError, ValueError):
        raise ValueError('Cannot read API connection JSON; use --connection or CHECK_DIFF_API_CONFIG_JSON') from None
    if not isinstance(data, dict):
        raise ValueError('API connection JSON must be an object')
    base_url = data.get('base_url', '')
    if not isinstance(base_url, str):
        raise ValueError('API base_url must be a string')
    base_url = base_url.rstrip('/')
    api_key = data.get('api_key', '')
    url = urllib.parse.urlsplit(base_url)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.query or url.fragment or not isinstance(api_key, str) or not api_key.strip()):
        raise ValueError('API connection requires an HTTPS base_url and a nonempty api_key')
    return Connection(base_url, api_key)


def redact(text, connection):
    return str(text).replace(connection.api_key, '[REDACTED]')


def atomic_write(path, value, *, text=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.stem}-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value if text else json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def build_payload(prompt, pdfs, schema, model, effort, uploaded_files=None):
    content = [{'type': 'input_text', 'text': prompt}]
    if uploaded_files is not None:
        if len(uploaded_files) != 2 or len(pdfs) != 2:
            raise ValueError('Uploaded-file inputs require exactly two files')
        for uploaded in uploaded_files:
            content.append({'type': 'input_file', 'file_id': uploaded['file_id']})
    else:
        for pdf in pdfs:
            content.append({'type': 'input_file', 'filename': pdf.name,
                            'detail': 'high', 'file_data': 'data:application/pdf;base64,' + base64.b64encode(pdf.read_bytes()).decode('ascii')})
    return {'model': model, 'reasoning': {'effort': effort}, 'stream': True, 'store': False,
            'input': [{'role': 'user', 'content': content}],
            'text': {'format': {'type': 'json_schema', 'name': 'paper_pair_comparison',
                                'strict': True, 'schema': schema}}}


def _abort(response):
    """Interrupt a blocked urllib socket read before closing the response."""
    try:
        sock = response.fp.raw._sock
        sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):
        pass
    try:
        response.close()
    except (OSError, ValueError):
        pass


def cancel_active_requests():
    with _ACTIVE_LOCK:
        responses = list(_ACTIVE)
    for response in responses:
        _abort(response)


def _events(response, archive, connection, stop, deadline):
    lines: list[str] = []
    event_name = ''
    for raw in _response_lines(response, connection):
        if stop.is_set():
            raise APIError('Interrupted')
        if time.monotonic() >= deadline:
            raise APIError('Responses API attempt timed out', retryable=True)
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise APIError('Responses API stream contains invalid UTF-8', retryable=True) from None
        archive.write(redact(text, connection))
        line = text.rstrip('\r\n')
        if not line:
            if lines:
                yield event_name, '\n'.join(lines)
            lines, event_name = [], ''
        elif line.startswith('data:'):
            lines.append(line[5:].lstrip(' '))
        elif line.startswith('event:'):
            event_name = line[6:].lstrip(' ')
        elif not line.startswith((':', 'id:', 'retry:')):
            raise APIError('Responses API returned a non-SSE stream', retryable=True)
    if lines:
        raise APIError('Responses API stream ended inside an event', retryable=True)


def _output_text(response):
    parts = []
    for item in response.get('output', []):
        if item.get('type') == 'message':
            for content in item.get('content', []):
                if content.get('type') == 'refusal':
                    raise APIError('Responses API refused the comparison')
                if content.get('type') == 'output_text':
                    parts.append(content.get('text', ''))
    return ''.join(parts) or response.get('output_text', '')


def request_response(connection, payload, timeout, stop, audit_prefix):
    """Require an explicit completed event; persist partial streams on failure."""
    prefix = Path(audit_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {'response_id': None, 'returned_model': None, 'returned_effort': None, 'usage': None,
                'artifacts': {}}
    started = time.monotonic()
    deadline = started + timeout
    response = None
    temporary = None
    timer = None
    timed_out = threading.Event()
    terminal = None
    deltas = []

    def expire():
        timed_out.set()
        if response is not None:
            _abort(response)

    try:
        if stop.is_set():
            raise APIError('Interrupted')
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        metadata.update(request_body_bytes=len(body), request_body_limit_bytes=MAX_REQUEST_BYTES)
        if len(body) > MAX_REQUEST_BYTES:
            raise APIError(f'Serialized request body is {len(body)} bytes; exceeds the {MAX_REQUEST_BYTES}-byte (90 MiB) limit before network submission', metadata)
        request = urllib.request.Request(connection.base_url + '/responses',
            data=body, method='POST',
            headers={'Authorization': 'Bearer ' + connection.api_key,
                     'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                     'User-Agent': 'Reviewer-Self-Ego/1.3'})
        timer = threading.Timer(max(0.001, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        # Large concurrent uploads may delay headers; retain the full remaining budget.
        # urllib exposes cancellation only after headers arrive, when a response exists.
        try:
            response = _open(request, timeout=max(0.001, deadline - time.monotonic()))
        except urllib.error.HTTPError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise _connection_error(exc, connection) from None
        metadata['retry_after_seconds'] = retry_after_seconds(getattr(response, 'headers', {}) or {})
        with _ACTIVE_LOCK:
            _ACTIVE.add(response)
        if stop.is_set() or timed_out.is_set():
            raise APIError('Interrupted' if stop.is_set() else 'Responses API attempt timed out',
                           retryable=not stop.is_set())
        try:
            response.fp.raw._sock.settimeout(max(0.001, deadline - time.monotonic()))
        except AttributeError:
            pass  # In-memory streams used by the transport tests have no socket.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=prefix.parent,
                                         prefix=f'.{prefix.name}-', suffix='.tmp', delete=False) as archive:
            temporary = Path(archive.name)
            metadata['artifacts']['stream'] = prefix.name + '.sse'
            for event_name, data in _events(response, archive, connection, stop, deadline):
                if data == '[DONE]':
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    raise APIError('Responses API stream contains malformed event JSON', retryable=True) from None
                if not isinstance(event, dict):
                    raise APIError('Responses API event must be an object', retryable=True)
                kind = event.get('type', event_name)
                current = event.get('response')
                if isinstance(current, dict):
                    current = json.loads(redact(json.dumps(current), connection))
                    metadata.update(response_id=current.get('id') or metadata['response_id'],
                                    returned_model=current.get('model') or metadata['returned_model'],
                                    returned_effort=(current.get('reasoning') or {}).get('effort') or metadata['returned_effort'],
                                    usage=current.get('usage') or metadata['usage'])
                if kind == 'response.output_text.delta':
                    deltas.append(event.get('delta', ''))
                elif kind in ('error', 'response.failed', 'response.incomplete'):
                    atomic_write(Path(str(prefix) + '.response.json'), json.loads(redact(json.dumps(event), connection)))
                    metadata['artifacts']['response'] = prefix.name + '.response.json'
                    details = error_details(current if isinstance(current, dict) else event)
                    incomplete = (current or {}).get('incomplete_details') if isinstance(current, dict) else None
                    if isinstance(incomplete, dict) and isinstance(incomplete.get('reason'), str):
                        details['incomplete_reason'] = incomplete['reason']
                    details = json.loads(redact(json.dumps(details), connection))
                    metadata.update(details)
                    codes = set(details.values())
                    raise APIError(f'Responses API terminated with {kind}', metadata,
                                   retryable=bool(codes & TRANSIENT_ERROR_CODES) and not bool(codes & PERMANENT_ERROR_CODES))
                elif kind == 'response.completed':
                    if terminal is not None or not isinstance(current, dict) or current.get('status') != 'completed' or current.get('error'):
                        raise APIError('Responses API completion event is invalid', metadata, retryable=True)
                    if current.get('incomplete_details'):
                        atomic_write(Path(str(prefix) + '.response.json'), current)
                        metadata['artifacts']['response'] = prefix.name + '.response.json'
                        metadata['incomplete_details'] = current['incomplete_details']
                        raise APIError('Responses API completion contains incomplete details', metadata)
                    # Require identity from the terminal response, not an earlier event.
                    metadata.update(returned_model=current.get('model'),
                                    returned_effort=(current.get('reasoning') or {}).get('effort'))
                    terminal = current
            if stop.is_set() or timed_out.is_set():
                raise APIError('Interrupted' if stop.is_set() else 'Responses API attempt timed out', metadata,
                               retryable=not stop.is_set())
            if terminal is None:
                raise APIError('Responses API stream ended without response.completed', metadata, retryable=True)
        response_text = redact(json.dumps(terminal, ensure_ascii=False), connection)
        atomic_write(Path(str(prefix) + '.response.json'), json.loads(response_text))
        metadata['artifacts']['response'] = prefix.name + '.response.json'
        output = _output_text(terminal)
        streamed_output = ''.join(deltas)
        output = output or streamed_output
        if not output or (deltas and streamed_output != output):
            raise APIError('Responses API completed output is empty or differs from streamed text', metadata, retryable=True)
        output = redact(output, connection)
        atomic_write(Path(str(prefix) + '.output.txt'), output, text=True)
        metadata['artifacts']['output'] = prefix.name + '.output.txt'
        try:
            result = json.loads(output)
        except ValueError:
            raise APIError('Responses API output is not valid JSON', metadata, retryable=True) from None
        return result, metadata
    except urllib.error.HTTPError as exc:
        metadata['http_status'] = exc.code
        metadata['retry_after_seconds'] = retry_after_seconds(exc.headers or {})
        try:
            detail = redact(exc.read(8192).decode('utf-8', errors='replace'), connection)
            try:
                metadata.update(error_details(json.loads(detail)))
            except ValueError:
                pass
            atomic_write(Path(str(prefix) + '.error.txt'), detail, text=True)
            metadata['artifacts']['http_error'] = prefix.name + '.error.txt'
        except (OSError, ValueError) as audit_error:
            raise APIError(f'Cannot preserve HTTP {exc.code} error evidence: {redact(str(audit_error), connection)}',
                           metadata) from None
        finally:
            try:
                exc.close()
            except (OSError, ValueError):
                pass
        codes = {metadata.get('error_code'), metadata.get('error_type')}
        retryable = (exc.code in (408, 409, 429) or 500 <= exc.code < 600) and not bool(codes & PERMANENT_ERROR_CODES)
        raise APIError(f'Responses API returned HTTP {exc.code}', metadata, retryable=retryable) from None
    except APIError as exc:
        exc.metadata = {**metadata, **exc.metadata}
        raise
    except Exception as exc:
        reason = 'Interrupted' if stop.is_set() else 'Responses API attempt timed out' if timed_out.is_set() else f'Responses API transport failed: {redact(str(exc), connection)}'
        raise APIError(reason, metadata) from None
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(response)
            try:
                response.close()
            except (OSError, ValueError):
                pass
        if temporary is not None:
            temporary.replace(Path(str(prefix) + '.sse'))

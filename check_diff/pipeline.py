"""Compare one fixed v1.3 unit with concurrent uploads and pair-ready dispatch."""
import argparse
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import re
import signal
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request
import uuid

if __package__:
    from . import api_transport, runner
else:
    import api_transport
    import runner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utility.prepare_v13_pilot import materialize_plan


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def verified_upload_requests(plan, evidence):
    """Bind every upload to the selected unit and its freshly verified PDF."""
    aid = plan['records'][0]['arxiv_id']
    expected = {(aid, version) for version in plan['selected_units'][0]['selected_version_ids']}
    requests = plan['requested_pdfs']
    actual = [(item['arxiv_id'], item['version']) for item in requests]
    if set(actual) != expected or len(actual) != len(expected) or evidence.get('status') != 'verified':
        raise ValueError('Upload requests do not match the verified selected unit')
    verified = evidence['verified_pdfs']
    if set(verified) != {f'{paper}v{version}' for paper, version in expected}:
        raise ValueError('Verified PDF inventory does not match the selected versions')
    bound = []
    for item in requests:
        info = verified[f'{aid}v{item["version"]}']
        if Path(item['pdf_path']).resolve() != Path(info['path']).resolve():
            raise ValueError('Upload path differs from the verified original PDF')
        bound.append({**item, 'pdf_path': info['path'], 'expected_sha256': info['sha256'],
                      'expected_bytes': info['bytes']})
    return bound


def run_ready_pairs(requests, pairs, upload_one, prepare_pair, compare_pair, stop, workers,
                    on_uploads_finished=None, upload_workers=None):
    """Upload a unit concurrently and dispatch pairs as their endpoints complete.

    The executor settles every submitted comparison before returning or raising,
    so the caller can safely delete shared uploaded files after this function.
    """
    requested = {(item['arxiv_id'], item['version']) for item in requests}
    identities = [(p.arxiv_id, p.before, p.after) for p in pairs]
    if len(requested) != len(requests) or len(set(identities)) != len(pairs):
        raise ValueError('Duplicate requested versions or pair identities')
    if any((p.arxiv_id, version) not in requested for p in pairs for version in (p.before, p.after)):
        raise ValueError('A pair endpoint is missing from the upload plan')
    upload_workers = len(requests) if upload_workers is None else upload_workers
    if type(upload_workers) is not int or upload_workers < 1:
        raise ValueError('Upload concurrency must be a positive integer')
    ready, submitted = {}, set()
    counts = dict(done=0, failed=0, stopped=0, skipped=0)
    def upload_if_active(item):
        return None if stop.is_set() else upload_one(item)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = []
        try:
            with ThreadPoolExecutor(max_workers=upload_workers) as uploads:
                futures = {}
                for item in requests:
                    if stop.is_set():
                        break
                    futures[uploads.submit(upload_if_active, item)] = item
                for future in as_completed(futures):
                    entry = future.result()
                    if stop.is_set():
                        continue
                    item = futures[future]
                    key = (item['arxiv_id'], item['version'])
                    if (not isinstance(entry, dict)
                            or (entry.get('arxiv_id'), entry.get('version')) != key
                            or entry.get('status') != 'processed'):
                        raise ValueError('Upload did not produce the requested processed version')
                    ready[key] = entry
                    for pair, identity in zip(pairs, identities):
                        endpoints = [(pair.arxiv_id, version) for version in (pair.before, pair.after)]
                        if stop.is_set():
                            break
                        if identity in submitted or not all(endpoint in ready for endpoint in endpoints):
                            continue
                        args = prepare_pair(pair, [ready[endpoint] for endpoint in endpoints])
                        if stop.is_set():
                            break
                        jobs.append(pool.submit(compare_pair, args, pair))
                        submitted.add(identity)
        finally:
            if on_uploads_finished:
                on_uploads_finished()
        for job in as_completed(jobs):
            counts[job.result()] += 1
    if not stop.is_set() and len(submitted) != len(pairs):
        raise RuntimeError('Not all planned pairs were dispatched')
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Prepared one-unit input directory')
    parser.add_argument('--output', required=True, type=Path, help='New run directory')
    parser.add_argument('--connection', type=Path, help='Private API configuration; or CHECK_DIFF_API_CONFIG_JSON')
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--effort', default='max', choices=('low', 'medium', 'high', 'xhigh', 'max'))
    parser.add_argument('--workers', type=int, default=50, help='Comparison concurrency cap')
    parser.add_argument('--upload-workers', type=int, help='Upload cap; default: all selected versions in this unit')
    parser.add_argument('--timeout', type=float, default=1800, help='Per-model-attempt timeout in seconds')
    parser.add_argument('--retries', type=int, default=1, help='Additional transient-error model attempts')
    parser.add_argument('--upload-only', action='store_true', help='Test file upload/processing without model calls')
    args = parser.parse_args(argv)
    if (args.workers < 1 or (args.upload_workers is not None and args.upload_workers < 1)
            or not math.isfinite(args.timeout) or args.timeout <= 0 or args.retries < 0):
        parser.error('Concurrency and timeout must be positive; retries must be nonnegative')
    input_path, root = args.input.resolve(), args.output.resolve()
    if root == input_path or root.is_relative_to(input_path):
        parser.error('The run directory must be separate from the prepared inputs')
    if root.exists() and any(root.iterdir()):
        parser.error('The run directory is not empty; preserve it and choose a fresh --output')
    plan_path = input_path / 'selection_manifest.json'
    plan = json.loads(plan_path.read_text())
    if plan['summary']['paper_count'] != 1 or len(plan['records']) != 1:
        parser.error('This pilot entry point requires exactly one prepared paper unit')
    connection = api_transport.load_connection(args.connection)
    materialize_plan(plan_path)
    requests = verified_upload_requests(plan, json.loads((input_path / 'materialization_manifest.json').read_text()))
    root.mkdir(parents=True, exist_ok=True)
    write = api_transport.atomic_write
    upload_only = args.upload_only
    state = {'run_id': root.name, 'input_directory': str(input_path),
             'arxiv_id': plan['records'][0]['arxiv_id'], 'expected_units': 1,
             'expected_pairs': plan['summary']['pair_count'], 'workers': args.workers,
             'upload_workers': args.upload_workers or len(plan['requested_pdfs']),
             'model': args.model, 'reasoning_effort': args.effort}
    manifest = {**state, 'status': 'uploading', 'started_at_utc': now(),
                'selection_manifest_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
                'input_summary': plan['summary'], 'sample_selection': plan['sample_selection'],
                'input_mode': 'uploaded_original_pdf_files', 'backend': 'responses',
                'source_release': 'v1.3', 'uploads': [], 'cleanup': [],
                'execution_scope': 'upload_only' if upload_only else 'paper_pair_comparison',
                'expected_pairs': 0 if upload_only else state['expected_pairs'],
                'planned_comparison_count': state['expected_pairs'],
                'model_calls_permitted': not upload_only}
    started = time.monotonic()
    output = root / 'output'
    output.mkdir(exist_ok=True)
    stop_monitor = threading.Event()
    manifest_lock = threading.RLock()

    def snapshot():
        with manifest_lock:
            counts = {'completed': 0, 'failed': 0, 'running': 0, 'stopped': 0, 'pending': manifest['expected_pairs']}
            for p in output.glob('*/*.execution.json'):
                if not re.fullmatch(r'diff_v\d+_v\d+\.execution\.json', p.name):
                    continue
                d = json.loads(p.read_text())
                counts[d['status']] = counts.get(d['status'], 0) + 1
                counts['pending'] -= 1
            manifest.update(counts=counts, wall_time_seconds=round(time.monotonic() - started, 6), updated_at_utc=now())
            write(root / 'run_manifest.json', manifest)
            print(json.dumps({'event': 'progress', 'status': manifest['status'],
                              'wall_seconds': manifest['wall_time_seconds'], **counts}), flush=True)

    def file_request(method, suffix, data=None, content_type=None):
        headers = {'Authorization': 'Bearer ' + connection.api_key,
                   'User-Agent': 'Reviewer-Self-Ego/1.3'}
        if content_type:
            headers['Content-Type'] = content_type
        request = urllib.request.Request(connection.base_url + suffix, data=data, headers=headers, method=method)
        try:
            with api_transport._open(request, 120) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = api_transport.redact(exc.read(4000).decode(errors='replace'), connection)
            raise RuntimeError(f'File service returned HTTP {exc.code}: {detail}') from None

    def monitor():
        while not stop_monitor.wait(30):
            snapshot()

    code = 1
    created = []
    thread = None
    model_started = None
    pairs = runner.load_pairs(input_path)
    assert [(p.before, p.after) for p in pairs] == [
        (edge['from_version'], edge['to_version']) for edge in plan['selected_units'][0]['selected_transitions']]
    settings = dict(backend='responses', input_mode='file_ids', model=state['model'],
                    effort=state['reasoning_effort'], workers=state['workers'], start_interval=0,
                    timeout=args.timeout, retries=args.retries, retry_base_delay=2, retry_max_delay=60, overwrite=False)
    manifest.update(scheduling_policy='dispatch_when_both_uploaded_endpoints_are_processed',
                    phase_durations_overlap=not upload_only, runner_settings=settings, dispatches=[],
                    upload_policy='all_selected_versions_of_unit_concurrently',
                    upload_workers=state.get('upload_workers', len(plan['requested_pdfs'])))
    runner.STOP.clear()
    runner.START_GATE = runner.RequestStartGate(0)

    def interrupted(number, frame):
        runner.request_stop(number, frame)
        raise KeyboardInterrupt('User interrupted the single-unit test')

    previous_signals = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}

    def upload_one(item):
        if runner.STOP.is_set():
            raise RuntimeError('Upload interrupted before transmission')
        pdf = Path(item['pdf_path'])
        data = pdf.read_bytes()
        if len(data) != item['expected_bytes'] or hashlib.sha256(data).hexdigest() != item['expected_sha256']:
            raise ValueError('Original PDF bytes changed after input verification')
        filename = 'v' + str(item['version']) + '.pdf'
        boundary = 'reviewer-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nuser_data\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                'Content-Type: application/pdf\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        upload_started = time.monotonic()
        upload_started_at = now()
        status, result = file_request('POST', '/files', body, 'multipart/form-data; boundary=' + boundary)
        file_id = result.get('id')
        if isinstance(file_id, str) and re.fullmatch(r'file-[A-Za-z0-9_-]+', file_id):
            with manifest_lock:
                created.append(file_id)
        else:
            raise ValueError('Upload returned no valid file ID')
        assert status in (200, 201) and result.get('bytes') == len(data), 'Uploaded byte count mismatch'
        assert result.get('filename') == filename, 'Uploaded filename mismatch'
        deadline = time.monotonic() + 300
        while result.get('status') != 'processed':
            if result.get('status') in ('error', 'failed') or time.monotonic() > deadline:
                raise RuntimeError('Uploaded PDF was not processed before the deadline')
            if runner.STOP.wait(2):
                raise RuntimeError('Upload processing interrupted')
            _, result = file_request('GET', '/files/' + urllib.parse.quote(file_id, safe=''))
        entry = {'arxiv_id': item['arxiv_id'], 'version': item['version'], 'filename': filename,
                 'file_id': file_id, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                 'status': 'processed', 'uploaded_at_utc': now(),
                 'upload_started_at_utc': upload_started_at,
                 'upload_seconds': round(time.monotonic() - upload_started, 6)}
        with manifest_lock:
            manifest['uploads'].append(entry)
            write(root / 'uploaded_inputs.json', {'schema_version': 'check-diff-uploaded-pdfs-1',
                  'base_url': connection.base_url, 'files': manifest['uploads']})
        snapshot()
        return entry

    def prepare_pair(pair, entries):
        nonlocal model_started
        if upload_only:
            raise RuntimeError('Comparison dispatch is forbidden in an upload-only test')
        uploaded = root / 'uploaded_pair_inputs' / f'{pair.arxiv_id}__v{pair.before}_v{pair.after}.json'
        if uploaded.exists():
            raise RuntimeError('A fixed pair upload manifest already exists; use a fresh run')
        write(uploaded, {'schema_version': 'check-diff-uploaded-pdfs-1',
                         'base_url': connection.base_url, 'files': entries})
        with manifest_lock:
            if model_started is None:
                model_started = time.monotonic()
                manifest['model_phase_started_at_utc'] = now()
            manifest['status'] = 'uploading_and_comparing'
            manifest['dispatches'].append({
                'pair_id': f'{pair.arxiv_id}__v{pair.before}_to_v{pair.after}',
                'ready_at_utc': max(entry['uploaded_at_utc'] for entry in entries),
                'prepared_at_utc': now(), 'uploaded_inputs_manifest': str(uploaded),
                'uploaded_inputs_manifest_sha256': api_transport.file_sha256(uploaded)})
        snapshot()
        return SimpleNamespace(**settings, output=output, uploaded_inputs=uploaded, api_connection=connection)

    def uploads_finished():
        with manifest_lock:
            complete = len(manifest['uploads']) == len(plan['requested_pdfs'])
            manifest.update(upload_phase_ended_at_utc=now(), uploads_complete=complete,
                            upload_wall_seconds=round(time.monotonic() - started, 6),
                            status='stopping' if runner.STOP.is_set() else 'uploads_incomplete' if not complete else 'uploads_processed' if upload_only else 'running')
            if complete:
                manifest['uploads_finished_at_utc'] = manifest['upload_phase_ended_at_utc']
        snapshot()

    try:
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        counts = run_ready_pairs(requests, [] if upload_only else pairs, upload_one, prepare_pair,
                                 runner.run_pair, runner.STOP, state['workers'], uploads_finished,
                                 upload_workers=manifest['upload_workers'])
        successful = (len(manifest['uploads']) == len(plan['requested_pdfs'])
                      and counts['done'] == manifest['expected_pairs'])
        code = 130 if runner.STOP.is_set() else 0 if successful else 1
    except Exception as exc:
        manifest['error'] = api_transport.redact(str(exc), connection)
        print(json.dumps({'event': 'error', 'error': manifest['error']}), flush=True)
    except KeyboardInterrupt:
        code = 130
        manifest['error'] = 'Single-unit test interrupted'
    finally:
        stop_monitor.set()
        if thread:
            thread.join()
        if model_started is not None:
            manifest['model_wall_seconds'] = round(time.monotonic() - model_started, 6)
        manifest['status'] = 'cleaning_uploaded_files'
        snapshot()
        for file_id in created:
            entry = {'file_id': file_id, 'deleted': False}
            try:
                status, result = file_request('DELETE', '/files/' + urllib.parse.quote(file_id, safe=''))
                entry.update(http_status=status, deleted=result.get('deleted') is True and result.get('id') == file_id)
            except Exception as exc:
                entry['error'] = api_transport.redact(str(exc), connection)
            manifest['cleanup'].append(entry)
            write(root / 'run_manifest.json', manifest)
        if code == 0 and not all(x['deleted'] for x in manifest['cleanup']):
            code = 1
        manifest.update(status='completed' if code == 0 else 'stopped' if code == 130 else 'completed_with_errors',
                        ended_at_utc=now(), runner_exit_code=code,
                        known_uploaded_files_cleanup_complete=all(x['deleted'] for x in manifest['cleanup']),
                        uploaded_files_cleanup_complete=(len(created) == len(plan['requested_pdfs'])
                                                         and all(x['deleted'] for x in manifest['cleanup'])))
        snapshot()
        for sig, handler in previous_signals.items():
            signal.signal(sig, handler)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read the root JSON, download frozen versions, and build adjacent PDF pairs."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
QUOTAS = {'ICLR2026': 8, 'ICML2026': 5, 'ACL2026': 2}


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


def select_records(source, ids, seed):
    by_id = {r['arxiv_id']: r for r in source['records']}
    if len(by_id) != len(source['records']):
        raise ValueError('Duplicate arXiv IDs in source')
    if ids:
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate requested IDs')
        records = [by_id[aid] for aid in ids]
    else:
        rng = random.Random(seed)
        records = []
        for venue, count in QUOTAS.items():
            pool = sorted((r for r in by_id.values() if r['venue'] == venue),
                          key=lambda r: r['arxiv_id'])
            records.extend(sorted(rng.sample(pool, count), key=lambda r: r['arxiv_id']))
    for paper in records:
        aid, history = paper['arxiv_id'], paper['version_history']
        if not re.fullmatch(r'\d{4}\.\d{4,5}', aid):
            raise ValueError(f'Unsupported arXiv ID: {aid}')
        versions = history['versions']
        if (not history['complete_history'] or history['validation_errors'] or
                [v['version'] for v in versions] != list(range(1, history['frozen_N'] + 1)) or
                len(versions) != paper['raw_version_count'] or len(versions) < 2):
            raise ValueError(f'Incomplete version history: {aid}')
        for version in versions:
            expected = f'https://arxiv.org/pdf/{aid}v{version["version"]}'
            if version['pdf_url'] not in (expected, expected + '.pdf'):
                raise ValueError(f'Unexpected PDF URL: {version["pdf_url"]}')
    return records


def download_pdf(version, aid, cache, delay):
    number = version['version']
    key = f'{aid}v{number}'
    path = cache / f'v{number}.pdf'
    if path.exists():
        try:
            return path, validate_pdf(path, aid, number)
        except Exception:
            pass  # Fetch invalid cached files again.
    part = path.with_suffix('.pdf.part')
    headers = path.with_suffix('.headers.txt')
    command = ['curl', '--fail', '--location', '--silent', '--show-error',
               '--connect-timeout', '20', '--max-time', '150', '--retry', '2',
               '--retry-delay', '5', '--dump-header', str(headers), '--output', str(part),
               '--write-out', '%{url_effective}', version['pdf_url']]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 92:
            result = subprocess.run(command[:1] + ['--http1.1'] + command[1:],
                                    capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        info = validate_pdf(part, aid, number)
        names = re.findall(r'filename="?([^"\r\n;]+)', headers.read_text(), re.I)
        if names and names[-1] != key + '.pdf':
            raise ValueError(f'Wrong response filename: {names[-1]}')
        effective = result.stdout.strip()
        if not re.search(rf'/{re.escape(key)}(?:\.pdf)?$', effective):
            raise ValueError(f'Redirect lost the requested version: {effective}')
        part.replace(path)
        return path, {**info, 'effective_url': effective,
                      'response_filename': names[-1] if names else None,
                      'downloaded_at_utc': datetime.now(timezone.utc).isoformat()}
    finally:
        part.unlink(missing_ok=True)
        time.sleep(delay)


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


def prepare(args):
    source_path, out = args.source.resolve(), args.output.resolve()
    raw = source_path.read_bytes()
    records = select_records(json.loads(raw), args.arxiv_ids, args.seed)
    expected_pdfs = sum(len(r['version_history']['versions']) for r in records)
    expected_pairs = expected_pdfs - len(records)
    print(f'{len(records)} papers, {expected_pdfs} PDFs, {expected_pairs} adjacent pairs', flush=True)
    if args.dry_run:
        for r in records:
            print(f'{r["arxiv_id"]}\t{r["venue"]}\t{r["raw_version_count"]}\t{r["title"]}')
        return 0
    out.mkdir(parents=True, exist_ok=True)
    selection = {'source_file': str(source_path), 'source_sha256': hashlib.sha256(raw).hexdigest(),
                 'sample_selection': {'method': 'explicit_ids' if args.arxiv_ids else 'stratified_random',
                                      'random_seed': args.seed,
                                      'venue_quotas': None if args.arxiv_ids else QUOTAS},
                 'scope': 'All versions in the source JSON (v1..frozen_N).', 'records': records}
    selection_path = out / 'sample_metadata.json'
    if selection_path.exists():
        previous = json.loads(selection_path.read_text())
        if (previous['source_sha256'] != selection['source_sha256'] or
                [r['arxiv_id'] for r in previous['records']] != [r['arxiv_id'] for r in records]):
            raise ValueError('Output contains a different sample; choose another --output directory')
    write_json(selection_path, selection)
    manifest_path = out / 'download_manifest.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest = {'downloads': {}, 'scope': selection['scope']}
    pairs, rows = [], []
    for paper in records:
        aid = paper['arxiv_id']
        folder, cache = out / aid, out / '.pdf_cache' / aid
        folder.mkdir(exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'metadata.json', paper)
        versions = paper['version_history']['versions']
        for version in versions:
            number, url = version['version'], version['pdf_url']
            key = f'{aid}v{number}'
            entry = {**old.get('downloads', {}).get(key, {}), 'arxiv_id': aid,
                     'version': number, 'requested_url': url, 'submitted_at': version['submitted_at']}
            try:
                path, info = download_pdf(version, aid, cache, args.delay)
                entry.update(info, status='verified', local_path=str(path.relative_to(out)))
                entry.pop('error', None)
                print(f'[{len(manifest["downloads"])+1}/{expected_pdfs}] OK {key}', flush=True)
            except Exception as exc:
                entry.update(status='failed', error=str(exc))
                print(f'FAILED {key}: {exc}', flush=True)
            manifest['downloads'][key] = entry
            write_json(manifest_path, manifest)
        paper_pair_count = 0
        for before, after in zip(versions, versions[1:]):
            b, a = before['version'], after['version']
            endpoints = [manifest['downloads'][f'{aid}v{v}'] for v in (b, a)]
            if any(e['status'] != 'verified' for e in endpoints):
                continue
            pair_folder = folder / f'v{b}_v{a}'
            pair_folder.mkdir(exist_ok=True)
            pair = {'pair_id': f'{aid}__v{b}_to_v{a}', 'arxiv_id': aid,
                    'title': paper['title'], 'venue': paper['venue'],
                    'pair_type': 'adjacent_original_versions', 'path_base': 'input_data',
                    'paper_metadata_path': f'{aid}/metadata.json'}
            for side, version, entry in zip(('before', 'after'), (before, after), endpoints):
                target = pair_folder / f'v{version["version"]}.pdf'
                materialize(out / entry['local_path'], target, entry['sha256'])
                pair[side] = {**version, 'pdf_path': str(target.relative_to(out)),
                              'sha256': entry['sha256'], 'pages': entry['pages']}
            write_json(pair_folder / 'pair.json', pair)
            pairs.append(pair)
            paper_pair_count += 1
        rows.append({'arxiv_id': aid, 'venue': paper['venue'], 'title': paper['title'],
                     'version_count': len(versions), 'pair_count': paper_pair_count})
    verified = sum(e['status'] == 'verified' for e in manifest['downloads'].values())
    summary = {'paper_count': len(records), 'expected_pdfs': expected_pdfs,
               'verified_pdfs': verified, 'failed_pdfs': expected_pdfs - verified,
               'expected_pairs': expected_pairs, 'pair_count': len(pairs)}
    manifest['summary'] = summary
    write_json(manifest_path, manifest)
    write_json(out / 'pairs.json', {'schema_version': '2.0', 'path_base': '.',
                                   'pairing_rule': 'All adjacent frozen versions, without gap filtering.',
                                   'summary': summary, 'papers': rows, 'pairs': pairs})
    with (out / 'papers.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# Review 输入数据', '',
             f'{len(records)} 篇论文，{verified}/{expected_pdfs} 个原始版本 PDF，'
             f'{len(pairs)}/{expected_pairs} 个相邻版本 pair。', '',
             '版本范围为主目录 JSON 记录的 v1…frozen_N；未查询冻结后的新增版本。',
             '每篇 N 个版本生成 N−1 个 pair，不按版本时间间隔过滤。', '',
             '`<arxiv_id>/v1_v2/v1.pdf` 和 `v2.pdf` 是可直接读取的普通文件（优先硬链接）。',
             '`.pdf_cache/` 保存每个版本的下载缓存；再次运行会验证并复用。',
             '`pairs.json` 中路径相对于此目录；`papers.csv` 为论文清单。', '',
             '| arXiv ID | 会议 | 版本数 | Pair 数 | 标题 |', '|---|---|---:|---:|---|']
    lines.extend(f'| {r["arxiv_id"]} | {r["venue"]} | {r["version_count"]} | '
                 f'{r["pair_count"]} | {r["title"].replace("|", " ")} |' for r in rows)
    (out / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if verified == expected_pdfs and len(pairs) == expected_pairs else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'v1.1.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'input_data')
    parser.add_argument('--seed', type=int, default=20260911)
    parser.add_argument('--arxiv-ids', nargs='+', help='Use these IDs instead of the default 15-paper sample')
    parser.add_argument('--delay', type=float, default=3.2, help='Seconds between network downloads')
    parser.add_argument('--dry-run', action='store_true', help='Print selection without downloading or writing')
    args = parser.parse_args()
    if args.delay < 0:
        parser.error('--delay must be nonnegative')
    return prepare(args)


if __name__ == '__main__':
    raise SystemExit(main())

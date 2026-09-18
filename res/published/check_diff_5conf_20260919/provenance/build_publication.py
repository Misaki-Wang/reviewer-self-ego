#!/usr/bin/env python3
"""Publish a hash-pinned, offline union of existing five-conference judgments.

Only the new package is written. No model calls, source edits, or Git mutations.
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess


CONFERENCES = ('iclr2026', 'icml2026', 'acl2026', 'eccv2026', 'cvpr2026')
EXPECTED = {
    'iclr2026': (2372, 3265, {'completed': 3252, 'input_unavailable': 13}),
    'icml2026': (2239, 2896, {'completed': 2892, 'input_unavailable': 3, 'provider_refused': 1}),
    'acl2026': (772, 1010, {'completed': 1004, 'input_unavailable': 6}),
    'eccv2026': (633, 776, {'completed': 775, 'blocked_input': 1}),
    'cvpr2026': (943, 1145, {'completed': 1143, 'blocked_input': 2}),
}
BASE_NAMES = {'base_iclr': 'iclr2026_full_20260913',
              'base_remaining': 'check_diff_remaining_sources_20260914',
              'extra': 'data_extra_check_diff_terminal_provider_refusal_20260917'}
ATTEMPT_ALLOWLIST = {
    'run_id', 'origin_run_id', 'arxiv_id', 'pair', 'pair_key', 'attempt', 'status',
    'started_at_utc', 'finished_at_utc', 'elapsed_seconds', 'requested_model',
    'requested_reasoning_effort', 'execution_signature', 'origin_execution_signature',
    'input_sha256', 'prompt_sha256', 'result_sha256', 'artifact_sha256',
    'exit_code', 'model_dispatched', 'token_usage', 'turn_completed_count',
    'turn_failed_count', 'result_label_normalization', 'source_receipt_sha256',
    'public_record_kind', 'transformation_note', 'omitted_fields',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'), sort_keys=True) + '\n')


def timestamp(value):
    return datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))


class Builder:
    def __init__(self, repo, snapshots, output):
        self.repo, self.snapshots, self.output = repo, snapshots, output
        self.commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        self.sources = {key: repo / 'res/published' / name for key, name in BASE_NAMES.items()}
        self.sources.update({conf: snapshots / conf for conf in ('eccv2026', 'cvpr2026')})
        self.sources['catalog'] = repo
        self.sources['independent_audit'] = snapshots.parent
        self.files = {}
        self.source_meta = {}
        self.validators = {}
        self.summary = {}
        self.pairs = {}
        self.attempts = {}
        self.catalog = {}
        self.evidence = []

    def source_file(self, source, relative):
        relative = str(relative)
        key = (source, relative)
        if key not in self.files:
            path = self.sources[source] / relative
            require(path.is_file() and not path.is_symlink(), f'Missing/nonregular source {path}')
            data = path.read_bytes()
            self.files[key] = {'source_id': source, 'path': relative,
                               'sha256': digest_bytes(data), 'bytes': len(data)}
        return dict(self.files[key])

    def rows(self, source, relative):
        ref = self.source_file(source, relative)
        with (self.sources[source] / relative).open('rb') as handle:
            for line, raw in enumerate(handle, 1):
                require(raw.strip(), f'Blank line in {relative}:{line}')
                yield json.loads(raw), {**ref, 'line': line, 'line_sha256': digest_bytes(raw)}

    def copy(self, source, relative, destination, expected_sha=None):
        ref = self.source_file(source, relative)
        if expected_sha is not None:
            require(ref['sha256'] == expected_sha, f'Source hash mismatch: {source}/{relative}')
        dest = self.output / destination
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.sources[source] / relative, dest)
        require(digest_bytes(dest.read_bytes()) == ref['sha256'], f'Copy mismatch {dest}')
        return {'path': str(destination), 'sha256': ref['sha256'], 'bytes': ref['bytes'], 'source': ref}

    def prepare_sources(self):
        for source, root in self.sources.items():
            if source == 'independent_audit':
                self.source_meta[source] = {
                    'kind': 'read_only_independent_audit',
                    'root': 'experiments/runs/20260919_checkdiff5conf_github_publish',
                    'source_checkout_git_commit': self.commit,
                }
                for filename in ('three_source_independent_audit.json', 'recent_source_independent_audit.json'):
                    audit = read_json(root / filename)
                    require(audit['status'] == 'passed', f'Independent audit not passed: {filename}')
                    self.evidence.append(self.copy(source, filename, f'verification/{filename}'))
                continue
            if source == 'catalog':
                self.source_meta[source] = {'kind': 'git_checkout', 'git_commit': self.commit, 'root': '.'}
                continue
            if source in ('eccv2026', 'cvpr2026'):
                snapshot = read_json(root / 'SNAPSHOT.json')
                for f in snapshot['files']:
                    actual = self.source_file(source, f['path'])
                    require((actual['sha256'], actual['bytes']) == (f['sha256'], f['bytes']),
                            f'Snapshot mismatch {source}/{f["path"]}')
                self.source_meta[source] = {'kind': 'remote_frozen_snapshot',
                                           'remote_root': snapshot['source_run'],
                                           'snapshot': self.source_file(source, 'SNAPSHOT.json')}
                self.evidence.append(self.copy(source, 'SNAPSHOT.json', f'evidence/{source}/SNAPSHOT.json'))
            else:
                self.source_meta[source] = {'kind': 'git_checkout', 'git_commit': self.commit,
                                           'root': str(root.relative_to(self.repo))}
            contract_files = ('schema.py', 'compare.md', 'rubric.md') if source == 'extra' else (
                'source_snapshot/check_diff/schema.py',
                'source_snapshot/check_diff/prompts/compare.md',
                'source_snapshot/check_diff/prompts/rubric.md')
            contracts = {Path(f).name: self.copy(source, f, f'contracts/{source}/{Path(f).name}')
                         for f in contract_files}
            self.validators[source] = runpy.run_path(str(root / contract_files[0]))['validate_result']
            self.source_meta[source]['scientific_contract'] = contracts
            if source == 'extra':
                public = read_json(root / 'public-contract.json')
                self.source_meta[source]['run_contracts'] = public['segments']
                for segment in public['segments']:
                    for basename, ref in contracts.items():
                        suffix = 'schema.py' if basename == 'schema.py' else f'prompts/{basename}'
                        require(segment['scientific_sha256'][f'source_snapshot/check_diff/{suffix}'] == ref['sha256'],
                                f'Extra scientific contract hash mismatch {basename}')
                evidence = ['public-contract.json', 'verification/provider-refusal.json',
                            'verification/runtime-qualification.json', 'verification/combined.json',
                            'verification/parent_checkpoint.json', 'verification/child_provider_refusal.json']
            else:
                config = read_json(root / 'run_config.json')
                contract = read_json(root / 'execution_contract.json')
                self.source_meta[source].update({
                    'run_id': config['run_id'], 'execution_signature': contract['execution_signature'],
                    'source_revision_as_reported': config.get('source_revision'),
                    'requested_model': contract['requested_model'],
                    'requested_reasoning_effort': contract['requested_reasoning_effort'],
                    'execution_contract_source': self.source_file(source, 'execution_contract.json'),
                    'run_config_source': self.source_file(source, 'run_config.json'),
                })
                scientific = contract.get('runtime_binding', {}).get('code_sha256', {})
                for basename, ref in contracts.items():
                    if ref['source']['path'] in scientific:
                        require(scientific[ref['source']['path']] == ref['sha256'], f'Contract mismatch {source}/{basename}')
                evidence = ['token_usage.json', 'run_manifest.json']
                if source.startswith('base_'):
                    evidence += ['final_verification.json', 'operations/independent_completion_check.json']
                elif source == 'eccv2026':
                    evidence += ['final_verification.json', 'FINAL_REPORT.md', 'REPORT.md']
                elif source == 'cvpr2026':
                    evidence += ['records/final_identity_audit.json', 'records/independent_delivery_verification.json']
            for f in evidence:
                self.evidence.append(self.copy(source, f, f'evidence/{source}/{f}'))

    def load_catalogs(self):
        for conf in CONFERENCES:
            path = f'data_5conf/index/{conf}.json'
            ref = self.source_file('catalog', path)
            records = read_json(self.repo / path)['records']
            by_id = {p['arxiv_id']: p for p in records}
            require(len(by_id) == len(records) == EXPECTED[conf][0], f'Catalog cardinality {conf}')
            self.catalog[conf] = by_id
            public = []
            for i, paper in enumerate(records):
                # Retain all original version dates and physical-page metadata;
                # the complete upstream catalog is pinned by Git commit and SHA.
                versions = [{k: v[k] for k in ('version', 'submitted_at', 'arxiv_url', 'pdf_url', 'page_count') if k in v}
                            for v in paper['versions']]
                public.append({'arxiv_id': paper['arxiv_id'], 'title': paper['title'],
                               'source_ids': paper['source_ids'], 'versions': versions,
                               'catalog_source': {**ref, 'record_index_zero_based': i},
                               'historical_pairs_are_authoritative': True})
            write_jsonl(self.output / conf / 'papers.jsonl', public)

    def add_pair(self, conf, source, row, provenance, input_batch):
        row = copy.deepcopy(row)
        key, paper_id = row['key'], row['arxiv_id']
        require(key not in self.pairs[conf], f'Duplicate pair {conf}/{key}')
        require(paper_id in self.catalog[conf], f'Out-of-scope paper {conf}/{paper_id}')
        before, after = row['before'], row['after']
        require(key == f'{paper_id}__v{before}_v{after}' and before < after, f'Invalid key {key}')
        versions = {v['version']: v for v in self.catalog[conf][paper_id]['versions']}
        require(before in versions and after in versions, f'Missing endpoint {key}')
        gap = (timestamp(versions[after]['submitted_at']) - timestamp(versions[before]['submitted_at'])).total_seconds()
        require(gap > 2592000, f'Ineligible temporal gap {key}')
        if 'gap_seconds' in row:
            require(row['gap_seconds'] == gap, f'Gap disagreement {key}')
        row['publication'] = {'conference': conf, 'input_batch': input_batch,
                              'source_id': source, 'source_row': provenance,
                              'validated_gap_seconds': gap,
                              'scientific_contract': f'contracts/{source}',
                              'artifacts': {}, 'attempt_ids': []}
        self.pairs[conf][key] = row
        return row

    def add_attempt(self, conf, source, key, record, source_ref, pointer=None):
        row = self.pairs[conf][key]
        run = record.get('origin_run_id', record.get('run_id'))
        require(run and isinstance(record.get('attempt'), int), f'Missing attempt identity {key}')
        identity = f'{run}/{key}/attempt-{record["attempt"]:03d}'
        require(identity not in self.attempts[conf], f'Duplicate attempt {identity}')
        if 'pair' in record:
            require(record['pair'] == [row['before'], row['after']], f'Attempt pair mismatch {identity}')
        require(record['arxiv_id'] == row['arxiv_id'], f'Attempt paper mismatch {identity}')
        if 'pair_key' in record:
            require(record['pair_key'] == key, f'Receipt key mismatch {identity}')
        projected = {k: copy.deepcopy(v) for k, v in record.items() if k in ATTEMPT_ALLOWLIST}
        projected.update({'attempt_id': identity, 'pair_key': key, 'conference': conf,
                          'input_batch': row['publication']['input_batch'],
                          'publication_source': source_ref,
                          'publication_omitted_fields': sorted(set(record) - ATTEMPT_ALLOWLIST)})
        if pointer is not None:
            projected['publication_source']['json_pointer'] = pointer
            projected.setdefault('source_receipt_sha256', None)
            projected['source_receipt_hash_note'] = ('The historical export retains this receipt only as an execution.json '
                                                      'attempt object. The exact execution file hash and JSON pointer are retained; '
                                                      'a standalone original receipt byte hash is unavailable.')
        elif source in ('eccv2026', 'cvpr2026'):
            projected['source_receipt_sha256'] = source_ref['sha256']
        require('token_usage' in projected, f'Usage missing from attempt {identity}')
        self.attempts[conf][identity] = projected
        row['publication']['attempt_ids'].append(identity)

    def completed(self, conf, source, row, raw_path, judgment_path, expected_raw=None, expected_judgment=None):
        key = row['key']
        raw = self.copy(source, raw_path, f'{conf}/responses/{key}.json', expected_raw)
        judgment = self.copy(source, judgment_path, f'{conf}/judgments/{key}.json', expected_judgment)
        result = read_json(self.output / judgment['path'])
        parsed_raw = read_json(self.output / raw['path'])
        require(parsed_raw == result, f'Raw/canonical mismatch {conf}/{key}')
        self.validators[source](result)
        if 'result' in row:
            require(row['result'] == result, f'Pair/canonical mismatch {conf}/{key}')
        row['result'] = result
        labels = {'A1': result['prelim']['A1']['value'],
                  'A2_before': result['prelim']['A2']['before']['value'],
                  'A2_after': result['prelim']['A2']['after']['value'],
                  **{d: v['change'] for d, v in result.items() if d != 'prelim'}}
        if 'labels' in row:
            require(row['labels'] == labels, f'Label disagreement {conf}/{key}')
        row['labels'] = labels
        row['publication']['artifacts'] = {'response': raw, 'judgment': judgment}

    def load_native(self, conf, source, input_batch):
        root = self.sources[source]
        for original, ref in self.rows(source, 'pair_status.jsonl'):
            if original['arxiv_id'] not in self.catalog[conf]:
                continue
            row = self.add_pair(conf, source, original, ref, input_batch)
            key, paper = row['key'], row['arxiv_id']
            if row['status'] != 'completed':
                require(row['attempt_count'] == 0, f'Unaccounted noncomplete attempts {key}')
                continue
            stem = f'output/{paper}/diff_v{row["before"]}_v{row["after"]}'
            execution_rel = f'{stem}.execution.json'
            execution_ref = self.source_file(source, execution_rel)
            execution = read_json(root / execution_rel)
            require(execution['status'] == 'completed', f'Execution not complete {key}')
            require(len(execution['attempts']) == row['attempt_count'], f'Attempt count mismatch {key}')
            row['publication']['execution_source'] = execution_ref
            row['publication']['run_id'] = execution['run_id']
            row['publication']['execution_signature'] = execution['execution_signature']
            row['token_usage'] = copy.deepcopy(execution['token_usage'])
            row['terminal_attempt'] = execution['attempt']
            for i, receipt in enumerate(execution['attempts']):
                if source in ('eccv2026', 'cvpr2026'):
                    receipt_rel = f'attempts/{key}/attempt-{receipt["attempt"]:03d}/receipt.json'
                    exact_receipt = read_json(root / receipt_rel)
                    require(exact_receipt == receipt, f'Embedded/standalone receipt mismatch {key}')
                    self.add_attempt(conf, source, key, exact_receipt, self.source_file(source, receipt_rel))
                else:
                    self.add_attempt(conf, source, key, receipt, dict(execution_ref), f'/attempts/{i}')
            require(execution['output_sha256'] == row['output_sha256'], f'Output SHA disagreement {key}')
            raw_rel = f'attempts/{key}/attempt-{execution["attempt"]:03d}/response.raw.json'
            self.completed(conf, source, row, raw_rel, f'{stem}.json',
                           execution['artifact_sha256']['response.raw.json'], row['output_sha256'])
        if source in ('eccv2026', 'cvpr2026'):
            expected_edges = {f'{p["arxiv_id"]}__v{e["from_version"]}_v{e["to_version"]}'
                              for p in self.catalog[conf].values() for e in p['selected_edges']}
            require(expected_edges == set(self.pairs[conf]), f'Selected-edge mismatch {conf}')
            receipt_files = list(root.glob('attempts/*/attempt-*/receipt.json'))
            require(len(receipt_files) == len(self.attempts[conf]), f'Unprojected native receipt {conf}')

    def load_extra(self):
        source = 'extra'
        root = self.sources[source]
        for path in sorted(root.glob('data/pairs-*.jsonl')):
            for original, ref in self.rows(source, str(path.relative_to(root))):
                for conf in CONFERENCES[:3]:
                    if conf not in original['sources']:
                        continue
                    row = self.add_pair(conf, source, original, ref, 'data_extra')
                    if row['status'] == 'completed':
                        self.completed(conf, source, row, row['links']['raw'], row['links']['judgment'],
                                       row['raw_response_sha256'], row['judgment_sha256'])
                    elif row['status'] == 'provider_refused':
                        row['publication']['artifacts']['provider_refusal'] = {
                            'path': 'evidence/extra/verification/provider-refusal.json',
                            **{k: v for k, v in self.source_file(source, 'verification/provider-refusal.json').items()
                               if k in ('sha256', 'bytes')}}
        for path in sorted(root.glob('data/receipts-*.jsonl')):
            for record, ref in self.rows(source, str(path.relative_to(root))):
                for conf in CONFERENCES[:3]:
                    pair = self.pairs[conf].get(record['pair_key'])
                    if pair is not None and pair['publication']['source_id'] == 'extra':
                        self.add_attempt(conf, source, record['pair_key'], record, ref)

    def finish_conference(self, conf):
        rows = sorted(self.pairs[conf].values(), key=lambda r: r['key'])
        attempts = sorted(self.attempts[conf].values(), key=lambda r: r['attempt_id'])
        paper_ids = {r['arxiv_id'] for r in rows}
        require(paper_ids == set(self.catalog[conf]), f'Paper scope mismatch {conf}')
        statuses = collections.Counter(r['status'] for r in rows)
        require(len(rows) == EXPECTED[conf][1] and statuses == EXPECTED[conf][2], f'Unexpected counts {conf}: {statuses}')
        batch_counts = {}
        label_counts = collections.defaultdict(collections.Counter)
        for row in rows:
            pubs = row['publication']
            actual_attempts = len(pubs['attempt_ids'])
            source_count = row.get('attempt_count', row.get('token_usage', {}).get('attempt_count', 0))
            require(actual_attempts == source_count, f'Incomplete attempts {conf}/{row["key"]}: {actual_attempts}/{source_count}')
            batch = batch_counts.setdefault(pubs['input_batch'], {'paper_ids': set(), 'pairs': 0, 'status_counts': collections.Counter()})
            batch['paper_ids'].add(row['arxiv_id'])
            batch['pairs'] += 1
            batch['status_counts'][row['status']] += 1
            for label, value in row.get('labels', {}).items():
                label_counts[label][value] += 1
        for batch in batch_counts.values():
            batch['papers'] = len(batch.pop('paper_ids'))
        usage_counts = collections.Counter()
        for attempt in attempts:
            usage = attempt['token_usage']
            records = usage.get('records', []) if isinstance(usage, dict) else []
            usage_counts['with_reported_usage' if records else 'without_reported_usage'] += 1
        summary = {'conference': conf, 'papers': len(paper_ids), 'pairs': len(rows),
                   'status_counts': dict(statuses), 'by_input_batch': batch_counts,
                   'label_counts_completed_only': dict(label_counts), 'attempt_count': len(attempts),
                   'attempt_usage_reporting': dict(usage_counts),
                   'reported_usage_policy': 'Attempt token_usage objects and historical pair summaries are retained verbatim. Missing usage is unknown; no token totals are recomputed.'}
        self.summary[conf] = summary
        write_jsonl(self.output / conf / 'pairs.jsonl', rows)
        write_jsonl(self.output / conf / 'attempts.jsonl', attempts)
        write_json(self.output / conf / 'summary.json', summary)

    def build(self):
        require(not self.output.exists(), f'Output already exists; refusing overwrite: {self.output}')
        self.output.mkdir(parents=True)
        self.prepare_sources()
        self.load_catalogs()
        for conf in CONFERENCES:
            self.pairs[conf], self.attempts[conf] = {}, {}
            if conf in ('eccv2026', 'cvpr2026'):
                self.load_native(conf, conf, 'data_5conf')
            else:
                self.load_native(conf, 'base_iclr' if conf == 'iclr2026' else 'base_remaining', 'data')
        self.load_extra()
        for conf in CONFERENCES:
            self.finish_conference(conf)
            print(conf, json.dumps({k: self.summary[conf][k] for k in ('papers', 'pairs', 'status_counts', 'attempt_count')}), flush=True)
        refusal = self.pairs['icml2026']['2510.17759__v1_v2']
        require(refusal['status'] == 'provider_refused' and len(refusal['publication']['attempt_ids']) == 2,
                'Refusal evidence lost')
        for identity in refusal['publication']['attempt_ids']:
            require(not self.attempts['icml2026'][identity]['token_usage']['records'], 'Refusal usage unexpectedly reported')
        caveats = [
            'ICML2026 pair 2510.17759__v1_v2 has terminal provider_refused status and no scientific judgment. Both known attempts have unknown, unreported consumption.',
            'The data_extra delivery retains an incomplete runtime process-quiescence proof. Finalized run records establish processing terminality; global process termination is not certified.',
            'CVPR2026 original input receipts omit arxiv_id. The retained read-only sidecar derives identity from frozen evidence; original receipts remain unchanged and the original strict verifier is not claimed to have passed.',
            'Historical pair ledgers are authoritative. Temporal witness_versions is not a complete historical pair selection. Every published pair endpoint was checked against the current pinned catalog and has a strict gap above 2592000 seconds.',
            'Source scientific contracts are retained separately. Contract hashes may differ across base and later batches; this publication does not harmonize prompts or labels.',
            'Historical attempt receipt projections omit host/account/runtime diagnostics. Base exports retain embedded attempts without standalone original receipt file hashes; execution-file hashes and JSON pointers identify these records.',
            'Scientific A1, per-version A2, all fourteen independent dimensions and overall improvement labels are unchanged. No A2 filtering or aggregate S2/T3 decision is introduced.',
        ]
        summary = {'schema_version': 'check-diff-five-conference-publication-1',
                   'release_id': self.output.name, 'catalog_git_commit': self.commit,
                   'conferences': self.summary, 'caveats': caveats,
                   'totals_as_conference_memberships': {
                       'papers': sum(s['papers'] for s in self.summary.values()),
                       'pairs': sum(s['pairs'] for s in self.summary.values()),
                       'completed': sum(s['status_counts']['completed'] for s in self.summary.values()),
                       'attempts': sum(s['attempt_count'] for s in self.summary.values())}}
        all_papers = [paper_id for conf in CONFERENCES for paper_id in self.catalog[conf]]
        require(len(all_papers) == len(set(all_papers)), 'Current conference paper memberships unexpectedly overlap')
        summary['unique_papers'] = len(set(all_papers))
        summary['conference_paper_sets_disjoint'] = True
        write_json(self.output / 'summary.json', summary)
        builder_dest = self.output / 'provenance/build_publication.py'
        builder_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__), builder_dest)
        manifest = {'schema_version': 'check-diff-publication-source-manifest-1',
                    'source_checkout_git_commit': self.commit, 'sources': self.source_meta,
                    'source_files': sorted(self.files.values(), key=lambda r: (r['source_id'], r['path'])),
                    'copied_evidence': self.evidence,
                    'builder_sha256': digest_bytes(Path(__file__).read_bytes()),
                    'jsonl_line_sha256_includes_line_terminator': True}
        write_json(self.output / 'source_manifest.json', manifest)
        lines = ['# Five-conference check_diff responses', '',
                 'This release merges existing results for the currently selected ICLR2026, ICML2026, ACL2026, ECCV2026 and CVPR2026 papers. It performs no model calls and changes no scientific judgments.', '',
                 '| Conference | Papers | Pairs | Completed | Other terminal status |',
                 '| --- | ---: | ---: | ---: | --- |']
        for conf, s in self.summary.items():
            other = ', '.join(f'{n} {status}' for status, n in s['status_counts'].items() if status != 'completed')
            lines.append(f'| {conf.upper()} | {s["papers"]} | {s["pairs"]} | {s["status_counts"]["completed"]} | {other} |')
        lines += ['', '## Layout', '',
                  '- `<conference>/pairs.jsonl` retains every selected historical pair, including unavailable inputs and the provider refusal. Complete results and original source-row fields are retained. New publication metadata is isolated under `publication`.',
                  '- `<conference>/responses/<pair-key>.json` and `judgments/<pair-key>.json` are exact accepted raw response and canonical judgment bytes. `publication.artifacts` contains package-relative links, byte hashes and source locators. Original `links` fields in historical rows remain source-relative provenance, not links into this package.',
                  '- `<conference>/attempts.jsonl` contains every known selected attempt, uniquely identified by source run, pair key and attempt number, with reported usage preserved exactly. The two ICML refusal attempts remain present with unknown usage.',
                  '- `<conference>/papers.jsonl` lists every selected paper and every original version date/page observation. This is an explicitly reduced metadata view; full source catalogs remain Git- and SHA-pinned in `source_manifest.json`.',
                  '- `contracts/<source>/` preserves the exact schema, comparison prompt and rubric for each source batch.',
                  '- `evidence/` contains only explicitly allowlisted historical audit, accounting, refusal, snapshot and runtime-qualification records. Native PDFs, credentials, caches, and private attempt diagnostics are excluded.',
                  '- `verification/` preserves two exact independent source audit reports; `provenance/build_publication.py` is the offline builder used for this release.',
                  '- `summary.json` gives status, input-batch and label counts. Counts sum conference memberships, not deduplicated papers across venues. Token totals are not recomputed; retained whole-source accounting artifacts may cover additional conferences.',
                  '- `source_manifest.json` identifies source file hashes, source Git commit or remote snapshot pins, and builder hash. `SHA256SUMS` covers every other file in this package.', '',
                  '## Provenance and limitations', '',
                  f'The source checkout and five-conference catalog are frozen at `{self.commit}`. ICLR2026 merges `iclr2026_full_20260913` and selected `data_extra` rows. ICML2026 and ACL2026 merge selected `check_diff_remaining_sources_20260914` and `data_extra` rows. ECCV2026 and CVPR2026 use their complete frozen September 18 runs.', '']
        lines += [f'- {c}' for c in caveats]
        lines += ['', '## Validation', '',
                  'The offline builder rejects duplicate or out-of-scope pairs, missing results, mismatched source/copy hashes, missing endpoints, nonqualifying temporal gaps, incomplete attempts, raw/canonical disagreement, schema failures and unexpected coverage counts. ECCV2026/CVPR2026 pairs must exactly match current catalog selected_edges. All known source statuses and all original labels are preserved.', '',
                  'From this directory, verify file integrity with `shasum -a 256 -c SHA256SUMS`.', '']
        (self.output / 'README.md').write_text('\n'.join(lines))
        files = sorted(p for p in self.output.rglob('*') if p.is_file())
        (self.output / 'SHA256SUMS').write_text(''.join(f'{digest_bytes(p.read_bytes())}  {p.relative_to(self.output)}\n' for p in files))
        print(json.dumps({'output': str(self.output), 'files': len(files) + 1,
                          'bytes': sum(p.stat().st_size for p in self.output.rglob('*') if p.is_file()),
                          'totals': summary['totals_as_conference_memberships']}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--snapshots', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.repo / 'res/published/check_diff_5conf_20260919'
    Builder(args.repo.resolve(), args.snapshots.resolve(), output.resolve()).build()


if __name__ == '__main__':
    main()

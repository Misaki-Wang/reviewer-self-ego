#!/usr/bin/env python3
"""Bind the authorized multi-source input inventory before any model calls."""
import hashlib, json, sys
from pathlib import Path
from datetime import datetime, timezone
sys.dont_write_bytecode = True
RUN = Path(__file__).resolve().parents[1]
SNAP = RUN / 'source_snapshot'
sys.path.insert(0, str(SNAP))
from review_experiment import runner

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def proof(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

def main():
    config = json.loads((RUN / 'pilot_config.json').read_text())
    manifest = json.loads((RUN / 'source_snapshot_manifest.json').read_text())
    assert hashlib.sha256(canonical(manifest['files'])).hexdigest() == manifest['revision'] == config['source_revision']
    for name, expected in manifest['files'].items():
        assert proof(SNAP / name) == expected, name
    preparation = json.loads((RUN / 'records/preparation/preparation.json').read_text())
    assert preparation['status'] == 'prepared'
    for name in ('scope_input', 'selection_manifest'):
        assert proof(RUN / (name + '.json')) == {k: preparation[name][k] for k in ('bytes', 'sha256')}
    scope = json.loads((RUN / 'scope_input.json').read_text())
    selection = json.loads((RUN / 'selection_manifest.json').read_text())
    expected_papers = {s['source_id']: s['paper_ids'] for s in scope['source_scopes']}
    expected_units = {(u['source_id'], u['arxiv_id']): u for u in selection['selected_units']}
    assert len(expected_units) == 100
    profiles, templates = runner.load_prompts(SNAP / 'review_experiment/prompts')
    assert len(templates) == 12 and len(config['sources']) == 10
    sources = []
    identities = set()
    for entry in config['sources']:
        sid = entry['source_id']
        input_root = RUN / entry['input']
        papers, pair_count = runner.discover(input_root)
        assert len(papers) == 10, (sid, len(papers))
        assert set(papers) == set(expected_papers[sid]), sid
        assigned, _ = runner.resolve_sources(input_root, papers, profiles, source=sid, project_root=SNAP)
        assert set(assigned.values()) == {sid}
        out = dict(entry)
        out.update(conditions=[name for name, _ in templates], replicates=1,
                   papers=[], paper_count=len(papers), system_prompt=profiles[sid].metadata(),
                   discovered_pairs=pair_count)
        for aid, versions in papers.items():
            identities.add(aid)
            unit = expected_units[(sid, aid)]
            assert [v.number for v in versions] == unit['selected_version_ids'], (sid, aid)
            assert all(t['gap_seconds'] > 2592000 for t in unit['selected_transitions'])
            expected_hashes = {v['version']: v['expected_sha256'] for v in unit['selected_versions']}
            canonical_paths = {v['version']: Path(v['pdf_path']) for v in unit['selected_versions']}
            selected = {'paper_id': aid, 'versions': []}
            for version in versions:
                raw, sha = version.read()
                # A null historical hash is missing metadata, not a different PDF.
                if expected_hashes[version.number] is not None:
                    assert sha == expected_hashes[version.number], (sid, aid, version.number)
                assert proof(canonical_paths[version.number]) == {'bytes': len(raw), 'sha256': sha}, (sid, aid, version.number)
                selected['versions'].append({'number': version.number, 'sha256': sha,
                    'bytes': len(raw), 'paths': [str(path.relative_to(RUN)) for path in version.sources]})
            out['papers'].append(selected)
        out['version_count'] = sum(len(p['versions']) for p in out['papers'])
        out['planned_reviews'] = out['version_count'] * len(templates)
        sources.append(out)
    scope_revision = hashlib.sha256(canonical(sources)).hexdigest()
    inventory = {'schema_version': 'review-pilot-inventory-1',
        'source_revision': manifest['revision'], 'scope_revision': scope_revision,
        'sources': sources, 'totals': {'sources': len(sources),
        'papers': sum(s['paper_count'] for s in sources), 'unique_paper_ids': len(identities),
        'versions': sum(s['version_count'] for s in sources),
        'reviews': sum(s['planned_reviews'] for s in sources)}}
    runner.save_json(RUN / 'inventory.json', inventory)
    if '--inventory-only' in sys.argv:
        print(json.dumps({'status': 'inventory_ready', 'source_revision': manifest['revision'], 'scope_revision': scope_revision, 'totals': inventory['totals']}, indent=2))
        return
    names = ['pilot_config.json', 'inventory.json', 'source_snapshot_manifest.json',
             'selection_manifest.json', 'scope_input.json', 'records/run_pilot.py',
             'records/freeze_inputs.py', 'records/controller-checks.json', 'records/preflight-results.json',
             'records/preparation/preparation.json', 'records/preparation/original_pdf_inventory.json',
             'records/preparation/verification.json', 'runtime/pdf/SKILL.md']
    for name in names:
        assert (RUN / name).is_file(), name
    checks = json.loads((RUN / 'records/controller-checks.json').read_text())
    assert checks.get('status') == 'passed' and checks.get('failures') == 0 and checks.get('errors') == 0, checks
    assert checks['controller_sha256'] == proof(RUN / 'records/run_pilot.py')['sha256'], 'Controller changed after tests'
    verification = {'status': 'passed', 'verified_at': datetime.now(timezone.utc).isoformat(),
        'source_revision': manifest['revision'], 'scope_revision': scope_revision,
        'scope_revision_basis': 'SHA-256 of canonical sorted-key compact JSON of inventory.sources',
        'bound_files': {name: proof(RUN / name) for name in names},
        'pdf_files_verified': inventory['totals']['versions'],
        'source_files_verified': len(manifest['files']),
        'historical_null_expected_hashes_measured_and_pinned': sum(v.get('expected_sha256') is None for u in selection['selected_units'] for v in u['selected_versions']),
        'totals': inventory['totals']}
    runner.save_json(RUN / 'records/freeze_verification.json', verification)
    print(json.dumps(verification, indent=2))

if __name__ == '__main__':
    main()

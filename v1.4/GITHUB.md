# v1.4 GitHub distribution

This directory publishes the frozen index, all ten source shards, all five supplemental artifacts, and selected verification/provenance records without modifying their bytes. The archived `README.md` describes the complete release. In this GitHub distribution, the combined `v1.4.json`, full tar archive, and their build/archive manifests are intentionally omitted. The combined JSON can be reconstructed exactly from the files below; it is not needed by the version-preparation adapter, which can consume `v1.4/index.json` directly.

The combined JSON is 129,350,612 bytes, with SHA-256 `9a1d5ee686417b6f5a5688f7c19957b11027aa2c6e3f9e3edb140cc6ced6e2eb`. The ten source files contain 3,706 memberships that deduplicate to 3,692 identical paper objects. Do not count repeated source memberships as additional papers.

Run from the repository root to verify the published package and reconstruct its original bytes in memory:

```sh
python3 - <<'PYCODE'
from pathlib import Path
import hashlib
import json

base = Path("v1.4")
index = json.loads((base / "index.json").read_bytes())
assert index["release"] == "v1.4"
by_id = {}
for entry in index["files"]:
    raw = (base / entry["path"]).read_bytes()
    assert len(raw) == entry["bytes"] < 100_000_000
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
    shard = json.loads(raw)
    assert len(shard["records"]) == entry["record_count"]
    for paper in shard["records"]:
        aid = paper["arxiv_id"]
        assert entry["source_id"] in paper["source_ids"]
        assert aid not in by_id or by_id[aid] == paper
        by_id[aid] = paper
assert len(by_id) == len(index["record_order"]) == len(set(index["record_order"])) == 3692
assert set(index["record_order"]) == set(by_id)
for entry in index["supplemental_artifacts"]:
    raw = (base / entry["path"]).read_bytes()
    assert len(raw) == entry["bytes"]
    assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
combined = dict(index["dataset_metadata"])
combined["records"] = [by_id[aid] for aid in index["record_order"]]
raw = (json.dumps(combined, ensure_ascii=False, sort_keys=True,
                  separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
assert len(raw) == index["source_json"]["bytes"] == 129350612
assert hashlib.sha256(raw).hexdigest() == index["source_json"]["sha256"]
assert combined["record_count"] == 3692
assert sum(len(p["versions"]) for p in combined["records"]) == 13965
print("PASS: exact v1.4 reconstruction and supplemental artifact hashes")
# Optional local output; excluded from Git by .gitignore:
# (base / "v1.4.json").write_bytes(raw)
PYCODE
```

The full metadata-only archive remains at `releases/candidate_index_v1_4_20260911` on MBZUAI and in the shared local workspace. Its tar.gz SHA-256 is `d3c4119db8419af43a9b74065a52ac18a93a1e8ae20799622aa4f16749acea24`.

The optional verification records describe the original archival checks. They do not claim a new model run. Neither manuscript PDFs, API credentials nor experiment responses are included in this GitHub data release.

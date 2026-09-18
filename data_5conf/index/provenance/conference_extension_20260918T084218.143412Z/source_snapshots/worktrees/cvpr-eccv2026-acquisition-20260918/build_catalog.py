#!/usr/bin/env python3
"""Stage additive CVPR/ECCV catalogs without modifying live data or PDFs.

Input: {"dataset":"source_data","records":[complete source_data records]}.
data receives >=3 selected versions; data_full receives >=2; data_extra is
the global data_full-minus-data difference. Gaps are strictly >2592000s.
All original versions are preserved, including skipped versions. Existing
records, shard bytes and record order are immutable. New PDF ledger rows
are explicitly unobserved; this tool performs no acquisition/model calls.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

CATALOGS = ("data", "data_full", "data_extra")
SOURCES = ("cvpr2026", "eccv2026")
EXT = "cvpr_eccv2026_20260918"
UNKNOWN = "This catalog extension has not observed the version-specific original PDF; verify its bytes and physical page count before use."


class CatalogConflict(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__(json.dumps({"conflicts": issues}, ensure_ascii=False))


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def pin(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return {"bytes": Path(path).stat().st_size, "sha256": h.hexdigest()}


def write_json(path, value, replace=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb" if replace else "xb") as stream:
        stream.write(encode(value))


def snapshot(project):
    root, result = Path(project).resolve(), {}
    for cat in CATALOGS:
        for path in sorted((root / cat / "index").rglob("*")):
            if path.is_symlink():
                raise ValueError("Catalog symlink is unsupported: " + str(path))
            if path.is_file():
                result[path.relative_to(root).as_posix()] = pin(path)
        if f"{cat}/index/index.json" not in result:
            raise ValueError("Missing catalog index: " + cat)
    return result


def freeze_baseline(project, path):
    value = {"schema_version": "source-data-three-catalog-baseline-1",
             "created_at": datetime.now(timezone.utc).isoformat(), "files": snapshot(project)}
    write_json(path, value)
    return value


def check_baseline(project, baseline):
    actual, expected = snapshot(project), baseline.get("files", {})
    issues = [{"kind": "stale_baseline", "path": p, "expected": expected.get(p),
               "actual": actual.get(p)} for p in sorted(set(actual) | set(expected))
              if actual.get(p) != expected.get(p)]
    if issues:
        raise CatalogConflict(issues)


def load_api(project):
    root = Path(project).resolve()
    sys.path[:0] = [str(root), str(root / "utility/.runtime/python_vendor")]
    from utility.data import prepare_source_data
    return prepare_source_data


def rule(minimum):
    return {"minimum_selected_versions": minimum, "operator": ">",
            "threshold_seconds": 2592000, "selected_versions_may_skip_intermediate_versions": True,
            "selection": "Earliest-valid greedy maximum sequence over complete original history."}


def merge_records(records, existing, api):
    if not isinstance(records, list):
        raise ValueError("Input records must be a list")
    result, issues = {}, []
    membership_keys = {"source_ids", "source_entries", "publications"}
    for position, record in enumerate(records):
        try:
            api.validate_identity(record)
            aid, sources = record["arxiv_id"], record["source_ids"]
            if not set(sources).issubset(SOURCES):
                raise ValueError("Only cvpr2026/eccv2026 incoming memberships are allowed")
            if any(not isinstance(e, dict) or e.get("source_id") not in sources
                   for e in record["source_entries"]):
                raise ValueError("Source entries must match source_ids")
            if {e["source_id"] for e in record["source_entries"]} != set(sources):
                raise ValueError("Every source membership requires a source entry")
            if record.get("version_history_status") not in (None, "complete"):
                raise ValueError("Full original history must be complete")
            if record.get("version_history_validation_errors"):
                raise ValueError("Full original history has validation errors")
            chosen, _ = api.select_versions(record)
            if len(chosen) < 2:
                raise ValueError("Fewer than two selected versions under strict >2592000s gaps")
            if record["temporal_eligibility"]["status"] != "eligible":
                raise ValueError("Input temporal eligibility must declare eligible")
            if aid in existing:
                issues.append({"kind": "existing_identity_overlap", "arxiv_id": aid,
                               "input_position": position,
                               "existing_source_ids": existing[aid]["source_ids"],
                               "incoming_source_ids": sources,
                               "existing_record_sha256": hashlib.sha256(encode(existing[aid])).hexdigest(),
                               "incoming_record_sha256": hashlib.sha256(encode(record)).hexdigest(),
                               "resolution": "Do not change historical shards or silently omit this paper."})
                continue
            if aid in result:
                prior = result[aid]
                changed = [k for k in sorted((set(prior) | set(record)) - membership_keys)
                           if prior.get(k) != record.get(k)]
                if changed:
                    issues.append({"kind": "new_identity_metadata_conflict", "arxiv_id": aid,
                                   "input_position": position, "conflicting_fields": changed})
                    continue
                for key in membership_keys:
                    for item in record.get(key, []):
                        if item not in prior.setdefault(key, []):
                            prior[key].append(copy.deepcopy(item))
            else:
                result[aid] = copy.deepcopy(record)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append({"kind": "invalid_input_record", "input_position": position,
                           "arxiv_id": record.get("arxiv_id") if isinstance(record, dict) else None,
                           "reason": str(exc)})
    if issues:
        raise CatalogConflict(issues)
    for record in result.values():
        record["source_ids"] = [sid for sid in SOURCES if sid in record["source_ids"]]
    return list(result.values())


def by_id(records):
    return {p["arxiv_id"]: p for p in records}


def verify_partition(catalogs):
    data, full, extra = (by_id(catalogs[k]) for k in CATALOGS)
    if set(data) & set(extra) or set(data) | set(extra) != set(full):
        raise ValueError("Catalog sets do not form data union extra equals full")
    if any(full[k] != v for k, v in {**data, **extra}.items()):
        raise ValueError("Catalog partition contains different paper metadata")


def counts(records):
    sids = sorted({sid for p in records for sid in p["source_ids"]})
    rows = [{"source_id": sid, "papers": sum(sid in p["source_ids"] for p in records),
             "original_versions": sum(len(p["versions"]) for p in records if sid in p["source_ids"])}
            for sid in sids]
    return {"unique_papers": len(records), "unique_original_versions": sum(len(p["versions"]) for p in records),
            "source_paper_memberships": sum(r["papers"] for r in rows),
            "source_version_memberships": sum(r["original_versions"] for r in rows), "per_source": rows}


def ledger_row(paper, version, input_pin, catalog="data"):
    if catalog not in CATALOGS:
        raise ValueError("Invalid PDF catalog")
    aid, number = paper["arxiv_id"], version["version"]
    return {"schema_version": "source-data-original-version-metadata-1",
            "arxiv_id": aid, "version": number, "requested_id": f"{aid}v{number}",
            "submitted_at": version["submitted_at"], "source_ids": paper["source_ids"],
            "observed_at_utc": None, "metadata_origin": "catalog_extension_without_pdf_observation",
            "source_catalog_record": {"input_sha256": input_pin["sha256"], "extension": EXT},
            "pdf": {"availability_status": "unresolved", "path": f"{catalog}/pdf/{aid}/{aid}v{number}.pdf",
                    "bytes": None, "sha256": None, "sha256_recomputed": False, "unresolved_reason": UNKNOWN},
            "page_count": {"value": None, "status": "unresolved", "unit": "physical_pdf_pages",
                           "includes_references_and_appendices": True, "source_pdf_sha256": None,
                           "page_count_reason": UNKNOWN, "unresolved_reason": UNKNOWN},
            "official_no_pdf_evidence": None}


def version_rows(records):
    for paper in records:
        for version in sorted(paper["versions"], key=lambda v: v["version"]):
            yield paper, version


def verify_version_ledger(path, records):
    expected = {(p["arxiv_id"], v["version"]) for p, v in version_rows(records)}
    seen = set()
    with Path(path).open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            row = json.loads(raw)
            key = (row["arxiv_id"], row["version"])
            if key in seen:
                raise ValueError(f"Duplicate version ledger identity at line {number}: {key}")
            seen.add(key)
    if seen != expected:
        raise ValueError(f"Version ledger coverage mismatch: missing={len(expected-seen)}, extra={len(seen-expected)}")
    return len(seen)


def append_rows(path, rows):
    if path.stat().st_size:
        with path.open("rb") as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                raise ValueError("Historical ledger must end with newline: " + str(path))
    with path.open("ab") as stream:
        for row in rows:
            stream.write(encode(row))


def write_shard(directory, name, records, source_id=None, minimum=2):
    shard = {"dataset": "source_data", "schema_version": "source-data-source-shard-1",
             "record_count": len(records), "temporal_rule": rule(minimum), "records": records}
    if source_id:
        shard["source_id"] = source_id
    path = directory / name
    write_json(path, shard)
    entry = {"path": name, "record_count": len(records), **pin(path)}
    if source_id:
        entry["source_id"] = source_id
    return entry


def build_catalog(project, input_path, baseline_path, output, api=None):
    project, output = Path(project).resolve(), Path(output).resolve()
    if not output.is_relative_to(project / "experiments/runs") or output == project / "experiments/runs":
        raise ValueError("Candidate output must be an isolated directory under experiments/runs")
    if any((output / name).exists() for name in (*CATALOGS, "build_report.json", "conflicts.json")):
        raise FileExistsError("Candidate targets already exist; use a fresh directory")
    baseline = json.loads(Path(baseline_path).read_bytes())
    check_baseline(project, baseline)
    api = api or load_api(project)
    old = {cat: api.load_source(project / cat / "index/index.json")[0] for cat in CATALOGS}
    verify_partition(old)
    indices = {cat: json.loads((project / cat / "index/index.json").read_bytes()) for cat in CATALOGS}
    if "bases" not in indices["data_full"]:
        raise ValueError("data_full must retain its canonical pinned-base overlay")
    for cat, index in indices.items():
        existing_sources = {e.get("source_id") for e in index.get("files", []) + index.get("bases", [])}
        if existing_sources.intersection(SOURCES):
            raise ValueError("Incoming sources already exist in " + cat)
    input_raw = Path(input_path).read_bytes()
    incoming = json.loads(input_raw)
    input_pin = {"bytes": len(input_raw), "sha256": hashlib.sha256(input_raw).hexdigest()}
    api.require_source_data(incoming, "Incoming source")
    try:
        records = merge_records(incoming.get("records"), by_id(old["data_full"]), api)
    except CatalogConflict as exc:
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "conflicts.json", {"input": input_pin, "conflicts": exc.issues})
        raise
    if not records:
        raise ValueError("No new eligible records; no catalog mutation is needed")
    chosen_counts = {p["arxiv_id"]: len(api.select_versions(p)[0]) for p in records}
    additions = {"data": [p for p in records if chosen_counts[p["arxiv_id"]] >= 3],
                 "data_full": records,
                 "data_extra": [p for p in records if chosen_counts[p["arxiv_id"]] == 2]}
    total = {cat: old[cat] + additions[cat] for cat in CATALOGS}
    verify_partition(total)
    output.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".catalog-build-", dir=output))
    changed = set()
    try:
        for rel, expected in baseline["files"].items():
            parts = Path(rel).parts
            if len(parts) < 3 or parts[0] not in CATALOGS or parts[1] != "index" or ".." in parts:
                raise ValueError("Unsafe baseline path: " + rel)
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / rel, target)
            if pin(target) != expected:
                raise ValueError("Baseline changed while copying: " + rel)
        proofs = []
        for paper in records:
            chosen, edges = api.select_versions(paper)
            proofs.append({"arxiv_id": paper["arxiv_id"], "source_ids": paper["source_ids"],
                           "frozen_N": paper["frozen_N"],
                           "selected_versions": [v["version"] for v in chosen], "selected_edges": edges,
                           "data_member": len(chosen) >= 3})
        for cat in ("data", "data_extra"):
            directory = stage / cat / "index"
            for sid in SOURCES:
                group = [p for p in additions[cat] if sid in p["source_ids"]]
                indices[cat]["files"].append(write_shard(directory, f"{sid}.json", group, sid,
                                                        minimum=3 if cat == "data" else 2))
        full = indices["data_full"]
        for sid in SOURCES:
            base = next(e for e in indices["data"]["files"] if e.get("source_id") == sid)
            full["bases"].append({"catalog": "data", **{k:v for k,v in base.items() if k != "path"}})
            group = [p for p in additions["data_extra"] if p["source_ids"] == [sid]]
            full["files"].append(write_shard(stage / "data_full/index", f"{sid}.additions.json", group, sid))
        shared = [p for p in additions["data_extra"] if len(p["source_ids"]) > 1]
        if shared:
            full["files"].append(write_shard(stage / "data_full/index", "cvpr_eccv2026.shared.additions.json", shared))
        extension_rel = f"provenance/{EXT}.json"
        for cat in CATALOGS:
            directory = stage / cat / "index"
            ledger = directory / f"provenance/{EXT}.versions.jsonl"
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("xb") as stream:
                for paper, version in version_rows(additions[cat]):
                    stream.write(encode(ledger_row(paper, version, input_pin, cat)))
            verify_version_ledger(ledger, additions[cat])
            extension = {
                "schema_version": "source-data-three-catalog-extension-1",
                "created_at_utc": datetime.now(timezone.utc).isoformat(), "catalog": cat,
                "input": {"path": str(Path(input_path).resolve()), **input_pin},
                "baseline_manifest": {"path": str(Path(baseline_path).resolve()), **pin(baseline_path)},
                "baseline_files": baseline["files"], "new_counts": counts(additions[cat]),
                "current_counts": counts(total[cat]), "proofs": proofs,
                "selection": {"data": "selected_count >= 3", "data_full": "selected_count >= 2",
                              "data_extra": "data_full minus data by global arxiv_id"},
                "strict_gap_seconds": 2592000,
                "new_version_metadata": {"path": ledger.relative_to(directory).as_posix(), **pin(ledger)},
                "new_version_metadata_scope": "New catalog members only; no PDF observation was performed.",
                "existing_records_and_shards_unchanged": True, "historical_records_reselected": False,
                "historical_build_provenance_preserved": True,
                "pdf_downloads": 0, "pdf_observations": 0, "model_calls": 0,
                "builder": {"path": str(Path(__file__).resolve()), **pin(__file__)}
            }
            write_json(directory / extension_rel, extension)
            extension_pin = {"path": extension_rel, **pin(directory / extension_rel)}
            index = indices[cat]
            index["record_order"] += [p["arxiv_id"] for p in additions[cat]]
            metadata = index["dataset_metadata"]
            metadata["record_count"] = len(total[cat])
            metadata.setdefault("catalog_extensions", []).append(extension_pin)
            # Existing source rules and historical provenance are retained unchanged.
            write_json(stage / cat / "source_data.json", {**metadata, "records": total[cat]})
            index["source_json"] = {"filename": "source_data.json", **pin(stage / cat / "source_data.json")}
            write_json(directory / "index.json", index, replace=True)
            changed.add(f"{cat}/index/index.json")
        # The existing data ledger has one row per original version; preserve its byte prefix.
        data_ledger = stage / "data/index/metadata/version_metadata.jsonl"
        verify_version_ledger(data_ledger, old["data"])
        append_rows(data_ledger, (ledger_row(p, v, input_pin) for p, v in version_rows(additions["data"])))
        verify_version_ledger(data_ledger, total["data"])
        changed.add("data/index/metadata/version_metadata.jsonl")
        meta_path = stage / "data/index/metadata/provenance.json"
        before_rel = f"provenance/{EXT}.metadata_before.json"
        shutil.copyfile(meta_path, stage / "data/index" / before_rel)
        meta = json.loads(meta_path.read_bytes())
        meta["previous_provenance_snapshot"] = {"path": before_rel, "relative_to": "data/index",
                                                **pin(stage / "data/index" / before_rel)}
        meta["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        meta["source_index_sha256"] = pin(stage / "data/index/index.json")["sha256"]
        meta["source_combined_sha256"] = indices["data"]["source_json"]["sha256"]
        meta["version_record_count"] = sum(len(p["versions"]) for p in total["data"])
        added_versions = sum(len(p["versions"]) for p in additions["data"])
        meta["unobserved_version_count"] = meta.get("unobserved_version_count", 0) + added_versions
        meta["current_scope"] = {"papers": len(total["data"]), "original_versions": meta["version_record_count"],
                                 "unobserved_added_versions": added_versions, "extension": EXT}
        meta.setdefault("catalog_extensions", []).append(
            {"path": extension_rel, **pin(stage / "data/index" / extension_rel)})
        meta["historical_audit_scope"] = "Audit timing, host, script and verified/no-PDF totals remain inherited historical evidence. This extension appended only unobserved version rows."
        meta["files"]["version_metadata.jsonl"] = pin(data_ledger)
        write_json(meta_path, meta, replace=True)
        changed.add("data/index/metadata/provenance.json")
        extra_versions = stage / "data_extra/index/versions.jsonl"
        verify_version_ledger(extra_versions, old["data_extra"])
        append_rows(extra_versions, (
            {"arxiv_id": p["arxiv_id"], "version": v["version"],
             "requested_id": f'{p["arxiv_id"]}v{v["version"]}', "source_ids": p["source_ids"],
             "relative_pdf_path": f'pdf/{p["arxiv_id"]}/{p["arxiv_id"]}v{v["version"]}.pdf'}
            for p, v in version_rows(additions["data_extra"])))
        verify_version_ledger(extra_versions, total["data_extra"])
        changed.add("data_extra/index/versions.jsonl")
        old_statistics = stage / "data_extra/index/statistics.json"
        shutil.copyfile(old_statistics, stage / "data_extra/index" / f"provenance/{EXT}.statistics_before.json")
        write_json(old_statistics, counts(total["data_extra"]), replace=True)
        changed.add("data_extra/index/statistics.json")
        # Validate the actual staged overlay in its required data_full/index location.
        for cat in CATALOGS:
            loaded, _ = api.load_source(stage / cat / "index/index.json")
            if loaded != total[cat]:
                raise ValueError("Canonical staged loader mismatch: " + cat)
            combined, _ = api.load_source(stage / cat / "source_data.json")
            if combined != total[cat]:
                raise ValueError("Combined source pin/records mismatch: " + cat)
            for sid in SOURCES:
                expected = [p for p in additions[cat] if sid in p["source_ids"]]
                if expected and api.load_source(stage / cat / "index/index.json", source_ids=[sid])[0] != expected:
                    raise ValueError("Source-filter loader mismatch: " + cat + "/" + sid)
        for rel, expected in baseline["files"].items():
            if rel not in changed and pin(stage / rel) != expected:
                raise ValueError("Historical file changed: " + rel)
        for rel in ("data/index/metadata/version_metadata.jsonl", "data_extra/index/versions.jsonl"):
            expected = baseline["files"][rel]
            with (stage / rel).open("rb") as stream:
                prefix_hash = hashlib.sha256(stream.read(expected["bytes"])).hexdigest()
            if prefix_hash != expected["sha256"]:
                raise ValueError("Historical ledger prefix changed: " + rel)
        check_baseline(project, baseline)
        report = {"schema_version": "source-data-three-catalog-validation-1", "status": "passed",
                  "new_counts": {c: counts(additions[c]) for c in CATALOGS},
                  "current_counts": {c: counts(total[c]) for c in CATALOGS},
                  "candidate_indices": {c: pin(stage / c / "index/index.json") for c in CATALOGS},
                  "modified_historical_paths": sorted(changed),
                  "baseline_manifest": pin(baseline_path),
                  "input": {"path": str(Path(input_path).resolve()), **input_pin},
                  "existing_shards_bytes_preserved": True, "existing_ledger_prefixes_preserved": True,
                  "existing_record_orders_preserved": True, "live_catalogs_unchanged": True,
                  "canonical_load_verified": True, "data_union_extra_equals_full": True,
                  "data_extra_disjoint": True, "pdf_observations": 0, "installed": False}
        write_json(stage / "build_report.json", report)
        for name in (*CATALOGS, "build_report.json"):
            (stage / name).rename(output / name)
        return report
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--freeze-baseline", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.freeze_baseline:
        if args.input or args.baseline_manifest or args.output:
            parser.error("--freeze-baseline cannot be combined with build arguments")
        print(json.dumps({"files": len(freeze_baseline(args.project_root, args.freeze_baseline)["files"])}))
        return
    if not all((args.input, args.baseline_manifest, args.output)):
        parser.error("Build requires --input, --baseline-manifest and --output")
    try:
        print(json.dumps(build_catalog(args.project_root, args.input, args.baseline_manifest, args.output), indent=2))
    except CatalogConflict as exc:
        print(json.dumps({"status": "blocked_by_conflicts", "conflicts": exc.issues}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()


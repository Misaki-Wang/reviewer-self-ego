"""Build a standalone, provenance-preserving three-conference source catalog.

Only destination index files and provenance snapshots are written. Paper records,
including historical PDF paths and observations, retain their exact JSON values.
Current filesystem availability belongs in the caller's operational inventory.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


GAP_SECONDS = 2592000
CATALOGS = ("data", "data_extra")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != raw:
            raise ValueError(f"Refusing to overwrite differing output: {path}")
        return
    with path.open("xb") as handle:
        handle.write(raw)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing original version timestamp")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Original version timestamp lacks a timezone")
    return stamp.astimezone(timezone.utc)


def select_versions(paper: dict) -> tuple[list[dict], list[dict]]:
    """Match prepare_source_data's full-history strict-gap greedy selection."""
    versions = paper.get("versions")
    frozen_n = paper.get("frozen_N")
    if (not isinstance(versions, list) or type(frozen_n) is not int or frozen_n < 1
            or any(not isinstance(v, dict) or "version" not in v for v in versions)):
        raise ValueError("Incomplete or invalid original history")
    numbers = [v["version"] for v in versions]
    if (any(type(v) is not int or v < 1 for v in numbers)
            or sorted(numbers) != list(range(1, frozen_n + 1))):
        raise ValueError("Incomplete or invalid original history")
    dated = sorted(((_timestamp(v.get("submitted_at")), v["version"], v) for v in versions),
                   key=lambda item: (item[0], item[1]))
    if [number for _, number, _ in dated] != sorted(numbers):
        raise ValueError("Timestamps conflict with original version order")
    chosen = []
    for date, number, version in dated:
        if not chosen or (date - chosen[-1][0]).total_seconds() > GAP_SECONDS:
            chosen.append((date, number, version))
    edges = []
    for before, after in zip(chosen, chosen[1:]):
        gap = (after[0] - before[0]).total_seconds()
        edges.append({"from_version": before[1], "to_version": after[1],
                      "from_submitted_at": before[2]["submitted_at"],
                      "to_submitted_at": after[2]["submitted_at"],
                      "gap_seconds": gap, "gap_days": gap / 86400})
    return [v for _, _, v in chosen], edges


def _checked_relative(parent: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe relative input path: {value}")
    path = parent / relative
    if not path.resolve().is_relative_to(parent.resolve()):
        raise ValueError(f"Input path escapes its catalog: {value}")
    return path


def _validate_record(record: dict, source: str) -> None:
    if not isinstance(record, dict) or not re.fullmatch(r"\d{4}\.\d{4,5}", str(record.get("arxiv_id", ""))):
        raise ValueError("Invalid paper identity")
    memberships = record.get("source_ids")
    if (not isinstance(memberships, list) or source not in memberships
            or any(not isinstance(s, str) or not s for s in memberships)
            or len(set(memberships)) != len(memberships)):
        raise ValueError(f"Invalid source membership: {record['arxiv_id']}")
    if (not isinstance(record.get("temporal_eligibility"), dict)
            or "status" not in record["temporal_eligibility"]
            or not isinstance(record.get("source_entries"), list)
            or not isinstance(record.get("title"), str)
            or not isinstance(record.get("arxiv_url"), str)):
        raise ValueError(f"Incomplete source paper: {record['arxiv_id']}")


def build_catalog(project_root: Path, destination: Path, sources: tuple[str, ...],
                  provenance_dir: Path) -> dict:
    """Merge selected standalone data/data_extra shards without mutating inputs.

    ``by_source`` maps each requested source ID to its preserved record list.
    ``expected_versions`` covers complete original histories; its PDF candidates
    are locations to inspect, never claims that a file exists or is verified.
    ``source_snapshots`` pins exact copied source bytes. Filtered historical
    ledgers have separate ``metadata_snapshots`` with original and subset pins.
    """
    project_root = Path(project_root).resolve()
    destination = Path(destination).resolve()
    provenance_dir = Path(provenance_dir).resolve()
    if (not sources or len(set(sources)) != len(sources)
            or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", s) or ".." in s for s in sources)):
        raise ValueError("Source IDs must be unique safe identifiers")
    for catalog in CATALOGS:
        source_root = (project_root / catalog).resolve()
        if destination == source_root or destination.is_relative_to(source_root):
            raise ValueError("Destination overlaps an original catalog")
        if provenance_dir == source_root or provenance_dir.is_relative_to(source_root):
            raise ValueError("Provenance destination overlaps an original catalog")
    selected_sources = set(sources)
    records_by_id = {}
    origins_by_id = {}
    input_order = []
    by_source = {source: [] for source in sources}
    source_snapshots = []
    snapshotted = set()
    catalog_ids = {catalog: set() for catalog in CATALOGS}

    def snapshot(path: Path) -> dict:
        relative = path.relative_to(project_root).as_posix()
        raw = path.read_bytes()
        target = provenance_dir / "source_snapshots" / relative
        entry = {"path": relative, "sha256": _sha(raw), "bytes": len(raw),
                 "snapshot_path": str(target)}
        if relative not in snapshotted:
            _write(target, raw)
            source_snapshots.append(entry)
            snapshotted.add(relative)
        return entry

    for catalog in CATALOGS:
        index_dir = project_root / catalog / "index"
        index_path = index_dir / "index.json"
        snapshot(index_path)
        index = json.loads(index_path.read_bytes())
        if (index.get("dataset") != "source_data" or "bases" in index
                or index.get("dataset_metadata", {}).get("dataset") != "source_data"):
            raise ValueError(f"Expected standalone source_data index: {index_path}")
        entries = index.get("files")
        if not isinstance(entries, list):
            raise ValueError(f"Missing shard entries: {index_path}")
        selected_entries = [entry for entry in entries if entry.get("source_id") in selected_sources]
        if Counter(entry["source_id"] for entry in selected_entries) != Counter(sources):
            raise ValueError(f"Expected one shard per requested source in {catalog}")
        local_ids = set()
        for entry in selected_entries:
            source = entry["source_id"]
            shard_path = _checked_relative(index_dir, entry["path"])
            pin = snapshot(shard_path)
            if pin["sha256"] != entry.get("sha256") or pin["bytes"] != entry.get("bytes"):
                raise ValueError(f"Source shard checksum mismatch: {shard_path}")
            shard = json.loads(shard_path.read_bytes())
            records = shard.get("records")
            if (shard.get("dataset") != "source_data" or shard.get("source_id") != source
                    or "bases" in shard or not isinstance(records, list)
                    or entry.get("record_count") != len(records)
                    or shard.get("record_count") != len(records)):
                raise ValueError(f"Invalid source shard: {shard_path}")
            seen_in_shard = set()
            for record in records:
                _validate_record(record, source)
                aid = record["arxiv_id"]
                if aid in seen_in_shard:
                    raise ValueError(f"Duplicate paper within source shard: {aid}")
                seen_in_shard.add(aid)
                if aid in records_by_id and records_by_id[aid] != record:
                    raise ValueError(f"Conflicting duplicate paper metadata: {aid}")
                records_by_id[aid] = record
                origins_by_id.setdefault(aid, []).append(catalog)
                catalog_ids[catalog].add(aid)
                local_ids.add(aid)
                if not any(p["arxiv_id"] == aid for p in by_source[source]):
                    by_source[source].append(record)
            reference = shard.get("build_provenance")
            if isinstance(reference, dict) and isinstance(reference.get("path"), str):
                dependency = _checked_relative(index_dir, reference["path"])
                dependency_pin = snapshot(dependency)
                if (reference.get("sha256") not in (None, dependency_pin["sha256"])
                        or reference.get("bytes") not in (None, dependency_pin["bytes"])):
                    raise ValueError(f"Build provenance checksum mismatch: {dependency}")
        order = index.get("record_order")
        if not isinstance(order, list) or len(order) != len(set(order)) or not local_ids.issubset(order):
            raise ValueError(f"Invalid source record order: {index_path}")
        input_order.extend(aid for aid in order if aid in local_ids and aid not in input_order)
        # Preserve root build provenance and source-specific update evidence.
        provenance = index_dir / "provenance"
        if provenance.is_dir():
            for path in sorted(provenance.rglob("*")):
                if path.is_file() and (path.name in {"build.json", "build_metadata.json", "input_catalogs.json"}
                                       or any(source in path.name for source in sources)):
                    snapshot(path)
        for name in ("provenance.json", "statistics.json", "unavailable_versions.json"):
            path = index_dir / "metadata" / name
            if path.is_file():
                snapshot(path)

    # Prefer the pinned full-catalog ordering embedded in extra's provenance.
    pinned_path = project_root / "data_extra/index/provenance/input_catalogs.json"
    if pinned_path.is_file():
        pinned = json.loads(pinned_path.read_bytes()).get("data_full", {})
        full_order = pinned.get("record_order", [])
        if full_order and set(records_by_id).issubset(full_order) and len(full_order) == len(set(full_order)):
            input_order = [aid for aid in full_order if aid in records_by_id]
    records = [records_by_id[aid] for aid in input_order]
    if set(input_order) != set(records_by_id):
        raise ValueError("Merged record order does not cover preserved records")
    for source in sources:
        source_ids = {r["arxiv_id"] for r in by_source[source]}
        by_source[source] = [record for record in records if record["arxiv_id"] in source_ids]

    metadata_snapshots = []
    for catalog in CATALOGS:
        for name in ("metadata/version_metadata.jsonl", "versions.jsonl"):
            path = project_root / catalog / "index" / name
            if not path.is_file():
                continue
            raw = path.read_bytes()
            lines = raw.splitlines(keepends=True)
            kept = [(number, line) for number, line in enumerate(lines, 1)
                    if line.strip() and json.loads(line).get("arxiv_id") in catalog_ids[catalog]]
            filtered = b"".join(line for _, line in kept)
            target = provenance_dir / "historical_metadata" / catalog / name
            _write(target, filtered)
            metadata_snapshots.append({"path": path.relative_to(project_root).as_posix(),
                                       "sha256": _sha(raw), "bytes": len(raw),
                                       "snapshot_path": str(target),
                                       "snapshot_sha256": _sha(filtered), "snapshot_bytes": len(filtered),
                                       "selected_row_count": len(kept),
                                       "original_line_numbers": [number for number, _ in kept],
                                       "semantics": "Unmodified historical rows; not current availability verification."})

    expected_versions = []
    metrics = {}
    for record in records:
        aid = record["arxiv_id"]
        selected, edges = select_versions(record)
        if len(selected) < 2:
            raise ValueError(f"Merged paper has no eligible selected pair: {aid}")
        metrics[aid] = {"paper_count": 1, "pair_count": len(edges),
                        "original_version_count": len(record["versions"]),
                        "selected_version_count": len(selected)}
        origins = list(dict.fromkeys(origins_by_id[aid]))
        for version in record["versions"]:
            number = version["version"]
            filename = f"{aid}v{number}.pdf"
            candidates = [f"{catalog}/pdf/{aid}/{filename}" for catalog in origins]
            if "data_extra" in origins:
                candidates.append(f"data_full/pdf/{aid}/{filename}")
            historical = version.get("pdf", {}).get("path")
            if isinstance(historical, str) and historical:
                path = Path(historical)
                if path.is_absolute():
                    if path.is_relative_to(project_root):
                        candidates.append(path.relative_to(project_root).as_posix())
                elif ".." not in path.parts:
                    candidates.append(path.as_posix())
            expected_versions.append({"arxiv_id": aid, "version": number,
                                      "paper_version_id": f"{aid}v{number}",
                                      "source_ids": record["source_ids"],
                                      "catalog_origin": origins[0] if len(origins) == 1 else "+".join(origins),
                                      "original_candidates": list(dict.fromkeys(candidates))})

    def counts(items: list[dict]) -> dict:
        return {key: sum(metrics[r["arxiv_id"]][key] for r in items)
                for key in ("paper_count", "pair_count", "original_version_count", "selected_version_count")}

    stats = {"total": counts(records), "per_source": {source: counts(by_source[source]) for source in sources},
             "by_catalog": {catalog: counts([records_by_id[aid] for aid in sorted(catalog_ids[catalog])])
                            for catalog in CATALOGS},
             "overlapping_catalog_paper_count": len(catalog_ids["data"] & catalog_ids["data_extra"])}
    temporal_rule = {"minimum_selected_versions": 2, "operator": ">", "threshold_seconds": GAP_SECONDS,
                     "selected_versions_may_skip_intermediate_versions": True,
                     "selection": "Earliest-valid greedy maximum sequence; every adjacent selected gap must strictly exceed the threshold."}
    build = {"schema_version": "source-data-five-conference-merge-1", "source_ids": list(sources),
             "catalogs": list(CATALOGS), "stats": stats, "temporal_rule": temporal_rule,
             "records_preserved_without_modification": True,
             "pdf_paths_in_paper_records": "Historical evidence paths; use the separate current PDF inventory for operations.",
             "source_snapshots": source_snapshots, "metadata_snapshots": metadata_snapshots}
    build_raw = _json_bytes(build)
    _write(destination / "index/provenance/catalog_merge.json", build_raw)
    build_pin = {"path": "provenance/catalog_merge.json", "bytes": len(build_raw), "sha256": _sha(build_raw)}
    files = []
    for source in sources:
        shard = {"dataset": "source_data", "schema_version": "source-data-source-shard-1",
                 "source_id": source, "record_count": len(by_source[source]),
                 "build_provenance": build_pin, "records": by_source[source]}
        raw = _json_bytes(shard)
        _write(destination / "index" / f"{source}.json", raw)
        files.append({"source_id": source, "path": f"{source}.json", "bytes": len(raw),
                      "sha256": _sha(raw), "record_count": len(by_source[source])})
    index = {"dataset": "source_data", "schema_version": "source-data-index-1",
             "dataset_metadata": {"dataset": "source_data", "schema_version": "source-data-source-1",
                                  "language": "en", "record_count": len(records),
                                  "source_ids": list(sources), "temporal_rule": temporal_rule,
                                  "build_provenance": build_pin},
             "files": files, "record_order": input_order}
    _write(destination / "index/index.json", _json_bytes(index))
    return {"by_source": by_source, "records": records, "expected_versions": expected_versions,
            "source_snapshots": source_snapshots, "metadata_snapshots": metadata_snapshots, "stats": stats}

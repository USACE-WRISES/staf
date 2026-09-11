"""What is on disk: the national datasets, each chunk's downloads, the
cross-section archive by state, the bandwidth budget and disk use. Pure
reads of files and ``units.json`` for the panel's Data and Cross-sections
tabs; nothing here changes anything."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config
from .paths import DataRoot
from .state import Bandwidth, UnitStates

CONUS_STATES = ("AL", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "ID", "IL", "IN", "IA", "KS",
                "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
                "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA",
                "WA", "WV", "WI", "WY")

#: the national job's steps: (step, label, files, origin)
NATIONAL_STEPS = (
    ("vaa", "NHDPlus V2 attributes (VAA + ENHD)", lambda r: [r.vaa, r.vaa_raw, r.enhd_raw], "USGS via pynhd"),
    ("index", "COMID index", lambda r: [r.index, r.huc8_index, r.huc4_vpu], "derived from the attributes"),
    ("nid", "Dam inventory (NID)", lambda r: [r.nid], "USACE FeatureServer"),
    ("huc4", "HUC4 polygons", lambda r: [r.huc4_geojson], "USGS fabric API"),
    ("streamcat", "StreamCat cache", lambda r: [r.national / "streamcat.parquet"], "EPA StreamCat API, by region"),
    ("nas", "NAS established records", lambda r: [r.national / "nas.parquet"], "USGS NAS API, paged"),
    ("flowlines", "Flowlines (seamless geodatabase)", lambda r: [r.national / "flowlines.parquet"],
     "NHDPlus V2 seamless geodatabase"),
    ("huc12", "HUC12 polygons (seamless geodatabase)", lambda r: [r.national / "huc12.parquet"],
     "NHDPlus V2 seamless geodatabase"),
    ("attains", "ATTAINS assessment units (geodatabase)", lambda r: [r.national / "attains.parquet"],
     "ATTAINS national geodatabase"),
    ("dem1m_index", "3DEP 1 m tile catalog", lambda r: [r.dem1m_catalog], "S3 bucket listings"),
    ("dem19_index", "3DEP 1/9 arc-second quad catalog", lambda r: [r.dem19_catalog], "S3 bucket listing"),
    ("wqp_monthly", "WQP nutrient results, national by month (WQX3)",
     lambda r: [r.national / "wqp" / "wqp_results.parquet"], "Water Quality Portal WQX3, one month at a time"),
)
#: measured bytes on the wire per sampled reach by resolution (3DEP range
#: reads: 2.3 to 3.0 MB at 1 m, about 0.3 MB at 3 m, a few KB at 10 m)
WIRE_BYTES_PER_REACH = {"1": 3.0e6, "3": 0.3e6, "10": 0.02e6}
#: chunk acquisition stages and the raw files they leave
CHUNK_SOURCES = (("streamcat", ("streamcat",)), ("geometry", ("flowlines",)), ("huc12", ("huc12",)),
                 ("wqp", ("wqp_tn", "wqp_tp", "wqp_stations")), ("attains", ("attains",)), ("nas", ("nas",)))


def _size(paths) -> int:
    total = 0
    for path in paths:
        try:
            total += Path(path).stat().st_size
        except OSError:
            continue
    return total


def _mtime(paths) -> Optional[str]:
    stamps = []
    for path in paths:
        try:
            stamps.append(Path(path).stat().st_mtime)
        except OSError:
            continue
    return datetime.fromtimestamp(max(stamps)).strftime("%Y-%m-%d %H:%M") if stamps else None


def _rows(path: Path) -> Optional[int]:
    if not path.exists() or path.suffix != ".parquet":
        return None
    try:
        import pyarrow.parquet as pq
        return int(pq.read_metadata(path).num_rows)
    except Exception:  # noqa: BLE001
        return None


def _folder_bytes(folder: Path) -> int:
    total = 0
    if not folder.exists():
        return 0
    for base, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.stat(os.path.join(base, name)).st_size
            except OSError:
                continue
    return total


def national_datasets(root: DataRoot) -> list[dict]:
    """One row per national step: status, size, rows, updated, origin."""
    unit = UnitStates(root).unit("national")
    rows = []
    for step, label, files_for, origin in NATIONAL_STEPS:
        paths = [Path(p) for p in files_for(root)]
        present = [p for p in paths if p.exists()]
        marker = unit.get(step) or {}
        status = marker.get("status") or ("done" if present and present[0] == paths[0] else "missing")
        if status == "done" and (not present or not paths[0].exists()):
            status = "missing"
        row = {"step": step, "label": label, "origin": origin, "status": status,
               "size": _size(present), "rows": _rows(paths[0]),
               "updated": marker.get("at") or _mtime(present), "note": marker.get("note") or ""}
        if step == "wqp_monthly":
            row.update(_wqp_monthly_row(root, row))
        rows.append(row)
    return rows


def _wqp_monthly_row(root: DataRoot, row: dict) -> dict:
    """The monthly pull's ledger speaks for the step until the parquet exists."""
    from .stages import wqp_national
    s = wqp_national.summary(root)
    if s["combined"]:
        return {"note": f"{s['total']} months, {s['rows']:,} result rows"}
    if not s["done"] and not s["failing"]:
        return {}
    status = f"{s['done']}/{s['total']} months"
    return {"status": status, "size": s["bytes"], "rows": s["rows"],
            "updated": (wqp_national.read_ledger(root).get("updated") or "")[:16],
            "note": (f"{s['failing']} months retrying: {s['last_error']}" if s["failing"] and s["last_error"] else "")}


def _manifest(root: DataRoot) -> dict:
    try:
        return json.loads((root.staging / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def chunk_downloads(root: DataRoot) -> list[dict]:
    """One row per chunk with each source's status and raw size, the HUC8
    stages as k of n, and the published units; every CONUS state is listed,
    those without a chunk as not started."""
    from . import pipeline
    from .units import list_chunks
    states = UnitStates(root)
    units = _manifest(root).get("units") or {}
    rows = []
    seen: set[str] = set()
    for chunk in sorted(list_chunks(root), key=lambda c: c.id):
        status = pipeline.chunk_status(states, chunk)
        sources = {}
        for stage, names in CHUNK_SOURCES:
            files = [root.chunk_raw(chunk.id, name) for name in names]
            sources[stage] = {"status": status.get(stage, "pending"), "size": _size(f for f in files if f.exists())}
        huc8_stages = {}
        for stage in pipeline.HUC8_STAGES:
            done = sum(1 for h in chunk.huc8s if (states.unit(h).get(stage) or {}).get("status") == "done")
            huc8_stages[stage] = {"done": done, "total": len(chunk.huc8s), "status": status.get(stage, "pending")}
        huc4s = sorted({h[:4] for h in chunk.huc8s})
        published = [h for h in huc4s if h in units]
        rows.append({"chunk": chunk.id, "label": chunk.label, "kind": chunk.kind, "states": list(chunk.states),
                     "huc8s": len(chunk.huc8s), "reaches": chunk.n_comids, "sources": sources,
                     "huc8_stages": huc8_stages, "raw_size": _folder_bytes(root.chunk_dir(chunk.id) / "raw"),
                     "published_huc4s": len(published), "huc4s": len(huc4s),
                     "status": "downloaded" if all(s["status"] == "done" for s in sources.values()) else "partial"})
        seen.update(chunk.states)
    for abbr in CONUS_STATES:
        if abbr not in seen:
            rows.append({"chunk": f"state-{abbr}", "label": abbr, "kind": "state", "states": [abbr], "huc8s": None,
                         "reaches": None, "sources": {}, "huc8_stages": {}, "raw_size": 0,
                         "published_huc4s": 0, "huc4s": 0, "status": "not started"})
    return rows


def xs_inventory(root: DataRoot) -> list[dict]:
    """The cross-section archive per chunk: HUC8s sampled and derived, reaches
    sampled by resolution, archive bytes, estimated bytes downloaded, status."""
    import pyarrow.parquet as pq
    from .units import list_chunks
    states = UnitStates(root)
    rows = []
    seen: set[str] = set()
    for chunk in sorted(list_chunks(root), key=lambda c: c.id):
        sampled = derived = 0
        n_ok = n_1m = n_3m = n_10m = 0
        archive_bytes = 0
        bytes_est = 0
        stamps = []
        running = paused = False
        for huc8 in chunk.huc8s:
            marker = states.unit(huc8)
            sample = marker.get("xs_sample") or {}
            if sample.get("status") == "done":
                sampled += 1
            running |= sample.get("status") == "running"
            paused |= "paused" in str(sample.get("note") or "")
            if (marker.get("xs_derive") or {}).get("status") == "done":
                derived += 1
            summary = root.huc8_file(huc8, "xs_sample")
            if summary.exists():
                table = pq.read_table(summary, columns=["status", "dem_res_m", "bytes_est"])
                for status, res, est in zip(table.column("status").to_pylist(), table.column("dem_res_m").to_pylist(),
                                            table.column("bytes_est").to_pylist()):
                    if status != "ok":
                        continue
                    n_ok += 1
                    if res == 1:
                        n_1m += 1
                    elif res == 3:
                        n_3m += 1
                    elif res == 10:
                        n_10m += 1
                stamps.append(summary.stat().st_mtime)
            elif sample.get("status") == "running":
                # in flight: the heartbeat counts the batches written so far
                beat = root.huc8_dir(huc8) / "xs_sample.progress.json"
                try:
                    j = json.loads(beat.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    j = {}
                n_ok += int(j.get("done") or 0)
                n_1m += int(j.get("n_1m") or 0)
                n_3m += int(j.get("n_3m") or 0)
                n_10m += int(j.get("n_10m") or 0)
                if beat.exists():
                    stamps.append(beat.stat().st_mtime)
            archive = root.huc8_file(huc8, "xs_profiles")
            if archive.exists():
                archive_bytes += archive.stat().st_size
        # the per-reach accounting in the summaries double counts under a pool
        # (every process sees the shared counter move), so the estimate is the
        # measured wire cost per resolution
        bytes_est = int(n_1m * WIRE_BYTES_PER_REACH["1"] + n_3m * WIRE_BYTES_PER_REACH["3"] + n_10m * WIRE_BYTES_PER_REACH["10"])
        total = len(chunk.huc8s)
        if running:
            status = "sampling"
        elif paused:
            status = "paused"
        elif total and sampled == total:
            status = "done" if derived == total else "sampled"
        elif sampled:
            status = "partial"
        else:
            status = "not started"
        rows.append({"chunk": chunk.id, "label": chunk.label, "states": list(chunk.states), "huc8s": total,
                     "sampled": sampled, "derived": derived, "reaches": chunk.n_comids, "reaches_sampled": n_ok,
                     "n_1m": n_1m, "n_3m": n_3m, "n_10m": n_10m, "archive_bytes": archive_bytes,
                     "bytes_est": bytes_est, "status": status,
                     "last_activity": datetime.fromtimestamp(max(stamps)).strftime("%Y-%m-%d %H:%M") if stamps else None})
        seen.update(chunk.states)
    for abbr in CONUS_STATES:
        if abbr not in seen:
            rows.append({"chunk": f"state-{abbr}", "label": abbr, "states": [abbr], "huc8s": None, "sampled": 0,
                         "derived": 0, "reaches": None, "reaches_sampled": 0, "n_1m": 0, "n_3m": 0, "n_10m": 0,
                         "archive_bytes": 0, "bytes_est": 0, "status": "not started", "last_activity": None})
    return rows


def catalog_status(root: DataRoot) -> dict:
    out = {}
    for key, meta_path, catalog_path in (("1m", root.dem1m_meta, root.dem1m_catalog),
                                         ("19", root.dem19_meta, root.dem19_catalog)):
        meta = {}
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        out[key] = {"present": catalog_path.exists(), "built_at": meta.get("built_at"),
                    "tiles": meta.get("tiles") or meta.get("quads"), "projects": meta.get("projects"),
                    "skipped": len(meta.get("skipped") or {}) if isinstance(meta.get("skipped"), dict) else 0,
                    "size": _size([catalog_path])}
    return out


def bandwidth_status(root: DataRoot) -> dict:
    counter = Bandwidth(root)
    months = counter.totals()
    month = Bandwidth.month()
    used = int(months.get(month, {}).get("bytes", 0))
    budget = float(config.XS_BYTE_BUDGET_GB) * 1e9
    return {"month": month, "bytes": used, "budget_bytes": budget,
            "fraction": min(1.0, used / budget) if budget else 0.0,
            "reaches": int(months.get(month, {}).get("reaches", 0)),
            "months": {k: dict(v) for k, v in sorted(months.items())}}


def disk_status(root: DataRoot) -> dict:
    usage = shutil.disk_usage(root.root)
    return {"free": usage.free, "total": usage.total,
            "folders": {name: _folder_bytes(getattr(root, name)) for name in
                        ("national", "chunks", "huc8", "tiles", "staging")}}

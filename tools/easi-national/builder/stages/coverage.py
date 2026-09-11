"""Staging: assemble the exact asset set of the next publish from every
scored HUC8: per-HUC4 evidence files, per-region score files, the region
tiles, the COMID index, the HUC4 coverage polygons, and the manifest."""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path

from .. import config
from ..paths import DataRoot, atomic_write_text
from ..state import Progress, UnitStates, now_iso
from ..units import load_huc4_vpu, load_huc8_index
from . import xs_derive

VINTAGE = "2026.09"
UNITS_TOTAL = 222


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _asset(path: Path) -> dict:
    return {"asset": path.name, "sha256": sha256_of(path), "bytes": path.stat().st_size}


def scored_by_huc4(root: DataRoot) -> dict[str, list[str]]:
    index = load_huc8_index(root)
    out: dict[str, list[str]] = defaultdict(list)
    for huc8 in sorted(index):
        if root.huc8_file(huc8, "scores").exists() and root.huc8_file(huc8, "evidence").exists():
            out[huc8[:4]].append(huc8)
    return dict(out)


def run_staging(root: DataRoot, states: UnitStates, progress: Progress) -> dict:
    """Rebuild ``staging/`` and return the manifest."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from easi.national import SCHEMA_VERSION, method_version
    staging = root.staging
    staging.mkdir(parents=True, exist_ok=True)
    index = load_huc8_index(root)
    huc4_vpu = load_huc4_vpu(root)
    by_huc4 = scored_by_huc4(root)
    progress.begin("staging", "stage", total=len(by_huc4) + 2, message="assembling the publish set")
    units: dict[str, dict] = {}
    keep: set[str] = set()
    per_vpu: dict[str, list] = defaultdict(list)
    for i, (huc4, huc8s) in enumerate(sorted(by_huc4.items())):
        tables = [pq.read_table(root.huc8_file(h, "evidence")) for h in huc8s]
        evidence = pa.concat_tables(tables, promote_options="default")
        name = f"evidence_{huc4}.parquet"
        tmp = staging / (name + ".part")
        pq.write_table(evidence, tmp, compression="zstd")
        tmp.replace(staging / name)
        keep.add(name)
        all_huc8s = [h for h in index if h.startswith(huc4)]
        n_comids = sum(int(index[h]["n"]) for h in all_huc8s)
        n_scored = int(evidence.num_rows)
        complete = set(huc8s) >= set(all_huc8s)
        vpu = huc4_vpu.get(huc4) or huc4[:2]
        xs = unit_cross_sections(root, huc8s, evidence)
        units[huc4] = {"vpu": vpu, "status": "complete" if complete else "partial",
                       "n_comids": n_comids, "n_scored": n_scored,
                       "huc8s_scored": len(huc8s), "huc8s_total": len(all_huc8s),
                       "fraction": round(n_scored / n_comids, 4) if n_comids else None,
                       "published_at": now_iso(), "evidence": _asset(staging / name), **xs}
        for h in huc8s:
            per_vpu[vpu].append(h)
        progress.tick(done=i + 1, message=f"evidence_{huc4}: {n_scored:,} reaches")
    scores_block: dict[str, dict] = {}
    for vpu, huc8s in sorted(per_vpu.items()):
        tables = [pq.read_table(root.huc8_file(h, "scores")) for h in sorted(huc8s)]
        table = pa.concat_tables(tables, promote_options="default")
        name = f"scores_{vpu}.parquet"
        tmp = staging / (name + ".part")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(staging / name)
        keep.add(name)
        scores_block[vpu] = {**_asset(staging / name), "n_scored": int(table.num_rows)}
    tiles_block: dict[str, dict] = {}
    if root.tiles.exists():
        for folder in sorted(root.tiles.iterdir()):
            pm = folder / f"tiles_{folder.name}.pmtiles"
            meta = folder / "tiles.json"
            if pm.exists():
                shutil.copyfile(pm, staging / pm.name)
                keep.add(pm.name)
                info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
                tiles_block[folder.name] = {**_asset(staging / pm.name),
                                            "built_at": now_iso(),
                                            "huc4s": sorted({h[:4] for h in info.get("huc8s", [])}),
                                            "n_lines": info.get("n_lines"),
                                            "n_scored": info.get("n_scored"),
                                            "minzoom": info.get("minzoom", 4),
                                            "maxzoom": info.get("maxzoom", 12)}
    shutil.copyfile(root.index, staging / "comid_huc4.parquet")
    keep.add("comid_huc4.parquet")
    coverage = _coverage(root, units)
    atomic_write_text(staging / "coverage.geojson", json.dumps(coverage, separators=(",", ":")))
    keep.add("coverage.geojson")
    tiers = {"1": sum(1 for u in units.values() if u.get("tier") == 1),
             "2": sum(1 for u in units.values() if u.get("tier") == 2)}
    manifest = {
        "schema_version": SCHEMA_VERSION, "dataset": "easi-national", "vintage": VINTAGE,
        "tier": min((u.get("tier") or config.BASE_TIER) for u in units.values()) if units else config.BASE_TIER,
        "tiers": tiers, "reach_length_ft": config.REACH_LENGTH_FT,
        "method_version": method_version(), "xs_method_version": xs_derive.xs_method_version(),
        "dem": {"source": "USGS 3DEP", "resolutions_m": [1, 3, 10],
                "rule": "1 m where a 3DEP lidar project covers at least half the reach buffer, "
                        "else the 1/9 arc-second (3 m) quads where they exist, else the 10 m seamless"},
        "reach_source": "nhdplus_v2_flowline", "updated": now_iso(),
        "units_total": UNITS_TOTAL, "units": units, "scores": scores_block,
        "tiles": tiles_block,
        "assets": {"comid_huc4.parquet": _asset(staging / "comid_huc4.parquet"),
                   "coverage.geojson": _asset(staging / "coverage.geojson")},
        "reaches_scored": sum(u["n_scored"] for u in units.values()),
    }
    atomic_write_text(staging / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True))
    keep.add("manifest.json")
    for path in staging.iterdir():
        if path.is_file() and path.name not in keep and not path.name.endswith(".part"):
            path.unlink()
    progress.say(f"staging: {len(units)} HUC4 units, {manifest['reaches_scored']:,} reaches, "
                 f"{len(tiles_block)} tile archives")
    states.set("staging", "stage", "done", note=f"{len(units)} units")
    return manifest


def unit_cross_sections(root: DataRoot, huc8s: list[str], evidence) -> dict:
    """The unit's tier and DEM resolution counts: Tier 2 when every scored
    reach carries a cross-section record (an empty one counts: sampling ran)."""
    import pyarrow.parquet as pq
    n_scored = int(evidence.num_rows)
    n_geomorph = 0
    if "geomorph" in evidence.column_names:
        column = evidence.column("geomorph")
        n_geomorph = n_scored - int(column.null_count)
    n_1m = n_3m = n_10m = 0
    for huc8 in huc8s:
        path = root.huc8_file(huc8, "scores")
        schema = pq.read_schema(path)
        if "dem_res_m" not in schema.names:
            continue
        for value in pq.read_table(path, columns=["dem_res_m"]).column("dem_res_m").to_pylist():
            if value == 1:
                n_1m += 1
            elif value == 3:
                n_3m += 1
            elif value == 10:
                n_10m += 1
    tier = config.XS_TIER if n_scored and n_geomorph == n_scored else config.BASE_TIER
    return {"tier": tier, "n_geomorph": n_geomorph, "n_1m": n_1m, "n_3m": n_3m, "n_10m": n_10m}


def _coverage(root: DataRoot, units: dict[str, dict]) -> dict:
    features = []
    try:
        polygons = json.loads(root.huc4_geojson.read_text(encoding="utf-8")).get("features") or []
    except (OSError, ValueError):
        polygons = []
    for feature in polygons:
        huc4 = str((feature.get("properties") or {}).get("huc4") or "")
        unit = units.get(huc4) or {}
        features.append({"type": "Feature", "geometry": feature.get("geometry"),
                         "properties": {"huc4": huc4, "status": unit.get("status", "not_started"),
                                        "n_comids": unit.get("n_comids"),
                                        "n_scored": unit.get("n_scored", 0),
                                        "fraction": unit.get("fraction", 0.0),
                                        "tier": unit.get("tier"),
                                        "published_at": unit.get("published_at")}})
    return {"type": "FeatureCollection", "features": features}

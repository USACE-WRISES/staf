"""Cross-section sampling: the raw elevation profiles of nine transects per
reach from the best available 3DEP elevation, kept as the archive.

Per reach: the 1,000 ft reach line from the local flowlines (``reaches``),
the app's buffer rule (eight bankfull widths, 250 to 800 m), the best
available DEM for that buffer (``dem``: 1 m project tiles by range requests,
else the 10 m seamless through py3dep), and the app's transect placement
copied from ``easi.datasources.threedep.reach_geomorphology``: nine
transects at one-tenth intervals, a 5 m forward chord for the normal,
samples at the DEM's spacing along ``linspace(-wide, wide)``, bilinear.
Nothing is derived here; ``xs_derive`` turns the archive into the metrics.

Archive ``huc8/<huc8>/xs_profiles.parquet``: one row per transect with the
elevations as ``list<float64>`` (byte-stream-split under zstd; float32 storage
flipped a bank threshold on 1 of 10 real reaches), the transect
placement in EPSG:5070 and the DEM provenance. ``xs_sample.parquet`` holds
one row per reach (status, resolution, source, timing) and
``reaches.parquet`` the reach lines. Resumable per batch; paced by the
monthly byte budget (``state.Bandwidth``).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from .. import config, dem, reaches
from ..network import NetworkIndex
from ..paths import DataRoot, atomic_write_text
from ..state import (Bandwidth, CancelRequested, Control, Ledger, PauseRequested, Progress,
                     UnitStates, digest, now_iso)
from ..units import Chunk
from . import common, local_gdb

STAGE = "xs_sample"
BATCH = 100
#: the app's rules (threedep.reach_geomorphology and geomorph.XS_COUNT)
SPACING_M = 10.0
N_TRANSECTS = 9
MAX_POINTS = 2001
CHORD_M = 5.0
WIDE_FACTOR, WIDE_MIN_M, WIDE_MAX_M = 8.0, 250.0, 800.0
PROFILE_COLUMNS = ("comid", "k", "frac", "x0", "y0", "nx", "ny", "wide_m", "step_m", "n_pts",
                   "dem_res_m", "dem_source", "tiles", "dem_last_modified", "n_finite", "z", "sampled_at")


#: The sampling rules' version. Bump it when the placement (transect
#: fractions, chord, spacing, buffer), the archive schema or the DEM reading
#: rules change: a bump marks every sampled HUC8 stale (hours of range reads
#: per state), so bookkeeping edits must leave it alone.
SAMPLING_VERSION = "2026-09-11.1"


def sampling_version() -> str:
    return SAMPLING_VERSION


# ------------------------------------------------------------- placement
def buffer_half_width(bankfull_width_m: Optional[float], da_sqkm: float) -> float:
    """``wide`` as the app computes it: eight bankfull widths, 250 to 800 m."""
    from easi import geomorph
    w_bf = bankfull_width_m if bankfull_width_m else geomorph.bankfull_geometry(da_sqkm or 1.0)[0]
    return min(max(WIDE_FACTOR * float(w_bf), WIDE_MIN_M), WIDE_MAX_M)


def transects(line5070, wide: float, dem_res: float) -> list[dict]:
    """The nine transects the app places: midpoint, unit normal, spacing."""
    import numpy as np
    step = min(SPACING_M, float(dem_res))
    n_pts = min(int(2 * wide / step) + 1, MAX_POINTS)
    out = []
    for k, frac in enumerate(np.linspace(0.0, 1.0, N_TRANSECTS + 2)[1:-1]):
        s = line5070.length * float(frac)
        p = line5070.interpolate(s)
        p2 = line5070.interpolate(min(s + CHORD_M, line5070.length))
        dx, dy = p2.x - p.x, p2.y - p.y
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        out.append({"k": k, "frac": float(frac), "x0": float(p.x), "y0": float(p.y),
                    "nx": float(-dy / norm), "ny": float(dx / norm), "step_m": float(step), "n_pts": int(n_pts)})
    return out


def sample_transect(dem_da, transect: dict, wide: float):
    """Elevations along the transect, bilinear on the EPSG:5070 grid (the
    app's ``dem.interp`` call), NaN where the grid has none."""
    import numpy as np
    import xarray as xr
    ts = np.linspace(-wide, wide, int(transect["n_pts"]))
    z = dem_da.interp(x=xr.DataArray(transect["x0"] + transect["nx"] * ts, dims="t"),
                      y=xr.DataArray(transect["y0"] + transect["ny"] * ts, dims="t")).values
    return np.asarray(z, dtype=float)


def reach_geometry(feature_collection: dict):
    """``(line in EPSG:5070, buffer polygon in EPSG:4326)`` from the reach."""
    import geopandas as gpd
    from shapely.geometry import LineString
    coords = feature_collection["features"][0]["geometry"]["coordinates"]
    line = gpd.GeoSeries([LineString(coords)], crs=4326).to_crs(5070).iloc[0]
    return line


def sample_reach(feature_collection: dict, wide: float, dem_source: Callable) -> tuple[list[dict], dict]:
    """All nine transects of one reach: ``(rows, meta)`` with the raw samples."""
    import geopandas as gpd
    import numpy as np
    line = reach_geometry(feature_collection)
    buf4326 = gpd.GeoSeries([line.buffer(wide)], crs=5070).to_crs(4326).iloc[0]
    dem_da, res, provenance = dem_source(buf4326)
    rows = []
    for transect in transects(line, wide, res):
        z = sample_transect(dem_da, transect, wide)
        rows.append({**transect, "wide_m": float(wide), "dem_res_m": float(res),
                     "z": np.asarray(z, dtype="float64"), "n_finite": int(np.isfinite(z).sum())})
    return rows, {"res": float(res), "provenance": provenance, "line_length_m": float(line.length),
                  "dem": dem_da}


# --------------------------------------------------------------- caches
_GEOMS: dict = {}


def chunk_geometries(root: DataRoot, chunk: Chunk) -> dict:
    """``{comid: geometry}`` of the chunk's flowlines, once per process."""
    hit = _GEOMS.get(chunk.id)
    if hit is not None:
        return hit
    import geopandas as gpd
    gdf = gpd.read_parquet(root.chunk_raw(chunk.id, "flowlines"), columns=["comid", "geometry"])
    geoms = {int(c): g for c, g in zip(gdf["comid"].tolist(), gdf.geometry.tolist())}
    _GEOMS.clear()
    _GEOMS[chunk.id] = geoms
    return geoms


def _profile_schema():
    import pyarrow as pa
    return pa.schema([
        ("comid", pa.int64()), ("k", pa.int8()), ("frac", pa.float64()), ("x0", pa.float64()),
        ("y0", pa.float64()), ("nx", pa.float64()), ("ny", pa.float64()), ("wide_m", pa.float64()),
        ("step_m", pa.float64()), ("n_pts", pa.int32()), ("dem_res_m", pa.float64()),
        ("dem_source", pa.string()), ("tiles", pa.string()), ("dem_last_modified", pa.string()),
        ("n_finite", pa.int32()), ("z", pa.list_(pa.float64())), ("sampled_at", pa.string())])


def _summary_schema():
    import pyarrow as pa
    return pa.schema([
        ("comid", pa.int64()), ("status", pa.string()), ("dem_res_m", pa.float64()),
        ("dem_source", pa.string()), ("n_transects_sampled", pa.int32()), ("reach_length_ft", pa.float64()),
        ("line_length_m", pa.float64()), ("bytes_est", pa.int64()), ("error", pa.string()),
        ("elapsed_ms", pa.int32())])


def _write_profiles(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    columns = {name: [r.get(name) for r in rows] for name in PROFILE_COLUMNS if name != "z"}
    arrays = {name: pa.array(values, type=_profile_schema().field(name).type) for name, values in columns.items()}
    arrays["z"] = pa.array([r["z"] for r in rows], type=pa.list_(pa.float64()))
    table = pa.table(arrays).select(list(PROFILE_COLUMNS))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    pq.write_table(table, tmp, compression="zstd", use_byte_stream_split=True)
    tmp.replace(path)


def _keep_window(dem_da, path: Path) -> None:
    """Opt-in: one reach's DEM window (EPSG:5070) as a deflate GeoTIFF."""
    if dem_da is None:
        return
    import rioxarray  # noqa: F401 - the rio accessor
    da = dem_da if dem_da.rio.crs is not None else dem_da.rio.write_crs(5070)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    da.rio.to_raster(tmp, driver="GTiff", compress="deflate", tiled=True)
    tmp.replace(path)


def _source_label(provenance: dict) -> str:
    if provenance.get("source") == "1m":
        return f"1m:{provenance.get('project', '')}"
    if provenance.get("source") == "19":
        quads = provenance.get("quads") or []
        return f"3m:{quads[0] if quads else ''}"
    return "seamless13"


# ---------------------------------------------------------------- stage
def run_xs_sample(root: DataRoot, chunk: Chunk, huc8: str, states: UnitStates, progress: Progress,
                  control: Control, *, force: bool = False, dem_source: Optional[Callable] = None,
                  bandwidth: Optional[Bandwidth] = None, keep_dem_windows: bool = False) -> None:
    """Sample the nine transects of every reach of the HUC8 from the best
    available 3DEP elevation into the archive. ``keep_dem_windows`` also
    writes each reach's DEM window as a GeoTIFF under ``dem_windows/`` (1 to
    3 MB per reach at 1 m; off by default)."""
    meta = json.loads(root.dem1m_meta.read_text(encoding="utf-8")) if root.dem1m_meta.exists() else {}
    inputs = digest(STAGE, huc8, chunk.id, meta.get("built_at", ""), config.REACH_LENGTH_FT,
                    sampling_version(), 1)
    derived_path = root.huc8_file(huc8, "derived")
    if not derived_path.exists():
        raise RuntimeError("the derive stage must run first")

    def work():
        import geopandas as gpd
        import pyarrow as pa
        import pyarrow.parquet as pq
        from shapely.geometry import shape
        derived = pq.read_table(derived_path, columns=["comid", "lat", "lon", "totdasqkm", "bankfull"]).to_pylist()
        derived.sort(key=lambda d: int(d["comid"]))
        network = NetworkIndex.load(root)
        geoms = dict(chunk_geometries(root, chunk))
        chains = {}
        for d in derived:
            comid = int(d["comid"])
            own = reaches.explode([geoms.get(comid)])
            chains[comid] = reaches.chain_for(network, comid, reaches.own_length_km(own))
        missing = reaches.missing_comids(chains, geoms)
        if missing:                                    # upstream reaches beyond the chunk: never clipped
            extra = local_gdb.flowlines_for(root, missing)
            if extra is not None:
                geoms.update({int(c): g for c, g in zip(extra["comid"].tolist(), extra.geometry.tolist())})
        catalog = dem.Catalog.load(root)
        catalog19 = dem.Catalog19.load(root)
        budget = bandwidth if bandwidth is not None else Bandwidth(root)
        meter = {"bytes": 0}                        # this reach's reads, this process only

        def account(n):
            meter["bytes"] += int(n)
            budget.add(n, reaches=0)
        source = dem_source or (lambda buf: dem.best_available_dem(
            buf, catalog=catalog, catalog19=catalog19, accounting=account))
        ledger = Ledger(root, f"{STAGE}-{huc8}")
        parts = root.huc8_parts(huc8, STAGE)
        parts.mkdir(parents=True, exist_ok=True)
        # parts left by an earlier sampling version (a different inputs digest)
        # would not concatenate with the new ones: discard them and start over
        stamp_path = parts / "_inputs.json"
        stamp = json.dumps({"inputs": inputs, "z": "float64"})
        old_stamp = stamp_path.read_text(encoding="utf-8") if stamp_path.exists() else None
        if old_stamp != stamp and len(ledger):
            progress.say(f"xs_sample {huc8}: {len(ledger)} batches came from another sampling version; discarded")
            ledger.clear()
            common.drop_parts(parts)
            parts.mkdir(parents=True, exist_ok=True)
        atomic_write_text(stamp_path, stamp)
        batches = [derived[i:i + BATCH] for i in range(0, len(derived), BATCH)]
        pending = [(f"b{i:05d}", batch) for i, batch in enumerate(batches) if f"b{i:05d}" not in ledger]
        done = len(derived) - sum(len(b) for _k, b in pending)
        counts = {"1": 0, "3": 0, "10": 0, "other": 0}
        bytes_this_run = 0
        progress.begin(huc8, STAGE, total=len(derived),
                       message=f"cross-section sampling {huc8}: {len(derived):,} reaches, {len(pending)} batches to go")
        progress.tick(done=done)
        progress_path = root.huc8_dir(huc8) / "xs_sample.progress.json"

        def heartbeat():
            atomic_write_text(progress_path, json.dumps({
                "done": done, "total": len(derived), "n_1m": counts["1"], "n_3m": counts["3"],
                "n_10m": counts["10"],
                "bytes_this_run": bytes_this_run, "at": now_iso()}))

        for key, batch in pending:
            control.check()
            budget.check()
            profile_rows, summary_rows, reach_rows = [], [], []
            for d in batch:
                t0 = time.monotonic()
                comid = int(d["comid"])
                fc, actual_ft, warnings, chain = reaches.reach_line(comid, float(d["lat"]), float(d["lon"]),
                                                                    geoms, network)
                if fc is None:
                    summary_rows.append({"comid": comid, "status": "no_reach", "dem_res_m": None, "dem_source": None,
                                         "n_transects_sampled": 0, "reach_length_ft": None, "line_length_m": None,
                                         "bytes_est": 0, "error": "; ".join(warnings)[:300],
                                         "elapsed_ms": int((time.monotonic() - t0) * 1000)})
                    continue
                reach_rows.append({"comid": comid, "reach_length_ft": actual_ft, "warnings": json.dumps(warnings),
                                   "chain": json.dumps(chain), "geometry": shape(fc["features"][0]["geometry"])})
                block = json.loads(d.get("bankfull") or "{}") if isinstance(d.get("bankfull"), str) else (d.get("bankfull") or {})
                wide = buffer_half_width(block.get("width_m"), float(d.get("totdasqkm") or 0.0))
                meter["bytes"] = 0
                try:
                    rows, sample_meta = sample_reach(fc, wide, source)
                except (PauseRequested, CancelRequested):
                    raise
                except Exception as exc:  # noqa: BLE001 - one reach, recorded, never silently empty
                    summary_rows.append({"comid": comid, "status": "error", "dem_res_m": None, "dem_source": None,
                                         "n_transects_sampled": 0, "reach_length_ft": actual_ft,
                                         "line_length_m": None, "bytes_est": 0,
                                         "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                                         "elapsed_ms": int((time.monotonic() - t0) * 1000)})
                    continue
                if keep_dem_windows:
                    _keep_window(sample_meta.get("dem"), root.huc8_dir(huc8) / "dem_windows" / f"{comid}.tif")
                used = meter["bytes"]
                bytes_this_run += used
                provenance = sample_meta["provenance"]
                label = _source_label(provenance)
                stamp = now_iso()
                for r in rows:
                    profile_rows.append({**{k: r[k] for k in ("k", "frac", "x0", "y0", "nx", "ny", "wide_m", "step_m",
                                                             "n_pts", "dem_res_m", "n_finite", "z")},
                                         "comid": comid, "dem_source": label,
                                         "tiles": json.dumps(provenance.get("tiles") or provenance.get("quads") or []),
                                         "dem_last_modified": provenance.get("last_modified") or "",
                                         "sampled_at": stamp})
                res_key = {1: "1", 3: "3", 10: "10"}.get(int(sample_meta["res"]) if float(sample_meta["res"]).is_integer() else -1, "other")
                counts[res_key] += 1
                summary_rows.append({"comid": comid, "status": "ok", "dem_res_m": sample_meta["res"],
                                     "dem_source": label, "n_transects_sampled": len(rows),
                                     "reach_length_ft": actual_ft, "line_length_m": sample_meta["line_length_m"],
                                     "bytes_est": int(used), "error": None,
                                     "elapsed_ms": int((time.monotonic() - t0) * 1000)})
                budget.add(0, reaches=1)
            if profile_rows:
                _write_profiles(profile_rows, parts / f"{key}.profiles.parquet")
            common.write_parquet(pa.Table.from_pylist(summary_rows, schema=_summary_schema()),
                                 parts / f"{key}.summary.parquet")
            if reach_rows:
                common.write_parquet(gpd.GeoDataFrame(reach_rows, geometry="geometry", crs="EPSG:4326"),
                                     parts / f"{key}.reaches.parquet")
            ledger.add(key, n=len(batch))
            done += len(batch)
            heartbeat()
            progress.tick(done=done, message=f"cross-section sampling {huc8}: {done:,} of {len(derived):,} reaches, "
                                              f"1 m on {counts['1']:,}, 3 m on {counts['3']:,}, 10 m on {counts['10']:,}")
        budget.flush()
        # assemble
        profile_tables = [pq.read_table(p) for p in sorted(parts.glob("*.profiles.parquet"))]
        summary_tables = [pq.read_table(p) for p in sorted(parts.glob("*.summary.parquet"))]
        reach_frames = [gpd.read_parquet(p) for p in sorted(parts.glob("*.reaches.parquet"))]
        profiles = pa.concat_tables(profile_tables) if profile_tables else pa.Table.from_pylist([], schema=_profile_schema())
        summary = pa.concat_tables(summary_tables) if summary_tables else pa.Table.from_pylist([], schema=_summary_schema())
        out = root.huc8_file(huc8, "xs_profiles")
        tmp = out.with_name(out.name + ".part")
        pq.write_table(profiles.sort_by([("comid", "ascending"), ("k", "ascending")]), tmp,
                       compression="zstd", use_byte_stream_split=True)
        tmp.replace(out)
        common.write_parquet(summary.sort_by("comid"), root.huc8_file(huc8, "xs_sample"))
        if reach_frames:
            import pandas as pd
            frame = gpd.GeoDataFrame(pd.concat(reach_frames, ignore_index=True), geometry="geometry", crs="EPSG:4326")
            common.write_parquet(frame.sort_values("comid").reset_index(drop=True), root.huc8_file(huc8, "reaches"))
        statuses = summary.column("status").to_pylist()
        progress.say(f"xs_profiles.parquet {huc8}: {profiles.num_rows:,} transects of "
                     f"{statuses.count('ok'):,} reaches (1 m {counts['1']:,}, 3 m {counts['3']:,}, 10 m {counts['10']:,}; "
                     f"no reach {statuses.count('no_reach')}, errors {statuses.count('error')}), "
                     f"{out.stat().st_size / 1e6:.1f} MB")
        common.drop_parts(parts)
        ledger.clear()
        try:
            progress_path.unlink()
        except OSError:
            pass

    common.run_stage(states, huc8, STAGE, inputs, work, progress, force=force)

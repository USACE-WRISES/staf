"""Per-catchment soil K (bundle v2, Phase 3).

Map units come from USDA gNATSGO (July 2020), whose 10 m map-unit grid is the catchments' own grid,
so every catchment cell has exactly one map unit. K per map unit follows the site engine
(``metrics/soils.py``): the surface horizon (``hzdept_r = 0``) ``kwfact`` of the map unit's major
components, weighted by component percent, asked from Soil Data Access with the engine's own SQL
(current SSURGO). The engine intersects SSURGO polygons only, so the STATSGO fill of gNATSGO
(legend area symbol ``US``) is kept apart and gives no K; map units retired since 2020 give no K
either, and are reported.

``soils2_<vpu>.parquet``, one row per catchment: ``k_sum`` (K summed over the cells that have one),
``k_cells``, ``ssurgo_cells`` (cells with a SSURGO map unit, the engine's area for the coverage
warning) and ``statsgo_cells``. A watershed's K is ``sum(k_sum) / sum(k_cells)``.

Survey areas re-mapped since 2020 retire every 2020 map unit, so gNATSGO 2020 has no current K
there. ``retired_tiles`` marks the 5 km tiles of the grid holding retired cells, ``ssurgo_patch``
asks Soil Data Access for the current SSURGO polygons clipped to each tile (the polygons the
engine intersects) and burns their map units onto the same 10 m grid (cell centres, as gNATSGO is
made), and ``soils`` then reads a retired cell's map unit from the patch.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import sources
from .zonal import GRID10, RegionCells, log_default

GNATSGO_NODATA = 2147483647
SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
SDA_CHUNK = 500
PATCH_TILE = 500          # cells: 5 km tiles of the 10 m grid


def gnatsgo_files(root: Path = sources.SOURCES_DIR) -> list[dict]:
    """The downloaded map-unit windows: path and window in 10 m cells (index cached)."""
    import rasterio
    folder = root / "gnatsgo" / "mukey"
    index_path = folder / "index.json"
    known = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    out, changed = [], False
    for path in sorted(folder.glob("*.tif")):
        if path.name not in known:
            with rasterio.open(path) as src:
                t = src.transform
                ix = (t.c - GRID10.offset) / GRID10.cell
                iy = (t.f - GRID10.offset) / GRID10.cell
                if abs(t.a - 10) > 1e-9 or abs(t.e + 10) > 1e-9 or abs(ix - round(ix)) > 1e-6 or abs(iy - round(iy)) > 1e-6:
                    raise RuntimeError(f"{path.name}: not on the catchments' 10 m grid ({t})")
                known[path.name] = dict(ix0=int(round(ix)), iy1=int(round(iy)), w=src.width, h=src.height)
            changed = True
        out.append(dict(known[path.name], path=path))
    if changed:
        index_path.write_text(json.dumps(known, indent=1), encoding="utf-8")
    return out


def statsgo_mukeys(root: Path = sources.SOURCES_DIR) -> np.ndarray:
    """Map units of the STATSGO fill (legend area symbol ``US``)."""
    tables = root / "gnatsgo" / "tables"
    mu = pq.read_table(sorted((tables / "mapunit").glob("part*"))[0], columns=["mukey", "lkey"]).to_pandas()
    lg = pq.read_table(sorted((tables / "legend").glob("part*"))[0], columns=["lkey", "areasymbol"]).to_pandas()
    us = set(lg.loc[lg["areasymbol"].astype(str).str.upper() == "US", "lkey"].astype(str))
    keys = pd.to_numeric(mu.loc[mu["lkey"].astype(str).isin(us), "mukey"], errors="coerce").dropna()
    return np.unique(keys.astype(np.int64).to_numpy())


def _sda_sql(chunk: list[str]) -> str:
    keys = ", ".join("'" + k + "'" for k in chunk)
    return ("SELECT c.mukey, ch.kwfact, c.comppct_r FROM component c JOIN chorizon ch ON ch.cokey = c.cokey "
            "WHERE c.mukey IN (" + keys + ") AND c.majcompflag = 'Yes' AND ch.hzdept_r = 0")


def sda_kwfact(mukeys, root: Path = sources.SOURCES_DIR, log: Callable = log_default) -> dict:
    """``{mukey: K or None}`` by the engine's rule, from Soil Data Access; cached in
    ``gnatsgo/kwfact_sda.json`` so a map unit is asked once."""
    import requests
    cache_path = root / "gnatsgo" / "kwfact_sda.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    want = sorted(str(int(m)) for m in mukeys if str(int(m)) not in cache)
    for i in range(0, len(want), SDA_CHUNK):
        chunk = want[i:i + SDA_CHUNK]
        rows = None
        for attempt in range(1, 7):
            try:
                r = requests.post(SDA_URL, json={"query": _sda_sql(chunk), "format": "JSON+COLUMNNAME"}, timeout=180)
                if r.status_code == 200:
                    rows = (r.json().get("Table") or [])[1:]
                    break
                log(f"soil K: Soil Data Access HTTP {r.status_code}, retrying")
            except Exception as exc:  # noqa: BLE001
                log(f"soil K: Soil Data Access attempt {attempt}: {exc}")
            time.sleep(10 * attempt)
        if rows is None:
            raise RuntimeError("Soil Data Access did not answer")
        acc: dict = {}
        for row in rows:
            try:
                key, k = str(row[0]), float(row[1])
                pct = float(row[2]) if row[2] not in (None, "") else 1.0
            except (TypeError, ValueError, IndexError):
                continue
            a = acc.setdefault(key, [0.0, 0.0])
            a[0] += k * pct
            a[1] += pct
        for key in chunk:
            a = acc.get(key)
            cache[key] = (a[0] / a[1]) if a and a[1] > 0 else None
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        log(f"soil K: {min(i + SDA_CHUNK, len(want))}/{len(want)} map units asked")
        time.sleep(0.5)
    return dict((int(k), v) for k, v in cache.items())


def mapunit_exists(mukeys, root: Path = sources.SOURCES_DIR, log: Callable = log_default) -> dict:
    """``{mukey: True/False}``: is the map unit in current SSURGO (Soil Data Access ``mapunit``)?
    Cached in ``gnatsgo/mapunit_exists_sda.json``."""
    import requests
    path = root / "gnatsgo" / "mapunit_exists_sda.json"
    known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    want = sorted(str(int(m)) for m in mukeys if str(int(m)) not in known)
    for i in range(0, len(want), SDA_CHUNK):
        chunk = want[i:i + SDA_CHUNK]
        keys = ", ".join("'" + k + "'" for k in chunk)
        for attempt in range(1, 7):
            try:
                r = requests.post(SDA_URL, json={"query": "SELECT mukey FROM mapunit WHERE mukey IN (" + keys + ")",
                                                 "format": "JSON+COLUMNNAME"}, timeout=180)
                if r.status_code == 200:
                    found = set(str(row[0]) for row in (r.json().get("Table") or [])[1:])
                    break
            except Exception as exc:  # noqa: BLE001
                log(f"map units: Soil Data Access attempt {attempt}: {exc}")
            time.sleep(10 * attempt)
        else:
            raise RuntimeError("Soil Data Access did not answer")
        for k in chunk:
            known[k] = k in found
        path.write_text(json.dumps(known), encoding="utf-8")
    return dict((int(k), v) for k, v in known.items())


def _mukey_chunks(data_dir: Path, vpu: str, sources_root: Path, chunk: int = 4096):
    """``(region, ix0, iy1, w, h, mask, mukeys)`` per chunk of every gNATSGO window over the region."""
    import rasterio
    from rasterio.windows import Window
    region = RegionCells(data_dir, vpu)
    b = region.bounds
    files = [f for f in gnatsgo_files(sources_root)
             if f["ix0"] < b[:, 2].max() and f["ix0"] + f["w"] > b[:, 0].min()
             and f["iy1"] > b[:, 1].min() and f["iy1"] - f["h"] < b[:, 3].max()]
    for f in files:
        with rasterio.open(f["path"]) as src:
            for r0 in range(0, f["h"], chunk):
                for c0 in range(0, f["w"], chunk):
                    w, h = min(chunk, f["w"] - c0), min(chunk, f["h"] - r0)
                    ix0, iy1 = f["ix0"] + c0, f["iy1"] - r0
                    rows = region.rows_in(ix0, iy1, w, h)
                    if not len(rows):
                        continue
                    lab = region.labels(rows, ix0, iy1, w, h)
                    mask = lab > 0
                    if not mask.any():
                        continue
                    yield region, ix0, iy1, w, h, lab, mask, src.read(1, window=Window(c0, r0, w, h))


def retired_tiles(data_dir: Path, vpu: str, *, sources_root: Path = sources.SOURCES_DIR,
                  log: Callable = log_default) -> list:
    """The 5 km tiles ``(tx, ty)`` (cell index // PATCH_TILE) holding catchment cells whose 2020 map
    unit is not in current SSURGO."""
    statsgo = statsgo_mukeys(sources_root)
    units = set()
    for *_, lab, mask, mk in _mukey_chunks(data_dir, vpu, sources_root):
        units.update(np.unique(mk[mask]).tolist())
    units.discard(GNATSGO_NODATA)
    exists = mapunit_exists(np.setdiff1d(np.array(sorted(units), dtype=np.int64), statsgo), sources_root, log)
    retired = np.array(sorted(k for k, v in exists.items() if v is False), dtype=np.int64)
    tiles = set()
    for region, ix0, iy1, w, h, lab, mask, mk in _mukey_chunks(data_dir, vpu, sources_root):
        hit = mask & np.isin(mk, retired)
        if not hit.any():
            continue
        rr, cc = np.nonzero(hit)
        ix = ix0 + cc
        iy = iy1 - 1 - rr
        tiles.update(zip((ix // PATCH_TILE).tolist(), (iy // PATCH_TILE).tolist()))
    out = sorted(tiles)
    log(f"[{vpu}] retired map units: {len(retired)} in current use nowhere; {len(out)} tiles to patch")
    return out


def _tile_wkt(tx: int, ty: int, pad_m: float = 30.0) -> str:
    """The tile as an EPSG:4326 polygon (edges densified), a little larger than the tile."""
    from pyproj import Transformer
    x0 = GRID10.offset + tx * PATCH_TILE * GRID10.cell - pad_m
    y0 = GRID10.offset + ty * PATCH_TILE * GRID10.cell - pad_m
    side = PATCH_TILE * GRID10.cell + 2 * pad_m
    t = np.linspace(0, 1, 11)
    xs = np.concatenate([x0 + side * t, np.full(11, x0 + side), x0 + side * t[::-1], np.full(11, x0)])
    ys = np.concatenate([np.full(11, y0), y0 + side * t, np.full(11, y0 + side), y0 + side * t[::-1]])
    lon, lat = Transformer.from_crs(5070, 4326, always_xy=True).transform(xs, ys)
    ring = ", ".join(f"{a:.6f} {b:.6f}" for a, b in zip(lon, lat))
    return f"POLYGON(({ring}))"


def _sda_polygons(wkt: str, log: Callable):
    """``[(mukey, geometry in EPSG:4326)]`` of current SSURGO polygons clipped to ``wkt``, or None."""
    import requests
    import shapely.wkt
    sql = ("SELECT m.mukey, m.mupolygongeo.STIntersection(geometry::STGeomFromText('" + wkt + "', 4326)).STAsText() "
           "FROM mupolygon m WHERE m.mupolygongeo.STIntersects(geometry::STGeomFromText('" + wkt + "', 4326)) = 1")
    for attempt in range(1, 5):
        try:
            r = requests.post(SDA_URL, json={"query": sql, "format": "JSON+COLUMNNAME"}, timeout=300)
            if r.status_code == 200:
                out = []
                for row in (r.json().get("Table") or [])[1:]:
                    try:
                        g = shapely.wkt.loads(row[1])
                    except Exception:  # noqa: BLE001 - an empty or odd clip
                        continue
                    if not g.is_empty:
                        out.append((int(row[0]), g))
                return out
            log(f"SSURGO patch: Soil Data Access HTTP {r.status_code} (attempt {attempt})")
        except Exception as exc:  # noqa: BLE001
            log(f"SSURGO patch: Soil Data Access attempt {attempt}: {exc}")
        time.sleep(15 * attempt)
    return None


def patch_dir(root: Path = sources.SOURCES_DIR) -> Path:
    return root / "ssurgo_patch"


def ssurgo_patch(tiles, root: Path = sources.SOURCES_DIR, workers: int = 3, log: Callable = log_default) -> dict:
    """Current SSURGO map units burned onto each tile of the 10 m grid (``ssurgo_patch/t_<tx>_<ty>.tif``,
    int32, 0 = no polygon), from Soil Data Access; a tile already on disk is kept."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    import rasterio
    import shapely
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.transform import Affine
    folder = patch_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True)

    def one(tile):
        tx, ty = tile
        path = folder / f"t_{tx}_{ty}.tif"
        if path.exists():
            return tile, "kept", 0
        polys = _sda_polygons(_tile_wkt(tx, ty), log)
        if polys is None:
            return tile, "failed", 0
        x0 = GRID10.offset + tx * PATCH_TILE * GRID10.cell
        y1 = GRID10.offset + (ty + 1) * PATCH_TILE * GRID10.cell
        transform = Affine(GRID10.cell, 0, x0, 0, -GRID10.cell, y1)
        shapes = [(shapely.transform(g, lambda q: np.column_stack(to5070.transform(q[:, 0], q[:, 1]))), mk)
                  for mk, g in polys if g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection")]
        grid = (rasterize(shapes, out_shape=(PATCH_TILE, PATCH_TILE), transform=transform, fill=0, dtype="int32")
                if shapes else np.zeros((PATCH_TILE, PATCH_TILE), dtype="int32"))
        tmp = path.with_suffix(".part")
        with rasterio.open(tmp, "w", driver="GTiff", width=PATCH_TILE, height=PATCH_TILE, count=1, dtype="int32",
                           crs="EPSG:5070", transform=transform, nodata=0, compress="deflate") as dst:
            dst.write(grid, 1)
        tmp.replace(path)
        return tile, "done", len(polys)

    done = failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, fut in enumerate(as_completed([pool.submit(one, t) for t in tiles])):
            tile, status, n = fut.result()
            done += status != "failed"
            failed += status == "failed"
            if (i + 1) % 50 == 0 or status == "failed":
                log(f"SSURGO patch: {i + 1}/{len(tiles)} tiles ({failed} failed), last {tile} {status} {n} polygons")
    stats = {"tiles": len(tiles), "done": done, "failed": failed}
    sources.record("ssurgo_patch", dict(stats, service="Soil Data Access mupolygon, clipped per 5 km tile",
                                        tile_cells=PATCH_TILE), root)
    return stats


def _patch_reader(root: Path):
    """``read(ix0, iy1, w, h)`` -> the patch map units over a window (0 where no tile or polygon)."""
    import rasterio
    folder = patch_dir(root)
    tiles = {}
    for path in folder.glob("t_*.tif"):
        _, tx, ty = path.stem.split("_")
        tiles[(int(tx), int(ty))] = path
    if not tiles:
        return None

    def read(ix0, iy1, w, h):
        out = np.zeros((h, w), dtype=np.int64)
        iy0 = iy1 - h
        for tx in range(ix0 // PATCH_TILE, (ix0 + w - 1) // PATCH_TILE + 1):
            for ty in range(iy0 // PATCH_TILE, (iy1 - 1) // PATCH_TILE + 1):
                path = tiles.get((tx, ty))
                if path is None:
                    continue
                with rasterio.open(path) as src:
                    a = src.read(1)
                tx0, ty1 = tx * PATCH_TILE, (ty + 1) * PATCH_TILE
                c0, c1 = max(ix0, tx0), min(ix0 + w, tx0 + PATCH_TILE)
                r_top, r_bot = min(iy1, ty1), max(iy0, ty1 - PATCH_TILE)
                if c0 >= c1 or r_bot >= r_top:
                    continue
                out[iy1 - r_top:iy1 - r_bot, c0 - ix0:c1 - ix0] = a[ty1 - r_top:ty1 - r_bot, c0 - tx0:c1 - tx0]
        return out
    return read


def soils(data_dir: Path, vpu: str, *, sources_root: Path = sources.SOURCES_DIR, out_dir: Optional[Path] = None,
          chunk: int = 4096, log: Callable = log_default) -> dict:
    t0 = time.time()
    patch = _patch_reader(sources_root)
    exists_path = sources_root / "gnatsgo" / "mapunit_exists_sda.json"
    known = json.loads(exists_path.read_text(encoding="utf-8")) if exists_path.exists() else {}
    retired = np.array(sorted(int(k) for k, v in known.items() if v is False), dtype=np.int64)
    parts = []
    patched_cells = still_retired = 0
    region = None
    for region, ix0, iy1, w, h, lab, mask, raw in _mukey_chunks(data_dir, vpu, sources_root, chunk):
        mk = raw[mask].astype(np.int64)
        if patch is not None and len(retired):
            gone = np.isin(mk, retired)
            if gone.any():
                pk = patch(ix0, iy1, w, h)[mask]
                use = gone & (pk > 0)
                mk = np.where(use, pk, mk)
                patched_cells += int(use.sum())
                still_retired += int((gone & ~use).sum())
        key = (lab[mask].astype(np.int64) - 1) * (1 << 31) + mk
        u, n = np.unique(key, return_counts=True)
        parts.append(pd.DataFrame({"key": u, "n": n}))
    region = region or RegionCells(data_dir, vpu)
    if not parts:                                   # a Canadian unit: no gNATSGO window covers it
        parts.append(pd.DataFrame({"key": np.empty(0, dtype=np.int64), "n": np.empty(0, dtype=np.int64)}))
    df = pd.concat(parts).groupby("key", as_index=False)["n"].sum()
    df["row"] = df["key"] // (1 << 31)
    df["mukey"] = df["key"] % (1 << 31)
    counted = np.bincount(df["row"], weights=df["n"], minlength=region.n).astype(np.int64)
    over = np.nonzero(counted > region.cells)[0]
    if len(over):
        raise RuntimeError(f"{vpu}: {len(over)} catchments counted twice (overlapping map-unit windows?)")
    uncovered = int((region.cells - counted).sum())
    mk = df["mukey"].to_numpy()
    has_mu = mk != GNATSGO_NODATA
    statsgo = np.isin(mk, statsgo_mukeys(sources_root)) & has_mu
    ssurgo = has_mu & ~statsgo
    kmap = sda_kwfact(np.unique(mk[ssurgo]), sources_root, log)
    k = pd.Series(mk).map(kmap).astype(float).to_numpy()
    with_k = ssurgo & ~np.isnan(k)
    n = df["n"].to_numpy().astype(np.float64)
    rows = df["row"].to_numpy()

    def per_row(sel, weights):
        return np.bincount(rows[sel], weights=weights[sel], minlength=region.n)

    out = {"nhdplusid": pa.array(region.ids),
           "k_sum": pa.array(per_row(with_k, n * np.nan_to_num(k))),
           "k_cells": pa.array(per_row(with_k, n).astype(np.int32)),
           "ssurgo_cells": pa.array(per_row(ssurgo, n).astype(np.int32)),
           "statsgo_cells": pa.array(per_row(statsgo, n).astype(np.int32))}
    out_dir = Path(out_dir or (data_dir.parent / "values"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"soils2_{vpu}.parquet"
    pq.write_table(pa.table(out), path, compression="zstd", compression_level=19)
    # the cells of every (catchment, map unit) pair: the input to patching retired map units
    pq.write_table(pa.table({"row": pa.array(rows.astype(np.int32)), "mukey": pa.array(mk.astype(np.int64)),
                             "cells": pa.array(n.astype(np.int32))}),
                   out_dir / f"soilmu2_{vpu}.parquet", compression="zstd", compression_level=19)
    units = np.unique(mk[ssurgo])
    stats = {"vpu": vpu, "map_units": int(len(units)),
             "map_units_without_k": int(sum(1 for m in units if kmap.get(int(m)) is None)),
             "cells_ssurgo": int(n[ssurgo].sum()), "cells_with_k": int(n[with_k].sum()),
             "cells_statsgo": int(n[statsgo].sum()), "cells_no_data": int(n[~has_mu].sum()),
             "cells_outside_windows": uncovered, "cells_patched_from_current_ssurgo": patched_cells,
             "cells_still_on_retired_map_units": still_retired,
             "bytes": path.stat().st_size, "seconds": round(time.time() - t0)}
    log(f"[{vpu}] soils done: {stats}")
    return stats

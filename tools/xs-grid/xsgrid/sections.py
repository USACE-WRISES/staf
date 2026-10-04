"""Where every grid section sits, which way it faces, and its bankfull inputs.

Sections sit on the STAF data bundle's lines (the apps' lines once the bundle is on), evenly spaced
on each flowline part: ``n = max(1, round(L / 30.48 m))`` sections at ``(k + 1/2) L / n``, so they
are as close to 100 ft apart as a whole number allows and never on a confluence. Each faces the
way the app's do: perpendicular to the chord from the section point to the point 5 m downstream
(or to the line's end). Drainage area is the flowline's own (``totdasqkm``, what a click on that
flowline uses); the Bieger division is looked up at the section point, as the app does at the
click, and the half-width is the app's: eight regional bankfull widths, 250 to 800 m.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import config
from .engine import bankfull, bieger


def flowlines(vpu: str, bundle: Path = config.BUNDLE) -> pa.Table:
    """The region's gridded flowlines (streams, rivers, canals, ditches) with their geometry."""
    t = pq.read_table(Path(bundle) / f"lines2_{vpu}.parquet",
                      columns=["nhdplusid", "fcode", "totdasqkm", "x", "y", "parts"])
    fc = t.column("fcode").to_numpy()
    keep = np.zeros(len(fc), dtype=bool)
    for lo, hi in config.FCODE_RANGES:
        keep |= (fc >= lo) & (fc < hi)
    return t.filter(pa.array(keep))


def _division_names():
    gdf = bieger._divisions()
    return np.asarray(gdf.geometry.values), [str(v) for v in gdf["DIVISION"]]


def divisions_at(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """``bieger.division_at`` for many points: the first division polygon (file order) the point
    intersects, else the nearest within 0.25 degree, else None (the national curve)."""
    import shapely
    geoms, names = _division_names()
    n = len(lon)
    first = np.full(n, -1, dtype=np.int64)
    for gi, geom in enumerate(geoms):                     # file order: the first that holds the point
        open_ = np.flatnonzero(first < 0)
        if not len(open_):
            break
        shapely.prepare(geom)
        hit = shapely.intersects_xy(geom, lon[open_], lat[open_])
        first[open_[hit]] = gi
    miss = np.flatnonzero(first < 0)
    if len(miss):
        tree = shapely.STRtree(geoms)
        pts = shapely.points(lon[miss], lat[miss])
        (mi, gj) = tree.query_nearest(pts, max_distance=0.25, all_matches=True)
        if len(mi):
            order = np.lexsort((gj, mi))
            mi, gj = mi[order], gj[order]
            head = np.r_[True, mi[1:] != mi[:-1]]
            first[miss[mi[head]]] = gj[head]
    out = np.full(n, None, dtype=object)
    abbr = [bieger._DIV_ABBR.get(name.strip().upper()) for name in names]
    hit = first >= 0
    out[hit] = np.asarray(abbr, dtype=object)[first[hit]]
    return out


def place(t: pa.Table) -> dict:
    """Every section of the flowlines in ``t``: arrays keyed by column name."""
    from pyproj import Transformer
    xs = t.column("x").combine_chunks()
    ys = t.column("y").combine_chunks()
    parts = t.column("parts").combine_chunks()
    lon = xs.values.to_numpy().astype(np.float64) / 1e5
    lat = ys.values.to_numpy().astype(np.float64) / 1e5
    counts = parts.values.to_numpy().astype(np.int64)
    n_parts_line = np.diff(parts.offsets.to_numpy())
    part_line = np.repeat(np.arange(len(t)), n_parts_line)
    part_index = np.arange(len(counts)) - np.repeat(parts.offsets.to_numpy()[:-1], n_parts_line)
    assert counts.sum() == len(lon), "parts must count every vertex"
    pstart = np.r_[0, np.cumsum(counts)[:-1]]
    pend = pstart + counts
    to5070 = Transformer.from_crs(4326, 5070, always_xy=True)
    px, py = to5070.transform(lon, lat)
    px, py = np.asarray(px), np.asarray(py)
    nv = len(px)
    seg = np.zeros(nv, dtype=np.float64)                 # seg[v]: vertex v to v + 1 in the same part
    if nv > 1:
        seg[:-1] = np.hypot(px[1:] - px[:-1], py[1:] - py[:-1])
    seg[pend - 1] = 0.0
    vpart = np.repeat(np.arange(len(counts)), counts)
    csum = np.cumsum(seg)
    before = np.r_[0.0, csum[:-1]]                       # distance from the region start to vertex v
    plen = np.where(counts > 1, before[pend - 1] - before[pstart], 0.0)
    local = before - before[pstart][vpart]
    base = np.r_[0.0, np.cumsum(plen + 1.0)[:-1]]        # parts 1 m apart: G strictly increasing between them
    G = base[vpart] + local

    nsec = np.where(plen > 0, np.maximum(1, np.floor(plen / config.SPACING_M + 0.5)), 0).astype(np.int64)
    sec_part = np.repeat(np.arange(len(counts)), nsec)
    k = np.arange(len(sec_part)) - np.repeat(np.r_[0, np.cumsum(nsec)[:-1]], nsec)
    s = (k + 0.5) * plen[sec_part] / nsec[sec_part]
    s2 = np.minimum(s + config.CHORD_M, plen[sec_part])

    def point_at(dist):
        g = base[sec_part] + dist
        idx = np.searchsorted(G, g, side="right") - 1
        idx = np.clip(idx, pstart[sec_part], np.maximum(pend[sec_part] - 2, pstart[sec_part]))
        sl = seg[idx]
        tt = np.where(sl > 0, (g - G[idx]) / np.where(sl > 0, sl, 1.0), 0.0)
        tt = np.clip(tt, 0.0, 1.0)
        return px[idx] + tt * (px[idx + 1] - px[idx]), py[idx] + tt * (py[idx + 1] - py[idx]), idx

    x0, y0, idx0 = point_at(s)
    x2, y2, _ = point_at(s2)
    dx, dy = x2 - x0, y2 - y0
    norm = np.hypot(dx, dy)
    flat = norm == 0                                     # zero-length chord: the segment's own direction
    if flat.any():
        dx[flat] = px[idx0[flat] + 1] - px[idx0[flat]]
        dy[flat] = py[idx0[flat] + 1] - py[idx0[flat]]
        norm = np.hypot(dx, dy)
    ok = norm > 0
    nx = np.where(ok, -dy / np.where(ok, norm, 1.0), 0.0)
    ny = np.where(ok, dx / np.where(ok, norm, 1.0), 0.0)

    line = part_line[sec_part]
    da = t.column("totdasqkm").to_numpy()[line]
    ids = t.column("nhdplusid").to_numpy()[line]
    to4326 = Transformer.from_crs(5070, 4326, always_xy=True)
    slon, slat = to4326.transform(x0, y0)
    abbr = divisions_at(np.asarray(slon), np.asarray(slat))
    cache: dict = {}
    w = np.empty(len(s)); d = np.empty(len(s)); a = np.empty(len(s))
    divname = np.empty(len(s), dtype=object); extrap = np.empty(len(s), dtype=bool)
    for i, (da_i, ab) in enumerate(zip(da.tolist(), abbr.tolist())):
        key = (da_i, ab)
        got = cache.get(key)
        if got is None:
            got = cache[key] = bankfull(da_i, ab)
        w[i], d[i], a[i], divname[i], extrap[i] = got
    wide = np.minimum(np.maximum(config.WIDE_FACTOR * w, config.WIDE_MIN_M), config.WIDE_MAX_M)
    keep = ok
    out = dict(nhdplusid=ids, part=part_index[sec_part].astype(np.int16), k=k.astype(np.int32),
               n_sec=nsec[sec_part].astype(np.int32), s_m=s, x=x0, y=y0, nx=nx, ny=ny, lon=np.asarray(slon),
               lat=np.asarray(slat), da_sqkm=da, division=divname, bf_width_m=w, bf_depth_m=d, bf_area_m2=a,
               bf_extrapolated=extrap, wide_m=wide)
    return {key: np.asarray(v)[keep] for key, v in out.items()}


def cell_ids(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The EPSG:5070 batch cell of each section point, one int64 per cell."""
    cx = np.floor(x / config.CELL_M).astype(np.int64)
    cy = np.floor(y / config.CELL_M).astype(np.int64)
    return (cx + 1000) * 10000 + (cy + 1000)

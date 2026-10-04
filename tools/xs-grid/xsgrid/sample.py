"""Elevation along every grid section from USGS's 3DEP tile files.

The source rule is the app's (``dem_tiles.best_tile_dem``), applied per section: the newest 1 m
lidar project touching the transect (year, then the tile's last-modified date; ties in catalog
order) whose tiles answer at least half of the samples, else the next newest, then the 1/9
arc-second (3 m) quads, then the 1/3 arc-second (10 m) seamless tiles, each under the same test.
The sampling differs from the app's on purpose (``config.SAMPLING_RULE``): the app reprojects a
window to EPSG:5070 and interpolates there, which depends on the window; the grid transforms each
sample point to the tile's own grid and interpolates the native cells, so the same section always
reads the same numbers and its profile can be rebuilt exactly from the recorded tiles.

Tiles are read from whole local copies (``tilecache``): the national driver downloads them ahead
of the samplers; anything not there yet is downloaded on demand.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pyarrow.parquet as pq

from . import config, tilecache
from .engine import dem_tiles

_TRANSFORMERS: dict = {}
_POOL: Optional[ThreadPoolExecutor] = None
SEAMLESS_TILE = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/"
                 "{name}/USGS_13_{name}.tif")
_MISSING: set = set()


def transformer(epsg_to: int):
    from pyproj import Transformer
    t = _TRANSFORMERS.get(epsg_to)
    if t is None:
        t = _TRANSFORMERS[epsg_to] = Transformer.from_crs(5070, epsg_to, always_xy=True)
    return t


def pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(16)
    return _POOL


@dataclass
class Catalogs:
    """The 1 m tile and 3 m quad catalogs with spatial indexes on their EPSG:4326 boxes."""
    folder: Path = config.CATALOGS
    one: dict = field(default_factory=dict)
    nine: dict = field(default_factory=dict)

    def __post_init__(self):
        import shapely
        t = pq.read_table(Path(self.folder) / "dem1m_tiles.parquet").to_pydict()
        n = len(t["name"])
        lm = np.asarray(t["last_modified"], dtype=object)
        ranks = dict((v, i) for i, v in enumerate(sorted(set(lm.tolist()))))
        projects = sorted(set(t["project"]))
        pid = dict((p, i) for i, p in enumerate(projects))
        self.one = dict(
            name=np.asarray(t["name"], dtype=object), url=np.asarray(t["url"], dtype=object),
            project=np.asarray([pid[p] for p in t["project"]], dtype=np.int64), projects=projects,
            epsg=np.asarray(t["epsg"], dtype=np.int64), bytes=np.asarray(t["bytes"], dtype=np.int64),
            key=np.asarray(t["year"], dtype=np.int64) * (len(ranks) + 1)
            + np.asarray([ranks[v] for v in lm.tolist()], dtype=np.int64),
            tree=shapely.STRtree(shapely.box(t["west"], t["south"], t["east"], t["north"])), n=n)
        q = pq.read_table(Path(self.folder) / "dem19_quads.parquet").to_pydict()
        self.nine = dict(name=np.asarray(q["name"], dtype=object), url=np.asarray(q["url"], dtype=object),
                         tree=shapely.STRtree(shapely.box(q["west"], q["south"], q["east"], q["north"])))


@dataclass
class Section:
    """One section's sampling state."""
    i: int
    x: float
    y: float
    nx: float
    ny: float
    wide: float
    bbox4326: tuple = ()
    candidates: list = field(default_factory=list)   # [(project id, [tile idx...]), ...] newest first
    res: int = 0
    source: str = ""
    tiles: str = ""
    z: Optional[np.ndarray] = None


def n_points(wide: float, res: float) -> int:
    """The app's sample count: ``min(int(2 * wide / step) + 1, 2001)``, step ``min(10, res)``."""
    step = min(config.STEP_CAP_M, float(res))
    return min(int(2 * wide / step) + 1, config.MAX_POINTS)


def stations(wide: float, n_pts: int) -> np.ndarray:
    return np.linspace(-wide, wide, n_pts)


def points5070(sec: Section, n_pts: int) -> tuple:
    ts = stations(sec.wide, n_pts)
    return sec.x + sec.nx * ts, sec.y + sec.ny * ts


def bilinear(arr: np.ndarray, transform, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """Bilinear between the four nearest cell centres of the grid (NaN when any is missing)."""
    fj = (X - transform.c) / transform.a - 0.5
    fi = (Y - transform.f) / transform.e - 0.5
    j0 = np.floor(fj).astype(np.int64)
    i0 = np.floor(fi).astype(np.int64)
    h, w = arr.shape
    ok = (j0 >= 0) & (i0 >= 0) & (j0 + 1 < w) & (i0 + 1 < h)
    out = np.full(X.shape, np.nan, dtype=np.float64)
    if ok.any():
        jj, ii = j0[ok], i0[ok]
        tx, ty = fj[ok] - jj, fi[ok] - ii
        z00 = arr[ii, jj].astype(np.float64)
        z01 = arr[ii, jj + 1].astype(np.float64)
        z10 = arr[ii + 1, jj].astype(np.float64)
        z11 = arr[ii + 1, jj + 1].astype(np.float64)
        out[ok] = (1.0 - ty) * ((1.0 - tx) * z00 + tx * z01) + ty * ((1.0 - tx) * z10 + tx * z11)
    return out


def transect_boxes(secs: list) -> None:
    """Each section's EPSG:4326 box of its transect's two ends (padded about 1 m)."""
    if not secs:
        return
    x = np.asarray([s.x for s in secs])
    y = np.asarray([s.y for s in secs])
    nx = np.asarray([s.nx for s in secs])
    ny = np.asarray([s.ny for s in secs])
    w = np.asarray([s.wide for s in secs])
    lon, lat = transformer(4326).transform(np.r_[x - nx * w, x + nx * w], np.r_[y - ny * w, y + ny * w])
    lon, lat = np.asarray(lon), np.asarray(lat)
    n = len(secs)
    pad = 1e-5
    w0 = np.minimum(lon[:n], lon[n:]) - pad
    e0 = np.maximum(lon[:n], lon[n:]) + pad
    s0 = np.minimum(lat[:n], lat[n:]) - pad
    n0 = np.maximum(lat[:n], lat[n:]) + pad
    for i, s in enumerate(secs):
        s.bbox4326 = (float(w0[i]), float(s0[i]), float(e0[i]), float(n0[i]))


def candidates(cat: Catalogs, secs: list) -> None:
    """Fill each section's 1 m candidates: the projects whose tiles touch its transect box,
    newest first (a project's date is its newest touching tile's, as ``Catalog.projects_for``)."""
    import shapely
    if not secs:
        return
    if not secs[0].bbox4326:
        transect_boxes(secs)
    boxes = shapely.box(*np.asarray([s.bbox4326 for s in secs]).T)
    si, ti = cat.one["tree"].query(boxes, predicate="intersects")
    if not len(si):
        return
    proj = cat.one["project"][ti]
    key = cat.one["key"][ti]
    order = np.lexsort((ti, -key, proj, si))             # by section, project, newest tile, catalog order
    si, ti, proj, key = si[order], ti[order], proj[order], key[order]
    start = np.r_[0, np.flatnonzero(np.diff(si)) + 1]
    stop = np.r_[start[1:], len(si)]
    for a, b in zip(start.tolist(), stop.tolist()):
        p, k, t = proj[a:b], key[a:b], ti[a:b]
        heads = np.r_[True, p[1:] != p[:-1]]
        hp, hk, hfirst = p[heads], k[heads], np.minimum.reduceat(t, np.flatnonzero(heads))
        rank = np.lexsort((hfirst, -hk))                   # newest first, ties in catalog order
        groups = np.split(t, np.flatnonzero(heads)[1:])
        secs[int(si[a])].candidates = [(int(hp[r]), groups[r].tolist()) for r in rank]


def first_candidate_urls(cat: Catalogs, secs: list) -> list:
    """``[(url, bytes), ...]`` of the tiles the sections' newest projects need (what to prefetch)."""
    tiles = sorted(set(t for s in secs if s.candidates for t in s.candidates[0][1]))
    return [(str(cat.one["url"][t]), int(cat.one["bytes"][t])) for t in tiles]


def ensure(url: str) -> str:
    """The local copy of ``url`` (downloaded now when the prefetcher has not)."""
    path = tilecache.local_path(url)
    if path.exists():
        try:
            path.touch()                                  # recently used: the cache evicts it last
        except OSError:
            pass
    else:
        tilecache.fetch(url, pool())
    return str(path)


def _read(urls: list, bounds) -> tuple:
    return dem_tiles.merge_windows([ensure(u) for u in urls], bounds)


def sample_group(urls: list, epsg: int, secs: list, n_pts: list, pad_units: float) -> list:
    """Bilinear samples of every section in ``secs`` from the rasters ``urls`` (one grid)."""
    tr = transformer(epsg)
    pts = []
    for sec, n in zip(secs, n_pts):
        X, Y = points5070(sec, n)
        u, v = tr.transform(X, Y)
        pts.append((np.asarray(u), np.asarray(v)))
    allu = np.concatenate([p[0] for p in pts])
    allv = np.concatenate([p[1] for p in pts])
    bounds = (allu.min() - pad_units, allv.min() - pad_units, allu.max() + pad_units, allv.max() + pad_units)
    arr, transform, _crs = _read(urls, bounds)
    return [bilinear(arr, transform, u, v) for u, v in pts]


def _finite(z: np.ndarray) -> float:
    return float(np.isfinite(z).mean()) if len(z) else 0.0


def seamless_urls(boxes: list) -> list:
    """The 1/3 arc-second tiles (one degree, named by the north-west corner) touching the boxes."""
    names = set()
    for w, s, e, n in boxes:
        for lat in range(math.floor(s), math.floor(n) + 1):
            for lon in range(math.floor(w), math.floor(e) + 1):
                names.add(f"n{lat + 1:02d}w{-lon:03d}" if lon < 0 else f"n{lat + 1:02d}e{lon:03d}")
    out = []
    for name in sorted(names):
        url = SEAMLESS_TILE.format(name=name)
        if url in _MISSING:
            continue
        try:
            ensure(url)
            out.append(url)
        except IOError:
            _MISSING.add(url)                             # offshore: USGS has no tile there
    return out


def sample_cell(cat: Catalogs, secs: list) -> dict:
    """Sample every section of a cell through the source tiers; fills ``res``, ``source``,
    ``tiles`` and ``z`` (float32) on each. Returns counts per tier."""
    counts = dict(one=0, three=0, ten=0, none=0, reads=0)
    if secs and not secs[0].bbox4326:
        transect_boxes(secs)
    if secs and not any(s.candidates for s in secs):
        candidates(cat, secs)
    pending = [s for s in secs if s.candidates]
    level = 0
    while pending:                                        # 1 m: newest project first, then older
        by_project: dict = {}
        for s in pending:
            if level < len(s.candidates):
                by_project.setdefault(s.candidates[level][0], []).append(s)
        if not by_project:
            break
        retry = []
        for p, group in sorted(by_project.items()):
            tiles = sorted(set(t for s in group for t in s.candidates[level][1]))
            by_epsg: dict = {}
            for t in tiles:
                by_epsg.setdefault(int(cat.one["epsg"][t]), []).append(t)
            n = [n_points(s.wide, 1) for s in group]
            zs = [np.full(k, np.nan) for k in n]
            for epsg, ts_ in by_epsg.items():                # a project crossing UTM zones: fill in turn
                got = sample_group([str(cat.one["url"][t]) for t in ts_], epsg, group, n, 2.5)
                counts["reads"] += 1
                for z, g in zip(zs, got):
                    take = ~np.isfinite(z) & np.isfinite(g)
                    z[take] = g[take]
            for s, z in zip(group, zs):
                if _finite(z) >= config.FINITE_MIN:
                    s.res, s.z = 1, z.astype(np.float32)
                    s.source = cat.one["projects"][p]
                    s.tiles = ";".join(str(cat.one["name"][t]) for t in s.candidates[level][1])
                    counts["one"] += 1
                else:
                    retry.append(s)
        pending = retry
        level += 1
    rest = [s for s in secs if s.z is None]
    if rest:                                              # 3 m quads
        import shapely
        boxes = shapely.box(*np.asarray([s.bbox4326 for s in rest]).T)
        si, qi = cat.nine["tree"].query(boxes, predicate="intersects")
        if len(si):
            quads = sorted(set(qi.tolist()))
            has = sorted(set(si.tolist()))
            group = [rest[i] for i in has]
            n = [n_points(s.wide, config.QUAD_RES_M) for s in group]
            got = sample_group([str(cat.nine["url"][q]) for q in quads], 4269, group, n, 2.5 / 32400.0)
            counts["reads"] += 1
            own: dict = {}
            for a, b in zip(si.tolist(), qi.tolist()):
                own.setdefault(a, []).append(b)
            for i, s, z in zip(has, group, got):
                if _finite(z) >= config.FINITE_MIN:
                    s.res, s.z, s.source = config.QUAD_RES_M, z.astype(np.float32), "3dep-19"
                    s.tiles = ";".join(str(cat.nine["name"][q]) for q in own[i])
                    counts["three"] += 1
    rest = [s for s in secs if s.z is None]
    if rest:                                              # 10 m seamless tiles
        urls = seamless_urls([s.bbox4326 for s in rest])
        if urls:
            n = [n_points(s.wide, config.SEAMLESS_RES_M) for s in rest]
            got = sample_group(urls, 4269, rest, n, 2.5 / 10800.0)
            counts["reads"] += 1
            names = ";".join(u.rsplit("/", 1)[-1] for u in urls)
        else:
            got, names = [np.zeros(0)] * len(rest), ""
        for s, z in zip(rest, got):
            if _finite(z) >= config.FINITE_MIN:
                s.res, s.z, s.source, s.tiles = config.SEAMLESS_RES_M, z.astype(np.float32), "3dep-13", names
                counts["ten"] += 1
            else:
                counts["none"] += 1
    return counts

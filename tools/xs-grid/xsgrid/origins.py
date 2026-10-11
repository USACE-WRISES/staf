"""Each section's sampling grid, as the build read it, so a pull from USGS reproduces the archive.

The build samples a cell's sections group by group (``sample.sample_cell``): per 1 m level, the sections
whose candidate project at that level is the same, read together through ``dem_tiles.merge_windows``
over all their points (2.5 m padded, one window per UTM zone), then the 3 m quads for the sections left,
then the 10 m tiles for the rest. ``merge_windows`` pastes every tile onto the grid of the group's first
tile, starting at the window's corner; USGS's tile origins are not whole metres (e.g. 539993.9999908725),
so a tile can sit micrometres off its own grid in that window, and bilinear interpolation then
depends on the window's corner. A pull of one section over its own window lands a few float32 steps
off the archive on some samples (measured 2026-10-10: up to 4.9e-5 m in Connecticut).

``region_grids`` replays the groups (the same catalog, candidates, levels and fallbacks, from the
archive's recorded tiers and sources) and returns, per section, the corner each of its reads used:
``(epsg, c, f, a, e)``, in the order its reads filled the samples. ``pull`` reads a section's recorded
tiles onto that corner and interpolates with the build's own arithmetic, which reproduces the archive bit
for bit. Tile grids come from the tile headers (the catalogs' boxes are rounded), cached in
``WORK/tile_grids.json``.
"""
from __future__ import annotations

import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import config, sample, sections

SEAMLESS_RES_M = config.SEAMLESS_RES_M
QUAD_PAD = 2.5 / 32400.0
SEAMLESS_PAD = 2.5 / 10800.0
METRE_PAD = 2.5


class TileGrids:
    """``url -> (c, f, a, e, width, height)`` of tile headers (GDAL's transform), cached on disk."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or (config.WORK / "tile_grids.json"))
        self.lock = threading.Lock()
        self.grids: dict = {}
        if self.path.exists():
            self.grids = json.loads(self.path.read_text(encoding="utf-8"))

    def _read(self, url: str) -> list:
        import rasterio
        from .engine import dem_tiles
        env = dict(dem_tiles.GDAL_ENV)
        env.update(config.gdal_env())                   # .img quads among the allowed extensions
        with rasterio.Env(**env):
            with rasterio.open("/vsicurl/" + url if url.startswith("http") else url) as ds:
                t = ds.transform
                return [t.c, t.f, t.a, t.e, ds.width, ds.height]

    def ensure(self, urls) -> None:
        missing = sorted(set(u for u in urls if u not in self.grids))
        if not missing:
            return
        with ThreadPoolExecutor(32) as pool:
            for url, got in zip(missing, pool.map(self._read, missing)):
                self.grids[url] = got
        self.save()

    def save(self) -> None:
        """Merge into the file on disk (several packing processes share it; the last write keeps all)."""
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            for _attempt in range(5):
                try:
                    if self.path.exists():
                        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
                        on_disk.update(self.grids)
                        self.grids = on_disk
                    tmp = self.path.with_name(f"{self.path.stem}.{os.getpid()}.tmp")
                    tmp.write_text(json.dumps(self.grids), encoding="utf-8", newline="\n")
                    os.replace(tmp, self.path)
                    return
                except (OSError, ValueError):
                    import time
                    time.sleep(0.5)

    def __getitem__(self, url: str) -> list:
        if url not in self.grids:
            self.ensure([url])
        return self.grids[url]


def one_m_url(source: str, name: str) -> str:
    return f"https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/{source}/TIFF/{name}"


def seamless_url(name: str) -> str:
    stem = name[len("USGS_13_"):-len(".tif")] if name.startswith("USGS_13_") else name
    return sample.SEAMLESS_TILE.format(name=stem)


def _corner(grid: list, bounds: tuple) -> tuple:
    """``merge_windows``' window corner on ``grid`` (the group's first tile) over ``bounds``."""
    c0, f0, a, e = grid[:4]
    minx, _miny, _maxx, maxy = bounds
    col0 = math.floor((minx - c0) / a)
    row0 = math.floor((maxy - f0) / e)
    return c0 + col0 * a, f0 + row0 * e, a, e


#: how far a transect's projected samples can stray past the range of its projected ends (a straight
#: 5070 line is slightly curved in UTM or NAD83; well under a centimetre over 1.6 km): metres, degrees
_STRAY = {"m": 1.0, "deg": 1e-5}


def _bounds(secs: list, n_pts: list, epsg: int, pad: float) -> tuple:
    """``sample.sample_group``'s window bounds: the extremes of every sample point of ``secs``
    transformed to ``epsg``, padded. Exact, but cheaper than transforming every point: each section's
    two ends are transformed, and only the sections whose ends come within ``_STRAY`` of an extreme
    (the only ones whose samples can hold it) are transformed in full."""
    tr = sample.transformer(epsg)
    n = len(secs)
    x = np.fromiter((s.x for s in secs), float, n)
    y = np.fromiter((s.y for s in secs), float, n)
    nx = np.fromiter((s.nx for s in secs), float, n)
    ny = np.fromiter((s.ny for s in secs), float, n)
    w = np.fromiter((s.wide for s in secs), float, n)
    # the ends are linspace's first and last stations exactly: -wide and wide
    u0, v0 = (np.asarray(t) for t in tr.transform(x + nx * -w, y + ny * -w))
    u1, v1 = (np.asarray(t) for t in tr.transform(x + nx * w, y + ny * w))
    lo_u, hi_u = np.minimum(u0, u1), np.maximum(u0, u1)
    lo_v, hi_v = np.minimum(v0, v1), np.maximum(v0, v1)
    stray = _STRAY["deg"] if epsg == 4269 else _STRAY["m"]
    near = ((lo_u <= lo_u.min() + stray) | (hi_u >= hi_u.max() - stray)
            | (lo_v <= lo_v.min() + stray) | (hi_v >= hi_v.max() - stray))
    best = [np.inf, np.inf, -np.inf, -np.inf]
    for i in np.flatnonzero(near).tolist():
        X, Y = sample.points5070(secs[i], n_pts[i])
        u, v = (np.asarray(t) for t in tr.transform(X, Y))
        best = [min(best[0], u.min()), min(best[1], v.min()), max(best[2], u.max()), max(best[3], v.max())]
    return (best[0] - pad, best[1] - pad, best[2] + pad, best[3] + pad)


def _bounds_all_points(secs: list, n_pts: list, epsg: int, pad: float) -> tuple:
    """The same bounds by transforming every sample point (the build's way; for tests)."""
    tr = sample.transformer(epsg)
    us, vs = [], []
    for sec, n in zip(secs, n_pts):
        u, v = tr.transform(*sample.points5070(sec, n))
        us.append(np.asarray(u))
        vs.append(np.asarray(v))
    allu = np.concatenate(us)
    allv = np.concatenate(vs)
    return (allu.min() - pad, allv.min() - pad, allu.max() + pad, allv.max() + pad)


def region_grids(table, cat: "sample.Catalogs", grids: TileGrids) -> list:
    """Per row of ``table`` (the archive's columns ``x, y, nx, ny, wide_m, res_m, source, tiles``; any
    order), the list of ``(epsg, c, f, a, e)`` its samples were read on, in reading order; ``[]`` for a
    section with no samples."""
    x = table.column("x").to_numpy()
    y = table.column("y").to_numpy()
    nx = table.column("nx").to_numpy()
    ny = table.column("ny").to_numpy()
    wide = table.column("wide_m").to_numpy()
    res = table.column("res_m").to_numpy()
    source = table.column("source").to_pylist()
    tiles = table.column("tiles").to_pylist()
    n = len(x)
    out: list = [[] for _ in range(n)]
    project_id = dict((p, i) for i, p in enumerate(cat.one["projects"]))
    cells = sections.cell_ids(x, y)
    order = np.argsort(cells, kind="stable")
    bounds_at = np.r_[0, np.flatnonzero(np.diff(cells[order])) + 1, n] if n else np.zeros(1, dtype=np.int64)
    url_one = np.asarray(cat.one["url"], dtype=object)
    epsg_one = cat.one["epsg"]
    tasks: list = []                                     # (epsg, first tile, bounds, the rows it gives)
    for a_, b_ in zip(bounds_at[:-1].tolist(), bounds_at[1:].tolist()):
        # one cell at a time, as the build sampled them (and memory stays one cell's)
        cell = [sample.Section(i=int(i), x=float(x[i]), y=float(y[i]), nx=float(nx[i]), ny=float(ny[i]),
                               wide=float(wide[i])) for i in order[a_:b_]]
        sample.transect_boxes(cell)
        sample.candidates(cat, cell)
        # 1 m: the level each section was sampled at (its recorded project), or past every level
        final = {}
        for s in cell:
            if res[s.i] == 1:
                levels = [lvl for lvl, (p, _t) in enumerate(s.candidates) if p == project_id.get(source[s.i])]
                if not levels:
                    raise ValueError(f"section {s.i}: its project {source[s.i]} is not among its candidates")
                final[s.i] = levels[0]
            else:
                final[s.i] = len(s.candidates)
        depth = max((len(s.candidates) for s in cell), default=0)
        for level in range(depth):
            by_project: dict = {}
            for s in cell:
                if level < len(s.candidates) and final[s.i] >= level:
                    by_project.setdefault(s.candidates[level][0], []).append(s)
            for _p, group in sorted(by_project.items()):
                tset = sorted(set(t for s in group for t in s.candidates[level][1]))
                by_epsg: dict = {}
                for t in tset:
                    by_epsg.setdefault(int(epsg_one[t]), []).append(t)
                done = [s for s in group if final[s.i] == level]
                if not done:
                    continue
                n_pts = [sample.n_points(s.wide, 1) for s in group]
                for epsg, ts_ in by_epsg.items():
                    gets = [s for s in done if any(int(epsg_one[t]) == epsg for t in s.candidates[level][1])]
                    if gets:
                        tasks.append((epsg, str(url_one[ts_[0]]), _bounds(group, n_pts, epsg, METRE_PAD),
                                      [g.i for g in gets]))
        rest = [s for s in cell if res[s.i] != 1]
        if not rest:
            continue
        # 3 m: every section left that touches a quad (all served since 2026-10-09; cells that needed a
        # misnamed quad failed before then and were sampled again after)
        import shapely
        boxes = shapely.box(*np.asarray([s.bbox4326 for s in rest]).T)
        si, qi = cat.nine["tree"].query(boxes, predicate="intersects")
        own: dict = {}
        for a2, b2 in zip(si.tolist(), qi.tolist()):
            own.setdefault(a2, []).append(b2)
        has = sorted(own)
        threes = [rest[i] for i in has if res[rest[i].i] == 3]
        if threes:
            group = [rest[i] for i in has]
            quads = sorted(set(q for i in has for q in own[i]))
            n_pts = [sample.n_points(s.wide, config.QUAD_RES_M) for s in group]
            tasks.append((4269, str(cat.nine["url"][quads[0]]), _bounds(group, n_pts, 4269, QUAD_PAD),
                          [g.i for g in threes]))
        # 10 m: every section still without samples (10 m or none); the build read every 1/3 arc-second
        # tile touching them, and recorded that list on each 10 m section
        tens = [s for s in rest if res[s.i] == SEAMLESS_RES_M]
        if tens:
            group = [s for s in rest if res[s.i] in (SEAMLESS_RES_M, 0)]
            names = [t for t in tiles[tens[0].i].split(";") if t]
            n_pts = [sample.n_points(s.wide, SEAMLESS_RES_M) for s in group]
            tasks.append((4269, seamless_url(names[0]), _bounds(group, n_pts, 4269, SEAMLESS_PAD),
                          [g.i for g in tens]))
    grids.ensure([t[1] for t in tasks])                  # every first tile's header, read in one batch
    for epsg, ref, bounds, rows in tasks:
        corner = (epsg,) + _corner(grids[ref], bounds)
        for i in rows:
            out[i].append(corner)
    return out


class Names:
    """Tile name -> (url, epsg) from the catalogs (a quad's file can be named otherwise than the quad)."""

    def __init__(self, cat: "sample.Catalogs"):
        self.one = dict((str(n), (str(u), int(e))) for n, u, e in
                        zip(cat.one["name"], cat.one["url"], cat.one["epsg"]))
        self.nine = dict((str(n), str(u)) for n, u in zip(cat.nine["name"], cat.nine["url"]))

    def urls(self, res: int, tiles: str) -> list:
        """``[(url, epsg)]`` a section was read from, in the build's paste order (catalog order, which
        is name order in both catalogs)."""
        names = sorted(t for t in (tiles or "").split(";") if t)
        if res == 1:
            return [self.one[t] for t in names]
        if res == config.QUAD_RES_M:
            return [(self.nine[t], 4269) for t in names]
        if res == SEAMLESS_RES_M:
            return [(seamless_url(t), 4269) for t in names]
        return []


def pull(sec: "sample.Section", n_pts: int, res: int, tiles: str, corners: list, grids: TileGrids,
         names: Names) -> np.ndarray:
    """A section's float32 samples read again from USGS on its recorded grids (``corners``): each read
    pastes the section's tiles of that CRS onto the recorded corner and interpolates exactly as
    ``sample.bilinear`` did, filling what earlier reads left missing."""
    from .engine import dem_tiles
    import rasterio
    from rasterio.windows import Window
    urls = names.urls(res, tiles)
    z = np.full(n_pts, np.nan)
    X, Y = sample.points5070(sec, n_pts)
    for epsg, c, f, a, e in corners:
        mine = [u for u, ue in urls if ue == epsg]
        if not mine:
            continue
        u, v = (np.asarray(t) for t in sample.transformer(epsg).transform(X, Y))
        fj = (u - c) / a - 0.5
        fi = (v - f) / e - 0.5
        j0 = int(np.floor(fj.min()))
        j1 = int(np.floor(fj.max())) + 2
        i0 = int(np.floor(fi.min()))
        i1 = int(np.floor(fi.max())) + 2
        w, h = j1 - j0, i1 - i0
        buf = np.full((h, w), np.nan, dtype="float32")
        with rasterio.Env(**dem_tiles.GDAL_ENV):
            for url in mine:
                tc, tf, _ta, _te, tw, th = grids[url]
                dc = int(round((tc - c) / a))                # the build's paste offset (half to even)
                dr = int(round((tf - f) / e))
                cc0, cc1 = max(j0, dc), min(j1, dc + tw)
                rr0, rr1 = max(i0, dr), min(i1, dr + th)
                if cc1 <= cc0 or rr1 <= rr0:
                    continue
                ds = dem_tiles.open_tile(url)
                data = np.asarray(ds.read(1, window=Window(cc0 - dc, rr0 - dr, cc1 - cc0, rr1 - rr0)), dtype="float32")
                mask = ~np.isfinite(data) | (data <= -1e30)
                if ds.nodata is not None:
                    mask |= data == np.float32(ds.nodata)
                region = buf[rr0 - i0:rr1 - i0, cc0 - j0:cc1 - j0]
                take = np.isnan(region) & ~mask
                region[take] = data[take]
        jj = np.floor(fj).astype(np.int64)
        ii = np.floor(fi).astype(np.int64)
        tx, ty = fj - jj, fi - ii
        bj, bi = jj - j0, ii - i0
        z00 = buf[bi, bj].astype(np.float64)
        z01 = buf[bi, bj + 1].astype(np.float64)
        z10 = buf[bi + 1, bj].astype(np.float64)
        z11 = buf[bi + 1, bj + 1].astype(np.float64)
        got = (1.0 - ty) * ((1.0 - tx) * z00 + tx * z01) + ty * ((1.0 - tx) * z10 + tx * z11)
        take = ~np.isfinite(z) & np.isfinite(got)
        z[take] = got[take]
    return z.astype(np.float32)

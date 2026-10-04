"""Per-catchment values from rasters on the catchments' own grid (bundle v2, Phase 3).

NHDPlus HR catchments are exact polygons on the region's 10 m grid (EPSG:5070, corners at 5 mod 10).
That grid nests in NLCD's 30 m grid (corners at 15 mod 30) and is gNATSGO's own grid, so every 10 m
cell lies inside exactly one source cell. Rasterized at 10 m by cell centre (a centre never sits on a
catchment edge), every catchment cell is counted once: the per-catchment counts are exact, and a
watershed's are the sums over its catchments.

Land cover (``lc2_<vpu>.parquet``, one row per catchment, the catchment order of ``cats2``): the
10 m cell counts of the 16 NLCD 2021 classes and ``lc_none`` (no class: outside NLCD's footprint),
and the sums and valid-cell counts of impervious 2021 and 2001 (values above 100 are nodata).

Riparian strip (``rip2_<vpu>.parquet``, one row per piece): the site engine buffers the walked
tree's flowlines 100 m and clips the union to the watershed. For a catchment C and an outlet O, a
network line L is in O's tree exactly when O lies on L's downstream path; on C's own downstream path
that means at or below the reach where L's path joins C's, ``k`` hops below C (``k = 0`` for C's own
line and every line upstream of C). A cell of C within 100 m of some lines therefore belongs to O's
strip when O sits at least ``k_min`` hops below C, ``k_min`` the smallest ``k`` among those lines. Each
piece is the cells of one catchment with one ``k_min``; for a watershed, catchment C (``d`` hops above
the outlet, its level in the walk) contributes its pieces with ``k <= d``. Lines are the original USGS
flowlines at full resolution; lines of other regions are not considered (their paths meet far
downstream, if at all).
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
import shapely

from . import bootstrap_hrslim, config, source, sources

bootstrap_hrslim()
from hrslim import arcs, fmt2  # noqa: E402
from hrslim.grid import Grid  # noqa: E402
from hrslim.values import LC_COLUMNS, NLCD_CODES  # noqa: E402

_LC_LUT = np.full(256, 16, dtype=np.int64)
_LC_LUT[list(NLCD_CODES)] = np.arange(16)
RIPARIAN_M = 100.0
BUFFER_QUAD_SEGS = 16                     # geopandas' default, what the engine's buffer uses
GRID10 = Grid(5070, 10.0, 5.0)
#: NLCD tiles in 10 m cells: the tile (i, j) window starts at this cell and is this wide.
_IX_ORIGIN = int(round((sources.NLCD_X0 - GRID10.offset) / GRID10.cell))
_IY_ORIGIN = int(round((sources.NLCD_Y0 - GRID10.offset) / GRID10.cell))
_TILE10 = sources.NLCD_TILE * 3


def log_default(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# --------------------------------------------------------------------------- #
# the region's catchments on the 10 m grid
# --------------------------------------------------------------------------- #
class RegionCells:
    def __init__(self, data_dir: Path, vpu: str):
        manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        self.vpu = vpu
        self.entry = manifest["vpus"][vpu]
        self.grid = Grid.from_dict(self.entry["grid"])
        if self.grid != GRID10:
            raise ValueError(f"{vpu}: catchments are on {self.grid}, not the NLCD-nested 10 m grid")
        self.data_dir = data_dir
        self.enc, self.ids, _ = fmt2.read_encoded(data_dir / self.entry["catchments"]["file"],
                                                  data_dir / self.entry["arcs"]["file"],
                                                  data_dir / self.entry["steps"]["file"])
        self.n = self.enc.n_rows
        self.geoms = arcs.rows_geometry(self.enc, np.arange(self.n))          # cell units
        self.bounds = shapely.bounds(self.geoms)
        self.cells = (self.enc.area2 // 2).astype(np.int64)

    def geoms_m(self):
        g = self.grid
        return shapely.transform(self.geoms, lambda q: q * g.cell + g.offset)

    def nlcd_tiles(self) -> list[tuple[int, int]]:
        b = self.bounds
        i0 = np.floor((b[:, 0] - _IX_ORIGIN) / _TILE10).astype(int)
        i1 = np.floor((b[:, 2] - 1e-9 - _IX_ORIGIN) / _TILE10).astype(int)
        j0 = np.floor((_IY_ORIGIN - b[:, 3]) / _TILE10).astype(int)
        j1 = np.floor((_IY_ORIGIN - b[:, 1] - 1e-9) / _TILE10).astype(int)
        tiles = set()
        for a, c, d, e in zip(i0, i1, j0, j1):
            for i in range(a, c + 1):
                for j in range(d, e + 1):
                    tiles.add((int(i), int(j)))
        return sorted(tiles)

    def rows_in(self, ix0: int, iy1: int, w: int, h: int) -> np.ndarray:
        b = self.bounds
        return np.nonzero((b[:, 0] < ix0 + w) & (b[:, 2] > ix0) & (b[:, 1] < iy1) & (b[:, 3] > iy1 - h))[0]

    def labels(self, rows: np.ndarray, ix0: int, iy1: int, w: int, h: int) -> np.ndarray:
        """Catchment row + 1 per 10 m cell of the window (0 outside every catchment)."""
        from affine import Affine
        from rasterio.features import rasterize
        if not len(rows):
            return np.zeros((h, w), dtype=np.int32)
        return rasterize(((self.geoms[r], int(r) + 1) for r in rows), out_shape=(h, w),
                         transform=Affine(1, 0, ix0, 0, -1, iy1), fill=0, dtype="int32", all_touched=False)


def _tile_window(i: int, j: int) -> tuple[int, int]:
    return _IX_ORIGIN + i * _TILE10, _IY_ORIGIN - j * _TILE10


def _read_nlcd(layer: str, i: int, j: int, root: Path) -> np.ndarray:
    """The NLCD tile as 10 m cells (each value repeated 3 x 3), fetched if missing; a tile beyond
    NLCD's raster is all class 0 (``lc_none``, outside the footprint)."""
    import rasterio
    if not sources.nlcd_tile_inside(i, j):
        return np.zeros((3 * sources.NLCD_TILE, 3 * sources.NLCD_TILE), dtype=np.uint8)
    path = sources.nlcd_tile_path(layer, i, j, root)
    if not path.exists():
        sources._nlcd_one(layer, i, j, root, log_default)
    with rasterio.open(path) as src:
        a = src.read(1)
        x0, y1 = sources.nlcd_tile_box(i, j)[0], sources.nlcd_tile_box(i, j)[3]
        if (round((src.transform.c - x0) / 30), round((y1 - src.transform.f) / 30)) != (0, 0) or a.shape != (
                sources.NLCD_TILE, sources.NLCD_TILE):
            full = np.zeros((sources.NLCD_TILE, sources.NLCD_TILE), dtype=a.dtype)
            c0, r0 = int(round((src.transform.c - x0) / 30)), int(round((y1 - src.transform.f) / 30))
            full[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a[:sources.NLCD_TILE - r0, :sources.NLCD_TILE - c0]
            a = full
    return np.repeat(np.repeat(a, 3, axis=0), 3, axis=1)


# --------------------------------------------------------------------------- #
# network depth and the join point of two downstream paths
# --------------------------------------------------------------------------- #
class Paths:
    """Downstream paths of the region's network lines (rows of ``lines2``)."""

    def __init__(self, down: np.ndarray):
        n = len(down)
        self.down = down.astype(np.int64)
        depth = np.zeros(n, dtype=np.int64)
        dist = (self.down >= 0).astype(np.int64)
        nxt = self.down.copy()
        while True:                                    # pointer jumping: hops to a terminal
            live = nxt >= 0
            if not live.any():
                break
            dist[live] += dist[nxt[live]]
            nxt[live] = nxt[nxt[live]]
        depth[:] = dist
        self.depth = depth
        levels = max(1, int(depth.max()).bit_length())
        up = [self.down.copy()]
        for _ in range(1, levels):
            prev = up[-1]
            nxt = np.where(prev >= 0, prev[np.maximum(prev, 0)], -1)
            up.append(nxt)
        self.up = up

    def _lift(self, v: np.ndarray, steps: np.ndarray) -> np.ndarray:
        v = v.copy()
        for k, table in enumerate(self.up):
            take = ((steps >> k) & 1).astype(bool) & (v >= 0)
            v[take] = table[v[take]]
        return v

    def join_hops(self, c: np.ndarray, l: np.ndarray) -> np.ndarray:
        """Hops from ``c`` down to where ``l``'s downstream path joins ``c``'s (0 when ``l``
        is ``c`` or upstream of it); -1 when the paths never meet in the region."""
        c = np.asarray(c, dtype=np.int64)
        l = np.asarray(l, dtype=np.int64)
        dc, dl = self.depth[c], self.depth[l]
        a = self._lift(c, np.maximum(dc - dl, 0))
        b = self._lift(l, np.maximum(dl - dc, 0))
        same = a == b
        for table in reversed(self.up):
            move = ~same & (table[a] != table[b]) & (table[a] >= 0) & (table[b] >= 0)
            a = np.where(move, table[a], a)
            b = np.where(move, table[b], b)
        meet = np.where(same, a, np.where((self.up[0][a] == self.up[0][b]) & (self.up[0][a] >= 0),
                                          self.up[0][a], -1))
        return np.where(meet >= 0, dc - self.depth[np.maximum(meet, 0)], -1)


def region_paths(data_dir: Path, entry: dict) -> tuple[Paths, np.ndarray]:
    t = pq.read_table(data_dir / entry["lines"]["file"], columns=["nhdplusid", "dn_step"])
    step = t.column("dn_step").to_numpy().astype(np.int64)
    down = np.where(step != 0, np.arange(len(step)) + step, -1)
    return Paths(down), t.column("nhdplusid").to_numpy().astype(np.int64)


def original_lines(entry: dict, line_ids: np.ndarray, zips_dir: Path = config.ZIPS_DIR):
    """The region's network flowlines at full resolution, EPSG:5070, in ``lines2`` row order."""
    pkg = entry["package"]
    src = "/vsizip/" + (zips_dir / pkg["name"]).as_posix() + "/" + pkg["name"][:-4] + ".gdb"
    df = source.read_layer(pkg, "NHDFlowline", ["nhdplusid"], geometry=True, path=src)
    ids = np.round(df["nhdplusid"].to_numpy("float64")).astype(np.int64)
    pos = pd.Series(np.arange(len(ids)), index=ids)
    pos = pos[~pos.index.duplicated()]
    take = pos.reindex(line_ids).to_numpy()
    if np.isnan(take).any():
        raise RuntimeError(f"{entry['vpu']}: {int(np.isnan(take).sum())} network lines missing from the package")
    from pyproj import Transformer
    tr = Transformer.from_crs(4269, 5070, always_xy=True)
    geoms = shapely.force_2d(df.geometry.to_numpy()[take.astype(np.int64)])
    return shapely.transform(geoms, lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))


def riparian_pieces(region: RegionCells, lines_m, paths: Paths, line_ids: np.ndarray,
                    log: Callable = log_default):
    """``(pieces, catchment rows, k)``, sorted by ``k`` descending (rasterizing in this
    order with replacement leaves each cell its smallest ``k``)."""
    t0 = time.time()
    buffers = shapely.buffer(lines_m, RIPARIAN_M, quad_segs=BUFFER_QUAD_SEGS)
    cats_m = region.geoms_m()
    tree = shapely.STRtree(cats_m)
    li, ci = tree.query(buffers, predicate="intersects")
    line_of_cat = pd.Series(np.arange(len(line_ids)), index=line_ids).reindex(region.ids).to_numpy()
    has = ~np.isnan(line_of_cat)
    keep = has[ci]
    li, ci = li[keep], ci[keep]
    k = paths.join_hops(line_of_cat[ci].astype(np.int64), li)
    keep = k >= 0
    li, ci, k = li[keep], ci[keep], k[keep]
    pieces = shapely.intersection(buffers[li], cats_m[ci])
    ok = ~shapely.is_empty(pieces) & (shapely.area(pieces) > 0)
    pieces, ci, k = pieces[ok], ci[ok], k[ok]
    order = np.argsort(-k, kind="stable")
    log(f"[{region.vpu}] riparian: {len(lines_m)} lines, {len(pieces)} line-catchment pieces "
        f"(k up to {int(k.max()) if len(k) else 0}) in {time.time() - t0:.0f} s")
    return pieces[order], ci[order], k[order]


# --------------------------------------------------------------------------- #
# the pass over the tiles
# --------------------------------------------------------------------------- #
def landcover(data_dir: Path, vpu: str, *, sources_root: Path = sources.SOURCES_DIR,
              out_dir: Optional[Path] = None, log: Callable = log_default) -> dict:
    """Land cover and riparian pieces for one region; writes ``lc2_`` and ``rip2_``."""
    from affine import Affine
    from rasterio.features import rasterize
    t0 = time.time()
    region = RegionCells(data_dir, vpu)
    paths, line_ids = region_paths(data_dir, region.entry)
    lines_m = original_lines(region.entry, line_ids)
    pieces, piece_rows, piece_k = riparian_pieces(region, lines_m, paths, line_ids, log)
    pb = shapely.bounds(pieces)
    tiles = region.nlcd_tiles()
    n = region.n
    lc = np.zeros((n, 17), dtype=np.int64)
    imp = np.zeros((n, 4), dtype=np.int64)              # sum21, n21, sum01, n01
    rip_parts = []
    for t, (i, j) in enumerate(tiles, 1):
        ix0, iy1 = _tile_window(i, j)
        rows = region.rows_in(ix0, iy1, _TILE10, _TILE10)
        if not len(rows):
            continue
        lab = region.labels(rows, ix0, iy1, _TILE10, _TILE10)
        mask = lab > 0
        if not mask.any():
            continue
        lab_m = lab[mask].astype(np.int64) - 1
        cover = _LC_LUT[_read_nlcd("lc2021", i, j, sources_root)[mask]]
        lc += np.bincount(lab_m * 17 + cover, minlength=n * 17).reshape(n, 17)
        for col, layer in ((0, "imp2021"), (2, "imp2001")):
            v = _read_nlcd(layer, i, j, sources_root)[mask].astype(np.int64)
            valid = v <= 100
            imp[:, col] += np.bincount(lab_m[valid], weights=v[valid], minlength=n).astype(np.int64)
            imp[:, col + 1] += np.bincount(lab_m[valid], minlength=n)
        # riparian: pieces inside this window, in k-descending order, smallest k wins
        x0m, y1m = ix0 * 10.0 + 5.0, iy1 * 10.0 + 5.0
        x1m, y0m = x0m + _TILE10 * 10.0, y1m - _TILE10 * 10.0
        sel = np.nonzero((pb[:, 0] < x1m) & (pb[:, 2] > x0m) & (pb[:, 1] < y1m) & (pb[:, 3] > y0m))[0]
        if len(sel):
            kr = rasterize(((pieces[s], int(piece_k[s]) + 1) for s in sel), out_shape=(_TILE10, _TILE10),
                           transform=Affine(10.0, 0, x0m, 0, -10.0, y1m), fill=0, dtype="int32",
                           all_touched=False)
            kr_m = kr[mask]
            on = kr_m > 0
            if on.any():
                df = pd.DataFrame({"row": lab_m[on], "k": kr_m[on].astype(np.int64) - 1, "cls": cover[on]})
                v21 = _read_nlcd("imp2021", i, j, sources_root)[mask][on].astype(np.int64)
                v01 = _read_nlcd("imp2001", i, j, sources_root)[mask][on].astype(np.int64)
                df["imp21"] = np.where(v21 <= 100, v21, 0)
                df["n21"] = (v21 <= 100).astype(np.int64)
                df["imp01"] = np.where(v01 <= 100, v01, 0)
                df["n01"] = (v01 <= 100).astype(np.int64)
                counts = pd.crosstab([df["row"], df["k"]], df["cls"])
                sums = df.groupby(["row", "k"])[["imp21", "n21", "imp01", "n01"]].sum()
                rip_parts.append(counts.join(sums, how="outer").fillna(0).astype(np.int64))
        if t % 10 == 0 or t == len(tiles):
            log(f"[{vpu}] land cover: {t}/{len(tiles)} tiles, {time.time() - t0:.0f} s")
    counted = lc.sum(axis=1)
    bad = np.nonzero(counted != region.cells)[0]
    if len(bad):
        raise RuntimeError(f"{vpu}: {len(bad)} catchments not counted cell for cell "
                           f"(first rows {bad[:5].tolist()}; counted {counted[bad[:5]].tolist()}, "
                           f"cells {region.cells[bad[:5]].tolist()})")
    out_dir = out_dir or (data_dir.parent / "values")
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = {"nhdplusid": pa.array(region.ids)}
    for c, name in enumerate(LC_COLUMNS):
        cols[name] = pa.array(lc[:, c].astype(np.int32))
    for c, name in enumerate(("imp21_sum", "imp21_n", "imp01_sum", "imp01_n")):
        cols[name] = pa.array(imp[:, c].astype(np.int64 if name.endswith("sum") else np.int32))
    lc_path = out_dir / f"lc2_{vpu}.parquet"
    pq.write_table(pa.table(cols), lc_path, compression="zstd", compression_level=19)
    # riparian pieces: merge the tiles (a piece can span tiles), then one row per (row, k)
    if rip_parts:
        rip = pd.concat(rip_parts).groupby(level=[0, 1]).sum()
    else:
        rip = pd.DataFrame(columns=list(range(17)) + ["imp21", "n21", "imp01", "n01"])
    rip = rip.reindex(columns=list(range(17)) + ["imp21", "n21", "imp01", "n01"], fill_value=0)
    idx = rip.index.to_frame(index=False)
    rcols = {"row": pa.array(idx["row"].to_numpy().astype(np.int32)),
             "k": pa.array(idx["k"].to_numpy().astype(np.int32))}
    for c, name in enumerate(LC_COLUMNS):
        rcols[name] = pa.array(rip[c].to_numpy().astype(np.int32))
    for name, src_col in (("imp21_sum", "imp21"), ("imp21_n", "n21"), ("imp01_sum", "imp01"), ("imp01_n", "n01")):
        rcols[name] = pa.array(rip[src_col].to_numpy().astype(np.int64 if name.endswith("sum") else np.int32))
    rip_path = out_dir / f"rip2_{vpu}.parquet"
    pq.write_table(pa.table(rcols), rip_path, compression="zstd", compression_level=19)
    stats = {"vpu": vpu, "catchments": int(n), "tiles": len(tiles), "pieces": int(len(rip)),
             "lc_bytes": lc_path.stat().st_size, "rip_bytes": rip_path.stat().st_size,
             "seconds": round(time.time() - t0)}
    log(f"[{vpu}] land cover done: {stats}")
    return stats


def values_task(what: str, data_dir: str, vpu: str) -> dict:
    """Process-pool entry: one stage for one region; logs to ``<root>/logs/values_<vpu>.log``;
    a failure is returned, never raised."""
    import traceback
    import warnings
    warnings.filterwarnings("ignore")
    root = Path(data_dir).parent
    (root / "logs").mkdir(parents=True, exist_ok=True)
    log_path = root / "logs" / f"values_{vpu}.log"

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    try:
        if what == "landcover":
            stats = landcover(Path(data_dir), vpu, log=log)
        elif what in ("roads", "dams"):
            from . import vectors
            stats = getattr(vectors, what)(Path(data_dir), vpu, log=log)
        elif what == "soils":
            from .soils import soils
            stats = soils(Path(data_dir), vpu, log=log)
        elif what == "extras":
            from .extras import extras
            stats = extras(Path(data_dir), vpu, log=log)
        elif what == "nwi":
            from .extras import nwi_update
            stats = nwi_update(Path(data_dir), vpu, log=log)
        else:
            raise ValueError(what)
        return dict(stats, ok=True, vpu=vpu)
    except Exception as exc:  # report, keep the pool going
        log(f"[{vpu}] FAILED {type(exc).__name__}: {exc}")
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc())
        return {"vpu": vpu, "ok": False, "error": f"{type(exc).__name__}: {exc}"}

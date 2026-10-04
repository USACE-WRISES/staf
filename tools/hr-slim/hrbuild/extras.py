"""Per-flowline extras (bundle v2, Phase 3): sinuosity and the ATTAINS assessment unit.

``extras2_<vpu>.parquet``, one row per network flowline (the row order of ``lines2``):

- ``sinuosity``: the site engine's rule (``geometry.line_sinuosity``): the first part of the
  original USGS line in EPSG:5070, length over the straight distance between its ends, 3 decimals;
- ATTAINS at three points along the line (5, 50 and 95 percent of its length): EASI's M16 asks
  the assessment unit at the point (``impairment_at_point``: a point, line or area unit within
  half a metre, ties to the smallest unit id) and, without one, the nearest unit within 2 km
  (``impairment_near_point``: smallest distance, then unit id). ``au_exact_<p>`` and
  ``au_near_<p>`` index ``au2_<vpu>.parquet`` (-1 for none), ``au_near_m_<p>`` is the distance.
  The app reads the sample nearest its snapped point along the line.

- ``huc12_<p>``: the current WBD HUC12 containing each of the same three points (0 for none);
  the apps look it up at the point today;
- the NWI strip (SFARI #28, owner decision D5): wetland area within 150 m of the line's
  centreline, by Cowardin system (``nwi_r_m2`` riverine, ``nwi_p_m2`` palustrine, ``nwi_l_m2``
  lacustrine, ``nwi_e_m2`` estuarine, ``nwi_m_m2`` marine), and the strip's own area
  (``strip_m2``). The strip has flat ends, so consecutive flowlines tile without double counting
  at their joints; an assessment reach sums the flowlines it covers.

ATTAINS comes from the national builder's pull of EPA's service
(``D:\\Data\\easi-national\\national\\attains.parquet``, the service's own fields).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from .zonal import RegionCells, log_default, original_lines, region_paths

ATTAINS_PARQUET = Path(r"D:\Data\easi-national\national\attains.parquet")
ATTAINS_GDB = Path(r"D:\Data\easi-national\national\attains\ATTAINS_Assessment_20260903.gdb")
SAMPLES = (0.05, 0.5, 0.95)
NWI_STRIP_M = 150.0
NWI_SYSTEMS = ("R", "P", "L", "E", "M")
SUBDIVIDE_VERTICES = 256  # wetland pieces stay this small, so each strip intersection is cheap
HEAVY_WORK = 2_000_000    # a wetland is cut first when its vertices times the strips it meets pass this
EXACT_M = 0.001          # the service's point intersect: a point on a line within its XY tolerance
NEAR_M = 2000.0
_cache: dict = {}


def sinuosity(lines_m) -> np.ndarray:
    first = shapely.get_geometry(lines_m, 0)
    first = np.where(shapely.get_type_id(lines_m) == 5, first, lines_m)
    n = shapely.get_num_points(first)
    a = shapely.get_point(first, 0)
    b = shapely.get_point(first, n - 1)
    straight = shapely.distance(a, b)
    length = shapely.length(first)
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(straight > 0, np.round(length / straight, 3), np.nan)
    return s


def attains_units(box5070=None):
    """``(geometries in EPSG:5070, attributes, rank, tree)``, one row per feature of EPA's national
    ATTAINS geodatabase (points, lines and areas: the service's layers 0, 1 and 2, every part
    kept); overall status and the impaired flag come from the national builder's pull of the
    service, joined by unit. ``rank`` orders by unit id, then layer, the app's tie rule."""
    key = ("attains", None if box5070 is None else tuple(round(v) for v in box5070))
    if key not in _cache:
        import pyogrio
        from pyproj import Transformer
        tr = Transformer.from_crs(3857, 5070, always_xy=True)
        bbox = None
        if box5070 is not None:                  # the region's box plus the 2 km search, in the layers' CRS
            back = Transformer.from_crs(5070, 3857, always_xy=True)
            w, s_, e, n = box5070
            pad = NEAR_M + 100.0
            xs, ys = back.transform([w - pad, e + pad, w - pad, e + pad], [s_ - pad, s_ - pad, n + pad, n + pad])
            bbox = (min(xs), min(ys), max(xs), max(ys))
        frames, geoms = [], []
        for layer, name in ((0, "attains_au_points"), (1, "attains_au_lines"), (2, "attains_au_areas")):
            df = pyogrio.read_dataframe(ATTAINS_GDB, layer=name, force_2d=True, bbox=bbox,
                                        columns=["assessmentunitidentifier", "assessmentunitname", "ircategory"])
            df = df[df.geometry.notna() & ~df.geometry.is_empty].reset_index(drop=True)
            g = shapely.transform(df.geometry.to_numpy(), lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))
            frames.append(pd.DataFrame({"layer": layer,
                                        "assessment_unit": df["assessmentunitidentifier"].astype(str).to_numpy(),
                                        "assessment_name": df["assessmentunitname"].astype(str).to_numpy(),
                                        "ircategory": df["ircategory"].astype(str).to_numpy()}))
            geoms.append(g)
        t = pd.concat(frames, ignore_index=True)
        geoms = np.concatenate(geoms)
        status = pq.read_table(ATTAINS_PARQUET, columns=["layer", "assessment_unit", "overallstatus", "isimpaired"]) \
            .to_pandas().drop_duplicates(["layer", "assessment_unit"])
        t = t.merge(status, on=["layer", "assessment_unit"], how="left")
        order = np.lexsort((t["layer"].to_numpy(), t["assessment_unit"].to_numpy()))
        rank = np.empty(len(t), dtype=np.int64)
        rank[order] = np.arange(len(t))
        for old_key in [k for k in _cache if isinstance(k, tuple) and k[0] == "attains"]:
            del _cache[old_key]                  # one region's units at a time per process
        _cache[key] = (geoms, t, rank, shapely.STRtree(geoms))
    return _cache[key]


def attains_at(points, box5070=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(exact unit, nearby unit, nearby distance m)`` per point (-1 / NaN for none)."""
    geoms, attrs, rank, tree = attains_units(box5070)
    n = len(points)
    exact = np.full(n, -1, dtype=np.int64)
    pi, gi = tree.query(points, predicate="dwithin", distance=EXACT_M)
    if len(pi):
        df = pd.DataFrame({"p": pi, "g": gi, "r": rank[gi]}).sort_values(["p", "r"]).drop_duplicates("p")
        exact[df["p"].to_numpy()] = df["g"].to_numpy()
    near = np.full(n, -1, dtype=np.int64)
    dist = np.full(n, np.nan)
    (pi, gi), d = tree.query_nearest(points, max_distance=NEAR_M, return_distance=True, all_matches=True)
    if len(pi):
        df = pd.DataFrame({"p": pi, "g": gi, "d": d, "r": rank[gi]}).sort_values(["p", "d", "r"]).drop_duplicates("p")
        near[df["p"].to_numpy()] = df["g"].to_numpy()
        dist[df["p"].to_numpy()] = df["d"].to_numpy()
    return exact, near, dist


def huc12_polygons():
    """Current WBD HUC12s in EPSG:5070 (codes as int64), cached as Parquet beside the zip."""
    if "huc12" not in _cache:
        from . import sources
        cache = sources.SOURCES_DIR / "wbd" / "huc12_5070.parquet"
        if not cache.exists():
            import pyogrio
            from pyproj import Transformer
            gdb = sources.SOURCES_DIR / "wbd" / "WBD_National_GDB.gdb"
            if not gdb.exists():                      # zipped, every seek re-inflates the table
                import zipfile
                tmp = gdb.parent / "unzip.tmp"
                with zipfile.ZipFile(sources.SOURCES_DIR / "wbd" / "WBD_National_GDB.zip") as z:
                    z.extractall(tmp)
                (tmp / "WBD_National_GDB.gdb").replace(gdb)
            df = pyogrio.read_dataframe(gdb, layer="WBDHU12", columns=["huc12", "name"], force_2d=True)
            tr = Transformer.from_crs(4269, 5070, always_xy=True)
            g = shapely.transform(df.geometry.to_numpy(), lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))
            pq.write_table(pa.table({"huc12": pa.array(df["huc12"].astype(str).tolist()),
                                     "name": pa.array(df["name"].astype(str).tolist()),
                                     "wkb": pa.array(shapely.to_wkb(g).tolist(), type=pa.binary())}), cache,
                           compression="zstd")
        t = pq.read_table(cache)
        geoms = shapely.from_wkb(t.column("wkb").to_numpy(zero_copy_only=False))
        codes = np.array([int(c) if str(c).isdigit() else 0 for c in t.column("huc12").to_pylist()], dtype=np.int64)
        _cache["huc12"] = (geoms, codes, shapely.STRtree(geoms))
    return _cache["huc12"]


def huc12_at(points) -> np.ndarray:
    geoms, codes, tree = huc12_polygons()
    out = np.zeros(len(points), dtype=np.int64)
    pi, gi = tree.query(points, predicate="intersects")
    if len(pi):
        df = pd.DataFrame({"p": pi, "c": codes[gi]}).sort_values(["p", "c"]).drop_duplicates("p")
        out[df["p"].to_numpy()] = df["c"].to_numpy()
    return out


def _nwi_gdb(st: str) -> Path:
    """The state's NWI geodatabase, unzipped once next to its zip (zipped, every seek re-inflates)."""
    import zipfile
    from . import sources
    folder = sources.SOURCES_DIR / "nwi" / st
    gdb = folder / f"{st}_geodatabase_wetlands.gdb"
    if not gdb.exists():
        tmp = folder.with_name(folder.name + ".tmp")
        with zipfile.ZipFile(sources.SOURCES_DIR / "nwi" / f"{st}_geodatabase_wetlands.zip") as z:
            z.extractall(tmp)
        tmp.replace(folder)
    return gdb


def _polygonal(g):
    """The polygon parts of a geometry (an intersection with a box can add lines and points)."""
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [q for q in shapely.get_parts(g) if q.geom_type in ("Polygon", "MultiPolygon")]
    if not parts:
        return None
    return shapely.union_all(parts) if len(parts) > 1 else parts[0]


def subdivide(geoms, max_vertices: int = SUBDIVIDE_VERTICES) -> tuple[np.ndarray, np.ndarray]:
    """Polygons cut by halving their bounds until no piece has more than ``max_vertices``
    coordinates (as PostGIS ST_Subdivide). Pieces do not overlap and together cover each polygon, so
    intersection areas summed over them equal the polygon's, while each intersection stays small
    (Maryland's Chesapeake Bay polygon has 4 million vertices). Returns ``(pieces, parent)``."""
    geoms = np.asarray(geoms, dtype=object)
    n = shapely.get_num_coordinates(geoms)
    small = np.nonzero(n <= max_vertices)[0]
    pieces, parent = [geoms[small]], [small]
    for i in np.nonzero(n > max_vertices)[0]:
        stack, out = [(geoms[i], 0)], []
        while stack:
            h, depth = stack.pop()
            x0, y0, x1, y1 = h.bounds
            if shapely.get_num_coordinates(h) <= max_vertices or depth >= 40 or max(x1 - x0, y1 - y0) < 1.0:
                out.append(h)
                continue
            if x1 - x0 >= y1 - y0:
                m = (x0 + x1) / 2
                halves = (shapely.box(x0, y0, m, y1), shapely.box(m, y0, x1, y1))
            else:
                m = (y0 + y1) / 2
                halves = (shapely.box(x0, y0, x1, m), shapely.box(x0, m, x1, y1))
            for b in halves:
                part = _polygonal(shapely.intersection(h, b))
                if part is not None and not part.is_empty:
                    stack.append((part, depth + 1))
        pieces.append(np.asarray(out, dtype=object))
        parent.append(np.full(len(out), i))
    return np.concatenate(pieces), np.concatenate(parent)


def nwi_strips(lines_m, vpu: str, log: Callable = log_default) -> dict:
    """Wetland area by system within 150 m of each line (flat ends), and the strip area."""
    import pyogrio
    from pyproj import CRS, Transformer
    from . import sources
    strips = shapely.buffer(lines_m, NWI_STRIP_M, cap_style="flat")
    out = dict((f"nwi_{c.lower()}_m2", np.zeros(len(lines_m))) for c in NWI_SYSTEMS)
    out["strip_m2"] = shapely.area(strips)
    box = shapely.box(*shapely.total_bounds(strips))
    tree = shapely.STRtree(strips)
    # State downloads repeat the wetlands along a shared border (the Connecticut River is in both the
    # New Hampshire and the Vermont geodatabase): a wetland already counted from another state is
    # skipped, matched on its code, area and centroid to 0.1 m.
    seen: set = set()
    for st in sources.states([vpu]):
        if not (sources.SOURCES_DIR / "nwi" / f"{st}_geodatabase_wetlands.zip").exists():
            log(f"[{vpu}] NWI: no geodatabase for {st}, skipped")
            continue
        gdb = _nwi_gdb(st)
        layer = f"{st}_Wetlands"
        crs = CRS.from_wkt(pyogrio.read_info(gdb, layer=layer)["crs"])
        to5070 = Transformer.from_crs(crs, 5070, always_xy=True)
        back = Transformer.from_crs(5070, crs, always_xy=True)
        bb = shapely.bounds(shapely.transform(box, lambda q: np.column_stack(back.transform(q[:, 0], q[:, 1]))))
        df = pyogrio.read_dataframe(gdb, layer=layer, columns=["ATTRIBUTE"], bbox=tuple(bb), force_2d=True)
        if not len(df):
            continue
        g = shapely.transform(df.geometry.to_numpy(), lambda q: np.column_stack(to5070.transform(q[:, 0], q[:, 1])))
        g = shapely.make_valid(g)
        attr = df["ATTRIBUTE"].astype(str).to_numpy()
        cx, cy = shapely.get_coordinates(shapely.centroid(g)).T
        keys = [f"{a}|{ar:.1f}|{x:.1f}|{y:.1f}" for a, ar, x, y in zip(attr, shapely.area(g), cx, cy)]
        fresh = np.array([k not in seen for k in keys])
        seen.update(keys)
        repeated = int((~fresh).sum())
        g, attr = g[fresh], attr[fresh]
        system = np.array([a[:1].upper() for a in attr])
        wi, li = tree.query(g, predicate="intersects")
        if not len(wi):
            continue
        work = np.bincount(wi, minlength=len(g)) * shapely.get_num_coordinates(g)
        heavy = np.nonzero(work > HEAVY_WORK)[0]
        pair_geoms, pair_lines, pair_system = g[wi], li, system[wi]
        if len(heavy):
            pieces, parent = subdivide(g[heavy])
            pi, pl = tree.query(pieces, predicate="intersects")
            light = ~np.isin(wi, heavy)
            pair_geoms = np.concatenate([g[wi[light]], pieces[pi]])
            pair_lines = np.concatenate([li[light], pl])
            pair_system = np.concatenate([system[wi[light]], system[heavy][parent[pi]]])
        area = shapely.area(shapely.intersection(strips[pair_lines], pair_geoms))
        for c in NWI_SYSTEMS:
            sel = pair_system == c
            out[f"nwi_{c.lower()}_m2"] += np.bincount(pair_lines[sel], weights=area[sel], minlength=len(lines_m))
        log(f"[{vpu}] NWI {st}: {len(df)} wetlands in the region box ({repeated} already counted from another "
            f"state, {len(heavy)} cut into pieces first), {len(pair_lines)} strip pieces")
    return out


def nwi_update(data_dir: Path, vpu: str, *, out_dir: Optional[Path] = None, log: Callable = log_default) -> dict:
    """Recompute the NWI strip columns of an existing ``extras2_<vpu>`` file in place (the other
    columns are kept)."""
    t0 = time.time()
    region = RegionCells(data_dir, vpu)
    _, line_ids = region_paths(data_dir, region.entry)
    out_dir = Path(out_dir or (data_dir.parent / "values"))
    path = out_dir / f"extras2_{vpu}.parquet"
    t = pq.read_table(path)
    if not np.array_equal(t.column("nhdplusid").to_numpy(), np.asarray(line_ids)):
        raise RuntimeError(f"{vpu}: extras rows no longer match the lines; rerun extras")
    lines_m = original_lines(region.entry, line_ids)
    for name, v in nwi_strips(lines_m, vpu, log).items():
        t = t.set_column(t.column_names.index(name), name, pa.array(v.astype(np.float32)))
    pq.write_table(t, path, compression="zstd", compression_level=19)
    stats = {"vpu": vpu, "lines": t.num_rows, "bytes": path.stat().st_size, "seconds": round(time.time() - t0)}
    log(f"[{vpu}] NWI strips updated: {stats}")
    return stats


def extras(data_dir: Path, vpu: str, *, out_dir: Optional[Path] = None, log: Callable = log_default) -> dict:
    t0 = time.time()
    region = RegionCells(data_dir, vpu)
    _, line_ids = region_paths(data_dir, region.entry)
    lines_m = original_lines(region.entry, line_ids)
    cols = {"nhdplusid": pa.array(line_ids)}
    sin = sinuosity(lines_m)
    cols["sinuosity"] = pa.array(sin.astype(np.float32), mask=np.isnan(sin))
    pts = np.concatenate([shapely.line_interpolate_point(lines_m, f, normalized=True) for f in SAMPLES])
    box = tuple(shapely.total_bounds(lines_m))
    exact, near, dist = attains_at(pts, box)
    attrs = attains_units(box)[1]
    used = np.unique(np.concatenate([exact[exact >= 0], near[near >= 0]]))
    keys = (attrs.iloc[used][["layer", "assessment_unit"]].astype(str).agg("|".join, axis=1).to_numpy()
            if len(used) else np.empty(0, dtype=object))      # no unit near (a Canadian unit)
    uniq, first = np.unique(keys, return_index=True)
    remap = np.full(max(len(attrs), 1), -1, dtype=np.int64)    # one slot even with no unit, for the -1s
    remap[used] = np.searchsorted(uniq, keys)
    used = used[first]
    n = len(lines_m)
    for k, f in enumerate(SAMPLES):
        tag = f"{int(round(f * 100)):02d}"
        e, ne, d = exact[k * n:(k + 1) * n], near[k * n:(k + 1) * n], dist[k * n:(k + 1) * n]
        cols[f"au_exact_{tag}"] = pa.array(np.where(e >= 0, remap[np.maximum(e, 0)], -1).astype(np.int32))
        cols[f"au_near_{tag}"] = pa.array(np.where(ne >= 0, remap[np.maximum(ne, 0)], -1).astype(np.int32))
        cols[f"au_near_m_{tag}"] = pa.array(np.round(d, 1).astype(np.float32), mask=np.isnan(d))
    hucs = huc12_at(pts)
    for k, f in enumerate(SAMPLES):
        cols[f"huc12_{int(round(f * 100)):02d}"] = pa.array(hucs[k * n:(k + 1) * n])
    for name, v in nwi_strips(lines_m, vpu, log).items():
        cols[name] = pa.array(v.astype(np.float32))
    out_dir = Path(out_dir or (data_dir.parent / "values"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"extras2_{vpu}.parquet"
    pq.write_table(pa.table(cols), path, compression="zstd", compression_level=19)
    au = attrs.iloc[used].reset_index(drop=True)
    au_path = out_dir / f"au2_{vpu}.parquet"
    pq.write_table(pa.Table.from_pandas(au, preserve_index=False), au_path, compression="zstd", compression_level=19)
    varies = 0
    e0, e1, e2 = (cols[f"au_near_{t}"].to_numpy(zero_copy_only=False) for t in ("05", "50", "95"))
    varies = int(((e0 != e1) | (e1 != e2)).sum())
    stats = {"vpu": vpu, "lines": n, "units": int(len(used)),
             "lines_with_exact_unit": int((cols["au_exact_50"].to_numpy(zero_copy_only=False) >= 0).sum()),
             "lines_with_nearby_unit": int((e1 >= 0).sum()), "lines_where_nearby_unit_varies": varies,
             "bytes": path.stat().st_size + au_path.stat().st_size, "seconds": round(time.time() - t0)}
    log(f"[{vpu}] extras done: {stats}")
    return stats

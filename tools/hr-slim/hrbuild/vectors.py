"""Per-catchment roads, road-stream crossings and dams (bundle v2, Phase 3).

Roads (``roads2_<vpu>.parquet``, one row per catchment): the length of TIGER/Line 2025 roads
inside each catchment, in metres. The site engine reads TIGERweb layers 2, 6 and 8, which hold
every TIGER road class (S1100 to S1830) once; the county files hold the same features, and a road
on a county line appears in both counties' files, so duplicates are dropped. Lengths add up over
catchments exactly (a watershed is the union of its catchments). A county whose 2025 file the
Census server refuses (it answers with a firewall rejection page) is read from its 2024 file, and a
Canadian unit, which touches no county, has no roads (TIGERweb has none there either).

Crossings (``xings2_<vpu>.parquet``, one row per point): every point where a road meets a network
flowline (original USGS geometry), with the road and the line. The engine counts, per road, the
points where it meets the union of the tree's flowlines, so a watershed counts the distinct
(road, point) pairs among its lines' rows; a road along a channel shares a segment, not a point,
and counts nothing, as in the engine.

Dams (``dams2_<vpu>.parquet``, one row per catchment): NID dams inside each catchment, their
normal storage and NID storage (acre-feet) and how many lack a normal storage (counted as zero,
as the engine does).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from . import sources
from .zonal import RegionCells, log_default, original_lines, region_paths

ROAD_CLASSES = ("S1100", "S1200", "S1400", "S1500", "S1630", "S1640", "S1710", "S1720", "S1730",
                "S1740", "S1750", "S1780", "S1810", "S1820", "S1830")
_nid_cache: dict = {}


def _to5070(geoms):
    from pyproj import Transformer
    tr = Transformer.from_crs(4269, 5070, always_xy=True)
    return shapely.transform(geoms, lambda q: np.column_stack(tr.transform(q[:, 0], q[:, 1])))


def region_counties(vpu: str, root: Path = sources.SOURCES_DIR) -> list[str]:
    path = root / "tiger" / str(sources.TIGER_YEAR) / "regions" / f"{vpu}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    geoids = sources.counties([vpu])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(geoids), encoding="utf-8")
    return geoids


def county_roads_zip(geoid: str, root: Path = sources.SOURCES_DIR, log: Callable = log_default) -> tuple[Path, int]:
    """The county's TIGER/Line roads zip and its year (``sources.tiger_county_zip``: the year before's
    file when the Census server refuses this year's)."""
    return sources.tiger_county_zip(geoid, root, log)


def read_roads(geoids: list[str], root: Path = sources.SOURCES_DIR, log: Callable = log_default):
    """TIGER roads of the counties, EPSG:5070, one feature per distinct road part; the duplicates
    dropped; and ``{county: year}`` for counties read from another year's file."""
    import pyogrio
    frames, other_year = [], {}
    for g in geoids:
        z, year = county_roads_zip(g, root, log)
        if year != sources.TIGER_YEAR:
            other_year[g] = year
        frames.append(pyogrio.read_dataframe(f"/vsizip/{z.as_posix()}/{z.stem}.shp", columns=["LINEARID", "MTFCC"]))
    if not frames:                                  # a Canadian unit: no county, no TIGER roads
        return np.empty(0, dtype=object), 0, other_year
    df = pd.concat(frames, ignore_index=True)
    df = df[df["MTFCC"].isin(ROAD_CLASSES) & df.geometry.notna()].reset_index(drop=True)
    geoms = shapely.force_2d(df.geometry.to_numpy())
    key = pd.Series(shapely.to_wkb(shapely.set_precision(geoms, 1e-7), output_dimension=2)).map(hash)
    keep = ~pd.DataFrame({"id": df["LINEARID"].astype(str), "g": key}).duplicated().to_numpy()
    return _to5070(geoms[keep]), int((~keep).sum()), other_year


def _points(geoms, owner):
    """Points (x, y) of intersection results and the index of the pair each came from."""
    parts, idx = shapely.get_parts(geoms, return_index=True)
    deeper = shapely.get_type_id(parts) >= 4                      # multi parts inside a collection
    if deeper.any():
        sub, sidx = shapely.get_parts(parts[deeper], return_index=True)
        parts = np.concatenate([parts[~deeper], sub])
        idx = np.concatenate([idx[~deeper], idx[deeper][sidx]])
    pt = shapely.get_type_id(parts) == 0
    xy = shapely.get_coordinates(parts[pt])
    return xy, owner[idx[pt]]


def roads(data_dir: Path, vpu: str, *, out_dir=None, log: Callable = log_default) -> dict:
    t0 = time.time()
    region = RegionCells(data_dir, vpu)
    cats_m = region.geoms_m()
    roads_m, dups, other_year = read_roads(region_counties(vpu), log=log)
    tree = shapely.STRtree(cats_m)
    ri, ci = tree.query(roads_m, predicate="intersects")
    lens = shapely.length(shapely.intersection(roads_m[ri], cats_m[ci]))
    road_m = np.bincount(ci, weights=lens, minlength=region.n)
    paths, line_ids = region_paths(data_dir, region.entry)
    lines_m = original_lines(region.entry, line_ids)
    rj, lj = shapely.STRtree(lines_m).query(roads_m, predicate="intersects")
    pts = shapely.intersection(roads_m[rj], lines_m[lj])
    pair = np.arange(len(rj))
    xy, owner = _points(pts, pair)
    xings = pd.DataFrame({"line_row": lj[owner].astype(np.int32), "road": rj[owner].astype(np.int32),
                          "x_dm": np.round(xy[:, 0] * 10).astype(np.int32),
                          "y_dm": np.round(xy[:, 1] * 10).astype(np.int32)}).drop_duplicates()
    out_dir = Path(out_dir or (data_dir.parent / "values"))
    out_dir.mkdir(parents=True, exist_ok=True)
    rpath = out_dir / f"roads2_{vpu}.parquet"
    pq.write_table(pa.table({"nhdplusid": pa.array(region.ids), "road_m": pa.array(road_m.astype(np.float32))}),
                   rpath, compression="zstd", compression_level=19)
    xpath = out_dir / f"xings2_{vpu}.parquet"
    xings = xings.sort_values(["line_row", "road"]).reset_index(drop=True)
    pq.write_table(pa.Table.from_pandas(xings, preserve_index=False), xpath, compression="zstd",
                   compression_level=19, column_encoding={"line_row": "DELTA_BINARY_PACKED"},
                   use_dictionary=False)
    stats = {"vpu": vpu, "roads": int(len(roads_m)), "duplicates_dropped": dups, "road_km": round(float(road_m.sum()) / 1000),
             "crossings": int(len(xings)), "roads_bytes": rpath.stat().st_size, "xings_bytes": xpath.stat().st_size,
             "seconds": round(time.time() - t0)}
    if other_year:
        stats["tiger_other_year"] = other_year
    log(f"[{vpu}] roads done: {stats}")
    return stats


def read_nid(root: Path = sources.SOURCES_DIR):
    """NID dams from the FeatureServer pull (the service the engine queries, its 4326 coordinates)."""
    if "nid" not in _nid_cache:
        path = sources.latest_nid_fs(root)
        df = pq.read_table(path).to_pandas()
        df = df[df["lon"].notna() & df["lat"].notna()].reset_index(drop=True)
        pts = _to5070(shapely.points(df["lon"].to_numpy(), df["lat"].to_numpy()))
        _nid_cache["nid"] = (df, pts, path.name)
    return _nid_cache["nid"]


def dams(data_dir: Path, vpu: str, *, out_dir=None, log: Callable = log_default) -> dict:
    t0 = time.time()
    region = RegionCells(data_dir, vpu)
    cats_m = region.geoms_m()
    df, pts, source_name = read_nid()
    pi, ci = shapely.STRtree(cats_m).query(pts, predicate="intersects")
    first = ~pd.Series(pi).duplicated().to_numpy()                 # a point on a shared edge: once
    pi, ci = pi[first], ci[first]
    normal = pd.to_numeric(df["normal_storage_acft"], errors="coerce").to_numpy()[pi]
    nid_st = pd.to_numeric(df["nid_storage_acft"], errors="coerce").to_numpy()[pi]
    n = region.n
    out = {
        "nhdplusid": pa.array(region.ids),
        "dams": pa.array(np.bincount(ci, minlength=n).astype(np.int16)),
        "normal_acft": pa.array(np.bincount(ci, weights=np.nan_to_num(normal), minlength=n)),
        "normal_missing": pa.array(np.bincount(ci, weights=np.isnan(normal).astype(float), minlength=n).astype(np.int16)),
        "nid_acft": pa.array(np.bincount(ci, weights=np.nan_to_num(nid_st), minlength=n)),
    }
    out_dir = Path(out_dir or (data_dir.parent / "values"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"dams2_{vpu}.parquet"
    pq.write_table(pa.table(out), path, compression="zstd", compression_level=19)
    stats = {"vpu": vpu, "dams": int(len(pi)), "source": source_name, "bytes": path.stat().st_size,
             "seconds": round(time.time() - t0)}
    log(f"[{vpu}] dams done: {stats}")
    return stats

"""The bundle's national point tables laid out by place, for reads by HTTP range.

The STAF data bundle keeps each point table (``tables/``) as one parquet row group in its build
order (NID by dam id, WQP stations by station id, gages by site number). A browser asking for the
dams in a one-mile box would read the whole table. Here each table is written again with the same
rows and columns, sorted along a Hilbert curve over longitude and latitude and cut into small row
groups whose statistics give each group's box, so a box or radius query reads the one to four groups
it needs:

- ``points/nid.parquet``, ``points/nwis.parquet``: ``nid_points`` and ``nwis_gages``, plus ``row0``,
  each row's position in the bundle's table (the order the apps' rules return rows in);
- ``points/wqp_stations.parquet``: ``wqp_stations`` plus ``row0`` (the station number the results
  carry) and, per station, its rows in the two result tables below (``r0``/``rn``, ``t0``/``tn``);
- ``points/wqp_results.parquet``, ``points/wqp_temperature.parquet``: every result, grouped by
  station in the stations' new order and in the bundle's order within a station (by date), so the
  results of nearby stations sit together.

Nothing is computed or rounded: a query over these files returns exactly the rows the bundle's
tables give (``_hrslim/points.py``), and ``row0`` restores their order where a rule depends on it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

#: rows per row group: one group spans a few miles to a few tens of miles
GROUP = 1024
#: the result tables' groups (results are many per station)
RESULT_GROUP = 8192
ORDER = 16
NAMES = {"nid": "nid_points.parquet", "nwis": "nwis_gages.parquet", "wqp_stations": "wqp_stations.parquet",
         "wqp_results": "wqp_results.parquet", "wqp_temperature": "wqp_temperature.parquet"}


def hilbert(lon: np.ndarray, lat: np.ndarray, order: int = ORDER) -> np.ndarray:
    """Each point's place along a Hilbert curve over the globe (``2**order`` cells a side); points
    without coordinates go last."""
    n = 1 << order
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    bad = ~(np.isfinite(lon) & np.isfinite(lat))
    x = np.clip(np.floor((np.nan_to_num(lon) + 180.0) / 360.0 * (n - 1)), 0, n - 1).astype(np.int64)
    y = np.clip(np.floor((np.nan_to_num(lat) + 90.0) / 180.0 * (n - 1)), 0, n - 1).astype(np.int64)
    d = np.zeros(len(x), dtype=np.int64)
    s = n // 2
    while s > 0:
        rx = ((x & s) > 0).astype(np.int64)
        ry = ((y & s) > 0).astype(np.int64)
        d += s * s * ((3 * rx) ^ ry)
        turn = ry == 0
        flip = turn & (rx == 1)
        x = np.where(flip, n - 1 - x, x)
        y = np.where(flip, n - 1 - y, y)
        x, y = np.where(turn, y, x), np.where(turn, x, y)
        s //= 2
    d[bad] = np.iinfo(np.int64).max
    return d


def _spatial(t):
    """``(table sorted along the curve with row0 added, order)``."""
    import pyarrow as pa
    lon = t.column("lon").to_numpy(zero_copy_only=False).astype(np.float64)
    lat = t.column("lat").to_numpy(zero_copy_only=False).astype(np.float64)
    row0 = np.arange(t.num_rows, dtype=np.int64)
    order = np.lexsort((row0, hilbert(lon, lat)))
    out = t.take(pa.array(order)).append_column("row0", pa.array(row0[order].astype(np.uint32)))
    return out, order


def _write(t, path: Path, group: int) -> Path:
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(t, tmp, row_group_size=group, compression="zstd", compression_level=9, write_statistics=True)
    tmp.replace(path)
    return path


def outputs(tables: Path, out: Path) -> list:
    """``[(logical, path)]`` of the laid-out tables whose bundle table exists."""
    keys = [k for k in ("nid", "nwis", "wqp_results", "wqp_temperature") if (Path(tables) / NAMES[k]).exists()]
    if (Path(tables) / NAMES["wqp_stations"]).exists():
        keys.append("wqp_stations")
    return [(f"{k}.parquet", Path(out) / f"{k}.parquet") for k in keys]


def write(tables: Path, out: Path, log=print) -> list:
    """Write the laid-out tables from the bundle's ``tables`` folder into ``out`` (again only when a
    bundle table is newer than its copies); returns ``[(logical, path)]``."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    tables, out = Path(tables), Path(out)
    have = outputs(tables, out)
    newest = max((p.stat().st_mtime for p in tables.glob("*.parquet")), default=0.0)
    if have and all(p.exists() and p.stat().st_mtime >= newest for _, p in have):
        return have
    done = []
    for key in ("nid", "nwis"):
        src = tables / NAMES[key]
        if src.exists():
            t, _ = _spatial(pq.read_table(src))
            done.append((f"{key}.parquet", _write(t, out / f"{key}.parquet", GROUP)))
    src = tables / NAMES["wqp_stations"]
    if src.exists():
        st, order = _spatial(pq.read_table(src))
        pos = np.empty(len(order), dtype=np.int64)
        pos[order] = np.arange(len(order))           # a station's place in the new order
        for key, first, count in (("wqp_results", "r0", "rn"), ("wqp_temperature", "t0", "tn")):
            rsrc = tables / NAMES[key]
            starts = np.zeros(len(order), dtype=np.int64)
            counts = np.zeros(len(order), dtype=np.int64)
            if rsrc.exists():
                r = pq.read_table(rsrc)
                station = r.column("station").to_numpy().astype(np.int64)
                rorder = np.lexsort((np.arange(r.num_rows), pos[station]))
                r = r.take(pa.array(rorder))
                counts = np.bincount(pos[station], minlength=len(order))
                starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
                done.append((f"{key}.parquet", _write(r, out / f"{key}.parquet", RESULT_GROUP)))
            st = st.append_column(first, pa.array(starts.astype(np.uint32))).append_column(count, pa.array(counts.astype(np.uint32)))
        done.append(("wqp_stations.parquet", _write(st, out / "wqp_stations.parquet", GROUP)))
    for logical, path in done:
        log(f"points/{logical}: {pq.ParquetFile(path).metadata.num_rows} rows, {path.stat().st_size / 1e6:.1f} MB")
    return done

"""The point tables laid out by place hold the bundle's rows unchanged (webdata/points.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webdata import points  # noqa: E402


def _tables(folder: Path, n_st: int = 20000, seed: int = 7) -> None:
    rng = np.random.default_rng(seed)
    folder.mkdir(parents=True, exist_ok=True)
    lon = rng.uniform(-125, -66, 5000)
    lat = rng.uniform(24, 50, 5000)
    lat[17] = np.nan                                      # a point without coordinates
    pq.write_table(pa.table({"nid_id": [f"D{i:05d}" for i in range(5000)], "name": [f"dam {i}" for i in range(5000)],
                             "lon": lon, "lat": lat, "nid_storage_acft": rng.uniform(0, 1e4, 5000)}),
                   folder / "nid_points.parquet")
    pq.write_table(pa.table({"station": [f"S-{i}" for i in range(n_st)], "lat": rng.uniform(24, 50, n_st),
                             "lon": rng.uniform(-125, -66, n_st), "name": [f"st {i}" for i in range(n_st)]}),
                   folder / "wqp_stations.parquet")
    k = 40000
    station = np.sort(rng.integers(0, n_st, k)).astype(np.int32)
    pq.write_table(pa.table({"station": station, "param": rng.integers(0, 2, k).astype(np.uint8),
                             "raw": rng.uniform(0, 5, k)}), folder / "wqp_results.parquet")


def test_layout_keeps_every_row(tmp_path):
    src, out = tmp_path / "tables", tmp_path / "out"
    _tables(src)
    done = dict(points.write(src, out, log=lambda *_: None))
    assert set(done) == {"nid.parquet", "wqp_results.parquet", "wqp_stations.parquet"}

    nid = pq.read_table(done["nid.parquet"])
    orig = pq.read_table(src / "nid_points.parquet")
    # row0 restores the bundle's order and every value
    back = nid.take(pa.array(np.argsort(nid.column("row0").to_numpy()))).drop(["row0"])
    assert back.to_pandas().equals(orig.to_pandas())          # NaN where the bundle has NaN
    assert np.isnan(nid.column("lat").to_numpy()[-1])        # no coordinates: last
    assert pq.ParquetFile(done["nid.parquet"]).metadata.num_row_groups == 5

    st = pq.read_table(done["wqp_stations.parquet"]).to_pydict()
    res = pq.read_table(done["wqp_results.parquet"]).to_pydict()
    orig_res = pq.read_table(src / "wqp_results.parquet").to_pydict()
    by_station: dict = {}
    for s, v in zip(orig_res["station"], orig_res["raw"]):
        by_station.setdefault(s, []).append(v)
    assert sum(st["rn"]) == len(res["raw"])
    for i in range(len(st["row0"])):
        s = st["row0"][i]
        span = range(st["r0"][i], st["r0"][i] + st["rn"][i])
        assert all(res["station"][j] == s for j in span)
        assert [res["raw"][j] for j in span] == by_station.get(s, [])   # the bundle's order within a station


def test_groups_are_compact(tmp_path):
    src, out = tmp_path / "tables", tmp_path / "out"
    _tables(src)
    path = dict(points.write(src, out, log=lambda *_: None))["wqp_stations.parquet"]
    md = pq.ParquetFile(path).metadata
    # a group's box is a small part of the whole extent, so a box query skips most groups
    assert md.num_row_groups == 20
    spans = []
    for g in range(md.num_row_groups):
        cols = dict((md.row_group(g).column(c).path_in_schema, md.row_group(g).column(c).statistics)
                    for c in range(md.row_group(g).num_columns))
        spans.append((cols["lon"].max - cols["lon"].min) * (cols["lat"].max - cols["lat"].min))
    assert sum(spans) / len(spans) < 0.15 * 59 * 26


def test_rewrites_only_when_stale(tmp_path):
    src, out = tmp_path / "tables", tmp_path / "out"
    _tables(src)
    first = dict(points.write(src, out, log=lambda *_: None))
    stamp = first["nid.parquet"].stat().st_mtime_ns
    points.write(src, out, log=lambda *_: None)
    assert first["nid.parquet"].stat().st_mtime_ns == stamp

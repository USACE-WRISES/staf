"""The cross-section stages on a synthetic valley: sampling into the archive
(resumable), derivation equal to the app's own function on the same
elevation, the published slim dict, and scoring from it."""
from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import MultiLineString

from builder import state
from builder.paths import DataRoot
from builder.stages import xs_derive, xs_sample
from builder.units import Chunk

LON, LAT = -78.5, 38.0
DEG_PER_M = 1 / 111_000.0
HUC8 = "02080204"
BANKFULL = {"width_m": 6.0, "depth_m": 0.5, "area_m2": 2.4, "division": "AP", "division_name": "Appalachian Highlands",
            "regional": True, "extrapolated": False, "fit_range_sqkm": [1, 1000]}


def _segment(lat0, lat1):
    return MultiLineString([[(LON, lat0), (LON, lat1)]])


def _root(tmp_path, n_reaches=3):
    root = DataRoot(tmp_path / "data").ensure()
    # a straight main path north-south: reach 1 downstream, reaches above it upstream
    comids = list(range(1, n_reaches + 2))
    hydroseq = [10 * c for c in comids]
    up = [10 * (c + 1) if c < comids[-1] else 0 for c in comids]
    dn = [10 * (c - 1) if c > 1 else 0 for c in comids]
    pq.write_table(pa.table({"comid": pa.array(comids, pa.int64()), "hydroseq": pa.array(hydroseq, pa.int64()),
                             "uphydroseq": pa.array(up, pa.int64()), "dnhydroseq": pa.array(dn, pa.int64()),
                             "lengthkm": pa.array([0.2] * len(comids), pa.float64()),
                             "huc8": [HUC8] * len(comids)}), root.vaa)
    import geopandas as gpd
    step = 200 * DEG_PER_M
    geoms = [_segment(LAT + c * step, LAT + (c - 1) * step) for c in comids]
    chunk = Chunk(id="huc8-test", kind="huc8", label="t", huc8s=[HUC8], bbox=[-79, 37, -78, 39])
    chunk.save(root)
    xs_sample.common.write_parquet(gpd.GeoDataFrame({"comid": comids}, geometry=geoms, crs="EPSG:4326"),
                                   root.chunk_raw("huc8-test", "flowlines"))
    derived = [{"comid": c, "lat": LAT + (c - 1) * step + 10 * DEG_PER_M, "lon": LON, "totdasqkm": 25.0,
                "bankfull": json.dumps(BANKFULL)} for c in comids[:n_reaches]]
    xs_sample.common.write_parquet(pa.Table.from_pylist(derived), root.huc8_file(HUC8, "derived"))
    return root, chunk


def valley_source(buf4326):
    """A V valley across the stream, 1 m grid in EPSG:5070 over the buffer."""
    import geopandas as gpd
    import rioxarray  # noqa: F401
    import xarray as xr
    from affine import Affine
    buf = gpd.GeoSeries([buf4326], crs=4326).to_crs(5070).iloc[0]
    minx, miny, maxx, maxy = buf.bounds
    minx, miny, maxx, maxy = minx - 20, miny - 20, maxx + 20, maxy + 20
    xs = np.arange(minx, maxx, 1.0) + 0.5
    ys = np.arange(maxy, miny, -1.0) - 0.5
    cx = buf.centroid.x
    z = 100.0 + np.abs(xs[None, :] - cx) * 0.08 + 0.0 * ys[:, None]
    da = xr.DataArray(z.astype("float32"), coords={"y": ys, "x": xs}, dims=("y", "x"), name="elevation")
    da = da.rio.write_crs(5070).rio.write_transform(Affine(1.0, 0, minx, 0, -1.0, maxy))
    return da.rio.write_nodata(np.nan, encoded=False), 1, {"source": "1m", "project": "TEST_2020",
                                                              "tiles": ["t1"], "last_modified": "2026-01-01"}


def _run_sample(root, chunk, monkeypatch, source=valley_source, **kw):
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    xs_sample.run_xs_sample(root, chunk, HUC8, states, progress, control, dem_source=source,
                            bandwidth=state.Bandwidth(root, budget_bytes=1e15), **kw)
    return states, progress, control


def test_sampling_archives_nine_native_transects_per_reach(tmp_path, monkeypatch):
    root, chunk = _root(tmp_path)
    states, _p, _c = _run_sample(root, chunk, monkeypatch)
    profiles = pq.read_table(root.huc8_file(HUC8, "xs_profiles")).to_pylist()
    summary = pq.read_table(root.huc8_file(HUC8, "xs_sample")).to_pylist()
    assert [r["status"] for r in summary] == ["ok"] * 3 and all(r["dem_res_m"] == 1 for r in summary)
    assert len(profiles) == 27 and sorted({r["k"] for r in profiles}) == list(range(9))
    first = profiles[0]
    assert first["wide_m"] == 250.0 and first["n_pts"] == 501 and len(first["z"]) == 501  # 8 x 6 m -> the 250 m floor
    assert first["dem_source"] == "1m:TEST_2020" and json.loads(first["tiles"]) == ["t1"]
    z = np.asarray(first["z"])
    assert np.nanmin(z) == pytest.approx(100.0, abs=0.1) and first["n_finite"] == 501
    stations = xs_derive.stations_for(first)
    assert stations[0] == -250.0 and stations[-1] == 250.0
    import geopandas as gpd
    reaches = gpd.read_parquet(root.huc8_file(HUC8, "reaches"))
    assert len(reaches) == 3 and abs(float(reaches["reach_length_ft"].iloc[0]) - 1000) < 2
    assert states.is_done(HUC8, "xs_sample") and not (root.huc8_dir(HUC8) / "xs_sample.progress.json").exists()


def test_sampling_resumes_after_a_pause_without_resampling(tmp_path, monkeypatch):
    root, chunk = _root(tmp_path, n_reaches=3)
    monkeypatch.setattr(xs_sample, "BATCH", 2)
    calls = []

    def pausing(buf):
        calls.append(1)
        if len(calls) == 3:
            raise state.PauseRequested("stop")
        return valley_source(buf)

    with pytest.raises(state.PauseRequested):
        _run_sample(root, chunk, monkeypatch, source=pausing)
    assert len(state.Ledger(root, f"xs_sample-{HUC8}")) == 1          # the first batch landed
    calls.clear()
    _run_sample(root, chunk, monkeypatch, source=lambda buf: (calls.append(1), valley_source(buf))[1])
    assert len(calls) == 1                                          # only the third reach
    assert pq.read_table(root.huc8_file(HUC8, "xs_profiles")).num_rows == 27


def test_derivation_matches_the_app_on_the_same_elevation(tmp_path, monkeypatch):
    from easi.datasources import threedep
    root, chunk = _root(tmp_path)
    _run_sample(root, chunk, monkeypatch)
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    xs_derive.run_xs_derive(root, chunk, HUC8, states, progress, control)
    rows = {r["comid"]: r for r in pq.read_table(root.huc8_file(HUC8, "xsections")).to_pylist()}
    assert [rows[c]["status"] for c in (1, 2, 3)] == ["ok"] * 3
    derived = json.loads(rows[1]["geomorph"])
    assert derived["n_transects"] == 9 and derived["reach"]["n"] == 9 and derived["dem_resolution_m"] == 1
    assert len(derived["candidates"]) == 9 and "profile" not in derived["candidates"][0] and "profile" not in derived
    assert rows[1]["profile_stations"] and rows[1]["profile_elevs"]
    # the app's own function on the same DEM
    import geopandas as gpd
    reaches = gpd.read_parquet(root.huc8_file(HUC8, "reaches"))
    fc = gpd.GeoSeries([reaches.geometry.iloc[0]], crs=4326).__geo_interface__
    monkeypatch.setattr(threedep, "_best_available_dem", lambda buf: valley_source(buf)[:2])
    live = threedep.reach_geomorphology(fc, 25.0, bankfull=(6.0, 0.5), bankfull_area_m2=2.4,
                                        division="Appalachian Highlands")
    assert live["n_transects"] == 9
    for key in ("entrenchment_ratio", "bank_height_ratio"):
        assert derived[key] == pytest.approx(live[key], abs=0.011)
        assert derived["reach"][key]["median"] == pytest.approx(live["reach"][key]["median"], abs=0.011)
    assert derived["selected"] == live["selected"]
    assert xs_derive.xs_method_version() == xs_derive.xs_method_version()


def test_published_dict_draws_one_section_and_scores_the_four_metrics(tmp_path, monkeypatch):
    from easi import assessment
    from easi.national import client, records
    root, chunk = _root(tmp_path)
    _run_sample(root, chunk, monkeypatch)
    states, progress, control = state.UnitStates(root), state.Progress(root, quiet=True), state.Control(root)
    xs_derive.run_xs_derive(root, chunk, HUC8, states, progress, control)
    row = pq.read_table(root.huc8_file(HUC8, "xsections")).to_pylist()[0]
    published = xs_derive.to_published(row["geomorph"], row["profile_stations"], row["profile_elevs"])
    assert len(published["candidates"]) == 1 and published["selected"] == 0
    assert published["selected_transect"] == row["selected"] and len(published["candidate_scalars"]) == 9
    assert published["candidates"][0]["profile"]["stations"][0] < 0
    assert xs_derive.to_published("{}", [], []) == {} and xs_derive.to_published(None, None, None) is None
    block = assessment._build_cross_section(published, 0.01, 46006)
    assert block and len(block["candidates"]) == 1 and block["geom"]["dem_source"] == "USGS 3DEP 1 m DEM"
    record = {**{k: None for k in records.IDENTITY_FIELDS}, "comid": 1, "lat": LAT, "lon": LON,
              "totdasqkm": 25.0, "slope": 0.01, "fcode": 46006, "streamorde": 2, "lengthkm": 0.2,
              "huc4": "0208", "huc8": HUC8, "streamcat": {}, "nrsa": None, "bankfull": BANKFULL,
              "attains_exact": {}, "attains_nearby": {}, "wqp_tn": None, "wqp_tp": None, "nid_dams": None,
              "nas_taxa": None, "nas_scope": None, "geomorph": published, "schema_version": 1}
    report = client.score_record(record, cross_section=True)
    rated = {r["metricId"]: r for r in report["metricRows"]}
    for metric in ("floodplain-connectivity-floodplain-access-entrenchment",
                   "high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence",
                   "channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition",
                   "channel-evolution-channel-evolution-stage-and-trends"):
        assert rated[metric]["rating"] in ("Good", "Fair", "Poor"), metric
        assert "median of 9" in (rated[metric].get("source") or "")
    assert report.get("crossSection") and len(report["crossSection"]["candidates"]) == 1


def test_parts_from_another_sampling_version_are_discarded_on_resume(tmp_path, monkeypatch):
    root, chunk = _root(tmp_path, n_reaches=3)
    monkeypatch.setattr(xs_sample, "BATCH", 2)
    calls = []

    def pausing(buf):
        calls.append(1)
        if len(calls) == 3:
            raise state.PauseRequested("stop")
        return valley_source(buf)

    with pytest.raises(state.PauseRequested):
        _run_sample(root, chunk, monkeypatch, source=pausing)
    parts = root.huc8_parts(HUC8, "xs_sample")
    assert (parts / "_inputs.json").exists() and len(state.Ledger(root, f"xs_sample-{HUC8}")) == 1
    (parts / "_inputs.json").write_text("{\"inputs\": \"older\"}", encoding="utf-8")   # an earlier version's parts
    calls.clear()
    _run_sample(root, chunk, monkeypatch, source=lambda buf: (calls.append(1), valley_source(buf))[1])
    assert len(calls) == 3                                          # every reach sampled again
    assert pq.read_table(root.huc8_file(HUC8, "xs_profiles")).num_rows == 27
    assert not parts.exists()

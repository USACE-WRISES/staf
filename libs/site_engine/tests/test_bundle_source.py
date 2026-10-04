"""The STAF data bundle as the engine's HR source (0.5.0), offline.

The bundle is the hr-data app's two-region test dataset (``apps/hr-data/tests/fixture2.py``, built
with the engine's synced reader): a Y in region 9901 whose top reach drains a reach of region 9902
across the boundary, and an isolated ditch; every catchment is a 1 km square on the 10 m grid.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from site_engine import _hrslim, bundle, compute_site, hr
from site_engine._hrslim import arcs, fmt, fmt2, grid

_FIXTURE = Path(__file__).resolve().parents[3] / "apps" / "hr-data" / "tests" / "fixture2.py"
pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="hr-data fixture not present")


def _fixture_module():
    # the fixture imports ``hrslim``; the engine's byte-identical copy stands in
    aliases = {"hrslim": _hrslim, "hrslim.arcs": arcs, "hrslim.fmt": fmt, "hrslim.fmt2": fmt2,
               "hrslim.grid": grid}
    saved = {k: sys.modules.get(k) for k in aliases}
    sys.modules.update(aliases)
    try:
        spec = importlib.util.spec_from_file_location("bundle_fixture2", _FIXTURE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(scope="module")
def fx():
    return _fixture_module()


@pytest.fixture()
def root(tmp_path, fx):
    folder = fx.build(tmp_path / "bundle")
    (folder / bundle.ABSENT_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": []}),
                                             encoding="utf-8")
    bundle.set_source("bundle", folder)
    hr.clear_caches()
    yield folder
    bundle.set_source(None)
    hr.clear_caches()


def test_the_service_is_the_default(monkeypatch):
    monkeypatch.delenv(bundle.ENV_SOURCE, raising=False)
    bundle.set_source(None)
    assert bundle.source() == "service" and not bundle.enabled()
    assert bundle.describe() == {"source": "service"}


def test_records_have_the_service_shape(root, fx):
    rec = hr.flowline_by_id(fx.ID[2])
    assert rec["nhdplusid"] == fx.ID[2] and rec["gnis_name"] == "Upper Run"
    assert (rec["hydroseq"], rec["dnhydroseq"], rec["uphydroseq"]) == (1002, 1001, 1004)
    assert rec["fcode"] == 46006 and rec["ftype"] == 460 and rec["vpuid"] == "9901"
    assert rec["geometry"]["type"] == "LineString" and rec["qama"] == 1.5
    assert hr.flowline_by_id(fx.ID[3])["qama"] is None            # NaN reads as missing, as parse_feature does
    assert hr.feature_by_hydroseq(2005)["nhdplusid"] == fx.ID[5]  # the other region


def test_one_walk_level_crosses_regions(root, fx):
    up_of_outlet = hr.parents_by_node([hr.flowline_by_id(fx.ID[1])])
    assert sorted(r["nhdplusid"] for r in up_of_outlet) == [fx.ID[2], fx.ID[3]]
    up_of_top = hr.parents_by_node([hr.flowline_by_id(fx.ID[4])])
    assert [r["nhdplusid"] for r in up_of_top] == [fx.ID[5]]     # region 9902, through the link table
    assert all(r.get("geometry") for r in up_of_outlet + up_of_top)


def test_site_watershed_and_reach_from_the_bundle(root, fx):
    lon, lat = fx.point_deg(50, 25)                              # on the outlet reach
    rec = compute_site(lat, lon, {"metricFamilies": []})
    assert rec["status"] == "ok" and rec["site"]["nhdplusId"] == fx.ID[1]
    ws = rec["watershed"]
    assert ws["nReaches"] == 5 and ws["areaSqkm"] == pytest.approx(5.0, abs=1e-6)
    assert ws["areaAgreement"] == pytest.approx(1.0, abs=1e-4) and not ws["warnings"]
    assert rec["reach"]["geometry"] is not None
    assert rec["hrSource"]["source"] == "bundle" and rec["hrSource"]["answeredBy"] == ["bundle"]
    assert compute_site(lat, lon, {"metricFamilies": []}) == rec   # deterministic


def test_the_budget_refuses_as_the_service_walk_does(root, fx):
    lon, lat = fx.point_deg(50, 25)
    rec = compute_site(lat, lon, {"metricFamilies": [], "maxReaches": 2})
    assert rec["status"] == "refused" and "exceeds the engine budget" in rec["reason"]


def test_a_box_near_a_missing_region_goes_to_the_service(root, fx, monkeypatch):
    west, south, east, north = fx.box_deg(-500, -500, 1000, 1000)
    (root / bundle.ABSENT_FILE).write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"vpu": "9999"},
         "geometry": {"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north],
                                                          [west, north], [west, south]]]}}]}),
        encoding="utf-8")
    bundle.set_source("bundle", root)
    asked = []

    def fake_attempt(url, params, *args):                      # the service, answering nothing there
        asked.append(params)
        return "answer", {"type": "FeatureCollection", "features": []}
    monkeypatch.setattr(hr, "_attempt", fake_attempt)
    lon, lat = fx.point_deg(50, 25)
    with hr.sources_used() as used:
        assert hr.flowlines_in_bbox(lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01) == []
    assert asked and used == {"service"}
    assert hr.flowline_by_id(fx.ID[1])["nhdplusid"] == fx.ID[1]  # ids still come from the bundle


CELLS = 10_000                       # every fixture catchment is 1 km2 of 10 m cells
#: reach -> the NLCD class filling its catchment (the walk from reach 1 holds reaches 1 to 5)
COVER = {1: 82, 2: 41, 3: 81, 4: 71, 5: 90, 6: 11}


def _write_values(folder: Path, fx) -> None:
    """Per-catchment tables for the fixture (the builder's exact encoding): one class per
    catchment, 20 percent impervious on reach 4, 1 km of road each, a dam on reach 3, K 0.3, a
    forested riparian piece of 100 cells each, sinuosity 1.234 on the outlet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from site_engine._hrslim.values import LC_COLUMNS
    folder.mkdir(parents=True, exist_ok=True)
    for vpu, reaches in fx.REGIONS.items():
        n = len(reaches)
        lc = dict((c, pa.array([CELLS if c == f"lc{COVER[r]}" else 0 for r in reaches], type=pa.int32()))
                  for c in LC_COLUMNS)
        lc.update({"imp21_sum": pa.array([20 * CELLS if r == 4 else 0 for r in reaches], type=pa.int64()),
                   "imp21_n": pa.array([CELLS] * n), "imp01_sum": pa.array([0] * n, type=pa.int64()),
                   "imp01_n": pa.array([CELLS] * n)})
        pq.write_table(pa.table(lc), folder / f"lc2_{vpu}.parquet")
        rip = dict((c, pa.array([100 if c == "lc41" else 0] * n, type=pa.int32())) for c in LC_COLUMNS)
        rip.update({"row": pa.array(list(range(n)), type=pa.int32()), "k": pa.array([0] * n, type=pa.int32()),
                    "imp21_sum": pa.array([0] * n, type=pa.int64()), "imp21_n": pa.array([100] * n),
                    "imp01_sum": pa.array([0] * n, type=pa.int64()), "imp01_n": pa.array([100] * n)})
        pq.write_table(pa.table(rip), folder / f"rip2_{vpu}.parquet")
        pq.write_table(pa.table({"road_m": pa.array([1000.0] * n)}), folder / f"roads2_{vpu}.parquet")
        pq.write_table(pa.table({"dams": pa.array([1 if r == 3 else 0 for r in reaches], type=pa.int16()),
                                 "normal_acft": pa.array([50.0 if r == 3 else 0.0 for r in reaches]),
                                 "normal_missing": pa.array([0] * n, type=pa.int16()),
                                 "nid_acft": pa.array([80.0 if r == 3 else 0.0 for r in reaches])}),
                       folder / f"dams2_{vpu}.parquet")
        pq.write_table(pa.table({"k_sum": pa.array([0.3 * CELLS] * n), "k_cells": pa.array([CELLS] * n),
                                 "ssurgo_cells": pa.array([CELLS] * n), "statsgo_cells": pa.array([0] * n)}),
                       folder / f"soils2_{vpu}.parquet")
        pq.write_table(pa.table(dict((c, pa.array([], type=pa.int32())) for c in ("line_row", "road", "x_dm", "y_dm"))),
                       folder / f"xings2_{vpu}.parquet")
        pq.write_table(pa.table({"sinuosity": pa.array([1.234 if r == 1 else 1.0 for r in reaches],
                                                       type=pa.float32())}),
                       folder / f"extras2_{vpu}.parquet")


def test_watershed_families_from_the_precomputed_tables(root, fx, monkeypatch):
    _write_values(root / "values", fx)
    bundle.set_source("bundle", root)
    import requests

    def no_network(*args, **kwargs):
        raise AssertionError("a precomputed family asked a live service")
    monkeypatch.setattr(requests, "post", no_network)
    monkeypatch.setattr(requests, "get", no_network)
    lon, lat = fx.point_deg(50, 25)
    rec = compute_site(lat, lon, {"metricFamilies": ["dams", "landcover", "roads", "soils"],
                                  "landcoverBaseline": True})
    m = dict((k, v["value"]) for k, v in rec["metrics"].items())
    for stem in ("crop", "forest", "hayPasture", "grassland", "woodyWetland"):
        assert m[f"{stem}PctWatershed"] == 20.0
    assert m["shrubPctWatershed"] == 0.0 and m["imperviousPctWatershed"] == 4.0
    assert m["imperviousPct2001Watershed"] == 0.0 and m["forestPctRiparian"] == 100.0
    assert m["roadLengthKm"] == 5.0 and m["roadDensity"] == 1.0 and m["roadCrossings"] == 0
    assert m["damCount"] == 1 and m["damStorageAcreFt"] == 50.0 and m["damNidStoragePerSqkm"] == 16.0
    assert m["soilKFactor"] == 0.3
    assert rec["metrics"]["forestPctWatershed"]["source"].startswith("NLCD, counted per HR catchment")
    assert rec["metrics"]["forestPctRiparian"]["spatialSupport"] == "riparianBuffer"
    assert rec["site"]["sinuosity"] == 1.234                      # the original line's, not the 2 m line's


def test_a_tree_without_tables_for_a_region_runs_live(root, fx):
    from site_engine.metrics import precomputed
    _write_values(root / "values", fx)
    (root / "values" / "lc2_9902.parquet").unlink()              # reach 5's region has no land cover table
    bundle.set_source("bundle", root)
    record = {"site": {"nhdplusId": fx.ID[1]}, "input": {"config": {"maxReaches": 5000, "maxHops": 200}}}
    assert precomputed.values_for(record) is None
    record["site"]["nhdplusId"] = fx.ID[2]                        # reach 2's tree reaches region 9902 too
    assert precomputed.values_for(record) is None
    record["site"]["nhdplusId"] = fx.ID[6]                        # the isolated ditch stays in 9901
    assert precomputed.values_for(record)["areaSqkm"] == pytest.approx(1.0)


def test_a_broken_bundle_reads_as_absent(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    bundle.set_source("bundle", tmp_path)
    try:
        assert not bundle.enabled() and bundle.describe()["source"] == "service"
        assert "unreadable" in bundle.describe()["bundleUnavailable"]
    finally:
        bundle.set_source(None)


@pytest.fixture()
def v2root(root, fx):
    """The bundle with a V2 part (the fixture network again, its ids standing in for COMIDs), the
    per-flowline extras and the ATTAINS unit table (``fixture2.write_lookups``)."""
    fx.build_v2(root)
    fx.write_lookups(root)
    bundle.set_source("bundle", root)
    return root


@pytest.fixture()
def partial_v2root(root, fx):
    """The bundle with a V2 part that lacks the HR-only reaches 2, 4 and 5 (their land in the
    outlet's V2 catchment)."""
    fx.build_v2(root, partial=True)
    fx.write_lookups(root)
    bundle.set_source("bundle", root)
    return root


def test_v2_reads_from_the_bundle(v2root, fx):
    feat = bundle.v2_feature(fx.ID[1])
    assert feat["properties"]["comid"] == fx.ID[1] and feat["properties"]["streamorde"] == 1
    assert feat["properties"]["totdasqkm"] == 5.0 and feat["geometry"]["type"] == "LineString"
    up = [r["nhdplusid"] for r in bundle.v2_mainstem(fx.ID[1], 10.0)]
    assert up == [fx.ID[1], fx.ID[2], fx.ID[4], fx.ID[5]]        # uphydroseq, across the region border
    assert [r["nhdplusid"] for r in bundle.v2_mainstem(fx.ID[1], 0.9)] == [fx.ID[1], fx.ID[2]]
    down = [r["nhdplusid"] for r in bundle.v2_mainstem(fx.ID[4], 10.0, upstream=False)]
    assert down == [fx.ID[4], fx.ID[2], fx.ID[1]]
    ws = bundle.v2_watershed(fx.ID[1])
    assert ws["areaSqkm"] == pytest.approx(5.0, abs=1e-6) and ws["nReaches"] == 5
    assert len(bundle.v2_lines_in_box(*fx.box_deg(-10, -10, 510, 510))) == 6
    assert bundle.v2_feature(12345) is None


def test_the_raindrop_is_the_v2_catchment_at_the_point(partial_v2root, fx):
    def drop(dx, dy):
        lon, lat = fx.point_deg(dx, dy)
        return bundle.raindrop(lat, lon)
    got = drop(50, 25)                                            # on the outlet
    lon, lat = fx.point_deg(50, 25)
    assert got["comid"] == fx.ID[1] and got["method"] == bundle.RAINDROP_METHOD
    assert (got["snap_lon"], got["snap_lat"]) == pytest.approx((lon, lat), abs=1e-6)
    assert drop(125, 50)["comid"] == fx.ID[3]                     # on the tributary
    assert drop(410, 430)["comid"] == fx.ID[6]                    # on the ditch
    jlon, jlat = fx.point_deg(50, 50)                             # the outlet's top
    got = drop(50, 125)                                           # an HR-only reach: the outlet's catchment
    assert got["comid"] == fx.ID[1]
    assert (got["snap_lon"], got["snap_lat"]) == pytest.approx((jlon, jlat), abs=1e-5)   # a 1e-5 degree vertex
    assert drop(95, 295)["comid"] == fx.ID[1]                     # far from a line, in the same catchment
    assert drop(50, 325)["comid"] == fx.ID[1]                     # an HR-only reach in the other HR region
    assert drop(300, 300) is None                                 # in no V2 catchment: NLDI answers


def test_the_anchor_routes_from_the_bundle_without_a_request(partial_v2root, fx, monkeypatch):
    import requests

    from site_engine import anchor

    def no_network(*args, **kwargs):
        raise AssertionError("the anchor asked a live service")
    monkeypatch.setattr(requests, "post", no_network)
    monkeypatch.setattr(requests, "get", no_network)
    lon, lat = fx.point_deg(50, 125)                              # a typed point on an HR-only reach
    out = anchor.classify(lat, lon)["anchor"]
    assert out["anchorKind"] == "hrSurrogate" and out["clickedStream"]["nhdplusId"] == fx.ID[2]
    assert out["scoredReach"]["comid"] == fx.ID[1] and out["scoredReach"]["drainageAreaSqkm"] == 5.0
    assert out["routing"]["method"] == bundle.RAINDROP_METHOD and out["routing"]["daRatio"] == 1.67
    lon, lat = fx.point_deg(50, 25)                               # on the covered outlet: the V2 reach itself
    out = anchor.classify(lat, lon)["anchor"]
    assert out["anchorKind"] == "v2Direct" and out["scoredReach"]["comid"] == fx.ID[1]
    attrs = anchor.v2_flowline_attrs(fx.ID[1])
    assert attrs == {"gnis_name": "Outlet Run", "drainage_area_sqkm": 5.0, "huc8": "02060005",
                     "slope": 0.001, "fcode": 46006, "stream_order": 1}


def test_point_lookups_from_the_flowline_at_the_point(v2root, fx):
    lon, lat = fx.point_deg(50, 25)                               # on the outlet
    got = bundle.point_extras(lat, lon)
    assert got["nhdplusid"] == fx.ID[1] and got["huc12"] == "020600050101" and got["sinuosity"] == 1.234
    assert got["attains_exact"]["assessment_unit"] == "IA 02-TEST-0001"
    assert got["attains_exact"]["ircategory"] == "5" and got["attains_exact"]["match_type"] == "intersect"
    lon, lat = fx.point_deg(125, 50)                              # on the tributary: a unit 120 m off
    got = bundle.point_extras(lat, lon)
    assert got["attains_exact"] == {} and got["huc12"] == "020600050102"
    assert got["attains_nearby"]["assessment_unit"] == "IA 02-TEST-0002"
    assert got["attains_nearby"]["distance_m"] == 120.0
    lon, lat = fx.point_deg(300, 300)                             # no flowline within 150 ft
    assert bundle.point_extras(lat, lon) is None


def test_nwi_along_the_assessment_reach(v2root, fx):
    lon, lat = fx.point_deg(50, 25)
    rec = compute_site(lat, lon, {"metricFamilies": []})
    got = bundle.reach_wetlands(fx.ID[1], rec["reach"]["geometry"])
    assert [f["nhdplusid"] for f in got["flowlines"]] == [fx.ID[1]]  # the reach runs along half of it
    share = got["flowlines"][0]["share"]
    assert share == pytest.approx(0.5, rel=0.02)
    assert got["wetlandM2"]["palustrine"] == pytest.approx(share * 30000.0, rel=1e-3)
    assert got["stripM2"] == pytest.approx(share * fx.STRIP_M2, rel=1e-3)
    assert got["wetlandM2Total"] == got["wetlandM2"]["palustrine"]

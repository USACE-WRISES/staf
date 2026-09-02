"""Any NHD stream: the HR display adapter, click classification, anchoring
helpers, and the delineation shape built from a STAF site engine record.
Offline: every client is monkeypatched."""
from __future__ import annotations

from deep import hr_site, pipeline
from deep.datasources import flowlines

HR_ONLY = {"anchorKind": "hrSurrogate",
           "clickedStream": {"network": "nhdplus-hr", "nhdplusId": 750012345,
                             "gnisName": "Sugar Run", "drainageAreaSqkm": 4.2,
                             "snapLat": 40.31125, "snapLon": -83.05615, "snapDistFt": 12.0},
           "scoredReach": {"network": "nhdplus-v2", "comid": 5214461, "gnisName": "Sugar Run",
                           "drainageAreaSqkm": 7.6, "snapLat": 40.3101, "snapLon": -83.0563},
           "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8, "declined": False}}
DECLINED = {**HR_ONLY, "routing": {"routedDistanceFt": 9000.0, "daRatio": 14.0,
                                    "declined": True, "declineCode": "da-ratio"}}

RECORD = {"status": "ok", "engineVersion": "0.2.0",
          "site": {"nhdplusId": 750012345, "gnisName": "Sugar Run", "reachcode": "05060001001234",
                   "snapLat": 40.31125, "snapLon": -83.05615, "slope": 0.004,
                   "streamOrder": 1, "fcode": 46006, "sinuosity": 1.1,
                   "drainageAreaSqkm": 4.2},
          "watershed": {"areaSqkm": 4.19, "areaAgreement": 0.998, "nReaches": 3,
                        "polygon": {"type": "FeatureCollection", "features": []},
                        "warnings": ["tree flowline geometries unavailable"]},
          "reach": {"lengthFt": 500.0, "geometry": {"type": "LineString",
                                                   "coordinates": [[0, 0], [1, 1]]},
                    "warnings": []},
          "metrics": {"imperviousPctWatershed": {"value": 1.2}}}


def test_hr_records_to_geojson_keeps_only_geometries():
    fc = hr_site.hr_records_to_geojson([{"nhdplusid": 1, "geometry": {"type": "LineString",
                                                                       "coordinates": [[0, 0], [1, 1]]}},
                                        {"nhdplusid": 2, "geometry": None}])
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
    assert fc["features"][0]["properties"] == {"nhdplusid": 1}
    assert hr_site.hr_records_to_geojson([]) is None


def test_hr_layer_is_absent_without_the_engine(monkeypatch):
    monkeypatch.setattr(hr_site, "hr_available", lambda: False)
    assert hr_site.hr_flowlines_fc(-83.1, 40.3, -83.0, 40.4) is None
    assert hr_site.snap_hr(40.3, -83.0) is None


def test_snap_both_prefers_the_covered_network(monkeypatch):
    monkeypatch.setattr(flowlines, "flowlines_in_bbox", lambda *a, **k: {"features": []})
    monkeypatch.setattr(flowlines, "nearest_point_on_lines",
                        lambda fc, lat, lon: (lat, lon, 40.0, 5214461))
    monkeypatch.setattr(hr_site, "snap_hr", lambda lat, lon: (lat, lon, 3.0, 750012345))
    res = hr_site.snap_both(40.31, -83.05)
    assert res["hit"][3] == 5214461 and "hrHit" not in res


def test_snap_both_falls_to_the_hr_network(monkeypatch):
    monkeypatch.setattr(flowlines, "flowlines_in_bbox", lambda *a, **k: {"features": []})
    monkeypatch.setattr(flowlines, "nearest_point_on_lines",
                        lambda fc, lat, lon: (lat, lon, 900.0, 5214461))
    monkeypatch.setattr(hr_site, "snap_hr", lambda lat, lon: (lat, lon, 3.0, 750012345))
    res = hr_site.snap_both(40.31, -83.05)
    assert res["hit"][2] == 900.0 and res["hrHit"][3] == 750012345
    assert res["lat"] == 40.31 and res["lon"] == -83.05


def test_anchor_helpers():
    assert hr_site.declined(HR_ONLY) is False and hr_site.declined(DECLINED) is True
    assert hr_site.declined(None) is False
    assert hr_site.clicked_reach(HR_ONLY)["nhdplusId"] == 750012345
    assert hr_site.clicked_reach({"anchorKind": "v2Direct"}) == {}
    assert hr_site.anchor_label(HR_ONLY).startswith("nearest covered reach, COMID 5214461")
    assert hr_site.anchor_label({"anchorKind": "v2Direct"}) == ""


def test_v2_anchor_shape():
    a = hr_site.v2_anchor(5214461, 40.31, -83.05, 40.3101, -83.0563, 12.0)
    assert a["anchorKind"] == "v2Direct"
    assert a["scoredReach"]["comid"] == 5214461 and a["scoredReach"]["network"] == "nhdplus-v2"
    assert a["scoredReach"]["snapLat"] == 40.3101


def test_route_from_hr_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("service down")
    monkeypatch.setattr(hr_site, "_engine", boom)
    res = hr_site.route_from_hr(40.31, -83.05, (40.31, -83.05, 3.0, 750012345))
    assert res["error"] == "snap_service_error" and "service down" in res["detail"]


def test_delineate_from_engine_shape():
    out = pipeline.delineate_from_engine(RECORD, HR_ONLY, 40.31, -83.05, 500.0)
    assert out["status"] == "ok" and out["watershedBasis"] == "site-engine"
    d = out["delineation"]
    assert d["network"] == "nhdplus-hr" and d["nhdplus_id"] == 750012345
    assert d["comid"] == 5214461                       # keys the labeled evidence
    assert d["huc8"] == "05060001" and d["drainage_area_sqkm"] == 4.19
    assert d["watershed_area_sqkm"] == 4.19 and d["reach_length_ft"] == 500.0
    assert d["gnis_name"] == "Sugar Run" and d["stream_order"] == 1
    assert d["warnings"] == ["tree flowline geometries unavailable"]
    assert out["watershed_geojson"] == RECORD["watershed"]["polygon"]
    assert out["reach_geojson"] == RECORD["reach"]["geometry"]
    assert out["siteEngine"]["watershed"]["polygon"] is None     # stripped for the session
    assert out["siteEngine"]["reach"]["geometry"] is None
    ci = out["ctx_inputs"]
    assert ci["comid"] == 5214461 and ci["siteAnchor"] is HR_ONLY
    assert ci["watershedBasis"] == "site-engine" and ci["lat"] == 40.31125


def test_delineate_from_engine_withholds_the_comid_when_declined():
    out = pipeline.delineate_from_engine(RECORD, DECLINED, 40.31, -83.05)
    assert out["delineation"]["comid"] is None
    assert out["ctx_inputs"]["comid"] is None
    assert out["input"]["reach_length_ft"] == pipeline.DEFAULT_REACH_FT


def test_basis_vocabulary():
    assert pipeline.BASIS_V2_BASIN == "nhdplus-v2-basin"
    assert pipeline.BASIS_SITE_ENGINE == "site-engine"
    assert pipeline.BASIS_SURROGATE_BASIN == "nhdplus-v2-basin-of-surrogate"

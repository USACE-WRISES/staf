"""Every NHD stream, one engine (2026-09-05): the HR display adapter, the
click snap, and the delineation shape built from a STAF site engine record.
Offline: every client is monkeypatched."""
from __future__ import annotations

from sfari import hr_site, pipeline

RECORD = {"status": "ok", "engineVersion": "0.3.0",
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


class _Anchor:
    @staticmethod
    def hr_snap(lat, lon, *, half_deg):
        return (40.31125, -83.05615, 12.0, 750012345)


class _HR:
    @staticmethod
    def flowlines_in_bbox(w, s, e, n):
        return [{"nhdplusid": 1, "geometry": {"type": "LineString",
                                              "coordinates": [[w, s], [e, n]]}}]


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
    assert hr_site.snap_point(40.3, -83.0) == {"hit": None, "lat": 40.3, "lon": -83.0}


def test_every_click_snaps_to_the_hr_network(monkeypatch):
    monkeypatch.setattr(hr_site, "hr_available", lambda: True)
    monkeypatch.setattr(hr_site, "_engine", lambda: (_Anchor, _HR))
    assert hr_site.snap_hr(40.31, -83.05) == (40.31125, -83.05615, 12.0, 750012345)
    res = hr_site.snap_point(40.31, -83.05)
    assert res["hit"][3] == 750012345 and res["lat"] == 40.31
    fc = hr_site.hr_flowlines_fc(-83.1, 40.3, -83.0, 40.4)
    assert fc["features"][0]["properties"] == {"nhdplusid": 1}
    for gone in ("snap_both", "route_from_hr", "v2_anchor", "anchor_label", "declined",
                 "clicked_reach"):
        assert not hasattr(hr_site, gone), gone


def test_snap_never_raises(monkeypatch):
    monkeypatch.setattr(hr_site, "hr_available", lambda: True)

    def boom():
        raise RuntimeError("service down")
    monkeypatch.setattr(hr_site, "_engine", boom)
    assert hr_site.snap_hr(40.31, -83.05) is None
    assert hr_site.hr_flowlines_fc(-83.1, 40.3, -83.0, 40.4) is None


def test_delineate_from_engine_shape():
    out = pipeline.delineate_from_engine(RECORD, 40.31, -83.05, 500.0)
    assert out["status"] == "ok" and out["watershedBasis"] == "site-engine"
    assert "siteAnchor" not in out
    d = out["delineation"]
    assert d["network"] == "nhdplus-hr" and d["nhdplus_id"] == 750012345
    assert d["comid"] is None                          # the engine reported none
    assert d["huc8"] == "05060001" and d["drainage_area_sqkm"] == 4.2
    assert d["watershed_area_sqkm"] == 4.19 and d["reach_length_ft"] == 500.0
    assert d["gnis_name"] == "Sugar Run" and d["stream_order"] == 1
    assert d["warnings"] == ["tree flowline geometries unavailable"]
    assert out["watershed_geojson"] == RECORD["watershed"]["polygon"]
    assert out["reach_geojson"] == RECORD["reach"]["geometry"]
    assert out["siteEngine"]["watershed"]["polygon"] is None     # stripped for the session
    assert out["siteEngine"]["reach"]["geometry"] is None
    ci = out["ctx_inputs"]
    assert ci["comid"] is None and "siteAnchor" not in ci
    assert ci["watershedBasis"] == "site-engine" and ci["lat"] == 40.31125
    assert out["input"]["reach_length_ft"] == 500.0
    assert pipeline.delineate_from_engine(RECORD, 40.31, -83.05)["input"]["reach_length_ft"] \
        == pipeline.DEFAULT_REACH_FT


def test_basis_vocabulary_and_the_no_watershed_continuation():
    # the engine's watershed is the normal basis; the two V2 values name the
    # StreamCat basin the values describe after the engine failed (2026-09-07)
    assert pipeline.BASIS_SITE_ENGINE == "site-engine"
    assert pipeline.BASIS_V2_BASIN == "nhdplus-v2-basin"
    assert pipeline.BASIS_SURROGATE_BASIN == "nhdplus-v2-basin-of-surrogate"
    assert not hasattr(pipeline, "delineate_only")
    routed = {"anchorKind": "hrSurrogate",
              "clickedStream": {"nhdplusId": 750012345, "gnisName": "Sugar Run",
                                "reachcode": "05060001001234", "drainageAreaSqkm": 4.2,
                                "slope": 0.004, "fcode": 46006, "streamOrder": 1,
                                "snapLat": 40.31125, "snapLon": -83.05615},
              "scoredReach": {"comid": 5214461, "gnisName": "Big Run", "drainageAreaSqkm": 40.0,
                              "snapLat": 40.30, "snapLon": -83.04},
              "routing": {"routedDistanceFt": 1240.0, "daRatio": 9.5, "declined": False}}
    failed = {"status": "refused", "reason": "over budget"}
    out = pipeline.delineate_without_watershed(routed, 40.31, -83.05,
                                               (40.31125, -83.05615, 12.0, 750012345), 500.0, failed)
    assert out["status"] == "ok" and out["watershedBasis"] == "nhdplus-v2-basin-of-surrogate"
    assert out["watershed_geojson"] is None and out["reach_geojson"] is None
    assert out["siteAnchor"] is routed and out["siteEngine"] == failed
    d = out["delineation"]
    assert d["comid"] == 5214461 and d["nhdplus_id"] == 750012345 and d["network"] == "nhdplus-hr"
    assert d["gnis_name"] == "Sugar Run" and d["drainage_area_sqkm"] == 4.2   # the clicked stream's
    assert d["huc8"] == "05060001" and d["watershed_area_sqkm"] is None
    assert d["reach_length_ft"] == 500.0 and "StreamCat reach" in d["warnings"][0]
    ci = out["ctx_inputs"]
    assert ci["comid"] == 5214461 and ci["siteAnchor"] is routed
    assert ci["watershedBasis"] == "nhdplus-v2-basin-of-surrogate" and ci["lat"] == 40.31125
    # a covered stream: the anchor is the shape comid_anchor.resolve produces
    # after it fills the engine's bare v2Direct payload from the fabric API
    # (2026-09-07); the continuation keeps every site attribute the pull needs
    covered = {"anchorKind": "v2Direct", "anchorSchemaVersion": 1,
               "clickedPoint": {"lat": 43.68, "lon": -72.23},
               "scoredReach": {"network": "nhdplus-v2", "comid": 7, "gnisName": "Mink Brook",
                               "drainageAreaSqkm": 32.6, "reachcode": "01080106000123",
                               "slope": 0.0123, "fcode": 46006, "streamOrder": 2,
                               "snapLat": 43.68582, "snapLon": -72.23667, "snapDistFt": 12.0},
               "notes": []}
    hr_hit = (43.68582, -72.23667, 12.0, 24000800011817)
    v2 = pipeline.delineate_without_watershed(covered, 43.68, -72.23, hr_hit, 1000.0, failed)
    assert v2["watershedBasis"] == "nhdplus-v2-basin" and v2["delineation"]["comid"] == 7
    d2 = v2["delineation"]
    assert d2["gnis_name"] == "Mink Brook" and d2["drainage_area_sqkm"] == 32.6
    assert d2["slope"] == 0.0123 and d2["fcode"] == 46006 and d2["stream_order"] == 2
    assert d2["huc8"] == "01080106"
    assert d2["nhdplus_id"] == 24000800011817 and d2["network"] == "nhdplus-hr"
    assert d2["snapped_lat"] == 43.68582
    ci2 = v2["ctx_inputs"]
    assert ci2["drainage_area_sqkm"] == 32.6 and ci2["slope"] == 0.0123
    assert ci2["fcode"] == 46006 and ci2["stream_order"] == 2 and ci2["huc8"] == "01080106"
    # the engine's bare payload (nothing filled, no HR hit) still continues, blank
    bare = {"anchorKind": "v2Direct", "scoredReach": {"network": "nhdplus-v2", "comid": 7,
                                                      "gnisName": None, "drainageAreaSqkm": None}}
    v3 = pipeline.delineate_without_watershed(bare, 43.68, -72.23, None, 1000.0, failed)
    assert v3["delineation"]["network"] == "nhdplus-v2" and v3["delineation"]["snapped_lat"] == 43.68
    assert v3["delineation"]["gnis_name"] == "(unnamed stream)"
    for key in ("drainage_area_sqkm", "slope", "fcode", "stream_order", "huc8", "nhdplus_id"):
        assert v3["delineation"][key] is None, key

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
    assert d["huc8"] == "05060001" and d["drainage_area_sqkm"] == 4.19
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


def test_basis_vocabulary_is_one_value():
    assert pipeline.BASIS_SITE_ENGINE == "site-engine"
    assert not hasattr(pipeline, "BASIS_V2_BASIN")
    assert not hasattr(pipeline, "BASIS_SURROGATE_BASIN")
    assert not hasattr(pipeline, "delineate_only")

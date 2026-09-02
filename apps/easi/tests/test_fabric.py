"""The USGS fabric API client: request shapes, retry, attribute mapping, and
the two readers that moved off the WaterData WFS. Fully offline."""
from __future__ import annotations

from easi import delineation
from easi.datasources import fabric, flowlines


def _feat(comid=5214461, **props):
    base = {"comid": comid, "gnis_name": "Sugar Run", "reachcode": "05060001000869",
            "totdasqkm": 15.4935, "slope": 0.01784203, "fcode": 46006,
            "streamorde": 1.0, "lengthkm": 0.709}
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "MultiLineString",
                         "coordinates": [[[-83.060, 40.310], [-83.056, 40.311],
                                          [-83.050, 40.3115]]]}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats),
            "numberReturned": len(feats)}


def test_get_retries_with_backoff_then_gives_up(monkeypatch):
    calls: list = []
    sleeps: list = []

    class _Resp:
        status_code = 200

        def json(self):
            return _fc(_feat())

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        if len(calls) < 3:
            raise ConnectionError("down")
        return _Resp()
    monkeypatch.setattr(fabric.requests, "get", fake_get)
    monkeypatch.setattr(fabric.time, "sleep", sleeps.append)
    data = fabric._get({"comid": 1}, timeout=5.0, retries=2)
    assert data["type"] == "FeatureCollection" and len(calls) == 3
    assert sleeps == [1.0, 3.0]
    assert calls[0]["f"] == "json"
    calls.clear(); sleeps.clear()
    monkeypatch.setattr(fabric.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    assert fabric._get({"comid": 1}, timeout=5.0, retries=1) is None


def test_features_in_bbox_and_by_comid_request_shapes(monkeypatch):
    seen: list = []

    def fake(params, *, timeout, retries=2):
        seen.append(params)
        return _fc(_feat()) if "bbox" in params or params.get("comid") == 5214461 else _fc()
    monkeypatch.setattr(fabric, "_get", fake)
    feats = fabric.features_in_bbox(-83.1, 40.3, -83.0, 40.4)
    assert len(feats) == 1 and seen[0]["bbox"] == "-83.1,40.3,-83.0,40.4"
    assert seen[0]["limit"] == fabric.BBOX_LIMIT and seen[0]["properties"] == "comid,gnis_name"
    feat = fabric.feature_by_comid(5214461)
    assert feat["properties"]["comid"] == 5214461 and seen[1]["limit"] == 1
    assert fabric.feature_by_comid(999) == {}          # unknown comid, service answered
    monkeypatch.setattr(fabric, "_get", lambda *a, **k: None)
    assert fabric.feature_by_comid(5214461) is None   # service failed
    assert fabric.features_in_bbox(0, 0, 1, 1) is None


def test_attrs_from_feature_maps_and_guards():
    out = fabric.attrs_from_feature(_feat())
    assert out == {"gnis_name": "Sugar Run", "drainage_area_sqkm": 15.4935,
                   "huc8": "05060001", "slope": 0.01784203, "fcode": 46006,
                   "stream_order": 1}
    odd = fabric.attrs_from_feature(_feat(gnis_name="  ", slope=-9998, streamorde=None,
                                          reachcode=None, totdasqkm="12.5"))
    assert odd["gnis_name"] is None and odd["slope"] is None
    assert odd["stream_order"] is None and odd["huc8"] is None
    assert odd["drainage_area_sqkm"] == 12.5
    assert fabric.attrs_from_feature(None)["fcode"] is None


def test_flowlines_fetch_builds_the_layer_from_fabric(monkeypatch):
    flowlines._fetch.cache_clear()
    monkeypatch.setattr(fabric, "features_in_bbox",
                        lambda *a, **k: [_feat(), {"type": "Feature", "properties": {},
                                                   "geometry": {"type": "LineString",
                                                                "coordinates": []}}])
    fc = flowlines.flowlines_in_bbox(-83.06, 40.30, -83.05, 40.32)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
    assert fc["features"][0]["properties"] == {"comid": 5214461}
    assert fc["features"][0]["geometry"]["type"] == "MultiLineString"
    hit = flowlines.nearest_point_on_lines(fc, 40.3112, -83.0561)
    assert hit is not None and hit[3] == 5214461
    flowlines._fetch.cache_clear()
    monkeypatch.setattr(fabric, "features_in_bbox", lambda *a, **k: None)
    assert flowlines.flowlines_in_bbox(-83.06, 40.30, -83.05, 40.32) is None
    flowlines._fetch.cache_clear()


def test_flowline_attrs_reads_fabric(monkeypatch):
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: _feat())
    out = delineation.flowline_attrs(5214461)
    assert out["gnis_name"] == "Sugar Run" and out["drainage_area_sqkm"] == 15.4935
    assert out["huc8"] == "05060001" and out["stream_order"] == 1
    assert out["sinuosity"] is not None and out["sinuosity"] >= 1.0
    assert "_flowline_error" not in out
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: None)
    out = delineation.flowline_attrs(5214461)
    assert out["drainage_area_sqkm"] is None and "fabric" in out["_flowline_error"]
    monkeypatch.setattr(fabric, "feature_by_comid", lambda comid, **k: {})
    out = delineation.flowline_attrs(5214461)
    assert out["gnis_name"] is None and "_flowline_error" not in out

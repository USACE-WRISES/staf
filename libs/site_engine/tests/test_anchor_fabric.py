"""The engine's V2 attribute reader on the fabric API and the hydrolocation
snap's retry and flowtrace fallback. Fully offline."""
from __future__ import annotations

from site_engine import anchor

FEAT = {"type": "Feature", "properties": {
    "comid": 5214461, "gnis_name": "Sugar Run", "totdasqkm": 15.4935,
    "reachcode": "05060001000869", "slope": 0.01784203, "fcode": 46006, "streamorde": 1.0},
    "geometry": {"type": "MultiLineString", "coordinates": [[[-83.06, 40.31], [-83.05, 40.311]]]}}
POINT = {"type": "Feature", "properties": {"comid": 5214461, "reachcode": "05060001000869"},
         "geometry": {"type": "Point", "coordinates": [-83.0563, 40.3101]}}
INDEXED = {"type": "Feature", "properties": {"comid": 5214461, "source": "indexed"},
           "geometry": {"type": "Point", "coordinates": [-83.0563, 40.3101]}}


def test_v2_attrs_read_the_fabric_items(monkeypatch):
    seen: list = []

    def fake_get(url, params, timeout, retries=1):
        seen.append((url, params))
        return {"type": "FeatureCollection", "features": [FEAT]}, None
    monkeypatch.setattr(anchor, "_get_json", fake_get)
    out = anchor.v2_flowline_attrs(5214461)
    assert out == {"gnis_name": "Sugar Run", "drainage_area_sqkm": 15.4935, "huc8": "05060001",
                   "slope": 0.01784203, "fcode": 46006, "stream_order": 1}
    url, params = seen[0]
    assert url == anchor.V2_ITEMS_URL
    assert params["comid"] == 5214461 and params["limit"] == 1 and params["f"] == "json"
    assert "wmadata" not in url and "geoserver" not in url


def test_v2_attrs_escalate_then_fail_cleanly(monkeypatch):
    calls: list = []

    def fake_get(url, params, timeout, retries=1):
        calls.append(timeout)
        return None, "timeout"
    monkeypatch.setattr(anchor, "_get_json", fake_get)
    out = anchor.v2_flowline_attrs(5214461)
    assert "error" in out and calls == [60.0, 120.0]


def test_hydrolocation_retries_then_falls_to_flowtrace(monkeypatch):
    gets: list = []
    monkeypatch.setattr(anchor.time, "sleep", lambda s: None)

    def failing(url, params, timeout, retries=1):
        gets.append(retries)
        return None, "HTTP 502"
    monkeypatch.setattr(anchor, "_get_json", failing)
    monkeypatch.setattr(anchor, "_post_json",
                        lambda url, params, body, timeout: (
                            {"type": "FeatureCollection", "features": [POINT]}, None))
    out = anchor.hydrolocation_snap(40.31, -83.05)
    assert out == {"comid": 5214461, "snap_lon": -83.0563, "snap_lat": 40.3101}
    assert gets == [1, 0]                       # the first pass retries, the second does not

    monkeypatch.setattr(anchor, "_post_json", lambda *a, **k: (None, "HTTP 400"))
    out = anchor.hydrolocation_snap(40.31, -83.05)
    assert out["error"] == ("hydrolocation: HTTP 502; hydrolocation: HTTP 502; "
                            "flowtrace: HTTP 400")


def test_hydrolocation_success_never_touches_flowtrace(monkeypatch):
    monkeypatch.setattr(anchor, "_get_json", lambda url, params, timeout, retries=1: (
        {"type": "FeatureCollection", "features": [INDEXED]}, None))

    def boom(*a, **k):
        raise AssertionError("no fallback expected")
    monkeypatch.setattr(anchor, "_post_json", boom)
    out = anchor.hydrolocation_snap(40.31, -83.05)
    assert out["comid"] == 5214461 and out["snap_lat"] == 40.3101


def test_flowtrace_request_and_parse(monkeypatch):
    seen = {}

    def fake_post(url, params, body, timeout):
        seen.update(url=url, params=params, body=body)
        return {"type": "FeatureCollection", "features": [POINT]}, None
    monkeypatch.setattr(anchor, "_post_json", fake_post)
    assert anchor.flowtrace_snap(40.31125, -83.05615)["comid"] == 5214461
    assert seen["url"] == anchor.NLDI_FLOWTRACE_URL
    ids = {i["id"]: i["value"] for i in seen["body"]["inputs"]}
    assert ids == {"lat": "40.311250", "lon": "-83.056150", "direction": "none"}
    assert anchor.parse_flowtrace({"features": []}) == {}
    assert "error" in anchor.parse_flowtrace({"features": [FEAT]})   # a line, no point

"""The engine's V2 attribute reader on the fabric API. Fully offline."""
from __future__ import annotations

from site_engine import anchor

FEAT = {"type": "Feature", "properties": {
    "comid": 5214461, "gnis_name": "Sugar Run", "totdasqkm": 15.4935,
    "reachcode": "05060001000869", "slope": 0.01784203, "fcode": 46006, "streamorde": 1.0},
    "geometry": {"type": "MultiLineString", "coordinates": [[[-83.06, 40.31], [-83.05, 40.311]]]}}


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

"""The hydrolocation snap: one retry, then the flowtrace process on a separate
route, never the catchment lookup. Fully offline."""
from __future__ import annotations

import sys
import types

from easi import routing

POINT = {"type": "Feature", "properties": {"comid": 5214461, "reachcode": "05060001000869",
                                           "measure": 12.5},
         "geometry": {"type": "Point", "coordinates": [-83.0563, 40.3101]}}
LINE = {"type": "Feature", "properties": {"comid": 5214461},
        "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.311]]}}


def _nldi(monkeypatch, *, fail_times: int):
    calls = {"n": 0}

    class _NLDI:
        def comid_byloc(self, coords):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise RuntimeError("502 Bad Gateway")
            import pandas as pd
            return pd.DataFrame({"comid": [5214461]})
    monkeypatch.setitem(sys.modules, "pynhd", types.SimpleNamespace(NLDI=_NLDI))
    monkeypatch.setattr(routing.time, "sleep", lambda s: None)
    return calls


def test_parse_flowtrace_shapes():
    out = routing._parse_flowtrace({"type": "FeatureCollection", "features": [LINE, POINT]})
    assert out == {"comid": 5214461, "snap_lon": -83.0563, "snap_lat": 40.3101}
    assert routing._parse_flowtrace({"type": "FeatureCollection", "features": []}) == {}
    assert routing._parse_flowtrace(None) == {}
    assert "error" in routing._parse_flowtrace({"features": [LINE]})


def test_retry_then_success_never_calls_flowtrace(monkeypatch):
    calls = _nldi(monkeypatch, fail_times=1)
    monkeypatch.setattr(routing, "_flowtrace_snap",
                        lambda lat, lon, **k: (_ for _ in ()).throw(AssertionError("no fallback")))
    out = routing._hydrolocation_snap(40.31, -83.05)
    assert out["comid"] == 5214461 and calls["n"] == 2


def test_flowtrace_answers_when_hydrolocation_stays_down(monkeypatch):
    calls = _nldi(monkeypatch, fail_times=5)
    monkeypatch.setattr(routing, "_flowtrace_snap",
                        lambda lat, lon, **k: {"comid": 5214461, "snap_lat": 40.3101,
                                               "snap_lon": -83.0563})
    out = routing._hydrolocation_snap(40.31, -83.05)
    assert out["comid"] == 5214461 and calls["n"] == 2


def test_every_attempt_is_named_when_all_fail(monkeypatch):
    _nldi(monkeypatch, fail_times=5)
    monkeypatch.setattr(routing, "_flowtrace_snap",
                        lambda lat, lon, **k: {"error": "flowtrace: HTTP 400"})
    out = routing._hydrolocation_snap(40.31, -83.05)
    assert out["error"].count("hydrolocation: 502 Bad Gateway") == 2
    assert out["error"].endswith("flowtrace: HTTP 400")


def test_flowtrace_request_shape(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"type": "FeatureCollection", "features": [POINT]}

    def fake_post(url, params=None, json=None, timeout=None):
        seen.update(url=url, params=params, body=json)
        return _Resp()
    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = routing._flowtrace_snap(40.31125, -83.05615)
    assert out["comid"] == 5214461
    assert seen["url"] == routing.FLOWTRACE_URL and seen["params"] == {"f": "json"}
    ids = {i["id"]: i["value"] for i in seen["body"]["inputs"]}
    assert ids == {"lat": "40.311250", "lon": "-83.056150", "direction": "none"}
    assert all(i["type"] == "text/plain" for i in seen["body"]["inputs"])

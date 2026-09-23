"""The fetch caches keep answers only (2026-09-23), and NWI reads its current service.

Offline: the network calls are monkeypatched. An unanswered request is asked
again on the next call; an answer (even an empty one) is kept.
"""
from __future__ import annotations

from types import SimpleNamespace

from sfari.datasources import fabric, flowlines, nwi, streamcat

LINE = {"type": "Feature", "properties": {"comid": 5214461, "gnis_name": "Sugar Run"},
        "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.31]]}}
BOX = (-83.06, 40.30, -83.05, 40.32)


def test_v2_lines_ask_again_after_a_failure_and_keep_an_empty_answer(monkeypatch):
    answers = [None, [LINE]]
    calls = []

    def fake(*a, **k):
        calls.append(1)
        return answers.pop(0)
    monkeypatch.setattr(fabric, "features_in_bbox", fake)
    flowlines._fetch.cache_clear()
    try:
        assert flowlines.flowlines_in_bbox(*BOX) is None          # unanswered: not kept
        assert flowlines.flowlines_in_bbox(*BOX)["features"][0]["properties"]["comid"] == 5214461
        assert flowlines.flowlines_in_bbox(*BOX)["features"][0]["properties"]["comid"] == 5214461
        assert len(calls) == 2
        monkeypatch.setattr(fabric, "features_in_bbox", lambda *a, **k: calls.append(1) or [])
        other = (-83.16, 40.30, -83.15, 40.32)
        assert flowlines.flowlines_in_bbox(*other) is None        # answered with no line
        assert flowlines.flowlines_in_bbox(*other) is None
        assert len(calls) == 3
    finally:
        flowlines._fetch.cache_clear()


def test_streamcat_asks_again_after_both_hosts_were_silent(monkeypatch):
    answers = [None, None, {"items": [{"COMID": 7, "PCTIMP2019WS": 3}]}]
    calls = []

    def request(url, params, timeout, retries=2):
        calls.append(url)
        return answers.pop(0)
    monkeypatch.setattr(streamcat, "_request", request)
    streamcat._fetch.cache_clear()
    try:
        assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {}
        assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {"comid": 7.0, "pctimp2019ws": 3.0}
        assert streamcat.metrics_by_comid(7, ["pctimp2019"]) == {"comid": 7.0, "pctimp2019ws": 3.0}
        assert calls == [streamcat._PRIMARY, streamcat._MIRROR, streamcat._PRIMARY]
    finally:
        streamcat._fetch.cache_clear()


def test_streamcat_keeps_an_answer_with_no_row(monkeypatch):
    calls = []
    monkeypatch.setattr(streamcat, "_request",
                        lambda url, params, timeout, retries=2: calls.append(url) or {"items": []})
    streamcat._fetch.cache_clear()
    try:
        assert streamcat.metrics_by_comid(8, ["pctimp2019"]) == {}
        assert streamcat.metrics_by_comid(8, ["pctimp2019"]) == {}
        assert calls == [streamcat._PRIMARY]
    finally:
        streamcat._fetch.cache_clear()


def test_nwi_reads_the_current_service_and_its_qualified_fields(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append((url, params))
        return SimpleNamespace(status_code=200, json=lambda: {"features": [
            {"attributes": {"Wetlands.ACRES": 1.5, "Wetlands.WETLAND_TYPE": "Riverine"}},
            {"attributes": {"ACRES": 2.0, "WETLAND_TYPE": "Lake"}}]})
    monkeypatch.setattr(nwi.requests, "get", get)
    assert nwi.wetlands_near(43.6858, -72.2367) == {
        "acres": 3.5, "count": 2, "types": {"Riverine": 1, "Lake": 1}}
    url, params = seen[0]
    assert url.startswith("https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/")
    assert params["outFields"] == "Wetlands.ACRES,Wetlands.WETLAND_TYPE"
    monkeypatch.setattr(nwi.requests, "get",
                        lambda *a, **k: SimpleNamespace(status_code=404, json=lambda: {}))
    assert nwi.wetlands_near(43.6858, -72.2367) is None

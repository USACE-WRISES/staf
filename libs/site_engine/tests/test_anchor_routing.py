"""Offline contracts for the shared, bounded NLDI raindrop client."""
from copy import deepcopy
import logging

import pytest
import requests

from site_engine import anchor


POINT = {"type": "Feature", "properties": {"comid": 5214461},
         "geometry": {"type": "Point", "coordinates": [-83.0563, 40.3101]}}
INDEXED = {**POINT, "properties": {"identifier": "5214461", "source": "indexed"}}
# Current nldi-flowtools output: the returned line endpoints are not the snap.
FLOWLINE = {"type": "Feature", "id": "nhdFlowline", "properties": {
    "comid": 5214461, "intersection_point": [-83.0563, 40.3101],
    "raindrop_pathDist": 127.2, "measure": 25.0},
    "geometry": {"type": "LineString", "coordinates": [[-83.06, 40.31], [-83.05, 40.32]]}}
PATH = {"type": "Feature", "id": "raindropPath", "properties": {},
        "geometry": {"type": "LineString", "coordinates": [[-83.07, 40.31], [-83.0563, 40.3101]]}}
SNAP = {"comid": 5214461, "snap_lon": -83.0563, "snap_lat": 40.3101}


def fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class Response:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self.data, self.text = status, data, text

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


def transport(monkeypatch, replies):
    pending = iter(replies)
    calls, pauses = [], []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        reply = next(pending)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(anchor.requests, "get", lambda url, **k: request("GET", url, **k))
    monkeypatch.setattr(anchor.requests, "post", lambda url, **k: request("POST", url, **k))
    monkeypatch.setattr(anchor.time, "sleep", pauses.append)
    return calls, pauses


@pytest.mark.parametrize("features", [[FLOWLINE, PATH], [PATH, FLOWLINE], [PATH, POINT]])
def test_current_intersection_and_legacy_point(features):
    assert anchor.parse_flowtrace(fc(*features)) == SNAP


@pytest.mark.parametrize("data", [None, {}, [], {"features": []},
    {"type": "FeatureCollection", "features": None},
    {"type": "FeatureCollection", "features": {}}, fc(None),
    fc({"type": "Feature", "properties": [], "geometry": {}}), fc(PATH)])
def test_malformed_is_not_a_clean_no_stream(data):
    assert "error" in anchor.parse_flowtrace(data)
    assert "error" in anchor._parse_hydrolocation(data)


def test_only_explicit_empty_collection_is_no_stream():
    assert anchor.parse_flowtrace(fc()) == {}
    assert anchor._parse_hydrolocation(fc()) == {}
    assert anchor._parse_hydrolocation(fc(INDEXED)) == SNAP


@pytest.mark.parametrize("comid", [None, True, 0, -1, 1.5, "bad", float("nan"), float("inf")])
@pytest.mark.parametrize("modern", [True, False])
def test_invalid_identifier_is_not_rounded_or_guessed(comid, modern):
    feature = deepcopy(FLOWLINE if modern else POINT)
    feature["properties"]["comid"] = comid
    assert "error" in anchor.parse_flowtrace(fc(feature))


@pytest.mark.parametrize("coords", [None, [], [-83], "-83,40", [True, 40],
    [float("nan"), 40], [-83, float("inf")], [-181, 40], [-83, 91]])
@pytest.mark.parametrize("modern", [True, False])
def test_invalid_intersection_is_not_replaced_with_line_endpoint(coords, modern):
    feature = deepcopy(FLOWLINE if modern else POINT)
    if modern:
        feature["properties"]["intersection_point"] = coords
    else:
        feature["geometry"]["coordinates"] = coords
    assert "error" in anchor.parse_flowtrace(fc(feature))


def test_fourth_request_recovers_with_progress_before_each_pause(monkeypatch, caplog):
    calls, pauses = transport(monkeypatch, [Response(502), requests.Timeout("slow"),
                                          Response(429),
                                          Response(data=fc(FLOWLINE, PATH))])
    progress = []
    progress_at_pause = []

    def pause(seconds):
        pauses.append(seconds)
        progress_at_pause.append((len(calls), dict(progress[-1])))

    monkeypatch.setattr(anchor.time, "sleep", pause)
    with caplog.at_level(logging.INFO, logger=anchor.__name__):
        assert anchor.hydrolocation_snap(43.68583, -72.23669, progress=progress.append) == SNAP
    assert [c[0] for c in calls] == ["GET", "POST", "POST", "POST"]
    assert [c[2]["timeout"] for c in calls] == [30.0, 60.0, 60.0, 60.0]
    assert all(c[2]["allow_redirects"] is False for c in calls)
    assert calls[0][1] == anchor.NLDI_HYDROLOCATION_URL
    assert calls[0][2]["params"] == {"coords": "POINT(-72.236690 43.685830)"}
    for _, url, kwargs in calls[1:]:
        assert url == anchor.NLDI_FLOWTRACE_URL and kwargs["params"] == {"f": "json"}
        assert kwargs["json"] == {"inputs": [
            {"id": "lat", "value": "43.685830", "type": "text/plain"},
            {"id": "lon", "value": "-72.236690", "type": "text/plain"},
            {"id": "direction", "value": "none", "type": "text/plain"}]}
    assert pauses == [5.0, 10.0, 15.0]
    assert progress == [{"status": "finding", "attempt": 1},
                        {"status": "retrying", "attempt": 2},
                        {"status": "retrying", "attempt": 3},
                        {"status": "retrying", "attempt": 4}]
    assert progress_at_pause == [(1, {"status": "retrying", "attempt": 2}),
                                 (2, {"status": "retrying", "attempt": 3}),
                                 (3, {"status": "retrying", "attempt": 4})]
    assert len(caplog.records) == 4
    for n, record in enumerate(caplog.records, 1):
        assert f"attempt={n}" in record.message
        assert all(key in record.message for key in ("endpoint=", "status=", "elapsed=", "error="))


@pytest.mark.parametrize("first", [Response(408), Response(429), Response(500), Response(503),
                                   requests.ConnectionError("offline"), requests.Timeout("slow")])
def test_transient_errors_recover_on_flowtrace(monkeypatch, first):
    calls, pauses = transport(monkeypatch, [first, Response(data=fc(FLOWLINE))])
    assert anchor.hydrolocation_snap(40, -83) == SNAP
    assert len(calls) == 2 and pauses == [5.0]


@pytest.mark.parametrize("reply", [Response(400), Response(404), Response(301),
    Response(data={}), Response(data=ValueError("broken JSON")),
    Response(data=fc(PATH)), requests.RequestException("invalid request")])
def test_terminal_errors_do_not_fall_back(monkeypatch, reply):
    calls, pauses = transport(monkeypatch, [reply])
    assert "error" in anchor.hydrolocation_snap(40, -83)
    assert len(calls) == 1 and pauses == []


@pytest.mark.parametrize("data", [fc(INDEXED), fc()])
def test_success_and_clean_empty_stop_without_fallback(monkeypatch, data):
    calls, pauses = transport(monkeypatch, [Response(data=data)])
    assert anchor.hydrolocation_snap(40, -83) == (SNAP if data["features"] else {})
    assert len(calls) == 1 and pauses == []


@pytest.mark.parametrize("reply", [Response(400), Response(data={}), Response(data=fc())])
@pytest.mark.parametrize("attempt", [2, 3])
def test_retry_terminal_or_empty_result_stops(monkeypatch, reply, attempt):
    calls, pauses = transport(monkeypatch, [Response(502)] * (attempt - 1) + [reply])
    result = anchor.hydrolocation_snap(40, -83)
    if reply.data == fc():
        assert result == {}
    else:
        assert "error" in result
    assert len(calls) == attempt and pauses == [5.0, 10.0][:attempt - 1]


def test_third_request_success_skips_final_retry(monkeypatch):
    calls, pauses = transport(monkeypatch, [Response(502), Response(503),
                                          Response(data=fc(FLOWLINE))])
    assert anchor.hydrolocation_snap(40, -83) == SNAP
    assert len(calls) == 3 and pauses == [5.0, 10.0]


def test_all_transient_failures_are_bounded_and_logged(monkeypatch, caplog):
    calls, pauses = transport(monkeypatch, [Response(502, {"detail": "upstream\n" + "x" * 2000})] * 4)
    result = anchor.hydrolocation_snap(40, -83)
    assert len(calls) == 4 and pauses == [5.0, 10.0, 15.0]
    assert result["error"].count("hydrolocation:") == 1
    assert result["error"].count("flowtrace:") == 3
    assert "\n" not in result["error"] and len(result["error"]) < 1900
    assert len(caplog.records) == 4
    assert all(len(record.message) < 550 for record in caplog.records)


def test_standalone_flowtrace_keeps_one_request_and_custom_timeout(monkeypatch):
    calls, pauses = transport(monkeypatch, [Response(data=fc(POINT))])
    assert anchor.flowtrace_snap(40, -83, timeout=12) == SNAP
    assert len(calls) == 1 and calls[0][0] == "POST"
    assert calls[0][2]["timeout"] == 12 and pauses == []


def test_progress_callback_failure_does_not_change_the_answer(monkeypatch):
    transport(monkeypatch, [Response(data=fc(INDEXED))])

    def broken_progress(event):
        raise RuntimeError("UI disconnected")

    assert anchor.hydrolocation_snap(40, -83, progress=broken_progress) == SNAP

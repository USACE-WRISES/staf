"""HR bbox fetch: the status channel, the cache that keeps only answers, and
the map's fast-fail policy (one attempt, no second try after a timeout)."""
from __future__ import annotations

import pytest
import requests

from site_engine import hr

FEATURE = {"type": "Feature",
           "properties": {"nhdplusid": 10000900049512.0, "innetwork": 1},
           "geometry": {"type": "LineString",
                        "coordinates": [[-72.24, 43.68], [-72.23, 43.69]]}}
BOX = (-72.25, 43.68, -72.22, 43.70)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _ok():
    return _Resp(200, {"type": "FeatureCollection", "features": [FEATURE]})


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    hr._fetch_bbox.cache_clear()
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    yield
    hr._fetch_bbox.cache_clear()


def _script(monkeypatch, *answers):
    """requests.get returns (or raises) each answer in turn; calls are recorded."""
    calls = []
    todo = list(answers)

    def fake_get(url, params=None, timeout=None):
        calls.append(timeout)
        answer = todo.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(hr.requests, "get", fake_get)
    return calls


def test_statuses(monkeypatch):
    _script(monkeypatch, _ok())
    status, recs = hr.flowlines_in_bbox_status(*BOX)
    assert status == "ok" and recs[0]["nhdplusid"] == 10000900049512
    hr._fetch_bbox.cache_clear()
    _script(monkeypatch, _Resp(200, {"type": "FeatureCollection", "features": []}))
    assert hr.flowlines_in_bbox_status(*BOX) == ("empty", [])
    hr._fetch_bbox.cache_clear()
    _script(monkeypatch, _Resp(200, {"features": [FEATURE], "exceededTransferLimit": True}))
    assert hr.flowlines_in_bbox_status(*BOX) == ("truncated", [])
    hr._fetch_bbox.cache_clear()
    _script(monkeypatch, _Resp(500), _Resp(502))
    assert hr.flowlines_in_bbox_status(*BOX) == ("failed", [])


def test_boxes_that_are_never_asked(monkeypatch):
    calls = _script(monkeypatch)
    assert hr.flowlines_in_bbox_status(-73.0, 43.0, -72.0, 44.0) == ("too-large", [])
    assert hr.flowlines_in_bbox_status(-72.2, 43.6, -72.2, 43.7) == ("empty", [])
    assert calls == []


def test_a_failure_is_asked_again_and_an_answer_is_kept(monkeypatch):
    calls = _script(monkeypatch, _Resp(503), _Resp(503), _ok())
    assert hr.flowlines_in_bbox(*BOX) == []
    assert len(calls) == 2
    assert hr.flowlines_in_bbox(*BOX)[0]["nhdplusid"] == 10000900049512
    assert len(calls) == 3
    assert hr.flowlines_in_bbox_status(*BOX)[0] == "ok"
    assert len(calls) == 3                 # the answer came from the cache


@pytest.mark.parametrize("payload", [
    {"type": "FeatureCollection", "features": []},
    {"features": [FEATURE], "exceededTransferLimit": True}])
def test_deterministic_answers_are_kept(monkeypatch, payload):
    calls = _script(monkeypatch, _Resp(200, payload))
    first = hr.flowlines_in_bbox_status(*BOX)
    assert hr.flowlines_in_bbox_status(*BOX) == first
    assert len(calls) == 1


def test_fast_fail_never_retries_a_timeout(monkeypatch):
    calls = _script(monkeypatch, requests.exceptions.ReadTimeout("slow"))
    assert hr.flowlines_in_bbox_status(*BOX, fast_fail=True) == ("failed", [])
    assert calls == [hr._DISPLAY_TIMEOUT_S]


def test_fast_fail_retries_a_quick_failure_once(monkeypatch):
    calls = _script(monkeypatch, _Resp(502), _ok())
    assert hr.flowlines_in_bbox_status(*BOX, fast_fail=True)[0] == "ok"
    assert len(calls) == 2


def test_fast_fail_does_not_retry_a_slow_failure(monkeypatch):
    ticks = []

    def clock():                           # the attempt starts at 0 and fails 10 s later
        ticks.append(1)
        return 0.0 if len(ticks) == 1 else 10.0
    monkeypatch.setattr(hr.time, "monotonic", clock)
    calls = _script(monkeypatch, _Resp(502), _ok())
    assert hr.flowlines_in_bbox_status(*BOX, fast_fail=True) == ("failed", [])
    assert len(calls) == 1


def test_default_policy_is_unchanged(monkeypatch):
    calls = _script(monkeypatch, requests.exceptions.ReadTimeout("slow"), _ok())
    assert hr.flowlines_in_bbox(*BOX)[0]["nhdplusid"] == 10000900049512
    assert calls == [30.0, 30.0]

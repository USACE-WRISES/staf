"""The HR client's resilience (2026-09-30): request policies and their caps,
the deadline, the disk cache of answers, the map's tiles, the pick's probe,
and batch splitting. Fully offline: requests.get / post are scripted."""
from __future__ import annotations

import json
import threading
import time

import pytest
import requests

from site_engine import delineate, httpcache, hr


def _nid(w, s):
    """A distinct id per grid cell of the smallest tile size."""
    return int(round((w + 180) / 0.0125)) * 100000 + int(round((s + 90) / 0.0125))


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _line(nid, x0, y0, x1, y1, **props):
    base = {"nhdplusid": float(nid), "innetwork": 1, "hydroseq": float(nid + 1000)}
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "LineString", "coordinates": [[x0, y0], [x1, y1]]}}


def _fc(*features, exceeded=False):
    out = {"type": "FeatureCollection", "features": list(features)}
    if exceeded:
        out["exceededTransferLimit"] = True
    return out


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    hr.clear_caches()
    hr.set_policy("patient")
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    yield
    hr.set_policy("patient")
    hr.clear_caches()
    httpcache.reset()


def _script_get(monkeypatch, answer_for):
    """requests.get answers through ``answer_for(params, timeout)``; each call
    is recorded as ``(geometry or where, timeout)``."""
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(((params or {}).get("geometry") or (params or {}).get("where"), timeout))
        answer = answer_for(params or {}, timeout)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(hr.requests, "get", fake_get)
    return calls


# --------------------------------------------------------------------------- #
# policies
# --------------------------------------------------------------------------- #
def test_the_process_default_is_patient_and_unknown_policies_refuse():
    assert hr.active_policy().name == "patient"
    hr.set_policy("interactive")
    assert hr.active_policy().name == "interactive"
    with pytest.raises(ValueError):
        hr.set_policy("eager")


def test_display_policy_one_attempt_after_a_timeout(monkeypatch):
    # A tile may take a minute in the background (the view waits 30 s): the
    # recovering service answered in 40 to 100 s on 2026-09-30.
    calls = _script_get(monkeypatch, lambda p, t: requests.exceptions.ReadTimeout("slow"))
    assert hr._query("u", {"where": "1=1"}, timeout=99.0, policy="display") is None
    assert [c[1] for c in calls] == [hr._TILE_TIMEOUT_S] == [60.0]


def test_display_policy_retries_a_quick_failure_once(monkeypatch):
    answers = [_Resp(502), _Resp(200, _fc())]
    calls = _script_get(monkeypatch, lambda p, t: answers.pop(0))
    assert hr._query("u", {"where": "1=1"}, timeout=99.0, policy="display") == _fc()
    assert len(calls) == 2


def test_pick_policy_one_long_attempt_and_a_quick_failure_retried(monkeypatch):
    calls = _script_get(monkeypatch, lambda p, t: requests.exceptions.ReadTimeout("slow"))
    assert hr._query("u", {"where": "1=1"}, timeout=99.0, policy="pick") is None
    assert [c[1] for c in calls] == [hr._PICK_TIMEOUT_S] == [45.0]
    answers = [_Resp(502), _Resp(200, _fc())]
    calls = _script_get(monkeypatch, lambda p, t: answers.pop(0))
    assert hr._query("u", {"where": "1=1"}, timeout=99.0, policy="pick") == _fc()
    assert len(calls) == 2


def test_interactive_caps_the_timeout_and_pauses_between_attempts(monkeypatch):
    pauses = []
    monkeypatch.setattr(hr.time, "sleep", lambda s: pauses.append(s))
    calls = _script_get(monkeypatch, lambda p, t: _Resp(503))
    assert hr._query("u", {"where": "1=1"}, timeout=90.0, policy="interactive") is None
    assert [c[1] for c in calls] == [60.0, 60.0]
    assert pauses == [2.0]                          # none after the last attempt


def test_a_client_error_is_not_asked_again_under_a_policy(monkeypatch):
    calls = _script_get(monkeypatch, lambda p, t: _Resp(400))
    assert hr._query("u", {"where": "1=1"}, timeout=30.0, policy="interactive") is None
    assert len(calls) == 1
    # the patient rules ask again whatever the failure (as they always did)
    calls = _script_get(monkeypatch, lambda p, t: _Resp(400))
    assert hr._query("u", {"where": "1=1"}, timeout=30.0, retries=1) is None
    assert len(calls) == 2


def test_an_error_payload_is_a_failure_and_observers_see_every_failure(monkeypatch):
    answers = [_Resp(200, {"error": {"code": 500}}), requests.exceptions.ConnectionError("reset"),
               _Resp(200, _fc())]
    _script_get(monkeypatch, lambda p, t: answers.pop(0))
    seen = []
    assert hr._query("u", {"where": "1=1"}, timeout=30.0, retries=2,
                     on_attempt=seen.append) == _fc()
    assert seen[0] == 500 and isinstance(seen[1], requests.exceptions.ConnectionError)


def test_last_answer_at_follows_the_service_not_the_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    httpcache.reset()
    _script_get(monkeypatch, lambda p, t: _Resp(200, _fc()))
    before = hr.last_answer_at()
    hr._query("u", {"where": "a"}, timeout=5.0)
    first = hr.last_answer_at()
    assert first is not None and (before is None or first >= before)
    monkeypatch.setattr(hr.requests, "get", lambda *a, **k: pytest.fail("cached"))
    hr._query("u", {"where": "a"}, timeout=5.0)
    assert hr.last_answer_at() == first


# --------------------------------------------------------------------------- #
# the deadline
# --------------------------------------------------------------------------- #
def test_past_the_deadline_nothing_is_sent(monkeypatch):
    calls = _script_get(monkeypatch, lambda p, t: _Resp(200, _fc()))
    with hr.deadline(10.0):
        monkeypatch.setattr(hr, "_remaining", lambda: -1.0)
        assert hr.out_of_time()
        assert hr._query("u", {"where": "1=1"}, timeout=30.0, policy="interactive") is None
        assert hr._query("u", {"where": "1=1"}, timeout=30.0) is None     # patient too
    assert calls == []


def test_an_attempt_never_outlives_the_deadline(monkeypatch):
    calls = _script_get(monkeypatch, lambda p, t: _Resp(200, _fc()))
    monkeypatch.setattr(hr, "_remaining", lambda: 7.5)
    hr._query("u", {"where": "1=1"}, timeout=30.0, policy="interactive")
    assert calls[0][1] == 7.5


def test_deadlines_nest_and_unwind():
    assert not hr.out_of_time() and hr._remaining() is None
    with hr.deadline(100.0):
        outer = hr._remaining()
        with hr.deadline(1000.0):                   # the earlier deadline wins
            assert hr._remaining() <= outer
        with hr.deadline(None):
            assert hr._remaining() is not None
    assert hr._remaining() is None


# --------------------------------------------------------------------------- #
# the disk cache
# --------------------------------------------------------------------------- #
def test_the_cache_is_off_under_pytest_unless_a_folder_is_named(monkeypatch, tmp_path):
    monkeypatch.delenv("STAF_HR_CACHE_DIR", raising=False)
    assert httpcache.cache_folder() is None
    monkeypatch.setenv("STAF_HR_CACHE_DIR", "off")
    assert httpcache.cache_folder() is None
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    assert httpcache.cache_folder() == tmp_path


def test_answers_are_stored_and_failures_are_not(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    httpcache.reset()
    answers = [_Resp(503), _Resp(503), _Resp(200, _fc(_line(1, 0, 0, 1, 1)))]
    calls = _script_get(monkeypatch, lambda p, t: answers.pop(0))
    params = {"where": "nhdplusid = 1"}
    assert hr._query("u", params, timeout=5.0, retries=1) is None       # two failures
    assert len(calls) == 2
    got = hr._query("u", params, timeout=5.0, retries=1)
    assert got["features"][0]["properties"]["nhdplusid"] == 1.0 and len(calls) == 3
    assert hr._query("u", params, timeout=5.0, retries=1) == got         # from disk
    assert len(calls) == 3
    assert hr._query("u", dict(params), timeout=5.0, offline=True) == got
    assert hr._query("u", {"where": "other"}, timeout=5.0, offline=True) is None
    assert len(calls) == 3
    assert httpcache.stats()["answers"] == 1


def test_expired_answers_are_asked_again(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    httpcache.reset()
    key = httpcache.key_for("u", {"a": 1})
    httpcache.put(key, {"features": []})
    assert httpcache.get(key) == {"features": []}
    real = time.time
    monkeypatch.setattr(httpcache.time, "time", lambda: real() + httpcache.TTL_S + 60)
    assert httpcache.get(key) is None


def test_the_oldest_answers_go_first_past_the_size_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    httpcache.reset()
    monkeypatch.setattr(httpcache, "_EVICT_EVERY", 1)
    blob = {"text": "x" * 4000}                     # compresses to a few dozen bytes
    size = len(httpcache.zlib.compress(json.dumps(blob, separators=(",", ":")).encode(), 6))
    monkeypatch.setattr(httpcache, "MAX_BYTES", size * 3)
    for n in range(6):
        httpcache.put(f"k{n}", {**blob, "n": n})
    kept = [n for n in range(6) if httpcache.get(f"k{n}") is not None]
    assert kept and kept == list(range(6 - len(kept), 6)) and len(kept) <= 4


def test_the_cache_key_ignores_parameter_order():
    assert httpcache.key_for("u", {"a": 1, "b": 2}) == httpcache.key_for("u", {"b": 2, "a": 1})
    assert httpcache.key_for("u", {"a": 1}) != httpcache.key_for("u", {"a": 1}, "POST")


# --------------------------------------------------------------------------- #
# the map's tiles
# --------------------------------------------------------------------------- #
def _tile_of(params):
    w, s, e, n = (float(v) for v in params["geometry"].split(","))
    return round(w, 6), round(s, 6), round(e, 6), round(n, 6)


def test_tiles_cover_the_view_on_the_global_grid():
    tiles = hr.tiles_over(-83.07, 40.01, -82.96, 40.09)
    assert [hr.tile_bbox(t) for t in tiles][:1] == [(-83.1, 40.0, -83.05, 40.05)]
    assert len(tiles) == 3 * 2
    # an edge on a grid line does not pull in the next tile
    assert len(hr.tiles_over(-83.1, 40.0, -83.05, 40.05)) == 1


def test_a_view_draws_the_tiles_that_answered(monkeypatch):
    box = (-83.07, 40.01, -82.96, 40.09)
    bad = hr.tile_bbox(hr.tiles_over(*box)[0])

    def answer(p, t):
        w, s, e, n = _tile_of(p)
        if (w, s, e, n) == bad:
            return _Resp(503)
        # one line inside the tile and one crossing into the tile to the east
        return _Resp(200, _fc(_line(_nid(w, s), w + 0.01, s + 0.01, w + 0.02, s + 0.02),
                              _line(7, -83.051, 40.02, -83.049, 40.03)))
    calls = _script_get(monkeypatch, answer)
    status, recs, missing = hr.flowlines_in_tiles(*box)
    assert status == "partial" and missing == [bad]
    ids = [r["nhdplusid"] for r in recs]
    assert len(ids) == len(set(ids)) and 7 in ids   # de-duplicated across tiles
    asked = len(calls)
    # the tiles that answered are remembered: asking again sends only the missing one
    hr.flowlines_in_tiles(*box)
    assert len(calls) - asked == 2                  # the missing tile, retried once (502-class)
    assert all(_tile_of({"geometry": c[0]}) == bad for c in calls[asked:])


def test_every_tile_missing_is_failed_and_empty_tiles_are_empty(monkeypatch):
    box = (-83.07, 40.01, -83.06, 40.02)
    _script_get(monkeypatch, lambda p, t: requests.exceptions.ReadTimeout("slow"))
    status, recs, missing = hr.flowlines_in_tiles(*box)
    assert status == "failed" and recs == [] and len(missing) == 1
    _script_get(monkeypatch, lambda p, t: _Resp(200, _fc()))
    assert hr.flowlines_in_tiles(*box) == ("empty", [], [])
    assert hr.flowlines_in_tiles(-84.0, 40.0, -83.0, 41.0)[0] == "too-large"


def test_a_tile_over_the_cap_is_answered_by_its_quarters(monkeypatch):
    box = (-83.07, 40.01, -83.06, 40.02)            # inside one top-level tile

    def answer(p, t):
        w, s, e, n = _tile_of(p)
        if round(e - w, 6) == hr.TILE_DEG:
            return _Resp(200, _fc(_line(1, w, s, e, n), exceeded=True))
        return _Resp(200, _fc(_line(_nid(w, s), w, s, e, n)))
    calls = _script_get(monkeypatch, answer)
    status, recs, missing = hr.flowlines_in_tiles(*box)
    assert status == "ok" and missing == [] and len(recs) == 4
    assert len(calls) == 5                          # the tile, then its four quarters


def test_a_tile_over_the_cap_at_the_smallest_size_is_truncated(monkeypatch):
    _script_get(monkeypatch, lambda p, t: _Resp(200, _fc(exceeded=True)))
    assert hr.flowlines_in_tiles(-83.07, 40.01, -83.06, 40.02)[0] == "truncated"


def test_offline_views_read_stored_tiles_only(monkeypatch, tmp_path):
    monkeypatch.setenv("STAF_HR_CACHE_DIR", str(tmp_path))
    httpcache.reset()
    box = (-83.07, 40.01, -83.06, 40.02)
    _script_get(monkeypatch, lambda p, t: _Resp(200, _fc(_line(5, -83.065, 40.015, -83.064, 40.016))))
    assert hr.flowlines_in_tiles(*box)[0] == "ok"
    hr.clear_caches()                               # a new process: only the disk remains
    monkeypatch.setattr(hr.requests, "get", lambda *a, **k: pytest.fail("offline asked"))
    status, recs, _ = hr.flowlines_in_tiles(*box, offline=True)
    assert status == "ok" and recs[0]["nhdplusid"] == 5
    assert hr.flowlines_in_tiles(-80.07, 40.01, -80.06, 40.02, offline=True)[0] == "failed"


def test_a_tile_still_running_counts_as_missing_and_finishes_into_the_memo(monkeypatch):
    box = (-83.07, 40.01, -83.06, 40.02)
    gate = threading.Event()

    def answer(p, t):
        gate.wait(5)
        return _Resp(200, _fc(_line(9, -83.065, 40.015, -83.064, 40.016)))
    calls = _script_get(monkeypatch, answer)
    status, _recs, missing = hr.flowlines_in_tiles(*box, wait_s=0.05)
    assert status == "failed" and len(missing) == 1
    job = next(iter(hr._tile_jobs.values()))
    assert hr.flowlines_in_tiles(*box, wait_s=0.05)[0] == "failed"
    assert len(calls) == 1                          # the second view joined the running fetch
    gate.set()
    job.result(timeout=5)
    assert hr.flowlines_in_tiles(*box)[0] == "ok" and len(calls) == 1


# --------------------------------------------------------------------------- #
# the pick's probe
# --------------------------------------------------------------------------- #
def test_snap_records_are_the_lines_in_the_probe_box(monkeypatch):
    lat, lon = 40.025, -83.025                      # probe box -83.037..-83.013

    def answer(p, t):
        return _Resp(200, _fc(_line(1, -83.026, 40.02, -83.024, 40.03),     # in the box
                              _line(2, -83.049, 40.02, -83.048, 40.03)))    # outside it
    _script_get(monkeypatch, answer)
    status, recs = hr.snap_records(lat, lon)
    assert status == "ok" and [r["nhdplusid"] for r in recs] == [1]


def test_snap_records_fail_when_a_tile_does_not_answer(monkeypatch):
    calls = _script_get(monkeypatch, lambda p, t: requests.exceptions.ReadTimeout("slow"))
    assert hr.snap_records(40.025, -83.025) == ("failed", [])
    assert [c[1] for c in calls] == [hr._PICK_TIMEOUT_S]   # the pick policy, one attempt


def test_a_pick_on_drawn_tiles_sends_nothing(monkeypatch):
    _script_get(monkeypatch, lambda p, t: _Resp(200, _fc(_line(1, -83.026, 40.02, -83.024, 40.03))))
    hr.flowlines_in_tiles(-83.04, 40.01, -83.01, 40.04)
    monkeypatch.setattr(hr.requests, "get", lambda *a, **k: pytest.fail("asked"))
    assert hr.snap_records(40.025, -83.025)[0] == "ok"


# --------------------------------------------------------------------------- #
# batch splitting
# --------------------------------------------------------------------------- #
def _catchment(nid):
    return {"type": "Feature", "properties": {"nhdplusid": float(nid), "areasqkm": 1.0},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}


def _script_post(monkeypatch, fail_over: int):
    """Catchment POSTs answer only batches of at most ``fail_over`` ids."""
    sizes = []

    def fake_post(url, data=None, timeout=None):
        ids = [int(x) for x in data["where"].split("(")[1].rstrip(")").split(",")]
        sizes.append(len(ids))
        if len(ids) > fail_over:
            raise requests.exceptions.ReadTimeout("too big")
        return _Resp(200, _fc(*[_catchment(i) for i in ids]))
    monkeypatch.setattr(hr.requests, "post", fake_post)
    return sizes


def test_a_failing_batch_splits_and_completes_under_interactive(monkeypatch):
    hr.set_policy("interactive")
    sizes = _script_post(monkeypatch, fail_over=50)
    progress = []
    out = hr.catchments_by_ids(list(range(1, 151)), progress=lambda d, t: progress.append((d, t)))
    assert [c["nhdplusid"] for c in out] == list(range(1, 151))
    assert sizes == [100, 100, 50, 50, 50]          # the 100 failed twice, then halves
    assert progress == [(1, 2), (2, 2)]


def test_splitting_stops_at_twenty_five_and_fails_whole(monkeypatch):
    hr.set_policy("interactive")
    sizes = _script_post(monkeypatch, fail_over=10)
    assert hr.catchments_by_ids(list(range(1, 101))) is None
    assert min(sizes) == 25


def test_the_patient_rules_never_split(monkeypatch):
    sizes = _script_post(monkeypatch, fail_over=50)
    assert hr.catchments_by_ids(list(range(1, 101))) is None
    assert set(sizes) == {100}


def test_a_stalled_walk_ends_at_the_deadline(monkeypatch):
    hr.set_policy("interactive")
    monkeypatch.setattr(hr.requests, "post", lambda *a, **k: pytest.fail("sent past the deadline"))
    monkeypatch.setattr(hr, "_remaining", lambda: -1.0)
    anchor = {"nhdplusid": 1, "hydroseq": 11, "dnhydroseq": 10, "totdasqkm": 1.0,
              "geometry": {"type": "LineString", "coordinates": [[-83.0, 40.0], [-83.0, 40.01]]}}
    out = delineate.delineate_watershed(anchor)
    assert out["status"] == "failed" and out["reason"] == "upstream tree query failed"


def test_the_catchment_stage_reports_its_batches(monkeypatch):
    monkeypatch.setattr(hr, "parents_by_node", lambda frontier, **k: [])

    def cats(ids, progress=None, **k):
        progress(1, 2)
        progress(2, 2)
        return [{"nhdplusid": 1, "areasqkm": 1.0, "geometry": {
            "type": "Polygon", "coordinates": [[[-83.0, 40.0], [-82.99, 40.0],
                                                [-82.99, 40.01], [-83.0, 40.0]]]}}]
    monkeypatch.setattr(hr, "catchments_by_ids", cats)
    events = []
    anchor = {"nhdplusid": 1, "hydroseq": 11, "dnhydroseq": 10, "totdasqkm": 0.5,
              "geometry": {"type": "LineString", "coordinates": [[-83.0, 40.0], [-83.0, 40.01]]}}
    out = delineate.delineate_watershed(anchor, progress=events.append)
    assert out["status"] == "ok"
    assert [(e.get("batch"), e.get("batches")) for e in events if e.get("batch")] == [(1, 2), (2, 2)]


def test_a_pick_joins_the_tile_the_map_is_fetching(monkeypatch):
    gate = threading.Event()

    def answer(p, t):
        gate.wait(5)
        return _Resp(200, _fc(_line(1, -83.026, 40.02, -83.024, 40.03)))
    calls = _script_get(monkeypatch, answer)
    assert hr.flowlines_in_tiles(-83.04, 40.01, -83.01, 40.04, wait_s=0.05)[0] == "failed"
    # the map's fetch of the tile is still running: the pick waits on it, never a second request
    assert hr.snap_records(40.025, -83.025, wait_s=0.05) == ("failed", [])
    assert len(calls) == 1
    gate.set()
    job = next(iter(hr._tile_jobs.values()), None)
    if job is not None:
        job.result(timeout=5)
    status, recs = hr.snap_records(40.025, -83.025)
    assert status == "ok" and [r["nhdplusid"] for r in recs] == [1] and len(calls) == 1


def test_a_tile_asks_spatially_and_keeps_network_lines_only(monkeypatch):
    seen = []

    def answer(p, t):
        seen.append(p.get("where"))
        return _Resp(200, _fc(_line(1, -83.065, 40.015, -83.064, 40.016),
                              _line(2, -83.066, 40.015, -83.065, 40.016, innetwork=0)))
    _script_get(monkeypatch, answer)
    status, recs, _missing = hr.flowlines_in_tiles(-83.07, 40.01, -83.06, 40.02)
    assert status == "ok" and [r["nhdplusid"] for r in recs] == [1]
    assert seen == ["1=1"]
    # the engine's own anchoring keeps its exact query and filter
    assert hr._bbox_params(0, 0, 1, 1)["where"] == "innetwork=1"

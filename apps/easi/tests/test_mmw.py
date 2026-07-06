"""Offline tests for the MMW delineation client (mock requests + time.sleep).

No network: ``requests.post``/``requests.get`` and ``time.sleep`` are monkey-
patched, so the POST -> poll-job -> extract flow and its graceful-failure paths
are exercised deterministically. Live behavior is covered manually via
``scripts/compare_watersheds.py`` (needs MMW_API_KEY).
"""
from __future__ import annotations

import easi.datasources.mmw as mmw


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def _boom(*_a, **_k):
    raise AssertionError("network call should not happen here")


def _square_fc(cx: float, cy: float, d: float = 0.05, props: dict | None = None) -> dict:
    """A small square polygon FeatureCollection centered on (cx, cy)."""
    h = d / 2
    ring = [[cx - h, cy - h], [cx + h, cy - h], [cx + h, cy + h],
            [cx - h, cy + h], [cx - h, cy - h]]
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props or {},
         "geometry": {"type": "Polygon", "coordinates": [ring]}}]}


def _point_fc(cx: float, cy: float) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [cx, cy]}}]}


def _seq_get(responses):
    """A fake requests.get that returns the queued responses, repeating the last."""
    it = iter(responses)
    last = responses[-1]

    def get(url, **kw):
        try:
            return next(it)
        except StopIteration:
            return last
    return get


def _ok_post(*_a, **_k):
    return _Resp(200, {"job": "abc-123", "status": "started"})


# --- happy path ------------------------------------------------------------ #
def test_poll_completes_and_extracts(monkeypatch):
    monkeypatch.setenv("MMW_API_KEY", "test-token")
    monkeypatch.setattr(mmw.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mmw.requests, "post", _ok_post)
    result = {"watershed": _square_fc(-83.0, 40.0, 0.05),
              "input_pt": _point_fc(-83.001, 39.999)}
    monkeypatch.setattr(mmw.requests, "get", _seq_get([
        _Resp(200, {"status": "started"}),
        _Resp(200, {"status": "complete", "result": result})]))

    fc, area, pt, warnings = mmw.delineate_watershed_mmw(40.0, -83.0, max_wait=30)
    assert fc and fc["features"]
    assert area is not None and area > 0
    assert pt and pt["features"][0]["geometry"]["type"] == "Point"
    assert warnings == []


def test_area_is_computed_from_geometry_not_property(monkeypatch):
    """A bogus area property must be ignored; area comes from the 5070 geometry."""
    monkeypatch.setenv("MMW_API_KEY", "test-token")
    monkeypatch.setattr(mmw.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mmw.requests, "post", _ok_post)
    ws = _square_fc(-83.0, 40.0, 0.05, props={"area": 999999.0})
    monkeypatch.setattr(mmw.requests, "get", _seq_get([
        _Resp(200, {"status": "complete", "result": {"watershed": ws}})]))

    _fc, area, _pt, _w = mmw.delineate_watershed_mmw(40.0, -83.0, max_wait=10)
    assert area is not None and 0 < area < 100  # ~0.95 km², not the bogus prop


# --- graceful-failure paths ------------------------------------------------ #
def test_job_failure_is_graceful(monkeypatch):
    monkeypatch.setenv("MMW_API_KEY", "test-token")
    monkeypatch.setattr(mmw.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mmw.requests, "post", _ok_post)
    monkeypatch.setattr(mmw.requests, "get",
                        _seq_get([_Resp(200, {"status": "failed", "error": "boom"})]))

    fc, area, pt, warnings = mmw.delineate_watershed_mmw(40.0, -83.0, max_wait=10)
    assert fc is None and area is None and pt is None
    assert any("failed" in w.lower() for w in warnings)


def test_poll_timeout_is_graceful(monkeypatch):
    monkeypatch.setenv("MMW_API_KEY", "test-token")
    monkeypatch.setattr(mmw.time, "sleep", lambda *_: None)
    monkeypatch.setattr(mmw.requests, "post", _ok_post)
    monkeypatch.setattr(mmw.requests, "get",
                        lambda *a, **k: _Resp(200, {"status": "started"}))  # never done

    fc, _area, _pt, warnings = mmw.delineate_watershed_mmw(
        40.0, -83.0, max_wait=3, poll_interval=2)
    assert fc is None
    assert any("did not complete" in w for w in warnings)


def test_post_http_error_is_graceful(monkeypatch):
    monkeypatch.setenv("MMW_API_KEY", "test-token")
    monkeypatch.setattr(mmw.requests, "post", lambda *a, **k: _Resp(500, {}))
    monkeypatch.setattr(mmw.requests, "get", _boom)  # must not poll after a failed POST

    fc, area, pt, warnings = mmw.delineate_watershed_mmw(40.0, -83.0)
    assert fc is None and area is None and pt is None
    assert any("HTTP 500" in w for w in warnings)


def test_missing_key_short_circuits(monkeypatch, tmp_path):
    monkeypatch.delenv("MMW_API_KEY", raising=False)
    monkeypatch.setattr(mmw, "_KEY_FILE", str(tmp_path / "absent"))
    monkeypatch.setattr(mmw.requests, "post", _boom)  # must not POST without a key

    fc, area, pt, warnings = mmw.delineate_watershed_mmw(40.0, -83.0)
    assert fc is None and area is None and pt is None
    assert any("not set" in w for w in warnings)


# --- key resolution -------------------------------------------------------- #
def test_api_key_from_file_when_env_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("MMW_API_KEY", raising=False)
    p = tmp_path / "key.txt"
    p.write_text("  filekey123  \n", encoding="utf-8")
    monkeypatch.setattr(mmw, "_KEY_FILE", str(p))
    assert mmw._api_key() == "filekey123"


def test_api_key_env_wins_over_file(monkeypatch, tmp_path):
    p = tmp_path / "key.txt"
    p.write_text("filekey", encoding="utf-8")
    monkeypatch.setattr(mmw, "_KEY_FILE", str(p))
    monkeypatch.setenv("MMW_API_KEY", "envkey")
    assert mmw._api_key() == "envkey"

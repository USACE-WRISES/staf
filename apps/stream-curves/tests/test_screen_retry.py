"""screen_pool retries transient screen failures (D10, 2026-09-02).

NLDI hydrolocation flapped for a night and the NEH screen lost 23 of 71 sites
to ``snap_service_error``; the cache then held the poisoned screen and would
have been reused silently. A retryable failure (an error-severity issue flagged
``retryable``, or a cancelled site) is re-screened on its own, merged back by
site id, and the cache is rewritten. Non-retryable failures (a data gap) are
left alone, and ``screen_retries=0`` is today's single pass.
"""
from __future__ import annotations

import json
from pathlib import Path

from streamcurves import regional_agent as ra

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _site(sid, state="succeeded", code=None, retryable=False):
    s = {"site_id": sid, "state": state, "input": {"lat": 40.0, "lon": -83.0},
         "issues": [], "qualification": {"final": "retained"} if state == "succeeded" else {}}
    if code:
        s["issues"] = [{"code": code, "severity": "error", "retryable": retryable,
                        "message": f"{code} happened"}]
    return s


def _fake_live(script):
    """``script``: one dict per pass, site_id -> (state, code, retryable)."""
    calls: list[list[str]] = []
    passes = iter(script)

    def fake(rows, preset, on_event=None):
        calls.append([r["site_id"] for r in rows])
        spec = next(passes)
        return {"sites": [_site(r["site_id"], *spec.get(r["site_id"], ("succeeded", None, False)))
                          for r in rows],
                "diagnostics": {"elapsed_s": 1.0}}
    return fake, calls


SNAP = ("failed", "snap_service_error", True)
GAP = ("failed", "metric_unavailable", False)
ROWS = [{"site_id": s, "lat": 40.0, "lon": -83.0} for s in ("A", "B", "C", "D")]


def test_retry_rescreens_only_the_retryable_failures_and_merges(tmp_path, monkeypatch):
    fake, calls = _fake_live([{"B": SNAP, "C": SNAP, "D": GAP}, {"C": SNAP}, {}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    events = []
    cache = tmp_path / "screening_cache_functional.json"
    res = ra.screen_pool(ROWS, "functional", cache_path=cache, screen_retries=2,
                         on_event=lambda *a: events.append(a))
    assert calls == [["A", "B", "C", "D"], ["B", "C"], ["C"]]
    states = {r["site_id"]: r["state"] for r in res["sites"]}
    assert states == {"A": "succeeded", "B": "succeeded", "C": "succeeded", "D": "failed"}
    assert [r["site_id"] for r in res["sites"]] == ["A", "B", "C", "D"]   # order kept
    assert res["screen_retry_passes"] == 2 and res["n_recovered"] == 2
    assert res["counts"]["n_failed"] == 1
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert {s["site_id"]: s["state"] for s in cached["sites"]} == states
    assert cached["diagnostics"]["screen_retry_passes"] == 2
    assert cached["diagnostics"]["n_recovered"] == 2
    retry_events = [e for e in events if e and e[0] == "screening_retry"]
    assert [(e[1]["pass"], e[1]["n"], e[1]["recovered"]) for e in retry_events] == [(1, 2, 1), (2, 1, 1)]


def test_retry_zero_is_the_single_pass(tmp_path, monkeypatch):
    fake, calls = _fake_live([{"B": SNAP}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    res = ra.screen_pool(ROWS, "functional", cache_path=tmp_path / "c.json")
    assert calls == [["A", "B", "C", "D"]]
    assert res["screen_retry_passes"] == 0 and res["n_recovered"] == 0
    assert res["counts"]["n_failed"] == 1


def test_a_reused_poisoned_cache_is_retried_and_rewritten(tmp_path, monkeypatch):
    cache = tmp_path / "screening_cache_functional.json"
    fake, calls = _fake_live([{"B": SNAP, "C": SNAP}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    first = ra.screen_pool(ROWS, "functional", cache_path=cache)      # the poisoned screen
    assert first["counts"]["n_failed"] == 2

    fake2, calls2 = _fake_live([{"C": SNAP}])
    monkeypatch.setattr(ra, "_screen_live", fake2)
    second = ra.screen_pool(ROWS, "functional", cache_path=cache, screen_retries=1)
    assert second["from_cache"] is True                # the 2 good sites were reused
    assert calls2 == [["B", "C"]]                      # only the failures were re-screened
    assert second["n_recovered"] == 1
    assert {r["site_id"]: r["state"] for r in second["sites"]} == {
        "A": "succeeded", "B": "succeeded", "C": "failed", "D": "succeeded"}

    fake3, calls3 = _fake_live([{}])
    monkeypatch.setattr(ra, "_screen_live", fake3)
    third = ra.screen_pool(ROWS, "functional", cache_path=cache, screen_retries=1)
    assert calls3 == [["C"]] and third["counts"]["n_failed"] == 0
    fourth = ra.screen_pool(ROWS, "functional", cache_path=cache, screen_retries=1)
    assert fourth["from_cache"] is True and calls3 == [["C"]]   # nothing left to retry


def test_non_retryable_failures_and_partial_sites_are_left_alone(tmp_path, monkeypatch):
    fake, calls = _fake_live([{"B": GAP, "C": ("partial", None, False)}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    res = ra.screen_pool(ROWS, "functional", cache_path=tmp_path / "c.json", screen_retries=2)
    assert calls == [["A", "B", "C", "D"]]
    assert res["screen_retry_passes"] == 0


def test_cancelled_sites_are_retryable(tmp_path, monkeypatch):
    fake, calls = _fake_live([{"B": ("cancelled", "cancelled", False)}, {}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    res = ra.screen_pool(ROWS, "functional", cache_path=tmp_path / "c.json", screen_retries=1)
    assert calls == [["A", "B", "C", "D"], ["B"]]
    assert res["n_recovered"] == 1


def test_retry_wait_is_honored_between_passes(tmp_path, monkeypatch):
    fake, _ = _fake_live([{"B": SNAP}, {}])
    monkeypatch.setattr(ra, "_screen_live", fake)
    slept = []
    monkeypatch.setattr(ra.time, "sleep", lambda s: slept.append(s))
    ra.screen_pool(ROWS, "functional", cache_path=tmp_path / "c.json",
                   screen_retries=1, screen_retry_wait=7.5)
    assert slept == [7.5]


def test_the_retry_flags_reach_run_evidence_from_both_scripts():
    batch = (_SCRIPTS / "run_region_batch.py").read_text(encoding="utf-8")
    assert 'add_argument("--screen-retries"' in batch
    assert 'add_argument("--screen-retry-wait"' in batch
    start = batch.index("ra.run_evidence(")
    window = batch[start:start + 900]
    assert "screen_retries=a.screen_retries" in window
    assert "screen_retry_wait=a.screen_retry_wait" in window
    ns = batch.index("argparse.Namespace(")
    assert "screen_retries=a.screen_retries" in batch[ns:ns + 900]
    analysis = (_SCRIPTS / "run_regional_analysis.py").read_text(encoding="utf-8")
    assert 'add_argument("--screen-retries"' in analysis
    start = analysis.index("ra.run(")
    assert "screen_retries=args.screen_retries" in analysis[start:start + 900]


def test_choose_reference_tier_passes_the_retry_settings(monkeypatch):
    seen = []

    def fake(rows, preset, on_event=None, cache_path=None, *, screen_retries=0,
             screen_retry_wait=0.0):
        seen.append((preset, screen_retries, screen_retry_wait))
        return {"retained_ids": [f"s{i}" for i in range(25)], "counts": {},
                "tables": {}, "sites": [], "preset": preset, "from_cache": False}

    monkeypatch.setattr(ra, "screen_pool", fake)
    ra.choose_reference_tier(ROWS, "functional", screen_retries=3, screen_retry_wait=1.0)
    assert seen == [("functional", 3, 1.0)]

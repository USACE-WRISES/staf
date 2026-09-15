"""E1 tests: batch contracts round-trip + data-dir/cache portability."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from easi.batch import contracts as C
from easi.batch import runtime


def test_site_request_roundtrip_and_key():
    s = C.SiteRequest(site_id="A", lat=40.123456789, lon=-83.987654321,
                      comid=42, reach_length_ft=1000.0,
                      source_choices={"m": "surrogate"}, overrides={"x": "Good"},
                      metadata={"note": "hi"})
    d = s.to_dict()
    s2 = C.SiteRequest.from_dict(d)
    assert s2 == s
    # coordinates normalize to 6 decimals for exact-result reuse
    assert s.key()[0] == 40.123457 and s.key()[1] == -83.987654
    # a different id at the same location + same config shares a key (may share
    # computation); different source choices are part of the key.
    same = C.SiteRequest(site_id="B", lat=40.123456789, lon=-83.987654321,
                         comid=42, source_choices={"m": "surrogate"})
    assert same.key() == s.key()
    diff = C.SiteRequest(site_id="C", lat=40.123456789, lon=-83.987654321, comid=42)
    assert diff.key() != s.key()


def test_batch_request_roundtrip():
    req = C.BatchRequest(
        sites=[C.SiteRequest("A", 40.0, -83.0), C.SiteRequest("B", 41.0, -84.0)],
        config=C.BatchConfig(metric_ids=["m1", "m2"], snap_tolerance_ft=300.0),
        criteria={"op": "and", "rules": []})
    back = C.BatchRequest.from_dict(req.to_dict())
    assert back == req
    assert back.schema_version == C.CONTRACTS_SCHEMA_VERSION


def test_site_and_batch_result_roundtrip():
    site = C.SiteResult(
        site_id="A", state="succeeded",
        delineation=C.DelineationSummary(comid=7, drainage_area_sqkm=12.3),
        metrics=[C.MetricRecord(metric_id="m1", final_rating="Good", index=0.85,
                                function_score=13, status="ok", availability="available")],
        raw_eci=0.734, eci=0.73, raw_sub_indices={"physical": 0.7},
        sub_indices={"physical": 0.7},
        completeness=C.Completeness(total=20, computed=19, unavailable=1),
        issues=[C.Issue(code="nutrients_unavailable", stage="metrics",
                        source="WQP", site_id="A", metric_id="m1")],
        qualification=C.Qualification(auto="qualified", final="retained",
                                      criteria_id="functional"))
    res = C.BatchResult(sites=[site], config=C.BatchConfig(),
                        diagnostics={"elapsed_s": 1.0},
                        generated_ids={"": "SITE-0001"})
    back = C.BatchResult.from_dict(res.to_dict())
    assert back == res
    assert back.sites[0].metrics[0].final_rating == "Good"
    assert back.sites[0].qualification.auto == "qualified"


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("EASI_DATA_DIR", str(tmp_path))
    from easi import config
    importlib.reload(config)
    try:
        assert config.DATA_DIR == Path(str(tmp_path))
    finally:
        monkeypatch.delenv("EASI_DATA_DIR", raising=False)
        importlib.reload(config)


def test_ensure_cache_idempotent(monkeypatch, tmp_path):
    monkeypatch.delenv("HYRIVER_CACHE_NAME", raising=False)
    runtime._configured = False
    p1 = runtime.ensure_cache(cache_dir=tmp_path)
    p2 = runtime.ensure_cache(cache_dir=tmp_path)
    assert p1 == p2 == str(Path(tmp_path) / "easi_hyriver.sqlite")
    assert os.environ["HYRIVER_CACHE_NAME"] == p1


def test_ensure_cache_honors_existing(monkeypatch):
    monkeypatch.setenv("HYRIVER_CACHE_NAME", "/custom/preset.sqlite")
    runtime._configured = False
    got = runtime.ensure_cache()
    assert got == "/custom/preset.sqlite"

"""E2/E4/E5 tests: batch API, scheduler, retry, cancellation, qualification.

Fully offline: the pipeline (delineate_only/assess_only) is stubbed so no network
is touched and the scored reports are deterministic.
"""
from __future__ import annotations

import asyncio

from easi import config, scoring
from easi.batch import api, qualify
from easi.batch import contracts as C


def _report(rating: str) -> dict:
    meta = config.metrics_by_id()
    rows, fscores = [], {}
    for mid, m in meta.items():
        idx = scoring.rating_to_index(rating, m.get("indexMidpoints"))
        fs = scoring.function_score(idx)
        fscores[m["functionId"]] = fs
        rows.append({"metricId": mid, "name": m["name"], "discipline": m["discipline"],
                     "functionId": m["functionId"], "functionName": m["functionName"],
                     "scale": "W", "confidence": "M", "rating": rating,
                     "generatedRating": rating, "index": round(idx, 3),
                     "functionScore": fs, "valueText": "x", "criteria": "",
                     "source": "test", "status": "ok", "note": "", "overrideable": True})
    roll = scoring.rollup(fscores)
    return {"metricRows": rows, "functionScores": roll.function_scores,
            "subIndices": {k: scoring.round2(v) for k, v in roll.sub_indices.items()},
            "ecosystemConditionIndex": scoring.round2(roll.ecosystem_condition_index),
            "computedCount": len(rows), "totalCount": len(rows)}


def _ok_delin(lat, lon, reach_ft, comid=None) -> dict:
    return {"status": "ok", "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft},
            "delineation": {"comid": comid or 111, "gnis_name": "Test Creek",
                            "huc8": "01020304", "huc12": None,
                            "drainage_area_sqkm": 50.0, "snapped_lat": lat,
                            "snapped_lon": lon, "watershed_area_sqkm": 40.0,
                            "reach_length_ft": reach_ft, "warnings": []},
            "watershed_geojson": None, "reach_geojson": None,
            "ctx_inputs": {"lat": lat, "lon": lon, "comid": comid or 111}}


def _stub_pipeline(monkeypatch):
    async def fake_delineate(lat, lon, reach_ft, comid=None):
        if lat < 0:                       # sentinel: unresolvable site
            return {"status": "error", "message": "no NHD stream found",
                    "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}
        return _ok_delin(lat, lon, reach_ft, comid)

    async def fake_assess(ctx_inputs, metric_ids=None, sources=None,
                          overrides=None, progress=None):
        rating = "Good" if ctx_inputs["lat"] >= 40.5 else "Poor"
        return {"status": "ok", "report": _report(rating), "huc12": "010203040506"}

    monkeypatch.setattr(api.pipeline, "delineate_only", fake_delineate)
    monkeypatch.setattr(api.pipeline, "assess_only", fake_assess)


def test_capabilities():
    caps = api.capabilities()
    assert caps["engine_api_version"] >= 1
    assert len(caps["metric_ids"]) == 20
    assert "functional" in caps["criteria_presets"]
    assert "reference_condition" in caps["criteria_presets"]


def test_run_batch_functional_preset(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(sites=[
        C.SiteRequest("HI", 41.0, -83.0),
        C.SiteRequest("", 40.0, -83.0),       # blank id -> SITE-0001
    ])
    res = api.run_batch_sync(req)
    assert [s.site_id for s in res.sites] == ["HI", "SITE-0001"]
    assert res.generated_ids == {"1": "SITE-0001"}
    hi, lo = res.sites
    assert hi.qualification.auto == "qualified" and hi.qualification.final == "retained"
    assert lo.qualification.auto == "excluded"
    assert hi.raw_eci > 0.69 and lo.raw_eci < 0.69
    assert res.diagnostics["succeeded"] == 2 and res.diagnostics["qualified"] == 1
    # compact serialization round-trips (private _artifacts metadata excluded)
    assert C.BatchResult.from_dict(res.to_dict()).to_dict() == res.to_dict()
    assert "_artifacts" not in res.to_dict()["sites"][0]["metadata"]


def test_reference_condition_preset(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(
        sites=[C.SiteRequest("HI", 41.0, -83.0), C.SiteRequest("LO", 40.0, -83.0)],
        criteria="reference_condition")
    res = api.run_batch_sync(req)
    assert res.sites[0].qualification.auto == "qualified"   # all-Good passes all three
    assert res.sites[1].qualification.auto == "excluded"


def test_duplicate_ids_rejected(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(sites=[C.SiteRequest("A", 41.0, -83.0),
                               C.SiteRequest("A", 40.0, -83.0)])
    try:
        api.run_batch_sync(req)
        assert False, "expected duplicate-id rejection"
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()


def test_over_limit_rejected(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(sites=[C.SiteRequest(f"s{i}", 41.0, -83.0 - i * 0.01)
                               for i in range(api.MAX_SITES + 1)])
    try:
        api.run_batch_sync(req)
        assert False, "expected over-limit rejection"
    except ValueError as exc:
        assert "150" in str(exc)


def test_failed_site_isolated_and_retried(monkeypatch):
    calls = {"n": 0}

    async def flaky_delineate(lat, lon, reach_ft, comid=None):
        calls["n"] += 1
        if calls["n"] == 1:                 # first attempt fails transiently
            return {"status": "error", "message": "transient",
                    "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}
        return _ok_delin(lat, lon, reach_ft, comid)

    async def fake_assess(ctx_inputs, **k):
        return {"status": "ok", "report": _report("Good"), "huc12": "x"}

    monkeypatch.setattr(api.pipeline, "delineate_only", flaky_delineate)
    monkeypatch.setattr(api.pipeline, "assess_only", fake_assess)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)

    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 41.0, -83.0)]))
    assert res.diagnostics["retries"] == 1
    assert res.sites[0].state == "succeeded"


def test_permanent_failure_isolated(monkeypatch):
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)
    req = C.BatchRequest(sites=[C.SiteRequest("OK", 41.0, -83.0),
                               C.SiteRequest("BAD", -1.0, -83.0)])   # lat<0 -> always fails
    res = api.run_batch_sync(req)
    ok, bad = res.sites
    assert ok.state == "succeeded"
    assert bad.state == "failed"
    assert bad.issues[0].code == "delineation_failed"


def test_cancellation(monkeypatch):
    _stub_pipeline(monkeypatch)
    req = C.BatchRequest(sites=[C.SiteRequest(f"s{i}", 41.0, -83.0 - i * 0.1)
                               for i in range(5)])
    res = api.run_batch_sync(req, cancel=lambda: True)
    assert all(s.state == "cancelled" for s in res.sites)
    assert res.diagnostics["cancelled"] == 5


# --- qualification unit tests (tri-state) ---------------------------------- #
def _site(eci, subs, fscores, unavailable=0):
    return C.SiteResult(site_id="x", raw_eci=eci, raw_sub_indices=subs,
                        function_scores=fscores,
                        completeness=C.Completeness(unavailable=unavailable))


def test_qualify_empty_rule_not_evaluable():
    s = _site(0.9, {"physical": 0.9}, {"f": 15})
    qualify.qualify_site(s, None)
    assert s.qualification.auto == "not_evaluable"


def test_qualify_skips_unavailable_predicate():
    # sub_index[chemical] missing -> that predicate skips, others decide
    s = _site(0.8, {"physical": 0.8}, {"f": 15})
    rule = {"op": "and", "rules": [
        {"field": "eci", "cmp": ">", "value": 0.69},
        {"field": "sub_index", "key": "chemical", "cmp": ">", "value": 0.69}]}
    qualify.qualify_site(s, rule)
    assert s.qualification.auto == "qualified"          # missing predicate skipped


def test_qualify_partial_evidence_flag():
    s = _site(0.9, {"physical": 0.9}, {"f": 15}, unavailable=3)
    qualify.qualify_site(s, qualify.PRESETS["functional"])
    assert s.qualification.auto == "qualified"
    assert s.qualification.partial_evidence is True


def test_qualify_boundary_exclusive():
    s = _site(0.69, {"physical": 0.69}, {"f": 10})
    qualify.qualify_site(s, qualify.PRESETS["functional"])
    assert s.qualification.auto == "excluded"           # 0.69 is not > 0.69

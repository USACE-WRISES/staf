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
                          overrides=None, prefetch=True, progress=None):
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
    assert "at_risk_or_better" in caps["criteria_presets"]
    assert "all_sites" in caps["criteria_presets"]


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


# --- condition-band presets ------------------------------------------------- #
def _auto(eci, preset):
    s = _site(eci, {"physical": eci}, {"f": 10})
    qualify.qualify_site(s, qualify.PRESETS[preset])
    return s.qualification.auto


def test_band_presets_match_index_band_labels():
    # Each ECI-threshold preset must select exactly one condition category, so the
    # dropdown wording and the retained set can never disagree.
    for eci in (0.20, 0.39, 0.53, 0.69, 0.85, 1.0):
        label = scoring.index_band_label(eci)
        assert (_auto(eci, "functional") == "qualified") is (label == "Functioning")
        assert (_auto(eci, "at_risk_or_better") == "qualified") is (
            label in ("Functioning", "Functioning-at-Risk"))


def test_at_risk_or_better_boundary_exclusive():
    assert _auto(0.39, "at_risk_or_better") == "excluded"   # 0.39 is not > 0.39
    assert _auto(0.40, "at_risk_or_better") == "qualified"


def test_all_sites_retains_every_scored_site():
    for eci in (0.0, 0.20, 0.69, 1.0):
        assert _auto(eci, "all_sites") == "qualified"


def test_all_sites_leaves_unscored_site_not_evaluable():
    # A site that never scored has no ECI: it must stay pending, never be retained
    # on absent evidence.
    s = _site(None, {}, {})
    qualify.qualify_site(s, qualify.PRESETS["all_sites"])
    assert s.qualification.auto == "not_evaluable"
    assert s.qualification.final == "pending"


def test_run_batch_all_sites_preset(monkeypatch):
    _stub_pipeline(monkeypatch)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)
    req = C.BatchRequest(
        sites=[C.SiteRequest("HI", 41.0, -83.0),      # Good
               C.SiteRequest("LO", 40.0, -83.0),      # Poor: retained anyway
               C.SiteRequest("BAD", -1.0, -83.0)],    # lat<0 -> delineation fails
        criteria="all_sites")
    res = api.run_batch_sync(req)
    hi, lo, bad = res.sites
    assert hi.qualification.final == "retained"
    assert lo.qualification.final == "retained"
    assert bad.state == "failed"
    assert bad.qualification.final == "pending"


def test_import_error_is_not_retried(monkeypatch):
    # A missing geospatial dep is a deployment fault, not a transient outage, so
    # the run must fail once and report the real cause rather than retry blindly.
    # The real delineate_only runs here; only the geo call underneath is stubbed.
    def missing_dep(*a, **k):
        raise ModuleNotFoundError("No module named 'pynhd'")

    monkeypatch.setattr("easi.delineation.run_delineation", missing_dep)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)

    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 41.0, -83.0)]))
    issue = res.sites[0].issues[0]
    assert res.sites[0].state == "failed"
    assert issue.code == "engine_dependency_missing"
    assert issue.retryable is False
    assert res.diagnostics["retries"] == 0
    assert "pynhd" in issue.message


def _stub_delineation(monkeypatch, *, snap_error=None):
    class _NoComid:
        comid = None
    _NoComid.snap_error = snap_error
    monkeypatch.setattr("easi.delineation.run_delineation",
                        lambda *a, **k: _NoComid())
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)


def test_no_stream_found_not_retried(monkeypatch):
    # An off-network point is a permanent fact about the geometry, not an outage.
    _stub_delineation(monkeypatch)
    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 41.0, -83.0)]))
    issue = res.sites[0].issues[0]
    assert res.sites[0].state == "failed"
    assert issue.code == "no_stream_found"
    assert issue.retryable is False
    assert res.diagnostics["retries"] == 0


def test_snap_service_error_is_retried_and_not_called_no_stream(monkeypatch):
    # A failing snap service must never be reported as "no stream near this
    # point": that invents a fact about the geometry from an outage.
    _stub_delineation(monkeypatch, snap_error="hydrolocation: 502 Bad Gateway")
    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 41.0, -83.0)]))
    issue = res.sites[0].issues[0]
    assert issue.code == "snap_service_error"
    assert issue.retryable is True
    assert "502" in issue.message
    assert "No NHD stream found" not in issue.message
    assert res.diagnostics["retries"] == 1        # retried once, unlike no_stream_found


# --- snap fallback --------------------------------------------------------- #
class _FakeFrame:
    """Minimal stand-in for the GeoDataFrame pynhd returns."""
    def __init__(self, rows, columns):
        self._rows, self.columns = rows, columns

    def __len__(self):
        return len(self._rows)

    @property
    def iloc(self):
        return self._rows


def _nldi_stub(monkeypatch, *, hydrolocation, position):
    """Patch pynhd.NLDI so snap_point sees scripted endpoint behaviour."""
    import sys
    import types

    class _NLDI:
        def comid_byloc(self, coords):
            if isinstance(hydrolocation, Exception):
                raise hydrolocation
            return hydrolocation

        def feature_byloc(self, coords):
            if isinstance(position, Exception):
                raise position
            return position

    monkeypatch.setitem(sys.modules, "pynhd", types.SimpleNamespace(NLDI=_NLDI))
    # flowline_attrs makes its own network calls; keep the test offline.
    monkeypatch.setattr("easi.delineation.flowline_attrs",
                        lambda comid: {"gnis_name": "Wabash River"})


def test_snap_falls_back_to_position_endpoint(monkeypatch):
    # The real failure: hydrolocation 502s, pynhd mistakes the error document for
    # data and raises, and comid/position answers correctly for the same point.
    from easi import delineation
    _nldi_stub(monkeypatch,
               hydrolocation=AttributeError("The CRS attribute of a GeoDataFrame..."),
               position=_FakeFrame([{"comid": 18509814}], ["geometry", "comid"]))
    out = delineation.snap_point(40.319653, -84.630337)
    assert out["comid"] == 18509814
    assert out["gnis_name"] == "Wabash River"
    assert out.get("_snap_error") is None


def test_snap_reports_service_error_when_both_endpoints_fail(monkeypatch):
    from easi import delineation
    _nldi_stub(monkeypatch, hydrolocation=RuntimeError("502 Bad Gateway"),
               position=RuntimeError("502 Bad Gateway"))
    out = delineation.snap_point(40.319653, -84.630337)
    assert out["comid"] is None
    assert "502" in out["_snap_error"]


def test_snap_empty_result_is_not_a_service_error(monkeypatch):
    # Both endpoints answered and found nothing: that IS "no stream here", and
    # must stay distinguishable from an outage.
    from easi import delineation
    _nldi_stub(monkeypatch, hydrolocation=_FakeFrame([], []),
               position=_FakeFrame([], []))
    out = delineation.snap_point(0.0, 0.0)
    assert out["comid"] is None
    assert out.get("_snap_error") is None

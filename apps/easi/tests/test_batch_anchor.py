"""Batch mode carries the siteAnchor: the result field, summary.csv routing
columns, the refusal failure state, and the published policy in capabilities.
Fully offline (pipeline stubbed)."""
from __future__ import annotations

import csv
import io

from easi import routing, scoring
from easi.batch import api, exports
from easi.batch import contracts as C


def _hr_anchor(declined=False) -> dict:
    return {
        "anchorSchemaVersion": 1, "anchorKind": "hrSurrogate",
        "clickedPoint": {"lat": 40.0962, "lon": -83.0203},
        "clickedStream": {"network": "nhdplus-hr", "nhdplusId": 24000800021917,
                          "gnisName": "Little Trib", "reachcode": "05060001001737",
                          "drainageAreaSqkm": 2.72, "slope": 0.0177,
                          "fcode": 46003, "streamOrder": 1, "vpuid": "0506",
                          "snapLat": 40.0958, "snapLon": -83.0201,
                          "snapDistFt": 42.0},
        "scoredReach": {"network": "nhdplus-v2", "comid": 5215053,
                        "gnisName": "Rush Run", "drainageAreaSqkm": 14.9,
                        "snapLat": 40.0953, "snapLon": -83.0199,
                        "snapDistFt": None},
        "routing": {"method": "nldi-hydrolocation-raindrop",
                    "routedDistanceFt": 291.4, "daRatio": 5.48,
                    "daRatioLimit": routing.DA_RATIO_MAX, "declined": declined},
        "notes": [],
    }


def _report() -> dict:
    roll = scoring.rollup({"catchment-hydrology": 3})
    return {"metricRows": [], "functionScores": {"catchment-hydrology": 3},
            "subIndices": {k: scoring.round2(v) for k, v in roll.sub_indices.items()},
            "ecosystemConditionIndex": scoring.round2(roll.ecosystem_condition_index),
            "computedCount": 0, "totalCount": 0}


def _stub_pipeline(monkeypatch, anchors: dict):
    """Fake delineate/assess; ``anchors`` maps rounded lat -> siteAnchor|None."""
    async def fake_delineate(lat, lon, reach_ft, comid=None, **kw):
        anchor = anchors.get(round(lat, 1))
        if isinstance(anchor, dict) and anchor.get("_refuse"):
            return {"status": "error", "code": "surrogate_da_ratio_exceeded",
                    "retryable": False,
                    "message": "EASI can't score this stream. limit 10.",
                    "anchor": anchor["anchor"],
                    "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft}}
        out = {"status": "ok",
               "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft},
               "delineation": {"comid": 111, "gnis_name": "Test Creek",
                               "huc8": "01020304", "huc12": None,
                               "drainage_area_sqkm": 50.0, "snapped_lat": lat,
                               "snapped_lon": lon, "watershed_area_sqkm": 40.0,
                               "reach_length_ft": reach_ft, "warnings": []},
               "watershed_geojson": None, "reach_geojson": None,
               "ctx_inputs": {"lat": lat, "lon": lon, "comid": 111}}
        if anchor:
            out["siteAnchor"] = anchor
        return out

    async def fake_assess(ctx_inputs, **k):
        return {"status": "ok", "report": _report(), "huc12": "x"}

    monkeypatch.setattr(api.pipeline, "delineate_only", fake_delineate)
    monkeypatch.setattr(api.pipeline, "assess_only", fake_assess)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)


def test_anchor_rides_result_and_roundtrips(monkeypatch):
    _stub_pipeline(monkeypatch, {40.1: _hr_anchor(), 41.0: None})
    res = api.run_batch_sync(C.BatchRequest(sites=[
        C.SiteRequest("HR", 40.1, -83.02), C.SiteRequest("V2", 41.0, -83.0)]))
    by_id = {s.site_id: s for s in res.sites}
    assert by_id["HR"].anchor["anchorKind"] == "hrSurrogate"
    assert by_id["V2"].anchor == {}
    back = C.BatchResult.from_dict(res.to_dict())
    assert {s.site_id: s.anchor for s in back.sites} == \
        {s.site_id: s.anchor for s in res.sites}


def test_summary_csv_routing_columns(monkeypatch):
    _stub_pipeline(monkeypatch, {40.1: _hr_anchor(), 41.0: None})
    res = api.run_batch_sync(C.BatchRequest(sites=[
        C.SiteRequest("HR", 40.1, -83.02), C.SiteRequest("V2", 41.0, -83.0)]))
    rows = list(csv.reader(io.StringIO(exports._summary_csv(res))))
    header = rows[0]
    for col in ("anchor_kind", "clicked_stream", "clicked_nhdplusid",
                "clicked_da_sqkm", "routed_distance_ft", "da_ratio",
                "da_ratio_limit"):
        assert col in header
    data = {r[0]: dict(zip(header, r)) for r in rows[1:]}
    assert data["HR"]["anchor_kind"] == "hrSurrogate"
    assert data["HR"]["clicked_stream"] == "Little Trib"
    assert data["HR"]["clicked_nhdplusid"] == "24000800021917"
    assert data["HR"]["da_ratio"] == "5.48"
    assert data["V2"]["anchor_kind"] == ""
    assert data["V2"]["clicked_stream"] == ""


def test_refusal_fails_without_retry(monkeypatch):
    _stub_pipeline(monkeypatch,
                   {40.1: {"_refuse": True, "anchor": _hr_anchor(declined=True)}})
    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 40.1, -83.0)]))
    site = res.sites[0]
    assert site.state == "failed"
    issue = site.issues[0]
    assert issue.code == "surrogate_da_ratio_exceeded"
    assert issue.stage == "snap"
    assert issue.retryable is False
    assert res.diagnostics["retries"] == 0
    assert site.anchor["routing"]["declined"] is True
    # the refusal reason lands in exclusions.csv
    assert "limit 10" in exports._exclusions_csv(res)


def test_capabilities_publish_the_policy():
    caps = api.capabilities()
    assert caps["defaults"]["da_ratio_max"] == routing.DA_RATIO_MAX
    assert caps["defaults"]["snap_tolerance_ft"] == routing.HR_SNAP_TOL_FT
    assert caps["defaults"]["watershed_engine"] == "auto"
    assert caps["watershed_engine_options"] == ["auto", "streamcat-legacy"]
    from easi._vendor.site_engine import ENGINE_VERSION
    assert caps["site_engine_version"] == ENGINE_VERSION      # whatever is vendored


def test_batch_config_policy_round_trip():
    assert C.BatchConfig.from_dict({}).watershed_engine == "auto"
    legacy = C.BatchConfig(watershed_engine="streamcat-legacy")
    assert C.BatchConfig.from_dict(legacy.to_dict()).watershed_engine == "streamcat-legacy"
    import pytest
    with pytest.raises(ValueError):
        C.BatchConfig(watershed_engine="nope")
    with pytest.raises(ValueError):
        C.BatchConfig.from_dict({"watershed_engine": "nope"})


def test_policy_reaches_delineate(monkeypatch):
    seen: list = []

    async def fake_delineate(lat, lon, reach_ft, comid=None, **kw):
        seen.append(kw.get("watershed_engine"))
        return {"status": "error", "code": "no_stream_found", "retryable": False,
                "message": "none", "input": {}}
    monkeypatch.setattr(api.pipeline, "delineate_only", fake_delineate)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)
    api.run_batch_sync(C.BatchRequest(
        sites=[C.SiteRequest("A", 40.1, -83.0)],
        config=C.BatchConfig(watershed_engine="streamcat-legacy")))
    api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("B", 40.1, -83.0)]))
    assert seen == ["streamcat-legacy", "auto"]


def _stub_engine_pipeline(monkeypatch, *, declined: bool):
    anchor = _hr_anchor(declined=declined)
    anchor["routing"]["declineMessage"] = "past the limit"

    async def fake_delineate(lat, lon, reach_ft, comid=None, **kw):
        return {"status": "ok",
                "input": {"lat": lat, "lon": lon, "reach_length_ft": reach_ft},
                "siteAnchor": anchor,
                "delineation": {"comid": 5215053, "gnis_name": "Little Trib",
                                "huc8": "05060001", "huc12": None,
                                "drainage_area_sqkm": 2.72, "snapped_lat": lat,
                                "snapped_lon": lon, "watershed_area_sqkm": 2.61,
                                "watershed_source": "site-engine",
                                "watershed_engine": {
                                    "engine": "site-engine", "engineVersion": "0.2.0",
                                    "status": "ok", "reason": None, "nReaches": 7,
                                    "nHops": 2, "areaSqkm": 2.61,
                                    "vaaAreaSqkm": 2.72, "areaAgreement": 0.96},
                                "reach_length_ft": reach_ft, "warnings": []},
                "watershed_geojson": None, "reach_geojson": None,
                "ctx_inputs": {"lat": lat, "lon": lon, "comid": 5215053}}

    async def fake_assess(ctx_inputs, **k):
        rep = _report()
        rep["metricRows"] = [
            {"metricId": "catchment-hydrology-impervious-surface-cover",
             "name": "Impervious", "discipline": "Hydrology",
             "functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
             "rating": "Good", "index": 0.85, "functionScore": 13, "status": "ok",
             "engine": "site-engine", "anchorLabel": "exact watershed (STAF site engine)"},
            {"metricId": "low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity",
             "name": "Low Flow", "discipline": "Hydraulics",
             "functionId": "low-flow", "functionName": "Low flow",
             "rating": None, "index": None, "functionScore": None,
             "status": "unavailable", "note": "past the limit",
             "engine": "unavailable",
             "anchorLabel": "unavailable past the substitution limit"}]
        return {"status": "ok", "report": rep, "huc12": "x"}
    monkeypatch.setattr(api.pipeline, "delineate_only", fake_delineate)
    monkeypatch.setattr(api.pipeline, "assess_only", fake_assess)
    monkeypatch.setattr("easi.batch.runner._RETRY_BACKOFF_S", 0.0)


def test_declined_under_auto_is_partial_not_failed(monkeypatch):
    _stub_engine_pipeline(monkeypatch, declined=True)
    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("A", 40.1, -83.0)]))
    site = res.sites[0]
    assert site.state == "partial"
    assert not [i for i in site.issues if i.severity == "error"]
    assert site.anchor["routing"]["declined"] is True
    assert site.watershed_engine["status"] == "ok"
    assert site.delineation.watershed_source == "site-engine"
    back = C.BatchResult.from_dict(res.to_dict())
    assert back.sites[0].watershed_engine == site.watershed_engine
    assert back.sites[0].delineation.watershed_source == "site-engine"
    engines = {m.metric_id: m.engine for m in back.sites[0].metrics}
    assert engines["catchment-hydrology-impervious-surface-cover"] == "site-engine"


def test_summary_csv_engine_columns(monkeypatch):
    _stub_engine_pipeline(monkeypatch, declined=False)
    res = api.run_batch_sync(C.BatchRequest(sites=[C.SiteRequest("HR", 40.1, -83.02)]))
    rows = list(csv.reader(io.StringIO(exports._summary_csv(res))))
    header = rows[0]
    for col in ("watershed_engine", "engine_status", "engine_version",
                "engine_reaches", "engine_hops", "engine_area_sqkm", "comid_evidence"):
        assert col in header
    data = dict(zip(header, rows[1]))
    assert data["watershed_engine"] == "site-engine"
    assert data["engine_status"] == "ok" and data["engine_version"] == "0.2.0"
    assert data["engine_reaches"] == "7" and data["engine_area_sqkm"] == "2.61"
    assert data["comid_evidence"] == "nearest covered reach"
    metrics = list(csv.reader(io.StringIO(exports._metrics_csv(res))))
    assert "engine" in metrics[0]
    engine_col = metrics[0].index("engine")
    assert {r[engine_col] for r in metrics[1:]} == {"site-engine", "unavailable"}

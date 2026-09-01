"""delineate_only under the auto policy: the STAF site engine computes the
exact watershed for a routed site and replaces the surrogate basin, an engine
failure draws no proxy, a declined routing still completes, the legacy policy
and covered clicks never run the engine. All offline (engine stubbed)."""
from __future__ import annotations

import asyncio

from easi import pipeline, routing, watershed
from test_pipeline_anchor import _fake_delin, _hr_anchor

_POLY = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[
        [-83.03, 40.09], [-83.01, 40.09], [-83.01, 40.11], [-83.03, 40.11],
        [-83.03, 40.09]]]}}]}


def _engine_ok(anchor, *, progress=None):
    if progress is not None:
        progress({"stage": "walk", "hops": 2, "reaches": 7})
    return {"engine": "site-engine", "engineVersion": "0.2.0", "status": "ok",
            "reason": None, "nReaches": 7, "nHops": 2, "areaSqkm": 2.61,
            "vaaAreaSqkm": 2.72, "areaAgreement": 0.96,
            "record": {"engineVersion": "0.2.0",
                       "metrics": {"imperviousPctWatershed": {"value": 3.0}}},
            "polygon": _POLY}


def _engine_refused(anchor, *, progress=None):
    return {"engine": "site-engine", "engineVersion": "0.2.0", "status": "refused",
            "reason": "watershed exceeds the engine budget (61 reaches, 12 hops)",
            "nReaches": 61, "nHops": 12, "areaSqkm": None, "vaaAreaSqkm": 2.72,
            "areaAgreement": None, "record": None, "polygon": None}


def _run(monkeypatch, engine, *, policy="auto", anchor=None):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))
    monkeypatch.setattr("easi.routing.resolve_anchor",
                        lambda lat, lon, **k: {"anchor": anchor or _hr_anchor()})
    monkeypatch.setattr("easi.routing.reanchor_inputs",
                        lambda a, ft: {"lat": 40.0958, "lon": -83.0201,
                                       "drainage_area_sqkm": 2.72,
                                       "reach_geojson": None,
                                       "reach_length_ft": 954.0, "_warnings": []})
    monkeypatch.setattr(watershed, "compute_exact_watershed", engine)
    progress: dict = {}
    res = asyncio.run(pipeline.delineate_only(
        40.0962, -83.0203, 1000.0, watershed_engine=policy, progress=progress))
    return res, captured, progress


def test_auto_runs_the_engine_and_swaps_the_watershed(monkeypatch):
    res, captured, progress = _run(monkeypatch, _engine_ok)
    assert res["status"] == "ok"
    assert captured["comid"] == 5215053           # COMID-keyed evidence stays keyed
    ci = res["ctx_inputs"]
    assert ci["watershed_geojson"] is _POLY and res["watershed_geojson"] is _POLY
    assert ci["watershedEngine"]["status"] == "ok"
    assert "polygon" not in ci["watershedEngine"]
    d = res["delineation"]
    assert d["watershed_source"] == "site-engine"
    assert d["watershed_area_sqkm"] == 2.61
    assert d["drainage_area_sqkm"] == 2.72          # the clicked stream's
    assert d["gnis_name"] == "(unnamed stream)"     # the clicked stream, not the surrogate
    assert d["comid"] == 5215053
    assert d["watershed_engine"]["nReaches"] == 7 and "record" not in d["watershed_engine"]
    assert progress["stage"] == "walk" and progress["reaches"] == 7
    ctx = pipeline._ctx_from_inputs(ci)
    assert ctx.extras["watershedEngine"]["status"] == "ok"


def test_engine_failure_draws_no_proxy(monkeypatch):
    res, _c, _p = _run(monkeypatch, _engine_refused)
    assert res["status"] == "ok"
    assert res["watershed_geojson"] is None
    assert res["ctx_inputs"]["watershed_geojson"] is None
    d = res["delineation"]
    assert d["watershed_source"] == "not-calculated"
    assert d["watershed_area_sqkm"] is None
    assert any("SFARI or DEEP" in w and "61 reaches" in w for w in d["warnings"])
    assert res["ctx_inputs"]["watershedEngine"]["status"] == "refused"


def test_declined_routing_still_completes(monkeypatch):
    declined = _hr_anchor(declined=True, daRatio=37.2,
                          declineCode="surrogate_da_ratio_exceeded",
                          declineMessage="past the limit")
    res, captured, _p = _run(monkeypatch, _engine_ok, anchor=declined)
    assert res["status"] == "ok"
    assert res["siteAnchor"]["routing"]["declined"] is True
    assert res["delineation"]["watershed_source"] == "site-engine"
    assert captured["comid"] == 5215053


def test_legacy_policy_never_calls_the_engine(monkeypatch):
    def boom(anchor, *, progress=None):
        raise AssertionError("legacy must not run the engine")
    res, _c, _p = _run(monkeypatch, boom, policy=routing.POLICY_STREAMCAT_LEGACY)
    assert res["status"] == "ok"
    assert res["delineation"]["watershed_source"] == "nhdplus-v2-basin"
    assert res["delineation"]["gnis_name"] == "Test Creek"      # the surrogate
    assert "watershedEngine" not in res["ctx_inputs"]


def test_covered_click_never_calls_the_engine(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))

    def boom(anchor, *, progress=None):
        raise AssertionError("covered clicks must not run the engine")
    monkeypatch.setattr(watershed, "compute_exact_watershed", boom)
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0, comid=123))
    assert res["status"] == "ok"
    assert res["delineation"]["watershed_source"] == "nhdplus-v2-basin"
    assert res["delineation"]["watershed_engine"] is None


def test_unknown_policy_is_a_request_error():
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0,
                                              watershed_engine="nope"))
    assert res["status"] == "error" and res["code"] == "invalid_request"

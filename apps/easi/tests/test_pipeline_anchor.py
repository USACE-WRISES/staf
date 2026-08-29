"""delineate_only threads the siteAnchor payload and classifies routing outcomes.

Covers: the synthesized v2Direct anchor for direct-COMID calls, pass-through +
enrichment of a UI-resolved anchor, the surrogate comid driving delineation,
the structured refusal error, outage/no-stream code mapping, and the ctx.extras
threading. All offline (run_delineation and routing.resolve_anchor stubbed).
"""
from __future__ import annotations

import asyncio

from easi import pipeline, routing
from easi.delineation import Delineation


def _fake_delin(captured: dict):
    def fake(lat, lon, length_ft, *, comid=None, snapped_lat=None,
             snapped_lon=None):
        captured.update({"comid": comid, "snapped_lat": snapped_lat,
                         "snapped_lon": snapped_lon})
        return Delineation(
            lat=lat, lon=lon, comid=comid, gnis_name="Test Creek",
            drainage_area_sqkm=5.0, huc8="05060001", slope=0.002, fcode=46006,
            stream_order=2, sinuosity=1.1, snapped_lat=snapped_lat,
            snapped_lon=snapped_lon, watershed_geojson=None,
            watershed_area_sqkm=4.0, reach_geojson=None, reach_length_ft=1000.0)
    return fake


def _hr_anchor(**routing_over) -> dict:
    r = {"method": "nldi-hydrolocation-raindrop", "routedDistanceFt": 291.4,
         "daRatio": 5.48, "daRatioLimit": routing.DA_RATIO_MAX,
         "declined": False}
    r.update(routing_over)
    return {
        "anchorSchemaVersion": 1, "anchorKind": "hrSurrogate",
        "clickedPoint": {"lat": 40.0962, "lon": -83.0203},
        "clickedStream": {"network": "nhdplus-hr", "nhdplusId": 24000800021917,
                          "gnisName": None, "reachcode": "05060001001737",
                          "drainageAreaSqkm": 2.72, "slope": 0.0177,
                          "fcode": 46003, "streamOrder": 1, "vpuid": "0506",
                          "snapLat": 40.0958, "snapLon": -83.0201,
                          "snapDistFt": 42.0},
        "scoredReach": {"network": "nhdplus-v2", "comid": 5215053,
                        "gnisName": None, "drainageAreaSqkm": None,
                        "snapLat": 40.0953, "snapLon": -83.0199,
                        "snapDistFt": None},
        "routing": r, "notes": [],
    }


def test_direct_comid_synthesizes_v2direct_anchor(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0, comid=123))
    assert res["status"] == "ok"
    anchor = res["siteAnchor"]
    assert anchor["anchorKind"] == "v2Direct"
    assert anchor["scoredReach"]["comid"] == 123
    # enriched from the delineation
    assert anchor["scoredReach"]["gnisName"] == "Test Creek"
    assert anchor["scoredReach"]["drainageAreaSqkm"] == 5.0
    assert res["ctx_inputs"]["siteAnchor"] is anchor
    assert captured["comid"] == 123


def test_ui_anchor_passes_through_and_enriches(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))
    ui_anchor = routing.v2_anchor(123, 40.0, -83.0, 40.0001, -83.0001, 12.0)
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0, comid=123,
                                              anchor=ui_anchor))
    anchor = res["siteAnchor"]
    assert anchor is ui_anchor                            # same payload, enriched
    assert anchor["scoredReach"]["snapDistFt"] == 12.0    # UI detail preserved
    assert anchor["scoredReach"]["gnisName"] == "Test Creek"


def test_resolved_hr_anchor_drives_surrogate_delineation(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))
    monkeypatch.setattr("easi.routing.resolve_anchor",
                        lambda lat, lon, **k: {"anchor": _hr_anchor()})
    res = asyncio.run(pipeline.delineate_only(40.0962, -83.0203, 1000.0))
    assert captured["comid"] == 5215053                   # the surrogate
    assert captured["snapped_lat"] == 40.0953             # the V2 snap point
    anchor = res["siteAnchor"]
    assert anchor["anchorKind"] == "hrSurrogate"
    assert anchor["scoredReach"]["gnisName"] == "Test Creek"   # enriched
    assert res["ctx_inputs"]["siteAnchor"]["clickedStream"]["nhdplusId"] \
        == 24000800021917


def test_refusal_is_structured_error(monkeypatch):
    def no_delin(*a, **k):
        raise AssertionError("a refused site must never delineate")
    monkeypatch.setattr("easi.delineation.run_delineation", no_delin)
    declined = _hr_anchor(declined=True, daRatio=37.2)
    monkeypatch.setattr(
        "easi.routing.resolve_anchor",
        lambda lat, lon, **k: {"refused": True,
                               "code": "surrogate_da_ratio_exceeded",
                               "message": "EASI can't score this stream. limit 10.",
                               "anchor": declined})
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0))
    assert res["status"] == "error"
    assert res["code"] == "surrogate_da_ratio_exceeded"
    assert res["retryable"] is False
    assert res["anchor"]["routing"]["daRatio"] == 37.2


def test_error_code_mapping(monkeypatch):
    monkeypatch.setattr(
        "easi.routing.resolve_anchor",
        lambda lat, lon, **k: {"error": "snap_service_error", "detail": "502"})
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0))
    assert res["code"] == "snap_service_error" and res["retryable"] is True
    assert "502" in res["message"]

    monkeypatch.setattr("easi.routing.resolve_anchor",
                        lambda lat, lon, **k: {"error": "no_stream_found"})
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0))
    assert res["code"] == "no_stream_found" and res["retryable"] is False


def test_dependency_fault_is_not_an_outage(monkeypatch):
    def missing(*a, **k):
        raise ModuleNotFoundError("No module named 'pynhd'")
    monkeypatch.setattr("easi.routing.resolve_anchor", missing)
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0))
    assert res["code"] == "engine_dependency_missing"
    assert res["retryable"] is False


def test_hr_reanchor_splits_the_ctx(monkeypatch):
    # Phase 2: the surrogate drives COMID-keyed inputs while the clicked HR
    # stream drives point + reach-scale inputs. Asserted field by field.
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))
    monkeypatch.setattr("easi.routing.resolve_anchor",
                        lambda lat, lon, **k: {"anchor": _hr_anchor()})
    hr_reach = {"type": "FeatureCollection", "features": [{"hr": True}]}
    monkeypatch.setattr(
        "easi.routing.reanchor_inputs",
        lambda anchor, ft: {"lat": 40.0958, "lon": -83.0201, "slope": 0.0177,
                            "sinuosity": 1.31, "fcode": 46003, "stream_order": 1,
                            "drainage_area_sqkm": 2.72, "huc8": "05060001",
                            "reach_geojson": hr_reach, "reach_length_ft": 954.0,
                            "_warnings": ["hr reach note"]})
    res = asyncio.run(pipeline.delineate_only(40.0962, -83.0203, 1000.0))
    ci = res["ctx_inputs"]
    assert ci["comid"] == 5215053                     # surrogate (StreamCat/NRSA)
    assert ci["watershed_geojson"] is None            # surrogate basin (fake None)
    assert ci["lat"] == 40.0958 and ci["lon"] == -83.0201   # true clicked point
    assert ci["slope"] == 0.0177 and ci["sinuosity"] == 1.31  # HR VAA/geometry
    assert ci["drainage_area_sqkm"] == 2.72           # HR totdasqkm (Bieger/3DEP)
    assert ci["reach_geojson"] is hr_reach            # HR-trimmed reach
    assert res["reach_geojson"] is hr_reach           # map draws the HR reach
    assert res["delineation"]["reach_length_ft"] == 954.0
    assert "hr reach note" in res["delineation"]["warnings"]
    # the scored-reach identity stays the surrogate's
    assert res["delineation"]["comid"] == 5215053
    assert res["delineation"]["drainage_area_sqkm"] == 5.0   # fake surrogate DA


def test_covered_run_never_touches_reanchor(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("easi.delineation.run_delineation", _fake_delin(captured))

    def boom(*a, **k):
        raise AssertionError("reanchor must not run for covered clicks")
    monkeypatch.setattr("easi.routing.reanchor_inputs", boom)
    res = asyncio.run(pipeline.delineate_only(40.0, -83.0, 1000.0, comid=123))
    assert res["status"] == "ok"


def test_ctx_extras_carries_the_anchor():
    ctx = pipeline._ctx_from_inputs(
        {"lat": 40.0, "lon": -83.0, "comid": 1,
         "siteAnchor": {"anchorKind": "v2Direct"}})
    assert ctx.extras["siteAnchor"]["anchorKind"] == "v2Direct"

    ctx = pipeline._ctx_from_inputs({"lat": 40.0, "lon": -83.0, "comid": 1})
    assert "siteAnchor" not in ctx.extras

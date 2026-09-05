"""Engine-only evidence (2026-09-05).

Every watershed metric reads the STAF site engine's exact-watershed value
(``origin="engine"``). While the engine runs the entry is pending; when it
failed or refused the entry is unavailable and says why; nothing is ever
substituted from a neighboring NHDPlus V2 reach. Injects ``ctx.extras``; no
network.
"""
from __future__ import annotations

import asyncio

import pytest

from sfari import engine_prefill, evidence
from sfari.metrics.base import AnalysisContext

ENGINE_VALUES = dict(
    imperviousPctWatershed=12.3, cropPctWatershed=20.0, hayPasturePctWatershed=5.0,
    roadDensity=1.59, damCount=2, damStorageAcreFt=150.0, damStoragePerSqkm=3.2,
    woodyWetlandPctWatershed=1.5, herbWetlandPctWatershed=0.5,
    forestPctRiparian=42.0, shrubPctRiparian=3.0, grasslandPctRiparian=5.0,
    woodyWetlandPctRiparian=2.0, herbWetlandPctRiparian=1.0, soilKFactor=0.28,
    imperviousPct2001Watershed=10.3)


def _rec(**vals):
    return {"status": "ok", "engineVersion": "0.3.0",
            "watershed": {"areaSqkm": 12.5, "areaAgreement": 1.0, "nReaches": 7},
            "metrics": {k: {"value": v} for k, v in vals.items()}}


OK = {"status": "ok", "record": _rec(**ENGINE_VALUES), "reason": None}
FAILED = {"status": "failed", "record": None, "reason": "no stream"}
REFUSED = {"status": "refused", "record": None, "reason": "over budget"}
RUNNING = {"status": "running"}


def _ctx(engine=None, slope=None, flow=None):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=None, slope=slope)
    state = engine if engine is not None else {"status": "idle"}
    c.extras["engine"] = state
    c.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    if flow is not None:
        c.extras["flow"] = flow
    return c


ENGINE_FIRST = [
    ("catchment-hydrology-impervious-surface-area", evidence.ev_impervious, 12.3),
    ("catchment-hydrology-road-density", evidence.ev_road_density, 1.59),
    ("catchment-hydrology-impoundments", evidence.ev_impoundments, 2),
    ("surface-water-storage-wetland-coverage", evidence.ev_wetland, 2.0),
    ("light-thermal-regime-riparian-canopy-cover", evidence.ev_canopy, 42.0),
    ("carbon-processing-riparian-corridor-width-and-quality", evidence.ev_corridor, 53.0),
    ("nutrient-cycling-vegetated-riparian-corridor-width", evidence.ev_veg_corridor, 53.0),
    ("community-dynamics-riparian-communities", evidence.ev_riparian_communities, 53.0),
    ("catchment-hydrology-land-use-change", evidence.ev_land_use_change, 2.0),
]


@pytest.mark.parametrize("mid,fn,value", ENGINE_FIRST)
def test_engine_value_is_the_value(mid, fn, value):
    r = fn(_ctx(engine=OK))
    assert r.metric_id == mid and r.status == "ok" and r.origin == "engine"
    assert r.value == value
    assert r.source == "STAF site engine v0.3.0 (exact watershed)"
    assert r.engine_version == "0.3.0"
    assert "12.5 km2" in r.note
    assert r.anchor_label == "" and r.fallback_reason == "" and not r.upgrade_pending


@pytest.mark.parametrize("mid,fn,_value", ENGINE_FIRST)
def test_pending_while_the_engine_runs(mid, fn, _value):
    r = fn(_ctx(engine=RUNNING))
    assert r.status == "pending" and r.origin == "engine"
    assert "running" in r.note


@pytest.mark.parametrize("state,reason", [(FAILED, "failed: no stream"),
                                          (REFUSED, "refused: over budget")])
def test_unavailable_with_the_reason_when_the_engine_did_not_answer(state, reason):
    for _mid, fn, _v in ENGINE_FIRST:
        r = fn(_ctx(engine=state))
        assert r.status == "unavailable" and r.origin == "engine"
        assert reason in r.note and "StreamCat" not in r.note
    idle = evidence.ev_impervious(_ctx())
    assert idle.status == "unavailable" and "has not run" in idle.note


def test_a_metric_the_engine_left_out_is_unavailable_not_borrowed():
    partial = {"status": "ok", "record": _rec(imperviousPctWatershed=3.0), "reason": None}
    assert evidence.ev_impervious(_ctx(engine=partial)).value == 3.0
    r = evidence.ev_road_density(_ctx(engine=partial))
    assert r.status == "unavailable" and "did not return road density" in r.note


def test_engine_impervious_keeps_the_more_limiting_indicator():
    r = evidence.ev_impervious(_ctx(engine=OK))
    assert r.confidence == "H"
    assert "12.3% impervious, 25.0% agricultural land (exact watershed)" == r.value_text
    assert r.suggested_likert == "Disagree"        # impervious 12.3% drives
    assert "agricultural 25.0%" in r.note and ";" not in r.note
    ag = {"status": "ok", "record": _rec(imperviousPctWatershed=2.0, cropPctWatershed=55.0,
                                         hayPasturePctWatershed=6.0), "reason": None}
    r2 = evidence.ev_impervious(_ctx(engine=ag))
    assert r2.suggested_likert == "Strongly Disagree"   # agriculture is more limiting
    solo = {"status": "ok", "record": _rec(imperviousPctWatershed=8.0), "reason": None}
    r3 = evidence.ev_impervious(_ctx(engine=solo))
    assert r3.suggested_likert == "Agree" and "agricultural" not in r3.value_text


def test_engine_impoundments_count_and_normal_storage():
    r = evidence.ev_impoundments(_ctx(engine=OK))
    assert r.value == 2 and "150 acre-ft" in r.value_text
    assert r.suggested_likert == "Agree"


def test_engine_land_use_change_is_2001_to_2021():
    r = evidence.ev_land_use_change(_ctx(engine=OK))
    assert r.value == 2.0 and "(2001)" in r.value_text and "(2021)" in r.value_text
    assert r.suggested_likert == "Agree"


def test_transport_capacity_reads_engine_k_and_agriculture():
    r = evidence.ev_transport_capacity(_ctx(engine=OK, slope=0.0031))
    assert r.origin == "engine" and r.confidence == "L" and r.suggested_likert is None
    assert "soil K 0.28" in r.value_text and "agriculture 25%" in r.value_text
    assert "channel slope 0.0031" in r.value_text and ";" not in r.value_text
    assert evidence.ev_transport_capacity(_ctx(engine=FAILED, slope=0.0031)).status == "unavailable"


def test_natural_flow_regime_converts_normal_storage_to_m3_per_km2():
    r = evidence.ev_natural_flow_regime(_ctx(engine=OK))
    assert r.origin == "engine" and r.value == round(3.2 * 1233.48184, 0)
    assert "m3/km2 (exact watershed)" in r.value_text
    gage = {"baseflow_ratio": 0.42, "site": "03219500"}
    both = evidence.ev_natural_flow_regime(_ctx(engine=OK, flow=gage))
    assert "Q90/Q50 = 0.42" in both.value_text and "m3/km2" in both.value_text
    # the gage alone while the engine is out: no dam storage borrowed from anywhere
    only_gage = evidence.ev_natural_flow_regime(_ctx(engine=FAILED, flow=gage))
    assert only_gage.origin == "pull" and only_gage.status == "ok"
    assert "Q90/Q50 = 0.42" in only_gage.value_text and "storage" not in only_gage.value_text
    assert evidence.ev_natural_flow_regime(_ctx(engine=FAILED)).status == "unavailable"


def test_riparian_canopy_is_forest_only_and_corridor_is_natural_vegetation():
    grass = {"status": "ok", "record": _rec(forestPctRiparian=8.0, shrubPctRiparian=10.0,
                                            grasslandPctRiparian=50.0,
                                            woodyWetlandPctRiparian=0.0,
                                            herbWetlandPctRiparian=0.0), "reason": None}
    canopy = evidence.ev_canopy(_ctx(engine=grass))
    assert canopy.value == 8.0 and "riparian buffer" in canopy.value_text
    corridor = evidence.ev_corridor(_ctx(engine=grass))
    assert corridor.value == 68.0 and "natural vegetation" in corridor.value_text
    assert "aerial basemap" in corridor.note


def test_pull_uses_only_the_engine_and_the_direct_services(monkeypatch):
    monkeypatch.setattr(evidence.nid_barriers, "barriers_near", lambda *a, **k: [])
    monkeypatch.setattr(evidence.wqp, "median_value", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwis, "flow_stats", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwi, "wetlands_near", lambda *a, **k: None)
    ci = {"lat": 40.0, "lon": -83.0, "comid": None, "slope": 0.002, "fcode": 46006,
          "sinuosity": 1.2, "drainage_area_sqkm": 12.5}
    out = asyncio.run(evidence.pull(ci, engine=OK))
    assert set(out) == set(evidence.REGISTRY)
    for mid in evidence.ENGINE_METRICS:
        assert out[mid]["origin"] == "engine" and out[mid]["status"] == "ok"
    assert all(e.get("origin") in ("engine", "pull") for e in out.values())
    assert all(not e.get("anchor_label") for e in out.values())
    assert not hasattr(evidence, "streamcat") and not hasattr(evidence, "STREAMCAT_WS")
    running = asyncio.run(evidence.pull(ci, engine=RUNNING))
    assert all(running[mid]["status"] == "pending" for mid in evidence.ENGINE_METRICS)
    assert running["watershed-connectivity-dewatered-or-intermittent-segments"]["status"] == "ok"

"""Engine-first evidence.

Every metric the STAF site engine covers reads the exact-watershed value
first. The StreamCat lookup engine's COMID-keyed value is the labeled
fallback: ``origin="streamcat"``, ``fallback_reason`` when the engine failed
or refused, ``upgrade_pending`` while it still runs on a covered site, and
``anchor_label`` naming the reach it describes on a site outside NHDPlus V2.
Injects ``ctx.extras``; no network.
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

SC = {"pctimp2019ws": 7.0, "pctimp2001ws": 5.0, "rddensws": 0.9, "damnrmstorws": 12.0,
      "pctwdwet2019ws": 1.0, "pcthbwet2019ws": 0.2, "pctcrop2019ws": 30.0,
      "pcthay2019ws": 4.0, "kffactws": 0.31}
RP = {"pctconif2019wsrp100": 10.0, "pctdecid2019wsrp100": 20.0, "pctmxfst2019wsrp100": 5.0,
      "pctgrs2019wsrp100": 3.0, "pctshrb2019wsrp100": 1.0, "pctwdwet2019wsrp100": 0.5,
      "pcthbwet2019wsrp100": 0.5}

HR_ONLY = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
           "routing": {"routedDistanceFt": 1240.0, "daRatio": 1.8, "declined": False}}
COVERED = {"anchorKind": "v2Direct", "scoredReach": {"comid": 1}}


def _rec(**vals):
    return {"status": "ok", "engineVersion": "0.2.0",
            "watershed": {"areaSqkm": 12.5, "areaAgreement": 1.0, "nReaches": 7},
            "metrics": {k: {"value": v} for k, v in vals.items()}}


OK = {"status": "ok", "record": _rec(**ENGINE_VALUES), "reason": None}
FAILED = {"status": "failed", "record": None, "reason": "no stream"}
RUNNING = {"status": "running"}


def _ctx(engine=None, anchor=None, streamcat=None, rp=None, slope=None):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1, slope=slope)
    state = engine if engine is not None else {"status": "idle"}
    c.extras["engine"] = state
    c.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    c.extras["site_anchor"] = anchor or COVERED
    c.extras["streamcat"] = streamcat or {}
    c.extras["streamcat_rp"] = rp or {}
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
def test_engine_value_comes_first(mid, fn, value):
    r = fn(_ctx(engine=OK, streamcat=SC, rp=RP))
    assert r.metric_id == mid
    assert r.origin == "engine" and r.status == "ok" and r.value == value
    assert r.engine_version == "0.2.0"
    assert r.source == "STAF site engine v0.2.0 (exact watershed)"
    assert r.source_url == engine_prefill.ENGINE_URL
    assert "(exact watershed)" in r.field_value_text
    assert r.fallback_reason == "" and r.anchor_label == "" and not r.upgrade_pending


def test_engine_impervious_keeps_the_more_limiting_indicator():
    r = evidence.ev_impervious(_ctx(engine=OK, streamcat=SC))
    assert "12.3% impervious" in r.value_text and "25.0% agricultural land" in r.value_text
    assert "(exact watershed)" in r.value_text
    assert "agricultural 25.0%" in r.note and r.confidence == "H"


def test_engine_impoundments_count_and_normal_storage():
    r = evidence.ev_impoundments(_ctx(engine=OK))
    assert "2 NID dam(s) in the watershed" in r.value_text
    assert "150 acre-ft normal storage" in r.value_text
    assert r.suggested_likert == "Agree"


def test_engine_land_use_change_is_2001_to_2021():
    r = evidence.ev_land_use_change(_ctx(engine=OK))
    assert "10.3% (2001)" in r.value_text and "12.3% (2021)" in r.value_text
    assert r.suggested_likert == "Agree"                     # +2.0 pts


def test_transport_capacity_reads_engine_k_and_agriculture():
    r = evidence.ev_transport_capacity(_ctx(engine=OK, streamcat=SC, slope=0.0031))
    assert r.origin == "engine" and r.suggested_likert is None and r.confidence == "L"
    assert "soil K 0.28 (exact watershed)" in r.value_text
    assert "watershed agriculture 25% (exact watershed)" in r.value_text
    assert r.field_value_text == "Slope 0.0031 m/m"
    sc = evidence.ev_transport_capacity(_ctx(streamcat=SC, slope=0.0031))
    assert sc.origin == "streamcat" and "soil K 0.31" in sc.value_text
    assert "watershed agriculture 34%" in sc.value_text


def test_natural_flow_regime_converts_normal_storage_to_m3_per_km2():
    r = evidence.ev_natural_flow_regime(_ctx(engine=OK))
    assert r.origin == "engine"
    assert r.value == round(3.2 * evidence.ACRE_FT_PER_KM2_TO_M3_PER_KM2, 0) == 3947.0
    assert "3947 m3/km2 (exact watershed)" in r.value_text
    assert r.suggested_likert is None and r.confidence == "L"
    sc = evidence.ev_natural_flow_regime(_ctx(streamcat=SC))
    assert sc.origin == "streamcat" and "upstream dam storage 12" in sc.value_text


def test_streamcat_columns_carry_the_aoi_suffix():
    # rddens -> rddensws, damnrmstor -> damnrmstorws (the bare names never matched)
    assert evidence.ev_road_density(_ctx(streamcat=SC)).value == 0.9
    assert evidence.ev_road_density(_ctx(streamcat={"rddens": 0.9})).status == "unavailable"
    r = evidence.ev_impoundments(_ctx(streamcat=SC))
    assert r.status == "ok" and "upstream normal storage 12" in r.value_text
    assert "damdens" not in evidence.STREAMCAT_WS and "kffact" in evidence.STREAMCAT_WS


def test_streamcat_fallback_when_the_engine_failed():
    r = evidence.ev_impervious(_ctx(engine=FAILED, streamcat=SC))
    assert r.origin == "streamcat" and r.value == 7.0
    assert r.fallback_reason.startswith("STAF site engine failed: no stream")
    assert not r.upgrade_pending
    idle = evidence.ev_impervious(_ctx(streamcat=SC))
    assert idle.origin == "streamcat" and idle.fallback_reason == ""


def test_covered_site_shows_streamcat_while_the_engine_runs():
    r = evidence.ev_wetland(_ctx(engine=RUNNING, anchor=COVERED, streamcat=SC))
    assert r.origin == "streamcat" and r.status == "ok" and r.value == 1.2
    assert r.upgrade_pending is True and r.fallback_reason == ""


def test_hr_only_site_is_pending_while_the_engine_runs():
    r = evidence.ev_wetland(_ctx(engine=RUNNING, anchor=HR_ONLY, streamcat=SC))
    assert r.status == "pending" and r.origin == "engine" and r.value is None
    assert "STAF site engine" in r.source
    # a metric the engine never covers stays a direct pull
    np_ = evidence.ev_np(_ctx(engine=RUNNING, anchor=HR_ONLY))
    assert np_.status == "unavailable" and np_.origin == "pull"


def _stub_sources(monkeypatch, streamcat_rows=None):
    rows = streamcat_rows or {}

    def by_comid(comid, names, aoi="watershed", timeout=25.0):
        return rows.get(aoi, {}) if comid is not None else {}
    monkeypatch.setattr(evidence.streamcat, "metrics_by_comid", by_comid)
    monkeypatch.setattr(evidence.nid_barriers, "barriers_near", lambda *a, **k: [])
    monkeypatch.setattr(evidence.wqp, "median_value", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwis, "flow_stats", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwi, "wetlands_near", lambda *a, **k: None)
    monkeypatch.setattr(evidence.tiger_roads, "roads_near", lambda *a, **k: None)


def test_pull_labels_streamcat_rows_on_a_stream_outside_v2(monkeypatch):
    _stub_sources(monkeypatch, {"watershed": SC, "riparian_watershed": RP})
    engine = {"status": "ok", "record": _rec(imperviousPctWatershed=12.3), "reason": None}
    out = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": 5214461,
                                     "siteAnchor": HR_ONLY}, engine=engine))
    imp = out["catchment-hydrology-impervious-surface-area"]
    assert imp["origin"] == "engine" and imp["anchor_label"] == ""
    rd = out["catchment-hydrology-road-density"]
    assert rd["origin"] == "streamcat" and rd["value"] == 0.9
    assert rd["anchor_label"].startswith("nearest covered reach, COMID 5214461")
    assert out["nutrient-cycling-n-p-concentrations"]["anchor_label"] == ""
    labeled = {m for m, e in out.items() if e.get("anchor_label")}
    assert labeled <= set(evidence.ANCHOR_LABELED_METRICS)


def test_pull_on_a_covered_site_has_no_anchor_labels(monkeypatch):
    _stub_sources(monkeypatch, {"watershed": SC, "riparian_watershed": RP})
    out = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": 1,
                                     "siteAnchor": COVERED}, engine=OK))
    assert all(e.get("anchor_label") == "" for e in out.values())
    assert out["catchment-hydrology-road-density"]["origin"] == "engine"


def test_pull_without_a_state_runs_the_engine_inline(monkeypatch):
    _stub_sources(monkeypatch, {"watershed": SC})
    monkeypatch.setattr(engine_prefill, "site_engine_available", lambda: False)
    out = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": 1}))
    imp = out["catchment-hydrology-impervious-surface-area"]
    assert imp["origin"] == "streamcat" and imp["value"] == 7.0
    assert "unavailable" in imp["fallback_reason"]


def test_pull_with_a_withheld_comid_yields_no_streamcat_values(monkeypatch):
    _stub_sources(monkeypatch, {"watershed": SC})
    declined = {"anchorKind": "hrSurrogate", "scoredReach": {"comid": 5214461},
                "routing": {"declined": True, "daRatio": 14.0}}
    out = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": None,
                                     "siteAnchor": declined}, engine=FAILED))
    assert out["catchment-hydrology-road-density"]["status"] == "unavailable"
    assert out["catchment-hydrology-impervious-surface-area"]["status"] == "unavailable"

"""Two watershed engines in the evidence pull (2026-09-07).

The STAF site engine answers every watershed metric first. The StreamCat
lookup engine answers by COMID: the EPA modeled indices that exist only per
NHDPlus V2 reach, and the labeled stand-in for a watershed value when the
engine failed, refused, or never ran. Every StreamCat value names the reach it
describes and, on a stream outside V2, the routed distance and the
drainage-area ratio. Injects ``ctx.extras``; no network.
"""
from __future__ import annotations

import asyncio

from sfari import comid_anchor, config, engine_prefill, evidence
from sfari.metrics.base import AnalysisContext

ENGINE_VALUES = dict(
    imperviousPctWatershed=12.3, cropPctWatershed=20.0, hayPasturePctWatershed=5.0,
    roadDensity=1.59, damCount=2, damStorageAcreFt=150.0, damStoragePerSqkm=3.2,
    woodyWetlandPctWatershed=1.5, herbWetlandPctWatershed=0.5,
    forestPctRiparian=42.0, shrubPctRiparian=3.0, grasslandPctRiparian=5.0,
    woodyWetlandPctRiparian=2.0, herbWetlandPctRiparian=1.0, soilKFactor=0.28,
    imperviousPct2001Watershed=10.3, roadCrossings=3, roadCrossingDensity=0.24,
    entrenchmentRatio=2.4, bankHeightRatio=1.3, bankfullWidthM=4.2, baseflowIndexPct=48.0)

SC_ROW = {"pctimp2019ws": 11.0, "pctimp2001ws": 9.0, "pctcrop2019ws": 20.0, "pcthay2019ws": 5.0,
          "rddensws": 1.4, "rdcrsws": 0.3, "pctwdwet2019ws": 1.0, "pcthbwet2019ws": 0.5,
          "pctconif2019wsrp100": 10.0, "pctdecid2019wsrp100": 20.0, "pctmxfst2019wsrp100": 5.0,
          "pctshrb2019wsrp100": 3.0, "pctgrs2019wsrp100": 4.0, "pctwdwet2019wsrp100": 1.0,
          "pcthbwet2019wsrp100": 1.0, "kffactws": 0.25, "damdensws": 0.05,
          "damnrmstorws": 1200.0, "hydws": 0.62, "hydcat": 0.70, "connws": 0.71,
          "conncat": 0.80, "chemws": 0.55, "chemcat": 0.60}

V2 = {"anchorKind": "v2Direct", "scoredReach": {"comid": 9327042, "gnisName": "Mink Brook"}}
ROUTED = {"anchorKind": "hrSurrogate",
          "clickedStream": {"nhdplusId": 24000800011817, "snapLat": 43.6858, "snapLon": -72.2367},
          "scoredReach": {"comid": 9327042, "gnisName": "Mink Brook",
                          "snapLat": 43.683, "snapLon": -72.23},
          "routing": {"routedDistanceFt": 1688.0, "daRatio": 32.0, "declined": True}}


def _rec(**vals):
    return {"status": "ok", "engineVersion": "0.4.0",
            "watershed": {"areaSqkm": 12.5, "areaAgreement": 1.0, "nReaches": 7},
            "metrics": {k: {"value": v} for k, v in vals.items()}}


OK = {"status": "ok", "record": _rec(**ENGINE_VALUES), "reason": None}
FAILED = {"status": "failed", "record": None, "reason": "no stream"}
RUNNING = {"status": "running"}


def _ctx(engine=None, anchor=None, sc=None, **extras):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=None, slope=extras.pop("slope", None),
                        fcode=extras.pop("fcode", None))
    state = engine if engine is not None else {"status": "idle"}
    c.extras["engine"] = state
    c.extras["engine_metrics"] = engine_prefill.engine_metrics(state.get("record"))
    c.extras["site_anchor"] = anchor
    c.extras["streamcat"] = dict(sc) if sc else {}
    c.extras.update(extras)
    return c


# --- the engine first ---------------------------------------------------------
def test_engine_value_wins_even_with_a_streamcat_row():
    r = evidence.ev_impervious(_ctx(engine=OK, anchor=ROUTED, sc=SC_ROW))
    assert r.origin == "engine" and r.value == 12.3 and r.anchor_label == ""
    assert r.value_text.endswith("(HR reach watershed)") and r.fallback_reason == ""


def test_running_stays_pending_even_with_a_streamcat_row():
    r = evidence.ev_road_density(_ctx(engine=RUNNING, anchor=V2, sc=SC_ROW))
    assert r.status == "pending" and r.origin == "engine"


def test_engine_ok_but_missing_a_key_is_borrowed_and_labeled():
    # the engine ran (impervious came back) but left road density out: the
    # StreamCat analog stands in, labeled with why (per-value fallback, 2026-09-07)
    partial = {"status": "ok", "record": _rec(imperviousPctWatershed=3.0), "reason": None}
    r = evidence.ev_road_density(_ctx(engine=partial, anchor=V2, sc=SC_ROW))
    assert r.origin == "streamcat" and r.status == "ok" and r.value == 1.4
    assert r.fallback_reason == ("The STAF site engine ran but did not return this value for "
                                 "the HR reach watershed.")
    assert r.anchor_label == "" and r.source == "StreamCat lookup engine, COMID 9327042 (this reach)"
    # the value the engine did return keeps the engine, and agriculture is not
    # borrowed from the other basin to sit beside it
    imp = evidence.ev_impervious(_ctx(engine=partial, anchor=V2, sc=SC_ROW))
    assert imp.origin == "engine" and imp.value == 3.0 and "agricultural" not in imp.value_text
    # without a StreamCat row the gap stays unavailable with the engine's reason
    gap = evidence.ev_road_density(_ctx(engine=partial))
    assert gap.status == "unavailable" and "did not return road density" in gap.note


def test_a_two_year_change_never_mixes_the_basins():
    # 2021 from the engine, no 2001 baseline: both years come from StreamCat
    # (2001 to 2019) rather than one from each
    no_base = {"status": "ok", "record": _rec(imperviousPctWatershed=6.2), "reason": None}
    lu = evidence.ev_land_use_change(_ctx(engine=no_base, anchor=ROUTED, sc=SC_ROW))
    assert lu.origin == "streamcat" and lu.value == 2.0
    assert "9.0% (2001)" in lu.value_text and "11.0% (2019)" in lu.value_text
    assert "32 times this stream" in lu.anchor_label and "ran but did not return" in lu.fallback_reason
    assert evidence.ev_land_use_change(_ctx(engine=no_base)).status == "unavailable"
    # the same rule for a class sum: the engine has woody wetland only
    part = {"status": "ok", "record": _rec(woodyWetlandPctWatershed=1.5), "reason": None}
    wet = evidence.ev_wetland(_ctx(engine=part, anchor=V2, sc=SC_ROW))
    assert wet.origin == "streamcat" and wet.value == 1.5
    assert evidence.ev_wetland(_ctx(engine=part)).status == "unavailable"


# --- the labeled stand-in -----------------------------------------------------
def test_streamcat_stands_in_on_a_routed_stream_with_the_ratio():
    r = evidence.ev_impervious(_ctx(engine=FAILED, anchor=ROUTED, sc=SC_ROW))
    assert r.origin == "streamcat" and r.status == "ok" and r.value == 11.0
    assert r.value_text == "11.0% impervious, 25.0% agricultural land (NHDPlus V2 basin, COMID 9327042)"
    assert r.source == "StreamCat lookup engine, nearest StreamCat reach Mink Brook (COMID 9327042)"
    assert r.anchor_label == ("nearest StreamCat reach Mink Brook (COMID 9327042), 1,688 ft "
                              "downstream, which drains 32 times this stream")
    assert r.fallback_reason == "STAF site engine failed: no stream."
    assert r.suggested_likert == "Disagree" and r.confidence == "M"
    assert "32 times this stream" in r.note and r.source_url == comid_anchor.STREAMCAT_URL


def test_streamcat_stands_in_on_a_covered_reach_without_a_describes_line():
    r = evidence.ev_wetland(_ctx(engine=FAILED, anchor=V2, sc=SC_ROW))
    assert r.origin == "streamcat" and r.value == 1.5
    assert r.source == "StreamCat lookup engine, COMID 9327042 (this reach)"
    assert r.anchor_label == "" and "the reach this point sits on" in r.note


def test_idle_engine_lets_streamcat_answer():
    r = evidence.ev_road_density(_ctx(anchor=V2, sc=SC_ROW))
    assert r.origin == "streamcat" and r.value == 1.4
    assert r.fallback_reason == "The STAF site engine has not run for this site."


def test_failed_engine_without_streamcat_is_unavailable_with_both_reasons():
    no_comid = evidence.ev_impervious(_ctx(engine=FAILED))
    assert no_comid.status == "unavailable" and "failed: no stream" in no_comid.note
    assert "No StreamCat reach is known" in no_comid.note
    no_row = evidence.ev_impervious(_ctx(engine=FAILED, anchor=V2))
    assert "did not answer for COMID 9327042" in no_row.note
    no_col = evidence.ev_impervious(_ctx(engine=FAILED, anchor=V2, sc={"rddensws": 1.0}))
    assert "has no impervious cover for COMID 9327042 either" in no_col.note


def test_streamcat_analogs_of_the_other_watershed_metrics():
    c = _ctx(engine=FAILED, anchor=V2, sc=SC_ROW, slope=0.003)
    lu = evidence.ev_land_use_change(c)
    assert lu.origin == "streamcat" and lu.value == 2.0 and "(2019)" in lu.value_text
    nfr = evidence.ev_natural_flow_regime(c)
    assert nfr.origin == "streamcat" and nfr.value == 1200.0 and "1200 m3/km2" in nfr.value_text
    imp = evidence.ev_impoundments(c)
    assert imp.origin == "streamcat" and imp.value == 0.05 and imp.suggested_likert is None
    assert "1,200 m3/km2 normal storage" in imp.value_text
    canopy = evidence.ev_canopy(c)
    assert canopy.origin == "streamcat" and canopy.value == 35.0
    corridor = evidence.ev_corridor(c)
    assert corridor.origin == "streamcat" and corridor.value == 44.0
    tc = evidence.ev_transport_capacity(c)
    assert tc.origin == "streamcat" and "soil K 0.25" in tc.value_text
    assert "(NHDPlus V2 basin, COMID 9327042)" in tc.value_text
    crossings = evidence.ev_concentrated_inputs(c)
    assert crossings.origin == "streamcat" and crossings.value == 0.3
    assert "rdcrs" in crossings.note


# --- the COMID-only indices ---------------------------------------------------
def test_flow_permanence_uses_hyd_when_no_gage_qualifies():
    r = evidence.ev_flow_permanence(_ctx(engine=OK, anchor=ROUTED, sc=SC_ROW))
    assert r.origin == "streamcat" and r.status == "ok" and r.value == 0.62
    assert r.value_text == ("HYD integrity 0.62 watershed, 0.70 catchment (EPA modeled index, "
                            "NHDPlus V2 basin, COMID 9327042)")
    assert r.suggested_likert is None and r.fallback_reason == ""
    assert r.note.startswith("No comparable nearby gage") and "0.40" in r.note and "0.70" in r.note
    assert "32 times this stream" in r.anchor_label
    gage = {"zero_frac": 0.01, "site": "01144000", "name": "X", "n_days": 3650}
    g = evidence.ev_flow_permanence(_ctx(engine=OK, anchor=ROUTED, sc=SC_ROW, flow=gage))
    assert g.origin == "pull" and "zero-flow days" in g.value_text
    none = evidence.ev_flow_permanence(_ctx(engine=OK))
    assert none.status == "unavailable"


def test_barriers_add_conn_beside_the_inventory_or_stand_in_for_it():
    beside = evidence.ev_barriers(_ctx(anchor=V2, sc=SC_ROW, nid=[]))
    assert beside.origin == "pull" and beside.value == 0
    assert beside.value_text == "0 dam/barrier(s) within ~1 mi · CONN integrity 0.71 (StreamCat, COMID 9327042)"
    assert "StreamCat CONN" in beside.source
    alone = evidence.ev_barriers(_ctx(anchor=V2, sc=SC_ROW, nid=None))
    assert alone.origin == "streamcat" and alone.value == 0.71 and "CONN integrity" in alone.value_text
    assert evidence.ev_barriers(_ctx(nid=None)).status == "unavailable"


def test_dewatered_adds_hyd_and_the_base_flow_index():
    r = evidence.ev_dewatered(_ctx(engine=OK, anchor=V2, sc=SC_ROW, fcode=46006))
    assert r.origin == "pull" and r.value == 46006
    assert r.value_text == ("NHD flow permanence: perennial · HYD integrity 0.62 (StreamCat, "
                            "COMID 9327042) · base-flow index 48% (HR reach watershed)")
    assert r.source == "NHDPlus FCODE + StreamCat HYD + STAF site engine base-flow index"
    plain = evidence.ev_dewatered(_ctx(fcode=46003))
    assert plain.value_text == "NHD flow permanence: intermittent" and plain.source == "NHDPlus FCODE"
    no_code = evidence.ev_dewatered(_ctx(anchor=V2, sc=SC_ROW))
    assert no_code.origin == "streamcat" and "HYD integrity" in no_code.value_text


def test_nutrients_use_chem_when_wqp_has_no_medians():
    r = evidence.ev_np(_ctx(anchor=V2, sc=SC_ROW, tn=None, tp=None))
    assert r.origin == "streamcat" and r.value == 0.55 and "CHEM integrity" in r.value_text
    obs = evidence.ev_np(_ctx(anchor=V2, sc=SC_ROW, tn=1.2, tp=None))
    assert obs.origin == "pull" and "TN 1.2 mg/L" in obs.value_text


# --- the engine's reach cross-sections and crossings --------------------------
def test_reach_cross_section_metrics_read_the_engine():
    c = _ctx(engine=OK)
    ob = evidence.ev_overbank_frequency(c)
    assert ob.origin == "engine" and ob.value == 1.3 and "bank height ratio 1.30" in ob.value_text
    assert ob.suggested_likert is None and ob.confidence == "L" and "nine" in ob.note
    pk = evidence.ev_peak_capacity(c)
    assert pk.value == 2.4 and "bankfull width 4.2 m" in pk.value_text
    assert "entrenchment ratio 2.40" in pk.value_text
    cr = evidence.ev_concentrated_inputs(c)
    assert cr.origin == "engine" and cr.value == 0.24 and "3 road-stream crossing(s)" in cr.value_text
    lat = evidence.ev_lateral_inundation(_ctx(engine=OK, nwi=None))
    assert lat.origin == "engine" and lat.value == 2.4 and "no NWI wetland feature" in lat.value_text
    with_nwi = evidence.ev_lateral_inundation(_ctx(engine=OK, nwi={"count": 2, "acres": 3.1}))
    assert with_nwi.origin == "pull" and "entrenchment ratio 2.40" in with_nwi.value_text
    assert evidence.ev_overbank_frequency(_ctx(engine=FAILED)).status == "unavailable"


def test_registry_covers_every_desktop_metric_the_app_computes():
    computed = {m["metricId"] for m in config.desktop_metrics()
                if (m.get("desktopSource") or {}).get("client") not in ("manual", "xscalc")}
    assert computed <= set(evidence.REGISTRY)
    assert set(evidence.ENGINE_METRICS) | set(evidence.ENGINE_REACH_METRICS) \
        | set(evidence.STREAMCAT_ONLY_METRICS) <= set(evidence.REGISTRY)
    assert len(evidence.REGISTRY) == 22


# --- the pull -----------------------------------------------------------------
def _quiet(monkeypatch):
    monkeypatch.setattr(evidence.nid_barriers, "barriers_near", lambda *a, **k: [])
    monkeypatch.setattr(evidence.wqp, "median_value", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwis, "flow_stats", lambda *a, **k: None)
    monkeypatch.setattr(evidence.nwi, "wetlands_near", lambda *a, **k: None)


def test_pull_fetches_streamcat_by_the_anchor_comid(monkeypatch):
    _quiet(monkeypatch)
    calls = []
    monkeypatch.setattr(evidence.streamcat, "metrics_by_comid",
                        lambda comid, names, *a, **k: calls.append((comid, tuple(names))) or SC_ROW)
    ci = {"lat": 40.0, "lon": -83.0, "comid": None, "slope": 0.002, "fcode": 46006,
          "sinuosity": 1.2, "drainage_area_sqkm": 12.5, "siteAnchor": ROUTED}
    out = asyncio.run(evidence.pull(ci, engine=FAILED))
    assert calls and calls[0][0] == 9327042 and "hyd" in calls[0][1] and "pctimp2001" in calls[0][1]
    imp = out["catchment-hydrology-impervious-surface-area"]
    assert imp["origin"] == "streamcat" and "32 times this stream" in imp["anchor_label"]
    assert out["streamflow-regime-flow-permanence"]["origin"] == "streamcat"
    assert out["watershed-connectivity-upstream-and-downstream-barriers"]["value_text"].endswith(
        "CONN integrity 0.71 (StreamCat, COMID 9327042)")
    # the engine's answer still wins everywhere it has one
    ok = asyncio.run(evidence.pull(ci, engine=OK))
    assert all(ok[mid]["origin"] == "engine" for mid in evidence.ENGINE_METRICS)
    assert all(ok[mid]["origin"] == "engine" for mid in evidence.ENGINE_REACH_METRICS)


def test_pull_with_a_bare_comid_and_with_none(monkeypatch):
    _quiet(monkeypatch)
    calls = []
    monkeypatch.setattr(evidence.streamcat, "metrics_by_comid",
                        lambda comid, names, *a, **k: calls.append(comid) or SC_ROW)
    ci = {"lat": 40.0, "lon": -83.0, "comid": 5214461, "slope": 0.002, "fcode": 46006}
    out = asyncio.run(evidence.pull(ci, engine=FAILED))
    assert calls == [5214461]
    assert out["catchment-hydrology-impervious-surface-area"]["source"] == \
        "StreamCat lookup engine, COMID 5214461 (this reach)"
    calls.clear()
    none = asyncio.run(evidence.pull({"lat": 40.0, "lon": -83.0, "comid": None}, engine=FAILED))
    assert calls == [] and none["catchment-hydrology-impervious-surface-area"]["status"] == "unavailable"
    assert "No StreamCat reach is known" in none["catchment-hydrology-impervious-surface-area"]["note"]


def test_an_engine_dam_density_without_a_count_stays_an_engine_entry():
    # dams.compute reports the count and the density together, so this branch
    # is defensive; when only the density exists it is still the engine's value
    # (one basin per value: no StreamCat storage clause, no StreamCat label)
    dens_only = {"status": "ok", "record": _rec(damDensityPerSqkm=0.081), "reason": None}
    r = evidence.ev_impoundments(_ctx(engine=dens_only, anchor=V2, sc=SC_ROW))
    assert r.origin == "engine" and r.value == 0.081 and r.suggested_likert is None
    assert r.value_text.endswith("(HR reach watershed)") and "m3/km2" not in r.value_text
    assert "StreamCat" not in r.source

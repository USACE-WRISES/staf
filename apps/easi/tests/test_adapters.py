"""Offline tests for metric-adapter binning (no network: inject ctx.extras)."""
from __future__ import annotations

import pytest

from easi import config, geomorph
from easi.datasources import wqp
from easi.metrics import biology, geomorphology, hydraulics, hydrology, physicochemistry
from easi.metrics.base import AnalysisContext


def _ctx(streamcat=None, landcover=None, **fields):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1, **fields)
    c.extras["streamcat"] = streamcat or {}
    c.extras["landcover"] = landcover or {}
    return c


@pytest.mark.parametrize("pct,expected", [(5, "Good"), (9.99, "Good"),
                                          (10, "Fair"), (25, "Fair"),
                                          (25.1, "Poor"), (60, "Poor")])
def test_impervious_bins_streamcat(pct, expected):
    r = hydrology.impervious(_ctx(streamcat={"pctimp2019ws": pct}))
    assert r.rating == expected
    assert r.confidence == "H"
    assert "StreamCat" in r.source


def test_impervious_falls_back_to_nlcd():
    r = hydrology.impervious(_ctx(landcover={"impervious_pct": 40}))
    assert r.rating == "Poor"
    assert "NLCD" in r.source


def test_impervious_unavailable():
    r = hydrology.impervious(_ctx())
    assert r.rating is None and r.status == "unavailable"


@pytest.mark.parametrize("pct,expected", [(6, "Good"), (5.1, "Good"),
                                          (5, "Fair"), (1, "Fair"),
                                          (0.5, "Poor"), (0, "Poor")])
def test_wetlands_bins_nlcd(pct, expected):
    r = hydrology.wetlands(_ctx(landcover={"wetland_pct": pct}))
    assert r.rating == expected


# --- impairment fallback chain (ATTAINS point -> nearby -> modeled surrogate) ---
def test_impairment_assessed_point(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point",
                        lambda lat, lon: {"assessment_unit": "AU1", "isimpaired": "Y",
                                          "ircategory": "5", "overallstatus": "Not Supporting"})
    r = physicochemistry.impairment(_ctx())
    assert r.rating == "Poor" and r.confidence == "H" and "ATTAINS" in r.source


def test_impairment_nearby_when_point_unassessed(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda lat, lon: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point",
                        lambda lat, lon: {"assessment_unit": "AU2", "isimpaired": "Y",
                                          "ircategory": "4A", "overallstatus": "Not Supporting"})
    r = physicochemistry.impairment(_ctx())
    assert r.rating == "Poor" and r.confidence == "M" and "within ~2 km" in r.value_text


def test_impairment_surrogate_when_no_attains(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda lat, lon: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", lambda lat, lon: {})
    r = physicochemistry.impairment(_ctx(streamcat={"pctimp2019ws": 60, "pctcrop2019ws": 80}))
    assert r.rating == "Poor" and r.confidence == "L" and "Modeled" in r.source


def test_impairment_force_surrogate_source(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point",
                        lambda lat, lon: {"assessment_unit": "AU1", "isimpaired": "Y",
                                          "ircategory": "5", "overallstatus": "Not Supporting"})
    ctx = _ctx(streamcat={"pctimp2019ws": 2})
    ctx.extras["source_choices"] = {physicochemistry.IMPAIRMENT_ID: "surrogate"}
    r = physicochemistry.impairment(ctx)
    assert r.confidence == "L" and "Modeled" in r.source   # surrogate forced, ATTAINS ignored


def test_wetlands_streamcat_sums_woody_and_herbaceous():
    r = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 3, "pcthbwet2019ws": 4}))
    assert r.value == 7.0 and r.rating == "Good" and "StreamCat" in r.source


# --- Phase 3 proxy adapters (offline) -------------------------------------- #
@pytest.mark.parametrize("fcode,expected", [(46006, "Good"), (46003, "Fair"),
                                            (46007, "Poor")])
def test_low_flow_fcode(fcode, expected):
    assert hydraulics.low_flow_connectivity(_ctx(fcode=fcode)).rating == expected


def test_low_flow_unavailable_without_fcode():
    assert hydraulics.low_flow_connectivity(_ctx()).status == "unavailable"


@pytest.mark.parametrize("stor,da,expected", [(0, 100, "Good"), (4000, 100, "Fair"),
                                              (20000, 100, "Poor")])
def test_flow_alteration_storage_ratio(stor, da, expected):
    r = hydrology.flow_alteration(_ctx(streamcat={"damnrmstorws": stor},
                                       drainage_area_sqkm=da))
    assert r.rating == expected


def test_reach_inflow_road_density():
    assert hydrology.reach_inflow(_ctx(streamcat={"rddensws": 0.5})).rating == "Good"
    assert hydrology.reach_inflow(_ctx(streamcat={"rddensws": 9.0})).rating == "Poor"


def test_sediment_supply_composite():
    low = geomorphology.sediment_supply(_ctx(streamcat={"kffactws": 0.1, "rddensws": 0.5,
                                                        "pctcrop2019ws": 0, "pcthay2019ws": 0}))
    high = geomorphology.sediment_supply(_ctx(streamcat={"kffactws": 0.4, "rddensws": 6,
                                                         "pctcrop2019ws": 60, "pcthay2019ws": 10}))
    assert low.rating == "Good" and high.rating == "Poor"


def test_hyporheic_slope_sinuosity():
    good = hydraulics.hyporheic(_ctx(slope=0.012, sinuosity=1.6))
    poor = hydraulics.hyporheic(_ctx(slope=0.0005, sinuosity=1.02))
    assert good.rating == "Good" and poor.rating == "Poor"


# --- high flow dynamics: floodplain engagement frequency (BHR -> recurrence) - #
def _geom_ctx(geom, slope=0.004):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1, slope=slope)
    c.extras["reach_geomorph"] = geom
    return c


def _trapezoid_geom(bank_h, da=50.0):
    st = list(range(0, 121))
    cx, half = 60, 10
    elevs = [(abs(x - cx) / half) * bank_h if abs(x - cx) <= half else bank_h for x in st]
    return geomorph.reach_summary([(st, elevs)], da)


@pytest.mark.parametrize("bank_h,expected", [(1.0, "Good"), (1.3, "Fair"), (3.0, "Poor")])
def test_floodplain_engagement_bins(bank_h, expected):
    # bank height -> bank-height ratio -> recurrence -> rating
    r = hydraulics.floodplain_engagement(_geom_ctx(_trapezoid_geom(bank_h)))
    assert r.rating == expected
    assert r.metric_id == hydraulics.FLOODPLAIN_ENGAGEMENT_ID


@pytest.mark.parametrize("bhr,expected", [(1.0, "Good"), (1.3, "Fair"), (2.0, "Poor")])
def test_rate_engagement_bins(bhr, expected):
    rating, t_years = hydraulics.rate_engagement(bhr)
    assert rating == expected and t_years is not None


def test_rate_engagement_no_bhr():
    assert hydraulics.rate_engagement(None) == (None, None)


def test_floodplain_engagement_from_bhr():
    # high bank-height ratio -> incised -> rarely engaged -> Poor
    r = hydraulics.floodplain_engagement(_geom_ctx({"bank_height_ratio": 2.0}))
    assert r.rating == "Poor" and r.status == "ok"


def test_floodplain_engagement_never_missing():
    r = hydraulics.floodplain_engagement(_geom_ctx({}))   # no terrain -> screening default
    assert r.rating in ("Good", "Fair", "Poor")


# --- floodplain connectivity: access / entrenchment (ER, lateral) ----------- #
@pytest.mark.parametrize("er,expected", [(3.0, "Good"), (1.8, "Fair"), (1.2, "Poor")])
def test_rate_entrenchment_er_only(er, expected):
    assert hydraulics.rate_entrenchment(er) == expected


def test_rate_entrenchment_none():
    assert hydraulics.rate_entrenchment(None) is None


def test_floodplain_access_is_lateral_only():
    # connectivity is ER-only: a wide flood-prone area rates Good even if incised
    # (incision / "how often it floods" is the separate High flow dynamics metric)
    incised = hydraulics.floodplain_access(_geom_ctx(
        {"entrenchment_ratio": 3.0, "bank_height_ratio": 1.6, "n_transects": 9}))
    assert incised.rating == "Good" and incised.metric_id == hydraulics.ENTRENCHMENT_ID
    entrenched = hydraulics.floodplain_access(_geom_ctx(
        {"entrenchment_ratio": 1.2, "n_transects": 9}))
    assert entrenched.rating == "Poor"


def test_rate_metrics_from_stages_splits_axes():
    # the two cross-section metrics use different axes and can differ: entrenchment (ER,
    # lateral) from the bankfull stage; engagement (BHR, vertical) from the floodplain stage
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]      # deep V; banks reach 4 m
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0}
    out = assessment.rate_metrics_from_stages(block, bankfull_stage=1.0, floodplain_stage=3.0)
    assert out[hydraulics.ENTRENCHMENT_ID]["rating"] == "Fair"          # ER 2.0 -> moderate access
    assert out[hydraulics.FLOODPLAIN_ENGAGEMENT_ID]["rating"] == "Poor"  # BHR 3.0 -> rarely engaged
    assert "entrenchment ratio" in out[hydraulics.ENTRENCHMENT_ID]["valueText"]
    assert "bank-height ratio" in out[hydraulics.FLOODPLAIN_ENGAGEMENT_ID]["valueText"]


def test_floodplain_height_moves_engagement_not_connectivity():
    # editing the floodplain height changes engagement (BHR) but not connectivity (ER)
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0}
    low = assessment.rate_metrics_from_stages(block, 1.0, 1.2)   # floodplain near bankfull
    high = assessment.rate_metrics_from_stages(block, 1.0, 3.5)  # floodplain raised
    en = hydraulics.ENTRENCHMENT_ID
    fp = hydraulics.FLOODPLAIN_ENGAGEMENT_ID
    assert low[en]["rating"] == high[en]["rating"]   # ER (connectivity) unchanged by floodplain
    order = {"Good": 2, "Fair": 1, "Poor": 0}
    assert order[high[fp]["rating"]] <= order[low[fp]["rating"]]  # engagement worsens


def test_rate_channel_evolution_bins():
    assert geomorphology.rate_channel_evolution(1.1) == "Good"
    assert geomorphology.rate_channel_evolution(1.5) == "Fair"
    assert geomorphology.rate_channel_evolution(2.0) == "Poor"
    assert geomorphology.rate_channel_evolution(None) is None


def test_rate_metrics_from_stages_includes_channel_evolution():
    # editing the low-bank height re-rates channel evolution from the same BHR
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]      # deep V; banks reach 4 m
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0}
    ce = geomorphology.CHANNEL_EVOL_ID
    low = assessment.rate_metrics_from_stages(block, 1.0, 1.2)   # BHR 1.2 -> stable
    high = assessment.rate_metrics_from_stages(block, 1.0, 3.5)  # BHR 3.5 -> incised
    assert low[ce]["rating"] == "Good" and high[ce]["rating"] == "Poor"
    assert "bank-height ratio" in high[ce]["valueText"]


def test_rate_metrics_from_stages_channelized_keeps_default_channel_evolution():
    # a canal/ditch stays Poor regardless of geometry edits, so it is not re-rated here
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0, "fcode": 33600}  # canal
    out = assessment.rate_metrics_from_stages(block, 1.0, 1.0)
    assert geomorphology.CHANNEL_EVOL_ID not in out
    assert hydraulics.FLOODPLAIN_ENGAGEMENT_ID in out   # floodplain metrics still recompute


def test_build_cross_section_three_candidates():
    # crossSection stores one editable block per candidate, with the middle selected
    from easi import assessment
    st = list(range(0, 101))
    el = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in st]
    base = geomorph.summarize_profile(st, el, 50.0, division="Interior Plains")
    cands = []
    for lab in ("Upstream", "Middle", "Downstream"):
        c = dict(base); c["label"] = lab; cands.append(c)
    geom = dict(base); geom["candidates"] = cands; geom["selected"] = 1
    cs = assessment._build_cross_section(geom, slope=0.004, fcode=None)
    assert cs and len(cs["candidates"]) == 3 and cs["selected"] == 1
    assert [b["label"] for b in cs["candidates"]] == ["Upstream", "Middle", "Downstream"]
    assert cs["geom"]["label"] == "Middle" and cs["geom"]["fcode"] is None


# --- biological integrity (modeled surrogate) ------------------------------ #
def test_biological_integrity_spread():
    good = biology.biological_integrity(_ctx(streamcat={
        "pctmxfst2019wsrp100": 80, "pctimp2019ws": 0, "pctcrop2019ws": 0, "rddensws": 0}))
    poor = biology.biological_integrity(_ctx(streamcat={
        "pctmxfst2019wsrp100": 5, "pctimp2019ws": 50, "pctcrop2019ws": 10, "rddensws": 8}))
    assert good.rating == "Good" and poor.rating == "Poor"
    assert good.confidence == "L" and "surrogate" in good.source.lower()


def test_biological_integrity_default_when_no_data():
    r = biology.biological_integrity(_ctx())
    assert r.rating == "Fair" and r.confidence == "L"


# --- source choice (configure step) ---------------------------------------- #
def test_wetlands_source_choice():
    base_sc = {"pctwdwet2019ws": 3, "pcthbwet2019ws": 4}   # StreamCat sums to 7
    c = _ctx(streamcat=base_sc, landcover={"wetland_pct": 9.0})
    c.extras["source_choices"] = {hydrology.WETLANDS_ID: "streamcat"}
    r = hydrology.wetlands(c)
    assert r.value == 7.0 and "StreamCat" in r.source
    c2 = _ctx(streamcat=base_sc, landcover={"wetland_pct": 9.0})
    c2.extras["source_choices"] = {hydrology.WETLANDS_ID: "nlcd"}
    r2 = hydrology.wetlands(c2)
    assert r2.value == 9.0 and "NLCD" in r2.source


def test_stream_temperature_source_choice_surrogate():
    from easi.metrics import physicochemistry
    c = _ctx(streamcat={"tmean8110ws": 22.0})   # warm, no riparian -> Poor surrogate
    c.extras["source_choices"] = {physicochemistry.TEMPERATURE_ID: "surrogate"}
    r = physicochemistry.stream_temperature(c)   # forced surrogate -> no WQP/network
    assert "surrogate" in r.source.lower() and r.rating in ("Good", "Fair", "Poor")


# --- stream temperature: observed (WQP) primary + climate surrogate fallback --- #
@pytest.mark.parametrize("temp,expected", [(15, "Good"), (19.9, "Good"),
                                           (20, "Fair"), (24.9, "Fair"),
                                           (25, "Poor"), (30, "Poor")])
def test_stream_temperature_observed_bins(monkeypatch, temp, expected):
    # WQP returns observed water temperature -> measured path (confidence M)
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a, **k: temp)
    r = physicochemistry.stream_temperature(_ctx())
    assert r.rating == expected
    assert r.confidence == "M" and "observed" in r.source.lower()


@pytest.mark.parametrize("tair,expected", [(8, "Good"), (11.9, "Good"),
                                           (12, "Fair"), (16.9, "Fair"),
                                           (17, "Poor"), (22, "Poor")])
def test_stream_temperature_climate_surrogate_bins(monkeypatch, tair, expected):
    # no WQP samples -> climate surrogate on the air-temp normal (no riparian credit)
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a, **k: None)
    r = physicochemistry.stream_temperature(_ctx(streamcat={"tmean8110ws": tair}))
    assert r.rating == expected
    assert r.confidence == "L" and "surrogate" in r.source.lower()


def test_stream_temperature_riparian_shade_relief(monkeypatch):
    # full riparian canopy credits ~2 C of relief: 13.5 C air -> 11.5 index -> Good
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a, **k: None)
    shaded = physicochemistry.stream_temperature(
        _ctx(streamcat={"tmean8110ws": 13.5, "pctmxfst2019wsrp100": 80}))
    bare = physicochemistry.stream_temperature(_ctx(streamcat={"tmean8110ws": 13.5}))
    assert shaded.rating == "Good" and bare.rating == "Fair"


def test_stream_temperature_never_missing(monkeypatch):
    # no observations and no climate data -> still rated (conservative default), not unavailable
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a, **k: None)
    r = physicochemistry.stream_temperature(_ctx())
    assert r.rating in ("Good", "Fair", "Poor")
    assert r.status == "ok" and r.confidence == "L"


class _FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


def test_wqp_fetch_parses_quoted_comma_fields(monkeypatch):
    # Rows carry quoted free-text with commas (station names, "Temperature, water").
    # A naive line.split(",") shifts past ResultMeasureValue and drops every row;
    # the csv parser must read 21.8 & 19.4 and skip the blank-value row -> median 20.6.
    csv_text = (
        "OrganizationIdentifier,MonitoringLocationName,CharacteristicName,"
        "ResultMeasureValue,ResultMeasure/MeasureUnitCode\n"
        '21OHIO_WQX,"Scioto River, at Columbus","Temperature, water",21.8,deg C\n'
        '21OHIO_WQX,"Big Walnut Ck, nr Central College","Temperature, water",19.4,deg C\n'
        '21OHIO_WQX,"Olentangy R, Worthington","Temperature, water",,deg C\n'
    )
    monkeypatch.setattr(wqp.requests, "get", lambda *a, **k: _FakeResp(csv_text))
    assert wqp.median_value("temp", 40.0, -83.0) == 20.6


# --- fail-fast: external-service timeouts + concurrent nutrients fetch --------- #
def test_external_datasource_timeouts_are_short():
    # Guard against regressing back to the old 25-40s timeouts that parked the
    # "Computing metrics… N/20" counter for tens of seconds when a service was slow.
    import inspect

    from easi.datasources import attains, nas, nid_barriers
    def _default(fn, name="timeout"):
        return inspect.signature(fn).parameters[name].default
    assert _default(wqp.median_value) <= 12
    assert _default(attains.impairment_at_point) <= 10
    assert _default(attains.impairment_near_point) <= 10
    assert _default(nid_barriers.barriers_near) <= 12
    assert _default(nas.established_taxa) <= 12


def test_nutrients_fetches_tn_tp_concurrently(monkeypatch):
    # nutrients() issues two WQP calls; they must overlap (max, not sum) so a slow
    # portal doesn't double the wait. Each stub sleeps 0.3s -> concurrent ~0.3s.
    import time

    def _slow(param, lat, lon, *a, **k):
        time.sleep(0.3)
        return {"tn": 0.4, "tp": 0.03}.get(param)
    monkeypatch.setattr(physicochemistry.wqp, "median_value", _slow)
    t0 = time.perf_counter()
    r = physicochemistry.nutrients(_ctx())
    elapsed = time.perf_counter() - t0
    assert r.value == {"tn": 0.4, "tp": 0.03}     # both fetched
    assert elapsed < 0.5                            # overlapped (~0.3s), not sequential (~0.6s)


# --- best-available 3DEP resolution (1 m else 10 m) ------------------------ #
def _fake_py3dep(avail, one_metre_finite=1.0, one_metre_raises=False):
    import types

    import numpy as np

    def _da(frac):
        a = np.arange(100, dtype=float)
        a[int(100 * frac):] = np.nan
        return types.SimpleNamespace(
            values=a, rio=types.SimpleNamespace(reproject=lambda crs: ("dem", crs)))

    def get_dem(geom, resolution):
        if resolution == 1:
            if one_metre_raises:
                raise RuntimeError("1 m boom")
            return _da(one_metre_finite)
        return _da(1.0)                              # 10 m always good
    return types.SimpleNamespace(check_3dep_availability=lambda bbox: avail, get_dem=get_dem)


@pytest.mark.parametrize("avail,finite,raises,expected", [
    ({"1m": True}, 1.0, False, 1),                  # 1 m present + valid -> 1 m
    ({"1m": True}, 0.2, False, 10),                 # 1 m present but mostly NaN -> 10 m
    ({"1m": False}, 1.0, False, 10),                # no 1 m coverage -> 10 m
    ({"1m": True}, 1.0, True, 10),                  # 1 m fetch errors -> 10 m
])
def test_best_available_dem_resolution(monkeypatch, avail, finite, raises, expected):
    import sys
    import types

    from easi.datasources import threedep
    monkeypatch.setitem(sys.modules, "py3dep",
                        _fake_py3dep(avail, finite, raises))
    buf = types.SimpleNamespace(bounds=(0.0, 0.0, 1.0, 1.0))
    _, res = threedep._best_available_dem(buf)
    assert res == expected


def test_channel_evolution_reports_dem_resolution():
    ctx = _ctx(fcode=46006)
    ctx.extras["reach_geomorph"] = {"bank_height_ratio": 1.2, "dem_resolution_m": 1}
    assert "(3DEP 1 m)" in geomorphology.channel_evolution(ctx).value_text
    ctx2 = _ctx(fcode=46006)                          # resolution unknown -> defaults to 10 m
    ctx2.extras["reach_geomorph"] = {"bank_height_ratio": 1.2}
    assert "(3DEP 10 m)" in geomorphology.channel_evolution(ctx2).value_text


def test_build_cross_section_threads_dem_source():
    from easi import assessment
    st = list(range(0, 101))
    el = [10.0 if not (40 <= x <= 60) else 6 + abs(x - 50) * 0.4 for x in st]
    geom = geomorph.summarize_profile(st, el, 50.0, division="Interior Plains")
    geom["candidates"] = [dict(geom, label=lab) for lab in ("Upstream", "Middle", "Downstream")]
    geom["selected"] = 1
    geom["dem_resolution_m"] = 1
    cs = assessment._build_cross_section(geom, slope=0.004, fcode=None)
    assert cs["geom"]["dem_resolution_m"] == 1
    assert cs["geom"]["dem_source"] == "USGS 3DEP 1 m DEM"


# --- catchment-hydrology land-cover: automatic more-limiting of impervious | agriculture --- #
def _lc_ctx(crop=None, hay=None, imp=None, landcover=None):
    sc = {}
    if crop is not None:
        sc["pctcrop2019ws"] = crop
    if hay is not None:
        sc["pcthay2019ws"] = hay
    if imp is not None:
        sc["pctimp2019ws"] = imp
    return _ctx(streamcat=sc, landcover=landcover or {})


def test_land_cover_impervious_governs_when_worse():
    # impervious high (Poor), agriculture low (Good) -> impervious governs
    r = hydrology.impervious(_lc_ctx(crop=5, imp=40))
    assert r.rating == "Poor" and r.detail["governing"] == "impervious"
    assert "impervious" in r.value_text


def test_land_cover_agriculture_governs_when_worse():
    # Idaho case: impervious near-zero (Good), agriculture dominant (Poor) -> agriculture governs
    r = hydrology.impervious(_lc_ctx(crop=61, imp=2))
    assert r.rating == "Poor" and r.detail["governing"] == "agriculture"
    assert "agricultural land" in r.value_text and "crop+hay" in r.source


@pytest.mark.parametrize("ag,expected", [(20, "Good"), (24.99, "Good"), (25, "Fair"),
                                         (50, "Fair"), (50.1, "Poor"), (70, "Poor")])
def test_land_cover_agriculture_bins(ag, expected):
    # impervious absent -> agriculture governs and uses the 25/50 bins
    r = hydrology.impervious(_lc_ctx(crop=ag))
    assert r.rating == expected and r.detail["governing"] == "agriculture"


def test_land_cover_tie_defaults_to_impervious():
    # both Good -> tie -> impervious (the anchor) governs
    r = hydrology.impervious(_lc_ctx(crop=10, imp=5))
    assert r.rating == "Good" and r.detail["governing"] == "impervious"
    assert "impervious" in r.value_text


def test_land_cover_uses_only_available_indicator():
    imp_only = hydrology.impervious(_lc_ctx(imp=40))               # no crop/hay in the row
    assert imp_only.rating == "Poor" and imp_only.detail["governing"] == "impervious"
    assert imp_only.detail["agriculture"] is None
    ag_only = hydrology.impervious(_ctx(landcover={"ag_pct": 60}))  # NLCD ag, no impervious
    assert ag_only.rating == "Poor" and ag_only.detail["governing"] == "agriculture"
    assert "NLCD" in ag_only.source and ag_only.detail["impervious"] is None


def test_land_cover_sums_crop_and_hay():
    r = hydrology.impervious(_lc_ctx(crop=30, hay=25))            # 55 -> Poor (agriculture)
    assert r.value == 55.0 and r.rating == "Poor"


def test_land_cover_unavailable_without_data():
    assert hydrology.impervious(_ctx()).status == "unavailable"


def test_land_cover_detail_carries_both_indicators():
    r = hydrology.impervious(_lc_ctx(crop=61, imp=2))
    assert r.detail["impervious"] == {"pct": 2.0, "rating": "Good"}
    assert r.detail["agriculture"] == {"pct": 61.0, "rating": "Poor"}
    assert "impervious 2.0%" in r.note and "agricultural 61.0%" in r.note


def test_criteria_bands_indicator_aware():
    mid = hydrology.IMPERVIOUS_ID
    assert config.criteria_bands(mid, "agriculture")["Poor"] == ">50%"
    assert config.criteria_bands(mid, "impervious")["Poor"] == ">25%"
    assert config.criteria_bands(mid, None)["Good"] == "<10%"


def test_land_cover_metric_display_name_is_neutral():
    assert config.metrics_by_id()[hydrology.IMPERVIOUS_ID]["name"] == "Watershed Land-Cover Pressure"


def test_rescore_keeps_agriculture_criteria_through_override():
    # an override on an agriculture-scored row must keep the agriculture criteria bands,
    # not silently revert to the impervious thresholds.
    from easi import assessment
    mid = hydrology.IMPERVIOUS_ID
    base_report = {"metricRows": [{
        "metricId": mid, "name": "Watershed Land-Cover Pressure", "discipline": "Hydrology",
        "functionId": "catchment-hydrology", "functionName": "Catchment hydrology",
        "rating": "Poor", "generatedRating": "Poor", "criteria": ">50%",
        "criteriaBands": {"Good": "<25%", "Fair": "25%-50%", "Poor": ">50%"},
        "index": 0.195, "functionScore": 3, "valueText": "61.0% agricultural land (watershed)",
        "source": "EPA StreamCat crop+hay (watershed)", "status": "ok", "overrideable": True,
    }], "totalCount": 20}
    row = assessment.rescore(base_report, {mid: "Fair"})["metricRows"][0]
    assert row["rating"] == "Fair" and row["criteria"] == "25%-50%"


# --- detrital processing: natural riparian vegetation (forest + shrub + grassland + wetland) --- #
def test_detrital_forest_only_unchanged():
    # forest 40% (conif+decid+mxfst), no grass/shrub/wetland -> 40% natural veg -> Fair (as before)
    r = physicochemistry.detrital_cpom(_ctx(streamcat={
        "pctconif2019wsrp100": 8, "pctdecid2019wsrp100": 20, "pctmxfst2019wsrp100": 12}))
    assert r.value == 40.0 and r.rating == "Fair"
    assert r.detail["forest"] == 40.0 and r.detail["total"] == 40.0


def test_detrital_grassland_buffer_scores_good():
    # grassland-region stream: ~0 forest but a dense grass/shrub buffer -> Good (was Poor forest-only)
    r = physicochemistry.detrital_cpom(_ctx(streamcat={
        "pctgrs2019wsrp100": 55, "pctshrb2019wsrp100": 10, "pctmxfst2019wsrp100": 2}))
    assert r.value == 67.0 and r.rating == "Good"
    assert r.detail["grassland"] == 55.0 and r.detail["shrub"] == 10.0 and r.detail["forest"] == 2.0


def test_detrital_wetland_counts():
    r = physicochemistry.detrital_cpom(_ctx(streamcat={
        "pctwdwet2019wsrp100": 30, "pcthbwet2019wsrp100": 25}))  # 55 -> Good
    assert r.detail["wetland"] == 55.0 and r.rating == "Good"


@pytest.mark.parametrize("grass,expected", [(15, "Poor"), (20, "Poor"), (20.1, "Fair"),
                                            (50, "Fair"), (50.1, "Good"), (60, "Good")])
def test_detrital_bins_unchanged(grass, expected):
    # thresholds unchanged: Good >50, Fair 20-50, Poor <=20 (applied to natural-veg %)
    r = physicochemistry.detrital_cpom(_ctx(streamcat={"pctgrs2019wsrp100": grass}))
    assert r.rating == expected


def test_detrital_unavailable_without_veg_data():
    assert physicochemistry.detrital_cpom(_ctx()).status == "unavailable"


def test_detrital_note_and_kind():
    r = physicochemistry.detrital_cpom(_ctx(streamcat={"pctgrs2019wsrp100": 60}))
    assert "aerial basemap" in r.note and "natural riparian vegetation" in r.value_text
    assert r.detail["kind"] == "riparian_veg"


def test_riparian_natural_veg_pct_sums_all_groups():
    from easi.metrics import base
    v = base.riparian_natural_veg_pct(_ctx(streamcat={
        "pctmxfst2019wsrp100": 10, "pctgrs2019wsrp100": 20, "pctshrb2019wsrp100": 5,
        "pctwdwet2019wsrp100": 5}))
    assert v == 40.0
    assert base.riparian_natural_veg_pct(_ctx()) is None


# --- prefetch-both source variants (worksheet instant source swap) ---------- #
def _prefetch_ctx(**fields):
    c = _ctx(**fields)
    c.extras["prefetch_variants"] = True
    return c


def test_wetlands_prefetch_carries_both_variants():
    # StreamCat sums to 3.0 (Fair); NLCD reads 8.0 (Good) — both from prefetched data.
    ctx = _prefetch_ctx(streamcat={"pctwdwet2019ws": 2, "pcthbwet2019ws": 1},
                        landcover={"wetland_pct": 8.0})
    r = hydrology.wetlands(ctx)
    assert r.source_key == "streamcat" and r.rating == "Fair"        # auto default
    assert set(r.variants) == {"streamcat", "nlcd"}
    assert r.variants["streamcat"].rating == "Fair"
    assert r.variants["nlcd"].rating == "Good"


def test_wetlands_no_variants_without_prefetch_flag():
    r = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 2, "pcthbwet2019ws": 1}))
    assert r.variants is None and r.source_key is None                # byte-identical legacy path


def test_temperature_prefetch_one_wqp_call_both_variants(monkeypatch):
    calls = {"n": 0}

    def counted(param, lat, lon):
        calls["n"] += 1
        return 18.0                                                   # observed -> Good

    monkeypatch.setattr(physicochemistry.wqp, "median_value", counted)
    ctx = _prefetch_ctx(streamcat={"tmean8110ws": 9.0, "pctconif2019wsrp100": 30})
    r = physicochemistry.stream_temperature(ctx)
    assert calls["n"] == 1                                            # WQP called exactly once
    assert r.source_key == "wqp" and set(r.variants) == {"wqp", "surrogate"}
    assert r.variants["wqp"].rating == "Good"
    assert r.variants["surrogate"].rating in {"Good", "Fair", "Poor"}


def test_temperature_prefetch_unavailable_wqp_falls_to_surrogate(monkeypatch):
    monkeypatch.setattr(physicochemistry.wqp, "median_value", lambda *a: None)
    ctx = _prefetch_ctx(streamcat={"tmean8110ws": 9.0})
    r = physicochemistry.stream_temperature(ctx)
    assert r.source_key == "surrogate"                               # no samples -> surrogate primary
    assert r.variants["wqp"].rating is None                          # wqp variant flagged empty


def test_impairment_prefetch_one_attains_chain_both_variants(monkeypatch):
    calls = {"pt": 0, "near": 0}

    def at_pt(lat, lon):
        calls["pt"] += 1
        return {"assessment_unit": "AU1", "overallstatus": "Fully Supporting", "ircategory": "1"}

    def at_near(lat, lon):
        calls["near"] += 1
        return None

    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", at_pt)
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", at_near)
    ctx = _prefetch_ctx(streamcat={"pctimp2019ws": 3, "rddensws": 1})
    r = physicochemistry.impairment(ctx)
    assert calls["pt"] == 1 and calls["near"] == 0                   # at-point hit; no extra calls
    assert r.source_key == "attains" and set(r.variants) == {"attains", "surrogate"}
    assert r.variants["attains"].rating == "Good"

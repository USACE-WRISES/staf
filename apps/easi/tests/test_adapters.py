"""Offline tests for metric-adapter binning (no network: inject ctx.extras)."""
from __future__ import annotations

import pytest

from easi import config, geomorph, screening_methods
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
def test_wetlands_bins_streamcat(pct, expected):
    r = hydrology.wetlands(_ctx(streamcat={
        "pctwdwet2019ws": pct, "pcthbwet2019ws": 0}))
    assert r.rating == expected


# --- ATTAINS-only impairment ------------------------------------------------
def test_impairment_assessed_point(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point",
                        lambda lat, lon: {"assessment_unit": "AU1", "isimpaired": "Y",
                                          "ircategory": "5", "overallstatus": "Not Supporting",
                                          "match_type": "intersect", "distance_m": 0})
    r = physicochemistry.impairment(_ctx())
    assert r.rating == "Poor" and r.confidence == "H" and "ATTAINS" in r.source


def test_impairment_nearby_when_point_unassessed(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda lat, lon: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point",
                        lambda lat, lon: {"assessment_unit": "AU2", "isimpaired": "Y",
                                          "ircategory": "4A", "overallstatus": "Not Supporting",
                                          "match_type": "nearby", "distance_m": 850})
    r = physicochemistry.impairment(_ctx())
    assert r.rating == "Fair" and r.confidence == "M/L" and "850 m away" in r.value_text
    assert "not necessarily this reach" in r.note.lower()


def test_impairment_uses_chem_fallback_when_no_attains(monkeypatch):
    monkeypatch.setattr(physicochemistry.attains, "impairment_at_point", lambda lat, lon: {})
    monkeypatch.setattr(physicochemistry.attains, "impairment_near_point", lambda lat, lon: {})
    r = physicochemistry.impairment(_ctx(streamcat={"chemcat": 0.8, "chemws": 0.6}))
    assert r.rating == "Fair" and r.status == "ok"
    assert r.scoring["methodKey"] == "streamcat-chem-integrity-regulatory"
    assert "not a measured concentration or regulatory determination" in r.note.lower()


def test_impairment_category_three_uses_chem_fallback(monkeypatch):
    monkeypatch.setattr(
        physicochemistry.attains, "impairment_at_point",
        lambda lat, lon: {"assessment_unit": "AU1", "ircategory": "3",
                          "match_type": "intersect", "distance_m": 0})
    r = physicochemistry.impairment(_ctx(streamcat={"chemcat": 0.8, "chemws": 0.8}))
    assert r.rating == "Good"
    assert r.scoring["usedFallback"] is True


def test_wetlands_streamcat_sums_woody_and_herbaceous():
    r = hydrology.wetlands(_ctx(streamcat={"pctwdwet2019ws": 3, "pcthbwet2019ws": 4}))
    assert r.value == 7.0 and r.rating == "Good" and "StreamCat" in r.source


# --- Revised adapters (offline) -------------------------------------------- #
@pytest.mark.parametrize("fcode", [46006, 46003, 46007])
def test_low_flow_fcode_alone_never_invents_condition(fcode):
    result = hydraulics.low_flow_connectivity(_ctx(fcode=fcode))
    assert result.rating is None and result.status == "unavailable"
    assert result.scoring["completeness"] == "not_assessed"


def test_low_flow_without_sources_is_unavailable():
    assert hydraulics.low_flow_connectivity(_ctx()).status == "unavailable"


def test_low_flow_prefers_nrsa_over_hyd_fallback():
    ctx = _ctx(streamcat={"hydcat": 0.1, "hydws": 0.1}, fcode=46006)
    ctx.extras["nrsa"] = {
        "wettedPct": 100, "siteId": "SITE", "date": "2019-07-17",
        "matchType": "connected_nearby", "distanceMi": 2.1, "confidence": "M/L",
        "warning": "Connected nearby NRSA site; not necessarily this reach.",
    }
    result = hydraulics.low_flow_connectivity(ctx)
    assert result.rating == "Good"
    assert result.scoring["methodKey"] == "nrsa-wetted-channel-condition"
    assert result.scoring["completeness"] == "partial"


def test_low_flow_uses_lower_hyd_integrity_component():
    result = hydraulics.low_flow_connectivity(
        _ctx(streamcat={"hydcat": 0.8, "hydws": 0.6}, fcode=46006))
    assert result.rating == "Fair" and result.value == pytest.approx(0.6)
    assert result.scoring["usedFallback"] is True


@pytest.mark.parametrize("stor,runoff,expected", [
    (0, 1000, "Good"), (20000, 1000, "Fair"), (400000, 1000, "Poor")])
def test_flow_alteration_degree_of_regulation(stor, runoff, expected):
    r = hydrology.flow_alteration(_ctx(streamcat={
        "damnrmstorws": stor, "runoffws": runoff}))
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


def test_substrate_prefers_nrsa_then_uses_sed_integrity():
    ctx = _ctx(streamcat={"sedcat": 0.9, "sedws": 0.9})
    ctx.extras["nrsa"] = {
        "embeddednessPct": 32.4, "siteId": "SITE", "date": "2019-07-17",
        "matchType": "exact", "distanceMi": 0, "confidence": "M", "warning": "",
    }
    observed = geomorphology.substrate(ctx)
    assert observed.rating == "Fair"
    assert observed.scoring["methodKey"] == "nrsa-embeddedness-condition"
    fallback = geomorphology.substrate(
        _ctx(streamcat={"sedcat": 0.8, "sedws": 0.3}))
    assert fallback.rating == "Poor" and fallback.value == pytest.approx(0.3)
    assert fallback.scoring["usedFallback"] is True


@pytest.mark.parametrize("bhr,expected", [(1.3, "Good"), (1.31, "Fair"),
                                           (1.5, "Fair"), (1.51, "Poor")])
def test_bank_instability_bhr_proxy(bhr, expected):
    ctx = _ctx()
    ctx.extras["reach_geomorph"] = {"bank_height_ratio": bhr}
    result = geomorphology.bank_erosion(ctx)
    assert result.rating == expected
    assert result.scoring["evidenceFamily"] == "incision_geometry"
    assert result.scoring["usedFallback"] is True


def test_hyporheic_slope_sinuosity():
    good = hydraulics.hyporheic(_ctx(slope=0.012, sinuosity=1.6))
    poor = hydraulics.hyporheic(_ctx(slope=0.0005, sinuosity=1.02))
    assert good.rating == "Good" and poor.rating == "Poor"


# --- high flow dynamics: floodplain engagement from BHR directly ----------- #
def _geom_ctx(geom, slope=0.004):
    c = AnalysisContext(lat=40.0, lon=-83.0, comid=1, slope=slope)
    c.extras["reach_geomorph"] = geom
    return c


def _trapezoid_geom(bank_h, da=50.0):
    st = list(range(0, 121))
    cx, half = 60, 10
    elevs = [(abs(x - cx) / half) * bank_h if abs(x - cx) <= half else bank_h for x in st]
    return geomorph.reach_summary([(st, elevs)], da)


@pytest.mark.parametrize(
    "bhr,expected",
    [(1.0, "Good"), (1.3, "Good"), (1.31, "Fair"), (1.5, "Fair"), (1.51, "Poor")],
)
def test_floodplain_engagement_bins(bhr, expected):
    r = hydraulics.floodplain_engagement(_geom_ctx({"bank_height_ratio": bhr}))
    assert r.rating == expected
    assert r.metric_id == hydraulics.FLOODPLAIN_ENGAGEMENT_ID


@pytest.mark.parametrize(
    "bhr,expected",
    [(1.0, "Good"), (1.3, "Good"), (1.31, "Fair"), (1.5, "Fair"), (2.0, "Poor")],
)
def test_rate_engagement_bins(bhr, expected):
    rating, returned_bhr = hydraulics.rate_engagement(bhr)
    assert rating == expected and returned_bhr == bhr


def test_rate_engagement_no_bhr():
    assert hydraulics.rate_engagement(None) == (None, None)


def test_floodplain_engagement_from_bhr():
    # high bank-height ratio -> incised -> rarely engaged -> Poor
    r = hydraulics.floodplain_engagement(_geom_ctx({"bank_height_ratio": 2.0}))
    assert r.rating == "Poor" and r.status == "ok"


def test_floodplain_engagement_missing_is_unscored():
    r = hydraulics.floodplain_engagement(_geom_ctx({}))
    assert r.rating is None and r.status == "unavailable"


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
    assert geomorphology.rate_channel_evolution(1.1, 2.3) == "Good"
    assert geomorphology.rate_channel_evolution(1.5, 2.3) == "Fair"
    assert geomorphology.rate_channel_evolution(2.0, 2.3) == "Poor"
    assert geomorphology.rate_channel_evolution(None) is None


def test_rate_metrics_from_stages_updates_all_four_geometry_metrics():
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]      # deep V; banks reach 4 m
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0}
    ce = geomorphology.CHANNEL_EVOL_ID
    low = assessment.rate_metrics_from_stages(block, 1.0, 1.2)   # BHR 1.2 -> stable
    high = assessment.rate_metrics_from_stages(block, 1.0, 3.5)  # BHR 3.5 -> incised
    assert ce in low and ce in high
    assert low[ce]["rating"] == "Fair"   # BHR Good, ER Fair
    assert high[ce]["rating"] == "Poor"
    assert geomorphology.BANK_EROSION_ID in low
    assert hydraulics.FLOODPLAIN_ENGAGEMENT_ID in low
    assert hydraulics.ENTRENCHMENT_ID in high


def test_rate_metrics_from_stages_channelized_remains_poor():
    from easi import assessment
    st = list(range(-60, 61, 5))
    elevs = [min(4.0, abs(x) * 0.5) for x in st]
    block = {"stations": st, "elevs": elevs, "thalweg": 0.0, "slope": 0.004,
             "bankfull_stage": 1.0, "floodplain_stage": 1.0, "fcode": 33600}  # canal
    out = assessment.rate_metrics_from_stages(block, 1.0, 1.0)
    assert out[geomorphology.CHANNEL_EVOL_ID]["rating"] == "Poor"
    assert out[geomorphology.CHANNEL_EVOL_ID]["scoring"]["methodKey"] == "channelized-fcode"
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


# --- biological integrity evidence hierarchy ------------------------------- #
def test_biological_integrity_ignores_unrelated_landscape_values():
    r = biology.biological_integrity(_ctx(streamcat={
        "pctmxfst2019wsrp100": 80, "pctimp2019ws": 0,
        "pctcrop2019ws": 0, "rddensws": 0}))
    assert r.rating is None and r.status == "unavailable"


def test_biological_integrity_no_default_fair():
    r = biology.biological_integrity(_ctx())
    assert r.rating is None and r.status == "unavailable"


def test_biological_integrity_prefers_measured_nrsa_classes():
    ctx = _ctx(streamcat={"prg_bmmi": 0.1})
    ctx.extras["nrsa"] = {
        "benthicClass": "Good", "benthicMmi": 61,
        "fishClass": "Fair", "fishMmi": 44,
        "siteId": "SITE", "date": "2019-07-17", "matchType": "exact",
        "distanceMi": 0, "confidence": "M", "warning": "",
    }
    result = biology.biological_integrity(ctx)
    assert result.rating == "Fair"
    assert result.scoring["methodKey"] == "nrsa-biological-condition"


def test_biological_integrity_uses_prg_then_integrity_products():
    modeled = biology.biological_integrity(_ctx(streamcat={"prg_bmmi": 0.8}))
    assert modeled.rating == "Good"
    assert modeled.scoring["methodKey"] == "streamcat-prg-bmmi"
    components = {f"{component}{scale}": 0.9
                  for component in ("hyd", "chem", "sed", "conn", "temp", "habt")
                  for scale in ("cat", "ws")}
    product = biology.biological_integrity(_ctx(streamcat=components))
    assert product.rating == "Fair"
    assert product.scoring["methodKey"] == "streamcat-integrity-products"


@pytest.mark.parametrize(
    "key", ["prg_bmmi", "prgbmmi", "prg_bmmiws", "prgbmmiws", "prg_bmmicat"])
def test_prg_bmmi_is_found_under_every_streamcat_column_spelling(key):
    """A missed column would silently demote the metric to the weaker ICI/IWI tier
    instead of using the published model, so every documented spelling must resolve."""
    components = {f"{component}{scale}": 0.2
                  for component in ("hyd", "chem", "sed", "conn", "temp", "habt")
                  for scale in ("cat", "ws")}
    result = biology.biological_integrity(_ctx(streamcat={**components, key: 0.8}))
    assert result.scoring["methodKey"] == "streamcat-prg-bmmi"
    assert result.rating == "Good"          # not the Poor the ICI/IWI products would give


# --- strict component requirements and thermal-vulnerability proxy -------- #
def test_wetlands_requires_both_streamcat_components():
    r = hydrology.wetlands(_ctx(
        streamcat={"pctwdwet2019ws": 3},
        landcover={"wetland_pct": 9.0}))
    assert r.rating is None and r.status == "unavailable"


def _woody_streamcat(*, woody=0, impervious=0):
    return {
        "pctconif2019wsrp100": woody,
        "pctdecid2019wsrp100": 0,
        "pctmxfst2019wsrp100": 0,
        "pctshrb2019wsrp100": 0,
        "pctwdwet2019wsrp100": 0,
        "pctimp2019ws": impervious,
    }


@pytest.mark.parametrize(
    "woody,impervious,expected",
    [(80, 5, "Good"), (80, 20, "Fair"), (80, 30, "Poor"),
     (50, 5, "Fair"), (20, 5, "Poor")],
)
def test_thermal_vulnerability_worse_input_governs(
        monkeypatch, woody, impervious, expected):
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *a, **k: None)
    r = physicochemistry.stream_temperature(
        _ctx(streamcat=_woody_streamcat(
            woody=woody, impervious=impervious)))
    assert r.rating == expected
    assert "not stream temperature" in r.note.lower()


def test_thermal_vulnerability_requires_both_inputs(monkeypatch):
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", lambda *a, **k: None)
    incomplete = _woody_streamcat(woody=80, impervious=5)
    incomplete.pop("pctimp2019ws")
    r = physicochemistry.stream_temperature(_ctx(streamcat=incomplete))
    assert r.rating is None and r.status == "unavailable"


def test_wqp_temperature_is_context_only(monkeypatch):
    monkeypatch.setattr(
        physicochemistry.wqp, "sample_summary",
        lambda *a, **k: {"value": 30, "observation_count": 4})
    r = physicochemistry.stream_temperature(
        _ctx(streamcat=_woody_streamcat(woody=80, impervious=5)))
    assert r.rating == "Good"
    assert "not scored" in r.note.lower()


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
        return {
            "value": {"tn": 0.4, "tp": 0.03}.get(param),
            "observation_count": 2,
            "station_count": 1,
            "nearest_distance_mi": 0.5,
            "excluded_count": 0,
            "date_start": "2025-01-01",
            "date_end": "2025-02-01",
        }
    monkeypatch.setattr(physicochemistry.wqp, "sample_summary", _slow)
    monkeypatch.setattr(
        physicochemistry.geo, "nars9_at",
        lambda lat, lon: {"code": "CPL", "name": "Coastal Plains"})
    t0 = time.perf_counter()
    r = physicochemistry.nutrients(_ctx())
    elapsed = time.perf_counter() - t0
    assert r.value == {"tn": 0.4, "tp": 0.03, "region": "CPL"}
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


def test_channel_evolution_uses_bhr_and_er_proxy():
    ctx = _ctx(fcode=46006)
    ctx.extras["reach_geomorph"] = {
        "bank_height_ratio": 1.2, "entrenchment_ratio": 2.3,
        "dem_resolution_m": 1}
    result = geomorphology.channel_evolution(ctx)
    assert result.rating == "Good"
    assert result.scoring["evidenceFamily"] == "incision_geometry"


def test_channel_evolution_rates_explicit_canal():
    result = geomorphology.channel_evolution(_ctx(fcode=33600))
    assert result.rating == "Poor"
    assert "canal/ditch" in result.value_text


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
    r = hydrology.impervious(_lc_ctx(crop=5, hay=0, imp=40))
    assert r.rating == "Poor" and r.detail["governing"] == "impervious"
    assert "impervious" in r.value_text


def test_land_cover_agriculture_governs_when_worse():
    # Idaho case: impervious near-zero (Good), agriculture dominant (Poor) -> agriculture governs
    r = hydrology.impervious(_lc_ctx(crop=61, hay=0, imp=2))
    assert r.rating == "Poor" and r.detail["governing"] == "agriculture"
    assert "agricultural cover" in r.value_text and "crop + hay" in r.source


@pytest.mark.parametrize("ag,expected", [(20, "Good"), (29.99, "Good"), (30, "Fair"),
                                         (50, "Fair"), (50.1, "Poor"), (70, "Poor")])
def test_land_cover_agriculture_bins(ag, expected):
    # impervious absent -> agriculture governs and uses the 30/50 bins (Allan 2004 / Wang 1997)
    r = hydrology.impervious(_lc_ctx(crop=ag, hay=0))
    assert r.rating == expected and r.detail["governing"] == "agriculture"


def test_land_cover_tie_defaults_to_impervious():
    # both Good -> tie -> impervious (the anchor) governs
    r = hydrology.impervious(_lc_ctx(crop=10, hay=0, imp=5))
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
    r = hydrology.impervious(_lc_ctx(crop=61, hay=0, imp=2))
    assert r.detail["impervious"] == {"pct": 2.0, "rating": "Good"}
    assert r.detail["agriculture"] == {"pct": 61.0, "rating": "Poor"}
    assert "impervious cover 2.0%" in r.note
    assert "agricultural cover 61.0%" in r.note


def test_criteria_bands_indicator_aware():
    mid = hydrology.IMPERVIOUS_ID
    assert config.criteria_bands(mid, "agriculture")["Poor"] == ">50%"
    assert config.criteria_bands(mid, "impervious")["Poor"] == ">25%"
    assert config.criteria_bands(mid, None)["Good"] == "<10%"


def test_land_cover_metric_display_name_is_neutral():
    # STAF metric names are unchanged by this revision; the name already reflects that the
    # metric scores the more limiting of impervious and agricultural cover.
    name = config.metrics_by_id()[hydrology.IMPERVIOUS_ID]["name"]
    assert name == "Watershed Land-Cover Pressure"
    assert screening_methods.method_for(hydrology.IMPERVIOUS_ID)["title"] == (
        "Catchment hydrology (land-cover pressure)")


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


def _riparian_streamcat(**values):
    data = {
        "pctconif2019wsrp100": 0,
        "pctdecid2019wsrp100": 0,
        "pctmxfst2019wsrp100": 0,
        "pctshrb2019wsrp100": 0,
        "pctgrs2019wsrp100": 0,
        "pctwdwet2019wsrp100": 0,
        "pcthbwet2019wsrp100": 0,
    }
    data.update(values)
    return data


# --- organic-matter supply potential: complete riparian components --------- #
def test_detrital_forest_only_unchanged():
    r = physicochemistry.detrital_cpom(_ctx(streamcat=_riparian_streamcat(
        pctconif2019wsrp100=8, pctdecid2019wsrp100=20, pctmxfst2019wsrp100=12)))
    assert r.value == 40.0 and r.rating == "Fair"
    assert r.detail["forest"] == 40.0 and r.detail["total"] == 40.0


def test_detrital_grassland_buffer_scores_good():
    # grassland-region stream: ~0 forest but a dense grass/shrub buffer -> Good (was Poor forest-only)
    r = physicochemistry.detrital_cpom(_ctx(streamcat=_riparian_streamcat(
        pctgrs2019wsrp100=55, pctshrb2019wsrp100=10, pctmxfst2019wsrp100=2)))
    assert r.value == 67.0 and r.rating == "Good"
    assert r.detail["grassland"] == 55.0 and r.detail["shrub"] == 10.0 and r.detail["forest"] == 2.0


def test_detrital_wetland_counts():
    r = physicochemistry.detrital_cpom(_ctx(streamcat=_riparian_streamcat(
        pctwdwet2019wsrp100=30, pcthbwet2019wsrp100=25)))
    assert r.detail["wetland"] == 55.0 and r.rating == "Good"


@pytest.mark.parametrize("grass,expected", [(15, "Poor"), (20, "Poor"), (20.1, "Fair"),
                                            (50, "Fair"), (50.1, "Good"), (60, "Good")])
def test_detrital_bins_unchanged(grass, expected):
    # thresholds unchanged: Good >50, Fair 20-50, Poor <=20 (applied to natural-veg %)
    r = physicochemistry.detrital_cpom(_ctx(streamcat=_riparian_streamcat(
        pctgrs2019wsrp100=grass)))
    assert r.rating == expected


def test_detrital_unavailable_without_veg_data():
    assert physicochemistry.detrital_cpom(_ctx()).status == "unavailable"


def test_detrital_note_and_kind():
    r = physicochemistry.detrital_cpom(_ctx(streamcat=_riparian_streamcat(
        pctgrs2019wsrp100=60)))
    assert "supply potential" in r.note and "organic-matter supply potential" in r.value_text
    assert r.detail["kind"] == "riparian_veg"


def test_riparian_natural_veg_pct_sums_all_groups():
    from easi.metrics import base
    v = base.riparian_natural_veg_pct(_ctx(streamcat=_riparian_streamcat(
        pctmxfst2019wsrp100=10, pctgrs2019wsrp100=20,
        pctshrb2019wsrp100=5, pctwdwet2019wsrp100=5)))
    assert v == 40.0
    assert base.riparian_natural_veg_pct(_ctx()) is None

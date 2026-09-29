"""The Round 4 candidate knobs K1 to K5 (WP-R4k) and their parity with the base package.

Every knob is inert while its field is absent from the method files: the base package's
scores and traces are what they were. Each knob's behavior is exercised on a synthetic
method record (a copy of the active catalog the test edits), never on the shipped files.

K1  an ``applicability`` rule on a context input or on an evidence quality record withholds
    the rating as a documented gap (``completeness`` withheld, a statement, never a rating);
K2  the 3DEP cross-section provider's quality record (``geomorph.cross_section_quality``)
    reaches the evaluator as named evidence;
K3  the adapters offer the flow ratio and the width variability beside the shipped inputs,
    so a candidate record can read them against its own curve set;
K4  the ``mean_index`` operator;
K5  the rollup's completeness fields, reported only when the package or the process asks.
"""
from __future__ import annotations

import copy
import json

import pytest

from easi import config, geomorph, methods, scoring
from easi import method_package as mp
from easi import screening_methods as sm
from easi.metrics import base, biology, geomorphology, hydraulics, hydrology
from easi.national import client, records
from test_batch_parity import EROM, STREAMCAT
from test_national_preloaded import _record

LOW_FLOW = hydraulics.LOW_FLOW_ID
BHR_METHOD = hydraulics.FLOODPLAIN_ENGAGEMENT_ID
CHANNEL = geomorphology.CHANNEL_EVOL_ID
CATCHMENT = hydrology.IMPERVIOUS_ID
HABITAT = biology.HABITAT_ID
E1_STATEMENT = ("Naturally intermittent or ephemeral reach (NHDPlus FCODE {value}); low-flow "
                "condition is not rated from flow variability.")
KNOWN_COMPLETENESS = {"complete", "partial", "not_assessed", "context_only"}


@pytest.fixture
def catalog(monkeypatch):
    """A deep copy of the active catalog the test may edit; the evaluator reads the copy."""
    data = copy.deepcopy(config.screening_methods())
    monkeypatch.setattr(sm, "catalog", lambda: data)
    return data


def _method(catalog: dict, key: str) -> dict:
    for m in catalog["methods"]:
        if m["methodKey"] == key:
            return m
        for v in m.get("variants") or []:
            if v["methodKey"] == key:
                return v
    raise KeyError(key)


def _ctx(**overrides):
    return records.build_context(_record(**overrides))


def _reach_geom(bhr: float = 1.1, er: float = 2.5, *, n: int = 9, capped: int = 0,
                detection: str = "slope_break", widths=None, extrapolated: bool = False,
                depth: float = 0.5, depths=None, dem_res: int = 10) -> dict:
    """A stored-evidence geometry the way the builder slims it: the reach medians and
    statistics on top, the sections' scalars under ``candidate_scalars``. The first
    ``capped`` sections hit the floodprone cap; each section carries its bankfull depth
    (``depth`` for all, or ``depths`` per section) and the reach its DEM resolution."""
    widths = list(widths) if widths is not None else [10.0 + i for i in range(n)]
    depths = list(depths) if depths is not None else [depth] * len(widths)
    scalars = [{"bank_height_ratio": bhr, "entrenchment_ratio": er, "bankfull_width_m": w,
                "bankfull_depth_m": d, "low_bank_capped": i < capped, "edge_limited": False,
                "bank_detection": detection, "position_frac": (i + 1) / (n + 1)}
               for i, (w, d) in enumerate(zip(widths, depths))]
    median_capped = bool(capped and bhr >= geomorph.BHR_CAP - 1e-9)
    return {"entrenchment_ratio": er, "bank_height_ratio": bhr, "edge_limited": False,
            "dem_resolution_m": dem_res, "n_transects": n, "selected": 0,
            "bankfull_extrapolated": extrapolated,
            "reach": {"n": n,
                      "entrenchment_ratio": {"median": er, "min": er, "max": er, "n": n},
                      "bank_height_ratio": {"median": bhr, "min": bhr, "max": bhr, "n": n,
                                            "capped": capped, "median_capped": median_capped,
                                            "max_capped": median_capped}},
            "candidate_scalars": scalars, "candidates": []}


# --------------------------------------------------------------------------- #
# parity: nothing moves while every knob is absent
# --------------------------------------------------------------------------- #
def test_the_base_package_carries_no_knob_and_its_traces_are_unchanged():
    data = config.screening_methods()
    for m in data["methods"]:
        for record in (m, *(m.get("variants") or [])):
            assert "applicability" not in record and "statement" not in record
    assert "rollupReporting" not in data
    ev = sm.evaluate(CATCHMENT, {"impervious": 2, "agriculture": 61})
    assert "applicability" not in ev.trace and "statement" not in ev.trace
    assert ev.trace["governingInput"] == "agriculture" and ev.rating == "Poor"
    report = client.score_record(_record(), cross_section=False)
    assert "functionsRated" not in report and "ecosystemConditionIndexInterval" not in report
    for row in report["metricRows"]:
        trace = row.get("scoring") or {}
        assert "applicability" not in trace and "statement" not in trace
        assert trace.get("completeness") in KNOWN_COMPLETENESS
    files = mp.package_from_dir(mp.builtin_data_dir()).files
    assert mp.requirements(files)["behaviors"] == list(mp.BEHAVIORS)
    assert "mean_index" not in mp.requirements(files)["operators"]


# --------------------------------------------------------------------------- #
# K1: applicability rules
# --------------------------------------------------------------------------- #
def test_an_input_rule_withholds_the_excluded_reach_and_rates_the_rest(catalog):
    values = {"flowCv": 0.5, "meanAnnualFlow": 400.0}
    before = sm.evaluate(LOW_FLOW, {**values, "fcodeContext": 46003})
    assert before.rating in ("Good", "Fair", "Poor")
    _method(catalog, "erom-flow-variability")["applicability"] = {
        "input": "fcodeContext", "exclude": ["46003", "46007"], "statement": E1_STATEMENT}
    withheld = sm.evaluate(LOW_FLOW, {**values, "fcodeContext": 46003})
    assert withheld.rating is None and withheld.index is None
    trace = withheld.trace
    assert trace["completeness"] == sm.WITHHELD and trace["generatedRating"] is None
    assert trace["usedFallback"] is False
    assert trace["applicability"]["withheld"] is True
    assert trace["applicability"]["matched"] == {"input": "fcodeContext", "value": 46003}
    assert trace["applicability"]["rule"]["exclude"] == ["46003", "46007"]
    assert trace["statement"] == E1_STATEMENT.replace("{value}", "46003")
    assert "FCODE 46003" in trace["statement"]
    # a float read back from a parquet row matches the same token
    assert sm.evaluate(LOW_FLOW, {**values, "fcodeContext": 46007.0}).rating is None
    # a perennial reach, or an unknown regime, rates exactly as before
    for fcode in (46006, None, "46006"):
        rated = sm.evaluate(LOW_FLOW, {**values, "fcodeContext": fcode})
        assert rated.rating == before.rating
        assert rated.trace["applicability"]["withheld"] is False
        assert "statement" not in rated.trace and rated.trace["completeness"] == "complete"
    assert sm.validate_catalog() == []


def test_a_rule_without_a_statement_uses_the_default_gap_statement(catalog):
    _method(catalog, "erom-flow-variability")["applicability"] = {
        "input": "fcodeContext", "exclude": [46003]}
    ev = sm.evaluate(LOW_FLOW, {"flowCv": 0.5, "fcodeContext": 46003})
    assert ev.rating is None and ev.trace["statement"] == sm.DEFAULT_GAP_STATEMENT


def test_an_evidence_rule_withholds_on_the_named_flags_of_the_rated_quantities(catalog):
    rule = {"evidence": "crossSectionQuality", "withhold_when": ["low_quality", "out_of_range"],
            "statement": "3DEP cross-section geometry withheld ({value})."}
    _method(catalog, "bank-height-ratio")["applicability"] = dict(rule)
    _method(catalog, "channel-adjustment-susceptibility")["applicability"] = dict(rule)
    good = {"crossSectionQuality": {"flags": [], "reasons": {}}}
    bhr_bad = {"crossSectionQuality": {"flags": ["out_of_range_bhr"],
                                       "reasons": {"out_of_range_bhr": "BHR 2.50 is outside 0 to 2"}}}
    er_bad = {"crossSectionQuality": {"flags": ["out_of_range_er"], "reasons": {}}}
    weak = {"crossSectionQuality": {"flags": ["low_quality"],
                                    "reasons": {"low_quality": "only 2 of 9 sections carry a ratio"}}}
    # no record, or a clean record: rated, and the trace says the rule was checked
    rated = sm.evaluate(BHR_METHOD, {"bhr": 1.1})
    assert rated.rating == "Good" and rated.trace["applicability"]["withheld"] is False
    rated = sm.evaluate(BHR_METHOD, {"bhr": 1.1}, evidence=good)
    assert rated.rating == "Good"
    assert rated.trace["applicability"]["checked"] == {"evidence": "crossSectionQuality", "flags": []}
    # the BHR method is withheld for its own ratio and for a low-quality record
    withheld = sm.evaluate(BHR_METHOD, {"bhr": 2.5}, evidence=bhr_bad)
    assert withheld.rating is None and withheld.trace["completeness"] == sm.WITHHELD
    assert withheld.trace["applicability"]["matched"]["flags"] == ["out_of_range_bhr"]
    assert withheld.trace["statement"] == "3DEP cross-section geometry withheld (BHR 2.50 is outside 0 to 2)."
    assert withheld.trace["usedFallback"] is False
    withheld = sm.evaluate(BHR_METHOD, {"bhr": 1.1}, evidence=weak)
    assert withheld.rating is None and "only 2 of 9" in withheld.trace["statement"]
    # ... never for the entrenchment ratio it does not read
    assert sm.evaluate(BHR_METHOD, {"bhr": 1.1}, evidence=er_bad).rating == "Good"
    # the method reading both ratios is withheld for either
    both = sm.evaluate(CHANNEL, {"bhr": 1.1, "er": 0.8, "fcodeContext": 46006},
                       evidence=er_bad, used_fallback=True)
    assert both.rating is None and both.trace["usedFallback"] is False
    assert both.trace["applicability"]["matched"]["flags"] == ["out_of_range_er"]
    assert sm.validate_catalog() == []


def test_a_variant_never_inherits_the_parents_rule(catalog):
    _method(catalog, "channel-adjustment-susceptibility")["applicability"] = {
        "evidence": "crossSectionQuality", "withhold_when": ["low_quality"]}
    flagged = {"crossSectionQuality": {"flags": ["low_quality"], "reasons": {}}}
    canal = sm.evaluate(CHANNEL, {"fcode": 33600}, variant_key="channelized-fcode",
                        evidence=flagged)
    assert canal.rating == "Poor" and "applicability" not in canal.trace
    proxy = sm.evaluate(CHANNEL, {"bhr": 1.1, "er": 2.5, "fcodeContext": 46006}, evidence=flagged)
    assert proxy.rating is None and proxy.trace["completeness"] == sm.WITHHELD


def test_validate_catalog_reports_a_malformed_rule(catalog):
    m = _method(catalog, "erom-flow-variability")
    m["applicability"] = {"input": "fcodeContext"}
    assert any("exclude" in p for p in sm.validate_catalog())
    m["applicability"] = {"input": "nothing", "exclude": ["1"]}
    assert any("not an input" in p for p in sm.validate_catalog())
    m["applicability"] = {"input": "fcodeContext", "exclude": ["1"], "evidence": "x",
                          "withhold_when": ["a"]}
    assert any("exactly one" in p for p in sm.validate_catalog())
    m["applicability"] = {"evidence": "crossSectionQuality", "withhold_when": []}
    assert any("withhold_when" in p for p in sm.validate_catalog())
    m["applicability"] = {"input": "fcodeContext", "exclude": ["46003"], "statement": 3}
    assert any("statement" in p for p in sm.validate_catalog())
    m["applicability"] = {"input": "fcodeContext", "exclude": ["46003"]}
    assert sm.validate_catalog() == []
    catalog["rollupReporting"] = "yes"
    assert any("rollupReporting" in p for p in sm.validate_catalog())


def test_the_low_flow_adapter_reports_the_documented_gap(catalog):
    _method(catalog, "erom-flow-variability")["applicability"] = {
        "input": "fcodeContext", "exclude": ["46003", "46007"], "statement": E1_STATEMENT}
    withheld = hydraulics.low_flow_connectivity(_ctx(fcode=46003))
    assert withheld.rating is None and withheld.status == "unavailable"
    assert withheld.note == E1_STATEMENT.replace("{value}", "46003")
    assert withheld.value_text == "not rated (documented gap)"
    assert withheld.scoring["completeness"] == sm.WITHHELD
    rated = hydraulics.low_flow_connectivity(_ctx(fcode=46006))
    assert rated.rating in ("Good", "Fair", "Poor") and rated.status == "ok"
    assert rated.value_text.startswith("EROM monthly flow variability")


def test_an_unscored_method_with_a_statement_is_a_documented_gap(catalog):
    fallback = _method(catalog, "streamcat-integrity-products")
    fallback.update(operator="unscored", statement="Population support is not rated where the "
                    "benthic model has no value.")
    fallback.pop("formula", None)
    ev = sm.evaluate(biology.BIOINTEGRITY_ID, {"hydCat": 0.5}, variant_key="streamcat-integrity-products")
    assert ev.rating is None and ev.trace["completeness"] == sm.WITHHELD
    assert ev.trace["statement"].startswith("Population support is not rated")
    assert ev.trace["equation"] == "No automated rating"
    res = base.unavailable(biology.BIOINTEGRITY_ID, "the components are required", "L", scoring=ev.trace)
    assert res.note == ev.trace["statement"] and res.value_text == "not rated (documented gap)"
    # the parent record (the model route) is untouched
    assert sm.evaluate(biology.BIOINTEGRITY_ID, {"prGBmmi": 0.7}).rating == "Good"
    assert sm.validate_catalog() == []


# --------------------------------------------------------------------------- #
# K2: the cross-section quality record
# --------------------------------------------------------------------------- #
def test_cross_section_quality_reads_what_the_provider_records():
    clean = geomorph.cross_section_quality(_reach_geom())
    assert clean["quality"] == "K2b" == geomorph.QUALITY_RULES
    assert clean["flags"] == [] and clean["reasons"] == {} and clean["matched"] == {}
    assert clean["sections"] == 9 and clean["bhrSections"] == 9 and clean["erSections"] == 9
    assert clean["detection"] == "slope_break" and clean["bhrInRange"] and clean["erInRange"]
    assert clean["demResolutionM"] == 10 and clean["profilePoints"] is None
    assert clean["capUnreachableDepthM"] == 0.15 and clean["capUnreachableSections"] == 0
    # K2b: a median at the floodprone cap is severe incision ("at least 2"), never a flag ...
    at_cap = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=5))
    assert at_cap["flags"] == [] and at_cap["bhrAtCap"] and at_cap["medianCapped"]
    assert at_cap["cappedSections"] == 5 and at_cap["bhrInRange"] is True and at_cap["capIsFloor"] is False
    # ... and a ratio above the cap (a hand-set low bank) is inside the physical range too
    assert geomorph.cross_section_quality(_reach_geom(bhr=2.5))["flags"] == []
    # physically impossible: a bank-height ratio at or below zero, an entrenchment ratio below 1
    out = geomorph.cross_section_quality(_reach_geom(bhr=0.0))
    assert out["flags"] == ["out_of_range_bhr"] and out["bhrInRange"] is False
    assert out["matched"] == {"out_of_range_bhr": ["bhr_at_or_below_0"]}
    assert "0.00" in out["reasons"]["out_of_range_bhr"]
    out = geomorph.cross_section_quality(_reach_geom(er=0.8))
    assert out["flags"] == ["out_of_range_er"] and out["erInRange"] is False
    assert out["matched"] == {"out_of_range_er": ["er_below_1"]}
    # low quality on the reach's own terms: too few sections, extrapolated bankfull, the
    # crest scan everywhere
    out = geomorph.cross_section_quality(_reach_geom(n=2))
    assert out["flags"] == ["low_quality"] and "only 2 of 2" in out["reasons"]["low_quality"]
    assert out["matched"]["low_quality"] == ["few_sections:bhr", "few_sections:er"]
    out = geomorph.cross_section_quality(_reach_geom(extrapolated=True))
    assert out["flags"] == ["low_quality"] and out["matched"]["low_quality"] == ["bankfull_extrapolated"]
    assert "Bieger" in out["reasons"]["low_quality"]
    out = geomorph.cross_section_quality(_reach_geom(detection="crest_scan"))
    assert out["flags"] == ["low_quality"] and out["detection"] == "crest_scan"
    assert out["matched"]["low_quality"] == ["crest_scan"]
    # several flags at once, the composition listed
    out = geomorph.cross_section_quality(_reach_geom(bhr=0.0, er=0.5, n=1))
    assert out["flags"] == ["out_of_range_bhr", "out_of_range_er", "low_quality"]
    # a legacy single-section stub (the parity fixture): one section, method unknown
    stub = geomorph.cross_section_quality({"entrenchment_ratio": 2.5, "bank_height_ratio": 1.1,
                                           "edge_limited": False, "dem_resolution_m": 10})
    assert stub["sections"] == 1 and stub["detection"] == "unknown"
    assert stub["flags"] == ["low_quality"] and stub["capUnreachableSections"] == 0
    assert geomorph.cross_section_quality({}) is None and geomorph.cross_section_quality(None) is None
    assert base.xs_evidence({}) is None
    assert base.xs_evidence(_reach_geom())["crossSectionQuality"]["flags"] == []


def test_the_cap_is_a_flag_only_where_the_dem_cannot_place_a_bank_below_it():
    """The bank detector considers a bank only after the profile has climbed the DEM's
    noise floor (0.3 m on a 10 m model, 0.1 m on lidar); the cap is twice the bankfull
    depth, so a section at or below half the floor (0.15 m, 0.05 m) reads 2.00 by
    construction. A median at the cap carried only by such sections is low quality; one the
    deeper capped sections reach on their own is the channel's incision, rated."""
    assert geomorph.cap_unreachable_depth(10) == pytest.approx(0.15)
    assert geomorph.cap_unreachable_depth(3) == pytest.approx(0.15)
    assert geomorph.cap_unreachable_depth(1) == pytest.approx(0.05)
    assert geomorph.cap_unreachable_depth(None) == pytest.approx(0.15)
    floor = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=5, depth=0.12))
    assert floor["flags"] == ["low_quality"] and floor["matched"]["low_quality"] == ["cap_unreachable"]
    assert floor["capUnreachableSections"] == 5 and floor["capIsFloor"] and floor["bhrAtCap"]
    assert "0.15 m" in floor["reasons"]["low_quality"] and "10 m DEM" in floor["reasons"]["low_quality"]
    assert "5 of 5 capped sections" in floor["reasons"]["low_quality"]
    # nine sections: five capped values put the median at the cap; four shallow and one
    # deep capped section leave one genuine section, fewer than the five it takes
    depths = [0.12, 0.12, 0.12, 0.12, 0.5, 0.5, 0.5, 0.5, 0.5]
    mixed = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=5, depths=depths))
    assert mixed["flags"] == ["low_quality"] and mixed["capUnreachableSections"] == 4
    # six capped sections of which one is shallow: five genuine, the cap is the channel's
    depths = [0.12, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    genuine = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=6, depths=depths))
    assert genuine["flags"] == [] and genuine["capUnreachableSections"] == 1 and genuine["bhrAtCap"]
    # an even count needs both middle values at the cap: eight sections, five capped
    even = geomorph.cross_section_quality(_reach_geom(bhr=2.0, n=8, capped=5, depths=[0.12] * 4 + [0.5] * 4))
    assert even["flags"] == ["low_quality"]          # one genuine capped section, five needed
    even = geomorph.cross_section_quality(_reach_geom(bhr=2.0, n=8, capped=5, depths=[0.5] * 8))
    assert even["flags"] == []
    # lidar resolves the same 0.12 m sections: its floor is 0.05 m
    lidar = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=5, depth=0.12, dem_res=1))
    assert lidar["flags"] == [] and lidar["capUnreachableDepthM"] == 0.05
    # a median below the cap never trips the rule, however shallow the capped sections
    assert geomorph.cross_section_quality(_reach_geom(bhr=1.4, capped=2, depth=0.12))["flags"] == []
    # a section without a recorded depth is never counted as unreachable
    no_depth = _reach_geom(bhr=2.0, capped=5)
    for s in no_depth["candidate_scalars"]:
        s.pop("bankfull_depth_m")
    assert geomorph.cross_section_quality(no_depth)["flags"] == []
    # the floor rule stacks with the others and the composition is recorded in order
    both = geomorph.cross_section_quality(_reach_geom(bhr=2.0, capped=5, depth=0.12, extrapolated=True))
    assert both["matched"]["low_quality"] == ["bankfull_extrapolated", "cap_unreachable"]


def test_a_hand_entered_section_is_valid_by_definition(catalog):
    """An edited or scrolled cross section (``rate_metrics_from_stages``) carries no
    evidence record, so no K2b flag applies even with the E5b rule installed: a low bank
    set at the floodprone stage is a bank-height ratio of 2.0, rated Poor."""
    from easi import assessment
    rule = {"evidence": "crossSectionQuality", "withhold_when": ["low_quality", "out_of_range"],
            "statement": "3DEP cross-section geometry withheld ({value})."}
    for key in ("bank-height-ratio", "entrenchment-ratio", "bhr-bank-instability-susceptibility",
                "channel-adjustment-susceptibility"):
        _method(catalog, key)["applicability"] = dict(rule)
    block = {"stations": [-10.0, -5.0, 0.0, 5.0, 10.0], "elevs": [102.0, 101.0, 100.0, 101.0, 102.0],
             "thalweg": 100.0, "fcode": 46006, "label": "700 ft"}
    out = assessment.rate_metrics_from_stages(block, 100.5, 101.0)
    assert out[BHR_METHOD]["rating"] == "Poor" and "bank-height ratio 2.0" in out[BHR_METHOD]["valueText"]
    assert out[hydraulics.ENTRENCHMENT_ID]["rating"] == "Good"
    assert out[CHANNEL]["rating"] == "Poor"
    for item in out.values():
        trace = item["scoring"]
        assert trace["completeness"] == "complete" and "statement" not in trace
        assert trace["applicability"]["withheld"] is False and "checked" not in trace["applicability"]
    # the same value on a stored record scores the same rating whether or not a rule exists
    # when the record is clean; the rule reads only the evidence record
    ctx = _ctx()
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=2.0, capped=6, depth=0.5)
    assert hydraulics.floodplain_engagement(ctx).rating == "Poor"


def test_the_quality_record_never_moves_a_score_without_a_rule():
    """Parity: with no applicability rule in the catalog the K2b record is inert; a stored
    record whose median sits at the cap on unresolvable sections still rates Poor, and no
    trace carries the record."""
    geom = _reach_geom(bhr=2.0, capped=9, depth=0.12)
    report = client.score_record(_record(geomorph=geom), cross_section=False)
    rows = {r["metricId"]: r for r in report["metricRows"]}
    assert rows[BHR_METHOD]["rating"] == "Poor" and rows[CHANNEL]["rating"] == "Poor"
    for row in report["metricRows"]:
        trace = row.get("scoring") or {}
        assert "applicability" not in trace and "statement" not in trace
    quality = geomorph.cross_section_quality(geom)
    assert quality["flags"] == ["low_quality"] and quality["capIsFloor"]


def test_summarize_profile_records_how_the_bank_was_found():
    stations = [-30.0, -20.0, -10.0, -6.0, -3.0, 0.0, 3.0, 6.0, 10.0, 20.0, 30.0]
    elevs = [102.0, 101.9, 101.8, 101.2, 100.4, 100.0, 100.4, 101.2, 101.8, 101.9, 102.0]
    s = geomorph.summarize_profile(stations, elevs, 50.0, bankfull=(10.0, 1.0))
    assert s["bank_detection"] in ("slope_break", "crest_scan")
    quality = geomorph.cross_section_quality(s)
    assert quality["detection"] == s["bank_detection"] and quality["profilePoints"] == 11


def test_bankfull_width_cv_is_the_builders_quantity():
    import numpy as np
    widths = [10.0, 12.0, 14.0, 9.0]
    got = geomorph.bankfull_width_cv(_reach_geom(widths=widths))
    arr = np.asarray(widths)
    assert got == pytest.approx(float(arr.std(ddof=0) / arr.mean()))
    assert geomorph.bankfull_width_cv(_reach_geom(widths=[10.0])) is None
    assert geomorph.bankfull_width_cv({"candidate_scalars": [{"bankfull_width_m": None}] * 3}) is None
    assert geomorph.bankfull_width_cv({}) is None and geomorph.bankfull_width_cv(None) is None
    live = {"candidates": [{"bankfull_width_m": w} for w in widths]}
    assert geomorph.bankfull_width_cv(live) == got


def test_the_cross_section_adapters_offer_the_quality_record(catalog):
    for key in ("bank-height-ratio", "entrenchment-ratio", "bhr-bank-instability-susceptibility",
                "channel-adjustment-susceptibility"):
        _method(catalog, key)["applicability"] = {
            "evidence": "crossSectionQuality", "withhold_when": ["low_quality", "out_of_range"],
            "statement": "3DEP cross-section geometry withheld ({value})."}
    ctx = _ctx()
    bhr_adapters = (hydraulics.floodplain_engagement, geomorphology.bank_erosion,
                    geomorphology.channel_evolution)
    # K2b: a median at the cap on sections the DEM resolves is severe incision, rated Poor
    # by the three BHR methods ("at least 2.00"), never withheld
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=2.0, capped=6, depth=0.5)
    for adapter in bhr_adapters:
        res = adapter(ctx)
        assert res.rating == "Poor" and res.scoring["completeness"] == "complete"
        assert res.scoring["applicability"]["withheld"] is False
        assert res.scoring["applicability"]["checked"]["flags"] == []
        assert "at least 2.00" in res.value_text
    assert hydraulics.floodplain_access(ctx).rating == "Good"
    # the same median carried only by sections the DEM cannot resolve is low quality: every
    # cross-section method is withheld with the reason in its note
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=2.0, capped=6, depth=0.12)
    for adapter in (*bhr_adapters, hydraulics.floodplain_access):
        res = adapter(ctx)
        assert res.rating is None and res.scoring["completeness"] == sm.WITHHELD
        assert res.note.startswith("3DEP cross-section geometry withheld (the reach median bank-height "
                                   "ratio sits at the 2 cap only with sections whose bankfull depth")
        assert res.scoring["applicability"]["matched"]["flags"] == ["low_quality"]
        assert res.scoring["applicability"]["matched"]["record"]["matched"]["low_quality"] == ["cap_unreachable"]
    # an impossible bank-height ratio withholds the BHR methods; the entrenchment ratio of
    # the same reach is inside its range and rated
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=0.0)
    for adapter in bhr_adapters:
        res = adapter(ctx)
        assert res.rating is None and res.scoring["completeness"] == sm.WITHHELD
        assert res.note.startswith("3DEP cross-section geometry withheld (bank-height ratio 0.00 is at or below zero")
    assert hydraulics.floodplain_access(ctx).rating == "Good"
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=1.1, er=2.5)
    assert hydraulics.floodplain_engagement(ctx).rating == "Good"
    assert geomorphology.channel_evolution(ctx).rating == "Good"
    assert hydraulics.floodplain_access(ctx).rating == "Good"


# --------------------------------------------------------------------------- #
# K3: the candidate quantities the adapters offer, and records that read them
# --------------------------------------------------------------------------- #
def test_monthly_flow_min_ratio_is_the_builders_quantity():
    months = [EROM[f"qe_{m:02d}"] for m in range(1, 13)]
    assert hydraulics.monthly_flow_min_ratio(EROM) == pytest.approx(min(months) / EROM["qe_ma"])
    assert hydraulics.monthly_flow_min_ratio({**EROM, "qe_07": None}) is None
    assert hydraulics.monthly_flow_min_ratio({**EROM, "qe_ma": 0.0}) is None
    assert hydraulics.monthly_flow_min_ratio({**EROM, "qe_ma": float("nan")}) is None
    assert hydraulics.monthly_flow_min_ratio(None) is None


def _candidate_set(quantity: str) -> dict:
    return {"higherIsBetter": True, "quantity": quantity, "stratifier": "nars9",
            "curves": {"national": {"points": [[0.0, 0.0], [0.2, 0.39], [0.5, 0.69], [1.0, 1.0]],
                                    "n": 40, "nMembers": 40, "q25": 0.2, "q50": 0.35, "q75": 0.5,
                                    "x39": 0.2, "x69": 0.5, "status": "complete",
                                    "panelTier": "complete", "screen": "strict"}}}


@pytest.fixture
def candidate_sets(monkeypatch):
    sets = dict(sm.curve_sets())
    sets["flow-min-ratio"] = _candidate_set("q_min_ratio")
    sets["width-variability"] = _candidate_set("bankfull_width_cv")
    monkeypatch.setattr(sm, "curve_sets", lambda: sets)
    return sets


def test_a_candidate_record_reads_the_flow_ratio_against_its_set(catalog, candidate_sets):
    m = _method(catalog, "erom-flow-variability")
    m["inputs"] = [{"key": "flowMinRatio", "label": "Minimum-month flow ratio", "required": True,
                    "symbol": "Qmin/Qma", "units": "ratio", "rationale": "candidate"},
                   *[i for i in m["inputs"] if i.get("contextOnly")]]
    m["curve"] = {"set": "flow-min-ratio", "stratifier": "nars9", "fallback": ["national"],
                  "mode": "banded"}
    assert sm.validate_catalog() == []
    ratio = hydraulics.monthly_flow_min_ratio(EROM)
    ev = sm.evaluate(LOW_FLOW, {"flowCv": 0.7, "flowMinRatio": ratio, "fcodeContext": 46006},
                     context={"strata": {"nars9": "TPL"}})
    assert ev.combined_value == ratio
    assert ev.rating == ("Good" if ratio >= 0.5 else "Fair" if ratio >= 0.2 else "Poor")
    assert ev.trace["curves"]["method"]["set"] == "flow-min-ratio"
    assert base.rated_keys(ev.trace) == ["flowMinRatio"]
    res = hydraulics.low_flow_connectivity(_ctx())
    assert res.rating == ev.rating and res.value == ratio
    assert res.value_text.startswith("EROM minimum-month over mean annual flow ratio")
    missing = hydraulics.low_flow_connectivity(_ctx(erom=None))
    assert missing.rating is None and "mean annual flow" in missing.note


def test_a_candidate_record_reads_width_variability_against_its_set(catalog, candidate_sets):
    m = _method(catalog, "habitat-support-potential")
    m["inputs"] = [{"key": "widthCv", "label": "Bankfull width variability", "required": True,
                    "symbol": "CVw", "units": "ratio", "rationale": "candidate"},
                   *[i for i in m["inputs"] if i.get("contextOnly")]]
    m["curve"] = {"set": "width-variability", "stratifier": "nars9", "fallback": ["national"],
                  "mode": "banded"}
    assert sm.validate_catalog() == []
    ctx = _ctx()
    ctx.extras["reach_geomorph"] = _reach_geom(widths=[10.0, 20.0, 30.0])
    cv = geomorph.bankfull_width_cv(ctx.extras["reach_geomorph"])
    res = biology.habitat_complexity(ctx)
    assert res.value == pytest.approx(round(cv, 4)) and res.rating == "Fair"      # cv ~ 0.41
    assert res.value_text.startswith("bankfull width variability (CV)") and "3 sections" in res.value_text
    assert base.rated_keys(res.scoring) == ["widthCv"]
    assert res.scoring["curves"]["method"]["set"] == "width-variability"
    # a reach without sections loses the rating, as the family says
    ctx.extras["reach_geomorph"] = {}
    gone = biology.habitat_complexity(ctx)
    assert gone.rating is None and "width variability" in gone.note


def test_the_shipped_records_ignore_the_offered_quantities():
    res = hydraulics.low_flow_connectivity(_ctx())
    assert base.rated_keys(res.scoring) == ["flowCv"]
    assert res.value_text.startswith("EROM monthly flow variability")
    keys = [x["key"] for x in res.scoring["inputs"]]
    assert "flowMinRatio" not in keys
    res = biology.habitat_complexity(_ctx())
    assert base.rated_keys(res.scoring) == ["woodyRiparian"]
    assert "widthCv" not in [x["key"] for x in res.scoring["inputs"]]


# --------------------------------------------------------------------------- #
# K4: the mean_index operator
# --------------------------------------------------------------------------- #
def test_mean_index_averages_the_anchor_indices_and_bands_the_mean(catalog):
    m = _method(catalog, "catchment-land-cover-pressure")
    m["operator"] = "mean_index"
    assert sm.validate_catalog() == []
    good_poor = sm.evaluate(CATCHMENT, {"impervious": 2, "agriculture": 61})
    assert good_poor.rating == "Fair" and good_poor.combined_value == pytest.approx((0.85 + 0.195) / 2)
    assert good_poor.trace["governingInput"] is None
    assert good_poor.trace["completeness"] == "complete"
    assert {x["key"]: x["rating"] for x in good_poor.trace["inputs"]} == {
        "impervious": "Good", "agriculture": "Poor"}
    assert sm.evaluate(CATCHMENT, {"impervious": 2, "agriculture": 40}).rating == "Good"     # 0.6975
    assert sm.evaluate(CATCHMENT, {"impervious": 15, "agriculture": 61}).rating == "Poor"    # 0.37
    assert sm.evaluate(CATCHMENT, {"impervious": 15, "agriculture": 40}).rating == "Fair"
    partial = sm.evaluate(CATCHMENT, {"impervious": 2, "agriculture": None})
    assert partial.rating == "Good" and partial.trace["completeness"] == "partial"
    assert sm.equation_for(m) == "Icombined = mean(Iimpervious, Iagriculture)"
    assert [a["input"] for a in sm.criteria_for(m)["automated"]] == ["impervious", "agriculture"]
    assert methods._mode(m) == "mean"
    projected = methods._project(m)
    assert projected.mode == "mean" and len(projected.per_input) == 2
    assert methods.evaluate_method(projected, {"impervious": 2, "agriculture": 61})["rating"] == "Fair"
    # the worst-of composite on the same case rates Poor: E7 differs where it should
    m["operator"] = "worst_index"
    assert sm.evaluate(CATCHMENT, {"impervious": 2, "agriculture": 61}).rating == "Poor"


def test_the_composite_adapters_describe_a_mean(catalog):
    for key in ("catchment-land-cover-pressure", "sediment-supply-potential",
                "hyporheic-exchange-potential", "channel-adjustment-susceptibility",
                "thermal-regulation-vulnerability"):
        _method(catalog, key)["operator"] = "mean_index"
    ctx = _ctx(streamcat={**STREAMCAT, "pctimp2019ws": 2.0, "pctcrop2019ws": 60.0,
                          "pcthay2019ws": 5.0})
    res = hydrology.impervious(ctx)
    assert res.rating == "Fair" and res.value_text.startswith("mean rating index 0.52")
    assert "impervious Good, agriculture Poor" in res.value_text and res.detail["governing"] is None
    res = geomorphology.sediment_supply(ctx)
    assert res.value_text.startswith("mean rating index") and res.rating in ("Good", "Fair", "Poor")
    res = hydraulics.hyporheic(ctx)
    assert "mean of the pathway indices" in res.value_text
    ctx.extras["reach_geomorph"] = _reach_geom(bhr=1.1, er=2.5)
    res = geomorphology.channel_evolution(ctx)
    assert res.value_text.endswith("mean of the BHR and ER indices")


def test_mean_index_is_a_package_capability_an_older_evaluator_refuses():
    assert "mean_index" in mp.OPERATORS and set(mp.OPERATORS) == set(sm.VALID_OPERATORS)
    files = dict(mp.package_from_dir(mp.builtin_data_dir()).files)
    cat = json.loads(files["screening-methods.json"].decode("utf-8"))
    for m in cat["methods"]:
        if m["methodKey"] == "catchment-land-cover-pressure":
            m["operator"] = "mean_index"
            m["applicability"] = {"input": "impervious", "exclude": ["-1"]}
    cat["rollupReporting"] = True
    files["screening-methods.json"] = json.dumps(cat).encode("utf-8")
    req = mp.requirements(files)
    assert "mean_index" in req["operators"]
    assert req["behaviors"] == [*mp.BEHAVIORS, "applicability-rules", "rollup-completeness-interval"]
    mp.check_requirements({"evaluator": {"requires": req}})       # this evaluator provides them
    with pytest.raises(mp.MethodPackageError, match="behaviors"):
        mp.check_requirements({"evaluator": {"requires": {"behaviors": ["some-other-rule"]}}})


# --------------------------------------------------------------------------- #
# K5: rollup reporting
# --------------------------------------------------------------------------- #
def test_rollup_reports_completeness_and_an_interval_that_brackets_the_point():
    mapping = config.cwa_mapping()
    one = scoring.rollup({"catchment-hydrology": 8})
    assert one.functions_rated == 1
    lo, hi = one.eci_interval
    assert lo < one.ecosystem_condition_index < hi
    poor, good = scoring.rollup({fid: 3 for fid in mapping}), scoring.rollup({fid: 13 for fid in mapping})
    assert poor.functions_rated == good.functions_rated == len(mapping) == 20
    assert poor.eci_interval == (poor.ecosystem_condition_index, poor.ecosystem_condition_index)
    assert good.eci_interval == (good.ecosystem_condition_index, good.ecosystem_condition_index)
    assert lo == pytest.approx(scoring.rollup({**{fid: 3 for fid in mapping},
                                               "catchment-hydrology": 8}).ecosystem_condition_index)
    assert hi == pytest.approx(scoring.rollup({**{fid: 13 for fid in mapping},
                                               "catchment-hydrology": 8}).ecosystem_condition_index)
    # a single Good function sits on the closed upper bound: the interval brackets, never excludes
    top = scoring.rollup({"catchment-hydrology": 13})
    assert top.eci_interval[0] < top.ecosystem_condition_index == top.eci_interval[1]
    none = scoring.rollup({})
    assert none.functions_rated == 0 and none.ecosystem_condition_index is None
    assert none.eci_interval == (poor.ecosystem_condition_index, good.ecosystem_condition_index)
    # the point ECI and the sub-indices are the mean of the available sub-indices, as before
    sub = [v for v in one.sub_indices.values() if v is not None]
    assert one.ecosystem_condition_index == pytest.approx(sum(sub) / len(sub))


def test_the_report_carries_the_fields_only_when_the_package_or_the_process_asks(monkeypatch):
    monkeypatch.delenv(scoring.ENV_ROLLUP_REPORTING, raising=False)
    legacy = client.score_record(_record(), cross_section=False)
    assert "functionsRated" not in legacy and "ecosystemConditionIndexInterval" not in legacy
    monkeypatch.setenv(scoring.ENV_ROLLUP_REPORTING, "1")
    report = client.score_record(_record(), cross_section=False)
    assert report["functionsRated"] == report["computedCount"] == 20
    lo, hi = report["ecosystemConditionIndexInterval"]
    assert lo <= report["ecosystemConditionIndex"] <= hi
    without = {k: v for k, v in report.items() if k not in ("functionsRated", "ecosystemConditionIndexInterval")}
    assert without == legacy
    partial = client.score_record(_record(geomorph=None, nas_taxa=None), cross_section=False)
    assert partial["functionsRated"] < 20
    lo, hi = partial["ecosystemConditionIndexInterval"]
    assert lo < partial["ecosystemConditionIndex"] < hi
    # the package asks (the catalog flag), and the process flag can refuse it
    monkeypatch.delenv(scoring.ENV_ROLLUP_REPORTING)
    data = {**config.screening_methods(), "rollupReporting": True}
    monkeypatch.setattr(config, "screening_methods", lambda: data)
    assert scoring.rollup_reporting_enabled()
    asked = client.score_record(_record(), cross_section=False)
    assert asked["functionsRated"] == 20
    monkeypatch.setenv(scoring.ENV_ROLLUP_REPORTING, "0")
    assert not scoring.rollup_reporting_enabled()
    refused = client.score_record(_record(), cross_section=False)
    assert "functionsRated" not in refused
    monkeypatch.setenv(scoring.ENV_ROLLUP_REPORTING, "1")
    scored = scoring.score_assessment({mid: "Good" for mid in list(config.metrics_by_id())[:5]})
    assert scored["functionsRated"] == 5
    assert scored["ecosystemConditionIndexInterval"][0] < scored["ecosystemConditionIndex"]

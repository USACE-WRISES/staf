"""Methodology 0.14: one reference-source hierarchy, its acceptance rules, the
model registry, the benchmark catalog, carry-forward, fill-to-two and the
documented gap."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from streamcurves import acceptance
from streamcurves import carry_forward as cf
from streamcurves import model_registry as mreg
from streamcurves import nrsa_dataset as nd
from streamcurves import pressure_evidence as pe
from streamcurves import published_benchmark as pb
from streamcurves import reference_pool as rp


# --------------------------------------------------------------------------- #
# ACC-05 and ACC-06: the acceptance record of a verdict
# --------------------------------------------------------------------------- #
def _verdict(a1, net, n=19, verdict="promising", kind="population", c3=None):
    return {"a1_share": a1, "net_opt": net, "n_cells": n, "verdict": verdict, "kind": kind,
            "c3_share": c3, "c1_share": 0.4}


def test_two_thirds_exactly_is_accepted_and_the_next_share_below_is_not():
    assert acceptance.acceptance_record(_verdict(0.6667, 0.0))["accepted"] is True
    # the nearest share below two thirds over 200 cells reads 0.6650
    assert acceptance.acceptance_record(_verdict(0.6650, 0.0))["accepted"] is False


def test_directional_bias_and_the_cell_floor_gate_acceptance():
    assert acceptance.acceptance_record(_verdict(0.9, 0.06))["accepted"] is False
    assert acceptance.acceptance_record(_verdict(0.9, -0.3))["accepted"] is True
    assert acceptance.acceptance_record(_verdict(0.9, 0.0, n=3))["accepted"] is False
    assert acceptance.acceptance_record(
        _verdict(0.9, 0.0, verdict="not quantified"))["accepted"] is False


def test_anchor_recovery_is_a_diagnostic_never_a_gate():
    rec = acceptance.acceptance_record({**_verdict(0.8, 0.0), "c1_share": 0.1})
    assert rec["accepted"] is True
    assert rec["anchor_recovery_diagnostic"] == 0.1


def test_a_model_also_needs_its_data_to_reach_the_regions():
    ok = acceptance.acceptance_record(_verdict(0.9, 0.0, kind="model", c3=0.7))
    thin = acceptance.acceptance_record(_verdict(0.9, 0.0, kind="model", c3=0.5))
    assert ok["accepted"] is True and thin["accepted"] is False


# --------------------------------------------------------------------------- #
# the evidence a source is judged on, and its family fallback
# --------------------------------------------------------------------------- #
def _rec(accepted, n=19, transport=None):
    out = {"verdict": "promising" if accepted else "unsupported", "n_cells": n,
           "acceptance": {"n_cells": n, "accepted": accepted,
                          "classification_error": accepted, "directional_bias": True}}
    if transport:
        out["transport"] = transport
    return out


def test_metric_evidence_decides_and_the_family_stands_in_below_four_regions():
    table = {"m1": {"2r_l2": _rec(True)},
             "m2": {"2r_l2": _rec(True, n=2)},
             acceptance.FAMILIES_KEY: {"fam": {"2r_l2": _rec(False)}}}
    assert acceptance.evidence("m1", "regional_l2", table, family="fam")[0] is True
    # two regions are too few to judge m2 itself, so its family decides
    ok, why = acceptance.evidence("m2", "regional_l2", table, family="fam")
    assert ok is False and "family" in why
    # no evidence at all is a refusal, never a pass
    ok, why = acceptance.evidence("m3", "regional_nars9", table, family="other")
    assert ok is False and "no evidence" in why


def test_a_national_source_applies_only_inside_the_range_its_evidence_covered():
    tr = {"measure": "distance_median", "better": "max", "bound": 0.08}
    table = {"m": {"3c_matched": _rec(True, transport=tr)}}
    assert acceptance.transport_ok("m", "3c_matched", table, measure=0.05)[0] is True
    ok, why = acceptance.transport_ok("m", "3c_matched", table, measure=0.12)
    assert ok is False and "median distance" in why
    tr = {"measure": "n_fit", "better": "min", "bound": 27}
    table = {"m": {"3a_envelope": _rec(True, transport=tr)}}
    assert acceptance.transport_ok("m", "3a_envelope", table, measure=40)[0] is True
    assert acceptance.transport_ok("m", "3a_envelope", table, measure=12)[0] is False


# --------------------------------------------------------------------------- #
# REF-11: the regional screen and the pool options
# --------------------------------------------------------------------------- #
def test_the_regional_screen_relaxes_agriculture_only_and_never_below_the_floor():
    hs = {"agriculture_quantile": 0.9, "agriculture_floor": 25.0,
          "min_epa_reference_sites": 5, "screen_id": "x", "widen": 0.05}
    table = pd.DataFrame({"nars9": ["TPL"] * 6 + ["WMT"] * 6,
                          "rt_nrsa": ["R"] * 12,
                          "agriculture_ws": [10, 20, 30, 40, 60, 80] + [0, 0, 0, 0, 0, 1]})
    tpl = rp.epa_agriculture_limit(table, "TPL", hs)
    assert tpl["rule"] == "epa_reference_range" and tpl["limit"] > 25
    wmt = rp.epa_agriculture_limit(table, "WMT", hs)
    assert wmt["rule"] == "floor" and wmt["limit"] == 25.0
    few = rp.epa_agriculture_limit(table.iloc[:3], "TPL", hs)
    assert few["rule"] == "floor", "fewer EPA sites than the minimum use the floor"


def test_the_committed_calibration_matches_pre_registration_iv():
    from streamcurves import reference_screen as rscreen
    got = {n: rp.epa_agriculture_limit(rscreen.load_station_screen(), n)["limit"]
           for n in ("CPL", "NPL", "SAP", "SPL", "TPL", "XER")}
    assert got == {"CPL": 25.0, "NPL": 47.8, "SAP": 37.8, "SPL": 25.0, "TPL": 77.37,
                   "XER": 25.0}


def test_every_family_searches_nars9_as_a_separate_grouping():
    cfg = rp.load_transfer_config()
    for fam, prof in (cfg.get("families") or {}).items():
        order = rp.search_order(prof)
        assert order[0] == "l3" and sorted(order) == ["l1", "l2", "l3", "nars9"], fam
    assert rp.search_order(rp.family_profile("chem_PTL"))[1] == "nars9"


def test_the_envelope_widens_by_a_share_of_the_national_span():
    env = {"a": (1.0, 2.0, 30), "b": (float("nan"), float("nan"), 0)}
    got = rp.widen_envelope(env, {"a": 10.0}, 0.05)
    assert got["a"] == (0.5, 2.5, 30)
    assert got["b"][2] == 0


def test_fauna_groups_follow_the_basins_a_region_drains_to():
    rows = pd.DataFrame({"huc12": ["050100010101"] * 9 + ["100100010101"]})
    assert rp.fauna_groups_of(rows) == ["interior_east", "interior_plains"]
    donors = pd.DataFrame({"huc12": ["170100010101", "051000010101", "030100010101"]})
    assert rp.fauna_mask(donors, ["interior_east"]).tolist() == [False, True, False]


def _pool_frame(seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for code, l2, n, strict in (("T", "8.1", 30, 4), ("N1", "8.1", 40, 30), ("N2", "8.2", 40, 30)):
        for i in range(n):
            rows.append({"station_key": f"{code}-{i}", "l3": code, "l2": l2, "l1": "8",
                         "nars9": "CPL", "l3_name": code, "l2_name": l2, "l1_name": "8",
                         "huc12": f"05{code}{i:04d}", "pass_strict": i < strict,
                         "pass_relaxed": True, "fail_strict": "" if i < strict else "ag",
                         "drainage_area_sqkm": float(rng.uniform(10, 100)),
                         "pctimp2019ws": 0.5, "agriculture_ws": 5.0 if i < strict else 20.0,
                         "rddensws": 1.0, "dor": 0.0, "npdesdensws": 0.0, "mines_ws": 0.0})
    return pd.DataFrame(rows)


def test_a_thin_local_pool_walks_to_the_regional_options_in_order():
    frame = _pool_frame()
    values = pd.Series(np.random.default_rng(1).normal(10, 2, len(frame)),
                       index=frame["station_key"].astype(str))
    profile = {"family": "t", "covariates": ["drainage_area_sqkm"],
               "search_order": ["l3", "l2", "nars9", "l1"]}
    d, _ = rp.choose_pool("m", values, frame, "T", profile=profile)
    # four strict stations are below the floor; the region's own streams under
    # the regional screen (agriculture 20 <= 25) are the first regional option
    assert d.status == rp.STATUS_LOCAL_RELAXED and d.screen == rp.SCREEN_REGIONAL
    assert [x["option"] for x in d.options_tried] == ["local", "regional_l3"]
    refused = {"regional_l3"}
    d2, _ = rp.choose_pool("m", values, frame, "T", profile=profile,
                           accept=lambda opt, v: (opt not in refused, "refused"))
    assert d2.status == rp.STATUS_BY_OPTION[("l2", rp.SCREEN_REGIONAL)]
    assert [x["option"] for x in d2.options_tried][:3] == ["local", "regional_l3",
                                                           "regional_l2"]


def test_withheld_stations_never_enter_a_pool():
    frame = _pool_frame()
    values = pd.Series(1.0, index=frame["station_key"].astype(str))
    profile = {"family": "t", "covariates": ["drainage_area_sqkm"],
               "search_order": ["l3", "l2", "nars9", "l1"]}
    held = [f"T-{i}" for i in range(30)]
    d, _ = rp.choose_pool("m", values, frame, "T", profile=profile, withhold=held,
                          only_option="regional_l2")
    assert not set(held) & set(d.station_ids)


# --------------------------------------------------------------------------- #
# REF-13: the model registry
# --------------------------------------------------------------------------- #
def test_the_registry_ships_the_two_approved_specifications():
    reg = mreg.load()
    approved = {e["metric"] for e in reg["entries"] if e["status"] == mreg.APPROVED}
    assert approved == {"bent_HPRIME", "chem_TURB"}
    for e in reg["entries"]:
        assert e["procedure"] in reg["procedures"]
        if e["status"] == mreg.APPROVED:
            assert e["approval"]["by"] and e["evidence"]["preregistration_sha256"]


def _model_frame(n_per=30, target="T", target_ag=30.0):
    rng = np.random.default_rng(5)
    rows = []
    for code in ("A", "B", "C", target):
        for i in range(n_per):
            ag = target_ag + rng.uniform(0, 20) if code == target else rng.uniform(0, 40)
            rows.append({"station_key": f"{code}-{i}", "l3": code, "huc12": f"{code}{i}",
                         "pass_strict": code != target and ag < 5,
                         "drainage_area_sqkm": rng.uniform(5, 300), "nhd_slope": rng.uniform(0.001, 0.02),
                         "tmean8110ws": rng.uniform(5, 15), "precip8110ws": rng.uniform(700, 1200),
                         "bfiws": rng.uniform(30, 60), "agriculture_ws": ag,
                         "pctimp2019ws": 0.1, "rddensws": 0.5, "dor": 0.0, "nabd_densws": 0.0,
                         "npdesdensws": 0.0, "mines_ws": 0.0})
    return pd.DataFrame(rows)


def test_applicability_is_computed_from_the_limits_not_listed():
    frame = _model_frame()
    values = pd.Series(np.random.default_rng(2).normal(3, 0.5, len(frame)), index=frame.index)
    entry = {"metric": "m", "procedure": "mixed_local", "status": "approved",
             "limits": {"min_coverage_share": 0.0,
                        "max_disturbance_gap": {"agriculture_ws": 10.0}}}
    got = mreg.applicability(entry, frame=frame, values=values, target_l3="T")
    assert got["ok"] is False and "agricultural cover" in got["why"]
    entry["limits"]["max_disturbance_gap"]["agriculture_ws"] = 40.0
    assert mreg.applicability(entry, frame=frame, values=values, target_l3="T")["ok"] is True
    few = values.copy()
    few[frame["l3"] == "T"] = np.nan
    got = mreg.applicability(entry, frame=frame, values=few, target_l3="T")
    assert got["ok"] is False and "own level" in got["why"]


def test_a_candidate_specification_is_never_run():
    from streamcurves import basis_ladder as bl
    reg = {"procedures": {"mixed_local": {}}, "requirements": {},
           "entries": [{"metric": "m", "procedure": "mixed_local", "status": "candidate"}]}
    frame = _model_frame()
    got = bl.try_modeled("m", frame=frame, values=pd.Series(1.0, index=frame.index),
                         target_l3="T", registry=reg)
    assert got["admitted"] is False and got["candidate"] is True
    assert "approval" in got["why"]


# --------------------------------------------------------------------------- #
# REF-14: the verified catalog
# --------------------------------------------------------------------------- #
def test_catalog_thresholds_match_the_vendored_criteria_they_are_copied_from():
    for entry in pb.load_catalog()["entries"]:
        inp = pb._input_of(entry["metric"])
        assert inp is not None, entry["id"]
        vendored = inp.get("regionalBands") or {}
        assert {k: [float(x) for x in v] for k, v in entry["thresholds"].items()} == \
            {k: [float(x) for x in v] for k, v in vendored.items()}, entry["id"]


def test_catalog_precedence_prefers_the_most_specific_then_the_newest(monkeypatch):
    specs = [
        {"id": "nat-old", "metric": "m", "geography": {"kind": "nars9"}, "edition": "2013",
         "thresholds": {"CPL": [1, 2]}},
        {"id": "nat-new", "metric": "m", "geography": {"kind": "nars9"}, "edition": "2019",
         "thresholds": {"CPL": [1, 2]}},
        {"id": "l3", "metric": "m", "geography": {"kind": "l3"}, "edition": "2010",
         "thresholds": {"65": [1, 2]}},
    ]
    cat = {**pb.load_catalog(), "entries": specs}
    monkeypatch.setattr(pb, "load_catalog", lambda path=None: cat)
    assert pb.lookup("m", target_l3="65", region="CPL")["id"] == "l3"
    assert pb.lookup("m", target_l3="27", region="CPL")["id"] == "nat-new"
    assert pb.lookup("other", target_l3="65") is None


def test_a_missing_entry_is_a_specific_gap_and_the_ohio_finding_stays_in_its_region():
    assert pb.refusal("phab_XEMBED", "65") == pb.NO_CRITERION
    ecbp = pb.refusal("fish_NAT_NTOLPTAX", "55")
    other = pb.refusal("fish_NAT_NTOLPTAX", "65")
    assert "Ohio" in ecbp and "Ohio" not in other
    assert other == pb.REFUSED["fish_NAT_NTOLPTAX"]


# --------------------------------------------------------------------------- #
# carry-forward: a defect is decided from data
# --------------------------------------------------------------------------- #
def test_a_published_pool_value_the_archive_does_not_hold_is_a_defect(monkeypatch):
    held = pd.DataFrame({"station_key": ["a", "a", "b", "c"], "cycle": ["2324", "1819", "1819", "2324"],
                         "value": [5.0, 4.0, 7.0, 9.0]})
    monkeypatch.setattr(nd, "valid_cycle_values", lambda keys, metric, **k: held)
    ok = cf._pool_defects("m", pd.Series({"a": 4.0, "b": 7.0, "c": 9.0}))
    assert ok == {"checked": True, "n_pool": 3, "n_defective": 0}
    bad = cf._pool_defects("m", pd.Series({"a": 4.5, "b": 7.0, "c": 9.0}))
    assert bad["n_defective"] == 1


def test_a_landscape_metric_is_not_checked_against_the_nrsa_archive(monkeypatch):
    monkeypatch.setattr(nd, "valid_cycle_values",
                        lambda keys, metric, **k: pd.DataFrame(columns=["station_key", "cycle", "value"]))
    got = cf._pool_defects("bfiws", pd.Series({"a": 1.0}))
    assert got["checked"] is False and got["n_defective"] == 0


def test_the_corrected_metrics_cover_the_verification_findings():
    got = nd.corrected_metrics()
    assert "chem_CHLA" in got and "phab_LWDeqVolM100" in got
    assert nd.is_corrected("fish_NAT_TOTLNTAX") and not nd.is_corrected("bent_HPRIME")


def test_the_session_round_trip_restores_carried_points_and_layers():
    pts = pd.DataFrame({"point_order": [1, 2], "metric_value": [0.0, 5.0], "index_score": [0.0, 1.0]})
    carried = {"m": {"row": {"metric": "m", "display_name": "M", "stratum": "",
                             "curve_status": "complete", "n_reference": 12, "curve_points": pts,
                             "all_strata": [{"stratum": "", "curve_points": pts},
                                            {"stratum": "ge_2", "curve_points": pts}]},
                     "config": {"units": "x"}, "annotations": {"basis": "regional-reference"},
                     "mapping": [{"metric_key": "m", "discipline": "Biology",
                                  "function_label": "Population support"}],
                     "decision": {"status": "local", "carried_from": 8}}}
    back = cf.restore_rows(cf.session_rows(carried))["m"]
    assert back["row"]["curve_points"]["metric_value"].tolist() == [0.0, 5.0]
    assert [L["stratum"] for L in back["row"]["all_strata"]] == ["", "ge_2"]
    assert back["decision"]["carried_from"] == 8


# --------------------------------------------------------------------------- #
# SELECT-04: fill to two
# --------------------------------------------------------------------------- #
def _mapping(pairs):
    return pd.DataFrame([{"metric_key": m, "discipline": "Biology", "function_label": f,
                          "sort_order": i} for i, (m, f) in enumerate(pairs)])


def test_new_curves_fill_a_function_to_two_by_source_then_score():
    ev = {"reference_support": {"a": {"status": "local"}, "b": {"status": "borrowed_l2"},
                                "c": {"status": "local"}},
          "carried": {}, "carry_rebuilt": {}, "fixed_metrics": {}}
    rows = {m: {} for m in "abc"}
    mapping = _mapping([("a", "Population support"), ("b", "Population support"),
                        ("c", "Population support")])
    scores = {"a": {"total": 50}, "c": {"total": 70}}
    meta: dict = {}
    got_rows, got_map = pe.select_portfolio(ev, rows, mapping, {}, scores, meta)
    sel = meta["portfolioSelection"]["population-support"]
    assert sel["selected"] == ["c", "a"], "local first, then the higher SELECT-02 score"
    assert [x["metric"] for x in sel["notSelected"]] == ["b"]
    assert set(got_map["metric_key"]) == {"a", "c"} and "b" not in got_rows


def test_carried_curves_keep_their_places_and_reserves_wait_for_a_gap():
    ev = {"reference_support": {"a": {"status": "local"}, "r": {"status": "local"},
                                "k1": {"status": "local"}, "k2": {"status": "local"},
                                "k3": {"status": "local"}},
          "carried": {"k1": {}, "k2": {}, "k3": {}}, "carry_rebuilt": {}, "fixed_metrics": {}}
    rows = {m: {} for m in ("a", "r", "k1", "k2", "k3")}
    mapping = _mapping([("k1", "Population support"), ("k2", "Population support"),
                        ("k3", "Population support"), ("a", "Population support"),
                        ("r", "Community dynamics")])
    config = {"r": {"reserve": True}}
    meta: dict = {}
    got_rows, got_map = pe.select_portfolio(ev, rows, mapping, config, {}, meta)
    pop = meta["portfolioSelection"]["population-support"]
    assert pop["kept"] == ["k1", "k2", "k3"] and pop["selected"] == []
    # a reserve alone on an otherwise unassessed function is used
    assert meta["portfolioSelection"]["community-dynamics"]["selected"] == ["r"]
    ev2 = {**ev, "reference_support": {**ev["reference_support"], "x": {"status": "local"}}}
    mapping2 = _mapping([("x", "Community dynamics"), ("r", "Community dynamics")])
    meta2: dict = {}
    pe.select_portfolio(ev2, {"x": {}, "r": {}}, mapping2, config, {}, meta2)
    cd = meta2["portfolioSelection"]["community-dynamics"]
    assert cd["selected"] == ["x"] and cd["notSelected"][0]["reserve"] is True


# --------------------------------------------------------------------------- #
# COV-01: the documented gap, pending until the owner confirms
# --------------------------------------------------------------------------- #
def test_a_gap_is_documented_only_when_every_candidate_has_a_blocker():
    from streamcurves import provenance as pv
    fn = [{"functionId": "population-support"}]
    result = {"coverage": {"missingFunctionIds": ["population-support"]},
              "meta": {"insufficientReferenceSupport": [
                  {"metricKey": "f1", "metricName": "F1", "functions": fn,
                   "reason": "insufficient-reference-support", "statement": "No source."},
                  {"metricKey": "f2", "metricName": "F2", "functions": fn,
                   "reason": "insufficient-reference-support", "statement": "No source."}]}}
    gap = pv.coverage_gaps(result)[0]
    assert gap["blockers_documented"] is True and gap["n_candidates"] == 2
    result["meta"]["insufficientReferenceSupport"][1]["reason"] = "held-for-review"
    assert pv.coverage_gaps(result)[0]["blockers_documented"] is False


def test_the_standing_decision_records_the_gap_under_a_pending_reviewer():
    from streamcurves import decisions as dec
    policy = dec.load_policy()
    item = {"status": "open", "item_id": "COV-01:population-support",
            "rule_ids": ["COV-01"], "subject": "population-support",
            "trigger": "function_unassessed",
            "evidence": {"function_id": "population-support", "candidates": ["f1"],
                         "candidates_text": "F1", "n_candidates": 1,
                         "blockers_documented": True}}
    doc = {"records": [], "reviewQueue": {"items": [item]}}
    pr = dec.apply_policy(doc, policy, result=None, date="2026-09-21")
    assert [d["decision_class"] for d in pr.decisions] == ["cov01-documented-gap"]
    ex = pr.coverage_exceptions[0]
    assert ex["functionId"] == "population-support"
    assert dec.PENDING_SUFFIX in ex["recordedBy"]


def test_promote_confirms_the_gap_under_the_owner_outside_the_digest():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import run_region_batch as rb
    from streamcurves import decisions as dec
    from streamcurves import library as lib
    pending = "standing-policy:cov01-documented-gap " + dec.PENDING_SUFFIX
    bundle = {"metricsByFunction": [], "region": {"code": "65"},
              "functionCoverage": {"exclusions": [{"functionId": "x", "recordedBy": pending}]}}
    session = {"fields": {"function_coverage_exceptions": [{"functionId": "x",
                                                            "recordedBy": pending}]}}
    before = lib.content_digest(bundle)
    n = rb._confirm_coverage_exceptions(bundle, session, {}, maintainer="owner", date="d")
    assert n == 2
    assert bundle["functionCoverage"]["exclusions"][0]["recordedBy"] == "owner"
    assert lib.content_digest(bundle) == before


def test_a_republish_leaves_out_what_the_build_did_not_select():
    mapping = _mapping([("a", "Population support"), ("b", "Population support"),
                        ("b", "Community dynamics"), ("f", "Catchment hydrology")])
    rows = {"a": {}, "b": {}, "f": {}}
    sel = {"population-support": {"notSelected": [{"metric": "b"}]}}
    got_rows, got_map = pe._apply_selection(sel, rows, mapping, keep={"f"})
    pairs = set(zip(got_map["metric_key"], got_map["function_label"]))
    assert ("b", "Population support") not in pairs and ("b", "Community dynamics") in pairs
    assert set(got_rows) == {"a", "b", "f"}
    sel["community-dynamics"] = {"notSelected": [{"metric": "b"}]}
    got_rows, _ = pe._apply_selection(sel, rows, mapping, keep={"f"})
    assert set(got_rows) == {"a", "f"}, "a curve left in no function is not published"


def test_a_reserve_whose_functions_are_scored_raises_no_review_item():
    r = "fish_NAT_NTOLPTAX"          # a reserve the crosswalk puts on Population support
    mapping = _mapping([("x", "Population support"), (r, "Population support")])
    config = {r: {"reserve": True}, "x": {}}
    assert pe.moot_reserves(config, {"x": {}}, mapping) == [r]
    assert pe.moot_reserves(config, {}, mapping) == [], "an unscored function keeps the reserve"

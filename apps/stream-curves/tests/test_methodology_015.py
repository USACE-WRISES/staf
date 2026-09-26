"""Methodology 0.15-provisional (campaign Round 2 close, WP-015a).

What the round changed and what must not have moved: the catalog (74 rules, the
three new ids, the version and the campaign block), the mirrors, the one search
order every family walks (B3 adopted), the iqr-seed-3 default (C3b adopted, pinned
by tests/test_golden_masters.py and tests/test_round2_knobs.py), and the two
withholds at the build: a metric whose pool is mostly unmeasured (DATA-03) and a
two-sided curve narrower than its measurement-precision floor (CURVE-09) leave
the build like an insufficient-support metric, with a statement naming the rule
and the numbers, a ledger row reading unsupported under the rule, and no review
item. Every check here is offline and synthetic.
"""
from __future__ import annotations

import collections
import copy

import pandas as pd

from streamcurves import candidates as C
from streamcurves import curves, methodology, run_state
from streamcurves import pressure_evidence as pe
from streamcurves import provenance as pv
from streamcurves import reference_pool as rp
from streamcurves import regional_agent as ra
from streamcurves import rules_view as rv
from tests.test_reference_pool import CFG, SETTINGS, _frame, _stations, _values

PROTOCOL_SHA = "84a5cabca0b08d403c44164b130500173451aa0d14ae11704abeeece014a1cfd"
VERSION = "0.15-provisional"


# --------------------------------------------------------------------------- #
# the catalog and the version
# --------------------------------------------------------------------------- #
def test_the_catalog_carries_74_rules_under_0_15_and_no_calibrated_threshold():
    cat = methodology.load_rule_catalog()
    assert methodology.methodology_version() == VERSION
    assert cat["meta"]["methodology_version"] == VERSION
    assert methodology.load_config()["meta"]["methodology_version"] == VERSION
    assert cat["meta"]["date"] == "2026-09-26"
    ids = methodology.rule_ids()
    assert len(ids) == 74
    assert {"CURVE-13", "DATA-12", "EVAL-01"} <= set(ids)
    campaign = cat["meta"]["campaign"]
    assert campaign["protocol"]["sha256"] == PROTOCOL_SHA and campaign["round"] == 2
    assert "B3" in campaign["verdicts"] and "C3b" in campaign["verdicts"]
    assert "none moved to calibrated" in campaign["threshold_status_review"]
    counts = collections.Counter(r["threshold_status"] for r in cat["rules"])
    assert counts == {"provisional": 62, "approved": 12}
    assert "62 rules provisional, 12 approved, 0 calibrated" in campaign["threshold_status_review"]
    assert "v0.15" in cat["meta"]["description"]


def test_the_new_rules_say_what_they_are():
    c13 = methodology.rule("CURVE-13")
    assert c13["name"] == "Residual structure" and c13["family"] == "CURVE"
    assert c13["implementation_status"] == "not_applicable"
    assert "S-01" in c13["note"] and "IQR" in c13["note"]
    c07 = methodology.rule("CURVE-07")
    assert c07["name"] == "Curve held for review" and "CURVE-13" in c07["note"]
    d12 = methodology.rule("DATA-12")
    assert d12["name"] == "Rebuild ledger" and d12["threshold_status"] == "approved"
    assert d12["implementation_status"] == "implemented"
    assert pv.LEDGER_SCHEMA in d12["maps_to"] and "provenance.build_ledger" in d12["maps_to"]
    for word in pv.LEDGER_DISPOSITIONS:
        assert word in d12["test"], word
    e01 = methodology.rule("EVAL-01")
    assert e01["family"] == "EVAL" and e01["threshold_status"] == "approved"
    assert PROTOCOL_SHA in e01["threshold"] and "round2" in e01["maps_to"]
    # the verdict notes, one sentence each
    assert "ladder_rule: first_pass" in methodology.rule("REF-11")["test"]
    assert "l3, l2, l1, nars9" in methodology.rule("REF-11")["test"]
    for rid, words in (("REF-11", ("B1 rejected", "B2 rejected", "B3 adopted")),
                       ("CURVE-10", ("C3a rejected", "C3b adopted", "iqr-seed-3")),
                       ("CURVE-12", ("C2 rejected", "curve12-inverted-thin")),
                       ("SELECT-04", ("C1 rejected",)),
                       ("SELECT-02", ("tie-break",)),
                       ("REF-15", ("alternatives_over_fitted", "C5")),
                       ("REF-06", ("nWithheld", "nHeldForReview", "high-missingness",
                                   "measurement-precision-floor")),
                       ("DATA-03", ("withholds", "data03-thin-metric-finalized")),
                       ("CURVE-09", ("withheld", "replays as before"))):
        note = methodology.rule(rid)["note"]
        for word in words:
            assert word in note, (rid, word)
    for rid in ("DATA-03", "CURVE-09"):
        rule = methodology.rule(rid)
        assert "withheld at the build" in rule["outcomes"]["fail"], rid
        assert "no review item" in rule["maps_to"] and "withholds" in rule["test"], rid
        assert rule["confidence_effect"] == "n/a (no curve is built)", rid
    assert methodology.rule("DATA-03")["threshold"] == "> 0.40"
    assert methodology.threshold("curve_rules.measurement_precision_floors") == {"chem_PH": 0.2}
    # the status legend knows the new implementation status
    legend = methodology.load_rule_catalog()["meta"]["status_legend"]["implementation_status"]
    assert "not_applicable" in legend


def test_the_mirrors_and_the_method_version():
    assert methodology.mirror_drift() == []
    assert run_state.CURVE_METHOD_VERSION == "iqr-seed-3"
    assert methodology.threshold("meta.curve_method_version") == "iqr-seed-3"
    assert curves.MONOTONE_TAIL_OFFSETS_IQR == methodology.MONOTONE_TAIL_OFFSETS_IQR == (0.5, 1.5, 2.5)
    assert curves.LEGACY_TAIL_OFFSETS_IQR_SEED_2 == (0.3, 4.0 / 3.0, 7.0 / 3.0)
    assert methodology.threshold("curve10.tail_offsets_iqr") == [0.5, 1.5, 2.5]
    assert methodology.round2_knobs() == {}
    assert methodology.seed_geometry()["custom_tail_offsets"] is False
    assert "v0.15" in methodology.load_config()["meta"]["calibration_note"]


def test_the_rules_view_knows_the_eval_family_and_reads_the_policy_counts():
    entries = rv.rule_entries()
    by_id = {e["id"]: e for e in entries}
    assert "EVAL" in {e["family"] for e in entries} and rv.FAMILY_LABELS["EVAL"]
    assert rv.RULE_ID_RE.fullmatch("EVAL-01") and rv.RULE_ID_RE.fullmatch("CURVE-13")
    assert rv.status_exceptions(by_id["CURVE-13"]) == [("implementation", "not_applicable")]
    assert rv.status_exceptions(by_id["EVAL-01"]) == [("threshold", "approved")]
    counts = rv.policy_counts()
    assert counts["total"] == counts["default"] + counts["optional"]
    assert counts["optional"] == 4 and counts["default"] >= 13
    assert counts["version"] == "1.3"


# --------------------------------------------------------------------------- #
# B3: the one search order
# --------------------------------------------------------------------------- #
def test_the_transfer_config_declares_one_search_order_for_every_family():
    cfg = rp.load_transfer_config()
    assert cfg["search_order"] == ["l3", "l2", "l1", "nars9"]
    assert cfg["version"] == 3
    for fam, prof in cfg["families"].items():
        assert "search_order" not in prof and "search_why" not in prof, fam
    for mk in ("chem_PTL", "bent_EPT_NTAX", "phab_XCMGW", "phab_SINU", "bfiws"):
        prof = rp.family_profile(mk)
        assert prof["search_order_source"] == "global", mk
        assert rp.search_order(prof) == ["l3", "l2", "l1", "nars9"], mk


def test_choose_pool_walks_the_one_order_where_a_family_order_used_to_differ():
    """Water chemistry tried NARS-9 second until 0.14 (l3, nars9, l2, l1); under
    the one order every family tries it last. The same synthetic frame, the two
    configurations, the two walks."""
    base = copy.deepcopy(CFG)
    base["families"]["water_chemistry"] = {"covariates": ["drainage_area_sqkm"], "lithology": False}
    base["metric_family"]["chem_X"] = "water_chemistry"
    new = {**base, "search_order": ["l3", "l2", "l1", "nars9"]}
    old = copy.deepcopy(base)
    old["families"]["water_chemistry"]["search_order"] = ["l3", "nars9", "l2", "l1"]
    frame = _frame(_stations("T", 40, l3="55", l2="8.2", strict=False),
                   _stations("M", 4, l3="56", l2="8.2", flat=True)).assign(nars9="TPL")
    values = _values(frame)
    d_new, _ = rp.choose_pool("chem_X", values, frame, "55",
                              profile=rp.family_profile("chem_X", new), cfg=new, settings=SETTINGS)
    d_old, _ = rp.choose_pool("chem_X", values, frame, "55",
                              profile=rp.family_profile("chem_X", old), cfg=old, settings=SETTINGS)
    assert [t["option"] for t in d_new.options_tried] == [
        "local", "regional_l3", "regional_l2", "regional_l1", "regional_nars9"]
    assert [t["option"] for t in d_old.options_tried] == [
        "local", "regional_l3", "regional_nars9", "regional_l2", "regional_l1"]
    assert d_new.status == d_old.status == rp.STATUS_INSUFFICIENT


# --------------------------------------------------------------------------- #
# DATA-03 and CURVE-09: withheld at the build
# --------------------------------------------------------------------------- #
def _local_decision():
    frame = _frame(_stations("A", 30, l3="58", l2="5.3", l1="5"))
    d, _ = rp.choose_pool("m_form", _values(frame), frame, "58",
                          profile=rp.family_profile("m_form", CFG), cfg=CFG, settings=SETTINGS)
    return d


def _borrowed_decision():
    frame = _frame(_stations("T", 3, l3="71"), _stations("D", 30, l3="65", flat=True))
    d, _ = rp.choose_pool("m_form", _values(frame), frame, "71",
                          profile=rp.family_profile("m_form", CFG), cfg=CFG, settings=SETTINGS)
    return d


def test_data03_withholds_a_review_disposition_with_the_numbers_in_its_statement():
    missingness = {"phab_LWDeqVolM100": {"missing_fraction": 0.55, "disposition": "review",
                                         "n_pool_members": 40, "n_with_value": 18},
                   "chem_COND": {"missing_fraction": 0.3, "disposition": "caution",
                                 "n_pool_members": 40, "n_with_value": 28},
                   "phab_XEMBED": {"missing_fraction": 0.0, "disposition": "auto",
                                   "n_pool_members": 40, "n_with_value": 40}}
    got = pe.missingness_withheld(missingness)
    assert list(got) == ["phab_LWDeqVolM100"]
    rec = got["phab_LWDeqVolM100"]
    assert rec["threshold"] == methodology.threshold("data_rules.max_missingness_review") == 0.40
    local = pe.missingness_statement(rec, _local_decision())
    assert local == ("High missingness. 55% of the 40 comparable reference stations of this "
                     "ecoregion's own reference pool have no value for this metric (18 carry "
                     "one), above the 40% limit of rule DATA-03, so the metric is withheld at "
                     "the build. No curve was built and the metric is not scored.")
    borrowed = pe.missingness_statement(rec, _borrowed_decision())
    assert "of the Level II 8.3 pool (L2 8.3) have no value" in borrowed
    assert "DATA-03" in borrowed and chr(8212) not in borrowed
    assert pe.WITHHELD_RULES[pe.HIGH_MISSINGNESS] == "DATA-03"


def _ph_row(values, units="su"):
    frame = pd.DataFrame({"site_id": [f"s{i}" for i in range(len(values))], "chem_PH": values})
    cfg = {"chem_PH": {"column_name": "chem_PH", "curve_form": "optimum", "units": units,
                       "display_name": "pH"}}
    res = curves.build_reference_curve(frame, "chem_PH", cfg, build_plots=False)
    return ra._row_to_dict(res, "chem_PH"), cfg


def test_curve09_withholds_a_core_narrower_than_the_floor_with_the_numbers():
    narrow, cfg = _ph_row([7.0 + 0.02 * i for i in range(10)])
    got = pe.precision_floor_records({"chem_PH": narrow}, cfg)
    assert list(got) == ["chem_PH"]
    rec = got["chem_PH"]
    assert rec["precision_sd"] == 0.2 and rec["core_multiple"] == 2 and rec["floor_width"] == 0.4
    assert rec["functioning_core_width"] == rec["functioning_max"] - rec["functioning_min"]
    assert rec["functioning_core_width"] < 0.4 and rec["n_reference"] == 10
    # the core is the curve's 0.70 band (7.01 to 7.17 su on this pool), not the
    # quartile span; the statement names it with the floor and the precision
    assert round(rec["functioning_core_width"], 3) == 0.153
    text = pe.precision_floor_statement(rec)
    assert text.startswith("Measurement-precision floor. The Functioning core of this two-sided "
                           "curve spans 0.153 su (7.01 to 7.17 su at n = 10), narrower than 0.4 su, "
                           "2 times the metric's documented measurement precision of 0.2 su")
    assert text.endswith("withheld at the build under rule CURVE-09. No curve was built and the "
                         "metric is not scored.")
    assert chr(8212) not in text
    # a wide core passes; a metric with no declared floor is never checked; a
    # monotone curve is never checked
    wide, cfg_wide = _ph_row([6.0 + 0.3 * i for i in range(10)])
    assert pe.precision_floor_records({"chem_PH": wide}, cfg_wide) == {}
    assert pe.precision_floor_records({"chem_X": narrow}, {"chem_X": cfg["chem_PH"]}) == {}
    mono = {"chem_PH": {**cfg["chem_PH"], "curve_form": "monotone", "higher_is_better": True}}
    assert pe.precision_floor_records({"chem_PH": narrow}, mono) == {}
    assert pe.WITHHELD_RULES[pe.MEASUREMENT_PRECISION_FLOOR] == "CURVE-09"


def _withheld_evidence():
    """A build's evidence after the two withholds, in the shape run_evidence returns."""
    local = _local_decision().to_dict()
    lwd = {"missing_fraction": 0.55, "threshold": 0.40, "n_pool_members": 40, "n_with_value": 18,
           "disposition": "review"}
    narrow, cfg = _ph_row([7.0 + 0.02 * i for i in range(10)])
    ph = pe.precision_floor_records({"chem_PH": narrow}, cfg)["chem_PH"]
    items = {
        "phab_LWDeqVolM100": {"decision": local, "config": {"display_name": "Large wood volume",
                                                            "units": "m3/100 m"},
                              "reason": pe.HIGH_MISSINGNESS, "rule": "DATA-03",
                              "statement": pe.missingness_statement(lwd, local), "detail": lwd},
        "chem_PH": {"decision": local, "config": cfg["chem_PH"],
                    "reason": pe.MEASUREMENT_PRECISION_FLOOR, "rule": "CURVE-09",
                    "statement": pe.precision_floor_statement(ph), "detail": ph},
        "chem_COND": {"decision": {**local, "status": "insufficient", "level": None,
                                   "options_tried": []},
                      "config": {"display_name": "Conductivity", "units": "uS/cm"}},
    }
    support = {"phab_LWDeqVolM100": pe.withheld_support_record(local, pe.HIGH_MISSINGNESS),
               "chem_PH": pe.withheld_support_record(local, pe.MEASUREMENT_PRECISION_FLOOR),
               "chem_COND": items["chem_COND"]["decision"],
               "phab_XEMBED": dict(local)}
    return {"insufficient_support": items, "reference_support": support,
            "reference_screen": {"id": "least-disturbed-v1", "tier": "strict"},
            "reference_pool_summary": {}, "local_comparison": {}, "ladder_attempts": [],
            "missingness": {"phab_LWDeqVolM100": {**lwd, "withheld": True, "rule": "DATA-03"},
                            "phab_XEMBED": {"missing_fraction": 0.0, "disposition": "auto",
                                            "n_pool_members": 40, "n_with_value": 40}}}


def test_the_support_record_of_a_withheld_metric_keeps_its_pool_under_status_withheld():
    local = _local_decision().to_dict()
    rec = pe.withheld_support_record(local, pe.HIGH_MISSINGNESS)
    assert rec["status"] == pe.STATUS_WITHHELD == "withheld"
    assert rec["pool_status"] == rp.STATUS_LOCAL and rec["withheld"] == pe.HIGH_MISSINGNESS
    assert rec["rule"] == "DATA-03" and rec["n_usable"] == local["n_usable"]
    assert rec["station_ids"] == local["station_ids"]
    assert pe.is_withheld_record(rec) and not pe.is_withheld_record(local)
    ev = _withheld_evidence()
    block = pe.reference_method_block(ev)
    assert block["nWithheld"] == 3 and block["nCurvesLocal"] == 1
    assert block["curvesByBasis"] == {"regional-reference": 1}
    assert pv._curves_by_basis(ev) == {"regional-reference": 1}
    frame = pe.support_frame({**ev, "metric_config": {"phab_XEMBED": {}}, "fixed_metrics": {}})
    statuses = dict(zip(frame["metric"], frame["status"]))
    assert statuses["phab_LWDeqVolM100"] == "withheld" and statuses["phab_XEMBED"] == "local"


def test_the_withheld_entries_name_the_rule_and_carry_the_statement():
    ev = _withheld_evidence()
    listed = {w["metricKey"]: w for w in pe.withheld_metrics(ev)}
    assert set(listed) == {"phab_LWDeqVolM100", "chem_PH", "chem_COND"}
    lwd, ph, cond = listed["phab_LWDeqVolM100"], listed["chem_PH"], listed["chem_COND"]
    assert lwd["reason"] == pe.HIGH_MISSINGNESS and lwd["rule"] == "DATA-03"
    assert lwd["statement"].startswith("High missingness. 55% of the 40")
    assert lwd["detail"]["missing_fraction"] == 0.55 and lwd["metricName"] == "Large wood volume"
    assert lwd["functionId"] and lwd["functions"]
    assert ph["reason"] == pe.MEASUREMENT_PRECISION_FLOOR and ph["rule"] == "CURVE-09"
    assert ph["statement"].startswith("Measurement-precision floor.")
    assert ph["detail"]["floor_width"] == 0.4
    # a REF-06 entry keeps its shape: no rule key, the hierarchy statement
    assert cond["reason"] == pe.INSUFFICIENT_REFERENCE_SUPPORT and "rule" not in cond
    assert cond["statement"].startswith("Insufficient reference support.")
    assert pe.withheld_rule(cond) == "REF-06" and pe.withheld_rule(lwd) == "DATA-03"
    # a documented gap drafted for the function names the rule in words
    result = {"coverage": {"missingFunctionIds": [lwd["functionId"]]},
              "insufficient_support": ev["insufficient_support"], "ladder_attempts": []}
    draft = pe.coverage_exceptions_draft(result)
    assert draft and "withheld by high missingness over its reference pool" in draft[0]["justification"]
    assert "No curve was forced." in draft[0]["justification"]


def test_a_function_left_empty_by_a_withhold_is_a_documented_gap():
    ev = _withheld_evidence()
    entries = pe.withheld_metrics(ev)
    fid = next(w["functionId"] for w in entries if w["metricKey"] == "phab_LWDeqVolM100")
    result = {"coverage": {"missingFunctionIds": [fid]},
              "meta": {"insufficientReferenceSupport": entries}}
    gaps = {g["function_id"]: g for g in pv.coverage_gaps(result)}
    gap = gaps[fid]
    assert gap["blockers_documented"] is True and gap["held_for_review"] == []
    assert gap["withheld_under"]["phab_LWDeqVolM100"] == "DATA-03"
    assert gap["n_candidates"] >= 1
    # a curve still held for a reviewer is not a documented blocker
    held = {"metricKey": "phab_LWDeqVolM100", "metricName": "Large wood volume",
            "reason": pe.HELD_FOR_REVIEW, "statement": "Held for review.",
            "functions": [{"functionId": fid}]}
    result_held = {"coverage": {"missingFunctionIds": [fid]},
                   "meta": {"insufficientReferenceSupport": [held]}}
    assert pv.coverage_gaps(result_held)[0]["blockers_documented"] is False


def _records_result():
    ev = _withheld_evidence()
    return {"region": {"kind": "ecoregion", "code": "58"}, "reference_method": pe.METHOD,
            "screening_counts": {"n_retained": 30}, **ev,
            "curve_rows": {"phab_XEMBED": {"n_reference": 30}},
            "curve_review": {"phab_XEMBED": {"status": run_state.CURVE_STATUS_AUTO_OK}},
            "sample_sizes": {"phab_XEMBED": {"n": 30, "disposition": "adequate"}},
            "metric_config": {"phab_XEMBED": {"column_name": "phab_XEMBED"}},
            "value_selection": {"policy": "newest-nonnull-v2", "byMetricCycle": {}},
            "scale_registry": {"present": False}, "strata_applied": {}, "fixed_metrics": {},
            "discrimination": {}, "diagnostics": {}, "portfolio": [], "coverage": {},
            "meta": {"insufficientReferenceSupport": pe.withheld_metrics(ev)}}


def test_the_records_state_the_withholds_and_raise_no_review_item():
    manifest = {"inputsDigest": "sha256:" + "a" * 64}
    records = pv.build_records(_records_result(), manifest, timestamp="2026-09-26T00:00:00+00:00")
    by = {(r["rule_id"], r["subject"]): r for r in records}
    data03 = by[("DATA-03", "phab_LWDeqVolM100")]
    assert data03["verdict"] == pv.VERDICT_FAIL and data03["review_required"] is False
    assert data03["review_triggers"] == [] and data03["computed"]["withheld"] is True
    assert data03["recommendation"].startswith("High missingness. 55% of the 40")
    assert data03["inputs"]["missing_fraction"] == 0.55
    curve09 = by[("CURVE-09", "chem_PH")]
    assert curve09["verdict"] == pv.VERDICT_FAIL and curve09["review_required"] is False
    assert curve09["computed"]["floor_width"] == 0.4 and curve09["computed"]["withheld"] is True
    assert curve09["recommendation"].startswith("Measurement-precision floor.")
    assert curve09["thresholds_used"] == {"precision_sd": 0.2, "core_multiple": 2.0}
    # the pool record of a withheld metric is written for the pool's own status
    # and points at the rule; the REF-06 metric keeps its REF-06 record
    pool = by[("REF-05", "phab_LWDeqVolM100")]
    assert pool["computed"]["status"] == rp.STATUS_LOCAL and pool["computed"]["withheld"] == pe.HIGH_MISSINGNESS
    assert pool["computed"]["withheld_rule"] == "DATA-03" and pool["review_required"] is False
    assert "withheld under DATA-03" in pool["recommendation"]
    assert by[("REF-06", "chem_COND")]["verdict"] == pv.VERDICT_FAIL
    assert ("REF-06", "phab_LWDeqVolM100") not in by and ("REF-06", "chem_PH") not in by
    assert by[("DATA-01", "phab_XEMBED")]["verdict"] == pv.VERDICT_PASS
    queue = pv.build_review_queue(records, manifest)
    subjects = {i["subject"] for i in queue["items"]}
    assert not subjects & {"phab_LWDeqVolM100", "chem_PH"}
    assert queue["counts"]["blocking"] == 0
    # DATA-12 is recorded on every build; EVAL-01 only under a protocol; CURVE-13 never
    assert by[("DATA-12", "rebuild_ledger")]["computed"]["schema"] == pv.LEDGER_SCHEMA
    assert by[("DATA-12", "rebuild_ledger")]["computed"]["refit"] == "missing"
    assert ("EVAL-01", "evaluation_protocol") not in by
    not_evaluated = {r["rule_id"]: r for r in pv.rules_not_evaluated(records)}
    assert "not applicable to the approved curve family" in not_evaluated["CURVE-13"]["reason"]
    assert not_evaluated["CURVE-13"]["implementation_status"] == "not_applicable"
    assert "no evaluation protocol was declared" in not_evaluated["EVAL-01"]["reason"]
    assert set(pv.rules_applied(records)) | set(not_evaluated) == set(methodology.rule_ids())
    stamped = {**manifest, "protocol": {"path": "evaluation_protocol_v1.yaml", "version": "1.0",
                                        "sha256": PROTOCOL_SHA}}
    with_protocol = pv.build_records(_records_result(), stamped, timestamp="2026-09-26T00:00:00+00:00")
    eval01 = next(r for r in with_protocol if r["rule_id"] == "EVAL-01")
    assert eval01["inputs"]["sha256"] == PROTOCOL_SHA and eval01["verdict"] == pv.VERDICT_PASS
    assert eval01["review_required"] is False


def test_the_ledger_reads_unsupported_under_the_withholding_rule():
    ev = _withheld_evidence()
    entries = pe.withheld_metrics(ev)
    build = {"method": pe.METHOD, "portfolioSelection": {}, "fixedMetrics": [],
             "metricAnnotations": {}, "insufficientReferenceSupport": entries}
    reg = C.deep_register(tiles=[], build=build, region={"code": "58"})
    rules = {}
    for r in reg["rows"]:
        cand = next(c for c in reg["candidates"] if c["candidateKey"] == r["candidateKey"])
        rules[cand["identity"]["subject"]["id"]] = (r["status"], r["decision"]["rule"])
    assert rules["phab_LWDeqVolM100"] == (C.EXCLUDED, "DATA-03")
    assert rules["chem_PH"] == (C.EXCLUDED, "CURVE-09")
    assert rules["chem_COND"] == (C.EXCLUDED, "REF-06")
    result = {"completed_metrics": {}, "metric_config": {}, "curve_review": {},
              "reference_build": build, "owner_curve_decisions": [],
              "region_of_applicability": {"kind": "ecoregion", "code": "58"},
              "reference_support": ev["reference_support"]}
    ledger = pv.build_ledger(result, manifest={"inputsDigest": "sha256:" + "b" * 64}, register=reg)
    rows = {r["metric"]: r for r in ledger["rows"]}
    assert rows["phab_LWDeqVolM100"]["disposition"] == pv.UNSUPPORTED
    assert rows["phab_LWDeqVolM100"]["rule"] == "DATA-03"
    assert rows["phab_LWDeqVolM100"]["reason"].startswith("High missingness.")
    assert rows["chem_PH"]["disposition"] == pv.UNSUPPORTED and rows["chem_PH"]["rule"] == "CURVE-09"
    assert rows["chem_COND"]["disposition"] == pv.UNSUPPORTED and rows["chem_COND"]["rule"] == "REF-06"
    assert all(not r["engine"]["executed"] for r in rows.values())
    assert ledger["build"]["methodologyVersion"] == VERSION
    # the REF-15 override name follows the rule too
    assert pv._overridden_rule({"insufficient_support": ev["insufficient_support"]},
                               "phab_LWDeqVolM100") == "DATA-03"
    assert pv._overridden_rule({"insufficient_support": ev["insufficient_support"]},
                               "chem_COND") == "REF-06"

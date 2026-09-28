"""Methodology 0.16-provisional (campaign Round 6, WP-R6a, owner decisions D11, D13
and D14 of 2026-09-28).

The flagged-transfer rung (REF-16): a pool refused by the recovery evidence alone is
taken in a second pass with transfer risk unvalidated, its verdict recorded, its
confidence capped and its limitation stated; a validated pool still wins; a pool
below the floor is never taken; the national options get the same second pass, last.
The refusals (D13): a pool option whose curve would be the engine's fallback, or would
read as inverted against its own pressured stations, fails acceptance and the ladder
moves on; a metric every option refuses is withheld with the reasons; no fallback
curve reaches a staged bundle. Standing decisions policy 1.5 and promotion policy 1.2.
Offline and synthetic; the region rehearsals are recorded in the work package's report.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from streamcurves import acceptance
from streamcurves import basis_ladder as bl
from streamcurves import campaign as camp
from streamcurves import confidence as conf
from streamcurves import curve_basis as cb
from streamcurves import decisions as dec
from streamcurves import deep_export, methodology
from streamcurves import pressure_evidence as pe
from streamcurves import provenance as pv
from streamcurves import reference_pool as rp
from streamcurves import regional_agent as ra
from tests.test_basis_ladder import CFG as NATIONAL_CFG, DIRTY, REGISTRY, _accepted, _series
from tests.test_basis_ladder import _frame as _national_frame
from tests.test_reference_pool import CFG, SETTINGS, _frame, _stations, _values

ENTRY = {"column_name": "m_form", "display_name": "Bank height ratio", "higher_is_better": True,
         "metric_family": "continuous", "domain_min": 0, "units": "ratio"}
FLAGGED = {"enabled": True, "min_usable": 10, "prefer_adequate": True, "confidence_cap": 39}
REFUSED = {"verdict": "failed", "n_cells": 6, "a1_share": 0.56, "net_opt": 0.09,
           "acceptance": {"n_cells": 6, "accepted": False, "classification_error": False,
                          "directional_bias": False}}
ACCEPTED = {"verdict": "validated", "n_cells": 6, "a1_share": 0.83, "net_opt": 0.01,
            "acceptance": {"n_cells": 6, "accepted": True, "classification_error": True,
                           "directional_bias": True}}
LIMITATION = ("Scored on a reference pool whose transfer to this ecoregion was not confirmed by "
              "the recovery test (class agreement 0.56 of evaluation regions; two thirds required; "
              "net optimism +0.09, at most 0.05 allowed); confidence capped at 39.")
EM_DASH = chr(0x2014)


def _thin_target():
    return _frame(_stations("T", 3, l3="71"), _stations("D", 30, l3="65", flat=True))


def _even_values(frame) -> pd.Series:
    """Evenly spaced values: a drop-one quartile shift well inside the stability limit."""
    return pd.Series(np.linspace(10.0, 30.0, len(frame)), index=frame["station_key"].astype(str))


def _choose(frame, *, validation, target="71", values=None, flagged=FLAGGED, curve_check=True, **kw):
    values = _values(frame) if values is None else values
    accept = acceptance.pool_acceptor("m_form", ENTRY, validation, family="channel_form")
    check = acceptance.curve_checker("m_form", ENTRY) if curve_check else None
    return rp.choose_pool("m_form", values, frame, target, profile=rp.family_profile("m_form", CFG),
                          cfg=CFG, settings=SETTINGS, accept=accept, curve_check=check,
                          flagged=flagged, **kw)


# --------------------------------------------------------------------------- #
# the version, the block, the rule, the vocabulary
# --------------------------------------------------------------------------- #
def test_the_version_the_block_the_rule_and_the_mirrors():
    assert methodology.methodology_version() == "0.16-provisional"
    assert methodology.flagged_transfer() == FLAGGED
    assert acceptance.flagged_settings() == FLAGGED
    assert methodology.mirror_drift() == []
    rule = methodology.rule("REF-16")
    assert rule["name"] == "Flagged transfer" and rule["family"] == "REF"
    assert rule["threshold_status"] == "provisional" and rule["implementation_status"] == "implemented"
    for where in ("choose_pool", "try_national", "gate_flagged_transfer_disclosed", "reference_support.py"):
        assert where in rule["maps_to"], where
    assert "D11" in rule["note"] and "39" in rule["threshold"]
    for rid in ("ACC-05", "ACC-06"):
        assert "REF-16" in methodology.rule(rid)["test"] and "recorded" in methodology.rule(rid)["test"]
    c07 = methodology.rule("CURVE-07")
    assert "D13" in c07["note"] and "curve07-degenerate-refused" in c07["standing_decision_ids"]
    assert "D13" in methodology.rule("CURVE-12")["note"]
    assert rp.RISK_UNVALIDATED == "unvalidated" and rp.RISK_UNVALIDATED in rp.RISKS
    assert rp.RISK_WORDS[rp.RISK_UNVALIDATED].startswith("unvalidated")
    assert rp.RULE_FLAGGED == bl.RULE_FLAGGED == "REF-16"
    note = methodology.load_config()["meta"]["calibration_note"]
    assert "v0.16 (2026-09-28" in note and "flagged-transfer rung" in note
    assert methodology.threshold("standing_decisions.retired") == ["curve07-fallback-accepted"]
    # a config root with no block reads as the validated-only ladder of 0.15
    assert methodology.FLAGGED_TRANSFER_DEFAULT_CAP == 39


# --------------------------------------------------------------------------- #
# REF-16: the second pass over the station pools
# --------------------------------------------------------------------------- #
def test_a_pool_refused_by_the_evidence_alone_is_taken_in_the_second_pass():
    d, ledger = _choose(_thin_target(), validation={"m_form": {"2r_l2": REFUSED}})
    assert d.status == "borrowed_l2" and d.level == "l2" and d.n_usable == 33
    assert d.flagged and d.transfer_risk == rp.RISK_UNVALIDATED
    assert d.disposition == "adequate" and d.confidence_cap == 39
    v = d.transfer_validation
    assert v["basis"] == "2r_l2" and v["evidence"] == "metric" and v["accepted"] is False
    assert (v["a1_share"], v["net_opt"], v["n_cells"], v["verdict"]) == (0.56, 0.09, 6, "failed")
    assert v["min_share"] == 0.6667 and v["max_net_optimism"] == 0.05 and v["min_cells"] == 4
    assert "did not agree with reference often enough" in v["why"]
    # the note says where the stations come from, what the test found, and the cap
    note = d.transfer_note
    assert note.startswith("33 stations passing the regional least-disturbed screen")
    assert "Level II 8.3" in note and "3 of them inside this ecoregion" in note
    assert "Flagged transfer: the recovery test did not confirm this source" in note
    assert "class agreement 0.56 of 6 evaluation regions" in note and "two thirds" in note
    assert "net optimism +0.09" in note and "confidence is capped at 39" in note
    assert EM_DASH not in note
    # the options tried record the second pass and keep the first pass's words
    tried = {t["option"]: t for t in d.options_tried}
    assert tried["regional_l2"]["flagged"] is True and tried["regional_l2"]["accepted"] is True
    assert tried["regional_l2"]["why"].startswith("accepted under the flagged-transfer rung (REF-16)")
    assert "did not agree with reference often enough" in tried["regional_l2"]["why"]
    assert [c["check"] for c in tried["regional_l2"]["checks"]] == ["ACC-04", "ACC-05/06", "CURVE-07"]
    assert pv._pool_option(d.to_dict()) == "regional_l2"
    # the pool is the pool: the ledger holds the Level II stations
    assert set(ledger.loc[ledger["in_pool"], "level"]) == {"l2"} and int(ledger["in_pool"].sum()) == 33
    # the support record and the limitation, in the words DEEP prints
    rec = rp.reference_support_record(d)
    assert rec["transferRisk"] == "unvalidated" and rec["confidenceCap"] == 39 and rec["rule"] == "REF-16"
    assert rec["transferValidation"]["a1_share"] == 0.56
    assert rp.flagged_limitation(d) == rp.flagged_limitation(rec) == LIMITATION
    assert acceptance.transfer_limitation(v, 39) == LIMITATION
    assert json.dumps(d.to_dict())  # every field plain


def test_a_validated_pool_still_wins_and_carries_no_flag():
    d, _ = _choose(_thin_target(), validation={"m_form": {"2r_l2": ACCEPTED}})
    assert d.status == "borrowed_l2" and not d.flagged and d.transfer_risk == rp.RISK_UNASSESSED
    assert d.transfer_validation == {} and d.confidence_cap is None
    rec = rp.reference_support_record(d)
    assert "transferValidation" not in rec and "confidenceCap" not in rec and "rule" not in rec
    assert rp.flagged_limitation(d) == ""
    tried = {t["option"]: t for t in d.options_tried}
    assert tried["regional_l2"]["why"] == "accepted" and "flagged" not in tried["regional_l2"]
    assert [c["check"] for c in tried["regional_l2"]["checks"]] == ["ACC-04", "ACC-05/06", "CURVE-07"]


def test_a_pool_below_the_floor_is_never_taken():
    thin = _frame(_stations("T", 3, l3="71"), _stations("D", 6, l3="65", flat=True))
    d, _ = _choose(thin, validation={"m_form": {"2r_l2": REFUSED}}, values=_even_values(thin))
    assert d.status == rp.STATUS_INSUFFICIENT
    assert all(not t.get("flag_eligible") for t in d.options_tried)
    # the exploratory band is taken, and min_usable governs it
    exploratory = _frame(_stations("T", 3, l3="71"), _stations("D", 14, l3="65", flat=True))
    d2, _ = _choose(exploratory, validation={"m_form": {"2r_l2": REFUSED}},
                    values=_even_values(exploratory))
    assert d2.status == "borrowed_l2" and d2.flagged and d2.disposition == "exploratory"
    assert d2.n_usable == 17
    d3, _ = _choose(exploratory, validation={"m_form": {"2r_l2": REFUSED}},
                    values=_even_values(exploratory), flagged=dict(FLAGGED, min_usable=20))
    assert d3.status == rp.STATUS_INSUFFICIENT


def test_the_adequate_flagged_pool_is_preferred_over_a_narrower_exploratory_one():
    frame = _frame(_stations("T", 3, l3="71", l2="8.3", l1="8"),
                   _stations("D", 11, l3="65", l2="8.3", l1="8", flat=True),     # Level II: 14 usable
                   _stations("W", 30, l3="66", l2="8.4", l1="8", flat=True))     # Level I: 44 usable
    validation = {"m_form": {"2r_l2": REFUSED, "2r_l1": REFUSED}}
    d, _ = _choose(frame, validation=validation, values=_even_values(frame))
    assert d.status == "borrowed_l1" and d.flagged and d.disposition == "adequate" and d.n_usable == 44
    tried = {t["option"]: t for t in d.options_tried}
    assert tried["regional_l2"]["flag_eligible"] is True and "flagged" not in tried["regional_l2"]
    assert tried["regional_l1"]["flagged"] is True
    narrow, _ = _choose(frame, validation=validation, values=_even_values(frame),
                        flagged=dict(FLAGGED, prefer_adequate=False))
    assert narrow.status == "borrowed_l2" and narrow.flagged and narrow.disposition == "exploratory"


def test_the_rung_disabled_reproduces_the_validated_only_ladder():
    d, _ = _choose(_thin_target(), validation={"m_form": {"2r_l2": REFUSED}},
                   flagged=dict(FLAGGED, enabled=False))
    assert d.status == rp.STATUS_INSUFFICIENT
    tried = {t["option"]: t for t in d.options_tried}
    assert "flag_eligible" not in tried["regional_l2"]
    assert "did not agree with reference often enough" in tried["regional_l2"]["why"]


def test_a_stability_refusal_or_a_two_tuple_acceptor_is_never_flaggable():
    frame = _thin_target()
    profile = rp.family_profile("m_form", CFG)

    def unstable(option, values):
        return False, "The pool is not stable, since one station moves a quartile.", {
            "checks": [{"check": "ACC-04", "pass": False, "why": "The pool is not stable."}],
            "validation": None}

    d, _ = rp.choose_pool("m_form", _values(frame), frame, "71", profile=profile, cfg=CFG,
                          settings=SETTINGS, accept=unstable, flagged=FLAGGED)
    assert d.status == rp.STATUS_INSUFFICIENT
    assert all(not t.get("flag_eligible") for t in d.options_tried)
    d2, _ = rp.choose_pool("m_form", _values(frame), frame, "71", profile=profile, cfg=CFG,
                           settings=SETTINGS, accept=lambda o, v: (False, "refused"), flagged=FLAGGED)
    assert d2.status == rp.STATUS_INSUFFICIENT
    assert not acceptance.evidence_only_refusal(
        {"checks": [{"check": "ACC-04", "pass": False, "why": "x"},
                    {"check": "ACC-05/06", "pass": False, "why": "y"}]})
    assert acceptance.evidence_only_refusal(
        {"checks": [{"check": "ACC-04", "pass": True, "why": ""},
                    {"check": "ACC-05/06", "pass": False, "why": "y"}]})
    assert not acceptance.evidence_only_refusal(None) and not acceptance.evidence_only_refusal({"checks": []})


def test_the_local_reference_needs_no_evidence_and_is_never_flagged():
    frame = _frame(_stations("A", 30, l3="58", l2="5.3", l1="5"))
    d, _ = _choose(frame, target="58", validation={})
    assert d.status == rp.STATUS_LOCAL and not d.flagged and d.transfer_risk == rp.RISK_NONE
    tried = {t["option"]: t for t in d.options_tried}
    assert [c["check"] for c in tried["local"]["checks"]] == ["ACC-04", "CURVE-07"]


# --------------------------------------------------------------------------- #
# D13: degenerate and inverted curves are refusals
# --------------------------------------------------------------------------- #
def test_a_degenerate_pool_option_is_refused_and_the_ladder_moves_on():
    frame = _frame(_stations("T", 3, l3="71", l2="8.3", l1="8"),
                   _stations("D", 30, l3="65", l2="8.3", l1="8", flat=True),
                   _stations("W", 100, l3="66", l2="8.4", l1="8", flat=True))
    values = _even_values(frame)
    # the Level II donors all sit at zero: that pool's lower quartile is zero, so its
    # curve would be the engine's fallback ramp
    values[[k for k in values.index if k.startswith("D")]] = 0.0
    validation = {"m_form": {"2r_l2": ACCEPTED, "2r_l1": ACCEPTED}}
    d, _ = _choose(frame, validation=validation, values=values)
    assert d.status == "borrowed_l1" and d.n_usable == 133 and not d.flagged
    tried = {t["option"]: t for t in d.options_tried}
    l2 = tried["regional_l2"]
    assert l2["refused_by"] == "curve" and l2["curve"]["curve_status"] == "degenerate_q25"
    assert "fallback ramp" in l2["why"] and "D13" in l2["why"]
    assert [c["check"] for c in l2["checks"]] == ["ACC-04", "ACC-05/06", "CURVE-07"]
    assert l2["checks"][-1]["pass"] is False
    # without the check the same pool was taken (what 0.15 built)
    old, _ = _choose(frame, validation=validation, values=values, curve_check=False)
    assert old.status == "borrowed_l2"


def test_every_option_refused_as_degenerate_leaves_the_metric_withheld_with_the_reasons():
    frame = _thin_target()
    values = _values(frame)
    values[:] = 0.0
    values[list(values.index[:4])] = 5.0
    d, ledger = _choose(frame, validation={"m_form": {"2r_l2": ACCEPTED, "2r_l1": ACCEPTED}},
                        values=values)
    assert d.status == rp.STATUS_INSUFFICIENT and d.station_ids == ()
    assert not ledger["in_pool"].any()
    refused = [t for t in d.options_tried if t.get("refused_by") == "curve"]
    assert [t["option"] for t in refused] == ["regional_l2", "regional_l1"]
    assert all("fallback ramp" in t["why"] for t in refused)
    # the flagged rung never rescues a degenerate pool
    assert all(not t.get("flagged") and not t.get("flag_eligible") for t in d.options_tried)
    assert "Insufficient reference support" in d.transfer_note


def test_an_inverted_pool_option_is_refused():
    reference = _stations("D", 30, l3="65", l2="8.3", l1="8", flat=True)
    pressured = _stations("P", 20, l3="65", l2="8.3", l1="8", strict=False, relaxed=False, flat=True)
    frame = _frame(_stations("T", 3, l3="71", l2="8.3", l1="8"), reference, pressured)
    frame = frame.assign(screen_evaluable=True)
    values = _even_values(frame)
    # the pressured stations of the same geography carry values above every
    # reference station's, so a higher-is-better curve ranks them above reference
    values[[k for k in values.index if k.startswith("P")]] = np.linspace(50.0, 70.0, 20)
    validation = {"m_form": {"2r_l2": ACCEPTED, "2r_l1": ACCEPTED}}
    d, _ = _choose(frame, validation=validation, values=values)
    assert d.status == rp.STATUS_INSUFFICIENT
    tried = {t["option"]: t for t in d.options_tried}
    l2 = tried["regional_l2"]
    assert l2["refused_by"] == "curve" and l2["curve"]["curve12"]["verdict"] == "inverted"
    assert l2["curve"]["curve12"]["nPressure"] == 20 and l2["curve"]["curve12"]["aucRefVsPressure"] < 0.45
    assert "rank pressured stations above reference stations" in l2["why"] and "D13" in l2["why"]
    # with the check off the option is taken: the refusal is the check's
    d2, _ = _choose(frame, validation=validation, values=values, curve_check=False)
    assert d2.status == "borrowed_l2"
    # a pool whose pressured stations score below reference passes the check
    values2 = values.copy()
    values2[[k for k in values2.index if k.startswith("P")]] = np.linspace(1.0, 5.0, 20)
    d3, _ = _choose(frame, validation=validation, values=values2)
    assert d3.status == "borrowed_l2" and not d3.flagged
    tried3 = {t["option"]: t for t in d3.options_tried}
    assert tried3["regional_l2"]["curve"]["curve12"]["verdict"] in ("discriminates", "weak", "none")


def test_the_curve_checks_name_their_reasons():
    zeros = pd.Series([0.0] * 20 + [3.0, 4.0, 5.0, 6.0])
    ok, why, rec = acceptance.curve_checks("m", ENTRY, zeros)
    assert not ok and rec["curve_status"] == "degenerate_q25" and "fallback ramp" in why
    # a two-sided curve with no spread has no usable shape (a monotone ladder with no
    # spread is the engine's own concern and stays as the golden masters pin it)
    flat = pd.Series([7.0] * 12)
    two_sided = {**ENTRY, "curve_form": "optimum", "higher_is_better": None}
    ok, why, rec = acceptance.curve_checks("m", two_sided, flat)
    assert not ok and rec["curve_status"] == "degenerate_curve" and "no usable shape" in why
    few = pd.Series([1.0, 2.0, 3.0])
    ok, why, rec = acceptance.curve_checks("m", ENTRY, few)
    assert not ok and rec["curve_status"] == "insufficient_data"
    good = pd.Series(np.linspace(10.0, 30.0, 30))
    ok, why, rec = acceptance.curve_checks("m", ENTRY, good)
    assert ok and why == "" and rec["curve_status"] == "complete" and "curve12" not in rec
    # too few pressured stations: the inverted check does not run, the option passes
    ok, _why, rec = acceptance.curve_checks("m", ENTRY, good, pd.Series([90.0, 95.0]))
    assert ok and "curve12" not in rec
    for text in (why,):
        assert EM_DASH not in text
    # the REF-15 record of a forced source lists the check, ACC-03 staying last
    checks = [c["check"] for c in acceptance.all_checks("m", "3c_matched", list(zeros), ENTRY, {}, n=24)]
    assert checks == ["ACC-01", "ACC-04", "ACC-05/06", "CURVE-07", "ACC-03"]


# --------------------------------------------------------------------------- #
# REF-16: the national second pass and the order of the rungs
# --------------------------------------------------------------------------- #
def _transport(bound):
    return {"measure": "distance_median", "better": "max", "bound": bound}


def test_the_national_second_pass_and_the_walk_order():
    frame, wide = _national_frame()
    series = _series(frame, wide)
    refused = {"bent_HPRIME": {"3c_matched": dict(REFUSED, transport=_transport(10.0))}}
    got = bl.try_national("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                          validation=refused, entry=NATIONAL_CFG["bent_HPRIME"], flagged=FLAGGED)
    assert not got["admitted"] and got["flagged"] is not None
    fd = got["flagged"]["decision"]
    assert fd.status == rp.STATUS_NATIONAL and fd.basis == cb.NATIONAL and fd.flagged
    assert fd.transfer_risk == rp.RISK_UNVALIDATED and fd.confidence_cap == 39
    assert fd.transfer_validation["basis"] == got["flagged"]["option"]
    assert "Flagged transfer" in fd.transfer_note and "national pool" in fd.transfer_note
    assert "confidence is capped at 39" in fd.transfer_note
    tried = {t["option"]: t for t in got["options"]}
    assert tried[got["flagged"]["option"]]["flag_eligible"] is True
    checks = [c["check"] for c in tried["3c_matched"]["checks"]]
    assert checks == ["ACC-01", "ACC-04", "CURVE-07", "ACC-05/06", "ACC-03"]
    assert {t["option"] for t in fd.options_tried if t.get("flagged")} == {got["flagged"]["option"]}
    # accepted evidence: admitted, and nothing flagged
    ok = {"bent_HPRIME": {"3c_matched": _accepted(_transport(10.0))}}
    good = bl.try_national("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                           validation=ok, entry=NATIONAL_CFG["bent_HPRIME"], flagged=FLAGGED)
    assert good["admitted"] and good["flagged"] is None
    # outside the transport bound the option is refused and never flaggable: the
    # comparability and transport conditions stay on the flagged rung
    far = {"bent_HPRIME": {"3c_matched": dict(REFUSED, transport=_transport(0.0001)),
                           "3a_envelope": dict(REFUSED, transport={"measure": "n_fit", "better": "min",
                                                                   "bound": 100000})}}
    out = bl.try_national("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                          validation=far, entry=NATIONAL_CFG["bent_HPRIME"], flagged=FLAGGED)
    assert not out["admitted"] and out["flagged"] is None
    # the rung disabled: no flagged candidate
    off = bl.try_national("bent_HPRIME", frame=frame, values=series, target_l3=DIRTY,
                          validation=refused, entry=NATIONAL_CFG["bent_HPRIME"],
                          flagged=dict(FLAGGED, enabled=False))
    assert off["flagged"] is None
    # D13 on the national pool: the donors' curve is checked against the national
    # frame's pressured stations, and an inverted one is refused and never flagged.
    # Read lower-is-better, the same values rank the pressured (high agriculture,
    # low value) stations above reference, so the curve inverts.
    screened = frame.assign(pass_relaxed=frame["agriculture_ws"] < 25.0, screen_evaluable=True)
    upside_down = {**NATIONAL_CFG["bent_HPRIME"], "higher_is_better": False, "domain_min": None}
    inverted = bl.try_national("bent_HPRIME", frame=screened, values=series, target_l3=DIRTY,
                               validation=ok, entry=upside_down, flagged=FLAGGED)
    assert not inverted["admitted"] and inverted["flagged"] is None
    first = inverted["options"][0]
    assert first["curve"]["curve12"]["verdict"] == "inverted"
    assert "rank pressured stations above reference stations" in first["why"]
    # and the right way up the same check records the verdict on the accepted option
    right = bl.try_national("bent_HPRIME", frame=screened, values=series, target_l3=DIRTY,
                            validation=ok, entry=NATIONAL_CFG["bent_HPRIME"], flagged=FLAGGED)
    assert right["admitted"]
    assert right["options"][0]["curve"]["curve12"]["verdict"] in ("discriminates", "weak", "none")
    assert right["options"][0]["curve"]["curve12"]["nPressure"] > 0
    # a frame with no screen columns (no pressured group) runs the degenerate check only
    assert "curve12" not in good["options"][0]["curve"]
    # the walk: the flagged national pool is taken last, only for a metric the
    # caller names, after the modeled and published sources refused
    no_model = {**REGISTRY, "entries": []}
    walked = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                        metric_config=NATIONAL_CFG, validation=refused, registry=no_model,
                        flagged=FLAGGED, flag_national=["bent_HPRIME"])
    assert walked["decisions"] == {} and "bent_HPRIME" in walked["flagged_national"]
    rungs = [a["rung"] for a in walked["attempts"] if a["metric"] == "bent_HPRIME"]
    assert rungs == ["REF-12", "REF-13", "REF-14", "REF-16"]
    taken = walked["flagged_national"]["bent_HPRIME"]
    assert taken["row"] is not None and taken["row"]["curve_status"] == "complete"
    assert taken["decision"].flagged
    kept = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                      metric_config=NATIONAL_CFG, validation=refused, registry=no_model,
                      flagged=FLAGGED)
    assert kept["flagged_national"] == {}
    assert [a["rung"] for a in kept["attempts"] if a["metric"] == "bent_HPRIME"] == ["REF-12", "REF-13", "REF-14"]
    # an approved model still wins over the flagged national pool
    modeled = bl.resolve(["bent_HPRIME"], frame=frame, values_wide=wide, target_l3=DIRTY,
                         metric_config=NATIONAL_CFG, validation=refused, registry=REGISTRY,
                         flagged=FLAGGED, flag_national=["bent_HPRIME"])
    assert modeled["decisions"]["bent_HPRIME"].basis == cb.MODELED and modeled["flagged_national"] == {}


def test_the_pooled_frame_holds_only_the_pools_that_stand():
    frame = _thin_target()
    values = _values(frame)
    wide = pd.DataFrame({"site_id": values.index, "m_form": values.values})
    d, _ = _choose(frame, validation={"m_form": {"2r_l2": REFUSED}}, values=values)
    with_pool = rp.pool_data({"m_form": d}, wide, frame)
    assert "m_form" in with_pool.columns and with_pool["m_form"].notna().sum() == 33
    national = bl._decision("m_form", status=rp.STATUS_NATIONAL, basis=cb.NATIONAL, region_code="national",
                            region_name="National least-disturbed pool", n_pool=50, n_usable=20,
                            station_ids=list(values.index[:20]), n_huc12=5, note="", risk=rp.RISK_MODERATE,
                            tried=[], detail={})
    replaced = rp.pool_data({"m_form": national}, wide, frame)
    assert "m_form" not in replaced.columns and len(replaced) == 0


# --------------------------------------------------------------------------- #
# the confidence cap
# --------------------------------------------------------------------------- #
def test_the_confidence_cap_of_a_flagged_transfer():
    base = {"sample_disposition": "adequate", "missingness_disposition": "auto",
            "curve_status": "auto_ok", "loo": {"evaluable": True, "held_out_mean_abs_delta": 0.01},
            "bootstrap": {"evaluable": True, "structure_stability": 0.95, "shape_stability": 0.95},
            "influence": {"evaluable": True, "flagged": False}, "direction_confidence": "high",
            "shape_ok": True, "mapped": True, "units_present": True,
            "reference_tier": "least_disturbed", "basis": cb.REGIONAL, "screen": "regional"}
    flagged = conf.curve_confidence({**base, "transfer_risk": "unvalidated"})
    assert flagged["total"] <= 39 and "flagged_transfer_unvalidated" in flagged["caps_applied"]
    assert flagged["label"] == "Low"
    moderate = conf.curve_confidence({**base, "transfer_risk": "moderate"})
    assert moderate["total"] <= 59 and "flagged_transfer_unvalidated" not in moderate["caps_applied"]
    assert flagged["total"] < moderate["total"]
    # the cap is the one acting number of the config block
    assert flagged["total"] == float(methodology.flagged_transfer()["confidence_cap"])


# --------------------------------------------------------------------------- #
# the flag downstream: annotations, the bundle, the tables, the records
# --------------------------------------------------------------------------- #
def _flagged_decision():
    d, _ = _choose(_thin_target(), validation={"m_form": {"2r_l2": REFUSED}})
    return d


def test_the_annotations_and_the_bundle_entry_carry_the_four_fields_and_the_limitation():
    d = _flagged_decision()
    evidence = {"reference_support": {"m_form": d.to_dict()}, "local_comparison": {},
                "discrimination": {}, "strata_applied": {}}
    ann = pe.reference_annotations(evidence, ["m_form"])["m_form"]
    assert ann["transferRisk"] == "unvalidated" and ann["confidenceCap"] == 39
    assert ann["transferValidation"]["a1_share"] == 0.56 and ann["transferNote"] == d.transfer_note
    assert LIMITATION in ann["curveCaveats"]
    assert any(c.startswith("The reference stations for this curve were borrowed") for c in ann["curveCaveats"])
    assert ann["referenceSupport"]["transferRisk"] == "unvalidated"
    assert set(deep_export.FLAGGED_ENTRY_KEYS) <= set(pe.SESSION_ANNOTATION_KEYS)
    # the exporter writes the four fields on the metric entry
    row = {"metric": "m_form", "display_name": "Bank height ratio", "stratum": "", "n_reference": 33,
           "curve_status": "complete", "curve_source": "auto",
           "curve_points": pd.DataFrame({"point_order": [1, 2, 3], "metric_value": [0.0, 1.0, 2.0],
                                         "index_score": [0.0, 0.5, 1.0]})}
    mapping = pd.DataFrame([{"metric_key": "m_form", "discipline": "Geomorphology",
                             "function_label": "Channel evolution", "sort_order": 1}])
    bundle = deep_export.build_deep_assessment_bundle(
        [row], mapping, {"m_form": ENTRY},
        {"assessmentName": "Synthetic", "metricAnnotations": {"m_form": ann}})
    entries = [m for fn in bundle["metricsByFunction"] for m in fn["metrics"]]
    assert len(entries) == 1
    m = entries[0]
    for key in deep_export.FLAGGED_ENTRY_KEYS:
        assert m.get(key) == ann[key], key
    assert LIMITATION in m["curveCaveats"] and m["referenceSupport"]["rule"] == "REF-16"
    assert [f["metricId"] for f in deep_export.flagged_entries(bundle)] == [m["metricId"]]
    assert deep_export.fallback_entries(bundle) == []
    # the method block, the statement and the tables count and carry the flag
    block = pe.reference_method_block({**evidence, "reference_screen": {}, "reference_pool_summary": {}})
    assert block["nCurvesFlagged"] == 1 and "flagged transfer" in block["statement"]
    table = rp.support_table({"m_form": d})
    assert table.loc[0, "confidence_cap"] == 39 and "2r_l2" in table.loc[0, "transfer_validation"]
    csv = pe.support_frame({**evidence, "metric_config": {"m_form": ENTRY}, "fixed_metrics": {}})
    assert bool(csv.loc[0, "flagged"]) and csv.loc[0, "confidence_cap"] == 39
    assert "a1_share 0.5600" in csv.loc[0, "transfer_validation"]
    assert rp.validation_words({}) == ""


def test_a_flagged_curve_ranks_last_in_a_functions_portfolio():
    """SELECT-04: a validated curve of any source wins a function's place over a
    flagged one (REF-16), so a flagged pool never displaces a validated national,
    modeled or published curve."""
    flagged = _flagged_decision().to_dict()
    assert pe.source_of(flagged) == "flagged"
    assert pe.source_of({"basis": cb.REGIONAL, "status": "borrowed_l2", "transfer_risk": "low"}) == "regional"
    assert pe.source_of({"basis": cb.NATIONAL, "status": "national", "transfer_risk": "moderate"}) == "national"
    rank = methodology.threshold("metric_portfolio.source_rank")
    # a flagged curve ranks after every validated source; only the last resort
    # (REF-17, which completes an otherwise empty function) ranks after it
    assert rank["flagged"] == 5 and rank["flagged"] > max(
        v for k, v in rank.items() if k not in ("flagged", "pathway"))
    assert rank["pathway"] > rank["flagged"]
    national = {"basis": cb.NATIONAL, "status": rp.STATUS_NATIONAL, "transfer_risk": "moderate"}
    published = {"basis": cb.PUBLISHED, "status": rp.STATUS_PUBLISHED, "transfer_risk": "none"}
    evidence = {"reference_support": {"m_flag": flagged, "m_nat": national, "m_pub": published},
                "carried": {}, "carry_rebuilt": {}, "fixed_metrics": {}, "metric_config": {},
                "insufficient_support": {}}
    rows = {mk: {"metric": mk} for mk in ("m_flag", "m_nat", "m_pub")}
    mapping = pd.DataFrame([{"metric_key": mk, "discipline": "Geomorphology",
                             "function_label": "Channel evolution", "sort_order": i}
                            for i, mk in enumerate(("m_flag", "m_nat", "m_pub"), start=1)])
    scores = {"m_flag": {"total": 100.0}, "m_nat": {"total": 50.0}, "m_pub": {"total": 40.0}}
    meta: dict = {}
    out_rows, out_mapping = pe.select_portfolio(evidence, rows, mapping, {}, scores, meta)
    assert set(out_rows) == {"m_nat", "m_pub"}
    sel = meta["portfolioSelection"]["channel-evolution"]
    assert sel["selected"] == ["m_nat", "m_pub"]
    assert [(x["metric"], x["source"]) for x in sel["notSelected"]] == [("m_flag", "flagged")]
    # with only the flagged curve and one validated curve, both places are filled
    two = {"reference_support": {"m_flag": flagged, "m_nat": national}, "carried": {},
           "carry_rebuilt": {}, "fixed_metrics": {}, "metric_config": {}, "insufficient_support": {}}
    meta2: dict = {}
    out2, _ = pe.select_portfolio(two, {mk: rows[mk] for mk in ("m_flag", "m_nat")},
                                  mapping[mapping["metric_key"] != "m_pub"], {}, scores, meta2)
    assert set(out2) == {"m_flag", "m_nat"}
    assert meta2["portfolioSelection"]["channel-evolution"]["selected"] == ["m_nat", "m_flag"]
    # a validated reserve candidate wins the place over a flagged regular one: the
    # function would otherwise be assessed by a validated source only through it
    regional = {"basis": cb.REGIONAL, "status": "borrowed_l1", "transfer_risk": "unassessed"}
    three = {"reference_support": {"m_flag": flagged, "m_res": regional}, "carried": {},
             "carry_rebuilt": {}, "fixed_metrics": {}, "metric_config": {}, "insufficient_support": {}}
    mapping3 = pd.DataFrame([{"metric_key": mk, "discipline": "Biology",
                              "function_label": "Population support", "sort_order": i}
                             for i, mk in enumerate(("m_flag", "m_res"), start=1)])
    meta3: dict = {}
    out3, _ = pe.select_portfolio(three, {"m_flag": {"metric": "m_flag"}, "m_res": {"metric": "m_res"}},
                                  mapping3, {"m_res": {"reserve": True}},
                                  {"m_flag": {"total": 100.0}, "m_res": {"total": 50.0}}, meta3)
    sel3 = meta3["portfolioSelection"]["population-support"]
    assert sel3["selected"] == ["m_res", "m_flag"] and set(out3) == {"m_flag", "m_res"}
    # and a validated regular curve keeps a validated reserve out, as before
    four = {"reference_support": {"m_reg": regional, "m_res": regional}, "carried": {},
            "carry_rebuilt": {}, "fixed_metrics": {}, "metric_config": {}, "insufficient_support": {}}
    mapping4 = mapping3.assign(metric_key=["m_reg", "m_res"])
    meta4: dict = {}
    out4, _ = pe.select_portfolio(four, {"m_reg": {"metric": "m_reg"}, "m_res": {"metric": "m_res"}},
                                  mapping4, {"m_res": {"reserve": True}},
                                  {"m_reg": {"total": 60.0}, "m_res": {"total": 90.0}}, meta4)
    assert meta4["portfolioSelection"]["population-support"]["selected"] == ["m_reg"]
    assert set(out4) == {"m_reg"}


def test_the_calculator_prints_the_same_words():
    from streamcurves import deep_calculator as dc
    d = _flagged_decision()
    rec = rp.reference_support_record(d)
    entry = {"metricId": "spring-m-form", "basis": cb.REGIONAL, "criteriaBasis": "reference",
             "referenceSupport": rec, "transferRisk": rec["transferRisk"],
             "transferValidation": rec["transferValidation"], "transferNote": rec["transferNote"],
             "confidenceCap": rec["confidenceCap"]}
    text = dc.support_text(entry)
    assert text.startswith("33 least-disturbed stations borrowed from Level II ecoregion 8.3 (L2 8.3)")
    assert "transfer risk unvalidated (transfer not confirmed by the recovery test)" in text
    assert text.endswith(LIMITATION) and EM_DASH not in text
    assert dc.flagged_note(entry) == LIMITATION
    # the words come from the support record when the entry carries none of the four fields
    only_support = {"metricId": "spring-m-form", "basis": cb.REGIONAL, "criteriaBasis": "reference",
                    "referenceSupport": rec}
    assert dc.flagged_note(only_support) == LIMITATION
    plain = {"metricId": "spring-x", "referenceSupport": {"status": "local", "nUsable": 30,
                                                         "transferRisk": "none"}}
    assert dc.flagged_note(plain) == "" and "unvalidated" not in dc.support_text(plain)


def test_the_records_name_ref_16_and_raise_no_review_item():
    d = _flagged_decision()
    result = {"region": {"kind": "ecoregion", "code": "71"}, "reference_method": pe.METHOD,
              "screening_counts": {"n_retained": 3},
              "reference_screen": {"id": "least-disturbed-v1", "tier": "strict"},
              "reference_pool_summary": {}, "reference_support": {"m_form": d.to_dict()},
              "local_comparison": {}, "ladder_attempts": [], "insufficient_support": {},
              "curve_rows": {"m_form": {"n_reference": 33}},
              "curve_review": {"m_form": {"status": "auto_ok"}},
              "sample_sizes": {"m_form": {"n": 33, "disposition": "adequate"}},
              "metric_config": {"m_form": ENTRY},
              "value_selection": {"policy": "newest-nonnull-v2", "byMetricCycle": {}},
              "scale_registry": {"present": False}, "strata_applied": {}, "fixed_metrics": {},
              "discrimination": {}, "diagnostics": {}, "portfolio": [], "coverage": {},
              "missingness": {}, "meta": {}}
    records = pv.build_records(result, {"inputsDigest": "sha256:" + "a" * 64},
                               timestamp="2026-09-28T00:00:00+00:00")
    by = {(r["rule_id"], r["subject"]): r for r in records}
    rec = by[("REF-16", "m_form")]
    assert rec["verdict"] == pv.VERDICT_PASS and not rec["review_required"]
    assert rec["computed"]["transfer_risk"] == "unvalidated" and rec["computed"]["confidence_cap"] == 39
    assert rec["computed"]["transfer_validation"]["a1_share"] == 0.56
    assert rec["thresholds_used"]["flagged_transfer"]["confidence_cap"] == 39
    assert ("REF-11", "m_form") not in by and ("REF-05", "m_form") not in by
    queue = pv.build_review_queue(records, {"inputsDigest": "x"})
    assert not any(i["item_id"].startswith("REF-16") for i in queue["items"])


def test_publish_refuses_a_fallback_curve_in_the_bundle(tmp_path):
    bundle = {"metricsByFunction": [{"functionId": "habitat-provision", "metrics": [
        {"metricId": "spring-phab-pct-fast", "curveStatus": "degenerate_q25"}]}]}
    assert deep_export.fallback_entries(bundle) == ["habitat-provision: spring-phab-pct-fast"]
    assert deep_export.FALLBACK_CURVE_STATUSES == ("degenerate_q25", "degenerate_curve")
    with pytest.raises(RuntimeError, match="REFUSED"):
        ra.publish({"bundle": bundle, "meta": {"assessmentName": "x"}, "assessment_id": "x"}, tmp_path)
    assert not (tmp_path / "assessments").exists()


# --------------------------------------------------------------------------- #
# standing decisions policy 1.5
# --------------------------------------------------------------------------- #
def _degenerate_item(subject="phab_PCT_FAST"):
    return {"item_id": f"CURVE-07:{subject}", "rule_ids": ["CURVE-07"], "subject": subject,
            "trigger": "curve_needs_review", "status": "open", "blocking": False, "question": "?",
            "allowed_actions": list(dec.ALLOWED_ACTIONS),
            "evidence": {"curve_status": "degenerate", "domain_violations": 0,
                         "reasons": ["Non-positive or non-finite Q25 produced a fallback curve."]}}


def test_policy_1_5_retires_the_acceptance_and_records_the_refusal():
    policy = dec.load_policy()
    assert dec.validate_policy(policy) == [] and dec.policy_version(policy) == "1.5"
    assert policy["meta"]["methodology_version"] == "0.16-provisional"
    by = dec.entries_by_id(policy)
    old = by["curve07-fallback-accepted"]
    assert old["retired_in_policy"] == "1.5" and old["enabled"] is True
    new = by["curve07-degenerate-refused"]
    assert new["introduced_in_policy"] == "1.5" and new["action"] == "reject"
    assert new["side_effect"] == "remove_metric" and new["approved_on"] is None
    assert "pending owner confirmation" in new["approved_under"]
    assert dec.entry_applies(old, None, "1.5") is False
    assert dec.entry_applies(old, "1.4", "1.5") is True
    assert dec.entry_applies(old, dec.ERA_UNRECORDED, "1.5") is True
    assert dec.entry_applies(new, None, "1.5") is True and dec.entry_applies(new, "1.4", "1.5") is False
    doc = {"records": [], "manifest": {}, "reviewQueue": {"items": [_degenerate_item()], "counts": {"open": 1}}}
    res = dec.apply_policy(doc, policy)
    d = res.decisions[0]
    assert d["decision_class"] == "curve07-degenerate-refused" and d["action"] == "reject"
    assert res.remove_metrics == {"phab_PCT_FAST": d["rationale"]} and res.finalize_metrics == {}
    assert "owner decision D13" in d["rationale"] and "no fallback curve is published" in d["rationale"]
    assert "Non-positive or non-finite Q25" in d["rationale"] and EM_DASH not in d["rationale"]
    assert d["asserts"] == {"curve_status": "degenerate"} and res.hard_stops == [] and res.uncovered == []
    # the recorded era decides: a version recorded under 1.3 or 1.4, or none, still accepts
    for era in ("1.4", "1.3", dec.ERA_UNRECORDED):
        past = dec.apply_policy(doc, policy, era=era)
        assert past.decisions[0]["decision_class"] == "curve07-fallback-accepted", era
        assert past.remove_metrics == {} and list(past.finalize_metrics) == ["phab_PCT_FAST"]
    assert dec.recorded_policy_version({"manifest": {"standingDecisions": {"policyVersion": "1.4"}}}) == "1.4"
    assert dec.recorded_policy_version({}) == dec.ERA_UNRECORDED
    # the validator refuses an era key newer than the file and a disabled retired entry
    broken = copy.deepcopy(policy)
    dec.entries_by_id(broken)["curve07-degenerate-refused"]["introduced_in_policy"] = "9.9"
    assert any("newer than this policy" in p for p in dec.validate_policy(broken))
    broken = copy.deepcopy(policy)
    dec.entries_by_id(broken)["curve07-fallback-accepted"]["enabled"] = False
    assert any("retired entry stays enabled" in p for p in dec.validate_policy(broken))
    # the side effect is a known one
    assert "remove_metric" in dec.SIDE_EFFECTS


def test_the_scorecard_replays_every_published_version_without_a_mismatch():
    scripts = Path(__file__).resolve().parents[1] / "scripts" / "rules_scorecard.py"
    spec = importlib.util.spec_from_file_location("rules_scorecard_016", scripts)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = Path(__file__).resolve().parents[2] / "library"
    if not (root / "assessments").is_dir():
        pytest.skip("the assessment library is not present")
    rows = mod.scorecard(root, dec.load_policy())
    assert rows
    assert [f"{r['assessment']}/{r['version']}" for r in rows if r["latest"] and r["mismatches"]] == []
    # the retired acceptance still replays the versions that recorded it: the pilot
    # Eastern Corn Belt Plains v3 aliases its fallback curve to the 1.4-era entry
    by = {(r["assessment"], r["version"]): r for r in rows}
    ecbp3 = by.get(("eastern-corn-belt-plains", "v3"))
    if ecbp3 is not None:
        assert ecbp3["all_optional"].get("alias_match", 0) >= 1 and not ecbp3["mismatches"]


# --------------------------------------------------------------------------- #
# promotion policy 1.2
# --------------------------------------------------------------------------- #
def test_promotion_policy_1_2_and_the_disclosure_gate(tmp_path, monkeypatch):
    policy = camp.load_promotion_policy()
    assert camp.validate_promotion_policy(policy) == [] and policy["meta"]["version"] == "1.2"
    assert camp.accepted_support_classes(policy) == ["noLocal", "frameUnder10"]
    assert "D14" in policy["meta"]["owner_decision"]
    assert "no local reference station" in policy["acceptance"]["statement"]
    assert "flagged-transfer-disclosed" in [g["id"] for g in policy["gates"]]
    assert "flagged-transfer-disclosed" in camp.GATE_IDS and "flagged-transfer-disclosed" in camp.RUN_FOLDER_GATES
    vdir = tmp_path / "v1"
    vdir.mkdir()
    monkeypatch.setattr(camp, "staged_version_dir", lambda run_dir, packet=None: vdir)

    def stage(bundle):
        (vdir / camp.BUNDLE_FILE).write_text(json.dumps(bundle), encoding="utf-8")

    entry = {"metricId": "spring-m-form", "transferRisk": "unvalidated",
             "transferValidation": {"a1_share": 0.56}, "transferNote": "note", "confidenceCap": 39,
             "curveCaveats": [LIMITATION], "referenceSupport": {"transferRisk": "unvalidated"}}
    stage({"metricsByFunction": [{"functionId": "f", "metrics": [entry]}]})
    ok, detail = camp.gate_flagged_transfer_disclosed(tmp_path)
    assert ok and "1 flagged curve" in detail
    stage({"metricsByFunction": [{"functionId": "f", "metrics": [
        {k: v for k, v in entry.items() if k != "confidenceCap"}]}]})
    ok, detail = camp.gate_flagged_transfer_disclosed(tmp_path)
    assert not ok and "confidenceCap" in detail
    stage({"metricsByFunction": [{"functionId": "f", "metrics": [{**entry, "curveCaveats": []}]}]})
    ok, detail = camp.gate_flagged_transfer_disclosed(tmp_path)
    assert not ok and "limitation caveat" in detail
    # a flag only inside referenceSupport (no entry fields) is not disclosed
    stage({"metricsByFunction": [{"functionId": "f", "metrics": [
        {"metricId": "spring-m-form", "referenceSupport": {"transferRisk": "unvalidated"}}]}]})
    ok, detail = camp.gate_flagged_transfer_disclosed(tmp_path)
    assert not ok and "transferRisk" in detail
    stage({"metricsByFunction": [{"functionId": "f", "metrics": [
        {"metricId": "spring-x", "referenceSupport": {"transferRisk": "low"}}]}]})
    ok, detail = camp.gate_flagged_transfer_disclosed(tmp_path)
    assert ok and "no curve on a flagged transfer" in detail
    broken = copy.deepcopy(policy)
    broken["gates"] = [g for g in broken["gates"] if g["id"] != "flagged-transfer-disclosed"]
    assert any("no gate flagged-transfer-disclosed" in p for p in camp.validate_promotion_policy(broken))
    broken2 = copy.deepcopy(policy)
    broken2["acceptance"]["preliminary_eligible_support_classes"] = ["nope"]
    assert any("unknown support class" in p for p in camp.validate_promotion_policy(broken2))
    assert camp.promotion_policy_record(policy)["acceptedSupportClasses"] == ["noLocal", "frameUnder10"]

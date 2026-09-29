"""Standing decisions policy 1.3 (campaign Round 2 close, WP-015b): each new entry
matches a synthetic item built from the fast-pass evidence shapes and leaves its
counter-case open; every application is pending owner confirmation; the replay reports
a policy decision on an item the record left open under its own outcome; the
stratifier class sizes are read from the manifest, not from the labels' digits."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from streamcurves import decisions as dec
from streamcurves import methodology
from streamcurves import provenance as pv

POLICY_PATH = Path(dec.POLICY_PATH)
NEW_IDS = ["strat09-advisory-not-applied", "curve07-fallback-accepted", "curve12-inverted-thin",
           "red01-keep-both-distinct-axes", "curve06-no-interval-fallback-curve",
           "curve06-no-interval-flagged"]
LEGACY_IDS = ["ref02-accept-best-available", "data03-thin-metric-finalized",
              "data06-insufficient-finalized", "curve07-thin-metric-finalized"]
FLOOR = int(methodology.threshold("data_rules.min_n_stratum"))
GROUP_FLOOR = int(methodology.threshold("data_rules.min_n_unstratified"))


@pytest.fixture(scope="module")
def policy():
    return dec.load_policy()


def _item(rule_id, subject, trigger, evidence, blocking=False):
    return {"item_id": f"{rule_id}:{subject}", "rule_ids": [rule_id], "subject": subject,
            "trigger": trigger, "evidence": evidence, "status": "open", "blocking": blocking,
            "question": "?", "allowed_actions": list(dec.ALLOWED_ACTIONS)}


def _doc(items, records=None, manifest=None):
    return {"records": records or [], "manifest": manifest or {},
            "reviewQueue": {"items": items, "counts": {"open": len(items)}}}


def _curve12(subject, verdict, auc, n_ref=34, n_pressure=17):
    return pv._record("r", "1", "CURVE-12", "metric", subject,
                      computed={"auc_ref_vs_pressure": auc, "n_ref": n_ref, "n_pressure": n_pressure,
                                "median_ref_index": 0.87, "median_pressure_index": 1.0,
                                "auc_r_vs_im": None, "verdict": verdict})


def _curve07(subject, status="degenerate", violations=0):
    return pv._record("r", "1", "CURVE-07", "metric", subject,
                      computed={"curve_status": status,
                                "reasons": ["Non-positive or non-finite Q25 produced a fallback curve."],
                                "domain_min": 0.0, "domain_max": None, "domain_violations": violations,
                                "reviewer_decision": "pending"})


def _fallback_item(subject, status="degenerate", violations=0):
    return _item("CURVE-07", subject, "curve_needs_review",
                 {"curve_status": status, "reasons": ["Non-positive or non-finite Q25 produced a fallback curve."],
                  "domain_min": 0.0, "domain_max": None, "domain_violations": violations,
                  "reviewer_decision": "pending"})


def _interval_item(subject):
    return _item("CURVE-06", subject, "no_interval",
                 {"evaluable": False, "structure_stability": 0.0, "shape_stability": 0.0, "n_boot": 200,
                  "seed": 7, "n_matched": 0,
                  "point_intervals": [{"index_score": 0.0, "x": 0.0, "x_lo": None, "x_hi": None, "n_matched": 0}]})


def _decided(res):
    return {d["subject"]: d for d in res.decisions}


# --------------------------------------------------------------------------- #
# the file
# --------------------------------------------------------------------------- #
def test_policy_1_3_validates_and_carries_the_new_entries(policy):
    assert dec.validate_policy(policy) == []
    assert dec.policy_version(policy) == "1.5"
    by_id = dec.entries_by_id(policy)
    assert set(NEW_IDS) <= set(by_id)
    # the 1.3 entries all apply to a version recorded under 1.4; under 1.5 the
    # retired curve07-fallback-accepted gives way to curve07-degenerate-refused
    enabled = {e["id"] for e in dec.enabled_entries(policy, era="1.4")}
    assert set(NEW_IDS) <= enabled and not (set(LEGACY_IDS) & enabled)
    now = {e["id"] for e in dec.enabled_entries(policy)}
    assert "curve07-fallback-accepted" not in now and "curve07-degenerate-refused" in now
    assert by_id["curve07-fallback-accepted"]["retired_in_policy"] == "1.5"
    assert by_id["curve07-degenerate-refused"]["introduced_in_policy"] == "1.5"
    for eid in NEW_IDS:
        e = by_id[eid]
        assert e.get("approved_on") is None, eid
        assert "pending owner confirmation" in str(e.get("approved_under")), eid
        assert dec.pending_reviewer(eid, policy) == f"standing-policy:{eid} (pending owner confirmation)"
    for eid in LEGACY_IDS:
        assert by_id[eid].get("legacy") is True and by_id[eid].get("enabled") is False, eid
    for eid in ("curve04-accept-with-flag", "data05-exploratory-pool-accepted", "red06-instability-is-noise",
                "strat09-defer-floors", "select01-complementary-set", "ref05-borrowed-pool-accepted",
                "cov01-documented-gap"):
        assert by_id[eid].get("approved_on") and by_id[eid].get("reaffirmed_under") == "0.15-provisional", eid
    text = POLICY_PATH.read_text(encoding="utf-8")
    assert chr(0x2014) not in text
    assert "GM" not in text.replace("GMT", "")
    assert by_id["curve07-fallback-accepted"]["aliases"] == ["curve07-thin-metric-finalized",
                                                             "curve07-fallback-curve-preliminary"]
    assert by_id["strat09-advisory-not-applied"]["aliases"] == ["strat09-defer-floors"]


def test_validate_refuses_an_unapproved_entry_that_claims_no_pending_status(policy):
    broken = json.loads(json.dumps(policy))
    entry = dec.entries_by_id(broken)["curve12-inverted-thin"]
    entry["approved_under"] = "0.15-provisional"
    problems = dec.validate_policy(broken)
    assert any("approved_on is empty" in p for p in problems)
    broken = json.loads(json.dumps(policy))
    dec.entries_by_id(broken)["data03-thin-metric-finalized"]["enabled"] = True
    assert any("legacy entry cannot be enabled" in p for p in dec.validate_policy(broken))
    broken = json.loads(json.dumps(policy))
    dec.entries_by_id(broken)["strat09-advisory-not-applied"]["aliases"] = "strat09-defer-floors"
    assert any("aliases must be a list" in p for p in dec.validate_policy(broken))


# --------------------------------------------------------------------------- #
# CURVE-07: a fallback curve the discrimination check does not contradict
# --------------------------------------------------------------------------- #
def test_curve07_fallback_accepted_unless_inverted_or_outside_the_domain(policy):
    """The 1.4 era's rule, which the replay of the versions recorded under 1.3 and
    1.4 still applies (policy 1.5 retired the entry for new builds: see
    test_methodology_016 for the refusal that replaced it)."""
    items = [_fallback_item("phab_PCT_FAST"),            # CURVE-12 none
             _fallback_item("fish_NAT_TOTLNTAX"),        # CURVE-12 inverted: the owner's
             _fallback_item("pctwet2019ws"),             # no CURVE-12 record: a build before 0.12
             _fallback_item("phab_XBKA", violations=1),  # an anchor outside the domain
             _fallback_item("phab_SINU", status="data_review"),
             _fallback_item("chem_PH", status="shape_conflict")]
    records = [_curve12("phab_PCT_FAST", "none", 0.499), _curve12("fish_NAT_TOTLNTAX", "inverted", 0.375),
               _curve12("phab_XBKA", "weak", 0.58), _curve12("phab_SINU", "weak", 0.57)]
    res = dec.apply_policy(_doc(items, records=records), policy, era="1.4")
    by = _decided(res)
    assert set(by) == {"phab_PCT_FAST", "pctwet2019ws"}
    d = by["phab_PCT_FAST"]
    assert d["decision_class"] == "curve07-fallback-accepted" and d["action"] == "accept_with_conditions"
    assert d["reviewer"] == "standing-policy:curve07-fallback-accepted (pending owner confirmation)"
    assert d["rationale_origin"] == "standing_policy:1.5"
    assert d["asserts"] == {"curve_status": "degenerate", "domain_violations": 0}
    assert "verdict none, AUC 0.499" in d["rationale"] and "marked for verification" in d["rationale"]
    assert "Non-positive or non-finite Q25" in d["rationale"]
    assert "verdict not_recorded" in by["pctwet2019ws"]["rationale"]
    # what stays open is a hard stop: the inverted fallback, the domain violation,
    # the thin metric (legacy entry off) and the shape conflict
    assert [h["item_id"] for h in res.hard_stops] == [
        "CURVE-07:fish_NAT_TOTLNTAX", "CURVE-07:phab_XBKA", "CURVE-07:phab_SINU", "CURVE-07:chem_PH"]
    # policy 1.4: the two accepted fallback curves publish as preliminary (finalized), as
    # the owner's own answer to the item does; nothing else is finalized
    assert set(res.finalize_metrics) == {"phab_PCT_FAST", "pctwet2019ws"}
    assert res.finalize_metrics["phab_PCT_FAST"] == d["rationale"]


# --------------------------------------------------------------------------- #
# CURVE-12: an inverted verdict on a thin group
# --------------------------------------------------------------------------- #
def _inverted(subject, n_ref, n_pressure, auc=0.35):
    return _item("CURVE-12", subject, "inverted_discrimination",
                 {"auc_ref_vs_pressure": auc, "n_ref": n_ref, "n_pressure": n_pressure,
                  "median_ref_index": 0.872, "median_pressure_index": 1.0, "auc_r_vs_im": None,
                  "verdict": "inverted"})


def test_curve12_inverted_thin_needs_a_group_below_the_floor(policy):
    items = [_inverted("chem_COND", 34, 17), _inverted("bent_HPRIME", 19, 21),
             _inverted("fish_NAT_TOTLNTAX", 66, 60), _inverted("phab_LSUB_DMM", GROUP_FLOOR, GROUP_FLOOR + 5)]
    res = dec.apply_policy(_doc(items), policy)
    by = _decided(res)
    assert set(by) == {"chem_COND", "bent_HPRIME"}
    d = by["chem_COND"]
    assert d["decision_class"] == "curve12-inverted-thin" and d["action"] == "accept_with_conditions"
    assert d["asserts"] == {"verdict": "inverted", "n_ref": 34, "n_pressure": 17}
    assert "34 reference and 17 pressured stations" in d["rationale"]
    assert f"a group of 17 stations, below the DATA-04 automated floor of {GROUP_FLOOR}" in d["rationale"]
    assert "a group of 19 stations" in by["bent_HPRIME"]["rationale"]
    # both groups at the floor or above: the owner's
    assert {u["item_id"] for u in res.uncovered} == {"CURVE-12:fish_NAT_TOTLNTAX", "CURVE-12:phab_LSUB_DMM"}
    assert res.hard_stops == []
    ev = dec.enrich_evidence(items[0], _doc(items))
    assert ev["min_group_n"] == 17 and ev["group_floor_n"] == GROUP_FLOOR


# --------------------------------------------------------------------------- #
# RED-01: a pair across functions keeps both metrics
# --------------------------------------------------------------------------- #
def test_red01_keeps_both_only_across_functions(policy):
    items = [_item("RED-01", "chem_COND|phab_XCMGW", "redundant_pair",
                   {"spearman": -0.83, "pearson": -0.79, "same_function": False}),
             _item("RED-01", "phab_PCT_SAFN|phab_XEMBED", "redundant_pair",
                   {"spearman": 0.8455, "pearson": 0.882, "same_function": True})]
    res = dec.apply_policy(_doc(items), policy)
    by = _decided(res)
    assert list(by) == ["chem_COND|phab_XCMGW"]
    d = by["chem_COND|phab_XCMGW"]
    assert d["decision_class"] == "red01-keep-both-distinct-axes" and d["action"] == "accept"
    assert d["asserts"] == {"same_function": False}
    assert "Spearman -0.830" in d["rationale"] and "different functions" in d["rationale"]
    assert [u["item_id"] for u in res.uncovered] == ["RED-01:phab_PCT_SAFN|phab_XEMBED"]


# --------------------------------------------------------------------------- #
# CURVE-06: a fallback curve has no interval
# --------------------------------------------------------------------------- #
def test_curve06_two_eras_and_the_inverted_counter_case(policy):
    items = [_interval_item("phab_PCT_FAST"),      # CURVE-12 weak: the D9 condition
             _interval_item("pctwet2019ws"),       # no CURVE-12 record: the pilots' class
             _interval_item("fish_NAT_TOTLNTAX"),  # CURVE-12 inverted: the owner's
             _interval_item("chem_PH")]            # no CURVE-07 record: not a fallback curve
    records = [_curve07("phab_PCT_FAST"), _curve12("phab_PCT_FAST", "weak", 0.578),
               _curve07("pctwet2019ws"),
               _curve07("fish_NAT_TOTLNTAX"), _curve12("fish_NAT_TOTLNTAX", "inverted", 0.375),
               _curve12("chem_PH", "none", 0.5)]
    res = dec.apply_policy(_doc(items, records=records), policy)
    by = _decided(res)
    assert set(by) == {"phab_PCT_FAST", "pctwet2019ws"}
    flagged = by["phab_PCT_FAST"]
    assert flagged["decision_class"] == "curve06-no-interval-flagged"
    assert flagged["action"] == "accept_with_conditions"
    assert flagged["asserts"] == {"evaluable": False, "n_matched": 0}
    assert "0 of 200 resamples" in flagged["rationale"] and "verdict weak, AUC 0.578" in flagged["rationale"]
    assert "marked for verification" in flagged["rationale"]
    legacy = by["pctwet2019ws"]
    assert legacy["decision_class"] == "curve06-no-interval-fallback-curve" and legacy["action"] == "accept"
    assert "a build before CURVE-12" in legacy["rationale"]
    assert {u["item_id"] for u in res.uncovered} == {"CURVE-06:fish_NAT_TOTLNTAX", "CURVE-06:chem_PH"}
    assert res.hard_stops == []


# --------------------------------------------------------------------------- #
# STRAT-09: the complement of defer-floors, and the class sizes read right
# --------------------------------------------------------------------------- #
def test_strat09_advisory_not_applied_is_the_complement_of_defer_floors(policy):
    manifest = {"stratifiers": {"candidates": [
        {"stratification": "ElevationClass", "level_counts": "Upland (300 to 1000 m)=31|Montane (> 1000 m)=39",
         "min_populated_n": 31},
        {"stratification": "DrainageAreaClass",
         "level_counts": "Headwater (<= 10 km2)=6|Small (10 to 100 km2)=33|Large (> 100 km2)=31",
         "min_populated_n": 6},
        {"stratification": "ChannelSlopeClass",
         "level_counts": "Low gradient (<= 0.5%)=18|Moderate (0.5 to 2%)=19|Steep (> 2%)=29"},
        {"stratification": "Supporting", "level_counts": "A=40|B=45", "min_populated_n": 40}]}}
    ev = {"n_metrics_tested": 13, "n_significant": 4, "consistency_score": 0.585, "tier": "Broad-Use Candidate"}
    items = [_item("STRAT-09", s, "advisory_stratifier_not_applied", dict(ev))
             for s in ("ElevationClass", "DrainageAreaClass", "ChannelSlopeClass")]
    items.append(_item("STRAT-09", "Supporting", "advisory_stratifier_not_applied",
                       dict(ev, tier="Supporting Candidate")))
    res = dec.apply_policy(_doc(items, manifest=manifest), policy)
    by = _decided(res)
    assert by["ElevationClass"]["decision_class"] == "strat09-advisory-not-applied"
    assert by["ElevationClass"]["action"] == "reject" and by["ElevationClass"]["asserts"] == {"tier": "Broad-Use Candidate"}
    assert "holds 31 sites (classes 31/39)" in by["ElevationClass"]["rationale"]
    assert "4 of 13 metrics tested" in by["ElevationClass"]["rationale"]
    assert "STRAT-07" in by["ElevationClass"]["rationale"]
    assert by["DrainageAreaClass"]["decision_class"] == "strat09-defer-floors"
    assert "holds 6 sites (classes 6/33/31)" in by["DrainageAreaClass"]["rationale"]
    # no min_populated_n on the record: the labelled string is read after each "=",
    # never the labels' own numbers (0.5, 2)
    assert by["ChannelSlopeClass"]["decision_class"] == "strat09-advisory-not-applied"
    assert "holds 18 sites (classes 18/19/29)" in by["ChannelSlopeClass"]["rationale"]
    assert [u["subject"] for u in res.uncovered] == ["Supporting"]


def test_level_counts_are_parsed_from_the_pairs():
    assert dec._parse_level_counts("Headwater (<= 10 km2)=72|Small (10 to 100 km2)=128|Large (> 100 km2)=66") == [72, 128, 66]
    assert dec._parse_level_counts("Low gradient (<= 0.5%)=80|Moderate (0.5 to 2%)=19|Steep (> 2%)=5") == [80, 19, 5]
    assert dec._parse_level_counts("12|13|8") == [12, 13, 8]
    assert dec._parse_level_counts({"a": 3, "b": 9}) == [3, 9]
    assert dec._parse_level_counts([4, "5"]) == [4, 5]
    assert dec._parse_level_counts(None) == []


# --------------------------------------------------------------------------- #
# the record round trip and the replay outcomes
# --------------------------------------------------------------------------- #
def _synthetic_version(tmp_path):
    """A provenance document with one item of each new class plus the records the
    replay compares against: one left open, one owner-written with the policy's action,
    one owner-written with another action, one under the policy's own class."""
    computed_c07 = {"curve_status": "degenerate", "reasons": ["Non-positive or non-finite Q25 produced a fallback curve."],
                    "domain_min": 0.0, "domain_max": 100.0, "domain_violations": 0, "reviewer_decision": "pending"}
    computed_c12 = {"auc_ref_vs_pressure": 0.157, "n_ref": 19, "n_pressure": 21, "median_ref_index": 0.75,
                    "median_pressure_index": 1.0, "auc_r_vs_im": None, "verdict": "inverted"}
    computed_c06 = {"evaluable": False, "structure_stability": 0.0, "shape_stability": 0.0, "n_boot": 200,
                    "seed": 1, "n_matched": 0}
    computed_red = {"spearman": -0.83, "pearson": -0.79, "same_function": False}
    records = [
        pv._record("r", "1", "CURVE-07", "metric", "phab_PCT_FAST", computed=computed_c07,
                   verdict=pv.VERDICT_REVIEW, review_required=True, review_triggers=["curve_needs_review"]),
        pv._record("r", "1", "CURVE-12", "metric", "phab_PCT_FAST",
                   computed=dict(computed_c12, verdict="weak", auc_ref_vs_pressure=0.578, n_ref=51, n_pressure=42)),
        pv._record("r", "1", "CURVE-12", "metric", "pctwet2019ws", computed=computed_c12,
                   verdict=pv.VERDICT_REVIEW, review_required=True, review_triggers=["inverted_discrimination"]),
        pv._record("r", "1", "CURVE-06", "metric", "phab_PCT_FAST", computed=computed_c06,
                   verdict=pv.VERDICT_REVIEW, review_required=True, review_triggers=["no_interval"]),
        pv._record("r", "1", "RED-01", "metric_pair", "chem_COND|phab_XCMGW", computed=computed_red,
                   verdict=pv.VERDICT_REVIEW, review_required=True, review_triggers=["redundant_pair"]),
    ]
    queue = pv.build_review_queue(records, {"inputsDigest": "x"})
    doc = {"records": records, "manifest": {}, "reviewQueue": queue}
    return doc


def test_new_entries_round_trip_through_apply_reviewer_decisions(tmp_path, policy):
    doc = _synthetic_version(tmp_path)
    # the 1.4 era: the fallback curve is accepted (a 1.5 build refuses it instead)
    res = dec.apply_policy(doc, policy, era="1.4")
    assert {d["decision_class"] for d in res.decisions} == {
        "curve07-fallback-accepted", "curve12-inverted-thin", "curve06-no-interval-flagged",
        "red01-keep-both-distinct-axes"}
    now = dec.apply_policy(_synthetic_version(tmp_path), policy)
    assert {d["decision_class"] for d in now.decisions} == {
        "curve07-degenerate-refused", "curve12-inverted-thin", "curve06-no-interval-flagged",
        "red01-keep-both-distinct-axes"}
    out = pv.apply_reviewer_decisions(doc, res.decisions, default_reviewer="owner")
    assert out["reviewQueue"]["counts"]["open"] == 0
    assert dec.is_pending(out)
    for rec in out["records"]:
        if rec.get("reviewer_action"):
            assert rec["reviewer_rationale_origin"] == "standing_policy:1.5"
            assert dec.PENDING_SUFFIX in rec["reviewer"]
            assert chr(0x2014) not in rec["reviewer_rationale"]
    # a tampered evidence value is refused by the same check a human decision faces
    for rec in doc["records"]:
        if rec["rule_id"] == "CURVE-12" and rec["subject"] == "pctwet2019ws":
            rec["computed"]["n_ref"] = 40
    with pytest.raises(ValueError, match="contradict"):
        pv.apply_reviewer_decisions(_synthetic_version(tmp_path) | {"records": doc["records"]}, res.decisions)


def test_replay_reports_policy_decides_open_and_owner_action_match(tmp_path, policy):
    doc = _synthetic_version(tmp_path)
    by_key = {(r["rule_id"], r["subject"]): r for r in doc["records"]}
    # the record: the fallback curve owner-written with the policy's action, the interval
    # owner-written with another action, the pair under the policy's own class, the
    # inversion left open
    by_key[("CURVE-07", "phab_PCT_FAST")].update(
        reviewer="owner", reviewer_action="accept_with_conditions", reviewer_rationale_origin="owner_written",
        reviewer_rationale="Owner decision: publishes as preliminary, marked for verification.")
    by_key[("CURVE-06", "phab_PCT_FAST")].update(
        reviewer="owner", reviewer_action="reject", reviewer_rationale_origin="owner_written",
        reviewer_rationale="Owner decision: dropped.")
    by_key[("RED-01", "chem_COND|phab_XCMGW")].update(
        reviewer="owner", reviewer_action="accept", reviewer_decision_class="red01-keep-both-distinct-axes",
        reviewer_rationale_origin="ai_drafted_owner_approved", reviewer_rationale="Keep both.")
    vdir = tmp_path / "v1"
    vdir.mkdir()
    (vdir / "provenance.json").write_text(json.dumps(doc), encoding="utf-8")
    (vdir / "assessment.deep.json").write_text(json.dumps({"metricsByFunction": []}), encoding="utf-8")
    rep = dec.replay(vdir, policy)
    outcomes = {r["item_id"]: r["outcome"] for r in rep.rows}
    assert outcomes == {"CURVE-07:phab_PCT_FAST": dec.OWNER_ACTION_MATCH,
                        "CURVE-12:pctwet2019ws": dec.POLICY_DECIDES_OPEN,
                        "CURVE-06:phab_PCT_FAST": dec.MISMATCH,
                        "RED-01:chem_COND|phab_XCMGW": dec.MATCH}
    assert rep.counts() == {"owner_action_match": 1, "policy_decides_open": 1, "mismatch": 1, "match": 1}
    assert [r["item_id"] for r in rep.mismatches()] == ["CURVE-06:phab_PCT_FAST"]
    assert rep.policy_decides_open() == {"CURVE-12:pctwet2019ws": "curve12-inverted-thin"}
    row = next(r for r in rep.rows if r["item_id"] == "CURVE-07:phab_PCT_FAST")
    assert row["published_origin"] == "owner_written" and row["published_class"] is None
    assert row["policy_class"] == "curve07-fallback-accepted"
    # an owner-written record with the policy's action but under a recorded class that is
    # neither the entry's nor an alias stays a mismatch
    by_key[("CURVE-07", "phab_PCT_FAST")]["reviewer_decision_class"] = "some-other-class"
    (vdir / "provenance.json").write_text(json.dumps(doc), encoding="utf-8")
    rep = dec.replay(vdir, policy)
    assert {r["item_id"] for r in rep.mismatches()} == {"CURVE-06:phab_PCT_FAST", "CURVE-07:phab_PCT_FAST"}
    # the CLI exits 1 on a mismatch and prints the new outcome names
    assert dec.main(["replay", "--version-dir", str(vdir)]) == 1

"""Standing decisions (methodology 0.7): the policy file, its matching, the
per-item expansion with the pending reviewer marker, the owner confirmation,
and the asserts round trip through the provenance consistency check."""
from __future__ import annotations

import json

import pytest

from streamcurves import decisions as dec
from streamcurves import methodology
from streamcurves import provenance as pv

DEFAULT_IDS = {"curve04-accept-with-flag", "data05-exploratory-pool-accepted",
               "red06-instability-is-noise", "strat09-defer-floors",
               "select01-complementary-set"}
OPTIONAL_IDS = {"ref02-accept-best-available", "data03-thin-metric-finalized",
                "data06-insufficient-finalized", "curve07-thin-metric-finalized"}


@pytest.fixture(scope="module")
def policy():
    return dec.load_policy()


def test_policy_file_loads_and_validates(policy):
    assert dec.validate_policy(policy) == []
    assert policy["meta"]["sha256"].startswith("sha256:")
    assert dec.policy_version(policy) == "1.0"
    assert policy["meta"]["methodology_version"] == methodology.methodology_version()


def test_default_enabled_set_is_the_five_routine_classes(policy):
    enabled = {e["id"] for e in dec.enabled_entries(policy)}
    assert enabled == DEFAULT_IDS
    everything = {e["id"] for e in dec.enabled_entries(policy, sorted(OPTIONAL_IDS))}
    assert everything == DEFAULT_IDS | OPTIONAL_IDS
    with pytest.raises(ValueError, match="unknown entries"):
        dec.enabled_entries(policy, ["no-such-entry"])


def _queue_item(rule_id, subject, trigger, evidence, blocking=False):
    return {"item_id": f"{rule_id}:{subject}", "rule_ids": [rule_id], "subject": subject,
            "trigger": trigger, "evidence": evidence, "status": "open", "blocking": blocking,
            "question": "?", "allowed_actions": list(dec.ALLOWED_ACTIONS)}


def _doc(items, records=None, manifest=None):
    return {"records": records or [], "manifest": manifest or {},
            "reviewQueue": {"items": items, "counts": {"open": len(items)}}}


def test_curve04_entry_matches_only_without_decision_flip(policy):
    ok = _queue_item("CURVE-04", "m1", "influential_site",
                     {"max_param_change_frac": 0.3, "max_param_change_iqr": 0.4,
                      "decision_flip": False, "driver": "S1"})
    flip = _queue_item("CURVE-04", "m2", "influential_site",
                       {"max_param_change_frac": 0.3, "max_param_change_iqr": 0.4,
                        "decision_flip": True, "driver": "S2"})
    res = dec.apply_policy(_doc([ok, flip]), policy)
    assert [d["subject"] for d in res.decisions] == ["m1"]
    d = res.decisions[0]
    assert d["decision_class"] == "curve04-accept-with-flag"
    assert d["rationale_origin"] == "standing_policy:1.0"
    assert d["reviewer"].startswith("standing-policy:curve04-accept-with-flag")
    assert dec.PENDING_SUFFIX in d["reviewer"]
    assert d["asserts"] == {"decision_flip": False, "driver": "S1"}
    assert "no decision flip" in d["rationale"] and "S1" in d["rationale"]
    assert [u["item_id"] for u in res.uncovered] == ["CURVE-04:m2"]
    assert res.hard_stops == []  # an influence flag is an open item, not a hard stop


def test_strat09_entry_needs_a_stratum_below_the_floor(policy):
    floor = int(methodology.threshold("data_rules.min_n_stratum"))
    manifest = {"stratifiers": {"candidates": [
        {"stratification": "Small", "level_counts": f"{floor - 2}|{floor + 1}|{floor + 3}"},
        {"stratification": "Big", "level_counts": f"{floor}|{floor + 5}|{floor + 9}"}]}}
    ev = {"n_metrics_tested": 10, "n_significant": 6, "consistency_score": 0.6,
          "tier": "Broad-Use Candidate"}
    items = [_queue_item("STRAT-09", "Small", "advisory_stratifier_not_applied", dict(ev)),
             _queue_item("STRAT-09", "Big", "advisory_stratifier_not_applied", dict(ev))]
    res = dec.apply_policy(_doc(items, manifest=manifest), policy)
    assert [d["subject"] for d in res.decisions] == ["Small"]
    assert res.decisions[0]["action"] == "reject"
    assert f"{floor - 2}" in res.decisions[0]["rationale"]
    assert [u["subject"] for u in res.uncovered] == ["Big"]


def test_select01_entry_requires_default_set_and_no_strong_pair(policy):
    strong = float(methodology.threshold("redundancy_rules.strong_abs_spearman"))
    bundle = {"metricsByFunction": [
        {"functionId": "water-soil-quality",
         "metrics": [{"metricId": "spring-chem-cond"}, {"metricId": "spring-chem-ph"},
                     {"metricId": "spring-chem-turb"}]},
        {"functionId": "community-dynamics",
         "metrics": [{"metricId": "spring-bent-ept-ntax"}, {"metricId": "spring-bent-hprime"},
                     {"metricId": "spring-bent-tolrpind"}]}]}
    records = [pv._record("r", "58", "CURVE-01", "metric", m) for m in
               ("chem_COND", "chem_PH", "chem_TURB", "bent_EPT_NTAX", "bent_HPRIME", "bent_TOLRPIND")]
    records.append(pv._record("r", "58", "RED-01", "metric_pair", "chem_COND|chem_PH",
                              computed={"spearman": strong + 0.05, "same_function": True}))
    items = [_queue_item("SELECT-01", "water-soil-quality", "more_than_two_metrics", {"n_metrics": 3}),
             _queue_item("SELECT-01", "community-dynamics", "more_than_two_metrics", {"n_metrics": 3})]
    res = dec.apply_policy(_doc(items, records=records), policy, bundle=bundle)
    assert [d["subject"] for d in res.decisions] == ["community-dynamics"]
    assert res.portfolio_approvals and res.portfolio_approvals[0]["functionId"] == "community-dynamics"
    assert dec.PENDING_SUFFIX in res.portfolio_approvals[0]["approvedBy"]
    assert [u["subject"] for u in res.uncovered] == ["water-soil-quality"]


def test_disabled_entries_are_reported_as_hard_stops(policy):
    ref = _queue_item("REF-02", "reference_screen", "reference_tier_fallback",
                      {"reference_tier": "best_available", "review_flags": []}, blocking=True)
    rec = pv._record("r", "55", "REF-01", "run", "reference_screen",
                     computed={"reference_tier": "best_available", "n_retained": 16})
    res = dec.apply_policy(_doc([ref], records=[rec]), policy)
    assert res.decisions == []
    assert [h["item_id"] for h in res.hard_stops] == ["REF-02:reference_screen"]
    enabled = dec.apply_policy(_doc([ref], records=[rec]), policy,
                               enabled=["ref02-accept-best-available"])
    assert [d["decision_class"] for d in enabled.decisions] == ["ref02-accept-best-available"]
    assert enabled.decisions[0]["asserts"] == {"reference_tier": "best_available"}


def test_curve07_thin_metric_finalization_is_a_side_effect_only_for_data_review(policy):
    thin = _queue_item("CURVE-07", "phab_SINU", "curve_needs_review",
                       {"curve_status": "data_review", "reasons": ["x"], "domain_violations": 0})
    degenerate = _queue_item("CURVE-07", "phab_PCT_FAST", "curve_needs_review",
                             {"curve_status": "degenerate", "reasons": ["y"], "domain_violations": 0})
    res = dec.apply_policy(_doc([thin, degenerate]), policy, enabled=["curve07-thin-metric-finalized"])
    assert list(res.finalize_metrics) == ["phab_SINU"]
    assert [h["subject"] for h in res.hard_stops] == ["phab_PCT_FAST"]


def test_asserts_round_trip_through_apply_reviewer_decisions(policy):
    rec = pv._record("run", "58", "CURVE-04", "metric", "m1",
                     computed={"max_param_change_frac": 0.25, "max_param_change_iqr": 0.3,
                               "decision_flip": False, "driver": "S9"},
                     verdict=pv.VERDICT_REVIEW, review_required=True,
                     review_triggers=["influential_site"])
    doc = {"records": [rec], "manifest": {},
           "reviewQueue": pv.build_review_queue([rec], {"inputsDigest": "x"})}
    res = dec.apply_policy(doc, policy)
    out = pv.apply_reviewer_decisions(doc, res.decisions, default_reviewer="owner")
    assert out["records"][0]["reviewer_decision_class"] == "curve04-accept-with-flag"
    assert out["records"][0]["reviewer_rationale_origin"] == "standing_policy:1.0"
    assert dec.PENDING_SUFFIX in out["records"][0]["reviewer"]
    assert out["reviewQueue"]["counts"]["open"] == 0
    assert dec.is_pending(out)
    # A tampered evidence value is refused by the same check a human decision faces.
    rec["computed"]["decision_flip"] = True
    with pytest.raises(ValueError, match="contradict"):
        pv.apply_reviewer_decisions(doc, res.decisions)


def test_confirm_decisions_replaces_the_pending_reviewer_and_keeps_the_origin(policy):
    item = _queue_item("DATA-05", "m", "n_exploratory", {"disposition": "exploratory"})
    rec = pv._record("r", "58", "DATA-05", "metric", "m", inputs={"n_reference": 12},
                     computed={"disposition": "exploratory"})
    res = dec.apply_policy(_doc([item], records=[rec]), policy)
    assert res.decisions and "12" in res.decisions[0]["rationale"]
    confirmed = dec.confirm_decisions(res.decisions, reviewer="gtmenichino", date="2026-08-22")
    assert confirmed[0]["reviewer"] == "gtmenichino"
    assert confirmed[0]["confirmed_by"] == "gtmenichino"
    assert confirmed[0]["rationale_origin"] == "standing_policy:1.0"
    assert not dec.is_pending(confirmed)
    with pytest.raises(ValueError):
        dec.confirm_decisions(res.decisions, reviewer="", date="2026-08-22")


def test_uncovered_items_are_listed_never_dropped(policy):
    items = [_queue_item("RED-01", "a|b", "redundant_pair",
                         {"spearman": 0.9, "pearson": 0.8, "same_function": True}),
             _queue_item("CURVE-05", "m", "direction_unresolved", {"reason": "no curated direction"})]
    res = dec.apply_policy(_doc(items), policy)
    assert res.decisions == []
    assert [u["item_id"] for u in res.uncovered] == ["RED-01:a|b", "CURVE-05:m"]
    assert [h["item_id"] for h in res.hard_stops] == ["CURVE-05:m"]


def test_expand_cli_exits_2_on_uncovered_items(tmp_path):
    queue = {"items": [_queue_item("RED-01", "a|b", "redundant_pair",
                                   {"spearman": 0.9, "pearson": 0.8, "same_function": True}),
                       _queue_item("DATA-05", "m", "n_exploratory", {"disposition": "exploratory"})]}
    prov = {"records": [pv._record("r", "58", "DATA-05", "metric", "m",
                                   inputs={"n_reference": 11}, computed={"disposition": "exploratory"})],
            "manifest": {}}
    qp, pp, out = tmp_path / "q.json", tmp_path / "p.json", tmp_path / "d.json"
    qp.write_text(json.dumps(queue), encoding="utf-8")
    pp.write_text(json.dumps(prov), encoding="utf-8")
    rc = dec.main(["expand", "--queue", str(qp), "--provenance", str(pp), "--out", str(out)])
    assert rc == 2
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [d["subject"] for d in written] == ["m"]


def test_validate_reports_a_bad_entry(policy):
    broken = json.loads(json.dumps(policy))
    broken["entries"].append({"id": "curve04-accept-with-flag", "rule_id": "NOPE-1",
                              "action": "yolo", "match": {}, "rationale": "{missing"})
    problems = dec.validate_policy(broken)
    assert any("duplicate id" in p for p in problems)
    assert any("not in the catalog" in p for p in problems)
    assert any("not allowed" in p for p in problems)
    assert any("match conditions" in p for p in problems)

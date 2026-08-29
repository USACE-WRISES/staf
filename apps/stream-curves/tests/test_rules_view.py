"""The Rules page's data layer: the catalog + policy + config join.

The three-way drift tests are the point: the catalog cross-links, the policy's
enabled flags, and the config's human-readable standing-decisions index have no
shared source, so this module is where a divergence first becomes visible."""
from __future__ import annotations

import re

from streamcurves import decisions as dec
from streamcurves import methodology
from streamcurves import rules_view as rv
from streamcurves import run_state as rs


def test_every_catalog_rule_appears_exactly_once_with_a_labeled_family():
    entries = rv.rule_entries()
    ids = [e["id"] for e in entries]
    assert sorted(ids) == methodology.rule_ids()
    assert len(ids) == len(set(ids))
    by_family = rv.rules_by_family(entries)
    assert sum(len(v) for v in by_family.values()) == len(entries)
    for fam in by_family:
        assert fam in rv.FAMILY_LABELS, f"family {fam} has no label"


def test_every_threshold_path_resolves_in_the_live_config():
    for rid, paths in rv.RULE_THRESHOLD_PATHS.items():
        assert rid in methodology.rule_ids(), f"{rid} is not a catalog rule"
        for path in paths:
            value = methodology.threshold(path, "__missing__")
            assert value != "__missing__", f"{rid}: {path} does not resolve"


def test_the_standing_decision_join_hits_exactly_the_cross_linked_rules():
    policy = dec.load_policy()
    entries = {e["id"]: e for e in rv.rule_entries(policy)}
    joined = {rid for rid, e in entries.items() if e["standing"] or e["optional"]}
    linked = set()
    for rule in methodology.load_rule_catalog()["rules"]:
        if rule.get("standing_decision_ids") or rule.get("standing_decision_ids_optional"):
            linked.add(rule["id"])
    assert joined == linked
    # every policy entry is reachable from some rule card
    reachable = {p["id"] for e in entries.values() for p in e["standing"] + e["optional"]}
    assert reachable == set(dec.entries_by_id(policy))


def test_the_three_sources_agree_on_which_entries_are_opt_in():
    """standing_decisions.yaml enabled flags, the catalog's optional cross-links,
    and methodology_config's standing_decisions index must name the same sets."""
    policy = dec.load_policy()
    opt_in = set(rv.optional_policy_ids(policy))
    default_on = set(rv.default_policy_ids(policy))
    index = methodology.load_config().get("standing_decisions") or {}
    assert opt_in == set(index.get("enable_per_run_only") or []), \
        "policy enabled flags disagree with the config index"
    assert default_on == set(index.get("default_enabled") or [])
    catalog_optional = set()
    for rule in methodology.load_rule_catalog()["rules"]:
        catalog_optional |= set(rule.get("standing_decision_ids_optional") or [])
    assert opt_in == catalog_optional, \
        "catalog optional cross-links disagree with the policy enabled flags"


def test_split_rule_ids_finds_ids_inside_reviewer_facing_copy():
    for reason_key in ("shape_conflict", "data_review"):
        text = rs.REVIEW_REASONS[reason_key]
        found = [v for kind, v in rv.split_rule_ids(text) if kind == "rule"]
        assert found, f"REVIEW_REASONS[{reason_key}] names no rule id"
        assert "".join(v for _, v in rv.split_rule_ids(text)) == text
    assert [v for k, v in rv.split_rule_ids("CURVE-07a gates; STRAT-00 screens")
            if k == "rule"] == ["CURVE-07a", "STRAT-00"]
    assert rv.split_rule_ids("no ids here") == [("text", "no ids here")]
    assert rv.split_rule_ids("") == []


def test_rule_dom_ids_are_unique_and_css_safe():
    ids = [rv.rule_dom_id(rid) for rid in methodology.rule_ids()]
    assert len(ids) == len(set(ids))
    for dom in ids:
        assert re.fullmatch(r"rule-[a-z0-9-]+", dom), dom


def test_validate_selections_keeps_opt_ins_and_drops_everything_else():
    policy = dec.load_policy()
    opt_in = rv.optional_policy_ids(policy)
    default_on = rv.default_policy_ids(policy)
    kept, dropped = rv.validate_selections(
        [opt_in[0], "not-a-policy", default_on[0], opt_in[0]], policy)
    assert kept == [opt_in[0]], "order-preserving, de-duplicated, opt-ins only"
    assert dropped == ["not-a-policy", default_on[0]]
    assert rv.validate_selections(None, policy) == ([], [])


def test_describe_match_resolves_methodology_references():
    policy = dec.load_policy()
    ref02 = dec.entries_by_id(policy)["ref02-accept-best-available"]
    lines = rv.describe_match(ref02)
    assert any("reference_tier is best_available" in ln for ln in lines)
    floor = methodology.threshold("data_rules.exploratory_n_unstratified")
    assert any(f"n_retained at least {floor}" in ln
               and "data_rules.exploratory_n_unstratified" in ln for ln in lines)


def test_resolved_values_ride_on_the_entries():
    entries = {e["id"]: e for e in rv.rule_entries()}
    d4 = entries["DATA-04"]
    assert d4["resolved"] == [{"path": "data_rules.min_n_unstratified",
                               "value": methodology.threshold("data_rules.min_n_unstratified")}]
    assert entries["DATA-09"]["resolved"] == [], "a rule with no numeric config shows prose only"
    assert entries["CURVE-07a"]["threshold_status"] == "approved"


# --------------------------------------------------------------------------- #
# The table view helpers
# --------------------------------------------------------------------------- #
def test_family_dom_ids_are_unique_css_safe_and_disjoint_from_rule_anchors():
    """The [id^="rule-"] scroll-margin CSS must catch only rule rows."""
    fam_ids = [rv.family_dom_id(f) for f in rv.FAMILY_ORDER]
    assert len(fam_ids) == len(set(fam_ids))
    for dom in fam_ids:
        assert re.fullmatch(r"rules-fam-[a-z0-9-]+", dom), dom
        assert not dom.startswith("rule-")
    rule_ids = {rv.rule_dom_id(r) for r in methodology.rule_ids()}
    assert not rule_ids & set(fam_ids)


def test_threshold_cell_pairs_prose_with_resolved_values():
    entries = {e["id"]: e for e in rv.rule_entries()}
    text, tip = rv.threshold_cell(entries["DATA-04"])
    assert text == entries["DATA-04"]["threshold"]
    assert tip == ("data_rules.min_n_unstratified = "
                   f"{methodology.threshold('data_rules.min_n_unstratified')}")
    text, tip = rv.threshold_cell(entries["DATA-09"])
    assert text and tip == "", "no numeric config means no tooltip"


def test_the_adjustable_predicate_matches_the_opt_in_join():
    entries = rv.rule_entries()
    adjustable = {e["id"] for e in entries if rv.is_adjustable(e)}
    assert adjustable == {"REF-02", "DATA-03", "DATA-06", "CURVE-07"}
    assert len(rv.filter_entries(entries, adjustable_only=True)) == 4


def test_filter_entries_matches_id_name_and_family_case_insensitively():
    entries = rv.rule_entries()
    assert [e["id"] for e in rv.filter_entries(entries, query="data-04")] == ["DATA-04"]
    assert rv.filter_entries(entries, query="") == entries, "empty query is identity"
    missing = {e["id"] for e in rv.filter_entries(entries, query="MISSINGNESS")}
    assert {"DATA-01", "DATA-02", "DATA-03"} <= missing
    strat = {e["id"] for e in rv.filter_entries(entries, query="stratification")}
    assert "STRAT-00" in strat
    both = rv.filter_entries(entries, query="missingness high")
    assert [e["id"] for e in both] == ["DATA-03"], "terms AND together"
    assert rv.filter_entries(entries, adjustable_only=True, query="missingness")
    assert not rv.filter_entries(entries, adjustable_only=True, query="vif")


def test_status_exceptions_flags_only_departures_from_the_baseline():
    entries = {e["id"]: e for e in rv.rule_entries()}
    assert rv.status_exceptions(entries["DATA-01"]) == []
    assert rv.status_exceptions(entries["STRAT-07"]) == [
        ("implementation", "not_yet_implemented")]
    assert rv.status_exceptions(entries["CURVE-07"]) == [("implementation", "partial")]
    assert rv.status_exceptions(entries["CURVE-07a"]) == [("threshold", "approved")]
    baseline = [e for e in entries.values() if not rv.status_exceptions(e)]
    assert len(baseline) >= 30, "the baseline is the overwhelming majority"

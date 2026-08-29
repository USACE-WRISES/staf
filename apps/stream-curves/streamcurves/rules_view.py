"""The one read-only join the Rules page renders: catalog + policy + live values.

Three files govern a build and none of them knows the others' display concerns:
``rule_catalog.json`` (the 42 rules with their dual status tags),
``standing_decisions.yaml`` (the 9 owner class decisions, 5 standing and 4
per-run opt-ins), and ``methodology_config.yaml`` (the numeric thresholds the
rules cite). ``rule_entries`` joins them through the same accessors the
pipeline itself uses (``methodology.threshold``, ``decisions.load_policy``), so
what the page shows is by construction what a run applies.

Pure module: no Shiny. The page (views/rules.py) renders these structures; the
chips (views/uihelpers.rule_chip / linkify_rule_ids) use the id vocabulary.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from . import decisions as dec
from . import methodology

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
FAMILY_ORDER = ["DATA", "RED", "STRAT", "CURVE", "REF", "CONF", "SELECT"]

FAMILY_LABELS = {
    "DATA": "Data adequacy",
    "RED": "Redundancy",
    "STRAT": "Stratification",
    "CURVE": "Curve fitting",
    "REF": "Reference screening",
    "CONF": "Confidence",
    "SELECT": "Metric selection",
}

#: Rule id -> the dotted config paths its numeric thresholds live at. Resolved
#: live through methodology.threshold, so a config edit shows up here without
#: touching the catalog (after the process restart every config edit needs).
#: Rules absent from this map have no numeric config (their catalog prose is
#: the whole story). test_rules_view pins that every path resolves.
RULE_THRESHOLD_PATHS: dict[str, list[str]] = {
    "DATA-01": ["data_rules.max_missingness_auto"],
    "DATA-02": ["data_rules.max_missingness_auto", "data_rules.max_missingness_review"],
    "DATA-03": ["data_rules.max_missingness_review"],
    "DATA-04": ["data_rules.min_n_unstratified"],
    "DATA-05": ["data_rules.exploratory_n_unstratified", "data_rules.min_n_unstratified"],
    "DATA-06": ["data_rules.insufficient_n_unstratified", "data_rules.curve_engine_hard_floor_n"],
    "DATA-07": ["data_rules.min_n_stratum"],
    "DATA-08": ["data_rules.very_small_stratum_n", "data_rules.very_small_stratum_frac"],
    "RED-01": ["redundancy_rules.strong_abs_spearman"],
    "RED-02": ["redundancy_rules.moderate_abs_spearman", "redundancy_rules.strong_abs_spearman"],
    "RED-03": ["redundancy_rules.moderate_abs_spearman"],
    "RED-04": ["redundancy_rules.vif_review"],
    "RED-05": ["redundancy_rules.vif_fail"],
    "RED-06": ["redundancy_rules.bootstrap_stability"],
    "RED-07": ["redundancy_rules.fdr_q"],
    "STRAT-01": ["stratifier_rules.min_cv_error_improvement"],
    "STRAT-02": ["stratifier_rules.strong_cv_error_improvement"],
    "STRAT-03": ["stratifier_rules.min_delta_cv_r2"],
    "STRAT-04": ["stratifier_rules.min_delta_aicc"],
    "STRAT-05": ["stratifier_rules.strong_delta_aicc"],
    "STRAT-06": ["stratifier_rules.min_resample_support"],
    "STRAT-07": ["stratifier_rules.breakpoint_interval_support"],
    "STRAT-08": ["stratifier_rules.max_data_derived_bins"],
    "STRAT-09": ["stratifier_rules.screening_significance_alpha"],
    "CURVE-01": ["curve_rules.approved_families"],
    "CURVE-04": ["curve_rules.influence_param_change_frac"],
    "CURVE-05": ["curve_rules.require_direction_check"],
    "CURVE-06": ["curve_rules.require_uncertainty_interval"],
    "CURVE-07a": ["curve_rules.index_low_band", "curve_rules.index_high_band",
                  "curve_rules.max_band_crossings"],
    "CURVE-08": ["curve_rules.require_reproducible_seed"],
    "CURVE-09": ["curve_rules.measurement_precision_core_multiple",
                 "curve_rules.measurement_precision_floors"],
    "REF-01": ["reference_tiers.primary_tier"],
    "REF-02": ["reference_tiers.fallback_tier",
               "confidence_rules.caps.best_available_reference"],
    "REF-03": ["reference_tiers.floor_tier"],
    "CONF-01": ["confidence_rules.components"],
    "CONF-02": ["confidence_rules.caps", "confidence_rules.deductions"],
    "SELECT-01": ["metric_portfolio.default_maximum_metrics_per_function"],
    "SELECT-02": ["metric_portfolio.metric_score_weights"],
}

#: Matches every catalog id, including CURVE-07a and STRAT-00, and nothing that
#: only looks like one (no trailing word characters).
RULE_ID_RE = re.compile(r"\b(?:DATA|RED|STRAT|CURVE|REF|CONF|SELECT)-\d{2}[a-z]?\b")

#: The page baseline: 36 of 42 rules carry exactly this pair, so a row shows a
#: status mark only when a rule DEPARTS from it (status_exceptions).
BASELINE_THRESHOLD_STATUS = "provisional"
BASELINE_IMPLEMENTATION_STATUS = "implemented"

_MATCH_OPS = {"eq": "is", "ne": "is not", "lt": "below", "lte": "at most",
              "gt": "above", "gte": "at least", "in": "one of"}


# --------------------------------------------------------------------------- #
# The join
# --------------------------------------------------------------------------- #
def _resolved(path: str):
    value = methodology.threshold(path)
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(", ", ": "), ensure_ascii=False)
    return value


def _entry_for_rule(rule: dict, by_id: dict[str, dict]) -> dict:
    rid = str(rule.get("id"))
    linked = [by_id[i] for i in (rule.get("standing_decision_ids") or []) if i in by_id]
    optional = [by_id[i] for i in (rule.get("standing_decision_ids_optional") or [])
                if i in by_id]
    return {
        "id": rid,
        "family": str(rule.get("family") or rid.split("-")[0]),
        "name": rule.get("name") or rid,
        "purpose": rule.get("purpose") or "",
        "test": rule.get("test") or "",
        "threshold": rule.get("threshold") or "",
        "threshold_status": rule.get("threshold_status") or "provisional",
        "implementation_status": rule.get("implementation_status") or "implemented",
        "override_permission": rule.get("override_permission") or "none",
        "maps_to": rule.get("maps_to") or "",
        "note": rule.get("note") or "",
        "verification": rule.get("verification"),
        "resolved": [{"path": p, "value": _resolved(p)}
                     for p in RULE_THRESHOLD_PATHS.get(rid, [])],
        "standing": linked,      # policy entries applied on every build
        "optional": optional,    # policy entries the owner opts into per run
    }


def rule_entries(policy: Optional[dict] = None) -> list[dict]:
    """Every catalog rule joined with its policy entries and resolved values."""
    policy = policy or dec.load_policy()
    by_id = dec.entries_by_id(policy)
    return [_entry_for_rule(r, by_id)
            for r in methodology.load_rule_catalog().get("rules") or []
            if r.get("id")]


def _rule_sort_key(rid: str) -> tuple:
    m = re.match(r"^[A-Z]+-(\d+)([a-z]?)$", rid)
    return (int(m.group(1)), m.group(2)) if m else (99, rid)


def rules_by_family(entries: list[dict]) -> dict[str, list[dict]]:
    """FAMILY_ORDER-ordered, rules numeric within a family. An unknown family
    (a future catalog addition) lands at the end rather than vanishing."""
    out: dict[str, list[dict]] = {}
    families = FAMILY_ORDER + sorted({e["family"] for e in entries} - set(FAMILY_ORDER))
    for fam in families:
        rows = sorted((e for e in entries if e["family"] == fam),
                      key=lambda e: _rule_sort_key(e["id"]))
        if rows:
            out[fam] = rows
    return out


def status_labels() -> dict:
    """The catalog's own status legend, so the page's badge tooltips cannot
    drift from what the statuses mean."""
    return (methodology.load_rule_catalog().get("meta") or {}).get("status_legend") or {}


# --------------------------------------------------------------------------- #
# The opt-in selection
# --------------------------------------------------------------------------- #
def optional_policy_ids(policy: Optional[dict] = None) -> list[str]:
    policy = policy or dec.load_policy()
    return [str(e["id"]) for e in policy.get("entries") or []
            if e.get("id") and not e.get("enabled", False)]


def default_policy_ids(policy: Optional[dict] = None) -> list[str]:
    policy = policy or dec.load_policy()
    return [str(e["id"]) for e in policy.get("entries") or []
            if e.get("id") and e.get("enabled", False)]


def validate_selections(ids, policy: Optional[dict] = None) -> tuple[list[str], list[str]]:
    """``(kept, dropped)``: the opt-in entry ids from ``ids``, order-preserving
    and de-duplicated; everything else (unknown ids, default-on ids) dropped.
    What the restore path and the Region builder both run a selection through."""
    allowed = set(optional_policy_ids(policy))
    kept: list[str] = []
    dropped: list[str] = []
    for raw in ids or []:
        pid = str(raw)
        if pid in allowed:
            if pid not in kept:
                kept.append(pid)
        else:
            dropped.append(pid)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Rendering vocabulary shared with the chips
# --------------------------------------------------------------------------- #
def rule_dom_id(rule_id: str) -> str:
    """Stable DOM id for a rule card ("CURVE-07a" -> "rule-curve-07a")."""
    return "rule-" + re.sub(r"[^a-z0-9]+", "-", str(rule_id).lower()).strip("-")


def split_rule_ids(text: str) -> list[tuple[str, str]]:
    """``[("text", ...), ("rule", "DATA-05"), ...]`` segments of a sentence, so
    a render site can substitute chips for ids without touching the string
    itself (which may also land in provenance, where it must stay plain)."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in RULE_ID_RE.finditer(text or ""):
        if m.start() > pos:
            out.append(("text", text[pos:m.start()]))
        out.append(("rule", m.group(0)))
        pos = m.end()
    if pos < len(text or ""):
        out.append(("text", text[pos:]))
    return out


def describe_match(entry: dict) -> list[str]:
    """A policy entry's match conditions as plain sentences, ``$methodology:``
    references resolved to their live values (path shown so the reader can find
    the number's home)."""
    out: list[str] = []
    for field, cond in (entry.get("match") or {}).items():
        for op, raw in (cond or {}).items():
            word = _MATCH_OPS.get(op, op)
            if isinstance(raw, str) and raw.startswith("$methodology:"):
                path = raw[len("$methodology:"):]
                out.append(f"{field} {word} {_resolved(path)} ({path})")
            else:
                out.append(f"{field} {word} {raw}")
    return out


# --------------------------------------------------------------------------- #
# The table view: anchors, cells, marks, filters
# --------------------------------------------------------------------------- #
def family_dom_id(family: str) -> str:
    """Stable DOM anchor for a family heading row ("DATA" -> "rules-fam-data").

    The prefix deliberately does not start with ``rule-``: the page's
    ``[id^="rule-"]`` scroll-margin CSS must catch only the rule rows, and the
    family rows carry their own margin rule."""
    return "rules-fam-" + re.sub(r"[^a-z0-9]+", "-", str(family).lower()).strip("-")


def is_adjustable(entry: dict) -> bool:
    """True when the rule carries a per-run opt-in standing decision, which is
    the only thing about a rule this app can change."""
    return bool(entry.get("optional"))


def threshold_cell(entry: dict) -> tuple[str, str]:
    """``(text, tooltip)`` for the Threshold column: the catalog's compact
    prose as the cell, the resolved "path = value" pairs as the tooltip. A rule
    with no numeric config gets an empty tooltip; a rule with pairs but no
    prose (none today) shows the pairs as the text instead."""
    pairs = "; ".join(f"{r['path']} = {r['value']}"
                      for r in entry.get("resolved") or [])
    text = str(entry.get("threshold") or "")
    return (text or pairs, pairs)


def status_exceptions(entry: dict) -> list[tuple[str, str]]:
    """The statuses departing from the page baseline, as
    ``("threshold"|"implementation", status)`` pairs. Nearly every rule returns
    ``[]``, which is the point: only exceptions earn a mark in the table."""
    out: list[tuple[str, str]] = []
    if entry.get("threshold_status") != BASELINE_THRESHOLD_STATUS:
        out.append(("threshold", str(entry.get("threshold_status"))))
    if entry.get("implementation_status") != BASELINE_IMPLEMENTATION_STATUS:
        out.append(("implementation", str(entry.get("implementation_status"))))
    return out


def matches(entry: dict, query: str) -> bool:
    """Case-insensitive AND over whitespace-separated terms, each a substring
    of the rule's id, name, family label, purpose, threshold prose, or code
    mapping. Empty query matches everything."""
    terms = [t for t in str(query or "").casefold().split() if t]
    if not terms:
        return True
    haystack = " ".join(str(entry.get(k) or "") for k in
                        ("id", "name", "purpose", "threshold", "maps_to"))
    haystack = (haystack + " "
                + FAMILY_LABELS.get(str(entry.get("family")), "")).casefold()
    return all(term in haystack for term in terms)


def filter_entries(entries: list[dict], *, adjustable_only: bool = False,
                   query: str = "") -> list[dict]:
    """The table's visible set: both filters applied, order preserved."""
    out = []
    for entry in entries or []:
        if adjustable_only and not is_adjustable(entry):
            continue
        if not matches(entry, query):
            continue
        out.append(entry)
    return out

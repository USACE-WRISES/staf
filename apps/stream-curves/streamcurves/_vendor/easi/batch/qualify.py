"""Reference-condition qualification: a recursive AND/OR criteria model.

A criteria rule is a JSON tree of AND/OR groups over predicates. Predicates read
a ``SiteResult`` (raw ECI, raw sub-indices, function scores/bands, per-metric
rating/confidence/availability/source-mode, and completeness). Evaluation is
tri-state: True / False / None (the predicate's evidence is unavailable, so it is
skipped, never counted as a failure). An empty rule is ``not_evaluable``.

Four presets ship. The three ECI-threshold presets cut at the ``config.INDEX_BANDS``
boundaries, so each one selects exactly a ``config.INDEX_BAND_LABELS`` condition
category (see ``scoring.index_band_label``):
  functional            raw ECI > 0.69   (Functioning)
  at_risk_or_better     raw ECI > 0.39   (Functioning or Functioning-at-Risk)
  all_sites             raw ECI >= 0.0   (any site that scored)
  reference_condition   raw ECI > 0.69 AND every available sub-index > 0.69
                        AND every available function score > 10
"""
from __future__ import annotations

from typing import Optional

from .contracts import SiteResult

# --- comparison operators --------------------------------------------------- #
def _cmp(a, op: str, b) -> bool:
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "in":
        return a in b
    if op == "not_in":
        return a not in b
    raise ValueError(f"unknown comparison operator {op!r}")


# Fields available to the criteria builder (for a UI / capabilities()).
CRITERIA_FIELDS = {
    "eci": "Raw Ecosystem Condition Index (0-1)",
    "sub_index": "Raw sub-index for a named outcome (key = physical|chemical|biological)",
    "every_sub_index": "Every available sub-index (quantifier)",
    "function_score": "Function score 0-15 for a named functionId (key)",
    "every_function_score": "Every available function score (quantifier)",
    "metric_rating": "Rating of a named metricId (key); value is a rating or list",
    "metric_confidence": "Confidence of a named metricId (key)",
    "metric_availability": "Availability of a named metricId (key)",
    "metric_source_mode": "Chosen source mode of a named metricId (key)",
    "completeness_computed": "Count of genuinely computed metrics",
    "completeness_unavailable": "Count of unavailable metrics",
    "completeness_defaulted": "Count of defaulted (screening-default) metrics",
}

PRESETS: dict[str, dict] = {
    "functional": {"field": "eci", "cmp": ">", "value": 0.69},
    "at_risk_or_better": {"field": "eci", "cmp": ">", "value": 0.39},
    # No condition threshold: every site that produced an ECI qualifies. A site
    # that never scored yields None here, so it stays not_evaluable rather than
    # being retained on absent evidence.
    "all_sites": {"field": "eci", "cmp": ">=", "value": 0.0},
    # Kept resolvable for archived batch requests and saved sessions; not offered
    # in the StreamCurves dropdown.
    "reference_condition": {"op": "and", "rules": [
        {"field": "eci", "cmp": ">", "value": 0.69},
        {"field": "every_sub_index", "cmp": ">", "value": 0.69},
        {"field": "every_function_score", "cmp": ">", "value": 10},
    ]},
}


def _metric(site: SiteResult, metric_id: str):
    for m in site.metrics:
        if m.metric_id == metric_id:
            return m
    return None


def _eval_predicate(site: SiteResult, rule: dict) -> Optional[bool]:
    field = rule["field"]
    op = rule.get("cmp", ">")
    value = rule.get("value")
    key = rule.get("key")

    if field == "eci":
        v = site.raw_eci
        return None if v is None else _cmp(v, op, value)

    if field == "sub_index":
        v = site.raw_sub_indices.get(key)
        return None if v is None else _cmp(v, op, value)

    if field == "every_sub_index":
        vals = list(site.raw_sub_indices.values())
        return None if not vals else all(_cmp(v, op, value) for v in vals)

    if field == "function_score":
        v = site.function_scores.get(key)
        return None if v is None else _cmp(v, op, value)

    if field == "every_function_score":
        vals = list(site.function_scores.values())
        return None if not vals else all(_cmp(v, op, value) for v in vals)

    if field in ("metric_rating", "metric_confidence", "metric_availability",
                 "metric_source_mode"):
        m = _metric(site, key)
        if m is None or m.availability not in ("available",):
            return None
        attr = {"metric_rating": m.final_rating,
                "metric_confidence": m.confidence,
                "metric_availability": m.availability,
                "metric_source_mode": m.source_mode}[field]
        if attr is None:
            return None
        return _cmp(attr, op, value)

    if field.startswith("completeness_"):
        attr = field[len("completeness_"):]
        v = getattr(site.completeness, attr, None)
        return None if v is None else _cmp(v, op, value)

    raise ValueError(f"unknown criteria field {field!r}")


def evaluate(rule: Optional[dict], site: SiteResult) -> Optional[bool]:
    """Tri-state evaluate a rule against a site. None = unevaluable/skipped."""
    if not rule:
        return None
    if "op" in rule:                      # AND/OR group
        op = rule["op"].lower()
        children = [evaluate(r, site) for r in rule.get("rules", [])]
        evaluable = [c for c in children if c is not None]
        if not evaluable:
            return None
        if op == "and":
            return all(evaluable)
        if op == "or":
            return any(evaluable)
        raise ValueError(f"unknown group op {op!r}")
    return _eval_predicate(site, rule)


def _reasons(rule: Optional[dict], site: SiteResult) -> list[str]:
    """Flatten predicate outcomes into human-readable reasons (leaves only)."""
    if not rule:
        return ["no criteria (empty rule)"]
    out: list[str] = []

    def walk(r):
        if "op" in r:
            for c in r.get("rules", []):
                walk(c)
            return
        res = _eval_predicate(site, r)
        tag = {True: "pass", False: "fail", None: "skip (no data)"}[res]
        key = f"[{r['key']}]" if r.get("key") else ""
        out.append(f"{r['field']}{key} {r.get('cmp', '')} {r.get('value')}: {tag}")

    walk(rule)
    return out


def qualify_site(site: SiteResult, rule: Optional[dict], *,
                 criteria_id: str = "custom") -> None:
    """Set ``site.qualification`` (automatic decision only) from ``rule`` in place.

    ``qualified`` / ``excluded`` / ``not_evaluable``. The final decision defaults to
    ``retained`` for qualified sites and ``excluded`` otherwise (an unreviewed
    starting point); a reviewer may override it later.
    """
    result = evaluate(rule, site)
    q = site.qualification
    q.criteria_id = criteria_id
    q.reasons = _reasons(rule, site)
    if result is None:
        q.auto, q.final = "not_evaluable", "pending"
    elif result:
        q.auto, q.final = "qualified", "retained"
        q.partial_evidence = site.completeness.unavailable > 0
    else:
        q.auto, q.final = "excluded", "excluded"

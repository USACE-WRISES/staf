"""CONF-01/02 numeric confidence, Review Priority, and the SELECT-02 metric score.

The methodology specifies a 0 to 100 workflow-triage confidence per decision,
built from six weighted components with hard caps that no subtotal can exceed,
plus a Review Priority ordering and a 100-point within-function metric score.
The component weights, band cutoffs, caps, and score weights all come from the
methodology config (one threshold home). The mapping formulas below, from
recorded evidence to a 0 to 1 component fraction, are provisional operating
formulas: they are deterministic, documented here, and subject to calibration
against the pilot benchmarks before any status promotion. Confidence is a
triage score, never a probability that a decision is scientifically correct.
"""

from __future__ import annotations

from typing import Any, Optional

from . import methodology


# --------------------------------------------------------------------------- #
# CONF-01: component fractions from recorded evidence
# --------------------------------------------------------------------------- #
_SAMPLE_FRAC = {"adequate": 1.0, "exploratory": 0.5, "insufficient": 0.2,
                "too_few": 0.0, "unknown": 0.2}
_MISSING_FRAC = {"auto": 1.0, "caution": 0.75, "review": 0.4, "unknown": 0.6}
_DIRECTION_FRAC = {"high": 1.0, "moderate": 0.7}


def _frac_data_adequacy(ev: dict) -> float:
    sample = _SAMPLE_FRAC.get(str(ev.get("sample_disposition")), 0.2)
    missing = _MISSING_FRAC.get(str(ev.get("missingness_disposition")), 0.6)
    return sample * missing


def _frac_statistical_strength(ev: dict) -> float:
    ok = str(ev.get("curve_status") or "") in ("auto_ok", "complete")
    base = 0.6 if ok else 0.2
    loo = ev.get("loo") or {}
    if loo.get("evaluable"):
        delta = loo.get("held_out_mean_abs_delta")
        if delta is not None:
            if delta < 0.02:
                base += 0.4
            elif delta < 0.05:
                base += 0.25
            else:
                base += 0.1
    return min(base, 1.0)


def _frac_robustness(ev: dict) -> float:
    boot = ev.get("bootstrap") or {}
    if not boot.get("evaluable"):
        return 0.2
    structure = float(boot.get("structure_stability") or 0.0)
    shape = float(boot.get("shape_stability") or 0.0)
    frac = 0.5 * structure + 0.5 * shape
    infl = ev.get("influence") or {}
    if infl.get("flagged"):
        frac *= 0.5
    return min(frac, 1.0)


def _frac_ecological(ev: dict) -> float:
    if ev.get("shape_ok") is False:
        return 0.0
    return _DIRECTION_FRAC.get(str(ev.get("direction_confidence")), 0.3)


def _frac_interpretability(ev: dict) -> float:
    parts = [bool(ev.get("mapped")), bool(ev.get("units_present")),
             bool(ev.get("family_known", True))]
    return sum(parts) / 3.0


def _frac_rule_completeness(ev: dict) -> float:
    if str(ev.get("curve_status")) == "error":
        return 0.0
    diagnostics = [ev.get("loo") or {}, ev.get("bootstrap") or {},
                   ev.get("influence") or {}]
    return 1.0 if all(d.get("evaluable") for d in diagnostics) else 0.5


_COMPONENT_FRACS = {
    "data_adequacy_quality": _frac_data_adequacy,
    "statistical_strength": _frac_statistical_strength,
    "robustness_out_of_sample": _frac_robustness,
    "ecological_plausibility": _frac_ecological,
    "interpretability_feasibility": _frac_interpretability,
    "rule_completeness_agreement": _frac_rule_completeness,
}


def curve_confidence(evidence: dict) -> dict:
    """The CONF-01 score with CONF-02 caps for one curve's recorded evidence.

    ``evidence`` carries: sample_disposition, missingness_disposition,
    curve_status, loo, bootstrap, influence (curve_stability results),
    direction_confidence, shape_ok, mapped, units_present, reference_tier.
    Returns components, the uncapped subtotal, the caps that applied, the final
    total, and the band label.
    """
    weights = methodology.threshold("confidence_rules.components")
    components: dict[str, float] = {}
    subtotal = 0.0
    for name, weight in weights.items():
        frac = _COMPONENT_FRACS[name](evidence)
        points = float(weight) * max(0.0, min(1.0, frac))
        components[name] = round(points, 1)
        subtotal += points

    caps = methodology.threshold("confidence_rules.caps")
    applied: list[str] = []
    total = subtotal
    loo = evidence.get("loo") or {}
    if str(evidence.get("reference_tier")) == "best_available":
        total = min(total, float(caps["best_available_reference"]))
        applied.append("best_available_reference")
    if str(evidence.get("sample_disposition")) != "adequate":
        total = min(total, float(caps["sample_below_minimum"]))
        applied.append("sample_below_minimum")
    if not loo.get("evaluable"):
        # CURVE-02: without completed out-of-sample evaluation, confidence
        # cannot claim robustness. Leave-one-site-out on one-row-per-site data
        # is the grouped CV this rule requires, so a run that produced it does
        # not take this cap.
        total = min(total, float(caps["no_out_of_sample_validation"]))
        applied.append("no_out_of_sample_validation")
    if evidence.get("leakage_unresolved"):
        total = min(total, float(caps["unresolved_leakage_or_circularity"]))
        applied.append("unresolved_leakage_or_circularity")
    if _frac_ecological(evidence) <= 0.3 and evidence.get("shape_ok") is not False:
        # No settled ecological rationale (direction below moderate confidence).
        total = min(total, float(caps["no_ecological_rationale"]))
        applied.append("no_ecological_rationale")

    bands = methodology.threshold("confidence_rules.bands")
    total = round(total, 1)
    if total >= float(bands["high_minimum"]):
        label = "High"
    elif total >= float(bands["moderate_minimum"]):
        label = "Moderate"
    else:
        label = "Low"
    return {"components": components, "subtotal": round(subtotal, 1),
            "caps_applied": applied, "total": total, "label": label,
            "basis": "conf-01-v1"}


# --------------------------------------------------------------------------- #
# Review Priority = Impact x Uncertainty x Novelty
# --------------------------------------------------------------------------- #
def review_priority(*, impact: Optional[int] = None, confidence_label: str = "Low",
                    ref02: bool = False, coverage_critical: bool = False,
                    in_portfolio: bool = False, novel_stratifier: bool = False,
                    optimum_form: bool = False) -> dict:
    """Provisional 1-3 scales, multiplied to a 1-27 priority.

    Impact: 3 when the decision rides a reference-tier fallback or is the only
    metric covering a function, 2 when the metric is in the compact portfolio,
    else 1 (or pass ``impact`` explicitly). Uncertainty from the confidence
    band. Novelty: 3 for a newly applied stratifier, 2 for a two-sided curve
    form, else 1.
    """
    if impact is None:
        impact = 3 if (ref02 or coverage_critical) else (2 if in_portfolio else 1)
    uncertainty = {"Low": 3, "Moderate": 2, "High": 1}.get(str(confidence_label), 3)
    novelty = 3 if novel_stratifier else (2 if optimum_form else 1)
    score = int(impact) * int(uncertainty) * int(novelty)
    return {"impact": int(impact), "uncertainty": uncertainty,
            "novelty": novelty, "priority": score}


# --------------------------------------------------------------------------- #
# SELECT-02: the 100-point within-function metric score
# --------------------------------------------------------------------------- #
def metric_score(evidence: dict) -> dict:
    """Rank a candidate within its function. Never an automatic decision: the
    tradeoffs stay visible in the per-component points."""
    weights = methodology.threshold("metric_portfolio.metric_score_weights")
    sample = _SAMPLE_FRAC.get(str(evidence.get("sample_disposition")), 0.2)
    missing = _MISSING_FRAC.get(str(evidence.get("missingness_disposition")), 0.6)
    boot = evidence.get("bootstrap") or {}
    loo = evidence.get("loo") or {}
    fracs = {
        "ecological_relevance_coverage": (
            (0.6 if evidence.get("mapped") else 0.0)
            + {"high": 0.4, "moderate": 0.25}.get(
                str(evidence.get("direction_confidence")), 0.0)),
        "data_quality_support_coverage": 0.6 * sample + 0.4 * missing,
        "curve_performance_stability": (
            (0.5 if str(evidence.get("curve_status")) in ("auto_ok", "complete") else 0.0)
            + (0.3 if loo.get("evaluable")
               and (loo.get("held_out_mean_abs_delta") or 1.0) < 0.05 else 0.0)
            + (0.2 if (boot.get("shape_stability") or 0.0) >= 0.8 else 0.0)),
        "nonredundancy_complementarity": max(
            0.0, 1.0 - 0.5 * int(evidence.get("redundant_pairs") or 0)),
        "interpretability_management": (
            (0.5 if evidence.get("units_present") else 0.0)
            + (0.5 if evidence.get("family_known", True) else 0.0)),
        "operational_availability_reproducibility": (
            (0.5 if evidence.get("source_available", True) else 0.0) + 0.5),
    }
    components = {name: round(float(weights[name]) * max(0.0, min(1.0, frac)), 1)
                  for name, frac in fracs.items()}
    return {"components": components,
            "total": round(sum(components.values()), 1),
            "basis": "select-02-v1"}

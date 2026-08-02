"""Metric-predictor overlap analysis (rule OVL-01).

A metric that tracks one of its own predictors too closely carries no independent
information: the predictor already explains it, so scoring it as a separate line of
evidence double-counts the same signal. This module measures that overlap and hands
back a per-metric verdict the wizard and the headless agent both act on.

Why it lives here and not in ``regional_agent``: the analysis is pure (frames in,
frames out) and both callers need it, but ``views/`` must never import the agent's
orchestration layer. ``regional_agent.redundancy_matrix`` now delegates here so there
is exactly one Spearman implementation in the app.

The rule, stated once:

    OVL-01 -- a metric whose |Spearman rho| against an assigned predictor reaches 0.80
    over at least 8 complete pairs is redundant with that predictor, and is dropped
    from the scored metric set unless a reviewer records a rationale to keep it.

Spearman is primary (monotone association without assuming a linear form, matching
RED-01); Pearson rides along for reporting so a reviewer can see when the two diverge.
Auto-drop is scoped to metric-vs-PREDICTOR pairs: dropping a metric because a predictor
already explains it is unambiguous, whereas choosing between two redundant METRICS needs
a keeper decision that automation should not make (SELECT-01 routes that to a person).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

OVERLAP_METHOD_VERSION = "spearman-abs-1"

DEFAULT_RHO_THRESHOLD = 0.80   # OVL-01 auto-drop
DEFAULT_MIN_PAIR_N = 8         # complete pairs required before a flag is trusted
DEFAULT_REPORT_FLOOR = 0.65    # reported as a near-miss, never auto-dropped

PARTNER_PREDICTOR = "predictor"
PARTNER_METRIC = "metric"

STATUS_CLEAR = "clear"
STATUS_OVERLAP = "overlap"

# Identity/geometry columns are never part of an overlap question.
_NON_ANALYSIS = {"site_id", "site_name", "lat", "lon", "comid", "source", ".source"}

PAIR_COLUMNS = [
    "metric", "partner", "partner_role", "n", "spearman", "pearson",
    "abs_spearman", "flagged", "low_n", "low_variety",
]


# --------------------------------------------------------------------------- #
# Role adapters -- the two callers describe roles differently.
# --------------------------------------------------------------------------- #
def roles_from_assignments(assignments: Any) -> tuple[list[str], list[str]]:
    """(metric columns, predictor columns) from the wizard's classify assignments.

    A column can legitimately hold both roles (``role: both`` is the metric_map default
    for every landscape variable), so the two lists may overlap; self-pairs are excluded
    downstream rather than here.
    """
    if assignments is None or not len(assignments):
        return [], []
    df = assignments
    cols = set(df.columns)
    if not {"column"} <= cols:
        return [], []
    metrics = [str(c) for c in df.loc[df.get("is_metric", False) == True, "column"]]   # noqa: E712
    preds = [str(c) for c in df.loc[df.get("is_predictor", False) == True, "column"]]  # noqa: E712
    return metrics, preds


def roles_from_configs(
    metric_config: Optional[dict],
    predictor_config: Optional[dict] = None,
    *,
    data_columns: Optional[Iterable[str]] = None,
) -> tuple[list[str], list[str]]:
    """(metric columns, predictor columns) from a built project's configs.

    Used for a restored or agent-built project, where the authoritative roles live in
    ``metric_config`` / ``predictor_config`` and the wizard's assignments frame does not
    exist. Entries are resolved to their ``column_name`` so the result indexes the data.
    """
    known = None if data_columns is None else {str(c) for c in data_columns}

    def _columns(cfg: Optional[dict]) -> list[str]:
        out: list[str] = []
        for key, entry in (cfg or {}).items():
            col = str((entry or {}).get("column_name") or key)
            if known is None or col in known:
                out.append(col)
        return out

    return _columns(metric_config), _columns(predictor_config)


# --------------------------------------------------------------------------- #
# Correlation
# --------------------------------------------------------------------------- #
def _numeric_frame(data: Any, columns: Iterable[str]) -> tuple[pd.DataFrame, list[dict]]:
    """Coerce the requested columns to numeric, reporting what could not be used."""
    skipped: list[dict] = []
    keep: list[str] = []
    frame = pd.DataFrame(data) if not isinstance(data, pd.DataFrame) else data
    present = set(frame.columns)
    out = {}
    for col in dict.fromkeys(str(c) for c in columns):
        if col in _NON_ANALYSIS:
            continue
        if col not in present:
            skipped.append({"column": col, "reason": "absent"})
            continue
        series = pd.to_numeric(frame[col], errors="coerce")
        if not series.notna().any():
            skipped.append({"column": col, "reason": "non_numeric"})
            continue
        if series.nunique(dropna=True) <= 1:
            skipped.append({"column": col, "reason": "constant"})
            continue
        out[col] = series
        keep.append(col)
    return (pd.DataFrame(out, index=frame.index) if out else pd.DataFrame()), skipped


def pairwise_correlations(
    data: Any,
    metric_columns: Iterable[str],
    partner_columns: Iterable[str],
    *,
    partner_role: str = PARTNER_PREDICTOR,
    threshold: float = DEFAULT_RHO_THRESHOLD,
    min_n: int = DEFAULT_MIN_PAIR_N,
    report_floor: float = DEFAULT_REPORT_FLOOR,
) -> tuple[pd.DataFrame, list[dict]]:
    """Every metric x partner pair at or above ``report_floor``, plus skip reasons.

    Correlations are computed once over the union of both column sets and sliced, and
    the per-pair n comes from the notna masks rather than a global ``dropna`` -- with
    ragged landscape data a shared dropna can collapse the sample to a handful of rows
    and quietly change every coefficient.
    """
    metrics = [str(c) for c in dict.fromkeys(metric_columns or [])]
    partners = [str(c) for c in dict.fromkeys(partner_columns or [])]
    union = list(dict.fromkeys(metrics + partners))
    num, skipped = _numeric_frame(data, union)
    usable = set(num.columns)
    metrics = [c for c in metrics if c in usable]
    partners = [c for c in partners if c in usable]
    if not metrics or not partners:
        return pd.DataFrame(columns=PAIR_COLUMNS), skipped

    sp = num.corr("spearman")
    pe = num.corr("pearson")
    mask = num.notna().astype(int)
    pair_n = mask.T.dot(mask)          # exact complete-pair counts
    # A two-valued column correlates perfectly with anything that splits the same way;
    # that is an artifact of coarseness, not redundancy, so it is reported but never
    # auto-flagged.
    binary = {c for c in usable if num[c].nunique(dropna=True) <= 2}

    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for m in metrics:
        for p in partners:
            if m == p:
                continue                      # self-pair: recorded as self_role, not here
            key = (m, p) if m <= p else (p, m)
            if key in seen:
                continue                      # unordered dedup when the sets overlap
            seen.add(key)
            rho = sp.at[m, p] if (m in sp.index and p in sp.columns) else np.nan
            r = pe.at[m, p] if (m in pe.index and p in pe.columns) else np.nan
            if rho is None or not np.isfinite(rho):
                continue
            n = int(pair_n.at[m, p])
            low_n = n < int(min_n)
            low_variety = (m in binary) or (p in binary)
            arho = abs(float(rho))
            if arho < float(report_floor):
                continue
            rows.append({
                "metric": m,
                "partner": p,
                "partner_role": partner_role,
                "n": n,
                "spearman": float(rho),
                "pearson": float(r) if r is not None and np.isfinite(r) else np.nan,
                "abs_spearman": arho,
                "flagged": bool(arho >= float(threshold) and not low_n and not low_variety),
                "low_n": bool(low_n),
                "low_variety": bool(low_variety),
            })
    if not rows:
        return pd.DataFrame(columns=PAIR_COLUMNS), skipped
    out = pd.DataFrame(rows, columns=PAIR_COLUMNS)
    return out.sort_values("abs_spearman", ascending=False).reset_index(drop=True), skipped


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def metric_overlap_fingerprint(summary: dict) -> str:
    """16-hex digest over one metric's overlap evidence.

    Deliberately per metric, not per run: re-analysing after a role change must only
    re-open the metrics whose own numbers moved. The threshold is inside the payload so
    that lowering it correctly invalidates every stale reviewer confirmation.
    """
    payload = {
        "method_version": summary.get("method_version"),
        "threshold": summary.get("threshold"),
        "min_n": summary.get("min_n"),
        "partner_role": summary.get("partner_role"),
        "metric": summary.get("metric"),
        "self_role": bool(summary.get("self_role")),
        "partners": sorted(
            (str(p.get("partner")), round(float(p.get("spearman") or 0.0), 4),
             round(float(p.get("pearson") or 0.0), 4), int(p.get("n") or 0))
            for p in (summary.get("flagged_partners") or [])
        ),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def analyze_overlap(
    data: Any,
    *,
    metric_columns: Iterable[str],
    partner_columns: Iterable[str],
    partner_role: str = PARTNER_PREDICTOR,
    column_functions: Optional[dict] = None,
    labels: Optional[dict] = None,
    sources: Optional[dict] = None,
    threshold: float = DEFAULT_RHO_THRESHOLD,
    min_n: int = DEFAULT_MIN_PAIR_N,
    report_floor: float = DEFAULT_REPORT_FLOOR,
    analyzed_at: Optional[str] = None,
) -> dict:
    """Full overlap picture for one dataset: pairs, a per-metric verdict, and skips."""
    metrics = [str(c) for c in dict.fromkeys(metric_columns or []) if c not in _NON_ANALYSIS]
    partners = [str(c) for c in dict.fromkeys(partner_columns or []) if c not in _NON_ANALYSIS]
    pairs, skipped = pairwise_correlations(
        data, metrics, partners, partner_role=partner_role,
        threshold=threshold, min_n=min_n, report_floor=report_floor)

    both_roles = set(metrics) & set(partners)
    labels = labels or {}
    column_functions = column_functions or {}
    sources = sources or {}
    n_rows = 0 if data is None else len(data)

    by_metric: dict[str, dict] = {}
    for metric in metrics:
        mine = pairs[pairs["metric"] == metric] if len(pairs) else pairs
        flagged = [r for _, r in mine.iterrows() if r["flagged"]]
        near = [r for _, r in mine.iterrows() if not r["flagged"]]
        self_role = metric in both_roles
        if not flagged and not near and not self_role:
            continue
        reasons = [
            f"|Spearman rho| = {abs(r['spearman']):.2f} with {r['partner']} (n = {int(r['n'])})."
            for r in flagged
        ]
        if self_role:
            reasons.append(
                f"{metric} is also assigned as a {partner_role}, so it sits in its own "
                "predictor pool."
            )
        summary = {
            "metric": metric,
            "display_name": labels.get(metric, metric),
            "function": column_functions.get(metric) or "",
            "source": sources.get(metric) or "",
            "method_version": OVERLAP_METHOD_VERSION,
            "threshold": float(threshold),
            "min_n": int(min_n),
            "partner_role": partner_role,
            "self_role": bool(self_role),
            "n_flagged_partners": len(flagged),
            "flagged_partners": [_pair_dict(r, labels, sources) for r in flagged],
            "near_miss_partners": [_pair_dict(r, labels, sources) for r in near],
            "analyzed_at": analyzed_at,
        }
        worst = flagged[0] if flagged else (near[0] if near else None)
        summary["worst_partner"] = None if worst is None else str(worst["partner"])
        summary["worst_spearman"] = None if worst is None else float(worst["spearman"])
        summary["worst_pearson"] = None if worst is None else _maybe_float(worst["pearson"])
        summary["worst_n"] = None if worst is None else int(worst["n"])
        summary["status"] = STATUS_OVERLAP if flagged else STATUS_CLEAR
        summary["fingerprint"] = metric_overlap_fingerprint(summary)
        summary["reasons"] = reasons
        by_metric[metric] = summary

    return {
        "method_version": OVERLAP_METHOD_VERSION,
        "threshold": float(threshold),
        "min_n": int(min_n),
        "report_floor": float(report_floor),
        "partner_role": partner_role,
        "analyzed_at": analyzed_at,
        "n_rows": int(n_rows),
        "metric_columns": metrics,
        "partner_columns": partners,
        "pairs": pairs,
        "by_metric": by_metric,
        "skipped": skipped,
    }


def _maybe_float(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _pair_dict(row, labels: dict, sources: dict) -> dict:
    partner = str(row["partner"])
    return {
        "partner": partner,
        "partner_display": labels.get(partner, partner),
        "partner_source": sources.get(partner) or "",
        "spearman": float(row["spearman"]),
        "pearson": _maybe_float(row["pearson"]),
        "n": int(row["n"]),
        "flagged": bool(row["flagged"]),
        "low_n": bool(row["low_n"]),
        "low_variety": bool(row["low_variety"]),
    }


# --------------------------------------------------------------------------- #
# Legacy view: the metric-vs-metric redundancy CSV the agent already publishes.
# --------------------------------------------------------------------------- #
def redundancy_view(analysis: dict, column_functions: Optional[dict] = None) -> pd.DataFrame:
    """``analysis`` reshaped into the historical RED-01 ``redundancy_matrix`` columns."""
    column_functions = column_functions or {}
    pairs = analysis.get("pairs")
    cols = ["metric_a", "metric_b", "function_a", "function_b", "same_function",
            "spearman", "pearson", "red01_spearman_flag", "code_pearson_flag", "divergence"]
    if pairs is None or not len(pairs):
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in pairs.iterrows():
        a, b = str(r["metric"]), str(r["partner"])
        fa = column_functions.get(a) or "(unmapped)"
        fb = column_functions.get(b) or "(unmapped)"
        sp = float(r["spearman"])
        pe = _maybe_float(r["pearson"])
        rows.append({
            "metric_a": a, "metric_b": b,
            "function_a": fa, "function_b": fb,
            "same_function": fa == fb,
            "spearman": sp,
            "pearson": pe,
            "red01_spearman_flag": abs(sp) >= DEFAULT_RHO_THRESHOLD,
            "code_pearson_flag": pe is not None and abs(pe) >= DEFAULT_RHO_THRESHOLD,
            "divergence": None if pe is None else abs(abs(sp) - abs(pe)),
        })
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("spearman", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)

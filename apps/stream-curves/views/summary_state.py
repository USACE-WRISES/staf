"""Summary-page state helpers — port of app/helpers/summary_page.R (part A).

Everything the Summary page and the four phase workspaces need: eligibility,
per-metric candidate/decision state, the phase 1/3/4 artifact backfills with
their step counts (these feed the workspace-modal prepare/steps registries),
phase-2 shared recompute, notes/status builders, and the recompute machinery.

Part B (the export-context builders: build_summary_export_context,
write_summary_export_stage, appendix tables) ships with M6's summary_export.

All functions take the AppState and use copy-then-set for container fields
(PORTING.md). Reads inside helpers use reactive.isolate() so they can be
called from effects, download handlers, and the modal-prepare task alike.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable

import numpy as np
import pandas as pd
from shiny import reactive

from streamcurves.consistency import (  # noqa: F401  (build_phase2_ranking re-exported)
    build_phase2_ranking,
    compute_strat_consistency,
)
from streamcurves.curves import (
    build_reference_curve,
    hydrate_reference_curve_result,
    normalize_reference_curve_result,
    reference_curve_rows_for_export,
)
from streamcurves.effects import compute_effect_sizes
from streamcurves.feasibility import assess_feasibility
from streamcurves.mapping import (
    function_mapping_full_coverage,
    validate_discipline_function_mapping,
)
from streamcurves.screening import (  # noqa: F401  (phase-1 candidate helpers re-exported)
    auto_phase1_candidate_status,
    build_metric_phase1_candidate_table_from_sources,
    screen_stratification,
)
from streamcurves import metric_names
from streamcurves.stability import assess_pattern_stability
from views.state import AppState, empty_phase2_settings

logger = logging.getLogger("streamcurves")

ANALYSIS_STEP_LABELS = [
    "Exploratory",
    "Cross-Metric Analysis",
    "Verification",
    "Reference Curves",
]


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == ""


def _iso(state: AppState, name: str):
    with reactive.isolate():
        return state.get(name)


def _first_not_none(*vals):
    """R %||% chain — DataFrames can't be used with `or`."""
    for v in vals:
        if v is not None:
            return v
    return None


def eligible_summary_metrics(metric_config: dict) -> list[str]:
    out = []
    for mk, mc in (metric_config or {}).items():
        if mc.get("metric_family") == "categorical":
            continue
        if mc.get("include_in_summary") is False:
            continue
        out.append(mk)
    return out


def get_first_value(df, column, default=None):
    if df is None or not isinstance(df, pd.DataFrame) or column not in df.columns or len(df) == 0:
        return default
    value = df[column].iloc[0]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    return value


def _resolved_display_name(mc, metric: str) -> str:
    """The metric's readable name, resolving a stored code against the dictionary."""
    stored = (mc or {}).get("display_name")
    if metric_names.is_placeholder_name(stored, metric):
        return metric_names.display_name_for(metric, stored) or str(metric)
    return str(stored)


def metric_direction_label(metric_config: dict, metric: str) -> str:
    mc = (metric_config or {}).get(metric)
    if mc is None:
        return "Unknown"
    hib = mc.get("higher_is_better")
    if hib is True:
        return "Higher is better"
    if hib is False:
        return "Lower is better"
    return "Neutral"


def get_metric_precheck_row(state: AppState, metric: str) -> pd.DataFrame:
    df = _iso(state, "precheck_df")
    if df is None:
        return pd.DataFrame()
    return df[df["metric"] == metric].head(1)


def get_metric_config_allowed_strats(state: AppState, metric: str) -> list[str]:
    mc = (_iso(state, "metric_config") or {}).get(metric) or {}
    allowed = mc.get("allowed_stratifications") or []
    strat_config = _iso(state, "strat_config") or {}
    return [s for s in allowed if s in strat_config]


def get_metric_allowed_strats(state: AppState, metric: str) -> list[str]:
    override = (_iso(state, "summary_available_overrides") or {}).get(metric)
    allowed = override if override is not None else get_metric_config_allowed_strats(state, metric)
    strat_config = _iso(state, "strat_config") or {}
    return [s for s in allowed if s in strat_config]


# Why a metric has no stratification to analyse. The three cases used to read as
# one "has not been run" message, which said a step was skipped when in fact
# there was nothing to run: a published assessment with no stratification
# variables looked identical to one whose screening genuinely failed.
STRAT_OK = "ok"
STRAT_NO_CONFIG = "no_strat_config"          # the project defines none at all
STRAT_NONE_ALLOWED = "none_allowed"          # none enabled for this metric
STRAT_COLUMNS_ABSENT = "columns_absent"      # configured, but not in the data


def usable_strat_keys(state: AppState) -> list[str]:
    """Configured stratifications whose column actually carries values here.

    Walked once per refresh rather than per metric: get_stratification_values
    materializes a column, and the summary page asks this for every row.
    """
    strat_config = _iso(state, "strat_config") or {}
    if not strat_config:
        return []
    data = _iso(state, "data")
    out = []
    for sk in strat_config:
        try:
            values = get_stratification_values(data, sk, strat_config)
        except Exception:  # noqa: BLE001 — a broken stratification is unusable, not fatal
            continue
        if values is not None and len(values) > 0 and values.notna().any():
            out.append(sk)
    return out


def metric_strat_eligibility(
    state: AppState, metric: str, usable: list[str] | None = None
) -> tuple[list[str], str]:
    """``(allowed, reason)`` where reason is one of the STRAT_* constants."""
    if not (_iso(state, "strat_config") or {}):
        return [], STRAT_NO_CONFIG
    allowed = get_metric_allowed_strats(state, metric)
    if not allowed:
        return [], STRAT_NONE_ALLOWED
    if usable is None:
        usable = usable_strat_keys(state)
    live = [s for s in allowed if s in usable]
    if not live:
        return allowed, STRAT_COLUMNS_ABSENT
    return live, STRAT_OK


def normalize_curve_stratification_value(state: AppState, metric: str, selected) -> str:
    if selected is None or _is_blank(selected) or selected == "none":
        return "none"
    if selected in get_metric_allowed_strats(state, metric):
        return str(selected)
    return "none"


def get_metric_curve_strat_recommendation(state: AppState, metric: str) -> str:
    candidates = get_metric_phase1_candidate_table(state, metric, include_all_allowed=True)
    if candidates is None or len(candidates) == 0:
        return "none"
    ranked = candidates[candidates["candidate_status"].isin(["promising", "possible"])].copy()
    if len(ranked) == 0:
        return "none"
    order = {"promising": 0, "possible": 1}
    ranked["_o"] = ranked["candidate_status"].map(order)
    ranked = ranked.sort_values(["_o", "p_value", "stratification"], kind="mergesort")
    return normalize_curve_stratification_value(state, metric, ranked["stratification"].iloc[0])


def get_metric_curve_stratification(
    state: AppState, metric: str, fallback_to_auto: bool = True
) -> str:
    stored = (_iso(state, "curve_stratification") or {}).get(metric)
    normalized = normalize_curve_stratification_value(state, metric, stored)
    if stored is not None or not fallback_to_auto:
        return normalized
    return get_metric_curve_strat_recommendation(state, metric)


def get_strat_display_name(state: AppState, strat_key) -> str:
    if strat_key is None or _is_blank(strat_key):
        return "None"
    cfg = (_iso(state, "strat_config") or {}).get(strat_key) or {}
    return cfg.get("display_name") or str(strat_key)


def get_metric_curve_strat_choices(state: AppState, metric: str) -> dict[str, str]:
    allowed = get_metric_allowed_strats(state, metric)
    choices = {"none": "None"}
    for sk in allowed:
        choices[sk] = get_strat_display_name(state, sk)
    return choices


def set_metric_curve_stratification(
    state: AppState, metric: str, selected, clear_phase4: bool = True
) -> str:
    old_value = get_metric_curve_stratification(state, metric, fallback_to_auto=False)
    normalized = normalize_curve_stratification_value(state, metric, selected)

    with reactive.isolate():
        cs = dict(state.curve_stratification() or {})
    cs[metric] = normalized
    state.curve_stratification.set(cs)
    sync_metric_decision_state(
        state, metric, build_metric_strat_decision(state, metric, normalized)
    )
    if clear_phase4 and old_value != normalized:
        clear_metric_phase4_results(state, metric)
    return normalized


def get_metric_curve_strat_label(state: AppState, metric: str, selected=None) -> str:
    if selected is None:
        selected = get_metric_curve_stratification(state, metric)
    if selected is None or selected == "none":
        return "None"
    return get_strat_display_name(state, selected)


def get_summary_available_choices(state: AppState) -> list[str]:
    keys = list((_iso(state, "strat_config") or {}).keys())
    if not keys:
        return []
    return sorted(keys, key=lambda sk: (get_strat_display_name(state, sk), sk))


def format_strat_list(state: AppState, strat_keys) -> str:
    strat_keys = list(strat_keys or [])
    if not strat_keys:
        return "None"
    return ", ".join(get_strat_display_name(state, sk) for sk in strat_keys)


# --------------------------------------------------------------------------- #
# Summary edit notes (R:161-208)
# --------------------------------------------------------------------------- #


def empty_summary_note_store() -> dict[str, list]:
    return {label: [] for label in ANALYSIS_STEP_LABELS}


def normalize_summary_note_items(items) -> list[dict]:
    out = []
    for item in items or []:
        text = (item or {}).get("text")
        if not text:
            continue
        out.append({"level": (item or {}).get("level") or "info", "text": text})
    return out


def get_metric_summary_edit_notes(state: AppState, metric: str) -> dict[str, list]:
    metric_notes = (_iso(state, "summary_edit_notes") or {}).get(metric)
    out = empty_summary_note_store()
    if metric_notes is None:
        return out
    for phase in out:
        out[phase] = normalize_summary_note_items(metric_notes.get(phase))
    return out


def set_metric_summary_edit_notes(state: AppState, metric: str, phase: str, items) -> None:
    with reactive.isolate():
        notes = dict(state.summary_edit_notes() or {})
    metric_notes = dict(notes.get(metric) or empty_summary_note_store())
    metric_notes[phase] = normalize_summary_note_items(items)
    notes[metric] = metric_notes
    state.summary_edit_notes.set(notes)


# --------------------------------------------------------------------------- #
# Phase 2 shared (R:210-422)
# --------------------------------------------------------------------------- #


def get_global_phase2_passed(state: AppState, metric: str | None = None) -> list[str]:
    ranking = _iso(state, "phase2_ranking")
    # A restored frame without the ranking columns would raise a KeyError here,
    # inside build_metric_notes, and blank the whole summary row.
    if (
        ranking is None
        or len(ranking) == 0
        or "tier" not in ranking.columns
        or "stratification" not in ranking.columns
    ):
        out: list[str] = []
    else:
        out = (
            ranking.loc[ranking["tier"] == "Broad-Use Candidate", "stratification"]
            .astype(str)
            .unique()
            .tolist()
        )
    if metric is not None:
        allowed = set(get_metric_allowed_strats(state, metric))
        out = [s for s in out if s in allowed]
    return out


def get_metric_phase2_passed(state: AppState, metric: str) -> list[str]:
    allowed = get_metric_allowed_strats(state, metric)
    passed = set(get_global_phase2_passed(state, metric))
    return [s for s in allowed if s in passed]


def set_metric_phase2_override(state: AppState, metric: str, selected) -> None:
    allowed = set(get_metric_allowed_strats(state, metric))
    with reactive.isolate():
        overrides = dict(state.phase2_metric_overrides() or {})
    overrides[metric] = [s for s in (selected or []) if s in allowed]
    state.phase2_metric_overrides.set(overrides)


def get_phase2_metric_choices(state: AppState) -> list[str]:
    l1 = _iso(state, "all_layer1_results") or {}
    return [mk for mk, df in l1.items() if isinstance(df, pd.DataFrame) and len(df) > 0]


def get_phase2_strat_choices(state: AppState, metric_filter=None) -> list[str]:
    l1 = _iso(state, "all_layer1_results") or {}
    if metric_filter is None:
        metric_filter = get_phase2_metric_choices(state)
    metric_filter = [m for m in (metric_filter or []) if m in l1]
    if not metric_filter:
        return []
    strats: set[str] = set()
    for mk in metric_filter:
        df = l1.get(mk)
        if df is None or len(df) == 0:
            continue
        strats.update(df["stratification"].astype(str).unique().tolist())
    return sorted(strats)


def normalize_phase2_settings(state: AppState, settings=None) -> dict:
    defaults = empty_phase2_settings()
    if settings is None:
        settings = _iso(state, "phase2_settings") or defaults

    metric_choices = get_phase2_metric_choices(state)
    metric_filter = [
        m for m in (settings.get("metric_filter") or metric_choices) if m in metric_choices
    ]
    if not metric_filter and metric_choices:
        metric_filter = metric_choices

    strat_choices = get_phase2_strat_choices(state, metric_filter)
    strat_filter = [s for s in (settings.get("strat_filter") or strat_choices) if s in strat_choices]
    if not strat_filter and strat_choices:
        strat_filter = strat_choices

    try:
        sig = float(settings.get("sig_threshold", defaults["sig_threshold"]))
    except (TypeError, ValueError):
        sig = defaults["sig_threshold"]
    if not math.isfinite(sig):
        sig = defaults["sig_threshold"]
    sig = min(max(sig, 0.01), 0.10)

    try:
        support = float(settings.get("support_threshold", defaults["support_threshold"]))
    except (TypeError, ValueError):
        support = defaults["support_threshold"]
    if not math.isfinite(support):
        support = defaults["support_threshold"]
    support = min(max(support, 0.10), 0.90)

    return {
        "metric_filter": metric_filter,
        "strat_filter": strat_filter,
        "sig_threshold": sig,
        "support_threshold": support,
    }


def set_phase2_settings(state: AppState, settings=None) -> dict:
    normalized = normalize_phase2_settings(state, settings)
    state.phase2_settings.set(normalized)
    return normalized


def recompute_phase2_shared(state: AppState, settings=None, persist_settings: bool = True):
    settings = (
        set_phase2_settings(state, settings)
        if persist_settings
        else normalize_phase2_settings(state, settings)
    )

    if len(settings["metric_filter"]) < 2 or len(settings["strat_filter"]) == 0:
        state.cross_metric_consistency.set(None)
        state.phase2_ranking.set(None)
        return None

    l1_all = _iso(state, "all_layer1_results") or {}
    l2_all = _iso(state, "all_layer2_results") or {}
    strat_filter = set(settings["strat_filter"])

    filtered_l1 = {}
    for mk in settings["metric_filter"]:
        df = l1_all.get(mk)
        if df is None or len(df) == 0:
            continue
        sub = df[df["stratification"].isin(strat_filter)]
        if len(sub) > 0:
            filtered_l1[mk] = sub
    filtered_l2 = {}
    for mk in settings["metric_filter"]:
        df = l2_all.get(mk)
        if df is None or len(df) == 0:
            continue
        sub = df[df["stratification"].isin(strat_filter)]
        if len(sub) > 0:
            filtered_l2[mk] = sub

    if len(filtered_l1) < 2:
        state.cross_metric_consistency.set(None)
        state.phase2_ranking.set(None)
        return None

    with reactive.isolate():
        metric_config = state.metric_config() or {}
        strat_config = state.strat_config() or {}
    result = compute_strat_consistency(
        filtered_l1, filtered_l2, metric_config, strat_config,
        sig_threshold=settings["sig_threshold"],
    )
    ranking = build_phase2_ranking(result, _iso(state, "phase1_candidates") or {},
                                   settings["support_threshold"])
    state.cross_metric_consistency.set(result)
    state.phase2_ranking.set(ranking)
    return {"result": result, "ranking": ranking, "settings": settings}


def refresh_phase2_ranking_shared(state: AppState, settings=None, persist_settings: bool = True):
    settings = (
        set_phase2_settings(state, settings)
        if persist_settings
        else normalize_phase2_settings(state, settings)
    )
    result = _iso(state, "cross_metric_consistency")
    summary = (result or {}).get("summary") if isinstance(result, dict) else None
    if summary is None or not isinstance(summary, pd.DataFrame) or len(summary) == 0:
        state.phase2_ranking.set(None)
        return None
    ranking = build_phase2_ranking(result, _iso(state, "phase1_candidates") or {},
                                   settings["support_threshold"])
    state.phase2_ranking.set(ranking)
    return {"result": result, "ranking": ranking, "settings": settings}


# --------------------------------------------------------------------------- #
# Phase 1 candidate tables (R:424-553)
# --------------------------------------------------------------------------- #


def get_metric_phase1_candidate_table(
    state: AppState, metric: str, include_all_allowed: bool = True
) -> pd.DataFrame:
    return build_metric_phase1_candidate_table_from_sources(
        metric=metric,
        allowed=get_metric_allowed_strats(state, metric),
        existing=(_iso(state, "phase1_candidates") or {}).get(metric),
        l1=(_iso(state, "all_layer1_results") or {}).get(metric),
        l2=(_iso(state, "all_layer2_results") or {}).get(metric),
        include_all_allowed=include_all_allowed,
    )


def get_metric_phase1_selected(state: AppState, metric: str) -> list[str]:
    tbl = get_metric_phase1_candidate_table(state, metric)
    if len(tbl) == 0:
        return []
    allowed = set(get_metric_allowed_strats(state, metric))
    sel = tbl[
        tbl["candidate_status"].isin(["promising", "possible"])
        & tbl["stratification"].isin(allowed)
    ]["stratification"].astype(str)
    return list(dict.fromkeys(sel))


def set_metric_phase1_candidates(state: AppState, metric: str, selected) -> None:
    allowed = get_metric_allowed_strats(state, metric)
    selected = [s for s in (selected or []) if s in allowed]
    base = get_metric_phase1_candidate_table(state, metric, include_all_allowed=True)

    if len(base) == 0 and allowed:
        base = pd.DataFrame(
            {
                "metric": metric,
                "stratification": allowed,
                "p_value": np.nan,
                "epsilon_squared": np.nan,
                "effect_size_label": None,
                "min_group_n": np.nan,
                "candidate_status": "not_promising",
                "reviewer_note": "",
            }
        )

    with reactive.isolate():
        cands = dict(state.phase1_candidates() or {})
    if len(base) == 0:
        cands[metric] = pd.DataFrame()
        state.phase1_candidates.set(cands)
        return

    sel = set(selected)

    def status_for(row):
        if row["stratification"] in sel and row["candidate_status"] in ("promising", "possible"):
            return row["candidate_status"]
        if row["stratification"] in sel:
            return "possible"
        return "not_promising"

    base = base.copy()
    base["candidate_status"] = base.apply(status_for, axis=1)
    cands[metric] = base
    state.phase1_candidates.set(cands)


# --------------------------------------------------------------------------- #
# Phase 3 selection + decisions (R:555-731)
# --------------------------------------------------------------------------- #


def get_metric_phase3_choices(state: AppState, metric: str) -> list[str]:
    out = list(get_metric_phase1_selected(state, metric))
    for s in get_metric_phase2_passed(state, metric):
        if s not in out:
            out.append(s)
    return out


def get_metric_phase3_selected(state: AppState, metric: str) -> str:
    curve_choice = get_metric_curve_stratification(state, metric)
    if curve_choice:
        return curve_choice

    verified = (_iso(state, "phase3_verification") or {}).get(metric)
    if verified is not None and verified.get("selected_strat") is not None:
        return verified["selected_strat"]

    def decision_answer(tbl):
        if tbl is not None and len(tbl) > 0:
            if tbl["decision_type"].iloc[0] == "single":
                v = tbl["selected_strat"].iloc[0]
                return "none" if _is_blank(v) else str(v)
            return "none"
        return None

    completed = (_iso(state, "completed_metrics") or {}).get(metric) or {}
    ans = decision_answer(completed.get("strat_decision"))
    if ans is not None:
        return ans

    cached = (_iso(state, "metric_phase_cache") or {}).get(metric) or {}
    ans = decision_answer(cached.get("strat_decision_user"))
    if ans is not None:
        return ans

    if _iso(state, "current_metric") == metric:
        ans = decision_answer(_iso(state, "strat_decision_user"))
        if ans is not None:
            return ans
    return "none"


def build_metric_strat_decision(state: AppState, metric: str, selected_strat) -> pd.DataFrame:
    if selected_strat is None or _is_blank(selected_strat) or selected_strat == "none":
        return pd.DataFrame(
            [
                {
                    "metric": metric,
                    "decision_type": "none",
                    "selected_strat": None,
                    "selected_p_value": np.nan,
                    "selected_n_groups": np.nan,
                    "selected_min_n": np.nan,
                    "runner_up_strat": None,
                    "runner_up_p_value": np.nan,
                    "needs_review": False,
                    "review_reason": None,
                    "notes": "Updated from Summary page",
                }
            ]
        )

    l1 = (_iso(state, "all_layer1_results") or {}).get(metric)
    p1 = (_iso(state, "phase1_candidates") or {}).get(metric)
    row = l1[l1["stratification"] == selected_strat].head(1) if l1 is not None and len(l1) else pd.DataFrame()
    cand = p1[p1["stratification"] == selected_strat].head(1) if p1 is not None and len(p1) else pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "metric": metric,
                "decision_type": "single",
                "selected_strat": selected_strat,
                "selected_p_value": get_first_value(
                    row, "p_value", get_first_value(cand, "p_value", np.nan)
                ),
                "selected_n_groups": get_first_value(row, "n_groups", np.nan),
                "selected_min_n": get_first_value(
                    row, "min_group_n", get_first_value(cand, "min_group_n", np.nan)
                ),
                "runner_up_strat": None,
                "runner_up_p_value": np.nan,
                "needs_review": False,
                "review_reason": None,
                "notes": "Updated from Summary page",
            }
        ]
    )


def _frames_equal(a, b) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, pd.DataFrame) and isinstance(b, pd.DataFrame):
        return a.equals(b)
    return a is b


def ensure_metric_phase_cache(state: AppState, metric: str) -> dict:
    with reactive.isolate():
        cache = dict(state.metric_phase_cache() or {})
    if metric not in cache or cache[metric] is None:
        cache[metric] = {}
        state.metric_phase_cache.set(cache)
    return cache[metric]


def _update_cache_entry(state: AppState, metric: str, **fields) -> None:
    with reactive.isolate():
        cache = dict(state.metric_phase_cache() or {})
    entry = dict(cache.get(metric) or {})
    entry.update(fields)
    cache[metric] = entry
    state.metric_phase_cache.set(cache)


def sync_metric_decision_state(state: AppState, metric: str, decision_tbl) -> None:
    ensure_metric_phase_cache(state, metric)
    with reactive.isolate():
        is_current = state.current_metric() == metric
        current_decision = state.strat_decision_user()
        cache_entry = (state.metric_phase_cache() or {}).get(metric) or {}
    if is_current and not _frames_equal(current_decision, decision_tbl):
        state.strat_decision_user.set(decision_tbl)
    if not _frames_equal(cache_entry.get("strat_decision_user"), decision_tbl):
        _update_cache_entry(state, metric, strat_decision_user=decision_tbl)


def set_metric_phase3_selected(state: AppState, metric: str, selected_strat) -> str:
    choices = get_metric_phase3_choices(state, metric)
    if selected_strat is None or _is_blank(selected_strat):
        normalized = "none"
    elif selected_strat in (["none"] + choices + get_metric_allowed_strats(state, metric)):
        normalized = normalize_curve_stratification_value(state, metric, selected_strat)
    else:
        normalized = "none"

    with reactive.isolate():
        verification = dict(state.phase3_verification() or {})
    existing = verification.get(metric) or {}
    verification[metric] = {
        "finalists": choices,
        "pattern_results": existing.get("pattern_results"),
        "feasibility_results": existing.get("feasibility_results"),
        "verification_status": existing.get("verification_status")
        or ({sk: "verified" for sk in choices} if choices else {}),
        "selected_strat": normalized,
        "justification": existing.get("justification") or "Updated from Summary page",
    }
    state.phase3_verification.set(verification)
    set_metric_curve_stratification(state, metric, normalized, clear_phase4=False)
    return normalized


def clear_metric_phase4_results(state: AppState, metric: str) -> None:
    with reactive.isolate():
        completed = dict(state.completed_metrics() or {})
        stratum_results = dict(state.stratum_results() or {})
        is_current = state.current_metric() == metric
    completed.pop(metric, None)
    stratum_results.pop(metric, None)
    state.completed_metrics.set(completed)
    state.stratum_results.set(stratum_results)
    if is_current:
        state.reference_curve.set(None)
        state.current_stratum_level.set(None)
        state.phase4_data.set(None)
    _update_cache_entry(
        state,
        metric,
        reference_curve=None,
        current_stratum_level=None,
        phase4_data=None,
        stratum_results=None,
        phase4_signature=None,
        phase4_artifact_mode=None,
        phase4_curve_rows=None,
    )


# --------------------------------------------------------------------------- #
# Phase 4 cache/signature machinery (R:718-1123)
# --------------------------------------------------------------------------- #


def get_metric_phase4_decision_state(state: AppState, metric: str) -> pd.DataFrame:
    with reactive.isolate():
        is_current = state.current_metric() == metric
        live = state.strat_decision_user()
    if is_current and live is not None and len(live) > 0:
        return live
    cached = ((_iso(state, "metric_phase_cache") or {}).get(metric) or {}).get(
        "strat_decision_user"
    )
    if cached is not None and len(cached) > 0:
        return cached
    return build_metric_strat_decision(
        state, metric, get_metric_curve_stratification(state, metric)
    )


def build_metric_phase4_signature(state: AppState, metric: str, decision_tbl=None) -> dict | None:
    if metric is None or metric == "":
        return None
    if decision_tbl is None:
        decision_tbl = get_metric_phase4_decision_state(state, metric)
    if decision_tbl is None or len(decision_tbl) == 0:
        decision_tbl = build_metric_strat_decision(
            state, metric, get_metric_phase3_selected(state, metric)
        )
    selected = decision_tbl["selected_strat"].iloc[0] if len(decision_tbl) else None
    if isinstance(selected, float) and math.isnan(selected):
        selected = None
    return {
        "data_fingerprint": _iso(state, "data_fingerprint"),
        "config_version": _iso(state, "config_version") or 0,
        "decision_type": decision_tbl["decision_type"].iloc[0] if len(decision_tbl) else "none",
        "selected_strat": selected,
    }


def phase4_signature_matches(lhs, rhs) -> bool:
    return (lhs or None) == (rhs or None)


def phase4_artifact_mode_satisfies(entry_mode, artifact_mode: str = "summary") -> bool:
    resolved = entry_mode or "full"
    if artifact_mode == "summary":
        return resolved in ("summary", "full")
    return resolved == "full"


def get_metric_phase4_cached_result(state: AppState, metric: str) -> dict:
    cache_entry = (_iso(state, "metric_phase_cache") or {}).get(metric) or {}
    completed_entry = (_iso(state, "completed_metrics") or {}).get(metric) or {}

    def pick(field):
        v = cache_entry.get(field)
        return v if v is not None else completed_entry.get(field)

    return {
        "signature": pick("phase4_signature"),
        "artifact_mode": pick("phase4_artifact_mode"),
        "reference_curve": pick("reference_curve"),
        "stratum_results": pick("stratum_results"),
        "curve_rows": pick("phase4_curve_rows"),
    }


def extract_metric_phase4_curve_rows(entry) -> pd.DataFrame:
    if entry is None or (entry or {}).get("type") == "regional":
        return pd.DataFrame()
    stored = entry.get("curve_rows")
    if stored is None:
        stored = entry.get("phase4_curve_rows")
    if stored is not None:
        return pd.DataFrame(stored)

    if entry.get("stratum_results") is not None:
        frames = []
        for lvl, x in (entry["stratum_results"] or {}).items():
            payload = (x or {}).get("reference_curve", x)
            result = normalize_reference_curve_result(payload, stratum_label=lvl)
            if result is not None and result.get("curve_row") is not None:
                frames.append(result["curve_row"])
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    rc = entry.get("reference_curve")
    if rc is not None and (rc or {}).get("curve_row") is not None:
        normalized = normalize_reference_curve_result(rc)
        row = (normalized or {}).get("curve_row")
        return row if row is not None else pd.DataFrame()
    return pd.DataFrame()


def update_metric_phase4_completed_entry(state: AppState, metric: str, phase4_fields: dict):
    with reactive.isolate():
        completed = dict(state.completed_metrics() or {})
    entry = dict(completed.get(metric) or {})
    entry.update(phase4_fields)
    completed[metric] = entry
    state.completed_metrics.set(completed)
    return entry


def metric_phase4_entry_is_current(entry, expected_signature) -> bool:
    if entry is None or (entry or {}).get("type") == "regional":
        return False
    sig = entry.get("phase4_signature")
    if sig is None or not phase4_signature_matches(sig, expected_signature):
        return False
    return len(extract_metric_phase4_curve_rows(entry)) > 0


def get_metric_phase4_display_state(state: AppState, metric: str, decision_tbl=None) -> dict:
    if decision_tbl is None:
        decision_tbl = get_metric_phase4_decision_state(state, metric)
    expected = build_metric_phase4_signature(state, metric, decision_tbl)

    cache_entry = dict((_iso(state, "metric_phase_cache") or {}).get(metric) or {})
    cache_entry["strat_decision"] = _first_not_none(
        cache_entry.get("strat_decision_user"), decision_tbl
    )
    completed_entry = (_iso(state, "completed_metrics") or {}).get(metric) or {}

    for source, entry in (("cache", cache_entry), ("completed", completed_entry)):
        if metric_phase4_entry_is_current(entry, expected):
            return {
                "source": source,
                "artifact_mode": entry.get("phase4_artifact_mode") or "full",
                "reference_curve": entry.get("reference_curve"),
                "stratum_results": entry.get("stratum_results"),
                "strat_decision": _first_not_none(entry.get("strat_decision"), decision_tbl),
                "curve_rows": extract_metric_phase4_curve_rows(entry),
            }
    return {
        "source": "none",
        "artifact_mode": None,
        "reference_curve": None,
        "stratum_results": None,
        "strat_decision": decision_tbl,
        "curve_rows": pd.DataFrame(),
    }


def metric_has_phase4_cache(
    state: AppState, metric: str, decision_tbl=None, artifact_mode: str = "full"
) -> bool:
    if decision_tbl is None:
        decision_tbl = get_metric_phase4_decision_state(state, metric)
    cached = get_metric_phase4_cached_result(state, metric)
    expected = build_metric_phase4_signature(state, metric, decision_tbl)
    if cached["signature"] is None or not phase4_signature_matches(cached["signature"], expected):
        return False
    if not phase4_artifact_mode_satisfies(cached["artifact_mode"], artifact_mode):
        return False
    if cached["stratum_results"] is not None:
        sr = cached["stratum_results"]
        return len(sr) > 0 and all(
            (e or {}).get("reference_curve") is not None
            and ((e or {}).get("reference_curve") or {}).get("curve_row") is not None
            for e in sr.values()
        )
    rc = cached["reference_curve"]
    return rc is not None and (rc or {}).get("curve_row") is not None


def cache_metric_phase4_results(
    state: AppState,
    metric: str,
    decision_tbl=None,
    reference_curve=None,
    stratum_results=None,
    artifact_mode: str = "full",
):
    if decision_tbl is None:
        decision_tbl = get_metric_phase4_decision_state(state, metric)
    ensure_metric_phase_cache(state, metric)
    signature = build_metric_phase4_signature(state, metric, decision_tbl)
    curve_rows = extract_metric_phase4_curve_rows(
        {"reference_curve": reference_curve, "stratum_results": stratum_results}
    )
    with reactive.isolate():
        data = state.data()
        is_current = state.current_metric() == metric
        stratum_map = dict(state.stratum_results() or {})
    _update_cache_entry(
        state,
        metric,
        strat_decision_user=decision_tbl,
        reference_curve=reference_curve,
        current_stratum_level=None,
        phase4_data=data,
        stratum_results=stratum_results,
        phase4_signature=signature,
        phase4_artifact_mode=artifact_mode,
        phase4_curve_rows=curve_rows,
    )
    stratum_map[metric] = stratum_results
    state.stratum_results.set(stratum_map)
    if is_current:
        state.reference_curve.set(reference_curve if stratum_results is None else None)
        state.current_stratum_level.set(None)
        state.phase4_data.set(data)
    return signature


def set_modal_progress_detail(progress, detail: str) -> None:
    if progress is not None and hasattr(progress, "set_detail"):
        progress.set_detail(detail)


def advance_modal_progress(progress, detail: str | None = None) -> None:
    if progress is not None and hasattr(progress, "advance"):
        progress.advance(detail)


def get_stratification_values(data, strat_key, strat_config) -> pd.Series:
    n = 0 if data is None else len(data)
    sc = (strat_config or {}).get(strat_key)
    if sc is None:
        return pd.Series([None] * n, dtype=object)
    col = sc.get("column_name")
    if col is not None and not _is_blank(col) and col in data.columns:
        return data[col].astype(object).where(data[col].notna(), None).astype(str).where(
            data[col].notna(), None
        )
    if sc.get("type") == "paired":
        primary, secondary = sc.get("primary"), sc.get("secondary")
        if primary not in data.columns or secondary not in data.columns:
            return pd.Series([None] * n, dtype=object)
        pv = data[primary]
        sv = data[secondary]
        out = (pv.astype(str) + " | " + sv.astype(str)).astype(object)
        out[pv.isna() | sv.isna()] = None
        return out
    return pd.Series([None] * n, dtype=object)


def count_metric_phase4_preload_steps(
    state: AppState, metric: str, artifact_mode: str = "full"
) -> int:
    decision_tbl = get_metric_phase4_decision_state(state, metric)
    if metric_has_phase4_cache(state, metric, decision_tbl, artifact_mode=artifact_mode):
        return 0
    if (
        decision_tbl is not None
        and len(decision_tbl) > 0
        and decision_tbl["decision_type"].iloc[0] == "single"
        and not _is_blank(decision_tbl["selected_strat"].iloc[0])
    ):
        with reactive.isolate():
            data = state.data()
            strat_config = state.strat_config() or {}
        values = get_stratification_values(data, decision_tbl["selected_strat"].iloc[0], strat_config)
        levels = sorted({v for v in values if v is not None})
        return max(len(levels), 1) + 1
    return 2


def preload_metric_phase4_workspace(
    state: AppState, metric: str, progress=None, artifact_mode: str = "full"
) -> bool:
    decision_tbl = get_metric_phase4_decision_state(state, metric)
    sync_metric_decision_state(state, metric, decision_tbl)
    with reactive.isolate():
        data = state.data()
        metric_config = state.metric_config() or {}
        strat_config = state.strat_config() or {}
    state.phase4_data.set(data)
    state.current_stratum_level.set(None)
    expected = build_metric_phase4_signature(state, metric, decision_tbl)
    cached = get_metric_phase4_cached_result(state, metric)

    if (
        cached["signature"] is not None
        and phase4_signature_matches(cached["signature"], expected)
        and phase4_artifact_mode_satisfies(cached["artifact_mode"], artifact_mode)
        and len(extract_metric_phase4_curve_rows(cached)) > 0
    ):
        if cached["stratum_results"] is not None:
            hydrated = {
                lvl: {
                    "reference_curve": hydrate_reference_curve_result(
                        (entry or {}).get("reference_curve", entry),
                        data, metric, metric_config,
                        stratum_label=lvl, artifact_mode=artifact_mode,
                    )
                }
                for lvl, entry in cached["stratum_results"].items()
            }
            cache_metric_phase4_results(
                state, metric, decision_tbl=decision_tbl,
                stratum_results=hydrated, artifact_mode=artifact_mode,
            )
            state.reference_curve.set(None)
        elif cached["reference_curve"] is not None:
            hydrated_curve = hydrate_reference_curve_result(
                cached["reference_curve"], data, metric, metric_config,
                artifact_mode=artifact_mode,
            )
            state.reference_curve.set(hydrated_curve)
            cache_metric_phase4_results(
                state, metric, decision_tbl=decision_tbl,
                reference_curve=hydrated_curve, artifact_mode=artifact_mode,
            )
        else:
            state.reference_curve.set(None)
        return False

    if (
        decision_tbl is not None
        and len(decision_tbl) > 0
        and decision_tbl["decision_type"].iloc[0] == "single"
        and not _is_blank(decision_tbl["selected_strat"].iloc[0])
    ):
        strat_key = decision_tbl["selected_strat"].iloc[0]
        values = get_stratification_values(data, strat_key, strat_config)
        levels = sorted({v for v in values if v is not None})
        stratum_results: dict[str, dict] = {}
        if not levels:
            set_modal_progress_detail(progress, "No strata available for curve generation.")
            advance_modal_progress(progress, "No strata available for curve generation.")
            cache_metric_phase4_results(
                state, metric, decision_tbl=decision_tbl,
                stratum_results={}, artifact_mode=artifact_mode,
            )
            advance_modal_progress(progress, "Prepared Phase 4 workspace.")
            return True
        for i, lvl in enumerate(levels, start=1):
            set_modal_progress_detail(progress, f"Building curve for {lvl}...")
            stratum_data = data[values == lvl]
            stratum_results[lvl] = {
                "reference_curve": build_reference_curve(
                    stratum_data, metric, metric_config,
                    stratum_label=lvl, build_plots=(artifact_mode == "full"),
                )
            }
            advance_modal_progress(progress, f"Built {lvl} curve ({i}/{len(levels)}).")
        set_modal_progress_detail(progress, "Caching stratified Phase 4 results...")
        cache_metric_phase4_results(
            state, metric, decision_tbl=decision_tbl,
            stratum_results=stratum_results, artifact_mode=artifact_mode,
        )
        advance_modal_progress(progress, "Prepared Phase 4 workspace.")
        return True

    set_modal_progress_detail(progress, "Building reference curve...")
    curve_result = build_reference_curve(
        data, metric, metric_config, build_plots=(artifact_mode == "full")
    )
    advance_modal_progress(progress, "Built reference curve.")
    set_modal_progress_detail(progress, "Caching Phase 4 results...")
    cache_metric_phase4_results(
        state, metric, decision_tbl=decision_tbl,
        reference_curve=curve_result, artifact_mode=artifact_mode,
    )
    advance_modal_progress(progress, "Prepared Phase 4 workspace.")
    return True


# --------------------------------------------------------------------------- #
# Phase 1 & 3 backfills (R:1125-1493)
# --------------------------------------------------------------------------- #


def get_metric_phase1_artifact_mode(state: AppState, metric: str) -> str:
    entry = (_iso(state, "metric_phase_cache") or {}).get(metric) or {}
    return entry.get("phase1_artifact_mode") or "full"


def get_metric_phase3_artifact_mode(state: AppState, metric: str) -> str:
    entry = (_iso(state, "metric_phase_cache") or {}).get(metric) or {}
    return entry.get("phase3_artifact_mode") or "full"


def _metric_phase1_screening(state: AppState, metric: str):
    with reactive.isolate():
        if state.current_metric() == metric and state.phase1_screening() is not None:
            return state.phase1_screening()
    return ((_iso(state, "metric_phase_cache") or {}).get(metric) or {}).get("phase1_screening")


def metric_needs_phase1_artifact_refresh(state: AppState, metric: str) -> bool:
    screening = _metric_phase1_screening(state, metric)
    return screening is not None and get_metric_phase1_artifact_mode(state, metric) == "summary"


def metric_needs_phase3_artifact_refresh(state: AppState, metric: str) -> bool:
    verification = (_iso(state, "phase3_verification") or {}).get(metric)
    return verification is not None and get_metric_phase3_artifact_mode(state, metric) == "summary"


def get_metric_phase1_display_state(state: AppState, metric: str):
    screening = _metric_phase1_screening(state, metric)
    with reactive.isolate():
        if state.current_metric() == metric and state.phase1_effect_sizes() is not None:
            effect_sizes = state.phase1_effect_sizes()
        else:
            effect_sizes = ((state.metric_phase_cache() or {}).get(metric) or {}).get(
                "phase1_effect_sizes"
            )
    if screening is None:
        return None
    return {
        "results": screening.get("results", pd.DataFrame()),
        "pairwise": screening.get("pairwise", pd.DataFrame()),
        "plots": screening.get("plots", {}),
        "plot_specs": screening.get("plot_specs", {}),
        "effect_sizes": effect_sizes if effect_sizes is not None else pd.DataFrame(),
    }


def count_metric_phase1_backfill_steps(state: AppState, metric: str, mode: str = "full") -> int:
    return len(get_metric_allowed_strats(state, metric)) + 1


def build_metric_phase1_backfill(
    state: AppState, metric: str, mode: str = "full", progress=None
) -> dict:
    allowed = get_metric_allowed_strats(state, metric)
    existing = (_iso(state, "phase1_candidates") or {}).get(metric)
    compute_pairwise = mode == "full"
    with reactive.isolate():
        data = state.data()
        metric_config = state.metric_config() or {}
        strat_config = state.strat_config() or {}

    results_list: dict[str, dict] = {}
    for i, sk in enumerate(allowed, start=1):
        set_modal_progress_detail(
            progress, f"Running screening for {get_strat_display_name(state, sk)}..."
        )
        try:
            res = screen_stratification(
                data, metric, sk, metric_config, strat_config,
                compute_pairwise=compute_pairwise,
            )
        except Exception:  # noqa: BLE001 — R tryCatch parity
            res = None
        if res is not None:
            results_list[sk] = res
        advance_modal_progress(
            progress,
            f"Screened {get_strat_display_name(state, sk)} ({i}/{len(allowed)}).",
        )

    result_frames = [r["result_row"] for r in results_list.values()]
    result_rows = (
        pd.concat(result_frames, ignore_index=True) if result_frames else pd.DataFrame()
    )
    if compute_pairwise:
        pw_frames = [
            r["pairwise_df"]
            for r in results_list.values()
            if r.get("pairwise_df") is not None and len(r["pairwise_df"]) > 0
        ]
        pairwise_rows = pd.concat(pw_frames, ignore_index=True) if pw_frames else pd.DataFrame()
    else:
        pairwise_rows = pd.DataFrame()
    plot_specs = {
        sk: r["plot_spec"] for sk, r in results_list.items() if r.get("plot_spec") is not None
    }

    tested = (
        result_rows["stratification"].astype(str).unique().tolist() if len(result_rows) else []
    )
    set_modal_progress_detail(progress, "Computing effect sizes and candidate defaults...")
    if tested:
        try:
            effect_sizes = compute_effect_sizes(data, metric, tested, metric_config, strat_config)
        except Exception:  # noqa: BLE001
            effect_sizes = pd.DataFrame()
    else:
        effect_sizes = pd.DataFrame()

    candidates = build_metric_phase1_candidate_table_from_sources(
        metric=metric, allowed=allowed, existing=existing,
        l1=result_rows, l2=effect_sizes, include_all_allowed=True,
    )
    advance_modal_progress(progress, "Prepared Phase 1 screening details.")

    return {
        "screening": {
            "results": result_rows,
            "pairwise": pairwise_rows,
            "plots": {},
            "plot_specs": plot_specs,
        },
        "effect_sizes": effect_sizes,
        "candidates": candidates,
        "artifact_mode": mode,
    }


def commit_metric_phase1_backfill(state: AppState, metric: str, backfill: dict) -> dict:
    ensure_metric_phase_cache(state, metric)
    with reactive.isolate():
        l1 = dict(state.all_layer1_results() or {})
        l2 = dict(state.all_layer2_results() or {})
        cands = dict(state.phase1_candidates() or {})
        is_current = state.current_metric() == metric

    if len(backfill["screening"]["results"]) > 0:
        l1[metric] = backfill["screening"]["results"]
    else:
        l1.pop(metric, None)
    if backfill["effect_sizes"] is not None and len(backfill["effect_sizes"]) > 0:
        l2[metric] = backfill["effect_sizes"]
    else:
        l2.pop(metric, None)
    cands[metric] = backfill["candidates"]

    state.all_layer1_results.set(l1)
    state.all_layer2_results.set(l2)
    state.phase1_candidates.set(cands)
    _update_cache_entry(
        state, metric,
        phase1_screening=backfill["screening"],
        phase1_effect_sizes=backfill["effect_sizes"],
        phase1_artifact_mode=backfill.get("artifact_mode") or "full",
    )
    if is_current:
        state.phase1_screening.set(backfill["screening"])
        state.phase1_effect_sizes.set(backfill["effect_sizes"])
    return backfill


def ensure_metric_phase1_artifacts(state: AppState, metric: str, progress=None) -> bool:
    if not metric_needs_phase1_artifact_refresh(state, metric):
        return False
    backfill = build_metric_phase1_backfill(state, metric, mode="full", progress=progress)
    commit_metric_phase1_backfill(state, metric, backfill)
    return True


def get_metric_phase3_display_state(state: AppState, metric: str):
    verified = (_iso(state, "phase3_verification") or {}).get(metric)
    if verified is None:
        return None
    cache_entry = (_iso(state, "metric_phase_cache") or {}).get(metric) or {}
    with reactive.isolate():
        is_current = state.current_metric() == metric
        live_patterns = state.phase3_patterns() if is_current else None
        live_feas = state.phase3_feasibility() if is_current else None

    patterns = _first_not_none(
        verified.get("pattern_results"), cache_entry.get("phase3_patterns"), live_patterns
    )
    feasibility = _first_not_none(
        verified.get("feasibility_results"), cache_entry.get("phase3_feasibility"), live_feas
    )

    strats = list(
        dict.fromkeys(
            list(verified.get("finalists") or [])
            + list((verified.get("verification_status") or {}).keys())
        )
    )
    current_finalists = get_metric_phase3_choices(state, metric)
    if current_finalists:
        strats = [s for s in current_finalists if s in (strats or current_finalists)]
        if not strats:
            strats = current_finalists

    if not strats and patterns is None and feasibility is None:
        return None
    return {
        "strats": strats,
        "patterns": patterns if patterns is not None else {"results": pd.DataFrame(), "plots": {}},
        "feasibility": feasibility if feasibility is not None else pd.DataFrame(),
    }


def count_metric_phase3_backfill_steps(state: AppState, metric: str, mode: str = "full") -> int:
    finalists = get_metric_phase3_choices(state, metric)
    if mode != "full":
        return 1
    if not finalists:
        return 1
    return len(finalists) + 2


def build_metric_phase3_backfill(
    state: AppState, metric: str, mode: str = "full", progress=None
) -> dict:
    finalists = get_metric_phase3_choices(state, metric)
    existing = (_iso(state, "phase3_verification") or {}).get(metric) or {}
    normalized_selected = get_metric_curve_stratification(state, metric)

    verification_status = dict(existing.get("verification_status") or {})
    if finalists:
        verification_status = {
            sk: verification_status.get(sk, "verified") for sk in finalists
        }
    else:
        verification_status = {}

    build_full = mode == "full"
    pattern_results = {"results": pd.DataFrame(), "plots": {}} if build_full else None
    feasibility_results = pd.DataFrame() if build_full else None

    with reactive.isolate():
        data = state.data()
        metric_config = state.metric_config() or {}
        strat_config = state.strat_config() or {}
        predictor_config = state.predictor_config() or {}

    if finalists and build_full:
        predictor_keys = (metric_config.get(metric) or {}).get("allowed_predictors") or []
        all_results = []
        targets = ["none"] + finalists
        for i, sk in enumerate(targets, start=1):
            sk_actual = None if sk == "none" else sk
            label = "(Unstratified baseline)" if sk == "none" else get_strat_display_name(state, sk)
            set_modal_progress_detail(progress, f"Assessing pattern stability for {label}...")
            try:
                res = assess_pattern_stability(
                    data, metric, sk_actual, predictor_keys,
                    metric_config, strat_config, predictor_config,
                )
            except Exception:  # noqa: BLE001
                res = pd.DataFrame()
            if res is not None and len(res) > 0:
                all_results.append(res)
            advance_modal_progress(
                progress, f"Checked pattern stability for {label} ({i}/{len(targets)})."
            )
        pattern_results = {
            "results": pd.concat(all_results, ignore_index=True) if all_results else pd.DataFrame(),
            "plots": {},
        }
        set_modal_progress_detail(progress, "Assessing feasibility...")
        try:
            feasibility_results = assess_feasibility(data, finalists, strat_config)
        except Exception:  # noqa: BLE001
            feasibility_results = pd.DataFrame()
        advance_modal_progress(progress, "Prepared Phase 3 verification details.")
    else:
        advance_modal_progress(progress, "Prepared Phase 3 verification details.")

    decision_tbl = build_metric_strat_decision(state, metric, normalized_selected)
    verification = {
        "finalists": finalists,
        "pattern_results": pattern_results,
        "feasibility_results": feasibility_results,
        "verification_status": verification_status,
        "selected_strat": normalized_selected,
        "justification": existing.get("justification") or "Updated from Summary page",
    }
    return {
        "verification": verification,
        "phase3_patterns": pattern_results,
        "phase3_feasibility": feasibility_results,
        "decision_tbl": decision_tbl,
        "artifact_mode": mode,
    }


def commit_metric_phase3_backfill(state: AppState, metric: str, backfill: dict) -> dict:
    ensure_metric_phase_cache(state, metric)
    with reactive.isolate():
        verification = dict(state.phase3_verification() or {})
        is_current = state.current_metric() == metric
    verification[metric] = backfill["verification"]
    state.phase3_verification.set(verification)
    _update_cache_entry(
        state, metric,
        phase3_patterns=backfill["phase3_patterns"],
        phase3_feasibility=backfill["phase3_feasibility"],
        phase3_artifact_mode=backfill.get("artifact_mode") or "full",
    )
    sync_metric_decision_state(state, metric, backfill["decision_tbl"])
    if is_current:
        state.phase3_patterns.set(backfill["phase3_patterns"])
        state.phase3_feasibility.set(backfill["phase3_feasibility"])
    return backfill


def ensure_metric_phase3_artifacts(state: AppState, metric: str, progress=None) -> bool:
    if not metric_needs_phase3_artifact_refresh(state, metric):
        return False
    backfill = build_metric_phase3_backfill(state, metric, mode="full", progress=progress)
    commit_metric_phase3_backfill(state, metric, backfill)
    return True


def ensure_metric_phase3_valid(state: AppState, metric: str) -> bool:
    stored = (_iso(state, "curve_stratification") or {}).get(metric)
    if stored is None or stored == "none":
        return False
    if stored in get_metric_allowed_strats(state, metric):
        return False
    with reactive.isolate():
        cs = dict(state.curve_stratification() or {})
    cs.pop(metric, None)
    state.curve_stratification.set(cs)
    sync_metric_decision_state(state, metric, build_metric_strat_decision(state, metric, "none"))
    clear_metric_phase4_results(state, metric)
    return True


def set_metric_available_strats(state: AppState, metric: str, selected) -> list[str]:
    current_allowed = get_metric_allowed_strats(state, metric)
    base_allowed = get_metric_config_allowed_strats(state, metric)
    all_choices = get_summary_available_choices(state)
    selected_set = set(selected or [])
    new_allowed = [s for s in all_choices if s in selected_set]

    old_curve_choice = get_metric_curve_stratification(state, metric)
    old_recommendation = get_metric_curve_strat_recommendation(state, metric)

    with reactive.isolate():
        overrides = dict(state.summary_available_overrides() or {})
    if sorted(new_allowed) == sorted(base_allowed):
        overrides.pop(metric, None)
    else:
        overrides[metric] = new_allowed
    state.summary_available_overrides.set(overrides)

    removed = [s for s in current_allowed if s not in new_allowed]
    invalidated = ensure_metric_phase3_valid(state, metric)
    new_recommendation = get_metric_curve_strat_recommendation(state, metric)

    exploratory_notes = (
        [
            {
                "level": "warning",
                "text": "Unavailable stratifications were removed from this metric: "
                + format_strat_list(state, removed),
            }
        ]
        if removed
        else []
    )
    cross_metric_notes = (
        [
            {
                "level": "warning",
                "text": "The recommended curve stratification changed to "
                + get_metric_curve_strat_label(state, metric, new_recommendation)
                + " after the available stratifications were updated.",
            }
        ]
        if old_recommendation != new_recommendation
        else []
    )
    reference_notes = (
        [
            {
                "level": "warning",
                "text": "The previous curve stratification ("
                + get_metric_curve_strat_label(state, metric, old_curve_choice)
                + ") is no longer available. Phase 4 outputs were cleared.",
            }
        ]
        if invalidated and old_curve_choice != "none"
        else []
    )
    set_metric_summary_edit_notes(state, metric, "Exploratory", exploratory_notes)
    set_metric_summary_edit_notes(state, metric, "Cross-Metric Analysis", cross_metric_notes)
    set_metric_summary_edit_notes(state, metric, "Verification", [])
    set_metric_summary_edit_notes(state, metric, "Reference Curves", reference_notes)
    return new_allowed


# --------------------------------------------------------------------------- #
# Recompute machinery (R:1656-1892)
# --------------------------------------------------------------------------- #


def get_metric_curve_rows(state: AppState, metric: str) -> pd.DataFrame:
    return get_metric_phase4_display_state(state, metric)["curve_rows"]


def manual_curve_info_from_rows(curve_rows, metric: str, display_name: str | None = None) -> dict:
    display_name = display_name or metric
    curve_rows = pd.DataFrame(curve_rows) if curve_rows is not None else pd.DataFrame()
    if len(curve_rows) == 0 or "curve_source" not in curve_rows.columns:
        return {
            "metric": metric,
            "display_name": display_name,
            "has_manual_curve": False,
            "manual_curve_count": 0,
            "manual_strata": [],
            "summary_label": None,
            "selection_label": f"{display_name} (manual curve)",
        }
    manual_rows = curve_rows[curve_rows["curve_source"].astype(str) == "manual"]
    manual_strata: list[str] = []
    if "stratum" in manual_rows.columns and len(manual_rows) > 0:
        vals = [str(v) for v in manual_rows["stratum"] if not _is_blank(v)]
        manual_strata = sorted(set(vals))
    has_manual = len(manual_rows) > 0
    if not has_manual:
        summary_label = None
        selection_label = f"{display_name} (manual curve)"
    elif manual_strata:
        summary_label = "Manual strata: " + ", ".join(manual_strata)
        selection_label = f"{display_name} (manual strata: {', '.join(manual_strata)})"
    elif len(manual_rows) > 1:
        summary_label = "Manual curves"
        selection_label = f"{display_name} (manual curves)"
    else:
        summary_label = "Manual curve"
        selection_label = f"{display_name} (manual curve)"
    return {
        "metric": metric,
        "display_name": display_name,
        "has_manual_curve": has_manual,
        "manual_curve_count": int(len(manual_rows)),
        "manual_strata": manual_strata,
        "summary_label": summary_label,
        "selection_label": selection_label,
    }


def get_metric_phase4_manual_curve_info(state: AppState, metric: str, phase4=None) -> dict:
    if phase4 is None:
        phase4 = get_metric_phase4_display_state(state, metric)
    display_name = ((_iso(state, "metric_config") or {}).get(metric) or {}).get(
        "display_name"
    ) or metric
    return manual_curve_info_from_rows(phase4.get("curve_rows"), metric, display_name)


def build_summary_recompute_plan(state: AppState, metrics) -> dict:
    with reactive.isolate():
        metric_config = state.metric_config() or {}
    eligible = set(eligible_summary_metrics(metric_config))
    metrics = [m for m in (metrics or []) if m in eligible]
    if not metrics:
        return {"auto_metrics": [], "manual_metrics": [], "manual_info": pd.DataFrame()}
    rows = []
    for metric in metrics:
        info = get_metric_phase4_manual_curve_info(state, metric)
        rows.append(
            {
                "metric": info["metric"],
                "display_name": info["display_name"],
                "has_manual_curve": bool(info["has_manual_curve"]),
                "manual_curve_count": int(info["manual_curve_count"]),
                "manual_strata": info["manual_strata"],
                "summary_label": info["summary_label"],
                "selection_label": info["selection_label"],
            }
        )
    info_rows = pd.DataFrame(rows)
    manual_info = info_rows[info_rows["has_manual_curve"]]
    return {
        "auto_metrics": [m for m in metrics if m not in set(manual_info["metric"])],
        "manual_metrics": manual_info["metric"].tolist(),
        "manual_info": manual_info,
    }


def resolve_summary_recompute_metrics(auto_metrics, manual_metrics, selected_manual_metrics=None):
    auto_metrics = list(auto_metrics or [])
    manual_metrics = set(manual_metrics or [])
    selected = [m for m in (selected_manual_metrics or []) if m in manual_metrics]
    return list(dict.fromkeys(auto_metrics + selected))


def metric_has_official_curve(state: AppState, metric: str) -> bool:
    return len(get_metric_curve_rows(state, metric)) > 0


def recompute_metric_phase4(state: AppState, metric: str, artifact_mode: str = "full"):
    selection = get_metric_curve_stratification(state, metric)
    decision_tbl = build_metric_strat_decision(state, metric, selection)
    sync_metric_decision_state(state, metric, decision_tbl)
    with reactive.isolate():
        data = state.data()
        metric_config = state.metric_config() or {}
        strat_config = state.strat_config() or {}

    if decision_tbl["decision_type"].iloc[0] == "single" and not _is_blank(
        decision_tbl["selected_strat"].iloc[0]
    ):
        strat_key = decision_tbl["selected_strat"].iloc[0]
        values = get_stratification_values(data, strat_key, strat_config)
        levels = sorted({v for v in values if v is not None})
        stratum_results = {
            lvl: {
                "reference_curve": build_reference_curve(
                    data[values == lvl], metric, metric_config,
                    stratum_label=lvl, build_plots=(artifact_mode == "full"),
                )
            }
            for lvl in levels
        }
        curve_rows = extract_metric_phase4_curve_rows({"stratum_results": stratum_results})
        clear_metric_phase4_results(state, metric)
        signature = cache_metric_phase4_results(
            state, metric, decision_tbl=decision_tbl,
            stratum_results=stratum_results, artifact_mode=artifact_mode,
        )
        return update_metric_phase4_completed_entry(
            state, metric,
            {
                "stratified": True,
                "strat_var": strat_key,
                "strat_decision": decision_tbl,
                "stratum_results": stratum_results,
                "phase4_signature": signature,
                "phase4_artifact_mode": artifact_mode,
                "phase4_curve_rows": curve_rows,
            },
        )

    curve_result = build_reference_curve(
        data, metric, metric_config, build_plots=(artifact_mode == "full")
    )
    curve_rows = extract_metric_phase4_curve_rows({"reference_curve": curve_result})
    clear_metric_phase4_results(state, metric)
    signature = cache_metric_phase4_results(
        state, metric, decision_tbl=decision_tbl,
        reference_curve=curve_result, artifact_mode=artifact_mode,
    )
    return update_metric_phase4_completed_entry(
        state, metric,
        {
            "strat_decision": decision_tbl,
            "reference_curve": curve_result,
            "phase4_signature": signature,
            "phase4_artifact_mode": artifact_mode,
            "phase4_curve_rows": curve_rows,
        },
    )


def recompute_metric_from_summary(
    state: AppState, metric: str, refresh_phase2: bool = True,
    mode: str = "summary", progress_cb: Callable | None = None,
):
    def cb(phase, m, i, n, stage):
        if progress_cb is not None:
            progress_cb(phase, m, i, n, stage)

    cb("phase1", metric, 1, 1, "start")
    backfill1 = build_metric_phase1_backfill(state, metric, mode=mode)
    commit_metric_phase1_backfill(state, metric, backfill1)
    cb("phase1", metric, 1, 1, "end")

    if refresh_phase2:
        cb("phase2", None, 1, 1, "start")
        recompute_phase2_shared(state)
        with reactive.isolate():
            metric_config = state.metric_config() or {}
        for mk in eligible_summary_metrics(metric_config):
            ensure_metric_phase3_valid(state, mk)
        cb("phase2", None, 1, 1, "end")

    cb("phase3", metric, 1, 1, "start")
    backfill3 = build_metric_phase3_backfill(state, metric, mode=mode)
    commit_metric_phase3_backfill(state, metric, backfill3)
    cb("phase3", metric, 1, 1, "end")

    cb("phase4", metric, 1, 1, "start")
    recompute_metric_phase4(
        state, metric, artifact_mode="summary" if mode == "summary" else "full"
    )
    cb("phase4", metric, 1, 1, "end")

    with reactive.isolate():
        completed = (state.completed_metrics() or {}).get(metric)
    return {"phase1": backfill1, "phase3": backfill3, "phase4": completed}


def recompute_steps_from_summary(state: AppState, metrics, mode: str = "summary"):
    """Ordered ``(phase, metric, index, total, run)`` steps equivalent to
    ``recompute_metrics_from_summary``: phase1 per metric, the shared phase2
    once, phase3 per metric, phase4 per metric (3N + 1 steps). Each ``run()``
    reads state at call time, so a driver can execute the list synchronously
    (the plural function below, used by curve_automation) or asynchronously
    with awaits between steps (the summary page's bulk runner, which is what
    lets the progress bar advance live instead of flushing in one burst)."""
    with reactive.isolate():
        metric_config = state.metric_config() or {}
    eligible = set(eligible_summary_metrics(metric_config))
    metrics = [m for m in (metrics or []) if m in eligible]
    if not metrics:
        return []
    n = len(metrics)
    steps: list[tuple] = []
    for i, metric in enumerate(metrics, start=1):
        steps.append(("phase1", metric, i, n, lambda m=metric:
                      commit_metric_phase1_backfill(
                          state, m, build_metric_phase1_backfill(state, m, mode=mode))))

    def _phase2():
        recompute_phase2_shared(state)
        for mk in eligible_summary_metrics(metric_config):
            ensure_metric_phase3_valid(state, mk)

    steps.append(("phase2", None, 1, 1, _phase2))
    for i, metric in enumerate(metrics, start=1):
        steps.append(("phase3", metric, i, n, lambda m=metric:
                      commit_metric_phase3_backfill(
                          state, m, build_metric_phase3_backfill(state, m, mode=mode))))
    for i, metric in enumerate(metrics, start=1):
        steps.append(("phase4", metric, i, n, lambda m=metric:
                      recompute_metric_phase4(
                          state, m,
                          artifact_mode="summary" if mode == "summary" else "full")))
    return steps


def recompute_metrics_from_summary(
    state: AppState, metrics, mode: str = "summary",
    progress_cb: Callable | None = None, on_metric_done: Callable | None = None,
):
    """Synchronous driver over recompute_steps_from_summary. The callback
    protocol is pinned by curve_automation.run_curve_automation: progress_cb
    fires start/end around every step, and on_metric_done fires after each
    phase4 build, before that step's end callback."""
    for phase, metric, i, n, run in recompute_steps_from_summary(state, metrics, mode=mode):
        if progress_cb is not None:
            progress_cb(phase, metric, i, n, "start")
        run()
        if phase == "phase4" and on_metric_done is not None:
            on_metric_done(metric)
        if progress_cb is not None:
            progress_cb(phase, metric, i, n, "end")


# --------------------------------------------------------------------------- #
# Notes + status + snapshot (R:1894-2153)
# --------------------------------------------------------------------------- #


def format_summary_number(x, digits: int = 2) -> str:
    if x is None:
        return "N/A"
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return "N/A"
    if math.isnan(xf):
        return "N/A"
    return f"{round(xf, digits):.{digits}f}"


def format_summary_range(min_val, max_val, digits: int = 2) -> str:
    try:
        if any(v is None or (isinstance(float(v), float) and math.isnan(float(v))) for v in (min_val, max_val)):
            return "N/A"
    except (TypeError, ValueError):
        return "N/A"
    return f"{format_summary_number(min_val, digits)} - {format_summary_number(max_val, digits)}"


def build_summary_snapshot_context(state: AppState) -> dict:
    available_choices = get_summary_available_choices(state)
    return {
        "data_fingerprint": _iso(state, "data_fingerprint"),
        "config_version": _iso(state, "config_version") or 0,
        "available_choices": available_choices,
        "strat_label_map": {sk: get_strat_display_name(state, sk) for sk in available_choices},
        # Computed once per refresh; every summary row would otherwise
        # re-materialize every stratification column to ask the same question.
        "usable_strats": usable_strat_keys(state),
    }


def context_usable_strats(context) -> list[str] | None:
    """The context's usable-stratification list, or None to compute on demand."""
    return None if context is None else (context.get("usable_strats") or [])


def build_metric_summary_snapshot_signature(
    state: AppState, metric: str, context=None, phase4=None, phase3_state=None
) -> dict:
    """R:1935-1972 — cheap change-detection signature for a summary row."""
    if context is None:
        context = build_summary_snapshot_context(state)
    if phase4 is None:
        phase4 = get_metric_phase4_display_state(state, metric)
    if phase3_state is None:
        phase3_state = get_metric_phase3_display_state(state, metric)
    precheck = get_metric_precheck_row(state, metric)
    p1_screening = (_iso(state, "all_layer1_results") or {}).get(metric)
    phase3_flagged: list[str] = []
    feas = (phase3_state or {}).get("feasibility") if phase3_state else None
    if feas is not None and len(feas) > 0:
        phase3_flagged = sorted(
            feas.loc[feas["feasibility_flag"] != "feasible", "stratification"].astype(str)
        )
    curve_rows = phase4.get("curve_rows")
    curve_rows = pd.DataFrame() if curve_rows is None else pd.DataFrame(curve_rows)
    edit_notes = (_iso(state, "summary_edit_notes") or {}).get(metric) or {}
    return {
        "metric": metric,
        "data_fingerprint": context.get("data_fingerprint"),
        "config_version": context.get("config_version"),
        "n_obs": get_first_value(precheck, "n_obs", "N/A"),
        "available_selected": tuple(sorted(get_metric_allowed_strats(state, metric))),
        "available_choices": tuple(context.get("available_choices") or []),
        "curve_strat_used": get_metric_curve_stratification(state, metric),
        "curve_strat_choices": tuple(get_metric_curve_strat_choices(state, metric).items()),
        "phase1_has_results": p1_screening is not None and len(p1_screening) > 0,
        "phase1_selected": tuple(sorted(get_metric_phase1_selected(state, metric))),
        "phase2_selected": tuple(sorted(get_metric_phase2_passed(state, metric))),
        "phase3_strats": tuple(sorted((phase3_state or {}).get("strats") or [])),
        # The three not-applicable cases and the two never-ran ones all leave the
        # tuples above empty, so without these a row keeps the previous session's
        # notes when one assessment is opened over another.
        "strat_reason": metric_strat_eligibility(
            state, metric, context_usable_strats(context))[1],
        "phase2_has_ranking": bool(
            (ranking := _iso(state, "phase2_ranking")) is not None
            and len(ranking) > 0
            and "tier" in ranking.columns
        ),
        "phase2_metric_included": metric in get_phase2_metric_choices(state),
        "phase3_has_entry": metric in (_iso(state, "phase3_verification") or {}),
        "phase3_flagged": tuple(phase3_flagged),
        "phase4_source": phase4.get("source") or "none",
        "phase4_artifact_mode": phase4.get("artifact_mode"),
        "curve_rows_repr": curve_rows.to_json(orient="split", default_handler=str)
        if len(curve_rows)
        else "",
        "edit_notes_repr": repr(edit_notes),
    }


def build_stratified_stats_table(curve_rows) -> pd.DataFrame:
    """R:2155-2181 — one column per stratum of descriptive stats."""
    stats = ["n", "Min", "Q25", "Median", "Q75", "Max", "IQR", "SD"]
    tbl = pd.DataFrame({"Statistic": stats})
    curve_rows = pd.DataFrame(curve_rows)
    for i in range(len(curve_rows)):
        row = curve_rows.iloc[i]
        lbl = row.get("stratum")
        if _is_blank(lbl):
            lbl = f"Curve {i + 1}"
        if row.get("curve_status") == "insufficient_data":
            tbl[str(lbl)] = ["N/A"] * len(stats)
        else:
            tbl[str(lbl)] = [
                str(row.get("n_reference")),
                format_summary_number(row.get("min_val")),
                format_summary_number(row.get("q25")),
                format_summary_number(row.get("median_val")),
                format_summary_number(row.get("q75")),
                format_summary_number(row.get("max_val")),
                format_summary_number(row.get("iqr")),
                format_summary_number(row.get("sd_val")),
            ]
    return tbl


def build_stratified_threshold_table(curve_rows) -> pd.DataFrame:
    """R:2183-2208 — per-stratum metric ranges for the three bands."""
    from streamcurves.curves import reference_curve_row_range_display

    tbl = pd.DataFrame(
        {
            "Category": ["Functioning", "Functioning-At-Risk", "Non-functioning"],
            "Score Range": ["0.70 - 1.00", "0.30 - 0.69", "0.00 - 0.29"],
        }
    )
    curve_rows = pd.DataFrame(curve_rows)
    for i in range(len(curve_rows)):
        row = curve_rows.iloc[[i]]
        lbl = row["stratum"].iloc[0] if "stratum" in row.columns else None
        if _is_blank(lbl):
            lbl = f"Curve {i + 1}"
        if row["curve_status"].iloc[0] == "insufficient_data":
            tbl[str(lbl)] = ["N/A"] * 3
        else:
            tbl[str(lbl)] = [
                reference_curve_row_range_display(row, "functioning"),
                reference_curve_row_range_display(row, "at_risk"),
                reference_curve_row_range_display(row, "not_functioning"),
            ]
    return tbl


def build_metric_notes(
    state: AppState, metric: str, phase4=None, context=None
) -> dict[str, list]:
    if phase4 is None:
        phase4 = get_metric_phase4_display_state(state, metric)
    notes = empty_summary_note_store()

    def add(step, level, text):
        notes[step].append({"level": level, "text": text})

    edit_notes = get_metric_summary_edit_notes(state, metric)
    for phase in notes:
        notes[phase] = notes[phase] + edit_notes.get(phase, [])

    # "warning" means something that should have happened did not, or is
    # misconfigured; "info" means a truthful, expected absence. The distinction
    # is load-bearing: metric_summary_status turns any warning into a
    # "Run (warnings)" badge, so treating a not-applicable metric as a warning
    # flagged every metric of every published assessment for review.
    allowed, strat_reason = metric_strat_eligibility(state, metric, context_usable_strats(context))
    p1_selected = get_metric_phase1_selected(state, metric)
    p1_screening = (_iso(state, "all_layer1_results") or {}).get(metric)

    if strat_reason == STRAT_NO_CONFIG:
        add("Exploratory", "info",
            "This project has no stratification variables, so there is nothing to screen.")
    elif strat_reason == STRAT_NONE_ALLOWED:
        add("Exploratory", "info",
            "No stratification is enabled for this metric, so exploratory screening "
            "does not apply.")
    elif strat_reason == STRAT_COLUMNS_ABSENT:
        add("Exploratory", "warning",
            "Stratifications are configured for this metric but their columns are not "
            "in the data.")
    elif p1_screening is None or len(p1_screening) == 0:
        add("Exploratory", "warning", "Exploratory screening has not been run for this metric.")
    else:
        add("Exploratory", "info",
            "Stratifications available for analysis: " + format_strat_list(state, allowed))
        if p1_selected:
            add("Exploratory", "info",
                "Automatic screening shortlist: " + format_strat_list(state, p1_selected))
        else:
            add("Exploratory", "warning",
                "No stratifications met the automatic exploratory shortlist criteria.")

    p2_selected = get_metric_phase2_passed(state, metric)
    ranking = _iso(state, "phase2_ranking")
    ranking_ready = ranking is not None and len(ranking) > 0 and "tier" in ranking.columns
    if p2_selected:
        add("Cross-Metric Analysis", "info",
            "Broad-use candidates in the current cross-metric analysis: "
            + format_strat_list(state, p2_selected))
    elif strat_reason != STRAT_OK:
        add("Cross-Metric Analysis", "info",
            "Cross-metric analysis needs at least one stratification for this metric. "
            "None are available.")
    elif not ranking_ready:
        add("Cross-Metric Analysis", "warning",
            "Cross-metric analysis has not been run for this project.")
    elif metric not in get_phase2_metric_choices(state):
        add("Cross-Metric Analysis", "warning",
            "This metric was not included in the cross-metric run because it has no "
            "exploratory screening results.")
    else:
        add("Cross-Metric Analysis", "info",
            "Cross-metric analysis ran. No stratification reached broad-use for this metric.")

    verification_state = get_metric_phase3_display_state(state, metric)
    if verification_state is None:
        if strat_reason != STRAT_OK:
            add("Verification", "info",
                "Verification needs a candidate stratification. None are available for "
                "this metric.")
        elif not get_metric_phase3_choices(state, metric):
            add("Verification", "info",
                "No candidate stratification reached verification for this metric.")
        else:
            add("Verification", "warning",
                "Verification diagnostics have not been run for this metric.")
    else:
        add("Verification", "info",
            f"Verification diagnostics available for {len(verification_state['strats'])} "
            "stratification(s).")
        feas = verification_state.get("feasibility")
        if feas is not None and len(feas) > 0:
            flagged = feas.loc[feas["feasibility_flag"] != "feasible", "stratification"].tolist()
            if flagged:
                add("Verification", "warning",
                    "Verification flagged feasibility concerns for: "
                    + format_strat_list(state, flagged))

    curve_rows = phase4.get("curve_rows")
    curve_rows = pd.DataFrame() if curve_rows is None else pd.DataFrame(curve_rows)
    curve_choice = get_metric_curve_stratification(state, metric)
    curve_recommendation = get_metric_curve_strat_recommendation(state, metric)

    add("Reference Curves", "info",
        "Stratification used for curves: "
        + get_metric_curve_strat_label(state, metric, curve_choice))
    add("Reference Curves", "info",
        "Current recommendation: "
        + get_metric_curve_strat_label(state, metric, curve_recommendation))

    if len(curve_rows) == 0:
        add("Reference Curves", "warning",
            "Reference curve outputs are not current. Recompute is required.")
    else:
        add("Reference Curves", "info",
            f"Reference curve outputs available for {len(curve_rows)} curve(s).")
        flags = sorted(set(curve_rows.loc[curve_rows["curve_status"] != "complete", "curve_status"]))
        if flags:
            add("Reference Curves", "warning",
                "Reference curve issues: " + ", ".join(flags))
        if curve_choice != "none":
            with reactive.isolate():
                data = state.data()
                strat_config = state.strat_config() or {}
                metric_config = state.metric_config() or {}
            values = get_stratification_values(data, curve_choice, strat_config)
            min_n = (metric_config.get(metric) or {}).get("min_sample_size") or 10
            counts = values.value_counts(dropna=True)
            sparse = [(lvl, int(n)) for lvl, n in sorted(counts.items()) if n < min_n]
            if sparse:
                sparse_text = ", ".join(f"{lvl} (n={n})" for lvl, n in sparse)
                add("Reference Curves", "warning",
                    f"Strata below minimum n={min_n}: {sparse_text}")
    return notes


def metric_summary_status(state: AppState, metric: str, phase4=None, context=None) -> dict:
    if phase4 is None:
        phase4 = get_metric_phase4_display_state(state, metric)
    notes = build_metric_notes(state, metric, phase4=phase4, context=context)
    has_warning = any(
        item.get("level") == "warning" for items in notes.values() for item in items
    )
    curve_rows = phase4.get("curve_rows")
    curve_rows = pd.DataFrame() if curve_rows is None else pd.DataFrame(curve_rows)

    if len(curve_rows) == 0:
        return {"label": "Incomplete", "badge": "fail", "summary_label": "Not Run",
                "summary_class": "summary-status-not-run", "notes": notes}
    if (curve_rows["curve_status"] != "complete").any():
        return {"label": "Failed", "badge": "fail", "summary_label": "Failed",
                "summary_class": "summary-status-failed", "notes": notes}
    if has_warning:
        return {"label": "Complete with Warnings", "badge": "caution",
                "summary_label": "Run (warnings)",
                "summary_class": "summary-status-run-warnings", "notes": notes}
    return {"label": "Complete", "badge": "pass", "summary_label": "Run",
            "summary_class": "summary-status-run", "notes": notes}


def build_metric_summary_snapshot(state: AppState, metric: str, context=None) -> dict:
    if context is None:
        context = build_summary_snapshot_context(state)
    with reactive.isolate():
        metric_config = state.metric_config() or {}
    mc = metric_config.get(metric) or {}
    precheck = get_metric_precheck_row(state, metric)
    allowed = get_metric_allowed_strats(state, metric)
    available_choices = context.get("available_choices") or get_summary_available_choices(state)
    curve_strat_used = get_metric_curve_stratification(state, metric)
    phase4 = get_metric_phase4_display_state(state, metric)
    status = metric_summary_status(state, metric, phase4=phase4, context=context)
    manual = get_metric_phase4_manual_curve_info(state, metric, phase4=phase4)
    return {
        "metric": metric,
        # the stored name is often the code itself; resolve against the metric
        # dictionary so older sessions read as names too
        "display_name": _resolved_display_name(mc, metric),
        "family": mc.get("metric_family") or "N/A",
        "units": mc.get("units") or metric_names.units_for(metric) or "N/A",
        "direction": metric_direction_label(metric_config, metric),
        "n_obs": get_first_value(precheck, "n_obs", "N/A"),
        "status": status,
        "notes": status["notes"],
        "available_selected": allowed,
        "available_choices": available_choices,
        "curve_strat_used": curve_strat_used,
        "curve_strat_recommended": get_metric_curve_strat_recommendation(state, metric),
        "curve_strat_choices": get_metric_curve_strat_choices(state, metric),
        "curve_rows": phase4["curve_rows"],
        "phase4": phase4,
        "phase4_source": phase4["source"],
        "phase4_artifact_mode": phase4["artifact_mode"],
        "strat_label_map": context.get("strat_label_map")
        or {sk: get_strat_display_name(state, sk) for sk in available_choices},
        "has_manual_curve": manual["has_manual_curve"],
        "manual_curve_count": manual["manual_curve_count"],
        "manual_curve_strata": manual["manual_strata"],
        "manual_curve_label": manual["summary_label"],
        "manual_curve_selection_label": manual["selection_label"],
    }


# --------------------------------------------------------------------------- #
# Summary-export context (Part B — ships with M6's summary_export).
# Port of app/helpers/summary_page.R:2210-2492.
# --------------------------------------------------------------------------- #


def flatten_summary_note_text(notes, level=None) -> list[str]:
    """Port of flatten_summary_note_text — "step: text" lines, level-filtered."""
    if not notes:
        return []
    out: list[str] = []
    for step, items in notes.items():
        for item in items or []:
            item_level = (item or {}).get("level")
            if level is not None and item_level != level:
                continue
            text = (item or {}).get("text") or ""
            if not text:
                continue
            out.append(f"{step}: {text}")
    # unique, order-preserving
    return list(dict.fromkeys(out))


def metric_curve_summary_label(snapshot: dict) -> str:
    """Port of metric_curve_summary_label — Q25-Q75 range, functioning range,
    or an "N stratified curves" count."""
    curve_rows = snapshot.get("curve_rows")
    if curve_rows is None or len(curve_rows) == 0:
        return "Not available"
    df = curve_rows if isinstance(curve_rows, pd.DataFrame) else pd.DataFrame(curve_rows)
    if len(df) == 1:
        row = df.iloc[[0]]
        q25 = row["q25"].iloc[0] if "q25" in row.columns else None
        q75 = row["q75"].iloc[0] if "q75" in row.columns else None
        if q25 is not None and q75 is not None and _finite_num(q25) and _finite_num(q75):
            return f"{format_summary_number(q25)} - {format_summary_number(q75)}"
        return reference_curve_row_range_display(row, "functioning")
    return f"{len(df)} stratified curves"


def _finite_num(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def metric_appendix_plot(state: AppState, metric: str, snapshot: dict):
    """Port of metric_appendix_plot — overlay plot for stratified curves, else
    the single reference-curve figure. Returns a plotnine plot or None."""
    from views.curve_plots import build_overlay_curve_plot, build_reference_curve_plot

    curve_rows = snapshot.get("curve_rows")
    if curve_rows is None or len(curve_rows) == 0:
        return None
    df = curve_rows if isinstance(curve_rows, pd.DataFrame) else pd.DataFrame(curve_rows)
    with reactive.isolate():
        metric_config = state.metric_config() or {}
    if len(df) > 1:
        return build_overlay_curve_plot(df, metric_config)
    row = df.iloc[[0]]
    hib = bool((metric_config.get(metric) or {}).get("higher_is_better"))
    points = reference_curve_points_from_row(row, hib)
    return build_reference_curve_plot(points, row, metric_config, metric)


def render_summary_plot_png(plot) -> bytes | None:
    """Render a plotnine plot to PNG bytes (the oh_export ``plot_png`` contract)."""
    if plot is None:
        return None
    try:
        from io import BytesIO

        buf = BytesIO()
        plot.save(buf, format="png", width=8, height=5, dpi=150, verbose=False)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning("appendix plot render failed: %s", e)
        return None


def build_metric_status_export_row(state: AppState, snapshot: dict) -> dict:
    """Port of build_metric_status_export_row (one metric_status row)."""
    warning_notes = flatten_summary_note_text(snapshot.get("notes"), level="warning")
    info_notes = flatten_summary_note_text(snapshot.get("notes"), level="info")
    status = snapshot.get("status") or {}
    badge = status.get("badge") or "unknown"
    metric = snapshot["metric"]
    curve_rows = snapshot.get("curve_rows")
    return {
        "metric": metric,
        "display_name": snapshot.get("display_name"),
        "family": snapshot.get("family"),
        "units": snapshot.get("units"),
        "direction": snapshot.get("direction"),
        "n_obs": snapshot.get("n_obs"),
        "status_label": status.get("label") or "Unknown",
        "status_badge": badge,
        "needs_review": badge != "pass" or bool(snapshot.get("has_manual_curve")),
        "available_stratifications": format_strat_list(
            state, snapshot.get("available_selected") or []
        ),
        "selected_curve_stratification": snapshot.get("curve_strat_used") or "none",
        "selected_curve_stratification_label": get_metric_curve_strat_label(
            state, metric, snapshot.get("curve_strat_used")
        ),
        "recommended_curve_stratification": snapshot.get("curve_strat_recommended")
        or "none",
        "recommended_curve_stratification_label": get_metric_curve_strat_label(
            state, metric, snapshot.get("curve_strat_recommended")
        ),
        "phase4_source": snapshot.get("phase4_source") or "none",
        "phase4_artifact_mode": snapshot.get("phase4_artifact_mode") or "none",
        "curve_count": 0 if curve_rows is None else len(curve_rows),
        "curve_summary": metric_curve_summary_label(snapshot),
        "has_manual_curve": bool(snapshot.get("has_manual_curve")),
        "manual_curve_count": int(snapshot.get("manual_curve_count") or 0),
        "manual_curve_label": snapshot.get("manual_curve_label"),
        "warning_count": len(warning_notes),
        "warning_summary": " | ".join(warning_notes) if warning_notes else None,
        "note_summary": " | ".join(warning_notes + info_notes)
        if (warning_notes or info_notes)
        else None,
    }


def build_metric_decision_export_row(state: AppState, snapshot: dict) -> pd.DataFrame:
    """Port of build_metric_decision_export_row (per-metric decision rows)."""
    decision_tbl = (snapshot.get("phase4") or {}).get("strat_decision")
    if decision_tbl is None or len(decision_tbl) == 0:
        decision_tbl = build_metric_strat_decision(
            state, snapshot["metric"], snapshot.get("curve_strat_used")
        )
    df = (
        decision_tbl.copy()
        if isinstance(decision_tbl, pd.DataFrame)
        else pd.DataFrame(decision_tbl)
    )
    if len(df) == 0:
        return df
    status = snapshot.get("status") or {}
    metric = snapshot["metric"]
    df["display_name"] = snapshot.get("display_name")
    df["selected_curve_stratification"] = snapshot.get("curve_strat_used") or "none"
    df["selected_curve_stratification_label"] = get_metric_curve_strat_label(
        state, metric, snapshot.get("curve_strat_used")
    )
    df["recommended_curve_stratification"] = (
        snapshot.get("curve_strat_recommended") or "none"
    )
    df["recommended_curve_stratification_label"] = get_metric_curve_strat_label(
        state, metric, snapshot.get("curve_strat_recommended")
    )
    df["phase4_source"] = snapshot.get("phase4_source") or "none"
    df["phase4_artifact_mode"] = snapshot.get("phase4_artifact_mode") or "none"
    df["status_label"] = status.get("label") or "Unknown"
    df["status_badge"] = status.get("badge") or "unknown"
    df["has_manual_curve"] = bool(snapshot.get("has_manual_curve"))
    df["manual_curve_label"] = snapshot.get("manual_curve_label")
    return df


def build_phase2_summary_export(state: AppState) -> pd.DataFrame:
    """Port of build_phase2_summary_export (phase-2 ranking + settings)."""
    with reactive.isolate():
        ranking = state.phase2_ranking()
        settings = state.phase2_settings() or empty_phase2_settings()
    if ranking is None or not isinstance(ranking, pd.DataFrame) or len(ranking) == 0:
        return pd.DataFrame()
    df = ranking.copy()
    if "stratification" in df.columns:
        df["stratification_label"] = [
            get_strat_display_name(state, sk) for sk in df["stratification"]
        ]
    df["sig_threshold"] = settings.get("sig_threshold")
    df["support_threshold"] = settings.get("support_threshold")
    return df


def _summary_export_mapping_ready(state: AppState) -> bool:
    """Exports unlock only when the mapping is confirmed AND valid AND covers
    every summary-eligible metric (R gating in build_summary_export_context)."""
    with reactive.isolate():
        confirmed = bool(state.discipline_function_mapping_confirmed())
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
    if not confirmed:
        return False
    try:
        validate_discipline_function_mapping(mapping)
        return function_mapping_full_coverage(
            mapping, eligible_summary_metrics(metric_config)
        )
    except Exception:  # noqa: BLE001
        return False


def build_summary_export_context(
    state: AppState, metrics=None, include_appendix_plots: bool = True
) -> dict:
    """Port of build_summary_export_context — assemble the export/report context
    consumed by oh_export, deep_export, and the science report.

    ``include_appendix_plots`` attaches a lazy ``plot_png`` callable per metric
    (rendered on demand by the SQT workbook / science report), avoiding R's
    temp-dir plot staging.
    """
    with reactive.isolate():
        metric_config = state.metric_config() or {}
        completed_metrics = state.completed_metrics() or {}
        decision_log = state.decision_log()
        mapping = state.discipline_function_mapping()

    eligible = eligible_summary_metrics(metric_config)
    metrics = [m for m in (metrics or eligible) if m in eligible]

    snap_ctx = build_summary_snapshot_context(state)
    snapshots = {m: build_metric_summary_snapshot(state, m, context=snap_ctx) for m in metrics}

    metric_status_rows = [
        build_metric_status_export_row(state, snap) for snap in snapshots.values()
    ]
    metric_status = pd.DataFrame(metric_status_rows)

    threshold_frames = []
    for snap in snapshots.values():
        curve_rows = snap.get("curve_rows")
        if curve_rows is None or len(curve_rows) == 0:
            continue
        threshold_frames.append(reference_curve_rows_for_export(curve_rows))
    threshold_rows = (
        pd.concat(threshold_frames, ignore_index=True) if threshold_frames else pd.DataFrame()
    )

    decision_frames = [
        build_metric_decision_export_row(state, snap) for snap in snapshots.values()
    ]
    decision_frames = [d for d in decision_frames if d is not None and len(d) > 0]
    current_decisions = (
        pd.concat(decision_frames, ignore_index=True) if decision_frames else pd.DataFrame()
    )

    decision_history = (
        decision_log.copy()
        if isinstance(decision_log, pd.DataFrame)
        else pd.DataFrame(decision_log or [])
    )

    regional_entries = {
        k: v for k, v in completed_metrics.items() if (v or {}).get("type") == "regional"
    }
    regional_frames = []
    for entry in regional_entries.values():
        ms = entry.get("model_summary")
        if ms is None or len(ms) == 0:
            continue
        df = ms.copy() if isinstance(ms, pd.DataFrame) else pd.DataFrame(ms)
        df["response"] = entry.get("response") or get_first_value(df, "response", None)
        df["predictor"] = entry.get("predictor") or get_first_value(df, "predictor", None)
        df["stratify"] = entry.get("stratify") or "None"
        regional_frames.append(df)
    regional_curves = (
        pd.concat(regional_frames, ignore_index=True) if regional_frames else pd.DataFrame()
    )

    phase2_summary = build_phase2_summary_export(state)

    metrics_info: dict[str, dict] = {}
    for metric, snap in snapshots.items():
        curve_rows = snap.get("curve_rows")
        has_curves = curve_rows is not None and len(curve_rows) > 0
        warning_notes = flatten_summary_note_text(snap.get("notes"), level="warning")
        status = snap.get("status") or {}
        info = {
            "metric": metric,
            "display_name": snap.get("display_name"),
            "family": snap.get("family"),
            "units": snap.get("units"),
            "direction": snap.get("direction"),
            "n_obs": snap.get("n_obs"),
            "status_label": status.get("label") or "Unknown",
            "status_badge": status.get("badge") or "unknown",
            "selected_curve_stratification_label": get_metric_curve_strat_label(
                state, metric, snap.get("curve_strat_used")
            ),
            "recommended_curve_stratification_label": get_metric_curve_strat_label(
                state, metric, snap.get("curve_strat_recommended")
            ),
            "curve_count": len(curve_rows) if has_curves else 0,
            "curve_summary": metric_curve_summary_label(snap),
            "has_manual_curve": bool(snap.get("has_manual_curve")),
            "warning_summary": " | ".join(warning_notes) if warning_notes else None,
            "notes": snap.get("notes"),
            "stats_table": build_stratified_stats_table(curve_rows)
            if has_curves
            else pd.DataFrame(),
            "threshold_table": build_stratified_threshold_table(curve_rows)
            if has_curves
            else pd.DataFrame(),
        }
        if include_appendix_plots:
            def _plot_png(state=state, metric=metric, snap=snap):
                return render_summary_plot_png(metric_appendix_plot(state, metric, snap))

            info["plot_png"] = _plot_png
        metrics_info[metric] = info

    session_meta = {
        "generated_at": None,  # stamped by the caller (Date.now() unavailable here)
        "metric_count": len(metrics),
        "complete_metrics": int((metric_status["status_label"] != "Incomplete").sum())
        if len(metric_status)
        else 0,
        "pending_metrics": int((metric_status["status_label"] == "Incomplete").sum())
        if len(metric_status)
        else 0,
        "review_metrics": int(metric_status["needs_review"].sum())
        if len(metric_status)
        else 0,
        "manual_curve_metrics": int(metric_status["has_manual_curve"].sum())
        if len(metric_status)
        else 0,
        "regional_curve_rows": len(regional_curves),
        "regional_curve_sets": len(regional_entries),
        "decision_history_entries": len(decision_history),
        "phase2_available": len(phase2_summary) > 0,
    }

    return {
        "session_meta": session_meta,
        "metric_status": metric_status,
        "threshold_rows": threshold_rows,
        "current_decisions": current_decisions,
        "decision_history": decision_history,
        "regional_curves": regional_curves,
        "phase2_summary": phase2_summary,
        "metrics": metrics_info,
        "discipline_function_mapping": mapping,
        "discipline_function_mapping_confirmed": _summary_export_mapping_ready(state),
    }

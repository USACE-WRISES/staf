"""AppState + phase tracking — port of the ``rv <- reactiveValues(...)`` block
(app/app.R:144-242) and app/helpers/phase_tracker.R.

One ``reactive.Value`` per rv field (same names), so R's per-field invalidation
granularity carries over: ``rv$phase1_screening`` -> ``state.phase1_screening()``.

Convention (see PORTING.md): NEVER mutate a held container in place — py-shiny
cannot see it. Copy-then-set, e.g.::

    cache = dict(state.metric_phase_cache());  cache[m] = snap
    state.metric_phase_cache.set(cache)
"""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass, field, fields as dc_fields

import pandas as pd
from shiny import reactive

from streamcurves import config as sc_config

logger = logging.getLogger("streamcurves")

# Serialization gate for reactive.flush() calls made from detached asyncio
# tasks. py-shiny's ReactiveEnvironment.flush has no re-entrancy guard and
# _flush_sequential's empty()/get() pair races when two flushers interleave —
# a lost context wedges the session (all outputs stuck "recalculating", CPU
# idle). Every task-side flush in this app must go through task_flush().
_task_flush_lock = asyncio.Lock()


async def task_flush() -> None:
    async with _task_flush_lock:
        await reactive.flush()

PHASE_LABELS = [
    "Exploratory",
    "Cross-Metric Analysis",
    "Verification",
    "Reference Curves",
]

## Per-phase state fields for save/restore across metric switches
## (phase_tracker.R:15-24)
PHASE_STATE_FIELDS = [
    "phase1_screening",
    "phase1_effect_sizes",
    "phase3_patterns",
    "phase3_feasibility",
    "strat_decision_user",
    "reference_curve",
    "current_stratum_level",
    "phase4_data",
]


def empty_phase2_settings() -> dict:
    """Port of summary_page.R:236-243."""
    return {
        "metric_filter": None,
        "strat_filter": None,
        "sig_threshold": 0.05,
        "support_threshold": 0.5,
    }


def deep_copy_value(x):
    """Port of phase_tracker.R deep_copy_value (unserialize(serialize(x)))."""
    if x is None:
        return None
    if isinstance(x, pd.DataFrame):
        return x.copy(deep=True)
    return copy.deepcopy(x)


def _rv(default=None):
    return field(default_factory=lambda: reactive.Value(default))


def _rv_factory(fn):
    return field(default_factory=lambda: reactive.Value(fn()))


@dataclass
class AppState:
    """The shared app state — field-for-field port of app.R's rv."""

    # ── data ────────────────────────────────────────────────────────────────
    data: reactive.Value = _rv()
    precheck_df: reactive.Value = _rv()

    data_source: reactive.Value = _rv()
    data_fingerprint: reactive.Value = _rv()
    upload_filename: reactive.Value = _rv()
    input_metadata: reactive.Value = _rv()
    site_mask_config: reactive.Value = _rv()

    # ── configs (populated by AppState.fresh) ───────────────────────────────
    metric_config: reactive.Value = _rv_factory(dict)
    strat_config: reactive.Value = _rv_factory(dict)
    predictor_config: reactive.Value = _rv_factory(dict)
    factor_recode_config: reactive.Value = _rv_factory(dict)
    output_config: reactive.Value = _rv_factory(dict)
    startup_metric_config: reactive.Value = _rv_factory(dict)
    startup_strat_config: reactive.Value = _rv_factory(dict)
    startup_predictor_config: reactive.Value = _rv_factory(dict)
    startup_factor_recode_config: reactive.Value = _rv_factory(dict)
    startup_output_config: reactive.Value = _rv_factory(dict)
    startup_config_version: reactive.Value = _rv(0)
    startup_current_metric: reactive.Value = _rv("perRiffle")
    config_version: reactive.Value = _rv(0)

    current_metric: reactive.Value = _rv("perRiffle")

    # ── phase 1 ─────────────────────────────────────────────────────────────
    phase1_screening: reactive.Value = _rv()
    phase1_effect_sizes: reactive.Value = _rv()
    phase1_candidates: reactive.Value = _rv_factory(dict)
    all_layer1_results: reactive.Value = _rv_factory(dict)
    all_layer2_results: reactive.Value = _rv_factory(dict)

    # ── phase 2 ─────────────────────────────────────────────────────────────
    phase2_ranking: reactive.Value = _rv()
    cross_metric_consistency: reactive.Value = _rv()
    phase2_settings: reactive.Value = _rv_factory(empty_phase2_settings)
    phase2_metric_overrides: reactive.Value = _rv_factory(dict)
    curve_stratification: reactive.Value = _rv_factory(dict)
    summary_available_overrides: reactive.Value = _rv_factory(dict)
    summary_edit_notes: reactive.Value = _rv_factory(dict)

    # ── phase 3 ─────────────────────────────────────────────────────────────
    phase3_patterns: reactive.Value = _rv()
    phase3_feasibility: reactive.Value = _rv()
    phase3_verification: reactive.Value = _rv_factory(dict)
    strat_decision_user: reactive.Value = _rv()

    # ── phase 4 ─────────────────────────────────────────────────────────────
    reference_curve: reactive.Value = _rv()
    phase4_data: reactive.Value = _rv()
    current_stratum_level: reactive.Value = _rv()
    stratum_results: reactive.Value = _rv_factory(dict)

    completed_metrics: reactive.Value = _rv_factory(dict)
    decision_log: reactive.Value = _rv_factory(pd.DataFrame)

    metric_phase_cache: reactive.Value = _rv_factory(dict)

    # ── STAF workbench mapping ──────────────────────────────────────────────
    discipline_function_mapping: reactive.Value = _rv()
    discipline_function_mapping_confirmed: reactive.Value = _rv(False)
    startup_discipline_function_mapping: reactive.Value = _rv()
    mapping_user_touched: reactive.Value = _rv(False)
    workbook_provided_mapping: reactive.Value = _rv(False)

    custom_groupings: reactive.Value = _rv_factory(dict)
    custom_grouping_counter: reactive.Value = _rv_factory(dict)

    session_name: reactive.Value = _rv()
    app_data_loaded: reactive.Value = _rv(False)
    app_reset_nonce: reactive.Value = _rv(0)

    # ── import wizard hand-off + cross-sections ─────────────────────────────
    import_inject_data: reactive.Value = _rv()
    import_inject_source: reactive.Value = _rv()
    import_inject_nonce: reactive.Value = _rv(0)
    cross_sections: reactive.Value = _rv_factory(dict)
    column_sources: reactive.Value = _rv_factory(dict)
    column_functions: reactive.Value = _rv_factory(dict)

    # Region of applicability chosen in the import wizard (persisted so it can
    # ride into the DEEP bundle meta + the assessment library). Shape:
    # {"kind": "ecoregion"|"state"|"polygon", "code", "name", "polygon"?}.
    region_of_applicability: reactive.Value = _rv()

    # ── EASI reference-condition screening (session schema v2) ──────────────
    # Candidate decisions from the vendored EASI batch engine, keyed by external
    # site id, persisted so screening survives save/reload. Every candidate is
    # preserved (passed and failed); only retained sites continue to enrichment.
    easi_screening_sites: reactive.Value = _rv()        # list[dict] / DataFrame
    easi_screening_metrics: reactive.Value = _rv()      # list[dict] / DataFrame
    easi_screening_criteria: reactive.Value = _rv()     # dict snapshot

    # ── staged-run state (streamcurves/run_state.py owns the shapes) ─────────
    # run_meta: {created, updated, method versions, region}; run_stage_status:
    # {stage_key: {status, detail}} snapshot for the stage banner; curve_review:
    # {metric: entry} flagged-review queue; screening_run: last direct-run
    # summary dict; site_exclusions: list[{site_id, reason, source, note}].
    run_meta: reactive.Value = _rv()
    run_stage_status: reactive.Value = _rv_factory(dict)
    curve_review: reactive.Value = _rv_factory(dict)
    screening_run: reactive.Value = _rv()
    site_exclusions: reactive.Value = _rv_factory(list)
    # Live extended tasks by stage key ({stage_key: True}); feeds
    # run_state.derive_stage_status(tasks_running=...) so the stage banner can
    # show a running stage. Transient by design: not in SESSION_FIELDS.
    tasks_running: reactive.Value = _rv_factory(dict)
    # Validation records (restricted): list[dict] of independent-check evidence
    # that a maintainer can attach before certifying a library version.
    validation_records: reactive.Value = _rv_factory(list)

    # ── root navigation requests (stage banner -> shell) ────────────────────
    # nav_request: a nav_panel value to switch main_navbar to; wizard_step_request:
    # a 1-based Data & Setup wizard step to jump to. Both are nonce-driven so the
    # same request can fire twice.
    nav_request: reactive.Value = _rv()
    nav_request_nonce: reactive.Value = _rv(0)
    wizard_step_request: reactive.Value = _rv()
    wizard_step_nonce: reactive.Value = _rv(0)
    # Wizard hydration: bumped when the Data & Setup wizard should seed its
    # local widgets from the saved region/screening state (stage-banner clicks
    # and header Open on restored sessions). Transient: not in SESSION_FIELDS.
    wizard_hydrate_nonce: reactive.Value = _rv(0)
    # Header "Open" asks Data & Setup to show the Open dialog (library picker
    # plus project-file upload). Transient: not in SESSION_FIELDS.
    open_dialog_nonce: reactive.Value = _rv(0)

    # Cross-tab session restore request: a view loads a session payload and asks
    # the Data & Setup tab to restore it (bump the nonce). The Open dialog now
    # calls the restore directly; this hook stays for out-of-module callers.
    session_restore_request: reactive.Value = _rv()
    session_restore_nonce: reactive.Value = _rv(0)

    # ── workspace modal state machine ───────────────────────────────────────
    workspace_modal_type: reactive.Value = _rv()
    workspace_modal_metric: reactive.Value = _rv()
    workspace_modal_stage: reactive.Value = _rv()
    workspace_modal_error: reactive.Value = _rv()
    workspace_modal_loading_detail: reactive.Value = _rv()
    workspace_modal_nonce: reactive.Value = _rv(0)
    workspace_modal_ready_nonce: reactive.Value = _rv(0)
    workspace_refresh_nonce: reactive.Value = _rv(0)
    analysis_tab_request_id: reactive.Value = _rv()
    analysis_tab_status: reactive.Value = _rv_factory(
        lambda: empty_analysis_tab_status("pending")
    )
    analysis_tab_status_nonce: reactive.Value = _rv(0)
    analysis_tab_preload_tab: reactive.Value = _rv()
    analysis_tab_preload_nonce: reactive.Value = _rv(0)
    analysis_tab_preload_completed_tab: reactive.Value = _rv()
    analysis_tab_preload_completed_status: reactive.Value = _rv()
    analysis_tab_preload_completed_nonce: reactive.Value = _rv(0)

    # ------------------------------------------------------------------ #

    @classmethod
    def fresh(cls) -> "AppState":
        """Construct per session with startup configs (global.R + app.R rv)."""
        state = cls()
        startup = sc_config.startup_configs()
        state.metric_config.set(dict(startup["metric_config"]))
        state.strat_config.set(dict(startup["strat_config"]))
        state.predictor_config.set(dict(startup["predictor_config"]))
        state.factor_recode_config.set(dict(startup["factor_recode_config"]))
        state.output_config.set(deep_copy_value(startup["output_config"]))
        state.startup_metric_config.set(deep_copy_value(startup["metric_config"]))
        state.startup_strat_config.set(deep_copy_value(startup["strat_config"]))
        state.startup_predictor_config.set(deep_copy_value(startup["predictor_config"]))
        state.startup_factor_recode_config.set(
            deep_copy_value(startup["factor_recode_config"])
        )
        state.startup_output_config.set(deep_copy_value(startup["output_config"]))
        return state

    def field_names(self) -> list[str]:
        return [f.name for f in dc_fields(self)]

    def get(self, name: str):
        return getattr(self, name)()

    def set(self, name: str, value) -> None:
        getattr(self, name).set(value)

    def isolate_get(self, name: str):
        with reactive.isolate():
            return getattr(self, name)()


# --------------------------------------------------------------------------- #
# Per-metric phase state save/restore (phase_tracker.R:74-142)
# --------------------------------------------------------------------------- #


def save_metric_phase_state(state: AppState, metric: str | None) -> None:
    if metric is None or metric == "":
        return
    with reactive.isolate():
        cache = dict(state.metric_phase_cache())
        snapshot = dict(cache.get(metric) or {})
        for f in PHASE_STATE_FIELDS:
            snapshot[f] = state.get(f)
        snapshot["config_version"] = state.config_version()
        snapshot["stratum_results"] = (state.stratum_results() or {}).get(metric)
        cache[metric] = snapshot
    state.metric_phase_cache.set(cache)


def restore_metric_phase_state(state: AppState, metric: str | None) -> None:
    with reactive.isolate():
        snapshot = (state.metric_phase_cache() or {}).get(metric)
        if snapshot is not None:
            for f in PHASE_STATE_FIELDS:
                state.set(f, snapshot.get(f))
            if snapshot.get("stratum_results") is not None:
                sr = dict(state.stratum_results() or {})
                sr[metric] = snapshot["stratum_results"]
                state.stratum_results.set(sr)
            snap_ver = snapshot.get("config_version")
            if snap_ver is not None and snap_ver != state.config_version():
                logger.warning("Config changed since %s's analysis was run", metric)
        else:
            for f in PHASE_STATE_FIELDS:
                state.set(f, None)


def save_stratum_state(state: AppState, metric: str | None, level: str | None) -> None:
    if metric is None or level is None:
        return
    with reactive.isolate():
        sr = dict(state.stratum_results() or {})
        per_metric = dict(sr.get(metric) or {})
        per_metric[level] = {"reference_curve": state.reference_curve()}
        sr[metric] = per_metric
    state.stratum_results.set(sr)


def restore_stratum_state(state: AppState, metric: str | None, level: str | None) -> None:
    with reactive.isolate():
        saved = ((state.stratum_results() or {}).get(metric) or {}).get(level)
    state.reference_curve.set(None if saved is None else saved.get("reference_curve"))


# --------------------------------------------------------------------------- #
# Resets (phase_tracker.R:147-221)
# --------------------------------------------------------------------------- #


def reset_all_analysis(state: AppState) -> None:
    with reactive.isolate():
        # Clean up custom groupings first
        groupings = state.custom_groupings() or {}
        if groupings:
            data = state.data()
            metric_config = deep_copy_value(state.metric_config() or {})
            strat_config = dict(state.strat_config() or {})
            for cg_key, cg in groupings.items():
                col = (cg or {}).get("column_name")
                if col is not None and data is not None and col in data.columns:
                    data = data.drop(columns=[col])
                strat_config.pop(cg_key, None)
                for mk, entry in metric_config.items():
                    allowed = entry.get("allowed_stratifications") or []
                    if cg_key in allowed:
                        entry["allowed_stratifications"] = [
                            s for s in allowed if s != cg_key
                        ]
            state.data.set(data)
            state.strat_config.set(strat_config)
            state.metric_config.set(metric_config)
            state.custom_groupings.set({})
            state.custom_grouping_counter.set({})

    for f in PHASE_STATE_FIELDS:
        state.set(f, None)

    state.metric_phase_cache.set({})
    state.completed_metrics.set({})
    state.all_layer1_results.set({})
    state.all_layer2_results.set({})
    state.phase1_candidates.set({})
    state.phase2_ranking.set(None)
    state.cross_metric_consistency.set(None)
    state.phase2_settings.set(empty_phase2_settings())
    state.phase2_metric_overrides.set({})
    state.curve_stratification.set({})
    state.summary_available_overrides.set({})
    state.summary_edit_notes.set({})
    state.phase3_verification.set({})
    state.decision_log.set(pd.DataFrame())
    state.stratum_results.set({})
    # Staged-run analysis outputs (curve classification + stage snapshot).
    state.curve_review.set({})
    state.run_stage_status.set({})
    state.tasks_running.set({})


def reset_app_to_startup(state: AppState) -> None:
    reset_all_analysis(state)
    with reactive.isolate():
        state.metric_config.set(deep_copy_value(state.startup_metric_config()))
        state.strat_config.set(deep_copy_value(state.startup_strat_config()))
        state.predictor_config.set(deep_copy_value(state.startup_predictor_config()))
        state.factor_recode_config.set(
            deep_copy_value(state.startup_factor_recode_config())
        )
        state.output_config.set(deep_copy_value(state.startup_output_config()))
        state.config_version.set(state.startup_config_version() or 0)

        state.data.set(None)
        state.precheck_df.set(None)
        state.input_metadata.set(None)
        state.site_mask_config.set(None)
        state.data_source.set(None)
        state.data_fingerprint.set(None)
        state.upload_filename.set(None)
        state.session_name.set(None)
        state.region_of_applicability.set(None)
        state.easi_screening_sites.set(None)
        state.easi_screening_metrics.set(None)
        state.easi_screening_criteria.set(None)
        state.run_meta.set(None)
        state.screening_run.set(None)
        state.site_exclusions.set([])
        state.validation_records.set([])
        state.current_metric.set(state.startup_current_metric() or "perRiffle")
        state.app_data_loaded.set(False)
        state.app_reset_nonce.set((state.app_reset_nonce() or 0) + 1)


def bump_config(state: AppState) -> None:
    with reactive.isolate():
        state.config_version.set((state.config_version() or 0) + 1)


# --------------------------------------------------------------------------- #
# Workspace modal launch + scope helpers (phase_tracker.R:224-273)
# --------------------------------------------------------------------------- #


def next_workspace_modal_request_id(state: AppState) -> int:
    with reactive.isolate():
        return (state.workspace_modal_nonce() or 0) + 1


def launch_workspace_modal(
    state: AppState, phase: str, metric: str | None = None, request_id: int | None = None
) -> int:
    next_request_id = (
        request_id if request_id is not None else next_workspace_modal_request_id(state)
    )

    if phase == "analysis":
        reset_analysis_tab_state(state, next_request_id, default="pending")

    with reactive.isolate():
        resolved_metric = metric if metric is not None else state.current_metric()
    state.workspace_modal_type.set(phase)
    state.workspace_modal_metric.set(resolved_metric)
    state.workspace_modal_stage.set("loading")
    state.workspace_modal_error.set(None)
    state.workspace_modal_loading_detail.set(None)
    state.workspace_modal_nonce.set(next_request_id)
    return next_request_id


def workspace_scope_is_active(
    state: AppState,
    workspace_scope: str = "standalone",
    standalone_modal_type: str | None = None,
    isolate_state: bool = False,
) -> bool:
    if workspace_scope not in ("standalone", "analysis"):
        raise ValueError(f"invalid workspace_scope {workspace_scope!r}")

    if isolate_state:
        with reactive.isolate():
            current = state.workspace_modal_type()
    else:
        current = state.workspace_modal_type()

    if workspace_scope == "analysis":
        return current == "analysis"
    return current == standalone_modal_type


def notify_workspace_refresh(state: AppState) -> None:
    with reactive.isolate():
        state.workspace_refresh_nonce.set((state.workspace_refresh_nonce() or 0) + 1)


# --------------------------------------------------------------------------- #
# Analysis-tab preload status machinery (phase_tracker.R:276-370)
# --------------------------------------------------------------------------- #


def analysis_tab_keys() -> list[str]:
    return ["exploratory", "cross_metric", "verification", "reference_curves"]


def analysis_tab_labels() -> dict[str, str]:
    return {
        "exploratory": "Exploratory",
        "cross_metric": "Cross-Metric Analysis",
        "verification": "Verification",
        "reference_curves": "Reference Curves",
    }


def empty_analysis_tab_status(default: str = "pending") -> dict[str, str]:
    return {k: default for k in analysis_tab_keys()}


def get_analysis_tab_status(state: AppState, tab: str | None = None):
    with reactive.isolate():
        status = state.analysis_tab_status() or empty_analysis_tab_status()
    if tab is None:
        return status
    return status.get(tab, "pending")


def analysis_tab_request_is_current(state: AppState, request_id=None) -> bool:
    with reactive.isolate():
        current = state.analysis_tab_request_id()
    if request_id is None:
        request_id = current
    return current == request_id


def reset_analysis_tab_state(
    state: AppState, request_id, default: str = "pending"
) -> dict[str, str]:
    status = empty_analysis_tab_status(default)
    with reactive.isolate():
        state.analysis_tab_request_id.set(request_id)
        state.analysis_tab_status.set(status)
        state.analysis_tab_status_nonce.set((state.analysis_tab_status_nonce() or 0) + 1)
        state.analysis_tab_preload_tab.set(None)
        state.analysis_tab_preload_nonce.set(
            (state.analysis_tab_preload_nonce() or 0) + 1
        )
        state.analysis_tab_preload_completed_tab.set(None)
        state.analysis_tab_preload_completed_status.set(None)
        state.analysis_tab_preload_completed_nonce.set(
            (state.analysis_tab_preload_completed_nonce() or 0) + 1
        )
    return status


def set_analysis_tab_status(state: AppState, tab: str, status: str, request_id=None) -> bool:
    if request_id is None:
        with reactive.isolate():
            request_id = state.analysis_tab_request_id()
    if tab not in analysis_tab_keys() or not analysis_tab_request_is_current(
        state, request_id
    ):
        return False

    current = get_analysis_tab_status(state)
    if current.get(tab) == status:
        return False

    updated = dict(current)
    updated[tab] = status
    with reactive.isolate():
        state.analysis_tab_status.set(updated)
        state.analysis_tab_status_nonce.set((state.analysis_tab_status_nonce() or 0) + 1)
    return True


def request_analysis_tab_preload(state: AppState, tab: str, request_id=None) -> bool:
    if request_id is None:
        with reactive.isolate():
            request_id = state.analysis_tab_request_id()
    if tab not in analysis_tab_keys() or not analysis_tab_request_is_current(
        state, request_id
    ):
        return False
    with reactive.isolate():
        state.analysis_tab_preload_tab.set(tab)
        state.analysis_tab_preload_nonce.set(
            (state.analysis_tab_preload_nonce() or 0) + 1
        )
    return True


def complete_analysis_tab_preload(
    state: AppState, tab: str, status: str | None = None, request_id=None
) -> bool:
    if request_id is None:
        with reactive.isolate():
            request_id = state.analysis_tab_request_id()
    if tab not in analysis_tab_keys() or not analysis_tab_request_is_current(
        state, request_id
    ):
        return False

    if status is not None:
        set_analysis_tab_status(state, tab, status, request_id)

    with reactive.isolate():
        state.analysis_tab_preload_completed_tab.set(tab)
        state.analysis_tab_preload_completed_status.set(
            status if status is not None else get_analysis_tab_status(state, tab)
        )
        state.analysis_tab_preload_completed_nonce.set(
            (state.analysis_tab_preload_completed_nonce() or 0) + 1
        )
    return True

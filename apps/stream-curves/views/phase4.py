"""Phase 4 — Reference Curve Finalization. Port of
app/modules/mod_phase4_finalization.R (dialog mode — the only variant the app
mounts; the legacy sidebar layout with the metric picker is not ported).

Stratified metrics show ALL strata simultaneously (overlay plots, strata-as-
columns tables, one manual curve editor per stratum); non-stratified metrics
delegate to views/ref_curve.py.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
from datetime import date

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves.curves import (
    CURVE_FORM_MONOTONE,
    CURVE_FORM_OPTIMUM,
    build_reference_curve,
    build_reference_curve_from_points,
    curve_form_of,
    hydrate_reference_curve_result,
    normalize_reference_curve_points,
    normalize_reference_curve_result,
    reference_curve_row_range_display,
    reference_curve_rows_for_export,
)
from streamcurves import curve_automation as ca
from views import state as st
from views import summary_state as ss
from views.curve_plots import (
    build_overlay_bar_chart,
    build_overlay_curve_plot,
    build_reference_curve_plot,
)
from views.ref_curve import (
    ref_curve_server,
    ref_curve_ui,
    reference_curve_editor_server,
    reference_curve_editor_ui,
    threshold_table_ui,
)
from views.state import AppState
from views.theme import fa
from views.uihelpers import (
    RESPONSE_SHAPE_CHOICES,
    RESPONSE_SHAPE_CONFIG,
    SHAPE_HIGHER,
    no_data_alert,
    response_shape_label,
    response_shape_of,
    status_badge,
)

logger = logging.getLogger("streamcurves")


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip() == ""


def _plain_table(df: pd.DataFrame):
    if df is None or len(df) == 0:
        return ui.div("No values available.", class_="text-muted")
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
        ui.tags.tbody(
            *[
                ui.tags.tr(*[ui.tags.td(str(v)) for v in df.iloc[i]])
                for i in range(len(df))
            ]
        ),
        class_="table table-sm table-striped compact",
    )


def build_phase4_threshold_table(curve_rows) -> pd.DataFrame:
    """R:1006-1045 — Category/Score Range plus one metric-range column per
    stratum (digits=1; module labels, not the summary-page ones)."""
    tbl = pd.DataFrame(
        {
            "Category": ["Functioning", "At-Risk", "Not Functioning"],
            "Score Range": ["0.70 - 1.00", "0.30 - 0.69", "0.00 - 0.29"],
        }
    )
    curve_rows = pd.DataFrame(curve_rows)
    for i in range(len(curve_rows)):
        row = curve_rows.iloc[[i]]
        lvl = str(row["stratum"].iloc[0])
        if row["curve_status"].iloc[0] == "insufficient_data":
            tbl[lvl] = ["N/A"] * 3
        else:
            tbl[lvl] = [
                reference_curve_row_range_display(row, "functioning", digits=1),
                reference_curve_row_range_display(row, "at_risk", digits=1),
                reference_curve_row_range_display(row, "not_functioning", digits=1),
            ]
    return tbl


@module.ui
def phase4_ui(dialog_mode: bool = False):
    return ui.output_ui("phase4_page")


@module.server
def phase4_server(
    input, output, session, state: AppState,
    dialog_mode: bool = False, workspace_scope: str = "standalone",
):
    ns = session.ns
    stratum_editor_ids = reactive.value({})
    registered_stratum_editors: set[str] = set()
    next_stratum_editor_seq = {"n": 0}
    artifacts_loading = reactive.value(False)
    artifacts_error = reactive.value(None)

    _tasks: set[asyncio.Task] = set()

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    def workspace_active(isolate_state: bool = False) -> bool:
        if not dialog_mode:
            return True
        return st.workspace_scope_is_active(
            state, workspace_scope, standalone_modal_type="phase4",
            isolate_state=isolate_state,
        )

    def allocate_stratum_editor_ids(levels) -> dict[str, str]:
        levels = [str(lvl) for lvl in (levels or [])]
        if not levels:
            return {}
        start = next_stratum_editor_seq["n"]
        next_stratum_editor_seq["n"] = start + len(levels)
        return {lvl: f"curve_editor_{start + i + 1}" for i, lvl in enumerate(levels)}

    def sync_phase4_inputs(metric=None):
        """Push config values into the settings inputs once they exist in the
        DOM (R defers via session$onFlushed)."""
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
            mc = (state.metric_config() or {}).get(metric)
        if mc is None:
            return

        async def _push():
            try:
                await asyncio.sleep(0.25)
                ui.update_numeric(
                    "min_sample", value=mc.get("min_sample_size") or 10, session=session
                )
                ui.update_radio_buttons(
                    "transform", selected=mc.get("preferred_transform") or "none",
                    session=session,
                )
                shape = response_shape_of(mc)
                if shape is not None:
                    ui.update_radio_buttons(
                        "response_shape", selected=shape, session=session
                    )
                await st.task_flush()
            except Exception:  # noqa: BLE001
                logger.exception("phase4 settings sync failed")

        _launch(_push())

    # ── page (R:63-137, dialog branch) ────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def phase4_page():
        if not workspace_active():
            return None
        if state.data() is None:
            return no_data_alert()
        return ui.div(
            ui.card(
                ui.card_header("Metric Settings"),
                ui.card_body(
                    ui.layout_column_wrap(
                        ui.output_ui(ns("metric_info")),
                        ui.div(
                            ui.input_numeric(
                                ns("min_sample"), "Min sample size:",
                                value=10, min=3, max=30,
                            ),
                            ui.input_radio_buttons(
                                ns("transform"), "Transform:",
                                choices={"none": "None", "log": "Log"},
                                selected="none",
                            ),
                            # One control for direction AND form. They are the same
                            # question -- which shape scores well -- and as two
                            # independent fields they could contradict: an optimum
                            # form with a TRUE direction reads as "higher is better"
                            # over a two-sided curve, and a blank direction with a
                            # monotone form makes the metric silently unbuildable.
                            ui.input_radio_buttons(
                                ns("response_shape"), "Response shape:",
                                choices=RESPONSE_SHAPE_CHOICES,
                                selected=SHAPE_HIGHER,
                            ),
                            ui.output_ui(ns("response_shape_note")),
                            class_="workspace-phase-settings",
                        ),
                        width=1 / 2,
                    )
                ),
                class_="mb-3",
            ),
            ui.output_ui(ns("artifact_status")),
            ui.output_ui(ns("strat_confirm_card")),
            ui.output_ui(ns("stratified_display")),
            ui.output_ui(ns("unstratified_display")),
            class_="workspace-phase-body",
        )

    # ── decision-state sync (R:139-151) ───────────────────────────────────────
    @reactive.effect
    def _sync_decision():
        if not workspace_active():
            return
        metric = state.current_metric()
        if not metric:
            return
        # R's observe depends on the stores its helpers read; the py helpers
        # isolate internally, so register the store that matters here.
        state.curve_stratification()
        ss.sync_metric_decision_state(
            state, metric,
            ss.build_metric_strat_decision(
                state, metric, ss.get_metric_curve_stratification(state, metric)
            ),
        )

    # ── stratum info (R:172-212) ──────────────────────────────────────────────
    @reactive.calc
    def stratum_info() -> dict:
        req(workspace_active())
        strat = state.strat_decision_user()
        if strat is None or len(strat) == 0:
            return {"has_strata": False, "levels": None, "strat_var": None}
        d = strat.iloc[0]
        if _is_blank(d.get("selected_strat")) or d.get("decision_type") != "single":
            return {"has_strata": False, "levels": None, "strat_var": None}

        strat_var = d.get("selected_strat")
        values = ss.get_stratification_values(
            state.data(), strat_var, state.strat_config() or {}
        )
        levels = sorted({str(v) for v in values if v is not None})
        return {"has_strata": True, "levels": levels, "strat_var": strat_var}

    @reactive.calc
    def strat_values() -> pd.Series:
        info = stratum_info()
        req(info["has_strata"])
        return ss.get_stratification_values(
            state.data(), info["strat_var"], state.strat_config() or {}
        )

    @reactive.effect
    def _allocate_editor_ids():
        if not workspace_active():
            return
        info = stratum_info()
        if not info["has_strata"] or not (info["levels"] or []):
            stratum_editor_ids.set({})
            registered_stratum_editors.clear()
            return
        with reactive.isolate():
            current_map = stratum_editor_ids()
        if list(current_map.keys()) != list(info["levels"]):
            stratum_editor_ids.set(allocate_stratum_editor_ids(info["levels"]))
            registered_stratum_editors.clear()

    @reactive.calc
    def current_phase4_decision() -> pd.DataFrame:
        metric = state.current_metric()
        req(metric)
        state.metric_phase_cache()
        return ss.get_metric_phase4_decision_state(state, metric)

    # ── analysis-tab bookkeeping + artifact refresh (R:219-305) ──────────────
    def sync_analysis_tab_state(status=None, request_id=None, complete=False):
        if request_id is None:
            with reactive.isolate():
                request_id = state.analysis_tab_request_id()
        if (
            not dialog_mode
            or workspace_scope != "analysis"
            or not workspace_active(isolate_state=True)
            or not st.analysis_tab_request_is_current(state, request_id)
        ):
            return None
        with reactive.isolate():
            err = artifacts_error()
        resolved = status or ("ready" if not err else "error")
        st.set_analysis_tab_status(state, "reference_curves", resolved, request_id)
        if complete:
            st.complete_analysis_tab_preload(
                state, "reference_curves", resolved, request_id
            )
        return resolved

    def refresh_phase4_artifacts(
        metric=None, request_id=None, complete=False, defer=False
    ):
        if not workspace_active(isolate_state=True):
            return False
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
            if request_id is None:
                request_id = state.analysis_tab_request_id()
        if not metric:
            return False

        if ss.metric_has_phase4_cache(state, metric, artifact_mode="full"):
            artifacts_loading.set(False)
            artifacts_error.set(None)
            sync_analysis_tab_state(request_id=request_id, complete=complete)
            return False

        artifacts_loading.set(True)
        artifacts_error.set(None)

        def run_refresh():
            if (
                dialog_mode
                and workspace_scope == "analysis"
                and not st.analysis_tab_request_is_current(state, request_id)
            ):
                artifacts_loading.set(False)
                return
            try:
                ss.preload_metric_phase4_workspace(state, metric, artifact_mode="full")
                artifacts_error.set(None)
            except Exception as e:  # noqa: BLE001
                logger.exception("phase4 artifact refresh failed")
                artifacts_error.set(str(e))
            finally:
                artifacts_loading.set(False)
                sync_analysis_tab_state(request_id=request_id, complete=complete)

        if defer:

            async def _deferred():
                try:
                    # Yield first so the in-progress flush (loading alert)
                    # transmits before we flush ourselves (M2 landmine:
                    # re-entrant reactive.flush() corrupts the cycle).
                    await asyncio.sleep(0)
                    await st.task_flush()
                    await asyncio.sleep(0)
                    run_refresh()
                    await st.task_flush()
                except Exception:  # noqa: BLE001
                    logger.exception("phase4 deferred artifact refresh failed")

            _launch(_deferred())
        else:
            run_refresh()
        return True

    def persist_stratified_curve_results(results: dict):
        with reactive.isolate():
            metric = state.current_metric()
            data = state.data()
            metric_config = state.metric_config() or {}
            completed = state.completed_metrics() or {}
        if not metric:
            return None
        with reactive.isolate():
            decision_tbl = current_phase4_decision()
            info = stratum_info()

        stratum_results = {
            lvl: {
                "reference_curve": hydrate_reference_curve_result(
                    result, data, metric, metric_config,
                    stratum_label=lvl, artifact_mode="full",
                )
            }
            for lvl, result in results.items()
        }

        curve_rows = ss.extract_metric_phase4_curve_rows(
            {"stratum_results": stratum_results}
        )
        phase4_signature = ss.cache_metric_phase4_results(
            state, metric, decision_tbl=decision_tbl,
            stratum_results=stratum_results, artifact_mode="full",
        )

        if metric in completed:
            ss.update_metric_phase4_completed_entry(
                state, metric,
                {
                    "stratified": True,
                    "strat_var": info["strat_var"],
                    "strat_decision": decision_tbl,
                    "stratum_results": stratum_results,
                    "phase4_signature": phase4_signature,
                    "phase4_artifact_mode": "full",
                    "phase4_curve_rows": curve_rows,
                },
            )
        return stratum_results

    # ── modal-ready + analysis preload + retry (R:368-447) ────────────────────
    @reactive.effect
    @reactive.event(state.workspace_modal_ready_nonce, ignore_init=True)
    def _modal_ready():
        if dialog_mode and workspace_active():
            with reactive.isolate():
                modal_metric = state.workspace_modal_metric() or state.current_metric()
            artifacts_loading.set(False)
            artifacts_error.set(None)
            if modal_metric:
                sync_phase4_inputs(modal_metric)

    @reactive.effect
    @reactive.event(state.analysis_tab_preload_nonce, ignore_init=True)
    def _preload():
        if (
            not dialog_mode
            or workspace_scope != "analysis"
            or not workspace_active()
        ):
            return
        with reactive.isolate():
            if state.analysis_tab_preload_tab() != "reference_curves":
                return
            request_id = state.analysis_tab_request_id()
            modal_metric = state.workspace_modal_metric() or state.current_metric()
        if not modal_metric or not st.analysis_tab_request_is_current(state, request_id):
            return
        refresh_phase4_artifacts(
            modal_metric, request_id=request_id, complete=True, defer=True
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def artifact_status():
        if not dialog_mode or workspace_scope != "analysis" or not workspace_active():
            return None
        if artifacts_loading():
            return ui.div(
                ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true"),
                ui.tags.span(
                    "Loading full reference curve visuals. Summary results stay "
                    "available while plots regenerate."
                ),
                class_="alert alert-info d-flex align-items-center gap-2",
            )
        err = artifacts_error()
        if err:
            return ui.div(
                ui.tags.span(f"Could not load full reference curve visuals: {err}"),
                ui.input_action_button(
                    ns("retry_artifacts"), "Retry details",
                    class_="btn btn-outline-danger btn-sm",
                ),
                class_="alert alert-danger d-flex justify-content-between "
                "align-items-center flex-wrap gap-2",
            )
        return None

    @reactive.effect
    @reactive.event(input.retry_artifacts, ignore_init=True)
    def _retry():
        refresh_phase4_artifacts(complete=True, defer=True)

    # ── phase4_data init (R:450-459) ──────────────────────────────────────────
    @reactive.effect
    def _init_phase4_data():
        if not workspace_active():
            return
        metric = state.current_metric()
        if not metric:
            return
        data = state.data()
        state.phase4_data.set(data)
        state.current_stratum_level.set(None)

    # ── advanced settings write-back (R:462-480) ──────────────────────────────
    def _write_back_config(key, value):
        with reactive.isolate():
            metric = state.current_metric()
            mc = state.metric_config() or {}
        if not metric or mc.get(metric) is None:
            return
        if mc[metric].get(key) == value:
            return
        mc = dict(mc)
        entry = dict(mc[metric])
        entry[key] = value
        mc[metric] = entry
        state.metric_config.set(mc)

    @reactive.effect
    @reactive.event(input.min_sample, ignore_init=True)
    def _write_min_sample():
        if input.min_sample() is not None:
            _write_back_config("min_sample_size", input.min_sample())

    @reactive.effect
    @reactive.event(input.transform, ignore_init=True)
    def _write_transform():
        _write_back_config("preferred_transform", input.transform())

    @reactive.effect
    @reactive.event(input.response_shape, ignore_init=True)
    def _write_response_shape():
        shape = input.response_shape()
        if shape not in RESPONSE_SHAPE_CONFIG:
            return
        higher_is_better, curve_form = RESPONSE_SHAPE_CONFIG[shape]
        with reactive.isolate():
            metric = state.current_metric()
            mc = state.metric_config() or {}
        if not metric or mc.get(metric) is None:
            return
        entry = mc[metric]
        if (entry.get("higher_is_better") == higher_is_better
                and curve_form_of(entry) == curve_form):
            return

        # Both keys in one update: a half-applied pair is exactly the contradiction
        # this control exists to prevent.
        mc = dict(mc)
        updated = dict(entry)
        updated["higher_is_better"] = higher_is_better
        updated["curve_form"] = curve_form
        mc[metric] = updated
        state.metric_config.set(mc)

        # The phase-4 signature carries only the data fingerprint, config version and
        # stratification decision, so a shape change does not invalidate anything on
        # its own -- the metric would keep showing a curve built for the old shape.
        # Rebuild just this metric; bumping config_version would invalidate every
        # metric and wipe all analysis state.
        try:
            ss.recompute_metric_phase4(state, metric)
        except Exception:  # noqa: BLE001 — the row falls back to "recompute required"
            logger.exception("Curve rebuild after a response-shape change failed")
        st.notify_workspace_refresh(state)
        ui.notification_show(
            f"Response shape set to {RESPONSE_SHAPE_CHOICES[shape].lower()}; "
            "the reference curve was rebuilt.",
            type="message", duration=5,
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def response_shape_note():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric)
        req(mc)
        if response_shape_of(mc) is not None:
            return None
        # No agreed direction: the engine builds no curve until one is chosen.
        return ui.tags.p(
            "Direction is under review for this metric, so no curve is built. "
            "Choose a response shape to resolve it.",
            class_="text-muted small mb-0",
        )

    # ── metric info card (R:483-524) ──────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def metric_info():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric)
        req(mc)
        precheck = state.precheck_df()
        n_obs = None
        if precheck is not None and len(precheck) > 0:
            rows = precheck[precheck["metric"] == metric]
            if len(rows) > 0:
                n_obs = rows["n_obs"].iloc[0]
        completed = metric in (state.completed_metrics() or {})

        # "Neutral" was misleading for a two-sided metric, whose null direction is
        # deliberate: the reference distribution itself is the optimum.
        direction = response_shape_label(mc)

        def _row(label, value):
            return ui.tags.tr(
                ui.tags.td(label, class_="info-label"),
                ui.tags.td(value, class_="info-value"),
            )

        return ui.div(
            ui.tags.table(
                ui.tags.tbody(
                    _row("Family:", mc.get("metric_family")),
                    _row("Units:", mc.get("units")),
                    _row("Direction:", direction),
                    _row("n obs:", str(n_obs)) if n_obs is not None else None,
                ),
                class_="table table-sm mb-0",
            ),
            ui.div(status_badge("pass", "COMPLETED"), class_="mt-2")
            if completed
            else None,
            class_="metric-info-card",
        )

    # ── stratification confirmation card (R:527-615) ──────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def strat_confirm_card():
        metric = state.current_metric()
        req(metric)
        # These helpers isolate internally; register the stores R depends on.
        state.curve_stratification()
        strat = current_phase4_decision()
        current_choice = ss.get_metric_curve_stratification(state, metric)
        recommended_choice = ss.get_metric_curve_strat_recommendation(state, metric)
        choice_choices = ss.get_metric_curve_strat_choices(state, metric)
        info = stratum_info()

        d = strat.iloc[0] if strat is not None and len(strat) > 0 else None
        is_single = (
            d is not None
            and d.get("decision_type") == "single"
            and not _is_blank(d.get("selected_strat"))
        )
        strat_name = (
            ss.get_strat_display_name(state, d.get("selected_strat"))
            if is_single
            else "None"
        )

        selector_card = ui.card(
            ui.card_header("Curve Stratification"),
            ui.card_body(
                ui.input_select(
                    ns("curve_strat_choice"),
                    "Stratification used for curves:",
                    choices=choice_choices,
                    selected=current_choice,
                    width="100%",
                ),
                ui.div(
                    fa("wand-magic-sparkles"),
                    ui.tags.strong(" Recommended stratification: "),
                    ss.get_metric_curve_strat_label(state, metric, recommended_choice),
                    class_="alert alert-info mb-0 py-2"
                    if current_choice == recommended_choice
                    else "alert alert-warning mb-0 py-2",
                ),
            ),
            class_="mb-3",
        )

        if info["has_strata"]:
            vals = strat_values()
            level_labels = [
                f"{lvl} (n={int((vals == lvl).sum())})" for lvl in info["levels"]
            ]
            return ui.TagList(
                selector_card,
                ui.div(
                    ui.tags.strong("Stratification: "),
                    strat_name,
                    " | Mode: Subset (separate curve per level)",
                    ui.tags.br(),
                    "Levels: ",
                    ", ".join(level_labels),
                    class_="alert alert-info mb-3",
                ),
            )

        detail = None
        if is_single:
            p = d.get("selected_p_value")
            n_groups = d.get("selected_n_groups")
            min_n = d.get("selected_min_n")
            p_txt = f"{float(p):.4f}" if p is not None and math.isfinite(float(p)) else "NA"
            g_txt = str(int(n_groups)) if n_groups is not None and math.isfinite(float(n_groups)) else "NA"
            m_txt = str(int(min_n)) if min_n is not None and math.isfinite(float(min_n)) else "NA"
            detail = f" | p = {p_txt} | groups = {g_txt} | min n = {m_txt}"
        return ui.TagList(
            selector_card,
            ui.div(
                ui.tags.strong("Stratification: "),
                strat_name,
                detail,
                class_="alert alert-info mb-3",
            ),
        )

    @reactive.effect
    @reactive.event(input.curve_strat_choice, ignore_init=True)
    def _curve_strat_choice():
        with reactive.isolate():
            metric = state.current_metric()
        if not metric:
            return
        old_choice = ss.get_metric_curve_stratification(state, metric)
        new_choice = ss.set_metric_curve_stratification(
            state, metric, input.curve_strat_choice() or "none"
        )
        if old_choice != new_choice:
            st.notify_workspace_refresh(state)

    # ── sub-module: unstratified path (R:617-632) ─────────────────────────────
    ref_curve_server("ref_curve", state=state, workspace_scope=workspace_scope)

    @output(suspend_when_hidden=False)
    @render.ui
    def unstratified_display():
        if not workspace_active():
            return None
        info = stratum_info()
        if info["has_strata"]:
            return None
        return ref_curve_ui(ns("ref_curve"))

    # ── all-strata results (R:639-700) ────────────────────────────────────────
    @reactive.calc
    def all_strata_results() -> dict:
        req(workspace_active())
        info = stratum_info()
        req(info["has_strata"])
        metric = state.current_metric()
        req(metric)
        # Explicit cache dependency (R reads rv$metric_phase_cache via helpers).
        state.metric_phase_cache()
        data = state.data()
        metric_config = state.metric_config() or {}
        decision_tbl = ss.get_metric_phase4_decision_state(state, metric)
        vals = strat_values()

        if ss.metric_has_phase4_cache(state, metric, decision_tbl, artifact_mode="summary"):
            cached = ss.get_metric_phase4_cached_result(state, metric)
            stratum_map = cached["stratum_results"] or {}
            cached_results = {}
            for lvl in info["levels"]:
                entry = stratum_map.get(lvl)
                if entry is None:
                    cached_results[lvl] = None
                    continue
                cached_results[lvl] = hydrate_reference_curve_result(
                    (entry or {}).get("reference_curve", entry),
                    data[vals == lvl], metric, metric_config,
                    stratum_label=lvl,
                    artifact_mode=cached["artifact_mode"] or "full",
                )
            if all(v is not None for v in cached_results.values()):
                return cached_results

        return {
            lvl: build_reference_curve(
                data[vals == lvl], metric, metric_config, stratum_label=lvl
            )
            for lvl in info["levels"]
        }

    @reactive.calc
    def all_strata_curve_rows() -> pd.DataFrame:
        req(workspace_active())
        results = all_strata_results()
        req(results)
        metric = state.current_metric()
        metric_config = state.metric_config() or {}
        frames = []
        for lvl, result in results.items():
            normalized = normalize_reference_curve_result(
                result, metric_config=metric_config, metric_key=metric,
                stratum_label=lvl,
            )
            if normalized is not None and normalized.get("curve_row") is not None:
                frames.append(normalized["curve_row"])
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    # ── per-stratum editor servers (R:702-788) ────────────────────────────────
    def _mount_stratum_editor(stratum_level: str, child_editor_id: str):
        def _current():
            results = all_strata_results()
            return results.get(stratum_level)

        def _metric_entry() -> dict:
            with reactive.isolate():
                metric = state.current_metric()
                return (state.metric_config() or {}).get(metric) or {}

        def _higher_is_better():
            # `is True`, not bool(): a two-sided metric carries a deliberate None.
            return _metric_entry().get("higher_is_better") is True

        def _curve_form():
            return curve_form_of(_metric_entry())

        def _rebuild(points=None):
            with reactive.isolate():
                metric = state.current_metric()
                metric_config = state.metric_config() or {}
                data = state.data()
                vals = strat_values()
                results = dict(all_strata_results())
            stratum_data = data[vals == stratum_level]
            if points is None:
                results[stratum_level] = build_reference_curve(
                    stratum_data, metric, metric_config, stratum_label=stratum_level
                )
            else:
                results[stratum_level] = build_reference_curve_from_points(
                    stratum_data, metric, metric_config,
                    curve_points=points, stratum_label=stratum_level,
                )
            persist_stratified_curve_results(results)
            st.notify_workspace_refresh(state)

        def _on_apply(points):
            _rebuild(points)
            ui.notification_show(
                f"Applied manual curve edits for stratum '{stratum_level}'.",
                type="message", duration=4,
            )

        def _on_reset():
            _rebuild(None)
            ui.notification_show(
                f"Reset stratum '{stratum_level}' to the auto-generated curve.",
                type="message", duration=4,
            )

        reference_curve_editor_server(
            child_editor_id,
            current_result=_current,
            higher_is_better=_higher_is_better,
            curve_form=_curve_form,
            on_apply=_on_apply,
            on_reset=_on_reset,
        )

    @reactive.effect
    def _register_editors():
        if not workspace_active():
            return
        info = stratum_info()
        req(info["has_strata"])
        editor_id_map = stratum_editor_ids()
        req(len(editor_id_map) == len(info["levels"]))

        new_ids = set(editor_id_map.values()) - registered_stratum_editors
        if not new_ids:
            return
        for lvl in info["levels"]:
            editor_id = editor_id_map[lvl]
            if editor_id in new_ids:
                _mount_stratum_editor(lvl, editor_id)
        registered_stratum_editors.update(new_ids)

    # ── stratified display (R:791-927) ────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def stratified_display():
        if not workspace_active():
            return None
        info = stratum_info()
        if not info["has_strata"]:
            return None

        results = all_strata_results()
        req(results)
        editor_id_map = stratum_editor_ids()
        req(len(editor_id_map) == len(info["levels"]))
        curve_rows = all_strata_curve_rows()
        req(curve_rows is not None and len(curve_rows) > 0)

        metric = state.current_metric()
        mc = (state.metric_config() or {}).get(metric) or {}
        min_n = mc.get("min_sample_size") or 10

        vals = strat_values()
        warnings = []
        for lvl in info["levels"]:
            n = int((vals == lvl).sum())
            if n < min_n:
                warnings.append(
                    ui.div(
                        fa("triangle-exclamation"),
                        f" Stratum '{lvl}' has only {n} observations "
                        f"(minimum: {min_n}). Results may be unreliable.",
                        class_="alert alert-warning py-2 mb-2",
                    )
                )

        n_valid = int((curve_rows["curve_status"] == "complete").sum())

        curve_card_body: object
        if n_valid >= 2:
            curve_card_body = ui.output_plot(ns("strat_curve_plot"), height="420px")
        elif n_valid == 1:
            curve_card_body = ui.TagList(
                ui.output_plot(ns("strat_curve_plot_single"), height="420px"),
                ui.div(
                    "Only 1 stratum has a valid curve. Overlay requires 2+.",
                    class_="text-muted mt-1",
                ),
            )
        else:
            curve_card_body = ui.div(
                "No valid strata for scoring curve plot.", class_="text-muted"
            )

        accordion_panels = []
        for lvl in info["levels"]:
            editor_result = results.get(lvl) or {}
            label = (
                f"{lvl} (Manual)"
                if (editor_result.get("curve_source") or "auto") == "manual"
                else lvl
            )
            accordion_panels.append(
                ui.accordion_panel(
                    label,
                    reference_curve_editor_ui(
                        ns(editor_id_map[lvl]), title=f"Edit {lvl} Curve"
                    ),
                )
            )

        return ui.TagList(
            *warnings,
            ui.layout_column_wrap(
                ui.card(
                    ui.card_header("Distributions by Stratum"),
                    ui.card_body(
                        ui.output_plot(ns("strat_dist_plot"), height="420px")
                        if n_valid >= 1
                        else ui.div(
                            "No valid strata for distribution plot.",
                            class_="text-muted",
                        )
                    ),
                    class_="mb-3",
                ),
                ui.card(
                    ui.card_header("Scoring Curves by Stratum"),
                    ui.card_body(curve_card_body),
                    class_="mb-3",
                ),
                width=1 / 2,
            ),
            ui.card(
                ui.card_header("Descriptive Statistics"),
                ui.card_body(_plain_table(ss.build_stratified_stats_table(curve_rows))),
                class_="mb-3",
            ),
            ui.card(
                ui.card_header("Scoring Thresholds"),
                ui.card_body(threshold_table_ui(build_phase4_threshold_table(curve_rows))),
                class_="mb-3",
            ),
            ui.card(
                ui.card_header("Manual Curve Editors"),
                ui.card_body(
                    ui.accordion(
                        *accordion_panels,
                        id=ns("strat_curve_editors"),
                        open=False,
                    )
                ),
                class_="mb-3",
            ),
            ui.div(
                ui.download_button(
                    ns("dl_strat_curve_png"), "Download Overlay Curve PNG",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.download_button(
                    ns("dl_strat_dist_png"), "Download Overlay Distribution PNG",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.download_button(
                    ns("dl_strat_csv"), "Download Thresholds CSV",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                class_="d-flex gap-2 mb-3",
            ),
            ui.div(
                ui.input_action_button(
                    ns("mark_all_complete"), "Mark All Strata Complete ✓",
                    class_="btn btn-success btn-proceed", icon=fa("check"),
                ),
                class_="d-flex justify-content-end mt-3",
            ),
        )

    # ── stratified plots (R:930-970) ──────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.plot
    def strat_curve_plot():
        req(workspace_active())
        curve_rows = all_strata_curve_rows()
        req(curve_rows is not None and len(curve_rows) > 0)
        with reactive.isolate():
            metric_config = state.metric_config() or {}
        fig = build_overlay_curve_plot(curve_rows, metric_config)
        req(fig is not None)
        return fig

    @output(suspend_when_hidden=False)
    @render.plot
    def strat_curve_plot_single():
        req(workspace_active())
        results = all_strata_results()
        req(results)
        with reactive.isolate():
            metric = state.current_metric()
            metric_config = state.metric_config() or {}
        for lvl, result in results.items():
            normalized = normalize_reference_curve_result(
                result, metric_config=metric_config, metric_key=metric,
                stratum_label=lvl,
            )
            if normalized is None or normalized.get("curve_row") is None:
                continue
            row = normalized["curve_row"]
            if str(row["curve_status"].iloc[0]) != "complete":
                continue
            points = normalize_reference_curve_points(normalized.get("curve_points"))
            if len(points) < 2:
                continue
            fig = build_reference_curve_plot(
                points, row, metric_config, metric, stratum_label=lvl
            )
            if fig is not None:
                return fig
        req(False)

    @output(suspend_when_hidden=False)
    @render.plot
    def strat_dist_plot():
        req(workspace_active())
        info = stratum_info()
        req(info["has_strata"])
        curve_rows = all_strata_curve_rows()
        req(curve_rows is not None and len(curve_rows) > 0)
        with reactive.isolate():
            metric = state.current_metric()
            metric_config = state.metric_config() or {}
            data = state.data()
        valid_levels = curve_rows[curve_rows["curve_status"] == "complete"]["stratum"]
        all_levels = list(dict.fromkeys(curve_rows["stratum"].astype(str)))
        levels_to_show = all_levels if len(valid_levels) > 0 else info["levels"]
        plot_data = data.copy()
        plot_data[".summary_stratum"] = strat_values().to_numpy()
        fig = build_overlay_bar_chart(
            plot_data, metric, metric_config, ".summary_stratum", levels_to_show
        )
        req(fig is not None)
        return fig

    # ── mark all complete (R:1048-1091) ───────────────────────────────────────
    @reactive.effect
    @reactive.event(input.mark_all_complete, ignore_init=True)
    def _mark_all_complete():
        info = stratum_info()
        req(info["has_strata"])
        metric = state.current_metric()
        req(metric)
        results = all_strata_results()
        req(results)
        with reactive.isolate():
            decision_tbl = current_phase4_decision()
            mc = (state.metric_config() or {}).get(metric) or {}

        stratum_results = {
            lvl: {"reference_curve": result} for lvl, result in results.items()
        }
        phase4_signature = ss.cache_metric_phase4_results(
            state, metric, decision_tbl=decision_tbl,
            stratum_results=stratum_results, artifact_mode="full",
        )
        curve_rows = ss.extract_metric_phase4_curve_rows(
            {"stratum_results": stratum_results}
        )
        ss.update_metric_phase4_completed_entry(
            state, metric,
            {
                "stratified": True,
                "strat_var": info["strat_var"],
                "strat_decision": decision_tbl,
                "stratum_results": stratum_results,
                "phase4_signature": phase4_signature,
                "phase4_artifact_mode": "full",
                "phase4_curve_rows": curve_rows,
            },
        )

        # Re-score this metric after a manual "mark all complete" so the guided
        # review queue reflects the finalized strata (choke point, mirrors summary).
        ca.sync_curve_review_after_recompute(state, [metric])

        ui.notification_show(
            f"{mc.get('display_name') or metric} — all {len(info['levels'])} "
            "strata marked complete!",
            type="message", duration=5,
        )
        st.notify_workspace_refresh(state)

    # ── downloads (R:1094-1136) ───────────────────────────────────────────────
    def _iso_metric() -> str:
        with reactive.isolate():
            return state.current_metric() or "metric"

    @render.download(filename=lambda: f"{_iso_metric()}_overlay_scoring_curves.png")
    def dl_strat_curve_png():
        with reactive.isolate():
            curve_rows = all_strata_curve_rows()
            metric_config = state.metric_config() or {}
        fig = build_overlay_curve_plot(curve_rows, metric_config)
        if fig is None:
            yield b""
            return
        buf = io.BytesIO()
        fig.save(buf, width=10, height=6, dpi=300, verbose=False)
        yield buf.getvalue()

    @render.download(filename=lambda: f"{_iso_metric()}_overlay_distributions.png")
    def dl_strat_dist_png():
        with reactive.isolate():
            info = stratum_info()
            metric = state.current_metric()
            metric_config = state.metric_config() or {}
            data = state.data()
            vals = strat_values()
        if not info["has_strata"]:
            yield b""
            return
        plot_data = data.copy()
        plot_data[".summary_stratum"] = vals.to_numpy()
        fig = build_overlay_bar_chart(
            plot_data, metric, metric_config, ".summary_stratum", info["levels"]
        )
        if fig is None:
            yield b""
            return
        buf = io.BytesIO()
        fig.save(buf, width=10, height=6, dpi=300, verbose=False)
        yield buf.getvalue()

    @render.download(filename=lambda: f"{_iso_metric()}_strata_thresholds.csv")
    def dl_strat_csv():
        with reactive.isolate():
            curve_rows = all_strata_curve_rows()
        yield reference_curve_rows_for_export(curve_rows).to_csv(index=False).encode(
            "utf-8"
        )

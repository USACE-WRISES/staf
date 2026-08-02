"""Phase 3 — Final Stratification Verification. Port of
app/modules/mod_phase3_verification.R (dialog mode — the only variant the app
mounts; the legacy sidebar layout is not ported).

Not ported (dead code in R — no UI renders those inputs): the confirm_phase3
observer block (final_strat_choice / justification / verify_status_* inputs).
The results card states the tab is advisory; the final curve stratification is
chosen on the Reference Curves tab.

Pattern scatterplots: the Python stability engine returns diagnostics only
(no figures); the Pattern Stability table carries the substance. The focused
boxplots render from the screening plot_specs.
"""

from __future__ import annotations

import logging

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves.feasibility import assess_feasibility
from streamcurves.stability import assess_pattern_stability
from views import state as st
from views import summary_state as ss
from views.screening_plots import build_screening_plot_from_spec
from views.state import AppState
from views.theme import fa
from views.uihelpers import no_data_alert, status_badge

logger = logging.getLogger("streamcurves")

_FLAG_BG = {
    "feasible": "rgba(39,174,96,0.15)",
    "marginal": "rgba(243,156,18,0.15)",
    "infeasible": "rgba(231,76,60,0.15)",
    "stable": "rgba(39,174,96,0.15)",
    "unstable": "rgba(231,76,60,0.15)",
}


def _styled_table(df: pd.DataFrame, flag_col: str | None = None):
    if df is None or len(df) == 0:
        return ui.div("No values available.", class_="text-muted")
    rows = []
    for _, r in df.iterrows():
        cells = []
        for col in df.columns:
            style = None
            if flag_col is not None and col == flag_col:
                style = f"background-color: {_FLAG_BG.get(str(r[col]), 'transparent')};"
            cells.append(ui.tags.td(str(r[col]), style=style))
        rows.append(ui.tags.tr(*cells))
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
        ui.tags.tbody(*rows),
        class_="table table-sm table-striped compact",
    )


@module.ui
def phase3_ui(dialog_mode: bool = False):
    return ui.output_ui("phase3_page")


@module.server
def phase3_server(
    input, output, session, state: AppState,
    dialog_mode: bool = False, workspace_scope: str = "standalone",
):
    ns = session.ns
    verify_data = reactive.value(None)
    artifacts_loading = reactive.value(False)
    artifacts_error = reactive.value(None)
    registered_bp: set[str] = set()

    def workspace_active(isolate_state: bool = False) -> bool:
        if not dialog_mode:
            return True
        return st.workspace_scope_is_active(
            state, workspace_scope, standalone_modal_type="phase3",
            isolate_state=isolate_state,
        )

    def set_verify_state(metric=None):
        if not workspace_active(isolate_state=True):
            verify_data.set(None)
            return
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
        if not metric:
            verify_data.set(None)
            return
        verify_data.set(ss.get_metric_phase3_display_state(state, metric))

    def refresh_verify_artifacts(metric=None, show_progress: bool = True):
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
        if not workspace_active(isolate_state=True):
            return False
        needs1 = ss.metric_needs_phase1_artifact_refresh(state, metric)
        needs3 = ss.metric_needs_phase3_artifact_refresh(state, metric)
        if not (needs1 or needs3):
            artifacts_loading.set(False)
            artifacts_error.set(None)
            set_verify_state(metric)
            return False
        artifacts_loading.set(True)
        artifacts_error.set(None)
        try:
            if needs1:
                ss.ensure_metric_phase1_artifacts(state, metric)
            if needs3:
                ss.ensure_metric_phase3_artifacts(state, metric)
            artifacts_error.set(None)
        except Exception as e:  # noqa: BLE001
            logger.exception("phase3 artifact refresh failed")
            artifacts_error.set(str(e))
        finally:
            artifacts_loading.set(False)
            set_verify_state(metric)
        return True

    # ── modal-ready + analysis preload (R:228-258) ───────────────────────────
    @reactive.effect
    @reactive.event(state.workspace_modal_ready_nonce, ignore_init=True)
    def _modal_ready():
        if dialog_mode and workspace_active():
            with reactive.isolate():
                metric = state.workspace_modal_metric() or state.current_metric()
            if not metric:
                return
            artifacts_loading.set(False)
            artifacts_error.set(None)
            set_verify_state(metric)

    @reactive.effect
    @reactive.event(state.analysis_tab_preload_nonce, ignore_init=True)
    def _preload():
        if not dialog_mode or workspace_scope != "analysis" or not workspace_active():
            return
        with reactive.isolate():
            if state.analysis_tab_preload_tab() != "verification":
                return
            request_id = state.analysis_tab_request_id()
            metric = state.workspace_modal_metric() or state.current_metric()
        if not metric or not st.analysis_tab_request_is_current(state, request_id):
            return
        artifacts_loading.set(False)
        artifacts_error.set(None)
        set_verify_state(metric)
        refresh_verify_artifacts(metric, show_progress=False)
        with reactive.isolate():
            status = "error" if artifacts_error() else "ready"
        st.set_analysis_tab_status(state, "verification", status, request_id)
        st.complete_analysis_tab_preload(state, "verification", status, request_id)

    @reactive.effect
    @reactive.event(input.retry_artifacts, ignore_init=True)
    def _retry():
        refresh_verify_artifacts()

    # ── page (R:139-190, dialog branch) ──────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def phase3_page():
        if not workspace_active():
            return None
        if state.data() is None:
            return no_data_alert()
        return ui.div(
            # verification_ui bottoms out in req(finalists()), so an unscreened
            # metric renders nothing at all -- this line is what makes that silence
            # legible.
            ui.tags.p(
                "Requires exploratory screening for this metric.",
                class_="text-muted small mb-2",
            ),
            ui.output_ui(ns("metric_info_brief")),
            ui.output_ui(ns("artifact_status")),
            ui.output_ui(ns("verification_ui")),
            class_="workspace-phase-body",
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def artifact_status():
        if artifacts_loading():
            return ui.div(
                ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true"),
                ui.tags.span(
                    "Loading full verification details. Finalist selections are ready "
                    "while verification plots and tables regenerate."
                ),
                class_="alert alert-info d-flex align-items-center gap-2",
            )
        err = artifacts_error()
        if err:
            return ui.div(
                ui.tags.span(f"Could not load full verification details: {err}"),
                ui.input_action_button(
                    ns("retry_artifacts"), "Retry details",
                    class_="btn btn-outline-danger btn-sm",
                ),
                class_="alert alert-danger d-flex justify-content-between "
                "align-items-center flex-wrap gap-2",
            )
        return None

    @output(suspend_when_hidden=False)
    @render.ui
    def metric_info_brief():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric)
        req(mc)
        return ui.div(
            ui.tags.p(
                ui.tags.strong(mc.get("display_name") or metric),
                ui.tags.br(),
                ui.tags.span(
                    f"{mc.get('metric_family')} | {mc.get('units')}", class_="text-muted"
                ),
            ),
            class_="metric-info-card mt-2",
        )

    # ── finalists (R:305-343) ─────────────────────────────────────────────────
    @reactive.calc
    def finalists() -> pd.DataFrame | None:
        metric = state.current_metric()
        req(metric)
        p1_cands = (state.phase1_candidates() or {}).get(metric)
        p2_selected = ss.get_metric_phase2_passed(state, metric)
        ranking = state.phase2_ranking()

        strats: list[str] = []
        p1_status: dict[str, str] = {}
        p2_tier: dict[str, str] = {}
        if p1_cands is not None and len(p1_cands) > 0:
            kept = p1_cands[p1_cands["candidate_status"].isin(["promising", "possible"])]
            strats = list(dict.fromkeys(kept["stratification"].astype(str)))
            for _, r in p1_cands.iterrows():
                p1_status[str(r["stratification"])] = str(r["candidate_status"])
        for sk in p2_selected:
            if sk not in strats:
                strats.append(sk)
        if ranking is not None and len(ranking) > 0:
            for _, r in ranking.iterrows():
                p2_tier[str(r["stratification"])] = str(r["tier"])
        if not strats:
            return None
        return pd.DataFrame(
            {
                "stratification": strats,
                "phase1_status": [p1_status.get(sk, "unknown") for sk in strats],
                "phase2_tier": [p2_tier.get(sk, "N/A") for sk in strats],
            }
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def verification_ui():
        fin = finalists()
        if fin is None or len(fin) == 0:
            return ui.div(
                fa("circle-info"),
                " No verification candidates are available yet. Complete exploratory "
                "screening for this metric first.",
                class_="alert alert-info mt-3",
            )
        with reactive.isolate():
            strat_config = state.strat_config() or {}
        rows = []
        for _, r in fin.iterrows():
            sk = r["stratification"]
            sc = strat_config.get(sk) or {}
            badges = [
                status_badge(
                    {"promising": "pass", "possible": "caution"}.get(
                        r["phase1_status"], "not_applicable"
                    ),
                    f"P1: {r['phase1_status']}",
                )
            ]
            if r["phase2_tier"] != "N/A":
                badges.append(
                    status_badge(
                        {
                            "Broad-Use Candidate": "pass",
                            "Metric-Specific Candidate": "caution",
                        }.get(r["phase2_tier"], "not_applicable"),
                        f"P2: {r['phase2_tier']}",
                    )
                )
            rows.append(
                ui.div(
                    ui.input_checkbox(
                        ns(f"verify_{sk}"), sc.get("display_name") or sk, value=True
                    ),
                    *badges,
                    class_="d-flex align-items-center gap-3 mb-2",
                )
            )
        return ui.TagList(
            ui.card(
                ui.card_header("Finalist Stratifications"),
                ui.card_body(
                    ui.tags.p("Select which finalists to verify.", class_="text-muted"),
                    *rows,
                    ui.input_action_button(
                        ns("run_verification"),
                        ui.TagList(fa("play"), " Run Verification Checks"),
                        class_="btn btn-primary mt-2",
                    ),
                ),
            ),
            ui.output_ui(ns("verify_results_ui")),
        )

    # ── run verification (R:397-463) ──────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.run_verification)
    def _run_verification():
        fin = finalists()
        req(fin is not None)
        checked = [
            sk for sk in fin["stratification"]
            if bool((input[f"verify_{sk}"]() if f"verify_{sk}" in input else False))
        ]
        req(len(checked) > 0)
        with reactive.isolate():
            metric = state.current_metric()
            data = state.data()
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}
            predictor_config = state.predictor_config() or {}
        predictor_keys = (metric_config.get(metric) or {}).get("allowed_predictors") or []

        p = ui.Progress(min=0, max=1)
        p.set(value=0, message="Running verification...")
        try:
            all_results = []
            targets = ["none"] + checked
            for i, sk in enumerate(targets):
                sk_actual = None if sk == "none" else sk
                p.set(value=0.3 * (i + 1) / len(targets), message="Running verification...")
                try:
                    res = assess_pattern_stability(
                        data, metric, sk_actual, predictor_keys,
                        metric_config, strat_config, predictor_config,
                    )
                except Exception:  # noqa: BLE001
                    res = pd.DataFrame()
                if res is not None and len(res) > 0:
                    all_results.append(res)
            patterns = {
                "results": pd.concat(all_results, ignore_index=True)
                if all_results
                else pd.DataFrame(),
                "plots": {},
            }
            state.phase3_patterns.set(patterns)
            p.set(value=0.6, message="Running verification...", detail="Assessing feasibility...")
            feas = assess_feasibility(data, checked, strat_config)
            state.phase3_feasibility.set(feas)
            p.set(value=1)
        finally:
            p.close()

        ss.ensure_metric_phase_cache(state, metric)
        ss._update_cache_entry(  # noqa: SLF001 — same-package state helper
            state, metric,
            phase3_patterns=patterns, phase3_feasibility=feas, phase3_artifact_mode="full",
        )
        with reactive.isolate():
            verification = dict(state.phase3_verification() or {})
        verification[metric] = {
            "finalists": checked,
            "pattern_results": patterns,
            "feasibility_results": feas,
            "verification_status": {sk: "reviewed" for sk in checked},
            "selected_strat": ss.get_metric_curve_stratification(state, metric),
            "justification": "",
        }
        state.phase3_verification.set(verification)
        verify_data.set({"strats": checked, "patterns": patterns, "feasibility": feas})
        st.notify_workspace_refresh(state)

    # ── results (R:466-562) ───────────────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def verify_results_ui():
        vd = verify_data()
        if vd is None:
            return None
        with reactive.isolate():
            metric = state.current_metric()
            strat_config = state.strat_config() or {}
        current_choice = ss.get_metric_curve_stratification(state, metric)
        current_label = ss.get_metric_curve_strat_label(state, metric, current_choice)

        phase1_state = ss.get_metric_phase1_display_state(state, metric)
        boxplot_section = None
        if phase1_state is not None:
            specs = {
                sk: spec
                for sk, spec in (phase1_state.get("plot_specs") or {}).items()
                if sk in set(vd["strats"]) and spec is not None
            }
            if specs:
                for sk in specs:
                    _register_boxplot(sk)
                boxplot_section = ui.navset_card_tab(
                    *[
                        ui.nav_panel(
                            (strat_config.get(sk) or {}).get("display_name") or sk,
                            ui.output_plot(ns(f"bp_{sk}"), height="450px"),
                        )
                        for sk in specs
                    ],
                    title="Focused Boxplots",
                )

        patterns = vd.get("patterns") or {}
        pattern_section = None
        pr = patterns.get("results")
        if pr is not None and len(pr) > 0:
            with reactive.isolate():
                predictor_config = state.predictor_config() or {}
            df = pr.copy()
            df["Stratification"] = [
                "(Unstratified)" if sk == "none"
                else (strat_config.get(sk) or {}).get("display_name") or sk
                for sk in df["stratification"].astype(str)
            ]
            df["Predictor"] = [
                (predictor_config.get(pk) or {}).get("display_name") or pk
                for pk in df["predictor"].astype(str)
            ]
            df["LOESS R-sq"] = df["loess_r_squared"].round(3)
            show = df[
                ["Stratification", "Predictor", "pattern_shape", "LOESS R-sq",
                 "n_sign_changes", "stability_rating"]
            ].rename(
                columns={
                    "pattern_shape": "Shape",
                    "n_sign_changes": "Sign Changes",
                    "stability_rating": "Stability",
                }
            )
            pattern_section = ui.card(
                ui.card_header("Pattern Stability"),
                ui.card_body(_styled_table(show, flag_col="Stability")),
            )

        feas = vd.get("feasibility")
        feas_section = None
        if feas is not None and len(feas) > 0:
            df = feas.copy()
            df["Stratification"] = [
                (strat_config.get(sk) or {}).get("display_name") or sk
                for sk in df["stratification"].astype(str)
            ]
            show = df[
                ["Stratification", "n_levels", "min_group_n", "max_group_n",
                 "pct_sparse_cells", "data_completeness_pct", "feasibility_flag"]
            ].rename(
                columns={
                    "n_levels": "Levels",
                    "min_group_n": "Min Group n",
                    "max_group_n": "Max Group n",
                    "pct_sparse_cells": "% Sparse",
                    "data_completeness_pct": "Completeness %",
                    "feasibility_flag": "Flag",
                }
            )
            flag_by_sk = dict(zip(feas["stratification"].astype(str), feas["feasibility_flag"]))
            feas_section = ui.TagList(
                ui.card(
                    ui.card_header("Feasibility Assessment"),
                    ui.card_body(_styled_table(show, flag_col="Flag")),
                ),
                ui.card(
                    ui.card_header("Diagnostic Summary"),
                    ui.card_body(
                        *[
                            ui.div(
                                ui.tags.strong(
                                    (strat_config.get(sk) or {}).get("display_name") or sk,
                                    style="min-width: 220px;",
                                ),
                                status_badge(
                                    {
                                        "feasible": "pass",
                                        "marginal": "caution",
                                        "infeasible": "fail",
                                    }.get(str(flag_by_sk.get(sk, "unknown")), "not_applicable"),
                                    str(flag_by_sk.get(sk, "unknown")),
                                ),
                                class_="d-flex align-items-center gap-3 mb-2",
                            )
                            for sk in vd["strats"]
                        ]
                    ),
                ),
            )

        return ui.TagList(
            ui.card(
                ui.card_header("Reference Curve Selection"),
                ui.card_body(
                    ui.tags.p(
                        f"Current stratification used for curves: {current_label}",
                        class_="mb-2",
                    ),
                    ui.tags.p(
                        "Use the Reference curves page to change the final curve "
                        "stratification. This verification tab is advisory only.",
                        class_="text-muted mb-0",
                    ),
                ),
                class_="border-info mb-3",
            ),
            boxplot_section,
            pattern_section,
            feas_section,
        )

    def _register_boxplot(sk: str):
        if sk in registered_bp:
            return
        registered_bp.add(sk)

        @output(id=f"bp_{sk}", suspend_when_hidden=False)
        @render.plot
        def _bp():
            with reactive.isolate():
                metric = state.current_metric()
                metric_config = state.metric_config() or {}
                strat_config = state.strat_config() or {}
            phase1_state = ss.get_metric_phase1_display_state(state, metric)
            req(phase1_state is not None)
            spec = (phase1_state.get("plot_specs") or {}).get(sk)
            req(spec is not None)
            fig = build_screening_plot_from_spec(
                spec, metric_config, strat_config,
                font_profile="large_analysis", show_points=False,
            )
            req(fig is not None)
            return fig

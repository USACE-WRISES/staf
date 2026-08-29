"""Phase 2 — Cross-Metric Consistency workspace. Port of
app/modules/mod_phase2_consistency.R.

py-shiny simplifications (per the port plan): the refresh runs as a kept-
reference async task with explicit flush points; the loading toast closes when
the recompute's flush completes (R's watchPhase2ResultsReady DOM polling and
its later::later timeout chain are dropped). The R module's carry-forward
observer is dead code (no UI renders those inputs) and is not ported.
The heatmap is rebuilt here with plotnine (the domain port returns data only).
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import date

import pandas as pd
from plotnine import aes, element_text, geom_tile, ggplot, labs, scale_fill_manual, theme
from shiny import module, reactive, render, req, ui

from streamcurves.plot_theme import minimal_plot_theme
from views import state as st
from views import summary_state as ss
from views.state import AppState
from views.theme import fa
from views.uihelpers import (
    no_data_alert,
    remove_final_loading_notification,
    show_final_loading_notification,
    status_badge,
)

logger = logging.getLogger("streamcurves")

TIER_BG = {
    "Broad-Use Candidate": "rgba(39,174,96,0.15)",
    "Metric-Specific Candidate": "rgba(243,156,18,0.15)",
    "Weak Candidate": "rgba(231,76,60,0.15)",
}


def heatmap_px(n_metrics: int) -> int:
    """Plot height for the consistency heatmap: a ~26px row band per metric
    plus title/legend/x-axis chrome, clamped so two metrics still get a
    readable plot and sixty do not become a wall. The container (results_ui)
    and the PNG download both size from this, so screen and export agree."""
    return max(320, min(26 * int(n_metrics or 0) + 170, 1200))


def _short_labels(labels, max_len: int = 30) -> dict[str, str]:
    """Truncate long display names with an ellipsis, uniquified so ggplot can
    never merge two metrics that truncate identically."""
    out: dict[str, str] = {}
    used: dict[str, int] = {}
    for lbl in dict.fromkeys(str(x) for x in labels):
        short = lbl if len(lbl) <= max_len else lbl[: max_len - 1].rstrip() + "…"
        n = used.get(short, 0)
        used[short] = n + 1
        out[lbl] = short if n == 0 else f"{short} ({n + 1})"
    return out


def build_consistency_heatmap(result: dict, metric_config: dict, strat_config: dict,
                              sig_threshold: float):
    """plotnine port of the R/05d heatmap (green/red significance tiles)."""
    wide = (result or {}).get("consistency_matrix")
    if wide is None or len(wide) == 0:
        return None
    long = wide.melt(id_vars="metric", var_name="stratification", value_name="cell_value")
    long["metric_label"] = [
        (metric_config.get(m) or {}).get("display_name") or m for m in long["metric"]
    ]
    long["strat_label"] = [
        (strat_config.get(s) or {}).get("display_name") or s for s in long["stratification"]
    ]
    # Resolved names can run long; unabridged they collide on the y axis as
    # soon as the metric list grows. Truncated + uniquified, full name in the
    # downloadable CSV.
    short_m = _short_labels(long["metric_label"])
    long["metric_label"] = [short_m[x] for x in long["metric_label"]]
    short_s = _short_labels(long["strat_label"], max_len=24)
    long["strat_label"] = [short_s[x] for x in long["strat_label"]]
    long["status"] = [
        None if pd.isna(v) else ("Significant" if v == 1 else "Not Significant")
        for v in long["cell_value"]
    ]
    fig = (
        ggplot(long, aes(x="strat_label", y="metric_label", fill="status"))
        + geom_tile(color="white", size=0.5)
        + scale_fill_manual(
            values={"Significant": "#27ae60", "Not Significant": "#e74c3c"},
            na_value="#ecf0f1",
            name="Result",
        )
        + labs(
            title="Stratification Consistency Across Metrics",
            subtitle=f"Green = significant (p < {sig_threshold}), Red = not significant",
            x="Stratification Variable",
            y="Metric",
        )
        + minimal_plot_theme(
            profile="large_analysis",
            axis_text_x_angle=30,
            axis_text_x_hjust="right",
            panel_grid_blank=True,
        )
    )
    # Data-driven size override on top of the shared profile: 14pt y labels
    # stop fitting their 26px row bands past ~a dozen metrics.
    n = long["metric_label"].nunique()
    if n > 20:
        fig = fig + theme(axis_text_y=element_text(size=9))
    elif n > 12:
        fig = fig + theme(axis_text_y=element_text(size=11))
    return fig


def tier_card(tier_name: str, color: str, tier_data: pd.DataFrame):
    n = len(tier_data)
    strats = ", ".join(tier_data["stratification"].astype(str)) if n > 0 else "None"
    header_cls = f"bg-{color}" + (" text-white" if color != "warning" else " text-dark")
    return ui.card(
        ui.card_header(tier_name, class_=header_cls),
        ui.card_body(
            ui.tags.p(f"{n} stratification(s)"),
            ui.tags.p(strats, class_="text-muted", style="font-size: 0.85rem;"),
        ),
        class_=f"border-{color}",
    )


@module.ui
def phase2_ui():
    return ui.TagList(
        ui.output_ui("consistency_ui", class_="phase2-consistency-shell-output"),
    )


@module.server
def phase2_server(input, output, session, state: AppState, workspace_scope: str = "standalone"):
    ns = session.ns
    _tasks: set[asyncio.Task] = set()

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    def workspace_active(isolate_state: bool = False) -> bool:
        return st.workspace_scope_is_active(
            state, workspace_scope, standalone_modal_type="phase2",
            isolate_state=isolate_state,
        )

    consistency_results = reactive.value(None)
    matrix_loading = reactive.value(False)
    controls_ready = reactive.value(False)
    applied_settings = reactive.value(None)
    programmatic_strat_update = reactive.value(False)
    loading_notification_id = ns("consistency_matrix_loading")

    def resolve_settings(settings=None) -> dict:
        return ss.normalize_phase2_settings(state, settings)

    def input_settings() -> dict:
        def _get(name):
            try:
                return input[name]()
            except Exception:
                return None

        return {
            "metric_filter": list(_get("metric_filter") or []),
            "strat_filter": list(_get("strat_filter") or []),
            "sig_threshold": _get("sig_threshold"),
            "support_threshold": _get("support_threshold"),
        }

    def inputs_bound() -> bool:
        try:
            return (
                input.metric_filter() is not None
                and input.strat_filter() is not None
                and input.sig_threshold() is not None
                and input.support_threshold() is not None
            )
        except Exception:
            return False

    def settings_match(left, right) -> bool:
        if left is None or right is None:
            return False
        return resolve_settings(left) == resolve_settings(right)

    # ── refresh runner (replaces R:412-556) ──────────────────────────────────
    async def _run_refresh(mode: str, settings: dict, error_prefix: str):
        detail = {
            "manual_full": "Loading page, please wait. Computing the consistency matrix "
            "and updating the support score heatmap, ranked summary, tier "
            "classification, and current metric highlights.",
            "auto_full": "Updating the consistency matrix and cross-metric results for "
            "the current filters.",
            "tier_only": "Updating the tier classification and current metric highlights "
            "from the current matrix.",
        }.get(mode, "Updating cross-metric analysis.")
        show_final_loading_notification(
            loading_notification_id, "Loading cross-metric analysis", detail,
            close_button=False,
        )
        await st.task_flush()
        await asyncio.sleep(0)
        try:
            if mode == "tier_only":
                ss.refresh_phase2_ranking_shared(state, settings, persist_settings=False)
            else:
                ss.recompute_phase2_shared(state, settings, persist_settings=False)
            with reactive.isolate():
                consistency_results.set(state.cross_metric_consistency())
            if workspace_scope == "analysis":
                with reactive.isolate():
                    request_id = state.analysis_tab_request_id()
                if (
                    workspace_active(isolate_state=True)
                    and st.analysis_tab_request_is_current(state, request_id)
                    and st.get_analysis_tab_status(state, "cross_metric") == "loading"
                ):
                    st.complete_analysis_tab_preload(state, "cross_metric", "ready", request_id)
            st.notify_workspace_refresh(state)
        except Exception as e:  # noqa: BLE001
            logger.exception("phase2 refresh failed")
            ui.notification_show(f"{error_prefix}: {e}", type="error", duration=8)
            if workspace_scope == "analysis":
                with reactive.isolate():
                    request_id = state.analysis_tab_request_id()
                if st.get_analysis_tab_status(state, "cross_metric") == "loading":
                    st.complete_analysis_tab_preload(state, "cross_metric", "error", request_id)
        finally:
            matrix_loading.set(False)
            remove_final_loading_notification(loading_notification_id)
            await st.task_flush()

    def request_consistency_refresh(mode: str = "manual_full", settings=None,
                                    error_prefix: str | None = None) -> bool:
        if not workspace_active(isolate_state=True):
            return False
        resolved = resolve_settings(settings)
        error_prefix = error_prefix or {
            "manual_full": "Consistency computation failed",
            "auto_full": "Cross-metric analysis update failed",
            "tier_only": "Tier classification update failed",
        }.get(mode, "Cross-metric analysis update failed")

        with reactive.isolate():
            if mode != "manual_full" and settings_match(resolved, applied_settings()):
                return False
            if matrix_loading():
                applied_settings.set(resolved)
                return False
            applied_settings.set(resolved)
            matrix_loading.set(True)
        _launch(_run_refresh(mode, resolved, error_prefix))
        return True

    # ── analysis-scope tab preload (R:45-79) ─────────────────────────────────
    @reactive.effect
    @reactive.event(state.analysis_tab_preload_nonce, ignore_init=True)
    def _preload():
        if workspace_scope != "analysis" or not workspace_active():
            return
        with reactive.isolate():
            if state.analysis_tab_preload_tab() != "cross_metric":
                return
            request_id = state.analysis_tab_request_id()
        if not st.analysis_tab_request_is_current(state, request_id):
            return
        available = ss.get_phase2_metric_choices(state)
        with reactive.isolate():
            needs_compute = (
                state.cross_metric_consistency() is None or state.phase2_ranking() is None
            )
        if len(available) >= 2 and needs_compute:
            request_consistency_refresh(mode="auto_full", settings=resolve_settings())
            return
        st.set_analysis_tab_status(state, "cross_metric", "ready", request_id)
        st.complete_analysis_tab_preload(state, "cross_metric", "ready", request_id)

    # ── controls-ready lifecycle (R:558-593) ─────────────────────────────────
    @reactive.effect
    @reactive.event(
        state.workspace_modal_nonce, state.phase2_settings, state.data,
        state.all_layer1_results, state.config_version,
        ignore_init=False, ignore_none=False,
    )
    def _reset_controls_state():
        controls_ready.set(False)
        programmatic_strat_update.set(False)
        applied_settings.set(None)

    @reactive.effect
    def _bind_controls():
        if not inputs_bound():
            return
        with reactive.isolate():
            if controls_ready():
                return
            applied_settings.set(resolve_settings(input_settings()))
            controls_ready.set(True)
            programmatic_strat_update.set(False)

    @reactive.effect
    @reactive.event(state.cross_metric_consistency, ignore_init=False, ignore_none=False)
    def _sync_results():
        consistency_results.set(state.cross_metric_consistency())

    # ── control observers (R:599-708) ────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.compute_matrix)
    def _compute():
        req(len(ss.get_phase2_metric_choices(state)) >= 2)
        req(input.metric_filter(), input.strat_filter())
        request_consistency_refresh("manual_full", resolve_settings(input_settings()))

    @reactive.effect
    @reactive.event(input.support_threshold, ignore_init=True)
    def _support_change():
        with reactive.isolate():
            if not controls_ready():
                return
            res = state.cross_metric_consistency()
        req(res is not None and (res or {}).get("summary") is not None)
        request_consistency_refresh("tier_only", resolve_settings(input_settings()))

    @reactive.effect
    @reactive.event(input.sig_threshold, ignore_init=True)
    def _sig_change():
        with reactive.isolate():
            if not controls_ready():
                return
            has_results = consistency_results() is not None
        if not has_results:
            return
        request_consistency_refresh("auto_full", resolve_settings(input_settings()))

    @reactive.effect
    @reactive.event(input.metric_filter, ignore_init=True)
    def _metric_filter_change():
        with reactive.isolate():
            if not controls_ready():
                return
        resolved = resolve_settings(input_settings())
        with reactive.isolate():
            if settings_match(resolved, applied_settings()):
                return
            strat_config = state.strat_config() or {}
        strat_choices = ss.get_phase2_strat_choices(state, resolved["metric_filter"])
        labels = {
            sk: (strat_config.get(sk) or {}).get("display_name") or sk for sk in strat_choices
        }
        current_sel = sorted(input.strat_filter() or [])
        if current_sel != sorted(resolved["strat_filter"]):
            programmatic_strat_update.set(True)
        ui.update_selectize(
            "strat_filter", choices=labels, selected=resolved["strat_filter"]
        )
        with reactive.isolate():
            has_results = consistency_results() is not None
        if has_results:
            request_consistency_refresh("auto_full", resolved)

    @reactive.effect
    @reactive.event(input.strat_filter, ignore_init=True)
    def _strat_filter_change():
        with reactive.isolate():
            if not controls_ready():
                return
            if programmatic_strat_update():
                programmatic_strat_update.set(False)
                return
            has_results = consistency_results() is not None
        if not has_results:
            return
        request_consistency_refresh("auto_full", resolve_settings(input_settings()))

    # ── main UI (R:110-192) ───────────────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def consistency_ui():
        if not workspace_active():
            return None
        if state.data() is None:
            return no_data_alert()
        state.all_layer1_results()  # re-render when screening lands
        settings = ss.normalize_phase2_settings(state)
        with reactive.isolate():
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}
        metric_choices_keys = ss.get_phase2_metric_choices(state)
        n_metrics = len(metric_choices_keys)
        if n_metrics < 2:
            return ui.div(
                fa("circle-info"),
                f" Complete exploratory screening for at least 2 metrics to enable this "
                f"view. Currently completed: {n_metrics} metric(s).",
                class_="alert alert-info mt-3",
            )
        metric_choices = {
            mk: (metric_config.get(mk) or {}).get("display_name") or mk
            for mk in metric_choices_keys
        }
        strat_choices_keys = ss.get_phase2_strat_choices(state, settings["metric_filter"])
        strat_choices = {
            sk: (strat_config.get(sk) or {}).get("display_name") or sk
            for sk in strat_choices_keys
        }
        return ui.TagList(
            ui.output_ui(ns("current_metric_context")),
            ui.card(
                ui.card_header("Analysis Controls"),
                ui.card_body(
                    # heights_equal="row" + fill=False: the bslib defaults
                    # ("all", fill) stretch the slider row to the tall
                    # selectize row and let the grid eat the modal's leftover
                    # height, which opened a dead band above the button.
                    ui.layout_column_wrap(
                        ui.input_selectize(
                            ns("metric_filter"), "Metrics to Include:",
                            choices=metric_choices, selected=settings["metric_filter"],
                            multiple=True,
                            options={"dropdownParent": "body", "plugins": ["remove_button"]},
                        ),
                        ui.input_selectize(
                            ns("strat_filter"), "Stratifications to Include:",
                            choices=strat_choices, selected=settings["strat_filter"],
                            multiple=True,
                            options={"dropdownParent": "body", "plugins": ["remove_button"]},
                        ),
                        ui.input_slider(
                            ns("sig_threshold"), "Significance Level (p-value):",
                            min=0.01, max=0.10, value=settings["sig_threshold"], step=0.01,
                        ),
                        ui.input_slider(
                            ns("support_threshold"), "Strong Support Cutoff:",
                            min=0.1, max=0.9, value=settings["support_threshold"], step=0.05,
                        ),
                        width=1 / 2,
                        heights_equal="row",
                        fill=False,
                    ),
                    fillable=False,
                ),
                ui.card_footer(
                    ui.input_action_button(
                        ns("compute_matrix"),
                        ui.TagList(fa("table"), " Compute Consistency Matrix"),
                        class_="btn btn-primary",
                    ),
                    class_="text-end",
                ),
                fill=False,
                class_="phase2-controls-card",
            ),
            ui.output_ui(ns("results_ui"), class_="phase2-consistency-results-output"),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def current_metric_context():
        if not workspace_active():
            return None
        metric = state.current_metric()
        req(metric)
        with reactive.isolate():
            metric_config = state.metric_config() or {}
        state.phase2_ranking()  # refresh highlights when ranking changes
        label = (metric_config.get(metric) or {}).get("display_name") or metric
        highlighted = ss.get_metric_phase2_passed(state, metric)
        highlighted_label = (
            "None highlighted yet"
            if not highlighted
            else ", ".join(ss.get_strat_display_name(state, sk) for sk in highlighted)
        )
        return ui.div(
            ui.tags.strong("Current metric context: "),
            label,
            ui.tags.br(),
            ui.tags.span("Broad-use candidates available to this metric: "),
            highlighted_label,
            ui.tags.br(),
            ui.tags.span("Current curve recommendation: "),
            ss.get_metric_curve_strat_label(state, metric),
            class_="alert alert-info mb-3",
        )

    # ── results (R:735-819) ───────────────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def results_ui():
        if not workspace_active():
            return None
        res = consistency_results()
        req(res is not None)
        ranking = state.phase2_ranking()
        with reactive.isolate():
            metric = state.current_metric()

        tier_section = None
        highlights_section = None
        if ranking is not None and len(ranking) > 0:
            tier_section = ui.div(
                ui.card(
                    ui.card_header("Tier Classification"),
                    ui.card_body(
                        ui.layout_column_wrap(
                            tier_card(
                                "Broad-Use Candidate", "success",
                                ranking[ranking["tier"] == "Broad-Use Candidate"],
                            ),
                            tier_card(
                                "Metric-Specific Candidate", "warning",
                                ranking[ranking["tier"] == "Metric-Specific Candidate"],
                            ),
                            tier_card(
                                "Weak Candidate", "danger",
                                ranking[ranking["tier"] == "Weak Candidate"],
                            ),
                            width=1 / 3,
                        )
                    ),
                ),
                id=ns("phase2_tier_classification"),
            )
            highlighted = ss.get_metric_phase2_passed(state, metric)
            highlights_section = ui.div(
                ui.card(
                    ui.card_header("Current Metric Highlights"),
                    ui.card_body(
                        ui.tags.p(
                            "Broad-use candidates are surfaced automatically here for the "
                            "current metric. Choose the final curve stratification on the "
                            "Reference curves page.",
                            class_="text-muted",
                        ),
                        (
                            ui.div(
                                # This card only renders once a ranking exists, so
                                # "never ran" is unreachable here: either the metric
                                # has no stratification to rank, or nothing reached
                                # broad-use in the run that did happen.
                                "Cross-metric analysis needs at least one stratification "
                                "for this metric. None are available."
                                if not ss.get_metric_allowed_strats(state, metric)
                                else "No stratification reached broad-use for this metric "
                                     "in this run.",
                                class_="text-muted",
                            )
                            if not highlighted
                            else ui.TagList(
                                *[
                                    ui.div(
                                        ui.tags.strong(
                                            ss.get_strat_display_name(state, sk),
                                            style="min-width: 220px;",
                                        ),
                                        status_badge("pass", "Broad-Use Candidate"),
                                        class_="d-flex align-items-center gap-3 mb-2",
                                    )
                                    for sk in highlighted
                                ]
                            )
                        ),
                    ),
                ),
                id=ns("phase2_current_metric_highlights"),
            )

        matrix = (res or {}).get("consistency_matrix")
        n_hm = 0 if matrix is None else len(matrix)
        return ui.div(
            ui.card(
                ui.card_header("Support Score Heatmap"),
                ui.card_body(
                    ui.tags.p(
                        ui.output_text(ns("sig_threshold_label"), inline=True),
                        class_="text-muted small",
                    ),
                    # Sized to the metric count so row bands stay readable
                    # (the fixed 450px squeezed 20+ metrics into overlap).
                    ui.output_plot(ns("heatmap"), height=f"{heatmap_px(n_hm)}px"),
                    class_="heatmap-container",
                ),
            ),
            ui.card(
                ui.card_header("Ranked Stratification Summary"),
                ui.card_body(ui.output_ui(ns("ranking_table"))),
            ),
            tier_section,
            highlights_section,
            ui.div(
                ui.download_button(
                    ns("dl_matrix"), "Download Consistency Matrix CSV",
                    class_="btn btn-outline-primary",
                ),
                ui.download_button(
                    ns("dl_heatmap"), "Download Heatmap PNG",
                    class_="btn btn-outline-primary ms-2",
                ),
                class_="mt-3",
            ),
            id=ns("phase2_results_root"),
            class_="phase2-consistency-results",
        )

    @output(suspend_when_hidden=False)
    @render.text
    def sig_threshold_label():
        req(workspace_active())
        settings = ss.normalize_phase2_settings(state)
        try:
            sig = input.sig_threshold() or settings["sig_threshold"]
        except Exception:
            sig = settings["sig_threshold"]
        return f"Cells colored by p-value significance at α = {sig}"

    @output(suspend_when_hidden=False)
    @render.plot
    def heatmap():
        req(workspace_active())
        res = consistency_results()
        req(res is not None)
        with reactive.isolate():
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}
            settings = ss.normalize_phase2_settings(state)
        fig = build_consistency_heatmap(
            res, metric_config, strat_config, settings["sig_threshold"]
        )
        req(fig is not None)
        return fig

    @output(suspend_when_hidden=False)
    @render.ui
    def ranking_table():
        req(workspace_active())
        ranking = state.phase2_ranking()
        req(ranking is not None and len(ranking) > 0)
        with reactive.isolate():
            strat_config = state.strat_config() or {}
        df = ranking.copy().sort_values("consistency_score", ascending=False)
        df["Stratification"] = [
            (strat_config.get(sk) or {}).get("display_name") or sk
            for sk in df["stratification"]
        ]
        rows = []
        for _, r in df.iterrows():
            rows.append(
                ui.tags.tr(
                    ui.tags.td(r["Stratification"]),
                    ui.tags.td(str(int(r["n_metrics_tested"]))),
                    ui.tags.td(str(int(r["n_promising"]))),
                    ui.tags.td(str(int(r["n_possible"]))),
                    ui.tags.td(f"{r['mean_effect_size']:.4f}"),
                    ui.tags.td(f"{r['consistency_score']:.3f}"),
                    ui.tags.td(
                        r["tier"],
                        style=f"background-color: {TIER_BG.get(r['tier'], 'transparent')};",
                    ),
                )
            )
        return ui.tags.table(
            ui.tags.thead(
                ui.tags.tr(
                    *[
                        ui.tags.th(h)
                        for h in [
                            "Stratification", "Metrics Tested", "# Promising",
                            "# Possible", "Mean Effect Size", "Consistency Score", "Tier",
                        ]
                    ]
                )
            ),
            ui.tags.tbody(*rows),
            class_="table table-sm table-striped compact",
        )

    # ── downloads (R:884-904) ─────────────────────────────────────────────────
    @render.download(filename=lambda: f"strat_consistency_matrix_{date.today():%Y%m%d}.csv")
    def dl_matrix():
        with reactive.isolate():
            res = consistency_results()
        matrix = (res or {}).get("consistency_matrix")
        if matrix is not None and len(matrix) > 0:
            yield matrix.to_csv(index=False).encode("utf-8")
        else:
            yield b"message\nNo data\n"

    @render.download(filename=lambda: f"strat_consistency_heatmap_{date.today():%Y%m%d}.png")
    def dl_heatmap():
        with reactive.isolate():
            res = consistency_results()
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}
            settings = ss.normalize_phase2_settings(state)
        fig = build_consistency_heatmap(
            res, metric_config, strat_config, settings["sig_threshold"]
        )
        req(fig is not None)
        # Same geometry the on-screen plot uses (heatmap_px at ~96 dpi), so
        # the export never re-crowds what the screen shows readably.
        matrix = (res or {}).get("consistency_matrix")
        n_m = 0 if matrix is None else len(matrix)
        n_s = 0 if matrix is None else max(0, matrix.shape[1] - 1)
        buf = io.BytesIO()
        fig.save(buf, width=min(16, max(8, 1.1 * n_s + 4)),
                 height=min(12.5, max(3.5, heatmap_px(n_m) / 96)),
                 dpi=300, verbose=False)
        yield buf.getvalue()

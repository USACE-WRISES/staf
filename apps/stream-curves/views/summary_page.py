"""Reference Curves summary page — port of app/modules/mod_summary_page.R.

The cross-metric tracking mega-table: one hand-rolled HTML row per metric
(py-shiny DataGrid can't host inputs), with per-row expand/detail, the two
stratification selects, Open Analysis / Recompute actions, and the bulk
recompute flow with manual-curve protection.

py-shiny adaptations (see PORTING.md):
- per-row snapshot/expanded/busy state lives in per-metric reactive.Values
  (created at registration) to keep R's per-row invalidation granularity;
- the row action buttons use one delegated onclick channel
  (``summary_row_action``) instead of 4xN observers;
- recomputes run inside async effects with an initial event-loop yield so the
  busy states paint; per-step progress paints in bursts between computations.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves import curve_automation as ca
from streamcurves import run_state as rs
from views import state as st
from views import summary_state as ss
from views.curve_plots import build_overlay_curve_plot, build_reference_curve_plot
from views.state import AppState
from views.theme import fa
from views.uihelpers import (
    no_data_alert,
    remove_final_loading_notification,
    show_final_loading_notification,
)

logger = logging.getLogger("streamcurves")

# curve_review status -> short badge text for the flagged-review queue.
_REVIEW_STATUS_LABELS = {
    rs.CURVE_STATUS_INSUFFICIENT: "Insufficient data",
    rs.CURVE_STATUS_DEGENERATE: "Degenerate curve",
    rs.CURVE_STATUS_UNMAPPED: "Unmapped metric",
    rs.CURVE_STATUS_STRAT_REVIEW: "Stratification review",
    rs.CURVE_STATUS_ERROR: "Build error",
}


class SummaryProgress:
    """Port of the module's make_progress_notifier (R:55-86)."""

    def __init__(self, state: AppState, total_steps: int, message: str):
        self._state = state
        self._message = message
        self._step = 0
        self._progress = ui.Progress(min=0, max=max(int(total_steps), 1))
        self._progress.set(value=0, message=message)

    def _detail(self, stage, metric, index, total):
        with reactive.isolate():
            mc = self._state.metric_config() or {}
        label = (mc.get(metric) or {}).get("display_name") or metric if metric else None
        return {
            "phase1": f"Exploratory 1/4: Screening {index}/{total} - {label}",
            "phase2": "Cross-Metric Analysis 2/4: Updating consistency results",
            "phase3": f"Verification 3/4: Refreshing diagnostics {index}/{total} - {label}",
            "phase4": f"Reference Curves 4/4: Building curve {index}/{total} - {label}",
        }.get(stage, "Working...")

    def update(self, stage, metric=None, index=1, total=1, stage_state="start"):
        detail = self._detail(stage, metric, index, total)
        if stage_state != "start":
            self._step += 1
        self._progress.set(value=self._step, message=self._message, detail=detail)

    def close(self):
        self._progress.close()


@module.ui
def summary_page_ui():
    return ui.output_ui("summary_page")


@module.server
def summary_page_server(input, output, session, state: AppState):
    ns = session.ns

    registered: set[str] = set()
    _tasks: set[asyncio.Task] = set()  # keep refs; fire-and-forget tasks get GC'd

    def _launch(coro):
        task = asyncio.create_task(coro)
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        return task

    row_snapshot: dict[str, reactive.Value] = {}
    row_signature: dict[str, dict | None] = {}
    row_expanded: dict[str, reactive.Value] = {}
    row_busy: dict[str, reactive.Value] = {}
    bulk_recompute_active = reactive.value(False)
    bulk_recompute_phase = reactive.value("idle")
    pending_bulk_recompute = reactive.value(None)
    pending_row_recompute = reactive.value(None)
    pending_review = reactive.value(None)  # {"metric", "decision"} awaiting a rationale

    @reactive.calc
    def summary_metrics() -> list[str]:
        return ss.eligible_summary_metrics(state.metric_config() or {})

    # ── row snapshot refresh (R:121-149) ─────────────────────────────────────
    def refresh_metric_row(metric: str, context=None, force: bool = False):
        with reactive.isolate():
            metrics = summary_metrics()
        if metric not in metrics or metric not in row_snapshot:
            return
        resolved = context if context is not None else ss.build_summary_snapshot_context(state)
        next_sig = ss.build_metric_summary_snapshot_signature(state, metric, context=resolved)
        if not force and row_signature.get(metric) == next_sig:
            return
        row_snapshot[metric].set(ss.build_metric_summary_snapshot(state, metric, context=resolved))
        row_signature[metric] = next_sig

    def refresh_metric_rows(metrics=None, force: bool = False):
        with reactive.isolate():
            if metrics is None:
                metrics = summary_metrics()
        context = ss.build_summary_snapshot_context(state)
        for metric in metrics:
            refresh_metric_row(metric, context=context, force=force)

    def set_row_busy(metric: str, value: bool = True):
        if metric in row_busy:
            row_busy[metric].set(bool(value))

    # ── recompute runners (R:151-262) ────────────────────────────────────────
    async def run_row_recompute(metric: str):
        set_row_busy(metric, True)
        await st.task_flush()
        await asyncio.sleep(0)
        with reactive.isolate():
            mc = state.metric_config() or {}
        label = (mc.get(metric) or {}).get("display_name") or metric
        progress = SummaryProgress(state, total_steps=4, message=f"Recomputing {label}")
        try:
            ss.recompute_metric_from_summary(
                state, metric, refresh_phase2=True, mode="summary",
                progress_cb=lambda phase, m, i, n, stage: progress.update(phase, m, i, n, stage),
            )
            ss.set_metric_summary_edit_notes(state, metric, "Reference Curves", [])
            # Re-score this metric's curve so a manual tweak updates the flagged
            # review queue (without clobbering a reviewer decision on a no-op).
            ca.sync_curve_review_after_recompute(state, [metric])
        except Exception as e:  # noqa: BLE001
            logger.exception("row recompute failed")
            ui.notification_show(
                f"Recompute failed for {label}: {e}", type="error", duration=8
            )
        finally:
            progress.close()
        refresh_metric_rows()
        set_row_busy(metric, False)
        await st.task_flush()

    async def run_bulk_recompute(metrics: list[str]):
        with reactive.isolate():
            eligible = summary_metrics()
        metrics = [m for m in (metrics or []) if m in eligible]
        if not metrics:
            ui.notification_show(
                "No rows selected for recompute. Manual curves were preserved.",
                type="message", duration=4,
            )
            refresh_metric_rows()
            return

        remove_final_loading_notification(ns("bulk_refresh_final_loading"))
        bulk_recompute_active.set(True)
        bulk_recompute_phase.set("running")
        for metric in metrics:
            set_row_busy(metric, True)
        await st.task_flush()
        await asyncio.sleep(0)

        progress = SummaryProgress(
            state, total_steps=len(metrics) * 3 + 1, message="Recomputing reference curves"
        )
        try:
            ss.recompute_metrics_from_summary(
                state, metrics, mode="summary",
                progress_cb=lambda phase, m, i, n, stage: progress.update(phase, m, i, n, stage),
                on_metric_done=lambda metric: (
                    ss.set_metric_summary_edit_notes(state, metric, "Reference Curves", []),
                    set_row_busy(metric, False),
                ),
            )
            ca.sync_curve_review_after_recompute(state, metrics)
        except Exception as e:  # noqa: BLE001
            logger.exception("bulk recompute failed")
            ui.notification_show(f"Recompute all failed: {e}", type="error", duration=8)

        bulk_recompute_phase.set("refreshing")
        progress.close()
        show_final_loading_notification(
            ns("bulk_refresh_final_loading"),
            "Recomputing reference curves",
            "Loading page, please wait. Refreshing reference curve analysis table.",
        )
        await st.task_flush()
        await asyncio.sleep(0)
        try:
            refresh_metric_rows()
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Reference Curves refresh failed: {e}", type="error", duration=8
            )
        remove_final_loading_notification(ns("bulk_refresh_final_loading"))
        bulk_recompute_active.set(False)
        bulk_recompute_phase.set("idle")
        for metric in metrics:
            set_row_busy(metric, False)
        await st.task_flush()

    # ── note/detail builders (R:348-490) ─────────────────────────────────────
    def note_group_ui(title: str, items: list[dict]):
        if not items:
            body = ui.div("No notes.", class_="summary-note-empty")
        else:
            body = ui.tags.ul(
                *[
                    ui.tags.li(
                        item["text"],
                        class_=f"summary-note-item summary-note-{item['level']}",
                    )
                    for item in items
                ],
                class_="summary-note-list",
            )
        return ui.div(
            ui.div(ui.tags.h6(title), class_="summary-note-heading"),
            body,
            class_="summary-note-group",
        )

    def plain_table_ui(df: pd.DataFrame):
        if df is None or len(df) == 0:
            return ui.div("No values available.", class_="text-muted")
        return ui.tags.table(
            ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
            ui.tags.tbody(
                *[
                    ui.tags.tr(*[ui.tags.td(str(v)) for v in row])
                    for row in df.itertuples(index=False)
                ]
            ),
            class_="table table-sm table-striped summary-detail-table",
        )

    def detail_ui(metric: str, row_data: dict):
        notes = row_data["notes"]
        curve_rows = pd.DataFrame(row_data["curve_rows"])

        plot_card = ui.card(
            ui.card_header("Reference Curve"),
            ui.card_body(
                ui.div(
                    "No current reference curve outputs. Use Recompute to build the curve.",
                    class_="text-muted",
                )
                if len(curve_rows) == 0
                else ui.output_plot(ns(f"plot_{metric}"), height="420px")
            ),
            class_="summary-detail-card mb-3",
        )

        if len(curve_rows) <= 1:
            if len(curve_rows) == 0:
                values = ["N/A"] * 7
            else:
                r = curve_rows.iloc[0]
                values = [
                    str(r["n_reference"]),
                    ss.format_summary_number(r["min_val"]),
                    ss.format_summary_number(r["q25"]),
                    ss.format_summary_number(r["q75"]),
                    ss.format_summary_number(r["max_val"]),
                    ss.format_summary_number(r["iqr"]),
                    ss.format_summary_number(r["sd_val"]),
                ]
            stats_df = pd.DataFrame(
                {"Statistic": ["n", "Min", "Q25", "Q75", "Max", "IQR", "SD"], "Value": values}
            )
            body = [plain_table_ui(stats_df)]
            if len(curve_rows) > 0:
                from streamcurves.curves import reference_curve_row_range_display

                body += [
                    ui.tags.hr(),
                    plain_table_ui(
                        pd.DataFrame(
                            {
                                "Category": [
                                    "Functioning", "Functioning-At-Risk", "Non-functioning",
                                ],
                                "Metric Range(s)": [
                                    reference_curve_row_range_display(curve_rows, "functioning"),
                                    reference_curve_row_range_display(curve_rows, "at_risk"),
                                    reference_curve_row_range_display(
                                        curve_rows, "not_functioning"
                                    ),
                                ],
                            }
                        )
                    ),
                ]
            detail_tables = ui.card(
                ui.card_header("Reference Curve Details"),
                ui.card_body(*body),
                class_="summary-detail-card mb-0",
            )
        else:
            detail_tables = ui.card(
                ui.card_header("Reference Curve Details by Stratum"),
                ui.card_body(
                    plain_table_ui(ss.build_stratified_stats_table(curve_rows)),
                    ui.tags.hr(),
                    plain_table_ui(ss.build_stratified_threshold_table(curve_rows)),
                ),
                class_="summary-detail-card mb-0",
            )

        note_card = ui.card(
            ui.card_header("Analysis Summary"),
            ui.card_body(
                ui.navset_card_tab(
                    *[
                        ui.nav_panel(label, note_group_ui(label, notes.get(label, [])))
                        for label in ss.ANALYSIS_STEP_LABELS
                    ],
                    id=ns(f"detail_tabs_{metric}"),
                )
            ),
            class_="summary-detail-card mb-3",
        )

        selected_label = ss.get_metric_curve_strat_label(
            state, metric, row_data["curve_strat_used"]
        )
        return ui.TagList(
            note_card,
            ui.layout_column_wrap(plot_card, detail_tables, width=1 / 2),
            ui.div(
                ui.tags.strong("Stratification used for curves: "),
                selected_label,
                class_="summary-detail-footer",
            ),
        )

    def _action(metric: str, action: str) -> str:
        payload = json.dumps({"metric": metric, "action": action})
        return (
            f"Shiny.setInputValue('{ns('summary_row_action')}',"
            f"{payload.replace(chr(39), chr(92) + chr(39))},"
            "{priority:'event'})"
        )

    def render_metric_row(metric: str, row_data: dict):
        is_expanded = bool(row_expanded[metric]())
        is_busy = bool(row_busy[metric]())
        with reactive.isolate():
            is_locked = is_busy or bool(bulk_recompute_active())

        direction_icon = (
            fa("arrow-up")
            if row_data["direction"] == "Higher is better"
            else fa("arrow-down")
            if row_data["direction"] == "Lower is better"
            else fa("minus")
        )
        available_choices = {
            sk: row_data["strat_label_map"].get(sk, sk) for sk in row_data["available_choices"]
        }

        main_row = ui.tags.tr(
            ui.tags.td(
                ui.tags.button(
                    "▾" if is_expanded else "▸",
                    type="button",
                    class_="btn btn-outline-secondary btn-sm summary-expand-btn",
                    onclick=_action(metric, "toggle"),
                ),
                class_="summary-cell-expand",
            ),
            ui.tags.td(
                ui.div(
                    ui.tags.strong(row_data["display_name"]),
                    (
                        ui.div(row_data["manual_curve_label"], class_="text-warning small mt-1")
                        if row_data["has_manual_curve"]
                        else None
                    ),
                )
            ),
            ui.tags.td(str(row_data["family"])),
            ui.tags.td(str(row_data["units"])),
            ui.tags.td(
                ui.tags.span(
                    direction_icon,
                    class_="summary-direction-icon",
                    title=row_data["direction"],
                ),
                class_="summary-col-direction",
            ),
            ui.tags.td(str(row_data["n_obs"])),
            ui.tags.td(
                ui.tags.span(
                    row_data["status"]["summary_label"],
                    class_=f"summary-status-text {row_data['status']['summary_class']}",
                ),
                class_="summary-col-status",
            ),
            ui.tags.td(
                ui.tags.fieldset(
                    ui.input_selectize(
                        ns(f"available_{metric}"),
                        None,
                        choices=available_choices,
                        selected=list(row_data["available_selected"]),
                        multiple=True,
                        options={"dropdownParent": "body", "plugins": ["remove_button"]},
                    ),
                    **({"disabled": "disabled"} if is_locked else {}),
                ),
                class_="summary-select-cell summary-select-cell-compact summary-picker-cell "
                "summary-col-available",
            ),
            ui.tags.td(
                ui.tags.fieldset(
                    ui.input_select(
                        ns(f"curve_{metric}"),
                        None,
                        choices=row_data["curve_strat_choices"],
                        selected=row_data["curve_strat_used"],
                        width="100%",
                    ),
                    **({"disabled": "disabled"} if is_locked else {}),
                ),
                class_="summary-select-cell summary-col-curve",
            ),
            ui.tags.td(
                ui.div(
                    ui.tags.button(
                        fa("up-right-and-down-left-from-center"),
                        type="button",
                        class_="btn btn-outline-secondary btn-sm summary-action-btn",
                        title="Open analysis",
                        onclick=_action(metric, "open"),
                        **({"disabled": "disabled"} if is_locked else {}),
                    ),
                    ui.tags.button(
                        (
                            ui.tags.span(
                                class_="streamcurves-inline-spinner", aria_hidden="true"
                            )
                            if is_busy
                            else fa("arrows-rotate")
                        ),
                        type="button",
                        class_="btn btn-primary btn-sm summary-action-btn",
                        title="Recompute row",
                        onclick=_action(metric, "recompute"),
                        **({"disabled": "disabled"} if is_locked or is_busy else {}),
                    ),
                    class_="d-flex gap-1",
                ),
                class_="summary-col-actions",
            ),
            class_="summary-main-row",
        )
        detail_row = ui.tags.tr(
            ui.tags.td(detail_ui(metric, row_data) if is_expanded else None, colspan="10"),
            class_="summary-detail-row" if is_expanded else "summary-detail-row d-none",
        )
        return ui.TagList(main_row, detail_row)

    # ── per-metric registration (R:604-711) ──────────────────────────────────
    def _register_metric(metric: str):
        row_snapshot[metric] = reactive.value(None)
        row_expanded[metric] = reactive.value(False)
        row_busy[metric] = reactive.value(False)
        row_signature[metric] = None

        @output(id=f"row_{metric}")
        @render.ui
        def _row():
            row_data = row_snapshot[metric]()
            req(row_data is not None)
            row_busy[metric]()  # busy/expanded flips re-render the row
            row_expanded[metric]()
            return render_metric_row(metric, row_data)

        @output(id=f"plot_{metric}")
        @render.plot
        def _plot():
            row_data = row_snapshot[metric]()
            req(row_data is not None)
            curve_rows = pd.DataFrame(row_data["curve_rows"])
            req(len(curve_rows) > 0)
            with reactive.isolate():
                metric_config = state.metric_config() or {}
            if len(curve_rows) > 1:
                fig = build_overlay_curve_plot(curve_rows, metric_config)
                req(fig is not None)
                return fig
            from streamcurves.curves import reference_curve_points_from_row

            mc = metric_config.get(metric) or {}
            points = reference_curve_points_from_row(
                curve_rows.iloc[[0]], bool(mc.get("higher_is_better"))
            )
            fig = build_reference_curve_plot(
                points, curve_rows.iloc[[0]], metric_config, metric,
                stratum_label=(
                    curve_rows["stratum"].iloc[0]
                    if "stratum" in curve_rows.columns
                    and not pd.isna(curve_rows["stratum"].iloc[0])
                    else None
                ),
            )
            req(fig is not None)
            return fig

        @reactive.effect
        @reactive.event(input[f"available_{metric}"], ignore_init=True)
        def _available():
            selected = list(input[f"available_{metric}"]() or [])
            with reactive.isolate():
                snap = row_snapshot[metric]()
            current = list((snap or {}).get("available_selected") or [])
            if sorted(set(selected)) == sorted(set(current)):
                return
            set_row_busy(metric, True)
            ss.set_metric_available_strats(state, metric, selected)
            refresh_metric_row(metric)
            set_row_busy(metric, False)

        @reactive.effect
        @reactive.event(input[f"curve_{metric}"], ignore_init=True)
        def _curve():
            new_value = input[f"curve_{metric}"]() or "none"
            with reactive.isolate():
                snap = row_snapshot[metric]()
            old_value = (snap or {}).get("curve_strat_used") or "none"
            if new_value == old_value:
                return
            set_row_busy(metric, True)
            ss.set_metric_curve_stratification(state, metric, new_value)
            ss.set_metric_summary_edit_notes(state, metric, "Reference Curves", [])
            refresh_metric_row(metric)
            set_row_busy(metric, False)

    @reactive.effect
    def _register_new_metrics():
        metrics = summary_metrics()
        new = [m for m in metrics if m not in registered]
        for metric in new:
            _register_metric(metric)
            registered.add(metric)
        refresh_metric_rows(metrics)

    # ── row action channel ────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.summary_row_action)  # no ignore_init: first event on a never-set input IS the init run in py-shiny
    def _row_action():
        payload = input.summary_row_action() or {}
        metric = payload.get("metric")
        action = payload.get("action")
        if not metric or metric not in row_snapshot:
            return
        if action == "toggle":
            row_expanded[metric].set(not bool(row_expanded[metric]()))
            return
        with reactive.isolate():
            if bulk_recompute_active():
                return
        if action == "open":
            request_id = st.next_workspace_modal_request_id(state)
            st.launch_workspace_modal(state, "analysis", metric, request_id=request_id)
            return
        if action == "recompute":
            manual_info = ss.get_metric_phase4_manual_curve_info(state, metric)
            if manual_info["has_manual_curve"]:
                pending_row_recompute.set({"metric": metric, "manual_info": manual_info})
                ui.modal_show(
                    ui.modal(
                        ui.tags.p(
                            f"{manual_info['display_name']} has "
                            f"{(manual_info['summary_label'] or 'a manual curve').lower()}."
                        ),
                        ui.tags.p(
                            "Recomputing this row will replace the current manual reference "
                            "curve output with a fresh auto-generated curve."
                        ),
                        title="Overwrite Manual Curve?",
                        footer=ui.TagList(
                            ui.modal_button("Cancel"),
                            ui.input_action_button(
                                ns("confirm_row_recompute"),
                                "Overwrite And Recompute",
                                class_="btn btn-primary",
                            ),
                        ),
                    )
                )
                return
            _launch(run_row_recompute(metric))

    @reactive.effect
    @reactive.event(input.confirm_row_recompute, ignore_init=True)
    def _confirm_row_recompute():
        pending = pending_row_recompute()
        req(pending and pending.get("metric"))
        pending_row_recompute.set(None)
        ui.modal_remove()
        _launch(run_row_recompute(pending["metric"]))

    # ── refresh triggers (R:713-735) ──────────────────────────────────────────
    @reactive.effect
    @reactive.event(state.data, ignore_init=True)
    def _on_data():
        if state.data() is not None:
            refresh_metric_rows()

    @reactive.effect
    @reactive.event(state.precheck_df, state.strat_config, ignore_init=True)
    def _on_config():
        refresh_metric_rows()

    @reactive.effect
    @reactive.event(state.workspace_refresh_nonce, ignore_init=True)
    def _on_refresh_nonce():
        refresh_metric_rows()

    # ── bulk recompute (R:737-761) ────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.recompute_all)
    def _recompute_all():
        with reactive.isolate():
            if bulk_recompute_active():
                return
            metrics = summary_metrics()
        plan = ss.build_summary_recompute_plan(state, metrics)
        if not plan["manual_metrics"]:
            _launch(run_bulk_recompute(metrics))
            return
        pending_bulk_recompute.set(plan)
        manual_choices = {
            row["metric"]: row["selection_label"]
            for _, row in plan["manual_info"].iterrows()
        }
        auto_note = (
            ui.tags.p(
                f"{len(plan['auto_metrics'])} metric(s) without manual curves will "
                "still be recomputed automatically.",
                class_="text-muted mb-3",
            )
            if plan["auto_metrics"]
            else None
        )
        ui.modal_show(
            ui.modal(
                ui.tags.p(
                    "Select which manual-curve metrics you want to recompute. Selected "
                    "metrics will be overwritten with fresh auto-generated reference "
                    "curves. Unchecked manual-curve metrics will be skipped and preserved."
                ),
                auto_note,
                ui.input_checkbox_group(
                    ns("bulk_manual_recompute_metrics"),
                    "Manual-curve metrics to overwrite",
                    choices=manual_choices,
                    selected=[],
                ),
                title="Manual Curves Will Be Overwritten",
                size="l",
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    ui.input_action_button(
                        ns("confirm_bulk_recompute"),
                        "Recompute Selected Rows",
                        class_="btn btn-primary",
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.confirm_bulk_recompute, ignore_init=True)
    def _confirm_bulk():
        plan = pending_bulk_recompute()
        req(plan)
        selected_manual = list(input.bulk_manual_recompute_metrics() or [])
        pending_bulk_recompute.set(None)
        ui.modal_remove()
        _launch(run_bulk_recompute(
                ss.resolve_summary_recompute_metrics(
                    plan["auto_metrics"], plan["manual_metrics"], selected_manual
                )
            )
        )

    @reactive.effect
    @reactive.event(input.open_summary_export, ignore_init=True)
    def _open_export():
        st.launch_workspace_modal(state, "summary_export")

    # ── flagged-curve review queue (inline; was a stage-banner modal) ────────
    def _metric_label(metric: str) -> str:
        with reactive.isolate():
            mc = state.metric_config() or {}
        return (mc.get(metric) or {}).get("display_name") or metric

    def _review_action(metric: str, action: str) -> str:
        payload = json.dumps({"metric": metric, "action": action})
        return (
            f"Shiny.setInputValue('{ns('review_queue_action')}',"
            f"{payload.replace(chr(39), chr(92) + chr(39))},"
            "{priority:'event'})"
        )

    @render.ui
    def review_queue():
        review = state.curve_review() or {}
        flagged = rs.flagged_metrics(review)
        if not flagged:
            return None
        mc = state.metric_config() or {}
        rows = []
        for metric in flagged:
            entry = review.get(metric) or {}
            label = (mc.get(metric) or {}).get("display_name") or metric
            status = entry.get("status") or ""
            reason = (entry.get("reasons") or ["Needs review."])[0]
            rows.append(ui.div(
                ui.div(
                    ui.div(
                        ui.tags.strong(label),
                        ui.tags.span(
                            _REVIEW_STATUS_LABELS.get(status, "Needs review"),
                            class_="badge review-queue-badge",
                        ),
                        class_="d-flex align-items-center gap-2 flex-wrap",
                    ),
                    ui.div(reason, class_="text-muted small"),
                    class_="review-queue-info",
                ),
                ui.div(
                    ui.tags.button(
                        "Adjust and rerun",
                        class_="btn btn-sm btn-outline-primary",
                        onclick=_review_action(metric, "adjust"),
                        title="Open the analysis workspace for this metric",
                    ),
                    ui.tags.button(
                        "Accept",
                        class_="btn btn-sm btn-outline-success",
                        onclick=_review_action(metric, "accept"),
                        title="Accept the proposed curve (rationale required)",
                    ),
                    ui.tags.button(
                        "Remove",
                        class_="btn btn-sm btn-outline-danger",
                        onclick=_review_action(metric, "remove"),
                        title="Remove from the published scope (rationale required)",
                    ),
                    class_="review-queue-actions",
                ),
                class_="review-queue-row",
            ))
        n = len(flagged)
        headline = f"{n} curve needs review" if n == 1 else f"{n} curves need review"
        return ui.div(
            ui.div(
                fa("triangle-exclamation"),
                ui.tags.strong(f" {headline}."),
                ui.tags.span(
                    " Resolve each one: adjust and rerun it, accept it with a "
                    "rationale, or remove it from the published scope.",
                    class_="review-queue-blurb",
                ),
                class_="review-queue-header",
            ),
            *rows,
            class_="review-queue",
        )

    @reactive.effect
    @reactive.event(input.review_queue_action)  # no ignore_init: same rule as summary_row_action
    def _review_queue_action():
        payload = input.review_queue_action() or {}
        metric = payload.get("metric")
        action = payload.get("action")
        if not metric:
            return
        if action == "adjust":
            with reactive.isolate():
                state.current_metric.set(metric)
            request_id = st.next_workspace_modal_request_id(state)
            st.launch_workspace_modal(state, "analysis", metric, request_id=request_id)
            return
        if action not in ("accept", "remove"):
            return
        decision = rs.DECISION_FINALIZED if action == "accept" else rs.DECISION_REMOVED
        pending_review.set({"metric": metric, "decision": decision})
        accepting = action == "accept"
        verb = "Accept" if accepting else "Remove"
        ui.modal_show(ui.modal(
            ui.tags.p(
                f"{verb} {_metric_label(metric)}"
                + ("." if accepting else " from the published scope."),
            ),
            ui.input_text_area(
                ns("review_note"), "Rationale (required)",
                width="100%", height="70px",
            ),
            title=f"{verb} curve",
            easy_close=True,
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button(
                    ns("review_confirm"), verb,
                    class_="btn btn-success" if accepting else "btn btn-danger",
                ),
            ),
        ))

    @reactive.effect
    @reactive.event(input.review_confirm, ignore_init=True)
    def _review_confirm():
        pending = pending_review()
        req(pending and pending.get("metric"))
        note = (input.review_note() or "").strip()
        if not note:
            ui.notification_show(
                "Add a rationale before continuing.", type="warning", duration=5
            )
            return
        metric = pending["metric"]
        decision = pending["decision"]
        pending_review.set(None)
        ca.set_review_decision(state, metric, decision, note=note, actor="reviewer")
        done = "Accepted" if decision == rs.DECISION_FINALIZED else "Removed"
        ui.notification_show(
            f"{done} {_metric_label(metric)}.", type="message", duration=4
        )
        ui.modal_remove()

    @render.ui
    def bulk_refresh_status():
        if bulk_recompute_phase() != "refreshing":
            return None
        return ui.div(
            ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true"),
            ui.tags.span("Loading page, please wait."),
            class_="alert alert-info d-flex align-items-center gap-2 mb-3",
        )

    # ── page shell (R:790-858) ────────────────────────────────────────────────
    @render.ui
    def summary_page():
        if state.data() is None:
            return no_data_alert()
        metrics = summary_metrics()
        return ui.TagList(
            ui.output_ui(ns("review_queue")),
            ui.card(
                ui.card_header(
                    ui.div(
                        ui.tags.div(
                            ui.tags.strong("Reference curve analysis table"),
                            ui.tags.div(f"{len(metrics)} metrics", class_="text-muted small"),
                        ),
                        ui.div(
                            ui.input_action_button(
                                ns("open_summary_export"), "Export",
                                class_="btn btn-outline-secondary",
                            ),
                            ui.input_action_button(
                                ns("recompute_all"), "Recompute All Rows",
                                class_="btn btn-primary",
                                **(
                                    {"disabled": "disabled"}
                                    if bulk_recompute_active()
                                    else {}
                                ),
                            ),
                            class_="d-flex align-items-center gap-2 flex-wrap",
                        ),
                        class_="d-flex justify-content-between align-items-center "
                        "flex-wrap gap-2",
                    )
                ),
                ui.card_body(
                    ui.output_ui(ns("bulk_refresh_status")),
                    ui.div(
                        ui.tags.table(
                            ui.tags.thead(
                                ui.tags.tr(
                                    ui.tags.th("Details"),
                                    ui.tags.th("Metric"),
                                    ui.tags.th("Family"),
                                    ui.tags.th("Units"),
                                    ui.tags.th("Direction", class_="summary-col-direction"),
                                    ui.tags.th("n obs"),
                                    ui.tags.th("Status", class_="summary-col-status"),
                                    ui.tags.th(
                                        "Stratifications Available",
                                        class_="summary-col-available",
                                    ),
                                    ui.tags.th(
                                        "Stratification Used for Curves",
                                        class_="summary-col-curve",
                                    ),
                                    ui.tags.th("Actions", class_="summary-col-actions"),
                                )
                            ),
                            *[
                                ui.output_ui(
                                    ns(f"row_{metric}"), container=ui.tags.tbody
                                )
                                for metric in metrics
                            ],
                            class_="table table-sm summary-progress-table align-middle",
                        ),
                        class_="summary-table-wrapper",
                    ),
                ),
                class_="summary-shell",
            ),
        )

"""Reference Curve module + reusable manual curve editor. Port of
app/modules/mod_ref_curve.R.

`reference_curve_editor_*` is the reusable point-editor submodule that both
this module (unstratified path) and phase 4 (one editor per stratum) mount.
R's DT proxy behavior maps onto py-shiny's DataGrid: cell edits patch the
authoritative table without re-rendering the grid (selection survives), while
structural ops (add/remove/move/reseed) bump a nonce that forces a re-render
and then restore the selection via ``update_cell_selection`` once the new grid
has painted.

Row indices are 0-based throughout (py side); R's 1-based DT indices only
existed at the DT boundary.
"""

from __future__ import annotations

import io
import logging
import math
from datetime import date

import pandas as pd
from shiny import module, reactive, render, req, ui

from streamcurves.curves import (
    CURVE_FORM_MONOTONE,
    build_reference_curve,
    build_reference_curve_from_points,
    curve_form_of,
    empty_reference_curve_points,
    hydrate_reference_curve_result,
    normalize_reference_curve_points,
    reference_curve_points_from_row,
    reference_curve_row_range_display,
    reference_curve_rows_for_export,
    validate_reference_curve_points,
)
from views import state as st
from views import summary_state as ss
from views.curve_plots import (
    build_reference_curve_plot,
    build_reference_distribution_plot,
    reference_values_from_data,
)
from views.state import AppState
from views.theme import fa
from views.uihelpers import explanation_card, response_shape_label, status_badge

logger = logging.getLogger("streamcurves")

_CATEGORY_BG = {
    "Functioning": "rgba(39,174,96,0.15)",
    "At-Risk": "rgba(243,156,18,0.15)",
    "Not Functioning": "rgba(231,76,60,0.15)",
}


def _fmt(v, digits: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return str(round(f, digits))


def threshold_table_ui(threshold_df: pd.DataFrame):
    """Static thresholds table with the DT styleEqual Category backgrounds."""
    rows = []
    for _, r in threshold_df.iterrows():
        cells = []
        for col in threshold_df.columns:
            style = None
            if col == "Category":
                bg = _CATEGORY_BG.get(str(r[col]))
                if bg:
                    style = f"background-color: {bg};"
            cells.append(ui.tags.td(str(r[col]), style=style))
        rows.append(ui.tags.tr(*cells))
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in threshold_df.columns])),
        ui.tags.tbody(*rows),
        class_="table table-sm compact",
    )


# --------------------------------------------------------------------------- #
# Editor pure helpers (R:8-98)
# --------------------------------------------------------------------------- #


def reference_curve_editor_table_df(curve_points) -> pd.DataFrame:
    points = normalize_reference_curve_points(curve_points)
    return pd.DataFrame(
        {
            "point_order": range(1, len(points) + 1),
            "metric_value": points["metric_value"].to_numpy(dtype=float)
            if len(points)
            else pd.Series(dtype=float),
            "index_score": points["index_score"].to_numpy(dtype=float)
            if len(points)
            else pd.Series(dtype=float),
        }
    )


def reference_curve_editor_points_from_table(table_df) -> pd.DataFrame:
    if table_df is None or len(table_df) == 0:
        return empty_reference_curve_points()
    return pd.DataFrame(
        {
            "point_order": range(1, len(table_df) + 1),
            "metric_value": pd.to_numeric(table_df["metric_value"], errors="coerce"),
            "index_score": pd.to_numeric(table_df["index_score"], errors="coerce"),
        }
    ).reset_index(drop=True)


def reference_curve_editor_move_row(table_df, selected_row, direction: str) -> dict:
    """0-based move; statuses mirror R ("empty" / "no_selection" / "boundary" /
    "moved")."""
    if direction not in ("up", "down"):
        raise ValueError("direction should be one of 'up', 'down'")

    if table_df is None or len(table_df) == 0:
        return {"table_df": table_df, "selected_row": None, "changed": False,
                "status": "empty"}

    try:
        current = int(selected_row)
    except (TypeError, ValueError):
        current = None
    if current is None or current < 0 or current >= len(table_df):
        return {"table_df": table_df, "selected_row": None, "changed": False,
                "status": "no_selection"}

    target = current - 1 if direction == "up" else current + 1
    if target < 0 or target >= len(table_df):
        return {"table_df": table_df, "selected_row": current, "changed": False,
                "status": "boundary"}

    order = list(range(len(table_df)))
    order[current], order[target] = order[target], order[current]
    moved = table_df.iloc[order].reset_index(drop=True).copy()
    moved["point_order"] = range(1, len(moved) + 1)
    return {"table_df": moved, "selected_row": target, "changed": True,
            "status": "moved"}


def reference_curve_editor_seed_points(result, higher_is_better) -> pd.DataFrame:
    result = result or {}
    points = normalize_reference_curve_points(result.get("curve_points"))
    if len(points) >= 2:
        return points

    curve_row = result.get("curve_row")
    if curve_row is not None and len(curve_row) > 0:
        return reference_curve_points_from_row(curve_row, higher_is_better)

    return empty_reference_curve_points()


def _curve_row_value(result, column, default=None):
    curve_row = (result or {}).get("curve_row")
    if curve_row is None or len(curve_row) == 0 or column not in curve_row.columns:
        return default
    v = curve_row[column].iloc[0]
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else v


# --------------------------------------------------------------------------- #
# Manual curve editor submodule (R:100-361)
# --------------------------------------------------------------------------- #


@module.ui
def reference_curve_editor_ui(title: str = "Manual Curve Editor"):
    return ui.card(
        ui.card_header(title),
        ui.card_body(
            ui.div(
                "Edit metric-score and index-score points, then click Apply Curve "
                "Edits. Row order is preserved when applied. Index scores may move "
                "freely. Metric scores must be non-decreasing from top to bottom, "
                "and equal consecutive values create a step. Threshold tables can "
                "show multiple metric ranges when the curve crosses a threshold "
                "twice.",
                class_="text-muted small mb-3",
            ),
            ui.output_ui("curve_source_note"),
            ui.output_data_frame("points_table"),
            ui.output_ui("validation_message"),
            ui.div(
                ui.input_action_button(
                    "add_point", "Add Point", class_="btn btn-outline-secondary btn-sm"
                ),
                ui.input_action_button(
                    "remove_point", "Remove Selected Point",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.input_action_button(
                    "move_point_up", "Move Selected Point Up",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.input_action_button(
                    "move_point_down", "Move Selected Point Down",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.input_action_button(
                    "apply_points", "Apply Curve Edits", class_="btn btn-primary btn-sm"
                ),
                ui.input_action_button(
                    "reset_points", "Reset to Auto",
                    class_="btn btn-outline-danger btn-sm",
                ),
                class_="d-flex flex-wrap gap-2 mt-3",
            ),
        ),
        class_="mb-3",
    )


@module.server
def reference_curve_editor_server(
    input, output, session, current_result, higher_is_better, on_apply, on_reset,
    curve_form=None,
):
    editor_table = reactive.value(reference_curve_editor_table_df(None))
    validation_message = reactive.value(None)
    grid_nonce = reactive.value(0)

    def _selected_row():
        try:
            sel = points_table.cell_selection()
        except Exception:  # noqa: BLE001 — grid not rendered yet
            return None
        rows = (sel or {}).get("rows") or ()
        return int(rows[0]) if len(rows) else None

    def _set_table(df: pd.DataFrame, selected=None):
        """Structural change: replace the table and force a grid re-render.

        Divergence from R: DT restores the row selection via a proxy after
        moves/removals; the DataGrid re-render clears it instead. Pushing the
        selection back via update_cell_selection from a detached task races
        the session's own websocket sends and can wedge the dispatch loop, so
        the user re-selects after structural edits.
        """
        editor_table.set(df.reset_index(drop=True))
        with reactive.isolate():
            grid_nonce.set(grid_nonce() + 1)

    # -- Reseed from the current result (R:150-164, ignoreInit = FALSE) --------
    @reactive.effect
    def _reseed():
        result = current_result()
        if result is None:
            _set_table(reference_curve_editor_table_df(None))
            validation_message.set(None)
            return
        # observeEvent isolates everything but current_result(); config reads
        # must not add dependencies or config write-backs would wipe edits.
        with reactive.isolate():
            hib = higher_is_better()
        seed_points = reference_curve_editor_seed_points(result, hib)
        _set_table(reference_curve_editor_table_df(seed_points))
        validation_message.set(None)

    @output(suspend_when_hidden=False)
    @render.ui
    def curve_source_note():
        result = current_result()
        if result is None:
            return None

        curve_status = _curve_row_value(result, "curve_status", "complete")
        if curve_status == "insufficient_data":
            return ui.div(
                "Manual editing is unavailable until this curve has enough "
                "reference data to seed a baseline curve.",
                class_="alert alert-warning py-2 mb-3",
            )

        is_manual = (result.get("curve_source") or "auto") == "manual"
        return ui.div(
            ui.tags.strong("Current source: "),
            "Manual edits active" if is_manual else "Auto-generated curve",
            ui.tags.span(
                " Plots, thresholds, downloads, and summary views use the edited curve."
            )
            if is_manual
            else None,
            class_="alert alert-warning py-2 mb-3"
            if is_manual
            else "alert alert-secondary py-2 mb-3",
        )

    @output(suspend_when_hidden=False)
    @render.data_frame
    def points_table():
        grid_nonce()
        with reactive.isolate():
            df = editor_table().copy()
        disp = df.rename(
            columns={
                "point_order": "Point",
                "metric_value": "Metric Score",
                "index_score": "Index Score",
            }
        )
        # Fixed height so the grid always measures visible (a height-less
        # empty grid reports 0x0 and shiny suspends it as hidden).
        return render.DataGrid(
            disp, editable=True, selection_mode="row", width="100%", height="260px"
        )

    @points_table.set_patches_fn
    def _patches(*, patches):
        with reactive.isolate():
            df = editor_table().copy()
        out = []
        for p in patches:
            i, j = p["row_index"], p["column_index"]
            if not (0 <= i < len(df) and 0 <= j < df.shape[1]):
                continue
            if j == 0:
                # Point column is protected (R: editable disable columns = 0).
                out.append({**p, "value": df.iloc[i, 0]})
                continue
            raw = p["value"]
            try:
                val = float(raw) if str(raw).strip() != "" else math.nan
            except (TypeError, ValueError):
                val = math.nan  # R suppressWarnings(as.numeric) -> NA
            df.iloc[i, j] = val
            out.append({**p, "value": "" if math.isnan(val) else val})
        df["point_order"] = range(1, len(df) + 1)
        editor_table.set(df)
        validation_message.set(None)
        # The grid render does not depend on editor_table, so the typed values
        # stay in place without a re-render (matches R's DT proxy editData).
        return out

    @reactive.effect
    @reactive.event(input.add_point, ignore_init=True)
    def _add_point():
        with reactive.isolate():
            df = editor_table().copy()
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    {
                        "point_order": [len(df) + 1],
                        "metric_value": [math.nan],
                        "index_score": [math.nan],
                    }
                ),
            ],
            ignore_index=True,
        )
        _set_table(df, selected=_selected_row())
        validation_message.set("Fill in the new point and click Apply Curve Edits.")

    @reactive.effect
    @reactive.event(input.remove_point, ignore_init=True)
    def _remove_point():
        selected = _selected_row()
        if selected is None:
            ui.notification_show(
                "Select a point row to remove.", type="warning", duration=4
            )
            return
        with reactive.isolate():
            df = editor_table().copy()
        if len(df) <= 2:
            validation_message.set("At least 2 curve points are required.")
            return
        df = df.drop(df.index[selected]).reset_index(drop=True)
        df["point_order"] = range(1, len(df) + 1)
        validation_message.set(None)
        _set_table(df, selected=min(selected, len(df) - 1))

    def _move_point(direction: str, boundary_message: str):
        with reactive.isolate():
            df = editor_table()
        move_result = reference_curve_editor_move_row(df, _selected_row(), direction)

        if move_result["status"] == "no_selection":
            ui.notification_show(
                "Select a point row to move.", type="warning", duration=4
            )
            return
        if move_result["status"] == "boundary":
            ui.notification_show(boundary_message, type="warning", duration=4)
            return
        if move_result["status"] != "moved":
            return
        validation_message.set(None)
        _set_table(move_result["table_df"], selected=move_result["selected_row"])

    @reactive.effect
    @reactive.event(input.move_point_up, ignore_init=True)
    def _move_point_up():
        _move_point("up", "Selected point is already at the top.")

    @reactive.effect
    @reactive.event(input.move_point_down, ignore_init=True)
    def _move_point_down():
        _move_point("down", "Selected point is already at the bottom.")

    # R's output id is validation_message; the reactive.value owns that name
    # in this scope, so register the renderer under the UI's id explicitly.
    @output(id="validation_message", suspend_when_hidden=False)
    @render.ui
    def validation_message_out():
        msg = validation_message()
        if not msg:
            return None
        return ui.div(msg, class_="alert alert-danger py-2 mt-3 mb-0")

    @reactive.effect
    @reactive.event(input.apply_points, ignore_init=True)
    def _apply_points():
        result = current_result()
        if result is None or _curve_row_value(result, "curve_status", "") == "insufficient_data":
            validation_message.set(
                "Manual editing is unavailable until the curve has enough reference data."
            )
            return

        with reactive.isolate():
            table_df = editor_table()
        points = reference_curve_editor_points_from_table(table_df)
        validation = validate_reference_curve_points(
            points, higher_is_better(),
            curve_form=(curve_form() if curve_form else CURVE_FORM_MONOTONE),
        )

        if not validation["valid"]:
            validation_message.set(" ".join(validation["errors"]))
            return

        try:
            on_apply(validation["points"])
            validation_message.set(None)
        except Exception as e:  # noqa: BLE001 — surfaced as the validation alert
            validation_message.set(str(e))

    @reactive.effect
    @reactive.event(input.reset_points, ignore_init=True)
    def _reset_points():
        validation_message.set(None)
        try:
            on_reset()
        except Exception as e:  # noqa: BLE001
            validation_message.set(str(e))


# --------------------------------------------------------------------------- #
# Reference curve module — unstratified path (R:363-773)
# --------------------------------------------------------------------------- #


@module.ui
def ref_curve_ui():
    return ui.TagList(
        explanation_card(
            "Empirical Scoring Curve",
            ui.tags.p(
                "The reference curve is seeded from the empirical distribution of "
                "reference-standard sites and can be manually revised below by "
                "editing metric/index score points."
            ),
            ui.tags.p(
                "Functional categories: ",
                ui.tags.span("Functioning (0.70-1.00)", class_="text-success fw-bold"),
                " | ",
                ui.tags.span("At-Risk (0.30-0.69)", class_="text-warning fw-bold"),
                " | ",
                ui.tags.span("Not Functioning (0.00-0.29)", class_="text-danger fw-bold"),
            ),
        ),
        ui.output_ui("curve_ui"),
    )


@module.server
def ref_curve_server(input, output, session, state: AppState, workspace_scope: str = "standalone"):
    ns = session.ns

    def workspace_active(isolate_state: bool = False) -> bool:
        return st.workspace_scope_is_active(
            state, workspace_scope, standalone_modal_type="phase4",
            isolate_state=isolate_state,
        )

    def persist_reference_curve_result(result):
        if not workspace_active(isolate_state=True):
            return None
        with reactive.isolate():
            metric = state.current_metric()
            data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
            metric_config = state.metric_config() or {}
            stratum_level = state.current_stratum_level()
            strat_decision = state.strat_decision_user()
        if not metric:
            return None
        decision_tbl = ss.get_metric_phase4_decision_state(state, metric)
        normalized_result = hydrate_reference_curve_result(
            result, data, metric, metric_config,
            stratum_label=stratum_level, artifact_mode="full",
        )

        state.reference_curve.set(normalized_result)
        curve_rows = ss.extract_metric_phase4_curve_rows(
            {"reference_curve": normalized_result}
        )
        phase4_signature = ss.cache_metric_phase4_results(
            state, metric, decision_tbl=decision_tbl,
            reference_curve=normalized_result, artifact_mode="full",
        )

        with reactive.isolate():
            completed = state.completed_metrics() or {}
        if metric in completed:
            ss.update_metric_phase4_completed_entry(
                state, metric,
                {
                    "strat_decision": strat_decision,
                    "reference_curve": normalized_result,
                    "phase4_signature": phase4_signature,
                    "phase4_artifact_mode": "full",
                    "phase4_curve_rows": curve_rows,
                },
            )
        return normalized_result

    def build_auto_curve_result():
        if not workspace_active(isolate_state=True):
            return None
        with reactive.isolate():
            metric = state.current_metric()
            data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
            metric_config = state.metric_config() or {}
            stratum_level = state.current_stratum_level()
        return build_reference_curve(
            data, metric, metric_config, stratum_label=stratum_level
        )

    def build_manual_curve_result(points):
        if not workspace_active(isolate_state=True):
            return None
        with reactive.isolate():
            metric = state.current_metric()
            data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
            metric_config = state.metric_config() or {}
            stratum_level = state.current_stratum_level()
        return build_reference_curve_from_points(
            data, metric, metric_config, curve_points=points,
            stratum_label=stratum_level,
        )

    @reactive.calc
    def curve_results():
        req(workspace_active())
        metric = state.current_metric()
        req(metric)
        # R reads rv$metric_phase_cache through its helpers, registering the
        # dependency that refreshes this calc after apply/reset persists; the
        # py helpers isolate internally, so register it explicitly.
        state.metric_phase_cache()
        stratum_level = state.current_stratum_level()
        data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
        metric_config = state.metric_config() or {}
        decision_tbl = ss.get_metric_phase4_decision_state(state, metric)

        if stratum_level is None and ss.metric_has_phase4_cache(
            state, metric, decision_tbl, artifact_mode="summary"
        ):
            cached = ss.get_metric_phase4_cached_result(state, metric)
            if cached["reference_curve"] is not None:
                return hydrate_reference_curve_result(
                    cached["reference_curve"], data, metric, metric_config,
                    stratum_label=stratum_level,
                    artifact_mode=cached["artifact_mode"] or "full",
                )

        return hydrate_reference_curve_result(
            build_auto_curve_result(), data, metric, metric_config,
            stratum_label=stratum_level,
        )

    def _on_apply(points):
        result = build_manual_curve_result(points)
        persist_reference_curve_result(result)
        st.notify_workspace_refresh(state)
        ui.notification_show("Manual curve edits applied.", type="message", duration=4)

    def _on_reset():
        result = build_auto_curve_result()
        persist_reference_curve_result(result)
        st.notify_workspace_refresh(state)
        ui.notification_show(
            "Reference curve reset to the auto-generated baseline.",
            type="message", duration=4,
        )

    reference_curve_editor_server(
        "curve_editor",
        current_result=curve_results,
        higher_is_better=lambda: (
            ((state.metric_config() or {}).get(state.current_metric()) or {}).get(
                "higher_is_better"
            ) is True
        ),
        curve_form=lambda: curve_form_of(
            (state.metric_config() or {}).get(state.current_metric()) or {}
        ),
        on_apply=_on_apply,
        on_reset=_on_reset,
    )

    @output(suspend_when_hidden=False)
    @render.ui
    def curve_ui():
        if not workspace_active():
            return None

        res = curve_results()
        req(res)
        metric = state.current_metric()
        mc = (state.metric_config() or {}).get(metric) or {}
        cr = res["curve_row"]
        req(cr is not None and len(cr) > 0)
        row = cr.iloc[0]
        curve_status = str(row.get("curve_status") or "complete")
        is_manual = (res.get("curve_source") or "auto") == "manual"

        # R shows the plot cards when build_plots produced figures: status
        # "complete" plus >=2 reference values / curve points.
        with reactive.isolate():
            data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
        n_ref_values = len(reference_values_from_data(data, {metric: mc}, metric))
        has_dist_plot = curve_status == "complete" and n_ref_values >= 2
        has_curve_plot = (
            curve_status == "complete"
            and len(normalize_reference_curve_points(res.get("curve_points"))) >= 2
        )

        strat_decision = state.strat_decision_user()
        stratum_level = state.current_stratum_level()
        strat_label = "None"
        if strat_decision is not None and len(strat_decision) > 0:
            d = strat_decision.iloc[0]
            if d.get("decision_type") == "single" and d.get("selected_strat"):
                strat_label = ss.get_strat_display_name(state, d.get("selected_strat"))
            elif stratum_level is not None:
                strat_label = "Subset"
        if stratum_level is not None:
            strat_label = f"{strat_label}: {stratum_level}"

        threshold_df = pd.DataFrame(
            {
                "Category": ["Functioning", "At-Risk", "Not Functioning"],
                "Score Range": ["0.70 - 1.00", "0.30 - 0.69", "0.00 - 0.29"],
                "Metric Range(s)": [
                    reference_curve_row_range_display(cr, "functioning"),
                    reference_curve_row_range_display(cr, "at_risk"),
                    reference_curve_row_range_display(cr, "not_functioning"),
                ],
            }
        )

        def _stat(label, value, big: bool = False):
            return ui.div(
                ui.tags.strong(label), ui.tags.br(),
                ui.tags.span(value, class_="fs-4" if big else "fs-5"),
            )

        return ui.TagList(
            ui.layout_column_wrap(
                ui.card(
                    ui.card_header("Reference Distribution"),
                    ui.card_body(ui.output_plot(ns("bar_chart"), height="400px")),
                )
                if has_dist_plot
                else None,
                ui.card(
                    ui.card_header("Scoring Curve"),
                    ui.card_body(ui.output_plot(ns("curve_plot"), height="400px")),
                )
                if has_curve_plot
                else None,
                width=1 / 2,
            ),
            reference_curve_editor_ui(ns("curve_editor")),
            ui.card(
                ui.card_header("Descriptive Statistics"),
                ui.card_body(
                    ui.layout_column_wrap(
                        _stat("n", str(row.get("n_reference")), big=True),
                        _stat("Min", _fmt(row.get("min_val"))),
                        _stat("Q25", _fmt(row.get("q25"))),
                        _stat("Median", _fmt(row.get("median_val"))),
                        _stat("Q75", _fmt(row.get("q75"))),
                        _stat("Max", _fmt(row.get("max_val"))),
                        _stat("IQR", _fmt(row.get("iqr"))),
                        _stat("SD", _fmt(row.get("sd_val"))),
                        width=1 / 4,
                    )
                ),
            ),
            ui.card(
                ui.card_header("Key Statistics"),
                ui.card_body(
                    ui.layout_column_wrap(
                        _stat("Sample Size", str(row.get("n_reference"))),
                        _stat("Stratification", strat_label),
                        _stat("Curve Source", "Manual" if is_manual else "Auto"),
                        ui.div(
                            ui.tags.strong("Curve Status"), ui.tags.br(),
                            status_badge(
                                "pass" if curve_status == "complete" else "fail",
                                curve_status,
                            ),
                        ),
                        width=1 / 4,
                    )
                ),
            ),
            ui.card(
                ui.card_header("Scoring Thresholds"),
                ui.card_body(threshold_table_ui(threshold_df)),
            ),
            ui.card(
                ui.card_header("Curve Details"),
                ui.card_body(
                    ui.tags.table(
                        ui.tags.tbody(
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Q25")),
                                ui.tags.td(_fmt(row.get("q25"), 3)),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Q75")),
                                ui.tags.td(_fmt(row.get("q75"), 3)),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("IQR")),
                                ui.tags.td(_fmt(row.get("iqr"), 3)),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Direction")),
                                # A two-sided metric's null direction used to fall
                                # through to "Lower is better", labelling a trapezoid
                                # as monotone-decreasing right beside the curve.
                                ui.tags.td(response_shape_label(mc)),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Curve points")),
                                ui.tags.td(str(row.get("curve_n_points"))),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Source")),
                                ui.tags.td("Manual" if is_manual else "Auto"),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("n (reference)")),
                                ui.tags.td(str(row.get("n_reference"))),
                            ),
                            ui.tags.tr(
                                ui.tags.td(ui.tags.strong("Status")),
                                ui.tags.td(
                                    status_badge(
                                        "pass" if curve_status == "complete" else "fail",
                                        curve_status,
                                    )
                                ),
                            ),
                        ),
                        class_="table table-sm table-bordered",
                    )
                ),
            ),
            ui.div(
                ui.download_button(
                    ns("dl_curve_plot"), "Download Curve Plot (PNG)",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                ui.download_button(
                    ns("dl_curve_table"), "Download Curve Table (CSV)",
                    class_="btn btn-outline-secondary btn-sm",
                ),
                class_="d-flex gap-2 mb-3",
            ),
            ui.div(
                ui.input_action_button(
                    ns("mark_complete"), "Mark Metric Complete ✓",
                    class_="btn btn-success btn-proceed", icon=fa("check"),
                ),
                class_="d-flex justify-content-end mt-3",
            ),
        )

    def _current_figure(kind: str):
        res = curve_results()
        if res is None:
            return None
        with reactive.isolate():
            metric = state.current_metric()
            metric_config = state.metric_config() or {}
            stratum_level = state.current_stratum_level()
            data = ss._first_not_none(state.phase4_data(), state.data())  # noqa: SLF001
        if kind == "bar":
            return build_reference_distribution_plot(
                reference_values_from_data(data, metric_config, metric),
                res["curve_row"], metric_config, metric, stratum_label=stratum_level,
            )
        return build_reference_curve_plot(
            res["curve_points"], res["curve_row"], metric_config, metric,
            stratum_label=stratum_level,
        )

    @output(suspend_when_hidden=False)
    @render.plot
    def bar_chart():
        req(workspace_active())
        fig = _current_figure("bar")
        req(fig is not None)
        return fig

    @output(suspend_when_hidden=False)
    @render.plot
    def curve_plot():
        req(workspace_active())
        fig = _current_figure("curve")
        req(fig is not None)
        return fig

    def _iso_metric() -> str:
        with reactive.isolate():
            return state.current_metric() or "metric"

    @render.download(
        filename=lambda: f"{_iso_metric()}_reference_curve_{date.today():%Y%m%d}.png"
    )
    def dl_curve_plot():
        with reactive.isolate():
            fig = _current_figure("curve")
        if fig is None:
            yield b""
            return
        buf = io.BytesIO()
        fig.save(buf, width=8, height=6, dpi=300, verbose=False)
        yield buf.getvalue()

    @render.download(
        filename=lambda: f"{_iso_metric()}_reference_curve_{date.today():%Y%m%d}.csv"
    )
    def dl_curve_table():
        with reactive.isolate():
            res = curve_results()
        if res is not None and res.get("curve_row") is not None:
            yield reference_curve_rows_for_export(res["curve_row"]).to_csv(
                index=False
            ).encode("utf-8")
        else:
            yield b""

    @reactive.effect
    @reactive.event(input.mark_complete, ignore_init=True)
    def _mark_complete():
        metric = state.current_metric()
        req(metric)
        current_result = curve_results()
        req(current_result is not None)
        with reactive.isolate():
            mc = (state.metric_config() or {}).get(metric) or {}
            stratum_level = state.current_stratum_level()
            strat_decision = state.strat_decision_user()

        if stratum_level is not None:
            ui.notification_show(
                f"{mc.get('display_name') or metric} - {stratum_level} stratum "
                "curve complete!",
                type="message", duration=3,
            )
            return

        phase4_signature = ss.cache_metric_phase4_results(
            state, metric,
            decision_tbl=ss.get_metric_phase4_decision_state(state, metric),
            reference_curve=current_result, artifact_mode="full",
        )
        curve_rows = ss.extract_metric_phase4_curve_rows(
            {"reference_curve": current_result}
        )
        ss.update_metric_phase4_completed_entry(
            state, metric,
            {
                "strat_decision": strat_decision,
                "reference_curve": current_result,
                "phase4_signature": phase4_signature,
                "phase4_artifact_mode": "full",
                "phase4_curve_rows": curve_rows,
            },
        )

        ui.notification_show(
            f"{mc.get('display_name') or metric} marked complete!",
            type="message", duration=3,
        )
        st.notify_workspace_refresh(state)

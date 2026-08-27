"""Discipline / Function / Metric workbench — port of
app/modules/mod_setup_discipline_map.R.

Two-pane assessment-design workbench: LEFT a fixed Discipline | Function |
Metric table (5 disciplines / 20 STAF functions) with assigned-metric chips;
RIGHT a searchable master metric library. Click "+" on a function to make it
active, then click a library metric to add it. All wiring uses onclick →
``Shiny.setInputValue(..., {priority:'event'})`` channels, exactly like R.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone

import pandas as pd
from shiny import module, reactive, render, ui

from streamcurves.deep_export import (
    FUNCTION_EXCLUSION_REASONS,
    deep_read_staf_crosswalk,
    uncovered_functions_from_mapping,
    validate_coverage_exceptions,
)
from streamcurves.mapping import (
    blank_function_mapping_scaffold,
    metric_usage_counts,
    validate_discipline_function_mapping,
)
from streamcurves.staf_library import (
    default_discipline_function_mapping,
    staf_function_meta,
    staf_functions_by_discipline,
    staf_metric_library_entries,
)
from views.state import AppState
from views.uihelpers import guard
from views.theme import fa
from views.wb_table import render_wb_table

## Separator joining metric_key + function name in the remove payload. Neither
## a metric_key nor a STAF function name contains this token.
WORKBENCH_SEP = "@@"


def js_str(x: str) -> str:
    """Safe JS string escape for embedding payloads in onclick handlers."""
    return str(x).replace("\\", "\\\\").replace("'", "\\'")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_actor() -> str:
    """Best-effort author name for a coverage exception, same env the library
    publisher reads for its maintainer audit name."""
    return (os.environ.get("STAF_LIBRARY_MAINTAINER")
            or os.environ.get("USERNAME") or os.environ.get("USER") or "")


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == ""


def workbench_metric_label(mk, metric_config: dict, lib_by_id: dict) -> str:
    """Display label for a metric_key: real workbook keys use metric_config;
    synthetic lib:<id> keys use the master-library label."""
    if _is_blank(mk):
        return mk
    mk = str(mk)
    if mk.startswith("lib:"):
        lid = mk[4:]
        return lib_by_id.get(lid, lid)
    entry = (metric_config or {}).get(mk) or {}
    return entry.get("display_name") or mk


def workbench_has_data(mk, metric_config: dict) -> bool:
    """TRUE when the metric is data-backed (a real key in the loaded workbook)."""
    if _is_blank(mk):
        return False
    mk = str(mk)
    return not mk.startswith("lib:") and mk in (metric_config or {})


def update_mapping(state: AppState, mutate_fn) -> pd.DataFrame:
    """Copy-mutate-validate helper (R:37-60). Any accepted change clears the
    confirmed flag and marks the mapping user-owned (stops STAF auto-seeding)."""
    with reactive.isolate():
        current = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
    if current is None:
        current = blank_function_mapping_scaffold(list(metric_config.keys()))
    try:
        new_mapping = mutate_fn(current.copy())
    except Exception as e:  # noqa: BLE001 — surfaced as a notification like R
        ui.notification_show(f"Mapping update failed: {e}", type="error", duration=8)
        return current
    try:
        validate_discipline_function_mapping(new_mapping)
    except Exception as e:  # noqa: BLE001
        ui.notification_show(f"Mapping update rejected: {e}", type="error", duration=8)
        return current
    if not new_mapping.equals(current):
        state.discipline_function_mapping.set(new_mapping.reset_index(drop=True))
        state.discipline_function_mapping_confirmed.set(False)
        state.mapping_user_touched.set(True)
    return new_mapping


@module.ui
def discipline_map_ui():
    return ui.div(
        ui.div(
            ui.input_action_button(
                "save_mapping", ui.TagList(fa("check"), " Save mapping"),
                class_="btn btn-primary btn-sm",
            ),
            ui.input_action_button(
                "reset_staf", ui.TagList(fa("rotate-left"), " Reset to STAF defaults"),
                class_="btn btn-outline-secondary btn-sm",
            ),
            ui.output_ui("reset_workbook_ui", inline=True),
            # Last in the group, away from Save: it empties every assignment.
            ui.input_action_button(
                "clear_mapping", ui.TagList(fa("eraser"), " Clear mappings"),
                class_="btn btn-outline-secondary btn-sm",
            ),
            ui.div(
                # On by default: the filter is workbench_has_data, i.e. exactly the
                # metric_config membership that uncovered_functions_from_mapping
                # counts as coverage. Off, the table shows lib: placeholder chips
                # that read as coverage but are not -- so the table would contradict
                # the uncovered panel right above it and the stage-4 pill above that.
                ui.input_switch("hide_nodata", "Hide no-data metrics", value=True),
                class_="ms-2 mb-0 small",
            ),
            ui.output_ui("status_badge", inline=True),
            class_="d-flex align-items-center flex-wrap mb-2 gap-2",
        ),
        ui.layout_columns(
            ui.div(
                ui.output_ui("unassigned_panel"),
                ui.output_ui("uncovered_panel"),
                ui.output_ui("workbench_table"),
                class_="workbench-left",
            ),
            ui.card(
                ui.card_header(
                    ui.div(
                        fa("layer-group"),
                        ui.tags.strong("Metric library"),
                        class_="d-flex align-items-center gap-2",
                    )
                ),
                ui.card_body(
                    ui.output_ui("active_fn_bar"),
                    ui.input_text("lib_search", None, placeholder="Search metrics…", width="100%"),
                    ui.div(ui.output_ui("library_palette"), class_="workbench-palette"),
                ),
                class_="workbench-library",
            ),
            col_widths=[8, 4],
            class_="workbench-grid",
        ),
        class_="discipline-map-module",
    )


@module.server
def discipline_map_server(input, output, session, state: AppState):
    ns = session.ns

    ## Static library metadata (computed once).
    try:
        lib_entries = staf_metric_library_entries()
    except Exception:
        lib_entries = None
    if lib_entries is not None and len(lib_entries) > 0:
        lib_unique = lib_entries.drop_duplicates(subset="library_id", keep="first")
    else:
        lib_unique = None
    lib_by_id = (
        dict(zip(lib_unique["library_id"], lib_unique["label"])) if lib_unique is not None else {}
    )
    try:
        fn_meta = staf_function_meta()
        fn_disc = dict(zip(fn_meta["name"], fn_meta["discipline"]))
    except Exception:
        fn_disc = {}

    active_function = reactive.value(None)

    @reactive.calc
    def usage() -> dict[str, int]:
        return metric_usage_counts(state.discipline_function_mapping())

    def resolve_lib_key(row, metric_config: dict) -> str:
        ak = row["app_metric_key"]
        if not _is_blank(ak) and str(ak) in (metric_config or {}):
            return str(ak)
        return f"lib:{row['library_id']}"

    # ---- status + reset controls ------------------------------------------
    @render.ui
    def status_badge():
        if state.discipline_function_mapping_confirmed():
            return ui.tags.span("Confirmed", class_="badge bg-success ms-2")
        return ui.tags.span(
            "Unconfirmed — exports locked", class_="badge bg-warning text-dark ms-2"
        )

    @render.ui
    def reset_workbook_ui():
        if not state.workbook_provided_mapping():
            return None
        startup = state.startup_discipline_function_mapping()
        if startup is None or len(startup) == 0:
            return None
        # dynamic UI in a module server needs explicit namespacing (R: ns())
        return ui.input_action_button(
            ns("reset_workbook"),
            ui.TagList(fa("file-arrow-down"), " Reset to workbook"),
            class_="btn btn-outline-secondary btn-sm",
        )

    @reactive.effect
    @reactive.event(input.save_mapping)
    @guard("save the mapping")
    def _save_mapping():
        with reactive.isolate():
            mapping = state.discipline_function_mapping()
        has_assignment = (
            mapping is not None
            and len(mapping) > 0
            and mapping["function_label"].map(lambda v: not _is_blank(v)).any()
        )
        if not has_assignment:
            ui.notification_show(
                "Nothing to save — assign at least one metric to a function.",
                type="warning",
                duration=6,
            )
            return
        state.discipline_function_mapping_confirmed.set(True)
        ui.notification_show("Mapping saved. Exports are unlocked.", type="message", duration=4)

    @reactive.effect
    @reactive.event(input.reset_staf)
    @guard("reset to STAF defaults")
    def _reset_staf():
        with reactive.isolate():
            metric_config = state.metric_config() or {}
        try:
            dm = default_discipline_function_mapping(list(metric_config.keys()), metric_config)
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Could not load STAF defaults: {e}", type="error", duration=8
            )
            return
        state.discipline_function_mapping.set(dm)
        state.discipline_function_mapping_confirmed.set(False)
        active_function.set(None)
        ui.notification_show(
            "Mapping reset to the comprehensive STAF defaults. Review and Save.",
            type="message",
            duration=6,
        )

    @reactive.effect
    @reactive.event(input.clear_mapping)
    @guard("clear the mapping")
    def _clear_mapping():
        """Every metric present, nothing assigned: the scaffold this stage starts
        from. Not a bare empty frame, which would leave the workbench table with
        no rows to show as unassigned."""
        with reactive.isolate():
            metric_config = state.metric_config() or {}
        state.discipline_function_mapping.set(
            blank_function_mapping_scaffold(list(metric_config.keys())))
        state.discipline_function_mapping_confirmed.set(False)
        active_function.set(None)
        ui.notification_show(
            "Mapping cleared: every metric is now unassigned and exports are "
            "locked. Assign metrics and Save, or Reset to STAF defaults.",
            type="warning",
            duration=8,
        )

    @reactive.effect
    @reactive.event(input.reset_workbook)
    @guard("reset to the workbook")
    def _reset_workbook():
        with reactive.isolate():
            startup = state.startup_discipline_function_mapping()
        if startup is None or len(startup) == 0:
            return
        state.discipline_function_mapping.set(startup)
        state.discipline_function_mapping_confirmed.set(False)
        active_function.set(None)
        ui.notification_show(
            "Mapping reset to the workbook's saved state. Review and Save.",
            type="message",
            duration=6,
        )

    # ---- active function ----------------------------------------------------
    @reactive.effect
    @reactive.event(input.set_active_fn)
    @guard("select that function")
    def _set_active_fn():
        fn = input.set_active_fn()
        if fn:
            active_function.set(fn)

    @render.ui
    def active_fn_bar():
        fn = active_function()
        if not fn:
            return ui.div(
                fa("hand-pointer"),
                " Click ",
                ui.tags.strong("+"),
                " on a function, then add metrics from below.",
                class_="workbench-active-hint small text-muted mb-2",
            )
        return ui.div(
            ui.tags.span("Adding to ", class_="small text-muted"),
            ui.tags.span(fn, class_="badge bg-primary"),
            class_="workbench-active-bar mb-2",
        )

    # ---- add / remove assignments -------------------------------------------
    @reactive.effect
    @reactive.event(input.add_assign)
    @guard("assign the metric")
    def _add_assign():
        mk = input.add_assign()
        if not mk:
            return
        with reactive.isolate():
            fn = active_function()
        if not fn:
            ui.notification_show(
                "Pick a function first — click + on a function in the table.",
                type="warning",
                duration=5,
            )
            return
        disc = fn_disc.get(fn)

        def mutate(m: pd.DataFrame) -> pd.DataFrame:
            keys = m["metric_key"].astype(object)
            fls = m["function_label"].astype(object)
            # metric now has a home -> drop its unassigned scaffold row
            scaffold = keys.map(lambda v: not _is_blank(v) and str(v) == mk) & fls.map(_is_blank)
            m = m[~scaffold]
            already = m.apply(
                lambda r: (
                    not _is_blank(r["metric_key"])
                    and str(r["metric_key"]) == mk
                    and not _is_blank(r["function_label"])
                    and str(r["function_label"]).strip().lower() == fn.strip().lower()
                ),
                axis=1,
            ) if len(m) else pd.Series([], dtype=bool)
            if len(m) and already.any():
                return m
            new_row = pd.DataFrame(
                [{"metric_key": mk, "discipline": disc, "function_label": fn, "sort_order": None}]
            )
            m = pd.concat([m, new_row], ignore_index=True)
            m["sort_order"] = range(1, len(m) + 1)
            return m

        update_mapping(state, mutate)

    @reactive.effect
    @reactive.event(input.remove_assign)
    @guard("remove the assignment")
    def _remove_assign():
        payload = input.remove_assign()
        if not payload:
            return
        parts = str(payload).split(WORKBENCH_SEP)
        mk = parts[0]
        fn = WORKBENCH_SEP.join(parts[1:]) if len(parts) >= 2 else ""
        with reactive.isolate():
            metric_config = state.metric_config() or {}

        def mutate(m: pd.DataFrame) -> pd.DataFrame:
            if len(m):
                drop = m.apply(
                    lambda r: (
                        not _is_blank(r["metric_key"])
                        and str(r["metric_key"]) == mk
                        and not _is_blank(r["function_label"])
                        and str(r["function_label"]).strip().lower() == fn.strip().lower()
                    ),
                    axis=1,
                )
                m = m[~drop]
            # a real workbook metric left with no home returns to the scaffold
            # so coverage keeps tracking it (planned lib: metrics disappear).
            if workbench_has_data(mk, metric_config):
                if len(m):
                    keys = m["metric_key"].astype(object)
                    fls = m["function_label"].astype(object)
                    still_assigned = (
                        keys.map(lambda v: not _is_blank(v) and str(v) == mk)
                        & fls.map(lambda v: not _is_blank(v))
                    ).any()
                    has_scaffold = (
                        keys.map(lambda v: not _is_blank(v) and str(v) == mk)
                        & fls.map(_is_blank)
                    ).any()
                else:
                    still_assigned = has_scaffold = False
                if not still_assigned and not has_scaffold:
                    m = pd.concat(
                        [
                            m,
                            pd.DataFrame(
                                [
                                    {
                                        "metric_key": mk,
                                        "discipline": None,
                                        "function_label": None,
                                        "sort_order": None,
                                    }
                                ]
                            ),
                        ],
                        ignore_index=True,
                    )
            if len(m) > 0:
                m = m.reset_index(drop=True)
                m["sort_order"] = range(1, len(m) + 1)
            return m

        update_mapping(state, mutate)

    # ---- LEFT: Discipline | Function | Metric table --------------------------
    @render.ui
    def workbench_table():
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
        usage_v = usage()
        hide_nodata = bool(input.hide_nodata())
        active = active_function()
        by_disc = staf_functions_by_discipline()

        def metrics_for_fn(fn: str) -> list[str]:
            if mapping is None or len(mapping) == 0:
                return []
            rows = mapping[
                mapping.apply(
                    lambda r: (
                        not _is_blank(r["metric_key"])
                        and not _is_blank(r["function_label"])
                        and str(r["function_label"]).strip().lower() == fn.strip().lower()
                    ),
                    axis=1,
                )
            ]
            if len(rows) == 0:
                return []
            return rows.sort_values("sort_order")["metric_key"].astype(str).tolist()

        def make_chip(mk: str, fn: str):
            nodata = not workbench_has_data(mk, metric_config)
            label = workbench_metric_label(mk, metric_config, lib_by_id)
            u = int(usage_v.get(mk, 1) or 1)
            payload = f"{mk}{WORKBENCH_SEP}{fn}"
            return ui.tags.span(
                ui.tags.span(label, class_="wb-chip-label"),
                ui.tags.span("no data", class_="wb-chip-tag") if nodata else None,
                (
                    ui.tags.span(
                        f"×{u}", class_="wb-usage", title=f"Used in {u} functions"
                    )
                    if u > 1
                    else None
                ),
                ui.tags.button(
                    ui.HTML("&times;"),
                    type="button",
                    class_="wb-chip-x",
                    title="Remove from this function",
                    onclick=(
                        f"Shiny.setInputValue('{ns('remove_assign')}',"
                        f"'{js_str(payload)}',{{priority:'event'}})"
                    ),
                ),
                class_="wb-chip " + ("wb-chip-nodata" if nodata else "wb-chip-data"),
            )

        def fn_cell(fn: str):
            is_active = active is not None and active == fn
            add_btn = ui.tags.button(
                fa("plus"),
                type="button",
                class_="wb-add-fn" + (" wb-add-fn-active" if is_active else ""),
                title="Add metrics to this function",
                onclick=(
                    f"Shiny.setInputValue('{ns('set_active_fn')}',"
                    f"'{js_str(fn)}',{{priority:'event'}})"
                ),
            )
            return ui.tags.td(
                ui.div(
                    ui.tags.span(fn),
                    add_btn,
                    class_="d-flex align-items-center justify-content-between gap-2",
                ),
                class_="wb-fn" + (" wb-fn-active" if is_active else ""),
            )

        def metrics_cell(fn: str):
            keys = metrics_for_fn(fn)
            if hide_nodata:
                keys = [k for k in keys if workbench_has_data(k, metric_config)]
            if not keys:
                chips = [ui.tags.span("—", class_="wb-empty text-muted")]
            else:
                chips = [make_chip(k, fn) for k in keys]
            return ui.tags.td(*chips, class_="wb-metrics")

        return render_wb_table(by_disc, fn_cell=fn_cell, metrics_cell=metrics_cell)

    # ---- unassigned workbook metrics -----------------------------------------
    @render.ui
    def unassigned_panel():
        mapping = state.discipline_function_mapping()
        metric_config = state.metric_config() or {}
        real_keys = list(metric_config.keys())
        if not real_keys:
            return None
        assigned: set[str] = set()
        if mapping is not None and len(mapping) > 0:
            am = mapping.apply(
                lambda r: not _is_blank(r["metric_key"]) and not _is_blank(r["function_label"]),
                axis=1,
            )
            assigned = set(mapping.loc[am, "metric_key"].astype(str))
        unassigned = [k for k in real_keys if k not in assigned]
        if not unassigned:
            return None
        labels = [workbench_metric_label(mk, metric_config, lib_by_id) for mk in unassigned]
        n = len(unassigned)
        return ui.div(
            ui.div(
                fa("triangle-exclamation"),
                ui.tags.strong(
                    f"{n} workbook metric{'' if n == 1 else 's'} not yet assigned"
                ),
                class_="d-flex align-items-center gap-2 mb-1",
            ),
            ui.tags.small(
                "Add each to a function from the library (they carry data): ",
                class_="text-muted",
            ),
            ui.tags.small(", ".join(labels)),
            class_="alert alert-warning py-2 px-3 mb-2 workbench-unassigned",
        )

    # ---- uncovered STAF functions --------------------------------------------
    # The mirror of unassigned_panel: that one warns about metrics with no function,
    # this one about functions with no metric. Only the second blocks a publish
    # (library.publish_version), and it is the gap that shipped every assessment so
    # far at 8-13 of 20 functions with no record of whether that was deliberate.
    def _uncovered_functions() -> list[tuple[str, str]]:
        """[(function_id, function_name)] with no metric and no documented exception.

        Thin reactive shim over the pure helper so this panel and the workflow
        strip's stage-4 status (assessment_publish.run_snapshot) can never
        disagree about what counts as a gap.
        """
        return uncovered_functions_from_mapping(
            state.discipline_function_mapping(),
            state.metric_config(),
            state.function_coverage_exceptions(),
        )

    @render.ui
    def uncovered_panel():
        if state.metric_config() is None or not state.metric_config():
            return None
        gaps = _uncovered_functions()
        if not gaps:
            return None
        n = len(gaps)
        return ui.div(
            ui.div(
                fa("triangle-exclamation"),
                ui.tags.strong(
                    f"{n} STAF function{'' if n == 1 else 's'} with no metric"
                ),
                ui.input_action_button(
                    ns("open_coverage_exception"), "Document a gap",
                    class_="btn btn-outline-secondary btn-sm ms-auto",
                ),
                class_="d-flex align-items-center gap-2 mb-1",
            ),
            ui.tags.small(
                "Publishing needs each of the 20 functions either covered by a metric "
                "or recorded with a reason. Assign a metric from the library, or "
                "document why it is out of scope: ",
                class_="text-muted",
            ),
            ui.tags.small(", ".join(name for _, name in gaps)),
            class_="alert alert-warning py-2 px-3 mb-2 workbench-uncovered",
        )

    @reactive.effect
    @reactive.event(input.open_coverage_exception)
    @guard("open the exception form")
    def _open_coverage_exception():
        gaps = _uncovered_functions()
        if not gaps:
            ui.notification_show("Every STAF function is covered or documented.",
                                 type="message")
            return
        ui.modal_show(ui.modal(
            ui.p("Recorded on the assessment so a reader can tell a deliberate scope "
                 "decision from an oversight. Travels with the published bundle.",
                 class_="text-muted small"),
            ui.input_select(ns("exc_function"), "Function",
                            {fid: name for fid, name in gaps}),
            ui.input_select(ns("exc_reason"), "Reason",
                            {r: r.replace("-", " ") for r in FUNCTION_EXCLUSION_REASONS}),
            ui.input_text_area(
                ns("exc_justification"), "Justification", rows=3, width="100%",
                placeholder="Why this function carries no metric in this assessment.",
            ),
            ui.input_text(ns("exc_recorded_by"), "Recorded by", value=_default_actor()),
            title="Document an uncovered function",
            footer=ui.TagList(
                ui.modal_button("Cancel"),
                ui.input_action_button(ns("exc_save"), "Record", class_="btn btn-primary"),
            ),
            easy_close=True,
        ))

    @reactive.effect
    @reactive.event(input.exc_save)
    @guard("record the exception")
    def _save_coverage_exception():
        record = {
            "functionId": input.exc_function(),
            "reason": input.exc_reason(),
            "justification": (input.exc_justification() or "").strip(),
            "recordedBy": (input.exc_recorded_by() or "").strip(),
            "recordedAt": _now_iso(),
        }
        existing = list(state.function_coverage_exceptions() or [])
        try:
            # Validate the whole set, so one bad entry cannot be smuggled past the
            # publish gate by being saved alongside good ones.
            validate_coverage_exceptions(
                [e for e in existing if e.get("functionId") != record["functionId"]]
                + [record],
                deep_read_staf_crosswalk(),
            )
        except ValueError as exc:
            ui.notification_show(str(exc), type="error", duration=10)
            return
        state.function_coverage_exceptions.set(
            [e for e in existing if e.get("functionId") != record["functionId"]] + [record]
        )
        ui.modal_remove()
        ui.notification_show(
            f"Recorded {record['functionId']} as out of scope.", type="message")

    # ---- RIGHT: searchable metric library -------------------------------------
    @render.ui
    def library_palette():
        metric_config = state.metric_config() or {}
        usage_v = usage()
        q = (input.lib_search() or "").strip().lower()
        active = active_function()

        items: list[dict] = []
        if lib_unique is not None:
            for _, row in lib_unique.iterrows():
                key = resolve_lib_key(row, metric_config)
                items.append(
                    {
                        "key": key,
                        "label": row["label"],
                        "discipline": row["discipline"],
                        "has_data": workbench_has_data(key, metric_config),
                    }
                )
        lib_keys = {x["key"] for x in items}
        for mk in metric_config:
            if mk in lib_keys:
                continue
            items.append(
                {
                    "key": mk,
                    "label": workbench_metric_label(mk, metric_config, lib_by_id),
                    "discipline": None,
                    "has_data": True,
                }
            )
        if q:
            items = [x for x in items if q in str(x["label"]).lower()]
        items.sort(key=lambda x: (not bool(x["has_data"]), str(x["label"]).lower()))
        if not items:
            return ui.div("No metrics match your search.", class_="text-muted small p-2")

        can_add = bool(active)
        rows = []
        for x in items:
            u = int(usage_v.get(x["key"], 0) or 0)
            if x["discipline"] is not None and not _is_blank(x["discipline"]):
                disc_dot = ui.tags.span(
                    class_=f"wb-dot discipline-{str(x['discipline']).lower()}",
                    title=str(x["discipline"]),
                )
            else:
                disc_dot = ui.tags.span(class_="wb-dot wb-dot-other", title="Workbook metric")
            add_btn = ui.tags.button(
                fa("plus"),
                type="button",
                class_="wb-lib-add",
                title=f"Add to {active}" if can_add else "Select a function first",
                onclick=(
                    f"Shiny.setInputValue('{ns('add_assign')}',"
                    f"'{js_str(x['key'])}',{{priority:'event'}})"
                ),
                **({} if can_add else {"disabled": "disabled"}),
            )
            usage_tag = None
            if u > 1:
                usage_tag = ui.tags.span(
                    f"×{u} review",
                    class_="wb-usage wb-usage-flag",
                    title=f"Already used in {u} functions — review",
                )
            elif u == 1:
                usage_tag = ui.tags.span("×1", class_="wb-usage", title="Used in 1 function")
            rows.append(
                ui.div(
                    disc_dot,
                    ui.div(
                        ui.tags.span(x["label"]),
                        (
                            ui.tags.span(
                                "data", class_="wb-lib-hasdata", title="In your workbook"
                            )
                            if x["has_data"]
                            else None
                        ),
                        usage_tag,
                        class_="wb-lib-label flex-grow-1",
                    ),
                    add_btn,
                    class_="wb-lib-row d-flex align-items-center gap-2",
                )
            )
        return ui.TagList(*rows)

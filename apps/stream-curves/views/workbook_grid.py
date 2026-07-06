"""Workbook GUI — port of app/modules/mod_workbook_grid.R.

An Excel-like, tabbed view of the StreamCurves workbook: one tab per sheet
(Data + config sheets), each an inline-editable grid (py-shiny DataGrid with a
patches handler replacing DT cell-edit). Edits accumulate in a working copy;
"Apply changes" commits them through the shared rebuild path
(views.rebuild.rebuild_app_from_tables), re-running clean/derive/precheck.

The default "Choose columns" view reuses the classify UI (views.classify_ui)
seeded from the CURRENT role membership; applying reconciles the delta via
streamcurves.profiler.reconcile_role_membership.
"""

from __future__ import annotations

import pandas as pd
from shiny import module, reactive, render, req, ui
from shiny.module import resolve_id

from streamcurves import workbook_tables as wt
from streamcurves.profiler import (
    current_role_membership,
    parse_pasted_table,
    profile_and_suggest,
    reconcile_role_membership,
)
from streamcurves.workbook import coerce_flag, ensure_workbook_sheet_columns
from views.classify_ui import (
    classify_assignments_from_input,
    classify_role_summary_html,
    classify_table_html,
)
from views.rebuild import rebuild_app_from_tables
from views.state import AppState
from views.theme import bi, fa

SHEETS = [
    {"key": "data", "label": "Data", "kind": "data"},
    {"key": "metrics", "label": "Metrics", "kind": "config"},
    {"key": "predictors", "label": "Predictors", "kind": "config"},
    {"key": "stratifications", "label": "Stratifications", "kind": "config"},
    {"key": "factor_recodes", "label": "Factor Recodes", "kind": "config"},
    {"key": "custom_groups", "label": "Custom Groups", "kind": "config"},
    {"key": "site_masks", "label": "Site Masks", "kind": "config"},
]


@module.ui
def workbook_grid_ui():
    """Static shell (like the R module's UI) — re-rendering it on edits would
    reset the view-mode radio and tab selection."""
    panels = []
    for s in SHEETS:
        is_data = s["kind"] == "data"
        toolbar = ui.div(
            ui.input_action_button(
                f"add_row_{s['key']}", ui.TagList(fa("plus"), " Add row"),
                class_="btn btn-outline-success btn-sm",
            ),
            ui.input_action_button(
                f"del_rows_{s['key']}", ui.TagList(fa("trash"), " Delete selected"),
                class_="btn btn-outline-danger btn-sm",
            ),
            (
                ui.TagList(
                    ui.input_action_button(
                        "add_col", ui.TagList(fa("table-columns"), " Add column"),
                        class_="btn btn-outline-secondary btn-sm",
                    ),
                    ui.div(
                        ui.input_file(
                            "import_csv", None,
                            accept=[".csv", ".tsv", ".txt", ".xlsx", ".xls"],
                            button_label="Import CSV", placeholder="",
                        ),
                        class_="workbook-import-inline",
                    ),
                    ui.input_action_button(
                        "paste_btn", ui.TagList(fa("clipboard"), " Paste"),
                        class_="btn btn-outline-secondary btn-sm",
                    ),
                    ui.tags.span(
                        "Click a cell and press Ctrl+V to paste from Excel",
                        class_="text-muted small ms-1",
                    ),
                )
                if is_data
                else None
            ),
            ui.tags.span(class_="flex-grow-1"),
            ui.output_ui(f"rowinfo_{s['key']}", inline=True),
            class_="workbook-grid-toolbar d-flex flex-wrap gap-2 align-items-center my-2",
        )
        panels.append(
            ui.nav_panel(
                s["label"],
                toolbar,
                ui.div(
                    ui.output_data_frame(f"grid_{s['key']}"),
                    class_="workbook-grid",
                    **(
                        {"data-paste-input": resolve_id("data_clipboard")}
                        if is_data
                        else {}
                    ),
                ),
                value=s["key"],
            )
        )
    return ui.div(
        ui.div(
            ui.input_radio_buttons(
                "view_mode", None,
                choices={"choose": "Choose columns", "table": "Table"},
                selected="choose", inline=True,
            ),
            class_="sites-view-toggle mb-2",
        ),
        ui.div(
            ui.input_action_button(
                "apply_changes", ui.TagList(fa("check"), " Apply changes"),
                class_="btn btn-primary btn-sm",
            ),
            ui.output_ui("dirty_badge", inline=True),
            ui.output_ui("apply_status_ui", inline=True),
            class_="workbook-grid-actionbar d-flex flex-wrap gap-2 align-items-center mb-2",
        ),
        ui.output_ui("view_style"),
        ui.div(
            ui.div(
                ui.div(
                    "Check which data columns to use as metrics, predictors, or "
                    "stratifications. Applying rebuilds the dataset — recompute "
                    "reference curves afterward. Switch to ",
                    ui.tags.strong("Table"),
                    " to fine-tune each entity's settings.",
                    class_="text-muted small mb-2",
                ),
                ui.output_ui("choose_summary"),
                ui.div(ui.output_ui("choose_table"), class_="workbook-choose-scroll"),
                ui.output_ui("choose_locked_note"),
                class_="workbook-choose",
            ),
            class_="wbv-panel wbv-panel-choose",
        ),
        ui.div(
            ui.navset_pill(*panels, id="sheet_tabs"),
            class_="wbv-panel wbv-panel-table",
        ),
        class_="workbook-grid-shell",
    )


@module.server
def workbook_grid_server(input, output, session, state: AppState):
    ns = session.ns

    working = reactive.value(None)
    dirty = reactive.value(False)
    render_nonce = reactive.value(0)
    apply_status = reactive.value(None)

    def bump():
        with reactive.isolate():
            render_nonce.set(render_nonce() + 1)

    ## Sync the working copy from state whenever the canonical tables change
    ## (load, Apply, session restore).
    @reactive.effect
    @reactive.event(state.input_metadata)
    def _sync_working():
        tables = state.input_metadata()
        if tables is None:
            return
        working.set(dict(tables))
        dirty.set(False)
        bump()

    # ── helpers ──────────────────────────────────────────────────────────────
    def coerce_to_col(col: pd.Series, value):
        if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
            return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.api.types.is_bool_dtype(col):
            try:
                return coerce_flag(value)
            except Exception:
                return None
        return None if value is None else str(value)

    def display_df(tab_key: str) -> pd.DataFrame | None:
        tabs = working()
        if tabs is None:
            return None
        if tab_key == "data":
            data = tabs.get("data")
            return None if data is None else pd.DataFrame(data)
        return wt.editor_df_for_tab(tabs, tab_key)

    def add_blank_row(tabs: dict, key: str) -> dict:
        tabs = dict(tabs)
        if key == "data":
            df = pd.DataFrame(tabs.get("data"))
            if df.shape[1] == 0:
                return tabs
            df.loc[len(df)] = [None] * df.shape[1]
            tabs["data"] = df
            return tabs
        if key == "metrics":
            return wt.add_metric_row_to_tables(tabs)
        if key == "stratifications":
            return wt.add_stratification_row_to_tables(tabs)
        if key == "predictors":
            return wt.add_predictor_row_to_tables(tabs)
        if key == "factor_recodes":
            return wt.add_factor_recode_row_to_tables(tabs)
        if key == "custom_groups":
            return wt.add_custom_grouping_to_tables(tabs)
        if key == "site_masks":
            sm = ensure_workbook_sheet_columns(tabs.get("site_masks"), "site_masks")
            sm.loc[len(sm)] = [None] * sm.shape[1]
            tabs["site_masks"] = sm
            return tabs
        return tabs

    def delete_grid_rows(tabs: dict, key: str, rows_1based: list[int]) -> dict:
        if key == "data":
            tabs = dict(tabs)
            df = pd.DataFrame(tabs.get("data"))
            keep = [i for i in range(len(df)) if (i + 1) not in set(rows_1based)]
            tabs["data"] = df.iloc[keep].reset_index(drop=True)
            return tabs
        return wt.delete_rows_from_tables(tabs, key, rows_1based)

    @render.ui
    def view_style():
        mode = input.view_mode() if "view_mode" in input else "choose"
        return ui.tags.style(
            ".wbv-panel {display: none;} " + f".wbv-panel-{mode} {{display: block;}}"
        )

    # ── per-sheet grids + observers ──────────────────────────────────────────
    def _register_sheet(s: dict):
        key = s["key"]
        is_data = s["kind"] == "data"

        @output(id=f"grid_{key}", suspend_when_hidden=False)
        @render.data_frame
        def grid():
            render_nonce()
            with reactive.isolate():
                df = display_df(key)
            req(df is not None)
            return render.DataGrid(
                df,
                editable=True,
                selection_mode="rows",
                height="460px" if is_data else "360px",
                width="100%",
            )

        @grid.set_patches_fn
        def _patches(*, patches):
            tabs = working()
            if tabs is None:
                return []
            tabs = dict(tabs)
            if is_data:
                df = pd.DataFrame(tabs.get("data"))
                for p in patches:
                    i, j = p["row_index"], p["column_index"]
                    if 0 <= i < len(df) and 0 <= j < df.shape[1]:
                        col = df.columns[j]
                        df[col] = df[col].astype(object) if not pd.api.types.is_object_dtype(df[col]) and pd.isna(coerce_to_col(df[col], p["value"])) else df[col]
                        df.iloc[i, j] = coerce_to_col(df[col], p["value"])
                tabs["data"] = df
            else:
                edf = wt.editor_df_for_tab(tabs, key)
                for p in patches:
                    i, j = p["row_index"], p["column_index"]
                    if 0 <= i < len(edf) and 0 <= j < edf.shape[1]:
                        edf.iloc[i, j] = "" if p["value"] is None else str(p["value"])
                tabs = wt.apply_editor_df_to_tables(tabs, key, edf)
            working.set(tabs)
            dirty.set(True)
            # Return the patches unchanged: the grid shows the typed values
            # without a re-render (matches the R module's no-rerender edit).
            return patches

        @reactive.effect
        @reactive.event(input[f"add_row_{key}"])
        def _add_row():
            tabs = working()
            req(tabs is not None)
            try:
                working.set(add_blank_row(tabs, key))
            except Exception as e:  # noqa: BLE001
                ui.notification_show(str(e), type="error", duration=6)
                return
            dirty.set(True)
            bump()

        @reactive.effect
        @reactive.event(input[f"del_rows_{key}"])
        def _del_rows():
            sel = grid.cell_selection()
            rows0 = list((sel or {}).get("rows") or [])
            if not rows0:
                apply_status.set(
                    {"type": "warning", "text": "Select one or more rows to delete."}
                )
                return
            tabs = working()
            req(tabs is not None)
            working.set(delete_grid_rows(tabs, key, [r + 1 for r in rows0]))
            dirty.set(True)
            bump()

        @output(id=f"rowinfo_{key}")
        @render.ui
        def rowinfo():
            render_nonce()
            with reactive.isolate():
                df = display_df(key)
            n_row = 0 if df is None else len(df)
            n_col = 0 if df is None else df.shape[1]
            return ui.tags.span(f"{n_row} rows × {n_col} cols", class_="text-muted small")

    for s in SHEETS:
        _register_sheet(s)

    # ── data sheet: add column ───────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.add_col)
    def _add_col_modal():
        ui.modal_show(
            ui.modal(
                ui.input_text(ns("new_col_name"), "Column name", placeholder="e.g. new_metric"),
                title="Add a column to the Data sheet",
                size="m",
                easy_close=True,
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    ui.input_action_button(
                        ns("add_col_confirm"), "Add column", class_="btn btn-primary"
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.add_col_confirm)
    def _add_col_do():
        nm = (input.new_col_name() or "").strip()
        tabs = working()
        req(tabs is not None)
        if not nm:
            ui.notification_show("Enter a column name.", type="warning")
            return
        df = pd.DataFrame(tabs.get("data"))
        if nm in df.columns:
            ui.notification_show("A column with that name already exists.", type="warning")
            return
        df[nm] = pd.Series([None] * len(df), dtype=object)
        tabs = dict(tabs)
        tabs["data"] = df
        working.set(tabs)
        dirty.set(True)
        bump()
        ui.modal_remove()

    # ── data sheet: import CSV/Excel ─────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.import_csv)
    def _import_csv():
        finfo = input.import_csv()
        req(finfo)
        f = finfo[0]
        try:
            name = f.get("name", "")
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in ("xlsx", "xls"):
                df = pd.read_excel(f["datapath"])
            elif ext in ("tsv", "txt"):
                df = pd.read_csv(f["datapath"], sep="\t")
            else:
                df = pd.read_csv(f["datapath"])
        except Exception as e:  # noqa: BLE001
            ui.notification_show(f"Import failed: {e}", type="error", duration=8)
            return
        tabs = working()
        req(tabs is not None)
        tabs = dict(tabs)
        tabs["data"] = df
        working.set(tabs)
        dirty.set(True)
        bump()
        ui.notification_show(
            f"Imported {len(df)} rows × {df.shape[1]} cols into the Data sheet. "
            "Click 'Apply changes' to build.",
            type="message",
            duration=6,
        )

    # ── data sheet: paste (modal; Ctrl+V on the grid pre-fills it) ───────────
    def show_paste_modal(prefill: str = ""):
        ui.modal_show(
            ui.modal(
                ui.tags.p("Paste tab- or comma-separated values (first row = column headers)."),
                ui.input_text_area(
                    ns("paste_text"), None, value=prefill, rows=8, width="100%"
                ),
                ui.input_radio_buttons(
                    ns("paste_mode"),
                    "When applying:",
                    choices={"replace": "Replace all data", "append": "Append as new rows"},
                    selected="replace",
                    inline=True,
                ),
                title="Paste data into the Data sheet",
                size="l",
                easy_close=True,
                footer=ui.TagList(
                    ui.modal_button("Cancel"),
                    ui.input_action_button(
                        ns("paste_confirm"), "Add to Data sheet", class_="btn btn-primary"
                    ),
                ),
            )
        )

    @reactive.effect
    @reactive.event(input.paste_btn)
    def _paste_btn():
        show_paste_modal("")

    @reactive.effect
    @reactive.event(input.data_clipboard)
    def _paste_capture():
        payload = input.data_clipboard() or {}
        txt = payload.get("text") or ""
        if txt:
            show_paste_modal(txt)

    @reactive.effect
    @reactive.event(input.paste_confirm)
    def _paste_confirm():
        df = parse_pasted_table(input.paste_text() or "", has_header=True)
        if df is None or len(df) == 0:
            ui.notification_show("Nothing to parse from the pasted text.", type="warning")
            return
        tabs = working()
        req(tabs is not None)
        tabs = dict(tabs)
        base = pd.DataFrame(tabs.get("data"))
        if input.paste_mode() == "append" and base.shape[1] > 0:
            for c in base.columns:
                if c not in df.columns:
                    df[c] = None
            df = df[list(base.columns)]
            tabs["data"] = pd.concat([base, df], ignore_index=True)
        else:
            tabs["data"] = df
        working.set(tabs)
        dirty.set(True)
        bump()
        ui.modal_remove()
        ui.notification_show(
            "Data sheet updated. Click 'Apply changes' to build.", type="message", duration=6
        )

    # ── apply / status ───────────────────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.apply_changes)
    def _apply():
        tabs = working()
        req(tabs is not None)
        choose_mode = input.view_mode() == "choose"
        if choose_mode:
            prof = choose_profile()
            asg = None if prof is None else classify_assignments_from_input(input, prof)
            if asg is None or int(asg["is_metric"].sum()) < 1:
                apply_status.set(
                    {
                        "type": "warning",
                        "text": "Mark at least one column as Metric before applying.",
                    }
                )
                return
            tabs = reconcile_role_membership(tabs, asg)
            working.set(tabs)
        rebuild_app_from_tables(
            state,
            tabs,
            success_text=(
                "Column roles applied and dataset rebuilt. Recompute reference curves "
                "to refresh the analysis."
                if choose_mode
                else "Workbook changes applied and dataset rebuilt."
            ),
            error_prefix="Could not apply changes",
            status_cb=lambda status: apply_status.set(status),
        )
        # On success the state.input_metadata observer resyncs working + clears dirty.

    @render.ui
    def dirty_badge():
        if dirty():
            return ui.tags.span(
                "Unsaved edits — click Apply changes", class_="badge text-bg-warning"
            )
        return ui.tags.span("All changes applied", class_="badge text-bg-success")

    @render.ui
    def apply_status_ui():
        status = apply_status()
        if status is None:
            return None
        cls = {
            "success": "alert alert-success",
            "danger": "alert alert-danger",
            "warning": "alert alert-warning",
        }.get(status.get("type", "info"), "alert alert-info")
        return ui.div(status.get("text", ""), class_=f"{cls} py-1 px-2 mb-0 d-inline-block")

    # ── "Choose columns" view ────────────────────────────────────────────────
    @reactive.calc
    def choose_profile():
        tabs = working()
        if tabs is None:
            return None
        data = pd.DataFrame(tabs.get("data"))
        if data.shape[1] == 0:
            return None
        prof = profile_and_suggest(data)
        memb = current_role_membership(tabs)
        memb_idx = {c: i for i, c in enumerate(memb["column"])}
        prof["role_metric"] = [
            bool(memb["is_metric"].iat[memb_idx[c]]) if c in memb_idx else False
            for c in prof["column"]
        ]
        prof["role_predictor"] = [
            bool(memb["is_predictor"].iat[memb_idx[c]]) if c in memb_idx else False
            for c in prof["column"]
        ]
        prof["role_stratifier"] = [
            bool(memb["is_stratifier"].iat[memb_idx[c]]) if c in memb_idx else False
            for c in prof["column"]
        ]
        mdf = ensure_workbook_sheet_columns(tabs.get("metrics"), "metrics")
        fam_by_col = dict(zip(mdf["column_name"].astype(str), mdf["metric_family"].astype(str)))
        prof["suggested_family"] = [
            fam_by_col[c] if fam_by_col.get(c) else fam
            for c, fam in zip(prof["column"].astype(str), prof["suggested_family"])
        ]
        return prof

    @reactive.calc
    def choose_assignments_live():
        prof = choose_profile()
        if prof is None:
            return None
        return classify_assignments_from_input(input, prof)

    @render.ui
    def choose_table():
        prof = choose_profile()
        if prof is None:
            return ui.div("No data loaded.", class_="text-muted")
        return classify_table_html(ns, prof)

    @render.ui
    def choose_summary():
        asg = choose_assignments_live()
        if asg is None:
            return None
        return classify_role_summary_html(asg)

    @render.ui
    def choose_locked_note():
        tabs = working()
        if tabs is None:
            return None
        preds = ensure_workbook_sheet_columns(tabs.get("predictors"), "predictors")
        strat = ensure_workbook_sheet_columns(tabs.get("stratifications"), "stratifications")

        def is_true(v) -> bool:
            try:
                return bool(coerce_flag(v))
            except Exception:
                return False

        derived_p = [
            str(n)
            for n, d in zip(preds["display_name"], preds["derived"])
            if is_true(d) and str(n).strip()
        ]
        custom_s = [
            str(n)
            for n, t in zip(strat["display_name"], strat["strat_type"])
            if str(t) in ("custom_group", "paired") and str(n).strip()
        ]
        recodes = ensure_workbook_sheet_columns(tabs.get("factor_recodes"), "factor_recodes")
        items = []
        if derived_p:
            items.append(f"derived predictors ({', '.join(derived_p)})")
        if custom_s:
            items.append(f"grouped/paired stratifications ({', '.join(custom_s)})")
        if len(recodes) > 0:
            items.append(f"{len(recodes)} factor recode(s)")
        if not items:
            return None
        return ui.div(
            bi("lock"),
            " Preserved automatically (fine-tune in the Table view): ",
            "; ".join(items),
            ".",
            class_="text-muted small mt-2",
        )

    ## Mark dirty when the checkbox selection diverges from applied membership.
    @reactive.effect
    def _choose_dirty_watch():
        if input.view_mode() != "choose":
            return
        asg = choose_assignments_live()
        if asg is None or int(asg["is_metric"].sum()) < 1:
            return
        with reactive.isolate():
            tabs = working()
        if tabs is None:
            return
        memb = current_role_membership(tabs)

        def sel(df, col):
            return set(df.loc[df[col], "column"].astype(str))

        same = (
            sel(asg, "is_metric") == sel(memb, "is_metric")
            and sel(asg, "is_predictor") == sel(memb, "is_predictor")
            and sel(asg, "is_stratifier") == sel(memb, "is_stratifier")
        )
        if not same:
            dirty.set(True)

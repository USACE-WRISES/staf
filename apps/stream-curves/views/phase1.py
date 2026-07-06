"""Phase 1 — Initial Exploration. Port of app/modules/mod_phase1_exploration.R
(dialog mode — the only variant the app mounts).

Not ported (R keeps them for the legacy standalone sidebar page, which the py
app never renders): the sidebar layout with the metric picker, bulk screening
(run_all_screening / bulk_progress — the summary page's Recompute All covers
bulk), Reset All Metrics, and the save_phase1 block (its UI is dead in R —
save_ui returns NULL).

Scatter comparison panels keep R's hard-won invariants (see the R module's
IMPORTANT comment): compare/strat/toggle state lives only in the panel store,
shells are structural-only, panel-input observers use ignore_init, and the
page render never reads cached scatter selections.
"""

from __future__ import annotations

import asyncio
import logging
import math

import pandas as pd
import plotly.graph_objects as go
from plotnine import (
    aes,
    coord_flip,
    geom_col,
    geom_histogram,
    geom_hline,
    ggplot,
    labs,
    scale_fill_manual,
    scale_x_discrete,
)
from shiny import module, reactive, render, req, ui

from streamcurves.effects import compute_effect_sizes
from streamcurves.plot_theme import (
    get_plot_font_profile,
    minimal_plot_theme,
    plotly_axis_defaults,
    plotly_legend_defaults,
    plotly_layout,
)
from streamcurves.screening import (
    screen_stratification,
    streamcurves_site_id_column,
    streamcurves_site_label_column,
)
from views import state as st
from views import summary_state as ss
from views.plotly_html import plotly_html_fragment
from views.screening_plots import build_screening_plot_from_spec
from views.state import AppState
from views.theme import fa
from views.uihelpers import (
    explanation_card,
    no_data_alert,
    p_value_badge,
    status_badge,
)

logger = logging.getLogger("streamcurves")

_EFFECT_FILL = {
    "large": "#27ae60",
    "medium": "#2980b9",
    "small": "#f39c12",
    "negligible": "#95a5a6",
}

_NO_COMPARISON_MSG = (
    "No eligible comparison metrics or stratifications are available for this metric."
)
_SELECT_COMPARISON_MSG = (
    "Select a comparison metric or stratification to start this panel."
)
# The scatter comparison plot is delivered as an htmlwidgets-style @render.ui
# fragment (views/plotly_html.py) — figure JSON + Plotly.newPlot, exactly R's
# plotlyOutput/renderPlotly mechanism. A shinywidgets render_widget cannot be
# used here: registered late (inside the panel-registration effect) its client
# model never wires up, and registered at server init it disrupts the sibling
# screening/precheck outputs (see 17ca25b and the M8 review notes).


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip() == ""


def _fmt(v, digits: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return str(round(f, digits))


def _signif4(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(f):
        return "NA"
    if f == 0:
        return "0"
    return f"{f:.4g}"


def _plain_table(df: pd.DataFrame, p_col: str | None = None):
    """dom='t' table; optional styleInterval background on a p-value column."""
    if df is None or len(df) == 0:
        return ui.div("No values available.", class_="text-muted")

    def _cell(col, v):
        style = None
        if p_col is not None and col == p_col:
            try:
                p = float(v)
                if math.isfinite(p):
                    if p <= 0.05:
                        style = "background-color: rgba(39,174,96,0.15);"
                    elif p <= 0.10:
                        style = "background-color: rgba(243,156,18,0.15);"
            except (TypeError, ValueError):
                pass
        return ui.tags.td(str(v), style=style)

    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
        ui.tags.tbody(
            *[
                ui.tags.tr(*[_cell(c, r[c]) for c in df.columns])
                for _, r in df.iterrows()
            ]
        ),
        class_="table table-sm table-striped compact",
    )


@module.ui
def phase1_ui(dialog_mode: bool = False):
    return ui.output_ui("phase1_page")


@module.server
def phase1_server(
    input, output, session, state: AppState,
    dialog_mode: bool = False, workspace_scope: str = "standalone",
):
    ns = session.ns
    screening_results = reactive.value(None)
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
            state, workspace_scope, standalone_modal_type="phase1",
            isolate_state=isolate_state,
        )

    # ── metric/config lookups (R:41-76) ───────────────────────────────────────
    def _mc(metric=None):
        with reactive.isolate():
            if metric is None:
                metric = scatter_context_metric()
            return ((state.metric_config() or {}).get(metric) or {}) if metric else {}

    def metric_display_name(metric):
        cfg = _mc(metric)
        return cfg.get("display_name") or metric

    def metric_units_label(metric):
        return _mc(metric).get("units") or ""

    def metric_axis_label(metric):
        label = metric_display_name(metric)
        units = metric_units_label(metric)
        return f"{label} ({units})" if units else label

    def metric_column_name(metric):
        return _mc(metric).get("column_name")

    def scatter_context_metric():
        with reactive.isolate():
            if dialog_mode:
                metric = state.workspace_modal_metric() or state.current_metric()
            else:
                metric = state.current_metric()
        return str(metric) if metric else None

    # ── site identity + quick mask (R:78-116) ─────────────────────────────────
    def site_identity_frame(data=None) -> pd.DataFrame:
        if data is None:
            with reactive.isolate():
                data = state.data()
        req(data is not None)
        cols = [streamcurves_site_id_column, streamcurves_site_label_column]
        if all(c in data.columns for c in cols):
            return pd.DataFrame(
                {
                    "site_id": pd.to_numeric(
                        data[streamcurves_site_id_column], errors="coerce"
                    ).astype("Int64"),
                    "site_label": data[streamcurves_site_label_column].astype(str),
                }
            )
        return pd.DataFrame(
            {
                "site_id": range(1, len(data) + 1),
                "site_label": [f"Site {i}" for i in range(1, len(data) + 1)],
            }
        )

    def exploratory_site_choices(data=None) -> dict[str, str]:
        site_df = site_identity_frame(data)
        return {
            str(sid): f"{sid} - {lbl}"
            for sid, lbl in zip(site_df["site_id"], site_df["site_label"])
        }

    def cached_phase1_quick_mask_ids(metric=None) -> list[int]:
        if metric is None:
            metric = scatter_context_metric()
        if not metric:
            return []
        with reactive.isolate():
            cache_entry = (state.metric_phase_cache() or {}).get(metric) or {}
        ids = cache_entry.get("phase1_quick_mask_site_ids") or []
        out = set()
        for v in ids:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
        return sorted(out)

    @reactive.calc
    def phase1_quick_mask_ids() -> list[int]:
        try:
            raw = input.phase1_quick_mask_sites() or ()
        except Exception:  # noqa: BLE001 — input not bound yet
            return []
        out = set()
        for v in raw:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
        return sorted(out)

    # ── comparison choices/values (R:118-307) ─────────────────────────────────
    def scatter_metric_choices(current_metric=None) -> dict[str, str]:
        if current_metric is None:
            current_metric = scatter_context_metric()
        with reactive.isolate():
            data = state.data()
            metric_config = state.metric_config() or {}
        req(data is not None)
        keys = []
        for mk, cfg in metric_config.items():
            cfg = cfg or {}
            col_name = cfg.get("column_name")
            if (
                mk != current_metric
                and cfg.get("metric_family") != "categorical"
                and col_name
                and col_name in data.columns
            ):
                keys.append(mk)
        keys.sort(key=lambda mk: (metric_display_name(mk), mk))
        return {mk: metric_display_name(mk) for mk in keys}

    def scatter_metric_choice_keys(current_metric=None) -> list[str]:
        return list(scatter_metric_choices(current_metric).keys())

    def scatter_comparison_strat_choices(current_metric=None) -> dict[str, str]:
        if current_metric is None:
            current_metric = scatter_context_metric()
        if not current_metric:
            return {}
        metric_keys = set(scatter_metric_choice_keys(current_metric))
        strat_keys = [
            sk
            for sk in ss.get_metric_allowed_strats(state, current_metric)
            if sk not in metric_keys
        ]
        if not strat_keys:
            return {}
        strat_keys.sort(key=lambda sk: (ss.get_strat_display_name(state, sk), sk))
        return {
            sk: f"{ss.get_strat_display_name(state, sk)} (Stratification)"
            for sk in strat_keys
        }

    def scatter_panel_compare_choices(current_metric=None) -> dict[str, str]:
        return {
            **scatter_metric_choices(current_metric),
            **scatter_comparison_strat_choices(current_metric),
        }

    def scatter_comparison_kind(compare_metric, current_metric=None):
        compare_metric = str(compare_metric or "")
        if not compare_metric:
            return None
        if compare_metric in scatter_metric_choice_keys(current_metric):
            return "metric"
        if compare_metric in scatter_comparison_strat_choices(current_metric):
            return "stratification"
        with reactive.isolate():
            if (state.metric_config() or {}).get(compare_metric) is not None:
                return "metric"
            if (state.strat_config() or {}).get(compare_metric) is not None:
                return "stratification"
        return None

    def scatter_comparison_display_name(compare_metric, current_metric=None):
        compare_metric = str(compare_metric or "")
        if not compare_metric:
            return "Comparison"
        kind = scatter_comparison_kind(compare_metric, current_metric)
        if kind == "metric":
            return metric_display_name(compare_metric)
        if kind == "stratification":
            return ss.get_strat_display_name(state, compare_metric)
        return compare_metric

    def scatter_comparison_axis_label(compare_metric, current_metric=None):
        kind = scatter_comparison_kind(compare_metric, current_metric)
        if kind == "metric":
            return metric_axis_label(compare_metric)
        if kind == "stratification":
            return ss.get_strat_display_name(state, compare_metric)
        return "Comparison"

    def scatter_comparison_axis_type(compare_metric, current_metric=None):
        kind = scatter_comparison_kind(compare_metric, current_metric)
        return "category" if kind == "stratification" else "linear"

    def scatter_strat_choices(compare_metric, current_metric=None) -> dict[str, str]:
        kind = scatter_comparison_kind(compare_metric, current_metric)
        if current_metric is None:
            current_metric = scatter_context_metric()
        allowed = []
        if kind == "metric":
            allowed = ss.get_metric_allowed_strats(state, compare_metric)
        elif kind == "stratification":
            allowed = ss.get_metric_allowed_strats(state, current_metric)
        if not allowed:
            return {"none": "None"}
        allowed = sorted(allowed, key=lambda sk: (ss.get_strat_display_name(state, sk), sk))
        return {
            "none": "None",
            **{sk: ss.get_strat_display_name(state, sk) for sk in allowed},
        }

    def scatter_strat_values(strat_key) -> pd.Series:
        with reactive.isolate():
            data = state.data()
            strat_config = state.strat_config() or {}
        n = 0 if data is None else len(data)
        if not strat_key or strat_key == "none":
            return pd.Series([None] * n, dtype=object)
        sc = strat_config.get(strat_key)
        if sc is None:
            return pd.Series([None] * n, dtype=object)
        col = sc.get("column_name")
        if col and col in data.columns:
            vals = data[col]
            return vals.astype(str).where(vals.notna(), None)
        if sc.get("type") == "paired":
            primary_key, secondary_key = sc.get("primary"), sc.get("secondary")
            primary_col = ((strat_config.get(primary_key) or {}).get("column_name")
                           or primary_key)
            secondary_col = ((strat_config.get(secondary_key) or {}).get("column_name")
                             or secondary_key)
            if primary_col not in data.columns or secondary_col not in data.columns:
                return pd.Series([None] * n, dtype=object)
            pv, sv = data[primary_col], data[secondary_col]
            out = (pv.astype(str) + " | " + sv.astype(str)).astype(object)
            out[pv.isna() | sv.isna()] = None
            return out
        return pd.Series([None] * n, dtype=object)

    def scatter_comparison_values(compare_metric, current_metric=None):
        kind = scatter_comparison_kind(compare_metric, current_metric)
        with reactive.isolate():
            data = state.data()
        if kind == "metric":
            compare_col = metric_column_name(compare_metric)
            if not compare_col or compare_col not in data.columns:
                return None
            return data[compare_col]
        if kind == "stratification":
            return scatter_strat_values(compare_metric)
        return None

    # ── scatter comparison assembly (R:317-455) ───────────────────────────────
    def build_empty_scatterplot(message_text: str):
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[], y=[], mode="markers"))
        sizes = get_plot_font_profile("large_analysis")
        return plotly_layout(
            fig,
            profile="large_analysis",
            # R sends no template, so plotly.js renders on its own defaults
            # (white bg, d3 colorway); Python's default template does not.
            template="none",
            xaxis=plotly_axis_defaults(profile="large_analysis", visible=False),
            yaxis=plotly_axis_defaults(profile="large_analysis", visible=False),
            annotations=[
                {
                    "text": message_text,
                    "x": 0.5,
                    "y": 0.5,
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                    "font": {"size": sizes["annotation"], "color": "#5b6570"},
                }
            ],
            margin={"l": 40, "r": 40, "t": 20, "b": 40},
        )

    def build_scatter_comparison(compare_metric, strat_key="none") -> dict:
        current_metric = scatter_context_metric()
        current_col = metric_column_name(current_metric)
        kind = scatter_comparison_kind(compare_metric, current_metric)
        compare_label = scatter_comparison_display_name(compare_metric, current_metric)
        compare_values = scatter_comparison_values(compare_metric, current_metric)
        with reactive.isolate():
            data = state.data()

        base = {
            "metric": compare_metric,
            "compare_kind": kind,
            "compare_label": compare_label,
            "stratification": strat_key,
            "traces": [],
            "warning": None,
        }
        if (
            not current_col
            or current_col not in data.columns
            or kind is None
            or compare_values is None
            or len(compare_values) != len(data)
        ):
            base["warning"] = f"{compare_label} is missing usable comparison values."
            return base

        site_df = site_identity_frame(data)
        scatter_df = pd.DataFrame(
            {
                "current_value": data[current_col].to_numpy(),
                "compare_value": pd.Series(compare_values).to_numpy(),
                "site_id": site_df["site_id"].to_numpy(),
                "site_label": site_df["site_label"].to_numpy(),
                "strat_label_raw": scatter_strat_values(strat_key).to_numpy()
                if strat_key != "none"
                else [None] * len(data),
            }
        )
        if kind == "stratification":
            scatter_df["compare_value"] = scatter_df["compare_value"].astype(object)

        quick_mask = set(phase1_quick_mask_ids())
        if quick_mask:
            scatter_df = scatter_df[~scatter_df["site_id"].isin(quick_mask)]

        base_complete = scatter_df["current_value"].notna() & scatter_df[
            "compare_value"
        ].notna()
        if kind == "stratification":
            base_complete = base_complete & (
                scatter_df["compare_value"].astype(str).str.len() > 0
            )

        if strat_key != "none":
            scatter_df["strat_label"] = scatter_df["strat_label_raw"]
            strat_complete = pd.Series(
                [bool(v) and str(v).strip() != "" for v in scatter_df["strat_label"]],
                index=scatter_df.index,
            )
            scatter_df = scatter_df[base_complete & strat_complete]
        else:
            scatter_df = scatter_df[base_complete].copy()
            scatter_df["strat_label"] = "All sites"

        if len(scatter_df) == 0:
            base["warning"] = (
                f"{compare_label} has no complete paired rows with "
                f"{metric_display_name(current_metric)}."
            )
            return base

        traces = []
        for level_label in dict.fromkeys(scatter_df["strat_label"]):
            if level_label is None:
                continue
            trace_df = scatter_df[scatter_df["strat_label"] == level_label]
            current_hover = [_signif4(v) for v in trace_df["current_value"]]
            if kind == "stratification":
                compare_hover = [str(v) for v in trace_df["compare_value"]]
            else:
                compare_hover = [_signif4(v) for v in trace_df["compare_value"]]

            if strat_key == "none":
                trace_name = compare_label
                extra = ""
            else:
                strat_name = ss.get_strat_display_name(state, strat_key)
                trace_name = f"{compare_label} - {strat_name}: {level_label}"
                extra = f"<br>{strat_name}: {level_label}"

            hover_text = [
                f"Site ID: {sid}<br>Site Label: {slbl}<br>"
                f"{metric_display_name(current_metric)}: {ch}<br>"
                f"{compare_label}: {cph}{extra}"
                for sid, slbl, ch, cph in zip(
                    trace_df["site_id"], trace_df["site_label"],
                    current_hover, compare_hover,
                )
            ]
            traces.append({"data": trace_df, "name": trace_name, "hover_text": hover_text})

        base["traces"] = traces
        return base

    # ── panel store machinery (R:457-835) ─────────────────────────────────────
    scatter_panel_tabs_input_id = "scatter_panel_tabs"
    scatter_panel_shells = reactive.value([])
    scatter_panel_shell_version = reactive.value(0)
    scatter_panel_store = reactive.value([])
    scatter_panel_active_tab = reactive.value(None)
    scatter_panel_loaded_context = reactive.value(None)
    _panel_observers: dict[str, list] = {}
    _panel_outputs_registered: set[str] = set()

    def default_scatter_panel(panel_id="panel_1", panel_index=1, current_metric=None):
        available = list(scatter_panel_compare_choices(current_metric).keys())
        return {
            "id": panel_id,
            "label": f"Panel {panel_index}",
            "current_on_x": False,
            "compare_metric": available[0] if available else None,
            "strat_key": "none",
        }

    def next_scatter_panel_index(panels) -> int:
        max_id = 0
        for panel in panels or []:
            pid = str(panel.get("id") or "")
            if pid.startswith("panel_"):
                try:
                    max_id = max(max_id, int(pid[len("panel_"):]))
                except ValueError:
                    continue
        return max_id + 1

    def scatter_panel_strat_key_choice_spec(panel, current_metric=None) -> dict:
        compare_metric = panel.get("compare_metric")
        choices = scatter_strat_choices(compare_metric, current_metric)
        kind = scatter_comparison_kind(compare_metric, current_metric)
        disabled = (
            [compare_metric] if kind == "stratification" and compare_metric in choices
            else []
        )
        selectable = [v for v in choices if v not in (["none"] + disabled)]
        return {"choices": choices, "disabled_values": disabled,
                "selectable_values": selectable}

    def normalize_scatter_panel(panel, current_metric=None) -> dict:
        available = list(scatter_panel_compare_choices(current_metric).keys())
        panel_id = str(panel.get("id") or "panel_1")
        panel_label = str(panel.get("label") or "Panel 1").strip() or "Panel 1"

        compare_metric = str(panel.get("compare_metric") or "")
        if not compare_metric or compare_metric not in available:
            compare_metric = available[0] if available else None

        strat_spec = scatter_panel_strat_key_choice_spec(
            {"compare_metric": compare_metric}, current_metric
        )
        strat_key = str(panel.get("strat_key") or "none")
        if (
            not strat_key
            or strat_key not in strat_spec["choices"]
            or strat_key in strat_spec["disabled_values"]
        ):
            strat_key = "none"

        return {
            "id": panel_id,
            "label": panel_label,
            "current_on_x": bool(panel.get("current_on_x")),
            "compare_metric": compare_metric,
            "strat_key": strat_key,
        }

    def normalize_scatter_panel_collection(panels, current_metric=None) -> dict:
        panels = list(panels or [])
        if not panels:
            panels = [default_scatter_panel(current_metric=current_metric)]
        normalized, seen_ids = [], set()
        next_index = next_scatter_panel_index(panels)
        for idx, panel in enumerate(panels, start=1):
            panel = dict(panel or {})
            if not str(panel.get("label") or "").strip():
                panel["label"] = f"Panel {idx}"
            panel["id"] = str(panel.get("id") or f"panel_{idx}")
            if not panel["id"] or panel["id"] in seen_ids:
                panel["id"] = f"panel_{next_index}"
                next_index += 1
            norm = normalize_scatter_panel(panel, current_metric)
            normalized.append(norm)
            seen_ids.add(norm["id"])
        return {"panels": normalized}

    def scatter_panel_shells_from_panels(panels) -> list[dict]:
        return [
            {
                "id": str(p.get("id") or f"panel_{i}"),
                "label": str(p.get("label") or f"Panel {i}").strip() or f"Panel {i}",
            }
            for i, p in enumerate(panels or [], start=1)
        ]

    def resolve_scatter_panel_active_tab(panel_defs=None, candidates=None) -> str | None:
        if panel_defs is None:
            with reactive.isolate():
                panel_defs = scatter_panel_shells()
        if not panel_defs:
            return None
        panel_ids = [p["id"] for p in panel_defs]
        if candidates is None:
            with reactive.isolate():
                candidates = [
                    input[scatter_panel_tabs_input_id]()
                    if scatter_panel_tabs_input_id in input
                    else None,
                    scatter_panel_active_tab(),
                ]
        for cand in candidates:
            if cand and str(cand) in panel_ids:
                return str(cand)
        return panel_ids[0]

    def set_scatter_panel_active_tab(panel_id=None, panel_defs=None):
        active = resolve_scatter_panel_active_tab(
            panel_defs=panel_defs, candidates=[panel_id]
        )
        scatter_panel_active_tab.set(active)
        return active

    def scatter_panel_context_key(metric=None):
        if metric is None:
            metric = scatter_context_metric()
        with reactive.isolate():
            data = state.data()
            fingerprint = state.data_fingerprint()
            config_version = state.config_version()
            reset_nonce = state.app_reset_nonce()
        if not metric or data is None:
            return None
        signature = fingerprint or f"{len(data)}x{data.shape[1]}"
        return f"{metric}::{config_version or 0}::{reset_nonce or 0}::{signature}"

    def persist_scatter_panel_state(current_metric=None, panels=None):
        if current_metric is None:
            current_metric = scatter_context_metric()
        if panels is None:
            with reactive.isolate():
                panels = scatter_panel_store()
        if not current_metric or not panels:
            return
        ss.ensure_metric_phase_cache(state, current_metric)
        ss._update_cache_entry(  # noqa: SLF001 — same-package state helper
            state, current_metric,
            phase1_scatter_panels=st.deep_copy_value(panels),
        )

    def set_scatter_panel_state(panels, current_metric=None, update_shell=True) -> dict:
        normalized = normalize_scatter_panel_collection(panels, current_metric)
        if update_shell:
            scatter_panel_shells.set(
                st.deep_copy_value(scatter_panel_shells_from_panels(normalized["panels"]))
            )
            with reactive.isolate():
                scatter_panel_shell_version.set(scatter_panel_shell_version() + 1)
        with reactive.isolate():
            if scatter_panel_store() != normalized["panels"]:
                scatter_panel_store.set(st.deep_copy_value(normalized["panels"]))
        persist_scatter_panel_state(current_metric, normalized["panels"])
        return normalized

    def snapshot_scatter_panels() -> dict:
        with reactive.isolate():
            panels = scatter_panel_store()
        return normalize_scatter_panel_collection(panels, scatter_context_metric())

    def get_scatter_panel_live_panel(panel_id):
        with reactive.isolate():
            panels = scatter_panel_store()
        for panel in panels or []:
            if panel.get("id") == panel_id:
                return panel
        return None

    def apply_scatter_panel_edit(panel_id, field, value, current_metric=None):
        with reactive.isolate():
            panels = st.deep_copy_value(scatter_panel_store())
        if not panels:
            return
        for idx, panel in enumerate(panels):
            if panel.get("id") == panel_id:
                panel[field] = value
                panels[idx] = normalize_scatter_panel(panel, current_metric)
                set_scatter_panel_state(
                    panels, current_metric=current_metric, update_shell=False
                )
                return

    def prune_scatter_panel_input_observers(active_panel_ids=(), reset_all=False):
        remove_ids = (
            list(_panel_observers)
            if reset_all
            else [pid for pid in _panel_observers if pid not in active_panel_ids]
        )
        for pid in remove_ids:
            for obs in _panel_observers.pop(pid, []):
                try:
                    obs.destroy()
                except Exception:  # noqa: BLE001
                    pass

    def load_scatter_panel_state(metric=None, force=False, context_key=None):
        if metric is None:
            metric = scatter_context_metric()
        if context_key is None:
            context_key = scatter_panel_context_key(metric)
        with reactive.isolate():
            data = state.data()
        req(metric, data is not None)
        with reactive.isolate():
            loaded = scatter_panel_loaded_context()
            store_len = len(scatter_panel_store())
        if not force and loaded == context_key and store_len > 0:
            return snapshot_scatter_panels()

        with reactive.isolate():
            cache_entry = (state.metric_phase_cache() or {}).get(metric) or {}
        panel_snapshot = cache_entry.get("phase1_scatter_panels") or []
        normalized = set_scatter_panel_state(
            panel_snapshot, current_metric=metric, update_shell=True
        )
        prune_scatter_panel_input_observers(reset_all=True)
        set_scatter_panel_active_tab(
            panel_id=(normalized["panels"][0]["id"] if normalized["panels"] else None),
            panel_defs=scatter_panel_shells_from_panels(normalized["panels"]),
        )
        scatter_panel_loaded_context.set(context_key)
        return normalized

    def build_scatter_panel_state(panel) -> dict:
        current_metric = scatter_context_metric()
        available = list(scatter_panel_compare_choices(current_metric).keys())
        compare_metric = panel.get("compare_metric")
        kind = scatter_comparison_kind(compare_metric, current_metric)
        compare_label = scatter_comparison_display_name(compare_metric, current_metric)
        strat_spec = scatter_panel_strat_key_choice_spec(panel, current_metric)
        warnings: list[str] = []

        if not available:
            warnings.append(_NO_COMPARISON_MSG)
        if compare_metric and not strat_spec["selectable_values"]:
            prefix = (
                "No additional stratifications are available for "
                if kind == "stratification"
                else "No stratifications are available for "
            )
            warnings.append(f"{prefix}{compare_label}.")

        comparison = (
            build_scatter_comparison(compare_metric, panel.get("strat_key") or "none")
            if compare_metric
            else None
        )
        if comparison is not None and comparison.get("warning"):
            if comparison["warning"] not in warnings:
                warnings.append(comparison["warning"])

        return {
            "panel": panel,
            "id": panel["id"],
            "label": panel["label"],
            "current_metric": current_metric,
            "current_on_x": bool(panel.get("current_on_x")),
            "compare_kind": kind,
            "compare_label": compare_label,
            "compare_metric": compare_metric,
            "strat_key": panel.get("strat_key"),
            "available_comparisons": available,
            "disabled_strat_keys": strat_spec["disabled_values"],
            "comparison": comparison if comparison and comparison["traces"] else None,
            "warnings": warnings,
        }

    @reactive.calc
    def scatter_panel_state_map() -> dict:
        panels = scatter_panel_store()
        phase1_quick_mask_ids()  # mask changes invalidate every panel state
        if not panels:
            return {}
        states = [build_scatter_panel_state(p) for p in panels]
        return {s["id"]: s for s in states}

    def get_scatter_panel_state(panel_id):
        return scatter_panel_state_map().get(panel_id)

    def scatter_panel_plot_height_px() -> int:
        return 380 if dialog_mode else 430

    def build_scatter_panel_messages(panel_state):
        blocks = []
        if not panel_state["available_comparisons"]:
            blocks.append(ui.div(_NO_COMPARISON_MSG, class_="alert alert-info py-2"))
        elif not panel_state["compare_metric"]:
            blocks.append(ui.div(_SELECT_COMPARISON_MSG, class_="alert alert-info py-2"))
        if panel_state["warnings"]:
            blocks.append(
                ui.div(
                    ui.tags.ul(
                        *[ui.tags.li(msg) for msg in panel_state["warnings"]],
                        class_="mb-0",
                    ),
                    class_="alert alert-warning py-2",
                )
            )
        return ui.TagList(*blocks) if blocks else None

    def build_scatter_panel_ui(panel_shell):
        current_metric = scatter_context_metric()
        live = get_scatter_panel_live_panel(panel_shell["id"])
        panel_values = normalize_scatter_panel(live or panel_shell, current_metric)
        pid = panel_shell["id"]

        return ui.div(
            ui.div(
                ui.input_select(
                    ns(f"scatter_panel_compare_{pid}"), "Comparison metric:",
                    choices=scatter_panel_compare_choices(current_metric),
                    selected=panel_values.get("compare_metric"),
                    width="100%",
                ),
                ui.input_select(
                    ns(f"scatter_panel_strat_key_{pid}"),
                    "Stratify by comparison metric:",
                    choices=scatter_panel_strat_key_choice_spec(
                        panel_values, current_metric
                    )["choices"],
                    selected=panel_values.get("strat_key") or "none",
                    width="100%",
                ),
                ui.div(
                    ui.input_checkbox(
                        ns(f"scatter_panel_toggle_{pid}"),
                        "Current metric on X axis",
                        value=bool(panel_values.get("current_on_x")),
                        width="100%",
                    ),
                    class_="scatterplot-panel-toggle",
                ),
                class_="scatterplot-panel-controls",
            ),
            ui.output_ui(ns(f"scatter_panel_messages_{pid}")),
            ui.output_ui(ns(f"scatter_panel_plot_{pid}")),
            class_="scatterplot-panel-tab",
        )

    def build_scatter_panel_plot(panel_state):
        if not panel_state["available_comparisons"]:
            return build_empty_scatterplot(_NO_COMPARISON_MSG)
        if not panel_state["compare_metric"]:
            return build_empty_scatterplot(_SELECT_COMPARISON_MSG)
        if panel_state["comparison"] is None:
            return build_empty_scatterplot(
                "No complete paired rows are available for the selected comparison."
            )

        current_metric = panel_state["current_metric"]
        compare_metric = panel_state["compare_metric"]
        compare_axis_label = scatter_comparison_axis_label(compare_metric, current_metric)
        current_on_x = panel_state["current_on_x"]
        compare_axis_type = scatter_comparison_axis_type(compare_metric, current_metric)

        fig = go.Figure()
        for trace in panel_state["comparison"]["traces"]:
            df = trace["data"]
            x = df["current_value"] if current_on_x else df["compare_value"]
            y = df["compare_value"] if current_on_x else df["current_value"]
            fig.add_trace(
                go.Scatter(
                    x=list(x), y=list(y), mode="markers", name=trace["name"],
                    text=trace["hover_text"], hoverinfo="text",
                    marker={"size": 7, "opacity": 0.72}, showlegend=True,
                )
            )

        return plotly_layout(
            fig,
            profile="large_analysis",
            template="none",  # plotly.js defaults — what R renders on
            xaxis=plotly_axis_defaults(
                profile="large_analysis",
                title_text=metric_axis_label(current_metric)
                if current_on_x
                else compare_axis_label,
                standoff=18,
                type="linear" if current_on_x else compare_axis_type,
            ),
            yaxis=plotly_axis_defaults(
                profile="large_analysis",
                title_text=compare_axis_label
                if current_on_x
                else metric_axis_label(current_metric),
                standoff=18,
                type=compare_axis_type if current_on_x else "linear",
            ),
            legend=plotly_legend_defaults(
                profile="large_analysis", orientation="h",
                x=0.5, xanchor="center", y=1.08, yanchor="bottom",
            ),
            hovermode="closest",
            margin={"l": 82, "r": 24, "t": 58, "b": 48},
        )

    # ── page (R:1046-1189, dialog branch) ─────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def phase1_page():
        if not workspace_active():
            return None
        if state.data() is None:
            return no_data_alert()

        return ui.div(
            explanation_card(
                "Exploratory Analysis",
                ui.tags.p(
                    "Which stratifications show meaningful group separation for "
                    "this metric?"
                ),
                ui.tags.p(
                    "Run screening to test for statistical significance "
                    "(Kruskal-Wallis) and effect sizes (epsilon-squared) in a single "
                    "pass. Review boxplots and summary statistics to identify "
                    "stratifications that may be useful for downstream analysis."
                ),
                ui.tags.p(
                    ui.tags.strong("Effect size thresholds:"),
                    " <0.01 negligible, 0.01–0.06 small, 0.06–0.14 medium, "
                    ">0.14 large.",
                ),
            ),
            ui.card(
                ui.card_body(
                    ui.input_selectize(
                        ns("phase1_quick_mask_sites"),
                        "Temporarily hide sites in exploratory plots:",
                        choices=exploratory_site_choices(state.data()),
                        # Selection stays empty at render; the context loader
                        # seeds it via update_selectize (reading cached state
                        # here used to remount + revert the controls in R).
                        selected=[],
                        multiple=True,
                    ),
                    ui.tags.small(
                        "Affects exploratory scatterplots, histograms, and boxplots "
                        "only. It does not recompute screening statistics or effect "
                        "sizes.",
                        class_="text-muted d-block mt-2",
                    ),
                    class_="py-2 phase1-quick-mask-card-body",
                ),
                class_="phase1-quick-mask-card mb-3",
            ),
            ui.card(
                ui.card_header(
                    ui.tags.span("Scatterplot Comparison Panels"),
                    ui.output_ui(ns("scatter_panel_actions")),
                    class_="d-flex flex-wrap align-items-center "
                    "justify-content-between gap-2",
                ),
                ui.card_body(
                    ui.output_ui(ns("scatter_panel_list_ui")),
                    class_="scatterplot-comparison-card-body",
                ),
                class_="scatterplot-comparison-card mb-3",
            ),
            ui.card(
                ui.card_body(
                    ui.layout_column_wrap(
                        ui.output_ui(ns("metric_info_table")),
                        ui.output_ui(ns("metric_info_controls")),
                        width=1 / 2,
                    ),
                    class_="py-2",
                ),
                class_="mb-3",
            ),
            ui.card(
                ui.card_header("Metric Summary"),
                ui.card_body(
                    ui.layout_column_wrap(
                        ui.output_ui(ns("precheck_stats")),
                        ui.output_plot(ns("precheck_hist"), height="200px"),
                        width=1 / 2,
                    )
                ),
                class_="mb-3",
            ),
            ui.output_ui(ns("artifact_status")),
            ui.output_ui(ns("results_ui")),
            ui.output_ui(ns("candidate_ui")),
            class_="workspace-phase-body",
        )

    # ── metric info (R:1242-1325) ─────────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def metric_info_table():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric)
        req(mc)
        precheck = state.precheck_df()
        completed = metric in (state.completed_metrics() or {})

        direction = "Neutral"
        if mc.get("higher_is_better") is True:
            direction = "Higher is better"
        elif mc.get("higher_is_better") is False:
            direction = "Lower is better"

        rows = [
            ui.tags.tr(
                ui.tags.td("Family:", class_="info-label"),
                ui.tags.td(mc.get("metric_family"), class_="info-value"),
            ),
            ui.tags.tr(
                ui.tags.td("Units:", class_="info-label"),
                ui.tags.td(mc.get("units"), class_="info-value"),
            ),
            ui.tags.tr(
                ui.tags.td("Direction:", class_="info-label"),
                ui.tags.td(direction, class_="info-value"),
            ),
        ]
        if precheck is not None and len(precheck) > 0:
            p_rows = precheck[precheck["metric"] == metric]
            if len(p_rows) > 0:
                rows.append(
                    ui.tags.tr(
                        ui.tags.td("n obs:", class_="info-label"),
                        ui.tags.td(str(p_rows["n_obs"].iloc[0]), class_="info-value"),
                    )
                )
                rows.append(
                    ui.tags.tr(
                        ui.tags.td("Status:", class_="info-label"),
                        ui.tags.td(status_badge(str(p_rows["precheck_status"].iloc[0]))),
                    )
                )

        return ui.div(
            ui.tags.table(ui.tags.tbody(*rows), class_="table table-sm mb-0"),
            ui.div(status_badge("pass", "COMPLETED"), class_="mt-1")
            if completed
            else None,
            class_="metric-info-card",
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def metric_info_controls():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric)
        req(mc)
        allowed = ss.get_metric_allowed_strats(state, metric)
        choices = {sk: ss.get_strat_display_name(state, sk) for sk in allowed}
        return ui.div(
            ui.input_selectize(
                ns("strat_checks"), "Stratifications to Test:",
                choices=choices, selected=allowed, multiple=True,
            ),
            ui.input_action_button(
                ns("run_screening"), "Run this metric screening",
                class_="btn btn-primary btn-sm w-100", icon=fa("play"),
            ),
            ui.input_action_button(
                ns("reset_metric"), "Reset This Metric",
                class_="btn btn-outline-danger btn-sm w-100 mt-1",
                icon=fa("arrow-rotate-left"),
            ),
        )

    # ── context load + quick-mask persistence (R:1328-1366) ───────────────────
    @reactive.effect
    def _load_context():
        # Deps mirror R's observeEvent list.
        state.current_metric()
        state.workspace_modal_metric()
        data = state.data()
        state.config_version()
        state.app_reset_nonce()

        current_metric = scatter_context_metric()
        if data is None or not current_metric:
            prune_scatter_panel_input_observers(reset_all=True)
            scatter_panel_shells.set([])
            with reactive.isolate():
                scatter_panel_shell_version.set(scatter_panel_shell_version() + 1)
            scatter_panel_store.set([])
            scatter_panel_active_tab.set(None)
            scatter_panel_loaded_context.set(None)
            return

        context_key = scatter_panel_context_key(current_metric)
        load_scatter_panel_state(current_metric, context_key=context_key)
        cached_mask = [str(v) for v in cached_phase1_quick_mask_ids(current_metric)]
        ui.update_selectize(
            "phase1_quick_mask_sites",
            choices=exploratory_site_choices(data),
            selected=cached_mask,
            session=session,
        )

    @reactive.effect
    def _persist_quick_mask():
        current_metric = scatter_context_metric()
        with reactive.isolate():
            data = state.data()
        if not current_metric or data is None:
            return
        quick_mask = phase1_quick_mask_ids()
        cached = cached_phase1_quick_mask_ids(current_metric)
        if cached == quick_mask:
            return
        ss.ensure_metric_phase_cache(state, current_metric)
        ss._update_cache_entry(  # noqa: SLF001
            state, current_metric, phase1_quick_mask_site_ids=quick_mask
        )

    # ── panel input observers (R:1368-1495) ───────────────────────────────────
    def sync_scatter_panel_strat_option_state(panel_id, current_metric=None):
        panel = get_scatter_panel_live_panel(panel_id)
        if panel is None:
            return
        strat_spec = scatter_panel_strat_key_choice_spec(panel, current_metric)

        async def _send():
            try:
                await asyncio.sleep(0.1)
                await session.send_custom_message(
                    "setSelectOptionsDisabled",
                    {
                        "inputId": ns(f"scatter_panel_strat_key_{panel_id}"),
                        "disabledValues": strat_spec["disabled_values"],
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("scatter strat option sync failed")

        _launch(_send())

    def register_scatter_panel_input_observers(panel_id):
        if panel_id in _panel_observers:
            return
        pid = panel_id
        toggle_id = f"scatter_panel_toggle_{pid}"
        compare_id = f"scatter_panel_compare_{pid}"
        strat_id = f"scatter_panel_strat_key_{pid}"

        @reactive.effect
        @reactive.event(input[toggle_id], ignore_init=True)
        def _toggle():
            value = input[toggle_id]()
            if value is None:
                return
            panel = get_scatter_panel_live_panel(pid)
            if panel is None or bool(panel.get("current_on_x")) == bool(value):
                return
            apply_scatter_panel_edit(pid, "current_on_x", bool(value))

        @reactive.effect
        @reactive.event(input[compare_id], ignore_init=True)
        def _compare():
            value = input[compare_id]()
            if not value:
                return
            panel = get_scatter_panel_live_panel(pid)
            if panel is None or panel.get("compare_metric") == value:
                return
            apply_scatter_panel_edit(pid, "compare_metric", value)
            updated = get_scatter_panel_live_panel(pid)
            if updated is not None:
                ui.update_select(
                    strat_id,
                    choices=scatter_panel_strat_key_choice_spec(
                        updated, scatter_context_metric()
                    )["choices"],
                    selected=updated.get("strat_key") or "none",
                    session=session,
                )
                sync_scatter_panel_strat_option_state(pid, scatter_context_metric())

        @reactive.effect
        @reactive.event(input[strat_id], ignore_init=True)
        def _strat():
            value = input[strat_id]()
            if not value:
                return
            panel = get_scatter_panel_live_panel(pid)
            if panel is None or (panel.get("strat_key") or "none") == value:
                return
            apply_scatter_panel_edit(pid, "strat_key", value)

        _panel_observers[pid] = [_toggle, _compare, _strat]

    @reactive.effect
    def _manage_panel_observers():
        panel_ids = [p["id"] for p in scatter_panel_shells()]
        if not panel_ids:
            prune_scatter_panel_input_observers(reset_all=True)
            return
        for pid in panel_ids:
            register_scatter_panel_input_observers(pid)
        prune_scatter_panel_input_observers(active_panel_ids=panel_ids)

    @reactive.effect
    def _sync_disabled_options():
        current_metric = scatter_context_metric()
        panel_ids = [p["id"] for p in scatter_panel_store()]
        if not current_metric or not panel_ids:
            return
        for pid in panel_ids:
            sync_scatter_panel_strat_option_state(pid, current_metric)

    # ── panel actions + tabs (R:1497-1622) ────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def scatter_panel_actions():
        panel_defs = scatter_panel_shells()
        active = resolve_scatter_panel_active_tab(panel_defs=panel_defs)
        can_remove = len(panel_defs) > 1 and active is not None
        return ui.div(
            ui.input_action_button(
                ns("add_scatter_panel"), "Add Panel",
                class_="btn btn-outline-primary btn-sm", icon=fa("plus"),
            ),
            ui.input_action_button(
                ns("remove_scatter_panel"), "Remove Panel",
                class_="btn btn-outline-danger btn-sm", icon=fa("trash"),
                disabled=not can_remove,
            ),
            class_="d-flex flex-wrap align-items-center gap-2",
        )

    @reactive.effect
    @reactive.event(input[scatter_panel_tabs_input_id], ignore_init=True)
    def _track_active_tab():
        active = resolve_scatter_panel_active_tab(
            candidates=[input[scatter_panel_tabs_input_id]()]
        )
        with reactive.isolate():
            if scatter_panel_active_tab() != active:
                scatter_panel_active_tab.set(active)

    @reactive.effect
    @reactive.event(input.add_scatter_panel, ignore_init=True)
    def _add_panel():
        current_metric = scatter_context_metric()
        with reactive.isolate():
            data = state.data()
        req(current_metric, data is not None)
        snapshot = snapshot_scatter_panels()
        next_index = next_scatter_panel_index(snapshot["panels"])
        new_panel = normalize_scatter_panel(
            default_scatter_panel(
                panel_id=f"panel_{next_index}", panel_index=next_index,
                current_metric=current_metric,
            ),
            current_metric,
        )
        updated = snapshot["panels"] + [new_panel]
        set_scatter_panel_state(updated, current_metric=current_metric, update_shell=True)
        set_scatter_panel_active_tab(
            panel_id=new_panel["id"],
            panel_defs=scatter_panel_shells_from_panels(updated),
        )

    @reactive.effect
    @reactive.event(input.remove_scatter_panel, ignore_init=True)
    def _remove_panel():
        current_metric = scatter_context_metric()
        with reactive.isolate():
            data = state.data()
        req(current_metric, data is not None)
        snapshot = snapshot_scatter_panels()
        if len(snapshot["panels"]) <= 1:
            return
        active = resolve_scatter_panel_active_tab(
            panel_defs=scatter_panel_shells_from_panels(snapshot["panels"])
        )
        panel_ids = [p["id"] for p in snapshot["panels"]]
        if active not in panel_ids:
            return
        remove_idx = panel_ids.index(active)
        remaining = [p for i, p in enumerate(snapshot["panels"]) if i != remove_idx]
        next_active = remaining[min(remove_idx, len(remaining) - 1)]["id"]
        set_scatter_panel_state(remaining, current_metric=current_metric, update_shell=True)
        set_scatter_panel_active_tab(
            panel_id=next_active,
            panel_defs=scatter_panel_shells_from_panels(remaining),
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def scatter_panel_list_ui():
        current_metric = scatter_context_metric()
        req(current_metric, state.data() is not None)
        scatter_panel_shell_version()
        panel_defs = scatter_panel_shells()
        req(len(panel_defs) > 0)
        selected = resolve_scatter_panel_active_tab(panel_defs=panel_defs)
        return ui.navset_tab(
            *[
                ui.nav_panel(p["label"], build_scatter_panel_ui(p), value=p["id"])
                for p in panel_defs
            ],
            id=ns(scatter_panel_tabs_input_id),
            selected=selected,
        )

    def _register_panel_outputs(panel_id):
        if panel_id in _panel_outputs_registered:
            return
        _panel_outputs_registered.add(panel_id)
        pid = panel_id

        @output(id=f"scatter_panel_messages_{pid}", suspend_when_hidden=False)
        @render.ui
        def _messages():
            req(scatter_context_metric(), state.data() is not None)
            panel_state = get_scatter_panel_state(pid)
            req(panel_state is not None)
            return build_scatter_panel_messages(panel_state)

        @output(id=f"scatter_panel_plot_{pid}", suspend_when_hidden=False)
        @render.ui
        def _plot():
            req(scatter_context_metric(), state.data() is not None)
            panel_state = get_scatter_panel_state(pid)
            req(panel_state is not None)
            return plotly_html_fragment(
                build_scatter_panel_plot(panel_state),
                height_px=scatter_panel_plot_height_px(),
            )

    @reactive.effect
    def _register_panel_output_renderers():
        # Reactive deps mirror R's registration observer (R:1624-1657): the
        # modal/current metric, the dataset, and the panel shells/version. All
        # are read BEFORE the guard — a first run with no data loaded must
        # still subscribe, or the effect dies with no dependencies and the
        # per-panel outputs never register (the scatter panels render blank).
        if dialog_mode:
            state.workspace_modal_metric()
        state.current_metric()
        data = state.data()
        scatter_panel_shell_version()
        shells = scatter_panel_shells()
        current_metric = scatter_context_metric()
        if not current_metric or data is None:
            return
        for p in shells:
            _register_panel_outputs(p["id"])

    # ── precheck summary (R:1659-1701) ────────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def precheck_stats():
        metric = state.current_metric()
        req(metric)
        precheck = state.precheck_df()
        req(precheck is not None)
        rows = precheck[precheck["metric"] == metric]
        req(len(rows) == 1)
        row = rows.iloc[0]
        stats = pd.DataFrame(
            {
                "Statistic": ["n", "Missing", "Min", "Median", "Mean", "Max", "SD"],
                "Value": [
                    str(row["n_obs"]), str(row["n_missing"]),
                    _fmt(row["min"]), _fmt(row["median"]), _fmt(row["mean"]),
                    _fmt(row["max"]), _fmt(row["sd"]),
                ],
            }
        )
        return _plain_table(stats)

    @output(suspend_when_hidden=False)
    @render.plot
    def precheck_hist():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric) or {}
        col = mc.get("column_name")
        data = state.data()
        req(data is not None and col in data.columns)

        quick_mask = set(phase1_quick_mask_ids())
        if quick_mask:
            visible = ~site_identity_frame(data)["site_id"].isin(quick_mask).to_numpy()
            data = data[visible]

        vals = pd.to_numeric(data[col], errors="coerce").dropna()
        req(len(vals) > 0)
        return (
            ggplot(pd.DataFrame({"value": vals}), aes(x="value"))
            + geom_histogram(bins=12, fill="steelblue", alpha=0.7, color="white")
            + labs(x=mc.get("units"), y="Count")
            + minimal_plot_theme()
        )

    # ── screening state + artifact refresh (R:1704-1858) ─────────────────────
    def set_screening_state(metric=None):
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
        display = ss.get_metric_phase1_display_state(state, metric)
        if display is not None and len(display["results"]) > 0:
            screening_results.set(display)
        else:
            screening_results.set(None)

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
        st.set_analysis_tab_status(state, "exploratory", resolved, request_id)
        if complete:
            st.complete_analysis_tab_preload(state, "exploratory", resolved, request_id)
        return resolved

    def refresh_screening_artifacts(metric=None, show_progress: bool = True):
        with reactive.isolate():
            if metric is None:
                metric = state.current_metric()
        if not workspace_active(isolate_state=True):
            return False
        if not ss.metric_needs_phase1_artifact_refresh(state, metric):
            artifacts_loading.set(False)
            artifacts_error.set(None)
            set_screening_state(metric)
            return False

        artifacts_loading.set(True)
        artifacts_error.set(None)
        try:
            ss.ensure_metric_phase1_artifacts(state, metric)
            artifacts_error.set(None)
        except Exception as e:  # noqa: BLE001
            logger.exception("phase1 artifact refresh failed")
            artifacts_error.set(str(e))
        finally:
            artifacts_loading.set(False)
            set_screening_state(metric)
        return True

    def load_screening_results(metric=None):
        artifacts_loading.set(False)
        artifacts_error.set(None)
        set_screening_state(metric)

    @reactive.effect
    @reactive.event(state.workspace_modal_ready_nonce, ignore_init=True)
    def _modal_ready():
        if dialog_mode and workspace_active():
            with reactive.isolate():
                modal_metric = state.workspace_modal_metric() or state.current_metric()
            if not modal_metric:
                return
            load_screening_results(modal_metric)

    @reactive.effect
    @reactive.event(state.analysis_tab_preload_nonce, ignore_init=True)
    def _preload():
        if not dialog_mode or workspace_scope != "analysis" or not workspace_active():
            return
        with reactive.isolate():
            if state.analysis_tab_preload_tab() != "exploratory":
                return
            request_id = state.analysis_tab_request_id()
            modal_metric = state.workspace_modal_metric() or state.current_metric()
        if not modal_metric or not st.analysis_tab_request_is_current(state, request_id):
            return
        load_screening_results(modal_metric)
        refresh_screening_artifacts(modal_metric, show_progress=False)
        sync_analysis_tab_state(request_id=request_id, complete=True)

    @output(suspend_when_hidden=False)
    @render.ui
    def artifact_status():
        if artifacts_loading():
            return ui.div(
                ui.tags.span(class_="streamcurves-inline-spinner", aria_hidden="true"),
                ui.tags.span(
                    "Loading full exploratory details. Summary results are available "
                    "while plots and pairwise tables regenerate."
                ),
                class_="alert alert-info d-flex align-items-center gap-2",
            )
        err = artifacts_error()
        if err:
            return ui.div(
                ui.tags.span(f"Could not load full exploratory details: {err}"),
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
        refresh_screening_artifacts()
        sync_analysis_tab_state()

    # ── run screening (R:1860-1926) ───────────────────────────────────────────
    @reactive.effect
    @reactive.event(input.run_screening)
    def _run_screening():
        metric = state.current_metric()
        strat_checks = list(input.strat_checks() or ())
        req(metric, len(strat_checks) > 0)
        with reactive.isolate():
            data = state.data()
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}

        p = ui.Progress(min=0, max=1)
        p.set(value=0, message="Running exploratory screening...")
        try:
            results_list = []
            for i, sk in enumerate(strat_checks, start=1):
                p.set(value=0.5 * i / len(strat_checks),
                      message="Running exploratory screening...")
                results_list.append(
                    screen_stratification(data, metric, sk, metric_config, strat_config)
                )

            result_rows = pd.concat(
                [r["result_row"] for r in results_list], ignore_index=True
            )
            pairwise_frames = [
                r["pairwise_df"] for r in results_list
                if r["pairwise_df"] is not None and len(r["pairwise_df"]) > 0
            ]
            pairwise_rows = (
                pd.concat(pairwise_frames, ignore_index=True)
                if pairwise_frames
                else pd.DataFrame()
            )
            plot_specs = {
                sk: r["plot_spec"]
                for sk, r in zip(strat_checks, results_list)
                if r.get("plot_spec") is not None
            }

            screening = {
                "results": result_rows,
                "pairwise": pairwise_rows,
                "plots": {},
                "plot_specs": plot_specs,
            }
            state.phase1_screening.set(screening)

            with reactive.isolate():
                all_l1 = dict(state.all_layer1_results() or {})
            all_l1[metric] = result_rows
            state.all_layer1_results.set(all_l1)

            p.set(value=0.8, message="Running exploratory screening...",
                  detail="Computing effect sizes...")
            strat_keys = list(dict.fromkeys(result_rows["stratification"]))
            es = compute_effect_sizes(data, metric, strat_keys, metric_config, strat_config)
            state.phase1_effect_sizes.set(es)
            with reactive.isolate():
                all_l2 = dict(state.all_layer2_results() or {})
            all_l2[metric] = es
            state.all_layer2_results.set(all_l2)

            ss.ensure_metric_phase_cache(state, metric)
            ss._update_cache_entry(  # noqa: SLF001
                state, metric,
                phase1_screening=screening,
                phase1_effect_sizes=es,
                phase1_artifact_mode="full",
            )
            with reactive.isolate():
                cands = dict(state.phase1_candidates() or {})
            cands[metric] = ss.build_metric_phase1_candidate_table_from_sources(
                metric=metric,
                allowed=ss.get_metric_allowed_strats(state, metric),
                existing=None, l1=result_rows, l2=es, include_all_allowed=True,
            )
            state.phase1_candidates.set(cands)
            p.set(value=1)
        finally:
            p.close()

        load_screening_results(metric)
        st.notify_workspace_refresh(state)

    # ── results UI (R:1929-2123) ──────────────────────────────────────────────
    registered_boxplots: set[str] = set()

    @output(suspend_when_hidden=False)
    @render.ui
    def results_ui():
        res = screening_results()
        req(res is not None)
        with reactive.isolate():
            strat_config = state.strat_config() or {}

        boxplot_card = None
        plot_specs = res.get("plot_specs") or {}
        if plot_specs:
            for sk in plot_specs:
                _register_boxplot(sk)
            boxplot_card = ui.card(
                ui.card_header(
                    ui.tags.span("Boxplots"),
                    ui.input_switch(ns("boxplot_points"), "Show datapoints", value=False),
                    class_="d-flex flex-wrap align-items-center "
                    "justify-content-between gap-2",
                ),
                ui.card_body(
                    ui.navset_tab(
                        *[
                            ui.nav_panel(
                                (strat_config.get(sk) or {}).get("display_name") or sk,
                                ui.output_plot(ns(f"plot_{sk}"), height="500px"),
                            )
                            for sk in plot_specs
                        ]
                    )
                ),
                class_="mb-3",
            )

        effect_sizes = res.get("effect_sizes")
        effect_card = None
        if effect_sizes is not None and len(effect_sizes) > 0:
            effect_card = ui.card(
                ui.card_header("Effect Size by Stratification"),
                ui.card_body(ui.output_plot(ns("effect_bar"), height="300px")),
                class_="mb-3",
            )

        pairwise = res.get("pairwise")
        pairwise_section = None
        if pairwise is not None and len(pairwise) > 0:
            pairwise_section = ui.accordion(
                ui.accordion_panel(
                    "Pairwise Wilcoxon Details",
                    ui.output_ui(ns("pairwise_table")),
                ),
                open=False,
            )

        return ui.TagList(
            ui.card(
                ui.card_header("Screening Results"),
                ui.card_body(ui.output_ui(ns("results_table"))),
                class_="mb-3",
            ),
            boxplot_card,
            effect_card,
            pairwise_section,
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def results_table():
        res = screening_results()
        req(res is not None)
        with reactive.isolate():
            strat_config = state.strat_config() or {}

        sig_df = res["results"][
            ["stratification", "p_value", "classification", "min_group_n"]
        ].copy()
        effect_sizes = res.get("effect_sizes")
        if effect_sizes is not None and len(effect_sizes) > 0:
            es_df = effect_sizes[
                ["stratification", "epsilon_squared", "effect_size_label"]
            ]
        else:
            es_df = pd.DataFrame(
                {"stratification": [], "epsilon_squared": [], "effect_size_label": []}
            )
        merged = sig_df.merge(es_df, on="stratification", how="left")

        display = pd.DataFrame(
            {
                "Stratification": [
                    (strat_config.get(sk) or {}).get("display_name") or sk
                    for sk in merged["stratification"]
                ],
                "p-value": [_fmt(v, 4) for v in merged["p_value"]],
                "Effect (eps-sq)": [_fmt(v, 4) for v in merged["epsilon_squared"]],
                "Effect Label": [
                    "" if _is_blank(v) else str(v) for v in merged["effect_size_label"]
                ],
                "Min Group n": [
                    "" if _is_blank(v) else str(int(v)) for v in merged["min_group_n"]
                ],
                "Classification": merged["classification"].astype(str),
            }
        )
        return _plain_table(display, p_col="p-value")

    @output(suspend_when_hidden=False)
    @render.ui
    def pairwise_table():
        res = screening_results()
        req(res is not None)
        pairwise = res.get("pairwise")
        req(pairwise is not None and len(pairwise) > 0)
        display = pairwise.copy()
        for col, digits in (("statistic", 3), ("p_value", 4), ("p_adjusted", 4)):
            if col in display.columns:
                display[col] = [_fmt(v, digits) for v in display[col]]
        return ui.div(
            _plain_table(display),
            style="max-height: 420px; overflow-y: auto;",
        )

    def _register_boxplot(sk: str):
        if sk in registered_boxplots:
            return
        registered_boxplots.add(sk)

        @output(id=f"plot_{sk}", suspend_when_hidden=False)
        @render.plot
        def _bp():
            res = screening_results()
            req(res is not None)
            spec = (res.get("plot_specs") or {}).get(sk)
            req(spec is not None)
            with reactive.isolate():
                metric_config = state.metric_config() or {}
                strat_config = state.strat_config() or {}
            show_points = bool(input.boxplot_points()) if "boxplot_points" in input else False
            fig = build_screening_plot_from_spec(
                spec, metric_config, strat_config,
                font_profile="large_analysis", show_points=show_points,
                masked_site_ids=phase1_quick_mask_ids(),
            )
            req(fig is not None)
            return fig

    @render.plot
    def effect_bar():
        res = screening_results()
        req(res is not None)
        effect_sizes = res.get("effect_sizes")
        req(effect_sizes is not None and len(effect_sizes) > 0)
        with reactive.isolate():
            metric = state.current_metric()
            metric_config = state.metric_config() or {}
            strat_config = state.strat_config() or {}

        plot_df = effect_sizes[effect_sizes["epsilon_squared"].notna()].copy()
        req(len(plot_df) > 0)
        plot_df["strat_label"] = [
            (strat_config.get(sk) or {}).get("display_name") or sk
            for sk in plot_df["stratification"]
        ]
        order = plot_df.sort_values("epsilon_squared")["strat_label"].tolist()
        return (
            ggplot(
                plot_df,
                aes(x="strat_label", y="epsilon_squared", fill="effect_size_label"),
            )
            + geom_col(width=0.6)
            + scale_x_discrete(limits=order)
            + scale_fill_manual(values=_EFFECT_FILL, name="Effect Size")
            + geom_hline(
                yintercept=[0.01, 0.06, 0.14], linetype="dashed",
                color="gray", alpha=0.7,
            )
            + coord_flip()
            + labs(
                title="Effect Size: "
                + ((metric_config.get(metric) or {}).get("display_name") or metric),
                x=None,
                y="ε² (Epsilon-squared)",
            )
            + minimal_plot_theme()
        )

    # ── candidate shortlist (R:2244-2305) ─────────────────────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def candidate_ui():
        res = screening_results()
        req(res is not None)
        metric = state.current_metric()
        candidates = ss.get_metric_phase1_candidate_table(state, metric)
        req(candidates is not None and len(candidates) > 0)
        with reactive.isolate():
            strat_config = state.strat_config() or {}

        rows = []
        for _, row in candidates.iterrows():
            sk = row["stratification"]
            sc = strat_config.get(sk) or {}
            selected_status = row.get("candidate_status") or "not_promising"
            row_class = {
                "promising": "candidate-promising",
                "possible": "candidate-possible",
                "not_promising": "candidate-not-promising",
            }.get(selected_status, "")

            badges = [
                status_badge(
                    {"promising": "pass", "possible": "caution"}.get(
                        selected_status, "not_applicable"
                    ),
                    {"promising": "Promising", "possible": "Possible"}.get(
                        selected_status, "Not Promising"
                    ),
                )
            ]
            p_val = row.get("p_value")
            if not _is_blank(p_val):
                badges.append(p_value_badge(p_val))
            eps = row.get("epsilon_squared")
            if not _is_blank(eps):
                badges.append(
                    ui.tags.span(
                        f"eps^2: {round(float(eps), 4):.4f}",
                        class_="badge bg-light text-dark border",
                    )
                )
            es_label = row.get("effect_size_label")
            if not _is_blank(es_label):
                badges.append(
                    status_badge(
                        {"large": "pass", "medium": "pass", "small": "caution"}.get(
                            str(es_label), "not_applicable"
                        ),
                        f"Effect: {es_label}",
                    )
                )

            rows.append(
                ui.div(
                    ui.tags.strong(
                        sc.get("display_name") or sk, style="min-width: 220px;"
                    ),
                    *badges,
                    class_=f"d-flex align-items-center gap-3 mb-2 p-2 rounded {row_class}",
                )
            )

        return ui.card(
            ui.card_header("Automatic Exploratory Shortlist"),
            ui.card_body(
                ui.tags.p(
                    "This shortlist is generated automatically from significance and "
                    "effect size and is used as guidance for later analysis steps.",
                    class_="text-muted",
                ),
                *rows,
            ),
            class_="mb-3",
        )

    # ── reset this metric (R:2378-2405) ───────────────────────────────────────
    @reactive.effect
    @reactive.event(input.reset_metric, ignore_init=True)
    def _reset_metric():
        metric = state.current_metric()
        req(metric)

        for field in st.PHASE_STATE_FIELDS:
            getattr(state, field).set(None)

        with reactive.isolate():
            cache = dict(state.metric_phase_cache() or {})
            completed = dict(state.completed_metrics() or {})
            all_l1 = dict(state.all_layer1_results() or {})
            all_l2 = dict(state.all_layer2_results() or {})
            cands = dict(state.phase1_candidates() or {})
            verification = dict(state.phase3_verification() or {})
            decision_log = state.decision_log()
        for container in (cache, completed, all_l1, all_l2, cands, verification):
            container.pop(metric, None)
        state.metric_phase_cache.set(cache)
        state.completed_metrics.set(completed)
        state.all_layer1_results.set(all_l1)
        state.all_layer2_results.set(all_l2)
        state.phase1_candidates.set(cands)
        state.phase3_verification.set(verification)

        if decision_log is not None and len(decision_log) > 0:
            state.decision_log.set(decision_log[decision_log["metric"] != metric])

        screening_results.set(None)
        load_scatter_panel_state(metric, force=True)

        ui.notification_show(f"Reset complete for {metric}.", type="message", duration=3)
        st.notify_workspace_refresh(state)

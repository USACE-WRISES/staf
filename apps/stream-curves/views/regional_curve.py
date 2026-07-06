"""Regional Curves page — port of app/modules/mod_regional_curve.R.

Power-function (log-log) curves for bankfull metrics vs drainage area. The R
ggplot objects become plotnine renders of the data-only plot specs produced by
``streamcurves.regional`` (fit ribbon + boxplot spec). ggpubr's pairwise
significance brackets are drawn from the spec's ``brackets`` geometry
(segments + p-value labels — plotnine has no bracket geom); the omnibus
Kruskal-Wallis result is carried in the boxplot subtitle.
"""

from __future__ import annotations

import logging
import math

import pandas as pd
from plotnine import (
    aes,
    annotate,
    geom_boxplot,
    geom_jitter,
    geom_line,
    geom_point,
    geom_ribbon,
    geom_segment,
    geom_smooth,
    geom_text,
    ggplot,
    guides,
    labs,
    scale_fill_cmap_d,
    scale_x_log10,
    scale_y_log10,
)
from shiny import module, reactive, render, req, ui

from streamcurves.plot_theme import geom_text_size, minimal_plot_theme
from streamcurves.regional import build_regional_boxplot_spec, fit_regional_curve
from views.state import AppState
from views.theme import fa
from views.uihelpers import explanation_card, no_data_alert

logger = logging.getLogger("streamcurves")

# R REGIONAL_RESPONSES / REGIONAL_PREDICTORS (display label -> column). The
# selectize ``choices`` dict is keyed by value, so these are inverted.
REGIONAL_RESPONSES = {
    "BW_ft": "Bankfull Width (ft)",
    "BD_ft": "Bankfull Depth (ft)",
    "BA_ft2": "Bankfull Area (ft²)",
}
REGIONAL_PREDICTORS = {
    "DA_km2": "Drainage Area (km²)",
    "DA_mi2": "Drainage Area (mi²)",
}
_DEFAULT_EXPLORATION_STRATS = ["Ecoregion", "DACAT", "StreamType2"]


# --------------------------------------------------------------------------- #
# Plot renderers (importable / testable) — plotnine ports of the R ggplots.
# --------------------------------------------------------------------------- #


def render_regional_curve_plot(plot_spec: dict | None):
    """Render a fit plot spec (from ``fit_regional_curve``) to a plotnine plot.

    Handles both the unstratified (points + 95% CI ribbon + equation label) and
    stratified (colored points + per-group ``lm`` smooth) specs. Returns ``None``
    when the spec is missing or empty.
    """
    if not plot_spec:
        return None

    if plot_spec.get("type") == "regional_curve_stratified":
        points = plot_spec.get("points")
        if points is None or len(points) == 0:
            return None
        group_var = plot_spec["group_var"]
        points = points.copy()
        points[group_var] = points[group_var].astype(str)
        return (
            ggplot(
                points,
                aes(
                    x=plot_spec["predictor_var"],
                    y=plot_spec["response_var"],
                    color=group_var,
                ),
            )
            + geom_point(size=2.5, alpha=0.7)
            + geom_smooth(method="lm", se=True, alpha=0.2)
            + scale_x_log10()
            + scale_y_log10()
            + labs(
                title=plot_spec.get("title"),
                subtitle=plot_spec.get("subtitle"),
                x=plot_spec.get("x_label"),
                y=plot_spec.get("y_label"),
                color=group_var,
            )
            + minimal_plot_theme(legend_position="bottom")
        )

    points = plot_spec.get("points")
    ribbon = plot_spec.get("ribbon")
    if points is None or len(points) == 0:
        return None

    response = plot_spec["response_var"]
    predictor = plot_spec["predictor_var"]
    p = ggplot(points, aes(x=predictor, y=response))
    if ribbon is not None and len(ribbon) > 0:
        p = (
            p
            + geom_ribbon(
                ribbon,
                aes(x="x", ymin="ymin", ymax="ymax"),
                fill="steelblue",
                alpha=0.2,
                inherit_aes=False,
            )
            + geom_line(
                ribbon, aes(x="x", y="y"), color="steelblue", inherit_aes=False
            )
        )
    p = p + geom_point(size=2.5, alpha=0.7) + scale_x_log10() + scale_y_log10()

    eq_label = plot_spec.get("eq_label")
    ann_xy = plot_spec.get("annotation_xy")
    if eq_label and ann_xy is not None:
        p = p + annotate(
            "text",
            x=ann_xy[0],
            y=ann_xy[1],
            label=eq_label,
            ha="left",
            va="top",
            size=geom_text_size("default"),
        )

    return p + labs(
        title=plot_spec.get("title"),
        subtitle=plot_spec.get("subtitle"),
        x=plot_spec.get("x_label"),
        y=plot_spec.get("y_label"),
    )


def render_regional_boxplot(box_spec: dict | None):
    """Render a boxplot spec (from ``build_regional_boxplot_spec``) to plotnine."""
    if not box_spec:
        return None
    df = box_spec.get("data")
    if df is None or len(df) == 0:
        return None
    p = (
        ggplot(df, aes(x="group_label", y=box_spec["y_col"], fill="group_label"))
        + geom_boxplot(alpha=0.7, outlier_alpha=0)
        + geom_jitter(width=0.15, alpha=0.45, size=1.6, show_legend=False)
        + scale_fill_cmap_d(cmap_name="viridis")
        + guides(fill="none")
    )

    # Pairwise Wilcoxon brackets (ggpubr stat_compare_means comparisons):
    # a horizontal bar with two down-ticks per pair, p.format label above the
    # midpoint. Numeric x maps onto the discrete axis positions (1-based).
    brackets = box_spec.get("brackets") or []
    if brackets:
        seg_rows: list[dict] = []
        lab_rows: list[dict] = []
        for b in brackets:
            seg_rows.append({"x": b["x1"], "xend": b["x2"], "y": b["y"], "yend": b["y"]})
            seg_rows.append(
                {"x": b["x1"], "xend": b["x1"], "y": b["y"] - b["tip"], "yend": b["y"]}
            )
            seg_rows.append(
                {"x": b["x2"], "xend": b["x2"], "y": b["y"] - b["tip"], "yend": b["y"]}
            )
            lab_rows.append(
                {
                    "x": (b["x1"] + b["x2"]) / 2.0,
                    "y": b["label_y"],
                    "label": b["p_label"],
                }
            )
        p = (
            p
            + geom_segment(
                pd.DataFrame(seg_rows),
                aes(x="x", xend="xend", y="y", yend="yend"),
                inherit_aes=False,
                size=0.4,
                color="black",
            )
            + geom_text(
                pd.DataFrame(lab_rows),
                aes(x="x", y="y", label="label"),
                inherit_aes=False,
                size=geom_text_size("default"),
                va="bottom",
            )
        )

    return (
        p
        + labs(
            title=box_spec.get("title"),
            subtitle=box_spec.get("kw_label"),
            x=box_spec.get("strat_label"),
            y=box_spec.get("response_label"),
        )
        + minimal_plot_theme(legend_position="none")
    )


# --------------------------------------------------------------------------- #
# Table + choice helpers.
# --------------------------------------------------------------------------- #


def _num_str(v, digits: int) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{round(f, digits):g}"


def build_model_summary_display(model_summary: pd.DataFrame) -> pd.DataFrame:
    """R model_summary -> the 7-column dom='t' table (equation + rounded fits)."""
    rows = []
    for _, r in model_summary.iterrows():
        a = r.get("coefficient_a")
        b = r.get("exponent_b")
        if a is not None and pd.notna(a):
            equation = (
                f"{r['response']} = {float(a):.3f} × "
                f"{r['predictor']}^{float(b):.3f}"
            )
        else:
            equation = "N/A"
        n_obs = r.get("n_obs")
        rows.append(
            {
                "group_level": str(r.get("group_level")),
                "equation": equation,
                "n_obs": int(n_obs) if pd.notna(n_obs) else "NA",
                "r_squared": _num_str(r.get("r_squared"), 4),
                "adj_r2": _num_str(r.get("adj_r2"), 4),
                "p_value": _num_str(r.get("p_value"), 6),
                "fit_status": str(r.get("fit_status")),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "group_level",
            "equation",
            "n_obs",
            "r_squared",
            "adj_r2",
            "p_value",
            "fit_status",
        ],
    )


def _plain_table(df: pd.DataFrame):
    """dom='t' read-only table (DT::datatable(dom='t') equivalent)."""
    if df is None or len(df) == 0:
        return ui.div("No values available.", class_="text-muted")
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(str(c)) for c in df.columns])),
        ui.tags.tbody(
            *[
                ui.tags.tr(*[ui.tags.td(str(r[c])) for c in df.columns])
                for _, r in df.iterrows()
            ]
        ),
        class_="table table-sm table-striped compact",
    )


def _strat_choices(strat_config: dict, data_columns, *, include_none: bool):
    """R exploration/stratify picker choices: single-type strats whose column is
    present, split into Base / Custom Groupings option groups."""
    base: dict[str, str] = {}
    custom: dict[str, str] = {}
    for sk, sc in (strat_config or {}).items():
        sc = sc or {}
        if sc.get("type") != "single":
            continue
        col = sc.get("column_name")
        if not col or col not in data_columns:
            continue
        label = sc.get("display_name") or sk
        (custom if sc.get("is_custom_grouping") else base)[sk] = label

    if include_none:
        base = {"none": "None", **base}

    if custom:
        return {"Base": base, "Custom Groupings": custom}
    return base


def _base_strat_keys(choices) -> list[str]:
    if isinstance(choices, dict) and "Base" in choices:
        base = choices["Base"]
    else:
        base = choices
    return [k for k in (base.keys() if isinstance(base, dict) else []) if k != "none"]


# --------------------------------------------------------------------------- #
# Module.
# --------------------------------------------------------------------------- #


@module.ui
def regional_curve_ui():
    return ui.output_ui("regional_page")


@module.server
def regional_curve_server(input, output, session, state: AppState):
    ns = session.ns
    regional_result = reactive.value(None)
    _bp_registered: set[str] = set()

    # ── Data gate + page shell (R output$regional_page) ───────────────────────
    @output(suspend_when_hidden=False)
    @render.ui
    def regional_page():
        if state.data() is None:
            return no_data_alert()
        return ui.TagList(
            explanation_card(
                "Regional / Hydraulic Geometry Curves",
                ui.tags.p(
                    "Regional curves describe the power-function relationship "
                    "between bankfull channel dimensions and drainage area: "
                    "Y = a × X^b. These are fit via log-log linear regression "
                    "and are a separate workstream from the standard scoring curves."
                ),
                ui.tags.p(
                    "Select a response variable, predictor, and optional "
                    "stratification, then fit the curve."
                ),
            ),
            ui.card(
                ui.card_header("Exploration"),
                ui.card_body(
                    ui.layout_column_wrap(
                        ui.input_selectize(
                            ns("response"),
                            "Response Variable:",
                            choices=REGIONAL_RESPONSES,
                        ),
                        ui.output_ui(ns("exploration_strat_picker_ui")),
                        width=1 / 2,
                    ),
                    ui.output_ui(ns("exploration_boxplots_ui")),
                ),
            ),
            ui.card(
                ui.card_header("Curve Settings"),
                ui.card_body(
                    ui.layout_column_wrap(
                        ui.input_selectize(
                            ns("predictor"),
                            "Predictor Variable:",
                            choices=REGIONAL_PREDICTORS,
                        ),
                        ui.output_ui(ns("stratify_ui")),
                        width=1 / 2,
                    ),
                    ui.div(
                        ui.input_action_button(
                            ns("fit_curve"),
                            "Fit Regional Curve",
                            class_="btn-primary mt-2",
                            icon=fa("chart-line"),
                        )
                    ),
                ),
            ),
            ui.output_ui(ns("results_ui")),
        )

    # ── Dynamic strat pickers (R exploration_strat_picker_ui / stratify_ui) ───
    # suspend_when_hidden=False on every nested output: they live inside the
    # `regional_page` @render.ui, which is inserted while the tab may be hidden
    # (data is loaded from the Data & Setup tab). Without this they stay stuck
    # suspended (.recalculating, never computed) — R sets outputOptions(
    # suspendWhenHidden = FALSE) for the same reason.
    @output(suspend_when_hidden=False)
    @render.ui
    def exploration_strat_picker_ui():
        req(state.data() is not None)
        choices = _strat_choices(
            state.strat_config(), state.data().columns, include_none=False
        )
        base_keys = _base_strat_keys(choices)
        defaults = [k for k in _DEFAULT_EXPLORATION_STRATS if k in base_keys]
        return ui.input_selectize(
            ns("exploration_strats"),
            "Stratifications:",
            choices=choices,
            selected=defaults,
            multiple=True,
            options={"dropdownParent": "body", "plugins": ["remove_button"]},
        )

    @output(suspend_when_hidden=False)
    @render.ui
    def stratify_ui():
        req(state.data() is not None)
        choices = _strat_choices(
            state.strat_config(), state.data().columns, include_none=True
        )
        return ui.input_selectize(ns("stratify"), "Stratify by:", choices=choices)

    # ── Exploration boxplots (R exploration_boxplots reactive) ────────────────
    @reactive.calc
    def exploration_boxplots() -> dict:
        req(state.data() is not None)
        response = input.response()
        strat_keys = input.exploration_strats()
        req(response, strat_keys)
        data = state.data()
        strat_config = state.strat_config() or {}
        response_label = REGIONAL_RESPONSES.get(response, response)

        specs: dict[str, dict] = {}
        for sk in strat_keys:
            sc = strat_config.get(sk)
            if not sc or not sc.get("column_name"):
                continue
            if sc["column_name"] not in data.columns:
                continue
            spec = build_regional_boxplot_spec(
                data,
                response_col=response,
                response_label=response_label,
                strat_col=sc["column_name"],
                strat_label=sc.get("display_name") or sk,
                pairwise_comparisons=sc.get("pairwise_comparisons"),
            )
            if spec is not None:
                specs[sk] = spec
        return specs

    @output(suspend_when_hidden=False)
    @render.ui
    def exploration_boxplots_ui():
        specs = exploration_boxplots()
        req(len(specs) > 0)
        strat_config = state.strat_config() or {}
        tabs = []
        for sk in specs:
            sc = strat_config.get(sk) or {}
            label = sc.get("display_name") or sk
            tabs.append(
                ui.nav_panel(label, ui.output_plot(ns(f"rc_bp_{sk}"), height="450px"))
            )
        return ui.navset_card_tab(*tabs, title="Stratification Boxplots")

    def _register_bp_output(strat_key: str):
        if strat_key in _bp_registered:
            return
        _bp_registered.add(strat_key)
        sk = strat_key

        @output(id=f"rc_bp_{sk}", suspend_when_hidden=False)
        @render.plot
        def _bp():
            return render_regional_boxplot(exploration_boxplots().get(sk))

    @reactive.effect
    def _register_bp_renderers():
        for sk in exploration_boxplots():
            _register_bp_output(sk)

    # ── Fit curve (R observeEvent input$fit_curve) ────────────────────────────
    @reactive.effect
    @reactive.event(input.fit_curve)
    def _fit_curve():
        data = state.data()
        if data is None:
            return
        stratify = input.stratify()
        group_var = None if (not stratify or stratify == "none") else stratify
        try:
            result = fit_regional_curve(
                data,
                response_var=input.response(),
                predictor_var=input.predictor(),
                group_var=group_var,
            )
        except Exception as e:  # noqa: BLE001
            ui.notification_show(
                f"Error fitting regional curve: {e}", type="error", duration=8
            )
            regional_result.set(None)
            return
        regional_result.set(result)

    # ── Results (R results_ui / regional_plot / model_summary_table) ──────────
    @output(suspend_when_hidden=False)
    @render.ui
    def results_ui():
        res = regional_result()
        req(res)
        parts = []
        if res.get("plot_spec") is not None:
            parts.append(
                ui.card(
                    ui.card_header("Log-Log Scatter Plot"),
                    ui.card_body(
                        ui.output_plot(ns("regional_plot"), height="500px")
                    ),
                )
            )
        parts.append(
            ui.card(
                ui.card_header("Model Summary"),
                ui.card_body(ui.output_ui(ns("model_summary_table"))),
            )
        )
        parts.append(
            ui.div(
                ui.input_action_button(
                    ns("mark_complete"),
                    "Mark Complete ✓",
                    class_="btn-success",
                    icon=fa("check"),
                ),
                class_="d-flex justify-content-end mt-3",
            )
        )
        return ui.TagList(*parts)

    @output(suspend_when_hidden=False)
    @render.plot
    def regional_plot():
        res = regional_result()
        req(res)
        spec = res.get("plot_spec")
        req(spec is not None)
        return render_regional_curve_plot(spec)

    @output(suspend_when_hidden=False)
    @render.ui
    def model_summary_table():
        res = regional_result()
        req(res)
        return _plain_table(build_model_summary_display(res["model_summary"]))

    # ── Mark complete (R observeEvent input$mark_complete) ────────────────────
    @reactive.effect
    @reactive.event(input.mark_complete)
    def _mark_complete():
        res = regional_result()
        if not res:
            return
        response = input.response()
        key = f"regional_{response}"
        entry = {
            "type": "regional",
            "response": response,
            "predictor": input.predictor(),
            "stratify": input.stratify() or "none",
            "model_summary": res["model_summary"],
            "plot_spec": res.get("plot_spec"),
        }
        cache = dict(state.completed_metrics() or {})
        cache[key] = entry
        state.completed_metrics.set(cache)
        ui.notification_show(
            f"Regional curve for {response} marked complete!",
            type="message",
            duration=3,
        )

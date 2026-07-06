"""Metric precheck panel — port of app/modules/mod_precheck.R.

Summary statistics, distribution histogram (plotnine), and quality flags for
the currently selected metric. The R DT (dom="t") table becomes a plain HTML
table.
"""

from __future__ import annotations

import math

import pandas as pd
from plotnine import aes, geom_histogram, ggplot, labs
from shiny import module, reactive, render, req, ui

from streamcurves.plot_theme import minimal_plot_theme
from views.state import AppState
from views.uihelpers import status_badge


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "NA"
    if isinstance(v, float):
        return f"{round(v, 3):g}"
    return str(v)


@module.ui
def precheck_ui():
    return ui.TagList(
        ui.card(
            ui.card_header("Step 0: Metric Precheck", class_="bg-info text-white"),
            ui.card_body(
                ui.tags.p(
                    "This step shows summary statistics for the selected metric: sample "
                    "size, distribution shape, missing data, and quality flags. Review "
                    "these before proceeding to stratification screening."
                ),
                ui.tags.p(
                    "Quality flags identify potential issues: low sample size "
                    "(n < min_sample_size), near-zero variance, or impossible values "
                    "(e.g., proportions outside 0-100%)."
                ),
            ),
            class_="border-info mb-2",
            fill=False,
        ),
        ui.layout_column_wrap(
            ui.card(
                ui.card_header("Summary Statistics"),
                ui.card_body(ui.output_ui("stats_table")),
            ),
            ui.card(
                ui.card_header("Distribution"),
                ui.card_body(ui.output_plot("histogram", height="300px")),
            ),
            width=1 / 2,
        ),
        ui.card(
            ui.card_header("Quality Flags"),
            ui.card_body(ui.output_ui("quality_flags")),
        ),
        ui.div(
            ui.input_action_button(
                "proceed", "Proceed to Stratification ▸", class_="btn btn-primary btn-proceed"
            ),
            class_="d-flex justify-content-end mt-3",
        ),
    )


@module.server
def precheck_server(input, output, session, state: AppState):
    @reactive.calc
    def precheck_row() -> pd.DataFrame:
        metric = state.current_metric()
        req(metric)
        df = state.precheck_df()
        req(df is not None)
        return df[df["metric"] == metric]

    @render.ui
    def stats_table():
        row = precheck_row()
        req(len(row) == 1)
        r = row.iloc[0]
        stats = [
            ("n", _fmt(r["n_obs"])),
            ("Missing", _fmt(r["n_missing"])),
            ("Min", _fmt(r["min"])),
            ("Q25", _fmt(r["q25"])),
            ("Median", _fmt(r["median"])),
            ("Mean", _fmt(r["mean"])),
            ("Q75", _fmt(r["q75"])),
            ("Max", _fmt(r["max"])),
            ("SD", _fmt(r["sd"])),
            ("IQR", _fmt(r["iqr"])),
        ]
        return ui.tags.table(
            ui.tags.thead(
                ui.tags.tr(ui.tags.th("Statistic"), ui.tags.th("Value"))
            ),
            ui.tags.tbody(
                *[
                    ui.tags.tr(ui.tags.td(label), ui.tags.td(value))
                    for label, value in stats
                ]
            ),
            class_="table table-sm compact w-auto",
        )

    @render.plot
    def histogram():
        metric = state.current_metric()
        req(metric)
        mc = (state.metric_config() or {}).get(metric) or {}
        col = mc.get("column_name")
        data = state.data()
        req(data is not None and col in data.columns)

        vals = pd.to_numeric(data[col], errors="coerce").dropna()
        req(len(vals) > 0)

        df = pd.DataFrame({"value": vals})
        display = mc.get("display_name") or metric
        units = mc.get("units") or ""
        return (
            ggplot(df, aes(x="value"))
            + geom_histogram(bins=12, fill="steelblue", alpha=0.7, color="white")
            + labs(
                title=f"{display} Distribution",
                x=f"{display} ({units})",
                y="Count",
            )
            + minimal_plot_theme()
        )

    @render.ui
    def quality_flags():
        row = precheck_row()
        req(len(row) == 1)
        r = row.iloc[0]

        flags = []
        if bool(r.get("flag_low_n")):
            flags.append(status_badge("caution", "Low sample size"))
        if bool(r.get("flag_low_variance")):
            flags.append(status_badge("caution", "Low variance"))
        if bool(r.get("flag_impossible_values")):
            flags.append(status_badge("fail", "Impossible values"))
        if not flags:
            flags = [status_badge("pass", "All checks passed")]

        return ui.div(
            ui.tags.strong("Overall: "),
            status_badge(str(r["precheck_status"])),
            ui.tags.span(class_="ms-3"),
            *flags,
            class_="d-flex align-items-center gap-2",
        )

    return reactive.calc(lambda: input.proceed())

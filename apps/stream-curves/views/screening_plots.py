"""Screening boxplot figures — plotnine port of R/05's
build_screening_plot_from_spec, rendering the plot_spec dicts produced by
streamcurves.screening.

Parity notes: ggpubr's pairwise significance brackets (stat_compare_means)
are not reproduced (plotnine has no bracket geom); the p-values live in the
screening/pairwise tables alongside the figure.
"""

from __future__ import annotations

from plotnine import (
    aes,
    facet_wrap,
    geom_boxplot,
    geom_jitter,
    ggplot,
    guides,
    labs,
    scale_fill_ordinal,
)

from streamcurves.plot_theme import minimal_plot_theme


def build_screening_plot_from_spec(
    plot_spec: dict,
    metric_config: dict | None = None,
    strat_config: dict | None = None,
    font_profile: str = "default",
    show_points: bool = True,
    masked_site_ids=None,
):
    if not plot_spec:
        return None
    data = plot_spec.get("data")
    if data is None or len(data) == 0:
        return None
    y_col = plot_spec["y_col"]

    if plot_spec.get("type") == "paired":
        primary_col = plot_spec["primary_col"]
        secondary_col = plot_spec["secondary_col"]
        p = (
            ggplot(data, aes(x=secondary_col, y=y_col, fill=secondary_col))
            + geom_boxplot(alpha=0.7, outlier_alpha=0 if show_points else 1)
            + facet_wrap(f"~{primary_col}")
        )
    else:
        x_col = plot_spec["x_col"]
        fill_col = plot_spec.get("fill_col") or x_col
        p = ggplot(data, aes(x=x_col, y=y_col, fill=fill_col)) + geom_boxplot(
            alpha=0.7, outlier_alpha=0 if show_points else 1
        )

    if show_points:
        p = p + geom_jitter(width=0.15, alpha=0.45, size=1.6, show_legend=False)

    return (
        p
        + scale_fill_ordinal()
        + guides(fill="none")
        + labs(
            title=plot_spec.get("title"),
            x=plot_spec.get("x_label"),
            y=plot_spec.get("y_label"),
        )
        + minimal_plot_theme(profile=font_profile, legend_position="none")
    )

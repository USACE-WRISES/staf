"""Reference-curve figures — plotnine ports of the R/10 plot builders
(build_reference_curve_plot, build_overlay_curve_plot, build_overlay_bar_chart)
that the M1 domain port deferred to the views layer.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from plotnine import (
    aes,
    after_stat,
    annotate,
    coord_cartesian,
    element_text,
    geom_density,
    geom_histogram,
    geom_hline,
    geom_line,
    geom_point,
    geom_rug,
    geom_vline,
    ggplot,
    labs,
    scale_y_continuous,
    theme,
)

from streamcurves.curves import (
    normalize_reference_curve_points,
    reference_curve_points_from_row,
    reference_curve_x_range,
)
from streamcurves.plot_theme import geom_text_size, minimal_plot_theme


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _band_annotations(x_range, alpha: float):
    return [
        annotate("rect", xmin=x_range[0], xmax=x_range[1], ymin=0.70, ymax=1.00,
                 fill="#2ca25f", alpha=alpha),
        annotate("rect", xmin=x_range[0], xmax=x_range[1], ymin=0.30, ymax=0.70,
                 fill="#f0ad4e", alpha=alpha),
        annotate("rect", xmin=x_range[0], xmax=x_range[1], ymin=0.00, ymax=0.30,
                 fill="#d9534f", alpha=alpha),
        geom_hline(yintercept=0.70, linetype="dashed", color="grey", size=0.5),
        geom_hline(yintercept=0.30, linetype="dashed", color="grey", size=0.5),
    ]


def reference_values_from_data(data, metric_config, metric_key) -> np.ndarray:
    """Finite reference values for a metric column (R/10 ref_values extract)."""
    mc = (metric_config or {}).get(metric_key) or {}
    col_name = mc.get("column_name")
    if data is None or col_name is None or col_name not in getattr(data, "columns", []):
        return np.array([], dtype=float)
    vals = pd.to_numeric(data[col_name], errors="coerce").to_numpy(dtype=float)
    return vals[np.isfinite(vals)]


def build_reference_distribution_plot(
    ref_values, curve_row, metric_config, metric_key, stratum_label=None
):
    """Port of R/10:743-863 — reference-distribution histogram with
    direction-aware zone shading and 0.30/0.70 crossing markers.

    R shades with +/-Inf rect bounds and places labels at y=Inf; plotnine needs
    finite bounds, so the rects clamp to the x-range and the y headroom comes
    from the histogram's density maximum.
    """
    ref_values = np.asarray(ref_values, dtype=float)
    ref_values = ref_values[np.isfinite(ref_values)]
    if len(ref_values) < 2 or curve_row is None or len(curve_row) == 0:
        return None

    row = pd.DataFrame(curve_row).iloc[0]
    mc = (metric_config or {}).get(metric_key) or {}
    higher_is_better = bool(row.get("higher_is_better"))
    points = reference_curve_points_from_row(pd.DataFrame(curve_row), higher_is_better)
    x_range = reference_curve_x_range(ref_values, points)
    x30 = float(row["score_30_metric"]) if _finite(row.get("score_30_metric")) else None
    x70 = float(row["score_70_metric"]) if _finite(row.get("score_70_metric")) else None
    ann_size = geom_text_size("large_analysis")

    density, _ = np.histogram(ref_values, bins=12, density=True)
    y_max = float(density.max()) if len(density) and math.isfinite(density.max()) else 0.0
    if y_max <= 0:
        y_max = 1.0
    y_top = y_max * 1.25

    def _zone_rect(xmin, xmax, fill):
        xmin = max(x_range[0], xmin)
        xmax = min(x_range[1], xmax)
        if not (xmin < xmax):
            return None
        return annotate("rect", xmin=xmin, xmax=xmax, ymin=0, ymax=y_top,
                        fill=fill, alpha=0.12)

    rects = []
    if x70 is not None:
        rects.append(
            _zone_rect(x70, x_range[1], "#2ca25f") if higher_is_better
            else _zone_rect(x_range[0], x70, "#2ca25f")
        )
    if x30 is not None and x70 is not None:
        rects.append(_zone_rect(min(x30, x70), max(x30, x70), "#f0ad4e"))
    if x30 is not None:
        rects.append(
            _zone_rect(x_range[0], x30, "#d9534f") if higher_is_better
            else _zone_rect(x30, x_range[1], "#d9534f")
        )
    rects = [r for r in rects if r is not None]

    suffix = f" ({stratum_label})" if stratum_label else ""
    p = (
        ggplot(pd.DataFrame({"value": ref_values}), aes(x="value"))
        + rects
        + geom_histogram(aes(y=after_stat("density")), bins=12, fill="steelblue",
                         alpha=0.45, color="white")
        + geom_rug(sides="b", color="steelblue", alpha=0.65)
        + coord_cartesian(xlim=x_range, ylim=(0, y_top), expand=False)
        + labs(
            title=f"{mc.get('display_name') or metric_key}: Reference Distribution{suffix}",
            subtitle=(
                f"n = {row.get('n_reference')}"
                f" | Q25 = {round(float(row['q25']), 2) if _finite(row.get('q25')) else 'NA'}"
                f" | Q75 = {round(float(row['q75']), 2) if _finite(row.get('q75')) else 'NA'}"
            ),
            x=f"{mc.get('display_name') or metric_key} ({mc.get('units') or ''})",
            y="Density",
        )
        + minimal_plot_theme(profile="large_analysis")
    )

    if x30 is not None:
        p = (
            p
            + geom_vline(xintercept=x30, linetype="dashed", color="#b22222", size=0.8)
            + annotate("text", x=x30, y=y_top * 0.95, label=f"Score 0.30 = {round(x30, 2)}",
                       ha="left", size=ann_size, color="#b22222")
        )
    if x70 is not None:
        p = (
            p
            + geom_vline(xintercept=x70, linetype="dashed", color="#1b7837", size=0.8)
            + annotate("text", x=x70, y=y_top * 0.86, label=f"Score 0.70 = {round(x70, 2)}",
                       ha="left", size=ann_size, color="#1b7837")
        )

    return p


def build_reference_curve_plot(
    curve_points, curve_row, metric_config, metric_key, stratum_label=None
):
    """Port of R/10:865-940 — the single reference-curve figure with band
    shading, 0.30/0.70 crossing markers, and zone labels."""
    points = normalize_reference_curve_points(curve_points)
    if points is None or len(points) < 2 or curve_row is None or len(curve_row) == 0:
        return None

    row = pd.DataFrame(curve_row).iloc[0]
    mc = (metric_config or {}).get(metric_key) or {}
    x_range = reference_curve_x_range([], points)
    ann_size = geom_text_size("large_analysis")

    suffix = f" ({stratum_label})" if stratum_label else ""
    source_label = "Manual point set" if str(row.get("curve_source")) == "manual" else \
        "Auto-generated point set"

    p = (
        ggplot(points, aes(x="metric_value", y="index_score"))
        + _band_annotations(x_range, 0.10)
        + geom_line(color="steelblue", size=1.2)
        + geom_point(color="steelblue", size=2.8)
        + scale_y_continuous(breaks=[round(v * 0.1, 1) for v in range(11)])
        + coord_cartesian(xlim=x_range, ylim=(0, 1.02), expand=False)
        + labs(
            title=f"{mc.get('display_name') or metric_key}: Reference Curve{suffix}",
            subtitle=f"{source_label} | n = {row.get('n_reference')}",
            x=f"{mc.get('display_name') or metric_key} ({mc.get('units') or ''})",
            y="Index Score",
        )
        + minimal_plot_theme(profile="large_analysis")
    )

    if _finite(row.get("score_30_metric")):
        x30 = float(row["score_30_metric"])
        p = (
            p
            + geom_vline(xintercept=x30, linetype="dashed", color="#b22222", size=0.7)
            + annotate("label", x=x30, y=0.03, label=f"0.30 = {round(x30, 2)}",
                       size=ann_size, color="#b22222", fill="white", alpha=0.75)
        )
    if _finite(row.get("score_70_metric")):
        x70 = float(row["score_70_metric"])
        p = (
            p
            + geom_vline(xintercept=x70, linetype="dashed", color="#1b7837", size=0.7)
            + annotate("label", x=x70, y=0.03, label=f"0.70 = {round(x70, 2)}",
                       size=ann_size, color="#1b7837", fill="white", alpha=0.75)
        )

    return (
        p
        + annotate("text", x=x_range[1], y=0.85, label="Functioning", ha="right",
                   size=ann_size, color="#1b7837", fontstyle="italic")
        + annotate("text", x=x_range[1], y=0.50, label="At-Risk", ha="right",
                   size=ann_size, color="#8a6d3b", fontstyle="italic")
        + annotate("text", x=x_range[1], y=0.15, label="Not Functioning", ha="right",
                   size=ann_size, color="#b22222", fontstyle="italic")
    )


def build_overlay_curve_plot(curve_rows, metric_config):
    """Port of R/10:1293-1366 — cross-stratum scoring curves overlay."""
    if curve_rows is None or len(curve_rows) < 2:
        return None
    curve_rows = pd.DataFrame(curve_rows)
    metric_key = curve_rows["metric"].iloc[0]
    mc = (metric_config or {}).get(metric_key) or {}

    frames = []
    for i in range(len(curve_rows)):
        row = curve_rows.iloc[[i]]
        if str(row["curve_status"].iloc[0]) != "complete":
            continue
        points = reference_curve_points_from_row(row, bool(mc.get("higher_is_better")))
        if points is None or len(points) < 2:
            continue
        frames.append(
            pd.DataFrame(
                {
                    "x": points["metric_value"].to_numpy(dtype=float),
                    "y": points["index_score"].to_numpy(dtype=float),
                    "stratum": str(row["stratum"].iloc[0]),
                }
            )
        )
    if not frames:
        return None
    all_curves = pd.concat(frames, ignore_index=True)

    x_range = reference_curve_x_range(
        [],
        pd.DataFrame(
            {
                "point_order": range(1, len(all_curves) + 1),
                "metric_value": all_curves["x"],
                "index_score": all_curves["y"],
            }
        ),
    )

    return (
        ggplot()
        + _band_annotations(x_range, 0.08)
        + geom_line(all_curves, aes(x="x", y="y", color="stratum"), size=1.2)
        + geom_point(all_curves, aes(x="x", y="y", color="stratum"), size=2.8)
        + scale_y_continuous(breaks=[round(v * 0.1, 1) for v in range(11)])
        + coord_cartesian(xlim=x_range, ylim=(0, 1.02), expand=False)
        + labs(
            title=f"{mc.get('display_name') or metric_key}: Cross-Stratum Scoring Curves",
            subtitle="Strata: " + ", ".join(dict.fromkeys(all_curves["stratum"])),
            x=f"{mc.get('display_name') or metric_key} ({mc.get('units') or ''})",
            y="Index Score",
            color="Stratum",
        )
        + minimal_plot_theme(profile="large_analysis", legend_position="bottom")
    )


def build_overlay_bar_chart(data, metric_key, metric_config, strat_var, levels):
    """Port of R/10:1368-1394 — cross-stratum density comparison."""
    mc = (metric_config or {}).get(metric_key) or {}
    col_name = mc.get("column_name")
    if data is None or col_name not in data.columns or strat_var not in data.columns:
        return None
    plot_data = data[data[strat_var].isin(levels)][[col_name, strat_var]].rename(
        columns={col_name: "value", strat_var: "stratum"}
    )
    plot_data = plot_data[plot_data["value"].notna()].copy()
    plot_data["stratum"] = plot_data["stratum"].astype(str)
    if len(plot_data) < 4:
        return None
    return (
        ggplot(plot_data, aes(x="value", fill="stratum", color="stratum"))
        + geom_density(alpha=0.30)
        + geom_rug(alpha=0.55, show_legend=False)
        + labs(
            title=f"{mc.get('display_name') or metric_key}: Cross-Stratum Distributions",
            subtitle="Strata: " + ", ".join(str(v) for v in levels),
            x=f"{mc.get('display_name') or metric_key} ({mc.get('units') or ''})",
            y="Density",
            fill="Stratum",
            color="Stratum",
        )
        + minimal_plot_theme(profile="large_analysis", legend_position="bottom")
    )

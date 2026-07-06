"""Port of R/00_plot_theme.R — shared plot typography for plotnine figures and
plotly layout dicts.

ggplot2's ``.pt`` (points-per-mm scaling used by geom_text sizes) is 72.27/25.4;
plotnine uses the same convention.
"""

from __future__ import annotations

from plotnine import element_blank, element_text, theme, theme_minimal

GGPLOT_PT = 72.27 / 25.4  # ggplot2::.pt

PLOT_FONT_PROFILES: dict[str, dict[str, int]] = {
    "default": {
        "base": 12,
        "title": 14,
        "subtitle": 12,
        "axis_text": 12,
        "axis_title": 12,
        "legend_text": 12,
        "legend_title": 12,
        "strip_text": 12,
        "annotation": 12,
    },
    "large_analysis": {
        "base": 14,
        "title": 14,
        "subtitle": 14,
        "axis_text": 14,
        "axis_title": 14,
        "legend_text": 14,
        "legend_title": 14,
        "strip_text": 14,
        "annotation": 14,
    },
}


def get_plot_font_profile(profile: str | None = "default") -> dict[str, int]:
    resolved = profile or "default"
    sizes = PLOT_FONT_PROFILES.get(resolved)
    if sizes is None:
        raise ValueError(f"Unknown plot font profile: {resolved}")
    return sizes


def geom_text_size(profile: str = "default", field: str = "annotation") -> float:
    sizes = get_plot_font_profile(profile)
    pt_value = sizes.get(field) or sizes.get("annotation") or sizes["base"]
    return pt_value / GGPLOT_PT


def plot_text_theme(
    profile: str = "default",
    legend_position=None,
    axis_text_x_angle=None,
    axis_text_x_hjust=None,
    axis_text_x_vjust=None,
    axis_text_x_blank: bool = False,
    axis_text_y_blank: bool = False,
    axis_ticks_x_blank: bool = False,
    axis_ticks_y_blank: bool = False,
    panel_grid_blank: bool = False,
):
    sizes = get_plot_font_profile(profile)
    kwargs = {
        "plot_title": element_text(size=sizes["title"]),
        "plot_subtitle": element_text(size=sizes["subtitle"]),
        "axis_title_x": element_text(size=sizes["axis_title"]),
        "axis_title_y": element_text(size=sizes["axis_title"]),
        "axis_text_x": element_text(size=sizes["axis_text"]),
        "axis_text_y": element_text(size=sizes["axis_text"]),
        "legend_text": element_text(size=sizes["legend_text"]),
        "legend_title": element_text(size=sizes["legend_title"]),
        "strip_text": element_text(size=sizes["strip_text"]),
    }
    if legend_position is not None:
        kwargs["legend_position"] = legend_position
    if (
        axis_text_x_angle is not None
        or axis_text_x_hjust is not None
        or axis_text_x_vjust is not None
    ):
        text_kwargs: dict = {"size": sizes["axis_text"]}
        if axis_text_x_angle is not None:
            text_kwargs["angle"] = axis_text_x_angle
        if axis_text_x_hjust is not None:
            text_kwargs["ha"] = axis_text_x_hjust
        if axis_text_x_vjust is not None:
            text_kwargs["va"] = axis_text_x_vjust
        kwargs["axis_text_x"] = element_text(**text_kwargs)
    if axis_text_x_blank:
        kwargs["axis_text_x"] = element_blank()
    if axis_text_y_blank:
        kwargs["axis_text_y"] = element_blank()
    if axis_ticks_x_blank:
        kwargs["axis_ticks_major_x"] = element_blank()
    if axis_ticks_y_blank:
        kwargs["axis_ticks_major_y"] = element_blank()
    if panel_grid_blank:
        kwargs["panel_grid"] = element_blank()
    return theme(**kwargs)


def minimal_plot_theme(profile: str = "default", base_size: int | None = None, **kwargs):
    """streamcurves_minimal_plot_theme(): theme_minimal + typography overrides."""
    sizes = get_plot_font_profile(profile)
    resolved_base = base_size if base_size is not None else sizes["base"]
    return theme_minimal(base_size=resolved_base) + plot_text_theme(profile=profile, **kwargs)


# --------------------------------------------------------------------------- #
# plotly layout helpers — the layout schema is identical to R's plotly, so
# these are literal dict builders.
# --------------------------------------------------------------------------- #


def _merge(base: dict, override: dict | None) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def plotly_axis_defaults(
    profile: str = "default",
    title_text: str | None = None,
    visible: bool = True,
    standoff=None,
    zeroline: bool = False,
    **kwargs,
) -> dict:
    sizes = get_plot_font_profile(profile)
    axis = _merge(
        {
            "visible": visible,
            "automargin": True,
            "zeroline": zeroline,
            "tickfont": {"size": sizes["axis_text"]},
        },
        kwargs,
    )
    if visible:
        title_defaults: dict = {"font": {"size": sizes["axis_title"]}}
        if title_text is not None:
            title_defaults["text"] = title_text
        if standoff is not None:
            title_defaults["standoff"] = standoff
        axis["title"] = _merge(title_defaults, axis.get("title") or {})
    return axis


def plotly_legend_defaults(profile: str = "default", **kwargs) -> dict:
    sizes = get_plot_font_profile(profile)
    return _merge(
        {
            "font": {"size": sizes["legend_text"]},
            "title": {"font": {"size": sizes["legend_title"]}},
        },
        kwargs,
    )


def plotly_layout(
    fig,
    profile: str = "default",
    xaxis: dict | None = None,
    yaxis: dict | None = None,
    legend: dict | None = None,
    annotations=None,
    margin=None,
    **kwargs,
):
    """Apply the shared typography layout to a plotly.graph_objects.Figure."""
    sizes = get_plot_font_profile(profile)
    layout_args: dict = {"font": {"size": sizes["base"]}, **kwargs}
    if xaxis is not None:
        layout_args["xaxis"] = xaxis
    if yaxis is not None:
        layout_args["yaxis"] = yaxis
    if legend is not None:
        layout_args["legend"] = legend
    if annotations is not None:
        layout_args["annotations"] = annotations
    if margin is not None:
        layout_args["margin"] = margin
    fig.update_layout(**layout_args)
    return fig

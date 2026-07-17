"""Interactive cross-section plot (Plotly ``go.Figure``) for the report modal.

Mirrors the datum, scaling, and styling of :func:`easi.xsplot.cross_section_png` (the
static matplotlib PNG embedded in the PDF), but returns an interactive figure so the
user can drag a box to zoom, pan, hover for station/height, and zoom in/out with the
modebar magnifiers. The default view is windowed where the terrain climbs far above
every reference line (a valley wall) — the full transect stays in the traces, so the
app's zoom-to-extents modebar button still reveals it, and its zoom-home modebar button
restores this window. (The native reset/autoscale are removed; the app injects those two
replacements into the modebar client-side, rebuilding the window from current geometry.)
The PDF export keeps the matplotlib renderer; this module is UI-only.

A plain ``go.Figure`` is returned (not a ``FigureWidget``) so it stays pure/testable;
shinywidgets coerces it to a ``FigureWidget`` inside the Shiny session at render time.

The Y axis is height above the channel bottom (thalweg = 0); both axes are in ``unit``
("ft" default, or "m"). Stages are absolute elevations on the profile's metres datum
and are converted to heights here.
"""
from __future__ import annotations

from typing import Optional

from . import geomorph

FT_PER_M = 3.28083989501312
# Label-collision handling for the low-bank reference line (mirrored in xsplot):
# treat labels as overlapping when their lines sit within this fraction of the
# y-span, and shift the low-bank label this many px left of the neighbouring text.
LABEL_EPS_FRAC = 0.06
LABEL_CLASH_XSHIFT_PX = -70


def _water_polygon(xs, h, level, *, span=None):
    """Densify ``(xs, h)`` with a point wherever the bed crosses ``level``, and return
    ``(x2, bed2, surf2)`` with the surface raised to ``level`` over the water body.

    ``span`` is the ``(left, right)`` edge-of-water stations of the contiguous water
    body containing the channel (:func:`geomorph.wetted_span`): the surface is raised
    only within it, so a disconnected low spot elsewhere on the transect is *not*
    drawn as water — matching :func:`geomorph.flow_width` / ``flow_area``, which never
    count it. ``span=None`` means no water body (the surface hugs the bed everywhere).

    Filling between ``surf2`` and ``bed2`` (Plotly ``fill="tonexty"``) then yields a
    water top that is *flat* at ``level`` and pinches to zero exactly at the crossings.
    Without the inserted crossings the surface would be clamped only at data points, so
    on a steep, sparsely-sampled bank Plotly draws it straight up to the next high point
    (the fill rides above bankfull). This mirrors matplotlib's
    ``fill_between(..., where=..., interpolate=True)`` used by the PDF renderer.
    """
    if span is None:
        return list(xs), list(h), list(h)
    left, right = float(span[0]), float(span[1])
    x2, bed2 = [xs[0]], [h[0]]
    for i in range(1, len(xs)):
        x0, y0, x1, y1 = xs[i - 1], h[i - 1], xs[i], h[i]
        if (y0 < level < y1) or (y1 < level < y0):      # segment crosses the level
            t = (level - y0) / (y1 - y0)
            x2.append(x0 + t * (x1 - x0))
            bed2.append(level)
        x2.append(x1)
        bed2.append(y1)
    surf2 = [level if (b <= level and left <= xv <= right) else b
             for xv, b in zip(x2, bed2)]
    return x2, bed2, surf2


def figure(stations, elevs, *, thalweg: Optional[float] = None,
           bankfull_stage: Optional[float] = None,
           floodplain_stage: Optional[float] = None,
           unit: str = "ft", source: Optional[str] = None):
    """Build the interactive cross-section as a Plotly ``go.Figure``.

    Always returns a fixed 5-trace structure (terrain baseline, terrain fill, water
    baseline, water fill, bed line) so the report can update the live widget by position
    without ever adding/removing traces; the water fill collapses to the bed (transparent)
    when there is no bankfull stage, and covers only the contiguous water body spanning
    the thalweg (a disconnected low spot is never drawn as water). Plus horizontal
    reference lines for bankfull, flood-prone (2x bankfull depth, Rosgen), and low bank,
    each labelled at the right edge. The axis ranges are set explicitly so "Reset
    axes"/double-click returns to the default view, which is windowed where the terrain
    climbs above ``VIEW_HEADROOM`` x the highest reference line (the full transect stays
    in the traces for pan/zoom). No in-figure title (the section renders one in HTML)
    and no fixed height, so shinywidgets stretches the plot to fill its container. The
    modebar keeps only zoom / pan / reset-axes.
    """
    import plotly.graph_objects as go

    u = FT_PER_M if unit == "ft" else 1.0
    ul = "ft" if unit == "ft" else "m"
    x = [float(s) for s in stations]
    z = [float(e) for e in elevs]
    thal = float(thalweg) if thalweg is not None else min(z)
    ti = z.index(min(z))
    x0 = x[ti]                              # centre the plot on the thalweg
    xs = [(xi - x0) * u for xi in x]
    h = [(zi - thal) * u for zi in z]       # height above the channel bottom

    fw = go.Figure()
    base = min(h) - 0.5
    # Fixed 5-trace structure, always in this order: terrain baseline, terrain fill,
    # water baseline, water fill, bed line (on top). Keeping the count/order constant
    # lets the report update the live FigureWidget by position (restyle only, never
    # add/remove traces), which is what keeps a candidate switch flicker-free.
    # terrain (0: baseline, 1: fill up to the bed)
    fw.add_trace(go.Scatter(x=xs, y=[base] * len(xs), mode="lines", line=dict(width=0),
                            hoverinfo="skip", showlegend=False))
    fw.add_trace(go.Scatter(x=xs, y=h, mode="lines", fill="tonexty", fillcolor="#efe9e1",
                            line=dict(width=0), hoverinfo="skip", showlegend=False))

    bf_h = (float(bankfull_stage) - thal) * u if bankfull_stage is not None else None
    lb_h = (float(floodplain_stage) - thal) * u if floodplain_stage is not None else None

    # water (2: baseline at the bed, 3: fill up to the bankfull surface). Always emitted
    # so the trace count is fixed; with no bankfull it collapses to the bed (zero area)
    # and is drawn transparent.
    if bf_h is not None:
        # Insert bankfull crossings so the water top is flat at bf_h and pinches exactly
        # where the bed rises through it (else it slants up steep, sparse banks). The
        # fill is bounded to the contiguous water body spanning the thalweg — the same
        # rule flow_width/flow_area use — so off-channel low spots stay dry.
        xw, bed_w, surf_w = _water_polygon(
            xs, h, bf_h, span=geomorph.wetted_span(xs, h, bf_h, thalweg_index=ti))
        water_fill = "rgba(77,163,255,0.45)"
    else:
        xw, bed_w, surf_w = xs, h, h
        water_fill = "rgba(0,0,0,0)"
    fw.add_trace(go.Scatter(x=xw, y=bed_w, mode="lines", line=dict(width=0),
                            hoverinfo="skip", showlegend=False))
    fw.add_trace(go.Scatter(x=xw, y=surf_w, mode="lines", fill="tonexty",
                            fillcolor=water_fill, line=dict(width=0),
                            hoverinfo="skip", showlegend=False))
    # bed line on top of the fills (4)
    fw.add_trace(go.Scatter(
        x=xs, y=h, mode="lines", line=dict(color="#5b4a3a", width=1.8), showlegend=False,
        hovertemplate=f"station %{{x:.0f}} {ul}<br>height %{{y:.1f}} {ul}<extra></extra>"))

    # Default view: window the axis ranges where the terrain climbs far above the
    # highest reference line (a valley wall would otherwise squash the channel into
    # the bottom sliver of the plot). The traces keep the full transect — pan/zoom
    # still reveals it; only the default (and reset) ranges are bounded. Computed
    # before the reference lines so the label-overlap check can use the final span.
    ymax = max(h)
    top_line = max(2.0 * bf_h, lb_h or 0.0) if bf_h is not None else 0.0
    if top_line > 0:
        ceiling_h = geomorph.VIEW_HEADROOM * top_line
        lo, hi = geomorph.display_window(xs, h, ceiling_stage=ceiling_h, thalweg_index=ti,
                                         min_half=geomorph.VIEW_MIN_HALF_M * u)
        padx = 0.03 * (hi - lo) if hi > lo else 1.0
        x_range = [lo - padx, hi + padx]
        hmax_win = max((hv for xv, hv in zip(xs, h) if lo <= xv <= hi), default=0.0)
        ymax = max(top_line, min(hmax_win, ceiling_h))  # terrain above exits the top
    else:
        xb = max((abs(v) for v in xs), default=1.0)
        x_range = [-xb, xb]
        if bf_h is not None:
            ymax = max(ymax, 2.0 * bf_h)
        if lb_h is not None:
            ymax = max(ymax, lb_h)
    pad = 0.06 * (ymax - base) if ymax > base else 1.0

    fw.add_hline(y=0.0, line=dict(color="#b9aa97", width=0.8, dash="dot"))   # bed datum
    if bf_h is not None:
        fw.add_hline(y=bf_h, line=dict(color="#1f6fc0", width=1.4),
                     annotation_text="bankfull", annotation_position="top right",
                     annotation_font=dict(color="#1f6fc0", size=11))
        fw.add_hline(y=2.0 * bf_h, line=dict(color="#9a6b3f", width=1.2, dash="dot"),
                     annotation_text="floodprone", annotation_position="top right",
                     annotation_font=dict(color="#9a6b3f", size=11))
    if lb_h is not None:
        # The low-bank line can coincide with floodprone (capped default) or with
        # bankfull (a low bench). Shift its label left of the neighbouring text when
        # the lines are close so they read side by side instead of overprinting;
        # normal right-edge position otherwise.
        yspan = (ymax + pad) - base
        clash = bf_h is not None and (abs(lb_h - 2.0 * bf_h) < LABEL_EPS_FRAC * yspan
                                      or abs(lb_h - bf_h) < LABEL_EPS_FRAC * yspan)
        ann = dict(annotation_text="low bank", annotation_position="top right",
                   annotation_font=dict(color="#3a8a5c", size=11))
        if clash:
            ann["annotation_xshift"] = LABEL_CLASH_XSHIFT_PX
        fw.add_hline(y=lb_h, line=dict(color="#3a8a5c", width=1.4, dash="dash"), **ann)
    fw.update_layout(
        dragmode="zoom", hovermode="closest", showlegend=False,
        # extra bottom room for the data-source caption when present
        margin=dict(l=55, r=74, t=12, b=62 if source else 42),
        plot_bgcolor="white", paper_bgcolor="white",
        # keep box-zoom, pan, and the +/- magnifiers; the app injects zoom-home /
        # zoom-to-extents modebar buttons in place of the native reset/autoscale
        modebar=dict(remove=["resetScale2d", "autoScale2d", "select2d", "lasso2d",
                             "toImage", "toggleSpikelines", "toggleHover"]),
        xaxis=dict(title=f"Station ({ul})", range=x_range, zeroline=False,
                   gridcolor="#eef0f4"),
        yaxis=dict(title=f"Height above bed ({ul})", range=[base, ymax + pad],
                   gridcolor="#eef0f4"),
    )
    # small data-source caption at the bottom-right (e.g., "USGS 3DEP 1 m DEM"). A fixed
    # pixel yshift (below the plot area, into the margin) keeps it in place regardless of the
    # plot's height — a paper-ratio y would drift with taller plots.
    if source:
        fw.add_annotation(text=source, xref="paper", yref="paper", x=1.0, y=0,
                          xanchor="right", yanchor="top", yshift=-46, showarrow=False,
                          font=dict(size=9, color="#9aa4b2"))
    return fw

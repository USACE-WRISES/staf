"""Interactive cross-section plot (Plotly ``go.Figure``) for the report modal.

Mirrors the datum, scaling, and styling of :func:`easi.xsplot.cross_section_png` (the
static matplotlib PNG embedded in the PDF), but returns an interactive figure so the
user can drag a box to zoom, pan, hover for station/height, and reset to the full
extent (Plotly modebar "Reset axes" / double-click). The PDF export keeps the
matplotlib renderer; this module is UI-only.

A plain ``go.Figure`` is returned (not a ``FigureWidget``) so it stays pure/testable;
shinywidgets coerces it to a ``FigureWidget`` inside the Shiny session at render time.

The Y axis is height above the channel bottom (thalweg = 0); both axes are in ``unit``
("ft" default, or "m"). Stages are absolute elevations on the profile's metres datum
and are converted to heights here.
"""
from __future__ import annotations

from typing import Optional

FT_PER_M = 3.28083989501312


def _water_polygon(xs, h, level):
    """Densify ``(xs, h)`` with a point wherever the bed crosses ``level``, and return
    ``(x2, bed2, surf2)`` with ``surf2 = max(bed2, level)``.

    Filling between ``surf2`` and ``bed2`` (Plotly ``fill="tonexty"``) then yields a
    water top that is *flat* at ``level`` and pinches to zero exactly at the crossings.
    Without the inserted crossings the surface would be clamped only at data points, so
    on a steep, sparsely-sampled bank Plotly draws it straight up to the next high point
    (the fill rides above bankfull). This mirrors matplotlib's
    ``fill_between(..., where=h<=level, interpolate=True)`` used by the PDF renderer.
    """
    x2, bed2 = [xs[0]], [h[0]]
    for i in range(1, len(xs)):
        x0, y0, x1, y1 = xs[i - 1], h[i - 1], xs[i], h[i]
        if (y0 < level < y1) or (y1 < level < y0):      # segment crosses the level
            t = (level - y0) / (y1 - y0)
            x2.append(x0 + t * (x1 - x0))
            bed2.append(level)
        x2.append(x1)
        bed2.append(y1)
    surf2 = [level if b <= level else b for b in bed2]
    return x2, bed2, surf2


def figure(stations, elevs, *, thalweg: Optional[float] = None,
           bankfull_stage: Optional[float] = None,
           floodplain_stage: Optional[float] = None,
           unit: str = "ft", source: Optional[str] = None):
    """Build the interactive cross-section as a Plotly ``go.Figure``.

    Always returns a fixed 5-trace structure (terrain baseline, terrain fill, water
    baseline, water fill, bed line) so the report can update the live widget by position
    without ever adding/removing traces; the water fill collapses to the bed (transparent)
    when there is no bankfull stage. Plus horizontal reference lines for bankfull,
    flood-prone (2x bankfull depth, Rosgen), and low bank, each labelled at the right edge. The
    axis ranges are set explicitly so "Reset axes"/double-click returns to full extent.
    No in-figure title (the section renders one in HTML) and no fixed height, so
    shinywidgets stretches the plot to fill its container. The modebar keeps only
    zoom / pan / reset-axes.
    """
    import plotly.graph_objects as go

    u = FT_PER_M if unit == "ft" else 1.0
    ul = "ft" if unit == "ft" else "m"
    x = [float(s) for s in stations]
    z = [float(e) for e in elevs]
    thal = float(thalweg) if thalweg is not None else min(z)
    x0 = x[z.index(min(z))]                 # centre the plot on the thalweg
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

    ymax = max(h)
    # water (2: baseline at the bed, 3: fill up to the bankfull surface). Always emitted
    # so the trace count is fixed; with no bankfull it collapses to the bed (zero area)
    # and is drawn transparent.
    if bankfull_stage is not None:
        bf_h = (float(bankfull_stage) - thal) * u
        # Insert bankfull crossings so the water top is flat at bf_h and pinches exactly
        # where the bed rises through it (else it slants up steep, sparse banks).
        xw, bed_w, surf_w = _water_polygon(xs, h, bf_h)
        water_fill = "rgba(77,163,255,0.45)"
        ymax = max(ymax, 2.0 * bf_h)
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

    fw.add_hline(y=0.0, line=dict(color="#b9aa97", width=0.8, dash="dot"))   # bed datum
    if bankfull_stage is not None:
        bf_h = (float(bankfull_stage) - thal) * u
        fw.add_hline(y=bf_h, line=dict(color="#1f6fc0", width=1.4),
                     annotation_text="bankfull", annotation_position="top right",
                     annotation_font=dict(color="#1f6fc0", size=11))
        fw.add_hline(y=2.0 * bf_h, line=dict(color="#9a6b3f", width=1.2, dash="dot"),
                     annotation_text="floodprone", annotation_position="top right",
                     annotation_font=dict(color="#9a6b3f", size=11))
    if floodplain_stage is not None:
        lb_h = (float(floodplain_stage) - thal) * u
        fw.add_hline(y=lb_h, line=dict(color="#3a8a5c", width=1.4, dash="dash"),
                     annotation_text="low bank", annotation_position="top right",
                     annotation_font=dict(color="#3a8a5c", size=11))
        ymax = max(ymax, lb_h)

    xb = max((abs(v) for v in xs), default=1.0)
    pad = 0.06 * (ymax - base) if ymax > base else 1.0
    fw.update_layout(
        dragmode="zoom", hovermode="closest", showlegend=False,
        # extra bottom room for the data-source caption when present
        margin=dict(l=55, r=74, t=12, b=62 if source else 42),
        plot_bgcolor="white", paper_bgcolor="white",
        modebar=dict(remove=["autoScale2d", "select2d", "lasso2d", "zoomIn2d",
                             "zoomOut2d", "toImage", "toggleSpikelines", "toggleHover"]),
        xaxis=dict(title=f"Station ({ul})", range=[-xb, xb], zeroline=False,
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

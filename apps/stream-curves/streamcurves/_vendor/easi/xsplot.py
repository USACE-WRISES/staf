"""Static cross-section hydraulics plot (matplotlib PNG, Agg backend).

Mirrors the xs-calc cross-section view: a station-elevation bed line, a blue water
fill to the bankfull stage, a dashed floodplain-engagement line, thalweg/bank
markers, and an entrenchment / bank-height annotation. The water fill covers only
the contiguous water body spanning the thalweg, and the axes are windowed where the
terrain climbs far above the reference lines (both mirroring
:mod:`easi.xsplotly`, the interactive renderer). Returns PNG bytes; on any failure
returns a small placeholder PNG so report embeds never crash.
"""
from __future__ import annotations

import base64
import io
from typing import Optional

from . import geomorph

# Label-collision handling for the low-bank reference line (mirrors xsplotly):
# labels overlap when their lines sit within this fraction of the y-span.
LABEL_EPS_FRAC = 0.06


def _placeholder(msg: str = "cross-section unavailable") -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=11, color="#888")
    ax.axis("off")
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=130)
    plt.close(fig)
    return out.getvalue()


FT_PER_M = 3.28083989501312


def cross_section_png(stations, elevs, *, bankfull_stage: Optional[float] = None,
                      floodplain_stage: Optional[float] = None,
                      thalweg: Optional[float] = None,
                      entrenchment_ratio: Optional[float] = None,
                      bank_height_ratio: Optional[float] = None,
                      bankfull_width_m: Optional[float] = None,
                      bankfull_depth_m: Optional[float] = None,
                      division: Optional[str] = None,
                      unit: str = "ft",
                      title: str = "Representative cross-section",
                      source: Optional[str] = None) -> bytes:
    """Render the cross-section as PNG bytes (placeholder PNG on any failure).

    The Y axis is **height above the channel bottom** (thalweg = 0); both axes are
    drawn in ``unit`` ("ft" default, or "m"). Stages are absolute elevations on the
    profile's metres datum and are converted to heights here. No thalweg marker.
    """
    try:
        if stations is None or elevs is None or len(stations) < 3:
            return _placeholder()
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        u = FT_PER_M if unit == "ft" else 1.0
        ul = "ft" if unit == "ft" else "m"
        x = np.asarray(stations, dtype=float)
        z = np.asarray(elevs, dtype=float)
        thal = float(thalweg) if thalweg is not None else float(z.min())
        ti = int(np.argmin(z))
        xs = (x - x[ti]) * u                        # station, centered on the bed, in unit
        h = (z - thal) * u                          # height above the channel bottom
        bf_h = (float(bankfull_stage) - thal) * u if bankfull_stage is not None else None
        lb_h = (float(floodplain_stage) - thal) * u if floodplain_stage is not None else None

        # Default view (mirrors xsplotly): window the axes where the terrain climbs
        # above VIEW_HEADROOM x the highest reference line, so a valley wall doesn't
        # squash the channel into the bottom sliver of the plot.
        base = float(h.min()) - 0.5
        ymax = float(h.max())
        top_line = max(2.0 * bf_h, lb_h or 0.0) if bf_h is not None else 0.0
        if top_line > 0:
            ceiling_h = geomorph.VIEW_HEADROOM * top_line
            lo, hi = geomorph.display_window(xs, h, ceiling_stage=ceiling_h, thalweg_index=ti,
                                             min_half=geomorph.VIEW_MIN_HALF_M * u)
            padx = 0.03 * (hi - lo) if hi > lo else 1.0
            x_lo, x_hi = float(lo - padx), float(hi + padx)
            in_win = (xs >= lo) & (xs <= hi)
            hmax_win = float(h[in_win].max()) if bool(in_win.any()) else 0.0
            ymax = max(top_line, min(hmax_win, ceiling_h))  # terrain above exits the top
        else:
            xb = float(np.max(np.abs(xs))) if xs.size else 1.0  # symmetric about channel center
            x_lo, x_hi = -xb, xb
            if lb_h is not None:
                ymax = max(ymax, lb_h)

        fig, ax = plt.subplots(figsize=(6.5, 2.6))
        ax.fill_between(xs, h, h.min() - 0.5, color="#efe9e1", zorder=1)        # ground
        ax.plot(xs, h, color="#5b4a3a", lw=1.6, zorder=3)                       # bed line
        ax.axhline(0.0, color="#b9aa97", lw=0.8, ls=":", zorder=2)             # bed datum

        if bf_h is not None:
            # Water fill bounded to the contiguous water body spanning the thalweg —
            # the same rule flow_width/flow_area use — so a disconnected low spot
            # elsewhere on the transect is never drawn as water.
            span = geomorph.wetted_span(xs, h, bf_h, thalweg_index=ti)
            wet = ((h <= bf_h) & (xs >= span[0]) & (xs <= span[1])) if span is not None \
                else np.zeros(xs.shape, dtype=bool)
            ax.fill_between(xs, h, bf_h, where=wet,
                            color="#4da3ff", alpha=0.45, interpolate=True, zorder=2)
            ax.axhline(bf_h, color="#1f6fc0", lw=1.1, zorder=4)
            ax.text(x_hi, bf_h, " bankfull", color="#1f6fc0",
                    fontsize=8, va="center", ha="left")
            # flood-prone stage = 2x bankfull depth (Rosgen): where the flood-prone
            # width and entrenchment ratio are measured
            fpr_h = 2.0 * bf_h
            ax.axhline(fpr_h, color="#9a6b3f", lw=1.0, ls=":", zorder=4)
            ax.text(x_hi, fpr_h, " floodprone", color="#9a6b3f",
                    fontsize=8, va="center", ha="left")
        pady = 0.06 * (ymax - base) if ymax > base else 1.0
        if lb_h is not None:  # the low-bank stage (drives the bank-height ratio)
            ax.axhline(lb_h, color="#3a8a5c", lw=1.1, ls="--", zorder=4)
            # The low-bank line can coincide with floodprone (capped default) or
            # bankfull (a low bench). When close, draw its label inside the right
            # edge (left of the outside labels) so they read side by side instead
            # of overprinting; normal outside position otherwise (mirrors xsplotly).
            yspan = (ymax + pady) - base
            clash = bf_h is not None and (abs(lb_h - 2.0 * bf_h) < LABEL_EPS_FRAC * yspan
                                          or abs(lb_h - bf_h) < LABEL_EPS_FRAC * yspan)
            if clash:
                ax.text(x_hi, lb_h, "low bank ", color="#3a8a5c",
                        fontsize=8, va="center", ha="right")
            else:
                ax.text(x_hi, lb_h, " low bank", color="#3a8a5c",
                        fontsize=8, va="center", ha="left")

        ax.set_xlabel(f"Station ({ul})", fontsize=9)
        ax.set_ylabel(f"Height above bed ({ul})", fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.tick_params(labelsize=8)
        ax.set_xlim(x_lo, x_hi)
        ax.set_ylim(base, ymax + pady)
        fig.tight_layout()
        if source:  # small data-source caption, bottom-right (e.g., "USGS 3DEP 1 m DEM")
            fig.text(0.995, 0.01, source, ha="right", va="bottom", fontsize=6.5, color="#999")
        out = io.BytesIO()
        fig.savefig(out, format="png", dpi=130)
        plt.close(fig)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - resilience by design
        return _placeholder()


def cross_section_png_b64(*args, **kwargs) -> str:
    """Base64-encoded PNG (no ``data:`` prefix) for embedding in HTML/JSON."""
    return base64.b64encode(cross_section_png(*args, **kwargs)).decode("ascii")

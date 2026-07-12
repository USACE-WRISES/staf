"""Static cross-section hydraulics plot (matplotlib PNG, Agg backend).

Mirrors the xs-calc cross-section view: a station-elevation bed line, a blue water
fill to the bankfull stage, a dashed floodplain-engagement line, thalweg/bank
markers, and an entrenchment / bank-height annotation. Returns PNG bytes; on any
failure returns a small placeholder PNG so report embeds never crash.
"""
from __future__ import annotations

import base64
import io
from typing import Optional


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
        xs = (x - x[int(np.argmin(z))]) * u        # station, centered on the bed, in unit
        h = (z - thal) * u                          # height above the channel bottom

        fig, ax = plt.subplots(figsize=(6.5, 2.6))
        ax.fill_between(xs, h, h.min() - 0.5, color="#efe9e1", zorder=1)        # ground
        ax.plot(xs, h, color="#5b4a3a", lw=1.6, zorder=3)                       # bed line
        ax.axhline(0.0, color="#b9aa97", lw=0.8, ls=":", zorder=2)             # bed datum

        if bankfull_stage is not None:
            bf_h = (float(bankfull_stage) - thal) * u
            ax.fill_between(xs, h, bf_h, where=(h <= bf_h),
                            color="#4da3ff", alpha=0.45, interpolate=True, zorder=2)
            ax.axhline(bf_h, color="#1f6fc0", lw=1.1, zorder=4)
            ax.text(xs.max(), bf_h, " bankfull", color="#1f6fc0",
                    fontsize=8, va="center", ha="left")
            # flood-prone stage = 2x bankfull depth (Rosgen): where the flood-prone
            # width and entrenchment ratio are measured
            fpr_h = 2.0 * bf_h
            ax.axhline(fpr_h, color="#9a6b3f", lw=1.0, ls=":", zorder=4)
            ax.text(xs.max(), fpr_h, " floodprone", color="#9a6b3f",
                    fontsize=8, va="center", ha="left")
        if floodplain_stage is not None:  # the low-bank stage (drives the bank-height ratio)
            lb_h = (float(floodplain_stage) - thal) * u
            ax.axhline(lb_h, color="#3a8a5c", lw=1.1, ls="--", zorder=4)
            ax.text(xs.max(), lb_h, " low bank", color="#3a8a5c",
                    fontsize=8, va="center", ha="left")

        ax.set_xlabel(f"Station ({ul})", fontsize=9)
        ax.set_ylabel(f"Height above bed ({ul})", fontsize=9)
        ax.set_title(title, fontsize=10)
        ax.tick_params(labelsize=8)
        xb = float(np.max(np.abs(xs))) if xs.size else 1.0  # symmetric about channel center
        ax.set_xlim(-xb, xb)
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

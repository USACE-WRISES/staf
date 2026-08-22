"""Inline SVG thumbnails of reference curves: the small-multiple grid.

The batch review packet draws a matplotlib PNG of every built curve; the app
wants the same picture as page content it can click. These builders take a
plain *tile* dict (one per metric) and return SVG and HTML strings, nothing
else: no shiny, no matplotlib, so the batch script and the Reference Curves
page share one drawing and the tests read the markup directly.

Tile schema (``tile_from_curve_rows`` builds it from registry rows)::

    {
      "metric": "phab_XEMBED", "display_name": "Embeddedness", "function": "...",
      "units": "%", "in_scope": True | False | None, "needs_review": bool,
      "review_status": "auto_ok" | ... | None, "decision": "auto_finalized" | ... | None,
      "reference_range": (min, max) or (None, None), "domain": (dmin, dmax),
      "strata": [{"label": None | "A", "points": [(x, y), ...],
                  "curve_status": "complete", "n_reference": 23, "curve_source": "auto"}],
      "flags": ["status data_review", ...], "badge": None | "Low",
    }

Band breaks default to the DEEP scoring contract (0.39 / 0.69), the breaks
DEEP prints a condition from; the app's per-metric plots draw the R-parity
drawing bands (0.30 / 0.70), exported here as ``DRAWING_BANDS`` so a caller can
pick either with one argument.
"""
from __future__ import annotations

import html
import math
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from . import curves, deep_export, run_state

DEEP_INDEX_BANDS: tuple[float, float] = tuple(
    float(v) for v in deep_export.SCORING_CONTRACT_CONSTANTS["indexBands"])  # (0.39, 0.69)
DRAWING_BANDS: tuple[float, float] = tuple(float(v) for v in curves.INDEX_DRAWING_BANDS)  # (0.30, 0.70)

# One dash pattern per stratum, cycling; the first stratum is solid.
STRATUM_DASHES = ("", "6 3", "2 3", "8 3 2 3")
OUT_OF_SCOPE_DASH = "2 3"

# Presentation attributes double as defaults for the standalone packet page; the
# app's stylesheet overrides them by class (a stylesheet beats a presentation
# attribute), so the STAF band tokens win inside the app.
BAND_FILL = {"band-poor": "#f5b5b5", "band-fair": "#f5e7a6", "band-good": "#c8d9f2"}
LINE_COLOR = "#2f4b7c"
OUT_OF_SCOPE_COLOR = "#b04040"
RANGE_COLOR = "#2f4b7c"
BREAK_COLOR = "#8a93a3"
FLAG_COLOR = "#f39c12"
TICK_COLOR = "#6b7689"

STATUS_LABELS = {
    run_state.CURVE_STATUS_AUTO_OK: "Clean build",
    run_state.CURVE_STATUS_INSUFFICIENT: "Insufficient data",
    run_state.CURVE_STATUS_DEGENERATE: "Degenerate curve",
    run_state.CURVE_STATUS_UNMAPPED: "Unmapped metric",
    run_state.CURVE_STATUS_SHAPE_CONFLICT: "Shape conflict",
    run_state.CURVE_STATUS_DATA_REVIEW: "Missing-data review",
    run_state.CURVE_STATUS_STRAT_REVIEW: "Stratification review",
    run_state.CURVE_STATUS_MULTI_CROSSING: "Multiple crossings",
    run_state.CURVE_STATUS_ERROR: "Build error",
}

DECISION_LABELS = {
    run_state.DECISION_AUTO: "Auto",
    run_state.DECISION_FINALIZED: "Accepted",
    run_state.DECISION_REMOVED: "Removed",
    run_state.DECISION_PENDING: "Needs review",
    None: "Unreviewed",
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def fmt_num(v: Any) -> str:
    """Compact axis label: 35.0 -> '35', 1.250 -> '1.25', 0.0012 -> '0.0012'."""
    f = _num(v)
    if f is None:
        return "" if v is None else str(v)
    if f == int(f) and abs(f) < 1e9:
        return str(int(f))
    if abs(f) < 0.01:
        return f"{f:.2g}"
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == "" or str(v).strip().lower() in ("nan", "none")


def points_from_curve_row(row: Mapping | None) -> list[tuple[float, float]]:
    """The seed points of a registry row as ``[(x, y), ...]`` sorted by x with y
    clamped to [0, 1]. Accepts the nested ``curve_points`` table (DataFrame,
    records, or column mapping) or the flat ``curve_point{n}_x/y`` columns,
    through the DEEP exporter's own reader so the thumbnail and the bundle
    agree. Empty when the row carries no usable points."""
    if row is None:
        return []
    r = dict(row)
    cp = r.get("curve_points")
    if cp is not None and not isinstance(cp, pd.DataFrame):
        # records, a column mapping, or a stray scalar: canonicalize first so the
        # DEEP reader sees the three-column table it expects
        try:
            r["curve_points"] = curves.normalize_reference_curve_points(cp)
        except (TypeError, ValueError, AttributeError):
            r["curve_points"] = None
    pts = deep_export.deep_points_from_row(r)
    out: list[tuple[float, float]] = []
    for p in pts or []:
        x, y = _num(p.get("x")), _num(p.get("y"))
        if x is None or y is None:
            continue
        out.append((x, max(0.0, min(1.0, y))))
    out.sort(key=lambda t: t[0])
    return out


def decision_of(review_entry: Mapping | None) -> Optional[str]:
    """The recorded decision, with the packet's fallback for an entry that
    carries a status but no decision yet."""
    if not review_entry:
        return None
    decision = review_entry.get("decision")
    if decision:
        return str(decision)
    status = review_entry.get("status")
    if not status:
        return None
    return (run_state.DECISION_AUTO if status == run_state.CURVE_STATUS_AUTO_OK
            else run_state.DECISION_PENDING)


def tile_from_curve_rows(metric: str, rows: Iterable[Mapping] | None, *,
                         metric_entry: Mapping | None = None,
                         review_entry: Mapping | None = None,
                         function_label: str | None = None,
                         in_scope: bool | None = None) -> dict:
    """One tile from a metric's registry rows (one row per stratum).

    Scope and the review flag come from the ``curve_review`` entry
    (``run_state.is_in_scope`` / ``needs_review``); a metric with no entry is
    "unreviewed" (``in_scope`` None, drawn solid) rather than out of scope, so a
    session that predates the review vocabulary does not render every curve
    dotted. ``in_scope=`` overrides the entry (the packet passes its own
    intended-metrics test). The reference range is the finite union of
    ``min_val`` / ``max_val`` across strata."""
    mc = dict(metric_entry or {})
    strata: list[dict] = []
    lo: Optional[float] = None
    hi: Optional[float] = None
    for row in rows or []:
        if row is None:
            continue
        r = dict(row)
        label = None if _is_blank(r.get("stratum")) else str(r.get("stratum")).strip()
        strata.append({
            "label": label,
            "points": points_from_curve_row(r),
            "curve_status": r.get("curve_status"),
            "n_reference": _num(r.get("n_reference")),
            "curve_source": r.get("curve_source"),
        })
        rlo, rhi = _num(r.get("min_val")), _num(r.get("max_val"))
        if rlo is not None:
            lo = rlo if lo is None else min(lo, rlo)
        if rhi is not None:
            hi = rhi if hi is None else max(hi, rhi)
    if review_entry:
        scope = run_state.is_in_scope(dict(review_entry))
        needs = run_state.needs_review(dict(review_entry))
        status = review_entry.get("status")
        flags = [str(x) for x in (review_entry.get("reasons") or []) if str(x).strip()]
    else:
        scope, needs, status, flags = None, False, None, []
    if in_scope is not None:
        scope = bool(in_scope)
    return {
        "metric": str(metric),
        "display_name": str(mc.get("display_name") or metric),
        "function": function_label,
        "units": mc.get("units"),
        "in_scope": scope,
        "needs_review": bool(needs),
        "review_status": status,
        "decision": decision_of(review_entry),
        "reference_range": (lo, hi) if (lo is not None and hi is not None) else (None, None),
        "domain": curves.metric_domain_of(mc),
        "strata": strata,
        "flags": flags,
        "badge": None,
    }


# --------------------------------------------------------------------------- #
# The thumbnail
# --------------------------------------------------------------------------- #
def _x_range(tile: Mapping) -> Optional[tuple[float, float]]:
    xs: list[float] = []
    for s in tile.get("strata") or []:
        xs.extend(x for x, _ in (s.get("points") or []))
    lo, hi = tile.get("reference_range") or (None, None)
    if lo is not None and hi is not None:
        xs.extend((lo, hi))
    if not xs:
        return None
    xmin, xmax = min(xs), max(xs)
    if xmax == xmin:
        pad = abs(xmin) * 0.05 or 0.5
    else:
        pad = (xmax - xmin) * 0.04
    return (xmin - pad, xmax + pad)


def tile_svg(tile: Mapping, *, w: int = 240, h: int = 150,
             band_breaks: tuple[float, float] = DEEP_INDEX_BANDS) -> str:
    """The thumbnail: band rects split at ``band_breaks``, two dashed break
    lines, the reference range shaded, one polyline per stratum (dash patterns
    cycle; every line dotted and red when the metric is out of scope), the seed
    points, min and max x ticks, y ticks at 0, the breaks, and 1, and a flag
    glyph carrying the first flag when the metric needs review. A tile with no
    points keeps its full size and says so."""
    lo_b, hi_b = float(band_breaks[0]), float(band_breaks[1])
    ml, mr, mt, mb = 30, 8, 8, 18
    x0, x1 = ml, w - mr
    y1, y0 = h - mb, mt
    metric = str(tile.get("metric") or "")
    name = str(tile.get("display_name") or metric)
    decision = tile.get("decision")
    in_scope = tile.get("in_scope")
    flags = [str(f) for f in (tile.get("flags") or [])]
    xr = _x_range(tile)
    strata = [s for s in (tile.get("strata") or []) if s.get("points")]

    def sy(v: float) -> float:
        return y1 - max(0.0, min(1.0, v)) * (y1 - y0)

    parts = [
        f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" class="curve-tile-svg" '
        f'role="img" preserveAspectRatio="xMidYMid meet" data-metric="{html.escape(metric, quote=True)}" '
        f'data-breaks="{fmt_num(lo_b)},{fmt_num(hi_b)}"'
        + (f' data-xmin="{xr[0]:g}" data-xmax="{xr[1]:g}"' if xr else ' data-empty="1"')
        + ">",
        f"<title>{html.escape(name)}: {html.escape(DECISION_LABELS.get(decision, str(decision)))}</title>",
    ]
    # condition bands, bottom to top
    for cls, (lo, hi) in (("band-poor", (0.0, lo_b)), ("band-fair", (lo_b, hi_b)), ("band-good", (hi_b, 1.0))):
        yt, yb = sy(hi), sy(lo)
        parts.append(f'<rect class="curve-tile-band {cls}" x="{x0:.1f}" y="{yt:.1f}" '
                     f'width="{x1 - x0:.1f}" height="{yb - yt:.1f}" fill="{BAND_FILL[cls]}" fill-opacity="0.45"/>')
    if xr is None:
        parts.append(f'<text class="curve-tile-empty" x="{(x0 + x1) / 2:.1f}" y="{(y0 + y1) / 2:.1f}" '
                     f'text-anchor="middle" font-size="10" fill="{TICK_COLOR}">No curve</text>')
        parts.append("</svg>")
        return "".join(parts)
    xmin, xmax = xr
    dx = (xmax - xmin) or 1.0

    def sx(v: float) -> float:
        return x0 + (v - xmin) / dx * (x1 - x0)

    rlo, rhi = tile.get("reference_range") or (None, None)
    if rlo is not None and rhi is not None:
        a, b = sx(max(rlo, xmin)), sx(min(rhi, xmax))
        parts.append(f'<rect class="curve-tile-range" x="{a:.1f}" y="{y0:.1f}" width="{max(b - a, 0.5):.1f}" '
                     f'height="{y1 - y0:.1f}" fill="{RANGE_COLOR}" fill-opacity="0.10"/>')
    for yv in (lo_b, hi_b):
        yp = sy(yv)
        parts.append(f'<line class="curve-tile-break" x1="{x0:.1f}" y1="{yp:.1f}" x2="{x1:.1f}" y2="{yp:.1f}" '
                     f'stroke="{BREAK_COLOR}" stroke-width="0.8" stroke-dasharray="3 2"/>')
    # axes and ticks
    parts.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x0:.1f}" y2="{y1:.1f}" stroke="#b7c0cf" stroke-width="0.8"/>')
    parts.append(f'<line x1="{x0:.1f}" y1="{y1:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="#b7c0cf" stroke-width="0.8"/>')
    for yv in (0.0, lo_b, hi_b, 1.0):
        parts.append(f'<text class="curve-tile-tick" x="{x0 - 3:.1f}" y="{sy(yv) + 2.5:.1f}" text-anchor="end" '
                     f'font-size="7" fill="{TICK_COLOR}">{fmt_num(yv)}</text>')
    for xv, anchor in ((xmin, "start"), (xmax, "end")):
        parts.append(f'<text class="curve-tile-tick" x="{sx(xv):.1f}" y="{y1 + 10:.1f}" text-anchor="{anchor}" '
                     f'font-size="7" fill="{TICK_COLOR}">{html.escape(fmt_num(xv))}</text>')
    # the curves
    color = OUT_OF_SCOPE_COLOR if in_scope is False else LINE_COLOR
    for i, s in enumerate(strata):
        pts = s["points"]
        line = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
        cls = "curve-tile-line" + (" out-of-scope" if in_scope is False else "")
        dash = OUT_OF_SCOPE_DASH if in_scope is False else STRATUM_DASHES[i % len(STRATUM_DASHES)]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        label_attr = f' data-stratum="{html.escape(str(s.get("label")), quote=True)}"' if s.get("label") else ""
        parts.append(f'<polyline class="{cls}" points="{line}" fill="none" stroke="{color}" '
                     f'stroke-width="1.6"{dash_attr}{label_attr}/>')
        for x, y in pts:
            parts.append(f'<circle class="curve-tile-point" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="1.8" fill="{color}"/>')
    if tile.get("needs_review"):
        fx, fy = x1 - 9, y0 + 2
        tip = flags[0] if flags else "Needs review"
        parts.append(f'<path class="curve-tile-flag" d="M{fx:.1f},{fy + 9:.1f} L{fx + 4.5:.1f},{fy:.1f} '
                     f'L{fx + 9:.1f},{fy + 9:.1f} Z" fill="{FLAG_COLOR}"><title>{html.escape(tip)}</title></path>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# A self-contained gallery page (the batch packet)
# --------------------------------------------------------------------------- #
GALLERY_CSS = """
body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; margin: 1.25rem; color: #1f2933; }
h1 { font-size: 1.1rem; margin: 0 0 .25rem; }
.curve-gallery-legend { color: #5b6776; font-size: .8rem; margin: 0 0 1rem; }
.curve-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: .75rem; }
.curve-tile { border: 1px solid #d8e2ec; border-radius: .5rem; background: #fff; padding: .45rem .6rem .35rem; }
.curve-tile.is-flagged { border-left: 4px solid #f39c12; }
.curve-tile.is-removed { opacity: .72; }
.curve-tile-head { display: flex; justify-content: space-between; align-items: baseline; gap: .4rem; }
.curve-tile-code { font-weight: 600; font-size: .8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.curve-tile-status { font-size: .66rem; padding: .05rem .4rem; border-radius: 999px; background: #eef1f5; color: #6c757d; white-space: nowrap; }
.is-flagged .curve-tile-status { background: #f39c12; color: #fff; }
.is-removed .curve-tile-status { background: #d9534f; color: #fff; }
.is-finalized .curve-tile-status { background: #27ae60; color: #fff; }
.curve-tile-svg { display: block; width: 100%; height: auto; }
.curve-tile-foot { display: flex; justify-content: space-between; gap: .4rem; font-size: .7rem; color: #6c757d; }
.curve-tile-strata { background: #6c757d; color: #fff; border-radius: 999px; padding: 0 .4rem; font-size: .62rem; }
"""


def tile_state_classes(tile: Mapping) -> list[str]:
    """The CSS state classes of a tile, shared by the page and the app."""
    classes = []
    if tile.get("needs_review"):
        classes.append("is-flagged")
    decision = tile.get("decision")
    if decision == run_state.DECISION_REMOVED or tile.get("in_scope") is False:
        classes.append("is-removed")
    elif decision == run_state.DECISION_FINALIZED:
        classes.append("is-finalized")
    elif decision is None:
        classes.append("is-unreviewed")
    if not any(s.get("points") for s in (tile.get("strata") or [])):
        classes.append("is-not-run")
    if len(tile.get("strata") or []) > 1:
        classes.append("is-stratified")
    return classes


def tile_title(tile: Mapping) -> str:
    """The hover text: display name, decision, the first flag."""
    bits = [str(tile.get("display_name") or tile.get("metric") or "")]
    bits.append(DECISION_LABELS.get(tile.get("decision"), str(tile.get("decision"))))
    flags = tile.get("flags") or []
    if flags:
        bits.append(str(flags[0]))
    return ". ".join(b for b in bits if b)


def status_label(tile: Mapping) -> str:
    status = tile.get("review_status")
    if status:
        return STATUS_LABELS.get(status, str(status))
    return DECISION_LABELS.get(tile.get("decision"), "Unreviewed")


def gallery_html(tiles: list[Mapping], *, title: str, w: int = 240, h: int = 150,
                 band_breaks: tuple[float, float] = DEEP_INDEX_BANDS) -> str:
    """A self-contained page: inline CSS, one tile per entry, no scripts."""
    out = ["<!doctype html>", "<html lang=\"en\"><head><meta charset=\"utf-8\">",
           f"<title>{html.escape(title)}</title>", f"<style>{GALLERY_CSS}</style></head><body>",
           f"<h1>{html.escape(title)}</h1>",
           "<p class=\"curve-gallery-legend\">Shaded column: the reference range. Dashed lines: the "
           f"condition breaks at {fmt_num(band_breaks[0])} and {fmt_num(band_breaks[1])}. "
           "Dotted red curve: not in scope. Orange marker: needs review.</p>",
           "<div class=\"curve-gallery\">"]
    for t in tiles:
        classes = " ".join(["curve-tile", *tile_state_classes(t)])
        n_strata = len(t.get("strata") or [])
        foot = [f"<span>{html.escape(str(t.get('function') or ''))}</span>"]
        right = []
        if t.get("badge"):
            right.append(f"<span>{html.escape(str(t['badge']))}</span>")
        if n_strata > 1:
            right.append(f"<span class=\"curve-tile-strata\">{n_strata} strata</span>")
        foot.append("<span>" + " ".join(right) + "</span>")
        out.append(
            f"<div class=\"{classes}\" title=\"{html.escape(tile_title(t), quote=True)}\">"
            f"<div class=\"curve-tile-head\"><span class=\"curve-tile-code\">{html.escape(str(t.get('metric')))}</span>"
            f"<span class=\"curve-tile-status\">{html.escape(status_label(t))}</span></div>"
            + tile_svg(t, w=w, h=h, band_breaks=band_breaks)
            + "<div class=\"curve-tile-foot\">" + "".join(foot) + "</div></div>")
    out.append("</div></body></html>")
    return "\n".join(out)


__all__ = [
    "DEEP_INDEX_BANDS", "DRAWING_BANDS", "STATUS_LABELS", "DECISION_LABELS", "GALLERY_CSS",
    "fmt_num", "points_from_curve_row", "decision_of", "tile_from_curve_rows", "tile_svg",
    "tile_state_classes", "tile_title", "status_label", "gallery_html",
]

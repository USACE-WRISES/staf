"""Hand-built SVG reference-curve plots for the worksheet "Scoring method" panel.

Pure string builders (no matplotlib/Plotly/widget) so they embed as ``ui.HTML`` inside a collapsed
``<details>`` and re-render instantly when a what-if slider moves. Each plot shows the Good/Fair/Poor
regions as colored bands, a stepped reference curve at the index level of each rating (0.195 / 0.545 /
0.850), the site's value as a hollow marker, and — when a slider deviates — the explored value as a
filled marker. Colors match the app's band palette so the plot regions and the criteria swatches
agree. ``decision_html`` renders the categorical metrics (a category -> rating table) instead.
"""
from __future__ import annotations

import html
from typing import Optional

from . import config
from .methods import ScoringMethod

COLORS = {"Good": "#c8d9f2", "Fair": "#f5e7a6", "Poor": "#f5b5b5"}
_LINE = "#2f4b7c"
_TEXT = "#26324a"
_MUTED = "#697386"
_GRID = "#d7dce5"
_ACCENT = "#1769aa"
_RATINGS = ("Poor", "Fair", "Good")


def _e(x) -> str:
    return html.escape(str(x))


def _fmt(v, integer: bool = False) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if integer or f == int(f):
        return str(int(round(f)))
    return f"{f:g}"


def _x(value, lo, hi, left, right):
    if hi == lo:
        return left
    return left + (float(value) - lo) / (hi - lo) * (right - left)


def _y(index, top, bottom):
    return bottom - float(index) * (bottom - top)


def _svg_shell(title, content, height) -> str:
    return (
        f'<svg class="easi-method-svg" viewBox="0 0 720 {height}" role="img" '
        f'aria-label="{_e(title)}" xmlns="http://www.w3.org/2000/svg">'
        '<style>'
        '.easi-method-svg text{font-family:Instrument Sans,Segoe UI,sans-serif;fill:#26324a}'
        '.easi-method-svg .mv-small{font-size:11px;fill:#697386}'
        '.easi-method-svg .mv-label{font-size:12px;font-weight:600}'
        '.easi-method-svg .mv-marker{font-size:10px;font-weight:700}'
        '.easi-method-svg .mv-zone{font-size:11px;font-weight:600}'
        '</style>'
        f'{content}</svg>')


def _boundaries(bands, lo, hi):
    """Interior band edges strictly inside (lo, hi), ascending — the breakpoint verticals."""
    edges = set()
    for b in bands:
        for v in (b.lo, b.hi):
            if v is not None and lo < float(v) < hi:
                edges.add(round(float(v), 6))
    return sorted(edges)


def _panel(bands, lo, hi, left, right, top, bottom, *, breakpoints=(), x_label="", integer=False,
           show_index_labels=True):
    """One step panel: colored regions, index gridlines, the step curve, x-axis, breakpoints.
    Returns the SVG fragment (markers are added by the caller)."""
    ordered = sorted(bands, key=lambda b: (-1e9 if b.lo is None else b.lo))
    out = []
    # colored Good/Fair/Poor regions + zone label
    for b in ordered:
        start = lo if b.lo is None else max(lo, b.lo)
        end = hi if b.hi is None else min(hi, b.hi)
        if end <= start:
            continue
        x0, x1 = _x(start, lo, hi, left, right), _x(end, lo, hi, left, right)
        out.append(f'<rect x="{x0:.1f}" y="{top:.1f}" width="{x1 - x0:.1f}" '
                   f'height="{bottom - top:.1f}" fill="{COLORS[b.rating]}" opacity="0.30"/>')
        if x1 - x0 > 46:
            out.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{top + 14:.1f}" text-anchor="middle" '
                       f'class="mv-zone">{b.rating}</text>')
    # horizontal index gridlines (0.195 / 0.545 / 0.850)
    for rating in _RATINGS:
        idx = config.RATING_INDEX[rating]
        y = _y(idx, top, bottom)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
                   f'stroke="{_GRID}" stroke-width="1"/>')
        if show_index_labels:
            out.append(f'<text x="{left - 8}" y="{y + 3:.1f}" text-anchor="end" '
                       f'class="mv-small">{idx:.3f}</text>')
    # the step polyline
    pts = []
    prev_y = None
    for b in ordered:
        start = lo if b.lo is None else max(lo, b.lo)
        end = hi if b.hi is None else min(hi, b.hi)
        if end <= start:
            continue
        x0, x1 = _x(start, lo, hi, left, right), _x(end, lo, hi, left, right)
        y = _y(config.RATING_INDEX[b.rating], top, bottom)
        if prev_y is None:
            pts.append(f"M {x0:.1f} {y:.1f}")
        else:
            pts.append(f"L {x0:.1f} {prev_y:.1f} L {x0:.1f} {y:.1f}")
        pts.append(f"L {x1:.1f} {y:.1f}")
        prev_y = y
    out.append(f'<path d="{" ".join(pts)}" fill="none" stroke="{_LINE}" stroke-width="2.5" '
               f'stroke-linejoin="miter"/>')
    # x-axis baseline
    out.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" '
               f'stroke="{_TEXT}" stroke-width="1"/>')
    # breakpoint verticals + tick values + optional annotation labels
    edges = _boundaries(bands, lo, hi)
    for i, val in enumerate(edges):
        x = _x(val, lo, hi, left, right)
        out.append(f'<line x1="{x:.1f}" y1="{top + 18}" x2="{x:.1f}" y2="{bottom}" '
                   f'stroke="{_MUTED}" stroke-width="1" stroke-dasharray="3 3"/>'
                   f'<line x1="{x:.1f}" y1="{bottom}" x2="{x:.1f}" y2="{bottom + 5}" '
                   f'stroke="{_TEXT}"/>'
                   f'<text x="{x:.1f}" y="{bottom + 18:.1f}" text-anchor="middle" '
                   f'class="mv-small">{_fmt(val, integer)}</text>')
        if i < len(breakpoints) and breakpoints[i]:
            anchor = "start" if x < (left + right) / 2 else "end"
            dx = 4 if anchor == "start" else -4
            out.append(f'<text x="{x + dx:.1f}" y="{top + 30}" text-anchor="{anchor}" '
                       f'class="mv-small">{_e(breakpoints[i])}</text>')
    if x_label:
        out.append(f'<text x="{(left + right) / 2:.1f}" y="{bottom + 36:.1f}" '
                   f'text-anchor="middle" class="mv-label">{_e(x_label)}</text>')
    return "".join(out)


def _marker(value, rating, lo, hi, left, right, top, bottom, *, explored=False, label=""):
    if value is None or rating not in config.RATING_INDEX:
        return ""
    cx = _x(_clamp(float(value), lo, hi), lo, hi, left, right)
    cy = _y(config.RATING_INDEX[rating], top, bottom)
    if explored:
        dot = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="{_ACCENT}" stroke="#fff" stroke-width="2"/>'
    else:
        dot = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="#fff" stroke="{_LINE}" stroke-width="2.5"/>'
    txt = (f'<text x="{cx:.1f}" y="{cy - 10:.1f}" text-anchor="middle" class="mv-marker" '
           f'fill="{_ACCENT if explored else _LINE}">{_e(label)}</text>' if label else "")
    return dot + txt


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _domain_of(method: ScoringMethod, *values):
    lo, hi = method.domain or (0, 1)
    nums = [float(v) for v in values if v is not None]
    if nums:
        hi = max(hi, max(nums) * 1.05)
        lo = min(lo, min(nums))
    return lo, hi


def scalar_svg(method: ScoringMethod, site_value, site_rating,
               explored_value=None, explored_rating=None) -> str:
    """Single-axis step plot (scalar / combined / count)."""
    integer = method.mode == "count"
    lo, hi = _domain_of(method, site_value, explored_value)
    left, right, top, bottom = 92, 690, 34, 226
    x_label = method.value_label + (f" ({method.value_unit})" if method.value_unit else "")
    body = _panel(method.bands, lo, hi, left, right, top, bottom,
                  breakpoints=method.breakpoints, x_label=x_label, integer=integer)
    body += _marker(site_value, site_rating, lo, hi, left, right, top, bottom,
                    label=f"Site {_fmt(site_value, integer)}")
    changed = (explored_value is not None and site_value is not None
               and abs(float(explored_value) - float(site_value)) > 1e-9)
    if changed:
        body += _marker(explored_value, explored_rating, lo, hi, left, right, top, bottom,
                        explored=True, label=f"Explore {_fmt(explored_value, integer)}")
    body += (f'<text x="16" y="130" transform="rotate(-90 16 130)" text-anchor="middle" '
             f'class="mv-label">Index value</text>')
    return _svg_shell(method.value_label, body, 272)


def count_svg(method: ScoringMethod, site_value, site_rating,
              explored_value=None, explored_rating=None) -> str:
    return scalar_svg(method, site_value, site_rating, explored_value, explored_rating)


def worst_svg(method: ScoringMethod, site_inputs: dict, explored_inputs: Optional[dict] = None,
              governing: Optional[str] = None) -> str:
    """Stacked mini step-panels, one per indicator; the more-limiting (worse) one is flagged."""
    left, right = 150, 690
    panel_h = 96
    gap = 30
    top0 = 30
    n = len(method.per_input)
    height = top0 + n * (panel_h + gap)
    body = []
    for i, (key, rate_fn, bands) in enumerate(method.per_input):
        top = top0 + i * (panel_h + gap)
        bottom = top + panel_h
        lo = min(b.lo for b in bands if b.lo is not None)
        hi = max(b.hi for b in bands if b.hi is not None)
        sv = (site_inputs or {}).get(key)
        ev = (explored_inputs or {}).get(key) if explored_inputs else None
        inp = next((mi for mi in method.inputs if mi.key == key), None)
        label = inp.label if inp else key
        unit = f" ({inp.unit})" if inp and inp.unit else ""
        gov = governing == key
        body.append(f'<text x="16" y="{top - 6:.1f}" class="mv-label">{_e(label)}{_e(unit)}'
                    f'{" — governs" if gov else ""}</text>')
        body.append(_panel(bands, lo, hi, left, right, top, bottom, x_label="",
                           show_index_labels=False))
        sr = rate_fn(sv) if sv is not None else None
        body.append(_marker(sv, sr, lo, hi, left, right, top, bottom,
                            label=f"{_fmt(sv)}"))
        if ev is not None and (sv is None or abs(float(ev) - float(sv)) > 1e-9):
            er = rate_fn(ev)
            body.append(_marker(ev, er, lo, hi, left, right, top, bottom, explored=True,
                               label=f"{_fmt(ev)}"))
    return _svg_shell("Indicator scoring", "".join(body), height)


def decision_html(method: ScoringMethod, site_rating: Optional[str]) -> str:
    """Categorical decision table (category -> rating), highlighting the site's rating."""
    rows = []
    for d in method.decisions:
        on = d.rating == site_rating
        dot = f'<span class="easi-tip-dot {d.rating.lower()}"></span>'
        here = '<span class="easi-method-decide-here">this reach</span>' if on else ""
        rows.append(f'<div class="easi-method-decide-row{" on" if on else ""}">{dot}'
                    f'<span class="easi-method-decide-cat">{_e(d.label)}</span>'
                    f'<span class="easi-method-decide-rate">{_e(d.rating)}</span>{here}</div>')
    return f'<div class="easi-method-decide">{"".join(rows)}</div>'

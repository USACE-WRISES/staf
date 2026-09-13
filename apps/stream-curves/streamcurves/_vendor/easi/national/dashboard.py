"""The condition dashboard of the Nationwide screening: box-and-whisker
plots of the Ecosystem Condition Index and its three sub-indices, the
rating shares of the 20 functions, a function sensitivity table and a
state comparison, all drawn from the published ``stats.json`` (the
builder's staging statistics by state).

Pure builders: htmltools tags and inline SVG whose x coordinates are
percentages of the plot column, so the plots stretch with the pane while
the text keeps its size and nothing new is loaded. Function scores are
three-valued (3, 8 and 13), so a function's picture is its rating shares;
the indices are continuous, so they get real box plots. Copy avoids em
dashes.
"""
from __future__ import annotations

import csv
import html
import io
import math
from typing import Iterable, Optional

from htmltools import HTML, Tag, tags

from .. import config

NATIONAL = "US"
NATIONAL_LABEL = "All published reaches"
#: a state below this share of its reaches screened is drawn muted: only
#: the HUC8s that spill over from a neighbour are in
COVERAGE_FLOOR = 0.5
#: fewer reaches than this and a distribution is a sketch
MIN_N = 200
#: spread below this reads "low" (about nine reaches in ten share one rating)
LOW_SPREAD = 0.25

INDEX_NAMES = (("eci", "Ecosystem Condition Index"), ("physical", "Physical"),
               ("chemical", "Chemical"), ("biological", "Biological"))
BAND_COLORS = ("#f5b5b5", "#f5e7a6", "#c8d9f2")          # Non-Functioning, At-Risk, Functioning
BAND_LABELS = ("Non-Functioning", "At-Risk", "Functioning")
UNRATED_COLOR = "#d7dce5"
_LINE = "#2f4b7c"
_MUTED = "#697386"


def _e(x) -> str:
    return html.escape(str(x))


# ----------------------------------------------------------------- numbers
def spread(bands) -> Optional[float]:
    """How evenly the rated reaches split over the three ratings, 0 (one
    rating everywhere) to 1 (an even split): the Gini-Simpson index of the
    shares scaled by its three-class maximum of two thirds."""
    total = float(sum(bands or []))
    if total <= 0:
        return None
    value = 1.0 - sum((b / total) ** 2 for b in bands)
    return max(0.0, min(1.0, value / (2.0 / 3.0)))


def hist_quantile(hist, q: float) -> Optional[float]:
    """The ``q`` (0 to 100) quantile of a histogram over the scores 0..len-1
    by the nearest rank, or None for an empty one."""
    n = sum(hist)
    if n <= 0:
        return None
    rank = max(1, int(math.ceil(q / 100.0 * n)))
    seen = 0
    for score, count in enumerate(hist):
        seen += count
        if seen >= rank:
            return float(score)
    return float(len(hist) - 1)


def shares(bands, unrated=0) -> tuple[float, float, float, float]:
    """``(NF, AR, F, unrated)`` as fractions of every reach in the group."""
    total = float(sum(bands) + unrated)
    if total <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(b / total for b in bands) + (unrated / total,)


def _pct(fraction, digits: int = 0) -> str:
    if fraction is None:
        return ""
    value = 100.0 * fraction
    if digits == 0 and 0 < value < 1:
        return f"{value:.1f}%"
    return f"{value:.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _int(value) -> str:
    return "" if value is None else f"{int(value):,}"


# ------------------------------------------------------------------ choices
def scope_choices(stats: dict) -> dict[str, str]:
    """``value -> label`` for the scope select: everything first, then the
    states with screened reaches by name, each with its count and coverage."""
    out = {NATIONAL: f"{NATIONAL_LABEL} ({_int((stats.get('groups') or {}).get(NATIONAL, {}).get('n'))})"}
    states = stats.get("states") or {}
    for abbr, info in sorted(states.items(), key=lambda kv: kv[1].get("name") or kv[0]):
        n = info.get("n_scored") or 0
        if not n:
            continue
        cov = info.get("coverage")
        label = f"{info.get('name') or abbr} ({_int(n)} reaches"
        label += f", {_pct(cov)} screened)" if cov is not None else ")"
        out[abbr] = label
    return out


def measure_choices(stats: dict) -> dict[str, dict[str, str]]:
    """Grouped choices for the compare select: the indices, then the
    functions by category."""
    out: dict[str, dict[str, str]] = {"Condition indices": dict(INDEX_NAMES)}
    for fn in (stats.get("measures") or {}).get("functions") or []:
        out.setdefault(fn.get("category") or "Functions", {})[fn["id"]] = fn["name"]
    return out


def scope_name(stats: dict, scope: str) -> str:
    if scope == NATIONAL:
        return NATIONAL_LABEL
    return ((stats.get("states") or {}).get(scope) or {}).get("name") or scope


def group(stats: dict, scope: str) -> Optional[dict]:
    return (stats.get("groups") or {}).get(scope)


def _functions(stats: dict) -> list[dict]:
    return list((stats.get("measures") or {}).get("functions") or [])


def _edges(stats: dict) -> tuple[float, float]:
    edges = (stats.get("measures") or {}).get("index_edges") or [config.INDEX_BANDS[0][0], config.INDEX_BANDS[1][0]]
    return float(edges[0]), float(edges[1])


def _score_edges(stats: dict) -> tuple[float, float]:
    edges = (stats.get("measures") or {}).get("score_edges") or [config.FUNCTION_SCORE_BANDS[0][0],
                                                                  config.FUNCTION_SCORE_BANDS[1][0]]
    # the bands sit between the integer scores, so shade to the midpoints
    return float(edges[0]) + 0.5, float(edges[1]) + 0.5


# --------------------------------------------------------------- box plots
def _x(value, lo, hi) -> float:
    if value is None or hi == lo:
        return 0.0
    return max(0.0, min(100.0, (float(value) - lo) / (hi - lo) * 100.0))


def _band_rects(lo, hi, edges, height) -> str:
    a, b = edges
    stops = ((lo, a, BAND_COLORS[0]), (a, b, BAND_COLORS[1]), (b, hi, BAND_COLORS[2]))
    parts = []
    for start, end, color in stops:
        x0, x1 = _x(start, lo, hi), _x(end, lo, hi)
        if x1 > x0:
            parts.append(f'<rect x="{x0:.2f}%" y="0" width="{x1 - x0:.2f}%" height="{height}" '
                         f'fill="{color}" fill-opacity="0.45"/>')
    for edge in edges:
        x = _x(edge, lo, hi)
        parts.append(f'<line x1="{x:.2f}%" x2="{x:.2f}%" y1="0" y2="{height}" stroke="#b8c0cc" '
                     'stroke-width="1" stroke-dasharray="3 3"/>')
    return "".join(parts)


def box_svg(s: dict, *, lo: float, hi: float, edges, tip: str, height: int = 30) -> HTML:
    """One row's plot: the shaded bands, whiskers p5 to p95, the p25 to p75
    box, the median line and the mean circle, in percent coordinates."""
    y = height / 2.0
    x5, x25, x50, x75, x95 = (_x(s.get(k), lo, hi) for k in ("p5", "p25", "p50", "p75", "p95"))
    parts = [f'<svg class="easi-dash-box-svg" width="100%" height="{height}" overflow="visible" '
             f'role="img" aria-label="{_e(tip)}"><title>{_e(tip)}</title>',
             _band_rects(lo, hi, edges, height)]
    if s.get("p50") is not None:
        parts.append(f'<line class="easi-dash-whisker" x1="{x5:.2f}%" x2="{x95:.2f}%" y1="{y}" y2="{y}"/>')
        for x in (x5, x95):
            parts.append(f'<line class="easi-dash-whisker" x1="{x:.2f}%" x2="{x:.2f}%" '
                         f'y1="{y - 4}" y2="{y + 4}"/>')
        width = max(x75 - x25, 0.35)
        parts.append(f'<rect class="easi-dash-boxrect" x="{x25:.2f}%" y="{y - 7}" width="{width:.2f}%" height="14"/>')
        parts.append(f'<line class="easi-dash-median" x1="{x50:.2f}%" x2="{x50:.2f}%" y1="{y - 8}" y2="{y + 8}"/>')
        if s.get("mean") is not None:
            parts.append(f'<circle class="easi-dash-mean" cx="{_x(s["mean"], lo, hi):.2f}%" cy="{y}" r="3"/>')
    parts.append("</svg>")
    return HTML("".join(parts))


def axis_svg(lo: float, hi: float, ticks: Iterable[float], fmt: str = "{:.1f}") -> HTML:
    parts = ['<svg class="easi-dash-axis-svg" width="100%" height="18" overflow="visible" aria-hidden="true">']
    for tick in ticks:
        x = _x(tick, lo, hi)
        anchor = "start" if x < 1 else ("end" if x > 99 else "middle")
        parts.append(f'<line x1="{x:.2f}%" x2="{x:.2f}%" y1="0" y2="4" stroke="#b8c0cc"/>')
        parts.append(f'<text x="{x:.2f}%" y="15" text-anchor="{anchor}">{_e(fmt.format(tick))}</text>')
    parts.append("</svg>")
    return HTML("".join(parts))


def _index_tip(name: str, s: dict, digits: int = 3) -> str:
    bands = s.get("bands") or [0, 0, 0]
    total = float(sum(bands)) or 1.0
    return (f"{name}: n {_int(s.get('n'))}, median {_num(s.get('p50'), digits)}, mean {_num(s.get('mean'), digits)}, "
            f"box p25 {_num(s.get('p25'), digits)} to p75 {_num(s.get('p75'), digits)}, "
            f"whiskers p5 {_num(s.get('p5'), digits)} to p95 {_num(s.get('p95'), digits)}, "
            f"range {_num(s.get('min'), digits)} to {_num(s.get('max'), digits)}. "
            f"Functioning {_pct(bands[2] / total, 1)}, At-Risk {_pct(bands[1] / total, 1)}, "
            f"Non-Functioning {_pct(bands[0] / total, 1)}.")


def box_rows(rows: list[dict], *, lo: float, hi: float, edges, ticks, fmt: str = "{:.1f}",
             digits: int = 2, label_class: str = "") -> Tag:
    """A box-plot table: one grid row per entry (label, plot, median, n) and
    the axis under them. Each row is ``{label, sub, stats, tip, muted}``."""
    out = []
    for row in rows:
        s = row.get("stats") or {}
        classes = "easi-dash-row easi-dash-box-row" + (" muted" if row.get("muted") else "")
        label = tags.div(tags.span(row["label"], class_="easi-dash-label-main"),
                         (tags.span(row["sub"], class_="easi-dash-label-sub") if row.get("sub") else None),
                         class_="easi-dash-label " + label_class)
        out.append(tags.div(label,
                            tags.div(box_svg(s, lo=lo, hi=hi, edges=edges, tip=row.get("tip") or row["label"]),
                                     class_="easi-dash-plot"),
                            tags.div(_num(s.get("p50"), digits), class_="easi-dash-val", title="median"),
                            tags.div(_int(s.get("n")), class_="easi-dash-n", title="reaches"),
                            class_=classes))
    out.append(tags.div(tags.div(class_="easi-dash-label"), tags.div(axis_svg(lo, hi, ticks, fmt), class_="easi-dash-plot"),
                        tags.div("median", class_="easi-dash-val easi-dash-colhead"),
                        tags.div("n", class_="easi-dash-n easi-dash-colhead"),
                        class_="easi-dash-row easi-dash-axis-row"))
    return tags.div(*out, class_="easi-dash-box")


# --------------------------------------------------------------- share bars
def stack_bar(bands, unrated: int, *, tip: str) -> Tag:
    """A stacked bar of the rating shares (NF, AR, F, then not rated)."""
    nf, ar, f, un = shares(bands, unrated)
    segments = []
    for share, color, label in ((nf, BAND_COLORS[0], BAND_LABELS[0]), (ar, BAND_COLORS[1], BAND_LABELS[1]),
                                (f, BAND_COLORS[2], BAND_LABELS[2]), (un, UNRATED_COLOR, "Not rated")):
        if share <= 0:
            continue
        text = _pct(share) if share >= 0.08 else ""
        segments.append(tags.span(text, class_="easi-dash-seg", title=f"{label} {_pct(share, 1)}",
                                  style=f"width:{share * 100:.2f}%;background:{color};"))
    return tags.div(*segments, class_="easi-dash-stack", title=tip)


def _function_tip(name: str, f: dict) -> str:
    bands = f.get("bands") or [0, 0, 0]
    nf, ar, fu, un = shares(bands, f.get("unrated") or 0)
    tiers = f.get("tiers") or {}
    proxy = tiers.get("screening-proxy", 0)
    rated = f.get("rated") or 0
    return (f"{name}: {_int(rated)} rated. Functioning {_pct(fu, 1)}, At-Risk {_pct(ar, 1)}, "
            f"Non-Functioning {_pct(nf, 1)}, not rated {_pct(un, 1)}. Mean score {_num(f.get('mean'), 1)}. "
            f"Spread {_num(spread(bands), 2)}. From a screening proxy: {_pct(proxy / rated if rated else None, 0)}.")


def share_rows(rows: list[dict]) -> Tag:
    """Stacked share bars, one grid row per entry ``{label, sub, function, tip, muted}``."""
    out = []
    for row in rows:
        f = row.get("function") or {}
        classes = "easi-dash-row easi-dash-share-row" + (" muted" if row.get("muted") else "")
        label = tags.div(tags.span(row["label"], class_="easi-dash-label-main"),
                         (tags.span(row["sub"], class_="easi-dash-label-sub") if row.get("sub") else None),
                         class_="easi-dash-label")
        out.append(tags.div(label,
                            tags.div(stack_bar(f.get("bands") or [0, 0, 0], f.get("unrated") or 0,
                                               tip=row.get("tip") or row["label"]), class_="easi-dash-plot"),
                            tags.div(_num(f.get("mean"), 1), class_="easi-dash-val", title="mean score, 0 to 15"),
                            class_=classes))
    return tags.div(*out, class_="easi-dash-shares")


def function_box_rows(fns: list[dict], g: dict, stats: dict) -> Tag:
    """The functions as box plots of their three-valued scores (the toggle's
    other view): quantiles by rank from the score histogram."""
    rows = []
    for fn in fns:
        f = (g.get("functions") or {}).get(fn["id"]) or {}
        hist = f.get("hist") or []
        s = {k: hist_quantile(hist, q) for k, q in (("p5", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))}
        s["mean"] = f.get("mean")
        s["n"] = f.get("rated")
        rows.append({"label": fn["name"], "stats": s, "tip": _function_tip(fn["name"], f)})
    edges = _score_edges(stats)
    return box_rows(rows, lo=0.0, hi=15.0, edges=edges, ticks=(0, 5, 10, 15), fmt="{:.0f}", digits=0)


# -------------------------------------------------------------------- cards
def legend(items) -> Tag:
    return tags.div(*[tags.span(tags.span(class_="easi-leg-sw", style=f"background:{color};"), text,
                                class_="easi-leg-item") for color, text in items],
                    class_="easi-plot-legend")


def _box_legend() -> Tag:
    return tags.div(
        tags.span(tags.span(class_="easi-dash-key-box"), "box p25 to p75, line at the median", class_="easi-leg-item"),
        tags.span(tags.span(class_="easi-dash-key-whisker"), "whiskers p5 to p95", class_="easi-leg-item"),
        tags.span(tags.span(class_="easi-dash-key-mean"), "mean", class_="easi-leg-item"),
        *[tags.span(tags.span(class_="easi-leg-sw", style=f"background:{color};opacity:.7"), text,
                    class_="easi-leg-item")
          for color, text in ((BAND_COLORS[2], "Functioning 0.70 to 1.00"), (BAND_COLORS[1], "At-Risk 0.40 to 0.69"),
                              (BAND_COLORS[0], "Non-Functioning 0.00 to 0.39"))],
        class_="easi-plot-legend")


def card(title: str, *body, note: Optional[str] = None, controls=None) -> Tag:
    head = tags.div(tags.div(title, class_="easi-plot-title"), controls, class_="easi-dash-card-head")
    return tags.div(head, (tags.div(note, class_="easi-dash-note") if note else None), *body,
                    class_="easi-dash-card")


def indices_card(stats: dict, scope: str) -> Tag:
    g = group(stats, scope) or {}
    lo, hi = 0.0, 1.0
    rows = []
    for key, name in INDEX_NAMES:
        s = (g.get("indices") or {}).get(key)
        if not s:
            rows.append({"label": name, "stats": {}, "tip": f"{name}: not computed"})
            continue
        rows.append({"label": name, "stats": s, "tip": _index_tip(name, s)})
    note = None
    if (g.get("n") or 0) < MIN_N:
        note = f"Only {_int(g.get('n'))} reaches: read the distribution with care."
    return card("Condition indices", _box_legend(),
                box_rows(rows, lo=lo, hi=hi, edges=_edges(stats), ticks=(0, 0.2, 0.4, 0.6, 0.8, 1.0)),
                index_band_table(g),
                note=note)


def index_band_table(g: dict) -> Tag:
    """Under the box plots: each index's share of reaches by band, with the
    mean and the standard deviation (what a box plot does not print)."""
    head = tags.tr(tags.th("Share of reaches"), tags.th("Functioning"), tags.th("At-Risk"),
                   tags.th("Non-Functioning"), tags.th("Mean"), tags.th("SD"))
    body = []
    for key, name in INDEX_NAMES:
        s = (g.get("indices") or {}).get(key)
        if not s:
            body.append(tags.tr(tags.td(name), tags.td("", colspan="5", class_="easi-dash-dim")))
            continue
        nf, ar, f, _un = shares(s.get("bands") or [0, 0, 0])
        body.append(tags.tr(tags.td(name),
                            tags.td(_pct(f, 1), style=f"background:{BAND_COLORS[2]}66"),
                            tags.td(_pct(ar, 1), style=f"background:{BAND_COLORS[1]}66"),
                            tags.td(_pct(nf, 1), style=f"background:{BAND_COLORS[0]}66"),
                            tags.td(_num(s.get("mean"), 2)), tags.td(_num(s.get("sd"), 2), class_="easi-dash-dim")))
    return tags.div(tags.table(tags.thead(head), tags.tbody(*body), class_="easi-dash-table easi-dash-mini"),
                    class_="easi-dash-table-wrap easi-dash-mini-wrap")


def functions_card(stats: dict, scope: str, mode: str = "shares", controls=None) -> Tag:
    g = group(stats, scope) or {}
    blocks = []
    by_category: dict[str, list] = {}
    for fn in _functions(stats):
        by_category.setdefault(fn.get("category") or "Functions", []).append(fn)
    for category, fns in by_category.items():
        if mode == "boxes":
            body = function_box_rows(fns, g, stats)
        else:
            rows = []
            for fn in fns:
                f = (g.get("functions") or {}).get(fn["id"]) or {}
                rows.append({"label": fn["name"], "function": f, "tip": _function_tip(fn["name"], f)})
            body = share_rows(rows)
        blocks.append(tags.div(tags.div(category, class_="easi-fn-group"), body, class_="easi-fn-block"))
    if mode == "boxes":
        key = tags.div(
            tags.span(tags.span(class_="easi-dash-key-box"), "box p25 to p75, line at the median", class_="easi-leg-item"),
            tags.span(tags.span(class_="easi-dash-key-whisker"), "whiskers p5 to p95", class_="easi-leg-item"),
            tags.span(tags.span(class_="easi-dash-key-mean"), "mean", class_="easi-leg-item"),
            class_="easi-plot-legend")
        note = ("Scores take three values (3, 8 and 13: the rating times 15), so a box "
                "collapses onto them; the rating shares are the fuller picture.")
    else:
        key = legend([(BAND_COLORS[2], "Functioning 11 to 15"), (BAND_COLORS[1], "At-Risk 6 to 10"),
                      (BAND_COLORS[0], "Non-Functioning 0 to 5"), (UNRATED_COLOR, "Not rated")])
        note = None
    return card("Function scores", key, *blocks, note=note, controls=controls)


def across_states(stats: dict, fid: str) -> Optional[tuple[float, float, int]]:
    """``(min, max, states)`` of the share Functioning over the states with
    at least half their reaches screened and ``MIN_N`` reaches, or None
    until two such states exist."""
    values = []
    for abbr, info in (stats.get("states") or {}).items():
        if (info.get("coverage") or 0) < COVERAGE_FLOOR or (info.get("n_scored") or 0) < MIN_N:
            continue
        f = ((group(stats, abbr) or {}).get("functions") or {}).get(fid) or {}
        bands = f.get("bands") or [0, 0, 0]
        if sum(bands) <= 0:
            continue
        values.append(bands[2] / float(sum(bands)))
    if len(values) < 2:
        return None
    return min(values), max(values), len(values)


def sensitivity_rows(stats: dict, scope: str) -> list[dict]:
    """One row per function, least spread first."""
    g = group(stats, scope) or {}
    rows = []
    for fn in _functions(stats):
        f = (g.get("functions") or {}).get(fn["id"]) or {}
        bands = f.get("bands") or [0, 0, 0]
        nf, ar, fu, un = shares(bands, f.get("unrated") or 0)
        rated = f.get("rated") or 0
        proxy = (f.get("tiers") or {}).get("screening-proxy", 0)
        rows.append({"id": fn["id"], "name": fn["name"], "category": fn.get("category") or "",
                     "nf": nf, "ar": ar, "f": fu, "unrated": un,
                     "proxy": (proxy / rated) if rated else None, "spread": spread(bands),
                     "across": across_states(stats, fn["id"]), "rated": rated})
    rows.sort(key=lambda r: (r["spread"] is None, r["spread"] if r["spread"] is not None else 0.0, r["name"]))
    return rows


def sensitivity_card(stats: dict, scope: str) -> Tag:
    rows = sensitivity_rows(stats, scope)
    head = tags.tr(*[tags.th(h, title=t) for h, t in (
        ("Function", ""), ("Category", ""), ("NF", "share Non-Functioning"), ("AR", "share At-Risk"),
        ("F", "share Functioning"), ("Not rated", "share of reaches without a rating"),
        ("Proxy", "share of rated reaches scored from a screening proxy"),
        ("Spread", "0: one rating everywhere. 1: the three ratings equally common."),
        ("Across states", "share Functioning, lowest to highest state, over states with at least half "
                          "their reaches screened"))])
    body = []
    for r in rows:
        chip = None
        if r["spread"] is not None and r["spread"] < LOW_SPREAD:
            chip = tags.span("low", class_="easi-dash-chip", title="about nine reaches in ten share one rating")
        across = ""
        if r["across"]:
            lo, hi, k = r["across"]
            across = f"{_pct(lo)} to {_pct(hi)} ({k} states)"
        body.append(tags.tr(
            tags.td(r["name"]), tags.td(r["category"], class_="easi-dash-dim"),
            tags.td(_pct(r["nf"], 1)), tags.td(_pct(r["ar"], 1)), tags.td(_pct(r["f"], 1)),
            tags.td(_pct(r["unrated"], 1), class_="easi-dash-dim"), tags.td(_pct(r["proxy"], 0), class_="easi-dash-dim"),
            tags.td(_num(r["spread"], 2), chip, class_="easi-dash-spread"),
            tags.td(across, class_="easi-dash-dim")))
    note = ("A function whose reaches nearly all share one rating separates little; sorted least spread first. "
            "Shares are of every reach in the scope; Proxy is of the rated reaches.")
    return card("Function sensitivity", tags.div(tags.table(tags.thead(head), tags.tbody(*body), class_="easi-dash-table"),
                                                 class_="easi-dash-table-wrap"), note=note)


def compare_card(stats: dict, measure: str) -> Tag:
    """One row per state (everything published first) for one measure:
    box plots for an index, share bars for a function."""
    states = stats.get("states") or {}
    index_names = dict(INDEX_NAMES)
    fn_by_id = {fn["id"]: fn for fn in _functions(stats)}
    entries = []
    for abbr, info in states.items():
        if not info.get("n_scored"):
            continue
        entries.append((abbr, info))

    def label_of(abbr, info):
        if abbr == NATIONAL:
            return NATIONAL_LABEL, None
        cov = info.get("coverage")
        sub = f"{_int(info.get('n_scored'))} reaches" + (f", {_pct(cov)} screened" if cov is not None else "")
        return (info.get("name") or abbr), sub

    if measure in index_names:
        name = index_names[measure]
        rows = []
        for abbr, info in [(NATIONAL, {})] + entries:
            s = ((group(stats, abbr) or {}).get("indices") or {}).get(measure) or {}
            label, sub = label_of(abbr, info)
            rows.append({"label": label, "sub": sub, "stats": s, "tip": _index_tip(f"{label}, {name}", s),
                         "muted": abbr != NATIONAL and (info.get("coverage") or 0) < COVERAGE_FLOOR,
                         "sort": -(s.get("p50") if s.get("p50") is not None else -1), "pin": abbr != NATIONAL})
        rows.sort(key=lambda r: (r["pin"], r["sort"]))
        body = box_rows(rows, lo=0.0, hi=1.0, edges=_edges(stats), ticks=(0, 0.2, 0.4, 0.6, 0.8, 1.0),
                        label_class="wide")
        key = _box_legend()
    else:
        fn = fn_by_id.get(measure) or {"name": measure}
        name = fn["name"]
        rows = []
        for abbr, info in [(NATIONAL, {})] + entries:
            f = ((group(stats, abbr) or {}).get("functions") or {}).get(measure) or {}
            label, sub = label_of(abbr, info)
            bands = f.get("bands") or [0, 0, 0]
            share_f = bands[2] / float(sum(bands)) if sum(bands) else -1.0
            rows.append({"label": label, "sub": sub, "function": f, "tip": _function_tip(f"{label}, {name}", f),
                         "muted": abbr != NATIONAL and (info.get("coverage") or 0) < COVERAGE_FLOOR,
                         "sort": -share_f, "pin": abbr != NATIONAL})
        rows.sort(key=lambda r: (r["pin"], r["sort"]))
        body = share_rows(rows)
        key = legend([(BAND_COLORS[2], "Functioning"), (BAND_COLORS[1], "At-Risk"),
                      (BAND_COLORS[0], "Non-Functioning"), (UNRATED_COLOR, "Not rated")])
    muted = [abbr for abbr, info in entries if (info.get("coverage") or 0) < COVERAGE_FLOOR]
    note = None
    if muted:
        note = (f"Muted rows have under half their reaches screened ({', '.join(sorted(muted))}): only HUC8s "
                "spilling over from a neighboring state are in, so they do not describe the state.")
    if len(entries) < 2:
        note = ((note + " ") if note else "") + "More states appear here as they are published."
    return card(f"{name} by state", key, tags.div(body, class_="easi-dash-compare"), note=note)


def summary_strip(stats: dict, scope: str) -> Tag:
    g = group(stats, scope) or {}
    n = g.get("n") or 0
    states = {abbr: info for abbr, info in (stats.get("states") or {}).items() if info.get("n_scored")}
    covered = [abbr for abbr, info in states.items() if (info.get("coverage") or 0) >= COVERAGE_FLOOR]
    items = [(_int(n), "reaches screened")]
    if scope == NATIONAL:
        items.append((str(len(covered)), "state" + ("" if len(covered) == 1 else "s") + " at least half screened"))
        if len(states) > len(covered):
            items.append((str(len(states) - len(covered)), "border-only"))
    else:
        info = states.get(scope) or {}
        if info.get("coverage") is not None:
            items.append((_pct(info.get("coverage")), f"of {_int(info.get('n_total'))} reaches screened"))
    if n:
        items.append((_pct((g.get("tier2") or 0) / n), "tier 2 (cross-sections from 3DEP)"))
        items.append((_pct((g.get("provisional") or 0) / n), "provisional coverage"))
    updated = str(stats.get("generated") or "")[:10]
    if updated:
        items.append((updated, "statistics generated"))
    return tags.div(*[tags.div(tags.span(value, class_="easi-dash-stat-value"),
                               tags.span(label, class_="easi-dash-stat-label"), class_="easi-dash-stat")
                      for value, label in items], class_="easi-dash-strip")


def footer_note(stats: dict) -> Tag:
    return tags.div(
        tags.p("Distributions of the precomputed, unreviewed EASI screening over the reaches published so far. "
               "A reach belongs to the state containing the midpoint of its NHDPlus V2 flowline; a state's "
               "coverage is the share of its reaches screened. Box plots span the 5th to the 95th percentile. "
               f"Groups under {MIN_N} reaches are sketches. Reaches without a rating for a function are "
               "counted as not rated, never as a low score."),
        class_="easi-dash-foot")


def unavailable(summary: Optional[dict]) -> Tag:
    if summary is None:
        text = "Reading the national dataset…"
    elif not summary.get("available"):
        text = "The national dataset is not reachable right now."
    else:
        text = "Statistics are not published for this dataset yet."
    return tags.div(text, class_="easi-dash-empty")


# ------------------------------------------------------------------- export
def export_csv(stats: dict) -> str:
    """Every scope's statistics in long form: scope, name, kind, measure, statistic, value."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["scope", "scope_name", "kind", "measure", "statistic", "value"])
    names = dict(INDEX_NAMES)
    fn_names = {fn["id"]: fn["name"] for fn in _functions(stats)}
    for scope, g in sorted((stats.get("groups") or {}).items(), key=lambda kv: (kv[0] != NATIONAL, kv[0])):
        sname = scope_name(stats, scope)
        writer.writerow([scope, sname, "scope", "reaches", "n", g.get("n")])
        info = (stats.get("states") or {}).get(scope) or {}
        if info.get("coverage") is not None:
            writer.writerow([scope, sname, "scope", "reaches", "coverage", info.get("coverage")])
        for key, s in (g.get("indices") or {}).items():
            if not s:
                continue
            for stat in ("n", "mean", "sd", "min", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "max"):
                writer.writerow([scope, sname, "index", names.get(key, key), stat, s.get(stat)])
            nf, ar, f, _un = shares(s.get("bands") or [0, 0, 0])
            for stat, value in (("share_non_functioning", nf), ("share_at_risk", ar), ("share_functioning", f)):
                writer.writerow([scope, sname, "index", names.get(key, key), stat, round(value, 4)])
        for fid, f in (g.get("functions") or {}).items():
            name = fn_names.get(fid, fid)
            bands = f.get("bands") or [0, 0, 0]
            nf, ar, fu, un = shares(bands, f.get("unrated") or 0)
            rows = [("rated", f.get("rated")), ("unrated", f.get("unrated")), ("mean_score", f.get("mean")),
                    ("share_non_functioning", round(nf, 4)), ("share_at_risk", round(ar, 4)),
                    ("share_functioning", round(fu, 4)), ("share_not_rated", round(un, 4)),
                    ("spread", None if spread(bands) is None else round(spread(bands), 4))]
            rows += [(f"tier_{tier}", count) for tier, count in sorted((f.get("tiers") or {}).items())]
            for stat, value in rows:
                writer.writerow([scope, sname, "function", name, stat, value])
    return buffer.getvalue()

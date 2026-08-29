"""DEEP — Detailed Evaluation of Ecosystem Processes (Shiny for Python, Core).

The detailed-tier STAF assessment tool, sibling to EASI (screening) and SFARI
(rapid). Workflow: zoom in until NHD stream vectors appear, click a stream to
snap a point, delineate the watershed + upstream reach, pick (or upload) a
detailed assessment definition, then enter each metric's measured value. Scores
are computed automatically from the assessment's reference curves and roll up to
Physical / Chemical / Biological outcome sub-indices and an Ecosystem Condition
Index.

Identify + Basin + the map/delineation engine are reused from SFARI/EASI. The
scoring rollup is DEEP's ``scoring`` (identical to SFARI's); the new front half
is curve-based (``deep.curves``) rather than Likert judgment.
"""
from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("HYRIVER_CACHE_NAME",
                      os.path.join(tempfile.gettempdir(), "deep_hyriver.sqlite"))
os.environ.setdefault("HYRIVER_CACHE_EXPIRE", str(7 * 24 * 3600))

import anyio  # noqa: E402
from shiny import App, reactive, render, ui  # noqa: E402

from deep import (assessments, config, curves, delineation, measure,  # noqa: E402
                  pipeline, report, scoring, session)
from deep.datasources import flowlines  # noqa: E402
from deep.datasources.geocode import geocode_address  # noqa: E402
from deep.metrics import computed as _computed  # noqa: E402
from deep.pipeline import DEFAULT_REACH_FT  # noqa: E402

try:
    from ipyleaflet import GeoJSON, LayersControl, Map, Marker, ScaleControl, TileLayer
    from ipywidgets import Layout
    from shinywidgets import output_widget, reactive_read, render_widget
    _HAS_MAP = True
except Exception:  # pragma: no cover
    _HAS_MAP = False

WATERSHED_STYLE = {"color": "#caa700", "weight": 1, "fillColor": "#fdf24a", "fillOpacity": 0.40}
REACH_STYLE = {"color": "#d6453d", "weight": 4}
FLOWLINE_STYLE = {"color": "#1f6feb", "weight": 2, "opacity": 0.9}

USGS_TOPO_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
USGS_HYDRO_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
USGS_ATTR = "USGS The National Map"
FLOW_ZOOM = 14
SNAP_TOL_FT = 150.0

# Four steps, the same shape EASI and SFARI use. Nothing sits between delineating and
# measuring: the assessment follows from the point, so Basin resolves it and reports it
# rather than asking for it.
STEP_IDENTIFY, STEP_BASIN, STEP_MEASURE, STEP_REPORT = \
    "identify", "basin", "measure", "report"
STEP_LABELS = [(STEP_IDENTIFY, "Identify"), (STEP_BASIN, "Basin"),
               (STEP_MEASURE, "Assessment"), (STEP_REPORT, "Report")]

CATEGORY_ORDER = list(config.CATEGORY_ORDER)
_FNF_SHORT = {"Functioning": "F", "Functioning-at-Risk": "AR", "Non-Functioning": "NF"}

# Detailed metricIds DEEP can desktop-compute (Phase 3).
_COMPUTED_IDS = _computed.computable_ids()


# --------------------------------------------------------------------------- #
# Small view helpers
# --------------------------------------------------------------------------- #
def _bar(label, value, color, *, vmax=1.0, fmt="{:.2f}", indent=False):
    pct = max(0.0, min(100.0, (value / vmax) * 100)) if vmax else 0.0
    cls = "easi-bar-row indent" if indent else "easi-bar-row"
    return ui.div(ui.div(label, class_="easi-bar-label"),
                  ui.div(ui.div(class_="easi-bar-fill", style=f"width:{pct:.0f}%;background:{color};"),
                         class_="easi-bar-track"),
                  ui.div(fmt.format(value), class_="easi-bar-val"), class_=cls)


def _chip(text, color):
    return ui.span(text, class_="easi-chip", style=f"background:{color};")


def _info(text: str = None, *, html_tip: str = None):
    """A small circled-'i'; the custom tooltip (www/tooltip.js) shows the tip. Pass
    ``html_tip`` for a rich card (data-tip-html) or ``text`` for a plain tooltip."""
    attrs = {"onclick": "event.preventDefault();event.stopPropagation();"}
    if html_tip:
        attrs["data-tip-html"] = html_tip
    elif text and text.strip():
        attrs["data-tip"] = text.strip()
    else:
        return None
    return ui.span("i", attrs, class_="easi-info")


def _fmt_num(v):
    """Compact number format for axis/table cells: 35.0 -> '35', 1.250 -> '1.25'."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".rstrip("0").rstrip(".")


# Index score bands (mirror deep.config.INDEX_BANDS): (lo, hi, color) over the 0-1 index.
_INDEX_BAND_SHADE = [(0.0, 0.39, "#f5b5b5"), (0.39, 0.69, "#f5e7a6"), (0.69, 1.0, "#c8d9f2")]


def _curve_svg(points, value=None, xlabel="", w=320, h=200):
    """Labeled reference-curve plot (measured value -> index 0-1) with a site marker.

    The larger, axis-labeled replacement for the old sparkline. Score bands are
    shaded; x ticks sit on the curve breakpoints; the y axis is the 0-1 index.
    Plot geometry is emitted as ``data-*`` attributes so ``www/measure.js`` can
    reposition the marker client-side (``fn_panel`` isolates ``measured_values``,
    so the server does not re-render per keystroke)."""
    pts = sorted(({"x": float(p["x"]), "y": float(p["y"])} for p in points
                  if p.get("x") is not None and p.get("y") is not None), key=lambda p: p["x"])
    if not pts:
        return ""
    xs = [p["x"] for p in pts]
    xmin, xmax = min(xs), max(xs)
    dx = (xmax - xmin) or 1.0
    ml, mr, mt, mb = 46, 12, 12, 42          # margins: left / right / top / bottom
    x0, x1 = ml, w - mr                        # plot box x range (pixels)
    y1, y0 = h - mb, mt                        # y1 = bottom (index 0), y0 = top (index 1)

    def sx(x):
        return x0 + (x - xmin) / dx * (x1 - x0)

    def sy(yv):
        return y1 - max(0.0, min(1.0, yv)) * (y1 - y0)

    P = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" class="deep-curve" '
         f'preserveAspectRatio="xMidYMid meet" style="width:100%;max-width:{w}px;height:auto;" '
         f'data-x0="{x0:.1f}" data-x1="{x1:.1f}" data-y0="{y0:.1f}" data-y1="{y1:.1f}" '
         f'data-xmin="{xmin:g}" data-xmax="{xmax:g}">']

    # score-band shading
    for lo, hi, col in _INDEX_BAND_SHADE:
        yt, yb = sy(hi), sy(lo)
        P.append(f'<rect x="{x0:.1f}" y="{yt:.1f}" width="{x1 - x0:.1f}" height="{yb - yt:.1f}" '
                 f'fill="{col}" fill-opacity="0.4"/>')

    # y axis + gridlines/labels at 0, 0.3, 0.7, 1.0
    P.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x0:.1f}" y2="{y1:.1f}" stroke="#b7c0cf" stroke-width="1"/>')
    for yy in (0.0, 0.3, 0.7, 1.0):
        yp = sy(yy)
        P.append(f'<line x1="{x0:.1f}" y1="{yp:.1f}" x2="{x1:.1f}" y2="{yp:.1f}" '
                 f'stroke="#ffffff" stroke-opacity="0.75" stroke-width="1"/>')
        P.append(f'<text x="{x0 - 6:.1f}" y="{yp + 3:.1f}" text-anchor="end" '
                 f'class="deep-curve-tick">{yy:.1f}</text>')

    # x axis + ticks/labels on the curve breakpoints (endpoints only if crowded)
    P.append(f'<line x1="{x0:.1f}" y1="{y1:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" stroke="#b7c0cf" stroke-width="1"/>')
    tick_xs, seen_tx = ([xmin, xmax] if len(set(xs)) > 6 else xs), set()
    for xv in tick_xs:
        if xv in seen_tx:
            continue
        seen_tx.add(xv)
        xp = sx(xv)
        P.append(f'<line x1="{xp:.1f}" y1="{y1:.1f}" x2="{xp:.1f}" y2="{y1 + 4:.1f}" '
                 f'stroke="#b7c0cf" stroke-width="1"/>')
        P.append(f'<text x="{xp:.1f}" y="{y1 + 15:.1f}" text-anchor="middle" '
                 f'class="deep-curve-tick">{_fmt_num(xv)}</text>')

    # axis titles
    if xlabel:
        P.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{h - 6:.1f}" text-anchor="middle" '
                 f'class="deep-curve-axis">{html.escape(xlabel)}</text>')
    P.append(f'<text transform="translate(13,{(y0 + y1) / 2:.1f}) rotate(-90)" text-anchor="middle" '
             f'class="deep-curve-axis">Index (0–1)</text>')

    # reference curve
    line = " ".join(f"{sx(p['x']):.1f},{sy(p['y']):.1f}" for p in pts)
    P.append(f'<polyline points="{line}" fill="none" stroke="#2f4b7c" stroke-width="2"/>')

    # site marker (always emitted; hidden until a value exists so measure.js can position it live)
    if value is not None:
        vx = min(max(float(value), xmin), xmax)
        iy = curves.interp_curve(pts, vx) or 0.0
        mxp, myp, hid = sx(vx), sy(iy), ""
    else:
        mxp, myp, hid = x0, y1, ' visibility="hidden"'
    P.append(f'<line class="deep-mk-v" x1="{mxp:.1f}" y1="{y1:.1f}" x2="{mxp:.1f}" y2="{myp:.1f}" '
             f'stroke="#d6453d" stroke-width="1.3" stroke-dasharray="3 2"{hid}/>')
    P.append(f'<line class="deep-mk-h" x1="{x0:.1f}" y1="{myp:.1f}" x2="{mxp:.1f}" y2="{myp:.1f}" '
             f'stroke="#d6453d" stroke-width="1" stroke-dasharray="3 2" stroke-opacity="0.6"{hid}/>')
    P.append(f'<circle class="deep-mk-dot" cx="{mxp:.1f}" cy="{myp:.1f}" r="4" fill="#d6453d"{hid}/>')
    P.append("</svg>")
    return "".join(P)


def _criteria_table(points):
    """Static reference-curve breakpoint legend beside the plot (value -> index ->
    condition band). The measured value's index + condition is shown on the chip next
    to the input instead, so this stays a read-only reference (it is NOT the site's row)."""
    pts = sorted(({"x": float(p["x"]), "y": float(p["y"])} for p in points
                  if p.get("x") is not None and p.get("y") is not None), key=lambda p: p["x"])
    if not pts:
        return ""
    rows = ['<table class="deep-criteria-table">'
            '<caption>Reference curve breakpoints</caption><thead><tr>'
            '<th>Value</th><th>Index</th><th>Condition</th></tr></thead><tbody>']
    for p in pts:
        col = scoring.index_band_color(p["y"])
        lbl = html.escape(scoring.index_band_label(p["y"]))
        rows.append(f'<tr><td>{_fmt_num(p["x"])}</td><td>{p["y"]:.2f}</td>'
                    f'<td><span class="deep-band-dot" style="background:{col};"></span>{lbl}</td></tr>')
    rows.append("</tbody></table>")
    return "".join(rows)


_TIER_LABELS = {
    "minimally_disturbed": "Minimally disturbed",
    "least_disturbed": "Least disturbed",
    "best_available": "Best available (fallback)",
}


def _tier_label(tier) -> str:
    """Human label for a bundle's reference tier; '' when the version predates the stamp."""
    if not tier:
        return ""
    return _TIER_LABELS.get(str(tier), str(tier).replace("_", " "))


def _ref_to_adopt(current_ref, covering):
    """The ref Basin should adopt on its own, or None to leave the choice alone.

    Adopting reloads the assessment, which clears every measured value, so this has to
    be one-shot: once a ref is set (automatically, by hand, by a ?assessment= link, or
    by a restored session) it stands until the user picks another.
    """
    if current_ref:
        return None
    if not covering:
        return None
    return covering[0].get("defaultRef") or None


def _session_ref(state: dict, raw: dict):
    """The "id@vN" a restored session was scored against, or None if it cannot be named.

    v2 sessions record assessmentId and version in their provenance block; the embedded
    bundle carries both as a fallback for a migrated v1 file.
    """
    prov = (state or {}).get("provenance") or {}
    aid = prov.get("assessmentId") or (raw or {}).get("assessmentId") or ""
    ver = prov.get("version") or ((raw or {}).get("library") or {}).get("version")
    if not aid:
        return None
    return f"{aid}@v{ver}" if ver else aid


def _assessment_facts(la, ref) -> dict:
    """The facts the Basin pane states about the assessment it resolved."""
    cov = assessments.coverage_of(la)
    nmet = sum(len(fn.get("metrics", [])) for fn in la.metrics_by_function)
    cov_note = ("" if cov["covered"] >= cov["total"]
                else f" ({cov['excluded']} documented)" if cov["declared"]
                else " (not declared)")
    lib = la.raw.get("library") or {}
    region = la.raw.get("region") or lib.get("region") or {}
    return {
        "name": la.assessment_name,
        "region": region.get("name") or "",
        "lifecycle": session.lifecycle_status(la.raw),
        "version": lib.get("version"),
        "ref": ref or "",
        "counts": f"{nmet} metrics · {cov['covered']} of {cov['total']} functions{cov_note}",
        "tier": _tier_label(la.raw.get("referenceTier")),
        "best_available": str(la.raw.get("referenceTier")) == "best_available",
    }


def _assessment_pane_block(la, ref, *, can_change: bool, covers_site: bool = True):
    """The resolved-assessment block that sits under the basin card.

    Everything the old Region step said, in the pane that already holds the basin it
    applies to. The version chooser moves behind "Change" because a point resolves to
    one candidate almost every time.
    """
    f = _assessment_facts(la, ref)
    badge_cls = "deep-badge-cert" if f["lifecycle"] == "certified" else "deep-badge-prelim"
    meta = ([f'v{f["version"]}'] if f["version"] else []) + [f["counts"]]
    lines = []
    if f["region"]:
        lines.append(ui.div(f["region"], class_="deep-pane-line"))
    lines.append(ui.div(" · ".join(meta), class_="deep-pane-line"))
    if f["tier"]:
        lines.append(ui.div(f'Reference tier: {f["tier"]}', class_="deep-pane-line"))
    if f["best_available"]:
        lines.append(ui.div("Scores compare the site with the best remaining streams of the "
                            "region, not with unimpaired condition.", class_="deep-pane-note"))
    if not covers_site:
        lines.append(ui.div("This assessment's region does not contain your site.",
                            class_="deep-pane-caution"))
    return ui.div(
        ui.div(ui.span("Assessment", class_="deep-pane-label"),
               ui.span(session.status_label(f["lifecycle"]),
                       class_=f"deep-card-badge {badge_cls}"),
               class_="deep-pane-head"),
        ui.div(f["name"], class_="deep-pane-name"),
        *lines,
        (ui.div(ui.input_action_link("change_assessment", "Change"),
                class_="deep-pane-change") if can_change else None),
        class_="deep-pane-assess")


def _no_assessment_block(*, has_candidates: bool = False):
    """Basin's blocked state.

    ``has_candidates`` is the rare case where assessments do cover the point but none
    loaded, i.e. load_ref raised; the picker is still worth offering there.
    """
    if has_candidates:
        return ui.div(
            ui.div("Assessment", class_="deep-pane-label"),
            ui.div("No assessment loaded", class_="deep-pane-name"),
            ui.div("Choose one of the assessments that cover this point.",
                   class_="deep-pane-line"),
            ui.div(ui.input_action_link("change_assessment", "Choose"),
                   class_="deep-pane-change"),
            class_="deep-pane-assess is-blocked")
    return ui.div(
        ui.div("Assessment", class_="deep-pane-label"),
        ui.div("No assessment covers this point", class_="deep-pane-name"),
        ui.div("Detailed scoring needs a published assessment whose region contains your "
               "site. The shaded regions on the map are the areas with coverage.",
               class_="deep-pane-line"),
        class_="deep-pane-assess is-blocked")


def _metric_tip_html(m) -> str:
    """Rich hover card for a metric: how to collect it, plus what stands behind
    its curve. Pulls the assessment's ``metricStatement`` / ``howToMeasure`` /
    ``methodContext`` prose (all optional) and the builder's annotations
    (``metricRole``, ``referenceN``, ``sampleDisposition``, ``curveCaveats``,
    ``confidenceLabel``, ``referenceTier``), all optional. Falls back to a muted
    note when the assessment carries none yet (raw text may hold '<', '>', '&',
    so escape it)."""
    name = m.get("metricName", m.get("metricId", ""))
    parts = [f'<div class="easi-tip-title">{html.escape(name)}</div>']
    any_sec = False
    for lbl, key in (("", "metricStatement"), ("How to measure", "howToMeasure"),
                     ("Method", "methodContext")):
        txt = (m.get(key) or "").strip()
        if not txt or txt == name:
            continue
        any_sec = True
        head = f'<span class="easi-tip-lbl">{html.escape(lbl)}</span>' if lbl else ""
        parts.append(f'<div class="easi-tip-sec">{head}{html.escape(txt)}</div>')
    if not any_sec:
        parts.append('<div class="easi-tip-sub">Field collection guidance has not been '
                     'provided for this assessment yet.</div>')
    # What stands behind the curve (stamped by StreamCurves at publish).
    basis = []
    tier = _tier_label(m.get("referenceTier"))
    if tier:
        basis.append(f"Reference tier: {tier}")
    role = str(m.get("metricRole") or "").strip()
    if role == "stressor_surrogate":
        basis.append("Landscape stressor surrogate (footprint comparison, not measured function)")
    elif role == "response":
        basis.append("Site-scale response measurement")
    n = m.get("referenceN")
    disp = str(m.get("sampleDisposition") or "").strip()
    if isinstance(n, (int, float)):
        basis.append(f"Reference sites: {int(n)}" + (f" ({disp})" if disp else ""))
    conf = m.get("confidenceLabel")
    if conf:
        basis.append(f"Builder confidence: {html.escape(str(conf))} (a review-priority heuristic, not a probability)")
    if basis:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Curve basis</span>'
                     + "; ".join(html.escape(b) if not b.startswith("Builder confidence") else b
                                 for b in basis) + "</div>")
    caveats = [str(c) for c in (m.get("curveCaveats") or []) if str(c).strip()]
    if caveats:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Read with care</span>'
                     + " ".join(html.escape(c) for c in caveats) + "</div>")
    return "".join(parts)


def _source_line(m, rc):
    """One-line data-source attribution from the metric's library fields + runtime
    provenance: humanized inputType, sourceCitation, curve layer, and (for a
    desktop-computed value) the pulled source. Deduped; '' when nothing to show."""
    it = (m.get("inputType") or "").strip()
    it_lbl = {"field": "Field measurement", "desktop (gis)": "Desktop (GIS)",
              "continuous": "Measured value"}.get(it.lower(), it)
    layer = ((m.get("curve") or {}).get("layerName") or "").strip()
    parts = []
    if it_lbl:
        parts.append(it_lbl)
    for s in ((m.get("sourceCitation") or "").strip(), layer):
        if s and s not in parts:
            parts.append(s)
    rsrc = (rc.get("source") or "").strip()
    if rc.get("origin") == "desktop" and rsrc and rsrc not in parts:
        parts.append(rsrc)
    return " · ".join(parts)


def _geo_svg(watershed_gj, reach_gj, w=290, h=180):
    import math

    def rings(gj):
        out = []
        for ft in (gj or {}).get("features", []):
            g = ft.get("geometry") or {}
            t, c = g.get("type"), g.get("coordinates")
            if not c:
                continue
            if t == "Polygon":
                out += [("poly", r) for r in c]
            elif t == "MultiPolygon":
                out += [("poly", r) for poly in c for r in poly]
            elif t == "LineString":
                out.append(("line", c))
            elif t == "MultiLineString":
                out += [("line", ln) for ln in c]
        return out

    ws = rings(delineation.display_simplify(watershed_gj, max_vertices=700)) if watershed_gj else []
    rc = rings(reach_gj) if reach_gj else []
    pts = [p for _t, ring in ws + rc for p in ring]
    if not pts:
        return ""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    kx = math.cos(math.radians((miny + maxy) / 2)) or 1.0
    dx = (maxx - minx) * kx or 1e-6
    dy = (maxy - miny) or 1e-6
    pad = 8
    scale = min((w - 2 * pad) / dx, (h - 2 * pad) / dy)

    def sx(lon):
        return pad + (lon - minx) * kx * scale

    def sy(lat):
        return h - pad - (lat - miny) * scale

    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
             f'class="sfari-minimap" style="width:{w}px;max-width:100%;">']
    for _t, ring in ws:
        d = "M" + " L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in ring) + " Z"
        parts.append(f'<path d="{d}" fill="#fdf24a" fill-opacity="0.35" stroke="#caa700" stroke-width="1"/>')
    for _t, ring in rc:
        d = "M" + " L".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in ring)
        parts.append(f'<path d="{d}" fill="none" stroke="#d6453d" stroke-width="2.4"/>')
    parts.append("</svg>")
    return "".join(parts)


def _stepper(active):
    """Step navigator. Plain data-step anchors rather than Shiny action links, matching
    EASI and SFARI: www/measure.js delegates a click to one `step_nav` event, so the left
    pane and the worksheet rail cannot register the same input id while both are briefly
    in the DOM during a step change. tabindex is carried because an anchor with no href
    is not focusable, which is the one thing input_action_link gave for free.
    """
    done = True
    items = []
    for key, label in STEP_LABELS:
        cls = "easi-step"
        if key == active:
            cls += " active"; done = False
        elif done:
            cls += " done"
        attrs = {"data-step": key, "role": "button", "tabindex": "0"}
        if key == active:
            attrs["aria-current"] = "step"
        items.append(ui.tags.a(label, attrs, class_=cls))
    return ui.div(*items, class_="easi-steps")


# --------------------------------------------------------------------------- #
# STAF top banner — a single link back to the STAF site; cross-links to the
# other tier apps were removed to keep the banner minimal. STAF_LINKS still
# carries every app URL: it is the in-app half of the URL mirror (see README)
# and the desktop shell rewrites all entries via STAF_LINKS_OVERRIDES.
# --------------------------------------------------------------------------- #
STAF_LINKS = {
    "home":   "https://usace-wrises.github.io/staf/",
    "easi":   "https://gtmenichino-easi.share.connect.posit.cloud/",
    "sfari":  "https://gtmenichino-sfari.share.connect.posit.cloud/",
    "curves": "https://gtmenichino-stream-curves.share.connect.posit.cloud/",
    "deep":   "https://gtmenichino-deep.share.connect.posit.cloud/",
}
_staf_links_overrides = os.environ.get("STAF_LINKS_OVERRIDES")
if _staf_links_overrides:  # desktop shell rewrites cross-app links; absent on web deploys
    STAF_LINKS.update(json.loads(_staf_links_overrides))


def staf_topnav():
    return ui.div(
        ui.tags.a("STAF", href=STAF_LINKS["home"], class_="staf-topnav-link",
                  target="_blank", rel="noopener"),
        class_="staf-topnav",
    )


app_ui = ui.page_fillable(
    ui.head_content(ui.tags.link(rel="stylesheet", href="styles.css?v=11"),
                    ui.tags.link(rel="stylesheet", href="deep.css?v=7"),
                    ui.tags.script(src="geocode-autocomplete.js", defer=""),
                    ui.tags.script(src="tooltip.js", defer=""),
                    ui.tags.script(src="coord-entry.js", defer=""),
                    ui.tags.script(src="measure.js?v=4", defer=""),
                    ui.tags.script(src="coverage.js?v=3", defer="")),
    ui.busy_indicators.use(pulse=False),
    ui.div(
        ui.div(
            ui.span("DEEP", ui.tags.small("Detailed Evaluation of Ecosystem Processes"),
                    class_="easi-brand"),
            staf_topnav(),
            ui.div(
                ui.input_action_link("nav_new", "New"),
                ui.input_file("load_session", None, accept=[".json"], multiple=False,
                              button_label="Open"),
                ui.download_button("save_session", "Save", class_="easi-nav-btn"),
                ui.input_action_link("nav_about", "About"),
                ui.input_action_link("nav_help", "Help"),
                class_="easi-nav",
            ),
            class_="easi-header",
        ),
        ui.div(
            output_widget("map", height="100%") if _HAS_MAP
            else ui.div("Map requires ipyleaflet + shinywidgets.", class_="text-muted p-3"),
            class_="easi-map-wrap",
        ),
        # Floating coverage panel (populated client-side by coverage.js). Always visible once the
        # panel docks; shows a "No published assessments yet" empty state until an assessment is
        # published.
        ui.div(
            ui.div(ui.span(class_="deep-cov-caret"),
                   ui.span("Assessment coverage", class_="deep-cov-title"),
                   class_="deep-cov-head"),
            ui.div(id="deep-cov-body", class_="deep-cov-body"),
            id="deep-cov-panel", class_="deep-cov-panel collapsed",
        ),
        ui.output_ui("worksheet"),
        ui.div(ui.output_ui("leftpane"), class_="easi-leftpane"),
        ui.output_ui("readout"),
        ui.output_ui("flow_loading"),
        ui.output_ui("cursor_style"),
        class_="easi-shell",
    ),
    title="DEEP — Detailed Stream Assessment",
    padding=0,
    fillable=True,
)


def _auto_measure_key(la, delineation):
    """Cache key for the desktop auto-measure guard (``computed_for``).

    Scopes the "already computed" check to the combination of assessment
    identity + published version + delineated site, so re-running the SAME
    assessment at a DIFFERENT site (or a newer library version) recomputes
    instead of reusing the stale prefill. Site identity is the snapped COMID plus
    snapped lat/lon rounded to 6 decimals (~0.1 m).
    """
    dl = (delineation or {}).get("delineation") or {}
    lat = dl.get("snapped_lat")
    lon = dl.get("snapped_lon")
    version = ((getattr(la, "raw", None) or {}).get("library") or {}).get("version")
    return (
        la.assessment_id,
        version,
        dl.get("comid"),
        None if lat is None else round(float(lat), 6),
        None if lon is None else round(float(lon), 6),
    )


def _parse_assessment_ref(raw_ref):
    """Parse an ``?assessment=`` deep-link value into ``(assessmentId, version|None)``.

    Accepts a bare id (``co-sqt-adapted``) or ``id@version`` (``co-sqt-adapted@2``). A
    missing/blank ref -> ``(None, None)``; a non-integer version -> version None (the id is
    still honored). DEEP bakes only the latest version per id, so the version is advisory:
    the caller loads the latest and notes any mismatch.
    """
    if not raw_ref:
        return None, None
    ref = str(raw_ref).strip()
    if "@" in ref:
        aid, _, ver = ref.rpartition("@")
        aid = aid.strip()
        ver = ver.strip()
        req = None
        if ver:
            try:
                req = int(ver)
            except ValueError:
                req = None
        return (aid or None), req
    return (ref or None), None


def server(input, output, session_):  # noqa: C901
    current_step = reactive.value(STEP_IDENTIFY)
    snapped_point = reactive.value(None)
    flow_geojson = reactive.value(None)
    delin = reactive.value(None)
    stage = reactive.value("")
    view_bbox = reactive.value(None)
    last_view_change = reactive.value(0.0)
    fetched_bbox = reactive.value(None)

    loaded_assessment = reactive.value(None)          # LoadedAssessment | None
    selected_ref = reactive.value(None)               # "id@vN" resolved on Basin
    covering_cache = reactive.value([])               # the list the open picker rendered
    measured_values = reactive.value({})              # {metricId: {value, na, note, origin, source}}
    current_fn = reactive.value(0)
    compute_nonce = reactive.value(0)          # bumped when desktop-compute merges values
    computed_for = reactive.value(None)        # (assessmentId, version, site) already desktop-computed

    # One-shot deep-link ingest from the URL query string:
    #   ?assessment=<assessmentId>            -> load that library/predefined assessment (latest)
    #   ?assessment=<assessmentId>@<version>  -> same, noting when the requested version is not
    #                                            the latest available (DEEP bakes only the latest)
    # Id-based only: the arbitrary local-path ``?handoff=`` opener was removed (Part A4). A
    # draft is previewed by publishing it, then opening it by id@version.
    _url_ingested = reactive.value(False)

    @reactive.effect
    def _ingest_url_params():
        if _url_ingested():
            return
        try:
            search = session_.clientdata.url_search()
        except Exception:  # noqa: BLE001
            return
        if not search:
            return  # none present yet — wait for a real value before deciding
        _url_ingested.set(True)
        from urllib.parse import parse_qs

        params = parse_qs(search.lstrip("?"))
        aid, req_version = _parse_assessment_ref((params.get("assessment") or [None])[0])
        if not aid:
            return
        # Resolve to an exact ref. A requested version that exists loads exactly; an
        # absent one falls back to the default version with a warning.
        requested_ref = f"{aid}@v{req_version}" if req_version is not None else None
        resolved_ref = requested_ref if (requested_ref and config.load_ref(requested_ref)) \
            else config.default_ref_for(aid)
        if not resolved_ref:
            ui.notification_show(f"Could not open the linked assessment {aid!r}.",
                                 type="error", duration=8)
            return
        try:
            la = assessments.load_ref(resolved_ref)
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not open the linked assessment: {exc}",
                                 type="error", duration=8)
            return
        loaded_assessment.set(la); selected_ref.set(resolved_ref)
        measured_values.set({}); current_fn.set(0)
        current_step.set(STEP_IDENTIFY)
        if requested_ref and resolved_ref != requested_ref:
            ui.notification_show(
                f"Requested v{req_version} of {la.assessment_name} is not available; "
                f"loaded {resolved_ref.split('@')[-1]} instead.", type="warning", duration=7)
        else:
            ui.notification_show(f"Loaded {la.assessment_name} from link.",
                                 type="message", duration=4)

    # Coverage panel (www/coverage.js): reply to the client's ready handshake with the
    # available-assessment outlines. coverage.js draws them as client-side, non-interactive
    # Leaflet layers (out of the LayersControl) and renders a per-assessment toggle panel.
    @reactive.effect
    @reactive.event(input.coverage_ready)
    async def _send_coverage():
        payload = []
        for f in assessments.library_region_features().get("features") or []:
            p = f.get("properties") or {}
            payload.append({
                "assessmentId": p.get("assessmentId"),
                "name": p.get("assessmentName") or p.get("assessmentId"),
                "region": p.get("regionName") or "",
                "version": p.get("version"),
                "geometry": f.get("geometry"),
            })
        await session_.send_custom_message("deep_coverage", {"features": payload})

    _layers: dict = {"flow": None, "marker": None, "ws": None, "reach": None}

    def _remove_layer(key):
        lyr = _layers.get(key)
        if lyr is not None:
            try:
                _MAP.remove(lyr)
            except Exception:  # noqa: BLE001
                pass
            _layers[key] = None

    def _add_layer(key, layer):
        _remove_layer(key)
        _MAP.add(layer)
        _layers[key] = layer

    # ---- persistent map (built once; mutated in place) ----
    if _HAS_MAP:
        clicked = reactive.value(None)

        def _on_map_interaction(**kwargs):
            if kwargs.get("type") == "click":
                c = kwargs.get("coordinates")
                if c:
                    clicked.set((float(c[0]), float(c[1])))

        def _build_map():
            mp = Map(center=(39.5, -98.35), zoom=4, max_zoom=19, scroll_wheel_zoom=True,
                     layout=Layout(height="100%"))
            mp.clear_layers()
            mp.add(TileLayer(url=USGS_IMAGERY_URL, name="USGS Imagery", base=True,
                             attribution=USGS_ATTR, max_native_zoom=16, max_zoom=19))
            mp.add(TileLayer(url=USGS_TOPO_URL, name="USGS Topo", base=True,
                             attribution=USGS_ATTR, max_native_zoom=16, max_zoom=19))
            mp.add(TileLayer(url=USGS_HYDRO_URL, name="NHD Hydrography", base=False,
                             opacity=0.85, attribution=USGS_ATTR, max_native_zoom=16, max_zoom=19))
            # top-right; coverage.js docks the coverage panel into this same control
            # stack (just below this button), so the two auto-space and never overlap.
            mp.add(LayersControl(position="topright"))
            mp.add(ScaleControl(position="bottomright", metric=True, imperial=True))
            mp.on_interaction(_on_map_interaction)
            return mp

        _MAP = _build_map()

        @render_widget
        def map():  # noqa: A001
            return _MAP

        @reactive.calc
        def _view():
            return reactive_read(_MAP, "zoom"), reactive_read(_MAP, "center")

        @reactive.effect
        def _track_view():
            import time
            z, c = _view()
            val = None
            if c and z is not None and z >= FLOW_ZOOM:
                lat, lon = float(c[0]), float(c[1])
                delta = min(0.08, 0.03 * (2 ** (15 - z)))
                val = flowlines._round_bbox(lon - delta, lat - delta, lon + delta, lat + delta)
            view_bbox.set(val)
            last_view_change.set(time.monotonic())

        @reactive.extended_task
        async def flow_task(bbox: tuple) -> "dict | None":
            return await anyio.to_thread.run_sync(lambda: flowlines.flowlines_in_bbox(*bbox))

        @reactive.effect
        def _settle_and_fetch():
            import time
            bbox = view_bbox()
            changed = last_view_change()
            if bbox is None:
                with reactive.isolate():
                    _remove_layer("flow"); flow_geojson.set(None); fetched_bbox.set(None)
                return
            elapsed = time.monotonic() - changed
            if elapsed < 0.5:
                reactive.invalidate_later(0.5 - elapsed + 0.02)
                return
            with reactive.isolate():
                if fetched_bbox() == bbox:
                    return
                fetched_bbox.set(bbox)
            flow_task(bbox)

        @reactive.effect
        def _apply_flowlines():
            try:
                fc = flow_task.result()
            except Exception:
                return
            with reactive.isolate():
                if fc and fc.get("features"):
                    _add_layer("flow", GeoJSON(data=fc, style=FLOWLINE_STYLE, name="Stream lines"))
                    flow_geojson.set(fc)
                else:
                    _remove_layer("flow"); flow_geojson.set(None)

        @reactive.effect
        @reactive.event(clicked)
        def _handle_click():
            if current_step() != STEP_IDENTIFY:
                return
            lat, lon = clicked()
            fc = flow_geojson()
            hit = flowlines.nearest_point_on_lines(fc, lat, lon) if fc else None
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)
            else:
                click_snap_task(lat, lon)

        def _apply_snap(hit):
            slat, slon, dist, comid = hit
            _add_layer("marker", Marker(location=(slat, slon), draggable=False,
                                        title="Selected point", name="Selected point"))
            snapped_point.set((slat, slon, dist, comid))
            ui.update_numeric("lat", value=round(slat, 5))
            ui.update_numeric("lon", value=round(slon, 5))

        @reactive.extended_task
        async def click_snap_task(lat: float, lon: float) -> dict:
            d = 0.012
            return {"hit": await anyio.to_thread.run_sync(
                lambda: flowlines.nearest_point_on_lines(
                    flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d), lat, lon))}

        @reactive.effect
        def _apply_click_snap():
            try:
                res = click_snap_task.result()
            except Exception:
                return
            hit = res.get("hit")
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)
            else:
                ui.notification_show("You didn't click on a stream line — zoom in and click a blue "
                                     "stream line.", type="warning", duration=5)

        @reactive.extended_task
        async def coord_snap_task(lat: float, lon: float) -> dict:
            d = 0.012
            return {"hit": await anyio.to_thread.run_sync(
                lambda: flowlines.nearest_point_on_lines(
                    flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d), lat, lon))}

        @reactive.effect
        def _apply_coord_snap():
            try:
                res = coord_snap_task.result()
            except Exception:
                return
            hit = res.get("hit")
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)
            else:
                _remove_layer("marker"); snapped_point.set(None)
                ui.notification_show("No stream within 150 ft of those coordinates. Adjust them, or "
                                     "zoom in and click a blue stream line.", type="warning", duration=6)

        @reactive.effect
        @reactive.event(input.coords_entered)
        def _coords_entered():
            if current_step() != STEP_IDENTIFY:
                return
            ev = input.coords_entered() or {}
            lat, lon = ev.get("lat"), ev.get("lon")
            if lat is None or lon is None:
                return
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                return
            if not (24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0):
                ui.notification_show("Coordinates must be within the continental United States.",
                                     type="warning", duration=5)
                return
            _MAP.center = (lat, lon); _MAP.zoom = 15
            coord_snap_task(lat, lon)

    # ---- address geocode ----
    @reactive.effect
    @reactive.event(input.find_address)
    def _geocode():
        hit = geocode_address(input.address())
        if hit and _HAS_MAP:
            _MAP.center = (hit[0], hit[1]); _MAP.zoom = 15
            ui.notification_show(f"Centered on {hit[0]:.4f}, {hit[1]:.4f}. Click a blue stream.",
                                 duration=4)
        elif not hit:
            ui.notification_show("Place not found — try a city, address, or stream name.",
                                 type="warning", duration=4)

    @reactive.effect
    @reactive.event(input.address_pick)
    def _geocode_pick():
        if not _HAS_MAP:
            return
        pick = input.address_pick() or {}
        lat, lon = pick.get("lat"), pick.get("lon")
        if lat is None or lon is None:
            return
        _MAP.center = (float(lat), float(lon)); _MAP.zoom = 15
        where = pick.get("label") or f"{float(lat):.4f}, {float(lon):.4f}"
        ui.notification_show(f"Centered on {where}. Click a blue stream.", duration=4)

    @reactive.effect
    def _toggle_delineate():
        ui.update_action_button("delineate", disabled=(snapped_point() is None))

    @reactive.extended_task
    async def delineate_task(lat: float, lon: float, reach_ft: float,
                             comid: "int | None" = None) -> dict:
        return await pipeline.delineate_only(lat, lon, reach_ft, comid=comid)

    @reactive.effect
    @reactive.event(input.delineate)
    def _start_delineate():
        pt = snapped_point()
        try:
            lat = pt[0] if pt else float(input.lat())
            lon = pt[1] if pt else float(input.lon())
        except Exception:
            ui.notification_show("Set a point first.", type="warning", duration=3)
            return
        comid = pt[3] if pt else None
        stage.set("Delineating basin & reach…")
        ui.notification_show("Delineating basin & reach… please wait", id="stage",
                             type="message", duration=None)
        delineate_task(lat, lon, float(input.reach_ft()), comid)

    @reactive.effect
    def _delineate_done():
        status = delineate_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("stage"); stage.set("")
        if status == "error":
            ui.notification_show("Delineation failed — try another point or zoom in further.",
                                 type="error", duration=8)
            return
        try:
            res = delineate_task.result()
        except Exception:
            ui.notification_show("Delineation failed.", type="error", duration=8)
            return
        if res.get("status") != "ok":
            ui.notification_show(res.get("message", "Delineation error"), type="error", duration=8)
            return
        try:
            if res.get("watershed_geojson"):
                _add_layer("ws", GeoJSON(data=delineation.display_simplify(res["watershed_geojson"]),
                                         style=WATERSHED_STYLE, name="Watershed"))
            if res.get("reach_geojson"):
                _add_layer("reach", GeoJSON(data=res["reach_geojson"], style=REACH_STYLE,
                                            name="Assessment reach"))
            d = res.get("delineation") or {}
            if _HAS_MAP:
                bounds = delineation.geojson_bounds(res.get("watershed_geojson"),
                                                    res.get("reach_geojson"))
                if bounds:
                    _MAP.fit_bounds(bounds)
                elif d.get("snapped_lat") is not None:
                    _MAP.center = (d["snapped_lat"], d["snapped_lon"])
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not draw the basin on the map: {exc}", type="error", duration=8)
            return
        delin.set(res)
        current_step.set(STEP_BASIN)

    # ---- step navigation ----
    @reactive.effect
    @reactive.event(input.to_measure)
    def _go_measure():
        if loaded_assessment() is None:
            # The button stays enabled rather than rendering disabled: re-rendering it to
            # flip the attribute resets its click count, which reactive.event reads as a
            # click.
            ui.notification_show(
                "No assessment covers this point, so there is nothing to score."
                if not _covering_here() else "Choose an assessment first.",
                type="warning", duration=4)
            return
        current_fn.set(0)
        current_step.set(STEP_MEASURE)

    def _has(step_target):
        if step_target == STEP_IDENTIFY:
            return True
        if delin() is None:
            return False
        if step_target in (STEP_MEASURE, STEP_REPORT) and loaded_assessment() is None:
            return False
        return True

    @reactive.effect
    @reactive.event(input.step_nav)
    def _stepper_nav():
        """One handler for both steppers; www/measure.js posts the target on a data-step
        click or an Enter/Space keypress."""
        target = (input.step_nav() or {}).get("key")
        if target not in dict(STEP_LABELS):
            return
        # _has reads delin() and loaded_assessment(); isolate them or this effect takes
        # them as dependencies and re-runs on every delineation.
        with reactive.isolate():
            allowed = _has(target)
        if allowed:
            current_step.set(target)
            if target == STEP_REPORT:
                ui.modal_show(_report_modal())
        else:
            ui.notification_show("Finish the earlier steps first.", type="message", duration=2)

    def _do_reset():
        for k in ("ws", "reach", "marker"):
            _remove_layer(k)
        snapped_point.set(None); delin.set(None); stage.set("")
        loaded_assessment.set(None); measured_values.set({}); current_fn.set(0)
        # Basin adopts only when nothing is chosen, so a stale ref here would stop the
        # next delineation from ever resolving one.
        selected_ref.set(None); covering_cache.set([])
        computed_for.set(None)
        ui.update_numeric("lat", value=None)
        ui.update_numeric("lon", value=None)
        if _HAS_MAP:
            _MAP.center = (39.5, -98.35); _MAP.zoom = 4
        current_step.set(STEP_IDENTIFY)
        try:
            ui.modal_remove()
        except Exception:  # noqa: BLE001
            pass

    @reactive.effect
    @reactive.event(input.clear_basin)
    def _clear_basin():
        _do_reset()

    @reactive.effect
    @reactive.event(input.nav_new)
    def _new_assessment():
        has_state = delin() is not None or bool(measured_values()) or loaded_assessment() is not None
        if not has_state:
            _do_reset(); return
        ui.modal_show(ui.modal(
            ui.markdown("Clear the delineation, chosen assessment, and all measured values and start "
                        "over? Use **Save** first if you want to keep it."),
            title="Start a new assessment?",
            footer=ui.TagList(ui.modal_button("Cancel"),
                              ui.input_action_button("confirm_new", "Clear & start new",
                                                     class_="btn-danger")),
            easy_close=True))

    @reactive.effect
    @reactive.event(input.confirm_new)
    def _confirm_new():
        _do_reset()

    @reactive.effect
    @reactive.event(input.nav_about)
    def _about():
        ui.modal_show(ui.modal(
            ui.markdown(
                "**DEEP** — Detailed Evaluation of Ecosystem Processes.\n\n"
                "The detailed tier of the Stream Tiered Assessment Framework. From a clicked point "
                "DEEP delineates the upstream watershed and an assessment reach, loads a detailed "
                "assessment definition (a selection of metrics per function, each with a published "
                "reference curve and the reference tier it was drawn at), and turns your measured "
                "metric values into function scores that roll up to Physical / Chemical / Biological "
                "outcome sub-indices and an Ecosystem Condition Index. Assessments are built in the "
                "companion StreamCurves builder and are preliminary until the scientific team "
                "certifies them."),
            title="About DEEP", easy_close=True, footer=ui.modal_button("Close")))

    @reactive.effect
    @reactive.event(input.nav_help)
    def _help():
        ui.modal_show(ui.modal(
            ui.markdown(
                "1. **Identify** — zoom in until blue stream lines appear and click a stream (or type "
                "coordinates / search an address). Set the reach length and click **Delineate**.\n"
                "2. **Basin** — review the watershed and reach. The published assessment "
                "whose area of applicability covers your site is resolved here (certified "
                "before preliminary); use **Change** when more than one applies.\n"
                "3. **Assessment** — enter each metric's measured value; the reference curve converts "
                "it to an index and the function/outcome scores update live.\n"
                "4. **Report** — review and export the detailed assessment."),
            title="How to use DEEP", easy_close=True, footer=ui.modal_button("Close")))

    # ---- left pane ----
    @render.ui
    def leftpane():
        step = current_step()
        if step == STEP_IDENTIFY:
            with reactive.isolate():
                picked = snapped_point() is not None
            body = ui.TagList(
                ui.div("Zoom in until blue stream lines appear and click a stream to place a point. "
                       "Or enter coordinates below, or search an address.", class_="easi-instr"),
                ui.input_text("address", "Address, place, or stream",
                              placeholder="e.g. Asheville, NC  ·  Mud Creek"),
                ui.input_action_button("find_address", "Find on map",
                                       class_="btn-outline-secondary btn-sm"),
                ui.div("Type to search — suggestions from OpenStreetMap / Photon.",
                       class_="easi-ac-credit"),
                ui.hr(),
                ui.input_numeric("lat", "Latitude", value=None, min=24.0, max=50.0, step=0.0001),
                ui.input_numeric("lon", "Longitude", value=None, min=-125.0, max=-66.0, step=0.0001),
                ui.input_numeric("reach_ft", "Assessment reach (ft)", value=int(DEFAULT_REACH_FT),
                                 min=100, max=5280, step=100),
                ui.output_ui("snap_status"),
                ui.div(ui.input_action_button("delineate", "Delineate Basin and Reach",
                                              class_="btn-primary", disabled=not picked),
                       class_="easi-pane-actions"),
                ui.output_text("busy_text"),
            )
        elif step == STEP_BASIN:
            body = ui.TagList(
                ui.output_ui("basin_card"),
                ui.output_ui("assess_pane"),
                ui.div(ui.input_action_button("clear_basin", "Clear", class_="btn-outline-secondary"),
                       ui.input_action_button("to_measure", "Continue", class_="btn-primary"),
                       class_="easi-pane-actions"))
        else:  # assess / measure / report -> full-width worksheet replaces the left pane
            return None
        head_label = dict(STEP_LABELS).get(step, "DEEP")
        return ui.TagList(
            ui.div(f"DEEP — {head_label}", class_="easi-pane-head"),
            ui.div(_stepper(step), body, class_="easi-pane-body"),
        )

    @render.ui
    def snap_status():
        pt = snapped_point()
        if not pt:
            return ui.p("No point yet — enter coordinates, search an address, or zoom in and click a "
                        "blue stream line.", class_="easi-snap-note")
        return ui.p(f"✓ Snapped to stream ({pt[2]:.0f} ft away). Click “Delineate”.",
                    class_="easi-snap-note ok")

    @render.ui
    def basin_card():
        d = (delin() or {}).get("delineation") or {}
        if not d:
            return None

        def row(label, val):
            return ui.div(ui.span(label), ui.tags.b(str(val)), class_="b-row")
        return ui.div(
            ui.h5(d.get("gnis_name") or "(unnamed reach)"),
            row("Drainage area", f'{d.get("drainage_area_sqkm")} km²'),
            row("Reach length", f'{d.get("reach_length_ft")} ft'),
            row("Stream order", d.get("stream_order")),
            row("COMID", d.get("comid")),
            class_="easi-basin-card")

    @render.text
    def busy_text():
        s = stage()
        running = (delineate_task.status() == "running")
        return s if (s and running) else ""

    @render.ui
    def readout():
        # The map is the workspace only on Identify/Basin; on the worksheet steps the
        # overlay covers it, so hide the zoom/lat-lon readout there (it would poke over
        # the worksheet's bottom-left corner otherwise).
        if not _HAS_MAP or current_step() in (STEP_MEASURE, STEP_REPORT):
            return None
        z, c = _view()
        if not c:
            return ui.div("Zoom in and click a stream", class_="easi-readout")
        return ui.div(f"Zoom {int(z)}  ·  Lat {float(c[0]):.4f}, Lon {float(c[1]):.4f}",
                      class_="easi-readout")

    @render.ui
    def flow_loading():
        if not _HAS_MAP or current_step() != STEP_IDENTIFY:
            return None
        z, _c = _view()
        if z is None or z < FLOW_ZOOM or flow_task.status() != "running":
            return None
        return ui.div(ui.div(class_="easi-spinner"), ui.span("Loading streams…"),
                      class_="easi-flow-loading")

    @render.ui
    def cursor_style():
        z, _c = _view()
        picking = (current_step() == STEP_IDENTIFY and z is not None and z >= FLOW_ZOOM)
        if not picking:
            return None
        return ui.tags.style(
            ".easi-map-wrap .leaflet-grab{cursor:crosshair !important;}"
            ".easi-map-wrap .leaflet-container.leaflet-dragging,"
            ".easi-map-wrap .leaflet-container.leaflet-dragging .leaflet-grab{cursor:grabbing !important;}")

    # ======================================================================= #
    # Assessment resolution (Basin step)
    # ======================================================================= #
    # covering_refs() groups the eligible versions per covering assessment id and sorts
    # certified before preliminary, so the first entry is the one to adopt. The card grid
    # is unchanged, it just lives behind "Change" now.
    _MAX_ASSESS_CARDS = 16

    @reactive.calc
    def _covering_here():
        """Assessments whose area of applicability contains this site's point.

        Reactive on the point deliberately. The old Region step re-read this only because
        the step changed; with the step gone nothing else would trigger it, so an isolated
        read would go stale the moment a second point was delineated.
        """
        pt = snapped_point()
        dd = (delin() or {}).get("delineation") or {}
        lat = pt[0] if pt else dd.get("snapped_lat")
        lon = pt[1] if pt else dd.get("snapped_lon")
        if lat is None or lon is None:
            return []
        return assessments.covering_refs(lat, lon, require_polygon=True)

    @reactive.effect
    def _resolve_assessment():
        """Adopt an assessment as soon as there is a basin for it to apply to."""
        if delin() is None:
            return
        covering = _covering_here()
        with reactive.isolate():
            ref = _ref_to_adopt(selected_ref(), covering)
        if ref:
            _load_ref_into_state(ref, quiet=True)

    @render.ui
    def assess_pane():
        if current_step() != STEP_BASIN or delin() is None:
            return None
        covering = _covering_here()
        la = loaded_assessment()
        if la is None:
            return _no_assessment_block(has_candidates=bool(covering))
        return _assessment_pane_block(
            la, selected_ref(),
            can_change=(len(covering) > 1
                        or sum(len(entry["refs"]) for entry in covering) > 1),
            # An assessment outlives the point it was loaded for: a ?assessment= link or
            # a restored session can name a region this site is not in. Compared by id,
            # not by ref, because every version of an assessment shares one region.
            covers_site=any(e["assessmentId"] == la.assessment_id for e in covering))

    def _assess_cards_ui(covering):
        """One card per covering assessment, each with its own version chooser."""
        sel = selected_ref()
        cards = []
        for i, entry in enumerate(covering[:_MAX_ASSESS_CARDS]):
            refs = entry["refs"]
            ver_by_ref = entry["versionByRef"]
            life_by_ref = entry["lifecycleByRef"]
            ver_choices = {
                ref: (f'v{ver_by_ref.get(ref)} '
                      f'({session.status_label(life_by_ref.get(ref, "preliminary"))})')
                for ref in refs
            }
            default_life = life_by_ref.get(entry["defaultRef"], "preliminary")
            tier_by_ref = entry.get("referenceTierByRef") or {}
            default_tier = _tier_label(tier_by_ref.get(entry["defaultRef"]))
            badge_cls = "deep-badge-cert" if default_life == "certified" else "deep-badge-prelim"
            is_sel = sel is not None and sel in refs
            pick_label = "✓ Selected" if is_sel else "Select this assessment"
            pick_cls = "btn btn-sm btn-primary" if is_sel else "btn btn-sm btn-outline-primary"
            cards.append(ui.div(
                ui.div(
                    ui.span(entry["assessmentName"], class_="deep-card-name"),
                    ui.span(session.status_label(default_life),
                            class_=f"deep-card-badge {badge_cls}"),
                    class_="deep-card-head"),
                ui.div(entry.get("regionName") or "", class_="deep-card-region"),
                (ui.div(ui.span("Reference tier", class_="deep-card-verlabel"),
                        ui.span(" " + default_tier, class_="deep-card-tier"),
                        class_="deep-card-tierrow")
                 if default_tier else None),
                ui.div(
                    ui.span("Version", class_="deep-card-verlabel"),
                    ui.input_select(f"assess_ver_{i}", None, choices=ver_choices,
                                    selected=entry["defaultRef"], width="200px"),
                    class_="deep-card-verrow"),
                ui.input_action_button(f"assess_pick_{i}", pick_label, class_=pick_cls),
                class_="deep-assess-card is-selected" if is_sel else "deep-assess-card"))
        return ui.div(*cards, class_="deep-assess-cards")

    @reactive.effect
    @reactive.event(input.change_assessment)
    def _change_assessment():
        covering = _covering_here()
        # The pick handlers index into this positionally, so snapshot exactly the list
        # the modal is about to render.
        covering_cache.set(covering)
        body = (_assess_cards_ui(covering) if covering
                else ui.p("No assessment covers this point."))
        ui.modal_show(ui.modal(body, title="Choose a detailed assessment", size="l",
                               easy_close=True, footer=ui.modal_button("Cancel")))

    def _load_ref_into_state(ref: str, *, quiet: bool = False) -> bool:
        try:
            la = assessments.load_ref(ref)
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not load assessment: {exc}", type="error", duration=6)
            return False
        loaded_assessment.set(la)
        selected_ref.set(ref)
        measured_values.set({})
        current_fn.set(0)
        if not quiet:
            ui.notification_show(f"Selected {la.assessment_name}.", type="message", duration=3)
        return True

    def _make_pick_handler(idx: int):
        @reactive.effect
        @reactive.event(input[f"assess_pick_{idx}"])
        def _pick():
            covering = covering_cache()
            if idx >= len(covering):
                return
            entry = covering[idx]
            try:
                ref = input[f"assess_ver_{idx}"]()
            except Exception:  # noqa: BLE001
                ref = entry["defaultRef"]
            if _load_ref_into_state(ref or entry["defaultRef"]):
                ui.modal_remove()

    for _i in range(_MAX_ASSESS_CARDS):
        _make_pick_handler(_i)

    # ======================================================================= #
    # Measure worksheet + live rollup + report
    # ======================================================================= #
    def _fns():
        la = loaded_assessment()
        return la.metrics_by_function if la is not None else []

    @reactive.effect
    @reactive.event(input.measure_set)
    def _on_measure_set():
        ev = input.measure_set() or {}
        mid = ev.get("mid")
        if not mid:
            return
        raw = ev.get("value")
        try:
            val = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            val = None
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        cur["value"] = val; cur["origin"] = "field"
        mvs[mid] = cur; measured_values.set(mvs)

    @reactive.effect
    @reactive.event(input.measure_na)
    def _on_measure_na():
        ev = input.measure_na() or {}
        mid = ev.get("mid")
        if not mid:
            return
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        cur["na"] = bool(ev.get("na"))
        mvs[mid] = cur; measured_values.set(mvs)

    @reactive.effect
    @reactive.event(input.measure_note)
    def _on_measure_note():
        ev = input.measure_note() or {}
        mid = ev.get("mid")
        if not mid:
            return
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        cur["note"] = ev.get("note", "")
        mvs[mid] = cur; measured_values.set(mvs)

    @reactive.effect
    @reactive.event(input.measure_stratum)
    def _on_measure_stratum():
        ev = input.measure_stratum() or {}
        mid = ev.get("mid")
        if not mid:
            return
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        cur["stratum"] = ev.get("stratum")
        mvs[mid] = cur
        measured_values.set(mvs)
        compute_nonce.set(compute_nonce() + 1)   # re-render the panel with the chosen curve

    @reactive.effect
    @reactive.event(input.metric_photo_add)
    def _on_photo_add():
        ev = input.metric_photo_add() or {}
        mid, pid, uri = ev.get("mid"), ev.get("id"), ev.get("uri")
        if not mid or not pid or not uri:
            return
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        photos = list(cur.get("photos", []))
        if len(photos) < 6 and not any(p.get("id") == pid for p in photos):
            photos.append({"id": pid, "uri": uri})
        cur["photos"] = photos
        mvs[mid] = cur; measured_values.set(mvs)

    @reactive.effect
    @reactive.event(input.metric_photo_remove)
    def _on_photo_remove():
        ev = input.metric_photo_remove() or {}
        mid, pid = ev.get("mid"), ev.get("id")
        if not mid or not pid:
            return
        mvs = dict(measured_values()); cur = dict(mvs.get(mid, {}))
        cur["photos"] = [p for p in cur.get("photos", []) if p.get("id") != pid]
        mvs[mid] = cur; measured_values.set(mvs)

    @reactive.effect
    @reactive.event(input.nav_move)
    def _nav_move():
        d = int((input.nav_move() or {}).get("d", 0) or 0)
        n = len(_fns())
        if n:
            current_fn.set(max(0, min(n - 1, current_fn() + d)))

    @reactive.effect
    @reactive.event(input.nav_jump)
    def _nav_jump():
        i = (input.nav_jump() or {}).get("i")
        n = len(_fns())
        if i is not None and n:
            current_fn.set(max(0, min(n - 1, int(i))))

    @reactive.calc
    def scored():
        la = loaded_assessment()
        if la is None:
            return scoring.score_assessment({}), {}
        return curves.score_site(la, measure.measured_from_state(measured_values()))

    # ---- desktop auto-compute (Phase 3): prefill the computable metrics ----
    @reactive.extended_task
    async def compute_task(ctx_inputs: dict, metric_ids: list,
                           assessment=None) -> dict:
        return await pipeline.compute_metrics_only(ctx_inputs, metric_ids,
                                                   assessment=assessment)

    @reactive.effect
    def _maybe_compute():
        if current_step() != STEP_MEASURE:
            return
        la = loaded_assessment(); d = delin()
        if la is None or d is None:
            return
        ci = d.get("ctx_inputs")
        if not ci:
            return
        key = _auto_measure_key(la, d)
        with reactive.isolate():
            if computed_for() == key:
                return
        ids = [m["metricId"] for fn in la.metrics_by_function
               for m in fn.get("metrics", []) if m["metricId"] in _COMPUTED_IDS]
        if not ids:
            return
        computed_for.set(key)
        # The loaded assessment rides along so the site-engine adapters can be
        # gated on its predictorSource (the train/serve pairing rule).
        compute_task(ci, ids, la)
        ui.notification_show("Computing desktop metrics (StreamCat / 3DEP)…", id="deep_compute",
                             type="message", duration=None)

    @reactive.effect
    def _compute_done():
        if compute_task.status() in ("initial", "running"):
            return
        ui.notification_remove("deep_compute")
        try:
            res = compute_task.result()
        except Exception:
            return
        if not res:
            return
        with reactive.isolate():
            mvs = dict(measured_values())
        n = 0
        for mid, entry in res.items():
            cur = mvs.get(mid) or {}
            if cur.get("value") in (None, "") and not cur.get("na"):
                mvs[mid] = {**cur, **entry}; n += 1   # merge so a prior note/photo survives prefill
        if n:
            measured_values.set(mvs)
            compute_nonce.set(compute_nonce() + 1)
            ui.notification_show(f"Auto-filled {n} desktop metric(s) — edit any value to override.",
                                 type="message", duration=5)

    @render.ui
    def worksheet():
        step = current_step()
        if step not in (STEP_MEASURE, STEP_REPORT):
            return None
        return ui.div(
            ui.div(
                ui.div("DEEP — Assessment", class_="easi-pane-head"),
                ui.div(_stepper(step), class_="sfari-nav-steps"),
                ui.download_button("dl_field_forms", "Get Field Forms",
                                   class_="sfari-btn sfari-nav-desktop",
                                   title="Print-ready field packet listing every metric to measure"),
                ui.output_ui("fn_nav"),
                class_="sfari-nav"),
            ui.div(ui.output_ui("fn_panel"), class_="sfari-fnpanel"),
            ui.div(ui.output_ui("rollup_rail"), class_="sfari-rollup"),
            class_="sfari-worksheet")

    @render.ui
    def fn_nav():
        if current_step() not in (STEP_MEASURE, STEP_REPORT):
            return None
        cur = current_fn()
        _sc, fresults = scored()
        fns = _fns()
        items = []
        prev_disc = None
        for idx, fn in enumerate(fns):
            disc = fn.get("discipline", "")
            if disc != prev_disc:
                items.append(ui.div(disc, class_="sfari-nav-cat")); prev_disc = disc
            fr = fresults.get(fn["functionId"])
            dot = scoring.function_score_band_color(fr.score) if (fr and fr.score is not None) else "#dfe4ec"
            cls = "sfari-nav-fn" + (" active" if idx == cur else "")
            items.append(ui.div(ui.span(class_="sfari-nav-dot", style=f"background:{dot};"),
                                ui.span(fn.get("functionName", "")),
                                {"data-idx": str(idx)}, class_=cls))
        return ui.TagList(*items)

    @render.ui
    def fn_panel():
        if current_step() not in (STEP_MEASURE, STEP_REPORT):
            return None
        fns = _fns()
        compute_nonce()  # re-render when desktop auto-compute fills values
        if not fns:
            return ui.div("No assessment loaded.", class_="sfari-nav-empty")
        idx = max(0, min(len(fns) - 1, current_fn()))
        fn = fns[idx]
        fid = fn["functionId"]
        with reactive.isolate():
            mvs = measured_values()
            _sc, fresults = scored()
        fr = fresults.get(fid)
        metric_blocks = []
        for m in fn.get("metrics", []):
            mid = m["metricId"]
            rc = mvs.get(mid) or {}
            val = rc.get("value"); na = bool(rc.get("na", False)); note = rc.get("note", "")
            strata = curves.curve_strata(m)
            cur_stratum = rc.get("stratum") or m.get("activeStratum") or (strata[0] if strata else None)
            points = curves.active_points(m, cur_stratum)
            midx = fr.metric_indices.get(mid) if fr else None
            mwarn = fr.metric_warnings.get(mid) if fr else None
            idx_txt = "—" if midx is None else f"{midx:.2f} · {scoring.index_band_label(midx)}"
            idx_col = scoring.index_band_color(midx) if midx is not None else "#eef1f6"
            plot_val = None if (na or val in (None, "")) else float(val)
            src_line = _source_line(m, rc)
            metric_blocks.append(ui.div(
                ui.div(m.get("metricName", mid),
                       ui.span(m.get("discipline", ""), class_="sfari-metric-scale"),
                       _info(html_tip=_metric_tip_html(m)),
                       class_="sfari-metric-name"),
                (ui.div(ui.span("Source", class_="deep-source-key"),
                        ui.span(src_line, class_="deep-source-val"),
                        class_="deep-source-row")
                 if src_line else None),
                (ui.div(
                    ui.span("Stratum", class_="deep-stratum-label"),
                    ui.tags.select(
                        {"data-mid-stratum": mid}, class_="deep-stratum-select",
                        *[ui.tags.option(s, {"value": s,
                                             **({"selected": "selected"} if s == cur_stratum else {})})
                          for s in strata]),
                    class_="deep-stratum-row")
                 if len(strata) > 1 else None),
                ui.div(
                    ui.tags.input({"type": "number", "step": "any", "inputmode": "decimal",
                                   "value": ("" if val in (None, "") else str(val)),
                                   "data-mid": mid, "placeholder": m.get("xLabel", "value"),
                                   **({"disabled": "disabled"} if na else {})},
                                  class_="deep-metric-input"),
                    ui.span(m.get("xLabel", ""), class_="deep-xlabel"),
                    ui.span(idx_txt, {"data-mid-idx": mid}, class_="deep-metric-index",
                            style=f"background:{idx_col};"),
                    class_="deep-measure-row"),
                ui.div(mwarn or "",
                       {"data-mid-warn": mid, "role": "status",
                        **({} if mwarn else {"hidden": "hidden"})},
                       class_="deep-domain-warn"),
                ui.div(
                    ui.HTML(_curve_svg(points, plot_val, m.get("xLabel", ""))),
                    ui.HTML(_criteria_table(points)),
                    class_="deep-plot-wrap"),
                ui.div(ui.tags.label(
                    ui.tags.input({"type": "checkbox", "data-mid-na": mid,
                                   **({"checked": "checked"} if na else {})}, class_="deep-na"),
                    ui.span(" Not applicable")), class_="deep-na-row"),
                ui.tags.textarea(note, {"data-mid-note": mid, "placeholder": "Note (optional)…"},
                                 class_="sfari-metric-note"),
                ui.div(
                    *[ui.span(
                        ui.tags.img({"src": p.get("uri", "")}, class_="sfari-thumb"),
                        ui.tags.button("×", {"data-mid": mid, "data-id": p.get("id"),
                                             "type": "button"}, class_="sfari-photo-rm"),
                        {"data-mid": mid, "data-id": p.get("id")}, class_="sfari-thumb-wrap")
                      for p in (rc.get("photos") or [])],
                    ui.tags.label("📷 Photo",
                                  ui.tags.input({"type": "file", "accept": "image/*",
                                                 "capture": "environment", "data-mid": mid},
                                                class_="sfari-photo"),
                                  class_="sfari-photo-btn"),
                    {"data-mid": mid}, class_="sfari-photos"),
                {"data-metric": mid, "data-points": json.dumps(points)},
                class_="sfari-metric deep-metric"))

        score = fr.score if fr else None
        if score is not None:
            band_lbl = scoring.function_score_band_label(score)
            band_col = scoring.function_score_band_color(score)
        else:
            band_lbl = "Not scored yet"; band_col = "#e7ebf1"
        fscore_tip = (
            '<div class="easi-tip-sec">Computed automatically: the mean of the metric indices '
            'above times 15.</div>'
            '<div class="easi-tip-sec"><span class="easi-tip-lbl">Condition bands</span>'
            '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span>'
            '<span><b>0 to 5:</b> Non-Functioning</span></div>'
            '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span>'
            '<span><b>6 to 10:</b> Functioning-at-Risk</span></div>'
            '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span>'
            '<span><b>11 to 15:</b> Functioning</span></div></div>')
        knob_style = "" if score is None else f"left:{score / 15 * 100:.1f}%;"
        scorecard = ui.div(
            ui.div(ui.span("Function score", class_="deep-fscore-lbl"),
                   _info(html_tip=fscore_tip), class_="deep-fscore-head"),
            ui.div(
                ui.div(ui.div({"style": knob_style}, class_="deep-fscore-knob"),
                       class_="deep-fscore-track"),
                ui.span("–" if score is None else f"{score:.1f}", class_="deep-fscore-num"),
                ui.span(band_lbl, class_="deep-fscore-band", style=f"background:{band_col};"),
                class_="deep-fscore-row"),
            {"data-fn": fid}, class_="deep-scorecard" + ("" if score is not None else " unset"))

        prev_attrs = {"data-nav": "-1", "type": "button"}
        if idx == 0:
            prev_attrs["disabled"] = "disabled"
        total = len(fn.get("metrics", []))
        entered = sum(1 for mm in fn.get("metrics", [])
                      if (mvs.get(mm["metricId"]) or {}).get("na")
                      or (mvs.get(mm["metricId"]) or {}).get("value") not in (None, ""))
        actions = ui.div(
            ui.div(ui.tags.button("‹ Previous", prev_attrs, class_="sfari-btn"),
                   class_="sfari-foot-left"),
            ui.div(ui.span(f"{entered}/{total} entered", class_="sfari-foot-rated"),
                   class_="sfari-foot-status"),
            ui.tags.button("Next function ›" if idx < len(fns) - 1 else "Done",
                           {"data-nav": "1", "type": "button"}, class_="sfari-btn primary"),
            class_="sfari-nav-actions")
        return ui.div(
            ui.div(ui.span(fn.get("functionName", "")),
                   ui.span(f"Function {idx + 1} / {len(fns)} · {fn.get('discipline', '')}",
                           class_="sfari-fn-counter"), class_="sfari-fn-title"),
            ui.div("Enter each metric's measured value. The reference curve converts it to a 0 to 1 index.",
                   class_="sfari-sec-lbl"),
            *metric_blocks,
            scorecard,
            ui.div(actions, class_="sfari-fn-footer"),
            class_="sfari-fnpanel-inner")

    @render.ui
    def rollup_rail():
        if current_step() not in (STEP_MEASURE, STEP_REPORT):
            return None
        sc, _fr = scored()
        eci = sc["ecosystemConditionIndex"]; subs = sc["subIndices"]
        cats = sc["categoryLabels"]; catvals = sc["categorySubIndices"]
        fns = _fns()
        fscores = sc.get("functionScores", {})
        n_total = len(fns)
        n_scored = sum(1 for fn in fns if fn["functionId"] in fscores)
        chips = []
        for cat in CATEGORY_ORDER:
            lbl = cats.get(cat)
            if lbl is None:
                val = ui.span("—", class_="val", style="background:#eef1f6;color:#8a93a3;")
            else:
                col = scoring.index_band_color(catvals.get(cat, 0.0))
                val = ui.span(_FNF_SHORT.get(lbl, "—"), class_="val",
                              style=f"background:{col};color:#33415c;")
            chips.append(ui.div(ui.span(cat, class_="lab"), val, class_="sfari-cat-chip"))
        pct = (n_scored / n_total * 100) if n_total else 0
        report_primary = bool(n_total) and n_scored == n_total
        # Two denominators, two meanings. The progress bar measures DATA ENTRY over
        # the functions this bundle can score, so a field user can still reach 100%
        # on what they are able to fill in. The caption reports FRAMEWORK COVERAGE
        # against all 20, so "12 / 12 functions scored" can no longer read as a
        # complete STAF assessment.
        cov = assessments.coverage_of(loaded_assessment())
        cov_caption = assessments.coverage_caption(cov)
        return ui.TagList(
            ui.h4("Live rollup"),
            ui.div(ui.div(f"{eci:.2f}", class_="sfari-eci"),
                   ui.div("Ecosystem Condition Index", class_="sfari-eci-lbl"), class_="sfari-eci-box"),
            _bar("Physical", subs["physical"], scoring.index_band_color(subs["physical"])),
            _bar("Chemical", subs["chemical"], scoring.index_band_color(subs["chemical"])),
            _bar("Biological", subs["biological"], scoring.index_band_color(subs["biological"])),
            ui.h4("Functional categories", style="margin-top:15px;"),
            ui.div(*chips, class_="sfari-cat-chips"),
            ui.div(ui.tags.span(style=f"width:{pct:.0f}%;"), class_="sfari-progress-bar"),
            ui.div(f"{n_scored} / {n_total} functions scored", class_="sfari-progress"),
            ui.div(cov_caption, class_="sfari-coverage-note") if cov_caption else None,
            ui.tags.button("Open report", {"data-report": "1", "type": "button"},
                           class_="sfari-btn sfari-rollup-report" + (" primary" if report_primary else "")),
        )

    # ---- report modal ----
    @reactive.effect
    @reactive.event(input.open_report_evt)
    def _open_report():
        if delin() is None or loaded_assessment() is None:
            return
        current_step.set(STEP_REPORT)
        ui.modal_show(_report_modal())

    def _report_modal():
        d = delin() or {}
        dl = d.get("delineation") or {}
        la = loaded_assessment()
        sc, _fr = scored()
        slat = dl.get("snapped_lat"); slon = dl.get("snapped_lon")
        coord = (f"{slat:.5f}, {slon:.5f}" if slat is not None and slon is not None else "—")
        minimap = _geo_svg(d.get("watershed_geojson"), d.get("reach_geojson"))
        header = ui.div(
            ui.div(
                ui.h3(la.assessment_name if la else "Detailed assessment", style="margin:0;"),
                ui.div(f"{(la.source_citation if la else '')} · {dl.get('gnis_name') or '(unnamed reach)'}",
                       style="font-size:12px;color:#667;margin-top:3px;"),
                ui.div(f"Lat/Lon {coord}  ·  COMID {dl.get('comid')}  ·  HUC8 {dl.get('huc8')}",
                       style="font-size:12px;color:#667;margin-top:1px;"),
                ui.div(f"Drainage {dl.get('drainage_area_sqkm')} km²  ·  Reach {dl.get('reach_length_ft')} ft",
                       style="font-size:12px;color:#667;margin-top:1px;"),
                style="flex:1;"),
            (ui.HTML(minimap) if minimap else None),
            style="display:flex;gap:16px;align-items:flex-start;margin-bottom:12px;")

        subs = sc["subIndices"]; eci = sc["ecosystemConditionIndex"]
        # The index is computed over the functions this assessment covers, which is
        # correct NA handling -- but unmarked it reads as comparable to a full
        # 20-function index. The caveat travels with the number, not somewhere else.
        rcov = assessments.coverage_of(la) if la else None
        caveat = None
        if rcov and rcov["covered"] < rcov["total"]:
            excl = (f"{rcov['excluded']} documented as out of scope"
                    if rcov["declared"] else "coverage not declared")
            caveat = ui.div(
                f"Index computed over the {rcov['covered']} of {rcov['total']} STAF "
                f"functions this assessment covers ({excl}). Not directly comparable "
                "to a full-framework index.",
                style="font-size:11px;color:#8a93a3;font-style:italic;margin-top:4px;")
        summary = ui.div(
            _bar("Ecosystem Condition Index", eci, scoring.index_band_color(eci)),
            caveat,
            _bar("Physical outcome", subs["physical"], scoring.index_band_color(subs["physical"]), indent=True),
            _bar("Chemical outcome", subs["chemical"], scoring.index_band_color(subs["chemical"]), indent=True),
            _bar("Biological outcome", subs["biological"], scoring.index_band_color(subs["biological"]), indent=True))

        fscores = sc.get("functionScores", {})
        fbars = []
        prev_disc = None
        for fn in _fns():
            disc = fn.get("discipline", "")
            if disc != prev_disc:
                fbars.append(ui.div(disc, style="font-family:var(--font-head);font-weight:700;"
                                                 "color:var(--easi-accent);font-size:12px;margin:8px 0 2px;"))
                prev_disc = disc
            s = fscores.get(fn["functionId"])
            if s is None:
                fbars.append(_bar(fn.get("functionName", "") + " (not scored)", 0, "#eef1f6",
                                  vmax=15, fmt="{:.0f}", indent=True))
            else:
                fbars.append(_bar(fn.get("functionName", ""), s, scoring.function_score_band_color(s),
                                  vmax=15, fmt="{:.1f}", indent=True))

        mvs = measured_values()
        rows = []
        for fn, m, val, idx in report._rows(la, mvs):
            ph = (mvs.get(m["metricId"]) or {}).get("photos") or []
            rows.append(ui.tags.tr(
                ui.tags.td(fn.get("functionName", ""), style="color:#8a93a3;font-size:11px;"),
                ui.tags.td(m.get("metricName", m["metricId"]),
                           (ui.div(*[ui.tags.img({"src": p.get("uri", "")}) for p in ph],
                                   class_="sfari-report-photos") if ph else None)),
                ui.tags.td("—" if val in (None, "") else str(val)),
                ui.tags.td("—" if idx is None else f"{idx:.2f}",
                           style=f"background:{scoring.index_band_color(idx) if idx is not None else '#fff'};"),
                ui.tags.td((m.get("curve") or {}).get("layerName", ""),
                           style="font-size:11px;color:#45506a;")))
        table = ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Function"), ui.tags.th("Metric"),
                                     ui.tags.th("Value"), ui.tags.th("Index"), ui.tags.th("Curve source"))),
            ui.tags.tbody(*rows), class_="easi-tbl")

        body = ui.div(
            header,
            ui.h4("Outcome sub-indices & Ecosystem Condition Index", style="margin-top:4px;"),
            summary,
            ui.h4("Function scores (0–15)", style="margin-top:14px;"),
            ui.div(*fbars),
            ui.h4("Metric values & curve indices", style="margin-top:14px;"),
            table,
            ui.div("Scores are computed automatically from the assessment's reference curves. "
                   "Confirm the curve source applies to your region/stream type.",
                   style="font-size:11px;color:#8a93a3;margin-top:10px;"),
            id="deep-report")
        return ui.modal(
            body, title="DEEP Detailed Assessment Report", easy_close=True, size="xl",
            footer=ui.div(ui.download_button("dl_pdf", "PDF", class_="btn-sm"),
                          ui.download_button("dl_csv", "CSV", class_="btn-sm"),
                          ui.download_button("dl_geojson", "GeoJSON", class_="btn-sm"),
                          ui.modal_button("Close"),
                          style="display:flex;gap:8px;align-items:center;"))

    # ---- exports + resumable session ----
    def _assessment_raw():
        la = loaded_assessment()
        return la.raw if la is not None else {}

    def _site_region():
        """Resolved {level3, state} for the snapped site — stamped into the session and
        reports so a completed assessment records where the point fell (Part D)."""
        dd = (delin() or {}).get("delineation") or {}
        return assessments.resolve_site_regions(dd.get("snapped_lat"), dd.get("snapped_lon"))

    @render.download(filename="deep-assessment.json")
    def save_session():
        yield session.dump(delin() or {}, _assessment_raw(), measured_values(),
                           region=_site_region())

    @render.download(filename="deep-report.csv")
    def dl_csv():
        sc, _fr = scored()
        yield report.build_csv(delin() or {}, loaded_assessment(), measured_values(), sc,
                               region=_site_region())

    @render.download(filename="deep-report.geojson")
    def dl_geojson():
        sc, _fr = scored()
        yield report.build_geojson(delin() or {}, loaded_assessment(), sc, region=_site_region())

    @render.download(filename="deep-report.pdf")
    def dl_pdf():
        sc, _fr = scored()
        yield report.build_pdf(delin() or {}, loaded_assessment(), measured_values(), sc,
                               region=_site_region())

    @render.download(filename=lambda: report.field_forms_filename(loaded_assessment()))
    def dl_field_forms():
        yield report.build_field_forms_pdf(loaded_assessment(), ref=selected_ref() or "")

    @reactive.effect
    @reactive.event(input.load_session)
    def _load_session():
        finfo = input.load_session()
        if not finfo:
            return
        try:
            with open(finfo[0]["datapath"], encoding="utf-8") as fh:
                st = session.load(fh.read())
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not load assessment: {exc}", type="error", duration=6)
            return
        d = st.get("delineation") or {}
        delin.set(d)
        measured_values.set(st.get("measured_values") or {})
        computed_for.set(None)  # restored site/version must recompute desktop metrics
        raw = st.get("assessment") or {}
        if raw:
            try:
                loaded_assessment.set(assessments.LoadedAssessment.from_dict(raw))
            except Exception:  # noqa: BLE001
                loaded_assessment.set(None); selected_ref.set(None)
            else:
                # Without this the next delineation would see no chosen ref, adopt
                # whatever covers the point, and wipe the values just restored.
                selected_ref.set(_session_ref(st, raw))
        if _HAS_MAP and d:
            for k in ("ws", "reach", "marker"):
                _remove_layer(k)
            try:
                if d.get("watershed_geojson"):
                    _add_layer("ws", GeoJSON(data=delineation.display_simplify(d["watershed_geojson"]),
                                             style=WATERSHED_STYLE, name="Watershed"))
                if d.get("reach_geojson"):
                    _add_layer("reach", GeoJSON(data=d["reach_geojson"], style=REACH_STYLE,
                                                name="Assessment reach"))
                dd = d.get("delineation") or {}
                if dd.get("snapped_lat") is not None:
                    _add_layer("marker", Marker(location=(dd["snapped_lat"], dd["snapped_lon"]),
                                                draggable=False, title="Selected point",
                                                name="Selected point"))
                    b = delineation.geojson_bounds(d.get("watershed_geojson"), d.get("reach_geojson"))
                    if b:
                        _MAP.fit_bounds(b)
            except Exception:  # noqa: BLE001
                pass
        current_fn.set(0)
        current_step.set(STEP_MEASURE if loaded_assessment() is not None else STEP_BASIN)
        ui.notification_show("Assessment loaded. Resuming.", type="message", duration=4)


app = App(app_ui, server, static_assets=Path(__file__).parent / "www")

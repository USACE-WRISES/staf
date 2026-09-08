"""SFARI: Stream Functional Assessment Rapid Index (Shiny for Python, Core).

A StreamStats-style workflow that mirrors EASI: zoom in until NHD stream vectors
appear, click a stream to snap a point, delineate the watershed + upstream reach,
review the basin, then walk function-by-function to Likert-score the metrics and
assign each of the 20 functions a 0-15 score (professional judgment), and open an
EASI-style screening report. Desktop GIS evidence is pulled to *support* scoring.

Phase 1 implements the map + delineation + Basin review; the Field-review
worksheet and Report modal are added in Phase 2.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# HyRiver cache -> writable temp dir (Connect Cloud FS is ephemeral). Set before
# any HyRiver import so the clients pick it up.
os.environ.setdefault("HYRIVER_CACHE_NAME",
                      os.path.join(tempfile.gettempdir(), "sfari_hyriver.sqlite"))
os.environ.setdefault("HYRIVER_CACHE_EXPIRE", str(7 * 24 * 3600))

import anyio  # noqa: E402
from shiny import App, reactive, render, ui  # noqa: E402

from sfari import bieger, config, delineation, pipeline, report, scoring, session as session_io, xscalc  # noqa: E402
from sfari import viewport  # noqa: E402
from sfari import comid_anchor, engine_prefill, hr_site, network_display  # noqa: E402
from sfari.datasources import flowlines  # noqa: E402
from sfari.datasources.geocode import geocode_address  # noqa: E402
from sfari.pipeline import DEFAULT_REACH_FT  # noqa: E402

FT_PER_M = 3.28083989501312

try:
    from ipyleaflet import (CircleMarker, GeoJSON, LayersControl, Map, Marker,  # noqa: F401
                        ScaleControl, TileLayer)
    from ipywidgets import Layout
    from shinywidgets import output_widget, reactive_read, render_widget
    _HAS_MAP = True
except Exception:  # pragma: no cover
    _HAS_MAP = False

WATERSHED_STYLE = {"color": "#caa700", "weight": 1, "fillColor": "#fdf24a", "fillOpacity": 0.40}
REACH_STYLE = {"color": "#d6453d", "weight": 4}
FLOWLINE_STYLE = {"color": "#1f6feb", "weight": 3, "opacity": 0.95}
# Two stream colors, EASI's rule (2026-09-07): the NHDPlus HR geometry is drawn
# once and split by the click rule. Dark blue where a click lands within
# SNAP_TOL_FT of an NHDPlus V2 reach (the StreamCat lookup engine answers by
# that COMID), cyan elsewhere (the STAF site engine alone answers there, and
# the COMID-keyed values come from the nearest StreamCat reach downstream,
# labeled). The split is sfari/network_display.py; every click still snaps to
# the high-resolution line.
HR_FLOWLINE_STYLE = {"color": "#22b8cf", "weight": 3, "opacity": 0.9}
# Translucent glow under the NHDPlus V2 reach whose COMID keys the StreamCat values.
SCORED_REACH_STYLE = {"color": "#1f6feb", "weight": 11, "opacity": 0.3}
# Dashed connector from a clicked HR-only stream to its nearest StreamCat reach.
ROUTE_STYLE = {"color": "#5b6472", "weight": 2, "dashArray": "6,5", "opacity": 0.9}
# LayersControl labels; the legend uses the same engine names.
LAYER_COVERED = "Streams: StreamCat lookup engine"
LAYER_UNCOVERED = "Streams: STAF site engine"
LAYER_SCORED = "StreamCat reach"

USGS_TOPO_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
USGS_HYDRO_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
USGS_ATTR = "USGS The National Map"
FLOW_ZOOM = 14          # NHD vectors appear at/above this zoom
SNAP_TOL_FT = 150.0     # click must land within this distance of a flowline
_MISS_TEXT = (f"No stream line within {int(SNAP_TOL_FT)} ft of the click. "
              "Zoom in and click a line.")


def _fmt_km2(value) -> str:
    """``12.35 km²`` from a number, ``unknown`` from None or junk (the HR
    drainage area arrives with eight decimals)."""
    try:
        return f"{float(value):,.2f} km²"
    except (TypeError, ValueError):
        return "unknown"


def _fmt_ft(value) -> str:
    try:
        return f"{float(value):,.0f} ft"
    except (TypeError, ValueError):
        return "unknown"


POINT_STYLE = {"radius": 7, "color": "#1f3b73", "fill_color": "#4c8ef5",
               "fill_opacity": 0.95, "weight": 2}


def _point_marker(lat: float, lon: float):
    """The selected point as a small circle, so the reach's downstream end
    stays visible under it (the tall pin and its shadow covered it, 2026-09-04)."""
    return CircleMarker(location=(lat, lon), name="Selected point", **POINT_STYLE)


def _watershed_engine_text(d_all: dict, es: dict, running: bool) -> str:
    """The Basin pane's engine row: which engine delineated the watershed (the
    per-value badges say what each value describes)."""
    es = es or {}
    d_all = d_all or {}
    rec = d_all.get("siteEngine") or es.get("record") or {}
    ver = rec.get("engineVersion")
    basis = d_all.get("watershedBasis")
    if basis == "site-engine":
        return f"STAF site engine v{ver}" if ver else "STAF site engine"
    if running or es.get("status") == "running":
        return "STAF site engine (calculating)"
    if basis == "nhdplus-v2-basin-of-surrogate":
        return "StreamCat lookup engine (nearest StreamCat reach basin)"
    if es.get("status") == "ok":
        # a session from before 2026-09-07: the V2 basin was drawn and the
        # engine ran beside it
        return "StreamCat lookup engine (NHDPlus V2 basin), HR reach watershed computed"
    return "StreamCat lookup engine (NHDPlus V2 basin)"


# The Identify busy row while the StreamCat reach resolves in the background
# (2026-09-07); the engine's progress line replaces it once Delineate runs.
_FINDING_REACH_TEXT = "Finding the StreamCat reach…"

STEP_IDENTIFY, STEP_BASIN, STEP_REVIEW, STEP_REPORT = "identify", "basin", "review", "report"
STEP_LABELS = [(STEP_IDENTIFY, "Identify"), (STEP_BASIN, "Basin"),
               (STEP_REVIEW, "Assessment"), (STEP_REPORT, "Report")]

# --- function / metric ordering (from the generated data) ---
FN_LIST = sorted(config.functions(), key=lambda f: f["order"])
FN_IDS = [f["id"] for f in FN_LIST]
FN_BY_ID = {f["id"]: f for f in FN_LIST}
METRICS_BY_FN = config.metrics_by_function()
METRICS_BY_ID = config.metrics_by_id()
CATEGORY_ORDER = list(config.CATEGORY_ORDER)
FNS_BY_CAT = config.functions_by_category()
_FNF_SHORT = {"Functioning": "F", "Functioning-at-Risk": "AR", "Non-Functioning": "NF"}


def _bar(label, value, color, *, vmax=1.0, fmt="{:.2f}", indent=False):
    pct = max(0.0, min(100.0, (value / vmax) * 100)) if vmax else 0.0
    cls = "easi-bar-row indent" if indent else "easi-bar-row"
    return ui.div(ui.div(label, class_="easi-bar-label"),
                  ui.div(ui.div(class_="easi-bar-fill", style=f"width:{pct:.0f}%;background:{color};"),
                         class_="easi-bar-track"),
                  ui.div(fmt.format(value), class_="easi-bar-val"), class_=cls)


def _chip(text, color):
    return ui.span(text, class_="easi-chip", style=f"background:{color};")


def _likert_color(lk):
    if lk in ("Strongly Agree", "Agree"):
        return "var(--band-good)"
    if lk in ("Disagree", "Strongly Disagree"):
        return "var(--band-poor)"
    if lk == "Neutral":
        return "#e7ebf1"
    return "#33415c"


# Hydraulics functions that expose the cross-section hydraulics popup.
HYDRAULICS_FNS = {"low-flow-baseflow-dynamics", "high-flow-dynamics",
                  "floodplain-connectivity"}


def _xs_svg(points, stage, lb, rb):
    """Lightweight inline SVG of the cross-section with the water surface at ``stage``.

    Pure string generation (no matplotlib) so the modal renders instantly.
    """
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    zmin, zmax = min(zs), max(zs)
    if stage is not None:
        zmax = max(zmax, stage)
    W, H, pad = 620, 300, 34
    dx = (xmax - xmin) or 1.0
    dz = (zmax - zmin) or 1.0

    def sx(x):
        return pad + (x - xmin) / dx * (W - 2 * pad)

    def sy(z):
        return H - pad - (z - zmin) / dz * (H - 2 * pad)     # invert (elevation up)

    parts = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;background:#fff;border:1px solid #e5e8ee;border-radius:6px;">']
    if stage is not None and stage > zmin:
        n = 300
        step = dx / (n - 1)
        xx = [xmin + i * step for i in range(n)]
        zz = [_interp_series(xs, zs, x) for x in xx]
        i = 0
        while i < n:
            if zz[i] < stage:
                j = i
                while j < n and zz[j] < stage:
                    j += 1
                poly = [f"{sx(xx[i]):.1f},{sy(stage):.1f}"]
                poly += [f"{sx(xx[k]):.1f},{sy(zz[k]):.1f}" for k in range(i, j)]
                poly.append(f"{sx(xx[j-1]):.1f},{sy(stage):.1f}")
                parts.append(f'<polygon points="{" ".join(poly)}" fill="#8fbce6" fill-opacity="0.65"/>')
                i = j
            else:
                i += 1
        parts.append(f'<line x1="{sx(xmin):.1f}" y1="{sy(stage):.1f}" x2="{sx(xmax):.1f}" '
                     f'y2="{sy(stage):.1f}" stroke="#1f6feb" stroke-width="1" stroke-dasharray="4 3"/>')
    for bx in (lb, rb):
        if bx is not None:
            parts.append(f'<line x1="{sx(bx):.1f}" y1="{pad}" x2="{sx(bx):.1f}" y2="{H-pad}" '
                         f'stroke="#c0c8d4" stroke-width="1" stroke-dasharray="2 3"/>')
    bed = " ".join(f"{sx(x):.1f},{sy(z):.1f}" for x, z in points)
    parts.append(f'<polyline points="{bed}" fill="none" stroke="#5b4636" stroke-width="2"/>')
    parts.append(f'<text x="{W/2:.0f}" y="{H-6}" font-size="11" text-anchor="middle" '
                 f'fill="#667">Station (ft) · Elevation (ft)</text>')
    parts.append("</svg>")
    return "".join(parts)


def _interp_series(xs, zs, x):
    """Linear interpolation of z at station x on the (sorted) profile."""
    if x <= xs[0]:
        return zs[0]
    if x >= xs[-1]:
        return zs[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            x0, x1 = xs[i], xs[i + 1]
            if x1 == x0:
                return zs[i]
            return zs[i] + (zs[i + 1] - zs[i]) * (x - x0) / (x1 - x0)
    return zs[-1]


def _geo_svg(watershed_gj, reach_gj, w=290, h=180):
    """Small SVG thumbnail of the watershed outline + assessment reach (report header)."""
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


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def _info(text: str = None, *, html_tip: str = None):
    """A small circled-'i'; the custom tooltip (www/tooltip.js) shows the tip."""
    attrs = {"onclick": "event.preventDefault();event.stopPropagation();"}
    if html_tip:
        attrs["data-tip-html"] = html_tip
    elif text and text.strip():
        attrs["data-tip"] = text.strip()
    else:
        return None
    return ui.span("i", attrs, class_="easi-info")


_LIKERT_DOT = {"Strongly Agree": "good", "Agree": "good", "Neutral": "fair",
               "Disagree": "poor", "Strongly Disagree": "poor"}


def _criteria_tip_html(m) -> str:
    """Rich hover card for a metric: its statement + an example Likert scoring ladder
    (illustrative anchors, not exact thresholds — the user judges fit for their stream).
    Criteria are raw text (may contain '<', '>', '&'), so escape them for the HTML tip."""
    import html as _h
    name = m.get("name", "")
    parts = [f'<div class="easi-tip-title">{_h.escape(name)}</div>']
    stmt = (m.get("metricStatement") or "").strip()
    if stmt and stmt != name:
        parts.append(f'<div class="easi-tip-sec">{_h.escape(stmt)}</div>')
    rungs = []
    for c in m.get("likertCriteria", []):
        crit = (c.get("criteria") or "").strip()
        if not crit:
            continue
        lk = c.get("likert", "")
        short = config.LIKERT_SHORT.get(lk, lk)
        rungs.append(
            f'<div class="easi-tip-crit"><span class="easi-tip-dot {_LIKERT_DOT.get(lk, "fair")}"></span>'
            f'<span><b>{_h.escape(short)}:</b> {_h.escape(crit)}</span></div>')
    if rungs:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Example scoring</span>'
                     '<div class="easi-tip-sub">Illustrative only. Judge what\'s appropriate '
                     'for your stream type and region.</div>'
                     + "".join(rungs) + "</div>")
    return "".join(parts)


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


# --------------------------------------------------------------------------- #
# Evidence provenance: which engine or service produced a value
# --------------------------------------------------------------------------- #
_EV_BADGE = {"engine": ("HR reach watershed", "sfari-ev-tag engine"),
             "streamcat": ("StreamCat", "sfari-ev-tag streamcat"),
             "pull": ("desktop", "sfari-ev-tag")}
_PENDING_BADGE = ("HR reach watershed pending", "sfari-ev-tag pending")


def _ev_badge(edata):
    """``(label, css class)`` of the provenance badge for one evidence entry."""
    if not edata:
        return "field", "sfari-ev-tag field"
    if edata.get("status") == "pending":
        return _PENDING_BADGE
    return _EV_BADGE.get(edata.get("origin") or "pull", _EV_BADGE["pull"])


def _ev_tip(edata):
    """Tooltip text: source, confidence, the reach a value describes, and the
    fallback reason when the STAF site engine did not answer."""
    tip = "Source: " + (edata.get("source") or "—")
    if edata.get("confidence"):
        tip += f"  ·  data confidence {edata['confidence']}"
    if edata.get("anchor_label"):
        tip += "\nDescribes the " + edata["anchor_label"] + "."
    if edata.get("fallback_reason"):
        tip += "\nFallback: " + edata["fallback_reason"]
    if edata.get("note"):
        tip += "\n" + edata["note"]
    return tip


def _ev_describes(edata):
    """A small line under a StreamCat value on a stream outside NHDPlus V2."""
    label = (edata or {}).get("anchor_label")
    if not label:
        return None
    return ui.span("describes the " + label, class_="sfari-ev-describes")


# The engine's progress stages as the assessor sees them: five steps, the
# reach count while the upstream trace and the catchment union run, the
# metric family with its position while the metrics run. Plain words only:
# "hops" (the walk's query levels) never reach the screen (2026-09-07).
_ENGINE_STEPS = {"site": (1, "finding the stream"), "walk": (2, "tracing upstream"),
                 "catchments": (3, "joining catchments"), "union": (3, "joining catchments"),
                 "geometry": (3, "joining catchments"),
                 "reach": (4, "marking the assessment reach"),
                 "metrics": (5, "computing metrics")}
_ENGINE_STEP_COUNT = 5
_ENGINE_FAMILY_TEXT = {"baseflow": "base flow", "dams": "dams", "landcover": "land cover",
                       "roads": "roads", "runoff": "runoff", "soils": "soils",
                       "xsection": "cross-sections"}


def _engine_progress_text(prog: dict, families=None) -> str:
    """The busy-row line while the STAF site engine runs, from the last
    progress event: ``Delineating watershed · step 2 of 5 · tracing upstream,
    42 reaches``. ``families`` is the metric family list the app requested
    (the engine runs them sorted), for the ``(3 of 7)`` position."""
    lead = "Delineating watershed"
    prog = prog or {}
    st = prog.get("stage")
    if st == "done":
        return f"{lead} · finishing"
    step = _ENGINE_STEPS.get(st)
    if step is None:
        return f"{lead} · starting"
    n, what = step
    if n in (2, 3) and prog.get("reaches"):
        count = int(prog["reaches"])
        what += f", {count:,} reach" + ("" if count == 1 else "es")
    if st == "metrics" and prog.get("family"):
        fam = str(prog["family"])
        fams = sorted(families if families is not None else engine_prefill.SFARI_FAMILIES)
        pos = f" ({fams.index(fam) + 1} of {len(fams)})" if fam in fams else ""
        what += f", {_ENGINE_FAMILY_TEXT.get(fam, fam)}{pos}"
    return f"{lead} · step {n} of {_ENGINE_STEP_COUNT} · {what}"


def _engine_line_ui(es: dict, running: bool, prog: dict):
    """The one-line engine status shown in the basin card and the worksheet."""
    st = (es or {}).get("status")
    if running or st == "running":
        return ui.div(_engine_progress_text(prog), class_="sfari-engine-line")
    if st == "ok":
        return None      # the Watershed engine row says it (2026-09-04)
    if st in ("failed", "refused", "unavailable"):
        return ui.div(f"STAF site engine {st}: {es.get('reason') or 'no detail'}. StreamCat "
                      "values stand in where the reach has them, labeled. Delineate again "
                      "to retry.", class_="sfari-engine-line warn")
    return None


def _legend_ui(step, zoomed, mode, reach, routed):
    """The map legend card (docked under the layers button by legend-dock.js):
    which color means which engine, the state of the stream fetch, and the
    StreamCat reach once it is known. Pure, so its states are tested offline.
    None outside the Identify and Basin steps. Every string is a plain sentence."""
    if step not in (STEP_IDENTIFY, STEP_BASIN):
        return None

    def row(color, label, sub=None, glow=False, fill=False):
        cls = "easi-legend-sw"
        if glow:
            cls += " easi-legend-sw-glow"
        if fill:
            cls += " easi-legend-sw-fill"
        return ui.div(ui.span(class_=cls, style=f"background:{color};"),
                      ui.div(ui.div(label, class_="easi-legend-label"),
                             ui.div(sub, class_="easi-legend-sub") if sub else None),
                      class_="easi-legend-row")

    rows = [ui.div("Streams", class_="easi-legend-title"),
            row(FLOWLINE_STYLE["color"], "StreamCat lookup engine",
                "COMID-keyed values from this reach"),
            row(HR_FLOWLINE_STYLE["color"], "STAF site engine",
                "the HR reach watershed, every stream")]
    note = None
    if not zoomed:
        note = "Zoom in to see streams"
    elif mode == "v2-only":
        note = "Fine streams unavailable here. Zoom in."
    elif mode == "hr-only":
        note = "No StreamCat reach in view."
    elif mode == "empty":
        note = "No streams in view."
    if note:
        rows.append(ui.div(note, class_="easi-legend-note"))
    if reach:
        name = reach.get("name") or "unnamed stream"
        comid = reach.get("comid")
        what = "Nearest StreamCat reach" if routed else "StreamCat reach"
        label = f"{what}: {name}" + (f" (COMID {comid})" if comid is not None else "")
        rows.append(row(SCORED_REACH_STYLE["color"], label, glow=True))
    if step == STEP_BASIN:
        rows.append(row(WATERSHED_STYLE["fillColor"], "Watershed", fill=True))
        rows.append(row(REACH_STYLE["color"], "Assessment reach"))
    return ui.div(*rows, class_="easi-legend")


def staf_topnav():
    return ui.div(
        ui.tags.a("STAF", href=STAF_LINKS["home"], class_="staf-topnav-link",
                  target="_blank", rel="noopener"),
        class_="staf-topnav",
    )


app_ui = ui.page_fillable(
    ui.head_content(ui.tags.link(rel="stylesheet", href="styles.css?v=21"),
                    ui.tags.script(src="geocode-autocomplete.js", defer=""),
                    ui.tags.script(src="legend-dock.js?v=1", defer=""),
                    ui.tags.script(src="tooltip.js", defer=""),
                    ui.tags.script(src="coord-entry.js", defer=""),
                    ui.tags.script(src="field-review.js?v=6", defer="")),
    ui.busy_indicators.use(pulse=False),
    ui.div(
        ui.div(
            ui.span("SFARI", ui.tags.small("Stream Functional Assessment Rapid Index"),
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
        ui.output_ui("worksheet"),
        ui.div(ui.output_ui("leftpane"), class_="easi-leftpane"),
        # Stream legend: legend-dock.js moves this wrapper into the map's
        # top-right control stack under the layers button. The card look lives
        # on the rendered content, so an empty output shows nothing.
        ui.div(ui.output_ui("stream_legend"), id="easi-legend-panel",
               class_="easi-legend-panel"),
        ui.output_ui("readout"),
        ui.output_ui("flow_loading"),
        ui.output_ui("cursor_style"),
        class_="easi-shell",
    ),
    title="SFARI · Rapid Stream Assessment",
    padding=0,
    fillable=True,
)


def _stepper(active):
    """Step navigator. Uses a data-step JS bridge (not Shiny input ids) so the two
    steppers (left pane + worksheet rail) never register duplicate input ids."""
    done = True
    items = []
    for key, label in STEP_LABELS:
        cls = "easi-step"
        if key == active:
            cls += " active"; done = False
        elif done:
            cls += " done"
        items.append(ui.tags.a(label, {"data-step": key, "role": "button"}, class_=cls))
    return ui.div(*items, class_="easi-steps")


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
def server(input, output, session):
    current_step = reactive.value(STEP_IDENTIFY)
    snapped_point = reactive.value(None)   # (lat, lon, dist_ft, comid) | None
    delin = reactive.value(None)           # delineate_only result (+ ctx_inputs)
    stage = reactive.value("")             # progress label
    view_bbox = reactive.value(None)       # rounded bbox at zoom >= FLOW_ZOOM | None
    last_view_change = reactive.value(0.0)
    fetched_bbox = reactive.value(None)
    view_bounds = reactive.value(None)     # (south, west, north, east) of the viewport | None

    # ---- field-review scoring state (Phase 2) ----
    metric_scores = reactive.value({})     # {metricId: {"likert": str|None, "note": str}}
    function_scores = reactive.value({})   # {functionId: {"score": int|None, "note": str}}
    current_fn = reactive.value(0)         # index into FN_IDS
    evidence = reactive.value({})          # {metricId: EvidenceResult dict} (desktop pull)
    _pull_prog = {"done": 0, "total": 0}
    xs_geom = reactive.value(None)         # cross-section geometry for the hydraulics popup
    hr_geojson = reactive.value(None)      # NHDPlus HR flowlines in the viewport | None
    flow_geojson = reactive.value(None)    # NHDPlus V2 flowlines in the viewport | None
    streams_mode = reactive.value(None)    # network_display mode of the drawn layers | None
    zoomed_in = reactive.value(False)      # zoom >= FLOW_ZOOM (the legend reads this, not the view)
    site_anchor = reactive.value(None)     # the StreamCat reach classification of the point | None
    evidence_reach = reactive.value(None)  # {"comid", "name"} of the glowing V2 reach | None
    engine_state = reactive.value({"status": "idle"})   # the STAF site engine on this site
    _engine_prog = {"stage": None, "reaches": None, "hops": None, "family": None}
    _no_watershed = {}                     # the pending continuation after an engine failure

    _layers: dict = {"flow": None, "hrflow": None, "route": None, "scored": None,
                     "marker": None, "ws": None, "reach": None}

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
        def _on_map_interaction(**kwargs):
            if kwargs.get("type") == "click":
                c = kwargs.get("coordinates")
                if c:
                    clicked.set((float(c[0]), float(c[1])))  # (lat, lon)

        clicked = reactive.value(None)

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
            mp.add(LayersControl(position="topright"))
            mp.add(ScaleControl(position="bottomright", metric=True, imperial=True))
            mp.on_interaction(_on_map_interaction)
            return mp

        _MAP = _build_map()

        @render_widget
        def map():  # noqa: A001
            return _MAP

        _EMPTY_FC = {"type": "FeatureCollection", "features": []}

        def _set_layer_data(key, fc, style, name):
            """Update a stream layer in place, creating it only the first time.

            The widget re-renders on a data change without tearing the layer
            down, so the map does not flash and the draw order is kept.
            Returns True when the layer was created (2026-09-02)."""
            lyr = _layers.get(key)
            if lyr is not None:
                lyr.data = fc
                return False
            _add_layer(key, GeoJSON(data=fc, style=style, name=name))
            return True

        @reactive.calc
        def _view():
            return reactive_read(_MAP, "zoom"), reactive_read(_MAP, "center")

        @reactive.effect
        def _track_view():
            import time
            z, c = _view()
            bounds = reactive_read(_MAP, "bounds")
            val = None
            if c and z is not None and z >= FLOW_ZOOM:
                # The padded fetch box around the center (viewport.fetch_box):
                # wider than tall from zoom 15, so a pan of a few hundred
                # pixels stays inside it and needs no fetch (2026-09-02).
                val = viewport.fetch_box(float(c[0]), float(c[1]), float(z))
            view_bbox.set(val)
            zoomed_in.set(val is not None)     # a bool: repeated sets never invalidate
            view_bounds.set(viewport.view_from_bounds(bounds))
            last_view_change.set(time.monotonic())

        @reactive.effect
        def _settle_and_fetch():
            import time
            bbox = view_bbox()
            changed = last_view_change()
            if bbox is None:
                with reactive.isolate():
                    fetched_bbox.set(None)
                    _remove_layer("flow"); flow_geojson.set(None)
                    _remove_layer("hrflow"); hr_geojson.set(None)
                    streams_mode.set(None)
                return
            elapsed = time.monotonic() - changed
            if elapsed < 0.5:
                reactive.invalidate_later(0.5 - elapsed + 0.02)
                return
            with reactive.isolate():
                # Fetch only when the viewport left the box last fetched: a pan
                # inside the margin or a zoom in keeps the lines it already has.
                if not viewport.needs_fetch(view_bounds(), fetched_bbox()):
                    return
                fetched_bbox.set(bbox)
            streams_task(bbox)

        @reactive.extended_task
        async def streams_task(bbox: tuple) -> dict:
            # Both networks fetched side by side, then split by the click rule
            # (sfari.network_display), all on the worker thread.
            return await anyio.to_thread.run_sync(
                lambda: network_display.fetch_streams(bbox, tol_ft=SNAP_TOL_FT))

        @reactive.effect
        def _apply_streams():
            try:
                res = streams_task.result()
            except Exception:
                return
            with reactive.isolate():
                fetched = fetched_bbox()
                if fetched is None or tuple(res.get("bbox") or ()) != tuple(fetched):
                    return                          # torn down or a stale box
                flow_geojson.set(res.get("v2"))     # raw networks: the click rule's input
                hr_geojson.set(res.get("hr"))
                # Cyan first, then dark blue on top; after the first fetch both
                # update in place (no flash, draw order kept).
                _set_layer_data("hrflow", res["uncovered"], HR_FLOWLINE_STYLE, LAYER_UNCOVERED)
                created = _set_layer_data("flow", res["covered"], FLOWLINE_STYLE, LAYER_COVERED)
                if created:
                    # Freshly created stream layers land above the pick chrome
                    # (a zoom out tears the streams down, a zoom in recreates
                    # them), so the chrome goes back on top.
                    for key in ("scored", "route", "marker"):
                        if _layers.get(key) is not None:
                            _add_layer(key, _layers[key])
                if streams_mode() != res.get("mode"):
                    streams_mode.set(res.get("mode"))

        # ---- click -> snap or reject (only during the identify step) ----
        @reactive.effect
        @reactive.event(clicked)
        def _handle_click():
            if current_step() != STEP_IDENTIFY:
                return
            lat, lon = clicked()
            # The viewport's stream vectors, when loaded, settle the click
            # without a fetch; otherwise the engine's HR client snaps it.
            fc = hr_geojson()
            hit = (flowlines.nearest_point_on_lines(fc, lat, lon, id_prop="nhdplusid")
                   if fc else None)
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit, click=(lat, lon))
            else:
                click_snap_task(lat, lon)

        def _place_pin(slat: float, slon: float):
            """The pin and the coordinate inputs on the snapped point, at once.
            An existing marker moves in place."""
            marker = _layers.get("marker")
            if marker is not None and marker in _MAP.layers:
                marker.location = (slat, slon)
            else:
                _add_layer("marker", _point_marker(slat, slon))
            ui.update_numeric("lat", value=round(slat, 5))
            ui.update_numeric("lon", value=round(slon, 5))

        def _clear_anchor_state():
            # A new pick invalidates the last StreamCat reach and its map chrome.
            _remove_layer("route")
            _remove_layer("scored")
            site_anchor.set(None)
            evidence_reach.set(None)

        def _apply_snap(hit, *, click=None):
            """Pin the HR snap point, then find the StreamCat reach for it in
            the background (comid_anchor): the V2 line under the click, or the
            nearest StreamCat reach downstream of the HR snap point."""
            slat, slon, dist, nhdplusid = hit
            _clear_anchor_state()
            _place_pin(slat, slon)
            snapped_point.set((slat, slon, dist, nhdplusid))
            lat, lon = click if click and click[0] is not None else (slat, slon)
            with reactive.isolate():
                v2_fc = flow_geojson()
            anchor_task(lat, lon, tuple(hit), v2_fc)
            stage.set(_FINDING_REACH_TEXT)          # the busy row until the reach lands

        def _apply_snap_result(res, *, from_coords=False):
            hit = res.get("hit")
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit, click=(res.get("lat"), res.get("lon")))
                return
            if from_coords:
                _remove_layer("marker")
                snapped_point.set(None)
                ui.notification_show(
                    "No stream within 150 ft of those coordinates. Adjust them, or zoom in "
                    "and click a stream line.", type="warning", duration=6)
            else:
                ui.notification_show(_MISS_TEXT, type="warning", duration=6)

        @reactive.extended_task
        async def click_snap_task(lat: float, lon: float) -> dict:
            return await anyio.to_thread.run_sync(
                lambda: hr_site.snap_point(lat, lon))

        @reactive.effect
        def _apply_click_snap():
            try:
                res = click_snap_task.result()
            except Exception:
                return
            _apply_snap_result(res)

        # ---- the StreamCat reach for the point (comid_anchor), in the background ----
        @reactive.extended_task
        async def anchor_task(lat: float, lon: float, hr_hit: tuple, v2_fc) -> dict:
            def run():
                res = comid_anchor.resolve(lat, lon, hr_hit, v2_fc=v2_fc)
                cid = comid_anchor.comid(res.get("anchor"))
                if cid is not None:
                    feat = (network_display.feature_by_id(v2_fc, "comid", cid)
                            or network_display.v2_reach_feature(int(cid)))
                    if feat:
                        res["scoredFeature"] = feat
                return res
            return await anyio.to_thread.run_sync(run)

        def _draw_anchor(res: dict):
            """The StreamCat reach on the map: a glow under the V2 reach and, on
            a stream outside V2, the dashed route from the pin to it."""
            _remove_layer("route")
            _remove_layer("scored")
            anchor = res.get("anchor")
            if not anchor:
                site_anchor.set(None)
                evidence_reach.set(None)
                return
            site_anchor.set(anchor)
            evidence_reach.set(comid_anchor.legend_reach(anchor))
            feat = res.get("scoredFeature")
            if feat and feat.get("geometry"):
                _add_layer("scored", GeoJSON(
                    data={"type": "FeatureCollection", "features": [feat]},
                    style=SCORED_REACH_STYLE, name=LAYER_SCORED))
            seg = comid_anchor.route_segment(anchor)
            if seg:
                _add_layer("route", GeoJSON(data=seg, style=ROUTE_STYLE,
                                            name="Nearest StreamCat reach"))
            if _layers.get("marker") is not None:
                _add_layer("marker", _layers["marker"])      # the point stays on top

        @reactive.effect
        def _anchor_done():
            try:
                res = anchor_task.result()
            except Exception:
                return
            with reactive.isolate():
                _draw_anchor(res)
                if stage() == _FINDING_REACH_TEXT:   # a running engine owns the row
                    stage.set("")

        @reactive.extended_task
        async def coord_snap_task(lat: float, lon: float) -> dict:
            return await anyio.to_thread.run_sync(
                lambda: hr_site.snap_point(lat, lon))

        @reactive.effect
        def _apply_coord_snap():
            try:
                res = coord_snap_task.result()
            except Exception:
                return
            _apply_snap_result(res, from_coords=True)

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
                ui.notification_show("Coordinates must be within the continental "
                                     "United States.", type="warning", duration=5)
                return
            _MAP.center = (lat, lon)
            _MAP.zoom = 15
            coord_snap_task(lat, lon)

    # ---- address geocode -> recenter the map so streams appear ----
    @reactive.effect
    @reactive.event(input.find_address)
    def _geocode():
        hit = geocode_address(input.address())
        if hit and _HAS_MAP:
            _MAP.center = (hit[0], hit[1])
            _MAP.zoom = 15
            ui.notification_show(f"Centered on {hit[0]:.4f}, {hit[1]:.4f}. Click a stream line.",
                                 duration=4)
        elif not hit:
            ui.notification_show("Place not found. Try a city, address, or stream name.",
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
        _MAP.center = (float(lat), float(lon))
        _MAP.zoom = 15
        where = pick.get("label") or f"{float(lat):.4f}, {float(lon):.4f}"
        ui.notification_show(f"Centered on {where}. Click a stream line.", duration=4)

    # ---- enable "Delineate" only once a point is picked on the map ----
    @reactive.effect
    def _toggle_delineate():
        ui.update_action_button("delineate", disabled=(snapped_point() is None))

    # ---- the STAF site engine: the HR reach watershed and the reach, every site ----
    @reactive.extended_task
    async def engine_task(lat: float, lon: float, reach_ft: float) -> dict:
        def _cb(event):
            for k in ("stage", "reaches", "hops", "family"):
                if k in event:
                    _engine_prog[k] = event[k]
        rec = await anyio.to_thread.run_sync(
            lambda: engine_prefill.run_engine(lat, lon, reach_length_ft=reach_ft, progress=_cb))
        return {"record": rec, "lat": lat, "lon": lon, "reach_ft": reach_ft}

    def _launch_engine(lat, lon, reach_ft):
        if not engine_prefill.site_engine_available():
            engine_state.set({"status": "unavailable",
                              "reason": "the STAF site engine is not available in this deployment"})
            ui.notification_show("The STAF site engine is not available in this deployment.",
                                 type="error", duration=8)
            return
        for k in _engine_prog:
            _engine_prog[k] = None
        engine_state.set({"status": "running"})
        stage.set(_engine_progress_text({}))
        engine_task(lat, lon, reach_ft)

    @reactive.effect
    def _engine_poll():
        if engine_task.status() != "running":
            ui.notification_remove("engine")      # never a stale progress toast
            return
        reactive.invalidate_later(1.0)
        stage.set(_engine_progress_text(_engine_prog))    # the busy row, no toast

    def _draw_delineation(res) -> bool:
        try:
            _remove_layer("ws"); _remove_layer("reach")
            if res.get("watershed_geojson"):
                _add_layer("ws", GeoJSON(data=delineation.display_simplify(res["watershed_geojson"]),
                                         style=WATERSHED_STYLE, name="Watershed"))
            if res.get("reach_geojson"):
                _add_layer("reach", GeoJSON(data=res["reach_geojson"], style=REACH_STYLE,
                                            name="Assessment reach"))
            if _layers.get("marker") is not None:
                _add_layer("marker", _layers["marker"])      # the point stays on top
            d = res.get("delineation") or {}
            if _HAS_MAP:
                bounds = delineation.geojson_bounds(res.get("watershed_geojson"),
                                                    res.get("reach_geojson"))
                if bounds:
                    _MAP.fit_bounds(bounds)
                elif d.get("snapped_lat") is not None:
                    _MAP.center = (d["snapped_lat"], d["snapped_lon"])
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not draw the basin on the map: {exc}",
                                 type="error", duration=8)
            return False
        return True

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
        # Every site: the STAF site engine computes the HR reach watershed and
        # the assessment reach (2026-09-05) at the typed length. There is no
        # basin to look up.
        _launch_engine(lat, lon, float(input.reach_ft() or DEFAULT_REACH_FT))

    def _with_anchor(d):
        """The delineation with the StreamCat reach attached. The reach resolves
        in the background right after the snap; the engine can finish first,
        so the pull reads the reach again when the Assessment opens."""
        with reactive.isolate():
            a = site_anchor()
        if not d or not a or d.get("siteAnchor") == a:
            return d
        out = dict(d)
        out["siteAnchor"] = a
        ci = dict(out.get("ctx_inputs") or {})
        ci["siteAnchor"] = a
        cid = comid_anchor.comid(a)
        if ci.get("comid") is None:
            ci["comid"] = cid
        out["ctx_inputs"] = ci
        dl = dict(out.get("delineation") or {})
        if dl.get("comid") is None:
            dl["comid"] = cid
        out["delineation"] = dl
        return out

    @reactive.effect
    def _engine_done():
        st = engine_task.status()
        if st in ("initial", "running"):
            return
        ui.notification_remove("engine")
        stage.set("")
        try:
            res = engine_task.result()
        except Exception as exc:  # noqa: BLE001
            res = {"record": {"status": "failed", "reason": str(exc)}}
        rec = res.get("record") or {}
        status = rec.get("status") or "failed"
        state = {"status": status, "reason": rec.get("reason"),
                 "record": engine_prefill.strip_geometry(rec) if status == "ok" else None}
        engine_state.set(state)
        if status != "ok":
            _offer_no_watershed(state, res)
            return
        out = _with_anchor(pipeline.delineate_from_engine(rec, res["lat"], res["lon"],
                                                          res["reach_ft"]))
        if not _draw_delineation(out):
            return
        delin.set(out)
        current_step.set(STEP_BASIN)

    def _offer_no_watershed(state: dict, res: dict):
        """The engine failed or refused. With a StreamCat reach the assessor can
        continue without a watershed polygon: watershed values then come from
        the StreamCat lookup engine, labeled with the reach they describe.
        Without one the site stays on Identify so Delineate can retry."""
        reason = state.get("reason") or state.get("status") or "no detail"
        with reactive.isolate():
            anchor = site_anchor()
            pt = snapped_point()
        if comid_anchor.comid(anchor) is None:
            ui.notification_show(
                "The STAF site engine could not compute the HR reach watershed "
                f"({reason}), and no StreamCat reach is known for this point. Delineate "
                "again to retry, or pick a point farther downstream.",
                type="warning", duration=12)
            return
        _no_watershed.clear()
        _no_watershed.update({"anchor": anchor, "hr_hit": pt, "lat": res["lat"],
                              "lon": res["lon"], "reach_ft": res["reach_ft"],
                              "engine": state})
        ui.modal_show(ui.modal(
            ui.markdown(
                "The STAF site engine could not compute the HR reach watershed for this "
                f"stream ({reason}).\n\n"
                "You can continue with the StreamCat lookup engine: the watershed values "
                f"then describe the NHDPlus V2 basin of {comid_anchor.reach_text(anchor)}, "
                "every such value says so, and no watershed is drawn. Or Delineate again "
                "to retry."),
            title="Watershed not available",
            footer=ui.TagList(ui.modal_button("Cancel"),
                              ui.input_action_button("use_streamcat",
                                                     "Continue with StreamCat values",
                                                     class_="btn-primary")),
            easy_close=True))

    @reactive.effect
    @reactive.event(input.use_streamcat)
    def _use_streamcat():
        ui.modal_remove()
        if not _no_watershed:
            return
        o = dict(_no_watershed)
        _no_watershed.clear()
        out = pipeline.delineate_without_watershed(o["anchor"], o["lat"], o["lon"],
                                                   o["hr_hit"], o["reach_ft"], o["engine"])
        if not _draw_delineation(out):
            return
        delin.set(out)
        current_step.set(STEP_BASIN)

    # ---- step navigation ----
    @reactive.effect
    @reactive.event(input.to_review)
    def _go_review():
        current_step.set(STEP_REVIEW)

    @reactive.effect
    @reactive.event(input.back_to_basin)
    def _go_basin():
        current_step.set(STEP_BASIN)

    @reactive.effect
    @reactive.event(input.step_nav)
    def _stepper_nav():
        target = (input.step_nav() or {}).get("key")
        if target not in dict(STEP_LABELS):
            return
        has_delin = delin() is not None
        if target == STEP_IDENTIFY:
            current_step.set(STEP_IDENTIFY)
        elif target in (STEP_BASIN, STEP_REVIEW, STEP_REPORT) and has_delin:
            if target == STEP_REPORT and not _any_scored():
                ui.notification_show("Score at least one function before viewing the report.",
                                     type="message", duration=4)
                return
            current_step.set(target)
            if target == STEP_REPORT:
                ui.modal_show(_report_modal())
        else:
            ui.notification_show("Finish the earlier steps first.", type="message", duration=2)

    def _do_reset():
        for k in ("ws", "reach", "marker", "route", "scored"):
            _remove_layer(k)
        site_anchor.set(None); evidence_reach.set(None); _no_watershed.clear()
        snapped_point.set(None); delin.set(None); stage.set("")
        engine_state.set({"status": "idle"})
        metric_scores.set({}); function_scores.set({}); current_fn.set(0)
        evidence.set({}); xs_geom.set(None)
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
        has_state = delin() is not None or bool(metric_scores()) or bool(function_scores())
        if not has_state:
            _do_reset()
            return
        ui.modal_show(ui.modal(
            ui.markdown("Clear all scores, notes, photos, and the delineation and start a new "
                        "assessment? This can't be undone. Use **Save** first if you want to keep it."),
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
                "**SFARI**, the Stream Functional Assessment Rapid Index.\n\n"
                "A rapid, field-based stream assessment. From a clicked point this app "
                "delineates the upstream watershed and an assessment reach, pulls national "
                "desktop GIS evidence to *support* your scoring, and walks you function by "
                "function to Likert-score metrics and assign each of 20 stream functions a "
                "0–15 score. Scores roll up to Physical / Chemical / Biological outcome "
                "sub-indices and an overall Ecosystem Condition Index.\n\n"
                "Two watershed engines answer the desktop evidence. The STAF site engine "
                "computes the HR reach watershed: the drainage area of the high-resolution "
                "NHD reach the point snaps to, built from NHDPlus HR catchments and checked "
                "against the reach's published drainage area. The reach, not the point, is "
                "the outlet. The StreamCat lookup engine answers by NHDPlus V2 COMID: the "
                "EPA modeled indices that exist only per V2 reach, and a labeled stand-in "
                "for a watershed value the site engine could not compute. On a stream "
                "outside V2 that COMID is the nearest StreamCat reach downstream, named with "
                "the routed distance and the drainage-area ratio on every such value."),
            title="About SFARI", easy_close=True, footer=ui.modal_button("Close")))

    @reactive.effect
    @reactive.event(input.nav_help)
    def _help():
        ui.modal_show(ui.modal(
            ui.markdown(
                "1. **Identify**: zoom in and click any stream, or type coordinates, or "
                "search a place. Dark blue lines are the NHDPlus V2 reaches the StreamCat "
                "lookup engine covers; cyan lines are every other NHD stream. Every click "
                "snaps to the high-resolution NHD. Set the reach length and click "
                "**Delineate**. The STAF site engine computes the HR reach watershed and "
                "the assessment reach, usually in under a minute.\n"
                "2. **Basin**: review the watershed and reach.\n"
                "3. **Assessment**: for each function, review the pulled evidence, "
                "Likert-score each metric, and assign the 0–15 function score. "
                "Each value carries a badge: HR reach watershed (STAF site engine), StreamCat "
                "(by COMID, naming the reach it describes) or desktop (direct services). "
                "Some values carry a suggested rating. Every "
                "score stays yours to set.\n"
                "4. **Report**: review the screening report and export.\n\n"
                "Address search uses OpenStreetMap data (Photon and Nominatim)."),
            title="How to use SFARI", easy_close=True, footer=ui.modal_button("Close")))

    # ---- left pane (per-step form) ----
    @render.ui
    def leftpane():
        step = current_step()
        if step == STEP_IDENTIFY:
            with reactive.isolate():
                picked = snapped_point() is not None
            body = ui.TagList(
                ui.div("Zoom in and click a stream, search a place, or enter coordinates.",
                       class_="easi-instr"),
                ui.input_text("address", "Address, place, or stream",
                              placeholder="e.g. Atlanta, GA  ·  Utoy Creek"),
                ui.input_action_button("find_address", "Find on map",
                                       class_="btn-outline-secondary btn-sm"),
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
                ui.div(ui.input_action_button("clear_basin", "Clear",
                                              class_="btn-outline-secondary"),
                       ui.input_action_button("to_review", "Continue",
                                              class_="btn-primary"),
                       class_="easi-pane-actions"))
        else:  # review / report -> the full-width worksheet overlay replaces the left pane
            return None
        active = current_step()
        head_label = dict(STEP_LABELS).get(active, "SFARI")
        return ui.TagList(
            ui.div(f"SFARI · {head_label}", class_="easi-pane-head"),
            ui.div(_stepper(active), body, class_="easi-pane-body"),
        )

    @render.ui
    def snap_status():
        pt = snapped_point()
        if not pt:
            return ui.p("No point yet.", class_="easi-snap-note")
        lines = [
            ui.p(f"✓ Snapped to a stream ({pt[2]:.0f} ft away).", class_="easi-snap-note ok"),
            ui.p("The STAF site engine calculates the HR reach watershed, usually in under "
                 "a minute.", class_="easi-snap-note")]
        reach_line = comid_anchor.snap_line(site_anchor())
        if reach_line:
            lines.append(ui.p(reach_line, class_="easi-snap-note"))
        return ui.TagList(*lines)

    @render.ui
    def basin_card():
        d_all = delin() or {}
        d = d_all.get("delineation") or {}
        if not d:
            return None

        def row(label, val):
            return ui.div(ui.span(label), ui.tags.b(str(val)), class_="b-row")
        # The EASI pane's rows (2026-09-04): the engine, the drainage area once,
        # the reach length, the ids that matter. The rest stays in the report.
        rows = [row("Watershed engine", _watershed_engine_text(
                    d_all, engine_state(), engine_task.status() == "running")),
                row("Drainage area", _fmt_km2(d.get("drainage_area_sqkm"))),
                row("Reach length", _fmt_ft(d.get("reach_length_ft")))]
        if d.get("nhdplus_id") is not None:
            rows.append(row("NHDPlusID", d.get("nhdplus_id")))
        rows.append(row("StreamCat reach",
                        comid_anchor.reach_text(d_all.get("siteAnchor") or site_anchor()
                                                or comid_anchor.synthetic(d.get("comid")))))
        return ui.div(
            ui.h5(d.get("gnis_name") or "(unnamed reach)"),
            *rows,
            ui.output_ui("engine_line"),
            class_="easi-basin-card",
        )

    @render.ui
    def engine_line():
        running = engine_task.status() == "running"
        if running:
            reactive.invalidate_later(1.0)
        return _engine_line_ui(engine_state(), running, _engine_prog)

    @render.ui
    def engine_line_ws():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        running = engine_task.status() == "running"
        if running:
            reactive.invalidate_later(1.0)
        return _engine_line_ui(engine_state(), running, _engine_prog)

    @render.text
    def busy_text():
        s = stage()
        running = (engine_task.status() == "running"
                   or (_HAS_MAP and anchor_task.status() == "running"))
        return s if (s and running) else ""

    @render.ui
    def stream_legend():
        # Reads the zoom flag, the fetch mode, and the reach, never the view
        # itself, so a pan does not re-render it.
        if not _HAS_MAP:
            return None
        return _legend_ui(current_step(), zoomed_in(), streams_mode(), evidence_reach(),
                          comid_anchor.is_routed(site_anchor()))

    @render.ui
    def readout():
        if not _HAS_MAP:
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
        fetching = streams_task.status() == "running"
        if z is None or z < FLOW_ZOOM or not fetching:
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
            ".easi-map-wrap .leaflet-container.leaflet-dragging .leaflet-grab"
            "{cursor:grabbing !important;}")

    # ======================================================================= #
    # Phase 2 — field-review worksheet + live rollup + report
    # ======================================================================= #
    # ---- edit bridge (JS setInputValue -> shadow score dicts) ----
    @reactive.effect
    @reactive.event(input.likert_set)
    def _on_likert():
        ev = input.likert_set() or {}
        mid = ev.get("mid")
        if not mid:
            return
        ms = dict(metric_scores()); cur = dict(ms.get(mid, {}))
        val = ev.get("val")
        cur["likert"] = val if val else None
        ms[mid] = cur; metric_scores.set(ms)

    @reactive.effect
    @reactive.event(input.metric_note_set)
    def _on_metric_note():
        ev = input.metric_note_set() or {}
        mid = ev.get("mid")
        if not mid:
            return
        ms = dict(metric_scores()); cur = dict(ms.get(mid, {}))
        cur["note"] = ev.get("note", "")
        ms[mid] = cur; metric_scores.set(ms)

    @reactive.effect
    @reactive.event(input.metric_photo_add)
    def _on_photo_add():
        ev = input.metric_photo_add() or {}
        mid, pid, uri = ev.get("mid"), ev.get("id"), ev.get("uri")
        if not mid or not pid or not uri:
            return
        ms = dict(metric_scores()); cur = dict(ms.get(mid, {}))
        photos = list(cur.get("photos", []))
        if len(photos) < 6 and not any(p.get("id") == pid for p in photos):
            photos.append({"id": pid, "uri": uri})
        cur["photos"] = photos
        ms[mid] = cur; metric_scores.set(ms)

    @reactive.effect
    @reactive.event(input.metric_photo_remove)
    def _on_photo_remove():
        ev = input.metric_photo_remove() or {}
        mid, pid = ev.get("mid"), ev.get("id")
        if not mid or not pid:
            return
        ms = dict(metric_scores()); cur = dict(ms.get(mid, {}))
        cur["photos"] = [p for p in cur.get("photos", []) if p.get("id") != pid]
        ms[mid] = cur; metric_scores.set(ms)

    @reactive.effect
    @reactive.event(input.fnscore_set)
    def _on_fnscore():
        ev = input.fnscore_set() or {}
        fid = ev.get("fid")
        if not fid:
            return
        try:
            val = int(ev.get("score"))
        except (TypeError, ValueError):
            return
        fs = dict(function_scores()); cur = dict(fs.get(fid, {}))
        cur["score"] = max(0, min(15, val))
        fs[fid] = cur; function_scores.set(fs)

    @reactive.effect
    @reactive.event(input.fn_note_set)
    def _on_fn_note():
        ev = input.fn_note_set() or {}
        fid = ev.get("fid")
        if not fid:
            return
        fs = dict(function_scores()); cur = dict(fs.get(fid, {}))
        cur["note"] = ev.get("note", "")
        fs[fid] = cur; function_scores.set(fs)

    # ---- navigation between functions ----
    @reactive.effect
    @reactive.event(input.nav_move)
    def _nav_move():
        d = int((input.nav_move() or {}).get("d", 0) or 0)
        current_fn.set(max(0, min(len(FN_IDS) - 1, current_fn() + d)))

    @reactive.effect
    @reactive.event(input.nav_jump)
    def _nav_jump():
        i = (input.nav_jump() or {}).get("i")
        if i is not None:
            current_fn.set(max(0, min(len(FN_IDS) - 1, int(i))))

    @reactive.extended_task
    async def pull_task(ctx_inputs: dict, progress: dict, engine: "dict | None" = None) -> dict:
        return await pipeline.pull_evidence_only(ctx_inputs, progress=progress, engine=engine)

    @reactive.effect
    @reactive.event(input.to_review)
    def _enter_review():
        current_fn.set(0)

    @reactive.effect
    def _maybe_pull():
        """The desktop pull when the Assessment opens (DEEP's ``_maybe_compute``,
        2026-09-07). The StreamCat reach resolves in the background right after
        the snap; until it lands the pull would run without a COMID and every
        COMID-keyed index would come back unavailable for good, so wait for it,
        attach it, then pull once."""
        if current_step() != STEP_REVIEW:
            return
        d = delin()
        if not d or not d.get("ctx_inputs"):
            return
        with reactive.isolate():
            already = bool(evidence())
            es = engine_state()
            pulling = pull_task.status() == "running"
        if already or pulling:
            return
        if _HAS_MAP and anchor_task.status() == "running":
            reactive.invalidate_later(0.5)
            return
        d2 = _with_anchor(d)
        if d2 is not d:
            delin.set(d2)
            return                                   # re-runs on the anchored delineation
        _pull_prog["done"], _pull_prog["total"] = 0, 0
        pull_task(d2["ctx_inputs"], _pull_prog, es)
        ui.notification_show("Pulling desktop evidence…", id="pull",
                             type="message", duration=None)

    @reactive.effect
    def _pull_poll():
        if pull_task.status() != "running":
            # The last progress toast outlived the pull when the completion
            # flush ran this effect after _pull_done's remove (2026-09-07):
            # the effect that shows the toast also clears it.
            ui.notification_remove("pull")
            return
        reactive.invalidate_later(0.4)
        done, total = _pull_prog.get("done", 0), _pull_prog.get("total", 0)
        ui.notification_show(f"Pulling desktop evidence… {done}/{total}", id="pull",
                             type="message", duration=None)

    @reactive.effect
    def _pull_done():
        st = pull_task.status()
        if st in ("initial", "running"):
            return
        ui.notification_remove("pull")
        try:
            res = pull_task.result()
        except Exception:
            return
        if res.get("status") == "ok":
            # MERGE (not replace): a re-run must not wipe attached cross-section
            # (Manning) entries. First run has nothing to preserve, so this equals
            # the pulled dict. Read current evidence isolated to avoid a self-loop.
            with reactive.isolate():
                cur = evidence()
                es = engine_state()
                d = delin()
            merged = pipeline.merge_pulled_evidence(cur, res.get("evidence") or {})
            evidence.set(merged)
            # The engine settled while this pull ran: pull once more so the pending
            # rows pick up the HR reach watershed values. A settled state yields no
            # pending rows, so this runs at most once.
            settled = es.get("status") in ("ok", "failed", "refused", "unavailable")
            stale = any(e.get("status") == "pending"
                        for e in merged.values() if isinstance(e, dict))
            if settled and stale and d and d.get("ctx_inputs"):
                _pull_prog["done"], _pull_prog["total"] = 0, 0
                pull_task(d["ctx_inputs"], _pull_prog, es)
                ui.notification_show("Updating desktop evidence…", id="pull",
                                     type="message", duration=None)

    # ---- live rollup ----
    @reactive.calc
    def scored():
        fs = function_scores()
        scores = {fid: v["score"] for fid, v in fs.items() if v.get("score") is not None}
        return scoring.score_assessment(scores)

    def _fn_suggest_value(fid):
        ms = metric_scores()
        likerts = [(ms.get(m["metricId"], {}) or {}).get("likert")
                   for m in METRICS_BY_FN.get(fid, [])]
        return scoring.likert_to_score([x for x in likerts if x])

    # ---- worksheet (3-column overlay) ----
    @render.ui
    def worksheet():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        return ui.div(
            ui.div(
                ui.div("SFARI · Assessment", class_="easi-pane-head"),
                ui.div(_stepper(current_step()), class_="sfari-nav-steps"),
                ui.tags.button("Field Forms",
                               {"data-desktop-metrics": "1", "type": "button",
                                "title": "The blank field-form pages, and the desktop metrics "
                                         "PDF with your pulled values"},
                               class_="sfari-btn sfari-nav-desktop"),
                ui.output_ui("engine_line_ws"),
                ui.output_ui("fn_nav"),
                class_="sfari-nav"),
            ui.div(ui.output_ui("fn_panel"), class_="sfari-fnpanel"),
            ui.div(ui.output_ui("rollup_rail"), class_="sfari-rollup"),
            class_="sfari-worksheet")

    @render.ui
    def fn_nav():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        cur = current_fn(); fs = function_scores()
        items = []
        idx = 0
        for cat in CATEGORY_ORDER:
            items.append(ui.div(cat, class_="sfari-nav-cat"))
            for f in FNS_BY_CAT.get(cat, []):
                rec = fs.get(f["id"], {})
                if rec.get("score") is not None:
                    dot = scoring.function_score_band_color(rec["score"])
                else:
                    dot = "#dfe4ec"
                cls = "sfari-nav-fn" + (" active" if idx == cur else "")
                items.append(ui.div(ui.span(class_="sfari-nav-dot", style=f"background:{dot};"),
                                    ui.span(f["name"]),
                                    {"data-idx": str(idx)}, class_=cls))
                idx += 1
        return ui.TagList(*items)

    @render.ui
    def fn_panel():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        idx = current_fn()
        ev_map = evidence()
        pulling = (pull_task.status() == "running"
                   or (_HAS_MAP and anchor_task.status() == "running"))
        fid = FN_IDS[idx]; f = FN_BY_ID[fid]
        with reactive.isolate():
            ms = metric_scores(); fs = function_scores()
        rec = fs.get(fid, {})
        metric_blocks = []
        for m in METRICS_BY_FN.get(fid, []):
            mid = m["metricId"]
            rc = ms.get(mid) or {}
            sel = rc.get("likert"); note = rc.get("note", "")
            ds = m.get("desktopSource")
            edata = ev_map.get(mid)
            if edata and edata.get("status") == "ok":
                sug = edata.get("suggested_likert")
                chip = (ui.tags.button(f"use {config.LIKERT_SHORT.get(sug, sug)}",
                                       {"data-mid": mid, "data-val": sug, "type": "button",
                                        "title": f"Use the suggested rating ({sug})"},
                                       class_="sfari-suggest-chip") if sug else None)
                blabel, bcls = _ev_badge(edata)
                ev = ui.div(ui.span(blabel, class_=bcls),
                            ui.tags.b(edata.get("value_text", ""), class_="sfari-ev-val"),
                            _info(_ev_tip(edata)), chip, _ev_describes(edata),
                            class_="sfari-evidence")
            elif edata and edata.get("status") == "pending":
                ev = ui.div(ui.span(_PENDING_BADGE[0], class_=_PENDING_BADGE[1]),
                            ui.span("The STAF site engine is computing the HR reach watershed…",
                                    class_="sfari-ev-val muted"),
                            (_info(edata.get("note", "")) if edata.get("note") else None),
                            class_="sfari-evidence pending")
            elif edata and edata.get("status") == "unavailable":
                url = (ds or {}).get("url")
                link = (ui.tags.a("look it up ↗", {"href": url, "target": "_blank",
                                                   "rel": "noopener"}) if url else None)
                blabel, bcls = _ev_badge(edata)
                has_tip = bool(edata.get("note") or edata.get("fallback_reason"))
                ev = ui.div(ui.span(blabel, class_=bcls),
                            ui.span("Not available. Review in the field.", class_="sfari-ev-val muted"),
                            link, (_info(_ev_tip(edata)) if has_tip else None),
                            class_="sfari-evidence")
            elif ds and pulling:
                ev = ui.div(ui.span("desktop", class_="sfari-ev-tag"),
                            ui.span("Pulling desktop evidence…", class_="sfari-ev-val muted"),
                            class_="sfari-evidence pending")
            elif ds:
                url = ds.get("url")
                link = (ui.tags.a("↗", {"href": url, "target": "_blank", "rel": "noopener",
                                        "title": "Open resource"}) if url else None)
                ev = ui.div(ui.span("desktop", class_="sfari-ev-tag"),
                            ui.span(ds.get("label", ""), class_="sfari-ev-val muted"), link,
                            class_="sfari-evidence")
            else:
                ev = ui.div(ui.span("field", class_="sfari-ev-tag field"),
                            ui.span("Field observation only.", class_="sfari-ev-val muted"),
                            class_="sfari-evidence")
            opts = [ui.tags.option("Rate…", {"value": ""})]
            for lv in list(config.LIKERT_ORDER) + [config.LIKERT_NA]:
                oattrs = {"value": lv}
                if sel == lv:
                    oattrs["selected"] = "selected"
                opts.append(ui.tags.option(lv, oattrs))
            rate = ui.tags.select(*opts, {"data-mid": mid, "aria-label": f"Rate: {m['name']}"},
                                  class_="sfari-likert-select" + (" set" if sel else ""))
            photos = rc.get("photos", []) or []
            thumbs = [
                ui.span(
                    ui.tags.img({"src": p.get("uri", "")}, class_="sfari-thumb"),
                    ui.tags.button("×", {"data-mid": mid, "data-id": p.get("id"), "type": "button"},
                                   class_="sfari-photo-rm"),
                    {"data-mid": mid, "data-id": p.get("id")}, class_="sfari-thumb-wrap")
                for p in photos
            ]
            photos_row = ui.div(
                *thumbs,
                ui.tags.label("📷 Photo",
                              ui.tags.input({"type": "file", "accept": "image/*",
                                             "capture": "environment", "data-mid": mid},
                                            class_="sfari-photo"),
                              class_="sfari-photo-btn"),
                {"data-mid": mid}, class_="sfari-photos")
            has_note = bool((note or "").strip())
            # Show the short field-form agreement statement; the longer library
            # metricStatement stays in the "how to score" tooltip (_criteria_tip_html).
            stmt = (m.get("fieldStatement") or m.get("metricStatement") or "").strip()
            name_row = ui.div(
                ui.span(m["name"], class_="sfari-metric-title"),
                ui.span(m.get("scale", "R"),
                        {"title": "Watershed-scale metric" if m.get("scale") == "W"
                         else "Reach-scale metric"}, class_="sfari-metric-scale"),
                _info(html_tip=_criteria_tip_html(m)),
                ui.div(
                    ui.tags.button("✎", {"data-toggle": "note", "type": "button", "title": "Add a note"},
                                   class_="sfari-metric-toggle" + (" on" if has_note else "")),
                    ui.tags.button("📷", {"data-toggle": "photo", "type": "button", "title": "Add a photo"},
                                   class_="sfari-metric-toggle" + (" on" if photos else "")),
                    class_="sfari-metric-actions"),
                class_="sfari-metric-name")
            mcls = ("sfari-metric" + (" show-note" if has_note else "")
                    + (" show-photo" if photos else ""))
            metric_blocks.append(ui.div(
                ui.div(
                    name_row,
                    (ui.div(stmt, class_="sfari-metric-statement")
                     if stmt and stmt != m["name"] else None),
                    ev,
                    class_="sfari-metric-main"),
                ui.div(rate, class_="sfari-metric-rate"),
                ui.tags.textarea(note, {"data-mid": mid, "placeholder": "Note (optional)…"},
                                 class_="sfari-metric-note"),
                photos_row,
                class_=mcls))
        score = rec.get("score")
        sval = score if score is not None else 8
        if score is not None:
            band_lbl = scoring.index_band_label(sval / 15.0)
            band_col = scoring.function_score_band_color(sval)
        else:
            band_lbl = "Not scored yet"; band_col = "#e7ebf1"
        has_fnnote = bool((rec.get("note") or "").strip())
        card_cls = ("sfari-scorecard" + ("" if score is not None else " unset")
                    + (" show-fnnote" if has_fnnote else ""))
        scorecard = ui.div(
            ui.div(
                ui.span("2", class_="sfari-step-num"),
                ui.span("Score this function", class_="sfari-fscore-lbl"),
                _info(html_tip=(
                    '<div class="easi-tip-sec">Your professional-judgment 0-15 score, '
                    'using the evidence above.</div>'
                    '<div class="easi-tip-sec">'
                    '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span>'
                    '<span><b>0-5:</b> Non-Functioning</span></div>'
                    '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span>'
                    '<span><b>6-10:</b> Functioning-at-Risk</span></div>'
                    '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span>'
                    '<span><b>11-15:</b> Functioning</span></div>'
                    '</div>')),
                ui.tags.button("✎", {"data-toggle": "fnnote", "type": "button",
                                     "title": "Add justification / notes"},
                               class_="sfari-metric-toggle" + (" on" if has_fnnote else "")),
                class_="sfari-fscore-head"),
            ui.p(f.get("functionStatement", ""), class_="sfari-fn-statement"),
            ui.div(
                ui.tags.input({"type": "range", "min": "0", "max": "15", "step": "1",
                               "value": str(sval), "data-fid": fid}, class_="sfari-fscore"),
                ui.span(str(score) if score is not None else "–", class_="sfari-fscore-num"),
                ui.span(band_lbl, class_="sfari-fscore-band", style=f"background:{band_col};"),
                class_="sfari-fscore-row"),
            ui.output_ui("fn_suggest"),
            ui.tags.textarea(rec.get("note", ""),
                             {"data-fid": fid,
                              "placeholder": "Justification / notes (especially if the score differs "
                                             "from the suggestion)…"},
                             class_="sfari-fn-note"),
            class_=card_cls)
        fn_head = ui.div(
            ui.div(f"Function {idx + 1} of {len(FN_IDS)} · {f['category']}",
                   class_="sfari-fn-eyebrow"),
            ui.div(f["name"], class_="sfari-fn-name"),
            class_="sfari-fn-head")
        total = len(METRICS_BY_FN.get(fid, []))
        rated = sum(1 for mm in METRICS_BY_FN.get(fid, [])
                    if (ms.get(mm["metricId"]) or {}).get("likert"))
        prev_attrs = {"data-nav": "-1", "type": "button"}
        if idx == 0:
            prev_attrs["disabled"] = "disabled"
        left_btns = [ui.tags.button("‹ Previous", prev_attrs, class_="sfari-btn")]
        if fid in HYDRAULICS_FNS:
            left_btns.append(ui.tags.button("Cross-section hydraulics",
                                            {"data-xs": fid, "type": "button"}, class_="sfari-btn"))
        scored_yet = rec.get("score") is not None
        actions = ui.div(
            ui.div(*left_btns, class_="sfari-foot-left"),
            ui.div(ui.span(f"{rated}/{total} rated", class_="sfari-foot-rated"),
                   ui.span("·"),
                   ui.span("scored" if scored_yet else "score needed",
                           class_="sfari-foot-score" + (" ok" if scored_yet else "")),
                   class_="sfari-foot-status"),
            ui.tags.button("Next function ›" if idx < len(FN_IDS) - 1 else "Done",
                           {"data-nav": "1", "type": "button"}, class_="sfari-btn primary"),
            class_="sfari-nav-actions")
        return ui.div(
            fn_head,
            ui.div(
                ui.div(ui.span("1", class_="sfari-step-num"),
                       ui.span("Evidence", class_="sfari-sec-title"),
                       ui.span(f"{rated} of {total} rated", class_="sfari-sec-count"),
                       class_="sfari-sec-lbl"),
                *metric_blocks,
                class_="sfari-ev-card"),
            scorecard,
            ui.div(actions, class_="sfari-fn-footer"),
            class_="sfari-fnpanel-inner")

    @render.ui
    def fn_suggest():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        fid = FN_IDS[current_fn()]
        sug = _fn_suggest_value(fid)
        if sug is None:
            return ui.div("A suggested value appears as evidence is rated.",
                          class_="sfari-suggest-line")
        return ui.div(f"Suggested from metric Likerts: {sug:g}  ",
                      ui.tags.button("Accept", {"data-fid": fid, "data-val": f"{sug:g}",
                                                "type": "button"}, class_="sfari-accept"),
                      class_="sfari-suggest-line")

    @render.ui
    def rollup_rail():
        if current_step() not in (STEP_REVIEW, STEP_REPORT):
            return None
        sc = scored()
        eci = sc["ecosystemConditionIndex"]; subs = sc["subIndices"]
        cats = sc["categoryLabels"]; catvals = sc["categorySubIndices"]
        fs = function_scores()
        n_scored = sum(1 for v in fs.values() if v.get("score") is not None)
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
        outs = sc["outcomes"]

        def _sub_bar(label, key):
            # an outcome no scored function contributes to stays dashed, not "0.00"
            if outs[key]["max"] <= 0:
                return _bar(label, 0.0, "#e7ebf1", fmt="–")
            return _bar(label, subs[key], scoring.index_band_color(subs[key]))

        return ui.TagList(
            ui.div(ui.div("–" if n_scored == 0 else f"{eci:.2f}",
                          class_="sfari-eci" + (" empty" if n_scored == 0 else "")),
                   ui.div("Ecosystem Condition Index", class_="sfari-eci-lbl"),
                   (ui.div(ui.div(class_="sfari-eci-knob",
                                  style=f"left:{max(2.0, min(98.0, eci * 100)):.1f}%;"),
                           class_="sfari-eci-track") if n_scored else None),
                   class_="sfari-eci-box"),
            _sub_bar("Physical", "physical"),
            _sub_bar("Chemical", "chemical"),
            _sub_bar("Biological", "biological"),
            ui.h4("Functional categories", style="margin-top:15px;"),
            ui.div(*chips, class_="sfari-cat-chips"),
            ui.div(ui.tags.span(style=f"width:{(n_scored / 20) * 100:.0f}%;"),
                   class_="sfari-progress-bar"),
            ui.div(f"{n_scored} / 20 functions scored", class_="sfari-progress"),
            ui.tags.button("Open report", {"data-report": "1", "type": "button"},
                           class_="sfari-btn sfari-rollup-report"
                           + (" primary" if n_scored == len(FN_IDS) else "")),
        )

    # ---- report modal ----
    def _any_scored():
        return any(v.get("score") is not None for v in function_scores().values())

    @reactive.effect
    @reactive.event(input.open_report_evt)
    def _open_report():
        if delin() is None:
            return
        if not _any_scored():
            ui.notification_show("Score at least one function before viewing the report.",
                                 type="message", duration=4)
            return
        current_step.set(STEP_REPORT)
        ui.modal_show(_report_modal())

    def _report_modal():
        d = delin() or {}
        dl = d.get("delineation") or {}
        sc = scored()
        ms = metric_scores(); fs = function_scores(); ev = evidence()

        # -- EASI-style header: stream name + fact chips, minimap at right --
        slat = dl.get("snapped_lat"); slon = dl.get("snapped_lon")
        coord = (f"{slat:.4f}, {slon:.4f}" if slat is not None and slon is not None else "—")

        def fact(label, val):
            return ui.span(ui.tags.b(f"{label}: "), str(val), class_="easi-fact")

        # -- basin characteristics (EASI report format) --
        basin_rows = [("NHDPlusID", dl.get("nhdplus_id")), ("HUC8", dl.get("huc8")),
                      ("Drainage area", _fmt_km2(dl.get("drainage_area_sqkm"))),
                      ("Stream order", dl.get("stream_order")),
                      ("Watershed basis", report.watershed_basis_label(d))]
        basin = ui.tags.details(
            ui.tags.summary("Basin characteristics", class_="easi-section-title easi-rollup-sum"),
            ui.tags.table(ui.tags.tbody(
                *[ui.tags.tr(ui.tags.th(k), ui.tags.td(str(v))) for k, v in basin_rows]),
                class_="easi-tbl", style="max-width:560px;"),
            class_="easi-rollup", open=True, style="margin:6px 0 0;")

        # Basin table sits in the header's left column so it fills the space
        # beside the minimap instead of leaving a gap below the fact chips.
        minimap = _geo_svg(d.get("watershed_geojson"), d.get("reach_geojson"))
        header = ui.div(
            ui.div(
                ui.div(
                    ui.h3(dl.get("gnis_name") or "(unnamed reach)"),
                    ui.div(fact("Analysis Point", coord),
                           fact("Reach", f'{dl.get("reach_length_ft")} ft upstream'),
                           class_="easi-facts"),
                    class_="easi-summary-head"),
                basin,
                style="flex:1 1 380px;min-width:0;"),
            (ui.HTML(minimap) if minimap else None),
            style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;")

        # -- metric evidence: one collapsed expander per discipline, functions as group rows --
        disc_blocks = []
        for cat in CATEGORY_ORDER:
            rows = []; n_rated = 0; n_total = 0
            for f in FNS_BY_CAT.get(cat, []):
                rows.append(ui.tags.tr(ui.tags.td(f["name"], {"colspan": "3"}),
                                       class_="sfari-rep-fn"))
                for m in METRICS_BY_FN.get(f["id"], []):
                    mid = m["metricId"]
                    rc = ms.get(mid) or {}
                    ed = ev.get(mid) or {}
                    lk = rc.get("likert")
                    n_total += 1
                    if lk:
                        n_rated += 1
                    cell = (_chip(config.LIKERT_SHORT.get(lk, "N/A"), _likert_color(lk))
                            if lk else ui.span("—", style="color:#aab;"))
                    evtxt = ed.get("value_text") or ("field only" if not m.get("desktopSource") else "—")
                    note = (rc.get("note") or "").strip()
                    photos = rc.get("photos", []) or []
                    rows.append(ui.tags.tr(
                        ui.tags.td(
                            m["name"],
                            (ui.div(note, class_="sfari-rep-note") if note else None),
                            (ui.div(*[ui.tags.img({"src": p.get("uri", "")}) for p in photos],
                                    class_="sfari-report-photos") if photos else None)),
                        ui.tags.td(cell),
                        ui.tags.td(evtxt, class_="sfari-rep-ev")))
            disc_blocks.append(ui.tags.details(
                ui.tags.summary(ui.span(cat), ui.span(f"{n_rated} of {n_total} rated",
                                                      class_="sfari-rep-cnt"),
                                class_="easi-rollup-sum sfari-rep-sum"),
                ui.tags.table(
                    ui.tags.thead(ui.tags.tr(ui.tags.th("Metric"), ui.tags.th("Likert"),
                                             ui.tags.th("Desktop evidence"))),
                    ui.tags.tbody(*rows), class_="easi-tbl sfari-rep-tbl"),
                class_="easi-rollup sfari-rep-disc"))
        evidence_tools = ui.div(
            ui.tags.button("Expand all", {"data-rep-expand": "1", "type": "button"},
                           class_="sfari-rep-toggle"),
            ui.tags.button("Collapse all", {"data-rep-expand": "0", "type": "button"},
                           class_="sfari-rep-toggle"),
            class_="sfari-rep-tools")

        # -- two-panel summary (EASI layout): function bars left, indices right --
        def leg(items):
            return ui.div(*[ui.span(ui.span(class_="easi-leg-sw", style=f"background:{c};"),
                                    t, class_="easi-leg-item") for c, t in items],
                          class_="easi-plot-legend")

        fn_blocks = []
        for cat in CATEGORY_ORDER:
            bars = []
            for f in FNS_BY_CAT.get(cat, []):
                rec = fs.get(f["id"], {})
                if rec.get("score") is not None:
                    bars.append(_bar(f["name"], rec["score"],
                                     scoring.function_score_band_color(rec["score"]),
                                     vmax=15, fmt="{:.0f}"))
                else:
                    bars.append(_bar(f["name"] + " (unscored)", 0, "#eef1f6", vmax=15, fmt="{:.0f}"))
            fn_blocks.append(ui.div(ui.div(cat, class_="easi-fn-group"), *bars,
                                    class_="easi-fn-block"))
        left = ui.div(ui.div("Function scores (0–15)", class_="easi-plot-title"),
                      leg([(scoring.function_score_band_color(15), "Functioning 11–15"),
                           (scoring.function_score_band_color(8), "At-Risk 6–10"),
                           (scoring.function_score_band_color(0), "Non-Functioning 0–5")]),
                      *fn_blocks, class_="easi-plot-panel")
        subs = sc["subIndices"]; eci = sc["ecosystemConditionIndex"]
        cat_chips = []
        cats = sc.get("categoryLabels", {})
        for cat in CATEGORY_ORDER:
            lbl = cats.get(cat)
            col = scoring.index_band_color(sc.get("categorySubIndices", {}).get(cat, 0.0)) if lbl else "#eef1f6"
            cat_chips.append(_chip(f"{cat}: {_FNF_SHORT.get(lbl, '—') if lbl else '—'}", col))
        right = ui.div(ui.div("Condition indices", class_="easi-plot-title"),
                       leg([(scoring.index_band_color(1.0), "Functioning 0.70–1.00"),
                            (scoring.index_band_color(0.5), "At-Risk 0.40–0.69"),
                            (scoring.index_band_color(0.0), "Non-Functioning 0.00–0.39")]),
                       _bar("Ecosystem Condition Index", eci, scoring.index_band_color(eci)),
                       _bar("Physical", subs["physical"],
                            scoring.index_band_color(subs["physical"]), indent=True),
                       _bar("Chemical", subs["chemical"],
                            scoring.index_band_color(subs["chemical"]), indent=True),
                       _bar("Biological", subs["biological"],
                            scoring.index_band_color(subs["biological"]), indent=True),
                       ui.div("Functional categories", class_="easi-plot-title",
                              style="margin-top:14px;"),
                       ui.div(*[ui.span(c, style="margin-right:6px;") for c in cat_chips],
                              style="margin:4px 0 2px;"),
                       class_="easi-plot-panel")

        body = ui.div(
            header,
            ui.div("Metric evidence & Likert scores", class_="easi-section-title"),
            evidence_tools,
            *disc_blocks,
            ui.div("Summary", class_="easi-section-title"),
            ui.div(left, right, class_="easi-summary-plots"),
            ui.div("Desktop evidence supports scoring; the assessor assigns the Likert and 0–15 "
                   "function scores. Likert thresholds are national defaults. Calibrate regionally.",
                   class_="easi-disclaimer"),
            id="sfari-report")
        return ui.modal(
            body, title="SFARI Screening Report", easy_close=True, size="xl",
            footer=ui.div(ui.download_button("dl_pdf", "PDF", class_="btn-sm"),
                          ui.download_button("dl_csv", "CSV", class_="btn-sm"),
                          ui.download_button("dl_geojson", "GeoJSON", class_="btn-sm"),
                          ui.modal_button("Close"),
                          style="display:flex;gap:8px;align-items:center;"))

    # ---- the Field Forms dialog (2026-09-07) ----
    # One modal, size xl. The shell is static: the site line, the tab strip with
    # both downloads, and the Close button never re-render, so the open tab and
    # the table's scroll position survive every pull update. Three outputs
    # inside rebuild live: the status line (owns the polling while the engine
    # or the pull runs), the table (re-renders only when the evidence changes),
    # and the preview (the blank vector worksheet served inline through a session
    # route).
    @reactive.effect
    @reactive.event(input.desktop_metrics_evt)
    def _open_desktop_metrics():
        ui.modal_show(_desktop_metrics_modal())

    def _desktop_metrics_modal():
        return ui.modal(
            ui.output_ui("ff_site"),
            ui.navset_pill(
                ui.nav_panel("Desktop metrics",
                             ui.output_ui("ff_status"),
                             ui.div(ui.output_ui("ff_table"), class_="ff-table-wrap"),
                             value="metrics"),
                ui.nav_panel("Field forms preview", ui.output_ui("ff_preview"),
                             value="preview"),
                ui.nav_spacer(),
                # Each download sits in its own div: Shiny's Bootstrap styles a bare
                # ``.nav-pills > li > a`` as a nav link (link-blue text, no button
                # chrome), and the wrapper keeps the anchors real buttons.
                ui.nav_control(ui.div(ui.download_button("dl_field_forms", "Field forms PDF",
                                                         class_="btn-sm btn-primary"),
                                      class_="ff-dl")),
                ui.nav_control(ui.div(ui.download_button("dl_desktop_metrics",
                                                         "Desktop metrics PDF",
                                                         class_="btn-sm btn-primary"),
                                      class_="ff-dl")),
                id="ff_tabs", selected="metrics"),
            title="Field Forms", easy_close=True, size="xl",
            footer=ui.modal_button("Close"), class_="ff-modal-body")

    # Field-form readiness status vocabulary, derived per metric from the pulled
    # evidence + the metric's desktop source client.
    _FF_BADGE = {
        "Available": "#d8ecd8;color:#1f6b32",
        "Pending": "#eef1f6;color:#5a6478",
        "Unavailable": "#f3d9d9;color:#8a2d2d",
        "Local review required": "#e7ddf3;color:#5b3f8a",
        "Run cross-section tool": "#dce8f5;color:#2c4f7a",
    }

    def _ff_status(m: dict, ed: dict, pulling: bool) -> str:
        if ed.get("status") == "ok" and ed.get("value_text"):
            return "Available"
        client = (m.get("desktopSource") or {}).get("client")
        if client == "xscalc":
            return "Run cross-section tool"
        if client == "manual":
            return "Local review required"
        if ed.get("status") in ("unavailable", "error"):
            return "Unavailable"
        return "Pending"

    def _ff_rows(ev_map: dict, pulling: bool):
        """The 26 desktop-metric rows and the status counts, pure."""
        rows, counts = [], {}
        dim = "color:#8a93a3;font-size:11px;"
        for cat in CATEGORY_ORDER:
            for f in FNS_BY_CAT.get(cat, []):
                for m in METRICS_BY_FN.get(f["id"], []):
                    if not m.get("desktopSupportable"):
                        continue
                    ds = m.get("desktopSource") or {}
                    ed = ev_map.get(m["metricId"]) or {}
                    st = _ff_status(m, ed, pulling)
                    counts[st] = counts.get(st, 0) + 1
                    badge = ui.span(st, class_="ff-badge",
                                    style=f"background:{_FF_BADGE.get(st, '#eef1f6;color:#5a6478')};")
                    val = (ui.tags.b(ed["value_text"]) if st == "Available"
                           else ui.span("\u2014", style="color:#8a93a3;"))
                    src_url = ed.get("source_url") or ds.get("url")
                    src_name = (ed.get("source")
                                or (urlparse(ds.get("url")).netloc if ds.get("url") else None)
                                or "\u2014")
                    src = (ui.tags.a(src_name, {"href": src_url, "target": "_blank",
                                                "rel": "noopener"}) if src_url else src_name)
                    describes = (ui.span("describes the " + ed["anchor_label"], class_="ff-src-sub")
                                 if ed.get("anchor_label") else None)
                    fallback = (ui.span(ed["fallback_reason"], class_="ff-src-sub")
                                if ed.get("fallback_reason") else None)
                    rows.append(ui.tags.tr(
                        ui.tags.td(cat, style=dim),
                        ui.tags.td(f["name"], style=dim),
                        ui.tags.td(m["name"]),
                        ui.tags.td(ds.get("label") or "\u2014", style="font-size:11px;color:#45506a;"),
                        ui.tags.td(badge, style="font-size:11px;"),
                        ui.tags.td(val, style="font-size:11px;color:#2f3a52;"),
                        ui.tags.td(src, describes, fallback, style="font-size:11px;")))
        return rows, counts

    def _ff_pulling() -> bool:
        return (pull_task.status() == "running" or engine_task.status() == "running"
                or (_HAS_MAP and anchor_task.status() == "running"))

    # suspend_when_hidden=False on every dialog output: they bind while the
    # modal is still hidden (Bootstrap's fade), and a suspended output never
    # resumes here.
    @output(suspend_when_hidden=False)
    @render.ui
    def ff_site():
        d = delin() or {}
        dl = d.get("delineation") or {}
        if not dl:
            return ui.div("Delineate a reach first.", class_="ff-site")
        parts = [dl.get("gnis_name") or "(unnamed stream)", report._reach_id_str(dl)]
        coords = report._coords_str(dl)
        if coords:
            parts.append(coords)
        if dl.get("reach_length_ft") not in (None, "", "None"):
            parts.append(_fmt_ft(dl.get("reach_length_ft")))
        return ui.div(" \u00b7 ".join(parts), class_="ff-site")

    @output(suspend_when_hidden=False)
    @render.ui
    def ff_status():
        ev_map = evidence()
        status = pull_task.status()
        engine_running = engine_task.status() == "running"
        pulling = status == "running" or engine_running
        done, total = _pull_prog.get("done", 0), _pull_prog.get("total", 0)
        n = len(config.desktop_metrics())
        _rows, counts = _ff_rows(ev_map, pulling)
        if engine_running:
            reactive.invalidate_later(1.0)
            text = "Preparing field forms\u2026 " + _engine_progress_text(_engine_prog)
        elif pulling:
            reactive.invalidate_later(1.0)
            text = f"Preparing field forms\u2026 pulling desktop evidence ({done} of {total})."
        elif status == "initial":
            text = (f"{n} of the {len(METRICS_BY_ID)} metrics can be evaluated from a desk "
                    "before the site visit.")
        else:
            avail = counts.get("Available", 0)
            text = (f"{avail} of {n} desktop metrics pulled. The rest are flagged for the "
                    "field visit.")
        retry = None
        if not pulling and status not in ("initial", "running"):
            failed = status == "error"
            try:
                pull_task.result()
            except Exception:  # noqa: BLE001
                failed = True
            if failed:
                text = "The desktop evidence pull failed. " + text
                retry = ui.input_action_button("ff_retry", "Retry pull",
                                               class_="btn btn-sm btn-outline-secondary")
        return ui.div(ui.span(text, {"aria-live": "polite"}), retry, class_="ff-status")

    @output(suspend_when_hidden=False)
    @render.ui
    def ff_table():
        ev_map = evidence()
        with reactive.isolate():
            pulling = _ff_pulling()
        rows, _counts = _ff_rows(ev_map, pulling)
        return ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Discipline"), ui.tags.th("Function"),
                                     ui.tags.th("Metric"), ui.tags.th("Method"),
                                     ui.tags.th("Status"), ui.tags.th("Value"),
                                     ui.tags.th("Source"))),
            ui.tags.tbody(*rows), class_="easi-tbl ff-table", id="sfari-desktop-metrics")

    # The preview: the blank worksheet served inline by a session route.
    def _ff_preview_route(request):
        from starlette.responses import Response
        pdf = report.build_field_forms_pdf()
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": "inline; filename=sfari-field-forms.pdf",
                                 "Cache-Control": "no-store"})

    _ff_preview_url = session.dynamic_route("field-forms-preview", _ff_preview_route)

    @output(suspend_when_hidden=False)
    @render.ui
    def ff_preview():
        return ui.div(
            ui.tags.iframe({"src": _ff_preview_url, "title": "Field forms preview"},
                           class_="ff-preview-frame"),
            class_="ff-preview")

    @reactive.effect
    @reactive.event(input.ff_retry)
    def _ff_retry():
        with reactive.isolate():
            d = _with_anchor(delin())
        ci = (d or {}).get("ctx_inputs")
        if not ci:
            ui.notification_show("Delineate a reach first.", type="warning", duration=3)
            return
        with reactive.isolate():
            es = engine_state()
        _pull_prog["done"], _pull_prog["total"] = 0, 0
        pull_task(ci, _pull_prog, es)   # merges into existing evidence (merge_pulled_evidence)
        ui.notification_show("Retrying desktop evidence pull…", id="pull",
                             type="message", duration=None)

    # ---- cross-section hydraulics popup ----
    @reactive.effect
    @reactive.event(input.xs_open_evt)
    def _xs_open():
        d = delin() or {}
        dl = d.get("delineation") or {}
        da = dl.get("drainage_area_sqkm")
        if da is None:
            ui.notification_show("Delineate a reach first.", type="warning", duration=3)
            return
        bf = bieger.bankfull_geometry(da, dl.get("snapped_lat"), dl.get("snapped_lon"))
        pts, lb, rb, bf_stage = xscalc.synthetic_section(bf["width_m"], bf["depth_m"])
        xs_geom.set({"points": pts, "lb": lb, "rb": rb, "bankfull_stage": bf_stage,
                     "slope": dl.get("slope") or 0.001, "da": da,
                     "width_m": bf["width_m"], "depth_m": bf["depth_m"],
                     "division_name": bf["division_name"]})
        ui.modal_show(_xs_modal(xs_geom()))

    def _xs_modal(geom):
        slope = geom.get("slope") or 0.001
        bf = xscalc.compute(geom["points"], geom["bankfull_stage"], slope, 0.035,
                            n_lob=0.06, n_rob=0.06, lb=geom["lb"], rb=geom["rb"])
        q_bf = int(round(bf["Q"])) if bf and bf.get("Q") else 100
        return ui.modal(
            ui.div(f"Synthetic bankfull section: {geom['division_name']} regional curve "
                   f"(Bieger 2015) at DA {geom['da']:.0f} km²: bankfull width "
                   f"{geom['width_m']:.1f} m, mean depth {geom['depth_m']:.2f} m. Adjust slope, "
                   f"roughness, and target discharge; results feed the hydraulics metrics.",
                   class_="easi-instr"),
            ui.row(
                ui.column(4, ui.input_numeric("xs_slope", "Channel slope (m/m)",
                                              value=round(float(slope), 5), min=0.00001, step=0.0005)),
                ui.column(4, ui.input_numeric("xs_nchan", "Manning n (channel)", value=0.035, step=0.005)),
                ui.column(4, ui.input_numeric("xs_nover", "Manning n (overbank)", value=0.06, step=0.005)),
            ),
            ui.row(
                ui.column(6, ui.input_numeric("xs_targetq", "Target discharge Q (cfs)",
                                              value=q_bf, min=0, step=1)),
                ui.column(6, ui.input_numeric("xs_d50", "Bed D50 (mm)", value=30.0, min=0, step=1)),
            ),
            ui.output_ui("xs_view"),
            ui.div(ui.tags.button("Attach results to hydraulics metrics",
                                  {"data-xs-attach": "1", "type": "button"}, class_="sfari-btn primary"),
                   class_="easi-pane-actions"),
            title="Cross-section hydraulics", easy_close=True, size="l",
            footer=ui.modal_button("Close"))

    @reactive.calc
    def xs_calc():
        geom = xs_geom()
        if not geom:
            return None
        try:
            slope = float(input.xs_slope()); nchan = float(input.xs_nchan())
            nover = float(input.xs_nover()); tq = float(input.xs_targetq())
        except Exception:
            return None
        if slope <= 0 or tq <= 0 or nchan <= 0:
            return None
        stage = xscalc.solve_stage(geom["points"], tq, slope, nchan, n_lob=nover, n_rob=nover,
                                   lb=geom["lb"], rb=geom["rb"])
        if stage is None:
            return None
        return xscalc.compute(geom["points"], stage, slope, nchan, n_lob=nover, n_rob=nover,
                              lb=geom["lb"], rb=geom["rb"])

    def _tau_c(d50_mm):
        d50_ft = (d50_mm or 0) / 304.8
        return 0.047 * 1.65 * 62.4 * d50_ft if d50_ft > 0 else None

    @render.ui
    def xs_view():
        geom = xs_geom()
        if not geom:
            return None
        res = xs_calc()
        stage = res["stage"] if res else geom["bankfull_stage"]
        img = ui.HTML(f'<div style="margin-bottom:8px;">'
                      f'{_xs_svg(geom["points"], stage, geom["lb"], geom["rb"])}</div>')
        if not res:
            return ui.div(img, ui.p("Enter a valid slope, roughness, and discharge.",
                                    class_="text-muted", style="font-size:12px;"))
        try:
            d50 = float(input.xs_d50() or 0)
        except Exception:
            d50 = 0.0
        tc = _tau_c(d50)
        rows = [
            ("Discharge Q", f"{res['Q']:.0f} cfs"),
            ("Water-surface stage / max depth", f"{res['depth_max']:.2f} ft"),
            ("Mean velocity V", f"{res['V']:.2f} ft/s"),
            ("Top width T", f"{res['T']:.1f} ft"),
            ("Hydraulic radius R", f"{res['R']:.2f} ft"),
            ("Bed shear τ = γRS", f"{res['tau']:.3f} lb/ft²"),
            ("Unit stream power τV", f"{res['power']:.2f} lb/(ft·s)"),
            ("Froude number", f"{res['froude']:.2f} ({'super' if res['froude'] > 1 else 'sub'}critical)"),
        ]
        if tc:
            rows.append(("Critical shear τc (D50)",
                         f"{tc:.3f} lb/ft² → {'mobilizes' if res['tau'] > tc else 'stable'}"))
        table = ui.tags.table(ui.tags.tbody(
            *[ui.tags.tr(ui.tags.td(a), ui.tags.td(ui.tags.b(b))) for a, b in rows]),
            class_="easi-tbl")
        return ui.div(img, table)

    @reactive.effect
    @reactive.event(input.xs_attach_evt)
    def _xs_attach():
        res = xs_calc()
        if not res:
            ui.notification_show("Enter a valid slope and discharge first.", type="warning", duration=3)
            return
        try:
            d50 = float(input.xs_d50() or 0)
        except Exception:
            d50 = 0.0
        tc = _tau_c(d50)
        q = res["Q"]
        ev = dict(evidence())

        def put(mid, vt, note, fvt=""):
            ev[mid] = {"metric_id": mid, "value_text": vt, "field_value_text": fvt,
                       "suggested_likert": None, "confidence": "M",
                       "source": pipeline.XS_MANNING_SOURCE,
                       "source_url": "", "status": "ok", "note": note}
        put("low-flow-baseflow-dynamics-low-flow-depth",
            f"modeled max depth {res['depth_max']:.2f} ft at Q {q:.0f} cfs",
            "From the cross-section Manning solver.",
            f"Low-flow depth {res['depth_max']:.2f} ft")
        put("low-flow-baseflow-dynamics-low-flow-velocity",
            f"modeled velocity {res['V']:.2f} ft/s at Q {q:.0f} cfs",
            "From the cross-section Manning solver.",
            f"Low-flow velocity {res['V']:.2f} ft/s")
        put("high-flow-dynamics-peak-flow-capacity-velocity-shear-stress",
            f"V {res['V']:.2f} ft/s, τ {res['tau']:.3f} lb/ft², Fr {res['froude']:.2f}",
            "At the modeled stage.",
            f"Peak V {res['V']:.2f} ft/s, Fr {res['froude']:.2f}")
        mob = f" → {'mobilizes' if res['tau'] > tc else 'stable'}" if tc else ""
        put("high-flow-dynamics-bed-mobilization-frequency",
            (f"bed shear τ {res['tau']:.3f} vs critical τc {tc:.3f} lb/ft²{mob}"
             if tc else f"bed shear τ {res['tau']:.3f} lb/ft²"),
            "Shields comparison with the entered D50.",
            f"Bed shear tau {res['tau']:.3f} lb/ft2")
        evidence.set(ev)
        ui.notification_show("Cross-section results attached to the hydraulics metrics.",
                             type="message", duration=4)
        ui.modal_remove()

    # ---- exports + resumable session ----
    @render.download(filename="sfari-assessment.json")
    def save_session():
        yield session_io.dump(delin() or {}, metric_scores(), function_scores(), evidence(), xs_geom())

    @render.download(filename="sfari-report.csv")
    def dl_csv():
        yield report.build_csv(delin() or {}, metric_scores(), function_scores(), evidence(), scored())

    @render.download(filename="sfari-report.geojson")
    def dl_geojson():
        yield report.build_geojson(delin() or {}, function_scores(), scored())

    @render.download(filename="sfari-report.pdf")
    def dl_pdf():
        yield report.build_pdf(delin() or {}, metric_scores(), function_scores(), evidence(), scored())

    @render.download(filename=report.field_forms_filename())
    def dl_field_forms():
        yield report.build_field_forms_pdf()

    @render.download(filename=lambda: report.desktop_metrics_filename(delin() or {}))
    def dl_desktop_metrics():
        yield report.build_desktop_metrics_pdf(delin() or {}, evidence())

    @reactive.effect
    @reactive.event(input.load_session)
    def _load_session():
        finfo = input.load_session()
        if not finfo:
            return
        try:
            with open(finfo[0]["datapath"], encoding="utf-8") as fh:
                st = session_io.load(fh.read())
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not load assessment: {exc}", type="error", duration=6)
            return
        d = st.get("delineation") or {}
        delin.set(d)
        metric_scores.set(st.get("metric_scores") or {})
        function_scores.set(st.get("function_scores") or {})
        evidence.set(st.get("evidence") or {})
        xs_geom.set(st.get("cross_section"))
        se = d.get("siteEngine") if isinstance(d, dict) else None
        engine_state.set({"status": "ok", "record": se, "reason": None}
                         if se and se.get("status", "ok") == "ok"
                         else dict(se) if se else {"status": "idle"})
        anchor = d.get("siteAnchor") if isinstance(d, dict) else None
        site_anchor.set(anchor)
        evidence_reach.set(comid_anchor.legend_reach(anchor))
        if _HAS_MAP and d:
            for k in ("ws", "reach", "marker", "route"):
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
                    _add_layer("marker", _point_marker(dd["snapped_lat"], dd["snapped_lon"]))
                    b = delineation.geojson_bounds(d.get("watershed_geojson"), d.get("reach_geojson"))
                    if b:
                        _MAP.fit_bounds(b)
            except Exception:  # noqa: BLE001
                pass
        current_fn.set(0)
        current_step.set(STEP_REVIEW)
        ui.notification_show("Assessment loaded. Resuming.", type="message", duration=4)


app = App(app_ui, server, static_assets=Path(__file__).parent / "www")

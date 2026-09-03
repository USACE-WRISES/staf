"""EASI — Ecosystem Assessment Screening Index (Shiny for Python, Core).

A StreamStats-style workflow: zoom in until NHD stream vectors appear, click a
stream to snap a point, delineate the watershed + upstream reach, review the
basin, configure which of the 20 EASI functions to compute (and the data source
where alternatives exist), then open a polished report popup (STAF screening
summary + basin characteristics + cross-section). Field/low-confidence metrics
are overrideable; export PDF / CSV / GeoJSON.
"""
from __future__ import annotations

import html
import json
import os
import tempfile
from pathlib import Path

# HyRiver cache -> writable temp dir (Connect Cloud FS is ephemeral). Set before
# any HyRiver import so the clients pick it up.
os.environ.setdefault("HYRIVER_CACHE_NAME",
                      os.path.join(tempfile.gettempdir(), "easi_hyriver.sqlite"))
os.environ.setdefault("HYRIVER_CACHE_EXPIRE", str(7 * 24 * 3600))

import anyio  # noqa: E402
from shiny import App, reactive, render, ui  # noqa: E402

from easi import (assessment, batch_ui, bieger, config, delineation,  # noqa: E402
                  geomorph, method_plot, methods as easi_methods, pipeline, report,
                  routing, scoring)
from easi.batch import api as batch_api  # noqa: E402
from easi.batch import contracts as batch_contracts  # noqa: E402
from easi.batch import exports as batch_exports  # noqa: E402
from easi.datasources import flowlines, nhd_hr  # noqa: E402
from easi.metrics import geomorphology, hydraulics  # noqa: E402  (cross-section metric ids)
from easi.datasources.geocode import geocode_address  # noqa: E402
from easi.pipeline import DEFAULT_REACH_FT  # noqa: E402
from easi.snapcard import hr_snap_card  # noqa: E402

FT_PER_M = 3.28083989501312

try:
    from ipyleaflet import GeoJSON, LayersControl, Map, Marker, ScaleControl, TileLayer
    from ipywidgets import Layout
    from shinywidgets import output_widget, reactive_read, render_widget
    _HAS_MAP = True
except Exception:  # pragma: no cover
    _HAS_MAP = False

try:
    import plotly.graph_objects  # noqa: F401  (interactive cross-section plot)
    _HAS_PLOTLY = _HAS_MAP       # also needs shinywidgets (output_widget/render_widget)
except Exception:  # pragma: no cover
    _HAS_PLOTLY = False

WATERSHED_STYLE = {"color": "#caa700", "weight": 1, "fillColor": "#fdf24a", "fillOpacity": 0.40}
REACH_STYLE = {"color": "#d6453d", "weight": 4}
FLOWLINE_STYLE = {"color": "#1f6feb", "weight": 3, "opacity": 0.95}
# The full NHDPlus HR network drawn under the V2 scoring network: lighter and
# thinner so covered (clickable-to-score) streams stay visually primary.
HR_FLOWLINE_STYLE = {"color": "#22b8cf", "weight": 2, "opacity": 0.9}
# Hover: the line thickens under the pointer (ipyleaflet applies hover_style on
# mouseover and resets it on mouseout), which together with Leaflet's pointer
# cursor on interactive paths says "this line is clickable" (2026-09-02).
FLOWLINE_HOVER_STYLE = {"weight": 5, "opacity": 1.0}
HR_FLOWLINE_HOVER_STYLE = {"weight": 4, "opacity": 1.0}
# Dashed connector from a clicked HR-only stream to its covered surrogate reach.
ROUTE_STYLE = {"color": "#5b6472", "weight": 2, "dashArray": "6,5", "opacity": 0.9}
# === TEMP: MMW comparison overlay (remove later) ===
MMW_STYLE = {"color": "#7b2cbf", "weight": 2, "dashArray": "5,4",
             "fillColor": "#b388eb", "fillOpacity": 0.18}  # distinct from yellow WATERSHED_STYLE
SHOW_MMW_OVERLAY = False  # hides the Basin-page comparison checkbox; the server logic stays dormant
# === END TEMP ===
RATING_COLOR = {"Good": "#c8d9f2", "Fair": "#f5e7a6", "Poor": "#f5b5b5"}
_DISC_ORDER = ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]


_FINDING_TEXT = "Finding the stream…"
_LOCATING_TEXT = "Locating the nearest covered reach…"


def _fmt_ratio_limit(value):
    """10.0 -> 10 for display; anything non-integral passes through."""
    try:
        f = float(value)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return value

USGS_TOPO_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
USGS_HYDRO_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
USGS_ATTR = "USGS The National Map"
FLOW_ZOOM = 14          # NHD vectors appear at/above this zoom
SNAP_TOL_FT = 150.0     # click must land within this distance of a flowline
BATCH_UI_MAX_SITES = 10  # per-batch cap in this UI; the engine accepts batch_api.MAX_SITES (150)

STEP_IDENTIFY, STEP_BASIN, STEP_ASSESS, STEP_REPORT = "identify", "basin", "assess", "report"
STEP_LABELS = [(STEP_IDENTIFY, "Identify"), (STEP_BASIN, "Basin"),
               (STEP_ASSESS, "Assessment"), (STEP_REPORT, "Report")]

_METRICS = config.metrics_by_id()
ALL_MIDS = list(_METRICS)
OVERRIDEABLE = [mid for mid, info in config.METRIC_REGISTRY.items() if info.get("overrideable")]
OVERRIDEABLE_SET = set(OVERRIDEABLE)

# Worksheet function ordering (functions.json is already discipline-grouped) and the
# 1:1 function -> metric map (every EASI function has exactly one metric).
_FUNCTIONS = config.functions()
_METRIC_BY_FID = {m["functionId"]: m for m in _METRICS.values()}
# The three functions whose metric rating derives from the editable cross-section; their
# cards carry the cross-section editor (entrenchment, floodplain engagement, channel evolution).
XS_METRIC_IDS = {hydraulics.ENTRENCHMENT_ID, hydraulics.FLOODPLAIN_ENGAGEMENT_ID,
                 geomorphology.CHANNEL_EVOL_ID}
XS_FUNCTION_IDS = {_METRICS[mid]["functionId"] for mid in XS_METRIC_IDS if mid in _METRICS}


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def _short(text: str, n: int = 46) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _info(text: str = None, *, html_tip: str = None):
    """A small circled-'i'; the custom tooltip (www/tooltip.js) shows the tip.

    Pass ``text`` for a plain-text tooltip (``data-tip``) or ``html_tip`` for a rich
    HTML card (``data-tip-html``). The onclick guard lets the icon sit inside a
    checkbox ``<label>`` without a click on it toggling the checkbox.
    """
    attrs = {"onclick": "event.preventDefault();event.stopPropagation();"}
    if html_tip:
        attrs["data-tip-html"] = html_tip
    elif text and text.strip():
        attrs["data-tip"] = text.strip()
    else:
        return None
    return ui.span("i", attrs, class_="easi-info")


def _metric_tip_html(name, definition, source, calc, note, crit, default, land_cover=None,
                     riparian=None):
    """Build the report ⓘ tooltip card: definition, data source, calculation, then the
    scoring criteria. Source is where the input value comes from; Calculation is any extra
    computation on top of it. ``land_cover`` (the catchment-hydrology land-cover metric) swaps
    the single Scoring block for a two-indicator block showing impervious and agricultural
    cover with their thresholds, marking the governing one. ``riparian`` (the detrital metric)
    adds a natural-vegetation composition block (forest/shrub/grassland/wetland). All dynamic
    values are HTML-escaped; markup is app-controlled.
    """
    e = html.escape
    parts = [f'<div class="easi-tip-title">{e(name or "")}</div>']
    if definition:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Definition</span>'
                     f'{e(definition)}</div>')
    if source:
        # the land-cover block already states both values, so skip the redundant note sub-line there
        sub = f'<div class="easi-tip-sub">{e(note)}</div>' if note and not land_cover else ""
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Source</span>'
                     f'{e(source)}{sub}</div>')
    if calc:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Calculation</span>'
                     f'{e(calc)}</div>')
    if riparian:  # detrital metric: natural-vegetation composition of the 100 m buffer
        veg_rows = [f'<div class="easi-tip-crit">{e(lbl)} {e(str(riparian.get(k, 0)))}%</div>'
                    for k, lbl in (("forest", "Forest"), ("shrub", "Shrub"),
                                   ("grassland", "Grassland"), ("wetland", "Wetland"))]
        veg_rows.append('<div class="easi-tip-crit"><b>Natural vegetation '
                        f'{e(str(riparian.get("total", 0)))}%</b></div>')
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Riparian vegetation'
                     '</span>' + "".join(veg_rows) + "</div>")
    if land_cover:
        gov = land_cover.get("governing")
        lc_rows = []
        for key, label in (("impervious", "Impervious"), ("agriculture", "Agricultural")):
            d = land_cover.get(key)
            bands = land_cover.get(f"{key}_bands") or {}
            thr = " / ".join(f"{b} {bands[b]}" for b in ("Good", "Fair", "Poor") if bands.get(b))
            if d:
                line = f"{e(label)} {e(str(d['pct']))}% &rarr; {e(d['rating'])}"
                line += f" ({e(thr)})" if thr else ""
                dot = (f'<span class="easi-tip-dot {d["rating"].lower()}"></span>'
                       if d.get("rating") in ("Good", "Fair", "Poor") else "")
            else:
                line = f"{e(label)}: no data"
                dot = ""
            lc_rows.append('<div class="easi-tip-crit">' + dot
                           + (f"<b>{line} [governs]</b>" if key == gov else line) + "</div>")
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Land-cover indicators'
                     '</span><span class="easi-tip-default">more limiting governs</span>'
                     + "".join(lc_rows) + "</div>")
    else:
        rows = []
        for band in ("Good", "Fair", "Poor"):
            c = crit.get(band)
            if c:
                rows.append(f'<div class="easi-tip-crit"><span class="easi-tip-dot {band.lower()}">'
                            f'</span><b>{band}</b>&nbsp;{e(c)}</div>')
        if rows:
            dflt = f'<span class="easi-tip-default">default: {e(default)}</span>' if default else ""
            parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Scoring</span>'
                         f'{dflt}{"".join(rows)}</div>')
    return "".join(parts)


def _bieger_area_tip_html(current_name: str | None = None) -> str:
    """Info card listing the Bieger (2015) bankfull cross-sectional-area regressions for
    every physiographic division; the analysis point's division is bolded."""
    e = html.escape
    parts = ['<div class="easi-tip-title">Bieger bankfull XS-area curves</div>',
             '<div class="easi-tip-sec"><span class="easi-tip-lbl">Regression</span>'
             'A = a·DA<sup>b</sup>, area in m², drainage area in km² '
             '(Bieger et al. 2015, Table 3).</div>',
             '<div class="easi-tip-sec"><span class="easi-tip-lbl">Physiographic division</span>']
    lines = []
    for _abbr, name, a, b, r2 in bieger.area_equations():
        eq = f"A = {a:g}·DA<sup>{b:g}</sup> (R²={r2:.2f})"
        line = f"{e(name)}: {eq}"
        if current_name and name == current_name:
            line = f"<b>{line}</b>"
        lines.append(f'<div class="easi-tip-crit">{line}</div>')
    parts.append("".join(lines) + "</div>")
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


def staf_topnav():
    return ui.div(
        ui.tags.a("STAF", href=STAF_LINKS["home"], class_="staf-topnav-link",
                  target="_blank", rel="noopener"),
        class_="staf-topnav",
    )


app_ui = ui.page_fillable(
    ui.head_content(ui.tags.link(rel="stylesheet", href="styles.css?v=40"),
                    ui.tags.script(src="geocode-autocomplete.js", defer=""),
                    ui.tags.script(src="tooltip.js", defer=""),
                    ui.tags.script(src="report-controls.js", defer=""),
                    ui.tags.script(src="report-edit.js", defer=""),
                    ui.tags.script(src="worksheet.js?v=8", defer=""),
                    ui.tags.script(src="coord-entry.js", defer="")),
    # Disable Shiny/bslib's page-level "pulse" loading bar at the top of the screen —
    # the bottom-right toast is the app's loading indicator (output spinners unaffected).
    ui.busy_indicators.use(pulse=False),
    ui.div(
        ui.div(
            ui.span("EASI", ui.tags.small("Ecosystem Assessment Screening Index"),
                    class_="easi-brand"),
            staf_topnav(),
            ui.div(
                ui.input_action_link("nav_new", "New analysis"),
                ui.input_action_link("nav_batch", "Batch"),
                ui.input_action_link("nav_help", "Help"),
                # Extended documentation (verification & validation) — a static,
                # self-contained Quarto page served from www/. Opens in a new tab
                # so the analysis session is preserved.
                ui.tags.a("Documentation", href="documentation.html",
                          target="_blank", rel="noopener", class_="easi-doclink"),
                class_="easi-nav",
            ),
            class_="easi-header",
        ),
        ui.div(
            output_widget("map", height="100%") if _HAS_MAP
            else ui.div("Map requires ipyleaflet + shinywidgets.", class_="text-muted p-3"),
            class_="easi-map-wrap",
        ),
        ui.div(ui.output_ui("leftpane"), class_="easi-leftpane"),
        ui.output_ui("worksheet"),
        ui.output_ui("batch_workspace"),
        ui.output_ui("readout"),
        ui.output_ui("flow_loading"),
        ui.output_ui("cursor_style"),
        class_="easi-shell",
    ),
    title="EASI · Automated Stream Screening",
    padding=0,
    fillable=True,
)


# --------------------------------------------------------------------------- #
# Report rendering helpers (shared by the modal output slots)
# --------------------------------------------------------------------------- #
def _chip(text, color):
    return ui.span(text, class_="easi-chip", style=f"background:{color};")


def _bar(label, value, color, *, vmax=1.0, value_fmt="{:.2f}", indent=False):
    """One horizontal bar row: label, a track with a colored fill, and the value.

    ``vmax`` scales the fill (1.0 for 0–1 indices, 15 for 0–15 function scores);
    ``value_fmt`` formats the printed value; ``indent`` nudges the sub-index rows in so
    they read as children of the Ecosystem Condition Index above them.
    """
    pct = 0.0 if value is None else max(0.0, min(1.0, value / vmax)) * 100
    val_txt = "—" if value is None else value_fmt.format(value)
    return ui.div(
        ui.div(label, class_="easi-bar-label"),
        ui.div(ui.div(class_="easi-bar-fill", style=f"width:{pct:.1f}%;background:{color};"),
               class_="easi-bar-track"),
        ui.div(val_txt, class_="easi-bar-val"),
        class_="easi-bar-row" + (" indent" if indent else ""),
    )


def _plot_legend(items):
    """A small color-swatch legend; ``items`` is a list of (color, label)."""
    return ui.div(
        *[ui.span(ui.span(class_="easi-leg-sw", style=f"background:{c};"), txt,
                  class_="easi-leg-item") for c, txt in items],
        class_="easi-plot-legend",
    )


def _summary_plots(sc):
    """Two-panel summary: all 20 function scores grouped by STAF category (left), and
    the condition indices with the Ecosystem index as parent of its three sub-indices
    (right). Bars are colored by their Functioning / At-Risk / Non-Functioning band."""
    fscores, sub = sc["functionScores"], sc["subIndices"]
    eci = sc["ecosystemConditionIndex"]

    groups: dict[str, list] = {}
    for fn in config.functions():
        groups.setdefault(fn["category"], []).append(fn)
    fn_blocks = []
    for cat, fns in groups.items():
        # An unrated function keeps its neutral color: missing evidence must not read as
        # a Non-Functioning (red) zero.
        bars = [_bar(fn["name"], fscores.get(fn["id"]),
                     scoring.function_score_band_color(fscores.get(fn["id"])),
                     vmax=config.FUNCTION_SCORE_MAX, value_fmt="{:.0f}")
                for fn in fns]
        fn_blocks.append(ui.div(ui.div(cat, class_="easi-fn-group"), *bars,
                                class_="easi-fn-block"))
    left = ui.div(
        ui.div("Function scores", class_="easi-plot-title"),
        _plot_legend([(scoring.function_score_band_color(15), "Functioning 11-15"),
                      (scoring.function_score_band_color(8), "At-Risk 6-10"),
                      (scoring.function_score_band_color(0), "Non-Functioning 0-5")]),
        *fn_blocks,
        class_="easi-plot-panel",
    )
    right = ui.div(
        ui.div("Condition indices", class_="easi-plot-title"),
        _plot_legend([(scoring.index_band_color(1.0), "Functioning 0.70-1.00"),
                      (scoring.index_band_color(0.5), "At-Risk 0.40-0.69"),
                      (scoring.index_band_color(0.0), "Non-Functioning 0.00-0.39")]),
        _bar("Ecosystem Condition Index", eci, scoring.index_band_color(eci)),
        _bar("Physical", sub["physical"], scoring.index_band_color(sub["physical"]),
             indent=True),
        _bar("Chemical", sub["chemical"], scoring.index_band_color(sub["chemical"]),
             indent=True),
        _bar("Biological", sub["biological"], scoring.index_band_color(sub["biological"]),
             indent=True),
        class_="easi-plot-panel",
    )
    return ui.div(left, right, class_="easi-summary-plots")


def _rate_select(mid, r):
    """Neutral SFARI-style native <select> that overrides a metric's rating.

    Styled like SFARI's likert select (no rating colors; ``set`` marks an answered state).
    Plain HTML (no Shiny binding) — www/report-edit.js posts the choice via setInputValue,
    so it survives the worksheet card's re-render on scored().
    """
    eff = r.get("rating")                        # effective (override or generated)
    opts = []
    if eff not in ("Good", "Fair", "Poor"):      # no current rating -> non-pickable placeholder
        opts.append(ui.tags.option("—", value="", selected="selected", disabled="disabled"))
    for rt in ("Good", "Fair", "Poor"):
        opts.append(ui.tags.option(rt, value=rt, selected="selected") if rt == eff
                    else ui.tags.option(rt, value=rt))
    # the criteria + computed value live in the metric-name ⓘ, so the control stays narrow.
    return ui.tags.select(*opts, {"class": "easi-rate-sel" + (" set" if eff else ""),
                                  "data-mid": mid, "title": "Click to override rating"})


def _rate_chip(r):
    """Static rating chip for read-only tables (batch report modal): the select's
    colors without the control, so nothing can post ``override_set`` from a popup."""
    eff = r.get("rating")
    return ui.tags.span(eff or "—", class_=f"easi-rate-chip rate-{eff or 'auto'}")


# Long labels for the mapping (D/i/–) cells, shown as a hover title.
_MAP_CODE = {"D": ("D", "Direct effect"), "i": ("i", "Indirect effect"),
             "-": ("–", "No mapped effect")}


def _fnscore_cell(r, meta):
    """The Function Score cell. Emits BOTH a read-only STAF-style slider and the plain number
    (+ F/AR/NF badge); a CSS class flip on #easi-report (``show-slider``) chooses which shows,
    so switching is instant with no re-render. On overridden rows a faint ``(auto: N)`` cue is
    appended (shown by ``show-suggested``)."""
    fs = r.get("functionScore")
    if fs is None:
        return ui.tags.td("", class_="easi-fs-cell")
    pct = max(2.0, min(98.0, fs / config.FUNCTION_SCORE_MAX * 100))
    # read-only slider: 3-band track (NF/AR/F) with a knob at the score; the NF/AR/F labels
    # above the track are revealed by the "Show F/AR/NF labels" checkbox (as in STAF).
    slider = ui.div(
        ui.div(
            ui.div(ui.tags.span("NF"), ui.tags.span("AR"), ui.tags.span("F"),
                   class_="easi-fslider-labels"),
            ui.div(ui.div(class_="easi-fslider-knob", style=f"left:{pct:.1f}%;"),
                   class_="easi-fslider-track"),
            class_="easi-fslider-bars"),
        ui.tags.span(str(fs), class_="easi-fslider-num"),
        class_="easi-fslider")
    # plain number + colored F/AR/NF badge (shown when the slider is toggled off)
    plain = ui.tags.span(
        str(fs),
        ui.tags.span(scoring.function_score_band_label(fs), class_="easi-fnf-badge",
                     style=f"background:{scoring.function_score_band_color(fs)};"),
        class_="easi-fscore-plain")
    kids = [slider, plain]
    if r.get("status") == "override":
        gen = r.get("generatedRating")
        if gen in config.RATINGS:
            auto = scoring.function_score(
                scoring.rating_to_index(gen, (meta.get(r["metricId"]) or {}).get("indexMidpoints")))
            kids.append(ui.tags.span(f"(auto: {auto})", class_="easi-auto-cue"))
    return ui.tags.td(*kids, class_="easi-fs-cell")


def _metric_card_tip(row):
    """The ⓘ hover card for a metric — definition, data source, calculation, and the
    Good/Fair/Poor criteria (the land-cover metric shows both indicators; the detrital metric
    shows its riparian-vegetation breakdown). Pure: takes a scored/base metric row and returns
    the ``_info`` span. Used by the read-only report row (and is the retarget point for the
    tooltip test)."""
    mid = row["metricId"]
    lc = row.get("landCover")
    if lc:  # attach both indicators' threshold sets for the tooltip
        lc = {**lc, "impervious_bands": config.criteria_bands(mid, "impervious"),
              "agriculture_bands": config.criteria_bands(mid, "agriculture")}
    # The equation comes from the trace that produced this rating, so a composite is never
    # described as a dataset value used directly.
    calc = ((row.get("scoring") or {}).get("equation")
            or config.METRIC_CALCULATIONS.get(mid)
            or "See the Scoring method panel for the equation and breakpoints.")
    tip_html = _metric_tip_html(
        name=row.get("name"), definition=config.METRIC_DEFINITIONS.get(mid, ""),
        source=row.get("source") or "", note=row.get("note") or "",
        calc=calc,
        crit=(row.get("criteriaBands") or _METRICS.get(mid, {}).get("criteria") or {}),
        default=row.get("generatedRating") or "n/a", land_cover=lc,
        riparian=row.get("ripVeg"))
    return _info(html_tip=tip_html)


# --------------------------------------------------------------------------- #
# "Scoring method" panel (worksheet metric card) — inputs, equation, and the
# scoring criteria. Definition/rationale/limitations moved to the docs site's
# Screening Metric Reference; the reference-curve plot and what-if sliders are gone.
# --------------------------------------------------------------------------- #
def _fmt_input(v, inp):
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if inp.integer or f == int(f):
        return str(int(round(f)))
    return f"{f:g}"


def _active_scoring(brow, src_choice):
    """The scoring trace to render for a metric: a chosen source variant's when one is
    present, else the base row's."""
    variants = brow.get("sourceVariants") or {}
    key = src_choice or brow.get("sourceChoice")
    v = variants.get(key) if key else None
    return ((v or brow).get("scoring")) or {}


def _trace_values(trace):
    """``{input key: value}`` from a scoring trace, for the inputs table and the sliders.

    The canonical trace records inputs as an ordered list of ``{key, value, source, ...}``
    so provenance travels with each value; the renderers only need the values."""
    return {item["key"]: item.get("value") for item in (trace or {}).get("inputs") or []}


def _method_inputs_ui(method, site_inputs):
    rows = []
    for inp in method.inputs:
        sv = (site_inputs or {}).get(inp.key)
        val = _fmt_input(sv, inp) + (f" {inp.unit}" if inp.unit and sv is not None else "")
        rows.append(ui.div(
            ui.div(ui.span(inp.label, class_="easi-method-input-label"),
                   (ui.span("context", class_="easi-method-input-context")
                    if inp.context_only else None),
                   class_="easi-method-input-name"),
            ui.span(val, class_="easi-method-input-value"),
            (ui.span(inp.source_label, class_="easi-method-input-source")
             if inp.source_label else None),
            class_="easi-method-input-row"))
    return ui.div(ui.div("Inputs used", class_="easi-method-inputs-title"), *rows,
                  class_="easi-method-inputs")


def _method_criteria_ui(row, method=None):
    """The Good/Fair/Poor scoring criteria (its own section below 'Scoring method').

    Every row is a colored swatch + the rating + the exact automated breakpoint, generated
    from the same catalog bands the evaluator used, so the list, the reference curve, and
    the rating can never disagree. Methods scored on more than one indicator (land cover,
    thermal vulnerability, nutrients, observed bank condition) list each indicator under
    its own sub-heading. Where the catalog carries a field profile it follows as a clearly
    separate reference block, so field guidance is never mistaken for what the automation
    actually tested.
    """
    mid = row["metricId"]
    criteria = row.get("methodCriteria") or {}
    automated = criteria.get("automated") or []
    blocks = []

    def _crow(b, text, rng=None):
        return ui.div(ui.span(class_=f"easi-tip-dot {b.lower()}"), ui.tags.b(b),
                      (ui.span(rng, class_="easi-method-crit-range") if rng else None),
                      ui.span(text, class_="easi-method-crit-text"),
                      class_="easi-method-crit-row")

    if len(automated) == 1:
        bands = automated[0].get("bands") or {}
        field = ((criteria.get("fieldReference") or {}).get("criteria") or {})
        crows = [_crow(b, field.get(b, ""), bands[b])
                 for b in config.RATINGS if bands.get(b)]
        if crows:
            blocks.append(ui.div(*crows))
    else:
        for block in automated:
            bands = block.get("bands") or {}
            crows = [_crow(b, "", bands[b]) for b in config.RATINGS if bands.get(b)]
            if crows:
                unit = f" ({block['units']})" if block.get("units") else ""
                blocks.append(ui.div(
                    ui.div(f"{block.get('label', '')}{unit}", class_="easi-method-crit-sub"),
                    *crows))
        field = (criteria.get("fieldReference") or {}).get("criteria") or {}
        if field:
            title = (criteria.get("fieldReference") or {}).get("title") or "Field reference"
            crows = [_crow(b, field[b]) for b in config.RATINGS if field.get(b)]
            blocks.append(ui.div(ui.div(title, class_="easi-method-crit-sub"), *crows))

    if not blocks:   # no numeric bands (a categorical method) — fall back to STAF field prose
        crit = row.get("criteriaBands") or _METRICS.get(mid, {}).get("criteria") or {}
        crows = [_crow(b, crit[b]) for b in config.RATINGS if crit.get(b)]
        if not crows:
            return None
        blocks.append(ui.div(*crows))
    return ui.div(*blocks, class_="easi-method-crit")


def _method_body_ui(method, row, site_inputs):
    """How the number is computed: inputs used plus equation, or (for categorical metrics) the
    decision table. The reference-curve plot and what-if sliders were removed; the scoring
    breakpoints live in 'Scoring criteria' and on the docs site's Screening Metric Reference."""
    if method.mode == "categorical":
        return ui.HTML(method_plot.decision_html(method, row.get("generatedRating")))
    parts = [_method_inputs_ui(method, site_inputs)]
    if method.equation:
        parts.append(ui.div(ui.span("Equation", class_="easi-method-equation-label"),
                            ui.tags.code(method.equation),
                            class_="easi-method-equation"))
    return ui.TagList(*parts)


def _method_expander(mid, scoring_trace):
    """The two stacked method sections on a metric card (skeleton part). Body and criteria are
    nested output slots so a data-source swap re-renders in place without remounting the card or
    the cross-section widget. Definition, rationale and limitations now live on the docs site's
    Screening Metric Reference, and the reference-curve plot and what-if sliders were removed."""
    method = easi_methods.resolve(mid, (scoring_trace or {}).get("methodKey"),
                                  (scoring_trace or {}).get("context"))
    if method is None:
        return None
    # Two stacked sections, both collapsed by default so the card opens compact: how the number is
    # computed (inputs + equation, or the categorical decision table), then the scoring criteria.
    return ui.TagList(
        ui.tags.details(
            ui.tags.summary("Scoring method", class_="easi-rollup-sum"),
            ui.output_ui("method_body"),
            class_="easi-method", **{"data-mid": mid}),
        ui.tags.details(
            ui.tags.summary("Scoring criteria", class_="easi-rollup-sum"),
            ui.output_ui("method_criteria"),
            class_="easi-method easi-method-critsec", **{"data-mid": mid}))


def _fmt2(x):
    return "—" if x is None else f"{x:.2f}"


def _metric_table(rows, notes=None, *, outcomes=None, eci=None):
    """The read-only metric grid (STAF layout). Always emits every column/badge (Index,
    Physical/Chemical/Biological mapping, F/AR/NF badge, auto cue) so the report's display
    checkboxes can reveal them purely client-side (CSS class flips on #easi-report) with no
    Shiny re-render. Ratings are static chips and notes plain text; edits happen only in the
    Assessment worksheet, so this view never posts anything. When ``outcomes`` is given the
    integrated rollup is appended flush below the table."""
    notes = notes or {}
    meta = config.metrics_by_id()
    mapping = config.cwa_mapping()
    # Column set (order matters); classed columns are hidden until their checkbox is on.
    head = ui.tags.tr(
        ui.tags.th("Function"), ui.tags.th("Metric"), ui.tags.th("Value"),
        ui.tags.th("Rating"),
        ui.tags.th("Function Score"),
        ui.tags.th("Phy", {"title": "Physical"}, class_="easi-col-map"),
        ui.tags.th("Chem", {"title": "Chemical"}, class_="easi-col-map"),
        ui.tags.th("Bio", {"title": "Biological"}, class_="easi-col-map"),
        ui.tags.th("Index", class_="easi-col-adv"),
        ui.tags.th("Note", class_="easi-note-cell"))
    n_cols = 10
    body = []
    order = {d: i for i, d in enumerate(_DISC_ORDER)}
    rows = sorted(rows, key=lambda r: (order.get(r["discipline"], 99), r["functionName"]))
    seen = []
    for r in rows:
        mid = r["metricId"]
        if r["discipline"] not in seen:
            seen.append(r["discipline"])
            body.append(ui.tags.tr(ui.tags.td(r["discipline"], colspan=str(n_cols)),
                                   class_="easi-disc"))
        is_ovr = r.get("status") == "override"     # manual override — the only tinted row
        # (cross-section-derived rows, status "xs-derived", are NOT tinted: they aren't
        # manual overrides. Their provenance shows in the ⓘ tooltip's Source section.)
        rating_cell = ui.tags.td(ui.div(_rate_chip(r), _metric_card_tip(r),
                                        class_="easi-rate-cell"))
        idx = r.get("index")
        idx_cell = ui.tags.td("—" if idx is None else f"{idx:.2f}", class_="easi-col-adv")
        codes = mapping.get(r.get("functionId"), {})
        map_cells = []
        for key in config.OUTCOMES:
            txt, title = _MAP_CODE.get(codes.get(key, "-"), _MAP_CODE["-"])
            map_cells.append(ui.tags.td(txt, {"title": f"{key.capitalize()}: {title}"},
                                        class_="easi-col-map"))
        note = notes.get(mid) or r.get("userNote") or ""
        body.append(ui.tags.tr(
            ui.tags.td(r["functionName"]),
            ui.tags.td(r["name"]),
            ui.tags.td(r["valueText"]),
            rating_cell,
            _fnscore_cell(r, meta),
            *map_cells,
            idx_cell,
            ui.tags.td(note, class_="easi-note-cell easi-rep-note"),
            {"data-mid": mid},
            class_=("easi-row-ovr" if is_ovr else ""),
            style=("" if r.get("rating") else "color:#aaa;"),
        ))
    if outcomes is None:
        return ui.tags.table(ui.tags.thead(head), ui.tags.tbody(*body), class_="easi-tbl")
    # The outcome rollup is rendered two ways, toggled purely by CSS on the mappings state so
    # it always reads like part of the table (STAF-style, no re-render):
    #  - an aligned <tfoot> INSIDE the table, whose P/C/B values sit under the mapping columns
    #    (shown when "Show function mappings" is on), and
    #  - a right-justified standalone table below it (shown when mappings are hidden).
    table = ui.tags.table(
        ui.tags.thead(head), ui.tags.tbody(*body),
        ui.tags.tfoot(*_rollup_rows(outcomes, eci, aligned=True), class_="easi-rollup-foot"),
        class_="easi-tbl")
    # the standalone (mappings-off) rollup carries its own Physical/Chemical/Biological
    # header — there are no mapping columns above to align to; the aligned tfoot omits it
    # (the table's own P/C/B headers serve).
    sa_head = ui.tags.thead(ui.tags.tr(
        ui.tags.th(""), ui.tags.th("Physical"), ui.tags.th("Chemical"),
        ui.tags.th("Biological")))
    standalone = ui.tags.table(
        sa_head, ui.tags.tbody(*_rollup_rows(outcomes, eci, aligned=False)),
        class_="easi-tbl easi-rollup-standalone")
    return ui.div(table, standalone, class_="easi-metrics-block")


_ROLLUP_KEYS = ("physical", "chemical", "biological")


def _rollup_rows(outcomes, eci, *, aligned):
    """Rows for the outcome rollup, shared by both renderings (see ``_metric_table``).

    ``aligned=True`` builds the metric table's ``<tfoot>`` so the three values land under the
    Physical/Chemical/Biological columns. The label spans the five always-visible left columns
    (Function..Function Score) and is right-justified, so its text sits directly left of the
    values with no blank column between. After the values come a placeholder for the Index
    column (``easi-col-adv``, positioned *after* the mapping columns so it collapses with them
    when "Show advanced" is off without ever gapping the label from the values) and a note
    cell — 10 columns total. ``aligned=False`` builds the full-width standalone table (label +
    three values, no placeholders). Direct/Indirect/Weighted/Max carry ``easi-rollup-row``
    (revealed by "Show roll-up at bottom"); Outcome Sub-index and Ecosystem Condition Index
    always show, tinted by their condition band."""
    val_cls = "easi-col-map" if aligned else ""

    def label(text):
        return ui.tags.th(text, {"colspan": "5"} if aligned else {}, class_="easi-rollup-lbl")

    def value(text, *, tint=None, span=None):
        attrs = {"class": (val_cls + (" easi-band" if tint is not None else "")).strip()}
        if tint is not None:
            attrs["style"] = f"background:{scoring.index_band_color(tint)};"
        if span:
            attrs["colspan"] = str(span)
        return ui.tags.td(text, attrs)

    # Trailing cells (aligned only): the Index placeholder collapses with the (now
    # after-the-values) Index column; the empty note cell keeps the row at 10 columns.
    def trail():
        return [ui.tags.td("", class_="easi-col-adv"),
                ui.tags.td("", class_="easi-note-cell")] if aligned else []

    def row(label_th, cells, cls):
        return ui.tags.tr(label_th, *cells, *trail(), class_=cls)

    def calc(lbl, fn):
        return row(label(lbl), [value(fn(outcomes[k])) for k in _ROLLUP_KEYS], "easi-rollup-row")

    return [
        calc("Direct functions", lambda o: str(o["direct"])),
        calc("Indirect functions", lambda o: str(o["indirect"])),
        calc("Weighted total", lambda o: f'{o["weighted"]:.1f}'),
        calc("Max weighted", lambda o: f'{o["max"]:.1f}'),
        row(label("Outcome Sub-index"),
            [value(f'{outcomes[k]["subIndex"]:.2f}', tint=outcomes[k]["subIndex"])
             for k in _ROLLUP_KEYS], "easi-subindex-row"),
        row(label("Ecosystem Condition Index"),
            [value("—" if eci is None else f"{eci:.2f}", tint=eci, span=3)],
            "easi-eci-row"),
    ]


def _summary_header(d):
    def fact(label, val):
        return ui.span(ui.tags.b(f"{label}: "), str(val), class_="easi-fact")

    lat, lon = d.get("snapped_lat"), d.get("snapped_lon")
    snapped = f"{lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "—"
    # Identity (COMID/HUC12) and drainage live in the basin table; the watershed area
    # duplicated the drainage area, so only the analysis point and reach are chipped here.
    return ui.div(
        ui.h3(d.get("gnis_name") or "(unnamed reach)"),
        ui.div(
            fact("Analysis Point", snapped),
            fact("Reach", f'{d.get("reach_length_ft")} ft upstream'),
            class_="easi-facts",
        ),
        class_="easi-summary-head",
    )


def _basin_block(d, rep):
    # Identity (COMID, HUC12) moved out of the header chips into this table; the data
    # exports already carry these fields, so they're prepended here at the view layer only.
    ident = [["COMID", d.get("comid")], ["HUC12", d.get("huc12") or "—"]]
    rows = ident + list((rep or {}).get("basin", {}).get("rows") or [])
    if not rows:
        return None
    body = [ui.tags.tr(ui.tags.th(lbl), ui.tags.td(str(val))) for lbl, val in rows]
    return ui.tags.details(
        ui.tags.summary("Basin characteristics", class_="easi-section-title easi-rollup-sum"),
        ui.tags.table(ui.tags.tbody(*body), class_="easi-tbl", style="max-width:560px;"),
        class_="easi-rollup", open=True,
    )


def _xs_readonly_block(rep):
    """Read-only cross-section for the report modal: the geometry summary panel (left)
    beside the static plot image (right), in the report's usual 300px|1fr grid. No inputs,
    no live widget; the cross-section is edited in the Assessment worksheet."""
    xs = (rep or {}).get("crossSection") or {}
    if not xs.get("png_b64"):
        return None
    block = xs.get("geom") or {}
    er = (xs.get("entrenchment_ratio") if xs.get("entrenchment_ratio") is not None
          else block.get("entrenchment_ratio"))
    bhr = (xs.get("bank_height_ratio") if xs.get("bank_height_ratio") is not None
           else block.get("bank_height_ratio"))

    def wd(m):
        return f"{m * FT_PER_M:.1f} ft" if m is not None else "n/a"

    rows = [("Bieger region", block.get("division") or "National curve"),
            ("Bankfull width", wd(block.get("bankfull_width_m"))),
            ("Floodprone width", wd(block.get("flood_prone_width_m"))),
            ("Entrenchment ratio", _fmt2(er)),
            ("Bank-height ratio", _fmt2(bhr))]
    table = ui.tags.table(
        ui.tags.tbody(*[ui.tags.tr(ui.tags.th(lbl), ui.tags.td(val)) for lbl, val in rows]),
        class_="easi-tbl easi-xs-tbl")
    panel = ui.div(ui.div("Cross-section geometry", class_="easi-xs-panel-title"),
                   table, class_="easi-xs-panel")
    plot = ui.div(ui.tags.img(src=f"data:image/png;base64,{xs['png_b64']}"),
                  class_="easi-xsection")
    return ui.div(panel, plot, class_="easi-xsection-wrap")


def _dl_buttons():
    return ui.div(
        ui.download_button("dl_pdf", "PDF", class_="btn-sm btn-outline-secondary"),
        ui.download_button("dl_csv", "CSV", class_="btn-sm btn-outline-secondary"),
        ui.download_button("dl_geojson", "GeoJSON", class_="btn-sm btn-outline-secondary"),
        ui.input_action_button("close_modal", "Close", class_="btn-sm btn-primary"),
        class_="easi-modal-footer",
    )


# Display toggles above the metric table (STAF "screening" controls). Plain HTML checkboxes
# wired by www/report-controls.js, which flips a class on #easi-report — purely client-side,
# so toggling reveals detail instantly with no Shiny re-render (hence no flicker/spinner).
# (class, label, default_on). All default off except the Function Score slider, which the
# user wants on by default so they can compare it against the plain number.
_METRIC_TOGGLES = [
    ("show-slider", "Show function score slider", True),
    ("show-adv", "Show advanced scoring columns", False),
    ("show-map", "Show function mappings", False),
    ("show-rollup", "Show roll-up at bottom", False),
    ("show-suggested", "Show suggested function scores", False),
    ("show-fnf", "Show F/AR/NF labels", False),
]


def _metric_toolbar():
    items = []
    for cls, label, default_on in _METRIC_TOGGLES:
        attrs = {"type": "checkbox", "class": "easi-toggle", "data-cls": cls}
        if default_on:
            attrs["checked"] = "checked"
        items.append(ui.tags.label(ui.tags.input(attrs), ui.tags.span(label),
                                   class_="easi-toggle-item"))
    return ui.div(*items, class_="easi-metric-toolbar")


_ANCHOR_GROUP_ORDER = {"watershed": 0, "clickedReach": 1, "clickedPoint": 2,
                       "surrogateComid": 3, "surrogateWatershed": 4}


def _routed_summary(anchor, d) -> tuple[str, str]:
    """(bold lead, sentence) for a routed site, by the watershed policy that
    ran: the exact watershed (auto) or the legacy surrogate."""
    clicked = anchor.get("clickedStream") or {}
    r = anchor.get("routing") or {}
    dist = r.get("routedDistanceFt")
    dist_txt = f"{dist:,.0f} ft downstream" if dist is not None else "downstream"
    name = clicked.get("gnisName") or "unnamed stream"
    ratio_txt = (f"Drainage area ratio {r.get('daRatio')} "
                 f"(limit {_fmt_ratio_limit(r.get('daRatioLimit'))}).")
    source = (d or {}).get("watershed_source") or ""
    eng = (d or {}).get("watershed_engine") or {}
    if source == "site-engine":
        lead = "Stream outside the StreamCat lookup network. "
        text = (f"{name} (NHDPlus HR) is assessed at the clicked point. Watershed "
                f"metrics describe its exact watershed ({eng.get('areaSqkm')} km², "
                f"STAF site engine v{eng.get('engineVersion')}). ")
    elif source == "not-calculated":
        lead = "Exact watershed not calculated. "
        text = (f"{name} (NHDPlus HR) is outside the StreamCat lookup network and the "
                f"STAF site engine could not compute its watershed "
                f"({eng.get('reason') or 'not calculated'}). Watershed metrics are "
                "unavailable. Use SFARI or DEEP for this site, or enter rating "
                "overrides. ")
    else:
        lead = "Scored at a surrogate reach. "
        text = (f"The clicked stream ({name}, NHDPlus HR) is not in the scoring "
                f"network. Results describe "
                f"{(d or {}).get('gnis_name') or 'the nearest covered reach'} "
                f"{dist_txt}. ")
        return lead, text + ratio_txt
    if r.get("declined"):
        text += ("Reach-keyed evidence (low flow, substrate, biological integrity) "
                 "is unavailable past the substitution limit. ")
    else:
        text += (f"Reach-keyed evidence comes from the nearest covered reach "
                 f"(COMID {(d or {}).get('comid')}, {dist_txt}). ")
    return lead, text + ratio_txt


def _anchor_banner(anchor, d):
    """Banner for routed sites; None on the covered network.

    With per-metric anchoring present, the banner carries the source table
    (grouped by anchor) so a reader sees exactly which rows describe the exact
    watershed, the clicked stream, and the nearest covered reach."""
    if not anchor or anchor.get("anchorKind") != "hrSurrogate":
        return None
    lead, text = _routed_summary(anchor, d)
    groups: dict[tuple, list[str]] = {}
    for entry in (anchor.get("metricAnchors") or {}).values():
        key = (_ANCHOR_GROUP_ORDER.get(entry.get("anchor"), 9), entry.get("label"))
        groups.setdefault(key, []).append(entry.get("name") or "")
    group_lines = [
        ui.div(ui.tags.b(f"{label[:1].upper()}{label[1:]}: "), ", ".join(sorted(names)),
               style="margin-top:.25rem;")
        for (_o, label), names in sorted(groups.items())]
    return ui.div(
        ui.div(ui.tags.b(lead), text),
        *group_lines,
        style=("background:#fff7e0;border:1px solid #e6c96b;border-radius:6px;"
               "padding:.5rem .7rem;margin:0 0 .6rem;font-size:13px;"))


def _report_body(d, rep, notes, downloads, anchor=None):
    """Read-only report body shared by the single-site and batch modals (STAF layout).
    Ratings and notes are edited only in the Assessment worksheet, so this view never posts
    anything: the dense metric table (display toggles reveal detail client-side), the static
    cross-section, the two-panel summary below the table, and the export row (``downloads``).
    The display-toggle classes live on the stable ``#easi-report`` wrapper."""
    return ui.div(
        _anchor_banner(anchor, d),
        _summary_header(d),
        _basin_block(d, rep),
        _xs_readonly_block(rep),
        ui.div("Metrics", class_="easi-section-title"),
        _metric_toolbar(),
        _metric_table(rep.get("metricRows") or [], notes,
                      outcomes=rep.get("outcomes"),
                      eci=rep.get("ecosystemConditionIndex")),
        ui.div("Summary plots", class_="easi-section-title"),
        _summary_plots(rep),
        downloads,
        id="easi-report", class_="show-slider",   # slider on by default (report-controls.js
    )                                             # reconciles with any saved preference)


def _report_modal(res, notes):
    """Single-site report popup, built from an ``export_result()`` snapshot (current overrides,
    swapped sources, and edited cross-section already folded in), so it is fully static."""
    d, rep = res["delineation"], res.get("report") or {}
    return ui.modal(
        _report_body(d, rep, notes, _dl_buttons(), anchor=res.get("siteAnchor")),
        # ✕ lives in the modal header so it stays put when the body scrolls; the muted
        # hint beside it cues that closing returns to the editable Assessment worksheet.
        title=ui.TagList("EASI Screening Report",
                         ui.span("Close to review the Assessment", class_="easi-modal-hint"),
                         ui.input_action_button("close_modal_x", "✕", class_="easi-modal-x")),
        size="xl", easy_close=True, footer=None,
    )


def _batch_report_modal(site_id, base):
    """Read-only per-site report popup for batch results — the same body as the single-site
    report. ``base`` is the site's ``metadata["_artifacts"]`` ``{"delineation","report"}`` dict."""
    d, rep = base.get("delineation") or {}, base.get("report") or {}
    downloads = ui.div(
        ui.download_button("dl_site_pdf", "PDF", class_="btn-sm btn-outline-secondary"),
        ui.download_button("dl_site_csv", "CSV", class_="btn-sm btn-outline-secondary"),
        ui.download_button("dl_site_geojson", "GeoJSON", class_="btn-sm btn-outline-secondary"),
        ui.input_action_button("close_modal", "Close", class_="btn-sm btn-primary"),
        class_="easi-modal-footer")
    return ui.modal(
        _report_body(d, rep, {}, downloads, anchor=base.get("siteAnchor")),
        title=ui.TagList(f"EASI Screening Report: {site_id}",
                         ui.input_action_button("close_modal_x", "✕", class_="easi-modal-x")),
        size="xl", easy_close=True, footer=None,
    )


def _stepper(active):
    # Plain data-step anchors (not Shiny action links): www/worksheet.js delegates a
    # click to the `step_nav` event, so one handler serves both the left-pane stepper and
    # the worksheet stepper without duplicate action-button ids.
    done = True
    items = []
    for key, label in STEP_LABELS:
        cls = "easi-step"
        if key == active:
            cls += " active"; done = False
        elif done:
            cls += " done"
        items.append(ui.tags.a(label, {"data-step": key}, class_=cls))
    return ui.div(*items, class_="easi-steps")


# --------------------------------------------------------------------------- #
# Server
# --------------------------------------------------------------------------- #
def server(input, output, session):
    current_step = reactive.value(STEP_IDENTIFY)
    snapped_point = reactive.value(None)   # (lat, lon, dist_ft) | None
    flow_geojson = reactive.value(None)    # current viewport flowlines FC | None
    delin = reactive.value(None)           # delineate_only result (+ ctx_inputs)
    base_result = reactive.value(None)     # merged delineation + report dict
    stage = reactive.value("")             # progress label
    _assess_prog = {"done": 0, "total": 0, "waiting": {}}  # shared metric-progress state (poller reads)
    # shared delineation-progress state: the STAF site engine's stage events
    # (walk / catchments / union / geometry / reach / metrics) for routed sites
    _delin_prog = {"stage": None, "reaches": None, "hops": None, "family": None}
    _overrides = reactive.value({})        # {metricId: "Good"/"Fair"/"Poor"} from the worksheet
    _notes = reactive.value({})            # {metricId: note text} from the worksheet
    _geom_owned = reactive.value(set())    # metricIds whose rating is currently derived from
    #                                        an edited cross-section (vs a manual dropdown pick)
    _geom_text = reactive.value({})        # {metricId: value text} for those edited rows
    _geom_scoring = reactive.value({})      # {metricId: scoring trace} recomputed from the
    #                                        edited stages, so the Scoring method panel shows
    #                                        the geometry that actually produced the rating
    current_fn = reactive.value(0)         # index into _FUNCTIONS shown in the worksheet
    view_bbox = reactive.value(None)       # rounded bbox at zoom >= FLOW_ZOOM | None
    last_view_change = reactive.value(0.0)
    fetched_bbox = reactive.value(None)
    hr_geojson = reactive.value(None)      # current viewport NHDPlus HR flowlines | None
    pending_anchor = reactive.value(None)  # routed siteAnchor awaiting Delineate | None
    anchor_error = reactive.value(None)    # routing refusal text (DA ratio) | None

    _layers: dict = {"flow": None, "hrflow": None, "route": None,
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
                     layout=Layout(height="100%"))  # fill the wrapper (default is 400px)
            mp.clear_layers()  # drop default OSM
            # The USGS basemap caches stop at zoom 16 (service maxScale ~1:9028); past that the
            # tiles do not exist and the map goes blank. max_native_zoom=16 makes Leaflet upscale
            # the zoom-16 tiles at higher zoom so the basemap stays visible (softer when deep in).
            # last-added base layer is the default -> USGS Topo on top (rivers + names)
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
            return _MAP  # same object every time -> pan/zoom persists

        @reactive.calc
        def _view():
            # Derive the fetch box from the map CENTER (always valid) + a zoom-scaled
            # radius — robust where viewport `bounds` are unreliable (e.g. a 0-width
            # container) and bounded in size for a fast fetch.
            return reactive_read(_MAP, "zoom"), reactive_read(_MAP, "center")

        # ---- vector flowlines on zoom (debounced trailing-edge) ----
        @reactive.effect
        def _track_view():
            import time
            z, c = _view()
            val = None
            if c and z is not None and z >= FLOW_ZOOM:
                lat, lon = float(c[0]), float(c[1])
                delta = min(0.08, 0.03 * (2 ** (15 - z)))  # half-box in degrees
                val = flowlines._round_bbox(lon - delta, lat - delta, lon + delta, lat + delta)
            view_bbox.set(val)
            last_view_change.set(time.monotonic())

        @reactive.extended_task
        async def flow_task(bbox: tuple) -> dict | None:
            return await anyio.to_thread.run_sync(lambda: flowlines.flowlines_in_bbox(*bbox))

        @reactive.extended_task
        async def hr_flow_task(bbox: tuple) -> dict | None:
            return await anyio.to_thread.run_sync(lambda: nhd_hr.hr_flowlines_in_bbox(*bbox))

        @reactive.effect
        def _settle_and_fetch():
            import time
            bbox = view_bbox()
            changed = last_view_change()
            if bbox is None:
                with reactive.isolate():
                    _remove_layer("flow"); flow_geojson.set(None); fetched_bbox.set(None)
                    _remove_layer("hrflow"); hr_geojson.set(None)
                return
            elapsed = time.monotonic() - changed
            if elapsed < 0.5:                       # wait for panning to settle
                reactive.invalidate_later(0.5 - elapsed + 0.02)
                return
            with reactive.isolate():
                if fetched_bbox() == bbox:
                    return
                fetched_bbox.set(bbox)
            flow_task(bbox)
            hr_flow_task(bbox)

        @reactive.effect
        def _apply_flowlines():
            try:
                fc = flow_task.result()
            except Exception:
                return
            with reactive.isolate():
                if fc and fc.get("features"):
                    _add_layer("flow", GeoJSON(data=fc, style=FLOWLINE_STYLE,
                                               hover_style=FLOWLINE_HOVER_STYLE,
                                               name="StreamCat data available (NHDPlus V2)"))
                    flow_geojson.set(fc)
                else:
                    _remove_layer("flow"); flow_geojson.set(None)

        @reactive.effect
        def _apply_hr_flowlines():
            try:
                fc = hr_flow_task.result()
            except Exception:
                return
            with reactive.isolate():
                if fc and fc.get("features"):
                    _add_layer("hrflow", GeoJSON(data=fc, style=HR_FLOWLINE_STYLE,
                                                 hover_style=HR_FLOWLINE_HOVER_STYLE,
                                                 name="Watershed calculation required (NHDPlus HR)"))
                    hr_geojson.set(fc)
                    # Keep the V2 scoring network drawn on top of the HR layer:
                    # whichever fetch settles last would otherwise sit above.
                    if _layers.get("flow") is not None:
                        _add_layer("flow", _layers["flow"])
                else:
                    _remove_layer("hrflow"); hr_geojson.set(None)

        def _clear_route_state():
            # A new pick invalidates any routed-substitution state from the last one.
            _remove_layer("route")
            pending_anchor.set(None)
            anchor_error.set(None)

        # ---- click -> snap or reject (only during the identify step) ----
        @reactive.effect
        @reactive.event(clicked)
        def _handle_click():
            if current_step() != STEP_IDENTIFY:
                return
            with reactive.isolate():
                _clear_route_state()
            lat, lon = clicked()
            fc = flow_geojson()
            hit = flowlines.nearest_point_on_lines(fc, lat, lon) if fc else None
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)                 # covered by the viewport vectors
                return
            # The viewport's HR vectors, when loaded, settle an HR-only click
            # without re-fetching a box around it (2026-09-02).
            hr_fc = hr_geojson()
            hr_hit = nhd_hr.nearest_point_on_hr_lines(hr_fc, lat, lon) if hr_fc else None
            if hr_hit and hr_hit[2] <= SNAP_TOL_FT:
                stage.set(_LOCATING_TEXT)
                route_task(lat, lon, tuple(hr_hit))
            else:
                stage.set(_FINDING_TEXT)
                click_snap_task(lat, lon)        # fetch flowlines around the click + snap

        def _apply_snap(hit):
            slat, slon, dist, comid = hit
            _add_layer("marker", Marker(location=(slat, slon), draggable=False,
                                        title="Selected point", name="Selected point"))
            snapped_point.set((slat, slon, dist, comid))
            ui.update_numeric("lat", value=round(slat, 5))
            ui.update_numeric("lon", value=round(slon, 5))

        def _snap_both(lat: float, lon: float) -> dict:
            """V2 snap first; if the click misses the scoring network, try the HR
            network so the point can be routed to a covered surrogate. Worker-thread
            sync helper shared by the click and typed-coordinate paths."""
            d = 0.012  # ~0.8 mi half-box around the click, so the snap uses the
            hit = flowlines.nearest_point_on_lines(  # line you actually clicked
                flowlines.flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d), lat, lon)
            if hit and hit[2] <= SNAP_TOL_FT:
                return {"hit": hit}
            hr_hit = nhd_hr.nearest_point_on_hr_lines(
                nhd_hr.hr_flowlines_in_bbox(lon - d, lat - d, lon + d, lat + d), lat, lon)
            return {"hit": hit, "hrHit": hr_hit, "lat": lat, "lon": lon}

        @reactive.extended_task
        async def click_snap_task(lat: float, lon: float) -> dict:
            return await anyio.to_thread.run_sync(lambda: _snap_both(lat, lon))

        @reactive.effect
        def _apply_click_snap():
            try:
                res = click_snap_task.result()
            except Exception:
                return
            stage.set("")
            hit = res.get("hit")
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)
                return
            hr_hit = res.get("hrHit")
            if hr_hit and hr_hit[2] <= SNAP_TOL_FT:
                stage.set(_LOCATING_TEXT)
                route_task(res["lat"], res["lon"], tuple(hr_hit))
                return
            ui.notification_show("You didn't click on a stream line. Zoom in and click "
                                 "a stream line: dark blue lines have StreamCat data, "
                                 "cyan lines get a calculated watershed.",
                                 type="warning", duration=5)

        # ---- HR-only stream -> deterministic surrogate routing ----
        @reactive.extended_task
        async def route_task(lat: float, lon: float, hr_hit: tuple) -> dict:
            return await anyio.to_thread.run_sync(
                lambda: routing.route_from_hr(lat, lon, hr_hit))

        @reactive.effect
        def _route_done():
            try:
                res = route_task.result()
            except Exception:
                return
            with reactive.isolate():
                stage.set("")
                _clear_route_state()
                if res.get("error") == "snap_service_error":
                    ui.notification_show("Could not reach the stream routing service. "
                                         "Try the click again.", type="warning", duration=6)
                    return
                if res.get("error"):
                    ui.notification_show("No stream in the scoring network could be "
                                         "reached from this point.", type="warning",
                                         duration=6)
                    return
                if res.get("refused"):
                    _remove_layer("marker")
                    snapped_point.set(None)
                    anchor_error.set(res.get("message"))
                    ui.notification_show(res.get("message"), type="warning", duration=9)
                    return
                anchor = res["anchor"]
                clicked_s = anchor.get("clickedStream") or {}
                scored = anchor.get("scoredReach") or {}
                routing_block = anchor.get("routing") or {}
                # A declined routing is not a refusal under the auto policy:
                # the exact watershed still comes from the site engine, only
                # reach-keyed evidence is withheld, and the card's third line
                # says so (no toast: it repeated the card in warning colors).
                if (clicked_s.get("snapLat") is not None
                        and scored.get("snapLat") is not None
                        and not routing_block.get("declined")):
                    seg = {"type": "FeatureCollection", "features": [{
                        "type": "Feature", "properties": {},
                        "geometry": {"type": "LineString", "coordinates": [
                            [clicked_s["snapLon"], clicked_s["snapLat"]],
                            [scored["snapLon"], scored["snapLat"]]]}}]}
                    _add_layer("route", GeoJSON(data=seg, style=ROUTE_STYLE,
                                                name="Nearest covered reach"))
                # The pin and the coordinate inputs mark the clicked stream, the
                # one the exact watershed is computed for (the pipeline reads the
                # covered reach's own snap from the anchor); before 2026-09-02 the
                # pin jumped to the covered reach, thousands of feet away.
                s_lat = clicked_s.get("snapLat")
                s_lon = clicked_s.get("snapLon")
                if s_lat is None:
                    s_lat, s_lon = scored.get("snapLat"), scored.get("snapLon")
                pending_anchor.set(anchor)
                _apply_snap((s_lat, s_lon, clicked_s.get("snapDistFt") or 0.0,
                             scored.get("comid")))

        # ---- typed lat/long -> recenter the map + snap (same path as a click) ----
        @reactive.extended_task
        async def coord_snap_task(lat: float, lon: float) -> dict:
            return await anyio.to_thread.run_sync(lambda: _snap_both(lat, lon))

        @reactive.effect
        def _apply_coord_snap():
            try:
                res = coord_snap_task.result()
            except Exception:
                return
            stage.set("")
            hit = res.get("hit")
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)
                return
            hr_hit = res.get("hrHit")
            if hr_hit and hr_hit[2] <= SNAP_TOL_FT:
                stage.set(_LOCATING_TEXT)
                route_task(res["lat"], res["lon"], tuple(hr_hit))
                return
            # No stream near the typed point: place nothing and clear any stale point
            # so "Delineate" stays disabled until a real stream is found.
            _remove_layer("marker")
            snapped_point.set(None)
            ui.notification_show(
                "No stream within 150 ft of those coordinates. Adjust them, or zoom in "
                "and click a blue stream line.", type="warning", duration=6)

        @reactive.effect
        @reactive.event(input.coords_entered)
        def _coords_entered():
            # Typed Latitude/Longitude (committed on Enter/blur via coord-entry.js).
            if current_step() != STEP_IDENTIFY:
                return
            ev = input.coords_entered() or {}
            lat, lon = ev.get("lat"), ev.get("lon")
            if lat is None or lon is None:
                return  # incomplete entry -> place nothing
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                return
            if not (24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0):
                ui.notification_show("Coordinates must be within the continental "
                                     "United States.", type="warning", duration=5)
                return
            with reactive.isolate():
                _clear_route_state()
            _MAP.center = (lat, lon)   # bring the typed point into view so it is visible
            _MAP.zoom = 15
            stage.set(_FINDING_TEXT)
            coord_snap_task(lat, lon)

    # ---- address geocode -> recenter the map so streams appear ----
    @reactive.effect
    @reactive.event(input.find_address)
    def _geocode():
        hit = geocode_address(input.address())
        if hit and _HAS_MAP:
            _MAP.center = (hit[0], hit[1])
            _MAP.zoom = 15
            ui.notification_show(f"Centered on {hit[0]:.4f}, {hit[1]:.4f}. Click a blue stream.",
                                 duration=4)
        elif not hit:
            ui.notification_show("Place not found. Try a city, address, or stream name.",
                                 type="warning", duration=4)

    @reactive.effect
    @reactive.event(input.address_pick)
    def _geocode_pick():
        # A suggestion was chosen in the type-ahead dropdown (coords come from the
        # client-side Photon query); just recenter the map.
        if not _HAS_MAP:
            return
        pick = input.address_pick() or {}
        lat, lon = pick.get("lat"), pick.get("lon")
        if lat is None or lon is None:
            return
        _MAP.center = (float(lat), float(lon))
        _MAP.zoom = 15
        where = pick.get("label") or f"{float(lat):.4f}, {float(lon):.4f}"
        ui.notification_show(f"Centered on {where}. Click a blue stream.", duration=4)

    # ---- enable "Delineate" only once a point is picked on the map ----
    @reactive.effect
    def _toggle_delineate():
        routed = pending_anchor() is not None
        ui.update_action_button(
            "delineate", disabled=(snapped_point() is None),
            label=("Compute exact watershed and reach" if routed
                   else "Delineate Basin and Reach"))

    # ---- staged analysis tasks ----
    @reactive.extended_task
    async def delineate_task(lat: float, lon: float, reach_ft: float,
                             comid: "int | None" = None,
                             anchor: "dict | None" = None) -> dict:
        return await pipeline.delineate_only(lat, lon, reach_ft, comid=comid,
                                             anchor=anchor, progress=_delin_prog)

    @reactive.extended_task
    async def assess_task(ctx_inputs: dict, metric_ids: list, sources: dict,
                          progress: dict) -> dict:
        return await pipeline.assess_only(ctx_inputs, metric_ids=metric_ids,
                                          sources=sources, progress=progress)

    # === TEMP: MMW comparison overlay (remove later — no workflow impact) ===
    # Overlays the Model My Watershed polygon on the EASI watershed in the Basin
    # view when the "show_mmw" checkbox is on. Purely a map layer keyed "mmw";
    # touches no scoring/report/ctx state. No API key (e.g. on deploy) -> the
    # helper returns a warning and this no-ops. Delete this block + MMW_STYLE +
    # the checkbox div to remove the feature.
    mmw_cache = reactive.value({})  # {(lat, lon): watershed_fc} fetched MMW polygons
    mmw_msg = reactive.value("")    # status line shown under the checkbox

    @reactive.extended_task
    async def mmw_task(lat: float, lon: float) -> dict:
        from easi.datasources import mmw
        fc, _area, _pt, warnings = await anyio.to_thread.run_sync(
            mmw.delineate_watershed_mmw, lat, lon)
        return {"lat": lat, "lon": lon, "fc": fc, "warnings": warnings}

    def _mmw_point():
        d = delin() or {}
        ci = d.get("ctx_inputs") or {}  # ctx_inputs always carries lat/lon (snapped or original)
        lat, lon = ci.get("lat"), ci.get("lon")
        if lat is None or lon is None:
            dd = d.get("delineation") or {}
            lat, lon = dd.get("snapped_lat"), dd.get("snapped_lon")
        return (lat, lon) if (lat is not None and lon is not None) else None

    def _fit_mmw(fc):
        # Re-fit to EASI+MMW bounds: a comm-added ipyleaflet layer doesn't paint
        # without a following view change, so this nudge forces the overlay to
        # render (and frames both basins). Mirrors _delineate_done's fit_bounds.
        if not _HAS_MAP:
            return
        with reactive.isolate():
            easi_ws = (delin() or {}).get("watershed_geojson")
        bounds = delineation.geojson_bounds(easi_ws, fc)
        if bounds:
            _MAP.fit_bounds(bounds)

    @reactive.effect
    def _mmw_toggle():
        # Plain effect (not @reactive.event) so it takes a live dependency on the
        # dynamically-rendered Basin checkbox — reruns on render and every toggle.
        on = input.show_mmw()
        if not _HAS_MAP or not on:
            _remove_layer("mmw")
            mmw_msg.set("")
            return
        pt = _mmw_point()
        if pt is None:
            return
        with reactive.isolate():
            cached = mmw_cache().get(pt)
        if cached is not None:
            try:
                _add_layer("mmw", GeoJSON(data=delineation.display_simplify(cached),
                                          style=MMW_STYLE, name="MMW watershed"))
                _fit_mmw(cached)
            except Exception as exc:  # noqa: BLE001
                ui.notification_show(f"Could not draw MMW overlay: {exc}",
                                     type="warning", duration=6)
            mmw_msg.set("")
            return
        mmw_msg.set("Fetching MMW watershed…")
        ui.notification_show("Fetching MMW watershed… please wait", id="mmw_stage",
                             type="message", duration=None)
        mmw_task(*pt)

    @reactive.effect
    def _mmw_done():
        status = mmw_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("mmw_stage")
        if status == "error":
            ui.notification_show("MMW overlay fetch failed.", type="warning", duration=4)
            mmw_msg.set("MMW watershed unavailable.")
            return
        out = mmw_task.result()
        fc = out.get("fc")
        if not fc:
            msg = "; ".join(out.get("warnings") or []) or "no watershed returned"
            ui.notification_show(f"MMW overlay unavailable: {msg}", type="warning", duration=5)
            mmw_msg.set("MMW watershed unavailable.")
            return
        with reactive.isolate():  # isolate read+write so this effect never re-triggers itself
            mmw_cache.set({**mmw_cache(), (out["lat"], out["lon"]): fc})
            draw = bool(input.show_mmw()) and current_step() == STEP_BASIN
        if draw and _HAS_MAP:
            try:
                _add_layer("mmw", GeoJSON(data=delineation.display_simplify(fc),
                                          style=MMW_STYLE, name="MMW watershed"))
                _fit_mmw(fc)  # nudge a repaint so the overlay actually paints
            except Exception as exc:  # noqa: BLE001 - never leave the status stuck
                ui.notification_show(f"Could not draw MMW overlay: {exc}",
                                     type="warning", duration=6)
        mmw_msg.set("")

    @reactive.effect
    def _mmw_step_sync():
        # The overlay belongs to the Basin view only; drop it elsewhere so the
        # (reset-to-off) checkbox and the map layer never disagree.
        if current_step() != STEP_BASIN:
            _remove_layer("mmw")
            mmw_msg.set("")

    @render.text
    def mmw_status():
        return mmw_msg()
    # === END TEMP ===

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
        for key in _delin_prog:
            _delin_prog[key] = None
        routed = bool((pending_anchor() or {}).get("anchorKind") == "hrSurrogate")
        label = ("Calculating the exact watershed…" if routed
                 else "Delineating basin & reach…")
        stage.set(label)
        ui.notification_show(label + " please wait", id="stage",
                             type="message", duration=None)
        delineate_task(lat, lon, float(input.reach_ft()), comid, pending_anchor())

    _ENGINE_STAGE_TEXT = {
        "site": "locating the stream", "walk": "walking upstream",
        "catchments": "fetching catchments", "union": "building the polygon",
        "geometry": "fetching flowlines", "reach": "trimming the reach",
        "metrics": "computing watershed metrics", "done": "finishing",
    }

    @reactive.effect
    def _delineate_progress_poll():
        # While a routed site's exact watershed computes, poll the shared engine
        # progress twice a second and narrate the stage (reaches walked, hops).
        if delineate_task.status() != "running":
            return
        reactive.invalidate_later(0.5)
        st = _delin_prog.get("stage")
        if not st:
            return
        detail = _ENGINE_STAGE_TEXT.get(st, st)
        if st == "metrics" and _delin_prog.get("family"):
            detail += f" ({_delin_prog['family']})"
        if _delin_prog.get("reaches") is not None:
            detail += (f", {_delin_prog['reaches']} reaches, "
                       f"{_delin_prog.get('hops') or 0} hops")
        label = f"Calculating the exact watershed: {detail}"
        stage.set(label)
        ui.notification_show(label + ", please wait", id="stage",
                             type="message", duration=None)

    @reactive.effect
    def _delineate_done():
        status = delineate_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("stage"); stage.set("")
        if status == "error":
            ui.notification_show("Delineation failed. Try another point or zoom in further.",
                                 type="error", duration=8)
            return  # keep the marker + stay on Identify so the user can retry
        try:
            res = delineate_task.result()
        except Exception:
            ui.notification_show("Delineation failed.", type="error", duration=8)
            return
        if res.get("status") != "ok":
            ui.notification_show(res.get("message", "Delineation error"), type="error", duration=8)
            return
        # Draw overlays defensively — a very large basin is display-simplified so it
        # renders without breaking the map (full geometry stays in `res` for area/export).
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
                    _MAP.fit_bounds(bounds)            # zoom to the full basin extent
                elif d.get("snapped_lat") is not None:
                    _MAP.center = (d["snapped_lat"], d["snapped_lon"])
        except Exception as exc:  # noqa: BLE001
            ui.notification_show(f"Could not draw the basin on the map: {exc}",
                                 type="error", duration=8)
            return  # keep the marker; don't advance half-rendered
        d = res.get("delineation") or {}
        if d.get("watershed_source") == "not-calculated":
            # The engine failed or refused: no watershed polygon, watershed
            # metrics unavailable, and the guidance says what to do next.
            _remove_layer("ws")
            guidance = next((w for w in reversed(d.get("warnings") or [])
                             if "SFARI or DEEP" in w), None)
            ui.notification_show(guidance or "The exact watershed could not be "
                                 "calculated. Watershed metrics are unavailable.",
                                 type="warning", duration=12)
        delin.set(res)
        current_step.set(STEP_BASIN)

    @reactive.effect
    @reactive.event(input.run_screening)
    def _run_screening():
        # The Basin "Run screening" button just advances to the Assessment worksheet;
        # _autostart_assess kicks off the actual compute (single place that starts the task).
        if delin() is None:
            return
        current_fn.set(0)
        current_step.set(STEP_ASSESS)

    @reactive.effect
    def _autostart_assess():
        # Landing on the Assessment step with a delineation but no screening yet starts the
        # run — covers both the Basin button and stepper navigation. Depends only on
        # current_step; the rest is isolated so it fires once per arrival, never on result.
        if current_step() != STEP_ASSESS:
            return
        with reactive.isolate():
            if base_result() is not None or assess_task.status() == "running":
                return
            d = delin()
        if not d:
            return
        n = len(selected_metric_ids())
        _assess_prog["done"], _assess_prog["total"], _assess_prog["waiting"] = 0, n, {}
        stage.set(f"Computing metrics… 0/{n}")
        ui.notification_show(f"Computing metrics… 0/{n}, please wait", id="stage",
                             type="message", duration=None)
        assess_task(d["ctx_inputs"], selected_metric_ids(), {}, _assess_prog)

    @reactive.effect
    def _assess_progress_poll():
        # While metrics compute, poll the shared counter ~3x/sec and update the
        # left-pane busy label + toast with a live "X/N" count.
        if assess_task.status() != "running":
            return  # stops rescheduling once the task settles
        reactive.invalidate_later(0.3)
        done, total = _assess_prog["done"], _assess_prog["total"]
        waiting = _assess_prog.get("waiting") or {}
        detail = (", waiting on " + ", ".join(sorted(waiting))) if waiting else ""
        label = f"Computing metrics… {done}/{total}{detail}"
        stage.set(label)
        ui.notification_show(label + ", please wait", id="stage",
                             type="message", duration=None)

    @reactive.effect
    def _assess_done():
        status = assess_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("stage"); stage.set("")
        if status == "error":
            ui.notification_show("Metric computation failed. Please try again.",
                                 type="error", duration=8)
            return
        try:
            res = assess_task.result()
        except Exception:
            ui.notification_show("Metric computation failed.", type="error", duration=8)
            return
        if res.get("status") != "ok":
            ui.notification_show("Analysis error", type="error", duration=8)
            return
        with reactive.isolate():
            d = delin()
        if not d:
            return
        merged = {k: v for k, v in d.items() if k != "ctx_inputs"}
        merged["delineation"] = {**d["delineation"], "huc12": res.get("huc12")}
        merged["report"] = res["report"]
        base_result.set(merged)
        # fresh screening: no overrides / notes / source swaps / geometry edits
        _overrides.set({}); _notes.set({})
        _geom_owned.set(set()); _geom_text.set({}); _geom_scoring.set({}); _xs_sel.set(None)
        _xs_unit_prev.set("ft"); current_fn.set(0)
        # Fresh run complete: auto-open the screening report (same path as "Open report",
        # so closing it lands on the Assessment worksheet either way). MUST stay isolated:
        # _show_report_modal reads export_result() (base_result/scored/notes calcs), and
        # without isolate this effect would gain those as dependencies and re-run — wiping
        # overrides/notes and re-opening the modal — on every later edit.
        current_step.set(STEP_REPORT)
        with reactive.isolate():
            _show_report_modal()

    # ---- step navigation ----
    @reactive.effect
    @reactive.event(input.back_to_basin)
    def _go_basin():
        current_step.set(STEP_BASIN)

    @reactive.effect
    @reactive.event(input.step_nav)
    def _step_nav():
        # One handler for both steppers (left pane + worksheet); www/worksheet.js posts the
        # target step key on a data-step click. Guards mirror the old counter-based nav.
        target = input.step_nav()
        if target not in dict(STEP_LABELS):
            return
        with reactive.isolate():
            has_delin = delin() is not None
            has_report = base_result() is not None
        if target == STEP_IDENTIFY:
            current_step.set(STEP_IDENTIFY)
        elif target == STEP_BASIN and has_delin:
            current_step.set(STEP_BASIN)
        elif target == STEP_ASSESS and has_delin:
            current_step.set(STEP_ASSESS)         # _autostart_assess runs it if not yet done
        elif target == STEP_REPORT and has_report:
            current_step.set(STEP_REPORT)
            _show_report_modal()                  # opening Report shows the read-only popup
        else:
            ui.notification_show("Finish the earlier steps first.", type="message", duration=2)

    @reactive.effect
    @reactive.event(input.nav_new, input.clear_basin)
    def _reset():
        for k in ("ws", "reach", "marker", "route"):
            _remove_layer(k)
        pending_anchor.set(None); anchor_error.set(None)
        snapped_point.set(None); delin.set(None); base_result.set(None)
        _overrides.set({}); _notes.set({})
        _geom_owned.set(set()); _geom_text.set({}); _geom_scoring.set({}); current_fn.set(0)
        stage.set("")
        current_step.set(STEP_IDENTIFY)
        try:
            ui.modal_remove()
        except Exception:  # noqa: BLE001
            pass

    @reactive.effect
    @reactive.event(input.close_modal, input.close_modal_x)
    def _close_modal():
        ui.modal_remove()

    @reactive.effect
    @reactive.event(input.nav_help)
    def _help():
        ui.modal_show(ui.modal(
            ui.markdown(
                "**EASI** automates the EASI Screening-tier assessment (from STAF) "
                "using national, public GIS and hydrology data. It is a desktop "
                "screening estimate, not a field-validated assessment.\n\n"
                "**How to use**\n\n"
                "1. **Zoom in** until stream lines appear. **Click a stream** to "
                "place a point, or enter coordinates, or search an address. Dark "
                "blue lines have StreamCat data: the StreamCat lookup engine answers "
                "their watershed metrics in seconds. Cyan lines are the rest of "
                "the NHD: the STAF site engine calculates the exact watershed at "
                "the clicked point, which usually takes well under a minute and up to about five minutes on a large basin. On "
                "those streams the three reach-keyed metrics (low flow, "
                "substrate, biological integrity) come from the nearest covered "
                "reach downstream, labeled, and are unavailable when that reach "
                f"drains more than {int(routing.DA_RATIO_MAX)} times the clicked "
                "stream's area. Every value in the report says which engine "
                "produced it.\n"
                "2. Adjust the reach length if needed, then click "
                "**Delineate Basin and Reach**.\n"
                "3. Review the basin, then click **Run screening**. EASI computes the "
                "20 metrics and scores them with the STAF rollup.\n"
                "4. Review each function in the **Assessment**. Adjust ratings, "
                "notes, or the cross-section as needed.\n"
                "5. The **report** opens when screening finishes. Download it as PDF, "
                "CSV, or GeoJSON.\n\n"
                f"**Batch** runs up to {BATCH_UI_MAX_SITES} sites at once and "
                "packages the reports as a ZIP.\n\n"
                "Switch basemaps and toggle the stream overlay with the layers "
                "control at the top right."),
            title="Help", easy_close=True))

    @reactive.calc
    def selected_metric_ids():
        # All 20 metrics always run (there is no per-metric selection).
        return list(ALL_MIDS)

    # ---- worksheet navigation (posted by www/worksheet.js) ----
    @reactive.effect
    @reactive.event(input.nav_move)
    def _nav_move():
        ev = input.nav_move() or {}
        try:
            d = int(ev.get("d", 0))
        except (TypeError, ValueError):
            return
        with reactive.isolate():
            cur = current_fn()
        current_fn.set(max(0, min(len(_FUNCTIONS) - 1, cur + d)))

    @reactive.effect
    @reactive.event(input.nav_jump)
    def _nav_jump():
        ev = input.nav_jump() or {}
        try:
            i = int(ev.get("i"))
        except (TypeError, ValueError):
            return
        current_fn.set(max(0, min(len(_FUNCTIONS) - 1, i)))

    def _show_report_modal():
        res = export_result()
        if not res:
            ui.notification_show("Run a screening first.", type="message", duration=3)
            return
        with reactive.isolate():
            notes = dict(_notes())
        _xs_unit_prev.set("ft")
        ui.modal_show(_report_modal(res, notes))

    @reactive.effect
    @reactive.event(input.open_report_evt)
    def _open_report():
        current_step.set(STEP_REPORT)
        _show_report_modal()

    # ---- in-table overrides + notes (posted by www/report-edit.js) ----
    @reactive.calc
    def current_overrides():
        return dict(_overrides())

    @reactive.effect
    @reactive.event(input.override_set)
    def _apply_override():
        ev = input.override_set() or {}
        mid, rating = ev.get("mid"), ev.get("rating")
        if not mid:
            return
        gen = None                          # the computed (generated) rating for this metric
        for row in ((base_result() or {}).get("report") or {}).get("metricRows", []):
            if row.get("metricId") == mid:
                gen = row.get("generatedRating")
                break
        cur = dict(_overrides())
        if rating in ("Good", "Fair", "Poor") and rating != gen:
            cur[mid] = rating
        else:                               # picking the computed value (or clearing) reverts
            cur.pop(mid, None)
        _overrides.set(cur)
        if mid in _geom_owned():             # a manual pick takes ownership from the geometry
            _geom_owned.set(_geom_owned() - {mid})
            _geom_text.set({k: v for k, v in _geom_text().items() if k != mid})
            _geom_scoring.set({k: v for k, v in _geom_scoring().items() if k != mid})

    @reactive.effect
    @reactive.event(input.note_set)
    def _apply_note():
        ev = input.note_set() or {}
        mid, text = ev.get("mid"), (ev.get("text") or "").strip()
        if not mid:
            return
        cur = dict(_notes())
        if text:
            cur[mid] = text
        else:
            cur.pop(mid, None)
        _notes.set(cur)

    # ---- editable cross-section geometry (bankfull / low-bank heights) + which of
    #      the candidate transects (upstream / middle / downstream) is selected -------
    _xs_unit_prev = reactive.value("ft")  # tracks the unit for input conversion
    _xs_sel = reactive.value(None)        # selected candidate index; None -> stored default

    @reactive.calc
    def _xs_cross():
        return ((base_result() or {}).get("report") or {}).get("crossSection") or {}

    @reactive.calc
    def _xs_candidates():
        return _xs_cross().get("candidates") or []

    @reactive.calc
    def _xs_default_sel():
        return int(_xs_cross().get("selected", 0) or 0)

    @reactive.calc
    def _xs_sel_idx():
        cands = _xs_candidates()
        if not cands:
            return 0
        s = _xs_sel()
        s = _xs_default_sel() if s is None else int(s)
        return min(max(s, 0), len(cands) - 1)

    @reactive.calc
    def _xs_block():
        cands = _xs_candidates()
        block = cands[_xs_sel_idx()] if cands else _xs_cross().get("geom")
        return block if (block and block.get("thalweg") is not None) else None

    @reactive.calc
    def current_geometry():
        """Current bankfull/floodplain stages (metres) from the edit inputs, or None."""
        block = _xs_block()
        if not block:
            return None
        try:
            unit, bf_h, lb_h = input.xs_unit(), input.xs_bankfull(), input.xs_lowbank()
        except Exception:
            return None
        if bf_h is None or lb_h is None:
            return None
        per_m = FT_PER_M if unit == "ft" else 1.0
        thalweg = block["thalweg"]
        return {"block": block, "unit": unit,
                "bankfull_stage": thalweg + float(bf_h) / per_m,
                "floodplain_stage": thalweg + float(lb_h) / per_m}  # low-bank stage (BHR)

    @reactive.calc
    def _geom_edited():
        """True only when the heights differ from the Bieger default. Compares in the
        *display* unit at display precision so the round-trip through the 2-dp inputs
        (feet by default) never reads as an edit on its own."""
        block = _xs_block()
        if not block:
            return False
        try:
            unit, bf_h, lb_h = input.xs_unit(), input.xs_bankfull(), input.xs_lowbank()
        except Exception:
            return False
        if bf_h is None or lb_h is None:
            return False
        per_m = FT_PER_M if unit == "ft" else 1.0
        thal = block["thalweg"]
        bf_def = round((block["bankfull_stage"] - thal) * per_m, 2)
        lb_def = round((block["floodplain_stage"] - thal) * per_m, 2)
        return abs(float(bf_h) - bf_def) > 0.005 or abs(float(lb_h) - lb_def) > 0.005

    def _set_geom_metrics(block, bankfull_stage, floodplain_stage, own):
        """Own (own=True) or release the cross-section-derived metric ratings
        (floodplain access ER, high flow + channel evolution BHR). Shared by geometry
        edits and candidate switching; ``_geom_text`` carries each row's value text."""
        cur = dict(_overrides())
        texts = dict(_geom_text())
        traces = dict(_geom_scoring())
        owned = set(_geom_owned())
        if own and block:
            derived = assessment.rate_metrics_from_stages(block, bankfull_stage, floodplain_stage)
            new_owned = set()
            for mid, info in derived.items():
                if info.get("rating"):
                    cur[mid] = info["rating"]
                    texts[mid] = info.get("valueText", "")
                    traces[mid] = info.get("scoring")
                    new_owned.add(mid)
            for mid in owned - new_owned:
                cur.pop(mid, None)
                texts.pop(mid, None)
                traces.pop(mid, None)
            _overrides.set(cur)
            _geom_text.set(texts)
            _geom_scoring.set(traces)
            _geom_owned.set(new_owned)
        elif owned:  # back to the default candidate, unedited -> release
            for mid in owned:
                cur.pop(mid, None)
                texts.pop(mid, None)
            _overrides.set(cur)
            _geom_text.set(texts)
            _geom_owned.set(set())

    @reactive.effect
    @reactive.event(input.xs_bankfull, input.xs_lowbank)
    def _xs_rerate():
        """A height edit (or a non-default candidate) drives the 3 cross-section metrics;
        a manual dropdown pick wins until the next geometry change (last-action-wins)."""
        if not _xs_block():
            return
        g = current_geometry()
        own = bool(g and (_geom_edited() or _xs_sel_idx() != _xs_default_sel()))
        _set_geom_metrics(g["block"] if g else None,
                          g["bankfull_stage"] if g else None,
                          g["floodplain_stage"] if g else None, own)

    def _select(delta):
        """Cycle the selected candidate cross-section (wrap-around), reset the height
        inputs to its defaults, and re-rate the metrics from it."""
        cands = _xs_candidates()
        if len(cands) < 2:
            return
        new = (_xs_sel_idx() + delta) % len(cands)
        _xs_sel.set(new)
        block = cands[new]
        per_m = FT_PER_M if input.xs_unit() == "ft" else 1.0
        thal = block["thalweg"]
        ui.update_numeric("xs_bankfull", value=round((block["bankfull_stage"] - thal) * per_m, 2))
        ui.update_numeric("xs_lowbank", value=round((block["floodplain_stage"] - thal) * per_m, 2))
        _set_geom_metrics(block, block["bankfull_stage"], block["floodplain_stage"],
                          new != _xs_default_sel())

    @reactive.effect
    @reactive.event(input.xs_prev)
    def _xs_go_prev():
        _select(-1)

    @reactive.effect
    @reactive.event(input.xs_next)
    def _xs_go_next():
        _select(+1)

    @reactive.calc
    def scored():
        base = base_result()
        if not base:
            return None
        sc = assessment.rescore(base["report"], dict(current_overrides()))
        owned = _geom_owned()
        if owned:  # relabel so an edited cross-section doesn't read as a manual override
            texts = _geom_text()
            traces = _geom_scoring()
            for row in sc["metricRows"]:
                mid = row["metricId"]
                if mid in owned:
                    row["status"] = "xs-derived"
                    row["source"] = "edited cross-section"
                    row["valueText"] = texts.get(mid) or f"from edited cross-section: {row['rating']}"
                    row["note"] = "recomputed from your bankfull/floodplain heights"
                    # carry the recomputed trace so the Scoring method panel shows the
                    # edited geometry, not the geometry the run started from
                    trace = traces.get(mid)
                    if trace:
                        row["scoring"] = trace
                        row["generatedRating"] = trace.get("generatedRating")
                        row["completeness"] = trace.get("completeness", row.get("completeness"))
        return sc

    @reactive.calc
    def xs_render():
        """The cross-section to show/export: recomputed when edited (or on a unit
        switch), else the original render (which matches the metric table)."""
        base = base_result()
        if not base:
            return None
        base_xs = (base["report"].get("crossSection") or {})
        g = current_geometry()
        is_default = (g and not _geom_edited() and g["unit"] == "ft"
                      and _xs_sel_idx() == _xs_default_sel())
        if not g or is_default:
            return base_xs
        try:
            if _geom_edited():
                return assessment.cross_section_from_stages(
                    g["block"], g["bankfull_stage"], g["floodplain_stage"], unit=g["unit"])
            # a non-default candidate (or unit switch) at its default stages -> its ER/BHR
            return assessment.cross_section_from_stages(
                g["block"], g["bankfull_stage"], g["floodplain_stage"], unit=g["unit"],
                er=g["block"].get("entrenchment_ratio"), bhr=g["block"].get("bank_height_ratio"),
                edited=False)
        except Exception:  # noqa: BLE001
            return base_xs

    @reactive.calc
    def export_result():
        base, sc = base_result(), scored()
        if not base or not sc:
            return None
        notes = _notes()
        rows = [{**r, "userNote": notes.get(r["metricId"], "")} for r in sc["metricRows"]]
        return {**base, "report": {**sc, "metricRows": rows,
                                   "crossSection": xs_render() or sc.get("crossSection")}}

    # ---- left pane (state machine) ----
    @render.ui
    def leftpane():
        # Batch mode is a full-screen takeover: drop the single-site card entirely
        # (mirror of batch_workspace, which returns None outside batch mode).
        if app_mode() == "batch":
            return None
        step = current_step()
        if step in (STEP_ASSESS, STEP_REPORT):
            return None            # the full-width Assessment worksheet replaces the left pane
        if step == STEP_IDENTIFY:
            # initial disabled state from the current point, without making the pane
            # re-render on every snap (the toggle effect updates it live)
            with reactive.isolate():
                picked = snapped_point() is not None
            body = ui.TagList(
                ui.div("Zoom in until blue stream lines appear and click a stream to place "
                       "a point. Or enter coordinates below, or search an address.",
                       class_="easi-instr"),
                ui.input_text("address", "Address, place, or stream",
                              placeholder="e.g. Atlanta, GA  ·  Utoy Creek"),
                ui.input_action_button("find_address", "Find on map",
                                       class_="btn-outline-secondary btn-sm"),
                ui.div("Type to search. Suggestions from OpenStreetMap / Photon.",
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
            body = ui.TagList(ui.output_ui("basin_card"),
                              # === TEMP: MMW comparison overlay checkbox (remove later) ===
                              (ui.div(ui.input_checkbox("show_mmw",
                                                        "Overlay MMW watershed (comparison)",
                                                        value=False),
                                      # suppress the auto .recalculating spinner on the
                                      # status text so it never jitters the panel
                                      ui.tags.style(
                                          "#mmw_status.recalculating{min-height:0!important;"
                                          "opacity:1!important}"
                                          "#mmw_status.recalculating::after{display:none!important}"),
                                      ui.div(ui.output_text("mmw_status"),
                                             style="font-size:12px;color:#667;min-height:1em;"
                                                   "margin:-.1rem 0 .2rem;"),
                                      style="margin:.4rem 0;")
                               if SHOW_MMW_OVERLAY else None),
                              # === END TEMP ===
                              ui.div(ui.input_action_button("clear_basin", "Clear",
                                                            class_="btn-outline-secondary"),
                                     ui.input_action_button("run_screening", "Run screening",
                                                            class_="btn-primary"),
                                     class_="easi-pane-actions"),
                              ui.output_text("busy_text"))
        active = current_step()
        head_label = dict(STEP_LABELS).get(active, "EASI")
        return ui.TagList(
            ui.div(f"EASI · {head_label}", class_="easi-pane-head"),
            ui.div(_stepper(active), body, class_="easi-pane-body"),
        )

    @render.ui
    def snap_status():
        cue = stage()
        if cue in (_FINDING_TEXT, _LOCATING_TEXT):
            return ui.p(cue, class_="easi-snap-note")
        err = anchor_error()
        if err:
            return ui.p(f"⚠ {err}", class_="easi-snap-note",
                        style="color:#8a5a00;")
        anchor = pending_anchor()
        if anchor:
            # Three short lines; the ratio, the COMID, and the reasoning sit
            # behind the info icon (easi.snapcard, 2026-09-02).
            card = hr_snap_card(anchor)
            lines = []
            for i, (cls, text) in enumerate(card["lines"]):
                kids = [text]
                if i == 2:
                    kids += [" ", _info(html_tip=card["tip_html"])]
                lines.append(ui.p(*kids, class_=f"easi-snap-note {cls}".strip()))
            return ui.div(*lines)
        pt = snapped_point()
        if not pt:
            return ui.p("No point yet. Enter coordinates, search an address, or zoom in "
                        "and click a blue stream line.", class_="easi-snap-note")
        return ui.p(f"✓ Snapped to stream ({pt[2]:.0f} ft away). "
                    f"Click “Delineate Basin and Reach”.",
                    class_="easi-snap-note ok")

    @render.ui
    def basin_card():
        res = delin() or {}
        d = res.get("delineation") or {}
        if not d:
            return None
        def row(label, val):
            return ui.div(ui.span(label), ui.tags.b(str(val)), class_="b-row")
        anchor = res.get("siteAnchor") or {}
        anchor_rows = []
        comid_label = "COMID"
        if anchor.get("anchorKind") == "hrSurrogate":
            clicked_s = anchor.get("clickedStream") or {}
            r = anchor.get("routing") or {}
            dist = r.get("routedDistanceFt")
            source = d.get("watershed_source") or ""
            eng = d.get("watershed_engine") or {}
            if source == "site-engine":
                anchor_rows = [
                    row("Watershed engine", f"STAF site engine v{eng.get('engineVersion')}"),
                    row("Exact watershed area", f"{eng.get('areaSqkm')} km²"),
                    row("Reaches walked", eng.get("nReaches")),
                ]
            elif source == "not-calculated":
                anchor_rows = [
                    row("Watershed engine",
                        f"unavailable ({eng.get('reason') or 'not calculated'})"),
                ]
            else:
                anchor_rows = [row("Scored at", "surrogate reach")]
            anchor_rows += [
                row("Reach-keyed evidence",
                    "unavailable past the substitution limit" if r.get("declined")
                    else (f"nearest covered reach, "
                          f"{dist:,.0f} ft downstream" if dist is not None
                          else "nearest covered reach")),
                row("Drainage area ratio",
                    f"{r.get('daRatio') if r.get('daRatio') is not None else 'unknown'} "
                    f"(limit {_fmt_ratio_limit(r.get('daRatioLimit'))})"),
            ]
            if source in ("site-engine", "not-calculated"):
                comid_label = "Evidence reach COMID"
        return ui.div(
            ui.h5(d.get("gnis_name") or "(unnamed reach)"),
            *anchor_rows,
            row("Drainage area", f'{d.get("drainage_area_sqkm")} km²'),
            row("Reach length", f'{d.get("reach_length_ft")} ft'),
            row(comid_label, d.get("comid")),
            class_="easi-basin-card",
        )

    @render.ui
    def anchor_ribbon():
        # One-line persistent reminder in the Assessment worksheet for routed sites.
        anchor = (delin() or {}).get("siteAnchor") or {}
        if anchor.get("anchorKind") != "hrSurrogate":
            return None
        clicked_s = anchor.get("clickedStream") or {}
        d = (delin() or {}).get("delineation") or {}
        source = d.get("watershed_source") or ""
        r = anchor.get("routing") or {}
        if source == "site-engine":
            text = ("Exact watershed: watershed metrics come from the STAF site "
                    "engine. Reach-keyed metrics "
                    + ("are unavailable past the substitution limit."
                       if r.get("declined") else
                       "describe the nearest covered reach downstream."))
        elif source == "not-calculated":
            text = ("Exact watershed not calculated: watershed metrics are "
                    "unavailable for this stream.")
        else:
            text = (f"Surrogate reach: results describe "
                    f"{d.get('gnis_name') or 'the nearest covered reach'}, not the "
                    f"clicked stream ({clicked_s.get('gnisName') or 'unnamed'}).")
        return ui.div(
            "⚠ " + text,
            style=("background:#fff7e0;border:1px solid #e6c96b;border-radius:6px;"
                   "padding:.3rem .5rem;margin:.3rem 0;font-size:12px;"))

    @render.text
    def busy_text():
        s = stage()
        running = (delineate_task.status() == "running") or (assess_task.status() == "running")
        if _HAS_MAP:
            running = running or any(t.status() == "running"
                                     for t in (click_snap_task, coord_snap_task, route_task))
        # A text output updates its textContent in place, so the row never reflows
        # as "3/20" ticks; the spinner is a CSS ::before on the persistent #busy_text
        # element (spins continuously). Empty string -> row collapses (no idle gap).
        return s if (s and running) else ""

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
        # Cue the user that the clickable blue stream vectors are being fetched —
        # only while in the identify step, zoomed in enough for them to appear, and
        # a fetch is actually in flight.
        if not _HAS_MAP or current_step() != STEP_IDENTIFY:
            return None
        z, _c = _view()
        if z is None or z < FLOW_ZOOM or flow_task.status() != "running":
            return None
        return ui.div(ui.div(class_="easi-spinner"), ui.span("Loading streams…"),
                      class_="easi-flow-loading")

    @render.ui
    def cursor_style():
        # When a point can be selected (identify step, zoomed in to the vectors),
        # show a crosshair; leaflet swaps to a grabbing hand while dragging.
        z, _c = _view()
        picking = (current_step() == STEP_IDENTIFY and z is not None and z >= FLOW_ZOOM)
        if not picking:
            return None
        # leaflet sets `cursor:grab` inline on the container, so override with !important
        return ui.tags.style(
            ".easi-map-wrap .leaflet-grab{cursor:crosshair !important;}"
            ".easi-map-wrap .leaflet-container.leaflet-dragging,"
            ".easi-map-wrap .leaflet-container.leaflet-dragging .leaflet-grab"
            "{cursor:grabbing !important;}")

    # ==================================================================== #
    # Assessment worksheet (full-width overlay: functions rail + one function
    # at a time + roll-up rail). Ports the SFARI/DEEP layout to EASI.
    # ==================================================================== #
    def _cur_fn():
        return _FUNCTIONS[max(0, min(len(_FUNCTIONS) - 1, current_fn()))]

    def _xs_editor():
        """The editable cross-section embedded in the 3 XS-metric cards. Same inputs/outputs
        as the old report panel (so the whole XS reactive pipeline is reused verbatim), but
        seeded from the LIVE input values so an edit persists when navigating among the XS
        cards (Shiny retains an input's value across the fn-panel re-render)."""
        with reactive.isolate():
            block = _xs_block()
            cands = _xs_candidates()
            try:
                bf_cur, lb_cur, unit_cur = input.xs_bankfull(), input.xs_lowbank(), input.xs_unit()
            except Exception:  # noqa: BLE001 — inputs not created on the first XS-card visit
                bf_cur = lb_cur = None
                unit_cur = "ft"
        if not block:
            return ui.div("Cross-section geometry is unavailable for this reach.",
                          class_="easi-instr")
        unit0 = unit_cur or "ft"
        per_m = FT_PER_M if unit0 == "ft" else 1.0
        thal = block["thalweg"]
        bf0 = bf_cur if bf_cur is not None else round((block["bankfull_stage"] - thal) * per_m, 2)
        lb0 = lb_cur if lb_cur is not None else round((block["floodplain_stage"] - thal) * per_m, 2)
        bk_area = block.get("bankfull_area_m2")
        area_txt = f"{bk_area:.1f} m² " if bk_area is not None else ""
        bf_tip = (f"The depth at which the channel cross-sectional area equals the Bieger et al. "
                  f"(2015) regional bankfull area ({area_txt}for the "
                  f"{block.get('division') or 'national'} division). Edit to use a surveyed value.")
        lb_tip = ("The first break in slope to a flat depositional surface (bank) on the DEM "
                  "profile, capped at the floodprone elevation. Drives the bank-height ratio. "
                  "Edit to use a surveyed value.")
        panel = ui.div(
            ui.div("Cross-section geometry", class_="easi-xs-panel-title"),
            ui.output_ui("xs_summary"),
            ui.div(
                ui.input_numeric("xs_bankfull", ui.span("Bankfull depth ", _info(text=bf_tip)),
                                 value=bf0, min=0, step=0.1),
                ui.input_numeric("xs_lowbank", ui.span("Low bank height ", _info(text=lb_tip)),
                                 value=lb0, min=0, step=0.1),
                class_="easi-xs-fields"),
            ui.input_radio_buttons("xs_unit", None, {"ft": "Feet", "m": "Meters"},
                                   selected=unit0, inline=True),
            class_="easi-xs-panel")
        switch = ui.div(
            ui.input_action_button("xs_prev", "◀", class_="easi-xs-arrow"),
            ui.output_ui("xs_selector"),
            ui.input_action_button("xs_next", "▶", class_="easi-xs-arrow"),
            class_="easi-xs-switch") if len(cands) >= 2 else None
        # Hidden output publishing the windowed default view. www/worksheet.js injects
        # "Zoom Home" (reframe to this window) and "Zoom to Extents" (autorange) into the
        # plot's OWN modebar and handles the clicks via Plotly.relayout — plotly config
        # can't carry JS click handlers through shinywidgets, the FigureWidget never
        # captures a reliable _rangeInitial, and Python range traits go stale after a
        # front-end zoom. Interactive widget only.
        xs_win_pub = (ui.div(ui.output_text("xs_window_range"), class_="easi-xs-winrange")
                      if _HAS_PLOTLY else None)
        head = ui.div(ui.span("Representative cross-section", class_="easi-xs-plot-title"),
                      switch, xs_win_pub, class_="easi-xs-plot-head")
        plot = (ui.div(output_widget("xsection_plot", height="100%"), class_="easi-xsection")
                if _HAS_PLOTLY else ui.output_ui("xsection"))
        right = ui.div(head, plot, class_="easi-xs-right")
        return ui.div(panel, right, class_="easi-xsection-wrap easi-xs-in-card")

    @render.ui
    def worksheet():
        if app_mode() == "batch":
            return None
        step = current_step()
        if step not in (STEP_ASSESS, STEP_REPORT):
            return None
        return ui.div(
            ui.div(
                ui.div("EASI · Assessment", class_="easi-pane-head"),
                ui.output_ui("anchor_ribbon"),
                ui.div(_stepper(step), class_="sfari-nav-steps"),
                ui.output_ui("fn_nav"),
                class_="sfari-nav"),
            ui.div(ui.output_ui("fn_panel"), class_="sfari-fnpanel"),
            ui.div(ui.output_ui("rollup_rail"), class_="sfari-rollup"),
            class_="sfari-worksheet")

    @render.ui
    def fn_nav():
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        sc = scored()
        fscores = (sc or {}).get("functionScores", {})
        cur = current_fn()
        items = []
        prev_disc = None
        for i, fn in enumerate(_FUNCTIONS):
            disc = fn.get("category", "")
            if disc != prev_disc:
                items.append(ui.div(disc, class_="sfari-nav-cat"))
                prev_disc = disc
            fs = fscores.get(fn["id"])
            dot = scoring.function_score_band_color(fs) if fs is not None else "#dfe4ec"
            cls = "sfari-nav-fn" + (" active" if i == cur else "")
            items.append(ui.div(ui.span(class_="sfari-nav-dot", style=f"background:{dot};"),
                                ui.span(fn.get("name", "")),
                                {"data-idx": str(i)}, class_=cls))
        return ui.TagList(*items)

    @render.ui
    def fn_panel():
        # Skeleton: depends only on current_fn + compute-state, so the Plotly cross-section
        # widget mounts once per XS-card visit; the live rating/score are nested slots that
        # re-render on scored(). Notes + source select are seeded under isolate (no re-render).
        if app_mode() == "batch" or current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        if base_result() is None:
            st = assess_task.status()
            if st in ("running", "initial"):
                # The stage text is a nested output: reading stage() here would re-render
                # this panel on every 0.3s poll, recreating the spinner node and restarting
                # its CSS animation (the quarter-turn stutter).
                return ui.div(ui.div(class_="easi-spinner"),
                              ui.output_text("assess_stage_label", inline=True),
                              ui.div("The screening report opens automatically when it's ready.",
                                     class_="easi-fn-compute-sub"),
                              class_="easi-fn-compute")
            if st == "error":
                return ui.div(
                    ui.p("Metric computation failed."),
                    ui.tags.button("Retry", {"data-step": "assess", "type": "button"},
                                   class_="sfari-btn primary"),
                    class_="easi-fn-compute")
            return ui.div("Run a screening from the Basin step.", class_="easi-instr")
        idx = max(0, min(len(_FUNCTIONS) - 1, current_fn()))
        fn = _FUNCTIONS[idx]
        fid = fn["id"]
        meta = _METRIC_BY_FID.get(fid) or {}
        mid = meta.get("metricId")
        is_xs = fid in XS_FUNCTION_IDS
        with reactive.isolate():
            note0 = (_notes() or {}).get(mid, "")
            brow = next((r for r in ((base_result() or {}).get("report") or {}).get("metricRows", [])
                         if r["metricId"] == mid), None) or {}
        card = ui.div(
            ui.div(ui.span("1", class_="sfari-step-num"),
                   ui.span("Score this metric", class_="sfari-sec-title"),
                   class_="sfari-sec-lbl"),
            ui.div(ui.span(meta.get("name", ""), class_="easi-metric-title"),
                   class_="sfari-metric-name"),
            (ui.div(meta.get("metricStatement", ""), class_="sfari-metric-statement")
             if meta.get("metricStatement") else None),
            ui.output_ui("fn_metric_live"),
            _method_expander(mid, _active_scoring(brow, None)),
            ui.tags.textarea(note0, {"class": "easi-note-ta", "data-mid": mid, "rows": "2",
                                     "placeholder": "Add a note for this metric…"}),
            _xs_editor() if is_xs else None,
            class_="sfari-metric easi-metric-card")
        prev_attrs = {"data-nav": "-1", "type": "button"}
        if idx == 0:
            prev_attrs["disabled"] = "disabled"
        footer = ui.div(
            ui.div(ui.tags.button("‹ Previous", prev_attrs, class_="sfari-btn"),
                   class_="sfari-foot-left"),
            ui.div(class_="sfari-foot-status"),
            ui.tags.button("Next function ›" if idx < len(_FUNCTIONS) - 1 else "Done",
                           {"data-nav": "1", "type": "button"}, class_="sfari-btn primary"),
            class_="sfari-nav-actions")
        return ui.div(
            ui.div(ui.span(f"Function {idx + 1} of {len(_FUNCTIONS)} · {fn.get('category', '')}",
                           class_="easi-fn-eyebrow"),
                   ui.span(fn["name"], class_="easi-fn-name"), class_="easi-fn-head"),
            card,
            ui.output_ui("fn_scorecard"),
            ui.div(footer, class_="sfari-fn-footer"),
            class_="sfari-fnpanel-inner")

    def _cur_row(sc):
        fid = _cur_fn()["id"]
        mid = (_METRIC_BY_FID.get(fid) or {}).get("metricId")
        return mid, next((r for r in sc["metricRows"] if r["metricId"] == mid), None) or {}

    @render.text
    def assess_stage_label():
        return stage() or "Computing metrics…"

    @render.ui
    def fn_metric_live():
        # Desktop evidence + override select — re-renders on scored() (override / source swap / XS edit).
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        sc = scored()
        if not sc:
            return None
        current_fn()  # depend on the active function so the slot follows navigation
        mid, row = _cur_row(sc)
        gen = row.get("generatedRating")
        chip = (ui.tags.button(f"use {gen}", {"data-suggest": mid, "type": "button",
                                              "title": f"Restore the desktop rating ({gen})"},
                               class_="sfari-suggest-chip")
                if gen and row.get("rating") != gen else None)
        return ui.div(
            ui.div(ui.span("desktop", class_="sfari-ev-tag"),
                   ui.tags.b(row.get("valueText") or "—", class_="sfari-ev-val"),
                   chip, class_="sfari-evidence"),
            ui.div(_rate_select(mid, row), class_="easi-rate-cell"),
            class_="easi-metric-live")

    def _cur_method():
        """(mid, row, method, site_inputs) for the active function's Scoring-method panel."""
        sc = scored()
        if not sc:
            return None
        current_fn()
        mid, row = _cur_row(sc)
        trace = (row or {}).get("scoring") or {}
        method = easi_methods.resolve(mid, trace.get("methodKey"), trace.get("context"))
        if method is None:
            return None
        return mid, row, trace, method, _trace_values(trace)

    # suspend_when_hidden=False: these two slots live inside a collapsed <details> (display:none),
    # which Shiny would otherwise suspend (never compute) until first opened — so the body and
    # criteria would be blank on open. They read scored() and re-render in place on a source swap
    # or override, keeping the fn_panel skeleton (and the mounted cross-section widget) untouched.
    @output(suspend_when_hidden=False)
    @render.ui
    def method_body():
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        ctx = _cur_method()
        if ctx is None:
            return None
        _mid, row, _trace, method, site_inputs = ctx
        return _method_body_ui(method, row, site_inputs)

    @output(suspend_when_hidden=False)
    @render.ui
    def method_criteria():
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        ctx = _cur_method()
        if ctx is None:
            return None
        return _method_criteria_ui(ctx[1], ctx[3])

    @render.ui
    def fn_scorecard():
        # Read-only derived-score gauge (0-15), band pill, and an "(auto: N)" cue when overridden.
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        sc = scored()
        if not sc:
            return None
        current_fn()
        mid, row = _cur_row(sc)
        fs = row.get("functionScore")
        if fs is None:
            band_lbl, band_col, knob = "Not scored yet", "#e7ebf1", None
        else:
            # written-out band label (Functioning / Functioning-at-Risk / Non-Functioning),
            # matching SFARI's scorecard badge; the short F/AR/NF form stays report-only
            band_lbl = scoring.index_band_label(fs / config.FUNCTION_SCORE_MAX)
            band_col = scoring.function_score_band_color(fs)
            knob = max(0.0, min(100.0, fs / config.FUNCTION_SCORE_MAX * 100))
        cue = None
        if row.get("status") == "override" and row.get("generatedRating") in config.RATINGS:
            auto = scoring.function_score(
                scoring.rating_to_index(row["generatedRating"],
                                        _METRICS.get(mid, {}).get("indexMidpoints")))
            cue = ui.span(f"(auto: {auto})", class_="easi-auto-cue")
        stmt = (_METRICS.get(mid, {}).get("functionStatement")
                or _cur_fn().get("function_statement") or "")
        return ui.div(
            ui.div(ui.span("2", class_="sfari-step-num"),
                   ui.span("Function score", class_="easi-fscore-lbl"),
                   _info(html_tip=(
                       '<div class="easi-tip-sec">Calculated automatically from the metric '
                       'rating; EASI derives the 0-15 score, so there is no manual scoring.'
                       '</div>'
                       '<div class="easi-tip-sec">'
                       '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span>'
                       '<span><b>0-5:</b> Non-Functioning (Poor rating)</span></div>'
                       '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span>'
                       '<span><b>6-10:</b> Functioning-at-Risk (Fair rating)</span></div>'
                       '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span>'
                       '<span><b>11-15:</b> Functioning (Good rating)</span></div>'
                       '</div>')),
                   class_="easi-fscore-head"),
            (ui.p(stmt, class_="sfari-fn-statement") if stmt else None),
            ui.div(
                ui.div(ui.div({"style": "" if knob is None else f"left:{knob:.1f}%;"},
                              class_="easi-fscore-knob"), class_="easi-fscore-track"),
                ui.span("–" if fs is None else str(fs), class_="easi-fscore-num"),
                ui.span(band_lbl, class_="easi-fscore-band", style=f"background:{band_col};"),
                cue, class_="easi-fscore-row"),
            class_="easi-scorecard" + ("" if fs is not None else " unset"))

    @render.ui
    def rollup_rail():
        if current_step() not in (STEP_ASSESS, STEP_REPORT):
            return None
        sc = scored()
        if not sc:
            return None          # empty rail until there's a rollup to show
        eci = sc["ecosystemConditionIndex"]
        sub = sc["subIndices"]
        rows = sc["metricRows"]
        n_total = len(rows)
        n_scored = sum(1 for r in rows if r.get("rating"))
        knob = max(2.0, min(98.0, (eci or 0.0) * 100))
        report_primary = bool(n_total) and n_scored == n_total
        pct = (n_scored / n_total * 100) if n_total else 0
        return ui.TagList(
            ui.div(ui.div("–" if eci is None else f"{eci:.2f}", class_="sfari-eci"),
                   ui.div("Ecosystem Condition Index", class_="sfari-eci-lbl"),
                   ui.div(ui.div(class_="sfari-eci-knob", style=f"left:{knob:.1f}%;"),
                          class_="sfari-eci-track"),
                   class_="sfari-eci-box"),
            _bar("Physical", sub["physical"], scoring.index_band_color(sub["physical"])),
            _bar("Chemical", sub["chemical"], scoring.index_band_color(sub["chemical"])),
            _bar("Biological", sub["biological"], scoring.index_band_color(sub["biological"])),
            ui.div(ui.tags.span(style=f"width:{pct:.0f}%;"), class_="sfari-progress-bar"),
            ui.div(f"{n_scored} / {n_total} functions computed", class_="sfari-progress"),
            ui.tags.button("Open report", {"data-report": "1", "type": "button"},
                           class_="sfari-btn sfari-rollup-report"
                           + (" primary" if report_primary else "")),
        )

    @render.ui
    def xsection():
        xs = xs_render() or {}
        if not xs.get("png_b64"):
            return None
        return ui.div(
            ui.tags.img(src=f"data:image/png;base64,{xs['png_b64']}"),
            class_="easi-xsection",
        )

    if _HAS_PLOTLY:
        @render_widget
        def xsection_plot():
            """Interactive cross-section (Plotly): drag-box zoom, pan, hover, and
            modebar/double-click reset. Built ONCE — the profile/stages/unit are read
            under ``reactive.isolate`` so this render has no reactive dependencies and
            never re-runs. Candidate switches, height edits, and unit toggles are applied
            in place by ``_sync_xsection_plot`` below, so the widget never unmounts /
            remounts (that DOM churn was the flicker). The PDF still uses the matplotlib
            PNG from ``xs_render``."""
            with reactive.isolate():
                block = _xs_block()
                if not block:
                    return None
                g = current_geometry()
                unit = g["unit"] if g else "ft"
                bankfull_stage = g["bankfull_stage"] if g else block.get("bankfull_stage")
                floodplain_stage = g["floodplain_stage"] if g else block.get("floodplain_stage")
            import plotly.graph_objects as go
            from easi import xsplotly
            fw = go.FigureWidget(xsplotly.figure(
                block["stations"], block["elevs"], thalweg=block["thalweg"],
                bankfull_stage=bankfull_stage, floodplain_stage=floodplain_stage,
                unit=unit, source=block.get("dem_source")))
            fw._config = {"displaylogo": False}   # hide the Plotly logo (config-only)
            return fw

        def _xs_src_figure():
            """Build a fresh source ``go.Figure`` (windowed default view) from the CURRENT
            block + edited geometry. Shared by the in-place sync and the "Reset view" button
            so both frame identically. Returns ``(block, src)`` or ``(None, None)``."""
            block = _xs_block()
            if not block:
                return None, None
            g = current_geometry()
            unit = g["unit"] if g else "ft"
            bankfull_stage = g["bankfull_stage"] if g else block.get("bankfull_stage")
            floodplain_stage = g["floodplain_stage"] if g else block.get("floodplain_stage")
            from easi import xsplotly
            src = xsplotly.figure(
                block["stations"], block["elevs"], thalweg=block["thalweg"],
                bankfull_stage=bankfull_stage, floodplain_stage=floodplain_stage, unit=unit,
                source=block.get("dem_source"))
            return block, src

        # suspend_when_hidden=False: this output lives in a display:none span, which Shiny
        # would otherwise suspend (never compute), leaving the Reset view button no window.
        @output(suspend_when_hidden=False)
        @render.text
        def xs_window_range():
            """Publish the current windowed default view as JSON ``{"x":[lo,hi],"y":[lo,hi]}``
            for the client "Reset view" button (www/worksheet.js). Recomputed from the live
            geometry so reset targets the right window after edits / candidate switches."""
            _block, src = _xs_src_figure()
            if src is None:
                return ""
            return json.dumps({"x": list(src.layout.xaxis.range),
                               "y": list(src.layout.yaxis.range)})

        @reactive.effect
        def _sync_xsection_plot():
            """Update the live cross-section figure IN PLACE when the selected candidate,
            edited heights, or unit change. Mutating the existing FigureWidget (rather than
            returning a new one from the render) is what removes the flicker: no DOM
            remount, and the trace count stays fixed (see ``xsplotly.figure``), so this is
            a single batched restyle/relayout. ``xsection_plot.widget`` is reactive and
            ``req()``-waits until the widget has first rendered, so ordering is safe."""
            w = xsection_plot.widget      # reactive: req()-waits until the widget exists
            block, src = _xs_src_figure()
            if w is None or not block:
                return
            with w.batch_update():        # one atomic client update -> no flash
                for wt, st in zip(w.data, src.data):
                    wt.x, wt.y = st.x, st.y
                    wt.fillcolor = st.fillcolor        # blue water vs. transparent (no bankfull)
                    wt.hovertemplate = st.hovertemplate  # carries the unit in the bed-line hover
                w.layout.shapes = tuple(s.to_plotly_json() for s in src.layout.shapes)
                w.layout.annotations = tuple(a.to_plotly_json() for a in src.layout.annotations)
                w.layout.xaxis.autorange = False
                w.layout.yaxis.autorange = False
                w.layout.xaxis.range = src.layout.xaxis.range
                w.layout.yaxis.range = src.layout.yaxis.range
                w.layout.xaxis.title.text = src.layout.xaxis.title.text
                w.layout.yaxis.title.text = src.layout.yaxis.title.text

    @render.ui
    def xs_selector():
        cands = _xs_candidates()
        if len(cands) < 2:
            return None
        i = _xs_sel_idx()
        label = cands[i].get("label") or str(i + 1)
        return ui.span(f"{label} ({i + 1} of {len(cands)})", class_="easi-xs-switch-lbl")

    @render.ui
    def xs_summary():
        """Computed cross-section metrics (left panel), recomputed live from the
        current bankfull/low-bank heights so the table always matches the plot and
        the floodplain metric ratings."""
        block = _xs_block()
        if not block:
            return None
        g = current_geometry()
        unit = (g or {}).get("unit", "ft")
        ul = "ft" if unit == "ft" else "m"
        per_m = FT_PER_M if unit == "ft" else 1.0
        if g:  # live values at the current (default or edited) stages
            d = geomorph.derive_from_stages(
                block["stations"], block["elevs"], thalweg=block["thalweg"],
                bankfull_stage=g["bankfull_stage"], floodplain_stage=g["floodplain_stage"])
        else:  # stored defaults
            d = block
        er, bhr = d.get("entrenchment_ratio"), d.get("bank_height_ratio")
        bf_w, fp_w = d.get("bankfull_width_m"), d.get("flood_prone_width_m")
        edge = d.get("edge_limited")

        def wd(x):
            return f"{x * per_m:.1f} {ul}" if x is not None else "n/a"

        def rt(x):
            return f"{x:.2f}" if x is not None else "n/a"

        def ar(m2):  # cross-sectional area in the selected unit (ft² or m²)
            if m2 is None:
                return "n/a"
            return f"{m2 * per_m * per_m:.1f} ft²" if unit == "ft" else f"{m2:.1f} m²"

        # measured channel area at the current bankfull stage (updates when edited)
        bf_stage = g["bankfull_stage"] if g else block.get("bankfull_stage")
        bf_area = (geomorph.flow_area(block["stations"], block["elevs"], bf_stage)[0]
                   if bf_stage is not None else None)

        region = block.get("division") or "National curve"
        bk_area = block.get("bankfull_area_m2")
        area_edge = block.get("bankfull_area_edge_limited")
        if bk_area is not None:
            area_txt = ar(bk_area) + (" ‡" if area_edge else "")
            area_val = ui.TagList(area_txt, " ",
                                  _info(html_tip=_bieger_area_tip_html(region)))
        else:
            area_val = "n/a"
        rows = [("Bieger region", region),
                ("Bieger XS area", area_val),
                ("Bankfull width", wd(bf_w)),
                ("Bankfull XS area", ar(bf_area)),
                ("Floodprone width", wd(fp_w) + (" †" if edge else "")),
                ("Entrenchment ratio", rt(er)),
                ("Bank-height ratio", rt(bhr))]
        body = [ui.tags.tr(ui.tags.th(lbl), ui.tags.td(val)) for lbl, val in rows]
        out = [ui.tags.table(ui.tags.tbody(*body), class_="easi-tbl easi-xs-tbl")]
        if area_edge:
            out.append(ui.p("‡ the sampled DEM window is narrower than the regional "
                            "bankfull area; the bankfull depth may be under-estimated.",
                            class_="easi-xs-foot"))
        if edge:
            out.append(ui.p("† floodprone reached the sampled edge; width is "
                            "likely under-estimated.", class_="easi-xs-foot"))
        return ui.TagList(*out)

    @reactive.effect
    @reactive.event(input.xs_unit)
    def _xs_convert_units():
        new, old = input.xs_unit(), _xs_unit_prev()
        if new == old:
            return
        factor = (1.0 / FT_PER_M) if (old == "ft" and new == "m") else (
            FT_PER_M if (old == "m" and new == "ft") else 1.0)
        for fid in ("xs_bankfull", "xs_lowbank"):
            try:
                v = input[fid]()
            except Exception:
                v = None
            if v is not None:
                ui.update_numeric(fid, value=round(float(v) * factor, 2))
        _xs_unit_prev.set(new)

    # ---- downloads (reflect current overrides) ----
    @render.download(filename="easi_report.pdf")
    def dl_pdf():
        res = export_result()
        if res:
            yield report.build_pdf(res)

    @render.download(filename="easi_report.csv")
    def dl_csv():
        res = export_result()
        if res:
            yield report.build_csv(res)

    @render.download(filename="easi_report.geojson")
    def dl_geojson():
        res = export_result()
        if res:
            yield report.build_geojson(res).encode("utf-8")

    # ==================================================================== #
    # Batch workspace (full-width overlay; the single-site flow stays default)
    # ==================================================================== #
    # One page: paste/upload -> live parse -> reach -> Run -> results in place.
    # The scaffold render depends only on app_mode; everything that changes during
    # a run lives in nested output_ui slots so the 0.6s progress ticks never
    # re-render (and clobber) the textarea/inputs the user is touching.
    app_mode = reactive.value("single")
    batch_sites = reactive.value([])          # [{site_id, lat, lon, comid?}]
    batch_text = reactive.value("")           # server mirror of the paste textarea:
                                              # seeds the scaffold on re-entry so the
                                              # rebind is a NoResend no-op, not a wipe
    batch_msg = reactive.value("")
    batch_result = reactive.value(None)       # BatchResult object (with artifacts)
    batch_modal_site = reactive.value(None)   # {"site_id", "base"} for the open popup
    _batch_prog = {"done": 0, "total": 0, "stage": "", "site": ""}
    batch_tick = reactive.value(0)

    @reactive.effect
    @reactive.event(input.nav_batch)
    def _enter_batch():
        app_mode.set("batch")

    @reactive.effect
    @reactive.event(input.batch_exit)
    def _leave_batch():
        app_mode.set("single")

    def _parse_and_set(text: str):
        sites, errors = batch_ui.parse_sites_text(text)
        n_parsed = len(sites)
        # The parser's own over-limit warning fires above the 150-site engine
        # limit; the stricter UI-cap note below supersedes it.
        errors = [e for e in errors if "-site limit" not in e]
        sites = sites[:BATCH_UI_MAX_SITES]
        batch_sites.set(sites)
        parts = [f"{len(sites)} site(s) ready"]
        if n_parsed > BATCH_UI_MAX_SITES:
            parts.append(f"only the first {BATCH_UI_MAX_SITES} of {n_parsed} "
                         "sites will run")
        if errors:
            parts.append(f"{len(errors)} issue(s): " + "; ".join(errors[:3]))
        batch_msg.set(" · ".join(parts))

    @reactive.effect
    @reactive.event(input.batch_paste)
    def _auto_parse():
        # The textarea updates live (update_on="change" default), so parsing is
        # immediate: no Parse button. Uploads funnel through here too (_on_file
        # writes the file's text into the textarea). reactive.event isolates the
        # body, so clearing batch_result below can't make this effect depend on
        # it (a bare effect would re-fire on run completion and wipe the result).
        text = input.batch_paste() or ""
        batch_text.set(text)
        if batch_result() is not None:
            # Editing the sites implicitly starts a new batch: the old results no
            # longer describe the text above them.
            batch_result.set(None)
        if not text.strip():
            batch_sites.set([])
            batch_msg.set("")
            return
        _parse_and_set(text)

    @reactive.effect
    @reactive.event(input.batch_file)
    def _on_file():
        finfo = input.batch_file()
        if not finfo:
            return
        try:
            text = Path(finfo[0]["datapath"]).read_text(
                encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            batch_msg.set(f"could not read file: {exc}")
            return
        # Single source of truth: show the file's rows in the textarea (editable);
        # the round-trip fires _auto_parse.
        ui.update_text_area("batch_paste", value=text)

    @reactive.extended_task
    async def batch_task(sites: list, reach_ft: float, criteria):
        req = batch_contracts.BatchRequest(
            sites=[batch_contracts.SiteRequest(
                site_id=str(s.get("site_id") or ""), lat=s["lat"], lon=s["lon"],
                comid=s.get("comid")) for s in sites],
            config=batch_contracts.BatchConfig(reach_length_ft=reach_ft),
            criteria=criteria)

        def on_event(stage, site_id, info):
            _batch_prog["stage"], _batch_prog["site"] = stage, site_id
            if stage == "site_done":
                _batch_prog["done"] = _batch_prog.get("done", 0) + 1

        return await batch_api.run_batch(req, on_event=on_event)

    @reactive.effect
    @reactive.event(input.batch_run)
    def _start_batch():
        # ExtendedTask QUEUES a second invocation instead of rejecting it, so the
        # re-entry guard matters (a stray click would silently run the batch twice).
        if batch_task.status() == "running":
            ui.notification_show("A batch is already running.", type="warning",
                                 duration=3)
            return
        sites = batch_sites()
        if not sites:
            ui.notification_show("Add sites first.", type="warning", duration=3)
            return
        reach_ft = float(input.batch_reach() or DEFAULT_REACH_FT)
        _batch_prog.update(done=0, total=len(sites), stage="starting", site="")
        batch_result.set(None)
        # Qualification is engine/export detail only (summary.csv, batch-results.json);
        # the UI no longer surfaces criteria or retained/excluded decisions.
        batch_task(sites, reach_ft, "functional")

    @reactive.effect
    def _poll_batch():
        if batch_task.status() == "running":
            reactive.invalidate_later(0.6)
            with reactive.isolate():
                batch_tick.set(batch_tick() + 1)

    @reactive.effect
    def _batch_done():
        st = batch_task.status()
        if st == "success":
            with reactive.isolate():
                batch_result.set(batch_task.result())
        elif st == "error":
            # result() re-raises the stored exception when the task errored.
            with reactive.isolate():
                try:
                    batch_task.result()
                except Exception as exc:  # noqa: BLE001
                    batch_msg.set(f"Batch failed: {exc}")

    @reactive.effect
    @reactive.event(input.batch_new)
    def _batch_new():
        batch_result.set(None)
        batch_sites.set([])
        batch_text.set("")
        batch_msg.set("")
        # Round-trips through _auto_parse, which re-clears sites/msg (idempotent).
        ui.update_text_area("batch_paste", value="")

    def _sites_preview():
        sites = batch_sites()
        if not sites:
            return None
        body = [ui.tags.tr(ui.tags.td(s.get("site_id") or "(auto)"),
                           ui.tags.td(f'{s["lat"]:.5f}'),
                           ui.tags.td(f'{s["lon"]:.5f}'),
                           ui.tags.td(str(s.get("comid") or "")))
                for s in sites[:60]]
        return ui.div(ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Site"), ui.tags.th("Lat"),
                                     ui.tags.th("Lon"), ui.tags.th("COMID"))),
            ui.tags.tbody(*body), class_="easi-tbl easi-batch-preview"),
            class_="easi-batch-preview-wrap")

    def _results_table(obj):
        # Built from the LIVE BatchResult (not to_dict()): the per-site report data
        # lives in metadata["_artifacts"], which serialization strips.
        body = []
        for i, s in enumerate(obj.sites):
            arts = (s.metadata or {}).get("_artifacts") or {}
            if s.state == "succeeded":
                status = ui.tags.span("✓", class_="easi-batch-ok", title="succeeded")
            else:
                status = ui.tags.span(s.state, class_=f"easi-batch-badge b-{s.state}")
            report_cell = (ui.tags.a(
                "View report", class_="easi-batch-report-link",
                onclick=("Shiny.setInputValue('batch_open_report', "
                         f"{i}, {{priority: 'event'}})"))
                if arts.get("report") else "—")
            body.append(ui.tags.tr(
                ui.tags.td(s.site_id),
                ui.tags.td("—" if s.eci is None else f"{s.eci:.2f}"),
                ui.tags.td(f"{s.completeness.computed}/{s.completeness.total}"),
                ui.tags.td(status),
                ui.tags.td(report_cell)))
        return ui.div(ui.tags.table(
            ui.tags.thead(ui.tags.tr(
                ui.tags.th("Site"), ui.tags.th("ECI"), ui.tags.th("Metrics"),
                ui.tags.th("Status"), ui.tags.th("Report"))),
            ui.tags.tbody(*body), class_="easi-tbl easi-batch-results"),
            class_="easi-batch-results-wrap")

    @render.ui
    def batch_status():
        if batch_result() is not None:
            return None                   # the summary line takes over after a run
        msg = batch_msg()
        return ui.div(msg, class_="easi-batch-msg") if msg else None

    @render.ui
    def batch_table():
        obj = batch_result()
        return _results_table(obj) if obj is not None else _sites_preview()

    @render.ui
    def batch_actions():
        if batch_task.status() == "running":
            return ui.div(ui.input_action_button(
                "batch_run", "Running…", disabled=True, class_="btn btn-primary"),
                class_="easi-batch-actions")
        if batch_result() is not None:
            return ui.div(
                ui.download_button("dl_batch_zip", "Download batch ZIP",
                                   class_="btn btn-primary"),
                ui.input_action_button("batch_run", "Run again",
                                       class_="btn btn-sm btn-secondary"),
                ui.input_action_button("batch_new", "New batch",
                                       class_="btn btn-sm btn-secondary"),
                class_="easi-batch-actions")
        have_sites = bool(batch_sites())
        return ui.div(ui.input_action_button(
            "batch_run", "Run batch", disabled=not have_sites,
            title=None if have_sites else "Add sites above first",
            class_="btn btn-primary"),
            class_="easi-batch-actions")

    @render.ui
    def batch_progress():
        batch_tick()                      # depend on the poller so this re-renders
        if batch_task.status() == "running":
            done, total = _batch_prog.get("done", 0), _batch_prog.get("total", 0)
            pct = int(100 * done / total) if total else 0
            detail = f" · {_batch_prog.get('stage', '')} {_batch_prog.get('site', '')}"
            return ui.div(
                ui.div(ui.div(class_="easi-batch-bar-fill", style=f"width:{pct}%"),
                       class_="easi-batch-bar"),
                ui.p(f"{done}/{total} sites complete{detail}", class_="easi-instr"))
        obj = batch_result()
        if obj is None:
            return None
        s = batch_ui.result_summary(obj.to_dict())
        bits = [f"{s['total']} site(s)", f"{s['succeeded']} succeeded"]
        for key in ("partial", "failed", "cancelled"):
            if s.get(key):
                bits.append(f"{s[key]} {key}")
        return ui.div(" · ".join(bits), class_="easi-batch-summary")

    @reactive.effect
    @reactive.event(input.batch_open_report)
    def _open_site_report():
        obj = batch_result()
        idx = input.batch_open_report()
        if obj is None or idx is None:
            return
        try:
            site = obj.sites[int(idx)]
        except (ValueError, IndexError):
            return
        base = (site.metadata or {}).get("_artifacts")
        if not base or not base.get("report"):
            ui.notification_show("No report is available for this site.",
                                 type="warning", duration=3)
            return
        batch_modal_site.set({"site_id": site.site_id, "base": base})
        ui.modal_show(_batch_report_modal(site.site_id, base))

    def _modal_site_file(ext):
        with reactive.isolate():
            sid = (batch_modal_site() or {}).get("site_id") or "site"
        safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in sid)
        return f"easi_{safe}_report.{ext}"

    @render.download(filename=lambda: _modal_site_file("pdf"))
    def dl_site_pdf():
        base = (batch_modal_site() or {}).get("base")
        if base:
            yield report.build_pdf(base)

    @render.download(filename=lambda: _modal_site_file("csv"))
    def dl_site_csv():
        base = (batch_modal_site() or {}).get("base")
        if base:
            yield report.build_csv(base)

    @render.download(filename=lambda: _modal_site_file("geojson"))
    def dl_site_geojson():
        base = (batch_modal_site() or {}).get("base")
        if base:
            yield report.build_geojson(base).encode("utf-8")

    @render.ui
    def batch_workspace():
        if app_mode() != "batch":
            return None
        # Seeds only (no deps): re-entering batch mode re-creates the inputs, and a
        # rebind whose DOM value matches the last-sent value is a NoResend no-op.
        # Seeding from server state therefore preserves the session across the
        # single-site round trip instead of wiping it with defaults.
        with reactive.isolate():
            paste0 = batch_text()
            try:
                reach0 = int(input.batch_reach())
            except Exception:  # noqa: BLE001 — input not created yet this session
                reach0 = int(DEFAULT_REACH_FT)
        return ui.div(
            ui.div(ui.span("EASI Batch Processing", class_="easi-batch-brand"),
                   ui.input_action_link("batch_exit", "Back to single site"),
                   class_="easi-batch-head"),
            ui.div(ui.div(
                ui.p(f"Paste a table of sites (id, lat, lon) or upload a CSV. "
                     f"Sites appear below as you type. A header row is optional. "
                     f"Up to {BATCH_UI_MAX_SITES} sites per batch.",
                     class_="easi-instr"),
                ui.input_text_area("batch_paste", None, value=paste0, rows=6,
                                   width="100%",
                                   placeholder="MB, 43.72, -72.25\nCC, 40.10, -83.10"),
                ui.div(ui.input_file("batch_file", None, accept=[".csv"],
                                     multiple=False, button_label="Upload CSV"),
                       class_="easi-batch-import-row"),
                ui.output_ui("batch_status"),
                ui.output_ui("batch_table"),
                ui.div(ui.input_numeric("batch_reach", "Reach length (ft)",
                                        value=reach0, min=100,
                                        max=10000, step=100),
                       class_="easi-batch-settings"),
                ui.output_ui("batch_actions"),
                ui.output_ui("batch_progress"),
            ), class_="easi-batch-scroll"),
            class_="easi-batch")

    @render.download(filename="easi_batch.zip")
    def dl_batch_zip():
        obj = batch_result()
        if obj is not None:
            yield batch_exports.build_batch_zip(obj, include_pdf=False)


# Shiny for Python serves a static dir only when configured (no implicit www/).
app = App(app_ui, server, static_assets=Path(__file__).parent / "www")

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
                  geomorph, pipeline, report, scoring)
from easi.batch import api as batch_api  # noqa: E402
from easi.batch import contracts as batch_contracts  # noqa: E402
from easi.batch import exports as batch_exports  # noqa: E402
from easi.datasources import flowlines  # noqa: E402
from easi.datasources.geocode import geocode_address  # noqa: E402
from easi.pipeline import DEFAULT_REACH_FT  # noqa: E402

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
FLOWLINE_STYLE = {"color": "#1f6feb", "weight": 2, "opacity": 0.9}
# === TEMP: MMW comparison overlay (remove later) ===
MMW_STYLE = {"color": "#7b2cbf", "weight": 2, "dashArray": "5,4",
             "fillColor": "#b388eb", "fillOpacity": 0.18}  # distinct from yellow WATERSHED_STYLE
# === END TEMP ===
RATING_COLOR = {"Good": "#c8d9f2", "Fair": "#f5e7a6", "Poor": "#f5b5b5"}
_DISC_ORDER = ["Hydrology", "Hydraulics", "Geomorphology", "Physicochemistry", "Biology"]

USGS_TOPO_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"
USGS_IMAGERY_URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
USGS_HYDRO_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}"
USGS_ATTR = "USGS The National Map"
FLOW_ZOOM = 14          # NHD vectors appear at/above this zoom
SNAP_TOL_FT = 150.0     # click must land within this distance of a flowline

STEP_IDENTIFY, STEP_BASIN, STEP_CONFIGURE, STEP_REPORT = "identify", "basin", "configure", "report"
STEP_LABELS = [(STEP_IDENTIFY, "Identify"), (STEP_BASIN, "Basin"),
               (STEP_CONFIGURE, "Configure"), (STEP_REPORT, "Report")]

_METRICS = config.metrics_by_id()
ALL_MIDS = list(_METRICS)
SRC_INDEX = {i: mid for i, mid in enumerate(ALL_MIDS) if mid in config.SOURCE_OPTIONS}
OVERRIDEABLE = [mid for mid, info in config.METRIC_REGISTRY.items() if info.get("overrideable")]
OVERRIDEABLE_SET = set(OVERRIDEABLE)


# --------------------------------------------------------------------------- #
# UI helpers
# --------------------------------------------------------------------------- #
def _ds_label(mid: str) -> str:
    ds = config.METRIC_REGISTRY.get(mid, {}).get("datasource", "")
    primary = ds.split("|")[0].replace("proxy:", "").replace("streamcat:", "StreamCat ")
    return primary.replace("+", " + ").replace("_", " ").strip() or "—"


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


def _metric_tip_html(name, definition, source, calc, note, crit, default):
    """Build the report ⓘ tooltip card: definition, data source, calculation, then the
    scoring criteria. Source is where the input value comes from; Calculation is any extra
    computation on top of it. All dynamic values are HTML-escaped; markup is app-controlled.
    """
    e = html.escape
    parts = [f'<div class="easi-tip-title">{e(name or "")}</div>']
    if definition:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Definition</span>'
                     f'{e(definition)}</div>')
    if source:
        sub = f'<div class="easi-tip-sub">{e(note)}</div>' if note else ""
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Source</span>'
                     f'{e(source)}{sub}</div>')
    if calc:
        parts.append('<div class="easi-tip-sec"><span class="easi-tip-lbl">Calculation</span>'
                     f'{e(calc)}</div>')
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
             'A = a·DA<sup>b</sup> — area in m², drainage area in km² '
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
    ui.head_content(ui.tags.link(rel="stylesheet", href="styles.css?v=23"),
                    ui.tags.script(src="geocode-autocomplete.js", defer=""),
                    ui.tags.script(src="tooltip.js", defer=""),
                    ui.tags.script(src="report-edit.js", defer=""),
                    ui.tags.script(src="report-controls.js", defer=""),
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
                ui.input_action_link("nav_about", "About"),
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
        ui.output_ui("batch_workspace"),
        ui.output_ui("readout"),
        ui.output_ui("flow_loading"),
        ui.output_ui("cursor_style"),
        class_="easi-shell",
    ),
    title="EASI — Automated Stream Screening",
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
        bars = [_bar(fn["name"], fscores.get(fn["id"]),
                     scoring.function_score_band_color(fscores.get(fn["id"]) or 0),
                     vmax=config.FUNCTION_SCORE_MAX, value_fmt="{:.0f}")
                for fn in fns]
        fn_blocks.append(ui.div(ui.div(cat, class_="easi-fn-group"), *bars,
                                class_="easi-fn-block"))
    left = ui.div(
        ui.div("Function scores", class_="easi-plot-title"),
        _plot_legend([(scoring.function_score_band_color(15), "Functioning 11–15"),
                      (scoring.function_score_band_color(8), "At-Risk 6–10"),
                      (scoring.function_score_band_color(0), "Non-Functioning 0–5")]),
        *fn_blocks,
        class_="easi-plot-panel",
    )
    right = ui.div(
        ui.div("Condition indices", class_="easi-plot-title"),
        _plot_legend([(scoring.index_band_color(1.0), "Functioning 0.70–1.00"),
                      (scoring.index_band_color(0.5), "At-Risk 0.40–0.69"),
                      (scoring.index_band_color(0.0), "Non-Functioning 0.00–0.39")]),
        _bar("Ecosystem Condition Index", eci, scoring.index_band_color(eci or 0)),
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
    """Chip-styled native <select> that overrides an overrideable metric's rating.

    Looks like the colored rating chip; opening it shows Auto + Good/Fair/Poor with
    their thresholds. Plain HTML (no Shiny binding) — www/report-edit.js posts the
    choice via setInputValue, so it survives the table re-render.
    """
    eff = r.get("rating")                        # effective (override or generated)
    opts = []
    if eff not in ("Good", "Fair", "Poor"):      # no current rating -> non-pickable placeholder
        opts.append(ui.tags.option("—", value="", selected="selected", disabled="disabled"))
    for rt in ("Good", "Fair", "Poor"):
        opts.append(ui.tags.option(rt, value=rt, selected="selected") if rt == eff
                    else ui.tags.option(rt, value=rt))
    # criteria + computed value live in the adjacent ⓘ (see _metric_table), so the
    # chip stays narrow; just a short hint here.
    return ui.tags.select(*opts, {"class": f"easi-rate-sel rate-{eff or 'auto'}",
                                  "data-mid": mid, "title": "Click to override rating"})


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


def _metric_table(rows, notes=None, *, outcomes=None, eci=None):
    """The metric grid. Always emits every column/badge (Index, Physical/Chemical/Biological
    mapping, F/AR/NF badge, auto cue) so the report's display checkboxes can reveal them
    purely client-side (CSS class flips on #easi-report) with no Shiny re-render. When
    ``outcomes`` is given the integrated rollup is appended flush below the table."""
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
        status = r.get("status")
        is_ovr = status == "override"          # manual dropdown override — the only tinted row
        # (cross-section-derived rows, status "xs-derived", are NOT tinted: they aren't
        # manual overrides. Their provenance shows in the ⓘ tooltip's Source section.)
        # Rating cell: override dropdown + an ⓘ whose hover shows the definition, the data
        # source + how it's calculated, and the Good/Fair/Poor criteria. (The standalone
        # Source column was dropped — its content now lives in the tooltip's Source section.)
        crit = (_METRICS.get(mid, {}).get("criteria") or {})
        tip_html = _metric_tip_html(
            name=r.get("name"), definition=config.METRIC_DEFINITIONS.get(mid, ""),
            source=r.get("source") or "", note=r.get("note") or "",
            calc=(config.METRIC_CALCULATIONS.get(mid)
                  or "Dataset value used directly (binned to a rating)."),
            crit=crit, default=r.get("generatedRating") or "n/a")
        rating_cell = ui.tags.td(ui.div(_rate_select(mid, r), _info(html_tip=tip_html),
                                        class_="easi-rate-cell"))
        idx = r.get("index")
        idx_cell = ui.tags.td("—" if idx is None else f"{idx:.2f}", class_="easi-col-adv")
        codes = mapping.get(r.get("functionId"), {})
        map_cells = []
        for key in config.OUTCOMES:
            txt, title = _MAP_CODE.get(codes.get(key, "-"), _MAP_CODE["-"])
            map_cells.append(ui.tags.td(txt, {"title": f"{key.capitalize()}: {title}"},
                                        class_="easi-col-map"))
        note = notes.get(mid) or ""
        note_btn = ui.tags.button("✎", {
            "class": "easi-note-btn" + (" has-note" if note else ""), "data-mid": mid,
            "type": "button", "title": ("Edit note" if note else "Add note"),
            "aria-label": ("Edit note" if note else "Add note")})
        body.append(ui.tags.tr(
            ui.tags.td(r["functionName"]),
            ui.tags.td(r["name"]),
            ui.tags.td(r["valueText"]),
            rating_cell,
            _fnscore_cell(r, meta),
            *map_cells,
            idx_cell,
            ui.tags.td(note_btn, class_="easi-note-cell"),
            {"data-mid": mid},
            class_=("easi-row-ovr" if is_ovr else ""),
            style=("" if r["rating"] else "color:#aaa;"),
        ))
        body.append(ui.tags.tr(
            ui.tags.td(ui.tags.textarea(note, {
                "class": "easi-note-ta", "data-mid": mid, "rows": "2",
                "placeholder": "Add a note for this metric…"}), colspan=str(n_cols)),
            {"data-mid": mid}, class_="easi-note-row"))
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
    # mappings-off standalone is full-width with room for the full outcome names
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


def _xsection_section(rep):
    """Cross-section block: a summary table + the editable bankfull/low-bank height
    inputs (left) beside the plot (right). The table's computed values (bankfull &
    flood-prone width, ER, BHR) recompute live from the inputs via ``xs_summary``;
    heights are above the channel bottom, defaulting to the Bieger bankfull and the
    DEM low bank, in feet."""
    xs = (rep or {}).get("crossSection") or {}
    if not xs.get("png_b64"):
        return None
    block = xs.get("geom") or {}
    thalweg = block.get("thalweg")
    if thalweg is None:  # no editable geometry — render the static image only
        return ui.div(ui.tags.img(src=f"data:image/png;base64,{xs['png_b64']}"),
                      class_="easi-xsection")

    def ft(stage):
        return round((stage - thalweg) * FT_PER_M, 2) if stage is not None else None

    bf_def = ft(block.get("bankfull_stage"))
    lb_def = ft(block.get("floodplain_stage"))
    bk_area = block.get("bankfull_area_m2")
    area_txt = f"{bk_area:.1f} m² " if bk_area is not None else ""
    bf_tip = (f"Default {bf_def} ft — the depth at which the channel cross-sectional area "
              f"equals the Bieger et al. (2015) regional bankfull area "
              f"({area_txt}for the {block.get('division') or 'national'} division). "
              f"Edit to use a surveyed value.")
    panel = ui.div(
        ui.div("Cross-section geometry", class_="easi-xs-panel-title"),
        ui.output_ui("xs_summary"),
        ui.div(
            ui.input_numeric("xs_bankfull", ui.span("Bankfull depth ", _info(text=bf_tip)),
                             value=bf_def, min=0, step=0.1),
            ui.input_numeric("xs_lowbank", "Low bank height", value=lb_def, min=0, step=0.1),
            class_="easi-xs-fields",
        ),
        ui.input_radio_buttons("xs_unit", None, {"ft": "Feet", "m": "Meters"},
                               selected="ft", inline=True),
        class_="easi-xs-panel",
    )
    n_cands = len(xs.get("candidates") or [])
    switch = ui.div(
        ui.input_action_button("xs_prev", "◀", class_="easi-xs-arrow"),
        ui.output_ui("xs_selector"),
        ui.input_action_button("xs_next", "▶", class_="easi-xs-arrow"),
        class_="easi-xs-switch",
    ) if n_cands >= 2 else None
    head = ui.div(ui.span("Representative cross-section", class_="easi-xs-plot-title"),
                  switch, class_="easi-xs-plot-head")
    plot = (ui.div(output_widget("xsection_plot", height="100%"), class_="easi-xsection")
            if _HAS_PLOTLY else ui.output_ui("xsection"))
    right = ui.div(head, plot, class_="easi-xs-right")
    return ui.div(panel, right, class_="easi-xsection-wrap")


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


def _report_modal(base):
    """Static modal skeleton: override-independent chrome + dynamic output slots.

    The dynamic body lives inside a stable ``#easi-report`` wrapper so the display-toggle
    classes set by www/report-controls.js persist across the metric table's re-render on a
    rating override (the re-rendered table inherits them via descendant CSS)."""
    d, rep = base["delineation"], base.get("report")
    body = ui.div(
        _summary_header(d),
        _basin_block(d, rep),
        _xsection_section(rep),
        ui.div("Metrics", class_="easi-section-title"),
        ui.div("Adjust a rating inline in the Rating column; click ✎ on any row to add a "
               "note. Use the checkboxes above to show more scoring detail. Edits flow into "
               "the report and exports.", class_="easi-instr"),
        _metric_toolbar(),
        ui.output_ui("m_metrics"),          # metric table + integrated outcome rollup
        ui.div("Summary plots", class_="easi-section-title"),
        ui.output_ui("m_scores"),           # moved below the table (STAF layout)
        ui.p("Generated from national datasets — a desktop screening estimate with "
             "per-metric confidence, not a field-validated assessment. Adjust field "
             "metrics inline to incorporate local evidence.", class_="easi-disclaimer"),
        _dl_buttons(),
        id="easi-report", class_="show-slider",   # slider on by default (report-controls.js
    )                                             # reconciles with any saved preference)
    return ui.modal(
        body,
        # ✕ lives in the modal header (anchors to .modal-content) so it sits at the
        # popup's top-right corner and stays put when the body scrolls.
        title=ui.TagList(
            "EASI Screening Report",
            ui.input_action_button("close_modal_x", "✕", class_="easi-modal-x"),
        ),
        size="xl",
        easy_close=True,
        footer=None,
    )


def _stepper(active):
    done = True
    items = []
    for key, label in STEP_LABELS:
        cls = "easi-step"
        if key == active:
            cls += " active"; done = False
        elif done:
            cls += " done"
        items.append(ui.input_action_link(f"go_{key}", label, class_=cls))
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
    _overrides = reactive.value({})        # {metricId: "Good"/"Fair"/"Poor"} from the table
    _notes = reactive.value({})            # {metricId: note text} from the table
    _geom_owned = reactive.value(set())    # metricIds whose rating is currently derived from
    #                                        an edited cross-section (vs a manual dropdown pick)
    _geom_text = reactive.value({})        # {metricId: value text} for those edited rows
    modal_shown = reactive.value(False)
    view_bbox = reactive.value(None)       # rounded bbox at zoom >= FLOW_ZOOM | None
    last_view_change = reactive.value(0.0)
    fetched_bbox = reactive.value(None)
    step_clicks = reactive.value({k: 0 for k, _ in STEP_LABELS})  # stepper nav counters

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
            if elapsed < 0.5:                       # wait for panning to settle
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

        # ---- click -> snap or reject (only during the identify step) ----
        @reactive.effect
        @reactive.event(clicked)
        def _handle_click():
            if current_step() != STEP_IDENTIFY:
                return
            lat, lon = clicked()
            fc = flow_geojson()
            hit = flowlines.nearest_point_on_lines(fc, lat, lon) if fc else None
            if hit and hit[2] <= SNAP_TOL_FT:
                _apply_snap(hit)                 # covered by the viewport vectors
            else:
                click_snap_task(lat, lon)        # fetch flowlines around the click + snap

        def _apply_snap(hit):
            slat, slon, dist, comid = hit
            _add_layer("marker", Marker(location=(slat, slon), draggable=False,
                                        title="Selected point", name="Selected point"))
            snapped_point.set((slat, slon, dist, comid))
            ui.update_numeric("lat", value=round(slat, 5))
            ui.update_numeric("lon", value=round(slon, 5))

        @reactive.extended_task
        async def click_snap_task(lat: float, lon: float) -> dict:
            d = 0.012  # ~0.8 mi half-box around the click, so the snap uses the
            return {"hit": await anyio.to_thread.run_sync(  # line you actually clicked
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
                ui.notification_show("You didn't click on a stream line — zoom in and click "
                                     "a blue stream line.", type="warning", duration=5)

        # ---- typed lat/long -> recenter the map + snap (same path as a click) ----
        @reactive.extended_task
        async def coord_snap_task(lat: float, lon: float) -> dict:
            d = 0.012  # same ~0.8 mi half-box as a map click
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
            _MAP.center = (lat, lon)   # bring the typed point into view so it is visible
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
            ui.notification_show(f"Centered on {hit[0]:.4f}, {hit[1]:.4f}. Click a blue stream.",
                                 duration=4)
        elif not hit:
            ui.notification_show("Place not found — try a city, address, or stream name.",
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
        ui.update_action_button("delineate", disabled=(snapped_point() is None))

    # ---- staged analysis tasks ----
    @reactive.extended_task
    async def delineate_task(lat: float, lon: float, reach_ft: float,
                             comid: "int | None" = None) -> dict:
        return await pipeline.delineate_only(lat, lon, reach_ft, comid=comid)

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
        delin.set(res)
        current_step.set(STEP_BASIN)

    @reactive.effect
    @reactive.event(input.to_report)
    def _start_assess():
        d = delin()
        if not d:
            return
        modal_shown.set(False)
        n = len(selected_metric_ids())
        _assess_prog["done"], _assess_prog["total"], _assess_prog["waiting"] = 0, n, {}
        stage.set(f"Computing metrics… 0/{n}")
        ui.notification_show(f"Computing metrics… 0/{n} — please wait", id="stage",
                             type="message", duration=None)
        assess_task(d["ctx_inputs"], selected_metric_ids(), source_choices(), _assess_prog)

    @reactive.effect
    def _assess_progress_poll():
        # While metrics compute, poll the shared counter ~3x/sec and update the
        # left-pane busy label + toast with a live "X/N" count.
        if assess_task.status() != "running":
            return  # stops rescheduling once the task settles
        reactive.invalidate_later(0.3)
        done, total = _assess_prog["done"], _assess_prog["total"]
        waiting = _assess_prog.get("waiting") or {}
        detail = (" — waiting on " + ", ".join(sorted(waiting))) if waiting else ""
        label = f"Computing metrics… {done}/{total}{detail}"
        stage.set(label)
        ui.notification_show(label + " — please wait", id="stage",
                             type="message", duration=None)

    @reactive.effect
    def _assess_done():
        status = assess_task.status()
        if status in ("initial", "running"):
            return
        ui.notification_remove("stage"); stage.set("")
        if status == "error":
            ui.notification_show("Metric computation failed — please try again.",
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
        _overrides.set({}); _notes.set({})   # fresh report starts with no overrides/notes
        _geom_owned.set(set()); _geom_text.set({}); _xs_sel.set(None)
        current_step.set(STEP_REPORT)
        if not modal_shown():
            modal_shown.set(True)
            _xs_unit_prev.set("ft")
            ui.modal_show(_report_modal(merged))

    # ---- step navigation ----
    @reactive.effect
    @reactive.event(input.to_configure)
    def _go_configure():
        current_step.set(STEP_CONFIGURE)

    @reactive.effect
    @reactive.event(input.back_to_basin)
    def _go_basin():
        current_step.set(STEP_BASIN)

    @reactive.effect
    @reactive.event(input.back_to_configure)
    def _go_configure2():
        current_step.set(STEP_CONFIGURE)

    @reactive.effect
    def _stepper_nav():
        # Stepper links live in the (re-rendering) left pane, so compare click
        # counters and only navigate on a genuine increase (ignore reset-to-0).
        cur = {}
        for key, _ in STEP_LABELS:
            try:
                cur[key] = input[f"go_{key}"]() or 0
            except Exception:
                cur[key] = 0
        with reactive.isolate():
            prev = step_clicks()
            target = next((k for k, _ in STEP_LABELS if cur[k] > prev.get(k, 0)), None)
            step_clicks.set(cur)
            if target is None:
                return
            has_delin = delin() is not None
            has_report = base_result() is not None
            if target == STEP_IDENTIFY:
                current_step.set(STEP_IDENTIFY)
            elif target in (STEP_BASIN, STEP_CONFIGURE) and has_delin:
                current_step.set(target)
            elif target == STEP_REPORT and has_report:
                current_step.set(STEP_REPORT)
            else:
                ui.notification_show("Finish the earlier steps first.", type="message", duration=2)

    @reactive.effect
    @reactive.event(input.nav_new, input.clear_basin)
    def _reset():
        for k in ("ws", "reach", "marker"):
            _remove_layer(k)
        snapped_point.set(None); delin.set(None); base_result.set(None)
        modal_shown.set(False); stage.set("")
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
    @reactive.event(input.nav_about)
    def _about():
        ui.modal_show(ui.modal(
            ui.markdown(
                "**EASI** automates the EASI Screening-tier assessment (from STAF). "
                "Click a stream, delineate the watershed and a reach upstream, and EASI "
                "computes the 20 EASI metrics from national, public GIS/hydrology data, "
                "scores them with the STAF rollup, and produces a read-only report. "
                "It is a desktop screening estimate, not a field-validated assessment."),
            title="About EASI", easy_close=True))

    @reactive.effect
    @reactive.event(input.nav_help)
    def _help():
        ui.modal_show(ui.modal(
            ui.markdown(
                "**How to use**\n\n"
                "1. **Zoom in** (to ~street level) until blue NHD stream lines appear.\n"
                "2. **Click a stream** to drop a point (it snaps to the line; clicking off a "
                "stream is rejected).\n"
                "3. **Delineate basin** — the watershed + upstream reach are traced.\n"
                "4. **Configure** which functions/sources to compute, then **open the report**.\n\n"
                "Switch basemaps and toggle the NHD overlay with the layers control (top-right)."),
            title="Help", easy_close=True))

    # ---- selection + source choices (configure step) ----
    @reactive.calc
    def selected_metric_ids():
        # All metrics always run — the per-metric checkboxes were removed from the
        # Configure page, so this is no longer user-selectable.
        return list(ALL_MIDS)

    @reactive.calc
    def source_choices():
        out = {}
        for i, mid in SRC_INDEX.items():
            try:
                v = input[f"src_{i}"]()
            except Exception:
                v = None
            if v:
                out[mid] = v
        return out

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
        owned = set(_geom_owned())
        if own and block:
            derived = assessment.rate_metrics_from_stages(block, bankfull_stage, floodplain_stage)
            new_owned = set()
            for mid, info in derived.items():
                if info.get("rating"):
                    cur[mid] = info["rating"]
                    texts[mid] = info.get("valueText", "")
                    new_owned.add(mid)
            for mid in owned - new_owned:
                cur.pop(mid, None)
                texts.pop(mid, None)
            _overrides.set(cur)
            _geom_text.set(texts)
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
            for row in sc["metricRows"]:
                mid = row["metricId"]
                if mid in owned:
                    row["status"] = "xs-derived"
                    row["source"] = "edited cross-section"
                    row["valueText"] = texts.get(mid) or f"from edited cross-section: {row['rating']}"
                    row["note"] = "recomputed from your bankfull/floodplain heights"
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
        step = current_step()
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
            body = ui.TagList(ui.output_ui("basin_card"),
                              # === TEMP: MMW comparison overlay checkbox (remove later) ===
                              ui.div(ui.input_checkbox("show_mmw",
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
                                     style="margin:.4rem 0;"),
                              # === END TEMP ===
                              ui.div(ui.input_action_button("clear_basin", "Clear",
                                                            class_="btn-outline-secondary"),
                                     ui.input_action_button("to_configure", "Continue",
                                                            class_="btn-primary"),
                                     class_="easi-pane-actions"))
        elif step == STEP_CONFIGURE:
            body = ui.TagList(
                ui.div("Explore the metrics and data sources used for each function.",
                       class_="easi-instr"),
                _configure_rows(),
                ui.div(ui.input_action_button("back_to_basin", "Back", class_="btn-outline-secondary"),
                       ui.input_action_button("to_report", "Compute & report", class_="btn-primary"),
                       class_="easi-pane-actions"),
                ui.output_text("busy_text"),
            )
        else:  # report
            body = ui.TagList(
                ui.div("Analysis complete. Open the report, adjust overrides, or export.",
                       class_="easi-instr"),
                ui.div(ui.input_action_button("show_report", "Open report", class_="btn-primary"),
                       class_="easi-pane-actions"),
                ui.div(ui.input_action_button("back_to_configure", "Back to configure",
                                              class_="btn-outline-secondary"),
                       class_="easi-pane-actions"),
            )
        active = current_step()
        head_label = dict(STEP_LABELS).get(active, "EASI")
        return ui.TagList(
            ui.div(f"EASI — {head_label}", class_="easi-pane-head"),
            ui.div(_stepper(active), body, class_="easi-pane-body"),
        )

    def _configure_rows():
        groups: dict[str, list] = {d: [] for d in _DISC_ORDER}
        for i, mid in enumerate(ALL_MIDS):
            meta = _METRICS[mid]
            groups.setdefault(meta["discipline"], []).append((i, mid, meta))
        out = []
        for disc in _DISC_ORDER:
            rows = groups.get(disc) or []
            if not rows:
                continue
            # within a discipline, group metrics under their STAF function (encounter order)
            fn_order: list[str] = []
            by_fn: dict[str, list] = {}
            for i, mid, meta in rows:
                fn = meta.get("functionName") or "—"
                if fn not in by_fn:
                    by_fn[fn] = []
                    fn_order.append(fn)
                by_fn[fn].append((i, mid, meta))
            blocks = []
            for fn in fn_order:
                members = by_fn[fn]
                stmt = (members[0][2].get("functionStatement") or "").strip()
                header = ui.div(ui.span(fn, class_="easi-fn-name"), _info(stmt),
                                class_="easi-fn-group")
                blocks.append(ui.div(
                    header,
                    *[_cfg_row(i, mid, meta) for i, mid, meta in members],
                    class_="easi-fn-block"))
            # collapsible discipline — no `open` attribute => collapsed by default
            out.append(ui.tags.details(
                ui.tags.summary(disc, class_="easi-cfg-group"),
                ui.div(*blocks, class_="easi-disc-body"),
                class_="easi-disc"))
        return ui.div(*out)

    def _cfg_row(i, mid, meta):
        # All metrics always run, so there's no per-metric checkbox — the inline row is
        # just the metric name + ⓘ hover (+ a source dropdown where alternatives exist).
        crit = meta.get("criteria") or {}
        tip = "\n".join(filter(None, [
            f"Source: {_ds_label(mid)}",
            (f"Alternative (planned): {config.PLANNED_ALT_SOURCE[mid]}"
             if mid in config.PLANNED_ALT_SOURCE else ""),
            (f"Good: {crit['Good']}" if crit.get("Good") else ""),
            (f"Fair: {crit['Fair']}" if crit.get("Fair") else ""),
            (f"Poor: {crit['Poor']}" if crit.get("Poor") else ""),
        ]))
        # ⓘ rides right after the metric name so it hugs the text.
        name = ui.span(meta["name"], _info(tip)) if tip else ui.span(meta["name"])
        children = [ui.span(name, class_="easi-metric-name")]
        if mid in config.SOURCE_OPTIONS:            # interactive source choice stays visible
            opts = {v: lbl for v, lbl in config.SOURCE_OPTIONS[mid]}
            children.append(ui.input_select(f"src_{i}", None, choices=opts,
                                            selected=next(iter(opts))))
        return ui.div(*children, class_="easi-cfg-row")

    @render.ui
    def snap_status():
        pt = snapped_point()
        if not pt:
            return ui.p("No point yet — enter coordinates, search an address, or zoom in "
                        "and click a blue stream line.", class_="easi-snap-note")
        return ui.p(f"✓ Snapped to stream ({pt[2]:.0f} ft away). Click “Delineate basin”.",
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
            row("Watershed area", f'{d.get("watershed_area_sqkm")} km²'),
            row("Reach length", f'{d.get("reach_length_ft")} ft'),
            row("COMID", d.get("comid")),
            class_="easi-basin-card",
        )

    @render.text
    def busy_text():
        s = stage()
        running = (delineate_task.status() == "running") or (assess_task.status() == "running")
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

    # ---- modal output slots (re-render in place on override change) ----
    @render.ui
    def m_scores():
        sc = scored()
        if not sc:
            return None
        return _summary_plots(sc)

    @render.ui
    def m_metrics():
        sc = scored()
        if not sc:
            return None
        with reactive.isolate():          # read notes without re-rendering on every keystroke
            notes = dict(_notes())
        return _metric_table(sc["metricRows"], notes,
                             outcomes=sc["outcomes"], eci=sc["ecosystemConditionIndex"])

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

        @reactive.effect
        def _sync_xsection_plot():
            """Update the live cross-section figure IN PLACE when the selected candidate,
            edited heights, or unit change. Mutating the existing FigureWidget (rather than
            returning a new one from the render) is what removes the flicker: no DOM
            remount, and the trace count stays fixed (see ``xsplotly.figure``), so this is
            a single batched restyle/relayout. ``xsection_plot.widget`` is reactive and
            ``req()``-waits until the widget has first rendered, so ordering is safe."""
            w = xsection_plot.widget      # reactive: req()-waits until the widget exists
            block = _xs_block()
            if w is None or not block:
                return
            g = current_geometry()
            unit = g["unit"] if g else "ft"
            bankfull_stage = g["bankfull_stage"] if g else block.get("bankfull_stage")
            floodplain_stage = g["floodplain_stage"] if g else block.get("floodplain_stage")
            from easi import xsplotly
            src = xsplotly.figure(
                block["stations"], block["elevs"], thalweg=block["thalweg"],
                bankfull_stage=bankfull_stage, floodplain_stage=floodplain_stage, unit=unit,
                source=block.get("dem_source"))
            with w.batch_update():        # one atomic client update -> no flash
                for wt, st in zip(w.data, src.data):
                    wt.x, wt.y = st.x, st.y
                    wt.fillcolor = st.fillcolor        # blue water vs. transparent (no bankfull)
                    wt.hovertemplate = st.hovertemplate  # carries the unit in the bed-line hover
                w.layout.shapes = tuple(s.to_plotly_json() for s in src.layout.shapes)
                w.layout.annotations = tuple(a.to_plotly_json() for a in src.layout.annotations)
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

    @reactive.effect
    @reactive.event(input.show_report)
    def _reopen():
        base = base_result()
        if base is None:
            ui.notification_show("Run an analysis first.", type="message", duration=3)
            return
        _xs_unit_prev.set("ft")  # modal recreated with feet-default inputs
        _xs_sel.set(None)        # back to the default (middle) cross-section
        _geom_owned.set(set()); _geom_text.set({})
        ui.modal_show(_report_modal(base))

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
    app_mode = reactive.value("single")
    batch_step = reactive.value("import")     # import | configure | run | results
    batch_sites = reactive.value([])          # [{site_id, lat, lon, comid?}]
    batch_msg = reactive.value("")
    batch_result = reactive.value(None)       # BatchResult object (with artifacts)
    _batch_prog = {"done": 0, "total": 0, "stage": "", "site": ""}
    batch_tick = reactive.value(0)

    _BATCH_STEPS = [("import", "Import"), ("configure", "Configure"),
                    ("run", "Run"), ("results", "Results")]

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
        sites = sites[:batch_api.MAX_SITES]
        batch_sites.set(sites)
        parts = [f"{len(sites)} site(s) ready"]
        if errors:
            parts.append(f"{len(errors)} issue(s): " + "; ".join(errors[:3]))
        batch_msg.set(" — ".join(parts))

    @reactive.effect
    @reactive.event(input.batch_parse)
    def _on_parse():
        _parse_and_set(input.batch_paste() or "")

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
        _parse_and_set(text)

    @reactive.effect
    @reactive.event(input.batch_to_configure)
    def _to_configure():
        if not batch_sites():
            ui.notification_show("Add at least one site first.", type="warning",
                                 duration=3)
            return
        batch_step.set("configure")

    @reactive.effect
    @reactive.event(input.batch_back_import)
    def _back_import():
        batch_step.set("import")

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
        sites = batch_sites()
        if not sites:
            ui.notification_show("Add sites first.", type="warning", duration=3)
            return
        reach_ft = float(input.batch_reach() or DEFAULT_REACH_FT)
        _batch_prog.update(done=0, total=len(sites), stage="starting", site="")
        batch_result.set(None)
        batch_step.set("run")
        batch_task(sites, reach_ft, input.batch_criteria() or "functional")

    @reactive.effect
    def _poll_batch():
        if batch_task.status() == "running":
            reactive.invalidate_later(0.6)
            with reactive.isolate():
                batch_tick.set(batch_tick() + 1)

    @reactive.effect
    def _batch_done():
        if batch_task.status() != "success":
            return
        with reactive.isolate():
            batch_result.set(batch_task.result())
            batch_step.set("results")

    @reactive.effect
    @reactive.event(input.batch_new)
    def _batch_new():
        batch_result.set(None)
        batch_sites.set([])
        batch_msg.set("")
        batch_step.set("import")

    def _batch_stepper(active):
        return ui.div(*[ui.span(label, class_="easi-batch-step"
                                + (" active" if key == active else ""))
                        for key, label in _BATCH_STEPS],
                      class_="easi-batch-steps")

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

    def _import_panel():
        return ui.div(
            ui.p("Paste a table of sites (id, lat, lon) or upload a CSV/TSV file. "
                 "A header row is optional; up to 150 sites.", class_="easi-instr"),
            ui.input_text_area("batch_paste", None, rows=6, width="100%",
                               placeholder="MB, 43.72, -72.25\nCC, 40.10, -83.10"),
            ui.div(
                ui.input_action_button("batch_parse", "Parse pasted sites",
                                       class_="btn btn-sm btn-secondary"),
                ui.input_file("batch_file", None,
                              accept=[".csv", ".tsv", ".txt"], multiple=False,
                              button_label="Upload CSV/TSV"),
                class_="easi-batch-import-row"),
            ui.div(batch_msg(), class_="easi-batch-msg") if batch_msg() else None,
            _sites_preview(),
            ui.div(ui.input_action_button("batch_to_configure",
                                          "Continue to Configure",
                                          class_="btn btn-primary"),
                   class_="easi-batch-actions"))

    def _configure_panel():
        return ui.div(
            ui.h5("Batch defaults"),
            ui.input_numeric("batch_reach", "Reach length (ft)",
                             value=int(DEFAULT_REACH_FT), min=100, max=10000,
                             step=100),
            ui.input_select(
                "batch_criteria", "Reference-condition criteria",
                {"functional": "Functional (raw ECI > 0.69)",
                 "reference_condition": "Reference condition (ECI, all sub-indices "
                 "> 0.69 and all function scores > 10)"},
                selected="functional"),
            ui.p(f"{len(batch_sites())} site(s) will run all 20 metrics.",
                 class_="easi-instr"),
            ui.div(
                ui.input_action_button("batch_back_import", "Back",
                                       class_="btn btn-sm btn-secondary"),
                ui.input_action_button("batch_run", "Run batch",
                                       class_="btn btn-primary"),
                class_="easi-batch-actions"))

    def _run_panel():
        batch_tick()                      # depend on the poller so this re-renders
        done, total = _batch_prog.get("done", 0), _batch_prog.get("total", 0)
        running = batch_task.status() == "running"
        pct = int(100 * done / total) if total else 0
        detail = (f" — {_batch_prog.get('stage', '')} {_batch_prog.get('site', '')}"
                  if running else "")
        return ui.div(
            ui.h5("Running batch…" if running else "Batch finished"),
            ui.div(ui.div(class_="easi-batch-bar-fill", style=f"width:{pct}%"),
                   class_="easi-batch-bar"),
            ui.p(f"{done}/{total} sites complete{detail}", class_="easi-instr"))

    def _stat(label, value):
        return ui.div(ui.div(str(value), class_="easi-batch-stat-v"),
                      ui.div(label, class_="easi-batch-stat-l"),
                      class_="easi-batch-stat")

    def _results_table(res: dict):
        body = []
        for s in res.get("sites", []):
            q = s.get("qualification", {})
            eci = s.get("eci")
            comp = s.get("completeness", {})
            body.append(ui.tags.tr(
                ui.tags.td(s.get("site_id")),
                ui.tags.td(s.get("state")),
                ui.tags.td("" if eci is None else f"{eci:.2f}"),
                ui.tags.td(q.get("auto") or ""),
                ui.tags.td(q.get("final") or ""),
                ui.tags.td(f'{comp.get("computed", 0)}/{comp.get("total", 0)}')))
        return ui.div(ui.tags.table(
            ui.tags.thead(ui.tags.tr(
                ui.tags.th("Site"), ui.tags.th("State"), ui.tags.th("ECI"),
                ui.tags.th("Auto"), ui.tags.th("Final"), ui.tags.th("Computed"))),
            ui.tags.tbody(*body), class_="easi-tbl easi-batch-results"),
            class_="easi-batch-results-wrap")

    def _results_panel():
        obj = batch_result()
        if obj is None:
            return ui.p("No results yet.", class_="easi-instr")
        res = obj.to_dict()
        s = batch_ui.result_summary(res)
        return ui.div(
            ui.div(_stat("Sites", s["total"]), _stat("Succeeded", s["succeeded"]),
                   _stat("Partial", s["partial"]), _stat("Failed", s["failed"]),
                   _stat("Qualified", s["qualified"]), _stat("Retained", s["retained"]),
                   class_="easi-batch-stats"),
            ui.div(ui.download_button("dl_batch_zip", "Download batch ZIP",
                                      class_="btn btn-primary"),
                   ui.input_action_button("batch_new", "New batch",
                                          class_="btn btn-sm btn-secondary"),
                   class_="easi-batch-actions"),
            _results_table(res))

    @render.ui
    def batch_workspace():
        if app_mode() != "batch":
            return None
        step = batch_step()
        panel = {"import": _import_panel, "configure": _configure_panel,
                 "run": _run_panel, "results": _results_panel}[step]()
        return ui.div(
            ui.div(ui.span("EASI Batch Processing", class_="easi-batch-brand"),
                   ui.input_action_link("batch_exit", "Back to single site"),
                   _batch_stepper(step), class_="easi-batch-head"),
            ui.div(panel, class_="easi-batch-scroll"),
            class_="easi-batch")

    @render.download(filename="easi_batch.zip")
    def dl_batch_zip():
        obj = batch_result()
        if obj is not None:
            yield batch_exports.build_batch_zip(obj, include_pdf=False)


# Shiny for Python serves a static dir only when configured (no implicit www/).
app = App(app_ui, server, static_assets=Path(__file__).parent / "www")

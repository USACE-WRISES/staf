"""SFARI report exports — CSV, GeoJSON, and PDF.

All exports are matplotlib-free (PDF uses reportlab Platypus with colored table
cells) so they are safe on Posit Connect and never trigger matplotlib's font-cache
build. Each function takes the assessment reactive state plus the scored rollup
(``sc`` = ``scoring.score_assessment(...)``).
"""
from __future__ import annotations

import csv
import io
import json

from . import config, delineation, reportmap, scoring

_CAT = config.CATEGORY_ORDER

# Render-time normalization of the few non-ASCII glyphs that appear in stored
# evidence text (km², Δ, τ, →, en/em dashes, degree, micro) to print-safe ASCII.
# Applied only when building output; stored evidence is never mutated.
_PRINT_SAFE = str.maketrans({
    "–": "-",      # – en dash
    "—": "-",      # — em dash
    "→": "->",     # → rightwards arrow
    "Δ": "delta",  # Δ greek capital delta
    "δ": "delta",  # δ greek small delta
    "τ": "tau",    # τ greek small tau
    "²": "2",      # ² superscript two (km² -> km2)
    "³": "3",      # ³ superscript three
    "°": "deg",    # ° degree sign
    "µ": "u",      # µ micro sign
    "μ": "u",      # μ greek small mu
})


def to_print_safe(text: str) -> str:
    """Map common non-ASCII glyphs in evidence text to print-safe ASCII equivalents.

    Handles ``km²``->``km2``, ``Δ``->``delta``, ``τ``->``tau``, ``→``->``->``, en/em
    dashes to ``-``, and the degree/micro signs. Callers apply this at render time
    only; it does not mutate stored evidence. Tolerates ``None`` (returns ``""``).
    """
    if not text:
        return ""
    return str(text).translate(_PRINT_SAFE)


def _ordered_metrics():
    by_cat = config.functions_by_category()
    mbf = config.metrics_by_function()
    for cat in _CAT:
        for f in by_cat.get(cat, []):
            for m in mbf.get(f["id"], []):
                yield cat, f, m


def _header_pairs(delin, sc):
    dl = (delin or {}).get("delineation", {})
    si = sc.get("subIndices", {})
    # the HR reach watershed area row only when there is one (the no-watershed
    # continuation has none; the PDF header already did this)
    area = ([("HR reach watershed area (km2)", dl.get("watershed_area_sqkm"))]
            if dl.get("watershed_area_sqkm") is not None else [])
    return [
        ("Stream", dl.get("gnis_name") or "(unnamed reach)"),
        ("Latitude", dl.get("snapped_lat")), ("Longitude", dl.get("snapped_lon")),
        ("COMID", dl.get("comid")), ("HUC8", dl.get("huc8")),
        ("Drainage area (km2)", dl.get("drainage_area_sqkm")),
        *area,
        ("Reach length (ft)", dl.get("reach_length_ft")),
        ("Watershed basis", watershed_basis_label(delin)),
        ("StreamCat reach", streamcat_reach_label(delin)),
        ("Ecosystem Condition Index", sc.get("ecosystemConditionIndex")),
        ("Physical sub-index", si.get("physical")),
        ("Chemical sub-index", si.get("chemical")),
        ("Biological sub-index", si.get("biological")),
    ]


def build_csv(delin, metric_scores, function_scores, evidence, sc) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["SFARI Screening Assessment"])
    for k, v in _header_pairs(delin, sc):
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["Category", "Function", "Metric", "Scale", "Likert", "Pulled evidence", "Source",
                "Origin", "Describes", "Note"])
    for cat, f, m in _ordered_metrics():
        mid = m["metricId"]
        rc = metric_scores.get(mid) or {}
        ev = evidence.get(mid) or {}
        w.writerow([cat, f["name"], m["name"], m.get("scale", ""), rc.get("likert") or "",
                    ev.get("value_text") or "", ev.get("source") or "",
                    ev.get("origin") or "", ev.get("anchor_label") or "",
                    rc.get("note") or ""])
    w.writerow([])
    w.writerow(["Category", "Function", "Function score (0-15)", "Condition", "Justification"])
    fbc = config.functions_by_category()
    for cat in _CAT:
        for f in fbc.get(cat, []):
            rec = function_scores.get(f["id"]) or {}
            score = rec.get("score")
            band = scoring.function_score_band_label(score) if score is not None else ""
            w.writerow([cat, f["name"], "" if score is None else score, band,
                        rec.get("note") or ""])
    w.writerow([])
    w.writerow(["Outcome", "Sub-index"])
    for k in ("physical", "chemical", "biological"):
        w.writerow([k.title(), sc.get("subIndices", {}).get(k)])
    w.writerow(["Ecosystem Condition Index", sc.get("ecosystemConditionIndex")])
    return out.getvalue()


def build_geojson(delin, function_scores, sc) -> str:
    dl = (delin or {}).get("delineation", {})
    props = {"stream": dl.get("gnis_name"), "comid": dl.get("comid"), "huc8": dl.get("huc8"),
             "drainage_area_sqkm": dl.get("drainage_area_sqkm"),
             "ecosystem_condition_index": sc.get("ecosystemConditionIndex")}
    for k, v in sc.get("subIndices", {}).items():
        props[f"subindex_{k}"] = v
    feats = []

    def add(fc, kind):
        if fc and fc.get("features"):
            for ft in fc["features"]:
                feats.append({"type": "Feature", "geometry": ft.get("geometry"),
                              "properties": {"type": kind, **props}})
    add((delin or {}).get("watershed_geojson"), "watershed")
    add((delin or {}).get("reach_geojson"), "reach")
    if dl.get("snapped_lat") is not None and dl.get("snapped_lon") is not None:
        pt_props = {"type": "analysis_point", **props}
        for f in config.functions():
            pt_props[f["id"]] = (function_scores.get(f["id"]) or {}).get("score")
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [dl.get("snapped_lon"), dl.get("snapped_lat")]},
                      "properties": pt_props})
    return json.dumps({"type": "FeatureCollection", "features": feats}, indent=2)


def build_pdf(delin, metric_scores, function_scores, evidence, sc) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    import base64

    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch, title="SFARI Screening Report")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7, leading=8.4)
    story = []
    dl = (delin or {}).get("delineation", {})
    grid = colors.HexColor("#d5deea")
    head_bg = colors.HexColor("#eef2f8")
    band_col = {"NF": "#f5b5b5", "AR": "#f5e7a6", "F": "#c8d9f2"}

    def _img(uri, max_w, max_h):
        if not uri or "," not in uri:
            return None
        try:
            raw = base64.b64decode(uri.split(",", 1)[1])
            iw, ih = ImageReader(io.BytesIO(raw)).getSize()
            s = min(max_w / iw, max_h / ih, 1.0)
            return Image(io.BytesIO(raw), width=iw * s, height=ih * s)
        except Exception:  # noqa: BLE001
            return None

    story.append(Paragraph("SFARI Screening Report", styles["Title"]))
    story.append(Paragraph(dl.get("gnis_name") or "(unnamed reach)", styles["Heading2"]))
    hdr = [["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"],
           ["Ecosystem Condition Index", f"{sc.get('ecosystemConditionIndex')}"]]
    if dl.get("watershed_area_sqkm") is not None:
        hdr.insert(3, ["HR reach watershed area", f"{dl.get('watershed_area_sqkm')} km2"])
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                           ("BACKGROUND", (0, 0), (0, -1), head_bg)]))
    story.append(t)
    story.append(Spacer(1, 10))
    # The watershed over a USGS topo basemap, the same map the report modal shows.
    # None when there is no geometry, and the basemap alone drops out when the
    # service does not answer, so neither case blocks the PDF.
    _map = reportmap.pdf_flowable(
        delineation.display_simplify(delin.get("watershed_geojson"), max_vertices=700),
        delin.get("reach_geojson"), 5.0 * inch, 5.0 * inch * 180 / 290)
    if _map is not None:
        story += [Paragraph("Watershed", styles["Heading3"]), _map, Spacer(1, 10)]
    story.append(Paragraph("Function scores (0-15)", styles["Heading3"]))

    data = [["Category", "Function", "Score", "Condition"]]
    bg = []
    fbc = config.functions_by_category()
    r = 1
    for cat in _CAT:
        for f in fbc.get(cat, []):
            rec = function_scores.get(f["id"]) or {}
            score = rec.get("score")
            if score is not None:
                band = scoring.function_score_band_label(score)
                col = band_col.get(band)
            else:
                band, col = "", None
            data.append([cat, f["name"], "" if score is None else str(score), band])
            if col:
                bg.append(("BACKGROUND", (3, r), (3, r), colors.HexColor(col)))
            r += 1
    ft = Table(data, colWidths=[1.2 * inch, 3.3 * inch, 0.7 * inch, 1.0 * inch])
    ft.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg)] + bg))
    story += [ft, Spacer(1, 10), Paragraph("Outcome sub-indices", styles["Heading3"])]

    si = sc.get("subIndices", {})
    sit = Table([["Physical", "Chemical", "Biological", "ECI"],
                 [si.get("physical"), si.get("chemical"), si.get("biological"),
                  sc.get("ecosystemConditionIndex")]], colWidths=[1.6 * inch] * 4)
    sit.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                             ("BACKGROUND", (0, 0), (-1, 0), head_bg)]))
    story += [sit, Spacer(1, 10), Paragraph("Metric evidence & Likert scores", styles["Heading3"])]

    mdata = [["Function", "Metric", "Likert", "Pulled evidence"]]
    for cat, f, m in _ordered_metrics():
        mid = m["metricId"]
        rc = metric_scores.get(mid) or {}
        ev = evidence.get(mid) or {}
        lk = config.LIKERT_SHORT.get(rc.get("likert"), "") if rc.get("likert") else ""
        mdata.append([Paragraph(f["name"], small), Paragraph(m["name"], small), lk,
                      Paragraph(ev.get("value_text") or "", small)])
    mt = Table(mdata, colWidths=[1.5 * inch, 2.0 * inch, 0.55 * inch, 2.65 * inch], repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 8),
              Paragraph("Desktop evidence supports scoring; the assessor assigns the Likert and "
                        "0-15 function scores. Likert thresholds are national defaults. Calibrate "
                        "regionally.", styles["Italic"])]

    # site-photo gallery
    gallery = []
    for cat, f, m in _ordered_metrics():
        ph = (metric_scores.get(m["metricId"]) or {}).get("photos") or []
        imgs = [im for im in (_img(p.get("uri"), 1.3 * inch, 1.3 * inch) for p in ph) if im]
        if imgs:
            gallery.append(Paragraph(f"<b>{f['name']}</b>: {m['name']}", small))
            gt = Table([imgs], colWidths=[1.42 * inch] * len(imgs), hAlign="LEFT")
            gt.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            gallery.append(gt)
            gallery.append(Spacer(1, 6))
    if gallery:
        story += [Spacer(1, 10), Paragraph("Site photos", styles["Heading3"])] + gallery

    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Desktop-metrics summary (shared by the standalone PDF and the field-forms
# appendix). The 26 desktopSupportable metrics, once each, with method + full
# value/status + a linked source.
# --------------------------------------------------------------------------- #
def _ff_styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    base = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=base["BodyText"], fontSize=7, leading=8.4)
    return {
        "base": base,
        "small": small,
        "small_dim": ParagraphStyle("small_dim", parent=small, textColor=colors.HexColor("#66708a")),
        "small_it": ParagraphStyle("small_it", parent=small, fontName="Helvetica-Oblique",
                                   textColor=colors.HexColor("#66708a")),
        "h1": ParagraphStyle("ff_h1", parent=base["Heading1"], fontSize=15, spaceAfter=4),
    }


def _value_or_status(ev, ds):
    """(display_text, is_status) for the appendix Value column: the full pulled value
    when available, otherwise an explicit per-metric status derived from the catalog
    source client + the evidence status."""
    if ev.get("status") == "ok" and (ev.get("value_text") or ev.get("field_value_text")):
        return to_print_safe(ev.get("value_text") or ev.get("field_value_text")), False
    if ev.get("status") == "pending":
        return "Pending: STAF site engine running", True
    client = (ds or {}).get("client")
    if client == "manual":
        return "Local review required", True
    if client == "xscalc":
        return "Run cross-section tool", True
    if ev.get("status") == "unavailable":
        return "Unavailable", True
    return "Review in the field", True


def _metrics_summary_table(delin, evidence):
    """The 26-row desktop-metrics table (Discipline, Function, Metric, Desktop method,
    Value/status, Data source). Header repeats on continuation pages (repeatRows=1)."""
    from urllib.parse import urlparse
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    st = _ff_styles()
    small, small_dim, small_it = st["small"], st["small_dim"], st["small_it"]
    head_bg = colors.HexColor("#eef2f8")
    evidence = evidence or {}

    data = [["Discipline", "Function", "Metric", "Desktop method", "Value / status", "Data source"]]
    for cat, f, m in _ordered_metrics():
        if not m.get("desktopSupportable"):
            continue
        ds = m.get("desktopSource") or {}
        ev = evidence.get(m["metricId"]) or {}
        text, is_status = _value_or_status(ev, ds)
        val = Paragraph(escape(text), small_it if is_status else small)
        url = ev.get("source_url") or ds.get("url") or ""
        name = ev.get("source") or (urlparse(url).netloc if url else "")
        extra = ""
        if ev.get("anchor_label"):
            extra += f"<br/>Describes: {escape(to_print_safe(ev['anchor_label']))}"
        if ev.get("fallback_reason"):
            extra += f"<br/>Fallback: {escape(to_print_safe(ev['fallback_reason']))}"
        if url:
            href = escape(url, {'"': "&quot;"})
            src = Paragraph(f'<link href="{href}" color="#1f4e8c">{escape(name or url)}</link>'
                            + extra, small)
        else:
            src = Paragraph(escape(name) + extra, small)
        data.append([Paragraph(escape(cat), small_dim), Paragraph(escape(f["name"]), small_dim),
                     Paragraph(escape(m["name"]), small),
                     Paragraph(escape(to_print_safe(ds.get("label") or "")), small), val, src])
    mt = Table(data, colWidths=[0.8 * inch, 1.15 * inch, 1.25 * inch, 1.5 * inch,
                                1.35 * inch, 1.25 * inch], repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return mt


def _site_header_table(delin):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle
    dl = (delin or {}).get("delineation", {})
    hdr = [["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"],
           ["Watershed basis", watershed_basis_label(delin)],
           ["StreamCat reach", streamcat_reach_label(delin)]]
    if dl.get("watershed_area_sqkm") is not None:
        hdr.insert(3, ["HR reach watershed area", f"{dl.get('watershed_area_sqkm')} km2"])
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5deea")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8"))]))
    return t


def build_desktop_metrics_pdf(delin, evidence) -> bytes:
    """The desktop metrics PDF: the site, the 26 desktop-supportable metrics with
    the pulled value or an explicit status, the source of each, and the status
    legend. The second download of the Field Forms dialog (2026-09-07); the
    field packet itself carries only the compact values."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    st = _ff_styles()
    dl = (delin or {}).get("delineation", {})
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            title="SFARI Desktop Metrics")
    story = [Paragraph("SFARI Desktop Metrics", st["base"]["Title"]),
             Paragraph(to_print_safe(dl.get("gnis_name") or "(unnamed reach)"), st["base"]["Heading2"]),
             _site_header_table(delin), Spacer(1, 10),
             _metrics_summary_table(delin, evidence), Spacer(1, 8),
             Paragraph("Available: the value was pulled and is printed beside its function on "
                       "the field forms. Pending: the STAF site engine is still running. "
                       "Unavailable: neither engine nor service answered. Local review "
                       "required and Run cross-section tool: the assessor supplies it. "
                       "Watershed basis and StreamCat reach above say whose watershed the "
                       "values describe.", st["small_dim"])]
    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# The field forms: the five-page vector worksheet, served blank (2026-09-07,
# the assessor's call). It prints once for any site; the pulled desktop values
# live in the Field Forms dialog and in build_desktop_metrics_pdf, never on
# the worksheet. Byte for byte the shipped template.
# --------------------------------------------------------------------------- #
_FIELDFORM_DIR = config.DATA_DIR / "FieldForm"
FIELD_FORM_TEMPLATE = "SFARI_Field_Form_v1.0.pdf"
_ff_template_cache: dict = {}


def _template_bytes() -> bytes:
    if "pdf" not in _ff_template_cache:
        _ff_template_cache["pdf"] = (_FIELDFORM_DIR / FIELD_FORM_TEMPLATE).read_bytes()
    return _ff_template_cache["pdf"]


def watershed_basis_label(delin) -> str:
    """Plain words for ``watershedBasis``: which engine's watershed the
    evidence describes."""
    basis = (delin or {}).get("watershedBasis") or ""
    eng = (delin or {}).get("siteEngine") or {}
    if basis == "site-engine":
        ver = eng.get("engineVersion")
        return f"HR reach watershed (STAF site engine v{ver})" if ver else \
            "HR reach watershed (STAF site engine)"
    if basis == "nhdplus-v2-basin-of-surrogate":
        return "NHDPlus V2 basin of the nearest StreamCat reach (StreamCat lookup engine)"
    if eng.get("status") == "ok":
        ver = eng.get("engineVersion")
        tail = f"STAF site engine v{ver}" if ver else "STAF site engine"
        return f"NHDPlus V2 basin drawn, watershed metrics from the HR reach watershed ({tail})"
    return "NHDPlus V2 basin (StreamCat lookup engine)"


def streamcat_reach_label(delin) -> str:
    """The reach the StreamCat lookup engine's values describe: ``COMID 9327042
    (this reach)`` or ``Mink Brook (COMID 9327042), 199 ft downstream``."""
    from . import comid_anchor
    anchor = (delin or {}).get("siteAnchor")
    if not anchor:
        anchor = comid_anchor.synthetic(((delin or {}).get("delineation") or {}).get("comid"))
    return comid_anchor.reach_text(anchor)


def _reach_id_str(dl: dict) -> str:
    """Canonical Reach ID: ``COMID <id>``, ``NHDPlusID <id>`` for a reach on the
    high-resolution NHD, or, when both are missing, snapped lat/lon."""
    if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
        return f"NHDPlusID {dl['nhdplus_id']}"
    comid = dl.get("comid")
    if comid not in (None, "", "None"):
        return f"COMID {comid}"
    lat, lon = dl.get("snapped_lat"), dl.get("snapped_lon")
    if lat is not None and lon is not None:
        return f"{float(lat):.5f}, {float(lon):.5f}"
    return "(reach)"


def _coords_str(dl: dict) -> str:
    lat, lon = dl.get("snapped_lat"), dl.get("snapped_lon")
    if lat is not None and lon is not None:
        return f"{float(lat):.5f}, {float(lon):.5f}"
    return ""


def _site_slug(delin) -> str:
    """``nhdplusid-<id>``, ``comid-<id>``, a hemisphere-based coordinate pair, or
    empty: the site part of an export filename."""
    dl = (delin or {}).get("delineation", {})
    if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
        return f"nhdplusid-{dl['nhdplus_id']}"
    comid = dl.get("comid")
    if comid not in (None, "", "None"):
        return f"comid-{comid}"
    lat, lon = dl.get("snapped_lat"), dl.get("snapped_lon")
    if lat is not None and lon is not None:
        ns = "n" if float(lat) >= 0 else "s"
        ew = "e" if float(lon) >= 0 else "w"
        return f"{ns}{abs(float(lat)):.5f}-{ew}{abs(float(lon)):.5f}"
    return ""


def field_forms_filename(delin=None) -> str:
    """The blank worksheet does not depend on the site."""
    return "sfari-field-forms.pdf"


def desktop_metrics_filename(delin) -> str:
    """``sfari-desktop-metrics-nhdplusid-<id>.pdf`` (or comid, or coordinates)."""
    slug = _site_slug(delin)
    return f"sfari-desktop-metrics-{slug}.pdf" if slug else "sfari-desktop-metrics.pdf"


def build_field_forms_pdf(delineation=None, evidence=None) -> bytes:
    """The blank field forms: the five vector worksheet pages, untouched. The
    arguments are accepted for the callers' symmetry with the desktop metrics
    PDF and ignored."""
    return _template_bytes()

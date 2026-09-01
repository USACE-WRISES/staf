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

from . import config, scoring

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
    return [
        ("Stream", dl.get("gnis_name") or "(unnamed reach)"),
        ("Latitude", dl.get("snapped_lat")), ("Longitude", dl.get("snapped_lon")),
        ("COMID", dl.get("comid")), ("HUC8", dl.get("huc8")),
        ("Drainage area (km2)", dl.get("drainage_area_sqkm")),
        ("Reach length (ft)", dl.get("reach_length_ft")),
        ("Watershed basis", watershed_basis_label(delin)),
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
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                           ("BACKGROUND", (0, 0), (0, -1), head_bg)]))
    story += [t, Spacer(1, 10), Paragraph("Function scores (0-15)", styles["Heading3"])]

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
                        "0-15 function scores. Likert thresholds are national defaults — calibrate "
                        "regionally.", styles["Italic"])]

    # site-photo gallery
    gallery = []
    for cat, f, m in _ordered_metrics():
        ph = (metric_scores.get(m["metricId"]) or {}).get("photos") or []
        imgs = [im for im in (_img(p.get("uri"), 1.3 * inch, 1.3 * inch) for p in ph) if im]
        if imgs:
            gallery.append(Paragraph(f"<b>{f['name']}</b> — {m['name']}", small))
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
    if client == "bieger":
        return "Additional hydraulic estimate required", True
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
           ["Watershed basis", watershed_basis_label(delin)]]
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5deea")),
                           ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8"))]))
    return t


def build_desktop_metrics_pdf(delin, evidence) -> bytes:
    """Field-prep list of the desktop-supportable metrics: what to look up, the pulled
    value when the app already has one, and a link to the data source.

    Retained as the standalone/compatibility export; the same summary table now also
    forms the appendix of :func:`build_field_forms_pdf`.
    """
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
             Paragraph("Evaluate these metrics from the desktop before the field visit. Pulled "
                       "values are shown; look up the rest at the linked data sources.",
                       st["base"]["Italic"])]
    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Combined field-forms packet: the five paper worksheet pages (JPEG at exact
# Letter size) prefilled with derived identity + pulled desktop values, then the
# desktop-metrics summary as an appendix. One ReportLab document (no PDF merge).
# Layout rectangles come from data/FieldForm/manifest.json (see
# scripts/build_fieldform_manifest.py for how they were measured).
# --------------------------------------------------------------------------- #
_FIELDFORM_DIR = config.DATA_DIR / "FieldForm"
_ff_manifest_cache: dict = {}
_ff_bytes_cache: dict = {}


def _load_field_form_manifest() -> dict:
    if "m" not in _ff_manifest_cache:
        _ff_manifest_cache["m"] = json.loads(
            (_FIELDFORM_DIR / "manifest.json").read_text(encoding="utf-8"))
    return _ff_manifest_cache["m"]


def _field_form_bytes(filename: str) -> bytes:
    if filename not in _ff_bytes_cache:
        _ff_bytes_cache[filename] = (_FIELDFORM_DIR / filename).read_bytes()
    return _ff_bytes_cache[filename]


def watershed_basis_label(delin) -> str:
    """Plain words for ``watershedBasis``: which engine's watershed the
    evidence describes."""
    basis = (delin or {}).get("watershedBasis") or ""
    eng = (delin or {}).get("siteEngine") or {}
    if basis == "site-engine":
        ver = eng.get("engineVersion")
        return f"exact watershed (STAF site engine v{ver})" if ver else \
            "exact watershed (STAF site engine)"
    if basis == "nhdplus-v2-basin-of-surrogate":
        return "NHDPlus V2 basin of the nearest covered reach (StreamCat lookup engine)"
    if eng.get("status") == "ok":
        ver = eng.get("engineVersion")
        tail = f"STAF site engine v{ver}" if ver else "STAF site engine"
        return f"NHDPlus V2 basin drawn, watershed metrics from the exact watershed ({tail})"
    return "NHDPlus V2 basin (StreamCat lookup engine)"


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


def field_forms_filename(delin) -> str:
    """Download filename: ``sfari-field-forms-comid-<id>.pdf`` or a safe
    hemisphere-based coordinate fallback when COMID is absent."""
    dl = (delin or {}).get("delineation", {})
    if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
        return f"sfari-field-forms-nhdplusid-{dl['nhdplus_id']}.pdf"
    comid = dl.get("comid")
    if comid not in (None, "", "None"):
        return f"sfari-field-forms-comid-{comid}.pdf"
    lat, lon = dl.get("snapped_lat"), dl.get("snapped_lon")
    if lat is not None and lon is not None:
        ns = "n" if float(lat) >= 0 else "s"
        ew = "e" if float(lon) >= 0 else "w"
        return f"sfari-field-forms-{ns}{abs(float(lat)):.5f}-{ew}{abs(float(lon)):.5f}.pdf"
    return "sfari-field-forms.pdf"


def _rect_pt(rect_px, scale: float, page_h_px: int):
    """(x, y, w, h) top-left pixels -> (x, y, w, h) PDF points (origin bottom-left)."""
    x, y, w, h = rect_px
    return (x * scale, (page_h_px - y - h) * scale, w * scale, h * scale)


def _fit_size(c, text, font, max_w, start=8.0, floor=5.5):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    size = start
    while size > floor and stringWidth(text, font, size) > max_w:
        size -= 0.25
    return size


def _desktop_entries_by_function(evidence) -> dict:
    """{functionId: [compact print entries]} for desktop metrics with successful
    evidence only. Prefers ``field_value_text``; falls back to a bounded value_text."""
    evidence = evidence or {}
    out: dict = {}
    for m in config.desktop_metrics():
        ev = evidence.get(m["metricId"]) or {}
        if ev.get("status") != "ok":
            continue
        entry = ev.get("field_value_text") or ""
        if not entry:
            vt = ev.get("value_text") or ""
            entry = vt if len(vt) <= 30 else (vt[:27] + "...")
        if entry:
            out.setdefault(m["functionId"], []).append(to_print_safe(entry))
    return out


def _pack_desktop_line(c, entries, font, size, max_w, max_lines):
    """Greedily pack ``DESKTOP: e1 | e2 | ...`` into <= max_lines lines without
    breaking mid-entry. Returns (lines, overflow_count)."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    prefix = "DESKTOP: "
    lines, cur = [], prefix
    for i, e in enumerate(entries):
        cand = cur + (" | " if cur not in (prefix,) else "") + e
        if stringWidth(cand, font, size) <= max_w:
            cur = cand
        else:
            if cur not in (prefix,):
                lines.append(cur)
            cur = e
            if len(lines) >= max_lines:
                return lines, len(entries) - i
    lines.append(cur)
    return lines, 0


def _draw_notes_overlay(c, rect_pt, entries):
    """White-fill the Notes cell interior, redraw its border, print the pulled desktop
    values, then a NOTES writing line. 6-6.5 pt; overflow -> '+N values - see appendix'."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    x, y, w, h = rect_pt
    font = "Helvetica"
    c.saveState()
    # interior white-out (inset so the ruled border survives) + crisp redraw of border
    c.setFillGray(1.0)
    c.rect(x + 1.5, y + 1.5, w - 3.0, h - 3.0, stroke=0, fill=1)
    c.setLineWidth(0.6)
    c.setStrokeGray(0.15)
    c.rect(x, y, w, h, stroke=1, fill=0)

    pad = 3.0
    usable_w = w - 2 * pad
    size = 6.5
    leading = 7.5
    notes_reserve = 8.5                      # bottom band for the NOTES writing line
    avail_h = h - notes_reserve - 2.0
    max_lines = max(1, int(avail_h // leading))
    lines, overflow = _pack_desktop_line(c, entries, font, size, usable_w, max_lines)
    if overflow:
        tag = f"  +{overflow} values - see appendix"
        if lines:
            # trim the last line until the overflow tag fits
            last = lines[-1]
            while last and stringWidth(last + tag, font, size) > usable_w:
                last = last[:-1]
            lines[-1] = last + tag

    c.setFillGray(0.0)
    c.setFont(font, size)
    ty = y + h - pad - size
    for ln in lines:
        c.drawString(x + pad, ty, ln)
        ty -= leading

    # NOTES: label + a horizontal writing line along the bottom of the cell
    c.setFont(font, 6.0)
    c.setFillGray(0.25)
    ny = y + 2.5
    c.drawString(x + pad, ny, "NOTES:")
    lx = x + pad + stringWidth("NOTES:", font, 6.0) + 4
    c.setLineWidth(0.3)
    c.setStrokeGray(0.55)
    c.line(lx, ny + 0.5, x + w - pad, ny + 0.5)
    c.restoreState()


def _draw_metadata(c, delin, manifest):
    """Prefill the Page-1 Reach ID / Reach length / Coordinates blanks."""
    dl = (delin or {}).get("delineation", {})
    scale = manifest["pdf_scale"]
    page_h = manifest["page_px"][1]
    baseline_pt = (page_h - manifest["metadata_baseline_px"]) * scale
    rlen = dl.get("reach_length_ft")
    values = {
        "reach_id": _reach_id_str(dl),
        "reach_length": (f"{rlen} ft" if rlen not in (None, "", "None") else ""),
        "coordinates": _coords_str(dl),
    }
    c.saveState()
    c.setFillGray(0.0)
    font = "Helvetica"
    for key, rect_px in manifest["metadata_rects_px"].items():
        text = to_print_safe(values.get(key, ""))
        if not text:
            continue
        x, _, w, _ = _rect_pt(rect_px, scale, page_h)
        size = _fit_size(c, text, font, w - 2, start=8.5, floor=5.5)
        c.setFont(font, size)
        c.drawString(x + 1, baseline_pt, text)
    c.restoreState()


def _draw_form_page(c, page, delin, evidence, manifest):
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    scale = manifest["pdf_scale"]
    page_h = manifest["page_px"][1]
    pw_pt, ph_pt = letter
    c.drawImage(ImageReader(io.BytesIO(_field_form_bytes(page["filename"]))),
                0, 0, width=pw_pt, height=ph_pt)
    # mask the baked-in "Page 1 of 1" footer
    mx, my, mw, mh = _rect_pt(manifest["footer_mask_px"], scale, page_h)
    c.saveState()
    c.setFillGray(1.0)
    c.rect(mx, my, mw, mh, stroke=0, fill=1)
    c.restoreState()
    if page["order"] == 1:
        _draw_metadata(c, delin, manifest)
    by_fn = _desktop_entries_by_function(evidence)
    for nr in page["notes_rows"]:
        entries = by_fn.get(nr["functionId"])
        if entries:
            _draw_notes_overlay(c, _rect_pt(nr["rect"], scale, page_h), entries)


def _draw_furniture(c, total_pages, ident):
    """Running header (identity) + footer (coords + Page X of Y) on every page."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    pw_pt, ph_pt = letter
    left = 101 * 0.36                          # align with the form's table edges
    right = 1555 * 0.36
    c.saveState()
    c.setFillGray(0.35)
    c.setFont("Helvetica", 8)
    c.drawString(left, ph_pt - 24, "SFARI Field Packet")
    hr = ident["header_right"]
    while hr and stringWidth(hr, "Helvetica", 8) > (right - left - 110):
        hr = hr[:-1]
    c.drawRightString(right, ph_pt - 24, hr)
    if ident.get("footer_left"):
        c.drawString(left, 24, ident["footer_left"])
    c.drawRightString(right, 24, f"Page {c._pageNumber} of {total_pages}")
    c.restoreState()


def _numbered_canvas(ident):
    """Two-pass canvas: capture each page, then stamp header/footer with the final
    total page count so 'Page X of Y' is continuous across forms + appendix."""
    from reportlab.pdfgen import canvas

    class _NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            canvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_states = []

        def showPage(self):
            self._saved_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_states)
            for state in self._saved_states:
                self.__dict__.update(state)
                _draw_furniture(self, total, ident)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

    return _NumberedCanvas


def build_field_forms_pdf(delineation, evidence) -> bytes:
    """One combined print-ready packet: five prefilled field-worksheet pages followed
    by the desktop-metrics summary appendix. Returns PDF bytes.

    Form pages draw the original worksheet JPEG at exact US Letter size and overlay the
    derived site identity (Page 1) plus each function's pulled desktop values into its
    Notes/Other Metrics row; the appendix lists all 26 desktop metrics once. A two-pass
    NumberedCanvas gives continuous 'Page X of Y' over the whole document.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (BaseDocTemplate, Frame, NextPageTemplate, PageBreak,
                                    PageTemplate, Paragraph, Spacer)

    manifest = _load_field_form_manifest()
    dl = (delineation or {}).get("delineation", {})
    reach_id = _reach_id_str(dl)
    stream = to_print_safe(dl.get("gnis_name") or "(unnamed reach)")
    ident = {"header_right": f"{stream}  ({reach_id})", "footer_left": _coords_str(dl)}
    title = to_print_safe(f"SFARI Field Forms - {reach_id}")

    pw_pt, ph_pt = letter
    buf = io.BytesIO()
    pages = sorted(manifest["pages"], key=lambda p: p["order"])
    templates = []
    for i, page in enumerate(pages):
        frame = Frame(0, 0, pw_pt, ph_pt, id=f"formframe{i}",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

        def _onpage(c, doc, _page=page):
            _draw_form_page(c, _page, delineation, evidence, manifest)

        templates.append(PageTemplate(id=f"form{i}", frames=[frame], onPage=_onpage))
    # appendix frame kept inside the running header/footer band
    templates.append(PageTemplate(id="appendix",
                                  frames=[Frame(36, 44, pw_pt - 72, ph_pt - 44 - 52,
                                                id="appendixframe", leftPadding=0, rightPadding=0)]))

    doc = BaseDocTemplate(buf, pagesize=letter, title=title, author="STAF SFARI",
                          pageTemplates=templates)

    st = _ff_styles()
    story = []
    for i in range(1, len(pages)):
        story += [NextPageTemplate(f"form{i}"), PageBreak()]
    story += [NextPageTemplate("appendix"), PageBreak(),
              Paragraph("Desktop Metrics Summary", st["h1"]),
              Paragraph(f"{stream} - {reach_id}", st["small_dim"]),
              Paragraph("Watershed basis: "
                        + to_print_safe(watershed_basis_label(delineation)), st["small_dim"]),
              Spacer(1, 6), _metrics_summary_table(delineation, evidence), Spacer(1, 8),
              Paragraph("All 26 desktop-supportable metrics. Available values are also printed "
                        "in each function's Notes row on the form pages; unavailable and "
                        "manual-review metrics appear only here.", st["small_it"])]
    doc.build(story, canvasmaker=_numbered_canvas(ident))
    return buf.getvalue()

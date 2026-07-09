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
        ("Watershed area (km2)", dl.get("watershed_area_sqkm")),
        ("Reach length (ft)", dl.get("reach_length_ft")),
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
    w.writerow(["Category", "Function", "Metric", "Scale", "Likert", "Pulled evidence", "Source", "Note"])
    for cat, f, m in _ordered_metrics():
        mid = m["metricId"]
        rc = metric_scores.get(mid) or {}
        ev = evidence.get(mid) or {}
        w.writerow([cat, f["name"], m["name"], m.get("scale", ""), rc.get("likert") or "",
                    ev.get("value_text") or "", ev.get("source") or "", rc.get("note") or ""])
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
           ["Drainage / watershed area", f"{dl.get('drainage_area_sqkm')} / {dl.get('watershed_area_sqkm')} km2"],
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


def build_desktop_metrics_pdf(delin, evidence) -> bytes:
    """Field-prep list of the desktop-supportable metrics: what to look up, the pulled
    value when the app already has one, and a link to the data source."""
    from urllib.parse import urlparse
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            title="SFARI Desktop Metrics")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7, leading=8.4)
    small_dim = ParagraphStyle("small_dim", parent=small, textColor=colors.HexColor("#66708a"))
    small_it = ParagraphStyle("small_it", parent=small_dim, fontName="Helvetica-Oblique")
    grid = colors.HexColor("#d5deea")
    head_bg = colors.HexColor("#eef2f8")
    dl = (delin or {}).get("delineation", {})

    story = [Paragraph("SFARI Desktop Metrics", styles["Title"]),
             Paragraph(dl.get("gnis_name") or "(unnamed reach)", styles["Heading2"])]
    hdr = [["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage / watershed area", f"{dl.get('drainage_area_sqkm')} / {dl.get('watershed_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"]]
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                           ("BACKGROUND", (0, 0), (0, -1), head_bg)]))
    story += [t, Spacer(1, 10)]

    data = [["Discipline", "Function", "Metric", "Desktop evidence", "Value", "Data source"]]
    for cat, f, m in _ordered_metrics():
        if not m.get("desktopSupportable"):
            continue
        ds = m.get("desktopSource") or {}
        ev = evidence.get(m["metricId"]) or {}
        if ev.get("status") == "ok" and ev.get("value_text"):
            val = Paragraph(escape(ev["value_text"]), small)
        else:
            val = Paragraph("review in the field", small_it)
        url = ev.get("source_url") or ds.get("url") or ""
        name = ev.get("source") or (urlparse(url).netloc if url else "")
        if url:
            href = escape(url, {'"': "&quot;"})
            src = Paragraph(f'<link href="{href}" color="#1f4e8c">{escape(name or url)}</link>', small)
        else:
            src = Paragraph(escape(name), small)
        data.append([Paragraph(escape(cat), small_dim), Paragraph(escape(f["name"]), small_dim),
                     Paragraph(escape(m["name"]), small),
                     Paragraph(escape(ds.get("label") or ""), small), val, src])
    mt = Table(data, colWidths=[0.8 * inch, 1.15 * inch, 1.25 * inch, 1.5 * inch,
                                1.35 * inch, 1.25 * inch], repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 8),
              Paragraph("Evaluate these metrics from the desktop before the field visit. Pulled "
                        "values are shown; look up the rest at the linked data sources.",
                        styles["Italic"])]
    doc.build(story)
    return buf.getvalue()

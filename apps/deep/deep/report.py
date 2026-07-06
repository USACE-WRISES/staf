"""DEEP report exports — CSV, GeoJSON, and PDF.

Matplotlib-free (PDF uses reportlab Platypus) like SFARI/EASI, so it is safe on
Posit Connect. Each function takes the delineation, the loaded assessment
(``metricsByFunction`` with inlined curves), the per-metric measured-value state,
and the scored rollup dict from ``curves.score_site`` /
``scoring.score_assessment``. Per-metric indices are recomputed from the curves
so the report is decoupled from the app's live state objects.
"""
from __future__ import annotations

import csv
import io
import json

from . import curves, scoring


def _mbf(assessment) -> list[dict]:
    mbf = getattr(assessment, "metrics_by_function", None)
    return mbf if mbf is not None else (assessment or {}).get("metricsByFunction", [])


def _attr(assessment, obj_attr, dict_key, default=""):
    val = getattr(assessment, obj_attr, None)
    if val:
        return val
    if isinstance(assessment, dict):
        return assessment.get(dict_key, default)
    return default


def _rows(assessment, measured):
    """Yield ``(fn, metric, value, index)`` for every metric in the assessment."""
    for fn in _mbf(assessment):
        for m in fn.get("metrics", []):
            mv = measured.get(m["metricId"]) or {}
            val = None if mv.get("na") else mv.get("value")
            idx = None
            if val is not None and val != "":
                pts = curves.active_points(m, mv.get("stratum"))
                idx = curves.interp_curve(pts, float(val))
            yield fn, m, val, idx


def _header_pairs(delin, assessment, sc):
    dl = (delin or {}).get("delineation", {})
    si = sc.get("subIndices", {})
    return [
        ("Assessment", _attr(assessment, "assessment_name", "assessmentName")),
        ("Source", _attr(assessment, "source_citation", "sourceCitation")),
        ("Stream", dl.get("gnis_name") or "(unnamed reach)"),
        ("Latitude", dl.get("snapped_lat")), ("Longitude", dl.get("snapped_lon")),
        ("COMID", dl.get("comid")), ("HUC8", dl.get("huc8")),
        ("Drainage area (km2)", dl.get("drainage_area_sqkm")),
        ("Reach length (ft)", dl.get("reach_length_ft")),
        ("Ecosystem Condition Index", sc.get("ecosystemConditionIndex")),
        ("Physical sub-index", si.get("physical")),
        ("Chemical sub-index", si.get("chemical")),
        ("Biological sub-index", si.get("biological")),
    ]


def _function_rows(assessment, sc):
    """Yield ``(functionName, score|None, condition)`` once per assessment function."""
    fscores = sc.get("functionScores", {})
    seen = set()
    for fn in _mbf(assessment):
        fid = fn["functionId"]
        if fid in seen:
            continue
        seen.add(fid)
        s = fscores.get(fid)
        cond = scoring.function_score_band_label(s) if s is not None else "Not scored"
        yield fn.get("functionName", fid), s, cond


def build_csv(delin, assessment, measured, sc) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DEEP Detailed Assessment"])
    for k, v in _header_pairs(delin, assessment, sc):
        w.writerow([k, v])
    w.writerow([])
    w.writerow(["Function", "Discipline", "Metric", "Measured value", "Curve (source)",
                "Metric index (0-1)", "Note"])
    for fn, m, val, idx in _rows(assessment, measured):
        note = (measured.get(m["metricId"]) or {}).get("note", "")
        w.writerow([fn.get("functionName", ""), fn.get("discipline", ""),
                    m.get("metricName", m["metricId"]),
                    "" if val is None else val, (m.get("curve") or {}).get("layerName", ""),
                    "" if idx is None else round(idx, 3), note])
    w.writerow([])
    w.writerow(["Function", "Function score (0-15)", "Condition"])
    for name, s, cond in _function_rows(assessment, sc):
        w.writerow([name, "" if s is None else round(s, 1), cond])
    w.writerow([])
    w.writerow(["Outcome", "Sub-index"])
    for k in ("physical", "chemical", "biological"):
        w.writerow([k.title(), sc.get("subIndices", {}).get(k)])
    w.writerow(["Ecosystem Condition Index", sc.get("ecosystemConditionIndex")])
    return out.getvalue()


def build_geojson(delin, assessment, sc) -> str:
    dl = (delin or {}).get("delineation", {})
    props = {"assessment": _attr(assessment, "assessment_name", "assessmentName"),
             "source": _attr(assessment, "source_citation", "sourceCitation"),
             "stream": dl.get("gnis_name"), "comid": dl.get("comid"), "huc8": dl.get("huc8"),
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
        for fid, s in sc.get("functionScores", {}).items():
            pt_props[fid] = s
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [dl.get("snapped_lon"), dl.get("snapped_lat")]},
                      "properties": pt_props})
    return json.dumps({"type": "FeatureCollection", "features": feats}, indent=2)


def build_pdf(delin, assessment, measured, sc) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            title="DEEP Detailed Assessment Report")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7, leading=8.4)
    grid = colors.HexColor("#d5deea")
    head_bg = colors.HexColor("#eef2f8")
    band_col = {"NF": "#f5b5b5", "AR": "#f5e7a6", "F": "#c8d9f2"}
    dl = (delin or {}).get("delineation", {})
    story = [Paragraph("DEEP Detailed Assessment", styles["Title"]),
             Paragraph(_attr(assessment, "assessment_name", "assessmentName") or "Detailed assessment",
                       styles["Heading2"]),
             Paragraph(dl.get("gnis_name") or "(unnamed reach)", styles["Heading3"])]

    hdr = [["Source", _attr(assessment, "source_citation", "sourceCitation")],
           ["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"],
           ["Ecosystem Condition Index", f"{sc.get('ecosystemConditionIndex')}"]]
    t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                           ("BACKGROUND", (0, 0), (0, -1), head_bg)]))
    story += [t, Spacer(1, 10), Paragraph("Function scores (0-15)", styles["Heading3"])]

    data = [["Function", "Score", "Condition"]]
    bg = []
    for i, (name, s, cond) in enumerate(_function_rows(assessment, sc), start=1):
        short = {"Functioning": "F", "Functioning-at-Risk": "AR", "Non-Functioning": "NF"}.get(cond)
        data.append([name, "" if s is None else f"{s:.1f}", cond])
        if short and band_col.get(short):
            bg.append(("BACKGROUND", (2, i), (2, i), colors.HexColor(band_col[short])))
    ft = Table(data, colWidths=[3.9 * inch, 0.8 * inch, 1.8 * inch])
    ft.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg)] + bg))
    story += [ft, Spacer(1, 10), Paragraph("Outcome sub-indices", styles["Heading3"])]

    si = sc.get("subIndices", {})
    sit = Table([["Physical", "Chemical", "Biological", "ECI"],
                 [si.get("physical"), si.get("chemical"), si.get("biological"),
                  sc.get("ecosystemConditionIndex")]], colWidths=[1.6 * inch] * 4)
    sit.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                             ("BACKGROUND", (0, 0), (-1, 0), head_bg)]))
    story += [sit, Spacer(1, 10), Paragraph("Metric measurements & curve indices", styles["Heading3"])]

    mdata = [["Function", "Metric", "Value", "Index", "Curve (source)"]]
    for fn, m, val, idx in _rows(assessment, measured):
        mdata.append([Paragraph(fn.get("functionName", ""), small),
                      Paragraph(m.get("metricName", m["metricId"]), small),
                      "" if val is None else Paragraph(str(val), small),
                      "" if idx is None else f"{idx:.2f}",
                      Paragraph((m.get("curve") or {}).get("layerName", ""), small)])
    mt = Table(mdata, colWidths=[1.5 * inch, 2.1 * inch, 0.8 * inch, 0.5 * inch, 1.8 * inch],
               repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 8),
              Paragraph("Scores are computed automatically from the assessment's reference curves. "
                        "Confirm state/region applicability of the curve source.", styles["Italic"])]
    doc.build(story)
    return buf.getvalue()

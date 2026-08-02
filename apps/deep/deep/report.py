"""DEEP report exports — CSV, GeoJSON, and PDF.

Matplotlib-free (PDF uses reportlab Platypus) like SFARI/EASI, so it is safe on
Posit Connect. Each function takes the delineation, the loaded assessment
(``metricsByFunction`` with inlined curves), the per-metric measured-value state,
and the scored rollup dict from ``curves.score_site`` /
``scoring.score_assessment``. Per-metric indices are recomputed from the curves
so the report is decoupled from the app's live state objects.
"""
from __future__ import annotations

import base64
import csv
import io
import json

from . import assessments, curves, scoring, session


def _mbf(assessment) -> list[dict]:
    mbf = getattr(assessment, "metrics_by_function", None)
    return mbf if mbf is not None else (assessment or {}).get("metricsByFunction", [])


def _raw(assessment) -> dict:
    """The underlying assessment dict, whether ``assessment`` is a LoadedAssessment or a dict."""
    r = getattr(assessment, "raw", None)
    if isinstance(r, dict) and r:
        return r
    return assessment if isinstance(assessment, dict) else {}


def _provenance(assessment, region):
    """(version, lifecycle_status, content_digest, level3_dict, state_dict) for the report
    header/props. Version/status/digest derive from the inlined bundle; region is the
    already-resolved ``{"level3","state"}`` the caller passes (session field), defaulting
    to empty when absent."""
    raw = _raw(assessment)
    version = (raw.get("library") or {}).get("version")
    status = session.lifecycle_status(raw)
    digest = session.bundle_digest(raw)
    reg = region or {}
    return version, status, digest, (reg.get("level3") or {}), (reg.get("state") or {})


def _method_version(assessment) -> str:
    """Scoring method version stamped on the bundle's ``scoringContract`` (empty if none)."""
    return (_raw(assessment).get("scoringContract") or {}).get("methodVersion") or ""


def _state_label(st: dict) -> str:
    name, code = st.get("name"), st.get("code")
    if name and code:
        return f"{name} ({code})"
    return name or code or ""


def _l3_label(l3: dict) -> str:
    code, name = l3.get("code"), l3.get("name")
    if code and name:
        return f"{code} {name}"
    return name or code or ""


def _region_combined(st: dict, l3: dict) -> str:
    parts = [p for p in (_state_label(st), _l3_label(l3)) if p]
    return "  ·  ".join(parts) if parts else "Not resolved"


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


def _header_pairs(delin, assessment, sc, region=None):
    dl = (delin or {}).get("delineation", {})
    si = sc.get("subIndices", {})
    version, status, digest, l3, st = _provenance(assessment, region)
    return [
        ("Assessment", _attr(assessment, "assessment_name", "assessmentName")),
        ("Assessment version", "" if version is None else version),
        ("Lifecycle status", status.title()),
        ("Scoring method version", _method_version(assessment)),
        ("Source", _attr(assessment, "source_citation", "sourceCitation")),
        ("State (region match)", _state_label(st)),
        ("Level III ecoregion", _l3_label(l3)),
        ("Content digest", digest),
        ("Stream", dl.get("gnis_name") or "(unnamed reach)"),
        ("Latitude", dl.get("snapped_lat")), ("Longitude", dl.get("snapped_lon")),
        ("COMID", dl.get("comid")), ("HUC8", dl.get("huc8")),
        ("Drainage area (km2)", dl.get("drainage_area_sqkm")),
        ("Reach length (ft)", dl.get("reach_length_ft")),
        ("Ecosystem Condition Index", sc.get("ecosystemConditionIndex")),
        # An export outlives the session, so the index's denominator travels with it:
        # scoring correctly excludes uncovered functions from both numerator and
        # denominator, which makes a partial-coverage ECI look directly comparable to
        # a full-framework one unless the coverage is stated alongside it.
        ("STAF function coverage", _coverage_label(assessment)),
        ("Physical sub-index", si.get("physical")),
        ("Chemical sub-index", si.get("chemical")),
        ("Biological sub-index", si.get("biological")),
    ]


def _coverage_label(assessment) -> str:
    """``"12 of 20 (8 documented exclusions)"`` / ``"... (not declared)"``."""
    cov = assessments.coverage_of(assessment)
    base = f"{cov['covered']} of {cov['total']}"
    if cov["covered"] >= cov["total"]:
        return base
    if cov["declared"]:
        n = cov["excluded"]
        return f"{base} ({n} documented exclusion{'' if n == 1 else 's'})"
    return f"{base} (not declared)"


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


def build_csv(delin, assessment, measured, sc, region=None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["DEEP Detailed Assessment"])
    for k, v in _header_pairs(delin, assessment, sc, region):
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
    w.writerow(["STAF function coverage", _coverage_label(assessment)])
    return out.getvalue()


def build_geojson(delin, assessment, sc, region=None) -> str:
    dl = (delin or {}).get("delineation", {})
    version, status, digest, l3, st = _provenance(assessment, region)
    props = {"assessment": _attr(assessment, "assessment_name", "assessmentName"),
             "assessment_version": version, "lifecycle_status": status,
             "source": _attr(assessment, "source_citation", "sourceCitation"),
             "region_state": _state_label(st), "region_level3": _l3_label(l3),
             "content_digest": digest,
             "stream": dl.get("gnis_name"), "comid": dl.get("comid"), "huc8": dl.get("huc8"),
             "drainage_area_sqkm": dl.get("drainage_area_sqkm"),
             "ecosystem_condition_index": sc.get("ecosystemConditionIndex"),
             "staf_function_coverage": _coverage_label(assessment)}
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


def build_pdf(delin, assessment, measured, sc, region=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            title="DEEP Detailed Assessment Report")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=7, leading=8.4)

    def _img(uri, max_w, max_h):
        """A base64 data-URI photo (from the measure state) scaled into a platypus Image."""
        if not uri or "," not in uri:
            return None
        try:
            raw = base64.b64decode(uri.split(",", 1)[1])
            iw, ih = ImageReader(io.BytesIO(raw)).getSize()
            s = min(max_w / iw, max_h / ih, 1.0)
            return Image(io.BytesIO(raw), width=iw * s, height=ih * s)
        except Exception:  # noqa: BLE001
            return None
    grid = colors.HexColor("#d5deea")
    head_bg = colors.HexColor("#eef2f8")
    band_col = {"NF": "#f5b5b5", "AR": "#f5e7a6", "F": "#c8d9f2"}
    dl = (delin or {}).get("delineation", {})
    story = [Paragraph("DEEP Detailed Assessment", styles["Title"]),
             Paragraph(_attr(assessment, "assessment_name", "assessmentName") or "Detailed assessment",
                       styles["Heading2"]),
             Paragraph(dl.get("gnis_name") or "(unnamed reach)", styles["Heading3"])]

    version, status, digest, l3, st = _provenance(assessment, region)
    ver_txt = "unversioned" if version is None else f"v{version}"
    hdr = [["Source", _attr(assessment, "source_citation", "sourceCitation")],
           ["Version / status", f"{ver_txt}  ·  {status.title()}"],
           ["Region match", _region_combined(st, l3)],
           ["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"],
           ["Content digest", digest or "(none)"],
           ["Ecosystem Condition Index", f"{sc.get('ecosystemConditionIndex')}"],
           ["STAF function coverage", _coverage_label(assessment)]]
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

    # site-photo gallery — per-metric photos attached during measurement
    gallery = []
    for fn, m, _val, _idx in _rows(assessment, measured):
        ph = (measured.get(m["metricId"]) or {}).get("photos") or []
        imgs = [im for im in (_img(p.get("uri"), 1.3 * inch, 1.3 * inch) for p in ph) if im]
        if imgs:
            gallery.append(Paragraph(f"<b>{fn.get('functionName', '')}</b> - "
                                     f"{m.get('metricName', m['metricId'])}", small))
            gt = Table([imgs], colWidths=[1.42 * inch] * len(imgs), hAlign="LEFT")
            gt.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            gallery += [gt, Spacer(1, 6)]
    if gallery:
        story += [Spacer(1, 10), Paragraph("Site photos", styles["Heading3"])] + gallery

    doc.build(story)
    return buf.getvalue()


def field_forms_filename(assessment) -> str:
    """Filename for the downloadable field-forms packet."""
    aid = _attr(assessment, "assessment_id", "assessmentId") or "assessment"
    return f"deep-field-forms-{aid}.pdf"


def build_field_forms_pdf(assessment, ref: str = "") -> bytes:
    """Print-ready field packet: every metric in the assessment with a blank write-in
    Value / Notes cell, grouped by function.

    PLACEHOLDER — generated locally from the bundle's metric list so field crews have
    something to carry today. Replace with the StreamCurves-authored field-form PDF
    shipped inside the published bundle (a future bundle key) once that exists.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch, title="DEEP Field Forms")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    grid = colors.HexColor("#c3ccda")
    head_bg = colors.HexColor("#eef2f8")

    name = _attr(assessment, "assessment_name", "assessmentName") or "Detailed assessment"
    cite = _attr(assessment, "source_citation", "sourceCitation")
    sub = "  ·  ".join([p for p in (ref, cite) if p])
    story = [Paragraph("DEEP Field Forms", styles["Title"]),
             Paragraph(name, styles["Heading2"])]
    if sub:
        story.append(Paragraph(sub, styles["Italic"]))
    story += [Paragraph("Record each metric's measured value in the field, then enter the values in "
                        "DEEP to compute the reference-curve scores.", small), Spacer(1, 8)]

    any_fn = False
    for fn in _mbf(assessment):
        any_fn = True
        disc = fn.get("discipline", "")
        head = fn.get("functionName", fn.get("functionId", ""))
        if disc:
            head = f"{head}  ({disc})"
        story.append(Paragraph(head, styles["Heading3"]))
        data = [["Metric", "Units / measure", "Value", "Notes"]]
        for m in fn.get("metrics", []):
            data.append([Paragraph(m.get("metricName", m.get("metricId", "")), small),
                         Paragraph(m.get("xLabel", ""), small), "", ""])
        t = Table(data, colWidths=[2.3 * inch, 1.9 * inch, 1.0 * inch, 2.1 * inch],
                  rowHeights=[0.28 * inch] + [0.4 * inch] * (len(data) - 1), repeatRows=1)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8),
                               ("GRID", (0, 0), (-1, -1), 0.4, grid),
                               ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t, Spacer(1, 10)]

    if not any_fn:
        story.append(Paragraph("This assessment has no metrics defined.", small))
    doc.build(story)
    return buf.getvalue()

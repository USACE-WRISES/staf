"""DEEP report exports: CSV, GeoJSON, PDF, and the field-form packet.

Matplotlib-free (PDF uses reportlab Platypus) like SFARI/EASI, so it is safe on
Posit Connect. Each function takes the delineation, the loaded assessment
(``metricsByFunction`` with inlined curves), the per-metric measured-value state,
and the scored rollup dict from ``curves.score_site`` /
``scoring.score_assessment``. Per-metric indices are recomputed through the
scoring layer (:func:`curves.metric_index`, so the train/serve pairing rule
applies to exports exactly as it does in the app) and every row carries its
provenance: origin, basis (which engine or layer produced a desktop value),
source label, the engine flag, the curve's predictor source, and the scoring
advisory.
"""
from __future__ import annotations

import base64
import csv
import io
import json

from . import assessments, curves, delineation, measure, reference_support, reportmap
from . import scoring, session


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


# --------------------------------------------------------------------------- #
# The two watershed engines in the exports
# --------------------------------------------------------------------------- #
_BASIS_WORDS = (("site-engine", "STAF site engine (HR reach watershed)"),
                ("streamcat", "StreamCat lookup engine (NHDPlus V2 basin)"),
                ("nlcd", "NLCD (HR reach watershed polygon)"), ("3dep", "3DEP"),
                ("nid", "USACE National Inventory of Dams"))


def desktop_basis_label(measured) -> str:
    """One short line on what the desktop values describe, counted by basis:
    ``STAF site engine (HR reach watershed) 8``, ``StreamCat lookup engine
    (NHDPlus V2 basin) 5, 3DEP 3``, or ``none``. The watershed row names the
    delineation; this row names the values (2026-09-07)."""
    counts: dict[str, int] = {}
    for rc in (measured or {}).values():
        if not isinstance(rc, dict) or rc.get("origin") != "desktop":
            continue
        basis = str(rc.get("basis") or ("site-engine" if rc.get("engine") else "")) or "other"
        counts[basis] = counts.get(basis, 0) + 1
    parts = [f"{words} {counts.pop(key)}" for key, words in _BASIS_WORDS if key in counts]
    parts += [f"{key} {n}" for key, n in counts.items()]
    return ", ".join(parts) if parts else "none"


def watershed_basis_label(delin) -> str:
    """Plain words for ``watershedBasis``: the watershed the site was
    delineated on. :func:`desktop_basis_label` and the per-metric Basis
    column say what each desktop value describes."""
    basis = (delin or {}).get("watershedBasis") or ""
    eng = (delin or {}).get("siteEngine") or {}
    ver = eng.get("engineVersion")
    if basis == "site-engine":
        return f"HR reach watershed (STAF site engine v{ver})" if ver else \
            "HR reach watershed (STAF site engine)"
    if basis == "nhdplus-v2-basin-of-surrogate":
        return "NHDPlus V2 basin of the nearest StreamCat reach (StreamCat lookup engine)"
    if eng.get("status") == "ok":
        tail = f"STAF site engine v{ver}" if ver else "STAF site engine"
        return f"NHDPlus V2 basin drawn, HR reach watershed computed ({tail})"
    return "NHDPlus V2 basin (StreamCat lookup engine)"


def streamcat_reach_label(delin) -> str:
    """The reach the StreamCat lookup engine's values describe: ``COMID 9327042
    (this reach)`` or ``Mink Brook (COMID 9327042), 446 ft downstream``."""
    from . import comid_anchor
    anchor = (delin or {}).get("siteAnchor")
    if not anchor:
        anchor = comid_anchor.synthetic(((delin or {}).get("delineation") or {}).get("comid"))
    return comid_anchor.reach_text(anchor)


def _metric_predictor_source(m: dict, assessment) -> str:
    """The per-metric ``predictorSource`` stamp, else the bundle's, else streamcat."""
    own = (m or {}).get("predictorSource")
    if own:
        return str(own)
    return assessments.predictor_source_of(assessment)


def _rows(assessment, measured):
    """Yield ``(fn, metric, value, index, meta)`` for every metric in the assessment.

    The index comes from :func:`curves.metric_index`, so an engine-computed value
    against a StreamCat-fitted curve yields ``None`` here exactly as it does in the
    app (the train/serve pairing rule). ``meta`` carries ``origin``, ``basis``,
    ``source``, ``engine``, ``predictor_source``, ``advisory`` (the scoring
    advisory text or None) and ``reference_only`` (True when the pairing rule
    withheld the index).
    """
    measured = measured or {}
    mv_objs = measure.measured_from_state(measured)
    for fn in _mbf(assessment):
        for m in fn.get("metrics", []):
            mid = m["metricId"]
            raw = measured.get(mid) or {}
            mv = mv_objs.get(mid)
            val = None if raw.get("na") else raw.get("value")
            idx = None
            advisory = None
            reference_only = False
            if val is not None and val != "" and mv is not None:
                idx = curves.metric_index(mv, m)
                advisory = curves.metric_warning(mv, m)
                reference_only = (idx is None
                                  and curves.engine_pairing_advisory(mv, m) is not None)
            meta = {
                "origin": raw.get("origin", "field") if raw else "",
                "basis": raw.get("basis", "") or "",
                "source": raw.get("source", "") or "",
                "engine": bool(mv.engine) if mv is not None else False,
                "predictor_source": _metric_predictor_source(m, assessment),
                "advisory": advisory,
                "reference_only": reference_only,
                # StreamCurves methodology 0.12; both empty for an older bundle
                "scored_against": reference_support.support_line(m),
                "curve_set": curve_set_label(m, raw),
            }
            yield fn, m, val, idx, meta


def curve_set_label(m: dict, raw: dict) -> str:
    """The curve layer a stratified metric was scored on, with how it was
    chosen: ``Steep (2 percent and above) (auto)`` or ``... (chosen)``. Empty
    for a single-curve metric."""
    strata = curves.curve_strata(m)
    if len(strata) < 2:
        return ""
    chosen = (raw or {}).get("stratum")
    if chosen is None:
        chosen = m.get("activeStratum") or strata[0]
    how = "auto" if (raw or {}).get("stratumAuto") else (
        "chosen" if (raw or {}).get("stratum") is not None else "default")
    return f"{reference_support.stratum_label(chosen, m)} ({how})"


def withheld_rows(assessment) -> list[list[str]]:
    """``[metric, functions, reason]`` per metric the assessment withholds for
    insufficient reference support (empty for an older bundle)."""
    out = []
    for w in reference_support.withheld(assessment):
        fns = ", ".join(str(f.get("functionName") or f.get("functionId"))
                        for f in w.get("functions") or [])
        out.append([str(w.get("metricName") or w.get("metricId") or ""), fns,
                    str(w.get("statement") or "Insufficient reference support, not scored.")])
    return out


def _header_pairs(delin, assessment, sc, region=None, measured=None):
    dl = (delin or {}).get("delineation", {})
    si = sc.get("subIndices", {})
    version, status, digest, l3, st = _provenance(assessment, region)
    return [
        ("Assessment", _attr(assessment, "assessment_name", "assessmentName")),
        ("Assessment version", "" if version is None else version),
        ("Lifecycle status", session.status_label(status)),
        ("Scoring method version", _method_version(assessment)),
        ("Source", _attr(assessment, "source_citation", "sourceCitation")),
        ("Predictor source", assessments.predictor_source_of(assessment)),
        ("State (region match)", _state_label(st)),
        ("Level III ecoregion", _l3_label(l3)),
        ("Content digest", digest),
        ("Stream", dl.get("gnis_name") or "(unnamed reach)"),
        ("Latitude", dl.get("snapped_lat")), ("Longitude", dl.get("snapped_lon")),
        ("COMID", dl.get("comid")), ("HUC8", dl.get("huc8")),
        ("Drainage area (km2)", dl.get("drainage_area_sqkm")),
        ("HR reach watershed area (km2)", dl.get("watershed_area_sqkm")),
        ("Reach length (ft)", dl.get("reach_length_ft")),
        ("Watershed basis", watershed_basis_label(delin)),
        ("Desktop values", desktop_basis_label(measured)),
        ("StreamCat reach", streamcat_reach_label(delin)),
        ("Ecosystem Condition Index", sc.get("ecosystemConditionIndex")),
        # An export outlives the session, so the claim travels with it rather than
        # the bare number. Scoring excludes an unassessed function from numerator
        # and denominator alike, which lands a partial assessment on the same 0 to 1
        # scale as a complete one and reads as comparable when it is not, so where
        # anything is unassessed the index is an interval and says so.
        ("Condition claim", scoring.index_claim(sc)),
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
    for k, v in _header_pairs(delin, assessment, sc, region, measured):
        w.writerow([k, v])
    w.writerow([])
    # The last two columns were added for StreamCurves methodology 0.12 and sit
    # at the end so every earlier column keeps its position.
    w.writerow(["Function", "Discipline", "Metric", "Measured value", "Curve (source)",
                "Metric index (0-1)", "Note", "Origin", "Basis", "Source", "Engine value",
                "Predictor source", "Scoring advisory", "Scored against", "Curve set"])
    for fn, m, val, idx, meta in _rows(assessment, measured):
        note = (measured.get(m["metricId"]) or {}).get("note", "")
        w.writerow([fn.get("functionName", ""), fn.get("discipline", ""),
                    m.get("metricName", m["metricId"]),
                    "" if val is None else val, (m.get("curve") or {}).get("layerName", ""),
                    "" if idx is None else round(idx, 3), note,
                    meta["origin"], meta["basis"], meta["source"],
                    "yes" if meta["engine"] else "", meta["predictor_source"],
                    meta["advisory"] or "", meta["scored_against"], meta["curve_set"]])
    withheld = withheld_rows(assessment)
    if withheld:
        w.writerow([])
        w.writerow(["Metrics withheld for insufficient reference support (not scored)"])
        w.writerow(["Metric", "Functions", "Reason"])
        for row in withheld:
            w.writerow(row)
    w.writerow([])
    w.writerow(["Function", "Function score (0-15)", "Condition"])
    for name, s, cond in _function_rows(assessment, sc):
        w.writerow([name, "" if s is None else round(s, 1), cond])
    w.writerow([])
    w.writerow(["Outcome", "Sub-index"])
    for k in ("physical", "chemical", "biological"):
        w.writerow([k.title(), sc.get("subIndices", {}).get(k)])
    w.writerow(["Ecosystem Condition Index", sc.get("ecosystemConditionIndex")])
    w.writerow(["Condition claim", scoring.index_claim(sc)])
    w.writerow(["STAF function coverage", _coverage_label(assessment)])
    return out.getvalue()


def build_geojson(delin, assessment, sc, region=None, measured=None) -> str:
    dl = (delin or {}).get("delineation", {})
    version, status, digest, l3, st = _provenance(assessment, region)
    withheld = sum(1 for _fn, _m, _v, _i, meta in _rows(assessment, measured or {})
                   if meta["reference_only"])
    props = {"assessment": _attr(assessment, "assessment_name", "assessmentName"),
             "assessment_version": version, "lifecycle_status": status,
             "source": _attr(assessment, "source_citation", "sourceCitation"),
             "predictor_source": assessments.predictor_source_of(assessment),
             "region_state": _state_label(st), "region_level3": _l3_label(l3),
             "content_digest": digest,
             "stream": dl.get("gnis_name"), "comid": dl.get("comid"), "huc8": dl.get("huc8"),
             "drainage_area_sqkm": dl.get("drainage_area_sqkm"),
             "watershed_basis": (delin or {}).get("watershedBasis") or "nhdplus-v2-basin",
             "engine_values_withheld": withheld,
             "streamcat_reach": streamcat_reach_label(delin),
             "ecosystem_condition_index": sc.get("ecosystemConditionIndex"),
             "ecosystem_condition_index_bounds": sc.get("ecosystemConditionIndexBounds"),
             "condition_claim": scoring.index_claim(sc),
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


def _source_cell_text(meta: dict) -> str:
    """``origin`` plus the source label, and the reference-only flag."""
    origin = meta.get("origin") or ""
    src = meta.get("source") or ""
    txt = f"{origin}: {src}" if (origin and src) else (src or origin)
    if meta.get("reference_only"):
        txt += " (reference only)"
    return txt


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
           ["Predictor source", assessments.predictor_source_of(assessment)],
           ["Region match", _region_combined(st, l3)],
           ["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
           ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
           ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
           ["Reach length", f"{dl.get('reach_length_ft')} ft"],
           ["Watershed basis", watershed_basis_label(delin)],
           ["Desktop values", desktop_basis_label(measured)],
           ["StreamCat reach", streamcat_reach_label(delin)],
           ["Content digest", digest or "(none)"],
           ["Ecosystem Condition Index", scoring.index_claim(sc)],
           ["STAF function coverage", _coverage_label(assessment)]]
    if dl.get("watershed_area_sqkm") is not None:
        hdr.insert(7, ["HR reach watershed area", f"{dl.get('watershed_area_sqkm')} km2"])
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
    # "not assessed" rather than a blank cell: an outcome with no direct contributor
    # is unmeasured, and a blank reads as an oversight.
    cell = lambda v: "not assessed" if v is None else v          # noqa: E731
    sit = Table([["Physical", "Chemical", "Biological", "ECI"],
                 [cell(si.get("physical")), cell(si.get("chemical")),
                  cell(si.get("biological")),
                  cell(sc.get("ecosystemConditionIndex"))]], colWidths=[1.6 * inch] * 4)
    sit.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.3, grid),
                             ("BACKGROUND", (0, 0), (-1, 0), head_bg)]))
    story += [sit, Spacer(1, 10), Paragraph("Metric measurements & curve indices", styles["Heading3"])]

    mdata = [["Function", "Metric", "Value", "Index", "Source", "Curve (source)"]]
    advisories = []
    support_rows: dict[str, list] = {}
    for fn, m, val, idx, meta in _rows(assessment, measured):
        if meta["scored_against"] and m["metricId"] not in support_rows:
            support_rows[m["metricId"]] = [
                Paragraph(m.get("metricName", m["metricId"]), small),
                Paragraph(meta["scored_against"], small),
                Paragraph(meta["curve_set"], small)]
        idx_txt = "ref. only" if meta["reference_only"] else ("" if idx is None else f"{idx:.2f}")
        mdata.append([Paragraph(fn.get("functionName", ""), small),
                      Paragraph(m.get("metricName", m["metricId"]), small),
                      "" if val is None else Paragraph(str(val), small),
                      idx_txt,
                      Paragraph(_source_cell_text(meta), small),
                      Paragraph((m.get("curve") or {}).get("layerName", ""), small)])
        if meta["advisory"]:
            advisories.append(f"<b>{m.get('metricName', m['metricId'])}</b>: {meta['advisory']}")
    mt = Table(mdata, colWidths=[1.3 * inch, 1.7 * inch, 0.7 * inch, 0.55 * inch,
                                 1.6 * inch, 1.2 * inch],
               repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 8)]
    if advisories:
        story += [Paragraph("Scoring advisories", styles["Heading4"])]
        story += [Paragraph(a, small) for a in advisories]
        story += [Spacer(1, 6)]
    # What each metric is scored against, and what the assessment withholds
    # (bundles built under StreamCurves methodology 0.12; absent otherwise).
    light = colors.HexColor("#e5e8ee")
    if support_rows:
        statement = reference_support.reference_method_statement(assessment)
        story += [Paragraph("Reference support", styles["Heading3"])]
        if statement:
            story += [Paragraph(statement, small), Spacer(1, 4)]
        st_tbl = Table([["Metric", "Scored against", "Curve set"]] + list(support_rows.values()),
                       colWidths=[1.8 * inch, 3.75 * inch, 1.5 * inch], repeatRows=1)
        st_tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                                    ("GRID", (0, 0), (-1, -1), 0.3, light),
                                    ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [st_tbl, Spacer(1, 8)]
    withheld = withheld_rows(assessment)
    if withheld:
        story += [Paragraph("Metrics withheld for insufficient reference support",
                            styles["Heading3"]),
                  Paragraph("These metrics have no curve and are not scored. No defensible pool "
                            "of least-disturbed stations exists for them in this ecoregion or "
                            "its parent ecoregions.", small), Spacer(1, 4)]
        wt = Table([["Metric", "Functions"]]
                   + [[Paragraph(r[0], small), Paragraph(r[1], small)] for r in withheld],
                   colWidths=[3.0 * inch, 4.05 * inch], repeatRows=1)
        wt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                                ("GRID", (0, 0), (-1, -1), 0.3, light),
                                ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [wt, Spacer(1, 8)]
    story += [Paragraph("Scores are computed automatically from the assessment's reference curves. "
                        "Confirm state/region applicability of the curve source.", styles["Italic"])]

    # site-photo gallery: per-metric photos attached during measurement
    gallery = []
    for fn, m, _val, _idx, _meta in _rows(assessment, measured):
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


def _desktop_entries(assessment, measured) -> dict:
    """``{metricId: (value_text, notes_text)}`` for every desktop-computed value:
    the value for the packet's Value cell and ``DESKTOP: <source>`` (plus
    ``reference only`` when the pairing rule withholds its index) for Notes."""
    out = {}
    if not measured:
        return out
    for _fn, m, val, _idx, meta in _rows(assessment, measured):
        if meta.get("origin") != "desktop" or val in (None, ""):
            continue
        note = f"DESKTOP: {meta.get('source') or meta.get('basis') or 'desktop'}"
        if meta.get("reference_only"):
            note += " (reference only)"
        out[m["metricId"]] = (str(val), note)
    return out


def build_field_forms_pdf(assessment, ref: str = "", *, measured=None,
                          delineation=None) -> bytes:
    """The field worksheet for the loaded assessment: every metric grouped by
    function in the SFARI form's layout, with a write-in Value and Index cell.
    Desktop-computed values (``measured``) are printed in the Value cell with
    ``DESKTOP: <source>`` under the metric so the crew sees what the desk already
    answered, and a site line names the delineated reach.

    Generated on the fly by :mod:`deep.field_form`, so it is always in step with
    the assessment and version that is loaded. This function keeps the call
    shape the app and the exports have always used.
    """
    from . import field_form
    return field_form.build_field_form_pdf(
        assessment, ref, measured=measured, delineation=delineation,
        desktop_entries=_desktop_entries(assessment, measured))


# --------------------------------------------------------------------------- #
# The metrics list of the Field Forms dialog and its PDF
# --------------------------------------------------------------------------- #
STATUS_AVAILABLE = "Available"
STATUS_PENDING = "Pending"
STATUS_UNAVAILABLE = "Unavailable"
STATUS_REFERENCE_ONLY = "Reference only"
STATUS_FIELD = "Measure in the field"
STATUS_ENTERED = "Entered"
STATUS_NA = "Not applicable"
STATUS_WITHHELD = "Not scored"


def metric_rows(assessment, measured, *, computing: bool = False,
                desktop_ids=None) -> list[dict]:
    """One row per metric of the assessment, for the dialog table and the
    metrics PDF: ``{discipline, function, metric, units, code, method, status,
    value, source}``.

    Status: **Available** (the desk answered it), **Pending** (a desk metric
    while the pull is still running), **Unavailable** (a desk metric no source
    answered), **Reference only** (an engine value the pairing rule keeps out of
    the score), **Measure in the field**, **Entered** (the assessor typed it),
    and **Not scored** for a metric the assessment withholds.
    """
    from . import field_form
    if desktop_ids is None:
        desktop_ids = field_form._desktop_ids()
    measured = measured or {}
    rows: list[dict] = []
    seen: set = set()
    for fn, m, val, _idx, meta in _rows(assessment, measured):
        mid = m["metricId"]
        code = field_form.measure_code(m, desktop_ids)
        raw = measured.get(mid) or {}
        if val not in (None, ""):
            if meta.get("reference_only"):
                status = STATUS_REFERENCE_ONLY
            elif meta.get("origin") == "desktop":
                status = STATUS_AVAILABLE
            else:
                status = STATUS_ENTERED
        elif raw.get("na"):
            status = STATUS_NA
        elif code == "D":
            status = STATUS_PENDING if computing else STATUS_UNAVAILABLE
        else:
            status = STATUS_FIELD
        rows.append({
            "discipline": fn.get("discipline", ""), "function": fn.get("functionName", ""),
            "metricId": mid, "metric": m.get("metricName", mid),
            "units": field_form.units_of(m), "code": code,
            "method": field_form.method_text(m), "status": status,
            "value": "" if val in (None, "") else str(val),
            "source": meta.get("source") or "",
            "scored_against": meta.get("scored_against") or "",
            "repeat": mid in seen})
        seen.add(mid)
    for w in reference_support.withheld(assessment):
        fns = ", ".join(str(f.get("functionName") or f.get("functionId"))
                        for f in w.get("functions") or [])
        rows.append({"discipline": "", "function": fns,
                     "metricId": w.get("metricId"), "metric": w.get("metricName") or "",
                     "units": w.get("units") or "", "code": "", "method": "",
                     "status": STATUS_WITHHELD, "value": "",
                     "source": "Insufficient reference support",
                     "scored_against": "", "repeat": False})
    return rows


def metric_status_counts(rows: list[dict]) -> dict[str, int]:
    """Counts by status over distinct metrics (a metric that serves several
    functions is counted once)."""
    out: dict[str, int] = {}
    for r in rows:
        if r.get("repeat"):
            continue
        out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def metrics_filename(assessment) -> str:
    aid = _attr(assessment, "assessment_id", "assessmentId") or "assessment"
    return f"deep-metrics-{aid}.pdf"


def build_metrics_pdf(assessment, ref: str = "", *, measured=None, delineation=None) -> bytes:
    """The metrics list as a PDF: the site, every metric of the assessment with
    how it is measured, its status, the value the desk answered and its source.
    The second download of the Field Forms dialog."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from . import field_form
    safe = field_form.to_print_safe
    base = getSampleStyleSheet()
    small = ParagraphStyle("m_small", parent=base["BodyText"], fontSize=7, leading=8.4)
    dim = ParagraphStyle("m_dim", parent=small, textColor=colors.HexColor("#66708a"))
    ital = ParagraphStyle("m_it", parent=small, fontName="Helvetica-Oblique",
                          textColor=colors.HexColor("#66708a"))
    head_bg = colors.HexColor("#eef2f8")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            title="DEEP Metrics", invariant=1)
    name = _attr(assessment, "assessment_name", "assessmentName") or "Detailed assessment"
    dl = (delineation or {}).get("delineation") or {}
    story = [Paragraph("DEEP Metrics", base["Title"]),
             Paragraph(escape(safe(name + (f"  ({ref})" if ref else ""))), base["Heading2"])]
    if dl:
        hdr = [["Stream", safe(dl.get("gnis_name") or "(unnamed reach)")],
               ["Coordinates", f"{dl.get('snapped_lat')}, {dl.get('snapped_lon')}"],
               ["COMID / HUC8", f"{dl.get('comid')} / {dl.get('huc8')}"],
               ["Drainage area", f"{dl.get('drainage_area_sqkm')} km2"],
               ["Reach length", f"{dl.get('reach_length_ft')} ft"],
               ["Watershed basis", safe(watershed_basis_label(delineation))],
               ["StreamCat reach", safe(streamcat_reach_label(delineation))]]
        t = Table(hdr, colWidths=[2.3 * inch, 4.4 * inch])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5deea")),
                               ("BACKGROUND", (0, 0), (0, -1), head_bg)]))
        story += [t, Spacer(1, 10)]

    data = [["Function", "Metric", "F / D", "How it is measured", "Status", "Value", "Source"]]
    for r in metric_rows(assessment, measured):
        metric = escape(safe(r["metric"])) + (f" ({escape(safe(r['units']))})" if r["units"] else "")
        is_value = r["status"] in (STATUS_AVAILABLE, STATUS_REFERENCE_ONLY, STATUS_ENTERED)
        data.append([Paragraph(escape(safe(r["function"])), dim), Paragraph(metric, small),
                     r["code"], Paragraph(escape(safe(r["method"])), small),
                     Paragraph(escape(r["status"]), small if is_value else ital),
                     Paragraph(escape(safe(r["value"])), small),
                     Paragraph(escape(safe(r["source"])), small)])
    mt = Table(data, colWidths=[1.15 * inch, 1.35 * inch, 0.4 * inch, 1.95 * inch,
                                0.85 * inch, 0.6 * inch, 1.0 * inch], repeatRows=1)
    mt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e8ee")),
                            ("BACKGROUND", (0, 0), (-1, 0), head_bg),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [mt, Spacer(1, 8),
              Paragraph("F is measured in the field and D is answered from the desk. Available: "
                        "the value was pulled and is printed on the field worksheet. Unavailable: "
                        "no source answered, so measure or estimate it on site. Reference only: "
                        "the value is shown and kept out of the score, because the curve was "
                        "fitted on another data source. Not scored: the assessment withholds the "
                        "metric for insufficient reference support.", dim)]
    doc.build(story)
    return buf.getvalue()

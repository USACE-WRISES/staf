"""Report exports (shiny-free): CSV, GeoJSON, and PDF from an analysis result.

A ``result`` is the dict returned by ``pipeline.run_analysis`` (with ``report``
possibly replaced by a re-scored report from ``assessment.rescore``):
``result["delineation"]``, ``result["report"]``, ``result["watershed_geojson"]``,
``result["reach_geojson"]``.
"""
from __future__ import annotations

import csv
import io
import json

from . import config
from .scoring import function_score_band_color, index_band_color, index_band_label

RATING_COLOR = {"Good": "#c8d9f2", "Fair": "#f5e7a6", "Poor": "#f5b5b5"}
_DISCIPLINE_ORDER = ["Hydrology", "Hydraulics", "Geomorphology",
                     "Physicochemistry", "Biology"]


def _ordered_rows(rep: dict) -> list[dict]:
    rows = rep.get("metricRows", [])
    order = {d: i for i, d in enumerate(_DISCIPLINE_ORDER)}
    return sorted(rows, key=lambda r: (order.get(r["discipline"], 99), r["functionName"]))


def _summary_pairs(result: dict) -> list[tuple[str, str]]:
    d, rep = result["delineation"], result["report"]
    sub = rep["subIndices"]
    coverage = rep.get("coverage") or {}
    outcome_coverage = coverage.get("outcomes") or {}
    profile = coverage.get("evidenceProfile") or {}

    def shown(value):
        return "—" if value is None else value

    def pct(value):
        return "—" if value is None else f"{100 * float(value):.0f}%"

    pairs = [
        ("Stream", d.get("gnis_name", "")),
        ("COMID", d.get("comid", "")),
        ("HUC12", d.get("huc12") or ""),
        ("Drainage area (km2)", d.get("drainage_area_sqkm", "")),
        ("Reach length (ft)", d.get("reach_length_ft", "")),
        ("Snapped lat", d.get("snapped_lat", "")),
        ("Snapped lon", d.get("snapped_lon", "")),
        ("Ecosystem Condition Index", shown(rep.get("ecosystemConditionIndex"))),
        ("Physical sub-index", shown(sub.get("physical"))),
        ("Chemical sub-index", shown(sub.get("chemical"))),
        ("Biological sub-index", shown(sub.get("biological"))),
        ("Metrics rated", f"{rep.get('computedCount')}/{rep.get('selectedCount', rep.get('totalCount'))}"),
        ("Overall coverage", pct((coverage.get("overall") or {}).get("fraction"))),
        ("Physical coverage", pct((outcome_coverage.get("physical") or {}).get("fraction"))),
        ("Chemical coverage", pct((outcome_coverage.get("chemical") or {}).get("fraction"))),
        ("Biological coverage", pct((outcome_coverage.get("biological") or {}).get("fraction"))),
        ("Coverage status", coverage.get("statusMessage") or
         ("Provisional (limited coverage)" if rep.get("provisionalCoverage")
          else "Sufficient coverage")),
        ("Observed evidence metrics", profile.get("observed", 0)),
        ("Connected/nearby evidence metrics", profile.get("connectedNearby", 0)),
        ("Published-model metrics", profile.get("publishedModel", 0)),
        ("Screening-proxy metrics", profile.get("screeningProxy", 0)),
        ("Manual metrics", profile.get("manual", 0)),
        ("Unavailable metrics", profile.get("unavailable", 0)),
        ("Overrides applied", ", ".join(rep.get("overridesApplied", [])) or "none"),
    ]
    # Substitution provenance rows appear ONLY for routed (hrSurrogate) sites so
    # covered-network exports stay byte-identical to their historical form.
    anchor = result.get("siteAnchor") or {}
    if anchor.get("anchorKind") == "hrSurrogate":
        clicked = anchor.get("clickedStream") or {}
        routing_info = anchor.get("routing") or {}
        pairs[7:7] = [
            ("Scored at surrogate reach", "yes"),
            ("Clicked stream (NHDPlus HR)",
             clicked.get("gnisName") or "(unnamed stream)"),
            ("Clicked stream NHDPlusID", clicked.get("nhdplusId", "")),
            ("Clicked stream drainage area (km2)",
             shown(clicked.get("drainageAreaSqkm"))),
            ("Routed distance (ft)", shown(routing_info.get("routedDistanceFt"))),
            ("Drainage area ratio", shown(routing_info.get("daRatio"))),
            ("Drainage area ratio limit", shown(routing_info.get("daRatioLimit"))),
            ("Routing method", routing_info.get("method") or ""),
        ]
    return pairs + [(label, val)
                    for label, val in (rep.get("basin") or {}).get("rows", [])]


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def build_csv(result: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["EASI Screening Report"])
    for k, v in _summary_pairs(result):
        w.writerow([k, v])
    w.writerow([])
    rows = _ordered_rows(result["report"])
    has_notes = any(r.get("userNote") for r in rows)   # only add the column if used
    # The per-metric anchor column appears ONLY for routed sites so covered
    # exports stay byte-identical.
    has_anchor = ((result.get("siteAnchor") or {}).get("anchorKind")
                  == "hrSurrogate")
    header = ["Discipline", "Function", "Metric", "Value", "Rating", "Index",
              "FunctionScore", "Confidence", "Source", "Status", "MethodKey",
              "MethodKind", "BasisClass", "InputTrace", "CombinedValue",
              "GoverningInput", "GeneratedRating", "GeneratedIndex", "Completeness",
              "SourceTier", "EvidenceFamily", "UsedFallback",
              "ObservedOverridesProxy", "RetainedProxyRating",
              "OverallCoverage", "PhysicalCoverage", "ChemicalCoverage",
              "BiologicalCoverage", "ProvisionalCoverage"]
    if has_anchor:
        header.append("Anchor")
    if has_notes:
        header.append("Notes")
    w.writerow(header)
    rep = result["report"]
    coverage = rep.get("coverage") or {}
    outcome_coverage = coverage.get("outcomes") or {}
    for r in rows:
        trace = r.get("scoring") or {}
        row = [r["discipline"], r["functionName"], r["name"], r["valueText"],
               r["rating"] or "", r["index"] if r["index"] is not None else "",
               r["functionScore"] if r["functionScore"] is not None else "",
               r["confidence"] or "", r["source"] or "", r["status"],
               r.get("methodKey", ""), r.get("methodKind", ""),
               r.get("basisClass", ""),
               json.dumps(trace.get("inputs") or [], separators=(",", ":"), default=str),
               trace.get("combinedValue"), trace.get("governingInput"),
               r.get("generatedRating"), trace.get("generatedIndex"),
               trace.get("completeness") or r.get("completeness", ""),
               trace.get("sourceTier") or r.get("sourceTier"),
               trace.get("evidenceFamily") or r.get("evidenceFamily"),
               bool(trace.get("usedFallback", r.get("usedFallback", False))),
               bool(trace.get("observedOverridesProxy",
                              r.get("observedOverridesProxy", False))),
               (r.get("proxyResult") or {}).get("rating"),
               (coverage.get("overall") or {}).get("fraction"),
               (outcome_coverage.get("physical") or {}).get("fraction"),
               (outcome_coverage.get("chemical") or {}).get("fraction"),
               (outcome_coverage.get("biological") or {}).get("fraction"),
               bool(rep.get("provisionalCoverage"))]
        if has_anchor:
            row.append(r.get("anchorLabel", ""))
        if has_notes:
            row.append(r.get("userNote", ""))
        w.writerow(row)
    return buf.getvalue().encode("utf-8")


# --------------------------------------------------------------------------- #
# GeoJSON
# --------------------------------------------------------------------------- #
def build_geojson(result: dict) -> str:
    d, rep = result["delineation"], result["report"]
    features: list[dict] = []
    summary = {
        "gnis_name": d.get("gnis_name"), "comid": d.get("comid"),
        "huc12": d.get("huc12"), "drainage_area_sqkm": d.get("drainage_area_sqkm"),
        "ecosystem_condition_index": rep.get("ecosystemConditionIndex"),
        "sub_indices": rep.get("subIndices"),
        "metrics_computed": (
            f"{rep.get('computedCount')}/{rep.get('selectedCount', rep.get('totalCount'))}"),
        "coverage": rep.get("coverage"),
        "evidence_profile": rep.get("evidenceProfile"),
        "complete_screening_coverage": bool(rep.get("completeScreeningCoverage")),
        "proxy_derived_ratings": bool(rep.get("proxyDerivedRatings")),
        "correlation_notes": rep.get("correlationNotes") or [],
        "provisional_coverage": bool(rep.get("provisionalCoverage")),
        "basin_characteristics": {lbl: val for lbl, val in (rep.get("basin") or {}).get("rows", [])},
    }
    # Only routed sites carry the anchor block, so covered-network GeoJSON stays
    # byte-identical to its historical form.
    anchor = result.get("siteAnchor") or {}
    if anchor.get("anchorKind") == "hrSurrogate":
        summary["site_anchor"] = anchor

    def _add(fc, props):
        for f in (fc or {}).get("features", []):
            features.append({"type": "Feature", "geometry": f.get("geometry"),
                             "properties": props})

    _add(result.get("watershed_geojson"), {"type": "watershed",
         "area_sqkm": d.get("watershed_area_sqkm"), **summary})
    _add(result.get("reach_geojson"), {"type": "reach",
         "length_ft": d.get("reach_length_ft"), **summary})
    if d.get("snapped_lat") is not None:
        features.append({"type": "Feature", "properties": {"type": "point", **summary},
                         "geometry": {"type": "Point",
                                      "coordinates": [d["snapped_lon"], d["snapped_lat"]]}})
    # per-metric ratings travel with the point/reach as a compact attribute table
    has_anchor = anchor.get("anchorKind") == "hrSurrogate"
    metrics = {}
    for r in rep.get("metricRows", []):
        trace = r.get("scoring") or {}
        m = {"rating": r["rating"], "value": r["valueText"],
             "confidence": r["confidence"], "source": r["source"],
             "method_key": r.get("methodKey"),
             "method_kind": r.get("methodKind"),
             "basis_class": r.get("basisClass"),
             "input_trace": trace.get("inputs") or [],
             "combined_value": trace.get("combinedValue"),
             "governing_input": trace.get("governingInput"),
             "generated_rating": r.get("generatedRating"),
             "generated_index": trace.get("generatedIndex"),
             "completeness": trace.get("completeness") or r.get("completeness"),
             "source_tier": trace.get("sourceTier") or r.get("sourceTier"),
             "evidence_family": trace.get("evidenceFamily") or r.get("evidenceFamily"),
             "used_fallback": bool(trace.get("usedFallback", r.get("usedFallback", False))),
             "observed_overrides_proxy": bool(
                 trace.get("observedOverridesProxy",
                           r.get("observedOverridesProxy", False))),
             "retained_proxy": r.get("proxyResult")}
        if has_anchor:
            m["anchor"] = r.get("anchorLabel")
        if r.get("userNote"):
            m["note"] = r["userNote"]
        metrics[r["metricId"]] = m
    for f in features:
        if f["properties"].get("type") in ("reach", "point"):
            f["properties"]["metrics"] = metrics
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _summary_plots_png(rep: dict) -> bytes:
    """Two-panel summary figure: all 20 function scores grouped by STAF category
    (left, 0–15) and the condition indices with the Ecosystem index as parent of its
    three sub-indices (right, 0–1). Every bar is colored by its Functioning /
    Functioning-at-Risk / Non-Functioning band, matching the on-screen report."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fscores = rep.get("functionScores") or {}
    sub = rep["subIndices"]
    eci = rep.get("ecosystemConditionIndex")

    # left panel: a header row per category, then its four function bars
    rows = []  # (kind, label, value, color)
    groups: dict[str, list] = {}
    for fn in config.functions():
        groups.setdefault(fn["category"], []).append(fn)
    for cat, fns in groups.items():
        rows.append(("hdr", cat, None, None))
        for fn in fns:
            s = fscores.get(fn["id"])
            rows.append(("bar", fn["name"], s,
                         function_score_band_color(s) if s is not None else "#d7dce5"))

    fig, (axL, axR) = plt.subplots(
        1, 2, figsize=(7.2, 5.6), gridspec_kw={"width_ratios": [1.5, 1]})

    plot_rows = rows[::-1]                      # ascending y → first category on top
    labels, weights = [], []
    for y, (kind, text, val, color) in enumerate(plot_rows):
        if kind == "hdr":
            labels.append(text); weights.append("bold")
        else:
            if val is None:
                axL.barh(y, 15, color="#eef0f4", edgecolor="#c7ccd5",
                         height=0.66, zorder=2)
                axL.text(0.35, y, "—", va="center", fontsize=8, color="#697386")
            else:
                axL.barh(y, val, color=color, edgecolor="#888", height=0.66, zorder=3)
                axL.text(min(val + 0.3, 14.4), y, f"{val:.0f}", va="center", fontsize=7)
            labels.append("   " + text); weights.append("normal")
    axL.set_yticks(range(len(plot_rows)))
    axL.set_yticklabels(labels, fontsize=7.2)
    for tick, w in zip(axL.get_yticklabels(), weights):
        tick.set_fontweight(w)
        if w == "bold":
            tick.set_color("#22304d")
    axL.set_ylim(-0.6, len(plot_rows) - 0.4)
    axL.set_xlim(0, 15)
    axL.set_xlabel("Function score (0-15)", fontsize=8)
    axL.set_title("Function scores", fontsize=9.5, fontweight="bold")
    axL.grid(axis="x", color="#eef0f4", lw=0.6)
    axL.set_axisbelow(True)
    for sp in ("top", "right"):
        axL.spines[sp].set_visible(False)

    # right panel: ECI parent on top, the three sub-indices indented beneath it
    ci = [("Biological", sub.get("biological")), ("Chemical", sub.get("chemical")),
          ("Physical", sub.get("physical")), ("Ecosystem Condition Index", eci)]
    for y, (lab, val) in enumerate(ci):
        if val is None:
            axR.barh(y, 1, color="#eef0f4", edgecolor="#c7ccd5", height=0.6, zorder=2)
            axR.text(0.03, y, "—", va="center", fontsize=8, color="#697386")
        else:
            axR.barh(y, val, color=index_band_color(val), edgecolor="#888",
                     height=0.6, zorder=3)
            axR.text(min(val + 0.02, 0.92), y, f"{val:.2f}", va="center", fontsize=8)
    axR.set_yticks(range(4))
    axR.set_yticklabels(["   Biological", "   Chemical", "   Physical",
                         "Ecosystem Condition Index"], fontsize=8)
    axR.get_yticklabels()[3].set_fontweight("bold")
    axR.set_ylim(-0.6, 3.6)
    axR.set_xlim(0, 1)
    axR.set_xlabel("Condition index (0-1)", fontsize=8)
    axR.set_title("Condition indices", fontsize=9.5, fontweight="bold")
    axR.grid(axis="x", color="#eef0f4", lw=0.6)
    axR.set_axisbelow(True)
    for sp in ("top", "right"):
        axR.spines[sp].set_visible(False)

    fig.tight_layout(w_pad=2.0)
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=150)
    plt.close(fig)
    return out.getvalue()


def build_pdf(result: dict) -> bytes:
    from reportlab.lib import colors as rc
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    d, rep = result["delineation"], result["report"]
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(f"EASI Screening Report: {d.get('gnis_name','')}",
                           styles["Title"]))
    lat, lon = d.get("snapped_lat"), d.get("snapped_lon")
    pt = f"{lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "—"
    meta = f"Analysis point {pt} · Reach {d.get('reach_length_ft')} ft upstream"
    story.append(Paragraph(meta, styles["Normal"]))
    story.append(Spacer(1, 8))
    anchor = result.get("siteAnchor") or {}
    if anchor.get("anchorKind") == "hrSurrogate":
        clicked = anchor.get("clickedStream") or {}
        routing_info = anchor.get("routing") or {}
        clicked_name = clicked.get("gnisName") or "an unnamed stream"
        dist = routing_info.get("routedDistanceFt")
        dist_txt = (f"{dist:,.0f} ft downstream" if dist is not None
                    else "downstream")
        ratio = routing_info.get("daRatio")
        ratio_txt = f"{ratio}" if ratio is not None else "unknown"
        story.append(Paragraph(
            f"<b>Scored at a surrogate reach.</b> The clicked stream "
            f"({clicked_name}, NHDPlus HR) is not on the scoring network. "
            f"Results describe {d.get('gnis_name') or 'the nearest covered reach'} "
            f"{dist_txt} on the covered network. Drainage area ratio {ratio_txt} "
            f"(limit {routing_info.get('daRatioLimit')}).", styles["Normal"]))
        story.append(Spacer(1, 6))
    if rep.get("provisionalCoverage"):
        story.append(Paragraph(
            "<b>Provisional (limited coverage).</b> One or more included coverage "
            "measures are below 70%. Missing domains are shown as a gray dash, not zero.",
            styles["Normal"]))
        story.append(Spacer(1, 6))
    elif (rep.get("coverage") or {}).get("completeWithProxies"):
        story.append(Paragraph(
            "<b>Complete screening coverage (includes proxy-derived ratings).</b> "
            "Use the evidence profile and per-metric source tier to distinguish "
            "observations, models, and screening proxies.", styles["Normal"]))
        story.append(Spacer(1, 6))
    evidence_profile = (rep.get("coverage") or {}).get("evidenceProfile") or {}
    if evidence_profile:
        story.append(Paragraph(
            "Evidence profile: "
            + " · ".join([
                f"observed {evidence_profile.get('observed', 0)}",
                f"connected/nearby {evidence_profile.get('connectedNearby', 0)}",
                f"published model {evidence_profile.get('publishedModel', 0)}",
                f"screening proxy {evidence_profile.get('screeningProxy', 0)}",
                f"manual {evidence_profile.get('manual', 0)}",
                f"unavailable {evidence_profile.get('unavailable', 0)}",
            ]), styles["Normal"]))
        story.append(Spacer(1, 4))
    for item in rep.get("correlationNotes") or []:
        story.append(Paragraph(
            f"<b>Shared evidence:</b> {item.get('text', '')}", styles["Normal"]))
        story.append(Spacer(1, 3))

    # COMID/HUC12 moved out of the meta line into the basin table (the data exports still
    # carry them separately); drainage area is already a basin row.
    basin_rows = [["COMID", d.get("comid")], ["HUC12", d.get("huc12") or "—"]] + \
        list((rep.get("basin") or {}).get("rows") or [])
    if basin_rows:
        story.append(Paragraph("Basin characteristics", styles["Heading4"]))
        btbl = Table([[Paragraph(f"<b>{lbl}</b>", styles["BodyText"]),
                       Paragraph(str(val), styles["BodyText"])] for lbl, val in basin_rows],
                     colWidths=[2.4 * inch, 4.0 * inch])
        btbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.25, rc.HexColor("#e5e8ee")),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [rc.white, rc.HexColor("#fafbfd")])]))
        story.append(btbl)
        story.append(Spacer(1, 8))

    story.append(Image(io.BytesIO(_summary_plots_png(rep)),
                       width=7.0 * inch, height=5.44 * inch))
    story.append(Paragraph(
        "Bars colored by condition band. Blue: Functioning · yellow: "
        "Functioning-at-Risk · red: Non-Functioning.", styles["Italic"]))
    story.append(Spacer(1, 8))

    xs = rep.get("crossSection") or {}
    if xs.get("png_b64"):
        import base64
        story.append(Paragraph("Representative cross-section", styles["Heading4"]))
        story.append(Image(io.BytesIO(base64.b64decode(xs["png_b64"])),
                           width=6.0 * inch, height=2.4 * inch))
        geom = xs.get("geom") or {}
        thal = geom.get("thalweg")
        ft = 3.28084  # metres -> feet for the report

        def _w(m):
            return f"{m * ft:.1f} ft" if m is not None else "n/a"

        def _h(stage):
            return (f"{(stage - thal) * ft:.2f} ft"
                    if stage is not None and thal is not None else "n/a")

        er, bhr = xs.get("entrenchment_ratio"), xs.get("bank_height_ratio")
        bka = geom.get("bankfull_area_m2")
        summary = (f"Bieger region: {geom.get('division') or 'National curve'} &middot; "
                   f"Bieger XS area: {bka:.1f} m² &middot; " if bka is not None else "")
        summary += (f"Bankfull width: {_w(geom.get('bankfull_width_m'))} &middot; "
                    f"Floodprone width: {_w(geom.get('flood_prone_width_m'))} &middot; "
                    f"Entrenchment ratio: {er if er is not None else 'n/a'} &middot; "
                    f"Bank-height ratio: {bhr if bhr is not None else 'n/a'} &middot; "
                    f"Bankfull height: {_h(geom.get('bankfull_stage'))} &middot; "
                    f"Low bank height: {_h(geom.get('floodplain_stage'))}")
        story.append(Paragraph(summary, styles["Normal"]))
        story.append(Spacer(1, 8))

    metric_rows = _ordered_rows(rep)
    has_notes = any(r.get("userNote") for r in metric_rows)   # add a Notes column only if used
    head = ["Function", "Metric", "Value", "Rating", "Idx", "Fn", "Conf"]
    if has_notes:
        head.append("Notes")
    data = [head]
    rating_bg = []
    for i, r in enumerate(metric_rows, start=1):
        row = [
            Paragraph(r["functionName"], styles["BodyText"]),
            Paragraph(r["name"], styles["BodyText"]),
            Paragraph(r["valueText"], styles["BodyText"]),
            r["rating"] or "—",
            "" if r["index"] is None else f'{r["index"]:.2f}',
            "" if r["functionScore"] is None else str(r["functionScore"]),
            r["confidence"] or "",
        ]
        if has_notes:
            row.append(Paragraph(r.get("userNote", ""), styles["BodyText"]))
        data.append(row)
        if r["rating"] in RATING_COLOR:
            rating_bg.append((i, rc.HexColor(RATING_COLOR[r["rating"]])))
    col_widths = ([1.3*inch, 1.5*inch, 1.9*inch, 0.5*inch, 0.35*inch, 0.3*inch, 0.4*inch]
                  if not has_notes else
                  [1.1*inch, 1.25*inch, 1.35*inch, 0.5*inch, 0.35*inch, 0.3*inch, 0.4*inch, 1.25*inch])
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), rc.HexColor("#2f4b7c")),
             ("TEXTCOLOR", (0, 0), (-1, 0), rc.white),
             ("FONTSIZE", (0, 0), (-1, -1), 7.5),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.25, rc.HexColor("#d7dce5"))]
    for row_i, color in rating_bg:
        style.append(("BACKGROUND", (3, row_i), (3, row_i), color))
    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Generated from national datasets. A desktop screening estimate with "
        "per-metric confidence, not a field-validated assessment.",
        styles["Italic"]))

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=letter, topMargin=0.6 * inch,
                      bottomMargin=0.6 * inch).build(story)
    return out.getvalue()

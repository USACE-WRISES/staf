"""The DEEP field worksheet, generated from the loaded assessment (reportlab only).

A detailed assessment's metrics differ by region and by version, so the
worksheet is built on the fly from whatever bundle is loaded and is always in
step with it. The page follows the SFARI field form an assessor already knows
(``apps/sfari/data/FieldForm/SFARI_Field_Form_v1.0.docx``): US Letter, half-inch
margins, Times 9 point, grey (D9D9D9) bands, a thin black grid, one block per
function with the function statement on the left, its metrics in the middle, a
"Score:" and "Notes/Other Metrics:" row under them, and a band per discipline.

What differs is what a detailed assessment records: a measured VALUE per metric
(with its units) and the 0 to 1 INDEX the reference curve gives it, instead of
an agreement rating. Each metric row says whether it is measured in the Field
(F) or answered from the Desk (D), how to measure it, and, for a metric with
several curve sets, which set applies. Values the desk already answered are
printed with their source, and metrics the assessment withholds for
insufficient reference support are listed at the end so the crew knows they
were considered.

Pure: a bundle, the measured-value state and the delineation in, PDF bytes out.
``invariant=1`` keeps the bytes identical for identical inputs.
"""
from __future__ import annotations

import io
from typing import Optional
from xml.sax.saxutils import escape

from . import config, reference_support

TITLE = "Detailed Evaluation of Ecosystem Processes (DEEP) Field Worksheet"
BAND_FILL = "#D9D9D9"
PAGE_MARGIN_IN = 0.5
# SFARI's grid is 2180 / 7964 / 646 twips. The middle column is split here into
# the metric text and the two write-in cells; the outer edges stay where they were.
COL_FUNCTION_IN = 2180 / 1440.0
COL_VALUE_IN = 1.05
COL_INDEX_IN = 0.55
COL_METRIC_IN = (2180 + 7964 + 646) / 1440.0 - COL_FUNCTION_IN - COL_VALUE_IN - COL_INDEX_IN

# Code points, never literal characters, so this file stays plain ASCII: the
# typographic dashes, quotes and symbols the built-in Times font cannot draw.
_PRINT_SAFE = {
    0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-", 0x2212: "-",
    0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2026: "...",
    0x00A0: " ", 0x2264: "<=", 0x2265: ">=", 0x00B2: "2", 0x00B3: "3",
    0x00B5: "u", 0x03BC: "u", 0x00D7: "x", 0x00B7: "-",
}

INSTRUCTIONS = (
    "Record the measured value of each metric in its units. Metrics measured in the field are "
    "marked F and metrics answered from the desk are marked D. A value printed in the Value "
    "cell was already answered from the desk and carries its source. Enter the values in DEEP, "
    "which reads each one on the metric's reference curve to give an index from 0 to 1. The "
    "function score is the mean index of the function's metrics times 15 and reads Functioning "
    "(15 to 11), Functioning At-Risk (10 to 6), or Non-Functioning (5 to 0). A metric that does "
    "not apply is marked NA and drops out of the score. Where a metric lists curve sets, mark "
    "the set that fits the reach.")


def to_print_safe(text) -> str:
    """Text the built-in Times font can draw: typographic dashes, quotes and a
    few symbols folded to plain characters, anything else outside Latin-1 to
    a question mark."""
    out = str(text or "").translate(_PRINT_SAFE)
    return out.encode("latin-1", "replace").decode("latin-1")


def _esc(text) -> str:
    return escape(to_print_safe(text))


def _attr(assessment, obj_attr, dict_key, default=""):
    val = getattr(assessment, obj_attr, None)
    if val:
        return val
    if isinstance(assessment, dict):
        return assessment.get(dict_key, default)
    return default


def _mbf(assessment) -> list[dict]:
    mbf = getattr(assessment, "metrics_by_function", None)
    if mbf is not None:
        return mbf
    return (assessment or {}).get("metricsByFunction", []) if isinstance(assessment, dict) else []


def _raw(assessment) -> dict:
    raw = getattr(assessment, "raw", None)
    if isinstance(raw, dict) and raw:
        return raw
    return assessment if isinstance(assessment, dict) else {}


def _first_sentences(text: str, limit: int = 320) -> str:
    """The opening of a method note, cut at a sentence end near ``limit``."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    return text[:cut + 1] if cut > 60 else text[:limit].rstrip() + "..."


def measure_code(metric: dict, desktop_ids: Optional[set] = None) -> str:
    """``D`` for a metric the desk can answer, ``F`` for one measured in the field."""
    desktop_ids = desktop_ids if desktop_ids is not None else _desktop_ids()
    if metric.get("metricId") in desktop_ids:
        return "D"
    kind = str(metric.get("inputType") or "").lower()
    return "D" if "desktop" in kind or "gis" in kind else "F"


def _desktop_ids() -> set:
    try:
        from .metrics import computed
        return computed.computable_ids()
    except Exception:  # noqa: BLE001 - the form still prints, every metric reads F
        return set()


def method_text(metric: dict) -> str:
    """How to measure the metric: the assessment's protocol text when it has
    one, else its measurement note, else nothing."""
    name = str(metric.get("metricName") or "")
    for key in ("methodContext", "howToMeasure", "metricStatement"):
        txt = " ".join(str(metric.get(key) or "").split())
        if txt and txt != name:
            return _first_sentences(txt)
    return ""


def curve_set_text(metric: dict) -> str:
    """``Curve set: [ ] All streams (pooled)  [ ] Steep ...`` for a metric with
    several curve layers, else ``""``."""
    layers = metric.get("curveLayers") or []
    if len(layers) < 2:
        return ""
    labels = [reference_support.stratum_label(layer.get("stratum"), metric) for layer in layers]
    return "Curve set: " + "   ".join(f"[  ] {lab}" for lab in labels)


def units_of(metric: dict) -> str:
    """The units from the curve's x label (``Embeddedness (%)`` gives ``%``)."""
    label = str(metric.get("xLabel") or "")
    if label.endswith(")") and "(" in label:
        return label[label.rfind("(") + 1:-1].strip()
    return ""


def _reach_id(dl: dict) -> str:
    if dl.get("network") == "nhdplus-hr" and dl.get("nhdplus_id") not in (None, "", "None"):
        return f"NHDPlusID {dl['nhdplus_id']}"
    if dl.get("comid") not in (None, "", "None"):
        return f"COMID {dl['comid']}"
    return ""


def _coords(dl: dict) -> str:
    lat, lon = dl.get("snapped_lat"), dl.get("snapped_lon")
    try:
        return f"{float(lat):.5f}, {float(lon):.5f}"
    except (TypeError, ValueError):
        return ""


def functions_in_order(assessment) -> list[tuple[str, list[dict]]]:
    """``[(discipline, [function block, ...]), ...]`` in the framework's order,
    withheld-only functions included (they print with their withheld metrics)."""
    blocks = [dict(fn) for fn in _mbf(assessment) if fn.get("metrics")]
    have = {fn.get("functionId") for fn in blocks}
    for extra in reference_support.withheld_only_functions(assessment):
        if extra["functionId"] not in have:
            blocks.append({"functionId": extra["functionId"],
                           "functionName": extra["functionName"], "metrics": []})
    try:
        by_id = config.functions_by_id()
    except Exception:  # noqa: BLE001
        by_id = {}
    for fn in blocks:
        ref = by_id.get(fn.get("functionId")) or {}
        fn.setdefault("discipline", ref.get("category") or "")
        if not fn.get("discipline"):
            fn["discipline"] = ref.get("category") or ""
        fn["_order"] = ref.get("order", 999)
        fn["_statement"] = ref.get("function_statement") or ""
    blocks.sort(key=lambda fn: fn["_order"])
    out: list[tuple[str, list[dict]]] = []
    for cat in list(config.CATEGORY_ORDER) + sorted(
            {fn["discipline"] for fn in blocks} - set(config.CATEGORY_ORDER)):
        members = [fn for fn in blocks if fn["discipline"] == cat]
        if members:
            out.append((cat, members))
    return out


def build_field_form_pdf(assessment, ref: str = "", *, measured=None, delineation=None,
                         desktop_entries: Optional[dict] = None) -> bytes:
    """The field worksheet for ``assessment``.

    ``desktop_entries``: ``{metricId: (value text, note text)}`` for the values
    the desk already answered (``report._desktop_entries``), printed in the
    Value cell with the note under the metric. ``measured`` is accepted for the
    callers' symmetry and not read here.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    body = ParagraphStyle("ff_body", fontName="Times-Roman", fontSize=9, leading=10.3)
    bold = ParagraphStyle("ff_bold", parent=body, fontName="Times-Bold")
    small = ParagraphStyle("ff_small", parent=body, fontSize=8, leading=9.2)
    note = ParagraphStyle("ff_note", parent=body, fontSize=7.5, leading=8.6,
                          fontName="Times-Italic")
    title = ParagraphStyle("ff_title", parent=bold, fontSize=11, leading=13)
    center = ParagraphStyle("ff_center", parent=bold, alignment=TA_CENTER)
    band_fill = colors.HexColor(BAND_FILL)
    line = 0.5
    widths = [COL_FUNCTION_IN * inch, COL_METRIC_IN * inch, COL_VALUE_IN * inch,
              COL_INDEX_IN * inch]
    full = sum(widths)
    desktop_entries = desktop_entries or {}
    desktop_ids = _desktop_ids()

    def band(text: str) -> Table:
        t = Table([[Paragraph(_esc(text), bold)]], colWidths=[full])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), band_fill),
                               ("BOX", (0, 0), (-1, -1), line, colors.black),
                               ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        return t

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.drawCentredString(letter[0] / 2.0, letter[1] - 0.32 * inch, str(doc.page))
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="DEEP Field Worksheet",
                            author="STAF DEEP", invariant=1,
                            topMargin=PAGE_MARGIN_IN * inch, bottomMargin=PAGE_MARGIN_IN * inch,
                            leftMargin=PAGE_MARGIN_IN * inch, rightMargin=PAGE_MARGIN_IN * inch)

    name = _attr(assessment, "assessment_name", "assessmentName") or "Detailed assessment"
    raw = _raw(assessment)
    version = (raw.get("library") or {}).get("version")
    status = str(raw.get("lifecycle") or (raw.get("library") or {}).get("status") or "").strip()
    about = [_esc(name)]
    if ref:
        about.append(_esc(ref))
    elif version is not None:
        about.append(f"version {version}")
    if status:
        about.append(_esc(status.replace("_", " ").title()))
    cite = _attr(assessment, "source_citation", "sourceCitation")
    story = [Paragraph(_esc(TITLE), title),
             Paragraph("  |  ".join(about), body)]
    if cite:
        story.append(Paragraph(_esc(cite), small))

    dl = (delineation or {}).get("delineation") or {}
    reach_len = dl.get("reach_length_ft")
    try:
        reach_len_txt = f"{float(reach_len):,.0f} ft" if reach_len not in (None, "") else ""
    except (TypeError, ValueError):
        reach_len_txt = ""

    def blank(label: str, value: str, width_chars: int) -> str:
        fill = _esc(value) if value else "_" * width_chars
        return f"<b>{label}</b> {fill}"
    story += [Spacer(1, 3), Paragraph("   ".join([
        blank("Reach ID:", _reach_id(dl), 13), blank("Reach Length:", reach_len_txt, 8),
        blank("Date:", "", 10), blank("Assessor(s):", "", 15),
        blank("Coordinates:", _coords(dl), 13)]), body)]      # sized to stay on one line at 9 pt
    if dl:
        from . import report as _report     # the shared basis wording
        stream = dl.get("gnis_name") or "(unnamed reach)"
        area = dl.get("drainage_area_sqkm")
        site = [f"Site: {_esc(stream)}"]
        if area not in (None, ""):
            site.append(f"drainage area {_esc(area)} km2")
        site.append(_esc(_report.watershed_basis_label(delineation)))
        story.append(Paragraph("  |  ".join(site), small))

    story += [Spacer(1, 4), band("RECORDING INSTRUCTIONS"),
              Table([[Paragraph(_esc(INSTRUCTIONS), body)]], colWidths=[full],
                    style=TableStyle([("BOX", (0, 0), (-1, -1), line, colors.black),
                                      ("TOPPADDING", (0, 0), (-1, -1), 2),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])),
              Table([[Paragraph("Function", bold), Paragraph("Metrics", bold),
                      Paragraph("Value", center), Paragraph("Index", center)]],
                    colWidths=widths,
                    style=TableStyle([("GRID", (0, 0), (-1, -1), line, colors.black),
                                      ("BACKGROUND", (0, 0), (-1, -1), band_fill),
                                      ("TOPPADDING", (0, 0), (-1, -1), 2),
                                      ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))]

    groups = functions_in_order(assessment) if assessment is not None else []
    for category, functions in groups:
        # the discipline band travels with its first function block, so it is
        # never left alone at the foot of a page
        pending_band = band(f"{str(category).upper()} FUNCTIONS")
        for fn in functions:
            fid = fn.get("functionId")
            left = f"<b>{_esc(fn.get('functionName') or fid)}:</b>"
            if fn.get("_statement"):
                left += f"<br/>{_esc(fn['_statement'])}"
            rows = []
            for m in fn.get("metrics", []):
                units = units_of(m)
                head = f"<b>{_esc(m.get('metricName') or m.get('metricId'))}</b>"
                if units:
                    head += f" ({_esc(units)})"
                head += f" {measure_code(m, desktop_ids)}"
                method = method_text(m)
                text = head + (f": {_esc(method)}" if method else "")
                parts = [Paragraph(text, body)]
                curve_sets = curve_set_text(m)
                if curve_sets:
                    parts.append(Paragraph(_esc(curve_sets), small))
                val_txt, note_txt = desktop_entries.get(m.get("metricId"), ("", ""))
                if note_txt:
                    parts.append(Paragraph(_esc(note_txt), note))
                rows.append([parts, Paragraph(_esc(val_txt), center) if val_txt else "", ""])
            for w in reference_support.withheld_for_function(assessment, fid):
                rows.append([[Paragraph(f"<b>{_esc(w.get('metricName') or w.get('metricId'))}"
                                        "</b> not scored", body),
                              Paragraph("Insufficient reference support. No value is needed.",
                                        note)], Paragraph("NA", center), ""])
            if not rows:
                rows.append([[Paragraph("No metric is scored for this function.", note)], "", ""])

            data = [[Paragraph(left, body), rows[0][0], rows[0][1], rows[0][2]]]
            for r in rows[1:]:
                data.append(["", r[0], r[1], r[2]])
            data.append([Paragraph("<b>Score:</b>", body),
                         Paragraph("<b>Notes/Other Metrics:</b>", body), "", ""])
            n = len(rows)
            style = [("GRID", (0, 0), (-1, -1), line, colors.black),
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("SPAN", (0, 0), (0, n - 1)),
                     ("SPAN", (1, n), (3, n)),
                     ("TOPPADDING", (0, 0), (-1, -1), 2),
                     ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                     ("LEFTPADDING", (0, 0), (-1, -1), 4),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
            block = Table(data, colWidths=widths,
                          rowHeights=[None] * n + [0.42 * inch], style=TableStyle(style))
            story.append(KeepTogether([pending_band, block] if pending_band is not None
                                      else [block]))
            pending_band = None

    if not groups:
        story += [Spacer(1, 8), Paragraph("This assessment has no metrics defined.", body)]

    withheld = reference_support.withheld(assessment) if assessment is not None else []
    if withheld:
        names = ", ".join(sorted(str(w.get("metricName") or w.get("metricId"))
                                 for w in withheld))
        story += [Spacer(1, 6), band("METRICS WITHHELD FOR INSUFFICIENT REFERENCE SUPPORT"),
                  Table([[Paragraph(
                      _esc(f"{names}. These metrics were considered for this assessment and are "
                           "not scored, because too few comparable least-disturbed stations "
                           "carry them in this ecoregion or in its parent ecoregions. No "
                           "measurement is needed for the score. A value may still be recorded "
                           "under Notes."), body)]], colWidths=[full],
                        style=TableStyle([("BOX", (0, 0), (-1, -1), line, colors.black)]))]

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return buf.getvalue()

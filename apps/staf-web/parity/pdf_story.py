"""A platypus story described as JSON, laid out by reportlab itself (tests/pdf.test.ts lays out the same
story with the static app's port and compares the files whole).

    spec = {"doc": {...}, "styles": [...], "story": [...]}

- ``doc``: pagesize, title, author, margins (top, bottom, left, right), pageNumbers (a centered number
  at the top of each page, as DEEP's field form draws it).
- ``styles``: ``{"name", "parent"?, "sample"?, ...ParagraphStyle attributes}``; ``"sample": "Heading3"``
  starts from getSampleStyleSheet()'s style of that name; a color is "#rrggbb".
- ``story`` items: ``{"P": [text, style]}``, ``{"S": [w, h]}``, ``{"K": [items]}`` (KeepTogether),
  ``{"T": [rows, opts]}`` with cells a string, null, a flowable item or a list of them; opts colWidths,
  rowHeights, repeatRows, hAlign and style (commands whose colors are "#rrggbb", "black" or "white").
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _color(v):
    if isinstance(v, str) and v.startswith("#"):
        return colors.HexColor(v)
    if v == "black":
        return colors.black
    if v == "white":
        return colors.white
    return v


def _styles(spec) -> dict:
    sample = getSampleStyleSheet()
    out = {}
    for s in spec["styles"]:
        kw = {k: (_color(v) if k.endswith("Color") else v) for k, v in s.items() if k not in ("name", "parent", "sample")}
        if "sample" in s:
            st = sample[s["sample"]]
            for k, v in kw.items():
                setattr(st, k, v)
            out[s["name"]] = st
        else:
            out[s["name"]] = ParagraphStyle(s["name"], parent=out.get(s.get("parent")), **kw)
    return out


def _flowable(item, styles):
    if "P" in item:
        text, style = item["P"]
        return Paragraph(text, styles[style])
    if "S" in item:
        return Spacer(*item["S"])
    if "K" in item:
        return KeepTogether([_flowable(i, styles) for i in item["K"]])
    if "T" in item:
        rows, opts = item["T"]
        data = [[_cell(c, styles) for c in row] for row in rows]
        cmds = [tuple([c[0], tuple(c[1]), tuple(c[2])] + [_color(v) for v in c[3:]]) for c in opts.get("style", [])]
        kw = {k: v for k, v in opts.items() if k in ("colWidths", "rowHeights", "repeatRows", "hAlign")}
        return Table(data, style=TableStyle(cmds), **kw)
    raise ValueError(item)


def _cell(c, styles):
    if c is None or isinstance(c, str):
        return c
    if isinstance(c, list):
        return [_flowable(i, styles) for i in c]
    return _flowable(c, styles)


def build(spec) -> bytes:
    styles = _styles(spec)
    d = spec["doc"]
    top, bottom, left, right = d["margins"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=tuple(d["pagesize"]), title=d.get("title"), author=d.get("author"), invariant=1,
                            topMargin=top, bottomMargin=bottom, leftMargin=left, rightMargin=right)

    def page_number(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.drawCentredString(d["pagesize"][0] / 2.0, d["pagesize"][1] - 0.32 * 72, str(doc_.page))
        canvas.restoreState()

    cb = page_number if d.get("pageNumbers") else (lambda c, dd: None)
    doc.build([_flowable(i, styles) for i in spec["story"]], onFirstPage=cb, onLaterPages=cb)
    return buf.getvalue()


def _block(name: str, statement: str, metrics: list[tuple[str, str, str]]) -> dict:
    """A function block as DEEP's field form draws it: the function spans its metric rows."""
    rows = []
    for i, (head, value, note) in enumerate(metrics):
        cell = [{"P": [head, "body"]}]
        if note:
            cell.append({"P": [note, "note"]})
        rows.append([{"P": [f"<b>{name}:</b><br/>{statement}", "body"]} if i == 0 else "", cell,
                     {"P": [value, "center"]} if value else "", ""])
    rows.append([{"P": ["<b>Score:</b>", "body"]}, {"P": ["<b>Notes/Other Metrics:</b>", "body"]}, "", ""])
    n = len(metrics)
    return {"T": [rows, {
        "colWidths": [109.0, 316.3, 75.6, 39.6], "rowHeights": [None] * n + [30.24],
        "style": [["GRID", [0, 0], [-1, -1], 0.5, "black"], ["VALIGN", [0, 0], [-1, -1], "TOP"], ["SPAN", [0, 0], [0, n - 1]],
                  ["SPAN", [1, n], [3, n]], ["TOPPADDING", [0, 0], [-1, -1], 2], ["BOTTOMPADDING", [0, 0], [-1, -1], 3],
                  ["LEFTPADDING", [0, 0], [-1, -1], 4], ["RIGHTPADDING", [0, 0], [-1, -1], 4]]}]}


def _band(text: str) -> dict:
    return {"T": [[[{"P": [text, "bold"]}]], {"colWidths": [540.5], "style": [
        ["BACKGROUND", [0, 0], [-1, -1], "#D9D9D9"], ["BOX", [0, 0], [-1, -1], 0.5, "black"],
        ["TOPPADDING", [0, 0], [-1, -1], 2], ["BOTTOMPADDING", [0, 0], [-1, -1], 2]]}]}


LOREM = ("Bankfull width is read where the active channel's scour line meets the floodplain; record it in feet to one decimal "
         "at three riffles &amp; average them. Where the banks are undercut, measure from the lip, not the toe, and note it. ")


def spec() -> dict:
    styles = [
        {"name": "body", "fontName": "Times-Roman", "fontSize": 9, "leading": 10.3},
        {"name": "bold", "parent": "body", "fontName": "Times-Bold"},
        {"name": "small", "parent": "body", "fontSize": 8, "leading": 9.2},
        {"name": "note", "parent": "body", "fontSize": 7.5, "leading": 8.6, "fontName": "Times-Italic"},
        {"name": "title", "parent": "bold", "fontSize": 11, "leading": 13},
        {"name": "center", "parent": "bold", "alignment": 1},
        {"name": "right", "parent": "body", "alignment": 2},
        {"name": "just", "parent": "body", "alignment": 4, "spaceBefore": 4, "spaceAfter": 2},
        {"name": "indent", "parent": "body", "alignment": 4, "firstLineIndent": 18, "spaceBefore": 6},
        {"name": "h3", "sample": "Heading3", "keepWithNext": True},
        {"name": "normal", "sample": "Normal"},
        {"name": "bodytext", "sample": "BodyText"},
    ]
    long_word = "Antidisestablishmentarianism" * 4
    story = [
        {"P": ["Detailed Evaluation of Ecosystem Processes (DEEP) Field Worksheet", "title"]},
        {"P": ["Mink Brook &amp; tributaries  |  NE Highlands L3 v3  |  Final", "body"]},
        {"P": ["Menichino, G. and others (2026). A curve-based detailed assessment for the Northeastern Highlands, "
               "built from the NRSA least-disturbed reference pool &lt;L3&gt; with L2 borrowing where the pool was thin.", "small"]},
        {"S": [1, 3]},
        {"P": ["<b>Reach ID:</b> NHDPlusID 10000100012345   <b>Reach Length:</b> 1,000 ft   <b>Date:</b> __________   "
               "<b>Assessor(s):</b> _______________   <b>Coordinates:</b> 43.68580, -72.23670", "body"]},
        {"P": ["Site: Mink Brook  |  drainage area 12.4 km2  |  STAF site engine (HR reach watershed)", "small"]},
        {"S": [1, 4]},
        _band("RECORDING INSTRUCTIONS"),
        {"T": [[[{"P": [LOREM * 3, "body"]}]], {"colWidths": [540.5], "style": [
            ["BOX", [0, 0], [-1, -1], 0.5, "black"], ["TOPPADDING", [0, 0], [-1, -1], 2], ["BOTTOMPADDING", [0, 0], [-1, -1], 3]]}]},
        {"T": [[[{"P": ["Function", "bold"]}, {"P": ["Metrics", "bold"]}, {"P": ["Value", "center"]}, {"P": ["Index", "center"]}]],
               {"colWidths": [109.0, 316.3, 75.6, 39.6], "style": [
                   ["GRID", [0, 0], [-1, -1], 0.5, "black"], ["BACKGROUND", [0, 0], [-1, -1], "#D9D9D9"],
                   ["TOPPADDING", [0, 0], [-1, -1], 2], ["BOTTOMPADDING", [0, 0], [-1, -1], 2]]}]},
    ]
    blocks = []
    for k in range(9):
        metrics = []
        for m in range(1 + (k * 2) % 5):
            head = f"<b>Metric {k}.{m} name</b> (%) {'F' if m % 2 else 'D'}: " + LOREM[: 40 + 37 * ((k + m) % 6)]
            value = "12.5 (desk)" if (k + m) % 3 == 0 else ""
            note = "From the STAF site engine, HR reach watershed &middot; 2026 run." if (k + m) % 4 == 1 else ""
            metrics.append((head, value, note))
        name = f"Function {k} &quot;Hydrology&quot;" if k % 3 else f"Function {k}"
        block = _block(name, "Statement of what the function does, " * (1 + k % 3), metrics)
        blocks.append({"K": ([_band(f"DISCIPLINE {k // 3} FUNCTIONS")] if k % 3 == 0 else []) + [block]})
    story += blocks
    story += [
        {"S": [1, 6]},
        {"P": ["Reference support", "h3"]},
        {"P": ["The pool " + LOREM * 4 + " <super>\u2020</super> <font size=6.5>override, computed none</font>", "just"]},
        {"P": [LOREM * 5, "just"]},
        {"P": ["Right aligned &middot; " + LOREM[:90], "right"]},
        {"P": ["Centered heading text that is long enough to wrap onto a second line at nine points " * 2, "center"]},
        {"P": [f"A long word: {long_word} then more words after it.", "body"]},
        {"P": [f"<b>Bold long word:</b> {long_word} then more.", "body"]},
        {"P": ['See <link href="https://usace-wrises.github.io/staf/" color="#1f4e8c">the STAF site</link> for the method '
               "and the calculators; " + LOREM, "normal"]},
        {"P": ['<link href="https://usace-wrises.github.io/staf/app/">https://usace-wrises.github.io/staf/app/</link>', "normal"]},
        {"P": ["Symbols \u2265 0.69 and \u2264 0.39 read on the curve; " + LOREM * 2, "bodytext"]},
        {"P": ["Line one<br/>Line two after a break<br/><b>Line three</b> " + LOREM, "just"]},
    ]
    header = ["Metric", "Measured", "Index", "Source"]
    rows = [header] + [[f"Metric row {i}", f"{i * 1.5:.1f}", f"{(i % 10) / 10:.2f}",
                        {"P": [("Field " if i % 2 else "Desk ") + LOREM[: 20 + (i * 13) % 90], "small"]}] for i in range(70)]
    story += [{"P": ["Metric measurements", "h3"]},
              {"T": [rows, {"colWidths": [120.0, 80.0, 50.0, 250.0], "repeatRows": 1, "style": [
                  ["FONTSIZE", [0, 0], [-1, -1], 7], ["GRID", [0, 0], [-1, -1], 0.3, "#e5e8ee"],
                  ["BACKGROUND", [0, 0], [-1, 0], "#eef1f6"], ["TEXTCOLOR", [0, 0], [-1, 0], "#344054"],
                  ["ALIGN", [1, 1], [2, -1], "RIGHT"], ["VALIGN", [0, 0], [-1, -1], "TOP"],
                  ["ROWBACKGROUNDS", [0, 1], [-1, -1], ["white", "#f8fafc"]], ["LINEBELOW", [0, 0], [-1, 0], 0.8, "#344054"]]}]},
              {"S": [1, 8]},
              {"T": [[["Physical", "Chemical", "Biological", "ECI"], ["0.72", "0.55", "n/a", "0.64"]],
                     {"colWidths": [90, 90, 90, 90], "hAlign": "LEFT", "style": [
                         ["FONTSIZE", [0, 0], [-1, -1], 9], ["GRID", [0, 0], [-1, -1], 0.3, "#c9d2de"],
                         ["ALIGN", [0, 0], [-1, -1], "CENTER"], ["BACKGROUND", [0, 0], [-1, 0], "#eef1f6"]]}]},
              {"P": ["Plain words without any markup at all repeat " * 260, "indent"]},
              {"P": ["<b>Bold start</b> then styled text " + LOREM * 24, "body"]},
              {"P": ["Centered closing words " * 60, "center"]},
              _band("METRICS NOT SCORED"),
              {"T": [[[{"P": ["Embeddedness, Fine sediment. These metrics were considered for this assessment. " + LOREM, "body"]}]],
                     {"colWidths": [540.5], "style": [["BOX", [0, 0], [-1, -1], 0.5, "black"]]}]}]
    return {"doc": {"pagesize": [612.0, 792.0], "title": "Platypus test", "author": "STAF", "margins": [36, 36, 36, 36],
                    "pageNumbers": True},
            "styles": styles, "story": story}

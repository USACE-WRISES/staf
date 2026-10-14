"""The assessment's status on every page of DEEP's PDFs (owner, 2026-10-14).

The field forms, the metrics list and the report each carry, at the top of every
page, a band in the status's color that says "Assessment status: Draft. For
research and regional assessment development only." (the words of
:mod:`deep.session`). Every page of a Draft's PDFs also carries a large, light
diagonal DRAFT behind the content.

Both are drawn by the page callback, which reportlab runs before a page's
flowables, so the content sits on top of the watermark. The watermark is a
solid light tint, not a transparency: a filled cell covers it either way, and
the files stay free of graphics states. The rotation is one explicit matrix
(cos and sin of 45 degrees), which the static app's port writes the same way.

reportlab only, imported when a page is drawn.
"""
from __future__ import annotations

from typing import Optional

from . import session

#: Every DEEP PDF keeps this much paper above its content: the band, and air under it.
TOP_MARGIN_IN = 0.65
#: The band: its top edge this far below the paper's top, this tall.
BAND_TOP_IN = 0.25
BAND_HEIGHT_IN = 0.28
BAND_FONT = 9.5
BAND_PAD_PT = 6.0
#: The watermark of a Draft.
WATERMARK = "DRAFT"
WATERMARK_SIZE = 150
WATERMARK_TINT = "#E3E6E9"
_R45 = 0.7071067811865476


def page_marker(status: Optional[str], *, page_number: bool = False, short: bool = False):
    """The onPage callback for one PDF: ``status`` is the assessment's lifecycle status
    (None when no assessment is loaded: no band and no watermark). ``page_number`` puts
    "Page N" at the band's right end (the field forms, which number their pages). ``short``
    says the shorter lines a spreadsheet says (``session.SHEET_NOTES``: the field forms,
    owner 2026-10-14)."""

    def mark(canvas, doc):
        from reportlab.lib import colors
        from reportlab.lib.units import inch

        width, height = doc.pagesize
        canvas.saveState()
        if status is not None and session.lifecycle_status({"lifecycle": status}) == "draft":
            canvas.saveState()
            canvas.setFillColor(colors.HexColor(WATERMARK_TINT))
            canvas.transform(_R45, _R45, -_R45, _R45, width / 2.0, height / 2.0)
            canvas.setFont("Helvetica-Bold", WATERMARK_SIZE)
            canvas.drawCentredString(0, -0.359 * WATERMARK_SIZE, WATERMARK)
            canvas.restoreState()
        left = doc.leftMargin
        right = width - doc.rightMargin
        bottom = height - (BAND_TOP_IN + BAND_HEIGHT_IN) * inch
        baseline = bottom + (BAND_HEIGHT_IN * inch - 0.718 * BAND_FONT) / 2.0
        if status is not None:
            line, fill = session.STATUS_COLORS[session.lifecycle_status({"lifecycle": status})]
            canvas.setFillColor(colors.HexColor(line))
            canvas.rect(left, bottom, right - left, BAND_HEIGHT_IN * inch, stroke=0, fill=1)
            head, note = (session.sheet_status_parts if short else session.status_parts)(status)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", BAND_FONT)
            x = left + BAND_PAD_PT
            canvas.drawString(x, baseline, head)
            x += canvas.stringWidth(head, "Helvetica-Bold", BAND_FONT)
            canvas.setFont("Helvetica", BAND_FONT)
            canvas.drawString(x, baseline, " " + note)
        if page_number:
            canvas.setFillColor(colors.white if status is not None else colors.black)
            canvas.setFont("Helvetica", BAND_FONT)
            canvas.drawRightString(right - BAND_PAD_PT, baseline, f"Page {doc.page}")
        canvas.restoreState()

    return mark

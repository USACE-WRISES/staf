// The assessment's status on every page of DEEP's PDFs (deep/status_mark.py; owner, 2026-10-14): a band
// in the status's color at the top of every page ("Assessment status: Draft. For research and regional
// assessment development only."), and a large, light diagonal DRAFT behind a Draft's content. Drawn by the
// page callback, which runs before a page's flowables, so the content sits on top of the watermark; a
// solid tint, no transparency; the rotation one explicit matrix, as the Python writes it.
import type { Canvas } from '../../files/pdf/canvas'
import { black, HexColor, white } from '../../files/pdf/colors'
import { inch, type SimpleDocTemplate } from '../../files/pdf/doctemplate'
import { lifecycleStatus, sheetStatusParts, STATUS_COLORS } from '../../app/deepStatus'

/** Every DEEP PDF keeps this much paper above its content: the band, and air under it. */
export const TOP_MARGIN_IN = 0.65
const BAND_TOP_IN = 0.25
const BAND_HEIGHT_IN = 0.28
const BAND_FONT = 9.5
const BAND_PAD_PT = 6.0
const WATERMARK = 'DRAFT'
const WATERMARK_SIZE = 150
const WATERMARK_TINT = '#E3E6E9'
const R45 = 0.7071067811865476

/** page_marker: the onPage callback for one PDF; ``status`` null (no assessment) draws no band and no
 * watermark; ``pageNumber`` puts "Page N" at the band's right end (the field forms). The band says the
 * shorter lines the files say (SHEET_NOTES). */
export function pageMarker(status: string | null, pageNumber = false): (canvas: Canvas, doc: SimpleDocTemplate) => void {
  return (canvas, doc) => {
    const [width, height] = doc.pagesize
    canvas.saveState()
    if (status !== null && lifecycleStatus({ lifecycle: status }) === 'draft') {
      canvas.saveState()
      canvas.setFillColor(HexColor(WATERMARK_TINT))
      canvas.transform(R45, R45, -R45, R45, width / 2.0, height / 2.0)
      canvas.setFont('Helvetica-Bold', WATERMARK_SIZE)
      canvas.drawCentredString(0, -0.359 * WATERMARK_SIZE, WATERMARK)
      canvas.restoreState()
    }
    const left = doc.leftMargin
    const right = width - doc.rightMargin
    const bottom = height - (BAND_TOP_IN + BAND_HEIGHT_IN) * inch
    const baseline = bottom + (BAND_HEIGHT_IN * inch - 0.718 * BAND_FONT) / 2.0
    if (status !== null) {
      const [line] = STATUS_COLORS[lifecycleStatus({ lifecycle: status })]
      canvas.setFillColor(HexColor(line))
      canvas.rect(left, bottom, right - left, BAND_HEIGHT_IN * inch, 0, 1)
      const [head, note] = sheetStatusParts(status)
      canvas.setFillColor(white)
      canvas.setFont('Helvetica-Bold', BAND_FONT)
      let x = left + BAND_PAD_PT
      canvas.drawString(x, baseline, head)
      x += canvas.stringWidth(head, 'Helvetica-Bold', BAND_FONT)
      canvas.setFont('Helvetica', BAND_FONT)
      canvas.drawString(x, baseline, ' ' + note)
    }
    if (pageNumber) {
      canvas.setFillColor(status !== null ? white : black)
      canvas.setFont('Helvetica', BAND_FONT)
      canvas.drawRightString(right - BAND_PAD_PT, baseline, `Page ${doc.page}`)
    }
    canvas.restoreState()
  }
}

// The JSON story of parity/pdf_story.py laid out with the static app's port of platypus.
import { type Canvas } from '../src/files/pdf/canvas'
import { black, type Color, HexColor, white } from '../src/files/pdf/colors'
import { SimpleDocTemplate } from '../src/files/pdf/doctemplate'
import { type Flowable, KeepTogether, Spacer } from '../src/files/pdf/flowables'
import { Paragraph } from '../src/files/pdf/paragraph'
import { getSampleStyleSheet, ParagraphStyle } from '../src/files/pdf/styles'
import { type CellValue, type Cmd, Table, TableStyle } from '../src/files/pdf/tables'

type Spec = any

function color(v: unknown): unknown {
  if (typeof v === 'string' && v.startsWith('#')) return HexColor(v)
  if (v === 'black') return black
  if (v === 'white') return white
  return v
}

function styles(spec: Spec): Record<string, ParagraphStyle> {
  const sample = getSampleStyleSheet()
  const out: Record<string, ParagraphStyle> = {}
  for (const s of spec.styles) {
    const kw: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(s)) if (!['name', 'parent', 'sample'].includes(k)) kw[k] = k.endsWith('Color') ? color(v) : v
    if (s.sample) {
      const st = sample[s.sample]
      Object.assign(st, kw)
      out[s.name] = st
    } else out[s.name] = new ParagraphStyle(s.name, { parent: s.parent ? out[s.parent] : null, ...kw })
  }
  return out
}

function flowable(item: Spec, st: Record<string, ParagraphStyle>): Flowable {
  if (item.P) return new Paragraph(item.P[0], st[item.P[1]])
  if (item.S) return new Spacer(item.S[0], item.S[1])
  if (item.K) return new KeepTogether(item.K.map((i: Spec) => flowable(i, st)))
  if (item.T) {
    const [rows, opts] = item.T
    const data: CellValue[][] = rows.map((row: Spec[]) => row.map((c) => cell(c, st)))
    const cmds = (opts.style ?? []).map((c: Spec[]) => [c[0], c[1], c[2], ...c.slice(3).map(color)] as Cmd)
    return new Table(data, {
      style: new TableStyle(cmds), colWidths: opts.colWidths ?? null, rowHeights: opts.rowHeights ?? null, repeatRows: opts.repeatRows ?? 0,
      hAlign: opts.hAlign ?? null,
    })
  }
  throw new Error(JSON.stringify(item))
}

function cell(c: Spec, st: Record<string, ParagraphStyle>): CellValue {
  if (c === null || typeof c === 'string') return c
  if (Array.isArray(c)) return c.map((i) => flowable(i, st))
  return flowable(c, st)
}

export function buildStory(spec: Spec): Uint8Array {
  const st = styles(spec)
  const d = spec.doc
  const [top, bottom, left, right] = d.margins
  const doc = new SimpleDocTemplate({
    pagesize: d.pagesize, title: d.title ?? null, author: d.author ?? null, invariant: true, topMargin: top, bottomMargin: bottom, leftMargin: left,
    rightMargin: right,
  })
  const pageNumber = (canvas: Canvas, dd: SimpleDocTemplate) => {
    canvas.saveState()
    canvas.setFont('Times-Roman', 9)
    canvas.drawCentredString(d.pagesize[0] / 2.0, d.pagesize[1] - 0.32 * 72, String(dd.page))
    canvas.restoreState()
  }
  const cb = d.pageNumbers ? pageNumber : () => {}
  return doc.build(spec.story.map((i: Spec) => flowable(i, st)), cb, cb)
}

export type { Color }

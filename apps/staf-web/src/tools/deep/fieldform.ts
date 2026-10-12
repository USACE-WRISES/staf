// DEEP's field worksheet (deep/field_form.py build_field_form_pdf and report.py build_field_forms_pdf):
// SFARI's field form layout made from the loaded assessment (US Letter, half-inch margins, Times 9 pt,
// grey bands, a thin black grid), one block per function with its metrics, the desk's values printed
// with their source, and the metrics the assessment withholds listed at the end. Laid out by the port of
// reportlab, so the file is reportlab's byte for byte (invariant=1).
import { pyFixedComma, pySum } from '../../app/pyfmt'
import { xmlEscape } from '../../files/py'
import type { Canvas } from '../../files/pdf/canvas'
import { black, HexColor } from '../../files/pdf/colors'
import { inch, letter, SimpleDocTemplate } from '../../files/pdf/doctemplate'
import { type Flowable, KeepTogether, Spacer } from '../../files/pdf/flowables'
import { Paragraph } from '../../files/pdf/paragraph'
import { ParagraphStyle, TA_CENTER } from '../../files/pdf/styles'
import { type CellValue, type Cmd, Table, TableStyle } from '../../files/pdf/tables'
import { COMPUTABLE } from './computed'
import { assessmentName, coords, measureCode, reachId } from './forms'
import { type Bundle, CATEGORY_ORDER, FUNCTIONS_BY_ID, type Metric, methodText, type Rec, unitsOf, withheldForFunction, withheldOnlyFunctions } from './method'
import { pyStrOf, rows, sourceLabel, watershedBasisLabel } from './report'
import { stratumLabel, withheld, withheldNote, withheldReason } from './support'

export const TITLE = 'Detailed Evaluation of Ecosystem Processes (DEEP) Field Worksheet'
const BAND_FILL = '#D9D9D9'
const PAGE_MARGIN_IN = 0.5
const COL_FUNCTION_IN = 2180 / 1440.0
const COL_VALUE_IN = 1.05
const COL_INDEX_IN = 0.55
const COL_METRIC_IN = (2180 + 7964 + 646) / 1440.0 - COL_FUNCTION_IN - COL_VALUE_IN - COL_INDEX_IN

/** what the built-in Times font cannot draw, folded to plain characters */
const PRINT_SAFE: Record<number, string> = {
  0x2010: '-', 0x2011: '-', 0x2012: '-', 0x2013: '-', 0x2014: '-', 0x2212: '-', 0x2018: "'", 0x2019: "'", 0x201c: '"', 0x201d: '"',
  0x2026: '...', 0x00a0: ' ', 0x2264: '<=', 0x2265: '>=', 0x00b2: '2', 0x00b3: '3', 0x00b5: 'u', 0x03bc: 'u', 0x00d7: 'x', 0x00b7: '-',
}

export const INSTRUCTIONS = 'Record the measured value of each metric in its units. Metrics measured in the field are '
  + 'marked F and metrics answered from the desk are marked D. A value printed in the Value '
  + 'cell was already answered from the desk and carries its source. Enter the values in DEEP, '
  + 'which reads each one on the metric\'s reference curve to give an index from 0 to 1. The '
  + 'function score is the mean index of the function\'s metrics times 15 and reads Functioning '
  + '(15 to 11), Functioning At-Risk (10 to 6), or Non-Functioning (5 to 0). A metric that does '
  + 'not apply is marked NA and drops out of the score. Where a metric lists curve sets, mark '
  + 'the set that fits the reach.'

/** to_print_safe: the folds above, then anything outside Latin-1 as a question mark */
export function toPrintSafe(text: string): string {
  let out = ''
  for (const ch of text) {
    const cp = ch.codePointAt(0)!
    const t = PRINT_SAFE[cp]
    if (t !== undefined) out += t
    else out += cp > 0xff ? '?' : ch
  }
  return out
}

const esc = (text: string) => xmlEscape(toPrintSafe(text))

/** str.title(): each cased run starts upper and goes on lower */
function pyTitle(s: string): string {
  let out = ''
  let prevCased = false
  for (const ch of s) {
    const cased = ch.toUpperCase() !== ch.toLowerCase()
    out += cased ? (prevCased ? ch.toLowerCase() : ch.toUpperCase()) : ch
    prevCased = cased
  }
  return out
}

/** float(x) for a number or a number's text, else null */
function floatOf(v: unknown): number | null {
  if (typeof v === 'number') return v
  if (typeof v !== 'string') return null
  const t = v.trim()
  return t !== '' && /^[+-]?(\d[\d_]*\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$/.test(t) ? Number(t.replace(/_/g, '')) : null
}

/** curve_set_text: the curve sets to mark for a metric with several, else "" */
export function curveSetText(m: Metric): string {
  const layers: Rec[] = m.curveLayers || []
  if (layers.length < 2) return ''
  return 'Curve set: ' + layers.map((layer) => `[  ] ${stratumLabel(layer.stratum, m)}`).join('   ')
}

interface Block extends Rec { functionId: string, functionName?: string, metrics: Metric[], discipline: string, _order: number, _statement: string }

/** functions_in_order: [discipline, function blocks] in the framework's order, withheld-only functions included */
export function functionsInOrder(la: Bundle | null): [string, Block[]][] {
  const blocks: Block[] = ((la?.metricsByFunction ?? []) as Rec[]).filter((fn) => fn.metrics && fn.metrics.length).map((fn) => ({ ...fn }) as Block)
  const have = new Set(blocks.map((fn) => fn.functionId))
  for (const extra of withheldOnlyFunctions(la)) {
    if (!have.has(extra.functionId)) blocks.push({ functionId: extra.functionId, functionName: extra.functionName, metrics: [] } as unknown as Block)
  }
  for (const fn of blocks) {
    const ref: Rec = FUNCTIONS_BY_ID.get(fn.functionId) ?? {}
    if (!fn.discipline) fn.discipline = ref.category || ''        // setdefault, then the empty case
    fn._order = ref.order ?? 999
    fn._statement = ref.function_statement || ''
  }
  blocks.sort((a, b) => a._order - b._order)
  const extraCats = [...new Set(blocks.map((fn) => fn.discipline))].filter((c) => !CATEGORY_ORDER.includes(c)).sort()
  const out: [string, Block[]][] = []
  for (const cat of [...CATEGORY_ORDER, ...extraCats]) {
    const members = blocks.filter((fn) => fn.discipline === cat)
    if (members.length) out.push([cat, members])
  }
  return out
}

/** report._desktop_entries: {metricId: [value text, note]} for every desktop-computed value */
export function desktopEntries(la: Bundle | null, measured: Record<string, Rec> | null): Map<string, [string, string]> {
  const out = new Map<string, [string, string]>()
  if (!measured || !Object.keys(measured).length) return out
  for (const [, m, val, , meta, raw] of rows(la, measured)) {
    if (meta.origin !== 'desktop' || val === null || val === undefined || val === '') continue
    let note = `DESKTOP: ${sourceLabel(meta) || 'desktop'}`
    if (meta.reference_only) note += ' (reference only)'
    out.set(m.metricId, [pyStrOf(raw, 'value'), note])
  }
  return out
}

/** report.build_field_forms_pdf: the field worksheet for the loaded assessment, as PDF bytes */
export function buildFieldFormsPdf(la: Bundle | null, ref: string, measured: Record<string, Rec> | null, delineation: Rec | null,
  desktopIds: Set<string> = COMPUTABLE): Uint8Array {
  const entries = desktopEntries(la, measured)
  const body = new ParagraphStyle('ff_body', { fontName: 'Times-Roman', fontSize: 9, leading: 10.3 })
  const bold = new ParagraphStyle('ff_bold', { parent: body, fontName: 'Times-Bold' })
  const small = new ParagraphStyle('ff_small', { parent: body, fontSize: 8, leading: 9.2 })
  const note = new ParagraphStyle('ff_note', { parent: body, fontSize: 7.5, leading: 8.6, fontName: 'Times-Italic' })
  const title = new ParagraphStyle('ff_title', { parent: bold, fontSize: 11, leading: 13 })
  const center = new ParagraphStyle('ff_center', { parent: bold, alignment: TA_CENTER })
  const bandFill = HexColor(BAND_FILL)
  const line = 0.5
  const widths = [COL_FUNCTION_IN * inch, COL_METRIC_IN * inch, COL_VALUE_IN * inch, COL_INDEX_IN * inch]
  const full = pySum(widths)

  const band = (text: string): Table => new Table([[new Paragraph(esc(text), bold)]], {
    colWidths: [full], style: [['BACKGROUND', [0, 0], [-1, -1], bandFill], ['BOX', [0, 0], [-1, -1], line, black],
      ['TOPPADDING', [0, 0], [-1, -1], 2], ['BOTTOMPADDING', [0, 0], [-1, -1], 2]],
  })
  const pageNumber = (canvas: Canvas, doc: SimpleDocTemplate) => {
    canvas.saveState()
    canvas.setFont('Times-Roman', 9)
    canvas.drawCentredString(letter[0] / 2.0, letter[1] - 0.32 * inch, String(doc.page))
    canvas.restoreState()
  }
  const doc = new SimpleDocTemplate({
    pagesize: letter, title: 'DEEP Field Worksheet', author: 'STAF DEEP', invariant: true, topMargin: PAGE_MARGIN_IN * inch,
    bottomMargin: PAGE_MARGIN_IN * inch, leftMargin: PAGE_MARGIN_IN * inch, rightMargin: PAGE_MARGIN_IN * inch,
  })

  const name = (la ? assessmentName(la) : '') || 'Detailed assessment'
  const raw: Rec = la ?? {}
  const version = (raw.library || {}).version
  const status = String(raw.lifecycle || (raw.library || {}).status || '').trim()
  const about = [esc(name)]
  if (ref) about.push(esc(ref))
  else if (version !== null && version !== undefined) about.push(`version ${pyStrOf(raw.library, 'version')}`)
  if (status) about.push(esc(pyTitle(status.replace(/_/g, ' '))))
  const cite = la?.sourceCitation
  const story: Flowable[] = [new Paragraph(esc(TITLE), title), new Paragraph(about.join('  |  '), body)]
  if (cite) story.push(new Paragraph(esc(String(cite)), small))

  const dl: Rec = (delineation ?? {}).delineation || {}
  const reachLen = dl.reach_length_ft
  let reachLenTxt = ''
  if (reachLen !== null && reachLen !== undefined && reachLen !== '') {
    const ft = floatOf(reachLen)
    if (ft !== null) reachLenTxt = `${pyFixedComma(ft, 0)} ft`
  }
  const blank = (label: string, value: string, widthChars: number) => `<b>${label}</b> ${value ? esc(value) : '_'.repeat(widthChars)}`
  story.push(new Spacer(1, 3), new Paragraph([
    blank('Reach ID:', reachId(dl), 13), blank('Reach Length:', reachLenTxt, 8), blank('Date:', '', 10), blank('Assessor(s):', '', 15),
    blank('Coordinates:', coords(dl), 13)].join('   '), body))
  if (Object.keys(dl).length) {
    const stream = dl.gnis_name || '(unnamed reach)'
    const area = dl.drainage_area_sqkm
    const site = [`Site: ${esc(String(stream))}`]
    if (area !== null && area !== undefined && area !== '') site.push(`drainage area ${esc(area ? pyStrOf(dl, 'drainage_area_sqkm') : '')} km2`)
    site.push(esc(watershedBasisLabel(delineation)))
    story.push(new Paragraph(site.join('  |  '), small))
  }

  story.push(new Spacer(1, 4), band('RECORDING INSTRUCTIONS'),
    new Table([[new Paragraph(esc(INSTRUCTIONS), body)]], {
      colWidths: [full], style: [['BOX', [0, 0], [-1, -1], line, black], ['TOPPADDING', [0, 0], [-1, -1], 2], ['BOTTOMPADDING', [0, 0], [-1, -1], 3]],
    }),
    new Table([[new Paragraph('Function', bold), new Paragraph('Metrics', bold), new Paragraph('Value', center), new Paragraph('Index', center)]], {
      colWidths: widths, style: [['GRID', [0, 0], [-1, -1], line, black], ['BACKGROUND', [0, 0], [-1, -1], bandFill],
        ['TOPPADDING', [0, 0], [-1, -1], 2], ['BOTTOMPADDING', [0, 0], [-1, -1], 2]],
    }))

  const groups = la ? functionsInOrder(la) : []
  for (const [category, functions] of groups) {
    // the discipline band travels with its first function block
    let pendingBand: Table | null = band(`${String(category).toUpperCase()} FUNCTIONS`)
    for (const fn of functions) {
      const fid = fn.functionId
      let left = `<b>${esc(String(fn.functionName || fid))}:</b>`
      if (fn._statement) left += `<br/>${esc(fn._statement)}`
      const rws: [CellValue, CellValue, CellValue][] = []
      for (const m of fn.metrics || []) {
        const units = unitsOf(m)
        let head = `<b>${esc(String(m.metricName || m.metricId))}</b>`
        if (units) head += ` (${esc(units)})`
        head += ` ${measureCode(m, desktopIds)}`
        const method = methodText(m)
        const parts: Flowable[] = [new Paragraph(head + (method ? `: ${esc(method)}` : ''), body)]
        const curveSets = curveSetText(m)
        if (curveSets) parts.push(new Paragraph(esc(curveSets), small))
        const [valTxt, noteTxt] = entries.get(m.metricId) ?? ['', '']
        if (noteTxt) parts.push(new Paragraph(esc(noteTxt), note))
        rws.push([parts, valTxt ? new Paragraph(esc(valTxt), center) : '', ''])
      }
      for (const w of withheldForFunction(la, fid)) {
        rws.push([[new Paragraph(`<b>${esc(String(w.metricName || w.metricId))}</b> not scored`, body),
          new Paragraph(`${withheldReason(w)}. No value is needed.`, note)], new Paragraph('NA', center), ''])
      }
      if (!rws.length) rws.push([[new Paragraph('No metric is scored for this function.', note)], '', ''])
      const data: CellValue[][] = [[new Paragraph(left, body), rws[0][0], rws[0][1], rws[0][2]]]
      for (const r of rws.slice(1)) data.push(['', r[0], r[1], r[2]])
      data.push([new Paragraph('<b>Score:</b>', body), new Paragraph('<b>Notes/Other Metrics:</b>', body), '', ''])
      const n = rws.length
      const style: Cmd[] = [['GRID', [0, 0], [-1, -1], line, black], ['VALIGN', [0, 0], [-1, -1], 'TOP'], ['SPAN', [0, 0], [0, n - 1]],
        ['SPAN', [1, n], [3, n]], ['TOPPADDING', [0, 0], [-1, -1], 2], ['BOTTOMPADDING', [0, 0], [-1, -1], 3],
        ['LEFTPADDING', [0, 0], [-1, -1], 4], ['RIGHTPADDING', [0, 0], [-1, -1], 4]]
      const block = new Table(data, { colWidths: widths, rowHeights: [...new Array(n).fill(null), 0.42 * inch], style: new TableStyle(style) })
      story.push(new KeepTogether(pendingBand !== null ? [pendingBand, block] : [block]))
      pendingBand = null
    }
  }
  if (!groups.length) story.push(new Spacer(1, 8), new Paragraph('This assessment has no metrics defined.', body))

  const wh = la ? withheld(la) : []
  if (wh.length) {
    const names = wh.map((w) => String(w.metricName || w.metricId)).sort().join(', ')
    story.push(new Spacer(1, 6), band('METRICS NOT SCORED'),
      new Table([[new Paragraph(esc(`${names}. These metrics were considered for this assessment. ` + withheldNote(wh)
        + ' No measurement is needed for the score. A value may still be recorded under Notes.'), body)]],
      { colWidths: [full], style: [['BOX', [0, 0], [-1, -1], line, black]] }))
  }
  return doc.build(story, pageNumber, pageNumber)
}

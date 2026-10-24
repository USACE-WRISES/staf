// The two authored tabs (libs/staf_workbook sheets.py): Summary (the summary block, then the scores of
// every scenario side by side, each alternative followed by its change from Existing Conditions, as the
// Compare dialog shows them) and ReferenceCurves (each metric's curve as a chart beside its points, then
// any tables). Scores are live formulas pointing at each scenario's calculator, with the app's values
// stored as their results so they read correctly before Excel recalculates.
import { pySum } from '../../app/pyfmt'
import { buildComparison, excelRound, type Measure, type ScenarioScores } from '../../shared/scenarios'
import { type SummaryInfo, summaryRows, TIER } from '../../shared/summary'
import { isAlpha, isDigit, isUpper, pySplit, pyStrip } from '../py'
import { type Built, type ScenarioInput, sheetFor, type AssemblySpec } from './assemble'
import { colLetter, SheetWriter } from './author'
import { addCharts, type ScatterChart, scatterChart, type Series } from './charts'
import { quoteSheet } from './formula'
import { StyleBook } from './styles'
import { definedName } from './workbook'

export const SUMMARY = 'Summary'
export const CURVES = 'ReferenceCurves'
/** The Compare dialog's palette: text, labels, muted text, rules, the Existing Conditions tint, and a
 * change up or down */
const INK = '2B3445'
const LABEL = '5A6578'
const MUTED = '7A8699'
const RULE = 'E5E8EE'
const RULE_STRONG = 'D5DEEA'
const TINT = 'F6F8FB'
const UP = '2F7A4B'
const DOWN = 'A33A3A'
/** The calculators' colour key (their Instructions sheet), which the summary block follows when it sits under
 * the title: an entry, a cell read from another sheet (and its rule), and every other rule */
const KEY_ENTRY = 'F8CBAD'
const KEY_LINKED = 'F2F2F2'
const KEY_LINKED_RULE = '7F7F7F'
const KEY_RULE = '000000'
/** Summary column widths (characters) and row heights (points) */
const VALUE_W = 13
/** sheets.BANNER_D_W: column D's width under a banner, so its line fits on one row (owner, 2026-10-14) */
const BANNER_D_W = 15.7109375        // Excel shows 15 (fifteen Calibri 11 characters and the cell padding)
const CHANGE_W = 10
const LABEL_W_MIN = 24
const LABEL_W_MAX = 44
const TITLE_H = 28
const LINE_H = 15
const ROW_H = 18
const GROUP_H = 22
const HEAD_H = 21
const HEADING_H = 28
const GAP_H = 10
/** printable area inside the margins (points), the smaller of Letter and A4 */
const PRINTABLE: Record<string, [number, number]> = { portrait: [523.0, 705.0], landscape: [720.0, 509.0] }
const ONE_PAGE_SCALE = 0.75

/** Where a scenario's results sit in the template (template sheet names and cells). */
export interface SummaryCells {
  eci: [string, string]
  /** physical/chemical/biological to (sheet, cell) */
  sub: Record<string, [string, string]>
  /** [fid, label, category, (sheet, cell)] */
  functions: [string, string, string, [string, string]][]
  indexDigits: number
  functionDigits: number
  /** [heading, [text per scenario]]: blocks under the table */
  statements: [string, string[]][]
  note: string
  /** summary-block rows read live from Existing Conditions: label to [template sheet, cell, the value stored as
   * the formula's result]; SFARI's Reach name and Reach length (owner, 2026-10-19) */
  siteCells: Record<string, [string, string, string | number]>
  /** the summary block right under the title lines, a gap row after it (SFARI, owner 2026-10-19), in the
   * calculator's colour key: every label and value ruled, entries orange, ``siteCells`` grey as cells linked to
   * another sheet (the owner's SFARI Summary; EASI's, owner 2026-10-23) */
  siteFirst: boolean
}

export const summaryCells = (c: Partial<SummaryCells> & Pick<SummaryCells, 'eci' | 'sub' | 'functions'>): SummaryCells =>
  ({ indexDigits: 2, functionDigits: 0, statements: [], note: '', siteCells: {}, siteFirst: false, ...c })

/** What the ReferenceCurves tab shows (model/refcurves.py). */
export interface CurveBlock { kind: 'curve', title: string, subtitle: string, xLabel: string, yLabel: string, series: [string, [number, number][]][], note: string }
export interface TableBlock { kind: 'table', title: string, header: string[], rows: unknown[][], note: string, widths: number[] }
export interface RefCurves { intro: string, blocks: (CurveBlock | TableBlock)[] }

export const curveBlock = (title: string, subtitle = '', xLabel = '', yLabel = 'Index', series: [string, [number, number][]][] = [], note = ''): CurveBlock =>
  ({ kind: 'curve', title, subtitle, xLabel, yLabel, series, note })
export const tableBlock = (title: string, header: string[] = [], rows: unknown[][] = [], note = '', widths: number[] = []): TableBlock =>
  ({ kind: 'table', title, header, rows, note, widths })

class Styles {
  title: number
  sub: number
  section: number
  headRule: number
  head: number
  headLeft: number
  note: number
  rule: number
  cell: number
  cellLeft: number

  constructor(sb: StyleBook) {
    const f = sb.font.bind(sb)
    this.title = sb.xf({ font: f({ bold: true, size: 16, color: INK }) })
    this.sub = sb.xf({ font: f({ size: 11, color: MUTED }) })
    this.section = sb.xf({ font: f({ bold: true, size: 12, color: INK }), valign: 'bottom' })
    this.headRule = sb.border({ bottom: RULE_STRONG, bottomStyle: 'medium' })
    this.head = sb.xf({ font: f({ bold: true, size: 11, color: INK }), border: this.headRule, halign: 'center', valign: 'bottom', wrap: true })
    this.headLeft = sb.xf({ font: f({ bold: true, size: 11, color: INK }), border: this.headRule, halign: 'left', valign: 'bottom', wrap: true })
    this.note = sb.xf({ font: f({ size: 11, color: MUTED }) })
    this.rule = sb.border({ bottom: RULE })
    this.cell = sb.xf({ font: f({ size: 11, color: INK }), border: this.rule, halign: 'center' })
    this.cellLeft = sb.xf({ font: f({ size: 11, color: INK }), border: this.rule, wrap: true })
  }
}

class SummaryStyles extends Styles {
  key: number
  val: number
  noteBlock: number
  headChange: number
  group: number
  groupCell: number
  label: number
  labelBold: number
  up: number
  down: number

  constructor(private sb: StyleBook) {
    super(sb)
    const f = sb.font.bind(sb)
    this.key = sb.xf({ font: f({ size: 11, color: LABEL }), valign: 'top', wrap: true })
    this.val = sb.xf({ font: f({ size: 11, color: INK }), valign: 'top', wrap: true })
    this.noteBlock = sb.xf({ font: f({ size: 11, color: MUTED }), valign: 'top', wrap: true })
    this.headChange = sb.xf({ font: f({ size: 11, color: MUTED }), border: this.headRule, halign: 'center', valign: 'bottom', wrap: true })
    const groupRule = sb.border({ bottom: RULE_STRONG })
    this.group = sb.xf({ font: f({ bold: true, size: 11, color: LABEL }), border: groupRule, valign: 'bottom' })
    this.groupCell = sb.xf({ border: groupRule, valign: 'bottom' })
    this.label = sb.xf({ font: f({ size: 11, color: INK }), border: this.rule, wrap: true })
    this.labelBold = sb.xf({ font: f({ bold: true, size: 11, color: INK }), border: this.rule, wrap: true })
    this.up = sb.dxf({ bold: true, color: UP })
    this.down = sb.dxf({ bold: true, color: DOWN })
  }

  /** keyed: the summary block's styles in the calculator's colour key: ``label``, ``entry`` (a value typed in
   * the workbook), ``linked`` (read from a scenario sheet) and ``fixed`` (set by the app). */
  keyed(): Record<'label' | 'entry' | 'linked' | 'fixed', number> {
    const sb = this.sb
    const f = sb.font.bind(sb)
    const rule = sb.box(KEY_RULE)
    const linkedRule = sb.box(KEY_LINKED_RULE)
    const label = sb.xf({ font: f({ size: 11, color: LABEL }), border: rule, wrap: true })
    const entry = sb.xf({ font: f({ size: 11, color: INK }), fill: sb.fill(KEY_ENTRY), border: rule, wrap: true })
    const linked = sb.xf({ font: f({ size: 11, color: INK }), fill: sb.fill(KEY_LINKED), border: linkedRule, wrap: true })
    const fixed = sb.xf({ font: f({ size: 11, color: INK }), border: rule, wrap: true })
    return { label, entry, linked, fixed }
  }

  value(digits: number, bold: boolean, base: boolean): number {
    const sb = this.sb
    return sb.xf({ font: sb.font({ bold, size: 11, color: INK }), numfmt: sb.numfmt(fmt(digits)), fill: sb.fill(base ? TINT : null),
      border: this.rule, halign: 'center' })
  }

  /** A change from Existing Conditions: signed and muted; the conditional formats colour it. */
  change(digits: number, bold: boolean): number {
    const sb = this.sb
    return sb.xf({ font: sb.font({ bold, size: 11, color: MUTED }), numfmt: sb.numfmt(signed(digits)), border: this.rule, halign: 'center' })
  }
}

const fmt = (digits: number) => (digits === 0 ? '0' : '0.' + '0'.repeat(digits))
const signed = (digits: number) => `+${fmt(digits)};-${fmt(digits)};${fmt(digits)}`

function ref(sheet: string, cell: string): string {
  const col = [...cell].filter(isAlpha).join('')
  const row = [...cell].filter(isDigit).join('')
  return `${quoteSheet(sheet)}!$${col}$${row}`
}

const isEmptyMeasure = (m: Measure | undefined) => !m || ((m.value === null || m.value === undefined) && !(m.low != null && m.high != null))

function cachedOf(m: Measure | undefined): number | '' {
  if (!m || isEmptyMeasure(m) || m.value === null || m.value === undefined) return ''
  return Number(m.value)
}

// ---- text metrics (Calibri 11 at 100%; estimates err wide so wrapped text is never cut off)
const NARROW = new Set("fijlrtI.,:;'!|() ")
const WIDE = new Set('mwMW@%')

export function textPx(text: string, bold = false): number {
  const px = pySum([...String(text)].map((ch) => (NARROW.has(ch) ? 3.5 : WIDE.has(ch) ? 10.5 : isUpper(ch) ? 7.5 : 6.6)))
  return px * (bold ? 1.06 : 1.0)
}

const colPx = (width: number) => 7.0 * width

/** _lines: how many lines ``text`` takes wrapped in a cell ``px`` pixels wide (a greedy word wrap). */
export function lines(text: string | number | null | undefined, px: number, bold = false): number {
  const room = Math.max(px - 7.0, 21.0)
  const space = textPx(' ', bold)
  let total = 0
  for (const para of String(text || '').split('\n')) {
    let count = 1
    let used = 0.0
    for (const word of pySplit(para)) {
      const wpx = textPx(word, bold)
      if (used && used + space + wpx > room) {
        count += 1
        used = 0.0
      }
      if (!used && wpx > room) {                    // a word wider than the cell breaks by itself
        const full = Math.ceil(wpx / room)
        count += full - 1
        used = wpx - (full - 1) * room
      } else {
        used += (used ? space : 0.0) + wpx
      }
    }
    total += count
  }
  return Math.max(total, 1)
}

const height = (n: number, base: number) => base + LINE_H * (Math.max(n, 1) - 1)

/** page_fit: (orientation, pages tall): one page when it prints at 75% or more (portrait unless
 * landscape fits better); otherwise as wide as the page and as many pages tall as it needs. */
export function pageFit(widthPt: number, heightPt: number): [string, number] {
  const scale = (o: string) => Math.min(1.0, PRINTABLE[o][0] / widthPt, PRINTABLE[o][1] / heightPt)
  const best = scale('landscape') > scale('portrait') ? 'landscape' : 'portrait'      // a tie goes to portrait
  if (scale(best) >= ONE_PAGE_SCALE) return [best, 1]
  return [PRINTABLE.portrait[0] / widthPt >= ONE_PAGE_SCALE ? 'portrait' : 'landscape', 0]
}

/** _extent: the sheet's printed size in points (columns 1..lastCol, rows 1..lastRow). */
function extent(w: SheetWriter, lastCol: number, lastRow: number): [number, number] {
  const cols: number[] = []
  for (let c = 1; c <= lastCol; c++) cols.push(colPx(w.widths.get(c) ?? 8.43))
  const rows: number[] = []
  for (let r = 1; r <= lastRow; r++) rows.push(w.heights.get(r) ?? 15.0)
  return [pySum(cols) * 0.75, pySum(rows)]
}

/** sheets.Banner: a line across the top of the authored tabs, also their print header and tab color
 * (DEEP's assessment status, owner 2026-10-14): ``text`` in white bold on ``color`` (RRGGBB); ``header``
 * is the print header's text (its two lines apart), else ``text``. */
export interface Banner { text: string, color: string, header?: string }

function bannerStyle(sb: StyleBook, banner: Banner): number {
  return sb.xf({ font: sb.font({ bold: true, size: 11, color: 'FFFFFF' }), fill: sb.fill(banner.color), halign: 'left', valign: 'center',
    wrap: true, indent: 1 })
}

function mark(w: SheetWriter, banner: Banner): void {
  w.header = [banner.header || banner.text, banner.color]
  w.tabColor = banner.color
}

/** add_summary_sheet: the Summary tab written into ``built``; its name. A ``banner`` takes row 3. */
export function addSummarySheet(built: Built, spec: AssemblySpec, sb: StyleBook, info: SummaryInfo, cells: SummaryCells, scores: ScenarioScores[],
  scenarios: ScenarioInput[], generatedOn: string, banner: Banner | null = null): string {
  const st = new SummaryStyles(sb)
  const w = new SheetWriter()
  const n = scenarios.length
  const last = 2 * n                                 // A, Existing Conditions, then value + change per alternative
  const span = Math.max(last, 4)                     // text blocks run at least to column D
  const valueCol = [2, ...Array.from({ length: n - 1 }, (_, k) => 2 * (k + 1) + 1)]
  const empty: ScenarioScores = { eci: {}, sub: {}, functions: {} }
  const scList = scenarios.map((_, i) => scores[i] ?? empty)
  const cmp = buildComparison(scenarios.map((s) => s.name), scList, cells.functions.map(([fid, label, cat]) => [fid, label, cat]),
    cells.indexDigits, cells.functionDigits)
  const where = new Map<string, [string, string]>([['eci', cells.eci], ...Object.entries(cells.sub),
    ...cells.functions.map(([fid, , , cell]) => ['fn:' + fid, cell] as [string, [string, string]])])
  // ---- columns: A fits its longest label; values and changes alternate after B
  const site = summaryRows(info)
  const groups: string[] = []
  for (const r of cmp.rows) if (!groups.length || groups[groups.length - 1] !== (r.group || 'Functions')) groups.push(r.group || 'Functions')
  const labelPx = Math.max(...site.map(([k]) => textPx(k)), ...groups.map((g) => textPx(g, true)), ...cmp.rows.map((r) => textPx(r.label, r.key === 'eci')))
  const widths = new Map<number, number>([[1, Math.min(LABEL_W_MAX, Math.max(LABEL_W_MIN, Math.ceil((labelPx + 12) / 7)))]])
  widths.set(2, VALUE_W)
  for (let c = 3; c <= span; c++) widths.set(c, c % 2 ? VALUE_W : CHANGE_W)
  if (banner) widths.set(4, Math.max(widths.get(4)!, BANNER_D_W))
  for (const [c, wd] of widths) w.width(c, wd)
  const aPx = colPx(widths.get(1)!)
  const restCols: number[] = []
  for (let c = 2; c <= span; c++) restCols.push(colPx(widths.get(c)!))
  const restPx = pySum(restCols)

  const pair = (row: number, key: string, value: string | number, keyStyle = st.key, formula: string | null = null, valStyle = st.val): number => {
    w.text(`A${row}`, key, keyStyle)
    if (formula === null) w.text(`B${row}`, value as string, valStyle)
    else w.formula(`B${row}`, formula, value, valStyle)
    for (let c = 3; c <= span; c++) w.blank(`${colLetter(c)}${row}`, valStyle)
    w.merge(`B${row}:${colLetter(span)}${row}`)
    w.height(row, height(Math.max(lines(key, aPx), lines(value, restPx)), ROW_H))
    return row + 1
  }
  const heading = (row: number, text: string): number => {
    w.text(`A${row}`, text, st.section)
    w.height(row, HEADING_H)
    return row + 1
  }

  // ---- title and the summary block
  w.text('A1', `${spec.appTitle} assessment summary`, st.title)
  w.height(1, TITLE_H)
  w.text('A2', `Generated ${generatedOn} by the ${spec.appTitle} web application.`, st.sub)
  w.height(2, ROW_H)
  let row = 4
  if (!banner && cells.siteFirst) row = 3
  else if (!banner) w.height(3, GAP_H)
  else {
    const style = bannerStyle(sb, banner)
    w.text('A3', banner.text, style)
    for (let c = 2; c <= span; c++) w.blank(`${colLetter(c)}3`, style)
    w.merge(`A3:${colLetter(span)}3`)
    w.height(3, height(lines(banner.text, aPx + restPx, true), ROW_H + 3))
    w.height(4, GAP_H)
    mark(w, banner)
    row = 5
  }
  const keyed = cells.siteFirst ? st.keyed() : null
  for (const [key, value] of site) {
    const live = Object.prototype.hasOwnProperty.call(cells.siteCells, key) ? cells.siteCells[key] : null
    const keyStyle = keyed ? keyed.label : st.key
    const valStyle = keyed ? keyed[live ? 'linked' : key === TIER ? 'fixed' : 'entry'] : st.val
    if (live) {
      const [sheet, cell, stored] = live
      const rf = `${quoteSheet(sheetFor(built, 0, sheet))}!${cell}`
      row = pair(row, key, stored, keyStyle, `IF(ISBLANK(${rf}),"",${rf})`, valStyle)
    } else row = pair(row, key, value, keyStyle, null, valStyle)
  }
  if (!banner && cells.siteFirst) {
    w.height(row, ROW_H)
    row += 1
  }
  const described = scenarios.filter((s) => pyStrip(s.description || ''))
  if (described.length) {
    row = heading(row, 'Scenarios')
    for (const s of described) row = pair(row, s.name, pyStrip(s.description))
  }
  // ---- the scores
  row = heading(row, 'Scores')
  const head = row
  w.text(`A${head}`, '', st.headLeft)
  let nameLines = 1
  scenarios.forEach((s, i) => {
    const c = valueCol[i]
    w.text(`${colLetter(c)}${head}`, s.name, st.head)
    nameLines = Math.max(nameLines, lines(s.name, colPx(widths.get(c)!), true))
    if (i) w.text(`${colLetter(c + 1)}${head}`, 'Change', st.headChange)
  })
  w.height(head, height(nameLines, HEAD_H))
  row = head + 1
  const first = row
  let group: string | null = null
  for (const r of cmp.rows) {
    if ((r.group || 'Functions') !== group) {
      group = r.group || 'Functions'
      w.text(`A${row}`, group, st.group)
      for (let c = 2; c <= last; c++) w.blank(`${colLetter(c)}${row}`, st.groupCell)
      w.height(row, GROUP_H)
      row += 1
    }
    const bold = r.key === 'eci'
    w.text(`A${row}`, r.label, bold ? st.labelBold : st.label)
    const [sheet, cell] = where.get(r.key)!
    for (let i = 0; i < n; i++) {
      const c = valueCol[i]
      const rf = ref(sheetFor(built, i, sheet), cell)
      let cached: number | '' = cachedOf(r.values[i])
      if (cached !== '' && !r.key.startsWith('fn:')) cached = excelRound(cached, 10)
      w.formula(`${colLetter(c)}${row}`, `IF(ISNUMBER(${rf}),${rf},"")`, cached, st.value(r.digits, bold, i === 0))
      if (i) {
        const a = `${colLetter(c)}${row}`
        const b = `$B${row}`
        const d = r.digits
        const delta = r.deltas[i - 1]
        const stored = delta !== null && delta[0] === delta[1] ? delta[0] : ''
        w.formula(`${colLetter(c + 1)}${row}`, `IF(AND(ISNUMBER(${a}),ISNUMBER(${b})),ROUND(${a},${d})-ROUND(${b},${d}),"")`, stored, st.change(d, bold))
      }
    }
    w.height(row, height(lines(r.label, aPx, bold), ROW_H))
    row += 1
  }
  const end = row - 1
  for (let i = 1; i < n; i++) {
    const col = colLetter(valueCol[i] + 1)
    const rng = `${col}${first}:${col}${end}`
    const top = `${col}${first}`
    w.conditional(rng, `AND(ISNUMBER(${top}),${top}>0)`, st.up)
    w.conditional(rng, `AND(ISNUMBER(${top}),${top}<0)`, st.down)
  }
  // ---- statements (DEEP's condition claim) and the note
  for (const [title, texts] of cells.statements) {
    const shown: [string, string][] = []
    for (let i = 0; i < Math.min(n, texts.length); i++) {
      const text = pyStrip(String(texts[i] || ''))
      if (text) shown.push([scenarios[i].name, text])
    }
    if (!shown.length) continue
    row = heading(row, title)
    for (const [name, text] of shown) row = pair(row, name, text)
  }
  if (cells.note) {
    w.height(row, GAP_H)
    row += 1
    w.text(`A${row}`, cells.note, st.noteBlock)
    for (let c = 2; c <= span; c++) w.blank(`${colLetter(c)}${row}`, st.noteBlock)
    w.merge(`A${row}:${colLetter(span)}${row}`)
    w.height(row, height(lines(cells.note, aPx + restPx), ROW_H))
    row += 1
  }
  // ---- the printed page: centred, fitted, the scores' header repeated on every page
  w.centerHorizontally = true
  ;[w.orientation, w.fitHeight] = pageFit(...extent(w, span, row - 1))
  const sheet = built.wb.addSheet(SUMMARY, w.xml())
  built.wb.names.push(definedName('_xlnm.Print_Titles', `${quoteSheet(sheet.name)}!$${head}:$${head}`, sheet.name))
  return sheet.name
}

/** add_curves_sheet: the ReferenceCurves tab (charts and tables); its name. */
export function addCurvesSheet(built: Built, sb: StyleBook, curves: RefCurves, banner: Banner | null = null): string {
  const st = new Styles(sb)
  const w = new SheetWriter()
  w.text('A1', 'Reference curves', st.title)
  if (curves.intro) w.text('A2', curves.intro, st.sub)
  w.height(1, 24)
  if (banner) {
    const style = bannerStyle(sb, banner)
    w.text('A3', banner.text, style)
    for (let c = 2; c <= 8; c++) w.blank(`${colLetter(c)}3`, style)
    w.merge('A3:H3')
    w.height(3, 21)
    mark(w, banner)
  }
  let row = 4
  const charts: ScatterChart[] = []
  for (const block of curves.blocks) {
    if (block.kind === 'curve') {
      w.text(`A${row}`, block.title, st.section)
      if (block.subtitle) w.text(`A${row + 1}`, block.subtitle, st.sub)
      const top = row + 3
      let col = 1
      const series: Series[] = []
      for (const [name, points] of block.series) {
        const xs = points.map((p) => p[0])
        const ys = points.map((p) => p[1])
        w.text(`${colLetter(col)}${top - 1}`, block.series.length > 1 ? name : block.xLabel || 'Value', st.headLeft)
        w.text(`${colLetter(col + 1)}${top - 1}`, block.yLabel, st.head)
        points.forEach(([x, y], k) => {
          w.number(`${colLetter(col)}${top + k}`, x, st.cell)
          w.number(`${colLetter(col + 1)}${top + k}`, y, st.cell)
        })
        const xRef = `${quoteSheet(CURVES)}!$${colLetter(col)}$${top}:$${colLetter(col)}$${top + points.length - 1}`
        const yRef = `${quoteSheet(CURVES)}!$${colLetter(col + 1)}$${top}:$${colLetter(col + 1)}$${top + points.length - 1}`
        series.push({ name, xRef, yRef, x: xs, y: ys })
        col += 2
      }
      const longest = Math.max(...block.series.map(([, p]) => p.length), 1)
      const chartCol = Math.max(col + 1, 6)
      const tall = 16
      charts.push(scatterChart(block.title, block.xLabel || 'Value', block.yLabel, series, [chartCol - 1, row - 1, chartCol + 7, row - 1 + tall]))
      if (block.note) w.text(`A${top + longest + 1}`, block.note, st.note)
      row += Math.max(tall + 2, longest + 6)
    } else {
      w.text(`A${row}`, block.title, st.section)
      row += 1
      block.header.forEach((hd, k) => w.text(`${colLetter(k + 1)}${row}`, hd, k === 0 ? st.headLeft : st.head))
      for (const values of block.rows) {
        row += 1
        values.forEach((v, k) => {
          if (typeof v === 'number') w.number(`${colLetter(k + 1)}${row}`, v, st.cell)
          else w.text(`${colLetter(k + 1)}${row}`, v === null || v === undefined ? '' : String(v), st.cellLeft)
        })
      }
      if (block.note) {
        row += 1
        w.text(`A${row}`, block.note, st.note)
      }
      row += 3
    }
  }
  ;[16, 10, 16, 10, 16, 10, 16, 10].forEach((wd, k) => w.width(k + 1, wd))
  for (const block of curves.blocks) {
    if (block.kind === 'table' && block.widths.length) block.widths.forEach((wd, k) => w.width(k + 1, Math.max(w.widths.get(k + 1) ?? 0, wd)))
  }
  const sheet = built.wb.addSheet(CURVES, w.xml())
  if (charts.length) {
    w.drawingRid = addCharts(built.pkg, sheet.part, charts)
    built.pkg.setText(sheet.part, w.xml())
  }
  return sheet.name
}

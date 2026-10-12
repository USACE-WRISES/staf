// A small worksheet writer for the authored sheets, Summary and ReferenceCurves (libs/staf_workbook
// xlsx/author.py): inline strings, numbers, formulas with stored results, merges, column widths, a
// frozen header, conditional formats, the printed page, and an optional drawing. Elements are written
// in the order the schema requires.
import { pyFloat, pyG } from '../../app/pyfmt'
import { xmlEscape } from '../py'

const NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
const NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
/** characters XML 1.0 forbids (a pasted control character would make the sheet unreadable) */
const NOT_XML = /[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]/g

export function colLetter(n: number): string {
  let s = ''
  while (n) {
    const r = (n - 1) % 26
    n = Math.floor((n - 1) / 26)
    s = String.fromCharCode(65 + r) + s
  }
  return s
}

export function colNumber(letters: string): number {
  let n = 0
  for (const ch of letters.toUpperCase()) n = n * 26 + ch.charCodeAt(0) - 64
  return n
}

export function splitRef(ref: string): [number, number] {
  const m = /^([A-Za-z]+)(\d+)$/.exec(ref)
  if (!m) throw new Error(`not a cell reference: ${ref}`)
  return [colNumber(m[1]), Number(m[2])]
}

/** _num: a number as the sheet carries it (a whole one without a decimal point). */
export function num(v: number | boolean): string {
  if (typeof v === 'boolean') return v ? '1' : '0'
  const f = Number(v)
  if (Number.isFinite(f) && Number.isInteger(f) && Math.abs(f) < 1e15) return String(Object.is(f, -0) ? 0 : f)
  return pyFloat(f)
}

export type Cached = number | string | null

export class SheetWriter {
  /** row to {col: cell xml} */
  rows = new Map<number, Map<number, string>>()
  heights = new Map<number, number>()
  widths = new Map<number, number>()
  merges: string[] = []
  freeze: string | null = null
  drawingRid: string | null = null
  /** [sqref, formula, dxf id], in priority order */
  conditions: [string, string, number][] = []
  orientation = 'landscape'
  /** 0: as many pages tall as needed */
  fitHeight = 0
  centerHorizontally = false

  private put(ref: string, body: string, style: number | null, cellType: string | null = null): void {
    const [col, row] = splitRef(ref)
    const attrs = ` r="${ref}"` + (style ? ` s="${style}"` : '') + (cellType ? ` t="${cellType}"` : '')
    if (!this.rows.has(row)) this.rows.set(row, new Map())
    this.rows.get(row)!.set(col, body ? `<c${attrs}>${body}</c>` : `<c${attrs}/>`)
  }

  text(ref: string, value: string | null, style: number | null = null): void {
    if (value === null || value === undefined || value === '') {
      this.put(ref, '', style)
      return
    }
    this.put(ref, `<is><t xml:space="preserve">${xmlEscape(String(value).replace(NOT_XML, ''))}</t></is>`, style, 'inlineStr')
  }

  number(ref: string, value: number | null, style: number | null = null): void {
    if (value === null || value === undefined) {
      this.put(ref, '', style)
      return
    }
    this.put(ref, `<v>${num(value)}</v>`, style)
  }

  /** A live formula with the app's value stored as its result (shown before recalculation). */
  formula(ref: string, text: string, cached: Cached = null, style: number | null = null): void {
    const f = xmlEscape(text.startsWith('=') ? text.slice(1) : text)
    if (cached === null || cached === undefined || cached === '') this.put(ref, `<f>${f}</f><v></v>`, style, 'str')
    else if (typeof cached === 'string') this.put(ref, `<f>${f}</f><v>${xmlEscape(cached)}</v>`, style, 'str')
    else this.put(ref, `<f>${f}</f><v>${num(cached)}</v>`, style)
  }

  blank(ref: string, style: number | null): void {
    this.put(ref, '', style)
  }

  merge(rng: string): void {
    this.merges.push(rng)
  }

  width(col: number, w: number): void {
    this.widths.set(col, w)
  }

  height(row: number, h: number): void {
    this.heights.set(row, h)
  }

  /** An expression rule: where ``formula`` (relative to the range's top-left cell) is true, the cell
   * takes differential format ``dxf``. */
  conditional(sqref: string, formula: string, dxf: number): void {
    this.conditions.push([sqref, formula.startsWith('=') ? formula.slice(1) : formula, dxf])
  }

  private conditionalXml(): string {
    const blocks = new Map<string, [string, number][]>()
    for (const [sqref, formula, dxf] of this.conditions) {
      if (!blocks.has(sqref)) blocks.set(sqref, [])
      blocks.get(sqref)!.push([formula, dxf])
    }
    const out: string[] = []
    let priority = 0
    for (const [sqref, rules] of blocks) {
      let body = ''
      for (const [formula, dxf] of rules) {
        priority += 1
        body += `<cfRule type="expression" dxfId="${dxf}" priority="${priority}"><formula>${xmlEscape(formula)}</formula></cfRule>`
      }
      out.push(`<conditionalFormatting sqref="${sqref}">${body}</conditionalFormatting>`)
    }
    return out.join('')
  }

  xml({ showGrid = false } = {}): string {
    const cells: [number, number][] = []
    for (const [r, cols] of this.rows) for (const c of cols.keys()) cells.push([r, c])
    let dim = '<dimension ref="A1"/>'
    if (cells.length) {
      const r1 = Math.min(...cells.map(([r]) => r))
      const r2 = Math.max(...cells.map(([r]) => r))
      const c1 = Math.min(...cells.map(([, c]) => c))
      const c2 = Math.max(...cells.map(([, c]) => c))
      dim = `<dimension ref="${colLetter(c1)}${r1}:${colLetter(c2)}${r2}"/>`
    }
    let view = '<sheetView workbookViewId="0"' + (showGrid ? '' : ' showGridLines="0"') + '>'
    if (this.freeze) {
      const [, row] = splitRef(this.freeze)
      view += `<pane ySplit="${row - 1}" topLeftCell="${this.freeze}" activePane="bottomLeft" state="frozen"/>`
        + `<selection pane="bottomLeft" activeCell="${this.freeze}" sqref="${this.freeze}"/>`
    }
    view += '</sheetView>'
    let cols = ''
    if (this.widths.size) {
      cols = '<cols>' + [...this.widths].sort((a, b) => a[0] - b[0])
        .map(([c, w]) => `<col min="${c}" max="${c}" width="${pyG(w)}" customWidth="1"/>`).join('') + '</cols>'
    }
    const rows: [number, string][] = []
    for (const r of [...this.rows.keys()].sort((a, b) => a - b)) {
      const ht = this.heights.has(r) ? ` ht="${pyG(this.heights.get(r)!)}" customHeight="1"` : ''
      const cs = this.rows.get(r)!
      rows.push([r, `<row r="${r}"${ht}>` + [...cs.keys()].sort((a, b) => a - b).map((c) => cs.get(c)).join('') + '</row>'])
    }
    for (const r of [...this.heights.keys()].filter((r) => !this.rows.has(r)).sort((a, b) => a - b)) {
      rows.push([r, `<row r="${r}" ht="${pyG(this.heights.get(r)!)}" customHeight="1"/>`])
    }
    rows.sort((a, b) => a[0] - b[0])
    const merges = this.merges.length
      ? `<mergeCells count="${this.merges.length}">` + this.merges.map((m) => `<mergeCell ref="${m}"/>`).join('') + '</mergeCells>' : ''
    const drawing = this.drawingRid ? `<drawing r:id="${this.drawingRid}"/>` : ''
    const options = this.centerHorizontally ? '<printOptions horizontalCentered="1"/>' : ''
    const margins = '<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>'
    const setup = `<pageSetup orientation="${this.orientation}" fitToWidth="1" fitToHeight="${Math.trunc(this.fitHeight)}"/>`
    return `<worksheet xmlns="${NS_MAIN}" xmlns:r="${NS_R}">`
      + '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
      + `${dim}<sheetViews>${view}</sheetViews>`
      + '<sheetFormatPr defaultRowHeight="15"/>'
      + `${cols}<sheetData>${rows.map(([, x]) => x).join('')}</sheetData>${merges}${this.conditionalXml()}`
      + `${options}${margins}${setup}${drawing}</worksheet>`
  }
}

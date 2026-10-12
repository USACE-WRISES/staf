// reportlab's platypus/tables.py, the paths the reports take: cell styles set by commands, rows sized
// from their cells (a string cell is its leading per line, a flowable cell its wrapped height, both plus
// padding), row spans growing their rows evenly (spanFixDim), row positions summed with Kahan's
// compensation, backgrounds, cells and lines drawn in reportlab's order (lines skip spanned cells), and a
// table split by rows between frames with its header rows repeated. Rounded corners, shadows, gradients,
// split-in-row and the special split rows are not ported (the reports use none).
import { pySumMixed } from '../../app/pyfmt'
import type { Canvas } from './canvas'
import { type Color, toColor } from './colors'
import { Flowable, FUZZ } from './flowables'
import { stringWidth } from './fonts'

export type CellValue = string | Flowable | CellValue[] | null
type XY = [number, number]
export type Cmd = [string, XY, XY, ...unknown[]]
/** a line command padded as _addCommand pads it: op, start, stop, weight, color, cap, dash, join, count, space */
type LineCmd = [string, XY, XY, number, Color | string | null, number | null, number[] | null, number | null, number, number]

const LINECAPS: Record<string, number> = { butt: 0, round: 1, projecting: 2, squared: 2 }
const LINEJOINS: Record<string, number> = { miter: 0, mitre: 0, round: 1, bevel: 2 }
const LINE_OPS: Record<string, string> = {
  GRID: 'grid', BOX: 'box', OUTLINE: 'box', INNERGRID: 'innerGrid', LINEBELOW: 'hLinesB', LINEABOVE: 'hLines', LINEBEFORE: 'vLines', LINEAFTER: 'vLinesA',
}

/** CellStyle: one per cell, changed in place by the style commands. */
export class CellStyle {
  fontname = 'Helvetica'
  fontsize = 10
  leading = 12
  leftPadding = 6
  rightPadding = 6
  topPadding = 3
  bottomPadding = 3
  firstLineIndent = 0
  color: Color | string = 'black'
  alignment = 'LEFT'
  valign = 'BOTTOM'
  href: string | null = null

  constructor(readonly name: string) {}
}

export class TableStyle {
  cmds: Cmd[]

  constructor(cmds: Cmd[] = []) {
    this.cmds = [...cmds]
  }

  add(...cmd: Cmd): void {
    this.cmds.push(cmd)
  }

  getCommands(): Cmd[] {
    return this.cmds
  }
}

function convert2int(value: unknown, map: Record<string, number>, low: number, high: number, name: string): number {
  if (typeof value === 'string' && value in map) return map[value]
  const iv = typeof value === 'number' ? Math.trunc(value) : parseInt(String(value), 10)
  if (!Number.isNaN(iv) && low <= iv && iv <= high) return iv
  throw new Error(`Bad ${name} value ${value}`)
}

/** _hLine: a line from scp to ecp at y, not through the spanned blocks at y. */
function hLine(canvLine: (x0: number, y0: number, x1: number, y1: number) => void, scp: number, ecp: number, y: number,
  blocks: Map<number, [number, number][]> | null): void {
  const hb = blocks ? blocks.get(y) : undefined
  if (!hb || !hb.length || scp >= hb[hb.length - 1][1] - FUZZ || ecp <= hb[0][0] + FUZZ) {
    canvLine(scp, y, ecp, y)
    return
  }
  let i = 0
  const n = hb.length
  while (scp < ecp - FUZZ && i < n) {
    const [x0, x1] = hb[i]
    if (x1 <= scp + FUZZ || x0 >= ecp - FUZZ) {
      i += 1
      continue
    }
    const i0 = Math.max(scp, x0)
    const i1 = Math.min(ecp, x1)
    if (i0 > scp) canvLine(scp, y, i0, y)
    scp = i1
  }
  if (scp < ecp - FUZZ) canvLine(scp, y, ecp, y)
}

/** spanFixDim: rows (or columns) a span needs more room from share the extra equally. */
function spanFixDim(V0: (number | null)[], V: number[], spanCons: Map<string, number>, lim: number | null = null): void {
  const M = new Map<number, number>()
  if (!lim) lim = V0.length
  const items = [...spanCons.entries()].map(([k, v]) => [v, ...k.split(',').map(Number)] as [number, number, number])
  items.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2])
  items.reverse()
  for (const [v0, x0, x1e] of items) {
    let v = v0
    if (x0 >= lim) continue
    const x1 = x1e + 1
    const terms: number[] = []
    for (let x = x0; x < x1; x++) terms.push(V[x] + (M.get(x) ?? 0))
    const t = pySumMixed(terms)
    if (t >= v - FUZZ) continue
    const X: number[] = []
    for (let x = x0; x < x1; x++) if (V0[x] === null) X.push(x)
    if (!X.length) continue
    v -= t
    v /= X.length
    for (const x of X) M.set(x, (M.get(x) ?? 0) + v)
  }
  for (const [x, v] of M) V[x] += v
}

const key = (x: number, y: number) => `${x},${y}`

function flatten(v: CellValue[], out: (string | Flowable | null)[] = []): (string | Flowable | null)[] {
  for (const x of v) {
    if (Array.isArray(x)) flatten(x, out)
    else out.push(x)
  }
  return out
}

export interface TableOptions {
  colWidths?: (number | null)[] | null
  rowHeights?: (number | null)[] | null
  style?: TableStyle | Cmd[] | null
  repeatRows?: number
  hAlign?: string | null
  vAlign?: string | null
  spaceBefore?: number | null
  spaceAfter?: number | null
  normalizedData?: boolean
  cellStyles?: CellStyle[][] | null
}

export class Table extends Flowable {
  nrows: number
  ncols: number
  cellvalues: CellValue[][]
  argW: (number | null)[]
  argH: (number | null)[]
  colWidths: (number | null)[]
  rowHeights: (number | null)[]
  cellStyles: CellStyle[][]
  bkgrndcmds: Cmd[] = []
  linecmds: LineCmd[] = []
  spanCmds: Cmd[] = []
  repeatRows: number
  vAlign: string
  private widthCalculatedOnce = false
  colpositions: number[] = []
  rowpositions: number[] = []
  private tWidth = 0
  private tHeight = 0
  private hmax = 0
  private spanRanges: Map<string, [number, number, number, number] | null> | null = null
  private colSpanCells = new Set<string>()
  private rowSpanCells = new Set<string>()
  private spanRects: Map<string, [number, number, number, number] | null> | null = null
  private hmaxSpanRects: number | null = null
  private vBlocks = new Map<number, [number, number][]>()
  private hBlocks = new Map<number, [number, number][]>()
  private curweight: number | null = null
  private curcolor: Color | string | null = null
  private curcellstyle: CellStyle | null = null

  constructor(data: CellValue[][], opts: TableOptions = {}) {
    super()
    this.hAlign = opts.hAlign || 'CENTER'
    this.vAlign = opts.vAlign || 'MIDDLE'
    const nrows = this.nrows = data.length
    const ncols = this.ncols = nrows ? Math.max(...data.map((r) => r.length)) : 0
    if (!(nrows && ncols)) throw new Error('Table must have at least a row and column')
    this.cellvalues = data = opts.normalizedData ? data : data.map((row) => row.map((c) => (c === null ? '' : c)))
    let colWidths = opts.colWidths ?? null
    if (colWidths === null) colWidths = new Array(ncols).fill(null)
    else if (colWidths.length !== ncols) throw new Error(`data error - ${ncols} columns in data but ${colWidths.length} column widths`)
    let rowHeights = opts.rowHeights ?? null
    if (rowHeights === null) rowHeights = new Array(nrows).fill(null)
    else if (rowHeights.length !== nrows) throw new Error(`data error - ${nrows} rows in data but ${rowHeights.length} row heights`)
    data.forEach((d, i) => {
      if (d.length !== ncols) throw new Error(`expected ${ncols} not ${d.length} columns in row ${i}!`)
    })
    this.rowHeights = this.argH = rowHeights
    this.colWidths = this.argW = colWidths
    this.cellStyles = opts.cellStyles ?? Array.from({ length: nrows }, (_, i) => Array.from({ length: ncols }, (__, j) => new CellStyle(`(${i}, ${j})`)))
    this.repeatRows = opts.repeatRows ?? 0
    if (opts.style) this.setStyle(opts.style)
    if (opts.spaceBefore !== null && opts.spaceBefore !== undefined) this.spaceBefore = opts.spaceBefore
    if (opts.spaceAfter !== null && opts.spaceAfter !== undefined) this.spaceAfter = opts.spaceAfter
  }

  setStyle(tblstyle: TableStyle | Cmd[]): void {
    const cmds = tblstyle instanceof TableStyle ? tblstyle.getCommands() : tblstyle
    for (const cmd of cmds) {
      if (cmd.length >= 3 && [cmd[1][0], cmd[1][1], cmd[2][0], cmd[2][1]].some((v) => typeof v === 'string')) {
        throw new Error(`bad style command ${JSON.stringify(cmd)}: the special split rows are not supported here`)
      }
      this.addCommand(cmd)
    }
  }

  normCellRange(sc: number, ec: number, sr: number, er: number): [number, number, number, number] {
    if (sc < 0) sc += this.ncols
    if (ec < 0) ec += this.ncols
    if (sr < 0) sr += this.nrows
    if (er < 0) er += this.nrows
    return [Math.max(0, sc), Math.min(this.ncols - 1, ec), Math.max(0, sr), Math.min(this.nrows - 1, er)]
  }

  addCommand(cmd: Cmd | LineCmd): void {
    const op = cmd[0]
    if (op === 'BACKGROUND' || op === 'ROWBACKGROUNDS' || op === 'COLBACKGROUNDS') this.bkgrndcmds.push(cmd as Cmd)
    else if (op === 'SPAN') this.spanCmds.push(cmd as Cmd)
    else if (op === 'NOSPLIT') throw new Error('NOSPLIT is not supported here')
    else if (op in LINE_OPS) {
      const c = [...cmd] as unknown[]
      if (c.length < 5) throw new Error(`bad line command ${JSON.stringify(cmd)}`)
      if (c.length < 6) c.push(1)
      else c[5] = convert2int(c[5], LINECAPS, 0, 2, 'cap')
      if (c.length < 7) c.push(null)
      if (c.length < 8) c.push(1)
      else c[7] = convert2int(c[7], LINEJOINS, 0, 2, 'join')
      if (c.length < 9) c.push(1)
      else if (c[8] === null) c[8] = 1
      if (c.length < 10) c.push(c[3])
      else if (c[9] === null) c[9] = c[3]
      this.linecmds.push(c as LineCmd)
    } else if (op === 'ROUNDEDCORNERS') throw new Error('ROUNDEDCORNERS is not supported here')
    else {
      const [, [sc0, sr0], [ec0, er0], ...values] = cmd as Cmd
      const [sc, ec, sr, er] = this.normCellRange(sc0, ec0, sr0, er0)
      for (let i = sr; i <= er; i++) for (let j = sc; j <= ec; j++) setCellStyle(this.cellStyles[i][j], op, values)
    }
  }

  // ---------------------------------------------------------------- sizing
  /** _cellListProcess: a flowable cell as a flat list of flowables */
  private cellList(v: CellValue): Flowable[] {
    const C = v instanceof Flowable ? [v] : flatten(v as CellValue[])
    for (const c of C) if (!(c instanceof Flowable)) throw new Error('a table cell list may hold only flowables here')
    return C as Flowable[]
  }

  /** _listCellGeom: the widest and the total height of a cell's flowables, wrapped inside its padding */
  private listCellGeom(V: Flowable[], w: number, s: CellStyle, W: number[] | null = null, H: number[] | null = null, aH = 72000): [number, number] {
    if (!V.length) return [0, 0]
    const aW = w - s.leftPadding - s.rightPadding
    aH = aH - s.topPadding - s.bottomPadding
    let t = 0
    let mw = 0
    let sb0: number | null = null
    let sa = 0
    for (const v of V) {
      const [vw, vh] = v.wrapOn(this.canv, aW, aH)
      const sb = v.getSpaceBefore()
      sa = v.getSpaceAfter()
      if (W !== null) W.push(vw)
      if (H !== null) H.push(vh)
      mw = Math.max(mw, vw)
      t += vh + sa + sb
      if (sb0 === null) sb0 = sb
    }
    return [mw, t - (sb0 ?? 0) - sa]
  }

  /** _elementWidth: the width a cell's content needs (auto-sized columns) */
  private elementWidth(v: CellValue, s: CellStyle): number | null {
    if (Array.isArray(v)) {
      let w = 0
      for (const e of v) {
        const ew = this.elementWidth(e, s)
        if (ew === null) return null
        w = Math.max(w, ew)
      }
      return w
    }
    if (v instanceof Flowable) return v.minWidth()
    if (v === null) return 0
    return Math.max(...String(v).split('\n').map((x) => stringWidth(x, s.fontname, s.fontsize)))
  }

  private calcWidth(): void {
    if (this.widthCalculatedOnce) return
    const W = [...this.argW]
    if (W.includes(null)) {
      const spanCons = new Map<string, number>()
      const W0 = [...W]
      while (W.includes(null)) {
        const j = W.indexOf(null)
        let w = 0
        this.cellvalues.forEach((Vi, i) => {
          const v = Vi[j]
          const s = this.cellStyles[i][j]
          const span = this.spanRanges ? this.spanRanges.get(key(j, i)) : undefined
          let t: number
          if (this.colSpanCells.has(key(j, i)) && !span) t = 0.0
          else {
            const ew = this.elementWidth(v, s)
            if (ew === null) throw new Error(`Flowable in cell(${i},${j}) can't have auto width`)
            t = ew + s.leftPadding + s.rightPadding
            if (span) {
              const [c0, , c1] = span
              if (c0 !== c1) {
                const x = `${c0},${c1}`
                spanCons.set(x, Math.max(spanCons.get(x) ?? t, t))
                t = 0
              }
            }
          }
          if (t > w) w = t
        })
        W[j] = w
      }
      if (spanCons.size) spanFixDim(W0, W as number[], spanCons)
    }
    this.colWidths = W
    let width = 0
    this.colpositions = [0]
    for (const w of W as number[]) {
      width = width + w
      this.colpositions.push(width)
    }
    this.tWidth = width
    this.widthCalculatedOnce = true
  }

  private calcHeight(availHeight: number): void {
    let H = this.argH
    const W = this.colWidths.some((w) => w === null) ? [...this.argW] : this.argW
    let hmax = H.length
    const lim = hmax
    if (H.includes(null)) {
      const spans = this.spanCmds.length > 0
      const H0 = H
      H = [...H]
      this.rowHeights = H
      const spanCons = new Map<string, number>()
      while (H.includes(null)) {
        const i = H.indexOf(null)
        const V = this.cellvalues[i]
        const S = this.cellStyles[i]
        let h = 0
        for (let j = 0; j < Math.min(V.length, S.length, W.length); j++) {
          let v = V[j]
          const s = S[j]
          let w = W[j]
          const ji = key(j, i)
          const span = spans ? this.spanRanges!.get(ji) : undefined
          if (spans && this.rowSpanCells.has(ji) && !span) continue
          let t: number
          if (Array.isArray(v) || v instanceof Flowable) {
            const list = this.cellList(v)
            v = V[j] = list
            if (spans && this.colSpanCells.has(ji)) {
              if (!span) continue
              w = Math.max(this.colpositions[span[2] + 1] - this.colpositions[span[0]], w ?? 0)
            }
            if (w === null) throw new Error(`Flowable in cell(${i},${j}) can't have auto width`)
            t = this.listCellGeom(list, w, s)[1]
          } else {
            const lines = (v !== null && v !== '' ? String(v) : '').split('\n')
            t = (s.leading || 1.2 * s.fontsize) * lines.length
          }
          t += s.bottomPadding + s.topPadding
          if (span) {
            const r0 = span[1]
            const r1 = span[3]
            if (r0 !== r1) {
              const x = `${r0},${r1}`
              spanCons.set(x, Math.max(spanCons.get(x) ?? t, t))
              t = 0
            }
          }
          if (t > h) h = t
        }
        H[i] = h
        hmax = i + 1
        const height = pySumMixed(H.slice(0, hmax) as number[])
        if (height > availHeight && spanCons.size) {
          const msr = Math.max(...[...spanCons.keys()].map((k) => Number(k.split(',')[1])))
          if (hmax > msr) break
        }
      }
      if (!H.includes(null)) hmax = lim
      if (spanCons.size) spanFixDim(H0, H as number[], spanCons)
    }
    // row positions bottom up, Kahan-compensated
    const j: number[] = []
    let height = 0
    let c = 0
    for (let i = hmax - 1; i >= 0; i--) {
      j.push(height)
      const y = (H[i] as number) - c
      const t = height + y
      c = (t - height) - y
      height = t
    }
    j.push(height)
    this.tHeight = height
    j.reverse()
    this.rowpositions = j
    this.hmax = hmax
  }

  private calcSpanRanges(): void {
    const spanRanges = this.spanRanges = new Map()
    for (let x = 0; x < this.ncols; x++) for (let y = 0; y < this.nrows; y++) spanRanges.set(key(x, y), [x, y, x, y])
    this.colSpanCells = new Set()
    this.rowSpanCells = new Set()
    for (const [, start, stop] of this.spanCmds) {
      let [x0, y0] = start
      let [x1, y1] = stop
      if (x0 < 0) x0 += this.ncols
      if (x1 < 0) x1 += this.ncols
      if (y0 < 0) y0 += this.nrows
      if (y1 < 0) y1 += this.nrows
      if (x0 > x1) [x0, x1] = [x1, x0]
      if (y0 > y1) [y0, y1] = [y1, y0]
      if (x0 !== x1 || y0 !== y1) {
        if (x0 !== x1) for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) this.colSpanCells.add(key(x, y))
        if (y0 !== y1) for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) this.rowSpanCells.add(key(x, y))
        for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) spanRanges.set(key(x, y), null)
        spanRanges.set(key(x0, y0), [x0, y0, x1, y1])
      }
    }
  }

  private calcSpanRects(): void {
    const spanRects = this.spanRects ?? new Map<string, [number, number, number, number] | null>()
    if (spanRects.size && this.hmax === this.hmaxSpanRects) return
    const colpositions = this.colpositions
    const rowpositions = this.rowpositions
    const vBlocks = new Map<number, [number, number][]>()
    const hBlocks = new Map<number, [number, number][]>()
    const rlim = rowpositions.length - 1
    const push = (m: Map<number, [number, number][]>, k: number, v: [number, number]) => {
      const a = m.get(k)
      if (a) a.push(v)
      else m.set(k, [v])
    }
    for (const [coord, value] of this.spanRanges!) {
      if (value === null) spanRects.set(coord, null)
      else {
        const [col0, row0, col1, row1] = value
        if (row1 >= rlim) continue
        if (col1 - col0 > 0) for (let c = col0 + 1; c <= col1; c++) push(vBlocks, colpositions[c], [rowpositions[row1 + 1], rowpositions[row0]])
        if (row1 - row0 > 0) for (let r = row0 + 1; r <= row1; r++) push(hBlocks, rowpositions[r], [colpositions[col0], colpositions[col1 + 1]])
        const x = colpositions[col0]
        const y = rowpositions[row1 + 1]
        spanRects.set(coord, [x, y, colpositions[col1 + 1] - x, rowpositions[row0] - y])
      }
    }
    for (const m of [hBlocks, vBlocks]) for (const v of m.values()) v.sort((a, b) => a[0] - b[0] || a[1] - b[1])
    this.spanRects = spanRects
    this.vBlocks = vBlocks
    this.hBlocks = hBlocks
    this.hmaxSpanRects = this.hmax
  }

  private calc(_availWidth: number, availHeight: number): void {
    if (this.spanCmds.length) {
      this.calcSpanRanges()
      if (this.argH.includes(null)) this.calcWidth()
    }
    this.calcHeight(availHeight)
    this.calcWidth()
    if (this.spanCmds.length) this.calcSpanRects()
  }

  wrap(availWidth: number, availHeight: number): [number, number] {
    this.calc(availWidth, availHeight)
    return [this.tWidth, this.tHeight]
  }

  // ---------------------------------------------------------------- splitting by rows
  private firstPossibleSplitRowPosition(availHeight: number): number {
    const impossible = new Set<number>()
    if (this.spanCmds.length) {
      for (const xy of this.rowSpanCells) {
        const r = this.spanRanges!.get(xy)
        if (r) {
          const [, y1, , y2] = r
          if (y1 !== y2) for (let y = Math.min(y1, y2) + 1; y <= Math.max(y1, y2); y++) impossible.add(y)
        }
      }
    }
    let h = 0
    let n = 1
    let splitAt = 0
    for (const rh of this.rowHeights as number[]) {
      if (h + rh > availHeight) break
      if (!impossible.has(n)) splitAt = n
      h = h + rh
      n = n + 1
    }
    return splitAt
  }

  /** _splitLineCmds: the line commands with each box or grid crossing row n cut there */
  private splitLineCmds(n: number): LineCmd[] {
    const A: LineCmd[] = []
    for (const [op, [sc0, sr0], [ec0, er0], ...rest] of this.linecmds) {
      let [sc, sr, ec, er] = [sc0, sr0, ec0, er0]
      if (sc < 0) sc += this.ncols
      if (ec < 0) ec += this.ncols
      if (sr < 0) sr += this.nrows
      if (er < 0) er += this.nrows
      const L = (o: string, s: XY, e: XY): LineCmd => [o, s, e, ...rest] as LineCmd
      if (op === 'BOX' || op === 'OUTLINE' || op === 'GRID') {
        if (sr < n && er >= n) {
          A.push(L('LINEABOVE', [sc, sr], [ec, sr]), L('LINEBEFORE', [sc, sr], [sc, er]), L('LINEAFTER', [ec, sr], [ec, er]),
            L('LINEBELOW', [sc, er], [ec, er]))
          if (op === 'GRID') A.push(L('LINEBELOW', [sc, n - 1], [ec, n - 1]), L('LINEABOVE', [sc, n], [ec, n]), L('INNERGRID', [sc, sr], [ec, er]))
        } else A.push(L(op, [sc, sr], [ec, er]))
      } else if (op === 'INNERGRID') {
        if (sr < n && er >= n) A.push(L('LINEBELOW', [sc, n - 1], [ec, n - 1]), L('LINEABOVE', [sc, n], [ec, n]))
        A.push(L(op, [sc, sr], [ec, er]))
      } else if (op === 'LINEBELOW') {
        if (sr < n && er >= n - 1) A.push(L('LINEABOVE', [sc, n], [ec, n]))
        A.push(L(op, [sc, sr], [ec, er]))
      } else if (op === 'LINEABOVE') {
        if (sr <= n && er >= n) A.push(L('LINEBELOW', [sc, n - 1], [ec, n - 1]))
        A.push(L(op, [sc, sr], [ec, er]))
      } else A.push(L(op, [sc, sr], [ec, er]))
    }
    return A
  }

  /** _cr_0: the commands that reach the first part's rows, cut at its end */
  private cr0(n: number, cmds: (Cmd | LineCmd)[], nr0: number): void {
    for (const c of cmds) {
      let [sr, er] = [c[1][1], c[2][1]]
      if (sr < 0) sr += nr0
      if (sr >= n) continue
      if (er >= n) er = n - 1
      this.addCommand([c[0], [c[1][0], sr], [c[2][0], er], ...c.slice(3)] as Cmd)
    }
  }

  /** _cr_1_0: the commands that reach the second part's rows, shifted up (no repeated rows) */
  private cr10(n: number, cmds: (Cmd | LineCmd)[]): void {
    for (const c of cmds) {
      let [sr, er] = [c[1][1], c[2][1]]
      if (er >= 0 && er < n) continue
      if (sr >= 0 && sr < n) sr = 0
      if (sr >= n) sr -= n
      if (er >= n) er -= n
      this.addCommand([c[0], [c[1][0], sr], [c[2][0], er], ...c.slice(3)] as Cmd)
    }
  }

  /** _cr_1_1: the same with the repeated header rows first */
  private cr11(n: number, nRows: number, repeatRows: number[], cmds: (Cmd | LineCmd)[]): void {
    const nrr = repeatRows.length
    const rrS = new Set(repeatRows)
    for (const c of cmds) {
      const sc = c[1][0]
      const ec = c[2][0]
      let [sr, er] = [c[1][1], c[2][1]]
      if (sr < 0) sr += nRows
      if (er < 0) er += nRows
      const cS: number[] = []
      for (let r = sr; r <= er; r++) if (rrS.has(r)) cS.push(r)
      if (cS.length) {
        this.addCommand([c[0], [sc, repeatRows.indexOf(Math.min(...cS))], [ec, repeatRows.indexOf(Math.max(...cS))], ...c.slice(3)] as Cmd)
      }
      if (er < n) continue
      sr = Math.max(sr - n, 0) + nrr
      er = Math.max(er - n, 0) + nrr
      this.addCommand([c[0], [sc, sr], [ec, er], ...c.slice(3)] as Cmd)
    }
  }

  private splitRows(availHeight: number): Flowable[] {
    const n = this.firstPossibleSplitRowPosition(availHeight)
    const repeatRows = this.repeatRows
    if (n <= repeatRows) return []
    const lim = this.rowHeights.length
    if (n === lim) return [this]
    const data = this.cellvalues
    const splitH = this.rowHeights
    const R0 = new Table(data.slice(0, n), {
      colWidths: this.colWidths, rowHeights: splitH.slice(0, n), repeatRows, normalizedData: true, cellStyles: this.cellStyles.slice(0, n),
      spaceBefore: this.spaceBefore ?? null,
    })
    const nrows = this.nrows
    const linecmds = this.splitLineCmds(n)
    R0.cr0(n, linecmds, nrows)
    R0.cr0(n, this.bkgrndcmds, nrows)
    R0.cr0(n, this.spanCmds, nrows)
    let R1: Table
    if (repeatRows) {
      const rr = Array.from({ length: repeatRows }, (_, i) => i)
      R1 = new Table([...data.slice(0, repeatRows), ...data.slice(n)], {
        colWidths: this.colWidths, rowHeights: [...splitH.slice(0, repeatRows), ...splitH.slice(n)], repeatRows: rr.length, normalizedData: true,
        cellStyles: [...this.cellStyles.slice(0, repeatRows), ...this.cellStyles.slice(n)], spaceAfter: this.spaceAfter ?? null,
      })
      R1.cr11(n, nrows, rr, linecmds)
      R1.cr11(n, nrows, rr, this.bkgrndcmds)
      R1.cr11(n, nrows, rr, this.spanCmds)
    } else {
      R1 = new Table(data.slice(n), {
        colWidths: this.colWidths, rowHeights: splitH.slice(n), repeatRows, normalizedData: true, cellStyles: this.cellStyles.slice(n),
        spaceAfter: this.spaceAfter ?? null,
      })
      R1.cr10(n, linecmds)
      R1.cr10(n, this.bkgrndcmds)
      R1.cr10(n, this.spanCmds)
    }
    R0.hAlign = R1.hAlign = this.hAlign
    R0.vAlign = R1.vAlign = this.vAlign
    return [R0, R1]
  }

  split(availWidth: number, availHeight: number): Flowable[] {
    this.calc(availWidth, availHeight)
    return this.splitRows(availHeight)
  }

  // ---------------------------------------------------------------- drawing
  draw(): void {
    const c = this.canv!
    c.saveState()
    this.curweight = this.curcolor = this.curcellstyle = null
    this.drawBkgrnd()
    if (!this.spanCmds.length) {
      // zip(): rows past the computed ones (a long table not yet split) are not drawn
      const nr = Math.min(this.nrows, this.rowpositions.length - 1, this.rowHeights.length)
      for (let r = 0; r < nr; r++) {
        for (let k = 0; k < this.ncols; k++) {
          this.drawCell(this.cellvalues[r][k], this.cellStyles[r][k], [this.colpositions[k], this.rowpositions[r + 1]],
            [this.colWidths[k] as number, this.rowHeights[r] as number])
        }
      }
    } else {
      for (let rowNo = 0; rowNo < this.nrows; rowNo++) {
        for (let colNo = 0; colNo < this.ncols; colNo++) {
          const cellRect = this.spanRects!.get(key(colNo, rowNo))
          if (cellRect === undefined) throw new Error(`KeyError: (${colNo}, ${rowNo})`)
          if (cellRect !== null) {
            const [x, y, width, height] = cellRect
            this.drawCell(this.cellvalues[rowNo][colNo], this.cellStyles[rowNo][colNo], [x, y], [width, height])
          }
        }
      }
    }
    this.drawLines()
    c.restoreState()
  }

  private drawBkgrnd(): void {
    const nrows = this.nrows
    const ncols = this.ncols
    const canv = this.canv!
    const colpositions = this.colpositions
    const rowpositions = this.rowpositions
    const spanRects = this.spanRects
    for (const [cmd, [sc0, sr0], [ec0, er0], arg] of this.bkgrndcmds) {
      let [sc, sr, ec, er] = [sc0, sr0, ec0, er0]
      if (sc < 0) sc += ncols
      if (ec < 0) ec += ncols
      if (sr < 0) sr += nrows
      if (er < 0) er += nrows
      let x0 = colpositions[sc]
      let y0 = rowpositions[sr]
      const x1 = colpositions[Math.min(ec + 1, ncols)]
      const y1 = rowpositions[Math.min(er + 1, nrows)]
      let w = x1 - x0
      let h = y1 - y0
      if (cmd === 'ROWBACKGROUNDS') {
        const colorCycle = (arg as (Color | string | null)[]).map((a) => (a === null || a === 'None' ? null : toColor(a)))
        const count = colorCycle.length
        for (let i = 0; i < er - sr + 1; i++) {
          const color = colorCycle[i % count]
          const rh = this.rowHeights[sr + i] as number
          if (color) {
            canv.setFillColor(color)
            canv.rect(x0, y0, w, -rh, 0, 1)
          }
          y0 = y0 - rh
        }
      } else if (cmd === 'COLBACKGROUNDS') {
        const colorCycle = (arg as (Color | string | null)[]).map((a) => (a === null || a === 'None' ? null : toColor(a)))
        const count = colorCycle.length
        for (let i = 0; i < ec - sc + 1; i++) {
          const color = colorCycle[i % count]
          const cw = this.colWidths[sc + i] as number
          if (color) {
            canv.setFillColor(color)
            canv.rect(x0, y0, cw, h, 0, 1)
          }
          x0 = x0 + cw
        }
      } else {
        if (Array.isArray(arg)) throw new Error('gradient backgrounds are not supported here')
        const color = arg === null || arg === undefined ? null : toColor(arg as Color | string)
        if (color) {
          if (ec === sc && er === sr && spanRects) {
            const xywh = spanRects.get(key(sc, sr))
            if (xywh) [x0, y0, w, h] = xywh
          }
          canv.setFillColor(color)
          canv.rect(x0, y0, w, h, 0, 1)
        }
      }
    }
  }

  private drawCell(cellval: CellValue, cellstyle: CellStyle, pos: [number, number], size: [number, number]): void {
    const canv = this.canv!
    const [colpos, rowpos] = pos
    const [colwidth, rowheight] = size
    if (this.curcellstyle !== cellstyle) {
      const cur = this.curcellstyle
      if (cur === null || !sameColor(cellstyle.color, cur.color)) canv.setFillColor(cellstyle.color)
      if (cur === null || cellstyle.leading !== cur.leading || cellstyle.fontname !== cur.fontname || cellstyle.fontsize !== cur.fontsize) {
        canv.setFont(cellstyle.fontname, cellstyle.fontsize, cellstyle.leading)
      }
      this.curcellstyle = cellstyle
    }
    const just = cellstyle.alignment
    const valign = cellstyle.valign
    if (Array.isArray(cellval) || cellval instanceof Flowable) {
      const cells = this.cellList(cellval)
      const W: number[] = []
      const H: number[] = []
      const [, h] = this.listCellGeom(cells, colwidth, cellstyle, W, H, rowheight)
      let y: number
      if (valign === 'TOP') y = rowpos + rowheight - cellstyle.topPadding
      else if (valign === 'BOTTOM') y = rowpos + cellstyle.bottomPadding + h
      else y = rowpos + (rowheight + cellstyle.bottomPadding - cellstyle.topPadding + h) / 2.0
      if (cells.length) y += cells[0].getSpaceBefore()
      cells.forEach((v, i) => {
        const w = W[i]
        const vh = H[i]
        let x: number
        if (just === 'LEFT') x = colpos + cellstyle.leftPadding
        else if (just === 'RIGHT') x = colpos + colwidth - cellstyle.rightPadding - w
        else if (just === 'CENTRE' || just === 'CENTER') x = colpos + (colwidth + cellstyle.leftPadding - cellstyle.rightPadding - w) / 2.0
        else throw new Error(`Invalid justification ${just}`)
        y -= v.getSpaceBefore()
        y -= vh
        v.drawOn(canv, x, y)
        y -= v.getSpaceAfter()
      })
    } else {
      let draw: (x: number, y: number, t: string) => void
      let x: number
      if (just === 'LEFT') {
        draw = (a, b, t) => canv.drawString(a, b, t)
        x = colpos + cellstyle.leftPadding
      } else if (just === 'CENTRE' || just === 'CENTER') {
        draw = (a, b, t) => canv.drawCentredString(a, b, t)
        x = colpos + (colwidth + cellstyle.leftPadding - cellstyle.rightPadding) * 0.5
      } else if (just === 'RIGHT') {
        draw = (a, b, t) => canv.drawRightString(a, b, t)
        x = colpos + colwidth - cellstyle.rightPadding
      } else throw new Error(`Invalid justification ${just}`)
      const vals = String(cellval).split('\n')
      const n = vals.length
      const leading = cellstyle.leading
      const fontsize = cellstyle.fontsize
      let y: number
      if (valign === 'BOTTOM') y = rowpos + cellstyle.bottomPadding + n * leading - fontsize
      else if (valign === 'TOP') y = rowpos + rowheight - cellstyle.topPadding - fontsize
      else if (valign === 'MIDDLE') y = rowpos + (cellstyle.bottomPadding + rowheight - cellstyle.topPadding + n * leading) / 2.0 - fontsize
      else throw new Error(`Bad valign: ${valign}`)
      for (const v of vals) {
        draw(x, y, v)
        y -= leading
      }
    }
    if (cellstyle.href) canv.linkURL(cellstyle.href, [colpos, rowpos, colpos + colwidth, rowpos + rowheight], 1)
  }

  private drawLines(): void {
    let ccap: number | null = null
    let cdash: number[] | null = null
    let cjoin: number | null = null
    const canv = this.canv!
    canv.saveState()
    for (const [op, [sc0, sr0], [ec0, er0], weight, color, cap, dash, join, count, space] of this.linecmds) {
      if (cap !== null && ccap !== cap) {
        canv.setLineCap(cap)
        ccap = cap
      }
      if (dash === null || dash.length === 0) {
        if (cdash !== null) {
          canv.setDash()
          cdash = null
        }
      } else if (JSON.stringify(dash) !== JSON.stringify(cdash)) {
        canv.setDash(dash)
        cdash = dash
      }
      if (join !== null && cjoin !== join) {
        canv.setLineJoin(join)
        cjoin = join
      }
      const [sc, ec, sr, er] = this.normCellRange(sc0, ec0, sr0, er0)
      if (count !== 1) throw new Error('multiple line counts are not supported here')
      void space
      const s: XY = [sc, sr]
      const e: XY = [ec, er]
      switch (LINE_OPS[op]) {
        case 'grid':
          this.drawBox(s, e, weight, color)
          this.drawInnerGrid(s, e, weight, color)
          break
        case 'box': this.drawBox(s, e, weight, color); break
        case 'innerGrid': this.drawInnerGrid(s, e, weight, color); break
        case 'hLinesB': this.drawHLines([sc, sr + 1], [ec, er + 1], weight, color); break
        case 'hLines': this.drawHLines(s, e, weight, color); break
        case 'vLines': this.drawVLines(s, e, weight, color); break
        case 'vLinesA': this.drawVLines([sc + 1, sr], [ec + 1, er], weight, color); break
        default: throw new Error(`Unknown line command ${op}`)
      }
    }
    canv.restoreState()
    this.curcolor = null
  }

  private drawBox([sc, sr]: XY, [ec, er]: XY, weight: number, color: Color | string | null): void {
    this.drawHLines([sc, sr], [ec, sr], weight, color)
    this.drawHLines([sc, er + 1], [ec, er + 1], weight, color)
    this.drawVLines([sc, sr], [sc, er], weight, color)
    this.drawVLines([ec + 1, sr], [ec + 1, er], weight, color)
  }

  private drawInnerGrid([sc, sr]: XY, [ec, er]: XY, weight: number, color: Color | string | null): void {
    this.drawHLines([sc, sr + 1], [ec, er], weight, color)
    this.drawVLines([sc + 1, sr], [ec, er], weight, color)
  }

  private prepLine(weight: number, color: Color | string | null): void {
    if (color && !sameColor(color, this.curcolor)) {
      this.canv!.setStrokeColor(color)
      this.curcolor = color
    }
    if (weight && weight !== this.curweight) {
      this.canv!.setLineWidth(weight)
      this.curweight = weight
    }
  }

  private drawHLines([sc, sr]: XY, [ec, er]: XY, weight: number, color: Color | string | null): void {
    const ecpL = this.colpositions.slice(sc, ec + 2)
    const rp = this.rowpositions.slice(sr, er + 1)
    if (ecpL.length <= 1 || rp.length < 1) return
    this.prepLine(weight, color)
    const scp = ecpL[0]
    const ecp = ecpL[ecpL.length - 1]
    const canv = this.canv!
    for (const y of rp) hLine((x0, y0, x1, y1) => canv.line(x0, y0, x1, y1), scp, ecp, y, this.hBlocks)
  }

  private drawVLines([sc, sr]: XY, [ec, er]: XY, weight: number, color: Color | string | null): void {
    const erpL = this.rowpositions.slice(sr, er + 2)
    const cp = this.colpositions.slice(sc, ec + 1)
    if (erpL.length <= 1 || cp.length < 1) return
    this.prepLine(weight, color)
    const srp = erpL[0]
    const erp = erpL[erpL.length - 1]
    const canv = this.canv!
    for (const x of cp) hLine((y0, x0, y1, x1) => canv.line(x0, y0, x1, y1), erp, srp, x, this.vBlocks)
  }
}

/** Color != Color as reportlab compares them (components), strings by value */
function sameColor(a: Color | string | null, b: Color | string | null): boolean {
  if (a === b) return true
  if (a === null || b === null || typeof a === 'string' || typeof b === 'string') return false
  return a.red === b.red && a.green === b.green && a.blue === b.blue && a.alpha === b.alpha
}

/** _setCellStyle: one style command applied to one cell */
function setCellStyle(s: CellStyle, op: string, values: unknown[]): void {
  switch (op) {
    case 'FONT': {
      s.fontname = values[0] as string
      if (values.length > 1) {
        s.fontsize = values[1] as number
        s.leading = values.length > 2 ? values[2] as number : s.fontsize * 1.2
      }
      break
    }
    case 'FONTNAME': case 'FACE': s.fontname = values[0] as string; break
    case 'SIZE': case 'FONTSIZE': s.fontsize = values[0] as number; break
    case 'LEADING': s.leading = values[0] as number; break
    case 'TEXTCOLOR': s.color = toColor(values[0] as Color | string); break
    case 'ALIGN': case 'ALIGNMENT': s.alignment = values[0] as string; break
    case 'VALIGN': s.valign = values[0] as string; break
    case 'LEFTPADDING': s.leftPadding = values[0] as number; break
    case 'RIGHTPADDING': s.rightPadding = values[0] as number; break
    case 'TOPPADDING': s.topPadding = values[0] as number; break
    case 'BOTTOMPADDING': s.bottomPadding = values[0] as number; break
    case 'HREF': s.href = values[0] as string; break
    default: break
  }
}

export type { Canvas }

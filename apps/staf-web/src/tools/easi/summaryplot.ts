// EASI's report summary plot (easi/report.py _summary_plots_png), drawn on a canvas as matplotlib lays it out:
// two axes side by side (width ratios 1.5 and 1), the function scores grouped by discipline on the left and
// the condition indices on the right, every bar in its band's color; the layout is matplotlib's (its grid,
// tick locator and formatter, text boxes, tight_layout(w_pad=2.0) measured at the figure's own 100 dpi and
// the figure saved at 150), the text DejaVu Sans and DejaVu Sans Bold at matplotlib's hinted metrics
// (content/easi/summaryplot-text.json, written by copy_page.py). layoutSummary is the layout alone (tested
// against matplotlib's own, make_summaryplot_fixtures.py); summaryPlotPng draws it.
import TEXT from '../../app/content/easi/summaryplot-text.json'
import { pyFixed } from '../../app/pyfmt'
import { functions } from './method'
import { type Box, formatTicks, type Metrics, type Placed, placeRun, setText, sized, tickValues, union } from './mpl'
import { functionScoreBandColor, indexBandColor } from './scoring'

const FIG_IN: [number, number] = [7.2, 5.6]
const LAYOUT_DPI = 100
const SAVE_DPI = 150
export const IMAGE_W = Math.trunc(FIG_IN[0] * SAVE_DPI)
export const IMAGE_H = Math.trunc(FIG_IN[1] * SAVE_DPI)
// rcParams the figure keeps: ticks out, 3.5 pt long and 3.5 pt from their labels, x tick labels at 10 pt,
// axis labels 4 pt off, titles 6 pt above, 0.8 pt spines and ticks; tight_layout's pad (1.08) and w_pad
// (2.0) in units of the 10 pt font size; the subplot box and spacing it starts from
const TICK_SIZE = 3.5
const TICK_PAD = 3.5
const XTICK_PT = 10
const LABEL_PAD = 4.0
const TITLE_PAD = 6.0
const AXES_LW = 0.8
const TIGHT_PAD = 1.08
const W_PAD = 2.0
const FONT_SIZE = 10.0
const SUBPLOT = { left: 0.125, right: 0.9, bottom: 0.11, top: 0.88, wspace: 0.2 }
const WIDTH_RATIOS = [1.5, 1]
const FAMILY = { normal: 'STAF DejaVu Sans Summary', bold: 'STAF DejaVu Sans Bold' } as const
type Weight = keyof typeof FAMILY

const SIZES = (TEXT as unknown as { sizes: Record<string, Metrics> }).sizes

interface Styled extends Placed { weight: Weight }

function place(text: string, pt: number, weight: Weight, dpi: number, x: number, y: number, ha: string, va: string, color = '#000000'): Styled {
  const f = SIZES[`${weight}:${dpi}:${pt}`]
  if (!f) throw new Error(`no metrics for ${weight} ${pt} pt at ${dpi} dpi`)
  return { ...placeRun(setText(f, text), pt, x, y, ha, va, { color }), weight }
}

// ---------------------------------------------------------------- the figure's data

interface Bar { y: number, width: number, height: number, face: string, edge: string, zorder: number }
interface Note { x: number, y: number, text: string, pt: number, color: string }
interface Tick { y: number, label: string, pt: number, weight: Weight, color: string }

interface AxesData {
  xlim: [number, number]
  ylim: [number, number]
  yticks: Tick[]
  xlabel: string
  title: string
  bars: Bar[]
  /** the bars and texts in the order they were made (their draw order within a zorder) */
  notes: Note[]
}

/** The two panels' contents (report.py _summary_plots_png before its layout). */
function figData(rep: Record<string, any>): [AxesData, AxesData] {
  const fscores: Record<string, number | null> = rep.functionScores || {}
  const sub: Record<string, number | null> = rep.subIndices || {}
  const eci = rep.ecosystemConditionIndex ?? null
  const rows: ['hdr' | 'bar', string, number | null, string | null][] = []
  const groups = new Map<string, Record<string, any>[]>()
  for (const fn of functions()) {
    if (!groups.has(fn.category)) groups.set(fn.category, [])
    groups.get(fn.category)!.push(fn)
  }
  for (const [cat, fns] of groups) {
    rows.push(['hdr', cat, null, null])
    for (const fn of fns) {
      const s = fscores[fn.id] ?? null
      rows.push(['bar', fn.name, s, s !== null ? functionScoreBandColor(s) : '#d7dce5'])
    }
  }
  const plotRows = [...rows].reverse()
  const left: AxesData = { xlim: [0, 15], ylim: [-0.6, plotRows.length - 0.4], yticks: [], xlabel: 'Function score (0-15)', title: 'Function scores',
    bars: [], notes: [] }
  plotRows.forEach(([kind, text, val, color], y) => {
    if (kind === 'hdr') left.yticks.push({ y, label: text, pt: 7.2, weight: 'bold', color: '#22304d' })
    else {
      if (val === null) {
        left.bars.push({ y, width: 15, height: 0.66, face: '#eef0f4', edge: '#c7ccd5', zorder: 2 })
        left.notes.push({ x: 0.35, y, text: '—', pt: 8, color: '#697386' })
      } else {
        left.bars.push({ y, width: val, height: 0.66, face: color!, edge: '#888888', zorder: 3 })
        left.notes.push({ x: Math.min(val + 0.3, 14.4), y, text: pyFixed(val, 0), pt: 7, color: '#000000' })
      }
      left.yticks.push({ y, label: '   ' + text, pt: 7.2, weight: 'normal', color: '#000000' })
    }
  })
  const ci: [string, number | null][] = [['Biological', sub.biological ?? null], ['Chemical', sub.chemical ?? null], ['Physical', sub.physical ?? null],
    ['Ecosystem Condition Index', eci]]
  const right: AxesData = { xlim: [0, 1], ylim: [-0.6, 3.6], yticks: [], xlabel: 'Condition index (0-1)', title: 'Condition indices', bars: [], notes: [] }
  ci.forEach(([, val], y) => {
    if (val === null) {
      right.bars.push({ y, width: 1, height: 0.6, face: '#eef0f4', edge: '#c7ccd5', zorder: 2 })
      right.notes.push({ x: 0.03, y, text: '—', pt: 8, color: '#697386' })
    } else {
      right.bars.push({ y, width: val, height: 0.6, face: indexBandColor(val), edge: '#888888', zorder: 3 })
      right.notes.push({ x: Math.min(val + 0.02, 0.92), y, text: pyFixed(val, 2), pt: 8, color: '#000000' })
    }
  })
  right.yticks = ['   Biological', '   Chemical', '   Physical', 'Ecosystem Condition Index'].map((label, y) => (
    { y, label, pt: 8, weight: y === 3 ? 'bold' : 'normal', color: '#000000' }))
  return [left, right]
}

// ---------------------------------------------------------------- the artists of one axes

interface Artists {
  axes: Box
  xticks: [number, string, Styled][]
  yticks: [number, string, Styled][]
  xlabel: Styled
  title: Styled
  notes: Styled[]
}

const near = (lim: [number, number], v: number) => {
  const a = Math.min(...lim)
  const b = Math.max(...lim)
  const rtol = (b - a) * 1e-10
  return a - rtol <= v && v <= b + rtol
}

function artists(d: AxesData, axes: Box, dpi: number): Artists {
  const px = dpi / 72
  const tx = (v: number) => axes[0] + (v - d.xlim[0]) / (d.xlim[1] - d.xlim[0]) * (axes[2] - axes[0])
  const ty = (v: number) => axes[1] + (v - d.ylim[0]) / (d.ylim[1] - d.ylim[0]) * (axes[3] - axes[1])
  const nbins = Math.max(1, Math.min(9, Math.floor((axes[2] - axes[0]) / dpi * 72 / (XTICK_PT * 3))))
  const locs = tickValues(d.xlim, nbins)
  const labels = formatTicks(locs, d.xlim)
  const off = (TICK_PAD + TICK_SIZE) * px
  const xticks = locs.map((loc, i) => [loc, labels[i]] as [number, string]).filter(([loc]) => near(d.xlim, loc))
    .map(([loc, lab]) => [loc, lab, place(lab, XTICK_PT, 'normal', dpi, tx(loc), axes[1] - off, 'center', 'top')] as [number, string, Styled])
  const yticks = d.yticks.filter((t) => near(d.ylim, t.y))
    .map((t) => [t.y, t.label, place(t.label, t.pt, t.weight, dpi, axes[0] - off, ty(t.y), 'right', 'center_baseline', t.color)] as [number, string, Styled])
  const below = Math.min(...xticks.map((t) => t[2].box[1]), axes[1] - TICK_SIZE * px)
  const xlabel = place(d.xlabel, 8, 'normal', dpi, tx(d.xlim[0]) + (tx(d.xlim[1]) - tx(d.xlim[0])) / 2, below - LABEL_PAD * px, 'center', 'top')
  const title = place(d.title, 9.5, 'bold', dpi, axes[0] + (axes[2] - axes[0]) / 2, axes[3] + TITLE_PAD * px, 'center', 'baseline')
  const notes = d.notes.map((n) => place(n.text, n.pt, 'normal', dpi, tx(n.x), ty(n.y), 'left', 'center', n.color))
  return { axes, xticks, yticks, xlabel, title, notes }
}

/** Axes.get_tightbbox(for_layout_only=True): the axes, tick labels, the x label collapsed across, the title
 * collapsed to 1 px wide, the left and bottom spines with their ticks (top and right are hidden), the texts. */
function tightBox(a: Artists, dpi: number): Box {
  const px = dpi / 72
  const ax = a.axes
  const xl = a.xlabel.box
  const tb = a.title.box
  const xmid = (xl[0] + xl[2]) / 2 - 0.5
  const tmid = (tb[0] + tb[2]) / 2 - 0.5
  const boxes: Box[] = [
    ax,
    ...[...a.xticks.map((t) => t[2].box), [xmid, xl[1], xmid + 1, xl[3]] as Box].filter(sized),
    ...a.yticks.map((t) => t[2].box).filter(sized),
    [tmid, tb[1], tmid + 1, tb[3]],
    [ax[0] - TICK_SIZE * px, ax[1], ax[0], ax[3]],
    [ax[0], ax[1] - TICK_SIZE * px, ax[2], ax[1]],
    ...a.notes.map((t) => t.box).filter(sized),
  ]
  return union(boxes.filter((b) => b[2] - b[0] !== 0 || b[3] - b[1] !== 0))
}

/** GridSpec.get_grid_positions for one row: each column's [left, right] as figure fractions. */
function gridColumns(left: number, right: number, wspace: number): [number, number][] {
  const ncols = WIDTH_RATIOS.length
  const totWidth = right - left
  const cellW = totWidth / (ncols + wspace * (ncols - 1))
  const sepW = wspace * cellW
  const norm = cellW * ncols / WIDTH_RATIOS.reduce((p, q) => p + q, 0)
  const flat: number[] = []
  WIDTH_RATIOS.forEach((r, i) => flat.push(i ? sepW : 0, r * norm))
  let acc = 0
  const cum = flat.map((v) => (acc += v))
  return WIDTH_RATIOS.map((_, i) => [left + cum[2 * i], left + cum[2 * i + 1]] as [number, number])
}

interface Layout { data: [AxesData, AxesData], art: [Artists, Artists] }

/** fig.tight_layout(w_pad=2.0) at the figure's 100 dpi, then the figure at 150 dpi. */
function layout(rep: Record<string, any>): Layout {
  const data = figData(rep)
  const [wIn, hIn] = FIG_IN
  const w100 = wIn * LAYOUT_DPI
  const h100 = hIn * LAYOUT_DPI
  const cols0 = gridColumns(SUBPLOT.left, SUBPLOT.right, SUBPLOT.wspace)
  const frac = (b: Box): Box => [b[0] / w100, b[1] / h100, b[2] / w100, b[3] / h100]
  const hspaces = [0, 0, 0]
  const vtop: number[] = []
  const vbottom: number[] = []
  data.forEach((d, c) => {
    const pos: Box = [cols0[c][0], SUBPLOT.bottom, cols0[c][1], SUBPLOT.top]
    const a = artists(d, [pos[0] * w100, pos[1] * h100, pos[2] * w100, pos[3] * h100], LAYOUT_DPI)
    const t = frac(tightBox(a, LAYOUT_DPI))
    hspaces[c] += pos[0] - t[0]
    hspaces[c + 1] += t[2] - pos[2]
    vtop.push(t[3] - pos[3])
    vbottom.push(pos[1] - t[1])
  })
  const fontInch = FONT_SIZE / 72
  const padInch = TIGHT_PAD * fontInch
  const hpadInch = W_PAD * fontInch
  const marginLeft = Math.max(hspaces[0], 0) + padInch / wIn
  const marginRight = Math.max(hspaces[2], 0) + padInch / wIn
  const marginTop = Math.max(Math.max(...vtop), 0) + padInch / hIn
  const marginBottom = Math.max(Math.max(...vbottom), 0) + padInch / hIn
  const hspace = hspaces[1] + hpadInch / wIn
  const hAxes = (1 - marginRight - marginLeft - hspace * (WIDTH_RATIOS.length - 1)) / WIDTH_RATIOS.length
  const cols = gridColumns(marginLeft, 1 - marginRight, hspace / hAxes)
  const w150 = wIn * SAVE_DPI
  const h150 = hIn * SAVE_DPI
  const top = 1 - marginTop
  const art = data.map((d, c) => artists(d, [cols[c][0] * w150, marginBottom * h150, cols[c][1] * w150, top * h150], SAVE_DPI)) as [Artists, Artists]
  return { data, art }
}

export interface SummaryLayout {
  axes: Box
  xlim: [number, number]
  ylim: [number, number]
  xticks: [number, string, Box][]
  yticks: [number, string, Box][]
  title: Box
  xlabel: Box
  texts: [string, Box][]
  bars: Box[]
}

/** The figure's layout as matplotlib reports it (display pixels at 150 dpi, y up), panel by panel. */
export function layoutSummary(rep: Record<string, any>): SummaryLayout[] {
  const { data, art } = layout(rep)
  return data.map((d, c) => {
    const a = art[c]
    const ax = a.axes
    const tx = (v: number) => ax[0] + (v - d.xlim[0]) / (d.xlim[1] - d.xlim[0]) * (ax[2] - ax[0])
    const ty = (v: number) => ax[1] + (v - d.ylim[0]) / (d.ylim[1] - d.ylim[0]) * (ax[3] - ax[1])
    return {
      axes: ax, xlim: d.xlim, ylim: d.ylim,
      xticks: a.xticks.map(([loc, lab, p]) => [loc, lab, p.box]), yticks: a.yticks.map(([loc, lab, p]) => [loc, lab, p.box]),
      title: a.title.box, xlabel: a.xlabel.box, texts: d.notes.map((n, i) => [n.text, a.notes[i].box]),
      bars: d.bars.map((b) => [tx(0), ty(b.y - b.height / 2), tx(b.width), ty(b.y + b.height / 2)] as Box),
    }
  })
}

// ---------------------------------------------------------------- drawing

let fontP: Promise<void> | null = null

/** DejaVu Sans and DejaVu Sans Bold, the page's copies (cut to the plot's characters). */
function loadFonts(): Promise<void> {
  fontP ??= (async () => {
    const faces = [new FontFace(FAMILY.normal, `url(${new URL('fonts/dejavu-sans-summary.woff2', document.baseURI).href})`),
      new FontFace(FAMILY.bold, `url(${new URL('fonts/dejavu-sans-bold.woff2', document.baseURI).href})`)]
    for (const f of await Promise.all(faces.map((f) => f.load()))) document.fonts.add(f)
  })()
  fontP.catch(() => { fontP = null })
  return fontP
}

/** The figure as report.py saves it (dpi 150), as PNG bytes. */
export async function summaryPlotPng(rep: Record<string, any>): Promise<Uint8Array> {
  const { data, art } = layout(rep)
  await loadFonts()
  const canvas = document.createElement('canvas')
  canvas.width = IMAGE_W
  canvas.height = IMAGE_H
  const ctx = canvas.getContext('2d')!
  const H = IMAGE_H
  const px = SAVE_DPI / 72
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, IMAGE_W, IMAGE_H)
  /** a rectilinear path's coordinate snapped as Agg's PathSnapper snaps it */
  const snap = (v: number, lwPx: number) => Math.floor(v + 0.5) + (Math.round(lwPx) % 2 ? 0.5 : 0)
  const drawText = (p: Styled) => {
    ctx.save()
    ctx.fillStyle = p.color
    ctx.font = `${p.pt * px}px "${FAMILY[p.weight]}"`
    ctx.textBaseline = 'alphabetic'
    ctx.textAlign = 'left'
    ctx.translate(p.pen[0], H - p.pen[1])
    for (const [g, dx] of p.run.glyphs) ctx.fillText(g, dx, 0)
    ctx.restore()
  }
  data.forEach((d, c) => {
    const a = art[c]
    const ax = a.axes
    const X = (v: number) => ax[0] + (v - d.xlim[0]) / (d.xlim[1] - d.xlim[0]) * (ax[2] - ax[0])
    const Y = (v: number) => H - (ax[1] + (v - d.ylim[0]) / (d.ylim[1] - d.ylim[0]) * (ax[3] - ax[1]))
    const clip = () => {
      ctx.beginPath()
      ctx.rect(Math.floor(ax[0] + 0.5), Math.floor(H - ax[3] + 0.5), Math.floor(ax[2] + 0.5) - Math.floor(ax[0] + 0.5),
        Math.floor(H - ax[1] + 0.5) - Math.floor(H - ax[3] + 0.5))
      ctx.clip()
    }
    // the axis below everything else (set_axisbelow): the x grid, the tick marks, the tick labels and the x label
    const glw = 0.6 * px
    ctx.save()
    clip()
    ctx.strokeStyle = '#eef0f4'
    ctx.lineWidth = glw
    ctx.lineCap = 'square'
    for (const [loc] of a.xticks) {
      const x = snap(X(loc), glw)
      ctx.beginPath()
      ctx.moveTo(x, snap(Y(d.ylim[1]), glw))
      ctx.lineTo(x, snap(Y(d.ylim[0]), glw))
      ctx.stroke()
    }
    ctx.restore()
    const slw = AXES_LW * px
    const tick = TICK_SIZE * px
    const sx0 = snap(ax[0], slw)
    const sx1 = snap(ax[2], slw)
    const sr0 = snap(H - ax[3], slw)
    const sr1 = snap(H - ax[1], slw)
    ctx.strokeStyle = '#000000'
    ctx.lineWidth = slw
    ctx.lineCap = 'butt'
    for (const [loc] of a.xticks) {
      const x = snap(Math.floor(X(loc) + 0.5), slw)
      ctx.beginPath()
      ctx.moveTo(x, sr1)
      ctx.lineTo(x, sr1 + tick)
      ctx.stroke()
    }
    for (const [loc] of a.yticks) {
      const r = snap(Math.floor(Y(loc) + 0.5), slw)
      ctx.beginPath()
      ctx.moveTo(sx0, r)
      ctx.lineTo(sx0 - tick, r)
      ctx.stroke()
    }
    for (const [, , p] of a.xticks) drawText(p)
    for (const [, , p] of a.yticks) drawText(p)
    drawText(a.xlabel)
    // the bars (missing ones at zorder 2, under the spines), the spines (2.5), then the scored bars and the
    // texts in the order they were made (3)
    const bar = (b: Bar) => {
      const elw = 1.0 * px
      const x0 = snap(X(0), elw)
      const x1 = snap(X(b.width), elw)
      const r0 = snap(Y(b.y + b.height / 2), elw)
      const r1 = snap(Y(b.y - b.height / 2), elw)
      ctx.save()
      clip()
      ctx.fillStyle = b.face
      ctx.fillRect(x0, r0, x1 - x0, r1 - r0)
      ctx.strokeStyle = b.edge
      ctx.lineWidth = elw
      ctx.lineJoin = 'miter'
      ctx.strokeRect(x0, r0, x1 - x0, r1 - r0)
      ctx.restore()
    }
    for (const b of d.bars) if (b.zorder < 2.5) bar(b)
    ctx.strokeStyle = '#000000'
    ctx.lineWidth = slw
    ctx.lineCap = 'square'
    for (const [p, q, r, s] of [[sx0, sr1, sx1, sr1], [sx0, sr0, sx0, sr1]]) {
      ctx.beginPath()
      ctx.moveTo(p, q)
      ctx.lineTo(r, s)
      ctx.stroke()
    }
    d.bars.forEach((b, i) => {
      if (b.zorder >= 2.5) bar(b)
      drawText(a.notes[i])
    })
    drawText(a.title)
  })
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) throw new Error('the summary plot could not be drawn')
  return new Uint8Array(await blob.arrayBuffer())
}

// EASI's report cross-section image (easi/xsplot.py cross_section_png), drawn on a canvas as matplotlib 3.11
// draws it with Agg: the figure's layout is matplotlib's (its tick locator and formatter, its text boxes,
// tight_layout measured at the figure's own 100 dpi and the figure saved at 130), the text is set with
// DejaVu Sans at matplotlib's hinted metrics (content/easi/xsplot-text.json, written by copy_page.py) and
// drawn glyph by glyph with the same font (fonts/dejavu-sans.woff2), the lines snap to whole pixels as
// Agg snaps them. layoutFigure is the layout alone (tested against matplotlib's own,
// make_xsimage_fixtures.py); xsImage draws it.
import TEXT from '../../app/content/easi/xsplot-text.json'
import { argmin, displayWindow, FT_PER_M, VIEW_HEADROOM, VIEW_MIN_HALF_M, wettedSpan } from '../../engine/geomorph'
import { type Box, formatTicks, type Metrics, type Placed, placeRun, setText, sized, tickValues, union } from './mpl'

export type { Box }
export { formatTicks, tickValues }

const FIG_IN: [number, number] = [6.5, 2.15]
const LAYOUT_DPI = 100
const SAVE_DPI = 130
/** the saved image (Agg's canvas is the figure's size in whole pixels) */
export const IMAGE_W = Math.trunc(FIG_IN[0] * SAVE_DPI)
export const IMAGE_H = Math.trunc(FIG_IN[1] * SAVE_DPI)
const LABEL_EPS_FRAC = 0.06
// matplotlib's defaults (rcParams) the figure keeps: ticks out, 3.5 pt long and 3.5 pt from their labels,
// axis labels 4 pt off, 0.8 pt spines and ticks, tight_layout's pad in units of the 10 pt font size, the
// default subplot box tight_layout starts from
const TICK_SIZE = 3.5
const TICK_PAD = 3.5
const LABEL_PAD = 4.0
const TITLE_PAD = 5.0
const AXES_LW = 0.8
const TIGHT_PAD = 0.4
const FONT_SIZE = 10.0
const SUBPLOT = { left: 0.125, right: 0.9, bottom: 0.11, top: 0.88 }
const FONT_FAMILY = 'STAF DejaVu Sans'

const SIZES = (TEXT as unknown as { sizes: Record<string, Metrics> }).sizes

export interface XsImageSpec {
  stations: number[]
  elevs: number[]
  thalweg: number | null
  bankfull_stage: number | null
  floodplain_stage: number | null
  unit: string
  source: string | null
  title?: string
}

// ---------------------------------------------------------------- text

/** a line of text at a size and resolution (setText over this image's metrics) */
function placeText(text: string, pt: number, dpi: number, x: number, y: number, ha: string, va: string,
  opts: { rot?: number, anchor?: boolean, color?: string } = {}): Placed {
  return placeRun(setText(SIZES[`${dpi}:${pt}`], text), pt, x, y, ha, va, opts)
}

// ---------------------------------------------------------------- the figure

interface FigData {
  xs: number[]
  h: number[]
  ti: number
  bfH: number | null
  lbH: number | null
  base: number
  xlim: [number, number]
  ylim: [number, number]
  ul: string
  title: string
  source: string | null
  texts: { text: string, ha: string, y: number, color: string }[]
}

/** cross_section_png up to the drawing: the profile on the thalweg datum, the reference heights, the window. */
function figData(spec: XsImageSpec): FigData | null {
  if (!spec.stations || !spec.elevs || spec.stations.length < 3) return null
  const u = spec.unit === 'ft' ? FT_PER_M : 1.0
  const ul = spec.unit === 'ft' ? 'ft' : 'm'
  const z = spec.elevs.map(Number)
  const thal = spec.thalweg !== null && spec.thalweg !== undefined ? Number(spec.thalweg) : Math.min(...z)
  const ti = argmin(z)
  const x0 = Number(spec.stations[ti])
  const xs = spec.stations.map((v) => (Number(v) - x0) * u)
  const h = z.map((v) => (v - thal) * u)
  const bfH = spec.bankfull_stage !== null && spec.bankfull_stage !== undefined ? (Number(spec.bankfull_stage) - thal) * u : null
  const lbH = spec.floodplain_stage !== null && spec.floodplain_stage !== undefined ? (Number(spec.floodplain_stage) - thal) * u : null
  const hmin = Math.min(...h)
  const base = hmin - 0.5
  let ymax = Math.max(...h)
  const topLine = bfH !== null ? Math.max(2.0 * bfH, lbH || 0.0) : 0.0
  let xlim: [number, number]
  if (topLine > 0) {
    const ceiling = VIEW_HEADROOM * topLine
    const [lo, hi] = displayWindow(xs, h, ceiling, ti, VIEW_MIN_HALF_M * u)
    const padx = hi > lo ? 0.03 * (hi - lo) : 1.0
    xlim = [lo - padx, hi + padx]
    let hmaxWin = -Infinity
    for (let k = 0; k < xs.length; k++) if (xs[k] >= lo && xs[k] <= hi) hmaxWin = Math.max(hmaxWin, h[k])
    if (hmaxWin === -Infinity) hmaxWin = 0.0
    ymax = Math.max(topLine, Math.min(hmaxWin, ceiling))
  } else {
    const xb = xs.length ? Math.max(...xs.map(Math.abs)) : 1.0
    xlim = [-xb, xb]
    if (lbH !== null) ymax = Math.max(ymax, lbH)
  }
  const pady = ymax > base ? 0.06 * (ymax - base) : 1.0
  const texts: FigData['texts'] = []
  if (bfH !== null) {
    texts.push({ text: ' bankfull', ha: 'left', y: bfH, color: '#1f6fc0' })
    texts.push({ text: ' floodprone', ha: 'left', y: 2.0 * bfH, color: '#9a6b3f' })
  }
  if (lbH !== null) {
    const yspan = (ymax + pady) - base
    const clash = bfH !== null && (Math.abs(lbH - 2.0 * bfH) < LABEL_EPS_FRAC * yspan || Math.abs(lbH - bfH) < LABEL_EPS_FRAC * yspan)
    texts.push(clash ? { text: 'low bank ', ha: 'right', y: lbH, color: '#3a8a5c' } : { text: ' low bank', ha: 'left', y: lbH, color: '#3a8a5c' })
  }
  return { xs, h, ti, bfH, lbH, base, xlim, ylim: [base, ymax + pady], ul, title: spec.title ?? 'Reach cross-section',
    source: spec.source ?? null, texts }
}

interface Artists {
  axes: Box
  xticks: [number, string, Placed][]
  yticks: [number, string, Placed][]
  xlabel: Placed
  ylabel: Placed
  title: Placed
  texts: Placed[]
}

const near = (lim: [number, number], v: number) => {
  const a = Math.min(...lim)
  const b = Math.max(...lim)
  const rtol = (b - a) * 1e-10
  return a - rtol <= v && v <= b + rtol
}

/** The axes' artists for an axes box at a dpi: the ticks with their labels, the axis labels, the title, the
 * line labels (Axis._update_ticks and _update_label_position, Axes._update_title_position). */
function artists(fig: FigData, axes: Box, dpi: number): Artists {
  const px = dpi / 72
  const tx = (v: number) => axes[0] + (v - fig.xlim[0]) / (fig.xlim[1] - fig.xlim[0]) * (axes[2] - axes[0])
  const ty = (v: number) => axes[1] + (v - fig.ylim[0]) / (fig.ylim[1] - fig.ylim[0]) * (axes[3] - axes[1])
  const space = (len: number, size: number) => Math.max(1, Math.min(9, Math.floor(len / dpi * 72 / size)))
  const ticks = (lim: [number, number], nbins: number) => {
    const locs = tickValues(lim, nbins)
    const labels = formatTicks(locs, lim)
    return locs.map((loc, i) => [loc, labels[i]] as [number, string]).filter(([loc]) => near(lim, loc))
  }
  const off = (TICK_PAD + TICK_SIZE) * px
  const xticks = ticks(fig.xlim, space(axes[2] - axes[0], 8 * 3))
    .map(([loc, lab]) => [loc, lab, placeText(lab, 8, dpi, tx(loc), axes[1] - off, 'center', 'top')] as [number, string, Placed])
  const yticks = ticks(fig.ylim, space(axes[3] - axes[1], 8 * 2))
    .map(([loc, lab]) => [loc, lab, placeText(lab, 8, dpi, axes[0] - off, ty(loc), 'right', 'center_baseline')] as [number, string, Placed])
  const below = Math.min(...xticks.map((t) => t[2].box[1]), axes[1] - TICK_SIZE * px)
  const xlabel = placeText(`Station (${fig.ul})`, 9, dpi, tx(fig.xlim[0]) + (tx(fig.xlim[1]) - tx(fig.xlim[0])) / 2, below - LABEL_PAD * px,
    'center', 'top')
  const left = Math.min(...yticks.map((t) => t[2].box[0]), axes[0] - TICK_SIZE * px)
  const ylabel = placeText(`Height above bed (${fig.ul})`, 9, dpi, left - LABEL_PAD * px, axes[1] + (axes[3] - axes[1]) / 2, 'center', 'bottom',
    { rot: 90, anchor: true })
  const title = placeText(fig.title, 10, dpi, axes[0] + (axes[2] - axes[0]) / 2, axes[3] + TITLE_PAD * px, 'center', 'baseline')
  const texts = fig.texts.map((t) => placeText(t.text, 8, dpi, tx(fig.xlim[1]), ty(t.y), t.ha, 'center', { color: t.color }))
  return { axes, xticks, yticks, xlabel, ylabel, title, texts }
}


/** Axes.get_tightbbox(for_layout_only=True): the axes, its tick labels and axis labels (each label
 * collapsed across its axis), the title (collapsed to 1 px wide), the spines with their ticks, the texts. */
function tightBox(a: Artists, dpi: number): Box {
  const px = dpi / 72
  const ax = a.axes
  const xl = a.xlabel.box
  const yl = a.ylabel.box
  const tb = a.title.box
  const xmid = (xl[0] + xl[2]) / 2 - 0.5
  const ymid = (yl[1] + yl[3]) / 2 - 0.5
  const tmid = (tb[0] + tb[2]) / 2 - 0.5
  const boxes: Box[] = [
    ax,
    ...[...a.xticks.map((t) => t[2].box), [xmid, xl[1], xmid + 1, xl[3]] as Box].filter(sized),
    ...[...a.yticks.map((t) => t[2].box), [yl[0], ymid, yl[2], ymid + 1] as Box].filter(sized),
    [tmid, tb[1], tmid + 1, tb[3]],
    [ax[0] - TICK_SIZE * px, ax[1], ax[0], ax[3]], [ax[2], ax[1], ax[2], ax[3]],
    [ax[0], ax[1] - TICK_SIZE * px, ax[2], ax[1]], [ax[0], ax[3], ax[2], ax[3]],
    ...a.texts.map((t) => t.box).filter(sized),
  ]
  return union(boxes.filter((b) => b[2] - b[0] !== 0 || b[3] - b[1] !== 0))
}

export interface FigureLayout {
  axes: Box
  xlim: [number, number]
  ylim: [number, number]
  xticks: [number, string, Box][]
  yticks: [number, string, Box][]
  title: Box
  xlabel: Box
  ylabel: Box
  texts: [string, string, Box][]
  figtexts: [string, Box][]
}

interface Layout { fig: FigData, art: Artists, source: Placed | null }

/** fig.tight_layout(pad=0.4) at the figure's 100 dpi, then the figure at 130 dpi. */
function layout(spec: XsImageSpec): Layout | null {
  const fig = figData(spec)
  if (!fig) return null
  const [wIn, hIn] = FIG_IN
  const w100 = wIn * LAYOUT_DPI
  const h100 = hIn * LAYOUT_DPI
  const start: Box = [SUBPLOT.left * w100, SUBPLOT.bottom * h100, SUBPLOT.right * w100, SUBPLOT.top * h100]
  const a0 = artists(fig, start, LAYOUT_DPI)
  const t = tightBox(a0, LAYOUT_DPI)
  const pad = TIGHT_PAD * FONT_SIZE / 72
  const left = Math.max((start[0] - t[0]) / w100, 0) + pad / wIn
  const right = Math.max((t[2] - start[2]) / w100, 0) + pad / wIn
  const top = Math.max((t[3] - start[3]) / h100, 0) + pad / hIn
  const bottom = Math.max((start[1] - t[1]) / h100, 0) + pad / hIn
  // the grid's one cell (GridSpec.get_grid_positions)
  const r = 1 - right
  const tp = 1 - top
  const w130 = wIn * SAVE_DPI
  const h130 = hIn * SAVE_DPI
  const axes: Box = [left * w130, (tp - (tp - bottom)) * h130, (left + (r - left)) * w130, tp * h130]
  const art = artists(fig, axes, SAVE_DPI)
  const source = fig.source ? placeText(fig.source, 6.5, SAVE_DPI, 0.995 * w130, 0.01 * h130, 'right', 'bottom', { color: '#999999' }) : null
  return { fig, art, source }
}

/** The figure's layout as matplotlib reports it (display pixels at 130 dpi, y up). */
export function layoutFigure(spec: XsImageSpec): FigureLayout | null {
  const l = layout(spec)
  if (!l) return null
  const { fig, art, source } = l
  return {
    axes: art.axes, xlim: fig.xlim, ylim: fig.ylim,
    xticks: art.xticks.map(([loc, lab, p]) => [loc, lab, p.box]), yticks: art.yticks.map(([loc, lab, p]) => [loc, lab, p.box]),
    title: art.title.box, xlabel: art.xlabel.box, ylabel: art.ylabel.box,
    texts: fig.texts.map((t, i) => [t.text, t.ha, art.texts[i].box]),
    figtexts: source ? [[fig.source as string, source.box]] : [],
  }
}

// ---------------------------------------------------------------- drawing

let fontP: Promise<void> | null = null

/** DejaVu Sans, the page's copy (cut to the image's characters). */
function loadFont(): Promise<void> {
  fontP ??= (async () => {
    const face = new FontFace(FONT_FAMILY, `url(${new URL('fonts/dejavu-sans.woff2', document.baseURI).href})`)
    await face.load()
    document.fonts.add(face)
  })()
  fontP.catch(() => { fontP = null })
  return fontP
}

/** cbook.contiguous_regions: [start, end) of each run of true */
function runs(mask: boolean[]): [number, number][] {
  const out: [number, number][] = []
  let i = 0
  while (i < mask.length) {
    if (!mask[i]) {
      i += 1
      continue
    }
    const j0 = i
    while (i < mask.length && mask[i]) i += 1
    out.push([j0, i])
  }
  return out
}

/** np.interp(0, xp, fp) for two points (or one) */
function interp0(xp: number[], fp: number[], x: number): number {
  if (xp.length === 1) return fp[0]
  if (x <= xp[0]) return fp[0]
  if (x >= xp[1]) return fp[1]
  return fp[0] + (x - xp[0]) * (fp[1] - fp[0]) / (xp[1] - xp[0])
}

/** fill_between's polygons (Axes._fill_between_x_or_y): each run of ``where``, closed along dep2, the
 * ends interpolated to the crossing when asked. */
function fillBetween(ind: number[], dep1: number[], dep2: number[], where: boolean[], interpolate: boolean): [number, number][][] {
  const polys: [number, number][][] = []
  const interpPoint = (idx: number): [number, number] => {
    const im1 = Math.max(idx - 1, 0)
    const inds = ind.slice(im1, idx + 1)
    const diffs = inds.map((_, k) => dep1[im1 + k] - dep2[im1 + k])
    const d1s = dep1.slice(im1, idx + 1)
    const order = diffs.map((_, k) => k).sort((p, q) => diffs[p] - diffs[q])
    const rootInd = interp0(order.map((k) => diffs[k]), order.map((k) => inds[k]), 0)
    const iorder = inds.map((_, k) => k).sort((p, q) => inds[p] - inds[q])
    const xi = iorder.map((k) => inds[k])
    const fi = iorder.map((k) => d1s[k])
    return [rootInd, xi.length === 1 ? fi[0] : interp0(xi, fi, rootInd)]
  }
  for (const [i0, i1] of runs(where)) {
    const n = i1 - i0
    if (!n) continue
    const start: [number, number] = interpolate ? interpPoint(i0) : [ind[i0], dep2[i0]]
    const end: [number, number] = interpolate ? interpPoint(i1) : [ind[i1 - 1], dep2[i1 - 1]]
    const pts: [number, number][] = [start]
    for (let k = i0; k < i1; k++) pts.push([ind[k], dep1[k]])
    pts.push(end)
    for (let k = i1 - 1; k >= i0; k--) pts.push([ind[k], dep2[k]])
    polys.push(pts)
  }
  return polys
}

/** The image as easi/xsplot.py saves it, as a PNG data URL (null for a section without a profile). */
export async function xsImage(spec: XsImageSpec): Promise<string | null> {
  const l = layout(spec)
  if (!l) return null
  await loadFont()
  const { fig, art, source } = l
  const canvas = document.createElement('canvas')
  canvas.width = IMAGE_W
  canvas.height = IMAGE_H
  const ctx = canvas.getContext('2d')!
  const H = IMAGE_H
  const px = SAVE_DPI / 72
  const ax = art.axes
  const X = (v: number) => ax[0] + (v - fig.xlim[0]) / (fig.xlim[1] - fig.xlim[0]) * (ax[2] - ax[0])
  const Y = (v: number) => H - (ax[1] + (v - fig.ylim[0]) / (fig.ylim[1] - fig.ylim[0]) * (ax[3] - ax[1]))
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, IMAGE_W, IMAGE_H)
  // Agg's clip box, in whole pixels
  const clip = () => {
    const x0 = Math.floor(ax[0] + 0.5)
    const x1 = Math.floor(ax[2] + 0.5)
    const r0 = Math.floor(H - ax[3] + 0.5)
    const r1 = Math.floor(H - ax[1] + 0.5)
    ctx.beginPath()
    ctx.rect(x0, r0, x1 - x0, r1 - r0)
    ctx.clip()
  }
  /** a rectilinear path's coordinate snapped as Agg's PathSnapper snaps it */
  const snap = (v: number, lwPx: number) => Math.floor(v + 0.5) + (Math.round(lwPx) % 2 ? 0.5 : 0)
  const polygon = (pts: [number, number][], face: string, lwPt: number) => {
    ctx.beginPath()
    pts.forEach(([x, y], k) => (k ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))))
    ctx.closePath()
    ctx.fillStyle = face
    ctx.fill()
    ctx.strokeStyle = face
    ctx.lineWidth = lwPt * px
    ctx.lineJoin = 'miter'
    ctx.stroke()
  }
  const hline = (y: number, color: string, lwPt: number, dashes: number[] | null) => {
    const lw = lwPt * px
    const r = snap(Y(y), lw)
    ctx.beginPath()
    ctx.moveTo(snap(ax[0], lw), r)
    ctx.lineTo(snap(ax[2], lw), r)
    ctx.strokeStyle = color
    ctx.lineWidth = lw
    ctx.lineCap = dashes ? 'butt' : 'square'
    ctx.setLineDash(dashes ? dashes.map((d) => d * lwPt * px) : [])
    ctx.lineDashOffset = 0
    ctx.stroke()
    ctx.setLineDash([])
  }
  ctx.save()
  clip()
  // the ground (zorder 1), the bed datum and the water (2)
  const baseArr = fig.xs.map(() => fig.base)
  for (const p of fillBetween(fig.xs, fig.h, baseArr, fig.xs.map(() => true), false)) polygon(p, '#efe9e1', 1.0)
  hline(0.0, '#b9aa97', 0.8, [1.0, 1.65])
  if (fig.bfH !== null) {
    const span = wettedSpan(fig.xs, fig.h, fig.bfH, fig.ti)
    const wet = fig.xs.map((x, k) => (span !== null ? fig.h[k] <= fig.bfH! && x >= span[0] && x <= span[1] : false))
    const lvl = fig.xs.map(() => fig.bfH as number)
    for (const p of fillBetween(fig.xs, fig.h, lvl, wet, true)) polygon(p, 'rgba(77,163,255,0.45)', 1.0)
  }
  ctx.restore()
  // the spines (2.5)
  const slw = AXES_LW * px
  ctx.strokeStyle = '#000000'
  ctx.lineWidth = slw
  ctx.lineCap = 'square'
  ctx.lineJoin = 'miter'
  ctx.setLineDash([])
  const sx0 = snap(ax[0], slw)
  const sx1 = snap(ax[2], slw)
  const sr0 = snap(H - ax[3], slw)
  const sr1 = snap(H - ax[1], slw)
  for (const [a, b, c, d] of [[sx0, sr1, sx1, sr1], [sx0, sr0, sx1, sr0], [sx0, sr0, sx0, sr1], [sx1, sr0, sx1, sr1]]) {
    ctx.beginPath()
    ctx.moveTo(a, b)
    ctx.lineTo(c, d)
    ctx.stroke()
  }
  // the bed (3) and the reference lines (4)
  ctx.save()
  clip()
  ctx.beginPath()
  fig.xs.forEach((x, k) => (k ? ctx.lineTo(X(x), Y(fig.h[k])) : ctx.moveTo(X(x), Y(fig.h[k]))))
  ctx.strokeStyle = '#5b4a3a'
  ctx.lineWidth = 1.6 * px
  ctx.lineJoin = 'round'
  ctx.lineCap = 'square'
  ctx.stroke()
  if (fig.bfH !== null) {
    hline(fig.bfH, '#1f6fc0', 1.1, null)
    hline(2.0 * fig.bfH, '#9a6b3f', 1.0, [1.0, 1.65])
  }
  if (fig.lbH !== null) hline(fig.lbH, '#3a8a5c', 1.1, [3.7, 1.6])
  ctx.restore()
  // the ticks: 3.5 pt outward, at whole pixels
  ctx.strokeStyle = '#000000'
  ctx.lineWidth = slw
  ctx.lineCap = 'butt'
  const tick = TICK_SIZE * px
  for (const [loc] of art.xticks) {
    const x = snap(Math.floor(X(loc) + 0.5), slw)
    ctx.beginPath()
    ctx.moveTo(x, sr1)
    ctx.lineTo(x, sr1 + tick)
    ctx.stroke()
  }
  for (const [loc] of art.yticks) {
    const r = snap(Math.floor(Y(loc) + 0.5), slw)
    ctx.beginPath()
    ctx.moveTo(sx0, r)
    ctx.lineTo(sx0 - tick, r)
    ctx.stroke()
  }
  // the text, glyph by glyph at matplotlib's pen positions
  const draw = (p: Placed) => {
    ctx.save()
    ctx.fillStyle = p.color
    ctx.font = `${p.pt * px}px "${FONT_FAMILY}"`
    ctx.textBaseline = 'alphabetic'
    ctx.textAlign = 'left'
    ctx.translate(p.pen[0], H - p.pen[1])
    if (p.rot) ctx.rotate(-p.rot * Math.PI / 180)
    for (const [g, dx] of p.run.glyphs) ctx.fillText(g, dx, 0)
    ctx.restore()
  }
  for (const t of art.texts) draw(t)
  for (const [, , p] of art.xticks) draw(p)
  for (const [, , p] of art.yticks) draw(p)
  draw(art.xlabel)
  draw(art.ylabel)
  draw(art.title)
  if (source) draw(source)
  return canvas.toDataURL('image/png')
}

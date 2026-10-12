// matplotlib's text and tick machinery as EASI's report images use it (xsimage.ts, summaryplot.ts): text set as
// FT2Font sets it (hinted advances, ink extents, ligatures and kerning from copy_page.py's metrics) and placed as
// Text._get_layout places it, AutoLocator's tick values and ScalarFormatter's labels.
import { pyFixed } from '../../app/pyfmt'

/** x0, y0, x1, y1 in display pixels (y up from the figure's bottom left) */
export type Box = [number, number, number, number]
/** one size of one face at one resolution (copy_page.py _ft_metrics) */
export type Metrics = { ascent: number, descent: number, glyphs: Record<string, number[]>, ligatures: Record<string, number[]>,
  kern: Record<string, number> }

export const STEPS = [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20]       // AutoLocator's staircase

// ---------------------------------------------------------------- text

/** A line of text as FT2Font sets it: its width (the pen's advance), ascent and descent (the line's ink,
 * at least the font's typographic ascent and descent, as Text._get_layout takes them), and each glyph
 * (or ligature) with its offset from the pen. */
export interface Run { w: number, a: number, d: number, glyphs: [string, number][] }

export function setText(f: Metrics, text: string): Run {
  const chars = [...text]
  const glyphs: [string, number][] = []
  let pen = 0
  let yMin = Infinity
  let yMax = -Infinity
  let prev: string | null = null
  for (let i = 0; i < chars.length; i++) {
    let tok = chars[i]
    let g = f.glyphs[tok] ?? f.glyphs['?']
    const pair = i + 1 < chars.length ? chars[i] + chars[i + 1] : ''
    if (pair && f.ligatures[pair]) {
      tok = pair
      g = f.ligatures[pair]
      i += 1
    }
    if (prev !== null) pen += f.kern[prev + tok] ?? 0
    glyphs.push([tok, pen])
    yMin = Math.min(yMin, g[1])
    yMax = Math.max(yMax, g[2])
    pen += g[0]
    prev = tok
  }
  if (!glyphs.length) yMin = yMax = 0
  const h = yMax - yMin
  const d = -yMin
  return { w: pen, a: Math.max(h - d, f.ascent), d: Math.max(d, f.descent), glyphs }
}

/** A text placed as matplotlib places it (Text._get_layout): its box, the pen at its baseline, its angle. */
export interface Placed { pt: number, run: Run, box: Box, pen: [number, number], rot: number, color: string }

export function rotate(deg: number, x: number, y: number): [number, number] {
  const t = deg * Math.PI / 180
  const s = Math.sin(t)
  const c = Math.cos(t)
  return [x * c - y * s, x * s + y * c]
}

export function placeRun(run: Run, pt: number, x: number, y: number, ha: string, va: string,
  { rot = 0, anchor = false, color = '#000000' } = {}): Placed {
  const { w, a, d } = run
  const corners: [number, number][] = [[0, -(a + d)], [0, 0], [w, 0], [w, -(a + d)]]
  const rc = corners.map(([px, py]) => rotate(rot, px, py))
  const xmin = Math.min(...rc.map((p) => p[0]))
  const xmax = Math.max(...rc.map((p) => p[0]))
  const ymin = Math.min(...rc.map((p) => p[1]))
  const ymax = Math.max(...rc.map((p) => p[1]))
  const baseline = a
  let ox: number
  let oy: number
  if (!anchor) {
    ox = ha === 'left' ? xmin : ha === 'right' ? xmax : (xmin + xmax) / 2
    oy = va === 'bottom' ? ymin : va === 'top' ? ymax : va === 'center' ? (ymin + ymax) / 2
      : va === 'baseline' ? ymin + d : ymin + (ymax - ymin) - baseline / 2
  } else {
    const [xmin1, ymin1] = corners[0]
    const [xmax1, ymax1] = corners[2]
    ox = ha === 'left' ? xmin1 : ha === 'right' ? xmax1 : (xmin1 + xmax1) / 2
    oy = va === 'bottom' ? ymin1 : va === 'top' ? ymax1 : va === 'center' ? (ymin1 + ymax1) / 2
      : va === 'baseline' ? ymax1 - baseline : ymax1 - baseline / 2
    ;[ox, oy] = rotate(rot, ox, oy)
  }
  const x0 = x + xmin - ox
  const y0 = y + ymin - oy
  const [lx, ly] = rotate(rot, 0, -a)
  return { pt, run, box: [x0, y0, x0 + (xmax - xmin), y0 + (ymax - ymin)], pen: [x + lx - ox, y + ly - oy], rot, color }
}

// ---------------------------------------------------------------- ticks (AutoLocator, ScalarFormatter)

/** Python's float divmod */
export function pyDivmod(x: number, y: number): [number, number] {
  let mod = x % y
  let div = (x - mod) / y
  if (mod) {
    if ((y < 0) !== (mod < 0)) {
      mod += y
      div -= 1.0
    }
  } else mod = y < 0 ? -0 : 0
  let floordiv: number
  if (div) {
    floordiv = Math.floor(div)
    if (div - floordiv > 0.5) floordiv += 1.0
  } else floordiv = x / y < 0 || Object.is(x / y, -0) ? -0 : 0
  return [floordiv, mod]
}

/** 10 ** k for an integral k, as Python's float power gives it */
export const pow10 = (k: number) => (k >= 0 ? 10 ** k : 1 / 10 ** -k)

function scaleRange(vmin: number, vmax: number, n: number): [number, number] {
  const dv = Math.abs(vmax - vmin)
  const meanv = (vmax + vmin) / 2
  const offset = Math.abs(meanv) / dv < 100 ? 0 : Math.sign(meanv) * pow10(Math.floor(Math.log10(Math.abs(meanv))))
  return [pow10(Math.floor(Math.log10(dv / n))), offset]
}

function nonsingular(vmin: number, vmax: number, expander: number, tiny: number): [number, number] {
  if (!Number.isFinite(vmin) || !Number.isFinite(vmax)) return [-expander, expander]
  if (vmax < vmin) [vmin, vmax] = [vmax, vmin]
  const maxabs = Math.max(Math.abs(vmin), Math.abs(vmax))
  if (maxabs < (1e6 / tiny) * 2.2250738585072014e-308) return [-expander, expander]
  if (vmax - vmin <= maxabs * tiny) {
    if (vmax === 0 && vmin === 0) return [-expander, expander]
    return [vmin - expander * Math.abs(vmin), vmax + expander * Math.abs(vmax)]
  }
  return [vmin, vmax]
}

/** MaxNLocator(nbins, steps=[1, 2, 2.5, 5, 10]).tick_values */
export function tickValues(lim: [number, number], nbins: number): number[] {
  const [vmin, vmax] = nonsingular(lim[0], lim[1], 1e-13, 1e-14)
  const [scale, offset] = scaleRange(vmin, vmax, nbins)
  const v0 = vmin - offset
  const v1 = vmax - offset
  const steps = STEPS.map((s) => s * scale)
  const raw = (v1 - v0) / nbins
  let istep = steps.findIndex((s) => s >= raw)
  if (istep < 0) istep = steps.length - 1
  const tol = 1e-10
  let ticks: number[] = []
  for (let j = istep; j >= 0; j--) {
    const step = steps[j]
    const best = pyDivmod(v0, step)[0] * step
    const [dl, ml] = pyDivmod(v0 - best, step)
    const low = Math.abs(ml / step - 1) < tol ? dl + 1 : dl
    const [dh, mh] = pyDivmod(v1 - best, step)
    const high = Math.abs(mh / step) < tol ? dh : dh + 1
    ticks = []
    for (let k = low; k < high + 1; k += 1) ticks.push(k * step + best)
    if (ticks.filter((t) => t <= v1 && t >= v0).length >= 2) break
  }
  return ticks.map((t) => t + offset)
}

/** numpy's round(x, decimals) for decimals >= 0 (rint of the scaled value) */
export function npRound(x: number, decimals: number): number {
  const f = 10 ** decimals
  const v = x * f
  let r = Math.round(v)
  if (Math.abs(v - Math.trunc(v)) === 0.5) r = 2 * Math.round(v / 2)
  return r / f
}

/** ScalarFormatter.format_ticks (offsets on, scientific outside 1e-5 to 1e6, the Unicode minus) */
export function formatTicks(locs: number[], view: [number, number]): string[] {
  if (!locs.length) return []
  const [vmin, vmax] = [Math.min(...view), Math.max(...view)]
  const visible = locs.filter((l) => vmin <= l && l <= vmax)
  // _compute_offset
  let offset = 0
  if (visible.length) {
    const lmin = Math.min(...visible)
    const lmax = Math.max(...visible)
    if (!(lmin === lmax || (lmin <= 0 && 0 <= lmax))) {
      const [absMin, absMax] = [Math.abs(lmin), Math.abs(lmax)].sort((p, q) => p - q)
      const sign = lmin < 0 ? -1 : 1
      const ommax = Math.ceil(Math.log10(absMax))
      let oom = ommax
      while (Math.floor(absMin / 10 ** oom) === Math.floor(absMax / 10 ** oom)) oom -= 1
      oom += 1
      if ((absMax - absMin) / 10 ** oom <= 1e-2) {
        oom = ommax
        while (!(Math.floor(absMax / 10 ** oom) - Math.floor(absMin / 10 ** oom) > 1)) oom -= 1
        oom += 1
      }
      offset = Math.floor(absMax / 10 ** oom) >= 10 ** 3 ? sign * Math.floor(absMax / 10 ** oom) * 10 ** oom : 0
    }
  }
  // _set_order_of_magnitude
  let oomag = 0
  const vis = locs.filter((l) => vmin <= l && l <= vmax).map(Math.abs)
  if (vis.length) {
    const val = Math.max(...vis)
    const oom = offset ? Math.floor(Math.log10(vmax - vmin)) : val === 0 ? 0 : Math.floor(Math.log10(val))
    oomag = oom <= -5 || oom >= 6 ? oom : 0
  }
  // _set_format
  const scaled = locs.map((l) => (l - offset) / 10 ** oomag)
  let range = Math.max(...scaled) - Math.min(...scaled)
  if (range === 0) range = Math.max(...scaled.map(Math.abs))
  if (range === 0) range = 1
  const rangeOom = Math.floor(Math.log10(range))
  let sigfigs = Math.max(0, 3 - rangeOom)
  const thresh = 1e-3 * pow10(rangeOom)
  while (sigfigs >= 0) {
    if (Math.max(...scaled.map((l) => Math.abs(l - npRound(l, sigfigs)))) < thresh) sigfigs -= 1
    else break
  }
  sigfigs += 1
  return locs.map((x) => {
    let xp = (x - offset) / 10 ** oomag
    if (Math.abs(xp) < 1e-8) xp = 0
    return pyFixed(xp, sigfigs).replace(/-/g, '−')
  })
}

export const union = (boxes: Box[]): Box => [Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])),
  Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))]
export const sized = (b: Box) => b[2] - b[0] > 0 && b[3] - b[1] > 0

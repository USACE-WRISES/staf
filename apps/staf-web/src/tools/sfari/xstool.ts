// SFARI's cross-section hydraulics tool (sfari/app.py _xs_open, _xs_modal, xs_calc, xs_view, _xs_attach
// and _xs_svg): a synthetic bankfull section from Bieger's regional curve at the site's drainage area,
// solved for the stage that carries the target discharge, drawn and tabulated as the boxes change; Attach
// writes the results into the four hydraulics metrics' evidence.
import { type Child, h, HTML, outputHtml, type Tag } from '../../app/htm'
import { pyFixed } from '../../app/pyfmt'
import { floatKeys, markFloats } from '../../app/pyjson'
import { bankfullGeometry } from '../../engine/bieger'
import type { DataSource } from '../../data/source'
import { column, inputNumeric, modal, modalButton, outputUi, row } from '../../shared/shinyui'
import { XS_MANNING_SOURCE } from './evidence'
import { compute, type Hydraulics, type Point, solveStage, syntheticSection } from './xscalc'

type Obj = Record<string, any>
const BOXES = ['xs_slope', 'xs_nchan', 'xs_nover', 'xs_targetq', 'xs_d50'] as const

/** The geometry the tool works on (xs_geom), saved with the assessment file. */
export interface XsGeom { points: Point[], lb: number, rb: number, bankfull_stage: number, slope: number, da: number, width_m: number,
  depth_m: number, division_name: string }

/** _xs_open's geometry: Bieger's bankfull at the drainage area, the synthetic section, the reach's slope. */
export async function openGeometry(src: DataSource, dl: Obj): Promise<XsGeom | null> {
  const da = dl.drainage_area_sqkm
  if (da === null || da === undefined) return null
  const bf = await bankfullGeometry(src, da, dl.snapped_lat ?? null, dl.snapped_lon ?? null)
  const [pts, lb, rb, stage] = syntheticSection(bf.width_m, bf.depth_m)
  for (const p of pts) markFloats(p)
  const geom: XsGeom = { points: pts, lb, rb, bankfull_stage: stage, slope: dl.slope || 0.001, da, width_m: bf.width_m, depth_m: bf.depth_m,
    division_name: bf.division_name }
  return floatKeys(geom, 'lb', 'rb', 'bankfull_stage', 'slope', 'da', 'width_m', 'depth_m')
}

const pyRound0 = (x: number) => Number(pyFixed(x, 0))

/** _xs_modal */
export function xsModal(geom: XsGeom, view: Child): Tag {
  const slope = geom.slope || 0.001
  const bf = compute(geom.points, geom.bankfull_stage, slope, 0.035, { nLob: 0.06, nRob: 0.06, lb: geom.lb, rb: geom.rb })
  const qBf = bf && bf.Q ? pyRound0(bf.Q) : 100
  return modal([
    h.div(`Synthetic bankfull section: ${geom.division_name} regional curve (Bieger 2015) at DA ${pyFixed(geom.da, 0)} km²: bankfull width `
      + `${pyFixed(geom.width_m, 1)} m, mean depth ${pyFixed(geom.depth_m, 2)} m. Adjust slope, roughness, and target discharge; results `
      + 'feed the hydraulics metrics.', { class: 'easi-instr' }),
    row(
      column(4, inputNumeric('sfari-xs_slope', 'Channel slope (m/m)', Number(pyFixed(Number(slope), 5)), { min: 0.00001, step: 0.0005 })),
      column(4, inputNumeric('sfari-xs_nchan', 'Manning n (channel)', 0.035, { step: 0.005 })),
      column(4, inputNumeric('sfari-xs_nover', 'Manning n (overbank)', 0.06, { step: 0.005 }))),
    row(
      column(6, inputNumeric('sfari-xs_targetq', 'Target discharge Q (cfs)', qBf, { min: 0, step: 1 })),
      column(6, inputNumeric('sfari-xs_d50', 'Bed D50 (mm)', '30.0', { min: 0, step: 1 }))),
    outputUi('sfari-xs_view', view),
    h.div(h.button('Attach results to hydraulics metrics', { 'data-xs-attach': '1', type: 'button' }, { class: 'sfari-btn primary' }),
      { class: 'easi-pane-actions' }),
  ], { title: 'Cross-section hydraulics', easyClose: true, size: 'l', footer: modalButton('Close') })
}

/** The dialog's boxes as the server reads them. */
export interface Boxes { slope: unknown, nchan: unknown, nover: unknown, targetq: unknown, d50: unknown }

/** A number box as Shiny's number binding reads it: null when blank, a number, or the text. */
function boxValue(id: string): unknown {
  const el = document.getElementById(`sfari-${id}`) as HTMLInputElement | null
  if (!el) return null
  const v = el.value
  if (/^\s*$/.test(v)) return null
  const n = Number(v)
  return Number.isNaN(n) ? v : n
}

/** The five boxes of the open dialog. */
export function readBoxes(): Boxes {
  return { slope: boxValue('xs_slope'), nchan: boxValue('xs_nchan'), nover: boxValue('xs_nover'), targetq: boxValue('xs_targetq'),
    d50: boxValue('xs_d50') }
}

/** float(x) as Python takes a box's value: null when it would raise. */
function pyFloatOf(v: unknown): number | null {
  if (v === null || v === undefined) return null
  if (typeof v === 'number') return v
  const t = String(v).trim().toLowerCase()
  if (/^[+-]?(nan|inf|infinity)$/.test(t)) return t.includes('nan') ? NaN : t.startsWith('-') ? -Infinity : Infinity
  const n = Number(t)
  return t !== '' && !Number.isNaN(n) ? n : null
}

/** xs_calc: the hydraulics at the stage that carries the target discharge, or null. */
export function xsCalc(geom: XsGeom | null, boxes: Boxes): Hydraulics | null {
  if (!geom) return null
  const slope = pyFloatOf(boxes.slope)
  const nchan = pyFloatOf(boxes.nchan)
  const nover = pyFloatOf(boxes.nover)
  const tq = pyFloatOf(boxes.targetq)
  if (slope === null || nchan === null || nover === null || tq === null) return null
  if (slope <= 0 || tq <= 0 || nchan <= 0) return null
  const opts = { nLob: nover, nRob: nover, lb: geom.lb, rb: geom.rb }
  const stage = solveStage(geom.points, tq, slope, nchan, opts)
  if (stage === null) return null
  return compute(geom.points, stage, slope, nchan, opts)
}

/** _tau_c: the critical shear of the bed's D50 (lb/ft²), or null. */
export function tauC(d50mm: number | null): number | null {
  const d50ft = (d50mm || 0) / 304.8
  return d50ft > 0 ? 0.047 * 1.65 * 62.4 * d50ft : null
}

/** The D50 box as float(input.xs_d50() or 0), 0 when it would raise. */
function d50(boxes: Boxes): number {
  return pyFloatOf(boxes.d50 || 0) ?? 0.0
}

/** _interp_series */
function interpSeries(xs: number[], zs: number[], x: number): number {
  if (x <= xs[0]) return zs[0]
  if (x >= xs[xs.length - 1]) return zs[zs.length - 1]
  for (let i = 0; i < xs.length - 1; i++) {
    if (xs[i] <= x && x <= xs[i + 1]) {
      const x0 = xs[i]
      const x1 = xs[i + 1]
      if (x1 === x0) return zs[i]
      return zs[i] + (zs[i + 1] - zs[i]) * (x - x0) / (x1 - x0)
    }
  }
  return zs[zs.length - 1]
}

/** _xs_svg: the section with the water surface at ``stage`` (string SVG, so the dialog draws at once). */
export function xsSvg(points: Point[], stage: number | null, lb: number | null, rb: number | null): string {
  const xs = points.map((p) => p[0])
  const zs = points.map((p) => p[1])
  const xmin = Math.min(...xs)
  const xmax = Math.max(...xs)
  const zmin = Math.min(...zs)
  let zmax = Math.max(...zs)
  if (stage !== null) zmax = Math.max(zmax, stage)
  const W = 620
  const H = 300
  const pad = 34
  const dx = (xmax - xmin) || 1.0
  const dz = (zmax - zmin) || 1.0
  const sx = (x: number) => pad + (x - xmin) / dx * (W - 2 * pad)
  const sy = (z: number) => H - pad - (z - zmin) / dz * (H - 2 * pad)
  const f1 = (v: number) => pyFixed(v, 1)
  const parts = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;background:#fff;border:1px solid #e5e8ee;border-radius:6px;">`]
  if (stage !== null && stage > zmin) {
    const n = 300
    const step = dx / (n - 1)
    const xx = Array.from({ length: n }, (_, i) => xmin + i * step)
    const zz = xx.map((x) => interpSeries(xs, zs, x))
    let i = 0
    while (i < n) {
      if (zz[i] < stage) {
        let j = i
        while (j < n && zz[j] < stage) j += 1
        const poly = [`${f1(sx(xx[i]))},${f1(sy(stage))}`]
        for (let k = i; k < j; k++) poly.push(`${f1(sx(xx[k]))},${f1(sy(zz[k]))}`)
        poly.push(`${f1(sx(xx[j - 1]))},${f1(sy(stage))}`)
        parts.push(`<polygon points="${poly.join(' ')}" fill="#8fbce6" fill-opacity="0.65"/>`)
        i = j
      } else i += 1
    }
    parts.push(`<line x1="${f1(sx(xmin))}" y1="${f1(sy(stage))}" x2="${f1(sx(xmax))}" y2="${f1(sy(stage))}" stroke="#1f6feb" stroke-width="1" stroke-dasharray="4 3"/>`)
  }
  for (const bx of [lb, rb]) {
    if (bx !== null) parts.push(`<line x1="${f1(sx(bx))}" y1="${pad}" x2="${f1(sx(bx))}" y2="${H - pad}" stroke="#c0c8d4" stroke-width="1" stroke-dasharray="2 3"/>`)
  }
  const bed = points.map(([x, z]) => `${f1(sx(x))},${f1(sy(z))}`).join(' ')
  parts.push(`<polyline points="${bed}" fill="none" stroke="#5b4636" stroke-width="2"/>`)
  parts.push(`<text x="${pyFixed(W / 2, 0)}" y="${H - 6}" font-size="11" text-anchor="middle" fill="#667">Station (ft) · Elevation (ft)</text>`)
  parts.push('</svg>')
  return parts.join('')
}

/** xs_view: the drawing and, with a solution, its table. */
export function xsView(geom: XsGeom | null, boxes: Boxes): Child {
  if (!geom) return null
  const res = xsCalc(geom, boxes)
  const stage = res ? res.stage : geom.bankfull_stage
  const img = new HTML(`<div style="margin-bottom:8px;">${xsSvg(geom.points, stage, geom.lb, geom.rb)}</div>`)
  if (!res) return h.div(img, h.p('Enter a valid slope, roughness, and discharge.', { class: 'text-muted', style: 'font-size:12px;' }))
  const tc = tauC(d50(boxes))
  const rows: [string, string][] = [
    ['Discharge Q', `${pyFixed(res.Q, 0)} cfs`],
    ['Water-surface stage / max depth', `${pyFixed(res.depth_max, 2)} ft`],
    ['Mean velocity V', `${pyFixed(res.V, 2)} ft/s`],
    ['Top width T', `${pyFixed(res.T, 1)} ft`],
    ['Hydraulic radius R', `${pyFixed(res.R, 2)} ft`],
    ['Bed shear τ = γRS', `${pyFixed(res.tau, 3)} lb/ft²`],
    ['Unit stream power τV', `${pyFixed(res.power, 2)} lb/(ft·s)`],
    ['Froude number', `${pyFixed(res.froude, 2)} (${res.froude > 1 ? 'super' : 'sub'}critical)`],
  ]
  if (tc) rows.push(['Critical shear τc (D50)', `${pyFixed(tc, 3)} lb/ft² → ${res.tau > tc ? 'mobilizes' : 'stable'}`])
  const table = h.table(h.tbody(...rows.map(([a, b]) => h.tr(h.td(a), h.td(h.b(b))))), { class: 'easi-tbl' })
  return h.div(img, table)
}

/** _xs_attach: the four hydraulics metrics' evidence from the solution (null without one). */
export function attachEvidence(geom: XsGeom | null, boxes: Boxes, evidence: Obj): Obj | null {
  const res = xsCalc(geom, boxes)
  if (!res) return null
  const tc = tauC(d50(boxes))
  const q = res.Q
  const ev: Obj = { ...evidence }
  const put = (mid: string, vt: string, note: string, fvt = '') => {
    ev[mid] = { metric_id: mid, value_text: vt, field_value_text: fvt, suggested_likert: null, confidence: 'M', source: XS_MANNING_SOURCE,
      source_url: '', status: 'ok', note }
  }
  const f = pyFixed
  put('low-flow-baseflow-dynamics-low-flow-depth', `modeled max depth ${f(res.depth_max, 2)} ft at Q ${f(q, 0)} cfs`,
    'From the cross-section Manning solver.', `Low-flow depth ${f(res.depth_max, 2)} ft`)
  put('low-flow-baseflow-dynamics-low-flow-velocity', `modeled velocity ${f(res.V, 2)} ft/s at Q ${f(q, 0)} cfs`,
    'From the cross-section Manning solver.', `Low-flow velocity ${f(res.V, 2)} ft/s`)
  put('high-flow-dynamics-peak-flow-capacity-velocity-shear-stress', `V ${f(res.V, 2)} ft/s, τ ${f(res.tau, 3)} lb/ft², Fr ${f(res.froude, 2)}`,
    'At the modeled stage.', `Peak V ${f(res.V, 2)} ft/s, Fr ${f(res.froude, 2)}`)
  const mob = tc ? ` → ${res.tau > tc ? 'mobilizes' : 'stable'}` : ''
  put('high-flow-dynamics-bed-mobilization-frequency',
    tc ? `bed shear τ ${f(res.tau, 3)} vs critical τc ${f(tc, 3)} lb/ft²${mob}` : `bed shear τ ${f(res.tau, 3)} lb/ft²`,
    'Shields comparison with the entered D50.', `Bed shear tau ${f(res.tau, 3)} lb/ft2`)
  return ev
}

/** The dialog's boxes post as Shiny's number inputs do: typing after a 250 ms pause, a change at once;
 * ``changed`` redraws the view. Returns a function that stops listening. */
export function listenBoxes(changed: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  const isBox = (e: Event) => {
    const el = e.target as HTMLElement | null
    return !!el && BOXES.some((b) => el.id === `sfari-${b}`)
  }
  const typing = (e: Event) => {
    if (!isBox(e)) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(changed, 250)
  }
  const change = (e: Event) => {
    if (!isBox(e)) return
    if (timer) clearTimeout(timer)
    changed()
  }
  document.addEventListener('input', typing)
  document.addEventListener('keyup', typing)
  document.addEventListener('change', change)
  return () => {
    if (timer) clearTimeout(timer)
    document.removeEventListener('input', typing)
    document.removeEventListener('keyup', typing)
    document.removeEventListener('change', change)
  }
}

export { outputHtml }

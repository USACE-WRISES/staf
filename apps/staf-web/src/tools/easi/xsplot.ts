// EASI's cross-section plot (easi/xsplotly.py figure): the profile on the thalweg datum, the water body
// up to bankfull, the bankfull, floodprone and low-bank lines with their labels, and the default
// window, as the plotly.py figure's JSON (data and layout, with plotly.py's default template), drawn
// with plotly.js 3.6.0, the version the Shiny app's widget bundles.
import template from '../../app/content/easi/plotly-template.json'
import { argmin, displayWindow, FT_PER_M, VIEW_HEADROOM, VIEW_MIN_HALF_M, wettedSpan } from '../../engine/geomorph'

const LABEL_EPS_FRAC = 0.06
const LABEL_CLASH_XSHIFT_PX = -70

export interface Figure { data: Record<string, any>[], layout: Record<string, any> }

/** _water_polygon: the bed densified at the level's crossings, the surface raised to it over the water body. */
function waterPolygon(xs: number[], h: number[], level: number, span: [number, number, boolean] | null): [number[], number[], number[]] {
  if (span === null) return [[...xs], [...h], [...h]]
  const [left, right] = span
  const x2 = [xs[0]]
  const bed2 = [h[0]]
  for (let i = 1; i < xs.length; i++) {
    const [x0, y0, x1, y1] = [xs[i - 1], h[i - 1], xs[i], h[i]]
    if ((y0 < level && level < y1) || (y1 < level && level < y0)) {
      const t = (level - y0) / (y1 - y0)
      x2.push(x0 + t * (x1 - x0))
      bed2.push(level)
    }
    x2.push(x1)
    bed2.push(y1)
  }
  const surf2 = x2.map((xv, k) => (bed2[k] <= level && left <= xv && xv <= right ? level : bed2[k]))
  return [x2, bed2, surf2]
}

const hline = (y: number, line: Record<string, any>) => ({ line, type: 'line', x0: 0, x1: 1, xref: 'x domain', y0: y, y1: y, yref: 'y' })
const label = (y: number, text: string, color: string, xshift?: number) => ({
  font: { color, size: 11 }, showarrow: false, text, x: 1, xanchor: 'right', xref: 'x domain', ...(xshift !== undefined ? { xshift } : {}),
  y, yanchor: 'bottom', yref: 'y',
})

/** xsplotly.figure */
export function figure(stations: number[], elevs: number[], thalweg: number | null, bankfullStage: number | null, floodplainStage: number | null,
  unit = 'ft', source: string | null = null): Figure {
  const u = unit === 'ft' ? FT_PER_M : 1.0
  const ul = unit === 'ft' ? 'ft' : 'm'
  const x = stations.map(Number)
  const z = elevs.map(Number)
  const thal = thalweg !== null ? thalweg : Math.min(...z)
  const ti = argmin(z)
  const x0 = x[ti]
  const xs = x.map((xi) => (xi - x0) * u)
  const h = z.map((zi) => (zi - thal) * u)
  const base = Math.min(...h) - 0.5
  const data: Record<string, any>[] = [
    { hoverinfo: 'skip', line: { width: 0 }, mode: 'lines', showlegend: false, x: xs, y: xs.map(() => base), type: 'scatter' },
    { fill: 'tonexty', fillcolor: '#efe9e1', hoverinfo: 'skip', line: { width: 0 }, mode: 'lines', showlegend: false, x: xs, y: h, type: 'scatter' },
  ]
  const bfH = bankfullStage !== null ? (bankfullStage - thal) * u : null
  const lbH = floodplainStage !== null ? (floodplainStage - thal) * u : null
  let xw = xs
  let bedW = h
  let surfW = h
  let waterFill = 'rgba(0,0,0,0)'
  if (bfH !== null) {
    ;[xw, bedW, surfW] = waterPolygon(xs, h, bfH, wettedSpan(xs, h, bfH, ti))
    waterFill = 'rgba(77,163,255,0.45)'
  }
  data.push({ hoverinfo: 'skip', line: { width: 0 }, mode: 'lines', showlegend: false, x: xw, y: bedW, type: 'scatter' })
  data.push({ fill: 'tonexty', fillcolor: waterFill, hoverinfo: 'skip', line: { width: 0 }, mode: 'lines', showlegend: false, x: xw, y: surfW, type: 'scatter' })
  data.push({ hovertemplate: `station %{x:.0f} ${ul}<br>height %{y:.1f} ${ul}<extra></extra>`, line: { color: '#5b4a3a', width: 1.8 }, mode: 'lines',
    showlegend: false, x: xs, y: h, type: 'scatter' })

  let ymax = Math.max(...h)
  const topLine = bfH !== null ? Math.max(2.0 * bfH, lbH || 0.0) : 0.0
  let xRange: [number, number]
  if (topLine > 0) {
    const ceiling = VIEW_HEADROOM * topLine
    const [lo, hi] = displayWindow(xs, h, ceiling, ti, VIEW_MIN_HALF_M * u)
    const padx = hi > lo ? 0.03 * (hi - lo) : 1.0
    xRange = [lo - padx, hi + padx]
    let hmaxWin = 0.0
    let any = false
    for (let k = 0; k < xs.length; k++) {
      if (lo <= xs[k] && xs[k] <= hi) {
        hmaxWin = any ? Math.max(hmaxWin, h[k]) : h[k]
        any = true
      }
    }
    if (!any) hmaxWin = 0.0
    ymax = Math.max(topLine, Math.min(hmaxWin, ceiling))
  } else {
    const xb = xs.length ? Math.max(...xs.map(Math.abs)) : 1.0
    xRange = [-xb, xb]
    if (bfH !== null) ymax = Math.max(ymax, 2.0 * bfH)
    if (lbH !== null) ymax = Math.max(ymax, lbH)
  }
  const pad = ymax > base ? 0.06 * (ymax - base) : 1.0

  const shapes: Record<string, any>[] = [hline(0.0, { color: '#b9aa97', dash: 'dot', width: 0.8 })]
  const annotations: Record<string, any>[] = []
  if (bfH !== null) {
    shapes.push(hline(bfH, { color: '#1f6fc0', width: 1.4 }))
    annotations.push(label(bfH, 'bankfull', '#1f6fc0'))
    shapes.push(hline(2.0 * bfH, { color: '#9a6b3f', dash: 'dot', width: 1.2 }))
    annotations.push(label(2.0 * bfH, 'floodprone', '#9a6b3f'))
  }
  if (lbH !== null) {
    const yspan = (ymax + pad) - base
    const clash = bfH !== null && (Math.abs(lbH - 2.0 * bfH) < LABEL_EPS_FRAC * yspan || Math.abs(lbH - bfH) < LABEL_EPS_FRAC * yspan)
    shapes.push(hline(lbH, { color: '#3a8a5c', dash: 'dash', width: 1.4 }))
    annotations.push(label(lbH, 'low bank', '#3a8a5c', clash ? LABEL_CLASH_XSHIFT_PX : undefined))
  }
  if (source) {
    annotations.push({ font: { color: '#9aa4b2', size: 9 }, showarrow: false, text: source, x: 1.0, xanchor: 'right', xref: 'paper', y: 0,
      yanchor: 'top', yref: 'paper', yshift: -46 })
  }
  const layout: Record<string, any> = {
    annotations, dragmode: 'zoom', hovermode: 'closest',
    margin: { b: source ? 62 : 42, l: 55, r: 74, t: 12 },
    modebar: { remove: ['resetScale2d', 'autoScale2d', 'select2d', 'lasso2d', 'toImage', 'toggleSpikelines', 'toggleHover'] },
    paper_bgcolor: 'white', plot_bgcolor: 'white', shapes, showlegend: false,
    template: JSON.parse(JSON.stringify(template)),
    xaxis: { gridcolor: '#eef0f4', range: xRange, title: { text: `Station (${ul})` }, zeroline: false },
    yaxis: { gridcolor: '#eef0f4', range: [base, ymax + pad], title: { text: `Height above bed (${ul})` } },
  }
  return { data, layout }
}

let plotlyP: Promise<any> | null = null

/** plotly.js, loaded with the first cross-section shown (and put on window for worksheet.js's modebar buttons). */
export function plotly(): Promise<any> {
  plotlyP ??= import('plotly.js-basic-dist-min').then((m: any) => {
    const P = m.default ?? m
    ;(window as any).Plotly = P
    return P
  })
  plotlyP.catch(() => { plotlyP = null })
  return plotlyP
}

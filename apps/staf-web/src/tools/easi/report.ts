// EASI's report popup (easi/app.py _report_modal and _report_body, easi/reportmap.py, the shared
// summary block of libs/staf_workbook): the site's name and point, the basin characteristics beside
// the basin map, the assessment summary, the metric table with its rollup and display toggles
// (report-controls.js), and the summary plots. Read-only: ratings and notes are edited on the
// worksheet. The downloads in its footer are Phase 6's.
import ecoregions from '../../app/content/shared/ecoregions.json'
import { type Child, h, HTML, pyHtmlEscape, type Tag, TagList } from '../../app/htm'
import { pyFixed, pyFixedComma, pyFloat } from '../../app/pyfmt'
import { displaySimplify } from '../../engine/simplify'
import type { SummaryInfo } from '../../shared/summary'
import { info } from '../sfari/markup'
import { criteriaBands } from './evaluator'
import { cwaMapping, functions, metricCalculation, metricDefinition, metricsById, OUTCOMES, RATINGS, FUNCTION_SCORE_MAX } from './method'
import { BORROWED_MARK, isBorrowed, markerNote, routedNotice } from './notices'
import { functionScore, functionScoreBandColor, functionScoreBandLabel, indexBandColor, ratingToIndex } from './scoring'
import { bar } from './markup'

type Obj = Record<string, any>
const DISC_ORDER = ['Hydrology', 'Hydraulics', 'Geomorphology', 'Physicochemistry', 'Biology']
const MAP_CODE: Record<string, [string, string]> = { D: ['D', 'Direct effect'], i: ['i', 'Indirect effect'], '-': ['–', 'No mapped effect'] }
const pyStr = (v: any): string => (typeof v === 'number' ? (Number.isInteger(v) && !Object.is(v, -0) ? String(v) : pyFloat(v)) : String(v))

// ---------------------------------------------------------------- the summary (libs/staf_workbook)

const L1_NAMES: Record<string, string> = { 1: 'Arctic Cordillera', 2: 'Tundra', 3: 'Taiga', 4: 'Hudson Plain', 5: 'Northern Forests',
  6: 'Northwestern Forested Mountains', 7: 'Marine West Coast Forest', 8: 'Eastern Temperate Forests', 9: 'Great Plains',
  10: 'North American Deserts', 11: 'Mediterranean California', 12: 'Southern Semiarid Highlands', 13: 'Temperate Sierras',
  14: 'Tropical Dry Forests', 15: 'Tropical Wet Forests' }
const NARS9_NAMES: Record<string, string> = { CPL: 'Coastal Plains', NAP: 'Northern Appalachians', NPL: 'Northern Plains',
  SAP: 'Southern Appalachians', SPL: 'Southern Plains', TPL: 'Temperate Plains', UMW: 'Upper Midwest', WMT: 'Western Mountains', XER: 'Xeric' }
const NOT_AVAILABLE = 'Not available'
const ECO = ((ecoregions as any).l3 ?? {}) as Record<string, any>

function cleanCode(code: any): string | null {
  if (code === null || code === undefined) return null
  let text = String(code).trim()
  if (text.endsWith('.0')) text = text.slice(0, -2)
  return text || null
}

/** model.summary.regions_for_l3 */
function regionsForL3(l3: any): Obj {
  const code = cleanCode(l3)
  const entry = ECO[code ?? ''] ?? {}
  const l1 = entry.l1 ?? null
  const nars = entry.nars9 ?? null
  return { l3: code, l3_name: entry.name ?? null, l2: entry.l2 ?? null, l2_name: entry.l2_name ?? null, l1, l1_name: L1_NAMES[l1 ?? ''] ?? null,
    nars9: nars, nars9_name: NARS9_NAMES[nars ?? ''] ?? null }
}

/** workbook._regions: EASI's own answers, named from the shared table. */
function regions(strata: Obj): Obj {
  const out = regionsForL3(strata.l3)
  const l2 = strata.l2
  if (l2 && String(l2) !== String(out.l2 || '')) {
    out.l2 = String(l2)
    const name = String(strata.l2_name || '').toLowerCase().replace(/(^|[^a-z])([a-z])/g, (m, a, b) => a + b.toUpperCase())
    out.l2_name = name || null
  }
  if (strata.l1) {
    out.l1 = String(strata.l1)
    out.l1_name = L1_NAMES[out.l1] ?? null
  }
  if (strata.nars9) {
    out.nars9 = String(strata.nars9)
    out.nars9_name = NARS9_NAMES[out.nars9] ?? null
  }
  return out
}

const region = (code: any, name: any) => (!code && !name ? NOT_AVAILABLE : code && name ? `${name} (${code})` : String(name || code))
const isNum = (v: any) => v !== null && v !== undefined && typeof v !== 'boolean' && Number.isFinite(Number(v)) && String(v).trim() !== ''

/** calculator.site_identity */
export function siteIdentity(result: Obj): Obj {
  const d = result.delineation || {}
  const anchor = result.siteAnchor || {}
  const clicked = anchor.clickedStream || {}
  const routed = anchor.anchorKind === 'hrSurrogate'
  const name = (routed ? clicked.gnisName : d.gnis_name) || ''
  let comid = d.comid
  comid = comid === null || comid === undefined || comid === '' || comid === 'None' ? '' : String(comid)
  const reach = routed && ![null, undefined, '', 'None'].includes(clicked.nhdplusId) ? `NHDPlusID ${clicked.nhdplusId}` : comid ? `COMID ${comid}` : ''
  const coords = isNum(d.snapped_lat) && isNum(d.snapped_lon) ? `${pyFixed(Number(d.snapped_lat), 5)}, ${pyFixed(Number(d.snapped_lon), 5)}` : ''
  const label = [name || (reach ? 'Unnamed stream' : ''), reach].filter(Boolean).join(', ')
  return { name, reach, comid, coords, label }
}

/** workbook.summary_info: the site as the workbook's Summary tab reads it. */
export function summaryInfo(result: Obj): SummaryInfo {
  const d = result.delineation || {}
  const report = result.report || {}
  let order = d.stream_order
  if (order === null || order === undefined) {
    const hit = ((report.basin || {}).rows || []).find((row: any) => Array.isArray(row) && row.length === 2 && row[0] === 'Stream order')
    order = hit ? hit[1] : null
  }
  return { app: 'EASI', reachName: siteIdentity(result).name || '(unnamed stream)', reachLengthFt: d.reach_length_ft,
    drainageAreaSqkm: d.drainage_area_sqkm, streamOrder: order, regions: regions(report.strata || {}) }
}

/** workbook.summary_info(...).rows() */
export function summaryRows(result: Obj): [string, string][] {
  const d = result.delineation || {}
  const report = result.report || {}
  let order = d.stream_order
  if (order === null || order === undefined) {
    const hit = ((report.basin || {}).rows || []).find((row: any) => Array.isArray(row) && row.length === 2 && row[0] === 'Stream order')
    order = hit ? hit[1] : null
  }
  const r = regions(report.strata || {})
  const name = siteIdentity(result).name || '(unnamed stream)'
  const length = isNum(d.reach_length_ft) ? `${pyFixedComma(Number(d.reach_length_ft), 0)} ft` : NOT_AVAILABLE
  const area = isNum(d.drainage_area_sqkm) ? `${pyFixedComma(Number(d.drainage_area_sqkm), 2)} km²` : NOT_AVAILABLE
  const ord = isNum(order) && Number(order) > 0 ? String(Math.trunc(Number(order))) : NOT_AVAILABLE
  return [['Reach name', name.trim() || 'Unnamed'], ['Assessment tier', 'Screening'], ['Reach length', length], ['Drainage area', area],
    ['Stream order (Strahler)', ord], ['NARS-9 region', region(r.nars9, r.nars9_name)], ['EPA Level I ecoregion', region(r.l1, r.l1_name)],
    ['EPA Level II ecoregion', region(r.l2, r.l2_name)], ['EPA Level III ecoregion', region(r.l3, r.l3_name)]]
}

/** web.summary_block */
export function summaryBlock(rows: [string, string][], scenario: [string, string] | null): Tag {
  const all: [string, string][] = []
  if (scenario) {
    all.push(['Scenario', scenario[0]])
    if (scenario[1]) all.push(['Description', scenario[1]])
  }
  all.push(...rows)
  return h.table(h.tbody(...all.map(([label, value]) => h.tr(h.th(label), h.td(value)))), { class: 'staf-sum' })
}

// ---------------------------------------------------------------- the metric table

/** _metric_tip_html: the (i) card of a report row. */
function metricTipHtml(name: string, definition: string, source: string, calc: string, note: string, crit: Obj, dflt: string,
  landCover: Obj | null, riparian: Obj | null): string {
  const e = pyHtmlEscape
  const parts = [`<div class="easi-tip-title">${e(name || '')}</div>`]
  if (definition) parts.push(`<div class="easi-tip-sec"><span class="easi-tip-lbl">Definition</span>${e(definition)}</div>`)
  if (source) {
    const sub = note && !landCover ? `<div class="easi-tip-sub">${e(note)}</div>` : ''
    parts.push(`<div class="easi-tip-sec"><span class="easi-tip-lbl">Source</span>${e(source)}${sub}</div>`)
  }
  if (calc) parts.push(`<div class="easi-tip-sec"><span class="easi-tip-lbl">Calculation</span>${e(calc)}</div>`)
  if (riparian) {
    const veg = [['forest', 'Forest'], ['shrub', 'Shrub'], ['grassland', 'Grassland'], ['wetland', 'Wetland']]
      .map(([k, lbl]) => `<div class="easi-tip-crit">${e(lbl)} ${e(k in riparian ? pyFloat(Number(riparian[k])) : '0')}%</div>`)
    veg.push(`<div class="easi-tip-crit"><b>Natural vegetation ${e('total' in riparian ? pyFloat(Number(riparian.total)) : '0')}%</b></div>`)
    parts.push('<div class="easi-tip-sec"><span class="easi-tip-lbl">Riparian vegetation</span>' + veg.join('') + '</div>')
  }
  if (landCover) {
    const gov = landCover.governing
    const rows: string[] = []
    for (const [key, label] of [['impervious', 'Impervious'], ['agriculture', 'Agricultural']]) {
      const d = landCover[key]
      const bands = landCover[`${key}_bands`] || {}
      const thr = RATINGS.filter((b) => bands[b]).map((b) => `${b} ${bands[b]}`).join(' / ')
      let line: string
      let dot = ''
      if (d) {
        line = `${e(label)} ${e(pyFloat(Number(d.pct)))}% &rarr; ${e(pyStr(d.rating))}` + (thr ? ` (${e(thr)})` : '')
        if (RATINGS.includes(d.rating)) dot = `<span class="easi-tip-dot ${d.rating.toLowerCase()}"></span>`
      } else line = `${e(label)}: no data`
      rows.push('<div class="easi-tip-crit">' + dot + (key === gov ? `<b>${line} [governs]</b>` : line) + '</div>')
    }
    parts.push('<div class="easi-tip-sec"><span class="easi-tip-lbl">Land-cover indicators</span><span class="easi-tip-default">more limiting governs</span>'
      + rows.join('') + '</div>')
  } else {
    const rows: string[] = []
    for (const band of RATINGS) {
      const c = crit[band]
      if (c) rows.push(`<div class="easi-tip-crit"><span class="easi-tip-dot ${band.toLowerCase()}"></span><b>${band}</b>&nbsp;${e(c)}</div>`)
    }
    if (rows.length) {
      const d = dflt ? `<span class="easi-tip-default">default: ${e(dflt)}</span>` : ''
      parts.push(`<div class="easi-tip-sec"><span class="easi-tip-lbl">Scoring</span>${d}${rows.join('')}</div>`)
    }
  }
  return parts.join('')
}

/** _metric_card_tip */
function metricCardTip(row: Obj): Tag | null {
  const mid = row.metricId
  let lc = row.landCover
  if (lc) lc = { ...lc, impervious_bands: criteriaBands(mid, 'impervious', metricsById()[mid]?.criteria), agriculture_bands: criteriaBands(mid, 'agriculture', metricsById()[mid]?.criteria) }
  const calc = (row.scoring || {}).equation || metricCalculation(mid) || "See the metric's Scoring panel for the equation and breakpoints."
  const note = [isBorrowed(row) ? row.anchorNote : null, row.note].filter((x) => x).join(' ')
  const crit = (row.criteriaBands && Object.keys(row.criteriaBands).length ? row.criteriaBands : metricsById()[mid]?.criteria) || {}
  return info(null, metricTipHtml(row.name, metricDefinition(mid), row.source || '', calc, note, crit, row.generatedRating || 'n/a', lc || null, row.ripVeg || null))
}

const rateChip = (r: Obj) => h.span(r.rating || '—', { class: `easi-rate-chip rate-${r.rating || 'auto'}` })

/** _fnscore_cell */
function fnscoreCell(r: Obj, meta: Obj): Tag {
  const fs = r.functionScore
  if (fs === null || fs === undefined) return h.td('', { class: 'easi-fs-cell' })
  const pct = Math.max(2.0, Math.min(98.0, (fs / FUNCTION_SCORE_MAX) * 100))
  const slider = h.div(
    h.div(h.div(h.span('NF'), h.span('AR'), h.span('F'), { class: 'easi-fslider-labels' }),
      h.div(h.div({ class: 'easi-fslider-knob', style: `left:${pyFixed(pct, 1)}%;` }), { class: 'easi-fslider-track' }), { class: 'easi-fslider-bars' }),
    h.span(String(fs), { class: 'easi-fslider-num' }), { class: 'easi-fslider' })
  const plain = h.span(String(fs), h.span(functionScoreBandLabel(fs), { class: 'easi-fnf-badge', style: `background:${functionScoreBandColor(fs)};` }),
    { class: 'easi-fscore-plain' })
  const kids: Child[] = [slider, plain]
  if (r.status === 'override' && RATINGS.includes(r.generatedRating)) {
    kids.push(h.span(`(auto: ${functionScore(ratingToIndex(r.generatedRating, (meta[r.metricId] || {}).indexMidpoints))})`, { class: 'easi-auto-cue' }))
  }
  return h.td(...kids, { class: 'easi-fs-cell' })
}

/** _rollup_rows: the outcome rollup, aligned under the mapping columns or standalone. */
function rollupRows(outcomes: Obj, eci: number | null, aligned: boolean): Tag[] {
  const valCls = aligned ? 'easi-col-map' : ''
  const label = (text: string) => h.th(text, aligned ? { colspan: '5' } : {}, { class: 'easi-rollup-lbl' })
  const value = (text: string, tint: number | null | undefined = undefined, span?: number) => {
    const attrs: Obj = { class: (valCls + (tint !== undefined ? ' easi-band' : '')).trim() }
    if (tint !== undefined) attrs.style = `background:${indexBandColor(tint)};`
    if (span) attrs.colspan = String(span)
    return h.td(text, attrs)
  }
  const trail = () => (aligned ? [h.td('', { class: 'easi-col-adv' }), h.td('', { class: 'easi-col-adv' }), h.td('', { class: 'easi-note-cell' })] : [])
  const row = (th: Tag, cells: Tag[], cls: string) => h.tr(th, ...cells, ...trail(), { class: cls })
  const keys = ['physical', 'chemical', 'biological']
  const calc = (lbl: string, fn: (o: Obj) => string) => row(label(lbl), keys.map((k) => value(fn(outcomes[k]))), 'easi-rollup-row')
  return [
    calc('Direct functions', (o) => String(o.direct)),
    calc('Indirect functions', (o) => String(o.indirect)),
    calc('Weighted total', (o) => pyFixed(o.weighted, 1)),
    calc('Max weighted', (o) => pyFixed(o.max, 1)),
    row(label('Outcome Sub-index'), keys.map((k) => value(pyFixed(outcomes[k].subIndex, 2), outcomes[k].subIndex)), 'easi-subindex-row'),
    row(label('Ecosystem Condition Index'), [value(eci === null || eci === undefined ? '—' : pyFixed(eci, 2), eci, 3)], 'easi-eci-row'),
  ]
}

/** _metric_table: the read-only metric grid, with the rollup below it (two renderings). */
function metricTable(rows: Obj[], notes: Obj, outcomes: Obj, eci: number | null): Tag {
  const meta = metricsById()
  const mapping = cwaMapping()
  const head = h.tr(h.th('Function'), h.th('Metric'), h.th('Value'), h.th('Rating'), h.th('Function Score'),
    h.th('Phy', { title: 'Physical' }, { class: 'easi-col-map' }), h.th('Chem', { title: 'Chemical' }, { class: 'easi-col-map' }),
    h.th('Bio', { title: 'Biological' }, { class: 'easi-col-map' }), h.th('Index', { class: 'easi-col-adv' }),
    h.th('Scored at', { class: 'easi-col-adv' }), h.th('Note', { class: 'easi-note-cell' }))
  const order = (d: string) => (DISC_ORDER.includes(d) ? DISC_ORDER.indexOf(d) : 99)
  const sorted = [...rows].sort((a, b) => order(a.discipline) - order(b.discipline) || (a.functionName < b.functionName ? -1 : a.functionName > b.functionName ? 1 : 0))
  const body: Tag[] = []
  const seen: string[] = []
  for (const r of sorted) {
    const mid = r.metricId
    if (!seen.includes(r.discipline)) {
      seen.push(r.discipline)
      body.push(h.tr(h.td(r.discipline, { colspan: '11' }), { class: 'easi-disc' }))
    }
    const isOvr = r.status === 'override'
    const idx = r.index
    const codes = mapping[r.functionId] || {}
    const mapCells = OUTCOMES.map((key) => {
      const [txt, title] = MAP_CODE[codes[key] ?? '-'] ?? MAP_CODE['-']
      return h.td(txt, { title: `${key[0].toUpperCase()}${key.slice(1)}: ${title}` }, { class: 'easi-col-map' })
    })
    const note = notes[mid] || r.userNote || ''
    body.push(h.tr(
      h.td(r.functionName),
      h.td(r.name, isBorrowed(r) ? h.sup(BORROWED_MARK, { title: r.anchorNote || '' }) : null),
      h.td(r.valueText),
      h.td(h.div(rateChip(r), metricCardTip(r), { class: 'easi-rate-cell' })),
      fnscoreCell(r, meta),
      ...mapCells,
      h.td(idx === null || idx === undefined ? '—' : pyFixed(idx, 2), { class: 'easi-col-adv' }),
      h.td(r.anchorLabel || '', { class: 'easi-col-adv' }),
      h.td(note, { class: 'easi-note-cell easi-rep-note' }),
      { 'data-mid': mid }, { class: isOvr ? 'easi-row-ovr' : '', style: r.rating ? '' : 'color:#aaa;' }))
  }
  const table = h.table(h.thead(head), h.tbody(...body), h.tfoot(...rollupRows(outcomes, eci, true), { class: 'easi-rollup-foot' }), { class: 'easi-tbl' })
  const saHead = h.thead(h.tr(h.th(''), h.th('Physical'), h.th('Chemical'), h.th('Biological')))
  const standalone = h.table(saHead, h.tbody(...rollupRows(outcomes, eci, false)), { class: 'easi-tbl easi-rollup-standalone' })
  return h.div(table, standalone, { class: 'easi-metrics-block' })
}

const TOGGLES: [string, string, boolean][] = [['show-slider', 'Show function score slider', true], ['show-adv', 'Show advanced scoring columns', false],
  ['show-map', 'Show function mappings', false], ['show-rollup', 'Show roll-up at bottom', false],
  ['show-suggested', 'Show suggested function scores', false], ['show-fnf', 'Show F/AR/NF labels', false]]

/** _metric_toolbar: the display toggles report-controls.js reads. */
function metricToolbar(): Tag {
  return h.div(...TOGGLES.map(([cls, label, on]) => h.label(h.input({ type: 'checkbox', class: 'easi-toggle', 'data-cls': cls, ...(on ? { checked: 'checked' } : {}) }),
    h.span(label), { class: 'easi-toggle-item' })), { class: 'easi-metric-toolbar' })
}

/** _summary_plots: the function scores by category and the condition indices. */
function summaryPlots(sc: Obj): Tag {
  const fs = sc.functionScores || {}
  const sub = sc.subIndices
  const eci = sc.ecosystemConditionIndex
  const groups = new Map<string, any[]>()
  for (const fn of functions()) {
    if (!groups.has(fn.category)) groups.set(fn.category, [])
    groups.get(fn.category)!.push(fn)
  }
  const legend = (items: [string, string][]) => h.div(...items.map(([c, txt]) => h.span(h.span({ class: 'easi-leg-sw', style: `background:${c};` }), txt,
    { class: 'easi-leg-item' })), { class: 'easi-plot-legend' })
  const blocks = [...groups].map(([cat, fns]) => h.div(h.div(cat, { class: 'easi-fn-group' }),
    ...fns.map((fn) => bar(fn.name, fs[fn.id] ?? null, functionScoreBandColor(fs[fn.id] ?? null), { vmax: FUNCTION_SCORE_MAX, fmt: (v) => pyFixed(v, 0) })),
    { class: 'easi-fn-block' }))
  const left = h.div(h.div('Function scores', { class: 'easi-plot-title' }),
    legend([[functionScoreBandColor(15), 'Functioning 11-15'], [functionScoreBandColor(8), 'At-Risk 6-10'], [functionScoreBandColor(0), 'Non-Functioning 0-5']]),
    ...blocks, { class: 'easi-plot-panel' })
  const right = h.div(h.div('Condition indices', { class: 'easi-plot-title' }),
    legend([[indexBandColor(1.0), 'Functioning 0.70-1.00'], [indexBandColor(0.5), 'At-Risk 0.40-0.69'], [indexBandColor(0.0), 'Non-Functioning 0.00-0.39']]),
    bar('Ecosystem Condition Index', eci, indexBandColor(eci)),
    bar('Physical', sub.physical, indexBandColor(sub.physical), { indent: true }),
    bar('Chemical', sub.chemical, indexBandColor(sub.chemical), { indent: true }),
    bar('Biological', sub.biological, indexBandColor(sub.biological), { indent: true }),
    { class: 'easi-plot-panel' })
  return h.div(left, right, { class: 'easi-summary-plots' })
}

// ---------------------------------------------------------------- the body

/** _summary_header: the stream's name, the analysis point and the reach. */
function summaryHeader(d: Obj): Tag {
  const fact = (label: string, val: string) => h.span(h.b(`${label}: `), val, { class: 'easi-fact' })
  const lat = d.snapped_lat
  const lon = d.snapped_lon
  const snapped = lat !== null && lat !== undefined && lon !== null && lon !== undefined ? `${pyFixed(lat, 4)}, ${pyFixed(lon, 4)}` : '—'
  return h.div(h.h3(d.gnis_name || '(unnamed reach)'),
    h.div(fact('Analysis Point', snapped), fact('Reach', `${typeof d.reach_length_ft === 'number' ? pyFloat(d.reach_length_ft) : pyStr(d.reach_length_ft)} ft upstream`),
      { class: 'easi-facts' }),
    { class: 'easi-summary-head' })
}

/** _basin_block: the identity rows, then the basin characteristics. */
function basinBlock(d: Obj, rep: Obj): Tag | null {
  const rows: [string, any][] = [['COMID', d.comid], ['HUC12', d.huc12 || '—'], ...((rep.basin || {}).rows || [])]
  if (!rows.length) return null
  return h.details(h.summary('Basin characteristics', { class: 'easi-section-title easi-rollup-sum' }),
    h.table(h.tbody(...rows.map(([lbl, val]) => h.tr(h.th(lbl), h.td(val === null || val === undefined ? 'None' : pyStr(val))))), { class: 'easi-tbl', style: 'max-width:560px;' }),
    { class: 'easi-rollup', open: true })
}

const NOTE_BOX = 'background:#eef3f8;border:1px solid #c6d4e3;border-radius:6px;'

/** _anchor_banner: only when the watershed could not be calculated, or under the legacy policy. */
function anchorBanner(anchor: Obj, d: Obj): Tag | null {
  if ((d || {}).watershed_source === 'site-engine') return null
  const w = routedNotice(anchor, d)
  if (!w) return null
  return h.div(h.div(h.b(w.title)), ...w.lines.map((line) => h.div(line, { style: 'margin-top:.25rem;' })),
    { style: NOTE_BOX + 'padding:.5rem .7rem;margin:0 0 .6rem;font-size:13px;' })
}

function borrowedFootnote(rows: Obj[], anchor: Obj): Tag | null {
  if (!rows.some((r) => isBorrowed(r))) return null
  const text = markerNote(anchor)
  if (!text) return null
  return h.div(`${BORROWED_MARK} ${text}`, { class: 'easi-disclaimer', style: 'margin-top:.35rem;margin-bottom:.4rem;' })
}

/** _header_with_map */
function headerWithMap(d: Obj, rep: Obj, minimap: string): Child {
  const header = new TagList(summaryHeader(d))
  const basin = basinBlock(d, rep)
  if (!minimap) return new TagList(header, basin)
  return h.div(h.div(header, basin, { style: 'flex:1 1 380px;min-width:0;' }), new HTML(minimap),
    { style: 'display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;' })
}

/** The cross-section block, filled once the static app draws cross-sections (_xs_readonly_block). */
export type XsBlock = (rep: Obj) => Child

/** _report_body */
export function reportBody(res: Obj, notes: Obj, summary: Child, xs: XsBlock = () => null, minimap = ''): Tag {
  const d = res.delineation
  const rep = res.report || {}
  return h.div(
    anchorBanner(res.siteAnchor, d),
    headerWithMap(d, rep, minimap),
    summary,
    xs(rep),
    h.div('Metrics', { class: 'easi-section-title' }),
    metricToolbar(),
    metricTable(rep.metricRows || [], notes, rep.outcomes, rep.ecosystemConditionIndex),
    borrowedFootnote(rep.metricRows || [], res.siteAnchor),
    h.div('Summary plots', { class: 'easi-section-title' }),
    summaryPlots(rep),
    { id: 'easi-report', class: 'show-slider' })
}

/** staf_web.download_button: a download link (enabled, as Shiny shows it once its server has it). */
function downloadButton(id: string, label: string, title?: string): Tag {
  return h.a(label, { class: 'btn btn-default shiny-download-link sfari-btn staf-btn', id: `easi-${id}`, href: '', target: '_blank', rel: 'noopener',
    ...(title ? { title } : {}), 'aria-live': 'polite' })
}

/** staf_web.report_footer */
export function reportFooter(): Child {
  return new TagList(downloadButton('dl_pdf', 'PDF'), downloadButton('dl_csv', 'CSV'), downloadButton('dl_geojson', 'GeoJSON'),
    downloadButton('dl_workbook', 'Completed workbook', 'The Excel calculator with this assessment entered'),
    h.button('Close', { class: 'btn btn-default sfari-btn primary staf-btn', type: 'button', 'data-dismiss': 'modal', 'data-bs-dismiss': 'modal' }))
}

export { summaryPlots, metricTable }

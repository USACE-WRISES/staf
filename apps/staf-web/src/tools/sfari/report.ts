// SFARI's report (sfari/app.py _report_modal and its helpers, sfari/report.py build_csv and
// build_geojson): the site's name, point and basin characteristics beside the basin map, the assessment
// summary, the metric evidence and Likert scores by discipline (Expand all / Collapse all), and the
// function scores beside the condition indices; its footer's downloads are the files below.
import { type Child, h, HTML, type Tag, TagList } from '../../app/htm'
import { pyFixed, pyFixedComma, pyFloat } from '../../app/pyfmt'
import { floatKeys, isMarkedFloat, pyDumps } from '../../app/pyjson'
import { csvText, type Cell, PyNum } from '../../files/csv'
import { reportFooter } from '../../shared/downloads'
import { modal } from '../../shared/shinyui'
import { reachText, synthetic } from './anchorText'
import type { Evidence } from './evidence'
import type { FunctionRecord, MetricRecord } from './markup'
import {
  CATEGORY_ORDER, FNS_BY_CAT, FUNCTION_SCORE_MAX, FUNCTIONS, functionScoreBandColor, functionScoreBandLabel, indexBandColor, METRICS_BY_FN,
  type Scored,
} from './method'

type Obj = Record<string, any>
export const BORROWED_MARK = '†'
export const BORROWED_NOTE = 'Marked desktop evidence comes from the nearest StreamCat reach downstream.'
export const LIKERT_SHORT: Record<string, string> = { 'Strongly Agree': 'SA', Agree: 'A', Neutral: 'N', Disagree: 'D', 'Strongly Disagree': 'SD' }
const FNF_SHORT: Record<string, string> = { Functioning: 'F', 'Functioning-at-Risk': 'AR', 'Non-Functioning': 'NF' }

/** report.is_borrowed: the shown evidence describes another reach. */
export function isBorrowed(e: Obj | null | undefined): boolean {
  const ev = e ?? {}
  return !!(ev.status === 'ok' && ev.origin === 'streamcat' && ev.anchor_label && ev.value_text)
}

/** report.watershed_basis_label */
export function watershedBasisLabel(delin: Obj | null): string {
  const basis = (delin ?? {}).watershedBasis || ''
  const eng = (delin ?? {}).siteEngine || {}
  if (basis === 'site-engine') {
    const ver = eng.engineVersion
    return ver ? `HR reach watershed (STAF site engine v${ver})` : 'HR reach watershed (STAF site engine)'
  }
  if (basis === 'nhdplus-v2-basin-of-surrogate') return 'NHDPlus V2 basin of the nearest StreamCat reach (StreamCat lookup engine)'
  if (eng.status === 'ok') {
    const ver = eng.engineVersion
    return `NHDPlus V2 basin drawn, watershed metrics from the HR reach watershed (${ver ? `STAF site engine v${ver}` : 'STAF site engine'})`
  }
  return 'NHDPlus V2 basin (StreamCat lookup engine)'
}

/** report.streamcat_reach_label */
export function streamcatReachLabel(delin: Obj | null): string {
  let anchor = (delin ?? {}).siteAnchor
  if (!anchor) anchor = synthetic(((delin ?? {}).delineation ?? {}).comid)
  return reachText(anchor)
}

/** str(x) of a value the report shows as is (Python's str: None, ints, floats as repr). */
function pyStrOf(container: Obj, key: string): string {
  const v = container[key]
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') return isMarkedFloat(container, key) || !Number.isInteger(v) ? pyFloat(v) : String(v)
  return String(v)
}

/** _fmt_km2 */
function fmtKm2(value: unknown): string {
  const v = typeof value === 'number' ? value : Number(value)
  return value === null || value === undefined || typeof value === 'boolean' || Number.isNaN(v) ? 'unknown' : `${pyFixedComma(v, 2)} km²`
}

/** _bar */
function bar(label: string, value: number, color: string, { vmax = 1.0, fmt = (v: number) => pyFixed(v, 2), indent = false } = {}): Tag {
  const pct = vmax ? Math.max(0.0, Math.min(100.0, (value / vmax) * 100)) : 0.0
  return h.div(h.div(label, { class: 'easi-bar-label' }),
    h.div(h.div({ class: 'easi-bar-fill', style: `width:${pyFixed(pct, 0)}%;background:${color};` }), { class: 'easi-bar-track' }),
    h.div(fmt(value), { class: 'easi-bar-val' }), { class: indent ? 'easi-bar-row indent' : 'easi-bar-row' })
}

/** _chip */
const chip = (text: string, color: string): Tag => h.span(text, { class: 'easi-chip', style: `background:${color};` })

/** _likert_color */
function likertColor(lk: string | null | undefined): string {
  if (lk === 'Strongly Agree' || lk === 'Agree') return 'var(--band-good)'
  if (lk === 'Disagree' || lk === 'Strongly Disagree') return 'var(--band-poor)'
  if (lk === 'Neutral') return '#e7ebf1'
  return '#33415c'
}

/** _ev_tip */
function evTip(e: Obj): string {
  let tip = 'Source: ' + (e.source || '—')
  if (e.confidence) tip += `  ·  data confidence ${e.confidence}`
  if (e.anchor_label) tip += '\nDescribes the ' + e.anchor_label + '.'
  if (e.fallback_reason) tip += '\nFallback: ' + e.fallback_reason
  if (e.note) tip += '\n' + e.note
  return tip
}

/** _report_evidence_value: the value, marked when it describes another reach. */
function reportEvidenceValue(e: Obj | undefined, fallback = '—'): TagList {
  const ev = e ?? {}
  return new TagList(ev.value_text || fallback, isBorrowed(ev) ? h.sup(BORROWED_MARK, { title: evTip(ev) }) : null)
}

/** _borrowed_report_footnote */
function borrowedFootnote(evidence: Obj[]): Tag | null {
  if (!evidence.some((e) => isBorrowed(e))) return null
  return h.div(`${BORROWED_MARK} ${BORROWED_NOTE}`, { class: 'easi-disclaimer', style: 'margin-top:.35rem;margin-bottom:.4rem;' })
}

export interface ReportState {
  delin: Obj
  sc: Scored
  ms: Record<string, MetricRecord>
  fs: Record<string, FunctionRecord>
  ev: Record<string, Evidence>
  /** the basin map (reportmap.svg), "" without geometry */
  minimap: string
  /** _summary_section */
  summary: Child
  title: string
}

/** _report_modal */
export function reportModal(st: ReportState): Tag {
  const d = st.delin ?? {}
  const dl = d.delineation ?? {}
  const { sc, ms, fs, ev } = st
  const slat = dl.snapped_lat
  const slon = dl.snapped_lon
  const coord = slat !== null && slat !== undefined && slon !== null && slon !== undefined ? `${pyFixed(slat, 4)}, ${pyFixed(slon, 4)}` : '—'
  const fact = (label: string, val: string) => h.span(h.b(`${label}: `), val, { class: 'easi-fact' })
  const basinRows: [string, string][] = [['NHDPlusID', pyStrOf(dl, 'nhdplus_id')], ['HUC8', pyStrOf(dl, 'huc8')],
    ['Drainage area', fmtKm2(dl.drainage_area_sqkm)], ['Stream order', pyStrOf(dl, 'stream_order')], ['Watershed basis', watershedBasisLabel(d)]]
  const basin = h.details(
    h.summary('Basin characteristics', { class: 'easi-section-title easi-rollup-sum' }),
    h.table(h.tbody(...basinRows.map(([k, v]) => h.tr(h.th(k), h.td(v)))), { class: 'easi-tbl', style: 'max-width:560px;' }),
    { class: 'easi-rollup', open: true, style: 'margin:6px 0 0;' })
  const header = h.div(
    h.div(
      h.div(h.h3(dl.gnis_name || '(unnamed reach)'),
        h.div(fact('Analysis Point', coord), fact('Reach', `${pyStrOf(dl, 'reach_length_ft')} ft upstream`), { class: 'easi-facts' }),
        { class: 'easi-summary-head' }),
      basin,
      { style: 'flex:1 1 380px;min-width:0;' }),
    st.minimap ? new HTML(st.minimap) : null,
    { style: 'display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;' })

  const discBlocks: Tag[] = []
  for (const cat of CATEGORY_ORDER) {
    const rows: Tag[] = []
    let nRated = 0
    let nTotal = 0
    const disciplineEvidence: Obj[] = []
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      rows.push(h.tr(h.td(f.name, { colspan: '3' }), { class: 'sfari-rep-fn' }))
      for (const m of METRICS_BY_FN.get(f.id) ?? []) {
        const mid = m.metricId
        const rc = ms[mid] ?? {}
        const ed = (ev[mid] ?? {}) as Obj
        disciplineEvidence.push(ed)
        const lk = rc.likert
        nTotal += 1
        if (lk) nRated += 1
        const cell = lk ? chip(LIKERT_SHORT[lk] ?? 'N/A', likertColor(lk)) : h.span('—', { style: 'color:#aab;' })
        const evtxt = reportEvidenceValue(ed, !m.desktopSource ? 'field only' : '—')
        const note = (rc.note || '').trim()
        const photos = rc.photos ?? []
        rows.push(h.tr(
          h.td(m.name, note ? h.div(note, { class: 'sfari-rep-note' }) : null,
            photos.length ? h.div(...photos.map((p) => h.img({ src: p.uri ?? '' })), { class: 'sfari-report-photos' }) : null),
          h.td(cell),
          h.td(evtxt, { class: 'sfari-rep-ev' })))
      }
    }
    discBlocks.push(h.details(
      h.summary(h.span(cat), h.span(`${nRated} of ${nTotal} rated`, { class: 'sfari-rep-cnt' }), { class: 'easi-rollup-sum sfari-rep-sum' }),
      h.table(h.thead(h.tr(h.th('Metric'), h.th('Likert'), h.th('Desktop evidence'))), h.tbody(...rows), { class: 'easi-tbl sfari-rep-tbl' }),
      borrowedFootnote(disciplineEvidence),
      { class: 'easi-rollup sfari-rep-disc' }))
  }
  const evidenceTools = h.div(
    h.button('Expand all', { 'data-rep-expand': '1', type: 'button' }, { class: 'sfari-rep-toggle' }),
    h.button('Collapse all', { 'data-rep-expand': '0', type: 'button' }, { class: 'sfari-rep-toggle' }),
    { class: 'sfari-rep-tools' })

  const leg = (items: [string, string][]) => h.div(...items.map(([c, t]) => h.span(h.span({ class: 'easi-leg-sw', style: `background:${c};` }), t,
    { class: 'easi-leg-item' })), { class: 'easi-plot-legend' })
  const fnBlocks: Tag[] = []
  for (const cat of CATEGORY_ORDER) {
    const bars: Tag[] = []
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      const rec = fs[f.id] ?? {}
      if (rec.score !== null && rec.score !== undefined) {
        bars.push(bar(f.name, rec.score, functionScoreBandColor(rec.score), { vmax: 15, fmt: (v) => pyFixed(v, 0) }))
      } else bars.push(bar(f.name + ' (unscored)', 0, '#eef1f6', { vmax: 15, fmt: (v) => pyFixed(v, 0) }))
    }
    fnBlocks.push(h.div(h.div(cat, { class: 'easi-fn-group' }), ...bars, { class: 'easi-fn-block' }))
  }
  const left = h.div(h.div('Function scores (0–15)', { class: 'easi-plot-title' }),
    leg([[functionScoreBandColor(15), 'Functioning 11–15'], [functionScoreBandColor(8), 'At-Risk 6–10'], [functionScoreBandColor(0), 'Non-Functioning 0–5']]),
    ...fnBlocks, { class: 'easi-plot-panel' })
  const subs = sc.subIndices
  const eci = sc.ecosystemConditionIndex
  const catChips = CATEGORY_ORDER.map((cat) => {
    const lbl = sc.categoryLabels[cat]
    const col = lbl ? indexBandColor(sc.categorySubIndices[cat] ?? 0.0) : '#eef1f6'
    return chip(`${cat}: ${lbl ? FNF_SHORT[lbl] ?? '—' : '—'}`, col)
  })
  const right = h.div(h.div('Condition indices', { class: 'easi-plot-title' }),
    leg([[indexBandColor(1.0), 'Functioning 0.70–1.00'], [indexBandColor(0.5), 'At-Risk 0.40–0.69'], [indexBandColor(0.0), 'Non-Functioning 0.00–0.39']]),
    bar('Ecosystem Condition Index', eci, indexBandColor(eci)),
    bar('Physical', subs.physical, indexBandColor(subs.physical), { indent: true }),
    bar('Chemical', subs.chemical, indexBandColor(subs.chemical), { indent: true }),
    bar('Biological', subs.biological, indexBandColor(subs.biological), { indent: true }),
    h.div('Functional categories', { class: 'easi-plot-title', style: 'margin-top:14px;' }),
    h.div(...catChips.map((c) => h.span(c, { style: 'margin-right:6px;' })), { style: 'margin:4px 0 2px;' }),
    { class: 'easi-plot-panel' })

  const body = h.div(header, st.summary, h.div('Metric evidence & Likert scores', { class: 'easi-section-title' }), evidenceTools, ...discBlocks,
    h.div('Summary', { class: 'easi-section-title' }), h.div(left, right, { class: 'easi-summary-plots' }), { id: 'sfari-report' })
  return modal(body, { title: st.title, easyClose: true, size: 'xl',
    footer: reportFooter('sfari-dl_pdf', 'sfari-dl_csv', 'sfari-dl_geojson', 'sfari-dl_calc_filled') })
}

// ---------------------------------------------------------------- the exports (report.py)

/** A value the CSV writes as Python's str() of what the page holds (a float kept a float). */
function cellOf(container: Obj, key: string): Cell {
  const v = container?.[key]
  if (typeof v === 'number' && isMarkedFloat(container, key)) return new PyNum(v)
  return v === undefined ? null : v
}

/** A score from the rollup: always a Python float. */
const flt = (v: number | null | undefined): Cell => (v === null || v === undefined ? null : new PyNum(v))

export function orderedMetrics(): [string, { name: string, id: string }, Obj][] {
  const out: [string, { name: string, id: string }, Obj][] = []
  for (const cat of CATEGORY_ORDER) for (const f of FNS_BY_CAT.get(cat) ?? []) for (const m of METRICS_BY_FN.get(f.id) ?? []) out.push([cat, f, m])
  return out
}

/** _header_pairs */
function headerPairs(delin: Obj, sc: Scored): [string, Cell][] {
  const dl = (delin ?? {}).delineation ?? {}
  const si = sc.subIndices ?? {}
  const area: [string, Cell][] = dl.watershed_area_sqkm !== null && dl.watershed_area_sqkm !== undefined
    ? [['HR reach watershed area (km2)', cellOf(dl, 'watershed_area_sqkm')]] : []
  return [
    ['Stream', dl.gnis_name || '(unnamed reach)'],
    ['Latitude', cellOf(dl, 'snapped_lat')], ['Longitude', cellOf(dl, 'snapped_lon')],
    ['COMID', cellOf(dl, 'comid')], ['HUC8', cellOf(dl, 'huc8')],
    ['Drainage area (km2)', cellOf(dl, 'drainage_area_sqkm')],
    ...area,
    ['Reach length (ft)', cellOf(dl, 'reach_length_ft')],
    ['Watershed basis', watershedBasisLabel(delin)],
    ['StreamCat reach', streamcatReachLabel(delin)],
    ['Ecosystem Condition Index', flt(sc.ecosystemConditionIndex)],
    ['Physical sub-index', flt(si.physical)],
    ['Chemical sub-index', flt(si.chemical)],
    ['Biological sub-index', flt(si.biological)],
  ]
}

/** build_csv */
export function buildCsv(delin: Obj, ms: Record<string, MetricRecord>, fs: Record<string, FunctionRecord>, ev: Record<string, Evidence>, sc: Scored): string {
  const rows: Cell[][] = [['SFARI Screening Assessment']]
  for (const [k, v] of headerPairs(delin, sc)) rows.push([k, v])
  rows.push([])
  rows.push(['Category', 'Function', 'Metric', 'Scale', 'Likert', 'Pulled evidence', 'Source', 'Origin', 'Describes', 'Note'])
  for (const [cat, f, m] of orderedMetrics()) {
    const rc = ms[m.metricId] ?? {}
    const e = (ev[m.metricId] ?? {}) as Obj
    rows.push([cat, f.name, m.name, m.scale ?? '', rc.likert || '', e.value_text || '', e.source || '', e.origin || '', e.anchor_label || '', rc.note || ''])
  }
  rows.push([])
  rows.push(['Category', 'Function', 'Function score (0-15)', 'Condition', 'Justification'])
  for (const cat of CATEGORY_ORDER) {
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      const rec = fs[f.id] ?? {}
      const score = rec.score
      const band = score !== null && score !== undefined ? functionScoreBandLabel(score) : ''
      rows.push([cat, f.name, score === null || score === undefined ? '' : score, band, rec.note || ''])
    }
  }
  rows.push([])
  rows.push(['Outcome', 'Sub-index'])
  for (const k of ['physical', 'chemical', 'biological']) rows.push([k[0].toUpperCase() + k.slice(1), flt(sc.subIndices?.[k])])
  rows.push(['Ecosystem Condition Index', flt(sc.ecosystemConditionIndex)])
  return csvText(rows)
}

/** build_geojson */
export function buildGeojson(delin: Obj, fs: Record<string, FunctionRecord>, sc: Scored): string {
  const dl = (delin ?? {}).delineation ?? {}
  const props: Obj = { stream: dl.gnis_name ?? null, comid: dl.comid ?? null, huc8: dl.huc8 ?? null, drainage_area_sqkm: dl.drainage_area_sqkm ?? null,
    ecosystem_condition_index: sc.ecosystemConditionIndex ?? null }
  if (isMarkedFloat(dl, 'drainage_area_sqkm')) floatKeys(props, 'drainage_area_sqkm')
  floatKeys(props, 'ecosystem_condition_index')
  for (const [k, v] of Object.entries(sc.subIndices ?? {})) {
    props[`subindex_${k}`] = v
    floatKeys(props, `subindex_${k}`)
  }
  const feats: Obj[] = []
  const add = (fc: Obj | null | undefined, kind: string) => {
    if (fc && fc.features?.length) {
      for (const ft of fc.features) feats.push({ type: 'Feature', geometry: ft.geometry ?? null, properties: withFloats({ type: kind, ...props }, props) })
    }
  }
  add((delin ?? {}).watershed_geojson, 'watershed')
  add((delin ?? {}).reach_geojson, 'reach')
  if (dl.snapped_lat !== null && dl.snapped_lat !== undefined && dl.snapped_lon !== null && dl.snapped_lon !== undefined) {
    const pt = withFloats({ type: 'analysis_point', ...props }, props)
    for (const f of FUNCTIONS) pt[f.id] = (fs[f.id] ?? {}).score ?? null
    const coords = [dl.snapped_lon, dl.snapped_lat]
    floatKeys(coords as any, '0', '1')
    feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: coords }, properties: pt })
  }
  return pyDumps({ type: 'FeatureCollection', features: feats }, { indent: 2 })
}

/** A copy of ``obj`` whose keys hold floats where ``from``'s do. */
function withFloats(obj: Obj, from: Obj): Obj {
  for (const k of Object.keys(from)) if (isMarkedFloat(from, k)) floatKeys(obj, k)
  return obj
}

export const FUNCTION_MAX = FUNCTION_SCORE_MAX

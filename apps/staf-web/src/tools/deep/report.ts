// DEEP's report (deep/app.py _report_modal and helpers, deep/report.py _rows, build_csv, build_geojson):
// the assessment and the site beside the basin map, the assessment summary, the outcome sub-indices and
// the index claim, the function scores, every metric's value, index, source and curve with what it is
// scored against, the metrics not scored, and the footer's downloads.
import { type Child, h, HTML, type Tag, TagList } from '../../app/htm'
import { pyFixed, pyFixedComma, pyFloat } from '../../app/pyfmt'
import { floatKeys, isMarkedFloat, markFloats, pyDumps } from '../../app/pyjson'
import { csvText, type Cell, PyNum } from '../../files/csv'
import { lifecycleStatus, bundleDigest } from '../../session/deep'
import { reportFooter } from '../../shared/downloads'
import { modal } from '../../shared/shinyui'
import { reportHeading, statusLabel } from '../../app/deepStatus'
import { reachText, synthetic } from '../sfari/anchorText'
import {
  type Bundle, coverageOf, curveStrata, enginePairingAdvisory, FUNCTION_SCORE_BAND_SHORT, FUNCTION_SCORE_BANDS, functionScoreBandColor,
  indexBandColor, indexBandForBounds, indexClaim, measuredFromState, type Metric, metricIndex, metricWarning, predictorSourceOf, type Rec,
  type Scored,
} from './method'
import {
  basisLabel, flaggedSummary, functionFlagLine, limitationsLine, referenceMethodStatement, stratumLabel, supportLine, uncertaintyLine,
  withheld, withheldNote, withheldReason, withheldTitle,
} from './support'

/** scoring.function_score_band_label: NF, AR or F. */
export function functionScoreBandLabel(value: number): string {
  for (let i = 0; i < FUNCTION_SCORE_BANDS.length; i++) if (value <= FUNCTION_SCORE_BANDS[i][0]) return FUNCTION_SCORE_BAND_SHORT[i]
  return FUNCTION_SCORE_BAND_SHORT[FUNCTION_SCORE_BAND_SHORT.length - 1]
}

/** str(x) of a value as Python shows it (an int, a float's repr, None). */
export function pyStrOf(container: Rec | null | undefined, key: string): string {
  const v = (container ?? {})[key]
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') return isMarkedFloat(container!, key) || !Number.isInteger(v) ? pyFloat(v) : String(v)
  return String(v)
}

function fmtNum(value: unknown, digits: number, unit: string): string {
  const v = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN
  if (value === null || value === undefined || typeof value === 'boolean' || (Number.isNaN(v) && !/nan/i.test(String(value)))) return 'unknown'
  return `${pyFixedComma(v, digits)} ${unit}`
}
const fmtKm2 = (v: unknown) => fmtNum(v, 2, 'km²')
const fmtFt = (v: unknown) => fmtNum(v, 0, 'ft')

// ---------------------------------------------------------------- report.py

const BASIS_WORDS: [string, string][] = [['site-engine', 'STAF site engine (HR reach watershed)'], ['streamcat', 'StreamCat lookup engine (NHDPlus V2 basin)'],
  ['nlcd', 'NLCD (HR reach watershed polygon)'], ['3dep', '3DEP'], ['nid', 'USACE National Inventory of Dams']]
const SOURCE_DATA: Record<string, string> = { streamcat: 'EPA StreamCat', nlcd: 'NLCD 2021', '3dep': '3DEP elevation (modeled)', nid: 'National Inventory of Dams' }

/** source_label: the short Source of a desktop value, "" for one the assessor entered. */
export function sourceLabel(meta: Rec | null | undefined): string {
  const m = meta ?? {}
  if (m.origin !== 'desktop') return ''
  const src = String(m.source || '')
  const basis = String(m.basis || (m.engine ? 'site-engine' : ''))
  if (basis === 'site-engine') {
    const at = src.lastIndexOf('(HR reach watershed, ')
    return at >= 0 ? src.slice(at + '(HR reach watershed, '.length).replace(/\)+$/, '').trim() : 'Computed by DEEP'
  }
  let label = SOURCE_DATA[basis]
  if (label === undefined) return src
  if (basis === 'streamcat' && src.includes('nearest StreamCat reach')) label += ', nearest reach'
  return label
}

/** desktop_basis_label */
export function desktopBasisLabel(measured: Record<string, Rec> | null | undefined): string {
  const counts = new Map<string, number>()
  for (const rc of Object.values(measured ?? {})) {
    if (!rc || typeof rc !== 'object' || rc.origin !== 'desktop') continue
    const basis = String(rc.basis || (rc.engine ? 'site-engine' : '')) || 'other'
    counts.set(basis, (counts.get(basis) ?? 0) + 1)
  }
  const parts: string[] = []
  for (const [key, words] of BASIS_WORDS) {
    if (counts.has(key)) {
      parts.push(`${words} ${counts.get(key)}`)
      counts.delete(key)
    }
  }
  for (const [key, n] of counts) parts.push(`${key} ${n}`)
  return parts.length ? parts.join(', ') : 'none'
}

/** watershed_basis_label */
export function watershedBasisLabel(delin: Rec | null | undefined): string {
  const basis = (delin ?? {}).watershedBasis || ''
  const eng = (delin ?? {}).siteEngine || {}
  const ver = eng.engineVersion
  if (basis === 'site-engine') return ver ? `HR reach watershed (STAF site engine v${ver})` : 'HR reach watershed (STAF site engine)'
  if (basis === 'nhdplus-v2-basin-of-surrogate') return 'NHDPlus V2 basin of the nearest StreamCat reach (StreamCat lookup engine)'
  if (eng.status === 'ok') return `NHDPlus V2 basin drawn, HR reach watershed computed (${ver ? `STAF site engine v${ver}` : 'STAF site engine'})`
  return 'NHDPlus V2 basin (StreamCat lookup engine)'
}

/** streamcat_reach_label */
export function streamcatReachLabel(delin: Rec | null | undefined): string {
  let anchor = (delin ?? {}).siteAnchor
  if (!anchor) anchor = synthetic(((delin ?? {}).delineation ?? {}).comid)
  return reachText(anchor)
}

/** curve_set_label: the curve layer a stratified metric was scored on, and how it was chosen. */
export function curveSetLabel(m: Metric, raw: Rec | null | undefined): string {
  const strata = curveStrata(m)
  if (strata.length < 2) return ''
  let chosen = (raw ?? {}).stratum
  if (chosen === null || chosen === undefined) chosen = m.activeStratum || strata[0]
  const how = (raw ?? {}).stratumAuto ? 'auto' : (raw ?? {}).stratum !== null && (raw ?? {}).stratum !== undefined ? 'chosen' : 'default'
  return `${stratumLabel(chosen, m)} (${how})`
}

export interface RowMeta {
  origin: string, basis: string, source: string, engine: boolean, predictor_source: string, advisory: string | null, reference_only: boolean,
  scored_against: string, curve_basis: string, curve_set: string
}

/** _rows: every metric with its value, index and provenance (the index as the app scores it). */
export function rows(la: Bundle | null, measured: Record<string, Rec>): [Rec, Metric, unknown, number | null, RowMeta, Rec][] {
  const out: [Rec, Metric, unknown, number | null, RowMeta, Rec][] = []
  const mv = measuredFromState(measured ?? {})
  for (const fn of (la ?? {}).metricsByFunction ?? []) {
    for (const m of fn.metrics ?? []) {
      const mid = m.metricId
      const raw = (measured ?? {})[mid] ?? {}
      const v = mv[mid]
      const val = raw.na ? null : raw.value ?? null
      let idx: number | null = null
      let advisory: string | null = null
      let referenceOnly = false
      if (val !== null && val !== '' && v !== undefined) {
        idx = metricIndex(v, m)
        advisory = metricWarning(v, m)
        referenceOnly = idx === null && enginePairingAdvisory(v, m) !== null
      }
      const meta: RowMeta = {
        origin: Object.keys(raw).length ? raw.origin ?? 'field' : '',
        basis: raw.basis || '', source: raw.source || '', engine: v !== undefined ? !!v.engine : false,
        predictor_source: m.predictorSource ? String(m.predictorSource) : predictorSourceOf(la),
        advisory, reference_only: referenceOnly, scored_against: supportLine(m), curve_basis: basisLabel(m), curve_set: curveSetLabel(m, raw),
      }
      out.push([fn, m, val, idx, meta, raw])
    }
  }
  return out
}

/** withheld_rows: [metric, functions, reason] per metric the assessment does not score. */
export function withheldRows(la: Bundle | null, short = false): string[][] {
  return withheld(la).map((w) => {
    const fns = (w.functions || []).map((f: Rec) => String(f.functionName || f.functionId)).join(', ')
    const reason = short ? withheldReason(w) : String(w.statement || withheldTitle(w) + '.')
    return [String(w.metricName || w.metricId || ''), fns, reason]
  })
}

/** _source_cell_text */
export function sourceCellText(meta: RowMeta): string {
  const origin = meta.origin || ''
  const src = meta.source || ''
  let txt = origin && src ? `${origin}: ${src}` : src || origin
  if (meta.reference_only) txt += ' (reference only)'
  return txt
}

function stateLabel(st: Rec): string {
  const { name, code } = st
  if (name && code) return `${name} (${code})`
  return name || code || ''
}

function l3Label(l3: Rec): string {
  const { code, name } = l3
  if (code && name) return `${code} ${name}`
  return name || code || ''
}

/** _coverage_label */
export function coverageLabel(la: Bundle | null): string {
  const cov = coverageOf(la)
  const base = `${cov.covered} of ${cov.total}`
  if (cov.covered >= cov.total) return base
  if (cov.declared) return `${base} (${cov.excluded} documented exclusion${cov.excluded === 1 ? '' : 's'})`
  return `${base} (not declared)`
}

const assessmentName = (la: Bundle | null) => (la ? la.assessmentName ?? la.assessmentId ?? '' : '')
const sourceCitation = (la: Bundle | null) => (la ? la.sourceCitation ?? '' : '')

/** A value the CSV writes as Python's str() (a float kept a float). */
function cellOf(container: Rec | null | undefined, key: string): Cell {
  const v = (container ?? {})[key]
  if (typeof v === 'number' && isMarkedFloat(container!, key)) return new PyNum(v)
  return v === undefined ? null : v
}
const flt = (v: number | null | undefined): Cell => (v === null || v === undefined ? null : new PyNum(v))
const roundTo = (v: number, n: number) => Number(pyFixed(v, n))

/** _header_pairs */
function headerPairs(delin: Rec, la: Bundle | null, sc: Scored, region: Rec | null, measured: Record<string, Rec>): [string, Cell][] {
  const dl = (delin ?? {}).delineation ?? {}
  const si = sc.subIndices ?? {}
  const raw = la ?? {}
  const version = (raw.library ?? {}).version
  const status = lifecycleStatus(raw)
  const reg = region ?? {}
  return [
    ['Assessment', assessmentName(la)],
    ['Assessment version', version === null || version === undefined ? '' : version],
    ['Lifecycle status', statusLabel(status)],
    ['Scoring method version', (raw.scoringContract ?? {}).methodVersion || ''],
    ['Source', sourceCitation(la)],
    ['Predictor source', predictorSourceOf(la)],
    ['State (region match)', stateLabel(reg.state ?? {})],
    ['Level III ecoregion', l3Label(reg.level3 ?? {})],
    ['Content digest', bundleDigest(raw)],
    ['Stream', dl.gnis_name || '(unnamed reach)'],
    ['Latitude', cellOf(dl, 'snapped_lat')], ['Longitude', cellOf(dl, 'snapped_lon')],
    ['COMID', cellOf(dl, 'comid')], ['HUC8', cellOf(dl, 'huc8')],
    ['Drainage area (km2)', cellOf(dl, 'drainage_area_sqkm')],
    ['HR reach watershed area (km2)', cellOf(dl, 'watershed_area_sqkm')],
    ['Reach length (ft)', cellOf(dl, 'reach_length_ft')],
    ['Watershed basis', watershedBasisLabel(delin)],
    ['Desktop values', desktopBasisLabel(measured)],
    ['StreamCat reach', streamcatReachLabel(delin)],
    ['Ecosystem Condition Index', flt(sc.ecosystemConditionIndex)],
    ['Condition claim', indexClaim(sc)],
    ['STAF function coverage', coverageLabel(la)],
    ['Flagged transfers', flaggedSummary(la) || 'none'],
    ['Physical sub-index', flt(si.physical)],
    ['Chemical sub-index', flt(si.chemical)],
    ['Biological sub-index', flt(si.biological)],
  ]
}

/** _function_rows_with_limits: [name, score, condition, limitation] once per assessment function. */
export function functionRows(la: Bundle | null, sc: Scored): [string, number | null, string, string][] {
  const fscores = sc.functionScores ?? {}
  const seen = new Set<string>()
  const out: [string, number | null, string, string][] = []
  for (const fn of (la ?? {}).metricsByFunction ?? []) {
    const fid = fn.functionId
    if (seen.has(fid)) continue
    seen.add(fid)
    const s = fscores[fid] ?? null
    out.push([fn.functionName ?? fid, s, s !== null && s !== undefined ? functionScoreBandLabel(s) : 'Not scored', functionFlagLine(la, fid)])
  }
  return out
}

/** build_csv */
export function buildCsv(delin: Rec, la: Bundle | null, measured: Record<string, Rec>, sc: Scored, region: Rec | null): string {
  const out: Cell[][] = [['DEEP Detailed Assessment']]
  for (const [k, v] of headerPairs(delin, la, sc, region, measured)) out.push([k, v])
  out.push([])
  out.push(['Function', 'Discipline', 'Metric', 'Measured value', 'Curve (source)', 'Metric index (0-1)', 'Note', 'Origin', 'Basis', 'Source',
    'Engine value', 'Predictor source', 'Scoring advisory', 'Curve basis', 'Scored against', 'Curve set', 'Uncertainty', 'Read with care'])
  for (const [fn, m, val, idx, meta, raw] of rows(la, measured)) {
    const note = ((measured ?? {})[m.metricId] ?? {}).note ?? ''
    out.push([fn.functionName ?? '', fn.discipline ?? '', m.metricName ?? m.metricId,
      val === null || val === undefined ? '' : (typeof val === 'number' && isMarkedFloat(raw, 'value') ? new PyNum(val) : val as Cell),
      (m.curve ?? {}).layerName ?? '', idx === null ? '' : new PyNum(roundTo(idx, 3)), note,
      meta.origin, meta.basis, meta.source, meta.engine ? 'yes' : '', meta.predictor_source, meta.advisory || '', meta.curve_basis,
      meta.scored_against, meta.curve_set, uncertaintyLine(m), limitationsLine(m)])
  }
  const wh = withheldRows(la)
  if (wh.length) {
    out.push([])
    out.push(['Metrics not scored'])
    out.push(['Metric', 'Functions', 'Reason'])
    for (const r of wh) out.push(r)
  }
  out.push([])
  out.push(['Function', 'Function score (0-15)', 'Condition', 'Limitation'])
  for (const [name, s, cond, limit] of functionRows(la, sc)) out.push([name, s === null ? '' : new PyNum(roundTo(s, 1)), cond, limit])
  out.push([])
  out.push(['Outcome', 'Sub-index'])
  for (const k of ['physical', 'chemical', 'biological']) out.push([k[0].toUpperCase() + k.slice(1), flt((sc.subIndices ?? {})[k])])
  out.push(['Ecosystem Condition Index', flt(sc.ecosystemConditionIndex)])
  out.push(['Condition claim', indexClaim(sc)])
  out.push(['STAF function coverage', coverageLabel(la)])
  return csvText(out)
}

/** build_geojson */
export function buildGeojson(delin: Rec, la: Bundle | null, sc: Scored, region: Rec | null, measured: Record<string, Rec>): string {
  const dl = (delin ?? {}).delineation ?? {}
  const raw = la ?? {}
  const reg = region ?? {}
  const withheldN = rows(la, measured ?? {}).filter(([, , , , meta]) => meta.reference_only).length
  const bounds = sc.ecosystemConditionIndexBounds ?? null
  if (Array.isArray(bounds)) markFloats(bounds)
  const props: Rec = {
    assessment: assessmentName(la), assessment_version: (raw.library ?? {}).version ?? null, lifecycle_status: lifecycleStatus(raw),
    source: sourceCitation(la), predictor_source: predictorSourceOf(la), region_state: stateLabel(reg.state ?? {}), region_level3: l3Label(reg.level3 ?? {}),
    content_digest: bundleDigest(raw), stream: dl.gnis_name ?? null, comid: dl.comid ?? null, huc8: dl.huc8 ?? null,
    drainage_area_sqkm: dl.drainage_area_sqkm ?? null, watershed_basis: (delin ?? {}).watershedBasis || 'nhdplus-v2-basin',
    engine_values_withheld: withheldN, streamcat_reach: streamcatReachLabel(delin), ecosystem_condition_index: sc.ecosystemConditionIndex ?? null,
    ecosystem_condition_index_bounds: bounds, condition_claim: indexClaim(sc), staf_function_coverage: coverageLabel(la),
  }
  if (isMarkedFloat(dl, 'drainage_area_sqkm')) floatKeys(props, 'drainage_area_sqkm')
  floatKeys(props, 'ecosystem_condition_index')
  for (const [k, v] of Object.entries(sc.subIndices ?? {})) {
    props[`subindex_${k}`] = v
    floatKeys(props, `subindex_${k}`)
  }
  const copyProps = (kind: string) => {
    const p: Rec = { type: kind, ...props }
    for (const k of Object.keys(props)) if (isMarkedFloat(props, k)) floatKeys(p, k)
    return p
  }
  const feats: Rec[] = []
  const add = (fc: Rec | null | undefined, kind: string) => {
    if (fc && fc.features?.length) for (const ft of fc.features) feats.push({ type: 'Feature', geometry: ft.geometry ?? null, properties: copyProps(kind) })
  }
  add((delin ?? {}).watershed_geojson, 'watershed')
  add((delin ?? {}).reach_geojson, 'reach')
  if (dl.snapped_lat !== null && dl.snapped_lat !== undefined && dl.snapped_lon !== null && dl.snapped_lon !== undefined) {
    const pt = copyProps('analysis_point')
    for (const [fid, s] of Object.entries(sc.functionScores ?? {})) {
      pt[fid] = s
      floatKeys(pt, fid)
    }
    const coords = [dl.snapped_lon, dl.snapped_lat]
    floatKeys(coords as any, '0', '1')
    feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: coords }, properties: pt })
  }
  return pyDumps({ type: 'FeatureCollection', features: feats }, { indent: 2 })
}

// ---------------------------------------------------------------- the dialog (app.py)

/** _bar: a null value is an absence (an empty track, no number). */
function bar(label: string, value: number | null, color: string, { vmax = 1.0, fmt = (v: number) => pyFixed(v, 2), indent = false } = {}): Tag {
  const pct = value === null ? 0.0 : vmax ? Math.max(0.0, Math.min(100.0, (value / vmax) * 100)) : 0.0
  return h.div(h.div(label, { class: 'easi-bar-label' }),
    h.div(h.div({ class: 'easi-bar-fill', style: `width:${pyFixed(pct, 0)}%;background:${color};` }), { class: 'easi-bar-track' }),
    h.div(value === null ? '' : fmt(value), { class: 'easi-bar-val' }), { class: indent ? 'easi-bar-row indent' : 'easi-bar-row' })
}

/** _outcome_bar */
function outcomeBar(label: string, value: number | null | undefined, indent = false): Tag {
  if (value === null || value === undefined) return bar(`${label} (not assessed)`, null, '#eef1f6', { indent })
  return bar(label, value, indexBandColor(value), { indent })
}

/** _index_claim_block */
function indexClaimBlock(sc: Scored): Child {
  const eci = sc.ecosystemConditionIndex
  if (eci !== null && eci !== undefined) return bar('Ecosystem Condition Index', eci, indexBandColor(eci))
  const span = sc.ecosystemConditionIndexBounds || [0.0, 1.0]
  const [low, high] = [Number(span[0]), Number(span[1])]
  const band = indexBandForBounds(low, high)
  const fill = band ? indexBandColor(low) : '#cfd6e2'
  return new TagList(
    h.div(h.div('Ecosystem Condition Index', { class: 'easi-bar-label' }),
      h.div(h.div({ class: 'easi-bar-fill', style: `margin-left:${pyFixed(low * 100, 0)}%;width:${pyFixed(Math.max(1.0, (high - low) * 100), 0)}%;background:${fill};` }),
        { class: 'easi-bar-track' }),
      h.div(`${pyFixed(low, 2)} to ${pyFixed(high, 2)}`, { class: 'easi-bar-val' }), { class: 'easi-bar-row' }),
    h.div(indexClaim(sc), { style: 'font-size:11px;color:#8a93a3;font-style:italic;margin-top:2px;' }))
}

export interface DeepReportState {
  delin: Rec
  la: Bundle | null
  sc: Scored
  measured: Record<string, Rec>
  minimap: string
  summary: Child
  title: string
  /** the loaded version has a calculator (the footer's completed workbook) */
  hasCalculator: boolean
}

/** _report_modal */
export function reportModal(st: DeepReportState): Tag {
  const d = st.delin ?? {}
  const dl = d.delineation ?? {}
  const { la, sc } = st
  const slat = dl.snapped_lat
  const slon = dl.snapped_lon
  const coord = slat !== null && slat !== undefined && slon !== null && slon !== undefined ? `${pyFixed(slat, 5)}, ${pyFixed(slon, 5)}` : '—'
  const small = (text: string, top: string) => h.div(text, { style: `font-size:12px;color:#667;margin-top:${top};` })
  const header = h.div(
    h.div(
      reportHeading(la),
      small(`${la ? sourceCitation(la) : ''} · ${dl.gnis_name || '(unnamed reach)'}`, '3px'),
      small(`Lat/Lon ${coord}  ·  COMID ${pyStrOf(dl, 'comid')}  ·  HUC8 ${pyStrOf(dl, 'huc8')}`, '1px'),
      small(`Drainage ${fmtKm2(dl.drainage_area_sqkm)}  ·  Reach ${fmtFt(dl.reach_length_ft)}`, '1px'),
      small(`Watershed basis: ${watershedBasisLabel(d)}  ·  Predictor source: ${la ? predictorSourceOf(la) : 'streamcat'}`, '1px'),
      { style: 'flex:1 1 380px;min-width:0;' }),
    st.minimap ? new HTML(st.minimap) : null,
    { style: 'display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;margin-bottom:12px;' })
  const subs = sc.subIndices ?? {}
  const rcov = la ? coverageOf(la) : null
  let caveat: Tag | null = null
  if (rcov && rcov.covered < rcov.total) {
    const excl = rcov.declared ? `${rcov.excluded} documented as out of scope` : 'coverage not declared'
    caveat = h.div(`This assessment covers ${rcov.covered} of ${rcov.total} STAF functions (${excl}).`,
      { style: 'font-size:11px;color:#8a93a3;font-style:italic;margin-top:4px;' })
  }
  const summary = h.div(indexClaimBlock(sc), caveat, outcomeBar('Physical outcome', subs.physical, true),
    outcomeBar('Chemical outcome', subs.chemical, true), outcomeBar('Biological outcome', subs.biological, true))
  const fscores = sc.functionScores ?? {}
  const fbars: Tag[] = []
  let prevDisc: string | null = null
  for (const fn of (la ?? {}).metricsByFunction ?? []) {
    const disc = fn.discipline ?? ''
    if (disc !== prevDisc) {
      fbars.push(h.div(disc, { style: 'font-family:var(--font-head);font-weight:700;color:var(--easi-accent);font-size:12px;margin:8px 0 2px;' }))
      prevDisc = disc
    }
    const s = fscores[fn.functionId]
    if (s === null || s === undefined) bars0(fbars, fn)
    else fbars.push(bar(fn.functionName ?? '', s, functionScoreBandColor(s), { vmax: 15, fmt: (v) => pyFixed(v, 1), indent: true }))
  }
  const tr: Tag[] = []
  for (const [fn, m, val, idx, meta, raw] of rows(la, st.measured)) {
    const ph = ((st.measured ?? {})[m.metricId] ?? {}).photos ?? []
    const idxTxt = meta.reference_only ? 'ref. only' : idx === null ? '—' : pyFixed(idx, 2)
    tr.push(h.tr(
      h.td(fn.functionName ?? '', { style: 'color:#8a93a3;font-size:11px;' }),
      h.td(m.metricName ?? m.metricId,
        ph.length ? h.div(...ph.map((p: Rec) => h.img({ src: p.uri ?? '' })), { class: 'sfari-report-photos' }) : null,
        meta.scored_against ? h.div(meta.scored_against + (meta.curve_set ? ` · Curve set: ${meta.curve_set}` : ''), { class: 'deep-report-support' }) : null,
        meta.advisory ? h.div(meta.advisory, { class: 'deep-report-advisory' }) : null),
      h.td(val === null || val === undefined || val === '' ? '—' : pyStrOf(raw, 'value')),
      h.td(idxTxt, { style: `background:${idx !== null ? indexBandColor(idx) : '#fff'};` }),
      h.td(sourceCellText(meta), { style: 'font-size:11px;color:#45506a;' }),
      h.td((m.curve ?? {}).layerName ?? '', { style: 'font-size:11px;color:#45506a;' })))
  }
  const table = h.table(h.thead(h.tr(h.th('Function'), h.th('Metric'), h.th('Value'), h.th('Index'), h.th('Source'), h.th('Curve source'))),
    h.tbody(...tr), { class: 'easi-tbl' })
  const wh = withheldRows(la, true)
  const withheldBlock = wh.length ? new TagList(
    h.h4('Metrics not scored', { style: 'margin-top:14px;' }),
    h.div(withheldNote(withheld(la)), { style: 'font-size:11px;color:#8a93a3;margin-bottom:4px;' }),
    h.table(h.thead(h.tr(h.th('Metric'), h.th('Functions'), h.th('Reason'))), h.tbody(...wh.map((r) => h.tr(h.td(r[0]), h.td(r[1]), h.td(r[2])))),
      { class: 'easi-tbl' })) : null
  const refStatement = referenceMethodStatement(la)
  const body = h.div(header, st.summary,
    h.h4('Outcome sub-indices & Ecosystem Condition Index', { style: 'margin-top:14px;' }), summary,
    h.h4('Function scores (0–15)', { style: 'margin-top:14px;' }), h.div(...fbars),
    h.h4('Metric values & curve indices', { style: 'margin-top:14px;' }),
    refStatement ? h.div(refStatement, { style: 'font-size:11px;color:#45506a;margin-bottom:6px;' }) : null,
    table, withheldBlock,
    h.div("Scores are computed automatically from the assessment's reference curves. Confirm the curve source applies to your region/stream type.",
      { style: 'font-size:11px;color:#8a93a3;margin-top:10px;' }),
    { id: 'deep-report' })
  return modal(body, { title: st.title, easyClose: true, size: 'xl',
    footer: reportFooter('deep-dl_pdf', 'deep-dl_csv', 'deep-dl_geojson', st.hasCalculator ? 'deep-dl_calc_filled' : null) })
}

function bars0(fbars: Tag[], fn: Rec): void {
  fbars.push(bar((fn.functionName ?? '') + ' (not scored)', 0, '#eef1f6', { vmax: 15, fmt: (v) => pyFixed(v, 0), indent: true }))
}

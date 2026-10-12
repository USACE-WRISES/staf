// EASI's 20 metric adapters (easi/metrics/: base, hydrology, hydraulics, geomorphology,
// physicochemistry, biology; the frozen method's code) under the regional criteria, the only set the
// adopted method scores with. Each reads its evidence from the run's context and asks the evaluator
// for the rating and trace; the texts are the Python's own. The point lookups the Python adapters make
// (ATTAINS, WQP, NAS, NID) come prefetched on ``extras`` from the STAF data bundle, the same tables the
// Shiny EASI reads them from.
import { fsum, pyFixed, pyFixedComma } from '../../app/pyfmt'
import { bankfullWidthCv, crossSectionQuality, describeReach, fmtBhr } from '../../engine/geomorph'
import { evaluate, pyFloatOf, pyRound, pyStr, type Evaluation } from './evaluator'
import * as ws from './watershed'

export const IMPERVIOUS_ID = 'catchment-hydrology-impervious-surface-cover'
export const WETLANDS_ID = 'surface-water-storage-percent-wetlands-in-watershed'
export const FLOW_ALTERATION_ID = 'streamflow-regime-flow-alteration-regulation-water-use'
export const REACH_INFLOW_ID = 'reach-inflow-concentrated-runoff-stormwater-inputs'
export const LOW_FLOW_ID = 'low-flow-and-baseflow-dynamics-low-flow-wetted-connectivity'
export const HYPORHEIC_ID = 'hyporheic-connectivity-hyporheic-exchange-indicators'
export const ENTRENCHMENT_ID = 'floodplain-connectivity-floodplain-access-entrenchment'
export const FLOODPLAIN_ENGAGEMENT_ID = 'high-flow-dynamics-floodplain-engagement-frequency-bankfull-recurrence'
export const SEDIMENT_ID = 'sediment-continuity-sediment-supply-potential-watershed-banks'
export const SUBSTRATE_ID = 'bed-composition-and-large-wood-substrate-condition-grain-size-embeddedness-fines-consolidation'
export const BANK_EROSION_ID = 'channel-and-floodplain-dynamics-bank-erosion-and-armoring-condition'
export const CHANNEL_EVOL_ID = 'channel-evolution-channel-evolution-stage-and-trends'
export const IMPAIRMENT_ID = 'water-and-soil-quality-regulatory-impairment-status-305b-303d-tmdl'
export const CPOM_ID = 'carbon-processing-detrital-processing-cpom-retention-shredders'
export const NUTRIENTS_ID = 'nutrient-cycling-nitrogen-and-phosphorus-concentrations'
export const TEMPERATURE_ID = 'light-and-thermal-regime-stream-temperature'
export const INVASIVES_ID = 'community-dynamics-invasive-non-native-species-presence'
export const BARRIERS_ID = 'watershed-connectivity-fish-passage-and-barrier-effects-longitudinal-connectivity'
export const HABITAT_ID = 'habitat-provision-in-stream-habitat-complexity-and-cover'
export const BIOINTEGRITY_ID = 'population-support-biological-integrity-ibi-community-condition'

export const CHANNELIZED_FCODES = new Set([33600, 33601, 33603])

/** metrics.base.AnalysisContext */
export interface EasiCtx {
  lat: number
  lon: number
  comid: number | null
  huc8: string | null
  huc12: string | null
  watershed_geojson: any
  reach_geojson: any
  drainage_area_sqkm: number | null
  slope: number | null
  fcode: number | null
  stream_order: number | null
  sinuosity: number | null
  extras: Record<string, any>
}

/** metrics.base.MetricResult */
export interface MetricResult {
  metric_id: string
  value: any
  value_text: string
  rating: string | null
  confidence: string
  source: string
  status: string
  note: string
  detail: Record<string, any> | null
  scoring: Record<string, any> | null
}

function result(mid: string, o: Partial<MetricResult>): MetricResult {
  return { metric_id: mid, value: null, value_text: '', rating: null, confidence: 'L', source: '', status: 'ok', note: '', detail: null, scoring: null, ...o }
}

/** unavailable: a graceful degradation, keeping the trace (a withheld rating says its statement). */
export function unavailable(mid: string, note = '', confidence = 'L', scoring: Record<string, any> | null = null, valueText = 'not available'): MetricResult {
  const trace = scoring || {}
  if (trace.completeness === 'withheld' && trace.statement) {
    note = String(trace.statement)
    valueText = 'not rated (documented gap)'
  }
  return result(mid, { rating: null, status: 'unavailable', confidence, note, value_text: valueText, scoring })
}

const f1 = (v: any) => pyFixed(Number(v), 1)
const f2 = (v: any) => pyFixed(Number(v), 2)
const f3 = (v: any) => pyFixed(Number(v), 3)
const f4 = (v: any) => pyFixed(Number(v), 4)

// ---- base.py

const sc = (ctx: EasiCtx): Record<string, any> => ctx.extras.streamcat || {}
const strata = (ctx: EasiCtx) => ctx.extras.strata || {}
const geomOf = (ctx: EasiCtx): Record<string, any> => ctx.extras.reach_geomorph || {}

export const XS_SOURCE_LEGACY = 'USGS 3DEP representative cross section'
export const XS_REACH_NOTE = 'Scored on the reach median; the plotted section is the one nearest the medians.'

export function xsCapNote(bhrStats: Record<string, any> | null | undefined): string {
  const s = bhrStats || {}
  const capped = Math.trunc(Number(s.capped || 0))
  const n = Math.trunc(Number(s.n || 0))
  if (!capped) return ''
  return `No bank was found below the floodprone stage on ${capped} of ${n} sections. Those read 2.00, the cap at twice the bankfull depth.`
}

export function xsSource(geom: Record<string, any> | null | undefined): string {
  const n = Math.trunc(Number(((geom || {}).reach || {}).n || 0))
  return n >= 2 ? `USGS 3DEP reach cross-sections (median of ${n})` : XS_SOURCE_LEGACY
}

export function xsEvidence(geom: Record<string, any> | null | undefined): Record<string, any> | null {
  const quality = crossSectionQuality(geom)
  return quality ? { crossSectionQuality: quality } : null
}

export const ratedKeys = (trace: Record<string, any> | null | undefined): string[] =>
  ((trace || {}).inputs || []).filter((x: any) => !x.contextOnly).map((x: any) => x.key)

export const meanComposite = (trace: Record<string, any> | null | undefined): boolean => (trace || {}).methodKind === 'mean_index'

export function meanText(trace: Record<string, any>): string {
  const rated = (trace.inputs || []).filter((x: any) => x.rating)
  const parts = rated.map((x: any) => `${x.key} ${x.rating}`).join(', ')
  const combined = trace.combinedValue
  const value = combined !== null && combined !== undefined ? f3(combined) : 'n/a'
  return `mean rating index ${value} (${parts})`
}

function integrityValue(value: any): number | null {
  if (value === null || value === undefined) return null
  const n = pyFloatOf(value)
  if (n === null) return null
  return n >= 0.0 && n <= 1.0 ? n : null
}

export function integrityPair(ctx: EasiCtx, component: string): [number | null, number | null] {
  const key = component.trim().toLowerCase()
  const values = sc(ctx)
  return [integrityValue(values[`${key}cat`]), integrityValue(values[`${key}ws`])]
}

export function integrityProducts(ctx: EasiCtx): Record<string, number> | null {
  const values: Record<string, number> = {}
  for (const component of ['hyd', 'chem', 'sed', 'conn', 'temp', 'habt']) {
    const [cat, wsv] = integrityPair(ctx, component)
    if (cat === null || wsv === null) return null
    values[`${component}Cat`] = cat
    values[`${component}Ws`] = wsv
  }
  return values
}

const layer = (ctx: EasiCtx) => ws.layer(ctx.extras)
const agPct = (ctx: EasiCtx) => ws.agPct(layer(ctx))
const inputSource = (ctx: EasiCtx, key: string) => ws.inputSource(ctx.extras, key)
const resultSource = (ctx: EasiCtx, key: string) => ws.resultSource(ctx.extras, key)
const guidance = (ctx: EasiCtx, dflt: string) => ws.guidance(ctx.extras, dflt)
const wsValue = (ctx: EasiCtx, key: string) => ws.value(ctx.extras, key)
const traceInputs = (ev: Evaluation): Record<string, any> => Object.fromEntries((ev.trace.inputs || []).map((x: any) => [x.key, x]))

// ---- hydrology.py

function imperviousPct(ctx: EasiCtx): [number | null, string] {
  const value = wsValue(ctx, 'imperviousPct')
  if (value !== null) return [Number(value), inputSource(ctx, 'impervious.impervious')]
  if (ws.provider(ctx.extras) !== ws.STREAMCAT) return [null, '']
  const v = (ctx.extras.landcover || {}).impervious_pct
  return v === null || v === undefined ? [null, ''] : [Number(v), 'NLCD 2021 impervious (watershed)']
}

function agriculturePct(ctx: EasiCtx): [number | null, string] {
  const value = agPct(ctx)
  if (value !== null) return [Number(value), inputSource(ctx, 'impervious.agriculture')]
  if (ws.provider(ctx.extras) !== ws.STREAMCAT) return [null, '']
  const v = (ctx.extras.landcover || {}).ag_pct
  return v === null || v === undefined ? [null, ''] : [Number(v), 'NLCD 2021 agriculture (watershed)']
}

function impervious(ctx: EasiCtx): MetricResult {
  const [imp, impSource] = imperviousPct(ctx)
  const [ag, agSource] = agriculturePct(ctx)
  const ev = evaluate(IMPERVIOUS_ID, { impervious: imp, agriculture: ag }, {
    input_meta: { impervious: { source: impSource }, agriculture: { source: agSource } }, confidence: 'H' })
  if (ev.rating === null) return unavailable(IMPERVIOUS_ID, guidance(ctx, 'no land-cover data available'), 'H', ev.trace)
  const inputs = traceInputs(ev)
  const governing = ev.trace.governingInput
  const available = ev.trace.inputs.filter((x: any) => x.available)
  const comparison = available.map((x: any) => `${x.label.split('Watershed ').join('')} ${f1(x.value)}% (${pyStr(x.rating ?? null)})`).join(', ')
  let value: number
  let valueText: string
  let note: string
  if (meanComposite(ev.trace)) {
    value = Number(ev.combined_value)
    valueText = meanText(ev.trace)
    note = `${comparison}. The composite index is the mean of the inputs' rating indices.`
  } else {
    const gov = inputs[governing]
    value = Number(gov.value)
    valueText = `${f1(gov.value)}% ${gov.label.split('Watershed ').join('').toLowerCase()} (${governing} governs)`
    note = `${comparison}. The more limiting input governs.`
  }
  const detail = {
    governing,
    impervious: imp === null ? null : { pct: pyRound(imp, 1), rating: inputs.impervious.rating ?? null },
    agriculture: ag === null ? null : { pct: pyRound(ag, 1), rating: inputs.agriculture.rating ?? null },
  }
  const sources = available.filter((x: any) => x.source).map((x: any) => x.source).join(' + ')
  return result(IMPERVIOUS_ID, { value, value_text: valueText, rating: ev.rating, confidence: 'H', source: sources, note, detail, scoring: ev.trace })
}

function wetlands(ctx: EasiCtx): MetricResult {
  const ev = evaluate(WETLANDS_ID, { woodyWetland: wsValue(ctx, 'woodyWetlandPct'), herbaceousWetland: wsValue(ctx, 'herbWetlandPct') }, {
    input_meta: { woodyWetland: { source: inputSource(ctx, 'wetlands.woodyWetland') },
      herbaceousWetland: { source: inputSource(ctx, 'wetlands.herbaceousWetland') } },
    confidence: 'H' })
  if (ev.rating === null) {
    return unavailable(WETLANDS_ID, guidance(ctx, 'both woody- and herbaceous-wetland source fields are required'), 'H', ev.trace)
  }
  const value = Number(ev.combined_value)
  return result(WETLANDS_ID, { value: pyRound(value, 2), value_text: `${f1(value)}% combined woody + herbaceous wetland cover`,
    rating: ev.rating, confidence: 'H', source: resultSource(ctx, 'wetlands'),
    note: 'Provisional national screening tiers; natural wetland abundance is regional.', scoring: ev.trace })
}

function flowAlteration(ctx: EasiCtx): MetricResult {
  const storage = wsValue(ctx, 'damStorageM3PerSqkm')
  const runoff = wsValue(ctx, 'runoffMm')
  const ev = evaluate(FLOW_ALTERATION_ID, { storage, runoff }, {
    input_meta: { storage: { source: inputSource(ctx, 'flowAlteration.storage') }, runoff: { source: inputSource(ctx, 'flowAlteration.runoff') } },
    confidence: 'M' })
  if (ev.rating === null) {
    const note = runoff !== null ? 'annual runoff must be present and greater than zero' : 'storage and annual runoff are required'
    return unavailable(FLOW_ALTERATION_ID, guidance(ctx, note), 'M', ev.trace)
  }
  const dor = Number(ev.combined_value)
  return result(FLOW_ALTERATION_ID, { value: pyRound(dor, 3),
    value_text: `degree of regulation ${f2(dor)}% (storage ${pyFixedComma(Number(storage), 0)} m³/km²; runoff ${pyFixedComma(Number(runoff), 0)} mm)`,
    rating: ev.rating, confidence: 'M', source: resultSource(ctx, 'flowAlteration'),
    note: ws.resultNote(ctx.extras, 'flowAlteration', 'Storage/annual-runoff screen; does not represent operating rules or diversions.'),
    scoring: ev.trace })
}

function reachInflow(ctx: EasiCtx): MetricResult {
  const ev = evaluate(REACH_INFLOW_ID, { roadDensity: wsValue(ctx, 'roadDensity') }, {
    input_meta: { roadDensity: { source: inputSource(ctx, 'reachInflow.roadDensity') } }, confidence: 'L' })
  if (ev.rating === null) return unavailable(REACH_INFLOW_ID, guidance(ctx, 'no road-density data'), 'L', ev.trace)
  const value = Number(ev.combined_value)
  return result(REACH_INFLOW_ID, { value: pyRound(value, 2), value_text: `${f2(value)} km/km² road density`,
    rating: ev.rating, confidence: 'L', source: resultSource(ctx, 'reachInflow'),
    note: 'Directional proxy for concentrated inflow pressure; does not count outfalls.', scoring: ev.trace })
}

// ---- hydraulics.py

function floodplainEngagement(ctx: EasiCtx): MetricResult {
  const geom = geomOf(ctx)
  const bhr = geom.bank_height_ratio ?? null
  const extrapolated = Boolean(geom.bankfull_extrapolated)
  const confidence = geom.edge_limited || extrapolated ? 'L' : 'M'
  const reach = describeReach(geom.reach, 'bank_height_ratio')
  const bhrStats = (geom.reach || {}).bank_height_ratio || {}
  const source = xsSource(geom)
  const ev = evaluate(FLOODPLAIN_ENGAGEMENT_ID, { bhr }, { input_meta: { bhr: { source } }, confidence, evidence: xsEvidence(geom) })
  if (ev.rating === null) {
    return unavailable(FLOODPLAIN_ENGAGEMENT_ID, 'bank-height ratio unavailable for the reach cross-sections', confidence, ev.trace)
  }
  const warning = (ev.trace.warnings || []).join(' ')
  let note = warning || 'Direct BHR screen; surveyed cross-section geometry may refine.'
  if (reach) note += ' ' + XS_REACH_NOTE
  const cap = xsCapNote(bhrStats)
  if (cap) note += ' ' + cap
  if (extrapolated) note += ' Drainage area is outside the Bieger fit range; bankfull is extrapolated.'
  const bhrTxt = fmtBhr(bhr, bhrStats.median_capped, true)
  return result(FLOODPLAIN_ENGAGEMENT_ID, { value: pyRound(Number(bhr), 3),
    value_text: `bank-height ratio ${bhrTxt} (low-bank height / max bankfull depth)` + (reach ? `, ${reach}` : ''),
    rating: ev.rating, confidence, source, note, scoring: ev.trace })
}

function floodplainAccess(ctx: EasiCtx): MetricResult {
  const geom = geomOf(ctx)
  const er = geom.entrenchment_ratio ?? null
  const edge = Boolean(geom.edge_limited)
  const extrapolated = Boolean(geom.bankfull_extrapolated)
  const confidence = edge || extrapolated ? 'L' : 'M'
  const reach = describeReach(geom.reach, 'entrenchment_ratio')
  const source = xsSource(geom)
  const ev = evaluate(ENTRENCHMENT_ID, { er }, { context: { strata: strata(ctx) }, input_meta: { er: { source } }, confidence, evidence: xsEvidence(geom) })
  if (ev.rating === null) return unavailable(ENTRENCHMENT_ID, '3DEP entrenchment ratio unavailable for reach', confidence, ev.trace)
  const res = geom.dem_resolution_m || 10
  let note = `DEM ${pyStr(res)} m, bankfull from the Bieger regional curve.`
  if (reach) note += ' ' + XS_REACH_NOTE
  if (edge) note += ' Flood-prone width reached the buffer edge and ER may be underestimated.'
  if (extrapolated) note += ' Drainage area is outside the Bieger fit range; bankfull is extrapolated.'
  note += ' Naturally confined valleys require field interpretation.'
  return result(ENTRENCHMENT_ID, { value: pyRound(Number(er), 3),
    value_text: `entrenchment ratio ${f2(er)} (flood-prone width / bankfull width)` + (reach ? `, ${reach}` : ''),
    rating: ev.rating, confidence, source, note, scoring: ev.trace })
}

const MONTHS = Array.from({ length: 12 }, (_, i) => `qe_${String(i + 1).padStart(2, '0')}`)

/** monthly_flow_cv: the population CV of the twelve EROM monthly flows, to six places. */
export function monthlyFlowCv(erom: any): number | null {
  if (!erom || typeof erom !== 'object' || Array.isArray(erom)) return null
  const raw = MONTHS.map((k) => erom[k])
  if (raw.some((v) => v === null || v === undefined || typeof v === 'boolean')) return null
  const monthly = raw.map((v) => pyFloatOf(v))
  if (monthly.some((v) => v === null)) return null
  if (!(monthly as number[]).every(Number.isFinite)) return null
  const mean = fsum(monthly as number[]) / 12
  if (!Number.isFinite(mean) || mean <= 0) return null
  const variance = fsum((monthly as number[]).map((v) => (v - mean) * (v - mean))) / 12
  const cv = Math.sqrt(variance) / mean
  return Number.isFinite(cv) ? pyRound(cv, 6) : null
}

/** monthly_flow_min_ratio: the lowest month over QE_MA (the national builder's q_min_ratio). */
export function monthlyFlowMinRatio(erom: any): number | null {
  if (!erom || typeof erom !== 'object' || Array.isArray(erom)) return null
  const raw = MONTHS.map((k) => erom[k])
  let annual = erom.qe_ma
  if (annual === null || annual === undefined || typeof annual === 'boolean') return null
  if (raw.some((v) => v === null || v === undefined || typeof v === 'boolean')) return null
  const monthly = raw.map((v) => pyFloatOf(v))
  annual = pyFloatOf(annual)
  if (monthly.some((v) => v === null) || annual === null) return null
  if (!(monthly as number[]).every(Number.isFinite) || !Number.isFinite(annual)) return null
  if (annual <= 0) return null
  const ratio = Math.min(...(monthly as number[])) / annual
  return Number.isFinite(ratio) ? ratio : null
}

function lowFlow(ctx: EasiCtx): MetricResult {
  const erom = ctx.extras.erom || {}
  const cv = monthlyFlowCv(erom)
  const ratio = monthlyFlowMinRatio(erom)
  const source = 'NHDPlus V2 EROM modeled monthly flows'
  const ev = evaluate(LOW_FLOW_ID, { flowCv: cv, flowMinRatio: ratio, meanAnnualFlow: erom.qe_ma ?? null, fcodeContext: ctx.fcode }, {
    context: { strata: strata(ctx) },
    input_meta: { flowCv: { source }, flowMinRatio: { source: source + ' and QE_MA' }, meanAnnualFlow: { source: 'NHDPlus V2 EROM QE_MA' },
      fcodeContext: { source: 'NHDPlus FCODE' } },
    confidence: 'L', source_tier: 'published-model', evidence_family: 'erom_flow', used_fallback: false })
  const readsRatio = ratedKeys(ev.trace).includes('flowMinRatio')
  if (ev.rating === null) {
    return unavailable(LOW_FLOW_ID, readsRatio ? 'twelve finite EROM monthly flows and a positive mean annual flow are required'
      : 'twelve finite EROM monthly flows with a positive mean are required', 'L', ev.trace)
  }
  if (readsRatio) {
    return result(LOW_FLOW_ID, { value: ratio, value_text: `EROM minimum-month over mean annual flow ratio ${f3(ratio)}`,
      rating: ev.rating, confidence: 'L', source,
      note: 'Candidate low-flow proxy: the lowest of the twelve EROM mean monthly flows over the mean annual flow, rated against NARS-9 reference curves with a national fallback. Modeled monthly flows do not measure daily low flow or wetted connectivity.',
      scoring: ev.trace })
  }
  return result(LOW_FLOW_ID, { value: cv, value_text: `EROM monthly flow variability (CV) ${f3(cv)}`, rating: ev.rating, confidence: 'L', source,
    note: 'Unvalidated low-flow proxy (field agreement 0.18). Monthly climatological variability does not measure daily low flow or wetted connectivity. Interpret natural intermittency against the regional reference expectations.',
    scoring: ev.trace })
}

function hyporheic(ctx: EasiCtx): MetricResult {
  const ev = evaluate(HYPORHEIC_ID, { slope: ctx.slope, sinuosity: ctx.sinuosity }, {
    input_meta: { slope: { source: 'NHDPlus slope' }, sinuosity: { source: 'selected reach geometry' } }, confidence: 'L' })
  if (ev.rating === null) return unavailable(HYPORHEIC_ID, 'channel slope and reach sinuosity are both unavailable', 'L', ev.trace)
  const inputs = traceInputs(ev)
  const governing = ev.trace.governingInput
  const gov = inputs[governing] || {}
  const parts: string[] = []
  for (const [key, spec] of [['slope', (v: number) => `slope ${f4(v)} m/m`], ['sinuosity', (v: number) => `sinuosity ${f3(v)}`]] as const) {
    const row = inputs[key] || {}
    if (row.value !== null && row.value !== undefined && row.rating) parts.push(`${spec(Number(row.value))} (${row.rating})`)
  }
  const partial = ev.trace.completeness === 'partial'
  const suffix = partial ? ', single pathway' : ''
  const sources = ev.trace.inputs.filter((x: any) => x.available && x.source).map((x: any) => x.source)
  if (meanComposite(ev.trace)) {
    return result(HYPORHEIC_ID, { value: pyRound(Number(ev.combined_value), 4),
      value_text: `${meanText(ev.trace)} (mean of the pathway indices${suffix})`, rating: ev.rating, confidence: 'L', source: sources.join(' + '),
      note: `${parts.join(', ')}. The composite index is the mean of the pathway indices. Slope screens vertical bedform-driven exchange and sinuosity screens lateral meander-driven exchange. Bed hydraulic conductivity is unavailable, so steep bedrock or fine-bedded reaches can overpredict exchange.`,
      scoring: ev.trace })
  }
  const govValue = Number(gov.value)
  const govText = governing === 'slope' ? `channel slope ${f4(govValue)} m/m` : `sinuosity ${f3(govValue)}`
  return result(HYPORHEIC_ID, { value: pyRound(govValue, 4), value_text: `${govText} (${governing} pathway governs${suffix})`,
    rating: ev.rating, confidence: 'L', source: sources.join(' + '),
    note: `${parts.join(', ')}. The better pathway governs. Slope screens vertical bedform-driven exchange and sinuosity screens lateral meander-driven exchange. Bed hydraulic conductivity is unavailable, so steep bedrock or fine-bedded reaches can overpredict exchange.`,
    scoring: ev.trace })
}

// ---- geomorphology.py

function channelEvolution(ctx: EasiCtx): MetricResult {
  const geom = geomOf(ctx)
  const bhr = geom.bank_height_ratio ?? null
  const er = geom.entrenchment_ratio ?? null
  const edge = Boolean(geom.edge_limited)
  const confidence = 'L'
  if (ctx.fcode !== null && CHANNELIZED_FCODES.has(Number(ctx.fcode))) {
    const ev = evaluate(CHANNEL_EVOL_ID, { fcode: ctx.fcode }, { input_meta: { fcode: { source: 'NHDPlus FCODE' } }, confidence: 'M',
      variant_key: 'channelized-fcode', source_tier: 'screening-proxy', evidence_family: 'channelization_class', used_fallback: true })
    return result(CHANNEL_EVOL_ID, { value: ctx.fcode, value_text: `canal/ditch classification (NHD FCODE ${pyStr(ctx.fcode)})`,
      rating: ev.rating, confidence: 'M', source: 'NHDPlus FCODE',
      note: 'Artificial channelization is directly identified; current adjustment severity is not measured.', scoring: ev.trace })
  }
  const source = xsSource(geom)
  const reachN = Math.trunc(Number((geom.reach || {}).n || 0))
  const bhrStats = (geom.reach || {}).bank_height_ratio || {}
  const ev = evaluate(CHANNEL_EVOL_ID, { bhr, er, fcodeContext: ctx.fcode }, {
    context: { strata: strata(ctx) },
    input_meta: { bhr: { source }, er: { source }, fcodeContext: { source: 'NHDPlus FCODE' } },
    confidence, source_tier: 'screening-proxy', evidence_family: 'incision_geometry', used_fallback: true, evidence: xsEvidence(geom) })
  if (ev.rating === null) return unavailable(CHANNEL_EVOL_ID, 'both BHR and ER are required for the channel-adjustment proxy', confidence, ev.trace)
  let note = 'Low-confidence susceptibility proxy; BHR and ER are channel-evolution clues, not a formal stage assessment. Observed stage evidence supersedes this result.'
  if (reachN >= 2) note += ' ' + XS_REACH_NOTE
  const cap = xsCapNote(bhrStats)
  if (cap) note += ' ' + cap
  if (edge) note += ' Flood-prone width reached the DEM buffer edge; ER may be underestimated.'
  const reachTxt = reachN >= 2 ? `, reach medians of ${reachN} sections` : ''
  const bhrTxt = fmtBhr(bhr, bhrStats.median_capped, true)
  const governs = meanComposite(ev.trace) ? 'mean of the BHR and ER indices' : `${pyStr(ev.trace.governingInput ?? null)} governs`
  return result(CHANNEL_EVOL_ID, { value: { bhr, er },
    value_text: `channel-adjustment susceptibility (BHR ${bhrTxt}, ER ${f2(er)}${reachTxt}), ${governs}`,
    rating: ev.rating, confidence, source: `${source} + NHDPlus FCODE`, note, scoring: ev.trace })
}

function sedimentSupply(ctx: EasiCtx): MetricResult {
  const ev = evaluate(SEDIMENT_ID, { agriculture: agPct(ctx), kFactor: wsValue(ctx, 'soilKFactor'), roadDensity: wsValue(ctx, 'roadDensity') }, {
    input_meta: { agriculture: { source: inputSource(ctx, 'sediment.agriculture') }, kFactor: { source: inputSource(ctx, 'sediment.kFactor') },
      roadDensity: { source: inputSource(ctx, 'sediment.roadDensity') } },
    confidence: 'M' })
  if (ev.rating === null) return unavailable(SEDIMENT_ID, guidance(ctx, 'agriculture, K-factor, and road density are all required'), 'M', ev.trace)
  const inputs = traceInputs(ev)
  const governing = ev.trace.governingInput
  const gov = inputs[governing] || {}
  const parts: string[] = []
  for (const [key, spec] of [['agriculture', (v: number) => `agriculture ${f1(v)}%`], ['kFactor', (v: number) => `K ${f2(v)}`],
    ['roadDensity', (v: number) => `roads ${f2(v)} km/km²`]] as const) {
    const v = (inputs[key] || {}).value
    if (v !== null && v !== undefined) parts.push(spec(Number(v)))
  }
  if (meanComposite(ev.trace)) {
    return result(SEDIMENT_ID, { value: Number(ev.combined_value), value_text: `${meanText(ev.trace)} (${parts.join(', ')})`,
      rating: ev.rating, confidence: 'M', source: resultSource(ctx, 'sediment'),
      note: 'The composite index is the mean of the source indicators\' rating indices. K-factor is intrinsic erodibility and can lower the rating without disturbance.',
      scoring: ev.trace })
  }
  const govLabel = ({ agriculture: 'agricultural cover', kFactor: 'soil erodibility', roadDensity: 'road density' } as Record<string, string>)[governing] ?? pyStr(governing)
  return result(SEDIMENT_ID, { value: gov.value === null || gov.value === undefined ? null : Number(gov.value),
    value_text: `${govLabel} governs (${parts.join(', ')})`, rating: ev.rating, confidence: 'M', source: resultSource(ctx, 'sediment'),
    note: 'The most limiting source indicator governs. K-factor is intrinsic erodibility and can lower the rating without disturbance.',
    scoring: ev.trace })
}

function substrate(ctx: EasiCtx): MetricResult {
  const agriculture = agPct(ctx)
  const ev = evaluate(SUBSTRATE_ID, { agriculture }, { input_meta: { agriculture: { source: inputSource(ctx, 'substrate.agriculture') } },
    confidence: 'L', source_tier: 'screening-proxy', evidence_family: 'watershed_landcover', used_fallback: false })
  if (ev.rating === null) return unavailable(SUBSTRATE_ID, guidance(ctx, 'watershed crop and hay cover are required'), 'L', ev.trace)
  return result(SUBSTRATE_ID, { value: agriculture, value_text: `watershed agriculture ${f1(agriculture)}%`, rating: ev.rating, confidence: 'L',
    source: resultSource(ctx, 'substrate'),
    note: 'Unvalidated pressure proxy for bed condition. Disclosed correlated pair with Catchment hydrology: both use watershed agriculture and the same 30/50 percent bands. This does not measure bed composition, embeddedness or large wood.',
    scoring: ev.trace })
}

function bankErosion(ctx: EasiCtx): MetricResult {
  const geom = geomOf(ctx)
  const bhr = geom.bank_height_ratio ?? null
  const reach = describeReach(geom.reach, 'bank_height_ratio')
  const bhrStats = (geom.reach || {}).bank_height_ratio || {}
  const source = xsSource(geom)
  const ev = evaluate(BANK_EROSION_ID, { bhr }, { input_meta: { bhr: { source } }, confidence: 'L', source_tier: 'screening-proxy',
    evidence_family: 'incision_geometry', used_fallback: true, evidence: xsEvidence(geom) })
  if (ev.rating === null) return unavailable(BANK_EROSION_ID, 'bank-height ratio unavailable for the bank-instability susceptibility proxy', 'L', ev.trace)
  const warning = (ev.trace.warnings || []).join(' ')
  const cap = xsCapNote(bhrStats)
  const bhrTxt = fmtBhr(bhr, bhrStats.median_capped, true)
  return result(BANK_EROSION_ID, { value: Number(bhr),
    value_text: `bank-instability susceptibility from BHR ${bhrTxt}` + (reach ? `, ${reach}` : ''),
    rating: ev.rating, confidence: 'L', source,
    note: (warning ? warning + ' ' : '') + 'BHR does not detect armoring or directly measure erosion. Complete observed erosion and armoring percentages supersede this proxy.'
      + (reach ? ' ' + XS_REACH_NOTE : '') + (cap ? ' ' + cap : ''),
    scoring: ev.trace })
}

// ---- physicochemistry.py

function attainsResult(record: Record<string, any>, confidence: string): MetricResult {
  const category = record.ircategory ?? null
  const ev = evaluate(IMPAIRMENT_ID, { category }, {
    input_meta: { category: { source: 'EPA ATTAINS', details: { assessmentUnit: record.assessment_unit ?? null, assessmentName: record.assessment_name ?? null,
      overallStatus: record.overallstatus ?? null, distanceM: record.distance_m ?? null, matchType: record.match_type ?? null } } },
    confidence, source_tier: record.match_type === 'nearby' ? 'connected-nearby' : 'observed', evidence_family: 'attains_assessment', used_fallback: false })
  const distance = record.distance_m
  let prefix: string
  let warning: string
  if (record.match_type === 'nearby') {
    prefix = `nearest assessed unit ${pyFixedComma(Number(distance), 0)} m away`
    warning = 'Nearby, not necessarily this reach.'
  } else {
    prefix = 'assessment unit intersects selected point'
    warning = ''
  }
  const text = `${prefix}: IR category ${category || '—'} (AU ${record.assessment_unit || '—'})`
  if (ev.rating === null) {
    return result(IMPAIRMENT_ID, { value: category, value_text: text, rating: null, confidence, source: 'EPA ATTAINS', status: 'not_assessed',
      note: ('Category 3 has insufficient evidence and remains unscored. ' + warning).trim(), scoring: ev.trace })
  }
  return result(IMPAIRMENT_ID, { value: category, value_text: text, rating: ev.rating, confidence, source: 'EPA ATTAINS', note: warning, scoring: ev.trace })
}

function chemFallback(ctx: EasiCtx, mid: string, reason: string): MetricResult {
  const [cat, wsv] = integrityPair(ctx, 'chem')
  const variant = mid === NUTRIENTS_ID ? 'streamcat-chem-integrity-nutrient' : 'streamcat-chem-integrity-regulatory'
  const ev = evaluate(mid, { chemCatchment: cat, chemWatershed: wsv }, { context: { fallbackReason: reason },
    input_meta: { chemCatchment: { source: 'EPA StreamCat CHEMcat' }, chemWatershed: { source: 'EPA StreamCat CHEMws' } },
    confidence: 'L', variant_key: variant, source_tier: 'screening-proxy', evidence_family: 'iwi_landscape', used_fallback: true })
  const label = mid === NUTRIENTS_ID ? 'nutrient-condition' : 'water-quality condition'
  if (ev.rating === null) {
    return unavailable(mid, `${reason} and both StreamCat CHEM components are also required`, 'L', ev.trace, `${label} evidence unavailable`)
  }
  const value = Number(ev.combined_value)
  return result(mid, { value, value_text: `CHEM integrity fallback ${f2(value)} (catchment ${f2(cat)}, watershed ${f2(wsv)})`,
    rating: ev.rating, confidence: 'L', source: 'EPA StreamCat CHEM catchment + watershed components',
    note: `${reason}. Landscape-integrity fallback for ${label}. It is not a measured concentration or regulatory determination. The 0.40/0.70 classes are EASI integration tiers.`,
    scoring: ev.trace })
}

function impairment(ctx: EasiCtx): MetricResult {
  const attains = ctx.extras.attains || {}
  const exact = attains.exact || {}
  if (exact.assessment_unit) {
    const r = attainsResult(exact, 'H')
    if (r.rating) return r
    return chemFallback(ctx, IMPAIRMENT_ID, 'intersecting ATTAINS Category 3 is inconclusive')
  }
  const nearby = attains.nearby || {}
  if (nearby.assessment_unit) {
    const r = attainsResult(nearby, 'M/L')
    if (r.rating) return r
    return chemFallback(ctx, IMPAIRMENT_ID, 'nearby ATTAINS Category 3 is inconclusive')
  }
  return chemFallback(ctx, IMPAIRMENT_ID, 'no qualifying ATTAINS assessed unit at the point or within 2 km')
}

function detritalCpom(ctx: EasiCtx): MetricResult {
  const breakdown = ws.riparianVegBreakdown(layer(ctx))
  const values: Record<string, number | null> = breakdown
    ? { forest: breakdown.forest, shrub: breakdown.shrub, grassland: breakdown.grassland, wetland: breakdown.wetland }
    : { forest: null, shrub: null, grassland: null, wetland: null }
  const ev = evaluate(CPOM_ID, values, { context: { strata: strata(ctx) },
    input_meta: Object.fromEntries(Object.keys(values).map((k) => [k, { source: inputSource(ctx, `cpom.${k}`) }])), confidence: 'M' })
  if (ev.rating === null) {
    return unavailable(CPOM_ID, guidance(ctx, 'forest, shrub, grassland, and wetland source fields are all required'), 'M', ev.trace)
  }
  const total = Number(ev.combined_value)
  return result(CPOM_ID, { value: pyRound(total, 1), value_text: `${f1(total)}% organic-matter supply potential (100 m corridor)`,
    rating: ev.rating, confidence: 'M', source: resultSource(ctx, 'cpom'),
    note: 'Proxy for supply potential only; it does not measure CPOM retention or shredder condition.',
    detail: { kind: 'riparian_veg', ...breakdown }, scoring: ev.trace })
}

function nutrients(ctx: EasiCtx): MetricResult {
  const wqp = ctx.extras.wqp || {}
  const tnSummary = wqp.tn ?? null
  const tpSummary = wqp.tp ?? null
  const code = (ctx.extras.strata || {}).nars9 ?? null
  const region = code ? { code, name: ctx.extras.nars9Name ?? null } : null
  const regionCode = (region || {} as any).code ?? null
  const tn = (tnSummary || {}).value ?? null
  const tp = (tpSummary || {}).value ?? null
  const available = [tnSummary, tpSummary].filter((s) => s && s.value !== null && s.value !== undefined)
  const observations = available.reduce((a, s) => a + Math.trunc(Number(s.observation_count || 0)), 0)
  const nearestValues = available.map((s) => s.nearest_distance_mi).filter((v) => v !== null && v !== undefined)
  const nearest = nearestValues.length ? Math.min(...nearestValues) : null
  const confidence = available.length && available.every((s) => Math.trunc(Number(s.observation_count || 0)) >= 3
    && s.nearest_distance_mi !== null && s.nearest_distance_mi !== undefined && Number(s.nearest_distance_mi) <= 1) ? 'M' : 'L'
  const ev = evaluate(NUTRIENTS_ID, { tn, tp }, { context: { region: regionCode, regionName: (region || {} as any).name ?? null },
    input_meta: { tn: { source: 'Water Quality Portal', details: tnSummary }, tp: { source: 'Water Quality Portal', details: tpSummary } },
    confidence, source_tier: 'connected-nearby', evidence_family: 'wqp_monitoring', used_fallback: false })
  const parts: string[] = []
  if (tn !== null) parts.push(`TN ${f3(tn)} mg/L`)
  if (tp !== null) parts.push(`TP ${f4(tp)} mg/L`)
  if (!parts.length) {
    return chemFallback(ctx, NUTRIENTS_ID, !regionCode ? 'NARS region could not be resolved' : 'no qualifying total-fraction WQP TN/TP observations')
  }
  if (ev.rating === null) return chemFallback(ctx, NUTRIENTS_ID, 'official NARS region is unavailable for WQP regional thresholds')
  const stationCount = available.reduce((a, s) => a + Math.trunc(Number(s.station_count || 0)), 0)
  const excluded = [tnSummary, tpSummary].filter((s) => s).reduce((a, s) => a + Math.trunc(Number(s.excluded_count || 0)), 0)
  const dates = available.flatMap((s) => [s.date_start, s.date_end]).filter((d) => d)
  const sortedDates = [...dates].sort()
  const coverage = `${observations} valid observation(s), ${stationCount} station(s)`
    + (dates.length ? `, ${sortedDates[0]} to ${sortedDates[sortedDates.length - 1]}` : '')
    + (nearest !== null ? `, nearest ${f2(nearest)} mi` : '')
    + `; ${excluded} row(s) excluded`
  return result(NUTRIENTS_ID, { value: { tn, tp, region: regionCode }, value_text: `${parts.join(', ')} (${pyStr(regionCode)} region)`,
    rating: ev.rating, confidence, source: 'Water Quality Portal + EPA NARS nine-region thresholds', note: coverage,
    detail: { tn: tnSummary, tp: tpSummary, region }, scoring: ev.trace })
}

function streamTemperature(ctx: EasiCtx): MetricResult {
  const woodyBreakdown = ws.riparianWoodyBreakdown(layer(ctx))
  const woody = woodyBreakdown === null ? null : woodyBreakdown.total
  const imperv = wsValue(ctx, 'imperviousPct')
  const temperature = (ctx.extras.wqp || {}).temp ?? null
  const ev = evaluate(TEMPERATURE_ID, { woodyRiparian: woody, impervious: imperv }, { context: { strata: strata(ctx) },
    input_meta: { woodyRiparian: { source: inputSource(ctx, 'temperature.woodyRiparian'), details: woodyBreakdown },
      impervious: { source: inputSource(ctx, 'temperature.impervious') } },
    confidence: 'L' })
  ev.trace.context.wqpTemperature = temperature
  if (ev.rating === null) {
    return unavailable(TEMPERATURE_ID, guidance(ctx, 'both woody riparian cover and watershed impervious cover are required'), 'L', ev.trace)
  }
  const inputs = traceInputs(ev)
  const governing = ev.trace.governingInput
  let contextNote = ''
  if (temperature && temperature.value !== null && temperature.value !== undefined) {
    contextNote = ` Nearby WQP temperature context: ${f1(temperature.value)} °C from ${pyStr(temperature.observation_count ?? 0)} observation(s), not scored.`
  }
  const governs = meanComposite(ev.trace) ? 'mean of both indices' : `${pyStr(governing)} governs`
  return result(TEMPERATURE_ID, { value: Number(ev.combined_value),
    value_text: `thermal vulnerability: woody riparian ${f1(woody)}%, impervious ${f1(imperv)}% (${governs})`,
    rating: ev.rating, confidence: 'L', source: resultSource(ctx, 'temperature'),
    note: 'Vulnerability proxy for thermal loading and shade loss; not stream temperature.' + contextNote,
    detail: { woody: woodyBreakdown, temperatureContext: temperature, governing, inputs }, scoring: ev.trace })
}

// ---- biology.py

function invasives(ctx: EasiCtx): MetricResult {
  const taxa: string[] | null = ctx.extras.nasTaxa ?? null
  const scope = ctx.huc12 ? 'HUC12' : ctx.huc8 ? 'HUC8' : ''
  const confidence = scope === 'HUC12' ? 'M' : 'L'
  if (taxa === null) {
    const ev = evaluate(INVASIVES_ID, { taxaCount: null }, { confidence, source_tier: 'connected-nearby', evidence_family: 'nas_records' })
    return unavailable(INVASIVES_ID, 'USGS NAS query unavailable', confidence, ev.trace)
  }
  const count = taxa.length
  const ev = evaluate(INVASIVES_ID, { taxaCount: count }, { input_meta: { taxaCount: { source: `USGS NAS (${scope})` } }, confidence,
    source_tier: 'connected-nearby', evidence_family: 'nas_records' })
  let valueText: string
  if (count === 0) valueText = `no established taxa recorded (${scope})`
  else {
    const sample = taxa.slice(0, 4).join(', ') + (count > 4 ? '…' : '')
    valueText = `${count} established non-native taxa recorded (${scope})`
    if (sample) valueText += `: ${sample}`
  }
  let note = 'Record-based screening result; zero is not confirmed absence.'
  if (scope === 'HUC8') note += ' HUC8 fallback is less spatially specific.'
  return result(INVASIVES_ID, { value: count, value_text: valueText, rating: ev.rating, confidence, source: `USGS NAS (${scope})`, note, scoring: ev.trace })
}

function barriers(ctx: EasiCtx): MetricResult {
  const dams: any[] | null = ctx.extras.nidDams ?? null
  if (dams === null) {
    const ev = evaluate(BARRIERS_ID, { damCount: null }, { confidence: 'M', source_tier: 'connected-nearby', evidence_family: 'nid_proximity' })
    return unavailable(BARRIERS_ID, 'USACE NID query unavailable', 'M', ev.trace)
  }
  const count = dams.length
  const ev = evaluate(BARRIERS_ID, { damCount: count }, { input_meta: { damCount: { source: 'USACE NID geodesic proximity query', details: dams } },
    confidence: 'M', source_tier: 'connected-nearby', evidence_family: 'nid_proximity' })
  const valueText = count === 0 ? 'no mapped NID dams within 1 mile' : `${count} mapped NID dam(s) within 1 mile (potential barrier)`
  return result(BARRIERS_ID, { value: count, value_text: valueText, rating: ev.rating, confidence: 'M', source: 'USACE NID',
    note: 'Proximity only. A mapped dam count does not establish passability or severity. Two or more dams within a mile rate Poor as a screening flag to verify.',
    scoring: ev.trace })
}

function habitatComplexity(ctx: EasiCtx): MetricResult {
  const woodyB = ws.riparianWoodyBreakdown(layer(ctx))
  const woody = woodyB === null ? null : woodyB.total
  const sinuosity = ctx.sinuosity
  const geom = geomOf(ctx)
  const widthCv = bankfullWidthCv(geom)
  const ev = evaluate(HABITAT_ID, { woodyRiparian: woody, sinuosity, widthCv }, { context: { strata: strata(ctx) },
    input_meta: { woodyRiparian: { source: inputSource(ctx, 'habitat.woodyRiparian') }, sinuosity: { source: 'selected reach geometry' },
      widthCv: { source: xsSource(geom) } },
    confidence: 'L' })
  const readsWidth = ratedKeys(ev.trace).includes('widthCv')
  if (ev.rating === null) {
    return unavailable(HABITAT_ID, readsWidth ? 'bankfull width variability across at least two 3DEP cross-sections is required'
      : guidance(ctx, 'woody riparian cover is required'), 'L', ev.trace)
  }
  const value = Number(ev.combined_value)
  const sinTxt = sinuosity === null || sinuosity === undefined ? '' : ` (sinuosity ${f2(sinuosity)} shown as context)`
  return result(HABITAT_ID, { value: pyRound(value, 1), value_text: `woody riparian cover ${f1(value)}%${sinTxt}`, rating: ev.rating,
    confidence: 'L', source: resultSource(ctx, 'habitat'),
    note: 'Corridor-cover proxy for habitat support. NARS-9 reference expectations can credit naturally open prairie and desert channels; this is not a field habitat inventory.',
    scoring: ev.trace })
}

function biologicalIntegrity(ctx: EasiCtx): MetricResult {
  const raw = sc(ctx).prg_bmmi0809
  let prg: number | null = raw !== null && raw !== undefined && typeof raw !== 'boolean' ? pyFloatOf(raw) : null
  if (prg !== null && (!Number.isFinite(prg) || !(prg >= 0 && prg <= 1))) prg = null
  if (prg !== null) {
    const source = 'EPA StreamCat prg_bmmi0809 (areaOfInterest other)'
    const ev = evaluate(BIOINTEGRITY_ID, { prGBmmi: prg }, { input_meta: { prGBmmi: { source } }, confidence: 'M/L', source_tier: 'published-model',
      evidence_family: 'streamcat_biological_model', used_fallback: false })
    return result(BIOINTEGRITY_ID, { value: prg, value_text: `predicted Good-BMMI probability ${f2(prg)}`, rating: ev.rating, confidence: 'M/L', source,
      note: 'Published benthic-condition model probability. The 0.25/0.50 classes are EASI integration tiers; this is not a measured MMI or multi-community IBI.',
      scoring: ev.trace })
  }
  const values = integrityProducts(ctx) || {}
  const ev = evaluate(BIOINTEGRITY_ID, values, { input_meta: Object.fromEntries(Object.keys(values).map((k) => [k, { source: `EPA StreamCat ${k}` }])),
    confidence: 'L', variant_key: 'streamcat-integrity-products', source_tier: 'published-model', evidence_family: 'iwi_landscape', used_fallback: true })
  if (ev.rating === null) return unavailable(BIOINTEGRITY_ID, 'prg_bmmi0809 or all twelve ICI/IWI components are required', 'L', ev.trace)
  const pv = (ev.trace.context || {}).products || {}
  return result(BIOINTEGRITY_ID, { value: Number(ev.combined_value),
    value_text: `landscape integrity fallback ${f3(ev.combined_value)} (ICI ${f3(pv.ICI)}, IWI ${f3(pv.IWI)})`,
    rating: ev.rating, confidence: 'L', source: 'EPA StreamCat published ICI/IWI component products',
    note: 'Predicted landscape condition, not a measured IBI. Multiplication reuses landscape evidence scored elsewhere; 0.40/0.70 are EASI integration tiers.',
    scoring: ev.trace })
}

// ---- registry.py

/** REGISTRY, in its order */
export const REGISTRY: Record<string, (ctx: EasiCtx) => MetricResult> = {
  [IMPERVIOUS_ID]: impervious, [WETLANDS_ID]: wetlands, [FLOW_ALTERATION_ID]: flowAlteration, [REACH_INFLOW_ID]: reachInflow,
  [LOW_FLOW_ID]: lowFlow, [HYPORHEIC_ID]: hyporheic, [ENTRENCHMENT_ID]: floodplainAccess, [FLOODPLAIN_ENGAGEMENT_ID]: floodplainEngagement,
  [SEDIMENT_ID]: sedimentSupply, [SUBSTRATE_ID]: substrate, [BANK_EROSION_ID]: bankErosion, [CHANNEL_EVOL_ID]: channelEvolution,
  [IMPAIRMENT_ID]: impairment, [CPOM_ID]: detritalCpom, [NUTRIENTS_ID]: nutrients, [TEMPERATURE_ID]: streamTemperature,
  [INVASIVES_ID]: invasives, [BARRIERS_ID]: barriers, [HABITAT_ID]: habitatComplexity, [BIOINTEGRITY_ID]: biologicalIntegrity,
}

/** EXTERNAL_SERVICE: the metrics whose Python adapters call a federal service (the live "waiting on" hint). */
export const EXTERNAL_SERVICE: Record<string, string> = {
  [IMPAIRMENT_ID]: 'EPA ATTAINS', [NUTRIENTS_ID]: 'Water Quality Portal', [TEMPERATURE_ID]: 'Water Quality Portal',
  [INVASIVES_ID]: 'USGS NAS', [BARRIERS_ID]: 'USACE NID',
}

const METRIC_ANCHOR: Record<string, string> = {
  [HYPORHEIC_ID]: 'clickedReach', [ENTRENCHMENT_ID]: 'clickedReach', [FLOODPLAIN_ENGAGEMENT_ID]: 'clickedReach',
  [BANK_EROSION_ID]: 'clickedReach', [CHANNEL_EVOL_ID]: 'clickedReach',
  [IMPAIRMENT_ID]: 'clickedPoint', [NUTRIENTS_ID]: 'clickedPoint', [INVASIVES_ID]: 'clickedPoint', [BARRIERS_ID]: 'clickedPoint',
  [LOW_FLOW_ID]: 'surrogateComid', [SUBSTRATE_ID]: 'surrogateComid', [BIOINTEGRITY_ID]: 'surrogateComid',
  [IMPERVIOUS_ID]: 'watershed', [WETLANDS_ID]: 'watershed', [FLOW_ALTERATION_ID]: 'watershed', [REACH_INFLOW_ID]: 'watershed',
  [SEDIMENT_ID]: 'watershed', [CPOM_ID]: 'watershed', [TEMPERATURE_ID]: 'watershed', [HABITAT_ID]: 'watershed',
}

export const STREAMCAT_FALLBACK_FAMILIES = ['iwi_landscape', 'streamcat_biological_model']

/** metric_anchor (the regional criteria anchor substrate to the watershed). */
export function metricAnchor(mid: string): string {
  if (mid === SUBSTRATE_ID) return 'watershed'
  return METRIC_ANCHOR[mid] ?? 'watershed'
}

/** STREAMCAT_NAMES less prG_BMMI (the regional criteria), and the model column of aoi "other". */
export const STREAMCAT_NAMES = ['pctimp2019', 'pctwdwet2019', 'pcthbwet2019', 'pctcrop2019', 'pcthay2019', 'pctmxfst2019', 'pctdecid2019',
  'pctconif2019', 'pctgrs2019', 'pctshrb2019', 'kffact', 'rddens', 'damnrmstor', 'runoff', 'hyd', 'sed', 'chem', 'conn', 'temp', 'habt']
export const STREAMCAT_OTHER_NAMES = ['prg_bmmi0809']


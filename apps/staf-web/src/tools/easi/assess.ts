// EASI's assessment (easi/assessment.py): the adapters' results as report rows for all 20 metrics,
// each row's anchor, the rollup with its coverage and evidence profile, and the instant re-scorings
// the worksheet makes (overrides, observations, edited cross-section heights). Key for key and in the
// Python's key order.
import { pyFixed, pyFloat } from '../../app/pyfmt'
import { deriveFromStages } from '../../engine/geomorph'
import { BANK_EROSION_ID, CHANNEL_EVOL_ID, CHANNELIZED_FCODES, type EasiCtx, ENTRENCHMENT_ID, FLOODPLAIN_ENGAGEMENT_ID, metricAnchor,
  type MetricResult, REGISTRY, STREAMCAT_FALLBACK_FAMILIES, unavailable } from './adapters'
import { criteriaFor, evaluate, methodFor, methodForTrace, pyRound, pyStr } from './evaluator'
import { cwaMapping, metricRegistry, metricsById, OUTCOMES, WEIGHTS } from './method'
import { basinCharacteristics, borrowedNote } from './notices'
import { functionScore, ratingToIndex, reportingFields, rollup, rollupReportingEnabled, round2 } from './scoring'
import { SITE_ENGINE, STREAMCAT } from './watershed'

export const VALID = new Set(['Good', 'Fair', 'Poor'])
type Obj = Record<string, any>

/** _build_row: one report row from an adapter result (or its absence). */
export function buildRow(mid: string, meta: Obj, info: Obj, res: MetricResult | undefined, selected: Set<string>, overrides: Obj): Obj {
  const catalogMethod = methodFor(mid)
  let generated: string | null
  let source: string
  let valueText: string
  let status: string
  let confidence: string
  let note: string
  let scoringTrace: Obj | null
  if (res !== undefined) {
    ;[generated, source, valueText] = [res.rating, res.source, res.value_text]
    ;[status, confidence, note] = [res.status, res.confidence, res.note]
    scoringTrace = res.scoring
  } else if (mid in REGISTRY && !selected.has(mid)) {
    ;[generated, source, valueText] = [null, '', 'not included in this analysis']
    ;[status, confidence] = ['excluded', info.confidence ?? 'L']
    note = 'excluded from this analysis'
    scoringTrace = null
  } else {
    ;[generated, source, valueText] = [null, '', 'not available']
    ;[status, confidence] = ['pending', info.confidence ?? 'L']
    note = 'metric adapter not yet implemented'
    scoringTrace = null
  }
  let rating = generated
  const generatedValueText = valueText
  let effectiveOverride: Obj | null = null
  if (mid in overrides && status !== 'excluded') {
    rating = overrides[mid]
    status = 'override'
    source = 'user override'
    valueText = `user-provided: ${rating}`
    note = 'overrides generated value'
    effectiveOverride = { rating, generatedRating: generated, generatedValueText }
  }
  let idx: number | null = null
  let fscore: number | null = null
  if (rating !== null && VALID.has(rating)) {
    idx = ratingToIndex(rating, meta.indexMidpoints)
    fscore = functionScore(idx)
  }
  const detail = res ? res.detail : null
  const landCover = detail && 'governing' in detail ? detail : null
  const ripVeg = detail && detail.kind === 'riparian_veg' ? detail : null
  const m = methodForTrace(mid, scoringTrace)
  const methodCriteria = criteriaFor(m, (scoringTrace || {}).context || {})
  const automated = methodCriteria.automated || []
  const bands: Obj = automated.length === 1 ? { ...(automated[0].bands || {}) } : {}
  const t = scoringTrace || {}
  return {
    metricId: mid, name: meta.name, discipline: meta.discipline,
    methodTitle: catalogMethod.title,
    functionId: meta.functionId, functionName: meta.functionName,
    scale: info.scale ?? null, confidence,
    rating, generatedRating: generated,
    generatedValueText,
    index: idx !== null ? pyRound(idx, 3) : null,
    functionScore: fscore, valueText,
    criteria: rating !== null && VALID.has(rating) ? (bands[rating] ?? '') : '',
    criteriaBands: bands,
    methodCriteria,
    methodKey: m.methodKey,
    methodKind: m.operator,
    basisClass: m.basisClass,
    sourceTier: t.sourceTier ?? null,
    evidenceFamily: t.evidenceFamily ?? null,
    usedFallback: Boolean(t.usedFallback),
    observedOverridesProxy: Boolean(t.observedOverridesProxy),
    completeness: t.completeness || (rating === null ? 'not_assessed' : 'complete'),
    scoring: scoringTrace,
    effectiveOverride,
    landCover,
    ripVeg,
    source, status, note,
    overrideable: Boolean(info.overrideable),
  }
}

const COVERED_LABELS: Record<string, string> = {
  clickedReach: 'assessed reach', clickedPoint: 'assessment point', surrogateComid: 'assessed reach (COMID {comid})', watershed: 'assessed watershed',
}
const ROUTED_WATERSHED_LABELS: Record<string, string> = {
  [SITE_ENGINE]: 'HR reach watershed (STAF site engine)', [STREAMCAT]: 'surrogate watershed (StreamCat lookup engine)',
  null: 'unavailable (HR reach watershed not calculated)',
}
const ENGINE_LABELS: Record<string, string> = {
  [STREAMCAT]: 'StreamCat lookup engine', [SITE_ENGINE]: 'STAF site engine', unavailable: 'watershed evidence unavailable', '': '',
}

/** _annotate_anchors: every row's framework-fixed anchor, its label and the engine token that answered it. */
export function annotateAnchors(rows: Obj[], siteAnchor: Obj | null | undefined, watershedLayer: Obj | null | undefined): void {
  const anchor = siteAnchor || {}
  const routed = anchor.anchorKind === 'hrSurrogate'
  const applied = Boolean((anchor.reanchored || {}).applied)
  const comid = (anchor.scoredReach || {}).comid ?? null
  const dist = (anchor.routing || {}).routedDistanceFt ?? null
  const provider = watershedLayer !== null && watershedLayer !== undefined ? ('provider' in watershedLayer ? watershedLayer.provider : STREAMCAT) : STREAMCAT
  let comidLabel = ''
  let comidNote = ''
  if (routed) {
    comidLabel = dist !== null ? `nearest covered reach (COMID ${pyStr(comid)}, ${commaInt(dist)} ft downstream)`
      : `nearest covered reach (COMID ${pyStr(comid)})`
    comidNote = borrowedNote(anchor)
  }
  const table: Obj = {}
  for (const r of rows) {
    let a = metricAnchor(r.metricId)
    if (r.usedFallback && STREAMCAT_FALLBACK_FAMILIES.includes(r.evidenceFamily)) a = 'surrogateComid'
    let engine = ''
    if (a === 'watershed') engine = provider ? provider : 'unavailable'
    else if (a === 'surrogateComid') engine = STREAMCAT
    let label: string
    if (!routed) label = COVERED_LABELS[a].split('{comid}').join(pyStr(comid))
    else if (a === 'clickedReach' || a === 'clickedPoint') {
      label = !applied ? 'surrogate reach (HR data unavailable)' : a === 'clickedReach' ? 'clicked HR reach' : 'clicked point'
    } else if (a === 'surrogateComid') label = comidLabel
    else label = ROUTED_WATERSHED_LABELS[String(provider)]
    r.anchor = a
    r.anchorLabel = label
    r.engine = engine
    r.engineLabel = ENGINE_LABELS[engine]
    if (routed && a === 'surrogateComid' && ['ok', 'observed'].includes(r.status)) r.anchorNote = comidNote
    table[r.metricId] = { anchor: a, label, name: r.name, engine }
  }
  if (routed && siteAnchor) siteAnchor.metricAnchors = table
}

/** f"{x:,.0f}" */
function commaInt(x: number): string {
  const text = pyFixed(Number(x), 0)
  const neg = text.startsWith('-')
  return (neg ? '-' : '') + (neg ? text.slice(1) : text).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

/** _finalize: the scored report (rollup, coverage, evidence profile) from finished rows. */
export function finalize(rows: Obj[], totalCount: number, overridesApplied: Iterable<string>): Obj {
  const meta = metricsById()
  const ratings: [string, string][] = rows.filter((r) => VALID.has(r.rating)).map((r) => [r.metricId, r.rating])
  const functionScores: Record<string, number> = {}
  for (const [mid, rt] of ratings) functionScores[meta[mid].functionId] = functionScore(ratingToIndex(rt, meta[mid].indexMidpoints))
  const roll = rollup(functionScores)
  const selectedRows = rows.filter((r) => r.status !== 'excluded')
  const ratedRows = selectedRows.filter((r) => VALID.has(r.rating))
  const overallFraction = selectedRows.length ? ratedRows.length / selectedRows.length : null
  const mapping = cwaMapping()
  const outcomeCoverage: Obj = {}
  for (const outcome of OUTCOMES) {
    let selectedWeight = 0.0
    let availableWeight = 0.0
    for (const row of selectedRows) {
      const code = (mapping[row.functionId] || {})[outcome] ?? '-'
      const weight = WEIGHTS[code] ?? 0.0
      selectedWeight += weight
      if (VALID.has(row.rating)) availableWeight += weight
    }
    const fraction = selectedWeight > 0 ? availableWeight / selectedWeight : null
    outcomeCoverage[outcome] = { availableWeight: pyRound(availableWeight, 3), selectedWeight: pyRound(selectedWeight, 3),
      fraction: fraction === null ? null : pyRound(fraction, 4) }
  }
  const limited: string[] = []
  if (overallFraction !== null && overallFraction < 0.70) limited.push('overall')
  for (const [outcome, item] of Object.entries(outcomeCoverage)) if (item.fraction !== null && item.fraction < 0.70) limited.push(outcome)
  const coverage: Obj = {
    overall: { rated: ratedRows.length, selected: selectedRows.length, fraction: overallFraction === null ? null : pyRound(overallFraction, 4) },
    outcomes: outcomeCoverage, threshold: 0.70, provisional: limited.length > 0, limited,
  }
  const evidenceProfile: Record<string, number> = { observed: 0, connectedNearby: 0, publishedModel: 0, screeningProxy: 0, manual: 0, unavailable: 0 }
  const familyRows: Record<string, Obj[]> = {}
  const TIERS: Record<string, string> = { observed: 'observed', 'connected-nearby': 'connectedNearby', 'published-model': 'publishedModel',
    'screening-proxy': 'screeningProxy', manual: 'manual' }
  for (const row of selectedRows) {
    if (!VALID.has(row.rating)) {
      evidenceProfile.unavailable += 1
      continue
    }
    if (row.status === 'override') {
      evidenceProfile.manual += 1
      continue
    }
    const trace = row.scoring || {}
    const tier = trace.sourceTier || row.sourceTier || 'screening-proxy'
    evidenceProfile[TIERS[tier] ?? 'screeningProxy'] += 1
    const family = trace.evidenceFamily || row.evidenceFamily
    if (family) (familyRows[family] ??= []).push(row)
  }
  const familyLabels: Record<string, string> = { incision_geometry: 'BHR/ER incision geometry', iwi_landscape: 'StreamCat integrity components',
    nrsa_field: 'shared NRSA reach evidence' }
  const correlationNotes: Obj[] = []
  for (const family of Object.keys(familyRows).sort()) {
    const related = familyRows[family]
    if (related.length < 2 || !(family in familyLabels)) continue
    correlationNotes.push({
      evidenceFamily: family, label: familyLabels[family], metricCount: related.length,
      metrics: related.map((row) => row.name || row.metricId),
      text: `${familyLabels[family]} is reused by ${related.length} metrics and should not be interpreted as independent evidence.`,
    })
  }
  const proxyDerived = selectedRows.some((row) => VALID.has(row.rating)
    && ((row.scoring || {}).usedFallback || (row.scoring || {}).sourceTier === 'screening-proxy'))
  const completeWithProxies = Boolean(overallFraction === 1.0 && proxyDerived)
  Object.assign(coverage, {
    evidenceProfile, completeWithProxies,
    statusMessage: completeWithProxies ? 'Complete screening coverage (includes proxy-derived ratings)'
      : overallFraction === 1.0 ? 'Complete screening coverage' : 'Partial screening coverage',
    correlationNotes,
  })
  const outcomes: Obj = {}
  for (const [k, o] of Object.entries(roll.outcomes)) {
    outcomes[k] = { direct: o.direct, indirect: o.indirect, weighted: round2(o.weighted), max: round2(o.max), subIndex: round2(o.subIndex) }
  }
  const out: Obj = {
    metricRows: rows,
    functionScores: roll.functionScores,
    subIndices: Object.fromEntries(Object.entries(roll.subIndices).map(([k, v]) => [k, round2(v)])),
    outcomes,
    ecosystemConditionIndex: round2(roll.eci),
    ecosystemConditionIndexRaw: roll.eci,
    subIndicesRaw: roll.subIndices,
    computedCount: ratedRows.length,
    selectedCount: selectedRows.length,
    totalCount,
    coverage,
    evidenceProfile,
    completeScreeningCoverage: overallFraction === 1.0,
    proxyDerivedRatings: proxyDerived,
    correlationNotes,
    provisionalCoverage: limited.length > 0,
    overridesApplied: [...overridesApplied].sort(),
  }
  if (rollupReportingEnabled()) Object.assign(out, reportingFields(roll))
  return out
}

/** The adapters over a prefetched context, the rows for all 20 metrics, the anchors and the rollup
 * (assess after its prefetch; assess_preloaded with the cross-section block left to the caller). */
export function scoreContext(ctx: EasiCtx, metricIds: string[] | null = null, overridesIn: Obj | null = null,
  onDone?: (mid: string) => void): Obj {
  const overrides = Object.fromEntries(Object.entries(overridesIn || {}).filter(([, v]) => VALID.has(v as string)))
  const selected = new Set(metricIds ?? Object.keys(REGISTRY))
  const byId: Record<string, MetricResult> = {}
  const registry = metricRegistry()
  for (const [mid, fn] of Object.entries(REGISTRY)) {
    if (!selected.has(mid)) continue
    try {
      byId[mid] = fn(ctx)
    } catch (err) {
      byId[mid] = unavailable(mid, `adapter error: ${err instanceof Error ? err.message : err}`, (registry[mid] || {} as any).confidence ?? 'L')
    }
    onDone?.(mid)
  }
  const meta = metricsById()
  const rows = Object.entries(meta).map(([mid, m]) => buildRow(mid, m, registry[mid] || {}, byId[mid], selected, overrides))
  annotateAnchors(rows, ctx.extras.siteAnchor, ctx.extras.watershed)
  const out = finalize(rows, Object.keys(meta).length, Object.keys(overrides))
  out.basin = basinCharacteristics(ctx)
  out.strata = { ...ctx.extras.strata }
  return out
}

/** rescore: overrides re-applied to a base report and the rollup recomputed (instant, no data). */
export function rescore(base: Obj, overridesIn: Obj | null): Obj {
  const overrides = Object.fromEntries(Object.entries(overridesIn || {}).filter(([, v]) => VALID.has(v as string)))
  const meta = metricsById()
  const rows: Obj[] = []
  for (const b of base.metricRows || []) {
    const mid = b.metricId
    const row = { ...b }
    if (mid in overrides && b.status !== 'excluded') {
      const rating = overrides[mid]
      const idx = ratingToIndex(rating, meta[mid].indexMidpoints)
      Object.assign(row, {
        rating, status: 'override', source: 'user override', valueText: `user-provided: ${rating}`, note: 'overrides generated value',
        index: pyRound(idx, 3), functionScore: functionScore(idx), criteria: (b.criteriaBands || {})[rating] ?? '',
        effectiveOverride: { rating, generatedRating: b.generatedRating ?? null, generatedValueText: b.generatedValueText ?? null },
      })
    } else {
      row.rating = b.generatedRating ?? null
      row.effectiveOverride = null
    }
    rows.push(row)
  }
  const result = finalize(rows, base.totalCount ?? Object.keys(meta).length, Object.keys(overrides))
  if (base.crossSection) result.crossSection = base.crossSection
  if (base.basin) result.basin = base.basin
  if ('strata' in base) result.strata = base.strata
  return result
}

/** apply_observed_evidence: complete user observations replace the bank and channel proxies. */
export function applyObservedEvidence(report: Obj, evidence: Obj | null): Obj {
  const supplied = { ...(evidence || {}) }
  const meta = metricsById()
  const rows = (report.metricRows || []).map((r: Obj) => ({ ...r }))
  const applied: string[] = []
  for (const row of rows) {
    const mid = row.metricId
    const item = supplied[mid] || {}
    let ev: ReturnType<typeof evaluate> | null = null
    let valueText = ''
    let source = ''
    let note = ''
    if (mid === BANK_EROSION_ID) {
      const erosion = item.erodingBankPct
      const armoring = item.armoredBankPct
      if (erosion === null || erosion === undefined || armoring === null || armoring === undefined) continue
      const values = { erodingBankPct: erosion, armoredBankPct: armoring, annualRetreatContext: item.annualRetreatContext ?? null }
      ev = evaluate(mid, values, {
        input_meta: Object.fromEntries(Object.keys(values).map((k) => [k, { source: 'user field/verified-imagery observation' }])),
        confidence: 'H', variant_key: 'observed-bank-condition', source_tier: 'observed', evidence_family: 'bank_observation',
        used_fallback: false, observed_overrides_proxy: true })
      valueText = `observed bank condition (${pyFixed(Number(erosion), 1)}% eroding, ${pyFixed(Number(armoring), 1)}% armored)`
      source = 'user-observed bank erosion and armoring'
      note = 'Both observed components supplied; the worse component governs.'
      if (item.annualRetreatContext !== null && item.annualRetreatContext !== undefined && item.annualRetreatContext !== '') {
        note += ` Annual retreat ${item.annualRetreatContext} is supporting evidence only and is not universally classified.`
      }
    } else if (mid === CHANNEL_EVOL_ID) {
      const stageClass = item.stageClass
      const indicators = String(item.indicators || '').trim()
      if (!VALID.has(stageClass) || !indicators) continue
      const values = { stageClass, indicators }
      ev = evaluate(mid, values, {
        input_meta: { stageClass: { source: 'user field assessment' }, indicators: { source: 'user field notes' } },
        confidence: 'H', variant_key: 'observed-channel-adjustment', source_tier: 'observed', evidence_family: 'channel_observation',
        used_fallback: false, observed_overrides_proxy: true })
      const labels: Record<string, string> = { Good: 'stable or recovered', Fair: 'moderately adjusting', Poor: 'severely or destructively adjusting' }
      valueText = `observed channel condition: ${labels[stageClass]}`
      source = 'user-documented channel-stage assessment'
      note = `Observed indicators: ${indicators}`
    }
    if (ev === null || !VALID.has(ev.rating as string)) continue
    const proxy = { rating: row.generatedRating || row.rating, valueText: row.generatedValueText || row.valueText, source: row.source ?? null,
      scoring: row.scoring ?? null }
    const m = methodForTrace(mid, ev.trace)
    const criteria = criteriaFor(m, ev.trace.context || {})
    const automated = criteria.automated || []
    const bands: Obj = automated.length === 1 ? { ...(automated[0].bands || {}) } : {}
    const idx = ratingToIndex(ev.rating as string, meta[mid].indexMidpoints)
    Object.assign(row, {
      rating: ev.rating, index: pyRound(idx, 3), functionScore: functionScore(idx), valueText, source, status: 'observed', confidence: 'H', note,
      scoring: ev.trace, methodKey: m.methodKey, methodKind: m.operator, basisClass: m.basisClass,
      sourceTier: ev.trace.sourceTier ?? null, evidenceFamily: ev.trace.evidenceFamily ?? null, usedFallback: false, observedOverridesProxy: true,
      completeness: ev.trace.completeness || 'complete', methodCriteria: criteria, criteriaBands: bands, criteria: bands[ev.rating as string] ?? '',
      proxyResult: proxy,
      effectiveOverride: { kind: 'observed', rating: ev.rating, generatedRating: proxy.rating, generatedValueText: proxy.valueText },
    })
    applied.push(mid)
  }
  const overridesApplied = new Set<string>(report.overridesApplied || [])
  for (const mid of applied) overridesApplied.delete(mid)
  const result = finalize(rows, report.totalCount ?? Object.keys(meta).length, overridesApplied)
  for (const key of ['crossSection', 'basin', 'strata']) if (report[key]) result[key] = report[key]
  result.observedEvidenceApplied = [...applied].sort()
  return result
}

/** rate_metrics_from_stages: the four cross-section metrics' ratings at user-chosen stages. */
export function rateMetricsFromStages(block: Obj, bankfullStage: number, floodplainStage: number, reason = 'edited', strata: Obj | null = null): Record<string, Obj> {
  const out: Record<string, Obj> = {}
  const where = reason !== 'scrolled' ? 'edited cross section' : `section at ${block.label || 'the shown station'}`
  // a grid section not yet drawn (no profile) is only ever shown at its own stages: its ratios are the grid's
  const d = block.stations
    ? deriveFromStages(block.stations, block.elevs, block.thalweg ?? null, bankfullStage, floodplainStage)
    : { entrenchment_ratio: block.entrenchment_ratio ?? null, bank_height_ratio: block.bank_height_ratio ?? null }
  const ctxStrata = { strata: strata || {} }
  const er = d.entrenchment_ratio
  const erEv = evaluate(ENTRENCHMENT_ID, { er }, { input_meta: { er: { source: where } }, confidence: 'M', context: ctxStrata })
  if (erEv.rating) {
    out[ENTRENCHMENT_ID] = { rating: erEv.rating, valueText: `entrenchment ratio ${pyStrNum(er)} (flood-prone width / bankfull width, ${where})`, scoring: erEv.trace }
  }
  const bhr = d.bank_height_ratio
  const bhrEv = evaluate(FLOODPLAIN_ENGAGEMENT_ID, { bhr }, { input_meta: { bhr: { source: where } }, confidence: 'M', context: ctxStrata })
  if (bhrEv.rating) out[FLOODPLAIN_ENGAGEMENT_ID] = { rating: bhrEv.rating, valueText: `bank-height ratio ${pyStrNum(bhr)} (${where})`, scoring: bhrEv.trace }
  const bankEv = evaluate(BANK_EROSION_ID, { bhr }, { input_meta: { bhr: { source: where } }, confidence: 'L', source_tier: 'screening-proxy',
    evidence_family: 'incision_geometry', used_fallback: true, context: ctxStrata })
  if (bankEv.rating) {
    out[BANK_EROSION_ID] = { rating: bankEv.rating, valueText: `bank-instability susceptibility from BHR ${pyStrNum(bhr)} (${where})`, scoring: bankEv.trace }
  }
  const fcode = block.fcode ?? null
  const channelized = fcode !== null && CHANNELIZED_FCODES.has(Number(fcode))
  const channelEv = evaluate(CHANNEL_EVOL_ID, channelized ? { fcode } : { bhr, er, fcodeContext: fcode }, {
    input_meta: channelized ? { fcode: { source: 'NHDPlus FCODE' } }
      : { bhr: { source: where }, er: { source: where }, fcodeContext: { source: 'NHDPlus FCODE' } },
    confidence: channelized ? 'M' : 'L', variant_key: channelized ? 'channelized-fcode' : null, source_tier: 'screening-proxy',
    evidence_family: channelized ? 'channelization_class' : 'incision_geometry', used_fallback: true, context: ctxStrata })
  if (channelEv.rating) {
    out[CHANNEL_EVOL_ID] = {
      rating: channelEv.rating,
      valueText: channelized ? `canal/ditch classification (NHD FCODE ${fcode})`
        : `channel-adjustment susceptibility (BHR ${pyStrNum(bhr)}, ER ${pyStrNum(er)}, ${where})`,
      scoring: channelEv.trace,
    }
  }
  return out
}

/** str() of a ratio as the Python's f-string writes it (a float, or None) */
const pyStrNum = (v: number | null): string => (v === null || v === undefined ? 'None' : pyFloat(v))

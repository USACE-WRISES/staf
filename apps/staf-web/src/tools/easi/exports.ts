// EASI's report exports (easi/report.py _summary_pairs, build_csv, build_geojson): the CSV of the summary
// and every metric row with its scoring trace, and the GeoJSON of the watershed, the reach and the point
// with the summary and the metric table. A routed site adds its substitution provenance; a covered site's
// files keep their historical form.
import { pyFixed } from '../../app/pyfmt'
import { isMarkedFloat, markFloat, pyDumps } from '../../app/pyjson'
import { csvText, type Cell, PyNum } from '../../files/csv'
import { scoringMethod, scoringMethodText } from './identity'

type Obj = Record<string, any>
const DISCIPLINE_ORDER = ['Hydrology', 'Hydraulics', 'Geomorphology', 'Physicochemistry', 'Biology']

/** Python's sort of str keys: by code point. */
function cmp(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0
}

/** _ordered_rows: by discipline, then function name (a stable sort). */
export function orderedRows(rep: Obj): Obj[] {
  const order = new Map(DISCIPLINE_ORDER.map((d, i) => [d, i]))
  return [...(rep.metricRows ?? [])].sort((x, y) => (order.get(x.discipline) ?? 99) - (order.get(y.discipline) ?? 99) || cmp(String(x.functionName), String(y.functionName)))
}

/** A value as the CSV writes Python's str() of it: a float kept a float. */
function cell(container: Obj | null | undefined, key: string, fallback: Cell = null): Cell {
  if (!container || !(key in container)) return fallback
  const v = container[key]
  if (typeof v === 'number' && isMarkedFloat(container, key)) return new PyNum(v)
  return v === undefined ? null : v
}

/** "—" for None, else the value (shown). */
function shown(container: Obj | null | undefined, key: string): Cell {
  const v = (container ?? {})[key]
  return v === null || v === undefined ? '—' : cell(container, key)
}

const fixed0 = (v: number) => pyFixed(v, 0)

/** _summary_pairs */
export function summaryPairs(result: Obj): [string, Cell][] {
  const d = result.delineation ?? {}
  const rep = result.report ?? {}
  const sub = rep.subIndices ?? {}
  const coverage = rep.coverage ?? {}
  const outcome = coverage.outcomes ?? {}
  const profile = coverage.evidenceProfile ?? {}
  const pctOf = (o: Obj | null | undefined) => {
    const f = (o ?? {}).fraction
    return f === null || f === undefined ? '—' : `${fixed0(100 * Number(f))}%`
  }
  const pre = rep.precomputed
  const method = scoringMethod(result)
  const pairs: [string, Cell][] = [
    ['Stream', cell(d, 'gnis_name', '')],
    ['COMID', cell(d, 'comid', '')],
    ['HUC12', d.huc12 || ''],
    ['Drainage area (km2)', cell(d, 'drainage_area_sqkm', '')],
    ['Reach length (ft)', cell(d, 'reach_length_ft', '')],
    ['Snapped lat', cell(d, 'snapped_lat', '')],
    ['Snapped lon', cell(d, 'snapped_lon', '')],
    ...(pre ? [['Precomputed national dataset', `vintage ${pre.vintage || 'staging'}, tier ${pre.tier || 1}`] as [string, Cell]] : []),
    ...([['alternative_id', 'National scoring alternative'], ['build_id', 'National dataset build'], ['method_version', 'National execution method']]
      .filter(([key]) => (pre ?? {})[key]).map(([key, label]) => [label, cell(pre, key)] as [string, Cell])),
    ['Scoring method', scoringMethodText()],
    ...(method.acquisition_digest ? [['Evidence acquisition code', method.acquisition_digest] as [string, Cell]] : []),
    ['Ecosystem Condition Index', shown(rep, 'ecosystemConditionIndex')],
    ['Physical sub-index', shown(sub, 'physical')],
    ['Chemical sub-index', shown(sub, 'chemical')],
    ['Biological sub-index', shown(sub, 'biological')],
    ['Metrics rated', `${pyStr(rep, 'computedCount')}/${'selectedCount' in rep ? pyStr(rep, 'selectedCount') : pyStr(rep, 'totalCount')}`],
    ['Overall coverage', pctOf(coverage.overall)],
    ['Physical coverage', pctOf(outcome.physical)],
    ['Chemical coverage', pctOf(outcome.chemical)],
    ['Biological coverage', pctOf(outcome.biological)],
    ['Coverage status', coverage.statusMessage || (rep.provisionalCoverage ? 'Provisional (limited coverage)' : 'Sufficient coverage')],
    ['Observed evidence metrics', cell(profile, 'observed', 0) ?? 0],
    ['Connected/nearby evidence metrics', cell(profile, 'connectedNearby', 0) ?? 0],
    ['Published-model metrics', cell(profile, 'publishedModel', 0) ?? 0],
    ['Screening-proxy metrics', cell(profile, 'screeningProxy', 0) ?? 0],
    ['Manual metrics', cell(profile, 'manual', 0) ?? 0],
    ['Unavailable metrics', cell(profile, 'unavailable', 0) ?? 0],
    ['Overrides applied', (rep.overridesApplied ?? []).join(', ') || 'none'],
  ]
  const anchor = result.siteAnchor ?? {}
  if (anchor.anchorKind === 'hrSurrogate') {
    const clicked = anchor.clickedStream ?? {}
    const routing = anchor.routing ?? {}
    const source = d.watershed_source || ''
    const eng = d.watershed_engine ?? {}
    let rows: [string, Cell][]
    if (source === 'site-engine' || source === 'not-calculated') {
      const engineTxt = source === 'site-engine' ? `STAF site engine v${pyStr(eng, 'engineVersion')}`
        : `unavailable (${eng.reason || eng.status || 'not calculated'})`
      rows = [
        ['Stream outside the StreamCat lookup network', 'yes'],
        ['Assessed stream (NHDPlus HR)', clicked.gnisName || '(unnamed stream)'],
        ['Assessed stream NHDPlusID', cell(clicked, 'nhdplusId', '')],
        ['Assessed stream drainage area (km2)', shown(clicked, 'drainageAreaSqkm')],
        ['Watershed engine', engineTxt],
        ['HR reach watershed area (km2)', shown(eng, 'areaSqkm')],
        ['HR reach watershed reaches walked', shown(eng, 'nReaches')],
        ['COMID-keyed evidence reach', `COMID ${pyStr(d, 'comid')}`],
        ['Routed distance (ft)', shown(routing, 'routedDistanceFt')],
        ['Drainage area ratio', shown(routing, 'daRatio')],
        ['Routing method', routing.method || ''],
      ]
    } else {
      rows = [
        ['Scored at surrogate reach', 'yes'],
        ['Clicked stream (NHDPlus HR)', clicked.gnisName || '(unnamed stream)'],
        ['Clicked stream NHDPlusID', cell(clicked, 'nhdplusId', '')],
        ['Clicked stream drainage area (km2)', shown(clicked, 'drainageAreaSqkm')],
        ['Routed distance (ft)', shown(routing, 'routedDistanceFt')],
        ['Drainage area ratio', shown(routing, 'daRatio')],
        ['Drainage area ratio limit', shown(routing, 'daRatioLimit')],
        ['Routing method', routing.method || ''],
      ]
    }
    pairs.splice(7, 0, ...rows)
  }
  for (const r of (rep.basin ?? {}).rows ?? []) pairs.push([r[0], typeof r[1] === 'number' && isMarkedFloat(r, 1) ? new PyNum(r[1]) : r[1]])
  return pairs
}

/** str(x) of a value inside an f-string. */
function pyStr(container: Obj, key: string): string {
  const v = (container ?? {})[key]
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') {
    const c = cell(container, key)
    return c instanceof PyNum ? csvText([[c]]).trimEnd() : String(v)
  }
  return String(v)
}

/** build_csv */
export function buildCsv(result: Obj): string {
  const out: Cell[][] = [['EASI Report']]
  for (const [k, v] of summaryPairs(result)) out.push([k, v])
  out.push([])
  const rows = orderedRows(result.report ?? {})
  const hasNotes = rows.some((r) => r.userNote)
  const hasAnchor = (result.siteAnchor ?? {}).anchorKind === 'hrSurrogate'
  const header = ['Discipline', 'Function', 'Metric', 'Value', 'Rating', 'Index', 'FunctionScore', 'Confidence', 'Source', 'Status', 'MethodKey',
    'MethodKind', 'BasisClass', 'InputTrace', 'CombinedValue', 'GoverningInput', 'GeneratedRating', 'GeneratedIndex', 'Completeness',
    'SourceTier', 'EvidenceFamily', 'UsedFallback', 'ObservedOverridesProxy', 'RetainedProxyRating', 'OverallCoverage', 'PhysicalCoverage',
    'ChemicalCoverage', 'BiologicalCoverage', 'ProvisionalCoverage']
  if (hasAnchor) header.push('Anchor', 'Engine')
  if (hasNotes) header.push('Notes')
  out.push(header)
  const rep = result.report ?? {}
  const coverage = rep.coverage ?? {}
  const outcome = coverage.outcomes ?? {}
  const frac = (o: Obj | null | undefined) => cell(o ?? {}, 'fraction')
  for (const r of rows) {
    const trace = r.scoring ?? {}
    const or = (a: Obj, ka: string, b: Obj, kb: string): Cell => (a[ka] ? cell(a, ka) : cell(b, kb))
    const row: Cell[] = [r.discipline, r.functionName, r.name, r.valueText, r.rating || '', r.index !== null && r.index !== undefined ? cell(r, 'index') : '',
      r.functionScore !== null && r.functionScore !== undefined ? cell(r, 'functionScore') : '', r.confidence || '', r.source || '', r.status,
      'methodKey' in r ? cell(r, 'methodKey') : '', 'methodKind' in r ? cell(r, 'methodKind') : '', 'basisClass' in r ? cell(r, 'basisClass') : '',
      pyDumps(trace.inputs || [], { separators: [',', ':'] }),
      cell(trace, 'combinedValue'), cell(trace, 'governingInput'), cell(r, 'generatedRating'), cell(trace, 'generatedIndex'),
      trace.completeness ? cell(trace, 'completeness') : ('completeness' in r ? cell(r, 'completeness') : ''),
      or(trace, 'sourceTier', r, 'sourceTier'), or(trace, 'evidenceFamily', r, 'evidenceFamily'),
      !!('usedFallback' in trace ? trace.usedFallback : r.usedFallback ?? false),
      !!('observedOverridesProxy' in trace ? trace.observedOverridesProxy : r.observedOverridesProxy ?? false),
      cell(r.proxyResult ?? {}, 'rating'),
      frac(coverage.overall), frac(outcome.physical), frac(outcome.chemical), frac(outcome.biological),
      !!rep.provisionalCoverage]
    if (hasAnchor) row.push(cell(r, 'anchorLabel', ''), cell(r, 'engineLabel', ''))
    if (hasNotes) row.push(cell(r, 'userNote', ''))
    out.push(row)
  }
  return csvText(out)
}

/** build_geojson */
export function buildGeojson(result: Obj): string {
  const d = result.delineation ?? {}
  const rep = result.report ?? {}
  const basinRows: Obj = {}
  for (const r of (rep.basin ?? {}).rows ?? []) basinRows[r[0]] = r[1]
  const summary: Obj = {
    gnis_name: d.gnis_name ?? null, comid: d.comid ?? null, huc12: d.huc12 ?? null, drainage_area_sqkm: d.drainage_area_sqkm ?? null,
    ecosystem_condition_index: rep.ecosystemConditionIndex ?? null, sub_indices: rep.subIndices ?? null,
    metrics_computed: `${pyStr(rep, 'computedCount')}/${'selectedCount' in rep ? pyStr(rep, 'selectedCount') : pyStr(rep, 'totalCount')}`,
    coverage: rep.coverage ?? null, evidence_profile: rep.evidenceProfile ?? null,
    complete_screening_coverage: !!rep.completeScreeningCoverage, proxy_derived_ratings: !!rep.proxyDerivedRatings,
    correlation_notes: rep.correlationNotes || [], provisional_coverage: !!rep.provisionalCoverage, basin_characteristics: basinRows,
  }
  copyFloats(summary, d, ['drainage_area_sqkm'])
  copyFloats(summary, rep, ['ecosystemConditionIndex'], ['ecosystem_condition_index'])
  const anchor = result.siteAnchor ?? {}
  if (anchor.anchorKind === 'hrSurrogate') summary.site_anchor = anchor
  const method = scoringMethod(result)
  if (Object.keys(method).length) summary.scoring_method = method
  if (rep.precomputed) {
    const nd: Obj = {}
    for (const key of ['alternative_id', 'build_id', 'method_version', 'criteria_set', 'vintage', 'tier']) {
      if (rep.precomputed[key] !== null && rep.precomputed[key] !== undefined) nd[key] = rep.precomputed[key]
    }
    summary.national_dataset = nd
  }
  const features: Obj[] = []
  const add = (fc: Obj | null | undefined, props: Obj) => {
    for (const f of (fc ?? {}).features ?? []) features.push({ type: 'Feature', geometry: f.geometry ?? null, properties: props })
  }
  const props = (head: Obj) => {
    const p = { ...head, ...summary }
    for (const k of Object.keys(summary)) if (isMarkedFloat(summary, k)) copyFloats(p, summary, [k])
    return p
  }
  const ws = props({ type: 'watershed', area_sqkm: d.watershed_area_sqkm ?? null })
  copyFloats(ws, d, ['watershed_area_sqkm'], ['area_sqkm'])
  add(result.watershed_geojson, ws)
  const reach = props({ type: 'reach', length_ft: d.reach_length_ft ?? null })
  copyFloats(reach, d, ['reach_length_ft'], ['length_ft'])
  add(result.reach_geojson, reach)
  if (d.snapped_lat !== null && d.snapped_lat !== undefined) {
    const coords = [d.snapped_lon, d.snapped_lat]
    copyFloats(coords as unknown as Obj, d, ['snapped_lon', 'snapped_lat'], ['0', '1'])
    features.push({ type: 'Feature', properties: props({ type: 'point' }), geometry: { type: 'Point', coordinates: coords } })
  }
  const hasAnchor = anchor.anchorKind === 'hrSurrogate'
  const metrics: Obj = {}
  for (const r of rep.metricRows ?? []) {
    const trace = r.scoring ?? {}
    const m: Obj = {
      rating: r.rating ?? null, value: r.valueText ?? null, confidence: r.confidence ?? null, source: r.source ?? null, method_key: r.methodKey ?? null,
      method_kind: r.methodKind ?? null, basis_class: r.basisClass ?? null, input_trace: trace.inputs || [], combined_value: trace.combinedValue ?? null,
      governing_input: trace.governingInput ?? null, generated_rating: r.generatedRating ?? null, generated_index: trace.generatedIndex ?? null,
      completeness: trace.completeness || (r.completeness ?? null), source_tier: trace.sourceTier || (r.sourceTier ?? null),
      evidence_family: trace.evidenceFamily || (r.evidenceFamily ?? null),
      used_fallback: !!('usedFallback' in trace ? trace.usedFallback : r.usedFallback ?? false),
      observed_overrides_proxy: !!('observedOverridesProxy' in trace ? trace.observedOverridesProxy : r.observedOverridesProxy ?? false),
      retained_proxy: r.proxyResult ?? null,
    }
    copyFloats(m, trace, ['combinedValue', 'generatedIndex'], ['combined_value', 'generated_index'])
    if (hasAnchor) {
      m.anchor = r.anchorLabel ?? null
      m.engine = r.engineLabel ?? null
    }
    if (r.userNote) m.note = r.userNote
    metrics[r.metricId] = m
  }
  for (const f of features) if (f.properties.type === 'reach' || f.properties.type === 'point') f.properties.metrics = metrics
  return pyDumps({ type: 'FeatureCollection', features }, { indent: 2 })
}

/** The keys of ``to`` named ``toKeys`` hold floats where ``from``'s ``keys`` do. */
function copyFloats(to: Obj, from: Obj, keys: string[], toKeys: string[] = keys): void {
  keys.forEach((k, i) => {
    if (typeof from?.[k] === 'number' && isMarkedFloat(from, k)) {
      to[toKeys[i]] = to[toKeys[i]]
      markFloat(to, toKeys[i])
    }
  })
}


// DEEP's method in the browser (apps/deep/deep): the framework's functions and outcome mapping (the
// app's copies), the rollup with the coverage restriction (scoring.py), the reference curves
// (curves.py, measure.py), what a bundle says about its coverage and its curves (assessments.py,
// reference_support.py), and the words under a metric (describe.py, field_form.py, method_text.py).
// The same arithmetic in the same order as the Python.
import functionsJson from '../../app/content/deep/deep-functions.json'
import mappingJson from '../../app/content/deep/deep-outcome-mapping.json'
import conciseJson from '../../app/content/deep/method-text.json'
import { pyFixed, pyFixedComma, pyFloat, pyG, pySum } from '../../app/pyfmt'

export interface DeepFunction { id: string, name: string, category: string, order: number, function_statement?: string, [k: string]: unknown }
export type Metric = Record<string, any>
export type Bundle = Record<string, any>

// ---------------------------------------------------------------- config.py
export const WEIGHTS: Record<string, number> = { D: 1.0, i: 0.1, '-': 0.0 }
export const OUTCOMES = ['physical', 'chemical', 'biological'] as const
export const FUNCTION_SCORE_MAX = 15
export const INDEX_BANDS: [number, string][] = [[0.39, '#f5b5b5'], [0.69, '#f5e7a6'], [1.01, '#c8d9f2']]
export const INDEX_BAND_LABELS = ['Non-Functioning', 'Functioning-at-Risk', 'Functioning']
export const FUNCTION_SCORE_BANDS: [number, string][] = [[5, '#f5b5b5'], [10, '#f5e7a6'], [FUNCTION_SCORE_MAX, '#c8d9f2']]
export const FUNCTION_SCORE_BAND_SHORT = ['NF', 'AR', 'F']
export const CATEGORY_ORDER = ['Hydrology', 'Hydraulics', 'Geomorphology', 'Physicochemistry', 'Biology']

export const FUNCTIONS = functionsJson as DeepFunction[]
export const FUNCTIONS_BY_ID = new Map(FUNCTIONS.map((f) => [f.id, f]))
const MAPPING = new Map<string, Record<string, string>>((mappingJson as Record<string, string>[])
  .map((r) => [r.id, Object.fromEntries(OUTCOMES.map((k) => [k, r[k]]))]))
const CONCISE = conciseJson as Record<string, string>

const pyRound = (x: number, n: number) => Number(pyFixed(x, n))

/** Python's round(x) to an integer: half to even. */
function roundHalfEven(x: number): number {
  const f = Math.floor(x)
  const d = x - f
  if (d > 0.5) return f + 1
  if (d < 0.5) return f
  return f % 2 === 0 ? f : f + 1
}
export const round2 = (v: number): number => roundHalfEven(v * 100) / 100

export function indexBandColor(value: number): string {
  for (const [t, c] of INDEX_BANDS) if (value <= t) return c
  return INDEX_BANDS[INDEX_BANDS.length - 1][1]
}
export function indexBandLabel(value: number): string {
  for (let i = 0; i < INDEX_BANDS.length; i++) if (value <= INDEX_BANDS[i][0]) return INDEX_BAND_LABELS[i]
  return INDEX_BAND_LABELS[INDEX_BAND_LABELS.length - 1]
}
export function functionScoreBandColor(value: number): string {
  for (const [t, c] of FUNCTION_SCORE_BANDS) if (value <= t) return c
  return FUNCTION_SCORE_BANDS[FUNCTION_SCORE_BANDS.length - 1][1]
}
export function indexBandForBounds(low: number, high: number): string | null {
  const lo = indexBandLabel(low)
  return lo === indexBandLabel(high) ? lo : null
}

// ---------------------------------------------------------------- scoring.py
interface Acc { weighted: number, max: number, direct: number, indirect: number, unMax: number, unDirect: number, unIndirect: number }

const subIndex = (o: Acc): number | null => (o.max <= 0 || o.direct === 0 ? null : o.weighted / o.max)
function bounds(o: Acc): [number, number] | null {
  const total = o.max + o.unMax
  if (total <= 0) return null
  return [o.weighted / total, (o.weighted + o.unMax) / total]
}

export type Scored = Record<string, any>

/** scoring.score_assessment: the rollup, an index an interval while anything is unassessed. */
export function scoreAssessment(functionScores: Record<string, number>, unassessed: string[] = []): Scored {
  const un = [...new Set(unassessed)]
  const acc = Object.fromEntries(OUTCOMES.map((k) => [k, { weighted: 0.0, max: 0.0, direct: 0, indirect: 0, unMax: 0.0, unDirect: 0, unIndirect: 0 }])) as Record<string, Acc>
  for (const [fid, score] of Object.entries(functionScores)) {
    if (score === null || score === undefined || un.includes(fid)) continue
    const codes = MAPPING.get(fid)
    if (!codes) continue
    for (const key of OUTCOMES) {
      const code = codes[key] ?? '-'
      const w = WEIGHTS[code] ?? 0.0
      if (code === 'D') acc[key].direct += 1
      else if (code === 'i') acc[key].indirect += 1
      if (w) {
        acc[key].weighted += score * w
        acc[key].max += FUNCTION_SCORE_MAX * w
      }
    }
  }
  for (const fid of un) {
    const codes = MAPPING.get(fid)
    if (!codes) continue
    for (const key of OUTCOMES) {
      const code = codes[key] ?? '-'
      const w = WEIGHTS[code] ?? 0.0
      if (code === 'D') acc[key].unDirect += 1
      else if (code === 'i') acc[key].unIndirect += 1
      if (w) acc[key].unMax += FUNCTION_SCORE_MAX * w
    }
  }
  const sub = Object.fromEntries(OUTCOMES.map((k) => [k, subIndex(acc[k])])) as Record<string, number | null>
  const spans = OUTCOMES.map((k) => bounds(acc[k]))
  let eciBounds: [number, number] | null = null
  if (spans.every((b) => b !== null)) {
    eciBounds = [pySum((spans as [number, number][]).map((b) => b[0])) / spans.length,
      pySum((spans as [number, number][]).map((b) => b[1])) / spans.length]
  }
  const complete = !un.length && OUTCOMES.every((k) => sub[k] !== null)
  let eci: number | null = null
  if (complete) eci = pySum(OUTCOMES.map((k) => sub[k] as number)) / OUTCOMES.length
  const over = pySum(OUTCOMES.map((k) => (acc[k].max > 0 ? acc[k].weighted / acc[k].max : 0.0))) / OUTCOMES.length
  // category_subindices over the framework's functions
  const buckets = new Map<string, number[]>()
  for (const f of FUNCTIONS) {
    const s = functionScores[f.id]
    if (s !== null && s !== undefined) {
      if (!buckets.has(f.category)) buckets.set(f.category, [])
      buckets.get(f.category)!.push(s / FUNCTION_SCORE_MAX)
    }
  }
  const cats: Record<string, number> = {}
  for (const [cat, v] of buckets) cats[cat] = v.length ? pySum(v) / v.length : 0.0
  const opt2 = (v: number | null) => (v === null ? null : round2(v))
  return {
    functionScores: { ...functionScores },
    subIndices: Object.fromEntries(OUTCOMES.map((k) => [k, opt2(sub[k])])),
    subIndicesRaw: sub,
    outcomes: Object.fromEntries(OUTCOMES.map((k) => {
      const o = acc[k]
      const b = bounds(o)
      return [k, { weighted: round2(o.weighted), max: round2(o.max), direct: o.direct, indirect: o.indirect,
        unassessedDirect: o.unDirect, unassessedIndirect: o.unIndirect, subIndex: opt2(subIndex(o)), bounds: b ? [round2(b[0]), round2(b[1])] : null }]
    })),
    categorySubIndices: Object.fromEntries(Object.entries(cats).map(([k, v]) => [k, round2(v)])),
    categoryLabels: Object.fromEntries(Object.entries(cats).map(([k, v]) => [k, indexBandLabel(v)])),
    ecosystemConditionIndex: opt2(eci),
    ecosystemConditionIndexRaw: eci,
    ecosystemConditionIndexBounds: eciBounds ? [round2(eciBounds[0]), round2(eciBounds[1])] : null,
    subIndicesOverScored: Object.fromEntries(OUTCOMES.map((k) => [k, round2(acc[k].max > 0 ? acc[k].weighted / acc[k].max : 0.0)])),
    subIndicesOverScoredRaw: Object.fromEntries(OUTCOMES.map((k) => [k, acc[k].max > 0 ? acc[k].weighted / acc[k].max : 0.0])),
    ecosystemConditionIndexOverScored: round2(over),
    ecosystemConditionIndexOverScoredRaw: over,
    assessable: eci !== null,
    unassessedFunctions: un,
    nUnassessed: un.length,
    nFunctions: MAPPING.size,
  }
}

/** index_claim: the condition claim the assessment supports, in one line. */
export function indexClaim(result: Scored): string {
  const eci = result.ecosystemConditionIndex
  if (eci !== null && eci !== undefined) return `${pyFixed(eci, 2)} (${indexBandLabel(eci)})`
  const span = result.ecosystemConditionIndexBounds
  const nUn = Math.trunc(Number(result.nUnassessed || 0))
  const nAll = Math.trunc(Number(result.nFunctions || 0))
  const scope = nUn && nAll ? `${nUn} of ${nAll} functions not assessed` : 'coverage incomplete'
  if (!span) return `not determinable, ${scope}`
  const [low, high] = [Number(span[0]), Number(span[1])]
  const band = indexBandForBounds(low, high)
  const tail = band ? ` (${band})` : ', condition not determinable'
  return `${pyFixed(low, 2)} to ${pyFixed(high, 2)}${tail}, ${scope}`
}

// ---------------------------------------------------------------- curves.py, measure.py, models.py
export interface MeasuredValue { value: number | null, na: boolean, origin: string, source: string, note: string, stratum: string | null, engine: boolean }
export type Rec = Record<string, any>

/** measure.measured_from_state */
export function measuredFromState(state: Record<string, Rec>): Record<string, MeasuredValue> {
  const out: Record<string, MeasuredValue> = {}
  for (const [mid, rc0] of Object.entries(state ?? {})) {
    const rc = rc0 ?? {}
    const origin = rc.origin ?? 'field'
    out[mid] = {
      value: rc.value ?? null, na: !!rc.na, note: rc.note ?? '', origin, source: rc.source ?? '', stratum: rc.stratum ?? null,
      engine: !!rc.engine && origin === 'desktop',
    }
  }
  return out
}

const isScored = (m: MeasuredValue | undefined): m is MeasuredValue => !!m && !m.na && m.value !== null && m.value !== undefined
const clamp01 = (y: number) => (y < 0.0 ? 0.0 : y > 1.0 ? 1.0 : y)

type Pt = { x: number, y: number }
function usable(points: Rec[]): Pt[] {
  return (points ?? []).filter((p) => p && p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined)
    .map((p) => ({ x: Number(p.x), y: Number(p.y) }))
}

/** interp_curve: the clamped index of x on a curve (sorted by x, stable). */
export function interpCurve(points: Rec[], x: number): number | null {
  const pts = usable(points).map((p, i) => ({ ...p, i })).sort((a, b) => a.x - b.x || a.i - b.i)
  if (!pts.length) return null
  if (pts.length === 1 || x <= pts[0].x) return clamp01(pts[0].y)
  if (x >= pts[pts.length - 1].x) return clamp01(pts[pts.length - 1].y)
  for (let k = 0; k + 1 < pts.length; k++) {
    const a = pts[k]
    const b = pts[k + 1]
    if (a.x <= x && x <= b.x) {
      const span = b.x - a.x
      if (span <= 0) return clamp01(b.y)
      const t = (x - a.x) / span
      return clamp01(a.y + t * (b.y - a.y))
    }
  }
  return clamp01(pts[pts.length - 1].y)
}

const fmtBound = (v: number) => pyFloat(pyRound(Number(v), 4))

/** domain_warning */
export function domainWarning(points: Rec[], x: number): string | null {
  const xs = usable(points).map((p) => p.x).sort((a, b) => a - b)
  if (xs.length < 2) return null
  const [lo, hi] = [xs[0], xs[xs.length - 1]]
  const xf = Number(x)
  if (xf < lo) return `value ${fmtBound(xf)} is below the curve domain [${fmtBound(lo)}, ${fmtBound(hi)}]; score clamped to the endpoint`
  if (xf > hi) return `value ${fmtBound(xf)} is above the curve domain [${fmtBound(lo)}, ${fmtBound(hi)}]; score clamped to the endpoint`
  return null
}

export const curveStrata = (m: Metric): string[] => (m.curveLayers ?? []).map((l: Rec) => String(l.stratum ?? ''))

/** active_points: the chosen stratum's layer, else the declared active one, else the first; one curve's points. */
export function activePoints(m: Metric, stratum?: string | null): Rec[] {
  const layers = m.curveLayers
  if (layers && layers.length) {
    if (stratum) for (const l of layers) if (String(l.stratum ?? '') === String(stratum)) return l.points ?? []
    if (m.activeStratum) for (const l of layers) if (String(l.stratum ?? '') === String(m.activeStratum)) return l.points ?? []
    return layers[0].points ?? []
  }
  return m.curve?.points ?? []
}

const ENGINE_PAIRING_MODE = 'refuse'

function mismatchedPairing(mv: MeasuredValue | undefined, m: Metric): boolean {
  if (!mv || !mv.engine) return false
  if (String(m?.criteriaBasis ?? '') === 'fixed') return false
  return String(m?.predictorSource || 'streamcat') === 'streamcat'
}

export function enginePairingAdvisory(mv: MeasuredValue | undefined, m: Metric): string | null {
  if (ENGINE_PAIRING_MODE !== 'refuse' || !mismatchedPairing(mv, m)) return null
  return 'engine-computed value shown as reference only: this curve was fitted on StreamCat predictors, so scoring it against an '
    + 'HR reach watershed value would mix training and serving sources'
}

export function metricIndex(mv: MeasuredValue | undefined, m: Metric): number | null {
  if (!isScored(mv)) return null
  if (enginePairingAdvisory(mv, m) !== null) return null
  return interpCurve(activePoints(m, mv.stratum), Number(mv.value))
}

const THIN = ['insufficient', 'too_few']

function sampleAdvisory(m: Metric): string | null {
  const disp = String(m?.sampleDisposition || '').trim().toLowerCase()
  if (!THIN.includes(disp)) return null
  const n = m?.referenceN
  const nTxt = typeof n === 'number' ? `${Math.trunc(n)} reference sites` : 'few reference sites'
  return `built from ${nTxt}, below the exploratory floor: read the condition band, not the point value`
}

function referenceRangeAdvisory(m: Metric, x: number): string | null {
  const rng = m?.referenceRange
  if (!Array.isArray(rng) || rng.length !== 2) return null
  const lo = Number(rng[0])
  const hi = Number(rng[1])
  if (Number.isNaN(lo) || Number.isNaN(hi)) return null
  const xf = Number(x)
  if (xf < lo) return `value ${pyG(xf)} is below the reference pool's range (${pyG(lo)} to ${pyG(hi)}); the score follows the seed's sub-reference convention, not reference data`
  if (xf > hi) return `value ${pyG(xf)} is above the reference pool's range (${pyG(lo)} to ${pyG(hi)}); the score follows the seed's convention beyond the data`
  return null
}

export function metricWarning(mv: MeasuredValue | undefined, m: Metric): string | null {
  if (!isScored(mv)) return null
  const pairing = enginePairingAdvisory(mv, m)
  if (pairing !== null) return pairing
  const points = activePoints(m, mv.stratum)
  const fixed = String(m?.criteriaBasis ?? '') === 'fixed'
  const parts = [null, fixed ? null : domainWarning(points, Number(mv.value)), referenceRangeAdvisory(m, Number(mv.value)), sampleAdvisory(m)]
    .filter(Boolean)
  return parts.length ? parts.join('; ') : null
}

export interface FunctionResult { functionId: string, metricIndices: Record<string, number | null>, score: number | null, na: boolean, metricWarnings: Record<string, string | null> }

function functionIndex(metrics: Metric[], byId: Record<string, MeasuredValue>): [number | null, Record<string, number | null>] {
  const indices: Record<string, number | null> = {}
  const scored: number[] = []
  for (const m of metrics) {
    const idx = metricIndex(byId[m.metricId], m)
    indices[m.metricId] = idx
    if (idx !== null) scored.push(idx)
  }
  if (!scored.length) return [null, indices]
  return [(pySum(scored) / scored.length) * FUNCTION_SCORE_MAX, indices]
}

/** curves.score_site: measured values -> function scores -> the rollup. */
export function scoreSite(bundle: Bundle, measured: Record<string, MeasuredValue>): [Scored, Record<string, FunctionResult>] {
  const scores: Record<string, number> = {}
  const results: Record<string, FunctionResult> = {}
  for (const fn of bundle.metricsByFunction ?? []) {
    const fid = fn.functionId
    const metrics = fn.metrics ?? []
    const [score, indices] = functionIndex(metrics, measured)
    const warnings = Object.fromEntries(metrics.map((m: Metric) => [m.metricId, metricWarning(measured[m.metricId], m)]))
    results[fid] = { functionId: fid, metricIndices: indices, score, na: score === null, metricWarnings: warnings }
    if (score !== null) scores[fid] = score
  }
  return [scoreAssessment(scores, unassessedFunctions(bundle).map((f) => f.functionId)), results]
}

// ---------------------------------------------------------------- assessments.py
export interface Coverage { total: number, covered: number, excluded: number, missing: number, exclusions: Rec[], declared: boolean }

export function coverageOf(bundle: Bundle | null): Coverage {
  const raw = bundle ?? {}
  const covered = (raw.metricsByFunction ?? []).filter((fn: Rec) => fn.metrics && fn.metrics.length).length
  const d = raw.functionCoverage
  if (d && typeof d === 'object' && !Array.isArray(d) && d.total) {
    return { total: Math.trunc(Number(d.total || 0)), covered: Math.trunc(Number(d.covered || covered)), excluded: Math.trunc(Number(d.excluded || 0)),
      missing: Math.trunc(Number(d.missing || 0)), exclusions: d.exclusions || [], declared: true }
  }
  const total = FUNCTIONS.length
  return { total, covered, excluded: 0, missing: Math.max(0, total - covered), exclusions: [], declared: false }
}

const GAP_REASON_WORDS: Record<string, string> = {
  'not-applicable-to-region': 'the function does not operate in this setting',
  'no-suitable-metric': 'no suitable metric measures it here',
  'data-unavailable': 'the data source has no coverage here',
  'direction-unresolved': "the metric's ecological direction is under review",
  'consolidated-into': 'folded into another function',
  'deferred-to-other-tier': 'assessed at another tier instead',
  'insufficient-reference-support': 'insufficient reference support',
}

export function documentedGap(bundle: Bundle | null, functionId: string): Rec | null {
  for (const e of coverageOf(bundle).exclusions ?? []) {
    if (e && typeof e === 'object' && String(e.functionId) === String(functionId ?? '')) return { ...e }
  }
  return null
}

export function gapLine(e: Rec | null): string {
  const x = e ?? {}
  const token = String(x.reason ?? '').trim()
  let words = GAP_REASON_WORDS[token] ?? token.split('-').join(' ')
  if (String(x.reason) === 'consolidated-into' && x.consolidatedInto) words += ` (${x.consolidatedInto})`
  return words ? `Not assessed: ${words}` : 'Not assessed'
}

export function coverageCaption(c: Coverage | null): string {
  if (!c || !c.total) return ''
  const base = `Assessment covers ${c.covered} of ${c.total} STAF functions`
  if (c.covered >= c.total) return base
  if (c.declared) {
    const n = c.excluded || 0
    return `${base} · ${n} documented exclusion${n === 1 ? '' : 's'}`
  }
  return `${base} · coverage not declared`
}

export function predictorSourceOf(bundle: Bundle | null): string {
  const v = (bundle ?? {}).predictorSource
  return v ? String(v) : 'streamcat'
}

export function engineGateByMetric(bundle: Bundle | null): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const fn of (bundle ?? {}).metricsByFunction ?? []) {
    for (const m of fn?.metrics ?? []) {
      if (m?.metricId) out[String(m.metricId)] = String(m.criteriaBasis || '') === 'fixed' || String(m.predictorSource || 'streamcat') !== 'streamcat'
    }
  }
  return out
}

// ---------------------------------------------------------------- reference_support.py
export const isFixed = (m: Metric | null) => String(m?.criteriaBasis || '') === 'fixed'
const HELD_FOR_REVIEW = 'held-for-review'
const RULE_TITLES: Record<string, string> = {
  'high-missingness': 'Withheld: high missingness (DATA-03), not scored',
  'measurement-precision-floor': 'Withheld: measurement-precision floor (CURVE-09), not scored',
}
export const isHeld = (w: Rec) => String(w?.reason || '') === HELD_FOR_REVIEW
export function withheldTitle(w: Rec): string {
  if (isHeld(w)) return 'Held for review, not scored'
  return RULE_TITLES[String(w?.reason || '')] ?? 'Insufficient reference support, not scored'
}

export function withheld(bundle: Bundle | null): Rec[] {
  const items = (bundle ?? {}).insufficientReferenceSupport
  return Array.isArray(items) ? items.filter((w) => w && typeof w === 'object' && !Array.isArray(w)) : []
}

export function withheldForFunction(bundle: Bundle | null, functionId: string): Rec[] {
  const out: Rec[] = []
  for (const w of withheld(bundle)) {
    let fids = (w.functions ?? []).filter((f: Rec) => f && typeof f === 'object').map((f: Rec) => f.functionId)
    if (!fids.length && w.functionId) fids = [w.functionId]
    if (fids.includes(functionId)) out.push(w)
  }
  return out
}

export function withheldOnlyFunctions(bundle: Bundle | null): Rec[] {
  const scored = new Set((bundle?.metricsByFunction ?? []).filter((fn: Rec) => fn.metrics && fn.metrics.length).map((fn: Rec) => fn.functionId))
  const byFn = new Map<string, Rec>()
  for (const w of withheld(bundle)) {
    for (const f of w.functions ?? []) {
      const fid = f?.functionId
      if (!fid || scored.has(fid)) continue
      if (!byFn.has(fid)) byFn.set(fid, { functionId: fid, functionName: f.functionName || fid, metrics: [] })
      byFn.get(fid)!.metrics.push(w)
    }
  }
  return [...byFn.values()]
}

export function unassessedFunctions(bundle: Bundle | null): Rec[] {
  const scored = new Set((bundle?.metricsByFunction ?? []).filter((fn: Rec) => fn.metrics && fn.metrics.length).map((fn: Rec) => fn.functionId))
  const byWithheld = new Map(withheldOnlyFunctions(bundle).map((f) => [f.functionId, f]))
  const out: Rec[] = []
  for (const f of FUNCTIONS) {
    if (scored.has(f.id)) continue
    const known = byWithheld.get(f.id)
    out.push({ functionId: f.id, functionName: f.name || known?.functionName || f.id, discipline: (f as Rec).discipline || f.category || '',
      metrics: known?.metrics ?? [] })
  }
  return out
}

const POOLED_LABEL = 'All streams (pooled)'

export function stratumLabel(label: string | null | undefined, m?: Metric | null): string {
  if (label === null || label === undefined || label === '') return POOLED_LABEL
  const block = m?.stratifier
  if (block && typeof block === 'object') {
    for (const cls of block.classes ?? []) if (String(cls?.key) === String(label) && cls.label) return String(cls.label)
  }
  return String(label)
}

function reachClass(m: Metric | null, delineation: Rec | null): Rec | null {
  const block = m?.stratifier
  const layers = m?.curveLayers ?? []
  if (!block || typeof block !== 'object' || layers.length < 2) return null
  const d = delineation?.delineation ?? delineation ?? {}
  const variable = String(block.variable)
  const raw = ({ nhd_slope: d.slope, drainage_area_sqkm: d.drainage_area_sqkm } as Rec)[variable]
  if (raw === null || raw === undefined || typeof raw === 'boolean') return null
  const value = Number(raw)
  const breaks = (block.breaks ?? []).map(Number)
  if (Number.isNaN(value) || breaks.some(Number.isNaN)) return null
  const right = !!block.right
  if (value < 0 || (right && value <= 0)) return null
  const classes = block.classes ?? []
  let idx = 0
  for (const b of breaks) if (right ? value > b : value >= b) idx += 1
  if (idx >= classes.length) return null
  const cls = classes[idx] ?? {}
  const key = String(cls.key || '')
  const label = String(cls.label || '')
  const have = new Set(layers.map((l: Rec) => String(l.stratum ?? '')))
  const layer = key && have.has(key) ? key : label && have.has(label) ? label : null
  return { key, label: label || key, value, variable, layer, hasCurve: layer !== null }
}

function autoStratum(m: Metric | null, delineation: Rec | null): string | null {
  if (!m?.stratifier || typeof m.stratifier !== 'object' || (m.curveLayers ?? []).length < 2) return null
  const cls = reachClass(m, delineation)
  return cls && cls.hasCurve ? cls.layer : ''
}

/** auto_strata: {metricId: stratum label} for every metric with a stratifier. */
export function autoStrata(bundle: Bundle | null, delineation: Rec | null): Record<string, string> {
  const out: Record<string, string> = {}
  for (const fn of bundle?.metricsByFunction ?? []) {
    for (const m of fn.metrics ?? []) {
      const got = autoStratum(m, delineation)
      if (got !== null && m.metricId) out[String(m.metricId)] = got
    }
  }
  return out
}

/** stratifier_note: why the chooser preselected what it did. */
export function stratifierNote(m: Metric | null, delineation: Rec | null): string {
  if (!m?.stratifier || typeof m.stratifier !== 'object') return ''
  const cls = reachClass(m, delineation)
  if (cls === null) return 'The reach has no NHDPlus value to choose a curve set from, so the pooled curve applies'
  const what = cls.variable === 'nhd_slope' ? `NHDPlus slope (${pyFixed(100 * cls.value, 2)} percent)` : `drainage area (${pyFixedComma(cls.value, 1)} km2)`
  if (cls.hasCurve) return `Chosen from the reach's ${what}`
  return `The reach's ${what} puts it in ${cls.label}, a class with too few reference stations for a curve of its own, so the pooled curve applies`
}

// ---------------------------------------------------------------- describe.py
const PCT_WS = 'Percent of the watershed in '
const NRSA_BENTHIC = 'benthic macroinvertebrate'
const WHAT: Record<string, string> = {
  'agriculture, crop and hay': PCT_WS + 'crops, hay and pasture',
  'bank angle': 'Mean bank angle',
  'bank height ratio': 'Bank height ratio (top of bank height over bankfull depth)',
  'bankfull height above channel': 'Mean bankfull height above the water surface',
  'bankfull width:depth ratio': 'Mean bankfull width over mean bankfull depth',
  'base flow index': 'Percent of streamflow that comes from base flow',
  'benthic macroinvertebrate mmi': `EPA's ${NRSA_BENTHIC} multimetric index (0 to 100)`,
  'benthic shannon diversity': `Shannon diversity of the ${NRSA_BENTHIC} sample`,
  'benthic total taxa richness': `Number of ${NRSA_BENTHIC} taxa`,
  'canopy density, mid-channel': 'Canopy density over the middle of the channel',
  'chlorophyll a': 'Chlorophyll a in the stream water, a measure of algae',
  'cultivated crops': PCT_WS + 'cultivated crops',
  'dam density': 'Dams per square kilometer of watershed',
  'degree of regulation': 'Upstream dam storage as a percent of mean annual runoff',
  'dissolved nitrogen': 'Dissolved nitrogen in the stream water',
  embeddedness: 'Percent of the coarse bed buried in fine sediment',
  'ept taxa richness': 'Number of mayfly, stonefly and caddisfly taxa',
  'fast-water / riffle habitat': 'Percent of the reach in riffles and other fast water',
  'fish mmi': "EPA's fish multimetric index (0 to 100)",
  'herbaceous wetland': PCT_WS + 'herbaceous wetland',
  'impervious surface': PCT_WS + 'impervious surface',
  'large wood volume': 'Volume of large wood per 100 m of channel',
  'mapped dams within one mile': 'Mapped dams within one mile of the site',
  'native fish taxa richness': 'Number of native fish taxa',
  'native non-tolerant fish taxa': 'Percent of native fish taxa that are not pollution tolerant',
  'native non-tolerant fish taxa richness': 'Number of native fish taxa that are not pollution tolerant',
  'natural cover of the riparian corridor': 'Percent natural cover in the 100 m riparian corridor',
  'natural fish cover': 'Areal cover of natural fish cover in the channel',
  ph: 'pH of the stream water',
  'relative bed stability': 'Bed particle size against what bankfull flow can move (log)',
  'residual pool depth': 'Mean residual pool depth',
  'riparian veg cover, woody': 'Woody vegetation cover in the riparian canopy, understory and ground layer',
  'road density': 'Road length per square kilometer of watershed',
  'road-stream crossings': 'Road and stream crossings per square kilometer of watershed',
  'sand + fines': 'Percent of the streambed that is sand or finer',
  'simple lithophil fish individuals': 'Percent of native fish that spawn on clean gravel without building nests',
  sinuosity: 'Channel length over straight line length (sinuosity)',
  'specific conductivity': 'Specific conductance of the stream water',
  'substrate size': 'Mean bed particle size (log mm)',
  'tolerant individuals': `Percent of ${NRSA_BENTHIC} individuals in pollution tolerant taxa`,
  'tolerant native fish individuals': 'Percent of native fish individuals that are pollution tolerant',
  'total nitrogen': 'Total nitrogen in the stream water',
  'total phosphorus': 'Total phosphorus in the stream water',
  turbidity: 'Turbidity of the stream water',
  wetland: PCT_WS + 'woody and herbaceous wetland',
  'woody wetland': PCT_WS + 'woody wetland',
}
const ALIASES: Record<string, string> = {
  xbka: 'bank angle', xbkf_h: 'bankfull height above channel', bfwd_rat: 'bankfull width:depth ratio', hprime: 'benthic shannon diversity',
  "shannon diversity (h')": 'benthic shannon diversity', totlntax: 'benthic total taxa richness', 'total benthic taxa': 'benthic total taxa richness',
  xcdenmid: 'canopy density, mid-channel', chla: 'chlorophyll a', cond: 'specific conductivity', ntl_diss: 'dissolved nitrogen', xembed: 'embeddedness',
  ept_ntax: 'ept taxa richness', pct_fast: 'fast-water / riffle habitat', 'large woody debris volume': 'large wood volume', lwdeqvolm100: 'large wood volume',
  nat_totlntax: 'native fish taxa richness', xfc_nat: 'natural fish cover', lrbs_use: 'relative bed stability', rp100_cm: 'residual pool depth',
  'riparian canopy + ground cover': 'riparian veg cover, woody', xcmgw: 'riparian veg cover, woody', 'sand and fines': 'sand + fines', pct_safn: 'sand + fines',
  sinu: 'sinuosity', lsub_dmm: 'substrate size', 'tolerant individuals (%) ~ hbi': 'tolerant individuals', tolrpind: 'tolerant individuals',
  ptl: 'total phosphorus', turb: 'turbidity', 'wetland cover of the watershed': 'wetland',
}
const EPS = 0.05
/** Python's str.split() then join: runs of whitespace to one space, trimmed. */
const words = (s: string) => s.split(/\s+/).filter(Boolean).join(' ')

function whatOf(m: Metric | null): string {
  let key = words(String(m?.metricName ?? '')).toLowerCase()
  key = ALIASES[key] ?? key
  if (key in WHAT) return WHAT[key]
  const how = words(String(m?.howToMeasure ?? ''))
  if (!how || how.includes(' is scored on ')) return ''
  const first = how.split(/;|\.(?:\s|$)/, 1)[0].trim()
  return first.length > 0 && first.length <= 80 ? first : ''
}

function direction(points: Rec[]): string {
  const pts = usable(points).map((p) => [p.x, p.y]).sort((a, b) => a[0] - b[0] || a[1] - b[1])
  if (pts.length < 2) return ''
  const ys = pts.map((p) => p[1])
  const lo = ys[0]
  const hi = ys[ys.length - 1]
  const top = Math.max(...ys)
  if (top - lo > EPS && top - hi > EPS) return 'both high and low values score lower'
  if (hi - lo > EPS) return 'higher is better'
  if (lo - hi > EPS) return 'higher is worse'
  return ''
}

/** describe.describe: the line under the metric's name. */
export function describe(m: Metric, stratum?: string | null): string {
  const what = whatOf(m)
  if (!what) return ''
  const way = direction(activePoints(m, stratum))
  return way ? `${what}; ${way}.` : `${what}.`
}

// ---------------------------------------------------------------- field_form.py, method_text.py
function firstSentences(text: string, limit = 320): string {
  const t = words(String(text ?? ''))
  if ([...t].length <= limit) return t
  const head = [...t].slice(0, limit).join('')
  const cut = head.lastIndexOf('. ')
  return cut > 60 ? t.slice(0, cut + 1) : head.replace(/\s+$/, '') + '...'
}

/** field_form.method_text: how to measure the metric, in the concise wording where one is recorded. */
export function methodText(m: Metric): string {
  const name = String(m.metricName ?? '')
  for (const key of ['methodContext', 'howToMeasure', 'metricStatement']) {
    const txt = words(String(m[key] ?? ''))
    if (txt && txt !== name) return firstSentences(CONCISE[txt] ?? txt)
  }
  return ''
}

/** field_form.units_of: the units from the curve's x label. */
export function unitsOf(m: Metric): string {
  const label = String(m.xLabel ?? '')
  if (label.endsWith(')') && label.includes('(')) return label.slice(label.lastIndexOf('(') + 1, -1).trim()
  return ''
}

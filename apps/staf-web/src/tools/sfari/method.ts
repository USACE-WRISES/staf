// SFARI's method in the browser: its functions, metrics and outcome mapping (the app's copies of
// apps/sfari/data, parity/copy_page.py), the scoring constants (sfari/config.py), the suggested-Likert
// break tables (likert.py) and the rollup (scoring.py), the same arithmetic in the same order.
import functionsJson from '../../app/content/sfari/sfari-functions.json'
import metricsJson from '../../app/content/sfari/sfari-metrics.json'
import mappingJson from '../../app/content/sfari/sfari-outcome-mapping.json'
import { pyFixed, pySum } from '../../app/pyfmt'

export interface SfariFunction {
  id: string
  category: string
  order: number
  name: string
  functionStatement?: string
  [k: string]: unknown
}

export interface LikertCriterion { likert: string, criteria?: string, exampleObservation?: string }

export interface SfariMetric {
  metricId: string
  functionId: string
  category: string
  name: string
  scale?: string
  metricStatement?: string
  fieldStatement?: string
  likertCriteria?: LikertCriterion[]
  desktopSupportable?: boolean
  desktopSource?: { label?: string, url?: string, [k: string]: unknown } | null
  [k: string]: unknown
}

// ---------------------------------------------------------------- config.py
export const WEIGHTS: Record<string, number> = { D: 1.0, i: 0.1, '-': 0.0 }
export const OUTCOMES = ['physical', 'chemical', 'biological'] as const
export const FUNCTION_SCORE_MAX = 15
export const INDEX_BANDS: [number, string][] = [[0.39, '#f5b5b5'], [0.69, '#f5e7a6'], [1.01, '#c8d9f2']]
export const INDEX_BAND_LABELS = ['Non-Functioning', 'Functioning-at-Risk', 'Functioning']
export const FUNCTION_SCORE_BANDS: [number, string][] = [[5, '#f5b5b5'], [10, '#f5e7a6'], [FUNCTION_SCORE_MAX, '#c8d9f2']]
export const FUNCTION_SCORE_BAND_SHORT = ['NF', 'AR', 'F']
export const LIKERT_ORDER = ['Strongly Agree', 'Agree', 'Neutral', 'Disagree', 'Strongly Disagree']
export const LIKERT_NUMERIC: Record<string, number> = { 'Strongly Agree': 14, Agree: 11, Neutral: 8, Disagree: 5, 'Strongly Disagree': 2 }
export const LIKERT_NA = 'Not Applicable'
export const CATEGORY_ORDER = ['Hydrology', 'Hydraulics', 'Geomorphology', 'Physicochemistry', 'Biology']

export const FUNCTIONS = functionsJson as SfariFunction[]
export const METRICS = (metricsJson as { metrics: SfariMetric[] }).metrics
export const MAPPING: [string, Record<string, string>][] = (mappingJson as Record<string, string>[])
  .map((r) => [r.id, Object.fromEntries(OUTCOMES.map((k) => [k, r[k]]))])

/** The functions in their order (app.py FN_LIST), and by id. */
export const FN_LIST = [...FUNCTIONS].sort((a, b) => a.order - b.order)
export const FN_IDS = FN_LIST.map((f) => f.id)
export const FN_BY_ID = new Map(FN_LIST.map((f) => [f.id, f]))
export const METRICS_BY_FN = new Map<string, SfariMetric[]>()
for (const m of METRICS) {
  if (!METRICS_BY_FN.has(m.functionId)) METRICS_BY_FN.set(m.functionId, [])
  METRICS_BY_FN.get(m.functionId)!.push(m)
}
export const METRICS_BY_ID = new Map(METRICS.map((m) => [m.metricId, m]))
/** config.functions_by_category: each category's functions in their order. */
export const FNS_BY_CAT = new Map<string, SfariFunction[]>()
for (const f of FN_LIST) {
  if (!FNS_BY_CAT.has(f.category)) FNS_BY_CAT.set(f.category, [])
  FNS_BY_CAT.get(f.category)!.push(f)
}

// ---------------------------------------------------------------- likert.py
type Spec = { dir: 'low' | 'high', breaks: [number | null, string][], proxy?: boolean }

export const BREAKS: Record<string, Spec> = {
  'catchment-hydrology-impervious-surface-area': {
    dir: 'low', breaks: [[5, 'Strongly Agree'], [10, 'Agree'], [20, 'Disagree'], [null, 'Strongly Disagree']] },
  'catchment-hydrology-agricultural-cover': {
    dir: 'low', breaks: [[15, 'Strongly Agree'], [25, 'Agree'], [50, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
  'catchment-hydrology-road-density': {
    dir: 'low', breaks: [[0.5, 'Strongly Agree'], [1.0, 'Agree'], [2.0, 'Disagree'], [null, 'Strongly Disagree']] },
  'surface-water-storage-wetland-coverage': {
    dir: 'high', breaks: [[5, 'Strongly Agree'], [2, 'Agree'], [1, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
  'light-thermal-regime-riparian-canopy-cover': {
    dir: 'high', breaks: [[75, 'Strongly Agree'], [50, 'Agree'], [25, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
  'carbon-processing-riparian-corridor-width-and-quality': {
    dir: 'high', breaks: [[60, 'Strongly Agree'], [40, 'Agree'], [20, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
  'nutrient-cycling-vegetated-riparian-corridor-width': {
    dir: 'high', breaks: [[60, 'Strongly Agree'], [40, 'Agree'], [20, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
  'community-dynamics-riparian-communities': {
    dir: 'high', breaks: [[60, 'Strongly Agree'], [40, 'Agree'], [20, 'Disagree'], [null, 'Strongly Disagree']], proxy: true },
}

/** likert.suggest: the suggested Likert for a value by the metric's break table, or null. */
export function suggest(metricId: string, value: unknown): string | null {
  const spec = BREAKS[metricId]
  if (!spec || value === null || value === undefined) return null
  const v = Number(value)
  if (Number.isNaN(v) && typeof value !== 'number') return null
  for (const [bound, likert] of spec.breaks) {
    if (bound === null || (spec.dir === 'low' ? v < bound : v >= bound)) return likert
  }
  return null
}

// ---------------------------------------------------------------- scoring.py

/** Python's round(x) to an integer: half to even. */
export function roundHalfEven(x: number): number {
  const f = Math.floor(x)
  const d = x - f
  if (d > 0.5) return f + 1
  if (d < 0.5) return f
  return f % 2 === 0 ? f : f + 1
}

export const round2 = (v: number): number => roundHalfEven(v * 100) / 100

/** likert_to_score: the mean of the metrics' Likert numbers to one place, or null. */
export function likertToScore(likerts: (string | null | undefined)[]): number | null {
  const vals = likerts.filter((x): x is string => !!x && x in LIKERT_NUMERIC).map((x) => LIKERT_NUMERIC[x])
  if (!vals.length) return null
  let sum = 0
  for (const v of vals) sum += v
  return Number(pyFixed(sum / vals.length, 1))
}

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

export function functionScoreBandLabel(value: number): string {
  for (let i = 0; i < FUNCTION_SCORE_BANDS.length; i++) if (value <= FUNCTION_SCORE_BANDS[i][0]) return FUNCTION_SCORE_BAND_SHORT[i]
  return FUNCTION_SCORE_BAND_SHORT[FUNCTION_SCORE_BAND_SHORT.length - 1]
}

export interface Outcome { weighted: number, max: number, direct: number, indirect: number, subIndex: number }

export interface Scored {
  functionScores: Record<string, number | null>
  subIndices: Record<string, number>
  subIndicesRaw: Record<string, number>
  outcomes: Record<string, Outcome>
  categorySubIndices: Record<string, number>
  categoryLabels: Record<string, string>
  ecosystemConditionIndex: number
  ecosystemConditionIndexRaw: number
}

/** scoring.score_assessment: every function in the mapping counts, a blank score as zero. */
export function scoreAssessment(functionScores: Record<string, number | null>): Scored {
  const acc = Object.fromEntries(OUTCOMES.map((k) => [k, { weighted: 0.0, max: 0.0, direct: 0, indirect: 0 }]))
  for (const [fid, codes] of MAPPING) {
    const raw = functionScores[fid]
    const score = raw === null || raw === undefined ? 0 : raw
    for (const key of OUTCOMES) {
      const code = codes[key] ?? '-'
      const weight = WEIGHTS[code] ?? 0.0
      if (code === 'D') acc[key].direct += 1
      else if (code === 'i') acc[key].indirect += 1
      if (weight) {
        acc[key].weighted += score * weight
        acc[key].max += FUNCTION_SCORE_MAX * weight
      }
    }
  }
  const sub = Object.fromEntries(OUTCOMES.map((k) => [k, acc[k].max > 0 ? acc[k].weighted / acc[k].max : 0.0]))
  const eci = pySum(OUTCOMES.map((k) => sub[k])) / OUTCOMES.length
  // category_subindices: the mean of the scored functions' normalized scores, in the functions' order
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
  return {
    functionScores: { ...functionScores },
    subIndices: Object.fromEntries(OUTCOMES.map((k) => [k, round2(sub[k])])),
    subIndicesRaw: sub,
    outcomes: Object.fromEntries(OUTCOMES.map((k) => [k, {
      weighted: round2(acc[k].weighted), max: round2(acc[k].max), direct: acc[k].direct, indirect: acc[k].indirect, subIndex: round2(sub[k]),
    }])),
    categorySubIndices: Object.fromEntries(Object.entries(cats).map(([k, v]) => [k, round2(v)])),
    categoryLabels: Object.fromEntries(Object.entries(cats).map(([k, v]) => [k, indexBandLabel(v)])),
    ecosystemConditionIndex: round2(eci),
    ecosystemConditionIndexRaw: eci,
  }
}

// EASI's rollup (easi/scoring.py): rating -> index -> function score (0-15) -> outcome sub-indices
// (weights D 1.0, i 0.1) -> the Ecosystem Condition Index (the mean of the available sub-indices), with
// Python's rounding (round half to even) and its compensated sum().
import { pySum } from '../../app/pyfmt'
import { catalog } from './evaluator'
import { cwaMapping, FUNCTION_SCORE_BAND_SHORT, FUNCTION_SCORE_BANDS, FUNCTION_SCORE_MAX, INDEX_BAND_LABELS, INDEX_BANDS,
  OUTCOMES, RATING_INDEX, WEIGHTS } from './method'

/** Python's round(x) for a float: the nearest integer, a tie to the even one. */
export function pyRoundInt(x: number): number {
  const f = Math.floor(x)
  const d = x - f
  if (d > 0.5) return f + 1
  if (d < 0.5) return f
  return f % 2 === 0 ? f : f + 1
}

/** rating_to_index: Good/Fair/Poor -> index, from the metric's midpoints when it has them. */
export function ratingToIndex(rating: string, midpoints?: Record<string, number> | null): number {
  const table = midpoints && Object.keys(midpoints).length ? midpoints : RATING_INDEX
  if (!(rating in table)) throw new Error(`unknown rating '${rating}'`)
  return Number(table[rating])
}

/** function_score: index -> 0-15, rounded and clamped. */
export function functionScore(metricIndex: number): number {
  const value = pyRoundInt(metricIndex * FUNCTION_SCORE_MAX)
  return Math.max(0, Math.min(FUNCTION_SCORE_MAX, Math.trunc(value)))
}

export interface OutcomeResult { weighted: number, max: number, direct: number, indirect: number, subIndex: number | null }

export interface RollupResult {
  functionScores: Record<string, number>
  outcomes: Record<string, OutcomeResult>
  eci: number | null
  subIndices: Record<string, number | null>
  functionsRated: number
  eciInterval: [number, number] | null
}

function accumulate(scores: Record<string, number>, mapping: Record<string, Record<string, string>>, weights: Record<string, number>) {
  const outcomes: Record<string, OutcomeResult> = {}
  for (const key of OUTCOMES) outcomes[key] = { weighted: 0.0, max: 0.0, direct: 0, indirect: 0, subIndex: null }
  for (const [fid, score] of Object.entries(scores)) {
    const codes = mapping[fid]
    if (codes === undefined) continue
    for (const key of OUTCOMES) {
      const code = codes[key] ?? '-'
      const weight = weights[code] ?? 0.0
      if (code === 'D') outcomes[key].direct += 1
      else if (code === 'i') outcomes[key].indirect += 1
      if (weight) {
        outcomes[key].weighted += score * weight
        outcomes[key].max += FUNCTION_SCORE_MAX * weight
      }
    }
  }
  const subIndices: Record<string, number | null> = {}
  for (const key of OUTCOMES) {
    const o = outcomes[key]
    o.subIndex = o.max > 0 ? o.weighted / o.max : null
    subIndices[key] = o.subIndex
  }
  const available = Object.values(subIndices).filter((v): v is number => v !== null)
  const eci = available.length ? pySum(available) / available.length : null
  return { outcomes, subIndices, eci }
}

/** rollup: function scores -> outcome sub-indices and the ECI, with the functions rated and the ECI
 * interval (every unrated function taken as Poor, then as Good). */
export function rollup(functionScores: Record<string, number>, mapping = cwaMapping(), weights = WEIGHTS): RollupResult {
  const { outcomes, subIndices, eci } = accumulate(functionScores, mapping, weights)
  const rated = Object.keys(functionScores).filter((fid) => fid in mapping)
  let interval: [number, number] | null = null
  if (Object.keys(mapping).length) {
    const bounds: (number | null)[] = []
    for (const anchor of [RATING_INDEX.Poor, RATING_INDEX.Good]) {
      const filled: Record<string, number> = { ...functionScores }
      const fill = functionScore(anchor)
      for (const fid of Object.keys(mapping)) if (!(fid in filled)) filled[fid] = fill
      bounds.push(accumulate(filled, mapping, weights).eci)
    }
    if (bounds[0] !== null && bounds[1] !== null) interval = [bounds[0], bounds[1]]
  }
  return { functionScores: { ...functionScores }, outcomes, eci, subIndices, functionsRated: rated.length, eciInterval: interval }
}

/** rollup_reporting_enabled: the catalog's ``rollupReporting`` (no process flag in the browser). */
export const rollupReportingEnabled = (): boolean => Boolean(catalog().rollupReporting)

/** reporting_fields */
export function reportingFields(r: RollupResult): Record<string, any> {
  const i = r.eciInterval
  return { functionsRated: r.functionsRated, ecosystemConditionIndexInterval: i === null ? null : [round2(i[0]), round2(i[1])] }
}

/** round2: STAF display rounding (2 decimals). */
export function round2(value: number | null | undefined): number | null {
  return value === null || value === undefined ? null : pyRoundInt(value * 100) / 100
}

export function indexBandColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return '#d7dce5'
  for (const [threshold, color] of INDEX_BANDS) if (value <= threshold) return color
  return INDEX_BANDS[INDEX_BANDS.length - 1][1]
}

export function indexBandLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Not assessed'
  for (let k = 0; k < INDEX_BANDS.length; k++) if (value <= INDEX_BANDS[k][0]) return INDEX_BAND_LABELS[k]
  return INDEX_BAND_LABELS[INDEX_BAND_LABELS.length - 1]
}

export function functionScoreBandColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return '#d7dce5'
  for (const [threshold, color] of FUNCTION_SCORE_BANDS) if (value <= threshold) return color
  return FUNCTION_SCORE_BANDS[FUNCTION_SCORE_BANDS.length - 1][1]
}

export function functionScoreBandLabel(value: number): string {
  for (let k = 0; k < FUNCTION_SCORE_BANDS.length; k++) if (value <= FUNCTION_SCORE_BANDS[k][0]) return FUNCTION_SCORE_BAND_SHORT[k]
  return FUNCTION_SCORE_BAND_SHORT[FUNCTION_SCORE_BAND_SHORT.length - 1]
}

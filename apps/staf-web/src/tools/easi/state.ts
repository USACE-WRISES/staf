// What one EASI scenario holds and its scores (easi/scenario_state.py), and the scenarios side by side
// (easi/workbook.py comparison and scenario_scores). A scenario changes ratings only: overrides, the
// cross-section (the shown section and its heights) and the observations; the screening is shared.
import { pyClone } from '../../app/pyjson'
import { buildComparison, type Comparison, type ScenarioScores } from '../../shared/scenarios'
import { applyObservedEvidence, rescore } from './assess'
import { functions } from './method'

export interface ScoreState {
  overrides: Record<string, string>
  observed: Record<string, Record<string, any>>
  geomOwned: string[]
  geomText: Record<string, string>
  geomScoring: Record<string, any>
  geomReason: string
  xsSel: number | null
  xsHeights: { bankfull_m: number, lowbank_m: number } | null
}

export const emptyState = (): ScoreState => ({ overrides: {}, observed: {}, geomOwned: [], geomText: {}, geomScoring: {}, geomReason: 'edited',
  xsSel: null, xsHeights: null })

const KINDS: Record<keyof ScoreState, 'object' | 'array' | 'string' | null> = {
  overrides: 'object', observed: 'object', geomOwned: 'array', geomText: 'object', geomScoring: 'object', geomReason: 'string',
  xsSel: null, xsHeights: null,
}
const copy = <T>(x: T): T => pyClone(x)

/** normalized: a copy with every field present and of the right kind. */
export function normalized(state: any): ScoreState {
  const out = emptyState() as any
  if (!state || typeof state !== 'object' || Array.isArray(state)) return out
  for (const [key, kind] of Object.entries(KINDS)) {
    const value = state[key]
    if (value === null || value === undefined) continue
    if (kind === 'object' && (typeof value !== 'object' || Array.isArray(value))) continue
    if (kind === 'array' && !Array.isArray(value)) continue
    if (kind === 'string' && typeof value !== 'string') continue
    out[key] = copy(value)
  }
  if (!['edited', 'scrolled'].includes(out.geomReason)) out.geomReason = 'edited'
  if (out.xsSel !== null) {
    const n = Number.parseInt(String(out.xsSel), 10)
    out.xsSel = Number.isNaN(n) ? null : n
  }
  return out
}

/** The section ``sel`` names (null: the screening's own), with its profile or not (a grid section's
 * profile is drawn once stored or pulled). */
export function xsCandidate(report: Record<string, any> | null | undefined, sel: number | null): Record<string, any> | null {
  const cross = (report || {}).crossSection || {}
  const cands = cross.candidates || []
  if (!cands.length) return cross.geom ?? null
  const def = Math.trunc(Number(cross.selected || 0))
  const idx = sel === null ? def : sel
  return cands[Math.min(Math.max(idx, 0), cands.length - 1)]
}

/** xs_block: the cross-section ``sel`` names (null: the screening's own), as the page shows it. */
export function xsBlock(report: Record<string, any> | null | undefined, sel: number | null): Record<string, any> | null {
  const block = xsCandidate(report, sel)
  return block && block.thalweg !== null && block.thalweg !== undefined ? block : null
}

/** scored_for: one scenario's scored report (the override rescore, the cross-section relabel, then the
 * observations, which must come last). */
export function scoredFor(report: Record<string, any>, state: any): Record<string, any> {
  const st = normalized(state)
  let sc = rescore(report, { ...st.overrides })
  const owned = new Set(st.geomOwned)
  if (owned.size) {
    const scrolled = st.geomReason === 'scrolled'
    const station = (xsCandidate(report, st.xsSel) || {}).label || 'the shown station'
    const where = scrolled ? `cross-section at ${station}` : 'edited cross-section'
    const note = scrolled ? `scored from the section at ${station}, not the reach median` : 'recomputed from your bankfull/floodplain heights'
    for (const row of sc.metricRows) {
      const mid = row.metricId
      if (!owned.has(mid)) continue
      row.status = 'xs-derived'
      row.source = where
      row.valueText = st.geomText[mid] || `from ${where}: ${row.rating}`
      row.note = note
      const trace = st.geomScoring[mid]
      if (trace) {
        row.scoring = trace
        row.generatedRating = trace.generatedRating ?? null
        row.completeness = 'completeness' in trace ? trace.completeness : row.completeness
      }
    }
  }
  if (Object.keys(st.observed).length) sc = applyObservedEvidence(sc, st.observed)
  return sc
}

/** workbook.scenario_scores */
export function scenarioScores(sc: Record<string, any> | null): ScenarioScores {
  if (!sc) return { eci: {}, sub: {}, functions: {} }
  const fs = Object.entries(sc.functionScores || {}).filter(([, s]) => s !== null && s !== undefined) as [string, number][]
  if (!fs.length) return { eci: {}, sub: {}, functions: {} }
  const eci = sc.ecosystemConditionIndexRaw ?? sc.ecosystemConditionIndex ?? null
  const subs = sc.subIndicesRaw || sc.subIndices || {}
  return {
    eci: eci === null ? {} : { value: Number(eci) },
    sub: Object.fromEntries(Object.entries(subs).map(([k, v]) => [k, v === null || v === undefined ? {} : { value: Number(v) }])),
    functions: Object.fromEntries(fs.map(([fid, s]) => [fid, { value: Number(s) }])),
  }
}

/** workbook.comparison: EASI's scenarios side by side, the index to 2 places and the scores whole. */
export function easiComparison(names: string[], reports: Record<string, any>[]): Comparison {
  const fns: [string, string, string][] = functions().map((f) => [f.id, f.name || f.id, f.category ?? ''])
  return buildComparison(names, reports.map(scenarioScores), fns, 2, 0)
}

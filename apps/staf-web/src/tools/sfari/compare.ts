// SFARI's scenarios side by side (sfari/workbook.py comparison and scenario_scores), for the Compare
// dialog and the rail's change line.
import { buildComparison, type Comparison, type ScenarioScores } from '../../shared/scenarios'
import type { FunctionRecord } from './markup'
import { FN_LIST, scoreAssessment } from './method'

/** workbook.scenario_scores: a scenario's scores as the comparison reads them (empty before any score). */
export function scenarioScores(fs: Record<string, FunctionRecord>): ScenarioScores {
  const raw = Object.fromEntries(Object.entries(fs ?? {}).map(([fid, v]) => [fid, v?.score ?? null]))
  const scored = Object.entries(raw).filter(([, s]) => s !== null) as [string, number][]
  if (!scored.length) return { eci: {}, sub: {}, functions: {} }
  const sc = scoreAssessment(raw)
  return {
    eci: { value: sc.ecosystemConditionIndexRaw },
    sub: Object.fromEntries(Object.entries(sc.subIndicesRaw).map(([k, v]) => [k, { value: v }])),
    functions: Object.fromEntries(scored.map(([fid, s]) => [fid, { value: s }])),
  }
}

/** workbook.comparison: SFARI's scenarios side by side, the index to 2 places and the scores whole. */
export function sfariComparison(names: string[], functionScoreSets: Record<string, FunctionRecord>[]): Comparison {
  return buildComparison(names, functionScoreSets.map(scenarioScores), FN_LIST.map((f) => [f.id, f.name, f.category]), 2, 0)
}

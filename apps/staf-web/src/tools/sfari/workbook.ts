// SFARI's workbook (sfari/workbook.py): the calculator's Instructions, the Summary, the calculator once
// per scenario, then the ReferenceCurves tab (files/xlsx, the port of libs/staf_workbook). It opens on
// Instructions (owner, 2026-10-19). Each scenario's tab is a copy of the score sheet with that
// scenario's entries. SFARI rates metrics on a Likert scale and has no numeric curves, so its
// ReferenceCurves tab holds the Likert criteria, the suggestion breakpoints and the Likert scores. The
// blank workbook is a file (forms.ts BLANK_URL), made once by the Shiny app's build_blank.
import { pyG } from '../../app/pyfmt'
import { assemble, type AssemblySpec, type ScenarioInput } from '../../files/xlsx/assemble'
import { type RefCurves, type SummaryCells, summaryCells, tableBlock } from '../../files/xlsx/sheets'
import type { Scenario } from '../../shared/scenarios'
import type { SummaryInfo } from '../../shared/summary'
import { FUNCTION_ROW, fillSheet, inputValues, SHEET_NAME, templateBytes } from './calculator'
import { scenarioScores } from './compare'
import type { FunctionRecord, MetricRecord } from './markup'
import { BREAKS, FN_LIST, LIKERT_NA, LIKERT_NUMERIC, LIKERT_ORDER, METRICS_BY_FN, METRICS_BY_ID } from './method'

/** the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19) */
export const SPEC: AssemblySpec = { appTitle: 'SFARI', scoreSheet: SHEET_NAME, blocks: { Instructions: 'L4:P27' }, forceRecalc: true,
  lead: ['Instructions'] }
const SUB_CELLS: Record<string, string> = { physical: 'C8', chemical: 'C56', biological: 'C72' }

/** functions: [(id, name, category)] in the worksheet's order. */
export const functions = (): [string, string, string][] => FN_LIST.map((f) => [f.id, f.name, f.category])

/** summary_cells */
export function sfariSummaryCells(): SummaryCells {
  const sheet = SHEET_NAME
  return summaryCells({
    eci: [sheet, 'A8'], sub: Object.fromEntries(Object.entries(SUB_CELLS).map(([k, c]) => [k, [sheet, c]])),
    functions: functions().map(([fid, name, cat]) => {
      const row = FUNCTION_ROW.get(fid)
      if (row === undefined) throw new Error(`KeyError: '${fid}'`)
      return [fid, name, cat, [sheet, `H${row}`]]
    }),
    indexDigits: 2, functionDigits: 0,
  })
}

/** reference_curves: the Likert scores, each metric's criteria, and the suggestion breakpoints. */
export function referenceCurves(): RefCurves {
  const order = [...LIKERT_ORDER]
  const scores = tableBlock('Likert scores', ['Likert', 'Score'],
    [...order.map((name) => [name, LIKERT_NUMERIC[name]]), [LIKERT_NA, 'Not counted']], '', [22, 12])
  const rows: unknown[][] = []
  const fnNames = new Map(functions().map(([fid, name]) => [fid, name]))
  for (const [fid] of functions()) {
    for (const m of METRICS_BY_FN.get(fid) ?? []) {
      const crit = new Map<unknown, unknown>()
      for (const c of (m as any).likertCriteria ?? []) {
        if (c && typeof c === 'object' && !Array.isArray(c)) crit.set(c.likert, 'criteria' in c ? c.criteria : '')
      }
      rows.push([fnNames.get(fid), m.name, ...order.map((name) => (crit.has(name) ? crit.get(name) : ''))])
    }
  }
  const criteria = tableBlock('Scoring criteria', ['Function', 'Metric', ...order], rows, '', [22, 26, 24, 24, 24, 24, 24])
  const brk: unknown[][] = []
  for (const [mid, spec] of Object.entries(BREAKS)) {
    const cells = spec.breaks.map(([bound]) => (bound === null ? 'otherwise' : (spec.dir === 'low' ? 'below ' : 'at least ') + pyG(bound)))
    const meta = METRICS_BY_ID.get(mid) as any
    brk.push([meta ? ('name' in meta ? meta.name : mid) : mid, spec.dir === 'low' ? 'Lower is better' : 'Higher is better', ...cells])
  }
  const breaks = tableBlock('Suggested ratings from desktop values', ['Metric', 'Direction', 'Strongly Agree', 'Agree', 'Disagree', 'Strongly Disagree'],
    brk, "Suggestions only: the assessor's Likert rating is what scores.")
  return { intro: 'SFARI rates metrics on a Likert scale; these are its criteria.', blocks: [scores, criteria, breaks] }
}

export interface Entries { metric_scores?: Record<string, MetricRecord>, function_scores?: Record<string, FunctionRecord> }

/** build: the workbook for [(scenario, entries)], Existing Conditions first (``today``: an ISO date). */
export async function buildWorkbook(delin: Record<string, any>, scenarios: [Scenario, Entries][], info: SummaryInfo, today: string,
  template: Uint8Array | null = null): Promise<Uint8Array> {
  const inputs: ScenarioInput[] = []
  const scores = []
  for (const [s, st] of scenarios) {
    const values = inputValues(delin, st.metric_scores ?? {}, st.function_scores ?? {}, today)
    inputs.push({ id: s.id, name: s.name, description: s.description, fill: (xml) => fillSheet(xml, values) })
    scores.push(scenarioScores(st.function_scores ?? {}))
  }
  return assemble(template ?? await templateBytes(), SPEC, inputs, { summary: info, cells: sfariSummaryCells(), scores, curves: referenceCurves(), generatedOn: today })
}

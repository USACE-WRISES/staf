// SFARI's workbook (sfari/workbook.py): the calculator's Instructions, the Summary, then the calculator
// once per scenario, each tab named after its scenario (files/xlsx, the port of libs/staf_workbook). It
// opens on Instructions. The layout is the owner's 2026-10-19 calculator: no ReferenceCurves tab, the
// Summary's site block right under its title with Reach name and Reach length read live from Existing
// Conditions (B2, G2), then a gap. Each scenario's tab is a copy of the score sheet with that scenario's
// entries. The blank workbook is a file (forms.ts BLANK_URL), made once by the Shiny app's build_blank.
import { assemble, type AssemblySpec, type ScenarioInput } from '../../files/xlsx/assemble'
import { type SummaryCells, summaryCells } from '../../files/xlsx/sheets'
import type { Scenario } from '../../shared/scenarios'
import type { SummaryInfo } from '../../shared/summary'
import { FUNCTION_ROW, fillSheet, inputValues, SHEET_NAME, templateBytes } from './calculator'
import { scenarioScores } from './compare'
import type { FunctionRecord, MetricRecord } from './markup'
import { FN_LIST } from './method'

/** the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19) */
export const SPEC: AssemblySpec = { appTitle: 'SFARI', scoreSheet: SHEET_NAME, blocks: { Instructions: 'L4:P27' }, forceRecalc: true,
  lead: ['Instructions'] }
const SUB_CELLS: Record<string, string> = { physical: 'C8', chemical: 'C56', biological: 'C72' }
/** the Summary's rows that read Existing Conditions live (the owner's 2026-10-19 Summary) */
const SITE_CELLS: [string, string][] = [['Reach name', 'B2'], ['Reach length', 'G2']]

/** functions: [(id, name, category)] in the worksheet's order. */
export const functions = (): [string, string, string][] => FN_LIST.map((f) => [f.id, f.name, f.category])

/** summary_cells: where the Summary reads the calculator; ``delin`` (with ``today``) gives the values its
 * live site rows store (what the calculator's B2 and G2 hold for this site). */
export function sfariSummaryCells(delin: Record<string, any> | null = null, today = ''): SummaryCells {
  const sheet = SHEET_NAME
  const stored = delin !== null ? inputValues(delin, {}, {}, today) : new Map()
  return summaryCells({
    eci: [sheet, 'A8'], sub: Object.fromEntries(Object.entries(SUB_CELLS).map(([k, c]) => [k, [sheet, c]])),
    functions: functions().map(([fid, name, cat]) => {
      const row = FUNCTION_ROW.get(fid)
      if (row === undefined) throw new Error(`KeyError: '${fid}'`)
      return [fid, name, cat, [sheet, `H${row}`]]
    }),
    indexDigits: 2, functionDigits: 0,
    siteCells: Object.fromEntries(SITE_CELLS.map(([label, cell]) => [label, [sheet, cell, stored.get(cell) ?? '']])),
    siteFirst: true,
  })
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
  return assemble(template ?? await templateBytes(), SPEC, inputs, { summary: info, cells: sfariSummaryCells(delin, today), scores, curves: null, generatedOn: today })
}

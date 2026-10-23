// EASI's workbook (easi/workbook.py): the calculator's Instructions, the Summary, then the calculator once
// per scenario, each tab named after its scenario (files/xlsx, the port of libs/staf_workbook). It opens
// on Instructions. The layout follows SFARI's (owner, 2026-10-23): no ReferenceCurves tab (the template's
// Reference sheet draws every curve and Metrics shows the Good, Fair and Poor ranges each value was rated
// against), and the Summary's site block right under its title with Reach name and NARS-9 region read
// live from Existing Conditions, then a gap. Each scenario's tab is a copy of the Scenario sheet with that
// scenario's screening typed in (the values the engine rated, the override scores and the notes), and the
// sheets that compute from it are copied too, hidden for the alternatives. The blank workbook is a file
// (calculator.ts BLANK_URL), made once by the Shiny app's build_blank.
import { xmlUnescape } from '../../files/py'
import { assemble, type AssemblySpec, type ScenarioInput } from '../../files/xlsx/assemble'
import { type SummaryCells, summaryCells } from '../../files/xlsx/sheets'
import { readZip } from '../../files/zip'
import type { Scenario } from '../../shared/scenarios'
import { type CellValue, fillSheet, filledValues, SHEET_NAME, templateBytes } from './calculator'
import { functions, metricsById } from './method'
import { summaryInfo } from './report'
import { scenarioScores } from './state'

type Obj = Record<string, any>

/** the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19) */
export const SPEC: AssemblySpec = { appTitle: 'EASI', scoreSheet: SHEET_NAME, forceRecalc: true, lead: ['Instructions'] }
const SUB_NAMES: Record<string, string> = { physical: 'sub_index_physical', chemical: 'sub_index_chemical', biological: 'sub_index_biological' }
const INDEX_DIGITS = 2
const FUNCTION_DIGITS = 0
/** the Summary's rows read live from Existing Conditions: the scenario sheet's Reach ID and NARS-9 region
 * entries (owner, 2026-10-23, as SFARI's Reach name and Reach length) */
const SITE_CELLS: [string, string][] = [['Reach name', 'site_name'], ['NARS-9 region', 'ctx_region']]

/** functions: [(function id, name, category)] in the worksheet's order. */
export const workbookFunctions = (): [string, string, string][] => functions().map((f) => [f.id, f.name || f.id, f.category ?? ''])

/** _named_cells: defined name to (sheet, cell) for the calculator's workbook-level names. */
async function namedCells(template: Uint8Array): Promise<Map<string, [string, string]>> {
  const book = new TextDecoder().decode(await readZip(template).read('xl/workbook.xml'))
  const out = new Map<string, [string, string]>()
  for (const m of book.matchAll(/<definedName\b([^>]*)>([^<]*)<\/definedName>/g)) {
    if (m[1].includes('localSheetId=')) continue
    const name = /\bname="([^"]+)"/.exec(m[1])
    const got = /^'?(.+?)'?!\$?([A-Z]+)\$?(\d+)$/.exec(xmlUnescape(m[2], { '&apos;': "'", '&quot;': '"' }))
    if (name && got) out.set(name[1], [got[1].split("''").join("'"), got[2] + got[3]])
  }
  return out
}

/** summary_cells: the calculator's ECI and outcome cells, each function's score cell (mNN_score, NN its
 * metric's catalog position), and the site rows the Summary reads live; ``stored`` (cell to value, what
 * Existing Conditions holds for this site) gives the values those rows store (none for the blank). */
export async function easiSummaryCells(template: Uint8Array, stored: Map<string, CellValue> = new Map()): Promise<SummaryCells> {
  const names = await namedCells(template)
  const ids = Object.keys(metricsById())
  const position = new Map(ids.map((mid, n) => [mid, n + 1]))
  const metricOf = new Map(Object.entries(metricsById()).map(([mid, m]) => [m.functionId, mid]))
  const fns: [string, string, string, [string, string]][] = []
  for (const [fid, label, cat] of workbookFunctions()) {
    const n = position.get(metricOf.get(fid) as string)
    const key = n ? `m${String(n).padStart(2, '0')}_score` : null
    if (key !== null && names.has(key)) fns.push([fid, label, cat, names.get(key)!])
  }
  const need = (k: string) => {
    const cell = names.get(k)
    if (!cell) throw new Error(`KeyError: '${k}'`)
    return cell
  }
  const siteCells = Object.fromEntries(SITE_CELLS.map(([label, entry]): [string, [string, string, CellValue]] => {
    const [sheet, cell] = need(entry)
    return [label, [sheet, cell, stored.get(cell) ?? '']]
  }))
  return summaryCells({ eci: need('eci'), sub: Object.fromEntries(Object.entries(SUB_NAMES).map(([k, v]) => [k, need(v)])), functions: fns,
    indexDigits: INDEX_DIGITS, functionDigits: FUNCTION_DIGITS, siteCells, siteFirst: true })
}

/** scenario_result: the screening with one scenario's scored report and the shared notes. */
export function scenarioResult(base: Obj | null, report: Obj, notes: Record<string, string>): Obj {
  const rows = (report.metricRows || []).map((r: Obj) => ({ ...r, userNote: (notes || {})[r.metricId] ?? '' }))
  return { ...(base || {}), report: { ...report, metricRows: rows } }
}

/** build: the workbook for [(scenario, scored report)], Existing Conditions first. */
export async function buildWorkbook(base: Obj, scenarios: [Scenario, Obj][], notes: Record<string, string>, today: string): Promise<Uint8Array> {
  const template = await templateBytes()
  const inputs: ScenarioInput[] = []
  const scores = []
  let stored: Map<string, CellValue> | null = null
  for (const [s, report] of scenarios) {
    const [values, notesRef] = await filledValues(scenarioResult(base, report, notes), today)
    stored ??= values
    inputs.push({ id: s.id, name: s.name, description: s.description, fill: (xml) => fillSheet(xml, values, notesRef) })
    scores.push(scenarioScores(report))
  }
  const first = scenarios.length ? scenarios[0][1] : (base || {}).report || {}
  return assemble(template, SPEC, inputs, { summary: summaryInfo({ ...(base || {}), report: first }),
    cells: await easiSummaryCells(template, stored ?? new Map()), scores, curves: null, generatedOn: today })
}

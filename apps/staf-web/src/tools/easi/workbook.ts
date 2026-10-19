// EASI's workbook (easi/workbook.py): the calculator once per scenario, then the Summary and
// ReferenceCurves tabs (files/xlsx, the port of libs/staf_workbook). The calculator stays the adopted
// method's own workbook; each scenario's tab is a copy of its EASI Score sheet with that scenario's
// screening typed in (the values the engine rated, the override scores and the notes), and the sheets
// that compute from it are copied too, hidden for the alternatives. ReferenceCurves shows the curves this
// site was scored against and the rating bands of the rule-based metrics.
import { pyFixedComma } from '../../app/pyfmt'
import { pyCapitalize, xmlUnescape } from '../../files/py'
import { pyStr } from '../../files/xlsx/calcfill'
import { assemble, type AssemblySpec, type ScenarioInput } from '../../files/xlsx/assemble'
import { curveBlock, type RefCurves, type SummaryCells, summaryCells, tableBlock } from '../../files/xlsx/sheets'
import { readZip } from '../../files/zip'
import { NARS9_NAMES } from '../../shared/summary'
import type { Scenario } from '../../shared/scenarios'
import { catalogRows, fillSheet, filledValues, SHEET_NAME } from './calculator'
import { curveSets } from './evaluator'
import { functions, method, metricsById } from './method'
import { summaryInfo } from './report'
import { scenarioScores } from './state'

type Obj = Record<string, any>

/** the workbook opens on the template's Instructions, ahead of Summary (owner, 2026-10-19) */
export const SPEC: AssemblySpec = { appTitle: 'EASI', scoreSheet: SHEET_NAME, forceRecalc: true, lead: ['Instructions'] }
const SUB_NAMES: Record<string, string> = { physical: 'sub_index_physical', chemical: 'sub_index_chemical', biological: 'sub_index_biological' }
const INDEX_DIGITS = 2
const FUNCTION_DIGITS = 0
/** the quantity each curve set scores, as a chart axis reads it */
const CURVE_X: Record<string, string> = { 'flow-variability': 'Monthly flow variability (ratio)', entrenchment: 'Entrenchment ratio',
  'corridor-woody': 'Woody riparian cover (%)', 'corridor-natural': 'Natural riparian cover (%)' }
const SLOPE_TEXT: Record<string, string> = { 'lt_0.5': 'slope under 0.5%', '0.5_to_2': 'slope 0.5 to 2%', ge_2: 'slope 2% or more' }

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

/** summary_cells: the calculator's ECI and outcome cells, and each function's score cell (mNN_score,
 * NN its metric's catalog position). */
export async function easiSummaryCells(template: Uint8Array): Promise<SummaryCells> {
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
  return summaryCells({ eci: need('eci'), sub: Object.fromEntries(Object.entries(SUB_NAMES).map(([k, v]) => [k, need(v)])), functions: fns,
    indexDigits: INDEX_DIGITS, functionDigits: FUNCTION_DIGITS })
}

function stratumText(definition: Obj, stratum: string): string {
  if (stratum === 'national') return 'national curve'
  const kind = definition.stratifier
  if (kind === 'slope_class') return SLOPE_TEXT[stratum] ?? `slope class ${stratum}`
  if (kind === 'nars9') {
    const name = NARS9_NAMES[stratum]
    return `NARS-9 region ${stratum}` + (name ? ` (${name})` : '')
  }
  if (kind === 'l2') return `Level II ecoregion ${stratum}`
  return String(stratum)
}

/** reference_curves: the curve each curve metric was scored against for this site, then the rating
 * bands of the metrics rated by rule (scenarios change ratings, never the curves). */
export function referenceCurves(report: Obj | null): RefCurves {
  const sets = curveSets()
  const blocks: RefCurves['blocks'] = []
  const bands: unknown[][] = []
  for (const [mkey, row] of catalogRows(report || {})) {
    const trace = row.scoring || {}
    const fn = row.functionName || row.name || mkey
    let drawn = false
    for (const [name, used0] of Object.entries((trace.curves || {}) as Obj)) {
      const used = used0 || {}
      const setId = used.set
      const stratum = pyStr(used.stratum)
      const definition = sets[setId] || {}
      const curve = (definition.curves || {})[stratum] || {}
      const points = ((curve.points || []) as unknown[]).filter((p): p is [number, number] => Array.isArray(p) && p.length === 2)
        .map((p) => [Number(p[0]), Number(p[1])] as [number, number])
      if (!points.length) continue
      const n = used.n || curve.n
      let subtitle = `${fn}. ${pyCapitalize(stratumText(definition, stratum))}`
      if (n) subtitle += `, ${pyFixedComma(Math.trunc(Number(n)), 0)} reference reaches`
      const input = name !== 'method' ? ((trace.inputs || []) as Obj[]).find((i) => i.key === name) : undefined
      const label = input ? (input.units ? `${pyStr(input.label)} (${pyStr(input.units)})` : pyStr(input.label)) : null
      blocks.push(curveBlock(row.name || fn, subtitle, label || (CURVE_X[setId] ?? setId), 'Index', [[pyCapitalize(stratumText(definition, stratum)), points]]))
      drawn = true
    }
    if (drawn) continue
    for (const crit of ((row.methodCriteria || {}).automated || []) as Obj[]) {
      const b = crit.bands || {}
      if (Object.keys(b).length) {
        const unit = crit.units
        bands.push([fn, String(crit.label || crit.input || '') + (unit ? ` (${unit})` : ''), b.Good ?? '', b.Fair ?? '', b.Poor ?? ''])
      }
    }
  }
  if (bands.length) blocks.push(tableBlock('Rating bands', ['Function', 'Measure', 'Good', 'Fair', 'Poor'], bands, '', [30, 34, 18, 18, 18]))
  return { intro: 'The reference curves this site was scored against, and the rating bands of the other metrics.', blocks }
}

/** scenario_result: the screening with one scenario's scored report and the shared notes. */
export function scenarioResult(base: Obj | null, report: Obj, notes: Record<string, string>): Obj {
  const rows = (report.metricRows || []).map((r: Obj) => ({ ...r, userNote: (notes || {})[r.metricId] ?? '' }))
  return { ...(base || {}), report: { ...report, metricRows: rows } }
}

/** build: the workbook for [(scenario, scored report)], Existing Conditions first. */
export async function buildWorkbook(base: Obj, scenarios: [Scenario, Obj][], notes: Record<string, string>, today: string): Promise<Uint8Array> {
  const template = method().calculator
  if (!template) throw new Error('No calculator workbook was generated for the active EASI method version.')
  const inputs: ScenarioInput[] = []
  const scores = []
  for (const [s, report] of scenarios) {
    const [values, notesRef] = await filledValues(scenarioResult(base, report, notes), today)
    inputs.push({ id: s.id, name: s.name, description: s.description, fill: (xml) => fillSheet(xml, values, notesRef) })
    scores.push(scenarioScores(report))
  }
  const first = scenarios.length ? scenarios[0][1] : (base || {}).report || {}
  return assemble(template, SPEC, inputs, { summary: summaryInfo({ ...(base || {}), report: first }), cells: await easiSummaryCells(template),
    scores, curves: referenceCurves(first), generatedOn: today })
}

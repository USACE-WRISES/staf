// DEEP's workbook (deep/workbook.py): the loaded version's calculator once per scenario, then the Summary
// and ReferenceCurves tabs (files/xlsx, the port of libs/staf_workbook). Each scenario's tab is a copy of
// its DEEP Score sheet with that scenario's measured values; the sheets that compute from it are copied
// too, hidden for the alternatives. The Summary shows the calculator's own numbers, with the
// application's condition claim for each scenario below them.
import { xmlUnescape } from '../../files/py'
import { assemble, type AssemblySpec, type ScenarioInput } from '../../files/xlsx/assemble'
import { pyStr } from '../../files/xlsx/calcfill'
import { type Banner, curveBlock, type RefCurves, type SummaryCells, summaryCells } from '../../files/xlsx/sheets'
import { lifecycleStatus, STATUS_COLORS, statusLine, statusParts } from '../../app/deepStatus'
import { readZip } from '../../files/zip'
import type { Scenario, ScenarioScores } from '../../shared/scenarios'
import type { SummaryInfo } from '../../shared/summary'
import { fillSheet, filledValues, layerLabel, metricKey, SHEET_NAME } from './calculator'
import { assessmentName } from './forms'
import { type Bundle, indexClaim, measuredFromState, type Rec, type Scored, scoreAssessment, scoreSite } from './method'
import { bookFunctions } from './worksheet'

export const SPEC: AssemblySpec = { appTitle: 'DEEP', scoreSheet: SHEET_NAME, forceRecalc: true }
const SUB_NAMES: Record<string, string> = { physical: 'sub_index_physical', chemical: 'sub_index_chemical', biological: 'sub_index_biological' }
const INDEX_DIGITS = 2
const FUNCTION_DIGITS = 1
const CLAIM_HEADER = 'Condition claim'
const SUMMARY_NOTE = "Index values are the calculator's arithmetic over the functions scored. The condition claim states what the assessment supports."

/** score: the application's score of one scenario's measured values. */
function score(assessment: Bundle | null, measured: Record<string, Rec>): Scored {
  if (!assessment) return scoreAssessment({})
  return scoreSite(assessment, measuredFromState(measured ?? {}))[0]
}

/** workbook_scores: a scenario as its calculator computes it (the Summary's stored results). */
function workbookScores(sc: Scored): ScenarioScores {
  const fs = Object.entries(sc.functionScores ?? {}).filter(([, s]) => s !== null && s !== undefined) as [string, number][]
  if (!fs.length) return { eci: {}, sub: {}, functions: {} }
  return {
    eci: { value: Number((sc as Rec).ecosystemConditionIndexOverScoredRaw || 0.0) },
    sub: Object.fromEntries(Object.entries((sc as Rec).subIndicesOverScoredRaw ?? {}).map(([k, v]) => [k, { value: Number(v) }])),
    functions: Object.fromEntries(fs.map(([fid, s]) => [fid, { value: Number(s) }])),
  }
}

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

/** summary_cells: the calculator's own named cells, and the condition claims listed under the scores. */
async function deepSummaryCells(template: Uint8Array, assessment: Bundle | null, claims: string[]): Promise<SummaryCells> {
  const names = await namedCells(template)
  const need = (k: string) => {
    const cell = names.get(k)
    if (!cell) throw new Error(`KeyError: '${k}'`)
    return cell
  }
  const fns = bookFunctions(assessment).filter(([fid]) => names.has(`fs_${metricKey(fid)}`))
    .map(([fid, label, cat]) => [fid, label, cat, names.get(`fs_${metricKey(fid)}`)!] as [string, string, string, [string, string]])
  return summaryCells({ eci: need('eci'), sub: Object.fromEntries(Object.entries(SUB_NAMES).map(([k, v]) => [k, need(v)])), functions: fns,
    indexDigits: INDEX_DIGITS, functionDigits: FUNCTION_DIGITS, statements: [[CLAIM_HEADER, [...claims]]], note: SUMMARY_NOTE })
}

/** _layers: [(label, stratum, points)]: each curve layer, or the single curve. */
function layers(m: Rec): [string, string | null, Rec[]][] {
  const ls = m.curveLayers || []
  if (ls.length) return ls.map((layer: Rec) => [layerLabel(layer.stratum, m), pyStr(layer.stratum ?? ''), layer.points || []])
  const curve = m.curve || {}
  return [[curve.layerName || 'Reference curve', null, curve.points || []]]
}

/** _used_layer: the curve set a scenario's state uses for the metric. */
function usedLayer(m: Rec, state: Record<string, Rec> | null): string | null {
  const ls: Rec[] = m.curveLayers || []
  if (!ls.length) return null
  const chosen = ((state ?? {})[m.metricId] ?? {}).stratum
  for (const layer of ls) if (chosen !== null && chosen !== undefined && pyStr(layer.stratum ?? '') === pyStr(chosen)) return layerLabel(layer.stratum, m)
  const active = m.activeStratum
  for (const layer of ls) if (active && pyStr(layer.stratum ?? '') === pyStr(active)) return layerLabel(layer.stratum, m)
  return layerLabel(ls[0].stratum, m)
}

/** reference_curves: every scored metric's curve (each layer of a stratified one as its own series),
 * with the curve set each scenario used. */
export function referenceCurves(assessment: Bundle | null, scenarios: [string, Record<string, Rec>][]): RefCurves {
  const blocks: RefCurves['blocks'] = []
  const seen = new Set<string>()
  const byFn = new Map<string, Rec>(((assessment ?? {}).metricsByFunction ?? []).map((fn: Rec) => [fn.functionId, fn]))
  for (const [fid, fname] of bookFunctions(assessment)) {
    for (const m of (byFn.get(fid) ?? {}).metrics ?? []) {
      const mid = m.metricId
      if (!mid || seen.has(mid)) continue
      seen.add(mid)
      const series = layers(m).map(([label, , pts]) => [label, pts.filter((p) => p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined)
        .map((p) => [Number(p.x), Number(p.y)] as [number, number])] as [string, [number, number][]]).filter(([, pts]) => pts.length)
      if (!series.length) continue
      const used = new Map<string, string[]>()
      for (const [name, state] of scenarios) {
        const layer = usedLayer(m, state)
        if (layer !== null) {
          if (!used.has(layer)) used.set(layer, [])
          used.get(layer)!.push(name)
        }
      }
      const subtitle = used.size && series.length > 1
        ? `${fname}. Curve set used: ` + [...used].map(([layer, names]) => `${layer} (${names.join(', ')})`).join('; ') : fname
      blocks.push(curveBlock(m.metricName || mid, subtitle, m.xLabel || '', 'Index', series))
    }
  }
  const name = assessment ? assessmentName(assessment) : ''
  return { intro: name ? `Reference curves of ${name}.` : 'Reference curves of this assessment.', blocks }
}

/** build: the workbook for [(scenario, measured values)], Existing Conditions first, from the loaded
 * version's calculator. */
export async function buildWorkbook(template: Uint8Array, assessment: Bundle | null, delin: Rec, scenarios: [Scenario, Record<string, Rec>][],
  info: SummaryInfo, today: string): Promise<Uint8Array> {
  const inputs: ScenarioInput[] = []
  const bookScores: ScenarioScores[] = []
  const claims: string[] = []
  const used: [string, Record<string, Rec>][] = []
  for (const [s, measured] of scenarios) {
    const [values, replaceable] = await filledValues(template, assessment, measured, delin, today)
    inputs.push({ id: s.id, name: s.name, description: s.description, fill: (xml) => fillSheet(xml, values, replaceable) })
    const sc = score(assessment, measured)
    bookScores.push(workbookScores(sc))
    claims.push(Object.keys(sc.functionScores ?? {}).length ? indexClaim(sc) : '')
    used.push([s.name, measured])
  }
  return assemble(template, SPEC, inputs, { summary: info, cells: await deepSummaryCells(template, assessment, claims), scores: bookScores,
    curves: referenceCurves(assessment, used), generatedOn: today, banner: statusBanner(assessment) })
}

/** workbook.status_banner: the assessment's status across the top of the Summary and ReferenceCurves
 * tabs, as the calculator's own sheets carry it (owner, 2026-10-14). */
export function statusBanner(assessment: Bundle | null): Banner {
  const status = lifecycleStatus(assessment ?? {})
  const [head, note] = statusParts(status)
  return { text: statusLine(status), color: STATUS_COLORS[status][0].replace('#', '').toUpperCase(), header: head + '\n' + note }
}

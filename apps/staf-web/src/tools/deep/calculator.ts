// DEEP's Excel calculator (deep/calculator.py): the workbook StreamCurves published for the loaded
// version (deepAssess.calculatorFor finds it, only when its content digest matches the loaded bundle),
// handed out blank byte for byte, or filled with the worksheet's values: each metric's measured value,
// the curve set in use, and the site block. The workbook recalculates on open, so a completed copy
// shows the application's own indices, function scores and Ecosystem Condition Index.
import { cpLen, cpSlice, pyRstrip, pySqueeze, pyStrip, xmlUnescape } from '../../files/py'
import { excelSerial, numberText, pyStr, sheetPart, xmlText } from '../../files/xlsx/calcfill'
import { readZip, repack } from '../../files/zip'
import { coords } from './forms'
import { type Bundle, curveStrata, enginePairingAdvisory, measuredFromState, type Rec } from './method'
import { stratumLabel } from './support'

export const SHEET_NAME = 'DEEP Score'
const ENTRY_PREFIXES = ['in_', 'st_', 'site_']
const NOTES_NAME = 'site_notes'
const NOTES_LIMIT = 1500
const WHO = 'the DEEP calculator template'

/** metric_key: a metric's defined-name stem (every character outside A-Za-z0-9 an underscore). */
export const metricKey = (metricId: unknown): string => pyStr(metricId).replace(/[^A-Za-z0-9]/g, '_')

/** layer_label: what the workbook's curve-set list shows for a layer. */
export const layerLabel = (stratum: unknown, metric: Rec | null = null): string => stratumLabel(stratum as string, metric)

/** entry_cells: entry name to cell on the DEEP Score sheet, from the workbook's own defined names. */
export async function entryCells(template: Uint8Array): Promise<Record<string, string>> {
  const book = new TextDecoder().decode(await readZip(template).read('xl/workbook.xml'))
  const cells: Record<string, string> = {}
  for (const m of book.matchAll(/<definedName\b[^>]*\bname="([^"]+)"[^>]*>([^<]*)<\/definedName>/g)) {
    if (!ENTRY_PREFIXES.some((p) => m[1].startsWith(p))) continue
    const target = xmlUnescape(m[2], { '&apos;': "'", '&quot;': '"' })
    const at = target.lastIndexOf('!')
    const sheet = at >= 0 ? target.slice(0, at) : ''
    if (sheet.replace(/^'+|'+$/g, '') !== SHEET_NAME) continue
    cells[m[1]] = target.slice(at + 1).split('$').join('')
  }
  if (!(NOTES_NAME in cells)) throw new Error('the DEEP calculator template has no entry cells')
  return cells
}

/** float(x) as Python reads a value (a number, a bool, or a number's text), else null. */
function pyFloatOf(v: unknown): number | null {
  if (typeof v === 'number') return v
  if (typeof v === 'boolean') return v ? 1.0 : 0.0
  if (typeof v !== 'string') return null
  const t = pyStrip(v).replace(/(\d)_(?=\d)/g, '$1')
  if (/^[+-]?(nan|inf|infinity)$/i.test(t)) return /nan/i.test(t) ? NaN : t.startsWith('-') ? -Infinity : Infinity
  return /^[+-]?(\d+\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$/.test(t) ? Number(t) : null
}

/** entries_from_state: the calculator entries for the worksheet's state and what a reader should know
 * about them. A not-applicable or blank metric stays blank; a value the pairing rule keeps out of the
 * score stays blank too and is named in the notes. */
export function entriesFromState(assessment: Bundle | null, measured: Record<string, Rec> | null, delineation: Rec | null): [Record<string, unknown>, string[]] {
  const ms = measured ?? {}
  const objects = measuredFromState(ms)
  const entries: Record<string, unknown> = {}
  const disclosures: string[] = []
  const withheld: string[] = []
  const seen = new Set<string>()
  for (const fn of (assessment ?? {}).metricsByFunction ?? []) {
    for (const m of fn.metrics ?? []) {
      const mid = m.metricId
      if (!mid || seen.has(mid)) continue
      seen.add(mid)
      const rc = ms[mid] ?? {}
      const key = metricKey(mid)
      if (curveStrata(m).length > 1 && rc.stratum !== null && rc.stratum !== undefined) entries[`st_${key}`] = layerLabel(rc.stratum, m)
      const value = rc.value
      if (rc.na || value === null || value === undefined || value === '') continue
      const mv = objects[mid]
      if (mv !== undefined && enginePairingAdvisory(mv, m) !== null) {
        withheld.push(String(m.metricName || mid))
        continue
      }
      const f = pyFloatOf(value)
      if (f === null) continue
      entries[`in_${key}`] = f
    }
  }
  if (withheld.length) disclosures.push('Shown in the application as reference only and left blank here: ' + withheld.join(', ') + '.')
  const dl = (delineation ?? {}).delineation ?? {}
  if (Object.keys(dl).length) {
    entries.site_name = dl.gnis_name || '(unnamed stream)'
    const blank = (v: unknown) => v === null || v === undefined || v === '' || v === 'None'
    if (dl.network === 'nhdplus-hr' && !blank(dl.nhdplus_id)) entries.site_reach = `NHDPlusID ${dl.nhdplus_id}`
    else if (!blank(dl.comid)) entries.site_reach = `COMID ${dl.comid}`
    const c = coords(dl)
    if (c) entries.site_coords = c
  }
  return [entries, disclosures]
}

/** notes_text */
export function notesText(measured: Record<string, Rec> | null, assessment: Bundle | null, disclosures: string[], today: string): string {
  const lines = [`Completed by the DEEP web application on ${today}.`, ...disclosures]
  const names = new Map<unknown, unknown>()
  for (const fn of (assessment ?? {}).metricsByFunction ?? []) {
    for (const m of fn.metrics ?? []) if (!names.has(m.metricId)) names.set(m.metricId, m.metricName || m.metricId)
  }
  const notes: string[] = []
  for (const [mid, rc] of Object.entries(measured ?? {})) {
    if (!rc || typeof rc !== 'object' || Array.isArray(rc)) continue
    const note = rc.note === null || rc.note === undefined || rc.note === '' ? '' : pyStr(rc.note)
    if (!pyStrip(note)) continue
    notes.push(`${names.has(mid) ? pyStr(names.get(mid)) : mid}: ${pySqueeze(pyStr(rc.note))}`)
  }
  if (notes.length) lines.push('Assessor notes. ' + notes.join(' | '))
  const text = lines.join('\n')
  return cpLen(text) <= NOTES_LIMIT ? text : pyRstrip(cpSlice(text, 0, NOTES_LIMIT - 3)) + '...'
}

export type CellValue = string | number

/** _fill_cells: each entry cell named in ``values`` replaced with the same cell carrying it; a curve-set
 * cell (``replaceable``) holds its default, which the value replaces; any other non-empty one fails. */
export function fillSheet(sheetXml: string, values: Map<string, CellValue>, replaceable: Set<string> = new Set()): string {
  const seen = new Set<string>()
  const out = sheetXml.replace(/<c r="([A-Z]+[0-9]+)"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:\/>|>([\s\S]*?)<\/c>)/g,
    (all: string, ref: string, attrs: string, content: string | undefined) => {
      if (!values.has(ref)) return all
      if (content && !replaceable.has(ref)) throw new Error(`DEEP calculator entry cell ${ref} is not empty in the template`)
      seen.add(ref)
      const value = values.get(ref)!
      const a = attrs.replace(/\s+t="[^"]*"/g, '')
      const number = numberText(value)
      if (number !== null) return `<c r="${ref}"${a} t="n"><v>${number}</v></c>`
      return `<c r="${ref}"${a} t="inlineStr"><is><t xml:space="preserve">${xmlText(value)}</t></is></c>`
    })
  const missing = [...values.keys()].filter((r) => !seen.has(r)).sort()
  if (missing.length) throw new Error('the DEEP calculator template does not carry the expected entry cells: ' + missing.join(', '))
  return out
}

/** filled_values: ({cell: value}, the curve-set cells) for the DEEP Score sheet. */
export async function filledValues(template: Uint8Array, assessment: Bundle | null, measured: Record<string, Rec> | null, delineation: Rec | null,
  today: string): Promise<[Map<string, CellValue>, Set<string>]> {
  const cells = await entryCells(template)
  const [entries, disclosures] = entriesFromState(assessment, measured, delineation)
  entries.site_date = excelSerial(today)
  entries[NOTES_NAME] = notesText(measured, assessment, disclosures, today)
  const values = new Map<string, CellValue>()
  for (const [name, raw] of Object.entries(entries)) {
    let value = raw
    if (!(name in cells) || value === null || value === undefined || typeof value === 'boolean') continue
    if (typeof value === 'number') {
      if (numberText(value) === null) continue
    } else {
      value = name === NOTES_NAME ? pyStr(value) : pyStrip(pyStr(value))
      if (!value) continue
    }
    values.set(cells[name], value as CellValue)
  }
  const replaceable = new Set(Object.entries(cells).filter(([name]) => name.startsWith('st_')).map(([, addr]) => addr))
  return [values, replaceable]
}

/** build_filled: the calculator with the worksheet's values typed in (the fallback when the scenario
 * workbook cannot be assembled); only the DEEP Score part changes. */
export async function buildFilled(template: Uint8Array, assessment: Bundle | null, measured: Record<string, Rec> | null, delineation: Rec | null,
  today: string): Promise<Uint8Array> {
  const [values, replaceable] = await filledValues(template, assessment, measured, delineation, today)
  const zin = readZip(template)
  const part = await sheetPart(zin, SHEET_NAME, WHO)
  const sheetXml = new TextDecoder().decode(await zin.read(part))
  return repack(template, new Map([[part, new TextEncoder().encode(fillSheet(sheetXml, values, replaceable))]]))
}

// EASI's Excel calculator (easi/calculator.py): the 2026-10-23 template (page/forms/easi, the app's
// own copy, served for the method files it was generated from), its entry cells (the template's defined
// names), and the entries and disclosures a screening carries into the completed copy
// (entries_from_result), which Get Forms' status line and the desktop metrics PDF list beside the
// workbook. The blank workbook is a file (BLANK_URL), made once by the Shiny app's build_blank.
import { pyFloat } from '../../app/pyfmt'
import { pageFile } from '../../files/download'
import { cpLen, cpSlice, pyRstrip, pySqueeze, pyStrip } from '../../files/py'
import { excelSerial, pyStr, sheetPart, xmlText } from '../../files/xlsx/calcfill'
import { readZip, repack } from '../../files/zip'
import { pyFloatOf } from './evaluator'
import { slopeClass } from './geo'
import { metricsById, PIN } from './method'
import { siteIdentity } from './report'

type Obj = Record<string, any>

/** the calculator's date, which names every download (owner, 2026-10-23, as SFARI's) */
export const TEMPLATE_VERSION = '2026-10-23'
/** the template every workbook is assembled from (calculator.TEMPLATE_PATH) */
export const TEMPLATE_URL = `forms/easi/EASI_Calculator_Template_${TEMPLATE_VERSION}.xlsx`
/** the blank workbook, served as it is (calculator.BLANK_PATH); fetch encodes the space */
export const BLANK_URL = `forms/easi/${TEMPLATE_VERSION}_EASI Calculator.xlsx`
/** both workbooks download under the calculator's date */
export const FILENAME = `${TEMPLATE_VERSION}_EASI Calculator.xlsx`
/** the method files the template was generated from: its "Package digest" document property
 * (tests/easi-calculator.test.ts holds the copied template to it) */
export const TEMPLATE_PACKAGE_DIGEST = 'sha256:7e44b19d4ad8415da0e0216f19c9d03a9e5bd021136439f8c43931e0fc44933d'
export const SHEET_NAME = 'Scenario'
const ENTRY_PREFIXES = ['in_', 'ctx_', 'ov_', 'site_']
export const NOTES_NAME = 'site_notes'
export const NOTES_LABEL = 'Other Metrics/Notes:'
/** the notes box is ten rows tall and a merged cell does not grow */
export const NOTES_LIMIT = 1500
export const NOTES_CONTINUED = ' (continued in the desktop metrics PDF)'
/** keys that name the same quantity under two catalog names */
const ALIASES: Record<string, string> = { chemCat: 'chemCatchment', chemWs: 'chemWatershed' }
/** traced inputs that are context for a rating and have no entry cell of their own */
const CONTEXT_KEYS = new Set(['fcode', 'fcodeContext', 'meanAnnualFlow', 'annualRetreatContext'])
const FCODE_KEYS = ['fcode', 'fcodeContext']
export const VALID = ['Good', 'Fair', 'Poor']

/** calculator.available: the template was generated from the adopted method's files (a workbook is only
 * ever served for the files it was generated from). */
export const available = (): boolean => PIN.packageDigest === TEMPLATE_PACKAGE_DIGEST
/** calculator.blank_filename */
export const blankFilename = (): string => FILENAME
/** calculator.filled_filename: the calculator's date, with no site id */
export const filledFilename = (): string => FILENAME

/** calculator.template_bytes: the template, read once. */
export function templateBytes(): Promise<Uint8Array> {
  if (!available()) return Promise.reject(new Error('No calculator workbook was generated for the active EASI method version.'))
  return pageFile(TEMPLATE_URL)
}

/** Python's truth value of a JSON value. */
export function pyBool(v: unknown): boolean {
  if (v === null || v === undefined) return false
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  if (typeof v === 'string') return v.length > 0
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'object') return Object.keys(v as object).length > 0
  return true
}

/** xml.sax.saxutils.unescape(text, {"&apos;": "'", "&quot;": '"'}) */
export function xmlUnescape(text: string): string {
  return text.split('&lt;').join('<').split('&gt;').join('>').split('&apos;').join("'").split('&quot;').join('"').split('&amp;').join('&')
}

const stripQuotes = (s: string) => s.replace(/^'+|'+$/g, '')

let cellsFor: Promise<Record<string, string>> | null = null

/** calculator.entry_cells: entry name to cell on the Scenario sheet, from the template's defined names
 * (read once). */
export function entryCells(): Promise<Record<string, string>> {
  if (!cellsFor) {
    cellsFor = (async () => {
      const tpl = await templateBytes()
      const book = new TextDecoder().decode(await readZip(tpl).read('xl/workbook.xml'))
      const cells: Record<string, string> = {}
      for (const m of book.matchAll(/<definedName\b[^>]*\bname="([^"]+)"[^>]*>([^<]*)<\/definedName>/g)) {
        const name = m[1]
        if (!ENTRY_PREFIXES.some((p) => name.startsWith(p))) continue
        const target = xmlUnescape(m[2])
        const at = target.lastIndexOf('!')
        const sheet = at >= 0 ? target.slice(0, at) : ''
        if (stripQuotes(sheet) !== SHEET_NAME) throw new Error(`EASI calculator entry ${name} is not on the ${SHEET_NAME} sheet`)
        cells[name] = target.slice(at + 1).split('$').join('')
      }
      if (!(NOTES_NAME in cells)) throw new Error('EASI calculator template has no entry cells')
      return cells
    })()
    cellsFor.catch(() => { cellsFor = null })
  }
  return cellsFor
}

/** _catalog_rows: (mNN, row) in catalog order, the order of the workbook's rows. */
export function catalogRows(report: Obj): [string, Obj][] {
  const byId = new Map<unknown, Obj>()
  for (const row of report.metricRows || []) byId.set(row.metricId, row)
  const out: [string, Obj][] = []
  let n = 0
  for (const mid of Object.keys(metricsById())) {
    n += 1
    const row = byId.get(mid)
    if (row) out.push([`m${String(n).padStart(2, '0')}`, row])
  }
  return out
}

const isReal = (v: unknown): v is number | boolean => typeof v === 'number' || typeof v === 'boolean'

/** str(x) for a JSON value (a whole number as an int's text) */
function pyStrValue(v: unknown): string {
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : pyFloat(v)
  return String(v)
}

/** _number_text: a number the way the sheet XML carries it, or null when it is not one. */
export function numberText(value: unknown): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(Object.is(value, -0) ? 0 : value)
  return pyFloat(value)
}

/** _same_value */
function sameValue(a: unknown, b: unknown): boolean {
  if (isReal(a) && isReal(b)) {
    const x = Number(a)
    const y = Number(b)
    return x === y || (Number.isFinite(x) && Number.isFinite(y) && Math.abs(x - y) <= 1e-12)
  }
  return pyStrValue(a) === pyStrValue(b)
}

/** _text: a traced value for a sentence */
function text(value: unknown): string {
  if (typeof value === 'number') return numberText(value) ?? pyFloat(value)
  return pyStrValue(value)
}

/** _withheld: a row whose method's applicability rule withheld the rating */
export const withheldRow = (row: Obj): boolean => pyBool(((row.scoring || {}).applicability || {}).withheld)

/** entries_from_result: the calculator entries for a screening ({entry name: value}) and what a reader
 * should know about them (the disclosure lines). */
export function entriesFromResult(result: Obj | null, cells: Record<string, string>): [Record<string, unknown>, string[]] {
  const res = result || {}
  const report = res.report || {}
  const entries: Record<string, unknown> = {}
  const origin: Record<string, string> = {}
  const disclosures: string[] = []
  const unknown = new Set<string>()
  let fcode: unknown = null

  const ordered = catalogRows(report)
  const rank: Record<string, number> = { 'xs-derived': 0, override: 2 }
  // stable: rows re-rated from a cross-section first, the computed rows, then the overridden ones
  const forValues = ordered.map((x, i) => [x, i] as const)
    .sort((a, b) => (rank[a[0][1].status] ?? 1) - (rank[b[0][1].status] ?? 1) || a[1] - b[1]).map(([x]) => x)
  for (const [mkey, row] of forValues) {
    const fn = row.functionName || row.name || mkey
    if (withheldRow(row)) continue
    for (const item of (row.scoring || {}).inputs || []) {
      const key = typeof item.key === 'string' && Object.hasOwn(ALIASES, item.key) ? ALIASES[item.key] : item.key
      const value = item.value
      if (key === null || key === undefined || value === null || value === undefined || value === '') continue
      if (FCODE_KEYS.includes(key)) {
        if (fcode === null) fcode = value
        continue
      }
      const name = `in_${key}` in cells ? `in_${key}` : `ov_${key}` in cells ? `ov_${key}` : null
      if (name === null) {
        if (!CONTEXT_KEYS.has(key) && !pyBool(item.contextOnly)) unknown.add(String(item.label || key))
        continue
      }
      if (!(name in entries)) {
        entries[name] = value
        origin[name] = fn
      } else if (!sameValue(entries[name], value)) {
        disclosures.push(`${item.label || key}: the workbook holds ${text(entries[name])} (from ${origin[name]}); `
          + `the application rated ${fn} with ${text(value)}.`)
      }
    }
  }

  // context
  const strata = report.strata || {}
  if (pyBool(strata.nars9)) entries.ctx_region = String(strata.nars9)
  if (fcode === null || fcode === undefined) fcode = ((report.crossSection || {}).geom || {}).fcode ?? null
  if (fcode === null || fcode === undefined) fcode = (res.delineation || {}).fcode ?? null
  const f = typeof fcode === 'number' || typeof fcode === 'string' || typeof fcode === 'boolean' ? pyFloatOf(fcode) : null
  if (f !== null && Number.isFinite(f)) entries.ctx_fcode = Math.trunc(f)
  if ('slope_class' in strata) {
    const used = strata.slope_class || 'national'
    if ((slopeClass(entries.in_slope) || 'national') !== used) entries.ctx_slope_override = used
  }

  // the monthly flow helper: the twelve flows behind the variability entered above
  const flows = res.eromMonthly
  if (Array.isArray(flows) && flows.length === 12 && 'in_flowCv' in entries && flows.every((v) => numberText(v) !== null)) {
    flows.forEach((value, i) => {
      const name = `in_m${String(i + 1).padStart(2, '0')}`
      if (name in cells) entries[name] = value
    })
  }

  // the override scores, and what the application left unrated
  const assessed: string[] = []
  const unrated: string[] = []
  const withheld: string[] = []
  for (const [mkey, row] of ordered) {
    const fn = row.functionName || row.name || mkey
    const rating = row.rating
    if (row.status === 'override' && VALID.includes(rating)) {
      const name = `ov_${mkey}_rating`
      const computed = (row.effectiveOverride || {}).generatedRating || 'not rated'
      if (name in cells) {
        entries[name] = rating
        assessed.push(`${fn} ${rating} (computed ${computed})`)
      } else {
        disclosures.push(`${fn}: the application's rating ${rating} is a user override, and this workbook has no Override Score entry for the function.`)
      }
    } else if (!VALID.includes(rating) && withheldRow(row)) {
      const reason = String((row.scoring || {}).statement || '').trim().replace(/\.+$/, '')
      withheld.push(reason ? `${fn} (${reason})` : fn)
    } else if (!VALID.includes(rating) && row.status !== 'excluded') {
      unrated.push(fn)
    }
  }
  if (assessed.length) disclosures.unshift('Override scores: ' + assessed.join('; ') + '.')
  if (withheld.length) {
    disclosures.push('Withheld by the application: ' + withheld.join('; ') + '. Their values are not entered; a measured value typed into the workbook rates them.')
  }
  if (unrated.length) disclosures.push('Not rated by the application: ' + unrated.join(', ') + '. The workbook rates a function whenever its entries are present.')
  if (unknown.size) disclosures.push('Not entered (no cell in this workbook): ' + [...unknown].sort().join(', ') + '.')

  // the site block
  const site = siteIdentity(res)
  if (site.label) entries.site_name = site.label
  if (site.comid) entries.site_comid = site.comid
  if (site.coords) entries.site_coords = site.coords
  return [entries, disclosures]
}

// ---------------------------------------------------------------- the completed workbook

/** notes_text: the notes box: the template's label, where the entries came from, and the assessor's
 * notes (cut to what the merged box can show; the desktop metrics PDF carries the rest). */
export function notesText(result: Obj | null, disclosures: string[], today: string): string {
  const report = (result || {}).report || {}
  const site = siteIdentity(result || {})
  const where = site.label ? ' from the screening of ' + site.label : ''
  const lines = [NOTES_LABEL, `Completed by the EASI web application on ${today}${where}.`, ...disclosures]
  const notes = catalogRows(report).filter(([, row]) => pyStrip(String(row.userNote ?? '')) !== '' && pyBool(row.userNote))
    .map(([mkey, row]) => `${row.functionName || mkey}: ${pySqueeze(String(row.userNote))}`)
  if (notes.length) lines.push('Assessor notes. ' + notes.join(' | '))
  let text = lines.join('\n')
  if (cpLen(text) > NOTES_LIMIT) text = pyRstrip(cpSlice(text, 0, NOTES_LIMIT - cpLen(NOTES_CONTINUED))) + NOTES_CONTINUED
  return text
}

export type CellValue = string | number

/** filled_values: ({cell: value}, the notes cell) for the Scenario sheet; every scenario's copy of the
 * sheet has the same cells. */
export async function filledValues(result: Obj | null, today: string): Promise<[Map<string, CellValue>, string]> {
  const cells = await entryCells()
  const [entries, disclosures] = entriesFromResult(result, cells)
  entries.site_date = excelSerial(today)
  entries[NOTES_NAME] = notesText(result, disclosures, today)
  const values = new Map<string, CellValue>()
  for (const [name, raw] of Object.entries(entries)) {
    let value = raw
    if (!(name in cells) || value === null || value === undefined || typeof value === 'boolean') continue
    if (typeof value === 'number') {
      if (numberText(value) === null) continue      // not finite: unknown, so the cell stays blank
    } else {
      value = name === NOTES_NAME ? pyStr(value) : pyStrip(pyStr(value))
      if (name === 'in_category') value = (value as string).toUpperCase().split(' ').join('')
      if (!value) continue
    }
    values.set(cells[name], value as CellValue)
  }
  return [values, cells[NOTES_NAME]]
}

/** _fill_cells: each entry cell named in ``values`` replaced with the same cell carrying it; a cell
 * that moved or already holds something fails here (the notes cell keeps its label as its first line). */
export function fillSheet(sheetXml: string, values: Map<string, CellValue>, notesRef: string | null = null): string {
  const seen = new Set<string>()
  const label = xmlText(NOTES_LABEL)
  const out = sheetXml.replace(/<c r="([A-Z]+[0-9]+)"((?:\s+[A-Za-z:]+="[^"]*")*)\s*(?:\/>|>([\s\S]*?)<\/c>)/g,
    (all: string, ref: string, attrs: string, content: string | undefined) => {
      if (!values.has(ref)) return all
      if (content && !(ref === notesRef && content.includes(label))) throw new Error(`EASI calculator entry cell ${ref} is not empty in the template`)
      seen.add(ref)
      const value = values.get(ref)!
      const a = attrs.replace(/\s+t="[^"]*"/g, '')
      const number = numberText(value)
      if (number !== null) return `<c r="${ref}"${a} t="n"><v>${number}</v></c>`
      return `<c r="${ref}"${a} t="inlineStr"><is><t xml:space="preserve">${xmlText(value)}</t></is></c>`
    })
  const missing = [...values.keys()].filter((r) => !seen.has(r)).sort()
  if (missing.length) throw new Error('EASI calculator template does not carry the expected entry cells: ' + missing.join(', '))
  return out
}

/** build_filled: the template with this screening typed into its entry cells (the fallback when the
 * scenario workbook cannot be assembled); only the Scenario part changes. */
export async function buildFilled(result: Obj, today: string): Promise<Uint8Array> {
  const [values, notesRef] = await filledValues(result, today)
  const source = await templateBytes()
  const zin = readZip(source)
  const part = await sheetPart(zin, SHEET_NAME, 'EASI calculator template')
  const sheetXml = new TextDecoder().decode(await zin.read(part))
  return repack(source, new Map([[part, new TextEncoder().encode(fillSheet(sheetXml, values, notesRef))]]))
}

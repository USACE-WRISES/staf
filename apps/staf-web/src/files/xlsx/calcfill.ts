// What the three tools' calculator.py modules share for typing values into a template's entry cells:
// text for an XML text node, a day as an Excel serial, the worksheet part of a sheet name, and the
// workbook's recalculation on open.
import { pyFloat } from '../../app/pyfmt'
import { reEscape, xmlUnescape } from '../py'
import type { ZipArchive } from '../zip'

const ILLEGAL_XML = /[\x00-\x08\x0b\x0c\x0e-\x1f]/g

/** str(value) for the values the calculators write (a whole number without a decimal point) */
export function pyStr(value: unknown): string {
  if (value === null || value === undefined) return 'None'
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : pyFloat(value)
  return String(value)
}

/** _xml_text: escaped for an XML text node, including what Excel's own escape covers (a literal
 * _xHHHH_ is escaped, or Excel would read it as another character). */
export function xmlText(value: unknown): string {
  let text = pyStr(value).replace(ILLEGAL_XML, '')
  text = text.replace(/_(x[0-9A-Fa-f]{4}_)/g, '_x005F_$1')
  return text.split('&').join('&amp;').split('<').join('&lt;').split('>').join('&gt;')
}

/** _number_text: a number the way the sheet XML carries it, or null when it is not one. */
export function numberText(value: unknown): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(Object.is(value, -0) ? 0 : value)
  return pyFloat(value)
}

/** The local day as date.today().isoformat() writes it. */
export function todayIso(now = new Date()): string {
  return `${String(now.getFullYear()).padStart(4, '0')}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

/** _excel_serial: days from 1899-12-30 (Excel's day zero for the 1900 date system). */
export function excelSerial(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(1899, 11, 30)) / 86_400_000)
}

/** _sheet_part: the worksheet part for a sheet name, resolved through the workbook's relationships. */
export async function sheetPart(archive: ZipArchive, name: string, who: string): Promise<string> {
  const book = new TextDecoder().decode(await archive.read('xl/workbook.xml'))
  let rid: string | null = null
  for (const m of book.matchAll(/<sheet\b[^>]*\/>/g)) {
    const got = /name="([^"]*)"/.exec(m[0])
    if (got && xmlUnescape(got[1], { '&apos;': "'", '&quot;': '"' }) === name) {
      const found = /r:id="([^"]*)"/.exec(m[0])
      rid = found ? found[1] : null
      break
    }
  }
  if (!rid) throw new Error(`${who} has no sheet named '${name}'`)
  const rels = new TextDecoder().decode(await archive.read('xl/_rels/workbook.xml.rels'))
  let target: string | null = null
  for (const m of rels.matchAll(/<Relationship\b[^>]*\/>/g)) {
    if (new RegExp(`\\bId="${reEscape(rid)}"`).test(m[0])) {
      const found = /\bTarget="([^"]*)"/.exec(m[0])
      target = found ? found[1] : null
      break
    }
  }
  if (!target) throw new Error(`${who} has no relationship '${rid}'`)
  const part = target.replace(/^\/+/, '')
  return part.startsWith('xl/') ? part : `xl/${part}`
}

/** _force_recalc (SFARI): Excel recalculates on open (calcId zeroed, fullCalcOnLoad set). */
export function forceRecalc(bookXml: string, who: string): string {
  const repl = (_all: string, attrs: string) => `<calcPr${attrs.replace(/\s*calcId="[^"]*"/g, '')} calcId="0" fullCalcOnLoad="1"/>`
  let out = bookXml.replace(/<calcPr\b([^>]*?)\/>/, repl)
  if (out === bookXml && !/<calcPr\b([^>]*?)\/>/.test(bookXml)) {
    out = bookXml.replace(/<calcPr\b([^>]*?)>[\s\S]*?<\/calcPr>/, repl)
    if (out === bookXml && !/<calcPr\b([^>]*?)>[\s\S]*?<\/calcPr>/.test(bookXml)) throw new Error(`${who} has no calcPr to set recalculation on`)
  }
  return out
}

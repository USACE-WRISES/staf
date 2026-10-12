// Sheet references inside formulas, found by spans and rewritten in place (libs/staf_workbook
// xlsx/formula.py). Only the Sheet! prefixes a formula carries are touched; every other character is
// copied unchanged. String and error literals are skipped. What the scanner does not recognise
// (external or structured references, 3-D references) raises UnsupportedFormula, so a workbook is
// refused rather than guessed at.
import { isAlnum, isAlpha, isDigit } from '../py'

const IDENT = /[A-Za-z_\\À-￿][A-Za-z0-9_.À-￿]*/y
const ERRORS = /#(?:NULL!|DIV\/0!|VALUE!|REF!|NAME\?|NUM!|N\/A|GETTING_DATA|SPILL!|CALC!|FIELD!|BLOCKED!|CONNECT!|BUSY!|UNKNOWN!)/iy
const PLAIN_SHEET = /^[A-Za-z_][A-Za-z0-9_.]*$/
const LOOKS_LIKE_REF = /^(?:[A-Za-z]{1,3}\d+|[Rr]\d*[Cc]\d*|[Rr]\d*|[Cc]\d*|TRUE|FALSE)$/i

/** A formula construct the rewriter does not handle. */
export class UnsupportedFormula extends Error {}

export interface Span { start: number, end: number, sheet: string, quoted: boolean }

/** _IDENT.match(f, i).end(), as Python's would fail on no match. */
function identEnd(f: string, i: number): number {
  IDENT.lastIndex = i
  const m = IDENT.exec(f)
  if (!m) throw new Error("AttributeError: 'NoneType' object has no attribute 'end'")
  return i + m[0].length
}

function identMatch(f: string, i: number): number | null {
  IDENT.lastIndex = i
  const m = IDENT.exec(f)
  return m ? i + m[0].length : null
}

const startsIdent = (c: string) => isAlpha(c) || c === '_' || c === '\\' || c.charCodeAt(0) >= 0xc0

/** sheet_spans: the Sheet! prefixes in ``formula``. */
export function sheetSpans(formula: string): Span[] {
  const f = formula
  const n = f.length
  let i = 0
  const out: Span[] = []
  while (i < n) {
    const c = f[i]
    if (c === '"') {                                  // a string literal, "" escapes
      i += 1
      while (i < n) {
        if (f[i] === '"') {
          if (i + 1 < n && f[i + 1] === '"') {
            i += 2
            continue
          }
          i += 1
          break
        }
        i += 1
      }
      continue
    }
    if (c === "'") {                                  // a quoted sheet name, '' escapes
      let j = i + 1
      const name: string[] = []
      while (j < n) {
        if (f[j] === "'") {
          if (j + 1 < n && f[j + 1] === "'") {
            name.push("'")
            j += 2
            continue
          }
          break
        }
        name.push(f[j])
        j += 1
      }
      if (j + 1 >= n || f[j + 1] !== '!') throw new UnsupportedFormula(`quoted text that is not a sheet reference: ${formula}`)
      const sheet = name.join('')
      if (sheet.includes(':')) throw new UnsupportedFormula(`3-D reference: ${formula}`)
      out.push({ start: i, end: j + 2, sheet, quoted: true })
      i = j + 2
      continue
    }
    if (c === '[') throw new UnsupportedFormula(`external or structured reference: ${formula}`)
    if (c === '#') {
      ERRORS.lastIndex = i
      const m = ERRORS.exec(f)
      i += m ? m[0].length : 1
      continue
    }
    if (isDigit(c)) {                                 // a number or a row: never a sheet name
      while (i < n && (isAlnum(f[i]) || f[i] === '.' || f[i] === '_')) i += 1
      continue
    }
    if (startsIdent(c)) {
      const end = identEnd(f, i)
      if (end < n && f[end] === '!') {
        out.push({ start: i, end: end + 1, sheet: f.slice(i, end), quoted: false })
        i = end + 1
        continue
      }
      if (end < n && f[end] === ':') {
        const e2 = identMatch(f, end + 1)
        if (e2 !== null && e2 < n && f[e2] === '!') throw new UnsupportedFormula(`3-D reference: ${formula}`)
      }
      i = end
      continue
    }
    i += 1
  }
  return out
}

/** identifiers: bare identifiers that could be defined names (lower-case): not a sheet prefix, not a
 * function call, not inside a string or a quoted sheet name. */
export function identifiers(formula: string): Set<string> {
  const f = formula
  const n = f.length
  let i = 0
  const out = new Set<string>()
  while (i < n) {
    const c = f[i]
    if (c === '"') {
      i += 1
      while (i < n) {
        if (f[i] === '"') {
          if (i + 1 < n && f[i + 1] === '"') {
            i += 2
            continue
          }
          i += 1
          break
        }
        i += 1
      }
      continue
    }
    if (c === "'") {
      let j = i + 1
      while (j < n) {
        if (f[j] === "'") {
          if (j + 1 < n && f[j + 1] === "'") {
            j += 2
            continue
          }
          break
        }
        j += 1
      }
      i = j + 2
      continue
    }
    if (isDigit(c)) {
      while (i < n && (isAlnum(f[i]) || f[i] === '.' || f[i] === '_')) i += 1
      continue
    }
    if (startsIdent(c)) {
      const end = identEnd(f, i)
      const nxt = end < n ? f[end] : ''
      const prev = i > 0 ? f[i - 1] : ''
      if (nxt !== '!' && nxt !== '(' && prev !== '!') out.add(f.slice(i, end).toLowerCase())
      i = end
      continue
    }
    i += 1
  }
  return out
}

/** quote_sheet: a sheet name as it must appear before "!" in a formula. */
export function quoteSheet(name: string): string {
  if (PLAIN_SHEET.test(name) && !LOOKS_LIKE_REF.test(name)) return name
  return "'" + name.split("'").join("''") + "'"
}

export function referencedSheets(formula: string): Set<string> {
  return new Set(sheetSpans(formula).map((s) => s.sheet.toLowerCase()))
}

/** rewrite: ``formula`` with every reference to a sheet in ``mapping`` (lower-case keys) renamed. */
export function rewrite(formula: string, mapping: Map<string, string>): string {
  const spans = sheetSpans(formula)
  if (!spans.length) return formula
  const out: string[] = []
  let last = 0
  for (const s of spans) {
    const next = mapping.get(s.sheet.toLowerCase())
    if (next === undefined) continue
    out.push(formula.slice(last, s.start))
    // keep the template's style: a quoted reference stays quoted
    out.push((s.quoted ? "'" + next.split("'").join("''") + "'" : quoteSheet(next)) + '!')
    last = s.end
  }
  if (last === 0) return formula
  out.push(formula.slice(last))
  return out.join('')
}

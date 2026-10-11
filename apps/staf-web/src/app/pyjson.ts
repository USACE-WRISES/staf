// JSON as Python writes it (json.dumps with its defaults), for text the Shiny tools put on the page.
//
// A JavaScript number does not remember whether Python wrote it as 5 or 5.0, and json.dumps of a value
// read back from a file writes it as the file had it. parseJson keeps each number's source text (the
// JSON.parse source-text access of current browsers), and pyDumps writes that text back; without it a
// whole number is written as an int.
import { pyFloat } from './pyfmt'

const RAW = new WeakMap<object, Record<string, string>>()

/** JSON.parse that remembers how each number was written. */
export function parseJson(text: string): any {
  return JSON.parse(text, function (this: Record<string, unknown>, key: string, value: unknown, ctx?: { source?: string }) {
    if (typeof value === 'number' && ctx && typeof ctx.source === 'string' && this && typeof this === 'object') {
      let m = RAW.get(this)
      if (!m) {
        m = {}
        RAW.set(this, m)
      }
      m[key] = ctx.source
    }
    return value
  } as (this: any, key: string, value: any) => any)
}

function pyString(s: string): string {
  let out = '"'
  for (const ch of s) {
    const c = ch.codePointAt(0)!
    if (ch === '"') out += '\\"'
    else if (ch === '\\') out += '\\\\'
    else if (ch === '\n') out += '\\n'
    else if (ch === '\r') out += '\\r'
    else if (ch === '\t') out += '\\t'
    else if (ch === '\b') out += '\\b'
    else if (ch === '\f') out += '\\f'
    else if (c < 0x20 || (c >= 0x7f && c <= 0xffff)) out += '\\u' + c.toString(16).padStart(4, '0')
    else if (c > 0xffff) {
      const v = c - 0x10000
      out += '\\u' + (0xd800 + (v >> 10)).toString(16) + '\\u' + (0xdc00 + (v & 0x3ff)).toString(16)
    } else out += ch
  }
  return out + '"'
}

function pyNumber(v: number, raw?: string): string {
  if (raw !== undefined) return raw
  if (Number.isNaN(v)) return 'NaN'
  if (!Number.isFinite(v)) return v > 0 ? 'Infinity' : '-Infinity'
  return Number.isInteger(v) ? String(v) : pyFloat(v)
}

/** json.dumps(value) with Python's defaults: ", " and ": " separators, ASCII only, keys in order. */
export function pyDumps(value: unknown, raw?: string): string {
  if (value === null || value === undefined) return 'null'
  if (value === true) return 'true'
  if (value === false) return 'false'
  if (typeof value === 'number') return pyNumber(value, raw)
  if (typeof value === 'string') return pyString(value)
  if (Array.isArray(value)) {
    const texts = RAW.get(value)
    return '[' + value.map((v, i) => pyDumps(v, texts?.[String(i)])).join(', ') + ']'
  }
  const obj = value as Record<string, unknown>
  const texts = RAW.get(obj)
  return '{' + Object.entries(obj).map(([k, v]) => `${pyString(k)}: ${pyDumps(v, texts?.[k])}`).join(', ') + '}'
}

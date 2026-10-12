// JSON as Python writes it (json.dumps with its defaults), for text the Shiny tools put on the page.
//
// A JavaScript number does not remember whether Python wrote it as 5 or 5.0, and json.dumps of a value
// read back from a file writes it as the file had it. parseJson keeps each number's source text (the
// JSON.parse source-text access of current browsers), and pyDumps writes that text back; without it a
// whole number is written as an int.
//
// A JavaScript object also lists keys that look like array indexes ("1", "5") first, in numeric order,
// where a Python dict keeps the file's order ("1", "4A", "5"). With ``order``, parseJson records the
// file's key order of each object that has such a key, and pyEntries and pyDumps follow it.
import { pyFloat } from './pyfmt'

const RAW = new WeakMap<object, Record<string, string>>()
const ORDER = new WeakMap<object, string[]>()
const INDEX_KEY = /^(0|[1-9][0-9]*)$/
const BACKSLASH = 92
const QUOTE = 34

/** Each object's keys in the file's order, objects in document (pre-)order. */
function keyOrders(text: string): string[][] {
  const out: string[][] = []
  const stack: { keys: string[] | null, expectKey: boolean }[] = []
  let i = 0
  const n = text.length
  while (i < n) {
    const c = text[i]
    if (c === '{') {
      const keys: string[] = []
      out.push(keys)
      stack.push({ keys, expectKey: true })
      i++
    } else if (c === '[') {
      stack.push({ keys: null, expectKey: false })
      i++
    } else if (c === '}' || c === ']') {
      stack.pop()
      i++
    } else if (c === ',') {
      const top = stack[stack.length - 1]
      if (top && top.keys) top.expectKey = true
      i++
    } else if (text.charCodeAt(i) === QUOTE) {
      let j = i + 1
      while (j < n && text.charCodeAt(j) !== QUOTE) j += text.charCodeAt(j) === BACKSLASH ? 2 : 1
      const top = stack[stack.length - 1]
      if (top && top.keys && top.expectKey) {
        top.keys.push(JSON.parse(text.slice(i, j + 1)))
        top.expectKey = false
      }
      i = j + 1
    } else i++
  }
  return out
}

function attachOrder(value: unknown, orders: string[][], at: { k: number }): void {
  if (Array.isArray(value)) {
    for (const v of value) attachOrder(v, orders, at)
    return
  }
  if (value === null || typeof value !== 'object') return
  const keys = orders[at.k++]
  if (keys.some((k) => INDEX_KEY.test(k))) ORDER.set(value, keys)
  for (const k of keys) attachOrder((value as Record<string, unknown>)[k], orders, at)
}

/** JSON.parse that remembers how each number was written (and, with ``order``, the file's key order). */
export function parseJson(text: string, { order = false } = {}): any {
  const value = JSON.parse(text, function (this: Record<string, unknown>, key: string, value: unknown, ctx?: { source?: string }) {
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
  if (order) attachOrder(value, keyOrders(text), { k: 0 })
  return value
}

/** dict.items(): an object's entries in the file's order where parseJson recorded it. */
export function pyEntries<T>(obj: Record<string, T>): [string, T][] {
  const keys = ORDER.get(obj)
  return keys ? keys.map((k) => [k, obj[k]] as [string, T]) : Object.entries(obj)
}

/** dict.values() in the file's order */
export const pyValues = <T>(obj: Record<string, T>): T[] => pyEntries(obj).map(([, v]) => v)

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
  return '{' + pyEntries(obj).map(([k, v]) => `${pyString(k)}: ${pyDumps(v, texts?.[k])}`).join(', ') + '}'
}

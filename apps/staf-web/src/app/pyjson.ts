// JSON as Python reads and writes it (json.loads, json.dumps), for the text the Shiny tools put on the
// page and the files they write.
//
// A JavaScript number does not say whether Python held it as an int (5) or a float (5.0), and json.dumps
// writes the two differently. parseJson notes which numbers a file wrote as floats (a fraction or an
// exponent, NaN, Infinity), and pyDumps writes those as Python's repr does; a number made here is a float
// when it is not whole, or when it is marked (markFloat) or its place matches a float pattern (``floats``
// of pyDumps), and an int otherwise.
//
// A JavaScript object also lists keys that look like array indexes ("1", "5") first, in numeric order,
// where a Python dict keeps the file's order ("1", "4A", "5"). parseJson records the file's key order of
// each object that has such a key, and pyEntries and pyDumps follow it.
//
// parseJson reads what json.loads reads: NaN, Infinity and -Infinity too; a leading byte order mark
// is refused, as json.loads refuses it.
import { pyFloat } from './pyfmt'

/** the keys of a container (an object, or an array by index) that hold Python floats */
const FLOATS = new WeakMap<object, Set<string>>()
const ORDER = new WeakMap<object, string[]>()
const INDEX_KEY = /^(0|[1-9][0-9]*)$/

export class JsonError extends Error {}

function floatsOf(container: object): Set<string> {
  let s = FLOATS.get(container)
  if (!s) {
    s = new Set()
    FLOATS.set(container, s)
  }
  return s
}

/** Marks ``keys`` of ``container`` (an object's keys, an array's indexes) as Python floats. */
export function markFloat(container: object, ...keys: (string | number)[]): void {
  const s = floatsOf(container)
  for (const k of keys) s.add(String(k))
}

/** Marks the keys of ``obj`` that hold numbers as Python floats (a block the Python builds with floats
 * there: ``float(x)``, ``round(x, n)``, a float column); keys holding anything else are left alone.
 * Returns ``obj``. */
export function floatKeys<T extends object | null | undefined>(obj: T, ...keys: string[]): T {
  if (!obj) return obj
  const o = obj as Record<string, unknown>
  for (const k of keys) if (typeof o[k] === 'number') floatsOf(o).add(k)
  return obj
}

/** Marks every number of an array as a float (a list of Python floats). */
export function markFloats(list: unknown[]): void {
  const s = floatsOf(list)
  for (let i = 0; i < list.length; i++) s.add(String(i))
}

/** Whether ``container[key]`` is marked a Python float. */
export function isMarkedFloat(container: object, key: string | number): boolean {
  return FLOATS.get(container)?.has(String(key)) ?? false
}

// ---------------------------------------------------------------- reading

const WS = new Set([0x20, 0x09, 0x0a, 0x0d])

class Parser {
  i = 0
  constructor(readonly s: string, readonly order: boolean) {}

  fail(what: string): never {
    throw new JsonError(`${what} at ${this.i}`)
  }

  ws(): void {
    const s = this.s
    while (this.i < s.length && WS.has(s.charCodeAt(this.i))) this.i++
  }

  value(): [unknown, boolean] {
    const s = this.s
    const c = s.charCodeAt(this.i)
    if (c === 0x22) return [this.string(), false]
    if (c === 0x7b) return [this.object(), false]
    if (c === 0x5b) return [this.array(), false]
    if (s.startsWith('true', this.i)) { this.i += 4; return [true, false] }
    if (s.startsWith('false', this.i)) { this.i += 5; return [false, false] }
    if (s.startsWith('null', this.i)) { this.i += 4; return [null, false] }
    if (s.startsWith('NaN', this.i)) { this.i += 3; return [NaN, true] }
    if (s.startsWith('Infinity', this.i)) { this.i += 8; return [Infinity, true] }
    if (s.startsWith('-Infinity', this.i)) { this.i += 9; return [-Infinity, true] }
    return this.number()
  }

  number(): [number, boolean] {
    const s = this.s
    const start = this.i
    let i = this.i
    if (s.charCodeAt(i) === 0x2d) i++
    const d0 = s.charCodeAt(i)
    if (d0 === 0x30) i++
    else if (d0 >= 0x31 && d0 <= 0x39) {
      while (i < s.length && s.charCodeAt(i) >= 0x30 && s.charCodeAt(i) <= 0x39) i++
    } else this.fail('Expecting value')
    let float = false
    if (s.charCodeAt(i) === 0x2e && s.charCodeAt(i + 1) >= 0x30 && s.charCodeAt(i + 1) <= 0x39) {
      float = true
      i += 1
      while (i < s.length && s.charCodeAt(i) >= 0x30 && s.charCodeAt(i) <= 0x39) i++
    }
    const e = s.charCodeAt(i)
    if (e === 0x65 || e === 0x45) {
      let j = i + 1
      if (s.charCodeAt(j) === 0x2b || s.charCodeAt(j) === 0x2d) j++
      if (s.charCodeAt(j) >= 0x30 && s.charCodeAt(j) <= 0x39) {
        while (j < s.length && s.charCodeAt(j) >= 0x30 && s.charCodeAt(j) <= 0x39) j++
        float = true
        i = j
      }
    }
    this.i = i
    return [Number(s.slice(start, i)), float]
  }

  string(): string {
    const s = this.s
    let i = this.i + 1
    let out = ''
    let run = i
    for (;;) {
      if (i >= s.length) this.fail('Unterminated string')
      const c = s.charCodeAt(i)
      if (c === 0x22) break
      if (c < 0x20) {
        this.i = i
        this.fail('Invalid control character')
      }
      if (c === 0x5c) {
        out += s.slice(run, i)
        const e = s[i + 1]
        if (e === 'u') {
          const h = s.slice(i + 2, i + 6)
          if (!/^[0-9a-fA-F]{4}$/.test(h)) {
            this.i = i
            this.fail('Invalid \\uXXXX escape')
          }
          out += String.fromCharCode(parseInt(h, 16))
          i += 6
        } else {
          const map: Record<string, string> = { '"': '"', '\\': '\\', '/': '/', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t' }
          if (!(e in map)) {
            this.i = i
            this.fail('Invalid \\escape')
          }
          out += map[e]
          i += 2
        }
        run = i
        continue
      }
      i++
    }
    out += s.slice(run, i)
    this.i = i + 1
    return out
  }

  object(): Record<string, unknown> {
    const s = this.s
    this.i++
    const obj: Record<string, unknown> = {}
    const keys: string[] = []
    let indexKey = false
    this.ws()
    if (s.charCodeAt(this.i) === 0x7d) {
      this.i++
      return obj
    }
    for (;;) {
      if (s.charCodeAt(this.i) !== 0x22) this.fail('Expecting property name enclosed in double quotes')
      const key = this.string()
      this.ws()
      if (s.charCodeAt(this.i) !== 0x3a) this.fail("Expecting ':' delimiter")
      this.i++
      this.ws()
      const [v, float] = this.value()
      if (!(key in obj) || !Object.prototype.hasOwnProperty.call(obj, key)) {
        keys.push(key)
        if (!indexKey && INDEX_KEY.test(key)) indexKey = true
      }
      // a repeated key keeps its first place and takes the last value (a dict's assignment)
      Object.defineProperty(obj, key, { value: v, writable: true, enumerable: true, configurable: true })
      if (float) floatsOf(obj).add(key)
      else FLOATS.get(obj)?.delete(key)
      this.ws()
      const c = s.charCodeAt(this.i)
      if (c === 0x2c) {
        this.i++
        this.ws()
        continue
      }
      if (c === 0x7d) {
        this.i++
        break
      }
      this.fail("Expecting ',' delimiter")
    }
    if (indexKey && this.order) ORDER.set(obj, keys)
    return obj
  }

  array(): unknown[] {
    const s = this.s
    this.i++
    const arr: unknown[] = []
    this.ws()
    if (s.charCodeAt(this.i) === 0x5d) {
      this.i++
      return arr
    }
    for (;;) {
      const [v, float] = this.value()
      if (float) floatsOf(arr).add(String(arr.length))
      arr.push(v)
      this.ws()
      const c = s.charCodeAt(this.i)
      if (c === 0x2c) {
        this.i++
        this.ws()
        continue
      }
      if (c === 0x5d) {
        this.i++
        break
      }
      this.fail("Expecting ',' delimiter")
    }
    return arr
  }
}

/** json.loads(text): plain values, with each float and (``order``, default on) each object's key order
 * noted. Throws JsonError on what json.loads refuses. */
export function parseJson(text: string, { order = true } = {}): any {
  if (text.charCodeAt(0) === 0xfeff) throw new JsonError('Unexpected UTF-8 BOM (decode using utf-8-sig)')
  const p = new Parser(text, order)
  p.ws()
  const [v] = p.value()
  p.ws()
  if (p.i !== text.length) p.fail('Extra data')
  return v
}

/** dict.items(): an object's entries in the file's order where parseJson recorded it (keys added since
 * follow in their own order). */
export function pyEntries<T>(obj: Record<string, T>): [string, T][] {
  const keys = ORDER.get(obj)
  if (!keys) return Object.entries(obj)
  const own = keys.filter((k) => Object.prototype.hasOwnProperty.call(obj, k))
  const seen = new Set(own)
  for (const k of Object.keys(obj)) if (!seen.has(k)) own.push(k)
  return own.map((k) => [k, obj[k]] as [string, T])
}

/** dict.values() in the file's order */
export const pyValues = <T>(obj: Record<string, T>): T[] => pyEntries(obj).map(([, v]) => v)

/** A deep copy that keeps the floats and key orders noted (copy.deepcopy of a parsed value). */
export function pyClone<T>(value: T): T {
  if (value === null || typeof value !== 'object') return value
  if (ArrayBuffer.isView(value)) return (value as unknown as Uint8Array).slice() as unknown as T
  if (Array.isArray(value)) {
    const out = value.map((v) => pyClone(v))
    const f = FLOATS.get(value)
    if (f) FLOATS.set(out, new Set(f))
    return out as unknown as T
  }
  const obj = value as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const [k, v] of pyEntries(obj)) {
    Object.defineProperty(out, k, { value: pyClone(v), writable: true, enumerable: true, configurable: true })
  }
  const f = FLOATS.get(obj)
  if (f) FLOATS.set(out, new Set(f))
  const order = ORDER.get(obj)
  if (order) ORDER.set(out, pyEntries(obj).map(([k]) => k))
  return out as T
}

/** dict(obj) (a shallow copy) that keeps the floats and key order noted. */
export function pyCopy<T extends object>(obj: T): T {
  const out = (Array.isArray(obj) ? [...obj] : { ...obj }) as T
  const f = FLOATS.get(obj)
  if (f) FLOATS.set(out, new Set(f))
  const o = ORDER.get(obj)
  if (o) ORDER.set(out, [...o])
  return out
}

/** obj[key] = value, the key now holding a float only when ``float`` (an int otherwise, as Python's
 * assignment of a new value would make it). */
export function pySet(obj: Record<string, any>, key: string, value: unknown, float = false): void {
  obj[key] = value
  if (float) floatsOf(obj).add(key)
  else FLOATS.get(obj)?.delete(key)
}

// ---------------------------------------------------------------- writing

/** Places that hold floats, as patterns of keys (``*`` any one key or index, ``**`` any run of them), for
 * values made here: ``["delineation", "drainage_area_sqkm"]``. */
export type FloatPattern = string[]

function matches(pattern: FloatPattern, path: string[], pi = 0, xi = 0): boolean {
  for (; pi < pattern.length; pi++, xi++) {
    const p = pattern[pi]
    if (p === '**') {
      for (let k = xi; k <= path.length; k++) if (matches(pattern, path, pi + 1, k)) return true
      return false
    }
    if (xi >= path.length) return false
    if (p !== '*' && p !== path[xi]) return false
  }
  return xi === path.length
}

export interface DumpOptions {
  /** json.dumps' indent (null: one line) */
  indent?: number | null
  /** json.dumps' separators (default: (", ", ": "), or (",", ": ") with an indent) */
  separators?: [string, string]
  ensureAscii?: boolean
  sortKeys?: boolean
  /** places holding floats (see FloatPattern) */
  floats?: FloatPattern[]
}

function pyString(s: string, ascii: boolean): string {
  let out = '"'
  let run = 0
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i)
    let esc: string | null = null
    if (c === 0x22) esc = '\\"'
    else if (c === 0x5c) esc = '\\\\'
    else if (c === 0x0a) esc = '\\n'
    else if (c === 0x0d) esc = '\\r'
    else if (c === 0x09) esc = '\\t'
    else if (c === 0x08) esc = '\\b'
    else if (c === 0x0c) esc = '\\f'
    else if (c < 0x20 || (ascii && c >= 0x7f)) esc = '\\u' + c.toString(16).padStart(4, '0')
    if (esc !== null) {
      out += s.slice(run, i) + esc
      run = i + 1
    }
  }
  return out + s.slice(run) + '"'
}

/** Python's sort of str keys: by code point. */
function codePointCompare(a: string, b: string): number {
  const x = [...a]
  const y = [...b]
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0)! - y[i].codePointAt(0)!
    if (d) return d
  }
  return x.length - y.length
}

/** float.__repr__ and int.__repr__ as json.dumps writes them (allow_nan). */
export function pyNumberText(v: number, float: boolean): string {
  if (Number.isNaN(v)) return 'NaN'
  if (!Number.isFinite(v)) return v > 0 ? 'Infinity' : '-Infinity'
  if (!float && Number.isInteger(v)) return Object.is(v, -0) ? '0' : String(v)
  return pyFloat(v)
}

/** json.dumps(value, ...) with Python's defaults: ", " and ": " separators, ASCII only, keys in order. */
export function pyDumps(value: unknown, opts: DumpOptions = {}): string {
  const indent = opts.indent ?? null
  const [itemSep, keySep] = opts.separators ?? (indent === null ? [', ', ': '] : [',', ': '])
  const ascii = opts.ensureAscii ?? true
  const patterns = opts.floats ?? []
  const path: string[] = []
  const floatAt = (container: object | null, key: string) =>
    (container !== null && isMarkedFloat(container, key)) || (patterns.length > 0 && patterns.some((p) => matches(p, path)))
  const enc = (v: unknown, float: boolean, level: number): string => {
    if (v === null || v === undefined) return 'null'
    if (v === true) return 'true'
    if (v === false) return 'false'
    if (typeof v === 'number') return pyNumberText(v, float)
    if (typeof v === 'string') return pyString(v, ascii)
    if (typeof v === 'bigint') return v.toString()
    const nl = indent === null ? null : '\n' + ' '.repeat(indent * (level + 1))
    const close = indent === null ? '' : '\n' + ' '.repeat(indent * level)
    const sep = nl === null ? itemSep : itemSep + nl
    if (Array.isArray(v)) {
      if (!v.length) return '[]'
      const parts: string[] = []
      for (let i = 0; i < v.length; i++) {
        path.push(String(i))
        parts.push(enc(v[i], floatAt(v, String(i)), level + 1))
        path.pop()
      }
      return '[' + (nl ?? '') + parts.join(sep) + close + ']'
    }
    if (ArrayBuffer.isView(v)) return enc(Array.from(v as unknown as ArrayLike<number>), float, level)
    const obj = v as Record<string, unknown>
    let entries = pyEntries(obj).filter(([, x]) => x !== undefined)
    if (!entries.length) return '{}'
    if (opts.sortKeys) entries = [...entries].sort(([a], [b]) => codePointCompare(a, b))
    const parts: string[] = []
    for (const [k, x] of entries) {
      path.push(k)
      parts.push(pyString(k, ascii) + keySep + enc(x, floatAt(obj, k), level + 1))
      path.pop()
    }
    return '{' + (nl ?? '') + parts.join(sep) + close + '}'
  }
  return enc(value, false, 0)
}

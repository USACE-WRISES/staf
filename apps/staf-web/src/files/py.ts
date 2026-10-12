// Python's string, path and XML-escape semantics, where JavaScript's own differ: the file writers
// (the workbooks, the PDFs) copy what the Shiny tools' Python writes, so they spell text, split
// words, slice and join paths as Python does.

/** Python's str.isspace characters (JavaScript's \s lacks \x1c-\x1f and \x85, and adds \ufeff). */
export const PY_WS = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
const WS_RUN = new RegExp(`[${PY_WS}]+`)
const LEAD = new RegExp(`^[${PY_WS}]+`)
const TRAIL = new RegExp(`[${PY_WS}]+$`)

/** str.strip() */
export const pyStrip = (s: string): string => s.replace(LEAD, '').replace(TRAIL, '')
/** str.rstrip() */
export const pyRstrip = (s: string): string => s.replace(TRAIL, '')

/** str.strip(chars) */
export function pyStripChars(s: string, chars: string): string {
  let a = 0
  let b = s.length
  while (a < b && chars.includes(s[a])) a++
  while (b > a && chars.includes(s[b - 1])) b--
  return s.slice(a, b)
}

/** str.split(): the words between runs of whitespace. */
export function pySplit(s: string): string[] {
  return s.split(WS_RUN).filter((w) => w !== '')
}

/** ' '.join(s.split()): runs of whitespace as one space, none at the ends. */
export const pySqueeze = (s: string): string => pySplit(s).join(' ')

/** len(s): code points, not UTF-16 units. */
export const cpLen = (s: string): number => [...s].length

/** s[start:end] by code points (non-negative bounds). */
export function cpSlice(s: string, start: number, end?: number): string {
  const cps = [...s]
  return cps.slice(start, end).join('')
}

/** str.isupper() of one character (Unicode's Uppercase property). */
export const isUpper = (ch: string): boolean => /^\p{Uppercase}$/u.test(ch)
/** str.isalpha() of one character */
export const isAlpha = (ch: string): boolean => /^\p{L}$/u.test(ch)
/** str.isdigit() of one character */
export const isDigit = (ch: string): boolean => /^\p{Nd}$/u.test(ch)
/** str.isalnum() of one character */
export const isAlnum = (ch: string): boolean => /^[\p{L}\p{N}]$/u.test(ch)

/** str.capitalize(): the first character upper, the rest lower. */
export function pyCapitalize(s: string): string {
  const cps = [...s]
  return cps.length ? cps[0].toUpperCase() + cps.slice(1).join('').toLowerCase() : ''
}

/** s.replace(old, new, 1): the first occurrence, as plain text ($ means nothing). */
export function replaceFirst(s: string, old: string, rep: string): string {
  const i = s.indexOf(old)
  return i < 0 ? s : s.slice(0, i) + rep + s.slice(i + old.length)
}

/** s.replace(old, new): every occurrence, as plain text. */
export const replaceAll = (s: string, old: string, rep: string): string => s.split(old).join(rep)

/** re.escape */
export const reEscape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\\/-]/g, '\\$&')

// ---------------------------------------------------------------- xml.sax.saxutils

/** escape(data, entities): &, then >, then <, then each extra entity. */
export function xmlEscape(data: string, entities: Record<string, string> = {}): string {
  let s = replaceAll(replaceAll(replaceAll(data, '&', '&amp;'), '>', '&gt;'), '<', '&lt;')
  for (const [k, v] of Object.entries(entities)) s = replaceAll(s, k, v)
  return s
}

/** unescape(data, entities): &lt;, &gt;, each extra entity, then &amp; last. */
export function xmlUnescape(data: string, entities: Record<string, string> = {}): string {
  let s = replaceAll(replaceAll(data, '&lt;', '<'), '&gt;', '>')
  for (const [k, v] of Object.entries(entities)) s = replaceAll(s, k, v)
  return replaceAll(s, '&amp;', '&')
}

// ---------------------------------------------------------------- posixpath

export const posix = {
  /** posixpath.split */
  split(p: string): [string, string] {
    const i = p.lastIndexOf('/') + 1
    let head = p.slice(0, i)
    const tail = p.slice(i)
    if (head && head !== '/'.repeat(head.length)) head = head.replace(/\/+$/, '')
    return [head, tail]
  },
  /** posixpath.join */
  join(a: string, ...rest: string[]): string {
    let path = a
    for (const b of rest) {
      if (b.startsWith('/')) path = b
      else if (!path || path.endsWith('/')) path += b
      else path += '/' + b
    }
    return path
  },
  /** posixpath.dirname */
  dirname(p: string): string {
    return posix.split(p)[0]
  },
  /** posixpath.normpath */
  normpath(path: string): string {
    if (!path) return '.'
    let initial = path.startsWith('/') ? 1 : 0
    if (initial && path.startsWith('//') && !path.startsWith('///')) initial = 2
    const out: string[] = []
    for (const comp of path.split('/')) {
      if (comp === '' || comp === '.') continue
      if (comp !== '..' || (!initial && !out.length) || (out.length && out[out.length - 1] === '..')) out.push(comp)
      else if (out.length) out.pop()
    }
    const joined = '/'.repeat(initial) + out.join('/')
    return joined || '.'
  },
  /** posixpath.relpath(path, start), both relative to the same working directory */
  relpath(path: string, start = '.'): string {
    if (!path) throw new Error('no path specified')
    const abs = (p: string) => posix.normpath(p.startsWith('/') ? p : posix.join('/cwd', p)).split('/').filter((x) => x)
    const s = abs(start)
    const t = abs(path)
    let i = 0
    while (i < s.length && i < t.length && s[i] === t[i]) i++
    const rel = [...Array(s.length - i).fill('..'), ...t.slice(i)]
    return rel.length ? posix.join(rel[0], ...rel.slice(1)) : '.'
  },
}

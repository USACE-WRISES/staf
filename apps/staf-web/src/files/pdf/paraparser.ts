// reportlab's platypus/paraparser.py, the markup the reports use (b/strong, i/em, br, super/sup, sub,
// font and span with size/name/color, link and a with href and color, u, para) over Python's html.parser
// scanning: data between markup is one fragment, each entity or character reference another, so the
// fragments, and the widths summed over them, are reportlab's.
import { Color, toColor } from './colors'

/** ParaFrag: a run of text with its style. */
export class Frag {
  fontName = ''
  fontSize = 0
  textColor: Color | number[] | null = null
  rise = 0
  bold = 0
  italic = 0
  greek = 0
  link: [number, string][] = []
  us_lines: unknown[] = []
  text?: string
  lineBreak?: boolean
  backColor?: Color | null
  cbDefn?: { kind: string, width?: number }
  nobr?: boolean
  _fkind?: number
  _selfClosingTag?: string
  __tag__?: string

  clone(attrs: Partial<Frag> = {}): Frag {
    const f = Object.assign(new Frag(), this)
    return Object.assign(f, attrs)
  }
}

/** sameFrag: two runs that draw alike (never a break or a callback). */
export function sameFrag(f: Frag, g: Frag): boolean {
  if (f.cbDefn !== undefined || f.lineBreak !== undefined || g.cbDefn !== undefined || g.lineBreak !== undefined) return false
  return f.fontName === g.fontName && f.fontSize === g.fontSize && colorEq(f.textColor, g.textColor) && f.rise === g.rise
    && JSON.stringify(f.us_lines) === JSON.stringify(g.us_lines) && JSON.stringify(f.link) === JSON.stringify(g.link)
    && colorEq(f.backColor ?? null, g.backColor ?? null) && (f.nobr ?? null) === (g.nobr ?? null)
}

/** Color == Color: by components; anything else by identity. */
export function colorEq(a: unknown, b: unknown): boolean {
  if (a instanceof Color && b instanceof Color) return a.red === b.red && a.green === b.green && a.blue === b.blue && a.alpha === b.alpha
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => v === b[i])
  return a === b
}

const PS2TT: Record<string, [string, number, number]> = {
  courier: ['courier', 0, 0], 'courier-oblique': ['courier', 0, 1], 'courier-bold': ['courier', 1, 0], 'courier-boldoblique': ['courier', 1, 1],
  helvetica: ['helvetica', 0, 0], 'helvetica-oblique': ['helvetica', 0, 1], 'helvetica-bold': ['helvetica', 1, 0],
  'helvetica-boldoblique': ['helvetica', 1, 1], symbol: ['symbol', 0, 0], 'times-roman': ['times', 0, 0], times: ['times', 0, 0],
  'times-italic': ['times', 0, 1], 'times-bold': ['times', 1, 0], 'times-bolditalic': ['times', 1, 1], zapfdingbats: ['zapfdingbats', 0, 0],
}
const TT2PS: Record<string, string> = {
  'times,0,0': 'Times-Roman', 'times,1,0': 'Times-Bold', 'times,0,1': 'Times-Italic', 'times,1,1': 'Times-BoldItalic',
  'courier,0,0': 'Courier', 'courier,1,0': 'Courier-Bold', 'courier,0,1': 'Courier-Oblique', 'courier,1,1': 'Courier-BoldOblique',
  'helvetica,0,0': 'Helvetica', 'helvetica,1,0': 'Helvetica-Bold', 'helvetica,0,1': 'Helvetica-Oblique', 'helvetica,1,1': 'Helvetica-BoldOblique',
  'symbol,0,0': 'Symbol', 'symbol,1,0': 'Symbol', 'symbol,0,1': 'Symbol', 'symbol,1,1': 'Symbol',
  'zapfdingbats,0,0': 'ZapfDingbats', 'zapfdingbats,1,0': 'ZapfDingbats', 'zapfdingbats,0,1': 'ZapfDingbats', 'zapfdingbats,1,1': 'ZapfDingbats',
}

/** lib/fonts ps2tt: a PostScript name as (family, bold, italic). */
export function ps2tt(psfn: string): [string, number, number] {
  const r = PS2TT[psfn.toLowerCase()]
  if (!r) throw new Error(`ValueError: Can't map determine family/bold/italic for ${psfn.toLowerCase()}`)
  return r
}

/** lib/fonts tt2ps: (family, bold, italic) as a PostScript name. */
export function tt2ps(fn: string, b: number, i: number): string {
  const r = TT2PS[`${fn.toLowerCase()},${b ? 1 : 0},${i ? 1 : 0}`]
  if (!r) throw new Error(`ValueError: Can't find concrete font for family=${fn}, bold=${b}, italic=${i}`)
  return r
}

export interface StyleLike {
  name: string
  fontName: string
  fontSize: number
  textColor: Color | number[] | null
  [k: string]: unknown
}

/** _num: a number of points (int or float; no units, no relative +/- sizes: the reports write none). */
function num(s: string): number {
  const t = s.trim()
  if (t[0] === '+' || t[0] === '-') throw new Error(`relative sizes are not supported here: '${s}'`)
  if (/^\d+$/.test(t)) return parseInt(t, 10)
  const v = Number(t)
  if (t === '' || Number.isNaN(v)) throw new Error(`ValueError: could not convert string to float: '${s}'`)
  return v
}

const SUP_FRACTION = 0.5
const SUB_FRACTION = 0.5
const SIZE_DELTA = 2

/** html.unescape for the attribute values the reports write (quotes and the three XML entities). */
function htmlUnescape(s: string): string {
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]*);?/g, (m: string, name: string) => {
    if (name[0] === '#') return String.fromCodePoint(name[1] === 'x' || name[1] === 'X' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10))
    const v = ENTITIES[name]
    return v === undefined ? m : v
  })
}

/** the named entities html.entities.name2codepoint holds that markup may carry */
const ENTITIES: Record<string, string> = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0', copy: '\u00a9', reg: '\u00ae', deg: '\u00b0', plusmn: '\u00b1',
  sup2: '\u00b2', sup3: '\u00b3', micro: '\u00b5', middot: '\u00b7', frac12: '\u00bd', times: '\u00d7', ndash: '\u2013', mdash: '\u2014',
  lsquo: '\u2018', rsquo: '\u2019', ldquo: '\u201c', rdquo: '\u201d', bull: '\u2022', hellip: '\u2026', dagger: '\u2020', ge: '\u2265', le: '\u2264',
}

const RE_PARA = /^\s*<\s*para(?:\s+|>|\/>)/
const ATTRFIND = /((?<=['"\s/])[^\s/>][^\s/=>]*)(\s*=+\s*('[^']*'|"[^"]*"|(?!['"])[^>\s]*))?(?:\s|\/(?!>))*/y

/** ParaParser.parse: the fragments of a paragraph's markup. */
export function parseParagraph(text: string, style: StyleLike): Frag[] {
  if (!(text.length >= 6 && text[0] === '<' && RE_PARA.test(text))) text = '<para>' + text + '</para>'
  const fragList: Frag[] = []
  let stack: Frag[] = []
  let nlinks = 0

  const initialFrag = (): Frag => {
    const frag = new Frag()
    frag.rise = 0
    frag.greek = 0
    frag.link = []
    const [fn, b, i] = ps2tt(style.fontName)
    frag.fontName = fn
    frag.bold = b
    frag.italic = i
    frag.fontSize = style.fontSize
    frag.textColor = style.textColor
    frag.us_lines = []
    return frag
  }
  const push = (tag: string, attrs: Partial<Frag> & Record<string, unknown> = {}): Frag => {
    const frag = stack[stack.length - 1].clone()
    frag.__tag__ = tag
    Object.assign(frag, attrs)
    stack.push(frag)
    return frag
  }
  const pop = (tag: string): Frag => {
    const frag = stack.pop()!
    if (frag.__tag__ !== tag) throw new Error(`Parse error: saw </${tag}> instead of expected </${frag.__tag__}>`)
    return frag
  }
  const handleData = (data: string): void => {
    const frag = stack[stack.length - 1].clone()
    if (frag._selfClosingTag !== undefined && frag.cbDefn === undefined) {
      if (data !== '') throw new Error(`paraparser: syntax error: No content allowed in ${frag._selfClosingTag} tag`)
      return
    }
    frag.fontName = tt2ps(frag.fontName, frag.bold, frag.italic)
    frag.text = data
    fragList.push(frag)
  }
  const fontAttrs = (attrs: Map<string, string>, link: boolean): Record<string, unknown> => {
    const A: Record<string, unknown> = {}
    for (const [k, v] of attrs) {
      if (k === 'size' || k === 'fontsize') A.fontSize = num(v)
      else if (k === 'face' || k === 'name' || k === 'fontname') A.fontName = v
      else if (k === 'color' || k === 'fg' || k === 'textcolor') A.textColor = toColor(v)
      else if (k === 'backcolor' || k === 'bgcolor') A.backColor = toColor(v)
      else if (link && (k === 'href' || k === 'dest' || k === 'destination' || k === 'target' || k === 'link')) A.link = v
      else if (link && k === 'underline') A.underline = /^(true|1|yes)$/i.test(v)
      else throw new Error(`paraparser: syntax error: invalid attribute name ${k}`)
    }
    if (typeof A.fontName === 'string') {
      const [fn, b, i] = ps2tt(A.fontName)
      A.fontName = fn
      A.bold = b
      A.italic = i
    }
    return A
  }
  const start = (tag: string, attrs: Map<string, string>): void => {
    switch (tag) {
      case 'para': {
        if (attrs.size) throw new Error('<para> attributes are not supported here')
        const frag = initialFrag()
        frag.__tag__ = 'para'
        stack = [frag]
        nlinks = 0
        return
      }
      case 'b': case 'strong':
        push(tag, { bold: 1 })
        return
      case 'i': case 'em':
        push(tag, { italic: 1 })
        return
      case 'br':
        push('br', { _selfClosingTag: 'br', lineBreak: true, text: '' })
        return
      case 'super': case 'sup': case 'sub': {
        if (attrs.size) throw new Error(`<${tag}> attributes are not supported here`)
        const frag = push(tag === 'sub' ? 'sub' : 'super')
        frag.rise += (tag === 'sub' ? -1 : 1) * frag.fontSize * (tag === 'sub' ? SUB_FRACTION : SUP_FRACTION)
        frag.fontSize = frag.fontSize - Math.min(SIZE_DELTA, 0.2 * frag.fontSize)
        return
      }
      case 'font': case 'span':
        push(tag, fontAttrs(attrs, false))
        return
      case 'link': case 'a': {
        const A = fontAttrs(attrs, true)
        delete A.underline
        const target = typeof A.link === 'string' ? A.link : ''
        A.link = [...stack[stack.length - 1].link, [nlinks, target.trim()]]
        nlinks += 1
        push(tag, A)
        return
      }
      default:
        return                                        // start_unknown
    }
  }
  const end = (tag: string): void => {
    switch (tag) {
      case 'para': pop('para'); return
      case 'b': case 'strong': case 'i': case 'em': case 'font': case 'span': pop(tag); return
      case 'super': case 'sup': pop('super'); return
      case 'sub': pop('sub'); return
      case 'link': case 'a': pop(tag); return
      case 'br': {
        const frag = stack[stack.length - 1]
        if (!(frag._selfClosingTag === 'br' && frag.lineBreak)) throw new Error('Parser failure in <br/>')
        delete frag._selfClosingTag
        handleData('')
        pop('br')
        return
      }
      default:
        return                                        // end_unknown
    }
  }

  // html.parser's scan (convert_charrefs=False): data, start/end tags, entity and character references
  const n = text.length
  let i = 0
  while (i < n) {
    let j = i
    while (j < n && text[j] !== '<' && text[j] !== '&') j++
    if (i < j) handleData(text.slice(i, j))
    i = j
    if (i >= n) break
    if (text[i] === '<') {
      if (/^<[a-zA-Z]/.test(text.slice(i, i + 2))) {
        const m = /^<([a-zA-Z][^\t\n\r\f />\x00]*)(?:\s|\/(?!>))*/.exec(text.slice(i))
        if (!m) throw new Error('bad start tag')
        const tag = m[1].toLowerCase()
        let k = i + m[0].length
        const attrs = new Map<string, string>()
        ATTRFIND.lastIndex = k
        for (let am = ATTRFIND.exec(text); am && am[0]; am = ATTRFIND.exec(text)) {
          const name = am[1].toLowerCase()
          let value = am[3] ?? null
          if (value === null) value = ''
          else if ((value[0] === "'" && value.endsWith("'")) || (value[0] === '"' && value.endsWith('"'))) value = value.slice(1, -1)
          if (value) value = htmlUnescape(value)
          attrs.set(name, value)
          k = ATTRFIND.lastIndex
        }
        const close = text.indexOf('>', k)
        if (close < 0) throw new Error('an unclosed tag')
        const rest = text.slice(k, close + 1).trim()
        if (rest !== '>' && rest !== '/>') handleData(text.slice(i, close + 1))   // not a tag after all: its text
        else {
          start(tag, attrs)
          if (rest === '/>') end(tag)
        }
        i = close + 1
      } else if (text.startsWith('</', i)) {
        const close = text.indexOf('>', i)
        if (close < 0) throw new Error('an unclosed end tag')
        const m = /^<\/\s*([a-zA-Z][^\t\n\r\f />\x00]*)/.exec(text.slice(i, close + 1))
        if (m) end(m[1].toLowerCase())
        i = close + 1
      } else if (text.startsWith('<!--', i)) {
        const close = text.indexOf('-->', i + 4)
        i = close < 0 ? n : close + 3
      } else {
        handleData('<')
        i += 1
      }
    } else {
      // '&': a character reference, an entity, or a bare ampersand
      const cm = /^&#(?:[0-9]+|[xX][0-9a-fA-F]+)[^0-9a-fA-F]/.exec(text.slice(i))
      if (cm) {
        const name = cm[0].slice(2, -1)
        handleData(String.fromCodePoint(name[0] === 'x' || name[0] === 'X' ? parseInt(name.slice(1), 16) : parseInt(name, 10)))
        let k = i + cm[0].length
        if (text[k - 1] !== ';') k -= 1
        i = k
        continue
      }
      const em = /^&([a-zA-Z][-.a-zA-Z0-9]*)[^a-zA-Z0-9]/.exec(text.slice(i))
      if (em) {
        const v = ENTITIES[em[1]]
        handleData(v === undefined ? `&${em[1]};` : v)
        let k = i + em[0].length
        if (text[k - 1] !== ';') k -= 1
        i = k
        continue
      }
      if (text.startsWith('&#', i)) {                   // no character reference: '&#' kept when a ';' follows, then stop
        if (text.indexOf(';', i) >= 0) handleData('&#')
        break
      }
      if (/^&[a-zA-Z]/.test(text.slice(i))) break       // incomplete: html.parser waits for more
      handleData('&')
      i += 1
    }
  }
  if (stack.length) throw new Error(`paraparser: syntax error: parse ended with ${stack.length} unclosed tags`)
  return fragList
}

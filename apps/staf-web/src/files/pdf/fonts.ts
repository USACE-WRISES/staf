// reportlab's standard-14 fonts (pdfbase/pdfmetrics.py with lib/rl_accel.py's pure-Python paths): each
// font's encoding and widths, the fonts it falls back on for characters its encoding lacks (Symbol, then
// ZapfDingbats, then ZapfDingbats' black square), string widths and ascent/descent. The tables are
// reportlab's own (content/pdf/fonts.json, parity/copy_page.py).
import data from '../../app/content/pdf/fonts.json'

interface FontData { encoding: string, widths: number[], ascent: number, descent: number, substitution: string[], notdef: [string, string] }
const DATA = data as unknown as { reportlab: string, fonts: Record<string, FontData>, encodings: Record<string, Record<string, number>> }

export const REPORTLAB_VERSION = DATA.reportlab

export interface Font {
  fontName: string
  encName: string
  widths: number[]
  ascent: number
  descent: number
  substitutionFonts: Font[]
  notdefFont: Font
  /** the byte drawn for a character no font can show */
  notdefChar: number
}

const codecs = new Map<string, Map<number, number>>()

/** A single-byte codec as {code point: byte}. */
export function codec(name: string): Map<number, number> {
  let c = codecs.get(name)
  if (!c) {
    const table = DATA.encodings[name]
    if (!table) throw new Error(`LookupError: unknown encoding: ${name}`)
    c = new Map(Object.entries(table).map(([cp, b]) => [Number(cp), b]))
    codecs.set(name, c)
  }
  return c
}

const fonts = new Map<string, Font>()

/** pdfmetrics.getFont: one of the standard 14. */
export function getFont(name: string): Font {
  let f = fonts.get(name)
  if (f) return f
  const d = DATA.fonts[name]
  if (!d) throw new Error(`KeyError: '${name}'`)
  f = { fontName: name, encName: d.encoding, widths: d.widths, ascent: d.ascent, descent: d.descent, substitutionFonts: [], notdefFont: null as unknown as Font,
    notdefChar: d.notdef[1].charCodeAt(0) }
  fonts.set(name, f)
  f.substitutionFonts = d.substitution.map(getFont)
  f.notdefFont = d.notdef[0] === name ? f : getFont(d.notdef[0])
  return f
}

/** text.encode(codec) as bytes, or the first run of characters it cannot encode ([start, end) in code
 * points) with the bytes before it. */
function encode(cps: number[], table: Map<number, number>): { bytes: Uint8Array, bad: [number, number] | null } {
  const out: number[] = []
  for (let i = 0; i < cps.length; i++) {
    const b = table.get(cps[i])
    if (b === undefined) {
      let j = i + 1
      while (j < cps.length && !table.has(cps[j])) j++
      return { bytes: Uint8Array.from(out), bad: [i, j] }
    }
    out.push(b)
  }
  return { bytes: Uint8Array.from(out), bad: null }
}

const codePoints = (text: string): number[] => Array.from(text, (ch) => ch.codePointAt(0)!)

/** unicode2T1: the text as [(font, bytes)] runs, each font taking what its encoding can show. */
export function unicode2T1(text: string | number[], fontList: Font[]): [Font, Uint8Array][] {
  const R: [Font, Uint8Array][] = []
  const [font, ...rest] = fontList
  const table = codec(font.encName)
  let cps = typeof text === 'string' ? codePoints(text) : text
  while (cps.length) {
    const { bytes, bad } = encode(cps, table)
    if (bad === null) {
      R.push([font, bytes])
      break
    }
    const [i0, il] = bad
    if (i0) R.push([font, bytes])
    if (rest.length) R.push(...unicode2T1(cps.slice(i0, il), rest))
    else R.push([font.notdefFont, new Uint8Array(il - i0).fill(font.notdefChar)])
    cps = cps.slice(il)
  }
  return R
}

/** Font.stringWidth (instanceStringWidthT1): the widths of the runs' bytes, in points. */
export function fontStringWidth(font: Font, text: string, size: number): number {
  let total = 0
  for (const [f, t] of unicode2T1(text, [font, ...font.substitutionFonts])) for (const b of t) total += f.widths[b]
  return total * 0.001 * size
}

/** pdfmetrics.stringWidth */
export const stringWidth = (text: string, fontName: string, fontSize: number): number => fontStringWidth(getFont(fontName), text, fontSize)

/** pdfmetrics.getAscentDescent: the face's ascent and descent, scaled to ``fontSize`` when given. */
export function getAscentDescent(fontName: string, fontSize: number | null = null): [number, number] {
  const f = getFont(fontName)
  if (fontSize) {
    const norm = fontSize / 1000.0
    return [f.ascent * norm, f.descent * norm]
  }
  return [f.ascent, f.descent]
}

// New cell styles appended to a workbook's styles.xml (libs/staf_workbook xlsx/styles.py). Existing
// entries never move (every template cell keeps its style index); new fonts, fills, borders, number
// formats, cell formats and differential formats go at the end and the count attributes follow. The
// workbook's own default font is reused so authored sheets sit naturally beside the template's.
import { pyG } from '../../app/pyfmt'
import { xmlEscape } from '../py'
import { attr, Package } from './package'

export const STYLES = 'xl/styles.xml'
const BUILTIN_FORMATS: Record<string, number> = { General: 0, 0: 1, '0.00': 2, '#,##0': 3, '#,##0.00': 4, '0%': 9, '0.00%': 10 }
const CHILD: Record<string, string> = { fonts: 'font', fills: 'fill', borders: 'border', cellXfs: 'xf', dxfs: 'dxf' }

export class StyleBook {
  xml: string
  fontName: string
  fontSize: number
  private memo = new Map<string, number>()

  constructor(private pkg: Package) {
    this.xml = pkg.text(STYLES)
    const font0 = /<fonts\b[^>]*>\s*<font\b[^>]*>([\s\S]*?)<\/font>/.exec(this.xml)
    const name = font0 ? /<name val="([^"]+)"/.exec(font0[1]) : null
    const size = font0 ? /<sz val="([^"]+)"/.exec(font0[1]) : null
    this.fontName = name ? name[1] : 'Calibri'
    this.fontSize = size ? Number(size[1]) : 11.0
  }

  // ---------------------------------------------------------------- collections
  private append(tag: string, childXml: string): number {
    const m = new RegExp(`<${tag}\\b([^>]*?)(?<!/)>([\\s\\S]*?)</${tag}>`).exec(this.xml)     // never a self-closing tag
    if (m === null) {
      const empty = new RegExp(`<${tag}\\b([^>]*)/>`).exec(this.xml)
      if (empty === null) throw new Error(`styles.xml has no ${tag}`)
      const i = this.xml.indexOf(empty[0])
      this.xml = this.xml.slice(0, i) + `<${tag} count="1">${childXml}</${tag}>` + this.xml.slice(i + empty[0].length)
      return 0
    }
    const index = [...m[2].matchAll(new RegExp(`<${CHILD[tag]}\\b`, 'g'))].length
    const attrs = m[1].replace(/\scount="\d+"/g, '')
    const next = `<${tag}${attrs} count="${index + 1}">${m[2]}${childXml}</${tag}>`
    this.xml = this.xml.slice(0, m.index) + next + this.xml.slice(m.index + m[0].length)
    return index
  }

  numfmt(code: string): number {
    if (Object.hasOwn(BUILTIN_FORMATS, code)) return BUILTIN_FORMATS[code]
    const key = JSON.stringify(['numfmt', code])
    const memo = this.memo.get(key)
    if (memo !== undefined) return memo
    const m = /<numFmts\b[^>]*>([\s\S]*?)<\/numFmts>/.exec(this.xml)
    const existing = m ? [...m[1].matchAll(/numFmtId="(\d+)"/g)].map((x) => Number(x[1])) : []
    for (const x of m ? m[1].matchAll(/<numFmt\b[^>]*numFmtId="(\d+)"[^>]*formatCode="([^"]*)"/g) : []) {
      if (x[2] === xmlEscape(code, { '"': '&quot;' })) {
        this.memo.set(key, Number(x[1]))
        return Number(x[1])
      }
    }
    const fid = Math.max(...existing, 163) + 1
    const tag = `<numFmt numFmtId="${fid}" formatCode="${attr(code)}"/>`
    if (m) {
      this.xml = this.xml.slice(0, m.index) + `<numFmts count="${existing.length + 1}">${m[1] + tag}</numFmts>` + this.xml.slice(m.index + m[0].length)
    } else {
      this.xml = this.xml.replace(/(<styleSheet\b[^>]*>)/, (_all: string, open: string) => open + `<numFmts count="1">${tag}</numFmts>`)
    }
    this.memo.set(key, fid)
    return fid
  }

  font({ bold = false, italic = false, size = null as number | null, color = null as string | null } = {}): number {
    const key = JSON.stringify(['font', bold, italic, size, color])
    let id = this.memo.get(key)
    if (id === undefined) {
      let parts = (bold ? '<b/>' : '') + (italic ? '<i/>' : '')
      parts += `<sz val="${pyG(size || this.fontSize)}"/>`
      parts += color ? `<color rgb="FF${color}"/>` : '<color theme="1"/>'
      parts += `<name val="${attr(this.fontName)}"/><family val="2"/>`
      id = this.append('fonts', `<font>${parts}</font>`)
      this.memo.set(key, id)
    }
    return id
  }

  fill(rgb: string | null): number {
    if (rgb === null) return 0
    const key = JSON.stringify(['fill', rgb])
    let id = this.memo.get(key)
    if (id === undefined) {
      id = this.append('fills', `<fill><patternFill patternType="solid"><fgColor rgb="FF${rgb}"/><bgColor indexed="64"/></patternFill></fill>`)
      this.memo.set(key, id)
    }
    return id
  }

  border({ bottom = null as string | null, top = null as string | null, bottomStyle = 'thin', topStyle = 'thin' } = {}): number {
    if (bottom === null && top === null) return 0
    const key = JSON.stringify(['border', bottom, top, bottomStyle, topStyle])
    let id = this.memo.get(key)
    if (id === undefined) {
      const side = (tag: string, rgb: string | null, style: string) => (rgb ? `<${tag} style="${style}"><color rgb="FF${rgb}"/></${tag}>` : `<${tag}/>`)
      id = this.append('borders', '<border><left/><right/>' + side('top', top, topStyle) + side('bottom', bottom, bottomStyle) + '<diagonal/></border>')
      this.memo.set(key, id)
    }
    return id
  }

  /** A differential format (a conditional format's font), appended to <dxfs>. */
  dxf({ bold = false, color = null as string | null } = {}): number {
    const key = JSON.stringify(['dxf', bold, color])
    let id = this.memo.get(key)
    if (id === undefined) {
      if (!/<dxfs\b/.test(this.xml)) {               // every template has one; this is a guard
        const after = /<\/cellStyles>|<cellStyles\b[^>]*\/>/.exec(this.xml) ?? /<\/cellXfs>/.exec(this.xml)!
        const at = after.index + after[0].length
        this.xml = this.xml.slice(0, at) + '<dxfs count="0"/>' + this.xml.slice(at)
      }
      const font = (bold ? '<b/>' : '') + (color ? `<color rgb="FF${color}"/>` : '')
      id = this.append('dxfs', `<dxf><font>${font}</font></dxf>`)
      this.memo.set(key, id)
    }
    return id
  }

  xf({ font = 0, fill = 0, border = 0, numfmt = 0, halign = null as string | null, valign = 'center', wrap = false, indent = 0 } = {}): number {
    const key = JSON.stringify(['xf', font, fill, border, numfmt, halign, valign, wrap, indent])
    let id = this.memo.get(key)
    if (id === undefined) {
      let align = ` vertical="${valign}"` + (halign ? ` horizontal="${halign}"` : '')
      align += wrap ? ' wrapText="1"' : ''
      align += indent ? ` indent="${indent}"` : ''
      id = this.append('cellXfs', `<xf numFmtId="${numfmt}" fontId="${font}" fillId="${fill}" borderId="${border}" xfId="0" `
        + 'applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
        + `<alignment${align}/></xf>`)
      this.memo.set(key, id)
    }
    return id
  }

  save(): void {
    this.pkg.setText(STYLES, this.xml)
  }
}

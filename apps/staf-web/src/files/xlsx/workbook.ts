// The workbook part (libs/staf_workbook xlsx/workbook.py): sheets (order, names, visibility), defined
// names and the book view. A defined name's scope is held as the sheet's name while editing;
// localSheetId, activeTab and firstSheet are computed from the final order only when written, so
// reordering can never leave a stale index. Everything else in workbook.xml is kept as it was.
import { replaceAll, replaceFirst, xmlEscape, xmlUnescape } from '../py'
import { attr, attrsOf, CT_WORKSHEET, OFFDOC, Package, R_CALC_CHAIN, R_OFFICE_DOCUMENT, R_WORKSHEET, rel } from './package'

const UNESC = { '&quot;': '"', '&apos;': "'" }

export interface Sheet { name: string, sheetId: number, rid: string, part: string, state: string }

export interface DefinedName {
  name: string
  /** the formula, unescaped */
  text: string
  /** the sheet's name for a local name, null for the workbook */
  scope: string | null
  /** other attributes, in order (hidden, comment, ...) */
  extra: Map<string, string>
}

export const definedName = (name: string, text: string, scope: string | null = null, extra = new Map<string, string>()): DefinedName =>
  ({ name, text, scope, extra })

export class WorkbookModel {
  part: string
  xml: string
  rootDeclaresR: boolean
  sheets: Sheet[] = []
  names: DefinedName[] = []

  constructor(private pkg: Package) {
    const office = pkg.rels('').filter((r) => r.type === R_OFFICE_DOCUMENT)
    this.part = office.length ? Package.resolve('', office[0].target!) : 'xl/workbook.xml'
    const xml = pkg.text(this.part)
    this.xml = xml
    const root = /<workbook\b[^>]*>/.exec(xml)
    if (!root) throw new Error("AttributeError: 'NoneType' object has no attribute 'group'")
    this.rootDeclaresR = root[0].includes('xmlns:r="' + OFFDOC + '"')
    const rels = new Map(pkg.rels(this.part).map((r) => [r.id, r]))
    for (const m of (this.block('sheets') ?? '').matchAll(/<sheet\b[^>]*?\/>/g)) {
      const a = attrsOf(m[0])
      const rid = a.get('r:id')!
      const target = rels.get(rid)
      if (!target) throw new Error(`KeyError: '${rid}'`)
      const name = a.get('name')
      const sheetId = a.get('sheetId')
      if (name === undefined || sheetId === undefined) throw new Error('KeyError: a sheet without its name or sheetId')
      this.sheets.push({ name, sheetId: pyInt(sheetId), rid, part: Package.resolve(this.part, target.target!), state: a.get('state') ?? 'visible' })
    }
    for (const m of (this.block('definedNames') ?? '').matchAll(/(<definedName\b[^>]*?)(?:\/>|>([\s\S]*?)<\/definedName>)/g)) {
      const a = attrsOf(m[1] + '>')
      const local = a.get('localSheetId')
      a.delete('localSheetId')
      const name = a.get('name')
      if (name === undefined) throw new Error("KeyError: 'name'")
      a.delete('name')
      let scope: string | null = null
      if (local !== undefined) {
        const s = this.sheets[pyInt(local)]
        if (!s) throw new Error('IndexError: list index out of range')
        scope = s.name
      }
      this.names.push(definedName(name, xmlUnescape(m[2] ?? '', UNESC), scope, a))
    }
  }

  // ---------------------------------------------------------------- lookups
  private block(tag: string): string | null {
    const m = new RegExp(`<${tag}\\b[^>]*>([\\s\\S]*?)</${tag}>`).exec(this.xml)
    if (m) return m[1]
    return new RegExp(`<${tag}\\b[^>]*/>`).test(this.xml) ? '' : null
  }

  sheet(name: string): Sheet {
    const low = name.toLowerCase()
    for (const s of this.sheets) if (s.name.toLowerCase() === low) return s
    throw new Error(`KeyError: '${name}'`)
  }

  hasSheet(name: string): boolean {
    return this.sheets.some((s) => s.name.toLowerCase() === name.toLowerCase())
  }

  namesIn(sheet: string | null): DefinedName[] {
    return this.names.filter((n) => (n.scope ?? '').toLowerCase() === (sheet ?? '').toLowerCase())
  }

  // ---------------------------------------------------------------- edits
  rename(old: string, next: string): void {
    const s = this.sheet(old)
    for (const n of this.names) if (n.scope !== null && n.scope.toLowerCase() === s.name.toLowerCase()) n.scope = next
    s.name = next
  }

  /** A new worksheet part with its relationship and content type; placed last. */
  addSheet(name: string, xml: string, state = 'visible'): Sheet {
    const part = this.pkg.unique('xl/worksheets/sheet{}.xml')
    this.pkg.add(part, new TextEncoder().encode(xml), CT_WORKSHEET)
    const rels = this.pkg.rels(this.part)
    const rid = Package.nextRelId(rels)
    rels.push(rel(rid, R_WORKSHEET, Package.relative(this.part, part)))
    this.pkg.writeRels(this.part, rels)
    const sheet: Sheet = { name, sheetId: Math.max(...this.sheets.map((s) => s.sheetId), 0) + 1, rid, part, state }
    this.sheets.push(sheet)
    return sheet
  }

  reorder(names: string[]): void {
    const by = new Map(this.sheets.map((s) => [s.name.toLowerCase(), s]))
    const ordered = names.map((n) => {
      const s = by.get(n.toLowerCase())
      if (!s) throw new Error(`KeyError: '${n.toLowerCase()}'`)
      by.delete(n.toLowerCase())
      return s
    })
    this.sheets = [...ordered, ...by.values()]
  }

  removeCalcChain(): void {
    const rels = this.pkg.rels(this.part)
    const keep = rels.filter((r) => {
      if (r.type !== R_CALC_CHAIN) return true
      this.pkg.remove(Package.resolve(this.part, r.target!))
      return false
    })
    if (keep.length !== rels.length) this.pkg.writeRels(this.part, keep)
  }

  /** fullCalcOnLoad on the calculation properties (Excel recalculates every formula). */
  forceFullCalc(): void {
    const xml = this.xml
    const m = /<calcPr\b[^>]*?\/>/.exec(xml)
    if (m) {
      const tag = m[0]
      const next = replaceFirst(tag.replace(/\s(calcId|fullCalcOnLoad)="[^"]*"/g, ''), '<calcPr', '<calcPr calcId="0" fullCalcOnLoad="1"')
      this.xml = replaceFirst(xml, tag, next)
    } else {
      this.xml = replaceAll(xml, '</workbook>', '<calcPr calcId="0" fullCalcOnLoad="1"/></workbook>')
    }
  }

  // ---------------------------------------------------------------- writing
  serialize(active: string | null = null): void {
    const index = new Map(this.sheets.map((s, i) => [s.name.toLowerCase(), i]))
    const ns = this.rootDeclaresR ? '' : ` xmlns:r="${OFFDOC}"`
    const sheets = this.sheets.map((s) => `<sheet${ns} name="${attr(s.name)}" sheetId="${s.sheetId}"`
      + (s.state !== 'visible' ? ` state="${s.state}"` : '') + ` r:id="${attr(s.rid)}"/>`).join('')
    let xml = this.xml.replace(/<sheets\b[^>]*>[\s\S]*?<\/sheets>|<sheets\b[^>]*\/>/, () => `<sheets>${sheets}</sheets>`)
    const names = this.names.map((n) => {
      let local = ''
      if (n.scope !== null) {
        const i = index.get(n.scope.toLowerCase())
        if (i === undefined) throw new Error(`KeyError: '${n.scope.toLowerCase()}'`)
        local = ` localSheetId="${i}"`
      }
      const extra = [...n.extra].map(([k, v]) => ` ${k}="${attr(v)}"`).join('')
      return `<definedName name="${attr(n.name)}"${local}${extra}>${xmlEscape(n.text)}</definedName>`
    })
    const block = names.length ? `<definedNames>${names.join('')}</definedNames>` : ''
    if (/<definedNames\b/.test(xml)) xml = xml.replace(/<definedNames\b[^>]*>[\s\S]*?<\/definedNames>|<definedNames\b[^>]*\/>/, () => block)
    else if (block) xml = replaceFirst(xml, '</sheets>', '</sheets>' + block)
    if (active !== null) {
      const tab = index.get(active.toLowerCase())
      if (tab === undefined) throw new Error(`KeyError: '${active.toLowerCase()}'`)
      const m = /<workbookView\b[^>]*?\/?>/.exec(xml)
      if (m) {
        const tag = m[0]
        const next = replaceFirst(tag.replace(/\s(activeTab|firstSheet)="[^"]*"/g, ''), '<workbookView', `<workbookView firstSheet="0" activeTab="${tab}"`)
        xml = replaceFirst(xml, tag, next)
      }
    }
    this.xml = xml
    this.pkg.setText(this.part, xml)
  }
}

/** int(text) as Python reads a whole number (spaces and underscores allowed). */
export function pyInt(text: string): number {
  const t = text.trim()
  if (!/^[+-]?\d+(_\d+)*$/.test(t)) throw new Error(`ValueError: invalid literal for int() with base 10: '${text}'`)
  return Number(t.replace(/_/g, ''))
}

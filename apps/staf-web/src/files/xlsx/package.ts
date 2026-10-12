// An xlsx file as its parts (libs/staf_workbook xlsx/package.py): relationships, content types, and a
// writer that keeps every part it was not asked to change exactly as it was.
import { posix, reEscape, xmlEscape, xmlUnescape } from '../py'
import { infoOf, newInfo, readZip, ZIP_DEFLATED, type ZipInfo, ZipWriter } from '../zip'

export const CONTENT_TYPES = '[Content_Types].xml'
export const NS_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
export const OFFDOC = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
export const R_OFFICE_DOCUMENT = OFFDOC + '/officeDocument'
export const R_WORKSHEET = OFFDOC + '/worksheet'
export const R_DRAWING = OFFDOC + '/drawing'
export const R_CHART = OFFDOC + '/chart'
export const R_CALC_CHAIN = OFFDOC + '/calcChain'
export const R_PRINTER = OFFDOC + '/printerSettings'
export const CT_WORKSHEET = 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'
export const CT_DRAWING = 'application/vnd.openxmlformats-officedocument.drawing+xml'
export const CT_CHART = 'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
export const XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
const ATTR = /([A-Za-z_][\p{L}\p{N}_:.\-]*)\s*=\s*"([^"]*)"/gu
const UNESC = { '&quot;': '"', '&apos;': "'" }

/** attrs_of: one start tag's attributes, unescaped, in order (a repeated one keeps its first place). */
export function attrsOf(tagText: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const m of tagText.matchAll(ATTR)) out.set(m[1], xmlUnescape(m[2], UNESC))
  return out
}

/** attr: a value for an attribute (&, <, > and the double quote escaped). */
export const attr = (value: unknown): string => xmlEscape(String(value), { '"': '&quot;' })

export interface Rel { id: string | undefined, type: string | undefined, target: string | undefined, mode: string | null }
export const rel = (id: string, type: string, target: string, mode: string | null = null): Rel => ({ id, type, target, mode })

const decoder = new TextDecoder('utf-8', { fatal: true })

/** The parts of an xlsx file, in their original order. */
export class Package {
  infos = new Map<string, ZipInfo>()
  order: string[] = []
  parts = new Map<string, Uint8Array>()
  changed = new Set<string>()
  private texts = new Map<string, string>()

  /** Package(data): every part read (each member inflated). */
  static async open(data: Uint8Array): Promise<Package> {
    const pkg = new Package()
    const zip = readZip(data)
    for (const e of zip.entries) {
      pkg.infos.set(e.name, infoOf(e))
      pkg.order.push(e.name)
    }
    for (const name of pkg.order) pkg.parts.set(name, await zip.read(name))
    return pkg
  }

  // ---------------------------------------------------------------- parts
  has(name: string): boolean {
    return this.parts.has(name)
  }

  get(name: string): Uint8Array {
    const data = this.parts.get(name)
    if (data === undefined) throw new Error(`KeyError: '${name}'`)
    return data
  }

  /** The part as text (UTF-8, a byte-order mark dropped). */
  text(name: string): string {
    let t = this.texts.get(name)
    if (t === undefined) {
      t = decoder.decode(this.get(name))
      this.texts.set(name, t)
    }
    return t
  }

  set(name: string, data: Uint8Array): void {
    if (!this.parts.has(name)) this.order.push(name)
    this.parts.set(name, data)
    this.texts.delete(name)
    this.changed.add(name)
  }

  setText(name: string, text: string): void {
    this.set(name, new TextEncoder().encode(text))
    this.texts.set(name, text)
  }

  add(name: string, data: Uint8Array, contentType: string | null = null): void {
    if (this.parts.has(name)) throw new Error(`AssertionError: ${name}`)
    this.set(name, data)
    if (contentType && this.contentType(name) !== contentType) this.setOverride(name, contentType)
  }

  remove(name: string): void {
    if (this.parts.has(name)) {
      this.parts.delete(name)
      this.texts.delete(name)
      this.order.splice(this.order.indexOf(name), 1)
      this.changed.add(name)
    }
    this.removeOverride(name)
    const rels = Package.relsPart(name)
    if (this.parts.has(rels)) this.remove(rels)
  }

  /** The first free name for ``pattern`` ("xl/worksheets/sheet{}.xml"). */
  unique(pattern: string): string {
    let n = 1
    while (this.parts.has(pattern.replace('{}', String(n)))) n += 1
    return pattern.replace('{}', String(n))
  }

  // ---------------------------------------------------------------- relationships
  static relsPart(part: string): string {
    const [folder, name] = posix.split(part)
    return posix.join(folder, '_rels', name + '.rels')
  }

  rels(part: string): Rel[] {
    const name = Package.relsPart(part)
    if (!this.parts.has(name)) return []
    const out: Rel[] = []
    for (const m of this.text(name).matchAll(/<Relationship\b[^>]*?\/?>/g)) {
      const a = attrsOf(m[0])
      out.push({ id: a.get('Id'), type: a.get('Type'), target: a.get('Target'), mode: a.get('TargetMode') ?? null })
    }
    return out
  }

  writeRels(part: string, rels: Rel[]): void {
    const body = rels.map((r) => `<Relationship Id="${attr(r.id)}" Type="${attr(r.type)}" Target="${attr(r.target)}"`
      + (r.mode ? ` TargetMode="${attr(r.mode)}"` : '') + '/>').join('')
    this.setText(Package.relsPart(part), XML_DECL + `<Relationships xmlns="${NS_REL}">${body}</Relationships>`)
    if (this.contentType(Package.relsPart(part)) === null) {
      this.setOverride(Package.relsPart(part), 'application/vnd.openxmlformats-package.relationships+xml')
    }
  }

  /** A relationship target as a part name. */
  static resolve(part: string, target: string): string {
    if (target.startsWith('/')) return target.replace(/^\/+/, '')
    return posix.normpath(posix.join(posix.dirname(part), target))
  }

  static relative(fromPart: string, toPart: string): string {
    return posix.relpath(toPart, posix.dirname(fromPart) || '.')
  }

  static nextRelId(rels: Rel[]): string {
    const used = new Set(rels.map((r) => r.id))
    let n = 1
    while (used.has(`rId${n}`)) n += 1
    return `rId${n}`
  }

  // ---------------------------------------------------------------- content types
  private ct(): string {
    return this.text(CONTENT_TYPES)
  }

  contentType(name: string): string | null {
    const ct = this.ct()
    for (const m of ct.matchAll(/<Override\b[^>]*?\/?>/g)) {
      const a = attrsOf(m[0])
      if ((a.get('PartName') ?? '').replace(/^\/+/, '') === name) return a.get('ContentType') ?? null
    }
    const dot = name.lastIndexOf('.')
    const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
    for (const m of ct.matchAll(/<Default\b[^>]*?\/?>/g)) {
      const a = attrsOf(m[0])
      if ((a.get('Extension') ?? '').toLowerCase() === ext) return a.get('ContentType') ?? null
    }
    return null
  }

  setOverride(name: string, contentType: string): void {
    this.removeOverride(name)
    const tag = `<Override PartName="/${attr(name)}" ContentType="${attr(contentType)}"/>`
    this.setText(CONTENT_TYPES, this.ct().split('</Types>').join(tag + '</Types>'))
  }

  removeOverride(name: string): void {
    const ct = this.ct()
    const pattern = new RegExp(`<Override\\b[^>]*PartName="/?${reEscape(attr(name))}"[^>]*/>`, 'g')
    const next = ct.replace(pattern, () => '')
    if (next !== ct) this.setText(CONTENT_TYPES, next)
  }

  // ---------------------------------------------------------------- writing
  /** The package's bytes: an unchanged part with its own entry, a changed or new one deflated with the
   * template's date and attributes (a new one dated 1980-01-01). */
  write(): Uint8Array {
    const zf = new ZipWriter()
    for (const name of this.order) {
      const info = this.infos.get(name)
      if (info === undefined || this.changed.has(name)) {
        const fresh = newInfo(name, info)
        fresh.compressType = ZIP_DEFLATED
        fresh.externalAttr = info ? info.externalAttr : 0o600 << 16
        if (!fresh.externalAttr) fresh.externalAttr = 0o600 << 16
        zf.writestr(fresh, this.get(name))
      } else {
        const copy = { ...info }
        if (!copy.externalAttr) copy.externalAttr = 0o600 << 16
        zf.writestr(copy, this.get(name))
      }
    }
    return zf.finish()
  }
}


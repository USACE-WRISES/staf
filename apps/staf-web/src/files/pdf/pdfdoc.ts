// reportlab's pdfbase/pdfdoc.py, the parts the reports reach: the document's objects, numbered as they
// are referenced (a reference made while formatting gets the next number), formatted as reportlab writes
// them (dictionary keys sorted, six to a line; arrays ten to a line), page streams deflated and ASCII
// base-85 encoded, the cross-reference table, the trailer with the document's ID, the info dictionary
// and its dates (fixed in invariant mode). Output is a "binary string": one character per byte.
import { md5 } from '../bytes'
import { asciiBase85Encode, escapePDF, fpStr } from './accel'
import { codec, getFont } from './fonts'
import { deflateZlib } from './zlib'

export const PDF_VERSION_DEFAULT: [number, number] = [1, 3]

/** A PDF object: it formats itself (``refOnly`` objects are always referenced, never inlined). */
export abstract class PDFObject {
  internalName?: string
  refOnly = false
  abstract format(doc: PDFDocument): string
}

/** Python's str() of a number as reportlab's format() writes it (fp_str). */
export type Element = PDFObject | number | string | null | Element[]

/** format(element, document, toplevel): an element's PDF text. */
export function format(element: Element, doc: PDFDocument, toplevel = false): string {
  if (element instanceof PDFObject) {
    if (!toplevel && element.refOnly) return (doc.reference(element) as PDFObjectReference).format(doc)
    return element.format(doc)
  }
  if (typeof element === 'number') return fpStr(element)
  if (element === null) return 'None'
  if (Array.isArray(element)) throw new Error('a list is not a PDF element (wrap it in PDFArray)')
  return element
}

/** PDFName: /name, with characters outside ! .. ~ and the delimiters as #hex. */
export function pdfName(data: string): string {
  let out = '/'
  for (const c of data) {
    const cp = c.codePointAt(0)!
    out += cp < 0x21 || cp > 0x7e || '%()<>{}[]#'.includes(c) ? '#' + cp.toString(16) : c
  }
  return out
}

/** A name used as an element (PDFName returns a plain string in reportlab). */
export class Name extends PDFObject {
  constructor(readonly name: string) {
    super()
  }

  format(): string {
    return pdfName(this.name)
  }
}

/** PDFText: bytes as a hex string <...>. */
export class PDFText extends PDFObject {
  constructor(readonly t: Uint8Array) {
    super()
  }

  format(): string {
    let hex = ''
    for (const b of this.t) hex += b.toString(16).padStart(2, '0')
    return '<' + hex + '>'
  }
}

/** str.encode(codec) of a string the codec can encode wholly, else null. */
function encodeAll(s: string, name: string): Uint8Array | null {
  const table = codec(name)
  const out: number[] = []
  for (const ch of s) {
    const b = table.get(ch.codePointAt(0)!)
    if (b === undefined) return null
    out.push(b)
  }
  return Uint8Array.from(out)
}

/** UTF-16BE with its byte-order mark */
function utf16be(s: string): Uint8Array {
  const out = [0xfe, 0xff]
  for (let i = 0; i < s.length; i++) {
    const u = s.charCodeAt(i)
    out.push(u >> 8, u & 0xff)
  }
  return Uint8Array.from(out)
}

/** PDFString: text in PDFDocEncoding where it fits, else UTF-16BE with a byte-order mark; escaped. */
export class PDFString extends PDFObject {
  constructor(readonly s: string) {
    super()
  }

  format(): string {
    const bytes = encodeAll(this.s, 'pdfdoc') ?? utf16be(this.s)
    return '(' + escapePDF(bytes) + ')'
  }
}

/** PDFDictionary: keys sorted, broken into lines of six. */
export class PDFDictionary extends PDFObject {
  dict: Map<string, Element>
  multiline: boolean | 'forced' = true
  constructor(d: Map<string, Element> | Record<string, Element> | null = null) {
    super()
    this.dict = d instanceof Map ? new Map(d) : new Map(Object.entries(d ?? {}))
  }

  set(name: string, value: Element): void {
    this.dict.set(name, value)
  }

  format(doc: PDFDocument, ind = '\n '): string {
    const keys = [...this.dict.keys()].sort()
    let L = keys.map((k) => pdfName(k) + ' ' + format(this.dict.get(k)!, doc))
    let body: string
    if (this.multiline === 'forced') body = L.join(ind)
    else {
      L = [...L]
      for (let i = Math.floor((L.length - 1) / 6) * 6; i >= 6; i -= 6) L.splice(i, 0, '\n ')
      body = L.join(' ')
    }
    return '<<\n' + body + '\n>>'
  }
}

/** PDFArray: elements broken into lines of ten. */
export class PDFArray extends PDFObject {
  constructor(public sequence: Element[]) {
    super()
  }

  references(doc: PDFDocument): void {
    this.sequence = this.sequence.map((e) => doc.reference(e))
  }

  format(doc: PDFDocument): string {
    const L = this.sequence.map((e) => format(e, doc))
    const n = L.length
    if (n > 10) for (let i = Math.floor((n - 1) / 10) * 10; i >= 10; i -= 10) L.splice(i, 0, '\n ')
    return '[ ' + L.join(' ') + ' ]'
  }
}

export interface StreamFilter { pdfname: string, encode(content: Uint8Array | string): Uint8Array | string }
const binary = (s: string) => Uint8Array.from(s, (c) => c.charCodeAt(0))
const latin1 = (b: Uint8Array) => {
  let s = ''
  for (let i = 0; i < b.length; i += 8192) s += String.fromCharCode(...b.subarray(i, i + 8192))
  return s
}

/** zlib.compress (Flate). */
export const ZCompress: StreamFilter = {
  pdfname: 'FlateDecode',
  encode: (text) => deflateZlib(typeof text === 'string' ? new TextEncoder().encode(text) : text),
}
/** ASCII base-85 (unwrapped, as rl_config.wrapA85 = 0 leaves it). */
export const Base85Encode: StreamFilter = {
  pdfname: 'ASCII85Decode',
  encode: (text) => asciiBase85Encode(typeof text === 'string' ? binary(text) : text),
}

/** PDFStream: the dictionary (Filter, Length) and the content, filters applied in reverse order. */
export class PDFStream extends PDFObject {
  dictionary: PDFDictionary
  constructor(dictionary: PDFDictionary | null = null, public content: string | Uint8Array | null = null, public filters: StreamFilter[] | null = null) {
    super()
    this.refOnly = true
    this.dictionary = dictionary ?? new PDFDictionary()
  }

  format(doc: PDFDocument): string {
    const dictionary = new PDFDictionary(this.dictionary.dict)
    let content = this.content
    if (content === null) throw new Error('stream content not set')
    const filters = this.filters
    if (filters !== null && !dictionary.dict.has('Filter')) {
      const names: Element[] = []
      for (const f of [...filters].reverse()) {
        content = f.encode(content)
        names.unshift(new Name(f.pdfname))
      }
      dictionary.set('Filter', new PDFArray(names))
    }
    const fc = typeof content === 'string' ? content : latin1(content)
    dictionary.set('Length', fc.length)
    return format(dictionary, doc) + '\nstream\n' + fc + 'endstream\n'
  }
}

/** PDFIndirectObject: "n v obj ... endobj". */
class PDFIndirectObject extends PDFObject {
  constructor(readonly name: string, readonly content: PDFObject) {
    super()
    this.refOnly = true
  }

  format(doc: PDFDocument): string {
    const [n, v] = doc.idToObjectNumberAndVersion.get(this.name)!
    const fcontent = format(this.content, doc, true)
    return `${n} ${v} obj\n` + fcontent + (fcontent.endsWith('\n') ? '' : '\n') + 'endobj\n'
  }
}

/** PDFObjectReference: "n v R". */
export class PDFObjectReference extends PDFObject {
  constructor(readonly name: string) {
    super()
  }

  format(doc: PDFDocument): string {
    const nv = doc.idToObjectNumberAndVersion.get(this.name)
    if (!nv) throw new Error(`forward reference to '${this.name}' not resolved upon final formatting`)
    return `${nv[0]} ${nv[1]} R`
  }
}

/** PDFCatalog and its kin: attributes with defaults, some forced to references. */
class CatalogLike extends PDFObject {
  defaults: [string, Element][] = []
  noDefault: string[] = []
  refs: string[] = []
  attrs = new Map<string, Element>()

  checkFormat(_doc: PDFDocument): void {}

  format(doc: PDFDocument): string {
    this.checkFormat(doc)
    const D = new Map<string, Element>()
    for (const [k, v] of this.defaults) {
      const value = this.attrs.has(k) ? this.attrs.get(k)! : v
      if (value !== null && value !== undefined) D.set(k, value)
    }
    for (const k of this.noDefault) {
      const v = this.attrs.get(k)
      if (v !== null && v !== undefined) D.set(k, v)
    }
    for (const k of this.refs) if (D.has(k)) D.set(k, doc.reference(D.get(k)!))
    return format(new PDFDictionary(D), doc)
  }
}

export class PDFCatalog extends CatalogLike {
  constructor() {
    super()
    this.refOnly = true
    this.defaults = [['Type', new Name('Catalog')], ['PageMode', new Name('UseNone')], ['Lang', null]]
    this.noDefault = ('Dests Outlines Pages Threads AcroForm Names OpenAction PageMode URI ViewerPreferences PageLabels PageLayout '
      + 'JavaScript StructTreeRoot SpiderInfo MarkInfo Metadata Tabs').split(' ')
    this.refs = this.noDefault
  }
}

export class PDFPages extends CatalogLike {
  pages: PDFPage[] = []
  constructor() {
    super()
    this.refOnly = true
    this.defaults = [['Type', new Name('Pages')]]
    this.noDefault = ['Kids', 'Count', 'Parent']
    this.refs = ['Parent']
  }

  addPage(page: PDFPage): void {
    this.pages.push(page)
  }

  checkFormat(doc: PDFDocument): void {
    const kids = new PDFArray(this.pages)
    kids.references(doc)
    this.attrs.set('Kids', kids)
    this.attrs.set('Count', this.pages.length)
  }
}

/** PDFResourceDictionary: fonts, the procedure sets, XObjects and graphics states. */
export class PDFResourceDictionary extends PDFObject {
  ColorSpace: Map<string, Element> | Element = new Map()
  XObject: Map<string, Element> | Element = new Map()
  ExtGState: Map<string, Element> | Element = new Map()
  Font: Map<string, Element> | Element = new Map()
  Pattern: Map<string, Element> | Element = new Map()
  Properties: Map<string, Element> | Element = new Map()
  Shading: Map<string, Element> | Element = new Map()
  ProcSet: Element[] | Element = STDPROCS.slice(0, 2)

  allProcs(): void {
    this.ProcSet = STDPROCS
  }

  basicProcs(): void {
    this.ProcSet = STDPROCS.slice(0, 2)
  }

  basicFonts(): void {
    this.Font = new PDFObjectReference('BasicFonts')
  }

  format(doc: PDFDocument): string {
    const D = new Map<string, Element>()
    for (const dname of ['ColorSpace', 'XObject', 'ExtGState', 'Font', 'Pattern', 'Properties', 'Shading'] as const) {
      const v = this[dname]
      if (v instanceof Map) {
        if (v.size) D.set(dname, new PDFDictionary(v))
      } else D.set(dname, v)
    }
    const v = this.ProcSet
    if (Array.isArray(v)) {
      if (v.length) D.set('ProcSet', new PDFArray(v))
    } else D.set('ProcSet', v)
    return format(new PDFDictionary(D), doc)
  }
}
const STDPROCS: Element[] = 'PDF Text ImageB ImageC ImageI'.split(' ').map((s) => new Name(s))

export class PDFPage extends CatalogLike {
  pagewidth = 595
  pageheight = 842
  stream: string | null = null
  hasImages = 0
  compression = 0
  XObjects: PDFDictionary | null = null
  ExtGState: PDFDictionary | null = null
  constructor() {
    super()
    this.refOnly = true
    this.defaults = [['Type', new Name('Page')]]
    this.noDefault = ('Parent MediaBox Resources Contents CropBox Rotate Thumb Annots B Dur Hid Trans AA PieceInfo LastModified '
      + 'SeparationInfo ArtBox TrimBox BleedBox ID PZ Trans').split(' ')
    this.refs = ['Contents', 'Parent', 'ID']
  }

  setStream(code: string[]): void {
    this.stream = code.join('\n') + '\n'
  }

  checkFormat(doc: PDFDocument): void {
    const rotate = this.attrs.get('Rotate')
    if (!this.attrs.get('MediaBox')) {
      this.attrs.set('MediaBox', new PDFArray(rotate === 90 || rotate === 270 ? [0, 0, this.pageheight, this.pagewidth] : [0, 0, this.pagewidth, this.pageheight]))
    }
    const annots = this.attrs.get('Annots') as Element[] | PDFObject | null | undefined
    if (!annots || (Array.isArray(annots) && !annots.length)) this.attrs.set('Annots', null)
    else if (Array.isArray(annots)) this.attrs.set('Annots', new PDFArray(annots))
    if (!this.attrs.get('Contents')) {
      const S = new PDFStream()
      if (this.compression) S.filters = [Base85Encode, ZCompress]
      S.content = this.stream ?? ''
      this.attrs.set('Contents', S)
    }
    if (!this.attrs.get('Resources')) {
      const resources = new PDFResourceDictionary()
      resources.basicFonts()
      if (this.hasImages) resources.allProcs()
      else resources.basicProcs()
      if (this.XObjects) resources.XObject = this.XObjects
      if (this.ExtGState) resources.ExtGState = this.ExtGState
      this.attrs.set('Resources', resources)
    }
    if (!this.attrs.get('Parent')) this.attrs.set('Parent', doc.reference(doc.pages))
  }
}

/** PDFDate: D:YYYYMMDDhhmmss+hh'mm'. */
export interface TimeStamp { t: number, ymdhms: [number, number, number, number, number, number], dhh: number, dmm: number, tzname: string }

/** TimeStamp: 2000-01-01 UTC in invariant mode, else now in local time. */
export function timeStamp(invariant: boolean, now = new Date()): TimeStamp {
  if (invariant) return { t: 946684800.0, ymdhms: [2000, 1, 1, 0, 0, 0], dhh: 0, dmm: 0, tzname: 'UTC' }
  const off = -now.getTimezoneOffset() * 60
  return {
    t: now.getTime() / 1000, ymdhms: [now.getFullYear(), now.getMonth() + 1, now.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()],
    dhh: Math.trunc(off / 3600.0), dmm: (((off % 3600) + 3600) % 3600) % 60, tzname: '',
  }
}

const pad = (n: number, w: number) => String(Math.abs(Math.trunc(n))).padStart(w, '0')

class PDFDate extends PDFObject {
  constructor(readonly ts: TimeStamp) {
    super()
  }

  format(doc: PDFDocument): string {
    const [y, mo, d, h, mi, s] = this.ts.ymdhms
    const sign = this.ts.dhh < 0 ? '-' : '+'
    const text = `D:${pad(y, 4)}${pad(mo, 2)}${pad(d, 2)}${pad(h, 2)}${pad(mi, 2)}${pad(s, 2)}${sign}${pad(this.ts.dhh, 2)}'${pad(this.ts.dmm, 2)}'`
    return format(new PDFString(text), doc)
  }
}

/** PDFInfo: title, author, dates, producer, creator, subject, keywords, Trapped. */
export class PDFInfo extends PDFObject {
  producer = 'ReportLab PDF Library - (opensource)'
  creator = 'anonymous'
  title = 'untitled'
  author = 'anonymous'
  subject = 'unspecified'
  keywords = ''
  trapped = 'False'

  digestInto(parts: Uint8Array[]): void {
    for (const x of [this.title, this.author, this.subject, this.keywords]) parts.push(new TextEncoder().encode(x))
  }

  format(doc: PDFDocument): string {
    const date = new PDFDate(doc.timeStamp)
    const D = new Map<string, Element>([['Title', new PDFString(this.title)], ['Author', new PDFString(this.author)], ['ModDate', date],
      ['CreationDate', date], ['Producer', new PDFString(this.producer)], ['Creator', new PDFString(this.creator)],
      ['Subject', new PDFString(this.subject)], ['Keywords', new PDFString(this.keywords)], ['Trapped', new Name(this.trapped)]])
    return new PDFDictionary(D).format(doc)
  }
}

/** PDFType1Font: one of the standard 14 by name, with its encoding. */
export class PDFType1Font extends PDFObject {
  constructor(readonly internalFontName: string, readonly baseFont: string, readonly encoding: Element | null) {
    super()
    this.refOnly = true
  }

  format(doc: PDFDocument): string {
    const D = new Map<string, Element>([['Type', new Name('Font')], ['Subtype', new Name('Type1')], ['BaseFont', new Name(this.baseFont)],
      ['Name', new Name(this.internalFontName)]])
    if (this.encoding !== null) D.set('Encoding', this.encoding)
    return new PDFDictionary(D).format(doc)
  }
}

/** PDFImageXObject: an image's stream (its samples deflated and base-85 encoded, or a JPEG as it is). */
export class PDFImageXObject extends PDFObject {
  width = 0
  height = 0
  bitsPerComponent = 8
  colorSpace = 'DeviceRGB'
  filters: string[] = []
  streamContent = ''
  mask: number[] | null = null
  decode: number[] | null = null
  smask: Element | null = null
  /** the soft mask made from an image's alpha, registered by the canvas */
  pendingSmask: PDFImageXObject | null = null

  constructor(readonly name: string) {
    super()
  }

  format(doc: PDFDocument): string {
    const S = new PDFStream(null, this.streamContent)
    const d = S.dictionary
    d.set('Type', new Name('XObject'))
    d.set('Subtype', new Name('Image'))
    d.set('Width', this.width)
    d.set('Height', this.height)
    d.set('BitsPerComponent', this.bitsPerComponent)
    d.set('ColorSpace', new Name(this.colorSpace))
    if (this.decode) d.set('Decode', new PDFArray(this.decode))
    d.set('Filter', new PDFArray(this.filters.map((f) => new Name(f))))
    d.set('Length', this.streamContent.length)
    if (this.mask) d.set('Mask', new PDFArray(this.mask))
    if (this.smask) d.set('SMask', this.smask)
    return S.format(doc)
  }
}

/** The document: objects by internal name and number, fonts mapped to F1, F2, ..., the info. */
export class PDFDocument {
  objectcounter = 0
  pageCounter = 1
  idToObjectNumberAndVersion = new Map<string, [number, number]>()
  idToObject = new Map<string, PDFObject | Element>()
  idToOffset = new Map<string, number>()
  numberToId = new Map<number, string>()
  catalog = new PDFCatalog()
  pages = new PDFPages()
  info = new PDFInfo()
  fontMapping = new Map<string, string>()
  pdfVersion: [number, number]
  timeStamp: TimeStamp
  private signature: Uint8Array[] = []
  private id: string | null = null

  constructor(readonly compression: number, readonly invariant: boolean, pdfVersion: [number, number] = PDF_VERSION_DEFAULT) {
    this.pdfVersion = [...pdfVersion]
    this.timeStamp = timeStamp(invariant)
    this.signature.push(new TextEncoder().encode('a reportlab document'), new TextEncoder().encode(pyFloatRepr(this.timeStamp.t)))
    this.catalog.attrs.set('Pages', this.pages)
    const basic = new PDFDictionary()
    this.reference(basic, 'BasicFonts')
  }

  /** ensureMinPdfVersion('transparency') */
  ensureTransparency(): void {
    if (this.pdfVersion[0] < 1 || (this.pdfVersion[0] === 1 && this.pdfVersion[1] < 4)) this.pdfVersion = [1, 4]
  }

  /** Reference: the object's reference, numbering it when new. */
  reference(obj: Element | PDFObject, name: string | null = null): PDFObjectReference | Element {
    // a name is a plain string in reportlab (PDFName returns str): never numbered
    const iob = obj instanceof PDFObject && !(obj instanceof Name)
    if (name === null && (!iob || obj instanceof PDFObjectReference)) return obj
    if (iob && obj.internalName !== undefined) {
      const intname = obj.internalName
      if (name !== null && name !== intname) throw new Error(`attempt to reregister object '${intname}' with new name '${name}'`)
      return new PDFObjectReference(intname)
    }
    this.objectcounter += 1
    const n = name ?? 'R' + this.objectcounter
    if (this.idToObject.has(n)) {
      if (this.idToObject.get(n) !== obj) throw new Error('redefining named object: ' + JSON.stringify(n))
      return new PDFObjectReference(n)
    }
    if (iob) obj.internalName = n
    this.idToObjectNumberAndVersion.set(n, [this.objectcounter, 0])
    this.numberToId.set(this.objectcounter, n)
    this.idToObject.set(n, obj)
    return new PDFObjectReference(n)
  }

  /** getInternalFontName: /F1 for the first font used, and so on. */
  getInternalFontName(psfontname: string): string {
    const known = this.fontMapping.get(psfontname)
    if (known) return known
    const font = getFont(psfontname)
    const internal = 'F' + (this.fontMapping.size + 1)
    const encoding = ['WinAnsiEncoding', 'MacRomanEncoding', 'MacExpertEncoding'].includes(font.encName) ? new Name(font.encName) : null
    const pdfFont = new PDFType1Font(internal, font.fontName, encoding)
    this.reference(pdfFont, internal)
    ;(this.idToObject.get('BasicFonts') as PDFDictionary).set(internal, pdfFont)
    this.fontMapping.set(psfontname, '/' + internal)
    return '/' + internal
  }

  addPage(page: PDFPage): void {
    this.reference(page, 'Page' + this.pageCounter)
    this.pages.addPage(page)
    this.pageCounter += 1
  }

  /** addForm / getXObjectName: an XObject by its digest name. */
  xObjectName = (name: string): string => `FormXob.${name}`

  addAnnotation(name: string, annotation: PDFObject): void {
    this.reference(annotation, `Annot.${name}`)
  }

  setTitle(title: string | null): void {
    this.info.title = title ?? '(anonymous)'
  }

  setAuthor(author: string | null): void {
    this.info.author = author ?? '(anonymous)'
  }

  setSubject(subject: string | null): void {
    this.info.subject = subject ?? '(unspecified)'
  }

  setCreator(creator: string | null): void {
    this.info.creator = creator ?? '(unspecified)'
  }

  setKeywords(keywords: string | null): void {
    this.info.keywords = keywords ?? ''
  }

  setProducer(producer: string | null): void {
    this.info.producer = producer ?? 'ReportLab PDF Library - (opensource)'
  }

  private ID(): string {
    if (this.id) return this.id
    const all = new Uint8Array(this.signature.reduce((n, p) => n + p.length, 0))
    let o = 0
    for (const p of this.signature) {
      all.set(p, o)
      o += p.length
    }
    const ids = new PDFText(md5(all)).format()
    this.id = '\n[' + ids + ids + ']\n% ReportLab generated PDF document -- digest (opensource)\n'
    return this.id
  }

  /** GetPDFData: the whole file. */
  getPDFData(): Uint8Array {
    this.info.digestInto(this.signature)
    this.reference(this.catalog)
    this.reference(this.info)
    this.catalog.attrs.set('Outlines', null)               // no outline entries: no outline object
    return binary(this.formatAll())
  }

  private formatAll(): string {
    const cat = this.catalog
    const info = this.info
    this.reference(cat)
    this.reference(info)
    let counter = 0
    const ids: string[] = []
    const strings: string[] = []
    let offset = 0
    const add = (s: string): number => {
      const at = offset
      offset += s.length
      strings.push(s)
      return at
    }
    add(`%PDF-${this.pdfVersion[0]}.${this.pdfVersion[1]}\n%\x93\x8c\x8b\x9e ReportLab Generated PDF document (opensource)\n`)
    for (;;) {
      counter += 1
      const oid = this.numberToId.get(counter)
      if (oid === undefined) break
      const obj = this.idToObject.get(oid)!
      const IOf = new PDFIndirectObject(oid, obj as PDFObject).format(this)
      this.idToOffset.set(oid, add(IOf))
      ids.push(oid)
    }
    const lno = this.numberToId.size
    if (counter - 1 !== lno) throw new Error(`counter ${counter} doesn't match number to id dictionary ${lno}`)
    // the cross-reference table
    const entries = ['0000000000 65535 f ']
    for (const id of ids) entries.push(`${String(this.idToOffset.get(id)!).padStart(10, '0')} 00000 n `)
    const xrefoffset = add('xref\n' + [`0 ${ids.length + 1}`, ...entries, ''].join('\n'))
    // the trailer
    const T = new PDFDictionary(new Map<string, Element>([['Size', lno + 1], ['Root', this.reference(cat)], ['Info', this.reference(info)],
      ['ID', this.ID()]]))
    T.multiline = 'forced'
    add('trailer\n' + T.format(this, '\n') + '\nstartxref\n' + String(xrefoffset) + '\n%%EOF\n')
    return strings.join('')
  }
}

/** repr(float) of a timestamp (the signature's seed). */
function pyFloatRepr(t: number): string {
  if (Number.isInteger(t)) return `${t}.0`
  return String(t)
}

export { binary, latin1 }

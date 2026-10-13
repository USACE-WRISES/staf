// reportlab's pdfgen/canvas.py and textobject.py, what the reports and platypus reach: the page's
// operator lines as reportlab writes them (fp_str numbers; consecutive transforms merged into one cm;
// text runs per font, characters an encoding lacks drawn in Symbol or ZapfDingbats), the graphics state
// stack, colors (a Color's alpha declares transparency: the file becomes PDF 1.4), images as XObjects
// named by their data's MD5, links, and pages handed to the document.
import { md5Hex } from '../bytes'
import { escapePDF, fpStr } from './accel'
import { Color, toColor } from './colors'
import { getFont, stringWidth, unicode2T1 } from './fonts'
import type { DocContext } from './flowables'
import type { ImageData } from './images'
import {
  Base85Encode, type Element, Name, PDFArray, PDFDictionary, PDFDocument, PDFImageXObject, PDFObject, PDFObjectReference, PDFPage, PDFString,
} from './pdfdoc'

export type ColorLike = Color | string | number[]

const PATH_OPS: Record<string, string> = { '0,0,0': 'n', '0,0,1': 'n', '1,0,0': 'S', '1,0,1': 'S', '0,1,0': 'f*', '0,1,1': 'f', '1,1,0': 'B*', '1,1,1': 'B' }
const FILL_EVEN_ODD = 0

interface CodeTarget { code: string[] }

/** ExtGState: alpha and overprint as named graphics states (gRLs0, ...), emitted only on a change. */
class ExtGState {
  static defaults: Record<string, number | boolean | string> = { CA: 1, ca: 1, OP: false, op: false, OPM: 0, BM: 'Normal' }
  d = new Map<string, number | boolean | string>()
  c: Map<string, [string, string, Element]>

  constructor(c?: Map<string, [string, string, Element]>) {
    this.c = c ?? new Map()
  }

  set(target: CodeTarget, a: string, v: number): void {
    const dflt = ExtGState.defaults[a]
    const cur = this.d.has(a) ? this.d.get(a) : dflt
    if (v !== cur) {
      this.d.set(a, v)
      const key = `${a}\u0000${v}`
      let entry = this.c.get(key)
      if (!entry) {
        entry = ['gRLs' + this.c.size, a, v]
        this.c.set(key, entry)
      }
      target.code.push(`/${entry[0]} gs`)
    }
  }

  getState(): PDFDictionary | null {
    if (!this.c.size) return null
    const S = new Map<string, Element>()
    for (const [name, a, v] of this.c.values()) S.set(name, new PDFDictionary(new Map([[a, v]])))
    return new PDFDictionary(S)
  }

  pushCopy(): ExtGState {
    const x = new ExtGState(this.c)
    x.d = new Map(this.d)
    return x
  }
}

/** The color operators reportlab's _PDFColorSetter writes. */
function colorOps(target: CodeTarget & { doc: PDFDocument, setAlpha(kind: 'fill' | 'stroke', a: number): void }, aColor: ColorLike, kind: 'fill' | 'stroke'): ColorLike {
  const op = kind === 'fill' ? 'rg' : 'RG'
  if (aColor instanceof Color) {
    target.code.push(`${fpStr(aColor.red, aColor.green, aColor.blue)} ${op}`)
  } else if (Array.isArray(aColor)) {
    if (aColor.length !== 3) throw new Error(`Unknown color ${JSON.stringify(aColor)}`)
    target.code.push(`${fpStr(aColor)} ${op}`)
    return aColor
  } else {
    return colorOps(target, toColor(aColor), kind)
  }
  if (aColor.alpha !== null && aColor.alpha !== undefined) target.setAlpha(kind, aColor.alpha)
  return aColor
}

export class Canvas {
  doc: PDFDocument
  code: string[] = []
  pagesize: [number, number]
  bottomup = 1
  pageCompression: number
  pageNumber = 1
  private preamble = ''
  private formsinuse: string[] = []
  private annotationrefs: PDFObjectReference[] = []
  private annotationCount = 0
  // graphics state
  x = 0
  y = 0
  fontname = 'Helvetica'
  fontsize = 12
  leading = 14.399999999999999
  currentMatrix: [number, number, number, number, number, number] = [1, 0, 0, 1, 0, 0]
  fillMode = FILL_EVEN_ODD
  lineWidth = 1
  fillColorObj: ColorLike = [0, 0, 0]
  strokeColorObj: ColorLike = [0, 0, 0]
  extgstate = new ExtGState()
  private stateStack: Record<string, unknown>[] = []
  /** canvas._doctemplate: the document laying out flowables on this canvas, while it builds */
  doctemplate: DocContext | null = null

  constructor(pagesize: [number, number], { pageCompression = 1, invariant = false } = {}) {
    this.pagesize = pagesize
    this.pageCompression = pageCompression
    this.doc = new PDFDocument(pageCompression, invariant)
    this.initGraphicsState()
    this.makePreamble()
  }

  private initGraphicsState(): void {
    this.x = 0
    this.y = 0
    this.fontname = 'Helvetica'
    this.fontsize = 12
    this.leading = 12 * 1.2
    this.currentMatrix = [1, 0, 0, 1, 0, 0]
    this.fillMode = FILL_EVEN_ODD
    this.lineWidth = 1
    this.fillColorObj = this.strokeColorObj = [0, 0, 0]
    this.extgstate = new ExtGState()
  }

  private static STATE = ['x', 'y', 'fontname', 'fontsize', 'leading', 'currentMatrix', 'fillMode', 'lineWidth', 'fillColorObj', 'strokeColorObj',
    'extgstate'] as const

  private makePreamble(): void {
    const P = [this.bottomup ? '1 0 0 1 0 0 cm' : `1 0 0 -1 0 ${fpStr(this.pagesize[1])} cm`, '']
    P.push(`BT ${this.doc.getInternalFontName(this.fontname)} 12 Tf 14.4 TL ET`)
    this.preamble = P.join(' ')
  }

  // ---------------------------------------------------------------- info
  setTitle(title: string | null): void { this.doc.setTitle(title) }
  setAuthor(author: string | null): void { this.doc.setAuthor(author) }
  setSubject(subject: string | null): void { this.doc.setSubject(subject) }
  setCreator(creator: string | null): void { this.doc.setCreator(creator) }
  setKeywords(keywords: string | null): void { this.doc.setKeywords(keywords) }
  setProducer(producer: string | null): void { this.doc.setProducer(producer) }

  /** setPageRotation: the reports never rotate a page (reportlab writes /Rotate 0). */
  setPageRotation(rot: number): void {
    if (rot !== 0) throw new Error('page rotation is not supported here')
  }

  getPageNumber(): number {
    return this.pageNumber
  }

  // ---------------------------------------------------------------- alpha (canvas level)
  setAlpha(kind: 'fill' | 'stroke', a: number): void {
    if (!(typeof a === 'number' && a >= 0 && a <= 1)) throw new Error(`set${kind === 'fill' ? 'Fill' : 'Stroke'}Alpha invalid value ${a}`)
    this.doc.ensureTransparency()
    this.extgstate.set(this, kind === 'fill' ? 'ca' : 'CA', a)
  }

  setFillColor(aColor: ColorLike, alpha: number | null = null): void {
    this.fillColorObj = colorOps(this, aColor, 'fill')
    if (alpha !== null) this.setAlpha('fill', alpha)
  }

  setStrokeColor(aColor: ColorLike, alpha: number | null = null): void {
    this.strokeColorObj = colorOps(this, aColor, 'stroke')
    if (alpha !== null) this.setAlpha('stroke', alpha)
  }

  setFillGray(gray: number): void {
    this.fillColorObj = [gray, gray, gray]
    this.code.push(`${fpStr(gray)} g`)
  }

  setStrokeGray(gray: number): void {
    this.strokeColorObj = [gray, gray, gray]
    this.code.push(`${fpStr(gray)} G`)
  }

  // ---------------------------------------------------------------- pages
  showPage(): void {
    this.code.push(' ')
    const page = new PDFPage()
    page.pagewidth = this.pagesize[0]
    page.pageheight = this.pagesize[1]
    page.attrs.set('Rotate', 0)
    page.hasImages = 1
    page.attrs.set('Trans', new PDFDictionary(null))
    page.compression = this.pageCompression
    page.setStream([this.preamble, ...this.code])
    page.ExtGState = this.extgstate.getState()
    page.XObjects = this.formsinuse.length ? this.xobjDict(this.formsinuse) : null
    page.attrs.set('Annots', this.annotationrefs.length ? this.annotationrefs : null)
    this.doc.addPage(page)
    this.pageNumber += 1
    this.code = []
    this.formsinuse = []
    this.annotationrefs = []
    this.initGraphicsState()
    this.stateStack = []
  }

  private xobjDict(formnames: string[]): PDFDictionary {
    const D = new Map<string, Element>()
    for (const name of formnames) {
      const internal = this.doc.xObjectName(name)
      D.set(internal, new PDFObjectReference(internal))
    }
    return new PDFDictionary(D)
  }

  /** getpdfdata: the file (the open page shown first if it has anything on it). */
  getpdfdata(): Uint8Array {
    if (this.code.length) this.showPage()
    return this.doc.getPDFData()
  }

  // ---------------------------------------------------------------- transforms and state
  transform(a: number, b: number, c: number, d: number, e: number, f: number): void {
    const [a0, b0, c0, d0, e0, f0] = this.currentMatrix
    this.currentMatrix = [a0 * a + c0 * b, b0 * a + d0 * b, a0 * c + c0 * d, b0 * c + d0 * d, a0 * e + c0 * f + e0, b0 * e + d0 * f + f0]
    const last = this.code.length ? this.code[this.code.length - 1] : null
    if (last !== null && last.endsWith(' cm')) {
      const L = last.split(/\s+/).filter((s) => s)
      const [la, lb, lc, ld, le, lf] = L.slice(-7, -1).map(Number)
      if (L.length > 7) throw new Error("NameError: name 'join' is not defined")
      this.code[this.code.length - 1] = `${fpStr(la * a + lc * b, lb * a + ld * b, la * c + lc * d, lb * c + ld * d, la * e + lc * f + le, lb * e + ld * f + lf)} cm`
    } else {
      this.code.push(`${fpStr(a, b, c, d, e, f)} cm`)
    }
  }

  translate(dx: number, dy: number): void {
    this.transform(1, 0, 0, 1, dx, dy)
  }

  scale(x: number, y: number): void {
    this.transform(x, 0, 0, y, 0, 0)
  }

  saveState(): void {
    const state: Record<string, unknown> = {}
    for (const name of Canvas.STATE) state[name] = this[name]
    this.stateStack.push(state)
    this.extgstate = this.extgstate.pushCopy()
    this.code.push('q')
  }

  restoreState(): void {
    this.code.push('Q')
    Object.assign(this, this.stateStack.pop())
  }

  // ---------------------------------------------------------------- lines and shapes
  setLineWidth(width: number): void {
    this.lineWidth = width
    this.code.push(`${fpStr(width)} w`)
  }

  setLineCap(mode: number): void {
    this.code.push(`${Math.trunc(mode)} J`)
  }

  setLineJoin(mode: number): void {
    this.code.push(`${Math.trunc(mode)} j`)
  }

  /** setDash: the pattern with its phase written as Python's str() of it. */
  setDash(array: number[] | number = [], phase: number | string = 0): void {
    let arr: number[]
    if (typeof array === 'number') {
      arr = [array, phase as number]
      phase = 0
    } else arr = array
    this.code.push(`[${fpStr(arr)}] ${phase} d`)
  }

  line(x1: number, y1: number, x2: number, y2: number): void {
    this.code.push(`n ${fpStr(x1, y1)} m ${fpStr(x2, y2)} l S`)
  }

  lines(linelist: [number, number, number, number][]): void {
    this.code.push('n')
    for (const [x1, y1, x2, y2] of linelist) this.code.push(`${fpStr(x1, y1)} m ${fpStr(x2, y2)} l`)
    this.code.push('S')
  }

  rect(x: number, y: number, width: number, height: number, stroke = 1, fill = 0): void {
    this.code.push(`n ${fpStr(x, y, width, height)} re ` + PATH_OPS[`${stroke ? 1 : 0},${fill ? 1 : 0},${this.fillMode}`])
  }

  beginPath(): PathObject {
    return new PathObject()
  }

  drawPath(p: PathObject, stroke = 1, fill = 0): void {
    this.code.push(p.getCode())
    this.code.push(PATH_OPS[`${stroke ? 1 : 0},${fill ? 1 : 0},${this.fillMode}`])
  }

  // ---------------------------------------------------------------- text
  setFont(psfontname: string, size: number, leading: number | null = null): void {
    this.fontname = psfontname
    this.fontsize = size
    this.leading = leading ?? size * 1.2
    const pdffontname = this.doc.getInternalFontName(psfontname)
    this.code.push(`BT ${pdffontname} ${fpStr(size)} Tf ${fpStr(this.leading)} TL ET`)
  }

  stringWidth(text: string, fontName: string | null = null, fontSize: number | null = null): number {
    return stringWidth(text, fontName ?? this.fontname, fontSize ?? this.fontsize)
  }

  beginText(x = 0, y = 0): PDFTextObject {
    return new PDFTextObject(this, x, y)
  }

  drawText(t: PDFTextObject): void {
    this.code.push(t.getCode())
  }

  drawString(x: number, y: number, text: string): void {
    const t = this.beginText(x, y)
    t.textLine(text)
    this.drawText(t)
  }

  drawRightString(x: number, y: number, text: string): void {
    const width = stringWidth(text, this.fontname, this.fontsize)
    const t = this.beginText(x - width, y)
    t.textLine(text)
    this.drawText(t)
  }

  drawCentredString(x: number, y: number, text: string): void {
    const width = stringWidth(text, this.fontname, this.fontsize)
    const t = this.beginText(x - 0.5 * width, y)
    t.textLine(text)
    this.drawText(t)
  }

  // ---------------------------------------------------------------- images and links
  /** drawImage: the image as an XObject named by the MD5 of its samples (and its mask's), drawn into the
   * box; an image with alpha gets a soft mask (mask "auto"). */
  drawImage(image: ImageData, x: number, y: number, width: number | null = null, height: number | null = null, mask: string | null = null): [number, number] {
    const smaskData = image.alpha
    const mdata = mask === 'auto' && smaskData ? smaskData.data : new TextEncoder().encode(String(mask === null ? 'None' : mask))
    const all = new Uint8Array(image.data.length + mdata.length)
    all.set(image.data)
    all.set(mdata, image.data.length)
    const name = image.jpeg ? md5Hex(concatBytes(image.data, mdata)) : md5Hex(all)
    const regName = this.doc.xObjectName(name)
    let imgObj = this.doc.idToObject.get(regName) as PDFImageXObject | undefined
    if (!imgObj) {
      imgObj = imageXObject(name, image, mask)
      this.doc.reference(imgObj, regName)
      const smask = imgObj.pendingSmask
      if (smask) {
        const mRegName = this.doc.xObjectName(smask.name)
        if (!this.doc.idToObject.get(mRegName)) imgObj.smask = this.doc.reference(smask, mRegName)
        else imgObj.smask = new PDFObjectReference(mRegName)
        imgObj.pendingSmask = null
      }
    }
    const w = width ?? imgObj.width
    const h = height ?? imgObj.height
    this.saveState()
    this.translate(x, y)
    this.scale(w, h)
    this.code.push(`/${regName} Do`)
    this.restoreState()
    this.formsinuse.push(name)
    return [imgObj.width, imgObj.height]
  }

  /** linkURL: a URI link over ``rect`` (x1, y1, x2, y2), in page space unless ``relative``. */
  linkURL(url: string, rect: [number, number, number, number], relative = 0, thickness = 0): void {
    const ann = new PDFDictionary()
    ann.set('Type', new Name('Annot'))
    ann.set('Subtype', new Name('Link'))
    ann.set('Rect', new PDFArray(this.absRect(rect, relative)))
    const A = new PDFDictionary()
    A.set('Type', new Name('Action'))
    A.set('S', new Name('URI'))
    A.set('URI', new PDFString(url))
    ann.set('A', A)
    ann.set('Border', new PDFArray([0, 0, thickness || 0]))
    this.annotationCount += 1
    const name = 'NUMBER' + this.annotationCount
    const annObj = new AnnotationObject(ann)
    this.doc.addAnnotation(name, annObj)
    this.annotationrefs.push(new PDFObjectReference(`Annot.${name}`))
  }

  private absRect(rect: [number, number, number, number], relative: number): number[] {
    if (!relative) return [...rect]
    const [lx, ly, ux, uy] = rect
    const [xll, yll] = this.absolutePosition(lx, ly)
    const [xur, yur] = this.absolutePosition(ux, uy)
    const [xul, yul] = this.absolutePosition(lx, uy)
    const [xlr, ylr] = this.absolutePosition(ux, ly)
    const xs = [xll, xur, xul, xlr]
    const ys = [yll, yur, yul, ylr]
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  }

  absolutePosition(x: number, y: number): [number, number] {
    const [a, b, c, d, e, f] = this.currentMatrix
    return [a * x + c * y + e, b * x + d * y + f]
  }
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length)
  out.set(a)
  out.set(b, a.length)
  return out
}

/** A plain dictionary standing as its own indirect object (an annotation). */
class AnnotationObject extends PDFObject {
  constructor(readonly d: PDFDictionary) {
    super()
  }

  format(doc: PDFDocument): string {
    return this.d.format(doc)
  }
}

/** PDFImageXObject from an image's samples (loadImageFromSRC), with the alpha's soft mask. */
function imageXObject(name: string, image: ImageData, mask: string | null): PDFImageXObject {
  const x = new PDFImageXObject(name)
  x.width = image.width
  x.height = image.height
  if (image.jpeg) {                                    // loadImageFromJPEG: the file's own bytes
    x.bitsPerComponent = 8
    x.colorSpace = image.mode === 'L' ? 'DeviceGray' : image.mode === 'RGB' ? 'DeviceRGB' : 'DeviceCMYK'
    if (image.mode === 'CMYK') x.decode = [1, 0, 1, 0, 1, 0, 1, 0]   // _dotrans: Adobe's inverted CMYK
    x.streamContent = Base85Encode.encode(image.jpeg) as string
    x.filters = ['ASCII85Decode', 'DCTDecode']
    return x
  }
  x.streamContent = Base85Encode.encode(image.compressed()) as string
  x.filters = ['ASCII85Decode', 'FlateDecode']
  x.colorSpace = image.mode === 'L' ? 'DeviceGray' : image.mode === 'CMYK' ? 'DeviceCMYK' : 'DeviceRGB'
  x.bitsPerComponent = 8
  if (mask === 'auto') {
    if (image.alpha) {
      const sm = imageXObject(md5Hex(image.alpha.data), image.alpha, null)
      sm.decode = [0, 1]
      x.pendingSmask = sm
    }
  }
  return x
}

/** PDFPathObject: m, l, h; the first operator preceded by n. */
export class PathObject {
  private code: string[] = []

  private add(c: string): void {
    if (!this.code.length) this.code.push('n')
    this.code.push(c)
  }

  moveTo(x: number, y: number): void {
    this.add(`${fpStr(x, y)} m`)
  }

  lineTo(x: number, y: number): void {
    this.add(`${fpStr(x, y)} l`)
  }

  close(): void {
    this.add('h')
  }

  getCode(): string {
    return this.code.join(' ')
  }
}

/** PDFTextObject: BT ... ET, the cursor tracked, fonts switched per run. */
export class PDFTextObject {
  code: string[] = ['BT']
  fontname: string
  fontsize: number
  leading: number
  x0 = 0
  y0 = 0
  x = 0
  y = 0
  rise = 0
  /** _wordSpace: the last word spacing set (0 before any) */
  wordSpace = 0
  doc: PDFDocument

  constructor(readonly canvas: Canvas, x = 0, y = 0) {
    this.fontname = canvas.fontname
    this.fontsize = canvas.fontsize
    this.leading = canvas.leading
    this.doc = canvas.doc
    this.setTextOrigin(x, y)
  }

  getCode(): string {
    this.code.push('ET')
    return this.code.join(' ')
  }

  setTextOrigin(x: number, y: number): void {
    this.code.push(`1 0 0 1 ${fpStr(x, y)} Tm`)
    this.x0 = this.x = x
    this.y0 = this.y = y
  }

  moveCursor(dx: number, dy: number): void {
    const last = this.code[this.code.length - 1]
    if (this.code.length && last.endsWith(' Td')) {
      const L = last.split(/\s+/).filter((s) => s)
      if (L.length === 3) this.code.pop()
      else this.code[this.code.length - 1] = L.slice(0, -4).join('')
      const lastDx = Number(L[L.length - 3])
      const lastDy = Number(L[L.length - 2])
      dx += lastDx
      dy -= lastDy
      this.x0 -= lastDx
      this.y0 -= lastDy
    }
    this.code.push(`${fpStr(dx, -dy)} Td`)
    this.x0 += dx
    this.y0 += dy
    this.x = this.x0
    this.y = this.y0
  }

  setXPos(dx: number): void {
    this.moveCursor(dx, 0)
  }

  setFont(psfontname: string, size: number, leading: number | null = null): void {
    this.fontname = psfontname
    this.fontsize = size
    this.leading = leading ?? size * 1.2
    this.code.push(`${this.doc.getInternalFontName(psfontname)} ${fpStr(size)} Tf ${fpStr(this.leading)} TL`)
  }

  /** _setFont: the font and size without the leading. */
  setFontOnly(psfontname: string, size: number): void {
    this.fontname = psfontname
    this.fontsize = size
    this.code.push(`${this.doc.getInternalFontName(psfontname)} ${fpStr(size)} Tf`)
  }

  setCharSpace(charSpace: number): void {
    this.code.push(`${fpStr(charSpace)} Tc`)
  }

  setWordSpace(wordSpace: number): void {
    this.wordSpace = wordSpace
    this.code.push(`${fpStr(wordSpace)} Tw`)
  }

  setLeading(leading: number): void {
    this.leading = leading
    this.code.push(`${fpStr(leading)} TL`)
  }

  setRise(rise: number): void {
    const v = `${fpStr(rise)} Ts`
    if (this.code[this.code.length - 1].endsWith(' Ts')) {
      this.y += this.rise
      this.code[this.code.length - 1] = v
    } else {
      this.rise = rise
      this.y -= rise
      this.code.push(v)
    }
  }

  setAlpha(kind: 'fill' | 'stroke', a: number): void {
    if (!(typeof a === 'number' && a >= 0 && a <= 1)) throw new Error(`set alpha invalid value ${a}`)
    if (kind === 'stroke') return                 // a text object has no stroke alpha (reportlab's lambda: None)
    this.doc.ensureTransparency()
    this.canvas.extgstate.set(this, 'ca', a)
  }

  setFillColor(aColor: ColorLike): void {
    colorOps(this, aColor, 'fill')
  }

  setStrokeColor(aColor: ColorLike): void {
    colorOps(this, aColor, 'stroke')
  }

  /** _formatText: the text as runs per font, with Tf switches and back. */
  formatText(text: string): string {
    const font = getFont(this.fontname)
    const R: string[] = []
    let fc = font
    for (const [f, t] of unicode2T1(text, [font, ...font.substitutionFonts])) {
      if (f !== fc) {
        R.push(`${this.doc.getInternalFontName(f.fontName)} ${fpStr(this.fontsize)} Tf ${fpStr(this.leading)} TL`)
        fc = f
      }
      R.push(`(${escapePDF(t)}) Tj`)
    }
    if (font !== fc) R.push(`${this.doc.getInternalFontName(this.fontname)} ${fpStr(this.fontsize)} Tf ${fpStr(this.leading)} TL`)
    return R.join(' ')
  }

  /** _textOut: the text at the current point (T* after it when asked). */
  textOutRaw(text: string, tstar = false): void {
    this.code.push(this.formatText(text) + (tstar ? ' T*' : ''))
  }

  textOut(text: string): void {
    this.x = this.x + this.canvas.stringWidth(text, this.fontname, this.fontsize)
    this.code.push(this.formatText(text))
  }

  textLine(text = ''): void {
    this.x = this.x0
    this.y = this.canvas.bottomup ? this.y - this.leading : this.y + this.leading
    this.y0 = this.y
    this.code.push(`${this.formatText(text)} T*`)
  }
}

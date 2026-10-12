// reportlab's platypus/flowables.py (Flowable, Spacer, Image, KeepTogether, _listWrapOn) and the action
// flowables of doctemplate.py (NullActionFlowable, FrameBreak, PageBreak), kept here so the containers
// need no import cycle.
import type { Canvas } from './canvas'
import type { Frame } from './frames'
import type { ImageData } from './images'

export const FUZZ = 1e-6

/** what a flowable reads from the document while it is laid out (canvas._doctemplate) */
export interface DocContext {
  frame: Frame | null
  peekNextFrame(): Frame
  handleFrameEnd(): void
  handlePageBreak(): void
}

export abstract class Flowable {
  width = 0
  height = 0
  /** null where reportlab's flowable has no hAlign (Paragraph, Spacer): drawn where it stands */
  hAlign: string | null = null
  canv: Canvas | null = null
  _frame: Frame | null = null
  keepWithNext?: number | boolean
  spaceBefore?: number
  spaceAfter?: number
  /** ActionFlowable and its kind: handled by the document, never drawn */
  isAction = false
  /** FrameActionFlowable (Indenter): acts on the frame; containers skip it */
  isFrameAction = false
  _postponed = false

  wrap(_availWidth: number, _availHeight: number): [number, number] {
    return [this.width, this.height]
  }

  wrapOn(canv: Canvas | null, aW: number, aH: number): [number, number] {
    this.canv = canv
    const r = this.wrap(aW, aH)
    this.canv = null
    return r
  }

  split(_availWidth: number, _availHeight: number): Flowable[] {
    return []
  }

  splitOn(canv: Canvas | null, aW: number, aH: number): Flowable[] {
    this.canv = canv
    const r = this.split(aW, aH)
    this.canv = null
    return r
  }

  minWidth(): number {
    return this.width
  }

  draw(): void {}

  _drawOn(canv: Canvas): void {
    this.canv = canv
    this.draw()
    this.canv = null
  }

  hAlignAdjust(x: number, sW = 0): number {
    if (sW && this.hAlign !== null) {
      const a = this.hAlign
      if (a === 'CENTER' || a === 'CENTRE') x += 0.5 * sW
      else if (a === 'RIGHT') x += sW
      else if (a !== 'LEFT') throw new Error(`Bad hAlign value ${a}`)
    }
    return x
  }

  drawOn(canvas: Canvas, x: number, y: number, sW = 0): void {
    x = this.hAlignAdjust(x, sW)
    canvas.saveState()
    canvas.translate(x, y)
    this._drawOn(canvas)
    canvas.restoreState()
  }

  getKeepWithNext(): boolean {
    return !!this.keepWithNext
  }

  getSpaceAfter(): number {
    return this.spaceAfter ?? 0
  }

  getSpaceBefore(): number {
    return this.spaceBefore ?? 0
  }

  /** the document a flowable is being laid out in (canv._doctemplate) */
  get doctemplate(): DocContext | null {
    return this.canv?.doctemplate ?? null
  }
}

/** Spacer: takes up space, draws nothing. */
export class Spacer extends Flowable {
  constructor(width: number, height: number) {
    super()
    this.width = width
    this.height = height
  }
}

/** Image: a picture drawn at its size (or the size given), centered by default. */
export class Image extends Flowable {
  drawWidth: number
  drawHeight: number

  constructor(readonly image: ImageData, width: number | null = null, height: number | null = null, readonly mask: string | null = 'auto',
    hAlign = 'CENTER') {
    super()
    this.hAlign = hAlign
    this.drawWidth = width || image.width
    this.drawHeight = height || image.height
  }

  wrap(): [number, number] {
    return [this.drawWidth, this.drawHeight]
  }

  draw(): void {
    this.canv!.drawImage(this.image, 0, 0, this.drawWidth, this.drawHeight, this.mask)
  }
}

/** ActionFlowable: applied by the document, never drawn. */
export class ActionFlowable extends Flowable {
  isAction = true

  apply(_doc: DocContext): void {}
}

export class NullActionFlowable extends ActionFlowable {}

/** FrameBreak (doctemplate._FrameBreak('frameEnd')): ends the frame. */
export class FrameBreak extends ActionFlowable {
  apply(doc: DocContext): void {
    doc.handleFrameEnd()
  }

  wrap(): [number, number] {
    throw new Error('FrameBreak.wrap should never be called')
  }
}

/** PageBreak: ends the page. */
export class PageBreak extends Flowable {
  isPageBreak = true

  wrap(availWidth: number, availHeight: number): [number, number] {
    this.width = availWidth
    this.height = availHeight
    return [availWidth, availHeight - 1e-8]
  }
}

/** _listWrapOn: the widest width and the total height of flowables stacked, their spaces merged. */
export function listWrapOn(F: Flowable[], availWidth: number, canv: Canvas | null, mergeSpace = true,
  obj: { _spaceBefore?: number, _spaceAfter?: number } | null = null, dims: [number, number][] | null = null): [number, number] {
  let W = 0
  let H = 0
  let pS = 0
  let atTop = true
  for (const f of F) {
    if (f.isFrameAction) continue
    const [w, h0] = f.wrapOn(canv, availWidth, 0xfffffff)
    if (dims !== null) dims.push([w, h0])
    if (h0 <= FUZZ) continue
    W = Math.max(W, Math.min(w, availWidth))
    H += h0
    if (!atTop) {
      let h = f.getSpaceBefore()
      if (mergeSpace) h = Math.max(h - pS, 0)
      H += h
    } else {
      if (obj !== null) obj._spaceBefore = f.getSpaceBefore()
      atTop = false
    }
    pS = f.getSpaceAfter()
    H += pS
  }
  if (obj !== null) obj._spaceAfter = pS
  return [W, H - pS]
}

/** KeepTogether: its flowables on one frame when they fit; a frame break first when they do not. */
export class KeepTogether extends Flowable {
  content: Flowable[]
  private H = 0
  private H0 = 0
  private wrapInfo: [number, number] | null = null

  constructor(flowables: Flowable[], readonly maxHeight: number | null = null) {
    super()
    this.content = flowables.length ? flowables : [new NullActionFlowable()]
  }

  getSpaceBefore(): number {
    for (const c of this.content) if (!c.isFrameAction) return c.getSpaceBefore()
    return 0
  }

  getSpaceAfter(): number {
    for (let i = this.content.length - 1; i >= 0; i--) if (!this.content[i].isFrameAction) return this.content[i].getSpaceAfter()
    return 0
  }

  wrap(aW: number, aH: number): [number, number] {
    const dims: [number, number][] = []
    const [W, H] = listWrapOn(this.content, aW, this.canv, true, null, dims)
    this.H = H
    this.H0 = dims.length ? dims[0][1] : 0
    this.wrapInfo = [aW, aH]
    return [W, 0xffffff]
  }

  split(aW: number, aH: number): Flowable[] {
    if (!this.wrapInfo || this.wrapInfo[0] !== aW || this.wrapInfo[1] !== aH) this.wrap(aW, aH)
    const S = [...this.content]
    const cf = this._frame
    const atTop = cf ? cf.atTop : false
    const C0 = this.H > aH && (!this.maxHeight || aH > this.maxHeight)
    const C1 = this.H0 > aH || (C0 && atTop)
    if (C0 || C1) {
      let fb = false
      const doc = this.doctemplate
      let nf: Frame | null = null
      if (cf && doc) nf = doc.peekNextFrame()
      if (C0) fb = !(atTop && cf && nf && cf.width >= nf.width && cf.height >= nf.height)
      else if (nf && cf && nf.width >= cf.width && nf.height >= this.H) fb = true
      S.unshift(fb ? new FrameBreak() : new NullActionFlowable())
    }
    return S
  }
}

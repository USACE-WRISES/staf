// reportlab's platypus/doctemplate.py SimpleDocTemplate: one frame inside the margins, a "First" and a
// "Later" page template (their onPage callbacks drawn when a page begins), the story handled flowable by
// flowable (keepWithNext grouped into KeepTogether, a flowable that does not fit split or carried to the
// next frame), and the canvas set up as _makeCanvas sets it (info fields reset as reportlab resets them).
import { Canvas } from './canvas'
import { ActionFlowable, type DocContext, type Flowable, KeepTogether } from './flowables'
import { Frame } from './frames'

export const inch = 72.0
export const letter: [number, number] = [612.0, 792.0]

export type PageCallback = (canvas: Canvas, doc: SimpleDocTemplate) => void
const doNothing: PageCallback = () => {}

class PageTemplate {
  constructor(readonly id: string, readonly frames: Frame[], readonly onPage: PageCallback) {}
}

/** PageBegin: the hanging action that starts a page. */
class PageBegin extends ActionFlowable {
  locChanger = true

  apply(doc: DocContext): void {
    (doc as SimpleDocTemplate).handlePageBegin()
  }
}

export interface DocOptions {
  pagesize?: [number, number]
  title?: string | null
  author?: string | null
  subject?: string | null
  invariant?: boolean
  leftMargin?: number
  rightMargin?: number
  topMargin?: number
  bottomMargin?: number
  allowSplitting?: boolean
}

export class LayoutError extends Error {}

/** _ktAllow: may this flowable go inside a KeepTogether? */
const ktAllow = (f: Flowable) => !(f instanceof KeepTogether || (f as { locChanger?: boolean }).locChanger)

export class SimpleDocTemplate implements DocContext {
  pagesize: [number, number]
  title: string | null
  author: string | null
  subject: string | null
  invariant: boolean
  leftMargin: number
  rightMargin: number
  topMargin: number
  bottomMargin: number
  allowSplitting: boolean
  width = 0
  height = 0
  page = 0
  canv!: Canvas
  frame: Frame | null = null
  private pageTemplates: PageTemplate[] = []
  private pageTemplate!: PageTemplate
  private nextPageTemplateIndex: number | null = null
  private hanging: Flowable[] = []
  private curPageFlowableCount = 0
  private emptyPages = 0

  constructor(opts: DocOptions = {}) {
    this.pagesize = opts.pagesize ?? [595.2755905511812, 841.8897637795277]
    this.title = opts.title ?? null
    this.author = opts.author ?? null
    this.subject = opts.subject ?? null
    this.invariant = opts.invariant ?? false
    this.leftMargin = opts.leftMargin ?? inch
    this.rightMargin = opts.rightMargin ?? inch
    this.topMargin = opts.topMargin ?? inch
    this.bottomMargin = opts.bottomMargin ?? inch
    this.allowSplitting = opts.allowSplitting ?? true
    this.calc()
  }

  private calc(): void {
    const rightMargin = this.pagesize[0] - this.rightMargin
    const topMargin = this.pagesize[1] - this.topMargin
    this.width = rightMargin - this.leftMargin
    this.height = topMargin - this.bottomMargin
  }

  /** build: lays the story out and returns the PDF's bytes. */
  build(flowables: Flowable[], onFirstPage: PageCallback = doNothing, onLaterPages: PageCallback = doNothing): Uint8Array {
    this.calc()
    const frameT = new Frame(this.leftMargin, this.bottomMargin, this.width, this.height, { id: 'normal' })
    this.pageTemplates = [new PageTemplate('First', [frameT], onFirstPage), new PageTemplate('Later', [frameT], onLaterPages)]
    flowables = [...flowables]
    this.canv = this.makeCanvas()
    this.hanging = [new PageBegin()]
    this.pageTemplate = this.pageTemplates[0]
    this.page = 0
    this.canv.doctemplate = this
    try {
      while (flowables.length) {
        this.cleanHanging()
        this.handleFlowable(flowables)
      }
    } finally {
      this.canv.doctemplate = null
    }
    if (this.hanging.length && this.hanging[this.hanging.length - 1] instanceof PageBegin) {
      this.hanging.pop()
      this.cleanHanging()
    } else {
      this.cleanHanging()
      this.handlePageBreak()
    }
    return this.canv.getpdfdata()
  }

  private makeCanvas(): Canvas {
    const canv = new Canvas(this.pagesize, { invariant: this.invariant })
    canv.setAuthor(this.author)
    canv.setTitle(this.title)
    canv.setSubject(this.subject)
    canv.setCreator(null)
    canv.setProducer(null)
    canv.setKeywords('')
    return canv
  }

  private cleanHanging(): void {
    while (this.hanging.length) this.handleFlowable(this.hanging)
  }

  /** handle_pageBegin, then SimpleDocTemplate's change to the Later template */
  handlePageBegin(): void {
    this.page += 1
    this.pageTemplate.onPage(this.canv, this)
    for (const f of this.pageTemplate.frames) f.reset()
    this.curPageFlowableCount = 0
    this.frame = this.pageTemplate.frames[0]
    this.nextPageTemplateIndex = 1
  }

  handlePageEnd(): void {
    if (this.curPageFlowableCount === 0) this.emptyPages += 1
    else this.emptyPages = 0
    if (this.emptyPages >= 10) {
      throw new LayoutError('More than 10 pages generated without content - halting layout.  Likely that a flowable is too large for any frame.')
    }
    this.canv.setPageRotation(0)
    this.canv.showPage()
    if (this.nextPageTemplateIndex !== null) {
      this.pageTemplate = this.pageTemplates[this.nextPageTemplateIndex]
      this.nextPageTemplateIndex = null
    }
    this.hanging.push(new PageBegin())
  }

  handlePageBreak(): void {
    this.handlePageEnd()
  }

  handleFrameEnd(): void {
    // one frame per page: its end is the page's end
    this.handlePageEnd()
    this.frame = null
  }

  /** _peekNextFrame: the frame the next frame break leads to (the next page's first) */
  peekNextFrame(): Frame {
    const pt = this.nextPageTemplateIndex !== null ? this.pageTemplates[this.nextPageTemplateIndex] : this.pageTemplate
    return pt.frames[0]
  }

  private handleKeepWithNext(flowables: Flowable[]): void {
    let i = 0
    const n = flowables.length
    while (i < n && flowables[i].getKeepWithNext() && ktAllow(flowables[i])) i += 1
    if (i) {
      if (i < n && ktAllow(flowables[i])) i += 1
      const K = new KeepTogether(flowables.slice(0, i))
      for (const f of K.content.slice(0, -1)) f.keepWithNext = 0
      flowables.splice(0, i, K)
    }
  }

  private handleFlowable(flowables: Flowable[]): void {
    this.handleKeepWithNext(flowables)
    const f = flowables.shift()!
    if ((f as { isPageBreak?: boolean }).isPageBreak) {
      this.handlePageBreak()
    } else if (f instanceof ActionFlowable) {
      f.apply(this)
    } else {
      const frame = this.frame!
      const canv = this.canv
      if (frame.add(f, canv)) {
        this.curPageFlowableCount += 1
      } else {
        const S = this.allowSplitting ? frame.split(f, canv) : []
        if (S.length) {
          if (!(S[0] instanceof ActionFlowable) && !(S[0] as { isPageBreak?: boolean }).isPageBreak) {
            if (!frame.add(S[0], canv)) throw new LayoutError(`Splitting error(n==${S.length}) on page ${this.page}`)
            this.curPageFlowableCount += 1
            flowables.unshift(...S.slice(1))
          } else flowables.unshift(...S)
        } else {
          if (f._postponed) throw new LayoutError(`Flowable too large on page ${this.page}`)
          f._postponed = true
          flowables.unshift(f)
          this.handleFrameEnd()
        }
      }
    }
  }
}

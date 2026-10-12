// reportlab's platypus/frames.py: a frame fills from the top, merging each flowable's space before with
// the last one's space after (overlapAttachedSpace), and draws what fits at its left edge.
import type { Canvas } from './canvas'
import { type Flowable, FUZZ } from './flowables'

export class Frame {
  x1: number
  y1: number
  width: number
  height: number
  leftPadding: number
  bottomPadding: number
  rightPadding: number
  topPadding: number
  x2 = 0
  y2 = 0
  y1p = 0
  aW = 0
  aH = 0
  x = 0
  y = 0
  atTop = true
  prevASpace = 0
  leftExtraIndent = 0
  rightExtraIndent = 0

  constructor(x1: number, y1: number, width: number, height: number,
    { leftPadding = 6, bottomPadding = 6, rightPadding = 6, topPadding = 6, id = null as string | null } = {}) {
    void id
    this.x1 = x1
    this.y1 = y1
    this.width = width
    this.height = height
    this.leftPadding = leftPadding
    this.bottomPadding = bottomPadding
    this.rightPadding = rightPadding
    this.topPadding = topPadding
    this.geom()
    this.reset()
  }

  private geom(): void {
    this.x2 = this.x1 + this.width
    this.y2 = this.y1 + this.height
    this.y1p = this.y1 + this.bottomPadding
    this.aW = this.x2 - this.x1 - this.leftPadding - this.rightPadding
    this.aH = this.y2 - this.y1p - this.topPadding
  }

  reset(): void {
    this.x = this.x1 + this.leftPadding
    this.y = this.y2 - this.topPadding
    this.atTop = true
    this.prevASpace = 0
    this.leftExtraIndent = 0.0
    this.rightExtraIndent = 0.0
  }

  availableWidth(): number {
    return this.aW - this.leftExtraIndent - this.rightExtraIndent
  }

  /** _add: draws the flowable here if it fits (true), else nothing (false). */
  add(flowable: Flowable, canv: Canvas): boolean {
    flowable._frame = this
    flowable.canv = canv
    try {
      let y = this.y
      const p = this.y1p
      let s = 0
      const aW = this.availableWidth()
      if (!this.atTop) {
        s = flowable.getSpaceBefore()
        s = Math.max(s - this.prevASpace, 0)
      }
      let h = y - p - s
      let w: number
      if (h > 0) [w, h] = flowable.wrap(aW, h)
      else return false
      h += s
      y -= h
      if (y < p - FUZZ) return false
      const sa = flowable.getSpaceAfter()
      flowable.drawOn(canv, this.x + this.leftExtraIndent, y, aW - w)
      flowable.canv = canv
      y -= sa
      this.prevASpace = sa
      if (y !== this.y) this.atTop = false
      this.y = y
      return true
    } finally {
      flowable.canv = null
      flowable._frame = null
    }
  }

  /** split: asks the flowable to split into the space left. */
  split(flowable: Flowable, canv: Canvas): Flowable[] {
    const y = this.y
    const p = this.y1p
    let s = 0
    if (!this.atTop) {
      s = flowable.getSpaceBefore()
      s = Math.max(s - this.prevASpace, 0)
    }
    const h = y - p - s
    if (h <= 0) return []
    flowable._frame = this
    flowable.canv = canv
    try {
      return flowable.split(this.aW, h)
    } finally {
      flowable.canv = null
      flowable._frame = null
    }
  }
}

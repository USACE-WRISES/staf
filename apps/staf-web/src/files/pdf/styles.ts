// reportlab's lib/styles.py ParagraphStyle and lib/enums.py: the defaults, a parent's attributes copied
// at construction (PropertySet.refresh), keywords last.
import { black, type Color } from './colors'

export const TA_LEFT = 0
export const TA_CENTER = 1
export const TA_RIGHT = 2
export const TA_JUSTIFY = 4

export interface ParagraphStyleAttrs {
  fontName?: string
  fontSize?: number
  leading?: number
  leftIndent?: number
  rightIndent?: number
  firstLineIndent?: number
  alignment?: number
  spaceBefore?: number
  spaceAfter?: number
  bulletFontName?: string
  bulletFontSize?: number
  bulletIndent?: number
  textColor?: Color
  backColor?: Color | null
  wordWrap?: string | null
  borderWidth?: number
  borderPadding?: number
  borderColor?: Color | null
  allowWidows?: number
  allowOrphans?: number
  splitLongWords?: number
  justifyLastLine?: number
  justifyBreaks?: number
  spaceShrinkage?: number
  linkUnderline?: number
  keepWithNext?: number | boolean
  endDots?: string | null
}

export class ParagraphStyle implements Required<Omit<ParagraphStyleAttrs, 'keepWithNext'>> {
  name: string
  parent: ParagraphStyle | null
  fontName = 'Helvetica'
  fontSize = 10
  leading = 12
  leftIndent = 0
  rightIndent = 0
  firstLineIndent = 0
  alignment = TA_LEFT
  spaceBefore = 0
  spaceAfter = 0
  bulletFontName = 'Helvetica'
  bulletFontSize = 10
  bulletIndent = 0
  textColor: Color = black
  backColor: Color | null = null
  wordWrap: string | null = null
  borderWidth = 0
  borderPadding = 0
  borderColor: Color | null = null
  allowWidows = 1
  allowOrphans = 0
  splitLongWords = 1
  justifyLastLine = 0
  justifyBreaks = 0
  spaceShrinkage = 0.05
  linkUnderline = 0
  endDots: string | null = null
  keepWithNext?: number | boolean
  [k: string]: unknown

  constructor(name: string, attrs: ParagraphStyleAttrs & { parent?: ParagraphStyle | null } = {}) {
    const { parent = null, ...kw } = attrs
    this.name = name
    this.parent = parent
    if (parent) {
      for (const [k, v] of Object.entries(parent)) if (k !== 'name' && k !== 'parent') this[k] = v
    }
    Object.assign(this, kw)
  }

  /** deepcopy(style) with some attributes changed (Paragraph.split's firstLineIndent=0) */
  clone(kw: ParagraphStyleAttrs = {}): ParagraphStyle {
    const r = new ParagraphStyle(this.name)
    for (const [k, v] of Object.entries(this)) r[k] = v
    return Object.assign(r, kw)
  }
}

/** getSampleStyleSheet: reportlab's sample styles by name (a fresh set each call, as reportlab builds it). */
export function getSampleStyleSheet(): Record<string, ParagraphStyle> {
  const S: Record<string, ParagraphStyle> = {}
  const add = (s: ParagraphStyle) => {
    S[s.name] = s
  }
  add(new ParagraphStyle('Normal', { fontName: 'Helvetica', fontSize: 10, leading: 12 }))
  add(new ParagraphStyle('BodyText', { parent: S.Normal, spaceBefore: 6 }))
  add(new ParagraphStyle('Italic', { parent: S.BodyText, fontName: 'Helvetica-Oblique' }))
  add(new ParagraphStyle('Heading1', { parent: S.Normal, fontName: 'Helvetica-Bold', fontSize: 18, leading: 22, spaceAfter: 6 }))
  add(new ParagraphStyle('Title', { parent: S.Normal, fontName: 'Helvetica-Bold', fontSize: 18, leading: 22, alignment: TA_CENTER, spaceAfter: 6 }))
  add(new ParagraphStyle('Heading2', { parent: S.Normal, fontName: 'Helvetica-Bold', fontSize: 14, leading: 18, spaceBefore: 12, spaceAfter: 6 }))
  add(new ParagraphStyle('Heading3', { parent: S.Normal, fontName: 'Helvetica-BoldOblique', fontSize: 12, leading: 14, spaceBefore: 12, spaceAfter: 6 }))
  add(new ParagraphStyle('Heading4', { parent: S.Normal, fontName: 'Helvetica-BoldOblique', fontSize: 10, leading: 12, spaceBefore: 10, spaceAfter: 4 }))
  add(new ParagraphStyle('Heading5', { parent: S.Normal, fontName: 'Helvetica-Bold', fontSize: 9, leading: 10.8, spaceBefore: 8, spaceAfter: 4 }))
  add(new ParagraphStyle('Heading6', { parent: S.Normal, fontName: 'Helvetica-Bold', fontSize: 7, leading: 8.4, spaceBefore: 6, spaceAfter: 2 }))
  add(new ParagraphStyle('Bullet', { parent: S.Normal, firstLineIndent: 0, spaceBefore: 3 }))
  add(new ParagraphStyle('Definition', { parent: S.Normal, firstLineIndent: 0, leftIndent: 36, bulletIndent: 0, spaceBefore: 6,
    bulletFontName: 'Helvetica-BoldOblique' }))
  add(new ParagraphStyle('Code', { parent: S.Normal, fontName: 'Courier', fontSize: 8, leading: 8.8, firstLineIndent: 0, leftIndent: 36 }))
  return S
}

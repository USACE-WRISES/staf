// reportlab's platypus/paragraph.py, the paths the reports take: whitespace cleaned as
// cleanBlockQuotedText does, lines broken greedily with reportlab's space shrinkage (one style: kind 0;
// several: kind 1, frag words), words wider than the line split by characters, the four alignments,
// links, and a paragraph split between frames by lines. Soft hyphens, hyphenation, bullets, CJK and
// right-to-left text are not ported (the reports have none).
import { pySplit } from '../py'
import { type Canvas, PDFTextObject } from './canvas'
import { getAscentDescent, stringWidth } from './fonts'
import { Flowable } from './flowables'
import { colorEq, Frag, parseParagraph, sameFrag } from './paraparser'
import { type ParagraphStyle, TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT } from './styles'

const FUZZ = 1e-6
/** reportlab's own whitespace set (no-break space excluded) */
const WSC = '\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u2028\u2029\u202f\u205f\u3000'
const WSC_RUN = new RegExp(`[${WSC.replace(/[\\\]^-]/g, '\\$&')}]+`)
const WSC_END = new RegExp(`[${WSC.replace(/[\\\]^-]/g, '\\$&')}]+$`)
const ASCII_WS = ' \t\n\r\x0b\x0c'

/** paragraph.split: str.split(), or reportlab's split when a no-break space must not separate words */
export function split(text: string, delim: string | null = null): string[] {
  if (delim === null) return text.includes('\u00a0') ? text.split(WSC_RUN) : pySplit(text)
  return text.split(delim)
}

const isWsc = (c: string) => WSC.includes(c)

export function strip(text: string): string {
  let a = 0
  let b = text.length
  while (a < b && isWsc(text[a])) a++
  while (b > a && isWsc(text[b - 1])) b--
  return text.slice(a, b)
}

function lstrip(text: string): string {
  let a = 0
  while (a < text.length && isWsc(text[a])) a++
  return text.slice(a)
}

/** cleanBlockQuotedText: each line's whitespace collapsed, empty lines dropped, joined by spaces. */
export function cleanBlockQuotedText(text: string, joiner = ' '): string {
  const lineClean = (L: string) => split(strip(L)).filter((s) => s).join(' ')
  return split(text, '\n').map(lineClean).filter((s) => s).join(joiner)
}

// ---------------------------------------------------------------- words of a kind-0 paragraph
/** _SplitWord / _SplitWordEnd: a piece of a word split across lines */
export class SplitWord {
  constructor(readonly s: string, readonly end = false) {}
}
export type Word = string | SplitWord
const wText = (w: Word) => (typeof w === 'string' ? w : w.s)

// ---------------------------------------------------------------- frag words of a kind-1 paragraph
export type WordKind = 'plain' | 'HS' | 'Injected' | 'Split' | 'SplitH' | 'SplitHS' | 'SplitLL'
export interface FragWord { w: number, parts: [Frag, string][], kind: WordKind }
const isHS = (w: FragWord) => w.kind === 'HS' || w.kind === 'SplitHS' || w.kind === 'SplitLL'
const isSplit = (w: FragWord) => w.kind === 'Split' || w.kind === 'SplitH' || w.kind === 'SplitHS' || w.kind === 'SplitLL'
const isSplitH = (w: FragWord) => w.kind === 'SplitH'
const FK_TEXT = 0
const FK_BREAK = 3

/** _getFragWords: the frags as words (width and styled pieces); a word followed by a space is HS. */
export function getFragWords(frags: Frag[] | FragWord[], processed: boolean): FragWord[] {
  const R: FragWord[] = []
  if (processed) {
    let W: FragWord[] = []
    const rejoin = (F: FragWord[]): FragWord => {
      const parts: [Frag, string][] = []
      let wLen = 0
      let psty: Frag | null = null
      for (const f of F) {
        wLen += f.w
        for (const [sty, t] of f.parts) {
          if (psty === sty) parts[parts.length - 1] = [sty, parts[parts.length - 1][1] + t]
          else {
            parts.push([sty, t])
            psty = sty
          }
        }
      }
      const last = F[F.length - 1]
      return { w: wLen, parts, kind: last.kind === 'SplitLL' ? 'SplitLL' : isHS(last) ? 'HS' : 'plain' }
    }
    for (const f of frags as FragWord[]) {
      if (f.kind === 'Injected') continue
      if (isSplit(f)) {
        W.push(f)
        if (isHS(f)) {
          R.push(rejoin(W))
          W = []
        }
      } else {
        if (W.length) {
          R.push(rejoin(W))
          W = []
        }
        R.push(f)
      }
    }
    if (W.length) R.push(rejoin(W))
    return R
  }
  let W: [Frag, string][] = []
  let hangingSpace = false
  let n = 0
  let hangingStrip = true
  for (const f of frags as Frag[]) {
    let text = f.text ?? ''
    if (text !== '') {
      f._fkind = FK_TEXT
      if (hangingStrip) {
        text = lstrip(text)
        if (!text) continue
        hangingStrip = false
      }
      const S = split(text)
      if (ASCII_WS.includes(text[0]) || !S.length) {
        let whs: boolean
        if (W.length) {
          R.push({ w: n, parts: W, kind: 'plain' })
          whs = hangingSpace
          W = []
          hangingSpace = false
          n = 0
        } else whs = R.length > 0 && isHS(R[R.length - 1])
        if (!whs) S.unshift('')
        else if (!S.length) continue
      }
      for (const w of S.slice(0, -1)) {
        W.push([f, w])
        n += stringWidth(w, f.fontName, f.fontSize)
        R.push({ w: n, parts: W, kind: 'HS' })
        W = []
        n = 0
      }
      hangingSpace = false
      const w = S[S.length - 1]
      W.push([f, w])
      n += stringWidth(w, f.fontName, f.fontSize)
      if (text && ASCII_WS.includes(text[text.length - 1])) {
        R.push({ w: n, parts: W, kind: 'HS' })
        W = []
        n = 0
      }
    } else if (f.cbDefn !== undefined) {
      throw new Error('paragraph callbacks are not supported here')
    } else if (f.lineBreak !== undefined) {
      if (W.length) {
        R.push({ w: n, parts: W, kind: 'plain' })
        W = []
        n = 0
        hangingSpace = false
      }
      f._fkind = FK_BREAK
      R.push({ w: 0, parts: [[f, '']], kind: 'plain' })
      hangingStrip = true
    }
  }
  if (W.length) R.push({ w: n, parts: W, kind: 'plain' })
  if (!R.length && frags.length) {
    const f = frags[0] as Frag
    f._fkind = FK_TEXT
    return [{ w: 0, parts: [[f, '']], kind: 'plain' }]
  }
  return R
}

/** _splitWord: a word too wide for any line, cut by characters into pieces that fit. */
function splitWord(w: string, lineWidth: number, maxWidths: number[], lineno: number, fontName: string, fontSize: number): Word[] {
  const R: Word[] = []
  const maxlineno = maxWidths.length - 1
  let wordText = ''
  let maxWidth = maxWidths[Math.min(maxlineno, lineno)]
  const maxWidthNext = maxWidths[Math.min(maxlineno, lineno + 1)]
  for (const c of w) {
    const cw = stringWidth(c, fontName, fontSize)
    let newLineWidth = lineWidth + cw
    if (newLineWidth > maxWidth && (wordText || (!wordText && cw <= maxWidthNext))) {
      R.push(new SplitWord(wordText))
      lineno += 1
      maxWidth = maxWidths[Math.min(maxlineno, lineno)]
      newLineWidth = cw
      wordText = ''
    }
    wordText += c
    lineWidth = newLineWidth
  }
  R.push(new SplitWord(wordText, true))
  return R
}

/** _splitFragWord: a frag word cut by characters into pieces that fit. */
function splitFragWord(w: FragWord, maxWidth: number, maxWidths: number[], lineno: number): FragWord[] {
  const R: FragWord[] = []
  const maxlineno = maxWidths.length - 1
  let maxWidthNext = maxWidths[Math.min(maxlineno, lineno + 1)]
  let W: [Frag, string][] = []
  let lineWidth = 0
  let fragText = ''
  let wordWidth = 0
  let f = w.parts[0][0]
  for (const [g, s] of w.parts) {
    const chars: [number, string][] = s ? Array.from(s, (c) => [stringWidth(c, g.fontName, g.fontSize), c]) : [[0, s]]
    for (const [cw, c] of chars) {
      let newLineWidth = lineWidth + cw
      const tooLong = newLineWidth > maxWidth && (wordWidth > 0 || (!wordWidth && cw <= maxWidthNext))
      if (g !== f || tooLong) {
        f = f.clone()
        if (f.text !== undefined) f.text = fragText
        W.push([f, fragText])
        if (tooLong) {
          R.push({ w: wordWidth, parts: W, kind: 'Split' })
          lineno += 1
          maxWidth = maxWidthNext
          maxWidthNext = maxWidths[Math.min(maxlineno, lineno + 1)]
          W = []
          newLineWidth = cw
          wordWidth = 0
        }
        fragText = ''
        f = g
      }
      wordWidth += cw
      fragText += c
      lineWidth = newLineWidth
    }
  }
  W.push([f, fragText])
  R.push({ w: wordWidth, parts: W, kind: isHS(w) ? 'SplitHS' : 'SplitH' })
  return R
}

/** ParaLines (kind 0) and FragLine (kind 1) */
export interface Line0 { extraSpace: number, words: Word[] }
export interface FragLine {
  extraSpace: number, wordCount: number, lineBreak: boolean, words: Frag[], fontSize: number, ascent: number, descent: number,
  maxWidth: number, sFW: number
}
export interface BLPara {
  kind: 0 | 1
  lines: (Line0 | FragLine)[]
  /** kind 0: the paragraph's one style */
  f?: Frag
  ascent?: number
  descent?: number
  fontSize?: number
}

export class Paragraph extends Flowable {
  style: ParagraphStyle
  frags: Frag[] | FragWord[]
  processed: boolean
  blPara: BLPara | null = null
  JustifyLast = false
  private wrapWidths: number[] = []
  widthMax = 0

  constructor(text: string | null, style: ParagraphStyle, frags: Frag[] | FragWord[] | null = null, processed = false) {
    super()
    this.style = style
    if (frags === null) {
      this.frags = parseParagraph(cleanBlockQuotedText(text ?? ''), style)
      this.processed = false
    } else {
      this.frags = frags
      this.processed = processed
    }
  }

  // Flowable's getters: the paragraph's own attribute first (KeepTogether clears keepWithNext), then its style's
  getSpaceBefore(): number {
    return this.spaceBefore ?? this.style.spaceBefore
  }

  getSpaceAfter(): number {
    return this.spaceAfter ?? this.style.spaceAfter
  }

  getKeepWithNext(): boolean {
    return this.keepWithNext !== undefined ? !!this.keepWithNext : !!this.style.keepWithNext
  }

  wrap(availWidth: number, _availHeight: number): [number, number] {
    if (availWidth < FUZZ) return [0, 0x7fffffff]
    this.width = availWidth
    const style = this.style
    const firstLineWidth = availWidth - (style.leftIndent + style.firstLineIndent) - style.rightIndent
    const laterWidths = availWidth - style.leftIndent - style.rightIndent
    this.wrapWidths = [firstLineWidth, laterWidths]
    const blPara = this.breakLines(this.wrapWidths)
    this.blPara = blPara
    this.height = blPara.lines.length * style.leading
    return [this.width, this.height]
  }

  minWidth(): number {
    const frags = this.frags
    if (!frags.length) return 0
    if (frags.length === 1 && !this.processed) {
      const f = frags[0] as Frag
      const words = f.text !== undefined ? split(f.text, ' ') : (f as unknown as { words: Word[] }).words.map(wText)
      return Math.max(...words.map((w) => stringWidth(w, f.fontName, f.fontSize)))
    }
    return Math.max(...getFragWords(frags, this.processed).map((w) => w.w))
  }

  /** breakLines: the lines of the paragraph at these widths. */
  breakLines(width: number[]): BLPara {
    this.widthMax = 0
    const maxWidths = [...width]
    const lines: (Line0 | FragLine)[] = []
    let lineno = 0
    const maxlineno = maxWidths.length - 1
    const style = this.style
    const spaceShrinkage = style.spaceShrinkage
    const splitLongWords = style.splitLongWords
    let maxWidth = maxWidths[0]
    const frags = this.frags
    const nFrags = frags.length
    const f0 = frags[0] as Frag
    if (nFrags === 1 && !this.processed && f0.cbDefn === undefined && f0.backColor === undefined) {
      const f = f0
      const fontSize = f.fontSize
      const fontName = f.fontName
      const [ascent, descent] = getAscentDescent(fontName, fontSize)
      let words: Word[]
      const fw = (f as unknown as { words?: Word[] }).words
      if (fw === undefined) {
        const text = strip(f.text ?? '')
        if (!text) return { kind: 0, lines: [], f, ascent, descent, fontSize }
        words = split(text)
      } else {
        words = [...fw]
        if (!words.some((w) => strip(wText(w)))) return { kind: 0, lines: [], f, ascent, descent, fontSize }
      }
      const spaceWidth = stringWidth(' ', fontName, fontSize)
      const dSpaceShrink = spaceShrinkage * spaceWidth
      let cLine: Word[] = []
      let currentWidth = -spaceWidth
      let forcedSplit = 0
      while (words.length) {
        const word = words.shift()!
        if (!wText(word) && word instanceof SplitWord) forcedSplit = 1
        const wordWidth = stringWidth(wText(word), fontName, fontSize)
        const newWidth = currentWidth + spaceWidth + wordWidth
        const limWidth = maxWidth + dSpaceShrink * cLine.length
        if (newWidth > limWidth && !forcedSplit) {
          if (splitLongWords && !(word instanceof SplitWord || forcedSplit)) {
            const nmw = Math.min(lineno, maxlineno)
            if (wordWidth > Math.max(...maxWidths.slice(nmw, nmw + 1))) {
              words.unshift(...splitWord(wText(word), currentWidth + spaceWidth, maxWidths, lineno, fontName, fontSize))
              forcedSplit = 1
              continue
            }
          }
        }
        if (newWidth <= limWidth || !cLine.length || forcedSplit) {
          if (wText(word)) cLine.push(word)
          if (forcedSplit) {
            forcedSplit = 0
            if (newWidth > this.widthMax) this.widthMax = newWidth
            lines.push({ extraSpace: maxWidth - newWidth, words: cLine })
            cLine = []
            currentWidth = -spaceWidth
            lineno += 1
            maxWidth = maxWidths[Math.min(maxlineno, lineno)]
          } else currentWidth = newWidth
        } else {
          if (currentWidth > this.widthMax) this.widthMax = currentWidth
          lines.push({ extraSpace: maxWidth - currentWidth, words: cLine })
          cLine = [word]
          currentWidth = wordWidth
          lineno += 1
          maxWidth = maxWidths[Math.min(maxlineno, lineno)]
        }
      }
      if (cLine.length) {
        if (currentWidth > this.widthMax) this.widthMax = currentWidth
        lines.push({ extraSpace: maxWidth - currentWidth, words: cLine })
      }
      return { kind: 0, lines, f, ascent, descent, fontSize }
    }
    if (nFrags <= 0) return { kind: 0, lines: [], fontSize: style.fontSize, ascent: style.fontSize, descent: -0.2 * style.fontSize }
    const njlbv = !style.justifyBreaks
    let words: Frag[] = []
    const FW: FragWord[] = []
    const _words = getFragWords(frags, this.processed)
    let sFW = 0
    let n = 0
    let spaceWidth = 0
    let currentWidth = 0
    let maxSize = 0
    let maxAscent = 0
    let minDescent = 0
    let g: Frag = null as unknown as Frag
    while (_words.length) {
      const w = _words.shift()!
      FW.push(w)
      let f = w.parts[w.parts.length - 1][0]
      const fontName = f.fontName
      let fontSize = f.fontSize
      if (!words.length) {
        n = spaceWidth = currentWidth = 0
        maxSize = fontSize
        ;[maxAscent, minDescent] = getAscentDescent(fontName, fontSize)
      }
      const wordWidth = w.w
      f = w.parts[0][0]
      const newWidth = wordWidth > 0 ? currentWidth + spaceWidth + wordWidth : currentWidth
      const lineBreak = f._fkind === FK_BREAK
      let limWidth = maxWidth
      if (spaceShrinkage) {
        let spaceShrink = spaceWidth
        for (const wi of words) {
          if (wi._fkind === FK_TEXT) {
            const ns = (wi.text ?? '').split(' ').length - 1
            if (ns) spaceShrink += ns * stringWidth(' ', wi.fontName, wi.fontSize)
          }
        }
        spaceShrink *= spaceShrinkage
        limWidth += spaceShrink
      }
      if (!lineBreak && newWidth > limWidth && !isSplitH(w) && f.cbDefn === undefined) {
        if (splitLongWords && !isSplit(w)) {
          const nmw = Math.min(lineno, maxlineno)
          if (wordWidth > Math.max(...maxWidths.slice(nmw, nmw + 1))) {
            const pieces = splitFragWord(w, maxWidth - spaceWidth - currentWidth, maxWidths, lineno)
            _words.unshift(...pieces)
            _words.splice(1, 0, { w: 0, parts: [[f.clone({ _fkind: FK_BREAK, text: '' }), '']], kind: 'Injected' })
            FW.pop()
            continue
          }
        }
      }
      const endLine = (newWidth > limWidth && n > 0) || lineBreak
      if (!endLine) {
        if (lineBreak) continue
        const nText = w.parts[0][1]
        if (nText) n += 1
        fontSize = f.fontSize
        const [ascent, descent] = getAscentDescent(f.fontName, fontSize)
        maxSize = Math.max(maxSize, fontSize)
        maxAscent = Math.max(maxAscent, ascent)
        minDescent = Math.min(minDescent, descent)
        if (!words.length) {
          g = f.clone()
          words = [g]
          g.text = nText
        } else if (!sameFrag(g, f)) {
          if (spaceWidth) {
            let i = words.length - 1
            while (i >= 0) {
              const wi = words[i]
              i -= 1
              if (wi._fkind === FK_TEXT) {
                if (!(wi.text ?? '').endsWith(' ')) wi.text = (wi.text ?? '') + ' '
                break
              }
            }
          }
          g = f.clone()
          words.push(g)
          g.text = nText
        } else if (spaceWidth) {
          if (!(g.text ?? '').endsWith(' ')) g.text = (g.text ?? '') + ' ' + nText
          else g.text = (g.text ?? '') + nText
        } else g.text = (g.text ?? '') + nText
        spaceWidth = isHS(w) ? stringWidth(' ', fontName, fontSize) : 0
        let ni = 0
        for (const [pf, pt] of w.parts.slice(1)) {
          g = pf.clone()
          g.text = pt
          if (g.text) ni = 1
          words.push(g)
          fontSize = g.fontSize
          const [a2, d2] = getAscentDescent(g.fontName, fontSize)
          maxSize = Math.max(maxSize, fontSize)
          maxAscent = Math.max(maxAscent, a2)
          minDescent = Math.min(minDescent, d2)
        }
        if (!nText && ni) n += 1
        currentWidth = newWidth
      } else {
        let llb = false
        if (lineBreak) {
          g = f.clone()
          words.push(g)
          llb = njlbv && w.kind !== 'Injected'
        }
        if (currentWidth > this.widthMax) this.widthMax = currentWidth
        lines.push({ extraSpace: maxWidth - currentWidth, wordCount: n, lineBreak: llb, words, fontSize: maxSize, ascent: maxAscent,
          descent: minDescent, maxWidth, sFW })
        sFW = FW.length - 1
        lineno += 1
        maxWidth = maxWidths[Math.min(maxlineno, lineno)]
        if (lineBreak) {
          words = []
          continue
        }
        spaceWidth = isHS(w) ? stringWidth(' ', fontName, fontSize) : 0
        currentWidth = wordWidth
        n = 1
        g = f.clone()
        maxSize = g.fontSize
        ;[maxAscent, minDescent] = getAscentDescent(g.fontName, maxSize)
        words = [g]
        g.text = w.parts[0][1]
        for (const [pf, pt] of w.parts.slice(1)) {
          g = pf.clone()
          g.text = pt
          words.push(g)
          fontSize = g.fontSize
          const [a2, d2] = getAscentDescent(g.fontName, fontSize)
          maxSize = Math.max(maxSize, fontSize)
          maxAscent = Math.max(maxAscent, a2)
          minDescent = Math.min(minDescent, d2)
        }
      }
    }
    if (words.length) {
      if (currentWidth > this.widthMax) this.widthMax = currentWidth
      lines.push({ extraSpace: maxWidth - currentWidth, wordCount: n, lineBreak: false, words, fontSize: maxSize, ascent: maxAscent,
        descent: minDescent, maxWidth, sFW })
    }
    this.frags = FW
    this.processed = true
    return { kind: 1, lines }
  }

  split(availWidth: number, availHeight: number): Flowable[] {
    if (this.frags.length <= 0 || availWidth < FUZZ || availHeight < FUZZ) return []
    if (!this.blPara) this.wrap(availWidth, availHeight)
    const blPara = this.blPara!
    const style = this.style
    const leading = style.leading
    const lines = blPara.lines
    let s = Math.trunc(availHeight / leading)
    const height = s * leading
    const allowOrphans = style.allowOrphans
    if ((!allowOrphans && s <= 1) || s === 0) {
      this.blPara = null
      return []
    }
    const n = lines.length
    const allowWidows = style.allowWidows
    if (n <= s) return [this]
    if (!allowWidows) {
      if (n === s + 1) {
        if ((allowOrphans && n === 3) || n > 3) s -= 1
        else {
          this.blPara = null
          return []
        }
      }
    }
    const take = (start: number, stop: number): [Frag[] | FragWord[], boolean] => {
      if (blPara.kind === 0) {
        const f = blPara.f!.clone()
        delete f.text
        ;(f as unknown as { words: Word[] }).words = yieldBLParaWords(blPara.lines as Line0[], start, stop)
        return [[f], false]
      }
      if (!stop) return [[], true]
      const ls = blPara.lines as FragLine[]
      const sFW = ls[start].sFW
      const sFWN = stop !== ls.length ? ls[stop].sFW : this.frags.length
      const F = (this.frags as FragWord[]).slice(sFW, sFWN)
      while (F.length && F[F.length - 1].kind === 'Injected') F.pop()
      return [F, true]
    }
    const [f1, p1] = take(0, s)
    const P1 = new Paragraph(null, style, f1, p1)
    P1.blPara = { ...blPara, kind: 1 as const, lines: blPara.lines.slice(0, s) }
    if (blPara.kind === 0) P1.blPara = { ...blPara, lines: blPara.lines.slice(0, s) }
    const lastLine = blPara.lines[s - 1] as FragLine
    P1.JustifyLast = !(blPara.kind === 1 && lastLine.lineBreak)
    P1.height = height
    P1.width = availWidth
    const style2 = style.firstLineIndent !== 0 ? style.clone({ firstLineIndent: 0 }) : style
    const [f2, p2] = take(s, n)
    const P2 = new Paragraph(null, style2, f2, p2)
    return [P1, P2]
  }

  draw(): void {
    this.drawPara()
  }

  /** drawPara: the lines as text, aligned, with their links. */
  drawPara(): void {
    const canvas = this.canv!
    const style = this.style
    const blPara = this.blPara!
    const lines = blPara.lines
    const leftIndent = style.leftIndent
    const curX = leftIndent
    const nLines = lines.length
    if (!nLines) return
    canvas.saveState()
    const alignment = style.alignment
    const offset = style.firstLineIndent
    const lim = nLines - 1
    const noJustifyLast = !this.JustifyLast
    const jllwc = style.justifyLastLine
    if (blPara.kind === 0) {
      const dpl = alignment === TA_LEFT ? leftDrawParaLine : alignment === TA_CENTER ? centerDrawParaLine
        : alignment === TA_RIGHT ? rightDrawParaLine : justifyDrawParaLine
      const f = blPara.f!
      const curY = this.height - f.fontSize
      canvas.setFillColor(f.textColor!)
      const tx = canvas.beginText(curX, curY)
      tx.setFont(f.fontName, f.fontSize, style.leading)
      for (let i = 0; i < nLines; i++) {
        const line = lines[i] as Line0
        let lastLine = noJustifyLast && i === lim
        if (lastLine && jllwc && line.words.length > jllwc) lastLine = false
        const tOff = dpl(tx, i === 0 ? offset : 0, line.extraSpace, line.words.map(wText), lastLine)
        if (f.link.length) doLinkLine(tx, canvas, style, f, line, i, tOff + leftIndent, curY)
      }
      canvas.drawText(tx)
    } else {
      const f = lines[0] as FragLine
      const curY = this.height - f.fontSize
      const dpl = alignment === TA_LEFT ? leftDrawParaLineX : alignment === TA_CENTER ? centerDrawParaLineX
        : alignment === TA_RIGHT ? rightDrawParaLineX : justifyDrawParaLineX
      const tx = canvas.beginText(curX, curY) as TX
      const xs: XtraState = { textColor: null, rise: 0, links: new Map(), leading: style.leading, leftIndent, curY, style }
      tx.xs = xs
      // tx._leading, tx._fontname and tx._fontsize = None: the first run sets them
      const t = tx as unknown as { leading: number | null, fontname: string | null, fontsize: number | null }
      t.leading = t.fontname = t.fontsize = null
      for (let i = 0; i < nLines; i++) {
        const line = lines[i] as FragLine
        let lastLine = noJustifyLast && i === lim
        if (lastLine && jllwc && line.wordCount > jllwc) lastLine = false
        dpl(tx, i === 0 ? offset : 0, line, lastLine)
        doPostText(tx, canvas)
      }
      canvas.drawText(tx)
    }
    canvas.restoreState()
  }
}

interface XtraState {
  textColor: unknown
  rise: number
  links: Map<number, LinkRec>
  leading: number
  leftIndent: number
  curY: number
  style: ParagraphStyle
}
/** a link being drawn: its first x, its vertical extent, then its last x */
interface LinkRec { n: number, link: string, x1: number, lo: number, hi: number, x2?: number }

function yieldBLParaWords(lines: Line0[], start: number, stop: number): Word[] {
  const out: Word[] = []
  let R: SplitWord[] = []
  for (const l of lines.slice(start, stop)) {
    for (const w of l.words) {
      if (w instanceof SplitWord) {
        R.push(w)
        if (w.end) {
          out.push(R.map((r) => r.s).join(''))
          R = []
        }
        continue
      }
      if (R.length) {
        out.push(R.map((r) => r.s).join(''))
        R = []
      }
      out.push(w)
    }
  }
  if (R.length) out.push(R.map((r) => r.s).join(''))
  return out
}

function setXPos(tx: PDFTextObject, dx: number): void {
  if (dx > 1e-6 || dx < -1e-6) tx.setXPos(dx)
}

const nbspCount = (w: string) => w.split('\u00a0').length - 1

function leftDrawParaLine(tx: PDFTextObject, offset: number, extraspace: number, words: string[]): number {
  let simple = extraspace > -1e-8
  const text = words.join(' ')
  setXPos(tx, offset)
  let nSpaces = 0
  if (!simple) {
    nSpaces = words.length + nbspCount(text) - 1
    simple = nSpaces <= 0
  }
  if (simple) tx.textOutRaw(text, true)
  else {
    tx.setWordSpace(extraspace / nSpaces)
    tx.textOutRaw(text, true)
    tx.setWordSpace(0)
  }
  setXPos(tx, -offset)
  return offset
}

function centerDrawParaLine(tx: PDFTextObject, offset: number, extraspace: number, words: string[]): number {
  let simple = extraspace > -1e-8
  const text = words.join(' ')
  let nSpaces = 0
  if (!simple) {
    nSpaces = words.length + nbspCount(text) - 1
    simple = nSpaces <= 0
  }
  let m: number
  if (simple) {
    m = offset + 0.5 * extraspace
    setXPos(tx, m)
    tx.textOutRaw(text, true)
  } else {
    m = offset
    tx.setWordSpace(extraspace / nSpaces)
    setXPos(tx, m)
    tx.textOutRaw(text, true)
    tx.setWordSpace(0)
  }
  setXPos(tx, -m)
  return m
}

function rightDrawParaLine(tx: PDFTextObject, offset: number, extraspace: number, words: string[]): number {
  let simple = extraspace > -1e-8
  const text = words.join(' ')
  let nSpaces = 0
  if (!simple) {
    nSpaces = words.length + nbspCount(text) - 1
    simple = nSpaces <= 0
  }
  let m: number
  if (simple) {
    m = offset + extraspace
    setXPos(tx, m)
    tx.textOutRaw(words.join(' '), true)
  } else {
    m = offset
    tx.setWordSpace(extraspace / nSpaces)
    setXPos(tx, m)
    tx.textOutRaw(text, true)
    tx.setWordSpace(0)
  }
  setXPos(tx, -m)
  return m
}

function justifyDrawParaLine(tx: PDFTextObject, offset: number, extraspace: number, words: string[], last: boolean): number {
  setXPos(tx, offset)
  const text = words.join(' ')
  let simple = (-1e-8 < extraspace && extraspace <= 1e-8) || (last && extraspace > -1e-8)
  let nSpaces = 0
  if (!simple) {
    nSpaces = words.length + nbspCount(text) - 1
    simple = nSpaces <= 0
  }
  if (simple) tx.textOutRaw(text, true)
  else {
    tx.setWordSpace(extraspace / nSpaces)
    tx.textOutRaw(text, true)
    tx.setWordSpace(0)
  }
  setXPos(tx, -offset)
  return offset
}

/** _do_link_line (kind 0): a link over the whole line. */
function doLinkLine(tx: PDFTextObject, canvas: Canvas, style: ParagraphStyle, f: Frag, line: Line0, i: number, tOff: number, curY: number): void {
  const leading = style.leading
  const y = curY - i * leading - f.fontSize / 8.0
  const textlen = canvas.stringWidth(line.words.map(wText).join(' '), tx.fontname, tx.fontsize)
  for (const [, link] of f.link) doLink(canvas, link, [tOff, y, tOff + textlen, y + leading])
}

/** _doLink: a URI link (a #name or document: link is not supported here). */
function doLink(canvas: Canvas, link: string, rect: [number, number, number, number]): void {
  if (!link) return
  if (link.startsWith('#') || link.split(':', 1)[0].toLowerCase() === 'document') throw new Error('in-document links are not supported here')
  canvas.linkURL(link, rect, 1)
}

const trailingSpaceLength = (text: string, tx: PDFTextObject): number => {
  const m = WSC_END.exec(text)
  return m ? tx.canvas.stringWidth(m[0], tx.fontname, tx.fontsize) : 0
}

type TX = PDFTextObject & { xs: XtraState }

/** _putFragLine: a line's runs, each with its font, color and rise; links tracked by x. */
function putFragLine(curX: number, tx: TX, line: FragLine, last: boolean): void {
  const xs = tx.xs
  const curY = xs.curY
  const x0 = tx.x0
  const leading = xs.leading
  curX += xs.leftIndent
  if ((tx.leading as number | null) !== leading) tx.setLeading(leading)
  const ws = tx.wordSpace
  let nSpaces = 0
  const words = line.words
  let AL: [number, string][] = []
  const links = xs.links
  words.forEach((f, i) => {
    if (f.cbDefn !== undefined) throw new Error('paragraph callbacks are not supported here')
    const curXs = curX + nSpaces * ws
    let endX = curXs
    const fontSize = f.fontSize
    const textColor = f.textColor
    const rise = f.rise
    if (i > 0) endX = curXs - trailingSpaceLength(words[i - 1].text ?? '', tx)
    if (tx.fontname !== f.fontName || tx.fontsize !== fontSize) tx.setFontOnly(f.fontName, fontSize)
    if (!colorEq(xs.textColor, textColor)) {
      xs.textColor = textColor
      tx.setFillColor(textColor!)
    }
    if (xs.rise !== rise) {
      xs.rise = rise
      tx.setRise(rise)
    }
    const fl = f.link
    const same = JSON.stringify(AL) === JSON.stringify(fl)
    let nAL: [number, string][] = []
    if (!same) {
      const S = new Set(AL.map((l) => JSON.stringify(l)))
      const NS = new Set(fl.map((l) => JSON.stringify(l)))
      nAL = fl.filter((l) => !S.has(JSON.stringify(l)))
      for (const l of AL.filter((l) => !NS.has(JSON.stringify(l)))) links.get(l[0])!.x2 = endX
    }
    const text = f.text ?? ''
    tx.textOutRaw(text, f === words[words.length - 1])
    const nlo = rise - 0.2 * fontSize
    const nhi = rise + fontSize
    if (!same) {
      for (const l of nAL) links.set(l[0], { n: l[0], link: l[1], x1: curX, lo: nlo, hi: nhi })
      AL = fl
    }
    for (const l of AL) {
      const rec = links.get(l[0])!
      if (nlo < rec.lo || nhi > rec.hi) {
        rec.lo = Math.min(nlo, rec.lo)
        rec.hi = Math.max(nhi, rec.hi)
      }
    }
    const txtlen = tx.canvas.stringWidth(text, tx.fontname, tx.fontsize)
    curX += txtlen
    nSpaces += text.split(' ').length - 1 + nbspCount(text)
  })
  const curXs = curX + (nSpaces - 1) * ws
  for (const l of AL) links.get(l[0])!.x2 = curXs
  if (tx.x0 !== x0) setXPos(tx, x0 - tx.x0)
  void curY
  void last
}

function leftDrawParaLineX(tx: TX, offset: number, line: FragLine, last: boolean): void {
  setXPos(tx, offset)
  const extraSpace = line.extraSpace
  let simple = extraSpace > -1e-8
  let nSpaces = 0
  if (!simple) {
    nSpaces = line.wordCount + line.words.reduce((a, w) => a + nbspCount(w.text ?? ''), 0) - 1
    simple = nSpaces <= 0
  }
  if (simple) putFragLine(offset, tx, line, last)
  else {
    tx.setWordSpace(extraSpace / nSpaces)
    putFragLine(offset, tx, line, last)
    tx.setWordSpace(0)
  }
  setXPos(tx, -offset)
}

function centerDrawParaLineX(tx: TX, offset: number, line: FragLine, last: boolean): void {
  const extraSpace = line.extraSpace
  let simple = extraSpace > -1e-8
  let nSpaces = 0
  if (!simple) {
    nSpaces = line.wordCount + line.words.reduce((a, w) => a + nbspCount(w.text ?? ''), 0) - 1
    simple = nSpaces <= 0
  }
  let m: number
  if (simple) {
    m = offset + 0.5 * line.extraSpace
    setXPos(tx, m)
    putFragLine(m, tx, line, last)
  } else {
    m = offset
    tx.setWordSpace(extraSpace / nSpaces)
    putFragLine(m, tx, line, last)
    tx.setWordSpace(0)
  }
  setXPos(tx, -m)
}

function rightDrawParaLineX(tx: TX, offset: number, line: FragLine, last: boolean): void {
  const extraSpace = line.extraSpace
  let simple = extraSpace > -1e-8
  let nSpaces = 0
  if (!simple) {
    nSpaces = line.wordCount + line.words.reduce((a, w) => a + nbspCount(w.text ?? ''), 0) - 1
    simple = nSpaces <= 0
  }
  let m: number
  if (simple) {
    m = offset + line.extraSpace
    setXPos(tx, m)
    putFragLine(m, tx, line, last)
  } else {
    m = offset
    tx.setWordSpace(extraSpace / nSpaces)
    putFragLine(m, tx, line, last)
    tx.setWordSpace(0)
  }
  setXPos(tx, -m)
}

function justifyDrawParaLineX(tx: TX, offset: number, line: FragLine, last: boolean): void {
  setXPos(tx, offset)
  const extraSpace = line.extraSpace
  let simple = line.lineBreak || (-1e-8 < extraSpace && extraSpace <= 1e-8) || (last && extraSpace > -1e-8)
  let nSpaces = 0
  if (!simple) {
    nSpaces = line.wordCount + line.words.reduce((a, w) => a + nbspCount(w.text ?? ''), 0) - 1
    simple = nSpaces <= 0
  }
  if (!simple) {
    tx.setWordSpace(extraSpace / nSpaces)
    putFragLine(offset, tx, line, last)
    tx.setWordSpace(0)
  } else putFragLine(offset, tx, line, last)
  setXPos(tx, -offset)
}

/** _do_post_text: the line's links as annotations (sorted as reportlab sorts them), the next baseline. */
function doPostText(tx: TX, canvas: Canvas): void {
  const xs = tx.xs
  const y0 = xs.curY
  const recs = [...xs.links.values()].sort((a, b) => (a.n - b.n) || (a.link < b.link ? -1 : a.link > b.link ? 1 : 0) || (a.x1 - b.x1)
    || (a.lo - b.lo) || (a.hi - b.hi) || ((a.x2 ?? 0) - (b.x2 ?? 0)))
  for (const r of recs) doLink(canvas, r.link, [r.x1, y0 + r.lo, r.x2!, y0 + r.hi])
  xs.links = new Map()
  xs.curY -= xs.style.leading
}

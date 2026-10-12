// The PDF writer against reportlab 4.5.1 (pdf_ref.json, pdf_canvas_ref.pdf; make_pdf_fixtures.py):
// fp_str, string widths, and a two-page canvas document byte for byte.
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { fpStr } from '../src/files/pdf/accel'
import { Canvas } from '../src/files/pdf/canvas'
import { black, HexColor } from '../src/files/pdf/colors'
import { stringWidth } from '../src/files/pdf/fonts'
import { decodePng } from '../src/files/pdf/images'
import ref from './fixtures/pdf_ref.json'
import { buildStory } from './pdfStory'

const R = ref as any
const latin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join('')

describe('the PDF primitives', () => {
  it('write numbers as fp_str does', () => {
    for (const [n, s] of R.fpStr) expect(fpStr(n), String(n)).toBe(s)
    expect(fpStr(1.5, 2, -3.25, 0.000001, 1e-9)).toBe(R.fpStrMany)
  })

  it('measure text as stringWidth does', () => {
    for (const [t, f, s, w] of R.widths) expect(stringWidth(t, f, s), `${t} ${f} ${s}`).toBe(w)
  })
})

describe('the canvas', () => {
  it('draws a document byte for byte as reportlab does', () => {
    const rgba = decodePng(Uint8Array.from(R.rgba))
    const pal = decodePng(Uint8Array.from(R.pal))
    const c = new Canvas([612.0, 792.0], { invariant: true })
    c.setTitle('Canvas test')
    c.setAuthor('STAF')
    c.setFont('Times-Roman', 9)
    c.drawString(72, 700, 'Plain text, (parens) and a back\\slash')
    c.drawCentredString(306, 680, 'Accents: é è ü — ≥ ² “quotes” ✓')
    c.setFont('Helvetica-Bold', 11.5)
    c.drawRightString(540, 660, 'Right aligned 12.5%')
    c.setStrokeColor(HexColor('#c9d2de'))
    c.setLineWidth(0.5)
    c.line(72, 650, 540, 650)
    c.lines([[72, 640, 100, 640], [100, 640, 100, 600]])
    c.setFillColor('#667085')
    c.rect(72, 500, 100.25, 80.5, 1, 1)
    c.setDash([2, 3], 0)
    c.rect(200, 500, 50, 50, 1, 0)
    c.setDash([], 0)
    c.saveState()
    c.translate(300, 400)
    c.scale(2, 0.5)
    const p = c.beginPath()
    p.moveTo(0, 0)
    p.lineTo(10.123456, 20.5)
    p.lineTo(30, 1e-8)
    p.close()
    c.drawPath(p, 1, 0)
    c.restoreState()
    c.drawImage(rgba, 72, 300, 90, 60, 'auto')
    c.drawImage(pal, 200, 300, 40, 40, 'auto')
    c.linkURL('https://usace-wrises.github.io/staf/', [72, 250, 300, 270], 1)
    c.showPage()
    c.setFont('Times-Bold', 14)
    c.setFillColor(black)
    c.drawString(72, 720, 'Page two')
    c.drawImage(rgba, 72, 600, 30, 20, 'auto')
    c.showPage()
    const got = latin1(c.getpdfdata())
    const want = latin1(new Uint8Array(readFileSync(join(__dirname, 'fixtures', 'pdf_canvas_ref.pdf'))))
    if (got !== want) {
      let i = 0
      while (i < got.length && got[i] === want[i]) i++
      expect(got.slice(Math.max(0, i - 200), i + 200), `first difference at ${i}`).toBe(want.slice(Math.max(0, i - 200), i + 200))
    }
    expect(got).toBe(want)
  })
})

describe('platypus', () => {
  it('lays out a story byte for byte as reportlab does', () => {
    const got = latin1(buildStory(R.platypus))
    const want = latin1(new Uint8Array(readFileSync(join(__dirname, 'fixtures', 'pdf_platypus_ref.pdf'))))
    if (got !== want) {
      const out = join(tmpdir(), 'staf-pdf-platypus.pdf')
      writeFileSync(out, Uint8Array.from(got, (c) => c.charCodeAt(0)))
      expect(got.length, `differs; compare with parity/pdf_compare.py tests/fixtures/pdf_platypus_ref.pdf ${out}`).toBe(-1)
    }
    expect(got).toBe(want)
  })
})

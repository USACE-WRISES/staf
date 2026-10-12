// The workbook writers against Python's (xlsx_ref.json, make_xlsx_fixtures.py): the zip writer on
// SFARI's calculator (as calculator._repack and Package.write copy it), the formula scanner, the Summary
// tab's text metrics and page fit, and a whole three-scenario SFARI workbook, byte for byte.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { sha256Hex } from '../src/files/bytes'
import * as F from '../src/files/xlsx/formula'
import { Package } from '../src/files/xlsx/package'
import { lines, pageFit, textPx } from '../src/files/xlsx/sheets'
import { validatePackage } from '../src/files/xlsx/validate'
import { readZip, repack } from '../src/files/zip'
import { regionsForL3 } from '../src/shared/summary'
import { buildWorkbook } from '../src/tools/sfari/workbook'
import ref from './fixtures/xlsx_ref.json'

const R = ref as any
const TEMPLATE = new Uint8Array(readFileSync(join(__dirname, '..', 'page', 'forms', 'sfari', 'SFARI_Calculator_Draft_2026-06-29.xlsx')))

describe('the zip writer', () => {
  it('copies a workbook as zipfile does, entry by entry', async () => {
    expect(sha256Hex(await repack(TEMPLATE, new Map()))).toBe(R.repack.repack)
    expect(sha256Hex((await Package.open(TEMPLATE)).write())).toBe(R.repack.package)
  })
})

describe('the formula scanner', () => {
  it('finds, rewrites and refuses as formula.py does', () => {
    const mapping = new Map([['sfari score', 'Existing Conditions'], ['score', "S2 Score's"], ['sheet1', 'Data']])
    for (const c of R.formulas) {
      if (c.error !== undefined) {
        expect(() => F.sheetSpans(c.formula), c.formula).toThrow(c.error)
      } else {
        expect(F.sheetSpans(c.formula).map((s) => [s.start, s.end, s.sheet, s.quoted]), c.formula).toEqual(c.spans)
        expect(F.rewrite(c.formula, mapping), c.formula).toBe(c.rewrite)
      }
      if (c.identifiers !== undefined) expect([...F.identifiers(c.formula)].sort(), c.formula).toEqual(c.identifiers)
      else expect(() => F.identifiers(c.formula), c.formula).toThrow()
    }
    for (const [name, quoted] of Object.entries(R.quote)) expect(F.quoteSheet(name), name).toBe(quoted)
  })
})

describe("the Summary tab's metrics", () => {
  it('measures text and fits the page as sheets.py does', () => {
    for (const [t, px, bold] of R.metrics.textPx) {
      expect(textPx(t), t).toBe(px)
      expect(textPx(t, true), t).toBe(bold)
    }
    for (const [t, px, n, nb] of R.metrics.lines) {
      expect(lines(t, px), `${t} ${px}`).toBe(n)
      expect(lines(t, px, true), `${t} ${px}`).toBe(nb)
    }
    for (const [w, h, fit] of R.metrics.pageFit) expect(pageFit(w, h), `${w}x${h}`).toEqual(fit)
  })
})

describe("SFARI's workbook", () => {
  it('is the Python workbook, byte for byte', async () => {
    const s = R.sfari
    const dl = s.delin.delineation
    const info = { app: 'SFARI', reachName: dl.gnis_name, reachLengthFt: dl.reach_length_ft, drainageAreaSqkm: dl.drainage_area_sqkm,
      streamOrder: dl.stream_order, regions: regionsForL3(s.delin.siteEngine.site.ecoregionL3.code) }
    const scenarios = s.scenarios.map((x: any) => [{ id: x.id, name: x.name, description: x.description, state: x.entries }, x.entries])
    const data = await buildWorkbook(s.delin, scenarios, info, s.today, TEMPLATE)
    const zip = readZip(data)
    const parts: Record<string, string> = {}
    for (const e of zip.entries) parts[e.name] = sha256Hex(await zip.read(e.name))
    expect(Object.keys(parts)).toEqual(Object.keys(s.parts))
    for (const [name, digest] of Object.entries(s.parts)) expect(parts[name], name).toBe(digest)
    expect(data.length).toBe(s.size)
    expect(sha256Hex(data)).toBe(s.sha256)
    expect(await validatePackage(data)).toEqual([])
  }, 60_000)
})

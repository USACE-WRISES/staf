// EASI's calculator in the web app (calculator.ts): its own copies of the 2026-10-23 template and blank
// (parity/copy_page.py), served only for the method files they were generated from, and the scenario cloner
// keeping a formula's characters that openpyxl writes as references (the Metrics sheet's ranges).
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { rewriteSheetXml, sheetFormulas } from '../src/files/xlsx/clone'
import { readZip } from '../src/files/zip'
import { available, BLANK_URL, FILENAME, SHEET_NAME, TEMPLATE_PACKAGE_DIGEST, TEMPLATE_URL } from '../src/tools/easi/calculator'
import { PIN } from '../src/tools/easi/method'

const page = (url: string) => new Uint8Array(readFileSync(join(__dirname, '..', 'page', ...url.split('/'))))
const text = async (data: Uint8Array, name: string) => new TextDecoder().decode(await readZip(data).read(name))

describe("EASI's calculator", () => {
  it('is served for the method files it was generated from', async () => {
    const custom = await text(page(TEMPLATE_URL), 'docProps/custom.xml')
    const stamp = /name="Package digest"[^>]*>\s*<vt:lpwstr\b[^>]*>([^<]*)</.exec(custom)
    expect(stamp?.[1]).toBe(TEMPLATE_PACKAGE_DIGEST)
    expect(PIN.packageDigest).toBe(TEMPLATE_PACKAGE_DIGEST)
    expect(available()).toBe(true)
    // the blank carries the template's identity, and both download under the calculator's date
    expect(await text(page(BLANK_URL), 'docProps/custom.xml')).toBe(custom)
    expect(FILENAME).toBe('2026-10-23_EASI Calculator.xlsx')
    expect(BLANK_URL.endsWith(FILENAME)).toBe(true)
  })

  it("names the score sheet Scenario, as the owner's workbook does", async () => {
    const book = await text(page(TEMPLATE_URL), 'xl/workbook.xml')
    const sheets = [...book.matchAll(/<sheet\b[^>]*\bname="([^"]+)"/g)].map((m) => m[1])
    expect(sheets).toEqual(['Instructions', SHEET_NAME, 'Metrics', 'Results', 'Reference', 'ChartData'])
    const blank = await text(page(BLANK_URL), 'xl/workbook.xml')
    expect([...blank.matchAll(/<sheet\b[^>]*\bname="([^"]+)"/g)].map((m) => m[1]).slice(0, 5))
      .toEqual(['Instructions', 'Summary', SHEET_NAME, 'Metrics', 'Reference'])
  })
})

describe('the scenario cloner', () => {
  it("keeps a formula's characters written as references", () => {
    const xml = '<c r="C5"><f>IF(ISNUMBER(Metrics!$B$5),"&#8804;"&amp;ROUND(Metrics!$B$5,2)&amp;" &#x2265;","")</f></c>'
    const out = rewriteSheetXml(xml, new Map([['metrics', 'S2 Metrics']]))
    expect(out).not.toContain('&amp;#')
    expect(out).toBe('<c r="C5"><f>IF(ISNUMBER(\'S2 Metrics\'!$B$5),"≤"&amp;ROUND(\'S2 Metrics\'!$B$5,2)&amp;" ≥","")</f></c>')
    expect(sheetFormulas(xml)).toEqual(['IF(ISNUMBER(Metrics!$B$5),"≤"&ROUND(Metrics!$B$5,2)&" ≥","")'])
  })
})

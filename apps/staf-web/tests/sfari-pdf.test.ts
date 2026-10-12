// SFARI's report and desktop metrics PDFs against SFARI's own report.build_pdf and build_desktop_metrics_pdf
// (parity/make_sfari_pdf_fixtures.py; the report in invariant mode, the basemap a fixed PNG or none).
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseJson } from '../src/app/pyjson'
import { buildDesktopMetricsPdf, buildPdf } from '../src/tools/sfari/reportpdf'

const FIX = join(__dirname, 'fixtures', 'sfari_pdf')
const index = JSON.parse(readFileSync(join(FIX, 'index.json'), 'utf8'))
const basemap = new Uint8Array(readFileSync(join(FIX, 'basemap.png')))
const latin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join('')

function same(got: Uint8Array, file: string): void {
  const g = latin1(got)
  const want = latin1(new Uint8Array(readFileSync(join(FIX, file))))
  if (g !== want) {
    const out = join(tmpdir(), `staf-${file}`)
    writeFileSync(out, got)
    expect(g.length, `differs; parity/pdf_compare.py tests/fixtures/sfari_pdf/${file} ${out}`).toBe(-1)
  }
  expect(g).toBe(want)
}

describe("SFARI's report and desktop metrics PDFs", () => {
  for (const name of index.cases as string[]) {
    const c = () => parseJson(readFileSync(join(FIX, `${name}.json`), 'utf8'))
    it(`${name}: the report byte for byte`, async () => {
      const k = c()
      same(await buildPdf(k.delin, k.metric_scores, k.function_scores, k.evidence, k.scored, k.scenario,
        { invariant: true, basemapPng: k.basemap ? basemap : null }), `${name}.report.pdf`)
    })
    it(`${name}: the desktop metrics PDF byte for byte`, () => {
      const k = c()
      same(buildDesktopMetricsPdf(k.delin, k.evidence, { invariant: true }), `${name}.metrics.pdf`)
    })
  }
})

// DEEP's report and metrics PDFs against DEEP's own report.build_pdf and build_metrics_pdf
// (parity/make_deep_pdf_fixtures.py; the report in invariant mode, the basemap a fixed PNG or none).
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseJson } from '../src/app/pyjson'
import { buildMetricsPdf, buildPdf } from '../src/tools/deep/reportpdf'

const FIX = join(__dirname, 'fixtures', 'deep_pdf')
const REPO = join(__dirname, '..', '..', '..')
const index = JSON.parse(readFileSync(join(FIX, 'index.json'), 'utf8'))
const basemap = new Uint8Array(readFileSync(join(FIX, 'basemap.png')))
const latin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join('')

function same(got: Uint8Array, file: string): void {
  const g = latin1(got)
  const want = latin1(new Uint8Array(readFileSync(join(FIX, file))))
  if (g !== want) {
    const out = join(tmpdir(), `staf-${file}`)
    writeFileSync(out, got)
    expect(g.length, `differs; parity/pdf_compare.py tests/fixtures/deep_pdf/${file} ${out}`).toBe(-1)
  }
  expect(g).toBe(want)
}

describe("DEEP's report and metrics PDFs", () => {
  for (const name of index.cases as string[]) {
    const c = parseJson(readFileSync(join(FIX, `${name}.json`), 'utf8'))
    const bundle = () => parseJson(readFileSync(join(REPO, c.bundle), 'utf8'))
    it(`${name}: the report byte for byte`, async () => {
      same(await buildPdf(c.delineation, bundle(), c.measured, c.scored, c.region, c.scenario,
        { invariant: true, basemapPng: c.basemap ? basemap : null }), `${name}.report.pdf`)
    })
    it(`${name}: the metrics PDF byte for byte`, () => {
      same(buildMetricsPdf(bundle(), c.ref, c.measured, c.delineation), `${name}.metrics.pdf`)
    })
  }
})

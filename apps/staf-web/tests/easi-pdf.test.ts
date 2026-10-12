// EASI's report and desktop metrics PDFs against EASI's own report.build_pdf and build_desktop_metrics_pdf
// (parity/make_easi_pdf_fixtures.py: saved assessments opened as the app opens them; invariant mode; the
// summary plot, the cross-section and the basemap pinned to fixed PNGs).
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { parseJson } from '../src/app/pyjson'
import { DataSource } from '../src/data/source'
import { loadMethod } from '../src/tools/easi/method'
import { buildDesktopMetricsPdf, buildPdf } from '../src/tools/easi/reportpdf'
import { BASE, have, localFetch } from './local'

const FIX = join(__dirname, 'fixtures', 'easi_pdf')
const index = JSON.parse(readFileSync(join(FIX, 'index.json'), 'utf8'))
const png = (name: string) => new Uint8Array(readFileSync(join(FIX, name)))
const latin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join('')

function same(got: Uint8Array, file: string): void {
  const g = latin1(got)
  const want = latin1(new Uint8Array(readFileSync(join(FIX, file))))
  if (g !== want) {
    const out = join(tmpdir(), `staf-${file}`)
    writeFileSync(out, got)
    expect(g.length, `differs; parity/pdf_compare.py tests/fixtures/easi_pdf/${file} ${out}`).toBe(-1)
  }
  expect(g).toBe(want)
}

// the method package names the scoring method and lists the metrics: read from the local layout
describe.skipIf(!have)("EASI's report and desktop metrics PDFs", () => {
  beforeAll(async () => {
    vi.stubGlobal('fetch', localFetch)
    await loadMethod(new DataSource(BASE))
  }, 120_000)
  afterAll(() => vi.unstubAllGlobals())

  for (const name of index.cases as string[]) {
    const c = () => parseJson(readFileSync(join(FIX, `${name}.json`), 'utf8'))
    it(`${name}: the report byte for byte`, async () => {
      const k = c()
      same(await buildPdf(k.result, k.scenario, { invariant: true, summaryPng: png('summary.png'), xsPng: k.xs ? png('xs.png') : null,
        basemapPng: k.basemap ? png('basemap.png') : null }), `${name}.report.pdf`)
    })
    it(`${name}: the desktop metrics PDF byte for byte`, () => {
      const k = c()
      same(buildDesktopMetricsPdf(k.result, k.cells, { invariant: true }), `${name}.metrics.pdf`)
    })
  }
})

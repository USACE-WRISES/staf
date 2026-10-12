// DEEP's field worksheet against DEEP's own report.build_field_forms_pdf (parity/make_deep_fieldform_fixtures.py):
// library bundles with desk and field values and a delineated site, compared as whole files.
import { readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parseJson } from '../src/app/pyjson'
import { COMPUTABLE } from '../src/tools/deep/computed'
import { buildFieldFormsPdf } from '../src/tools/deep/fieldform'

const FIX = join(__dirname, 'fixtures', 'deep_fieldform')
const REPO = join(__dirname, '..', '..', '..')
const index = JSON.parse(readFileSync(join(FIX, 'index.json'), 'utf8'))
const latin1 = (b: Uint8Array) => Array.from(b, (c) => String.fromCharCode(c)).join('')

describe("DEEP's field worksheet", () => {
  it('knows the metrics the desk computes', () => {
    expect([...COMPUTABLE].sort()).toEqual(index.computable)
  })

  for (const name of index.cases as string[]) {
    it(`${name}: byte for byte as DEEP makes it`, () => {
      const c = parseJson(readFileSync(join(FIX, `${name}.json`), 'utf8'))
      const bundle = parseJson(readFileSync(join(REPO, c.bundle), 'utf8'))
      const got = latin1(buildFieldFormsPdf(bundle, c.ref, c.measured, c.delineation))
      const want = latin1(new Uint8Array(readFileSync(join(FIX, `${name}.pdf`))))
      if (got !== want) {
        const out = join(tmpdir(), `staf-deep-fieldform-${name}.pdf`)
        writeFileSync(out, Uint8Array.from(got, (ch) => ch.charCodeAt(0)))
        expect(got.length, `differs; parity/pdf_compare.py tests/fixtures/deep_fieldform/${name}.pdf ${out}`).toBe(-1)
      }
      expect(got).toBe(want)
    })
  }
})

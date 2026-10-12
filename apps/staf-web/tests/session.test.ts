// Save and Open against each Shiny tool's own reader and writer (files_ref.json "sessions",
// make_file_fixtures.py): the files each tool refuses and why, the files from before the shared format
// it still reads, the text its Save writes for fixed blocks, DEEP's digests and provenance, and the
// saved site's point and source.
import { describe, expect, it } from 'vitest'
import { parseJson, pyDumps } from '../src/app/pyjson'
import * as deep from '../src/session/deep'
import * as easi from '../src/session/easi'
import { AssessmentFileError, dump, type ToolName } from '../src/session/file'
import * as sfari from '../src/session/sfari'
import ref from './fixtures/files_ref.json'

const S = (ref as any).sessions
const loaders: Record<string, (t: string) => unknown> = { sfari: sfari.load, deep: deep.load, easi: easi.load }

describe('the assessment file', () => {
  it('refuses what each tool refuses, saying why', () => {
    for (const r of S.refusals) {
      // EASI checks that a screening rescores, which needs its method: those cases are its own suite's
      if (r.tool === 'easi' && r.text.includes('"metricRows": []')) continue
      let message: string | null = null
      try {
        loaders[r.tool](r.text)
      } catch (err) {
        message = err instanceof AssessmentFileError ? err.message : `!${String(err)}`
      }
      expect(message, `${r.tool} ${r.text.slice(0, 80)}`).toBe(r.message)
    }
  })

  it('reads the files SFARI and DEEP wrote before the shared format', () => {
    for (const l of S.legacy) {
      const got = loaders[l.tool](l.text)
      expect(JSON.parse(pyDumps(got)), `${l.tool} ${l.text.slice(0, 60)}`).toEqual(l.loaded)
    }
  })

  it('writes what Save writes', () => {
    for (const d of S.dumps) {
      const text = dump(d.tool.toUpperCase() as ToolName, d.delineation === null ? null : parseJson(JSON.stringify(d.delineation)),
        d.toolData === null ? null : parseJson(JSON.stringify(d.toolData)), d.scenarios === null ? null : parseJson(JSON.stringify(d.scenarios)),
        '2026-10-12T00:00:00Z')
      // the fixture's JSON lost which numbers were floats: compare the parsed text, then the layout
      expect(text.split('\n').map((l) => l.slice(0, l.indexOf(':') + 1)), d.tool).toEqual(d.text.split('\n').map((l: string) => l.slice(0, l.indexOf(':') + 1)))
      expect(JSON.parse(text), d.tool).toEqual(JSON.parse(d.text))
    }
  })

  it("writes floats as DEEP's provenance and digest read them", () => {
    const D = S.deep
    const bundle = parseJson(D.bundle)
    expect(deep.contentDigest(bundle)).toBe(D.contentDigest)
    expect(deep.bundleDigest(bundle)).toBe(D.bundleDigest)
    expect(deep.lifecycleStatus(bundle)).toBe(D.lifecycle)
    expect(deep.provenance(bundle, { level3: { code: '58' }, state: null })).toEqual(D.provenance)
    expect(deep.bundleDigest({ library: { digest: 'sha256:abc' } })).toBe(D.digestPreferred)
    const text = deep.dump(parseJson('{"delineation": {"snapped_lat": 1.0}}'), bundle,
      { version: 1, active: 'existing', next: 2, items: [{ id: 'existing', name: 'Existing Conditions', description: '', state: { measured_values: {} } }] },
      { level3: null, state: { code: 'NH', abbr: 'NH', name: 'New Hampshire' } })
    const stamp = (t: string) => t.replace(/"savedAt": "[^"]*"/, '"savedAt": ""')
    expect(stamp(text)).toBe(stamp(D.dump))
  })

  it("finds a saved site's point and source as SFARI does", () => {
    for (const p of S.points) {
      expect(sfari.savedAnchor(p.d), JSON.stringify(p.d)).toEqual(p.anchor)
      const pt = sfari.savedPoint(p.d)
      expect(pt, JSON.stringify(p.d)).toEqual(p.point)
    }
  })
})

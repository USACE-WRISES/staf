// The bundle's lines of a view read by HTTP range, against the Python reference (streams_ref.json):
// the same features in the same order. Reads the local browser layout through a stand-in fetch
// (local.ts); skipped where it is not.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { hrFeatures, Network, v2Features } from '../src/streams/network'
import streams from './fixtures/streams_ref.json'
import { BASE, have, localFetch } from './local'

describe.skipIf(!have)('stream lines from the bundle', () => {
  beforeAll(() => {
    vi.stubGlobal('fetch', localFetch)
  })
  afterAll(() => vi.unstubAllGlobals())

  for (const c of (streams as { cases: any[] }).cases) {
    it(`reads ${c.name}'s HR and V2 lines as the Shiny tools do`, async () => {
      const src = new DataSource(BASE)
      await src.load()
      const [w, s, e, n] = c.bbox
      const hr = hrFeatures(await new Network(src, 'bundle/').inBox(w, s, e, n))
      const v2 = v2Features(await new Network(src, 'bundle/v2/').inBox(w, s, e, n))
      expect(JSON.stringify(hr)).toBe(JSON.stringify(c.hr))
      expect(JSON.stringify(v2)).toBe(JSON.stringify(c.v2))
    })
  }
})

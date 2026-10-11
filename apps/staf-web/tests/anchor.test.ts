// EASI's anchor for a click on a stream outside the V2 network (routing.route_from_hr), from the
// bundle: the same payload, key for key, as the Python reference (snap_ref.json). Reads the local
// layout (local.ts); skipped where it is not.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { routeFromHr } from '../src/engine/anchor'
import { flowlineAt } from '../src/engine/bundle'
import type { Hit } from '../src/engine/snap'
import { Network } from '../src/streams/network'
import snap from './fixtures/snap_ref.json'
import streams from './fixtures/streams_ref.json'
import { BASE, have, localFetch } from './local'

const cases = Object.fromEntries((streams as { cases: any[] }).cases.map((c) => [c.name, c]))

describe.skipIf(!have)('the anchor of a routed click', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())

  it('is the Python payload, key for key', { timeout: 60_000 }, async () => {
    const src = new DataSource(BASE)
    const hr = new Network(src, 'bundle/')
    const views = new Map<string, Awaited<ReturnType<Network['inBox']>>>()
    for (const p of (snap as { points: any[] }).points.filter((x) => x.route)) {
      const c = cases[p.case]
      if (!views.has(c.name)) views.set(c.name, await hr.inBox(c.bbox[0], c.bbox[1], c.bbox[2], c.bbox[3]))
      const line = views.get(c.name)!.find((l) => l.id === p.hrHit[3])!
      const rec = await flowlineAt(src, 'bundle/', line.region, line.row)
      const got = await routeFromHr(src, p.lat, p.lon, p.hrHit as Hit, rec, 'easi')
      expect(JSON.stringify(got)).toBe(JSON.stringify(p.route))
    }
  })
})

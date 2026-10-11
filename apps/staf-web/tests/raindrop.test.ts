// The catchment that holds a point and the raindrop's reach, read from the local layout, against the
// Python reference (snap_ref.json); skipped where the layout is not.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { catchmentAt, raindrop } from '../src/engine/bundle'
import snap from './fixtures/snap_ref.json'
import { BASE, have, localFetch } from './local'

const points = (snap as { points: any[] }).points

describe.skipIf(!have)('the bundle answers a raindrop', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())

  it('finds the V2 catchment that contains each point', async () => {
    const src = new DataSource(BASE)
    for (const p of points) {
      const got = await catchmentAt(src, 'bundle/v2/', p.lat, p.lon)
      expect(got?.id ?? null).toBe(p.catchment)
    }
  })

  it("reaches the Python raindrop's reach, snap and attributes", async () => {
    const src = new DataSource(BASE)
    for (const p of points.filter((x) => x.hrHit)) {
      const got = await raindrop(src, p.hrHit[0], p.hrHit[1])
      const want = p.raindrop
      expect(got === null).toBe(want === null)
      if (!got) continue
      expect([got.comid, got.snap_lat, got.snap_lon, got.method]).toEqual([want.comid, want.snap_lat, want.snap_lon, want.method])
      const r = p.raindropReach
      expect([got.reach.gnis_name, got.reach.reachcode, got.reach.totdasqkm, got.reach.slope, got.reach.fcode, got.reach.stream_order, got.reach.lengthkm])
        .toEqual([r.gnis_name, r.reachcode, r.totdasqkm, r.slope, r.fcode, r.stream_order, r.lengthkm])
    }
  })
})

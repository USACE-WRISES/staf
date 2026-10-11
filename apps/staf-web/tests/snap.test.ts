import { describe, expect, it } from 'vitest'
import { nearestPointOnLines } from '../src/engine/snap'
import snap from './fixtures/snap_ref.json'
import streams from './fixtures/streams_ref.json'

const cases = Object.fromEntries((streams as { cases: any[] }).cases.map((c) => [c.name, c]))
const points = (snap as { points: any[] }).points

describe('a click snaps to the nearest line', () => {
  it('as the Shiny tools snap to the V2 and HR lines of the view', () => {
    let exact = 0
    for (const p of points) {
      const c = cases[p.case]
      const v2 = nearestPointOnLines(c.v2, p.lat, p.lon, 'comid')
      const hr = nearestPointOnLines(c.hr, p.lat, p.lon, 'nhdplusid')
      for (const [got, want] of [[v2, p.v2Hit], [hr, p.hrHit]] as const) {
        expect(got === null).toBe(want === null)
        if (!got || !want) continue
        expect(got[3]).toBe(want[3])
        expect(Math.abs(got[0] - want[0])).toBeLessThan(1e-12)
        expect(Math.abs(got[1] - want[1])).toBeLessThan(1e-12)
        expect(Math.abs(got[2] - want[2])).toBeLessThan(1e-7)
        if (got[0] === want[0] && got[1] === want[1] && got[2] === want[2]) exact += 1
      }
    }
    // the browser's sin, cos and log round differently from the Windows C runtime's now and then
    expect(exact).toBeGreaterThan(points.length)
  })
})

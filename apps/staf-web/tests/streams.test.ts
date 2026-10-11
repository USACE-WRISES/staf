import { describe, expect, it } from 'vitest'
import { buildDisplay, type Feature, type FeatureCollection } from '../src/streams/display'
import { fetchBox, needsFetch } from '../src/streams/viewport'
import { albersForward, albersToLonLat } from '../src/xs/projExact'
import xs from './fixtures/xs_ref.json'
import streams from './fixtures/streams_ref.json'

interface Case {
  name: string, lat: number, lon: number, zoom: number, bbox: number[], mode: string
  hr: FeatureCollection | null, v2: FeatureCollection | null, covered: FeatureCollection, uncovered: FeatureCollection
}
const CASES = (streams as { cases: Case[] }).cases

describe('viewport', () => {
  it('gives the Python fetch box', () => {
    for (const c of CASES) expect(fetchBox(c.lat, c.lon, c.zoom)).toEqual(c.bbox)
  })
  it('fetches only when the view leaves the box', () => {
    const box = fetchBox(43.6858, -72.2367, 16)
    expect(needsFetch(null, box)).toBe(true)
    expect(needsFetch([43.68, -72.24, 43.69, -72.23], box)).toBe(false)
    expect(needsFetch([43.68, -72.30, 43.69, -72.23], box)).toBe(true)
  })
})

describe('Conus Albers both ways', () => {
  it('lands within 1e-8 m of pyproj, and mostly on it', () => {
    const [lon, lat] = xs.proj.lonlat
    const [gx, gy] = xs.proj.albers
    let same = 0
    lon.forEach((x, i) => {
      const [ax, ay] = albersForward(x, lat[i])
      expect(Math.abs(ax - gx[i])).toBeLessThan(1e-8)
      expect(Math.abs(ay - gy[i])).toBeLessThan(1e-8)
      if (ax === gx[i] && ay === gy[i]) same += 1
    })
    expect(same / lon.length).toBeGreaterThan(0.5)
  })
  it('comes back within 1e-12 degree', () => {
    const [lon, lat] = xs.proj.lonlat
    lon.forEach((x, i) => {
      const [bx, by] = albersToLonLat(...albersForward(x, lat[i]))
      expect(Math.abs(bx - x)).toBeLessThan(1e-12)
      expect(Math.abs(by - lat[i])).toBeLessThan(1e-12)
    })
  })
})

function key(f: Feature): string {
  return JSON.stringify([f.properties, f.geometry])
}

describe('the stream split', () => {
  for (const c of CASES) {
    it(`draws ${c.name} as the Shiny tools do`, () => {
      const got = buildDisplay(c.v2, c.hr, 150)
      expect(got.mode).toBe(c.mode)
      for (const layer of ['covered', 'uncovered'] as const) {
        const want = c[layer].features
        const have = got[layer].features
        expect(have.length).toBe(want.length)
        const diff = have.map((f, i) => [key(f) === key(want[i]), i] as const).filter(([ok]) => !ok)
        expect(diff.map(([, i]) => i)).toEqual([])
      }
    })
  }
})

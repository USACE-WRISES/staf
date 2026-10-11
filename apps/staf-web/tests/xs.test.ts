// The cross-section ports against the Python they mirror (fixtures: parity/make_fixtures.py).
import { describe, expect, it } from 'vitest'
import ref from './fixtures/xs_ref.json'
import { decode, encode, stations } from '../src/xs/codec'
import { placeSections } from '../src/xs/place'
import { transform } from '../src/xs/proj'
import { bilinear, pyRound, tileUrls, type Grid } from '../src/xs/sample'

const fromHex = (h: string): number => new DataView(Uint8Array.from(h.match(/../g)!.map((b) => parseInt(b, 16))).buffer).getFloat64(0)

describe('codec', () => {
  it('decodes archive transects bit for bit', () => {
    for (const c of ref.codec) {
      const z = decode(c.codes)
      expect(Array.from(new Uint32Array(z.buffer))).toEqual(c.bits)
    }
  })
  it('encodes back to the same codes', () => {
    for (const c of ref.codec) {
      const z = new Float32Array(Uint32Array.from(c.bits).buffer)
      expect(Array.from(encode(z))).toEqual(c.codes)
    }
  })
})

describe('stations', () => {
  it('are numpy.linspace bit for bit', () => {
    for (const l of ref.linspace) {
      const got = stations(l.wide, l.n)
      expect(Array.from(got)).toEqual(l.hex.map(fromHex))
    }
  })
})

describe('projections', () => {
  it('match pyproj to well under a micrometre', () => {
    const p = ref.proj
    const [u, v] = transform(5070, 26915, p.x, p.y)
    const [lon, lat] = transform(5070, 4269, p.x, p.y)
    const [ax, ay] = transform(4326, 5070, p.lonlat[0], p.lonlat[1])
    let worstM = 0
    let worstDeg = 0
    for (let i = 0; i < p.x.length; i++) {
      worstM = Math.max(worstM, Math.abs(u[i] - p.utm15[0][i]), Math.abs(v[i] - p.utm15[1][i]),
                        Math.abs(ax[i] - p.albers[0][i]), Math.abs(ay[i] - p.albers[1][i]))
      worstDeg = Math.max(worstDeg, Math.abs(lon[i] - p.nad83[0][i]), Math.abs(lat[i] - p.nad83[1][i]))
    }
    expect(worstM).toBeLessThan(1e-6)
    expect(worstDeg).toBeLessThan(1e-11)
  })
})

describe('placement', () => {
  it('puts every section where the build did', () => {
    for (const line of ref.place) {
      const got = placeSections({ x: line.x, y: line.y, parts: line.parts })
      expect(got.map((s) => s.k)).toEqual(line.sections.map((s) => s.k))
      line.sections.forEach((want, i) => {
        expect(Math.abs(got[i].s - want.s)).toBeLessThan(1e-6)
        expect(Math.abs(got[i].x - want.x)).toBeLessThan(1e-6)
        expect(Math.abs(got[i].y - want.y)).toBeLessThan(1e-6)
        // proj4 and PROJ agree to ~1e-9 m, so the 5 m chord's direction to ~1e-9 (0.3 µm at 250 m)
        expect(Math.abs(got[i].nx - want.nx)).toBeLessThan(1e-8)
        expect(Math.abs(got[i].ny - want.ny)).toBeLessThan(1e-8)
      })
    }
  })
})

describe('sampling', () => {
  it('is exact on a plane and propagates a missing cell', () => {
    const width = 4
    const height = 3
    const data = new Float32Array(width * height)
    for (let r = 0; r < height; r++) for (let c = 0; c < width; c++) data[r * width + c] = 10 + 2 * c - 3 * r
    const g: Grid = { data, width, height, c: 100, a: 1, f: 50, e: -1 }
    // cell centres at x = 100.5 + c, y = 49.5 - r; the plane z = 10 + 2 (x - 100.5) - 3 (49.5 - y)
    const X = [100.75, 101.5, 102.25]
    const Y = [49.25, 48.5, 48.0]
    const got = bilinear(g, X, Y)
    X.forEach((x, i) => expect(got[i]).toBeCloseTo(10 + 2 * (x - 100.5) - 3 * (49.5 - Y[i]), 12))
    data[1 * width + 1] = NaN
    expect(Number.isNaN(bilinear(g, [101.0], [49.0])[0])).toBe(true)
    expect(Number.isNaN(bilinear(g, [100.2], [49.0])[0])).toBe(true) // outside the cell centres
  })
  it('rounds half to even like Python', () => {
    // int(round(x)) in Python: half to even (int(round(-0.5)) is 0)
    expect([0.5, 1.5, 2.5, -0.5, -1.5, 2.4999, 2.5001].map(pyRound)).toEqual([0, 2, 2, 0, -2, 2, 3])
  })
  it('builds tile addresses in the build’s paste order', () => {
    expect(tileUrls({ res: 1, source: 'IA_X_2019', tiles: 'USGS_1M_15_x41y462_IA_X_2019.tif;USGS_1M_15_x40y462_IA_X_2019.tif' }))
      .toEqual(['https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/IA_X_2019/TIFF/USGS_1M_15_x40y462_IA_X_2019.tif',
                'https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/IA_X_2019/TIFF/USGS_1M_15_x41y462_IA_X_2019.tif'])
    expect(tileUrls({ res: 10, source: '3dep-13', tiles: 'USGS_13_n42w094.tif' }))
      .toEqual(['https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/current/n42w094/USGS_13_n42w094.tif'])
    expect(tileUrls({ res: 3, source: '3dep-19', tiles: 'ned19_n33x00_w088x75_ms_x' }))
      .toEqual(['https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/19/TILES/ned19_n33x00_w088x75_ms_x/ned19_n33x00_w088x75_ms_x.img'])
  })
})

// Exact pulls: the release's profile checksum (zlib's CRC-32 of the archive codes), the checksum search
// that settles samples the browser's projection leaves within a hair of a rounding boundary, and the
// PROJ port's distance from pyproj (fixture from Python, see the notes).
import { describe, expect, it } from 'vitest'
import ref from './fixtures/proj_ref.json'
import { crc32, profileCrc } from '../src/xs/crc32'
import { from5070 } from '../src/xs/projExact'
import { nextFloat32, resolveByChecksum } from '../src/xs/sample'

describe('checksum', () => {
  it('is zlib.crc32', () => {
    expect(crc32(new TextEncoder().encode('123456789'))).toBe(ref.check_123456789)
    const z = Float32Array.from(ref.profile, (v) => (v === null ? NaN : v))
    expect(profileCrc(z)).toBe(ref.profile_crc)
  })
})

describe('resolveByChecksum', () => {
  const truth = Float32Array.from(ref.profile, (v) => (v === null ? NaN : v))
  const crc = profileCrc(truth)

  it('confirms a pull that rounds as the build did', () => {
    const r = resolveByChecksum(Float64Array.from(truth), new Float64Array(truth.length), crc)
    expect(r).toMatchObject({ verified: true, adjusted: 0, open: 0 })
  })

  it('rounds the other way a sample its slack leaves open', () => {
    // sample 100 computed a hair below the midpoint under the build's value: it rounds down here
    const i = 100
    const lower = nextFloat32(truth[i], false)
    const mid = (truth[i] + lower) / 2
    const z64 = Float64Array.from(truth)
    z64[i] = mid - 1e-12
    expect(Math.fround(z64[i])).toBe(lower)
    const slack = new Float64Array(truth.length)
    slack[i] = 1e-11
    const r = resolveByChecksum(z64, slack, crc)
    expect(r.verified).toBe(true)
    expect(r.adjusted).toBe(1)
    expect(Array.from(new Uint32Array(r.z.buffer))).toEqual(Array.from(new Uint32Array(truth.slice().buffer)))
    // with no slack the sample isn't open, and the pull is reported unverified
    expect(resolveByChecksum(z64, new Float64Array(truth.length), crc)).toMatchObject({ verified: false, open: 0 })
  })

  it('steps float32 values both ways', () => {
    expect(nextFloat32(1, true)).toBe(1 + 2 ** -23)
    expect(nextFloat32(1, false)).toBe(1 - 2 ** -24)
    expect(nextFloat32(-1, true)).toBe(-1 + 2 ** -24)
  })
})

describe('projExact', () => {
  it('lands within the slack the pull allows of pyproj', () => {
    const [lon, lat] = from5070(4269, ref.x, ref.y)
    let degrees = 0
    let metres = 0
    let identical = 0
    for (let i = 0; i < ref.x.length; i++) {
      degrees = Math.max(degrees, Math.abs(lon[i] - ref.lon[i]), Math.abs(lat[i] - ref.lat[i]))
      const [u, v] = from5070(26900 + ref.zone[i], [ref.x[i]], [ref.y[i]])
      metres = Math.max(metres, Math.abs(u[0] - ref.u[i]), Math.abs(v[0] - ref.v[i]))
      if (u[0] === ref.u[i] && v[0] === ref.v[i]) identical += 1
    }
    expect(degrees).toBeLessThan(1e-12)
    expect(metres).toBeLessThan(1.1e-7)
    expect(identical).toBeGreaterThan(ref.x.length / 2) // most points match PROJ to the last bit
  })
})

// The PROJ port (src/xs/projExact.ts) against pyproj, counted: raw float64 files written by pyproj
// (PROJ_FIXTURES=<dir>, from parity/proj_fixtures.py). On 400,000 points (2026-10-11) the port matched
// PROJ to the last bit on most, and was never more than 1.1e-8 m or 1e-13 degrees off: the browser's
// sin, cos, asin, atan2, log and asinh round otherwise than the Windows C runtime's. tests/exact.test.ts
// holds the bound on a small fixture.
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { albersInverse, from5070, setHypot, utmForward } from '../src/xs/projExact'

const dir = process.env.PROJ_FIXTURES ?? ''

function f64(name: string): Float64Array {
  const b = readFileSync(join(dir, `${name}.f64`))
  return new Float64Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength))
}

function i32(name: string): Int32Array {
  const b = readFileSync(join(dir, `${name}.i32`))
  return new Int32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength))
}

const same = (a: number, b: number) => Object.is(a, b)

describe.skipIf(!dir || !existsSync(join(dir, 'X.f64')))('projExact against pyproj (large)', () => {
  const variants: Record<string, (x: number, y: number) => number> = {
    'Math.hypot': (x, y) => Math.hypot(x, y),
    'sqrt(x*x+y*y)': (x, y) => Math.sqrt(x * x + y * y),
  }
  it.each(Object.keys(variants))('counts mismatches with %s', (name) => {
    setHypot(variants[name])
    const X = f64('X'), Y = f64('Y'), lam = f64('lam'), phi = f64('phi'), lon = f64('lon'), lat = f64('lat')
    const u = f64('u'), v = f64('v'), u2 = f64('u2'), v2 = f64('v2'), ub = f64('ub'), vb = f64('vb')
    const zone = i32('zone'), zone2 = i32('zone2')
    const n = X.length
    const bad = { albers: 0, geographic: 0, utmStep: 0, utm: 0, utmNext: 0 }
    for (let i = 0; i < n; i++) {
      const [l, p] = albersInverse(X[i], Y[i])
      if (!same(l, lam[i]) || !same(p, phi[i])) bad.albers++
      const [x, y] = utmForward(zone[i], lam[i], phi[i])
      if (!same(x, ub[i]) || !same(y, vb[i])) bad.utmStep++
    }
    const [glon, glat] = from5070(4269, X, Y)
    for (let i = 0; i < n; i++) if (!same(glon[i], lon[i]) || !same(glat[i], lat[i])) bad.geographic++
    let worstM = 0
    let worstDeg = 0
    for (let i = 0; i < n; i++) {
      const [a, b] = from5070(26900 + zone[i], [X[i]], [Y[i]])
      if (!same(a[0], u[i]) || !same(b[0], v[i])) bad.utm++
      const [c, d] = from5070(26900 + zone2[i], [X[i]], [Y[i]])
      if (!same(c[0], u2[i]) || !same(d[0], v2[i])) bad.utmNext++
      worstM = Math.max(worstM, Math.abs(a[0] - u[i]), Math.abs(b[0] - v[i]), Math.abs(c[0] - u2[i]), Math.abs(d[0] - v2[i]))
      worstDeg = Math.max(worstDeg, Math.abs(glon[i] - lon[i]), Math.abs(glat[i] - lat[i]))
    }
    console.log(`${name}: ${n} points; mismatches ${JSON.stringify(bad)}; largest ${worstM} m, ${worstDeg} deg`)
    expect(n).toBeGreaterThan(0)
  })
})

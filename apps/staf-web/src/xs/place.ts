/**
 * Where a flowline's grid sections sit and which way they face: tools/xs-grid/xsgrid/sections.py
 * `place`, for one flowline. Each part of length L (EPSG:5070) carries n = max(1, round(L / 30.48))
 * sections at (k + 1/2) L / n from its first (upstream) vertex, each perpendicular to the chord from its
 * point to the point 5 m downstream. The build accumulates distances over a whole region, so a
 * position here agrees with the archive's to within about 1e-9 m.
 */
import { transform } from './proj'

export const SPACING_M = 30.48
export const CHORD_M = 5.0

export interface PlacedSection {
  part: number
  k: number
  nSec: number
  s: number
  x: number
  y: number
  nx: number
  ny: number
}

/** A flowline from the bundle's lines (`x`, `y` in 1e-5 degrees, `parts` = vertices per part). */
export interface LineGeometry {
  x: ArrayLike<number>
  y: ArrayLike<number>
  parts: ArrayLike<number>
}

export function placeSections(line: LineGeometry): PlacedSection[] {
  const lon = Array.from(line.x, (v) => v / 1e5)
  const lat = Array.from(line.y, (v) => v / 1e5)
  const [px, py] = transform(4326, 5070, lon, lat)
  const out: PlacedSection[] = []
  let start = 0
  for (let part = 0; part < line.parts.length; part++) {
    const count = line.parts[part]
    const end = start + count
    const seg = new Float64Array(count)
    const local = new Float64Array(count)
    for (let v = start; v < end - 1; v++) seg[v - start] = Math.hypot(px[v + 1] - px[v], py[v + 1] - py[v])
    for (let i = 1; i < count; i++) local[i] = local[i - 1] + seg[i - 1]
    const plen = count > 1 ? local[count - 1] : 0
    const nSec = plen > 0 ? Math.max(1, Math.floor(plen / SPACING_M + 0.5)) : 0
    const pointAt = (dist: number): [number, number, number] => {
      let lo = 0
      let hi = count
      while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (local[mid] <= dist) lo = mid + 1
        else hi = mid
      }
      const idx = Math.min(Math.max(lo - 1, 0), Math.max(count - 2, 0))
      const sl = seg[idx]
      let tt = sl > 0 ? (dist - local[idx]) / sl : 0
      tt = Math.min(Math.max(tt, 0), 1)
      const a = start + idx
      return [px[a] + tt * (px[a + 1] - px[a]), py[a] + tt * (py[a + 1] - py[a]), a]
    }
    for (let k = 0; k < nSec; k++) {
      const s = ((k + 0.5) * plen) / nSec
      const s2 = Math.min(s + CHORD_M, plen)
      const [x0, y0, a] = pointAt(s)
      const [x2, y2] = pointAt(s2)
      let dx = x2 - x0
      let dy = y2 - y0
      let norm = Math.hypot(dx, dy)
      if (norm === 0) {
        dx = px[a + 1] - px[a]
        dy = py[a + 1] - py[a]
        norm = Math.hypot(dx, dy)
      }
      if (!(norm > 0)) continue
      out.push({ part, k, nSec, s, x: x0, y: y0, nx: -dy / norm, ny: dx / norm })
    }
    start = end
  }
  return out
}

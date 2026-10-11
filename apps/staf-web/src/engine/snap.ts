// The nearest point on a set of lines: how a click snaps to a stream.
//
// The Shiny tools (flowlines.nearest_point_on_lines; site_engine geometry.nearest_point_on_records)
// project the lines and the click to EPSG:5070, take the first line nearest the click (GEOS distance,
// pandas idxmin), the point on it nearest the click (GEOS DistanceOp: the first segment at the least
// distance, LineSegment::closestPoint), and that point's distance in feet; the point goes back to
// degrees. The same steps here, with GEOS's arithmetic (geom.ts) and PROJ's projection (projExact.ts).
import type { Feature, FeatureCollection } from '../streams/display'
import { FT_PER_M } from '../streams/display'
import { pointToSegment } from '../streams/geom'
import { albersForward, albersToLonLat } from '../xs/projExact'

/** (snapLat, snapLon, distanceFt, id), as the Python returns them. */
export type Hit = [number, number, number, number | null]

type Pt = [number, number]

function partsOf(geometry: Feature['geometry']): Pt[][] {
  const c = geometry?.coordinates as number[][] | number[][][] | undefined
  if (!c || !(c as unknown[]).length) return []
  if (geometry.type === 'LineString') return [c as number[][] as Pt[]]
  if (geometry.type === 'MultiLineString') return (c as number[][][]).map((p) => p as Pt[])
  return []
}

/** GEOS LineSegment::closestPoint. */
function closestOnSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): Pt {
  let factor: number
  if (px === ax && py === ay) factor = 0
  else if (px === bx && py === by) factor = 1
  else {
    const dx = bx - ax
    const dy = by - ay
    factor = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
  }
  if (factor > 0 && factor < 1) return [ax + factor * (bx - ax), ay + factor * (by - ay)]
  const d0 = Math.sqrt((ax - px) * (ax - px) + (ay - py) * (ay - py))
  const d1 = Math.sqrt((bx - px) * (bx - px) + (by - py) * (by - py))
  return d0 < d1 ? [ax, ay] : [bx, by]
}

/** The least distance from the point to the lines and the nearest point (GEOS DistanceOp over the
 * line components in order: a later segment wins only when strictly nearer). */
function nearestOn(lines: Pt[][], px: number, py: number): { dist: number, at: Pt | null } {
  let dist = Infinity
  let at: Pt | null = null
  for (const line of lines) {
    for (let i = 0; i < line.length - 1; i++) {
      const [ax, ay] = line[i]
      const [bx, by] = line[i + 1]
      const d = pointToSegment(px, py, ax, ay, bx, by)
      if (d < dist) {
        dist = d
        at = closestOnSegment(px, py, ax, ay, bx, by)
      }
    }
    if (line.length === 1 && dist > 0) {
      const [ax, ay] = line[0]
      const d = Math.sqrt((ax - px) * (ax - px) + (ay - py) * (ay - py))
      if (d < dist) {
        dist = d
        at = [ax, ay]
      }
    }
  }
  return { dist, at }
}

/** Snap (lat, lon) to the nearest line of ``fc``: (snapLat, snapLon, distanceFt, the line's
 * ``idProp``), or null when there are no lines. */
export function nearestPointOnLines(fc: FeatureCollection | null, lat: number, lon: number, idProp = 'comid'): Hit | null {
  if (!fc?.features?.length) return null
  const [cx, cy] = albersForward(lon, lat)
  let best = -1
  let bestDist = Infinity
  const projected: Pt[][][] = []
  fc.features.forEach((f, i) => {
    const lines = partsOf(f.geometry).map((part) => part.map(([x, y]) => albersForward(Number(x), Number(y))))
    projected.push(lines)
    if (!lines.length) return
    const { dist } = nearestOn(lines, cx, cy)
    if (dist < bestDist) {                     // idxmin: the first of the least
      bestDist = dist
      best = i
    }
  })
  if (best < 0) return null
  const { at } = nearestOn(projected[best], cx, cy)
  if (!at) return null
  const dx = cx - at[0]
  const dy = cy - at[1]
  const distFt = Math.sqrt(dx * dx + dy * dy) * FT_PER_M
  const [blon, blat] = albersToLonLat(at[0], at[1])
  const id = fc.features[best].properties?.[idProp]
  return [blat, blon, distFt, id === undefined || id === null ? null : Number(id)]
}

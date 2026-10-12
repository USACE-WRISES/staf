// NWI wetland area along the assessment reach (site_engine bundle.reach_wetlands and SFARI's
// nwi.wetlands_along_reach), from the 150 m strips the bundle stores per HR flowline.
//
// Each flowline the reach runs along (the anchor and its upstream mainstem, as the reach was walked)
// counts for the share of its length the reach covers: the length of the reach inside the flowline's
// 1 m buffer over the flowline's length, in EPSG:5070. The buffer is GEOS's: the segments' offset
// rectangles, round joins and round end caps as polygons of 8 segments a quarter circle (the reach
// leaves a flowline's buffer through its end cap where it runs on to the next flowline).
import { pyFixed, pySum } from '../app/pyfmt'
import { albersForward } from '../xs/projExact'
import { lineLength, type Line, type XY } from '../streams/geom'
import type { Dataset } from './dataset'
import type { ValueTables } from './values'

export const NWI_SYSTEMS: [string, string][] = [['r', 'riverine'], ['p', 'palustrine'], ['l', 'lacustrine'], ['e', 'estuarine'], ['m', 'marine']]
export const NWI_STRIP_HALF_WIDTH_M = 150.0
const M2_PER_ACRE = 4046.8564224
const QUAD_SEGS = 8
const QUANTUM = Math.PI / 2.0 / QUAD_SEGS

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

type Poly = XY[]

/** GEOS OffsetSegmentGenerator::computeOffsetSegment: the segment moved ``d`` to its left (+1) or right (-1). */
function offset(a: XY, b: XY, side: number, d: number): [XY, XY] {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len = Math.sqrt(dx * dx + dy * dy)
  const ux = side * d * dx / len
  const uy = side * d * dy / len
  return [[a[0] - uy, a[1] + ux], [b[0] - uy, b[1] + ux]]
}

/** addDirectedFillet's points around ``p``. */
function fillet(p: XY, start: number, end: number, clockwise: boolean, r: number): XY[] {
  const total = Math.abs(start - end)
  const n = Math.trunc(total / QUANTUM + 0.5)
  if (n < 1) return []
  const inc = total / n
  const out: XY[] = []
  for (let i = 0; i < n; i++) {
    const ang = start + (clockwise ? -1 : 1) * i * inc
    out.push([p[0] + r * Math.cos(ang), p[1] + r * Math.sin(ang)])
  }
  return out
}

/** addLineEndCap(p0, p1), round: the half disc ahead of p1. */
function endCap(p0: XY, p1: XY, r: number): Poly {
  const angle = Math.atan2(p1[1] - p0[1], p1[0] - p0[0])
  const left = offset(p0, p1, 1, r)[1]
  const right = offset(p0, p1, -1, r)[1]
  return [left, ...fillet(p1, angle + Math.PI / 2.0, angle - Math.PI / 2.0, true, r), right]
}

/** The convex pieces of a line's buffer: each segment's rectangle, the fan of each round join and the
 * two end caps. */
function bufferPieces(line: Line, r: number): Poly[] {
  const pts = line.filter((p, i) => i === 0 || p[0] !== line[i - 1][0] || p[1] !== line[i - 1][1])
  const out: Poly[] = []
  if (pts.length < 2) {
    return pts.length ? [fillet(pts[0], 0, 2 * Math.PI, false, r)] : []
  }
  for (let i = 0; i + 1 < pts.length; i++) {
    const [l0, l1] = offset(pts[i], pts[i + 1], 1, r)
    const [r0, r1] = offset(pts[i], pts[i + 1], -1, r)
    out.push([l0, l1, r1, r0])
  }
  for (let i = 1; i + 1 < pts.length; i++) {
    const s0 = pts[i - 1]
    const s1 = pts[i]
    const s2 = pts[i + 1]
    const cross = (s1[0] - s0[0]) * (s2[1] - s1[1]) - (s1[1] - s0[1]) * (s2[0] - s1[0])
    if (cross === 0) continue
    // the outside of the turn: the left for a right turn, the right for a left turn
    const side = cross < 0 ? 1 : -1
    const p0 = offset(s0, s1, side, r)[1]
    const p1 = offset(s1, s2, side, r)[0]
    let start = Math.atan2(p0[1] - s1[1], p0[0] - s1[0])
    const end = Math.atan2(p1[1] - s1[1], p1[0] - s1[0])
    const clockwise = side === 1
    if (clockwise && start <= end) start += 2.0 * Math.PI
    if (!clockwise && start >= end) start -= 2.0 * Math.PI
    out.push([s1, p0, ...fillet(s1, start, end, clockwise, r), p1])
  }
  out.push(endCap(pts[pts.length - 2], pts[pts.length - 1], r))
  out.push(endCap(pts[1], pts[0], r))
  return out
}

/** The part [t0, t1] of segment a-b inside a convex polygon (Cyrus-Beck), or null. */
function clip(a: XY, b: XY, poly: Poly): [number, number] | null {
  let area = 0
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i]
    const q = poly[(i + 1) % poly.length]
    area += p[0] * q[1] - q[0] * p[1]
  }
  if (area === 0) return null
  const sign = area > 0 ? 1 : -1
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  let t0 = 0
  let t1 = 1
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i]
    const q = poly[(i + 1) % poly.length]
    // inward normal of edge p-q
    const nx = -(q[1] - p[1]) * sign
    const ny = (q[0] - p[0]) * sign
    const num = nx * (a[0] - p[0]) + ny * (a[1] - p[1])
    const den = nx * dx + ny * dy
    if (den === 0) {
      if (num < 0) return null
      continue
    }
    const t = -num / den
    if (den > 0) t0 = Math.max(t0, t)
    else t1 = Math.min(t1, t)
    if (t0 > t1) return null
  }
  return [t0, t1]
}

/** The length of the reach inside the buffer of ``line`` at ``r`` (shapely reach.intersection(
 * line.buffer(r)).length). */
export function lengthWithin(reach: Line[], line: Line[], r = 1.0): number {
  const pieces = line.flatMap((part) => bufferPieces(part, r))
  let total = 0
  for (const part of reach) {
    for (let i = 0; i + 1 < part.length; i++) {
      const a = part[i]
      const b = part[i + 1]
      const len = Math.sqrt((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2)
      if (!len) continue
      const spans = pieces.map((p) => clip(a, b, p)).filter((s): s is [number, number] => s !== null).sort((x, y) => x[0] - y[0])
      let covered = 0
      let cur: [number, number] | null = null
      for (const s of spans) {
        if (cur && s[0] <= cur[1]) cur[1] = Math.max(cur[1], s[1])
        else {
          if (cur) covered += cur[1] - cur[0]
          cur = [s[0], s[1]]
        }
      }
      if (cur) covered += cur[1] - cur[0]
      total += covered * len
    }
  }
  return total
}

type Geometry = { type: string, coordinates: any }

/** The one geometry of a FeatureCollection, Feature or geometry (bundle._single_geometry). */
function singleGeometry(gj: any): Geometry | null {
  if (!gj) return null
  if (gj.type === 'FeatureCollection') {
    const feats = (gj.features ?? []).filter((f: any) => f.geometry)
    return feats.length === 1 ? feats[0].geometry : null
  }
  if (gj.type === 'Feature') return gj.geometry ?? null
  return gj
}

function metres(g: Geometry): Line[] {
  const parts: number[][][] = g.type === 'LineString' ? [g.coordinates] : g.type === 'MultiLineString' ? g.coordinates : []
  return parts.map((p) => p.map(([x, y]) => albersForward(x, y)))
}

const partsLength = (parts: Line[]): number => parts.reduce((s, p) => s + (p.length > 1 ? lineLength(p) : 0), 0)

export interface ReachWetlands {
  wetlandM2: Record<string, number>
  wetlandM2Total: number
  stripM2: number
  reachM: number
  flowlines: { nhdplusid: number, share: number }[]
}

/** bundle.reach_wetlands: the NWI wetland area within 150 m of the assessment reach, or null where
 * the bundle cannot answer. */
export async function reachWetlands(hr: Dataset, values: ValueTables, nhdplusid: number, reachGeojson: unknown): Promise<ReachWetlands | null> {
  try {
    const reach = singleGeometry(reachGeojson)
    if (!reach) return null
    const reachM = metres(reach)
    const reachLen = partsLength(reachM)
    if (!reachLen) return null
    const rec = await hr.flowline('by_id', nhdplusid)
    if (!rec) return null
    const wantedKm = reachLen / 1000.0 + (rec.lengthkm || 0.0) + 0.3
    const chain = [rec]
    let total = rec.lengthkm || 0.0
    while (total < wantedKm && chain[chain.length - 1].uphydroseq && chain.length < 26) {
      const nxt = await hr.flowline('by_hs', chain[chain.length - 1].uphydroseq as number)
      if (!nxt) break
      chain.push(nxt)
      total += nxt.lengthkm || 0.0
    }
    const area: Record<string, number> = Object.fromEntries(NWI_SYSTEMS.map(([, name]) => [name, 0.0]))
    let strip = 0.0
    const used: { nhdplusid: number, share: number }[] = []
    for (const r of chain) {
      const lineM = metres(r.geometry as Geometry)
      const len = partsLength(lineM)
      if (!len) continue
      const share = Math.min(1.0, lengthWithin(reachM, lineM, 1.0) / len)
      if (share <= 0.0) continue
      const found = await hr.find('by_id', [r.nhdplusid])
      if (!found.length || !(await values.present('extras', found[0].region.vpu))) return null
      const row = found[0].rows[0]
      const t = await values.nwiStrips(found[0].region.vpu, [row])
      if (!t) return null
      for (const [code, name] of NWI_SYSTEMS) area[name] += share * (t[`nwi_${code}_m2`][row] || 0.0)
      strip += share * (t.strip_m2[row] || 0.0)
      used.push({ nhdplusid: r.nhdplusid, share: pyRound(share, 4) })
    }
    if (!used.length) return null
    const sum = pySum(Object.values(area))
    return {
      wetlandM2: Object.fromEntries(Object.entries(area).map(([k, v]) => [k, pyRound(v, 1)])),
      wetlandM2Total: pyRound(sum, 1), stripM2: pyRound(strip, 1), reachM: pyRound(reachLen, 1), flowlines: used,
    }
  } catch {
    return null
  }
}

export interface WetlandsAlongReach {
  acres: number
  stripAcres: number
  pctOfStrip: number
  bySystem: Record<string, number>
  flowlines: number
  halfWidthM: number
}

/** SFARI nwi.wetlands_along_reach: the NWI wetland acres within 150 m of the engine's reach (``record``:
 * its site.nhdplusId and reach.geometry, or ``reachGeojson``), or null. */
export async function wetlandsAlongReach(hr: Dataset, values: ValueTables, record: any, reachGeojson?: unknown): Promise<WetlandsAlongReach | null> {
  try {
    const nid = record?.site?.nhdplusId
    const geom = reachGeojson || record?.reach?.geometry
    const got = nid && geom ? await reachWetlands(hr, values, Number(nid), geom) : null
    if (!got || !got.stripM2) return null
    const acres = got.wetlandM2Total / M2_PER_ACRE
    return {
      acres: pyRound(acres, 1), stripAcres: pyRound(got.stripM2 / M2_PER_ACRE, 1),
      pctOfStrip: pyRound(100.0 * got.wetlandM2Total / got.stripM2, 1),
      bySystem: Object.fromEntries(Object.entries(got.wetlandM2).map(([k, v]) => [k, pyRound(v / M2_PER_ACRE, 2)])),
      flowlines: got.flowlines.length, halfWidthM: NWI_STRIP_HALF_WIDTH_M,
    }
  } catch {
    return null
  }
}

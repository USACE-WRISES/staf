// The assessment reach: the mainstem trimmed to the reach length upstream of the snapped point
// (site_engine reach.py derive_reach and geometry.reach_from_lines; EASI's delineation.derive_reach for
// a reach of the covered network).
//
// The lines are projected to EPSG:5070, merged as GEOS's LineMerger merges them, oriented by the anchor
// reach's outlet (its end within a metre of one end of the merged line), and cut with shapely's
// substring at the click's projection on the line. The reach goes back to degrees unrounded.
import { pyFixed } from '../app/pyfmt'
import { FT_PER_M } from '../streams/display'
import { interpolate, lineLength, pointToSegment, type Line, type XY } from '../streams/geom'
import { albersForward, albersToLonLat } from '../xs/projExact'
import type { FlowlineRecord } from './bundle'
import type { Dataset } from './dataset'

export type LineGeometry = { type: 'LineString', coordinates: XY[] } | { type: 'MultiLineString', coordinates: XY[][] }
type Geom5070 = { type: 'LineString', line: Line } | { type: 'MultiLineString', lines: Line[] } | { type: 'Point', at: XY }

/** A GeoJSON FeatureCollection of one geometry, as geopandas writes a GeoSeries (``__geo_interface__``). */
export interface GeoSeriesJson {
  type: 'FeatureCollection'
  features: { id: string, type: 'Feature', properties: Record<string, never>, geometry: any, bbox: number[] }[]
  bbox: number[]
}

export interface Reach {
  geojson: GeoSeriesJson | null
  lengthFt: number | null
  warnings: string[]
}

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

function partsOf(g: LineGeometry): XY[][] {
  return g.type === 'LineString' ? [g.coordinates] : g.coordinates
}

function project(g: LineGeometry): Geom5070 {
  const lines = partsOf(g).map((part) => part.map(([x, y]) => albersForward(x, y)))
  return g.type === 'LineString' ? { type: 'LineString', line: lines[0] } : { type: 'MultiLineString', lines }
}

/** geopandas' ``__geo_interface__`` of one geometry (bbox from its coordinates). */
export function geoSeries(geometry: any): GeoSeriesJson {
  const xs: number[] = []
  const ys: number[] = []
  const walk = (c: any) => {
    if (typeof c[0] === 'number') {
      xs.push(c[0])
      ys.push(c[1])
    } else c.forEach(walk)
  }
  walk(geometry.coordinates)
  const bbox = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  return { type: 'FeatureCollection', features: [{ id: '0', type: 'Feature', properties: {}, geometry, bbox }], bbox }
}

// ------------------------------------------------------------------ GEOS LineMerger (undirected)

/** GEOS Quadrant::quadrant. */
function quadrant(dx: number, dy: number): number {
  if (dx >= 0) return dy >= 0 ? 0 : 3
  return dy >= 0 ? 1 : 2
}

interface Node { at: XY, out: DirEdge[], sorted: boolean, marked: boolean }
interface Edge { line: Line, marked: boolean }
interface DirEdge { from: Node, to: Node, p0: XY, p1: XY, quadrant: number, forward: boolean, edge: Edge, sym: DirEdge | null }

/** RepeatedPointRemover: consecutive duplicates dropped. */
function noRepeats(line: Line): Line {
  const out: Line = []
  for (const p of line) {
    const last = out[out.length - 1]
    if (!last || last[0] !== p[0] || last[1] !== p[1]) out.push(p)
  }
  return out
}

function sortedOut(node: Node): DirEdge[] {
  if (!node.sorted) {
    // DirectedEdge::compareDirection: by quadrant, then counter-clockwise
    node.out.sort((a, b) => {
      if (a.quadrant !== b.quadrant) return a.quadrant - b.quadrant
      const det = (b.p1[0] - b.p0[0]) * (a.p1[1] - b.p0[1]) - (b.p1[1] - b.p0[1]) * (a.p1[0] - b.p0[0])
      return det > 0 ? 1 : det < 0 ? -1 : 0
    })
    node.sorted = true
  }
  return node.out
}

/** shapely.line_merge of LineStrings (GEOS LineMerger, undirected): the merged lines in GEOS's order. */
export function lineMerge(lines: Line[]): Line[] {
  const nodes = new Map<string, Node>()
  const nodeAt = (p: XY): Node => {
    const k = `${p[0]},${p[1]}`
    let n = nodes.get(k)
    if (!n) {
      n = { at: p, out: [], sorted: false, marked: false }
      nodes.set(k, n)
    }
    return n
  }
  for (const line of lines) {
    if (!line.length) continue
    const c = noRepeats(line)
    if (c.length <= 1) continue
    const start = nodeAt(c[0])
    const end = nodeAt(c[c.length - 1])
    const edge: Edge = { line, marked: false }
    const mk = (from: Node, to: Node, dir: XY, forward: boolean): DirEdge => ({
      from, to, p0: from.at, p1: dir, quadrant: quadrant(dir[0] - from.at[0], dir[1] - from.at[1]), forward, edge, sym: null,
    })
    const d0 = mk(start, end, c[1], true)
    const d1 = mk(end, start, c[c.length - 2], false)
    d0.sym = d1
    d1.sym = d0
    start.out.push(d0)
    start.sorted = false
    end.out.push(d1)
    end.sorted = false
  }
  // the node map is ordered by coordinate: x, then y
  const ordered = [...nodes.values()].sort((a, b) => a.at[0] - b.at[0] || a.at[1] - b.at[1])
  const strings: DirEdge[][] = []
  const next = (de: DirEdge): DirEdge | null => {
    const to = de.to
    if (to.out.length !== 2) return null
    const out = sortedOut(to)
    return out[0] === de.sym ? out[1] : out[0]
  }
  const startAt = (node: Node) => {
    for (const de of sortedOut(node)) {
      if (de.edge.marked) continue
      const string: DirEdge[] = []
      let cur: DirEdge | null = de
      do {
        string.push(cur)
        cur.edge.marked = true
        cur = next(cur)
      } while (cur !== null && cur !== de)
      strings.push(string)
    }
  }
  for (const node of ordered) {
    if (node.out.length !== 2) {
      startAt(node)
      node.marked = true
    }
  }
  for (const node of ordered) {
    if (!node.marked) {
      startAt(node)
      node.marked = true
    }
  }
  // EdgeString::getCoordinates: each line in its direction, repeats dropped; the majority direction wins
  return strings.map((string) => {
    const coords: Line = []
    let fwd = 0
    let rev = 0
    for (const de of string) {
      if (de.forward) fwd++
      else rev++
      const pts = de.forward ? de.edge.line : [...de.edge.line].reverse()
      for (const p of pts) {
        const last = coords[coords.length - 1]
        if (!last || last[0] !== p[0] || last[1] !== p[1]) coords.push(p)
      }
    }
    return rev > fwd ? coords.reverse() : coords
  })
}

// ------------------------------------------------------------------ linear referencing

/** GEOS LengthIndexedLine::project (LengthIndexOfPoint::indexOf) for a line. */
export function projectOnto(line: Line, px: number, py: number): number {
  let minDistance = Infinity
  let measure = -1
  let start = 0
  for (let i = 0; i < line.length - 1; i++) {
    const [ax, ay] = line[i]
    const [bx, by] = line[i + 1]
    const d = pointToSegment(px, py, ax, ay, bx, by)
    const dx = ax - bx
    const dy = ay - by
    const len = Math.sqrt(dx * dx + dy * dy)
    let factor: number
    if (px === ax && py === ay) factor = 0
    else if (px === bx && py === by) factor = 1
    else {
      const sx = bx - ax
      const sy = by - ay
      factor = ((px - ax) * sx + (py - ay) * sy) / (sx * sx + sy * sy)
    }
    const m = factor <= 0 ? start : factor <= 1 ? start + factor * len : start + len
    if (d < minDistance && m > -1) {
      measure = m
      minDistance = d
    }
    start += len
  }
  return measure
}

/** shapely.ops.substring (not normalized): a line, or a point where the distances meet. */
export function substringOf(line: Line, startDist: number, endDist: number): Geom5070 {
  const length = lineLength(line)
  const at = (d: number): Geom5070 => ({ type: 'Point', at: interpolate(line, d) })
  if (startDist === endDist) return at(startDist)
  if (startDist >= length && endDist >= length) return at(length)
  if (-startDist >= length && -endDist >= length) return at(0)
  if (startDist < 0 && 0 < endDist && Math.abs(startDist) + endDist === length) return at(endDist)
  if (endDist < 0 && 0 < startDist && Math.abs(endDist) + startDist === length) return at(startDist)
  const startPoint = interpolate(line, startDist)
  const endPoint = interpolate(line, endDist)
  if (startDist < 0) startDist = length + startDist
  if (endDist < 0) endDist = length + endDist
  const reverse = startDist > endDist
  if (reverse) [startDist, endDist] = [endDist, startDist]
  startDist = Math.max(startDist, 0)
  const out: Line = [reverse ? endPoint : startPoint]
  let current = 0
  for (let i = 0; i < line.length - 1; i++) {
    const p1 = line[i]
    const p2 = line[i + 1]
    if (startDist < current && current < endDist) out.push(p1)
    else if (current >= endDist) break
    const dx = p2[0] - p1[0]
    const dy = p2[1] - p1[1]
    current += Math.sqrt(dx * dx + dy * dy)
  }
  out.push(reverse ? startPoint : endPoint)
  return { type: 'LineString', line: reverse ? out.reverse() : out }
}

function distanceToLine(line: Line, px: number, py: number): number {
  let d = Infinity
  for (let i = 0; i < line.length - 1; i++) d = Math.min(d, pointToSegment(px, py, line[i][0], line[i][1], line[i + 1][0], line[i + 1][1]))
  if (line.length === 1) d = Math.hypot(line[0][0] - px, line[0][1] - py)
  return d
}

const dist = (a: XY, b: XY) => Math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

/** Which end of the merged line is the anchor reach's outlet (geometry._outlet_at_start): true when
 * its first point, false when its last, null when both or neither match. */
function outletAtStart(merged: Line, own: LineGeometry[]): boolean | null {
  if (!own.length) return null
  const parts: Line[] = []
  for (const g of own) for (const part of partsOf(g)) parts.push(part.map(([x, y]) => albersForward(x, y)))
  let c: Line
  if (parts.length === 1) c = parts[0]
  else {
    const merged2 = lineMerge(parts)
    c = merged2.reduce((best, l) => (lineLength(l) > lineLength(best) ? l : best), merged2[0])
  }
  const ends = [c[0], c[c.length - 1]]
  const m0 = merged[0]
  const m1 = merged[merged.length - 1]
  const hits0 = ends.some((e) => dist(e, m0) < 1.0)
  const hits1 = ends.some((e) => dist(e, m1) < 1.0)
  if (hits0 && !hits1) return true
  if (hits1 && !hits0) return false
  return null
}

/** Merge, orient and trim the mainstem to ``lengthFt`` upstream of the snap (geometry.reach_from_lines;
 * EASI's delineation._reach_from_lines). */
export function reachFromLines(geoms: LineGeometry[], own: LineGeometry[], lat: number, lon: number, lengthFt: number,
  warnings: string[]): Reach {
  const lengthM = lengthFt / FT_PER_M
  const lines = geoms.map(project)
  const [sx, sy] = albersForward(lon, lat)
  let merged: Line
  if (lines.length === 1) {
    const g = lines[0]
    if (g.type === 'LineString') merged = g.line
    else merged = pickNearest(g.type === 'MultiLineString' ? g.lines : [], sx, sy, warnings)
  } else {
    if (lines.some((g) => g.type !== 'LineString')) throw new Error('Multi-part geometries do not provide a coordinate sequence')
    const out = lineMerge(lines.map((g) => (g as { line: Line }).line))
    merged = out.length === 1 ? out[0] : pickNearest(out, sx, sy, warnings)
  }
  const total = lineLength(merged)
  const proj = projectOnto(merged, sx, sy)
  let outletFirst = outletAtStart(merged, own)
  if (outletFirst === null) outletFirst = proj <= total - proj
  const seg = outletFirst ? substringOf(merged, proj, Math.min(proj + lengthM, total)) : substringOf(merged, Math.max(0, proj - lengthM), proj)
  const segLength = seg.type === 'LineString' ? lineLength(seg.line) : 0
  const actualFt = segLength * FT_PER_M
  if (actualFt < lengthFt - 1) warnings.push(`only ${pyFixed(actualFt, 0)} ft of mainstem available upstream`)
  const back = (p: XY): XY => albersToLonLat(p[0], p[1])
  const geometry = seg.type === 'LineString'
    ? { type: 'LineString', coordinates: seg.line.map(back) }
    : { type: 'Point', coordinates: back(seg.type === 'Point' ? seg.at : [0, 0]) }
  return { geojson: geoSeries(geometry), lengthFt: pyRound(actualFt, 1), warnings }
}

function pickNearest(lines: Line[], sx: number, sy: number, warnings: string[]): Line {
  let best = lines[0]
  let bestD = Infinity
  for (const l of lines) {
    const d = distanceToLine(l, sx, sy)
    if (d < bestD) {
      bestD = d
      best = l
    }
  }
  warnings.push('flowline had gaps; used nearest mainstem component')
  return best
}

// ------------------------------------------------------------------ the two reaches

const HOP_CAP = 25

/** The engine's reach on the HR mainstem (reach.derive_reach): ``uphydroseq`` until the line walked
 * clears the needed length, then trimmed. Never throws. EASI's copy (nhd_hr.derive_reach_hr) words
 * its failures ``HR reach derivation failed`` and names the reach it lacks. */
export async function deriveReachHr(hr: Dataset, anchor: FlowlineRecord, lat: number, lon: number, lengthFt: number,
  failure = 'reach derivation failed'): Promise<Reach> {
  const warnings: string[] = []
  try {
    if (!anchor?.geometry) {
      const what = failure === 'reach derivation failed' ? 'the reach' : `nhdplusid ${anchor?.nhdplusid}`
      return { geojson: null, lengthFt: null, warnings: [`no HR flowline geometry for ${what}`] }
    }
    const lengthKm = (lengthFt / FT_PER_M) / 1000.0
    const ownKm = anchor.lengthkm || 0.0
    const neededKm = pyRound(Math.max(lengthKm * 4, ownKm + lengthKm) + 0.3, 1)
    const geoms: LineGeometry[] = [anchor.geometry as LineGeometry]
    let total = ownKm
    let up = anchor.uphydroseq ?? null
    for (let i = 0; i < HOP_CAP; i++) {
      if (total >= neededKm || !up) break
      const nxt = await hr.flowline('by_hs', up)
      if (!nxt?.geometry) break
      geoms.push(nxt.geometry as LineGeometry)
      total += nxt.lengthkm || 0.0
      up = nxt.uphydroseq ?? null
    }
    return reachFromLines(geoms, [geoms[0]], lat, lon, lengthFt, warnings)
  } catch (err) {
    return { geojson: null, lengthFt: null, warnings: [`${failure}: ${err instanceof Error ? err.message : err}`] }
  }
}

/** The COMID's flowline and the mainstem above it within ``km`` (bundle.v2_mainstem): NLDI rounds the
 * distance to whole kilometres and takes the next line while the line walked is shorter. */
export async function v2Mainstem(v2: Dataset, comid: number, km: number): Promise<FlowlineRecord[] | null> {
  const first = await v2.flowline('by_id', comid)
  if (!first) return null
  const limit = pyRound(km, 0)
  const out = [first]
  let total = first.lengthkm || 0.0
  let rec = first
  for (let i = 0; i < 5000; i++) {
    const nxt = rec.uphydroseq
    if (total >= limit || !nxt) return out
    const recs = await v2.reach('by_hs', nxt)
    if (recs.length !== 1) return null
    rec = recs[0]
    out.push(rec)
    total += rec.lengthkm || 0.0
  }
  return null
}

/** EASI's reach on the covered network (delineation.derive_reach): the COMID's line and its upstream
 * mainstem, sized to clear the line's own length, merged and trimmed. */
export async function deriveReachV2(v2: Dataset, comid: number, lat: number, lon: number, lengthFt: number): Promise<Reach> {
  const warnings: string[] = []
  const lengthKm = (lengthFt / FT_PER_M) / 1000.0
  const ownRec = await v2.flowline('by_id', comid)
  const own: LineGeometry[] = ownRec?.geometry ? [ownRec.geometry as LineGeometry] : []
  let ownKm = 0.0
  for (const g of own) for (const part of partsOf(g)) ownKm += lineLength(part.map(([x, y]) => albersForward(x, y)))
  ownKm /= 1000.0
  const navKm = pyRound(Math.max(lengthKm * 4, ownKm + lengthKm) + 0.3, 1)
  const main = await v2Mainstem(v2, comid, Math.max(1.0, navKm))
  let geoms: LineGeometry[] = (main ?? []).map((r) => r.geometry as LineGeometry)
  if (!main) warnings.push('upstream navigation failed: the mainstem leaves the published data')
  if (!geoms.length) geoms = own
  if (!geoms.length) return { geojson: null, lengthFt: null, warnings: warnings.length ? warnings : ['no flowline geometry for reach'] }
  return reachFromLines(geoms, own, lat, lon, lengthFt, warnings)
}

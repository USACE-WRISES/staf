// Polygons simplified as GEOS 3.13 TopologyPreservingSimplifier simplifies them (shapely's
// ``simplify(tolerance, preserve_topology=True)``), for the map's display of a large watershed
// (delineation.display_simplify).
//
// Every ring is a TaggedLineString; Douglas-Peucker flattens a section only when the flat segment
// crosses no remaining input segment and no output segment, and moves no other ring to its other side
// (ComponentJumpChecker); a closed ring may then drop its start point (simplifyRingEndpoint). The
// predicates are GEOS's: the orientation test exact (its filter, then exact arithmetic where the
// filter cannot decide), LineIntersector's interior-intersection rule, RayCrossingCounter's parity.
import { pointToSegment } from '../streams/geom'

type Pt = [number, number]

// ------------------------------------------------------------------ predicates

/** A double as an exact mantissa and power of two. */
function exact(x: number): [bigint, number] {
  if (x === 0) return [0n, 0]
  const view = new DataView(new ArrayBuffer(8))
  view.setFloat64(0, x)
  const hi = view.getUint32(0)
  const lo = view.getUint32(4)
  const sign = hi >>> 31 ? -1n : 1n
  const biased = (hi >>> 20) & 0x7ff
  const frac = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo)
  if (biased === 0) return [sign * frac, -1074]
  return [sign * ((1n << 52n) | frac), biased - 1075]
}

/** The exact sign of (b - a) x (c - b)'s determinant, as CGAlgorithmsDD::orientationIndex. */
function exactOrientation(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  const vals = [ax, ay, bx, by, cx, cy].map(exact)
  const minExp = Math.min(...vals.filter(([m]) => m !== 0n).map(([, e]) => e), 0)
  const [Ax, Ay, Bx, By, Cx, Cy] = vals.map(([m, e]) => m << BigInt(e - minExp))
  const det = (Bx - Ax) * (Cy - By) - (By - Ay) * (Cx - Bx)
  return det > 0n ? 1 : det < 0n ? -1 : 0
}

/** Orientation::index (CGAlgorithmsDD::orientationIndex): 1 when c is left of a->b, -1 right, 0 on. */
export function orientation(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  // orientationIndexFilter
  const detleft = (ax - cx) * (by - cy)
  const detright = (ay - cy) * (bx - cx)
  const det = detleft - detright
  let detsum: number
  if (detleft > 0) {
    if (detright <= 0) return Math.sign(det)
    detsum = detleft + detright
  } else if (detleft < 0) {
    if (detright >= 0) return Math.sign(det)
    detsum = -detleft - detright
  } else {
    return Math.sign(det)
  }
  const errbound = 1e-15 * detsum
  if (det >= errbound || -det >= errbound) return Math.sign(det)
  return exactOrientation(ax, ay, bx, by, cx, cy)
}

function envIntersects(p1: Pt, p2: Pt, q1: Pt, q2: Pt): boolean {
  const minq = Math.min(q1[0], q2[0])
  const maxq = Math.max(q1[0], q2[0])
  const minp = Math.min(p1[0], p2[0])
  const maxp = Math.max(p1[0], p2[0])
  if (minp > maxq || maxp < minq) return false
  const minqy = Math.min(q1[1], q2[1])
  const maxqy = Math.max(q1[1], q2[1])
  const minpy = Math.min(p1[1], p2[1])
  const maxpy = Math.max(p1[1], p2[1])
  return !(minpy > maxqy || maxpy < minqy)
}

function inEnv(p1: Pt, p2: Pt, q: Pt): boolean {
  return q[0] >= Math.min(p1[0], p2[0]) && q[0] <= Math.max(p1[0], p2[0])
    && q[1] >= Math.min(p1[1], p2[1]) && q[1] <= Math.max(p1[1], p2[1])
}

const eq = (a: Pt, b: Pt) => a[0] === b[0] && a[1] === b[1]

/** LineIntersector.computeIntersection then isInteriorIntersection: the segments meet at a point
 * other than an endpoint of each. */
function interiorIntersection(p1: Pt, p2: Pt, q1: Pt, q2: Pt): boolean {
  if (!envIntersects(p1, p2, q1, q2)) return false
  const Pq1 = orientation(p1[0], p1[1], p2[0], p2[1], q1[0], q1[1])
  const Pq2 = orientation(p1[0], p1[1], p2[0], p2[1], q2[0], q2[1])
  if ((Pq1 > 0 && Pq2 > 0) || (Pq1 < 0 && Pq2 < 0)) return false
  const Qp1 = orientation(q1[0], q1[1], q2[0], q2[1], p1[0], p1[1])
  const Qp2 = orientation(q1[0], q1[1], q2[0], q2[1], p2[0], p2[1])
  if ((Qp1 > 0 && Qp2 > 0) || (Qp1 < 0 && Qp2 < 0)) return false
  let pts: Pt[]
  if (Pq1 === 0 && Pq2 === 0 && Qp1 === 0 && Qp2 === 0) {
    // computeCollinearIntersection
    const q1inP = inEnv(p1, p2, q1)
    const q2inP = inEnv(p1, p2, q2)
    const p1inQ = inEnv(q1, q2, p1)
    const p2inQ = inEnv(q1, q2, p2)
    if (q1inP && q2inP) pts = [q1, q2]
    else if (p1inQ && p2inQ) pts = [p1, p2]
    else if (q1inP && p1inQ) pts = eq(q1, p1) && !q2inP && !p2inQ ? [q1] : [q1, p1]
    else if (q1inP && p2inQ) pts = eq(q1, p2) && !q2inP && !p1inQ ? [q1] : [q1, p2]
    else if (q2inP && p1inQ) pts = eq(q2, p1) && !q1inP && !p2inQ ? [q2] : [q2, p1]
    else if (q2inP && p2inQ) pts = eq(q2, p2) && !q1inP && !p1inQ ? [q2] : [q2, p2]
    else return false
  } else if (Pq1 === 0 || Pq2 === 0 || Qp1 === 0 || Qp2 === 0) {
    let p: Pt
    if (eq(p1, q1)) p = p1
    else if (eq(p1, q2)) p = p1
    else if (eq(p2, q1)) p = p2
    else if (eq(p2, q2)) p = p2
    else if (Pq1 === 0) p = q1
    else if (Pq2 === 0) p = q2
    else if (Qp1 === 0) p = p1
    else p = p2
    pts = [p]
  } else {
    return true                       // a proper intersection is interior to both
  }
  return pts.some((p) => !(eq(p, p1) || eq(p, p2))) || pts.some((p) => !(eq(p, q1) || eq(p, q2)))
}

/** RayCrossingCounter's crossings of a ray to the right of the point. */
class Crossings {
  count = 0
  constructor(readonly pt: Pt) {}

  add(p1: Pt, p2: Pt): void {
    const [x, y] = this.pt
    if (p1[0] < x && p2[0] < x) return
    if (x === p2[0] && y === p2[1]) return
    if (p1[1] === y && p2[1] === y) return
    if ((p1[1] > y && p2[1] <= y) || (p2[1] > y && p1[1] <= y)) {
      let sign = orientation(p1[0], p1[1], p2[0], p2[1], x, y)
      if (sign === 0) return
      if (p2[1] < p1[1]) sign = -sign
      if (sign > 0) this.count++
    }
  }
}

// ------------------------------------------------------------------ the tagged lines

interface Seg { p0: Pt, p1: Pt, parent: TaggedLine | null, index: number }

class TaggedLine {
  segs: Seg[] = []
  result: Seg[] = []

  constructor(readonly pts: Pt[], readonly minSize: number, readonly isRing: boolean) {
    for (let i = 0; i < pts.length - 1; i++) this.segs.push({ p0: pts[i], p1: pts[i + 1], parent: this, index: i })
  }

  resultSize(): number {
    return this.result.length === 0 ? 0 : this.result.length + 1
  }

  componentPoint(): Pt {
    return this.result.length ? this.result[0].p0 : this.pts[1]
  }

  resultCoordinates(): Pt[] {
    if (!this.result.length) return []
    return [...this.result.map((s) => s.p0), this.result[this.result.length - 1].p1]
  }
}

/** A segment index (LineSegmentIndex: a quadtree there, a grid here; queries answer the same set). */
class SegIndex {
  private cells = new Map<number, Set<Seg>>()

  constructor(private readonly x0: number, private readonly y0: number, private readonly size: number) {}

  private keys(a: Pt, b: Pt): number[] {
    const i0 = Math.floor((Math.min(a[0], b[0]) - this.x0) / this.size)
    const i1 = Math.floor((Math.max(a[0], b[0]) - this.x0) / this.size)
    const j0 = Math.floor((Math.min(a[1], b[1]) - this.y0) / this.size)
    const j1 = Math.floor((Math.max(a[1], b[1]) - this.y0) / this.size)
    const out: number[] = []
    for (let i = i0; i <= i1; i++) for (let j = j0; j <= j1; j++) out.push(i * 1_000_003 + j)
    return out
  }

  add(seg: Seg): void {
    for (const k of this.keys(seg.p0, seg.p1)) {
      let set = this.cells.get(k)
      if (!set) this.cells.set(k, (set = new Set()))
      set.add(seg)
    }
  }

  remove(seg: Seg): void {
    for (const k of this.keys(seg.p0, seg.p1)) this.cells.get(k)?.delete(seg)
  }

  query(q: Seg): Seg[] {
    const out = new Set<Seg>()
    for (const k of this.keys(q.p0, q.p1)) {
      for (const s of this.cells.get(k) ?? []) if (envIntersects(s.p0, s.p1, q.p0, q.p1)) out.add(s)
    }
    return [...out]
  }
}

class Simplifier {
  private line!: TaggedLine

  constructor(private readonly input: SegIndex, private readonly output: SegIndex, private readonly components: TaggedLine[]) {}

  simplify(line: TaggedLine, tol: number): void {
    this.line = line
    const pts = line.pts
    if (!pts.length) return
    this.section(0, pts.length - 1, 0, tol)
    if (line.isRing && pts.length >= 4 && eq(pts[0], pts[pts.length - 1])) this.ringEndpoint(tol)
  }

  private section(i: number, j: number, depth: number, tol: number): void {
    const line = this.line
    depth += 1
    if (i + 1 === j) {
      line.result.push({ ...line.segs[i] })
      return
    }
    let valid = true
    if (line.resultSize() < line.minSize && depth + 1 < line.minSize) valid = false
    const pts = line.pts
    let maxDist = -1
    let far = i
    for (let k = i + 1; k < j; k++) {
      const d = pointToSegment(pts[k][0], pts[k][1], pts[i][0], pts[i][1], pts[j][0], pts[j][1])
      if (d > maxDist) {
        maxDist = d
        far = k
      }
    }
    if (maxDist < 0) {
      for (let k = i; k < j; k++) line.result.push({ ...line.segs[k] })
      return
    }
    if (maxDist > tol) valid = false
    const flat: Seg = { p0: pts[i], p1: pts[j], parent: null, index: 0 }
    if (valid) valid = this.validSection(i, j, flat)
    if (valid) {
      this.output.add(flat)
      for (let k = i; k < j; k++) this.input.remove(line.segs[k])
      line.result.push(flat)
      return
    }
    this.section(i, far, depth, tol)
    this.section(far, j, depth, tol)
  }

  private ringEndpoint(tol: number): void {
    const line = this.line
    if (line.resultSize() <= line.minSize) return
    const first = line.result[0]
    const last = line.result[line.result.length - 1]
    const simp: Seg = { p0: last.p0, p1: first.p1, parent: null, index: 0 }
    const end = first.p0
    if (pointToSegment(end[0], end[1], simp.p0[0], simp.p0[1], simp.p1[0], simp.p1[1]) <= tol && this.validPair(first, last, simp)) {
      this.input.remove(first)
      this.input.remove(last)
      this.output.remove(first)
      this.output.remove(last)
      first.p0 = last.p0
      line.result.pop()
      this.output.add(first)
    }
  }

  private invalid(a: Seg, b: Seg): boolean {
    if ((eq(a.p0, b.p0) && eq(a.p1, b.p1)) || (eq(a.p0, b.p1) && eq(a.p1, b.p0))) return true
    return interiorIntersection(a.p0, a.p1, b.p0, b.p1)
  }

  private outputHit(flat: Seg): boolean {
    return this.output.query(flat).some((s) => this.invalid(s, flat))
  }

  private inputHit(flat: Seg, section: [number, number] | null): boolean {
    for (const s of this.input.query(flat)) {
      if (!this.invalid(s, flat)) continue
      if (section && s.parent === this.line) {
        const [a, b] = section
        const k = s.index
        if (a <= b ? k >= a && k < b : k >= a || k <= b) continue
      }
      return true
    }
    return false
  }

  private validSection(i: number, j: number, flat: Seg): boolean {
    if (this.outputHit(flat)) return false
    if (this.inputHit(flat, [i, j])) return false
    return !this.jumpSection(i, j, flat)
  }

  private validPair(first: Seg, last: Seg, flat: Seg): boolean {
    if (orientation(flat.p0[0], flat.p0[1], flat.p1[0], flat.p1[1], first.p0[0], first.p0[1]) === 0) return true
    if (this.outputHit(flat)) return false
    if (this.inputHit(flat, null)) return false
    return !this.jumpPair(first, last, flat)
  }

  private jumpSection(start: number, end: number, flat: Seg): boolean {
    const pts = this.line.pts
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
    for (let i = start; i <= end; i++) {
      x0 = Math.min(x0, pts[i][0])
      y0 = Math.min(y0, pts[i][1])
      x1 = Math.max(x1, pts[i][0])
      y1 = Math.max(y1, pts[i][1])
    }
    for (const comp of this.components) {
      if (comp === this.line) continue
      const c = comp.componentPoint()
      if (c[0] < x0 || c[0] > x1 || c[1] < y0 || c[1] > y1) continue
      const section = new Crossings(c)
      for (let i = start; i < end; i++) section.add(pts[i], pts[i + 1])
      const seg = new Crossings(c)
      seg.add(flat.p0, flat.p1)
      if (section.count % 2 !== seg.count % 2) return true
    }
    return false
  }

  private jumpPair(s1: Seg, s2: Seg, flat: Seg): boolean {
    const xs = [s1.p0[0], s1.p1[0], s2.p0[0], s2.p1[0]]
    const ys = [s1.p0[1], s1.p1[1], s2.p0[1], s2.p1[1]]
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    for (const comp of this.components) {
      if (comp === this.line) continue
      const c = comp.componentPoint()
      if (c[0] < x0 || c[0] > x1 || c[1] < y0 || c[1] > y1) continue
      const section = new Crossings(c)
      section.add(s1.p0, s1.p1)
      section.add(s2.p0, s2.p1)
      const seg = new Crossings(c)
      seg.add(flat.p0, flat.p1)
      if (section.count % 2 !== seg.count % 2) return true
    }
    return false
  }
}

/** Polygons (each its shell then holes, closed rings) simplified as GEOS does; the GeoJSON geometry
 * GeometryTransformer builds: a Polygon when one is left, else a MultiPolygon. */
export function simplifyPolygons(polys: Pt[][][], tol: number): { type: 'Polygon' | 'MultiPolygon', coordinates: any } {
  const lines = polys.map((rings) => rings.map((ring) => new TaggedLine(ring, eq(ring[0], ring[ring.length - 1]) ? 4 : 2, true)))
  const all = lines.flat()
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const l of all) for (const [x, y] of l.pts) {
    x0 = Math.min(x0, x)
    y0 = Math.min(y0, y)
    x1 = Math.max(x1, x)
    y1 = Math.max(y1, y)
  }
  const size = Math.max(x1 - x0, y1 - y0, 1e-9) / 64
  const input = new SegIndex(x0, y0, size)
  const output = new SegIndex(x0, y0, size)
  for (const l of all) for (const s of l.segs) input.add(s)
  for (const l of all) new Simplifier(input, output, all).simplify(l, tol)
  const out = lines.map((rings) => rings.map((l) => l.resultCoordinates()))
  return out.length === 1 ? { type: 'Polygon', coordinates: out[0] } : { type: 'MultiPolygon', coordinates: out }
}

/** delineation.display_simplify: a watershed of more than ``maxVertices`` coordinates simplified
 * (topology-preserving, ``tolDeg``) for the map; smaller ones unchanged. */
export function displaySimplify<T extends { features: { geometry: any }[] }>(fc: T | null, maxVertices = 1500, tolDeg = 0.0002): T | null {
  if (!fc?.features?.length) return fc
  const polysOf = (g: any): Pt[][][] => (g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [])
  let count = 0
  for (const f of fc.features) for (const rings of polysOf(f.geometry)) for (const r of rings) count += r.length
  if (count <= maxVertices) return fc
  const features = fc.features.map((f) => {
    const geometry = simplifyPolygons(polysOf(f.geometry), tolDeg)
    const xs: number[] = []
    const ys: number[] = []
    const walk = (c: any) => (typeof c[0] === 'number' ? (xs.push(c[0]), ys.push(c[1])) : c.forEach(walk))
    walk(geometry.coordinates)
    return { ...f, geometry, bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] }
  })
  const bb = features.map((f) => f.bbox)
  return {
    ...fc, features,
    bbox: [Math.min(...bb.map((b) => b[0])), Math.min(...bb.map((b) => b[1])), Math.max(...bb.map((b) => b[2])), Math.max(...bb.map((b) => b[3]))],
  }
}

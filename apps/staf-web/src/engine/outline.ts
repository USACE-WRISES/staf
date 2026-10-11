// The outline of a set of catchments from their shared borders (site_engine _hrslim/arcs.py outline),
// in whole grid cells.
//
// Every boundary arc (exactly one side in the set) is turned so the set is on its left, and the arcs are
// chained end to start into rings. Where two corners of the set touch (two arcs leave one node) the
// chain takes the left turn, which keeps parts touching at a corner apart; a ring that still passes such
// a node twice (a hole touching its shell, or two holes touching) swaps the pairing there, splitting
// it, so every ring is simple. Counter-clockwise rings are shells, clockwise ones holes; a hole belongs
// to the smallest shell around a point a quarter cell inside its first edge. The steps, the orders and
// the tie rules are the Python's, so the rings come out vertex for vertex as shapely builds them.
import { arcVertices, type Encoding } from './bundle'

export type Pt = [number, number]
/** A closed ring (the first vertex repeated at the end). */
export type Ring = Pt[]
/** Polygons, each its shell then its holes. */
export type Polys = Ring[][]

/** Arcs with exactly one side in the member rows (arcs.boundary_arcs), ascending. */
export function boundaryArcs(enc: Encoding, member: Uint8Array): number[] {
  const out: number[] = []
  for (let a = 0; a < enc.left.length; a++) {
    const l = enc.left[a]
    const r = enc.right[a]
    const lf = l >= 0 && member[l] === 1
    const rt = r >= 0 && member[r] === 1
    if (lf !== rt) out.push(a)
  }
  return out
}

/** A node's sort key: lexicographic in (x, y), as arcs._node_key orders them. */
function nodeKey(x: number, y: number): number {
  return (x + 2 ** 24) * 2 ** 25 + (y + 2 ** 24)
}

function sign(v: number): number {
  return v > 0 ? 1 : v < 0 ? -1 : 0
}

/** The cycles of a successor map (arcs._follow): arcs in ring order, arcs per ring, ring of each arc. */
function follow(succ: Int32Array): { seq: number[], ringArcs: number[], ringOf: Int32Array } {
  const n = succ.length
  const ringOf = new Int32Array(n).fill(-1)
  const seq: number[] = []
  const ringArcs: number[] = []
  for (let i = 0; i < n; i++) {
    if (ringOf[i] >= 0) continue
    const r = ringArcs.length
    let k = 0
    let j = i
    while (ringOf[j] < 0) {
      ringOf[j] = r
      seq.push(j)
      k += 1
      j = succ[j]
    }
    if (j !== i) throw new Error('boundary arcs do not chain into rings')
    ringArcs.push(k)
  }
  return { seq, ringArcs, ringOf }
}

/** Whether the closed ring holds the point in its interior (on the boundary is outside). */
function ringContains(ring: Ring, px: number, py: number): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((xj - xi) * (py - yi) - (yj - yi) * (px - xi) === 0
      && Math.min(xi, xj) <= px && px <= Math.max(xi, xj) && Math.min(yi, yj) <= py && py <= Math.max(yi, yj)) return false
    if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

/** Rings (open, as vertex lists) into shells with their holes (arcs.outline's tail). */
export function assemble(rings: Pt[][]): Polys {
  // drop the joints along a straight border (one pass, with the ring's wrap-around neighbours)
  const kept = rings.map((ring) => {
    const n = ring.length
    return ring.filter(([x, y], i) => {
      const [px, py] = ring[(i - 1 + n) % n]
      const [nx, ny] = ring[(i + 1) % n]
      return !((px === x && x === nx) || (py === y && y === ny))
    })
  })
  // twice each ring's signed area (shoelace about its first vertex)
  const area2 = kept.map((ring) => {
    if (!ring.length) return 0
    const [x0, y0] = ring[0]
    let s = 0
    for (let i = 0; i < ring.length; i++) {
      const [ax, ay] = ring[i]
      const [bx, by] = ring[(i + 1) % ring.length]
      s += (ax - x0) * (by - y0) - (bx - x0) * (ay - y0)
    }
    return Math.round(s)
  })
  const closed: Ring[] = kept.map((ring) => [...ring, ring[0]])
  const shells: number[] = []
  const holes: number[] = []
  area2.forEach((a, i) => {
    if (a > 0) shells.push(i)
    else if (a < 0) holes.push(i)
  })
  const home: number[] = holes.map((h) => {
    if (shells.length === 1) return 0
    const [x0, y0] = kept[h][0]
    const [x1, y1] = kept[h][1]
    const dx = sign(x1 - x0)
    const dy = sign(y1 - y0)
    const px = (x0 + x1) / 2 - 0.25 * dy
    const py = (y0 + y1) / 2 + 0.25 * dx
    let best = -1
    for (let s = 0; s < shells.length; s++) {
      if (!ringContains(closed[shells[s]], px, py)) continue
      if (best < 0 || area2[shells[s]] < area2[shells[best]]) best = s
    }
    if (best < 0) throw new Error('a hole lies in no shell')
    return best
  })
  const polys: Polys = shells.map((s) => [closed[s]])
  holes.forEach((h, k) => polys[home[k]].push(closed[h]))
  return polys
}

/** The polygons (cells) the member rows cover, from their boundary arcs (arcs.outline). */
export function outline(enc: Encoding, member: Uint8Array): Polys {
  const arcs = boundaryArcs(enc, member)
  const n = arcs.length
  if (!n) return []
  // each arc's vertices, turned so the member side is on its left
  const verts: Pt[][] = arcs.map((a) => {
    const v = arcVertices(enc, a)
    const l = enc.left[a]
    return l >= 0 && member[l] === 1 ? v : v.reverse()
  })
  const startKey = new Float64Array(n)
  const endKey = new Float64Array(n)
  const inDx = new Int8Array(n)
  const inDy = new Int8Array(n)
  const outDx = new Int8Array(n)
  const outDy = new Int8Array(n)
  for (let i = 0; i < n; i++) {
    const v = verts[i]
    const f = v[0]
    const l = v[v.length - 1]
    startKey[i] = nodeKey(f[0], f[1])
    endKey[i] = nodeKey(l[0], l[1])
    inDx[i] = sign(l[0] - v[v.length - 2][0])
    inDy[i] = sign(l[1] - v[v.length - 2][1])
    outDx[i] = sign(v[1][0] - f[0])
    outDy[i] = sign(v[1][1] - f[1])
  }
  // the successor of every arc: the arc leaving its end node, the left turn at a pinch
  const byStart = new Map<number, number[]>()
  for (let i = 0; i < n; i++) {
    const list = byStart.get(startKey[i])
    if (list) list.push(i)
    else byStart.set(startKey[i], [i])
  }
  const succ = new Int32Array(n)
  const pinch: number[] = []
  for (let i = 0; i < n; i++) {
    const out = byStart.get(endKey[i])
    if (!out || out.length > 2) throw new Error('boundary arcs do not chain into rings')
    if (out.length === 1) {
      succ[i] = out[0]
      continue
    }
    const [a, b] = out
    const cross = inDx[i] * outDy[a] - inDy[i] * outDx[a]
    succ[i] = cross > 0 ? a : b
    pinch.push(i)
  }
  // the two arcs that end at each pinch node, paired, nodes in key order
  const byNode = [...pinch].sort((p, q) => endKey[p] - endKey[q] || p - q)
  if (byNode.length % 2) throw new Error('boundary arcs do not chain into rings')
  const pairs: [number, number][] = []
  for (let k = 0; k < byNode.length; k += 2) {
    if (endKey[byNode[k]] !== endKey[byNode[k + 1]]) throw new Error('boundary arcs do not chain into rings')
    pairs.push([byNode[k], byNode[k + 1]])
  }
  // rings: follow the successors; split a ring that passes a pinch node twice, one node per ring a pass
  let rings: ReturnType<typeof follow> | null = null
  for (let pass = 0; pass < 256; pass++) {
    const r = follow(succ)
    const first = new Map<number, [number, number]>()
    for (const [a, b] of pairs) {
      const ring = r.ringOf[a]
      if (ring === r.ringOf[b] && !first.has(ring)) first.set(ring, [a, b])
    }
    if (!first.size) {
      rings = r
      break
    }
    for (const [a, b] of first.values()) {
      const t = succ[a]
      succ[a] = succ[b]
      succ[b] = t
    }
  }
  if (!rings) throw new Error('rings did not settle')
  // each ring's vertices: every arc without its end vertex, in ring order
  const out: Pt[][] = []
  let s = 0
  for (const count of rings.ringArcs) {
    const ring: Pt[] = []
    for (let k = 0; k < count; k++) {
      const v = verts[rings.seq[s + k]]
      for (let j = 0; j < v.length - 1; j++) ring.push(v[j])
    }
    out.push(ring)
    s += count
  }
  return assemble(out)
}

/** Whole cells covered by the member rows (arcs.area_cells). */
export function areaCells(enc: Encoding, member: Uint8Array): number {
  let cells = 0
  for (let r = 0; r < member.length; r++) if (member[r]) cells += enc.cells[r]
  return cells
}

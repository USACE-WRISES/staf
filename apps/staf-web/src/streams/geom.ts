// Planar line geometry as GEOS 3.13 (shapely 2.1) computes it, for the stream split.
//
// The Shiny tools split the drawn streams with shapely in EPSG:5070 metres (network_display.py); these
// are the same operations in the same order of floating-point steps: a line's length (Length::ofLine),
// a point at a distance along it (LengthIndexedLine::extractPoint), a point's distance to a segment
// (Distance::pointToSegment), shapely.ops.substring, and whether a line meets an axis-aligned box.

export type XY = [number, number]
export type Line = XY[]

function dist(ax: number, ay: number, bx: number, by: number): number {
  const dx = ax - bx
  const dy = ay - by
  return Math.sqrt(dx * dx + dy * dy)
}

/** GEOS Length::ofLine. */
export function lineLength(line: Line): number {
  let len = 0
  let [x0, y0] = line[0]
  for (let i = 1; i < line.length; i++) {
    const [x1, y1] = line[i]
    const dx = x1 - x0
    const dy = y1 - y0
    len += Math.sqrt(dx * dx + dy * dy)
    x0 = x1
    y0 = y1
  }
  return len
}

/** GEOS LengthIndexedLine::extractPoint (shapely line_interpolate_point) for a single line. */
export function interpolate(line: Line, length: number): XY {
  let forward = length
  if (length < 0) forward = lineLength(line) + length
  if (forward <= 0) return [line[0][0], line[0][1]]
  let total = 0
  for (let i = 0; i < line.length - 1; i++) {
    const p0 = line[i]
    const p1 = line[i + 1]
    const segLen = dist(p1[0], p1[1], p0[0], p0[1])
    if (total + segLen > forward) {
      const frac = (forward - total) / segLen
      if (frac <= 0) return [p0[0], p0[1]]
      if (frac >= 1) return [p1[0], p1[1]]
      return [(p1[0] - p0[0]) * frac + p0[0], (p1[1] - p0[1]) * frac + p0[1]]
    }
    total += segLen
  }
  const last = line[line.length - 1]
  return [last[0], last[1]]
}

/** GEOS Distance::pointToSegment. */
export function pointToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  if (ax === bx && ay === by) return dist(px, py, ax, ay)
  const len2 = (bx - ax) * (bx - ax) + (by - ay) * (by - ay)
  const r = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / len2
  if (r <= 0) return dist(px, py, ax, ay)
  if (r >= 1) return dist(px, py, bx, by)
  const s = ((ay - py) * (bx - ax) - (ax - px) * (by - ay)) / len2
  return Math.abs(s) * Math.sqrt(len2)
}

/** shapely.ops.substring for 0 <= start < end <= length (the split's pieces), not normalized. */
export function substring(line: Line, start: number, end: number): Line {
  const startPoint = interpolate(line, start)
  const endPoint = interpolate(line, end)
  const s = Math.max(start, 0)
  const out: Line = [startPoint]
  let current = 0
  for (let i = 0; i < line.length - 1; i++) {
    const p1 = line[i]
    const p2 = line[i + 1]
    if (s < current && current < end) out.push([p1[0], p1[1]])
    else if (current >= end) break
    // ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    const dx = p2[0] - p1[0]
    const dy = p2[1] - p1[1]
    current += Math.sqrt(dx * dx + dy * dy)
  }
  out.push(endPoint)
  return out
}

/** Whether a segment meets the closed box (Liang-Barsky clipping; endpoints exact). */
function segmentMeetsBox(ax: number, ay: number, bx: number, by: number,
  w: number, s: number, e: number, n: number): boolean {
  const inside = (x: number, y: number) => x >= w && x <= e && y >= s && y <= n
  if (inside(ax, ay) || inside(bx, by)) return true
  let t0 = 0
  let t1 = 1
  const dx = bx - ax
  const dy = by - ay
  const clip = (p: number, q: number): boolean => {
    if (p === 0) return q >= 0
    const r = q / p
    if (p < 0) {
      if (r > t1) return false
      if (r > t0) t0 = r
    } else {
      if (r < t0) return false
      if (r < t1) t1 = r
    }
    return true
  }
  return clip(-dx, ax - w) && clip(dx, e - ax) && clip(-dy, ay - s) && clip(dy, n - ay) && t0 <= t1
}

/** Whether any part of a (multi)line meets the closed box (shapely.intersects with shapely.box). */
export function linesMeetBox(parts: Line[], w: number, s: number, e: number, n: number): boolean {
  for (const part of parts) {
    if (part.length === 1) {
      const [x, y] = part[0]
      if (x >= w && x <= e && y >= s && y <= n) return true
    }
    for (let i = 0; i < part.length - 1; i++) {
      if (segmentMeetsBox(part[i][0], part[i][1], part[i + 1][0], part[i + 1][1], w, s, e, n)) return true
    }
  }
  return false
}

// One stream network on the map, split by the click rule (each Shiny tool's network_display.py).
//
// The HR geometry is drawn once and split by whether a stretch lies within the snap tolerance
// (150 ft) of an NHDPlus V2 reach, in planar EPSG:5070: those stretches are "v2" (the StreamCat lookup
// engine answers there), the rest "hr" (the STAF site engine). V2 stretches with no HR line within the
// tolerance draw too ("v2-orphan"), so nothing clickable disappears. Each line is sampled every 50 ft
// (plus its vertices); a run of samples that agree keeps the line's own coordinates, and a line whose
// samples differ is cut midway between them, the pieces projected back and rounded to 1e-6 degree.
// The same steps as the Python, in the same order (geom.ts mirrors GEOS), so the drawn pieces match.
import { albersForward, albersToLonLat } from '../xs/projExact'
import { pyFixed } from '../app/pyfmt'
import { type Line, lineLength, interpolate, pointToSegment, substring } from './geom'

export const FT_PER_M = 3.28083989501312
/** Sample spacing along a line: a third of the snap tolerance. */
export const SAMPLE_FT = 50.0
/** Above this many samples the spacing doubles (up to 8 times). */
export const MAX_SAMPLES = 150_000
const MIN_PIECE_M = 1.0
export const COVER_PROP = 'cover'

export interface Geometry { type: string, coordinates: unknown }
export interface Feature { type: 'Feature', properties: Record<string, unknown>, geometry: Geometry }
export interface FeatureCollection { type: 'FeatureCollection', features: Feature[] }
export type Mode = 'segmented' | 'hr-only' | 'empty' | 'hr-unavailable' | 'hr-truncated' | 'hr-partial'
export interface Display { mode: Mode, covered: FeatureCollection, uncovered: FeatureCollection }

const empty = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] })

interface Part { props: Record<string, unknown>, coords: Line }

/** Each line part of ``fc`` with its feature's properties (multipart lines exploded). */
function partsOf(fc: FeatureCollection | null): Part[] {
  const out: Part[] = []
  for (const feat of fc?.features ?? []) {
    const geom = feat.geometry
    const props = feat.properties ?? {}
    const coords = geom?.coordinates as number[][] | number[][][] | undefined
    if (!coords || !(coords as unknown[]).length) continue
    let parts: number[][][]
    if (geom.type === 'LineString') parts = [coords as number[][]]
    else if (geom.type === 'MultiLineString') parts = coords as number[][][]
    else continue
    for (const part of parts) {
      if (part.length < 2) continue
      out.push({ props, coords: part.map((p) => [Number(p[0]), Number(p[1])] as [number, number]) })
    }
  }
  return out
}

function toAlbers(line: Line): Line {
  return line.map(([lon, lat]) => albersForward(lon, lat))
}

/** numpy.linspace(0, stop, n) */
function linspace(stop: number, n: number): number[] {
  const step = stop / (n - 1)
  const out = new Array<number>(n)
  for (let i = 0; i < n; i++) out[i] = i * step + 0
  out[n - 1] = stop
  return out
}

/** Distances along a line to sample: a regular grid plus every vertex (sorted, unique). */
function sampleDistances(line: Line, spacing: number): number[] {
  const length = lineLength(line)
  const n = Math.max(3, Math.ceil(length / spacing) + 1)
  const all = linspace(length, n)
  let acc = 0
  all.push(0)
  for (let i = 1; i < line.length; i++) {
    const dx = line[i][0] - line[i - 1][0]
    const dy = line[i][1] - line[i - 1][1]
    acc += Math.sqrt(dx * dx + dy * dy)           // numpy.hypot (the C runtime's: this, here)
    all.push(acc)
  }
  all.sort((a, b) => a - b)
  return all.filter((d, i) => i === 0 || d !== all[i - 1])
}

/** The lines' sample distances, doubling the spacing while there are too many samples. */
function samples(lines: Line[], spacing: number): number[][] {
  for (;;) {
    const dists = lines.map((ln) => sampleDistances(ln, spacing))
    const total = dists.reduce((s, d) => s + d.length, 0)
    if (total <= MAX_SAMPLES || spacing >= 8 * SAMPLE_FT / FT_PER_M) return dists
    spacing *= 2.0
  }
}

/** Lines to test samples against: each one's envelope, in a grid of cells, for "within tol". */
class LineIndex {
  private cells = new Map<string, number[]>()
  private boxes: [number, number, number, number][] = []
  constructor(private lines: Line[], private tol: number, private cell = 500) {
    lines.forEach((line, i) => {
      let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity
      for (const [x, y] of line) {
        if (x < w) w = x
        if (x > e) e = x
        if (y < s) s = y
        if (y > n) n = y
      }
      this.boxes.push([w, s, e, n])
      const [cw, cs, ce, cn] = [w - tol, s - tol, e + tol, n + tol].map((v) => Math.floor(v / cell))
      for (let cx = cw; cx <= ce; cx++) {
        for (let cy = cs; cy <= cn; cy++) {
          const key = `${cx},${cy}`
          const list = this.cells.get(key)
          if (list) list.push(i)
          else this.cells.set(key, [i])
        }
      }
    })
  }

  /** Whether any line comes within the tolerance of the point (GEOS dwithin: distance <= tol). */
  near(x: number, y: number): boolean {
    const list = this.cells.get(`${Math.floor(x / this.cell)},${Math.floor(y / this.cell)}`)
    if (!list) return false
    const tol = this.tol
    for (const i of list) {
      const [w, s, e, n] = this.boxes[i]
      if (x < w - tol || x > e + tol || y < s - tol || y > n + tol) continue
      const line = this.lines[i]
      for (let k = 0; k < line.length - 1; k++) {
        if (pointToSegment(x, y, line[k][0], line[k][1], line[k + 1][0], line[k + 1][1]) <= tol) return true
      }
    }
    return false
  }
}

/** (start, end, state) runs along a line; cuts sit midway between adjacent samples that differ. */
function runs(dists: number[], mask: boolean[]): [number, number, boolean][] {
  const out: [number, number, boolean][] = []
  let start = 0.0
  let state = mask[0]
  for (let i = 1; i < dists.length; i++) {
    if (mask[i] !== state) {
      const cut = 0.5 * (dists[i - 1] + dists[i])
      out.push([start, cut, state])
      start = cut
      state = mask[i]
    }
  }
  out.push([start, dists[dists.length - 1], state])
  return out
}

/** round(x, 6) */
function round6(x: number): number {
  return Number(pyFixed(x, 6))
}

function feature(props: Record<string, unknown>, coords: Line, cover: string): Feature {
  return { type: 'Feature', properties: { ...props, [COVER_PROP]: cover }, geometry: { type: 'LineString', coordinates: coords } }
}

/** Classify ``parts`` against ``index``; returns (kept, other) features in input order. */
function split(parts: Part[], linesM: Line[], index: LineIndex | null, spacing: number, keepState: boolean,
  coverKept: string, coverOther: string | null): [Feature[], Feature[]] {
  const kept: Feature[] = []
  const other: Feature[] = []
  if (!parts.length) return [kept, other]
  const dists = samples(linesM, spacing)
  const slots: Feature[][] = parts.map(() => [])
  const pending: [number, number, number, string][] = []
  parts.forEach((part, i) => {
    const line = linesM[i]
    const mask = dists[i].map((d) => {
      if (!index) return false
      const [x, y] = interpolate(line, d)
      return index.near(x, y)
    })
    if (mask.every((m) => m) || !mask.some((m) => m)) {
      const state = mask.length ? mask[0] : false
      const cover = state === keepState ? coverKept : coverOther
      if (cover !== null) slots[i].push(feature(part.props, part.coords, cover))
      return
    }
    for (const [a, b, state] of runs(dists[i], mask)) {
      if (b - a < MIN_PIECE_M) continue
      const cover = state === keepState ? coverKept : coverOther
      if (cover !== null) pending.push([i, a, b, cover])
    }
  })
  for (const [i, a, b, cover] of pending) {
    const piece = substring(linesM[i], a, b).map(([x, y]) => albersToLonLat(x, y))
    slots[i].push(feature(parts[i].props, piece.map(([x, y]) => [round6(x), round6(y)]), cover))
  }
  for (const list of slots) {
    for (const feat of list) (feat.properties[COVER_PROP] === coverKept ? kept : other).push(feat)
  }
  return [kept, other]
}

/** (covered, uncovered): the HR geometry split by whether a stretch lies within ``tolFt`` of a V2
 * reach, plus the V2 stretches with no HR line within ``tolFt`` (drawn covered). */
export function splitByCoverage(hrFc: FeatureCollection | null, v2Fc: FeatureCollection | null, tolFt = 150.0,
  spacingFt = SAMPLE_FT): [FeatureCollection, FeatureCollection] {
  const hr = partsOf(hrFc)
  const v2 = partsOf(v2Fc)
  const covered = empty()
  const uncovered = empty()
  if (!hr.length && !v2.length) return [covered, uncovered]
  const tol = tolFt / FT_PER_M
  const spacing = spacingFt / FT_PER_M
  const hrM = hr.map((p) => toAlbers(p.coords))
  const v2M = v2.map((p) => toAlbers(p.coords))
  const hrIndex = hrM.length ? new LineIndex(hrM, tol) : null
  const v2Index = v2M.length ? new LineIndex(v2M, tol) : null
  const [hrCov, hrUnc] = split(hr, hrM, v2Index, spacing, true, 'v2', 'hr')
  const [orphans] = split(v2, v2M, hrIndex, spacing, false, 'v2-orphan', null)
  covered.features = [...hrCov, ...orphans]
  uncovered.features = hrUnc
  return [covered, uncovered]
}

function tagged(fc: FeatureCollection, cover: string): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: fc.features.map((f) => ({ type: 'Feature', properties: { ...(f.properties ?? {}), [COVER_PROP]: cover }, geometry: f.geometry })),
  }
}

/** The two map layers and the legend's mode for a view (network_display.build_display, from the bundle:
 * every HR line of the box answered, so there is no partial or unanswered view here). */
export function buildDisplay(v2Fc: FeatureCollection | null, hrFc: FeatureCollection | null, tolFt = 150.0): Display {
  const hasV2 = !!v2Fc?.features?.length
  const hasHr = !!hrFc?.features?.length
  if (hasV2 && hasHr) {
    const [covered, uncovered] = splitByCoverage(hrFc, v2Fc, tolFt)
    return { mode: 'segmented', covered, uncovered }
  }
  if (hasV2) return { mode: 'segmented', covered: tagged(v2Fc as FeatureCollection, 'v2-orphan'), uncovered: empty() }
  if (hasHr) return { mode: 'hr-only', covered: empty(), uncovered: tagged(hrFc as FeatureCollection, 'hr') }
  return { mode: 'empty', covered: empty(), uncovered: empty() }
}

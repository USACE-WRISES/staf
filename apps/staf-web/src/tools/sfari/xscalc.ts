// Open-channel cross-section hydraulics (sfari/xscalc.py, a port of xs-calc's core): Manning's normal
// depth on an irregular station/elevation section with left and right overbank zones, each with its own
// n (conveyance summed across zones, as HEC-RAS and xs-calc do). At a water-surface stage it integrates
// the wetted area, perimeter and top width and gives the discharge Q, the mean velocity V, the
// hydraulic radius R, the bed shear tau = gamma R S, the unit stream power tau V and the Froude number;
// solveStage finds the stage that conveys a target Q. Sums are Python's (pySum), so the numbers are its.
import { pyHypot, pySum } from '../../app/pyfmt'

export const UNITS: Record<string, { k: number, gamma: number, g: number }> = {
  US: { k: 1.49, gamma: 62.4, g: 32.174 },
  SI: { k: 1.0, gamma: 9810.0, g: 9.81 },
}
export const FT_PER_M = 3.28083989501312

export type Point = [number, number]

/** synthetic_section: a trapezoidal channel and floodplain benches from bankfull width and depth
 * (metres), in US feet: ``[points, lb, rb, bankfullStage]``. */
export function syntheticSection(widthM: number, depthM: number, sideSlope = 2.0, floodplainFactor = 1.0): [Point[], number, number, number] {
  const w = Math.max(Number(widthM), 0.3) * FT_PER_M
  const d = Math.max(Number(depthM), 0.1) * FT_PER_M
  const b = Math.max(w - 2.0 * sideSlope * d, 0.3 * w)
  const fp = w * floodplainFactor
  const pts: Point[] = [[-(w / 2 + fp), d * 1.15], [-w / 2, d], [-b / 2, 0.0], [b / 2, 0.0], [w / 2, d], [w / 2 + fp, d * 1.15]]
  return [pts, -w / 2, w / 2, d]
}

function interpZ(xa: number, za: number, xb: number, zb: number, x: number): number {
  if (xb === xa) return za
  return za + (zb - za) * (x - xa) / (xb - xa)
}

/** _wetted: the wetted area, perimeter, top width and greatest depth below ``stage`` within [xl, xr]. */
function wetted(points: Point[], stage: number, xLeft: number | null, xRight: number | null): [number, number, number, number] {
  const xl = xLeft === null ? -Infinity : xLeft
  const xr = xRight === null ? Infinity : xRight
  let area = 0.0
  let perim = 0.0
  let top = 0.0
  let dmax = 0.0
  for (let i = 0; i + 1 < points.length; i++) {
    const [xa, za] = points[i]
    const [xb, zb] = points[i + 1]
    if (xb <= xa) continue
    const cxa = Math.max(xa, xl)
    const cxb = Math.min(xb, xr)
    if (cxb <= cxa) continue
    const cza = interpZ(xa, za, xb, zb, cxa)
    const czb = interpZ(xa, za, xb, zb, cxb)
    const da = stage - cza
    const db = stage - czb
    if (da <= 0 && db <= 0) continue
    if (da > 0 && db > 0) {
      area += (da + db) / 2.0 * (cxb - cxa)
      perim += pyHypot(cxb - cxa, czb - cza)
      top += (cxb - cxa)
      dmax = Math.max(dmax, da, db)
    } else {
      const xc = cxa + da / (da - db) * (cxb - cxa)
      if (da > 0) {
        area += da / 2.0 * (xc - cxa)
        perim += pyHypot(xc - cxa, da)
        top += (xc - cxa)
        dmax = Math.max(dmax, da)
      } else {
        area += db / 2.0 * (cxb - xc)
        perim += pyHypot(cxb - xc, db)
        top += (cxb - xc)
        dmax = Math.max(dmax, db)
      }
    }
  }
  return [area, perim, top, dmax]
}

export interface Zone { A: number, P: number, T: number, dmax: number, R: number, K: number, Q?: number, V?: number }

function zone(points: Point[], stage: number, n: number, xl: number | null, xr: number | null, k: number): Zone {
  const [a, p, t, dmax] = wetted(points, stage, xl, xr)
  if (a <= 0 || p <= 0 || !n) return { A: a, P: p, T: t, dmax, R: 0.0, K: 0.0 }
  const r = a / p
  return { A: a, P: p, T: t, dmax, R: r, K: (k / n) * a * Math.pow(r, 2.0 / 3.0) }
}

export interface Hydraulics {
  stage: number, Q: number, A: number, P: number, T: number, R: number, V: number, tau: number, power: number, froude: number,
  depth_max: number, depth_avg: number, zones: Record<string, Zone>, units: string
}

const sortedPoints = (points: Point[]): Point[] =>
  points.map(([x, z]) => [Number(x), Number(z)] as Point).sort((a, b) => a[0] - b[0] || a[1] - b[1])

export interface Options { nLob?: number | null, nRob?: number | null, lb?: number | null, rb?: number | null, units?: string }

/** compute: the hydraulics at ``stage``, totals and each zone; null for fewer than two points. */
export function compute(points: Point[], stage: number, slope: number, nChan: number, opts: Options = {}): Hydraulics | null {
  const units = opts.units ?? 'US'
  const u = UNITS[units] ?? UNITS.US
  const { k, gamma, g } = u
  const s = Math.max(Number(slope), 0.0)
  const pts = sortedPoints(points)
  if (pts.length < 2) return null
  const lb = opts.lb ?? null
  const rb = opts.rb ?? null
  const zones: Record<string, Zone> = lb !== null && rb !== null && rb > lb
    ? { LOB: zone(pts, stage, opts.nLob || nChan, null, lb, k), channel: zone(pts, stage, nChan, lb, rb, k), ROB: zone(pts, stage, opts.nRob || nChan, rb, null, k) }
    : { channel: zone(pts, stage, nChan, null, null, k) }
  const zs = Object.values(zones)
  const A = pySum(zs.map((z) => z.A))
  const P = pySum(zs.map((z) => z.P))
  const T = pySum(zs.map((z) => z.T))
  const K = pySum(zs.map((z) => z.K))
  const dmax = zs.length ? Math.max(...zs.map((z) => z.dmax)) : 0.0
  if (A <= 0 || P <= 0) {
    return { stage, Q: 0.0, A: 0.0, P: 0.0, T: 0.0, R: 0.0, V: 0.0, tau: 0.0, power: 0.0, froude: 0.0, depth_max: 0.0, depth_avg: 0.0, zones, units }
  }
  const sq = Math.sqrt(s)
  const Q = K * sq
  const R = A / P
  const V = A ? Q / A : 0.0
  const dHyd = T ? A / T : 0.0
  const tau = gamma * R * s
  const power = tau * V
  const froude = dHyd > 0 ? V / Math.sqrt(g * dHyd) : 0.0
  for (const z of zs) {
    z.Q = z.K * sq
    z.V = z.A ? z.Q / z.A : 0.0
  }
  return { stage, Q, A, P, T, R, V, tau, power, froude, depth_max: dmax, depth_avg: dHyd, zones, units }
}

/** solve_stage: the stage (elevation) that conveys ``targetQ``, by bisection; null when none can. */
export function solveStage(points: Point[], targetQ: number, slope: number, nChan: number, opts: Options = {}, tol = 1e-3, iters = 60): number | null {
  const pts = sortedPoints(points)
  if (targetQ <= 0 || pts.length < 2 || slope <= 0) return null
  let lo = Math.min(...pts.map((p) => p[1]))
  let hi = Math.max(...pts.map((p) => p[1]))
  const top = compute(pts, hi, slope, nChan, opts)
  if (!top || top.Q < targetQ) {
    const span = hi - lo || 1.0
    hi = hi + span * 3.0
  }
  for (let i = 0; i < iters; i++) {
    const mid = (lo + hi) / 2.0
    const res = compute(pts, mid, slope, nChan, opts)
    const q = res ? res.Q : 0.0
    if (Math.abs(q - targetQ) <= tol * Math.max(targetQ, 1.0)) return mid
    if (q < targetQ) lo = mid
    else hi = mid
  }
  return (lo + hi) / 2.0
}

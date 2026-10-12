// Cross-section geomorphology (geomorph.py, the same file in EASI and the STAF site engine's
// _extracted copy): widths and areas at a stage, the bankfull stage for a regional area, the default
// low bank (slope break, else crest scan), Rosgen's entrenchment and bank-height ratios, the display
// window and profile thinning, the reach statistics and the K2b quality record. Pure; Python's
// rounding (pyFixed) and its first-minimum rules are kept.
import { pyFixed, pyG } from '../app/pyfmt'
import { pairwiseSum } from './values'

const pyRound = (x: number, n: number) => Number(pyFixed(x, n))

export const FT_PER_M = 3.28083989501312
export const XS_COUNT = 9
export const BHR_CAP = 2.0
export const ER_MIN = 1.0
export const MIN_QUALITY_SECTIONS = 3
export const QUALITY_RULES = 'K2b'
const BANK_ARM_RISE_M = 0.3
const BANK_ARM_RISE_FINE_M = 0.10
const BANK_ARM_RISE_FRAC = 0.25
const BANK_FLAT_GRADE = 0.05
const BANK_FLAT_REL = 0.25
const BANK_FLAT_LEN_M = 3.0
export const VIEW_HEADROOM = 1.5
export const VIEW_MIN_HALF_M = 15.0
const BF_WIDTH = [2.70, 0.352]
const BF_DEPTH = [0.30, 0.315]

/** min(range(n), key=lambda i: elevs[i]): the first index of the lowest elevation. */
export function argmin(elevs: ArrayLike<number>): number {
  let best = 0
  for (let i = 1; i < elevs.length; i++) if (elevs[i] < elevs[best]) best = i
  return best
}

/** bankfull_geometry: the national interim curve. */
export function nationalBankfull(daSqkm: number): [number, number] {
  const da = Math.max(Number(daSqkm || 0.0), 0.01)
  return [BF_WIDTH[0] * Math.pow(da, BF_WIDTH[1]), BF_DEPTH[0] * Math.pow(da, BF_DEPTH[1])]
}

/** wetted_span: the edge-of-water stations of the water body spanning the thalweg. */
export function wettedSpan(st: number[], el: number[], stage: number, thalwegIndex: number | null = null): [number, number, boolean] | null {
  const n = st.length
  if (n < 2) return null
  const ti = thalwegIndex ?? argmin(el)
  if (el[ti] >= stage) return null
  const edge = (dir: number): [number, boolean] => {
    let i = ti
    while (i + dir >= 0 && i + dir < n) {
      const j = i + dir
      if (el[j] >= stage) {
        const t = (stage - el[i]) / (el[j] - el[i])
        return [st[i] + t * (st[j] - st[i]), false]
      }
      i = j
    }
    return [st[i], true]
  }
  const [left, le] = edge(-1)
  const [right, re] = edge(+1)
  return [left, right, le || re]
}

/** flow_width: the contiguous top width spanning the thalweg at a stage. */
export function flowWidth(st: number[], el: number[], stage: number, thalwegIndex: number | null = null): [number, boolean] {
  const span = wettedSpan(st, el, stage, thalwegIndex)
  if (span === null) return [0.0, false]
  const [left, right, edge] = span
  return [Math.max(right - left, 0.0), edge]
}

/** display_window: the station window around the thalweg the plot shows. */
export function displayWindow(st: number[], el: number[], ceilingStage: number, thalwegIndex: number | null = null, minHalf = 15.0): [number, number] {
  const n = st.length
  if (n === 0) return [0.0, 0.0]
  if (n < 2) return [st[0], st[n - 1]]
  const ti = thalwegIndex ?? argmin(el)
  const span = wettedSpan(st, el, ceilingStage, ti)
  if (span === null) return [st[0], st[n - 1]]
  const [left, right] = span
  const lo = Math.min(left, st[ti] - minHalf)
  const hi = Math.max(right, st[ti] + minHalf)
  return [Math.max(lo, st[0]), Math.min(hi, st[n - 1])]
}

/** flow_area: the contiguous channel area below a stage (trapezoids), and whether a bank was missed. */
export function flowArea(st: number[], el: number[], stage: number, thalwegIndex: number | null = null): [number, boolean] {
  const n = st.length
  if (n < 2) return [0.0, false]
  const ti = thalwegIndex ?? argmin(el)
  if (el[ti] >= stage) return [0.0, false]
  const edge = (dir: number): [number, number, boolean] => {
    let i = ti
    while (i + dir >= 0 && i + dir < n) {
      const j = i + dir
      if (el[j] >= stage) {
        const t = (stage - el[i]) / (el[j] - el[i])
        return [i, st[i] + t * (st[j] - st[i]), false]
      }
      i = j
    }
    return [i, st[i], true]
  }
  const [li, left, le] = edge(-1)
  const [ri, right, re] = edge(+1)
  const xs = [left]
  const depths = [0.0]
  for (let k = li; k <= ri; k++) {
    xs.push(st[k])
    depths.push(Math.max(stage - el[k], 0.0))
  }
  xs.push(right)
  depths.push(0.0)
  let area = 0.0
  for (let k = 0; k < xs.length - 1; k++) {
    const dx = xs[k + 1] - xs[k]
    if (dx > 0) area += 0.5 * (depths[k] + depths[k + 1]) * dx
  }
  return [Math.max(area, 0.0), le || re]
}

/** stage_for_area: the stage whose contiguous channel area is the target (bisection). */
export function stageForArea(st: number[], el: number[], target: number | null, thalwegIndex: number | null = null, iters = 48): [number, boolean] {
  const n = el.length
  if (n < 2) return [n ? el[0] : 0.0, false]
  const ti = thalwegIndex ?? argmin(el)
  let lo = el[ti]
  if (target === null || target <= 0) return [lo, false]
  let hi = Math.max(...el)
  if (hi <= lo) return [lo, false]
  const [aHi] = flowArea(st, el, hi, ti)
  if (aHi < target) return [hi, true]
  for (let k = 0; k < iters; k++) {
    const mid = 0.5 * (lo + hi)
    const [a] = flowArea(st, el, mid, ti)
    if (a < target) lo = mid
    else hi = mid
  }
  return [0.5 * (lo + hi), false]
}

/** balanced_profile: recentred on the thalweg and trimmed to a height-balanced band. */
export function balancedProfile(st: number[], el: number[], minHalf = 30.0): [number[], number[]] | null {
  if (st.length !== el.length || st.length < 7) return null
  const ti = argmin(el)
  const t0 = st[ti]
  const rel = st.map((s) => s - t0)
  const lim = Math.min(-Math.min(...rel), Math.max(...rel))
  if (lim < minHalf) return null
  const crestDist = (side: [number, number][]): number => {
    if (!side.length) return 0.0
    let top = -Infinity
    for (const [, e] of side) if (e > top) top = e
    let best = Infinity
    for (const [d, e] of side) if (e >= top - 0.3 && d < best) best = d
    return best
  }
  const left: [number, number][] = []
  const right: [number, number][] = []
  for (let i = 0; i < rel.length; i++) {
    const r = rel[i]
    if (-lim <= r && r < 0) left.push([-r, el[i]])
    if (0 < r && r <= lim) right.push([r, el[i]])
  }
  const reach = Math.min(Math.max(crestDist(left), crestDist(right), minHalf), lim)
  const outS: number[] = []
  const outE: number[] = []
  for (let i = 0; i < rel.length; i++) {
    if (-reach - 1e-6 <= rel[i] && rel[i] <= reach + 1e-6) {
      outS.push(rel[i])
      outE.push(el[i])
    }
  }
  return outS.length < 7 ? null : [outS, outE]
}

/** simplify_profile: xs-calc's near, collinear and Visvalingam filters (the ends and the thalweg kept). */
export function simplifyProfile(st: number[], el: number[], tolX = 0.15, tolZ = 0.03, colTol = 0.03, minSlope = 0.001, maxPoints = 250): [number[], number[]] {
  const n = st.length
  if (n !== el.length || n <= 3) return [[...st], [...el]]
  const thal = argmin(el)
  type P = { x: number, z: number, g: boolean }
  let pts: P[] = st.map((s, i) => ({ x: s, z: el[i], g: i === 0 || i === n - 1 || i === thal }))
  const slope = (a: P, b: P) => {
    const dx = b.x - a.x
    return dx === 0 ? Infinity : (b.z - a.z) / dx
  }
  const perp = (p: P, a: P, b: P) => {
    const dx = b.x - a.x
    const dz = b.z - a.z
    const den = Math.sqrt(dx * dx + dz * dz)
    if (den === 0) return Math.sqrt((p.x - a.x) ** 2 + (p.z - a.z) ** 2)
    return Math.abs(dz * p.x - dx * p.z + b.x * a.z - b.z * a.x) / den
  }
  const tri = (a: P, b: P, c: P) => Math.abs(a.x * (b.z - c.z) + b.x * (c.z - a.z) + c.x * (a.z - b.z)) / 2.0
  const kept: P[] = [pts[0]]
  for (const b of pts.slice(1)) {
    const a = kept[kept.length - 1]
    if (!b.g && Math.abs(b.x - a.x) <= tolX && Math.abs(b.z - a.z) <= tolZ) continue
    kept.push(b)
  }
  pts = kept
  let changed = true
  while (changed) {
    changed = false
    let i = 1
    while (i < pts.length - 1) {
      const a = pts[i - 1]
      const b = pts[i]
      const c = pts[i + 1]
      if (!b.g && perp(b, a, c) <= colTol) {
        const ma = slope(a, b)
        const mc = slope(a, c)
        if ((ma === Infinity && mc === Infinity) || Math.abs(ma - mc) <= minSlope) {
          pts.splice(i, 1)
          changed = true
          continue
        }
      }
      i += 1
    }
  }
  while (pts.length > maxPoints) {
    let bestI = -1
    let bestArea = Infinity
    for (let i = 1; i < pts.length - 1; i++) {
      if (pts[i].g) continue
      const area = tri(pts[i - 1], pts[i], pts[i + 1])
      if (area < bestArea) {
        bestArea = area
        bestI = i
      }
    }
    if (bestI < 0) break
    pts.splice(bestI, 1)
  }
  return [pts.map((p) => p.x), pts.map((p) => p.z)]
}

/** top_of_bank_elev: the lower of the two bank crests, scanning out from the thalweg. */
export function topOfBankElev(st: number[], el: number[]): number | null {
  if (st.length < 5) return null
  const ti = el.indexOf(Math.min(...el))
  const crest = (dir: number) => {
    let best = el[ti]
    let i = ti
    while (i + dir >= 0 && i + dir < el.length) {
      i += dir
      if (el[i] > best) best = el[i]
      else if (best - el[i] > 0.5) break
    }
    return best
  }
  return Math.min(crest(-1), crest(+1))
}

/** bank_rise_floor: the climb a bank must make before a flat counts (lidar or the 10 m convention). */
export function bankRiseFloor(demResM: number | null | undefined): number {
  const fine = demResM !== null && demResM !== undefined && Number(demResM) <= 1.5
  return fine ? BANK_ARM_RISE_FINE_M : BANK_ARM_RISE_M
}

/** bank_break_elev: the lower first-bank elevation, the first definitive slope break onto a flat. */
export function bankBreakElev(st: number[], el: number[], dBf: number | null, thalwegIndex: number | null = null, minRiseM = BANK_ARM_RISE_M): number | null {
  const n = st.length
  if (n < 5 || dBf === null || dBf <= 0) return null
  const ti = thalwegIndex ?? argmin(el)
  const arm = el[ti] + Math.max(minRiseM, BANK_ARM_RISE_FRAC * dBf)
  const zAhead = (i: number, dir: number): number | null => {
    const target = st[i] + dir * BANK_FLAT_LEN_M
    let j = i
    while (j + dir >= 0 && j + dir < n) {
      const k = j + dir
      if ((st[k] - target) * dir >= 0) {
        const x0 = st[j]
        const x1 = st[k]
        if (x1 === x0) return el[k]
        const t = (target - x0) / (x1 - x0)
        return el[j] + t * (el[k] - el[j])
      }
      j = k
    }
    return null
  }
  const breakElev = (dir: number): number | null => {
    let maxClimb = 0.0
    let i = ti
    while (i + dir >= 0 && i + dir < n) {
      const j = i + dir
      const run = Math.abs(st[j] - st[i])
      const rise = el[j] - el[i]
      if (run > 0 && rise > 0) maxClimb = Math.max(maxClimb, rise / run)
      i = j
      if (el[i] < arm || maxClimb <= 0) continue
      const zEnd = zAhead(i, dir)
      if (zEnd === null) return null
      const avg = Math.abs(zEnd - el[i]) / BANK_FLAT_LEN_M
      if (avg < BANK_FLAT_GRADE && avg < BANK_FLAT_REL * maxClimb) return el[i]
    }
    return null
  }
  const found = [breakElev(-1), breakElev(+1)].filter((b): b is number => b !== null)
  return found.length ? Math.min(...found) : null
}

/** fmt_bhr: ``≥2.00`` (``at least 2.00`` in words) at the cap, else two decimals, ``n/a`` for None. */
export function fmtBhr(value: number | null | undefined, capped = false, words = false): string {
  if (value === null || value === undefined) return 'n/a'
  const v = Number(value)
  if (capped && v >= BHR_CAP - 1e-9) return words ? `at least ${pyFixed(v, 2)}` : `≥${pyFixed(v, 2)}`
  return pyFixed(v, 2)
}

export type Section = Record<string, any>

/** _sections: the per-section records of a geometry (stored ``candidate_scalars`` or live ``candidates``). */
export function sections(geom: Record<string, any> | null | undefined): Section[] {
  if (!geom || typeof geom !== 'object') return []
  for (const key of ['candidate_scalars', 'candidates']) {
    const found = geom[key]
    if (Array.isArray(found) && found.length) return found.filter((c) => c && typeof c === 'object')
  }
  return []
}

/** bankfull_width_cv: numpy's population std of the sections' bankfull widths over their mean. */
export function bankfullWidthCv(geom: Record<string, any> | null | undefined): number | null {
  const widths: number[] = []
  for (const s of sections(geom)) {
    const v = s.bankfull_width_m
    if (v === null || v === undefined || typeof v === 'boolean') continue
    const f = Number(v)
    if (Number.isNaN(f) && typeof v !== 'number') return null
    widths.push(f)
  }
  if (widths.length < 2) return null
  if (!widths.every(Number.isFinite)) return null
  const mean = pairwiseSum(widths) / widths.length
  if (mean <= 0) return null
  const sq = widths.map((w) => {
    const d = w - mean
    return d * d
  })
  return Math.sqrt(pairwiseSum(sq) / widths.length) / mean
}

/** cap_unreachable_depth: the bankfull depth at or below which the default low bank cannot sit
 * below the floodprone cap on a DEM of this resolution. */
export const capUnreachableDepth = (demResM: number | null | undefined) => bankRiseFloor(demResM) / BHR_CAP

const sectionDepth = (s: Section): number | null => {
  const v = s.bankfull_depth_m
  if (v === null || v === undefined || typeof v === 'boolean') return null
  const f = Number(v)
  return Number.isNaN(f) ? null : f
}

/** cross_section_quality: the K2b quality record of a reach's cross-section evidence, or null. */
export function crossSectionQuality(geom: Record<string, any> | null | undefined): Record<string, any> | null {
  if (!geom || typeof geom !== 'object' || !Object.keys(geom).length) return null
  const reach = geom.reach && typeof geom.reach === 'object' ? geom.reach : {}
  const secs = sections(geom)
  const ratio = (v: any) => (v === null || v === undefined || typeof v === 'boolean' ? null : Number(v))
  let bhr = ratio(geom.bank_height_ratio)
  let er = ratio(geom.entrenchment_ratio)
  if ((bhr !== null && Number.isNaN(bhr)) || (er !== null && Number.isNaN(er))) {
    bhr = null
    er = null
  }
  if (bhr === null && er === null && !secs.length && !Object.keys(reach).length) return null
  const nSections = Math.trunc(Number(reach.n || geom.n_transects || secs.length || (bhr !== null || er !== null ? 1 : 0)))
  const ratioN = (key: string, value: number | null) => {
    const stats = reach[key] && typeof reach[key] === 'object' ? reach[key] : null
    if (stats !== null) return Math.trunc(Number(stats.n || 0))
    if (secs.length) return secs.filter((s) => s[key] !== null && s[key] !== undefined).length
    return value !== null ? 1 : 0
  }
  const bhrN = ratioN('bank_height_ratio', bhr)
  const erN = ratioN('entrenchment_ratio', er)
  const bhrStats = reach.bank_height_ratio && typeof reach.bank_height_ratio === 'object' ? reach.bank_height_ratio : {}
  const capped = Math.trunc(Number(bhrStats.capped || secs.filter((s) => s.low_bank_capped).length))
  const medianCapped = Boolean(bhrStats.median_capped || (capped && bhr !== null && bhr >= BHR_CAP - 1e-9))
  const demRes = geom.dem_resolution_m ?? null
  const floorDepth = capUnreachableDepth(demRes)
  const unreachable = secs.filter((s) => s.low_bank_capped && s.bank_height_ratio !== null && s.bank_height_ratio !== undefined
    && sectionDepth(s) !== null && (sectionDepth(s) as number) <= floorDepth + 1e-9).length
  const needed = bhrN ? Math.floor(bhrN / 2) + 1 : 0
  const capIsFloor = Boolean(medianCapped && secs.length && bhrN && (capped - unreachable) < needed)
  let found = new Set(secs.filter((s) => s.bank_detection).map((s) => String(s.bank_detection)))
  if (!found.size && geom.bank_detection) found = new Set([String(geom.bank_detection)])
  const detection = !found.size ? 'unknown' : found.size === 1 ? [...found][0] : 'mixed'
  const profile = geom.profile && typeof geom.profile === 'object' ? geom.profile : null
  const points = profile && Object.keys(profile).length ? (profile.stations || []).length : null
  const flags: string[] = []
  const reasons: Record<string, string> = {}
  const matched: Record<string, string[]> = {}
  const weak: string[] = []
  const weakTokens: string[] = []
  if (bhr !== null && !(bhr > 0.0)) {
    flags.push('out_of_range_bhr')
    matched.out_of_range_bhr = ['bhr_at_or_below_0']
    reasons.out_of_range_bhr = `bank-height ratio ${pyFixed(bhr, 2)} is at or below zero (the low bank at or below the thalweg)`
  }
  if (er !== null && er < ER_MIN) {
    flags.push('out_of_range_er')
    matched.out_of_range_er = ['er_below_1']
    reasons.out_of_range_er = `entrenchment ratio ${pyFixed(er, 2)} is below ${pyG(ER_MIN)} (flood-prone width narrower than bankfull width)`
  }
  for (const [key, token, count] of [['bank-height ratio', 'bhr', bhr !== null ? bhrN : null], ['entrenchment ratio', 'er', er !== null ? erN : null]] as const) {
    if (count !== null && count < MIN_QUALITY_SECTIONS) {
      weak.push(`only ${count} of ${nSections} section(s) carry a ${key}`)
      weakTokens.push(`few_sections:${token}`)
    }
  }
  if (geom.bankfull_extrapolated) {
    weak.push('bankfull geometry is extrapolated outside the Bieger fit range')
    weakTokens.push('bankfull_extrapolated')
  }
  if (detection === 'crest_scan') {
    weak.push('every section found its low bank by the crest scan, not a slope break')
    weakTokens.push('crest_scan')
  }
  if (capIsFloor) {
    const resWords = typeof demRes === 'number' ? `${pyG(demRes)} m` : 'this'
    weak.push(`the reach median bank-height ratio sits at the ${pyG(BHR_CAP)} cap only with sections whose bankfull depth is at or below `
      + `${pyFixed(floorDepth, 2)} m, where the ${resWords} DEM's bank detector cannot place a bank below the cap (${unreachable} of ${capped} capped sections)`)
    weakTokens.push('cap_unreachable')
  }
  if (weak.length) {
    flags.push('low_quality')
    matched.low_quality = weakTokens
    reasons.low_quality = weak.join('; ')
  }
  return {
    quality: QUALITY_RULES,
    sections: nSections, bhrSections: bhrN, erSections: erN,
    cappedSections: capped, medianCapped,
    capUnreachableSections: unreachable, capUnreachableDepthM: pyRound(floorDepth, 3),
    bhrAtCap: medianCapped, capIsFloor,
    detection, profilePoints: points,
    edgeLimited: Boolean(geom.edge_limited),
    bankfullExtrapolated: Boolean(geom.bankfull_extrapolated),
    demResolutionM: demRes,
    bhr, er,
    bhrInRange: bhr === null ? null : bhr > 0.0,
    erInRange: er === null ? null : er >= ER_MIN,
    flags, matched, reasons,
  }
}

/** describe_reach: ``reach median of 9 sections (1.26 to 2.24)``, or '' without reach statistics. */
export function describeReach(stats: Record<string, any> | null | undefined, key: string): string {
  const s = (stats ?? {})[key] || {}
  const n = Math.trunc(Number(s.n || 0))
  if (s.median === null || s.median === undefined || n < 2) return ''
  const hi = key === 'bank_height_ratio' && s.max_capped ? fmtBhr(s.max, true, true) : pyFixed(s.max, 2)
  return `reach median of ${n} sections (${pyFixed(s.min, 2)} to ${hi})`
}

/** derive_from_stages: Rosgen ER and BHR and the measured bankfull width at chosen stages. */
export function deriveFromStages(st: number[], el: number[], thalweg: number | null, bankfullStage: number, floodplainStage: number): Record<string, any> {
  const th = thalweg ?? Math.min(...el)
  const ti = el.length ? argmin(el) : 0
  const d = bankfullStage - th
  const out: Record<string, any> = {
    thalweg: th, bankfull_stage: bankfullStage, floodplain_stage: floodplainStage,
    bankfull_depth_max_m: d > 0 ? pyRound(d, 3) : null,
    entrenchment_ratio: null, bank_height_ratio: null, bankfull_width_m: null, flood_prone_width_m: null,
    fp_stage_m: null, edge_limited: false,
  }
  if (d <= 0) return out
  const [wBf] = flowWidth(st, el, bankfullStage, ti)
  const fpStage = th + 2.0 * d
  const [wFp, fpEdge] = flowWidth(st, el, fpStage, ti)
  out.bankfull_width_m = wBf > 0 ? pyRound(wBf, 1) : null
  out.flood_prone_width_m = wFp > 0 ? pyRound(wFp, 1) : null
  out.fp_stage_m = pyRound(fpStage, 3)
  out.edge_limited = Boolean(fpEdge)
  if (wBf > 0) out.entrenchment_ratio = pyRound(wFp / wBf, 2)
  out.bank_height_ratio = pyRound((floodplainStage - th) / d, 2)
  return out
}

/** summarize_profile: one profile's Rosgen summary at its default stages. */
export function summarizeProfile(st: number[], el: number[], daSqkm: number, o: { bankfull?: [number, number] | null, bankfullAreaM2?: number | null,
  division?: string | null, demResM?: number | null } = {}): Record<string, any> {
  let [wBf, dBf] = o.bankfull ?? nationalBankfull(daSqkm)
  const thalweg = Math.min(...el)
  const ti = el.length ? argmin(el) : 0
  let areaEdge = false
  let bankfullStage: number
  if (o.bankfullAreaM2 !== null && o.bankfullAreaM2 !== undefined && o.bankfullAreaM2 > 0) {
    ;[bankfullStage, areaEdge] = stageForArea(st, el, o.bankfullAreaM2, ti)
    const dArea = bankfullStage - thalweg
    if (dArea > 0.01) dBf = dArea
    else bankfullStage = thalweg + dBf
  } else {
    bankfullStage = thalweg + dBf
  }
  const tob = topOfBankElev(st, el)
  const brk = bankBreakElev(st, el, dBf, ti, bankRiseFloor(o.demResM))
  const lowBankStage = brk !== null
    ? Math.min(brk, thalweg + 2.0 * dBf)
    : Math.min(Math.max(tob !== null ? tob : bankfullStage, bankfullStage), thalweg + 2.0 * dBf)
  const d = deriveFromStages(st, el, thalweg, bankfullStage, lowBankStage)
  const out: Record<string, any> = {
    profile: { stations: [...st], elevs: [...el] },
    thalweg, fp_stage_m: thalweg + 2.0 * dBf, bankfull_stage_m: bankfullStage, low_bank_stage_m: lowBankStage,
    bank_detection: brk !== null ? 'slope_break' : 'crest_scan',
    low_bank_capped: lowBankStage >= thalweg + BHR_CAP * dBf - 1e-9,
    bankfull_width_m: d.bankfull_width_m || pyRound(wBf, 1),
    bankfull_depth_m: pyRound(dBf, 2),
    flood_prone_width_m: d.flood_prone_width_m,
    entrenchment_ratio: d.entrenchment_ratio,
    bank_height_ratio: d.bank_height_ratio,
    edge_limited: Boolean(d.edge_limited),
  }
  if (o.bankfullAreaM2 !== null && o.bankfullAreaM2 !== undefined) {
    out.bankfull_area_m2 = pyRound(o.bankfullAreaM2, 2)
    out.bankfull_area_edge_limited = areaEdge
  }
  if (tob !== null) out.top_of_bank_m = tob
  if (o.division) out.bankfull_division = o.division
  return out
}

// A reach's cross-section geometry from the published grid, in the shape the apps read
// (threedep.reach_geomorphology's result, geomorph.reach_stats, median_candidate and the K2b
// cross_section_quality record).
//
// The Shiny apps sample nine sections at 1/10 to 9/10 of the assessment reach from live 3DEP. The
// static app takes, at each of those nine positions, the nearest section of the national grid
// (staf-xs-current) on the HR lines the reach was cut from; a section the grid could not score is
// dropped, as the live sampler drops a transect it cannot use. Until the Shiny apps read the grid too
// (plan Phase 2) the two can differ.
import { pyFixed } from '../app/pyfmt'
import type { DataSource } from '../data/source'
import { interpolate, lineLength } from '../streams/geom'
import { sectionNumbers, sectionPulls, type XsNumbers } from '../xs/grid'
import { albersForward } from '../xs/projExact'
import type { Dataset } from './dataset'

const pyRound = (x: number, n: number) => Number(pyFixed(x, n))
const SCORED = new Set(['ok', 'ok_reference'])
export const BHR_CAP = 2.0
const ER_MIN = 1.0
const MIN_QUALITY_SECTIONS = 3
const BANK_ARM_RISE_M = 0.3
const BANK_ARM_RISE_FINE_M = 0.10

/** One sampled section, as candidates_from_transects records it (the fields the apps read). */
export interface Candidate {
  position_frac: number
  entrenchment_ratio: number | null
  bank_height_ratio: number | null
  bankfull_width_m: number | null
  bankfull_depth_m: number | null
  flood_prone_width_m: number | null
  low_bank_capped: boolean
  edge_limited: boolean
  bank_detection: string | null
  res: number
}

function median(v: number[]): number {
  const s = [...v].sort((a, b) => a - b)
  const n = s.length
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
}

/** geomorph.reach_stats */
export function reachStats(cands: Candidate[]): Record<string, any> {
  const out: Record<string, any> = { n: cands.length }
  for (const key of ['entrenchment_ratio', 'bank_height_ratio'] as const) {
    const vals = cands.filter((c) => c[key] !== null).map((c) => c[key] as number)
    if (vals.length) out[key] = { median: pyRound(median(vals), 2), min: pyRound(Math.min(...vals), 2), max: pyRound(Math.max(...vals), 2), n: vals.length }
  }
  const bhr = out.bank_height_ratio
  if (bhr) {
    const capped = cands.filter((c) => c.bank_height_ratio !== null && c.low_bank_capped).length
    bhr.capped = capped
    bhr.median_capped = !!(capped && bhr.median >= BHR_CAP - 1e-9)
    bhr.max_capped = !!(capped && bhr.max >= BHR_CAP - 1e-9)
  }
  return out
}

function avgRanks(idx: number[], values: Map<number, number>): Map<number, number> {
  const order = [...idx].sort((a, b) => values.get(a)! - values.get(b)!)
  const ranks = new Map<number, number>()
  let j = 0
  while (j < order.length) {
    let k = j
    while (k + 1 < order.length && values.get(order[k + 1]) === values.get(order[j])) k += 1
    for (let m = j; m <= k; m++) ranks.set(order[m], (j + k) / 2.0)
    j = k + 1
  }
  return ranks
}

function nearestMiddle(cands: Candidate[], idx: number[]): number {
  const n = cands.length
  const dist = (i: number) => (cands[i].position_frac !== null && cands[i].position_frac !== undefined
    ? Math.abs(cands[i].position_frac - 0.5) : Math.abs(i - (n - 1) / 2.0))
  return idx.reduce((best, i) => (dist(i) < dist(best) || (dist(i) === dist(best) && i < best) ? i : best), idx[0])
}

/** geomorph.median_candidate: the section nearest both reach medians (the default drawn). */
export function medianCandidate(cands: Candidate[]): number {
  if (!cands.length) return 0
  const both = cands.map((c, i) => [c, i] as const).filter(([c]) => c.entrenchment_ratio !== null && c.bank_height_ratio !== null).map(([, i]) => i)
  if (both.length >= 3) {
    const rer = avgRanks(both, new Map(both.map((i) => [i, cands[i].entrenchment_ratio as number])))
    const rbhr = avgRanks(both, new Map(both.map((i) => [i, cands[i].bank_height_ratio as number])))
    const mid = (both.length - 1) / 2.0
    const score = new Map(both.map((i) => [i, Math.abs(rer.get(i)! - mid) + Math.abs(rbhr.get(i)! - mid)]))
    const best = Math.min(...score.values())
    return nearestMiddle(cands, both.filter((i) => score.get(i) === best))
  }
  const ers = cands.map((c, i) => [c, i] as const).filter(([c]) => c.entrenchment_ratio !== null).map(([, i]) => i)
  if (ers.length) {
    const med = median(ers.map((i) => cands[i].entrenchment_ratio as number))
    const dist = new Map(ers.map((i) => [i, Math.abs((cands[i].entrenchment_ratio as number) - med)]))
    const best = Math.min(...dist.values())
    return nearestMiddle(cands, ers.filter((i) => dist.get(i) === best))
  }
  return nearestMiddle(cands, cands.map((_, i) => i))
}

/** geomorph.median_edge_limited */
export function medianEdgeLimited(cands: Candidate[]): boolean {
  const withEr = cands.filter((c) => c.entrenchment_ratio !== null).sort((a, b) => (a.entrenchment_ratio as number) - (b.entrenchment_ratio as number))
  const n = withEr.length
  if (!n) return false
  const mid = n % 2 ? [withEr[Math.floor(n / 2)]] : [withEr[n / 2 - 1], withEr[n / 2]]
  return mid.some((c) => c.edge_limited)
}

export interface ReachGeometry {
  candidate_scalars: Candidate[]
  reach: Record<string, any>
  entrenchment_ratio: number | null
  bank_height_ratio: number | null
  bankfull_width_m: number | null
  bankfull_depth_m: number | null
  edge_limited: boolean
  bankfull_extrapolated: boolean
  dem_resolution_m: number | null
  default_index: number
}

/** The grid's sections nearest the nine reach positions, as a reach geometry; null without any. */
export async function gridReachGeometry(src: DataSource, hr: Dataset, lineIds: number[], reachCoords: [number, number][],
                                        bankfullExtrapolated: boolean): Promise<{ geom: ReachGeometry | null, covered: boolean }> {
  const sections: { n: XsNumbers, x: number, y: number }[] = []
  let covered = true
  for (const id of lineIds) {
    const found = await hr.find('by_id', [id])
    const vpu = found[0]?.region.vpu
    await src.ready()
    if (!vpu || !src.has(`xs/xs-${vpu}.parquet`)) {
      covered = false
      continue
    }
    const [nums, pulls] = await Promise.all([sectionNumbers(src, vpu, BigInt(id)), sectionPulls(src, vpu, BigInt(id))])
    if (!pulls) continue
    const at = new Map(pulls.map((p) => [`${p.part}:${p.k}`, p]))
    for (const n of nums) {
      const p = at.get(`${n.part}:${n.k}`)
      if (p) sections.push({ n, x: p.x, y: p.y })
    }
  }
  if (reachCoords.length < 2 || !sections.length) return { geom: null, covered }
  const line = reachCoords.map(([x, y]) => albersForward(x, y))
  const length = lineLength(line)
  const cands: Candidate[] = []
  for (let i = 1; i <= 9; i++) {
    const [px, py] = interpolate(line, length * (i * 0.1))
    let best = sections[0]
    let bestD = Infinity
    for (const s of sections) {
      const dd = (s.x - px) ** 2 + (s.y - py) ** 2
      if (dd < bestD) {
        bestD = dd
        best = s
      }
    }
    const n = best.n
    if (!SCORED.has(n.status)) continue
    cands.push({
      position_frac: i * 0.1, entrenchment_ratio: n.er, bank_height_ratio: n.bhr, bankfull_width_m: n.bfw, bankfull_depth_m: n.bfd,
      flood_prone_width_m: n.fpw, low_bank_capped: !!(n.flags & 1), edge_limited: !!(n.flags & 2), bank_detection: n.bank, res: n.res,
    })
  }
  if (!cands.length) return { geom: null, covered }
  const reach = reachStats(cands)
  const d = medianCandidate(cands)
  return {
    covered,
    geom: {
      candidate_scalars: cands, reach,
      entrenchment_ratio: reach.entrenchment_ratio?.median ?? null, bank_height_ratio: reach.bank_height_ratio?.median ?? null,
      bankfull_width_m: cands[d].bankfull_width_m, bankfull_depth_m: cands[d].bankfull_depth_m,
      edge_limited: medianEdgeLimited(cands), bankfull_extrapolated: bankfullExtrapolated,
      dem_resolution_m: Math.max(...cands.map((c) => c.res)), default_index: d,
    },
  }
}

const bankRiseFloor = (res: number | null) => (res !== null && res <= 1.5 ? BANK_ARM_RISE_FINE_M : BANK_ARM_RISE_M)
export const capUnreachableDepth = (res: number | null) => bankRiseFloor(res) / BHR_CAP

/** geomorph.cross_section_quality (K2b): flags out_of_range_bhr, out_of_range_er, low_quality. */
export function crossSectionQuality(geom: ReachGeometry | null): Record<string, any> | null {
  if (!geom) return null
  const reach = geom.reach ?? {}
  const sections = geom.candidate_scalars ?? []
  const bhr = geom.bank_height_ratio
  const er = geom.entrenchment_ratio
  if (bhr === null && er === null && !sections.length && !Object.keys(reach).length) return null
  const nSections = Math.trunc(reach.n || sections.length || (bhr !== null || er !== null ? 1 : 0))
  const ratioN = (key: 'bank_height_ratio' | 'entrenchment_ratio', value: number | null) => {
    const stats = reach[key]
    if (stats) return Math.trunc(stats.n || 0)
    if (sections.length) return sections.filter((s) => s[key] !== null).length
    return value !== null ? 1 : 0
  }
  const bhrN = ratioN('bank_height_ratio', bhr)
  const erN = ratioN('entrenchment_ratio', er)
  const bhrStats = reach.bank_height_ratio ?? {}
  const capped = Math.trunc(bhrStats.capped || sections.filter((s) => s.low_bank_capped).length)
  const medianCapped = !!(bhrStats.median_capped || (capped && bhr !== null && bhr >= BHR_CAP - 1e-9))
  const demRes = geom.dem_resolution_m
  const floor = capUnreachableDepth(demRes)
  const unreachable = sections.filter((s) => s.low_bank_capped && s.bank_height_ratio !== null && s.bankfull_depth_m !== null
    && s.bankfull_depth_m <= floor + 1e-9).length
  const needed = bhrN ? Math.floor(bhrN / 2) + 1 : 0
  const capIsFloor = !!(medianCapped && sections.length && bhrN && (capped - unreachable) < needed)
  const found = new Set(sections.filter((s) => s.bank_detection).map((s) => String(s.bank_detection)))
  const detection = !found.size ? 'unknown' : found.size === 1 ? [...found][0] : 'mixed'
  const flags: string[] = []
  const reasons: Record<string, string> = {}
  const weak: string[] = []
  if (bhr !== null && !(bhr > 0.0)) {
    flags.push('out_of_range_bhr')
    reasons.out_of_range_bhr = `bank-height ratio ${pyFixed(bhr, 2)} is at or below zero (the low bank at or below the thalweg)`
  }
  if (er !== null && er < ER_MIN) {
    flags.push('out_of_range_er')
    reasons.out_of_range_er = `entrenchment ratio ${pyFixed(er, 2)} is below 1 (flood-prone width narrower than bankfull width)`
  }
  for (const [key, count] of [['bank-height ratio', bhr !== null ? bhrN : null], ['entrenchment ratio', er !== null ? erN : null]] as const) {
    if (count !== null && count < MIN_QUALITY_SECTIONS) weak.push(`only ${count} of ${nSections} section(s) carry a ${key}`)
  }
  if (geom.bankfull_extrapolated) weak.push('bankfull geometry is extrapolated outside the Bieger fit range')
  if (detection === 'crest_scan') weak.push('every section found its low bank by the crest scan, not a slope break')
  if (capIsFloor) {
    const resWords = demRes !== null ? `${demRes} m` : 'this'
    weak.push(`the reach median bank-height ratio sits at the ${BHR_CAP} cap only with sections whose bankfull depth is at or below `
      + `${pyFixed(floor, 2)} m, where the ${resWords} DEM's bank detector cannot place a bank below the cap (${unreachable} of ${capped} capped sections)`)
  }
  if (weak.length) {
    flags.push('low_quality')
    reasons.low_quality = weak.join('; ')
  }
  return { flags, reasons, detection, capIsFloor, medianCapped }
}

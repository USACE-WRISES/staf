// What stands behind a DEEP metric's criteria, read from the bundle (deep/reference_support.py): what it
// is scored against, the uncertainty and limitations lines, flagged transfers (StreamCurves REF-16), the
// metrics withheld and why, and the reference method statement. Every reader answers empty for a bundle
// that carries none of this, as the Python's do. The worksheet stays minimal (owner, 2026-10-04); the
// report, its CSV and the printed forms carry these lines.
import { pyFixed, pyG, pySigned } from '../../app/pyfmt'
import type { Bundle, Metric, Rec } from './method'

export const FIXED = 'fixed'
export const BASIS_REGIONAL = 'regional-reference'
export const BASIS_NATIONAL = 'national-reference'
export const BASIS_MODELED = 'modeled-reference'
export const BASIS_PUBLISHED = 'published-benchmark'
export const BASIS_OWNER = 'owner-entered'
export const BASIS_EASI_SCREENING = 'easi-screening-method'
const BASIS_LABELS: Record<string, string> = {
  [BASIS_REGIONAL]: 'Regional reference', [BASIS_NATIONAL]: 'National reference', [BASIS_MODELED]: 'Modeled reference',
  [BASIS_PUBLISHED]: 'Published benchmark', [BASIS_OWNER]: 'Owner-entered',
  [BASIS_EASI_SCREENING]: "Adopted from EASI's national screening method (provisional)",
}
const LEVEL_WORDS: Record<string, string> = { l3: 'Level III', l2: 'Level II', l1: 'Level I', nars9: 'NARS-9 region' }
export const UNVALIDATED = 'unvalidated'
const RISK_WORDS: Record<string, string> = {
  low: 'low', moderate: 'moderate', high: 'high', unassessed: 'not yet assessed',
  [UNVALIDATED]: 'unvalidated (transfer not confirmed by the recovery test)',
}
const LADDER_STATUSES = ['national', 'modeled', 'published']
const isObj = (v: unknown): v is Rec => v !== null && typeof v === 'object' && !Array.isArray(v)
const str = (v: unknown): string => (v === null || v === undefined || v === false ? '' : String(v))

/** str(x) of a Python value inside an f-string (an int as is, a float as its repr). */
function pyStr(v: unknown): string {
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  return String(v)
}

export const isFixed = (m: Metric | null | undefined): boolean => str((m ?? {}).criteriaBasis) === FIXED

/** basis_of: the rung of the basis ladder the curve rests on (derived for a bundle before 0.13). */
export function basisOf(m: Metric | null | undefined): string {
  const declared = str((m ?? {}).basis)
  if (declared in BASIS_LABELS) return declared
  return isFixed(m) ? BASIS_PUBLISHED : BASIS_REGIONAL
}

/** basis_label */
export function basisLabel(m: Metric | null | undefined): string {
  return str((m ?? {}).basisLabel) || BASIS_LABELS[basisOf(m)] || ''
}

function rawOf(assessment: Bundle | null | undefined): Rec {
  return (assessment ?? {}) as Rec
}

function support(m: Metric | null | undefined): Rec {
  const sup = (m ?? {}).referenceSupport
  return isObj(sup) ? sup : {}
}

export const isLadder = (m: Metric | null | undefined): boolean => LADDER_STATUSES.includes(str(support(m).status))

/** basis_text: basisStatement or basisLimit, at the metric or inside referenceSupport. */
export function basisText(m: Metric | null | undefined, key: string): string {
  return str((m ?? {})[key] || support(m)[key] || '').trim()
}

export function ownerChoice(m: Metric | null | undefined): Rec {
  const got = (m ?? {}).ownerDecision
  return isObj(got) ? got : {}
}

export function borrowedFrom(m: Metric | null | undefined): Rec {
  const got = (m ?? {}).borrowedFrom
  return isObj(got) ? got : {}
}

/** owner_line */
export function ownerLine(m: Metric | null | undefined): string {
  const d = ownerChoice(m)
  if (!Object.keys(d).length) return ''
  const who = str(d.recordedBy || "the assessment's owner")
  const on = str(d.recordedAt || '').slice(0, 10)
  const why = str(d.rationale || '').trim()
  return `Source chosen by ${who}${on ? ' on ' + on : ''}` + (why ? `: ${why}` : '')
}

function screenWords(sup: Rec): string {
  const lim = sup.agricultureLimit
  if (!sup.screenId || lim === null || lim === undefined) return ''
  const v = typeof lim === 'number' ? lim : typeof lim === 'string' && lim.trim() !== '' ? Number(lim) : NaN
  if (Number.isNaN(v) && !(typeof lim === 'string' && /^\s*[+-]?nan\s*$/i.test(lim))) return 'under the regional screen'
  return `under the regional screen (watershed agriculture at most ${pyG(v)} percent)`
}

/** support_line: what the metric is scored against, or "" for a bundle that does not say. */
export function supportLine(m: Metric | null | undefined): string {
  const mm = m ?? {}
  if (basisOf(mm) === BASIS_OWNER || Object.keys(borrowedFrom(mm)).length) return basisText(mm, 'basisStatement') || `${basisLabel(mm)}.`
  if (isFixed(mm) && !isLadder(mm)) return 'Fixed criteria, the same in every region'
  const basis = basisOf(mm)
  if ([BASIS_MODELED, BASIS_NATIONAL, BASIS_PUBLISHED].includes(basis) || isLadder(mm)) return basisText(mm, 'basisStatement') || `${basisLabel(mm)}.`
  const sup = mm.referenceSupport
  if (!isObj(sup) || !sup.status) return ''
  const n = pyStr(sup.nUsable)
  const status = str(sup.status)
  if (status === 'local') return `Reference curve from ${n} least-disturbed stations of this ecoregion`
  const screen = screenWords(sup)
  if (status === 'local_relaxed') return `Reference curve from ${n} least-disturbed streams of this ecoregion ${screen}`.trimEnd()
  const levelKey = str(sup.level)
  const level = LEVEL_WORDS[levelKey] ?? 'a parent'
  let where = levelKey === 'nars9' ? `${level} ${pyStr(sup.regionCode)}` : `${level} ecoregion ${pyStr(sup.regionCode)}`
  if (sup.regionName && levelKey !== 'nars9') where += ` (${pyStr(sup.regionName)})`
  const local = sup.nLocal
  const localTxt = local === null || local === undefined || local === '' ? '' : `, ${pyStr(local)} of them in this ecoregion`
  const risk = RISK_WORDS[str(sup.transferRisk)] ?? ''
  const riskTxt = risk ? `. Transfer risk ${risk}` : ''
  return `Reference curve from ${n} least-disturbed stations borrowed from ${where}${screen ? ' ' + screen : ''}${localTxt}${riskTxt}`
}

/** carried_line */
export function carriedLine(m: Metric | null | undefined): string {
  const got = (m ?? {}).carriedForward
  if (!isObj(got) || !got.fromVersion) return ''
  return `Carried forward unchanged from version ${pyStr(got.fromVersion)} of this assessment`
}

const PRIORITY_FROM_CONFIDENCE: Record<string, string> = { low: 'high', moderate: 'moderate', medium: 'moderate', high: 'low' }

/** reviewer_priority */
export const reviewerPriority = (label: unknown): string => PRIORITY_FROM_CONFIDENCE[str(label).trim().toLowerCase()] ?? ''

/** uncertainty_line */
export function uncertaintyLine(m: Metric | null | undefined): string {
  const mm = m ?? {}
  if (isFixed(mm) && !isLadder(mm)) return ''
  const n = mm.referenceN
  const disp = str(mm.sampleDisposition).trim()
  const parts: string[] = []
  if (typeof n === 'number' && !Number.isNaN(n)) parts.push(`Reference sample: ${Math.trunc(n)} stations` + (disp ? `, ${disp}` : ''))
  else if (disp) parts.push(`Reference sample: ${disp}`)
  const conf = str(mm.confidenceLabel).trim()
  const priority = reviewerPriority(conf)
  if (priority) parts.push(`reviewer priority: ${priority}` + (priority === conf.toLowerCase() ? '' : ` (builder confidence ${conf.toLowerCase()})`))
  return parts.join('; ')
}

/** is_flagged */
export function isFlagged(m: Metric | null | undefined): boolean {
  const mm = m ?? {}
  return str(mm.transferRisk) === UNVALIDATED || str(support(mm).transferRisk) === UNVALIDATED
}

function transferValidation(m: Metric | null | undefined): Rec {
  const mm = m ?? {}
  let got = mm.transferValidation
  if (!isObj(got)) got = support(mm).transferValidation
  return isObj(got) ? got : {}
}

function confidenceCap(m: Metric | null | undefined): unknown {
  const mm = m ?? {}
  let cap = mm.confidenceCap
  if (cap === null || cap === undefined) cap = support(mm).confidenceCap
  return cap
}

/** flag_line: the limitation a flagged transfer carries, "" for a curve that is not flagged. */
export function flagLine(m: Metric | null | undefined): string {
  if (!isFlagged(m)) return ''
  const v = transferValidation(m)
  const a1 = v.a1_share
  const n = v.n_cells
  const net = v.net_opt
  const minCells = Math.trunc(Number(v.min_cells || 4))
  const maxNet = Number(v.max_net_optimism || 0.05)
  let found: string
  if (a1 === null || a1 === undefined || Number.isNaN(a1)) {
    found = 'no recovery evidence for this source and metric; class agreement in two thirds of evaluation regions required'
  } else {
    found = `class agreement ${pyFixed(Number(a1), 2)} of evaluation regions; two thirds required`
    if (n !== null && n !== undefined && Math.trunc(Number(n)) < minCells) found += `, over ${Math.trunc(Number(n))} evaluation regions where ${minCells} are required`
    if (net !== null && net !== undefined && !Number.isNaN(net) && Number(net) > maxNet) {
      found += `; net optimism ${pySigned(Number(net), 2)}, at most ${pyFixed(maxNet, 2)} allowed`
    }
  }
  const cap = confidenceCap(m)
  const tail = cap !== null && cap !== undefined ? `; confidence capped at ${Math.trunc(Number(cap))}` : '; confidence capped'
  return `Scored on a reference pool whose transfer to this ecoregion was not confirmed by the recovery test (${found})${tail}.`
}

/** limitations_line: the curve's caveats, its basis's limit and a flagged transfer's line, each once. */
export function limitationsLine(m: Metric | null | undefined): string {
  const mm = m ?? {}
  const parts: string[] = []
  for (const c of mm.curveCaveats || []) {
    const text = str(c).trim()
    if (text && !parts.includes(text)) parts.push(text)
  }
  const limit = basisText(mm, 'basisLimit')
  if (limit && !parts.includes(limit)) parts.push(limit)
  const flag = flagLine(mm)
  if (flag && !parts.includes(flag)) parts.push(flag)
  return parts.join(' ')
}

/** practitioner_lines */
export function practitionerLines(m: Metric | null | undefined): string[] {
  return [supportLine(m), carriedLine(m), ownerLine(m), uncertaintyLine(m), limitationsLine(m)].filter(Boolean)
}

/** flagged_functions */
export function flaggedFunctions(assessment: Bundle | null | undefined): Rec[] {
  const out: Rec[] = []
  for (const fn of rawOf(assessment).metricsByFunction || []) {
    const names = (fn.metrics || []).filter((m: Metric) => isFlagged(m)).map((m: Metric) => str(m.metricName || m.metricId))
    if (names.length) out.push({ functionId: fn.functionId, functionName: fn.functionName || fn.functionId, metrics: names })
  }
  return out
}

/** function_flag_line */
export function functionFlagLine(assessment: Bundle | null | undefined, functionId: string): string {
  const fid = str(functionId)
  for (const f of flaggedFunctions(assessment)) {
    if (str(f.functionId) === fid) {
      const one = f.metrics.length === 1
      return `This function is rated on ${one ? 'a curve' : 'curves'} whose reference pool transfer to this ecoregion was not confirmed by the `
        + `recovery test (${f.metrics.join(', ')}); the score enters the index with confidence capped.`
    }
  }
  return ''
}

/** flagged_summary */
export function flaggedSummary(assessment: Bundle | null | undefined): string {
  const flagged = flaggedFunctions(assessment)
  if (!flagged.length) return ''
  const rated = (rawOf(assessment).metricsByFunction || []).filter((fn: Rec) => fn.metrics?.length).length
  const names = flagged.map((f) => str(f.functionName)).join(', ')
  return `${flagged.length} of the ${rated} rated functions rest on a reference pool whose transfer to this ecoregion was not confirmed by the `
    + `recovery test (${names}); each is rated with confidence capped and flagged on its metric row.`
}

// ---------------------------------------------------------------- metrics withheld
export const HELD_FOR_REVIEW = 'held-for-review'
export const HIGH_MISSINGNESS = 'high-missingness'
export const MEASUREMENT_PRECISION_FLOOR = 'measurement-precision-floor'
const RULE_LABELS: Record<string, [string, string]> = {
  [HIGH_MISSINGNESS]: ['withheld: high missingness (DATA-03)', 'Withheld: high missingness (DATA-03), not scored'],
  [MEASUREMENT_PRECISION_FLOOR]: ['withheld: measurement-precision floor (CURVE-09)', 'Withheld: measurement-precision floor (CURVE-09), not scored'],
}
const reasonOf = (w: Rec | null | undefined) => str((w ?? {}).reason)
const isHeld = (w: Rec) => reasonOf(w) === HELD_FOR_REVIEW

/** withheld_reason: the reason as a short label. */
export function withheldReason(w: Rec): string {
  if (isHeld(w)) return 'Held for review'
  return (RULE_LABELS[reasonOf(w)] ?? ['Insufficient reference support', ''])[0]
}

/** withheld_title */
export function withheldTitle(w: Rec): string {
  if (isHeld(w)) return 'Held for review, not scored'
  return (RULE_LABELS[reasonOf(w)] ?? ['', 'Insufficient reference support, not scored'])[1]
}

/** withheld_note: one sentence per reason present. */
export function withheldNote(items: Rec[]): string {
  const reasons = new Set(items.map(reasonOf))
  const parts = ['These metrics are not scored.']
  if (items.some((w) => !isHeld(w) && !(reasonOf(w) in RULE_LABELS))) {
    parts.push('A metric withheld for insufficient reference support has no curve. No pool of least-disturbed stations in this ecoregion or its '
      + 'parents supports it, no national donor pool was shown to transfer, no modeled expectation passed validation, and no published criterion applies.')
  }
  if (reasons.has(HIGH_MISSINGNESS)) {
    parts.push('A metric withheld for high missingness has a reference pool, but more of its stations than the rule allows carry no value for the '
      + 'metric, so no curve was built (rule DATA-03).')
  }
  if (reasons.has(MEASUREMENT_PRECISION_FLOOR)) {
    parts.push("A metric withheld under the measurement-precision floor has a two-sided curve whose Functioning core is narrower than the metric's "
      + 'documented measurement precision, so the curve was not published (rule CURVE-09).')
  }
  if (items.some(isHeld)) parts.push('A metric held for review has a curve that a reviewer has not yet cleared, so the curve is not published.')
  return parts.join(' ')
}

/** withheld */
export function withheld(assessment: Bundle | null | undefined): Rec[] {
  const items = rawOf(assessment).insufficientReferenceSupport
  return Array.isArray(items) ? items.filter(isObj) : []
}

/** reference_method_statement */
export function referenceMethodStatement(assessment: Bundle | null | undefined): string {
  const block = rawOf(assessment).referenceMethod
  return isObj(block) ? str(block.statement || '') : ''
}

/** stratum_label */
export function stratumLabel(label: string | null | undefined, m: Metric | null | undefined = null): string {
  if (label === null || label === undefined || label === '') return 'All streams (pooled)'
  const block = (m ?? {}).stratifier
  if (isObj(block)) {
    for (const cls of block.classes || []) if (str((cls ?? {}).key) === str(label) && cls.label) return str(cls.label)
  }
  return str(label)
}

// EASI's screening-method evaluator (easi/screening_methods.py, the frozen method's code), over the
// adopted package's catalog (screening-methods.json) and reference curves: the typed operators, the
// curve bands at 0.39 and 0.69, the applicability rules, and the serializable scoring trace, key for
// key and in the Python's key order. No arbitrary expressions.
import { pyFixed, pyFloat, pyG, pySum } from '../../app/pyfmt'
import { pyEntries, pyValues } from '../../app/pyjson'
import { method, RATING_INDEX, RATINGS } from './method'

export const WITHHELD = 'withheld'
export const DEFAULT_GAP_STATEMENT = 'Rating withheld: the method does not apply to this reach.'
export const COMPOSITE_OPERATORS = new Set(['worst_index', 'best_index', 'mean_index'])

export interface Evaluation { rating: string | null, index: number | null, combined_value: any, trace: Record<string, any> }

type Obj = Record<string, any>
const has = (o: Obj | null | undefined, k: string): boolean => o !== null && o !== undefined && Object.prototype.hasOwnProperty.call(o, k)
/** dict.get: the value, or ``dflt`` when the key is absent (a present None stays None). */
const get = (o: Obj | null | undefined, k: string, dflt: any = null): any => (has(o, k) ? (o as Obj)[k] : dflt)
const truthy = (v: any): boolean => !(v === null || v === undefined || v === false || v === 0 || v === '' || (Array.isArray(v) && !v.length)
  || (typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length))
/** Python's round(x, n) for a float. */
export const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))
const clone = <T>(v: T): T => (v === undefined ? v : JSON.parse(JSON.stringify(v)))

export const catalog = (): Obj => method().catalog
const ratingIndexTable = (): Record<string, number> => (truthy(catalog().ratingIndex) ? catalog().ratingIndex : RATING_INDEX)

/** curve_sets: the packaged reference curves. */
export const curveSets = (): Obj => method().curves.sets ?? {}

const PY_FLOAT = /^[+-]?(\d+(_?\d+)*(\.(\d+(_?\d+)*)?)?|\.\d+(_?\d+)*)([eE][+-]?\d+(_?\d+)*)?$/

/** float(value) as Python reads it, or null where it raises. */
export function pyFloatOf(value: any): number | null {
  if (typeof value === 'number') return value
  if (typeof value === 'bigint') return Number(value)
  if (typeof value === 'boolean') return value ? 1 : 0
  if (typeof value === 'string') {
    const s = value.trim()
    if (PY_FLOAT.test(s)) return Number(s.replace(/_/g, ''))
    if (/^[+-]?(inf|infinity)$/i.test(s)) return s.startsWith('-') ? -Infinity : Infinity
    if (/^[+-]?nan$/i.test(s)) return NaN
  }
  return null
}

/** _number: a finite float, else None (booleans and None are not numbers). */
export function num(value: any): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') return null
  const out = pyFloatOf(value)
  return out !== null && Number.isFinite(out) ? out : null
}

/** _fmt: an integer-valued number without its point, else f"{value:g}". */
export function fmt(value: number): string {
  return Number.isInteger(value) ? BigInt(value).toString() : pyG(value)
}

/** interp_curve (StreamCurves' interpolation for JSON point lists). */
export function interpCurve(points: any, x: number): number | null {
  if (points === null || points === undefined) return null
  let pairs: [any, any][]
  if (!Array.isArray(points) && typeof points === 'object') {
    const xs = get(points, 'metric_value', get(points, 'x'))
    const ys = get(points, 'index_score', get(points, 'y'))
    if (xs === null || ys === null) return null
    pairs = xs.map((v: any, i: number) => [v, ys[i]]).slice(0, Math.min(xs.length, ys.length))
  } else {
    pairs = points.map((p: any) => (p !== null && typeof p === 'object' && !Array.isArray(p)
      ? [get(p, 'x', get(p, 'metric_value')), get(p, 'y', get(p, 'index_score'))] : [p[0], p[1]]))
  }
  const pts: [number, number][] = []
  for (const [px, py] of pairs) {
    if (px === null || px === undefined || py === null || py === undefined) continue
    const fx = Number(pyFloatOf(px))
    const fy = Number(pyFloatOf(py))
    if (Number.isNaN(fx) || Number.isNaN(fy)) continue
    pts.push([fx, fy])
  }
  pts.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  if (!pts.length) return null
  const clamp = (y: number) => (y < 0.0 ? 0.0 : y > 1.0 ? 1.0 : y)
  if (pts.length === 1 || x <= pts[0][0]) return clamp(pts[0][1])
  if (x >= pts[pts.length - 1][0]) return clamp(pts[pts.length - 1][1])
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i]
    const b = pts[i + 1]
    if (a[0] <= x && x <= b[0]) {
      const span = b[0] - a[0]
      if (span <= 0) return clamp(b[1])
      const t = (x - a[0]) / span
      return clamp(a[1] + t * (b[1] - a[1]))
    }
  }
  return clamp(pts[pts.length - 1][1])
}

/** resolve_curve: the requested stratum or an explicit fallback, with its depth. */
export function resolveCurve(spec: Obj, strata: Obj | null): [string, Obj, number] {
  if (spec.mode !== 'banded') throw new Error('reference curves support only banded mode')
  const setId = spec.set
  const definition = curveSets()[setId]
  if (definition === undefined || definition === null) throw new Error(`unknown reference curve set '${setId}'`)
  const stratifier = spec.stratifier
  if (stratifier !== definition.stratifier) throw new Error(`${setId}: curve stratifier does not match the set`)
  const curves = definition.curves ?? {}
  if (!has(curves, 'national')) throw new Error(`${setId}: a national reference curve is required`)
  const primary = stratifier === 'national' ? 'national' : get(strata ?? {}, stratifier)
  const candidates = [primary, ...(spec.fallback ?? [])]
  for (let depth = 0; depth < candidates.length; depth++) {
    const key = candidates[depth]
    if (key !== null && key !== undefined && has(curves, String(key))) return [String(key), curves[String(key)], depth]
  }
  throw new Error(`${setId}: no reference curve resolves for '${primary}'`)
}

export const strataOf = (context: Obj | null | undefined): Obj => (context ?? {}).strata || {}

function curveTrace(spec: Obj, strata: Obj | null): Obj {
  const [key, curve, depth] = resolveCurve(spec, strata)
  return { set: spec.set, stratum: key, fallbackDepth: depth, n: get(curve, 'n', get(curve, 'nMembers')), x69: curve.x69, x39: curve.x39 }
}

/** score_quantity: one physical quantity's rating, its anchor index and its curve trace. */
export function scoreQuantity(value: any, rule: Obj, strata: Obj | null): [string | null, number | null, Obj | null] {
  const number = num(value)
  if (number === null) return [null, null, null]
  let trace: Obj | null = null
  let rating: string | null
  if (truthy(rule.curve)) {
    const spec = rule.curve
    const [, curve] = resolveCurve(spec, strata)
    const interpolated = interpCurve(curve.points, number)
    if (interpolated === null) return [null, null, null]
    rating = interpolated >= 0.69 ? 'Good' : interpolated >= 0.39 ? 'Fair' : 'Poor'
    trace = curveTrace(spec, strata)
  } else {
    rating = ratingForValue(number, rule.bands || [])
  }
  const index = rating !== null && has(ratingIndexTable(), rating) ? ratingIndexTable()[rating] : null
  return [rating, index, trace]
}

/** curve_bands: synthetic physical-value bands for display, never used for curve scoring. */
export function curveBands(spec: Obj, strata: Obj | null): Obj[] {
  const [, curve] = resolveCurve(spec, strata)
  const x39 = Number(pyFloatOf(curve.x39))
  const x69 = Number(pyFloatOf(curve.x69))
  const values: [string, number | null, number | null, boolean, boolean, string][] = curveSets()[spec.set].higherIsBetter
    ? [['Poor', null, x39, false, false, `<${fmt(x39)}`],
      ['Fair', x39, x69, true, false, `≥${fmt(x39)} to <${fmt(x69)}`],
      ['Good', x69, null, true, false, `≥${fmt(x69)}`]]
    : [['Good', null, x69, false, true, `≤${fmt(x69)}`],
      ['Fair', x69, x39, false, true, `>${fmt(x69)} to ≤${fmt(x39)}`],
      ['Poor', x39, null, false, false, `>${fmt(x39)}`]]
  return values.map(([rating, lo, hi, loInc, hiInc, label]) => ({ rating, min: lo, max: hi, minInclusive: loInc, maxInclusive: hiInc, label }))
}

let nars9Names: ((code: string) => string | null) | null = null
/** geo.nars9_name, supplied by the strata module once the polygons are read. */
export function setNars9Names(fn: (code: string) => string | null): void {
  nars9Names = fn
}

/** pyFixedComma for an int (f"{int(n):,}") */
const intComma = (n: number) => Math.trunc(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')

/** curve_reference: the reference-panel description attached to displayed criteria. */
export function curveReference(spec: Obj, strata: Obj | null): string {
  const [key, curve, depth] = resolveCurve(spec, strata)
  const n = get(curve, 'n', get(curve, 'nMembers', 0))
  let location: string
  if (key === 'national' || depth) location = 'national curve'
  else if (spec.stratifier === 'l2') {
    const name = get(strata ?? {}, 'l2_name')
    location = `Level II region ${key}` + (truthy(name) ? `, ${name}` : '')
  } else if (spec.stratifier === 'nars9') {
    const name = nars9Names ? nars9Names(key) : null
    location = `NARS-9 region ${key}` + (truthy(name) ? `, ${name}` : '')
  } else location = `slope class ${key}`
  return `${location}, ${intComma(Number(n))} reference reaches; crossings are approximate`
}

export function ruleForInput(m: Obj, inp: Obj, context: Obj | null = null): Obj {
  if (truthy(inp.regionalBands)) {
    const key = (m.formula || {}).regionalContextKey ?? 'region'
    return { bands: regionalBands(inp, get(context ?? {}, key)) }
  }
  if (truthy(inp.curve) || truthy(inp.bands)) return inp
  return m
}

export function ruleForMethod(m: Obj): Obj {
  if (truthy(m.curve) || truthy(m.bands)) return m
  return (m.inputs ?? []).find((i: Obj) => !truthy(i.contextOnly)) ?? m
}

export function methodsByMetric(): Record<string, Obj> {
  const out: Record<string, Obj> = {}
  for (const m of catalog().methods ?? []) out[m.metricId] = m
  return out
}

export function methodFor(metricId: string): Obj {
  const m = methodsByMetric()[metricId]
  if (!m) throw new Error(`no screening method for metric '${metricId}'`)
  return m
}

/** _resolved_method: a parent method or one of its source-tier variants. */
export function resolvedMethod(parent: Obj, variantKey: string | null | undefined): Obj {
  if (!variantKey || variantKey === parent.methodKey) return parent
  const variant = (parent.variants ?? []).find((v: Obj) => v.methodKey === variantKey)
  if (!variant) throw new Error(`method '${parent.metricId}' has no variant '${variantKey}'`)
  const merged: Obj = { ...parent, ...variant }
  if (has(variant, 'curve')) delete merged.bands
  else if (has(variant, 'bands')) delete merged.curve
  for (const key of ['applicability', 'statement']) if (!has(variant, key)) delete merged[key]
  merged.metricId = parent.metricId
  merged.title = parent.title
  merged.catalogMethodKey = parent.methodKey
  merged.sourceHierarchy = [...(parent.sourceHierarchy || [])]
  merged.variants = [...(parent.variants || [])]
  return merged
}

/** method_for_trace: the exact method variant that produced a serialized trace. */
export function methodForTrace(metricId: string, trace: Obj | null | undefined): Obj {
  return resolvedMethod(methodFor(metricId), (trace ?? {}).methodKey)
}

export function equationFor(m: Obj): string {
  let equation = baseEquationFor(m)
  if (truthy(m.curve) || (m.inputs ?? []).some((i: Obj) => truthy(i.curve))) equation += '; curve index is banded at 0.39 and 0.69'
  return equation
}

function baseEquationFor(m: Obj): string {
  const operator = m.operator
  const inputs: Record<string, Obj> = {}
  for (const i of m.inputs ?? []) inputs[i.key] = i
  const formula = m.formula || {}
  const sym = (i: Obj | undefined, dflt: string) => get(i ?? {}, 'symbol', dflt)
  if (operator === 'unscored') return 'No automated rating'
  if (operator === 'categorical_lookup') {
    const key = get(formula, 'input')
    return `Rating = lookup(${sym(inputs[key], key || 'category')})`
  }
  if (operator === 'threshold') {
    const inp = (m.inputs ?? []).find((i: Obj) => !truthy(i.contextOnly)) ?? null
    return `Rating = bands(${get(inp ?? {}, 'symbol', 'value')})`
  }
  if (operator === 'ratio') {
    const numerator = sym(inputs[formula.numerator], formula.numerator)
    const denominator = sym(inputs[formula.denominator], formula.denominator)
    const nMult = Number(pyFloatOf(get(formula, 'numeratorMultiplier', 1)))
    const dMult = Number(pyFloatOf(get(formula, 'denominatorMultiplier', 1)))
    const nText = nMult === 1 ? numerator : `${fmt(nMult)} × ${numerator}`
    const dText = dMult === 1 ? denominator : `${fmt(dMult)} × ${denominator}`
    const result = get(formula, 'resultSymbol', 'V')
    const units = truthy(formula.units) ? ` (${formula.units})` : ''
    return `${result}${units} = ${nText} / (${dText})`
  }
  if (operator === 'minimum') {
    const symbols = (m.inputs ?? []).filter((i: Obj) => !truthy(i.contextOnly)).map((i: Obj) => sym(i, i.key))
    return `${get(formula, 'resultSymbol', 'V')} = min(${symbols.join(', ')})`
  }
  if (operator === 'minimum_of_products') {
    const products: string[] = []
    const names: string[] = []
    for (const [name, keys] of pyEntries(get(formula, 'products', {}) as Record<string, string[]>)) {
      names.push(name)
      products.push(`${name} = ${keys.map((k) => sym(inputs[k], k)).join(' × ')}`)
    }
    products.push(`${get(formula, 'resultSymbol', 'V')} = min(${names.join(', ')})`)
    return products.join('; ')
  }
  if (COMPOSITE_OPERATORS.has(operator)) {
    const symbols = (m.inputs ?? []).filter((i: Obj) => !truthy(i.contextOnly)).map((i: Obj) => get(i, 'indexSymbol', `I${i.key}`))
    if (operator === 'mean_index') return `Icombined = mean(${symbols.join(', ')})`
    return `Icombined = ${operator === 'worst_index' ? 'min' : 'max'}(${symbols.join(', ')})`
  }
  if (operator === 'sum_capped') {
    const symbols = (m.inputs ?? []).filter((i: Obj) => !truthy(i.contextOnly)).map((i: Obj) => sym(i, i.key))
    return `${get(formula, 'resultSymbol', 'V')} = min(${symbols.join(' + ')}, ${fmt(Number(pyFloatOf(formula.cap)))})`
  }
  if (operator === 'weighted_capped_sum') {
    const pieces: string[] = []
    for (const term of get(formula, 'terms', []) as Obj[]) {
      const symbol = sym(inputs[term.input], term.input)
      const weight = fmt(Number(pyFloatOf(term.weight)))
      const expr = term.transform === 'cap'
        ? `min(${symbol}/${fmt(Number(pyFloatOf(term.cap)))}, 1)`
        : `clamp((${symbol}−${fmt(Number(pyFloatOf(get(term, 'offset', 0))))})/${fmt(Number(pyFloatOf(term.cap)))}, 0, 1)`
      pieces.push(`${weight} × ${expr}`)
    }
    return `${get(formula, 'resultSymbol', 'V')} = ${pieces.join(' + ')}`
  }
  throw new Error(`unsupported operator '${operator}'`)
}

/** regional_bands: exact NRSA Good/Fair/Poor bands for one analyte and region. */
export function regionalBands(inputDef: Obj, region: any): Obj[] {
  const pair = get(inputDef.regionalBands || {}, String(region || '').toUpperCase())
  if (!truthy(pair)) return []
  const goodFair = Number(pyFloatOf(pair[0]))
  const fairPoor = Number(pyFloatOf(pair[1]))
  return [
    { rating: 'Good', min: null, max: goodFair, minInclusive: false, maxInclusive: true, label: `≤${fmt(goodFair)}` },
    { rating: 'Fair', min: goodFair, max: fairPoor, minInclusive: false, maxInclusive: false, label: `>${fmt(goodFair)}-<${fmt(fairPoor)}` },
    { rating: 'Poor', min: fairPoor, max: null, minInclusive: true, maxInclusive: false, label: `≥${fmt(fairPoor)}` },
  ]
}

export function bandsForInput(m: Obj, inputDef: Obj, context: Obj | null = null): Obj[] {
  const rule = ruleForInput(m, inputDef, context)
  if (truthy(rule.curve)) return curveBands(rule.curve, strataOf(context))
  return rule.bands || []
}

/** rating_for_value */
export function ratingForValue(value: number, bands: Obj[]): string | null {
  for (const band of bands) {
    const lo = get(band, 'min')
    const hi = get(band, 'max')
    const loOk = lo === null || value > Number(pyFloatOf(lo)) || (get(band, 'minInclusive', false) && value === Number(pyFloatOf(lo)))
    const hiOk = hi === null || value < Number(pyFloatOf(hi)) || (get(band, 'maxInclusive', false) && value === Number(pyFloatOf(hi)))
    if (loOk && hiOk) return band.rating
  }
  return null
}

/** criteria_for: the automated and field criteria for the tooltip and the Scoring panel. */
export function criteriaFor(m: Obj, context: Obj | null = null): Obj {
  const labels = (bands: Obj[], rule: Obj) => {
    const reference = truthy(rule.curve) ? curveReference(rule.curve, strataOf(context)) : ''
    const suffix = reference ? ` (${reference})` : ''
    const out: Obj = {}
    for (const b of bands) out[b.rating] = b.label + suffix
    return out
  }
  const auto: Obj[] = []
  if (COMPOSITE_OPERATORS.has(m.operator)) {
    for (const inp of m.inputs ?? []) {
      const bands = bandsForInput(m, inp, context)
      if (bands.length) {
        const entry: Obj = { input: inp.key, label: inp.label, units: get(inp, 'units', ''), bands: labels(bands, ruleForInput(m, inp, context)) }
        if (truthy(inp.curve)) entry.reference = curveReference(inp.curve, strataOf(context))
        auto.push(entry)
      }
    }
  } else if (truthy(ruleForMethod(m).bands) || truthy(ruleForMethod(m).curve)) {
    const rule = ruleForMethod(m)
    const bands = truthy(rule.curve) ? curveBands(rule.curve, strataOf(context)) : rule.bands
    const scored = (m.inputs ?? []).filter((i: Obj) => !truthy(i.contextOnly))
    const units = (m.formula || {}).units
    const entry: Obj = {
      input: scored.length ? scored[0].key : 'value',
      label: m.title,
      units: truthy(units) ? units : (scored.length ? get(scored[0], 'units', '') : ''),
      bands: labels(bands, rule),
    }
    if (truthy(rule.curve)) entry.reference = curveReference(rule.curve, strataOf(context))
    auto.push(entry)
  } else if (m.operator === 'categorical_lookup') {
    const grouped: Record<string, string[]> = {}
    for (const item of pyValues(get(m.formula || {}, 'lookup', {}) as Record<string, Obj>)) {
      if (truthy(item.rating)) (grouped[item.rating] ??= []).push(get(item, 'label', ''))
    }
    if (Object.keys(grouped).length) {
      const bands: Obj = {}
      for (const [rating, ls] of Object.entries(grouped)) bands[rating] = ls.join(' · ')
      auto.push({ input: get(m.formula || {}, 'input', 'category'), label: m.title, units: '', bands })
    }
  }
  return { automated: auto, fieldReference: get(m, 'fieldReference') }
}

const normalCategory = (value: any): string => (truthy(value) ? pyStr(value) : '').trim().toUpperCase().replace(/ /g, '')
const indexRating = (index: number): string => (index >= 0.69 ? 'Good' : index >= 0.39 ? 'Fair' : 'Poor')

/** str(value) for the values a method reads (text, numbers as Python writes them). */
export function pyStr(value: any): string {
  if (typeof value === 'number') return Number.isSafeInteger(value) ? String(value) : pyFloat(value)
  if (typeof value === 'boolean') return value ? 'True' : 'False'
  if (value === null || value === undefined) return 'None'
  return String(value)
}

/** _match_token */
function matchToken(value: any): string {
  if (typeof value === 'boolean') return String(value)
  const number = num(value)
  if (number !== null && Number.isInteger(number)) return BigInt(number).toString()
  return pyStr(value).trim()
}

function withholdFlags(rule: Obj, m: Obj): Set<string> {
  const rated = (m.inputs ?? []).filter((i: Obj) => !truthy(i.contextOnly)).map((i: Obj) => i.key)
  const out = new Set<string>()
  for (const raw of rule.withhold_when || []) {
    const flag = String(raw)
    if (flag === 'out_of_range') for (const key of rated) out.add(`out_of_range_${key}`)
    else out.add(flag)
  }
  return out
}

/** applicability_check: the outcome of a method's ``applicability`` rule, or null without one. */
export function applicabilityCheck(m: Obj, values: Obj, evidence: Obj | null): Obj | null {
  const rule = m.applicability
  if (rule === null || rule === undefined || typeof rule !== 'object' || Array.isArray(rule)) return null
  const statement = String(truthy(rule.statement) ? rule.statement : DEFAULT_GAP_STATEMENT)
  const out: Obj = { rule: clone(rule), withheld: false, matched: null }
  if (truthy(rule.input)) {
    const value = get(values, rule.input)
    if (value === null || value === undefined) return out
    const token = matchToken(value)
    if (!(rule.exclude || []).map(matchToken).includes(token)) return out
    out.withheld = true
    out.matched = { input: rule.input, value }
    out.statement = statement.split('{value}').join(token)
    return out
  }
  if (truthy(rule.evidence)) {
    const name = String(rule.evidence)
    const record = get(evidence ?? {}, name)
    if (record === null || typeof record !== 'object' || Array.isArray(record)) return out
    const flags = (record.flags || []).map((f: any) => String(f))
    out.checked = { evidence: name, flags }
    const wanted = withholdFlags(rule, m)
    const hit = [...new Set<string>(flags.filter((f: string) => wanted.has(f)))].sort()
    if (!hit.length) return out
    const reasons = record.reasons || {}
    const words = hit.map((f) => String(truthy(reasons[f]) ? reasons[f] : f)).join('; ')
    out.withheld = true
    out.matched = { evidence: name, flags: hit, record: clone(record) }
    out.statement = statement.split('{value}').join(words)
    return out
  }
  return out
}

function inputTrace(m: Obj, values: Obj, inputMeta: Obj | null): Obj[] {
  const meta = inputMeta ?? {}
  return (m.inputs ?? []).map((inp: Obj) => {
    const value = get(values, inp.key)
    const extra = meta[inp.key] || {}
    const available = value !== null && value !== undefined && (num(value) !== null || typeof value === 'string')
    const out: Obj = {
      key: inp.key, label: inp.label, value: value === undefined ? null : value, units: get(inp, 'units', ''),
      source: truthy(extra.source) ? extra.source : get(inp, 'sourceField', ''), available, required: truthy(inp.required),
      contextOnly: truthy(inp.contextOnly), rationale: get(inp, 'rationale', ''),
    }
    if (extra.details !== null && extra.details !== undefined) out.details = extra.details
    return out
  })
}

interface FinishOpts { rating: string | null, combined?: any, governing?: string | null, completeness: string, warnings?: string[],
  inputRatings?: Record<string, string>, context?: Obj | null }

function finish(m: Obj, values: Obj, inputMeta: Obj | null, confidence: string | null | undefined, o: FinishOpts): Evaluation {
  const table = ratingIndexTable()
  const index = o.rating ? (has(table, o.rating) ? table[o.rating] : null) : null
  const traces = inputTrace(m, values, inputMeta)
  for (const item of traces) {
    if (o.inputRatings && has(o.inputRatings, item.key)) {
      item.rating = o.inputRatings[item.key]
      item.index = table[o.inputRatings[item.key]]
    }
  }
  let sourceTier = truthy(m._sourceTierOverride) ? m._sourceTierOverride : get(m, 'sourceTier')
  if (!truthy(sourceTier)) sourceTier = o.rating === null ? 'unavailable' : 'screening-proxy'
  const trace: Obj = {
    methodKey: m.methodKey,
    methodKind: m.operator,
    methodStatus: m.status,
    basisClass: m.basisClass,
    provisional: truthy(m.provisional),
    equation: equationFor(m),
    inputs: traces,
    combinedValue: o.combined === undefined ? null : o.combined,
    governingInput: o.governing ?? null,
    generatedRating: o.rating,
    generatedIndex: index,
    completeness: o.completeness,
    confidence: truthy(confidence) ? confidence : get(m, 'confidence', 'L'),
    sourceTier,
    evidenceFamily: truthy(m._evidenceFamilyOverride) ? m._evidenceFamilyOverride : (truthy(m.evidenceFamily) ? m.evidenceFamily : ''),
    usedFallback: truthy(has(m, '_usedFallbackOverride') ? m._usedFallbackOverride : get(m, 'usedFallback', false)),
    observedOverridesProxy: truthy(has(m, '_observedOverridesProxyOverride') ? m._observedOverridesProxyOverride : get(m, 'observedOverridesProxy', false)),
    sourceHierarchy: [...(m.sourceHierarchy || [])],
    limitations: [...(m.limitations || [])],
    warnings: [...(o.warnings || [])],
    context: { ...(o.context || {}) },
  }
  const curves: Obj = {}
  for (const [key, rule] of [['method', m], ...(m.inputs ?? []).map((i: Obj) => [i.key, i])] as [string, Obj][]) {
    if (truthy(rule.curve)) curves[key] = curveTrace(rule.curve, strataOf(o.context))
  }
  if (Object.keys(curves).length) trace.curves = curves
  const check = get(m, '_applicability')
  if (check !== null) {
    trace.applicability = check
    if (check.withheld) {
      trace.statement = check.statement
      trace.usedFallback = false
    }
  } else if (o.completeness === WITHHELD && truthy(m.statement)) {
    trace.statement = String(m.statement)
  }
  return { rating: o.rating, index, combined_value: o.combined === undefined ? null : o.combined, trace }
}

export interface EvaluateOpts {
  context?: Obj | null
  input_meta?: Obj | null
  confidence?: string | null
  variant_key?: string | null
  source_tier?: string | null
  evidence_family?: string | null
  used_fallback?: boolean | null
  observed_overrides_proxy?: boolean | null
  evidence?: Obj | null
}

const given = (v: any) => v !== null && v !== undefined

/** evaluate: one catalog method's rating and its full scoring trace. */
export function evaluate(metricId: string, valuesIn: Obj | null, o: EvaluateOpts = {}): Evaluation {
  const m: Obj = { ...resolvedMethod(methodFor(metricId), o.variant_key) }
  if (given(o.source_tier)) m._sourceTierOverride = o.source_tier
  if (given(o.evidence_family)) m._evidenceFamilyOverride = o.evidence_family
  if (given(o.used_fallback)) m._usedFallbackOverride = o.used_fallback
  if (given(o.observed_overrides_proxy)) m._observedOverridesProxyOverride = o.observed_overrides_proxy
  const operator = m.operator
  const values: Obj = { ...(valuesIn || {}) }
  const context: Obj = { ...(o.context || {}) }
  const inputMeta = o.input_meta ?? null
  const confidence = o.confidence ?? null
  const required = (m.inputs ?? []).filter((i: Obj) => truthy(i.required) && !truthy(i.contextOnly))
  const availableRequired = required.filter((i: Obj) => given(values[i.key]))
  const done = (x: FinishOpts) => finish(m, values, inputMeta, confidence, x)

  const check = applicabilityCheck(m, values, o.evidence ?? null)
  if (check !== null) {
    m._applicability = check
    if (check.withheld) return done({ rating: null, completeness: WITHHELD, context })
  }

  if (operator === 'unscored') {
    const anyContext = (m.inputs ?? []).some((i: Obj) => given(values[i.key]))
    let completeness = anyContext ? 'context_only' : 'not_assessed'
    if (truthy(m.statement)) completeness = WITHHELD
    return done({ rating: null, completeness, context })
  }

  if (operator === 'categorical_lookup') {
    const formula = m.formula || {}
    const raw = get(values, get(formula, 'input'))
    if (!given(raw)) return done({ rating: null, completeness: 'not_assessed', context })
    const lookup = formula.lookup || {}
    const key = normalCategory(raw)
    const item = has(lookup, key) ? lookup[key] : (formula.default || {})
    const rating = get(item, 'rating')
    return done({ rating, combined: truthy(item.label) ? item.label : raw, completeness: truthy(rating) ? 'complete' : 'context_only', context })
  }

  if (COMPOSITE_OPERATORS.has(operator)) {
    const allowPartial = truthy((m.formula || {}).allowPartial)
    const rated: [number, string, string][] = []
    const inputRatings: Record<string, string> = {}
    for (const inp of required) {
      const value = num(values[inp.key])
      const [rating, idx] = scoreQuantity(value, ruleForInput(m, inp, context), strataOf(context))
      if (rating) {
        rated.push([Number(idx), inp.key, rating])
        inputRatings[inp.key] = rating
      }
    }
    if (!rated.length || (rated.length < required.length && !allowPartial)) {
      return done({ rating: null, completeness: 'not_assessed', inputRatings, context })
    }
    let idx: number
    let governing: string | null
    let rating: string
    if (operator === 'mean_index') {
      idx = pyRound(pySum(rated.map((r) => r[0])) / rated.length, 12)
      governing = null
      rating = indexRating(idx)
    } else {
      // min/max return the first extreme in input order
      let pick = rated[0]
      for (const r of rated.slice(1)) {
        if (operator === 'worst_index' ? r[0] < pick[0] : r[0] > pick[0]) pick = r
      }
      ;[idx, governing, rating] = pick
    }
    const completeness = rated.length === required.length ? 'complete' : 'partial'
    return done({ rating, combined: idx, governing, completeness, inputRatings, context })
  }

  if (availableRequired.length < required.length) return done({ rating: null, completeness: 'not_assessed', context })

  const warnings: string[] = []
  let combined: number | null = null
  const formula = m.formula || {}
  if (operator === 'threshold') {
    const inp = (m.inputs ?? []).find((i: Obj) => !truthy(i.contextOnly))
    combined = num(values[inp.key])
  } else if (operator === 'ratio') {
    const numerator = num(values[formula.numerator])
    const denominator = num(values[formula.denominator])
    const positiveKey = (m.inputs ?? []).find((i: Obj) => truthy(i.positive))?.key ?? null
    if (numerator === null || denominator === null || denominator === 0) return done({ rating: null, completeness: 'not_assessed', context })
    if (positiveKey && (num(values[positiveKey]) || 0) <= 0) return done({ rating: null, completeness: 'not_assessed', context })
    combined = Number(pyFloatOf(get(formula, 'numeratorMultiplier', 1))) * numerator
      / (Number(pyFloatOf(get(formula, 'denominatorMultiplier', 1))) * denominator)
  } else if (operator === 'minimum') {
    const vals = required.map((i: Obj) => num(values[i.key]))
    if (vals.some((v: number | null) => v === null)) return done({ rating: null, completeness: 'not_assessed', context })
    combined = Math.min(...(vals as number[]))
  } else if (operator === 'minimum_of_products') {
    const products: Record<string, number> = {}
    for (const [name, keys] of pyEntries(get(formula, 'products', {}) as Record<string, string[]>)) {
      const vals = keys.map((k) => num(values[k]))
      if (!vals.length || vals.some((v) => v === null)) return done({ rating: null, completeness: 'not_assessed', context })
      products[name] = (vals as number[]).reduce((a, b) => a * b, 1)
    }
    if (!Object.keys(products).length) return done({ rating: null, completeness: 'not_assessed', context })
    context.products = Object.fromEntries(Object.entries(products).map(([k, v]) => [k, pyRound(v, 12)]))
    combined = Math.min(...Object.values(products))
  } else if (operator === 'sum_capped') {
    const vals = required.map((i: Obj) => num(values[i.key]))
    if (vals.some((v: number | null) => v === null)) return done({ rating: null, completeness: 'not_assessed', context })
    combined = Math.min(pySum(vals as number[]), Number(pyFloatOf(formula.cap)))
  } else if (operator === 'weighted_capped_sum') {
    let total = 0.0
    for (const term of formula.terms ?? []) {
      const value = num(values[term.input])
      if (value === null) return done({ rating: null, completeness: 'not_assessed', context })
      let transformed: number
      if (term.transform === 'cap') transformed = Math.min(value / Number(pyFloatOf(term.cap)), 1.0)
      else if (term.transform === 'offset_cap') {
        transformed = Math.max(0.0, Math.min((value - Number(pyFloatOf(get(term, 'offset', 0)))) / Number(pyFloatOf(term.cap)), 1.0))
      } else throw new Error(`unsupported transform '${term.transform}'`)
      total += Number(pyFloatOf(term.weight)) * transformed
    }
    combined = total
  }

  if (combined === null) return done({ rating: null, completeness: 'not_assessed', context })
  if (['ratio', 'minimum', 'minimum_of_products', 'sum_capped', 'weighted_capped_sum'].includes(operator)) {
    combined = pyRound(combined, 12)
  }
  const [rating] = scoreQuantity(combined, ruleForMethod(m), strataOf(context))
  const below = get(formula, 'geometryWarningBelow')
  if (below !== null) {
    const threshold = Number(pyFloatOf(below))
    if (combined < threshold) warnings.push(`Value ${pyG(combined)} is below ${pyG(threshold)}. Verify cross-section geometry.`)
  }
  return done({ rating, combined, completeness: rating ? 'complete' : 'not_assessed', warnings, context })
}

/** config.criteria_bands: the Good/Fair/Poor criteria strings of a metric, indicator-aware. */
export function criteriaBands(mid: string, indicator: string | null = null, metricCriteria: Obj | null = null): Obj {
  let m: Obj | null = null
  try {
    m = methodFor(mid)
  } catch {
    m = null
  }
  if (m !== null) {
    const auto = criteriaFor(m).automated || []
    if (auto.length) {
      const block = auto.find((a: Obj) => a.input === indicator) ?? auto[0]
      const bands = block.bands || {}
      if (Object.keys(bands).length) return Object.fromEntries(RATINGS.filter((r) => truthy(bands[r])).map((r) => [r, bands[r]]))
    }
  }
  return metricCriteria || {}
}

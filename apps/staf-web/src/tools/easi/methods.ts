// The Scoring panel's view of a catalog method (easi/methods.py): the method that produced a row's
// rating (its trace's methodKey and context) projected to what "How it's computed" shows, the inputs,
// the equation, the reference-curve provenance and a categorical method's decision table. No numbers
// of its own: everything comes from the catalog through the evaluator.
import { pyEntries } from '../../app/pyjson'
import { curveReference, equationFor, methodFor, resolvedMethod, strataOf } from './evaluator'

type Obj = Record<string, any>

export interface MethodInput { key: string, label: string, unit: string, symbol: string, sourceLabel: string, contextOnly: boolean, integer: boolean }
export interface ScoringMethod {
  metricId: string
  mode: string
  inputs: MethodInput[]
  equation: string
  decisions: { label: string, rating: string }[]
  reference: string
  methodKey: string
  title: string
}

/** _mode: the render mode from the operator that scores the metric (None for unscored). */
function mode(m: Obj): string | null {
  const op = m.operator
  if (op === 'unscored') return null
  if (op === 'categorical_lookup') return 'categorical'
  if (op === 'worst_index') return 'worst'
  if (op === 'best_index') return 'best'
  if (op === 'mean_index') return 'mean'
  if (op === 'threshold') {
    const inp = (m.inputs ?? []).find((i: Obj) => !i.contextOnly) ?? {}
    return inp.valueType === 'integer' ? 'count' : 'scalar'
  }
  return 'combined'
}

/** _project (the fields the panel reads). */
function project(m: Obj, context: Obj | null): ScoringMethod | null {
  const md = mode(m)
  if (md === null) return null
  const inputs: MethodInput[] = (m.inputs ?? []).map((i: Obj) => ({
    key: i.key, label: i.label ?? i.key, unit: i.units ?? '', symbol: i.symbol ?? '', sourceLabel: i.sourceField ?? '',
    contextOnly: Boolean(i.contextOnly), integer: i.valueType === 'integer',
  }))
  const decisions = md === 'categorical'
    ? pyEntries<Obj>((m.formula || {}).lookup || {}).filter(([, item]) => item.rating).map(([key, item]) => ({ label: item.label ?? key, rating: item.rating }))
    : []
  const specs = [m, ...(m.inputs ?? [])].filter((x: Obj) => x.curve).map((x: Obj) => x.curve)
  const refs: string[] = []
  for (const spec of specs) {
    const r = curveReference(spec, strataOf(context))
    if (!refs.includes(r)) refs.push(r)
  }
  return { metricId: m.metricId, mode: md, inputs, equation: equationFor(m), decisions, reference: refs.join('; '), methodKey: m.methodKey, title: m.title ?? '' }
}

const needsContext = (m: Obj): boolean => Boolean(m.curve || (m.inputs ?? []).some((i: Obj) => i.regionalBands || i.curve))

/** resolve: the method a row's trace names (its methodKey and context), else the catalog's primary one. */
export function resolve(mid: string, model: string | null | undefined, context: Obj | null | undefined): ScoringMethod | null {
  let parent: Obj
  try {
    parent = methodFor(mid)
  } catch {
    return null
  }
  const keys = [parent.methodKey, ...(parent.variants ?? []).map((v: Obj) => v.methodKey)]
    .filter((k: string) => project(resolvedMethod(parent, k), null) !== null)
  const known = Boolean(model) && keys.includes(model as string)
  const resolved = known ? resolvedMethod(parent, model) : parent
  if (context && Object.keys(context).length && needsContext(resolved)) return project(resolved, context)
  if (known) return project(resolvedMethod(parent, model), null)
  return project(parent, null)
}

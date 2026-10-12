// The SFARI assessment file (sfari/session.py, comid_anchor.saved_anchor and saved_point): the header's
// Save and Open.
//
// * ``delineation``: the whole delineate result (with ``siteAnchor``, ``siteEngine`` and
//   ``watershedBasis`` since 2026-09; earlier files lack them).
// * ``toolData``: what every scenario shares, the pulled desktop ``evidence`` and the ``cross_section``
//   geometry of the hydraulics tool.
// * ``scenarios``: each scenario's entries (``metric_scores``, ``function_scores``).
//
// Files from before the shared format (``schemaVersion`` 1) still open.
import { dump as dumpFile, legacyScenarios, parse } from './file'

type Obj = Record<string, any>
export const TOOL = 'SFARI'
const isObj = (v: unknown): v is Obj => v !== null && typeof v === 'object' && !Array.isArray(v)

/** dump: ``scenarios`` is scenariosBlock's (every scenario's metric and function scores). */
export function dump(delineation: Obj | null, evidence: Obj | null, crossSection: unknown, scenarios: Obj): string {
  return dumpFile(TOOL, delineation ?? {}, { evidence: evidence ?? {}, cross_section: crossSection ?? null }, scenarios)
}

/** _legacy: a file from before the shared format, in its shape. */
function legacy(d: Obj): Obj {
  const entries = { metric_scores: d.metric_scores || {}, function_scores: d.function_scores || {} }
  return {
    delineation: 'delineation' in d ? d.delineation : {},
    toolData: { evidence: d.evidence || {}, cross_section: d.cross_section ?? null },
    scenarios: legacyScenarios(d.scenarios ?? null, entries),
  }
}

/** _without_function_na: function-level N/A was removed (only metrics can be N/A). */
function withoutFunctionNa(block: Obj | null): Obj | null {
  for (const item of (block ?? {}).items ?? []) {
    const state = isObj(item) ? item.state : null
    const scores = isObj(state) ? state.function_scores : null
    if (isObj(scores)) for (const rec of Object.values(scores)) if (isObj(rec)) delete rec.na
  }
  return block
}

export interface Loaded { delineation: Obj, evidence: Obj, cross_section: unknown, scenarios: Obj | null }

/** load: a saved file (missing blocks read empty). Throws AssessmentFileError. */
export function load(text: string): Loaded {
  const st = parse(text, TOOL, legacy)
  const data = st.toolData
  return {
    delineation: st.delineation,
    evidence: isObj(data.evidence) ? data.evidence : {},
    cross_section: data.cross_section ?? null,
    scenarios: withoutFunctionNa(st.scenarios),
  }
}

// ---------------------------------------------------------------- comid_anchor

/** comid: the COMID that keys the StreamCat values. */
export function comid(anchor: unknown): number | null {
  if (!isObj(anchor) || !isObj(anchor.scoredReach)) return null
  const c = anchor.scoredReach.comid
  if (c === null || c === undefined || typeof c === 'boolean') return null
  if (typeof c === 'number') return Number.isInteger(c) && c > 0 ? c : null
  // int(c), kept only when the text is the int's own (str(c).strip() == str(int(c)))
  const text = String(c).trim()
  return /^[1-9]\d*$/.test(text) ? Number(text) : null
}

/** synthetic: a minimal covered-reach anchor for a session that carries a COMID but no classification. */
export function synthetic(value: unknown): Obj | null {
  const c = comid({ scoredReach: { comid: value } })
  return c !== null ? { anchorKind: 'v2Direct', anchorSchemaVersion: 1, scoredReach: { network: 'nhdplus-v2', comid: c }, notes: [] } : null
}

/** saved_anchor: the source this saved site carries, legacy COMID sessions included. */
export function savedAnchor(d: unknown): Obj | null {
  const dd = isObj(d) ? d : {}
  const ci = isObj(dd.ctx_inputs) ? dd.ctx_inputs : {}
  const dl = isObj(dd.delineation) ? dd.delineation : {}
  for (const candidate of [dd.siteAnchor, ci.siteAnchor]) if (comid(candidate) !== null) return candidate
  for (const value of [ci.comid, dl.comid]) {
    const a = synthetic(value)
    if (a) return a
  }
  return null
}

const toFloat = (v: unknown): number => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN)

/** saved_point: the assessment point, never the downstream source's: ``[lat, lon, 0.0, nhdplusid]``. */
export function savedPoint(d: unknown): [number, number, number, number | null] | null {
  const dd = isObj(d) ? d : {}
  const dl = isObj(dd.delineation) ? dd.delineation : {}
  const ci = isObj(dd.ctx_inputs) ? dd.ctx_inputs : {}
  const c = (savedAnchor(dd) ?? {}).clickedStream
  const clicked = isObj(c) ? c : {}
  for (const [lat, lon, id] of [[dl.snapped_lat, dl.snapped_lon, dl.nhdplus_id], [ci.lat, ci.lon, ci.nhdplus_id],
    [clicked.snapLat, clicked.snapLon, clicked.nhdplusId]]) {
    const la = toFloat(lat)
    const lo = toFloat(lon)
    if (Number.isNaN(la) || Number.isNaN(lo)) continue
    if (la >= 24 && la <= 50 && lo >= -125 && lo <= -66) return [la, lo, 0.0, id ?? null]
  }
  return null
}

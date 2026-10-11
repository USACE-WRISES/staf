// The words SFARI puts on a StreamCat value: whose basin it describes (sfari/comid_anchor.py, its pure
// helpers over the anchor payload) and the STAF site engine's labels (engine_prefill.py).
import { pyFixed, pyFixedComma, pyFloat } from '../../app/pyfmt'
import { ENGINE_VERSION } from '../../engine/site'

export const STREAMCAT_ENGINE = 'StreamCat lookup engine'
export const STREAMCAT_URL = 'https://www.epa.gov/national-aquatic-resource-surveys/streamcat-dataset'
/** The engine has no page of its own; the STAF site documents both engines. */
export const ENGINE_URL = 'https://usace-wrises.github.io/staf/computation-engines/'

type Anchor = Record<string, any> | null | undefined

/** comid_anchor.comid: the COMID that keys the StreamCat values. */
export function comid(anchor: Anchor): number | null {
  const scored = anchor && typeof anchor === 'object' ? anchor.scoredReach : null
  if (!scored || typeof scored !== 'object') return null
  const c = scored.comid
  if (c === null || c === undefined || typeof c === 'boolean') return null
  const v = typeof c === 'number' ? c : /^\s*\d+\s*$/.test(String(c)) ? Number(String(c).trim()) : NaN
  return Number.isInteger(v) && v > 0 ? v : null
}

export const isRouted = (anchor: Anchor): boolean => anchor?.anchorKind === 'hrSurrogate'

/** comid_anchor.synthetic: a covered-reach anchor from a bare COMID. */
export function synthetic(comidValue: unknown): Record<string, any> | null {
  const value = comid({ scoredReach: { comid: comidValue } })
  return value !== null ? { anchorKind: 'v2Direct', anchorSchemaVersion: 1, scoredReach: { network: 'nhdplus-v2', comid: value }, notes: [] } : null
}

/** fmt_ratio: ``32`` past ten times, ``2.7`` below it. */
export function fmtRatio(value: unknown): string | null {
  const v = Number(value)
  if (value === null || value === undefined || typeof value === 'boolean' || !Number.isFinite(v) || v <= 0) return null
  return v >= 10 ? pyFixed(v, 0) : pyFixed(v, 1)
}

function dist(anchor: Record<string, any>): string | null {
  const d = anchor.routing?.routedDistanceFt
  const v = Number(d)
  return d === null || d === undefined || !Number.isFinite(v) ? null : `${pyFixedComma(v, 0)} ft`
}

export function reachName(anchor: Anchor): string {
  const name = anchor?.scoredReach?.gnisName
  return name && String(name).trim() ? String(name).trim() : 'an unnamed reach'
}

/** describes: the reach a StreamCat value describes on a stream outside NHDPlus V2, empty on a
 * covered reach. */
export function describes(anchor: Anchor): string {
  const c = comid(anchor)
  if (c === null || !isRouted(anchor)) return ''
  const parts = [`nearest StreamCat reach ${reachName(anchor)} (COMID ${c})`]
  const d = dist(anchor!)
  if (d) parts.push(`${d} downstream`)
  const ratio = fmtRatio(anchor!.routing?.daRatio)
  if (ratio) parts.push(`which drains ${ratio} times this stream`)
  return parts.join(', ')
}

export function sourceLabel(anchor: Anchor): string {
  const c = comid(anchor)
  if (c === null) return STREAMCAT_ENGINE
  if (!isRouted(anchor)) return `${STREAMCAT_ENGINE}, COMID ${c} (this reach)`
  return `${STREAMCAT_ENGINE}, nearest StreamCat reach ${reachName(anchor)} (COMID ${c})`
}

export function basinSuffix(anchor: Anchor): string {
  const c = comid(anchor)
  return c !== null ? ` (NHDPlus V2 basin, COMID ${c})` : ' (NHDPlus V2 basin)'
}

export function basinNote(anchor: Anchor): string {
  const c = comid(anchor)
  if (c === null) return 'Describes an NHDPlus V2 reach basin.'
  if (!isRouted(anchor)) return `Describes the NHDPlus V2 basin of COMID ${c}, the reach this point sits on, as EPA StreamCat summarized it.`
  const d = dist(anchor!)
  const ratio = fmtRatio(anchor!.routing?.daRatio)
  let txt = `Describes the NHDPlus V2 basin of ${reachName(anchor)} (COMID ${c}), the nearest StreamCat reach downstream`
  if (d) txt += `, ${d} away`
  if (ratio) txt += `, which drains ${ratio} times this stream`
  return txt + '.'
}

// ---------------------------------------------------------------- engine_prefill.py

/** naming.engine_label: ``STAF site engine v0.5.0``. */
export const engineLabel = (version?: string | null): string => `STAF site engine v${version || ENGINE_VERSION}`

export const engineSource = (record: any): string => `${engineLabel(record?.engineVersion)} (HR reach watershed)`

export function engineNote(record: any): string {
  const ws = record?.watershed ?? {}
  const parts = ['Watershed of the clicked NHD reach, HR catchments aggregated']
  if (ws.areaSqkm !== null && ws.areaSqkm !== undefined) parts.push(`${pyFloat(Number(ws.areaSqkm))} km2`)
  if (ws.areaAgreement !== null && ws.areaAgreement !== undefined) parts.push(`area agreement ${pyFloat(Number(ws.areaAgreement))}`)
  return parts.join(', ') + '. NLCD 2021.'
}

/** engine_metrics: ``{metric: value}`` of an ok record, else empty. */
export function engineMetrics(record: any): Record<string, any> {
  if (!record || record.status !== 'ok') return {}
  return Object.fromEntries(Object.entries(record.metrics ?? {}).map(([k, v]: [string, any]) => [k, v?.value ?? null]))
}

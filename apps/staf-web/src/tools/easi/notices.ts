// EASI's routed-site texts (easi/notices.py) and basin characteristics (easi/basin.py).
import { pyFixedComma, pyFixed, pyFloat, pyG } from '../../app/pyfmt'
import { pyFloatOf } from './evaluator'
import { ecoregionCrosswalk } from './geo'
import type { EasiCtx } from './adapters'

export const TITLE = 'Note'
export const BORROWED_MARK = '†'
export const BORROWED_NOTE = 'Marked desktop evidence comes from the nearest StreamCat reach downstream.'
export const OUTSIDE = 'This stream is outside the StreamCat network'

const float = (v: any): number | null => (v === null || v === undefined || typeof v === 'boolean' ? null : pyFloatOf(v))

/** basin.fmt_km2: ``12.35 km²``, ``unknown`` from None or junk. */
export function fmtKm2(value: any): string {
  const f = float(value)
  return f === null ? 'unknown' : `${pyFixedComma(f, 2)} km²`
}

/** basin.fmt_ft */
export function fmtFt(value: any): string {
  const f = float(value)
  return f === null ? 'unknown' : `${pyFixedComma(f, 0)} ft`
}

/** basin.fmt_ratio: ``32`` at or above ten, ``2.7`` below, null from None, junk or a non-positive value. */
export function fmtRatio(value: any): string | null {
  const f = float(value)
  if (f === null || Number.isNaN(f) || f <= 0) return null
  return f >= 10 ? pyFixed(f, 0) : pyFixed(f, 1)
}

/** notices.is_borrowed: the displayed result uses downstream evidence. */
export function isBorrowed(row: Record<string, any> | null | undefined): boolean {
  const r = row || {}
  return Boolean(r.anchorNote && ['ok', 'observed', 'computed'].includes(r.status) && ['Good', 'Fair', 'Poor'].includes(r.rating) && r.valueText)
}

function limit(value: any): string {
  const f = float(value)
  if (f === null) return '10'
  return Number.isInteger(f) ? pyFixed(f, 0) : pyG(f)
}

const ft = (value: any): string | null => {
  const f = float(value)
  return f === null ? null : `${pyFixedComma(f, 0)} ft`
}

/** notices.routed_notice: ``{title, lines}`` for a routed site, null on the covered network. */
export function routedNotice(anchor: any, delineation: any): { title: string, lines: string[] } | null {
  const a = anchor || {}
  if (a.anchorKind !== 'hrSurrogate') return null
  const d = delineation || {}
  const r = a.routing || {}
  const source = d.watershed_source || ''
  const eng = d.watershed_engine || {}
  const dist = ft(r.routedDistanceFt)
  const distTxt = dist ? `${dist} downstream` : 'downstream'
  let first: string
  if (source === 'site-engine') {
    first = `${OUTSIDE}. Watershed metrics use the HR reach watershed (${fmtKm2(eng.areaSqkm)}) from the STAF site engine.`
  } else if (source === 'not-calculated') {
    first = `${OUTSIDE}, and the STAF site engine could not calculate its watershed (${eng.reason || 'not calculated'}). Watershed metrics are unavailable.`
  } else {
    const reach = d.gnis_name || 'the nearest StreamCat reach'
    const ratio = fmtRatio(r.daRatio)
    const lim = limit(r.daRatioLimit || 10)
    const tail = ratio ? ` (drainage area ratio ${ratio}, limit ${lim})` : ''
    return { title: TITLE, lines: [`${OUTSIDE}. Results describe ${reach}, ${distTxt}, not the clicked stream${tail}.`] }
  }
  return { title: TITLE, lines: [first] }
}

/** notices.marker_note */
export const markerNote = (anchor: any): string => ((anchor || {}).anchorKind !== 'hrSurrogate' ? '' : BORROWED_NOTE)

/** notices.borrowed_note: the sentence for a COMID-keyed row on a routed site. */
export function borrowedNote(anchor: any): string {
  const a = anchor || {}
  if (a.anchorKind !== 'hrSurrogate') return ''
  const scored = a.scoredReach || {}
  const r = a.routing || {}
  const name = scored.gnisName
  const comid = scored.comid
  let reach: string
  if (name && comid !== null && comid !== undefined) reach = `the nearest StreamCat reach, ${name} (COMID ${comid})`
  else if (comid !== null && comid !== undefined) reach = `the nearest StreamCat reach (COMID ${comid})`
  else if (name) reach = `the nearest StreamCat reach, ${name}`
  else reach = 'the nearest StreamCat reach'
  const parts = [`Scored from ${reach}`]
  const dist = ft(r.routedDistanceFt)
  if (dist) parts.push(`${dist} downstream`)
  const ratio = fmtRatio(r.daRatio)
  if (ratio) parts.push(`which drains ${ratio} times this stream`)
  return parts.join(', ') + '.'
}

/** basin.basin_characteristics: the ordered ``{rows: [[label, value], ...]}`` of the report's basin block. */
export function basinCharacteristics(ctx: EasiCtx): { rows: [string, string][] } {
  const extras = ctx.extras || {}
  const sc = extras.streamcat || {}
  const rows: [string, string][] = []
  const anchor = extras.siteAnchor || {}
  const routed = anchor.anchorKind === 'hrSurrogate'
  const layer = extras.watershed || {}
  if (routed) {
    const provider = layer.provider
    if (provider === 'site-engine') rows.push(['Watershed engine', String(layer.label || 'STAF site engine')])
    else if (provider === null || provider === undefined) rows.push(['Watershed engine', `unavailable (${layer.unavailableReason || 'not calculated'})`])
    else rows.push(['Watershed engine', 'StreamCat lookup engine (nearest covered reach)'])
  }
  if (ctx.drainage_area_sqkm !== null && ctx.drainage_area_sqkm !== undefined) rows.push(['Drainage area', fmtKm2(ctx.drainage_area_sqkm)])
  const slope = ctx.slope
  if (slope !== null && slope !== undefined) rows.push(['Channel slope', `${pyFixed(slope, 4)} m/m (${pyFixed(slope * 100, 2)}%)`])
  if (ctx.stream_order !== null && ctx.stream_order !== undefined) rows.push(['Stream order', String(ctx.stream_order)])
  if (ctx.sinuosity !== null && ctx.sinuosity !== undefined) rows.push(['Sinuosity', pyFloat(ctx.sinuosity)])
  let eco: { code: any, name: any } | null = null
  try {
    const code = (extras.strata || {}).l3 ?? null
    const entry = (code !== null ? (ecoregionCrosswalk().l3 ?? {})[code] : null) || {}
    eco = { code, name: entry.name ?? null }
  } catch {
    eco = null
  }
  if (eco && eco.name) rows.push(['EPA ecoregion (Level III)', eco.code ? `${eco.name} (${eco.code})` : eco.name])
  const suffix = routed ? ' (nearest covered reach)' : ''
  const elev = sc.elevws
  if (elev !== null && elev !== undefined) rows.push(['Mean basin elevation' + suffix, `${pyFixed(elev, 0)} m`])
  const precip = sc.precip8110ws
  if (precip !== null && precip !== undefined) rows.push(['Mean annual precipitation' + suffix, `${pyFixed(precip, 0)} mm`])
  return { rows }
}

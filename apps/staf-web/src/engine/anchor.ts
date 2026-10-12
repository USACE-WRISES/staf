// Which NHDPlus V2 reach (COMID) answers a site for the StreamCat lookup engine: the anchor payload
// each Shiny tool keeps for a pick (site_engine anchor.py classify_click, v2_anchor, route_from_hr;
// EASI's routing.py route_from_hr under its auto policy).
//
// A V2 line within 150 ft of the click is the reach itself ("v2Direct"). Otherwise the click's HR
// stream is routed: a raindrop from its HR snap point reaches the covered reach whose catchment holds
// it (the bundle's answer, bundle.ts raindrop), recorded with the routed distance and the
// drainage-area ratio ("hrSurrogate"). Key order and rounding follow the Python, so a saved file
// carries the same payload.
import { pyFixed, pyFloat } from '../app/pyfmt'
import { floatKeys } from '../app/pyjson'
import type { DataSource } from '../data/source'
import { FT_PER_M } from '../streams/display'
import { albersForward } from '../xs/projExact'
import { type FlowlineRecord, raindrop } from './bundle'
import type { Hit } from './snap'

export const ANCHOR_SCHEMA_VERSION = 1
export const DA_RATIO_MAX = 10.0
export const SNAP_TOL_FT = 150.0
export const ROUTING_METHOD = 'nldi-hydrolocation-raindrop'
const ROUTED_NOTE = 'COMID-keyed evidence describes the nearest downstream reach of the covered network.'
export const DECLINE_DA_UNAVAILABLE = 'surrogate_da_unavailable'
export const DECLINE_DA_RATIO = 'surrogate_da_ratio_exceeded'

export type Anchor = Record<string, any>
export type AnchorResult = { anchor: Anchor } | { error: string, detail?: string }

/** round(x, n), as Python rounds. */
export function pyRound(x: number, n: number): number {
  return Number(pyFixed(x, n))
}

/** Straight-line distance in feet (EPSG:5070), rounded to 0.1 ft (anchor.distance_ft). */
export function distanceFt(lat1: number, lon1: number, lat2: number, lon2: number): number | null {
  const [x1, y1] = albersForward(lon1, lat1)
  const [x2, y2] = albersForward(lon2, lat2)
  const dx = x1 - x2
  const dy = y1 - y2
  const d = Math.sqrt(dx * dx + dy * dy) * FT_PER_M
  return Number.isFinite(d) ? pyRound(d, 1) : null
}

function payload(kind: string, clickedLat: number, clickedLon: number,
  extra: { clickedStream?: Anchor, scoredReach?: Anchor, routing?: Anchor, notes?: string[] }): Anchor {
  const p: Anchor = {
    anchorSchemaVersion: ANCHOR_SCHEMA_VERSION,
    anchorKind: kind,
    clickedPoint: { lat: pyRound(clickedLat, 6), lon: pyRound(clickedLon, 6) },
    scoredReach: extra.scoredReach ?? {},
    notes: extra.notes ?? [],
  }
  if (extra.clickedStream) p.clickedStream = extra.clickedStream
  if (extra.routing) p.routing = extra.routing
  return p
}

/** The covered-network anchor (the point is on a V2 flowline). */
export function v2Anchor(comid: number, clickedLat: number, clickedLon: number, snapLat: number | null = null,
  snapLon: number | null = null, snapDistFt: number | null = null): Anchor {
  const scored = { network: 'nhdplus-v2', comid, gnisName: null, drainageAreaSqkm: null, snapLat, snapLon, snapDistFt }
  return payload('v2Direct', clickedLat, clickedLon, { scoredReach: scored })
}

function clickedStreamOf(hr: Hit, rec: FlowlineRecord | null): Anchor {
  const [hrLat, hrLon, hrDistFt, nhdplusid] = hr
  const out: Anchor = {
    network: 'nhdplus-hr', nhdplusId: nhdplusid,
    gnisName: null, reachcode: null, drainageAreaSqkm: null,
    slope: null, fcode: null, streamOrder: null, vpuid: null,
    snapLat: hrLat, snapLon: hrLon, snapDistFt: pyRound(hrDistFt, 1),
  }
  if (rec) {
    Object.assign(out, {
      gnisName: rec.gnis_name, reachcode: rec.reachcode, drainageAreaSqkm: rec.totdasqkm, slope: rec.slope,
      fcode: rec.fcode, streamOrder: rec.stream_order, vpuid: rec.vpuid,
    })
  }
  return out
}

/** Route an HR-only stream click to the covered network. ``policy``: "engine" (SFARI, DEEP:
 * anchor.route_from_hr records a declined substitution) or "easi" (EASI's routing.route_from_hr under
 * the auto policy: the ratio is provenance, never a gate). ``hrRecord`` is the clicked HR flowline. */
export async function routeFromHr(src: DataSource, clickedLat: number, clickedLon: number, hr: Hit,
  hrRecord: FlowlineRecord | null, policy: 'engine' | 'easi'): Promise<AnchorResult> {
  const [hrLat, hrLon, , nhdplusid] = hr
  if (nhdplusid === null) return { error: 'no_stream_found' }
  const clickedStream = clickedStreamOf(hr, hrRecord)
  let snap
  try {
    snap = await raindrop(src, hrLat, hrLon)
  } catch (err) {
    return { error: 'snap_service_error', detail: String(err).slice(0, 500) }
  }
  if (!snap) return { error: 'no_stream_found' }
  const reach = snap.reach
  const scored: Anchor = {
    network: 'nhdplus-v2', comid: snap.comid,
    gnisName: reach.gnis_name, drainageAreaSqkm: reach.totdasqkm,
    snapLat: snap.snap_lat, snapLon: snap.snap_lon, snapDistFt: null,
  }
  const routedFt = distanceFt(hrLat, hrLon, snap.snap_lat, snap.snap_lon)
  const clickedDa = clickedStream.drainageAreaSqkm
  const surrogateDa = scored.drainageAreaSqkm
  const daRatio = clickedDa && surrogateDa ? pyRound(surrogateDa / clickedDa, 2) : null
  const routing: Anchor = floatKeys({ method: snap.method || ROUTING_METHOD, routedDistanceFt: routedFt, daRatio,
                            daRatioLimit: DA_RATIO_MAX, declined: false }, 'routedDistanceFt', 'daRatio', 'daRatioLimit')
  const anchor = payload('hrSurrogate', clickedLat, clickedLon, { clickedStream, scoredReach: scored, routing, notes: [ROUTED_NOTE] })
  if (policy === 'easi') return { anchor }
  const limit = Number.isInteger(DA_RATIO_MAX) ? String(DA_RATIO_MAX) : pyFloat(DA_RATIO_MAX)
  if (daRatio === null) {
    Object.assign(routing, {
      declined: true, declineCode: DECLINE_DA_UNAVAILABLE,
      declineMessage: 'Drainage area is unavailable for the clicked stream or the nearest covered reach, so the '
        + 'substitution limit cannot be checked. COMID-keyed evidence is unavailable here.',
    })
  } else if (daRatio > DA_RATIO_MAX) {
    Object.assign(routing, {
      declined: true, declineCode: DECLINE_DA_RATIO,
      declineMessage: `The nearest covered reach drains ${pyFloat(daRatio)} times the area of the clicked stream (limit ${limit}). `
        + 'COMID-keyed evidence is unavailable here.',
    })
  }
  return { anchor }
}

/** The engine's click rule (anchor.classify_click): a V2 line within the tolerance is the reach;
 * else the HR line within it is routed; else there is no stream. */
export async function classifyClick(src: DataSource, lat: number, lon: number, v2Hit: Hit | null, hrHit: Hit | null,
  hrRecord: FlowlineRecord | null): Promise<AnchorResult> {
  if (v2Hit && v2Hit[3] !== null && v2Hit[2] <= SNAP_TOL_FT) {
    return { anchor: v2Anchor(v2Hit[3], lat, lon, v2Hit[0], v2Hit[1], pyRound(v2Hit[2], 1)) }
  }
  if (hrHit && hrHit[3] !== null && hrHit[2] <= SNAP_TOL_FT) return routeFromHr(src, lat, lon, hrHit, hrRecord, 'engine')
  return { error: 'no_stream_found' }
}

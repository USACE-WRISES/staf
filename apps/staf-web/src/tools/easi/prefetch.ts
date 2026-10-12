// What EASI's assessment reads before scoring (assessment.assess's prefetch and the adapters' point
// lookups), all from the STAF data bundle as the Shiny EASI reads it there: StreamCat's row by COMID,
// the HUC12 and ATTAINS units of the flowline at the point (bundle.point_extras), the WQP summaries,
// the NAS taxa of the HUC, the NID dams within a mile, the strata and the regional bankfull. The
// cross-sections come from the published grid (the Shiny EASI samples live 3DEP; see xsreach.ts).
// Where the Shiny EASI falls back to a federal service (NLCD, WBD, ATTAINS, WQP, NAS, NID, StreamCat's
// API) because the bundle cannot answer, the static app has no answer: the evidence is left empty and
// the metric says it is unavailable.
import { timed } from '../../app/perf'
import { pyFixed } from '../../app/pyfmt'
import { readColumns } from '../../data/parquet'
import { bankfullGeometry } from '../../engine/bieger'
import type { Dataset } from '../../engine/dataset'
import type { PointTables } from '../../engine/points'
import { deriveReachHr, lineMerge, projectOnto } from '../../engine/reach'
import { type EngineData, ENGINE_VERSION } from '../../engine/site'
import { nearestPointOnLines } from '../../engine/snap'
import { streamcatRow } from '../../engine/streamcat'
import { gridReachGeometry, type PickedSection } from '../../engine/xsreach'
import { hrFeatures } from '../../streams/network'
import { type EasiCtx, STREAMCAT_NAMES, STREAMCAT_OTHER_NAMES } from './adapters'
import { nars9Name, strataAt } from './geo'
import { build as buildLayer } from './watershed'

export interface EasiAssessData extends EngineData {
  v2: Dataset
  points: PointTables
}

const POINT_TOL_FT = 150.0
const SAMPLES = [0.05, 0.5, 0.95]
const NO_DM = 65535

/** The HR flowline nearest the point within 150 ft (the box and rule of bundle.point_extras):
 * ``{nid, region vpu, row, the snapped point's place along the line (0 to 1)}``. */
async function flowlineAtPoint(d: EasiAssessData, lat: number, lon: number): Promise<{ nid: number, fraction: number } | null> {
  const half = 0.004
  const recs = await d.hrNet.inBox(lon - half, lat - half, lon + half, lat + half)
  const hit = nearestPointOnLines(hrFeatures(recs), lat, lon, 'nhdplusid')
  if (!hit || hit[3] === null || hit[2] > POINT_TOL_FT) return null
  const nid = Number(hit[3])
  const rec = recs.find((r) => r.id === nid)
  if (!rec) return null
  const parts = rec.parts.length > 1 ? lineMerge(rec.parts) : rec.parts
  // shapely's project on the (merged) line in degrees, normalized
  let length = 0
  for (const p of parts) for (let i = 1; i < p.length; i++) length += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
  let fraction = 0.5
  if (length) {
    if (parts.length === 1) fraction = projectOnto(parts[0], hit[1], hit[0]) / length
    else {
      // a line that stays in parts: the distance along the parts in order to the nearest one
      let best = Infinity
      let along = 0
      let offset = 0
      for (const p of parts) {
        let plen = 0
        for (let i = 1; i < p.length; i++) plen += Math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
        const at = projectOnto(p, hit[1], hit[0])
        let dmin = Infinity
        for (let i = 1; i < p.length; i++) dmin = Math.min(dmin, segDist(hit[1], hit[0], p[i - 1], p[i]))
        if (dmin < best) {
          best = dmin
          along = offset + at
        }
        offset += plen
      }
      fraction = along / length
    }
  }
  return { nid, fraction }
}

function segDist(px: number, py: number, a: number[], b: number[]): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const len2 = dx * dx + dy * dy
  const t = len2 ? Math.max(0, Math.min(1, ((px - a[0]) * dx + (py - a[1]) * dy) / len2)) : 0
  return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))
}

/** bundle.point_extras: the HUC12 and the ATTAINS units (at the point, nearest within 2 km) of the HR
 * flowline at the point, at its sample (5, 50 or 95 percent along it) nearest the point's place; null
 * where the bundle cannot answer. */
export async function pointExtras(d: EasiAssessData, lat: number, lon: number): Promise<Record<string, any> | null> {
  try {
    const at = await flowlineAtPoint(d, lat, lon)
    if (!at) return null
    const found = await d.hr.find('by_id', [at.nid])
    if (!found.length) return null
    const vpu = found[0].region.vpu
    const row = Number(found[0].rows[0])
    const extras = `bundle/values/extras2_${vpu}.parquet`
    const au = `bundle/values/au2_${vpu}.parquet`
    await d.src.ready()
    if (!d.src.has(extras) || !d.src.has(au)) return null
    let k = 0
    for (let i = 1; i < SAMPLES.length; i++) if (Math.abs(at.fraction - SAMPLES[i]) < Math.abs(at.fraction - SAMPLES[k])) k = i
    const tag = ['05', '50', '95'][k]
    const [t, units] = await Promise.all([
      readColumns(d.src, extras, [`au_exact_${tag}`, `au_near_${tag}`, `au_near_dm_${tag}`, `huc12_${tag}`, 'sinuosity_milli'], row, row + 1,
        {}, [], true),
      readColumns(d.src, au, ['layer', 'assessment_unit', 'assessment_name', 'ircategory', 'overallstatus', 'isimpaired']),
    ])
    const unit = (index: any, distance: number | null, match: string) => {
      if (index === null || index === undefined || Number(index) < 0) return {}
      const i = Number(index)
      const v = (c: string) => {
        const x = units[c][i]
        return x === undefined ? null : typeof x === 'bigint' ? Number(x) : x
      }
      return { assessment_unit: v('assessment_unit'), assessment_name: v('assessment_name'), overallstatus: v('overallstatus'),
        isimpaired: v('isimpaired'), ircategory: v('ircategory'), distance_m: distance, match_type: match, source_layer: v('layer') }
    }
    const dm = t[`au_near_dm_${tag}`][0]
    const nearM = dm === null || dm === undefined || Number(dm) === NO_DM ? null : Number(dm) / 10.0
    const huc = t[`huc12_${tag}`][0]
    const milli = t.sinuosity_milli[0]
    return {
      nhdplusid: at.nid, sample: tag,
      sinuosity: milli === null || milli === undefined || Number(milli) === 0 ? null : Number(pyFixed(Number(milli) / 1000.0, 3)),
      huc12: huc ? String(huc).padStart(12, '0') : null,
      attains_exact: unit(t[`au_exact_${tag}`][0], 0.0, 'intersect'),
      attains_nearby: unit(t[`au_near_${tag}`][0], nearM === null ? null : Number(pyFixed(nearM, 1)), 'nearby'),
    }
  } catch {
    return null
  }
}

/** The HR lines a reach's grid sections are taken from: the flowline at the reach's downstream point
 * and its upstream mainstem as reach.derive_reach walks it (a routed site's clicked stream; on the
 * covered network, the HR flowline under the StreamCat reach's snap). */
export async function xsLineIds(d: EasiAssessData, lat: number, lon: number, lengthFt: number): Promise<number[]> {
  const at = await flowlineAtPoint(d, lat, lon)
  if (!at) return []
  const rec = await d.hr.flowline('by_id', at.nid)
  if (!rec) return []
  const reach = await deriveReachHr(d.hr, rec, lat, lon, lengthFt)
  return reach.lineIds ?? [at.nid]
}

const coordsOf = (gj: any): [number, number][] => {
  const feats = gj?.features ?? []
  const g = feats.length ? feats[0].geometry : null
  return g && g.type === 'LineString' ? g.coordinates : []
}

/** Which grid section each of a reach's candidates is (to read or pull its profile), the reach line's
 * length, and whether every line under the reach is in a published region. */
export interface XsPicked { picked: PickedSection[], lengthM: number, covered: boolean }

/** The reach geometry the cross-section metrics read: the grid's sections nearest the nine reach
 * positions with their reach medians, and the Bieger fit flags (assessment.assess); with ``xs``, the
 * sections they are. */
export async function reachGeomorph(d: EasiAssessData, ctx: EasiCtx, bf: { extrapolated: boolean, fit_range_sqkm: [number, number] },
  lengthFt: number): Promise<{ geom: Record<string, any>, xs: XsPicked }> {
  const coords = coordsOf(ctx.reach_geojson)
  let geom: Record<string, any> = {}
  const xs: XsPicked = { picked: [], lengthM: 0, covered: true }
  if (coords.length >= 2) {
    const ids = await xsLineIds(d, ctx.lat, ctx.lon, lengthFt)
    const got = await gridReachGeometry(d.src, d.hr, ids, coords, bf.extrapolated)
    if (got.geom) geom = { ...got.geom }
    Object.assign(xs, { picked: got.picked, lengthM: got.lengthM, covered: got.covered })
  }
  geom.bankfull_extrapolated = Boolean(bf.extrapolated)
  geom.bankfull_fit_range_sqkm = bf.fit_range_sqkm
  return { geom, xs }
}

/** pipeline._ctx_from_inputs */
export function ctxFromInputs(ci: Record<string, any>): EasiCtx {
  const ctx: EasiCtx = {
    lat: ci.lat, lon: ci.lon, comid: ci.comid ?? null, huc8: ci.huc8 ?? null, huc12: null,
    watershed_geojson: ci.watershed_geojson ?? null, reach_geojson: ci.reach_geojson ?? null,
    drainage_area_sqkm: ci.drainage_area_sqkm ?? null, slope: ci.slope ?? null, fcode: ci.fcode ?? null,
    stream_order: ci.stream_order ?? null, sinuosity: ci.sinuosity ?? null, extras: {},
  }
  ctx.extras.erom = ci.erom ?? null
  if (ci.siteAnchor) ctx.extras.siteAnchor = ci.siteAnchor
  if (ci.watershedEngine) ctx.extras.watershedEngine = ci.watershedEngine
  return ctx
}

/** The prefetch: every input the 20 adapters read, put on ``ctx.extras`` (and ``ctx.huc12``). */
export async function prefetch(d: EasiAssessData, ctx: EasiCtx, reachLengthFt: number,
  wqpStart: [number, number, number] | null = null): Promise<void> {
  const [strata, bf] = await Promise.all([timed('easi:strata', strataAt(d.src, ctx.lat, ctx.lon, ctx.slope)),
    timed('easi:bankfull', bankfullGeometry(d.src, ctx.drainage_area_sqkm, ctx.lat, ctx.lon))])
  ctx.extras.strata = strata
  ctx.extras.nars9Name = strata.nars9 ? await nars9Name(strata.nars9) : null
  const comid = ctx.comid
  const [sc, other, extras, geom, tn, tp, temp, dams] = await Promise.all([
    comid !== null ? timed('easi:streamcat', streamcatRow(d.src, d.v2, comid, STREAMCAT_NAMES)) : null,
    comid !== null ? timed('easi:streamcat-other', streamcatRow(d.src, d.v2, comid, STREAMCAT_OTHER_NAMES)) : null,
    timed('easi:point-extras', pointExtras(d, ctx.lat, ctx.lon)),
    timed('easi:cross-sections', reachGeomorph(d, ctx, bf, reachLengthFt)),
    timed('easi:wqp-tn', d.points.wqpEasi('tn', ctx.lat, ctx.lon, 5.0, wqpStart)),
    timed('easi:wqp-tp', d.points.wqpEasi('tp', ctx.lat, ctx.lon, 5.0, wqpStart)),
    timed('easi:wqp-temp', d.points.wqpEasi('temp', ctx.lat, ctx.lon, 5.0, wqpStart)),
    timed('easi:nid', d.points.nidRadius(ctx.lat, ctx.lon, 1.0)),
  ])
  const row: Record<string, any> = {}
  for (const part of [sc, other]) for (const [k, v] of Object.entries(part || {})) row[k.toLowerCase()] = v
  ctx.extras.streamcat = row
  ctx.extras.landcover = {}
  ctx.extras.watershed = buildLayer(ctx.extras, row, ENGINE_VERSION)
  ctx.huc12 = extras && extras.huc12 ? extras.huc12 : null
  ctx.extras.reach_geomorph = geom.geom
  ctx.extras.xsPicked = geom.xs
  ctx.extras.nrsa = null
  ctx.extras.attains = { exact: extras ? { ...(extras.attains_exact || {}) } : {}, nearby: extras ? { ...(extras.attains_nearby || {}) } : {} }
  ctx.extras.wqp = { tn, tp, temp }
  ctx.extras.nidDams = dams
  ctx.extras.nasTaxa = ctx.huc12 || ctx.huc8 ? await timed('easi:nas', d.points.nasEstablished(ctx.huc12, ctx.huc8)) : null
}

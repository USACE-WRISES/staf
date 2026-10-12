// What Delineate returns in each tool, key for key: SFARI's and DEEP's delineation from the STAF site
// engine's record (pipeline.delineate_from_engine), and EASI's (pipeline.delineate_only): a covered
// reach's NHDPlus V2 basin and reach (delineation.run_delineation), or for a routed site the clicked HR
// stream's reach (routing.reanchor_inputs) and its HR reach watershed from the engine
// (watershed.compute_reach_watershed). Also the map's frame for them (delineation.geojson_bounds).
import { pyFixed } from '../app/pyfmt'
import { readColumns } from '../data/parquet'
import { albersForward } from '../xs/projExact'
import type { FlowlineRecord } from './bundle'
import type { Anchor } from './anchor'
import type { Dataset } from './dataset'
import { deriveReachHr, deriveReachV2, geoSeries, type GeoSeriesJson, type LineGeometry } from './reach'
import { computeSite, type EngineData, ENGINE_VERSION, INTERACTIVE_CONFIG, lineSinuosity, type Progress, reachLineIds, type SiteRecord } from './site'

export const DEFAULT_REACH_FT = 1000.0
export const BASIS_SITE_ENGINE = 'site-engine'
export const WATERSHED_V2_BASIN = 'nhdplus-v2-basin'
export const WATERSHED_SITE_ENGINE = 'site-engine'
export const WATERSHED_NOT_CALCULATED = 'not-calculated'
/** EASI's families for a routed site's engine run (watershed.ENGINE_FAMILIES). */
export const EASI_ENGINE_FAMILIES = ['dams', 'landcover', 'roads', 'runoff', 'soils']
export const GUIDANCE_UNAVAILABLE = 'Watershed evidence is unavailable for this stream. The HR reach watershed '
  + 'could not be calculated ({reason}). Use SFARI or DEEP for this site, or enter a rating override.'
const EROM_PROPERTIES = ['qe_ma', ...Array.from({ length: 12 }, (_, i) => `qe_${String(i + 1).padStart(2, '0')}`)]

export type Result = Record<string, any>

/** delineation.geojson_bounds: the collections' bounds as [[S, W], [N, E]], padded 6%. */
export function geojsonBounds(...fcs: (GeoSeriesJson | null | undefined)[]): [[number, number], [number, number]] | null {
  const boxes: number[][] = []
  for (const fc of fcs) {
    if (!fc?.features?.length) continue
    const xs: number[] = []
    const ys: number[] = []
    const walk = (c: any) => (typeof c[0] === 'number' ? (xs.push(c[0]), ys.push(c[1])) : c.forEach(walk))
    for (const f of fc.features) if (f.geometry?.coordinates?.length) walk(f.geometry.coordinates)
    if (xs.length) boxes.push([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)])
  }
  if (!boxes.length) return null
  const minx = Math.min(...boxes.map((b) => b[0]))
  const miny = Math.min(...boxes.map((b) => b[1]))
  const maxx = Math.max(...boxes.map((b) => b[2]))
  const maxy = Math.max(...boxes.map((b) => b[3]))
  const dx = (maxx - minx) * 0.06 || 0.001
  const dy = (maxy - miny) * 0.06 || 0.001
  return [[miny - dy, minx - dx], [maxy + dy, maxx + dx]]
}

/** The engine record without its polygon and reach geometry (engine_prefill.strip_geometry). */
export function stripGeometry(record: SiteRecord): Result {
  const { anchor: _anchor, ...out } = record as SiteRecord & Result
  if (out.watershed) out.watershed = { ...out.watershed, polygon: null }
  if (out.reach) out.reach = { ...out.reach, geometry: null }
  // the reach's lines go with the copy (the cross-section grid reads them)
  const ids = reachLineIds.get(record)
  if (ids) reachLineIds.set(out, ids)
  return out
}

/** SFARI's and DEEP's delineation result from an engine record of status ok. */
export function delineateFromEngine(record: SiteRecord, lat: number, lon: number, reachLengthFt = DEFAULT_REACH_FT): Result {
  const site = record.site ?? {}
  const ws = record.watershed
  const reach = record.reach
  const reachcode: string = site.reachcode || ''
  const warnings = [...(ws?.warnings ?? []), ...(reach?.warnings ?? [])]
  const stripped = stripGeometry(record)
  const huc8 = reachcode ? reachcode.slice(0, 8) : null
  const da = site.drainageAreaSqkm !== null && site.drainageAreaSqkm !== undefined ? site.drainageAreaSqkm : ws?.areaSqkm ?? null
  const delin = {
    comid: site.comid ?? null, nhdplus_id: site.nhdplusId ?? null, gnis_name: site.gnisName || '(unnamed stream)',
    network: 'nhdplus-hr', huc8, huc12: null, drainage_area_sqkm: da, slope: site.slope ?? null,
    stream_order: site.streamOrder ?? null, sinuosity: site.sinuosity ?? null, fcode: site.fcode ?? null,
    snapped_lat: site.snapLat ?? null, snapped_lon: site.snapLon ?? null, watershed_area_sqkm: ws?.areaSqkm ?? null,
    reach_length_ft: reach?.lengthFt ?? null, warnings,
  }
  const ctxInputs = {
    lat: site.snapLat ?? lat, lon: site.snapLon ?? lon, comid: site.comid ?? null, huc8,
    watershed_geojson: ws?.polygon ?? null, reach_geojson: reach?.geometry ?? null, drainage_area_sqkm: da,
    slope: site.slope ?? null, fcode: site.fcode ?? null, stream_order: site.streamOrder ?? null, sinuosity: site.sinuosity ?? null,
    watershedBasis: BASIS_SITE_ENGINE, site_engine: stripped,
  }
  return {
    status: 'ok', watershedBasis: BASIS_SITE_ENGINE, siteEngine: stripped,
    input: { lat, lon, reach_length_ft: reachLengthFt }, delineation: delin,
    watershed_geojson: ws?.polygon ?? null, reach_geojson: reach?.geometry ?? null, ctx_inputs: ctxInputs,
  }
}

export const BASIS_V2_BASIN = 'nhdplus-v2-basin'
export const BASIS_SURROGATE_BASIN = 'nhdplus-v2-basin-of-surrogate'

/** SFARI's and DEEP's continuation when the engine failed (pipeline.delineate_without_watershed): no
 * polygon, the StreamCat reach's basin as the labeled basis. */
export function delineateWithoutWatershed(anchor: Anchor | null, lat: number, lon: number, hrHit: (number | null)[] | null,
  reachLengthFt: number, engine: Result | null): Result {
  const a = anchor ?? {}
  const clicked = a.clickedStream ?? {}
  const scored = a.scoredReach ?? {}
  const routed = a.anchorKind === 'hrSurrogate'
  const hr = hrHit ?? [null, null, null, null]
  const snapLat = clicked.snapLat ?? hr[0]
  const snapLon = clicked.snapLon ?? hr[1]
  const nhdplusId = clicked.nhdplusId ?? hr[3]
  const site = routed ? clicked : scored
  const reachcode = String(site.reachcode || '')
  const c = scored.comid
  const comid = typeof c === 'number' && Number.isInteger(c) && c > 0 ? c : null
  const delin = {
    comid, nhdplus_id: nhdplusId ?? null, gnis_name: site.gnisName || '(unnamed stream)',
    network: nhdplusId !== null && nhdplusId !== undefined ? 'nhdplus-hr' : 'nhdplus-v2',
    huc8: reachcode ? reachcode.slice(0, 8) : null, huc12: null, drainage_area_sqkm: site.drainageAreaSqkm ?? null,
    slope: site.slope ?? null, stream_order: site.streamOrder ?? null, sinuosity: null, fcode: site.fcode ?? null,
    snapped_lat: snapLat ?? lat, snapped_lon: snapLon ?? lon, watershed_area_sqkm: null, reach_length_ft: reachLengthFt,
    warnings: ["the STAF site engine could not compute the HR reach watershed; watershed values describe the StreamCat reach's NHDPlus V2 basin"],
  }
  const basis = routed ? BASIS_SURROGATE_BASIN : BASIS_V2_BASIN
  return {
    status: 'ok', watershedBasis: basis, siteAnchor: anchor, siteEngine: engine ? { ...engine } : null,
    input: { lat, lon, reach_length_ft: reachLengthFt }, delineation: delin, watershed_geojson: null, reach_geojson: null,
    ctx_inputs: {
      lat: delin.snapped_lat, lon: delin.snapped_lon, comid, huc8: delin.huc8, watershed_geojson: null, reach_geojson: null,
      drainage_area_sqkm: delin.drainage_area_sqkm, slope: delin.slope, fcode: delin.fcode, stream_order: delin.stream_order,
      sinuosity: null, watershedBasis: basis, siteAnchor: anchor,
    },
  }
}

// ------------------------------------------------------------------ EASI

export interface EasiData extends EngineData {
  v2: Dataset
}

/** GEOS Area::ofRingSigned (the shoelace about the first vertex's x). */
function ringArea(ring: [number, number][]): number {
  const n = ring.length
  if (n < 3) return 0
  let [p1x, p1y] = ring[0]
  const x0 = p1x
  let [p2x, p2y] = ring[1]
  p2x -= x0
  let sum = 0
  for (let i = 1; i < n - 1; i++) {
    const p0y = p1y
    p1x = p2x
    p1y = p2y
    ;[p2x, p2y] = ring[i + 1]
    p2x -= x0
    sum += p1x * (p0y - p2y)
  }
  return Math.abs(sum / 2.0)
}

/** delineation._largest_polygon's area: the basin's planar area in EPSG:5070, km² (unrounded). */
export function planarAreaSqkm(geometry: { type: string, coordinates: any }): number {
  const polys: [number, number][][][] = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  let area = 0
  for (const rings of polys) {
    const projected = rings.map((r) => r.map(([x, y]) => albersForward(x, y)))
    let a = ringArea(projected[0])
    for (const hole of projected.slice(1)) a -= ringArea(hole)
    area += a
  }
  return area / 1e6
}

/** The COMID's flowline attributes (delineation.flowline_attrs from the bundle's V2 feature): name,
 * drainage area, HUC8, slope, FCODE, order, the 13 EROM flows and the line's sinuosity. */
export async function flowlineAttrsV2(d: EasiData, comid: number): Promise<{ attrs: Result, record: FlowlineRecord | null }> {
  const out: Result = { gnis_name: null, drainage_area_sqkm: null, huc8: null, slope: null, fcode: null, stream_order: null, sinuosity: null, erom: null }
  const found = await d.v2.find('by_id', [comid])
  if (!found.length) return { attrs: { ...out, _flowline_error: 'fabric: the NHDPlus V2 flowline service did not answer' }, record: null }
  const { region, rows } = found[0]
  const recs = await d.v2.records(region, [rows[0]])
  if (recs.length !== 1) return { attrs: { ...out, _flowline_error: 'fabric: the NHDPlus V2 flowline service did not answer' }, record: null }
  const rec = recs[0]
  out.gnis_name = rec.gnis_name
  out.drainage_area_sqkm = rec.totdasqkm
  out.huc8 = rec.reachcode ? rec.reachcode.slice(0, 8) : null
  out.slope = rec.slope
  out.fcode = rec.fcode
  out.stream_order = rec.stream_order
  if (region.v2attrs) {
    const t = await readColumns(d.src, 'bundle/v2/' + region.v2attrs, EROM_PROPERTIES, rows[0], rows[0] + 1, {}, [], true)
    const erom: Record<string, number> = {}
    let complete = true
    for (const k of EROM_PROPERTIES) {
      const v = t[k][0]
      if (v === null || v === undefined || !Number.isFinite(Number(v))) complete = false
      else erom[k] = Number(v)
    }
    out.erom = complete ? erom : null
  }
  const sin = rec.geometry ? lineSinuosity(rec.geometry as LineGeometry) : null
  if (sin !== null) out.sinuosity = sin
  return { attrs: out, record: rec }
}

/** EASI's delineation of a covered reach (delineation.run_delineation with the reach's COMID). */
export async function runDelineationV2(d: EasiData, lat: number, lon: number, lengthFt: number, comid: number,
  snappedLat: number | null, snappedLon: number | null, withBasin = true): Promise<Result> {
  const out: Result = {
    lat, lon, comid, gnis_name: null, drainage_area_sqkm: null, huc8: null, slope: null, fcode: null, stream_order: null,
    sinuosity: null, erom: null, snapped_lat: snappedLat ?? lat, snapped_lon: snappedLon ?? lon,
    watershed_geojson: null, watershed_area_sqkm: null, reach_geojson: null, reach_length_ft: null, warnings: [] as string[],
  }
  const { attrs } = await flowlineAttrsV2(d, comid)
  for (const k of ['gnis_name', 'drainage_area_sqkm', 'huc8', 'slope', 'fcode', 'stream_order', 'sinuosity', 'erom']) out[k] = attrs[k]
  if (attrs._flowline_error) out.warnings.push(`flowline context: ${attrs._flowline_error}`)
  if (withBasin) {
    const ws = await d.v2.watershed(comid, 50_000, 50_000)
    if (ws.status === 'ok' && ws.geometry) {
      out.watershed_geojson = geoSeries(ws.geometry)
      out.watershed_area_sqkm = planarAreaSqkm(ws.geometry)
    } else {
      out.watershed_area_sqkm = 0.0
      out.warnings.push(`watershed delineation failed: ${ws.reason ?? 'the published data cannot answer for this basin'}`)
    }
  }
  const reach = await deriveReachV2(d.v2, comid, out.snapped_lat || lat, out.snapped_lon || lon, lengthFt)
  out.reach_geojson = reach.geojson
  out.reach_length_ft = reach.lengthFt
  out.warnings.push(...reach.warnings)
  return out
}

/** routing.reanchor_inputs: a routed site's reach-scale inputs from the clicked HR stream. */
export async function reanchorInputs(d: EasiData, anchor: Anchor, lengthFt: number): Promise<Result> {
  if (anchor?.anchorKind !== 'hrSurrogate') return {}
  const clicked = anchor.clickedStream ?? {}
  const nid = clicked.nhdplusId
  const state = (anchor.reanchored ??= { applied: false, warnings: [] })
  try {
    if (!nid || clicked.snapLat === null || clicked.snapLat === undefined) {
      state.warnings.push('clicked HR reach id or snap point missing')
      return {}
    }
    const rec = await d.hr.flowline('by_id', nid)
    if (!rec) {
      state.warnings.push(`no HR flowline for nhdplusid ${nid}`)
      return {}
    }
    let sinuosity: number | null = null
    const found = await d.hr.find('by_id', [nid])
    const logical = found.length ? `bundle/values/extras2_${found[0].region.vpu}.parquet` : null
    if (logical && d.src.has(logical)) {
      const t = await readColumns(d.src, logical, ['sinuosity_milli'], found[0].rows[0], found[0].rows[0] + 1, {}, [], true)
      const raw = t.sinuosity_milli[0]
      const v = raw === null || raw === undefined ? null : Number(raw) / 1000.0
      sinuosity = v === null || v === 0 ? null : Number(pyFixed(v, 3))
    }
    if (sinuosity === null && rec.geometry) sinuosity = lineSinuosity(rec.geometry as LineGeometry)
    const out: Result = {
      lat: clicked.snapLat, lon: clicked.snapLon, slope: rec.slope, sinuosity, fcode: rec.fcode,
      stream_order: rec.stream_order, drainage_area_sqkm: rec.totdasqkm,
    }
    if (rec.reachcode) out.huc8 = rec.reachcode.slice(0, 8)
    const reach = await deriveReachHr(d.hr, rec, clicked.snapLat, clicked.snapLon, lengthFt, 'HR reach derivation failed')
    out.reach_geojson = reach.geojson
    out.reach_length_ft = reach.lengthFt
    out._warnings = [...reach.warnings]
    state.applied = true
    state.warnings = [...reach.warnings]
    return out
  } catch (err) {
    state.warnings.push(`re-anchoring failed: ${err instanceof Error ? err.message : err}`)
    return {}
  }
}

/** watershed.compute_reach_watershed: the engine at a routed site's clicked HR stream. */
export async function computeReachWatershed(d: EasiData, anchor: Anchor, progress?: Progress): Promise<Result> {
  const out: Result = {
    engine: WATERSHED_SITE_ENGINE, engineVersion: null, status: 'failed', reason: null, nReaches: null, nHops: null,
    areaSqkm: null, vaaAreaSqkm: null, areaAgreement: null, record: null, polygon: null,
  }
  const clicked = anchor?.clickedStream ?? {}
  if (clicked.snapLat == null || clicked.snapLon == null) {
    out.reason = "the clicked stream's snap point is missing"
    return out
  }
  const rec = await computeSite(d, Number(clicked.snapLat), Number(clicked.snapLon),
    { ...INTERACTIVE_CONFIG, includeGeometry: true, metricFamilies: EASI_ENGINE_FAMILIES }, progress)
  out.engineVersion = rec.engineVersion || ENGINE_VERSION
  const ws = rec.watershed
  Object.assign(out, {
    status: rec.status || 'failed', reason: rec.reason, nReaches: ws?.nReaches ?? null, nHops: ws?.nHops ?? null,
    areaSqkm: ws?.areaSqkm ?? null, vaaAreaSqkm: ws?.vaaAreaSqkm ?? null, areaAgreement: ws?.areaAgreement ?? null,
  })
  if (out.status !== 'ok') return out
  const nid = rec.site?.nhdplusId
  if (clicked.nhdplusId && nid !== clicked.nhdplusId) {
    rec.warnings.push(`engine anchored reach ${nid} differs from the clicked stream ${clicked.nhdplusId}`)
  }
  out.polygon = ws?.polygon ?? null
  out.record = stripGeometry(rec)
  return out
}

/** pipeline.delineate_only, as EASI's app calls it (the point, its COMID and the pick's anchor). */
export async function delineateOnlyEasi(d: EasiData, lat: number, lon: number, reachLengthFt: number, comid: number,
  anchor: Anchor | null, progress?: Progress): Promise<Result> {
  const routed = anchor?.anchorKind === 'hrSurrogate'
  // the routed site's V2 basin is neither drawn nor scored, so it is not walked here
  const dl = await runDelineationV2(d, lat, lon, reachLengthFt, comid, lat, lon, !routed)
  const siteAnchor: Anchor = anchor ?? {}
  const scored = (siteAnchor.scoredReach ??= {})
  if (scored.gnisName === null || scored.gnisName === undefined) scored.gnisName = dl.gnis_name
  if (scored.drainageAreaSqkm === null || scored.drainageAreaSqkm === undefined) scored.drainageAreaSqkm = dl.drainage_area_sqkm
  if (scored.snapLat === null || scored.snapLat === undefined) {
    scored.snapLat = dl.snapped_lat
    scored.snapLon = dl.snapped_lon
  }
  let reanchor: Result = {}
  if (routed) {
    reanchor = await reanchorInputs(d, siteAnchor, reachLengthFt)
    dl.warnings.push(...(reanchor._warnings ?? []))
    delete reanchor._warnings
  }
  const engineBlock = routed ? await computeReachWatershed(d, siteAnchor, progress) : null
  const ctxInputs: Result = {
    lat: dl.snapped_lat || lat, lon: dl.snapped_lon || lon, comid: dl.comid, huc8: dl.huc8,
    watershed_geojson: dl.watershed_geojson, reach_geojson: dl.reach_geojson, drainage_area_sqkm: dl.drainage_area_sqkm,
    slope: dl.slope, fcode: dl.fcode, stream_order: dl.stream_order, sinuosity: dl.sinuosity, siteAnchor,
    erom: dl.erom, watershedPolicy: 'auto',
  }
  let outReach = dl.reach_geojson
  let outReachLen = dl.reach_length_ft
  if (Object.keys(reanchor).length) {
    for (const k of ['lat', 'lon', 'slope', 'sinuosity', 'fcode', 'stream_order', 'drainage_area_sqkm', 'huc8', 'reach_geojson']) {
      if (k in reanchor) ctxInputs[k] = reanchor[k]
    }
    outReach = reanchor.reach_geojson
    outReachLen = reanchor.reach_length_ft
  }
  let outWatershed = dl.watershed_geojson
  let watershedArea = dl.watershed_area_sqkm ? Number(pyFixed(dl.watershed_area_sqkm, 2)) : null
  let watershedSource = WATERSHED_V2_BASIN
  let delineationDa = dl.drainage_area_sqkm
  let gnisName = dl.gnis_name || '(unnamed reach)'
  let engineSummary: Result | null = null
  if (engineBlock) {
    const { polygon, ...block } = engineBlock
    engineSummary = Object.fromEntries(['engine', 'engineVersion', 'status', 'reason', 'nReaches', 'nHops', 'areaSqkm',
      'vaaAreaSqkm', 'areaAgreement'].map((k) => [k, block[k] ?? null]))
    ctxInputs.watershedEngine = block
    gnisName = siteAnchor.clickedStream?.gnisName || '(unnamed stream)'
    delineationDa = ctxInputs.drainage_area_sqkm
    if (block.status === 'ok') {
      ctxInputs.watershed_geojson = polygon
      outWatershed = polygon
      watershedArea = block.areaSqkm
      watershedSource = WATERSHED_SITE_ENGINE
    } else {
      ctxInputs.watershed_geojson = null
      outWatershed = null
      watershedArea = null
      watershedSource = WATERSHED_NOT_CALCULATED
      dl.warnings.push(GUIDANCE_UNAVAILABLE.replace('{reason}', String(block.reason || block.status)))
    }
  }
  return {
    status: 'ok', siteAnchor, input: { lat, lon, reach_length_ft: reachLengthFt },
    delineation: {
      comid: dl.comid, gnis_name: gnisName, huc8: dl.huc8, huc12: null, drainage_area_sqkm: delineationDa,
      snapped_lat: dl.snapped_lat, snapped_lon: dl.snapped_lon, watershed_area_sqkm: watershedArea,
      watershed_source: watershedSource, watershed_engine: engineSummary, reach_length_ft: outReachLen, warnings: dl.warnings,
    },
    watershed_geojson: outWatershed, reach_geojson: outReach, ctx_inputs: ctxInputs,
  }
}

// The STAF site engine's record for a point (site_engine engine.compute_site), up to the metrics: the
// site (context.site_context), the HR reach watershed (delineate.delineate_watershed's bundle path and
// its area check) and the assessment reach (reach.derive_reach). Never throws: a failure is the
// record's status and reason. Progress events are the engine's (progress.py), for the busy line.
import { pyFixed } from '../app/pyfmt'
import { metadata, readColumns } from '../data/parquet'
import type { DataSource } from '../data/source'
import { hrFeatures, type Network } from '../streams/network'
import { lineLength } from '../streams/geom'
import { albersForward } from '../xs/projExact'
import type { FlowlineRecord } from './bundle'
import type { Dataset } from './dataset'
import { computeAll } from './metrics'
import { deriveReachHr, geoSeries, type GeoSeriesJson, type LineGeometry } from './reach'
import type { ValueTables } from './values'
import { nearestPointOnLines } from './snap'

export const ENGINE_ID = 'site-engine'
export const ENGINE_VERSION = '0.5.0'
export const SNAP_TOL_FT = 150.0
const PROBE_HALF_DEG = 0.012
/** Union area vs published drainage area: past this the result carries a warning. */
const AREA_AGREEMENT_WARN = 0.05

/** provenance.DEFAULT_CONFIG and the apps' INTERACTIVE_CONFIG budget. */
export const DEFAULT_CONFIG = {
  reachLengthFt: 1000.0, snapTolFt: 150.0, maxHops: 200, maxReaches: 5000, includeGeometry: true,
  metricFamilies: null as string[] | null, landcoverBaseline: false,
}
export const INTERACTIVE_CONFIG = { ...DEFAULT_CONFIG, maxReaches: 3000, maxHops: 190 }
export type EngineConfig = typeof DEFAULT_CONFIG

export interface ProgressEvent { stage: string, hops?: number, reaches?: number, family?: string, batch?: number, batches?: number }
export type Progress = (event: ProgressEvent) => void

export interface EngineData {
  src: DataSource
  hr: Dataset
  hrNet: Network
  /** the bundle's per-catchment tables (the metric families) */
  values: ValueTables
}

/** Each record's reach lines (anchor first), kept beside the record: the cross-section family reads
 * their grid sections. */
export const reachLineIds = new WeakMap<object, number[]>()

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

function notify(progress: Progress | undefined, event: ProgressEvent): void {
  try {
    progress?.(event)
  } catch {
    // the UI never breaks a run
  }
}

/** geometry.line_sinuosity: length over endpoint distance in EPSG:5070 (first part), rounded to 3. */
export function lineSinuosity(g: LineGeometry): number | null {
  const parts = g.type === 'LineString' ? [g.coordinates] : g.coordinates
  if (!parts.length || !parts[0].length) return null
  const line = parts[0].map(([x, y]) => albersForward(x, y))
  const [ax, ay] = line[0]
  const [bx, by] = line[line.length - 1]
  const straight = Math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
  return straight > 0 ? pyRound(lineLength(line) / straight, 3) : null
}

/** The reach's sinuosity from its original line, stored with the bundle's values (bundle.sinuosity). */
async function bundleSinuosity(d: EngineData, nhdplusid: number): Promise<number | null> {
  const found = await d.hr.find('by_id', [nhdplusid])
  if (!found.length) return null
  const { region, rows } = found[0]
  const logical = `bundle/values/extras2_${region.vpu}.parquet`
  await d.src.ready()
  if (!d.src.has(logical)) return null
  // the lean encoding (hrslim.lean) stores thousandths, 0 for none; the exact file the value itself
  const lean = (await metadata(d.src, logical)).schema.some((s) => s.name === 'sinuosity_milli')
  const column = lean ? 'sinuosity_milli' : 'sinuosity'
  const t = await readColumns(d.src, logical, [column], rows[0], rows[0] + 1)
  const raw = t[column][0]
  if (raw === null || raw === undefined) return null
  const v = lean ? Number(raw) / 1000.0 : Number(raw)
  return lean && v === 0 ? null : pyRound(v, 3)
}

export interface SiteContext {
  status: 'ok' | 'failed'
  reason: string | null
  anchor: FlowlineRecord | null
  site: Record<string, any> | null
}

/** Anchor the point on the HR network and assemble the site block (context.site_context). */
export async function siteContext(d: EngineData, lat: number, lon: number, snapTolFt = SNAP_TOL_FT): Promise<SiteContext> {
  const lines = await d.hrNet.inBox(lon - PROBE_HALF_DEG, lat - PROBE_HALF_DEG, lon + PROBE_HALF_DEG, lat + PROBE_HALF_DEG)
  const snap = nearestPointOnLines(hrFeatures(lines), lat, lon, 'nhdplusid')
  if (!snap) return { status: 'failed', reason: 'no HR flowline near the point', anchor: null, site: null }
  const [sLat, sLon, distFt, nid] = snap
  if (distFt > snapTolFt) {
    return {
      status: 'failed', anchor: null, site: null,
      reason: `nearest HR flowline is ${pyFixed(distFt, 0)} ft away (tolerance ${pyFixed(snapTolFt, 0)} ft)`,
    }
  }
  const hit = lines.find((l) => l.id === nid)
  const anchor = hit ? (await d.hr.records(hit.region, [hit.row]))[0] ?? null : null
  if (!anchor) return { status: 'failed', reason: 'anchored reach not resolvable', anchor: null, site: null }
  let sinuosity = await bundleSinuosity(d, anchor.nhdplusid)
  if (sinuosity === null && anchor.geometry) sinuosity = lineSinuosity(anchor.geometry as LineGeometry)
  const site = {
    network: 'nhdplus-hr',
    nhdplusId: anchor.nhdplusid,
    gnisName: anchor.gnis_name,
    reachcode: anchor.reachcode,
    vpuid: anchor.vpuid,
    snapLat: pyRound(sLat, 6), snapLon: pyRound(sLon, 6),
    snapDistFt: pyRound(distFt, 1),
    drainageAreaSqkm: anchor.totdasqkm,
    slope: anchor.slope,
    streamOrder: anchor.stream_order,
    fcode: anchor.fcode,
    sinuosity,
    eromQamaCfs: anchor.qama ?? null,
    // EPA Level III ecoregion (context.ecoregion_l3_at): its polygons are not in the published data yet
    ecoregionL3: null,
  }
  return { status: 'ok', reason: null, anchor, site }
}

/** Each watershed block's tree (the reach ids walked), kept beside the record as delineate.py keeps
 * the tree's flowlines beside it: the riparian metrics read them. */
export const treeIds = new WeakMap<object, number[]>()

export interface WatershedBlock {
  status: 'ok' | 'refused' | 'failed'
  method: string
  polygon: GeoSeriesJson | null
  areaSqkm: number | null
  vaaAreaSqkm: number | null
  areaAgreement: number | null
  nReaches: number
  nHops: number
  warnings: string[]
  reason: string | null
}

/** The HR reach watershed of a parsed reach (delineate.delineate_watershed, bundle path and _finish). */
export async function delineateWatershed(d: EngineData, anchor: FlowlineRecord, maxHops: number, maxReaches: number,
  progress?: Progress): Promise<WatershedBlock> {
  const out: WatershedBlock = {
    status: 'failed', method: 'hr-catchment-aggregation', polygon: null, areaSqkm: null,
    vaaAreaSqkm: anchor.totdasqkm, areaAgreement: null, nReaches: 0, nHops: 0, warnings: [], reason: null,
  }
  if (!anchor.nhdplusid || !anchor.hydroseq) {
    out.reason = 'anchor reach has no id or hydroseq'
    return out
  }
  const ws = await d.hr.watershed(anchor.nhdplusid, maxReaches, maxHops)
  if (ws.status === 'failed') {
    out.reason = ws.reason === 'reach not found' ? 'the published data does not hold this reach' : ws.reason ?? 'delineation failed'
    return out
  }
  out.nReaches = ws.nReaches
  out.nHops = ws.nHops
  notify(progress, { stage: 'walk', hops: out.nHops, reaches: out.nReaches })
  if (ws.status === 'refused') {
    out.status = 'refused'
    out.reason = ws.reason ?? null
    return out
  }
  notify(progress, { stage: 'union', hops: out.nHops, reaches: out.nReaches })
  out.polygon = geoSeries(ws.geometry)
  out.areaSqkm = pyRound(ws.areaSqkm!, 4)
  treeIds.set(out, ws.ids ?? [])
  // _finish: the area against the published drainage area
  const vaa = anchor.totdasqkm
  if (vaa) {
    const agreement = out.areaSqkm / vaa
    out.areaAgreement = pyRound(agreement, 4)
    if (Math.abs(agreement - 1.0) > AREA_AGREEMENT_WARN) {
      out.warnings.push(`union area disagrees with the published drainage area by ${pyFixed(Math.abs(agreement - 1.0) * 100, 1)}%`)
    }
  } else {
    out.warnings.push('published drainage area unavailable; union not validated')
  }
  out.status = 'ok'
  return out
}

export interface SiteRecord {
  engineId: string
  engineVersion: string
  status: 'ok' | 'refused' | 'failed'
  reason: string | null
  input: { lat: number, lon: number, config: Record<string, unknown> }
  site: Record<string, any> | null
  watershed: WatershedBlock | null
  reach: { lengthFt: number | null, geometry: GeoSeriesJson | null, warnings: string[] } | null
  metrics: Record<string, unknown>
  exclusions: { code: string, reason: string }[]
  warnings: string[]
  /** the anchored reach (not in the Python record: the app keeps it for the reach's attributes) */
  anchor?: FlowlineRecord | null
}

export const PERMANENT_EXCLUSIONS = [{
  code: 'epa-modeled-indices',
  reason: 'The EPA watershed/catchment integrity components (hyd, sed, chem, conn, temp, habt) and prG_BMMI exist only '
    + 'as per-COMID EPA model outputs on the NHDPlus V2 network.',
}]

/** compute_site for a point on any NHD HR stream, layer A (the metrics come with the assessment). */
export async function computeSite(d: EngineData, lat: number, lon: number, config: Partial<EngineConfig> = {},
  progress?: Progress): Promise<SiteRecord> {
  const cfg: EngineConfig = { ...DEFAULT_CONFIG }
  for (const [k, v] of Object.entries(config)) if (k in cfg && v !== null && v !== undefined) (cfg as any)[k] = v
  if (cfg.metricFamilies) cfg.metricFamilies = [...new Set(cfg.metricFamilies.map(String))].sort()
  cfg.landcoverBaseline = Boolean(cfg.landcoverBaseline)
  const record: SiteRecord = {
    engineId: ENGINE_ID, engineVersion: ENGINE_VERSION, status: 'failed', reason: null,
    input: { lat: pyRound(lat, 6), lon: pyRound(lon, 6), config: Object.fromEntries(Object.keys(cfg).sort().map((k) => [k, (cfg as any)[k]])) },
    site: null, watershed: null, reach: null, metrics: {}, exclusions: PERMANENT_EXCLUSIONS.map((e) => ({ ...e })), warnings: [],
  }
  try {
    notify(progress, { stage: 'site' })
    const ctx = await siteContext(d, lat, lon, cfg.snapTolFt)
    if (ctx.status !== 'ok' || !ctx.anchor || !ctx.site) {
      record.reason = ctx.reason
      return record
    }
    record.site = ctx.site
    record.anchor = ctx.anchor
    notify(progress, { stage: 'walk', hops: 0, reaches: 1 })
    const ws = await delineateWatershed(d, ctx.anchor, cfg.maxHops, cfg.maxReaches, progress)
    record.watershed = ws
    if (ws.status === 'refused') {
      record.status = 'refused'
      record.reason = ws.reason
      return record
    }
    if (ws.status !== 'ok') {
      record.reason = ws.reason || 'delineation failed'
      return record
    }
    notify(progress, { stage: 'reach' })
    const reach = await deriveReachHr(d.hr, ctx.anchor, ctx.site.snapLat, ctx.site.snapLon, cfg.reachLengthFt)
    record.reach = { lengthFt: reach.lengthFt, geometry: reach.geojson, warnings: reach.warnings }
    reachLineIds.set(record, reach.lineIds ?? [ctx.anchor.nhdplusid])
    // Layer B: the metric families over the watershed and the reach
    notify(progress, { stage: 'metrics' })
    record.metrics = await computeAll(d, record, cfg.metricFamilies, progress)
    if (!cfg.includeGeometry) {
      ws.polygon = null
      record.reach.geometry = null
    }
    record.status = 'ok'
    notify(progress, { stage: 'done' })
    return record
  } catch (err) {
    record.reason = `engine error: ${err instanceof Error ? err.message : err}`
    return record
  }
}

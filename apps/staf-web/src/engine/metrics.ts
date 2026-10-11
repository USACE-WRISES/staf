// Layer B of the STAF site engine: the metric families over a record's HR reach watershed and reach
// (site_engine metrics/: baseflow, dams, landcover, roads, runoff, soils; precomputed.py).
//
// Every watershed family answers from the bundle's per-catchment tables (values.ts), as the Python does
// when the bundle answered the walk; the base-flow index is the mean of the USGS grid's cells whose
// centres lie in the watershed. Entries keep the Python's names, units, sources, vintages, rounding and
// warnings; the families run in name order, so the record is the same whatever order a tool asks.
import { fromArrayBuffer } from 'geotiff'
import { pyFixed } from '../app/pyfmt'
import type { DataSource } from '../data/source'
import { albersForward } from '../xs/projExact'
import { bankfullGeometry } from './bieger'
import type { Dataset } from './dataset'
import { type Progress, reachLineIds } from './site'
import { gridReachGeometry } from './xsreach'
import { type ValueTables, watershedValues } from './values'

export interface MetricEntry {
  value: any
  unit: string
  source: string
  vintage: string
  spatialSupport: string
  warnings: string[]
}

/** provenance.VINTAGES */
export const VINTAGES = {
  nhdplusHr: 'USGS NHDPlus HR (hydro.nationalmap.gov, live service)',
  nlcd: '2021',
  nlcdBaseline: '2001',
  tigerRoads: 'current TIGERweb service',
  nid: 'current NID service',
  bfiGrid: 'USGS base-flow index grid, Wolock 2003 (1 km)',
}

const SRC_NLCD = 'NLCD, counted per HR catchment (STAF data bundle)'
const SRC_ROADS = 'TIGER/Line roads, measured per HR catchment (STAF data bundle)'
const SRC_CROSS = 'TIGER/Line roads x NHDPlus HR flowlines, per HR catchment (STAF data bundle)'
const SRC_DAMS = 'USACE NID, located per HR catchment (STAF data bundle)'
const SRC_SOILS = 'gNATSGO surface-horizon kwfact by 10 m cell, retired map units from current SSURGO, per HR catchment (STAF data bundle)'
const SRC_SOILS_FALLBACK = 'SSURGO surface-horizon kwfact'
const SRC_RUNOFF = 'NHDPlus HR EROM mean-annual flow'
const VINTAGE_RUNOFF = 'NHDPlus HR VAA (live service)'
const SRC_BFI = 'USGS base-flow index grid (Wolock 2003)'
const CFS_TO_M3YR = 0.0283168466 * 3600 * 24 * 365.25
const STEMS = ['crop', 'hayPasture', 'forest', 'shrub', 'grassland', 'woodyWetland', 'herbWetland']
const NO_COVER = "NLCD reports no land cover for this polygon (outside the product's footprint, or the service failed)"
const COVERAGE_WARN = 0.95

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

export function metricEntry(value: any, unit: string, source: string, vintage: string, spatialSupport: string,
  warnings: string[] = []): MetricEntry {
  return { value, unit, source, vintage, spatialSupport, warnings: [...warnings] }
}

export interface MetricsData {
  src: DataSource
  hr: Dataset
  values: ValueTables
}

// ------------------------------------------------------------------ the bundle's vintages

let sourcesP: Promise<Record<string, any>> | null = null

/** The bundle manifest's ``sources`` (precomputed._source). */
function bundleSources(src: DataSource): Promise<Record<string, any>> {
  sourcesP ??= src.ready().then(() => src.bytesOf('bundle/manifest.json')).then((buf) =>
    (JSON.parse(new TextDecoder().decode(buf)).sources ?? {}) as Record<string, any>)
  sourcesP.catch(() => { sourcesP = null })
  return sourcesP
}

/** precomputed.vintages */
async function precomputedVintages(src: DataSource): Promise<{ tiger: string, nid: string, soils: string }> {
  const s = await bundleSources(src)
  const day = (e: Record<string, any>) => String(e?.recorded ?? '').slice(0, 10)
  const nid = s.nid_featureserver ?? {}
  const patch = s.ssurgo_patch ?? {}
  const soils = [s.gnatsgo_tables?.vintage || 'gNATSGO']
  if (Object.keys(patch).length) soils.push(`SSURGO patch ${day(patch)}`)
  return {
    tiger: s.tiger?.vintage || 'TIGER/Line roads',
    nid: Object.keys(nid).length ? `NID FeatureServer snapshot ${day(nid)}` : 'NID snapshot',
    soils: soils.join('; '),
  }
}

// ------------------------------------------------------------------ the families

type Record_ = Record<string, any>
type Family = (d: MetricsData, record: Record_, pre: Record_ | null) => Promise<Record<string, MetricEntry>>

function watershedPolygon(record: Record_): any | null {
  const fc = record.watershed?.polygon
  return fc?.features?.length ? fc.features[0].geometry : null
}

const landcover: Family = async (_d, record, pre) => {
  if (!pre) {
    return { landcoverUnavailable: metricEntry(null, '', 'NLCD', VINTAGES.nlcd, 'pointWatershed', ['the published data cannot answer for this watershed']) }
  }
  const out: Record<string, MetricEntry> = {}
  const baseline = !!record.input?.config?.landcoverBaseline
  for (const [label, support] of [['Watershed', 'pointWatershed'], ['Riparian', 'riparianBuffer']]) {
    if (pre[`landcover${label}Unavailable`]) {
      out[`landcover${label}Unavailable`] = metricEntry(null, '', 'NLCD', VINTAGES.nlcd, support, [NO_COVER])
      continue
    }
    const covered = pre[`coveredFraction${label}`]
    const warnings = covered === undefined || covered === null ? []
      : [`NLCD covers ${pyFixed(covered * 100, 0)} percent of this polygon; the percentages describe the covered part`]
    for (const key of ['imperviousPct', ...STEMS.map((s) => `${s}Pct`)]) {
      out[`${key}${label}`] = metricEntry(pre[`${key}${label}`] ?? null, 'percent', SRC_NLCD, VINTAGES.nlcd, support, warnings)
    }
    if (baseline) {
      const base = pre[`imperviousPct2001${label}`] ?? null
      out[`imperviousPct2001${label}`] = metricEntry(base, 'percent', SRC_NLCD, VINTAGES.nlcdBaseline, support,
        base !== null ? [] : ['NLCD 2001 impervious unavailable'])
    }
  }
  return out
}

const roads: Family = async (d, record, pre): Promise<Record<string, MetricEntry>> => {
  const area = record.watershed?.areaSqkm
  if (!watershedPolygon(record) || !area || !pre) {
    const reason = !pre && watershedPolygon(record) && area ? 'the published data cannot answer for this watershed' : 'watershed polygon or area unavailable'
    return {
      roadDensity: metricEntry(null, 'km/km2', 'TIGERweb roads', VINTAGES.tigerRoads, 'pointWatershed', [reason]),
      roadCrossingDensity: metricEntry(null, 'crossings/km2', 'TIGERweb roads x NHDPlus HR flowlines', VINTAGES.tigerRoads, 'pointWatershed', [reason]),
    }
  }
  const { tiger } = await precomputedVintages(d.src)
  return {
    roadLengthKm: metricEntry(pre.roadLengthKm, 'km', SRC_ROADS, tiger, 'pointWatershed'),
    roadDensity: metricEntry(pre.roadDensity, 'km/km2', SRC_ROADS, tiger, 'pointWatershed'),
    roadCrossings: metricEntry(Math.trunc(pre.roadCrossings), 'count', SRC_CROSS, tiger, 'pointWatershed'),
    roadCrossingDensity: metricEntry(pre.roadCrossingDensity, 'crossings/km2', SRC_CROSS, tiger, 'pointWatershed'),
  }
}

const dams: Family = async (d, record, pre): Promise<Record<string, MetricEntry>> => {
  const area = record.watershed?.areaSqkm
  if (!watershedPolygon(record) || !area || !pre) {
    const reason = !pre && watershedPolygon(record) && area ? 'the published data cannot answer for this watershed' : 'watershed polygon or area unavailable'
    return { damCount: metricEntry(null, 'count', 'USACE NID', VINTAGES.nid, 'pointWatershed', [reason]) }
  }
  const { nid } = await precomputedVintages(d.src)
  const missing = Math.trunc(pre.damsWithoutNormalStorage || 0)
  const warns = missing ? [`${missing} dam(s) without normal storage counted as zero`] : []
  return {
    damCount: metricEntry(Math.trunc(pre.damCount), 'count', SRC_DAMS, nid, 'pointWatershed'),
    damDensityPerSqkm: metricEntry(pre.damDensityPerSqkm, 'count/km2', SRC_DAMS, nid, 'pointWatershed'),
    damStorageAcreFt: metricEntry(pre.damStorageAcreFt, 'acre-ft', `${SRC_DAMS}, normal storage`, nid, 'pointWatershed', warns),
    damStoragePerSqkm: metricEntry(pre.damStoragePerSqkm, 'acre-ft/km2', `${SRC_DAMS}, normal storage`, nid, 'pointWatershed', warns),
    damNidStorageAcreFt: metricEntry(pre.damNidStorageAcreFt, 'acre-ft', `${SRC_DAMS}, NID storage`, nid, 'pointWatershed'),
    damNidStoragePerSqkm: metricEntry(pre.damNidStoragePerSqkm, 'acre-ft/km2', `${SRC_DAMS}, NID storage`, nid, 'pointWatershed'),
  }
}

const soils: Family = async (d, record, pre) => {
  if (!watershedPolygon(record)) {
    return { soilKFactor: metricEntry(null, 'dimensionless', SRC_SOILS_FALLBACK, 'gSSURGO via Soil Data Access (live service)', 'pointWatershed', ['watershed polygon unavailable']) }
  }
  if (!pre) {
    return { soilKFactor: metricEntry(null, 'dimensionless', SRC_SOILS_FALLBACK, 'gSSURGO via Soil Data Access (live service)', 'pointWatershed', ['the published data cannot answer for this watershed']) }
  }
  const value = pre.soilKFactor ?? null
  const share = pre.soilKCoverage ?? null
  const warns: string[] = []
  if (value === null) warns.push('no surface-horizon kwfact in the AOI')
  else if (share !== null && share < COVERAGE_WARN) {
    warns.push(`${pyFixed((1 - share) * 100, 0)}% of the watershed area has no surface-horizon K and is excluded from the mean`)
  }
  const { soils: vintage } = await precomputedVintages(d.src)
  return { soilKFactor: metricEntry(value, 'dimensionless', SRC_SOILS, vintage, 'pointWatershed', warns) }
}

const runoff: Family = async (_d, record) => {
  const qama = record.site?.eromQamaCfs
  const da = record.site?.drainageAreaSqkm
  if (qama === null || qama === undefined) {
    return { meanAnnualFlowCfs: metricEntry(null, 'cfs', SRC_RUNOFF, VINTAGE_RUNOFF, 'reach', ['EROM qama unavailable on the anchor reach']) }
  }
  const out: Record<string, MetricEntry> = { meanAnnualFlowCfs: metricEntry(pyRound(qama, 3), 'cfs', SRC_RUNOFF, VINTAGE_RUNOFF, 'reach') }
  if (da) {
    const depth = (qama * CFS_TO_M3YR) / (da * 1e6) * 1000
    out.runoffDepthMm = metricEntry(pyRound(depth, 1), 'mm/yr', `${SRC_RUNOFF} over drainage area`, VINTAGE_RUNOFF, 'pointWatershed',
      ['EROM-derived; not equivalent to StreamCat runoffws'])
  }
  return out
}

// ---- base flow: the grid's cells whose centres lie in the watershed

const BFI = { x0: -2356000, y0: 3173000, cell: 1000, width: 4614, height: 2905, nodata: 255 }
let bfiP: Promise<any> | null = null

function bfiImage(src: DataSource): Promise<any> {
  bfiP ??= src.ready().then(() => src.bytesOf('engine/bfi48grd.tif')).then(async (buf) => (await fromArrayBuffer(buf)).getImage())
  bfiP.catch(() => { bfiP = null })
  return bfiP
}

async function bfiWindow(src: DataSource, i0: number, j0: number, i1: number, j1: number): Promise<{ at: (i: number, j: number) => number }> {
  const image = await bfiImage(src)
  const ci0 = Math.max(i0, 0)
  const cj0 = Math.max(j0, 0)
  const ci1 = Math.min(i1, BFI.width)
  const cj1 = Math.min(j1, BFI.height)
  if (ci1 <= ci0 || cj1 <= cj0) return { at: () => BFI.nodata }
  const data = (await image.readRasters({ window: [ci0, cj0, ci1, cj1], interleave: true })) as ArrayLike<number>
  const w = ci1 - ci0
  return { at: (i, j) => (i < ci0 || i >= ci1 || j < cj0 || j >= cj1 ? BFI.nodata : data[(j - cj0) * w + (i - ci0)]) }
}

/** Rings of a (Multi)Polygon in EPSG:5070 metres. */
function ringsIn5070(geom: any): [number, number][][] {
  const polys: number[][][][] = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates
  return polys.flatMap((rings) => rings.map((ring) => ring.map(([x, y]) => albersForward(x, y))))
}

/** baseflow.grid_mean: the cells whose centre is inside, else every cell touched, else the cell under
 * the site point; ``(mean, method, cells)``. Grid centres lie at least 5 m from any catchment edge, so
 * the even-odd test at the centres is GDAL's. */
async function gridMean(src: DataSource, geom: any, point: [number, number] | null): Promise<[number | null, string, number]> {
  const rings = ringsIn5070(geom)
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
  for (const r of rings) for (const [x, y] of r) {
    x0 = Math.min(x0, x)
    y0 = Math.min(y0, y)
    x1 = Math.max(x1, x)
    y1 = Math.max(y1, y)
  }
  const i0 = Math.floor((x0 - BFI.x0) / BFI.cell)
  const i1 = Math.floor((x1 - BFI.x0) / BFI.cell) + 1
  const j0 = Math.floor((BFI.y0 - y1) / BFI.cell)
  const j1 = Math.floor((BFI.y0 - y0) / BFI.cell) + 1
  const win = await bfiWindow(src, i0, j0, i1, j1)
  const mean = (cells: [number, number][]): [number | null, number] => {
    const vals = cells.map(([i, j]) => win.at(i, j)).filter((v) => v !== BFI.nodata)
    if (!vals.length) return [null, 0]
    let s = 0
    for (const v of vals) s += v
    return [pyRound(s / vals.length, 1), vals.length]
  }
  // the centres inside (even-odd along each row of centres)
  const centres: [number, number][] = []
  for (let j = j0; j < j1; j++) {
    const yc = BFI.y0 - (j + 0.5) * BFI.cell
    const xs: number[] = []
    for (const r of rings) {
      for (let k = 0; k < r.length - 1; k++) {
        const [ax, ay] = r[k]
        const [bx, by] = r[k + 1]
        if ((ay > yc) !== (by > yc)) xs.push(ax + (yc - ay) * (bx - ax) / (by - ay))
      }
    }
    xs.sort((a, b) => a - b)
    for (let k = 0; k + 1 < xs.length; k += 2) {
      for (let i = Math.max(i0, Math.ceil((xs[k] - BFI.x0) / BFI.cell - 0.5)); i < i1; i++) {
        const xc = BFI.x0 + (i + 0.5) * BFI.cell
        if (xc <= xs[k]) continue
        if (xc >= xs[k + 1]) break
        centres.push([i, j])
      }
    }
  }
  let [v, n] = mean(centres)
  if (v !== null) return [v, 'cellCenters', n]
  // every cell the polygon touches (the polygon is smaller than a cell here)
  const touched: [number, number][] = []
  for (let j = j0; j < j1; j++) {
    for (let i = i0; i < i1; i++) {
      const cx0 = BFI.x0 + i * BFI.cell
      const cy1 = BFI.y0 - j * BFI.cell
      if (rectMeetsRings(rings, cx0, cy1 - BFI.cell, cx0 + BFI.cell, cy1)) touched.push([i, j])
    }
  }
  ;[v, n] = mean(touched)
  if (v !== null) return [v, 'cellsTouched', n]
  if (point) {
    const [px, py] = albersForward(point[1], point[0])
    if (px >= BFI.x0 && px <= BFI.x0 + BFI.width * BFI.cell && py <= BFI.y0 && py >= BFI.y0 - BFI.height * BFI.cell) {
      const i = Math.floor((px - BFI.x0) / BFI.cell)
      const j = Math.floor((BFI.y0 - py) / BFI.cell)
      const one = await bfiWindow(src, i, j, i + 1, j + 1)
      const value = one.at(i, j)
      if (value !== BFI.nodata) return [value, 'sitePoint', 1]
    }
  }
  return [null, 'none', 0]
}

function rectMeetsRings(rings: [number, number][][], x0: number, y0: number, x1: number, y1: number): boolean {
  const inside = (x: number, y: number) => x >= x0 && x <= x1 && y >= y0 && y <= y1
  for (const r of rings) {
    for (let k = 0; k < r.length - 1; k++) {
      const [ax, ay] = r[k]
      const [bx, by] = r[k + 1]
      if (inside(ax, ay) || inside(bx, by)) return true
      // the edge crosses the rectangle (Liang-Barsky)
      let t0 = 0
      let t1 = 1
      const dx = bx - ax
      const dy = by - ay
      const clip = (p: number, q: number) => {
        if (p === 0) return q >= 0
        const t = q / p
        if (p < 0) {
          if (t > t1) return false
          if (t > t0) t0 = t
        } else {
          if (t < t0) return false
          if (t < t1) t1 = t
        }
        return true
      }
      if (clip(-dx, ax - x0) && clip(dx, x1 - ax) && clip(-dy, ay - y0) && clip(dy, y1 - ay) && t0 <= t1) return true
    }
  }
  // the rectangle inside the polygon
  const cx = (x0 + x1) / 2
  const cy = (y0 + y1) / 2
  let inside2 = false
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i]
      const [xj, yj] = r[j]
      if ((yi > cy) !== (yj > cy) && cx < (xj - xi) * (cy - yi) / (yj - yi) + xi) inside2 = !inside2
    }
  }
  return inside2
}

const baseflow: Family = async (d, record): Promise<Record<string, MetricEntry>> => {
  const geom = watershedPolygon(record)
  const entry = (value: number | null, warnings: string[] = []) =>
    metricEntry(value, '%', SRC_BFI, VINTAGES.bfiGrid, 'pointWatershed', warnings)
  if (!geom) return { baseflowIndexPct: entry(null, ['watershed polygon unavailable']) }
  await d.src.ready()
  if (!d.src.has('engine/bfi48grd.tif')) return { baseflowIndexPct: entry(null, ['the base-flow index grid is not shipped with this engine copy']) }
  const site = record.site ?? {}
  const point: [number, number] | null = site.snapLat != null && site.snapLon != null ? [site.snapLat, site.snapLon] : null
  let value: number | null, method: string, n: number
  try {
    ;[value, method, n] = await gridMean(d.src, geom, point)
  } catch (err) {
    return { baseflowIndexPct: entry(null, [`grid read failed: ${err instanceof Error ? err.message : err}`]) }
  }
  const warnings: string[] = []
  if (value === null) warnings.push('the watershed lies outside the base-flow index grid (conterminous United States only)')
  else if (method === 'cellsTouched') warnings.push(`watershed smaller than a 1 km grid cell: the mean of the ${n} cells it touches`)
  else if (method === 'sitePoint') warnings.push('watershed smaller than a 1 km grid cell: the cell under the site point')
  return {
    baseflowIndexPct: entry(value, warnings),
    baseflowIndexCells: metricEntry(n, 'count', SRC_BFI, VINTAGES.bfiGrid, 'pointWatershed'),
  }
}

// ---- cross-sections: the published grid's sections nearest the reach's nine positions

const SRC_XS = 'USGS 3DEP reach cross-sections from the STAF cross-section grid, median of the grid sections nearest nine reach positions (engine reach)'
const VINTAGE_XS = '3DEP 1 m where published, else 3 or 10 m (staf-xs-current); Bieger et al. 2015 bankfull'

const xsection: Family = async (d, record): Promise<Record<string, MetricEntry>> => {
  const reach = record.reach?.geometry
  const site = record.site ?? {}
  const da = site.drainageAreaSqkm
  if (!reach || !da) {
    return { entrenchmentRatio: metricEntry(null, 'ratio', SRC_XS, VINTAGE_XS, 'reach', ['reach geometry or drainage area unavailable']) }
  }
  const bf = await bankfullGeometry(d.src, da, site.snapLat ?? null, site.snapLon ?? null)
  const warns: string[] = []
  const coords: [number, number][] = reach.features?.[0]?.geometry?.type === 'LineString' ? reach.features[0].geometry.coordinates : []
  const { geom, covered } = await gridReachGeometry(d.src, d.hr, reachLineIds.get(record) ?? [], coords, bf.extrapolated)
  if (!covered && !geom) warns.push('the cross-section grid does not cover this region yet')
  if (geom?.edge_limited) warns.push('flood-prone width reached the DEM buffer edge')
  if (bf.extrapolated) warns.push('drainage area is outside the Bieger fit range; bankfull is extrapolated')
  if (geom?.dem_resolution_m) warns.push(`DEM resolution ${geom.dem_resolution_m} m`)
  const out: Record<string, MetricEntry> = {}
  for (const [name, value] of [['entrenchmentRatio', geom?.entrenchment_ratio ?? null], ['bankHeightRatio', geom?.bank_height_ratio ?? null]] as const) {
    out[name] = metricEntry(value !== null ? pyRound(value, 3) : null, 'ratio', SRC_XS, VINTAGE_XS, 'reach',
      value !== null ? warns : [...warns, 'cross-section ratio unavailable'])
  }
  out.bankfullWidthM = metricEntry(bf.width_m, 'm', `Bieger 2015 (${bf.division_name})`, VINTAGE_XS, 'reach', bf.extrapolated ? ['extrapolated'] : [])
  return out
}

/** The registered families (metrics.families()). */
export const FAMILIES: Record<string, Family> = { baseflow, dams, landcover, roads, runoff, soils, xsection }

const valuesMemo = new Map<string, Promise<Record_ | null>>()

/** precomputed.values_for: the bundle's watershed values for the record's outlet under its budget. */
function valuesFor(d: MetricsData, record: Record_): Promise<Record_ | null> {
  const nid = record.site?.nhdplusId
  const cfg = record.input?.config ?? {}
  if (!nid) return Promise.resolve(null)
  const key = `${d.src.base}|${nid}|${cfg.maxReaches || 5000}|${cfg.maxHops || 200}`
  let got = valuesMemo.get(key)
  if (!got) {
    got = watershedValues(d.hr, d.values, nid, cfg.maxReaches || 5000, cfg.maxHops || 200)
      .then((v) => (v.status === 'ok' ? v : null), () => null)
    valuesMemo.set(key, got)
  }
  return got
}

/** metrics.compute_all: the asked families (all when ``families`` is null) in name order, merged. */
export async function computeAll(d: MetricsData, record: Record_, families: string[] | null, progress?: Progress): Promise<Record<string, MetricEntry>> {
  const names = Object.keys(FAMILIES).sort()
  let wanted = names
  if (families) {
    const asked = new Set(families.map(String))
    const unknown = [...asked].filter((f) => !names.includes(f)).sort()
    if (unknown.length) (record.warnings ??= []).push(`unknown metric families ignored: ${unknown.join(', ')}`)
    wanted = names.filter((n) => asked.has(n))
  }
  const pre = wanted.some((n) => ['dams', 'landcover', 'roads', 'soils'].includes(n)) ? await valuesFor(d, record) : null
  const out: Record<string, MetricEntry> = {}
  for (const name of wanted) {
    try {
      progress?.({ stage: 'metrics', family: name })
    } catch {
      // the UI never breaks a run
    }
    let entries: Record<string, MetricEntry>
    try {
      entries = await FAMILIES[name](d, record, pre)
    } catch (err) {
      entries = { [`${name}Error`]: metricEntry(null, '', name, '', 'pointWatershed', [`metric family failed: ${err instanceof Error ? err.message : err}`]) }
    }
    Object.assign(out, entries)
  }
  return out
}

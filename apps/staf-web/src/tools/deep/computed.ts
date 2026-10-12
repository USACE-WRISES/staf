// DEEP's desktop prefill (deep/metrics/computed.py, measure.compute_metrics_only): the metrics a desk
// can answer, from the STAF site engine's record (when the metric's curve allows engine values), the
// StreamCat row of the site's COMID, the NID dams within a mile, and the reach cross-sections (from
// the published grid; the Shiny DEEP samples live 3DEP). Where the Shiny DEEP would then ask NLCD's
// live service, the static app has no answer: no outside data services.
import { pyFixed, pySum } from '../../app/pyfmt'
import type { DataSource } from '../../data/source'
import { bankfullGeometry } from '../../engine/bieger'
import type { Dataset } from '../../engine/dataset'
import type { PointTables } from '../../engine/points'
import { engineLineIds } from '../../engine/site'
import { streamcatRow } from '../../engine/streamcat'
import { crossSectionQuality, gridReachGeometry, type ReachGeometry } from '../../engine/xsreach'
import { describes } from '../sfari/anchorText'
import { type Bundle, engineGateByMetric, predictorSourceOf, type Rec } from './method'

const BASIS_SITE_ENGINE = 'site-engine'
const BASIS_STREAMCAT = 'streamcat'
const BASIS_3DEP = '3dep'
const BASIS_NID = 'nid'
const STREAMCAT_NAMES = ['pctimp2019', 'pctcrop2019', 'pcthay2019', 'pctwdwet2019', 'pcthbwet2019', 'rddens', 'damdens', 'bfi', 'rdcrs', 'damnrmstor', 'runoff']
const RP100_NATURAL = ['pctconif2019', 'pctdecid2019', 'pctmxfst2019', 'pctshrb2019', 'pctgrs2019', 'pctwdwet2019', 'pcthbwet2019']
const RDCRS_NOTE = 'API-served units, enter values as served, a crossings per km2 value computed by hand lands about 100 times above this curve'
const XS_WITHHOLD: Record<string, string[]> = { bhr: ['low_quality', 'out_of_range_bhr'], er: ['low_quality', 'out_of_range_er'] }

const round = (v: number, n: number) => Number(pyFixed(v, n))

interface Computed { value: number, source: string, confidence: string, engine: boolean, basis: string }
const cv = (value: number, source: string, confidence = 'M', engine = false, basis = ''): Computed => ({ value, source, confidence, engine, basis })

export interface PrefillData { src: DataSource, hr: Dataset, v2: Dataset, points: PointTables }

class Ctx {
  allowEngine = false
  private sc: Promise<Record<string, any>> | null = null
  private rp: Promise<Record<string, any>> | null = null
  private geom: Promise<ReachGeometry | null> | null = null
  withheldXs: Record<string, string> = {}

  constructor(readonly d: PrefillData, readonly ci: Rec, readonly record: Rec | null, readonly anchor: Rec) {}

  get lat(): number | null { return this.ci.lat ?? null }
  get lon(): number | null { return this.ci.lon ?? null }
  get comid(): number | null { return this.ci.comid ?? null }

  engineMetric(key: string): any {
    if (!this.allowEngine) return null
    return this.record?.metrics?.[key]?.value ?? null
  }

  engineLabel(): string {
    const ver = String(this.record?.engineVersion || '')
    return `STAF site engine v${ver || '0.5.0'}`
  }

  describes(): string {
    const label = describes(this.anchor)
    return label ? `, describes the ${label}` : ''
  }

  streamcatSource(what: string): string {
    return `StreamCat lookup engine ${what} (watershed)${this.describes()}`
  }

  streamcat(): Promise<Record<string, any>> {
    this.sc ??= this.comid ? streamcatRow(this.d.src, this.d.v2, this.comid, STREAMCAT_NAMES).then((r) => r ?? {}, () => ({})) : Promise.resolve({})
    return this.sc
  }

  streamcatRp100(): Promise<Record<string, any>> {
    this.rp ??= this.comid ? streamcatRow(this.d.src, this.d.v2, this.comid, RP100_NATURAL).then((r) => r ?? {}, () => ({})) : Promise.resolve({})
    return this.rp
  }

  /** _reach_geom: the reach's sections from the grid, with Bieger's bankfull. */
  reachGeom(): Promise<ReachGeometry | null> {
    this.geom ??= (async () => {
      const reach = this.ci.reach_geojson
      const da = this.ci.drainage_area_sqkm
      if (!reach || !da) return null
      const coords = reach.features?.[0]?.geometry?.coordinates ?? []
      if (coords.length < 2) return null
      const bf = await bankfullGeometry(this.d.src, da, this.lat, this.lon)
      const ids = await engineLineIds(this.d, this.record)
      return (await gridReachGeometry(this.d.src, this.d.hr, ids, coords, bf.extrapolated)).geom
    })().catch(() => null)
    return this.geom
  }

  /** _xs_withheld: the K2b rule EASI v2 withholds a geometry rating on. */
  xsWithheld(geom: ReachGeometry, ratio: 'bhr' | 'er'): boolean {
    const record = crossSectionQuality(geom) ?? {}
    const hit = (record.flags ?? []).filter((f: string) => XS_WITHHOLD[ratio].includes(f))
    if (hit.length) this.withheldXs[ratio] = hit.map((f: string) => String(record.reasons?.[f] || f)).join('; ')
    return hit.length > 0
  }
}

const asFloat = (v: unknown): number | null => {
  if (v === null || v === undefined || typeof v === 'boolean') return null
  const x = Number(v)
  return Number.isNaN(x) && typeof v !== 'number' ? null : x
}

async function layered(ctx: Ctx, o: { engineKey: string | null, engineWhat?: string, scCol: string, scWhat: string, ndigits?: number,
  confidence?: string, scNote?: string }): Promise<Computed | null> {
  const ndigits = o.ndigits ?? 2
  const confidence = o.confidence ?? 'H'
  if (o.engineKey) {
    const ev = asFloat(ctx.engineMetric(o.engineKey))
    if (ev !== null) return cv(round(ev, ndigits), `${ctx.engineLabel()} ${o.engineWhat ?? ''}`, confidence, true, BASIS_SITE_ENGINE)
  }
  const v = asFloat((await ctx.streamcat())[o.scCol])
  if (v !== null) {
    let src = ctx.streamcatSource(o.scWhat)
    if (o.scNote) src = `${src}, ${o.scNote}`
    return cv(round(v, ndigits), src, confidence, false, BASIS_STREAMCAT)
  }
  return null      // NLCD over the polygon is a live service: no answer here
}

async function both(getter: (k: string) => Promise<any> | any, keys: string[], cap = 100.0): Promise<number | null> {
  const vals: (number | null)[] = []
  for (const k of keys) vals.push(asFloat(await getter(k)))
  if (vals.some((v) => v === null)) return null
  return Math.min(cap, pySum(vals as number[]))
}

type Adapter = (ctx: Ctx) => Promise<Computed | null>

const impervious: Adapter = async (ctx) => {
  const ev = ctx.engineMetric('imperviousPctWatershed')
  if (ev !== null) return cv(round(Number(ev), 2), `${ctx.engineLabel()} impervious (HR reach watershed, NLCD 2021)`, 'H', true, BASIS_SITE_ENGINE)
  const v = (await ctx.streamcat()).pctimp2019ws
  if (v === null || v === undefined) return null
  return cv(round(Number(v), 2), ctx.streamcatSource('pctimp2019'), 'H', false, BASIS_STREAMCAT)
}

const anthropogenic: Adapter = async (ctx) => {
  const [eImp, eCrop, eHay] = ['imperviousPctWatershed', 'cropPctWatershed', 'hayPasturePctWatershed'].map((k) => ctx.engineMetric(k))
  if ([eImp, eCrop, eHay].some((v) => v !== null)) {
    const total = (eImp ?? 0.0) + (eCrop ?? 0.0) + (eHay ?? 0.0)
    return cv(round(Number(total), 2), `${ctx.engineLabel()} crop+hay+impervious (HR reach watershed, NLCD 2021)`, 'M', true, BASIS_SITE_ENGINE)
  }
  const sc = await ctx.streamcat()
  const [crop, hay, imp] = [sc.pctcrop2019ws, sc.pcthay2019ws, sc.pctimp2019ws].map((v) => (v === undefined ? null : v))
  if (crop !== null || hay !== null || imp !== null) {
    const total = (crop ?? 0.0) + (hay ?? 0.0) + (imp ?? 0.0)
    return cv(round(Number(total), 2), ctx.streamcatSource('crop+hay+impervious'), 'M', false, BASIS_STREAMCAT)
  }
  return null
}

const crop: Adapter = (ctx) => layered(ctx, { engineKey: 'cropPctWatershed', engineWhat: 'cultivated crops (HR reach watershed, NLCD 2021)',
  scCol: 'pctcrop2019ws', scWhat: 'pctcrop2019' })

const wetland: Adapter = async (ctx) => {
  const ev = await both((k) => ctx.engineMetric(k), ['woodyWetlandPctWatershed', 'herbWetlandPctWatershed'])
  if (ev !== null) return cv(round(ev, 2), `${ctx.engineLabel()} wetland, woody plus herbaceous (HR reach watershed, NLCD 2021)`, 'H', true, BASIS_SITE_ENGINE)
  const sc = await ctx.streamcat()
  const sv = await both((k) => sc[k], ['pctwdwet2019ws', 'pcthbwet2019ws'])
  if (sv !== null) return cv(round(sv, 2), ctx.streamcatSource('pctwdwet2019 plus pcthbwet2019'), 'H', false, BASIS_STREAMCAT)
  return null
}

const woodyWetland: Adapter = (ctx) => layered(ctx, { engineKey: 'woodyWetlandPctWatershed', engineWhat: 'woody wetland (HR reach watershed, NLCD 2021)',
  scCol: 'pctwdwet2019ws', scWhat: 'pctwdwet2019' })
const herbWetland: Adapter = (ctx) => layered(ctx, { engineKey: 'herbWetlandPctWatershed', engineWhat: 'herbaceous wetland (HR reach watershed, NLCD 2021)',
  scCol: 'pcthbwet2019ws', scWhat: 'pcthbwet2019' })

const agriculture: Adapter = async (ctx) => {
  const ev = await both((k) => ctx.engineMetric(k), ['cropPctWatershed', 'hayPasturePctWatershed'])
  if (ev !== null) return cv(round(ev, 2), `${ctx.engineLabel()} agriculture, crops plus hay and pasture (HR reach watershed, NLCD 2021)`, 'H', true, BASIS_SITE_ENGINE)
  const sc = await ctx.streamcat()
  const sv = await both((k) => sc[k], ['pctcrop2019ws', 'pcthay2019ws'])
  if (sv !== null) return cv(round(sv, 2), ctx.streamcatSource('pctcrop2019 plus pcthay2019'), 'H', false, BASIS_STREAMCAT)
  return null
}

const degreeOfRegulation: Adapter = async (ctx) => {
  const sc = await ctx.streamcat()
  const storage = asFloat(sc.damnrmstorws)
  const runoff = asFloat(sc.runoffws)
  if (storage === null || runoff === null || runoff <= 0) return null
  return cv(round(100.0 * storage / (1000.0 * runoff), 2), ctx.streamcatSource('damnrmstor over runoff'), 'M', false, BASIS_STREAMCAT)
}

const nidDams: Adapter = async (ctx) => {
  if (ctx.lat === null || ctx.lon === null) return null
  const dams = await ctx.d.points.nidRadius(Number(ctx.lat), Number(ctx.lon), 1.0).catch(() => null)
  if (dams === null) return null
  return cv(dams.length, 'USACE National Inventory of Dams, mapped dams within one mile of the site', 'M', false, BASIS_NID)
}

const roadDensity: Adapter = (ctx) => layered(ctx, { engineKey: 'roadDensity', engineWhat: 'road density (HR reach watershed, TIGERweb roads)',
  scCol: 'rddensws', scWhat: 'rddens', ndigits: 4, confidence: 'M' })
const damDensity: Adapter = (ctx) => layered(ctx, { engineKey: 'damDensityPerSqkm', engineWhat: 'dam density (HR reach watershed, USACE NID)',
  scCol: 'damdensws', scWhat: 'damdens', ndigits: 4 })
const baseflowIndex: Adapter = (ctx) => layered(ctx, { engineKey: 'baseflowIndexPct', engineWhat: 'base flow index (HR reach watershed, USGS base-flow index grid)',
  scCol: 'bfiws', scWhat: 'bfi', confidence: 'M' })
const roadCrossings: Adapter = (ctx) => layered(ctx, { engineKey: 'roadCrossingDensity',
  engineWhat: 'road-stream crossings per km2 (HR reach watershed, TIGERweb roads on the NHDPlus HR network)',
  scCol: 'rdcrsws', scWhat: 'rdcrs', ndigits: 4, confidence: 'M', scNote: RDCRS_NOTE })

const naturalRiparianCover: Adapter = async (ctx) => {
  if (!ctx.comid) return null
  const got = await ctx.streamcatRp100()
  const vals = RP100_NATURAL.map((n) => asFloat(got[n + 'wsrp100']))
  if (!vals.length || vals.some((v) => v === null)) return null
  return cv(round(Math.min(100.0, pySum(vals as number[])), 2), ctx.streamcatSource('natural cover of the 100 m riparian corridor (forest, shrub, grassland, wetland)'),
    'M', false, BASIS_STREAMCAT)
}

const entrenchment: Adapter = async (ctx) => {
  const geom = await ctx.reachGeom()
  const er = geom?.entrenchment_ratio ?? null
  if (!geom || er === null || ctx.xsWithheld(geom, 'er')) return null
  return cv(round(er, 2), '3DEP DEM cross-sections, reach median ER of the sampled sections (modeled)', 'M', false, BASIS_3DEP)
}

const bankHeight: Adapter = async (ctx) => {
  const geom = await ctx.reachGeom()
  const bhr = geom?.bank_height_ratio ?? null
  if (!geom || bhr === null || ctx.xsWithheld(geom, 'bhr')) return null
  return cv(round(bhr, 2), '3DEP DEM cross-sections, reach median bank-height ratio of the sampled sections (modeled)', 'M', false, BASIS_3DEP)
}

const widthDepth: Adapter = async (ctx) => {
  const g = await ctx.reachGeom()
  const w = g?.bankfull_width_m
  const dd = g?.bankfull_depth_m
  if (w && dd && dd > 0) return cv(round(w / dd, 1), '3DEP DEM cross-section, width/depth of the default section (modeled)', 'M', false, BASIS_3DEP)
  return null
}

const ADAPTERS: Record<string, Adapter> = {
  'catchment-hydrology-impervious-cover': impervious, 'catchment-hydrology-percent-impervious-cover': impervious,
  'catchment-hydrology-effective-impervious-cover': impervious, 'spring-pctimp2019ws': impervious,
  'catchment-hydrology-anthropogenic-land-cover': anthropogenic,
  'spring-pctcrop2019ws': crop, 'spring-pctwet2019ws': wetland, 'spring-pctwdwet2019ws': woodyWetland, 'spring-pcthbwet2019ws': herbWetland,
  'spring-pctag2019ws': agriculture, 'spring-dorws': degreeOfRegulation, 'spring-nid-dams-1mi': nidDams,
  'spring-rddensws': roadDensity, 'spring-damdensws': damDensity, 'spring-bfiws': baseflowIndex, 'spring-rdcrsws': roadCrossings,
  'spring-natural-riparian-cover': naturalRiparianCover,
  'floodplain-connectivity-entrenchment-ratio-er': entrenchment,
  'channel-and-floodplain-dynamics-bank-height-ratio-bhr': bankHeight, 'spring-bank-height-ratio': bankHeight,
  'channel-evolution-width-depth-ratio': widthDepth,
}

/** computed.computable_ids */
export const COMPUTABLE = new Set(Object.keys(ADAPTERS))

/** measure.compute_metrics_only: {metricId: {value, na, note, origin, source, engine, basis}}. */
export async function computeMetrics(d: PrefillData, ci: Rec, metricIds: string[], bundle: Bundle | null,
                                     engineRecord: Rec | null): Promise<Record<string, Rec>> {
  const pre = engineRecord ?? ci.site_engine
  const record = pre && pre.status === 'ok' ? pre : null
  const ctx = new Ctx(d, ci, record, ci.siteAnchor || {})
  const def = bundle !== null ? predictorSourceOf(bundle) !== 'streamcat' : false
  const byId = bundle !== null ? engineGateByMetric(bundle) : {}
  const out: Record<string, Rec> = {}
  // Python's compute_for walks set(metric_ids); every adapter is independent, so order is free
  for (const mid of [...new Set(metricIds)]) {
    const fn = ADAPTERS[mid]
    if (!fn) continue
    ctx.allowEngine = mid in byId ? byId[mid] : def
    let got: Computed | null = null
    try {
      got = await fn(ctx)
    } catch {
      got = null
    }
    if (got) out[mid] = { value: got.value, na: false, note: '', origin: 'desktop', source: got.source, engine: got.engine, basis: got.basis || '' }
  }
  return out
}

// SFARI's desktop evidence pull (sfari/evidence.py), in the browser.
//
// The prefetch reads the published data the Shiny SFARI reads from the STAF data bundle: the NID dams
// in a one-mile box, the WQP nutrient medians, the nearest comparable NWIS gage, the NWI wetland strips
// along the reach and the StreamCat row of the site's COMID (src/engine/points.ts, wetlands.ts,
// streamcat.ts). Where the Shiny app would then ask a live service (the bundle cannot answer), the
// static app has no answer: no outside data services. Then the 22 adapters, unchanged: each returns
// an evidence entry with a value, a suggested Likert where a break table exists, a confidence and a
// source, shown in the worksheet to support the assessor's scoring.
import { pyFixed, pyFixedComma, pyFloat, pySigned, pySum } from '../../app/pyfmt'
import type { Dataset } from '../../engine/dataset'
import { ENGINE_VERSION } from '../../engine/site'
import { type FlowStats, PointTables } from '../../engine/points'
import { streamcatRow } from '../../engine/streamcat'
import type { ValueTables } from '../../engine/values'
import { wetlandsAlongReach, type WetlandsAlongReach } from '../../engine/wetlands'
import type { DataSource } from '../../data/source'
import * as A from './anchorText'
import { suggest } from './method'

export const FCODE_LABEL: Record<number, string> = {
  46006: 'perennial', 46003: 'intermittent', 46007: 'ephemeral', 55800: 'artificial path', 33600: 'canal/ditch',
}
const ACRE_FT_PER_KM2_TO_M3_PER_KM2 = 1233.48184

/** StreamCat base names fetched once per COMID. */
export const STREAMCAT_NAMES = [
  'pctimp2019', 'pctimp2001', 'pctcrop2019', 'pcthay2019', 'pctwdwet2019', 'pcthbwet2019',
  'pctconif2019', 'pctdecid2019', 'pctmxfst2019', 'pctshrb2019', 'pctgrs2019',
  'kffact', 'rddens', 'rdcrs', 'damdens', 'damnrmstor',
  'hyd', 'sed', 'chem', 'conn', 'temp', 'habt',
]

/** engine metric key -> the StreamCat columns whose sum is its analog */
const SC_COLUMNS: Record<string, string[]> = {
  imperviousPctWatershed: ['pctimp2019ws'],
  imperviousPct2001Watershed: ['pctimp2001ws'],
  cropPctWatershed: ['pctcrop2019ws'],
  hayPasturePctWatershed: ['pcthay2019ws'],
  roadDensity: ['rddensws'],
  roadCrossingDensity: ['rdcrsws'],
  woodyWetlandPctWatershed: ['pctwdwet2019ws'],
  herbWetlandPctWatershed: ['pcthbwet2019ws'],
  forestPctRiparian: ['pctconif2019wsrp100', 'pctdecid2019wsrp100', 'pctmxfst2019wsrp100'],
  shrubPctRiparian: ['pctshrb2019wsrp100'],
  grasslandPctRiparian: ['pctgrs2019wsrp100'],
  woodyWetlandPctRiparian: ['pctwdwet2019wsrp100'],
  herbWetlandPctRiparian: ['pcthbwet2019wsrp100'],
  soilKFactor: ['kffactws'],
  damDensityPerSqkm: ['damdensws'],
}
const INTEGRITY_TIERS = 'Below 0.40 reads poor and above 0.70 good, the EASI integration tiers.'

/** models.EvidenceResult as its to_dict(). */
export interface Evidence {
  metric_id: string
  value: any
  value_text: string
  field_value_text: string
  suggested_likert: string | null
  confidence: string
  source: string
  source_url: string
  status: string
  note: string
  origin: string
  engine_version: string | null
  anchor_label: string
  fallback_reason: string
  upgrade_pending: boolean
}

function ev(metricId: string, f: Partial<Evidence> = {}): Evidence {
  return {
    metric_id: metricId, value: null, value_text: '', field_value_text: '', suggested_likert: null, confidence: 'M',
    source: '', source_url: '', status: 'ok', note: '', origin: 'pull', engine_version: null, anchor_label: '',
    fallback_reason: '', upgrade_pending: false, ...f,
  }
}

export interface EngineState { status: string, record?: any, reason?: string | null }

interface Ctx {
  lat: number
  lon: number
  comid: number | null
  huc8: string | null
  reach_geojson: unknown
  drainage_area_sqkm: number | null
  slope: number | null
  fcode: number | null
  stream_order: number | null
  sinuosity: number | null
  extras: {
    engine: EngineState
    engine_metrics: Record<string, any>
    site_anchor: Record<string, any> | null
    nid?: unknown[] | null
    tn?: number | null
    tp?: number | null
    flow?: FlowStats | null
    nwi?: WetlandsAlongReach | null
    streamcat: Record<string, any>
  }
}

const f1 = (v: number) => pyFixed(v, 1)
const f2 = (v: number) => pyFixed(v, 2)
const round = (v: number, n: number) => Number(pyFixed(v, n))
const isNum = (v: unknown): boolean => v !== null && v !== undefined

// ---------------------------------------------------------------- the two engines' state
const engineState = (ctx: Ctx): EngineState => ctx.extras.engine ?? { status: 'idle' }
const eng = (ctx: Ctx, key: string): any => ctx.extras.engine_metrics?.[key] ?? null
const anchorOf = (ctx: Ctx) => ctx.extras.site_anchor
const scRow = (ctx: Ctx) => ctx.extras.streamcat ?? {}

/** _sc: the sum of StreamCat columns, null when any is missing or not numeric. */
function sc(ctx: Ctx, ...cols: string[]): number | null {
  const row = scRow(ctx)
  let total = 0.0
  for (const c of cols) {
    const v = row[c]
    if (v === null || v === undefined || typeof v === 'boolean') return null
    const x = Number(v)
    if (typeof v !== 'number' && Number.isNaN(x)) return null
    total += x
  }
  return cols.length ? total : null
}

const integrity = (ctx: Ctx, key: string): [number | null, number | null] => [sc(ctx, `${key}cat`), sc(ctx, `${key}ws`)]

const fallbackAllowed = (ctx: Ctx): boolean => engineState(ctx).status !== 'running' && Object.keys(scRow(ctx)).length > 0

function scFor(ctx: Ctx, key: string): number | null {
  const cols = SC_COLUMNS[key]
  return cols ? sc(ctx, ...cols) : null
}

type Origin = 'engine' | 'streamcat'

/** _wv: the engine's value, else the StreamCat analog. */
function wv(ctx: Ctx, key: string): [number | null, Origin | null] {
  const v = eng(ctx, key)
  if (v !== null) return [Number(v), 'engine']
  if (fallbackAllowed(ctx)) {
    const s = scFor(ctx, key)
    if (s !== null) return [s, 'streamcat']
  }
  return [null, null]
}

/** _wv_all: several keys from one basin. */
function wvAll(ctx: Ctx, keys: string[]): [Record<string, number> | null, Origin | null] {
  const e = keys.map((k) => eng(ctx, k))
  if (e.every((v) => v !== null)) return [Object.fromEntries(keys.map((k, i) => [k, Number(e[i])])), 'engine']
  if (fallbackAllowed(ctx)) {
    const s = keys.map((k) => scFor(ctx, k))
    if (s.every((v) => v !== null)) return [Object.fromEntries(keys.map((k, i) => [k, s[i] as number])), 'streamcat']
  }
  return [null, null]
}

const sfx = (ctx: Ctx, origin: Origin | null): string => (origin === 'streamcat' ? A.basinSuffix(anchorOf(ctx)) : ' (HR reach watershed)')

function fallbackReason(ctx: Ctx): string {
  const st = engineState(ctx)
  const status = st.status || 'idle'
  if (status === 'idle') return 'The STAF site engine has not run for this site.'
  if (status === 'ok') return 'The STAF site engine ran but did not return this value for the HR reach watershed.'
  return `STAF site engine ${status}: ${st.reason || 'no detail'}.`
}

const versionHint = (ctx: Ctx): string | null => ctx.extras.engine?.record?.engineVersion ?? null

function pending(ctx: Ctx, mid: string): Evidence {
  return ev(mid, {
    status: 'pending', origin: 'engine', source: A.engineLabel(versionHint(ctx)), source_url: A.ENGINE_URL,
    note: 'STAF site engine running; the HR reach watershed value replaces this entry when it finishes.',
  })
}

function engineEntry(ctx: Ctx, mid: string, value: any, valueText: string, fieldText: string, note = ''): Evidence {
  const rec = engineState(ctx).record ?? {}
  return ev(mid, {
    value, value_text: valueText, field_value_text: fieldText, suggested_likert: value !== null ? suggest(mid, value) : null,
    confidence: 'M', source: A.engineSource(rec), source_url: A.ENGINE_URL, note: (note ? note + ' ' : '') + A.engineNote(rec),
    origin: 'engine', engine_version: rec.engineVersion || ENGINE_VERSION,
  })
}

function streamcatEntry(ctx: Ctx, mid: string, value: any, valueText: string, fieldText: string, note = '',
                        { fallback = true, suggestIt = true } = {}): Evidence {
  const anchor = anchorOf(ctx)
  return ev(mid, {
    value, value_text: valueText, field_value_text: fieldText,
    suggested_likert: suggestIt && value !== null ? suggest(mid, value) : null,
    confidence: 'M', source: A.sourceLabel(anchor), source_url: A.STREAMCAT_URL,
    note: (note ? note + ' ' : '') + A.basinNote(anchor), origin: 'streamcat', anchor_label: A.describes(anchor),
    fallback_reason: fallback ? fallbackReason(ctx) : '',
  })
}

function entry(ctx: Ctx, mid: string, origin: Origin | null, value: any, valueText: string, fieldText: string, note = ''): Evidence {
  return origin === 'streamcat' ? streamcatEntry(ctx, mid, value, valueText, fieldText, note) : engineEntry(ctx, mid, value, valueText, fieldText, note)
}

function missing(ctx: Ctx, mid: string, what: string): Evidence {
  const st = engineState(ctx)
  const status = st.status || 'idle'
  if (status === 'running') return pending(ctx, mid)
  let note: string
  if (status === 'ok') note = `The STAF site engine did not return ${what} for this watershed.`
  else {
    note = fallbackReason(ctx)
    const c = A.comid(anchorOf(ctx))
    if (c === null) note += ' No StreamCat reach is known for this site.'
    else if (!Object.keys(scRow(ctx)).length) note += ` The StreamCat lookup engine did not answer for COMID ${c}.`
    else note += ` The StreamCat lookup engine has no ${what} for COMID ${c} either.`
  }
  return ev(mid, { status: 'unavailable', origin: 'engine', source: A.engineLabel(versionHint(ctx)), source_url: A.ENGINE_URL, note })
}

const RIPARIAN_KEYS = ['forestPctRiparian', 'shrubPctRiparian', 'grasslandPctRiparian', 'woodyWetlandPctRiparian', 'herbWetlandPctRiparian']

function riparianVegPct(ctx: Ctx): [number | null, Origin | null] {
  const [vals, origin] = wvAll(ctx, RIPARIAN_KEYS)
  if (vals === null) return [null, null]
  return [round(pySum(RIPARIAN_KEYS.map((k) => vals[k])), 1), origin]
}

const AG_COVER_KEY = 'catchment-hydrology-agricultural-cover'
const LIKERT_RANK: Record<string, number> = { 'Strongly Disagree': 0, Disagree: 1, Neutral: 2, Agree: 3, 'Strongly Agree': 4 }

function agPct(ctx: Ctx, origin?: Origin | null): number | null {
  const [vals, o] = wvAll(ctx, ['cropPctWatershed', 'hayPasturePctWatershed'])
  if (vals === null || (origin !== undefined && origin !== null && o !== origin)) return null
  return round(vals.cropPctWatershed + vals.hayPasturePctWatershed, 1)
}

// ---------------------------------------------------------------- the watershed metrics

function evImpervious(ctx: Ctx): Evidence {
  const mid = 'catchment-hydrology-impervious-surface-area'
  const [imp, origin] = wv(ctx, 'imperviousPctWatershed')
  if (imp === null) return missing(ctx, mid, 'impervious cover')
  const s = sfx(ctx, origin)
  // _land_cover_entry
  const ag = agPct(ctx, origin)
  const impLikert = suggest(mid, imp)
  let suggested = impLikert
  let valueText = `${f1(imp)}% impervious${s}`
  let note = ''
  if (ag !== null) {
    valueText = `${f1(imp)}% impervious, ${f1(ag)}% agricultural land${s}`
    const agLikert = suggest(AG_COVER_KEY, ag)
    if (agLikert && (LIKERT_RANK[agLikert] ?? 9) < (LIKERT_RANK[impLikert ?? ''] ?? 9)) suggested = agLikert
    note = `Land-cover indicators: impervious ${f1(imp)}% (${impLikert ?? 'None'}), agricultural ${f1(ag)}% (${agLikert ?? 'None'}). The more limiting one is suggested.`
  }
  const e = entry(ctx, mid, origin, round(imp, 1), valueText, `Impervious ${f1(imp)}%${s}`, note)
  e.suggested_likert = suggested
  e.confidence = origin === 'engine' ? 'H' : 'M'
  return e
}

function evRoadDensity(ctx: Ctx): Evidence {
  const mid = 'catchment-hydrology-road-density'
  const [rd, origin] = wv(ctx, 'roadDensity')
  if (rd === null) return missing(ctx, mid, 'road density')
  const s = sfx(ctx, origin)
  const what = origin === 'engine' ? 'TIGERweb primary + secondary + local roads clipped to the watershed.' : 'StreamCat road density (rddens).'
  return entry(ctx, mid, origin, round(rd, 2), `${f2(rd)} km/km2 road density${s}`, `Roads ${f2(rd)} km/km2${s}`, what)
}

function evImpoundments(ctx: Ctx): Evidence {
  const mid = 'catchment-hydrology-impoundments'
  const dams = eng(ctx, 'damCount')
  if (dams !== null) {
    const storage = eng(ctx, 'damStorageAcreFt')
    const n = Math.trunc(Number(dams))
    const txt = `${n} NID dam(s) in the watershed` + (storage !== null ? `, ${pyFixedComma(Number(storage), 0)} acre-ft normal storage` : '')
    const e = engineEntry(ctx, mid, n, txt, `Dams ${n} in watershed (HR reach watershed)`)
    e.suggested_likert = n === 0 ? 'Strongly Agree' : n <= 2 ? 'Agree' : n <= 5 ? 'Disagree' : 'Strongly Disagree'
    return e
  }
  const [dens, origin] = wv(ctx, 'damDensityPerSqkm')
  if (dens === null) return missing(ctx, mid, 'the dam count')
  const s = sfx(ctx, origin)
  if (origin === 'engine') {
    const e = engineEntry(ctx, mid, round(dens, 3), `${pyFixed(dens, 3)} dams/km2${s}`, `Dams ${pyFixed(dens, 3)}/km2${s}`,
      'NID dams in the HR reach watershed per km2; the count was not reported.')
    e.suggested_likert = null
    return e
  }
  const stor = sc(ctx, 'damnrmstorws')
  const txt = `${pyFixed(dens, 3)} dams/km2` + (stor !== null ? `, ${pyFixedComma(stor, 0)} m3/km2 normal storage` : '') + s
  return streamcatEntry(ctx, mid, round(dens, 3), txt, `Dams ${pyFixed(dens, 3)}/km2${s}`,
    'StreamCat dam density and normal storage per km2; the count itself is not published.', { suggestIt: false })
}

function evWetland(ctx: Ctx): Evidence {
  const mid = 'surface-water-storage-wetland-coverage'
  const [vals, origin] = wvAll(ctx, ['woodyWetlandPctWatershed', 'herbWetlandPctWatershed'])
  if (vals === null) return missing(ctx, mid, 'wetland cover')
  const wet = round(vals.woodyWetlandPctWatershed + vals.herbWetlandPctWatershed, 1)
  const s = sfx(ctx, origin)
  return entry(ctx, mid, origin, wet, `${f1(wet)}% wetland${s}`, `Wetlands ${f1(wet)}%${s}`)
}

function riparian(ctx: Ctx, mid: string, extra = ''): Evidence {
  const [raw, origin] = wv(ctx, 'forestPctRiparian')
  if (raw === null) return missing(ctx, mid, 'riparian forest cover')
  const fp = round(raw, 1)
  const s = sfx(ctx, origin)
  return entry(ctx, mid, origin, fp, `${f1(fp)}% forest in the 100 m riparian buffer${extra}`, `Riparian forest ${f1(fp)}%${s}`,
    origin === 'engine' ? '100 m buffer of the upstream network.' : 'StreamCat riparian forest (100 m buffer, wsrp100).')
}

function riparianVeg(ctx: Ctx, mid: string): Evidence {
  const [veg, origin] = riparianVegPct(ctx)
  if (veg === null) return missing(ctx, mid, 'riparian vegetation')
  const s = sfx(ctx, origin)
  return entry(ctx, mid, origin, veg,
    `${f1(veg)}% natural vegetation in the 100 m riparian buffer (forest + shrub + grassland + wetland)`,
    `Riparian natural veg ${f1(veg)}%${s}`,
    (origin === 'engine' ? '100 m buffer of the upstream network. ' : 'StreamCat riparian classes (100 m buffer, wsrp100). ')
    + 'In grassland or arid ecoregions the natural buffer is non-forest; verify on the aerial basemap.')
}

const evCanopy = (ctx: Ctx) => riparian(ctx, 'light-thermal-regime-riparian-canopy-cover')
const evCorridor = (ctx: Ctx) => riparianVeg(ctx, 'carbon-processing-riparian-corridor-width-and-quality')
const evVegCorridor = (ctx: Ctx) => riparianVeg(ctx, 'nutrient-cycling-vegetated-riparian-corridor-width')
const evRiparianCommunities = (ctx: Ctx) => riparianVeg(ctx, 'community-dynamics-riparian-communities')

function evTransportCapacity(ctx: Ctx): Evidence {
  const mid = 'sediment-continuity-transport-capacity'
  const slope = ctx.slope
  const [k, kOrigin] = wv(ctx, 'soilKFactor')
  const ag = k !== null ? agPct(ctx, kOrigin) : agPct(ctx)
  if (ag === null && k === null) return missing(ctx, mid, 'soil erodibility and agriculture')
  const origin: Origin = kOrigin || wvAll(ctx, ['cropPctWatershed', 'hayPasturePctWatershed'])[1] || 'engine'
  const s = sfx(ctx, origin)
  const parts: string[] = []
  if (isNum(slope)) parts.push(`channel slope ${pyFixed(slope as number, 4)} m/m`)
  if (ag !== null) parts.push(`watershed agriculture ${pyFixed(ag, 0)}%${s}`)
  if (k !== null) parts.push(`soil K ${f2(k)}${s}`)
  const fvt = isNum(slope) ? `Slope ${pyFixed(slope as number, 4)} m/m` : ag !== null ? `Agriculture ${pyFixed(ag, 0)}%${s}` : `Soil K ${f2(k as number)}${s}`
  const e = entry(ctx, mid, origin, slope, parts.join(', '), fvt,
    'Screening context; run the cross-section tool for a Shields transport-capacity analysis.')
  e.suggested_likert = null
  e.confidence = 'L'
  return e
}

const landUseChangeSuggestion = (d: number): string => (d < 1 ? 'Strongly Agree' : d < 3 ? 'Agree' : d < 6 ? 'Disagree' : 'Strongly Disagree')

function evLandUseChange(ctx: Ctx): Evidence {
  const mid = 'catchment-hydrology-land-use-change'
  const [vals, origin] = wvAll(ctx, ['imperviousPct2001Watershed', 'imperviousPctWatershed'])
  if (vals === null) return missing(ctx, mid, 'impervious cover for 2001 and 2021')
  const a = vals.imperviousPct2001Watershed
  const b = vals.imperviousPctWatershed
  const later = origin === 'engine' ? '2021' : '2019'
  const d = round(b - a, 1)
  const s = sfx(ctx, origin)
  const e = entry(ctx, mid, origin, d, `impervious ${f1(a)}% (2001) → ${f1(b)}% (${later}), Δ ${pySigned(d, 1)} pts${s}`,
    `Impervious change ${pySigned(d, 1)} pts${s}`, `NLCD 2001 to ${later} impervious-cover change as a proxy for land conversion.`)
  e.suggested_likert = landUseChangeSuggestion(d)
  return e
}

function evNaturalFlowRegime(ctx: Ctx): Evidence {
  const mid = 'streamflow-regime-channel-natural-flow-regime'
  const f = ctx.extras.flow
  const ratio = f ? f.baseflow_ratio : null
  const storage = eng(ctx, 'damStoragePerSqkm')
  let origin: Origin = 'engine'
  let dam: number | null = null
  if (storage !== null) dam = round(Number(storage) * ACRE_FT_PER_KM2_TO_M3_PER_KM2, 0)
  else if (fallbackAllowed(ctx)) {
    const scStor = sc(ctx, 'damnrmstorws')
    if (scStor !== null) {
      dam = round(scStor, 0)
      origin = 'streamcat'
    }
  }
  if (dam !== null) {
    const s = sfx(ctx, origin)
    const parts: string[] = []
    if (ratio !== null && ratio !== undefined) parts.push(`baseflow ratio Q90/Q50 = ${pyFloat(ratio)} (gage ${f!.site})`)
    parts.push(`upstream normal storage ${pyFixed(dam, 0)} m3/km2${s}`)
    const fvt = ratio !== null && ratio !== undefined ? `Baseflow ratio ${pyFloat(ratio)}` : `Dam storage ${pyFixed(dam, 0)} m3/km2${s}`
    const e = entry(ctx, mid, origin, dam, parts.join(', '), fvt, 'Compare to a reference or unregulated regime (TNC IHA) to judge alteration.')
    e.suggested_likert = null
    e.confidence = 'L'
    return e
  }
  if (ratio !== null && ratio !== undefined) {
    return ev(mid, {
      value_text: `baseflow ratio Q90/Q50 = ${pyFloat(ratio)} (gage ${f!.site})`, field_value_text: `Baseflow ratio ${pyFloat(ratio)}`,
      confidence: 'L', source: `USGS NWIS ${f!.site}`, source_url: `https://waterdata.usgs.gov/monitoring-location/${f!.site}/`,
      note: 'Compare to a reference or unregulated regime (TNC IHA) to judge alteration.',
    })
  }
  return missing(ctx, mid, 'upstream dam storage')
}

function evConcentratedInputs(ctx: Ctx): Evidence {
  const mid = 'reach-inflow-concentrated-flow-inputs'
  const [dens, origin] = wv(ctx, 'roadCrossingDensity')
  if (dens === null) return missing(ctx, mid, 'road-stream crossings')
  const s = sfx(ctx, origin)
  const n = origin === 'engine' ? eng(ctx, 'roadCrossings') : null
  const txt = (n !== null ? `${Math.trunc(Number(n))} road-stream crossing(s), ` : '') + `${f2(dens)} crossings/km2${s}`
  const e = entry(ctx, mid, origin, round(dens, 2), txt, `Road crossings ${f2(dens)}/km2${s}`,
    origin === 'engine'
      ? 'TIGERweb roads crossing the NHDPlus HR network in the watershed, a proxy for concentrated runoff inputs; outfalls and ditches need local layers.'
      : 'StreamCat road-stream crossings (rdcrs), a proxy for concentrated runoff inputs; outfalls and ditches need local layers.')
  e.suggested_likert = null
  e.confidence = 'L'
  return e
}

// ---------------------------------------------------------------- reach cross-sections
const XS_NOTE = 'Reach median of nine USGS 3DEP cross-sections on the assessment reach, bankfull from the Bieger 2015 regional regression. Screening geometry; the cross-section tool refines it.'

function evOverbankFrequency(ctx: Ctx): Evidence {
  const mid = 'high-flow-dynamics-overbank-flow-frequency'
  const raw = eng(ctx, 'bankHeightRatio')
  if (raw === null) return missing(ctx, mid, 'the bank-height ratio')
  const bhr = Number(raw)
  const e = engineEntry(ctx, mid, round(bhr, 2), `bank height ratio ${f2(bhr)} (reach median, 3DEP sections)`, `BHR ${f2(bhr)} (reach)`, XS_NOTE)
  e.suggested_likert = null
  e.confidence = 'L'
  return e
}

function evPeakCapacity(ctx: Ctx): Evidence {
  const mid = 'high-flow-dynamics-peak-flow-capacity-morphological-check'
  const er = eng(ctx, 'entrenchmentRatio')
  const w = eng(ctx, 'bankfullWidthM')
  if (er === null && w === null) return missing(ctx, mid, 'the reach cross-section geometry')
  const parts: string[] = []
  if (w !== null) parts.push(`regional bankfull width ${f1(Number(w))} m`)
  if (er !== null) parts.push(`entrenchment ratio ${f2(Number(er))} (reach median, 3DEP sections)`)
  const fvt = er !== null ? `ER ${f2(Number(er))} (reach)` : `Bankfull width ${f1(Number(w))} m`
  const e = engineEntry(ctx, mid, er !== null ? round(Number(er), 2) : round(Number(w), 1), parts.join(', '), fvt, XS_NOTE)
  e.suggested_likert = null
  e.confidence = 'L'
  return e
}

// ---------------------------------------------------------------- direct lookups, with the COMID-only indices
function integrityEntry(ctx: Ctx, mid: string, key: string, label: string, what: string): Evidence | null {
  const [cat, ws] = integrity(ctx, key)
  if (ws === null && cat === null) return null
  const lead = (ws !== null ? ws : cat) as number
  let txt = `${label} integrity ${f2(lead)}`
  if (ws !== null && cat !== null) txt = `${label} integrity ${f2(ws)} watershed, ${f2(cat)} catchment`
  txt += ' (EPA modeled index' + A.basinSuffix(anchorOf(ctx)).replace(' (', ', ')
  return streamcatEntry(ctx, mid, round(lead, 2), txt, `${label} integrity ${f2(lead)}`,
    `Modeled landscape-integrity index for the NHDPlus V2 reach, not ${what}. ` + INTEGRITY_TIERS, { fallback: false, suggestIt: false })
}

function integrityContext(ctx: Ctx, key: string, label: string): string {
  const [, ws] = integrity(ctx, key)
  const c = A.comid(anchorOf(ctx))
  if (ws === null || c === null) return ''
  return ` · ${label} integrity ${f2(ws)} (StreamCat, COMID ${c})`
}

function evNp(ctx: Ctx): Evidence {
  const mid = 'nutrient-cycling-n-p-concentrations'
  const tn = ctx.extras.tn ?? null
  const tp = ctx.extras.tp ?? null
  if (tn === null && tp === null) {
    const chem = integrityEntry(ctx, mid, 'chem', 'CHEM', 'an observed concentration')
    if (chem) return chem
    return ev(mid, { status: 'unavailable', source: 'Water Quality Portal', source_url: 'https://www.waterqualitydata.us/',
      note: 'No nearby WQP nutrient samples (2015+, within 5 mi).' })
  }
  const parts: string[] = []
  if (tn !== null) parts.push(`TN ${pyFloat(tn)} mg/L`)
  if (tp !== null) parts.push(`TP ${pyFloat(tp)} mg/L`)
  const short = [tn !== null ? `TN ${pyFloat(tn)}` : '', tp !== null ? `TP ${pyFloat(tp)}` : ''].filter(Boolean).join(' / ')
  return ev(mid, { value_text: 'observed median: ' + parts.join(', '), field_value_text: `${short} mg/L`, confidence: 'M',
    source: 'EPA/USGS Water Quality Portal', source_url: 'https://www.waterqualitydata.us/',
    note: 'Observed medians; compare to ecoregion reference criteria to score (reference added in Phase 4).' })
}

function evChannelPattern(ctx: Ctx): Evidence {
  const mid = 'channel-floodplain-dynamics-channel-pattern'
  const sin = ctx.sinuosity
  if (sin === null || sin === undefined) return ev(mid, { status: 'unavailable', source: 'NHDPlus flowline geometry' })
  return ev(mid, { value: sin, value_text: `sinuosity ${pyFloat(sin)}`, field_value_text: `Sinuosity ${pyFloat(sin)}`, confidence: 'L',
    source: 'NHDPlus flowline geometry', note: 'Compare planform to local reference (multi-date imagery / TopoView) to score.' })
}

function evDewatered(ctx: Ctx): Evidence {
  const mid = 'watershed-connectivity-dewatered-or-intermittent-segments'
  const fc = ctx.fcode
  if (fc === null || fc === undefined) {
    const hyd = integrityEntry(ctx, mid, 'hyd', 'HYD', 'the flow permanence code')
    if (hyd) return hyd
    return ev(mid, { status: 'unavailable', source: 'NHDPlus FCODE' })
  }
  const lab = FCODE_LABEL[fc] ?? `FCODE ${fc}`
  const hydCtx = integrityContext(ctx, 'hyd', 'HYD')
  let txt = `NHD flow permanence: ${lab}` + hydCtx
  const bfi = eng(ctx, 'baseflowIndexPct')
  const sources = ['NHDPlus FCODE']
  if (hydCtx) sources.push('StreamCat HYD')
  if (bfi !== null) {
    txt += ` · base-flow index ${pyFixed(Number(bfi), 0)}% (HR reach watershed)`
    sources.push('STAF site engine base-flow index')
  }
  return ev(mid, { value: fc, value_text: txt, field_value_text: `Flow class ${lab}`, confidence: 'M', source: sources.join(' + '),
    note: 'Natural intermittency is not artificial dewatering. Check NWIS zero-flow days and imagery. The HYD index is the EPA modeled '
      + 'hydrologic integrity of the NHDPlus V2 reach; the base-flow index is the share of streamflow that is base flow.' })
}

function evFlowPermanence(ctx: Ctx): Evidence {
  const mid = 'streamflow-regime-flow-permanence'
  const f = ctx.extras.flow
  if (!f) {
    const hyd = integrityEntry(ctx, mid, 'hyd', 'HYD', 'gaged flow')
    if (hyd) {
      hyd.note = 'No comparable nearby gage with daily flow. ' + hyd.note
      return hyd
    }
    return ev(mid, { status: 'unavailable', source: 'USGS NWIS', source_url: 'https://waterdata.usgs.gov/nwis',
      note: 'No comparable nearby gage with daily flow.' })
  }
  const z = f.zero_frac
  const sug = z <= 0.0 ? 'Strongly Agree' : z < 0.02 ? 'Agree' : z < 0.1 ? 'Disagree' : 'Strongly Disagree'
  return ev(mid, { value: z, value_text: `${f1(z * 100)}% zero-flow days at gage ${f.site} (${f.n_days} d)`,
    field_value_text: `Zero-flow ${f1(z * 100)}%`, confidence: 'M', source: `USGS NWIS ${f.site} ${f.name === null ? 'None' : f.name}`.trim(),
    source_url: `https://waterdata.usgs.gov/monitoring-location/${f.site}/`, note: 'Zero-flow-day fraction from the nearest comparable gage.',
    suggested_likert: sug })
}

function evFlowStatistics(ctx: Ctx): Evidence {
  const mid = 'streamflow-regime-flow-permanence-statistics'
  const f = ctx.extras.flow
  if (!f) return ev(mid, { status: 'unavailable', source: 'USGS NWIS', source_url: 'https://waterdata.usgs.gov/nwis' })
  return ev(mid, { value: f.q50, value_text: `Q10 ${pyFloat(f.q10)} / Q50 ${pyFloat(f.q50)} / Q90 ${pyFloat(f.q90)} cfs (gage ${f.site})`,
    field_value_text: `Q50 ${pyFloat(f.q50)} cfs`, confidence: 'M', source: `USGS NWIS ${f.site}`,
    source_url: `https://waterdata.usgs.gov/monitoring-location/${f.site}/`, note: 'Flow-duration percentiles (Qp = discharge exceeded p% of days).' })
}

function evArtificialStructures(ctx: Ctx): Evidence {
  const mid = 'streamflow-regime-artificial-structures-and-inputs'
  const nid = ctx.extras.nid
  if (nid === null || nid === undefined) return ev(mid, { status: 'unavailable', source: 'USACE NID / NLD', source_url: 'https://levees.sec.usace.army.mil/' })
  const n = nid.length
  const sug = n === 0 ? 'Strongly Agree' : n <= 1 ? 'Agree' : n <= 3 ? 'Disagree' : 'Strongly Disagree'
  return ev(mid, { value: n, value_text: `${n} dam(s) within ~1 mi (also check NLD levees)`, field_value_text: `Structures ${n} dam(s)`,
    confidence: 'M', source: 'USACE NID (dams) + National Levee Database', source_url: 'https://levees.sec.usace.army.mil/',
    note: 'Dams from NID; add levees from the National Levee Database (deep-link).', suggested_likert: sug })
}

function evBarriers(ctx: Ctx): Evidence {
  const mid = 'watershed-connectivity-upstream-and-downstream-barriers'
  const nid = ctx.extras.nid
  if (nid === null || nid === undefined) {
    const conn = integrityEntry(ctx, mid, 'conn', 'CONN', 'a barrier inventory')
    if (conn) {
      conn.note = 'The dam inventory did not answer near this reach. ' + conn.note
      return conn
    }
    return ev(mid, { status: 'unavailable', source: 'USACE NID / Aquatic Barrier Inventory', source_url: 'https://connectivity.sarpdata.com/' })
  }
  const n = nid.length
  const sug = n === 0 ? 'Strongly Agree' : n <= 1 ? 'Agree' : n <= 3 ? 'Disagree' : 'Strongly Disagree'
  const ctxTxt = integrityContext(ctx, 'conn', 'CONN')
  return ev(mid, { value: n, value_text: `${n} dam/barrier(s) within ~1 mi` + ctxTxt, field_value_text: `Barriers ${n} dam(s)`, confidence: 'M',
    source: 'USACE NID (+ National Aquatic Barrier Inventory)' + (ctxTxt ? ' + StreamCat CONN' : ''), source_url: 'https://connectivity.sarpdata.com/',
    note: 'Dam count near the reach; add road-crossing / aquatic barriers for passability.'
      + (ctxTxt ? ' The CONN index is the EPA modeled connectivity integrity of the NHDPlus V2 reach.' : ''),
    suggested_likert: sug })
}

function lateralFromStrips(ctx: Ctx, mid: string, w: WetlandsAlongReach, er: any): Evidence {
  const half = Math.trunc(Number(w.halfWidthM || 150))
  let txt = `${pyFloat(w.acres)} ac of NWI wetland within ${half} m of the reach (${pyFloat(w.pctOfStrip)}% of the strip)`
  const riverine = (w.bySystem ?? {}).riverine
  if (riverine) txt += `, ${pyFloat(riverine)} ac of it riverine`
  if (er !== null) txt += ` · entrenchment ratio ${f2(Number(er))} (reach median, 3DEP sections)`
  return ev(mid, { value: w.acres, value_text: txt, field_value_text: `Floodplain ${pyFloat(w.acres)} ac NWI within ${half} m`, confidence: 'L',
    source: 'USFWS National Wetlands Inventory (STAF data bundle)' + (er !== null ? ' + STAF site engine reach cross-sections' : ''),
    source_url: 'https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper',
    note: `NWI wetland area within ${half} m of the reach centerline, summed over the HR flowlines the reach runs along (each for the share of `
      + 'it the reach covers). Screening; confirm inundation with 3DEP.' })
}

function evLateralInundation(ctx: Ctx): Evidence {
  const mid = 'floodplain-connectivity-lateral-floodplain-inundation'
  const w = ctx.extras.nwi ?? null
  const er = eng(ctx, 'entrenchmentRatio')
  const strips = !!w && w.stripAcres !== null && w.stripAcres !== undefined
  if (strips && w!.acres) return lateralFromStrips(ctx, mid, w!, er)
  // the bundle's strips are the only NWI the static app reads: no strips is no answer
  const nwiText = w === null ? 'NWI did not answer' : `no NWI wetland within ${Math.trunc(Number(w.halfWidthM || 150))} m of the reach`
  if (er !== null) {
    const e = engineEntry(ctx, mid, round(Number(er), 2), `entrenchment ratio ${f2(Number(er))} (reach median, 3DEP sections); ${nwiText}`,
      `ER ${f2(Number(er))} (reach)`, XS_NOTE + ' Inspect NWI and the 3DEP hillshade for floodplain features.')
    e.suggested_likert = null
    e.confidence = 'L'
    return e
  }
  return ev(mid, { status: 'unavailable', source: 'USFWS NWI / 3DEP', source_url: 'https://www.fws.gov/program/national-wetlands-inventory/wetlands-mapper',
    note: (w === null ? 'NWI did not answer. ' : '') + 'Inspect NWI + 3DEP hillshade for floodplain features.' })
}

export const REGISTRY: [string, (ctx: Ctx) => Evidence][] = [
  ['catchment-hydrology-impervious-surface-area', evImpervious],
  ['catchment-hydrology-road-density', evRoadDensity],
  ['catchment-hydrology-impoundments', evImpoundments],
  ['surface-water-storage-wetland-coverage', evWetland],
  ['reach-inflow-concentrated-flow-inputs', evConcentratedInputs],
  ['light-thermal-regime-riparian-canopy-cover', evCanopy],
  ['carbon-processing-riparian-corridor-width-and-quality', evCorridor],
  ['nutrient-cycling-vegetated-riparian-corridor-width', evVegCorridor],
  ['community-dynamics-riparian-communities', evRiparianCommunities],
  ['sediment-continuity-transport-capacity', evTransportCapacity],
  ['nutrient-cycling-n-p-concentrations', evNp],
  ['channel-floodplain-dynamics-channel-pattern', evChannelPattern],
  ['watershed-connectivity-dewatered-or-intermittent-segments', evDewatered],
  ['catchment-hydrology-land-use-change', evLandUseChange],
  ['streamflow-regime-flow-permanence', evFlowPermanence],
  ['streamflow-regime-flow-permanence-statistics', evFlowStatistics],
  ['streamflow-regime-channel-natural-flow-regime', evNaturalFlowRegime],
  ['streamflow-regime-artificial-structures-and-inputs', evArtificialStructures],
  ['watershed-connectivity-upstream-and-downstream-barriers', evBarriers],
  ['floodplain-connectivity-lateral-floodplain-inundation', evLateralInundation],
  ['high-flow-dynamics-overbank-flow-frequency', evOverbankFrequency],
  ['high-flow-dynamics-peak-flow-capacity-morphological-check', evPeakCapacity],
]

export interface PullData {
  src: DataSource
  hr: Dataset
  v2: Dataset
  values: ValueTables
  points: PointTables
}

/** What the pull reads, fetched at once (the Python's asyncio.gather): each answer or null. */
export interface Prefetched {
  nid: unknown[] | null
  tn: number | null
  tp: number | null
  flow: FlowStats | null
  nwi: WetlandsAlongReach | null
  streamcat: Record<string, any>
}

const quiet = <T>(p: Promise<T>): Promise<T | null> => p.catch(() => null)

export async function prefetch(d: PullData, ci: Record<string, any>, state: EngineState, anchor: Record<string, any> | null): Promise<Prefetched> {
  const lat = Number(ci.lat)
  const lon = Number(ci.lon)
  const c = A.comid(anchor)
  const [nid, tn, tp, flow, nwi, row] = await Promise.all([
    quiet(d.points.nidBox(lat, lon, 1.0)),
    quiet(d.points.wqpSfari('tn', lat, lon)),
    quiet(d.points.wqpSfari('tp', lat, lon)),
    quiet(d.points.nwisFlowStats(lat, lon, ci.drainage_area_sqkm)),
    // the record is kept without its geometry; the reach line rides in ctx_inputs
    quiet(wetlandsAlongReach(d.hr, d.values, state.record, ci.reach_geojson)),
    c !== null ? quiet(streamcatRow(d.src, d.v2, c, STREAMCAT_NAMES)) : Promise.resolve(null),
  ])
  return { nid, tn, tp, flow, nwi, streamcat: row ?? {} }
}

/** The anchor the pull uses: the classification the app attached, else a covered-reach anchor from a
 * bare COMID (evidence._resolve_anchor). */
export function resolveAnchor(ci: Record<string, any>): Record<string, any> | null {
  const a = ci.siteAnchor
  if (a && typeof a === 'object' && A.comid(a) !== null) return a
  return A.synthetic(ci.comid)
}

/** The adapters over prefetched answers: ``{metricId: evidence}`` in the registry's order. */
export function evaluate(ci: Record<string, any>, state: EngineState, anchor: Record<string, any> | null, got: Prefetched,
                         progress?: { done: number, total: number }): Record<string, Evidence> {
  const ctx: Ctx = {
    lat: ci.lat, lon: ci.lon, comid: ci.comid ?? null, huc8: ci.huc8 ?? null, reach_geojson: ci.reach_geojson ?? null,
    drainage_area_sqkm: ci.drainage_area_sqkm ?? null, slope: ci.slope ?? null, fcode: ci.fcode ?? null,
    stream_order: ci.stream_order ?? null, sinuosity: ci.sinuosity ?? null,
    extras: { engine: state, engine_metrics: A.engineMetrics(state.record), site_anchor: anchor, ...got },
  }
  if (progress) {
    progress.total = REGISTRY.length
    progress.done = 0
  }
  const out: Record<string, Evidence> = {}
  for (const [mid, fn] of REGISTRY) {
    try {
      out[mid] = fn(ctx)
    } catch (err) {
      out[mid] = ev(mid, { status: 'unavailable', note: `adapter error: ${err instanceof Error ? err.message : err}` })
    }
    if (progress) progress.done = Object.keys(out).length
  }
  return out
}

/** evidence.pull with the app's engine state: ``{metricId: evidence}``. */
export async function pull(d: PullData, ci: Record<string, any>, state: EngineState,
                           progress?: { done: number, total: number }): Promise<Record<string, Evidence>> {
  const anchor = resolveAnchor(ci)
  const got = await prefetch(d, ci, state, anchor)
  return evaluate(ci, state, anchor, got, progress)
}

/** pipeline.merge_pulled_evidence: a fresh pull replaces the automatic entries and keeps the ones the
 * cross-section tool attached. */
export const XS_MANNING_SOURCE = 'Native cross-section hydraulics (Manning)'
export function mergePulledEvidence(existing: Record<string, any>, pulled: Record<string, any>): Record<string, any> {
  const merged = { ...pulled }
  for (const [mid, e] of Object.entries(existing ?? {})) {
    if (e && typeof e === 'object' && e.source === XS_MANNING_SOURCE) merged[mid] = e
  }
  return merged
}

// EASI's watershed evidence layer (easi/watershed.py): one canonical key set behind the two STAF
// engines, so the eight watershed metrics read StreamCat's row on the covered network and the STAF
// site engine's HR reach watershed on a routed site, with each engine's labels. The key and label
// tables are watershed.py's own (content/easi/config.json).
import { pySum } from '../../app/pyfmt'
import { pyFloatOf, pyRound } from './evaluator'
import { APP } from './method'

export const STREAMCAT = 'streamcat'
export const SITE_ENGINE = 'site-engine'
const T = APP.watershed
export const VALUE_KEYS: string[] = T.VALUE_KEYS
export const GUIDANCE_UNAVAILABLE: string = T.GUIDANCE_UNAVAILABLE
const ACRE_FT = T.ACRE_FT_PER_KM2_TO_M3_PER_KM2 as number
export const ENGINE_FAMILIES: string[] = T.ENGINE_FAMILIES

export interface Layer {
  provider: string | null
  label: string
  values: Record<string, number | null>
  inputSources: Record<string, string>
  resultSources: Record<string, string>
  notes: Record<string, string>
  meta: Record<string, any>
  unavailableReason: string | null
}

/** _num: float(value), or None for None, booleans and anything float() refuses. */
const num = (value: any): number | null =>
  (value === null || value === undefined || typeof value === 'boolean' ? null : pyFloatOf(value))

const DISPLAY: Record<string, string> = { [STREAMCAT]: 'StreamCat lookup engine', [SITE_ENGINE]: 'STAF site engine' }
export const displayName = (token: string): string => DISPLAY[token] ?? token
/** naming.engine_label: ``STAF site engine v0.5.0`` */
export const engineLabel = (version: string | null | undefined, fallback: string): string => `STAF site engine v${version || fallback}`

/** from_streamcat: the StreamCat lookup engine, today's values and labels. */
export function fromStreamcat(row: Record<string, any> | null | undefined): Layer {
  const s = row || {}
  const values: Record<string, number | null> = Object.fromEntries(VALUE_KEYS.map((k) => [k, null]))
  for (const [key, col] of Object.entries(T._STREAMCAT_COLUMNS as Record<string, string>)) values[key] = num(s[col])
  return {
    provider: STREAMCAT, label: displayName(STREAMCAT), values,
    inputSources: { ...T.STREAMCAT_INPUT_SOURCES }, resultSources: { ...T.STREAMCAT_RESULT_SOURCES },
    notes: {}, meta: {}, unavailableReason: null,
  }
}

/** from_engine: the STAF site engine's HR reach watershed values from a compute_site record. */
export function fromEngine(record: Record<string, any>, version: string | null, fallbackVersion: string): Layer {
  const metrics = (record || {}).metrics || {}
  const ver = version || (record || {}).engineVersion || null
  const values: Record<string, number | null> = Object.fromEntries(VALUE_KEYS.map((k) => [k, null]))
  for (const [key, engineKey] of Object.entries(T._ENGINE_KEYS as Record<string, string>)) {
    let v = num((metrics[engineKey] || {}).value)
    if (key === 'damStorageM3PerSqkm' && v !== null) v = pyRound(v * ACRE_FT, 3)
    values[key] = v
  }
  const label = engineLabel(ver, fallbackVersion)
  const ws = (record || {}).watershed || {}
  const detail = (table: Record<string, string>) => Object.fromEntries(Object.entries(table).map(([k, d]) => [k, `${label}, HR reach watershed: ${d}`]))
  return {
    provider: SITE_ENGINE, label, values,
    inputSources: detail(T._ENGINE_INPUT_DETAILS), resultSources: detail(T._ENGINE_RESULT_DETAILS),
    notes: { ...T._ENGINE_NOTES },
    meta: { engineVersion: ver, areaSqkm: ws.areaSqkm ?? null, vaaAreaSqkm: ws.vaaAreaSqkm ?? null, areaAgreement: ws.areaAgreement ?? null,
      nReaches: ws.nReaches ?? null, nHops: ws.nHops ?? null },
    unavailableReason: null,
  }
}

/** unavailable: no watershed evidence, every value None, the reason kept. */
export function unavailableLayer(reason: string | null | undefined): Layer {
  return {
    provider: null, label: 'watershed evidence unavailable', values: Object.fromEntries(VALUE_KEYS.map((k) => [k, null])),
    inputSources: Object.fromEntries(Object.keys(T.STREAMCAT_INPUT_SOURCES).map((k) => [k, ''])),
    resultSources: Object.fromEntries(Object.keys(T.STREAMCAT_RESULT_SOURCES).map((k) => [k, ''])),
    notes: {}, meta: {}, unavailableReason: reason || 'the HR reach watershed could not be calculated',
  }
}

/** build: the layer for one run, decided by the engine block on the context. */
export function build(extras: Record<string, any>, streamcatRow: Record<string, any> | null, fallbackVersion: string): Layer {
  const eng = extras.watershedEngine || {}
  const status = eng.status
  if (status === 'ok' && eng.record) return fromEngine(eng.record, eng.engineVersion ?? null, fallbackVersion)
  if (['failed', 'refused', 'unavailable'].includes(status)) return unavailableLayer(eng.reason || status)
  return fromStreamcat(streamcatRow)
}

/** wants_nlcd_fallback: the NLCD outage fallback belongs to the StreamCat lookup engine only (the
 * static app has no NLCD service: its landcover stays empty). */
export function wantsNlcdFallback(extras: Record<string, any>): boolean {
  const status = (extras.watershedEngine || {}).status
  return !['ok', 'failed', 'refused', 'unavailable'].includes(status)
}

// ---- reads

export const layer = (extras: Record<string, any>): Layer => extras.watershed || fromStreamcat(extras.streamcat || {})
export const provider = (extras: Record<string, any>) => layer(extras).provider
export const value = (extras: Record<string, any>, key: string): number | null => layer(extras).values[key] ?? null
export const inputSource = (extras: Record<string, any>, key: string): string => layer(extras).inputSources[key] ?? ''
export const resultSource = (extras: Record<string, any>, key: string): string => layer(extras).resultSources[key] ?? ''
export const resultNote = (extras: Record<string, any>, key: string, dflt: string): string => layer(extras).notes[key] || dflt

/** guidance: the plain guidance when the watershed evidence itself is unavailable, else the note. */
export function guidance(extras: Record<string, any>, dflt: string): string {
  const lyr = layer(extras)
  if (lyr.provider === null) return GUIDANCE_UNAVAILABLE.replace('{reason}', String(lyr.unavailableReason))
  return dflt
}

/** ag_pct: crop + hay, both required, rounded to two places. */
export function agPct(lyr: Layer): number | null {
  const vals = [lyr.values.cropPct, lyr.values.hayPct]
  if (vals.some((v) => v === null || v === undefined)) return null
  return pyRound(pySum(vals as number[]), 2)
}

/** forest_pct: the three StreamCat forest classes summed when all are present, else the engine's merged class. */
export function forestPct(lyr: Layer, ndigits: number): number | null {
  const v = lyr.values
  const comps = [v.ripConifPct, v.ripDecidPct, v.ripMixedPct]
  if (comps.every((c) => c !== null && c !== undefined)) return pyRound(pySum(comps as number[]), ndigits)
  const merged = v.ripForestPct
  return merged === null || merged === undefined ? null : pyRound(merged, ndigits)
}

export const riparianForestPct = (lyr: Layer) => forestPct(lyr, 2)

export interface VegBreakdown { forest: number, shrub: number, grassland: number, wetland: number, total: number }

export function riparianVegBreakdown(lyr: Layer): VegBreakdown | null {
  const v = lyr.values
  const forest = forestPct(lyr, 1)
  if (forest === null) return null
  const groups: Record<string, number> = { forest }
  for (const [grp, keys] of [['shrub', ['ripShrubPct']], ['grassland', ['ripGrasslandPct']], ['wetland', ['ripWoodyWetlandPct', 'ripHerbWetlandPct']]] as [string, string[]][]) {
    const vals = keys.map((k) => v[k])
    if (vals.some((x) => x === null || x === undefined)) return null
    groups[grp] = pyRound(pySum(vals as number[]), 1)
  }
  groups.total = pyRound(pySum(['forest', 'shrub', 'grassland', 'wetland'].map((g) => groups[g])), 1)
  return groups as unknown as VegBreakdown
}

export interface WoodyBreakdown { forest: number, shrub: number, woodyWetland: number, total: number }

export function riparianWoodyBreakdown(lyr: Layer): WoodyBreakdown | null {
  const v = lyr.values
  const forest = forestPct(lyr, 1)
  if (forest === null) return null
  const out: Record<string, number> = { forest }
  for (const [grp, key] of [['shrub', 'ripShrubPct'], ['woodyWetland', 'ripWoodyWetlandPct']]) {
    const x = v[key]
    if (x === null || x === undefined) return null
    out[grp] = pyRound(x, 1)
  }
  out.total = pyRound(pySum(Object.values(out)), 1)
  return out as unknown as WoodyBreakdown
}

// EASI's scoring method: the adopted version of ``easi-screening`` in the STAF assessment library
// (adopted_method.py, owner decision D20). The Shiny EASI reads the pinned method package from the
// ``library`` release; this reads the same package from the library mirror, checks it against the pin
// the app keeps (content/easi/adopted-method.json: the package file's sha256, its method files' digest),
// and scores with its method files (method_package.METHOD_FILES). Nothing else is ever substituted.
//
// The app's own tables (config.py's metric registry, definitions and calculation notes, the watershed
// layer's keys and labels) are kept in content/easi/config.json, written by parity/copy_page.py.
import pinJson from '../../app/content/easi/adopted-method.json'
import appJson from '../../app/content/easi/config.json'
import { parseJson } from '../../app/pyjson'
import type { DataSource } from '../../data/source'

export interface Pin {
  assessmentId: string
  version: number
  methodVersion: string
  packageDigest: string
  evaluatorDigest: string
  package: { name: string, sha256: string, bytes: number }
}

export const PIN = pinJson as Pin
export const APP = appJson as {
  criteriaSet: string
  registry: Record<string, { scale: string, confidence: string, proxy: boolean, overrideable: boolean, datasource: string }>
  definitions: Record<string, string>
  regionalDefinitions: Record<string, string>
  calculations: Record<string, string>
  watershed: Record<string, any>
}

/** method_package.METHOD_FILES */
export const METHOD_FILES = ['screening-methods.json', 'reference-curves.json', 'easi-metrics.json', 'cwa-mapping.json',
  'functions.json', 'ecoregion-crosswalk.json', 'scoring-identity.json', 'nars-ecoregions-9.geojson.gz'] as const

export interface EasiMethod {
  pin: Pin
  envelope: any
  catalog: any
  curves: any
  metrics: any
  functions: any[]
  cwa: any[]
  crosswalk: any
  identity: any
  /** the gzipped NARS nine-region polygons, parsed on first use (geo.nars9_at) */
  nars9Gz: Uint8Array
  calculator: Uint8Array | null
}

// ---------------------------------------------------------------- the package file

async function inflate(data: Uint8Array, format: 'deflate-raw' | 'gzip'): Promise<Uint8Array> {
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream(format))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

export const gunzip = (data: Uint8Array) => inflate(data, 'gzip')

/** The members of a ZIP file (stored or deflated), by name. */
export async function readZip(buf: ArrayBuffer): Promise<Map<string, Uint8Array>> {
  const dv = new DataView(buf)
  const u8 = new Uint8Array(buf)
  let eocd = -1
  for (let i = buf.byteLength - 22; i >= Math.max(0, buf.byteLength - 22 - 65535); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('not a ZIP file')
  const count = dv.getUint16(eocd + 10, true)
  let p = dv.getUint32(eocd + 16, true)
  const out = new Map<string, Uint8Array>()
  const text = new TextDecoder()
  for (let k = 0; k < count; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error('a damaged ZIP directory')
    const method = dv.getUint16(p + 10, true)
    const csize = dv.getUint32(p + 20, true)
    const nlen = dv.getUint16(p + 28, true)
    const xlen = dv.getUint16(p + 30, true)
    const clen = dv.getUint16(p + 32, true)
    const local = dv.getUint32(p + 42, true)
    const name = text.decode(u8.subarray(p + 46, p + 46 + nlen))
    p += 46 + nlen + xlen + clen
    const start = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true)
    const raw = u8.subarray(start, start + csize)
    if (method === 0) out.set(name, raw.slice())
    else if (method === 8) out.set(name, await inflate(raw, 'deflate-raw'))
    else throw new Error(`${name}: ZIP method ${method} is not supported`)
  }
  return out
}

async function sha256Hex(data: Uint8Array | ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data as BufferSource)
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** method_package.package_digest: ``sha256:`` of the canonical JSON of ``{"files": {name: sha256}}``. */
export async function packageDigest(files: Record<string, string>): Promise<string> {
  const sorted = Object.keys(files).sort().map((k) => `${JSON.stringify(k)}:${JSON.stringify(files[k])}`).join(',')
  return 'sha256:' + await sha256Hex(new TextEncoder().encode(`{"files":{${sorted}}}`))
}

let current: EasiMethod | null = null
let loading: Promise<EasiMethod> | null = null

/** The adopted method, read once from the library mirror and verified (adopted_method.resolve's release
 * branch: the file must be the pin's bytes, its method files the pinned ones). */
export function loadMethod(src: DataSource): Promise<EasiMethod> {
  if (current) return Promise.resolve(current)
  loading ??= (async () => {
    await src.ready()
    const logical = `library/${PIN.package.name}`
    const entry = src.entry(logical)
    if (entry.sha256.toLowerCase() !== PIN.package.sha256.toLowerCase()) {
      throw new Error(`EASI cannot verify its adopted method ${PIN.assessmentId} v${PIN.version}: the library lists another file`)
    }
    const blob = await src.bytesOf(logical)
    if (await sha256Hex(blob) !== PIN.package.sha256.toLowerCase()) {
      throw new Error(`EASI cannot verify its adopted method ${PIN.assessmentId} v${PIN.version}: the package file's sha256 is not the pin's`)
    }
    const zip = await readZip(blob)
    const file = (name: string) => {
      const got = zip.get(name)
      if (!got) throw new Error(`the method package lacks ${name}`)
      return got
    }
    const sums: Record<string, string> = {}
    for (const name of METHOD_FILES) sums[name] = await sha256Hex(file(`method/${name}`))
    if (await packageDigest(sums) !== PIN.packageDigest) {
      throw new Error(`EASI cannot verify its adopted method ${PIN.assessmentId} v${PIN.version}: the package's method files are not the pinned ones`)
    }
    const json = (name: string) => parseJson(new TextDecoder().decode(file(`method/${name}`)), { order: true })
    const envelope = parseJson(new TextDecoder().decode(file('method.json')))
    const calcName = envelope?.calculator?.name
    current = {
      pin: PIN, envelope, catalog: json('screening-methods.json'), curves: json('reference-curves.json'),
      metrics: json('easi-metrics.json'), functions: json('functions.json'), cwa: json('cwa-mapping.json'),
      crosswalk: json('ecoregion-crosswalk.json'), identity: json('scoring-identity.json'),
      nars9Gz: file('method/nars-ecoregions-9.geojson.gz'),
      calculator: calcName ? zip.get(`calculator/${calcName}`) ?? null : null,
    }
    return current
  })().catch((err) => {
    loading = null
    throw err
  })
  return loading
}

/** The loaded method (loadMethod first). */
export function method(): EasiMethod {
  if (!current) throw new Error('the EASI method is not loaded')
  return current
}

export const methodLoaded = (): boolean => current !== null

// ---------------------------------------------------------------- config.py

/** config.RATING_INDEX */
export const RATING_INDEX: Record<string, number> = { Good: 0.85, Fair: 0.545, Poor: 0.195 }
export const RATINGS = ['Good', 'Fair', 'Poor'] as const
export const WEIGHTS: Record<string, number> = { D: 1.0, i: 0.1, '-': 0.0 }
export const OUTCOMES = ['physical', 'chemical', 'biological'] as const
export const FUNCTION_SCORE_MAX = 15
export const INDEX_BANDS: [number, string][] = [[0.39, '#f5b5b5'], [0.69, '#f5e7a6'], [1.01, '#c8d9f2']]
export const INDEX_BAND_LABELS = ['Non-Functioning', 'Functioning-at-Risk', 'Functioning']
export const FUNCTION_SCORE_BANDS: [number, string][] = [[5, '#f5b5b5'], [10, '#f5e7a6'], [FUNCTION_SCORE_MAX, '#c8d9f2']]
export const FUNCTION_SCORE_BAND_SHORT = ['NF', 'AR', 'F']

/** config.cwa_mapping: function id -> {physical, chemical, biological} contribution codes. */
export function cwaMapping(): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {}
  for (const r of method().cwa) out[r.id] = Object.fromEntries(OUTCOMES.map((k) => [k, r[k]]))
  return out
}

export const functions = (): any[] => method().functions

/** config.metrics_by_id, in easi-metrics.json's order. */
export function metricsById(): Record<string, any> {
  const out: Record<string, any> = {}
  for (const m of method().metrics.metrics) out[m.metricId] = m
  return out
}

/** config.metric_registry (the regional criteria's metadata). */
export const metricRegistry = () => APP.registry

/** config.metric_definition */
export function metricDefinition(mid: string, fallback?: string | null): string {
  if (mid in APP.regionalDefinitions) return APP.regionalDefinitions[mid]
  return fallback !== undefined && fallback !== null ? fallback : APP.definitions[mid] ?? ''
}

/** config.metric_calculation */
export const metricCalculation = (mid: string): string | null => APP.calculations[mid] ?? null

/** config.scoring_identity (the regional criteria with the package's identity file) */
export const scoringIdentity = () => ({ ...method().identity })

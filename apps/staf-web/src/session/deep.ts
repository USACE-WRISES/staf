// The DEEP assessment file (deep/session.py): the header's Save and Open, and the provenance it records.
//
// A whole DEEP run in one file: the delineation, the assessment it was scored against (inlined with its
// curves, so the file resumes on its own) with its provenance (the resolved site region, the version and
// its status, a content digest of the inlined bundle), and every scenario's measured values. Scores are
// recomputed on Open, never read from the file. Files from before the shared format (schema v2, and v1
// or version-less ones, whose provenance is rebuilt from the bundle) still open.
import { pyCopy, pyDumps } from '../app/pyjson'
import { sha256Hex } from '../files/bytes'
import { AssessmentFileError, dump as dumpFile, legacyScenarios, parse } from './file'

type Obj = Record<string, any>
export const TOOL = 'DEEP'
export const SCHEMA_VERSION = 2
export const LIFECYCLES = ['draft', 'preliminary', 'certified']
const isObj = (v: unknown): v is Obj => v !== null && typeof v === 'object' && !Array.isArray(v)

/** lifecycle_status: draft, preliminary or certified (preliminary when the bundle names none it runs). */
export function lifecycleStatus(bundle: Obj | null): string {
  const lib = (bundle ?? {}).library ?? {}
  for (const src of [bundle ?? {}, lib]) {
    for (const key of ['lifecycle', 'status']) {
      const v = src?.[key]
      if (v) {
        const s = String(v).trim().toLowerCase()
        if (LIFECYCLES.includes(s)) return s
      }
    }
  }
  return 'preliminary'
}

/** content_digest: ``sha256:`` over the bundle's canonical JSON (sorted keys, compact, UTF-8); "" for none. */
export function contentDigest(bundle: Obj | null): string {
  if (!bundle || !Object.keys(bundle).length) return ''
  return 'sha256:' + sha256Hex(pyDumps(bundle, { sortKeys: true, separators: [',', ':'], ensureAscii: false }))
}

/** bundle_digest: the publisher's digest when the bundle carries one, else the content digest. */
export function bundleDigest(bundle: Obj | null): string {
  const lib = (bundle ?? {}).library ?? {}
  for (const src of [bundle ?? {}, lib]) {
    for (const key of ['contentDigest', 'digest', 'fingerprint']) {
      const v = src?.[key]
      if (v) return typeof v === 'string' ? v : String(v)
    }
  }
  return contentDigest(bundle)
}

/** _provenance */
export function provenance(bundle: Obj | null, region: Obj | null, completeness: unknown = null, resultState: unknown = null): Obj {
  const lib = (bundle ?? {}).library ?? {}
  return {
    assessmentId: (bundle ?? {}).assessmentId ?? '',
    version: lib.version ?? null,
    lifecycle: lifecycleStatus(bundle),
    contentDigest: bundleDigest(bundle),
    region: region || { level3: null, state: null },
    completeness,
    resultState,
  }
}

/** dump: the file; ``scenarios`` is scenariosBlock's (every scenario's measured values). */
export function dump(delineation: Obj | null, assessment: Obj | null, scenarios: Obj, region: Obj | null = null): string {
  const bundle = assessment ?? {}
  return dumpFile(TOOL, delineation ?? {}, { assessment: bundle, provenance: provenance(bundle, region) }, scenarios)
}

/** _migrate: a v1 or version-less file brought up to schema 2 (its provenance from the bundle). */
function migrate(d: Obj, fromVersion: number): Obj {
  const out = pyCopy(d)
  if (fromVersion <= 1) {
    const bundle = out.assessment || {}
    const prov = provenance(bundle, null)
    prov.migratedFrom = fromVersion
    out.provenance = prov
  }
  out.schemaVersion = SCHEMA_VERSION
  return out
}

/** int(x) as Python takes it from a file's value, or NaN. */
function pyInt(v: unknown): number {
  if (typeof v === 'number') return Number.isFinite(v) ? Math.trunc(v) : NaN
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'string' && /^\s*[+-]?\d+\s*$/.test(v)) return Number(v.trim())
  return NaN
}

/** _legacy: a file from before the shared format, in its shape. */
function legacy(d: Obj): Obj {
  const version = pyInt(d.schemaVersion || 1)
  if (Number.isNaN(version)) throw new AssessmentFileError('the file has no valid schema version.')
  const m = version < SCHEMA_VERSION ? migrate(d, version) : d
  return {
    delineation: 'delineation' in m ? m.delineation : {},
    toolData: { assessment: m.assessment || {}, provenance: m.provenance || {} },
    scenarios: legacyScenarios(m.scenarios ?? null, { measured_values: m.measured_values || {} }),
  }
}

export interface Loaded { delineation: Obj, assessment: Obj, provenance: Obj, scenarios: Obj | null }

/** load: a saved run (missing blocks read empty). Throws AssessmentFileError. */
export function load(text: string): Loaded {
  const st = parse(text, TOOL, legacy)
  const data = st.toolData
  return {
    delineation: st.delineation,
    assessment: isObj(data.assessment) ? data.assessment : {},
    provenance: isObj(data.provenance) ? data.provenance : {},
    scenarios: st.scenarios,
  }
}

/** _session_ref: the "id@vN" a restored session was scored against, or null. */
export function sessionRef(st: { provenance?: Obj | null }, raw: Obj | null): string | null {
  const prov = (st ?? {}).provenance ?? {}
  const aid = prov.assessmentId || (raw ?? {}).assessmentId || ''
  const ver = prov.version || ((raw ?? {}).library ?? {}).version
  if (!aid) return null
  return ver ? `${aid}@v${typeof ver === 'number' ? ver : String(ver)}` : String(aid)
}

const toFloat = (v: unknown): number => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN)

/** _valid_lookup_point */
export function validLookupPoint(point: unknown[] | null): boolean {
  if (!point) return false
  const lat = toFloat(point[0])
  const lon = toFloat(point[1])
  return Number.isFinite(lat) && Number.isFinite(lon) && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180
}

/** _saved_lookup_point: the assessment point, an HR site kept apart from its source reach. */
export function savedLookupPoint(d: Obj, comidOf: (a: unknown) => number | null, routed: (a: Obj) => boolean): [number, number, number, number | null] | null {
  const dd = isObj(d.delineation) ? d.delineation : {}
  const ci = isObj(d.ctx_inputs) ? d.ctx_inputs : {}
  const top = isObj(d.siteAnchor) ? d.siteAnchor : {}
  const context = isObj(ci.siteAnchor) ? ci.siteAnchor : {}
  const anchor = [top, context].find((a) => comidOf(a) !== null) ?? (Object.keys(top).length ? top : context)
  const clicked = isObj(anchor.clickedStream) ? anchor.clickedStream : {}
  const scored = isObj(anchor.scoredReach) ? anchor.scoredReach : {}
  const choices: unknown[][] = [[dd.snapped_lat, dd.snapped_lon], [clicked.snapLat, clicked.snapLon], [ci.lat, ci.lon]]
  if (!routed(anchor)) choices.push([scored.snapLat, scored.snapLon])
  for (const point of choices) {
    if (validLookupPoint(point)) return [toFloat(point[0]), toFloat(point[1]), 0.0, dd.nhdplus_id || clicked.nhdplusId || null]
  }
  return null
}

/** _own_entries: the assessor's own entries (desktop values the app fills in by itself do not count). */
export function ownEntries(measured: Obj | null): Obj {
  const out: Obj = {}
  for (const [mid, v] of Object.entries(measured ?? {})) {
    if (isObj(v) && (v.origin === 'field' || v.na || v.note || (Array.isArray(v.photos) ? v.photos.length : v.photos) || v.stratumAuto === false)) out[mid] = v
  }
  return out
}

// DEEP's assessment for a delineated site (deep/assessments.py covering_refs; app.py _resolve_assessment,
// _assessment_facts, assess_pane): the assessments whose region holds the point, the one Basin adopts
// (the first's default version), and what the pane states about it. The library comes from the data
// mirror: the catalog (library.json, read as deep/remote_library.py reads it) and the version's bundle.
import type { DataSource } from '../data/source'
import { deepAssessPane, deepNoAssessment } from './markup'

const ELIGIBLE = ['draft', 'preliminary', 'certified']
const TIER_ORDER = ['certified', 'preliminary', 'draft']
const HIDDEN_ID_SUFFIXES = ['-sqt-adapted']
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/
const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft', preliminary: 'Preliminary', under_review: 'Under review', certified: 'Final', revised: 'Revised', retired: 'Retired',
}
const BADGE_CLASS: Record<string, string> = { draft: 'deep-badge-draft', preliminary: 'deep-badge-prelim', certified: 'deep-badge-cert' }
const TIER_LABELS: Record<string, string> = {
  minimally_disturbed: 'Minimally disturbed', least_disturbed: 'Least disturbed', best_available: 'Best available (fallback)',
}
/** The STAF framework's functions (deep config.functions_by_id), for a bundle that declares no coverage. */
const FRAMEWORK_FUNCTIONS = 20

interface Version { id: string, version: number, status: string, bundle: string }
export interface Covering {
  assessmentId: string
  defaultRef: string
  refs: string[]
  lifecycleByRef: Record<string, string>
  versionByRef: Record<string, number>
  hasCertified: boolean
}
type Feature = { assessmentId: string, version?: number | null, geometry?: { type: string, coordinates: any } }

const asInt = (v: unknown): number | null => {
  if (typeof v === 'boolean') return null
  if (typeof v === 'number' && Number.isInteger(v)) return v
  if (typeof v === 'string' && /^\s*\d+\s*$/.test(v)) return Number(v.trim())
  return null
}

let catalogP: Promise<Version[]> | null = null

/** The catalog's eligible versions (deep_regions.eligible_versions): DEEP's type, a status DEEP runs,
 * Show in DEEP not off, a bundle; the hidden assessments left out. */
function catalog(src: DataSource): Promise<Version[]> {
  catalogP ??= (async () => {
    const doc = JSON.parse(new TextDecoder().decode(await src.bytesOf('library/library.json')))
    const out: Version[] = []
    const seen = new Set<string>()
    for (const entry of doc.assessments ?? []) {
      const aid = entry?.id
      if (typeof aid !== 'string' || !ID_RE.test(aid) || seen.has(aid)) continue
      if (String(entry.type || entry.assessmentType || 'deep') !== 'deep') continue
      seen.add(aid)
      const byNumber = new Map<number, Version | null>()
      for (const raw of Array.isArray(entry.versions) ? entry.versions : []) {
        const number = asInt(raw?.version)
        const status = typeof raw?.status === 'string' ? raw.status.trim().toLowerCase() : ''
        if (number === null || number < 1 || !status || byNumber.has(number)) continue
        const visible = raw.visibleInDeep !== false
        const bundle = raw.assets?.bundle?.name
        if (ELIGIBLE.includes(status) && visible && typeof bundle !== 'string') continue
        byNumber.set(number, ELIGIBLE.includes(status) && visible ? { id: aid, version: number, status, bundle } : null)
      }
      for (const n of [...byNumber.keys()].sort((a, b) => a - b)) {
        const v = byNumber.get(n)
        if (v && !HIDDEN_ID_SUFFIXES.some((s) => aid.endsWith(s))) out.push(v)
      }
    }
    return out
  })()
  catalogP.catch(() => { catalogP = null })
  return catalogP
}

function pointInRing(x: number, y: number, ring: number[][]): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function pointInPolygon(x: number, y: number, rings: number[][][]): boolean {
  if (!rings?.length || !pointInRing(x, y, rings[0])) return false
  return !rings.slice(1).some((h) => pointInRing(x, y, h))
}

/** assessments._point_in_geometry. */
function pointInGeometry(lon: number, lat: number, geom: Feature['geometry']): boolean {
  if (!geom) return false
  if (geom.type === 'Polygon') return pointInPolygon(lon, lat, geom.coordinates)
  if (geom.type === 'MultiPolygon') return geom.coordinates.some((p: number[][][]) => pointInPolygon(lon, lat, p))
  return false
}

/** covering_refs: per covering assessment, its eligible refs (certified, then preliminary, then
 * draft, newest first; the default first), assessments with a Final version first. */
export async function coveringRefs(src: DataSource, regions: Feature[], lat: number, lon: number): Promise<Covering[]> {
  const versions = await catalog(src)
  const out: Covering[] = []
  for (const f of regions) {
    if (!pointInGeometry(lon, lat, f.geometry)) continue
    const recs = versions.filter((v) => v.id === f.assessmentId)
    if (!recs.length) continue
    const ordered = [...recs].sort((a, b) => TIER_ORDER.indexOf(a.status) - TIER_ORDER.indexOf(b.status) || b.version - a.version)
    const defaultVersion = recs.find((r) => r.version === f.version) ? f.version : ordered[0].version
    const defaultRef = `${f.assessmentId}@v${defaultVersion}`
    let refs = ordered.map((r) => `${r.id}@v${r.version}`)
    if (refs.includes(defaultRef)) refs = [defaultRef, ...refs.filter((r) => r !== defaultRef)]
    out.push({
      assessmentId: f.assessmentId, defaultRef, refs,
      lifecycleByRef: Object.fromEntries(ordered.map((r) => [`${r.id}@v${r.version}`, r.status])),
      versionByRef: Object.fromEntries(ordered.map((r) => [`${r.id}@v${r.version}`, r.version])),
      hasCertified: ordered.some((r) => r.status === 'certified'),
    })
  }
  out.sort((a, b) => (a.hasCertified ? 0 : 1) - (b.hasCertified ? 0 : 1) || (a.assessmentId < b.assessmentId ? -1 : a.assessmentId > b.assessmentId ? 1 : 0))
  return out
}

const bundles = new Map<string, Promise<Record<string, any>>>()

/** A version's bundle, stamped as DEEP stamps a registry record (version, lifecycle, assessmentRef). */
export function loadRef(src: DataSource, ref: string): Promise<Record<string, any>> {
  let got = bundles.get(ref)
  if (!got) {
    got = (async () => {
      const [aid, v] = ref.split('@v')
      const rec = (await catalog(src)).find((x) => x.id === aid && x.version === Number(v))
      if (!rec) throw new Error(`unknown assessment ref ${ref}`)
      const bundle = JSON.parse(new TextDecoder().decode(await src.bytesOf(`library/${rec.bundle}`)))
      return { ...bundle, version: rec.version, lifecycle: rec.status, assessmentRef: ref }
    })()
    got.catch(() => bundles.delete(ref))
    bundles.set(ref, got)
  }
  return got
}

/** session.lifecycle_status. */
function lifecycleStatus(raw: Record<string, any>): string {
  const lib = raw.library ?? {}
  for (const src of [raw, lib]) {
    for (const key of ['lifecycle', 'status']) {
      const v = src?.[key]
      if (v && ELIGIBLE.includes(String(v).trim().toLowerCase())) return String(v).trim().toLowerCase()
    }
  }
  return 'preliminary'
}

/** The Basin pane's assessment block for the loaded bundle (or the blocked one without). */
export function assessPane(raw: Record<string, any> | null, covering: Covering[]): string {
  if (!raw) return deepNoAssessment(covering.length > 0)
  const mbf: any[] = raw.metricsByFunction ?? []
  const covered = mbf.filter((fn) => fn.metrics?.length).length
  const declared = raw.functionCoverage
  const cov = declared && typeof declared === 'object' && declared.total
    ? { total: Number(declared.total), covered: Number(declared.covered || covered), excluded: Number(declared.excluded || 0), declared: true }
    : { total: FRAMEWORK_FUNCTIONS, covered, excluded: 0, declared: false }
  const nmet = new Set(mbf.flatMap((fn) => fn.metrics ?? []).map((m: any) => m.metricId)).size
  const note = cov.covered >= cov.total ? '' : cov.declared ? ` (${cov.excluded} documented)` : ' (not declared)'
  const lib = raw.library ?? {}
  const region = raw.region ?? lib.region ?? {}
  const lifecycle = lifecycleStatus(raw)
  const tier = raw.referenceTier ? TIER_LABELS[String(raw.referenceTier)] ?? String(raw.referenceTier).replace(/_/g, ' ') : ''
  const counts = `${nmet} metrics · ${cov.covered} of ${cov.total} functions${note}`
  const meta = [...(lib.version ? [`v${lib.version}`] : []), counts].join(' · ')
  return deepAssessPane({
    name: raw.assessmentName ?? raw.assessmentId ?? '', region: region.name || '', lifecycle,
    label: STATUS_LABELS[lifecycle] ?? lifecycle, badge: BADGE_CLASS[lifecycle], meta, tier,
    bestAvailable: String(raw.referenceTier) === 'best_available',
    coversSite: covering.some((e) => e.assessmentId === raw.assessmentId),
    canChange: covering.length > 1 || covering.reduce((s, e) => s + e.refs.length, 0) > 1,
  })
}

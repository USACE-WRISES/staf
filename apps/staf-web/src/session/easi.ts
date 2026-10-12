// The EASI assessment file (easi/session.py): the header's Save and Open.
//
// * ``delineation``: the delineate_only result as the page holds it, ``ctx_inputs`` included, so a site
//   saved before its screening finished can still be screened after Open.
// * ``toolData``: ``screening`` (the HUC12 and the report every scenario scores from, null until one has
//   run), ``notes`` (shared by every scenario) and ``method`` (the scoring method it ran under).
// * ``scenarios``: each scenario's rating state.
//
// Open restores the screening as it was saved, with no network call: each scenario rescores from the
// stored report, as a scenario switch does.
import { scoredFor } from '../tools/easi/state'
import { scoringMethodText } from '../tools/easi/identity'
import { normalized } from '../tools/easi/state'
import { AssessmentFileError, dump as dumpFile, parse } from './file'

type Obj = Record<string, any>
export const TOOL = 'EASI'

/** dump: ``base`` is the page's screening result (null before one ran); ``scenarios`` scenariosBlock's. */
export function dump(delin: Obj | null, base: Obj | null, notes: Obj | null, methodId: Obj | null, scenarios: Obj): string {
  let screening: Obj | null = null
  if (base) screening = { huc12: (base.delineation ?? {}).huc12 ?? null, report: base.report ?? null }
  return dumpFile(TOOL, delin ?? {}, { screening, notes: notes ?? {}, method: methodId ?? {} }, scenarios)
}

export interface Loaded { delineation: Obj, screening: Obj | null, notes: Record<string, string>, method: Obj, scenarios: Obj | null }

const isObj = (v: unknown): v is Obj => v !== null && typeof v === 'object' && !Array.isArray(v)

/** load: a saved file, checked (a screening's report must rescore). Throws AssessmentFileError. */
export function load(text: string): Loaded {
  const st = parse(text, TOOL)
  const data = st.toolData
  const screening = data.screening
  if (screening !== null && screening !== undefined) {
    const report = isObj(screening) ? screening.report : null
    if (!isObj(report) || !Array.isArray(report.metricRows)) throw new AssessmentFileError('the saved screening has no report.')
    if (!Object.keys(st.delineation).length) throw new AssessmentFileError('the saved screening has no delineation.')
    try {
      scoredFor(report, null)
    } catch {
      throw new AssessmentFileError('the saved screening cannot be scored.')
    }
  }
  const notes: Record<string, string> = {}
  if (isObj(data.notes)) for (const [k, v] of Object.entries(data.notes)) if (typeof v === 'string' && v.trim()) notes[k] = v
  return { delineation: st.delineation, screening: screening ?? null, notes, method: isObj(data.method) ? data.method : {}, scenarios: st.scenarios }
}

const num = (v: unknown): number => (typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN)

/** saved_point: the pin on the stream that was picked and the StreamCat COMID that sources it,
 * ``[lat, lon, dist_ft, comid]``, or null without a usable pair. */
export function savedPoint(delin: Obj): [number, number, number, number] | null {
  const anchor = delin.siteAnchor ?? {}
  const scored = anchor.scoredReach ?? {}
  let site = [anchor.selectedSite, anchor.clickedStream, scored].find((s) => isObj(s) && s.snapLat != null && s.snapLon != null) as Obj | undefined
  if (!site) {
    const dd = delin.delineation ?? {}
    site = { snapLat: dd.snapped_lat, snapLon: dd.snapped_lon }
  }
  const comid = scored.comid
  if (typeof comid === 'boolean' || comid === null || comid === undefined) return null
  const lat = num(site.snapLat)
  const lon = num(site.snapLon)
  const dist = site.snapDistFt ? num(site.snapDistFt) : 0.0
  const cid = typeof comid === 'number' ? Math.trunc(comid) : /^\s*[+-]?\d+\s*$/.test(String(comid)) ? Number(comid) : NaN
  if (![lat, lon, dist].every(Number.isFinite) || !Number.isFinite(cid) || cid <= 0 || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
  return [lat, lon, dist, cid]
}

/** method_changed: a line for Open when the file's screening ran under another scoring method. */
export function methodChanged(saved: Obj | null, current: Obj | null): string | null {
  const old = (saved ?? {}).method_version
  const now = (current ?? {}).method_version
  if (!old || !now || old === now) return null
  return `This screening ran under ${scoringMethodText(saved)}. This EASI scores with ${scoringMethodText(current)}: screen the site again for current results.`
}

/** fingerprint_state: a scenario's ratings for the unsaved-work check, without the height boxes' values. */
export function fingerprintState(state: unknown): Obj {
  const st = normalized(state) as Obj
  const out: Obj = {}
  for (const [k, v] of Object.entries(st)) if (k !== 'xsHeights') out[k] = v
  return out
}

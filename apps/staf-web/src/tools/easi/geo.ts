// EASI's strata (easi/geo.py): the EPA Level III ecoregion at the point (the polygons EASI ships, the
// same file as the engine's engine/ecoregions_l3.geojson), the official NARS nine-region code (the
// method package's nars-ecoregions-9.geojson.gz), the Level II and I codes from the package's
// crosswalk, and the slope class. Point in polygon is boundary-inclusive (shapely ``covers``), the
// first feature in file order wins, and a feature's bounds prefilter as in the Python.
import type { DataSource } from '../../data/source'
import { covers, finite, firstCovering, type Indexed, indexFeatures, level3At } from '../../engine/ecoregion'
import { setNars9Names } from './evaluator'
import { gunzip, method } from './method'

let nars: Promise<{ idx: Indexed[], names: Map<string, string | null> }> | null = null

/** The NARS nine-region polygons and names (read once from the method package). */
export function nars9Index(): Promise<{ idx: Indexed[], names: Map<string, string | null> }> {
  nars ??= gunzip(method().nars9Gz).then((raw) => {
    const fc = JSON.parse(new TextDecoder().decode(raw))
    const names = new Map<string, string | null>()
    for (const f of fc.features ?? []) {
      const p = f.properties ?? {}
      if (p.WSA_9) names.set(String(p.WSA_9), p.WSA_9_NM ?? null)
    }
    setNars9Names((code) => names.get(code) ?? null)
    return { idx: indexFeatures(fc, 'WSA_9', 'WSA_9_NM'), names }
  })
  nars.catch(() => { nars = null })
  return nars
}

export { covers, level3At }

/** nars9_at */
export async function nars9At(lat: any, lon: any): Promise<{ code: string | null, name: string } | null> {
  if (!finite(lat) || !finite(lon)) return null
  const hit = firstCovering((await nars9Index()).idx, Number(lat), Number(lon))
  return hit ? { code: hit.value === null ? null : String(hit.value), name: hit.name || '' } : null
}

/** nars9_name (the polygons must have been read: nars9Index). */
export async function nars9Name(code: string): Promise<string | null> {
  return (await nars9Index()).names.get(code) ?? null
}

export const ecoregionCrosswalk = (): any => method().crosswalk

/** slope_class: m/m, right-open at 0.005 and 0.02. */
export function slopeClass(slope: any): string | null {
  if (typeof slope === 'boolean' || !finite(slope) || Number(slope) < 0) return null
  const v = Number(slope)
  return v < 0.005 ? 'lt_0.5' : v < 0.02 ? '0.5_to_2' : 'ge_2'
}

/** strata_for: the Level III identity, its Level II and I, and the slope class. */
export function strataFor(l3Code: any, slope: any = null): Record<string, any> {
  let code: string | null = l3Code === null || l3Code === undefined ? null : String(l3Code).trim()
  if (code && code.endsWith('.0')) code = code.slice(0, -2)
  code = code || null
  const cw = ecoregionCrosswalk()
  const entry = (code !== null ? (cw.l3 ?? {})[code] : null) || {}
  const l2 = entry.l2 ?? null
  return { l3: code, l2, l1: entry.l1 ?? null, l2_name: ((l2 !== null ? (cw.l2 ?? {})[l2] : null) || {}).name ?? null, slope_class: slopeClass(slope) }
}

/** strata_at: the strata of a live location, with the NARS nutrient region. */
export async function strataAt(src: DataSource, lat: any, lon: any, slope: any = null): Promise<Record<string, any>> {
  const [l3hit, narsHit] = await Promise.all([level3At(src, lat, lon), nars9At(lat, lon)])
  return { ...strataFor((l3hit ?? {} as any).code ?? null, slope), nars9: (narsHit ?? {} as any).code ?? null }
}

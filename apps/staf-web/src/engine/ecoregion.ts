// Point in polygon over the region layers the tools ask (the engine's context.ecoregion_l3_at, EASI's
// geo.level3_at, DEEP's state lookup): boundary-inclusive (shapely ``covers``), the first feature in
// file order wins, and each feature's bounds prefilter as in the Python.
import type { DataSource } from '../data/source'

export type Ring = number[][]
export interface Indexed { value: any, name: string, polys: Ring[][], bounds: [number, number, number, number], props: Record<string, any> }

/** The features' polygons with their bounds (a (multi)polygon's shell extents) and properties. */
export function indexFeatures(fc: any, valueProp: string, nameProp: string): Indexed[] {
  const out: Indexed[] = []
  for (const f of fc.features ?? []) {
    const g = f.geometry
    if (!g) continue
    const polys: Ring[][] = g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : []
    if (!polys.length) continue
    let minx = Infinity
    let miny = Infinity
    let maxx = -Infinity
    let maxy = -Infinity
    for (const poly of polys) {
      for (const [x, y] of poly[0] ?? []) {
        if (x < minx) minx = x
        if (x > maxx) maxx = x
        if (y < miny) miny = y
        if (y > maxy) maxy = y
      }
    }
    if (!Number.isFinite(minx)) continue
    const props = f.properties ?? {}
    out.push({ value: props[valueProp] ?? null, name: props[nameProp] ?? null, polys, bounds: [minx, miny, maxx, maxy], props })
  }
  return out
}

/** The point on the segment exactly (collinear and within its box). */
function onSegment(x: number, y: number, ax: number, ay: number, bx: number, by: number): boolean {
  if (x < Math.min(ax, bx) || x > Math.max(ax, bx) || y < Math.min(ay, by) || y > Math.max(ay, by)) return false
  return (bx - ax) * (y - ay) - (by - ay) * (x - ax) === 0
}

/** shapely ``covers`` of a point by a (multi)polygon: inside a shell and outside its holes, or on a boundary. */
export function covers(polys: Ring[][], x: number, y: number): boolean {
  for (const poly of polys) {
    let inShell = false
    let inHole = false
    for (let k = 0; k < poly.length; k++) {
      const ring = poly[k]
      let inside = false
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i]
        const [xj, yj] = ring[j]
        if (onSegment(x, y, xj, yj, xi, yi)) return true
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside
      }
      if (k === 0) inShell = inside
      else if (inside) inHole = true
    }
    if (inShell && !inHole) return true
  }
  return false
}

/** The first feature covering the point (bounds first), or null. */
export function firstCovering(idx: Indexed[], lat: number, lon: number): Indexed | null {
  for (const e of idx) {
    const [minx, miny, maxx, maxy] = e.bounds
    if (lon < minx || lon > maxx || lat < miny || lat > maxy) continue
    if (covers(e.polys, lon, lat)) return e
  }
  return null
}

export const finite = (v: any): boolean => v !== null && v !== undefined && typeof v !== 'boolean' && Number.isFinite(Number(v))

let l3: Promise<Indexed[]> | null = null

/** The Level III polygons (engine/ecoregions_l3.geojson, the engine's own file), read once. */
export function level3Index(src: DataSource): Promise<Indexed[]> {
  l3 ??= src.ready().then(() => src.bytesOf('engine/ecoregions_l3.geojson'))
    .then((buf) => indexFeatures(JSON.parse(new TextDecoder().decode(buf)), 'US_L3CODE', 'US_L3NAME'))
  l3.catch(() => { l3 = null })
  return l3
}

let states: Promise<Indexed[]> | null = null

/** The US state polygons (engine/us_states.geojson: DEEP's Census 1:500,000 boundaries), read once. */
export function stateIndex(src: DataSource): Promise<Indexed[]> {
  states ??= src.ready().then(() => src.bytesOf('engine/us_states.geojson'))
    .then((buf) => indexFeatures(JSON.parse(new TextDecoder().decode(buf)), 'state', 'name'))
  states.catch(() => { states = null })
  return states
}

/** deep/geo.state_at: ``{code, abbr, name}`` of the state (or DC) covering the point, or null. */
export async function stateAt(src: DataSource, lat: any, lon: any): Promise<{ code: string | null, abbr: string | null, name: string } | null> {
  if (!finite(lat) || !finite(lon)) return null
  try {
    const hit = firstCovering(await stateIndex(src), Number(lat), Number(lon))
    if (!hit) return null
    const code = hit.value === null ? null : String(hit.value)
    return { code, abbr: code, name: hit.name || '' }
  } catch {
    return null
  }
}

/** assessments.resolve_site_regions: the snapped site's Level III ecoregion and state (both null
 * without coordinates). */
export async function resolveSiteRegions(src: DataSource, lat: unknown, lon: unknown): Promise<{ level3: any, state: any }> {
  if (lat === null || lat === undefined || lon === null || lon === undefined) return { level3: null, state: null }
  const [level3, state] = await Promise.all([level3At(src, lat, lon), stateAt(src, lat, lon)])
  return { level3, state }
}

/** ecoregion_l3_at: ``{code, name}`` of the Level III ecoregion covering the point, or null. */
export async function level3At(src: DataSource, lat: any, lon: any): Promise<{ code: string | null, name: string } | null> {
  if (!finite(lat) || !finite(lon)) return null
  try {
    const hit = firstCovering(await level3Index(src), Number(lat), Number(lon))
    return hit ? { code: hit.value === null ? null : String(hit.value), name: hit.name || '' } : null
  } catch {
    return null
  }
}

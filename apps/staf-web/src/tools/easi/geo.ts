// EASI's strata (easi/geo.py): the EPA Level III ecoregion at the point (the polygons EASI ships, the
// same file as the engine's engine/ecoregions_l3.geojson), the official NARS nine-region code (the
// method package's nars-ecoregions-9.geojson.gz), the Level II and I codes from the package's
// crosswalk, and the slope class. Point in polygon is boundary-inclusive (shapely ``covers``), the
// first feature in file order wins, and a feature's bounds prefilter as in the Python.
import type { DataSource } from '../../data/source'
import { setNars9Names } from './evaluator'
import { gunzip, method } from './method'

type Ring = number[][]
interface Indexed { value: any, name: string, polys: Ring[][], bounds: [number, number, number, number] }

function index(fc: any, valueProp: string, nameProp: string): Indexed[] {
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
    out.push({ value: props[valueProp] ?? null, name: props[nameProp] ?? null, polys, bounds: [minx, miny, maxx, maxy] })
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

function lookup(idx: Indexed[], lat: number, lon: number): { code: string | null, name: string } | null {
  for (const e of idx) {
    const [minx, miny, maxx, maxy] = e.bounds
    if (lon < minx || lon > maxx || lat < miny || lat > maxy) continue
    if (covers(e.polys, lon, lat)) return { code: e.value === null ? null : String(e.value), name: e.name || '' }
  }
  return null
}

let l3: Promise<Indexed[]> | null = null
let nars: Promise<{ idx: Indexed[], names: Map<string, string | null> }> | null = null

function level3Index(src: DataSource): Promise<Indexed[]> {
  l3 ??= src.ready().then(() => src.bytesOf('engine/ecoregions_l3.geojson'))
    .then((buf) => index(JSON.parse(new TextDecoder().decode(buf)), 'US_L3CODE', 'US_L3NAME'))
  l3.catch(() => { l3 = null })
  return l3
}

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
    return { idx: index(fc, 'WSA_9', 'WSA_9_NM'), names }
  })
  nars.catch(() => { nars = null })
  return nars
}

const finite = (v: any): boolean => v !== null && v !== undefined && typeof v !== 'boolean' && Number.isFinite(Number(v))

/** level3_at */
export async function level3At(src: DataSource, lat: any, lon: any): Promise<{ code: string | null, name: string } | null> {
  if (!finite(lat) || !finite(lon)) return null
  return lookup(await level3Index(src), Number(lat), Number(lon))
}

/** nars9_at */
export async function nars9At(lat: any, lon: any): Promise<{ code: string | null, name: string } | null> {
  if (!finite(lat) || !finite(lon)) return null
  return lookup((await nars9Index()).idx, Number(lat), Number(lon))
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

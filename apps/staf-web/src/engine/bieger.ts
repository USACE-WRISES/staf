// Bieger et al. (2015) regional bankfull geometry (site_engine _extracted/bieger.py): bankfull width,
// mean depth and area as power functions of drainage area, by the physiographic division at the site
// (the engine's division polygons, engine/physio_divisions.geojson); the national curve outside them.
import { pyFixed } from '../app/pyfmt'
import type { DataSource } from '../data/source'

/** Division -> (a, b) for width (m), depth (m) and area (m2); DA in km2. Bieger 2015, Table 3. */
export const COEF: Record<string, Record<'width' | 'depth' | 'area', [number, number]>> = {
  LUP: { width: [4.15, 0.308], depth: [0.31, 0.202], area: [1.27, 0.509] },
  APL: { width: [2.22, 0.363], depth: [0.24, 0.323], area: [0.52, 0.680] },
  AHI: { width: [3.12, 0.415], depth: [0.26, 0.287], area: [0.82, 0.704] },
  IPL: { width: [2.56, 0.351], depth: [0.38, 0.191], area: [1.28, 0.472] },
  IHI: { width: [23.23, 0.121], depth: [0.27, 0.267], area: [6.28, 0.387] },
  RMS: { width: [1.24, 0.435], depth: [0.23, 0.225], area: [0.20, 0.688] },
  IMP: { width: [1.11, 0.415], depth: [0.07, 0.329], area: [0.07, 0.751] },
  PMS: { width: [2.76, 0.399], depth: [0.23, 0.294], area: [0.87, 0.652] },
  USA: { width: [2.70, 0.352], depth: [0.30, 0.213], area: [0.95, 0.540] },
}
/** The field sites' drainage-area range per division (Table 2). */
export const DA_FIT_RANGE_SQKM: Record<string, [number, number]> = {
  LUP: [43.0, 948.0], APL: [0.8, 2815.0], AHI: [0.2, 2435.0], IPL: [0.5, 155213.0], IHI: [78.0, 2484.0],
  RMS: [0.4, 25201.0], IMP: [9.4, 19632.0], PMS: [16.0, 20927.0], USA: [0.2, 155213.0],
}
const DIV_ABBR: Record<string, string> = {
  'LAURENTIAN UPLAND': 'LUP', 'ATLANTIC PLAIN': 'APL', 'APPALACHIAN HIGHLANDS': 'AHI', 'INTERIOR PLAINS': 'IPL',
  'INTERIOR HIGHLANDS': 'IHI', 'ROCKY MOUNTAIN SYSTEM': 'RMS', 'INTERMONTANE PLATEAUS': 'IMP', 'PACIFIC MOUNTAIN SYSTEM': 'PMS',
}
const title = (s: string) => s.toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase())
export const DIV_NAME: Record<string, string> = { ...Object.fromEntries(Object.entries(DIV_ABBR).map(([n, a]) => [a, title(n)])), USA: 'National curve' }

type Poly = number[][][]
let divisionsP: Promise<{ name: string, polys: Poly[] }[]> | null = null

function divisions(src: DataSource): Promise<{ name: string, polys: Poly[] }[]> {
  divisionsP ??= src.ready().then(() => src.bytesOf('engine/physio_divisions.geojson')).then((buf) => {
    const fc = JSON.parse(new TextDecoder().decode(buf))
    return (fc.features ?? []).map((f: any) => ({
      name: String(f.properties?.DIVISION ?? ''),
      polys: f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates,
    }))
  })
  divisionsP.catch(() => { divisionsP = null })
  return divisionsP
}

function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  const r = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0
  const t = Math.max(0, Math.min(1, r))
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
}

/** The point's place against a polygon: inside or on its boundary (shapely intersects), and its distance. */
function locate(polys: Poly[], x: number, y: number): { hit: boolean, dist: number } {
  let dist = Infinity
  for (const rings of polys) {
    let inShell = false
    let inHole = false
    rings.forEach((ring, k) => {
      let inside = false
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i]
        const [xj, yj] = ring[j]
        dist = Math.min(dist, segDist(x, y, xj, yj, xi, yi))
        if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside
      }
      if (k === 0) inShell = inside
      else if (inside) inHole = true
    })
    if (dist === 0) return { hit: true, dist: 0 }
    if (inShell && !inHole) return { hit: true, dist: 0 }
  }
  return { hit: false, dist }
}

/** bieger.division_at: the division holding the point, else the nearest within 0.25 degree, else null. */
export async function divisionAt(src: DataSource, lat: number | null | undefined, lon: number | null | undefined): Promise<string | null> {
  if (lat === null || lat === undefined || lon === null || lon === undefined) return null
  try {
    const divs = await divisions(src)
    let best = -1
    let bestDist = Infinity
    for (let i = 0; i < divs.length; i++) {
      const { hit, dist } = locate(divs[i].polys, lon, lat)
      if (hit) return DIV_ABBR[divs[i].name.trim().toUpperCase()] ?? null
      if (dist < bestDist) {
        bestDist = dist
        best = i
      }
    }
    if (best < 0 || bestDist > 0.25) return null
    return DIV_ABBR[divs[best].name.trim().toUpperCase()] ?? null
  } catch {
    return null
  }
}

export interface Bankfull {
  width_m: number
  depth_m: number
  area_m2: number
  division: string
  division_name: string
  regional: boolean
  extrapolated: boolean
  fit_range_sqkm: [number, number]
}

/** bieger.bankfull_geometry */
export async function bankfullGeometry(src: DataSource, daSqkm: number | null, lat: number | null, lon: number | null): Promise<Bankfull> {
  const da = Math.max(Number(daSqkm || 0.0), 0.01)
  const abbr = await divisionAt(src, lat, lon)
  const key = abbr && abbr in COEF ? abbr : 'USA'
  const c = COEF[key]
  const [lo, hi] = DA_FIT_RANGE_SQKM[key] ?? DA_FIT_RANGE_SQKM.USA
  const power = ([a, b]: [number, number]) => a * Math.pow(da, b)
  return {
    width_m: Number(pyFixed(power(c.width), 2)), depth_m: Number(pyFixed(power(c.depth), 3)), area_m2: Number(pyFixed(power(c.area), 2)),
    division: key, division_name: DIV_NAME[key] ?? 'National curve', regional: key !== 'USA',
    extrapolated: !(lo <= da && da <= hi), fit_range_sqkm: [lo, hi],
  }
}

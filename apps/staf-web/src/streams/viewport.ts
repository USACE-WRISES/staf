// Which stream lines the map needs, and when a fetch is due (each Shiny tool's viewport.py).
//
// The map fetches the streams for a box around the view centre: square at zoom 14, twice as wide as
// tall from 15 (half-height 0.03 degrees of latitude at 15, halving with each zoom in, at most 0.08),
// its edges rounded outward to 0.001 degree. A fetch is due only when the view leaves the box last
// fetched, so a pan inside the margin or a zoom in keeps the lines already drawn.

export const HALF_LAT_AT_15 = 0.03
export const HALF_CAP = 0.08
export const WIDE_FROM_ZOOM = 15
const ROUND = 10 ** 3

/** (west, south, east, north) */
export type BBox = [number, number, number, number]
/** (south, west, north, east) */
export type View = [number, number, number, number]

function floor3(x: number): number {
  return Math.floor(x * ROUND + 1e-9) / ROUND
}

function ceil3(x: number): number {
  return Math.ceil(x * ROUND - 1e-9) / ROUND
}

export function halfSizes(zoom: number): [number, number] {
  const halfLat = Math.min(HALF_CAP, HALF_LAT_AT_15 * 2 ** (15 - zoom))
  return [halfLat * (zoom >= WIDE_FROM_ZOOM ? 2 : 1), halfLat]
}

/** The padded box to fetch around the view centre, rounded outward. */
export function fetchBox(lat: number, lon: number, zoom: number): BBox {
  const [halfLon, halfLat] = halfSizes(zoom)
  return [floor3(lon - halfLon), floor3(lat - halfLat), ceil3(lon + halfLon), ceil3(lat + halfLat)]
}

export function areaDeg2([w, s, e, n]: BBox): number {
  return Math.max(0, e - w) * Math.max(0, n - s)
}

/** True when nothing was fetched yet or the view is not inside the box last fetched. */
export function needsFetch(view: View | null, fetched: BBox | null): boolean {
  if (!fetched || !view) return true
  const [s, w, n, e] = view
  const [fw, fs, fe, fn] = fetched
  return !(w >= fw && e <= fe && s >= fs && n <= fn)
}

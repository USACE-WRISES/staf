/**
 * The coordinate systems the cross-section grid uses (pyproj in the Python, proj4 here): EPSG:5070
 * (NAD83 Conus Albers) for placement and transects, NAD83 UTM zones for the 1 m tiles, NAD83
 * geographic for the 3 m quads and the 10 m tiles, and the bundle's lon/lat.
 */
import proj4 from 'proj4'

proj4.defs('EPSG:5070', '+proj=aea +lat_0=23 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs')
proj4.defs('EPSG:4269', '+proj=longlat +datum=NAD83 +no_defs')
for (let zone = 1; zone <= 60; zone++) {
  proj4.defs(`EPSG:${26900 + zone}`, `+proj=utm +zone=${zone} +datum=NAD83 +units=m +no_defs`)
}

type Converter = { forward: (p: [number, number]) => [number, number] }
const cache = new Map<string, Converter>()

/** A converter `from` -> `to` (EPSG codes), x/y order (pyproj's `always_xy`). */
export function converter(from: number, to: number): Converter {
  const key = `${from}>${to}`
  let c = cache.get(key)
  if (!c) {
    const p = proj4(`EPSG:${from}`, `EPSG:${to}`)
    c = { forward: (xy) => p.forward(xy) as [number, number] }
    cache.set(key, c)
  }
  return c
}

/** Transform parallel coordinate arrays. */
export function transform(from: number, to: number, xs: ArrayLike<number>, ys: ArrayLike<number>): [Float64Array, Float64Array] {
  const c = converter(from, to)
  const ox = new Float64Array(xs.length)
  const oy = new Float64Array(xs.length)
  for (let i = 0; i < xs.length; i++) {
    const [x, y] = c.forward([xs[i], ys[i]])
    ox[i] = x
    oy[i] = y
  }
  return [ox, oy]
}

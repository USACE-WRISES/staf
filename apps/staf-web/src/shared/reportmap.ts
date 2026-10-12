// The report's watershed map: the outline and reach over a USGS topo basemap (reportmap.py, the same
// file in EASI, SFARI and DEEP).
//
// The basemap is one request to USGS's MapServer export (it answers browsers: CORS "*", checked
// 2026-10-12), in Web Mercator, the projection it draws in. Like the Shiny tools, a successful image is
// kept (on the rounded box and size), so the PDF draws the report's own image; a failed request is not
// kept, so the next report tries again. The Shiny report embeds the PNG as a data address; so here.
import { base64 } from '../files/bytes'
import { pyFixed } from '../app/pyfmt'

export const EXPORT_URL = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/export'
export const WATERSHED_FILL = '#fdf24a'
export const WATERSHED_STROKE = '#caa700'
export const REACH_STROKE = '#d6453d'
const PAD = 0.08
const MIN_EXTENT_M = 1200.0
const FETCH_TIMEOUT_S = 12.0
const FETCH_ATTEMPTS = 2
const RETRY_PAUSE_S = 1.0
export const UNAVAILABLE_NOTE = 'Map background unavailable'
const R = 6378137.0
/** math.radians multiplies by pi / 180 computed once (CPython's degToRad) */
const DEG_TO_RAD = Math.PI / 180.0

/** Lon/lat degrees to EPSG:3857 metres. */
export function mercator(lon: number, lat: number): [number, number] {
  const la = Math.max(-85.05112878, Math.min(85.05112878, lat))
  return [R * (lon * DEG_TO_RAD), R * Math.log(Math.tan(Math.PI / 4 + (la * DEG_TO_RAD) / 2))]
}

/** Every coordinate ring in a FeatureCollection, polygons and lines alike. */
export function rings(gj: any): number[][][] {
  const out: number[][][] = []
  for (const ft of (gj || {}).features || []) {
    const g = ft.geometry || {}
    const c = g.coordinates
    if (!c || !c.length) continue
    if (g.type === 'Polygon') out.push(...c)
    else if (g.type === 'MultiPolygon') for (const poly of c) out.push(...poly)
    else if (g.type === 'LineString') out.push(c)
    else if (g.type === 'MultiLineString') out.push(...c)
  }
  return out
}

export type BBox = [number, number, number, number]

/** frame: the padded Mercator box of both inputs at the card's aspect, and the projection to the
 * card's pixels; null without geometry. */
export function frame(ws: any, reach: any, w: number, hgt: number): { bbox: BBox, project: (lon: number, lat: number) => [number, number] } | null {
  const pts = [...rings(ws), ...rings(reach)].flat()
  if (!pts.length) return null
  let minx = Infinity
  let maxx = -Infinity
  let miny = Infinity
  let maxy = -Infinity
  for (const p of pts) {
    const [x, y] = mercator(p[0], p[1])
    if (x < minx) minx = x
    if (x > maxx) maxx = x
    if (y < miny) miny = y
    if (y > maxy) maxy = y
  }
  const cx = (minx + maxx) / 2
  const cy = (miny + maxy) / 2
  let bw = Math.max((maxx - minx) * (1 + PAD), MIN_EXTENT_M)
  let bh = Math.max((maxy - miny) * (1 + PAD), MIN_EXTENT_M)
  if (bw / bh < w / hgt) bw = bh * (w / hgt)
  else bh = bw / (w / hgt)
  const bbox: BBox = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]
  const project = (lon: number, lat: number): [number, number] => {
    const [x, y] = mercator(lon, lat)
    return [(x - bbox[0]) / (bbox[2] - bbox[0]) * w, (bbox[3] - y) / (bbox[3] - bbox[1]) * hgt]
  }
  return { bbox, project }
}

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
const images = new Map<string, Promise<Uint8Array>>()

/** _validated_png: a complete PNG the browser decodes. */
async function validated(bytes: Uint8Array): Promise<Uint8Array> {
  if (bytes.length < 8 || PNG_SIGNATURE.some((b, i) => bytes[i] !== b)) throw new Error('Response is not a PNG image')
  if (typeof createImageBitmap === 'function') (await createImageBitmap(new Blob([bytes as BlobPart], { type: 'image/png' }))).close()
  return bytes
}

const pause = (s: number) => new Promise((r) => setTimeout(r, s * 1000))

/** _fetch: the export for a (rounded) box, retried once on a timeout, a lost connection, 408, 429 or 5xx. */
async function fetchPng(bbox: number[], size: [number, number], timeout: number): Promise<Uint8Array> {
  const params = new URLSearchParams({ bbox: bbox.map((v) => pyFixed(v, 2)).join(','), bboxSR: '3857', imageSR: '3857',
    size: `${size[0]},${size[1]}`, format: 'png', transparent: 'false', f: 'image' })
  for (let attempt = 1; attempt <= FETCH_ATTEMPTS; attempt++) {
    let retryable = false
    try {
      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), timeout * 1000)
      try {
        const res = await fetch(`${EXPORT_URL}?${params}`, { signal: ctl.signal })
        if (res.status === 200) return await validated(new Uint8Array(await res.arrayBuffer()))
        retryable = res.status === 408 || res.status === 429 || (res.status >= 500 && res.status <= 599)
      } finally {
        clearTimeout(timer)
      }
    } catch (err) {
      retryable = err instanceof TypeError || (err instanceof DOMException && err.name === 'AbortError')
    }
    if (!retryable || attempt === FETCH_ATTEMPTS) break
    await pause(RETRY_PAUSE_S)
  }
  throw new Error('basemap unavailable')
}

/** topo_png: the basemap for a Mercator box, or null when the service did not answer. Successful images
 * are kept on the box rounded to 0.1 m and the size, so the PDF reuses the report's. */
export async function topoPng(bbox: BBox, size: [number, number] = [580, 360], timeout = FETCH_TIMEOUT_S): Promise<Uint8Array | null> {
  const key = bbox.map((v) => Number(pyFixed(v, 1)))
  const id = `${key.join(',')}|${size[0]},${size[1]}`
  let p = images.get(id)
  if (!p) {
    p = fetchPng(key, size, timeout)
    images.set(id, p)
    p.catch(() => images.delete(id))
  }
  return p.catch(() => null)
}

/** The modal map's basemap read ahead (a report about to open does not wait for it). */
export function warmTopo(ws: any, reach: any, w = 290, hgt = 180): void {
  const fr = frame(ws, reach, w, hgt)
  if (fr) void topoPng(fr.bbox, [w * 2, hgt * 2])
}

function paths(ws: any, reach: any, project: (lon: number, lat: number) => [number, number]): string[] {
  const pt = (p: number[]) => project(p[0], p[1]).map((v) => pyFixed(v, 1)).join(',')
  const out: string[] = []
  for (const ring of rings(ws)) {
    out.push(`<path d="M${ring.map(pt).join(' L')} Z" fill="${WATERSHED_FILL}" fill-opacity="0.35" stroke="${WATERSHED_STROKE}" stroke-width="1"/>`)
  }
  for (const ring of rings(reach)) out.push(`<path d="M${ring.map(pt).join(' L')}" fill="none" stroke="${REACH_STROKE}" stroke-width="2.4"/>`)
  return out
}

/** svg: the report thumbnail as inline SVG with the basemap embedded, "" without geometry; a basemap
 * that did not load leaves the outline with a caption under it. */
export async function svg(ws: any, reach: any, w = 290, hgt = 180, { basemap = true, timeout = FETCH_TIMEOUT_S } = {}): Promise<string> {
  const fr = frame(ws, reach, w, hgt)
  if (!fr) return ''
  const parts = [`<svg viewBox="0 0 ${w} ${hgt}" xmlns="http://www.w3.org/2000/svg" class="sfari-minimap" style="width:${w}px;max-width:100%;">`]
  const png = basemap ? await topoPng(fr.bbox, [w * 2, hgt * 2], timeout) : null
  if (png) {
    parts.push(`<image x="0" y="0" width="${w}" height="${hgt}" preserveAspectRatio="none" href="data:image/png;base64,${base64(png)}"/>`)
  }
  parts.push(...paths(ws, reach, fr.project))
  parts.push('</svg>')
  const result = parts.join('')
  if (basemap && !png) {
    return `<div class="reportmap-fallback" style="width:${w}px;max-width:100%;">${result}`
      + `<p style="margin:4px 0 0;color:#667085;font-size:11px;">${UNAVAILABLE_NOTE}</p></div>`
  }
  return result
}

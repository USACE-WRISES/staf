// The STAF data bundle's regions, catchments and flowlines by id (site_engine bundle.py and
// _hrslim/reader2.py, fmt2.py, arcs.py, grid.py), read by HTTP range from the browser layout.
//
// A dataset folder ("bundle/" for NHDPlus HR, "bundle/v2/" for NHDPlus V2) has a manifest naming each
// region's files, bounds and grid. Catchments are exact grid polygons stored as shared borders: each
// border once, as an arc (its start corner, the axis of its first move, one signed run per corner in
// steps2_<vpu>.bin), each catchment a list of polygons of rings of arc references. A region's
// encoding is read once and decoded per catchment as needed, to whole cells, then to degrees rounded
// to 1e-7 as the reader rounds them.
import { decompressZstd } from 'hyparquet-compressors'
import { pyFixed } from '../app/pyfmt'
import { metadata, readRows, rowGroups } from '../data/parquet'
import type { DataSource } from '../data/source'
import { albersToLonLat } from '../xs/projExact'
import { nearestPointOnLines } from './snap'

export interface RegionMeta {
  vpu: string
  bounds: [number, number, number, number]
  grid: { crs: number, cell: number, offset: number }
  lines: string
  catchments: string
  arcs: string
  steps: string
}

const manifests = new Map<string, Promise<RegionMeta[]>>()

/** A dataset's regions in region order (the reader sorts them by code). */
export function datasetRegions(src: DataSource, folder: string): Promise<RegionMeta[]> {
  const key = src.base + folder
  let got = manifests.get(key)
  if (!got) {
    got = src.ready().then(() => src.bytesOf(`${folder}manifest.json`)).then((buf) => {
      const m = JSON.parse(new TextDecoder().decode(buf)) as { vpus: Record<string, any> }
      const file = (v: any) => (typeof v === 'string' ? v : v?.file)
      return Object.keys(m.vpus).sort().map((vpu) => {
        const r = m.vpus[vpu]
        return { vpu, bounds: r.bounds, grid: r.grid, lines: file(r.lines), catchments: file(r.catchments), arcs: file(r.arcs), steps: file(r.steps) }
      })
    })
    got.catch(() => manifests.delete(key))
    manifests.set(key, got)
  }
  return got
}

function overlaps([w, s, e, n]: number[], west: number, south: number, east: number, north: number): boolean {
  return w <= east && e >= west && s <= north && n >= south
}

// ------------------------------------------------------------------ catchments

interface Encoding {
  ids: number[]
  bbox: Int32Array[]            // xmin, ymin, xmax, ymax per row (1e-5 degree units)
  refs: number[][]              // per row: its arc references, ring after ring
  rings: number[][]             // per row: references per ring
  polys: number[][]             // per row: rings per polygon
  x0: Int32Array
  y0: Int32Array
  axis: Uint8Array
  runOff: Float64Array          // per arc: offset of its first run
  runs: Int32Array
}

const encodings = new Map<string, Promise<Encoding>>()

/** fmt2.unpack_steps: zigzag varints, zstd-compressed, behind "HRSTEPS2", count and raw length. */
export function unpackSteps(data: Uint8Array): Int32Array {
  const magic = new TextDecoder().decode(data.subarray(0, 8))
  if (magic !== 'HRSTEPS2') throw new Error('not a steps file')
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength)
  const n = Number(view.getBigUint64(8, true))
  const rawLen = Number(view.getBigUint64(16, true))
  const raw = decompressZstd(data.subarray(24), new Uint8Array(rawLen))
  const out = new Int32Array(n)
  let k = 0
  let value = 0
  let shift = 0
  for (let i = 0; i < raw.length; i++) {
    const b = raw[i]
    value += (b & 0x7f) * 2 ** shift
    if (b & 0x80) {
      shift += 7
      continue
    }
    // zigzag: (zz >> 1) ^ -(zz & 1)
    out[k++] = value % 2 === 0 ? value / 2 : -(value + 1) / 2
    value = 0
    shift = 0
  }
  if (k !== n) throw new Error('steps file is truncated')
  return out
}

async function wholeColumns(src: DataSource, logical: string, columns: string[]): Promise<Record<string, any>[]> {
  const meta = await metadata(src, logical)
  const total = rowGroups(meta).reduce((s, g) => s + g.rows, 0)
  return readRows(src, logical, columns, 0, total)
}

function encoding(src: DataSource, folder: string, region: RegionMeta): Promise<Encoding> {
  const key = src.base + folder + region.vpu
  let got = encodings.get(key)
  if (!got) {
    got = (async () => {
      const [cats, arcs, steps] = await Promise.all([
        wholeColumns(src, folder + region.catchments, ['nhdplusid', 'xmin', 'ymin', 'xmax', 'ymax', 'refs', 'rings', 'polys']),
        wholeColumns(src, folder + region.arcs, ['x0', 'y0', 'axis', 'n']),
        src.ready().then(() => src.bytesOf(folder + region.steps)),
      ])
      const runOff = new Float64Array(arcs.length + 1)
      arcs.forEach((a, i) => { runOff[i + 1] = runOff[i] + Number(a.n) })
      return {
        ids: cats.map((c) => Number(c.nhdplusid)),
        bbox: cats.map((c) => Int32Array.from([c.xmin, c.ymin, c.xmax, c.ymax].map(Number))),
        refs: cats.map((c) => Array.from(c.refs as ArrayLike<number>, Number)),
        rings: cats.map((c) => Array.from(c.rings as ArrayLike<number>, Number)),
        polys: cats.map((c) => Array.from(c.polys as ArrayLike<number>, Number)),
        x0: Int32Array.from(arcs, (a) => Number(a.x0)),
        y0: Int32Array.from(arcs, (a) => Number(a.y0)),
        axis: Uint8Array.from(arcs, (a) => Number(a.axis)),
        runOff,
        runs: unpackSteps(new Uint8Array(steps)),
      }
    })()
    got.catch(() => encodings.delete(key))
    encodings.set(key, got)
  }
  return got
}

/** An arc's vertices in cells (arcs.arc_vertices): its start, then one corner per run. */
function arcVertices(enc: Encoding, arc: number): [number, number][] {
  let x = enc.x0[arc]
  let y = enc.y0[arc]
  const out: [number, number][] = [[x, y]]
  const a = enc.axis[arc]
  for (let j = enc.runOff[arc], k = 0; j < enc.runOff[arc + 1]; j++, k++) {
    if (((a + k) & 1) === 0) x += enc.runs[j]
    else y += enc.runs[j]
    out.push([x, y])
  }
  return out
}

/** numpy.round(x, 7): rint(x * 1e7) / 1e7 (half to even). */
function round7(x: number): number {
  const v = x * 1e7
  const f = Math.floor(v)
  const d = v - f
  const r = d > 0.5 ? f + 1 : d < 0.5 ? f : (f % 2 === 0 ? f : f + 1)
  return r / 1e7
}

/** A catchment row's polygons, rings of [lon, lat] (arcs.rows_ragged, Grid.to_lonlat, np.round 7). */
export function rowPolygons(enc: Encoding, row: number, grid: RegionMeta['grid']): [number, number][][][] {
  const refs = enc.refs[row]
  const ringSizes = enc.rings[row]
  const polySizes = enc.polys[row]
  const rings: [number, number][][] = []
  let r = 0
  for (const size of ringSizes) {
    const ring: [number, number][] = []
    for (let i = 0; i < size; i++, r++) {
      const ref = refs[r]
      let verts = arcVertices(enc, ref >> 1)
      if (ref & 1) verts = verts.reverse()
      ring.push(...(i === 0 ? verts : verts.slice(1)))
    }
    rings.push(ring.map(([ix, iy]) => {
      const [lon, lat] = albersToLonLat(ix * grid.cell + grid.offset, iy * grid.cell + grid.offset)
      return [round7(lon), round7(lat)]
    }))
  }
  const polys: [number, number][][][] = []
  let k = 0
  for (const count of polySizes) {
    polys.push(rings.slice(k, k + count))
    k += count
  }
  return polys
}

function onSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number): boolean {
  if ((bx - ax) * (py - ay) - (by - ay) * (px - ax) !== 0) return false
  return Math.min(ax, bx) <= px && px <= Math.max(ax, bx) && Math.min(ay, by) <= py && py <= Math.max(ay, by)
}

/** Whether the ring holds the point: 1 inside, 0 on its boundary, -1 outside. */
function ringLocate(ring: [number, number][], px: number, py: number): number {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (onSegment(px, py, xj, yj, xi, yi)) return 0
    if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside
  }
  return inside ? 1 : -1
}

/** Whether the polygons cover the point (inside or on the boundary; holes taken out). */
export function covers(polys: [number, number][][][], px: number, py: number): boolean {
  for (const rings of polys) {
    const shell = ringLocate(rings[0], px, py)
    if (shell === 0) return true
    if (shell < 0) continue
    let inHole = false
    for (const hole of rings.slice(1)) {
      const at = ringLocate(hole, px, py)
      if (at === 0) return true
      if (at > 0) inHole = true
    }
    if (!inHole) return true
  }
  return false
}

/** The id of the catchment that contains the point, with its region, or null (bundle._catchment_at). */
export async function catchmentAt(src: DataSource, folder: string, lat: number, lon: number): Promise<{ id: number, region: RegionMeta } | null> {
  const eps = 1e-6
  const [w, s, e, n] = [lon - eps, lat - eps, lon + eps, lat + eps]
  const qw = Math.floor(w * 1e5), qs = Math.floor(s * 1e5), qe = Math.ceil(e * 1e5), qn = Math.ceil(n * 1e5)
  for (const region of (await datasetRegions(src, folder)).filter((r) => overlaps(r.bounds, w, s, e, n))) {
    const enc = await encoding(src, folder, region)
    for (let row = 0; row < enc.ids.length; row++) {
      const b = enc.bbox[row]
      if (b[0] > qe || b[2] < qw || b[1] > qn || b[3] < qs) continue
      if (covers(rowPolygons(enc, row, region.grid), lon, lat)) return { id: enc.ids[row], region }
    }
  }
  return null
}

// ------------------------------------------------------------------ flowlines by id

export interface FlowlineRecord {
  nhdplusid: number
  gnis_name: string | null
  reachcode: string | null
  lengthkm: number | null
  totdasqkm: number | null
  slope: number | null
  fcode: number | null
  stream_order: number | null
  vpuid: string
  geometry: { type: 'LineString' | 'MultiLineString', coordinates: unknown }
}

const idColumns = new Map<string, Promise<number[]>>()

function regionIds(src: DataSource, logical: string): Promise<number[]> {
  const key = src.base + logical
  let got = idColumns.get(key)
  if (!got) {
    got = wholeColumns(src, logical, ['nhdplusid']).then((rows) => rows.map((r) => Number(r.nhdplusid)))
    got.catch(() => idColumns.delete(key))
    idColumns.set(key, got)
  }
  return got
}

const LINE_FIELDS = ['nhdplusid', 'gnis_name', 'reachcode', 'lengthkm', 'totdasqkm', 'slope', 'fcode', 'streamorde', 'x', 'y', 'parts']

function num(v: unknown): number | null {
  return v === null || v === undefined ? null : Number(v)
}

/** A flowline's record by id from the region that holds it (hr.parse_feature's fields and guards). */
export async function flowlineById(src: DataSource, folder: string, region: RegionMeta, id: number): Promise<FlowlineRecord | null> {
  const row = (await regionIds(src, folder + region.lines)).indexOf(id)
  return row < 0 ? null : flowlineAt(src, folder, region, row)
}

const v2Reaches = new Map<number, FlowlineRecord>()

/** A V2 reach read this session (a raindrop's, a pick's), or undefined. */
export function knownReach(comid: number): FlowlineRecord | undefined {
  return v2Reaches.get(comid)
}

/** The flowline record at a row of a region's lines file. */
export async function flowlineAt(src: DataSource, folder: string, region: RegionMeta, row: number): Promise<FlowlineRecord | null> {
  const rec = await readFlowline(src, folder, region, row)
  if (rec && folder === 'bundle/v2/') v2Reaches.set(rec.nhdplusid, rec)
  return rec
}

async function readFlowline(src: DataSource, folder: string, region: RegionMeta, row: number): Promise<FlowlineRecord | null> {
  const logical = folder + region.lines
  const [r] = await readRows(src, logical, LINE_FIELDS, row, row + 1)
  if (!r) return null
  const xs = r.x as ArrayLike<number>
  const ys = r.y as ArrayLike<number>
  const parts: [number, number][][] = []
  let k = 0
  for (const count of Array.from(r.parts as ArrayLike<number>, Number)) {
    const part: [number, number][] = []
    for (let j = 0; j < count; j++, k++) part.push([Number(xs[k]) / 1e5, Number(ys[k]) / 1e5])
    parts.push(part)
  }
  const name = r.gnis_name == null ? null : String(r.gnis_name).trim() || null
  const rc = r.reachcode
  const da = num(r.totdasqkm)
  const slope = num(r.slope)
  return {
    nhdplusid: Number(r.nhdplusid), gnis_name: name,
    reachcode: rc === null || rc === undefined ? null : typeof rc === 'string' ? rc : String(rc).padStart(14, '0'),
    lengthkm: num(r.lengthkm), totdasqkm: da !== null && da > 0 ? da : null,
    slope: slope !== null && slope >= 0 ? slope : null, fcode: num(r.fcode), stream_order: num(r.streamorde),
    vpuid: region.vpu,
    geometry: parts.length === 1 ? { type: 'LineString', coordinates: parts[0] } : { type: 'MultiLineString', coordinates: parts },
  }
}

// ------------------------------------------------------------------ the raindrop

export const RAINDROP_METHOD = 'staf-data-bundle-v2-catchment'

export interface Raindrop { comid: number, snap_lat: number, snap_lon: number, method: string, reach: FlowlineRecord }

/** NLDI's hydrolocation answer from the bundle (bundle.raindrop): the V2 reach whose catchment contains
 * the point and the nearest point on its flowline, or null where no V2 catchment holds the point. */
export async function raindrop(src: DataSource, lat: number, lon: number): Promise<Raindrop | null> {
  const found = await catchmentAt(src, 'bundle/v2/', lat, lon)
  if (!found) return null
  const reach = await flowlineById(src, 'bundle/v2/', found.region, found.id)
  if (!reach) return null
  const hit = nearestPointOnLines({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: reach.geometry }] }, lat, lon)
  if (!hit) return null
  return { comid: found.id, snap_lat: Number(pyFixed(hit[0], 7)), snap_lon: Number(pyFixed(hit[1], 7)), method: RAINDROP_METHOD, reach }
}

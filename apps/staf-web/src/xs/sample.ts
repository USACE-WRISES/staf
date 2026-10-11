/**
 * A cross-section pulled from USGS's 3DEP tile files: tools/xs-grid/xsgrid/sample.py (sampling rule
 * `xsgrid-1`) with the window merge of the site engine's `dem_tiles.merge_windows`.
 *
 * Each sample point (EPSG:5070, along the section's transect) is transformed to the tile's own CRS
 * and interpolated bilinearly between the four nearest cell centres of the native grid, then
 * rounded to float32. The section's recorded tiles are read by HTTP range (geotiff.js) and pasted
 * onto the first tile's grid, the first tile with data winning a pixel. The build read its windows
 * per group of sections, so a single pull agrees with the archive to within the last float32 bit
 * (measured 2026-10-10: 3 m and 10 m bit for bit, 1 m within one step on a few samples).
 */
import { BaseClient, BaseResponse, fromCustomClient, type GeoTIFF, type GeoTIFFImage } from 'geotiff'
import { fetchRange } from '../data/range'
import { stations } from './codec'
import { profileCrc } from './crc32'
import { registerLzw } from './lzw'
import { transform } from './proj'
import { from5070 } from './projExact'

registerLzw()

/** A checked range read handed to geotiff.js. */
class CheckedResponse extends BaseResponse {
  constructor(private readonly data: ArrayBuffer) {
    super()
  }

  get status(): number {
    return 206
  }

  getHeader(): string | undefined {
    return undefined
  }

  async getData(): Promise<ArrayBuffer> {
    return this.data
  }
}

/** geotiff.js's tile reads, each held to the bytes it asked for (`fetchRange`): a block that
 * arrives short is read again rather than decoded. */
class CheckedClient extends BaseClient {
  async request(options: RequestInit = {}): Promise<BaseResponse> {
    const range = new Headers(options.headers).get('range') ?? ''
    const m = /^bytes=(\d+)-(\d+)$/.exec(range)
    if (!m) throw new Error(`${this.url}: a tile read without a byte range (${range})`)
    const data = await fetchRange(this.url, Number(m[1]), Number(m[2]) + 1, { signal: options.signal ?? undefined })
    return new CheckedResponse(data)
  }
}

const BASE = 'https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/'

/** The recorded source of a section (`source`, `tiles` from the release's sections file). */
export interface SectionSource {
  res: number // DEM metres: 1, 3, 10 (0: none)
  source: string // the 1 m project, `3dep-19` or `3dep-13`
  tiles: string // `;`-joined tile names, as the build recorded them
}

/** The files a section was sampled from, in the build's merge order. The build pastes tiles in
 * catalog order, and both catalogs are sorted by name (checked 2026-10-10), while a section records
 * its 1 m tiles newest first: sorting the names restores the paste order. */
export function tileUrls(src: SectionSource): string[] {
  const names = src.tiles.split(';').filter(Boolean).sort()
  if (src.res === 1) return names.map((n) => `${BASE}1m/Projects/${src.source}/TIFF/${n}`)
  if (src.res === 3) return names.map((n) => `${BASE}19/TILES/${n}/${n}.img`)
  if (src.res === 10) return names.map((n) => `${BASE}13/TIFF/current/${n.replace(/^USGS_13_/, '').replace(/\.tif$/, '')}/${n}`)
  return []
}

/** Python's `round` (half to even), as `int(round(x))` in `merge_windows`. */
export function pyRound(x: number): number {
  const f = Math.floor(x)
  const d = x - f
  if (d > 0.5) return f + 1
  if (d < 0.5) return f
  return f % 2 === 0 ? f : f + 1
}

export interface Grid {
  data: Float32Array // row-major, NaN where no tile answered
  width: number
  height: number
  c: number // x of the top-left corner
  a: number // pixel width
  f: number // y of the top-left corner
  e: number // pixel height (negative)
}

/** `sample.bilinear`: bilinear between the four nearest cell centres; NaN when any is missing. */
export function bilinear(g: Grid, X: ArrayLike<number>, Y: ArrayLike<number>): Float64Array {
  const out = new Float64Array(X.length).fill(NaN)
  for (let i = 0; i < X.length; i++) {
    const fj = (X[i] - g.c) / g.a - 0.5
    const fi = (Y[i] - g.f) / g.e - 0.5
    const j0 = Math.floor(fj)
    const i0 = Math.floor(fi)
    if (j0 < 0 || i0 < 0 || j0 + 1 >= g.width || i0 + 1 >= g.height) continue
    const tx = fj - j0
    const ty = fi - i0
    const z00 = g.data[i0 * g.width + j0]
    const z01 = g.data[i0 * g.width + j0 + 1]
    const z10 = g.data[(i0 + 1) * g.width + j0]
    const z11 = g.data[(i0 + 1) * g.width + j0 + 1]
    out[i] = (1.0 - ty) * ((1.0 - tx) * z00 + tx * z01) + ty * ((1.0 - tx) * z10 + tx * z11)
  }
  return out
}

interface OpenTile {
  url: string
  image: GeoTIFFImage
  epsg: number
  c: number
  f: number
  a: number
  e: number
  width: number
  height: number
  nodata: number | null
}

const opened = new Map<string, Promise<OpenTile>>()

/** A tile's header (cached per page): grid, CRS and nodata. */
export function openTile(url: string): Promise<OpenTile> {
  let p = opened.get(url)
  if (!p) {
    p = (async () => {
      const tiff: GeoTIFF = await fromCustomClient(new CheckedClient(url), { allowFullFile: false })
      const image = await tiff.getImage()
      const [c, f] = image.getOrigin()
      const [a, e] = image.getResolution()
      const keys = image.getGeoKeys() ?? {}
      const epsg = Number(keys.ProjectedCSTypeGeoKey ?? keys.GeographicTypeGeoKey ?? 0)
      const nd = image.getGDALNoData()
      return { url, image, epsg, c, f, a, e, width: image.getWidth(), height: image.getHeight(), nodata: nd }
    })()
    opened.set(url, p)
    p.catch(() => opened.delete(url))
  }
  return p
}

/** `dem_tiles.merge_windows`: the tiles over `bounds` on the first tile's grid, the first with data winning. */
export async function mergeWindows(urls: string[], bounds: [number, number, number, number]): Promise<Grid> {
  const tiles = await Promise.all(urls.map(openTile))
  const ref = tiles[0]
  const { a, e } = ref
  const [minx, miny, maxx, maxy] = bounds
  const col0 = Math.floor((minx - ref.c) / a)
  const col1 = Math.ceil((maxx - ref.c) / a)
  const row0 = Math.floor((maxy - ref.f) / e)
  const row1 = Math.ceil((miny - ref.f) / e)
  const width = Math.max(col1 - col0, 1)
  const height = Math.max(row1 - row0, 1)
  const data = new Float32Array(width * height).fill(NaN)
  const c = ref.c + col0 * a
  const f = ref.f + row0 * e
  const reads = tiles.map(async (t) => {
    const dc = pyRound((t.c - c) / a)
    const dr = pyRound((t.f - f) / e)
    const c0 = Math.max(0, dc)
    const c1 = Math.min(width, dc + t.width)
    const r0 = Math.max(0, dr)
    const r1 = Math.min(height, dr + t.height)
    if (c1 <= c0 || r1 <= r0) return null
    const win = [c0 - dc, r0 - dr, c1 - dc, r1 - dr]
    let rasters
    try {
      rasters = await t.image.readRasters({ window: win, samples: [0], interleave: true })
    } catch (err) {
      throw new Error(`${t.url} window ${win.join(',')}: ${String(err)}`)
    }
    return { t, c0, c1, r0, r1, values: rasters as unknown as ArrayLike<number> }
  })
  for (const got of await Promise.all(reads)) {
    if (!got) continue // paste in list order: the first tile with data keeps a pixel
    const { t, c0, c1, r0, r1, values } = got
    const nd = t.nodata === null ? null : Math.fround(t.nodata)
    const w = c1 - c0
    for (let r = r0; r < r1; r++) {
      for (let col = c0; col < c1; col++) {
        const v = Math.fround(values[(r - r0) * w + (col - c0)])
        const masked = !Number.isFinite(v) || v <= -1e30 || (nd !== null && v === nd)
        const o = r * width + col
        if (!masked && Number.isNaN(data[o])) data[o] = v
      }
    }
  }
  return { data, width, height, c, a, f, e }
}

export interface PullSection {
  x: number
  y: number
  nx: number
  ny: number
  wide: number
  nPts: number
}

/** One read the build made of a section: its CRS and the corner and pixel size of the window
 * (the release's `grid_epsg`, `grid_c`, `grid_f`, `grid_a`, `grid_e`). */
export interface ReadGrid {
  epsg: number
  c: number
  f: number
  a: number
  e: number
}

/** How far this page's projection of a sample can land from the build's (PROJ on the Windows C runtime):
 * the port in projExact.ts follows PROJ's arithmetic, but the browser's sin, cos, atan2, asin, log and
 * asinh round differently now and then (measured 2026-10-11 on 400,000 points: at most 1.1e-8 m and
 * 1e-13 degrees). Ten times that, per unit of the read's CRS. */
const PROJECTION_SLACK = { metres: 1.1e-7, degrees: 1e-12 }

/** A pull before rounding: each sample's float64 value, and how far it may sit from the build's. */
export interface PullCore {
  z64: Float64Array
  slack: Float64Array
}

/** A section's samples pulled again from USGS on the grids the build read it on (xsgrid/origins.py
 * `pull`): per read, its recorded tiles of that CRS are pasted at the build's offsets from the recorded
 * corner and interpolated with the build's arithmetic, filling what earlier reads left missing. The
 * samples are projected with PROJ's own arithmetic (projExact.ts); each one's slack bounds what the
 * browser's math functions can move it: the local slope times the projection's error. */
export async function pullCore(sec: PullSection, src: SectionSource, reads: ReadGrid[]): Promise<PullCore> {
  const ts = stations(sec.wide, sec.nPts)
  const X = new Float64Array(sec.nPts)
  const Y = new Float64Array(sec.nPts)
  for (let i = 0; i < sec.nPts; i++) {
    X[i] = sec.x + sec.nx * ts[i]
    Y[i] = sec.y + sec.ny * ts[i]
  }
  const urls = tileUrls(src)
  const tiles = await Promise.all(urls.map(openTileOrAlternate))
  const z = new Float64Array(sec.nPts).fill(NaN)
  const slack = new Float64Array(sec.nPts)
  for (const g of reads) {
    const mine = tiles.filter((t) => (src.res === 1 ? t.epsg === g.epsg : true))
    if (!mine.length) continue
    const [u, v] = project(g.epsg, X, Y)
    const off = g.epsg === 4269 ? PROJECTION_SLACK.degrees : PROJECTION_SLACK.metres
    const px = Math.max(off / Math.abs(g.a), off / Math.abs(g.e)) // in pixels
    let j0 = Infinity
    let j1 = -Infinity
    let i0 = Infinity
    let i1 = -Infinity
    const fj = new Float64Array(u.length)
    const fi = new Float64Array(u.length)
    for (let i = 0; i < u.length; i++) {
      fj[i] = (u[i] - g.c) / g.a - 0.5
      fi[i] = (v[i] - g.f) / g.e - 0.5
      j0 = Math.min(j0, Math.floor(fj[i]))
      j1 = Math.max(j1, Math.floor(fj[i]) + 2)
      i0 = Math.min(i0, Math.floor(fi[i]))
      i1 = Math.max(i1, Math.floor(fi[i]) + 2)
    }
    const width = j1 - j0
    const height = i1 - i0
    const buf = new Float32Array(width * height).fill(NaN)
    const reads2 = mine.map(async (t) => {
      const dc = pyRound((t.c - g.c) / g.a)
      const dr = pyRound((t.f - g.f) / g.e)
      const c0 = Math.max(j0, dc)
      const c1 = Math.min(j1, dc + t.width)
      const r0 = Math.max(i0, dr)
      const r1 = Math.min(i1, dr + t.height)
      if (c1 <= c0 || r1 <= r0) return null
      const win = [c0 - dc, r0 - dr, c1 - dc, r1 - dr]
      let values
      try {
        values = await t.image.readRasters({ window: win, samples: [0], interleave: true })
      } catch (err) {
        throw new Error(`${t.url} window ${win.join(',')}: ${String(err)}`)
      }
      return { t, c0, c1, r0, r1, values: values as unknown as ArrayLike<number> }
    })
    for (const got of await Promise.all(reads2)) {
      if (!got) continue // in name order: the first tile with data keeps a pixel
      const { t, c0, c1, r0, r1, values } = got
      const nd = t.nodata === null ? null : Math.fround(t.nodata)
      const w = c1 - c0
      for (let r = r0; r < r1; r++) {
        for (let col = c0; col < c1; col++) {
          const val = Math.fround(values[(r - r0) * w + (col - c0)])
          const masked = !Number.isFinite(val) || val <= -1e30 || (nd !== null && val === nd)
          const o = (r - i0) * width + (col - j0)
          if (!masked && Number.isNaN(buf[o])) buf[o] = val
        }
      }
    }
    for (let i = 0; i < u.length; i++) {
      if (Number.isFinite(z[i])) continue
      const jj = Math.floor(fj[i])
      const ii = Math.floor(fi[i])
      const tx = fj[i] - jj
      const ty = fi[i] - ii
      const b = (ii - i0) * width + (jj - j0)
      const z00 = buf[b]
      const z01 = buf[b + 1]
      const z10 = buf[b + width]
      const z11 = buf[b + width + 1]
      const got = (1.0 - ty) * ((1.0 - tx) * z00 + tx * z01) + ty * ((1.0 - tx) * z10 + tx * z11)
      if (Number.isFinite(got)) {
        z[i] = got
        const steep = Math.abs(z01 - z00) + Math.abs(z11 - z10) + Math.abs(z10 - z00) + Math.abs(z11 - z01)
        slack[i] = steep * px + Math.abs(got) * 2 ** -48
      }
    }
  }
  return { z64: z, slack }
}

/** A section's float32 samples pulled again from USGS (`pullCore`, rounded). */
export async function pullOnGrids(sec: PullSection, src: SectionSource, reads: ReadGrid[]): Promise<Float32Array> {
  return Float32Array.from((await pullCore(sec, src, reads)).z64)
}

export interface ExactPull {
  z: Float32Array
  /** the profile's checksum is the release's: the archive's samples bit for bit */
  verified: boolean
  /** samples rounded the other way to meet the checksum */
  adjusted: number
  /** samples whose rounding the projection's slack left open */
  open: number
}

/** A pull resolved by the release's profile checksum (`z_crc`, format 3): a sample whose float64 value
 * lies within its slack of a float32 rounding boundary could have rounded either way in the build, so
 * each such choice is tried until the profile's checksum is the archive's. Usually none is open and the
 * checksum just confirms the pull. */
export async function pullExact(sec: PullSection, src: SectionSource, reads: ReadGrid[], crc: number | null): Promise<ExactPull> {
  const core = await pullCore(sec, src, reads)
  return resolveByChecksum(core.z64, core.slack, crc)
}

const F32 = new Float32Array(1)
const U32 = new Uint32Array(F32.buffer)

/** The float32 next to `f` toward +Infinity (`up`) or -Infinity. */
export function nextFloat32(f: number, up: boolean): number {
  if (f === 0) return up ? 1.401298464324817e-45 : -1.401298464324817e-45
  F32[0] = f
  U32[0] += (f > 0) === up ? 1 : -1
  return F32[0]
}

/** The checksum search of `pullExact`, on values already pulled. Tries the open samples' other rounding
 * (at most 2^12 combinations), then once more with ten times the slack. */
export function resolveByChecksum(z64: Float64Array, slack: Float64Array, crc: number | null): ExactPull {
  const z = Float32Array.from(z64)
  if (crc === null) return { z, verified: false, adjusted: 0, open: 0 }
  if (profileCrc(z) === crc) return { z, verified: true, adjusted: 0, open: 0 }
  let open = 0
  for (const widen of [1, 10]) {
    const at: number[] = []
    const other: number[] = []
    for (let i = 0; i < z64.length; i++) {
      const v = z64[i]
      if (!Number.isFinite(v)) continue
      const f = z[i]
      const alt = nextFloat32(f, v >= f)
      if (Math.abs(v - (f + alt) / 2) <= slack[i] * widen) {
        at.push(i)
        other.push(alt)
      }
    }
    open = at.length
    if (!open || open > 12) continue
    const trial = z.slice()
    for (let mask = 1; mask < 1 << open; mask++) {
      let changed = 0
      for (let j = 0; j < open; j++) {
        const flip = (mask >> j) & 1
        trial[at[j]] = flip ? other[j] : z[at[j]]
        changed += flip
      }
      if (profileCrc(trial) === crc) return { z: trial, verified: true, adjusted: changed, open }
    }
  }
  return { z, verified: false, adjusted: 0, open }
}

/** EPSG:5070 to a read's CRS: PROJ's arithmetic for the tiles' CRSs (NAD83 UTM, NAD83 geographic), proj4
 * for anything else. */
function project(epsg: number, X: Float64Array, Y: Float64Array): [ArrayLike<number>, ArrayLike<number>] {
  if (epsg === 4269 || (epsg > 26900 && epsg <= 26923)) return from5070(epsg, X, Y)
  return transform(5070, epsg, X, Y)
}

/** A 3 m quad's file can be named `img<name>_19.img` (105 of 8,345; `sample.quad_url`). */
async function openTileOrAlternate(url: string): Promise<OpenTile> {
  try {
    return await openTile(url)
  } catch (err) {
    const m = /\/19\/TILES\/([^/]+)\/\1\.img$/.exec(url)
    if (!m) throw err
    return openTile(url.replace(/\/([^/]+)\.img$/, `/img${m[1]}_19.img`))
  }
}

/** A section's float32 samples pulled again from its recorded tiles over its own window (no recorded
 * grids: within a few float32 steps of the archive). */
export async function pullSection(sec: PullSection, src: SectionSource): Promise<Float32Array> {
  const ts = stations(sec.wide, sec.nPts)
  const X = new Float64Array(sec.nPts)
  const Y = new Float64Array(sec.nPts)
  for (let i = 0; i < sec.nPts; i++) {
    X[i] = sec.x + sec.nx * ts[i]
    Y[i] = sec.y + sec.ny * ts[i]
  }
  const urls = tileUrls(src)
  const z = new Float64Array(sec.nPts).fill(NaN)
  if (src.res === 1) {
    // a project crossing UTM zones: each zone's tiles in turn, filling what is still missing
    const tiles = await Promise.all(urls.map(openTile))
    const byEpsg = new Map<number, string[]>()
    tiles.forEach((t, i) => byEpsg.set(t.epsg, [...(byEpsg.get(t.epsg) ?? []), urls[i]]))
    for (const [epsg, group] of byEpsg) {
      const [u, v] = transform(5070, epsg, X, Y)
      const got = bilinear(await mergeWindows(group, padBounds(u, v, 2.5)), u, v)
      for (let i = 0; i < z.length; i++) if (!Number.isFinite(z[i]) && Number.isFinite(got[i])) z[i] = got[i]
    }
  } else if (urls.length) {
    const [u, v] = transform(5070, 4269, X, Y)
    const pad = src.res === 3 ? 2.5 / 32400.0 : 2.5 / 10800.0
    z.set(bilinear(await mergeWindows(urls, padBounds(u, v, pad)), u, v))
  }
  return Float32Array.from(z)
}

function padBounds(u: Float64Array, v: Float64Array, pad: number): [number, number, number, number] {
  let minx = Infinity
  let miny = Infinity
  let maxx = -Infinity
  let maxy = -Infinity
  for (let i = 0; i < u.length; i++) {
    if (u[i] < minx) minx = u[i]
    if (u[i] > maxx) maxx = u[i]
    if (v[i] < miny) miny = v[i]
    if (v[i] > maxy) maxy = v[i]
  }
  return [minx - pad, miny - pad, maxx + pad, maxy + pad]
}

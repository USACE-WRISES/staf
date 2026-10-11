// Point lookups from the national tables (site_engine _hrslim/points.py), over the browser layout's
// copies laid out by place (tools/staf-web-data webdata/points.py): the same rows, sorted along a
// Hilbert curve in groups of 1,024 whose statistics give each group's box, so a query reads the few
// groups near the point. ``row0`` is a row's place in the bundle's table, which restores the order
// the Python returns rows in; a station's results are the rows r0 to r0 + rn of the results table.
import { columnStats, metadata, readColumns, rowGroups, type RowGroupSpan } from '../data/parquet'
import type { DataSource } from '../data/source'
import { pyFixed } from '../app/pyfmt'

const NID = 'points/nid.parquet'
const NWIS = 'points/nwis.parquet'
const STATIONS = 'points/wqp_stations.parquet'
const RESULTS = 'points/wqp_results.parquet'
export const WQP_PARAMS = ['tn', 'tp'] as const
const EARTH_MI = 3958.7613
const EARTH_M = 6371008.8
const SQMI_PER_SQKM = 0.386102
const RAD = Math.PI / 180.0

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

/** The row groups whose lat/lon statistics meet the box (a group without statistics is read). */
async function groupsInBox(src: DataSource, logical: string, s: number, w: number, n: number, e: number): Promise<RowGroupSpan[]> {
  const meta = await metadata(src, logical)
  const lat = columnStats(meta, 'lat')
  const lon = columnStats(meta, 'lon')
  return rowGroups(meta).filter((g) => {
    const a = lat[g.index]
    const o = lon[g.index]
    if (!a || !o) return true
    return !(Number(a.max) < s || Number(a.min) > n || Number(o.max) < w || Number(o.min) > e)
  })
}

type Kinds = Record<string, 'f64' | 'i32' | 'u8'>

/** Every row of the groups meeting the box, as columns (``rows`` of them). */
async function boxRows(src: DataSource, logical: string, s: number, w: number, n: number, e: number,
                       columns: string[], typed: Kinds = {}, nan: string[] = []): Promise<{ rows: number, cols: Record<string, any[]> }> {
  await src.ready()
  const groups = await groupsInBox(src, logical, s, w, n, e)
  const parts = await Promise.all(groups.map((g) => readColumns(src, logical, columns, g.start, g.start + g.rows, typed, nan)))
  const cols: Record<string, any[]> = Object.fromEntries(columns.map((c) => [c, [] as any[]]))
  let rows = 0
  for (const p of parts) {
    for (const c of columns) cols[c].push(...Array.from(p[c] as ArrayLike<any>))
    rows += (p[columns[0]] as ArrayLike<any>).length
  }
  return { rows, cols }
}

/** points._haversine, element by element. */
function haversine(lat: number, lon: number, lat2: number, lon2: number, radius: number): number {
  const p1 = lat * RAD
  const p2 = lat2 * RAD
  const dp = (lat2 - lat) * RAD
  const dl = (lon2 - lon) * RAD
  const sp = Math.sin(dp / 2)
  const sl = Math.sin(dl / 2)
  const a = sp * sp + Math.cos(p1) * Math.cos(p2) * sl * sl
  return 2 * radius * Math.asin(Math.min(1.0, Math.sqrt(a)))
}

/** A box sure to hold every point within ``miles`` of (lat, lon) (a degree of latitude is more than 68
 * miles everywhere). */
function radiusBox(lat: number, lon: number, miles: number): [number, number, number, number] {
  const dlat = miles / 60.0
  const dlon = dlat / Math.max(Math.cos(Math.min(89.0, Math.abs(lat) + dlat) * RAD), 1e-6)
  return [lat - dlat, lon - dlon, lat + dlat, lon + dlon]
}

/** statistics.median */
function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b)
  const n = s.length
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
}

export interface Dam { name: string | null, storage: number | null, height: number | null, distance_m?: number }

export interface FlowStats {
  site: string
  name: string | null
  da_sqmi: number | null
  n_days: number
  zero_frac: number
  q10: number
  q50: number
  q90: number
  baseflow_ratio: number | null
  as_of: string | null
}

const num = (v: unknown): number | null => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v))

/** The bundle's national point tables (points.PointTables) as the browser reads them. */
export class PointTables {
  constructor(private src: DataSource) {}

  /** points.nid_box: NID dams in SFARI's box of ``miles / 69`` degrees around the point, in the
   * table's order. */
  async nidBox(lat: number, lon: number, miles = 1.0): Promise<Dam[]> {
    const dx = miles / 69.0
    const { rows, cols } = await boxRows(this.src, NID, lat - dx, lon - dx, lat + dx, lon + dx,
      ['lat', 'lon', 'name', 'nid_storage_acft', 'dam_height_ft', 'row0'], { lat: 'f64', lon: 'f64', row0: 'f64' }, ['lat', 'lon'])
    const hits: { row0: number, dam: Dam }[] = []
    for (let i = 0; i < rows; i++) {
      const x = cols.lon[i]
      const y = cols.lat[i]
      if (x >= lon - dx && x <= lon + dx && y >= lat - dx && y <= lat + dx) {
        hits.push({ row0: cols.row0[i], dam: { name: cols.name[i] ?? null, storage: num(cols.nid_storage_acft[i]), height: num(cols.dam_height_ft[i]) } })
      }
    }
    return hits.sort((a, b) => a.row0 - b.row0).map((h) => h.dam)
  }

  /** points.nid_radius: mapped NID dams within ``miles`` (geodesic), nearest first (EASI's and
   * DEEP's barriers_near). */
  async nidRadius(lat: number, lon: number, miles = 1.0): Promise<Dam[]> {
    const radiusM = miles * 1609.344
    const [s, w, n, e] = radiusBox(lat, lon, miles * 1.01)
    const { rows, cols } = await boxRows(this.src, NID, s, w, n, e,
      ['lat', 'lon', 'name', 'nid_storage_acft', 'dam_height_ft', 'row0'], { lat: 'f64', lon: 'f64', row0: 'f64' }, ['lat', 'lon'])
    const hits: { row0: number, dam: Dam }[] = []
    for (let i = 0; i < rows; i++) {
      if (!(haversine(lat, lon, cols.lat[i], cols.lon[i], EARTH_M) <= radiusM + 1.0)) continue
      const d = haversine(lat, lon, cols.lat[i], cols.lon[i], EARTH_M)
      if (d <= radiusM) {
        hits.push({ row0: cols.row0[i], dam: { name: cols.name[i] ?? null, storage: num(cols.nid_storage_acft[i]), height: num(cols.dam_height_ft[i]),
          distance_m: Number(pyFixed(d, 1)) } })
      }
    }
    hits.sort((a, b) => a.row0 - b.row0)
    const dams = hits.map((x) => x.dam)
    const key = (x: Dam) => String(x.name ?? '')
    return dams.sort((a, b) => (a.distance_m! - b.distance_m!) || (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0))
  }

  /** The WQP stations within ``withinMi`` of the point: ``[station number, first result row, results]``. */
  private async stationsNear(lat: number, lon: number, withinMi: number): Promise<[number, number, number][]> {
    const [s, w, n, e] = radiusBox(lat, lon, withinMi)
    const { rows, cols } = await boxRows(this.src, STATIONS, s, w, n, e, ['lat', 'lon', 'row0', 'r0', 'rn'],
      { lat: 'f64', lon: 'f64', row0: 'f64', r0: 'f64', rn: 'f64' }, ['lat', 'lon'])
    const out: [number, number, number][] = []
    for (let i = 0; i < rows; i++) {
      if (haversine(lat, lon, cols.lat[i], cols.lon[i], EARTH_MI) <= withinMi) out.push([cols.row0[i], cols.r0[i], cols.rn[i]])
    }
    return out.sort((a, b) => a[0] - b[0])
  }

  /** The result rows [r0, r0 + rn) of each station, read from the groups that hold them. */
  private async results(spans: [number, number][], columns: string[], typed: Kinds, nan: string[]): Promise<Record<string, any[]>> {
    const meta = await metadata(this.src, RESULTS)
    const groups = rowGroups(meta).filter((g) => spans.some(([a, k]) => k > 0 && a < g.start + g.rows && a + k > g.start))
    const read = new Map<number, Record<string, any>>()
    await Promise.all(groups.map(async (g) => read.set(g.start, await readColumns(this.src, RESULTS, columns, g.start, g.start + g.rows, typed, nan))))
    const out: Record<string, any[]> = Object.fromEntries(columns.map((c) => [c, [] as any[]]))
    for (const [a, k] of spans) {
      for (let r = a; r < a + k; r++) {
        const g = groups.find((x) => r >= x.start && r < x.start + x.rows)
        if (!g) continue
        const t = read.get(g.start)!
        for (const c of columns) out[c].push(t[c][r - g.start])
      }
    }
    return out
  }

  /** points.wqp_sfari: SFARI's median of every numeric TN or TP result since ``startMs`` (2015 by
   * default) at stations within ``withinMi`` miles, to three places; null without any. */
  async wqpSfari(param: string, lat: number, lon: number, withinMi = 5.0, startMs = Date.UTC(2015, 0, 1)): Promise<number | null> {
    const code = (WQP_PARAMS as readonly string[]).indexOf(param)
    if (code < 0) return null
    const near = await this.stationsNear(lat, lon, withinMi)
    if (!near.length) return null
    const r = await this.results(near.map(([, a, k]) => [a, k]), ['param', 'date', 'raw'], { param: 'i32', date: 'f64', raw: 'f64' }, ['raw'])
    const vals: number[] = []
    for (let i = 0; i < r.raw.length; i++) {
      if (r.date[i] >= startMs && r.param[i] === code && !Number.isNaN(r.raw[i])) vals.push(r.raw[i])
    }
    return vals.length ? pyRound(median(vals), 3) : null
  }

  /** points.nwis_flow_stats: SFARI's gage in the 0.25 degree box, ranked by drainage-area ratio and
   * distance, the first of the top five with 60 or more daily values; null without one. */
  async nwisFlowStats(lat: number, lon: number, daSqkm: number | null | undefined): Promise<FlowStats | null> {
    const [w, s, e, n] = [lon - 0.25, lat - 0.25, lon + 0.25, lat + 0.25].map((v) => Number(pyFixed(v, 5)))
    const columns = ['lat', 'lon', 'site', 'name', 'da_sqmi', 'n_days', 'zero_frac', 'q10', 'q50', 'q90', 'baseflow_ratio', 'as_of', 'row0']
    const { rows, cols } = await boxRows(this.src, NWIS, s, w, n, e, columns,
      { lat: 'f64', lon: 'f64', da_sqmi: 'f64', zero_frac: 'f64', q10: 'f64', q50: 'f64', q90: 'f64', baseflow_ratio: 'f64', row0: 'f64' },
      ['lat', 'lon', 'da_sqmi', 'zero_frac', 'q10', 'q50', 'q90', 'baseflow_ratio'])
    const inside: number[] = []
    for (let i = 0; i < rows; i++) {
      if (cols.lon[i] >= w && cols.lon[i] <= e && cols.lat[i] >= s && cols.lat[i] <= n) inside.push(i)
    }
    inside.sort((a, b) => cols.row0[a] - cols.row0[b])
    const daSqmi = daSqkm ? daSqkm * SQMI_PER_SQKM : null
    const key = (i: number): [number, number] => {
      const dist = Math.sqrt((cols.lat[i] - lat) ** 2 + (cols.lon[i] - lon) ** 2)
      const gda = cols.da_sqmi[i]
      if (daSqmi && gda) {
        const ratio = gda / daSqmi
        if (ratio < 0.3 || ratio > 3.5) return [2, dist]
        return [0, Math.abs(1.0 - ratio) + dist * 5]
      }
      return [1, dist]
    }
    const keys = new Map(inside.map((i) => [i, key(i)]))
    const ranked = [...inside].sort((a, b) => {
      const ka = keys.get(a)!
      const kb = keys.get(b)!
      return ka[0] - kb[0] || (ka[1] < kb[1] ? -1 : ka[1] > kb[1] ? 1 : 0)
    })
    for (const i of ranked.slice(0, 5)) {
      const days = cols.n_days[i] == null ? 0 : Number(cols.n_days[i])
      if (days < 60) continue
      const gda = cols.da_sqmi[i]
      const ratio = cols.baseflow_ratio[i]
      return {
        site: String(cols.site[i]), name: cols.name[i] ?? null, da_sqmi: Number.isNaN(gda) ? null : gda,
        n_days: Math.trunc(days), zero_frac: cols.zero_frac[i], q10: cols.q10[i], q50: cols.q50[i], q90: cols.q90[i],
        baseflow_ratio: Number.isNaN(ratio) ? null : ratio, as_of: cols.as_of[i] == null ? null : String(cols.as_of[i]),
      }
    }
    return null
  }
}

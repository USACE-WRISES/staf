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
const TEMPERATURE = 'points/wqp_temperature.parquet'
const NAS = 'points/nas.parquet'
export const WQP_PARAMS = ['tn', 'tp'] as const
/** EASI's exclusion reasons by code (hrbuild.tables.WQP_REASONS), temperature's after them */
const WQP_REASONS = ['ok', 'blank', 'rejected', 'censored', 'non_total_fraction', 'unsupported_unit', 'nonnumeric']
const TEMP_REASONS = [...WQP_REASONS, 'fahrenheit', 'fahrenheit_implausible', 'celsius_implausible', 'celsius_seasonal']
const FAHRENHEIT = TEMP_REASONS.indexOf('fahrenheit')
const FAHRENHEIT_IMPLAUSIBLE = TEMP_REASONS.indexOf('fahrenheit_implausible')
const SCREENED = [TEMP_REASONS.indexOf('celsius_implausible'), TEMP_REASONS.indexOf('celsius_seasonal')]

interface Station { row0: number, id: string, name: string | null, lat: number, lon: number, r0: number, rn: number, t0: number, tn: number }

/** points._ten_years_before(date.today()): today's local date ten years back (a leap day to the 28th). */
export function tenYearsBeforeToday(today = new Date()): [number, number, number] {
  const y = today.getFullYear() - 10
  const m = today.getMonth() + 1
  let d = today.getDate()
  if (m === 2 && d === 29) d = 28
  return [y, m, d]
}
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

  /** The WQP stations within ``withinMi`` of the point with their ids, names, places and result rows,
   * by station number (the bundle's order). */
  private async stationsNearFull(lat: number, lon: number, withinMi: number): Promise<Station[]> {
    const [s, w, n, e] = radiusBox(lat, lon, withinMi)
    const { rows, cols } = await boxRows(this.src, STATIONS, s, w, n, e, ['station', 'name', 'lat', 'lon', 'row0', 'r0', 'rn', 't0', 'tn'],
      { lat: 'f64', lon: 'f64', row0: 'f64', r0: 'f64', rn: 'f64', t0: 'f64', tn: 'f64' }, ['lat', 'lon'])
    const out: Station[] = []
    for (let i = 0; i < rows; i++) {
      if (haversine(lat, lon, cols.lat[i], cols.lon[i], EARTH_MI) <= withinMi) {
        out.push({ row0: cols.row0[i], id: String(cols.station[i]), name: cols.name[i] ?? null, lat: cols.lat[i], lon: cols.lon[i],
          r0: cols.r0[i], rn: cols.rn[i], t0: cols.t0[i], tn: cols.tn[i] })
      }
    }
    return out.sort((a, b) => a.row0 - b.row0)
  }

  /** points.wqp_easi: EASI's station-balanced TN, TP or water-temperature summary from the bundle's
   * results (the last ten years by default, within 5 miles; EASI's exclusions and unit rules; for
   * temperature, results reported in Fahrenheit at stations with no Celsius result, converted, and
   * unrealistic Celsius results set apart). ``start`` is ``[year, month, day]``. */
  async wqpEasi(param: string, lat: number, lon: number, withinMi = 5.0, start: [number, number, number] | null = null): Promise<Record<string, any> | null> {
    if (!['tn', 'tp', 'temp'].includes(param)) return null
    const [y, m, d] = start ?? tenYearsBeforeToday()
    const startMs = Date.UTC(y, m - 1, d)
    const temp = param === 'temp'
    const near = await this.stationsNearFull(lat, lon, withinMi)
    const spans: [number, number][] = near.map((st) => (temp ? [st.t0, st.tn] : [st.r0, st.rn]))
    const r = near.length
      ? (temp ? await this.resultRows(TEMPERATURE, spans, ['date', 'value_c100', 'reason'], { date: 'f64', value_c100: 'f64', reason: 'i32' }, ['value_c100'])
        : await this.resultRows(RESULTS, spans, ['param', 'date', 'raw', 'reason', 'milli'], { param: 'i32', date: 'f64', raw: 'f64', reason: 'i32' }, ['raw']))
      : null
    // the rows in the window, by station (each row's station is the span it came from)
    const rows: { st: Station, i: number }[] = []
    if (r) {
      let k = 0
      near.forEach((st, si) => {
        for (let j = 0; j < spans[si][1]; j++, k++) {
          if (r.date[k] < startMs) continue
          if (!temp && r.param[k] !== WQP_PARAMS.indexOf(param as 'tn' | 'tp')) continue
          rows.push({ st, i: k })
        }
      })
    }
    // temperature: per station, Celsius results when it has one, else its results converted from Fahrenheit
    const choice = new Map<number, string>()
    if (temp) {
      for (const st of near) {
        const mine = rows.filter((x) => x.st === st).map((x) => r!.reason[x.i])
        if (!mine.length) continue
        choice.set(st.row0, mine.includes(0) ? 'celsius' : mine.includes(FAHRENHEIT) ? 'fahrenheit' : 'none')
      }
    }
    const used = (st: Station, code: number) => (!temp ? code === 0
      : (code === 0 && choice.get(st.row0) === 'celsius') || (code === FAHRENHEIT && choice.get(st.row0) === 'fahrenheit'))
    const excluded: Record<string, number> = { blank: 0, nonnumeric: 0, unsupported_unit: 0, non_total_fraction: 0, rejected: 0, censored: 0 }
    if (temp) excluded.implausible = 0
    const byStation = new Map<string, number[]>()
    const distances = new Map<string, number>()
    let first: number | null = null
    let last: number | null = null
    let converted = 0
    for (const { st, i } of rows) {
      const code = r!.reason[i]
      if (!used(st, code)) {
        let name = (temp ? TEMP_REASONS : WQP_REASONS)[code]
        if (temp && (SCREENED.includes(code) || code === FAHRENHEIT_IMPLAUSIBLE)) name = 'implausible'
        excluded[name in excluded ? name : 'unsupported_unit'] += 1
        continue
      }
      if (temp && code === FAHRENHEIT) converted += 1
      const value = temp ? r!.value_c100[i] / 100.0 : r!.raw[i] * (r!.milli[i] ? 0.001 : 1.0)
      if (!byStation.has(st.id)) byStation.set(st.id, [])
      byStation.get(st.id)!.push(value)
      const day = r!.date[i]
      if (first === null || day < first) first = day
      if (last === null || day > last) last = day
      if (!distances.has(st.id)) distances.set(st.id, haversine(lat, lon, st.lat, st.lon, EARTH_MI))
    }
    const medians = new Map([...byStation].map(([k, v]) => [k, median(v)]))
    const value = medians.size ? median([...medians.values()]) : null
    const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10)
    const out: Record<string, any> = {
      parameter: param,
      value: value === null ? null : pyRound(value, 4),
      units: temp ? '°C' : 'mg/L',
      observation_count: [...byStation.values()].reduce((a, v) => a + v.length, 0),
      station_count: byStation.size,
      date_start: first === null ? null : iso(first),
      date_end: last === null ? null : iso(last),
      nearest_distance_mi: distances.size ? pyRound(Math.min(...distances.values()), 3) : null,
      excluded_count: Object.values(excluded).reduce((a, b) => a + b, 0),
      excluded,
      station_medians: Object.fromEntries([...medians].map(([k, v]) => [k, pyRound(v, 4)])),
      query_ok: true,
    }
    if (temp) out.fahrenheit_converted = converted
    return out
  }

  /** The rows [a, a + k) of each span of a results table, in span order, as columns. */
  private async resultRows(logical: string, spans: [number, number][], columns: string[], typed: Kinds, nan: string[]): Promise<Record<string, any[]>> {
    const meta = await metadata(this.src, logical)
    const groups = rowGroups(meta).filter((g) => spans.some(([a, k]) => k > 0 && a < g.start + g.rows && a + k > g.start))
    const read = new Map<number, Record<string, any>>()
    await Promise.all(groups.map(async (g) => read.set(g.start, await readColumns(this.src, logical, columns, g.start, g.start + g.rows, typed, nan))))
    const out: Record<string, any[]> = Object.fromEntries(columns.map((c) => [c, [] as any[]]))
    for (const [a, k] of spans) {
      for (let row = a; row < a + k; row++) {
        const g = groups.find((x) => row >= x.start && row < x.start + x.rows)!
        const t = read.get(g.start)!
        for (const c of columns) out[c].push(t[c][row - g.start])
      }
    }
    return out
  }

  /** points.nas_established: the sorted scientific names of the established NAS records in the HUC12,
   * else the HUC8; null without either. */
  async nasEstablished(huc12: string | null, huc8: string | null): Promise<string[] | null> {
    if (!huc12 && !huc8) return null
    const [column, key] = huc12 ? ['huc12', String(huc12)] : ['huc8', String(huc8)]
    await this.src.ready()
    const meta = await metadata(this.src, NAS)
    const stats = columnStats(meta, column)
    const groups = rowGroups(meta).filter((g) => {
      const s = stats[g.index]
      return !s || (String(s.min) <= key && key <= String(s.max))
    })
    const names = new Set<string>()
    for (const g of groups) {
      const t = await readColumns(this.src, NAS, ['scientificName', column, 'status'], g.start, g.start + g.rows)
      for (let i = 0; i < t.status.length; i++) {
        if (t[column][i] === key && t.status[i] === 'established' && t.scientificName[i]) names.add(String(t.scientificName[i]))
      }
    }
    return [...names].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
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

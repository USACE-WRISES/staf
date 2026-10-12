// Watershed values from the bundle's per-catchment tables (site_engine _hrslim/values.py and lean.py).
//
// For every HR catchment the bundle holds its NLCD 2021 class cells and impervious sums (and 2001's),
// the pieces of its 100 m riparian strip keyed by the walk level that first reaches them, its road
// length, its road-stream crossings, its NID dams and its soil K cells, in the lean encoding (shares
// per mille, centimetres, ...). A watershed's values are sums over the catchments of its walk. The sums
// run in numpy's order (a 2-D sum over rows adds row after row; a 1-D sum is numpy's pairwise sum), so
// the rounded values are the Python's.
import { pyFixed } from '../app/pyfmt'
import { metadata, readColumns } from '../data/parquet'
import type { DataSource } from '../data/source'
import { KeyBlocks, RowBlocks } from './blocks'
import { encodingOf, type RegionMeta } from './bundle'
import type { Dataset } from './dataset'

export const NLCD_CODES = [11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95]
const LEAN_LC = [...NLCD_CODES.map((c) => `p${c}`), 'p_none']
const NO_IMP = 65535
const NO_DM = 65535
/** The site engine's land-cover stems (landcover._CLASS_KEYWORDS) as NLCD codes. */
export const ENGINE_STEMS: [string, number[]][] = [['crop', [82]], ['hayPasture', [81]], ['forest', [41, 42, 43]],
  ['shrub', [52]], ['grassland', [71]], ['woodyWetland', [90]], ['herbWetland', [95]]]

const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

/** numpy's pairwise sum of a 1-D float64 array (DOUBLE_pairwise_sum, blocks of 128, 8 accumulators). */
export function pairwiseSum(a: ArrayLike<number>, start = 0, n = a.length - start): number {
  if (n < 8) {
    let res = 0.0
    for (let i = 0; i < n; i++) res += a[start + i]
    return res
  }
  if (n <= 128) {
    const r = [a[start], a[start + 1], a[start + 2], a[start + 3], a[start + 4], a[start + 5], a[start + 6], a[start + 7]]
    let i = 8
    for (; i < n - (n % 8); i += 8) for (let j = 0; j < 8; j++) r[j] += a[start + i + j]
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]))
    for (; i < n; i++) res += a[start + i]
    return res
  }
  let n2 = Math.floor(n / 2)
  n2 -= n2 % 8
  return pairwiseSum(a, start, n2) + pairwiseSum(a, start + n2, n - n2)
}

/** Rows of a (rows x width) table picked and summed over rows in order (numpy ``m[rows].sum(axis=0)``). */
function rowSum(m: Float64Array, width: number, rows: ArrayLike<number>): Float64Array {
  const out = new Float64Array(width)
  for (let k = 0; k < rows.length; k++) {
    const base = rows[k] * width
    if (k === 0) for (let j = 0; j < width; j++) out[j] = m[base + j]
    else for (let j = 0; j < width; j++) out[j] = out[j] + m[base + j]
  }
  return out
}

/** A 1-D column picked and summed (numpy ``a[rows].sum()``). */
function pickSum(a: ArrayLike<number>, rows: ArrayLike<number>): number {
  const picked = new Float64Array(rows.length)
  for (let k = 0; k < rows.length; k++) picked[k] = a[rows[k]]
  return pairwiseSum(picked)
}

/** lean.decode_lc: the shares scaled back to cells (17 per row) and impervious sums (4 per row), for
 * table rows `rows` in order (each row decodes on its own). */
function decodeLc(t: Record<string, any>, rows: ArrayLike<number>): { counts: Float64Array, imp: Float64Array } {
  const m = rows.length
  const counts = new Float64Array(m * 17)
  const imp = new Float64Array(m * 4)
  const p = new Float64Array(17)
  for (let k = 0; k < m; k++) {
    const r = rows[k]
    for (let j = 0; j < 17; j++) p[j] = t[LEAN_LC[j]][r]
    const s = pairwiseSum(p)
    const cells = t.cells[r]
    const scale = s > 0 ? cells / s : 0
    for (let j = 0; j < 17; j++) counts[k * 17 + j] = p[j] * scale
    const valid = pairwiseSum(counts, k * 17, 16)
    for (const [j, col] of [[0, 'imp21'], [2, 'imp01']] as const) {
      const v = t[col][r]
      const has = v !== NO_IMP
      imp[k * 4 + j] = has ? v / 10 * valid : 0.0
      imp[k * 4 + j + 1] = has ? valid : 0.0
    }
  }
  return { counts, imp }
}

const iota = (n: number): Int32Array => Int32Array.from({ length: n }, (_v, i) => i)
const LC_COLUMNS = ['cells', ...LEAN_LC, 'imp21', 'imp01']

/** One region's value tables (values.ValueTables, lean files), each read by blocks of rows as walks need
 * them (`RowBlocks`, `KeyBlocks`): what a watershed takes of a table comes back for its rows, in order. */
export class ValueTables {
  private tables = new Map<string, RowBlocks | KeyBlocks>()
  private cache = new Map<string, Promise<any>>()

  constructor(readonly src: DataSource) {}

  private logical(prefix: string, vpu: string): string {
    return `bundle/values/${prefix}2_${vpu}.parquet`
  }

  /** Whether the region has the table. */
  async present(prefix: string, vpu: string): Promise<boolean> {
    await this.src.ready()
    return this.src.has(this.logical(prefix, vpu))
  }

  private byRow(prefix: string, vpu: string, columns: string[], nan: string[] = []): RowBlocks {
    const key = `${prefix}${vpu}`
    let t = this.tables.get(key) as RowBlocks | undefined
    if (!t) {
      t = new RowBlocks(this.src, this.logical(prefix, vpu), columns, nan)
      this.tables.set(key, t)
    }
    return t
  }

  private byKey(prefix: string, vpu: string, keyColumn: string, columns: string[]): KeyBlocks {
    const key = `${prefix}${vpu}`
    let t = this.tables.get(key) as KeyBlocks | undefined
    if (!t) {
      t = new KeyBlocks(this.src, this.logical(prefix, vpu), keyColumn, columns)
      this.tables.set(key, t)
    }
    return t
  }

  private once<T>(key: string, load: () => Promise<T>): Promise<T> {
    let got = this.cache.get(key) as Promise<T> | undefined
    if (!got) {
      got = load()
      got.catch(() => this.cache.delete(key))
      this.cache.set(key, got)
    }
    return got
  }

  /** The land cover of catchment rows `rows`: 17 class cells and 4 impervious sums per row, in order. */
  async lc(vpu: string, rows: ArrayLike<number>): Promise<{ counts: Float64Array, imp: Float64Array }> {
    return decodeLc(await this.byRow('lc', vpu, LC_COLUMNS).rows(rows), rows)
  }

  /** The riparian strip pieces of catchment rows `rows` (with others of their blocks), in the table's order:
   * each piece's catchment row, walk level, class cells and impervious sums. */
  async rip(vpu: string, rows: ArrayLike<number>): Promise<{ row: Float64Array, k: Float64Array, counts: Float64Array, imp: Float64Array }> {
    const t = await this.byKey('rip', vpu, 'row', ['k', ...LC_COLUMNS]).rows(rows)
    return { row: t.row, k: t.k, ...decodeLc(t, iota(t.row.length)) }
  }

  /** The road metres of catchment rows `rows`, in order. */
  async roads(vpu: string, rows: ArrayLike<number>): Promise<Float64Array> {
    const t = await this.byRow('roads', vpu, ['road_cm']).rows(rows)
    return Float64Array.from(rows, (r) => t.road_cm[r] / 100.0)
  }

  /** The dams by catchment row (the table lists the few rows with dams: read whole). */
  dams(vpu: string): Promise<{ dams: Float64Array, normal: Float64Array, missing: Float64Array, nid: Float64Array }> {
    return this.once(`dams${vpu}`, async () => {
      const logical = this.logical('dams', vpu)
      const meta = await metadata(this.src, logical)
      const n = Number(meta.key_value_metadata?.find((e) => e.key === 'rows')?.value ?? 0)
      const cols = ['row', 'dams', 'normal_acft', 'normal_missing', 'nid_acft']
      const t = await readColumns(this.src, logical, cols, 0, undefined, Object.fromEntries(cols.map((c) => [c, 'f64'])))
      const out = { dams: new Float64Array(n), normal: new Float64Array(n), missing: new Float64Array(n), nid: new Float64Array(n) }
      for (let i = 0; i < t.row.length; i++) {
        const r = t.row[i]
        out.dams[r] = t.dams[i]
        out.normal[r] = t.normal_acft[i]
        out.missing[r] = t.normal_missing[i]
        out.nid[r] = t.nid_acft[i]
      }
      return out
    })
  }

  /** The soils of catchment rows `rows`, in order: K-factor sums (mean x cells), K cells, SSURGO cells. */
  async soils(vpu: string, rows: ArrayLike<number>): Promise<{ kSum: Float64Array, kCells: Float64Array, ssurgo: Float64Array }> {
    const t = await this.byRow('soils', vpu, ['k_mean', 'k_cells', 'ssurgo_cells'], ['k_mean']).rows(rows)
    const m = rows.length
    const out = { kSum: new Float64Array(m), kCells: new Float64Array(m), ssurgo: new Float64Array(m) }
    for (let k = 0; k < m; k++) {
      const r = rows[k]
      // np.nan_to_num(float32 mean) * cells, the mean exact as float32
      const mean = Number.isNaN(t.k_mean[r]) ? 0 : Math.fround(t.k_mean[r])
      out.kSum[k] = mean * t.k_cells[r]
      out.kCells[k] = t.k_cells[r]
      out.ssurgo[k] = t.ssurgo_cells[r]
    }
    return out
  }

  /** The flowlines' 150 m NWI strips (extras2_, by line row): wetland m2 per NWI system and the strip's
   * m2 at full length with line rows `rows` read, or null where the region's extras have no strips. */
  async nwiStrips(vpu: string, rows: ArrayLike<number>): Promise<Record<string, Float64Array> | null> {
    const logical = this.logical('extras', vpu)
    const names = (await metadata(this.src, logical)).schema.map((s) => s.name)
    if (!names.includes('strip_m2')) return null
    return this.byRow('extras', vpu, ['nwi_r_m2', 'nwi_p_m2', 'nwi_l_m2', 'nwi_e_m2', 'nwi_m_m2', 'strip_m2']).rows(rows)
  }

  /** The road-stream crossings of line rows `rows` (with others of their blocks): each one's line row,
   * road and place. */
  async xings(vpu: string, rows: ArrayLike<number>): Promise<{ lineRow: Float64Array, road: Float64Array, x: Float64Array, y: Float64Array }> {
    const t = await this.byKey('xings', vpu, 'line_row', ['road', 'x_dm', 'y_dm']).rows(rows)
    return { lineRow: t.line_row, road: t.road, x: t.x_dm, y: t.y_dm }
  }
}

/** values.summarize: the engine's land-cover values from summed class cells and impervious sums. */
export function summarize(counts: Float64Array, imp: Float64Array, label: string): Record<string, number | boolean | null> {
  const total = pairwiseSum(counts)
  const covered = total - counts[16]
  if (covered <= 0) return { [`landcover${label}Unavailable`]: true }
  const out: Record<string, number | boolean | null> = {}
  for (const [stem, codes] of ENGINE_STEMS) {
    const picked = codes.map((c) => counts[NLCD_CODES.indexOf(c)])
    out[`${stem}Pct${label}`] = pyRound(100.0 * pairwiseSum(picked) / covered, 2)
  }
  out[`imperviousPct${label}`] = imp[1] ? pyRound(imp[0] / imp[1], 2) : null
  out[`imperviousPct2001${label}`] = imp[3] ? pyRound(imp[2] / imp[3], 2) : null
  out[`cells${label}`] = pyRound(total, 0)
  if (counts[16] > 0) out[`coveredFraction${label}`] = pyRound(covered / total, 4)
  return out
}

/** The catchment row of each line row, -1 where a line has none (values._catchment_rows). */
async function catchmentRows(ds: Dataset, region: RegionMeta, lineRows: number[]): Promise<number[]> {
  const t = await ds.topo(region)
  const enc = await encodingOf(ds.src, ds.folder, region)
  return lineRows.map((r) => enc.byId.get(t.ids[r]) ?? -1)
}

export type WatershedValues = Record<string, any>

/** values.watershed_values: land cover (watershed and riparian strip), roads, crossings, dams and soils
 * of the walked watershed, with the engine's names and rounding; ``status`` failed when a region the
 * tree touches has no tables. */
export async function watershedValues(ds: Dataset, tables: ValueTables, nhdplusid: number, maxReaches = 3000,
  maxHops = 190): Promise<WatershedValues> {
  const { tree, members, levels } = await ds.walk(nhdplusid, maxReaches, maxHops)
  const { ids: _ids, ...out } = tree as WatershedValues
  if (tree.status !== 'ok') return out
  // watershed_landcover
  let wsCounts = new Float64Array(17)
  let wsImp = new Float64Array(4)
  let rpCounts = new Float64Array(17)
  let rpImp = new Float64Array(4)
  const missing: string[] = []
  const regionRows = new Map<string, { region: RegionMeta, catRows: number[] }>()
  // every table the sums take, read at once: each read is a few requests (footer, page index, pages),
  // and one table after another they added up. The sums below take them from memory, in their order.
  await Promise.all([...members].map(async ([vpu, lineRows]) => {
    const region = await ds.region(vpu)
    if (!region || !(await tables.present('lc', vpu))) return
    const rows = (await catchmentRows(ds, region, lineRows)).filter((c) => c >= 0)
    await Promise.all([tables.lc(vpu, rows), tables.rip(vpu, rows), tables.roads(vpu, rows), tables.dams(vpu),
      tables.soils(vpu, rows), tables.xings(vpu, lineRows)])
  })).catch(() => {})   // a failed read is made again below, where its error counts
  for (const [vpu, lineRows] of members) {
    const region = (await ds.region(vpu))!
    if (!(await tables.present('lc', vpu))) {
      missing.push(vpu)
      continue
    }
    const allCat = await catchmentRows(ds, region, lineRows)
    const lv = levels.get(vpu)!
    const catRows: number[] = []
    const catLv: number[] = []
    allCat.forEach((c, i) => {
      if (c >= 0) {
        catRows.push(c)
        catLv.push(lv[i])
      }
    })
    regionRows.set(vpu, { region, catRows })
    // the rows picked, in order (numpy m[rows].sum(axis=0) over the picked rows)
    const { counts, imp } = await tables.lc(vpu, catRows)
    const add = (acc: Float64Array, v: Float64Array) => acc.map((x, j) => x + v[j])
    wsCounts = add(wsCounts, rowSum(counts, 17, iota(catRows.length)))
    wsImp = add(wsImp, rowSum(imp, 4, iota(catRows.length)))
    // the strip pieces of the walk's catchments, in the table's order (levels are never negative, so a
    // piece of a catchment outside the walk is never taken)
    const rip = await tables.rip(vpu, catRows)
    const levelOf = new Map<number, number>()
    catRows.forEach((c, i) => { levelOf.set(c, catLv[i]) })
    const take: number[] = []
    for (let i = 0; i < rip.row.length; i++) if ((levelOf.get(rip.row[i]) ?? -1) >= rip.k[i]) take.push(i)
    rpCounts = add(rpCounts, rowSum(rip.counts, 17, take))
    rpImp = add(rpImp, rowSum(rip.imp, 4, take))
  }
  if (missing.length) return { ...out, status: 'failed', reason: `no precomputed land cover for region(s) ${missing.join(', ')}` }
  Object.assign(out, summarize(wsCounts, wsImp, 'Watershed'), summarize(rpCounts, rpImp, 'Riparian'))
  // roads, crossings, dams, soils
  let areaM2 = 0.0
  let roadM = 0.0
  let damN = 0
  let normal = 0.0
  let missingNormal = 0
  let nidAcft = 0.0
  let crossings = 0
  let kSum = 0.0
  let kCells = 0
  let ssurgo = 0
  for (const [vpu, lineRows] of members) {
    const { region, catRows } = regionRows.get(vpu)!
    const enc = await encodingOf(ds.src, ds.folder, region)
    let cells = 0
    for (const c of catRows) cells += enc.cells[c]
    areaM2 += (cells * 2) / 2.0 * (region.grid.cell * region.grid.cell)
    roadM += pairwiseSum(await tables.roads(vpu, catRows))
    const d = await tables.dams(vpu)
    for (const c of catRows) damN += d.dams[c]
    normal += pickSum(d.normal, catRows)
    for (const c of catRows) missingNormal += d.missing[c]
    nidAcft += pickSum(d.nid, catRows)
    const so = await tables.soils(vpu, catRows)
    kSum += pairwiseSum(so.kSum)
    for (let k = 0; k < catRows.length; k++) {
      kCells += so.kCells[k]
      ssurgo += so.ssurgo[k]
    }
    const xg = await tables.xings(vpu, lineRows)
    const wanted = new Set(lineRows)
    const seen = new Set<string>()
    for (let i = 0; i < xg.lineRow.length; i++) if (wanted.has(xg.lineRow[i])) seen.add(`${xg.road[i]},${xg.x[i]},${xg.y[i]}`)
    crossings += seen.size
  }
  const area = areaM2 / 1e6
  const km = roadM / 1000.0
  Object.assign(out, {
    areaSqkm: pyRound(area, 4),
    roadLengthKm: pyRound(km, 3), roadDensity: pyRound(km / area, 4),
    roadCrossings: crossings, roadCrossingDensity: pyRound(crossings / area, 4),
    damCount: damN, damDensityPerSqkm: pyRound(damN / area, 4),
    damStorageAcreFt: pyRound(normal, 1), damStoragePerSqkm: pyRound(normal / area, 3),
    damNidStorageAcreFt: pyRound(nidAcft, 1), damNidStoragePerSqkm: pyRound(nidAcft / area, 3),
    damsWithoutNormalStorage: missingNormal,
    soilKFactor: kCells ? pyRound(kSum / kCells, 3) : null,
    soilKCoverage: ssurgo ? pyRound(kCells / ssurgo, 4) : null,
  })
  return out
}

export { NO_DM }

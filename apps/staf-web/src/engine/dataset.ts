// The bundle's networks as the site engine walks them (site_engine _hrslim/reader2.py Dataset2 and the
// bundle.py calls on it): each region's topology (rows, hydroseqs, the steps to the rows downstream and
// upstream, minor divergences on NHDPlus V2), the links between regions, the id index of V2 (whose
// COMIDs do not name their region), the breadth-first upstream walk, the watershed outline from the
// tree's catchments and its area from their cells, and flowline records by id or hydroseq.
//
// "bundle/" is NHDPlus HR, "bundle/v2/" NHDPlus V2; one Dataset per folder per session.
import { pyFixed } from '../app/pyfmt'
import { columnStats, metadata, readColumns, rowGroups } from '../data/parquet'
import type { DataSource } from '../data/source'
import type { Line } from '../streams/geom'
import { albersToLonLat } from '../xs/projExact'
import { datasetRegions, encodingOf, type FlowlineRecord, type RegionMeta, round7 } from './bundle'
import { areaCells, assemble, outline, type Polys, type Pt } from './outline'

export interface Topo {
  ids: Float64Array
  hs: Float64Array
  dnStep: Int32Array
  upStep: Int32Array
  dnOut: Float64Array
  upOut: Float64Array
  da: Float64Array                // NaN where unknown
  byId: Map<number, number[]>     // value -> rows, ascending
  byHs: Map<number, number[]>
  parentPtr: Int32Array           // (n + 1) offsets into parents
  parents: Int32Array             // the rows draining to each row: main paths, then minor divergences
}

export type Key = 'by_id' | 'by_hs'

export interface Walk {
  status: 'ok' | 'refused' | 'failed'
  nReaches: number
  nHops: number
  ids?: number[]
  reason?: string
}

export interface Watershed extends Walk {
  nCatchments?: number
  areaSqkm?: number
  /** MultiPolygon coordinates in degrees (np.round 7) */
  geometry?: { type: 'MultiPolygon', coordinates: number[][][][] }
}

const LINE_COLUMNS = ['nhdplusid', 'gnis_name', 'reachcode', 'lengthkm', 'totdasqkm', 'slope', 'fcode', 'streamorde',
  'hydroseq', 'qama', 'x', 'y', 'parts']

const num = (v: unknown): number => (v === null || v === undefined ? 0 : Number(v))
const pyRound = (x: number, n: number): number => Number(pyFixed(x, n))

/** A list of polygons' rings in cells moved to metres on the region's grid (Grid.cells_to_xy). */
function toMetres(polys: Polys, grid: RegionMeta['grid']): Polys {
  return polys.map((rings) => rings.map((ring) => ring.map(([x, y]) => [x * grid.cell + grid.offset, y * grid.cell + grid.offset] as Pt)))
}

/** Polygons on one grid joined where they share borders: the pieces' boundary edges, split to single
 * cells, with each edge met by its reverse dropped, chained into rings as the outline chains arcs.
 * The same area and outline shapely.union_all gives the pieces (the vertex order can differ). */
export function unionCells(pieces: Polys[]): Polys {
  const edges = new Map<string, number>()
  const key = (x: number, y: number, dx: number, dy: number) => `${x},${y},${dx},${dy}`
  for (const polys of pieces) {
    for (const rings of polys) {
      for (const ring of rings) {
        for (let i = 0; i < ring.length - 1; i++) {
          let [x, y] = ring[i]
          const [x1, y1] = ring[i + 1]
          const dx = Math.sign(x1 - x)
          const dy = Math.sign(y1 - y)
          while (x !== x1 || y !== y1) {
            const back = key(x + dx, y + dy, -dx, -dy)
            if (edges.has(back)) edges.delete(back)
            else edges.set(key(x, y, dx, dy), 1)
            x += dx
            y += dy
          }
        }
      }
    }
  }
  // chain the unit edges (interior on the left) into rings, the left turn at a node two leave
  const out = new Map<string, [number, number][]>()
  for (const k of edges.keys()) {
    const [x, y, dx, dy] = k.split(',').map(Number)
    const node = `${x},${y}`
    const list = out.get(node)
    if (list) list.push([dx, dy])
    else out.set(node, [[dx, dy]])
  }
  const rings: Pt[][] = []
  const used = new Set<string>()
  const sorted = [...edges.keys()].sort()
  for (const start of sorted) {
    if (used.has(start)) continue
    const ring: Pt[] = []
    let [x, y, dx, dy] = start.split(',').map(Number)
    let k = start
    while (!used.has(k)) {
      used.add(k)
      ring.push([x, y])
      x += dx
      y += dy
      const options = (out.get(`${x},${y}`) ?? []).filter(([ex, ey]) => !used.has(key(x, y, ex, ey)))
      if (!options.length) break
      let next = options[0]
      if (options.length > 1) next = options.find(([ex, ey]) => dx * ey - dy * ex > 0) ?? options[0]
      ;[dx, dy] = next
      k = key(x, y, dx, dy)
    }
    rings.push(ring)
  }
  return assemble(rings)
}

export class Dataset {
  private topos = new Map<string, Promise<Topo>>()
  private linksP: Promise<{ targets: Float64Array, vpus: string[], rows: Int32Array }> | null = null
  private indexGroups = new Map<number, Promise<{ key: Int32Array, value: Float64Array, region: Int32Array }>>()

  constructor(readonly src: DataSource, readonly folder: string) {}

  regions(): Promise<RegionMeta[]> {
    return datasetRegions(this.src, this.folder)
  }

  async region(vpu: string): Promise<RegionMeta | undefined> {
    return (await this.regions()).find((r) => r.vpu === vpu)
  }

  /** The region's network arrays with id, hydroseq and parent indexes (_Region.topo), read once. */
  topo(region: RegionMeta): Promise<Topo> {
    let got = this.topos.get(region.vpu)
    if (!got) {
      got = (async () => {
        // the browser layout's topology file (one request), else the lines file's same columns
        await this.src.ready()
        const own = `topo/${this.folder === 'bundle/v2/' ? 'v2' : 'hr'}_${region.vpu}.parquet`
        const logical = this.src.has(own) ? own : this.folder + region.lines
        const meta = await metadata(this.src, logical)
        const minor = meta.schema.some((s) => s.name === 'dnminor_step')
        const cols = ['nhdplusid', 'hydroseq', 'dn_step', 'up_step', 'dn_out', 'up_out', 'totdasqkm', ...(minor ? ['dnminor_step'] : [])]
        const t = await readColumns(this.src, logical, cols, 0, undefined,
          { nhdplusid: 'f64', hydroseq: 'f64', dn_step: 'i32', up_step: 'i32', dn_out: 'f64', up_out: 'f64', totdasqkm: 'f64', dnminor_step: 'i32' },
          ['totdasqkm'])
        const n = t.nhdplusid.length
        const ids: Float64Array = t.nhdplusid
        const hs: Float64Array = t.hydroseq
        const dnStep: Int32Array = t.dn_step
        const upStep: Int32Array = t.up_step
        const da: Float64Array = t.totdasqkm
        const byId = new Map<number, number[]>()
        const byHs = new Map<number, number[]>()
        for (let r = 0; r < n; r++) {
          const a = byId.get(ids[r])
          if (a) a.push(r)
          else byId.set(ids[r], [r])
          const b = byHs.get(hs[r])
          if (b) b.push(r)
          else byHs.set(hs[r], [r])
        }
        // the parents of each row: rows stepping down to it, main paths first, then minor divergences
        const child: number[] = []
        const target: number[] = []
        for (let r = 0; r < n; r++) {
          if (dnStep[r] !== 0) {
            child.push(r)
            target.push(r + dnStep[r])
          }
        }
        if (minor) {
          for (let r = 0; r < n; r++) {
            const m = t.dnminor_step[r]
            if (m !== 0) {
              child.push(r)
              target.push(r + m)
            }
          }
        }
        const parentPtr = new Int32Array(n + 1)
        for (const tg of target) parentPtr[tg + 1] += 1
        for (let r = 0; r < n; r++) parentPtr[r + 1] += parentPtr[r]
        const fill = parentPtr.slice(0, n)
        const parents = new Int32Array(child.length)
        for (let k = 0; k < child.length; k++) parents[fill[target[k]]++] = child[k]
        return {
          ids, hs, dnStep, upStep, dnOut: t.dn_out, upOut: t.up_out,
          da, byId, byHs, parentPtr, parents,
        }
      })()
      got.catch(() => this.topos.delete(region.vpu))
      this.topos.set(region.vpu, got)
    }
    return got
  }

  /** Cross-region links sorted by target hydroseq (Dataset2._links_index). */
  links(): Promise<{ targets: Float64Array, vpus: string[], rows: Int32Array }> {
    this.linksP ??= (async () => {
      const logical = this.folder + 'links2.parquet'
      await this.src.ready()
      if (!this.src.has(logical)) return { targets: new Float64Array(0), vpus: [], rows: new Int32Array(0) }
      const t = await readColumns(this.src, logical, ['target', 'vpu', 'row'], 0, undefined, { target: 'f64', row: 'i32' })
      const target = t.target as Float64Array
      const order = Array.from(target, (_, i) => i).sort((a, b) => target[a] - target[b] || a - b)
      return {
        targets: Float64Array.from(order, (i) => target[i]),
        vpus: order.map((i) => String(t.vpu[i])),
        rows: Int32Array.from(order, (i) => (t.row as Int32Array)[i]),
      }
    })()
    this.linksP.catch(() => { this.linksP = null })
    return this.linksP
  }

  private indexGroup(logical: string, g: { index: number, start: number, rows: number }) {
    let got = this.indexGroups.get(g.index)
    if (!got) {
      got = readColumns(this.src, logical, ['key', 'value', 'region'], g.start, g.start + g.rows, { key: 'i32', value: 'f64', region: 'i32' })
        .then((t) => ({ key: t.key as Int32Array, value: t.value as Float64Array, region: t.region as Int32Array }))
      got.catch(() => this.indexGroups.delete(g.index))
      this.indexGroups.set(g.index, got)
    }
    return got
  }

  /** The regions that may hold the values (Dataset2._regions_holding): the id index's when the dataset
   * has one (V2), else every region whose range covers one (HR), in region order. */
  async regionsHolding(key: Key, values: number[]): Promise<RegionMeta[]> {
    const regions = await this.regions()
    const logical = this.folder + 'idindex.parquet'
    await this.src.ready()
    if (!this.src.has(logical)) {
      const rangeKey = key === 'by_id' ? 'idRange' : 'hydroseqRange'
      return regions.filter((r) => {
        const rng = r[rangeKey]
        return !rng || values.some((v) => rng[0] <= v && v <= rng[1])
      })
    }
    const meta = await metadata(this.src, logical)
    const kv = meta.key_value_metadata?.find((e) => e.key === 'regions')?.value
    const names: string[] = kv ? JSON.parse(kv) : []
    const k = key === 'by_id' ? 0 : 1
    const kStats = columnStats(meta, 'key')
    const vStats = columnStats(meta, 'value')
    const held = new Set<string>()
    for (const v of new Set(values)) {
      for (const g of rowGroups(meta)) {
        const ks = kStats[g.index]
        const vs = vStats[g.index]
        if (ks && (k < Number(ks.min) || k > Number(ks.max))) continue
        if (vs && (v < Number(vs.min) || v > Number(vs.max))) continue
        const t = await this.indexGroup(logical, g)
        // the first row at or after (k, v), the table being sorted by key, then value
        let lo = 0
        let hi = t.key.length
        while (lo < hi) {
          const mid = (lo + hi) >> 1
          if (t.key[mid] < k || (t.key[mid] === k && t.value[mid] < v)) lo = mid + 1
          else hi = mid
        }
        for (let i = lo; i < t.key.length && t.key[i] === k && t.value[i] === v; i++) held.add(names[t.region[i]])
      }
    }
    return regions.filter((r) => held.has(r.vpu))
  }

  /** The regions holding the values and the rows there (Dataset2._find). */
  async find(key: Key, values: number[]): Promise<{ region: RegionMeta, rows: number[] }[]> {
    const wanted = [...new Set(values)].sort((a, b) => a - b)
    const out: { region: RegionMeta, rows: number[] }[] = []
    for (const region of await this.regionsHolding(key, values)) {
      const t = await this.topo(region)
      const index = key === 'by_id' ? t.byId : t.byHs
      const rows = wanted.flatMap((v) => index.get(v) ?? [])
      if (rows.length) out.push({ region, rows })
    }
    return out
  }

  /** The breadth-first upstream walk, the budget checked before every level (Dataset2._walk); members
   * maps each region to the rows it holds, in the order they joined, and levels to each row's hops
   * above the outlet. */
  async walk(nhdplusid: number, maxReaches: number, maxHops: number, onLevel?: (hops: number, reaches: number) => void):
    Promise<{ tree: Walk, members: Map<string, number[]>, levels: Map<string, number[]> }> {
    const found = await this.find('by_id', [nhdplusid])
    if (!found.length) {
      return { tree: { status: 'failed', reason: 'reach not found', nReaches: 0, nHops: 0 }, members: new Map(), levels: new Map() }
    }
    const { region, rows } = found[0]
    const links = await this.links()
    const tree = new Set<number>([nhdplusid])
    const members = new Map<string, number[]>([[region.vpu, [rows[0]]]])
    const levels = new Map<string, number[]>([[region.vpu, [0]]])
    let frontier: [RegionMeta, number][] = [[region, rows[0]]]
    let hops = 0
    while (frontier.length) {
      if (hops >= maxHops || tree.size >= maxReaches) {
        return {
          tree: {
            status: 'refused', nReaches: tree.size, nHops: hops,
            reason: `watershed exceeds the engine budget (${tree.size} reaches, ${hops} hops; the budget is ${maxReaches} reaches and ${maxHops} hops)`,
          },
          members,
          levels,
        }
      }
      const next: [RegionMeta, number][] = []
      for (const [reg, row] of frontier) {
        const t = await this.topo(reg)
        const h = t.hs[row]
        if (!h) continue
        const cands: [RegionMeta, number][] = []
        for (let k = t.parentPtr[row]; k < t.parentPtr[row + 1]; k++) cands.push([reg, t.parents[k]])
        let lo = 0
        let hi = links.targets.length
        while (lo < hi) {
          const mid = (lo + hi) >> 1
          if (links.targets[mid] < h) lo = mid + 1
          else hi = mid
        }
        for (let k = lo; k < links.targets.length && links.targets[k] === h; k++) {
          const other = await this.region(links.vpus[k])
          if (other) cands.push([other, links.rows[k]])
        }
        for (const [preg, prow] of cands) {
          const pt = await this.topo(preg)
          const pid = pt.ids[prow]
          if (tree.has(pid)) continue
          tree.add(pid)
          const list = members.get(preg.vpu)
          if (list) list.push(prow)
          else members.set(preg.vpu, [prow])
          const lv = levels.get(preg.vpu)
          if (lv) lv.push(hops + 1)
          else levels.set(preg.vpu, [hops + 1])
          if (pt.hs[prow]) next.push([preg, prow])
        }
      }
      frontier = next
      hops += 1
      onLevel?.(hops, tree.size)
    }
    return { tree: { status: 'ok', nReaches: tree.size, nHops: hops, ids: [...tree].sort((a, b) => a - b) }, members, levels }
  }

  /** The walk, the outline of the tree's catchments assembled from their shared borders and its area
   * from their cells (Dataset2.watershed with_ids): EPSG:5070 metres, then degrees to 1e-7. */
  async watershed(nhdplusid: number, maxReaches: number, maxHops: number,
    onLevel?: (hops: number, reaches: number) => void): Promise<Watershed> {
    const { tree, members } = await this.walk(nhdplusid, maxReaches, maxHops, onLevel)
    const out: Watershed = { ...tree }
    if (tree.status !== 'ok') return out
    let areaM2 = 0
    let nCats = 0
    const pieces: { region: RegionMeta, polys: Polys }[] = []
    for (const [vpu, lineRows] of members) {
      const region = (await this.region(vpu))!
      const t = await this.topo(region)
      const want = new Set(lineRows.map((r) => t.ids[r]))
      const enc = await encodingOf(this.src, this.folder, region)
      const member = new Uint8Array(enc.ids.length)
      let hits = 0
      for (let r = 0; r < enc.ids.length; r++) {
        if (want.has(enc.ids[r])) {
          member[r] = 1
          hits += 1
        }
      }
      if (!hits) continue
      nCats += hits
      areaM2 += areaCells(enc, member) * (region.grid.cell * region.grid.cell)
      pieces.push({ region, polys: outline(enc, member) })
    }
    if (!pieces.length) return { ...out, status: 'failed', reason: 'no catchments for the upstream tree' }
    const grid = pieces[0].region.grid
    const cells = pieces.length === 1 ? pieces[0].polys : unionCells(pieces.map((p) => p.polys))
    const coordinates = toMetres(cells, grid).map((rings) => rings.map((ring) => ring.map(([x, y]) => {
      const [lon, lat] = albersToLonLat(x, y)
      return [round7(lon), round7(lat)]
    })))
    return { ...out, nCatchments: nCats, areaSqkm: pyRound(areaM2 / 1e6, 4), geometry: { type: 'MultiPolygon', coordinates } }
  }

  /** Starts reading what a watershed at this reach needs (its region's network and catchments), so
   * Delineate finds them read, and gives the regions' codes. Nothing is computed; a failed read is
   * tried again when needed. */
  async warm(nhdplusid: number): Promise<string[]> {
    try {
      const regions = await this.regionsHolding('by_id', [nhdplusid])
      for (const region of regions) {
        void this.topo(region).catch(() => {})
        void encodingOf(this.src, this.folder, region).catch(() => {})
      }
      return regions.map((r) => r.vpu)
    } catch {
      return []   // the reads happen again on Delineate
    }
  }

  /** Line rows of one region with their attributes (Dataset2._line_rows, _as_v1, bundle._records). */
  async records(region: RegionMeta, rows: number[]): Promise<FlowlineRecord[]> {
    const logical = this.folder + region.lines
    const t = await this.topo(region)
    const sorted = [...new Set(rows)].sort((a, b) => a - b)
    const out: FlowlineRecord[] = []
    for (const row of sorted) {
      const r = await readColumns(this.src, logical, LINE_COLUMNS, row, row + 1, {}, [], true)
      const parts: Line[] = []
      const xs = r.x[0] as ArrayLike<number>
      const ys = r.y[0] as ArrayLike<number>
      let k = 0
      for (const count of Array.from(r.parts[0] as ArrayLike<number>, Number)) {
        const part: Line = []
        for (let j = 0; j < count; j++, k++) part.push([Number(xs[k]) / 1e5, Number(ys[k]) / 1e5])
        parts.push(part)
      }
      if (!parts.length || !parts[0].length) continue
      const dnStep = t.dnStep[row]
      const upStep = t.upStep[row]
      const dn = dnStep !== 0 ? t.hs[row + dnStep] : t.dnOut[row]
      const up = upStep !== 0 ? t.hs[row + upStep] : t.upOut[row]
      const name = r.gnis_name[0] == null ? null : String(r.gnis_name[0]).trim() || null
      const rc = r.reachcode[0]
      const da = r.totdasqkm[0] == null ? null : Number(r.totdasqkm[0])
      const slope = r.slope[0] == null ? null : Number(r.slope[0])
      const qama = r.qama[0] == null ? null : Number(r.qama[0])
      const fcode = r.fcode[0] == null ? null : Number(r.fcode[0])
      const id = (v: number) => (v > 0 ? Math.round(v) : null)
      out.push({
        nhdplusid: Number(r.nhdplusid[0]), gnis_name: name,
        reachcode: rc === null || rc === undefined ? null : typeof rc === 'string' ? rc : String(rc).padStart(14, '0'),
        lengthkm: r.lengthkm[0] == null ? null : Number(r.lengthkm[0]),
        totdasqkm: da !== null && da > 0 ? da : null, slope: slope !== null && slope >= 0 ? slope : null,
        fcode, ftype: fcode === null ? null : Math.trunc(fcode / 100), stream_order: r.streamorde[0] == null ? null : Number(r.streamorde[0]),
        hydroseq: id(t.hs[row]), uphydroseq: id(up), dnhydroseq: id(dn), vpuid: region.vpuid ?? region.vpu,
        qama: qama !== null && qama >= 0 ? qama : null,
        geometry: parts.length === 1 ? { type: 'LineString', coordinates: parts[0] } : { type: 'MultiLineString', coordinates: parts },
      })
    }
    return out
  }

  /** The records under an id or hydroseq (Dataset2.reach), region by region. */
  async reach(key: Key, value: number): Promise<FlowlineRecord[]> {
    const out: FlowlineRecord[] = []
    for (const { region, rows } of await this.find(key, [value])) out.push(...await this.records(region, rows))
    return out
  }

  /** One flowline by id or hydroseq, or null unless exactly one answers (bundle.flowline). */
  async flowline(key: Key, value: number): Promise<FlowlineRecord | null> {
    const recs = await this.reach(key, value)
    return recs.length === 1 ? recs[0] : null
  }
}

// A region's table read a block of rows at a time, as a watershed or a reach needs them. The bundle
// writes each region's value tables as one row group, so a read of a few catchments' values took the
// tables' whole columns (1.4 MB of land cover in 0108 for one headwater catchment). The browser layout's
// copies have a page index (tools/staf-web-data pages.py), and a watershed's catchments sit close
// together in a region's rows (Utoy Creek's 89 in 3 blocks of 105,471 rows), so a block of BLOCK rows
// costs a few KB. A file without a page index is read whole, once, as before.
import { metadata, readColumns, readWhere } from '../data/parquet'
import type { DataSource } from '../data/source'

/** Rows per block (the layout's value tables have pages of this many rows). */
export const BLOCK = 256

type Columns = Record<string, Float64Array>

/** Whether every column chunk of a file has an offset index (the browser layout's copies). */
export async function hasPageIndex(src: DataSource, logical: string): Promise<boolean> {
  const meta = await metadata(src, logical)
  return meta.row_groups.length > 0 &&
    meta.row_groups.every((g) => g.columns.every((c) => c.offset_index_offset !== undefined && c.offset_index_offset !== null))
}

const sortedBlocks = (values: ArrayLike<number>): number[] =>
  [...new Set(Array.from(values, (v) => Math.floor(v / BLOCK)))].sort((a, b) => a - b)

/** Runs of consecutive blocks among `blocks` (sorted) that `have` lacks, as [first, last]. */
function missingRuns(blocks: number[], have: (b: number) => boolean): [number, number][] {
  const runs: [number, number][] = []
  for (const b of blocks) {
    if (have(b)) continue
    const last = runs[runs.length - 1]
    if (last && b === last[1] + 1) last[1] = b
    else runs.push([b, b])
  }
  return runs
}

/** A table aligned with a region's rows (its row i is the region's catchment or line row i), every
 * column as float64 (nulls as 0, or NaN for the columns in `nan`). */
export class RowBlocks {
  private cols: Columns | null = null
  private n = 0
  private whole = false
  private setup: Promise<void> | null = null
  private reads = new Map<number, Promise<void>>()

  constructor(private readonly src: DataSource, private readonly logical: string, private readonly columns: string[],
              private readonly nan: string[] = []) {}

  /** The table's columns at full length, every row of `rows` read (rows not asked for may be 0). */
  async rows(rows: ArrayLike<number>): Promise<Columns> {
    this.setup ??= (async () => {
      const meta = await metadata(this.src, this.logical)
      this.n = Number(meta.num_rows)
      this.whole = !(await hasPageIndex(this.src, this.logical))
      this.cols = Object.fromEntries(this.columns.map((c) => [c, new Float64Array(this.n)]))
    })()
    this.setup.catch(() => { this.setup = null })
    await this.setup
    const blocks = this.whole ? [0] : sortedBlocks(rows)
    for (const [b0, b1] of missingRuns(blocks, (b) => this.reads.has(b))) {
      const start = this.whole ? 0 : b0 * BLOCK
      const end = this.whole ? this.n : Math.min((b1 + 1) * BLOCK, this.n)
      const read = this.read(start, end)
      for (let b = b0; b <= b1; b++) this.reads.set(b, read)
      read.catch(() => {
        for (let b = b0; b <= b1; b++) if (this.reads.get(b) === read) this.reads.delete(b)
      })
    }
    await Promise.all(blocks.map((b) => this.reads.get(b)))
    return this.cols!
  }

  get length(): number {
    return this.n
  }

  private async read(start: number, end: number): Promise<void> {
    if (end <= start) return
    const typed = Object.fromEntries(this.columns.map((c) => [c, 'f64' as const]))
    const t = await readColumns(this.src, this.logical, this.columns, start, end, typed, this.nan, true)
    for (const c of this.columns) this.cols![c].set(t[c] as Float64Array, start)
  }
}

/** A table sorted by a key column holding region rows (the riparian pieces by catchment row, the road
 * crossings by line row), every column as float64. */
export class KeyBlocks {
  private setup: Promise<boolean> | null = null
  private whole: Promise<Columns> | null = null
  private blocks = new Map<number, Promise<Columns>>()

  constructor(private readonly src: DataSource, private readonly logical: string, private readonly key: string,
              private readonly columns: string[]) {}

  /** The table's rows whose key is among `keys`, in the table's order, with others of their blocks (or
   * the whole table, for a file without a page index). */
  async rows(keys: ArrayLike<number>): Promise<Columns> {
    this.setup ??= hasPageIndex(this.src, this.logical)
    this.setup.catch(() => { this.setup = null })
    if (!(await this.setup)) {
      this.whole ??= this.read(null)
      this.whole.catch(() => { this.whole = null })
      return this.whole
    }
    const blocks = sortedBlocks(keys)
    for (const [b0, b1] of missingRuns(blocks, (b) => this.blocks.has(b))) {
      const read = this.read([b0 * BLOCK, (b1 + 1) * BLOCK - 1])
      for (let b = b0; b <= b1; b++) {
        const part = read.then((t) => slice(t, this.key, b))
        this.blocks.set(b, part)
        part.catch(() => { if (this.blocks.get(b) === part) this.blocks.delete(b) })
      }
    }
    return concat(await Promise.all(blocks.map((b) => this.blocks.get(b)!)), [this.key, ...this.columns])
  }

  private async read(range: [number, number] | null): Promise<Columns> {
    const cols = [this.key, ...this.columns]
    const typed = Object.fromEntries(cols.map((c) => [c, 'f64' as const]))
    if (!range) return readColumns(this.src, this.logical, cols, 0, undefined, typed) as Promise<Columns>
    const t = await readWhere(this.src, this.logical, cols, { [this.key]: { $gte: range[0], $lte: range[1] } }, typed)
    return Object.fromEntries(cols.map((c) => [c, Float64Array.from(t.cols[c] as number[])]))
  }
}

/** The rows of `t` (sorted by `key`) whose key falls in block `b`. */
function slice(t: Columns, key: string, b: number): Columns {
  const k = t[key]
  let i = 0
  while (i < k.length && k[i] < b * BLOCK) i++
  let j = i
  while (j < k.length && k[j] < (b + 1) * BLOCK) j++
  return Object.fromEntries(Object.entries(t).map(([c, v]) => [c, v.subarray(i, j)]))
}

function concat(parts: Columns[], cols: string[]): Columns {
  const out: Columns = {}
  for (const c of cols) {
    const n = parts.reduce((s, p) => s + p[c].length, 0)
    const arr = new Float64Array(n)
    let at = 0
    for (const p of parts) {
      arr.set(p[c], at)
      at += p[c].length
    }
    out[c] = arr
  }
  return out
}

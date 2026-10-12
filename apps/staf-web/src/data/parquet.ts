/**
 * Parquet by HTTP range (hyparquet): footers cached per file, row groups chosen from their
 * statistics, and only the requested columns of the chosen row groups read.
 */
import { parquetMetadataAsync, parquetRead, parquetReadObjects, type FileMetaData, type ParquetQueryFilter } from 'hyparquet'
export { rowIndex } from 'hyparquet'
import { compressors, zstdReady } from './zstd'
import type { DataSource } from './source'

const metas = new Map<string, Promise<FileMetaData>>()
/** The first read from a file's end where the data's index doesn't give its footer's size (a larger
 * footer costs one more request; hyparquet's default reads 512 KB). */
export const FOOTER_FETCH = 1 << 16

/** A file's footer: the index gives its size (from 0.5 KB to 0.9 MB), so one request reads exactly it. */
export function metadata(src: DataSource, logical: string): Promise<FileMetaData> {
  const key = src.base + logical
  let m = metas.get(key)
  if (!m) {
    m = src.ready().then(() => parquetMetadataAsync(src.file(logical), { initialFetchSize: src.entry(logical).footer ?? FOOTER_FETCH }))
    metas.set(key, m)
    m.catch(() => metas.delete(key))
  }
  return m
}

export interface RowGroupSpan {
  index: number
  start: number
  rows: number
}

export function rowGroups(meta: FileMetaData): RowGroupSpan[] {
  let start = 0
  return meta.row_groups.map((rg, index) => {
    const rows = Number(rg.num_rows)
    const span = { index, start, rows }
    start += rows
    return span
  })
}

export interface MinMax {
  min: number | bigint
  max: number | bigint
}

/** The min and max statistics of `column` per row group (undefined where none were written). */
export function columnStats(meta: FileMetaData, column: string): Array<MinMax | undefined> {
  return meta.row_groups.map((rg) => {
    const cc = rg.columns.find((c) => c.meta_data?.path_in_schema.join('.') === column)
    const st = cc?.meta_data?.statistics
    const min = st?.min_value ?? st?.min
    const max = st?.max_value ?? st?.max
    if (min === undefined || max === undefined || min === null || max === null) return undefined
    return { min: min as number | bigint, max: max as number | bigint }
  })
}

/** Rows [rowStart, rowEnd) as objects; with ``pages``, through the file's offset index where it has one
 * (the pages holding the rows, not the row group's whole columns). */
export async function readRows(src: DataSource, logical: string, columns: string[], rowStart: number,
                               rowEnd: number, pages = false): Promise<Record<string, any>[]> {
  const meta = await metadata(src, logical)
  await Promise.all([src.ready(), zstdReady()])
  return parquetReadObjects({ file: src.file(logical), metadata: meta, columns, rowStart, rowEnd, compressors, useOffsetIndex: pages })
}

/** The rows matching ``filter`` as objects, read as ``readWhere`` reads them; with ``rowIndex``, each
 * object's place in the file is ``obj[rowIndex]`` (hyparquet's symbol). */
export async function readObjectsWhere(src: DataSource, logical: string, columns: string[],
                                       filter: ParquetQueryFilter, includeRowIndex = false): Promise<Record<string | symbol, any>[]> {
  const meta = await metadata(src, logical)
  await Promise.all([src.ready(), zstdReady()])
  return parquetReadObjects({ file: src.file(logical), metadata: meta, columns, filter, usePageIndex: true, useOffsetIndex: true,
    includeRowIndex, compressors })
}

/** The rows matching ``filter`` (hyparquet's filter: ``{lat: {$gte: s, $lte: n}}``, ``{huc12: key}``), as
 * columns. Where the file has a page index (the browser layout's points/ tables), the row groups and
 * then the pages whose lowest and highest values cannot match are skipped: only the pages left are read. */
export async function readWhere(src: DataSource, logical: string, columns: string[], filter: ParquetQueryFilter,
                                typed: Record<string, TypedKind> = {}, nan: string[] = []): Promise<{ rows: number, cols: Record<string, any[]> }> {
  const meta = await metadata(src, logical)
  await Promise.all([src.ready(), zstdReady()])
  const objs = await parquetReadObjects({ file: src.file(logical), metadata: meta, columns, filter, usePageIndex: true,
    useOffsetIndex: true, compressors })
  const cols: Record<string, any[]> = {}
  for (const c of columns) {
    const kind = typed[c]
    const empty = nan.includes(c) ? NaN : 0
    cols[c] = objs.map((o) => {
      const v = o[c]
      if (!kind) return v
      return v === null || v === undefined ? empty : Number(v)
    })
  }
  return { rows: objs.length, cols }
}

type Typed = Float64Array | Int32Array | Uint8Array
type TypedKind = 'f64' | 'i32' | 'u8'

/** Columns of rows [rowStart, rowEnd) (the whole file by default) as one array per column, without
 * building a row object each: a region's network or catchments read at once. ``typed`` names the
 * columns to return as typed arrays (int64 as float64 numbers, nulls as 0, or NaN for f64 with
 * ``nan``); the others come as hyparquet decodes them (int64 as bigint, lists as arrays). With
 * ``pages``, a few rows of a large group are read through the file's offset index (where it has one):
 * the pages holding them, not the whole group. */
export async function readColumns(src: DataSource, logical: string, columns: string[], rowStart = 0,
                                  rowEnd?: number, typed: Record<string, TypedKind> = {},
                                  nan: string[] = [], pages = false): Promise<Record<string, any>> {
  const meta = await metadata(src, logical)
  await Promise.all([src.ready(), zstdReady()])
  const total = rowGroups(meta).reduce((s, g) => s + g.rows, 0)
  const end = Math.min(rowEnd ?? total, total)
  const size = Math.max(end - rowStart, 0)
  const out: Record<string, any> = {}
  for (const c of columns) {
    const kind = typed[c]
    out[c] = kind === 'f64' ? new Float64Array(size) : kind === 'i32' ? new Int32Array(size) : kind === 'u8' ? new Uint8Array(size) : new Array(size)
  }
  await parquetRead({
    file: src.file(logical), metadata: meta, columns, rowStart, rowEnd: end, compressors, useOffsetIndex: pages,
    onChunk: ({ columnName, columnData, rowStart: at }) => {
      const dst = out[columnName]
      if (!dst) return
      const from = Math.max(rowStart - at, 0)
      const to = Math.min(columnData.length, end - at)
      const off = at - rowStart
      if (!(columnName in typed)) {
        for (let i = from; i < to; i++) dst[off + i] = columnData[i]
        return
      }
      const empty = nan.includes(columnName) ? NaN : 0
      const d = dst as Typed
      if (columnData instanceof BigInt64Array || columnData instanceof BigUint64Array) {
        for (let i = from; i < to; i++) d[off + i] = Number(columnData[i])
      } else if (ArrayBuffer.isView(columnData)) {
        const v = columnData as unknown as ArrayLike<number>
        for (let i = from; i < to; i++) d[off + i] = v[i]
      } else {
        for (let i = from; i < to; i++) {
          const v = columnData[i]
          d[off + i] = v === null || v === undefined ? empty : Number(v)
        }
      }
    },
  })
  return out
}

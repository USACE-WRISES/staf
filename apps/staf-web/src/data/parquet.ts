/**
 * Parquet by HTTP range (hyparquet): footers cached per file, row groups chosen from their
 * statistics, and only the requested columns of the chosen row groups read.
 */
import { parquetMetadataAsync, parquetRead, parquetReadObjects, type FileMetaData } from 'hyparquet'
import { compressors, zstdReady } from './zstd'
import type { DataSource } from './source'

const metas = new Map<string, Promise<FileMetaData>>()
/** The first read from a file's end: our footers run 25 to 95 KB, and a larger one costs one more
 * request (hyparquet's default reads 512 KB). */
export const FOOTER_FETCH = 1 << 16

export function metadata(src: DataSource, logical: string): Promise<FileMetaData> {
  const key = src.base + logical
  let m = metas.get(key)
  if (!m) {
    m = src.ready().then(() => parquetMetadataAsync(src.file(logical), { initialFetchSize: FOOTER_FETCH }))
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

export async function readRows(src: DataSource, logical: string, columns: string[], rowStart: number,
                               rowEnd: number): Promise<Record<string, any>[]> {
  const meta = await metadata(src, logical)
  await Promise.all([src.ready(), zstdReady()])
  return parquetReadObjects({ file: src.file(logical), metadata: meta, columns, rowStart, rowEnd, compressors })
}

type Typed = Float64Array | Int32Array | Uint8Array
type TypedKind = 'f64' | 'i32' | 'u8'

/** Columns of rows [rowStart, rowEnd) (the whole file by default) as one array per column, without
 * building a row object each: a region's network or catchments read at once. ``typed`` names the
 * columns to return as typed arrays (int64 as float64 numbers, nulls as 0, or NaN for f64 with
 * ``nan``); the others come as hyparquet decodes them (int64 as bigint, lists as arrays). */
export async function readColumns(src: DataSource, logical: string, columns: string[], rowStart = 0,
                                  rowEnd?: number, typed: Record<string, TypedKind> = {},
                                  nan: string[] = []): Promise<Record<string, any>> {
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
    file: src.file(logical), metadata: meta, columns, rowStart, rowEnd: end, compressors,
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

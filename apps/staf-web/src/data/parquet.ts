/**
 * Parquet by HTTP range (hyparquet): footers cached per file, row groups chosen from their
 * statistics, and only the requested columns of the chosen row groups read.
 */
import { parquetMetadataAsync, parquetReadObjects, type FileMetaData } from 'hyparquet'
import { compressors } from 'hyparquet-compressors'
import type { DataSource } from './source'

const metas = new Map<string, Promise<FileMetaData>>()
/** The first read from a file's end: our footers run 25 to 95 KB, and a larger one costs one more
 * request (hyparquet's default reads 512 KB). */
export const FOOTER_FETCH = 1 << 16

export function metadata(src: DataSource, logical: string): Promise<FileMetaData> {
  const key = src.base + logical
  let m = metas.get(key)
  if (!m) {
    m = parquetMetadataAsync(src.file(logical), { initialFetchSize: FOOTER_FETCH })
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
  return parquetReadObjects({ file: src.file(logical), metadata: await metadata(src, logical), columns,
                              rowStart, rowEnd, compressors })
}

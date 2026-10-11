/**
 * The bundle's HR flowlines (`lines2_<vpu>.parquet`: Hilbert-sorted, 4,096-row groups, bounding boxes
 * as 1e-5 degree integers with statistics): the lines in a box, read by range
 * (site_engine/_hrslim/reader2.py `lines_in_bbox`).
 */
import { columnStats, metadata, readRows, rowGroups } from './parquet'
import type { DataSource } from './source'

export interface Flowline {
  nhdplusid: bigint
  gnisName: string | null
  fcode: number
  totdasqkm: number
  x: number[]
  y: number[]
  parts: number[]
  bbox: [number, number, number, number]
}

export const LINE_COLUMNS = ['nhdplusid', 'gnis_name', 'fcode', 'totdasqkm', 'x', 'y', 'parts', 'xmin', 'ymin', 'xmax', 'ymax']

/** Gridded flowline types: StreamRiver (460xx) and CanalDitch (336xx). */
export function gridded(fcode: number): boolean {
  return (fcode >= 46000 && fcode < 46100) || (fcode >= 33600 && fcode < 33700)
}

function toFlowline(r: Record<string, any>): Flowline {
  return {
    nhdplusid: BigInt(r.nhdplusid), gnisName: r.gnis_name ?? null, fcode: Number(r.fcode),
    totdasqkm: Number(r.totdasqkm), x: Array.from(r.x as ArrayLike<number>, Number),
    y: Array.from(r.y as ArrayLike<number>, Number), parts: Array.from(r.parts as ArrayLike<number>, Number),
    bbox: [Number(r.xmin) / 1e5, Number(r.ymin) / 1e5, Number(r.xmax) / 1e5, Number(r.ymax) / 1e5],
  }
}

/** Flowlines of region `vpu` whose box meets `box` (west, south, east, north in degrees). */
export async function linesInBox(src: DataSource, vpu: string, box: [number, number, number, number]): Promise<Flowline[]> {
  const logical = `bundle/lines2_${vpu}.parquet`
  const meta = await metadata(src, logical)
  const [W, S, E, N] = box.map((v) => Math.round(v * 1e5))
  const x0 = columnStats(meta, 'xmin')
  const x1 = columnStats(meta, 'xmax')
  const y0 = columnStats(meta, 'ymin')
  const y1 = columnStats(meta, 'ymax')
  const groups = rowGroups(meta).filter((g) => {
    const a = x0[g.index]
    const b = x1[g.index]
    const c = y0[g.index]
    const d = y1[g.index]
    if (!a || !b || !c || !d) return true
    return !(Number(a.min) > E || Number(b.max) < W || Number(c.min) > N || Number(d.max) < S)
  })
  const rows = (await Promise.all(groups.map((g) => readRows(src, logical, LINE_COLUMNS, g.start, g.start + g.rows)))).flat()
  return rows
    .filter((r) => !(Number(r.xmin) > E || Number(r.xmax) < W || Number(r.ymin) > N || Number(r.ymax) < S))
    .map(toFlowline)
}

/** One row group's flowlines (for the proof page's choice of a site). */
export async function rowGroupLines(src: DataSource, vpu: string, index: number): Promise<Flowline[]> {
  const logical = `bundle/lines2_${vpu}.parquet`
  const g = rowGroups(await metadata(src, logical))[index]
  return (await readRows(src, logical, LINE_COLUMNS, g.start, g.start + g.rows)).map(toFlowline)
}

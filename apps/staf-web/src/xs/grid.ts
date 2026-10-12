/**
 * The cross-section grid (`staf-xs-current`; tools/xs-grid/xsgrid/release.py): per region the
 * numbers each section scores with (`xs-<vpu>.parquet`), what drawing or pulling a section needs
 * (`xs-<vpu>-sections.parquet`, same rows) and one exact profile per segment (`xs-<vpu>-median.parquet`).
 * Rows are sorted by flowline; a flowline's rows are found from the row groups' `nhdplusid` statistics.
 */
import { decode } from './codec'
import { metadata, readObjectsWhere } from '../data/parquet'
import type { DataSource } from '../data/source'

export const STATUS = ['ok', 'unbalanced', 'no_dem', 'too_few_points', 'ok_reference'] as const
export const BANK = ['slope_break', 'crest_scan'] as const
export const FLAGS = { lowBankCapped: 1, edgeLimited: 2, bankfullAreaEdgeLimited: 4, bfExtrapolated: 8 }

export interface XsNumbers {
  nhdplusid: bigint
  part: number
  k: number
  nSec: number
  res: number
  status: (typeof STATUS)[number]
  bank: (typeof BANK)[number] | null
  flags: number
  er: number | null
  bhr: number | null
  bfd: number | null
  bfw: number | null
  fpw: number | null
}

export interface XsSection {
  nhdplusid: bigint
  part: number
  k: number
  thalweg: number | null
  thalwegI: number | null
  bfM: number | null
  lbM: number | null
  daSqkm: number
  division: string | null
  rcWidth: number
  rcDepth: number
  rcArea: number
  wide: number
  nPts: number
  source: string
  tiles: string
  /** the build's reads of this section (release format 2): CRS and window corner, in reading order */
  reads: Array<{ epsg: number; c: number; f: number; a: number; e: number }>
}

export interface XsMedian {
  nhdplusid: bigint
  part: number
  k: number
  res: number
  wide: number
  nPts: number
  thalweg: number | null
  thalwegI: number | null
  bfM: number | null
  lbM: number | null
  z: Float32Array
}

const scaled = (v: unknown, scale: number): number | null => (v === null || v === undefined ? null : Number(v) / scale)

/** A flowline's rows: the row groups and (where the file has a page index, the browser layout's copies)
 * the pages whose nhdplusid range holds it, its rows among them. */
async function flowlineRows(src: DataSource, logical: string, id: bigint, columns: string[]): Promise<Record<string, any>[]> {
  const rows = await readObjectsWhere(src, logical, columns, { nhdplusid: { $eq: id } })
  return rows.filter((r) => BigInt(r.nhdplusid) === id).sort((a, b) => a.part - b.part || a.k - b.k)
}

export async function sectionNumbers(src: DataSource, vpu: string, id: bigint): Promise<XsNumbers[]> {
  const rows = await flowlineRows(src, `xs/xs-${vpu}.parquet`, id,
    ['nhdplusid', 'part', 'k', 'n_sec', 'res', 'status', 'bank', 'flags', 'er', 'bhr', 'bfd', 'bfw', 'fpw'])
  return rows.map((r) => ({
    nhdplusid: BigInt(r.nhdplusid), part: r.part, k: r.k, nSec: r.n_sec, res: r.res, status: STATUS[r.status],
    bank: r.bank === 255 ? null : BANK[r.bank], flags: r.flags, er: scaled(r.er, 100), bhr: scaled(r.bhr, 100),
    bfd: scaled(r.bfd, 100), bfw: scaled(r.bfw, 10), fpw: scaled(r.fpw, 10),
  }))
}

export async function sectionDetails(src: DataSource, vpu: string, id: bigint): Promise<XsSection[]> {
  const logical = `xs/xs-${vpu}-sections.parquet`
  const meta = await metadata(src, logical)
  const hasGrids = meta.schema.some((s) => s.name === 'grid_epsg')
  const rows = await flowlineRows(src, logical, id,
    ['nhdplusid', 'part', 'k', 'thalweg', 'thalweg_i', 'bf_m', 'lb_m', 'da_sqkm', 'division', 'rc_width', 'rc_depth',
     'rc_area', 'wide_cm', 'n_pts', 'source', 'tiles',
     ...(hasGrids ? ['grid_epsg', 'grid_c', 'grid_f', 'grid_a', 'grid_e'] : [])])
  return rows.map((r) => ({
    nhdplusid: BigInt(r.nhdplusid), part: r.part, k: r.k, thalweg: r.thalweg ?? null, thalwegI: r.thalweg_i ?? null,
    bfM: r.bf_m ?? null, lbM: r.lb_m ?? null, daSqkm: r.da_sqkm, division: r.division ?? null,
    rcWidth: r.rc_width / 100, rcDepth: r.rc_depth / 1000, rcArea: r.rc_area / 100, wide: r.wide_cm / 100,
    nPts: r.n_pts, source: r.source, tiles: r.tiles,
    reads: hasGrids ? Array.from(r.grid_epsg as ArrayLike<number>, (epsg: number, i: number) => ({
      epsg: Number(epsg), c: Number(r.grid_c[i]), f: Number(r.grid_f[i]), a: Number(r.grid_a), e: Number(r.grid_e),
    })) : [],
  }))
}

/** What an exact pull needs beyond `sectionDetails` (release format 3). */
export interface XsPull {
  part: number
  k: number
  /** the archive's own position and unit direction (EPSG:5070, float64) */
  x: number
  y: number
  nx: number
  ny: number
  /** zlib's CRC-32 of the profile's archive codes (`profileCrc`), null without samples */
  zCrc: number | null
}

/** Each section's stored position, direction and profile checksum, read only when a section is pulled
 * (four float64 columns and one uint32 of the flowline's row group). Null for a release older than
 * format 3: then `placeSections` places them from the bundle's lines, about 1e-9 m off. */
export async function sectionPulls(src: DataSource, vpu: string, id: bigint): Promise<XsPull[] | null> {
  const logical = `xs/xs-${vpu}-sections.parquet`
  const meta = await metadata(src, logical)
  if (!meta.schema.some((s) => s.name === 'z_crc')) return null
  const rows = await flowlineRows(src, logical, id, ['nhdplusid', 'part', 'k', 'x', 'y', 'nx', 'ny', 'z_crc'])
  return rows.map((r) => ({
    part: r.part, k: r.k, x: Number(r.x), y: Number(r.y), nx: Number(r.nx), ny: Number(r.ny),
    zCrc: r.z_crc === null || r.z_crc === undefined ? null : Number(r.z_crc),
  }))
}

export async function medianProfile(src: DataSource, vpu: string, id: bigint): Promise<XsMedian | null> {
  const rows = await flowlineRows(src, `xs/xs-${vpu}-median.parquet`, id,
    ['nhdplusid', 'part', 'k', 'res', 'wide_cm', 'n_pts', 'thalweg', 'thalweg_i', 'bf_m', 'lb_m', 'z'])
  const r = rows[0]
  if (!r) return null
  return {
    nhdplusid: BigInt(r.nhdplusid), part: r.part, k: r.k, res: r.res, wide: r.wide_cm / 100, nPts: r.n_pts,
    thalweg: r.thalweg ?? null, thalwegI: r.thalweg_i ?? null, bfM: r.bf_m ?? null, lbM: r.lb_m ?? null,
    z: decode(Int32Array.from(r.z as ArrayLike<number>)),
  }
}

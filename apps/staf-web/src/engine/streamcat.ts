// StreamCat values by NHDPlus V2 COMID from the bundle's slices (site_engine bundle.streamcat, the
// apps' streamcat._from_bundle): every column a requested base name begins, as the EPA API answers
// them, from the V2 region holding the COMID.
import { metadata, readColumns } from '../data/parquet'
import type { DataSource } from '../data/source'
import type { Dataset } from './dataset'

const comidRows = new Map<string, Promise<Map<number, number>>>()

function rowsOf(src: DataSource, logical: string): Promise<Map<number, number>> {
  const key = src.base + logical
  let got = comidRows.get(key)
  if (!got) {
    got = readColumns(src, logical, ['comid'], 0, undefined, { comid: 'f64' })
      .then((t) => new Map(Array.from(t.comid as Float64Array, (c, i) => [c, i] as [number, number])))
    got.catch(() => comidRows.delete(key))
    comidRows.set(key, got)
  }
  return got
}

/** ``{comid, column: value}`` for every column a base name begins (lowercase), or null unless the
 * bundle holds the COMID and a column for every base name. */
export async function streamcatRow(src: DataSource, v2: Dataset, comid: number, baseNames: string[]): Promise<Record<string, number | null> | null> {
  try {
    const names = baseNames.filter(Boolean).map((b) => b.toLowerCase())
    if (!names.length) return null
    const found = await v2.find('by_id', [comid])
    if (!found.length) return null
    const logical = `bundle/v2/streamcat2_${found[0].region.vpu}.parquet`
    await src.ready()
    if (!src.has(logical)) return null
    const cols = (await metadata(src, logical)).schema.slice(1).map((s) => s.name).filter((c) => c !== 'comid')
    const wanted = cols.filter((c) => names.some((b) => c.toLowerCase().startsWith(b)))
    if (names.some((b) => !wanted.some((c) => c.toLowerCase().startsWith(b)))) return null
    // the slice's rows follow the region's V2 lines, so the COMID's line row is its row there (read
    // with its comid to be sure, through the page index); else the slice's comid column says where
    let row = found[0].rows[0]
    let t = await readColumns(src, logical, ['comid', ...wanted], row, row + 1, {}, [], true)
    if (Number(t.comid[0]) !== comid) {
      const at = (await rowsOf(src, logical)).get(comid)
      if (at === undefined) return null
      row = at
      t = await readColumns(src, logical, wanted, row, row + 1, {}, [], true)
    }
    const out: Record<string, number | null> = { comid }
    for (const c of wanted) {
      const v = t[c][0]
      out[c.toLowerCase()] = v === null || v === undefined ? null : Number(v)
    }
    return out
  } catch {
    return null
  }
}

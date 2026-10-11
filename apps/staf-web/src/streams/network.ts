// The stream lines of a box from the STAF data bundle, as the Shiny tools read them for the map.
//
// The bundle holds the NHDPlus HR network (bundle/lines2_<vpu>.parquet) and the NHDPlus V2 network
// (bundle/v2/lines2_<vpu>.parquet): Hilbert-sorted rows with their boxes in 1e-5 degree integers, row
// groups with statistics. A box's lines (site_engine bundle.lines_in_box / v2_lines_in_box, through
// _hrslim/reader2.py lines_in_bbox): the regions whose bounds meet the box, in region order; the row
// groups whose extents meet the box rounded outward to 1e-5 degree; the rows whose boxes meet it; then
// the lines that meet the box itself. Each becomes a GeoJSON feature as the tools draw it: HR lines
// with their nhdplusid (nhd_hr._id_only), V2 lines with their COMID and name (flowlines._fetch).
// The Shiny SFARI and DEEP ask the USGS fabric service for the V2 lines instead; the static app reads
// the bundle's (EASI's source) in every tool.
import { columnStats, metadata, readRows, rowGroups } from '../data/parquet'
import type { DataSource } from '../data/source'
import { datasetRegions, type RegionMeta } from '../engine/bundle'
import type { Feature, FeatureCollection } from './display'
import { linesMeetBox, type Line } from './geom'

const SCALE = 100_000
const COLUMNS = ['nhdplusid', 'gnis_name', 'x', 'y', 'parts', 'xmin', 'ymin', 'xmax', 'ymax']

export interface LineRecord {
  id: number
  name: string | null
  /** degrees, one list of [lon, lat] per part */
  parts: Line[]
  /** where the line is: its region and its row in the region's file (for its attributes) */
  region: RegionMeta
  row: number
}

/** One of the bundle's line networks: ``folder`` is "bundle/" (HR) or "bundle/v2/" (V2). */
export class Network {
  constructor(readonly src: DataSource, readonly folder: string) {}

  /** The lines that meet the box (west, south, east, north in degrees), in the reader's order. */
  async inBox(west: number, south: number, east: number, north: number): Promise<LineRecord[]> {
    const [w, s, e, n] = [Math.min(west, east), Math.min(south, north), Math.max(west, east), Math.max(south, north)]
    const qw = Math.floor(w * SCALE)
    const qs = Math.floor(s * SCALE)
    const qe = Math.ceil(e * SCALE)
    const qn = Math.ceil(n * SCALE)
    const regions = (await datasetRegions(this.src, this.folder)).filter(({ bounds: [bw, bs, be, bn] }) => bw <= e && be >= w && bs <= n && bn >= s)
    const perRegion = await Promise.all(regions.map(async (region) => {
      const logical = `${this.folder}${region.lines}`
      const meta = await metadata(this.src, logical)
      const [x0, y0, x1, y1] = ['xmin', 'ymin', 'xmax', 'ymax'].map((c) => columnStats(meta, c))
      const groups = rowGroups(meta).filter((g) => {
        const a = x0[g.index], b = x1[g.index], c = y0[g.index], d = y1[g.index]
        if (!a || !b || !c || !d) return true
        return Number(a.min) <= qe && Number(b.max) >= qw && Number(c.min) <= qn && Number(d.max) >= qs
      })
      const rows = await Promise.all(groups.map(async (g) => (await readRows(this.src, logical, COLUMNS, g.start, g.start + g.rows))
        .map((r, k) => ({ r, row: g.start + k }))))
      return rows.flat().map((x) => ({ ...x, region }))
    }))
    const out: LineRecord[] = []
    for (const { r, row, region } of perRegion.flat()) {
      if (Number(r.xmin) > qe || Number(r.xmax) < qw || Number(r.ymin) > qn || Number(r.ymax) < qs) continue
      const xs = r.x as ArrayLike<number>
      const ys = r.y as ArrayLike<number>
      const counts = r.parts as ArrayLike<number>
      const parts: Line[] = []
      let k = 0
      for (let p = 0; p < counts.length; p++) {
        const part: Line = []
        for (let j = 0; j < Number(counts[p]); j++, k++) part.push([Number(xs[k]) / SCALE, Number(ys[k]) / SCALE])
        parts.push(part)
      }
      if (!parts.length || !linesMeetBox(parts, w, s, e, n)) continue
      const name = r.gnis_name == null ? null : String(r.gnis_name).trim() || null
      out.push({ id: Number(r.nhdplusid), name, parts, region, row })
    }
    return out
  }
}

function geometry(parts: Line[]): Feature['geometry'] {
  return parts.length === 1 ? { type: 'LineString', coordinates: parts[0] } : { type: 'MultiLineString', coordinates: parts }
}

/** The map's HR lines of a box: features with their nhdplusid, or null when there are none. */
export function hrFeatures(lines: LineRecord[]): FeatureCollection | null {
  const features: Feature[] = lines.filter((l) => l.parts.some((p) => p.length))
    .map((l) => ({ type: 'Feature', properties: { nhdplusid: l.id }, geometry: geometry(l.parts) }))
  return features.length ? { type: 'FeatureCollection', features } : null
}

/** The map's V2 lines of a box: features with their COMID and name, or null when there are none. */
export function v2Features(lines: LineRecord[]): FeatureCollection | null {
  const features: Feature[] = lines.filter((l) => l.parts.some((p) => p.length)).map((l) => {
    const properties: Record<string, unknown> = { comid: l.id }
    if (l.name) properties.gnis_name = l.name
    return { type: 'Feature', properties, geometry: geometry(l.parts) }
  })
  return features.length ? { type: 'FeatureCollection', features } : null
}

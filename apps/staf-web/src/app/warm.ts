// What a tool reads once Delineate is pressed, started when its pick is ready.
//
// On Hugging Face a file's first request costs 0.5 to 0.7 s (the look-up, then the CDN's first read of
// the file; traced 2026-10-12), and Delineate and the Assessment step open their files one after
// another, each waiting on the last. Opened while the user decides to delineate, the files are there
// when the steps reach them. A parquet file is opened: its footer read in one request, or the whole
// file when it is under the whole-file size (`WHOLE`); a whole file is read (`bytesOf`, which its later
// reader shares); the base-flow grid's first bytes are read the way `metrics.bfiImage` reads them.
// Nothing is decoded or computed. The lists are the files each tool read after the pick at Mink Brook
// (`parity/perf.py --trace`), less what `Dataset.warm` already opens (the topology and catchments).
import { metadata } from '../data/parquet'
import type { DataSource } from '../data/source'
import type { ToolKey } from './markup'

const POINTS = ['points/nid.parquet', 'points/wqp_stations.parquet', 'points/wqp_results.parquet']
const VALUES = ['bundle/values/lc2_{vpu}.parquet', 'bundle/values/rip2_{vpu}.parquet', 'bundle/values/roads2_{vpu}.parquet',
  'bundle/values/dams2_{vpu}.parquet', 'bundle/values/soils2_{vpu}.parquet', 'bundle/values/xings2_{vpu}.parquet']

/** Each tool's files, a region's named with `{vpu}`. */
export const WARM: Record<ToolKey, string[]> = {
  easi: ['bundle/v2/idindex.parquet', 'bundle/v2/links2.parquet', 'bundle/v2/v2attrs2_{vpu}.parquet', 'bundle/v2/streamcat2_{vpu}.parquet',
    'topo/hr_{vpu}.parquet', 'bundle/values/extras2_{vpu}.parquet', 'bundle/values/au2_{vpu}.parquet', 'bundle/lines2_{vpu}.parquet',
    'xs/xs-{vpu}.parquet', 'xs/xs-{vpu}-sections.parquet', 'xs/xs-{vpu}-median.parquet', ...POINTS,
    'points/wqp_temperature.parquet', 'points/nas.parquet', 'engine/ecoregions_l3.geojson', 'engine/physio_divisions.geojson'],
  sfari: ['bundle/links2.parquet', 'bundle/values/extras2_{vpu}.parquet', ...VALUES, 'bundle/v2/idindex.parquet', 'topo/v2_{vpu}.parquet',
    'bundle/v2/streamcat2_{vpu}.parquet', 'xs/xs-{vpu}.parquet', 'xs/xs-{vpu}-sections.parquet', ...POINTS, 'points/nwis.parquet',
    'engine/physio_divisions.geojson', 'engine/bfi48grd.tif'],
  deep: ['bundle/links2.parquet', 'bundle/values/extras2_{vpu}.parquet', ...VALUES, 'bundle/v2/idindex.parquet', 'topo/v2_{vpu}.parquet',
    'bundle/v2/streamcat2_{vpu}.parquet', 'points/nid.parquet', 'engine/bfi48grd.tif'],
}

/** The base-flow grid's first read (`metrics.bfiImage`: its first 16 KB, or the whole file when small). */
const BFI_HEAD = 16 * 1024

/** Opens the tool's files for these regions (in the background; a failed read is made again when needed). */
export function warmFiles(src: DataSource, tool: ToolKey, vpus: string[]): void {
  void src.ready().then(() => {
    for (const pattern of WARM[tool]) {
      for (const logical of new Set(vpus.map((vpu) => pattern.replace('{vpu}', vpu)))) {
        if (!src.has(logical)) continue
        const read = logical.endsWith('.parquet') ? metadata(src, logical)
          : logical.endsWith('.tif') ? src.file(logical).slice(0, Math.min(BFI_HEAD, src.entry(logical).bytes))
          : src.bytesOf(logical)
        void Promise.resolve(read).catch(() => {})
      }
    }
  }).catch(() => {})
}

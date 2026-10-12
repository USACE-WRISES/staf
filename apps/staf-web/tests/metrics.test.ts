// The STAF site engine's metric families against the Python reference (metrics_ref.json,
// make_metrics_fixtures.py), from the local layout; skipped where the layout is not.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { hasPageIndex } from '../src/engine/blocks'
import { encodingOf } from '../src/engine/bundle'
import { Dataset } from '../src/engine/dataset'
import { computeAll } from '../src/engine/metrics'
import { computeSite, INTERACTIVE_CONFIG } from '../src/engine/site'
import { ValueTables, watershedValues } from '../src/engine/values'
import { Network } from '../src/streams/network'
import ref from './fixtures/metrics_ref.json'
import { BASE, have, localFetch } from './local'

const cases = (ref as { families: string[], cases: any[] }).cases

describe.skipIf(!have)('the engine computes its metric families as the Python does', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())

  it("sums the bundle's per-catchment values over the walk", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const hr = new Dataset(src, 'bundle/')
    const values = new ValueTables(src)
    for (const c of cases) {
      const got = await watershedValues(hr, values, c.nhdplusId, 3000, 190)
      expect(got, c.name).toEqual(c.values)
    }
  })

  it("reads a small region's value tables in a request each, and a larger table's blocks", { timeout: 120_000 }, async (ctx) => {
    const src = new DataSource(BASE)
    if (!(await hasPageIndex(src, 'bundle/values/lc2_0108.parquet'))) ctx.skip()   // a layout from before the page index
    const hr = new Dataset(src, 'bundle/')
    const c = cases.find((x) => x.name === 'headwater')!
    await hr.walk(c.nhdplusId, 3000, 190)
    await encodingOf(src, 'bundle/', (await hr.region('0108'))!)
    const before = src.requests
    expect(await watershedValues(hr, new ValueTables(src), c.nhdplusId, 3000, 190)).toEqual(c.values)
    // 0108's six tables are each under the size read whole (WHOLE): one request each, where their pages took 70
    expect(src.requests - before).toBe(6)
  })

  it('gives the metric entries key for key', { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const hr = new Dataset(src, 'bundle/')
    const d = { src, hr, hrNet: new Network(src, 'bundle/'), values: new ValueTables(src) }
    for (const c of cases) {
      const rec = await computeSite(d, c.lat, c.lon, { ...INTERACTIVE_CONFIG, includeGeometry: true, metricFamilies: ref.families, landcoverBaseline: true })
      const metrics = await computeAll(d, rec as any, ref.families)
      expect(Object.keys(metrics), c.name).toEqual(Object.keys(c.metrics))
      expect(metrics, c.name).toEqual(c.metrics)
    }
  })
})

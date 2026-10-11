// Delineate against the Python reference (delineate_ref.json, make_delineate_fixtures.py), from the
// local layout; skipped where the layout is not.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { Dataset } from '../src/engine/dataset'
import { deriveReachV2 } from '../src/engine/reach'
import { delineateFromEngine, delineateOnlyEasi, flowlineAttrsV2, geojsonBounds, planarAreaSqkm, reanchorInputs } from '../src/engine/pipeline'
import { displaySimplify } from '../src/engine/simplify'
import { computeSite, INTERACTIVE_CONFIG } from '../src/engine/site'
import { ValueTables } from '../src/engine/values'
import { Network } from '../src/streams/network'
import ref from './fixtures/delineate_ref.json'
import { BASE, have, localFetch } from './local'

const cases = (ref as { cases: any[] }).cases
const BUDGET = { maxReaches: 3000, maxHops: 190 }

function coordsOf(fc: any): unknown {
  return fc.features[0].geometry.coordinates
}

/** Reach coordinates agree within 1e-11 degree (the inverse projection's last bits differ between
 * libms), shape and count exactly. */
function expectNear(got: any, want: any, label: string) {
  const g = got.features[0].geometry
  const w = want.features[0].geometry
  expect(g.type, label).toBe(w.type)
  const flat = (c: any): number[] => (typeof c[0] === 'number' ? c : c.flatMap(flat))
  const a = flat(g.coordinates)
  const b = flat(w.coordinates)
  expect(a.length, label).toBe(b.length)
  a.forEach((v, i) => expect(Math.abs(v - b[i]), `${label} [${i}]`).toBeLessThan(1e-11))
}

describe.skipIf(!have)('the bundle delineates as the site engine does', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())

  it('walks the HR tree and outlines its catchments', { timeout: 120_000 }, async () => {
    const hr = new Dataset(new DataSource(BASE), 'bundle/')
    for (const c of cases) {
      const e = c.engine
      const ws = await hr.watershed(e.site.nhdPlusId ?? e.site.nhdplusId, BUDGET.maxReaches, BUDGET.maxHops)
      expect([ws.status, ws.nReaches, ws.nHops], c.name).toEqual([e.watershed.status, e.watershed.nReaches, e.watershed.nHops])
      expect(ws.ids, c.name).toEqual(e.ids)
      expect(ws.nCatchments, c.name).toBe(e.nCatchments)
      expect(ws.areaSqkm, c.name).toBe(e.watershed.areaSqkm)
      expect(ws.geometry?.coordinates, c.name).toEqual(coordsOf(e.watershed.polygon))
    }
  })

  it("walks EASI's V2 basin and outlines it", { timeout: 120_000 }, async () => {
    const v2 = new Dataset(new DataSource(BASE), 'bundle/v2/')
    for (const c of cases.filter((x) => x.v2)) {
      const want = c.v2
      const ws = await v2.watershed(want.input[2], 50_000, 50_000)
      expect([ws.status, ws.nReaches], c.name).toEqual(['ok', want.nReaches])
      expect(ws.areaSqkm, c.name).toBe(want.bundleAreaSqkm)
      expect(ws.geometry?.coordinates, c.name).toEqual(coordsOf(want.watershed))
    }
  })

  it("computes the engine's record: site, watershed and reach", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), values: new ValueTables(src) }
    for (const c of cases) {
      const e = c.engine
      const rec = await computeSite(d, e.input[0], e.input[1], { ...INTERACTIVE_CONFIG, reachLengthFt: ref.reachFt })
      expect(rec.status, `${c.name}: ${rec.reason}`).toBe('ok')
      expect({ ...rec.site, ecoregionL3: e.site.ecoregionL3 }, c.name).toEqual(e.site)
      const { polygon, ...wsRest } = rec.watershed!
      const { polygon: wantPolygon, ...wantRest } = e.watershed
      expect(wsRest, c.name).toEqual(wantRest)
      expect(polygon, c.name).toEqual(wantPolygon)
      expect(rec.reach!.lengthFt, c.name).toBe(e.reach.lengthFt)
      expect(rec.reach!.warnings, c.name).toEqual(e.reach.warnings)
      expectNear(rec.reach!.geometry, e.reach.geometry, c.name)
    }
  })

  it("trims EASI's V2 reach", { timeout: 120_000 }, async () => {
    const v2 = new Dataset(new DataSource(BASE), 'bundle/v2/')
    for (const c of cases.filter((x) => x.v2)) {
      const [, , comid, lat, lon] = c.v2.input
      const r = await deriveReachV2(v2, comid, lat, lon, ref.reachFt)
      expect(r.lengthFt, c.name).toBe(c.v2.reachLengthFt)
      expectNear(r.geojson, c.v2.reach, c.name)
    }
  })

  it('simplifies a large watershed for the map as shapely does', () => {
    for (const c of cases) {
      expect(displaySimplify(c.engine.watershed.polygon), c.name).toEqual(c.engine.display)
      if (c.v2) expect(displaySimplify(c.v2.watershed), c.name).toEqual(c.v2.display)
    }
  })

  it("reads EASI's V2 attributes and measures its basin", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), v2: new Dataset(src, 'bundle/v2/'), values: new ValueTables(src) }
    for (const c of cases.filter((x) => x.v2)) {
      const { attrs } = await flowlineAttrsV2(d, c.v2.input[2])
      const { comid: _c, ...want } = c.v2.attrs
      expect(attrs, c.name).toEqual(want)
      const area = planarAreaSqkm(c.v2.watershed.features[0].geometry)
      expect(Math.abs(area - c.v2.watershedAreaSqkm), c.name).toBeLessThan(1e-9)
      const bounds = geojsonBounds(c.v2.watershed, c.v2.reach)!
      bounds.flat().forEach((v, i) => expect(Math.abs(v - c.v2.bounds.flat()[i]), c.name).toBeLessThan(1e-9))
    }
    for (const c of cases) {
      const e = c.engine
      const bounds = geojsonBounds(e.watershed.polygon, e.reach.geometry)!
      bounds.flat().forEach((v, i) => expect(Math.abs(v - e.bounds.flat()[i]), c.name).toBeLessThan(1e-9))
    }
  })

  it("re-anchors EASI's routed sites on the clicked HR stream", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), v2: new Dataset(src, 'bundle/v2/'), values: new ValueTables(src) }
    for (const c of cases.filter((x) => x.reanchor)) {
      const anchor = structuredClone(c.route.anchor)
      const got = await reanchorInputs(d, anchor, ref.reachFt)
      const { reach_geojson: gr, ...rest } = got
      const { reach_geojson: wr, ...wantRest } = c.reanchor
      expect(rest, c.name).toEqual(wantRest)
      expectNear(gr, wr, c.name)
      expect(anchor.reanchored, c.name).toEqual({ applied: true, warnings: c.reanchor._warnings })
    }
  })

  it("returns EASI's Delineate result key for key", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), v2: new Dataset(src, 'bundle/v2/'), values: new ValueTables(src) }
    for (const c of cases.filter((x) => x.easi)) {
      const [lat, lon, comid] = c.easi.input
      const got = await delineateOnlyEasi(d, lat, lon, ref.reachFt, comid, structuredClone(c.easi.anchor))
      const want = structuredClone(c.easi.result)
      // the reach agrees within 1e-11 degree; the engine's record has no ecoregion yet
      expectNear(got.reach_geojson, want.reach_geojson, c.name)
      expectNear(got.ctx_inputs.reach_geojson, want.ctx_inputs.reach_geojson, c.name)
      for (const r of [got, want]) {
        delete r.reach_geojson
        delete r.ctx_inputs.reach_geojson
      }
      const rec = want.ctx_inputs.watershedEngine?.record
      if (rec) {
        // the reference leaves the engine's metrics out (tests/metrics.test.ts checks them)
        got.ctx_inputs.watershedEngine.record.site.ecoregionL3 = rec.site.ecoregionL3
        got.ctx_inputs.watershedEngine.record.metrics = {}
      }
      expect(got, c.name).toEqual(want)
    }
  })

  it("returns SFARI's Delineate result key for key", { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), values: new ValueTables(src) }
    for (const c of cases) {
      const rec = await computeSite(d, c.lat, c.lon, { ...INTERACTIVE_CONFIG, includeGeometry: true, metricFamilies: [] })
      rec.site!.ecoregionL3 = c.sfari.record.site.ecoregionL3
      const got = delineateFromEngine(rec, c.lat, c.lon, ref.reachFt)
      const want = structuredClone(c.sfari.result)
      expectNear(got.reach_geojson, want.reach_geojson, c.name)
      for (const r of [got, want]) {
        delete r.reach_geojson
        delete r.ctx_inputs.reach_geojson
      }
      expect(got, c.name).toEqual(want)
    }
  })
})


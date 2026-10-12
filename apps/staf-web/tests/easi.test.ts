// EASI's Assessment step against EASI's own code (easi_ref.json, make_easi_fixtures.py): the evaluator on
// random inputs for every method and variant, the criteria and equations, the cross-section math, the
// prefetch from the bundle, and the whole report of five screenings with overrides and observations,
// over the local layout (skipped where it is not).
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { Dataset } from '../src/engine/dataset'
import { balancedProfile, bankfullWidthCv, crossSectionQuality, deriveFromStages, describeReach, displayWindow, flowArea,
  simplifyProfile, summarizeProfile, topOfBankElev } from '../src/engine/geomorph'
import { PointTables } from '../src/engine/points'
import { ValueTables } from '../src/engine/values'
import { Network } from '../src/streams/network'
import { applyObservedEvidence, rateMetricsFromStages, rescore, scoreContext } from '../src/tools/easi/assess'
import { criteriaBands, criteriaFor, equationFor, evaluate, methodFor, resolvedMethod } from '../src/tools/easi/evaluator'
import { nars9Index } from '../src/tools/easi/geo'
import { loadMethod, metricsById } from '../src/tools/easi/method'
import { ctxFromInputs, type EasiAssessData, prefetch } from '../src/tools/easi/prefetch'
import { figure } from '../src/tools/easi/xsplot'
import ref from './fixtures/easi_ref.json'
import { BASE, have, localFetch } from './local'

const R = ref as any
const REACH_FT = 1000.0

describe.skipIf(!have)('EASI against its Python', () => {
  const src = new DataSource(BASE)
  beforeAll(async () => {
    vi.stubGlobal('fetch', localFetch)
    await loadMethod(src)
    await nars9Index()
  }, 120_000)
  afterAll(() => vi.unstubAllGlobals())

  it('evaluates every method as screening_methods.evaluate', () => {
    for (const e of R.evals) {
      const ev = evaluate(e.mid, e.values, e.opts)
      const where = `${e.mid} ${JSON.stringify(e.values)} ${e.opts.variant_key}`
      expect({ rating: ev.rating, index: ev.index, combined: ev.combined_value, trace: ev.trace }, where).toEqual(e.out)
    }
  })

  it('writes the criteria and equations as criteria_for and equation_for', () => {
    for (const c of R.criteria) {
      if (c.bands) {
        for (const [ind, bands] of Object.entries(c.bands)) {
          expect(criteriaBands(c.mid, ind || null, metricsById()[c.mid].criteria), `${c.mid} ${ind}`).toEqual(bands)
        }
        continue
      }
      const m = resolvedMethod(methodFor(c.mid), c.key)
      expect(criteriaFor(m, c.context), `${c.mid} ${c.key}`).toEqual(c.criteria)
      expect(equationFor(m), `${c.mid} ${c.key}`).toBe(c.equation)
    }
  })

  it('does the cross-section math as geomorph', () => {
    for (const g of R.geomorph) {
      const bal = balancedProfile(g.stations, g.elevs)
      expect(bal, g.source).toEqual(g.balanced)
      const simp = bal ? simplifyProfile(bal[0], bal[1]) : null
      expect(simp, g.source).toEqual(g.simplified)
      const [st, el] = simp ?? [g.stations, g.elevs]
      expect(summarizeProfile(st, el, g.da, { bankfull: g.bankfull, bankfullAreaM2: g.area, division: g.summary.bankfull_division ?? null,
        demResM: g.res }), g.source).toEqual(g.summary)
      const th = Math.min(...el)
      let k = 0
      for (const b of g.stages) {
        for (const f of [-0.3, 0.0, 0.8]) expect(deriveFromStages(st, el, th, b, b + f), `${g.source} ${b} ${f}`).toEqual(g.derived[k++])
      }
      expect(g.stages.map((s: number) => flowArea(st, el, s)), g.source).toEqual(g.flowArea)
      expect(g.stages.map((s: number) => displayWindow(st, el, s)), g.source).toEqual(g.window)
      expect(topOfBankElev(st, el), g.source).toEqual(g.topOfBank)
    }
    for (const q of R.quality) {
      expect(crossSectionQuality(q.geom)).toEqual(q.quality)
      expect(bankfullWidthCv(q.geom)).toEqual(q.widthCv)
      expect(['bank_height_ratio', 'entrenchment_ratio'].map((k) => describeReach(q.geom.reach, k))).toEqual(q.describe)
    }
    for (const s of R.stages) expect(rateMetricsFromStages(s.block, s.bf, s.lb, s.reason, s.strata)).toEqual(s.out)
  })

  it('draws the cross-section as xsplotly.figure', () => {
    for (const f of R.figures) {
      const got = figure(f.stations, f.elevs, f.thalweg, f.bf, f.lb, f.unit, f.source)
      expect(got.data, `${f.unit} ${f.bf} ${f.lb}`).toEqual(f.data)
      expect(got.layout, `${f.unit} ${f.bf} ${f.lb}`).toEqual(f.layout)
    }
  })

  const ctxWith = (c: any) => {
    const ctx = ctxFromInputs(JSON.parse(JSON.stringify(c.ci)))
    const p = c.prefetch
    Object.assign(ctx.extras, {
      strata: p.strata, streamcat: p.streamcat, landcover: {}, watershed: p.watershed, reach_geomorph: p.geom, nrsa: null,
      attains: { exact: p.attainsExact, nearby: p.attainsNearby }, wqp: p.wqp, nidDams: p.nid, nasTaxa: p.nas,
      nars9Name: p.strata.nars9 === 'NAP' ? 'Northern Appalachians' : p.strata.nars9 === 'SAP' ? 'Southern Appalachians' : null,
    })
    ctx.huc12 = p.huc12
    return ctx
  }

  it('scores the screenings as assessment.assess (the Python prefetch)', () => {
    for (const c of R.cases) {
      const report = scoreContext(ctxWith(c))
      expect(report, c.name).toEqual(c.report)
      expect(rescore(report, c.overrides), `${c.name} rescore`).toEqual(c.rescored)
      expect(applyObservedEvidence(report, c.observed), `${c.name} observed`).toEqual(c.observedReport)
    }
  })

  it('prefetches from the bundle as the Python reads it', { timeout: 300_000 }, async () => {
    const d: EasiAssessData = {
      src, hr: new Dataset(src, 'bundle/'), hrNet: new Network(src, 'bundle/'), values: new ValueTables(src),
      v2: new Dataset(src, 'bundle/v2/'), points: new PointTables(src),
    }
    for (const c of R.cases) {
      const ctx = ctxFromInputs(JSON.parse(JSON.stringify(c.ci)))
      await prefetch(d, ctx, REACH_FT, R.start)
      const p = c.prefetch
      expect(ctx.extras.strata, `${c.name} strata`).toEqual(p.strata)
      expect(ctx.extras.streamcat, `${c.name} streamcat`).toEqual(p.streamcat)
      expect(ctx.huc12, `${c.name} huc12`).toBe(p.huc12)
      expect(ctx.extras.attains, `${c.name} attains`).toEqual({ exact: p.attainsExact, nearby: p.attainsNearby })
      expect(ctx.extras.wqp, `${c.name} wqp`).toEqual(p.wqp)
      expect(ctx.extras.nasTaxa, `${c.name} nas`).toEqual(p.nas)
      expect(ctx.extras.nidDams, `${c.name} nid`).toEqual(p.nid)
      expect(ctx.extras.reach_geomorph, `${c.name} cross-sections`).toEqual(p.geom)
      expect(ctx.extras.watershed, `${c.name} watershed layer`).toEqual(p.watershed)
      expect(scoreContext(ctx), `${c.name} report`).toEqual(c.report)
    }
  })
})

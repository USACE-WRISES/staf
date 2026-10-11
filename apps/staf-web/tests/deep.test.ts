// DEEP's Assessment step against DEEP's own code (deep_ref.json, make_deep_fixtures.py): the words under
// each metric, the scores of random measured states, the worksheet's markup, the preselected curve sets
// and the desktop prefill, over the bundles of the local layout (skipped where it is not).
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { loadRef } from '../src/app/deepAssess'
import { TagList } from '../src/app/htm'
import { DataSource } from '../src/data/source'
import { Dataset } from '../src/engine/dataset'
import { PointTables } from '../src/engine/points'
import { computeMetrics } from '../src/tools/deep/computed'
import { fnFooter, metricRow, scorecard, unassessedPanel, withheldCard } from '../src/tools/deep/markup'
import { autoStrata, curveStrata, describe as describeMetric, measuredFromState, methodText, scoreSite, stratifierNote, stratumLabel, unitsOf } from '../src/tools/deep/method'
import ref from './fixtures/deep_ref.json'
import { BASE, have, localFetch } from './local'

const R = ref as any
const render = (t: any) => new TagList(t).render()

describe.skipIf(!have)('DEEP against its Python', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())
  const src = new DataSource(BASE)
  // the static app lists only the versions DEEP runs (hidden assessments and ineligible versions out)
  const bundle = async (r: string) => loadRef(src, r).catch((err) => {
    if (String(err).includes('unknown assessment ref')) return null
    throw err
  })
  const metric = (b: any, fid: string, mid: string) => b.metricsByFunction.find((f: any) => f.functionId === fid).metrics.find((m: any) => m.metricId === mid)

  it('words every metric as DEEP does', { timeout: 120_000 }, async () => {
    let tested = 0
    for (const w of R.words) {
      const b = await bundle(w.ref)
      if (!b) continue
      tested += 1
      const m = metric(b, w.functionId, w.metricId)
      const where = `${w.ref} ${w.metricId}`
      expect(curveStrata(m), where).toEqual(w.strata)
      for (const [s, text] of Object.entries(w.describe)) expect(describeMetric(m, s === 'None' ? null : s), `${where} ${s}`).toBe(text)
      expect(methodText(m), where).toBe(w.method)
      expect(unitsOf(m), where).toBe(w.units)
      expect(w.strata.map((s: string) => stratumLabel(s, m)), where).toEqual(w.labels)
    }
    expect(tested).toBeGreaterThan(R.words.length * 0.8)
  })

  it('scores measured states as curves.score_site', { timeout: 120_000 }, async () => {
    for (const s of R.scores) {
      const b = await bundle(s.ref)
      if (!b) continue
      const [sc, frs] = scoreSite(b, measuredFromState(s.state))
      expect(sc, s.ref).toEqual(s.result)
      expect(frs, s.ref).toEqual(s.functions)
    }
  })

  it('draws the worksheet as app.py', { timeout: 120_000 }, async () => {
    for (const r of R.rows) {
      let got: string
      if (r.footer) got = render(fnFooter(r.footer[0], r.footer[1], r.footer[2]))
      else if (r.withheld) got = render(withheldCard(r.withheld))
      else if (r.unassessed) got = render(unassessedPanel(r.unassessed, await bundle(r.ref), 'Function 3 of 20 · Hydrology'))
      else if (r.metricId) got = render(metricRow(metric(await bundle(r.ref), r.functionId, r.metricId), r.rc, r.midx, r.note))
      else got = render(scorecard(r.functionId, r.score))
      expect(got, `${r.ref} ${r.metricId ?? r.functionId ?? ''}`).toBe(r.html)
    }
  })

  it('preselects curve sets as auto_strata', { timeout: 120_000 }, async () => {
    for (const s of R.strata) {
      const b = await bundle(s.ref)
      if (!b) continue
      const d = { delineation: R.delineations[s.case] }
      expect(autoStrata(b, d), `${s.case} ${s.ref}`).toEqual(s.auto)
      for (const [mid, note] of Object.entries(s.notes)) {
        const m = b.metricsByFunction.flatMap((f: any) => f.metrics ?? []).find((x: any) => x.metricId === mid)
        expect(stratifierNote(m, d), `${s.case} ${s.ref} ${mid}`).toBe(note)
      }
    }
  })

  it('prefills desktop values as compute_metrics_only', { timeout: 120_000 }, async () => {
    const d = { src, hr: new Dataset(src, 'bundle/'), v2: new Dataset(src, 'bundle/v2/'), points: new PointTables(src) }
    for (const c of R.computed) {
      const b = await bundle(c.ref)
      if (!b) continue
      const got = await computeMetrics(d, c.ci, c.ids, b, c.record)
      expect(got, `${c.case} ${c.ref}`).toEqual(c.values)
    }
  })
})

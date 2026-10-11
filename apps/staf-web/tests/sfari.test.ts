// SFARI's desktop evidence against the Python's (sfari_ref.json, make_sfari_fixtures.py): the lookups
// from the local layout (skipped where it is not), then the 22 adapters over the same answers.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { Dataset } from '../src/engine/dataset'
import { PointTables } from '../src/engine/points'
import { ValueTables } from '../src/engine/values'
import { evaluate, prefetch, resolveAnchor } from '../src/tools/sfari/evidence'
import { likertToScore, scoreAssessment } from '../src/tools/sfari/method'
import ref from './fixtures/sfari_ref.json'
import { BASE, have, localFetch } from './local'

const cases = (ref as { cases: any[] }).cases

describe('SFARI evidence adapters', () => {
  it('give the Python entries over the same lookups', () => {
    for (const c of cases) {
      const anchor = resolveAnchor(c.ci)
      const got = evaluate(c.ci, c.state, anchor, c.prefetched)
      expect(Object.keys(got), c.name).toEqual(Object.keys(c.evidence))
      for (const mid of Object.keys(c.evidence)) expect(got[mid], `${c.name} ${mid}`).toEqual(c.evidence[mid])
    }
  })
})

describe.skipIf(!have)('SFARI evidence lookups', () => {
  beforeAll(() => vi.stubGlobal('fetch', localFetch))
  afterAll(() => vi.unstubAllGlobals())

  it('read what the Python reads from the bundle', { timeout: 120_000 }, async () => {
    const src = new DataSource(BASE)
    const d = { src, hr: new Dataset(src, 'bundle/'), v2: new Dataset(src, 'bundle/v2/'), values: new ValueTables(src), points: new PointTables(src) }
    for (const c of cases) {
      const got = await prefetch(d, c.ci, c.state, resolveAnchor(c.ci))
      expect(got, c.name).toEqual(c.prefetched)
    }
  })
})

describe('SFARI scoring', () => {
  it('rolls function scores up as scoring.score_assessment', () => {
    const none = scoreAssessment({})
    expect(none.ecosystemConditionIndex).toBe(0)
    const one = scoreAssessment({ 'catchment-hydrology': 11 })
    expect(one.subIndices.physical).toBeGreaterThan(0)
    expect(likertToScore(['Agree', 'Strongly Agree', 'Not Applicable', null])).toBe(12.5)
  })
})

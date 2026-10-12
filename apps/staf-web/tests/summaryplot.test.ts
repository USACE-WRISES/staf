// EASI's report summary plot laid out as matplotlib lays it out (summaryplot_ref.json,
// make_summaryplot_fixtures.py): both axes after tight_layout, the ticks and their labels, the titles, the
// axis labels, every text's box and every bar.
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { functions, loadMethod } from '../src/tools/easi/method'
import { layoutSummary } from '../src/tools/easi/summaryplot'
import ref from './fixtures/summaryplot_ref.json'
import { BASE, have, localFetch } from './local'

const R = ref as any
const close = (got: number[], want: number[], where: string) => {
  expect(got.length, where).toBe(want.length)
  got.forEach((v, i) => expect(Math.abs(v - want[i]), `${where} [${i}] ${v} vs ${want[i]}`).toBeLessThan(1e-6))
}

describe.skipIf(!have)('the report summary plot', () => {
  beforeAll(async () => {
    vi.stubGlobal('fetch', localFetch)
    await loadMethod(new DataSource(BASE))
  }, 120_000)
  afterAll(() => vi.unstubAllGlobals())

  it('lists the functions matplotlib drew', () => {
    expect(functions().map((f) => [f.id, f.name, f.category])).toEqual(R.functions)
  })

  it('is laid out as matplotlib lays it out', () => {
    R.renders.forEach((r: any, n: number) => {
      const got = layoutSummary(r.rep)
      expect(got.length).toBe(r.layout.length)
      got.forEach((g, k) => {
        const L = r.layout[k]
        const where = `render ${n} axes ${k}`
        close(g.xlim, L.xlim, `${where} xlim`)
        close(g.ylim, L.ylim, `${where} ylim`)
        expect(g.xticks.map((t) => [t[0], t[1]]), `${where} xticks`).toEqual(L.xticks.map((t: any) => [t[0], t[1]]))
        expect(g.yticks.map((t) => [t[0], t[1]]), `${where} yticks`).toEqual(L.yticks.map((t: any) => [t[0], t[1]]))
        close(g.axes, L.axes, `${where} axes`)
        g.xticks.forEach((t, i) => close(t[2], L.xticks[i][2], `${where} xtick ${t[1]}`))
        g.yticks.forEach((t, i) => close(t[2], L.yticks[i][2], `${where} ytick ${t[1]}`))
        close(g.title, L.title, `${where} title`)
        close(g.xlabel, L.xlabel, `${where} xlabel`)
        expect(g.texts.map((t) => t[0]), `${where} texts`).toEqual(L.texts.map((t: any) => t[0]))
        g.texts.forEach((t, i) => close(t[1], L.texts[i][1], `${where} text ${t[0]}`))
        expect(g.bars.length, `${where} bars`).toBe(L.bars.length)
        g.bars.forEach((b, i) => close(b, L.bars[i][4], `${where} bar ${i}`))
      })
    })
  })
})

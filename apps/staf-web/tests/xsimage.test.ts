// EASI's report cross-section image laid out as matplotlib lays it out (xsimage_ref.json,
// make_xsimage_fixtures.py): the axes box after tight_layout, the ticks and their labels, every text's box.
import { describe, expect, it } from 'vitest'
import { layoutFigure } from '../src/tools/easi/xsimage'
import ref from './fixtures/xsimage_ref.json'

const R = ref as any
const close = (got: number[], want: number[], where: string) => {
  expect(got.length, where).toBe(want.length)
  got.forEach((v, i) => expect(Math.abs(v - want[i]), `${where} [${i}] ${v} vs ${want[i]}`).toBeLessThan(1e-6))
}

describe('the report cross-section image', () => {
  it('is laid out as matplotlib lays it out', () => {
    R.renders.forEach((r: any, n: number) => {
      const where = `render ${n} (${r.unit})`
      const got = layoutFigure({ stations: r.stations, elevs: r.elevs, thalweg: r.thalweg, bankfull_stage: r.bankfull_stage,
        floodplain_stage: r.floodplain_stage, unit: r.unit, source: r.source })!
      const L = r.layout
      close(got.xlim, L.xlim, `${where} xlim`)
      close(got.ylim, L.ylim, `${where} ylim`)
      expect(got.xticks.map((t) => [t[0], t[1]]), `${where} xticks`).toEqual(L.xticks.map((t: any) => [t[0], t[1]]))
      expect(got.yticks.map((t) => [t[0], t[1]]), `${where} yticks`).toEqual(L.yticks.map((t: any) => [t[0], t[1]]))
      close(got.axes, L.axes, `${where} axes`)
      got.xticks.forEach((t, i) => close(t[2], L.xticks[i][2], `${where} xtick ${t[1]}`))
      got.yticks.forEach((t, i) => close(t[2], L.yticks[i][2], `${where} ytick ${t[1]}`))
      close(got.title, L.title, `${where} title`)
      close(got.xlabel, L.xlabel, `${where} xlabel`)
      close(got.ylabel, L.ylabel, `${where} ylabel`)
      expect(got.texts.map((t) => [t[0], t[1]]), `${where} texts`).toEqual(L.texts.map((t: any) => [t[0], t[1]]))
      got.texts.forEach((t, i) => close(t[2], L.texts[i][2], `${where} text ${t[0]}`))
      expect(got.figtexts.map((t) => t[0]), `${where} figtexts`).toEqual(L.figtexts.map((t: any) => t[0]))
      got.figtexts.forEach((t, i) => close(t[1], L.figtexts[i][1], `${where} figtext`))
    })
  })
})

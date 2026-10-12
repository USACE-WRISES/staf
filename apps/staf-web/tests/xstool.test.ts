// SFARI's cross-section hydraulics tool against sfari/xscalc.py and app.py (xstool_ref.json,
// make_xstool_fixtures.py): the synthetic section, the hydraulics at bankfull, the dialog, and for each
// set of box values the view (drawing and table) and the evidence Attach writes.
import { describe, expect, it } from 'vitest'
import { TagList } from '../src/app/htm'
import { compute, syntheticSection } from '../src/tools/sfari/xscalc'
import { attachEvidence, xsCalc, xsModal, type XsGeom, xsView } from '../src/tools/sfari/xstool'
import ref from './fixtures/xstool_ref.json'

const R = ref as any
const render = (t: any) => new TagList(t).render()

describe("SFARI's hydraulics tool", () => {
  it('builds the synthetic section and solves it as xscalc', () => {
    for (const c of R.cases) {
      const [pts, lb, rb, stage] = syntheticSection(c.width, c.depth)
      expect([pts, lb, rb, stage], `${c.width}`).toEqual([c.geom.points, c.geom.lb, c.geom.rb, c.geom.bankfull_stage])
      const bf = compute(pts, stage, c.geom.slope, 0.035, { nLob: 0.06, nRob: 0.06, lb, rb })!
      // the geometry exactly; what passes through r ** (2/3) to two ulps (V8's Math.pow and the C
      // runtime's pow, which Python calls, can differ in the last bit; what the dialog shows does not)
      for (const k of ['stage', 'A', 'P', 'T', 'R', 'tau', 'depth_max', 'depth_avg']) expect((bf as any)[k], `${c.width} ${k}`).toBe(c.bankfull[k])
      for (const k of ['Q', 'V', 'power', 'froude']) {
        const want = c.bankfull[k] as number
        expect(Math.abs((bf as any)[k] - want), `${c.width} ${k}`).toBeLessThanOrEqual(Math.abs(want) * 4.5e-16)
      }
    }
  })

  it('draws the dialog, the view and the evidence as SFARI', () => {
    for (const c of R.cases) {
      const geom = c.geom as XsGeom
      // the view's output as shiny.js binds it (polite to screen readers)
      expect(render(xsModal(geom, null)), `${c.width}`).toBe(c.modal.replace('id="sfari-xs_view"></div>', 'id="sfari-xs_view" aria-live="polite"></div>'))
      for (const v of c.views) {
        const res = xsCalc(geom, v.boxes)
        expect(res ? [res.stage, res.Q] : [null, null], `${c.width} ${JSON.stringify(v.boxes)}`).toEqual([v.stage, v.Q])
        expect(render(xsView(geom, v.boxes)), `${c.width} ${JSON.stringify(v.boxes)}`).toBe(v.html)
        expect(attachEvidence(geom, v.boxes, {}), `${c.width} ${JSON.stringify(v.boxes)}`).toEqual(v.evidence)
      }
    }
  })
})

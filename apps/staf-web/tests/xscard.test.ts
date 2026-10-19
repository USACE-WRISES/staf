// The report's cross-section block (xscard.xsReadonly): the shown section's image, or the Pull from 3DEP
// button for a section the release does not store, as the worksheet draws it again in place
// (#easi-report_xs); and the card's own pull text (pullContent).
import { describe, expect, it } from 'vitest'
import { outputHtml } from '../src/app/htm'
import { pullContent, type PullState, xsReadonly } from '../src/tools/easi/xscard'

type Obj = Record<string, any>

/** A report's cross-section showing one section: the grid's numbers, and its profile only with ``extra``. */
function report(extra: Obj): Obj {
  const geom = { division: 'Appalachian Plateau', bankfull_width_m: 4.2, flood_prone_width_m: 11.0, entrenchment_ratio: 2.62,
    bank_height_ratio: 1.08, low_bank_capped: false, stations: null, elevs: null, thalweg: null,
    xs: { vpu: '0108', id: 5000100000123, part: 0, k: 4, flags: 0, res: 1, bfM: 0.41, lbM: 0.47, stored: false }, ...extra }
  return { crossSection: { geom, entrenchment_ratio: geom.entrenchment_ratio, bank_height_ratio: geom.bank_height_ratio, reach: null,
    caption: '3DEP cross-section nearest the reach medians.' } }
}
const PROFILE = { stations: [0, 5, 10], elevs: [101, 100, 101], thalweg: 100 }
const IDLE: PullState = { pulling: false, error: null }
const html = (rep: Obj, png: string | null, pull: PullState = IDLE) => outputHtml(xsReadonly(rep, png, pull))

describe("the report's cross-section", () => {
  it('draws the image of a section with its profile, with no button', () => {
    const out = html(report(PROFILE), 'data:image/png;base64,AAAA')
    expect(out).toContain('id="easi-report_xs"')
    expect(out).toContain('<img src="data:image/png;base64,AAAA"')
    expect(out).not.toContain('data-xs-pull')
  })

  it('offers Pull from 3DEP for a section the release does not store', () => {
    const out = html(report({}), null)
    expect(out).toContain('id="easi-report_xs"')
    expect(out).toContain('elevation profile from USGS 3DEP to draw it.</p>')
    expect(out).toMatch(/<button[^>]*data-xs-pull="1"[^>]*>Pull from 3DEP<\/button>/)
    expect(out).not.toContain('Assessment step')
  })

  it('shows a pull going on, its button disabled', () => {
    const out = html(report({}), null, { pulling: true, error: null })
    expect(out).toMatch(/<button[^>]*disabled="disabled"[^>]*>Pulling from 3DEP…<\/button>/)
  })

  it("shows the last pull's error under the button", () => {
    const out = html(report({}), null, { pulling: false, error: 'The 3DEP pull did not work. Try again.' })
    expect(out).toContain('The 3DEP pull did not work. Try again.')
    expect(out).toMatch(/<button[^>]*>Pull from 3DEP<\/button>/)
  })

  it('has a plain note for a section it cannot pull', () => {
    const out = html(report({ xs: undefined }), null)
    expect(out).toContain('No elevation profile is available for this section.')
    expect(out).not.toContain('data-xs-pull')
  })

  it('says so when a section with its profile could not be drawn', () => {
    const out = html(report(PROFILE), null)
    expect(out).toContain('The cross-section could not be drawn.')
    expect(out).not.toContain('data-xs-pull')
  })

  it('is nothing without a cross-section', () => {
    expect(outputHtml(xsReadonly({}, null))).toBe('')
  })
})

describe("the card's pull", () => {
  it('keeps its own text', () => {
    const out = outputHtml(pullContent(false, null))
    expect(out).toContain('elevation profile from USGS 3DEP to draw it and edit its heights.</p>')
    expect(out).toMatch(/<button[^>]*data-xs-pull="1"[^>]*>Pull from 3DEP<\/button>/)
  })
})

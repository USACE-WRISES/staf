// DEEP's Change dialog against app.py's _assess_cards_ui (change_ref.json, make_change_fixtures.py): the
// cards, their badges, tiers and version choosers, the selected card, and the dialog with none.
import { describe, expect, it } from 'vitest'
import { TagList } from '../src/app/htm'
import { changeDialog } from '../src/app/deepChange'
import ref from './fixtures/change_ref.json'

const render = (t: any) => new TagList(t).render()

describe("DEEP's Change dialog", () => {
  it('draws the cards as DEEP', () => {
    for (const d of (ref as any).dialogs) {
      expect(render(changeDialog(d.covering, d.selected)), `${d.name} ${d.selected}`).toBe(d.html)
    }
  })
})

// The scenario controls against libs/staf_workbook's own (scenarios_ref.json, make_scenario_fixtures.py):
// the chip, the dialogs (py-shiny's modal with its show script), the comparisons, the name rules.
import { describe, expect, it } from 'vitest'
import { TagList } from '../src/app/htm'
import {
  addDialog, compareDialog, deleteDialog, deltaSign, excelRound, fmtDelta, fmtMeasure, railDelta, ScenarioSet, scenarioBar, scenarioDialog,
  sheetNameProblem,
} from '../src/shared/scenarios'
import { sfariComparison } from '../src/tools/sfari/compare'
import ref from './fixtures/scenarios_ref.json'

const R = ref as any
const render = (t: any) => new TagList(t).render()

describe('scenarios', () => {
  it('draw the chip as web.scenario_bar', () => {
    for (const b of R.bars) expect(render(scenarioBar(ScenarioSet.fromJson(b.set))), b.name).toBe(b.html)
  })

  it('draw the dialogs as web.py', () => {
    const d = Object.fromEntries(R.dialogs.map((x: any) => [x.name, x]))
    const two = ScenarioSet.fromJson(d.add.set)
    expect(render(addDialog('sfari', two, d.add.args))).toBe(d.add.html)
    expect(render(addDialog('sfari', two, d['add-error'].args))).toBe(d['add-error'].html)
    for (const k of ['edit-baseline', 'edit-alt']) {
      const a = d[k].args
      expect(render(scenarioDialog('sfari', { title: a.title, name: a.name, description: a.description, nameLocked: a.name_locked, canDelete: a.can_delete })), k)
        .toBe(d[k].html)
    }
    expect(render(deleteDialog('sfari', d.delete.args.name))).toBe(d.delete.html)
  })

  it('compare scenarios as SFARI does', () => {
    for (const c of R.comparisons) {
      const cmp = sfariComparison(c.names, c.functionScores)
      expect(cmp.rows.map((r) => r.key)).toEqual(c.rows.map((r: any) => r.key))
      cmp.rows.forEach((r, i) => {
        const want = c.rows[i]
        expect([r.label, r.group, r.digits], want.key).toEqual([want.label, want.group, want.digits])
        expect(r.values.map((v) => fmtMeasure(v, r.digits)), want.key).toEqual(want.values)
        expect(r.deltas, want.key).toEqual(want.deltas)
        expect(r.deltas.map((x) => fmtDelta(x, r.digits)), want.key).toEqual(want.deltaText)
        expect(r.deltas.map(deltaSign), want.key).toEqual(want.signs)
      })
      expect(render(compareDialog(cmp))).toBe(c.dialog)
      const rail = railDelta(cmp, 1)
      expect(rail ? render(rail) : null).toBe(c.railDelta)
    }
  })

  it('check names and round as the Python', () => {
    for (const n of R.names) expect(sheetNameProblem(n.name, n.taken), JSON.stringify(n.name)).toBe(n.problem)
    for (const [x, d, want] of R.excelRound) expect(excelRound(x, d), `${x} ${d}`).toBe(want)
  })

  it('add, rename, describe and delete as ScenarioSet', () => {
    const s = new ScenarioSet()
    expect(s.defaultName()).toBe('Alternative 1')
    const a = s.add(null, 'x', { state: { function_scores: {} } })
    expect([a.id, a.name, s.active]).toEqual(['s2', 'Alternative 1', 's2'])
    expect(() => s.add('summary')).toThrow('That name is already used.')
    s.rename('s2', ' Restored ')
    expect(s.get('s2').name).toBe('Restored')
    s.describe('existing', '  As built. ')
    expect(s.baseline.description).toBe('As built.')
    s.delete('s2')
    expect([s.active, s.items.length]).toEqual(['existing', 1])
    const back = ScenarioSet.fromJson({ items: [{ id: 's7', name: 'Seven' }], active: 's7', next: 3 })
    expect([back.active, back.nextNumber, back.items.length]).toEqual(['s7', 8, 2])
  })
})

// Scenarios of one assessment (libs/staf_workbook model/scenarios.py, model/compare.py, xlsx/names.py
// and web.py's scenario controls), the same in EASI, SFARI and DEEP: "Existing Conditions" first,
// then the alternatives a user adds; the chip on the score rail, its dialogs and the comparison.
import { type Child, h, HTML, type Tag } from '../app/htm'
import { pyFixed, pySigned } from '../app/pyfmt'
import { pyClone } from '../app/pyjson'
import { actionButton, inputSelect, inputText, inputTextArea, modal, modalButton } from './shinyui'

export const BASELINE_ID = 'existing'
export const BASELINE_NAME = 'Existing Conditions'
export const MAX_SCENARIOS = 10
export const MAX_DESCRIPTION = 500
export const RESERVED_NAMES = ['Summary', 'ReferenceCurves']
/** web.py's input ids (each tool's prefix in front) and the Add dialog's Blank choice. */
export const NAME_INPUT = 'staf_sc_name'
export const DESC_INPUT = 'staf_sc_desc'
export const SAVE_BUTTON = 'staf_sc_save'
export const DELETE_BUTTON = 'staf_sc_delete'
export const START_INPUT = 'staf_sc_from'
export const START_BLANK = 'blank'

// ------------------------------------------------------------------ xlsx/names.py
const MAX_LEN = 31
const FORBIDDEN = new Set(['[', ']', ':', '*', '?', '/', '\\'])
const RESERVED = ['history']

const pyStrip = (s: string) => s.replace(/^\s+|\s+$/g, '')

/** sheet_name_problem: why ``name`` cannot be a sheet name, or null when it can. */
export function sheetNameProblem(name: string, taken: string[] = []): string | null {
  if (!name || !pyStrip(name)) return 'Enter a name.'
  if ([...name].length > MAX_LEN) return `Use ${MAX_LEN} characters or fewer.`
  const bad = [...new Set([...name].filter((c) => FORBIDDEN.has(c)))].sort()
  if (bad.length) return 'These characters are not allowed: ' + bad.join(' ')
  if (name.startsWith("'") || name.endsWith("'")) return 'A name cannot start or end with an apostrophe.'
  if (RESERVED.includes(pyStrip(name).toLowerCase())) return 'That name is reserved by Excel.'
  if (taken.some((t) => t.toLowerCase() === pyStrip(name).toLowerCase())) return 'That name is already used.'
  return null
}

// ------------------------------------------------------------------ model/scenarios.py
export interface Scenario { id: string, name: string, description: string, state: any }

const clone = <T>(x: T): T => pyClone(x)

export class ScenarioSet {
  items: Scenario[]
  active: string
  nextNumber: number

  constructor(items?: Scenario[], active = BASELINE_ID, nextNumber = 2) {
    this.items = items && items.length ? items : [{ id: BASELINE_ID, name: BASELINE_NAME, description: '', state: null }]
    this.active = this.items.some((s) => s.id === active) ? active : BASELINE_ID
    this.nextNumber = Math.max(nextNumber, 2)
  }

  get baseline(): Scenario { return this.items[0] }
  get current(): Scenario { return this.get(this.active) }
  get alternatives(): Scenario[] { return this.items.slice(1) }
  static isBaseline(s: Scenario): boolean { return s.id === BASELINE_ID }

  get(sid: string): Scenario {
    const s = this.items.find((x) => x.id === sid)
    if (!s) throw new Error(sid)
    return s
  }

  hasAlternatives(): boolean { return this.items.length > 1 }
  canAdd(): boolean { return this.items.length < MAX_SCENARIOS }

  nameProblem(name: string, sid: string | null = null): string | null {
    const taken = [...this.items.filter((s) => s.id !== sid).map((s) => s.name), ...RESERVED_NAMES]
    return sheetNameProblem(name ? pyStrip(name) : name, taken)
  }

  defaultName(): string {
    const used = new Set(this.items.map((s) => s.name.toLowerCase()))
    let n = 1
    while (used.has(`alternative ${n}`)) n += 1
    return `Alternative ${n}`
  }

  /** add: a copy of ``copyFrom`` (default the one shown), or ``state`` when given (an app's blank). */
  add(name: string | null, description = '', { copyFrom = null as string | null, state = undefined as any } = {}): Scenario {
    if (!this.canAdd()) throw new Error(`At most ${MAX_SCENARIOS} scenarios.`)
    const n = pyStrip(name || this.defaultName())
    const problem = this.nameProblem(n)
    if (problem) throw new Error(problem)
    const start = state === undefined ? this.get(copyFrom || this.active).state : state
    const sid = `s${this.nextNumber}`
    this.nextNumber += 1
    const created: Scenario = { id: sid, name: n, description: [...(description || '')].slice(0, MAX_DESCRIPTION).join(''), state: clone(start) }
    this.items.push(created)
    this.active = sid
    return created
  }

  rename(sid: string, name: string): void {
    const s = this.get(sid)
    if (ScenarioSet.isBaseline(s)) throw new Error('Existing Conditions keeps its name.')
    const n = pyStrip(name || '')
    const problem = this.nameProblem(n, sid)
    if (problem) throw new Error(problem)
    s.name = n
  }

  describe(sid: string, text: string): void {
    this.get(sid).description = [...pyStrip(text || '')].slice(0, MAX_DESCRIPTION).join('')
  }

  delete(sid: string): void {
    const s = this.get(sid)
    if (ScenarioSet.isBaseline(s)) throw new Error('Existing Conditions cannot be deleted.')
    this.items = this.items.filter((x) => x !== s)
    if (this.active === sid) this.active = BASELINE_ID
  }

  select(sid: string): void {
    this.get(sid)
    this.active = sid
  }

  setState(sid: string, state: any): void {
    this.get(sid).state = state
  }

  /** to_json: the ``scenarios`` key of an assessment file (the baseline's state left out by default). */
  toJson(includeBaselineState = false): Record<string, any> {
    return {
      version: 1, active: this.active, next: this.nextNumber,
      items: this.items.map((s) => ({
        id: s.id, name: s.name, description: s.description,
        ...(ScenarioSet.isBaseline(s) && !includeBaselineState ? {} : { state: s.state }),
      })),
    }
  }

  /** from_json: anything malformed falls back to Existing Conditions alone. */
  static fromJson(data: any, baselineState: any = null): ScenarioSet {
    const base: Scenario = { id: BASELINE_ID, name: BASELINE_NAME, description: '', state: baselineState }
    if (!data || typeof data !== 'object' || !Array.isArray(data.items)) return new ScenarioSet([base])
    const items = [base]
    const seen = new Set([BASELINE_ID])
    for (const raw of data.items) {
      if (!raw || typeof raw !== 'object') continue
      const sid = String(raw.id || '')
      if (sid === BASELINE_ID) {
        base.description = [...String(raw.description || '')].slice(0, MAX_DESCRIPTION).join('')
        continue
      }
      const name = pyStrip(String(raw.name || ''))
      if (!sid || seen.has(sid) || items.length >= MAX_SCENARIOS) continue
      if (sheetNameProblem(name, [...items.map((s) => s.name), ...RESERVED_NAMES])) continue
      seen.add(sid)
      items.push({ id: sid, name, description: [...String(raw.description || '')].slice(0, MAX_DESCRIPTION).join(''), state: raw.state })
    }
    const nums = items.slice(1).map((s) => s.id.slice(1)).filter((d) => /^\d+$/.test(d)).map(Number)
    const next = Number.parseInt(String(data.next ?? 2), 10)
    return new ScenarioSet(items, String(data.active || BASELINE_ID), Math.max(Number.isNaN(next) ? 2 : next, ...nums.map((n) => n + 1)))
  }
}

// ------------------------------------------------------------------ model/compare.py
export interface Measure { value?: number | null, low?: number | null, high?: number | null }
const isInterval = (m: Measure) => (m.value === null || m.value === undefined) && m.low != null && m.high != null
const isEmpty = (m: Measure) => (m.value === null || m.value === undefined) && !isInterval(m)

export interface ScenarioScores { eci: Measure, sub: Record<string, Measure>, functions: Record<string, Measure> }
export type Delta = [number, number] | null
export interface Row { key: string, label: string, group: string, digits: number, values: Measure[], deltas: Delta[] }
export interface Comparison { names: string[], rows: Row[] }

export const SUB_INDICES: [string, string][] = [['physical', 'Physical'], ['chemical', 'Chemical'], ['biological', 'Biological']]

/** excel_round: Excel's ROUND (half away from zero) on the number's shortest decimal form
 * (Decimal(repr(x)).quantize(..., ROUND_HALF_UP)). */
export function excelRound(x: number, digits: number): number {
  const m = /^(-?)(\d+)(?:\.(\d+))?(?:e([+-]\d+))?$/.exec(String(x))
  if (!m) return x                              // NaN and the infinities
  const [, sign, int, frac = '', e = '0'] = m
  const k = -(Number(e) - frac.length + digits)  // |x| = digits x 10^(e - frac): k digits to drop
  if (k <= 0) return x
  const D = BigInt(int + frac)
  const p = 10n ** BigInt(k)
  let q = D / p
  if ((D % p) * 2n >= p) q += 1n
  return Number(`${sign}${q}e-${digits}`)
}

const pyRound = (x: number, n: number) => Number(pyFixed(x, n))

function delta(a: Measure, b: Measure, digits: number): Delta {
  if (isEmpty(a) || isEmpty(b)) return null
  const loA = excelRound((isInterval(a) ? a.low : a.value) as number, digits)
  const hiA = excelRound((isInterval(a) ? a.high : a.value) as number, digits)
  const loB = excelRound((isInterval(b) ? b.low : b.value) as number, digits)
  const hiB = excelRound((isInterval(b) ? b.high : b.value) as number, digits)
  if (isInterval(a) || isInterval(b)) return [pyRound(loB - hiA, digits), pyRound(hiB - loA, digits)]
  const d = pyRound(loB - loA, digits)
  return [d, d]
}

/** compare.build: ``functions`` = [(id, label, category)] in display order. */
export function buildComparison(names: string[], scores: ScenarioScores[], functions: [string, string, string][],
                                indexDigits = 2, functionDigits = 0): Comparison {
  const rows: Row[] = [{ key: 'eci', label: 'ECI', group: 'Index', digits: indexDigits, values: scores.map((s) => s.eci), deltas: [] }]
  for (const [key, label] of SUB_INDICES) rows.push({ key, label, group: 'Index', digits: indexDigits, values: scores.map((s) => s.sub[key] ?? {}), deltas: [] })
  for (const [fid, label, category] of functions) {
    rows.push({ key: `fn:${fid}`, label, group: category || '', digits: functionDigits, values: scores.map((s) => s.functions[fid] ?? {}), deltas: [] })
  }
  for (const row of rows) row.deltas = row.values.slice(1).map((v) => delta(row.values[0], v, row.digits))
  return { names: [...names], rows }
}

export function fmtMeasure(m: Measure, digits: number): string {
  if (isEmpty(m)) return ''
  if (isInterval(m)) return `${pyFixed(excelRound(m.low as number, digits), digits)} to ${pyFixed(excelRound(m.high as number, digits), digits)}`
  return pyFixed(excelRound(m.value as number, digits), digits)
}

export function fmtDelta(d: Delta, digits: number): string {
  if (d === null) return ''
  const [lo, hi] = d
  const one = (v: number) => (Math.abs(v) < 0.5 * 10 ** -digits ? (0).toFixed(digits) : pySigned(v, digits))
  return lo === hi ? one(lo) : `${one(lo)} to ${one(hi)}`
}

/** +1 better, -1 worse, 0 unchanged or mixed. */
export function deltaSign(d: Delta): number {
  if (d === null) return 0
  const [lo, hi] = d
  if (lo > 0 && hi > 0) return 1
  if (lo < 0 && hi < 0) return -1
  return 0
}

// ------------------------------------------------------------------ web.py
const ICONS: Record<string, string> = {
  new: '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
    + '<path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  edit: '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
    + '<path d="M11.2 2.6l2.2 2.2-7.7 7.7-2.9.7.7-2.9z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
  compare: '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">'
    + '<rect x="2" y="3" width="5" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>'
    + '<rect x="9" y="3" width="5" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
}

function scenarioLink(action: string, label: string, title: string | null, disabled = false): Tag {
  return h.button(new HTML(ICONS[action]), h.span(label), { type: 'button', 'data-sc-action': action },
    { class: 'staf-scen-link', title, disabled: disabled ? true : null })
}

/** scenario_bar: the chip that switches scenarios, then Add, Edit and (with an alternative) Compare. */
export function scenarioBar(sset: ScenarioSet): Tag {
  const cur = sset.current
  const items = sset.items.map((s) => {
    const parts: Child[] = [h.span(s.name, { class: 'staf-scen-item-name' })]
    if (s.description) parts.push(h.span(s.description, { class: 'staf-scen-item-desc' }))
    return h.button(...parts, { type: 'button', 'data-sc-action': 'select', 'data-sc-id': s.id, role: 'menuitemradio', 'aria-checked': s.id === cur.id ? 'true' : 'false' },
      { class: 'staf-scen-item' + (s.id === cur.id ? ' active' : ''), title: s.description || null })
  })
  const summary = h.summary(h.span('Scenario', { class: 'staf-scen-lab' }), h.span(cur.name, { class: 'staf-scen-name' }),
    { class: 'staf-scen-sum', title: cur.description || null })
  const chip = h.details(summary, h.div(...items, { class: 'staf-scen-menu', role: 'menu' }),
    { class: 'staf-scen-chip' + (ScenarioSet.isBaseline(cur) ? '' : ' alt') })
  const links = [
    scenarioLink('new', 'Add', sset.canAdd() ? 'Add a scenario' : 'Up to 10 scenarios', !sset.canAdd()),
    scenarioLink('edit', 'Edit', ScenarioSet.isBaseline(cur) ? 'Describe Existing Conditions' : 'Rename or describe'),
  ]
  if (sset.hasAlternatives()) links.push(scenarioLink('compare', 'Compare', 'Compare scenarios'))
  return h.div(chip, h.div(...links, { class: 'staf-scen-actions' }), { class: 'staf-scen' + (sset.hasAlternatives() ? '' : ' solo') })
}

/** scenario_dialog: name and description; ``canDelete`` adds Delete scenario, ``start`` the Add
 * dialog's Start from. */
export function scenarioDialog(ns: string, { title, name, description, nameLocked, error = null as string | null, canDelete = false,
  start = null as [[string, string][], string, string] | null, saveLabel = 'Save' }: {
  title: string, name: string, description: string, nameLocked: boolean, error?: string | null, canDelete?: boolean,
  start?: [[string, string][], string, string] | null, saveLabel?: string,
}): Tag {
  const nameField = nameLocked
    ? h.div(h.label('Name', { class: 'control-label' }), h.div(name, { class: 'staf-scen-fixed' }), { class: 'form-group' })
    : inputText(`${ns}-${NAME_INPUT}`, 'Name', name, '100%')
  let startField: Tag | null = null
  if (start) {
    const [choices, selected, hint] = start
    startField = h.div(inputSelect(`${ns}-${START_INPUT}`, 'Start from', choices, selected, '100%'),
      hint ? h.div(hint, { class: 'staf-scen-hint' }) : null, { class: 'staf-scen-start' })
  }
  const footer: Tag[] = [modalButton('Cancel', 'sfari-btn staf-btn'), actionButton(`${ns}-${SAVE_BUTTON}`, saveLabel, 'sfari-btn primary staf-btn')]
  if (canDelete) footer.unshift(h.button('Delete scenario', { type: 'button', 'data-sc-action': 'delete' }, { class: 'btn sfari-btn staf-btn danger me-auto' }))
  return modal(
    h.div(nameField, startField,
      inputTextArea(`${ns}-${DESC_INPUT}`, 'Description', description, { rows: 3, width: '100%', placeholder: 'Optional' }),
      error ? h.div(error, { class: 'staf-scen-err' }) : null,
      { class: 'staf-scen-form' }),
    { title, easyClose: true, size: 'm', footer, bodyClass: 'staf-dialog' })
}

/** add_dialog: its name, where it starts (every scenario, then Blank) and a description. */
export function addDialog(ns: string, sset: ScenarioSet, { name, description = '', start = null as string | null, hint = '', error = null as string | null }: {
  name: string, description?: string, start?: string | null, hint?: string, error?: string | null,
}): Tag {
  const choices: [string, string][] = [...sset.items.map((s) => [s.id, s.name] as [string, string]), [START_BLANK, 'Blank']]
  const selected = start && choices.some(([v]) => v === start) ? start : sset.baseline.id
  return scenarioDialog(ns, { title: 'Add a scenario', name, description, nameLocked: false, error, start: [choices, selected, hint], saveLabel: 'Add' })
}

export function deleteDialog(ns: string, name: string): Tag {
  return modal(h.p(`Delete "${name}"? Its entries and scores are removed. Existing Conditions is not affected.`),
    { title: 'Delete scenario', easyClose: true, size: 'm', bodyClass: 'staf-dialog',
      footer: [modalButton('Cancel', 'sfari-btn staf-btn'), actionButton(`${ns}-${DELETE_BUTTON}`, 'Delete', 'sfari-btn staf-btn danger-solid')] })
}

/** comparison_table: the scenarios side by side, each alternative followed by its change. */
export function comparisonTable(cmp: Comparison): Tag {
  const head: Tag[] = [h.th('', { class: 'staf-cmp-label' }), h.th(cmp.names[0], { class: 'staf-cmp-col base' })]
  for (const name of cmp.names.slice(1)) head.push(h.th(name, { class: 'staf-cmp-col' }), h.th('Change', { class: 'staf-cmp-col delta' }))
  const body: Tag[] = []
  let group: string | null = null
  const span = 2 + 2 * (cmp.names.length - 1)
  for (const row of cmp.rows) {
    if (row.group !== group) {
      group = row.group
      body.push(h.tr(h.td(group || 'Functions', { colspan: String(span) }), { class: 'staf-cmp-group' }))
    }
    const cells: Tag[] = [h.td(row.label, { class: 'staf-cmp-label' }), h.td(fmtMeasure(row.values[0], row.digits), { class: 'staf-cmp-num base' })]
    row.values.slice(1).forEach((v, i) => {
      const d = row.deltas[i]
      const sign = deltaSign(d)
      cells.push(h.td(fmtMeasure(v, row.digits), { class: 'staf-cmp-num' }),
        h.td(fmtDelta(d, row.digits), { class: 'staf-cmp-num delta' + (sign > 0 ? ' up' : sign < 0 ? ' down' : '') }))
    })
    body.push(h.tr(...cells, { class: 'staf-cmp-row' + (row.key === 'eci' ? ' headline' : '') }))
  }
  return h.div(h.table(h.thead(h.tr(...head)), h.tbody(...body), { class: 'staf-cmp' }), { class: 'staf-cmp-wrap' })
}

export function compareDialog(cmp: Comparison): Tag {
  return modal(comparisonTable(cmp), { title: 'Compare scenarios', easyClose: true, size: 'xl', bodyClass: 'staf-dialog',
    footer: modalButton('Close', 'sfari-btn staf-btn') })
}

/** rail_delta: under the chip while an alternative is shown, its ECI against Existing Conditions. */
export function railDelta(cmp: Comparison | null, index: number): Tag | null {
  if (!cmp || index < 1 || !cmp.rows.length) return null
  const d = index - 1 < cmp.rows[0].deltas.length ? cmp.rows[0].deltas[index - 1] : null
  if (d === null) return h.div('vs Existing Conditions: no ECI yet', { class: 'staf-scen-delta' })
  const sign = deltaSign(d)
  return h.div('vs Existing Conditions: ECI ', h.span(fmtDelta(d, cmp.rows[0].digits), { class: sign > 0 ? 'up' : sign < 0 ? 'down' : '' }),
    { class: 'staf-scen-delta' })
}

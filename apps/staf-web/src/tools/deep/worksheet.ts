// DEEP's Assessment step: the measure worksheet over the map, its desktop prefill, its scenarios and
// the live rollup, as deep's app.py server keeps them.
//
// DEEP's own page script (measure.js, unchanged) updates a row's index, its curve marker and the
// function's score card as a value is typed, and posts every edit as an input. The server side here
// keeps the measured values, redraws the function list and the rail on every change (they read the
// scores), and redraws the function's page only when Shiny would: a move, a curve-set choice, the
// desktop prefill landing or a scenario switch.
import { type Child, h, HTML, outputHtml } from '../../app/htm'
import { notify, removeNotification } from '../../app/notify'
import { timed } from '../../app/perf'
import { removeModal, showModal } from '../../shared/modal'
import {
  addDialog, BASELINE_ID, buildComparison, compareDialog, type Comparison, DESC_INPUT, deleteDialog, type Measure, NAME_INPUT, railDelta,
  type Scenario, scenarioBar, scenarioDialog, type ScenarioScores, ScenarioSet, START_BLANK, START_INPUT,
} from '../../shared/scenarios'
import { COMPUTABLE, computeMetrics, type PrefillData } from './computed'
import {
  fnFooter, fnHead, fnNav, isEntered, metricRow, output, rollupRail, scorecard, secLbl, stepper, unassessedPanel, withheldCard,
} from './markup'
import {
  autoStrata, type Bundle, coverageOf, FUNCTIONS, type FunctionResult, measuredFromState, type Rec, scoreAssessment, scoreSite,
  type Scored, stratifierNote, unassessedFunctions, withheldForFunction,
} from './method'

export interface DeepHost {
  data: PrefillData
  step(): string
  delin(): Rec | null
  engineState(): Rec | null
  engineLine(): string
  lookupStatus(): string
  sourceReady(): boolean
  /** the assessment Basin adopted (its bundle) and its ref */
  loaded(): Bundle | null
  ref(): string | null
  setStep(step: string): void
}

const ADD_HINT = 'A copy brings its values and notes; photos stay with the original. Blank keeps only the desktop values.'
const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x ?? {}))
const strip = (s: string) => s.replace(/^\s+|\s+$/g, '')

function withoutPhotos(measured: Record<string, Rec>): Record<string, Rec> {
  const out = copy(measured)
  for (const rec of Object.values(out)) if (rec && typeof rec === 'object') delete rec.photos
  return out
}

/** workbook.functions: the scored functions in framework order. */
function bookFunctions(bundle: Bundle | null): [string, string, string][] {
  const blocks = new Map<string, Rec>((bundle?.metricsByFunction ?? []).map((fn: Rec) => [fn.functionId, fn]))
  const out: [string, string, string][] = []
  for (const f of [...FUNCTIONS].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
    const fn = blocks.get(f.id)
    if (fn) {
      out.push([f.id, fn.functionName || f.name || f.id, f.category ?? ''])
      blocks.delete(f.id)
    }
  }
  for (const [fid, fn] of blocks) out.push([fid, fn.functionName || fid, fn.discipline ?? ''])
  return out
}

/** workbook.claim_scores */
function claimScores(sc: Scored): ScenarioScores {
  const fs = Object.entries(sc.functionScores ?? {}).filter(([, s]) => s !== null && s !== undefined) as [string, number][]
  if (!fs.length) return { eci: {}, sub: {}, functions: {} }
  const claim = (bounds: number[] | null, value: number | null): Measure => {
    if (value !== null && value !== undefined) return { value: Number(value) }
    if (!bounds) return {}
    const [low, high] = [Number(bounds[0]), Number(bounds[1])]
    return Math.abs(high - low) < 1e-12 ? { value: low } : { low, high }
  }
  return {
    eci: claim(sc.ecosystemConditionIndexBounds, sc.ecosystemConditionIndexRaw),
    sub: Object.fromEntries(Object.entries(sc.subIndicesRaw ?? {}).map(([k, v]) => [k, v === null ? {} : { value: Number(v) }])),
    functions: Object.fromEntries(fs.map(([fid, s]) => [fid, { value: Number(s) }])),
  }
}

/** workbook.comparison: DEEP's scenarios side by side, the index to 2 places and the scores to 1. */
export function deepComparison(bundle: Bundle | null, names: string[], results: Scored[]): Comparison {
  return buildComparison(names, results.map(claimScores), bookFunctions(bundle), 2, 1)
}

export class DeepWorksheet {
  measured: Record<string, Rec> = {}
  currentFn = 0
  scenarios = new ScenarioSet()
  private dialog: 'new' | 'edit' | null = null
  private computedFor: string | null = null
  private computeGen = 0
  /** the desktop values computed ahead for a site and assessment version (computeAhead) */
  private ahead: { key: string, p: Promise<Record<string, Rec> | null> } | null = null
  private desktopFill: [string, Record<string, Rec>] | null = null

  constructor(private host: DeepHost) {}

  private el(id: string): HTMLElement | null {
    return document.getElementById(`deep-${id}`)
  }

  private shown(): boolean {
    const s = this.host.step()
    return s === 'measure' || s === 'report'
  }

  /** A new pick: the values, the scenarios and the prefill go (_begin_pick). */
  reset(): void {
    this.measured = {}
    this.scenarios = new ScenarioSet()
    this.computedFor = null
    this.computeGen += 1
    this.currentFn = 0
    removeNotification('deep_compute')
  }

  /** An assessment adopted or changed (_load_ref_into_state): its values start empty. */
  assessmentLoaded(): void {
    this.measured = {}
    this.scenarios = new ScenarioSet()
    this.currentFn = 0
    this.seedAutoStrata()
  }

  // ---------------------------------------------------------------- scoring
  private scored(): [Scored, Record<string, FunctionResult>] {
    const la = this.host.loaded()
    if (!la) return [scoreAssessment({}), {}]
    return scoreSite(la, measuredFromState(this.measured))
  }

  private score(measured: Record<string, Rec>): Scored {
    const la = this.host.loaded()
    if (!la) return scoreAssessment({})
    return scoreSite(la, measuredFromState(measured ?? {}))[0]
  }

  /** _walk_fns: every framework function in order, an unassessed one as its own step. */
  private walk(): Rec[] {
    const la = this.host.loaded()
    if (!la) return []
    const scoredBlocks = new Map<string, Rec>((la.metricsByFunction ?? []).map((fn: Rec) => [fn.functionId, fn]))
    const gaps = new Map(unassessedFunctions(la).map((f) => [f.functionId, f]))
    const blocks: Rec[] = []
    for (const f of FUNCTIONS) {
      if (scoredBlocks.has(f.id)) blocks.push(scoredBlocks.get(f.id)!)
      else if (gaps.has(f.id)) {
        const g = gaps.get(f.id)!
        blocks.push({ functionId: f.id, functionName: g.functionName, discipline: g.discipline ?? '', metrics: [], unassessed: g })
      }
    }
    const known = new Set(blocks.map((b) => b.functionId))
    blocks.push(...(la.metricsByFunction ?? []).filter((fn: Rec) => !known.has(fn.functionId)))
    return blocks
  }

  // ---------------------------------------------------------------- outputs
  render(): void {
    const slot = this.el('worksheet')
    if (!slot) return
    if (!this.shown()) {
      if (slot.innerHTML) slot.innerHTML = ''
      return
    }
    const ws = h.div(
      h.div(
        h.div('DEEP · Assessment', { class: 'easi-pane-head' }),
        h.div(stepper(this.host.step()), { class: 'sfari-nav-steps' }),
        h.button('Get Forms', { 'data-field-forms': '1', type: 'button',
          title: 'The metrics of this assessment, the field worksheet to print, and the Excel calculator' }, { class: 'sfari-btn sfari-nav-desktop' }),
        output('engine_line_ws', this.engineLineWs(), outputHtml),
        output('streamcat_lookup_status_ws', this.lookupWs(), outputHtml),
        output('fn_nav', this.nav(), outputHtml),
        { class: 'sfari-nav' }),
      h.div(output('fn_panel', this.panel(), outputHtml), { class: 'sfari-fnpanel' }),
      h.div(output('scenario_bar', this.scenarioBar(), outputHtml), output('rollup_rail', this.rail(), outputHtml), { class: 'sfari-rollup' }),
      { class: 'sfari-worksheet' })
    slot.innerHTML = outputHtml(ws)
  }

  private setOutput(id: string, content: Child): void {
    const el = this.el(id)
    if (el) el.innerHTML = outputHtml(content)
  }

  private engineLineWs(): Child {
    const html = this.host.engineLine().trim()
    return html ? new HTML(html) : null
  }

  private lookupWs(): Child {
    const html = this.host.lookupStatus().trim()
    return html ? new HTML(html.split('deep-retry_streamcat"').join('deep-retry_streamcat_ws"')) : null
  }

  private nav(): Child {
    const [, results] = this.scored()
    return fnNav(this.walk(), this.currentFn, results)
  }

  /** fn_panel */
  private panel(): Child {
    const fns = this.walk()
    if (!fns.length) return h.div('No assessment loaded.', { class: 'sfari-nav-empty' })
    const idx = Math.max(0, Math.min(fns.length - 1, this.currentFn))
    const fn = fns[idx]
    const fid = fn.functionId
    const eyebrow = `Function ${idx + 1} of ${fns.length} · ${fn.discipline ?? ''}`
    const la = this.host.loaded()
    if (fn.unassessed) return h.div(unassessedPanel(fn, la, eyebrow), fnFooter(idx, fns.length), { class: 'sfari-fnpanel-inner' })
    const [, results] = this.scored()
    const fr = results[fid]
    const d = this.host.delin()
    const rows: Child[] = (fn.metrics ?? []).map((m: Rec) => {
      const rc = this.measured[m.metricId] ?? {}
      const note = rc.stratumAuto ? stratifierNote(m, d) : ''
      return metricRow(m, rc, fr ? fr.metricIndices[m.metricId] : null, note)
    })
    rows.push(...withheldForFunction(la, fid).map(withheldCard))
    const total = (fn.metrics ?? []).length
    const entered = (fn.metrics ?? []).filter((m: Rec) => isEntered(this.measured[m.metricId])).length
    return h.div(
      fnHead(eyebrow, fn.functionName ?? ''),
      h.div(secLbl(entered, total), ...rows, { class: 'sfari-ev-card' }),
      scorecard(fid, fr ? fr.score : null),
      fnFooter(idx, fns.length, [entered, total]),
      { class: 'sfari-fnpanel-inner' })
  }

  /** rollup_rail */
  private rail(): Child {
    const [sc] = this.scored()
    const la = this.host.loaded()
    return rollupRail(sc, la, this.railDelta(), coverageOf(la))
  }

  /** The outputs that read the scores, after a value changed. */
  private scoresChanged(): void {
    if (!this.shown()) return
    this.setOutput('fn_nav', this.nav())
    this.setOutput('rollup_rail', this.rail())
  }

  private move(i: number): void {
    const n = this.walk().length
    if (!n) return
    this.currentFn = Math.max(0, Math.min(n - 1, i))
    if (!this.shown()) return
    this.setOutput('fn_nav', this.nav())
    this.setOutput('fn_panel', this.panel())
  }

  // ---------------------------------------------------------------- the desktop prefill and the curve sets
  private siteKey(): string | null {
    const la = this.host.loaded()
    const dl = this.host.delin()?.delineation ?? {}
    if (!la) return null
    const r6 = (v: unknown) => (v === null || v === undefined ? null : Math.round(Number(v) * 1e6) / 1e6)
    return JSON.stringify([la.assessmentId, la.library?.version ?? null, dl.comid ?? null, r6(dl.snapped_lat), r6(dl.snapped_lon)])
  }

  /** _seed_auto_strata: each stratified metric's curve set from the reach (never over the assessor's). */
  seedAutoStrata(): void {
    const la = this.host.loaded()
    const d = this.host.delin()
    if (!la || !d) return
    const auto = autoStrata(la, d)
    if (!Object.keys(auto).length) return
    const seed = (mv: Record<string, Rec>): boolean => {
      let changed = false
      for (const [mid, label] of Object.entries(auto)) {
        const cur = { ...(mv[mid] ?? {}) }
        if (cur.stratumAuto === false) continue
        if (cur.stratum !== label || cur.stratumAuto !== true) {
          cur.stratum = label
          cur.stratumAuto = true
          mv[mid] = cur
          changed = true
        }
      }
      return changed
    }
    const mvs = { ...this.measured }
    if (seed(mvs)) {
      this.measured = mvs
      if (this.shown()) this.setOutput('fn_panel', this.panel())
    }
    this.mergeIntoStored(seed)
  }

  private mergeIntoStored(update: (mv: Record<string, Rec>) => boolean): void {
    let changed = false
    for (const s of this.scenarios.items) {
      if (s.id === this.scenarios.active) continue
      const mv = copy(s.state?.measured_values ?? {})
      if (update(mv)) {
        s.state = { measured_values: mv }
        changed = true
      }
    }
    if (changed && this.shown()) {
      this.setOutput('scenario_bar', this.scenarioBar())
      this.setOutput('rollup_rail', this.rail())
    }
  }

  /** The desktop values computed ahead, once the Basin step shows a delineation with the assessment
   * loaded: the Shiny DEEP computes them when the measure step opens; here they are read while the
   * assessor looks at the Basin step, so the step finds them done. Nothing is shown. */
  computeAhead(): void {
    const la = this.host.loaded()
    const d = this.host.delin()
    if (!la || !d || !this.host.sourceReady() || !d.ctx_inputs) return
    const key = this.siteKey()
    if (key === null || this.computedFor === key || this.ahead?.key === key) return
    const ids = this.computableIds(la)
    if (!ids.length) return
    const es = this.host.engineState()
    const p = timed('deep:compute-ahead', computeMetrics(this.host.data, d.ctx_inputs, ids, la, es?.status === 'ok' ? es.record : null))
    p.catch(() => {})
    this.ahead = { key, p }
  }

  private computableIds(la: Rec): string[] {
    return (la.metricsByFunction ?? []).flatMap((fn: Rec) => fn.metrics ?? []).map((m: Rec) => m.metricId).filter((mid: string) => COMPUTABLE.has(mid))
  }

  /** _maybe_compute: once per assessment version and site, the desktop values the bundle can take. */
  maybeCompute(): void {
    if (this.host.step() !== 'measure') return
    const la = this.host.loaded()
    const d = this.host.delin()
    if (!la || !d || !this.host.sourceReady() || !d.ctx_inputs) return
    const key = this.siteKey()
    if (key === null || this.computedFor === key) return
    const ids = this.computableIds(la)
    if (!ids.length) return
    this.computedFor = key
    const gen = ++this.computeGen
    const es = this.host.engineState()
    const ahead = this.ahead?.key === key ? this.ahead.p : null
    this.ahead = null
    notify('Computing desktop metrics (STAF site engine / StreamCat lookup / 3DEP)…', { id: 'deep_compute', type: 'message', seconds: 0 })
    void timed('deep:compute', ahead ?? computeMetrics(this.host.data, d.ctx_inputs, ids, la, es?.status === 'ok' ? es.record : null)).then(
      (res) => this.computeDone(gen, key, res), () => this.computeDone(gen, key, null))
  }

  private computeDone(gen: number, key: string, res: Record<string, Rec> | null): void {
    if (gen !== this.computeGen || this.siteKey() !== key) return
    removeNotification('deep_compute')
    if (!res) return
    this.desktopFill = [key, copy(res)]
    const mvs = { ...this.measured }
    let n = 0
    for (const [mid, entry] of Object.entries(res)) {
      const cur = mvs[mid] ?? {}
      if ((cur.value === null || cur.value === undefined || cur.value === '') && !cur.na) {
        mvs[mid] = { ...cur, ...entry }
        n += 1
      }
    }
    this.mergeIntoStored((mv) => {
      let filled = false
      for (const [mid, entry] of Object.entries(res)) {
        const cur = mv[mid] ?? {}
        if ((cur.value === null || cur.value === undefined || cur.value === '') && !cur.na) {
          mv[mid] = { ...cur, ...copy(entry) }
          filled = true
        }
      }
      return filled
    })
    if (n) {
      this.measured = mvs
      if (this.shown()) {
        this.setOutput('fn_panel', this.panel())
        this.scoresChanged()
      }
      notify(`Auto-filled ${n} desktop metrics. Edit any value to override.`, { type: 'message', seconds: 5 })
    }
  }

  // ---------------------------------------------------------------- scenarios
  private scenarioValues(): [Scenario, Record<string, Rec>][] {
    return this.scenarios.items.map((s) => [s, s.id === this.scenarios.active ? this.measured : (s.state?.measured_values ?? {})])
  }

  private comparison(rows = this.scenarioValues()): Comparison {
    return deepComparison(this.host.loaded(), rows.map(([s]) => s.name), rows.map(([, mv]) => this.score(mv)))
  }

  private scenarioBar(): Child {
    if (!this.host.loaded()) return null
    return scenarioBar(this.scenarios)
  }

  private railDelta(): Child {
    if (this.scenarios.active === BASELINE_ID) return null
    const rows = this.scenarioValues()
    const active = rows.findIndex(([s]) => s.id === this.scenarios.active)
    return railDelta(this.comparison([rows[0], rows[active]]), 1)
  }

  private scenariosChanged(switched: boolean): void {
    if (!this.shown()) return
    this.setOutput('scenario_bar', this.scenarioBar())
    this.setOutput('rollup_rail', this.rail())
    if (switched) {
      this.setOutput('fn_nav', this.nav())
      this.setOutput('fn_panel', this.panel())
    }
  }

  /** _desktop_start: a Blank scenario, the desktop values and the preselected curve sets. */
  private desktopStart(): Rec {
    const la = this.host.loaded()
    const d = this.host.delin()
    const held = this.desktopFill
    const mv: Record<string, Rec> = held && la && d && held[0] === this.siteKey() ? copy(held[1]) : {}
    if (la && d) {
      for (const [mid, label] of Object.entries(autoStrata(la, d))) mv[mid] = { ...(mv[mid] ?? {}), stratum: label, stratumAuto: true }
    }
    return { measured_values: mv }
  }

  private switchScenario(sid: string): void {
    const s = this.scenarios
    if (sid === s.active || !s.items.some((x) => x.id === sid)) return
    s.setState(s.active, { measured_values: copy(this.measured) })
    s.select(sid)
    this.measured = copy(s.current.state?.measured_values ?? {})
    this.scenariosChanged(true)
  }

  private scenarioDialog(mode: 'new' | 'edit', name: string, description: string, start: string = BASELINE_ID, error: string | null = null): void {
    if (mode === 'new') {
      showModal(addDialog('deep', this.scenarios, { name, description, start, hint: ADD_HINT, error }))
      return
    }
    const base = this.scenarios.current.id === BASELINE_ID
    showModal(scenarioDialog('deep', { title: base ? 'Describe Existing Conditions' : 'Rename or describe', name, description, nameLocked: base,
      error, canDelete: mode === 'edit' && !base }))
  }

  private scenarioEvent(ev: Rec): void {
    const s = this.scenarios
    const sid = ev.id || ''
    if (ev.action === 'select' && sid) this.switchScenario(sid)
    else if (ev.action === 'new') {
      if (!s.canAdd()) {
        notify('Up to 10 scenarios.', { type: 'warning', seconds: 4 })
        return
      }
      this.dialog = 'new'
      this.scenarioDialog('new', s.defaultName(), '')
    } else if (ev.action === 'edit') {
      this.dialog = 'edit'
      this.scenarioDialog('edit', s.current.name, s.current.description)
    } else if (ev.action === 'delete' && s.current.id !== BASELINE_ID) showModal(deleteDialog('deep', s.current.name))
    else if (ev.action === 'compare' && s.hasAlternatives()) showModal(compareDialog(this.comparison()))
  }

  private field(id: string): string {
    const el = document.getElementById(`deep-${id}`) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null
    return el ? el.value : ''
  }

  private scenarioSave(): void {
    const s = this.scenarios
    const mode = this.dialog
    const desc = strip(this.field(DESC_INPUT))
    let name = s.current.name
    if (mode === 'new' || s.current.id !== BASELINE_ID) name = strip(this.field(NAME_INPUT))
    let start = BASELINE_ID
    try {
      if (mode === 'new') {
        start = this.field(START_INPUT) || BASELINE_ID
        if (start !== START_BLANK && !s.items.some((x) => x.id === start)) start = BASELINE_ID
        s.setState(s.active, { measured_values: copy(this.measured) })
        const created = start === START_BLANK ? s.add(name, desc, { state: this.desktopStart() }) : s.add(name, desc, { copyFrom: start })
        if (start !== START_BLANK) created.state = { measured_values: withoutPhotos(created.state?.measured_values ?? {}) }
        this.measured = copy(created.state?.measured_values ?? {})
        this.scenariosChanged(true)
      } else {
        const cur = s.current
        if (cur.id !== BASELINE_ID && name !== cur.name) s.rename(cur.id, name)
        s.describe(cur.id, desc)
        this.scenariosChanged(false)
      }
    } catch (err) {
      this.scenarioDialog(mode ?? 'edit', name, desc, start, err instanceof Error ? err.message : String(err))
      return
    }
    removeModal()
  }

  private scenarioDelete(): void {
    const s = this.scenarios
    if (s.current.id !== BASELINE_ID) {
      s.delete(s.active)
      this.measured = copy(s.current.state?.measured_values ?? {})
      this.scenariosChanged(true)
    }
    removeModal()
  }

  // ---------------------------------------------------------------- the edit bridge
  private update(mid: string, change: (cur: Rec) => void): void {
    const cur = { ...(this.measured[mid] ?? {}) }
    change(cur)
    this.measured = { ...this.measured, [mid]: cur }
  }

  /** An input measure.js (or the scenario chip) posted; true when it was the worksheet's. */
  input(name: string, value: unknown): boolean {
    const ev = (value ?? {}) as Rec
    switch (name) {
      case 'measure_set': {
        if (!ev.mid) return true
        const raw = ev.value
        let val: number | null = null
        if (raw !== null && raw !== undefined && raw !== '') {
          const x = Number(raw)
          val = Number.isNaN(x) && String(raw).trim().toLowerCase() !== 'nan' ? null : x
        }
        this.update(ev.mid, (cur) => {
          cur.value = val
          cur.origin = 'field'
        })
        this.scoresChanged()
        return true
      }
      case 'measure_na':
        if (!ev.mid) return true
        this.update(ev.mid, (cur) => { cur.na = !!ev.na })
        this.scoresChanged()
        return true
      case 'measure_note':
        if (!ev.mid) return true
        this.update(ev.mid, (cur) => { cur.note = ev.note ?? '' })
        this.scoresChanged()
        return true
      case 'measure_stratum':
        if (!ev.mid) return true
        this.update(ev.mid, (cur) => {
          cur.stratum = ev.stratum
          cur.stratumAuto = false
        })
        if (this.shown()) this.setOutput('fn_panel', this.panel())
        this.scoresChanged()
        return true
      case 'metric_photo_add': {
        if (!ev.mid || !ev.id || !ev.uri) return true
        this.update(ev.mid, (cur) => {
          const photos = [...(cur.photos ?? [])]
          if (photos.length < 6 && !photos.some((p: Rec) => p.id === ev.id)) photos.push({ id: ev.id, uri: ev.uri })
          cur.photos = photos
        })
        this.scoresChanged()
        return true
      }
      case 'metric_photo_remove':
        if (!ev.mid || !ev.id) return true
        this.update(ev.mid, (cur) => { cur.photos = (cur.photos ?? []).filter((p: Rec) => p.id !== ev.id) })
        this.scoresChanged()
        return true
      case 'nav_move':
        this.move(this.currentFn + Math.trunc(Number(ev.d) || 0))
        return true
      case 'nav_jump':
        if (ev.i !== undefined && ev.i !== null) this.move(Math.trunc(Number(ev.i)))
        return true
      case 'open_report_evt':
        notify('The report is not in the web app yet.', { type: 'message', seconds: 4 })
        return true
      case 'field_forms_evt':
        notify('Get Forms is not in the web app yet.', { type: 'message', seconds: 4 })
        return true
      case 'staf_scenario_evt':
        this.scenarioEvent(ev)
        return true
      case 'staf_sc_save':
        this.scenarioSave()
        return true
      case 'staf_sc_delete':
        this.scenarioDelete()
        return true
      default:
        return false
    }
  }
}

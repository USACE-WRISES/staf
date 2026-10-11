// SFARI's Assessment step: the worksheet over the map, its evidence pull and its scoring state, as
// sfari/app.py's server keeps them (Phase 2 of its server: the edit bridge, the navigation, the pull
// and the live rollup).
//
// The page script field-review.js (the Shiny app's own, unchanged) posts every edit as an input; the
// outputs it does not update itself are drawn again as Shiny would draw them: the function list and the
// rail when a function score changes, the suggestion line when a rating changes, everything on a move
// to another function. The function panel keeps its own state between moves, as in Shiny (the server
// reads the scores isolated), so a rating never redraws the row being rated.
import { type Child, h, HTML, outputHtml } from '../../app/htm'
import { notify, removeNotification } from '../../app/notify'
import {
  addDialog, BASELINE_ID, compareDialog, type Comparison, DESC_INPUT, deleteDialog, NAME_INPUT, railDelta, type Scenario,
  scenarioBar, scenarioDialog, ScenarioSet, START_BLANK, START_INPUT,
} from '../../shared/scenarios'
import { removeModal, showModal } from '../../shared/modal'
import { sfariComparison } from './compare'
import { type Evidence, type EngineState, mergePulledEvidence, pull, type PullData } from './evidence'
import { FN_BY_ID, FN_IDS, likertToScore, METRICS_BY_FN, scoreAssessment } from './method'
import {
  fnNav, type FunctionRecord, metricRow, type MetricRecord, output, rollupRail, scoreCard, stepper, suggestionLine, HYDRAULICS_FNS,
} from './markup'

export interface WorksheetHost {
  root: HTMLElement
  data: PullData
  /** the step shown */
  step(): string
  /** the delineation result (ctx_inputs) and the engine state Delineate left */
  delin(): Record<string, any> | null
  engineState(): EngineState | null
  /** the engine's line (Basin card and worksheet), the lookup status for the worksheet */
  engineLine(): string
  lookupStatus(): string
  /** the source is resolved for this site (_source_ready) */
  sourceReady(): boolean
  /** go to a step */
  setStep(step: string): void
}

/** The Add dialog's line under Start from (app.py _ADD_HINT). */
const ADD_HINT = 'A copy brings its scores and notes; photos stay with the original. Blank starts with no scores.'

interface Entries { metric_scores: Record<string, MetricRecord>, function_scores: Record<string, FunctionRecord> }
const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x ?? {}))
const strip = (s: string) => s.replace(/^\s+|\s+$/g, '')

/** _without_photos: a scenario's entries without photos (an alternative is a plan, not a site visit). */
function withoutPhotos(state: Entries): Entries {
  const out = copy(state)
  for (const rec of Object.values(out.metric_scores ?? {})) delete (rec as MetricRecord).photos
  return out
}

export class SfariWorksheet {
  metricScores: Record<string, MetricRecord> = {}
  functionScores: Record<string, FunctionRecord> = {}
  currentFn = 0
  evidence: Record<string, Evidence> = {}
  private pulling = false
  private pullGen = 0
  private progress = { done: 0, total: 0 }
  private poll: ReturnType<typeof setInterval> | null = null
  /** Existing Conditions and the alternatives; the shown one's entries live in the two score maps */
  scenarios = new ScenarioSet()
  private dialog: 'new' | 'edit' | null = null

  constructor(private host: WorksheetHost) {}

  private el(id: string): HTMLElement | null {
    return document.getElementById(`sfari-${id}`)
  }

  /** A new pick or a reset: the scores, the evidence and the place in the list go (_invalidate_selection). */
  reset(): void {
    this.pullGen += 1
    this.stopPoll()
    this.pulling = false
    this.metricScores = {}
    this.functionScores = {}
    this.currentFn = 0
    this.evidence = {}
    this.scenarios = new ScenarioSet()
  }

  private shown(): boolean {
    const s = this.host.step()
    return s === 'review' || s === 'report'
  }

  // ---------------------------------------------------------------- outputs

  /** The worksheet output: the whole overlay on the Assessment step, nothing elsewhere. */
  render(): void {
    const slot = this.el('worksheet')
    if (!slot) return
    if (!this.shown()) {
      if (slot.innerHTML) slot.innerHTML = ''
      return
    }
    const ws = h.div(
      h.div(
        h.div('SFARI · Assessment', { class: 'easi-pane-head' }),
        h.div(stepper(this.host.step()), { class: 'sfari-nav-steps' }),
        h.button('Get Forms', {
          'data-desktop-metrics': '1', type: 'button',
          title: 'The field forms, the desktop metrics PDF with your pulled values, and the Excel calculator, completed or blank',
        }, { class: 'sfari-btn sfari-nav-desktop' }),
        output('engine_line_ws', this.engineLineWs()),
        output('streamcat_lookup_status_ws', this.lookupWs()),
        output('fn_nav', fnNav(this.currentFn, this.functionScores)),
        { class: 'sfari-nav' }),
      h.div(output('fn_panel', this.panel()), { class: 'sfari-fnpanel' }),
      h.div(output('scenario_bar', this.scenarioBar()), output('rollup_rail', this.rail()), { class: 'sfari-rollup' }),
      { class: 'sfari-worksheet' })
    slot.innerHTML = outputHtml(ws)
  }

  /** engine_line_ws: the engine's line, as in the Basin card (nothing once the engine is ok). */
  private engineLineWs(): Child {
    const html = this.host.engineLine().trim()
    return html ? new HTML(html) : null
  }

  /** streamcat_lookup_status_ws: the lookup's status with the worksheet's own retry button. */
  private lookupWs(): Child {
    const html = this.host.lookupStatus().trim()
    return html ? new HTML(html.split('sfari-retry_streamcat"').join('sfari-retry_streamcat_ws"')) : null
  }

  private setOutput(id: string, content: Child): void {
    const el = this.el(id)
    if (el) el.innerHTML = outputHtml(content)
  }

  /** fn_panel */
  private panel(): Child {
    const idx = this.currentFn
    const fid = FN_IDS[idx]
    const f = FN_BY_ID.get(fid)!
    const ms = this.metricScores
    const rec = this.functionScores[fid] ?? {}
    const metrics = METRICS_BY_FN.get(fid) ?? []
    const blocks = metrics.map((m) => metricRow(m, ms[m.metricId], this.evidence[m.metricId], this.pulling))
    const total = metrics.length
    const rated = metrics.filter((m) => ms[m.metricId]?.likert).length
    const left = [h.button('‹ Previous', { 'data-nav': '-1', type: 'button', disabled: idx === 0 ? 'disabled' : null }, { class: 'sfari-btn' })]
    if (HYDRAULICS_FNS.has(fid)) left.push(h.button('Cross-section hydraulics', { 'data-xs': fid, type: 'button' }, { class: 'sfari-btn' }))
    const scored = rec.score !== null && rec.score !== undefined
    const actions = h.div(
      h.div(...left, { class: 'sfari-foot-left' }),
      h.div(h.span(`${rated}/${total} rated`, { class: 'sfari-foot-rated' }), h.span('·'),
        h.span(scored ? 'scored' : 'score needed', { class: 'sfari-foot-score' + (scored ? ' ok' : '') }), { class: 'sfari-foot-status' }),
      h.button(idx < FN_IDS.length - 1 ? 'Next function ›' : 'Done', { 'data-nav': '1', type: 'button' }, { class: 'sfari-btn primary' }),
      { class: 'sfari-nav-actions' })
    return h.div(
      h.div(h.div(`Function ${idx + 1} of ${FN_IDS.length} · ${f.category}`, { class: 'sfari-fn-eyebrow' }), h.div(f.name, { class: 'sfari-fn-name' }), { class: 'sfari-fn-head' }),
      h.div(h.div(h.span('1', { class: 'sfari-step-num' }), h.span('Metrics', { class: 'sfari-sec-title' }), h.span(`${rated} of ${total} rated`, { class: 'sfari-sec-count' }),
        { class: 'sfari-sec-lbl' }), ...blocks, { class: 'sfari-ev-card' }),
      scoreCard(f, fid, rec, this.suggestion()),
      h.div(actions, { class: 'sfari-fn-footer' }),
      { class: 'sfari-fnpanel-inner' })
  }

  /** fn_suggest */
  private suggestion(): Child {
    const fid = FN_IDS[this.currentFn]
    const likerts = (METRICS_BY_FN.get(fid) ?? []).map((m) => this.metricScores[m.metricId]?.likert)
    return suggestionLine(fid, likertToScore(likerts.filter(Boolean)))
  }

  /** rollup_rail: every function counts, a blank one as zero (scored()). */
  private rail(): Child {
    const scores = Object.fromEntries(Object.entries(this.functionScores).map(([fid, v]) => [fid, v.score ?? null]))
    return rollupRail(scoreAssessment(scores), this.functionScores, this.railDelta())
  }

  /** scenario_bar */
  private scenarioBar(): Child {
    return scenarioBar(this.scenarios)
  }

  // ---------------------------------------------------------------- scenarios
  private entriesNow(): Entries {
    return { metric_scores: copy(this.metricScores), function_scores: copy(this.functionScores) }
  }

  private showEntries(state: Partial<Entries> | null): void {
    this.metricScores = copy(state?.metric_scores ?? {})
    this.functionScores = copy(state?.function_scores ?? {})
  }

  /** [(scenario, entries)] in order, the shown one's read live. */
  private scenarioEntries(): [Scenario, Partial<Entries>][] {
    const live = { metric_scores: this.metricScores, function_scores: this.functionScores }
    return this.scenarios.items.map((s) => [s, s.id === this.scenarios.active ? live : (s.state ?? {})])
  }

  private comparison(rows = this.scenarioEntries()): Comparison {
    return sfariComparison(rows.map(([s]) => s.name), rows.map(([, e]) => e.function_scores ?? {}))
  }

  /** _rail_delta: while an alternative is shown, its ECI against Existing Conditions. */
  private railDelta(): Child {
    if (this.scenarios.active === BASELINE_ID) return null
    const rows = this.scenarioEntries()
    const active = rows.findIndex(([s]) => s.id === this.scenarios.active)
    return railDelta(this.comparison([rows[0], rows[active]]), 1)
  }

  /** A scenario change: the chip and the rail again; a switch redraws the function too. */
  private scenariosChanged(switched: boolean): void {
    if (!this.shown()) return
    this.setOutput('scenario_bar', this.scenarioBar())
    this.setOutput('rollup_rail', this.rail())
    if (switched) {
      this.setOutput('fn_nav', fnNav(this.currentFn, this.functionScores))
      this.setOutput('fn_panel', this.panel())
    }
  }

  private switchScenario(sid: string): void {
    const s = this.scenarios
    if (sid === s.active || !s.items.some((x) => x.id === sid)) return
    s.setState(s.active, this.entriesNow())
    s.select(sid)
    this.showEntries(s.current.state)
    this.scenariosChanged(true)
  }

  private scenarioDialog(mode: 'new' | 'edit', name: string, description: string, start: string = BASELINE_ID, error: string | null = null): void {
    if (mode === 'new') {
      showModal(addDialog('sfari', this.scenarios, { name, description, start, hint: ADD_HINT, error }))
      return
    }
    const cur = this.scenarios.current
    const base = cur.id === BASELINE_ID
    showModal(scenarioDialog('sfari', { title: base ? 'Describe Existing Conditions' : 'Rename or describe', name, description,
      nameLocked: base, error, canDelete: mode === 'edit' && !base }))
  }

  /** staf_scenario_evt: the chip's choices and links (scenarios.js). */
  private scenarioEvent(ev: Record<string, any>): void {
    const action = ev.action
    const sid = ev.id || ''
    const s = this.scenarios
    if (action === 'select' && sid) this.switchScenario(sid)
    else if (action === 'new') {
      if (!s.canAdd()) {
        notify('Up to 10 scenarios.', { type: 'warning', seconds: 4 })
        return
      }
      this.dialog = 'new'
      this.scenarioDialog('new', s.defaultName(), '')
    } else if (action === 'edit') {
      this.dialog = 'edit'
      this.scenarioDialog('edit', s.current.name, s.current.description)
    } else if (action === 'delete' && s.current.id !== BASELINE_ID) {
      showModal(deleteDialog('sfari', s.current.name))
    } else if (action === 'compare' && s.hasAlternatives()) {
      showModal(compareDialog(this.comparison()))
    }
  }

  private field(id: string): string {
    const el = document.getElementById(`sfari-${id}`) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null
    return el ? el.value : ''
  }

  /** staf_sc_save: Add, or rename and describe. */
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
        s.setState(s.active, this.entriesNow())
        const created = start === START_BLANK
          ? s.add(name, desc, { state: { metric_scores: {}, function_scores: {} } })
          : s.add(name, desc, { copyFrom: start })
        if (start !== START_BLANK) created.state = withoutPhotos(created.state)
        this.showEntries(created.state)
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

  /** staf_sc_delete: the shown alternative goes; Existing Conditions shows. */
  private scenarioDelete(): void {
    const s = this.scenarios
    if (s.current.id !== BASELINE_ID) {
      s.delete(s.active)
      this.showEntries(s.current.state)
      this.scenariosChanged(true)
    }
    removeModal()
  }

  // ---------------------------------------------------------------- the pull
  /** _maybe_pull: on the Assessment step, once per site, with the source resolved. */
  maybePull(): void {
    if (this.host.step() !== 'review') return
    const d = this.host.delin()
    if (!d?.ctx_inputs || this.pulling || Object.keys(this.evidence).length || !this.host.sourceReady()) return
    this.launch(d, this.host.engineState() ?? { status: 'idle' })
  }

  private launch(d: Record<string, any>, state: EngineState): void {
    const gen = ++this.pullGen
    this.pulling = true
    this.progress = { done: 0, total: 0 }
    notify('Pulling desktop evidence…', { id: 'pull', type: 'message', seconds: 0 })
    this.stopPoll()
    this.poll = setInterval(() => {
      if (this.pulling && gen === this.pullGen) {
        notify(`Pulling desktop evidence… ${this.progress.done}/${this.progress.total}`, { id: 'pull', type: 'message', seconds: 0 })
      }
    }, 400)
    void pull(this.host.data, d.ctx_inputs, state, this.progress).then((got) => {
      if (gen !== this.pullGen) return
      this.finish(got)
    }, () => {
      if (gen !== this.pullGen) return
      this.finish(null)
    })
  }

  private stopPoll(): void {
    if (this.poll) clearInterval(this.poll)
    this.poll = null
    removeNotification('pull')
  }

  private finish(got: Record<string, Evidence> | null): void {
    this.pulling = false
    this.stopPoll()
    if (got) this.evidence = mergePulledEvidence(this.evidence, got) as Record<string, Evidence>
    if (this.shown()) this.setOutput('fn_panel', this.panel())
  }

  // ---------------------------------------------------------------- the edit bridge
  /** An input field-review.js posted; true when it was the worksheet's. */
  input(name: string, value: unknown): boolean {
    const ev = (value ?? {}) as Record<string, any>
    switch (name) {
      case 'likert_set': {
        if (!ev.mid) return true
        this.metricScores = { ...this.metricScores, [ev.mid]: { ...this.metricScores[ev.mid], likert: ev.val ? ev.val : null } }
        this.setOutput('fn_suggest', this.suggestion())
        return true
      }
      case 'metric_note_set': {
        if (!ev.mid) return true
        this.metricScores = { ...this.metricScores, [ev.mid]: { ...this.metricScores[ev.mid], note: ev.note ?? '' } }
        this.setOutput('fn_suggest', this.suggestion())
        return true
      }
      case 'metric_photo_add': {
        if (!ev.mid || !ev.id || !ev.uri) return true
        const cur = { ...this.metricScores[ev.mid] }
        const photos = [...(cur.photos ?? [])]
        if (photos.length < 6 && !photos.some((p) => p.id === ev.id)) photos.push({ id: ev.id, uri: ev.uri })
        this.metricScores = { ...this.metricScores, [ev.mid]: { ...cur, photos } }
        this.setOutput('fn_suggest', this.suggestion())
        return true
      }
      case 'metric_photo_remove': {
        if (!ev.mid || !ev.id) return true
        const cur = { ...this.metricScores[ev.mid] }
        this.metricScores = { ...this.metricScores, [ev.mid]: { ...cur, photos: (cur.photos ?? []).filter((p) => p.id !== ev.id) } }
        this.setOutput('fn_suggest', this.suggestion())
        return true
      }
      case 'fnscore_set': {
        const val = Number.parseInt(String(ev.score), 10)
        if (!ev.fid || Number.isNaN(val)) return true
        this.functionScores = { ...this.functionScores, [ev.fid]: { ...this.functionScores[ev.fid], score: Math.max(0, Math.min(15, val)) } }
        this.setOutput('fn_nav', fnNav(this.currentFn, this.functionScores))
        this.setOutput('rollup_rail', this.rail())
        return true
      }
      case 'fn_note_set': {
        if (!ev.fid) return true
        this.functionScores = { ...this.functionScores, [ev.fid]: { ...this.functionScores[ev.fid], note: ev.note ?? '' } }
        this.setOutput('fn_nav', fnNav(this.currentFn, this.functionScores))
        this.setOutput('rollup_rail', this.rail())
        return true
      }
      case 'nav_move':
        this.move(this.currentFn + Math.trunc(Number(ev.d) || 0))
        return true
      case 'nav_jump':
        if (ev.i !== undefined && ev.i !== null) this.move(Math.trunc(Number(ev.i)))
        return true
      case 'open_report_evt':
        this.requestReport()
        return true
      case 'desktop_metrics_evt':
        notify('Get Forms is not in the web app yet.', { type: 'message', seconds: 4 })
        return true
      case 'xs_open_evt':
        notify('The cross-section hydraulics tool is not in the web app yet.', { type: 'message', seconds: 4 })
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

  private move(i: number): void {
    this.currentFn = Math.max(0, Math.min(FN_IDS.length - 1, i))
    if (!this.shown()) return
    this.setOutput('fn_nav', fnNav(this.currentFn, this.functionScores))
    this.setOutput('fn_panel', this.panel())
  }

  anyScored(): boolean {
    return Object.values(this.functionScores).some((v) => v.score !== null && v.score !== undefined)
  }

  /** _request_report: the report needs a scored function. */
  requestReport(): void {
    if (!this.host.delin()) return
    if (!this.anyScored()) {
      notify('Score at least one function before viewing the report.', { type: 'message', seconds: 4 })
      return
    }
    notify('The report is not in the web app yet.', { type: 'message', seconds: 4 })
  }
}

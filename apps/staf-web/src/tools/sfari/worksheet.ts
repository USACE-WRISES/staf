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
import { timed } from '../../app/perf'
import {
  addDialog, BASELINE_ID, compareDialog, type Comparison, DESC_INPUT, deleteDialog, NAME_INPUT, railDelta, type Scenario,
  scenarioBar, scenarioDialog, ScenarioSet, START_BLANK, START_INPUT,
} from '../../shared/scenarios'
import { removeModal, showModal } from '../../shared/modal'
import { sfariComparison } from './compare'
import * as shim from '../../app/shim'
import { level3At } from '../../engine/ecoregion'
import { displaySimplify } from '../../engine/simplify'
import { pageFile, registerDownload } from '../../files/download'
import { scenarioSuffix, XLSX_MEDIA_TYPE } from '../../shared/downloads'
import {
  BLANK_FILENAME, BLANK_URL, FIELD_FORMS_FILENAME, FIELD_FORMS_URL, ffPreview, ffSite, ffStatus, ffTable, formsModal, type PullState,
} from './forms'
import { todayIso } from '../../files/xlsx/calcfill'
import { buildCalculator, calculatorFilename } from './calculator'
import type { SummaryInfo } from '../../shared/summary'
import { svg } from '../../shared/reportmap'
import { regionsForL3, reportScenario, summaryRows, summarySection } from '../../shared/summary'
import { buildCsv, buildGeojson, reportModal } from './report'
import { attachEvidence, listenBoxes, openGeometry, readBoxes, type XsGeom, xsModal, xsView } from './xstool'
import { pyClone } from '../../app/pyjson'
import * as sfariSession from '../../session/sfari'
import { scenarioSet, scenariosBlock } from '../../session/file'
import { stateFingerprint } from '../../session/guard'
import { type Evidence, type EngineState, mergePulledEvidence, pull, type PullData } from './evidence'
import { FN_BY_ID, FN_IDS, likertToScore, METRICS_BY_FN, type Scored, scoreAssessment } from './method'
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
  /** the StreamCat lookup runs (snapping, finding, retrying); the engine's progress line while it runs */
  lookupBusy(): boolean
  engineProgress(): string | null
}

/** The Add dialog's line under Start from (app.py _ADD_HINT). */
const ADD_HINT = 'A copy brings its scores and notes; photos stay with the original. Blank starts with no scores.'

interface Entries { metric_scores: Record<string, MetricRecord>, function_scores: Record<string, FunctionRecord> }
const copy = <T>(x: T): T => pyClone(x ?? ({} as T))
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
  /** the cross-section hydraulics tool's geometry (xs_geom: set when the tool opens), saved with the file */
  xsGeom: Record<string, any> | null = null
  private pulling = false
  private pullGen = 0
  private progress = { done: 0, total: 0 }
  /** the desktop evidence pulled ahead for a delineation (pullAhead), with its progress */
  private ahead: { delin: Record<string, any>, progress: { done: number, total: number }, p: Promise<Record<string, Evidence>> } | null = null
  private poll: ReturnType<typeof setInterval> | null = null
  /** the pull for this site: none yet (initial), running, or done (failed when it threw) */
  private pullStatus: 'initial' | 'running' | 'done' = 'initial'
  private pullFailed = false
  /** counts every change of the evidence, so Get Forms redraws its table only then */
  private evidenceRev = 0
  /** Get Forms while it is open: its refresh timer and what its outputs show */
  private forms: { timer: ReturnType<typeof setInterval>, rev: number, status: string } | null = null
  /** Existing Conditions and the alternatives; the shown one's entries live in the two score maps */
  scenarios = new ScenarioSet()
  private dialog: 'new' | 'edit' | null = null

  constructor(private host: WorksheetHost) {
    this.registerDownloads()
  }

  private el(id: string): HTMLElement | null {
    return document.getElementById(`sfari-${id}`)
  }

  /** A new pick or a reset: the scores, the evidence and the place in the list go (_invalidate_selection). */
  reset(): void {
    this.cancelReport()
    this.pullGen += 1
    this.ahead = null
    this.stopPoll()
    this.pulling = false
    this.pullStatus = 'initial'
    this.pullFailed = false
    this.metricScores = {}
    this.functionScores = {}
    this.currentFn = 0
    this.evidence = {}
    this.evidenceRev += 1
    this.xsGeom = null
    this.scenarios = new ScenarioSet()
  }

  // ---------------------------------------------------------------- the assessment file

  /** _session_scenarios: every scenario's entries, the shown one's read live. */
  private sessionScenarios(): Record<string, any> {
    return scenariosBlock(this.scenarios, this.entriesNow())
  }

  /** Save (save_session): the file's text for this delineation. */
  saveText(delin: Record<string, any> | null): string {
    return sfariSession.dump(delin ?? {}, this.evidence, this.xsGeom, this.sessionScenarios())
  }

  /** _work_fp: the site and every scenario's entries (null: nothing to lose). */
  workFingerprint(delin: Record<string, any> | null): string | null {
    const dd = (delin ?? {}).delineation ?? {}
    const s = this.scenarios
    if (!Object.keys(dd).length && !this.hasEntries() && !s.hasAlternatives() && !s.baseline.description) return null
    const site = ['nhdplusid', 'comid', 'snapped_lat', 'snapped_lon'].map((k) => dd[k] ?? null)
    const rows = this.scenarioEntries().map(([sc, e]) => [sc.id, sc.name, sc.description, e])
    return stateFingerprint(site, rows)
  }

  /** Open (_load_session's part): the pulled evidence (no pull runs again), the hydraulics geometry and
   * every scenario's entries, the saved one shown. */
  restore(evidence: Record<string, any>, xsGeom: unknown, scenarios: Record<string, any> | null): void {
    this.reset()
    this.evidence = evidence as Record<string, Evidence>
    this.evidenceRev += 1
    this.xsGeom = (xsGeom ?? null) as Record<string, any> | null
    const sset = scenarioSet(scenarios)
    this.scenarios = sset
    this.showEntries(sset.current.state)
    this.currentFn = 0
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
  /** The desktop evidence pulled ahead, once the Basin step shows a delineation with the source resolved
   * (the engine's record is final by then): the Shiny SFARI pulls when the Assessment step opens; here the
   * pull runs while the assessor looks at the Basin step, so the step finds it done. Nothing is shown. */
  pullAhead(): void {
    const d = this.host.delin()
    if (!d?.ctx_inputs || this.pulling || Object.keys(this.evidence).length || !this.host.sourceReady() || this.ahead?.delin === d) return
    const progress = { done: 0, total: 0 }
    const p = timed('sfari:pull-ahead', pull(this.host.data, d.ctx_inputs, this.host.engineState() ?? { status: 'idle' }, progress))
    p.catch(() => {})
    this.ahead = { delin: d, progress, p }
  }

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
    this.pullStatus = 'running'
    this.pullFailed = false
    const ahead = this.ahead?.delin === d ? this.ahead : null
    this.ahead = null
    this.progress = ahead ? ahead.progress : { done: 0, total: 0 }
    notify('Pulling desktop evidence…', { id: 'pull', type: 'message', seconds: 0 })
    this.stopPoll()
    this.poll = setInterval(() => {
      if (this.pulling && gen === this.pullGen) {
        notify(`Pulling desktop evidence… ${this.progress.done}/${this.progress.total}`, { id: 'pull', type: 'message', seconds: 0 })
      }
    }, 400)
    void timed('sfari:pull', ahead ? ahead.p : pull(this.host.data, d.ctx_inputs, state, this.progress)).then((got) => {
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
    this.pullStatus = 'done'
    this.pullFailed = got === null
    this.stopPoll()
    if (got) {
      this.evidence = mergePulledEvidence(this.evidence, got) as Record<string, Evidence>
      this.evidenceRev += 1
    }
    if (this.shown()) this.setOutput('fn_panel', this.panel())
    this.refreshForms()
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
        this.openForms()
        return true
      case 'ff_retry':
        this.retryPull()
        return true
      case 'xs_open_evt':
        void this.openXs()
        return true
      case 'xs_attach_evt':
        this.attachXs()
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

  // ---------------------------------------------------------------- Get Forms
  /** What Get Forms' status line reads from (the pull's, the engine's and the lookup's state). */
  private pullState(): PullState {
    const engine = this.host.engineProgress()
    return { engineRunning: engine !== null, engineText: engine ?? '', finding: this.host.lookupBusy(),
      pull: this.pullStatus === 'running' ? 'running' : this.pullStatus === 'initial' ? 'initial' : this.pullFailed ? 'failed' : 'ok',
      done: this.progress.done, total: this.progress.total }
  }

  /** _open_desktop_metrics: the dialog's shell with its outputs; while it is open, the status line is
   * drawn again each second (ff_status polls while the engine or the pull runs) and the table when the
   * evidence changes. */
  private openForms(): void {
    this.cancelReport()
    this.closeForms()
    const status = ffStatus(this.evidence, this.pullState())
    const el = showModal(formsModal(ffSite(this.host.delin()), status, ffTable(this.evidence), ffPreview()))
    this.forms = { timer: setInterval(() => this.refreshForms(), 1000), rev: this.evidenceRev, status: outputHtml(status) }
    el.addEventListener('hidden.bs.modal', () => this.closeForms(), { once: true })
  }

  private closeForms(): void {
    if (this.forms) clearInterval(this.forms.timer)
    this.forms = null
  }

  /** Get Forms' outputs drawn again where they changed (nothing when the dialog is closed). */
  private refreshForms(): void {
    const f = this.forms
    if (!f) return
    if (!document.getElementById('sfari-ff_status')) {
      this.closeForms()
      return
    }
    const status = outputHtml(ffStatus(this.evidence, this.pullState()))
    if (status !== f.status) {
      f.status = status
      this.setOutput('ff_status', new HTML(status))
    }
    if (f.rev !== this.evidenceRev) {
      f.rev = this.evidenceRev
      this.setOutput('ff_table', ffTable(this.evidence))
    }
  }

  /** _ff_retry: the pull again, once the source is resolved for this site. */
  private retryPull(): void {
    const d = this.host.delin()
    if (!d || !this.host.sourceReady()) {
      notify('Resolve the StreamCat lookup before pulling desktop evidence.', { type: 'warning', seconds: 5 })
      return
    }
    if (!d.ctx_inputs || this.pulling) return
    this.launch(d, this.host.engineState() ?? { status: 'idle' })
    notify('Retrying desktop evidence pull…', { id: 'pull', type: 'message', seconds: 0 })
    this.refreshForms()
  }

  // ---------------------------------------------------------------- the cross-section hydraulics tool
  private xsStop: (() => void) | null = null

  /** _xs_open: the synthetic bankfull section at the site's drainage area, in its dialog. */
  private async openXs(): Promise<void> {
    this.cancelReport()
    const dl = (this.host.delin() ?? {}).delineation ?? {}
    if (dl.drainage_area_sqkm === null || dl.drainage_area_sqkm === undefined) {
      notify('Delineate a reach first.', { type: 'warning', seconds: 3 })
      return
    }
    const geom = await openGeometry(this.host.data.src, dl)
    if (!geom) return
    this.xsGeom = geom
    const el = showModal(xsModal(geom, null))
    // xs_view, as the server draws it once the dialog's boxes are bound, then on each change
    this.xsRedraw()
    this.xsStop?.()
    this.xsStop = listenBoxes(() => this.xsRedraw())
    el.addEventListener('hidden.bs.modal', () => {
      this.xsStop?.()
      this.xsStop = null
    })
  }

  private xsRedraw(): void {
    const view = document.getElementById('sfari-xs_view')
    if (view) view.innerHTML = outputHtml(xsView(this.xsGeom as XsGeom | null, readBoxes()))
  }

  /** _xs_attach: the solution written into the four hydraulics metrics' evidence. */
  private attachXs(): void {
    const ev = attachEvidence(this.xsGeom as XsGeom | null, readBoxes(), this.evidence)
    if (!ev) {
      notify('Enter a valid slope and discharge first.', { type: 'warning', seconds: 3 })
      return
    }
    this.evidence = ev as Record<string, Evidence>
    this.evidenceRev += 1
    notify('Cross-section results attached to the hydraulics metrics.', { type: 'message', seconds: 4 })
    removeModal()
    if (this.shown()) this.setOutput('fn_panel', this.panel())
  }

  private move(i: number): void {
    this.currentFn = Math.max(0, Math.min(FN_IDS.length - 1, i))
    if (!this.shown()) return
    this.setOutput('fn_nav', fnNav(this.currentFn, this.functionScores))
    this.setOutput('fn_panel', this.panel())
  }

  /** Entries to lose (New's question): any metric or function entry. */
  hasEntries(): boolean {
    return Object.keys(this.metricScores).length > 0 || Object.keys(this.functionScores).length > 0
  }

  anyScored(): boolean {
    return Object.values(this.functionScores).some((v) => v.score !== null && v.score !== undefined)
  }

  // ---------------------------------------------------------------- the report

  private reportSerial = 0
  /** the report being prepared: its request id and what it was asked for (_report_request) */
  private pendingReport: { id: number, delin: Record<string, any>, step: string } | null = null

  /** _finish_report: report-ready.js hears the request settle (opened or not). */
  private finishReport(opened = false): void {
    const req = this.pendingReport
    if (!req) return
    this.pendingReport = null
    shim.send('staf-report-state', { requestId: req.id, busy: false, opened, ns: 'sfari' })
  }

  /** _cancel_report */
  cancelReport(): void {
    if (this.pendingReport) this.finishReport()
  }

  /** _report_title: the scenario shown names the report once there are alternatives. */
  private reportTitle(): string {
    const s = this.scenarios
    return s.hasAlternatives() ? `SFARI Report · ${s.current.name}` : 'SFARI Report'
  }

  /** workbook.summary_info: the site's rows, its Level III ecoregion from the engine's site (else the
   * polygons at its point). */
  private async summaryInfo(d: Record<string, any>): Promise<SummaryInfo> {
    const dl = d.delineation ?? {}
    let l3 = (((d.siteEngine ?? {}).site ?? {}).ecoregionL3 ?? {}).code ?? null
    if (l3 === null && dl.snapped_lat !== null && dl.snapped_lat !== undefined) {
      l3 = (await level3At(this.host.data.src, dl.snapped_lat, dl.snapped_lon) ?? {} as any).code ?? null
    }
    return { app: 'SFARI', reachName: dl.gnis_name, reachLengthFt: dl.reach_length_ft, drainageAreaSqkm: dl.drainage_area_sqkm,
      streamOrder: dl.stream_order, regions: regionsForL3(l3) }
  }

  private async summaryRows(d: Record<string, any>): Promise<[string, string][]> {
    return summaryRows(await this.summaryInfo(d))
  }

  /** _workbook_bytes: the scenario workbook, or the single calculator when it cannot be assembled
   * (never a failed download). */
  private async workbookBytes(): Promise<Uint8Array> {
    const d = this.host.delin() ?? {}
    const entries = this.scenarioEntries()
    const today = todayIso()
    try {
      const { buildWorkbook } = await import('./workbook')
      return await buildWorkbook(d, entries, await this.summaryInfo(d), today)
    } catch (err) {
      console.warn('SFARI: the scenario workbook failed; serving the single calculator', err)
      const ec = entries[0][1]
      return buildCalculator(d, ec.metric_scores ?? {}, ec.function_scores ?? {}, today)
    }
  }

  /** _request_report: the report needs a scored function; it opens once its basin map is drawn. */
  requestReport(): void {
    const d = this.host.delin()
    if (!d) return
    if (!this.anyScored()) {
      notify('Score at least one function before viewing the report.', { type: 'message', seconds: 4 })
      return
    }
    const step = this.host.step()
    if (this.pendingReport && this.pendingReport.delin === d && this.pendingReport.step === step) return
    this.cancelReport()
    const id = ++this.reportSerial
    this.pendingReport = { id, delin: d, step }
    shim.send('staf-report-state', { requestId: id, busy: true, opened: false, ns: 'sfari' })
    void (async () => {
      let minimap = ''
      let rows: [string, string][] = []
      try {
        ;[minimap, rows] = await Promise.all([svg(displaySimplify(d.watershed_geojson, 700), d.reach_geojson), this.summaryRows(d)])
      } catch {
        if (this.pendingReport?.id === id) {
          notify('Could not prepare the report. Please try again.', { type: 'error', seconds: 5 })
          this.finishReport()
        }
        return
      }
      const req = this.pendingReport
      if (!req || req.id !== id) return
      if (this.host.delin() !== d) {
        this.finishReport()
        return
      }
      if (!this.anyScored()) {
        notify('Score at least one function before viewing the report.', { type: 'message', seconds: 4 })
        this.finishReport()
        return
      }
      const summary = summarySection(rows, reportScenario(this.scenarios))
      showModal(reportModal({ delin: d, sc: this.scored(), ms: this.metricScores, fs: this.functionScores, ev: this.evidence, minimap, summary,
        title: this.reportTitle() }))
      this.finishReport(true)
    })()
  }

  /** scored(): every function counts, a blank one as zero. */
  private scored(): Scored {
    const scores = Object.fromEntries(Object.entries(this.functionScores).map(([fid, v]) => [fid, v.score ?? null]))
    return scoreAssessment(scores)
  }

  /** The report's downloads (dl_csv, dl_geojson), named for the scenario shown. */
  private registerDownloads(): void {
    const suffix = () => scenarioSuffix(this.scenarios)
    registerDownload('sfari-dl_csv', { filename: () => `sfari-report${suffix()}.csv`, mime: 'text/csv',
      build: () => buildCsv(this.host.delin() ?? {}, this.metricScores, this.functionScores, this.evidence, this.scored()) })
    registerDownload('sfari-dl_geojson', { filename: () => `sfari-report${suffix()}.geojson`, mime: 'application/geo+json',
      build: () => buildGeojson(this.host.delin() ?? {}, this.functionScores, this.scored()) })
    // the report's PDF and Get Forms' desktop metrics PDF (report.build_pdf, build_desktop_metrics_pdf)
    registerDownload('sfari-dl_pdf', { filename: () => `sfari-report${suffix()}.pdf`, mime: 'application/pdf', build: async () => {
      const { buildPdf } = await import('./reportpdf')
      return buildPdf(this.host.delin() ?? {}, this.metricScores, this.functionScores, this.evidence, this.scored(), reportScenario(this.scenarios))
    } })
    registerDownload('sfari-dl_desktop_metrics', { filename: 'sfari-desktop-metrics.pdf', mime: 'application/pdf', build: async () => {
      const { buildDesktopMetricsPdf } = await import('./reportpdf')
      return buildDesktopMetricsPdf(this.host.delin() ?? {}, this.evidence)
    } })
    // Get Forms' files that never change: the field forms and the blank calculator, as SFARI ships them
    registerDownload('sfari-dl_field_forms', { filename: FIELD_FORMS_FILENAME, mime: 'application/pdf', build: () => pageFile(FIELD_FORMS_URL) })
    registerDownload('sfari-dl_calc_blank', { filename: BLANK_FILENAME, mime: XLSX_MEDIA_TYPE, build: () => pageFile(BLANK_URL) })
    // the completed workbook (Get Forms and the report's footer)
    registerDownload('sfari-dl_calc_filled', { filename: calculatorFilename(), mime: XLSX_MEDIA_TYPE, build: () => this.workbookBytes() })
  }
}

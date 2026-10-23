// EASI's Assessment step: the screening run and the worksheet over the map, as easi/app.py's server
// keeps them. Landing on the step starts the screening once per pick (_autostart_assess), with the
// "Computing metrics" toast; its result is the base the page scores (base_result); overrides, notes,
// observations and the scenarios re-score it instantly (scored()). The page scripts worksheet.js and
// report-edit.js (the Shiny app's own) post every edit as an input; each output is drawn again when
// what Shiny would re-render it on changes.
import { type Child, h, outputHtml, TagList } from '../../app/htm'
import { notify, removeNotification } from '../../app/notify'
import { timed, timedSync } from '../../app/perf'
import { pyFixed } from '../../app/pyfmt'
import { deriveFromStages, FT_PER_M } from '../../engine/geomorph'
import { removeModal, showModal } from '../../shared/modal'
import { actionButton, modal } from '../../shared/shinyui'
import * as shim from '../../app/shim'
import {
  addDialog, BASELINE_ID, compareDialog, type Comparison, DESC_INPUT, deleteDialog, NAME_INPUT, railDelta, scenarioBar, scenarioDialog,
  ScenarioSet, START_BLANK, START_INPUT,
} from '../../shared/scenarios'
import { info, metricAction, metricActions } from '../sfari/markup'
import { CHANNEL_EVOL_ID, ENTRENCHMENT_ID, EXTERNAL_SERVICE, FLOODPLAIN_ENGAGEMENT_ID, REGISTRY } from './adapters'
import { rateMetricsFromStages, scoreContext } from './assess'
import { nars9Index } from './geo'
import {
  fnNav, metricAboutTip, metricLive, methodBody, methodCriteria, observedEditor, observedValue, OBSERVED_INPUTS, output, rateSelect,
  rollupRail, scorecard, stepper,
} from './markup'
import { functions, loadMethod, metricDefinition, metricsById } from './method'
import { resolve } from './methods'
import { ctxFromInputs, type EasiAssessData, prefetch } from './prefetch'
import { reportBody, reportFooter, summaryBlock, summaryRows } from './report'
import { easiComparison, emptyState, normalized, scoredFor, type ScoreState, xsBlock, xsCandidate } from './state'
import { plotly } from './xsplot'
import * as easiSession from '../../session/easi'
import { scenarioSet, scenariosBlock } from '../../session/file'
import { stateFingerprint } from '../../session/guard'
import { scoringMethod } from './identity'
import { buildCsv, buildGeojson } from './exports'
import { displaySimplify } from '../../engine/simplify'
import { svg } from '../../shared/reportmap'
import { pyCopy, pySet } from '../../app/pyjson'
import { fromBase64 } from '../../files/bytes'
import { pageFile, registerDownload } from '../../files/download'
import { scenarioSuffix, XLSX_MEDIA_TYPE } from '../../shared/downloads'
import { available as calculatorAvailable, BLANK_URL, blankFilename, buildFilled, entryCells, filledFilename } from './calculator'
import { todayIso } from '../../files/xlsx/calcfill'
import { formsModal } from './forms'
import {
  buildCrossSection, type CardState, defaultHeights, type Geometry, hasProfile, pullContent, pullProfile, type PullState, reportImageSpec,
  sourceFigure, windowRange, xsCaption, xsEditor, XsPlot, xsReachTable, xsReadonly, xsSelector, xsSummary,
} from './xscard'

export { loadMethod } from './method'

export interface EasiHost {
  data: EasiAssessData
  /** the step shown */
  step(): string
  /** the delineation result (with ctx_inputs), when the pick it came from is still the shown one */
  delin(): Record<string, any> | null
  setStep(step: string): void
}

const ADD_HINT = 'A copy brings its rating changes, cross-section and observations. Blank starts from the screening as computed.'
const XS_METRIC_IDS = new Set([ENTRENCHMENT_ID, FLOODPLAIN_ENGAGEMENT_ID, CHANNEL_EVOL_ID])
const strip = (s: string) => s.replace(/^\s+|\s+$/g, '')
const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x ?? null))
const round2 = (x: number) => Number(pyFixed(x, 2))
const BOXES = ['xs_bankfull', 'xs_lowbank']
type Obj = Record<string, any>

/** A number box's value as Shiny's number binding reads it (blank: null; not a number: null here). */
function boxValue(el: HTMLInputElement | null): number | null {
  const v = (el?.value ?? '').trim()
  if (v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** The report's cross-section image of an export result (xsimage.ts, a data URL): null when the section
 * shown has no profile, or its image cannot be drawn. */
function xsImageUrl(res: Record<string, any>): Promise<string | null> {
  const spec = reportImageSpec(res.report.crossSection)
  return spec ? import('./xsimage').then((m) => m.xsImage(spec)).catch(() => null) : Promise.resolve(null)
}

/** session_io.screened: the result the page scores (the delineation without the run's inputs, with
 * the HUC12, the report and the twelve EROM monthly flows). */
export function screened(delin: Record<string, any>, huc12: string | null, report: Record<string, any>): Record<string, any> {
  // the delineation without the run's inputs; copies keep each number's int or float (dict(**x))
  const merged = pyCopy(delin)
  delete merged.ctx_inputs
  const dl = pyCopy(delin.delineation || {})
  pySet(dl, 'huc12', huc12)
  merged.delineation = dl
  merged.report = report
  const erom = (delin.ctx_inputs || {}).erom
  const flows: number[] = []
  let ok = !!erom && typeof erom === 'object'
  for (let m = 1; ok && m <= 12; m++) {
    const v = erom[`qe_${String(m).padStart(2, '0')}`]
    if (typeof v !== 'number' || !Number.isFinite(v)) ok = false
    else flows.push(v)
  }
  merged.eromMonthly = ok ? flows : null
  return merged
}

export class EasiWorksheet {
  base: Record<string, any> | null = null
  currentFn = 0
  private status: 'initial' | 'running' | 'error' | 'success' = 'initial'
  /** the pick the last run was launched for (_analysis_runs["assess"]); a new pick bumps pickGen */
  private launched: number | null = null
  private pickGen = 0
  private progress = { done: 0, total: 0, waiting: {} as Record<string, number> }
  private stage = ''
  private poll: ReturnType<typeof setInterval> | null = null
  private runGen = 0
  overrides: Record<string, string> = {}
  notes: Record<string, string> = {}
  observed: Record<string, Record<string, any>> = {}
  private geomOwned = new Set<string>()
  private geomText: Record<string, string> = {}
  private geomScoring: Record<string, any> = {}
  private geomReason = 'edited'
  private xsSel: number | null = null
  scenarios = new ScenarioSet()
  private dialog: 'new' | 'edit' | null = null
  private scoredCache: Record<string, any> | null = null
  // the cross-section card: the unit (_xs_unit_prev and the radio), the height boxes' values as they
  // were last read (input.xs_bankfull / xs_lowbank; null before the card was first shown), the heights
  // the server keeps (_xs_heights), a pull from 3DEP going on, and the plot
  private xsUnit = 'ft'
  private xsIn: [number | null, number | null] | null = null
  private xsHeights: [number, number] | null = null
  private xsPulling: number | null = null
  private xsPullError: Record<number, string> = {}
  private xsUnpublished = false
  private readonly xsPlot = new XsPlot()
  private boxTimer: ReturnType<typeof setTimeout> | null = null
  /** the screening's reads made ahead for a delineation (prefetchAhead) */
  private ahead: { delin: Record<string, any>, p: Promise<{ ctx: Record<string, any>, cross: Obj | null }> } | null = null

  private reportCounter = 0
  /** the report being prepared (its request id), until it opens */
  private pendingReport: number | null = null
  /** the open report's cross-section drawn again (reportXsDraw): the latest redraw wins */
  private reportXsGen = 0

  constructor(private host: EasiHost) {
    // the report's downloads (each named for the scenario shown)
    const suffix = () => scenarioSuffix(this.scenarios)
    registerDownload('easi-dl_csv', { filename: () => `easi-report${suffix()}.csv`, mime: 'text/csv', build: () => {
      const res = this.exportResult()
      return res ? buildCsv(res) : null
    } })
    registerDownload('easi-dl_geojson', { filename: () => `easi-report${suffix()}.geojson`, mime: 'application/geo+json', build: () => {
      const res = this.exportResult()
      return res ? buildGeojson(res) : null
    } })
    // the report's PDF (report.build_pdf): the summary plot and the cross-section drawn as matplotlib lays them out
    registerDownload('easi-dl_pdf', { filename: () => `easi-report${suffix()}.pdf`, mime: 'application/pdf', build: async () => {
      const res = this.exportResult()
      if (!res) return null
      const [{ buildPdf }, { summaryPlotPng }, xsUrl] = await Promise.all([import('./reportpdf'), import('./summaryplot'), xsImageUrl(res)])
      const xsPng = xsUrl ? fromBase64(xsUrl.slice(xsUrl.indexOf(',') + 1)) : null
      return buildPdf(res, this.reportScenario(), { summaryPng: await summaryPlotPng(res.report), xsPng })
    } })
    // Get Forms: the blank workbook, byte for byte (the Shiny app's build_blank, copied by parity/copy_page.py)
    registerDownload('easi-dl_forms_blank', { filename: () => blankFilename(), mime: XLSX_MEDIA_TYPE,
      build: () => (calculatorAvailable() ? pageFile(BLANK_URL) : null) })
    // Get Forms: the desktop metrics list as a PDF (report.build_desktop_metrics_pdf)
    registerDownload('easi-dl_forms_pdf', { filename: 'easi-desktop-metrics.pdf', mime: 'application/pdf', build: async () => {
      const res = this.exportResult()
      if (!res) return null
      const [{ buildDesktopMetricsPdf }, cells] = await Promise.all([import('./reportpdf'), calculatorAvailable() ? entryCells() : Promise.resolve({})])
      return buildDesktopMetricsPdf(res, cells)
    } })
    // the completed workbook: Get Forms' and the report footer's copy
    for (const id of ['easi-dl_forms_filled', 'easi-dl_workbook']) {
      registerDownload(id, { filename: () => filledFilename(), mime: XLSX_MEDIA_TYPE, build: () => this.workbookBytes() })
    }
    // the cross-section card's height boxes post as Shiny's number inputs do: typing after a 250 ms
    // pause, a change at once; its unit as a radio group; its Pull from 3DEP button, and the report's
    const box = (e: Event): HTMLInputElement | null => {
      const el = e.target as HTMLInputElement | null
      return el && (el.id === 'easi-xs_bankfull' || el.id === 'easi-xs_lowbank') ? el : null
    }
    for (const type of ['input', 'keyup']) {
      document.addEventListener(type, (e) => {
        if (!box(e)) return
        if (this.boxTimer) clearTimeout(this.boxTimer)
        this.boxTimer = setTimeout(() => this.boxesPosted(), 250)
      })
    }
    document.addEventListener('change', (e) => {
      const el = e.target as HTMLInputElement | null
      if (box(e)) {
        if (this.boxTimer) clearTimeout(this.boxTimer)
        this.boxesPosted()
      } else if (el && el.name === 'easi-xs_unit' && el.checked) this.convertUnits(el.value)
    })
    document.addEventListener('click', (e) => {
      const b = (e.target as Element | null)?.closest?.('#easi-xsection_plot [data-xs-pull], #easi-report_xs [data-xs-pull]')
      if (b && !(b as HTMLButtonElement).disabled) void this.pull()
    })
  }

  private el(id: string): HTMLElement | null {
    return document.getElementById(`easi-${id}`)
  }

  private shown(): boolean {
    const s = this.host.step()
    return s === 'assess' || s === 'report'
  }

  /** A new pick, a reset or a new delineation: the screening and every edit go (_invalidate_analysis). */
  reset(): void {
    this.pickGen += 1
    this.runGen += 1
    this.ahead = null
    this.stopPoll()
    this.status = 'initial'
    this.launched = null
    this.stage = ''
    this.base = null
    this.clearEdits()
    this.currentFn = 0
    this.scenarios = new ScenarioSet()
  }

  private clearEdits(): void {
    this.overrides = {}
    this.notes = {}
    this.observed = {}
    this.geomOwned = new Set()
    this.geomText = {}
    this.geomScoring = {}
    this.geomReason = 'edited'
    this.xsSel = null
    this.scoredCache = null
    this.xsUnit = 'ft'
    this.xsIn = null
    this.xsHeights = null
    this.xsPulling = null
    this.xsPullError = {}
    this.xsPlot.drop()
  }

  // ---------------------------------------------------------------- the screening run

  /** _autostart_assess: on the Assessment step with a delineation and no screening yet, once per pick. */
  maybeAssess(): void {
    if (this.host.step() !== 'assess') return
    const d = this.host.delin()
    if (!d?.ctx_inputs || this.status === 'running') return
    if (this.base !== null || this.launched === this.pickGen) return
    this.launch(d)
  }

  /** _run_screening: the Basin's Run screening goes to the worksheet (a failed run may start again). */
  runScreening(): void {
    if (this.base === null && this.status !== 'running') this.launched = null
    this.currentFn = 0
    this.host.setStep('assess')
  }

  /** The run is going (stepping away keeps it). */
  get running(): boolean {
    return this.status === 'running'
  }

  private launch(d: Record<string, any>): void {
    const gen = ++this.runGen
    const n = Object.keys(REGISTRY).length
    this.progress = { done: 0, total: n, waiting: {} }
    this.launched = this.pickGen
    this.status = 'running'
    this.stage = `Computing metrics… 0/${n}`
    notify(`Computing metrics… 0/${n}, please wait`, { id: 'stage', type: 'message', seconds: 0 })
    this.stopPollOnly()
    this.poll = setInterval(() => {
      if (this.status !== 'running' || gen !== this.runGen) return
      const { done, total, waiting } = this.progress
      const names = Object.keys(waiting).sort()
      const label = `Computing metrics… ${done}/${total}` + (names.length ? `, waiting on ${names.join(', ')}` : '')
      this.stage = label
      const slot = this.el('assess_stage_label')
      if (slot && slot.textContent !== label) slot.textContent = label
      notify(label + ', please wait', { id: 'stage', type: 'message', seconds: 0 })
    }, 300)
    this.render()
    void timed('easi:run', this.run(d)).then((res) => {
      if (gen !== this.runGen) return
      this.done(d, res)
    }, () => {
      if (gen !== this.runGen) return
      this.done(d, { status: 'error', message: 'Metric computation failed. Please try again.' })
    })
  }

  /** The screening's reads started ahead, once the Basin step shows a delineation: the Shiny EASI reads
   * when the run starts; here they are made while the assessor looks at the Basin step, so Run screening
   * finds them read. Nothing is shown or scored. */
  prefetchAhead(): void {
    const d = this.host.delin()
    if (!d?.ctx_inputs || this.base !== null || this.status === 'running' || this.ahead?.delin === d) return
    const p = timed('easi:prefetch-ahead', this.prepare(d))
    p.catch(() => {})
    this.ahead = { delin: d, p }
  }

  /** What the screening reads: the method, the prefetch (assessment.assess's), the sections drawn. */
  private async prepare(d: Record<string, any>): Promise<{ ctx: Record<string, any>, cross: Obj | null }> {
    await timed('easi:method', loadMethod(this.host.data.src))
    await timed('easi:nars9', nars9Index())
    const ctx = ctxFromInputs(copy(d.ctx_inputs))
    await prefetch(this.host.data, ctx, Number((d.input || {}).reach_length_ft) || 1000.0)
    // the sections the cross-section metrics score on, the stored profiles with them (assess's crossSection)
    const cross = await timed('easi:cross-section-card',
      buildCrossSection(this.host.data, ctx.extras.reach_geomorph || {}, ctx.extras.xsPicked, ctx.slope, ctx.fcode).catch(() => null))
    return { ctx, cross }
  }

  /** pipeline.assess_only from the bundle: the prefetch (started ahead when it was), then the adapters and
   * the rollup. */
  private async run(d: Record<string, any>): Promise<Record<string, any>> {
    for (const mid of Object.keys(EXTERNAL_SERVICE)) this.progress.waiting[EXTERNAL_SERVICE[mid]] = (this.progress.waiting[EXTERNAL_SERVICE[mid]] ?? 0) + 1
    const ahead = this.ahead?.delin === d ? this.ahead.p : null
    this.ahead = null
    const { ctx, cross } = await timed('easi:prefetch', ahead ?? this.prepare(d))
    this.progress.waiting = {}
    const scoredReport = timedSync('easi:score', () => scoreContext(ctx as any, null, null, () => { this.progress.done += 1 }))
    // the cross-section block takes its place before the basin and the strata, as assess.py builds the report
    const report: Obj = {}
    for (const [k, v] of Object.entries(scoredReport)) {
      if (k === 'basin' && cross) report.crossSection = cross
      if (k !== 'crossSection') report[k] = v
    }
    if (cross && !('crossSection' in report)) report.crossSection = cross
    else if (!cross && 'crossSection' in scoredReport) report.crossSection = scoredReport.crossSection
    return { status: 'ok', report, huc12: ctx.huc12, xsUnpublished: !cross && ctx.extras.xsPicked?.covered === false,
      metricAnchors: ctx.extras.siteAnchor?.metricAnchors ?? null }
  }

  private done(d: Record<string, any>, res: Record<string, any>): void {
    this.stopPoll()
    this.stage = ''
    if (res.status !== 'ok') {
      this.status = 'error'
      notify(res.message || 'Analysis error', { type: 'error', seconds: 8 })
      this.renderPanel()
      return
    }
    this.status = 'success'
    // assess stamps a routed site's per-metric table onto the anchor of the inputs it ran on, the same
    // object as the delineation's own anchor while the pick is the page's (the saved file carries it)
    if (res.metricAnchors && d.ctx_inputs?.siteAnchor) {
      if (d.siteAnchor === d.ctx_inputs.siteAnchor) d.siteAnchor.metricAnchors = res.metricAnchors
      d.ctx_inputs.siteAnchor.metricAnchors = res.metricAnchors
    }
    this.base = screened(d, res.huc12 ?? null, res.report)
    this.clearEdits()
    this.xsUnpublished = !!res.xsUnpublished
    // the cross-section card's plotting library, read now so the card draws at once when shown
    void plotly().catch(() => {})
    this.currentFn = 0
    this.scenarios = new ScenarioSet()
    if (this.shown()) this.render()
    // the Shiny EASI opens the screening report here (_show_report_modal)
    this.requestReport()
  }

  private stopPollOnly(): void {
    if (this.poll) clearInterval(this.poll)
    this.poll = null
  }

  private stopPoll(): void {
    this.stopPollOnly()
    removeNotification('stage')
  }

  // ---------------------------------------------------------------- scoring state

  private scoreState(): ScoreState {
    return { overrides: { ...this.overrides }, observed: copy(this.observed), geomOwned: [...this.geomOwned].sort(), geomText: { ...this.geomText },
      geomScoring: { ...this.geomScoring }, geomReason: this.geomReason, xsSel: this.xsSel, xsHeights: null }
  }

  /** scored(): the base re-scored with the shown scenario's state. */
  scored(): Record<string, any> | null {
    if (!this.base) return null
    this.scoredCache ??= scoredFor(this.base.report, this.scoreState())
    return this.scoredCache
  }

  private changed(): void {
    this.scoredCache = null
  }

  /** export_result: the scored report with each row's note (and the shown cross-section). */
  exportResult(): Record<string, any> | null {
    const sc = this.scored()
    if (!this.base || !sc) return null
    // copies as dict(**x) makes them: each key keeps its place, each number its int or float
    const rows = sc.metricRows.map((r: Record<string, any>) => {
      const row = pyCopy(r)
      row.userNote = this.notes[r.metricId] ?? ''
      return row
    })
    const report = pyCopy(sc)
    report.metricRows = rows
    report.crossSection = this.xsRender() || sc.crossSection
    const out = pyCopy(this.base)
    out.report = report
    return out
  }

  // ---------------------------------------------------------------- the cross-section card

  private xsCross(): Obj {
    return (this.base?.report || {}).crossSection || {}
  }

  private xsCands(): Obj[] {
    return this.xsCross().candidates || []
  }

  private xsDefault(): number {
    return Math.trunc(Number(this.xsCross().selected || 0))
  }

  private xsSelIdx(): number {
    const cands = this.xsCands()
    if (!cands.length) return 0
    const s = this.xsSel === null ? this.xsDefault() : Math.trunc(this.xsSel)
    return Math.min(Math.max(s, 0), cands.length - 1)
  }

  /** The section shown, with its profile or not. */
  private xsShown(): Obj | null {
    const cands = this.xsCands()
    return cands.length ? cands[this.xsSelIdx()] : this.xsCross().geom ?? null
  }

  /** _xs_block: the section shown when it has its profile. */
  private xsBlock(): Obj | null {
    const b = this.xsShown()
    return hasProfile(b) ? b : null
  }

  private perM(unit = this.xsUnit): number {
    return unit === 'ft' ? FT_PER_M : 1.0
  }

  /** _geom_edited: the boxes differ from the section's own heights (display unit, display precision). */
  private geomEdited(): boolean {
    const block = this.xsBlock()
    if (!block || !this.xsIn) return false
    const [bf, lb] = this.xsIn
    if (bf === null || lb === null) return false
    const [bfDef, lbDef] = defaultHeights(block, this.perM())
    return Math.abs(bf - bfDef) > 0.005 || Math.abs(lb - lbDef) > 0.005
  }

  /** current_geometry: the stages from the boxes (the section's own when they are untouched), or null. */
  private currentGeometry(): Geometry | null {
    const block = this.xsBlock()
    if (!block || !this.xsIn) return null
    const [bf, lb] = this.xsIn
    if (bf === null || lb === null) return null
    if (!this.geomEdited()) return { block, unit: this.xsUnit, bankfull_stage: block.bankfull_stage, floodplain_stage: block.floodplain_stage }
    const perM = this.perM()
    return { block, unit: this.xsUnit, bankfull_stage: block.thalweg + bf / perM, floodplain_stage: block.thalweg + lb / perM }
  }

  /** What the boxes show when the card is drawn (_xs_editor: the server's heights, else the boxes', else
   * the section's own). */
  private xsBoxes(): [number, number] {
    const shown = this.xsShown()!
    let [bf, lb]: [number | null, number | null] = this.xsIn ? [this.xsIn[0], this.xsIn[1]] : [null, null]
    if (this.xsHeights) [bf, lb] = this.xsHeights
    const def = defaultHeights(shown, this.perM())
    return [bf ?? def[0], lb ?? def[1]]
  }

  private xsCardState(): CardState | null {
    const shown = this.xsShown()
    if (!shown) return null
    const cands = this.xsCands()
    const sel = this.xsSelIdx()
    const block = this.xsBlock()
    const g = this.currentGeometry()
    return {
      cands: cands.length ? cands : [shown], sel: cands.length ? sel : 0, def: this.xsDefault(), reach: this.xsCross().reach ?? null,
      unit: this.xsUnit, boxes: this.xsBoxes(), geometry: g, edited: this.geomEdited(),
      windowRange: block ? windowRange(sourceFigure(block, g)) : '', pulling: this.xsPulling === sel, pullError: this.xsPullError[sel] ?? null,
    }
  }

  /** _set_geom_metrics: own (or release) the three cross-section metrics' ratings. */
  private setGeomMetrics(block: Obj | null, bankfullStage: number | null, floodplainStage: number | null, own: boolean, reason = 'edited'): void {
    const cur = { ...this.overrides }
    const texts = { ...this.geomText }
    const traces = { ...this.geomScoring }
    const owned = new Set(this.geomOwned)
    if (own && block) {
      this.geomReason = reason
      const derived = rateMetricsFromStages(block, bankfullStage as number, floodplainStage as number, reason,
        ((this.base || {}).report || {}).strata ?? null)
      const newOwned = new Set<string>()
      for (const [mid, inf] of Object.entries(derived)) {
        if (inf.rating) {
          cur[mid] = inf.rating
          texts[mid] = inf.valueText ?? ''
          traces[mid] = inf.scoring
          newOwned.add(mid)
        }
      }
      for (const mid of owned) {
        if (newOwned.has(mid)) continue
        delete cur[mid]
        delete texts[mid]
        delete traces[mid]
      }
      this.overrides = cur
      this.geomText = texts
      this.geomScoring = traces
      this.geomOwned = newOwned
    } else if (owned.size) {
      // back to the default section, unedited: released (the traces stay, as the Shiny EASI keeps them)
      for (const mid of owned) {
        delete cur[mid]
        delete texts[mid]
      }
      this.overrides = cur
      this.geomText = texts
      this.geomOwned = new Set()
    }
    this.changed()
  }

  /** The boxes posted (typed, stepped or changed): _xs_rerate when either moved. */
  private boxesPosted(): void {
    this.boxTimer = null
    const bf = boxValue(this.el('xs_bankfull') as HTMLInputElement | null)
    const lb = boxValue(this.el('xs_lowbank') as HTMLInputElement | null)
    if (this.xsIn && this.xsIn[0] === bf && this.xsIn[1] === lb) return
    this.xsIn = [bf, lb]
    this.xsRerate()
  }

  /** _xs_rerate: an edit (or a section other than the default) drives the three cross-section metrics. */
  private xsRerate(): void {
    if (!this.xsBlock()) return
    const [bf, lb] = this.xsIn ?? [null, null]
    if (bf !== null && lb !== null) this.xsHeights = [bf, lb]
    const g = this.currentGeometry()
    const edited = !!(g && this.geomEdited())
    const own = !!(g && (edited || this.xsSelIdx() !== this.xsDefault()))
    this.setGeomMetrics(g ? g.block : null, g ? g.bankfull_stage : null, g ? g.floodplain_stage : null, own, edited ? 'edited' : 'scrolled')
    this.xsRefresh(false)
    this.scoredChanged()
  }

  /** _select: the previous or next section (wrapping), the boxes reset to its heights, the metrics re-rated. */
  private select(delta: number): void {
    const cands = this.xsCands()
    const n = cands.length
    if (n < 2) return
    const nw = (((this.xsSelIdx() + delta) % n) + n) % n
    this.xsSel = nw
    const block = cands[nw]
    const heights = defaultHeights(block, this.perM())
    this.xsIn = [heights[0], heights[1]]
    this.xsHeights = heights
    this.setGeomMetrics(block, block.bankfull_stage, block.floodplain_stage, nw !== this.xsDefault(), 'scrolled')
    this.xsRefresh(true)
    this.scoredChanged()
  }

  /** _xs_convert_units: the boxes and the kept heights in the new unit (the converted boxes post again). */
  private convertUnits(unit: string): void {
    const old = this.xsUnit
    if (unit === old || !this.xsShown()) return
    const factor = old === 'ft' && unit === 'm' ? 1.0 / FT_PER_M : old === 'm' && unit === 'ft' ? FT_PER_M : 1.0
    if (this.xsIn) this.xsIn = [this.xsIn[0] === null ? null : round2(this.xsIn[0] * factor), this.xsIn[1] === null ? null : round2(this.xsIn[1] * factor)]
    if (this.xsHeights) this.xsHeights = [round2(this.xsHeights[0] * factor), round2(this.xsHeights[1] * factor)]
    this.xsUnit = unit
    this.setBoxes()
    if (this.xsBlock()) this.xsRerate()
    else this.xsRefresh(true)
  }

  /** The boxes show the values last read (an update_numeric). */
  private setBoxes(): void {
    const shown = this.xsShown()
    if (!shown || !this.xsIn) return
    BOXES.forEach((id, i) => {
      const el = this.el(id) as HTMLInputElement | null
      const v = this.xsIn![i]
      if (el) {
        el.value = v === null ? '' : String(v)
        el.disabled = !hasProfile(shown)
      }
    })
  }

  /** _show_heights: the boxes point at a scenario's cross-section (its edited heights, else the shown
   * section's own) without re-rating: its ratings come with it. */
  private showHeights(st: ScoreState): void {
    const report = (this.base || {}).report || {}
    const block = xsBlock(report, st.xsSel)
    if (!block) {
      this.xsHeights = null
      const cand = xsCandidate(report, st.xsSel)
      if (cand && this.xsIn) this.xsIn = defaultHeights(cand, this.perM())
      return
    }
    const perM = this.perM()
    const hh = st.xsHeights
    const target: [number, number] = hh ? [round2(hh.bankfull_m * perM), round2(hh.lowbank_m * perM)] : defaultHeights(block, perM)
    this.xsHeights = target
    if (this.xsIn) this.xsIn = [target[0], target[1]]
  }

  /** xs_render: the cross-section the report shows (the screening's own, or the shown section at its
   * current heights and unit, keeping the reach's sections and statistics). */
  private xsRender(): Obj | null {
    if (!this.base) return null
    const baseXs = this.base.report.crossSection || {}
    const shown = this.xsShown()
    const g = this.currentGeometry()
    const edited = this.geomEdited()
    const sel = this.xsSelIdx()
    const def = this.xsDefault()
    const carry = { candidates: baseXs.candidates, reach: baseXs.reach, n_transects: baseXs.n_transects, selected: sel, default: def }
    if (!g) {
      // a section without its profile, scrolled to: its own numbers (it has no plot yet)
      if (!shown || hasProfile(shown) || sel === def) return baseXs
      return { geom: shown, entrenchment_ratio: shown.entrenchment_ratio, bank_height_ratio: shown.bank_height_ratio,
        caption: xsCaption(shown.entrenchment_ratio, shown.bank_height_ratio, shown.division, false), ...carry }
    }
    if (!edited && g.unit === 'ft' && sel === def) return baseXs
    const block = g.block
    // the stages and unit the report's image draws (cross_section_from_stages's PNG)
    const drawn = { bankfull_stage: g.bankfull_stage, floodplain_stage: g.floodplain_stage, unit: g.unit }
    if (edited) {
      const d = deriveFromStages(block.stations, block.elevs, block.thalweg, g.bankfull_stage, g.floodplain_stage)
      return { geom: block, entrenchment_ratio: d.entrenchment_ratio, bank_height_ratio: d.bank_height_ratio,
        caption: xsCaption(d.entrenchment_ratio, d.bank_height_ratio, block.division, true), ...carry, drawn }
    }
    return { geom: block, entrenchment_ratio: block.entrenchment_ratio, bank_height_ratio: block.bank_height_ratio,
      caption: xsCaption(block.entrenchment_ratio, block.bank_height_ratio, block.division, false), ...carry, drawn }
  }

  /** The card (_xs_editor); the boxes are read from now on. */
  private xsEditorOut(): Child {
    const st = this.xsCardState()
    if (st && !this.xsIn) {
      this.xsIn = [st.boxes[0], st.boxes[1]]
      if (this.xsBlock()) this.xsHeights = [st.boxes[0], st.boxes[1]]
    }
    return xsEditor(st, this.xsUnpublished)
  }

  /** The card's outputs drawn again (xs_summary, xs_selector, xs_reach_table, xs_window_range, the plot),
   * with the boxes and the unit when ``boxes``. */
  private xsRefresh(boxes: boolean): void {
    if (!this.shown() || !document.querySelector('#easi-fn_panel .easi-xs-in-card')) return
    const st = this.xsCardState()
    if (!st) return
    const block = st.cands[st.sel]
    this.setOutput('xs_summary', xsSummary(block, st.geometry, st.unit, st.edited, st.reach))
    this.setOutput('xs_selector', xsSelector(st.cands, st.sel, st.def))
    this.setOutput('xs_reach_table', xsReachTable(st.cands, st.sel, st.def, st.reach))
    const win = this.el('xs_window_range')
    if (win && win.textContent !== st.windowRange) win.textContent = st.windowRange
    if (boxes) {
      this.setBoxes()
      document.querySelectorAll<HTMLInputElement>('input[name="easi-xs_unit"]').forEach((r) => { r.checked = r.value === this.xsUnit })
    }
    this.xsDraw(st)
  }

  /** The plot of the shown section (in place), or the button that pulls its profile. */
  private xsDraw(st: CardState | null = this.xsCardState()): void {
    const box = this.el('xsection_plot')
    if (!box || !st) {
      this.xsPlot.drop()
      return
    }
    const block = st.cands[st.sel]
    if (!hasProfile(block)) {
      this.xsPlot.drop()
      box.innerHTML = outputHtml(pullContent(st.pulling, st.pullError))
      return
    }
    if (box.querySelector('.easi-xs-pull')) box.innerHTML = ''
    void timed('xs:plot', this.xsPlot.show(sourceFigure(block, st.geometry))).catch(() => {})
  }

  /** Pull from 3DEP (the card's button or the report's): the shown section's profile, read as the grid
   * build read it. */
  private async pull(): Promise<void> {
    const i = this.xsSelIdx()
    const block = this.xsCands()[i]
    if (!block || hasProfile(block) || this.xsPulling !== null) return
    const base = this.base
    this.xsPulling = i
    delete this.xsPullError[i]
    this.xsDraw()
    void this.reportXsDraw()
    let ok = false
    try {
      ok = await timed('xs:pull', pullProfile(this.host.data, block))
    } catch {
      ok = false
    }
    if (this.base !== base) return
    this.xsPulling = null
    if (!ok) this.xsPullError[i] = 'The 3DEP pull did not work. Try again.'
    if (ok && i === this.xsSelIdx()) {
      const heights = defaultHeights(block, this.perM())
      this.xsIn = [heights[0], heights[1]]
      this.xsHeights = heights
    }
    this.changed()
    void this.reportXsDraw()
    if (!this.shown() || i !== this.xsSelIdx()) return
    this.xsRefresh(true)
  }

  /** The report's pull state for the section it shows (the shown one). */
  private reportPull(): PullState {
    const sel = this.xsSelIdx()
    return { pulling: this.xsPulling === sel, error: this.xsPullError[sel] ?? null }
  }

  /** The open report's cross-section drawn again, as a pull from it or the card starts or ends: the shown
   * section's image, or its Pull from 3DEP button. Nothing when no report is open. */
  private async reportXsDraw(): Promise<void> {
    if (!document.getElementById('easi-report_xs')) return
    const res = this.exportResult()
    if (!res) return
    const gen = ++this.reportXsGen
    const png = await xsImageUrl(res)
    const wrap = document.getElementById('easi-report_xs')
    if (gen !== this.reportXsGen || !wrap) return
    wrap.outerHTML = outputHtml(xsReadonly(res.report, png, this.reportPull()))
  }

  private curFn(): Record<string, any> {
    const fns = functions()
    return fns[Math.max(0, Math.min(fns.length - 1, this.currentFn))]
  }

  private metricOf(fid: string): Record<string, any> {
    return Object.values(metricsById()).find((m: any) => m.functionId === fid) ?? {}
  }

  private curRow(sc: Record<string, any>): [string, Record<string, any>] {
    const mid = this.metricOf(this.curFn().id).metricId
    return [mid, sc.metricRows.find((r: any) => r.metricId === mid) ?? {}]
  }

  // ---------------------------------------------------------------- outputs

  /** worksheet: the overlay on the Assessment and Report steps. */
  render(): void {
    const slot = this.el('worksheet')
    if (!slot) return
    if (!this.shown()) {
      if (slot.innerHTML) slot.innerHTML = ''
      return
    }
    const ws = h.div(
      h.div(
        h.div('EASI · Assessment', { class: 'easi-pane-head' }),
        h.div(stepper(this.host.step()), { class: 'sfari-nav-steps' }),
        h.button('Get Forms', { 'data-forms': '1', type: 'button',
          title: "The desktop metrics list, and the Excel calculator completed with this site's values or blank" }, { class: 'sfari-btn sfari-nav-desktop' }),
        output('fn_nav', this.nav()),
        { class: 'sfari-nav' }),
      h.div(output('fn_panel', this.panel()), { class: 'sfari-fnpanel' }),
      h.div(output('scenario_bar', this.scenarioBarOut()), output('rollup_rail', this.rail()), { class: 'sfari-rollup' }),
      { class: 'sfari-worksheet' })
    slot.innerHTML = outputHtml(ws)
    this.xsMount()
  }

  /** After the function panel is drawn: the cross-section card's plot, when the card is shown. */
  private xsMount(): void {
    if (document.querySelector('#easi-fn_panel .easi-xs-in-card')) this.xsDraw()
    else this.xsPlot.drop()
  }

  private setOutput(id: string, content: Child): void {
    const el = this.el(id)
    if (el) el.innerHTML = outputHtml(content)
  }

  /** fn_nav */
  private nav(): Child {
    const sc = this.scored()
    return fnNav(functions(), (sc || {}).functionScores || {}, this.currentFn)
  }

  /** fn_panel: the computing state, or the shown function's metric card, its score and the footer. */
  private panel(): Child {
    if (!this.base) {
      if (this.status === 'running' || this.status === 'initial') {
        return h.div(h.div({ class: 'easi-spinner' }),
          h.span(this.stage || 'Computing metrics…', { id: 'easi-assess_stage_label', class: 'shiny-text-output', 'aria-live': 'polite' }),
          h.div("The screening report opens automatically when it's ready.", { class: 'easi-fn-compute-sub' }),
          { class: 'easi-fn-compute' })
      }
      if (this.status === 'error') {
        return h.div(h.p('Metric computation failed.'), h.button('Retry', { 'data-step': 'assess', type: 'button' }, { class: 'sfari-btn primary' }),
          { class: 'easi-fn-compute' })
      }
      return h.div('Run a screening from the Basin step.', { class: 'easi-instr' })
    }
    const fns = functions()
    const idx = Math.max(0, Math.min(fns.length - 1, this.currentFn))
    const fn = fns[idx]
    const meta = this.metricOf(fn.id)
    const mid = meta.metricId
    const statement = metricDefinition(mid, meta.metricStatement ?? '')
    const isXs = XS_METRIC_IDS.has(mid)
    const note0 = this.notes[mid] ?? ''
    const brow = (this.base.report.metricRows || []).find((r: any) => r.metricId === mid) ?? {}
    const scoringPanel = this.scoringPanel(mid, brow.scoring || {})
    const hasNote = !!note0.trim()
    const extras = [observedEditor(mid, this.observed[mid]), isXs ? this.xsEditorOut() : null].filter((x) => x !== null)
    const sc = this.scored()!
    const [, row] = this.curRow(sc)
    const card = h.div(
      h.div(h.span('1', { class: 'sfari-step-num' }), h.span('Metric', { class: 'sfari-sec-title' }), { class: 'sfari-sec-lbl' }),
      h.div(
        h.div(
          h.div(h.span(meta.name ?? '', { class: 'staf-metric-name' }), info(null, metricAboutTip(meta)), { class: 'staf-metric-head' }),
          statement ? h.div(statement, { class: 'staf-metric-desc' }) : null,
          output('fn_metric_live', metricLive(mid, row, this.observed[mid])),
          metricActions(
            scoringPanel ? metricAction('scoring', 'Scoring', { title: 'How this metric is scored' }) : null,
            metricAction('note', 'Note', { on: hasNote, has: hasNote, title: 'Add a note' })),
          { class: 'staf-metric-main' }),
        output('fn_metric_rate', rateSelect(mid, row), 'staf-metric-input'),
        scoringPanel,
        h.div(h.textarea(note0, { class: 'staf-metric-note easi-note-ta', 'data-mid': mid, rows: '2', placeholder: 'Note (optional)',
          'aria-label': `Note: ${meta.name ?? ''}` }), { 'data-panel': 'note' }, { class: 'staf-metric-panel' }),
        ...extras.map((x) => h.div(x, { class: 'easi-card-wide' })),
        { class: 'staf-metric' + (hasNote ? ' show-note' : '') }),
      { class: 'sfari-ev-card easi-metric-card' })
    const footer = h.div(
      h.div(h.button('‹ Previous', { 'data-nav': '-1', type: 'button', ...(idx === 0 ? { disabled: 'disabled' } : {}) }, { class: 'sfari-btn' }),
        { class: 'sfari-foot-left' }),
      h.div({ class: 'sfari-foot-status' }),
      h.button(idx < fns.length - 1 ? 'Next function ›' : 'Done', { 'data-nav': '1', type: 'button' }, { class: 'sfari-btn primary' }),
      { class: 'sfari-nav-actions' })
    return h.div(
      h.div(h.span(`Function ${idx + 1} of ${fns.length} · ${fn.category ?? ''}`, { class: 'easi-fn-eyebrow' }), h.span(fn.name, { class: 'easi-fn-name' }),
        { class: 'easi-fn-head' }),
      card,
      output('fn_scorecard', scorecard(row, meta, fn)),
      h.div(footer, { class: 'sfari-fn-footer' }),
      { class: 'sfari-fnpanel-inner' })
  }

  /** _scoring_panel: the Scoring panel's skeleton with its two nested outputs. */
  private scoringPanel(mid: string, trace: Record<string, any>): Child {
    const method = resolve(mid, trace.methodKey, trace.context)
    if (method === null) return null
    return h.div(
      h.div('Criteria', { class: 'staf-panel-title' }),
      output('method_criteria', this.criteriaOut()),
      h.div("How it's computed", { class: 'staf-panel-title' }),
      output('method_body', this.bodyOut()),
      { 'data-panel': 'scoring', 'data-mid': mid }, { class: 'staf-metric-panel' })
  }

  /** _cur_method: the shown row's method, from its (scored) trace. */
  private curMethod() {
    const sc = this.scored()
    if (!sc) return null
    const [mid, row] = this.curRow(sc)
    const trace = row.scoring || {}
    const method = resolve(mid, trace.methodKey, trace.context)
    if (method === null) return null
    const values = Object.fromEntries((trace.inputs || []).map((x: any) => [x.key, x.value ?? null]))
    return { mid, row, method, values }
  }

  private criteriaOut(): Child {
    const c = this.curMethod()
    return c ? methodCriteria(c.row, metricsById()[c.mid]?.criteria ?? null) : null
  }

  private bodyOut(): Child {
    const c = this.curMethod()
    return c ? methodBody(c.method, c.row, c.values) : null
  }

  /** rollup_rail */
  private rail(): Child {
    const sc = this.scored()
    if (!sc) return null
    return rollupRail(sc, this.railDelta())
  }

  /** scenario_bar */
  private scenarioBarOut(): Child {
    return this.base ? scenarioBar(this.scenarios) : null
  }

  /** What scored() feeds: the list's dots, the shown row, its panels and score, and the rail. */
  private scoredChanged(): void {
    this.changed()
    if (!this.shown() || !this.base) return
    const sc = this.scored()!
    const [mid, row] = this.curRow(sc)
    this.setOutput('fn_nav', this.nav())
    this.setOutput('fn_metric_live', metricLive(mid, row, this.observed[mid]))
    this.setOutput('fn_metric_rate', rateSelect(mid, row))
    this.setOutput('method_body', this.bodyOut())
    this.setOutput('method_criteria', this.criteriaOut())
    this.setOutput('fn_scorecard', scorecard(row, this.metricOf(this.curFn().id), this.curFn()))
    this.setOutput('rollup_rail', this.rail())
  }

  private renderPanel(): void {
    if (!this.shown()) return
    this.setOutput('fn_panel', this.panel())
    this.xsMount()
  }

  // ---------------------------------------------------------------- scenarios

  /** _capture_state: the shown scenario's state, with its edited heights (in metres) when they differ
   * from the shown section's own. */
  private captureState(): ScoreState {
    const st = normalized(this.scoreState())
    const block = this.xsBlock()
    const hgt = this.xsHeights
    if (block && hgt) {
      const perM = this.perM()
      const [bfDef, lbDef] = defaultHeights(block, perM)
      if (Math.abs(hgt[0] - bfDef) > 0.005 || Math.abs(hgt[1] - lbDef) > 0.005) st.xsHeights = { bankfull_m: hgt[0] / perM, lowbank_m: hgt[1] / perM }
    }
    return st
  }

  private applyState(state: any): void {
    const st = normalized(state)
    this.overrides = { ...st.overrides }
    this.observed = st.observed
    this.geomOwned = new Set(st.geomOwned)
    this.geomText = { ...st.geomText }
    this.geomScoring = st.geomScoring
    this.geomReason = st.geomReason
    this.xsSel = st.xsSel
    this.showHeights(st)
    this.changed()
  }

  private scenarioReports(): [import('../../shared/scenarios').Scenario, Record<string, any>][] {
    if (!this.base) return []
    const live = this.scored()!
    return this.scenarios.items.map((s) => [s, s.id === this.scenarios.active ? live : scoredFor(this.base!.report, s.state)])
  }

  private comparison(rows = this.scenarioReports()): Comparison {
    return easiComparison(rows.map(([s]) => s.name), rows.map(([, r]) => r))
  }

  private railDelta(): Child {
    if (this.scenarios.active === BASELINE_ID) return null
    const rows = this.scenarioReports()
    const active = rows.findIndex(([s]) => s.id === this.scenarios.active)
    if (active < 0) return null
    return railDelta(this.comparison([rows[0], rows[active]]), 1)
  }

  private scenariosChanged(switched: boolean): void {
    if (!this.shown()) return
    this.setOutput('scenario_bar', this.scenarioBarOut())
    if (switched) {
      this.setOutput('fn_panel', this.panel())
      this.xsMount()
      this.scoredChanged()
    } else this.setOutput('rollup_rail', this.rail())
  }

  private switchScenario(sid: string): void {
    const s = this.scenarios
    if (sid === s.active || !s.items.some((x) => x.id === sid)) return
    s.setState(s.active, this.captureState())
    s.select(sid)
    this.applyState(s.current.state)
    this.scenariosChanged(true)
  }

  private scenarioDialog(mode: 'new' | 'edit', name: string, description: string, start: string = BASELINE_ID, error: string | null = null): void {
    if (mode === 'new') {
      showModal(addDialog('easi', this.scenarios, { name, description, start, hint: ADD_HINT, error }))
      return
    }
    const cur = this.scenarios.current
    const base = cur.id === BASELINE_ID
    showModal(scenarioDialog('easi', { title: base ? 'Describe Existing Conditions' : 'Rename or describe', name, description,
      nameLocked: base, error, canDelete: mode === 'edit' && !base }))
  }

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
      showModal(deleteDialog('easi', s.current.name))
    } else if (action === 'compare' && s.hasAlternatives()) {
      showModal(compareDialog(this.comparison()))
    }
  }

  private field(id: string): string {
    const el = document.getElementById(`easi-${id}`) as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement | null
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
        s.setState(s.active, this.captureState())
        const created = start === START_BLANK ? s.add(name, desc, { state: emptyState() }) : s.add(name, desc, { copyFrom: start })
        this.applyState(created.state)
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
      this.applyState(s.current.state)
      this.scenariosChanged(true)
    }
    removeModal()
  }

  // ---------------------------------------------------------------- the edit bridge

  /** An input the page scripts posted; true when it was the worksheet's. */
  input(name: string, value: unknown): boolean {
    const ev = (value ?? {}) as Record<string, any>
    switch (name) {
      case 'override_set': {
        const mid = ev.mid
        if (!mid) return true
        const gen = (this.base?.report.metricRows || []).find((r: any) => r.metricId === mid)?.generatedRating ?? null
        const cur = { ...this.overrides }
        if (['Good', 'Fair', 'Poor'].includes(ev.rating) && ev.rating !== gen) cur[mid] = ev.rating
        else delete cur[mid]
        this.overrides = cur
        if (this.geomOwned.has(mid)) {
          this.geomOwned.delete(mid)
          delete this.geomText[mid]
          delete this.geomScoring[mid]
        }
        this.scoredChanged()
        return true
      }
      case 'note_set': {
        const mid = ev.mid
        const text = strip(String(ev.text ?? ''))
        if (!mid) return true
        if (text) this.notes = { ...this.notes, [mid]: text }
        else {
          const cur = { ...this.notes }
          delete cur[mid]
          this.notes = cur
        }
        return true
      }
      case 'observed_set': {
        const { mid, key } = ev
        if (!(OBSERVED_INPUTS[mid] ?? []).includes(key)) return true
        const v = observedValue(mid, key, ev.value)
        const cur = copy(this.observed)
        const entries = (cur[mid] ??= {})
        if (v === null) delete entries[key]
        else entries[key] = v
        if (!Object.keys(entries).length) delete cur[mid]
        this.observed = cur
        this.scoredChanged()
        return true
      }
      case 'nav_move':
        this.move(this.currentFn + Math.trunc(Number(ev.d) || 0))
        return true
      case 'nav_jump':
        if (ev.i !== undefined && ev.i !== null && !Number.isNaN(Number.parseInt(String(ev.i), 10))) this.move(Number.parseInt(String(ev.i), 10))
        return true
      case 'open_report_evt':
        this.requestReport()
        return true
      case 'close_modal_x':
        removeModal()
        return true
      case 'xs_prev':
        this.select(-1)
        return true
      case 'xs_next':
        this.select(1)
        return true
      case 'forms_evt':
        void this.openForms()
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
    const n = functions().length
    this.currentFn = Math.max(0, Math.min(n - 1, i))
    if (!this.shown()) return
    this.setOutput('fn_nav', this.nav())
    this.setOutput('fn_panel', this.panel())
    this.xsMount()
  }

  // ---------------------------------------------------------------- the assessment file

  /** _session_scenarios: every scenario's rating state, the shown one's read live. */
  sessionScenarios(): Record<string, any> {
    return scenariosBlock(this.scenarios, this.captureState())
  }

  /** Save (save_session): the file's text for this delineation. */
  saveText(delin: Record<string, any> | null): string {
    return easiSession.dump(delin, this.base, { ...this.notes }, scoringMethod(), this.sessionScenarios())
  }

  /** _work_fp: the site, whether it was screened, the notes and every scenario's ratings (null: nothing
   * to lose). */
  workFingerprint(delin: Record<string, any> | null): string | null {
    const s = this.scenarios
    if (!delin && !this.base && !s.hasAlternatives() && !s.baseline.description) return null
    const dd = (delin ?? {}).delineation ?? {}
    const site = ['comid', 'snapped_lat', 'snapped_lon'].map((k) => dd[k] ?? null)
    const live = this.scoreState()
    const rows = s.items.map((sc) => [sc.id, sc.name, sc.description, easiSession.fingerprintState(sc.id === s.active ? live : sc.state)])
    return stateFingerprint(site, this.base !== null, this.notes, rows)
  }

  /** Open (_load_session's part): the file's screening as it was saved (scored from the stored report,
   * no network call), its notes and every scenario's ratings, the saved one shown. */
  restore(base: Record<string, any> | null, notes: Record<string, string>, scenarios: Record<string, any> | null): void {
    this.reset()
    this.base = base
    this.status = base ? 'success' : 'initial'
    // _analysis_runs: a restored screening is this pick's; a site saved before it ran screens on the step
    this.launched = base ? this.pickGen : null
    this.notes = { ...notes }
    const sset = scenarioSet(scenarios)
    this.scenarios = sset
    this.xsIn = null
    this.xsHeights = null
    this.applyState(sset.current.state)
    this.currentFn = 0
    if (base) void plotly().catch(() => {})
  }

  /** _cancel_report: a report still being prepared will not open. */
  /** _workbook_bytes: the scenario workbook, or the single calculator when it cannot be assembled (never
   * a failed download); nothing before the screening has run. */
  private async workbookBytes(): Promise<Uint8Array | null> {
    const res = this.exportResult()
    if (!res || !this.base) return null
    const today = todayIso()
    try {
      const { buildWorkbook } = await import('./workbook')
      return await buildWorkbook(this.base, this.scenarioReports(), { ...this.notes }, today)
    } catch (err) {
      console.warn('EASI: the scenario workbook failed; serving the single calculator', err)
      return buildFilled(res, today)
    }
  }

  /** _open_forms: the Get Forms dialog for the screening (a snapshot, as the report is). */
  private async openForms(): Promise<void> {
    const res = this.exportResult()
    if (!res) {
      notify('The desktop metrics are still being computed. Get Forms opens when they finish.', { type: 'message', seconds: 4 })
      return
    }
    // a report still being prepared would open over this dialog and replace it
    this.cancelReport()
    const has = calculatorAvailable()
    const cells = has ? await entryCells().catch(() => ({})) : {}
    showModal(formsModal(res, cells, has))
  }

  cancelReport(): void {
    const pending = this.pendingReport
    if (pending === null) return
    this.pendingReport = null
    this.reportCounter += 1
    shim.send('staf-report-state', { requestId: pending, busy: false, opened: false, ns: 'easi' })
  }

  /** _report_title: the scenario shown names the report once there are alternatives. */
  private reportTitle(): string {
    const s = this.scenarios
    return s.hasAlternatives() ? `EASI Report · ${s.current.name}` : 'EASI Report'
  }

  /** web.report_scenario: the scenario the report is for, or null for a lone undescribed Existing Conditions. */
  private reportScenario(): [string, string] | null {
    const cur = this.scenarios.current
    if (!this.scenarios.hasAlternatives() && !cur.description) return null
    return [cur.name, cur.description || '']
  }

  /** _show_report_modal: the read-only report of the scenario shown (report-ready.js hears it start and open). */
  requestReport(): void {
    const res = this.exportResult()
    if (!this.base || !res) {
      notify('Run a screening first.', { type: 'message', seconds: 3 })
      return
    }
    const id = ++this.reportCounter
    const base = this.base
    this.pendingReport = id
    shim.send('staf-report-state', { requestId: id, busy: true, ns: 'easi' })
    // the cross-section's image first (the Shiny EASI draws its PNG before sending the report)
    const image = xsImageUrl(res)
    // the basin map over its basemap (reportmap.svg, read once and kept for the PDF)
    const ws = res.watershed_geojson ? displaySimplify(res.watershed_geojson, 700) : null
    const minimap = svg(ws, res.reach_geojson).catch(() => '')
    void Promise.all([image, minimap]).then(([png, map]) => {
      if (id !== this.reportCounter || this.base !== base) return
      this.pendingReport = null
      const summary = h.div(h.div('Assessment summary', { class: 'easi-section-title' }), summaryBlock(summaryRows(res), this.reportScenario()),
        { class: 'staf-report-summary' })
      const title = new TagList(this.reportTitle(), h.span('Close to review the Assessment', { class: 'easi-modal-hint' }),
        actionButton('easi-close_modal_x', '✕', 'easi-modal-x'))
      showModal(modal(reportBody(res, this.notes, summary, (rep) => xsReadonly(rep, png, this.reportPull()), map),
        { title, footer: reportFooter(), size: 'xl', easyClose: true }))
      // a pull that ended while the report was being prepared: its image now
      if (!png && hasProfile(this.xsShown())) void this.reportXsDraw()
      shim.send('staf-report-state', { requestId: id, busy: false, opened: true, ns: 'easi' })
    })
  }
}


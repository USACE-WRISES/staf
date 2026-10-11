// One tool's page: its map and what each Shiny tool's server draws around it on Identify.
//
// The Shiny tools render these as outputs from the map's zoom and center (each app.py: readout,
// zoom_cue, cursor_style, stream_legend, flow_loading); here they redraw on the map's own events,
// into the same output elements. The page scripts' inputs (shim.ts) arrive at input().
import type L from 'leaflet'
import type { EasiData } from '../engine/pipeline'
import type { PointTables } from '../engine/points'
import type { DeepWorksheet } from '../tools/deep/worksheet'
import type { SfariWorksheet } from '../tools/sfari/worksheet'
import { Basin } from './basin'
import { assessPane, type Covering, coveringRefs, loadRef } from './deepAssess'
import { esc, flowLoading, FLOW_ZOOM, identifyPane, legend, readout, START, type ToolKey, zoomCue } from './markup'
import { Identify } from './identify'
import { makeMap, type ToolMap } from './map'
import { notify } from './notify'
import { pyFixed } from './pyfmt'
import * as shim from './shim'
import { type Networks, StreamLayers } from './streams'

const FLOWLINE_COLOR = '#1f6feb'
const HR_FLOWLINE_COLOR = '#22b8cf'
/** The legend's Try again: a clockwise arrow (each app.py _RETRY_ICON). */
const RETRY_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true" focusable="false">'
  + '<path d="M12.33 5.5A5 5 0 1 1 8 3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"></path>'
  + '<path d="M7.6 1.2 10.4 3 7.6 4.8z" fill="currentColor"></path></svg>'

/** DEEP's assessment regions by status, as app.py REGION_STATUS_STYLE draws their legend key. */
const REGION_STATUS = {
  draft: { label: 'Draft', color: '#475569', fill: '107,100,89' },
  preliminary: { label: 'Preliminary', color: '#b45309', fill: '217,119,6' },
  certified: { label: 'Final', color: '#303f9f', fill: '63,81,181' },
} as const
type RegionStatus = keyof typeof REGION_STATUS

/** What the tools read: the bundle's networks, catchments and values, and the point tables. */
export type AppData = EasiData & { points: PointTables }

export class Tool {
  readonly key: ToolKey
  readonly root: HTMLElement
  step = 'identify'
  view: ToolMap | null = null
  /** map clicks made too far out: each pulses the zoom cue */
  private nudge = 0
  private seen = 0
  private coverage = false
  private streamsVisible = true
  /** DEEP: the statuses its regions show, for the legend's key */
  regionStatuses: RegionStatus[] = []
  streams: StreamLayers | null = null
  identify: Identify | null = null
  basin: Basin | null = null
  /** SFARI's Assessment worksheet: its code and SFARI's method load on their own, once SFARI is shown */
  sfari: SfariWorksheet | null = null
  private sfariP: Promise<SfariWorksheet> | null = null
  /** DEEP's measure worksheet, loaded the same way */
  deep: DeepWorksheet | null = null
  private deepP: Promise<DeepWorksheet> | null = null
  /** DEEP: the assessments covering the site and the one Basin adopted (selected_ref, loaded_assessment) */
  private covering: Covering[] = []
  private selectedRef: string | null = null
  private loaded: Record<string, any> | null = null
  /** the pick was ready at the last change (its regions were asked for then) */
  private warmed = false

  constructor(key: ToolKey, private nets: Networks, private data: AppData) {
    this.key = key
    const root = document.querySelector<HTMLElement>(`.easi-shell[data-staf-tool="${key}"]`)
    if (!root) throw new Error(`no section for ${key}`)
    this.root = root
  }

  private out(id: string): HTMLElement {
    const el = document.getElementById(`${this.key}-${id}`)
    if (!el) throw new Error(`no output ${this.key}-${id}`)
    return el
  }

  /** An output the current step may not have. */
  private maybe(id: string): HTMLElement | null {
    return document.getElementById(`${this.key}-${id}`)
  }

  /** The tool is shown: its map is made the first time (the Shiny tool's server starts then). */
  show(): void {
    if (this.view) return
    const box = this.root.querySelector<HTMLElement>('.leaflet-widgets')
    if (!box) return
    this.view = makeMap(box)
    const map = this.view.map
    this.streams = new StreamLayers(map, this.view.streams, this.nets, () => this.streamsChanged())
    this.streams.setCoverage(this.coverage)
    const streams = this.streams
    this.identify = new Identify({
      key: this.key, map, nets: this.nets, layers: this.view.layers,
      lines: () => streams.lines(),
      setSource: (k, layer) => streams.setSource(k, layer),
      changed: () => this.pickChanged(),
    })
    const identify = this.identify
    this.basin = new Basin({
      key: this.key, map, layers: this.view.layers, identify, data: this.data,
      reachFt: () => {
        const v = Number((this.maybe('reach_ft') as HTMLInputElement | null)?.value)
        return Number.isFinite(v) && v > 0 ? v : 1000.0
      },
      render: () => this.setStep('basin'),
      busy: () => this.renderBusy(),
      resolveAssessment: this.key === 'deep' ? () => void this.resolveAssessment() : undefined,
    })
    if (this.key === 'sfari') void this.loadSfari().catch(() => {})
    if (this.key === 'deep') void this.loadDeep().catch(() => {})
    map.on('zoomend moveend', () => this.viewChanged())
    map.on('click', (e: L.LeafletMouseEvent) => this.mapClick(e))
    this.viewChanged()
    // a Shiny download link turns on once its server has registered it
    const save = document.getElementById(`${this.key}-save_session`)
    save?.classList.remove('disabled')
    save?.removeAttribute('aria-disabled')
    save?.removeAttribute('tabindex')
    // The page scripts dock DEEP's regions button and the legend into the map's top right corner
    // as soon as it exists; ipyleaflet then adds its Layers control again whenever a layer
    // changes, so on the Shiny page the corner holds the button, the legend, then Layers. The
    // stylesheets order the corner's column, so this is the focus order, not the look.
    setTimeout(() => {
      const corner = this.view?.layers.getContainer()?.parentElement
      for (const sel of ['.deep-cov-tab', '.easi-legend-panel', '.leaflet-control-layers']) {
        const el = corner?.querySelector(`:scope > ${sel}`)
        if (el) corner?.appendChild(el)
      }
    }, 0)
  }

  zoom(): number {
    return this.view ? this.view.map.getZoom() : START.zoom
  }

  zoomedIn(): boolean {
    return this.zoom() >= FLOW_ZOOM
  }

  private viewChanged(): void {
    if (!this.view) return
    const map = this.view.map
    const c = map.getCenter()
    const hidden = this.key === 'deep' && (this.step === 'measure' || this.step === 'report')
    this.out('readout').innerHTML = hidden ? '' : '\n' + readout(map.getZoom(), c.lat, c.lng) + '\n'
    this.renderCue(false)
    this.renderCursor()
    this.renderLegend()
    this.streams?.viewChanged()
  }

  private streamsChanged(): void {
    this.renderLegend()
    // the cue while lines load: on Identify, zoomed in, a read in flight
    const loading = this.step === 'identify' && this.zoomedIn() && !!this.streams?.state.loading
    const slot = this.out('flow_loading')
    const html = loading ? flowLoading : ''
    if (slot.innerHTML !== html) slot.innerHTML = html
  }

  private mapClick(e: L.LeafletMouseEvent): void {
    if (this.step !== 'identify') return
    if (!this.zoomedIn()) {
      // no streams are drawn this far out: the zoom cue answers the click
      this.nudge += 1
      this.renderCue(true)
      return
    }
    this.basin?.invalidate()          // a new pick invalidates the last delineation
    this.sfari?.reset()
    this.deep?.reset()
    this.identify?.click(e.latlng.lat, e.latlng.lng)
  }

  // ------------------------------------------------------------------- the steps

  /** The left pane for a step (each app.py leftpane). */
  setStep(step: string): void {
    this.step = step
    const pane = this.out('leftpane')
    if ((step === 'review' && this.sfari && this.basin?.delin) || (step === 'measure' && this.deep && this.basin?.delin)) {
      // the worksheet over the map replaces the left pane
      pane.innerHTML = ''
    } else if (step === 'basin' && this.basin?.delin) {
      const assess = this.key === 'deep' ? assessPane(this.loaded, this.covering) : ''
      pane.innerHTML = '\n' + this.basin.pane(assess) + '\n'
    } else {
      this.step = 'identify'
      pane.innerHTML = '\n' + identifyPane(this.key, !!this.identify?.ready()) + '\n'
      this.pickChanged()
    }
    this.sfari?.render()
    this.sfari?.maybePull()
    this.deep?.render()
    this.deep?.maybeCompute()
    this.renderBusy()
    this.viewChanged()
    this.sendCoverageCurrent()
  }

  /** DEEP's regions hear the assessment in use and whether they take clicks (Identify only), whenever
   * either changes (app.py _send_coverage_current). */
  private sendCoverageCurrent(): void {
    if (this.key !== 'deep') return
    shim.send('deep_coverage_current', { assessmentId: this.loaded?.assessmentId ?? null, identify: this.step === 'identify', focus: false })
  }

  /** The busy line, and Delineate off while it runs (_toggle_delineate). */
  private renderBusy(): void {
    const b = this.basin
    const busy = this.maybe('busy_text')
    const text = b?.running ? b.stage : ''
    if (busy && busy.textContent !== text) busy.textContent = text
    const button = this.maybe('delineate') as HTMLButtonElement | null
    if (button) button.disabled = !this.identify?.ready() || !!b?.running
    const line = this.maybe('engine_line')
    if (line && b) {
      const html = b.engineLine()
      if (line.innerHTML !== html) line.innerHTML = html
    }
  }

  /** SFARI's worksheet module, loaded once. */
  private loadSfari(): Promise<SfariWorksheet> {
    this.sfariP ??= import('../tools/sfari/worksheet').then(({ SfariWorksheet }) => {
      const basin = this.basin!
      const identify = this.identify!
      this.sfari = new SfariWorksheet({
        root: this.root, data: this.data, step: () => this.step, delin: () => basin.delin,
        engineState: () => basin.engineState, engineLine: () => basin.engineLine(), lookupStatus: () => identify.lookupStatus(),
        sourceReady: () => identify.ready(), setStep: (s) => this.setStep(s),
      })
      return this.sfari
    })
    this.sfariP.catch(() => { this.sfariP = null })
    return this.sfariP
  }

  /** DEEP's worksheet module, loaded once. */
  private loadDeep(): Promise<DeepWorksheet> {
    this.deepP ??= import('../tools/deep/worksheet').then(({ DeepWorksheet }) => {
      const basin = this.basin!
      const identify = this.identify!
      this.deep = new DeepWorksheet({
        data: this.data, step: () => this.step, delin: () => basin.delin, engineState: () => basin.engineState,
        engineLine: () => basin.engineLine(), lookupStatus: () => identify.lookupStatus(), sourceReady: () => identify.ready(),
        loaded: () => this.loaded, ref: () => this.selectedRef, setStep: (s) => this.setStep(s),
      })
      this.deep.seedAutoStrata()
      return this.deep
    })
    this.deepP.catch(() => { this.deepP = null })
    return this.deepP
  }

  /** DEEP's Continue (_go_measure): the worksheet, from the first function. */
  private toMeasure(): void {
    if (!this.basin?.delin) {
      notify('Delineate the current point before continuing.', { type: 'warning', seconds: 4 })
      return
    }
    if (!this.identify?.ready()) {
      notify('Complete the StreamCat lookup before continuing.', { type: 'warning', seconds: 4 })
      return
    }
    if (!this.loaded) {
      notify(this.covering.length ? 'Choose an assessment first.' : 'No assessment covers this point, so there is nothing to score.',
        { type: 'warning', seconds: 4 })
      return
    }
    void this.loadDeep().then((ws) => {
      ws.currentFn = 0
      this.setStep('measure')
    }, () => notify('The worksheet did not load. Check the connection and try again.', { type: 'error', seconds: 6 }))
  }

  /** A step link in the pane (each tool's step_nav). */
  private stepNav(target: string): void {
    const hasDelin = !!this.basin?.delin
    if (this.key === 'deep') {
      // DEEP's _stepper_nav: Assessment and Report need the delineation and an assessment
      if (!['identify', 'basin', 'measure', 'report'].includes(target)) return
      const allowed = target === 'identify' || (hasDelin && (target === 'basin' || !!this.loaded))
      if (!allowed) notify('Finish the earlier steps first.', { seconds: 2 })
      else if (target === 'report') notify('The report is not in the web app yet.', { seconds: 4 })
      else if (target === 'measure') void this.loadDeep().then(() => this.setStep('measure'))
      else this.setStep(target)
      return
    }
    if (this.key === 'sfari') {
      // SFARI's _stepper_nav
      if (!['identify', 'basin', 'review', 'report'].includes(target)) return
      if (target === 'identify') this.setStep('identify')
      else if (hasDelin && target === 'report') {
        if (!this.sfari?.anyScored()) notify('Score at least one function before viewing the report.', { seconds: 4 })
        else this.sfari.requestReport()
      } else if (hasDelin && target === 'review') void this.loadSfari().then(() => this.setStep('review'))
      else if (hasDelin) this.setStep(target)
      else notify('Finish the earlier steps first.', { seconds: 2 })
      return
    }
    if (target === 'identify') this.setStep('identify')
    else if (target === 'basin' && hasDelin) this.setStep('basin')
    else if (target !== 'report' && hasDelin) notify('The Assessment step is not in the web app yet.', { seconds: 4 })
    else notify('Finish the earlier steps first.', { seconds: 2 })
  }

  /** SFARI's Continue (_go_review, _enter_review): the worksheet, from the first function. */
  private toReview(): void {
    if (!this.basin?.delin || !this.identify?.ready()) {
      notify('Wait for the StreamCat lookup, or retry it, before continuing.', { seconds: 5 })
      return
    }
    void this.loadSfari().then((ws) => {
      ws.currentFn = 0
      this.setStep('review')
    }, () => notify('The worksheet did not load. Check the connection and try again.', { type: 'error', seconds: 6 }))
  }

  /** Clear: back to an empty Identify (each app.py _reset). */
  private clear(): void {
    this.sfari?.reset()
    this.deep?.reset()
    this.basin?.invalidate()
    this.identify?.clear()
    this.selectedRef = null
    this.loaded = null
    this.covering = []
    this.setStep('identify')
  }

  /** DEEP adopts an assessment once there is a basin for it (app.py _resolve_assessment). */
  private async resolveAssessment(): Promise<void> {
    const d = this.basin?.delin?.delineation ?? {}
    const pt = this.identify?.snapped
    const lat = pt ? pt[0] : d.snapped_lat
    const lon = pt ? pt[1] : d.snapped_lon
    if (lat == null || lon == null || !this.regionSource) return
    this.regions ??= this.regionSource()
    const features = await this.regions.catch(() => [])
    this.covering = await coveringRefs(this.data.src, features as any[], lat, lon).catch(() => [])
    if (!this.selectedRef && this.covering.length) {
      const ref = this.covering[0].defaultRef
      this.loaded = await loadRef(this.data.src, ref).catch(() => null)
      if (this.loaded) {
        this.selectedRef = ref
        this.deep?.assessmentLoaded()
      }
    }
    this.deep?.seedAutoStrata()
    const slot = this.maybe('assess_pane')
    if (slot && this.step === 'basin') slot.innerHTML = '\n' + assessPane(this.loaded, this.covering) + '\n'
    this.sendCoverageCurrent()
  }

  /** The pick changed: the snap status, the lookup line, Delineate, the legend. */
  private pickChanged(): void {
    const id = this.identify
    if (!id) return
    const snap = this.maybe('snap_status')
    const html = '\n' + id.snapStatus() + '\n'
    if (snap && snap.innerHTML !== html) snap.innerHTML = html
    if (this.key !== 'easi') {
      const look = this.maybe('streamcat_lookup_status')
      const lh = id.lookupStatus()
      const lhtml = lh ? '\n' + lh + '\n' : ''
      if (look && look.innerHTML !== lhtml) look.innerHTML = lhtml
    }
    if (id.ready() && !this.warmed) this.basin?.prefetch()
    this.warmed = id.ready()
    const button = document.getElementById(`${this.key}-delineate`) as HTMLButtonElement | null
    if (button) {
      button.disabled = !id.ready() || !!this.basin?.running
      // EASI names the routed site's step (app.py _toggle_delineate)
      if (this.key === 'easi') {
        const label = button.querySelector('.action-label')
        const text = id.anchor?.anchorKind === 'hrSurrogate' ? 'Compute watershed and reach' : 'Delineate Basin and Reach'
        if (label && label.textContent !== text) label.textContent = text
      }
    }
    this.renderLegend()
  }

  /** Typed latitude and longitude (coord-entry.js): the map goes there and the point snaps. */
  private coordsEntered(value: unknown): void {
    if (this.step !== 'identify' || !this.view || !this.identify) return
    const ev = (value ?? {}) as { lat?: number, lon?: number }
    const lat = Number(ev.lat)
    const lon = Number(ev.lon)
    if (ev.lat == null || ev.lon == null || !Number.isFinite(lat) || !Number.isFinite(lon)) return
    if (!(lat >= 24 && lat <= 50 && lon >= -125 && lon <= -66)) {
      notify('Coordinates must be within the continental United States.', { type: 'warning', seconds: 5 })
      return
    }
    this.view.map.setView([lat, lon], 15)
    this.basin?.invalidate()
    this.sfari?.reset()
    this.deep?.reset()
    this.identify.typed(lat, lon)
  }

  /** A place chosen in the search's suggestions (geocode-autocomplete.js): the map goes there. */
  private addressPick(value: unknown): void {
    if (!this.view) return
    const pick = (value ?? {}) as { lat?: number, lon?: number, label?: string }
    if (pick.lat == null || pick.lon == null) return
    const lat = Number(pick.lat)
    const lon = Number(pick.lon)
    this.view.map.setView([lat, lon], 15)
    const where = pick.label || `${pyFixed(lat, 4)}, ${pyFixed(lon, 4)}`
    notify(`Centered on ${where}. Click a stream line.`, { seconds: 4 })
  }

  /** Find on map: the typed place from Photon (each app's geocode_address, its first provider). */
  async findAddress(): Promise<void> {
    const box = document.getElementById(`${this.key}-address`) as HTMLInputElement | null
    const q = (box?.value ?? '').trim()
    const hit = q ? await photon(q).catch(() => null) : null
    if (hit && this.view) {
      this.view.map.setView([hit[0], hit[1]], 15)
      notify(`Centered on ${pyFixed(hit[0], 4)}, ${pyFixed(hit[1], 4)}. Click a stream line.`, { seconds: 4 })
    } else {
      notify('Place not found. Try a city, address, or stream name.', { type: 'warning', seconds: 4 })
    }
  }

  private renderCue(clicked: boolean): void {
    const shown = this.step === 'identify' && !this.zoomedIn()
    const pulse = clicked && this.nudge > this.seen
    this.seen = this.nudge
    const slot = this.out('zoom_cue')
    const html = shown ? '\n' + zoomCue(this.nudge, pulse) + '\n' : ''
    // a redraw with nothing new keeps the element (and a running pulse)
    if (pulse || slot.innerHTML !== html) slot.innerHTML = html
  }

  /** A crosshair while a click picks a stream (Identify, zoomed in); the hand while dragging. */
  private renderCursor(): void {
    const picking = this.step === 'identify' && this.zoomedIn()
    const scope = `.easi-shell[data-staf-tool="${this.key}"] .easi-map-wrap`
    this.out('cursor_style').innerHTML = picking
      ? `<style>${scope} .leaflet-grab{cursor:crosshair !important;}`
        + `${scope} .leaflet-container.leaflet-dragging,`
        + `${scope} .leaflet-container.leaflet-dragging .leaflet-grab{cursor:grabbing !important;}</style>`
      : ''
  }

  /** The map key (each app.py _legend_ui), Identify before a pick. */
  renderLegend(): void {
    if (this.step !== 'identify' && this.step !== 'basin') {
      this.out('stream_legend').innerHTML = ''
      return
    }
    const st = this.streams?.state
    const mode = st?.mode ?? null
    // the lines did not load and the retries ran out: a notice and Try again, at any zoom
    const unavailable = !!st?.down && mode === 'hr-unavailable'
    const rows = unavailable || !this.streamsVisible ? []
      : this.coverage
        ? [{ color: FLOWLINE_COLOR, label: 'StreamCat reaches' }, { color: HR_FLOWLINE_COLOR, label: 'Other streams' }]
        : [{ color: FLOWLINE_COLOR, label: 'Streams' }]
    let note: string | undefined
    if (unavailable) note = undefined
    else if (!this.streamsVisible) note = 'Streams are hidden'
    else if (!this.zoomedIn()) note = this.step === 'identify' ? undefined : 'Zoom in to see streams'
    else if (mode === 'hr-truncated') note = 'Too many streams to show here. Zoom in.'
    else if (mode === 'hr-unavailable') note = 'Stream lines did not load. Retrying.'
    else if (mode === 'hr-only' && this.coverage) note = 'StreamCat coverage unavailable here.'
    else if (mode === 'empty') note = 'No streams in view.'
    const alert = unavailable ? this.unavailableNotice() : ''
    // the pick's reach and route, with StreamCat coverage on
    const source = this.coverage && this.streamsVisible ? (this.identify?.legendRows() ?? []) : []
    const regions = (this.key === 'deep' && this.step === 'identify' ? this.regionKey() : '')
      + (this.step === 'basin' ? Basin.legendRows() : '')
    const extra = source.map((r) => `
  <div class="easi-legend-row">
    <span class="easi-legend-sw easi-legend-sw-${r.kind}" style="--swatch-color:${r.color};"></span>
    <div>
      <div class="easi-legend-label">${esc(r.label)}</div>
    </div>
  </div>`).join('') + regions
    const html = '\n' + legend(rows, note, extra, alert) + '\n'
    const slot = this.out('stream_legend')
    if (slot.innerHTML !== html) slot.innerHTML = html
  }

  /** The legend's notice when the lines did not load (each app.py _legend_ui): the static app's lines
   * come from the STAF data mirror, where the Shiny tools' come from the USGS service. */
  private unavailableNotice(): string {
    return `
  <div class="easi-legend-alert">
    <div>
      <div class="easi-legend-label">Streams unavailable</div>
      <div class="easi-legend-note">The stream data did not load. Try again later.</div>
    </div>
    <button class="btn btn-default action-button easi-legend-retry" id="${this.key}-retry_streams" type="button" title="Try again" aria-label="Try again">${RETRY_ICON}</button>
  </div>`
  }

  /** DEEP's key to its assessment regions (app.py _region_key): one toggle row per status shown. */
  private regionKey(): string {
    const rows = (Object.keys(REGION_STATUS) as RegionStatus[]).filter((s) => this.regionStatuses.includes(s))
      .map((s) => {
        const st = REGION_STATUS[s]
        return `<div class="easi-legend-row deep-reg-row" role="button" tabindex="0" title="Hide ${st.label} regions" data-status="${s}" aria-pressed="true">
      <span class="deep-reg-sw" style="--swatch-line:${st.color};--swatch-fill:rgba(${st.fill},.28);"></span>
      <div class="easi-legend-label">${st.label}</div>
    </div>`
      })
    if (!rows.length) return ''
    return `
  <div class="deep-reg-key">
    <div class="easi-legend-sub">Assessment regions</div>
    ${rows.join('\n    ')}
  </div>`
  }

  /** An input a page script posted (shim.ts), by its bare name. */
  input(name: string, value: unknown): void {
    switch (name) {
      case 'streamcat_coverage':
        this.coverage = !!value
        this.streams?.setCoverage(this.coverage)
        this.renderLegend()
        break
      case 'retry_streams':
        this.streams?.retry()
        break
      case 'retry_streamcat':
      case 'retry_streamcat_ws':
        this.basin?.invalidate()
        this.identify?.retry()
        break
      case 'delineate':
        this.basin?.start()
        break
      case 'clear_basin':
        this.clear()
        break
      case 'to_review':
        if (this.key === 'sfari') {
          this.toReview()
          break
        }
        notify('The Assessment step is not in the web app yet.', { seconds: 4 })
        break
      case 'to_measure':
        this.toMeasure()
        break
      case 'run_screening':
        notify('The Assessment step is not in the web app yet.', { seconds: 4 })
        break
      case 'change_assessment':
        notify('Changing the assessment comes with the Assessment step.', { seconds: 4 })
        break
      case 'step_nav':
        // a link's key, or field-review.js's {key}
        this.stepNav(String(value && typeof value === 'object' ? (value as { key?: string }).key : value))
        break
      case 'coords_entered':
        this.coordsEntered(value)
        break
      case 'address_pick':
        this.addressPick(value)
        break
      case 'streams_visible':
        this.streamsVisible = value !== false
        this.renderLegend()
        break
      case 'coverage_ready':
        if (this.key === 'deep') this.sendCoverage()
        break
      default:
        if (!this.sfari?.input(name, value)) this.deep?.input(name, value)
        break
    }
  }

  // ------------------------------------------------------------------- DEEP's assessment regions
  private coverageSent = false
  private regions: Promise<unknown[]> | null = null
  /** The region outlines for coverage.js, once per page (as DEEP's server sends them once). */
  regionSource: (() => Promise<unknown[]>) | null = null

  private sendCoverage(): void {
    if (this.coverageSent || !this.regionSource) return
    this.coverageSent = true
    this.regions ??= this.regionSource()
    void this.regions.then((features) => {
      const statuses = new Set((features as { lifecycle: RegionStatus }[]).map((f) => f.lifecycle))
      this.regionStatuses = [...statuses].sort() as RegionStatus[]
      shim.send('deep_coverage', { features })
      shim.send('deep_coverage_current', { assessmentId: this.loaded?.assessmentId ?? null, identify: this.step === 'identify', focus: false })
      this.renderLegend()
    }, () => {
      this.coverageSent = false
    })
  }
}

/** Photon's first US place for a query (geocode._photon; the as-you-type list asks it too). */
async function photon(q: string): Promise<[number, number] | null> {
  const url = `https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=5&lang=en&lat=39.5&lon=-98.35`
  const res = await fetch(url)
  if (res.status !== 200) return null
  const j = await res.json() as { features?: { properties?: { countrycode?: string }, geometry: { coordinates: number[] } }[] }
  for (const f of j.features ?? []) {
    if (f.properties?.countrycode === 'US') return [Number(f.geometry.coordinates[1]), Number(f.geometry.coordinates[0])]
  }
  return null
}

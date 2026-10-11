// One tool's page: its map and what each Shiny tool's server draws around it on Identify.
//
// The Shiny tools render these as outputs from the map's zoom and center (each app.py: readout,
// zoom_cue, cursor_style, stream_legend, flow_loading); here they redraw on the map's own events,
// into the same output elements. The page scripts' inputs (shim.ts) arrive at input().
import type L from 'leaflet'
import { flowLoading, FLOW_ZOOM, legend, readout, START, type ToolKey, zoomCue } from './markup'
import { makeMap, type ToolMap } from './map'
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

  constructor(key: ToolKey, private nets: Networks) {
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

  /** The tool is shown: its map is made the first time (the Shiny tool's server starts then). */
  show(): void {
    if (this.view) return
    const box = this.root.querySelector<HTMLElement>('.leaflet-widgets')
    if (!box) return
    this.view = makeMap(box)
    const map = this.view.map
    this.streams = new StreamLayers(map, this.view.streams, this.nets, () => this.streamsChanged())
    this.streams.setCoverage(this.coverage)
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

  private mapClick(_e: L.LeafletMouseEvent): void {
    if (this.step !== 'identify') return
    if (!this.zoomedIn()) {
      // no streams are drawn this far out: the zoom cue answers the click
      this.nudge += 1
      this.renderCue(true)
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
    const key = this.key === 'deep' && this.step === 'identify' ? this.regionKey() : ''
    const html = '\n' + legend(rows, note, key, alert) + '\n'
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
      case 'streams_visible':
        this.streamsVisible = value !== false
        this.renderLegend()
        break
      case 'coverage_ready':
        if (this.key === 'deep') this.sendCoverage()
        break
      default:
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
      shim.send('deep_coverage_current', { assessmentId: null, identify: this.step === 'identify', focus: false })
      this.renderLegend()
    }, () => {
      this.coverageSent = false
    })
  }
}

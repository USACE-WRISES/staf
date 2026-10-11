// The map's stream lines from zoom 14 (each Shiny tool's streams_task, _settle_and_fetch and
// _apply_streams, and its _StreamMapLayers).
//
// When the view settles at zoom 14 or more and has left the box last fetched, the padded box around
// its centre (viewport.ts) is read from the bundle (network.ts) and split by the click rule
// (display.ts). The lines draw in the staf-streams pane in the Streams group: the HR stretches the STAF
// site engine answers, then the covered ones on top, all one blue unless StreamCat coverage is on
// (then cyan for the first). A newer box replaces a slower one; zooming out clears the lines.
import L from 'leaflet'
import { buildDisplay, type Display, type FeatureCollection, type Mode } from '../streams/display'
import { hrFeatures, type Network, v2Features } from '../streams/network'
import { type BBox, fetchBox, needsFetch, type View } from '../streams/viewport'
import { FLOW_ZOOM } from './markup'

export const FLOWLINE_STYLE: L.PathOptions = { color: '#1f6feb', weight: 3, opacity: 0.95 }
export const HR_FLOWLINE_STYLE: L.PathOptions = { color: '#22b8cf', weight: 3, opacity: 0.9 }
/** The Shiny map fetches once the view has been still this long; the static app reads quicker. */
const SETTLE_MS = 250
/** A read that failed is asked again after these pauses, then not until Try again (as the Shiny
 * tools retry the USGS service). */
export const RETRY_DELAYS_MS = [15_000, 30_000, 60_000]

export interface Networks { hr: Network, v2: Network }

export interface StreamState {
  mode: Mode | null
  loading: boolean
  /** the reads failed and their retries ran out: the legend offers Try again */
  down: boolean
  /** the raw networks of the view, the click rule's input */
  hr: FeatureCollection | null
  v2: FeatureCollection | null
}

export class StreamLayers {
  readonly state: StreamState = { mode: null, loading: false, down: false, hr: null, v2: null }
  private hrflow: L.GeoJSON
  private flow: L.GeoJSON
  private fetched: BBox | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private retries = 0
  private generation = 0
  private coverage = false

  constructor(private map: L.Map, group: L.LayerGroup, private nets: Networks, private changed: () => void) {
    const opts = (style: L.PathOptions): L.GeoJSONOptions => ({ style: () => style, pane: 'staf-streams' })
    this.hrflow = L.geoJSON(undefined, opts(FLOWLINE_STYLE))
    this.flow = L.geoJSON(undefined, opts(FLOWLINE_STYLE))
    group.addLayer(this.hrflow)
    group.addLayer(this.flow)
  }

  setCoverage(on: boolean): void {
    this.coverage = on
    this.hrflow.setStyle(on ? HR_FLOWLINE_STYLE : FLOWLINE_STYLE)
    this.hrflow.options.style = () => (this.coverage ? HR_FLOWLINE_STYLE : FLOWLINE_STYLE)
  }

  /** The view moved or zoomed: fetch for it once it settles (out too far, clear at once). */
  viewChanged(): void {
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    if (!this.box()) {
      this.settle()
      return
    }
    this.timer = setTimeout(() => {
      this.timer = null
      this.settle()
    }, SETTLE_MS)
  }

  private box(): BBox | null {
    const z = this.map.getZoom()
    if (z < FLOW_ZOOM) return null
    const c = this.map.getCenter()
    return fetchBox(c.lat, c.lng, z)
  }

  private view(): View {
    const b = this.map.getBounds()
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
  }

  private settle(): void {
    const box = this.box()
    if (!box) {
      this.generation += 1
      this.clear()
      this.fetched = null
      Object.assign(this.state, { mode: null, loading: false, hr: null, v2: null })
      this.changed()
      return
    }
    if (!needsFetch(this.view(), this.fetched)) return
    this.fetched = box
    void this.fetch(box)
  }

  /** Try again (the legend's button): the retries start over, for the box in view. */
  retry(): void {
    if (!this.state.down) return
    this.retries = 0
    this.state.down = false
    this.fetched = null
    this.settle()
  }

  private async fetch(box: BBox): Promise<void> {
    const mine = ++this.generation
    this.state.loading = true
    this.changed()
    let display: Display
    let hr: FeatureCollection | null
    let v2: FeatureCollection | null
    try {
      const [w, s, e, n] = box
      const [hrLines, v2Lines] = await Promise.all([this.nets.hr.inBox(w, s, e, n), this.nets.v2.inBox(w, s, e, n)])
      hr = hrFeatures(hrLines)
      v2 = v2Features(v2Lines)
      display = buildDisplay(v2, hr, 150)
    } catch (err) {
      if (mine !== this.generation) return
      console.warn('stream lines did not load', err)
      this.state.loading = false
      this.state.mode = 'hr-unavailable'
      this.gap()
      this.changed()
      return
    }
    if (mine !== this.generation || this.fetched !== box) return     // torn down or a stale box
    this.retries = 0
    Object.assign(this.state, { mode: display.mode, loading: false, down: false, hr, v2 })
    this.hrflow.clearLayers()
    this.hrflow.addData(display.uncovered as GeoJSON.FeatureCollection)
    this.flow.clearLayers()
    this.flow.addData(display.covered as GeoJSON.FeatureCollection)
    this.changed()
  }

  /** The view's lines did not load: ask again after 15, 30 and 60 seconds, then wait for Try again. */
  private gap(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer)
    if (this.retries >= RETRY_DELAYS_MS.length) {
      this.state.down = true
      return
    }
    const wait = RETRY_DELAYS_MS[this.retries]
    this.retries += 1
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      this.fetched = null
      this.settle()
    }, wait)
  }

  private clear(): void {
    this.hrflow.clearLayers()
    this.flow.clearLayers()
  }
}

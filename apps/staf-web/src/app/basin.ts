// Delineate and the Basin step, as each Shiny tool runs them.
//
// EASI (app.py _start_delineate, _delineate_done): pipeline.delineate_only on the pick's COMID, a
// "Delineating basin & reach…" toast while it runs (a routed site narrates the engine's steps); SFARI
// and DEEP (_launch_engine, _engine_done): the STAF site engine at the pick, its steps on the busy row,
// and a dialog offering the StreamCat values when it cannot compute the watershed. The basin is drawn
// as _draw_delineation draws it: the watershed (display-simplified) and the reach, the point on top,
// the map framed as ipyleaflet's fit_bounds frames it; then the Basin card.
import L from 'leaflet'
import Modal from 'bootstrap/js/dist/modal'
import type { Anchor } from '../engine/anchor'
import { delineateFromEngine, delineateOnlyEasi, delineateWithoutWatershed, type EasiData, geojsonBounds, type Result, stripGeometry } from '../engine/pipeline'
import { displaySimplify } from '../engine/simplify'
import { computeSite, INTERACTIVE_CONFIG, type ProgressEvent } from '../engine/site'
import type { Identify } from './identify'
import { basinCard, basinPane, esc, type ToolKey } from './markup'
import { notify, removeNotification } from './notify'
import { timed } from './perf'
import { pyFixedComma } from './pyfmt'
import { pyCopy, pySet } from './pyjson'
import { warmFiles } from './warm'

export const WATERSHED_STYLE: L.PathOptions = { color: '#caa700', weight: 1, fillColor: '#fdf24a', fillOpacity: 0.40 }
export const REACH_STYLE: L.PathOptions = { color: '#d6453d', weight: 4 }

const SFARI_FAMILIES = ['baseflow', 'dams', 'landcover', 'roads', 'runoff', 'soils', 'xsection']
const DEEP_FAMILIES = ['baseflow', 'dams', 'landcover', 'roads', 'runoff', 'soils']
const HR_FAILURE_REASONS = ['upstream tree query failed', 'catchment query failed', 'anchor reach geometry unavailable',
  'no HR flowline near the point']
const HR_FAILURE_TEXT = 'the USGS stream service did not answer in time'
const ENGINE_STEPS: Record<string, [number, string]> = {
  site: [1, 'finding the stream'], walk: [2, 'tracing upstream'], catchments: [3, 'joining catchments'],
  union: [3, 'joining catchments'], geometry: [3, 'joining catchments'], reach: [4, 'marking the assessment reach'],
  metrics: [5, 'computing metrics'],
}
const FAMILY_TEXT: Record<string, string> = {
  baseflow: 'base flow', dams: 'dams', landcover: 'land cover', roads: 'roads', runoff: 'runoff', soils: 'soils', xsection: 'cross-sections',
}

/** The busy line while the engine runs (each app.py _engine_progress_text). */
export function progressText(prog: Partial<ProgressEvent>, families: string[] = []): string {
  const lead = 'Delineating watershed'
  const st = prog.stage
  if (st === 'done') return `${lead} · finishing`
  const step = st ? ENGINE_STEPS[st] : undefined
  if (!step) return `${lead} · starting`
  let [n, what] = step
  if ((n === 2 || n === 3) && prog.reaches) {
    const count = Math.trunc(prog.reaches)
    what += `, ${pyFixedComma(count, 0)} reach${count === 1 ? '' : 'es'}`
  }
  if (st === 'catchments' && prog.batch && (prog.batches ?? 0) > 1) what += `, batch ${prog.batch} of ${prog.batches}`
  if (st === 'metrics' && prog.family) {
    const fams = [...families].sort()
    const pos = fams.includes(prog.family) ? ` (${fams.indexOf(prog.family) + 1} of ${fams.length})` : ''
    what += `, ${FAMILY_TEXT[prog.family] ?? prog.family}${pos}`
  }
  return `${lead} · step ${n} of 5 · ${what}`
}

/** The engine's failure in the assessor's words (SFARI's and DEEP's _engine_reason_text). */
function reasonText(reason: unknown, fallback = 'no detail'): string {
  const text = String(reason ?? '').trim()
  if (HR_FAILURE_REASONS.some((p) => text.startsWith(p))) return HR_FAILURE_TEXT
  return text || fallback
}

/** ``{:,.2f} km²`` (basin.fmt_km2, each tool's _fmt_km2). */
function km2(v: unknown): string {
  const n = Number(v)
  return v === null || v === undefined || !Number.isFinite(n) ? 'unknown' : `${pyFixedComma(n, 2)} km²`
}

/** ``{:,.0f} ft`` (basin.fmt_ft). */
function feet(v: unknown): string {
  const n = Number(v)
  return v === null || v === undefined || !Number.isFinite(n) ? 'unknown' : `${pyFixedComma(n, 0)} ft`
}

/** ipyleaflet Map.fit_bounds: the bounds' center in degrees, then whole zoom steps: out until the view
 * holds the bounds, else in while it holds them strictly, then one back. */
export function fitBounds(map: L.Map, [[s, w], [n, e]]: [[number, number], [number, number]]): void {
  map.setView([s + (n - s) / 2, w + (e - w) / 2], map.getZoom(), { animate: false })
  let zoomedOut = false
  for (;;) {
    if (map.getZoom() <= 1) break
    const b = map.getBounds()
    if (b.getSouth() > s || b.getNorth() < n || b.getWest() > w || b.getEast() < e) {
      map.setZoom(map.getZoom() - 1, { animate: false })
      zoomedOut = true
    } else break
  }
  if (zoomedOut) return
  for (;;) {
    const b = map.getBounds()
    if (b.getSouth() < s && b.getNorth() > n && b.getWest() < w && b.getEast() > e && map.getZoom() < map.getMaxZoom()) {
      map.setZoom(map.getZoom() + 1, { animate: false })
    } else {
      map.setZoom(map.getZoom() - 1, { animate: false })
      break
    }
  }
}

export interface BasinHost {
  key: ToolKey
  map: L.Map
  layers: L.Control.Layers
  identify: Identify
  data: EasiData
  /** the Identify card's reach length (ft) */
  reachFt(): number
  /** the step changed or the pane needs drawing again */
  render(): void
  /** the busy line changed */
  busy(): void
  /** DEEP: the Basin pane's assessment block, once the delineation is drawn */
  resolveAssessment?(): void
}

interface EngineState { status: string, reason?: string | null, record?: Result | null }

export class Basin {
  delin: Result | null = null
  running = false
  stage = ''
  engineState: EngineState | null = null
  private gen = 0
  private ws: L.GeoJSON | null = null
  private reach: L.GeoJSON | null = null

  constructor(private host: BasinHost) {}

  /** A new pick or a reset: the delineation and its layers go (_invalidate_analysis). */
  invalidate(): void {
    this.gen += 1
    this.delin = null
    this.running = false
    this.stage = ''
    this.engineState = null
    removeNotification('stage')
    this.removeLayers()
  }

  private removeLayers(): void {
    if (this.ws) {
      this.host.layers.removeLayer(this.ws)
      this.ws.remove()
    }
    this.reach?.remove()
    this.ws = null
    this.reach = null
  }

  /** A pick is ready: the regions Delineate will read start loading (no work is done before the
   * button is pressed; this only saves the wait for the files). */
  prefetch(): void {
    const id = this.host.identify
    const pt = id.snapped
    if (!pt) return
    const anchor = id.anchor ?? {}
    // the region's network and catchments, then what the tool's Delineate and Assessment step read (warm.ts)
    const files = (vpus: string[]) => warmFiles(this.host.data.src, this.host.key, vpus)
    if (this.host.key === 'easi') {
      if (anchor.anchorKind === 'hrSurrogate' && anchor.clickedStream?.nhdplusId) void this.host.data.hr.warm(anchor.clickedStream.nhdplusId).then(files)
      else if (pt[3] !== null) void this.host.data.v2.warm(pt[3]).then(files)
    } else if (pt[3] !== null) {
      void this.host.data.hr.warm(pt[3]).then(files)
    }
  }

  /** Delineate pressed. */
  start(): void {
    void timed('delineate', this.host.key === 'easi' ? this.startEasi() : this.startEngine())
  }

  // ---------------------------------------------------------------- EASI

  private async startEasi(): Promise<void> {
    const id = this.host.identify
    if (!id.ready() || this.running) return
    this.invalidate()
    const pt = id.snapped!
    const [lat, lon, , comid] = pt
    const anchor: Anchor | null = id.anchor
    const routed = anchor?.anchorKind === 'hrSurrogate'
    const label = routed ? progressText({}) : 'Delineating basin & reach…'
    this.stage = label
    notify(label + ', please wait', { id: 'stage', type: 'message', seconds: 0 })
    this.running = true
    const gen = this.gen
    this.host.busy()
    const prog: Partial<ProgressEvent> = {}
    let res: Result
    try {
      res = await delineateOnlyEasi(this.host.data, lat, lon, this.host.reachFt(), comid as number, anchor, (ev) => {
        if (gen !== this.gen) return
        Object.assign(prog, ev)
        this.stage = progressText(prog)
        notify(this.stage + ', please wait', { id: 'stage', type: 'message', seconds: 0 })
        this.host.busy()
      })
    } catch {
      res = { status: 'error', message: 'Delineation failed. Please try again.' }
    }
    if (gen !== this.gen) return
    removeNotification('stage')
    this.stage = ''
    this.running = false
    this.host.busy()
    if (res.status !== 'ok') {
      notify(res.message || 'Delineation error', { type: 'error', seconds: 8 })
      return
    }
    if (!this.draw(res)) return
    const d = res.delineation ?? {}
    if (d.watershed_source === 'not-calculated') {
      // the engine failed or refused: no polygon, and the guidance says what to do next
      this.removeWatershed()
      let guidance: string | undefined = [...(d.warnings ?? [])].reverse().find((w: string) => w.includes('SFARI or DEEP'))
      const reason = d.watershed_engine?.reason
      if (HR_FAILURE_REASONS.some((p) => String(reason ?? '').startsWith(p))) {
        guidance = `The HR reach watershed could not be calculated because ${HR_FAILURE_TEXT}. Delineate again in a few minutes.`
      }
      notify(guidance || 'The HR reach watershed could not be calculated. Watershed metrics are unavailable.', { type: 'warning', seconds: 12 })
    }
    this.delin = res
    this.host.render()
  }

  // ---------------------------------------------------------------- SFARI and DEEP

  private async startEngine(): Promise<void> {
    const id = this.host.identify
    const key = this.host.key
    if (!id.ready()) {
      if (key === 'sfari') notify('Wait for the StreamCat lookup, or retry it, before continuing.', { type: 'message', seconds: 5 })
      else notify('Finish the StreamCat lookup before delineating.', { type: 'warning', seconds: 4 })
      return
    }
    if (this.running) return
    const pt = id.snapped!
    const [lat, lon] = pt
    const reachFt = this.host.reachFt()
    this.gen += 1
    const gen = this.gen
    this.engineState = { status: 'running' }
    this.running = true
    this.stage = progressText({})
    this.host.busy()
    const prog: Partial<ProgressEvent> = {}
    const families = key === 'sfari' ? SFARI_FAMILIES : DEEP_FAMILIES
    const cfg = { ...INTERACTIVE_CONFIG, includeGeometry: true, metricFamilies: families, landcoverBaseline: key === 'sfari',
      ...(reachFt > 0 ? { reachLengthFt: reachFt } : {}) }
    const rec = await computeSite(this.host.data, lat, lon, cfg, (ev) => {
      if (gen !== this.gen) return
      Object.assign(prog, ev)
      this.stage = progressText(prog, families)
      this.host.busy()
    })
    if (gen !== this.gen || !id.ready()) return
    this.running = false
    this.stage = ''
    const status = rec.status || 'failed'
    this.engineState = { status, reason: rec.reason, record: status === 'ok' ? stripGeometry(rec) : null }
    this.host.busy()
    if (status !== 'ok') {
      this.offerNoWatershed(this.engineState, lat, lon, reachFt, gen)
      return
    }
    const out = this.withAnchor(delineateFromEngine(rec, lat, lon, reachFt))
    if (!this.draw(out)) return
    this.delin = out
    this.host.render()
    this.host.resolveAssessment?.()
  }

  /** The current source attached to the result (_with_anchor), its COMID canonical. */
  private withAnchor(d: Result): Result {
    const a = this.host.identify.anchor
    const c = a?.scoredReach?.comid
    const cid = typeof c === 'number' && Number.isInteger(c) && c > 0 ? c : null
    // copies that keep each number's int or float (and the keys' places), as dict() does
    const out = pyCopy(d)
    const ci = pyCopy(d.ctx_inputs ?? {})
    pySet(ci, 'siteAnchor', a)
    pySet(ci, 'comid', cid)
    const dl = pyCopy(d.delineation ?? {})
    pySet(dl, 'comid', cid)
    pySet(out, 'siteAnchor', a)
    pySet(out, 'ctx_inputs', ci)
    pySet(out, 'delineation', dl)
    return out
  }

  private offerNoWatershed(state: EngineState, lat: number, lon: number, reachFt: number, gen: number): void {
    const id = this.host.identify
    const reason = reasonText(state.reason || state.status)
    const anchor = id.anchor
    const pt = id.snapped
    const noReach = this.host.key === 'sfari' ? !id.ready() : anchor?.scoredReach?.comid == null
    if (noReach) {
      notify('The STAF site engine could not compute the HR reach watershed '
        + `(${reason}), and no StreamCat reach is known for this point. Delineate `
        + 'again to retry, or pick a point farther downstream.', { type: 'warning', seconds: 12 })
      return
    }
    const body = `<p>The STAF site engine could not compute the HR reach watershed for this stream (${esc(reason)}).</p>
<p>You can continue with the StreamCat lookup engine: the watershed values then describe the NHDPlus V2 basin of ${esc(id.reachText())}, every such value says so, and no watershed is drawn. Or Delineate again to retry.</p>`
    showChoice('Watershed not available', body, 'Continue with StreamCat values', `${this.host.key}-use_streamcat`, () => {
      if (gen !== this.gen || !id.ready() || id.anchor?.scoredReach?.comid !== anchor?.scoredReach?.comid) return
      const out = delineateWithoutWatershed(anchor, lat, lon, pt, reachFt, state)
      if (!this.draw(out)) return
      this.delin = out
      this.host.render()
      this.host.resolveAssessment?.()
    })
  }

  /** Open (each app.py _load_session): a saved delineation as if this pick had just delineated it, drawn
   * and framed (``center``: where the map goes when there is nothing to frame); SFARI and DEEP restore
   * the engine's state from the saved record. */
  restore(d: Result, center: [number, number] | null = null): boolean {
    this.invalidate()
    if (this.host.key !== 'easi') {
      const se = d.siteEngine
      this.engineState = se && (se.status ?? 'ok') === 'ok' ? { status: 'ok', record: se, reason: null }
        : se ? { ...se } : { status: 'idle' }
    }
    if (!this.draw(d, center)) return false
    this.delin = d
    return true
  }

  // ---------------------------------------------------------------- the map

  private removeWatershed(): void {
    if (!this.ws) return
    this.host.layers.removeLayer(this.ws)
    this.ws.remove()
    this.ws = null
  }

  /** _draw_delineation: the watershed and the reach, the point on top, the map framed. */
  private draw(res: Result, center: [number, number] | null = null): boolean {
    const map = this.host.map
    try {
      this.removeLayers()
      if (res.watershed_geojson) {
        this.ws = L.geoJSON(displaySimplify(res.watershed_geojson) as GeoJSON.FeatureCollection, { style: () => WATERSHED_STYLE }).addTo(map)
        // Layers lists the watershed after the point, and not the reach, where ipyleaflet's rebuilds
        // leave the list
        this.host.layers.addOverlay(this.ws, 'Watershed')
      }
      if (res.reach_geojson) this.reach = L.geoJSON(res.reach_geojson as GeoJSON.FeatureCollection, { style: () => REACH_STYLE }).addTo(map)
      // The tools re-add the point "on top", but ipyleaflet keeps the point's place when it is removed
      // and added in one update, so the Shiny map draws the basin and the reach over it: so here.
      const bounds = geojsonBounds(res.watershed_geojson, res.reach_geojson)
      const d = res.delineation ?? {}
      if (bounds) fitBounds(map, bounds)
      else if (center) map.setView(center, map.getZoom(), { animate: false })
      else if (d.snapped_lat != null) map.setView([d.snapped_lat, d.snapped_lon], map.getZoom(), { animate: false })
    } catch (err) {
      notify(`Could not draw the basin on the map: ${err instanceof Error ? err.message : err}`, { type: 'error', seconds: 8 })
      return false
    }
    return true
  }

  // ---------------------------------------------------------------- the pane

  /** The Basin card (each tool's basin_card). */
  card(): string {
    const res = this.delin ?? {}
    const d = res.delineation ?? {}
    const key = this.host.key
    if (key === 'easi') {
      const anchor = res.siteAnchor ?? {}
      const rows: [string, string][] = []
      let comidRow: [string, string] = ['COMID', String(d.comid)]
      if (anchor.anchorKind === 'hrSurrogate') {
        const source = d.watershed_source || ''
        const eng = d.watershed_engine ?? {}
        if (source === 'not-calculated') rows.push(['Watershed', `unavailable (${reasonText(eng.reason, 'not calculated')})`])
        else if (source !== 'site-engine') rows.push(['Scored at', 'surrogate reach'])
        if (source === 'site-engine' || source === 'not-calculated') comidRow = ['Evidence reach COMID', String(d.comid)]
      }
      rows.push(['Drainage area', km2(d.drainage_area_sqkm)], ['Reach length', feet(d.reach_length_ft)], comidRow)
      return basinCard(key, d.gnis_name || '(unnamed reach)', rows)
    }
    const rows: [string, string][] = [['Drainage area', km2(d.drainage_area_sqkm)], ['Reach length', feet(d.reach_length_ft)]]
    if (d.nhdplus_id !== null && d.nhdplus_id !== undefined) rows.push(['NHDPlusID', String(d.nhdplus_id)])
    rows.push(['StreamCat reach', this.host.identify.reachText(res.siteAnchor)])
    return basinCard(key, d.gnis_name || '(unnamed reach)', rows, this.engineLine())
  }

  /** The engine's progress line while it runs for this site (_engine_progress_text), else null. */
  engineProgress(): string | null {
    return this.engineState?.status === 'running' ? this.stage || progressText({}) : null
  }

  /** SFARI's and DEEP's engine line in the card (_engine_line_ui). */
  engineLine(): string {
    const st = this.engineState?.status
    if (this.running || st === 'running') return `\n<div class="sfari-engine-line">${esc(this.stage || progressText({}))}</div>\n`
    if (st === 'failed' || st === 'refused' || st === 'unavailable') {
      return `\n<div class="sfari-engine-line warn">STAF site engine ${st}: ${esc(reasonText(this.engineState?.reason))}. StreamCat values stand in where the reach has them, labeled. Delineate again to retry.</div>\n`
    }
    return ''
  }

  pane(assess = ''): string {
    return basinPane(this.host.key, this.card(), assess)
  }

  /** The Basin rows of the legend (each app.py _legend_ui on the Basin step). */
  static legendRows(): string {
    return `
  <div class="easi-legend-row">
    <span class="easi-legend-sw easi-legend-sw-fill" style="--swatch-color:${WATERSHED_STYLE.fillColor};"></span>
    <div>
      <div class="easi-legend-label">Watershed</div>
    </div>
  </div>
  <div class="easi-legend-row">
    <span class="easi-legend-sw" style="--swatch-color:${REACH_STYLE.color};"></span>
    <div>
      <div class="easi-legend-label">Assessment reach</div>
    </div>
  </div>`
  }
}

/** A Shiny modal with Cancel and one action (ui.modal(..., footer=[modal_button, action], easy_close)). */
function showChoice(title: string, body: string, action: string, id: string, onAction: () => void): void {
  document.getElementById('shiny-modal-wrapper')?.remove()
  const wrapper = document.createElement('div')
  wrapper.id = 'shiny-modal-wrapper'
  wrapper.innerHTML = `<div id="shiny-modal" class="modal fade" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h4 class="modal-title">${esc(title)}</h4>
      </div>
      <div class="modal-body">${body}</div>
      <div class="modal-footer">
        <button class="btn btn-default" type="button" data-dismiss="modal" data-bs-dismiss="modal">Cancel</button>
        <button class="btn btn-default action-button btn-primary" id="${id}" type="button">
          <span class="action-label">${esc(action)}</span>
        </button>
      </div>
    </div>
  </div>
</div>`
  document.body.appendChild(wrapper)
  const el = wrapper.firstElementChild as HTMLElement
  el.addEventListener('hidden.bs.modal', () => wrapper.remove())
  const modal = new Modal(el)
  el.querySelector(`#${id}`)?.addEventListener('click', () => {
    modal.hide()
    onAction()
  })
  modal.show()
}

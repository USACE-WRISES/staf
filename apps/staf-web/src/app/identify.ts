// A pick on the Identify step: the click snapped to a stream, the point marked, and the NHDPlus V2
// reach the StreamCat lookup engine answers by (each Shiny tool's click handlers, _apply_snap and
// snap_status).
//
// EASI (app.py _handle_click): a click within 150 ft of a V2 line takes that reach, pinned on the
// HR line the map draws when there is one (a V2 line with no HR line near it must be a drawn
// "v2-orphan"); a click on an HR line only is routed to the nearest covered reach downstream
// (routing.route_from_hr). SFARI and DEEP (app.py _apply_snap, comid_anchor.resolve) pin every click
// on the HR line and find its reach in the background by the engine's rule (anchor.classify_click).
// Lines not drawn yet are read around the click (EASI _snap_both, hr_site.snap_point). With
// StreamCat coverage on, the map shows the reach (a glow) and, for a routed site, a dashed line to
// it. Delineate turns on once the reach is known.
//
// On the Shiny map the glow and the route land in Leaflet's overlay pane, with the point: ipyleaflet
// applies a layer's pane only after the layer is made, and these are made with their lines (the
// stream layers, made empty, do get theirs). So they draw there here too, in the order the tools add
// them, and the point is listed in Layers as "Selected point", as ipyleaflet lists every map layer.
import L from 'leaflet'
import { type Anchor, classifyClick, routeFromHr, SNAP_TOL_FT, v2Anchor } from '../engine/anchor'
import { flowlineAt, type FlowlineRecord, knownReach } from '../engine/bundle'
import { type Hit, nearestPointOnLines } from '../engine/snap'
import { buildDisplay, type Feature, type FeatureCollection } from '../streams/display'
import { hrFeatures, type LineRecord, type Network, v2Features } from '../streams/network'
import { esc, type ToolKey } from './markup'
import { notify } from './notify'
import { pyFixed, pyFixedComma } from './pyfmt'

export const POINT_STYLE: L.CircleMarkerOptions = { radius: 7, color: '#1f3b73', fillColor: '#4c8ef5', fillOpacity: 0.95, weight: 2 }
export const SCORED_REACH_STYLE: L.PathOptions = { color: '#1f6feb', weight: 11, opacity: 0.3 }
export const ROUTE_STYLE: L.PathOptions = { color: '#5b6472', weight: 2, dashArray: '6,5', opacity: 0.9 }
const MISS_TEXT = 'No stream line within 150 ft of the click. Zoom in and click a line.'
const MISS_COORDS_TEXT = 'No stream within 150 ft of those coordinates. Adjust them, or zoom in and click a stream line.'
const HR_PICK_HALF_DEG = 0.002
const V2_PROBE_HALF_DEG = 0.012

type Status = 'idle' | 'snapping' | 'finding' | 'retrying' | 'ready' | 'failed' | 'no_match'

/** What the map's stream layers hold for the view: the click rule's input. */
export interface ViewLines {
  hr: FeatureCollection | null
  v2: FeatureCollection | null
  hrLines: LineRecord[]
  v2Lines: LineRecord[]
  covered: FeatureCollection | null
}

export interface IdentifyHost {
  key: ToolKey
  map: L.Map
  /** the map's Layers control, which lists the point */
  layers: L.Control.Layers
  nets: { hr: Network, v2: Network }
  lines(): ViewLines
  /** the glow under the reach and the dashed route (in the Streams group, shown with coverage) */
  setSource(key: 'scored' | 'route', layer: L.GeoJSON | null): void
  changed(): void
}

function validSiteHit(hit: Hit | null): hit is Hit {
  if (!hit || hit.length !== 4) return false
  const [lat, lon, dist, id] = hit
  return [lat, lon, dist].every(Number.isFinite) && id !== null && Number.isInteger(id) && id > 0
    && lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180 && dist >= 0 && dist <= SNAP_TOL_FT
}

function featureById(fc: FeatureCollection | null, prop: string, value: number | null): Feature | null {
  if (!fc || value === null) return null
  return fc.features.find((f) => f.properties?.[prop] !== undefined && f.properties[prop] !== null && Number(f.properties[prop]) === value) ?? null
}

/** ``{:,.0f} ft`` */
function ft(value: unknown): string | null {
  const v = Number(value)
  return value === null || value === undefined || !Number.isFinite(v) ? null : `${pyFixedComma(v, 0)} ft`
}

/** ``32`` at or above ten, ``2.7`` below, null when unknown (basin.fmt_ratio, comid_anchor.fmt_ratio). */
function fmtRatio(value: unknown): string | null {
  const v = Number(value)
  if (value === null || value === undefined || !Number.isFinite(v) || v <= 0) return null
  return v >= 10 ? pyFixed(v, 0) : pyFixed(v, 1)
}

function comidOf(anchor: Anchor | null): number | null {
  const c = anchor?.scoredReach?.comid
  return typeof c === 'number' && Number.isInteger(c) && c > 0 ? c : null
}

function isRouted(anchor: Anchor | null): boolean {
  return anchor?.anchorKind === 'hrSurrogate'
}

export class Identify {
  private generation = 0
  /** (lat, lon, distance ft, id): EASI's id is the reach's COMID once known, SFARI's and DEEP's the
   * HR line's */
  snapped: Hit | null = null
  anchor: Anchor | null = null
  /** the reach on the legend: {comid, name} */
  reach: { comid: number, name: string | null } | null = null
  private status: Status = 'idle'
  private attempt = 1
  private detail = ''
  private marker: L.CircleMarker | null = null
  private request: (() => void) | null = null      // the lookup to run again (Retry StreamCat lookup)
  glow = false
  route = false

  constructor(private host: IdentifyHost) {}

  /** Delineate can run: the site's reach is known (each app.py _source_ready). */
  ready(): boolean {
    return this.status === 'ready' && this.snapped !== null && (this.host.key === 'easi' ? this.snapped[3] !== null : comidOf(this.anchor) !== null)
  }

  // ---------------------------------------------------------------- the map's pieces

  private placePin(lat: number, lon: number): void {
    if (this.marker) {
      this.marker.setLatLng([lat, lon])
    } else {
      this.marker = L.circleMarker([lat, lon], POINT_STYLE).addTo(this.host.map)
      this.host.layers.addOverlay(this.marker, 'Selected point')
    }
    for (const [name, v] of [['lat', lat], ['lon', lon]] as const) {
      const input = document.getElementById(`${this.host.key}-${name}`) as HTMLInputElement | null
      if (!input) continue
      input.value = String(Number(pyFixed(v, 5)))
      input.setAttribute('aria-live', 'polite')     // as Shiny's update_numeric leaves it
    }
  }

  private removePin(): void {
    if (this.marker) {
      this.host.layers.removeLayer(this.marker)
      this.marker.remove()
    }
    this.marker = null
  }

  private setScored(feature: Feature | null): void {
    const layer = feature?.geometry
      ? L.geoJSON({ type: 'FeatureCollection', features: [feature] } as GeoJSON.FeatureCollection,
        { style: () => SCORED_REACH_STYLE })
      : null
    this.glow = !!layer
    this.host.setSource('scored', layer)
  }

  private setRoute(anchor: Anchor | null): void {
    const clicked = anchor?.clickedStream ?? {}
    const scored = anchor?.scoredReach ?? {}
    const ok = isRouted(anchor) && clicked.snapLat != null && scored.snapLat != null
    const layer = ok
      ? L.geoJSON({ type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: {
        type: 'LineString', coordinates: [[clicked.snapLon, clicked.snapLat], [scored.snapLon, scored.snapLat]] } }] } as GeoJSON.FeatureCollection,
      { style: () => ROUTE_STYLE })
      : null
    this.route = !!layer
    this.host.setSource('route', layer)
  }

  /** The reach's own line when the view's V2 lines lack it (each app's source_geometry_task). */
  private async loadScored(comid: number, record: LineRecord | null, gen: number): Promise<void> {
    const rec = record ? await flowlineAt(this.host.nets.v2.src, 'bundle/v2/', record.region, record.row) : null
    if (gen !== this.generation || !rec || comidOf(this.anchor) !== comid && this.snapped?.[3] !== comid) return
    this.setScored({ type: 'Feature', properties: { comid, gnis_name: rec.gnis_name }, geometry: rec.geometry })
    if (rec.gnis_name && this.host.key === 'easi') this.reach = { comid, name: rec.gnis_name }
    this.host.changed()
  }

  private hrRecordOf(hit: Hit, lines: LineRecord[]): Promise<FlowlineRecord | null> {
    const line = lines.find((l) => l.id === hit[3])
    return line ? flowlineAt(this.host.nets.hr.src, 'bundle/', line.region, line.row) : Promise.resolve(null)
  }

  // ---------------------------------------------------------------- the pick

  /** A click on the map at the stream zoom, on Identify. */
  click(lat: number, lon: number): void {
    this.generation += 1
    if (this.host.key === 'easi') void this.clickEasi(lat, lon, this.generation, false)
    else void this.clickHr(lat, lon, this.generation, false)
  }

  /** Typed coordinates: the lines around the point are read, whatever the view draws (each app's
   * coord_snap_task). */
  typed(lat: number, lon: number): void {
    this.generation += 1
    if (this.host.key === 'easi') void this.clickEasi(lat, lon, this.generation, true)
    else void this.clickHr(lat, lon, this.generation, true)
  }

  private reset(status: Status): void {
    this.setScored(null)
    this.setRoute(null)
    this.anchor = null
    this.reach = null
    this.request = null
    this.status = status
    this.attempt = 1
    this.detail = ''
  }

  /** The lines around a point the view has not drawn (EASI _snap_both, hr_site.snap_point). */
  private async around(lat: number, lon: number): Promise<ViewLines> {
    const h = HR_PICK_HALF_DEG
    const d = V2_PROBE_HALF_DEG
    const [hrLines, v2Lines] = await Promise.all([
      this.host.nets.hr.inBox(lon - h, lat - h, lon + h, lat + h),
      this.host.key === 'easi' ? this.host.nets.v2.inBox(lon - d, lat - d, lon + d, lat + d) : Promise.resolve([] as LineRecord[]),
    ])
    const hr = hrFeatures(hrLines)
    const v2 = v2Features(v2Lines)
    return { hr, v2, hrLines, v2Lines, covered: null }
  }

  // ---- EASI

  private async clickEasi(lat: number, lon: number, gen: number, typed: boolean): Promise<void> {
    this.reset('snapping')
    this.snapped = null
    this.host.changed()
    const view = this.host.lines()
    const hit = !typed && view.v2 ? nearestPointOnLines(view.v2, lat, lon, 'comid') : null
    const hrHit = !typed && view.hr ? nearestPointOnLines(view.hr, lat, lon, 'nhdplusid') : null
    if (!typed && view.v2 && view.hr && hit && hit[2] <= SNAP_TOL_FT) {
      const visible = validSiteHit(hrHit) ? hit : this.visibleV2Hit(hit, view.covered, lat, lon)
      if (visible) {
        this.applySnapEasi(visible, featureById(view.v2, 'comid', hit[3]), view, validSiteHit(hrHit) ? hrHit : null, [lat, lon], gen)
        return
      }
    }
    if (!typed && view.v2 && validSiteHit(hrHit)) {
      this.startRoute(lat, lon, hrHit, view.hrLines, gen)
      return
    }
    let res: ViewLines
    try {
      res = await this.around(lat, lon)
    } catch {
      if (gen !== this.generation) return
      this.status = 'failed'
      this.detail = 'snap_error'
      this.host.changed()
      return
    }
    if (gen !== this.generation) return
    // the snap result reconciled with the lines the map draws (_display_snap_result)
    let rh = res.hr ? nearestPointOnLines(res.hr, lat, lon, 'nhdplusid') : null
    let rHit = res.v2 ? nearestPointOnLines(res.v2, lat, lon, 'comid') : null
    const now = this.host.lines()
    if (!validSiteHit(rh) && now.hr?.features.length) {
      const cached = nearestPointOnLines(now.hr, lat, lon, 'nhdplusid')
      if (validSiteHit(cached)) rh = cached
    }
    if (!validSiteHit(rh)) {
      const covered = now.hr?.features.length ? now.covered : buildDisplay(res.v2, res.hr, SNAP_TOL_FT).covered
      rHit = this.visibleV2Hit(rHit, covered, lat, lon)
    }
    if (rHit && rHit[2] <= SNAP_TOL_FT) {
      const feat = featureById(res.v2, 'comid', rHit[3]) ?? featureById(now.v2, 'comid', rHit[3])
      this.applySnapEasi(rHit, feat, res, validSiteHit(rh) ? rh : null, [lat, lon], gen)
      return
    }
    if (validSiteHit(rh)) {
      this.startRoute(lat, lon, rh, res.hrLines, gen)
      return
    }
    this.removePin()
    this.status = 'idle'
    this.snapped = null
    this.host.changed()
    if (typed) notify(MISS_COORDS_TEXT, { type: 'warning', seconds: 6 })
    else notify(MISS_TEXT, { type: 'warning', seconds: 5 })
  }

  /** Only a drawn V2 orphan stretch can supply a V2 site pin. */
  private visibleV2Hit(hit: Hit | null, covered: FeatureCollection | null, lat: number, lon: number): Hit | null {
    if (!hit || hit[2] > SNAP_TOL_FT) return null
    const visible: FeatureCollection = { type: 'FeatureCollection', features: (covered?.features ?? []).filter((f) =>
      f.properties?.comid === hit[3] && f.properties?.cover === 'v2-orphan') }
    const selected = nearestPointOnLines(visible, lat, lon)
    return selected && selected[2] <= SNAP_TOL_FT ? selected : null
  }

  private applySnapEasi(hit: Hit, feature: Feature | null, view: ViewLines, siteHit: Hit | null,
    clickedAt: [number, number] | null, gen: number): void {
    let [slat, slon, dist] = hit
    const comid = hit[3] as number
    let anchor = this.anchor ?? {}
    const [clat, clon] = clickedAt ?? [slat, slon]
    if (siteHit) {
      anchor = v2Anchor(comid, clat, clon, slat, slon, dist)
      ;[slat, slon, dist] = siteHit
      anchor.selectedSite = { network: 'nhdplus-hr', nhdplusid: siteHit[3], snapLat: slat, snapLon: slon, snapDistFt: dist }
      this.anchor = anchor
    } else if (anchor.anchorKind !== 'hrSurrogate') {
      anchor = v2Anchor(comid, clat, clon, slat, slon, dist)
      this.anchor = anchor
    }
    this.reach = { comid, name: anchor.scoredReach?.gnisName ?? null }
    if (feature?.geometry) {
      this.setScored(feature)
      this.reach = { comid: Number(feature.properties?.comid ?? comid), name: (feature.properties?.gnis_name as string) ?? null }
    } else {
      this.setScored(null)
    }
    this.placePin(slat, slon)
    this.snapped = [slat, slon, dist, comid]
    this.status = 'ready'
    this.host.changed()
    if (!feature) void this.loadScored(comid, view.v2Lines.find((l) => l.id === comid) ?? null, gen)
  }

  private startRoute(lat: number, lon: number, hrHit: Hit, hrLines: LineRecord[], gen: number): void {
    this.placePin(hrHit[0], hrHit[1])
    this.snapped = [hrHit[0], hrHit[1], hrHit[2], null]
    this.status = 'finding'
    this.attempt = 1
    this.request = () => {
      this.generation += 1
      this.reset('finding')
      this.startRoute(lat, lon, hrHit, hrLines, this.generation)
    }
    this.host.changed()
    void (async () => {
      const rec = await this.hrRecordOf(hrHit, hrLines).catch(() => null)
      const res = await routeFromHr(this.host.nets.hr.src, lat, lon, hrHit, rec, 'easi')
      if (gen !== this.generation) return
      if ('error' in res) {
        this.status = res.error === 'no_stream_found' ? 'no_match' : 'failed'
        this.detail = res.detail ?? ''
        this.host.changed()
        return
      }
      const anchor = res.anchor
      this.setRoute(anchor)
      const clicked = anchor.clickedStream ?? {}
      const scored = anchor.scoredReach ?? {}
      let sLat = clicked.snapLat
      let sLon = clicked.snapLon
      if (sLat == null) {
        sLat = scored.snapLat
        sLon = scored.snapLon
      }
      this.anchor = anchor
      this.applySnapEasi([sLat, sLon, clicked.snapDistFt || 0.0, scored.comid], null, this.host.lines(), null, null, gen)
      void this.loadScoredRouted(scored.comid, gen)
    })()
  }

  /** A routed site's reach line: the raindrop read it already (bundle.ts keeps it). */
  private async loadScoredRouted(comid: number, gen: number): Promise<void> {

    const rec = knownReach(comid)
    if (gen !== this.generation || !rec) return
    this.setScored({ type: 'Feature', properties: { comid, gnis_name: rec.gnis_name }, geometry: rec.geometry })
    this.host.changed()
  }

  // ---- SFARI and DEEP

  private async clickHr(lat: number, lon: number, gen: number, typed: boolean): Promise<void> {
    this.removePin()
    this.reset('snapping')
    this.snapped = null
    this.host.changed()
    const view = this.host.lines()
    const hit = !typed && view.hr ? nearestPointOnLines(view.hr, lat, lon, 'nhdplusid') : null
    if (hit && hit[2] <= SNAP_TOL_FT) {
      this.applySnapHr(hit, [lat, lon], view, gen)
      return
    }
    let res: ViewLines
    try {
      res = await this.around(lat, lon)
    } catch (err) {
      if (gen !== this.generation) return
      this.snapped = null
      this.status = 'failed'
      this.detail = String(err)
      this.host.changed()
      return
    }
    if (gen !== this.generation) return
    const rh = res.hr ? nearestPointOnLines(res.hr, lat, lon, 'nhdplusid') : null
    if (rh && rh[2] <= SNAP_TOL_FT) {
      this.applySnapHr(rh, [lat, lon], { ...view, hrLines: [...res.hrLines, ...view.hrLines] }, gen)
      return
    }
    this.removePin()
    this.snapped = null
    this.status = 'no_match'
    this.detail = 'No stream was found at this point.'
    this.host.changed()
    notify(typed ? MISS_COORDS_TEXT : MISS_TEXT, { type: 'warning', seconds: 6 })
  }

  private applySnapHr(hit: Hit, click: [number, number], view: ViewLines, gen: number): void {
    this.setScored(null)
    this.setRoute(null)
    this.anchor = null
    this.reach = null
    this.placePin(hit[0], hit[1])
    this.snapped = hit
    const [lat, lon] = click
    this.request = () => {
      this.generation += 1
      this.lookup(lat, lon, hit, view, this.generation)
    }
    this.lookup(lat, lon, hit, view, gen)
  }

  /** The reach for the site, in the background (comid_anchor.resolve). */
  private lookup(lat: number, lon: number, hit: Hit, view: ViewLines, gen: number): void {
    this.status = 'finding'
    this.attempt = 1
    this.host.changed()
    void (async () => {
      let fc = view.v2?.features.length ? view.v2 : null
      let v2Lines = view.v2Lines
      try {
        if (!fc) {
          const d = V2_PROBE_HALF_DEG
          v2Lines = await this.host.nets.v2.inBox(lon - d, lat - d, lon + d, lat + d)
          fc = v2Features(v2Lines)
        }
        const v2Hit = fc ? nearestPointOnLines(fc, lat, lon, 'comid') : null
        const rec = await this.hrRecordOf(hit, view.hrLines).catch(() => null)
        const res = await classifyClick(this.host.nets.hr.src, lat, lon, v2Hit, hit, rec)
        if (gen !== this.generation) return
        if ('error' in res) {
          this.status = res.error === 'no_stream_found' ? 'no_match' : 'failed'
          this.detail = res.detail ?? ''
          this.host.changed()
          return
        }
        const anchor = res.anchor
        if (anchor.anchorKind === 'v2Direct' && v2Hit) {
          const feat = featureById(fc, 'comid', v2Hit[3])
          const name = feat?.properties?.gnis_name
          if (name && !anchor.scoredReach.gnisName) anchor.scoredReach.gnisName = String(name).trim() || null
          await this.fillCovered(anchor.scoredReach, v2Lines.find((l) => l.id === v2Hit[3]) ?? null)
        }
        if (gen !== this.generation) return
        const cid = comidOf(anchor)
        this.anchor = anchor
        const scoredName = anchor.scoredReach?.gnisName
        this.reach = cid !== null ? { comid: cid, name: scoredName ? String(scoredName).trim() || null : null } : null
        const scoredFeature = featureById(view.v2, 'comid', cid)
        this.setScored(scoredFeature)
        this.setRoute(anchor)
        if (this.marker) this.marker.bringToFront()
        this.status = cid === null ? 'failed' : 'ready'
        this.host.changed()
        if (cid !== null && !scoredFeature) {
          if (isRouted(anchor)) void this.loadScoredRouted(cid, gen)
          else void this.loadScored(cid, v2Lines.find((l) => l.id === cid) ?? null, gen)
        }
      } catch (err) {
        if (gen !== this.generation) return
        this.status = 'failed'
        this.detail = String(err)
        this.host.changed()
      }
    })()
  }

  /** A covered reach's drainage area, slope, FCODE, order, reach code and name (comid_anchor
   * _fill_covered_reach; the static app reads the bundle's V2 attributes). */
  private async fillCovered(scored: Anchor, line: LineRecord | null): Promise<void> {
    const fields: [string, keyof FlowlineRecord][] = [['drainageAreaSqkm', 'totdasqkm'], ['slope', 'slope'], ['fcode', 'fcode'],
      ['streamOrder', 'stream_order'], ['reachcode', 'reachcode'], ['gnisName', 'gnis_name']]
    if (scored.comid == null || fields.every(([k]) => scored[k] != null) || !line) return
    const rec = await flowlineAt(this.host.nets.v2.src, 'bundle/v2/', line.region, line.row).catch(() => null)
    if (!rec) return
    for (const [key, src] of fields) if (scored[key] == null && rec[src] != null) scored[key] = rec[src]
  }

  /** Retry StreamCat lookup. */
  retry(): void {
    if ((this.status === 'failed' || this.status === 'no_match') && this.request) this.request()
  }

  // ---------------------------------------------------------------- what the pane says

  /** The snap status output (each app.py snap_status). */
  snapStatus(): string {
    const k = this.host.key
    if (k === 'easi') return this.snapStatusEasi()
    const pt = this.snapped
    if (!pt) return '<p class="easi-snap-note">No point yet.</p>'
    const lines = [`<p class="easi-snap-note ok">✓ Snapped to a stream (${pyFixed(pt[2], 0)} ft away).</p>`]
    const line = this.snapLine()
    if (line) lines.push(`<p class="easi-snap-note">${esc(line)}</p>`)
    const atomic = k === 'sfari' ? ' aria-atomic="true"' : ''
    return `<div role="status" aria-live="polite"${atomic}>\n  ${lines.join('\n  ')}\n</div>`
  }

  private lookupMessage(): string | null {
    const messages: Record<string, string> = {
      snapping: 'Finding the selected stream…',
      finding: 'Finding the nearest StreamCat reach…',
      retrying: `Retrying StreamCat lookup (${Math.max(1, Math.min(3, this.attempt - 1))} of 3)…`,
      no_match: 'No StreamCat reach was found downstream. Retry the lookup or choose another stream.',
      failed: 'Could not reach the StreamCat routing service. Retry the lookup to continue.',
    }
    return messages[this.status] ?? null
  }

  private snapStatusEasi(): string {
    const message = this.lookupMessage()
    if (message) {
      const text = this.status === 'failed' && this.detail === 'snap_error' ? 'Could not load the stream network. Select the point again.' : message
      const retry = (this.status === 'failed' || this.status === 'no_match') && this.request
        ? '\n  <button class="btn btn-default action-button btn-outline-secondary btn-sm" id="easi-retry_streamcat" type="button">\n    <span class="action-label">Retry StreamCat lookup</span>\n  </button>'
        : ''
      const spinner = ['snapping', 'finding', 'retrying'].includes(this.status)
        ? '<span class="easi-spinner easi-lookup-spinner" aria-hidden="true"></span>' : ''
      return `<div role="status" aria-live="polite">\n  <p class="easi-snap-note">${spinner}${esc(text)}</p>${retry}\n</div>`
    }
    if (this.anchor?.anchorKind === 'hrSurrogate' && this.status === 'ready') {
      const card = this.hrSnapCard(this.anchor)
      const lines = card.lines.map(([cls, text], i) => {
        const tip = i === card.lines.length - 1
          ? ` <span onclick="event.preventDefault();event.stopPropagation();" tabindex="0" data-tip-html="${esc(card.tipHtml)}" class="easi-info">i</span>` : ''
        // htmltools breaks the line inside a paragraph that holds a tag
        return tip
          ? `<p class="${`easi-snap-note ${cls}`.trim()}">\n    ${esc(text)}${tip}\n  </p>`
          : `<p class="${`easi-snap-note ${cls}`.trim()}">${esc(text)}</p>`
      })
      return `<div role="status" aria-live="polite">\n  ${lines.join('\n  ')}\n</div>`
    }
    const pt = this.snapped
    if (!pt) return '<p class="easi-snap-note">No point yet.</p>'
    const name = this.reach?.name || 'the stream'
    return `<div role="status" aria-live="polite">\n  <p class="easi-snap-note ok">✓ Snapped to ${esc(name)} (${pyFixed(pt[2], 0)} ft away).</p>\n`
      + '  <p class="easi-snap-note">StreamCat source reach resolved.</p>\n</div>'
  }

  /** EASI's card for a routed site (snapcard.hr_snap_card). */
  private hrSnapCard(anchor: Anchor): { lines: [string, string][], tipHtml: string } {
    const clicked = anchor.clickedStream ?? {}
    const scored = anchor.scoredReach ?? {}
    const routing = anchor.routing ?? {}
    const name = clicked.gnisName || 'an unnamed stream'
    const snapFt = ft(clicked.snapDistFt)
    const where = snapFt ? `Snapped to ${name} (${snapFt} away).` : `Snapped to ${name}.`
    const reachName = scored.gnisName || 'an unnamed reach'
    const reachId = scored.comid != null ? `${reachName} (COMID ${scored.comid})` : reachName
    const routed = ft(routing.routedDistanceFt)
    const ratio = fmtRatio(routing.daRatio)
    const line2 = `Downstream reach: ${reachName}, ${routed ? routed + ' ' : ''}downstream.`
    const why = `Low flow, substrate, and biological integrity come from ${reachId}, the nearest StreamCat reach`
      + (routed ? `, ${routed} downstream` : '') + '.' + (ratio ? ` It drains ${ratio} times this stream.` : '')
    return {
      lines: [['ok', `✓ ${where}`], ['', line2]],
      tipHtml: `<div class="easi-tip-title">Downstream reach</div><div class="easi-tip-sec">${esc(why)}</div>`,
    }
  }

  /** SFARI's and DEEP's third line once the reach is known (comid_anchor.snap_line). */
  private snapLine(): string | null {
    const c = comidOf(this.anchor)
    if (c === null) return null
    if (!isRouted(this.anchor)) return `StreamCat values come from this reach (COMID ${c}).`
    const scored = this.anchor?.scoredReach ?? {}
    const name = scored.gnisName && String(scored.gnisName).trim() ? String(scored.gnisName).trim() : 'an unnamed reach'
    const dist = ft(this.anchor?.routing?.routedDistanceFt)
    const ratio = fmtRatio(this.anchor?.routing?.daRatio)
    let txt = `StreamCat values come from ${name} (COMID ${c})`
    if (dist) txt += `, ${dist} downstream`
    if (ratio) txt += `, which drains ${ratio} times this stream`
    return txt + '.'
  }

  /** SFARI's and DEEP's lookup status output (streamcat_lookup_status). */
  lookupStatus(): string {
    if (this.host.key === 'easi' || this.status === 'idle' || this.status === 'ready') return ''
    const messages: Record<string, string> = {
      snapping: 'Finding the selected stream…',
      finding: 'Finding the nearest StreamCat reach…',
      retrying: `Retrying StreamCat lookup (${Math.max(1, Math.min(3, this.attempt - 1))} of 3)…`,
      failed: this.snapped === null ? 'Could not locate the selected stream. Choose the stream again.'
        : 'Could not reach the StreamCat routing service. Retry the lookup to continue.',
      no_match: 'No StreamCat reach was found downstream. Retry the lookup or choose another stream.',
    }
    const retry = (this.status === 'failed' || this.status === 'no_match') && this.snapped !== null
      ? `\n  <button class="btn btn-default action-button btn-outline-secondary btn-sm" id="${this.host.key}-retry_streamcat" type="button">\n    <span class="action-label">Retry StreamCat lookup</span>\n  </button>`
      : ''
    const spinner = ['snapping', 'finding', 'retrying'].includes(this.status)
      ? '<span class="easi-spinner easi-lookup-spinner" aria-hidden="true"></span>' : ''
    return `<div role="status" aria-live="polite" aria-atomic="true">\n  <p class="easi-snap-note">${spinner}${esc(messages[this.status] ?? '')}</p>${retry}\n</div>`
  }

  /** The legend's rows for the reach (each app.py _legend_ui), when StreamCat coverage is on. */
  legendRows(): { color: string, label: string, kind: 'glow' | 'dashed' }[] {
    const rows: { color: string, label: string, kind: 'glow' | 'dashed' }[] = []
    if (this.glow && this.reach) {
      const name = this.reach.name || 'unnamed stream'
      const what = isRouted(this.anchor) ? 'Downstream source' : 'StreamCat source'
      rows.push({ color: '#1f6feb', label: `${what}: ${name}`, kind: 'glow' })
    }
    if (this.route) rows.push({ color: '#5b6472', label: 'Connection to source', kind: 'dashed' })
    return rows
  }
}

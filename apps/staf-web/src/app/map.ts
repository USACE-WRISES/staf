// A tool's map, made the way the Shiny apps make theirs with ipyleaflet 0.20.
//
// Every tool builds the same map (apps/easi/app.py _build_map, and SFARI's and DEEP's copies): the
// USGS Imagery and USGS Topo basemaps (Topo shown), the NHD Hydrography overlay at 0.85, the panes
// the stream lines draw in, the Streams layer group, the Layers control (top right) and the scale
// (bottom right, metric and imperial). jupyter-leaflet makes the Leaflet map in a box inside the
// widget's element, with Leaflet's own zoom and attribution controls off, then adds ipyleaflet's
// (zoom top left, "+" and "-"; attribution bottom right). Its options are ipyleaflet's defaults
// where they differ from Leaflet's (inertia's top speed), and the container's cursor is the
// widget's style: grab, and move while the map moves.
//
// The attribution names Leaflet where the Shiny map names ipyleaflet: the static app draws with
// Leaflet itself.
import L from 'leaflet'
import { START } from './markup'

const USGS_TOPO_URL = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}'
const USGS_IMAGERY_URL = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}'
const USGS_HYDRO_URL = 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}'
const USGS_ATTR = 'USGS The National Map'

/** Below the assessment vectors (Leaflet's overlay pane is 400), above the basemaps; separate panes
 * keep a stream refresh from covering the selected reach or pin. */
export const STREAM_PANES: Record<string, Partial<CSSStyleDeclaration>> = {
  'staf-source-glow': { zIndex: '385', pointerEvents: 'none' },
  'staf-streams': { zIndex: '390' },
  'staf-source-route': { zIndex: '395', pointerEvents: 'none' },
}

/** The USGS basemap caches stop at zoom 16; past that Leaflet scales the zoom-16 tiles up. */
function usgs(url: string, opacity = 1): L.TileLayer {
  return L.tileLayer(url, { attribution: USGS_ATTR, maxNativeZoom: 16, maxZoom: 19, opacity })
}

export interface ToolMap {
  map: L.Map
  /** the Streams group: the HR and V2 lines and the source reach's glow and route */
  streams: L.LayerGroup
  layers: L.Control.Layers
}

/** Makes the map in the tool's widget box (``.leaflet-widgets``). */
export function makeMap(box: HTMLElement): ToolMap {
  const el = document.createElement('div')      // jupyter-leaflet's map_container
  box.appendChild(el)
  const map = L.map(el, {
    center: [START.lat, START.lon], zoom: START.zoom, maxZoom: 19, scrollWheelZoom: true,
    zoomControl: false, attributionControl: false,
    tapTolerance: 15, keyboardPanDelta: 80, inertiaDeceleration: 3000, inertiaMaxSpeed: 1500,
    zoomAnimationThreshold: 4,
  })
  el.style.cursor = 'grab'
  map.on('movestart', () => { el.style.cursor = 'move' })
  map.on('moveend', () => { el.style.cursor = 'grab' })
  // ipyleaflet's controls, in the order it adds them
  L.control.zoom({ position: 'topleft', zoomInText: '+', zoomOutText: '-' }).addTo(map)
  L.control.attribution({ position: 'bottomright', prefix: 'Leaflet' }).addTo(map)
  const imagery = usgs(USGS_IMAGERY_URL)
  const topo = usgs(USGS_TOPO_URL)
  const hydro = usgs(USGS_HYDRO_URL, 0.85)
  topo.addTo(map)                                // the last base layer added is the one shown
  hydro.addTo(map)
  for (const [name, style] of Object.entries(STREAM_PANES)) Object.assign(map.createPane(name).style, style)
  const streams = L.layerGroup().addTo(map)
  const layers = L.control.layers({ 'USGS Imagery': imagery, 'USGS Topo': topo },
    { 'NHD Hydrography': hydro, Streams: streams }, { position: 'topright' }).addTo(map)
  L.control.scale({ position: 'bottomright', metric: true, imperial: true }).addTo(map)
  // jupyter-leaflet listens for every pointer event on the map (it sends them to Python); DEEP's
  // coverage.js finds a map it did not see made by moving the pointer over it, which needs a listener
  map.on('mousemove', () => {})
  return { map, streams, layers }
}

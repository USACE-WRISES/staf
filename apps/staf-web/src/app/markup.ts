// The STAF page's markup: the header and each tool's section, as the Shiny STAF app renders them.
//
// The Shiny app (apps/staf) draws its page on the server; this module writes the same elements,
// attributes, classes and text, so the tools' stylesheets (loaded unchanged, vite.config.ts) give
// the same pixels. The references are parity/shiny/<tool>.html, captured from the running app by
// parity/capture_shiny.py. Ids keep each tool's prefix ("easi-lat"), as in the Shiny app, where
// every tool is a module. The wrappers Shiny adds (.shiny-html-output, .form-group
// .shiny-input-container) stay too: the stylesheets select on them.
//
// The page is written into index.html at build time (vite.config.ts), so it paints before any
// script runs; the shown tool comes from the address (?tool=, ?assessment=) in the head's inline
// script and the switch's own (switchScript). Pieces that change at run time (the left pane, the
// readout) are rendered again by the same functions.

import { pyFixed } from './pyfmt'

export type ToolKey = 'easi' | 'sfari' | 'deep'

export const ORDER: readonly ToolKey[] = ['easi', 'sfari', 'deep']

/** The STAF site: the header's STAF link. */
export const SITE = 'https://usace-wrises.github.io/staf/'

export interface ToolInfo {
  name: string
  fullName: string
  tier: string
  /** the Identify card's search hint */
  placeholder: string
  /** the workflow's steps, as each tool keys them */
  steps: readonly (readonly [string, string])[]
}

export const TOOLS: Record<ToolKey, ToolInfo> = {
  easi: {
    name: 'EASI', fullName: 'Ecosystem Assessment Screening Index', tier: 'Screening',
    placeholder: 'e.g. Atlanta, GA  ·  Utoy Creek',
    steps: [['identify', 'Identify'], ['basin', 'Basin'], ['assess', 'Assessment'], ['report', 'Report']],
  },
  sfari: {
    name: 'SFARI', fullName: 'Stream Functions Assessment and Rapid Index', tier: 'Rapid',
    placeholder: 'e.g. Atlanta, GA  ·  Utoy Creek',
    steps: [['identify', 'Identify'], ['basin', 'Basin'], ['review', 'Assessment'], ['report', 'Report']],
  },
  deep: {
    name: 'DEEP', fullName: 'Detailed Evaluation of Ecosystem Processes', tier: 'Detailed',
    placeholder: 'e.g. Asheville, NC  ·  Mud Creek',
    steps: [['identify', 'Identify'], ['basin', 'Basin'], ['measure', 'Assessment'], ['report', 'Report']],
  },
}

/** The map's center and zoom when a tool opens (every tool's ipyleaflet Map). */
export const START = { lat: 39.5, lon: -98.35, zoom: 4 }
/** Stream lines show, and a click picks one, from this zoom on. */
export const FLOW_ZOOM = 14

export function esc(text: string): string {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] as string)
}

// ------------------------------------------------------------------------------------------ header

function switchButton(key: ToolKey, active: ToolKey): string {
  const t = TOOLS[key]
  const on = key === active
  return `<button type="button" role="tab" data-tool="${key}" aria-selected="${on}" tabindex="${on ? 0 : -1}" `
    + `title="${t.fullName}" aria-label="${t.name}, ${t.fullName}, ${t.tier} tier" `
    + `class="staf-tool-btn${on ? ' active' : ''}">${t.name}</button>`
}

/** The header's actions, the same in every tool (staf_workbook web.nav_actions). */
export function navActions(key: ToolKey): string {
  // Save starts off, as a Shiny download link does until its tool's server is running (the tool is
  // shown): Tool.show() turns it on
  const save = `<a class="btn btn-default shiny-download-link disabled easi-nav-btn" id="${key}-save_session" href="" target="_blank" rel="noopener" aria-disabled="true" tabindex="-1" aria-live="polite">Save</a>`
  return `<div class="easi-nav">
                <a class="action-button action-link" id="${key}-nav_new" href="#">
<span class="action-label">New</span>
</a>
                <div class="form-group shiny-input-container">
                  <label class="control-label shiny-label-null" id="${key}-load_session-label" for="${key}-load_session"></label>
                  <div class="input-group">
                    <label class="input-group-btn input-group-prepend">
<span class="btn btn-default btn-file">Open<input id="${key}-load_session" name="${key}-load_session" type="file" accept=".json" style="position: absolute !important; top: -99999px !important; left: -99999px !important;" class="shiny-input-file">
</span>
</label>
<input type="text" class="form-control" placeholder="No file selected" readonly="readonly">
                  </div>
                  <div id="${key}-load_session_progress" class="progress active shiny-file-input-progress">
                    <div class="progress-bar"></div>
                  </div>
                </div>
                ${save}
<a class="action-button action-link easi-nav-sep" id="${key}-nav_about" href="#">
<span class="action-label">About</span>
</a>
<a class="action-button action-link" id="${key}-nav_help" href="#">
<span class="action-label">Help</span>
</a>
              </div>`
}

export function header(active: ToolKey = 'easi'): string {
  const tiers = ORDER.map((key) => `<span data-staf-tool="${key}" class="staf-tier">${TOOLS[key].tier}</span>`)
    .join('\n<span class="staf-tier-sep">›</span>\n')
  const navs = ORDER.map((key) => `<div data-staf-tool="${key}" class="staf-slot staf-nav">
              ${navActions(key)}
            </div>`).join('\n            ')
  return `<div class="staf-header easi-header">
          <div class="staf-header-left">
            <a href="${SITE}" target="_blank" rel="noopener" class="staf-brand" title="Stream Tiered Assessment Framework">STAF</a>
            <div class="staf-tiers" aria-hidden="true">
              ${tiers}
            </div>
          </div>
          <div class="staf-header-center">
            <div id="staf_tool" class="staf-tool-switch" role="tablist" aria-label="STAF tools">
              ${ORDER.map((key) => switchButton(key, active)).join('\n')}
            </div>
            <div data-staf-tool="easi" class="staf-slot staf-center">
              <div class="easi-mode-toggle"></div>
            </div>
          </div>
          <div class="staf-header-right">
            ${navs}
            <button type="button" aria-expanded="false" class="staf-menu-btn">Menu</button>
          </div>
        </div>`
}

/** Marks the shown tool's switch button before the first paint (the page is built for EASI). */
export const switchScript = `<script>(function () {
  var tool = document.documentElement.getAttribute("data-staf-tool");
  document.querySelectorAll(".staf-tool-btn").forEach(function (b) {
    var on = b.getAttribute("data-tool") === tool;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
})();</script>`

// ------------------------------------------------------------------------------------- the left pane

/** The step navigator, as each tool draws it. */
export function stepper(key: ToolKey, active: string): string {
  let done = true
  const items = TOOLS[key].steps.map(([step, label]) => {
    let cls = 'easi-step'
    if (step === active) {
      cls += ' active'
      done = false
    } else if (done) {
      cls += ' done'
    }
    const on = step === active
    if (key === 'deep') {
      return `<a data-step="${step}" role="button" tabindex="0"${on ? ' aria-current="step"' : ''} class="${cls}">${label}</a>`
    }
    // the Report step takes the focus (staf report-ready.js)
    const report = step === 'report'
    if (key === 'sfari') {
      return `<a data-step="${step}" role="button" class="${cls}"${report ? ' tabindex="0"' : ''}>${label}</a>`
    }
    return `<a data-step="${step}" class="${cls}"${report ? ' role="button" tabindex="0"' : ''}>${label}</a>`
  })
  return `<div class="easi-steps">
    ${items.join('\n')}
  </div>`
}

function numeric(key: ToolKey, id: string, label: string, attrs: string): string {
  return `<div class="form-group shiny-input-container">
    <label class="control-label" id="${key}-${id}-label" for="${key}-${id}">${label}</label>
<input id="${key}-${id}" type="number" class="shiny-input-number form-control" ${attrs} data-update-on="change">
  </div>`
}

export function snapNote(text: string, cls = ''): string {
  return `<p class="easi-snap-note${cls ? ' ' + cls : ''}">${esc(text)}</p>`
}

/** The Identify card: search, coordinates, reach length, the pick's status and Delineate. */
export function identifyPane(key: ToolKey, picked = false): string {
  const lookup = key === 'easi' ? '' : `
  <div class="shiny-html-output" id="${key}-streamcat_lookup_status" aria-live="polite"></div>`
  return `<div class="easi-pane-head">${TOOLS[key].name} · Identify</div>
<div class="easi-pane-body">
  ${stepper(key, 'identify')}
  <div class="easi-instr">Zoom in and click a stream, search a place, or enter coordinates.</div>
  <div class="form-group shiny-input-container">
    <label class="control-label" id="${key}-address-label" for="${key}-address">Address, place, or stream</label>
<input id="${key}-address" type="text" class="shiny-input-text form-control" value="" placeholder="${TOOLS[key].placeholder}" autocomplete="off" data-update-on="change">
  </div>
  <button class="btn btn-default action-button btn-outline-secondary btn-sm" id="${key}-find_address" type="button">
    <span class="action-label">Find on map</span>
  </button>
  <hr>
  ${numeric(key, 'lat', 'Latitude', 'min="24.0" max="50.0" step="0.0001"')}
  ${numeric(key, 'lon', 'Longitude', 'min="-125.0" max="-66.0" step="0.0001"')}
  ${numeric(key, 'reach_ft', 'Assessment reach (ft)', 'value="1000" min="100" max="5280" step="100"')}
  <div class="shiny-html-output" id="${key}-snap_status" aria-live="polite">
${snapNote('No point yet.')}
</div>${lookup}
  <div class="easi-pane-actions">
    <button class="btn btn-default action-button btn-primary" id="${key}-delineate" type="button"${picked ? '' : ' disabled=""'} aria-live="polite">
      <span class="action-label">Delineate Basin and Reach</span>
    </button>
  </div>
  <div id="${key}-busy_text" class="shiny-text-output easi-busy-text" aria-live="polite"></div>
</div>`
}

// ------------------------------------------------------------------------------- over the map

/** The zoom readout under the left pane. */
export function readout(zoom: number, lat: number, lon: number): string {
  return `<div class="easi-readout">Zoom ${Math.trunc(zoom)}  ·  Lat ${pyFixed(lat, 4)}, Lon ${pyFixed(lon, 4)}</div>`
}

const MAGNIFIER = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false" fill="none" '
  + 'stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><circle cx="7" cy="7" r="4.6"></circle>'
  + '<path d="M10.4 10.4L14 14M7 5v4M5 7h4"></path></svg>'
/** What the cue says while streams are hidden (staf_workbook web.ZOOM_CUE_TEXT). */
export const ZOOM_CUE_TEXT = 'Zoom in and click a stream'

/** The cue over the map while it is too far out for streams; ``pulse`` answers a click out there. */
export function zoomCue(nudge: number, pulse: boolean): string {
  return `<div data-nudge="${nudge}" class="staf-zoom-cue${pulse ? ' is-nudged' : ''}">
  ${MAGNIFIER}
<span>${ZOOM_CUE_TEXT}</span>
</div>`
}

/** The spinner while stream lines load (each tool's flow_loading). */
export const flowLoading = '<div class="easi-flow-loading"><div class="easi-spinner"></div><span>Loading streams…</span></div>'

/** The map key (each tool's _legend_ui, Identify, before a pick): the title, a notice when the
 * stream lines did not load (``alert``), the stream rows, a note, then ``extra`` (DEEP's region key). */
export function legend(rows: { color: string, label: string }[], note?: string, extra = '', alert = ''): string {
  const body = rows.map((r) => `<div class="easi-legend-row">
    <span class="easi-legend-sw" style="--swatch-color:${r.color};"></span>
    <div>
      <div class="easi-legend-label">${esc(r.label)}</div>
    </div>
  </div>`).join('\n  ')
  return `<div class="easi-legend">
  <div class="easi-legend-title">Map legend</div>${alert}
  ${body}${note ? `\n  <div class="easi-legend-note">${esc(note)}</div>` : ''}${extra}
</div>`
}

// ---------------------------------------------------------------------------------------- sections

const CHEVRON_RIGHT = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false" fill="none" '
  + 'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"></path></svg>'
const PANEL_ICON = '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" focusable="false" '
  + 'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round">'
  + '<rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.75"></rect>'
  + '<path d="M9.75 2.75v10.5"></path><path d="M9.75 2.75h2.75a1.75 1.75 0 0 1 1.75 1.75v7a1.75 1.75 '
  + '0 0 1-1.75 1.75H9.75z" fill="currentColor" fill-opacity=".22" stroke="none"></path></svg>'

/** DEEP's assessment regions: the closed sidebar's button and the sidebar (coverage.js fills it). */
function deepRegions(): string {
  return `<div class="deep-cov">
              <button type="button" class="deep-cov-tab" title="Show the assessment regions" aria-controls="deep-cov-panel" aria-expanded="false">${PANEL_ICON}<span class="deep-cov-tab-label">Assessment regions</span><span class="deep-cov-tab-count"></span></button>
              <div data-flow-zoom="${FLOW_ZOOM}" aria-label="Assessment regions" id="deep-cov-panel" class="deep-cov-panel" role="complementary">
                <div class="easi-pane-head deep-cov-head">
                  <span class="deep-cov-title">Assessment regions</span>
<span class="deep-cov-count"></span>
<button type="button" class="deep-cov-close" title="Hide the assessment regions" aria-label="Hide the assessment regions">${CHEVRON_RIGHT}</button>
                </div>
                <div id="deep-cov-body" class="deep-cov-body"></div>
              </div>
            </div>`
}

/** The map's slot: the Shiny output and the ipyleaflet widget's box, which the map fills. */
function mapWrap(key: ToolKey): string {
  return `<div class="easi-map-wrap">
              <div id="${key}-map" class="shiny-ipywidget-output" style="height: 100%;">
                <div class="lm-Widget p-Widget jupyter-widgets leaflet-widgets" style="height: 100%;"></div>
              </div>
            </div>`
}

function out(key: ToolKey, id: string, inner = ''): string {
  return `<div class="shiny-html-output" id="${key}-${id}" aria-live="polite">${inner}</div>`
}

export function section(key: ToolKey): string {
  const leftpane = `<div class="easi-leftpane">
              ${out(key, 'leftpane', '\n' + identifyPane(key) + '\n')}
            </div>`
  const legendPanel = `<div class="easi-legend-panel">
              ${out(key, 'stream_legend')}
            </div>`
  const cue = `<div class="staf-zoom-cue-slot" role="status" aria-live="polite">
              ${out(key, 'zoom_cue', '\n' + zoomCue(0, false) + '\n')}
            </div>`
  const tail = [out(key, 'readout', '\n' + readout(START.zoom, START.lat, START.lon) + '\n'), out(key, 'flow_loading'),
    cue, out(key, 'cursor_style')]
  // each tool's _tool_body order
  const parts = key === 'easi'
    ? [mapWrap(key), leftpane, out(key, 'worksheet'), out(key, 'batch_workspace'), legendPanel, ...tail]
    : key === 'sfari'
      ? [mapWrap(key), out(key, 'worksheet'), leftpane, legendPanel, ...tail]
      : [mapWrap(key), deepRegions(), legendPanel, out(key, 'worksheet'), leftpane, ...tail]
  return `<div data-staf-tool="${key}" class="staf-section">
          <div data-staf-tool="${key}" data-staf-ns="${key}" class="easi-shell">
            ${parts.join('\n            ')}
          </div>
        </div>`
}

/** The page's body: the header and every tool's section (only the shown one displays). */
export function page(): string {
  return [header('easi'), switchScript, ...ORDER.map(section)].join('\n')
}

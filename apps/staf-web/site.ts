// The STAF page as the website serves it: its stylesheets and shared page scripts, in the Shiny STAF
// app's order, and the page's markup, written into index.html.
//
// Everything the page serves as is lives in page/ (Vite's public folder: served at the app's root in
// development, copied into docs/app/ as is): the stylesheets and page scripts the static app keeps of
// the Shiny STAF app, the framework's stylesheets and fonts, Leaflet's stylesheet. The static app owns
// those copies, so it keeps working once the Shiny apps are retired; parity/copy_page.py made them and
// page-sources.json says where each came from.
//
// Each tool's own stylesheets apply only while that tool is shown, as in the Shiny app
// (apps/staf/staf_shell/head.py): every stylesheet starts on, so all of them load before the first
// paint, and the head's inline script (which waits for them) turns the other tools' off. The shared
// page scripts call window.Shiny, which the static app stands in for (src/app/shim.ts). Each link
// carries a ?v= content hash, as the Shiny app's assets do.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import { ORDER, page, TOOLS, type ToolKey } from './src/app/markup'

/** The page's own files (Vite's public folder). */
export const PAGE = fileURLToPath(new URL('./page', import.meta.url))

interface Item { path: string, tool?: ToolKey }

/** The page's stylesheets, in the Shiny page's order (its busy-indicator styles left out; the
 * Jupyter widget rules and Leaflet's, which the Shiny page gets from the map widget, after its own). */
export const STYLES: Item[] = [
  { path: 'css/shiny/bootstrap.min.css' },
  { path: 'css/easi/styles.css', tool: 'easi' },
  { path: 'css/sfari/styles.css', tool: 'sfari' },
  { path: 'css/deep/styles.css', tool: 'deep' },
  { path: 'css/deep/deep.css', tool: 'deep' },
  { path: 'css/staf/staf.css' },
  { path: 'css/staf/metric-rows.css' },
  { path: 'css/shell/staf-shell.css' },
  { path: 'css/shiny/shinywidgets.css' },
  { path: 'css/shiny/fill.css' },
  { path: 'css/shiny/jupyter.css' },
  { path: 'css/leaflet/leaflet.css' },
  { path: 'css/shiny/jupyter-leaflet.css' },
]

/** The shared page scripts the static app runs, in the Shiny page's order (staf-ns.js first). */
export const SCRIPTS: Item[] = [
  { path: 'js/staf/staf-ns.js' },
  { path: 'js/staf/geocode-autocomplete.js' },
  { path: 'js/staf/legend-dock.js' },
  { path: 'js/staf/tooltip.js' },
  { path: 'js/staf/coord-entry.js' },
  { path: 'js/deep/coverage.js' },
]

function url(item: Item): string {
  const hash = createHash('sha256').update(readFileSync(resolve(PAGE, item.path))).digest('hex').slice(0, 10)
  return `${item.path}?v=${hash}`
}

/** Picks the shown tool before the first paint: html[data-staf-tool], the title and the tools'
 * stylesheets (the Shiny app does this on the server from the request; here the address is read). */
const TOOL_SCRIPT = `<script>(function () {
  var q = new URLSearchParams(location.search), tool = (q.get("tool") || "").trim().toLowerCase();
  if (${JSON.stringify(ORDER)}.indexOf(tool) < 0) tool = q.get("assessment") ? "deep" : "easi";
  document.documentElement.setAttribute("data-staf-tool", tool);
  document.querySelectorAll("link[data-staf-css]").forEach(function (link) {
    link.media = link.getAttribute("data-staf-css") === tool ? "all" : "not all";
  });
  document.title = ${JSON.stringify(Object.fromEntries(ORDER.map((k) => [k, TOOLS[k].name])))}[tool] + " · STAF";
})();</script>`

export function stafSite(): Plugin {
  return {
    name: 'staf-site',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        if (!ctx.filename.endsWith('index.html')) return html
        const links = STYLES.map((item) => item.tool
          ? `<link rel="stylesheet" href="${url(item)}" media="all" data-staf-css="${item.tool}">`
          : `<link rel="stylesheet" href="${url(item)}">`)
        const scripts = SCRIPTS.map((item) => `<script src="${url(item)}" defer></script>`)
        return html
          .replace('<!--staf:head-->', [...links, TOOL_SCRIPT, ...scripts].join('\n    '))
          .replace('<!--staf:body-->', page())
      },
    },
  }
}

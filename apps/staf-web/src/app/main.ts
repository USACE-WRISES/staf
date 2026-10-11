// The STAF static web app's page: EASI, SFARI and DEEP under one header, as the Shiny STAF app.
//
// The page's markup and stylesheets are already in index.html (site.ts); this starts the switch,
// makes the shown tool's map, answers the shared page scripts (shim.ts) and the header's actions.
// Leaflet is window.L as on the Shiny page, where the scripts find it.
import L from 'leaflet'
import { dataBase } from '../config'
import { DataSource } from '../data/source'
import { Network } from '../streams/network'
import { showInfo } from './dialogs'
import { ORDER, type ToolKey } from './markup'
import { shownTool, startShell } from './shell'
import * as shim from './shim'
import { Tool } from './tool'

shim.install()
;(window as unknown as { L: typeof L }).L = L

const src = new DataSource(dataBase(new URLSearchParams(location.search)))
void src.ready().catch(() => {})   // every read needs the data manifest: ask for it at once

// the bundle's two stream networks, read by range and shared by the three tools
const nets = { hr: new Network(src, 'bundle/'), v2: new Network(src, 'bundle/v2/') }
const tools = Object.fromEntries(ORDER.map((key) => [key, new Tool(key, nets)])) as Record<ToolKey, Tool>
tools.deep.regionSource = async () => {
  await src.ready()
  const buf = await src.bytesOf('library/deep-regions.json')
  return (JSON.parse(new TextDecoder().decode(buf)) as { features: unknown[] }).features
}

shim.onInput((id, value) => {
  const key = ORDER.find((k) => id.startsWith(`${k}-`))
  if (key) tools[key].input(id.slice(key.length + 1), value)
})

// the header's actions (staf_workbook web.nav_actions); New, Open and Save come with the workflow
document.addEventListener('click', (e) => {
  const link = e.target instanceof Element ? e.target.closest('a[id]') : null
  const m = link && /^(easi|sfari|deep)-nav_(about|help)$/.exec(link.id)
  if (!m) return
  e.preventDefault()
  showInfo(m[1] as ToolKey, m[2] as 'about' | 'help')
})

startShell((tool) => tools[tool].show())
tools[shownTool()].show()
// every deferred page script has run by now: they hear the session start
document.addEventListener('DOMContentLoaded', () => shim.connect())

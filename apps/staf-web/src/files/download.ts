// The downloads: each Shiny download link (``@render.download``) the tools draw, answered in the page.
//
// The Shiny tools render a download link with ``target="_blank"`` and no ``download`` attribute
// (staf_workbook web.download_button): its file comes from the server in a tab of its own, so a
// managed network that takes the file over never replaces the app's page. Menlo Security's isolated
// browsing on the USACE network (CBII) shows an Office file or a PDF in place of the tab it came to:
// saved from the app's own tab, the workbook's preview replaced the page and its work (2026-10-12).
//
// Here every file is made in the page, and a click still opens the link's own tab: the link follows to
// ``download/<token>/<name>``, which the page's download worker (page/download-sw.js, scoped to
// download/) answers with the file it asks this page for, sent as an attachment, as the Shiny server
// sends it. The browser saves it and closes the tab, as it does for the Shiny links. Where there is no
// worker (none registered yet, or none allowed), the click opens a blank tab and the file is saved from
// there once made. The links answer through one listener: a tool registers each id with what its
// handler writes (registerDownload); a disabled link does nothing, as Shiny's does until its server has
// registered it.
//
// With ``?capture=1`` each file made is also kept in ``window.stafDownloads`` (its id, name, type and
// bytes), so parity/capture.py can compare it with the Shiny tool's; the file is then saved from the
// page itself.
import { notify } from '../app/notify'
import { base64, utf8 } from './bytes'

export type FileData = Uint8Array | string

export interface Download {
  /** the file's name (Shiny's ``filename=``, a value or a function read at the click) */
  filename: string | (() => string)
  /** the media type (``media_type=``) */
  mime: string
  /** the file, or null when the handler yields nothing (no file is saved) */
  build: () => FileData | null | Promise<FileData | null>
}

const registry = new Map<string, Download>()
const building = new Set<string>()

/** A download link's handler (``@render.download`` named ``id``, the tool's prefix included). */
export function registerDownload(id: string, d: Download): void {
  registry.set(id, d)
}

export function hasDownload(id: string): boolean {
  return registry.has(id)
}

const pageFiles = new Map<string, Promise<Uint8Array>>()
/** A file the page serves as it is (page/: the field forms, a blank calculator), read once and kept. */
export function pageFile(url: string): Promise<Uint8Array> {
  let p = pageFiles.get(url)
  if (!p) {
    p = fetch(url).then((r) => {
      if (!r.ok) throw new Error(`${url} answered ${r.status}`)
      return r.arrayBuffer()
    }).then((b) => new Uint8Array(b))
    p.catch(() => pageFiles.delete(url))
    pageFiles.set(url, p)
  }
  return p
}

interface Captured { id: string, filename: string, mime: string, size: number, base64: string }
const capture = typeof location !== 'undefined' && new URLSearchParams(location.search).get('capture') === '1'
const captured: Captured[] = []
if (capture) (window as unknown as { stafDownloads: () => Captured[] }).stafDownloads = () => captured

/** A file made for a click: its name, type and bytes (null when the handler yields nothing). */
interface Made { name: string, mime: string, bytes: Uint8Array | null }

const errorText = (err: unknown): string => (err instanceof Error ? err.message : String(err))

/** Saves ``data`` as a file named ``filename`` (a ``blob:`` address and ``<a download>``), from ``tab``
 * (a blank tab this page opened) or else from the page itself. */
export function saveFile(filename: string, data: FileData, mime = 'application/octet-stream', tab: Window | null = null): void {
  const bytes = typeof data === 'string' ? utf8(data) : data
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: mime }))
  const doc = tab ? tab.document : document
  const a = doc.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  doc.body.appendChild(a)
  a.click()
  a.remove()
  // the browser has the file once the click is handled; the address is freed after
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** A registered link's file, made now (a second click while it is made does nothing); a failure is told
 * in the page. */
function make(id: string, d: Download, name: string): Promise<Made> {
  building.add(id)
  const made = (async (): Promise<Made> => {
    const data = await d.build()
    const bytes = data === null || data === undefined ? null : typeof data === 'string' ? utf8(data) : data
    if (bytes && capture) captured.push({ id, filename: name, mime: d.mime, size: bytes.length, base64: base64(bytes) })
    return { name, mime: d.mime, bytes }
  })()
  made.catch((err) => notify(`The file could not be made: ${errorText(err)}`, { type: 'error', seconds: 8 }))
    .finally(() => building.delete(id))
  return made
}

// --- the download worker (page/download-sw.js), which answers the links' tabs

/** the files being made for download tabs, by token, until the worker asks for them */
const jobs = new Map<string, Promise<Made>>()
let worker: ServiceWorkerRegistration | null = null

if (!capture && typeof window !== 'undefined' && typeof navigator !== 'undefined' && 'serviceWorker' in navigator) {
  const container = navigator.serviceWorker
  container.addEventListener('message', (e: MessageEvent) => {
    const port = e.ports[0]
    if (!port || !e.data || e.data.type !== 'staf-download') return
    const job = jobs.get(e.data.token)
    if (!job) {
      port.postMessage({ has: false })
      return
    }
    jobs.delete(e.data.token)
    job.then((f) => port.postMessage(f), (err) => port.postMessage({ error: errorText(err) }))
  })
  container.startMessages()
  const register = (): void => {
    container.register('download-sw.js', { scope: './download/' }).then((r) => { worker = r }, () => {
      // no worker here: a download opens a blank tab and is saved from it
    })
  }
  if (document.readyState === 'complete') register()
  else window.addEventListener('load', register, { once: true })
}

function token(): string {
  const b = crypto.getRandomValues(new Uint8Array(12))
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
}

/** A blank tab for a download, opened during the click (null when the browser refuses it). */
function blankTab(name: string): Window | null {
  let tab: Window | null = null
  try {
    tab = window.open('', '_blank')
  } catch {
    return null
  }
  if (tab) {
    try {
      tab.document.title = name
      tab.document.body.style.cssText = 'font: 15px system-ui, sans-serif; margin: 2rem;'
      tab.document.body.textContent = `Preparing ${name}...`
    } catch {
      return null
    }
  }
  return tab
}

/** What a blank download tab says. */
function tell(tab: Window, text: string): void {
  try {
    const p = tab.document.createElement('p')
    p.textContent = text
    tab.document.body.replaceChildren(p)
  } catch {
    // the tab has moved on (a managed network's preview of the file)
  }
}

// (a page only: the writers are also imported where there is none, in the tests)
if (typeof document !== 'undefined') document.addEventListener('click', (e) => {
  const a = e.target instanceof Element ? e.target.closest('a.shiny-download-link') : null
  if (!(a instanceof HTMLAnchorElement)) return
  const d = registry.get(a.id)
  if (a.classList.contains('disabled') || a.getAttribute('aria-disabled') === 'true' || !d || building.has(a.id)) {
    // the link's own address is the page itself: never follow it
    e.preventDefault()
    if (!d && !a.classList.contains('disabled')) notify('This download is not in the web app yet.', { seconds: 4 })
    return
  }
  const name = typeof d.filename === 'function' ? d.filename() : d.filename
  const made = make(a.id, d, name)
  if (!capture && worker?.active) {
    // the link follows to the file's address in its own tab (target="_blank"); the worker answers it
    const key = token()
    jobs.set(key, made)
    setTimeout(() => jobs.delete(key), 600_000)
    a.target = '_blank'
    a.href = `download/${key}/${encodeURIComponent(name)}`
    setTimeout(() => a.setAttribute('href', ''), 0)
    return
  }
  e.preventDefault()
  const tab = capture ? null : blankTab(name)
  made.then((f) => {
    if (tab && tab.closed) return                     // the tab was closed: the download is called off
    if (!f.bytes) {
      tab?.close()
      return
    }
    saveFile(f.name, f.bytes, f.mime, tab)
    if (tab) tell(tab, `${f.name} is downloading. You can close this tab.`)
  }, (err) => {
    if (tab && !tab.closed) tell(tab, `The file could not be made: ${errorText(err)}. You can close this tab.`)
  })
})

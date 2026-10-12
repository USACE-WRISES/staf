// The downloads: each Shiny download link (``@render.download``) the tools draw, answered in the page.
//
// The Shiny tools render a download link with ``target="_blank"`` and no ``download`` attribute
// (staf_workbook web.download_button): its file comes from the server in a tab of its own, so a
// managed network that redirects the request never replaces the app's page. Here every file is built
// in the page and saved from a ``blob:`` address, which makes no network request at all: the link
// keeps the Shiny markup and classes, and a click saves the file under its name. The links answer
// through one listener: a tool registers each id with what its handler writes (registerDownload); a
// disabled link does nothing, as Shiny's does until its server has registered it.
//
// With ``?capture=1`` each file saved is also kept in ``window.stafDownloads`` (its id, name, type and
// bytes), so parity/capture.py can compare it with the Shiny tool's.
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

/** Saves ``data`` as a file named ``filename`` (a ``blob:`` address and ``<a download>``). */
export function saveFile(filename: string, data: FileData, mime = 'application/octet-stream', id = ''): void {
  const bytes = typeof data === 'string' ? utf8(data) : data
  if (capture) captured.push({ id, filename, mime, size: bytes.length, base64: base64(bytes) })
  const url = URL.createObjectURL(new Blob([bytes as BlobPart], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  a.remove()
  // the browser has the file once the click is handled; the address is freed after
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** A click on a registered link: its file is built and saved (a second click while it builds waits). */
async function run(id: string, d: Download): Promise<void> {
  if (building.has(id)) return
  building.add(id)
  try {
    const name = typeof d.filename === 'function' ? d.filename() : d.filename
    const data = await d.build()
    if (data !== null && data !== undefined) saveFile(name, data, d.mime, id)
  } catch (err) {
    notify(`The file could not be made: ${err instanceof Error ? err.message : String(err)}`, { type: 'error', seconds: 8 })
  } finally {
    building.delete(id)
  }
}

// (a page only: the writers are also imported where there is none, in the tests)
if (typeof document !== 'undefined') document.addEventListener('click', (e) => {
  const a = e.target instanceof Element ? e.target.closest('a.shiny-download-link') : null
  if (!(a instanceof HTMLAnchorElement)) return
  // the link's own address is the page itself: never follow it
  e.preventDefault()
  if (a.classList.contains('disabled') || a.getAttribute('aria-disabled') === 'true') return
  const d = registry.get(a.id)
  if (d) void run(a.id, d)
  else notify('This download is not in the web app yet.', { seconds: 4 })
})

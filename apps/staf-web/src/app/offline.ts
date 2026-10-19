// The app's offline copy (docs/app/app-sw.js, which scripts/after-build.mjs writes from sw/app-sw.js):
// registered on the built site only, never by the dev server. A page the copy opened asks for a newer
// build at once, and a newer build takes over as soon as its copy is in (owner, 2026-10-19): a page with
// nothing unsaved reloads into it straight away, and a page with unsaved work keeps running and says so
// with Reload to update (the unsaved-work guard asks first). A page open for hours looks for one hourly.
import { notify } from './notify'

const UPDATE_ID = 'staf-update'
const HOURLY = 60 * 60 * 1000
/** the first install waits until the page's first reads have had the network to themselves */
const START_AFTER_MS = 5000

export function registerOffline(params: URLSearchParams): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  // `?cache=0` measures the app without what earlier visits kept; `?sw=0` leaves the worker out too
  if (params.get('cache') === '0' || params.get('sw') === '0') return
  const sw = navigator.serviceWorker
  let current = sw.controller
  let reloading = false
  sw.addEventListener('controllerchange', () => {
    // the first install takes the page over too: only a running build being replaced counts
    const replaced = current
    current = sw.controller
    if (!replaced || reloading) return
    if (unsavedWork()) {
      offer()
      return
    }
    reloading = true
    location.reload()
  })
  // a page the copy opened: a newer build is looked for now, so it can take over before work starts
  if (sw.controller) void sw.getRegistration().then((reg) => reg?.update()).catch(() => {})
  const start = () => {
    sw.register('app-sw.js', { scope: './', updateViaCache: 'none' }).then((reg) => {
      setInterval(() => void reg.update().catch(() => {}), HOURLY)
    }, () => {
      // no worker here (a private window, a blocked origin): the app runs online only
    })
  }
  if (document.readyState === 'complete') setTimeout(start, START_AFTER_MS)
  else window.addEventListener('load', () => setTimeout(start, START_AFTER_MS), { once: true })
}

/** Whether leaving now would lose work: the unsaved-work guard (page/js/staf/unsaved-guard.js) cancels a
 * page's beforeunload while any tool's work differs from its last Save or Open. */
function unsavedWork(): boolean {
  const probe = new Event('beforeunload', { cancelable: true })
  window.dispatchEvent(probe)
  return probe.defaultPrevented
}

/** "A new version of STAF is ready.", with Reload: the page reloads into it (the guard asks first). */
function offer(): void {
  notify('A new version of STAF is ready.', { id: UPDATE_ID, type: 'message', seconds: 0 })
  const slot = document.querySelector(`#shiny-notification-${UPDATE_ID} .shiny-notification-content-action`)
  if (!slot) return
  slot.innerHTML = '<a href="#">Reload to update</a>'
  slot.querySelector('a')?.addEventListener('click', (e) => {
    e.preventDefault()
    location.reload()
  })
}

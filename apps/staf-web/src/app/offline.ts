// The app's offline copy (docs/app/app-sw.js, which scripts/after-build.mjs writes from sw/app-sw.js):
// registered on the built site only, never by the dev server. A build pushed since the page opened is
// offered with a Reload notice once its worker has installed, and every STAF page reloads when the new
// build takes over (the unsaved-work guard asks first). A page open for hours looks for one hourly.
import { notify } from './notify'

const UPDATE_ID = 'staf-update'
const HOURLY = 60 * 60 * 1000
/** after the page's first reads have had the network to themselves */
const START_AFTER_MS = 5000

export function registerOffline(params: URLSearchParams): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return
  // `?cache=0` measures the app without what earlier visits kept; `?sw=0` leaves the worker out too
  if (params.get('cache') === '0' || params.get('sw') === '0') return
  const sw = navigator.serviceWorker
  let current = sw.controller
  let reloading = false
  sw.addEventListener('controllerchange', () => {
    // the first install takes the page over too: a reload only when a running build was replaced
    const replaced = current
    current = sw.controller
    if (!replaced || reloading) return
    reloading = true
    location.reload()
  })
  const start = () => {
    sw.register('app-sw.js', { scope: './', updateViaCache: 'none' }).then((reg) => {
      if (reg.waiting && sw.controller) offer(reg.waiting)
      reg.addEventListener('updatefound', () => {
        const next = reg.installing
        next?.addEventListener('statechange', () => {
          if (next.state === 'installed' && sw.controller) offer(next)
        })
      })
      setInterval(() => void reg.update().catch(() => {}), HOURLY)
    }, () => {
      // no worker here (a private window, a blocked origin): the app runs online only
    })
  }
  if (document.readyState === 'complete') setTimeout(start, START_AFTER_MS)
  else window.addEventListener('load', () => setTimeout(start, START_AFTER_MS), { once: true })
}

/** "A new version of STAF is ready", with Reload: the waiting build takes over. */
function offer(worker: ServiceWorker): void {
  notify('A new version of STAF is ready.', { id: UPDATE_ID, type: 'message', seconds: 0 })
  const slot = document.querySelector(`#shiny-notification-${UPDATE_ID} .shiny-notification-content-action`)
  if (!slot) return
  slot.innerHTML = '<a href="#">Reload to update</a>'
  slot.querySelector('a')?.addEventListener('click', (e) => {
    e.preventDefault()
    worker.postMessage({ type: 'staf-app-update' })
  })
}

// The STAF web app's offline copy and its updates (src/app/offline.ts registers it; its scope is the
// app's folder, beside the download worker's download/).
//
// scripts/after-build.mjs writes this file into docs/app with the build's id and its files, so every
// pushed build is a new worker. The browser looks for one each time the app opens online (the page asks
// for this file past the HTTP cache), and a new build takes over as soon as its copy is in (owner,
// 2026-10-19): a page with nothing unsaved reloads into it at once, and a page with unsaved work keeps
// running and offers Reload (src/app/offline.ts). Such a page still reads its own files from the build
// before, kept until the next build takes over. The page and the app's own files come from the running
// build's copy, so the app opens without the network, and the fonts the page's stylesheets ask Google
// for are kept as first read. Everything else (the data host, the basemaps, place search, 3DEP) goes to
// the network as it would without a worker: the page itself keeps the data it has read
// (src/data/cache.ts).
'use strict'

const BUILD = "3a81a2e02f8635ae"
const FILES = ["assets/chunk--Go39HHy.js","assets/chunk-4HVfDzLp.js","assets/chunk-B3hA14pn.js","assets/chunk-B5hS9wZo.js","assets/chunk-B61JLJ-t.js","assets/chunk-B8MpGCA_.js","assets/chunk-BeeL7-hC.js","assets/chunk-Bg2Ysv15.js","assets/chunk-BhTQX914.js","assets/chunk-BuG7fe0q.js","assets/chunk-BwP1DIml.js","assets/chunk-C-4BCd0I.js","assets/chunk-CCai1sSQ.js","assets/chunk-CKOcl3Kf.js","assets/chunk-CLnMMoef.js","assets/chunk-CPNpSpNI.js","assets/chunk-CQ12zZ_Q.js","assets/chunk-CQiFGC7b.js","assets/chunk-CUCmuC61.js","assets/chunk-CUPJ0lUF.js","assets/chunk-CWKuH4O5.js","assets/chunk-CaaO7G-u.js","assets/chunk-CbdFy9Re.js","assets/chunk-CcOwfO-U.js","assets/chunk-CiWSDZhD.js","assets/chunk-Cjwo-3KE.js","assets/chunk-Cm8qisJz.js","assets/chunk-CmdrVVic.js","assets/chunk-Ct07wkCB.js","assets/chunk-D4BmvaYI.js","assets/chunk-D4YpKtpq.js","assets/chunk-D7LtYXE3.js","assets/chunk-DAgkTwkl.js","assets/chunk-DTaeYbWr.js","assets/chunk-Ddyou4eR.js","assets/chunk-DfRT4Thk.js","assets/chunk-DhPmDCcZ.js","assets/chunk-Dj4rC45y.js","assets/chunk-DkRn1D-M.js","assets/chunk-DtofApKv.js","assets/chunk-DxLeA411.js","assets/chunk-KO-1WKtx.js","assets/chunk-N1fIi8pF.js","assets/chunk-TirHqoiO.js","assets/chunk-UprahKSt.js","assets/chunk-cKtu3inb.js","assets/chunk-t4yKptG8.js","assets/index-CbuaajfS.js","assets/proof-BBQtHa3a.js","css/deep/deep.css","css/deep/styles.css","css/easi/styles.css","css/leaflet/images/layers-2x.png","css/leaflet/images/layers.png","css/leaflet/images/marker-icon-2x.png","css/leaflet/images/marker-icon.png","css/leaflet/images/marker-shadow.png","css/leaflet/leaflet.css","css/sfari/styles.css","css/shell/staf-shell.css","css/shiny/bootstrap.min.css","css/shiny/fill.css","css/shiny/font.css","css/shiny/fonts/HI_QiYsKILxRpg3hIP6sJ7fM7PqlONvUlMI.woff2","css/shiny/fonts/HI_QiYsKILxRpg3hIP6sJ7fM7PqlONvalMIXxw.woff2","css/shiny/fonts/HI_SiYsKILxRpg3hIP6sJ7fM7PqlM-vWjMY.woff2","css/shiny/fonts/HI_SiYsKILxRpg3hIP6sJ7fM7PqlPevW.woff2","css/shiny/fonts/memtYaGs126MiZpBA-UFUIcVXSCEkx2cmqvXlWqWt06F15M.woff2","css/shiny/fonts/memtYaGs126MiZpBA-UFUIcVXSCEkx2cmqvXlWqWuU6F.woff2","css/shiny/fonts/memvYaGs126MiZpBA-UvWbX2vVnXBbObj2OVTS-muw.woff2","css/shiny/fonts/memvYaGs126MiZpBA-UvWbX2vVnXBbObj2OVTSGmu1aB.woff2","css/shiny/jupyter-leaflet.css","css/shiny/jupyter.css","css/shiny/shinywidgets.css","css/staf/metric-rows.css","css/staf/staf.css","favicon.svg","fonts/dejavu-sans-bold.woff2","fonts/dejavu-sans-license.txt","fonts/dejavu-sans-summary.woff2","fonts/dejavu-sans.woff2","forms/easi/2026-10-23_EASI Calculator.xlsx","forms/easi/EASI_Calculator_Template_2026-10-23.xlsx","forms/sfari/2026-10-19_SFARI Calculator.xlsx","forms/sfari/SFARI_Calculator_Template_2026-10-19.xlsx","forms/sfari/SFARI_Field_Form_v1.0.pdf","icons/staf-192.png","icons/staf-512.png","icons/staf-maskable-512.png","index.html","js/deep/coverage.js","js/deep/measure.js","js/easi/report-controls.js","js/easi/report-edit.js","js/easi/worksheet.js","js/sfari/field-review.js","js/staf/coord-entry.js","js/staf/geocode-autocomplete.js","js/staf/legend-dock.js","js/staf/metric-rows.js","js/staf/report-ready.js","js/staf/scenarios.js","js/staf/staf-ns.js","js/staf/tooltip.js","js/staf/unsaved-guard.js","manifest.webmanifest"]
const CACHE = `staf-app-${BUILD}`
const FONTS = 'staf-fonts-1'
const FONT_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com']
/** which build's copy was running, so the next build keeps it for the pages still on it */
const META = 'staf-meta-1'

const here = (path) => new URL(path, self.registration.scope).href

self.addEventListener('install', (event) => {
  // every file of the build, fresh from the site (a file whose name carries no hash may sit in the HTTP
  // cache from the build before); the content-hashed assets may come from the HTTP cache. Then the build
  // takes over without waiting for every STAF window to close.
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(FILES.map((f) =>
    new Request(here(f), { cache: f.startsWith('assets/') ? 'default' : 'reload' })))).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // the copy of the build being replaced stays for the pages still running it (unsaved work); any
    // older one goes. Without the record (the first build that keeps one) every older copy stays once.
    const meta = await caches.open(META)
    const kept = await meta.match('running')
    const previous = kept ? await kept.text() : null
    for (const name of await caches.keys()) {
      if (name.startsWith('staf-app-') && name !== CACHE && previous !== null && name !== previous) await caches.delete(name)
    }
    if (previous !== CACHE) await meta.put('running', new Response(CACHE))
    await self.clients.claim()
  })())
})

// Reload in a page from a build before 2026-10-19: the build waiting takes over (each STAF page reloads
// when it does)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'staf-app-update') self.skipWaiting()
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (FONT_HOSTS.includes(url.hostname)) {
    event.respondWith(font(req))
    return
  }
  const scope = self.registration.scope
  if (!req.url.startsWith(scope) || req.url.startsWith(here('download/'))) return
  if (req.mode === 'navigate') {
    // the app's page (any ?tool=); another page of the folder (proof.html) is the site's
    if (url.pathname === new URL(scope).pathname || url.pathname === new URL(here('index.html')).pathname) event.respondWith(page(req))
    return
  }
  event.respondWith(own(req))
})

// The copies are matched past their Vary header: a server may say Vary: Origin (vite preview does), and a
// module script's request carries an Origin header the copy's request had not, while the app's files
// are the same for every request.

/** The app's page: the running build's, else the site's. */
async function page(req) {
  return (await caches.match(here('index.html'), { cacheName: CACHE, ignoreVary: true })) || fetch(req)
}

/** A file of the app: the running build's copy (its ?v= aside: a build holds one version of each), else
 * the copy of the build before (a page still running it asks for its own files), else the site's. */
async function own(req) {
  const opts = { ignoreSearch: true, ignoreVary: true }
  const mine = await caches.match(req, { ...opts, cacheName: CACHE })
  if (mine) return mine
  for (const name of await caches.keys()) {
    if (!name.startsWith('staf-app-') || name === CACHE) continue
    const before = await caches.match(req, { ...opts, cacheName: name })
    if (before) return before
  }
  return fetch(req)
}

/** A font host's answer, kept as first read (the stylesheet comes opaque: kept as it is). */
async function font(req) {
  const cache = await caches.open(FONTS)
  const kept = await cache.match(req, { ignoreVary: true })
  if (kept) return kept
  const res = await fetch(req)
  if (res.ok || res.type === 'opaque') cache.put(req, res.clone()).catch(() => {})
  return res
}

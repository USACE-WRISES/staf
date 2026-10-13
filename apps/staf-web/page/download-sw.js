// The STAF web app's downloads, each in a tab of its own (src/files/download.ts registers this worker).
//
// A Shiny tool answers a download link (target="_blank") from its server, in a new tab: the browser saves
// the file and closes that tab, and a managed network that shows the file instead (Menlo Security's
// isolated browsing shows an Office file or a PDF in place of the tab it came to) leaves the app's page
// alone. The web app makes its files in the page, so a click opens a new tab at
// download/<token>/<name>, and this worker answers it: it asks the app's pages for the file the token
// names and sends it as the Shiny server does, as an attachment. Its scope is download/ alone, so it
// sees no other request of the app.
'use strict'

/** how long a download tab waits for its file (a report with a map can take a while) */
const WAIT_MS = 180000

self.addEventListener('install', () => self.skipWaiting())

self.addEventListener('fetch', (event) => {
  const scope = self.registration.scope
  if (event.request.method !== 'GET' || !event.request.url.startsWith(scope)) return
  event.respondWith(answer(event.request.url.slice(scope.length).split('/')[0]))
})

/** The file the token names, from the app page that made it: {name, mime, bytes} or {error}; null when no
 * page has it (a download tab opened again later). */
async function ask(token) {
  const pages = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  return new Promise((resolve) => {
    let left = pages.length
    const timer = setTimeout(() => resolve(null), WAIT_MS)
    if (!left) {
      clearTimeout(timer)
      resolve(null)
    }
    for (const page of pages) {
      const channel = new MessageChannel()
      channel.port1.onmessage = (event) => {
        const reply = event.data || {}
        if (reply.has === false) {
          if (--left === 0) {
            clearTimeout(timer)
            resolve(null)
          }
          return
        }
        clearTimeout(timer)
        resolve(reply)
      }
      page.postMessage({ type: 'staf-download', token }, [channel.port2])
    }
  })
}

async function answer(token) {
  const reply = await ask(token)
  if (!reply) return note('This download is no longer here. Start it again in STAF.', 404)
  if (reply.error) return note(`The file could not be made: ${reply.error}`, 500)
  if (!reply.bytes) return note('There is no file to download.', 200)
  const name = reply.name
  return new Response(reply.bytes, {
    headers: {
      'Content-Type': reply.mime,
      'Content-Disposition': `attachment; filename="${name.replace(/[^\x20-\x7e]|["\\]/g, '_')}"; filename*=UTF-8''${encodeURIComponent(name)}`,
      'Cache-Control': 'no-store',
    },
  })
}

/** A short page in the download tab, when there is no file to send. */
function note(text, status) {
  const safe = text.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c])
  const html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>STAF download</title></head>' +
    `<body style="font: 15px system-ui, sans-serif; margin: 2rem;"><p>${safe}</p><p>You can close this tab.</p></body></html>`
  return new Response(html, { status, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } })
}

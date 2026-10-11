// Shiny's notifications (ui.notification_show), as shiny.js draws them: a panel at the page's bottom
// right holding one box per notification, with a close button, removed after its duration with a
// 250 ms fade.
import { esc } from './markup'

const FADE_MS = 250
const timers = new Map<string, ReturnType<typeof setTimeout>>()

function panel(): HTMLElement {
  let p = document.getElementById('shiny-notification-panel')
  if (!p) {
    p = document.createElement('div')
    p.id = 'shiny-notification-panel'
    document.body.appendChild(p)
  }
  return p
}

function remove(id: string): void {
  const el = document.getElementById(`shiny-notification-${id}`)
  if (!el) return
  el.style.transition = `opacity ${FADE_MS}ms`
  el.style.opacity = '0'
  setTimeout(() => {
    el.remove()
    const p = document.getElementById('shiny-notification-panel')
    if (p && !p.querySelector('.shiny-notification')) p.remove()
  }, FADE_MS)
}

/** ``ui.notification_show(text, type=..., duration=...)``: ``seconds`` 0 keeps it until closed. */
export function notify(text: string, opts: { type?: 'message' | 'warning' | 'error', seconds?: number, id?: string } = {}): string {
  const id = opts.id ?? Math.random().toString(16).slice(2, 18)
  let el = document.getElementById(`shiny-notification-${id}`)
  if (!el) {
    el = document.createElement('div')
    el.id = `shiny-notification-${id}`
    el.className = 'shiny-notification'
    el.innerHTML = '<div class="shiny-notification-close">&times;</div><div class="shiny-notification-content"></div>'
    el.querySelector('.shiny-notification-close')?.addEventListener('click', (e) => {
      e.preventDefault()
      e.stopPropagation()
      remove(id)
    })
    panel().appendChild(el)
  }
  el.querySelector('.shiny-notification-content')!.innerHTML =
    `<div class="shiny-notification-content-text">${esc(text)}</div><div class="shiny-notification-content-action"></div>`
  el.className = 'shiny-notification' + (opts.type ? ` shiny-notification-${opts.type}` : '')
  const old = timers.get(id)
  if (old) clearTimeout(old)
  const seconds = opts.seconds ?? 5
  if (seconds) timers.set(id, setTimeout(() => remove(id), seconds * 1000))
  return id
}

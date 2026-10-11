// The header's EASI | SFARI | DEEP switch and the Menu button (apps/staf/www/shell/staf-shell.js).
//
// A switch is instant and local: the page shows the chosen tool's section, header slots and lit
// tier (all from html[data-staf-tool]), turns on that tool's own stylesheets (their media), names
// the tool in the title and the address bar (?tool=, so it can be bookmarked) and tells the page
// (staf:tool-shown), which makes the tool's map the first time it is shown. Every tool keeps its
// work while another one is shown.
import { ORDER, TOOLS, type ToolKey } from './markup'

type Shown = (tool: ToolKey) => void

function button(tool: string): HTMLButtonElement | null {
  return document.querySelector(`.staf-tool-btn[data-tool="${tool}"]`)
}

function header(): HTMLElement | null {
  return document.querySelector('.staf-header')
}

function closeMenu(): void {
  header()?.classList.remove('menu-open')
  document.querySelector('.staf-menu-btn')?.setAttribute('aria-expanded', 'false')
}

export function shownTool(): ToolKey {
  return (document.documentElement.getAttribute('data-staf-tool') as ToolKey) || 'easi'
}

let onShown: Shown = () => {}

export function activate(tool: ToolKey): boolean {
  const btn = button(tool)
  const html = document.documentElement
  if (!btn || btn.disabled || html.getAttribute('data-staf-tool') === tool) return false
  html.setAttribute('data-staf-tool', tool)
  document.querySelectorAll<HTMLButtonElement>('.staf-tool-btn').forEach((b) => {
    const on = b === btn
    b.classList.toggle('active', on)
    b.setAttribute('aria-selected', on ? 'true' : 'false')
    b.tabIndex = on ? 0 : -1
  })
  document.querySelectorAll<HTMLLinkElement>('link[data-staf-css]').forEach((link) => {
    link.media = link.getAttribute('data-staf-css') === tool ? 'all' : 'not all'
  })
  document.title = `${TOOLS[tool].name} · STAF`
  try {
    history.replaceState(history.state, '', `?tool=${tool}${location.hash}`)
  } catch {
    // no history
  }
  closeMenu()
  onShown(tool)
  // Leaflet measures a map only while it shows: let every map re-measure on the next frame
  requestAnimationFrame(() => window.dispatchEvent(new Event('resize')))
  document.dispatchEvent(new CustomEvent('staf:tool-shown', { detail: { tool } }))
  return true
}

export function startShell(shown: Shown): void {
  onShown = shown
  const tabs = document.querySelector('.staf-tool-switch')
  tabs?.addEventListener('click', (e) => {
    const btn = (e.target as Element).closest('.staf-tool-btn')
    if (btn) activate(btn.getAttribute('data-tool') as ToolKey)
  })
  tabs?.addEventListener('keydown', (e) => {
    const ev = e as KeyboardEvent
    const btn = (ev.target as Element).closest('.staf-tool-btn')
    const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0
    if (!btn || !step) return
    ev.preventDefault()
    const at = ORDER.indexOf(btn.getAttribute('data-tool') as ToolKey)
    for (let n = 1; n < ORDER.length; n++) {
      const next = ORDER[(at + step * n + ORDER.length) % ORDER.length]
      if (activate(next)) {
        button(next)?.focus()
        return
      }
    }
  })
  document.addEventListener('click', (e) => {
    const target = e.target instanceof Element ? e.target : null
    const bar = header()
    if (!target || !bar) return
    const menu = target.closest('.staf-menu-btn')
    if (menu) {
      const open = bar.classList.toggle('menu-open')
      menu.setAttribute('aria-expanded', open ? 'true' : 'false')
      return
    }
    if (bar.classList.contains('menu-open') && !target.closest('.staf-slot.staf-nav')) closeMenu()
  })
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu()
  })
}

// Shiny's modal_show and modal_remove: a ui.modal (shinyui.ts) in Shiny's wrapper, shown with
// Bootstrap's Modal; a dialog shown over an open one replaces it, as Shiny's does.
import Modal from 'bootstrap/js/dist/modal'
import { outputHtml, type Tag, type TagList } from '../app/htm'

let current: { el: HTMLElement, modal: Modal } | null = null

/** ui.modal_show: the dialog in Shiny's wrapper, replacing any open one; returns its element. */
export function showModal(tag: Tag | TagList): HTMLElement {
  const wrapper = document.getElementById('shiny-modal-wrapper') ?? document.body.appendChild(Object.assign(document.createElement('div'), { id: 'shiny-modal-wrapper' }))
  if (current) {
    // a modal shown over an open one replaces it, as Shiny's does (no second backdrop)
    const old = current
    current = null
    old.modal.dispose()
    document.querySelectorAll('.modal-backdrop').forEach((b) => b.remove())
    document.body.classList.remove('modal-open')
    document.body.style.removeProperty('overflow')
    document.body.style.removeProperty('padding-right')
  }
  wrapper.innerHTML = outputHtml(tag)
  const el = wrapper.querySelector<HTMLElement>('#shiny-modal')!
  const m = new Modal(el)
  current = { el, modal: m }
  el.addEventListener('hidden.bs.modal', () => {
    if (current?.el === el) {
      current = null
      m.dispose()
      wrapper.innerHTML = ''
    }
  })
  m.show()
  return el
}

/** ui.modal_remove */
export function removeModal(): void {
  current?.modal.hide()
}

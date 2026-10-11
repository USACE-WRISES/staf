// The header's About and Help dialogs, as Shiny shows a modal (ui.modal: Bootstrap 5.3.8's).
//
// Their text is each tool's (written in its app.py, staf_workbook web.info_dialog); it is captured
// from the running Shiny app into content/dialogs.json by parity/capture.py --app shiny.
import Modal from 'bootstrap/js/dist/modal'
import dialogs from './content/dialogs.json'
import { esc, type ToolKey } from './markup'

type Which = 'about' | 'help'
const TEXT = dialogs as Record<ToolKey, Record<Which, { title: string, body: string } | null>>

/** A Shiny modal (``ui.modal(..., easy_close=True, footer=ui.modal_button("Close"))``). */
export function showModal(title: string, body: string): void {
  document.getElementById('shiny-modal-wrapper')?.remove()
  const wrapper = document.createElement('div')
  wrapper.id = 'shiny-modal-wrapper'
  wrapper.innerHTML = `<div id="shiny-modal" class="modal fade" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h4 class="modal-title">${esc(title)}</h4>
      </div>
      <div class="modal-body">${body}</div>
      <div class="modal-footer">
        <button class="btn btn-default" type="button" data-dismiss="modal" data-bs-dismiss="modal">Close</button>
      </div>
    </div>
  </div>
</div>`
  document.body.appendChild(wrapper)
  const el = wrapper.firstElementChild as HTMLElement
  el.addEventListener('hidden.bs.modal', () => wrapper.remove())
  new Modal(el).show()
}

/** About or Help for a tool. */
export function showInfo(tool: ToolKey, which: Which): void {
  const text = TEXT[tool]?.[which]
  if (text) showModal(text.title, text.body)
}

// The py-shiny controls the tools put in their dialogs, as py-shiny 1.x renders them (rendered by
// htm.ts), and ui.modal's markup (modal.ts shows it).
// Input ids carry the tool's prefix, as inside the STAF app's modules ("sfari-staf_sc_name").
import { type Child, h, HTML, type Tag, tag } from '../app/htm'

/** ui.input_text */
export function inputText(id: string, label: string, value = '', width?: string): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `${id}-label`, for: id }),
    h.input({ id, type: 'text', class: 'shiny-input-text form-control', value, autocomplete: 'off', 'data-update-on': 'change' }),
    { class: 'form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** ui.input_select (selectize off): ``choices`` as [value, label] in order. */
export function inputSelect(id: string, label: string, choices: [string, string][], selected: string, width?: string): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `${id}-label`, for: id }),
    h.div(h.select(...choices.map(([v, l]) => h.option(l, { value: v, selected: v === selected ? true : null })),
      { class: 'shiny-input-select form-select', id })),
    { class: 'form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** ui.input_text_area */
export function inputTextArea(id: string, label: string, value = '', { rows = null as number | null, width = null as string | null,
  placeholder = null as string | null } = {}): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `${id}-label`, for: id }),
    h.textarea(value, { id, class: 'form-control', placeholder, rows: rows === null ? null : String(rows), 'data-update-on': 'change' }),
    { class: 'shiny-input-textarea form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** ui.input_action_button */
export function actionButton(id: string, label: string, cls = ''): Tag {
  return h.button(h.span(label, { class: 'action-label' }),
    { class: `btn btn-default action-button${cls ? ' ' + cls : ''}`, id, type: 'button', _add_ws: true })
}

/** ui.modal_button */
export function modalButton(label: string, cls = ''): Tag {
  return h.button(label, { class: `btn btn-default${cls ? ' ' + cls : ''}`, type: 'button', 'data-dismiss': 'modal', 'data-bs-dismiss': 'modal' })
}

const SIZE: Record<string, string> = { s: ' modal-sm', l: ' modal-lg', xl: ' modal-xl' }
/** py-shiny's show script, kept in the markup as Shiny has it (inserted as HTML, it never runs:
 * showModal shows the dialog) */
const SHOW_JS = String.raw`if (window.bootstrap && !window.bootstrap.Modal.VERSION.match(/^4\. /)) {
  var modal=new bootstrap.Modal(document.getElementById('shiny-modal'))
  modal.show()
} else {
  $('#shiny-modal').modal().focus()
}`

/** ui.modal: the dialog's markup. ``bodyClass`` is the modal's keyword attributes, which py-shiny
 * puts on its body. */
export function modal(body: Child, { title = null as Child, footer = null as Child, size = 'm', easyClose = false,
  bodyClass = null as string | null } = {}): Tag {
  const titleDiv = title ? h.div(h.h4(title, { class: 'modal-title' }), { class: 'modal-header' }) : null
  const foot = footer === null ? null : h.div(footer, { class: 'modal-footer' })
  const dialog = h.div(h.div(titleDiv, h.div({ class: 'modal-body' }, body, bodyClass ? { class: bodyClass } : {}), foot, { class: 'modal-content' }),
    { class: 'modal-dialog' + (SIZE[size] ?? '') })
  return h.div(dialog, tag('script', new HTML(SHOW_JS)), {
    id: 'shiny-modal', class: 'modal fade', tabindex: '-1',
    'data-backdrop': easyClose ? null : 'static', 'data-bs-backdrop': easyClose ? null : 'static',
    'data-keyboard': easyClose ? null : 'false', 'data-bs-keyboard': easyClose ? null : 'false',
  })
}

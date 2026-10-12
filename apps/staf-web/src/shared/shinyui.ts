// The py-shiny controls the tools put in their dialogs, as py-shiny 1.x renders them (rendered by
// htm.ts), and ui.modal's markup (modal.ts shows it).
// Input ids carry the tool's prefix, as inside the STAF app's modules ("sfari-staf_sc_name").
import { type Child, h, HTML, outputHtml, type Tag, tag, TagList } from '../app/htm'
import { pyFloat } from '../app/pyfmt'

/** ui.input_text */
export function inputText(id: string, label: string, value = '', width?: string): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `${id}-label`, for: id }),
    h.input({ id, type: 'text', class: 'shiny-input-text form-control', value, autocomplete: 'off', 'data-update-on': 'change' }),
    { class: 'form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** ui.input_select (selectize off): ``choices`` as [value, label] in order; a null label is py-shiny's
 * empty label. */
export function inputSelect(id: string, label: string | null, choices: [string, string][], selected: string, width?: string): Tag {
  return h.div(
    h.label(label ?? '', { class: label === null ? 'control-label shiny-label-null' : 'control-label', id: `${id}-label`, for: id }),
    h.div(h.select(...choices.map(([v, l]) => h.option(l, { value: v, selected: v === selected ? true : null })),
      { class: 'shiny-input-select form-select', id })),
    { class: 'form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** A number's text as Python's str() writes it: pass a float that is whole as its text ("30.0"). */
function numText(v: number | string | null | undefined): string | null {
  if (v === null || v === undefined) return null
  if (typeof v === 'string') return v
  return Number.isInteger(v) ? String(v) : pyFloat(v)
}

/** ui.input_numeric */
export function inputNumeric(id: string, label: string, value: number | string | null, { min = null as number | string | null,
  max = null as number | string | null, step = null as number | string | null, width = null as string | null } = {}): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `${id}-label`, for: id }),
    h.input({ id, type: 'number', class: 'shiny-input-number form-control', value: numText(value), min: numText(min), max: numText(max),
      step: numText(step), 'data-update-on': 'change' }),
    { class: 'form-group shiny-input-container', style: width ? `width:${width};` : null })
}

/** ui.row */
export function row(...children: Child[]): Tag {
  return h.div(...children, { class: 'row' })
}

/** ui.column */
export function column(width: number, ...children: Child[]): Tag {
  return h.div(...children, { class: `col-sm-${width}` })
}

/** ui.output_ui as Shiny binds it (its content inside, polite to screen readers). */
export function outputUi(id: string, content: Child = null): Tag {
  return h.div({ class: 'shiny-html-output', id, 'aria-live': 'polite' }, new HTML(outputHtml(content)))
}

/** staf_web.download_button as Shiny shows it once the tool's server has registered it (the link's
 * own markup: download.ts answers its click). */
export function downloadButton(id: string, label: string, cls = '', title: string | null = null): Tag {
  return h.a(label, { class: `btn btn-default shiny-download-link${cls ? ' ' + cls : ''}`, id, href: '', target: '_blank', rel: 'noopener',
    title, 'aria-live': 'polite' })
}

/** staf_web.download_link, shown as registered. */
export function downloadLink(id: string, label: string, cls = '', title: string | null = null): Tag {
  return h.a(label, { class: `shiny-download-link${cls ? ' ' + cls : ''}`, id, href: '', target: '_blank', rel: 'noopener', title, 'aria-live': 'polite' })
}

export interface NavPanel { title: string, value?: string, content: Child }

/** ui.navset_pill(*nav_panels, ui.nav_spacer(), *nav_controls, id=...): the pills (Bootstrap's tab
 * plugin switches them) and their panes; ``controls`` follow the spacer, each in its own item. */
export function navsetPill(id: string, panels: NavPanel[], controls: Child[] = [], selected: string | null = null): TagList {
  const tabset = String(Math.floor(100000 + Math.random() * 900000))
  const value = (p: NavPanel) => p.value ?? p.title
  const active = selected ?? (panels.length ? value(panels[0]) : null)
  const items = panels.map((p, i) => h.li(h.a(p.title, { 'data-bs-toggle': 'tab', 'data-toggle': 'tab', 'data-value': value(p), role: 'tab',
    class: 'nav-link' + (value(p) === active ? ' active' : ''), href: `#tab-${tabset}-${i}` }), { class: 'nav-item' }))
  const ul = h.ul(...items, controls.length ? h.li({ class: 'bslib-nav-spacer' }) : null, ...controls.map((c) => h.li(c)),
    { class: 'nav nav-pills shiny-tab-input', id, 'data-tabsetid': tabset })
  const content = h.div(...panels.map((p, i) => h.div(p.content, { class: 'tab-pane' + (value(p) === active ? ' active' : ''), role: 'tabpanel',
    'data-value': value(p), id: `tab-${tabset}-${i}` })), { class: 'tab-content', 'data-tabsetid': tabset })
  return new TagList(ul, content)
}

/** The value of a navset's shown pill (its input in Shiny), or null. */
export function navsetValue(id: string): string | null {
  return document.querySelector(`#${CSS.escape(id)} .nav-link.active`)?.getAttribute('data-value') ?? null
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

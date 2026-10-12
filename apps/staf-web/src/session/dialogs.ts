// The header's New: its question while there is work to lose (staf_workbook web.new_dialog), the same
// in every tool; each tool names what it clears (CLEARS) and decides whether there is anything to lose.
import { h, HTML, type Tag, TagList } from '../app/htm'
import type { ToolKey } from '../app/markup'
import { actionButton, modal, modalButton } from '../shared/shinyui'

/** What each tool's New clears (each app.py _new_assessment / _new_analysis). */
export const CLEARS: Record<ToolKey, string> = {
  easi: 'the delineation, the screening and every rating, note and observation',
  sfari: 'the delineation and every score, note and photo',
  deep: 'the delineation, the chosen assessment and every measured value',
}

/** markdown's escape of plain text in a paragraph */
const md = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** new_dialog: "Start a new assessment?", Cancel and "Clear & start new" (``confirm_new``). */
export function newDialog(ns: string, clears: string): Tag {
  const text = `<p>Clear ${md(clears)} and start a new assessment? Use <strong>Save</strong> first if you want to keep it.</p>\n`
  return modal(new HTML(text), {
    title: 'Start a new assessment?', easyClose: true,
    footer: new TagList(modalButton('Cancel'), actionButton(`${ns}-confirm_new`, 'Clear & start new', 'btn-danger')),
  })
}

export { h }

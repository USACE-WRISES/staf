// The downloads every tool offers alike (libs/staf_workbook web.py, owner 2026-10-08): Get Forms' pills
// after the spacer (forms_downloads), the report's footer (report_footer), and the scenario's part of a
// report file's name (scenario_suffix). The links are drawn as Shiny shows them once registered;
// files/download.ts answers their clicks.
import { type Child, h, TagList } from '../app/htm'
import type { ScenarioSet } from './scenarios'
import { downloadButton, modalButton } from './shinyui'

export const FORMS_TITLE = 'Get Forms'
export const FIELD_FORMS_LABEL = 'Field forms PDF'
export const COMPLETED_LABEL = 'Completed workbook'
export const BLANK_LABEL = 'Blank workbook'
/** shown where the workbooks would be when the loaded method or version has no calculator */
export const NO_WORKBOOK_NOTE = 'No calculator workbook for this version'
const FIELD_FORMS_TITLE = 'The field forms to print'
const COMPLETED_TITLE = 'The Excel calculator with this assessment entered'
const BLANK_TITLE = 'The Excel calculator with empty entry cells'
/** the media type of every workbook download (web.XLSX_MEDIA_TYPE): a plain file, never a viewer */
export const XLSX_MEDIA_TYPE = 'application/octet-stream'

/** forms_downloads: the field forms (``fieldForms``: its download id), the metrics PDF (``metrics``: [id,
 * label] or [id, label, title]), then the completed and blank workbooks (``workbooks``: [completed id,
 * blank id]), or the note in their place. Ids carry the tool's prefix. */
export function formsDownloads(metrics: [string, string] | [string, string, string], { fieldForms = null as string | null,
  workbooks = null as [string, string] | null } = {}): Child[] {
  const item = (id: string, label: string, title: string | null) => h.div(downloadButton(id, label, 'btn-sm btn-primary', title), { class: 'staf-dl' })
  const out: Child[] = []
  if (fieldForms) out.push(item(fieldForms, FIELD_FORMS_LABEL, FIELD_FORMS_TITLE))
  out.push(item(metrics[0], metrics[1], metrics.length > 2 ? metrics[2]! : null))
  if (workbooks) {
    out.push(item(workbooks[0], COMPLETED_LABEL, COMPLETED_TITLE))
    out.push(item(workbooks[1], BLANK_LABEL, BLANK_TITLE))
  } else out.push(h.div(NO_WORKBOOK_NOTE, { class: 'staf-dl-note' }))
  return out
}

/** report_footer: PDF, CSV, GeoJSON, the completed workbook (``workbook``: its id, or null), then Close. */
export function reportFooter(pdf: string, csv: string, geojson: string, workbook: string | null = null): TagList {
  const btn = 'sfari-btn staf-btn'
  const items: Child[] = [downloadButton(pdf, 'PDF', btn), downloadButton(csv, 'CSV', btn), downloadButton(geojson, 'GeoJSON', btn)]
  if (workbook) items.push(downloadButton(workbook, COMPLETED_LABEL, btn, COMPLETED_TITLE))
  items.push(modalButton('Close', 'sfari-btn primary staf-btn'))
  return new TagList(...items)
}

/** scenario_suffix: ``-<name>`` for a report file made while an alternative is shown, empty for
 * Existing Conditions. */
export function scenarioSuffix(sset: ScenarioSet): string {
  const cur = sset.current
  if (cur.id === 'existing') return ''
  const slug = cur.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return `-${slug || cur.id}`
}

// EASI's Get Forms dialog (easi/app.py _forms_modal, _forms_summary, _forms_table; report.py
// desktop_metric_rows): SFARI's shell, built once from the screening (export_result()) as the report is:
// nothing under the backdrop can change while it is open, so it has no live outputs. The tab strip
// carries the downloads; the 20-row table scrolls in its own box.
import { h, type Tag } from '../../app/htm'
import { pyG } from '../../app/pyfmt'
import { FORMS_TITLE, formsDownloads } from '../../shared/downloads'
import { modal, modalButton, navsetPill } from '../../shared/shinyui'
import { entriesFromResult } from './calculator'
import { pyFloatOf } from './evaluator'
import { metricsById } from './method'
import { BORROWED_MARK, isBorrowed } from './notices'
import { siteIdentity } from './report'

type Obj = Record<string, any>

const BARE_UNITS = new Set(['', 'ratio', 'index', 'probability'])

/** report._input_value_text: a traced input for a reader (12.4%, 1.8 km/km2, 4A), a dash when blank. */
export function inputValueText(value: unknown, units = ''): string {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  const number = typeof value === 'number' || typeof value === 'string' ? pyFloatOf(value) : null
  if (number === null) return String(value)
  const text = Number.isFinite(number) && Number.isInteger(number) && Math.abs(number) < 1e15 ? String(Object.is(number, -0) ? 0 : number) : pyG(number)
  const u = (units || '').trim()
  if (BARE_UNITS.has(u)) return text
  return u === '%' ? text + u : `${text} ${u}`
}

export interface DesktopRow {
  n: number, metricId: string, discipline: string, function: string, metric: string, method: string,
  inputs: { label: string, value: string }[], valueText: string, rating: string, assessed: boolean, computed: string, status: string,
  source: string, note: string, borrowed: boolean
}

/** report.desktop_metric_rows: the 20 desktop metrics with the values each was rated from, in catalog order. */
export function desktopMetricRows(result: Obj | null): DesktopRow[] {
  const report = (result || {}).report || {}
  const byId = new Map<unknown, Obj>()
  for (const row of report.metricRows || []) byId.set(row.metricId, row)
  const rows: DesktopRow[] = []
  let n = 0
  for (const [mid, meta] of Object.entries(metricsById())) {
    n += 1
    const row = byId.get(mid) || {}
    const inputs = ((row.scoring || {}).inputs || [])
      .filter((item: Obj) => !item.contextOnly || item.key === 'indicators')
      .map((item: Obj) => ({ label: String(item.label || item.key || ''), value: inputValueText(item.value, item.units || '') }))
    const assessed = row.status === 'override'
    rows.push({
      n, metricId: mid, discipline: row.discipline || meta.discipline || '', function: row.functionName || meta.functionName || '',
      metric: row.name || meta.name || '', method: row.methodTitle || '', inputs, valueText: row.valueText || '', rating: row.rating || '',
      assessed, computed: assessed ? (row.effectiveOverride || {}).generatedRating || '' : '', status: row.status || '', source: row.source || '',
      note: String(row.userNote || '').trim(), borrowed: isBorrowed(row),
    })
  }
  return rows
}

const countText = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
/** str.capitalize */
const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()

/** _forms_summary: what the list holds and what the completed workbook carries. */
export function formsSummary(res: Obj, rows: DesktopRow[], cells: Record<string, string>): Tag {
  const rated = rows.filter((r) => r.rating).length
  const nAssessed = rows.filter((r) => r.assessed).length
  const nNotes = rows.filter((r) => r.note).length
  const carried = [nAssessed ? countText(nAssessed, 'override score', 'override scores') : '', nNotes ? countText(nNotes, 'note', 'notes') : '']
    .filter((t) => t)
  let text = `${rated} of ${rows.length} desktop metrics rated.`
  if (carried.length) text += ` ${capitalize(carried.join(' and '))} ${nAssessed + nNotes === 1 ? 'is' : 'are'} carried into the completed workbook.`
  text += ' Excel calculates the ratings when the workbook opens. In Protected View, choose Enable Editing first.'
  const [, disclosures] = entriesFromResult(res, cells)
  const extra = disclosures.filter((line) => !line.startsWith('Override scores:')).map((line) => h.span(line, { class: 'ff-src-sub' }))
  return h.div(h.span(text, { role: 'status' }), ...extra, { class: 'ff-status' })
}

/** app._rate_chip: the rating's colors without the control. */
const rateChip = (r: DesktopRow) => h.span(r.rating || '—', { class: `easi-rate-chip rate-${r.rating || 'auto'}` })

/** _forms_table */
export function formsTable(rows: DesktopRow[]): Tag {
  const dim = 'color:#8a93a3;font-size:11px;'
  const body = rows.map((r) => {
    const entries = r.inputs.length ? r.inputs.map((item) => h.div(h.span(item.label + ': '), h.b(item.value)))
      : [h.span(r.valueText || 'not available', { style: 'color:#8a93a3;' })]
    const rating: Tag[] = [rateChip(r)]
    if (r.assessed) rating.push(h.span(`override, computed ${r.computed || 'none'}`, { class: 'ff-src-sub' }))
    return h.tr(
      h.td(String(r.n), { style: dim }), h.td(r.discipline, { style: dim }), h.td(r.function), h.td(r.metric + (r.borrowed ? BORROWED_MARK : '')),
      h.td(...entries, { style: 'font-size:11.5px;color:#2f3a52;' }), h.td(...rating),
      h.td(r.source, r.note ? h.span(r.note, { class: 'ff-src-sub' }) : null, { style: 'font-size:11px;color:#45506a;' }))
  })
  return h.table(h.thead(h.tr(...['#', 'Discipline', 'Function', 'Metric', 'Entries (value rated)', 'Rating', 'Source'].map((t) => h.th(t)))),
    h.tbody(...body), { class: 'easi-tbl ff-table', id: 'easi-desktop-metrics' })
}

/** _forms_modal: the Get Forms dialog for one screening (``cells``: the calculator's entry cells, empty
 * without one). */
export function formsModal(res: Obj, cells: Record<string, string>, hasCalculator: boolean): Tag {
  const rows = desktopMetricRows(res)
  const site = siteIdentity(res)
  const siteLine = [site.name || '(unnamed stream)', site.reach, site.coords].filter((p) => p).join(' · ')
  return modal([
    h.div(siteLine, { class: 'ff-site' }),
    navsetPill('easi-gf_tabs', [
      { title: 'Desktop metrics', value: 'metrics', content: [formsSummary(res, rows, cells), h.div(formsTable(rows), { class: 'ff-table-wrap' })] },
    ], formsDownloads(['easi-dl_forms_pdf', 'Desktop metrics PDF', "The 20 desktop metrics with this site's values, ratings and sources"],
      { workbooks: hasCalculator ? ['easi-dl_forms_filled', 'easi-dl_forms_blank'] : null }), 'metrics'),
  ], { title: FORMS_TITLE, easyClose: true, size: 'xl', footer: modalButton('Close'), bodyClass: 'ff-modal-body' })
}

// DEEP's Get Forms dialog (deep/app.py _field_forms_modal, ff_site, ff_status, ff_table, ff_preview;
// report.py metric_rows): SFARI's shell around the loaded assessment's metrics, each with how it is
// measured (F in the field, D from the desk), its status and the value the desk answered; the preview is
// the field forms made for the loaded version. The shell never redraws while it is open; the status,
// the table and the preview are its outputs.
import { h, type Tag } from '../../app/htm'
import { pyFixed, pyFixedComma } from '../../app/pyfmt'
import { FORMS_TITLE, formsDownloads } from '../../shared/downloads'
import { modal, modalButton, navsetPill, outputUi } from '../../shared/shinyui'
import { COMPUTABLE } from './computed'
import { type Bundle, isHeld, type Metric, methodText, type Rec, unitsOf } from './method'
import { pyStrOf, rows, sourceLabel } from './report'
import { withheld, withheldReason } from './support'

export const STATUS_AVAILABLE = 'Available'
export const STATUS_PENDING = 'Pending'
export const STATUS_UNAVAILABLE = 'Unavailable'
export const STATUS_REFERENCE_ONLY = 'Reference only'
export const STATUS_FIELD = 'Measure in the field'
export const STATUS_ENTERED = 'Entered'
export const STATUS_NA = 'Not applicable'
export const STATUS_WITHHELD = 'Not scored'

const BADGE: Record<string, string> = {
  [STATUS_AVAILABLE]: '#d8ecd8;color:#1f6b32', [STATUS_ENTERED]: '#d8ecd8;color:#1f6b32', [STATUS_PENDING]: '#eef1f6;color:#5a6478',
  [STATUS_UNAVAILABLE]: '#f3d9d9;color:#8a2d2d', [STATUS_REFERENCE_ONLY]: '#e7ddf3;color:#5b3f8a', [STATUS_FIELD]: '#dce8f5;color:#2c4f7a',
  [STATUS_NA]: '#eef1f6;color:#5a6478', [STATUS_WITHHELD]: '#fdf0d6;color:#7a5b12',
}

/** field_form.measure_code: D for a metric the desk can answer, F for one measured in the field. */
export function measureCode(m: Metric, desktopIds: Set<string> = COMPUTABLE): string {
  if (desktopIds.has(m.metricId)) return 'D'
  const kind = String(m.inputType || '').toLowerCase()
  return kind.includes('desktop') || kind.includes('gis') ? 'D' : 'F'
}

const blank = (v: unknown) => v === null || v === undefined || v === '' || v === 'None'

/** field_form._reach_id */
export function reachId(dl: Rec): string {
  if (dl.network === 'nhdplus-hr' && !blank(dl.nhdplus_id)) return `NHDPlusID ${dl.nhdplus_id}`
  if (!blank(dl.comid)) return `COMID ${dl.comid}`
  return ''
}

/** float(x) as Python reads it (a number, or a number's text), else null */
function pyFloatOf(v: unknown): number | null {
  if (typeof v === 'number') return v
  if (typeof v !== 'string') return null
  const t = v.trim()
  if (/^[+-]?(nan|inf(inity)?)$/i.test(t)) return t.replace(/^[+-]/, '').toLowerCase() === 'nan' ? NaN : t.startsWith('-') ? -Infinity : Infinity
  return t !== '' && /^[+-]?(\d[\d_]*\.?\d*([eE][+-]?\d+)?|\.\d+([eE][+-]?\d+)?)$/.test(t) ? Number(t.replace(/_/g, '')) : null
}

/** field_form._coords */
export function coords(dl: Rec): string {
  const lat = pyFloatOf(dl.snapped_lat)
  const lon = pyFloatOf(dl.snapped_lon)
  return lat === null || lon === null ? '' : `${pyFixed(lat, 5)}, ${pyFixed(lon, 5)}`
}

/** LoadedAssessment.assessment_name */
export function assessmentName(la: Bundle): string {
  return String('assessmentName' in la ? la.assessmentName ?? '' : la.assessmentId ?? '')
}

/** ff_site: the assessment, its version and the site. */
export function ffSite(la: Bundle | null, ref: string | null, delin: Rec | null): Tag {
  const dl = (delin ?? {}).delineation ?? {}
  const name = la ? assessmentName(la) : 'No assessment loaded'
  const head = name + (ref ? ` (${ref})` : '')
  if (!Object.keys(dl).length) return h.div(`${head} · Delineate a reach to print the site on the forms.`, { class: 'ff-site' })
  const parts = [head, dl.gnis_name || '(unnamed stream)', reachId(dl)]
  const c = coords(dl)
  if (c) parts.push(c)
  if (!blank(dl.reach_length_ft)) {
    const ft = pyFloatOf(dl.reach_length_ft)
    if (ft !== null) parts.push(`${pyFixedComma(ft, 0)} ft`)
  }
  return h.div(parts.filter((p) => p).join(' · '), { class: 'ff-site' })
}

export interface MetricRow {
  discipline: string, function: string, metricId: string, metric: string, units: string, code: string, method: string, status: string,
  value: string, source: string, scored_against: string, repeat: boolean
}

/** report.metric_rows: one row per metric of the assessment, then one per metric it withholds. */
export function metricRows(la: Bundle | null, measured: Record<string, Rec>, computing = false, desktopIds: Set<string> = COMPUTABLE): MetricRow[] {
  const out: MetricRow[] = []
  const seen = new Set<string>()
  for (const [fn, m, val, , meta, raw] of rows(la, measured ?? {})) {
    const mid = m.metricId
    const code = measureCode(m, desktopIds)
    const has = val !== null && val !== undefined && val !== ''
    let status: string
    if (has) status = meta.reference_only ? STATUS_REFERENCE_ONLY : meta.origin === 'desktop' ? STATUS_AVAILABLE : STATUS_ENTERED
    else if (raw.na) status = STATUS_NA
    else if (code === 'D') status = computing ? STATUS_PENDING : STATUS_UNAVAILABLE
    else status = STATUS_FIELD
    out.push({
      discipline: fn.discipline ?? '', function: fn.functionName ?? '', metricId: mid, metric: m.metricName ?? mid,
      units: unitsOf(m), code, method: methodText(m), status, value: has ? pyStrOf(raw, 'value') : '', source: sourceLabel(meta),
      scored_against: meta.scored_against || '', repeat: seen.has(mid),
    })
    seen.add(mid)
  }
  for (const w of withheld(la)) {
    const fns = (w.functions || []).map((f: Rec) => String(f.functionName || f.functionId)).join(', ')
    out.push({ discipline: '', function: fns, metricId: w.metricId, metric: w.metricName || '', units: w.units || '', code: '', method: '',
      status: STATUS_WITHHELD, value: '', source: withheldReason(w), scored_against: '', repeat: false })
  }
  return out
}

/** report.metric_status_counts: by status over distinct metrics. */
export function metricStatusCounts(rs: MetricRow[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const r of rs) if (!r.repeat) out[r.status] = (out[r.status] ?? 0) + 1
  return out
}

/** ff_status: how many metrics the desk answered, or what the forms wait on. */
export function ffStatus(la: Bundle | null, measured: Record<string, Rec>, computing: boolean, delineated: boolean): Tag {
  if (!la) return h.div('Load an assessment to list its metrics.', { class: 'ff-status' })
  const rs = metricRows(la, measured, computing)
  const counts = metricStatusCounts(rs)
  const n = Object.values(counts).reduce((a, b) => a + b, 0)
  const nDesk = rs.filter((r) => !r.repeat && r.code === 'D').length
  let text: string
  if (computing) text = 'Preparing field forms… computing the desktop metrics (STAF site engine, StreamCat lookup, 3DEP).'
  else if (!delineated) text = `${n} metrics in this assessment. ${nDesk} can be answered from the desk once a reach is delineated.`
  else text = `${counts[STATUS_AVAILABLE] ?? 0} of ${nDesk} desktop metrics answered. ${counts[STATUS_FIELD] ?? 0} metrics are measured in the field.`
  if (counts[STATUS_WITHHELD]) {
    const items = withheld(la)
    const nHeld = items.filter(isHeld).length
    const nIns = items.length - nHeld
    if (nIns) text += ` ${nIns} metric${nIns === 1 ? '' : 's'} not scored for insufficient reference support.`
    if (nHeld) text += ` ${nHeld} metric${nHeld === 1 ? '' : 's'} held for review and not scored.`
  }
  return h.div(h.span(text, { role: 'status', 'aria-live': 'polite' }), { class: 'ff-status' })
}

/** ff_table */
export function ffTable(la: Bundle | null, measured: Record<string, Rec>, computing: boolean): Tag | null {
  if (!la) return null
  const dim = 'color:#8a93a3;font-size:11px;'
  const trs = metricRows(la, measured, computing).map((r) => {
    const badge = h.span(r.status, { class: 'ff-badge', style: `background:${BADGE[r.status] ?? '#eef1f6;color:#5a6478'};` })
    const value = r.value ? h.b(r.value) : h.span('—', { style: 'color:#8a93a3;' })
    return h.tr(
      h.td(r.discipline, { style: dim }), h.td(r.function, { style: dim }), h.td(r.metric + (r.units ? ` (${r.units})` : '')),
      h.td(r.code, { style: 'font-size:11px;text-align:center;' }), h.td(r.method, { style: 'font-size:11px;color:#45506a;' }),
      h.td(badge, { style: 'font-size:11px;' }), h.td(value, { style: 'font-size:11px;color:#2f3a52;' }), h.td(r.source, { style: 'font-size:11px;' }))
  })
  return h.table(
    h.thead(h.tr(h.th('Discipline'), h.th('Function'), h.th('Metric'),
      h.th('F/D', { title: 'F: measured in the field. D: answered from the desk.' }, { style: 'white-space:nowrap;text-align:center;' }),
      h.th('How it is measured'), h.th('Status'), h.th('Value'), h.th('Source'))),
    h.tbody(...trs), { class: 'easi-tbl ff-table', id: 'deep-field-form-metrics' })
}

/** ff_preview: the field forms of the loaded version (``src``: the made PDF's address). */
export function ffPreview(src: string): Tag {
  return h.div(h.iframe({ src, title: 'Field forms preview' }, { class: 'ff-preview-frame' }), { class: 'ff-preview' })
}

/** _field_forms_modal: the shell; without a calculator for the loaded version a note stands where the
 * workbooks would be. */
export function formsModal(hasCalc: boolean, site: Tag, status: Tag, table: Tag | null, preview: Tag | null): Tag {
  return modal([
    outputUi('deep-ff_site', site),
    navsetPill('deep-ff_tabs', [
      { title: 'Metrics', value: 'metrics', content: [outputUi('deep-ff_status', status), h.div(outputUi('deep-ff_table', table), { class: 'ff-table-wrap' })] },
      { title: 'Field forms preview', value: 'preview', content: outputUi('deep-ff_preview', preview) },
    ], formsDownloads(['deep-dl_metrics_pdf', 'Metrics PDF'], { fieldForms: 'deep-dl_field_forms',
      workbooks: hasCalc ? ['deep-dl_calc_filled', 'deep-dl_calc_blank'] : null }), 'metrics'),
  ], { title: FORMS_TITLE, easyClose: true, size: 'xl', footer: modalButton('Close'), bodyClass: 'ff-modal-body' })
}

/** calculator._ref_slug, blank_filename, filled_filename */
export function refSlug(la: Bundle | null): string {
  const raw = la ?? {}
  let ref: string | null = raw.assessmentRef ? String(raw.assessmentRef) : null
  if (!ref) {
    const aid = raw.assessmentId
    const version = raw.version || (raw.library ?? {}).version
    ref = aid && version ? `${aid}@v${Math.trunc(Number(version))}` : null
  }
  return (ref ?? 'assessment').split('@').join('-')
}
export const blankFilename = (la: Bundle | null) => `deep-calculator-${refSlug(la)}-blank.xlsx`
export const filledFilename = (la: Bundle | null) => `deep-calculator-${refSlug(la)}.xlsx`
/** report.field_forms_filename, metrics_filename */
export const fieldFormsFilename = (la: Bundle | null) => `deep-field-forms-${(la ?? {}).assessmentId || 'assessment'}.pdf`
export const metricsFilename = (la: Bundle | null) => `deep-metrics-${(la ?? {}).assessmentId || 'assessment'}.pdf`

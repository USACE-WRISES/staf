// SFARI's Get Forms dialog (sfari/app.py _desktop_metrics_modal, ff_site, ff_status, ff_table,
// ff_preview): the site line, the desktop metrics pill (a live status line and the 26 desktop-metric rows
// with their readiness), the field forms' preview, and the downloads every tool's Get Forms offers. The
// shell never redraws while it is open; the status, the table and the preview are its outputs.
import { type Child, h, type Tag } from '../../app/htm'
import { pyFixed, pyFixedComma } from '../../app/pyfmt'
import { FORMS_TITLE, formsDownloads } from '../../shared/downloads'
import { actionButton, modal, modalButton, navsetPill, outputUi } from '../../shared/shinyui'
import type { Evidence } from './evidence'
import { CATEGORY_ORDER, FNS_BY_CAT, METRICS, METRICS_BY_FN, METRICS_BY_ID, type SfariMetric } from './method'

type Obj = Record<string, any>
/** The field forms the dialog previews and offers (parity/copy_page.py's copy of SFARI's own). */
export const FIELD_FORMS_URL = 'forms/sfari/SFARI_Field_Form_v1.0.pdf'
export const FIELD_FORMS_FILENAME = 'sfari-field-forms.pdf'
/** the plain calculator every workbook is assembled from (calculator.TEMPLATE_PATH) */
export const TEMPLATE_URL = 'forms/sfari/SFARI_Calculator_Template_2026-10-19.xlsx'
/** the blank workbook, served as it is (calculator.BLANK_PATH); fetch encodes the space */
export const BLANK_URL = 'forms/sfari/2026-10-19_SFARI Calculator.xlsx'
/** both workbooks download under the calculator's version (owner, 2026-10-19) */
export const BLANK_FILENAME = '2026-10-19_SFARI Calculator.xlsx'

const BADGE: Record<string, string> = {
  Available: '#d8ecd8;color:#1f6b32', Pending: '#eef1f6;color:#5a6478', Unavailable: '#f3d9d9;color:#8a2d2d',
  'Local review required': '#e7ddf3;color:#5b3f8a', 'Run cross-section tool': '#dce8f5;color:#2c4f7a',
}

/** config.desktop_metrics */
export const DESKTOP_METRICS = METRICS.filter((m) => (m as Obj).desktopSupportable)

/** report._reach_id_str */
export function reachIdStr(dl: Obj): string {
  if (dl.network === 'nhdplus-hr' && ![null, undefined, '', 'None'].includes(dl.nhdplus_id)) return `NHDPlusID ${dl.nhdplus_id}`
  const comid = dl.comid
  if (![null, undefined, '', 'None'].includes(comid)) return `COMID ${comid}`
  if (dl.snapped_lat !== null && dl.snapped_lat !== undefined && dl.snapped_lon !== null && dl.snapped_lon !== undefined) {
    return `${pyFixed(Number(dl.snapped_lat), 5)}, ${pyFixed(Number(dl.snapped_lon), 5)}`
  }
  return '(reach)'
}

/** report._coords_str */
export function coordsStr(dl: Obj): string {
  if (dl.snapped_lat !== null && dl.snapped_lat !== undefined && dl.snapped_lon !== null && dl.snapped_lon !== undefined) {
    return `${pyFixed(Number(dl.snapped_lat), 5)}, ${pyFixed(Number(dl.snapped_lon), 5)}`
  }
  return ''
}

/** _fmt_ft */
function fmtFt(value: unknown): string {
  const v = typeof value === 'number' ? value : typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN
  return Number.isNaN(v) ? 'unknown' : `${pyFixedComma(v, 0)} ft`
}

/** ff_site */
export function ffSite(delin: Obj | null): Tag {
  const dl = (delin ?? {}).delineation ?? {}
  if (!Object.keys(dl).length) return h.div('Delineate a reach first.', { class: 'ff-site' })
  const parts = [dl.gnis_name || '(unnamed stream)', reachIdStr(dl)]
  const coords = coordsStr(dl)
  if (coords) parts.push(coords)
  if (![null, undefined, '', 'None'].includes(dl.reach_length_ft)) parts.push(fmtFt(dl.reach_length_ft))
  return h.div(parts.join(' · '), { class: 'ff-site' })
}

/** _ff_status: a metric's readiness for the field forms. */
function ffStatusOf(m: SfariMetric, ed: Obj): string {
  if (ed.status === 'ok' && ed.value_text) return 'Available'
  const client = ((m as Obj).desktopSource ?? {}).client
  if (client === 'xscalc') return 'Run cross-section tool'
  if (client === 'manual') return 'Local review required'
  if (ed.status === 'unavailable' || ed.status === 'error') return 'Unavailable'
  return 'Pending'
}

/** urlparse(url).netloc */
export function netloc(url: string): string {
  const m = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)/i.exec(url)
  return m ? m[1] : ''
}

/** _ff_rows: the desktop-metric rows and the status counts. */
export function ffRows(ev: Record<string, Evidence>): [Tag[], Record<string, number>] {
  const rows: Tag[] = []
  const counts: Record<string, number> = {}
  const dim = 'color:#8a93a3;font-size:11px;'
  for (const cat of CATEGORY_ORDER) {
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      for (const m of METRICS_BY_FN.get(f.id) ?? []) {
        const mm = m as Obj
        if (!mm.desktopSupportable) continue
        const ds = mm.desktopSource ?? {}
        const ed = (ev[m.metricId] ?? {}) as Obj
        const st = ffStatusOf(m, ed)
        counts[st] = (counts[st] ?? 0) + 1
        const badge = h.span(st, { class: 'ff-badge', style: `background:${BADGE[st] ?? '#eef1f6;color:#5a6478'};` })
        const val = st === 'Available' ? h.b(ed.value_text) : h.span('—', { style: 'color:#8a93a3;' })
        const srcUrl = ed.source_url || ds.url
        const srcName = ed.source || (ds.url ? netloc(ds.url) || null : null) || '—'
        const src = srcUrl ? h.a(srcName, { href: srcUrl, target: '_blank', rel: 'noopener' }) : srcName
        const describes = ed.anchor_label ? h.span('describes the ' + ed.anchor_label, { class: 'ff-src-sub' }) : null
        const fallback = ed.fallback_reason ? h.span(ed.fallback_reason, { class: 'ff-src-sub' }) : null
        rows.push(h.tr(
          h.td(cat, { style: dim }), h.td(f.name, { style: dim }), h.td(m.name),
          h.td(ds.label || '—', { style: 'font-size:11px;color:#45506a;' }),
          h.td(badge, { style: 'font-size:11px;' }),
          h.td(val, { style: 'font-size:11px;color:#2f3a52;' }),
          h.td(src, describes, fallback, { style: 'font-size:11px;' })))
      }
    }
  }
  return [rows, counts]
}

/** ff_table */
export function ffTable(ev: Record<string, Evidence>): Tag {
  const [rows] = ffRows(ev)
  return h.table(h.thead(h.tr(h.th('Discipline'), h.th('Function'), h.th('Metric'), h.th('Method'), h.th('Status'), h.th('Value'), h.th('Source'))),
    h.tbody(...rows), { class: 'easi-tbl ff-table', id: 'sfari-desktop-metrics' })
}

export interface PullState {
  /** the engine runs (its progress line, without the family's place) */
  engineRunning: boolean
  engineText: string
  /** the StreamCat source is being found */
  finding: boolean
  /** the pull: not run for this site, running, done, or failed */
  pull: 'initial' | 'running' | 'ok' | 'failed'
  done: number
  total: number
}

/** ff_status: what the forms wait on, or how many desktop metrics the pull found; Retry after a failure. */
export function ffStatus(ev: Record<string, Evidence>, st: PullState): Tag {
  const pulling = st.pull === 'running' || st.engineRunning || st.finding
  const n = DESKTOP_METRICS.length
  const [, counts] = ffRows(ev)
  let text: string
  if (st.engineRunning) text = 'Preparing field forms… ' + st.engineText
  else if (st.finding) text = 'Preparing field forms… finding the StreamCat source.'
  else if (pulling) text = `Preparing field forms… pulling desktop evidence (${st.done} of ${st.total}).`
  else if (st.pull === 'initial') text = `${n} of the ${METRICS_BY_ID.size} metrics can be evaluated from a desk before the site visit.`
  else text = `${counts.Available ?? 0} of ${n} desktop metrics pulled. The rest are flagged for the field visit.`
  let retry: Tag | null = null
  if (!pulling && st.pull === 'failed') {
    text = 'The desktop evidence pull failed. ' + text
    retry = actionButton('sfari-ff_retry', 'Retry pull', 'btn btn-sm btn-outline-secondary')
  }
  const spinner = st.finding ? h.span({ class: 'easi-spinner easi-lookup-spinner', 'aria-hidden': 'true' }) : null
  return h.div(h.span(spinner, text, { role: 'status', 'aria-live': 'polite' }), retry, { class: 'ff-status' })
}

/** ff_preview: the blank field forms, shown by the browser's PDF viewer. */
export function ffPreview(): Tag {
  return h.div(h.iframe({ src: FIELD_FORMS_URL, title: 'Field forms preview' }, { class: 'ff-preview-frame' }), { class: 'ff-preview' })
}

/** _desktop_metrics_modal: the shell; its outputs are drawn into it. */
export function formsModal(site: Child, status: Child, table: Child, preview: Child): Tag {
  return modal([
    outputUi('sfari-ff_site', site),
    navsetPill('sfari-ff_tabs', [
      { title: 'Desktop metrics', value: 'metrics', content: [outputUi('sfari-ff_status', status), h.div(outputUi('sfari-ff_table', table), { class: 'ff-table-wrap' })] },
      { title: 'Field forms preview', value: 'preview', content: outputUi('sfari-ff_preview', preview) },
    ], formsDownloads(['sfari-dl_desktop_metrics', 'Desktop metrics PDF'], { fieldForms: 'sfari-dl_field_forms', workbooks: ['sfari-dl_calc_filled', 'sfari-dl_calc_blank'] }),
    'metrics'),
  ], { title: FORMS_TITLE, easyClose: true, size: 'xl', footer: modalButton('Close'), bodyClass: 'ff-modal-body' })
}

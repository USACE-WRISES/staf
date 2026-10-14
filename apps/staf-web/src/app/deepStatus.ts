// DEEP's assessment statuses (deep/session.py and app.py; owner, 2026-10-14): what each status is
// called and means, its colors, and the markup that shows it: the pill, the worksheet's first line, the
// report's heading, the Draft notice and Basin's offer of a Draft. The static app keeps its own copy of
// the words (tests/status.test.ts holds them, and the markup, to Shiny's while the Shiny app exists).
import { h, type Tag, TagList } from './htm'
import { actionButton, modal, modalButton } from '../shared/shinyui'

type Obj = Record<string, any>

/** The statuses DEEP runs (session.LIFECYCLES). */
export const LIFECYCLES = ['draft', 'preliminary', 'certified']
export const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft', preliminary: 'Preliminary', under_review: 'Under review', certified: 'Final', revised: 'Revised', retired: 'Retired',
}
/** session.STATUS_NOTES: what each status DEEP runs means, in one line. */
export const STATUS_NOTES: Record<string, string> = {
  draft: 'For research and regional assessment development only.',
  preliminary: 'Reviewed and approved as Preliminary. Not yet certified for use.',
  certified: 'Cleared and approved for use.',
}
/** session.SHEET_NOTES: what a spreadsheet's banner says, Preliminary's shorter (owner, 2026-10-14). */
export const SHEET_NOTES: Record<string, string> = {
  draft: 'For research and regional assessment development only.',
  preliminary: 'Reviewed and approved as Preliminary.',
  certified: 'Cleared and approved for use.',
}
/** session.STATUS_COLORS: (line, fill), the map's region palette; the line color carries the files' bands. */
export const STATUS_COLORS: Record<string, [string, string]> = {
  draft: ['#475569', '#6b6459'],
  preliminary: ['#b45309', '#d97706'],
  certified: ['#303f9f', '#3f51b5'],
}
/** session.STATUS_LINE */
export const STATUS_LINE = 'Assessment status: {label}. {note}'
/** app.py _BADGE_CLASS */
export const BADGE_CLASS: Record<string, string> = { draft: 'deep-badge-draft', preliminary: 'deep-badge-prelim', certified: 'deep-badge-cert' }
/** app.py REGIONS_OFF_AT_START: the statuses the map's legend starts with turned off. */
export const REGIONS_OFF_AT_START = ['draft']

/** str.title() for a status DEEP does not know (status_label's fallback). */
const title = (s: string) => s.replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase())

/** session.lifecycle_status: draft, preliminary or certified (preliminary when the bundle names none it runs). */
export function lifecycleStatus(bundle: Obj | null): string {
  const lib = (bundle ?? {}).library ?? {}
  for (const src of [bundle ?? {}, lib]) {
    for (const key of ['lifecycle', 'status']) {
      const v = src?.[key]
      if (v) {
        const s = String(v).trim().toLowerCase()
        if (LIFECYCLES.includes(s)) return s
      }
    }
  }
  return 'preliminary'
}

/** session.status_label */
export function statusLabel(status: unknown): string {
  const s = String(status ?? '').trim().toLowerCase()
  return STATUS_LABELS[s] || (s ? title(s) : STATUS_LABELS.preliminary)
}

/** session.status_note */
export function statusNote(status: unknown): string {
  return STATUS_NOTES[lifecycleStatus({ lifecycle: status })]
}

/** session.status_parts: the head ("Assessment status: Draft.") and the line of meaning. */
export function statusParts(status: unknown): [string, string] {
  const s = lifecycleStatus({ lifecycle: status })
  return [STATUS_LINE.replace('{label}', statusLabel(s)).replace('{note}', '').trimEnd(), STATUS_NOTES[s]]
}

/** session.status_line: what every file says. */
export function statusLine(status: unknown): string {
  return statusParts(status).join(' ')
}

/** session.sheet_status_parts and sheet_status_line: as a spreadsheet says them. */
export function sheetStatusParts(status: unknown): [string, string] {
  const s = lifecycleStatus({ lifecycle: status })
  return [STATUS_LINE.replace('{label}', statusLabel(s)).replace('{note}', '').trimEnd(), SHEET_NOTES[s]]
}
export function sheetStatusLine(status: unknown): string {
  return sheetStatusParts(status).join(' ')
}

/** session.status_slug: the status word a file name carries (draft, preliminary, final). */
export function statusSlug(status: unknown): string {
  return statusLabel(lifecycleStatus({ lifecycle: status })).toLowerCase()
}

/** app.py _version_of: the library block's version, else the stamped one. */
export function versionOf(raw: Obj | null): unknown {
  const r = raw ?? {}
  const lib = r.library ?? {}
  return lib.version !== null && lib.version !== undefined ? lib.version : r.version
}

/** session.download_stem: ``<id>-v<N>-<status>``, the part every DEEP download's name shares. */
export function downloadStem(bundle: Obj | null): string {
  const b = bundle ?? {}
  const v = versionOf(b)
  const parts = [String(b.assessmentId || 'assessment'), ...(v !== null && v !== undefined && v !== '' ? [`v${v}`] : [])]
  return [...parts, statusSlug(lifecycleStatus(b))].join('-')
}

/** app.py _badge_class: a status DEEP does not run reads as preliminary. */
export function badgeClass(lifecycle: unknown): string {
  return BADGE_CLASS[lifecycleStatus({ lifecycle })]
}

/** app.py _status_pill: DRAFT, PRELIMINARY or FINAL in the status's color, its line on hover. */
export function statusPill(lifecycle: unknown, cls = ''): Tag {
  const life = lifecycleStatus({ lifecycle })
  return h.span(statusLabel(life), { class: `deep-card-badge ${badgeClass(life)}` + (cls ? ` ${cls}` : ''), title: statusNote(life) })
}

/** app.py _assessment_line: the worksheet's first line, the assessment in use, its version and its status. */
export function assessmentLine(name: string, version: unknown, lifecycle: unknown): Tag {
  const life = lifecycleStatus({ lifecycle })
  return h.div(
    h.span(name, { class: 'deep-ws-name', title: name }),
    version !== null && version !== undefined && version !== '' ? h.span(`v${version}`, { class: 'deep-ws-ver' }) : null,
    statusPill(life),
    { class: 'deep-ws-assess', 'data-status': life })
}

/** app.py _report_heading: the report's heading, the assessment's name and its status pill. */
export function reportHeading(raw: Obj | null): Tag {
  if (!raw) return h.h3('Detailed assessment', { style: 'margin:0;' })
  return h.h3(raw.assessmentName ?? raw.assessmentId ?? '', statusPill(lifecycleStatus(raw), 'deep-report-pill'), { style: 'margin:0;' })
}

/** app.py _draft_notice: what DEEP asks before it uses a Draft, once per visit. */
export function draftNotice(name: string, version: unknown): Tag {
  return modal([assessmentLine(name, version, 'draft'), h.p(statusNote('draft'), { class: 'deep-draft-note' })], {
    title: 'Draft assessment',
    footer: new TagList(modalButton('Cancel'), actionButton('deep-draft_accept', 'Use the Draft', 'btn-primary')),
    easyClose: false,
  })
}

/** ui.input_action_link, as py-shiny renders it. */
export function actionLink(id: string, label: string): Tag {
  return h.a(h.span(label, { class: 'action-label' }), { class: 'action-button action-link', id, href: '#' })
}

/** A covering assessment as Basin offers it (deep/assessments.py covering_refs). */
export interface DraftEntry {
  assessmentId: string
  assessmentName: string
  regionName: string
  defaultRef: string
  versionByRef: Record<string, number>
}

/** app.py _draft_pending_block: Basin while only Drafts cover the site and none is in use. */
export function draftPending(entry: DraftEntry, several = false): Tag {
  const version = entry.versionByRef[entry.defaultRef]
  const line = [entry.regionName || '', version ? `v${version}` : ''].filter((x) => x).join(' · ')
  return h.div(
    h.div(h.span('Assessment', { class: 'deep-pane-label' }), statusPill('draft'), { class: 'deep-pane-head' }),
    h.div(entry.assessmentName || entry.assessmentId || '', { class: 'deep-pane-name' }),
    line ? h.div(line, { class: 'deep-pane-line' }) : null,
    h.div(statusNote('draft'), { class: 'deep-pane-note' }),
    h.div(several ? actionLink('deep-change_assessment', 'Choose') : actionLink('deep-use_draft', 'Use the Draft'),
      { class: 'deep-pane-change' }),
    { class: 'deep-pane-assess is-draft' })
}

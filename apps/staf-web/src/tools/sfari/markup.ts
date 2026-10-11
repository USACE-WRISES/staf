// SFARI's Assessment worksheet markup, as sfari/app.py builds it with htmltools (rendered by htm.ts):
// the function list, a function's metric rows (the row design EASI, SFARI and DEEP share,
// staf_workbook web.metric_action and friends), the score card, the suggestion line and the rollup rail.
import { type Child, h, HTML, outputHtml, pyHtmlEscape, type Tag } from '../../app/htm'
import { pyFixed, pyG } from '../../app/pyfmt'
import type { Evidence } from './evidence'
import {
  CATEGORY_ORDER, FN_IDS, FNS_BY_CAT, functionScoreBandColor, indexBandColor, indexBandLabel, LIKERT_NA, LIKERT_ORDER,
  type Scored, type SfariFunction, type SfariMetric,
} from './method'

export interface MetricRecord { likert?: string | null, note?: string, photos?: { id: string, uri: string }[] }
export interface FunctionRecord { score?: number | null, note?: string }

/** The worksheet's steps (app.py STEP_LABELS). */
export const STEP_LABELS: [string, string][] = [['identify', 'Identify'], ['basin', 'Basin'], ['review', 'Assessment'], ['report', 'Report']]
/** Hydraulics functions that open the cross-section hydraulics tool. */
export const HYDRAULICS_FNS = new Set(['low-flow-baseflow-dynamics', 'high-flow-dynamics', 'floodplain-connectivity'])
const FNF_SHORT: Record<string, string> = { Functioning: 'F', 'Functioning-at-Risk': 'AR', 'Non-Functioning': 'NF' }

/** A Shiny ui output holding ``content`` (``ui.output_ui(id)`` once rendered). */
export function output(id: string, content: Child = null): Tag {
  return h.div({ class: 'shiny-html-output', id: `sfari-${id}`, 'aria-live': 'polite' }, new HTML(outputHtml(content)))
}

// ------------------------------------------------------------------ staf_workbook web.py (metric rows)
const ROW_SVG = (body: string) => '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false" fill="none" '
  + `stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
export const ROW_ICONS: Record<string, string> = {
  scoring: ROW_SVG('<path d="M2.5 2v11.5H14"/><path d="M4.5 4.5c2.5 0 3 4.5 5 6.2 1 .8 2.2 1 3.5 1"/>'),
  note: ROW_SVG('<path d="M11.2 2.6l2.2 2.2-7.7 7.7-2.9.7.7-2.9z"/><path d="M9.6 4.2l2.2 2.2"/>'),
  photo: ROW_SVG('<path d="M2 5.5h2.6l1.2-1.8h4.4l1.2 1.8H14v7.5H2z"/><circle cx="8" cy="9.1" r="2.3"/>'),
  na: ROW_SVG('<circle cx="8" cy="8" r="5.6"/><path d="M4.1 11.9l7.8-7.8"/>'),
}

/** web.metric_action: one button of a metric row. */
export function metricAction(kind: string, label: string, { on = false, has = false, count = null as number | null, title = null as string | null,
  icon = null as string | null } = {}): Tag {
  const ic = icon ?? (kind === 'fnnote' ? 'note' : kind)
  if (count !== null && count > 1 && label === 'Photo') label = 'Photos'
  const parts: Child[] = [new HTML(ROW_ICONS[ic]), h.span(label, { class: 'staf-act-label' })]
  if (kind === 'note' || kind === 'fnnote') parts.push(h.span({ class: 'staf-act-dot' }))
  if (count !== null) parts.push(h.span(count ? String(count) : '', { class: 'staf-act-count' }))
  const cls = 'staf-act' + (on ? ' on' : '') + (has || (count ?? 0) > 0 ? ' has' : '')
  return h.button(...parts, { type: 'button', 'data-staf-panel': kind, 'aria-expanded': on ? 'true' : 'false', title: title || label }, { class: cls })
}

export const metricActions = (...buttons: (Tag | null)[]): Tag => h.div(...buttons.filter(Boolean), { class: 'staf-metric-acts' })

/** web.scoring_criteria: the rating rungs of a Scoring panel. */
export function scoringCriteria(rows: [string, string, string][], title?: string, sub?: string): Tag {
  const cells: Tag[] = []
  for (const [label, band, text] of rows) {
    cells.push(h.span(h.span({ class: `staf-crit-dot ${band}` }), label, { class: 'staf-crit-lbl' }))
    cells.push(h.span(text))
  }
  return h.div(title ? h.div(title, { class: 'staf-panel-title' }) : null, sub ? h.div(sub, { class: 'staf-panel-sub' }) : null,
    h.div(...cells, { class: 'staf-crit' }))
}

// ------------------------------------------------------------------ app.py
/** _info: the small circled i whose tip tooltip.js shows. */
export function info(text?: string | null, htmlTip?: string | null): Tag | null {
  const attrs: Record<string, string> = { onclick: 'event.preventDefault();event.stopPropagation();', tabindex: '0' }
  if (htmlTip) attrs['data-tip-html'] = htmlTip
  else if (text && text.trim()) attrs['data-tip'] = text.trim()
  else return null
  return h.span('i', attrs, { class: 'easi-info' })
}

const LIKERT_DOT: Record<string, string> = { 'Strongly Agree': 'good', Agree: 'good', Neutral: 'fair', Disagree: 'poor', 'Strongly Disagree': 'poor' }

function aboutTipHtml(m: SfariMetric): string {
  const name = m.name ?? ''
  const parts = [`<div class="easi-tip-title">${pyHtmlEscape(name)}</div>`]
  const stmt = (m.metricStatement ?? '').trim()
  if (stmt && stmt !== name) parts.push(`<div class="easi-tip-sec">${pyHtmlEscape(stmt)}</div>`)
  return parts.join('')
}

function scoringPanel(m: SfariMetric): Tag | null {
  const rungs: [string, string, string][] = []
  for (const c of m.likertCriteria ?? []) {
    const crit = (c.criteria ?? '').trim()
    if (crit) rungs.push([c.likert ?? '', LIKERT_DOT[c.likert ?? ''] ?? 'fair', crit])
  }
  if (!rungs.length) return null
  return scoringCriteria(rungs, 'Example scoring', "Illustrative only. Judge what's appropriate for your stream type and region.")
}

const PENDING_BADGE: [string, string] = ['Desktop pending', 'staf-ev-tag pending']
const BASIS_SUFFIX = /\s*\((?:HR reach watershed|NHDPlus V2 basin(?:, COMID \d+)?)\)/g
const ENGINE_NOTE = /Watershed of the clicked NHD reach, HR catchments aggregated(?:, [\d.]+ km2)?(?:, area agreement [\d.]+)?\.\s*/g
const ENGINE_WORDS = ['STAF site engine', 'StreamCat lookup engine', 'site engine']
const BORROWED_NOTICE = 'Desktop evidence comes from the nearest StreamCat reach downstream.'
const words = (s: string) => s.split(/\s+/).filter(Boolean).join(' ')

function evBadge(e: Evidence | undefined): [string, string] {
  if (!e) return ['Field', 'staf-ev-tag field']
  if (e.status === 'pending') return PENDING_BADGE
  return ['Desktop', 'staf-ev-tag']
}

/** _ev_value_ws: a value without its watershed-basis suffix. */
export function evValueWs(text: string): string {
  return words(String(text || '').replace(BASIS_SUFFIX, '').split('HR reach watershed').join('watershed'))
}

/** _ev_tip_ws: why a value suggests its rating, without the sentences that name an engine. */
export function evTipWs(e: Evidence | undefined): string {
  let note = String(e?.note || '').replace(ENGINE_NOTE, '')
  note = note.replace(BASIS_SUFFIX, '').split('HR reach watershed').join('watershed')
  const kept = words(note).split(/(?<=\.)\s+/).filter((s) => s && !ENGINE_WORDS.some((w) => s.includes(w)))
  if (e?.origin === 'streamcat' || e?.fallback_reason) kept.unshift('Read from EPA StreamCat.')
  return kept.join(' ').trim()
}

function evNotice(e: Evidence | undefined): Tag | null {
  const label = e?.anchor_label
  if (!label) return null
  return h.div(BORROWED_NOTICE, { title: 'Describes the ' + label + '.' }, { class: 'staf-metric-warn' })
}

function evidenceLine(m: SfariMetric, e: Evidence | undefined, pulling: boolean): Tag {
  const mid = m.metricId
  const ds = m.desktopSource
  if (e && e.status === 'ok') {
    const sug = e.suggested_likert
    const suggest = sug ? h.span('Suggested: ', h.b(sug),
      h.button('Use', { 'data-mid': mid, 'data-val': sug, type: 'button', title: `Use the suggested rating (${sug})` }, { class: 'staf-suggest-use sfari-suggest-chip' }),
      { class: 'staf-suggest' }) : null
    const [label, cls] = evBadge(e)
    return h.div(h.span(label, { class: cls }), h.b(evValueWs(e.value_text ?? ''), { class: 'staf-ev-val' }), info(evTipWs(e)), suggest, { class: 'staf-metric-ev' })
  }
  if (e && e.status === 'pending') {
    return h.div(h.span(PENDING_BADGE[0], { class: PENDING_BADGE[1] }), h.span('Computing the watershed…', { class: 'staf-ev-val muted' }), { class: 'staf-metric-ev' })
  }
  if (e && e.status === 'unavailable') {
    const url = ds?.url
    const link = url ? h.a('look it up ↗', { href: url, target: '_blank', rel: 'noopener' }) : null
    const [label, cls] = evBadge(e)
    return h.div(h.span(label, { class: cls }), h.span('Not available. Review in the field.', { class: 'staf-ev-val muted' }), link, info(evTipWs(e)), { class: 'staf-metric-ev' })
  }
  if (ds && pulling) {
    return h.div(h.span(PENDING_BADGE[0], { class: PENDING_BADGE[1] }), h.span('Pulling desktop evidence…', { class: 'staf-ev-val muted' }), { class: 'staf-metric-ev' })
  }
  if (ds) {
    const url = ds.url
    const link = url ? h.a('↗', { href: url, target: '_blank', rel: 'noopener', title: 'Open resource' }) : null
    return h.div(h.span('Desktop', { class: 'staf-ev-tag' }), h.span(ds.label ?? '', { class: 'staf-ev-val muted' }), link, { class: 'staf-metric-ev' })
  }
  return h.div(h.span('Field', { class: 'staf-ev-tag field' }), h.span('Field observation only.', { class: 'staf-ev-val muted' }), { class: 'staf-metric-ev' })
}

/** _metric_row: one metric of a function page. */
export function metricRow(m: SfariMetric, rc: MetricRecord | undefined, e: Evidence | undefined, pulling: boolean): Tag {
  const mid = m.metricId
  const rec = rc ?? {}
  const sel = rec.likert
  const note = rec.note || ''
  const hasNote = !!note.trim()
  const photos = rec.photos || []
  const opts: Tag[] = [h.option('Rate…', { value: '' })]
  for (const lv of [...LIKERT_ORDER, LIKERT_NA]) opts.push(h.option(lv, { value: lv, selected: sel === lv ? 'selected' : null }))
  const rate = h.select(...opts, { 'data-mid': mid, 'aria-label': `Rate: ${m.name}` }, { class: 'staf-rate sfari-likert-select' + (sel ? ' set' : '') })
  const thumbs = photos.map((p) => h.span(h.img({ src: p.uri ?? '' }, { class: 'sfari-thumb' }),
    h.button('×', { 'data-mid': mid, 'data-id': p.id, type: 'button' }, { class: 'sfari-photo-rm' }),
    { 'data-mid': mid, 'data-id': p.id }, { class: 'sfari-thumb-wrap' }))
  const photosRow = h.div(...thumbs,
    h.label(new HTML(ROW_ICONS.photo), 'Add photo',
      h.input({ type: 'file', accept: 'image/*', capture: 'environment', 'data-mid': mid }, { class: 'sfari-photo' }), { class: 'sfari-photo-btn' }),
    { 'data-mid': mid }, { class: 'sfari-photos' })
  const panel = scoringPanel(m)
  const stmt = (m.fieldStatement || m.metricStatement || '').trim()
  const actions = metricActions(
    panel ? metricAction('scoring', 'Scoring', { title: 'Example scoring' }) : null,
    metricAction('note', 'Note', { on: hasNote, has: hasNote, title: 'Add a note' }),
    metricAction('photo', 'Photo', { on: photos.length > 0, count: photos.length, title: 'Add a photo' }))
  const cls = 'staf-metric' + (hasNote ? ' show-note' : '') + (photos.length ? ' show-photo' : '')
  const scale = m.scale ?? 'R'
  return h.div(
    h.div(
      h.div(h.span(m.name, { class: 'staf-metric-name' }),
        h.span(scale, { title: m.scale === 'W' ? 'Watershed-scale metric' : 'Reach-scale metric' }, { class: 'staf-metric-scale' }),
        info(null, aboutTipHtml(m)), { class: 'staf-metric-head' }),
      stmt && stmt !== m.name ? h.div(stmt, { class: 'staf-metric-desc' }) : null,
      evidenceLine(m, e, pulling),
      e && e.status === 'ok' ? evNotice(e) : null,
      actions,
      { class: 'staf-metric-main' }),
    h.div(rate, { class: 'staf-metric-input' }),
    panel ? h.div(panel, { 'data-panel': 'scoring' }, { class: 'staf-metric-panel' }) : null,
    h.div(h.textarea(note, { 'data-mid': mid, placeholder: 'Note (optional)', 'aria-label': `Note: ${m.name}` }, { class: 'staf-metric-note sfari-metric-note' }),
      { 'data-panel': 'note' }, { class: 'staf-metric-panel' }),
    h.div(photosRow, { 'data-panel': 'photo' }, { class: 'staf-metric-panel' }),
    { 'data-metric': mid }, { class: cls })
}

const FSCORE_TIP = '<div class="easi-tip-sec">Your professional-judgment 0-15 score, using the evidence above.</div>'
  + '<div class="easi-tip-sec">'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span><span><b>0-5:</b> Non-Functioning</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span><span><b>6-10:</b> Functioning-at-Risk</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span><span><b>11-15:</b> Functioning</span></div>'
  + '</div>'

/** _score_card: the function's 0-15 score, its suggestion line and its note. */
export function scoreCard(f: SfariFunction, fid: string, rec: FunctionRecord, suggestion: Child): Tag {
  const score = rec.score ?? null
  const sval = score !== null ? score : 8
  const bandLbl = score !== null ? indexBandLabel(sval / 15.0) : 'Not scored yet'
  const bandCol = score !== null ? functionScoreBandColor(sval) : '#e7ebf1'
  const fnote = rec.note || ''
  const hasNote = !!fnote.trim()
  const cls = 'sfari-scorecard' + (score !== null ? '' : ' unset') + (hasNote ? ' show-fnnote' : '')
  return h.div(
    h.div(h.span('2', { class: 'sfari-step-num' }), h.span('Function score', { class: 'sfari-fscore-lbl' }), info(null, FSCORE_TIP), { class: 'sfari-fscore-head' }),
    h.p(f.functionStatement ?? '', { class: 'sfari-fn-statement' }),
    h.div(h.input({ type: 'range', min: '0', max: '15', step: '1', value: String(sval), 'data-fid': fid }, { class: 'sfari-fscore' }),
      h.span(score !== null ? String(score) : '–', { class: 'sfari-fscore-num' }),
      h.span(bandLbl, { class: 'sfari-fscore-band', style: `background:${bandCol};` }), { class: 'sfari-fscore-row' }),
    output('fn_suggest', suggestion),
    metricActions(metricAction('fnnote', 'Note', { on: hasNote, has: hasNote, title: 'Justification or notes, especially if the score differs from the suggestion' })),
    h.textarea(fnote, { 'data-fid': fid, placeholder: 'Justification / notes (especially if the score differs from the suggestion)…' }, { class: 'sfari-fn-note staf-metric-note' }),
    { 'data-staf-host': '' }, { class: cls })
}

/** fn_suggest */
export function suggestionLine(fid: string, sug: number | null): Tag {
  if (sug === null) return h.div('A suggested value appears as evidence is rated.', { class: 'sfari-suggest-line' })
  return h.div(`Suggested from metric Likerts: ${pyG(sug)}  `,
    h.button('Accept', { 'data-fid': fid, 'data-val': pyG(sug), type: 'button' }, { class: 'sfari-accept' }), { class: 'sfari-suggest-line' })
}

/** _stepper: the step links (report-ready.js gives the Report step the focus order). */
export function stepper(active: string): Tag {
  let done = true
  const items = STEP_LABELS.map(([key, label]) => {
    let cls = 'easi-step'
    if (key === active) {
      cls += ' active'
      done = false
    } else if (done) {
      cls += ' done'
    }
    return h.a(label, { 'data-step': key, role: 'button' }, { class: cls }, key === 'report' ? { tabindex: '0' } : {})
  })
  return h.div(...items, { class: 'easi-steps' })
}

/** fn_nav */
export function fnNav(current: number, fs: Record<string, FunctionRecord>): Child[] {
  const items: Tag[] = []
  let idx = 0
  for (const cat of CATEGORY_ORDER) {
    items.push(h.div(cat, { class: 'sfari-nav-cat' }))
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      const rec = fs[f.id] ?? {}
      const dot = rec.score !== null && rec.score !== undefined ? functionScoreBandColor(rec.score) : '#dfe4ec'
      items.push(h.div(h.span({ class: 'sfari-nav-dot', style: `background:${dot};` }), h.span(f.name), { 'data-idx': String(idx) },
        { class: 'sfari-nav-fn' + (idx === current ? ' active' : '') }))
      idx += 1
    }
  }
  return items
}

function bar(label: string, value: number, color: string, fmt: (v: number) => string = (v) => pyFixed(v, 2)): Tag {
  const pct = Math.max(0.0, Math.min(100.0, (value / 1.0) * 100))
  return h.div(h.div(label, { class: 'easi-bar-label' }),
    h.div(h.div({ class: 'easi-bar-fill', style: `width:${pyFixed(pct, 0)}%;background:${color};` }), { class: 'easi-bar-track' }),
    h.div(fmt(value), { class: 'easi-bar-val' }), { class: 'easi-bar-row' })
}

/** rollup_rail */
export function rollupRail(sc: Scored, fs: Record<string, FunctionRecord>, delta: Child = null): Child[] {
  const eci = sc.ecosystemConditionIndex
  const nScored = Object.values(fs).filter((v) => v.score !== null && v.score !== undefined).length
  const chips = CATEGORY_ORDER.map((cat) => {
    const lbl = sc.categoryLabels[cat]
    const val = lbl === undefined
      ? h.span('—', { class: 'val', style: 'background:#eef1f6;color:#8a93a3;' })
      : h.span(FNF_SHORT[lbl] ?? '—', { class: 'val', style: `background:${indexBandColor(sc.categorySubIndices[cat] ?? 0.0)};color:#33415c;` })
    return h.div(h.span(cat, { class: 'lab' }), val, { class: 'sfari-cat-chip' })
  })
  const sub = (label: string, key: string) => nScored === 0
    ? bar(label, 0.0, '#e7ebf1', () => '–')
    : bar(label, sc.subIndices[key], indexBandColor(sc.subIndices[key]))
  return [
    delta,
    h.div(h.div(nScored === 0 ? '–' : pyFixed(eci, 2), { class: 'sfari-eci' + (nScored === 0 ? ' empty' : '') }),
      h.div('Ecosystem Condition Index', { class: 'sfari-eci-lbl' }),
      nScored ? h.div(h.div({ class: 'sfari-eci-knob', style: `left:${pyFixed(Math.max(2.0, Math.min(98.0, eci * 100)), 1)}%;` }), { class: 'sfari-eci-track' }) : null,
      { class: 'sfari-eci-box' }),
    sub('Physical', 'physical'), sub('Chemical', 'chemical'), sub('Biological', 'biological'),
    h.h4('Functional categories', { style: 'margin-top:15px;' }),
    h.div(...chips, { class: 'sfari-cat-chips' }),
    h.div(h.span({ style: `width:${pyFixed((nScored / 20) * 100, 0)}%;` }), { class: 'sfari-progress-bar' }),
    h.div(`${nScored} / 20 functions scored`, { class: 'sfari-progress' }),
    h.button('Open report', { 'data-report': '1', type: 'button' }, { class: 'sfari-btn sfari-rollup-report' + (nScored === FN_IDS.length ? ' primary' : '') }),
  ]
}

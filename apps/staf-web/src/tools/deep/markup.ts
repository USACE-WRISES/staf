// DEEP's Assessment worksheet markup, as deep's app.py builds it with htmltools (rendered by htm.ts):
// the metric rows with their reference curves, the computed score card, the unassessed function's page,
// the function list and the rollup rail. The row buttons are the rows EASI, SFARI and DEEP share.
import { type Child, h, HTML, pyHtmlEscape, type Tag } from '../../app/htm'
import { pyFixed, pyFloat, pyG } from '../../app/pyfmt'
import { pyDumps } from '../../app/pyjson'
import { info, metricAction, metricActions, ROW_ICONS } from '../sfari/markup'
import {
  activePoints, type Bundle, CATEGORY_ORDER, type Coverage, coverageCaption, curveStrata, describe, documentedGap, domainWarning,
  enginePairingAdvisory, FUNCTION_SCORE_BANDS, functionScoreBandColor, FUNCTIONS_BY_ID, gapLine, INDEX_BAND_LABELS, indexBandColor,
  indexBandLabel, indexClaim, interpCurve, isFixed, isHeld, measuredFromState, type Metric, methodText, type Rec,
  type Scored, stratumLabel, unitsOf, withheldOnlyFunctions, withheldTitle,
} from './method'

export const STEP_LABELS: [string, string][] = [['identify', 'Identify'], ['basin', 'Basin'], ['measure', 'Assessment'], ['report', 'Report']]
const FNF_SHORT: Record<string, string> = { Functioning: 'F', 'Functioning-at-Risk': 'AR', 'Non-Functioning': 'NF' }

/** A Shiny ui output of DEEP's holding ``content``. */
export function output(id: string, content: Child = null, outputHtml: (c: Child) => string): Tag {
  return h.div({ class: 'shiny-html-output', id: `deep-${id}`, 'aria-live': 'polite' }, new HTML(outputHtml(content)))
}

/** _stepper: data-step anchors with tabindex (measure.js gives them Enter and Space). */
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
    return h.a(label, { 'data-step': key, role: 'button', tabindex: '0', 'aria-current': key === active ? 'step' : null }, { class: cls })
  })
  return h.div(...items, { class: 'easi-steps' })
}

// ------------------------------------------------------------------ the reference curve
const BAND_SHADE: [number, number, string][] = [[0.0, 0.39, '#f5b5b5'], [0.39, 0.69, '#f5e7a6'], [0.69, 1.0, '#c8d9f2']]
const f1 = (v: number) => pyFixed(v, 1)

/** _fmt_num: 35.0 -> '35', 1.250 -> '1.25'. */
function fmtNum(v: number): string {
  const f = Number(v)
  if (Number.isInteger(f)) return String(Math.trunc(f) === 0 ? 0 : Math.trunc(f))
  return pyFixed(f, 2).replace(/0+$/, '').replace(/\.$/, '')
}

/** _curve_svg: the labeled reference curve with the site's marker (measure.js moves it live). */
export function curveSvg(points: Rec[], value: number | null = null, xlabel = '', w = 320, hgt = 200): string {
  const pts = points.filter((p) => p && p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined)
    .map((p, i) => ({ x: Number(p.x), y: Number(p.y), i })).sort((a, b) => a.x - b.x || a.i - b.i)
  if (!pts.length) return ''
  const xs = pts.map((p) => p.x)
  const xmin = Math.min(...xs)
  const xmax = Math.max(...xs)
  const dx = (xmax - xmin) || 1.0
  const [ml, mr, mt, mb] = [46, 12, 12, 42]
  const x0 = ml
  const x1 = w - mr
  const y1 = hgt - mb
  const y0 = mt
  const sx = (x: number) => x0 + (x - xmin) / dx * (x1 - x0)
  const sy = (yv: number) => y1 - Math.max(0.0, Math.min(1.0, yv)) * (y1 - y0)
  const P = [`<svg viewBox="0 0 ${w} ${hgt}" xmlns="http://www.w3.org/2000/svg" class="deep-curve" `
    + `preserveAspectRatio="xMidYMid meet" style="width:100%;max-width:${w}px;height:auto;" `
    + `data-x0="${f1(x0)}" data-x1="${f1(x1)}" data-y0="${f1(y0)}" data-y1="${f1(y1)}" `
    + `data-xmin="${pyG(xmin)}" data-xmax="${pyG(xmax)}">`]
  for (const [lo, hi, col] of BAND_SHADE) {
    const yt = sy(hi)
    const yb = sy(lo)
    P.push(`<rect x="${f1(x0)}" y="${f1(yt)}" width="${f1(x1 - x0)}" height="${f1(yb - yt)}" fill="${col}" fill-opacity="0.4"/>`)
  }
  P.push(`<line x1="${f1(x0)}" y1="${f1(y0)}" x2="${f1(x0)}" y2="${f1(y1)}" stroke="#b7c0cf" stroke-width="1"/>`)
  for (const yy of [0.0, 0.3, 0.7, 1.0]) {
    const yp = sy(yy)
    P.push(`<line x1="${f1(x0)}" y1="${f1(yp)}" x2="${f1(x1)}" y2="${f1(yp)}" stroke="#ffffff" stroke-opacity="0.75" stroke-width="1"/>`)
    P.push(`<text x="${f1(x0 - 6)}" y="${f1(yp + 3)}" text-anchor="end" class="deep-curve-tick">${f1(yy)}</text>`)
  }
  P.push(`<line x1="${f1(x0)}" y1="${f1(y1)}" x2="${f1(x1)}" y2="${f1(y1)}" stroke="#b7c0cf" stroke-width="1"/>`)
  const distinct = new Set(xs)
  const tickXs = distinct.size > 6 ? [xmin, xmax] : xs
  const seen = new Set<number>()
  for (const xv of tickXs) {
    if (seen.has(xv)) continue
    seen.add(xv)
    const xp = sx(xv)
    P.push(`<line x1="${f1(xp)}" y1="${f1(y1)}" x2="${f1(xp)}" y2="${f1(y1 + 4)}" stroke="#b7c0cf" stroke-width="1"/>`)
    P.push(`<text x="${f1(xp)}" y="${f1(y1 + 15)}" text-anchor="middle" class="deep-curve-tick">${fmtNum(xv)}</text>`)
  }
  if (xlabel) P.push(`<text x="${f1((x0 + x1) / 2)}" y="${f1(hgt - 6)}" text-anchor="middle" class="deep-curve-axis">${pyHtmlEscape(xlabel)}</text>`)
  P.push(`<text transform="translate(13,${f1((y0 + y1) / 2)}) rotate(-90)" text-anchor="middle" class="deep-curve-axis">Index (0–1)</text>`)
  P.push(`<polyline points="${pts.map((p) => `${f1(sx(p.x))},${f1(sy(p.y))}`).join(' ')}" fill="none" stroke="#2f4b7c" stroke-width="2"/>`)
  let mxp: number
  let myp: number
  let hid: string
  if (value !== null) {
    const vx = Math.min(Math.max(Number(value), xmin), xmax)
    const iy = interpCurve(pts, vx) || 0.0
    mxp = sx(vx)
    myp = sy(iy)
    hid = ''
  } else {
    mxp = x0
    myp = y1
    hid = ' visibility="hidden"'
  }
  P.push(`<line class="deep-mk-v" x1="${f1(mxp)}" y1="${f1(y1)}" x2="${f1(mxp)}" y2="${f1(myp)}" stroke="#d6453d" stroke-width="1.3" stroke-dasharray="3 2"${hid}/>`)
  P.push(`<line class="deep-mk-h" x1="${f1(x0)}" y1="${f1(myp)}" x2="${f1(mxp)}" y2="${f1(myp)}" stroke="#d6453d" stroke-width="1" stroke-dasharray="3 2" stroke-opacity="0.6"${hid}/>`)
  P.push(`<circle class="deep-mk-dot" cx="${f1(mxp)}" cy="${f1(myp)}" r="4" fill="#d6453d"${hid}/>`)
  P.push('</svg>')
  return P.join('')
}

// ------------------------------------------------------------------ the metric row
/** _fmt_bound: four decimals at most, trailing zeros dropped. */
function fmtBound(v: number): string {
  const s = pyFixed(Number(pyFixed(Number(v), 4)), 4).replace(/0+$/, '').replace(/\.$/, '')
  return s === '' || s === '-0' ? '0' : s
}

/** _card_warning: (short, full, reference only). */
function cardWarning(m: Metric, rc: Rec | undefined): [string, string, boolean] {
  const r = rc ?? {}
  const mid = m.metricId
  if (r.na || r.value === null || r.value === undefined || r.value === '') return ['', '', false]
  const x = Number(r.value)
  if (Number.isNaN(x)) return ['', '', false]
  const mv = measuredFromState({ [mid]: r })[mid]
  const pairing = enginePairingAdvisory(mv, m)
  if (pairing) return ['Not scored: shown for reference only.', pairing, true]
  if (isFixed(m)) return ['', '', false]
  const pts = activePoints(m, r.stratum)
  const full = domainWarning(pts, x)
  if (!full) return ['', '', false]
  const xs = pts.filter((p) => p.x !== null && p.x !== undefined && p.y !== null && p.y !== undefined).map((p) => Number(p.x)).sort((a, b) => a - b)
  return [`Outside the curve's range (${fmtBound(xs[0])} to ${fmtBound(xs[xs.length - 1])}); scored at the nearest end.`, full, false]
}

/** _metric_tip_html: the metric's name and how to measure it. */
function metricTipHtml(m: Metric): string {
  const name = String(m.metricName ?? m.metricId ?? '')
  const parts = [`<div class="easi-tip-title">${pyHtmlEscape(name)}</div>`]
  const how = methodText(m)
  if (how) parts.push(`<div class="easi-tip-sec"><span class="easi-tip-lbl">How to measure</span>${pyHtmlEscape(how)}</div>`)
  else parts.push('<div class="easi-tip-sub">Field collection guidance has not been provided for this assessment yet.</div>')
  return parts.join('')
}

/** web.metric_check: a checkbox shaped like a row button (DEEP's N/A). */
function metricCheck(label: string, box: Tag, on: boolean, title: string, extra: string): Tag {
  return h.label(new HTML(ROW_ICONS.na), h.span(label, { class: 'staf-act-label' }), box, { title }, { class: 'staf-act' + (on ? ' on' : '') + (extra ? ` ${extra}` : '') })
}

/** str() of a stored value (a float as Python prints it). */
const valueText = (v: unknown): string => (typeof v === 'number' ? pyFloat(v) : String(v))

/** _metric_row: one metric of a function page. */
export function metricRow(m: Metric, rc: Rec | undefined, midx: number | null | undefined, stratumNote = ''): Tag {
  const mid = m.metricId
  const r = rc ?? {}
  const name = m.metricName ?? mid
  const val = r.value
  const na = !!r.na
  const note = r.note || ''
  const hasNote = !!note.trim()
  const photos: Rec[] = r.photos || []
  const hasVal = !na && val !== null && val !== undefined && val !== ''
  const strata = curveStrata(m)
  const cur = r.stratum || m.activeStratum || (strata.length ? strata[0] : null)
  const points = activePoints(m, cur)
  const units = unitsOf(m)
  const desc = describe(m, cur)
  const [short, full, refOnly] = cardWarning(m, r)
  let idxTxt: string
  let idxCol: string
  if (na) [idxTxt, idxCol] = ['N/A', '#eef1f6']
  else if (midx === null || midx === undefined) [idxTxt, idxCol] = ['—', '#eef1f6']
  else [idxTxt, idxCol] = [`${pyFixed(midx, 2)} · ${indexBandLabel(midx)}`, indexBandColor(midx)]
  const ev: Tag[] = []
  if (hasVal && r.origin === 'desktop') {
    ev.push(h.span('Desktop', { title: 'Filled in from desktop data. Type a value to replace it.' }, { class: 'staf-ev-tag deep-desktop-tag' }))
  }
  if (strata.length > 1) {
    const auto = !!r.stratumAuto
    ev.push(h.label(h.span('Curve set', { class: 'deep-stratum-label' }),
      h.select({ 'data-mid-stratum': mid, 'aria-label': `Curve set: ${name}`, ...(stratumNote ? { title: stratumNote } : {}) },
        ...strata.map((s) => h.option(stratumLabel(s, m) + (auto && s === cur ? ' (auto)' : ''), { value: s, ...(s === cur ? { selected: 'selected' } : {}) })),
        { class: 'deep-stratum-select' }),
      { class: 'deep-stratum' }))
  }
  const naBox = h.input({ type: 'checkbox', 'data-mid-na': mid, ...(na ? { checked: 'checked' } : {}) }, { class: 'deep-na' })
  const actions = metricActions(
    points.length ? metricAction('scoring', 'Scoring', { title: 'Show the reference curve' }) : null,
    metricAction('note', 'Note', { on: hasNote, has: hasNote, title: 'Add a note' }),
    metricAction('photo', 'Photo', { on: photos.length > 0, count: photos.length, title: 'Add a photo' }),
    metricCheck('N/A', naBox, na, 'Not applicable at this site', 'deep-na-toggle'))
  const valueBox = h.div(
    h.input({ type: 'number', step: 'any', inputmode: 'decimal', value: val === null || val === undefined || val === '' ? '' : valueText(val),
      'data-mid': mid, placeholder: 'Value', 'aria-label': name + (units ? ` (${units})` : ''), ...(na ? { disabled: 'disabled' } : {}) },
    { class: 'deep-metric-input' }),
    units ? h.span(units, { class: 'deep-unit' }) : null,
    { class: 'deep-value-box' })
  const photosRow = h.div(
    ...photos.map((p) => h.span(h.img({ src: p.uri ?? '' }, { class: 'sfari-thumb' }),
      h.button('×', { 'data-mid': mid, 'data-id': p.id, type: 'button' }, { class: 'sfari-photo-rm' }),
      { 'data-mid': mid, 'data-id': p.id }, { class: 'sfari-thumb-wrap' })),
    h.label(new HTML(ROW_ICONS.photo), 'Add photo',
      h.input({ type: 'file', accept: 'image/*', capture: 'environment', 'data-mid': mid }, { class: 'sfari-photo' }), { class: 'sfari-photo-btn' }),
    { 'data-mid': mid }, { class: 'sfari-photos' })
  const attrs: Record<string, string> = { 'data-metric': mid, 'data-points': pyDumps(points) }
  if (isFixed(m)) attrs['data-fixed'] = '1'
  if (refOnly) attrs['data-ref-only'] = '1'
  const rowCls = 'staf-metric deep-metric' + (hasNote ? ' show-note' : '') + (photos.length ? ' show-photo' : '')
  return h.div(
    h.div(
      h.div(h.span(name, { class: 'staf-metric-name' }), info(null, metricTipHtml(m)), { class: 'staf-metric-head' }),
      desc ? h.div(desc, { class: 'staf-metric-desc' }) : null,
      ev.length ? h.div(...ev, { class: 'staf-metric-ev deep-metric-sub' }) : null,
      h.div(short, { 'data-mid-warn': mid, role: 'status', ...(full ? { title: full } : {}), ...(short ? {} : { hidden: 'hidden' }) },
        { class: 'staf-metric-warn deep-domain-warn' }),
      actions,
      { class: 'staf-metric-main' }),
    h.div(valueBox, h.span(idxTxt, { 'data-mid-idx': mid }, { class: 'deep-metric-index', style: `background:${idxCol};` }), { class: 'staf-metric-input' }),
    points.length
      ? h.div(h.div(new HTML(curveSvg(points, hasVal ? Number(val) : null, m.xLabel ?? '')), { class: 'deep-plot-wrap' }), { 'data-panel': 'scoring' }, { class: 'staf-metric-panel' })
      : null,
    h.div(h.textarea(note, { 'data-mid-note': mid, placeholder: 'Note (optional)', 'aria-label': `Note: ${name}` }, { class: 'staf-metric-note' }),
      { 'data-panel': 'note' }, { class: 'staf-metric-panel' }),
    h.div(photosRow, { 'data-panel': 'photo' }, { class: 'staf-metric-panel' }),
    attrs, { class: rowCls })
}

export const isEntered = (rc: Rec | undefined): boolean => !!rc?.na || !(rc?.value === null || rc?.value === undefined || rc?.value === '')

function functionBandLabel(score: number): string {
  for (let i = 0; i < FUNCTION_SCORE_BANDS.length; i++) if (score <= FUNCTION_SCORE_BANDS[i][0]) return INDEX_BAND_LABELS[i]
  return INDEX_BAND_LABELS[INDEX_BAND_LABELS.length - 1]
}

/** _fn_head */
export function fnHead(eyebrow: string, name: string, pill: Child = null): Tag {
  return h.div(eyebrow ? h.div(eyebrow, { class: 'sfari-fn-eyebrow' }) : null, h.div(h.span(name), pill, { class: 'sfari-fn-name' }), { class: 'sfari-fn-head' })
}

export function secLbl(entered: number, total: number): Tag {
  return h.div(h.span('1', { class: 'sfari-step-num' }), h.span('Metrics', { class: 'sfari-sec-title' }),
    h.span(`${entered} of ${total} entered`, { class: 'sfari-sec-count' }), { class: 'sfari-sec-lbl' })
}

const FSCORE_TIP = '<div class="easi-tip-sec">Computed automatically: the mean of the metric indices above times 15.</div>'
  + '<div class="easi-tip-sec"><span class="easi-tip-lbl">Condition bands</span>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span><span><b>0 to 5:</b> Non-Functioning</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span><span><b>6 to 10:</b> Functioning-at-Risk</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span><span><b>11 to 15:</b> Functioning</span></div></div>'

/** _scorecard: the computed 0-15 score on a read-only gauge. */
export function scorecard(fid: string, score: number | null): Tag {
  const bandLbl = score !== null ? functionBandLabel(score) : 'Not scored yet'
  const bandCol = score !== null ? functionScoreBandColor(score) : '#e7ebf1'
  const knob = score === null ? '' : `left:${pyFixed(score / 15 * 100, 1)}%;`
  const statement = FUNCTIONS_BY_ID.get(fid)?.function_statement ?? ''
  return h.div(
    h.div(h.span('2', { class: 'sfari-step-num' }), h.span('Function score', { class: 'deep-fscore-lbl' }), info(null, FSCORE_TIP), { class: 'deep-fscore-head' }),
    statement ? h.p(statement, { class: 'sfari-fn-statement' }) : null,
    h.div(h.div(h.div({ style: knob }, { class: 'deep-fscore-knob' }), { class: 'deep-fscore-track' }),
      h.span(score === null ? '–' : pyFixed(score, 1), { class: 'deep-fscore-num' }),
      h.span(bandLbl, { class: 'deep-fscore-band', style: `background:${bandCol};` }), { class: 'deep-fscore-row' }),
    { 'data-fn': fid }, { class: 'deep-scorecard' + (score !== null ? '' : ' unset') })
}

/** _fn_footer: Previous, the entered count, Next. */
export function fnFooter(idx: number, n: number, counts: [number, number] | null = null): Tag {
  const status = counts ? h.span(`${counts[0]}/${counts[1]} entered`, { class: 'sfari-foot-rated' }) : null
  return h.div(h.div(
    h.div(h.button('‹ Previous', { 'data-nav': '-1', type: 'button', disabled: idx === 0 ? 'disabled' : null }, { class: 'sfari-btn' }), { class: 'sfari-foot-left' }),
    h.div(status, { class: 'sfari-foot-status' }),
    h.button(idx < n - 1 ? 'Next function ›' : 'Done', { 'data-nav': '1', type: 'button' }, { class: 'sfari-btn primary' }),
    { class: 'sfari-nav-actions' }), { class: 'sfari-fn-footer' })
}

/** _withheld_card: a withheld metric, named and marked Not scored. */
export function withheldCard(w: Rec): Tag {
  let statement = String(w.statement || 'No basis supports this metric in this ecoregion, so no curve was built and the metric is not scored.')
  for (const prefix of ['Insufficient reference support.', 'Held for review.']) {
    if (statement.startsWith(prefix)) statement = statement.slice(prefix.length)
    statement = statement.trim()
  }
  const tip = `<div class="easi-tip-title">${pyHtmlEscape(withheldTitle(w))}</div><div class="easi-tip-sec">${pyHtmlEscape(statement)}</div>`
  return h.div(
    h.div(h.div(h.span(w.metricName || w.metricId || '', { class: 'staf-metric-name' }), info(null, tip), { class: 'staf-metric-head' }), { class: 'staf-metric-main' }),
    h.div(h.span('Not scored', { class: 'deep-notscored-tag' }), { class: 'staf-metric-input' }),
    { 'data-metric-withheld': String(w.metricId || '') }, { class: 'staf-metric deep-metric-withheld' })
}

/** _unassessed_panel: a function the assessment cannot score. */
export function unassessedPanel(fn: Rec, bundle: Bundle | null, eyebrow = ''): Tag {
  const name = fn.functionName || fn.functionId
  const detail: Rec[] = fn.unassessed?.metrics || []
  const rows: Tag[] = detail.map(withheldCard)
  const gap = bundle ? documentedGap(bundle, fn.functionId) : null
  let lead: string
  if (gap) {
    lead = gapLine(gap) + '.'
    const who = String(gap.recordedBy || '').trim()
    const when = String(gap.recordedAt || '').slice(0, 10)
    rows.unshift(h.div(h.div(String(gap.justification || ''), { class: 'deep-gap-text' }),
      who ? h.div('Recorded by ' + who + (when ? ` on ${when}` : '') + '.', { class: 'deep-gap-who' }) : null,
      { 'data-function-gap': String(gap.reason || '') }, { class: 'deep-gap' }))
  } else if (detail.length && detail.every(isHeld)) {
    lead = 'Its candidate curves are held for review, so this function carries no score.'
  } else {
    lead = 'This assessment has no defensible basis for scoring this function, so it carries no score.'
  }
  const list = rows.length ? rows : [h.div('No metric is assigned to this function in this assessment.', { class: 'deep-gap deep-gap-text' })]
  return h.div(
    fnHead(eyebrow, name, h.span('Not assessed', { class: 'deep-fn-pill' })),
    h.p(lead + ' It does not count as a low score.', { class: 'deep-fn-lead' }),
    h.div(...list, { class: 'sfari-ev-card deep-unassessed-card' }),
    { 'data-fn': fn.functionId, 'data-unassessed': '1' }, { class: 'deep-fn-unassessed' })
}

/** _withheld_note: the rail's note naming the functions scored nothing because every metric was withheld. */
function withheldNote(bundle: Bundle | null): Tag | null {
  const fns = withheldOnlyFunctions(bundle)
  if (!fns.length) return null
  const names = fns.map((f) => String(f.functionName || f.functionId)).join(', ')
  const held = fns.flatMap((f) => (f.metrics || []).map(isHeld))
  const lead = !held.some(Boolean) ? 'Not scored for insufficient reference support'
    : held.every(Boolean) ? 'Not scored while their curves are held for review' : 'Not scored'
  return h.div(`${lead}: ${names}.`, { class: 'sfari-coverage-note deep-withheld-note' })
}

/** fn_nav: the walk's functions under their disciplines. */
export function fnNav(walk: Rec[], current: number, results: Record<string, { score: number | null }>): Child[] {
  const items: Tag[] = []
  let prev: string | null = null
  walk.forEach((fn, idx) => {
    const disc = fn.discipline ?? ''
    if (disc !== prev) {
      items.push(h.div(disc, { class: 'sfari-nav-cat' }))
      prev = disc
    }
    const fr = results[fn.functionId]
    const dot = fr && fr.score !== null && fr.score !== undefined ? functionScoreBandColor(fr.score) : '#dfe4ec'
    items.push(h.div(h.span({ class: 'sfari-nav-dot', style: `background:${dot};` }), h.span(fn.functionName ?? ''), { 'data-idx': String(idx) },
      { class: 'sfari-nav-fn' + (idx === current ? ' active' : '') + (fn.unassessed ? ' unassessed' : '') }))
  })
  return items
}

function bar(label: string, value: number | null, color: string): Tag {
  const pct = value === null ? 0.0 : Math.max(0.0, Math.min(100.0, (value / 1.0) * 100))
  return h.div(h.div(label, { class: 'easi-bar-label' }),
    h.div(h.div({ class: 'easi-bar-fill', style: `width:${pyFixed(pct, 0)}%;background:${color};` }), { class: 'easi-bar-track' }),
    h.div(value === null ? '' : pyFixed(value, 2), { class: 'easi-bar-val' }), { class: 'easi-bar-row' })
}

/** _outcome_bar: an outcome's sub-index, or its marked absence. */
function outcomeBar(label: string, value: number | null): Tag {
  if (value === null) return bar(`${label} (not assessed)`, null, '#eef1f6')
  return bar(label, value, indexBandColor(value))
}

/** rollup_rail */
export function rollupRail(sc: Scored, bundle: Bundle | null, delta: Child, coverage: Coverage): Child[] {
  const eci = sc.ecosystemConditionIndex
  const subs = sc.subIndices
  const cats = sc.categoryLabels
  const catvals = sc.categorySubIndices
  const fns: Rec[] = bundle?.metricsByFunction ?? []
  const fscores = sc.functionScores ?? {}
  const nTotal = fns.length
  const nScored = fns.filter((fn) => fn.functionId in fscores).length
  const chips = CATEGORY_ORDER.map((cat) => {
    const lbl = cats[cat]
    const val = lbl === undefined
      ? h.span('—', { class: 'val', style: 'background:#eef1f6;color:#8a93a3;' })
      : h.span(FNF_SHORT[lbl] ?? '—', { class: 'val', style: `background:${indexBandColor(catvals[cat] ?? 0.0)};color:#33415c;` })
    return h.div(h.span(cat, { class: 'lab' }), val, { class: 'sfari-cat-chip' })
  })
  const pct = nTotal ? (nScored / nTotal * 100) : 0
  const caption = coverageCaption(coverage)
  return [
    delta,
    h.h4('Live rollup'),
    h.div(h.div(eci !== null && eci !== undefined ? pyFixed(eci, 2) : pyFixed(sc.ecosystemConditionIndexOverScored, 2), { class: 'sfari-eci' }),
      h.div(eci !== null && eci !== undefined ? 'Ecosystem Condition Index' : 'Score so far', { class: 'sfari-eci-lbl' }), { class: 'sfari-eci-box' }),
    eci !== null && eci !== undefined ? null : h.div(indexClaim(sc), { class: 'sfari-coverage-note' }),
    outcomeBar('Physical', subs.physical), outcomeBar('Chemical', subs.chemical), outcomeBar('Biological', subs.biological),
    h.h4('Functional categories', { style: 'margin-top:15px;' }),
    h.div(...chips, { class: 'sfari-cat-chips' }),
    h.div(h.span({ style: `width:${pyFixed(pct, 0)}%;` }), { class: 'sfari-progress-bar' }),
    h.div(`${nScored} / ${nTotal} functions scored`, { class: 'sfari-progress' }),
    caption ? h.div(caption, { class: 'sfari-coverage-note' }) : null,
    withheldNote(bundle),
    h.button('Open report', { 'data-report': '1', type: 'button' }, { class: 'sfari-btn sfari-rollup-report' + (nTotal && nScored === nTotal ? ' primary' : '') }),
  ]
}


// EASI's Assessment worksheet markup, as easi/app.py builds it with htmltools (rendered by htm.ts):
// the stepper, the function list, a function's metric card (the row design EASI, SFARI and DEEP share)
// with its observed entries and Scoring panel, the score card and the rollup rail.
import { type Child, h, HTML, outputHtml, pyHtmlEscape, type Tag, TagList } from '../../app/htm'
import { pyFixed, pyG } from '../../app/pyfmt'
import { info } from '../sfari/markup'
import { BANK_EROSION_ID, CHANNEL_EVOL_ID } from './adapters'
import { methodFor } from './evaluator'
import { FUNCTION_SCORE_MAX, RATINGS } from './method'
import type { ScoringMethod } from './methods'
import { isBorrowed } from './notices'
import { functionScore, functionScoreBandColor, indexBandColor, indexBandLabel, ratingToIndex } from './scoring'

export const STEP_LABELS: [string, string][] = [['identify', 'Identify'], ['basin', 'Basin'], ['assess', 'Assessment'], ['report', 'Report']]

/** A Shiny ui output of EASI's (``ui.output_ui(id)`` once rendered). */
export function output(id: string, content: Child = null, cls = ''): Tag {
  return h.div({ class: 'shiny-html-output' + (cls ? ` ${cls}` : ''), id: `easi-${id}`, 'aria-live': 'polite' }, new HTML(outputHtml(content)))
}

/** _stepper: plain data-step links (report-ready.js makes the Report link a keyboard control). */
export function stepper(active: string): Tag {
  let done = true
  const items = STEP_LABELS.map(([key, label]) => {
    let cls = 'easi-step'
    if (key === active) {
      cls += ' active'
      done = false
    } else if (done) cls += ' done'
    return h.a(label, { 'data-step': key }, { class: cls })
  })
  return h.div(...items, { class: 'easi-steps' })
}

/** fn_nav: the functions by category, each dot in its function score's band. */
export function fnNav(functions: any[], scores: Record<string, number>, current: number): Child[] {
  const items: Tag[] = []
  let prev: string | null = null
  functions.forEach((fn, i) => {
    const disc = fn.category ?? ''
    if (disc !== prev) {
      items.push(h.div(disc, { class: 'sfari-nav-cat' }))
      prev = disc
    }
    const fs = scores[fn.id]
    const dot = fs !== undefined && fs !== null ? functionScoreBandColor(fs) : '#dfe4ec'
    items.push(h.div(h.span({ class: 'sfari-nav-dot', style: `background:${dot};` }), h.span(fn.name ?? ''), { 'data-idx': String(i) },
      { class: 'sfari-nav-fn' + (i === current ? ' active' : '') }))
  })
  return items
}

/** _rate_select: the override select, the right column of the metric row. */
export function rateSelect(mid: string, r: Record<string, any>): Tag {
  const eff = r.rating
  const opts: Tag[] = []
  if (!RATINGS.includes(eff)) opts.push(h.option('—', { value: '', selected: 'selected', disabled: 'disabled' }))
  for (const rt of RATINGS) opts.push(rt === eff ? h.option(rt, { value: rt, selected: 'selected' }) : h.option(rt, { value: rt }))
  const attrs: Record<string, string> = { class: 'staf-rate easi-rate-sel' + (eff ? ' set' : ''), 'data-mid': mid, title: 'Click to override rating' }
  if (r.status === 'observed') Object.assign(attrs, { disabled: 'disabled', title: 'Rated from your observation. Clear it to set a rating by hand.' })
  return h.select(...opts, attrs)
}

/** _metric_about_tip: how the metric is measured (the catalog's howToMeasure). */
export function metricAboutTip(meta: Record<string, any>): string {
  const how = String(meta?.howToMeasure ?? '').split(/\s+/).filter(Boolean).join(' ')
  if (!how) return ''
  return `<div class="easi-tip-title">${pyHtmlEscape(String(meta.name ?? ''))}</div>`
    + `<div class="easi-tip-sec"><span class="easi-tip-lbl">How to measure</span>${pyHtmlEscape(how)}</div>`
}

// ---------------------------------------------------------------- observed evidence

export const OBSERVED_INPUTS: Record<string, string[]> = {
  [CHANNEL_EVOL_ID]: ['stageClass', 'indicators'],
  [BANK_EROSION_ID]: ['erodingBankPct', 'armoredBankPct'],
}

/** _observed_value: a posted entry as the engine takes it, or null to clear it. */
export function observedValue(mid: string, key: string, value: any): any {
  if (!(OBSERVED_INPUTS[mid] ?? []).includes(key)) return null
  if (key === 'stageClass') return RATINGS.includes(value) ? value : null
  if (key === 'indicators') return String(value ?? '').split(/\s+/).filter(Boolean).join(' ').slice(0, 500) || null
  const text = String(value ?? '').trim()
  const n = text === '' ? NaN : Number(text)
  return Number.isFinite(n) && n >= 0.0 && n <= 100.0 ? n : null
}

const observedCriteria = (mid: string): Record<string, string> => ((methodFor(mid) || {}).fieldReference || {}).criteria || {}

/** _observed_editor: the optional observed-evidence block of the two metrics that take one. */
export function observedEditor(mid: string, current: Record<string, any> | null | undefined): Tag | null {
  if (!(mid in OBSERVED_INPUTS)) return null
  const cur = current || {}
  const criteria = observedCriteria(mid)
  if (mid === CHANNEL_EVOL_ID) {
    const options: Tag[] = [h.option('No observation', { value: '' })]
    for (const rating of RATINGS) {
      const text = `${rating}: ${criteria[rating] ?? ''}`.replace(/[: ]+$/, '')
      options.push(h.option(text, { value: rating, ...(cur.stageClass === rating ? { selected: 'selected' } : {}) }))
    }
    return h.div(
      h.div('Observed channel condition ', h.span('(optional)', { class: 'easi-obs-opt' }), { class: 'easi-obs-title' }),
      h.div('A documented field visit or geomorphic report replaces the automatic rating. Both entries are needed.', { class: 'easi-obs-help' }),
      h.label('Observed class', h.select(...options, { class: 'easi-obs-in', 'data-mid': mid, 'data-key': 'stageClass' }), { class: 'easi-obs-field' }),
      h.label('Observed indicators', h.textarea(cur.indicators || '', {
        class: 'easi-obs-in', 'data-mid': mid, 'data-key': 'indicators', rows: '2', maxlength: '500',
        placeholder: 'Headcuts, bars, width or depth change, bank erosion, recovery features, with the source and date',
      }), { class: 'easi-obs-field' }),
      { class: 'easi-obs' })
  }
  const percent = (key: string, label: string) => {
    const value = cur[key]
    return h.label(label, h.input({ class: 'easi-obs-in', 'data-mid': mid, 'data-key': key, type: 'number', min: '0', max: '100', step: '1',
      value: value === null || value === undefined ? '' : pyG(Number(value)) }), { class: 'easi-obs-field easi-obs-num' })
  }
  return h.div(
    h.div('Observed bank condition ', h.span('(optional)', { class: 'easi-obs-opt' }), { class: 'easi-obs-title' }),
    h.div('Field or verified-imagery estimates replace the automatic rating. Both entries are needed, and the worse one governs.', { class: 'easi-obs-help' }),
    h.div(percent('erodingBankPct', 'Eroding bank (%)'), percent('armoredBankPct', 'Armored bank (%)'), { class: 'easi-obs-row' }),
    h.div(...RATINGS.filter((r) => criteria[r]).map((r) => h.div(h.b(r + ': '), criteria[r])), { class: 'easi-obs-crit' }),
    { class: 'easi-obs' })
}

/** _observed_hint: what the card says about the observed entries. */
export function observedHint(mid: string, entries: Record<string, any> | null | undefined, row: Record<string, any> | null | undefined): string | null {
  if (!(mid in OBSERVED_INPUTS)) return null
  if ((row || {}).status === 'observed') {
    const proxy = ((row as any).proxyResult || {}).rating || 'not rated'
    return `Rated from your observation. The automatic rating was ${proxy}.`
  }
  if (!entries || !Object.keys(entries).length) return null
  if (mid === CHANNEL_EVOL_ID) {
    return entries.stageClass ? 'Add the observed indicators. The class applies only with them.'
      : 'Pick the observed class. The indicators alone do not change the rating.'
  }
  return 'Enter both percentages. The observation applies only with both.'
}

// ---------------------------------------------------------------- the metric row

/** _borrowed_metric_note: source context beside a routed site's borrowed value. */
export function borrowedMetricNote(row: Record<string, any>): Tag | null {
  if (!isBorrowed(row)) return null
  return h.div('Desktop evidence comes from the nearest StreamCat reach downstream.', info(row.anchorNote), { class: 'staf-metric-warn' })
}

/** fn_metric_live: the desktop evidence, the restore chip and the observed hint. */
export function metricLive(mid: string, row: Record<string, any>, observedEntries: Record<string, any> | null | undefined): Tag {
  const gen = row.generatedRating
  const isObserved = row.status === 'observed'
  const chip = gen && row.rating !== gen && !isObserved
    ? h.span('Desktop rating: ', h.b(gen),
      h.button('Use', { 'data-suggest': mid, type: 'button', title: `Restore the desktop rating (${gen})` }, { class: 'staf-suggest-use' }),
      { class: 'staf-suggest' })
    : null
  const hint = observedHint(mid, observedEntries, row)
  return h.div(
    h.div(h.span(isObserved ? 'observed' : 'desktop', { class: 'staf-ev-tag' + (isObserved ? ' observed' : '') }),
      h.b(row.valueText || '—', { class: 'staf-ev-val' }), chip, { class: 'staf-metric-ev' }),
    borrowedMetricNote(row),
    hint ? h.div(hint, { class: 'easi-obs-hint' + (isObserved ? ' applied' : '') }) : null,
    { class: 'easi-metric-live' })
}

/** _fmt_input */
function fmtInput(v: any, integer: boolean): string {
  if (v === null || v === undefined) return '—'
  const f = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : NaN
  if (Number.isNaN(f)) return typeof v === 'string' ? v : String(v)
  if (integer || f === Math.trunc(f)) return String(Math.round(f) === f ? f : roundHalfEven(f))
  return pyG(f)
}
const roundHalfEven = (x: number) => {
  const fl = Math.floor(x)
  const d = x - fl
  return d > 0.5 ? fl + 1 : d < 0.5 ? fl : fl % 2 === 0 ? fl : fl + 1
}

/** _method_inputs_ui */
function methodInputs(method: ScoringMethod, siteInputs: Record<string, any>): Tag {
  const rows = method.inputs.map((inp) => {
    const sv = siteInputs[inp.key]
    const val = fmtInput(sv, inp.integer) + (inp.unit && sv !== null && sv !== undefined ? ` ${inp.unit}` : '')
    return h.div(
      h.div(h.span(inp.label, { class: 'easi-method-input-label' }), inp.contextOnly ? h.span('context', { class: 'easi-method-input-context' }) : null,
        { class: 'easi-method-input-name' }),
      h.span(val, { class: 'easi-method-input-value' }),
      inp.sourceLabel ? h.span(inp.sourceLabel, { class: 'easi-method-input-source' }) : null,
      { class: 'easi-method-input-row' })
  })
  return h.div(h.div('Inputs used', { class: 'easi-method-inputs-title' }), ...rows, { class: 'easi-method-inputs' })
}

/** method_plot.decision_html */
function decisionHtml(method: ScoringMethod, siteRating: string | null): string {
  const rows = method.decisions.map((d) => {
    const on = d.rating === siteRating
    const dot = `<span class="easi-tip-dot ${d.rating.toLowerCase()}"></span>`
    const here = on ? '<span class="easi-method-decide-here">this reach</span>' : ''
    return `<div class="easi-method-decide-row${on ? ' on' : ''}">${dot}<span class="easi-method-decide-cat">${pyHtmlEscape(d.label)}</span>`
      + `<span class="easi-method-decide-rate">${pyHtmlEscape(d.rating)}</span>${here}</div>`
  })
  return `<div class="easi-method-decide">${rows.join('')}</div>`
}

/** _method_body_ui: the inputs and the equation, or a categorical method's decision table. */
export function methodBody(method: ScoringMethod, row: Record<string, any>, siteInputs: Record<string, any>): TagList {
  const parts: Child[] = method.mode === 'categorical' ? [new HTML(decisionHtml(method, row.generatedRating ?? null))] : [methodInputs(method, siteInputs)]
  if (method.equation && method.mode !== 'categorical') {
    parts.push(h.div(h.span('Equation', { class: 'easi-method-equation-label' }), h.code(method.equation), { class: 'easi-method-equation' }))
  }
  if (method.reference) parts.push(h.p(method.reference, { class: 'easi-method-equation' }))
  return new TagList(...parts)
}

/** _method_criteria_ui: the Good/Fair/Poor criteria (and the field reference) of the row's method. */
export function methodCriteria(row: Record<string, any>, metricCriteria: Record<string, string> | null): Tag | null {
  const criteria = row.methodCriteria || {}
  const automated: any[] = criteria.automated || []
  const blocks: Tag[] = []
  const crow = (b: string, text: string, rng?: string) => h.div(h.span({ class: `easi-tip-dot ${b.toLowerCase()}` }), h.b(b),
    rng ? h.span(rng, { class: 'easi-method-crit-range' }) : null, h.span(text, { class: 'easi-method-crit-text' }), { class: 'easi-method-crit-row' })
  if (automated.length === 1) {
    const bands = automated[0].bands || {}
    const field = (criteria.fieldReference || {}).criteria || {}
    const rows = RATINGS.filter((b) => bands[b]).map((b) => crow(b, field[b] ?? '', bands[b]))
    if (rows.length) blocks.push(h.div(...rows))
  } else {
    for (const block of automated) {
      const bands = block.bands || {}
      const rows = RATINGS.filter((b) => bands[b]).map((b) => crow(b, '', bands[b]))
      if (rows.length) {
        const unit = block.units ? ` (${block.units})` : ''
        blocks.push(h.div(h.div(`${block.label ?? ''}${unit}`, { class: 'easi-method-crit-sub' }), ...rows))
      }
    }
    const field = (criteria.fieldReference || {}).criteria || {}
    if (Object.keys(field).length) {
      const title = (criteria.fieldReference || {}).title || 'Field reference'
      blocks.push(h.div(h.div(title, { class: 'easi-method-crit-sub' }), ...RATINGS.filter((b) => field[b]).map((b) => crow(b, field[b]))))
    }
  }
  if (!blocks.length) {
    const crit = (row.criteriaBands && Object.keys(row.criteriaBands).length ? row.criteriaBands : metricCriteria) || {}
    const rows = RATINGS.filter((b) => crit[b]).map((b) => crow(b, crit[b]))
    if (!rows.length) return null
    blocks.push(h.div(...rows))
  }
  return h.div(...blocks, { class: 'easi-method-crit' })
}

// ---------------------------------------------------------------- the score card and the rail

const FSCORE_TIP = '<div class="easi-tip-sec">Calculated automatically from the metric rating; EASI derives the 0-15 score, so there is no manual scoring.</div>'
  + '<div class="easi-tip-sec">'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot poor"></span><span><b>0-5:</b> Non-Functioning (Poor rating)</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot fair"></span><span><b>6-10:</b> Functioning-at-Risk (Fair rating)</span></div>'
  + '<div class="easi-tip-crit"><span class="easi-tip-dot good"></span><span><b>11-15:</b> Functioning (Good rating)</span></div>'
  + '</div>'

/** fn_scorecard: the derived 0-15 score, its band and the "(auto: N)" cue of an override. */
export function scorecard(row: Record<string, any>, meta: Record<string, any>, fn: Record<string, any>): Tag {
  const fs = row.functionScore
  let bandLbl: string
  let bandCol: string
  let knob: number | null
  if (fs === null || fs === undefined) [bandLbl, bandCol, knob] = ['Not scored yet', '#e7ebf1', null]
  else {
    bandLbl = indexBandLabel(fs / FUNCTION_SCORE_MAX)
    bandCol = functionScoreBandColor(fs)
    knob = Math.max(0.0, Math.min(100.0, (fs / FUNCTION_SCORE_MAX) * 100))
  }
  let cue: Tag | null = null
  if (row.status === 'override' && RATINGS.includes(row.generatedRating)) {
    cue = h.span(`(auto: ${functionScore(ratingToIndex(row.generatedRating, meta?.indexMidpoints))})`, { class: 'easi-auto-cue' })
  }
  const stmt = meta?.functionStatement || fn?.function_statement || ''
  return h.div(
    h.div(h.span('2', { class: 'sfari-step-num' }), h.span('Function score', { class: 'easi-fscore-lbl' }), info(null, FSCORE_TIP), { class: 'easi-fscore-head' }),
    stmt ? h.p(stmt, { class: 'sfari-fn-statement' }) : null,
    h.div(
      h.div(h.div({ style: knob === null ? '' : `left:${pyFixed(knob, 1)}%;` }, { class: 'easi-fscore-knob' }), { class: 'easi-fscore-track' }),
      h.span(fs === null || fs === undefined ? '–' : String(fs), { class: 'easi-fscore-num' }),
      h.span(bandLbl, { class: 'easi-fscore-band', style: `background:${bandCol};` }),
      cue, { class: 'easi-fscore-row' }),
    { class: 'easi-scorecard' + (fs !== null && fs !== undefined ? '' : ' unset') })
}

/** _bar: one horizontal bar (label, track, value). */
export function bar(label: string, value: number | null, color: string, { vmax = 1.0, fmt = (v: number) => pyFixed(v, 2), indent = false } = {}): Tag {
  const pct = value === null ? 0.0 : Math.max(0.0, Math.min(1.0, value / vmax)) * 100
  return h.div(h.div(label, { class: 'easi-bar-label' }),
    h.div(h.div({ class: 'easi-bar-fill', style: `width:${pyFixed(pct, 1)}%;background:${color};` }), { class: 'easi-bar-track' }),
    h.div(value === null ? '—' : fmt(value), { class: 'easi-bar-val' }),
    { class: 'easi-bar-row' + (indent ? ' indent' : '') })
}

/** rollup_rail */
export function rollupRail(sc: Record<string, any>, delta: Child): TagList {
  const eci = sc.ecosystemConditionIndex
  const sub = sc.subIndices
  const rows = sc.metricRows
  const nTotal = rows.length
  const nScored = rows.filter((r: any) => r.rating).length
  const knob = Math.max(2.0, Math.min(98.0, (eci || 0.0) * 100))
  const primary = Boolean(nTotal) && nScored === nTotal
  const pct = nTotal ? (nScored / nTotal) * 100 : 0
  return new TagList(
    delta,
    h.div(h.div(eci === null || eci === undefined ? '–' : pyFixed(eci, 2), { class: 'sfari-eci' }),
      h.div('Ecosystem Condition Index', { class: 'sfari-eci-lbl' }),
      h.div(h.div({ class: 'sfari-eci-knob', style: `left:${pyFixed(knob, 1)}%;` }), { class: 'sfari-eci-track' }),
      { class: 'sfari-eci-box' }),
    bar('Physical', sub.physical, indexBandColor(sub.physical)),
    bar('Chemical', sub.chemical, indexBandColor(sub.chemical)),
    bar('Biological', sub.biological, indexBandColor(sub.biological)),
    h.div(h.span({ style: `width:${pyFixed(pct, 0)}%;` }), { class: 'sfari-progress-bar' }),
    h.div(`${nScored} / ${nTotal} functions computed`, { class: 'sfari-progress' }),
    h.button('Open report', { 'data-report': '1', type: 'button' }, { class: 'sfari-btn sfari-rollup-report' + (primary ? ' primary' : '') }),
  )
}

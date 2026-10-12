// EASI's cross-section card (app.py _xs_editor, xs_summary, xs_selector, xs_reach_table and the plot
// widget) over the published grid, and the report's cross-section (assessment._build_cross_section).
//
// A reach's sections are the grid's nearest the nine reach positions (engine/xsreach.ts) and carry the
// grid's numbers. A section's elevation profile is drawn when the release stores it (the median section
// of its segment, staf-xs-current) or once the assessor pulls it from USGS 3DEP with the card's button:
// a pull is never automatic (the owner, 2026-10-11). A profile is processed as the grid build processed
// it (balanced_profile, simplify_profile, summarize_profile on the section's regional inputs), so its
// stages and ratios are the grid's own.
import { type Child, h, pyHtmlEscape, type Tag, TagList } from '../../app/htm'
import { pyFixed, pyFixedComma, pyFloat, pyG } from '../../app/pyfmt'
import { COEF, DIV_NAME } from '../../engine/bieger'
import { balancedProfile, deriveFromStages, flowArea, fmtBhr, FT_PER_M, simplifyProfile, summarizeProfile } from '../../engine/geomorph'
import { actionButton } from '../../shared/shinyui'
import { stations as linspace } from '../../xs/codec'
import { FLAGS, medianProfile, sectionDetails, sectionPulls, type XsSection } from '../../xs/grid'
import { pullExact } from '../../xs/sample'
import { info } from '../sfari/markup'
import { output } from './markup'
import type { EasiAssessData, XsPicked } from './prefetch'
import { pyRoundInt } from './scoring'
import type { XsImageSpec } from './xsimage'
import { type Figure, figure, plotly } from './xsplot'

type Obj = Record<string, any>
const AREA_R2: Record<string, number> = { LUP: 0.50, APL: 0.84, AHI: 0.90, IPL: 0.65, IHI: 0.55, RMS: 0.74, IMP: 0.64, PMS: 0.66, USA: 0.58 }
const AREA_ORDER = ['LUP', 'APL', 'AHI', 'IPL', 'IHI', 'RMS', 'IMP', 'PMS', 'USA']
const round2 = (x: number) => Number(pyFixed(x, 2))

// ---------------------------------------------------------------- the sections and their profiles

const details = new Map<string, Promise<XsSection[]>>()
function sectionsOf(d: EasiAssessData, vpu: string, id: number): Promise<XsSection[]> {
  const key = `${vpu}:${id}`
  let got = details.get(key)
  if (!got) {
    got = sectionDetails(d.src, vpu, BigInt(id))
    got.catch(() => details.delete(key))
    details.set(key, got)
  }
  return got
}

/** derive.metrics' chain on one transect (``n_pts`` float32 samples across ``wide`` either side):
 * the summarize_profile record, or null when the transect cannot be scored. */
function summarized(z: Float32Array, sec: XsSection, res: number): Obj | null {
  const ts = linspace(sec.wide, sec.nPts)
  const st: number[] = []
  const el: number[] = []
  for (let i = 0; i < z.length; i++) {
    if (Number.isFinite(z[i])) {
      st.push(ts[i])
      el.push(z[i])
    }
  }
  if (st.length < 7) return null
  const bal = balancedProfile(st, el)
  if (!bal) return null
  const [s2, e2] = simplifyProfile(bal[0], bal[1])
  return summarizeProfile(s2, e2, sec.daSqkm || 1.0, { bankfull: [sec.rcWidth, sec.rcDepth], bankfullAreaM2: sec.rcArea,
    division: sec.division, demResM: res })
}

/** _xsection_geom_block's fields from a summarized profile (null when it has no bankfull depth). */
function profileFields(s: Obj): Obj | null {
  const dBf = s.bankfull_depth_m
  const thalweg = s.thalweg
  if (!s.profile || thalweg === null || thalweg === undefined || !dBf) return null
  const fp = s.fp_stage_m || thalweg + 2.0 * dBf
  let lb = s.low_bank_stage_m
  if (lb === null || lb === undefined) lb = Math.min(Math.max(s.top_of_bank_m || fp, thalweg + dBf), fp)
  return {
    stations: [...s.profile.stations], elevs: [...s.profile.elevs], thalweg,
    bankfull_stage: s.bankfull_stage_m ?? thalweg + dBf, floodplain_stage: lb,
    low_bank_capped: Boolean(s.low_bank_capped), bankfull_width_m: s.bankfull_width_m, bankfull_depth_m: s.bankfull_depth_m,
    flood_prone_width_m: s.flood_prone_width_m, entrenchment_ratio: s.entrenchment_ratio, bank_height_ratio: s.bank_height_ratio,
    edge_limited: s.edge_limited, bankfull_area_m2: s.bankfull_area_m2 ?? null, bankfull_area_edge_limited: s.bankfull_area_edge_limited ?? null,
    division: s.bankfull_division ?? null,
  }
}

/** Whether a section has its profile (stored or pulled): it can then be drawn and its heights edited. */
export const hasProfile = (b: Obj | null | undefined): boolean => Boolean(b && b.stations && b.thalweg !== null && b.thalweg !== undefined)

/** The report's cross-section (assessment._build_cross_section over the grid): a block per section with
 * the grid's numbers, the stored profiles in place, the default one (nearest both medians) selected. */
export async function buildCrossSection(d: EasiAssessData, geom: Obj, xs: XsPicked | undefined, slope: any, fcode: any): Promise<Obj | null> {
  const cands: Obj[] = geom?.candidate_scalars ?? []
  const picked = xs?.picked ?? []
  if (!xs || !cands.length || picked.length !== cands.length) return null
  const res = geom.dem_resolution_m ?? null
  const source = res ? `USGS 3DEP ${res} m DEM` : 'USGS 3DEP DEM'
  const stored = new Map<string, Promise<any>>()
  // each line's section details and stored profile read at once (a block after another, each waited on
  // the last's requests); the blocks below take them in order
  for (const p of picked) {
    const key = `${p.vpu}:${p.id}`
    if (stored.has(key)) continue
    sectionsOf(d, p.vpu, p.id).catch(() => {})
    stored.set(key, medianProfile(d.src, p.vpu, BigInt(p.id)).catch(() => null))
  }
  const blocks: Obj[] = []
  for (let i = 0; i < cands.length; i++) {
    const c = cands[i]
    const p = picked[i]
    const secs = await sectionsOf(d, p.vpu, p.id)
    const sec = secs.find((s) => s.part === p.part && s.k === p.k)
    const stationFt = pyRoundInt(Number(c.position_frac) * xs.lengthM * FT_PER_M)
    let block: Obj = {
      stations: null, elevs: null, thalweg: null, slope: slope ?? null, bankfull_stage: null, floodplain_stage: null,
      low_bank_capped: Boolean(c.low_bank_capped), bankfull_width_m: c.bankfull_width_m, bankfull_depth_m: c.bankfull_depth_m,
      flood_prone_width_m: c.flood_prone_width_m, entrenchment_ratio: c.entrenchment_ratio, bank_height_ratio: c.bank_height_ratio,
      edge_limited: Boolean(c.edge_limited), bankfull_area_m2: sec ? sec.rcArea : null,
      bankfull_area_edge_limited: Boolean(p.flags & FLAGS.bankfullAreaEdgeLimited), division: sec?.division ?? null, fcode: fcode ?? null,
    }
    const key = `${p.vpu}:${p.id}`
    if (!stored.has(key)) stored.set(key, medianProfile(d.src, p.vpu, BigInt(p.id)).catch(() => null))
    const m = await stored.get(key)
    if (m && sec && m.part === p.part && m.k === p.k) {
      const s = summarized(m.z, sec, c.res)
      const f = s && profileFields(s)
      if (f) block = { ...f, slope: slope ?? null, fcode: fcode ?? null }
    }
    Object.assign(block, {
      label: `${pyFixedComma(stationFt, 0)} ft`, station_ft: stationFt, position_frac: Number(pyFixed(Number(c.position_frac), 4)),
      dem_resolution_m: res, dem_source: source,
    })
    // which grid section it is (to pull its profile) and its stages above the thalweg (the heights the
    // boxes show before it has its profile); the Shiny block has no such field
    block.xs = { ...p, res: c.res, bfM: sec?.bfM ?? null, lbM: sec?.lbM ?? null, stored: hasProfile(block) }
    blocks.push(block)
  }
  const sel = Math.min(Math.max(Math.trunc(geom.default_index ?? Math.floor(blocks.length / 2)), 0), blocks.length - 1)
  const block = blocks[sel]
  return { geom: block, candidates: blocks, selected: sel, default: sel, entrenchment_ratio: block.entrenchment_ratio,
    bank_height_ratio: block.bank_height_ratio, reach: geom.reach, n_transects: blocks.length,
    caption: xsCaption(block.entrenchment_ratio, block.bank_height_ratio, block.division) }
}

/** A section's profile from USGS 3DEP, as the build read it (the release's recorded position, tiles and
 * read grids; the checksum settles the samples the browser's math rounds differently). The block gets
 * its profile; false when the section cannot be pulled. */
export async function pullProfile(d: EasiAssessData, block: Obj): Promise<boolean> {
  const p = block.xs
  if (!p) return false
  const secs = await sectionsOf(d, p.vpu, p.id)
  const sec = secs.find((s) => s.part === p.part && s.k === p.k)
  const pulls = await sectionPulls(d.src, p.vpu, BigInt(p.id))
  const at = pulls?.find((s) => s.part === p.part && s.k === p.k)
  if (!sec || !at || !sec.reads.length) return false
  const got = await pullExact({ x: at.x, y: at.y, nx: at.nx, ny: at.ny, wide: sec.wide, nPts: sec.nPts },
    { res: p.res, source: sec.source, tiles: sec.tiles }, sec.reads, at.zCrc)
  const s = summarized(got.z, sec, p.res)
  const f = s && profileFields(s)
  if (!f) return false
  Object.assign(block, f)
  p.pulled = true
  return true
}

/** assessment._xsection_caption */
export function xsCaption(er: number | null, bhr: number | null, division: string | null, edited = false): string {
  void er
  void bhr
  if (edited) {
    return 'Edited cross-section. Floodprone width is measured at 2x max bankfull depth (Rosgen). The bank-height ratio uses your low-bank height.'
  }
  const reg = division ? ` (Bieger bankfull, ${division})` : ''
  return '3DEP cross-section nearest the reach medians' + reg + '. The metrics score on the medians of all sampled sections. Edit '
    + 'the bankfull and low-bank heights in the table.'
}

/** The heights a section's boxes show by default (its stages above the thalweg, display unit). */
export function defaultHeights(block: Obj, perM: number): [number, number] {
  if (hasProfile(block)) {
    return [round2((block.bankfull_stage - block.thalweg) * perM), round2((block.floodplain_stage - block.thalweg) * perM)]
  }
  const p = block.xs || {}
  return [round2((p.bfM ?? 0) * perM), round2((p.lbM ?? 0) * perM)]
}

// ---------------------------------------------------------------- the card's markup

/** _bieger_area_tip_html */
function biegerAreaTip(current: string | null): string {
  const parts = ['<div class="easi-tip-title">Bieger bankfull XS-area curves</div>',
    '<div class="easi-tip-sec"><span class="easi-tip-lbl">Regression</span>A = a·DA<sup>b</sup>, area in m², drainage area in km² '
    + '(Bieger et al. 2015, Table 3).</div>',
    '<div class="easi-tip-sec"><span class="easi-tip-lbl">Physiographic division</span>']
  const lines = AREA_ORDER.map((k) => {
    const [a, b] = COEF[k].area
    const name = DIV_NAME[k] ?? k
    let line = `${pyHtmlEscape(name)}: A = ${pyG(a)}·DA<sup>${pyG(b)}</sup> (R²=${pyFixed(AREA_R2[k], 2)})`
    if (current && name === current) line = `<b>${line}</b>`
    return `<div class="easi-tip-crit">${line}</div>`
  })
  parts.push(lines.join('') + '</div>')
  return parts.join('')
}

/** The stages the shown section is drawn and measured at (current_geometry). */
export interface Geometry { block: Obj, unit: string, bankfull_stage: number, floodplain_stage: number }

/** _xs_reach_rows */
export function reachRows(reach: Obj | null | undefined): [string, string][] {
  const out: [string, string][] = []
  for (const [key, label] of [['entrenchment_ratio', 'Reach median ER'], ['bank_height_ratio', 'Reach median BHR']]) {
    const s = (reach || {})[key] || {}
    if (s.median === null || s.median === undefined) continue
    const n = Math.trunc(Number(s.n || 0))
    const bhr = key === 'bank_height_ratio'
    const med = fmtBhr(s.median, bhr && s.median_capped)
    const hi = fmtBhr(s.max, bhr && s.max_capped)
    out.push([label, `${med} (${pyFixed(s.min, 2)} to ${hi}, ${n} section${n !== 1 ? 's' : ''})`])
  }
  return out
}

/** xs_summary: the shown section's geometry at the current stages, and the reach medians. A section
 * without its profile shows the grid's numbers (in the unit chosen). */
export function xsSummary(block: Obj, g: Geometry | null, unitChosen: string, edited: boolean, reach: Obj | null): Child {
  const unit = g ? g.unit : hasProfile(block) ? 'ft' : unitChosen
  const ul = unit === 'ft' ? 'ft' : 'm'
  const perM = unit === 'ft' ? FT_PER_M : 1.0
  const d = g ? deriveFromStages(block.stations, block.elevs, block.thalweg, g.bankfull_stage, g.floodplain_stage) : block
  const er = d.entrenchment_ratio ?? null
  const bhr = d.bank_height_ratio ?? null
  const bfW = d.bankfull_width_m ?? null
  const fpW = d.flood_prone_width_m ?? null
  const edge = d.edge_limited
  const wd = (x: number | null) => (x !== null ? `${pyFixed(x * perM, 1)} ${ul}` : 'n/a')
  const rt = (x: number | null) => (x !== null ? pyFixed(x, 2) : 'n/a')
  const ar = (m2: number | null) => (m2 === null || m2 === undefined ? 'n/a' : unit === 'ft' ? `${pyFixed(m2 * perM * perM, 1)} ft²` : `${pyFixed(m2, 1)} m²`)
  const bfStage = g ? g.bankfull_stage : block.bankfull_stage
  const bfArea = hasProfile(block) && bfStage !== null && bfStage !== undefined ? flowArea(block.stations, block.elevs, bfStage)[0] : null
  const region = block.division || 'National curve'
  const bkArea = block.bankfull_area_m2
  const areaEdge = block.bankfull_area_edge_limited
  const areaVal: Child = bkArea !== null && bkArea !== undefined
    ? new TagList(ar(bkArea) + (areaEdge ? ' ‡' : ''), ' ', info(null, biegerAreaTip(region)))
    : 'n/a'
  const rows: [string, Child][] = [['Bieger region', region], ['Bieger XS area', areaVal], ['Bankfull width', wd(bfW)],
    ['Bankfull XS area', ar(bfArea)], ['Floodprone width', wd(fpW) + (edge ? ' †' : '')], ['Section ER', rt(er)],
    ['Section BHR', fmtBhr(bhr, !edited && block.low_bank_capped)], ...reachRows(reach)]
  const out: Child[] = [h.table(h.tbody(...rows.map(([lbl, val]) => h.tr(h.th(lbl), h.td(val)))), { class: 'easi-tbl easi-xs-tbl' })]
  if (areaEdge) {
    out.push(h.p('‡ the sampled DEM window is narrower than the regional bankfull area; the bankfull depth may be under-estimated.',
      { class: 'easi-xs-foot' }))
  }
  if (edge) out.push(h.p('† floodprone reached the sampled edge; width is likely under-estimated.', { class: 'easi-xs-foot' }))
  return new TagList(...out)
}

/** xs_selector */
export function xsSelector(cands: Obj[], i: number, def: number): Child {
  if (cands.length < 2) return null
  const label = cands[i].label || String(i + 1)
  return h.span(`${label}${i === def ? ' · default' : ''} (${i + 1} of ${cands.length})`, { class: 'easi-xs-switch-lbl' })
}

/** _xs_reach_table_ui */
export function xsReachTable(cands: Obj[], selected: number, def: number, reach: Obj | null): Child {
  if (!cands || cands.length < 2) return null
  const cell = (i: number, text: string, tag = false) => h.td(text, tag ? h.small(' default', { class: 'easi-xs-median' }) : null,
    { style: i === selected ? 'font-weight:600;' : '' })
  const rt = (v: any) => (v !== null && v !== undefined ? pyFixed(v, 2) : 'n/a')
  const stats = reach || {}
  const has = (k: string) => (stats[k] || {}).median !== null && (stats[k] || {}).median !== undefined
  const withMed = has('entrenchment_ratio') || has('bank_height_ratio')
  const med = (key: string): Tag[] => {
    const s = stats[key] || {}
    if (s.median === null || s.median === undefined) return [h.td('n/a', { class: 'easi-xs-reach-med' })]
    const bhr = key === 'bank_height_ratio'
    const hi = fmtBhr(s.max, bhr && s.max_capped)
    return [h.td(fmtBhr(s.median, bhr && s.median_capped), h.small(` (${rt(s.min)} to ${hi})`), { class: 'easi-xs-reach-med' })]
  }
  const head = h.tr(h.th('Section'), ...cands.map((c, i) => cell(i, c.label || String(i + 1), i === def)),
    ...(withMed ? [h.th('Reach median', { class: 'easi-xs-reach-med' })] : []))
  const er = h.tr(h.th('Entrenchment ratio'), ...cands.map((c, i) => cell(i, rt(c.entrenchment_ratio))), ...(withMed ? med('entrenchment_ratio') : []))
  const bhr = h.tr(h.th('Bank-height ratio'), ...cands.map((c, i) => cell(i, fmtBhr(c.bank_height_ratio, c.low_bank_capped))),
    ...(withMed ? med('bank_height_ratio') : []))
  const capped = cands.some((c) => c.low_bank_capped)
  return h.div(
    h.div('Reach cross-sections', { class: 'easi-xs-panel-title' }),
    h.table(h.tbody(head, er, bhr), { class: 'easi-tbl easi-xs-tbl easi-xs-reach' }),
    h.p('Stations from the upstream end of the reach. Metrics score on the reach medians. The default section is the one nearest both medians.',
      { class: 'easi-xs-foot' }),
    capped ? h.p('≥2.00: no bank found below the floodprone stage, so the low bank is capped at twice the bankfull depth.', { class: 'easi-xs-foot' }) : null,
    { class: 'easi-xs-reach-wrap' })
}

/** ui.input_numeric */
function numeric(id: string, label: Child, value: number, disabled: boolean): Tag {
  return h.div(
    h.label(label, { class: 'control-label', id: `easi-${id}-label`, for: `easi-${id}` }),
    h.input({ id: `easi-${id}`, type: 'number', class: 'shiny-input-number form-control', value: pyFloat(value), min: '0', step: '0.1',
      'data-update-on': 'change', disabled: disabled ? 'disabled' : null }),
    { class: 'form-group shiny-input-container' })
}

/** ui.input_radio_buttons (inline, no label) */
function unitRadio(unit: string): Tag {
  const opt = (value: string, text: string) => h.label(
    h.input({ type: 'radio', name: 'easi-xs_unit', value, checked: unit === value ? 'checked' : null }), ' ', h.span(text),
    { class: 'radio-inline', _add_ws: true })
  return h.div(
    h.label({ class: 'control-label shiny-label-null', id: 'easi-xs_unit-label', for: 'easi-xs_unit' }),
    h.div(opt('ft', 'Feet'), opt('m', 'Meters'), { class: 'shiny-options-group' }),
    { id: 'easi-xs_unit', class: 'form-group shiny-input-radiogroup shiny-input-container shiny-input-container-inline', role: 'radiogroup',
      'aria-labelledby': 'easi-xs_unit-label' })
}

/** The plot's place for a section whose profile is neither stored nor pulled: the button that pulls it
 * (a section with its profile gets the plotly.js plot, drawn into the box by XsPlot). */
export function pullContent(pulling: boolean, error: string | null): Child {
  return h.div(
    h.p("Pull this section's elevation profile from USGS 3DEP to draw it and edit its heights.",
      { style: 'margin:0;font-size:12.5px;color:#33415c;max-width:300px;' }),
    h.button(pulling ? 'Pulling from 3DEP…' : 'Pull from 3DEP', { type: 'button', 'data-xs-pull': '1', disabled: pulling ? 'disabled' : null },
      { class: 'sfari-btn primary' }),
    error ? h.p(error, { class: 'easi-xs-foot', style: 'margin:0;' }) : null,
    { class: 'easi-xs-pull', style: 'height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;' })
}

/** What the card shows. */
export interface CardState {
  cands: Obj[]
  sel: number
  def: number
  reach: Obj | null
  unit: string
  boxes: [number, number]
  geometry: Geometry | null
  edited: boolean
  windowRange: string
  pulling: boolean
  pullError: string | null
}

/** _xs_editor: the geometry panel with the height boxes and the unit, the plot with its section switch,
 * and the reach's sections side by side. */
export function xsEditor(st: CardState | null, unpublished = false): Child {
  const block = st && st.cands.length ? st.cands[st.sel] : null
  if (!st || !block) {
    return h.div(unpublished ? 'Cross-sections for this region are not published yet.' : 'Cross-section geometry is unavailable for this reach.',
      { class: 'easi-instr' })
  }
  const bkArea = block.bankfull_area_m2
  const areaTxt = bkArea !== null && bkArea !== undefined ? `${pyFixed(bkArea, 1)} m² ` : ''
  const bfTip = 'The depth at which the channel cross-sectional area equals the Bieger et al. (2015) regional bankfull area '
    + `(${areaTxt}for the ${block.division || 'national'} division). Edit to use a surveyed value.`
  const lbTip = 'The first break in slope to a flat depositional surface (bank) on the DEM profile, capped at the floodprone elevation. '
    + 'Drives the bank-height ratio. Edit to use a surveyed value.'
  const locked = !hasProfile(block)
  const panel = h.div(
    h.div('Cross-section geometry', { class: 'easi-xs-panel-title' }),
    output('xs_summary', xsSummary(block, st.geometry, st.unit, st.edited, st.reach)),
    h.div(numeric('xs_bankfull', h.span('Bankfull depth ', info(bfTip)), st.boxes[0], locked),
      numeric('xs_lowbank', h.span('Low bank height ', info(lbTip)), st.boxes[1], locked), { class: 'easi-xs-fields' }),
    unitRadio(st.unit),
    { class: 'easi-xs-panel' })
  const sw = st.cands.length >= 2
    ? h.div(actionButton('easi-xs_prev', '◀', 'easi-xs-arrow'), output('xs_selector', xsSelector(st.cands, st.sel, st.def)),
      actionButton('easi-xs_next', '▶', 'easi-xs-arrow'), { class: 'easi-xs-switch' })
    : null
  const win = h.div(h.div(st.windowRange, { id: 'easi-xs_window_range', class: 'shiny-text-output', 'aria-live': 'polite' }), { class: 'easi-xs-winrange' })
  const head = h.div(h.span('Reach cross-section', { class: 'easi-xs-plot-title' }), sw, win, { class: 'easi-xs-plot-head' })
  const plot = h.div(h.div(locked ? pullContent(st.pulling, st.pullError) : null, { id: 'easi-xsection_plot',
    class: 'shiny-ipywidget-output shiny-report-size shiny-report-theme', style: 'height: 100%; visibility: inherit;', 'aria-live': 'polite' }),
    { class: 'easi-xsection' })
  return new TagList(h.div(panel, h.div(head, plot, { class: 'easi-xs-right' }), { class: 'easi-xsection-wrap easi-xs-in-card' }),
    output('xs_reach_table', xsReachTable(st.cands, st.sel, st.def, st.reach)))
}

// ---------------------------------------------------------------- the report's read-only cross-section

/** What the report's image draws for a cross-section (xs_render: the shown section at its heights and
 * unit, or the screening's own section as it was), null without a profile. */
export function reportImageSpec(xs: Obj | null | undefined): XsImageSpec | null {
  const block = (xs || {}).geom
  if (!hasProfile(block)) return null
  const drawn = xs!.drawn || {}
  return { stations: block.stations, elevs: block.elevs, thalweg: block.thalweg,
    bankfull_stage: drawn.bankfull_stage ?? block.bankfull_stage, floodplain_stage: drawn.floodplain_stage ?? block.floodplain_stage,
    unit: drawn.unit ?? 'ft', source: block.dem_source ?? null }
}

/** _xs_readonly_block: the shown section's ratios beside the reach medians, and its image (``png``, a data
 * URL); a section whose profile is not pulled has the note in its place. */
export function xsReadonly(rep: Obj, png: string | null): Child {
  const xs = (rep || {}).crossSection || {}
  const block = xs.geom || {}
  if (!xs.geom) return null
  const er = xs.entrenchment_ratio ?? block.entrenchment_ratio ?? null
  const bhr = xs.bank_height_ratio ?? block.bank_height_ratio ?? null
  const wd = (m: number | null | undefined) => (m !== null && m !== undefined ? `${pyFixed(m * FT_PER_M, 1)} ft` : 'n/a')
  const f2 = (x: number | null) => (x === null ? '—' : pyFixed(x, 2))
  const edited = String(xs.caption || '').startsWith('Edited')
  const rows: [string, string][] = [['Bieger region', block.division || 'National curve'], ['Bankfull width', wd(block.bankfull_width_m)],
    ['Floodprone width', wd(block.flood_prone_width_m)], ['Section ER', f2(er)], ['Section BHR', fmtBhr(bhr, block.low_bank_capped && !edited)],
    ...reachRows(xs.reach)]
  const table = h.table(h.tbody(...rows.map(([lbl, val]) => h.tr(h.th(lbl), h.td(val)))), { class: 'easi-tbl easi-xs-tbl' })
  const panel = h.div(h.div('Cross-section geometry', { class: 'easi-xs-panel-title' }), table, { class: 'easi-xs-panel' })
  const plot = h.div(png ? h.img({ src: png }) : h.p("Pull this section's profile from USGS 3DEP on the Assessment step to draw it.",
    { style: 'margin:auto;font-size:12.5px;color:#33415c;' }), { class: 'easi-xsection' })
  return h.div(panel, plot, { class: 'easi-xsection-wrap' })
}

// ---------------------------------------------------------------- the plot

/** The figure of the shown section at the current stages (_xs_src_figure). */
export function sourceFigure(block: Obj, g: Geometry | null): Figure {
  return figure(block.stations, block.elevs, block.thalweg, g ? g.bankfull_stage : block.bankfull_stage,
    g ? g.floodplain_stage : block.floodplain_stage, g ? g.unit : 'ft', block.dem_source ?? null)
}

/** xs_window_range: the windowed default view, as json.dumps writes it (worksheet.js's Zoom Home reads it). */
export function windowRange(fig: Figure | null): string {
  if (!fig) return ''
  const pair = (r: number[]) => `[${pyFloat(r[0])}, ${pyFloat(r[1])}]`
  return `{"x": ${pair(fig.layout.xaxis.range)}, "y": ${pair(fig.layout.yaxis.range)}}`
}

/** xsplotly.sync_payload: the figure replayed onto the live plot, one value per trace. */
function syncPayload(fig: Figure): [Obj, Obj] {
  const t = fig.data
  const restyle = { x: t.map((d) => [...d.x]), y: t.map((d) => [...d.y]), fillcolor: t.map((d) => d.fillcolor ?? null),
    hovertemplate: t.map((d) => d.hovertemplate ?? null) }
  const lay = fig.layout
  const relayout = { shapes: lay.shapes, annotations: lay.annotations, 'xaxis.autorange': false, 'yaxis.autorange': false,
    'xaxis.range': [...lay.xaxis.range], 'yaxis.range': [...lay.yaxis.range], 'xaxis.title.text': lay.xaxis.title.text,
    'yaxis.title.text': lay.yaxis.title.text }
  return [restyle, relayout]
}

/** The cross-section plot as the Shiny card's FigureWidget draws it (plotly.py's widget: 360 px high,
 * the width of its box, no logo, resized with the window) and keeps it (updated in place). */
export class XsPlot {
  private gd: HTMLElement | null = null
  private gen = 0
  private readonly onResize = (): void => {
    const gd = this.gd
    if (!gd) return
    if (!gd.isConnected) {
      this.drop()
      return
    }
    void plotly().then((P) => P.Plots.resize(gd))
  }

  /** Draws the figure into the card's widget box (a new plot when the box is new). */
  async show(fig: Figure): Promise<void> {
    const box = document.getElementById('easi-xsection_plot')
    if (!box) return
    const gen = ++this.gen
    const P = await plotly()
    if (gen !== this.gen) return
    if (this.gd && this.gd.isConnected && this.gd.parentElement === box) {
      const [restyle, relayout] = syncPayload(fig)
      await P.update(this.gd, restyle, relayout, fig.data.map((_, i) => i))
      return
    }
    this.release()
    box.innerHTML = ''
    const gd = document.createElement('div')
    gd.className = 'lm-Widget p-Widget'
    box.appendChild(gd)
    this.gd = gd
    window.addEventListener('resize', this.onResize)
    await P.newPlot(gd, JSON.parse(JSON.stringify(fig.data)), { ...JSON.parse(JSON.stringify(fig.layout)), height: 360 },
      { displaylogo: false, editSelection: false })
  }

  /** The plot goes (its box was drawn again, or the section shown has no profile). */
  drop(): void {
    this.gen += 1
    this.release()
  }

  private release(): void {
    window.removeEventListener('resize', this.onResize)
    const gd = this.gd
    this.gd = null
    const P = (window as any).Plotly
    if (gd && P) P.purge(gd)
  }
}

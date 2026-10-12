// EASI's PDFs (easi/report.py build_pdf and build_desktop_metrics_pdf), laid out by the port of reportlab.
// EASI builds neither in invariant mode (their dates are the download's), so ``invariant`` is for tests.
// The report's two pictures are the app's own: the summary plot (summaryplot.ts) and the cross-section
// (xsimage.ts), drawn as matplotlib lays them out; the caller passes their PNGs.
import { pyFixed, pyFloat } from '../../app/pyfmt'
import { isMarkedFloat } from '../../app/pyjson'
import { xmlEscape } from '../../files/py'
import { HexColor, white } from '../../files/pdf/colors'
import { inch, letter, SimpleDocTemplate } from '../../files/pdf/doctemplate'
import { type Flowable, Image, Spacer } from '../../files/pdf/flowables'
import { decodePng } from '../../files/pdf/images'
import { Paragraph } from '../../files/pdf/paragraph'
import { getSampleStyleSheet, ParagraphStyle } from '../../files/pdf/styles'
import { type CellValue, type Cmd, Table } from '../../files/pdf/tables'
import { fmtBhr } from '../../engine/geomorph'
import { displaySimplify } from '../../engine/simplify'
import { reachStats } from '../../engine/xsreach'
import { pdfFlowable } from '../../shared/reportmapPdf'
import { entriesFromResult } from './calculator'
import { orderedRows } from './exports'
import { desktopMetricRows } from './forms'
import { scoringMethodText } from './identity'
import { BORROWED_MARK, borrowedNote, isBorrowed, markerNote, routedNotice } from './notices'
import { siteIdentity } from './report'

type Obj = Record<string, any>
const RATING_COLOR: Record<string, string> = { Good: '#c8d9f2', Fair: '#f5e7a6', Poor: '#f5b5b5' }
const esc = (s: string) => xmlEscape(s)

/** str() of a value (a float's repr where the screening holds a float) */
function pyText(v: unknown, container?: Obj, key?: string | number): string {
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') {
    const marked = container !== undefined && key !== undefined && isMarkedFloat(container, key)
    return Number.isInteger(v) && !Object.is(v, -0) && !marked ? String(v) : pyFloat(v)
  }
  return String(v)
}

/** _xs_reach_sentence: the reach medians the metrics score on, and which section is drawn */
export function xsReachSentence(xs: Obj | null | undefined): string {
  const x = xs || {}
  const cands: Obj[] = x.candidates || []
  const stats: Obj = x.reach || reachStats(cands as never)
  const n = Math.trunc(Number(stats.n || 0))
  if (n < 2) return ''
  const parts: string[] = []
  for (const [key, label] of [['entrenchment_ratio', 'entrenchment ratio'], ['bank_height_ratio', 'bank-height ratio']]) {
    const s: Obj = stats[key] || {}
    if (s.median !== null && s.median !== undefined) {
      const bhr = key === 'bank_height_ratio'
      const med = fmtBhr(s.median, bhr && s.median_capped, true)
      const hi = fmtBhr(s.max, bhr && s.max_capped, true)
      parts.push(`${label} ${med} (${pyFixed(s.min, 2)} to ${hi})`)
    }
  }
  let text = `Metrics score on the reach medians of ${n} sections`
  text += parts.length ? ': ' + parts.join(', ') + '.' : '.'
  if (cands.length) {
    const selI = Math.min(Math.max(Math.trunc(Number(x.selected || 0) || 0), 0), cands.length - 1)
    const defaultI = Math.trunc(x.default !== null && x.default !== undefined ? Number(x.default) : selI)
    const which = selI === defaultI ? 'nearest both medians' : 'chosen in the worksheet'
    const label = cands[selI].label
    text += label ? ` The section at ${label}, ${which}, is shown.` : ` The section ${which} is shown.`
  }
  return text
}

export interface EasiPdfOptions {
  invariant?: boolean
  /** the summary plot's PNG (summaryplot.ts) */
  summaryPng: Uint8Array
  /** the cross-section's PNG (xsimage.ts), null when the section has no profile */
  xsPng?: Uint8Array | null
  /** the basemap's PNG instead of the fetch (tests); null draws the map without it */
  basemapPng?: Uint8Array | null
}

/** build_pdf: the report of the screening ("EASI Report: <stream>"); ``scenario`` names the scenario. */
export async function buildPdf(result: Obj, scenario: [string, string] | null, opts: EasiPdfOptions): Promise<Uint8Array> {
  const d: Obj = result.delineation
  const rep: Obj = result.report
  const styles = getSampleStyleSheet()
  const story: Flowable[] = []
  story.push(new Paragraph(`EASI Report: ${'gnis_name' in d ? pyText(d.gnis_name) : ''}`, styles.Title))
  if (scenario) {
    const [name, description] = scenario
    story.push(new Paragraph(`<b>Scenario:</b> ${esc(name)}`, styles.Normal))
    if (description) story.push(new Paragraph(esc(description), styles.Italic))
  }
  const lat = d.snapped_lat
  const lon = d.snapped_lon
  const pt = lat !== null && lat !== undefined && lon !== null && lon !== undefined ? `${pyFixed(lat, 4)}, ${pyFixed(lon, 4)}` : '—'
  const reachFt = typeof d.reach_length_ft === 'number' ? pyFloat(d.reach_length_ft) : pyText(d.reach_length_ft)
  story.push(new Paragraph(`Analysis point ${pt} · Reach ${reachFt} ft upstream`, styles.Normal))
  const methodLine = scoringMethodText()
  if (methodLine) story.push(new Paragraph(`Scoring method: ${methodLine}`, styles.Normal))
  story.push(new Spacer(1, 8))
  const anchor: Obj = result.siteAnchor || {}
  const w = routedNotice(anchor, d)
  if (w && d.watershed_source !== 'site-engine') story.push(new Paragraph('<b>Note.</b> ' + w.lines.join(' '), styles.Normal), new Spacer(1, 6))
  if (rep.provisionalCoverage) {
    story.push(new Paragraph('<b>Provisional (limited coverage).</b> One or more included coverage '
      + 'measures are below 70%. Missing domains are shown as a gray dash, not zero.', styles.Normal), new Spacer(1, 6))
  } else if ((rep.coverage || {}).completeWithProxies) {
    story.push(new Paragraph('<b>Complete screening coverage (includes proxy-derived ratings).</b> '
      + 'Use the evidence profile and per-metric source tier to distinguish '
      + 'observations, models, and screening proxies.', styles.Normal), new Spacer(1, 6))
  }
  const ep: Obj = (rep.coverage || {}).evidenceProfile || {}
  if (Object.keys(ep).length) {
    const g = (k: string) => (k in ep ? pyText(ep[k], ep, k) : '0')
    story.push(new Paragraph('Evidence profile: ' + [`observed ${g('observed')}`, `connected/nearby ${g('connectedNearby')}`,
      `published model ${g('publishedModel')}`, `screening proxy ${g('screeningProxy')}`, `manual ${g('manual')}`,
      `unavailable ${g('unavailable')}`].join(' · '), styles.Normal), new Spacer(1, 4))
  }
  for (const item of rep.correlationNotes || []) {
    story.push(new Paragraph(`<b>Shared evidence:</b> ${item.text ?? ''}`, styles.Normal), new Spacer(1, 3))
  }
  const basinRows: [unknown, unknown, Obj | undefined, number | undefined][] = [['COMID', d.comid, d, undefined], ['HUC12', d.huc12 || '—', undefined, undefined],
    ...((rep.basin || {}).rows || []).map((r: unknown[]) => [r[0], r[1], r, 1] as [unknown, unknown, Obj, number])]
  if (basinRows.length) {
    story.push(new Paragraph('Basin characteristics', styles.Heading4))
    story.push(new Table(basinRows.map(([lbl, val, c, k]) => [new Paragraph(`<b>${lbl}</b>`, styles.BodyText),
      new Paragraph(c && k === undefined ? pyText(val, c, 'comid') : c ? pyText(val, c, k) : pyText(val), styles.BodyText)]), {
      colWidths: [2.4 * inch, 4.0 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 8.5], ['VALIGN', [0, 0], [-1, -1], 'TOP'],
        ['GRID', [0, 0], [-1, -1], 0.25, HexColor('#e5e8ee')], ['ROWBACKGROUNDS', [0, 0], [-1, -1], [white, HexColor('#fafbfd')]]],
    }), new Spacer(1, 8))
  }
  story.push(new Image(decodePng(opts.summaryPng), 7.0 * inch, 5.44 * inch),
    new Paragraph('Bars colored by condition band. Blue: Functioning · yellow: '
      + 'Functioning-at-Risk · red: Non-Functioning.', styles.Italic), new Spacer(1, 8))
  const map = await pdfFlowable(result.watershed_geojson ? displaySimplify(result.watershed_geojson, 700) : result.watershed_geojson ?? null,
    result.reach_geojson ?? null, 5.0 * inch, 5.0 * inch * 180 / 290, { png: opts.basemapPng })
  if (map !== null) story.push(new Paragraph('Watershed', styles.Heading4), map, new Spacer(1, 8))

  const xs: Obj = rep.crossSection || {}
  if (opts.xsPng) {
    story.push(new Paragraph('Reach cross-sections', styles.Heading4), new Image(decodePng(opts.xsPng), 6.0 * inch, 2.4 * inch))
    const geom: Obj = xs.geom || {}
    const thal = geom.thalweg
    const ft = 3.28084
    const wd = (m: number | null | undefined) => (m !== null && m !== undefined ? `${pyFixed(m * ft, 1)} ft` : 'n/a')
    const hgt = (stage: number | null | undefined) => (stage !== null && stage !== undefined && thal !== null && thal !== undefined
      ? `${pyFixed((stage - thal) * ft, 2)} ft` : 'n/a')
    const er = xs.entrenchment_ratio
    const bhr = xs.bank_height_ratio
    const edited = String(xs.caption || '').startsWith('Edited')
    const bhrTxt = fmtBhr(bhr, geom.low_bank_capped && !edited, true)
    const bka = geom.bankfull_area_m2
    let summary = bka !== null && bka !== undefined
      ? `Bieger region: ${geom.division || 'National curve'} &middot; Bieger XS area: ${pyFixed(bka, 1)} m² &middot; ` : ''
    summary += `Bankfull width: ${wd(geom.bankfull_width_m)} &middot; Floodprone width: ${wd(geom.flood_prone_width_m)} &middot; `
      + `Section ER: ${er !== null && er !== undefined ? (typeof er === 'number' ? pyFloat(er) : String(er)) : 'n/a'} &middot; Section BHR: ${bhrTxt} &middot; `
      + `Bankfull height: ${hgt(geom.bankfull_stage)} &middot; Low bank height: ${hgt(geom.floodplain_stage)}`
    story.push(new Paragraph(summary, styles.Normal))
    const sentence = xsReachSentence(xs)
    if (sentence) story.push(new Paragraph(sentence, styles.Normal))
    story.push(new Spacer(1, 8))
  }

  const metricRows = orderedRows(rep)
  const hasNotes = metricRows.some((r) => r.userNote)
  const head: CellValue[] = ['Function', 'Metric', 'Value', 'Rating', 'Idx', 'Fn', 'Conf']
  if (hasNotes) head.push('Notes')
  const data: CellValue[][] = [head]
  const ratingBg: [number, string][] = []
  metricRows.forEach((r, k) => {
    const i = k + 1
    const mark = isBorrowed(r) ? BORROWED_MARK : ''
    const row: CellValue[] = [new Paragraph(r.functionName, styles.BodyText), new Paragraph(r.name + mark, styles.BodyText),
      new Paragraph(r.valueText, styles.BodyText), r.rating || '—', r.index === null || r.index === undefined ? '' : pyFixed(r.index, 2),
      r.functionScore === null || r.functionScore === undefined ? '' : pyText(r.functionScore, r, 'functionScore'), r.confidence || '']
    if (hasNotes) row.push(new Paragraph(esc(r.userNote ?? ''), styles.BodyText))
    data.push(row)
    if (r.rating in RATING_COLOR) ratingBg.push([i, RATING_COLOR[r.rating]])
  })
  const colWidths = !hasNotes ? [1.3 * inch, 1.5 * inch, 1.9 * inch, 0.5 * inch, 0.35 * inch, 0.3 * inch, 0.4 * inch]
    : [1.1 * inch, 1.25 * inch, 1.35 * inch, 0.5 * inch, 0.35 * inch, 0.3 * inch, 0.4 * inch, 1.25 * inch]
  const style: Cmd[] = [['BACKGROUND', [0, 0], [-1, 0], HexColor('#2f4b7c')], ['TEXTCOLOR', [0, 0], [-1, 0], white], ['FONTSIZE', [0, 0], [-1, -1], 7.5],
    ['VALIGN', [0, 0], [-1, -1], 'TOP'], ['GRID', [0, 0], [-1, -1], 0.25, HexColor('#d7dce5')]]
  for (const [rowI, color] of ratingBg) style.push(['BACKGROUND', [3, rowI], [3, rowI], HexColor(color)])
  story.push(new Table(data, { colWidths, repeatRows: 1, style }))
  const markerText = markerNote(anchor)
  const hasBorrowed = metricRows.some((r) => isBorrowed(r))
  const sourceStyle = new ParagraphStyle('SourceNote', { parent: styles.BodyText, fontSize: 8, leading: 10, textColor: HexColor('#556070') })
  if (markerText && hasBorrowed) story.push(new Spacer(1, 4), new Paragraph(`${BORROWED_MARK} ${markerText}`, sourceStyle))
  const sourceDetails: string[] = []
  if (w && d.watershed_source === 'site-engine') sourceDetails.push(...w.lines)
  if (hasBorrowed) sourceDetails.push(borrowedNote(anchor))
  if (sourceDetails.length) story.push(new Spacer(1, 8), new Paragraph('<b>Source details.</b> ' + esc(sourceDetails.join(' ')), sourceStyle))
  story.push(new Spacer(1, 8), new Paragraph('Generated from national datasets. A desktop screening estimate with '
    + 'per-metric confidence, not a field-validated assessment.', styles.Italic))
  const doc = new SimpleDocTemplate({ pagesize: letter, topMargin: 0.6 * inch, bottomMargin: 0.6 * inch, invariant: opts.invariant ?? false })
  return doc.build(story)
}

/** build_desktop_metrics_pdf: Get Forms' desktop metrics list ("EASI Desktop Metrics"). ``cells`` are the
 * calculator's entry cells (calculator.entryCells). */
export function buildDesktopMetricsPdf(result: Obj, cells: Record<string, string>, { invariant = false } = {}): Uint8Array {
  const res = result || {}
  const d: Obj = res.delineation || {}
  const rep: Obj = res.report || {}
  const anchor: Obj = res.siteAnchor || {}
  const [entries, disclosures] = entriesFromResult(res, cells)
  const site = siteIdentity(res)
  const rows = desktopMetricRows(res)
  const base = getSampleStyleSheet()
  const small = new ParagraphStyle('dm_small', { parent: base.BodyText, fontSize: 7.5, leading: 9.2 })
  const dim = new ParagraphStyle('dm_dim', { parent: small, textColor: HexColor('#556070') })
  const noteStyle = new ParagraphStyle('dm_note', { parent: base.BodyText, fontSize: 8.5, leading: 10.5 })
  const p = (text: string, style = small) => new Paragraph(esc(text), style)
  const shownText = (v: unknown, c?: Obj, k?: string): string => (v === null || v === undefined || v === '' ? '-' : pyText(v, c, k))

  const story: Flowable[] = [new Paragraph('EASI Desktop Metrics', base.Title), new Paragraph(esc(site.name || 'Unnamed stream'), base.Heading2)]
  const slopeClass = entries.ctx_slope_override || (rep.strata || {}).slope_class
  const sub: Obj = rep.subIndices || {}
  const ratedOf = 'selectedCount' in rep ? rep.selectedCount : 'totalCount' in rep ? rep.totalCount : rows.length
  const siteRows: [string, string][] = [
    ['Reach', shownText(site.reach)],
    ['Analysis point', shownText(site.coords)],
    ['COMID (StreamCat reach)', shownText(site.comid)],
    ['Drainage area (km2)', shownText(d.drainage_area_sqkm, d, 'drainage_area_sqkm')],
    ['NARS-9 region', shownText(entries.ctx_region, entries, 'ctx_region')],
    ['Slope class', shownText(slopeClass || 'national')],
    ['NHD feature code', shownText(entries.ctx_fcode, entries, 'ctx_fcode')],
    ['Ecosystem Condition Index', shownText(rep.ecosystemConditionIndex, rep, 'ecosystemConditionIndex')],
    ['Physical, chemical, biological', ['physical', 'chemical', 'biological'].map((k) => shownText(sub[k], sub, k)).join(', ')],
    ['Functions rated', `${pyText('computedCount' in rep ? rep.computedCount : 0)} of ${pyText(ratedOf)}`],
  ]
  story.push(new Table(siteRows.map(([label, value]) => [p(label), p(value)]), { colWidths: [2.3 * inch, 5.0 * inch], style: [
    ['VALIGN', [0, 0], [-1, -1], 'TOP'], ['GRID', [0, 0], [-1, -1], 0.25, HexColor('#e5e8ee')], ['BACKGROUND', [0, 0], [0, -1], HexColor('#eef2f8')]] }),
  new Spacer(1, 10))

  const data: CellValue[][] = [['#', 'Discipline', 'Function', 'Metric', 'Entries (value rated)', 'Rating', 'Source'].map((t) => p(t))]
  const ratingBg: [number, string][] = []
  rows.forEach((r, k) => {
    const i = k + 1
    const entriesCell = r.inputs.map((item) => `${esc(item.label)}: <b>${esc(item.value)}</b>`).join('<br/>')
    let rating = r.rating || '-'
    if (r.assessed) rating += `<br/><font size=6.5>override, computed ${esc(r.computed || 'none')}</font>`
    data.push([p(String(r.n)), p(r.discipline, dim), p(r.function), p(r.metric + (r.borrowed ? BORROWED_MARK : '')),
      new Paragraph(entriesCell || esc(r.valueText || '-'), small), new Paragraph(rating, small), p(r.source, dim)])
    if (r.rating in RATING_COLOR) ratingBg.push([i, RATING_COLOR[r.rating]])
  })
  const style: Cmd[] = [['BACKGROUND', [0, 0], [-1, 0], HexColor('#eef2f8')], ['VALIGN', [0, 0], [-1, -1], 'TOP'],
    ['GRID', [0, 0], [-1, -1], 0.25, HexColor('#d7dce5')], ...ratingBg.map(([rowI, color]) => ['BACKGROUND', [5, rowI], [5, rowI], HexColor(color)] as Cmd)]
  story.push(new Table(data, { repeatRows: 1, colWidths: [0.32 * inch, 1.0 * inch, 1.05 * inch, 1.25 * inch, 1.88 * inch, 0.65 * inch, 1.15 * inch],
    style }))
  if (rows.some((r) => r.borrowed)) {
    const markerText = markerNote(anchor)
    if (markerText) story.push(new Spacer(1, 4), new Paragraph(`${BORROWED_MARK} ${esc(markerText)}`, dim))
  }
  const routed = routedNotice(anchor, d)
  if (routed) story.push(new Spacer(1, 6), new Paragraph('<b>Note.</b> ' + esc(routed.lines.join(' ')), noteStyle))
  const notes = rows.filter((r) => r.note)
  if (disclosures.length || notes.length) {
    story.push(new Spacer(1, 8), new Paragraph('Override scores and notes', base.Heading4))
    for (const line of disclosures) story.push(new Paragraph(esc(line), noteStyle))
    for (const r of notes) story.push(new Paragraph(`<b>${esc(r.function)}.</b> ${esc(r.note)}`, noteStyle))
  }
  story.push(new Spacer(1, 8), new Paragraph(
    'The entries are the values the EASI calculator takes in column J of its EASI Score sheet, as the '
    + 'application rated them. Generated from national datasets. A desktop screening estimate with '
    + 'per-metric confidence, not a field-validated assessment.', dim))
  const doc = new SimpleDocTemplate({ pagesize: letter, topMargin: 0.6 * inch, bottomMargin: 0.6 * inch, leftMargin: 0.6 * inch,
    rightMargin: 0.6 * inch, title: 'EASI Desktop Metrics', invariant })
  return doc.build(story)
}

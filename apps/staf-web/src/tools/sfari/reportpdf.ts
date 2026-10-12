// SFARI's PDFs (sfari/report.py build_pdf and build_desktop_metrics_pdf), laid out by the port of
// reportlab. SFARI builds neither in invariant mode (their dates are the download's), so ``invariant`` is
// for tests.
import { pyFloat } from '../../app/pyfmt'
import { isMarkedFloat } from '../../app/pyjson'
import { xmlEscape } from '../../files/py'
import { HexColor } from '../../files/pdf/colors'
import { inch, letter, SimpleDocTemplate } from '../../files/pdf/doctemplate'
import { type Flowable, Image, Spacer } from '../../files/pdf/flowables'
import { Paragraph } from '../../files/pdf/paragraph'
import { getSampleStyleSheet, ParagraphStyle } from '../../files/pdf/styles'
import { type CellValue, type Cmd, Table } from '../../files/pdf/tables'
import { displaySimplify } from '../../engine/simplify'
import { photoImage } from '../../shared/photos'
import { pdfFlowable } from '../../shared/reportmapPdf'
import type { Evidence } from './evidence'
import { netloc } from './forms'
import type { FunctionRecord, MetricRecord } from './markup'
import { CATEGORY_ORDER, FNS_BY_CAT, functionScoreBandLabel, type Scored } from './method'
import { BORROWED_MARK, BORROWED_NOTE, isBorrowed, LIKERT_SHORT, orderedMetrics, streamcatReachLabel, watershedBasisLabel } from './report'

type Obj = Record<string, any>

const PRINT_SAFE: Record<string, string> = {
  '–': '-', '—': '-', '→': '->', 'Δ': 'delta', 'δ': 'delta', 'τ': 'tau', '²': '2', '³': '3', '°': 'deg',
  'µ': 'u', 'μ': 'u',
}

/** to_print_safe: the few glyphs evidence text carries, as plain ASCII ("" for nothing) */
export function toPrintSafe(text: unknown): string {
  if (!text) return ''
  let out = ''
  for (const ch of String(text)) out += PRINT_SAFE[ch] ?? ch
  return out
}

/** str() of a value in a header line (None for a missing one, a float's repr) */
function pyText(container: Obj, key: string): string {
  const v = (container ?? {})[key]
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'number') return Number.isInteger(v) && !isMarkedFloat(container, key) ? String(v) : pyFloat(v)
  return String(v)
}

/** a score or index: a Python float */
const floatText = (v: unknown): string => (typeof v === 'number' ? pyFloat(v) : String(v))

function ffStyles() {
  const base = getSampleStyleSheet()
  const small = new ParagraphStyle('small', { parent: base.BodyText, fontSize: 7, leading: 8.4 })
  return {
    base, small,
    smallDim: new ParagraphStyle('small_dim', { parent: small, textColor: HexColor('#66708a') }),
    smallIt: new ParagraphStyle('small_it', { parent: small, fontName: 'Helvetica-Oblique', textColor: HexColor('#66708a') }),
  }
}

/** _value_or_status: the pulled value, or the metric's status */
function valueOrStatus(ev: Obj, ds: Obj): [string, boolean] {
  if (ev.status === 'ok' && (ev.value_text || ev.field_value_text)) return [toPrintSafe(ev.value_text || ev.field_value_text), false]
  if (ev.status === 'pending') return ['Pending: STAF site engine running', true]
  const client = (ds ?? {}).client
  if (client === 'manual') return ['Local review required', true]
  if (client === 'xscalc') return ['Run cross-section tool', true]
  if (ev.status === 'unavailable') return ['Unavailable', true]
  return ['Review in the field', true]
}

function metricsSummaryTable(evidence: Record<string, Evidence>): Table {
  const { small, smallDim, smallIt } = ffStyles()
  const data: CellValue[][] = [['Discipline', 'Function', 'Metric', 'Desktop method', 'Value / status', 'Data source']]
  for (const [cat, f, m] of orderedMetrics()) {
    if (!m.desktopSupportable) continue
    const ds: Obj = m.desktopSource || {}
    const ev: Obj = (evidence ?? {})[m.metricId] || {}
    const [text, isStatus] = valueOrStatus(ev, ds)
    const val = new Paragraph(xmlEscape(text), isStatus ? smallIt : small)
    const url: string = ev.source_url || ds.url || ''
    const name: string = ev.source || (url ? netloc(url) : '')
    let extra = ''
    if (ev.anchor_label) extra += `<br/>Describes: ${xmlEscape(toPrintSafe(ev.anchor_label))}`
    if (ev.fallback_reason) extra += `<br/>Fallback: ${xmlEscape(toPrintSafe(ev.fallback_reason))}`
    const src = url
      ? new Paragraph(`<link href="${xmlEscape(url, { '"': '&quot;' })}" color="#1f4e8c">${xmlEscape(name || url)}</link>` + extra, small)
      : new Paragraph(xmlEscape(name) + extra, small)
    data.push([new Paragraph(xmlEscape(cat), smallDim), new Paragraph(xmlEscape(f.name), smallDim), new Paragraph(xmlEscape(m.name), small),
      new Paragraph(xmlEscape(toPrintSafe(ds.label || '')), small), val, src])
  }
  return new Table(data, { colWidths: [0.8 * inch, 1.15 * inch, 1.25 * inch, 1.5 * inch, 1.35 * inch, 1.25 * inch], repeatRows: 1, style: [
    ['FONTSIZE', [0, 0], [-1, -1], 7], ['GRID', [0, 0], [-1, -1], 0.3, HexColor('#e5e8ee')], ['BACKGROUND', [0, 0], [-1, 0], HexColor('#eef2f8')],
    ['VALIGN', [0, 0], [-1, -1], 'TOP']] })
}

function siteHeaderTable(delin: Obj): Table {
  const dl: Obj = (delin ?? {}).delineation ?? {}
  const hdr: CellValue[][] = [['Coordinates', `${pyText(dl, 'snapped_lat')}, ${pyText(dl, 'snapped_lon')}`],
    ['COMID / HUC8', `${pyText(dl, 'comid')} / ${pyText(dl, 'huc8')}`],
    ['Drainage area', `${pyText(dl, 'drainage_area_sqkm')} km2`],
    ['Reach length', `${pyText(dl, 'reach_length_ft')} ft`],
    ['Watershed basis', watershedBasisLabel(delin)],
    ['StreamCat reach', streamcatReachLabel(delin)]]
  if (dl.watershed_area_sqkm !== null && dl.watershed_area_sqkm !== undefined) {
    hdr.splice(3, 0, ['HR reach watershed area', `${pyText(dl, 'watershed_area_sqkm')} km2`])
  }
  return new Table(hdr, { colWidths: [2.3 * inch, 4.4 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 9],
    ['GRID', [0, 0], [-1, -1], 0.3, HexColor('#d5deea')], ['BACKGROUND', [0, 0], [0, -1], HexColor('#eef2f8')]] })
}

/** build_desktop_metrics_pdf: Get Forms' desktop metrics ("SFARI Desktop Metrics") */
export function buildDesktopMetricsPdf(delin: Obj, evidence: Record<string, Evidence>, { invariant = false } = {}): Uint8Array {
  const st = ffStyles()
  const dl: Obj = (delin ?? {}).delineation ?? {}
  const doc = new SimpleDocTemplate({ pagesize: letter, topMargin: 0.6 * inch, bottomMargin: 0.6 * inch, leftMargin: 0.6 * inch,
    rightMargin: 0.6 * inch, title: 'SFARI Desktop Metrics', invariant })
  return doc.build([new Paragraph('SFARI Desktop Metrics', st.base.Title),
    new Paragraph(toPrintSafe(dl.gnis_name || '(unnamed reach)'), st.base.Heading2),
    siteHeaderTable(delin), new Spacer(1, 10), metricsSummaryTable(evidence), new Spacer(1, 8),
    new Paragraph('Available: the value was pulled and is printed beside its function on '
      + 'the field forms. Pending: the STAF site engine is still running. '
      + 'Unavailable: neither engine nor service answered. Local review '
      + 'required and Run cross-section tool: the assessor supplies it. '
      + 'Watershed basis and StreamCat reach above say whose watershed the '
      + 'values describe.', st.smallDim)])
}


export interface SfariPdfOptions {
  invariant?: boolean
  /** the basemap's PNG instead of the fetch (tests); null draws the map without it */
  basemapPng?: Uint8Array | null
}

/** build_pdf: the report ("SFARI Screening Report"); ``scenario`` names the scenario it is for */
export async function buildPdf(delin: Obj, metricScores: Record<string, MetricRecord>, functionScores: Record<string, FunctionRecord>,
  evidence: Record<string, Evidence>, sc: Scored, scenario: [string, string] | null, opts: SfariPdfOptions = {}): Promise<Uint8Array> {
  const doc = new SimpleDocTemplate({ pagesize: letter, topMargin: 0.6 * inch, bottomMargin: 0.6 * inch, leftMargin: 0.6 * inch,
    rightMargin: 0.6 * inch, title: 'SFARI Screening Report', invariant: opts.invariant ?? false })
  const styles = getSampleStyleSheet()
  styles.Heading3.keepWithNext = true
  const small = new ParagraphStyle('small', { parent: styles.BodyText, fontSize: 7, leading: 8.4 })
  const story: Flowable[] = []
  const dl: Obj = (delin ?? {}).delineation ?? {}
  const grid = HexColor('#d5deea')
  const headBg = HexColor('#eef2f8')
  const bandCol: Record<string, string> = { NF: '#f5b5b5', AR: '#f5e7a6', F: '#c8d9f2' }
  const sco = sc as Obj

  story.push(new Paragraph('SFARI Screening Report', styles.Title), new Paragraph(dl.gnis_name || '(unnamed reach)', styles.Heading2))
  if (scenario) {
    const [name, description] = scenario
    story.push(new Paragraph(`<b>Scenario:</b> ${xmlEscape(name)}`, styles.Normal))
    if (description) story.push(new Paragraph(xmlEscape(description), styles.Italic))
  }
  const eci = sco.ecosystemConditionIndex
  const hdr: CellValue[][] = [['Coordinates', `${pyText(dl, 'snapped_lat')}, ${pyText(dl, 'snapped_lon')}`],
    ['COMID / HUC8', `${pyText(dl, 'comid')} / ${pyText(dl, 'huc8')}`],
    ['Drainage area', `${pyText(dl, 'drainage_area_sqkm')} km2`],
    ['Reach length', `${pyText(dl, 'reach_length_ft')} ft`],
    ['Ecosystem Condition Index', eci === null || eci === undefined ? 'None' : floatText(eci)]]
  if (dl.watershed_area_sqkm !== null && dl.watershed_area_sqkm !== undefined) {
    hdr.splice(3, 0, ['HR reach watershed area', `${pyText(dl, 'watershed_area_sqkm')} km2`])
  }
  story.push(new Table(hdr, { colWidths: [2.3 * inch, 4.4 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 9], ['GRID', [0, 0], [-1, -1], 0.3, grid],
    ['BACKGROUND', [0, 0], [0, -1], headBg]] }), new Spacer(1, 10))
  const map = await pdfFlowable(displaySimplify((delin ?? {}).watershed_geojson ?? null, 700), (delin ?? {}).reach_geojson ?? null, 5.0 * inch,
    5.0 * inch * 180 / 290, { png: opts.basemapPng })
  if (map !== null) story.push(new Paragraph('Watershed', styles.Heading3), map, new Spacer(1, 10))
  story.push(new Paragraph('Function scores (0-15)', styles.Heading3))

  const data: CellValue[][] = [['Category', 'Function', 'Score', 'Condition']]
  const bg: Cmd[] = []
  let r = 1
  for (const cat of CATEGORY_ORDER) {
    for (const f of FNS_BY_CAT.get(cat) ?? []) {
      const score = ((functionScores ?? {})[f.id] ?? {}).score
      let band = ''
      let col: string | undefined
      if (score !== null && score !== undefined) {
        band = functionScoreBandLabel(score)
        col = bandCol[band]
      }
      data.push([cat, f.name, score === null || score === undefined ? '' : Number.isInteger(score) ? String(score) : pyFloat(score), band])
      if (col) bg.push(['BACKGROUND', [3, r], [3, r], HexColor(col)])
      r += 1
    }
  }
  story.push(new Table(data, { colWidths: [1.2 * inch, 3.3 * inch, 0.7 * inch, 1.0 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 8],
    ['GRID', [0, 0], [-1, -1], 0.3, grid], ['BACKGROUND', [0, 0], [-1, 0], headBg], ...bg] }), new Spacer(1, 10),
  new Paragraph('Outcome sub-indices', styles.Heading3))

  const si: Obj = sco.subIndices ?? {}
  const num = (v: unknown): CellValue => (v === null || v === undefined ? '' : floatText(v))
  story.push(new Table([['Physical', 'Chemical', 'Biological', 'ECI'], [num(si.physical), num(si.chemical), num(si.biological), num(eci)]], {
    colWidths: new Array(4).fill(1.6 * inch), style: [['FONTSIZE', [0, 0], [-1, -1], 9], ['GRID', [0, 0], [-1, -1], 0.3, grid],
      ['BACKGROUND', [0, 0], [-1, 0], headBg]] }), new Spacer(1, 10), new Paragraph('Metric evidence & Likert scores', styles.Heading3))

  const mdata: CellValue[][] = [['Function', 'Metric', 'Likert', 'Pulled evidence']]
  let hasBorrowed = false
  for (const [, f, m] of orderedMetrics()) {
    const rc: Obj = (metricScores ?? {})[m.metricId] ?? {}
    const ev: Obj = (evidence ?? {})[m.metricId] ?? {}
    const lk = rc.likert ? LIKERT_SHORT[rc.likert] ?? '' : ''
    const borrowed = isBorrowed(ev)
    hasBorrowed = hasBorrowed || borrowed
    const mark = borrowed ? `<super>${BORROWED_MARK}</super>` : ''
    mdata.push([new Paragraph(f.name, small), new Paragraph(m.name, small), lk, new Paragraph((ev.value_text || '') + mark, small)])
  }
  story.push(new Table(mdata, { colWidths: [1.5 * inch, 2.0 * inch, 0.55 * inch, 2.65 * inch], repeatRows: 1, style: [
    ['FONTSIZE', [0, 0], [-1, -1], 7], ['GRID', [0, 0], [-1, -1], 0.3, HexColor('#e5e8ee')], ['BACKGROUND', [0, 0], [-1, 0], headBg],
    ['VALIGN', [0, 0], [-1, -1], 'TOP']] }))
  if (hasBorrowed) {
    const noteStyle = new ParagraphStyle('SourceNote', { parent: styles.BodyText, fontSize: 8, leading: 10, textColor: HexColor('#556070') })
    story.push(new Spacer(1, 4), new Paragraph(`${BORROWED_MARK} ${BORROWED_NOTE}`, noteStyle))
  }
  story.push(new Spacer(1, 8), new Paragraph('Desktop evidence supports scoring; the assessor assigns the Likert and '
    + '0-15 function scores. Likert thresholds are national defaults. Calibrate regionally.', styles.Italic))

  const gallery: Flowable[] = []
  for (const [, f, m] of orderedMetrics()) {
    const ph = ((metricScores ?? {})[m.metricId] ?? {}).photos || []
    const imgs = (await Promise.all(ph.map((p) => photoImage(p.uri, 1.3 * inch, 1.3 * inch)))).filter((im): im is Image => im !== null)
    if (imgs.length) {
      gallery.push(new Paragraph(`<b>${f.name}</b>: ${m.name}`, small),
        new Table([imgs], { colWidths: new Array(imgs.length).fill(1.42 * inch), hAlign: 'LEFT', style: [['LEFTPADDING', [0, 0], [-1, -1], 0],
          ['TOPPADDING', [0, 0], [-1, -1], 1], ['BOTTOMPADDING', [0, 0], [-1, -1], 1]] }), new Spacer(1, 6))
    }
  }
  if (gallery.length) story.push(new Spacer(1, 10), new Paragraph('Site photos', styles.Heading3), ...gallery)
  return doc.build(story)
}

// DEEP's PDFs other than the field worksheet (deep/report.py build_pdf and build_metrics_pdf), laid out
// by the port of reportlab. The report is not built in invariant mode in DEEP (its dates are the
// download's), so ``invariant`` is for tests.
import { pyFixed, pyFloat } from '../../app/pyfmt'
import { xmlEscape } from '../../files/py'
import { HexColor } from '../../files/pdf/colors'
import { inch, letter, SimpleDocTemplate } from '../../files/pdf/doctemplate'
import { type Flowable, Image, Spacer } from '../../files/pdf/flowables'
import { Paragraph } from '../../files/pdf/paragraph'
import { getSampleStyleSheet, ParagraphStyle } from '../../files/pdf/styles'
import { type CellValue, type Cmd, Table, TableStyle } from '../../files/pdf/tables'
import { displaySimplify } from '../../engine/simplify'
import { bundleDigest, lifecycleStatus } from '../../session/deep'
import { photoImage } from '../../shared/photos'
import { pdfFlowable } from '../../shared/reportmapPdf'
import { toPrintSafe } from './fieldform'
import { assessmentName as assessmentNameOf, metricRows, STATUS_AVAILABLE, STATUS_ENTERED, STATUS_REFERENCE_ONLY } from './forms'
import { type Bundle, indexClaim, predictorSourceOf, type Rec, type Scored } from './method'
import {
  coverageLabel, desktopBasisLabel, functionRows, pyStrOf, rows, sourceCellText, streamcatReachLabel, watershedBasisLabel, withheldRows,
} from './report'
import { flaggedSummary, referenceMethodStatement, withheld, withheldNote } from './support'
import { sheetStatusLine, statusLabel } from '../../app/deepStatus'
import { pageMarker, TOP_MARGIN_IN } from './statusmark'

/** str.title() */
function pyTitle(s: string): string {
  let out = ''
  let prev = false
  for (const ch of s) {
    const cased = ch.toUpperCase() !== ch.toLowerCase()
    out += cased ? (prev ? ch.toLowerCase() : ch.toUpperCase()) : ch
    prev = cased
  }
  return out
}

/** str() of a number Python holds as a float (a score, an index) */
const floatText = (v: unknown): string => (typeof v === 'number' ? pyFloat(v) : String(v))


export interface DeepPdfOptions {
  invariant?: boolean
  /** the basemap's PNG instead of the fetch (tests); null draws the map without it */
  basemapPng?: Uint8Array | null
}

/** build_pdf: the report for the scored assessment ("DEEP Detailed Assessment Report"). */
export async function buildPdf(delin: Rec, la: Bundle | null, measured: Record<string, Rec>, sc: Scored, region: Rec | null,
  scenario: [string, string] | null, opts: DeepPdfOptions = {}): Promise<Uint8Array> {
  const markStatus = la && Object.keys(la).length ? lifecycleStatus(la) : null
  const doc = new SimpleDocTemplate({
    pagesize: letter, topMargin: TOP_MARGIN_IN * inch, bottomMargin: 0.6 * inch, leftMargin: 0.6 * inch, rightMargin: 0.6 * inch,
    title: 'DEEP Detailed Assessment Report', invariant: opts.invariant ?? false,
    subject: markStatus ? sheetStatusLine(markStatus) : null,
  })
  const mark = pageMarker(markStatus)
  const styles = getSampleStyleSheet()
  const small = new ParagraphStyle('small', { parent: styles.BodyText, fontSize: 7, leading: 8.4 })
  const grid = HexColor('#d5deea')
  const headBg = HexColor('#eef2f8')
  const dl: Rec = (delin ?? {}).delineation ?? {}
  const story: Flowable[] = [new Paragraph('DEEP Detailed Assessment', styles.Title),
    new Paragraph((la ? assessmentNameOf(la) : '') || 'Detailed assessment', styles.Heading2),
    new Paragraph(dl.gnis_name || '(unnamed reach)', styles.Heading3)]
  if (scenario) {
    const [name, description] = scenario
    story.push(new Paragraph(`<b>Scenario:</b> ${xmlEscape(name)}`, styles.Normal))
    if (description) story.push(new Paragraph(xmlEscape(description), styles.Italic))
  }
  const raw: Rec = la ?? {}
  const version = (raw.library ?? {}).version
  const status = lifecycleStatus(raw)
  const digest = bundleDigest(raw)
  const reg: Rec = region ?? {}
  const st: Rec = reg.state ?? {}
  const l3: Rec = reg.level3 ?? {}
  const stLabel = st.name && st.code ? `${st.name} (${st.code})` : st.name || st.code || ''
  const l3Label = l3.code && l3.name ? `${l3.code} ${l3.name}` : l3.name || l3.code || ''
  const regionParts = [stLabel, l3Label].filter((p) => p)
  const verTxt = version === null || version === undefined ? 'unversioned' : `v${pyStrOf(raw.library, 'version')}`
  const hdr: CellValue[][] = [
    ['Source', String((la ? la.sourceCitation : '') || '')],
    ['Version / status', `${verTxt}  ·  ${statusLabel(status)}`],
    ['Predictor source', predictorSourceOf(la)],
    ['Region match', regionParts.length ? regionParts.join('  ·  ') : 'Not resolved'],
    ['Coordinates', `${pyStrOf(dl, 'snapped_lat')}, ${pyStrOf(dl, 'snapped_lon')}`],
    ['COMID / HUC8', `${pyStrOf(dl, 'comid')} / ${pyStrOf(dl, 'huc8')}`],
    ['Drainage area', `${pyStrOf(dl, 'drainage_area_sqkm')} km2`],
    ['Reach length', `${pyStrOf(dl, 'reach_length_ft')} ft`],
    ['Watershed basis', watershedBasisLabel(delin)],
    ['Desktop values', desktopBasisLabel(measured)],
    ['StreamCat reach', streamcatReachLabel(delin)],
    ['Content digest', digest || '(none)'],
    ['Ecosystem Condition Index', indexClaim(sc)],
    ['STAF function coverage', coverageLabel(la)],
    ['Flagged transfers', new Paragraph(flaggedSummary(la) || 'none', new ParagraphStyle('hdr', { parent: styles.BodyText, fontSize: 9, leading: 10.5 }))],
  ]
  if (dl.watershed_area_sqkm !== null && dl.watershed_area_sqkm !== undefined) {
    hdr.splice(7, 0, ['HR reach watershed area', `${pyStrOf(dl, 'watershed_area_sqkm')} km2`])
  }
  story.push(new Table(hdr, { colWidths: [2.3 * inch, 4.4 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 9], ['GRID', [0, 0], [-1, -1], 0.3, grid],
    ['BACKGROUND', [0, 0], [0, -1], headBg]] }), new Spacer(1, 10))
  const map = await pdfFlowable(displaySimplify((delin ?? {}).watershed_geojson ?? null, 700), (delin ?? {}).reach_geojson ?? null, 5.0 * inch,
    5.0 * inch * 180 / 290, { png: opts.basemapPng })
  if (map !== null) story.push(new Paragraph('Watershed', styles.Heading3), map, new Spacer(1, 10))
  story.push(new Paragraph('Function scores (0-15)', styles.Heading3))

  // the band colors key on long condition names the rows never carry, so no cell is colored (as in DEEP)
  const data: CellValue[][] = [['Function', 'Score', 'Condition']]
  const frows = functionRows(la, sc)
  for (const [name, s, cond] of frows) data.push([name, s === null || s === undefined ? '' : pyFixed(s, 1), cond])
  story.push(new Table(data, { colWidths: [3.9 * inch, 0.8 * inch, 1.8 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 8],
    ['GRID', [0, 0], [-1, -1], 0.3, grid], ['BACKGROUND', [0, 0], [-1, 0], headBg]] }), new Spacer(1, 6))
  const limits = frows.filter((r) => r[3]).map((r) => [r[0], r[3]])
  if (limits.length) {
    story.push(new Paragraph('Function limitations', styles.Heading4))
    for (const [name, limit] of limits) story.push(new Paragraph(`<b>${name}</b>: ${limit}`, small))
    story.push(new Spacer(1, 6))
  }
  story.push(new Spacer(1, 4), new Paragraph('Outcome sub-indices', styles.Heading3))
  const si: Rec = sc.subIndices ?? {}
  const cell = (v: unknown): CellValue => (v === null || v === undefined ? 'not assessed' : floatText(v))
  story.push(new Table([['Physical', 'Chemical', 'Biological', 'ECI'],
    [cell(si.physical), cell(si.chemical), cell(si.biological), cell(sc.ecosystemConditionIndex)]],
  { colWidths: new Array(4).fill(1.6 * inch), style: [['FONTSIZE', [0, 0], [-1, -1], 9], ['GRID', [0, 0], [-1, -1], 0.3, grid],
    ['BACKGROUND', [0, 0], [-1, 0], headBg]] }), new Spacer(1, 10), new Paragraph('Metric measurements & curve indices', styles.Heading3))

  const mdata: CellValue[][] = [['Function', 'Metric', 'Value', 'Index', 'Source', 'Curve (source)']]
  const advisories: string[] = []
  const supportRows = new Map<string, CellValue[]>()
  const all = rows(la, measured)
  for (const [fn, m, val, idx, meta, rawVal] of all) {
    const mname = m.metricName ?? m.metricId
    if (meta.scored_against && !supportRows.has(m.metricId)) {
      supportRows.set(m.metricId, [new Paragraph(mname, small), new Paragraph(meta.scored_against, small), new Paragraph(meta.curve_set, small)])
    }
    const idxTxt = meta.reference_only ? 'ref. only' : idx === null ? '' : pyFixed(idx, 2)
    mdata.push([new Paragraph(fn.functionName ?? '', small), new Paragraph(mname, small),
      val === null || val === undefined ? '' : new Paragraph(pyStrOf(rawVal, 'value'), small), idxTxt,
      new Paragraph(sourceCellText(meta), small), new Paragraph((m.curve ?? {}).layerName ?? '', small)])
    if (meta.advisory) advisories.push(`<b>${mname}</b>: ${meta.advisory}`)
  }
  const tableStyle = (gridColor: string): Cmd[] => [['FONTSIZE', [0, 0], [-1, -1], 7], ['GRID', [0, 0], [-1, -1], 0.3, HexColor(gridColor)],
    ['BACKGROUND', [0, 0], [-1, 0], headBg], ['VALIGN', [0, 0], [-1, -1], 'TOP']]
  story.push(new Table(mdata, { colWidths: [1.3 * inch, 1.7 * inch, 0.7 * inch, 0.55 * inch, 1.6 * inch, 1.2 * inch], repeatRows: 1,
    style: tableStyle('#e5e8ee') }), new Spacer(1, 8))
  if (advisories.length) {
    story.push(new Paragraph('Scoring advisories', styles.Heading4), ...advisories.map((a) => new Paragraph(a, small)), new Spacer(1, 6))
  }
  if (supportRows.size) {
    const statement = referenceMethodStatement(la)
    story.push(new Paragraph('Reference support', styles.Heading3))
    if (statement) story.push(new Paragraph(statement, small), new Spacer(1, 4))
    story.push(new Table([['Metric', 'Scored against', 'Curve set'], ...supportRows.values()], {
      colWidths: [1.8 * inch, 3.75 * inch, 1.5 * inch], repeatRows: 1, style: tableStyle('#e5e8ee') }), new Spacer(1, 8))
  }
  const wrows = withheldRows(la, true)
  if (wrows.length) {
    story.push(new Paragraph('Metrics not scored', styles.Heading3), new Paragraph(withheldNote(withheld(la)), small), new Spacer(1, 4),
      new Table([['Metric', 'Functions', 'Reason'], ...wrows.map((r) => r.map((x) => new Paragraph(x, small)))], {
        colWidths: [2.6 * inch, 2.75 * inch, 1.7 * inch], repeatRows: 1, style: tableStyle('#e5e8ee') }), new Spacer(1, 8))
  }
  story.push(new Paragraph("Scores are computed automatically from the assessment's reference curves. "
    + 'Confirm state/region applicability of the curve source.', styles.Italic))

  const gallery: Flowable[] = []
  for (const [fn, m] of all) {
    const ph: Rec[] = ((measured ?? {})[m.metricId] ?? {}).photos || []
    const imgs = (await Promise.all(ph.map((p) => photoImage(p.uri, 1.3 * inch, 1.3 * inch)))).filter((im): im is Image => im !== null)
    if (imgs.length) {
      gallery.push(new Paragraph(`<b>${fn.functionName ?? ''}</b> - ${m.metricName ?? m.metricId}`, small))
      gallery.push(new Table([imgs], { colWidths: new Array(imgs.length).fill(1.42 * inch), hAlign: 'LEFT', style: [
        ['LEFTPADDING', [0, 0], [-1, -1], 0], ['TOPPADDING', [0, 0], [-1, -1], 1], ['BOTTOMPADDING', [0, 0], [-1, -1], 1]] }), new Spacer(1, 6))
    }
  }
  if (gallery.length) story.push(new Spacer(1, 10), new Paragraph('Site photos', styles.Heading3), ...gallery)
  return doc.build(story, mark, mark)
}

/** build_metrics_pdf: Get Forms' metrics list ("DEEP Metrics"), built in invariant mode as DEEP builds it. */
export function buildMetricsPdf(la: Bundle | null, ref: string, measured: Record<string, Rec> | null, delineation: Rec | null): Uint8Array {
  const safe = (t: unknown) => toPrintSafe(t ? String(t) : '')
  const base = getSampleStyleSheet()
  const small = new ParagraphStyle('m_small', { parent: base.BodyText, fontSize: 7, leading: 8.4 })
  const dim = new ParagraphStyle('m_dim', { parent: small, textColor: HexColor('#66708a') })
  const ital = new ParagraphStyle('m_it', { parent: small, fontName: 'Helvetica-Oblique', textColor: HexColor('#66708a') })
  const headBg = HexColor('#eef2f8')
  const markStatus = la && Object.keys(la).length ? lifecycleStatus(la) : null
  const doc = new SimpleDocTemplate({
    pagesize: letter, topMargin: TOP_MARGIN_IN * inch, bottomMargin: 0.6 * inch, leftMargin: 0.6 * inch, rightMargin: 0.6 * inch, title: 'DEEP Metrics',
    invariant: true, subject: markStatus ? sheetStatusLine(markStatus) : null,
  })
  const mark = pageMarker(markStatus)
  const name = (la ? assessmentNameOf(la) : '') || 'Detailed assessment'
  const dl: Rec = (delineation ?? {}).delineation || {}
  const heading = name + (ref ? `  (${ref})` : '') + (markStatus ? `  |  ${statusLabel(markStatus)}` : '')
  const story: Flowable[] = [new Paragraph('DEEP Metrics', base.Title), new Paragraph(xmlEscape(safe(heading)), base.Heading2)]
  if (Object.keys(dl).length) {
    const hdr: CellValue[][] = [['Stream', safe(dl.gnis_name || '(unnamed reach)')],
      ['Coordinates', `${pyStrOf(dl, 'snapped_lat')}, ${pyStrOf(dl, 'snapped_lon')}`],
      ['COMID / HUC8', `${pyStrOf(dl, 'comid')} / ${pyStrOf(dl, 'huc8')}`],
      ['Drainage area', `${pyStrOf(dl, 'drainage_area_sqkm')} km2`],
      ['Reach length', `${pyStrOf(dl, 'reach_length_ft')} ft`],
      ['Watershed basis', safe(watershedBasisLabel(delineation))],
      ['StreamCat reach', safe(streamcatReachLabel(delineation))]]
    story.push(new Table(hdr, { colWidths: [2.3 * inch, 4.4 * inch], style: [['FONTSIZE', [0, 0], [-1, -1], 9],
      ['GRID', [0, 0], [-1, -1], 0.3, HexColor('#d5deea')], ['BACKGROUND', [0, 0], [0, -1], headBg]] }), new Spacer(1, 10))
  }
  const data: CellValue[][] = [['Function', 'Metric', 'F/D', 'How it is measured', 'Status', 'Value', 'Source']]
  for (const r of metricRows(la, measured ?? {})) {
    const metric = xmlEscape(safe(r.metric)) + (r.units ? ` (${xmlEscape(safe(r.units))})` : '')
    const isValue = [STATUS_AVAILABLE, STATUS_REFERENCE_ONLY, STATUS_ENTERED].includes(r.status)
    data.push([new Paragraph(xmlEscape(safe(r.function)), dim), new Paragraph(metric, small), r.code, new Paragraph(xmlEscape(safe(r.method)), small),
      new Paragraph(xmlEscape(r.status), isValue ? small : ital), new Paragraph(xmlEscape(safe(r.value)), small),
      new Paragraph(xmlEscape(safe(r.source)), small)])
  }
  story.push(new Table(data, { colWidths: [1.15 * inch, 1.35 * inch, 0.4 * inch, 1.95 * inch, 0.85 * inch, 0.6 * inch, 1.0 * inch], repeatRows: 1,
    style: [['FONTSIZE', [0, 0], [-1, -1], 7], ['GRID', [0, 0], [-1, -1], 0.3, HexColor('#e5e8ee')], ['BACKGROUND', [0, 0], [-1, 0], headBg],
      ['VALIGN', [0, 0], [-1, -1], 'TOP']] }), new Spacer(1, 8),
  new Paragraph('F is measured in the field and D is answered from the desk. Available: '
    + 'the value was pulled and is printed on the field worksheet. Unavailable: '
    + 'no source answered, so measure or estimate it on site. Reference only: '
    + 'the value is shown and kept out of the score, because the curve was '
    + 'fitted on another data source. Not scored: the assessment withholds the '
    + 'metric for insufficient reference support.', dim))
  return doc.build(story, mark, mark)
}

// Scatter charts (straight lines with markers) for reference curves, and the drawing that places them on
// a sheet (libs/staf_workbook xlsx/charts.py). Each series points at cells on the sheet and carries a
// cached copy of the values, so the chart draws even before Excel recalculates.
import { pyFloat } from '../../app/pyfmt'
import { xmlEscape } from '../py'
import { CT_CHART, CT_DRAWING, Package, R_CHART, R_DRAWING, rel, type Rel } from './package'

const NS_C = 'http://schemas.openxmlformats.org/drawingml/2006/chart'
const NS_A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
const NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
const NS_XDR = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
const PALETTE = ['1F5F99', 'C0582B', '3C8D5A', '7A5195', 'B8860B', '5F6B73']

export interface Series { name: string, xRef: string, yRef: string, x: (number | null)[], y: (number | null)[] }

export interface ScatterChart {
  title: string
  xLabel: string
  yLabel: string
  series: Series[]
  yMin: number | null
  yMax: number | null
  /** from col, from row, to col, to row (zero-based) */
  anchor: [number, number, number, number]
}

export const scatterChart = (title: string, xLabel: string, yLabel: string, series: Series[] = [],
  anchor: [number, number, number, number] = [0, 0, 8, 16]): ScatterChart => ({ title, xLabel, yLabel, series, yMin: 0.0, yMax: 1.0, anchor })

function rich(text: string, size = 1000, bold = false): string {
  const b = bold ? ' b="1"' : ' b="0"'
  return `<c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="${size}"${b}/></a:pPr>`
    + `<a:r><a:rPr lang="en-US" sz="${size}"${b}/><a:t>${xmlEscape(text)}</a:t></a:r></a:p></c:rich></c:tx>`
}

function cache(values: (number | null)[]): string {
  const pts = values.map((v, i) => (v === null || v === undefined ? '' : `<c:pt idx="${i}"><c:v>${pyFloat(Number(v))}</c:v></c:pt>`)).join('')
  return `<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="${values.length}"/>${pts}</c:numCache>`
}

export function chartXml(ch: ScatterChart): string {
  const sers = ch.series.map((s, i) => {
    const color = PALETTE[i % PALETTE.length]
    return `<c:ser><c:idx val="${i}"/><c:order val="${i}"/><c:tx><c:v>${xmlEscape(s.name)}</c:v></c:tx>`
      + `<c:spPr><a:ln w="22225" cap="rnd"><a:solidFill><a:srgbClr val="${color}"/></a:solidFill></a:ln></c:spPr>`
      + `<c:marker><c:symbol val="circle"/><c:size val="5"/><c:spPr><a:solidFill><a:srgbClr val="${color}"/>`
      + '</a:solidFill><a:ln><a:noFill/></a:ln></c:spPr></c:marker>'
      + `<c:xVal><c:numRef><c:f>${xmlEscape(s.xRef)}</c:f>${cache(s.x)}</c:numRef></c:xVal>`
      + `<c:yVal><c:numRef><c:f>${xmlEscape(s.yRef)}</c:f>${cache(s.y)}</c:numRef></c:yVal>`
      + '<c:smooth val="0"/></c:ser>'
  })
  let scalingY = '<c:orientation val="minMax"/>'
  if (ch.yMax !== null) scalingY += `<c:max val="${pyFloat(ch.yMax)}"/>`
  if (ch.yMin !== null) scalingY += `<c:min val="${pyFloat(ch.yMin)}"/>`
  const grid = '<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="E3E7EB"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>'
  const axisLine = '<c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="9AA5AE"/></a:solidFill></a:ln></c:spPr>'
  const tickText = '<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="800"/></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>'
  const legend = ch.series.length > 1 ? '<c:legend><c:legendPos val="b"/><c:overlay val="0"/>' + tickText + '</c:legend>' : ''
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    + `<c:chartSpace xmlns:c="${NS_C}" xmlns:a="${NS_A}" xmlns:r="${NS_R}"><c:roundedCorners val="0"/><c:chart>`
    + `<c:title>${rich(ch.title, 1000, true)}<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>`
    + '<c:plotArea><c:layout/>'
    + `<c:scatterChart><c:scatterStyle val="lineMarker"/><c:varyColors val="0"/>${sers.join('')}`
    + '<c:axId val="510001"/><c:axId val="510002"/></c:scatterChart>'
    + '<c:valAx><c:axId val="510001"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/>'
    + `<c:axPos val="b"/><c:title>${rich(ch.xLabel, 800)}<c:overlay val="0"/></c:title>`
    + '<c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
    + `<c:tickLblPos val="nextTo"/>${axisLine}${tickText}<c:crossAx val="510002"/><c:crosses val="autoZero"/>`
    + '<c:crossBetween val="midCat"/></c:valAx>'
    + `<c:valAx><c:axId val="510002"/><c:scaling>${scalingY}</c:scaling><c:delete val="0"/><c:axPos val="l"/>`
    + `${grid}<c:title>${rich(ch.yLabel, 800)}<c:overlay val="0"/></c:title>`
    + '<c:numFmt formatCode="0.0" sourceLinked="0"/><c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
    + `<c:tickLblPos val="nextTo"/>${axisLine}${tickText}<c:crossAx val="510001"/><c:crosses val="autoZero"/>`
    + '<c:crossBetween val="midCat"/></c:valAx>'
    + '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr></c:plotArea>'
    + `${legend}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>`
    + '<c:spPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln w="6350"><a:solidFill>'
    + '<a:srgbClr val="D5DBE0"/></a:solidFill></a:ln></c:spPr></c:chartSpace>'
}

/** add_charts: the charts on ``sheetPart`` (a new drawing); the sheet's drawing rId. */
export function addCharts(pkg: Package, sheetPart: string, charts: ScatterChart[]): string | null {
  if (!charts.length) return null
  const drawing = pkg.unique('xl/drawings/drawing{}.xml')
  const anchors: string[] = []
  const drels: Rel[] = []
  charts.forEach((ch, k) => {
    const i = k + 1
    const part = pkg.unique('xl/charts/chart{}.xml')
    pkg.add(part, new TextEncoder().encode(chartXml(ch)), CT_CHART)
    const rid = `rId${i}`
    drels.push(rel(rid, R_CHART, Package.relative(drawing, part)))
    const [c1, r1, c2, r2] = ch.anchor
    anchors.push(
      `<xdr:twoCellAnchor editAs="oneCell"><xdr:from><xdr:col>${c1}</xdr:col><xdr:colOff>0</xdr:colOff>`
      + `<xdr:row>${r1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>${c2}</xdr:col>`
      + `<xdr:colOff>0</xdr:colOff><xdr:row>${r2}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>`
      + `<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="${i + 1}" name="Curve ${i}"/>`
      + '<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
      + `</xdr:xfrm><a:graphic><a:graphicData uri="${NS_C}"><c:chart xmlns:c="${NS_C}" r:id="${rid}"/>`
      + '</a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor>')
  })
  const xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    + `<xdr:wsDr xmlns:xdr="${NS_XDR}" xmlns:a="${NS_A}" xmlns:r="${NS_R}">${anchors.join('')}</xdr:wsDr>`
  pkg.add(drawing, new TextEncoder().encode(xml), CT_DRAWING)
  pkg.writeRels(drawing, drels)
  const rels = pkg.rels(sheetPart)
  const rid = Package.nextRelId(rels)
  rels.push(rel(rid, R_DRAWING, Package.relative(sheetPart, drawing)))
  pkg.writeRels(sheetPart, rels)
  return rid
}

"""Scatter charts (straight lines with markers) for reference curves, and the drawing that places
them on a sheet. Each series points at cells on the sheet and carries a cached copy of the values,
so the chart draws even before Excel recalculates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape

from .package import CT_CHART, CT_DRAWING, R_CHART, R_DRAWING, Package, Rel

NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
PALETTE = ("1F5F99", "C0582B", "3C8D5A", "7A5195", "B8860B", "5F6B73")


@dataclass
class Series:
    name: str
    x_ref: str                  # "'ReferenceCurves'!$B$5:$B$9"
    y_ref: str
    x: list
    y: list


@dataclass
class ScatterChart:
    title: str
    x_label: str
    y_label: str
    series: list = field(default_factory=list)
    y_min: Optional[float] = 0.0
    y_max: Optional[float] = 1.0
    anchor: tuple = (0, 0, 8, 16)     # from col, from row, to col, to row (zero-based)


def _rich(text: str, size: int = 1000, bold: bool = False) -> str:
    b = ' b="1"' if bold else ' b="0"'
    return (f'<c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="{size}"{b}/></a:pPr>'
            f'<a:r><a:rPr lang="en-US" sz="{size}"{b}/><a:t>{escape(text)}</a:t></a:r></a:p></c:rich></c:tx>')


def _cache(values: list) -> str:
    pts = "".join(f'<c:pt idx="{i}"><c:v>{float(v)!r}</c:v></c:pt>' for i, v in enumerate(values) if v is not None)
    return f"<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val=\"{len(values)}\"/>{pts}</c:numCache>"


def chart_xml(ch: ScatterChart) -> str:
    sers = []
    for i, s in enumerate(ch.series):
        color = PALETTE[i % len(PALETTE)]
        sers.append(
            f'<c:ser><c:idx val="{i}"/><c:order val="{i}"/><c:tx><c:v>{escape(s.name)}</c:v></c:tx>'
            f'<c:spPr><a:ln w="22225" cap="rnd"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr>'
            f'<c:marker><c:symbol val="circle"/><c:size val="5"/><c:spPr><a:solidFill><a:srgbClr val="{color}"/>'
            f"</a:solidFill><a:ln><a:noFill/></a:ln></c:spPr></c:marker>"
            f"<c:xVal><c:numRef><c:f>{escape(s.x_ref)}</c:f>{_cache(s.x)}</c:numRef></c:xVal>"
            f"<c:yVal><c:numRef><c:f>{escape(s.y_ref)}</c:f>{_cache(s.y)}</c:numRef></c:yVal>"
            '<c:smooth val="0"/></c:ser>')
    scaling_y = '<c:orientation val="minMax"/>'
    if ch.y_max is not None:
        scaling_y += f'<c:max val="{ch.y_max!r}"/>'
    if ch.y_min is not None:
        scaling_y += f'<c:min val="{ch.y_min!r}"/>'
    grid = '<c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="E3E7EB"/></a:solidFill></a:ln></c:spPr></c:majorGridlines>'
    axis_line = '<c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="9AA5AE"/></a:solidFill></a:ln></c:spPr>'
    tick_text = '<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="800"/></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>'
    legend = ('<c:legend><c:legendPos val="b"/><c:overlay val="0"/>' + tick_text + "</c:legend>") if len(ch.series) > 1 else ""
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<c:chartSpace xmlns:c="{NS_C}" xmlns:a="{NS_A}" xmlns:r="{NS_R}"><c:roundedCorners val="0"/><c:chart>'
            f'<c:title>{_rich(ch.title, 1000, True)}<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
            "<c:plotArea><c:layout/>"
            f'<c:scatterChart><c:scatterStyle val="lineMarker"/><c:varyColors val="0"/>{"".join(sers)}'
            '<c:axId val="510001"/><c:axId val="510002"/></c:scatterChart>'
            '<c:valAx><c:axId val="510001"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/>'
            f'<c:axPos val="b"/><c:title>{_rich(ch.x_label, 800)}<c:overlay val="0"/></c:title>'
            f'<c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
            f'<c:tickLblPos val="nextTo"/>{axis_line}{tick_text}<c:crossAx val="510002"/><c:crosses val="autoZero"/>'
            '<c:crossBetween val="midCat"/></c:valAx>'
            f'<c:valAx><c:axId val="510002"/><c:scaling>{scaling_y}</c:scaling><c:delete val="0"/><c:axPos val="l"/>'
            f'{grid}<c:title>{_rich(ch.y_label, 800)}<c:overlay val="0"/></c:title>'
            '<c:numFmt formatCode="0.0" sourceLinked="0"/><c:majorTickMark val="out"/><c:minorTickMark val="none"/>'
            f'<c:tickLblPos val="nextTo"/>{axis_line}{tick_text}<c:crossAx val="510001"/><c:crosses val="autoZero"/>'
            '<c:crossBetween val="midCat"/></c:valAx>'
            '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr></c:plotArea>'
            f'{legend}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>'
            '<c:spPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln w="6350"><a:solidFill>'
            '<a:srgbClr val="D5DBE0"/></a:solidFill></a:ln></c:spPr></c:chartSpace>')


def add_charts(pkg: Package, sheet_part: str, charts: list) -> Optional[str]:
    """Add the charts to ``sheet_part`` (a new drawing); returns the sheet's drawing rId."""
    if not charts:
        return None
    drawing = pkg.unique("xl/drawings/drawing{}.xml")
    anchors, drels = [], []
    for i, ch in enumerate(charts, start=1):
        part = pkg.unique("xl/charts/chart{}.xml")
        pkg.add(part, chart_xml(ch).encode("utf-8"), CT_CHART)
        rid = f"rId{i}"
        drels.append(Rel(rid, R_CHART, Package.relative(drawing, part)))
        c1, r1, c2, r2 = ch.anchor
        anchors.append(
            f'<xdr:twoCellAnchor editAs="oneCell"><xdr:from><xdr:col>{c1}</xdr:col><xdr:colOff>0</xdr:colOff>'
            f'<xdr:row>{r1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from><xdr:to><xdr:col>{c2}</xdr:col>'
            f'<xdr:colOff>0</xdr:colOff><xdr:row>{r2}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
            f'<xdr:graphicFrame macro=""><xdr:nvGraphicFramePr><xdr:cNvPr id="{i + 1}" name="Curve {i}"/>'
            '<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr><xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            f'</xdr:xfrm><a:graphic><a:graphicData uri="{NS_C}"><c:chart xmlns:c="{NS_C}" r:id="{rid}"/>'
            "</a:graphicData></a:graphic></xdr:graphicFrame><xdr:clientData/></xdr:twoCellAnchor>")
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
           f'<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" xmlns:r="{NS_R}">{"".join(anchors)}</xdr:wsDr>')
    pkg.add(drawing, xml.encode("utf-8"), CT_DRAWING)
    pkg.write_rels(drawing, drels)
    rels = pkg.rels(sheet_part)
    rid = Package.next_rel_id(rels)
    rels.append(Rel(rid, R_DRAWING, Package.relative(sheet_part, drawing)))
    pkg.write_rels(sheet_part, rels)
    return rid

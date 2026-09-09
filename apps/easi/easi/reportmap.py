"""The report's watershed map: the outline and reach over a USGS topo basemap.

Byte-identical in EASI, SFARI and DEEP (the ``comid_anchor`` / ``network_display``
convention). It takes GeoJSON that the caller has already simplified, so it needs
nothing from any app's own modules and the three copies can stay the same file.

The basemap is one request, not a tile mosaic: the USGS MapServer draws a whole
bounding box in a single call, so there is no slippy-tile arithmetic and no
stitching. Everything is done in Web Mercator, the projection the service draws
in, because the outline has to land on the right valley: the older thumbnail used
an equirectangular fit with a cosine-of-latitude stretch, which is close at this
size but is not what the raster is drawn in.

Nothing here raises. A refused or slow service, or a desktop with no network,
returns no image and the map falls back to the plain outline it drew before.

No new dependency: the browser gets an inline SVG with the PNG as a data URI, and
the PDF gets a reportlab flowable that draws the image on the canvas and strokes
the rings over it. Rendering the composite with matplotlib would have been the
obvious route but DEEP does not have matplotlib, and SFARI's exports are
deliberately matplotlib-free so Connect never triggers its font-cache build.
"""
from __future__ import annotations

import base64
import math
from functools import lru_cache
from typing import Optional

import requests

#: USGS's own service, the same basemap the interactive map shows.
EXPORT_URL = ("https://basemap.nationalmap.gov/arcgis/rest/services/"
              "USGSTopo/MapServer/export")

WATERSHED_FILL = "#fdf24a"
WATERSHED_STROKE = "#caa700"
REACH_STROKE = "#d6453d"

#: Fraction of the extent left as margin around the geometry.
PAD = 0.08

#: Smallest frame we will ask for, in metres. The USGS caches stop around zoom
#: 16 (the interactive maps pin max_native_zoom=16 for the same reason), so a
#: small headwater basin would otherwise ask for a scale the service cannot
#: draw and come back blank. Widening the frame costs a little context and
#: keeps the basemap.
MIN_EXTENT_M = 1200.0

_R = 6378137.0          # WGS84 semi-major axis, the Web Mercator sphere


def mercator(lon: float, lat: float) -> tuple[float, float]:
    """Lon/lat degrees to EPSG:3857 metres."""
    lat = max(-85.05112878, min(85.05112878, float(lat)))
    return (_R * math.radians(float(lon)),
            _R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def rings(gj) -> list:
    """Every coordinate ring in a FeatureCollection, polygons and lines alike."""
    out = []
    for ft in (gj or {}).get("features", []):
        g = ft.get("geometry") or {}
        t, c = g.get("type"), g.get("coordinates")
        if not c:
            continue
        if t == "Polygon":
            out += list(c)
        elif t == "MultiPolygon":
            out += [r for poly in c for r in poly]
        elif t == "LineString":
            out.append(c)
        elif t == "MultiLineString":
            out += list(c)
    return out


def frame(watershed_gj, reach_gj, w: int, h: int):
    """``(bbox_3857, project)`` covering both inputs, or None if there is none.

    The box is padded, then widened to the card's aspect so the basemap fills
    the frame rather than sitting in a letterbox, and never smaller than
    ``MIN_EXTENT_M``. ``project`` maps lon/lat to the card's pixel space.
    """
    pts = [p for ring in rings(watershed_gj) + rings(reach_gj) for p in ring]
    if not pts:
        return None
    xy = [mercator(p[0], p[1]) for p in pts]
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    bw = max((max(xs) - min(xs)) * (1 + PAD), MIN_EXTENT_M)
    bh = max((max(ys) - min(ys)) * (1 + PAD), MIN_EXTENT_M)
    if bw / bh < w / h:                      # widen or heighten to the card
        bw = bh * (w / h)
    else:
        bh = bw / (w / h)
    bbox = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)

    def project(lon, lat):
        x, y = mercator(lon, lat)
        return ((x - bbox[0]) / (bbox[2] - bbox[0]) * w,
                (bbox[3] - y) / (bbox[3] - bbox[1]) * h)     # SVG y grows down

    return bbox, project


@lru_cache(maxsize=32)
def _fetch(bbox: tuple, size: tuple, timeout: float) -> Optional[bytes]:
    params = {"bbox": ",".join(f"{v:.2f}" for v in bbox), "bboxSR": "3857",
              "imageSR": "3857", "size": f"{size[0]},{size[1]}",
              "format": "png", "transparent": "false", "f": "image"}
    try:
        r = requests.get(EXPORT_URL, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        if not (r.content or b"").startswith(b"\x89PNG"):    # an error page, not a map
            return None
        return r.content
    except Exception:  # noqa: BLE001 - resilience by design
        return None


def topo_png(bbox, size=(580, 360), timeout: float = 8.0) -> Optional[bytes]:
    """The basemap for a Mercator bbox, or None if the service did not answer.

    Memoised on the rounded box, so opening the report twice, or exporting the
    PDF after looking at it, costs one request.
    """
    key = tuple(round(float(v), 1) for v in bbox)
    return _fetch(key, (int(size[0]), int(size[1])), float(timeout))


def _paths(watershed_gj, reach_gj, project) -> list:
    out = []
    for ring in rings(watershed_gj):
        d = "M" + " L".join("%.1f,%.1f" % project(p[0], p[1]) for p in ring) + " Z"
        out.append(f'<path d="{d}" fill="{WATERSHED_FILL}" fill-opacity="0.35" '
                   f'stroke="{WATERSHED_STROKE}" stroke-width="1"/>')
    for ring in rings(reach_gj):
        d = "M" + " L".join("%.1f,%.1f" % project(p[0], p[1]) for p in ring)
        out.append(f'<path d="{d}" fill="none" stroke="{REACH_STROKE}" stroke-width="2.4"/>')
    return out


def svg(watershed_gj, reach_gj, w: int = 290, h: int = 180, *,
        basemap: bool = True, timeout: float = 8.0) -> str:
    """The report thumbnail as inline SVG, or ``""`` when there is no geometry.

    The empty string is the contract the call sites branch on
    (``ui.HTML(minimap) if minimap else None``), so a site with no watershed
    drops the element rather than drawing an empty card.
    """
    fr = frame(watershed_gj, reach_gj, w, h)
    if fr is None:
        return ""
    bbox, project = fr
    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
             f'class="sfari-minimap" style="width:{w}px;max-width:100%;">']
    png = topo_png(bbox, (w * 2, h * 2), timeout) if basemap else None
    if png:
        href = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        parts.append(f'<image x="0" y="0" width="{w}" height="{h}" '
                     f'preserveAspectRatio="none" href="{href}"/>')
    parts += _paths(watershed_gj, reach_gj, project)
    parts.append("</svg>")
    return "".join(parts)


def pdf_flowable(watershed_gj, reach_gj, width, height, *,
                 basemap: bool = True, timeout: float = 8.0):
    """The same map as a reportlab flowable, or None when there is no geometry.

    reportlab's ``shapes.Image`` wants a real file path and refuses an in-memory
    PNG at draw time, so this draws on the canvas instead, which is what the
    report photo helpers already do.
    """
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Flowable

    fr = frame(watershed_gj, reach_gj, width, height)
    if fr is None:
        return None
    bbox, _ = fr
    png = topo_png(bbox, (580, 360), timeout) if basemap else None
    ws = rings(watershed_gj)
    rc = rings(reach_gj)

    class _Map(Flowable):
        def __init__(self):
            super().__init__()
            self.width, self.height = width, height

        def draw(self):
            import io

            c = self.canv
            if png:
                c.drawImage(ImageReader(io.BytesIO(png)), 0, 0,
                            width=self.width, height=self.height, mask=None)
            x0, y0, x1, y1 = bbox

            def at(lon, lat):
                x, y = mercator(lon, lat)
                return ((x - x0) / (x1 - x0) * self.width,
                        (y - y0) / (y1 - y0) * self.height)   # PDF y grows up

            def stroke(ring, close):
                if len(ring) < 2:
                    return
                p = c.beginPath()
                p.moveTo(*at(ring[0][0], ring[0][1]))
                for pt in ring[1:]:
                    p.lineTo(*at(pt[0], pt[1]))
                if close:
                    p.close()
                c.drawPath(p, stroke=1, fill=0)

            c.setLineWidth(1)
            c.setStrokeColor(WATERSHED_STROKE)
            for ring in ws:
                stroke(ring, True)
            c.setLineWidth(1.6)
            c.setStrokeColor(REACH_STROKE)
            for ring in rc:
                stroke(ring, False)
            if not png:                       # no basemap: frame the empty card
                c.setLineWidth(0.5)
                c.setStrokeColor("#c9d2de")
                c.rect(0, 0, self.width, self.height, stroke=1, fill=0)

    return _Map()

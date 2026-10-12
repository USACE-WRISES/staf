// reportmap.pdf_flowable: the report's watershed map as a PDF flowable (the basemap drawn as an image,
// the outline and reach stroked over it; a frame and a caption when the basemap did not load). Kept apart
// from reportmap.ts so the report modal does not load the PDF engine.
import { Flowable } from '../files/pdf/flowables'
import { decodePng, type ImageData } from '../files/pdf/images'
import { type BBox, frame, mercator, REACH_STROKE, rings, topoPng, UNAVAILABLE_NOTE, WATERSHED_STROKE } from './reportmap'

class MapFlowable extends Flowable {
  constructor(readonly bbox: BBox, readonly png: ImageData | null, readonly ws: number[][][], readonly rc: number[][][], readonly mapWidth: number,
    readonly mapHeight: number, readonly captionHeight: number) {
    super()
    this.hAlign = 'LEFT'
    this.width = mapWidth
    this.height = mapHeight + captionHeight
  }

  draw(): void {
    const c = this.canv!
    const { mapWidth: width, mapHeight: height, captionHeight } = this
    if (this.png) c.drawImage(this.png, 0, captionHeight, width, height, null)
    const [x0, y0, x1, y1] = this.bbox
    const at = (lon: number, lat: number): [number, number] => {
      const [x, y] = mercator(lon, lat)
      return [(x - x0) / (x1 - x0) * width, captionHeight + (y - y0) / (y1 - y0) * height]
    }
    const stroke = (ring: number[][], close: boolean) => {
      if (ring.length < 2) return
      const p = c.beginPath()
      p.moveTo(...at(ring[0][0], ring[0][1]))
      for (const pt of ring.slice(1)) p.lineTo(...at(pt[0], pt[1]))
      if (close) p.close()
      c.drawPath(p, 1, 0)
    }
    c.setLineWidth(1)
    c.setStrokeColor(WATERSHED_STROKE)
    for (const ring of this.ws) stroke(ring, true)
    c.setLineWidth(1.6)
    c.setStrokeColor(REACH_STROKE)
    for (const ring of this.rc) stroke(ring, false)
    if (!this.png) {
      c.setLineWidth(0.5)
      c.setStrokeColor('#c9d2de')
      c.rect(0, captionHeight, width, height, 1, 0)
    }
    if (captionHeight) {
      c.setFont('Helvetica', 7.5)
      c.setFillColor('#667085')
      c.drawString(0, 2, UNAVAILABLE_NOTE)
    }
  }
}

/** pdf_flowable: the map at ``width`` x ``height`` points, or null without geometry. ``png`` replaces the
 * fetch (tests); otherwise the basemap is the report modal's (topoPng keeps it). */
export async function pdfFlowable(ws: any, reach: any, width: number, height: number,
  { basemap = true, timeout = 12.0, png = undefined as Uint8Array | null | undefined } = {}): Promise<Flowable | null> {
  const fr = frame(ws, reach, width, height)
  if (!fr) return null
  const bytes = png !== undefined ? png : basemap ? await topoPng(fr.bbox, [580, 360], timeout) : null
  const img = bytes ? decodePng(bytes) : null
  const captionHeight = basemap && !img ? 12 : 0
  return new MapFlowable(fr.bbox, img, rings(ws), rings(reach), width, height, captionHeight)
}

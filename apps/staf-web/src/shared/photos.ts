// The reports' site photos (each tool's _img): a data-address photo as reportlab's Image flowable takes it,
// scaled into its box. A PNG is decoded as PIL decodes it; a JPEG is drawn as its own bytes, as reportlab
// draws it, and named (an MD5 of its decoded samples) from the samples Pillow decodes (files/pdf/jpeg.ts),
// so the XObject's name is the Shiny PDF's. A JPEG that decoder declines (arithmetic coding, lossless,
// a progressive file libjpeg smooths) is named from the browser's decode instead: the picture is the same,
// its name may differ. What PIL cannot open is left out, as the Shiny report leaves it out.
import { fromBase64 } from '../files/bytes'
import { Image } from '../files/pdf/flowables'
import { decodePng, type ImageData, jpegImage } from '../files/pdf/images'
import { decodeJpeg } from '../files/pdf/jpeg'

/** A JPEG's samples as the browser decodes them, in the JPEG's own orientation (zeros where it cannot). */
async function jpegSamples(jpeg: Uint8Array, width: number, height: number, comps: number): Promise<Uint8Array> {
  const out = new Uint8Array(width * height * comps)
  if (typeof createImageBitmap !== 'function' || typeof OffscreenCanvas !== 'function') return out
  try {
    const bmp = await createImageBitmap(new Blob([jpeg as BlobPart], { type: 'image/jpeg' }),
      { imageOrientation: 'none' as ImageOrientation, colorSpaceConversion: 'none', premultiplyAlpha: 'none' })
    if (bmp.width !== width || bmp.height !== height || (comps !== 1 && comps !== 3)) return out
    const c = new OffscreenCanvas(width, height)
    const ctx = c.getContext('2d')!
    ctx.drawImage(bmp, 0, 0)
    const rgba = ctx.getImageData(0, 0, width, height).data
    for (let i = 0, j = 0; i < rgba.length; i += 4) {
      if (comps === 1) out[j++] = rgba[i]
      else {
        out[j++] = rgba[i]
        out[j++] = rgba[i + 1]
        out[j++] = rgba[i + 2]
      }
    }
  } catch {
    // the samples stay zeros: only the XObject's name depends on them
  }
  return out
}

/** _img: the photo at its size, at most maxW x maxH points (never enlarged); null when it cannot be read. */
export async function photoImage(uri: unknown, maxW: number, maxH: number): Promise<Image | null> {
  if (typeof uri !== 'string' || !uri.includes(',')) return null
  try {
    const raw = fromBase64(uri.slice(uri.indexOf(',') + 1).replace(/[^A-Za-z0-9+/=]/g, ''))
    let img: ImageData
    if (raw[0] === 0x89 && raw[1] === 0x50) img = decodePng(raw)
    else if (raw[0] === 0xff && raw[1] === 0xd8) {
      const probe = jpegImage(raw, new Uint8Array(0))   // its header, as PIL opens it
      let samples: Uint8Array
      try {
        samples = decodeJpeg(raw).data
      } catch {
        samples = await jpegSamples(raw, probe.width, probe.height, probe.mode === 'L' ? 1 : probe.mode === 'RGB' ? 3 : 4)
      }
      img = jpegImage(raw, samples)
    } else return null
    const s = Math.min(maxW / img.width, maxH / img.height, 1.0)
    return new Image(img, img.width * s, img.height * s)
  } catch {
    return null
  }
}

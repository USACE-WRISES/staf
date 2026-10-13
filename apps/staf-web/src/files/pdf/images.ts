// Images as reportlab's ImageReader hands them to the PDF (lib/utils.py getRGBData with PIL): a PNG decoded
// to its samples, its alpha split off as an image of its own (the soft mask), a palette or 1-bit image
// converted to RGB; a JPEG drawn as its own bytes. The samples are what reportlab digests for the
// XObject's name and deflates for its stream.
import { inflateRaw } from 'pako'
import { deflateZlib } from './zlib'

export interface ImageData {
  width: number
  height: number
  mode: 'L' | 'RGB' | 'CMYK'
  /** getRGBData(): the samples, row by row */
  data: Uint8Array
  /** the alpha band as its own image (mode L) */
  alpha: ImageData | null
  /** a JPEG's own bytes, drawn as they are */
  jpeg: Uint8Array | null
  /** zlib.compress(data), made once */
  compressed(): Uint8Array
}

function makeImage(width: number, height: number, mode: ImageData['mode'], data: Uint8Array, alpha: ImageData | null = null,
  jpeg: Uint8Array | null = null): ImageData {
  let z: Uint8Array | null = null
  return { width, height, mode, data, alpha, jpeg, compressed: () => (z ??= deflateZlib(data)) }
}

const be32 = (b: Uint8Array, o: number) => ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0

/** A PNG's pixels, as PIL decodes and reportlab converts them. */
export function decodePng(png: Uint8Array): ImageData {
  if (png[0] !== 0x89 || png[1] !== 0x50 || png[2] !== 0x4e || png[3] !== 0x47) throw new Error('not a PNG')
  let width = 0
  let height = 0
  let depth = 0
  let ctype = 0
  let interlace = 0
  let palette: Uint8Array | null = null
  let trns: Uint8Array | null = null
  const idat: Uint8Array[] = []
  for (let p = 8; p < png.length;) {
    const len = be32(png, p)
    const type = String.fromCharCode(png[p + 4], png[p + 5], png[p + 6], png[p + 7])
    const body = png.subarray(p + 8, p + 8 + len)
    if (type === 'IHDR') {
      width = be32(body, 0)
      height = be32(body, 4)
      depth = body[8]
      ctype = body[9]
      interlace = body[12]
    } else if (type === 'PLTE') palette = body.slice()
    else if (type === 'tRNS') trns = body.slice()
    else if (type === 'IDAT') idat.push(body)
    else if (type === 'IEND') break
    p += 12 + len
  }
  const z = new Uint8Array(idat.reduce((n, b) => n + b.length, 0))
  let o = 0
  for (const b of idat) {
    z.set(b, o)
    o += b.length
  }
  const raw = inflateRaw(z.subarray(2))               // the zlib stream's deflate body
  const channels = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[ctype]
  if (!channels) throw new Error(`PNG color type ${ctype} is not supported`)
  const bitsPerPixel = channels * depth
  const bpp = Math.max(1, bitsPerPixel >> 3)
  // the samples per pixel, at their own depth (16-bit kept whole for now)
  const samples = new Uint16Array(width * height * channels)
  const passes = interlace ? [[0, 0, 8, 8], [4, 0, 8, 8], [0, 4, 4, 8], [2, 0, 4, 4], [0, 2, 2, 4], [1, 0, 2, 2], [0, 1, 1, 2]] : [[0, 0, 1, 1]]
  let rp = 0
  for (const [x0, y0, dx, dy] of passes) {
    const pw = Math.ceil((width - x0) / dx)
    const ph = Math.ceil((height - y0) / dy)
    if (pw <= 0 || ph <= 0) continue
    const stride = Math.ceil((pw * bitsPerPixel) / 8)
    let prev = new Uint8Array(stride)
    for (let row = 0; row < ph; row++) {
      const filter = raw[rp++]
      const line = raw.slice(rp, rp + stride)
      rp += stride
      for (let i = 0; i < stride; i++) {
        const a = i >= bpp ? line[i - bpp] : 0
        const b = prev[i]
        const c = i >= bpp ? prev[i - bpp] : 0
        let v = line[i]
        if (filter === 1) v += a
        else if (filter === 2) v += b
        else if (filter === 3) v += (a + b) >> 1
        else if (filter === 4) {
          const pa = Math.abs(b - c)
          const pb = Math.abs(a - c)
          const pc = Math.abs(a + b - 2 * c)
          v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c
        }
        line[i] = v & 0xff
      }
      prev = line
      const y = y0 + row * dy
      for (let col = 0; col < pw; col++) {
        const x = x0 + col * dx
        for (let ch = 0; ch < channels; ch++) {
          let s: number
          if (depth === 8) s = line[col * channels + ch]
          else if (depth === 16) s = (line[(col * channels + ch) * 2] << 8) | line[(col * channels + ch) * 2 + 1]
          else {
            const bit = (col * channels + ch) * depth
            s = (line[bit >> 3] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1)
          }
          samples[(y * width + x) * channels + ch] = s
        }
      }
    }
  }
  const n = width * height
  // to 8 bits as PIL's PNG rawmodes do: the high byte of a 16-bit sample, a 1/2/4-bit gray scaled to 255
  const to8 = (s: number): number => (depth === 16 ? s >> 8 : depth === 8 ? s : ctype === 3 ? s : Math.round((s * 255) / ((1 << depth) - 1)))
  if (ctype === 0) {
    const L = new Uint8Array(n)
    for (let i = 0; i < n; i++) L[i] = to8(samples[i])
    if (depth === 1) {                                 // mode "1": convert("RGB")
      const rgb = new Uint8Array(n * 3)
      for (let i = 0; i < n; i++) rgb[i * 3] = rgb[i * 3 + 1] = rgb[i * 3 + 2] = samples[i] ? 255 : 0
      return makeImage(width, height, 'RGB', rgb)
    }
    return makeImage(width, height, 'L', L)
  }
  if (ctype === 2) {
    const rgb = new Uint8Array(n * 3)
    for (let i = 0; i < n * 3; i++) rgb[i] = to8(samples[i])
    return makeImage(width, height, 'RGB', rgb)
  }
  if (ctype === 4 || ctype === 6) {
    const c = ctype === 4 ? 1 : 3
    const color = new Uint8Array(n * c)
    const a = new Uint8Array(n)
    for (let i = 0; i < n; i++) {
      for (let k = 0; k < c; k++) color[i * c + k] = to8(samples[i * (c + 1) + k])
      a[i] = to8(samples[i * (c + 1) + c])
    }
    return makeImage(width, height, c === 1 ? 'L' : 'RGB', color, makeImage(width, height, 'L', a))
  }
  // a palette image: RGB through the palette; its tRNS (when present) as an alpha band
  if (!palette) throw new Error('a palette PNG without a palette')
  const rgb = new Uint8Array(n * 3)
  const a = trns ? new Uint8Array(n) : null
  for (let i = 0; i < n; i++) {
    const k = samples[i]
    rgb[i * 3] = palette[k * 3] ?? 0
    rgb[i * 3 + 1] = palette[k * 3 + 1] ?? 0
    rgb[i * 3 + 2] = palette[k * 3 + 2] ?? 0
    if (a) a[i] = k < trns!.length ? trns![k] : 255
  }
  return makeImage(width, height, 'RGB', rgb, a ? makeImage(width, height, 'L', a) : null)
}

/** A JPEG's size and components (pdfutils.readJPEGInfo), its bytes kept to draw as they are. ``rgb``:
 * its decoded samples, which reportlab digests for the XObject's name. Throws where PIL cannot open it
 * (JpegImagePlugin's SOF: 8-bit samples, one, three or four components). */
export function jpegImage(jpeg: Uint8Array, rgb: Uint8Array): ImageData {
  let p = 2
  while (p < jpeg.length) {
    if (jpeg[p] !== 0xff) throw new Error('a damaged JPEG')
    const marker = jpeg[p + 1]
    if (marker === 0xd8 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0x01) {
      p += 2
      continue
    }
    const len = (jpeg[p + 2] << 8) | jpeg[p + 3]
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      const height = (jpeg[p + 5] << 8) | jpeg[p + 6]
      const width = (jpeg[p + 7] << 8) | jpeg[p + 8]
      const comps = jpeg[p + 9]
      if (jpeg[p + 4] !== 8) throw new Error(`cannot handle ${jpeg[p + 4]}-bit layers`)
      if (comps !== 1 && comps !== 3 && comps !== 4) throw new Error(`cannot handle ${comps}-layer images`)
      return makeImage(width, height, comps === 1 ? 'L' : comps === 3 ? 'RGB' : 'CMYK', rgb, null, jpeg)
    }
    p += 2 + len
  }
  throw new Error('a JPEG without a frame header')
}

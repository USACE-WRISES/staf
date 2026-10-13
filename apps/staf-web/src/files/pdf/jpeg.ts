// A JPEG's samples as Pillow decodes them for reportlab, which names a JPEG photo's XObject after them
// (lib/utils.py ImageReader.getRGBData: PIL.Image.open(...).tobytes()). Pillow 12 decodes with
// libjpeg-turbo 3.1 at its defaults (JpegImagePlugin, decode.c): the accurate integer IDCT, fancy
// upsampling, RGB out for three components, gray for one, CMYK for four (inverted, Adobe's way). This
// ports what libjpeg-turbo runs on that path on x86-64:
//
//   jdmarker.c   markers, JFIF and Adobe markers, restart markers and resynchronising
//   jdhuff.c     baseline Huffman decoding, data that ends early (the rest of the segment stays zero)
//   jdphuff.c    progressive scans (DC and AC, first and refining)
//   jidctint     the accurate integer IDCT as its SIMD code computes it (see idctIslow)
//   jdmainct.c   the context rows the fancy upsamplers read: the first and last rows repeat
//   jdsample.c   h2v1, h1v2 and h2v2 fancy upsampling, plain replication otherwise
//   jdcolor.c    YCbCr to RGB, YCCK to CMYK (default_decompress_parms picks the colour space)
//
// The upsampling and colour conversion SIMD code gives the C code's samples. tests/jpeg.test.ts holds this
// to Pillow on parity/make_jpeg_fixtures.py's files, damaged and cut-short ones included.
//
// What PIL cannot open (not 8-bit, not 1, 3 or 4 components) throws an Error; what libjpeg-turbo decodes
// in a way not ported here (arithmetic coding, lossless, progressive files that leave coefficients
// unsent, which libjpeg smooths) throws UnsupportedJpeg.

/** A JPEG this port does not decode as libjpeg-turbo does. */
export class UnsupportedJpeg extends Error {}

export interface JpegPixels {
  width: number
  height: number
  mode: 'L' | 'RGB' | 'CMYK'
  /** im.tobytes(): the samples row by row */
  data: Uint8Array
}

/** jpeg_natural_order, with the 16 extra entries libjpeg keeps for corrupt data */
const NATURAL = Int32Array.of(
  0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
  12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
  35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
  58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
  63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63,
)

/** jpeg_make_d_derived_tbl's tables for bit-sequential decoding (Figure F.15) */
interface Huff {
  maxcode: Int32Array
  valoffset: Int32Array
  huffval: Uint8Array
}

function huffTable(counts: Uint8Array, symbols: Uint8Array, dc: boolean): Huff {
  const size: number[] = []
  for (let l = 1; l <= 16; l++) {
    if (size.length + counts[l - 1] > 256) throw new Error('a bad Huffman table')
    for (let i = 0; i < counts[l - 1]; i++) size.push(l)
  }
  const code: number[] = []
  let c = 0
  let si = size[0]
  for (let p = 0; p < size.length;) {
    while (p < size.length && size[p] === si) {
      code.push(c++)
      p++
    }
    if (c >= 2 ** si) throw new Error('a bad Huffman table')
    c *= 2
    si++
  }
  const maxcode = new Int32Array(18)
  const valoffset = new Int32Array(18)
  for (let l = 1, p = 0; l <= 16; l++) {
    if (counts[l - 1]) {
      valoffset[l] = p - code[p]
      p += counts[l - 1]
      maxcode[l] = code[p - 1]
    } else maxcode[l] = -1
  }
  maxcode[17] = 0xfffff                               // ends jpeg_huff_decode's loop on garbage
  if (dc && symbols.subarray(0, size.length).some((s) => s > 15)) throw new Error('a bad Huffman table')
  return { maxcode, valoffset, huffval: symbols }
}

/** The entropy-coded data: bytes unstuffed, stopping at a marker (libjpeg's jpeg_fill_bit_buffer). Past the
 * data it reads zero bits and notes it (insufficient_data). */
class Bits {
  pos: number
  private buf = 0
  private n = 0
  /** the marker that ended the data (unread_marker), 0 while the data goes on */
  marker = 0
  /** a bit past the data was read (insufficient_data) */
  short = false

  constructor(readonly d: Uint8Array, pos: number) {
    this.pos = pos
  }

  bit(): number {
    if (this.n === 0) {
      if (this.marker !== 0 || !this.load()) {
        this.short = true
        return 0
      }
    }
    this.n--
    return (this.buf >> this.n) & 1
  }

  bits(k: number): number {
    let v = 0
    for (let i = 0; i < k; i++) v = (v << 1) | this.bit()
    return v
  }

  private load(): boolean {
    const d = this.d
    if (this.pos >= d.length) {
      this.marker = 0xd9                               // the file ends: as if at EOI
      return false
    }
    let c = d[this.pos++]
    if (c === 0xff) {
      let m: number
      do m = this.pos < d.length ? d[this.pos++] : 0xd9; while (m === 0xff)
      if (m === 0) c = 0xff                            // FF/00 is an FF data byte
      else {
        this.marker = m
        return false
      }
    }
    this.buf = c
    this.n = 8
    return true
  }

  /** process_restart: drop what is left of the buffered byte */
  drop(): void {
    this.n = 0
  }
}

/** next_marker: the next marker from ``pos``, skipping anything that is not one; [marker, position after it] */
function nextMarker(d: Uint8Array, pos: number): [number, number] {
  for (;;) {
    while (pos < d.length && d[pos] !== 0xff) pos++
    if (pos >= d.length) return [0xd9, pos]
    let c: number
    do c = pos + 1 < d.length ? d[++pos] : 0xd9; while (c === 0xff && pos + 1 < d.length)
    pos++
    if (c !== 0) return [c, pos]
  }
}

function decodeSym(b: Bits, t: Huff): number {
  let code = b.bit()
  let l = 1
  while (code > t.maxcode[l]) {
    code = (code << 1) | b.bit()
    l++
  }
  return l > 16 ? 0 : t.huffval[code + t.valoffset[l]]
}

/** HUFF_EXTEND */
const extend = (v: number, s: number): number => (v < 1 << (s - 1) ? v - (1 << s) + 1 : v)

interface Component {
  id: number
  h: number
  v: number
  tq: number
  /** the quantization table latched at the component's first scan (natural order) */
  quant: Int16Array | null
  /** width_in_blocks, height_in_blocks */
  bw: number
  bh: number
  /** blocks per line of the coefficient grid, padded to whole MCUs */
  stride: number
  coefs: Int16Array
  /** downsampled_width, downsampled_height */
  dw: number
  dh: number
  /** coef_bits: the Al of the last scan of each coefficient (zigzag order), -1 before any */
  coefBits: Int32Array
}

// The accurate integer IDCT as libjpeg-turbo's SIMD code computes it (jidctint-avx2.asm and -sse2.asm, which
// Pillow's libjpeg-turbo runs on x86-64): jidctint.c's arithmetic with its multiplications regrouped, the
// dequantized coefficients and four of the sums kept in 16 bits (wrapping), each pass's results packed to
// 16 bits and the samples to 8 (saturating). Within range this is jidctint.c to the bit; where damaged data
// pushes a block out of range, the C code's range-limit table wraps where the SIMD code saturates.
const F130 = 4433 + 6270                               // F_0_541 + F_0_765
const F054 = 4433
const MF130 = 4433 - 15137                             // F_0_541 - F_1_847
const MF078 = 9633 - 16069                             // F_1_175 - F_1_961
const F117 = 9633
const F078 = 9633 - 3196                               // F_1_175 - F_0_390
const MF060 = 2446 - 7373                              // F_0_298 - F_0_899
const MF089 = -7373
const F060 = 12299 - 7373                              // F_1_501 - F_0_899
const MF050 = 16819 - 20995                            // F_2_053 - F_2_562
const MF256 = -20995
const F050 = 25172 - 20995                             // F_3_072 - F_2_562

/** pmullw, paddw: 16 bits, wrapping */
const w16 = (x: number): number => (x << 16) >> 16
/** packssdw: 16 bits, saturating */
const s16 = (x: number): number => (x < -32768 ? -32768 : x > 32767 ? 32767 : x)
/** packsswb, then the centre added back: a sample */
const sample = (x: number): number => (x < -128 ? 0 : x > 127 ? 255 : x + 128)

const WS = new Int32Array(64)
const D = new Int32Array(8)

/** The dodct macro: one 1-D pass over eight 16-bit inputs, descaled by ``n`` bits and packed to 16 bits, into D. */
function dodct(i0: number, i1: number, i2: number, i3: number, i4: number, i5: number, i6: number, i7: number, n: number): void {
  const half = 1 << (n - 1)
  const tmp3 = i2 * F130 + i6 * F054
  const tmp2 = i2 * F054 + i6 * MF130
  const tmp0 = w16(i0 + i4) * 8192
  const tmp1 = w16(i0 - i4) * 8192
  const tmp10 = tmp0 + tmp3
  const tmp13 = tmp0 - tmp3
  const tmp11 = tmp1 + tmp2
  const tmp12 = tmp1 - tmp2
  const z3 = w16(i7 + i3)
  const z4 = w16(i5 + i1)
  const z3p = z3 * MF078 + z4 * F117
  const z4p = z3 * F117 + z4 * F078
  const t0 = i7 * MF060 + i1 * MF089 + z3p
  const t3 = i7 * MF089 + i1 * F060 + z4p
  const t1 = i5 * MF050 + i3 * MF256 + z4p
  const t2 = i5 * MF256 + i3 * F050 + z3p
  D[0] = s16((tmp10 + t3 + half) >> n)
  D[7] = s16((tmp10 - t3 + half) >> n)
  D[1] = s16((tmp11 + t2 + half) >> n)
  D[6] = s16((tmp11 - t2 + half) >> n)
  D[2] = s16((tmp12 + t1 + half) >> n)
  D[5] = s16((tmp12 - t1 + half) >> n)
  D[3] = s16((tmp13 + t0 + half) >> n)
  D[4] = s16((tmp13 - t0 + half) >> n)
}

/** jsimd_idct_islow: one block's coefficients (natural order) to 8 x 8 samples */
function idctIslow(coefs: Int16Array, o: number, q: Int16Array, out: Uint8Array, at: number, stride: number): void {
  const ws = WS
  let acZero = true
  for (let k = o + 8; k < o + 64; k++) {
    if (coefs[k] !== 0) {
      acZero = false
      break
    }
  }
  if (acZero) {                                        // every column DC alone: the DC, shifted in 16 bits
    for (let c = 0; c < 8; c++) {
      const v = w16(w16(coefs[o + c] * q[c]) << 2)
      for (let r = 0; r < 64; r += 8) ws[r + c] = v
    }
  } else {
    for (let c = 0; c < 8; c++) {
      dodct(w16(coefs[o + c] * q[c]), w16(coefs[o + 8 + c] * q[8 + c]), w16(coefs[o + 16 + c] * q[16 + c]),
        w16(coefs[o + 24 + c] * q[24 + c]), w16(coefs[o + 32 + c] * q[32 + c]), w16(coefs[o + 40 + c] * q[40 + c]),
        w16(coefs[o + 48 + c] * q[48 + c]), w16(coefs[o + 56 + c] * q[56 + c]), 11)
      for (let r = 0; r < 8; r++) ws[r * 8 + c] = D[r]
    }
  }
  for (let r = 0; r < 64; r += 8, at += stride) {
    if (ws[r + 1] === 0 && ws[r + 2] === 0 && ws[r + 3] === 0 && ws[r + 4] === 0 && ws[r + 5] === 0 &&
      ws[r + 6] === 0 && ws[r + 7] === 0) {
      out.fill(sample((ws[r] + 16) >> 5), at, at + 8)
      continue
    }
    dodct(ws[r], ws[r + 1], ws[r + 2], ws[r + 3], ws[r + 4], ws[r + 5], ws[r + 6], ws[r + 7], 18)
    for (let k = 0; k < 8; k++) out[at + k] = sample(D[k])
  }
}

/** A component's samples at the image's size (jdsample.c, with jdmainct.c's context rows). */
function upsample(plane: Uint8Array, ps: number, c: Component, maxH: number, maxV: number, W: number, H: number): Uint8Array {
  const out = new Uint8Array(W * H)
  const { dw, dh } = c
  const row = (r: number): number => r * ps
  if (c.h === maxH && c.v === maxV) {                 // fullsize_upsample
    for (let y = 0; y < H; y++) out.set(plane.subarray(row(y), row(y) + W), y * W)
    return out
  }
  const line = new Uint8Array(2 * dw + 2)
  if (c.h * 2 === maxH && c.v === maxV && dw > 2) {   // h2v1_fancy_upsample
    for (let y = 0; y < H; y++) {
      const i = row(y)
      let v = plane[i]
      line[0] = v
      line[1] = (v * 3 + plane[i + 1] + 2) >> 2
      for (let j = 1; j < dw - 1; j++) {
        v = plane[i + j] * 3
        line[2 * j] = (v + plane[i + j - 1] + 1) >> 2
        line[2 * j + 1] = (v + plane[i + j + 1] + 2) >> 2
      }
      v = plane[i + dw - 1]
      line[2 * dw - 2] = (v * 3 + plane[i + dw - 2] + 1) >> 2
      line[2 * dw - 1] = v
      out.set(line.subarray(0, W), y * W)
    }
    return out
  }
  if (c.h === maxH && c.v * 2 === maxV) {             // h1v2_fancy_upsample
    for (let y = 0; y < H; y++) {
      const r = y >> 1
      const near = row(r)
      const far = row(y & 1 ? Math.min(r + 1, dh - 1) : Math.max(r - 1, 0))
      const bias = y & 1 ? 2 : 1
      for (let x = 0; x < W; x++) out[y * W + x] = (plane[near + x] * 3 + plane[far + x] + bias) >> 2
    }
    return out
  }
  if (c.h * 2 === maxH && c.v * 2 === maxV && dw > 2) { // h2v2_fancy_upsample
    const sums = new Int32Array(dw)
    for (let y = 0; y < H; y++) {
      const r = y >> 1
      const near = row(r)
      const far = row(y & 1 ? Math.min(r + 1, dh - 1) : Math.max(r - 1, 0))
      for (let j = 0; j < dw; j++) sums[j] = plane[near + j] * 3 + plane[far + j]
      line[0] = (sums[0] * 4 + 8) >> 4
      line[1] = (sums[0] * 3 + sums[1] + 7) >> 4
      for (let j = 1; j < dw - 1; j++) {
        line[2 * j] = (sums[j] * 3 + sums[j - 1] + 8) >> 4
        line[2 * j + 1] = (sums[j] * 3 + sums[j + 1] + 7) >> 4
      }
      line[2 * dw - 2] = (sums[dw - 1] * 3 + sums[dw - 2] + 8) >> 4
      line[2 * dw - 1] = (sums[dw - 1] * 4 + 7) >> 4
      out.set(line.subarray(0, W), y * W)
    }
    return out
  }
  if (maxH % c.h !== 0 || maxV % c.v !== 0) throw new UnsupportedJpeg('fractional sampling')
  const he = maxH / c.h                                // int_upsample (and h2v1/h2v2 box upsampling)
  const ve = maxV / c.v
  for (let y = 0; y < H; y++) {
    const i = row(Math.floor(y / ve))
    for (let x = 0; x < W; x++) out[y * W + x] = plane[i + Math.floor(x / he)]
  }
  return out
}

/** build_ycc_rgb_table */
const YCC = (() => {
  const crR = new Int32Array(256)
  const cbB = new Int32Array(256)
  const crG = new Int32Array(256)
  const cbG = new Int32Array(256)
  for (let i = 0; i < 256; i++) {
    const x = i - 128
    crR[i] = (91881 * x + 32768) >> 16
    cbB[i] = (116130 * x + 32768) >> 16
    crG[i] = -46802 * x
    cbG[i] = -22554 * x + 32768
  }
  return { crR, cbB, crG, cbG }
})()

const clamp = (v: number): number => (v < 0 ? 0 : v > 255 ? 255 : v)

/** The JPEG's samples as PIL's im.tobytes() gives them. */
export function decodeJpeg(d: Uint8Array): JpegPixels {
  if (d[0] !== 0xff || d[1] !== 0xd8) throw new Error('not a JPEG')
  const qt: (Uint16Array | null)[] = [null, null, null, null]
  const dcTables: (Huff | null)[] = [null, null, null, null]
  const acTables: (Huff | null)[] = [null, null, null, null]
  let comps: Component[] = []
  let W = 0
  let H = 0
  let maxH = 1
  let maxV = 1
  let mcusX = 0
  let mcusY = 0
  let progressive = false
  let restartInterval = 0
  let jfif = false
  let adobe = -1
  let space = ''
  let pos = 2
  for (;;) {
    let marker: number
    [marker, pos] = nextMarker(d, pos)
    if (marker === 0xd9) break                        // EOI
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7) || marker === 0xd8) continue
    const len = (d[pos] << 8) | d[pos + 1]
    const seg = d.subarray(pos + 2, pos + len)
    const end = pos + len
    if (marker === 0xc0 || marker === 0xc1 || marker === 0xc2) {
      if (seg[0] !== 8) throw new Error(`PIL cannot open a ${seg[0]}-bit JPEG`)
      H = (seg[1] << 8) | seg[2]
      W = (seg[3] << 8) | seg[4]
      const n = seg[5]
      if (n !== 1 && n !== 3 && n !== 4) throw new Error(`PIL cannot open a JPEG of ${n} components`)
      if (!W || !H) throw new UnsupportedJpeg('an empty JPEG')
      progressive = marker === 0xc2
      comps = []
      for (let i = 0; i < n; i++) {
        const h = seg[7 + 3 * i] >> 4
        const v = seg[7 + 3 * i] & 15
        if (h < 1 || h > 4 || v < 1 || v > 4) throw new UnsupportedJpeg('bad sampling factors')
        comps.push({ id: seg[6 + 3 * i], h, v, tq: seg[8 + 3 * i], quant: null, bw: 0, bh: 0, stride: 0,
          coefs: new Int16Array(0), dw: 0, dh: 0, coefBits: new Int32Array(64).fill(-1) })
      }
      maxH = Math.max(...comps.map((c) => c.h))
      maxV = Math.max(...comps.map((c) => c.v))
      mcusX = Math.ceil(W / (8 * maxH))
      mcusY = Math.ceil(H / (8 * maxV))
      for (const c of comps) {
        c.dw = Math.ceil((W * c.h) / maxH)
        c.dh = Math.ceil((H * c.v) / maxV)
        c.bw = Math.ceil(c.dw / 8)
        c.bh = Math.ceil(c.dh / 8)
        c.stride = mcusX * c.h
        c.coefs = new Int16Array(c.stride * mcusY * c.v * 64)
      }
    } else if (marker >= 0xc3 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      throw new UnsupportedJpeg(`frame marker ${marker.toString(16)} (lossless, arithmetic or hierarchical)`)
    } else if (marker === 0xc8 || marker === 0xcc) {
      throw new UnsupportedJpeg('arithmetic coding')
    } else if (marker === 0xc4) {                     // DHT
      for (let p = 0; p < seg.length;) {
        const tc = seg[p] >> 4
        const th = seg[p] & 15
        const counts = seg.subarray(p + 1, p + 17)
        const total = counts.reduce((a, b) => a + b, 0)
        const table = huffTable(counts, seg.slice(p + 17, p + 17 + total), tc === 0)
        if (th > 3) throw new Error('a bad Huffman table')
        if (tc === 0) dcTables[th] = table
        else acTables[th] = table
        p += 17 + total
      }
    } else if (marker === 0xdb) {                     // DQT
      for (let p = 0; p < seg.length;) {
        const prec = seg[p] >> 4
        const tq = seg[p] & 15
        if (tq > 3) throw new Error('a bad quantization table')
        const t = new Uint16Array(64)
        for (let i = 0; i < 64; i++) t[NATURAL[i]] = prec ? (seg[p + 1 + 2 * i] << 8) | seg[p + 2 + 2 * i] : seg[p + 1 + i]
        qt[tq] = t
        p += 1 + (prec ? 128 : 64)
      }
    } else if (marker === 0xdd) {                     // DRI
      restartInterval = (seg[0] << 8) | seg[1]
    } else if (marker === 0xe0) {                     // APP0: JFIF (examine_app0)
      if (seg.length >= 14 && seg[0] === 0x4a && seg[1] === 0x46 && seg[2] === 0x49 && seg[3] === 0x46 && seg[4] === 0) jfif = true
    } else if (marker === 0xee) {                     // APP14: Adobe (examine_app14)
      if (seg.length >= 12 && seg[0] === 0x41 && seg[1] === 0x64 && seg[2] === 0x6f && seg[3] === 0x62 && seg[4] === 0x65) adobe = seg[11]
    } else if (marker === 0xda) {                     // SOS
      if (!comps.length) throw new Error('a scan before the frame')
      if (!space) space = colorSpace(comps, jfif, adobe)
      const ns = seg[0]
      const scan: Component[] = []
      const tds: number[] = []
      const tas: number[] = []
      for (let i = 0; i < ns; i++) {
        const c = comps.find((k) => k.id === seg[1 + 2 * i])
        if (!c || scan.includes(c)) throw new Error('a bad scan component')
        scan.push(c)
        tds.push(seg[2 + 2 * i] >> 4)
        tas.push(seg[2 + 2 * i] & 15)
      }
      const ss = seg[1 + 2 * ns]
      const se = seg[2 + 2 * ns]
      const ah = seg[3 + 2 * ns] >> 4
      const al = seg[3 + 2 * ns] & 15
      for (const c of scan) {                          // latch_quant_tables
        if (!c.quant) {
          const t = qt[c.tq]
          if (!t) throw new Error('a missing quantization table')
          c.quant = Int16Array.from(t)                  // libjpeg's ISLOW multipliers are shorts
        }
      }
      const b = new Bits(d, end)
      decodeScan(b, scan, progressive, ss, se, ah, al, tds.map((t) => dcTables[t]), tas.map((t) => acTables[t]),
        restartInterval, mcusX, mcusY)
      if (b.marker !== 0) {
        if (b.marker === 0xd9) break
        // the scan stopped at the next marker: read it as the next one
        pos = b.pos - 2
        while (pos > 0 && d[pos] !== 0xff) pos--
        continue
      }
      pos = b.pos
      continue
    }
    pos = end
  }
  if (!comps.length || !space) throw new Error('a JPEG without image data')
  if (progressive && smooths(comps)) throw new UnsupportedJpeg('a progressive JPEG that leaves coefficients unsent')
  const planes = comps.map((c) => {
    const ps = c.bw * 8
    const plane = new Uint8Array(ps * c.bh * 8)
    const q = c.quant ?? new Int16Array(64)
    for (let by = 0; by < c.bh; by++) {
      for (let bx = 0; bx < c.bw; bx++) idctIslow(c.coefs, (by * c.stride + bx) * 64, q, plane, by * 8 * ps + bx * 8, ps)
    }
    return upsample(plane, ps, c, maxH, maxV, W, H)
  })
  const n = W * H
  if (comps.length === 1) return { width: W, height: H, mode: 'L', data: planes[0] }
  if (comps.length === 3) {
    const out = new Uint8Array(n * 3)
    const [p0, p1, p2] = planes
    if (space === 'RGB') {
      for (let i = 0; i < n; i++) {
        out[3 * i] = p0[i]
        out[3 * i + 1] = p1[i]
        out[3 * i + 2] = p2[i]
      }
    } else {
      const { crR, cbB, crG, cbG } = YCC
      for (let i = 0; i < n; i++) {
        const y = p0[i]
        const cb = p1[i]
        const cr = p2[i]
        out[3 * i] = clamp(y + crR[cr])
        out[3 * i + 1] = clamp(y + ((cbG[cb] + crG[cr]) >> 16))
        out[3 * i + 2] = clamp(y + cbB[cb])
      }
    }
    return { width: W, height: H, mode: 'RGB', data: out }
  }
  // CMYK, as Pillow's "CMYK;I" unpacker gives it: every sample inverted
  const out = new Uint8Array(n * 4)
  const [p0, p1, p2, p3] = planes
  if (space === 'YCCK') {
    const { crR, cbB, crG, cbG } = YCC
    for (let i = 0; i < n; i++) {
      const y = p0[i]
      const cb = p1[i]
      const cr = p2[i]
      out[4 * i] = 255 - clamp(255 - (y + crR[cr]))
      out[4 * i + 1] = 255 - clamp(255 - (y + ((cbG[cb] + crG[cr]) >> 16)))
      out[4 * i + 2] = 255 - clamp(255 - (y + cbB[cb]))
      out[4 * i + 3] = 255 - p3[i]
    }
  } else {
    for (let i = 0; i < n; i++) {
      out[4 * i] = 255 - p0[i]
      out[4 * i + 1] = 255 - p1[i]
      out[4 * i + 2] = 255 - p2[i]
      out[4 * i + 3] = 255 - p3[i]
    }
  }
  return { width: W, height: H, mode: 'CMYK', data: out }
}

/** default_decompress_parms: the colour space libjpeg takes the file to be in */
function colorSpace(comps: Component[], jfif: boolean, adobe: number): string {
  if (comps.length === 1) return 'L'
  if (comps.length === 3) {
    if (jfif) return 'YCbCr'
    if (adobe >= 0) return adobe === 0 ? 'RGB' : 'YCbCr'
    const [a, b, c] = comps.map((k) => k.id)
    return a === 82 && b === 71 && c === 66 ? 'RGB' : 'YCbCr'
  }
  return adobe > 0 ? 'YCCK' : 'CMYK'                // Adobe transform 2 (or any but 0): YCCK
}

/** smoothing_ok: whether libjpeg would smooth this progressive file's blocks (some of the first nine AC
 * coefficients never refined to the last bit), which this port does not do */
function smooths(comps: Component[]): boolean {
  let useful = false
  for (const c of comps) {
    const q = c.quant
    if (!q) return false
    for (let i = 0; i < 10; i++) if (q[NATURAL[i]] === 0) return false
    if (c.coefBits[0] < 0) return false
    for (let i = 1; i < 10; i++) if (c.coefBits[i] !== 0) useful = true
  }
  return useful
}

/** One scan: its MCUs, restarts and (progressive) bands, into the components' coefficients. */
function decodeScan(b: Bits, scan: Component[], progressive: boolean, ss: number, se: number, ah: number, al: number,
  dcs: (Huff | null)[], acs: (Huff | null)[], restartInterval: number, mcusX: number, mcusY: number): void {
  const dcBand = ss === 0
  if (progressive) {
    // start_pass_phuff_decoder's checks, then the coefficient bits it records
    let bad = dcBand ? se !== 0 : ss > se || se >= 64 || scan.length !== 1
    if (ah !== 0 && al !== ah - 1) bad = true
    if (al > 13) bad = true
    if (bad) throw new Error('a bad progression')
    for (const c of scan) for (let k = ss; k <= se; k++) c.coefBits[k] = al
  }
  const single = scan.length === 1
  // the blocks of an MCU: [component, block row offset, block column offset]
  const layout: [number, number, number][] = []
  scan.forEach((c, i) => {
    if (single) layout.push([i, 0, 0])
    else for (let y = 0; y < c.v; y++) for (let x = 0; x < c.h; x++) layout.push([i, y, x])
  })
  if (layout.length > 10) throw new Error('too many blocks in an MCU')
  const across = single ? scan[0].bw : mcusX
  const down = single ? scan[0].bh : mcusY
  const tables = scan.map((_, i) => {
    const needDc = !progressive || (dcBand && ah === 0)
    const needAc = !progressive || !dcBand
    if ((needDc && !dcs[i]) || (needAc && !acs[i])) throw new Error('a missing Huffman table')
    return [dcs[i], acs[i]] as const
  })
  const pred = new Int32Array(scan.length)
  let eobrun = 0
  let toGo = restartInterval
  let nextRst = 0
  const p1 = 1 << al
  const m1 = -1 << al
  for (let my = 0; my < down; my++) {
    for (let mx = 0; mx < across; mx++) {
      if (restartInterval) {
        if (toGo === 0) {                                // process_restart
          b.drop()
          restart(b, nextRst)
          nextRst = (nextRst + 1) & 7
          pred.fill(0)
          eobrun = 0
          toGo = restartInterval
          if (b.marker === 0) b.short = false
        }
        toGo--
      }
      if (b.short && !(progressive && dcBand && ah !== 0)) continue
      for (const [i, by, bx] of layout) {
        const c = scan[i]
        const row = single ? my : my * c.v + by
        const col = single ? mx : mx * c.h + bx
        const o = (row * c.stride + col) * 64
        const coefs = c.coefs
        const [dc, ac] = tables[i]
        if (!progressive) {
          const s = decodeSym(b, dc!)
          pred[i] = (pred[i] + (s ? extend(b.bits(s), s) : 0)) | 0
          coefs[o] = pred[i]
          for (let k = 1; k < 64; k++) {
            const rs = decodeSym(b, ac!)
            const r = rs >> 4
            const t = rs & 15
            if (t) {
              k += r
              coefs[o + NATURAL[k]] = extend(b.bits(t), t)
            } else {
              if (r !== 15) break
              k += 15
            }
          }
        } else if (dcBand && ah === 0) {
          const s = decodeSym(b, dc!)
          pred[i] = (pred[i] + (s ? extend(b.bits(s), s) : 0)) | 0
          coefs[o] = pred[i] << al
        } else if (dcBand) {
          if (b.bit()) coefs[o] |= p1
        } else if (ah === 0) {
          if (eobrun > 0) eobrun--
          else {
            for (let k = ss; k <= se; k++) {
              const rs = decodeSym(b, ac!)
              const r = rs >> 4
              const t = rs & 15
              if (t) {
                k += r
                coefs[o + NATURAL[k]] = extend(b.bits(t), t) << al
              } else if (r === 15) k += 15
              else {
                eobrun = 1 << r
                if (r) eobrun += b.bits(r)
                eobrun--
                break
              }
            }
          }
        } else {
          let k = ss
          if (eobrun === 0) {
            for (; k <= se; k++) {
              const rs = decodeSym(b, ac!)
              let r = rs >> 4
              let t = rs & 15
              if (t) t = b.bit() ? p1 : m1
              else if (r !== 15) {
                eobrun = 1 << r
                if (r) eobrun += b.bits(r)
                break
              }
              do {
                const at = o + NATURAL[k]
                const v = coefs[at]
                if (v !== 0) {
                  if (b.bit() && (v & p1) === 0) coefs[at] = v >= 0 ? v + p1 : v + m1
                } else if (--r < 0) break
                k++
              } while (k <= se)
              if (t) coefs[o + NATURAL[k]] = t
            }
          }
          if (eobrun > 0) {
            for (; k <= se; k++) {
              const at = o + NATURAL[k]
              const v = coefs[at]
              if (v !== 0 && b.bit() && (v & p1) === 0) coefs[at] = v >= 0 ? v + p1 : v + m1
            }
            eobrun--
          }
        }
      }
    }
  }
}

/** read_restart_marker, with jpeg_resync_to_restart when the marker is not the one expected */
function restart(b: Bits, want: number): void {
  if (b.marker === 0) {
    const [m, p] = nextMarker(b.d, b.pos)
    b.marker = m
    b.pos = p
  }
  if (b.marker === 0xd0 + want) {
    b.marker = 0
    return
  }
  for (;;) {
    const m = b.marker
    let action: number
    if (m < 0xc0) action = 2
    else if (m < 0xd0 || m > 0xd7) action = 3
    else if (m === 0xd0 + ((want + 1) & 7) || m === 0xd0 + ((want + 2) & 7)) action = 3
    else if (m === 0xd0 + ((want - 1) & 7) || m === 0xd0 + ((want - 2) & 7)) action = 2
    else action = 1
    if (action === 1) {
      b.marker = 0
      return
    }
    if (action === 3) return
    const [n, p] = nextMarker(b.d, b.pos)
    b.marker = n
    b.pos = p
  }
}

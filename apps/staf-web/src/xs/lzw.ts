/**
 * TIFF LZW (compression 5) for the 3DEP tiles, replacing geotiff.js's decoder.
 *
 * geotiff.js 3.0.5 keeps 4,093 dictionary entries; libtiff's encoder fills the table to entry 4093
 * before it emits Clear, so the last entry was dropped and some blocks of USGS's 1 m tiles decoded
 * short (found 2026-10-10: CT_Statewide_C16 x69y465's bottom row of blocks), and the predictor then
 * failed with "Invalid typed array length". This decoder keeps all 4,096 entries (libtiff's
 * `LZWDecode`: codes of 9 to 12 bits, most significant bit first, one code early in widening) and
 * returns a whole block. A stream that ends properly (EOI) before the block is full, as a stripped
 * file's last strip does, is zero-filled; a stream that runs out of bytes or meets a code that can't
 * be is an error, as it is to libtiff, never a block of zeros (found 2026-10-11: responses that
 * arrived short decoded to runs of 0 m elevations).
 */
import { addDecoder, BaseDecoder } from 'geotiff'

const CLEAR = 256
const EOI = 257
const FIRST = 258
const MAX_BITS = 12

/** How a stream ended: its end code, a full block, out of input, or a code that can't occur. */
export type LzwEnd = 'eoi' | 'full' | 'input' | 'corrupt'

export interface LzwResult {
  written: number
  end: LzwEnd
}

/** Decode one LZW stream into `out` (as many bytes as fit). */
export function lzwDecode(input: Uint8Array, out: Uint8Array): LzwResult {
  const prefix = new Int32Array(4096)
  const suffix = new Uint8Array(4096)
  const first = new Uint8Array(4096)
  const length = new Int32Array(4096)
  for (let i = 0; i < 256; i++) {
    prefix[i] = -1
    suffix[i] = i
    first[i] = i
    length[i] = 1
  }
  const totalBits = input.length * 8
  let bitPos = 0
  let width = 9
  let next = FIRST
  let prev = -1
  let pos = 0
  const OUT_OF_INPUT = -1
  const read = (): number => {
    if (bitPos + width > totalBits) return OUT_OF_INPUT
    let v = 0
    for (let n = 0; n < width; n++) {
      const bit = (input[(bitPos + n) >> 3] >> (7 - ((bitPos + n) & 7))) & 1
      v = (v << 1) | bit
    }
    bitPos += width
    return v
  }
  const emit = (code: number): void => {
    const len = length[code]
    let end = pos + len
    let c = code
    // write the entry back to front, clipped to the output
    for (let p = end - 1; p >= pos; p--) {
      if (p < out.length) out[p] = suffix[c]
      c = prefix[c]
    }
    pos = end
  }
  const done = (end: LzwEnd): LzwResult => ({ written: Math.min(pos, out.length), end })
  for (;;) {
    const code = read()
    if (code === OUT_OF_INPUT) return done('input')
    if (code === EOI) return done('eoi')
    if (code === CLEAR) {
      width = 9
      next = FIRST
      prev = -1
      continue
    }
    if (prev === -1) {
      if (code > 255) return done('corrupt') // a stream must restart on a literal
      emit(code)
      prev = code
      if (pos >= out.length) return done('full')
      continue
    }
    if (code < next) {
      emit(code)
      if (next < 4096) {
        prefix[next] = prev
        suffix[next] = first[code]
        first[next] = first[prev]
        length[next] = length[prev] + 1
        next++
      }
    } else if (code === next && next < 4096) {
      prefix[next] = prev
      suffix[next] = first[prev]
      first[next] = first[prev]
      length[next] = length[prev] + 1
      next++
      emit(code)
    } else {
      return done('corrupt')
    }
    if (next >= (1 << width) - 1 && width < MAX_BITS) width++
    prev = code
    if (pos >= out.length) return done('full')
  }
}

interface DecoderParameters {
  tileWidth: number
  tileHeight: number
  bitsPerSample: number | ArrayLike<number>
  planarConfiguration: number
  samplesPerPixel?: number
}

/** geotiff.js decoder: whole blocks from `lzwDecode` (the predictor is applied by BaseDecoder); a
 * stream cut short or corrupt throws. */
export class FullBlockLZWDecoder extends BaseDecoder {
  decodeBlock(buffer: ArrayBufferLike): ArrayBuffer {
    const p = (this as unknown as { parameters: DecoderParameters }).parameters
    const bits = typeof p.bitsPerSample === 'number' ? [p.bitsPerSample] : Array.from(p.bitsPerSample)
    const bytesPerPixel = p.planarConfiguration === 2 ? bits[0] / 8 : bits.reduce((a, b) => a + b, 0) / 8
    const out = new Uint8Array(p.tileWidth * p.tileHeight * bytesPerPixel)
    const { written, end } = lzwDecode(new Uint8Array(buffer), out)
    if (end === 'input' || end === 'corrupt') {
      throw new Error(`LZW block ${end === 'input' ? 'cut short' : 'corrupt'} after ${written} of ${out.length} bytes`)
    }
    return out.buffer
  }
}

let registered = false

/** Use `FullBlockLZWDecoder` for every LZW tile geotiff.js reads in this page. */
export function registerLzw(): void {
  if (registered) return
  addDecoder(5, async () => FullBlockLZWDecoder as unknown as typeof BaseDecoder, undefined, false)
  registered = true
}

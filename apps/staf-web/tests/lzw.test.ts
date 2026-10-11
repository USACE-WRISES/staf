// The LZW decoder against GDAL's own decode of an LZW + floating-point-predictor tiled GeoTIFF
// (fixture from parity/make_fixtures.py: partial edge blocks, blocks that fill the LZW table).
import { readFileSync } from 'node:fs'
import { fromArrayBuffer } from 'geotiff'
import { describe, expect, it } from 'vitest'
import ref from './fixtures/lzw_ref.json'
import { FullBlockLZWDecoder, lzwDecode, registerLzw } from '../src/xs/lzw'

describe('lzw', () => {
  it('reads a 3DEP-style tile exactly as GDAL does', async () => {
    registerLzw()
    const bytes = readFileSync(new URL('./fixtures/lzw_pred3.tif', import.meta.url))
    const tiff = await fromArrayBuffer(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength))
    const image = await tiff.getImage()
    const r = await image.readRasters({ samples: [0], interleave: true })
    const u = new Uint32Array(Float32Array.from(r as unknown as ArrayLike<number>).buffer)
    let h = 0
    for (const v of u) h = (Math.imul(h, 31) + v) >>> 0
    expect([image.getWidth(), image.getHeight()]).toEqual([ref.width, ref.height])
    expect(Array.from(u.slice(0, 4))).toEqual(ref.first)
    expect(Array.from(u.slice(-4))).toEqual(ref.last)
    expect(h).toBe(ref.hash)
  })
  it('zero-fills a stream that ends properly, and stops at the block', () => {
    // 'ABAB' as LZW 9-bit codes: CLEAR, 65, 66, 258, EOI
    const input = codes([256, 65, 66, 258, 257])
    const out = new Uint8Array(6)
    expect(lzwDecode(input, out)).toEqual({ written: 4, end: 'eoi' })
    expect(Array.from(out)).toEqual([65, 66, 65, 66, 0, 0])
    const small = new Uint8Array(3)
    expect(lzwDecode(input, small)).toEqual({ written: 3, end: 'full' })
    expect(Array.from(small)).toEqual([65, 66, 65])
  })
  it('tells a stream cut short or corrupt from one that ended', () => {
    expect(lzwDecode(codes([256, 65, 66, 258]).slice(0, 3), new Uint8Array(6)).end).toBe('input')
    expect(lzwDecode(codes([256, 65, 300, 257]), new Uint8Array(6)).end).toBe('corrupt')
  })
  it('refuses a block cut short (a response that arrived short), and decodes it whole', async () => {
    const bytes = readFileSync(new URL('./fixtures/lzw_pred3.tif', import.meta.url))
    const whole = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)
    const image = await (await fromArrayBuffer(whole)).getImage()
    const offset = Number(await image.fileDirectory.loadValueIndexed('TileOffsets', 0))
    const count = Number(await image.fileDirectory.loadValueIndexed('TileByteCounts', 0))
    const block = whole.slice(offset, offset + count)
    const decoder = new FullBlockLZWDecoder({ tileWidth: image.getTileWidth(), tileHeight: image.getTileHeight(),
                                              planarConfiguration: 1, bitsPerSample: 32, predictor: 3 })
    await expect(decoder.decode(block.slice(0, 1000))).rejects.toThrow(/LZW block cut short/)
    const full = await decoder.decode(block)
    expect(full.byteLength).toBe(image.getTileWidth() * image.getTileHeight() * 4)
  })
})

/** LZW codes packed 9 bits each, most significant bit first. */
function codes(list: number[]): Uint8Array {
  const bits = list.map((c) => c.toString(2).padStart(9, '0')).join('')
  const input = new Uint8Array(Math.ceil(bits.length / 8))
  for (let i = 0; i < bits.length; i++) if (bits[i] === '1') input[i >> 3] |= 1 << (7 - (i & 7))
  return input
}

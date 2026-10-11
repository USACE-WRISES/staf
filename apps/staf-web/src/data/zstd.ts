// Zstandard for the bundle's files: zstddec (WebAssembly, a dependency geotiff already brings) once it
// has started, ten to twenty times faster than the pure-JS fzstd hyparquet-compressors carries, which
// answers until then. Both give the same bytes.
import { compressors as jsCompressors, decompressZstd } from 'hyparquet-compressors'
import { ZSTDDecoder } from 'zstddec'

let decoder: ZSTDDecoder | null = null
let starting: Promise<void> | null = null

/** Starts the WebAssembly decoder (once); reads wait for it, and a failure leaves fzstd answering. */
export function zstdReady(): Promise<void> {
  starting ??= (async () => {
    const d = new ZSTDDecoder()
    await d.init()
    decoder = d
  })().catch(() => {})
  return starting
}

/** Decompress a zstd frame of known size. */
export function zstd(input: Uint8Array, outputLength: number): Uint8Array {
  if (decoder) return decoder.decode(input, outputLength)
  return decompressZstd(input, new Uint8Array(outputLength))
}

/** hyparquet's codecs with zstd from the WebAssembly decoder. */
export const compressors = { ...jsCompressors, ZSTD: zstd }

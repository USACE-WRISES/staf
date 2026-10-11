/**
 * zlib's CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320), the checksum the release stores per
 * section as `z_crc` (tools/xs-grid/xsgrid/release.py `_profile_crcs`): of the profile's archive codes,
 * `codec.encode` of the float32 samples, as little-endian int32.
 */
import { encode } from './codec'

const TABLE = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

export function crc32(bytes: Uint8Array, crc = 0): number {
  let c = (crc ^ 0xffffffff) >>> 0
  for (let i = 0; i < bytes.length; i++) c = TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

/** The release's `z_crc` of a profile (typed arrays are little-endian on every platform the app runs on). */
export function profileCrc(z: Float32Array): number {
  const codes = encode(z)
  return crc32(new Uint8Array(codes.buffer, codes.byteOffset, codes.byteLength))
}

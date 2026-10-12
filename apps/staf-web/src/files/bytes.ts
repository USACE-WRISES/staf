// Bytes for the files the app writes: UTF-8, the digests the Python code takes (hashlib's sha1 for
// uuid5, md5 for reportlab's image names, sha256 for digests and fingerprints) and base64, all
// synchronous so a file is built in one pass. Pure TypeScript: the browser's crypto.subtle has no MD5
// and answers only asynchronously.

const enc = new TextEncoder()

/** str.encode("utf-8") (a lone surrogate is written as U+FFFD, where Python would refuse it). */
export function utf8(text: string): Uint8Array {
  return enc.encode(text)
}

/** bytes.decode("utf-8") of valid UTF-8. */
export function fromUtf8(data: Uint8Array): string {
  return new TextDecoder('utf-8', { ignoreBOM: true }).decode(data)
}

/** Python's error for bytes that are not UTF-8 (UnicodeDecodeError's text), or null when they are. */
export function utf8Error(data: Uint8Array): string | null {
  let i = 0
  const n = data.length
  const fail = (start: number, end: number, reason: string) => {
    const pos = end - start > 1 ? `bytes in position ${start}-${end - 1}` : `byte 0x${data[start].toString(16).padStart(2, '0')} in position ${start}`
    return `'utf-8' codec can't decode ${pos}: ${reason}`
  }
  while (i < n) {
    const b = data[i]
    if (b < 0x80) {
      i++
      continue
    }
    let need = 0
    let lo = 0x80
    let hi = 0xbf
    if (b >= 0xc2 && b <= 0xdf) need = 1
    else if (b === 0xe0) { need = 2; lo = 0xa0 }
    else if (b >= 0xe1 && b <= 0xec) need = 2
    else if (b === 0xed) { need = 2; hi = 0x9f }
    else if (b >= 0xee && b <= 0xef) need = 2
    else if (b === 0xf0) { need = 3; lo = 0x90 }
    else if (b >= 0xf1 && b <= 0xf3) need = 3
    else if (b === 0xf4) { need = 3; hi = 0x8f }
    else return fail(i, i + 1, 'invalid start byte')
    // CPython's decoder: the bytes read so far form a valid prefix; the first that does not ends it
    let j = i + 1
    for (let k = 0; k < need; k++, j++) {
      if (j >= n) return fail(i, j, 'unexpected end of data')
      const c = data[j]
      const min = k === 0 ? lo : 0x80
      const max = k === 0 ? hi : 0xbf
      if (c < min || c > max) return fail(i, j, 'invalid continuation byte')
    }
    i = j
  }
  return null
}

// ---------------------------------------------------------------- digests

function hex(bytes: Uint8Array): string {
  let s = ''
  for (const b of bytes) s += b.toString(16).padStart(2, '0')
  return s
}

const K256 = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01,
  0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
  0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08,
  0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
])

/** The message padded as SHA-1 and SHA-256 pad it (big-endian length) or MD5 (little-endian). */
function padded(data: Uint8Array, littleEndian: boolean): DataView {
  const n = data.length
  const total = (((n + 8) >> 6) + 1) << 6
  const buf = new Uint8Array(total)
  buf.set(data)
  buf[n] = 0x80
  const view = new DataView(buf.buffer)
  const bits = n * 8
  const hiBits = Math.floor(n / 0x20000000)
  if (littleEndian) {
    view.setUint32(total - 8, bits >>> 0, true)
    view.setUint32(total - 4, hiBits, true)
  } else {
    view.setUint32(total - 8, hiBits)
    view.setUint32(total - 4, bits >>> 0)
  }
  return view
}

/** hashlib.sha256(data).digest() */
export function sha256(data: Uint8Array | string): Uint8Array {
  const view = padded(typeof data === 'string' ? utf8(data) : data, false)
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19])
  const w = new Uint32Array(64)
  for (let off = 0; off < view.byteLength; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4)
    for (let i = 16; i < 64; i++) {
      const a = w[i - 15]
      const b = w[i - 2]
      const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3)
      const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10)
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0
    }
    let [a, b, c, d, e, f, g, hh] = h
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7))
      const ch = (e & f) ^ (~e & g)
      const t1 = (hh + S1 + ch + K256[i] + w[i]) | 0
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10))
      const maj = (a & b) ^ (a & c) ^ (b & c)
      const t2 = (S0 + maj) | 0
      hh = g
      g = f
      f = e
      e = (d + t1) | 0
      d = c
      c = b
      b = a
      a = (t1 + t2) | 0
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh
  }
  const out = new Uint8Array(32)
  const ov = new DataView(out.buffer)
  for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i])
  return out
}

/** hashlib.sha256(data).hexdigest() */
export const sha256Hex = (data: Uint8Array | string): string => hex(sha256(data))

/** hashlib.sha1(data).digest() */
export function sha1(data: Uint8Array | string): Uint8Array {
  const view = padded(typeof data === 'string' ? utf8(data) : data, false)
  let h0 = 0x67452301
  let h1 = 0xefcdab89
  let h2 = 0x98badcfe
  let h3 = 0x10325476
  let h4 = 0xc3d2e1f0
  const w = new Uint32Array(80)
  for (let off = 0; off < view.byteLength; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4)
    for (let i = 16; i < 80; i++) {
      const x = w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]
      w[i] = (x << 1) | (x >>> 31)
    }
    let a = h0
    let b = h1
    let c = h2
    let d = h3
    let e = h4
    for (let i = 0; i < 80; i++) {
      let f: number
      let k: number
      if (i < 20) { f = (b & c) | (~b & d); k = 0x5a827999 }
      else if (i < 40) { f = b ^ c ^ d; k = 0x6ed9eba1 }
      else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc }
      else { f = b ^ c ^ d; k = 0xca62c1d6 }
      const t = (((a << 5) | (a >>> 27)) + f + e + k + w[i]) | 0
      e = d
      d = c
      c = (b << 30) | (b >>> 2)
      b = a
      a = t
    }
    h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0; h4 = (h4 + e) | 0
  }
  const out = new Uint8Array(20)
  const ov = new DataView(out.buffer)
  ;[h0, h1, h2, h3, h4].forEach((v, i) => ov.setUint32(i * 4, v))
  return out
}

const S_MD5 = [7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21]
const K_MD5 = new Int32Array(64).map((_, i) => Math.floor(Math.abs(Math.sin(i + 1)) * 0x100000000) | 0)

/** hashlib.md5(data).digest() */
export function md5(data: Uint8Array | string): Uint8Array {
  const view = padded(typeof data === 'string' ? utf8(data) : data, true)
  let a0 = 0x67452301
  let b0 = 0xefcdab89 | 0
  let c0 = 0x98badcfe | 0
  let d0 = 0x10325476
  const m = new Int32Array(16)
  for (let off = 0; off < view.byteLength; off += 64) {
    for (let i = 0; i < 16; i++) m[i] = view.getInt32(off + i * 4, true)
    let a = a0
    let b = b0
    let c = c0
    let d = d0
    for (let i = 0; i < 64; i++) {
      let f: number
      let g: number
      if (i < 16) { f = (b & c) | (~b & d); g = i }
      else if (i < 32) { f = (d & b) | (~d & c); g = (5 * i + 1) % 16 }
      else if (i < 48) { f = b ^ c ^ d; g = (3 * i + 5) % 16 }
      else { f = c ^ (b | ~d); g = (7 * i) % 16 }
      const t = (a + f + K_MD5[i] + m[g]) | 0
      a = d
      d = c
      c = b
      b = (b + ((t << S_MD5[i]) | (t >>> (32 - S_MD5[i])))) | 0
    }
    a0 = (a0 + a) | 0; b0 = (b0 + b) | 0; c0 = (c0 + c) | 0; d0 = (d0 + d) | 0
  }
  const out = new Uint8Array(16)
  const ov = new DataView(out.buffer)
  ;[a0, b0, c0, d0].forEach((v, i) => ov.setInt32(i * 4, v, true))
  return out
}

export const md5Hex = (data: Uint8Array | string): string => hex(md5(data))
export const sha1Hex = (data: Uint8Array | string): string => hex(sha1(data))
export { hex }

// ---------------------------------------------------------------- uuid5

/** uuid.NAMESPACE_URL */
export const NAMESPACE_URL = '6ba7b811-9dad-11d1-80b4-00c04fd430c8'
/** uuid.NAMESPACE_DNS */
export const NAMESPACE_DNS = '6ba7b810-9dad-11d1-80b4-00c04fd430c8'

/** str(uuid.uuid5(namespace, name)) */
export function uuid5(namespace: string, name: string): string {
  const ns = new Uint8Array(16)
  const h = namespace.replace(/[{}-]/g, '')
  for (let i = 0; i < 16; i++) ns[i] = parseInt(h.slice(i * 2, i * 2 + 2), 16)
  const nm = utf8(name)
  const buf = new Uint8Array(16 + nm.length)
  buf.set(ns)
  buf.set(nm, 16)
  const d = sha1(buf).slice(0, 16)
  d[6] = (d[6] & 0x0f) | 0x50
  d[8] = (d[8] & 0x3f) | 0x80
  const x = hex(d)
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`
}

// ---------------------------------------------------------------- base64

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/** base64.b64encode(data).decode() */
export function base64(data: Uint8Array): string {
  let out = ''
  let i = 0
  for (; i + 2 < data.length; i += 3) {
    const v = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
    out += B64[v >> 18] + B64[(v >> 12) & 63] + B64[(v >> 6) & 63] + B64[v & 63]
  }
  if (i < data.length) {
    const v = (data[i] << 16) | ((data[i + 1] ?? 0) << 8)
    out += B64[v >> 18] + B64[(v >> 12) & 63] + (i + 1 < data.length ? B64[(v >> 6) & 63] : '=') + '='
  }
  return out
}

/** base64.b64decode(text) */
export function fromBase64(text: string): Uint8Array {
  const clean = text.replace(/[^A-Za-z0-9+/]/g, '')
  const out = new Uint8Array(Math.floor(clean.length * 3 / 4))
  let o = 0
  for (let i = 0; i < clean.length; i += 4) {
    const v = (B64.indexOf(clean[i]) << 18) | (B64.indexOf(clean[i + 1]) << 12) | ((B64.indexOf(clean[i + 2] ?? 'A') & 63) << 6) | (B64.indexOf(clean[i + 3] ?? 'A') & 63)
    out[o++] = v >> 16
    if (i + 2 < clean.length) out[o++] = (v >> 8) & 255
    if (i + 3 < clean.length) out[o++] = v & 255
  }
  return out.slice(0, o)
}

/** Several byte arrays as one. */
export function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const p of parts) {
    out.set(p, at)
    at += p.length
  }
  return out
}

/**
 * The cross-section archive's exact coding of float32 transects (tools/xs-grid/xsgrid/codec.py):
 * each float32 as an order-preserving uint32, then second differences in wrap-around arithmetic.
 * `decode` reverses it bit for bit.
 */

/** float32 samples from the stored int32 codes (two cumulative sums mod 2^32, then the key undone). */
export function decode(codes: ArrayLike<number>): Float32Array {
  const n = codes.length
  const k = new Uint32Array(n)
  let acc = 0
  for (let i = 0; i < n; i++) {
    acc = (acc + (codes[i] >>> 0)) >>> 0
    k[i] = acc
  }
  acc = 0
  for (let i = 0; i < n; i++) {
    acc = (acc + k[i]) >>> 0
    k[i] = acc
  }
  for (let i = 0; i < n; i++) {
    const v = k[i]
    k[i] = (v & 0x80000000 ? v ^ 0x80000000 : ~v) >>> 0
  }
  return new Float32Array(k.buffer)
}

/** The int32 codes of float32 samples (the archive's `codec.encode`). */
export function encode(z: Float32Array): Int32Array {
  const u = new Uint32Array(z.buffer.slice(z.byteOffset, z.byteOffset + z.byteLength))
  for (let i = 0; i < u.length; i++) {
    const v = u[i]
    u[i] = (v & 0x80000000 ? ~v : v ^ 0x80000000) >>> 0
  }
  for (let pass = 0; pass < 2; pass++) {
    for (let i = u.length - 1; i > 0; i--) u[i] = (u[i] - u[i - 1]) >>> 0
  }
  return new Int32Array(u.buffer)
}

/** `numpy.linspace(-wide, wide, n)`: `i * step + start`, the last exactly `stop`. */
export function stations(wide: number, n: number): Float64Array {
  const out = new Float64Array(n)
  if (n === 1) {
    out[0] = -wide
    return out
  }
  const step = (wide - -wide) / (n - 1)
  for (let i = 0; i < n; i++) out[i] = i * step + -wide
  out[n - 1] = wide
  return out
}

// Python's number formatting, for text the Shiny apps write with f-strings.
//
// f"{x:.4f}" rounds the double's exact binary value half to even; JavaScript's toFixed rounds an
// exact tie away from zero ("pick the larger n"). The two agree except on exact ties, which only
// dyadic values with few decimals hit (39.03125 to four places), so pyFixed asks toFixed and fixes
// the tie case from the double's exact value.

const view = new DataView(new ArrayBuffer(8))

/** ``x`` as m * 2**e with m an integer (exact); null for 0, infinities and NaN. */
function binary(x: number): { m: bigint, e: number } | null {
  if (!Number.isFinite(x) || x === 0) return null
  view.setFloat64(0, Math.abs(x))
  const hi = view.getUint32(0)
  const lo = view.getUint32(4)
  const exp = (hi >>> 20) & 0x7ff
  let m = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo)
  if (exp) m |= 1n << 52n
  return { m, e: (exp || 1) - 1075 }
}

/** The digit string of |x| * 10**digits when that is an exact half (n + 0.5), else null. */
function exactHalf(x: number, digits: number): bigint | null {
  const b = binary(x)
  if (!b || b.e >= 0) return null
  // |x| * 10**(digits+1) = m * 10**(digits+1) / 2**(-e): an integer ending in 5 is a tie
  const num = b.m * 10n ** BigInt(digits + 1)
  const den = 1n << BigInt(-b.e)
  if (num % den !== 0n) return null
  const n = num / den
  return n % 10n === 5n ? n / 10n : null
}

/** str(x) for a Python float (repr: the shortest round trip, "12.0" for a whole number, an exponent
 * below 1e-4 and from 1e16 on, written "1e-05"). */
export function pyFloat(x: number): string {
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'nan' : x > 0 ? 'inf' : '-inf'
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0'
  const ax = Math.abs(x)
  if (ax < 1e-4 || ax >= 1e16) {
    const [m, e] = x.toExponential().split('e')
    const exp = Number(e)
    return `${m}e${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`
  }
  const s = String(x)
  return Number.isInteger(x) ? `${s}.0` : s
}

/** f"{x:+.{digits}f}": the sign always written. */
export function pySigned(x: number, digits: number): string {
  const text = pyFixed(x, digits)
  return text.startsWith('-') ? text : `+${text}`
}

/** f"{x:,.{digits}f}": fixed digits with thousands separators. */
export function pyFixedComma(x: number, digits: number): string {
  const text = pyFixed(x, digits)
  const neg = text.startsWith('-')
  const [whole, frac] = (neg ? text.slice(1) : text).split('.')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return (neg ? '-' : '') + grouped + (frac !== undefined ? `.${frac}` : '')
}

/** f"{x:.{digits}f}" */
export function pyFixed(x: number, digits: number): string {
  if (Object.is(x, -0)) return '-' + (0).toFixed(digits)
  const text = x.toFixed(digits)
  const tie = exactHalf(x, digits)
  if (tie === null || tie % 2n !== 0n) return text   // no tie, or toFixed's away-from-zero is even
  // the tie rounds down to the even neighbour: |x| truncated at ``digits``
  const s = tie.toString().padStart(digits + 1, '0')
  const body = digits ? `${s.slice(0, -digits)}.${s.slice(-digits)}` : s
  return (x < 0 ? '-' : '') + body
}

/** f"{x:g}": six significant digits, trailing zeros dropped, an exponent below 1e-4 and from 1e6. */
export function pyG(x: number): string {
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'nan' : x > 0 ? 'inf' : '-inf'
  if (x === 0) return Object.is(x, -0) ? '-0' : '0'
  const [m, e] = x.toExponential(5).split('e')
  const exp = Number(e)
  const strip = (s: string) => (s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s)
  if (exp < -4 || exp >= 6) return `${strip(m)}e${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`
  return strip(pyFixed(x, 5 - exp))
}

/** math.fsum: the exactly rounded sum (CPython's Shewchuk partials, with its half-even fix-up across
 * partials). */
export function fsum(values: Iterable<number>): number {
  const p: number[] = []
  let special = 0.0
  let infSum = 0.0
  for (const value of values) {
    let x = value
    const xsave = x
    let i = 0
    for (let j = 0; j < p.length; j++) {
      let y = p[j]
      if (Math.abs(x) < Math.abs(y)) {
        const t = x
        x = y
        y = t
      }
      const hi = x + y
      const lo = y - (hi - x)
      if (lo !== 0.0) p[i++] = lo
      x = hi
    }
    p.length = i
    if (x !== 0.0) {
      if (!Number.isFinite(x)) {
        if (Number.isFinite(xsave)) throw new RangeError('intermediate overflow in fsum')
        if (!Number.isNaN(xsave)) infSum += xsave
        special += xsave
        p.length = 0
      } else p.push(x)
    }
  }
  if (special !== 0.0) {
    if (Number.isNaN(infSum)) throw new RangeError('-inf + inf in fsum')
    return special
  }
  let hi = 0.0
  let n = p.length
  if (n > 0) {
    hi = p[--n]
    let lo = 0.0
    while (n > 0) {
      const x = hi
      const y = p[--n]
      hi = x + y
      lo = y - (hi - x)
      if (lo !== 0.0) break
    }
    if (n > 0 && ((lo < 0.0 && p[n - 1] < 0.0) || (lo > 0.0 && p[n - 1] > 0.0))) {
      const y = lo * 2.0
      const x = hi + y
      if (y === x - hi) hi = x
    }
  }
  return hi
}

/** Dekker's error-free product (CPython's dl_mul without an FMA): [x*y, the rounding error]. */
function dlMul(x: number, y: number): [number, number] {
  const split = (v: number): [number, number] => {
    const t = v * 134217729.0
    const hi = t - (t - v)
    return [hi, v - hi]
  }
  const [xh, xl] = split(x)
  const [yh, yl] = split(y)
  const p = xh * yh
  const q = xh * yl + xl * yh
  const z = p + q
  return [z, p - z + q + xl * yl]
}

/** math.hypot(x, y) as CPython 3.12 computes it (vector_norm: lossless scaling and squaring, then a
 * differential correction), which JavaScript's Math.hypot can miss by an ulp. */
export function pyHypot(...coords: number[]): number {
  const vec = coords.map((v) => Math.abs(v))
  let max = 0.0
  let nan = false
  for (const v of vec) {
    if (Number.isNaN(v)) nan = true
    else if (v > max) max = v
  }
  if (max === Infinity) return max
  if (nan) return NaN
  if (max === 0.0 || vec.length <= 1) return max
  // frexp(max): max = m * 2**e, 0.5 <= m < 1
  const view = new DataView(new ArrayBuffer(8))
  view.setFloat64(0, max)
  const bits = (view.getUint32(0) >>> 20) & 0x7ff
  if (bits === 0) {
    const DBL_MIN = 2.2250738585072014e-308
    return DBL_MIN * pyHypot(...vec.map((v) => v / DBL_MIN))
  }
  const maxE = bits - 1022
  const scale = 2 ** -maxE
  let csum = 1.0
  let frac1 = 0.0
  let frac2 = 0.0
  for (let x of vec) {
    x *= scale
    const [prHi, prLo] = dlMul(x, x)
    const s = csum + prHi
    const sLo = (csum - s) + prHi
    csum = s
    frac1 += prLo
    frac2 += sLo
  }
  let h = Math.sqrt(csum - 1.0 + (frac1 + frac2))
  const [pHi, pLo] = dlMul(-h, h)
  const s = csum + pHi
  const sLo = (csum - s) + pHi
  csum = s
  frac1 += pLo
  frac2 += sLo
  const x = csum - 1.0 + (frac1 + frac2)
  h += x / (2.0 * h)
  return h / scale
}

/** Python 3.12's built-in sum() over a mix of ints and floats, an integral value taken for an int:
 * ints added exactly until the first float, then the floats Neumaier-compensated while later ints are
 * added plainly (CPython 3.12's int and float loops; sum([1e16, 1, 1, -1e16]) is 0.0 there). */
export function pySumMixed(values: Iterable<number>): number {
  let iResult = 0
  let floatPhase = false
  let f = 0
  let c = 0
  for (const x of values) {
    if (!floatPhase) {
      if (Number.isInteger(x)) iResult += x
      else {
        f = iResult + x
        floatPhase = true
      }
      continue
    }
    if (Number.isInteger(x)) {
      f += x
      continue
    }
    const t = f + x
    if (Math.abs(f) >= Math.abs(x)) c += (f - t) + x
    else c += (x - t) + f
    f = t
  }
  if (!floatPhase) return iResult
  if (c && Number.isFinite(c)) f += c
  return f
}

/** Python 3.12's built-in sum() of floats (from the default start 0): the first item exactly, then
 * Neumaier's compensated summation, the compensation added at the end (CPython builtin_sum_impl). */
export function pySum(values: Iterable<number>): number {
  let first = true
  let f = 0
  let c = 0
  for (const x of values) {
    if (first) {
      f = x
      first = false
      continue
    }
    const t = f + x
    if (Math.abs(f) >= Math.abs(x)) c += (f - t) + x
    else c += (x - t) + f
    f = t
  }
  if (c && Number.isFinite(c)) f += c
  return f
}

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

/** str(x) for a Python float in the range the apps print (repr: shortest round trip, "12.0" for a
 * whole number). */
export function pyFloat(x: number): string {
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'nan' : x > 0 ? 'inf' : '-inf'
  const s = String(x)
  return Number.isInteger(x) && Math.abs(x) < 1e16 && !s.includes('e') ? `${s}.0` : s
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
  const text = x.toFixed(digits)
  const tie = exactHalf(x, digits)
  if (tie === null || tie % 2n !== 0n) return text   // no tie, or toFixed's away-from-zero is even
  // the tie rounds down to the even neighbour: |x| truncated at ``digits``
  const s = tie.toString().padStart(digits + 1, '0')
  const body = digits ? `${s.slice(0, -digits)}.${s.slice(-digits)}` : s
  return (x < 0 ? '-' : '') + body
}

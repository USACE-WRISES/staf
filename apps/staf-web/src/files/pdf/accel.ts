// reportlab's lib/rl_accel.py, the pure-Python paths it takes without its C extension: numbers as a
// page's operators write them (fp_str), bytes escaped for a PDF string, ASCII base-85.
import { pyFixed } from '../../app/pyfmt'

const LOG_E_10 = Math.log(10.0)

/** fp_str: each number with at most six decimals (fewer as it grows), trailing zeros and a leading
 * zero dropped, joined by spaces. */
export function fpStr(...args: (number | number[])[]): string {
  const a: number[] = args.length === 1 && Array.isArray(args[0]) ? args[0] : (args as number[])
  const s: string[] = []
  for (const i of a) {
    const sa = Math.abs(i)
    if (sa <= 1e-7) {
      s.push('0')
      continue
    }
    const l = sa <= 1 ? 6 : Math.min(Math.max(0, 6 - Math.trunc(Math.log(sa) / LOG_E_10)), 6)
    let n = pyFixed(i, l)
    if (l) {
      let j = n.length
      while (j) {
        j -= 1
        if (n[j] !== '0') {
          if (n[j] !== '.') j += 1
          break
        }
      }
      n = n.slice(0, j)
    }
    s.push(n[0] !== '0' || n.length === 1 ? n : n.slice(1))
  }
  return s.join(' ')
}

const ESCAPE: string[] = []
for (let c = 0; c < 256; c++) {
  if (c < 32 || c >= 127) ESCAPE[c] = '\\' + c.toString(8).padStart(3, '0')
  else if (c === 0x5c || c === 0x28 || c === 0x29) ESCAPE[c] = '\\' + String.fromCharCode(c)
  else ESCAPE[c] = String.fromCharCode(c)
}

/** escapePDF: bytes for a PDF string, \ ( ) escaped and the rest outside printable ASCII as octal. */
export function escapePDF(bytes: Uint8Array | number[]): string {
  let out = ''
  for (const b of bytes) out += ESCAPE[b]
  return out
}

/** asciiBase85Encode: four bytes to five characters (z for four zeros), the tail cut, then ~>. */
export function asciiBase85Encode(input: Uint8Array): string {
  const whole = Math.floor(input.length / 4)
  const rem = input.length % 4
  const out: string[] = []
  const five = (num: number): string => {
    const c5 = num % 85
    let t = Math.floor(num / 85)
    const c4 = t % 85
    t = Math.floor(t / 85)
    const c3 = t % 85
    t = Math.floor(t / 85)
    const c2 = t % 85
    const c1 = Math.floor(t / 85)
    return String.fromCharCode(c1 + 33, c2 + 33, c3 + 33, c4 + 33, c5 + 33)
  }
  for (let i = 0; i < whole; i++) {
    const o = i * 4
    const num = input[o] * 16777216 + input[o + 1] * 65536 + input[o + 2] * 256 + input[o + 3]
    out.push(num === 0 ? 'z' : five(num))
  }
  if (rem > 0) {
    const o = whole * 4
    const b = [0, 1, 2, 3].map((k) => (k < rem ? input[o + k] : 0))
    out.push(five(b[0] * 16777216 + b[1] * 65536 + b[2] * 256 + b[3]).slice(0, rem + 1))
  }
  out.push('~>')
  return out.join('')
}

// reportlab's lib/colors.py, what the reports use: Color (with its alpha, which makes reportlab declare
// transparency when one is set), HexColor, toColor of a "#rrggbb" string, black and white.

export class Color {
  constructor(public red = 0, public green = 0, public blue = 0, public alpha = 1) {}

  rgb(): [number, number, number] {
    return [this.red, this.green, this.blue]
  }
}

/** HexColor: "#rrggbb", "0xrrggbb" or a number as 0xRRGGBB. */
export function HexColor(val: string | number): Color {
  let v: number
  if (typeof val === 'string') {
    if (val.startsWith('#')) v = parseInt(val.slice(1), 16)
    else if (val.slice(0, 2).toLowerCase() === '0x') v = parseInt(val.slice(2), 16)
    else v = parseInt(val, 10)
    if (Number.isNaN(v)) throw new Error(`ValueError: invalid literal for int(): '${val}'`)
  } else v = val
  return new Color(((v >> 16) & 0xff) / 255.0, ((v >> 8) & 0xff) / 255.0, (v & 0xff) / 255.0)
}

export const black = HexColor(0x000000)
export const white = HexColor(0xffffff)

/** toColor: a Color as it is, or a "#rrggbb" string read as HexColor reads it. */
export function toColor(arg: Color | string | number[]): Color {
  if (arg instanceof Color) return arg
  if (Array.isArray(arg)) {
    if (arg.length !== 3) throw new Error('only an RGB sequence is supported here')
    return new Color(arg[0], arg[1], arg[2])
  }
  if (/^#[0-9a-fA-F]{6}$/.test(arg)) return HexColor(arg)
  if (arg.toLowerCase() === 'black') return black
  if (arg.toLowerCase() === 'white') return white
  throw new Error(`ValueError: Invalid color value '${arg}'`)
}

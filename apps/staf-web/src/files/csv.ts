// CSV as Python's csv.writer writes it with its defaults (the "excel" dialect): fields joined by
// commas, a field quoted only when it holds a comma, a quote or a line break (quotes doubled), rows
// ended by CRLF, None as an empty field and anything else as its str(); a row of one empty field is
// written "" so it reads back as a field. Numbers are written with pyStr (Python's str of an int or a
// float): pass a float that is whole as a PyNum to write it "5.0".
import { pyFloat } from '../app/pyfmt'

/** A number Python holds as a float (written "5.0" when whole). */
export class PyNum {
  constructor(readonly value: number) {}
}

export type Cell = string | number | boolean | null | undefined | PyNum

/** str(x) of a Python value: an int, a float (repr), True/False, None as "" (the csv writer's). */
export function pyStr(v: Cell): string {
  if (v === null || v === undefined) return ''
  if (v instanceof PyNum) return pyFloat(v.value)
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') {
    if (Number.isNaN(v)) return 'nan'
    if (!Number.isFinite(v)) return v > 0 ? 'inf' : '-inf'
    return Number.isInteger(v) ? String(Object.is(v, -0) ? 0 : v) : pyFloat(v)
  }
  return v
}

const NEEDS_QUOTE = /[",\r\n]/

function field(v: Cell): string {
  const text = pyStr(v)
  return NEEDS_QUOTE.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text
}

/** writer.writerow(row) */
export function csvRow(row: Cell[]): string {
  const text = row.map(field).join(',')
  if (row.length > 0 && text === '') return '""\r\n'
  return text + '\r\n'
}

/** Every row, as a csv.writer writes them into a StringIO. */
export function csvText(rows: Cell[][]): string {
  return rows.map(csvRow).join('')
}

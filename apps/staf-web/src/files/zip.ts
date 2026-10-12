// ZIP archives as Python's zipfile reads and writes them. Reading: every entry's metadata from the
// central directory (its name, method, times, attributes, extra field and comment), in the directory's
// order, and each member's bytes on demand. Writing: ZipFile(buf, "w").writestr(info, data) byte for
// byte: the local header with the info's versions and times, data deflated as zlib does at its default
// level (pako, whose output equals zlib's), then the central directory and its end record. The
// workbook writers copy a template's entries through with their metadata, as the tools do.
import { deflateRaw } from 'pako'

export interface ZipEntry {
  /** the name as decoded (UTF-8 when flag bit 11 is set, else cp437, as zipfile decodes it) */
  name: string
  versionMadeBy: number
  versionNeeded: number
  flags: number
  method: number
  dosTime: number
  dosDate: number
  crc: number
  compressedSize: number
  size: number
  internalAttr: number
  externalAttr: number
  /** the central directory's extra field and comment */
  extra: Uint8Array
  comment: Uint8Array
  localOffset: number
  /** where the member's data starts (after its local header) */
  dataStart: number
}

export interface ZipArchive {
  entries: ZipEntry[]
  /** the archive comment */
  comment: Uint8Array
  has(name: string): boolean
  entry(name: string): ZipEntry
  /** a member's bytes, inflated (each read once) */
  read(name: string): Promise<Uint8Array>
}

/** cp437, the code page zipfile decodes a name without the UTF-8 flag in */
const CP437 = '\u00c7\u00fc\u00e9\u00e2\u00e4\u00e0\u00e5\u00e7\u00ea\u00eb\u00e8\u00ef\u00ee\u00ec\u00c4\u00c5\u00c9\u00e6\u00c6\u00f4\u00f6\u00f2\u00fb\u00f9\u00ff\u00d6\u00dc\u00a2\u00a3\u00a5\u20a7\u0192\u00e1\u00ed\u00f3\u00fa\u00f1\u00d1\u00aa\u00ba\u00bf\u2310\u00ac\u00bd\u00bc\u00a1\u00ab\u00bb\u2591\u2592\u2593\u2502\u2524\u2561\u2562\u2556\u2555\u2563\u2551\u2557\u255d\u255c\u255b\u2510\u2514\u2534\u252c\u251c\u2500\u253c\u255e\u255f\u255a\u2554\u2569\u2566\u2560\u2550\u256c\u2567\u2568\u2564\u2565\u2559\u2558\u2552\u2553\u256b\u256a\u2518\u250c\u2588\u2584\u258c\u2590\u2580\u03b1\u00df\u0393\u03c0\u03a3\u03c3\u00b5\u03c4\u03a6\u0398\u03a9\u03b4\u221e\u03c6\u03b5\u2229\u2261\u00b1\u2265\u2264\u2320\u2321\u00f7\u2248\u00b0\u2219\u00b7\u221a\u207f\u00b2\u25a0\u00a0'

function decodeName(bytes: Uint8Array, utf8: boolean): string {
  if (utf8) return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  let out = ''
  for (const b of bytes) out += b < 128 ? String.fromCharCode(b) : CP437[b - 128]
  return out
}

/** Raw deflate, inflated (the browser's own inflater; the output never depends on it). */
export async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([data as BlobPart]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

/** The archive's central directory (zipfile.ZipFile(...).infolist()). */
export function readZip(data: Uint8Array): ZipArchive {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength)
  let eocd = -1
  for (let i = data.length - 22; i >= Math.max(0, data.length - 22 - 65535); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) {
      eocd = i
      break
    }
  }
  if (eocd < 0) throw new Error('File is not a zip file')
  const count = dv.getUint16(eocd + 10, true)
  let p = dv.getUint32(eocd + 16, true)
  const comment = data.slice(eocd + 22, eocd + 22 + dv.getUint16(eocd + 20, true))
  const entries: ZipEntry[] = []
  for (let k = 0; k < count; k++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error('Bad magic number for central directory')
    const flags = dv.getUint16(p + 8, true)
    const nlen = dv.getUint16(p + 28, true)
    const xlen = dv.getUint16(p + 30, true)
    const clen = dv.getUint16(p + 32, true)
    const localOffset = dv.getUint32(p + 42, true)
    const name = decodeName(data.subarray(p + 46, p + 46 + nlen), (flags & 0x800) !== 0)
    entries.push({
      name, versionMadeBy: dv.getUint16(p + 4, true), versionNeeded: dv.getUint16(p + 6, true), flags, method: dv.getUint16(p + 10, true),
      dosTime: dv.getUint16(p + 12, true), dosDate: dv.getUint16(p + 14, true), crc: dv.getUint32(p + 16, true),
      compressedSize: dv.getUint32(p + 20, true), size: dv.getUint32(p + 24, true), internalAttr: dv.getUint16(p + 36, true),
      externalAttr: dv.getUint32(p + 38, true), extra: data.slice(p + 46 + nlen, p + 46 + nlen + xlen),
      comment: data.slice(p + 46 + nlen + xlen, p + 46 + nlen + xlen + clen), localOffset,
      dataStart: localOffset + 30 + dv.getUint16(localOffset + 26, true) + dv.getUint16(localOffset + 28, true),
    })
    p += 46 + nlen + xlen + clen
  }
  const byName = new Map<string, ZipEntry>()
  for (const e of entries) byName.set(e.name, e)       // zipfile's NameToInfo: the last of a repeated name
  const reads = new Map<string, Promise<Uint8Array>>()
  const entry = (name: string): ZipEntry => {
    const e = byName.get(name)
    if (!e) throw new Error(`There is no item named '${name}' in the archive`)
    return e
  }
  return {
    entries, comment, entry, has: (name) => byName.has(name),
    read(name: string): Promise<Uint8Array> {
      let got = reads.get(name)
      if (!got) {
        const e = entry(name)
        const raw = data.subarray(e.dataStart, e.dataStart + e.compressedSize)
        if (e.method === 0) got = Promise.resolve(raw.slice())
        else if (e.method === 8) got = inflateRaw(raw)
        else got = Promise.reject(new Error(`compression type ${e.method} is not supported`))
        reads.set(name, got)
      }
      return got
    },
  }
}

// ---------------------------------------------------------------- writing (zipfile.ZipFile(..., "w"))

export const ZIP_STORED = 0
export const ZIP_DEFLATED = 8
/** zipfile.ZipInfo's create_system for a new entry: 0 where Python runs on Windows, 3 elsewhere. The
 * tools' Windows runs are the reference the parity checks compare against. */
export const NEW_ENTRY_SYSTEM = 0
const DEFAULT_VERSION = 20

/** A zipfile.ZipInfo: what writestr writes for one member. */
export interface ZipInfo {
  name: string
  dosTime: number
  dosDate: number
  compressType: number
  createVersion: number
  createSystem: number
  extractVersion: number
  reserved: number
  internalAttr: number
  externalAttr: number
  extra: Uint8Array
  comment: Uint8Array
}

/** The ZipInfo zipfile reads for an entry (its central directory record). */
export function infoOf(e: ZipEntry): ZipInfo {
  return {
    name: e.name, dosTime: e.dosTime, dosDate: e.dosDate, compressType: e.method, createVersion: e.versionMadeBy & 0xff,
    createSystem: e.versionMadeBy >> 8, extractVersion: e.versionNeeded & 0xff, reserved: e.versionNeeded >> 8,
    internalAttr: e.internalAttr, externalAttr: e.externalAttr, extra: e.extra, comment: e.comment,
  }
}

/** zipfile.ZipInfo(name, date_time=...): a new entry, dated 1980-01-01 00:00 unless ``time`` (a read
 * entry's DOS time and date) is given. */
export function newInfo(name: string, time: { dosTime: number, dosDate: number } = { dosTime: 0, dosDate: (1 << 5) | 1 }): ZipInfo {
  return {
    name, dosTime: time.dosTime, dosDate: time.dosDate, compressType: ZIP_STORED, createVersion: DEFAULT_VERSION, createSystem: NEW_ENTRY_SYSTEM,
    extractVersion: DEFAULT_VERSION, reserved: 0, internalAttr: 0, externalAttr: 0, extra: new Uint8Array(0), comment: new Uint8Array(0),
  }
}

let CRC_TABLE: Uint32Array | null = null

/** zlib.crc32 */
export function crc32(data: Uint8Array): number {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256)
    for (let n = 0; n < 256; n++) {
      let c = n
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
      CRC_TABLE[n] = c >>> 0
    }
  }
  let crc = 0xffffffff
  for (let i = 0; i < data.length; i++) crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

/** zlib.compressobj(Z_DEFAULT_COMPRESSION, DEFLATED, -15): raw deflate at level 6 */
export const deflate = (data: Uint8Array): Uint8Array => deflateRaw(data, { level: 6 })

interface Written { info: ZipInfo, name: Uint8Array, flags: number, crc: number, csize: number, usize: number, offset: number }

/** zipfile.ZipFile(io.BytesIO(), "w"): members written in order, then the central directory. */
export class ZipWriter {
  private chunks: Uint8Array[] = []
  private size = 0
  private written: Written[] = []

  private push(b: Uint8Array): void {
    this.chunks.push(b)
    this.size += b.length
  }

  /** ZipFile.writestr(info, data): the info's metadata, the data compressed by its compress type. */
  writestr(info: ZipInfo, data: Uint8Array | string): void {
    const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data
    const ascii = /^[\x00-\x7f]*$/.test(info.name)
    const name = new TextEncoder().encode(info.name)
    const flags = ascii ? 0 : 0x800
    let body: Uint8Array
    if (info.compressType === ZIP_DEFLATED) body = deflate(bytes)
    else if (info.compressType === ZIP_STORED) body = bytes
    else throw new Error(`compression type ${info.compressType} is not supported`)
    const w: Written = { info, name, flags, crc: crc32(bytes), csize: body.length, usize: bytes.length, offset: this.size }
    const head = new Uint8Array(30 + name.length + info.extra.length)
    const dv = new DataView(head.buffer)
    dv.setUint32(0, 0x04034b50, true)
    head[4] = info.extractVersion
    head[5] = info.reserved
    dv.setUint16(6, flags, true)
    dv.setUint16(8, info.compressType, true)
    dv.setUint16(10, info.dosTime, true)
    dv.setUint16(12, info.dosDate, true)
    dv.setUint32(14, w.crc, true)
    dv.setUint32(18, w.csize, true)
    dv.setUint32(22, w.usize, true)
    dv.setUint16(26, name.length, true)
    dv.setUint16(28, info.extra.length, true)
    head.set(name, 30)
    head.set(info.extra, 30 + name.length)
    this.push(head)
    this.push(body)
    this.written.push(w)
  }

  /** ZipFile.close(): the central directory and the end record; the archive's bytes. */
  finish(): Uint8Array {
    const start = this.size
    for (const w of this.written) {
      const { info } = w
      const rec = new Uint8Array(46 + w.name.length + info.extra.length + info.comment.length)
      const dv = new DataView(rec.buffer)
      dv.setUint32(0, 0x02014b50, true)
      rec[4] = info.createVersion
      rec[5] = info.createSystem
      rec[6] = info.extractVersion
      rec[7] = info.reserved
      dv.setUint16(8, w.flags, true)
      dv.setUint16(10, info.compressType, true)
      dv.setUint16(12, info.dosTime, true)
      dv.setUint16(14, info.dosDate, true)
      dv.setUint32(16, w.crc, true)
      dv.setUint32(20, w.csize, true)
      dv.setUint32(24, w.usize, true)
      dv.setUint16(28, w.name.length, true)
      dv.setUint16(30, info.extra.length, true)
      dv.setUint16(32, info.comment.length, true)
      dv.setUint16(34, 0, true)
      dv.setUint16(36, info.internalAttr, true)
      dv.setUint32(38, info.externalAttr >>> 0, true)
      dv.setUint32(42, w.offset, true)
      rec.set(w.name, 46)
      rec.set(info.extra, 46 + w.name.length)
      rec.set(info.comment, 46 + w.name.length + info.extra.length)
      this.push(rec)
    }
    const end = new Uint8Array(22)
    const dv = new DataView(end.buffer)
    dv.setUint32(0, 0x06054b50, true)
    dv.setUint16(8, this.written.length, true)
    dv.setUint16(10, this.written.length, true)
    dv.setUint32(12, this.size - start, true)
    dv.setUint32(16, start, true)
    this.push(end)
    const out = new Uint8Array(this.size)
    let p = 0
    for (const c of this.chunks) {
      out.set(c, p)
      p += c.length
    }
    return out
  }
}

/** _repack: the template rewritten with some parts replaced and every entry's metadata carried through
 * (zout.writestr(copy.copy(info), data) over zin.infolist()). */
export async function repack(source: Uint8Array, replacements: Map<string, Uint8Array>): Promise<Uint8Array> {
  const zin = readZip(source)
  const zout = new ZipWriter()
  for (const e of zin.entries) zout.writestr(infoOf(e), replacements.get(e.name) ?? await zin.read(e.name))
  return zout.finish()
}

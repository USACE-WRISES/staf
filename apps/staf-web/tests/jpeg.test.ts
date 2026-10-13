// The JPEG decoder (src/files/pdf/jpeg.ts) against Pillow: each file of parity/make_jpeg_fixtures.py decodes to
// the samples Pillow's Image.open(...).tobytes() gives (their MD5), which reportlab names a JPEG photo's
// XObject after.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { md5Hex } from '../src/files/bytes'
import { decodeJpeg } from '../src/files/pdf/jpeg'

const DIR = new URL('./fixtures/jpeg/', import.meta.url)
interface Case { name: string, width: number, height: number, mode: string, md5: string }
const { cases } = JSON.parse(readFileSync(new URL('index.json', DIR), 'utf-8')) as { cases: Case[] }

describe('decodeJpeg', () => {
  for (const c of cases) {
    it(c.name, () => {
      const d = decodeJpeg(new Uint8Array(readFileSync(new URL(`${c.name}.jpg`, DIR))))
      expect([d.width, d.height, d.mode]).toEqual([c.width, c.height, c.mode])
      expect(md5Hex(d.data)).toBe(c.md5)
    })
  }
})

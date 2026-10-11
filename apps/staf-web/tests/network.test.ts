// The bundle's lines of a view read by HTTP range, against the Python reference (streams_ref.json):
// the same features in the same order. Reads the local browser layout (STAF_WEB_DATA, default
// D:\Data\staf-web\site) through a stand-in fetch; skipped where it is not.
import { existsSync, openSync, readSync, closeSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'
import { hrFeatures, Network, v2Features } from '../src/streams/network'
import streams from './fixtures/streams_ref.json'

const ROOT = process.env.STAF_WEB_DATA ?? 'D:/Data/staf-web/site'
const BASE = 'http://local.test/'
const have = existsSync(join(ROOT, 'web-manifest.json'))

function read(path: string, start: number, end: number): Uint8Array<ArrayBuffer> {
  const fd = openSync(path, 'r')
  try {
    const buf = new Uint8Array(new ArrayBuffer(end - start + 1))
    readSync(fd, buf, 0, buf.length, start)
    return buf
  } finally {
    closeSync(fd)
  }
}

async function localFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input instanceof Request ? input.url : input)
  const path = join(ROOT, decodeURIComponent(url.slice(BASE.length).split('?')[0]))
  const size = statSync(path).size
  const m = /bytes=(\d+)-(\d+)/.exec(new Headers(init?.headers).get('range') ?? '')
  const [start, end] = m ? [Number(m[1]), Math.min(Number(m[2]), size - 1)] : [0, size - 1]
  const body = read(path, start, end)
  return new Response(body, {
    status: m ? 206 : 200,
    headers: { 'content-length': String(body.length), ...(m ? { 'content-range': `bytes ${start}-${end}/${size}` } : {}) },
  })
}

describe.skipIf(!have)('stream lines from the bundle', () => {
  beforeAll(() => {
    vi.stubGlobal('fetch', localFetch)
  })
  afterAll(() => vi.unstubAllGlobals())

  for (const c of (streams as { cases: any[] }).cases) {
    it(`reads ${c.name}'s HR and V2 lines as the Shiny tools do`, async () => {
      const src = new DataSource(BASE)
      await src.load()
      const [w, s, e, n] = c.bbox
      const hr = hrFeatures(await new Network(src, 'bundle/').inBox(w, s, e, n))
      const v2 = v2Features(await new Network(src, 'bundle/v2/').inBox(w, s, e, n))
      expect(JSON.stringify(hr)).toBe(JSON.stringify(c.hr))
      expect(JSON.stringify(v2)).toBe(JSON.stringify(c.v2))
    })
  }
})

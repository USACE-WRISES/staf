// DataSource: once a file's read is redirected (Hugging Face sends each file to a signed CDN address),
// later ranges go there, not through the data host's rate-limited /resolve/; an expired address
// sends the read back through the host.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DataSource } from '../src/data/source'

const BASE = 'https://host.test/resolve/main/'
// larger than hyparquet's whole-file cache threshold (512 KB), so each slice is its own read
const FILE = Uint8Array.from({ length: 600_000 }, (_v, i) => (i * 13) & 255)

function answer(body: Uint8Array, url: string, redirected: boolean, status = 206): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    url,
    redirected,
    headers: new Headers({ 'content-length': String(body.length) }),
    arrayBuffer: async () => body.slice().buffer,
    json: async () => JSON.parse(new TextDecoder().decode(body)),
  } as unknown as Response
}

function host(expired: Set<string>) {
  const calls: string[] = []
  let signature = 0
  const f = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    calls.push(url)
    if (url.endsWith('web-manifest.json')) {
      const m = { format: 1, built: 'x', sources: {}, regions: [],
                  files: { 'xs/a.parquet': { path: 'xs/a.1234.parquet', bytes: FILE.length, sha256: '', kind: 'region' } } }
      return answer(new TextEncoder().encode(JSON.stringify(m)), url, false, 200)
    }
    if (expired.has(url)) return answer(new Uint8Array(0), url, false, 403)
    const m = /bytes=(\d+)-(\d+)/.exec(new Headers(init?.headers).get('range') ?? '')!
    const body = FILE.slice(Number(m[1]), Number(m[2]) + 1)
    if (url.startsWith(BASE)) return answer(body, `https://cdn.test/a?sig=${++signature}`, true)
    return answer(body, url, false)
  }
  return { f, calls }
}

describe('DataSource', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads through the CDN address a file redirected to, and goes back when it expires', async () => {
    const expired = new Set<string>()
    const { f, calls } = host(expired)
    vi.stubGlobal('fetch', f)
    const src = new DataSource(BASE)
    await src.load()
    const file = src.file('xs/a.parquet')
    expect(new Uint8Array(await file.slice(0, 100))).toEqual(FILE.slice(0, 100))
    expect(new Uint8Array(await file.slice(100_000, 100_100))).toEqual(FILE.slice(100_000, 100_100))
    expect(calls.slice(1)).toEqual([`${BASE}xs/a.1234.parquet`, 'https://cdn.test/a?sig=1'])
    expect(src.hostRequests).toBe(2) // the manifest and the first range
    expired.add('https://cdn.test/a?sig=1')
    expect(new Uint8Array(await file.slice(200_000, 200_100))).toEqual(FILE.slice(200_000, 200_100))
    expect(calls.slice(3)).toEqual(['https://cdn.test/a?sig=1', `${BASE}xs/a.1234.parquet`])
    expect(new Uint8Array(await file.slice(300_000, 300_100))).toEqual(FILE.slice(300_000, 300_100))
    expect(calls.at(-1)).toBe('https://cdn.test/a?sig=2')
    expect(src.hostRequests).toBe(3)
  })
})

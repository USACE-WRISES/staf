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
    if (url.endsWith('web-index.json.gz')) return answer(new Uint8Array(0), url, false, 404)
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
    expect(calls.slice(2)).toEqual([`${BASE}xs/a.1234.parquet`, 'https://cdn.test/a?sig=1'])
    expect(src.hostRequests).toBe(3) // the index (none), the manifest and the first range
    expired.add('https://cdn.test/a?sig=1')
    expect(new Uint8Array(await file.slice(200_000, 200_100))).toEqual(FILE.slice(200_000, 200_100))
    expect(calls.slice(4)).toEqual(['https://cdn.test/a?sig=1', `${BASE}xs/a.1234.parquet`])
    expect(new Uint8Array(await file.slice(300_000, 300_100))).toEqual(FILE.slice(300_000, 300_100))
    expect(calls.at(-1)).toBe('https://cdn.test/a?sig=2')
    expect(src.hostRequests).toBe(4)
  })

  it("reads the build's gzipped index in place of the manifest when there is one", async () => {
    const index = { format: 1, built: 'y', regions: ['0101'],
                    files: { 'xs/a.parquet': ['xs/a.1234.parquet', FILE.length], 'library/m.zip': ['library/m.ab.zip', 9, 'abc'] },
                    footers: { 'xs/a.parquet': 4321 } }
    const gz = new Uint8Array(await new Response(new Blob([JSON.stringify(index)]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer())
    const calls: string[] = []
    vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
      calls.push(String(input))
      if (String(input).endsWith('web-index.json.gz')) return new Response(gz, { status: 200 })
      return new Response('not found', { status: 404 })
    })
    const src = new DataSource(BASE)
    const m = await src.load()
    expect(calls).toEqual([`${BASE}web-index.json.gz`])
    expect(m.regions).toEqual(['0101'])
    expect(src.entry('xs/a.parquet')).toEqual({ path: 'xs/a.1234.parquet', bytes: FILE.length, sha256: '', footer: 4321 })
    expect(src.entry('library/m.zip').sha256).toBe('abc')
    expect(src.entry('library/m.zip').footer).toBeUndefined()
  })

  it('unzips a file stored gzipped, and takes it as it is from a host that unzipped it', async () => {
    const content = new TextEncoder().encode(JSON.stringify({ features: Array.from({ length: 300 }, (_, i) => ({ id: i })) }))
    const zip = async (b: Uint8Array) => new Uint8Array(await new Response(new Blob([b as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer())
    const stored = await zip(content)
    const index = { format: 1, built: 'y', regions: [], files: { 'library/r.json': ['library/r.ab.json.gz', stored.length, 'c'] },
                    gzip: { 'library/r.json': content.length } }
    const gz = await zip(new TextEncoder().encode(JSON.stringify(index)))
    let unzipped = false
    vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('web-index.json.gz')) return new Response(gz as BlobPart, { status: 200 })
      if (url === `${BASE}library/r.ab.json.gz`) return new Response((unzipped ? content : stored) as BlobPart, { status: 200 })
      return new Response('not found', { status: 404 })
    })
    const src = new DataSource(BASE)
    await src.load()
    expect(src.entry('library/r.json')).toMatchObject({ encoding: 'gzip', raw_bytes: content.length, bytes: stored.length })
    expect(new Uint8Array(await src.bytesOf('library/r.json'))).toEqual(content)
    unzipped = true
    expect(new Uint8Array(await src.bytesOf('library/r.json'))).toEqual(content)
  })

  it('reads the slices asked for together as one request where they sit close', async () => {
    const { f, calls } = host(new Set())
    vi.stubGlobal('fetch', f)
    const src = new DataSource(BASE)
    await src.load()
    const file = src.file('xs/a.parquet')
    // three slices within 32 KB of each other and one far off, asked for in the same turn
    const wanted: [number, number][] = [[10_000, 10_500], [20_000, 21_000], [12_000, 12_100], [400_000, 400_200]]
    const got = await Promise.all(wanted.map(([a, b]) => file.slice(a, b)))
    got.forEach((buf, i) => expect(new Uint8Array(buf)).toEqual(FILE.slice(wanted[i][0], wanted[i][1])))
    // the first read goes through the host; the far slice in the same turn goes there too
    expect(calls.length - 2).toBe(2)
    // a slice asked for again comes from the cache
    expect(new Uint8Array(await file.slice(20_000, 21_000))).toEqual(FILE.slice(20_000, 21_000))
    expect(calls.length - 2).toBe(2)
  })

  it('keeps what a visit read for the next (ranges and whole files), and drops what the index no longer names', async () => {
    const stores = new Map<string, FakeCache>()
    vi.stubGlobal('caches', {
      open: async (name: string) => stores.get(name) ?? stores.set(name, new FakeCache()).get(name),
      keys: async () => [...stores.keys()],
      delete: async (name: string) => stores.delete(name),
    })
    stores.set('staf-data-0', new FakeCache())   // an older layout's cache
    const { f, calls } = host(new Set())
    vi.stubGlobal('fetch', f)
    const wanted: [number, number][] = [[10_000, 10_500], [12_000, 12_100], [400_000, 400_200]]
    const visit = async () => {
      const src = new DataSource(BASE)
      await src.load()
      const file = src.file('xs/a.parquet')
      const got = await Promise.all(wanted.map(([a, b]) => file.slice(a, b)))
      got.forEach((buf, i) => expect(new Uint8Array(buf)).toEqual(FILE.slice(wanted[i][0], wanted[i][1])))
      await new Promise((ok) => setTimeout(ok, 10))   // the cache's writes land
      return src
    }
    const first = await visit()
    expect(first.cache.stats.hits).toBe(0)
    expect([...stores.keys()]).toEqual(['staf-data-1'])
    const read = calls.length
    const second = await visit()
    expect(calls.length - read).toBe(2)   // the index and the manifest only
    expect(second.cache.stats).toEqual({ hits: 3, bytes: 800 })
    // a whole file comes back from the cache too
    second.manifest!.files['library/r.json'] = { path: 'library/r.ab.json', bytes: 3, sha256: '' }
    vi.stubGlobal('fetch', async () => answer(new TextEncoder().encode('[1]'), 'x', false, 200))
    expect(new TextDecoder().decode(await second.bytesOf('library/r.json'))).toBe('[1]')
    await new Promise((ok) => setTimeout(ok, 10))
    vi.stubGlobal('fetch', async () => { throw new Error('read from the host') })
    expect(new TextDecoder().decode(await second.bytesOf('library/r.json'))).toBe('[1]')
    // pruning: the index names only the parquet file, and the cap leaves room for 600 bytes
    const cache = stores.get('staf-data-1')!
    expect(await second.cache.prune(new Map([['xs/a.1234.parquet', FILE.length]]), 600)).toBe(2)
    expect([...cache.m.keys()].map((k) => k.replace('https://staf-data.invalid/', ''))).toEqual(['xs/a.1234.parquet?r=12000-12100', 'xs/a.1234.parquet?r=400000-400200'])
  })
})

/** Cache Storage's part the data cache uses, in memory. */
class FakeCache {
  m = new Map<string, ArrayBuffer>()
  async match(key: RequestInfo | URL): Promise<Response | undefined> {
    const buf = this.m.get(String(key instanceof Request ? key.url : key))
    return buf ? new Response(buf.slice(0)) : undefined
  }

  async put(key: RequestInfo | URL, res: Response): Promise<void> {
    this.m.set(String(key instanceof Request ? key.url : key), await res.arrayBuffer())
  }

  async keys(): Promise<Request[]> {
    return [...this.m.keys()].map((k) => new Request(k))
  }

  async delete(key: RequestInfo | URL): Promise<boolean> {
    return this.m.delete(String(key instanceof Request ? key.url : key))
  }
}

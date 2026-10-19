/**
 * The data the app has read, kept in the browser between visits (Cache Storage).
 *
 * The data host's redirects can't be cached (Hugging Face answers a large file with a `no-store`
 * redirect to a signed address that changes every time), so without this a second visit reads
 * everything again: the EASI method package, every footer, every page. Stored files never change in
 * place (their names carry their content's hash), so what was read under a path is good for as long
 * as the data's index names that path. Kept: whole files (`bytesOf`) under their path, and the ranges
 * read by `DataSource.file` under their path and bytes. A session's first idle moment drops what the
 * current index no longer names, then the oldest entries past `CAP`.
 *
 * Where Cache Storage is missing or refuses (a page not served over HTTPS, some private windows, a
 * full disk), reads go to the host as before.
 */

/** The cache's name: a new layout of the entries takes a new name (and drops the old cache). */
export const NAME = 'staf-data-1'
/** The data's index as the last visit read it, one per data address, for a visit without the network
 * (`DataSource.load`). A cache of its own, so `prune` never drops it. */
export const INDEX_NAME = 'staf-index-1'
/** Keys are addresses nothing serves (only Cache Storage sees them). */
const ORIGIN = 'https://staf-data.invalid/'
/** The most kept, in bytes. */
export const CAP = 1024 * 1024 * 1024

export interface Stats { hits: number, bytes: number }

export class DataCache {
  readonly stats: Stats = { hits: 0, bytes: 0 }
  private opened: Promise<Cache | null> | null = null

  constructor(private readonly enabled = true) {}

  private open(): Promise<Cache | null> {
    this.opened ??= (async () => {
      if (!this.enabled || typeof caches === 'undefined') return null
      try {
        for (const name of await caches.keys()) {
          if (name.startsWith('staf-data-') && name !== NAME) await caches.delete(name)
        }
        return await caches.open(NAME)
      } catch {
        return null
      }
    })()
    return this.opened
  }

  private key(path: string, start?: number, end?: number): string {
    return ORIGIN + path + (start === undefined ? '' : `?r=${start}-${end}`)
  }

  private async get(key: string, bytes: number): Promise<ArrayBuffer | undefined> {
    const cache = await this.open()
    if (!cache) return undefined
    try {
      const res = await cache.match(key)
      if (!res) return undefined
      const buf = await res.arrayBuffer()
      if (buf.byteLength !== bytes) return undefined
      this.stats.hits += 1
      this.stats.bytes += bytes
      return buf
    } catch {
      return undefined
    }
  }

  private put(key: string, buf: ArrayBuffer): void {
    void this.open().then((cache) => cache?.put(key, new Response(buf.slice(0)))).catch(() => {})
  }

  /** A stored file read whole, or undefined. */
  file(path: string, bytes: number): Promise<ArrayBuffer | undefined> {
    return this.get(this.key(path), bytes)
  }

  keepFile(path: string, buf: ArrayBuffer): void {
    this.put(this.key(path), buf)
  }

  /** Bytes `start` to `end` of a stored file, as read before, or undefined. */
  range(path: string, start: number, end: number): Promise<ArrayBuffer | undefined> {
    return this.get(this.key(path, start, end), end - start)
  }

  keepRange(path: string, start: number, end: number, buf: ArrayBuffer): void {
    this.put(this.key(path, start, end), buf)
  }

  /** The index's bytes as the last visit read them from ``base``, or undefined. */
  async index(base: string): Promise<ArrayBuffer | undefined> {
    if (!this.enabled || typeof caches === 'undefined') return undefined
    try {
      const res = await (await caches.open(INDEX_NAME)).match(ORIGIN + 'index?base=' + encodeURIComponent(base))
      return res ? await res.arrayBuffer() : undefined
    } catch {
      return undefined
    }
  }

  /** Keeps the index just read from ``base`` (its bytes as stored) for a later visit without the network. */
  keepIndex(base: string, buf: ArrayBuffer): void {
    if (!this.enabled || typeof caches === 'undefined') return
    void caches.open(INDEX_NAME)
      .then((cache) => cache.put(ORIGIN + 'index?base=' + encodeURIComponent(base), new Response(buf.slice(0))))
      .catch(() => {})
  }

  /** Drops what `sizes` (stored path -> bytes: the current index) no longer names, then the oldest
   * entries until the rest fit in `cap`; returns how many were dropped. */
  async prune(sizes: Map<string, number>, cap = CAP): Promise<number> {
    const cache = await this.open()
    if (!cache) return 0
    let dropped = 0
    try {
      const kept: { req: Request, bytes: number }[] = []
      const stale: Request[] = []
      for (const req of await cache.keys()) {
        const url = new URL(req.url)
        const path = decodeURIComponent(url.pathname.slice(1))
        const size = sizes.get(path)
        if (size === undefined) {
          stale.push(req)
          continue
        }
        const r = /^(\d+)-(\d+)$/.exec(url.searchParams.get('r') ?? '')
        kept.push({ req, bytes: r ? Number(r[2]) - Number(r[1]) : size })
      }
      let total = kept.reduce((s, k) => s + k.bytes, 0)
      for (const k of kept) {   // oldest first: Cache Storage lists keys in the order they were put
        if (total <= cap) break
        stale.push(k.req)
        total -= k.bytes
      }
      for (let i = 0; i < stale.length; i += 64) {
        const done = await Promise.all(stale.slice(i, i + 64).map((req) => cache.delete(req)))
        dropped += done.filter(Boolean).length
      }
    } catch {
      // a cache gone meanwhile
    }
    return dropped
  }
}

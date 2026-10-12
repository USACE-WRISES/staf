/**
 * Where the app's data comes from: the browser layout of STAF's releases (tools/staf-web-data), read
 * by HTTP range. `web-manifest.json` maps each logical file (`bundle/lines2_0108.parquet`,
 * `xs/xs-0710.parquet`, ...) to its content-hashed path, size and sha256; stored files never change
 * in place. Every request is counted, so the app can report what a click costs.
 */
import { cachedAsyncBuffer, type AsyncBuffer } from 'hyparquet'
import { DataCache } from './cache'
import { fetchPolitely, fetchRange, HttpError } from './range'
import { SignedAddresses } from './signed'

export interface ManifestFile {
  path: string
  bytes: number
  /** the full manifest's for every file; the app's index keeps the library's only (else '') */
  sha256: string
  kind?: 'core' | 'table' | 'region' | 'library' | 'engine'
  region?: string
  /** stored gzipped (a file read whole: JSON, GeoJSON); `bytes` is the stored size, `raw_bytes` the content's */
  encoding?: 'gzip'
  raw_bytes?: number
  /** a parquet file's footer with its length and magic: the bytes its first read takes from the end */
  footer?: number
}

export interface WebManifest {
  format: number
  built: string
  sources: Record<string, Record<string, unknown>>
  regions: string[]
  files: Record<string, ManifestFile>
  /** the app's index only: each bundle dataset's regions (folder -> vpu -> what datasetRegions reads) */
  datasets?: Record<string, Record<string, any>>
  /** the app's index only: the bundle manifest's sources (its vintages) */
  bundleSources?: Record<string, any>
}

/** Files smaller than this are read whole at their first read (hyparquet's default is 512 KB, which read
 * the small regions' paged tables whole: 0108's road crossings, 328 KB, for a headwater's few). */
const WHOLE = 64 * 1024
/** Ranges closer than this merge into one request (the gap is read and dropped). */
const MERGE_GAP = 32 * 1024
/** ...up to this size a request. */
const MERGE_MAX = 8 * 1024 * 1024

interface Wanted { start: number, end: number, resolve: (b: ArrayBuffer) => void, reject: (e: unknown) => void }

/** Where ranges read before are kept (the browser's cache of the data, `DataCache`). */
export interface RangeStore {
  get(start: number, end: number): Promise<ArrayBuffer | undefined>
  put(start: number, end: number, buf: ArrayBuffer): void
}

/** ``raw`` with the slices asked for together read as few requests: hyparquet asks for every column chunk,
 * page index and page on its own (all of a plan's at once), and a page's offset indexes sit side by side.
 * The batch closes at the end of the current task's microtasks (a timer would be slowed to once a second
 * in a background tab). Slices kept in `store` come from it, the batch's others from the host. */
function merging(raw: AsyncBuffer, store?: RangeStore): AsyncBuffer {
  let queue: Wanted[] = []
  const flush = async () => {
    let wanted = queue.sort((a, b) => a.start - b.start || a.end - b.end)
    queue = []
    if (store) {
      const kept = await Promise.all(wanted.map((w) => store.get(w.start, w.end).catch(() => undefined)))
      wanted = wanted.filter((w, i) => {
        const buf = kept[i]
        if (buf) w.resolve(buf)
        return !buf
      })
    }
    const runs: { start: number, end: number, items: Wanted[] }[] = []
    for (const w of wanted) {
      const last = runs[runs.length - 1]
      if (last && w.start <= last.end + MERGE_GAP && Math.max(last.end, w.end) - last.start <= MERGE_MAX) {
        last.end = Math.max(last.end, w.end)
        last.items.push(w)
      } else {
        runs.push({ start: w.start, end: w.end, items: [w] })
      }
    }
    for (const run of runs) {
      Promise.resolve(raw.slice(run.start, run.end)).then((buf) => {
        for (const w of run.items) {
          const part = buf.slice(w.start - run.start, w.end - run.start)
          store?.put(w.start, w.end, part)
          w.resolve(part)
        }
      }, (err) => {
        for (const w of run.items) w.reject(err)
      })
    }
  }
  return {
    byteLength: raw.byteLength,
    slice(start: number, end?: number): Promise<ArrayBuffer> {
      return new Promise((resolve, reject) => {
        if (!queue.length) queueMicrotask(flush)
        queue.push({ start, end: end ?? raw.byteLength, resolve, reject })
      })
    },
  }
}

export class DataSource {
  readonly base: string
  manifest: WebManifest | null = null
  requests = 0
  /** Requests to the data host itself: Hugging Face allows 3,000 `/resolve/` requests per IP every
   * five minutes, so reads go to the address a file redirected to once it is known. */
  hostRequests = 0
  bytes = 0
  /** Waits after the host refused (429) or was busy (503); `onBusy` is told of each, to show. */
  busyWaits = 0
  onBusy: ((ms: number) => void) | null = null
  /** What earlier visits read (`DataCache`), and the signed addresses files redirected to within the
   * hour (`SignedAddresses`); both off with `{cache: false}`. */
  readonly cache: DataCache
  readonly signed: SignedAddresses
  private buffers = new Map<string, AsyncBuffer>()

  constructor(base: string, opts: { cache?: boolean } = {}) {
    this.base = base.endsWith('/') ? base : base + '/'
    this.cache = new DataCache(opts.cache ?? true)
    this.signed = new SignedAddresses(opts.cache ?? true)
  }

  /** `fetch`, counted (requests, and bytes as the server reports them). */
  readonly fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    this.requests += 1
    if (String(input instanceof Request ? input.url : input).startsWith(this.base)) this.hostRequests += 1
    const res = await fetch(input, init)
    this.bytes += Number(res.headers.get('content-length') ?? 0)
    return res
  }

  /** The data's index: `web-index.json.gz` (the build's gzipped copy of what the app reads, a twelfth of
   * the manifest's size; the host sends neither compressed), else `web-manifest.json`. */
  async load(): Promise<WebManifest> {
    const idx = await fetchPolitely(this.base + 'web-index.json.gz', { cache: 'no-cache' }, this.polite())
    if (idx.ok && idx.body && typeof DecompressionStream !== 'undefined') {
      const text = await new Response(idx.body.pipeThrough(new DecompressionStream('gzip'))).text()
      const raw = JSON.parse(text) as { format: number, built: string, regions: string[], files: Record<string, [string, number, string?]>,
        gzip?: Record<string, number>, footers?: Record<string, number>, datasets?: Record<string, Record<string, any>>,
        bundle_sources?: Record<string, any> }
      const files: Record<string, ManifestFile> = {}
      for (const [logical, [path, bytes, sha256]] of Object.entries(raw.files)) {
        files[logical] = { path, bytes, sha256: sha256 ?? '' }
        const unzipped = raw.gzip?.[logical]
        if (unzipped !== undefined) Object.assign(files[logical], { encoding: 'gzip', raw_bytes: unzipped })
        const footer = raw.footers?.[logical]
        if (footer !== undefined) files[logical].footer = footer
      }
      this.manifest = { format: raw.format, built: raw.built, sources: {}, regions: raw.regions, files, datasets: raw.datasets,
        bundleSources: raw.bundle_sources && Object.keys(raw.bundle_sources).length ? raw.bundle_sources : undefined }
      return this.manifest
    }
    try {
      await idx.body?.cancel()
    } catch {
      // a body already gone
    }
    const res = await fetchPolitely(this.base + 'web-manifest.json', { cache: 'no-cache' }, this.polite())
    if (!res.ok) throw new Error(`web-manifest.json: HTTP ${res.status}`)
    this.manifest = (await res.json()) as WebManifest
    return this.manifest
  }

  private loading: Promise<WebManifest> | null = null

  /** The manifest, read once (a failed read is tried again on the next call). Once read, the cache
   * drops what it no longer names, after the page has had its first reads. */
  ready(): Promise<WebManifest> {
    if (this.manifest) return Promise.resolve(this.manifest)
    this.loading ??= this.load().then((m) => {
      if (typeof caches !== 'undefined') {
        const sizes = new Map(Object.values(m.files).map((e) => [e.path, e.bytes]))
        setTimeout(() => void this.cache.prune(sizes), 10_000)
      }
      return m
    }, (err) => {
      this.loading = null
      throw err
    })
    return this.loading
  }

  entry(logical: string): ManifestFile {
    const e = this.manifest?.files[logical]
    if (!e) throw new Error(`not in the data manifest: ${logical}`)
    return e
  }

  has(logical: string): boolean {
    return Boolean(this.manifest?.files[logical])
  }

  url(logical: string): string {
    return this.base + this.entry(logical).path
  }

  /** A range-read buffer over a logical file (its size comes from the manifest: no HEAD request).
   * Every range is held to the bytes asked for (`fetchRange`). When the data host redirects (Hugging
   * Face sends each file to a signed CDN address), later ranges go to that address, in this visit and
   * the next ones within its hour (`SignedAddresses`), until it stops answering, then through the host
   * again. */
  file(logical: string): AsyncBuffer {
    let b = this.buffers.get(logical)
    if (!b) {
      const e = this.entry(logical)
      const home = this.base + e.path
      let at = this.signed.get(e.path) ?? home
      const read = (url: string, start: number, end: number) => fetchRange(url, start, end, {
        ...this.polite(),
        fileSize: e.bytes,
        onResponse: (res) => {
          if (res.redirected && res.url) {
            at = res.url
            this.signed.remember(e.path, res.url)
          }
        },
      })
      const raw: AsyncBuffer = {
        byteLength: e.bytes,
        slice: async (start: number, end?: number) => {
          const stop = end ?? e.bytes
          const url = at
          if (url === home) return read(home, start, stop)
          try {
            return await read(url, start, stop)
          } catch (err) {
            if (!(err instanceof HttpError)) throw err
            if (at === url) at = home // the signed address expired
            this.signed.forget(e.path)
            return read(home, start, stop)
          }
        },
      }
      const store: RangeStore = {
        get: (start, end) => this.cache.range(e.path, start, end),
        put: (start, end, buf) => this.cache.keepRange(e.path, start, end, buf),
      }
      // a file under WHOLE is read in one request; a larger one by its footer (its size is in the index:
      // one exact request), then the column chunks or pages a read needs
      b = cachedAsyncBuffer(merging(raw, store), { minSize: WHOLE })
      this.buffers.set(logical, b)
    }
    return b
  }

  /** The counted fetch, with refusals counted and reported. */
  private polite() {
    return {
      fetch: this.fetch,
      onBusy: (ms: number) => {
        this.busyWaits += 1
        this.onBusy?.(ms)
      },
    }
  }

  /** Bytes of a whole (small) logical file; one stored gzipped comes unzipped. */
  async bytesOf(logical: string): Promise<ArrayBuffer> {
    const e = this.entry(logical)
    const kept = await this.cache.file(e.path, e.bytes)
    if (kept) {
      try {
        return e.encoding === 'gzip' ? await unzipped(kept, e) : kept
      } catch {
        // a damaged copy: read it again
      }
    }
    let url = this.signed.get(e.path) ?? this.url(logical)
    for (let attempt = 0; attempt < 3; attempt++) {
      const res = await fetchPolitely(url, { cache: attempt === 0 ? 'default' : 'no-store' }, this.polite())
      if (!res.ok && url !== this.url(logical)) {
        // a remembered address the CDN refused: look the file up again
        try {
          await res.body?.cancel()
        } catch {
          // a body already gone
        }
        this.signed.forget(e.path)
        url = this.url(logical)
        attempt -= 1
        continue
      }
      if (!res.ok) throw new Error(`${logical}: HTTP ${res.status}`)
      if (res.redirected && res.url) this.signed.remember(e.path, res.url)
      const buf = await res.arrayBuffer()
      if (e.encoding !== 'gzip') {
        if (buf.byteLength !== e.bytes) continue
        this.cache.keepFile(e.path, buf)
        return buf
      }
      const head = new Uint8Array(buf, 0, Math.min(2, buf.byteLength))
      if (head[0] === 0x1f && head[1] === 0x8b) {
        if (buf.byteLength !== e.bytes) continue
        try {
          const out = await unzipped(buf, e)
          this.cache.keepFile(e.path, buf)
          return out
        } catch {
          continue
        }
      } else if (buf.byteLength === e.raw_bytes) {
        return buf   // a host that unzipped it on the way (Content-Encoding)
      }
    }
    throw new Error(`${logical}: arrived short three times`)
  }
}

/** A gzipped stored file's content, held to its size. */
async function unzipped(buf: ArrayBuffer, e: ManifestFile): Promise<ArrayBuffer> {
  const out = await gunzip(buf)
  if (out.byteLength !== e.raw_bytes) throw new Error(`${e.path}: unzipped to ${out.byteLength} bytes, not ${e.raw_bytes}`)
  return out
}

async function gunzip(buf: ArrayBuffer): Promise<ArrayBuffer> {
  return new Response(new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer()
}

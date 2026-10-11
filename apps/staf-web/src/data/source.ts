/**
 * Where the app's data comes from: the browser layout of STAF's releases (tools/staf-web-data), read
 * by HTTP range. `web-manifest.json` maps each logical file (`bundle/lines2_0108.parquet`,
 * `xs/xs-0710.parquet`, ...) to its content-hashed path, size and sha256; stored files never change
 * in place. Every request is counted, so the app can report what a click costs.
 */
import { cachedAsyncBuffer, type AsyncBuffer } from 'hyparquet'
import { fetchPolitely, fetchRange, HttpError } from './range'

export interface ManifestFile {
  path: string
  bytes: number
  sha256: string
  kind: 'core' | 'table' | 'region'
  region?: string
}

export interface WebManifest {
  format: number
  built: string
  sources: Record<string, Record<string, unknown>>
  regions: string[]
  files: Record<string, ManifestFile>
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
  private buffers = new Map<string, AsyncBuffer>()

  constructor(base: string) {
    this.base = base.endsWith('/') ? base : base + '/'
  }

  /** `fetch`, counted (requests, and bytes as the server reports them). */
  readonly fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    this.requests += 1
    if (String(input instanceof Request ? input.url : input).startsWith(this.base)) this.hostRequests += 1
    const res = await fetch(input, init)
    this.bytes += Number(res.headers.get('content-length') ?? 0)
    return res
  }

  async load(): Promise<WebManifest> {
    const res = await fetchPolitely(this.base + 'web-manifest.json', { cache: 'no-cache' }, this.polite())
    if (!res.ok) throw new Error(`web-manifest.json: HTTP ${res.status}`)
    this.manifest = (await res.json()) as WebManifest
    return this.manifest
  }

  private loading: Promise<WebManifest> | null = null

  /** The manifest, read once (a failed read is tried again on the next call). */
  ready(): Promise<WebManifest> {
    if (this.manifest) return Promise.resolve(this.manifest)
    this.loading ??= this.load().catch((err) => {
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
   * Face sends each file to a signed CDN address), later ranges go to that address until it stops
   * answering, then through the host again. */
  file(logical: string): AsyncBuffer {
    let b = this.buffers.get(logical)
    if (!b) {
      const e = this.entry(logical)
      const home = this.base + e.path
      let at = home
      const read = (url: string, start: number, end: number) => fetchRange(url, start, end, {
        ...this.polite(),
        fileSize: e.bytes,
        onResponse: (res) => {
          if (res.redirected && res.url) at = res.url
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
            return read(home, start, stop)
          }
        },
      }
      b = cachedAsyncBuffer(raw)
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

  /** Bytes of a whole (small) logical file. */
  async bytesOf(logical: string): Promise<ArrayBuffer> {
    const e = this.entry(logical)
    for (let attempt = 0; attempt < 3; attempt++) {
      const res = await fetchPolitely(this.url(logical), { cache: attempt === 0 ? 'default' : 'no-store' }, this.polite())
      if (!res.ok) throw new Error(`${logical}: HTTP ${res.status}`)
      const buf = await res.arrayBuffer()
      if (buf.byteLength === e.bytes) return buf
    }
    throw new Error(`${logical}: arrived short three times`)
  }
}

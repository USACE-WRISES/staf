/**
 * One HTTP range read, checked before anything decodes it.
 *
 * A response can arrive short with no error: on 2026-10-11, 3 of 213 block reads of USGS's 3DEP tiles
 * in the app's browser pane resolved with 1 to 3 KB of the 0.25 to 0.75 MB asked for, while the
 * response's Content-Length said the full size. Decoded, such a block is silently wrong. So every body
 * is held to the bytes asked for (and to the Content-Length, which CORS always exposes; Content-Range
 * it often doesn't), and a body that fails is read again with the HTTP cache bypassed. A dropped
 * connection (fetch's TypeError; one in 2,005 reads in headless Edge the same day) is read again too.
 * After `tries` reads the error is thrown, never a short buffer. A host that refuses for now (429, 503)
 * is asked again after a growing wait (`fetchPolitely`).
 */

export interface RangeOptions {
  /** The file's size when known (the data manifest knows it): a range past the end is clipped to it. */
  fileSize?: number
  /** Reads before giving up (the first plus retries). */
  tries?: number
  signal?: AbortSignal
  /** The fetch to use (the app's counted fetch, or a test's). */
  fetch?: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  /** Told of the response a returned body came in (its final `url` after any redirect). */
  onResponse?: (res: Response) => void
  /** Told each time the host refuses for now (HTTP 429, or 503) and the read waits `ms` to ask again. */
  onBusy?: (ms: number) => void
  /** How long a read keeps asking a refusing host before it gives up, in ms (default 6 minutes: Hugging
   * Face counts requests in 5-minute windows, and a page can't read when one ends). */
  busyFor?: number
  /** Waits (tests replace it). */
  sleep?: (ms: number) => Promise<void>
}

export class ShortReadError extends Error {}

/** An HTTP error answer (a signed address that expired answers 403). */
export class HttpError extends Error {
  constructor(readonly url: string, readonly status: number) {
    super(`${url}: HTTP ${status}`)
  }
}

/** The host refused (429) or was busy (503) for longer than the read would wait. */
export class BusyError extends HttpError {}

/** The wait after the `n`th refusal in a row: 2, 4, 8, 16, 32 s, then 60 s, each up to a quarter longer
 * at random so that a page's refused reads don't all ask again at once. */
export function busyWait(n: number, random: () => number = Math.random): number {
  return Math.round(Math.min(60_000, 2000 * 2 ** n) * (1 + 0.25 * random()))
}

/** `fetch`, asked again while the host refuses for now. Hugging Face answers 429 once an IP has made
 * 3,000 resolver requests in its 5-minute window, and its RateLimit header is not one a page may read,
 * so the waits grow until the window has surely turned. */
export async function fetchPolitely(url: string, init: RequestInit, opts: RangeOptions = {}): Promise<Response> {
  const f = opts.fetch ?? fetch
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((ok) => setTimeout(ok, ms)))
  const budget = opts.busyFor ?? 6 * 60_000
  let waited = 0
  for (let n = 0; ; n++) {
    const res = await f(url, init)
    if (res.status !== 429 && res.status !== 503) return res
    const ms = busyWait(n)
    if (waited + ms > budget || opts.signal?.aborted) throw new BusyError(url, res.status)
    try {
      await res.body?.cancel() // free the connection
    } catch {
      // a body already gone
    }
    opts.onBusy?.(ms)
    await sleep(ms)
    waited += ms
  }
}

/** Bytes `start` to `end` (exclusive) of `url`. */
export async function fetchRange(url: string, start: number, end: number, opts: RangeOptions = {}): Promise<ArrayBuffer> {
  const f = opts.fetch ?? fetch
  const tries = Math.max(1, opts.tries ?? 3)
  const stop = opts.fileSize === undefined ? end : Math.min(end, opts.fileSize)
  const want = stop - start
  if (want <= 0) return new ArrayBuffer(0)
  let problem = ''
  for (let attempt = 0; attempt < tries; attempt++) {
    let res: Response
    let buf: ArrayBuffer
    try {
      res = await fetchPolitely(url, {
        headers: { Range: `bytes=${start}-${stop - 1}` },
        signal: opts.signal,
        // a retry skips the HTTP cache, in case the short copy came from it
        cache: attempt === 0 ? 'default' : 'no-store',
      }, { ...opts, fetch: f })
      if (!res.ok) throw new HttpError(url, res.status)
      buf = await res.arrayBuffer()
    } catch (err) {
      // fetch rejects with a TypeError when the connection fails; anything else (an HTTP error, an
      // abort) is final
      if (!(err instanceof TypeError) || opts.signal?.aborted) throw err
      problem = `the connection failed (${err.message})`
      continue
    }
    const declared = res.headers.get('content-length')
    if (res.status === 200) {
      // the server ignored the range and sent the whole file
      if ((opts.fileSize === undefined || buf.byteLength === opts.fileSize) &&
          (declared === null || Number(declared) === buf.byteLength) && buf.byteLength >= stop) {
        opts.onResponse?.(res)
        return buf.slice(start, stop)
      }
      problem = `whole-file answer of ${buf.byteLength} bytes`
      continue
    }
    if (declared !== null && Number(declared) !== buf.byteLength) {
      problem = `${buf.byteLength} of ${declared} bytes arrived`
      continue
    }
    // past the end of a file of unknown size, the server clips the range (and says so)
    if (buf.byteLength === want || (opts.fileSize === undefined && buf.byteLength < want && declared !== null)) {
      opts.onResponse?.(res)
      return buf
    }
    problem = `${buf.byteLength} of ${want} bytes arrived`
  }
  throw new ShortReadError(`${url} bytes ${start}-${stop - 1}: ${problem} (${tries} reads)`)
}

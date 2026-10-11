// fetchRange: a range that arrives short is read again (bypassing the HTTP cache), never handed on.
import { describe, expect, it } from 'vitest'
import { BusyError, busyWait, fetchRange, ShortReadError } from '../src/data/range'

const FILE = Uint8Array.from({ length: 5000 }, (_v, i) => (i * 7) & 255)

/** A server over FILE; `short` answers (in order) cut the body to that many bytes while the
 * Content-Length still states the full range, as the failing responses did. */
function server(short: number[] = [], whole = false) {
  const calls: RequestInit[] = []
  const f = async (_url: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    calls.push(init ?? {})
    const m = /bytes=(\d+)-(\d+)/.exec(new Headers(init?.headers).get('range') ?? '')!
    const a = Number(m[1])
    const b = Math.min(Number(m[2]) + 1, FILE.length)
    if (whole) return new Response(FILE.slice(), { status: 200, headers: { 'content-length': String(FILE.length) } })
    const body = FILE.slice(a, b)
    const cut = short.length ? short.shift()! : body.length
    return new Response(body.slice(0, cut), { status: 206, headers: { 'content-length': String(body.length) } })
  }
  return { f, calls }
}

describe('fetchRange', () => {
  it('returns the bytes asked for', async () => {
    const { f, calls } = server()
    const buf = await fetchRange('x', 100, 1100, { fetch: f })
    expect(new Uint8Array(buf)).toEqual(FILE.slice(100, 1100))
    expect(calls.length).toBe(1)
  })
  it('reads a short body again, skipping the HTTP cache', async () => {
    const { f, calls } = server([37])
    const buf = await fetchRange('x', 100, 1100, { fetch: f })
    expect(new Uint8Array(buf)).toEqual(FILE.slice(100, 1100))
    expect(calls.map((c) => c.cache)).toEqual(['default', 'no-store'])
  })
  it('throws rather than return a short body', async () => {
    const { f } = server([1, 2, 3])
    await expect(fetchRange('x', 100, 1100, { fetch: f })).rejects.toBeInstanceOf(ShortReadError)
  })
  it('reads again after a dropped connection, but not after an HTTP error', async () => {
    const { f, calls } = server()
    let fails = 1
    const flaky = async (u: RequestInfo | URL, init?: RequestInit) => {
      if (fails-- > 0) throw new TypeError('Failed to fetch')
      return f(u, init)
    }
    expect((await fetchRange('x', 0, 10, { fetch: flaky })).byteLength).toBe(10)
    expect(calls.length).toBe(1)
    const missing = async () => new Response('', { status: 404 })
    await expect(fetchRange('x', 0, 10, { fetch: missing })).rejects.toThrow(/HTTP 404/)
  })
  it('clips to a known file size and slices a whole-file answer', async () => {
    const { f } = server()
    expect((await fetchRange('x', 4900, 6000, { fetch: f, fileSize: 5000 })).byteLength).toBe(100)
    const all = server([], true)
    const buf = await fetchRange('x', 10, 20, { fetch: all.f, fileSize: 5000 })
    expect(new Uint8Array(buf)).toEqual(FILE.slice(10, 20))
  })
  it('waits and asks again while the host refuses or is busy', async () => {
    const { f, calls } = server()
    const refusals = [429, 503]
    const flaky = async (u: RequestInfo | URL, init?: RequestInit) => {
      const status = refusals.shift()
      return status ? new Response('', { status }) : f(u, init)
    }
    const waits: number[] = []
    const told: number[] = []
    const buf = await fetchRange('x', 0, 10, { fetch: flaky, sleep: async (ms) => { waits.push(ms) }, onBusy: (ms) => told.push(ms) })
    expect(new Uint8Array(buf)).toEqual(FILE.slice(0, 10))
    expect(calls.length).toBe(1)
    expect(told).toEqual(waits)
    expect(waits.length).toBe(2)
    expect(waits[1]).toBeGreaterThan(waits[0])
  })
  it('gives up on a host that keeps refusing', async () => {
    const refusing = async () => new Response('', { status: 429 })
    await expect(fetchRange('x', 0, 10, { fetch: refusing, sleep: async () => {}, busyFor: 10_000 }))
      .rejects.toBeInstanceOf(BusyError)
  })
  it('waits 2, 4, 8, 16, 32 s, then a minute, up to a quarter longer', () => {
    expect([0, 1, 2, 3, 4, 5, 9].map((n) => busyWait(n, () => 0))).toEqual([2000, 4000, 8000, 16000, 32000, 60000, 60000])
    expect(busyWait(9, () => 1)).toBe(75000)
  })
})

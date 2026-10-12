// A stand-in fetch over the local browser layout (STAF_WEB_DATA, default D:\Data\staf-web\site), so
// tests read the bundle by range as the app does; ``have`` is false where the layout is not.
import { closeSync, existsSync, openSync, readSync, statSync } from 'node:fs'
import { join } from 'node:path'

export const ROOT = process.env.STAF_WEB_DATA ?? 'D:/Data/staf-web/site'
export const BASE = 'http://local.test/'
export const have = existsSync(join(ROOT, 'web-manifest.json'))

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

const realFetch = globalThis.fetch

export async function localFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = String(input instanceof Request ? input.url : input)
  if (url.startsWith('data:')) return realFetch(input, init)   // a WebAssembly module inlined in a library
  const path = join(ROOT, decodeURIComponent(url.slice(BASE.length).split('?')[0]))
  if (!existsSync(path)) return new Response('not found', { status: 404 })
  const size = statSync(path).size
  const m = /bytes=(\d+)-(\d+)/.exec(new Headers(init?.headers).get('range') ?? '')
  const [start, end] = m ? [Number(m[1]), Math.min(Number(m[2]), size - 1)] : [0, size - 1]
  const body = read(path, start, end)
  return new Response(body, {
    status: m ? 206 : 200,
    headers: { 'content-length': String(body.length), ...(m ? { 'content-range': `bytes ${start}-${end}/${size}` } : {}) },
  })
}

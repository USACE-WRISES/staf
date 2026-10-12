/// <reference types="vitest/config" />
// The STAF static web app's build and dev server.
//
// The app is a section of the STAF website (GitHub Pages from docs/, Jekyll): `npm run build` writes
// it into docs/app/, served at https://usace-wrises.github.io/staf/app/. That folder is generated and
// self-contained: Jekyll copies it as is, so no built name may start with "_" or "." (Jekyll skips
// those) and no Markdown may land there (GitHub Pages would render it). scripts/after-build.mjs
// checks both. Removing the app from the website is deleting docs/app/ (see README).
//
// The page (index.html) gets its stylesheets, shared page scripts and markup from site.ts; page/ is
// the public folder, the files the page serves as is (the app's own copies, see site.ts).
//
// In development `/data/...` serves the browser layout that tools/staf-web-data writes (default
// D:\Data\staf-web\site, or STAF_WEB_DATA) with HTTP range support, standing in for the Hugging Face
// mirror the deployed app reads.
import { createReadStream, readFileSync, statSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import { PAGE, stafSite } from './site'

/** The website path the built app is served under, and the folder of docs/ it is built into. */
export const SITE_PATH = '/staf/app/'
const OUT = fileURLToPath(new URL('../../docs/app', import.meta.url))

const DATA = resolve(process.env.STAF_WEB_DATA ?? 'D:/Data/staf-web/site')

/** `vite preview` over HTTPS, so HTTP/2, as the data host serves (STAF_WEB_CERT and STAF_WEB_KEY: a
 * localhost certificate and key): timings over HTTP/1.1 count six connections a host. */
const HTTPS = process.env.STAF_WEB_CERT && process.env.STAF_WEB_KEY
  ? { cert: readFileSync(process.env.STAF_WEB_CERT), key: readFileSync(process.env.STAF_WEB_KEY) }
  : undefined

/** `/data/...` from the local layout, with range reads (the dev server, and `vite preview` of the build
 * for timing it: open it with `?data=<its origin>/data/`). */
/** Each `/data` answer held back this long (STAF_WEB_DELAY_MS), as a data host far away answers: unlike
 * the browser's throttling, which lets one request through at a time, requests stay concurrent. */
const DELAY = Number(process.env.STAF_WEB_DELAY_MS ?? 0)

function serveData(req: IncomingMessage, res: ServerResponse, next: () => void): void {
  if (DELAY > 0) setTimeout(() => answerData(req, res, next), DELAY)
  else answerData(req, res, next)
}

function answerData(req: IncomingMessage, res: ServerResponse, next: () => void): void {
  const rel = decodeURIComponent((req.url ?? '/').split('?')[0])
  const path = normalize(resolve(DATA, '.' + rel))
  if (path !== DATA && !path.startsWith(DATA + sep)) {
    res.statusCode = 403
    res.end()
    return
  }
  let size: number
  try {
    size = statSync(path).size
  } catch {
    next()
    return
  }
  res.setHeader('Accept-Ranges', 'bytes')
  res.setHeader('Cache-Control', 'no-cache')
  const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '')
  if (range) {
    const start = range[1] === '' ? Math.max(0, size - Number(range[2])) : Number(range[1])
    const end = range[1] !== '' && range[2] !== '' ? Math.min(Number(range[2]), size - 1) : size - 1
    if (start > end || start >= size) {
      res.statusCode = 416
      res.setHeader('Content-Range', `bytes */${size}`)
      res.end()
      return
    }
    res.statusCode = 206
    res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
    res.setHeader('Content-Length', String(end - start + 1))
    if (req.method === 'HEAD') {
      res.end()
      return
    }
    createReadStream(path, { start, end }).pipe(res)
    return
  }
  res.statusCode = 200
  res.setHeader('Content-Length', String(size))
  if (req.method === 'HEAD') {
    res.end()
    return
  }
  createReadStream(path).pipe(res)
}

function localData(): Plugin {
  return {
    name: 'staf-web-local-data',
    configureServer(server) {
      server.middlewares.use('/data', serveData)
    },
    configurePreviewServer(server) {
      server.middlewares.use('/data', serveData)
    },
  }
}

export default defineConfig(({ command, isPreview }) => ({
  base: command === 'build' || isPreview ? SITE_PATH : '/',
  publicDir: PAGE,
  plugins: [localData(), stafSite()],
  server: { port: 5180, strictPort: true },
  // the PDF and workbook goldens build whole files: room for them when the suite runs in parallel
  test: { testTimeout: 30_000 },
  preview: { https: HTTPS },
  build: {
    outDir: OUT,
    emptyOutDir: true,
    rollupOptions: {
      input: { index: 'index.html', proof: 'proof.html' },
      // plain names only: Jekyll skips files starting with "_" (rollup names helper chunks that way)
      output: {
        entryFileNames: 'assets/[name]-[hash].js',
        chunkFileNames: 'assets/chunk-[hash].js',
        assetFileNames: 'assets/asset-[hash][extname]',
      },
    },
  },
}))

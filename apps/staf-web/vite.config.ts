// The STAF static web app's build and dev server.
//
// The app is a section of the STAF website (GitHub Pages from docs/, Jekyll): `npm run build` writes
// it into docs/app/, served at https://usace-wrises.github.io/staf/app/. That folder is generated and
// self-contained: Jekyll copies it as is, so no built name may start with "_" or "." (Jekyll skips
// those) and no Markdown may land there (GitHub Pages would render it). scripts/after-build.mjs
// checks both. Removing the app from the website is deleting docs/app/ (see README).
//
// In development `/data/...` serves the browser layout that tools/staf-web-data writes (default
// D:\Data\staf-web\site, or STAF_WEB_DATA) with HTTP range support, standing in for the Hugging Face
// mirror the deployed app reads.
import { createReadStream, statSync } from 'node:fs'
import { normalize, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'

/** The website path the built app is served under, and the folder of docs/ it is built into. */
export const SITE_PATH = '/staf/app/'
const OUT = fileURLToPath(new URL('../../docs/app', import.meta.url))

const DATA = resolve(process.env.STAF_WEB_DATA ?? 'D:/Data/staf-web/site')

function localData(): Plugin {
  return {
    name: 'staf-web-local-data',
    configureServer(server) {
      server.middlewares.use('/data', (req, res, next) => {
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
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  base: command === 'build' ? SITE_PATH : '/',
  plugins: [localData()],
  server: { port: 5180, strictPort: true },
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

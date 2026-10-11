// The STAF static web app's build and dev server.
//
// In development `/data/...` serves the browser layout that tools/staf-web-data writes (default
// D:\Data\staf-web\site, or STAF_WEB_DATA) with HTTP range support, standing in for the Hugging Face
// mirror the deployed app reads.
import { createReadStream, statSync } from 'node:fs'
import { normalize, resolve, sep } from 'node:path'
import { defineConfig, type Plugin } from 'vite'

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

export default defineConfig({
  plugins: [localData()],
  server: { port: 5180, strictPort: true },
  build: { rollupOptions: { input: { proof: 'proof.html' } } },
})

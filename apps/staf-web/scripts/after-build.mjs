// After `vite build`: write the offline worker, hold docs/app/ to what the STAF website's Jekyll build
// (GitHub Pages, the github-pages gem) copies as is, and mark the folder as generated.
// - app-sw.js is sw/app-sw.js with this build's id and files written in (a hash of their names and
//   bytes, so the same build gives the same worker), so every pushed build is a new worker.
// - Jekyll skips names starting with "_", ".", "#" or ending with "~".
// - GitHub Pages renders Markdown even without front matter, and a README.md becomes an index.
// - A file that starts with "---" is front matter: Jekyll would run it through Liquid.
import { createHash } from 'node:crypto'
import { openSync, readSync, closeSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const out = fileURLToPath(new URL('../../../docs/app', import.meta.url))
const bad = []

// the worker's own files are not its copy; the proof page is the site's
const NOT_KEPT = new Set(['GENERATED.txt', 'app-sw.js', 'download-sw.js', 'proof.html'])
const files = []
function listFiles(dir) {
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) listFiles(path)
    else {
      const rel = relative(out, path).split(sep).join('/')
      if (!NOT_KEPT.has(rel)) files.push(rel)
    }
  }
}
listFiles(out)
const template = readFileSync(fileURLToPath(new URL('../sw/app-sw.js', import.meta.url)), 'utf8')
// the files and the worker's own code: a changed worker keeps its copy apart too
const hash = createHash('sha256').update(template).update('\0')
for (const f of files) hash.update(f).update('\0').update(readFileSync(join(out, f))).update('\0')
const build = hash.digest('hex').slice(0, 16)
const worker = template.replace("'__STAF_BUILD__'", JSON.stringify(build)).replace('__STAF_FILES__', JSON.stringify(files))
writeFileSync(join(out, 'app-sw.js'), worker)

function startsWithFrontMatter(path) {
  const fd = openSync(path, 'r')
  const head = Buffer.alloc(3)
  readSync(fd, head, 0, 3, 0)
  closeSync(fd)
  return head.toString() === '---'
}

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    const rel = relative(out, path)
    if (/^[_.#]/.test(name) || name.endsWith('~')) bad.push(`${rel}: Jekyll skips it`)
    if (statSync(path).isDirectory()) {
      walk(path)
      continue
    }
    if (/\.(md|markdown)$/i.test(name)) bad.push(`${rel}: GitHub Pages would render it`)
    else if (startsWithFrontMatter(path)) bad.push(`${rel}: starts with front matter`)
  }
}

walk(out)
if (bad.length) {
  console.error(`docs/app holds files the website's Jekyll build would not copy as they are:\n  ${bad.join('\n  ')}`)
  process.exit(1)
}
writeFileSync(join(out, 'GENERATED.txt'),
  'The STAF static web app, built from apps/staf-web by "npm run build". Do not edit.\n' +
  "The site's app links open it (docs/_data/apps.yml); app-sw.js is its offline copy (apps/staf-web/README.md).\n")
console.log(`docs/app checked: every file is one Jekyll copies as is; app-sw.js build ${build}, ${files.length} files`)

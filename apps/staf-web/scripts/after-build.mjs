// After `vite build`: hold docs/app/ to what the STAF website's Jekyll build (GitHub Pages, the
// github-pages gem) copies as is, and mark the folder as generated.
// - Jekyll skips names starting with "_", ".", "#" or ending with "~".
// - GitHub Pages renders Markdown even without front matter, and a README.md becomes an index.
// - A file that starts with "---" is front matter: Jekyll would run it through Liquid.
import { openSync, readSync, closeSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const out = fileURLToPath(new URL('../../../docs/app', import.meta.url))
const bad = []

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
  'Deleting this folder removes the app from the STAF website (see apps/staf-web/README.md).\n')
console.log(`docs/app checked: every file is one Jekyll copies as is`)

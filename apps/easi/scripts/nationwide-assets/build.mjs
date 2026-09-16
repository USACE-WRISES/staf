// Run npm ci, then npm run build here. No network access occurs during build.
import { build } from 'esbuild';
import { readFile, writeFile, mkdir, copyFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const destination = path.resolve(here, '../../www/vendor');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
// Git stores text as LF on every platform; hash the same bytes that it ships.
const text = value => value.replace(/\r\n/g, '\n').replace(/[ \t]+$/gm, '');
await mkdir(path.join(destination, 'leaflet/images'), { recursive: true });
for (const filename of ['leaflet.js']) {
  await writeFile(path.join(destination, 'leaflet', filename),
    text(await readFile(path.join(here, 'node_modules/leaflet/dist', filename), 'utf8')));
}
// Scope Leaflet's stock rules so the single-site ipyleaflet widget is unchanged.
// This pinned stylesheet has ordinary rules and one @media block, no keyframes.
const leafletCss = (await readFile(path.join(here, 'node_modules/leaflet/dist/leaflet.css'), 'utf8'))
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/([^{}]+)\{/g, (match, selector) => {
    if (selector.trim().startsWith('@')) return match;
    return selector.split(',').flatMap(s => {
      s = s.trim();
      return s.startsWith('.') ? ['#easi-viewer-map' + s, '#easi-viewer-map ' + s] : ['#easi-viewer-map ' + s];
    }).join(',\n') + ' {';
  });
await writeFile(path.join(destination, 'leaflet/leaflet.css'), text('/* Leaflet 1.9.4, scoped by the canonical Nationwide asset build. */\n' + leafletCss));
for (const filename of (await readdir(path.join(here, 'node_modules/leaflet/dist/images'))).sort()) {
  await copyFile(path.join(here, 'node_modules/leaflet/dist/images', filename), path.join(destination, 'leaflet/images', filename));
}
await build({ entryPoints: [path.join(here, 'entry.js')], outfile: path.join(destination, 'easi-vector-tile.js'),
  bundle: true, minify: true, format: 'iife', globalName: 'EASIVectorTile', target: ['es2020'],
  legalComments: 'inline', sourcemap: false, charset: 'ascii' });
const packages = ['leaflet', '@mapbox/vector-tile', '@mapbox/point-geometry', 'pbf', 'ieee754', 'resolve-protobuf-schema', 'protocol-buffers-schema'];
const licenses = [];
const dependencies = [];
for (const name of packages) {
  const folder = path.join(here, 'node_modules', name);
  let meta;
  try { meta = JSON.parse(await readFile(path.join(folder, 'package.json'), 'utf8')); }
  catch { continue; } // Transitive packages differ between major releases.
  const licenseName = (await readdir(folder)).find(n => /^license(?:\..*)?$/i.test(n));
  if (!licenseName) throw new Error(`Missing license for ${name}`);
  const license = await readFile(path.join(folder, licenseName), 'utf8');
  licenses.push(`${name} ${meta.version}\n${license}`);
  dependencies.push({ name, version: meta.version, license: meta.license });
}
await writeFile(path.join(destination, 'NATIONWIDE-LICENSES.txt'), text(licenses.join('\n\n--------------------\n\n') + '\n'));
const files = ['easi-vector-tile.js', 'NATIONWIDE-LICENSES.txt', 'leaflet/leaflet.js', 'leaflet/leaflet.css',
  ...(await readdir(path.join(destination, 'leaflet/images'))).sort().map(n => 'leaflet/images/' + n)];
const manifest = { build: 'npm ci && npm run build in apps/easi/scripts/nationwide-assets', dependencies,
  lock_sha256: sha(await readFile(path.join(here, 'package-lock.json'))),
  files: await Promise.all(files.map(async name => ({ path: name, sha256: sha(await readFile(path.join(destination, name))) }))) };
await writeFile(path.join(destination, 'nationwide-assets.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`Built ${files.length} pinned local assets.`);

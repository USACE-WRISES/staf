# Nationwide browser assets

From this directory, run `npm ci --ignore-scripts --no-audit --no-fund` and `npm run build`.
The lockfile pins Leaflet 1.9.4, @mapbox/vector-tile 2.0.5, pbf 4.0.2 and the
build tool. The build uses local packages only, copies Leaflet images and script,
scopes its stylesheet to the Nationwide map, and bundles the flowline decoder.
It writes licenses and a SHA-256 asset manifest under `../../www/vendor/`.
Do not edit the generated assets by hand.

The renderer uses 512-pixel MVT tiles in Leaflet, so Leaflet zoom is one higher
than the shared logical MapLibre zoom. A detached canvas rasterizes each tile
once and is released after a PNG Blob is created. The map displays image tiles,
not a persistent canvas. Hover uses an SVG overlay. Decoded tiles have an LRU
budget of 128 entries and 32 MiB; only loaded viewport tiles retain hit geometry.
Requests use a six-slot queue and are cancelled when their tiles unload or the
dataset changes. Dataset identity and generation are included in tile requests
and report picks. These choices affect display only, never stored scores.

Above the source archive's maximum zoom, each visible 512-pixel display tile
rasterizes clipped geometry from its cached source parent at full stroke width.
Sibling tiles share downloads and decoding; cancellation releases one consumer
at a time. This keeps high-zoom lines sharp without enlarging a parent canvas or
allocating images for offscreen children. Text assets use LF before hashing so
their generated hashes also match Git checkouts on other platforms.

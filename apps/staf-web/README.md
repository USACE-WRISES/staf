# STAF static web app (in development)

EASI, SFARI and DEEP as a static TypeScript app, a section of the STAF website: a clone of the Shiny
STAF app (`apps/staf`) that reads STAF's published data by HTTP range and computes in the browser. The
Shiny apps are untouched. Plan and hand-off: `notes/2026-10-06_STAF_Static_Web_App/` (local only).

Phase 1 is built: the data layer and the cross-section pulls, with a proof page.

## On the STAF website, kept apart

The owner's choice (2026-10-11): the app lives in this repo and is served by the existing STAF website
(GitHub Pages, Jekyll from `docs/`) as a separate part, so it can be removed cleanly.

- `npm run build` writes the app into **`docs/app/`**, served at https://usace-wrises.github.io/staf/app/.
  - The folder is generated: never edit it. Rebuild and commit it with its source.
  - Pushing it to `main` publishes it with the rest of the website.
- **Jekyll copies it as is:** a full site build on 2026-10-11 gave all 13 files byte for byte.
  - `scripts/after-build.mjs` fails the build on anything Jekyll would skip or render: names starting
    with `_` or `.`, Markdown, or a file starting with front matter.
  - It also writes `GENERATED.txt`.
- **The app reads its data from the Hugging Face mirror,** so the website stays small (about 0.6 MB
  for the app).
- **No other file of the website refers to it.** When the app is ready, the Apply STAF page gets one
  link, added with the owner's go.

## Removing the app

Nothing else in the repo depends on it. Delete:
1. `docs/app/`: the website section, gone after the next Pages build;
2. `apps/staf-web/` (the source) and `tools/staf-web-data/` (the data tools);
3. the link from the website, once there is one;
4. if wanted, the Hugging Face dataset `gtmenichino/staf-data` and the `staf-web` entry of
   `.claude/launch.json`.

The cross-section release (`staf-xs-current`, `tools/xs-grid`) is not part of the app and stays.

## The data

The app reads the browser layout of STAF's releases (`tools/staf-web-data`): `staf-data-current` (the
bundle), `staf-xs-current` (the cross-section grid) and `library`. They are mirrored to the Hugging Face
dataset `huggingface.co/datasets/gtmenichino/staf-data`, because a browser can't read GitHub release
downloads (no CORS header). `web-manifest.json` maps each logical file to a content-hashed one.

- `src/data/range.ts` holds every range read to the bytes it asked for. A short body arrived with no
  error in testing (3 of 213 reads in the Claude app's browser pane), so a failed read is read again,
  bypassing the HTTP cache. A dropped connection is read again too. A host that refuses for now (429,
  503) is asked again after growing waits, for up to 6 minutes.
- `src/data/source.ts` reuses the address Hugging Face redirects a file to. Big files go to its CDN,
  which isn't rate-limited, so a file costs one rate-limited request an hour.
- `src/data/parquet.ts` and `src/data/lines.ts` read parquet footers, row groups and flowlines with
  hyparquet.

## Cross-sections

- `src/xs/grid.ts` reads a flowline's section numbers, details, stored median profile, and (release
  format 3) each section's exact position and profile checksum.
- `src/xs/sample.ts` pulls a section from USGS's 3DEP tiles on the grids the build read it on.
  - Tiles are read with geotiff.js through the checked range reads. `src/xs/lzw.ts` replaces
    geotiff.js 3.0.5's LZW decoder, which drops the 4,096th table entry, and throws on a block cut short.
  - Samples are projected with `src/xs/projExact.ts`, PROJ 9.5.1's own arithmetic. A browser's math
    functions still round differently from the Windows C runtime the build ran on, up to 1.1e-8 m. So
    `pullExact` tries the other rounding of any sample that close to a float32 rounding boundary until
    the profile's CRC-32 is the release's `z_crc`. A verified pull is the archive's profile bit for bit.
- `src/xs/codec.ts` decodes the archive codes, `src/xs/place.ts` places sections from the bundle's
  lines (for releases without stored positions), and `src/xs/crc32.ts` is zlib's CRC-32.

## Working

```bash
npm install
npm run dev        # http://localhost:5180/proof.html?vpu=0710 ; serves /data from STAF_WEB_DATA
npm test           # Vitest; fixtures from parity/make_fixtures.py
npm run typecheck
npm run build      # into docs/app/ (the website section), then the Jekyll check
```

- `proof.html` measures what a click costs and checks pulls against the stored medians:
  `?vpu=0108&pulls=20`, and `&data=https://huggingface.co/datasets/gtmenichino/staf-data/resolve/main/`
  to read from Hugging Face instead of the local layout.
- `parity/make_fixtures.py` writes the Python references the tests compare with.
- `parity/proj_fixtures.py` writes the large projection set for `tests/projExact.test.ts`
  (`PROJ_FIXTURES=<dir>`).

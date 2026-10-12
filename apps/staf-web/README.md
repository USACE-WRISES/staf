# STAF static web app (in development)

EASI, SFARI and DEEP as a static TypeScript app, a section of the STAF website: a clone of the Shiny
STAF app (`apps/staf`) that reads STAF's published data by HTTP range and computes in the browser. The
Shiny apps are untouched. Plan and hand-off: `notes/2026-10-06_STAF_Static_Web_App/` (local only).

Built so far:
- Phase 1: the data layer and the cross-section pulls, with a proof page (`proof.html`).
- Phase 3, under way:
  - the page itself (`index.html`): the Shiny STAF app's header and EASI | SFARI | DEEP switch, each
    tool's Identify card, About and Help;
  - the map: USGS basemaps, NHD overlay, Layers, legend, scale, zoom cue, readout, and DEEP's
    assessment regions;
  - the stream lines from zoom 14, split by the click rule, with StreamCat coverage;
  - the pick: a click, typed coordinates or a place search snaps to a stream, marks it and finds the
    StreamCat reach, as each tool does;
  - Delineate and the Basin step: the watershed, the assessment reach and the Basin card in each
    tool, computed in the browser by a port of the STAF site engine.
  - Next: the Assessment step (the metric families, then each tool's worksheet).

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
- **The app reads its data from the Hugging Face mirror,** so the website stays small (about 2 MB
  for the app, most of it the Shiny page's stylesheets and fonts).
- **No other file of the website refers to it.** When the app is ready, the Apply STAF page gets one
  link, added with the owner's go.

## Removing the app

Nothing else in the repo depends on it. Delete:
1. `docs/app/`: the website section, gone after the next Pages build;
2. `apps/staf-web/` (the source) and `tools/staf-web-data/` (the data tools);
3. the link from the website, once there is one;
4. if wanted, the Hugging Face dataset `gtmenichino/staf-data` and the `staf-web` and
   `staf-web-built` entries of `.claude/launch.json`.

The cross-section release (`staf-xs-current`, `tools/xs-grid`) is not part of the app and stays.

## The page: the Shiny app's look, in the app's own copies

The static page is the Shiny STAF app's page drawn without Shiny, so it looks the same pixel for pixel
(the parity captures below). **The app keeps its own copy of everything it takes from the Shiny apps**
(the owner, 2026-10-11: the Posit Connect app will be retired and removed). Nothing in it reads
`apps/easi`, `apps/sfari`, `apps/deep`, `apps/staf` or `libs/staf_workbook`, at build time or in the
browser.

- **`page/`** is Vite's public folder: the files the page serves as is, copied into `docs/app/`
  unchanged. It holds:
  - each tool's stylesheet (on only while that tool is shown) and DEEP's `deep.css`;
  - the shared `staf.css` and `metric-rows.css`, and the shell's `staf-shell.css` and favicon;
  - the shared page scripts (`js/`);
  - py-shiny's Bootstrap build with its fonts (latin), `fill.css` and `shinywidgets.css`;
  - Leaflet's stylesheet;
  - two short files of the Jupyter widget rules the Shiny maps take from ipyleaflet
    (`css/shiny/jupyter.css`, `jupyter-leaflet.css`, written here).
- **`parity/copy_page.py`** made those copies. `page-sources.json` records each one's source and its
  sha256.
  - While the Shiny apps exist, `copy_page.py --check` reports any source that changed since the copy
    (copy again to follow it).
  - It never overwrites a copy edited here (`--force` does).
  - Once the Shiny apps are gone, `page/` is the source: edit it directly.
- **Markup** (`src/app/markup.ts`): the header and each tool's section with the Shiny page's elements,
  classes and ids (`easi-lat`), wrappers included, since the stylesheets select on them. It is written
  into `index.html` when the page is built or served (`site.ts`, which also writes the links in the
  Shiny page's order), so it paints before any script runs.
- **Page scripts:** the plain JavaScript the Shiny apps share runs unchanged: `staf-ns.js`, the legend
  dock, the place search, coordinate entry, tooltips and DEEP's `coverage.js`. They call
  `Shiny.setInputValue` and `Shiny.addCustomMessageHandler`, which `src/app/shim.ts` answers inside the
  page. No Shiny, no server, no Python.
- **The map** (`src/app/map.ts`): Leaflet 1.9.4 (ipyleaflet's version), built as ipyleaflet builds the
  Shiny map. The attribution names Leaflet where the Shiny map names ipyleaflet; that corner is the one
  difference in the captures.
- **About and Help** (`src/app/content/dialogs.json`): each tool writes them in its `app.py`, so they
  were captured from the running Shiny app (`parity/capture.py --app shiny` writes them again).
- **DEEP's regions** are `library/deep-regions.json` in the data layout.
  - `tools/staf-web-data/webdata/deep_regions.py` writes it from the library release with DEEP's rules,
    carried there: eligibility, hidden assessments, the default version.
  - Its test holds it equal to DEEP's own list while `apps/deep` exists.

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

## Stream lines

From zoom 14 each tool draws the streams of the view as the Shiny tools do (`src/app/streams.ts`):

- **When to fetch:** once the view settles, a padded box around its centre (`src/streams/viewport.ts`)
  is fetched if the view has left the last one. The Shiny app waits 0.5 s for the view to settle; the
  static app waits 0.25 s.
- **Reading** (`src/streams/network.ts`): the box's NHDPlus HR and NHDPlus V2 lines are read from the
  bundle by range, as `site_engine/_hrslim/reader2.py` does. The Shiny SFARI and DEEP ask the USGS
  fabric service for the V2 lines; the static app reads the bundle's, as EASI does.
- **Splitting** (`src/streams/display.ts`, `geom.ts`): the HR lines are split by the click rule against
  the V2 lines (150 ft in EPSG:5070). This is `network_display.py` step for step, with GEOS's
  arithmetic (`geom.ts`) and PROJ's Conus Albers both ways (`src/xs/projExact.ts`).
- **Checked against the Python:**
  - `parity/make_stream_fixtures.py` writes three views' inputs and displays from EASI's own code
    (`tests/fixtures/streams_ref.json`).
  - `tests/streams.test.ts` holds the split to them feature for feature and coordinate for
    coordinate.
  - `tests/network.test.ts` holds the reader to them when the local layout is there.
  - `parity/capture.py --view 43.6858,-72.2367,16 [--coverage]` with `compare.py`: pixel-identical to
    the Shiny tools, coverage on and off (2026-10-11).
- **Speed:** a new region at zoom 14 drew in 0.8 to 1.0 s from the local layout, settle included. The
  split itself takes about 26 ms for 857 HR lines.
- **A failed read** is asked again after 15, 30 and 60 s, then the legend offers Try again, as the
  Shiny tools do when the USGS service doesn't answer.

## The pick (Identify)

A click at the stream zoom, typed coordinates or a found place work as in each Shiny tool
(`src/app/identify.ts`):

- **EASI** (`app.py _handle_click`): a click within 150 ft of a V2 line takes that reach, pinned on the
  HR line the map draws. A click on an HR line alone is routed to the nearest covered reach downstream
  (`routing.route_from_hr`), and the pane shows EASI's card ("Downstream reach: ...").
- **SFARI and DEEP:** every click pins the HR line, and the reach is found in the background by the
  engine's rule (`anchor.classify_click`).
- **Engine pieces** (`src/engine/`):
  - `snap.ts`: the nearest point on lines, GEOS's DistanceOp step for step.
  - `bundle.ts`: the bundle's catchments, decoded from their shared borders (arcs, zstd step runs);
    a flowline by id or row; the raindrop (the V2 catchment that holds a point names the reach).
  - `anchor.ts`: the anchor payload, key for key.
- **Checked against the Python:** `parity/make_snap_fixtures.py` writes 42 clicks around the three
  stream views (`tests/fixtures/snap_ref.json`).
  - The snaps match within 1e-12 degree.
  - The catchments, raindrops and reach attributes match exactly.
  - EASI's routed anchors match key for key (`tests/snap.test.ts`, `raindrop.test.ts`,
    `anchor.test.ts`).
- **On screen:** `parity/capture.py --click LAT,LON` picks in both apps.
  - Picks on a covered stream and on a routed one (with coverage) showed the same status lines in all
    three tools.
  - EASI and DEEP matched pixel for pixel (one-level colour rounding aside).
  - In SFARI, Shiny drew the glow over the point (an ipyleaflet timing artefact); the static app keeps
    the point on top, as SFARI's code intends.
- **Kept from Shiny's map:** the glow and route land in Leaflet's overlay pane (ipyleaflet applies a
  pane only after a layer is made), and the point is listed in Layers as "Selected point".
- **No live services:** "Find on map" asks Photon only. Shiny then tries Nominatim; the static app
  doesn't.

## Delineate (the Basin step)

Delineate computes the watershed and the assessment reach in the browser, as each Shiny tool does:

- **SFARI and DEEP** run the STAF site engine at the pick (`src/engine/site.ts`, `compute_site` up to
  the metrics):
  - the site: the nearest HR line within 150 ft and its attributes;
  - the HR reach watershed: the breadth-first upstream walk with the interactive budget (3,000
    reaches, 190 hops), the outline of the tree's catchments from their shared borders and the area
    from their cells (`src/engine/dataset.ts`, `outline.ts`; reader2's `watershed`, arcs' `outline`);
  - the reach: the HR mainstem trimmed to the reach length upstream of the snap (`reach.ts`: GEOS's
    line merger, its projection, shapely's substring).
  - Past the budget the engine refuses, and the "Watershed not available" dialog offers the StreamCat
    values, as in Shiny.
- **EASI** (`src/engine/pipeline.ts`, `pipeline.delineate_only`):
  - a covered reach: its NHDPlus V2 basin (the V2 walk, minor divergences included) and its V2 reach;
  - a routed site: the clicked HR stream's reach and its HR reach watershed from the engine.
- **The map** (`src/app/basin.ts`), as `_draw_delineation` draws it:
  - a watershed of more than 1,500 coordinates is simplified for display as GEOS's topology-preserving
    simplifier does (`simplify.ts`, a port of GEOS 3.13.1);
  - the map is framed as ipyleaflet's `fit_bounds` frames it (the center in degrees, then whole zoom
    steps), not as Leaflet's `fitBounds`.
- **DEEP's assessment block** (`src/app/deepAssess.ts`): the assessments whose region holds the point,
  the first one's default version read from the library mirror, and the facts DEEP states.
- **Checked against the Python:** `parity/make_delineate_fixtures.py` delineates five sites from the
  local bundle (`tests/fixtures/delineate_ref.json`, `tests/delineate.test.ts`):
  - the walk's ids, the catchment count, the area, and the outline vertex for vertex;
  - the display simplification, exactly;
  - EASI's result and SFARI's, key for key; the reach within 1e-11 degree (the inverse projection's
    last bits differ between the browser's and Windows' math libraries).
- **On screen:** `parity/capture.py --click LAT,LON --delineate` at Mink Brook (covered) and at a routed
  tributary: all three tools matched the Shiny captures pixel for pixel, the attribution corner aside.
- **Kept from Shiny's map:** the tools put the point back "on top", but ipyleaflet keeps a layer's
  place when it is removed and added in one update, so the Shiny map draws the basin and the reach over
  the point; Layers lists the watershed after the point and not the reach. Both are reproduced.
- **Speed:** a new region's network and catchments (8 MB for 0108) start loading as soon as the pick is
  ready; Delineate then took 0.2 to 0.3 s at Mink Brook (276 reaches). The bundle's zstd is read with
  zstddec (WebAssembly), 10 to 20 times faster than the JavaScript decoder.
- **Not yet:** the metric families (step 5 of the engine; they come with the Assessment step), the
  site's EPA Level III ecoregion (its polygons are not in the published data yet), DEEP's Change
  dialog. A watershed across two regions has the same outline and area as shapely's union, its
  vertices possibly in another order.

## The Assessment step

- **SFARI** (`src/tools/sfari/`): the 22 evidence adapters from the bundle, the worksheet, scoring and
  suggestions. `parity/make_sfari_fixtures.py` + `tests/sfari.test.ts`.
- **DEEP** (`src/tools/deep/`): the measured values, curves, coverage interval, the prefill and the
  method texts. `parity/make_deep_fixtures.py` + `tests/deep.test.ts`.
- **EASI** (`src/tools/easi/`): the adopted method package read from the library mirror (digests
  checked), the 20 adapters, the evaluator and the rollup, the worksheet and the report.
  `parity/make_easi_fixtures.py` + `tests/easi.test.ts` (random evaluations, criteria, cross-section math,
  plot figures, five whole screenings and the prefetch).
- **Scenarios** (`src/shared/scenarios.ts`) for all three. `parity/make_scenario_fixtures.py`.
- **EASI's cross-section card** (`xscard.ts`): the reach's nine grid sections with their numbers. A
  section's profile is drawn (`xsplot.ts`, plotly.js 3.6.0, plotly.py's figure) when the release stores
  it, or after the assessor presses **Pull from 3DEP**: pulls are never automatic. A pulled profile gives
  the grid's own numbers.
- **EASI's report image** (`xsimage.ts`): matplotlib's figure (`easi/xsplot.py`) drawn on a canvas with
  DejaVu Sans at matplotlib's hinted text metrics (`content/easi/xsplot-text.json` and
  `page/fonts/dejavu-sans.woff2`, from `copy_page.py`). `parity/make_xsimage_fixtures.py` +
  `tests/xsimage.test.ts` hold its layout to matplotlib's; `parity/xsimage_compare.py` compares the pixels.
- **Not yet:** Get Forms, the report downloads, Save and Open (Phase 6). They say so when clicked.

## Speed

- **Reads:** the data's index is `web-index.json.gz` (the manifest's paths and sizes, the bundle's regions,
  each parquet file's footer size; about 130 KB where the manifests are 1.7 MB), so a file's footer is
  read in one exact request. Files the app reads a few rows of carry a page index in
  the browser layout (`tools/staf-web-data`): `readWhere` and `readObjectsWhere` read only the pages a
  filter can match (a map view's lines, the points near a site, a flowline's sections), `readColumns(...,
  pages)` and `readRows(..., pages)` the pages holding a few rows. `DataSource` reads the slices asked
  for in one turn as one request where they sit within 32 KB. Every reader works with files that have
  no page index too (it then reads whole row groups). The JSON and GeoJSON files the app reads whole
  (DEEP's bundles and region list, the catalogs, the ecoregion and division polygons) are stored
  gzipped, and `bytesOf` unzips them (a tenth of the bytes for a DEEP bundle, a third for polygons).
  The base-flow grid (1.27 MB) is read by tile: its first 16 KB (directory, tile index, geokeys), then
  the 256-cell tiles a watershed covers. The catchments' value tables (land cover, riparian strips,
  roads, soils, crossings, NWI strips) are read by blocks of 256 rows (`src/engine/blocks.ts`; the
  layout's pages hold 1,024): a watershed's catchments sit close together in a region's rows (Utoy
  Creek's 89 in 3 blocks of 105,471), so a headwater's values cost 0.27 MB, not 4.2 MB. Files under
  64 KB are read whole at once (hyparquet's default, 512 KB, read the small paged tables whole).
- **Kept between visits:** Hugging Face answers a large file with a `no-store` redirect to a signed
  address that changes every time, so the browser's HTTP cache can't serve a second visit.
  `src/data/cache.ts` keeps what the app read in Cache Storage under each file's content-hashed path
  (whole files, and ranges by their bytes), so a visit reads from the host only what no earlier visit
  read. A session's first idle moment drops what the index no longer names, then the oldest past 1 GB.
  `?cache=0` turns it off; the proof page never uses it.
- **Together:** reads that don't depend on each other start together: the metric families
  (`computeAll`: their entries still go in the families' order) and the value tables a watershed's sums
  take (`watershedValues`). Each read is a few requests in a row (footer, page index, pages), so one
  after another they added up.
- **Ahead of time:** when the Basin step shows a delineation, EASI starts its screening's reads, SFARI its
  desktop evidence pull and DEEP (once its assessment is loaded) its desktop values, so the next step
  finds them done; plotly.js loads when EASI's screening ends. A watershed walk reads the region's
  topology file (`topo/`, one request).
- **Measuring:** the steps record `staf:` performance measures (`src/app/perf.ts`). `parity/capture.py
  --perf` saves them with every request (`[--throttle MS,MBPS]` slows the network, `--visits 2` adds a
  second visit in the same browser profile), and `parity/perf.py
  <stem>` sums them by step and file. To time the build rather than the dev server: `npm run build`,
  `npx vite preview --port 5181`, then `--url http://localhost:5181/staf/app --query
  data=http://localhost:5181/data/`. Over HTTP/2, as the data host serves: set `STAF_WEB_CERT` and
  `STAF_WEB_KEY` to a localhost certificate and key (the preview then serves HTTPS; the launch entry
  `staf-web-built-h2` does, on port 5182) and use `https://localhost:5182`.

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
- `index.html` is the app: `http://localhost:5180/?tool=easi|sfari|deep`.
- **Parity with the Shiny app** (until it is retired): with the Shiny STAF app running (port 8040) and `npm run dev`,
  `parity/capture.py --app shiny` and `--app static` screenshot each tool and its About and Help at
  1440x900 and save the markup; `parity/compare.py` reports the pixels and lines that differ and
  draws them in `parity/diff/` (all gitignored). On 2026-10-11 the three tools and their dialogs
  differed only in the attribution corner. `--view LAT,LON,ZOOM`, `--click LAT,LON` and `--delineate`
  capture a view, a pick and the Basin step; `--assess` the Assessment step, and `--actions
  parity/actions/<script>.js` then scripts a user's clicks and typing (its answer is saved beside the
  capture).
- `parity/make_delineate_fixtures.py` writes the Delineate reference (above).
- `parity/make_fixtures.py` writes the Python references the tests compare with.
- `parity/proj_fixtures.py` writes the large projection set for `tests/projExact.test.ts`
  (`PROJ_FIXTURES=<dir>`).

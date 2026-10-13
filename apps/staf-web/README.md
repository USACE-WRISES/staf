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

Browsers that used the app keep its download worker registered for `/staf/app/download/`. It answers
nothing else, so it does no harm once the app is gone.

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
  - Leaflet's stylesheet, without its rules for Internet Explorer 8 and older (they carry IE's script
    hooks, which network filters treat as attacks);
  - two short files of the Jupyter widget rules the Shiny maps take from ipyleaflet
    (`css/shiny/jupyter.css`, `jupyter-leaflet.css`, written here).
  - The map's three stylesheets (those two and Leaflet's) are written into the page as style
    elements, where the Shiny page has them. As a separate file, Leaflet's stylesheet did not reach
    the page on the USACE network's isolated browsing (CBII, 2026-10-12): the tiles sat out of place
    and the map's controls were gone.
- **`parity/copy_page.py`** made those copies. `page-sources.json` records each one's source and its
  sha256.
  - While the Shiny apps exist, `copy_page.py --check` reports any source that changed since the copy
    (copy again to follow it).
  - It never overwrites a copy edited here (`--force` does).
  - Once the Shiny apps are gone, `page/` is the source: edit it directly.
- **Markup** (`src/app/markup.ts`): the header and each tool's section with the Shiny page's elements,
  classes and ids (`easi-lat`), wrappers included, since the stylesheets select on them. It is written
  into `index.html` when the page is built or served (`site.ts`, which also writes the stylesheets in
  the Shiny page's order), so it paints before any script runs.
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
- **Files** (`src/files/`, `src/session/`): Save and Open, Get Forms and the report downloads, made in
  the page (the assessment file, CSV, GeoJSON, workbooks, the reportlab PDFs).
- **Site photos in the PDFs** (`src/shared/photos.ts`): a JPEG photo is drawn as its own bytes, and
  reportlab names it after the samples Pillow decodes. `src/files/pdf/jpeg.ts` decodes as Pillow's
  libjpeg-turbo does (its SIMD integer IDCT included), so the names are the Shiny PDF's.
  `parity/make_jpeg_fixtures.py` + `tests/jpeg.test.ts` hold it to Pillow on about 200 files: Pillow's and
  Edge's encoders, unusual sampling, damaged data. The SFARI and DEEP report goldens include JPEG photos.
- **Downloads in their own tab** (`src/files/download.ts`, `page/download-sw.js`): a click on a
  download link opens its tab, as the Shiny links do (`target="_blank"`), at `download/<token>/<name>`.
  A service worker scoped to `download/` answers that tab with the file the page made, sent as an
  attachment, so the browser saves it and closes the tab. On the USACE network, Menlo Security's
  isolated browsing (CBII) shows a workbook or PDF in place of the tab it came to. Saved from the app's
  own tab, that preview replaced the page and its work (2026-10-12). Without the worker (not yet
  registered, or not allowed), the click opens a blank tab and the file is saved from it.

## Speed

Tuned against the Hugging Face mirror itself (2026-10-12, `?trace=1` captures; the numbers are in the
notes): it answers a request in 0.15 to 0.2 s, a file's first request in 0.5 to 0.7 s (the look-up,
then the CDN's first read of the file), takes the requests a page asks for together one after another
(about 30 ms apart), and moves 17 MB/s from here. So the app makes few requests, and early.

- **Reads:** the data's index is `web-index.json.gz` (the manifest's paths and sizes, the bundle's
  regions, each parquet file's footer size; about 150 KB where the manifests are 1.7 MB), so a file's
  footer is read in one exact request. A file under 3 MB is read whole, in one request (`WHOLE` in
  `src/data/source.ts`; 256 KB where the browser rates the connection 3G or slower): a paged read of a
  small table took a footer, a page index and a request per column's page, about a second on Hugging
  Face, where the whole file takes a quarter of one. The stream lines are always read by page (a map
  view needs a few). Larger files carry a page index in the browser layout (`tools/staf-web-data`):
  `readWhere` and `readObjectsWhere` read only the pages a filter can match, `readColumns(..., pages)`
  and `readRows(..., pages)` the pages holding a few rows; the value tables of large regions are read by
  blocks of 256 rows (`src/engine/blocks.ts`). Slices asked for in one turn are read as one request where
  they sit within 32 KB. Every reader also works on files without a page index. The JSON and GeoJSON
  files are stored gzipped, and `bytesOf` unzips them; a visit reads a whole file once (`bytesOf` shares
  the read).
- **Kept between visits:** Hugging Face answers a large file with a `no-store` redirect to a signed
  address that changes every time, so the browser's HTTP cache can't serve a second visit.
  `src/data/cache.ts` keeps what the app read in Cache Storage under each file's content-hashed path
  (whole files, and ranges by their bytes), so a visit reads from the host only what no earlier visit
  read. A session's first idle moment drops what the index no longer names, then the oldest past 1 GB.
  `?cache=0` turns it off; the proof page never uses it.
- **Fewer look-ups:** Hugging Face counts each file's `/resolve/` request (3,000 a 5-minute window per IP
  address; reads at the signed CDN address it redirects to don't count). A first visit opens 26 to 29
  files. `src/data/signed.ts` keeps each redirect's address for its hour (a CloudFront policy with only
  an expiry, so it works from any network) in localStorage: a reload or a second tab within the hour
  reads those files without looking them up, and an address the CDN refuses is looked up again.
  `?cache=0` turns this off too.
- **Early:** when the pick is ready, the tool opens the files its Delineate and Assessment step will read
  (`src/app/warm.ts`: each tool's list, from its trace), so each file's slow first request happens
  while the user decides to delineate: a small file is read whole, a large one's footer. When the Basin
  step shows a delineation, EASI starts its screening's reads, SFARI its desktop evidence pull and DEEP
  (once its assessment is loaded) its desktop values; plotly.js loads when EASI's screening ends.
- **Together:** reads that don't depend on each other start together, and their results are still taken
  in order: the metric families (`computeAll`), a watershed's value tables (`watershedValues`), EASI's
  screening lookups (`prefetch`: only the cross-sections wait for the bankfull curve, NAS for the point's
  HUC12), each reach line's grid sections (`gridReachGeometry`), and the cross-section card's section
  details and stored profiles.
- **Measuring:** the steps record `staf:` performance measures (`src/app/perf.ts`). `parity/capture.py
  --perf` saves them with every request (`[--throttle MS,MBPS]` slows the network, but Chrome's throttle
  lets one request through at a time; `--visits 2` adds a second visit in the same browser profile), and
  `parity/perf.py <stem>` sums them by step and file. With `--query trace=1` the app logs each data
  request (file, bytes, when; `window.stafTrace()`), and `perf.py <stem> --step <step> --trace` lists
  a step's requests in order: the ones a step waits on in a row show. To time the build against the
  live mirror: `npm run build`, `npx vite preview --port 5181`, then `--url http://localhost:5181/staf/app`
  (a built page reads the mirror); against the local layout, add `--query data=http://localhost:5181/data/`.
  Over HTTP/2 locally: set `STAF_WEB_CERT` and `STAF_WEB_KEY` to a localhost certificate and key (the
  launch entry `staf-web-built-h2`, port 5182), and `STAF_WEB_DELAY_MS` to hold each `/data` answer back.

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

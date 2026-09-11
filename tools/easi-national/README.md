# EASI National Builder

Local operator tool that precomputes the EASI screening for every NHDPlus V2
reach, chunk by chunk, and publishes the result to the rolling GitHub
prerelease `easi-national-current` that the EASI app's Nationwide screening map reads.
Never deployed; lives outside `apps/` so the desktop payload and CI's lock
gate ignore it. It imports the EASI package straight from `apps/easi`.

## Run

```
cd tools/easi-national
..\..\.venv\Scripts\shiny run app.py --port 8020
```

Headless (same worker the panel drives):

```
python -m builder.worker national                       # one-time pulls (~1.5 GB, incl. the StreamCat cache)
python -m builder.worker chunk --kind huc8 --value 02080204
python -m builder.worker chunk --kind state --value VA
python -m builder.worker chunk --kind state --value VA --stages xs_sample xs_derive joins score   # Tier 2 for a state
python -m builder.worker chunk --kind huc8 --value 02080204 --stages xs_sample --keep-dem-windows  # also keep the DEM windows
python -m builder.worker tiles --vpu 02                 # needs Docker running
python -m builder.worker stage                          # rebuild staging/
python -m builder.worker publish --dry-run
python -m builder.worker queue                          # drain state/queue.json
```

Data root: `EASI_NATIONAL_ROOT` (default `D:\Data\easi-national`). Layout in
`builder/paths.py`.

Concurrency knobs (environment variables, read at worker start): the pipeline
is network-bound, so most stages run one polite request at a time; these are
the places where more helps.

| Variable | Default | What it controls |
|---|---|---|
| `EASI_NATIONAL_WQP_CONCURRENCY` | 8 | Water Quality Portal requests in flight (station lists per cell, then results by station-id batches sized to the portal's answer time) |
| `EASI_NATIONAL_WQP_METHOD` | stations | `cells` restores the older bounding-box pull (slow: the portal trickles rows for a dense cell for an hour) |
| `EASI_NATIONAL_FABRIC_CONCURRENCY` | 3 | flowline geometry batches in flight (only without the seamless geodatabase) |
| `EASI_NATIONAL_STREAMCAT_CONCURRENCY` | 3 | StreamCat region pulls in flight while the national cache builds |
| `EASI_NATIONAL_HUC8_WORKERS` | cores minus one, at most 6 | processes for the per-HUC8 derive, joins and score steps |
| `EASI_NATIONAL_XS_WORKERS` | one per CPU, 8 to 16 | processes for the cross-section sampling (about 0.5 s and 3 MB of 3DEP range reads per reach in one process) and derivation; a HUC8 runs serially inside one process, so the pool starts the largest HUC8s first |
| `EASI_NATIONAL_XS_BYTE_BUDGET_GB` | 800 | elevation bytes the sampling may read per calendar month (estimated from the tile blocks each window touches); the stage pauses at the budget and resumes next month, or sooner when the budget is raised |
| `EASI_NATIONAL_DEM_CATALOG_WORKERS` | 4 | S3 listings and tile headers in flight while the 3DEP catalogs build |
| `EASI_NATIONAL_WQP_MONTHLY_WORKERS` | 3 | months downloaded at once in the national monthly WQP pull |
| `EASI_NATIONAL_WQP_MONTHLY_START` | 2016-09 | first calendar month of that pull (through the current month) |

## How it works

- A **chunk** is a set of HUC8s (one HUC8, a HUC4, a state, a region). Every
  source is fetched only for the chunk: StreamCat (by state or COMID list),
  flowline geometry and HUC12 polygons (USGS fabric API), ATTAINS units (by
  bounding box with a 10-mile buffer), NAS taxa (per HUC12), WQP nutrients
  (station lists per grid cell over the buffered bbox, then results by
  station-id batches: the portal materializes a whole answer before its first
  byte and drops anything silent for three minutes, so batches adapt to the
  answer time and a cut batch is halved).
  StreamCat comes from the national cache (all regions, pulled once by the
  national job through the API's region selector) when it exists, and NAS
  taxa from the national cache of established records (one paged pull).
  With the NHDPlus V2 seamless geodatabase extracted under `national/nhdplus/`
  and the ATTAINS national geodatabase under `national/attains/`, the national
  job converts them once (`flowlines.parquet`, `huc12.parquet`,
  `attains.parquet`) and the geometry, HUC12 and ATTAINS stages filter those
  files instead of paging the services. The other one-time pulls are the
  NHDPlus attribute tables, the dam inventory, the HUC4 polygons and the two
  3DEP catalogs (below).
  The national job's `wqp_monthly` step pulls every calendar month of
  national TN and TP results (stream sites, the five characteristic names
  the app queries, the `fullPhysChem` profile) through the portal's WQX3
  service into `national/wqp/monthly/`, a few months at a time, with a
  ledger of status, rows, bytes and attempts per month. A month counts only
  when the transfer ended normally and the file does not end with the
  portal's "ERROR: INCOMPLETE DATA ... PLEASE RETRY THE REQUEST." trailer;
  cut connections and overloaded answers go back in the queue with a growing
  wait (a cut month is halved down to single days, finished pieces kept; a
  stream still running at the one-hour cap is retried whole in a later pass,
  never split). When every month is in, the CSVs are combined into
  `national/wqp/wqp_results.parquet` (every column as text, duplicates on the
  result identifier dropped) and removed.
- Per HUC8: derive (anchor point, sinuosity, HUC12, regions, bankfull, NRSA),
  the two cross-section stages (below), joins (the four point services
  reproduced from the bulk pulls), score (`easi.assessment.assess_preloaded`,
  the app's own adapters).
- Every stage is idempotent with a done marker keyed by its inputs; long loops
  keep ledgers so **Pause** (finish the current request, exit) and **Resume**
  never repeat work. **Cancel** stops between requests.
- Tiles per region: FlatGeobuf + tippecanoe in Docker (`docker/tippecanoe`)
  -> PMTiles. Staging assembles the publish set (per-HUC4 evidence, per-region
  scores and tiles, the COMID index, HUC4 coverage, `manifest.json`).
  Publish uploads changed assets with `gh release upload --clobber`, manifest
  last, then verifies the manifest download.

## Tier 2: cross-sections from the best available 3DEP elevation

Tier 1 records carry no cross-sections, so four functions (floodplain access,
floodplain engagement frequency, bank erosion and armoring, channel evolution
stage) read "not available" and the physical sub-index is provisional. Tier 2
fills them with the live app's own arithmetic from the highest resolution
USGS 3DEP has for each reach, in two per-HUC8 stages:

- **`xs_sample`** (network) builds the archive. Per reach: the reach line is
  rebuilt offline exactly as the live app builds it from NLDI (the same
  upstream and downstream rule, `delineation._reach_from_lines`), but from the
  national VAA table and the national flowline file, so a reach is **never
  clipped at a HUC8, state or region border**: the chain simply continues to
  completion. The buffer rule (8 times the bankfull width, 250 to 800 m), the
  nine transect positions, the chord, the spacing (`min(10, res)`) and the
  bilinear sampling are the live app's lines, copied. Elevation comes from,
  in order, the 1 m lidar project tiles, the 1/9 arc-second (3 m) quads, then
  the 10 m seamless, each taken when at least half the buffer is finite
  (the live app's own acceptance rule). Output per HUC8:
  `xs_profiles.parquet` (one row per transect: placement, the raw elevations
  as `list<float64>` under BYTE_STREAM_SPLIT + zstd (float32 storage flipped a
  bank threshold on one real reach in ten: the derivation is that sensitive),
  the DEM's resolution, source, tiles and Last-Modified), `xs_sample.parquet`
  (one row per reach:
  status, resolution, bytes read, timing) and `reaches.parquet` (the reach
  lines, GeoParquet). Batches of 100 with a ledger, so Pause and Resume never
  repeat a reach. `--keep-dem-windows` also writes each reach's DEM window
  under `dem_windows/` (1 to 3 MB per reach at 1 m; off by default).
- **`xs_derive`** (pure, seconds per HUC8) turns the archive into the metric
  inputs with the live app's `geomorph` functions (bank detection, the nine
  candidates, reach medians) and writes `xsections.parquet`: the scalars, the
  drawn section's profile, `dem_res_m`. A change to the method re-derives
  every HUC8 from the archive with no network; only a change to the transect
  placement itself would need elevation again, and the archive records each
  tile and its Last-Modified so the exact windows can be re-read.
- **Score** reads `xsections.parquet` and stores the slim geomorph block in
  the evidence (the medians, the reach stats and one drawable candidate).
  `scores.parquet` gains `tier, xs_status, dem_res_m, xs_n, xs_er, xs_bhr`.
  Staging sets each unit's `tier` (2 when every scored reach carries a
  geomorph block), `tiers` counts, and a `dem` block in the manifest; the
  viewer legend and each report's tier follow the unit.

Fidelity: the 10 m path is the live app's own py3dep call. The 1 m and 3 m
paths read the native tiles by HTTP range requests and reproject once, where
the live app receives a WMS mosaic resampled to a 1 m grid; elevations agree
to within the data's vertical accuracy and ratios can differ in the second
decimal. The QA tab quantifies both: mode "10m" forces the live path to 10 m
for an exact check of the arithmetic and the reach line, mode "best" runs the
live app unchanged.

Bandwidth: a 1 m reach costs 2.3 to 3.0 MB of range reads on the wire
(measured; about 0.3 MB at 3 m, a few KB at 10 m), so Virginia is roughly
270 GB and CONUS 4 to 5 TB. The sampling estimates every window's cost from
the tile blocks it touches (scaled to the measured wire cost), accumulates it
in `state/bandwidth.json` per calendar month and pauses at
`EASI_NATIONAL_XS_BYTE_BUDGET_GB`; the Cross-sections tab shows the month's
use against the budget. Nothing is downloaded whole: no tiles, no DEMs.

Storage on disk, per reach and in total:

| Kept | Per reach | Virginia (96,315) | CONUS (2.7 M) |
|---|---|---|---|
| `xs_profiles.parquet` (the archive) | about 20 KB at 1 m, about 3 KB at 10 m | about 2 GB | 55 to 75 GB |
| `reaches.parquet` and `xsections.parquet` | 3 to 4 KB | about 0.4 GB | about 10 GB |
| slim geomorph inside the published evidence | 0.4 to 0.7 KB | +50 MB | +1.1 to 1.9 GB |
| `dem_windows/` (opt-in) | 1 to 3 MB | 150 to 250 GB | not feasible |

The two catalogs (`national/dem/1m/tiles.parquet`, `national/dem/19/quads.parquet`)
are built by the national job from the S3 bucket listings: every 1 m project
tile with its UTM bounds (read from the tile headers where a project spans two
zones) and every 1/9 arc-second quad, each with size and Last-Modified.

## The panel

Overview (setup checks, the worker, coverage map), Units (queue chunks and
stages), **Data** (every national dataset with status, size, rows and a Fetch
button; every state's downloads by source with sizes; disk use), **Cross-sections**
(the catalogs, the month's bandwidth against the budget, the archive by state
with a Sample button that queues the two stages and, optionally, the re-score,
tiles, staging and publish; live sampling progress), Publish (staged changes,
history) and QA (parity against the live app, with the cross-section modes).
The worker buttons (Start, Pause, Resume, Stop now) are the same controls on
every tab: the tabs only append jobs to the queue.

Test: `python -m pytest` from this folder.

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
| `EASI_NATIONAL_WQP_METHOD` | auto | `auto` reads the chunk's nutrient results from the national ten-year parquet (`wqp_monthly`, no portal request) when it exists and pulls by station otherwise; `stations` forces the station pull, `cells` the older bounding-box pull (slow: the portal trickles rows for a dense cell for an hour) |
| `EASI_NATIONAL_FABRIC_CONCURRENCY` | 3 | flowline geometry batches in flight (only without the seamless geodatabase) |
| `EASI_NATIONAL_STREAMCAT_CONCURRENCY` | 3 | StreamCat region pulls in flight while the national cache builds |
| `EASI_NATIONAL_HUC8_WORKERS` | cores minus one, at most 6 | processes for the per-HUC8 derive, joins and score steps |
| `EASI_NATIONAL_XS_WORKERS` | one per CPU, 8 to 16 | processes for the cross-section sampling (about 0.5 s and 3 MB of 3DEP range reads per reach in one process) and derivation; a HUC8 runs serially inside one process, so the pool starts the largest HUC8s first |
| `EASI_NATIONAL_XS_BYTE_BUDGET_GB` | 800 | elevation bytes the sampling may read per calendar month (estimated from the tile blocks each window touches); the stage pauses at the budget and resumes next month, or sooner when the budget is raised |
| `EASI_NATIONAL_DEM_CATALOG_WORKERS` | 4 | S3 listings and tile headers in flight while the 3DEP catalogs build |
| `EASI_NATIONAL_WQP_MONTHLY_WORKERS` | 3 | months downloaded at once in the national monthly WQP pull |
| `EASI_NATIONAL_WQP_MONTHLY_START` | 2016-09 | first calendar month of that pull (through the current month) |

## Regional criteria and stored evidence

The builder and live app share the same adapters and criteria selection.
`EASI_CRITERIA_SET` defaults to `regional`; `legacy` retains the former
criteria with the current EASI rating anchors. Good / Fair / Poor map to
0.85 / 0.545 / 0.195 and function scores 13 / 8 / 3. SFARI and DEEP are unaffected.
The set name, active catalog, crosswalk and reference artifact contribute to
the method version. Staging records `criteria_set` beside `method_version`.
A mismatched set or method makes baked scores stale, while recalled reports
still score their stored evidence with the app's active criteria.

Schema 2 evidence stores `l3_code` and `nars9` at the existing derive anchor,
and an `erom` JSON block containing `qe_ma` plus `qe_01` through `qe_12` in
cubic feet per second. The national `erom` step reads those thirteen raw
estimates and COMID from `NHDFlowline_Network` into sorted
`national/erom.parquet`. The score stage reads only each HUC8's COMIDs and
passes the raw block to the app. A missing or nonfinite estimate makes the
block unknown. Derive and the elevation archive do not need to rerun.

The same `hydraulics.monthly_flow_cv()` evaluates both paths. It requires all
twelve months, uses population variance, rejects a nonpositive monthly mean
and rounds the CV to six decimals. The national path never fetches missing
evidence during scoring. Schema 1 records remain readable, resolving absent
regional keys from the bundled polygons and leaving absent EROM unknown.

`config.streamcat_aoi_by_name()` sends `prg_bmmi0809` to `other` and the
remaining names to their applicable AOIs. The map is part of both national
and chunk StreamCat digests. The model column has no AOI suffix. Population
support uses the integrity products when this probability is unavailable.
Live StreamCat GET requests for this model require `aoi=other`. The
2026-09-15 check found that `areaOfInterest=other` returned HTTP 200 with
only COMID. Check for the requested model column as well as the HTTP status.
The builder also sends `aoi` in its request payload. Standard live AOIs
retain their existing `areaOfInterest` parameter.

### Reference artifact

The emitter reads the completed registry, its historical `values_meta.json`,
the dataset vintage and existing panel evidence. It writes only the four
approved sets: corridor woody cover, corridor natural cover, monthly flow
variability and entrenchment. Level II sets require national fallbacks.
Entrenchment uses the three slope classes and a pooled national fallback
for missing slope. The latter is fitted from the already-selected national
panel and stored ER measurements, including members with unknown slope.

From this folder, using the shared root virtual environment:

```powershell
& ..\..\.venv\Scripts\python.exe -m builder.analysis.artifact --out ..\..\apps\easi\data\reference-curves.json
```

The export has sorted keys and six-place floats, and records historical
values method/version/date, registry timestamp/hash, StreamCurves engine
hash, screen caps and panel floors of 100 / 30. It does not rerun the stress
test or change the national data root. Re-vendor EASI into StreamCurves
after changing either generated scoring artifact. Displayed physical-value
crossings are rounded approximations; scoring interpolates the stored curve
at the exact 0.39 / 0.69 index edges.

### Phase F refresh order

The 2026-09 rework stops before Phase F until the owner confirms the national
refresh and publication. Once confirmed, use this order:

1. Run the national `erom` step to create the raw evidence cache.
2. Rebuild the national StreamCat cache for the new name-to-AOI map.
3. For each of the sixteen existing states, queue the `streamcat` and `score`
   chunk stages, then the affected tiles. Existing derive and cross-section
   evidence are reused. Run the queue with its retry wrapper.
4. Run one staging pass, inspect method/set freshness and the regional rating
   shares, then publish once. Upload the manifest last.
5. Verify live/preloaded parity at the stored anchors and `method_current`
   at the spot reports. `easi-national-current` must **always remain a prerelease**.
6. Snapshot `analysis/values.parquet` as `analysis/values_legacy.parquet`
   before the approved closing re-harvest. Refresh the closing analysis
   against the shipped criteria and verify S0 agrees with published stats.

The expected approximate 47% Functioning / 50% At-Risk / 3% Non-Functioning
shares are the approved analysis comparison, not a claim that Phase F has
already published those results.

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
  `erom.parquet`, `attains.parquet`) and the geometry, HUC12 and ATTAINS stages filter those
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
  result identifier dropped) and removed. From then on the chunk `wqp` stage
  (`stages/wqp_local.py`) takes its results from that file: the rows inside
  the chunk's buffered box and the app's ten-year window, renamed from the
  WQX3 columns to the legacy result columns and run through the same
  `normalize_rows` as the portal path, so the joins see identical
  station-result files and no state run touches the portal.
  The `states` step (after the geodatabase conversions) assigns every
  flowline to the Census state polygon containing its midpoint (the nearest
  polygon for the few coastal midpoints the 1:500,000 coastline leaves
  outside every state) and writes `national/comid_state.parquet`; the
  dashboard statistics group by it, so a reach belongs to one state even
  where a HUC8 crosses a border.
- Per HUC8: derive (anchor point, sinuosity, HUC12, regions, bankfull, NRSA),
  the two cross-section stages (below), joins (the four point services
  reproduced from the bulk pulls), score (`easi.assessment.assess_preloaded`,
  the app's own adapters).
- Every stage is idempotent with a done marker keyed by its inputs; long loops
  keep ledgers so **Pause** (finish the current request, exit) and **Resume**
  never repeat work. **Cancel** stops between requests.
- Tiles per region: FlatGeobuf + tippecanoe in Docker (`docker/tippecanoe`)
  -> PMTiles. Staging assembles the publish set (per-HUC4 evidence, per-region
  scores and tiles, the COMID index, HUC4 coverage, `stats.json`,
  `manifest.json`). `stats.json` (`builder/stages/stats.py`, about 5 KB per
  state) carries what the app's condition dashboard draws: for everything
  published and for each state, quantiles, a histogram and the band counts of
  the ECI and the three sub-indices, and for each function the rating
  counts, a 16-bin score histogram and the source-tier counts, plus each
  state's coverage (reaches screened over the state's reaches). It is skipped
  with a note until the national `states` step has run.
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

## The sensitivity analysis (builder.analysis)

The stress test of the EASI scoring over the national dataset (see
`notes/EASI_Rework/` for the brief, the decisions and the protocol). It
reads the evidence the builder wrote and never touches the scoring code:
`method_version()` stays as it is and the published scores are the baseline
every scheme is measured against.

```
python -m builder.analysis --steps strata candidates erom attains       # national caches (once)
python -m builder.analysis --steps landscape values nrsa                # the tables
python -m builder.analysis --steps panels curves runs stats report      # the stress test
python -m builder.worker analysis --steps ...                           # the same as a queue job
```

Every step writes under `<data root>/analysis/`, records a done marker in
`state/units.json` (unit `analysis`) with an inputs digest, and is skipped
while that digest matches (`--force` reruns). The command-line entry keeps
its own heartbeat (`state/analysis_progress.json`) so it can run beside a
queue worker.

| step | writes | what |
|---|---|---|
| `strata` | `strata.parquet`, `l3_to_l2_l1.csv`, `strata_parity.json` | every flowline's Level III / II / I, NARS-9 and physiographic keys, HUC12, state, anchor, flowline sinuosity and the slope, drainage-area and FCODE classes; the crosswalk from the NRSA site files |
| `candidates` | `streamcat_candidates.parquet` | about 60 StreamCat names the candidate metrics read, pulled by region with a per-name area of interest (`prg_bmmi0809`, `nrsa_frame`, `nars_region` only under `other`), after a probe of every name and scale |
| `erom` | `erom.parquet` | QE flow quantities from the raw national EROM cache, with earlier QA/QC/area-derived analysis quantities retained from stored analysis evidence when available |
| `attains` | `attains_au_attributes.parquet` | every ATTAINS assessment unit with its per-use statuses, cause columns and the two aquatic-life-use ratings |
| `landscape` | `landscape.parquet` | every reach: strata, cached StreamCat columns, candidates, EROM and the derived screen and curve quantities (checked against the values table) |
| `values` | `values.parquet`, `parity_A.json` | every scored reach re-scored with the app's evaluator; the flattened scoring trace (every input value, per-input rating, context), cross-section extras, evidence facts; scheme A parity is asserted |
| `nrsa` | `nrsa/nrsa_targets.parquet`, `nrsa_desktop.parquet`, `nrsa_frame.parquet`, `screen_check.csv` | the NRSA condition classes and field indicators per station visit, the desktop metrics per station (stored evidence inside the scored extent, synthetic records from the caches outside it), and the reference-screen check against EPA's 2013-14 designations |
| `panels` | `panels/reference_panels.parquet`, `panel_members.parquet` | least-disturbed panels per level and stratum: the strict desktop screen, one reach per HUC12, floors of 100 and 30, the relaxed tier below them |
| `curves` | `curves/curve_registry.parquet`, `curve_points.parquet`, `curves_<level>.json` | the reference curves per quantity, level and stratum (StreamCurves' engine), their 0.69 / 0.39 crossings, the usability rules and the fallback chain |
| `runs` | `schemes/<run>.parquet`, `candidates_<run>.parquet`, `scheme_comparison.csv`, `pinned_cells.csv`, `sanity_gradients.csv` | the runs S0, SN, S9, S2, S3 with the continuous, banded and mix views, the candidate substitutions, and the comparison tables |
| `stats` | `stats/*.csv`, `validation/*.csv`, `stability/*.csv` | distributions and flags, variance shares, the level tables (T-L1, T-L2), the paradigm tables (T-P1 to T-P3), border excess, the NRSA agreement and the panel bootstraps |
| `report` | `report/index.html`, `scorecards/`, `maps/`, `routes.csv`, `decision_sheet.md` | the report the owner decides from |

Tests: `tests/test_analysis_*.py` (synthetic fixtures; the values and NRSA
tests run the real evaluator over hand-built records).

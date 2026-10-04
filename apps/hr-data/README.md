# NHDPlus HR data app

A Dash app that serves a slim copy of the USGS NHDPlus HR network flowlines and
catchments, so the STAF apps no longer depend on the USGS NHDPlus HR MapServer
(`hydro.nationalmap.gov`), which answers its root in a second but takes 20 s to
over two minutes, or times out, on the spatial queries the apps need.

Status: **bundle v2 pilot** (plan: `~/.claude/plans/i-m-having-alot-of-bubbly-starlight.md`,
review: `notes/2026-10-01_HR_Mirror/precompute_review.md`). The engine and the three
apps do not call it yet.

## What it serves

- **Home page:** a Leaflet map. Click anywhere to drop a point, choose
  **Catchment** or **Upstream watershed**, then fetch it three ways and compare:
  1. **GDAL streaming from USGS**: GDAL reads the zipped USGS geodatabase on S3 in
     place (`/vsizip//vsicurl/`), no copy kept;
  2. **Download the USGS region (HU4)**: the region's USGS package is downloaded
     once to the server's disk, unzipped and read locally;
  3. **Slim copy in this app**: the files in `data/`.

  Each method finds the nearest network flowline within 200 m, then the catchment,
  or walks upstream with the site engine's interactive budget (3,000 reaches, 190
  levels) and joins the catchments. Each result is drawn in its own color with a
  card: stream, snap distance, area against the USGS area, outline size and the time
  of every step. The map also draws slim flowlines from zoom 12, slim catchments
  from zoom 13 (layer menu), the original USGS geometry (red dashed) in a few QA
  boxes per region, and the cached USGS NHD image.
- **REST API** (JSON, gzip when asked, CORS open):

| Route | Answers |
|---|---|
| `GET api/health` | dataset summary: recipe, regions, sizes, QA boxes |
| `GET api/lines?bbox=w,s,e,n` | network flowlines with the 15 fields of USGS layer 3 |
| `GET api/catchments?bbox=w,s,e,n&tol=20` | catchments in a box (`tol` applies to version 1 data only) |
| `POST api/catchments` `{"ids": [...], "tol": 20}` | catchments by NHDPlusID |
| `GET api/reach?nhdplusid=N` or `?hydroseq=N` | one flowline |
| `POST api/flowlines` `{"ids": [...]}` | flowline geometry by NHDPlusID |
| `GET api/tree?nhdplusid=N&max_reaches=&max_hops=` | the engine's upstream walk in one call |
| `GET api/watershed?nhdplusid=N&tol=20` | walk plus catchment union, area and agreement |
| `GET api/qa?bbox=w,s,e,n` | original geometry in the QA boxes |
| `GET api/where?lon=&lat=` | the USGS package at a point, whether it is downloaded, slim coverage |
| `POST api/pick` `{"lon", "lat", "method", "scope", "tol"}` | starts a fetch job; answers `{"job"}` |
| `GET api/job/<id>` | the job's step, timings and result (`running`, `done` or `error`) |
| `GET api/values?nhdplusid=N` | precomputed watershed values (version 2 with a values folder): land cover and riparian, impervious, roads, crossings, dams, soil K, with the site engine's names |
| `GET api/extras?nhdplusid=N&fraction=0.5` | per-flowline lookups at the nearest sample: sinuosity, HUC12, ATTAINS (EASI's record shape), NWI strip |

The values folder is `HR_DATA_VALUES`, else `values/` in or beside the data folder. It holds the
builder's exact files or their lean encoding (`hrslim/lean.py`: tenths of a percent, centimetres, sparse
dams; what `pack` ships), and `hrslim/values.py` reads either.
`hrslim/points.py` repeats the apps' point rules over the national tables (`tables/` beside the
data folder): EASI's WQP summaries (TN, TP and water temperature) and SFARI's nutrient medians,
NID dams within a mile (EASI and DEEP's radius, SFARI's box), SFARI's NWIS gage statistics and
EASI's NAS taxa; no route serves them yet. `wqp_results` (or `wqp_easi(..., details=True)`)
returns the results behind a WQP answer, each with its WQP result identifier. Water temperature
follows the STAF rule by default (converted Fahrenheit where a station has no Celsius; unrealistic
Celsius results left out); `fahrenheit=False, screen=False` is EASI's own rule.

## How the comparison runs

- `hrslim/jobs.py` holds the three fetch methods and a job store of small JSON
  files (`HR_DATA_JOBS`, default `<temp>/hr_data_jobs`), so any process sees a job.
- The slim method runs on a thread of the web app.
- The two USGS methods run in worker processes (`hrslim/worker.py`), one per method,
  started on first use. GDAL reads hold Python's GIL, so a streamed read (about a
  minute) on a thread would freeze every other request. A worker keeps its regions
  open between jobs, so later clicks show the warm cost.
- `hrslim/direct.py` reads the USGS packages: the S3 listing, the point to package
  lookup (`hrslim/vpu_index.geojson`, HU4 outlines), the download cache
  (`HR_DIRECT_CACHE`, default `<temp>/hr_direct`, oldest regions removed past
  `HR_DIRECT_CACHE_GB`, default 12) and the queries. `HR_DIRECT_PACKAGES` can name
  a JSON listing of local zips instead of S3 (the tests use it).
- A watershed from one USGS package stops at the package edge; the slim copy walks
  across regions.

## Data

`data/` (gitignored) holds the files `tools/hr-slim` builds. `hrslim.Dataset(folder)`
opens either format from its `manifest.json`:

- **Version 2** (`hrslim/fmt2.py`, the pilot since 2026-10-01): per region
  `lines2_<vpu>.parquet` (network flowlines simplified 2 m, slim attributes, the
  network as row offsets), `cats2_<vpu>.parquet`, `arcs2_<vpu>.parquet` and
  `steps2_<vpu>.bin` (catchments stored exactly, as shared borders on the
  elevation grid they were cut from, `hrslim/arcs.py` and `hrslim/grid.py`), plus
  `links2.parquet` for walks that cross regions. Catchment tolerance no longer
  applies: answers are exact, watershed areas are exact cell counts, and outlines
  are assembled from the borders the tree's catchments do not share.
  Line attributes are float64 (the services' values). A dataset whose lines carry
  `dnminor_step` (NHDPlus V2, from `DnMinorHyd`) is walked through minor divergences too, as
  NLDI navigates; NHDPlus HR keeps the site engine's main-path walk.
- **Version 1** (`hrslim/fmt.py`): `lines_<vpu>.parquet`, `catchments<tol>_<vpu>.parquet`
  and `qa_<vpu>.parquet`, coordinates rounded to 1e-5 degree, flowlines simplified
  5 m, catchments coverage-simplified at 10 or 20 m.

Point `HR_DATA_DIR` at another folder to serve it instead.

## Run and test

```bash
python apps/hr-data/app.py            # http://127.0.0.1:8030, data from HR_DATA_DIR or apps/hr-data/data
cd apps/hr-data && python -m pytest   # two-region fixtures for both formats and a tiny USGS-style package
```

## Deploy

`python tools/hr-slim/run.py --root D:\Data\nhdplus-hr\slim2 pack` copies the
version 2 pilot into `data/` (removing the files of an earlier pack first);
without `--root` it packs version 1 (`--tolerance 20` keeps one tolerance). Then deploy with Posit Publisher from this folder as its own VS Code window
(`.posit/publish/hr-data.toml`, type `python-dash`, Python 3.12), as a new content
item. If Publisher generates its own configuration instead, give it the same
`files` list (it must include `/hrslim/` and `/data/`) and `[python] version = "3.12"`.
The deployment record under `.posit/publish/deployments/` is untracked and must be
kept, like the other apps'.

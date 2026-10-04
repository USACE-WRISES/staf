# NHDPlus HR straight from the USGS zipped FileGDBs (proof of concept)

Can an app read one NHDPlus HR flowline and its catchment directly from the USGS
regional package on S3 (a zipped File Geodatabase) fast enough for an interactive
click, without hosting a copy of its own? This folder measures it. Nothing here is
wired into EASI.

## Modes

- **remote**: GDAL opens `/vsizip//vsicurl/<zip url>/<zip name>.gdb` and queries the
  package in place with HTTP range requests.
- **download**: the zip is downloaded once, unzipped to a cache folder, and the same
  queries run against the local FileGDB.

Each run starts in a fresh child process (cold GDAL caches); later clicks in a run
show the warm cost. Per-step HTTP range requests and bytes come from GDAL's own
debug log (`VSICURL: Downloading a-b`).

## Run locally

```bash
python tools/hr-gdal-poc/poc.py --lat 41.016806 --lon -93.76691 --clicks 3                 # remote, region looked up
python tools/hr-gdal-poc/poc.py --vpu 0710 --lat 41.016806 --lon -93.76691 --mode both --cache-mb 1024 --json out.json
python tools/hr-gdal-poc/vpu_index.py                                                       # rebuild the region outlines
cd tools/hr-gdal-poc && python -m pytest                                                    # log parser and lookup tests
```

Options: `--vpu` or `--url` (else the bundled outline lookup picks the package),
`--box-m` (half-width of the click box, 200 m), `--cache-mb` (0 = GDAL default),
`--clicks` (point, 300 m, 10 km, point again), `--keep` (reuse a downloaded copy).

## Posit Connect Cloud

`posit_app/` is a minimal Shiny app (`app.py`, `gdalpoc.py`, `vpu_index.geojson`,
`requirements.txt`, `.python-version`). Open the folder as its own VS Code window and
deploy it with Posit Publisher as a new content item (`hr-gdal-bench`, never a STAF
app's deployment). The configuration Publisher generated
(`.posit/publish/hr-gdal-bench-U783.toml`) must list all five files and Python 3.12.
Then use **Check environment** and **Run benchmark**. Results also print to the log
as `ENVIRONMENT {...}` and `BENCHMARK {...}` lines. Delete the content item afterwards.

## Facts found

- Packages: `prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/`,
  266 zips (HU4 in CONUS, five Great Lakes "i" units, HU8 in Alaska); the data the USGS
  MapServer serves. The `.gdb` folder inside dated packages keeps the date; field names are
  CamelCase in older packages and lower case in the 2022 reprocessed ones.
- Every member of the zip is deflate-compressed, so reaching a feature in place means
  inflating its table from the start; GDAL's default 16 MB HTTP cache then re-downloads.
- pyogrio's raw reader returns columns in file order, and a `where` column must be read too.
- Results and the recommendation: `notes/2026-10-01_HR_Mirror/gdal_direct.md`.

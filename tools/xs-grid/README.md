# Cross-section grid builder

The builder places a section every 100 ft (30.48 m) on every NHDPlus HR stream, river, canal and ditch flowline in the lower 48. It samples each section from USGS's 3DEP tile files, scores it with EASI's cross-section code, and keeps two things:

- **The archive**: every sampled transect, at full width and full resolution, stored in float32. It lives on a dedicated drive (`F:\staf-xs`, about 0.35 to 0.4 TB nationally). It lets the metrics be recomputed after a method change without reading the tiles again.
- **The metrics grid**: per-section ER, BHR, bankfull width, depth and stage, flood-prone width, low-bank stage, top of bank, and the quality flags. It is 3 to 5 GB nationally and could later be published as its own rolling prerelease. At a click, an app takes the 9 grid sections inside the reach and computes the reach medians by today's rules.

Plan and decisions: `notes/2026-10-01_HR_Mirror/xs_section_grid_plan.md`.

## Commands

Run from the repo root with the repo `.venv`. Every command runs below normal priority unless you pass `--full-priority`.

```
python tools/xs-grid/run.py place 0710               # place sections only, report counts
python tools/xs-grid/run.py build 0710 --workers 4   # sample, score, write (resumable per cell)
python tools/xs-grid/run.py build 0710 --cells 3     # test run on three cells (not merged)
python tools/xs-grid/run.py national --workers 5     # every lower-48 region not done yet
python tools/xs-grid/run.py status                   # regions done, bytes, drive space
python tools/xs-grid/run.py verify 0710 --sample 2000  # rederive from the archive, compare
```

Tests: `cd tools/xs-grid && python -m pytest tests`.

## Starting and pausing (the owner decides when)

The owner starts and stops runs. There is no schedule, so nothing starts on its own.

- **Start**, with no window, logging to the national log. The command uses PowerShell; run it from the repo root:

  ```
  Start-Process -FilePath ".venv\Scripts\pythonw.exe" -WorkingDirectory "D:\Code\Work\staf" -ArgumentList "tools\xs-grid\run.py","national","--first","0710,0601,0205,1711,1402,0307,0408,0506,1111,1506,0108,0206","--log","D:\Data\xs-grid\logs\national.log"
  ```

  The run picks up where the last one paused: cells already written are skipped, and cells sampled but not yet scored are scored first. Before starting, it checks that the archive drive is connected and writable, and that no other run is going (`D:\Data\xs-grid\build.lock`). It also refuses to start when `F:\staf-xs` is missing although regions are done (their metrics copies are in `D:\Data\xs-grid\metrics`): the drive is unplugged, or another drive took its letter, and a new empty archive must not be started there. A run also keeps the machine from sleeping.
- **Pause**: `python tools/xs-grid/run.py stop`. The run stops taking new cells, finishes the ones in hand (usually a minute or two), logs `PAUSED`, and exits.
- **Check**: `python tools/xs-grid/run.py status` says RUNNING or PAUSED, and shows regions done, the cells of the region in progress, and space left on the drive.
- **Optional window**: `--window 20:00-07:00` limits a run to those hours. It will not start outside them and pauses itself at the end.
- **Failed cells**: a cell whose sampling fails is tried once more later in the same run. If it fails again it stays to do, the region stays unmerged (`some cells failed; rerun to finish the region`), and the next start samples that cell and merges the region. A tile another process is renaming or deleting at that moment refuses to open ("file used by other process"); the read waits and tries again, up to 5 times.

Stopping the process tree outright (Task Manager, `taskkill /T`) also loses nothing. Every file is written under a temporary name and renamed when complete, and the next start deletes half-written tile downloads.

## What a section is

- **Placement.** Sections sit on the STAF data bundle's lines (`lines2_<vpu>.parquet`, the apps' lines). Each flowline part of length L gets `n = max(1, round(L / 30.48 m))` sections at `(k + 1/2) L / n`, so sections are as close to 100 ft apart as a whole number allows and never sit on a confluence. Only StreamRiver (460xx) and CanalDitch (336xx) are gridded. Artificial paths (lake and wide-river centerlines), connectors and pipelines are not.
- **Orientation and width.** These follow the app (`threedep.reach_geomorphology`):
  - the section is perpendicular to the chord from its point to the point 5 m downstream;
  - the half-width is eight regional bankfull widths, clamped to 250 to 800 m;
  - samples are spaced by min(10 m, DEM resolution), at most 2001 of them.
- **Bankfull inputs.** Drainage area is the flowline's own `totdasqkm`, the value a click on that flowline uses. The Bieger division is looked up at the section point (`xsgrid.sections.divisions_at` equals `bieger.division_at`).
- **Elevation source.** The rule is the app's (`dem_tiles.best_tile_dem`), applied per section, using the bundle's tile catalogs:
  1. the newest 1 m lidar project touching the transect that answers at least half of the samples;
  2. otherwise the next newest 1 m project;
  3. then the 1/9 arc-second (3 m) quads;
  4. then the 1/3 arc-second (10 m) seamless DEM.

  USGS stores 105 of the 8,345 quads, all from eight 2014-era projects, as `img<name>_19.img`. The catalog listed them as `<name>.img` until 2026-10-09, when the national builder's catalog step was fixed and the local bundle's copy rebuilt. The sampler still reads the file USGS serves when the listed one isn't there (`sample.quad_url`), and a quad served under neither name sends the sections touching it to the 10 m tiles, as a quad that won't read does in the app.
- **Sampling** (`config.SAMPLING_RULE`, version `xsgrid-1`). Each sample point is transformed from EPSG:5070 to the tile's own CRS and interpolated bilinearly between the four nearest cell centres of the native grid. The result is rounded to float32, the tiles' own precision, a change of at most 0.12 mm. The app instead reprojects a window and interpolates there, which depends on the window. The grid's numbers are exactly repeatable, so a section's profile can be rebuilt from its recorded tiles.
- **Metrics.** Each section goes through the app's chain for one transect: `balanced_profile`, `simplify_profile`, then `summarize_profile` at the section's regional bankfull. The input is the float32 samples the archive keeps, so recomputing from the archive reproduces the metrics bit for bit (`run.py verify`).
- **Faster thinning, same output.** `xsgrid.derive.simplify` replaces the last step of `simplify_profile`, a Visvalingam trim that rescans every point for each one it drops, with a heap. It uses the same areas by the same arithmetic and removes points in the same order (smallest first, leftmost on a tie), so the same points survive. The tests check this on 600 profiles, ties included. In the build, one section in 200 also runs the reference.

## Storage

Archive transects are coded exactly (`xsgrid/codec.py`): float32 mapped to order-preserving integers, then second differences under zstd. Measured on 262 real 1 m sections (`notes/2026-10-01_HR_Mirror/xs_research/xs_archive_codecs.py`):

| Stored | Bytes a sample | Bytes a section | National, about 378 M sections |
|---|---|---|---|
| float64, best lossless coding | 5.34 | 2,699 | about 1.0 TB (does not fit a 1 TB drive) |
| float32 written as is | 2.73 | 1,379 | about 0.52 TB |
| **float32, integer second differences (used)** | **1.84** | **927** | **about 0.35 TB** |

Metrics derived from float32 differ from metrics derived from float64 on about 6% of sections. On those sections the bank rules sit at a threshold, and a 0.1 mm change tips the entrenchment ratio. That is why the grid scores the stored float32 values: the archive stays exactly reproducible.

## Files

| Path | Holds |
|---|---|
| `F:\staf-xs\archive\xs_<vpu>.parquet` | transects and section geometry |
| `F:\staf-xs\metrics\xsm_<vpu>.parquet` | metrics (copy in `D:\Data\xs-grid\metrics`) |
| `F:\staf-xs\regions\<vpu>.json` | counts, bytes, sha256, tiers, timings |
| `F:\staf-xs\parts\<vpu>\` | per-cell part files while a region is in progress |
| `D:\Data\xs-grid\sections\` | placed sections per region |
| `D:\Data\xs-grid\tilecache\` | the 3DEP tiles in use, kept to about 100 GB (`--cache-gb`): the least recently used go first, and the finished build empties it |
| `D:\Data\xs-grid\logs\cells.jsonl` | one line per finished cell |

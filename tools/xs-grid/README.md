# Cross-section grid builder

The builder places a section every 100 ft (30.48 m) on every NHDPlus HR stream, river, canal and ditch flowline in the lower 48. It samples each section from USGS's 3DEP tile files, scores it with EASI's cross-section code, and keeps two things:

- **The archive**: every sampled transect, at full width and full resolution, stored in float32. It lives on a dedicated drive (`F:\staf-xs`, about 0.35 to 0.4 TB nationally). It lets the metrics be recomputed after a method change without reading the tiles again.
- **The metrics grid**: per-section ER, BHR, bankfull width, depth and stage, flood-prone width, low-bank stage, top of bank, and the quality flags. At a click, an app takes the 9 grid sections inside the reach and computes the reach medians by today's rules. It is published, region by region, as the rolling `staf-xs-current` prerelease (see "Publishing").

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
python tools/xs-grid/run.py release pack             # merged regions -> D:\Data\xs-grid\release
python tools/xs-grid/run.py release verify --placement  # packed files against the archive drive's
python tools/xs-grid/run.py release publish --yes    # upload what differs, release.json last
python tools/xs-grid/run.py release sync --yes       # pack, then publish
python tools/xs-grid/run.py release status --remote  # merged, packed, published
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

## Publishing (the rolling `staf-xs-current` prerelease)

The grid goes out region by region while the build runs. `xsgrid/release.py` packs each merged region into release files in `D:\Data\xs-grid\release` (`XSGRID_RELEASE` overrides). It only reads the archive drive and never changes it. The release is **always a prerelease** (CLAUDE.md guardrail 8), and publishing needs the owner's go.

- **Per region, three files**, read as they are by the apps and the static web app. Rows are sorted by flowline, and only `nhdplusid` carries row-group statistics, so a reader range-reads one row group.
  - `xs-<vpu>.parquet`: every section's scoring numbers. These are status, DEM resolution, bank method, the quality flags, ER, BHR, bankfull depth and the two widths, stored as scaled integers. They are exact at the grid's precision: ratios and depth to 0.01, widths to 0.1.
  - `xs-<vpu>-sections.parquet`: what drawing a section or pulling it again from USGS needs.
    - the thalweg (float32, exact) and its sample index;
    - the bankfull and low-bank stages above it (float32 m);
    - the regional bankfull inputs;
    - the half-width, the sample count, and the 3DEP project and tiles read.
    - Position and direction are not stored: `sections.place` rebuilds them bit for bit from the bundle's `lines2_<vpu>`, which are byte-identical to the published bundle's.
  - `xs-<vpu>-median.parquet`: one profile per HR segment, with its stages.
    - The section is the segment's one nearest both medians (`geomorph.median_candidate`, the apps' drawing rule).
    - The samples are the archive's own codes, copied unchanged.
    - Segments with no scored section have none.
- **`release.json`, written and uploaded last**: the grid's identity, the codes and column scales, each packed region's counts and assets, every asset's bytes and sha256, and the `pending` regions.
- **Commands:**
  - **`release pack`**: packs every merged region whose metrics changed (or the format did).
  - **`release verify`**: decodes every packed file and compares it with the archive drive's.
    - Numbers, thalwegs and stations must be exact, and stages exact at float32.
    - Medians must be the drawing rule's choice, with codes identical.
    - With `--placement` it also re-places every section from the bundle's lines and requires identical positions.
  - **`release publish --yes`**: creates the release as a prerelease if it's missing, uploads only the assets whose sha256 differs from GitHub's, then `release.json`. Add `--dry-run` to list the uploads first.
- **While the build runs:** `national ... --publish` packs and publishes each region as it merges.
  - This happens in a separate process (`release sync --yes`, logging to `D:\Data\xs-grid\logs\release.log`), one round at a time. A failed round is logged and the next merge tries again.
  - A pause waits for the round in hand, because it reads the archive drive.
- **Size, measured on 0710** (1.1 M sections): 6.4 MB numbers, 12.8 MB sections and 31.6 MB medians (33,387 profiles). Nationally that comes to about 25 GB, most of it the median profiles.

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
| `D:\Data\xs-grid\tilecache\` | the 3DEP tiles in use, kept to about 100 GB (`--cache-gb`): the least recently used go first, except tiles used in the last 10 minutes, and the finished build empties it |
| `D:\Data\xs-grid\logs\cells.jsonl` | one line per finished cell |
| `D:\Data\xs-grid\release\` | the `staf-xs-current` files (`release.json`, `xs-<vpu>*.parquet`) |
| `D:\Data\xs-grid\logs\release.log` | packing and publishing |

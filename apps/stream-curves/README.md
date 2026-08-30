# StreamCurves (Python)

The StreamCurves app — an interactive tool for developing **reference and regional
curves** for geomorphic stream metrics (STAF detailed tier: *Build*). Sister apps:
EASI (screening), SFARI (rapid), DEEP (detailed: run).

This Shiny for Python implementation replaced the original R Shiny app; the R
implementation is preserved on the
[`r-shiny-legacy`](https://github.com/USACE-WRISES/stream-curves/tree/r-shiny-legacy) branch.

## Layout

- `app.py` — thin Shiny shell: navbar, theme, STAF cross-app nav, help modal; mounts view modules.
- `streamcurves/` — pure domain package (no `shiny` imports): workbook I/O, cleaning/derivation,
  stratification screening, effect sizes, model candidates/diagnostics, the reference-curve
  engine, exports (OH SQT workbook, DEEP assessment bundles), REST data sources, and
  `site_engine_source.py` (the selectable site-engine predictor source).
  Each module's docstring names the R source file it ports.
- `streamcurves/_vendor/` — vendored copies of the EASI screening engine and the STAF site
  engine (`libs/site_engine`), each refreshed by its `scripts/vendor_*.py` and drift-gated.
- `views/` — py-shiny UI/server modules (one per R `mod_*` module) sharing a typed `AppState`.
- `config/`, `data/` — copied from the R repo (YAML/JSON registries, catalogs, geojson, OH templates);
  `config/metric_map.yaml` also carries the `se_*` site-engine predictor vocabulary.
- `tests/` — pytest; `tests/golden/` holds fixtures generated from the R pipeline
  (`scripts/export_golden.R`) that the Python port must reproduce. The fixtures
  and the workbook they derive from are internal data and are not committed —
  golden-parity tests skip when they are absent.

## NRSA data

The app reads NRSA through `streamcurves.nrsa_dataset`, which knows two datasets.

**`legacy-1819`** is the default: the bundled `data/nrsa_metrics.parquet` and
`data/nrsa_sites.csv`, NRSA 2018-19 only, 1,919 sites. Do not regenerate these
two files. Every published assessment fingerprints them in its manifest
(`provenance.build_inputs`), `tests/test_data_provenance.py` pins their sha256,
and changing them would break the reproducibility of work already published.

**`multi-cycle-v1`** is the archive under `data/nrsa/`, built from EPA's own
public files for 2013-14, 2018-19 and 2023-24. It exists because EPA renames
every site each cycle, so the cycles are mostly different places rather than
repeat visits, and pooling takes the station pool from 1,919 to 4,378. Rebuilding
2018-19 from it reproduces the legacy snapshot cell for cell (898,716 cells, zero
differing), which is the check that the crosswalk and the site matching are right.

Building it, in order (nothing but the fetch touches the network):

```powershell
.venv/Scripts/python.exe scripts/nrsa/import_reference_workbooks.py   # xlsx -> data/nrsa/reference/*.csv
.venv/Scripts/python.exe scripts/nrsa/build_metric_dictionary.py      # readable metric names
.venv/Scripts/python.exe scripts/nrsa/fetch_nrsa_raw.py               # 112 files, ~107 MB, pinned in sources.lock.json
.venv/Scripts/python.exe scripts/nrsa/build_station_tables.py         # stations that persist across cycles
.venv/Scripts/python.exe scripts/nrsa/snap_missing_comids.py          # ~1,200 NHD snaps, cached, run once
.venv/Scripts/python.exe scripts/nrsa/build_station_tables.py         # again, to fold the snaps in
.venv/Scripts/python.exe scripts/nrsa/build_values_table.py           # values, counts, crosswalk, manifest
```

Three things the build does that are worth knowing. It **backfills 2018-19 benthic
and fish metrics from the legacy snapshot**, because EPA publishes none for that
cycle and the bundled parquet carries the prior R application's, exactly, for all
1,919 sites; `data/nrsa/value_origins.csv` records which metric and cycle came from
where. It **derives `phab_XSLOPE_use` for 2013-14** from `phab_XSLOPE`, which the
build re-measures at 0.999 agreement on the cycles publishing both before applying.
And it **snaps the stations EPA published no COMID for**, which matters because
StreamCat is joined by COMID and the reference-evidence index drops any record it
cannot place on a reach.

`fetch_nrsa_raw.py --verify` re-checks the live URLs against the lock without
downloading, so an EPA republication is visible rather than silently absorbed.
The raw CSVs land in `notes/DEEP_Working/nrsa_raw/` and are never committed;
`data/nrsa/` is, and the build fails if it exceeds 40 MB, because everything
under `apps/` ships in the desktop payload.

Three traps are pinned in `scripts/nrsa/nrsa_io.py` and worth knowing before
touching this code: the files are latin-1 rather than UTF-8, some carry a UTF-8
BOM on top of that, and `VISIT_NO` is not always a number (`R` marks a repeat
sample and must not be coerced onto visit 1).

### Using it in a run

`--nrsa-dataset multi-cycle-v1` on `scripts/run_regional_analysis.py` or
`scripts/run_region_batch.py stage` pools the cycles; `--nrsa-cycle` (repeatable)
narrows it. The default stays `legacy-1819`, and a default run reproduces its
`inputsDigest` byte for byte, which is what keeps the three published assessments
reproducible.

The manifest records the dataset id, the cycles, the selection policy and a digest
over `data/nrsa/manifest.json`, and the **dataset is part of `inputsDigest`** for
anything other than the default. Without that, two runs over one ecoregion on
different data would share a digest, which is precisely what that digest promises
cannot happen.

The predictor source works the same way: the manifest records
`inputs.predictor_source` (engine id, version, and the vendored-copy hash for a
site-engine run), it joins `inputsDigest` for anything other than the default
`streamcat`, and published bundles carry a `predictorSource` stamp (bundle-level
plus per-metric, inside the content digest) because the same curve fitted on a
different predictor source is a different assessment. The default contributes no
digest key, so every previously published digest still reproduces byte for byte.

Two properties of a pooled pool to expect rather than be surprised by. The cycles
are mostly *different places*, so pooling roughly doubles the pool rather than
adding repeat visits. And several habitat metrics are measured only on wadeable
reaches, so they sit near half missing: embeddedness is present for 30 of 30
wadeable stations and 1 of 34 boatable. That share is the same pooled or not, 48
percent either way on Interior Plateau, because roughly half of NRSA is boatable by
design. What pooling changes is the count: 31 wadeable sites instead of 12.
`protocol` rides on the panel so a run can filter or stratify on it.

### Metric names

`data/nrsa_metric_catalog.csv` has a `label` column, but it is only the mnemonic,
which is why metrics used to render as `phab_XEMBED`. `streamcurves.metric_names`
resolves a real name from `data/nrsa/metric_dictionary.csv`, preferring a curated
label in `config/metric_names.yaml`, then `config/metric_map.yaml`, then EPA's own
field metadata. Names resolve at render time as well as at build time, so a
session saved before the dictionary existed, and a published assessment that must
never be edited, both still read as names. A name someone typed is never
overwritten.

## Develop (Windows)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m shiny run --port 8012 --host 127.0.0.1 app.py
.venv\Scripts\python.exe -m pytest -q          # unit + golden parity tests
```

Sessions save as schema-versioned `.streamcurves.json` files (the R app's `.rds`
sessions are not readable here — keep the R app around for those).

`requirements.txt` is the pinned runtime for deployment; `requirements-dev.txt`
adds pytest and other dev-only tooling.

## Navigation

The workflow strip under the top bar is the primary navigation: six numbered
stages (`streamcurves/run_state.py` `STAGE_KEYS`), each a page. Stages 1-3 open
the Data & Setup wizard at the matching step; two supplementary tools sit in
the top bar, outside the numbered stages.

- **1 Region & Data Sources / 2 Screen Candidate Sites (EASI) / 3 Build
  Dataset** — the Data & Setup page: landing (open a workbook/session), then
  the map-first import wizard (region → add data → screen sites → choose
  metrics → compile → classify → build).
- **4 Refine Workbook, Map Functions & Validate** — the opened-project
  workspace: the editable workbook grid, discipline→function mapping,
  redundancy review, validation/QA prechecks.
- **5 Reference Curves & Flagged Review** — the flagged-curve review queue,
  the curve gallery (one inline-SVG thumbnail per metric with the reference
  range shaded and the DEEP condition breaks drawn; click a tile to open its
  analysis, or jump to its table row) beside the per-metric summary
  mega-table (the strip's Gallery / Table chips switch them), the 4-phase
  workspace (explore → verify → confirm → finalize), the curve editor, and
  the export hub (OH List-of-Metrics + SQT Reference Curves workbooks, the
  Science Support HTML, and a DEEP assessment bundle).
- **6 Package & Publish** — Draft file downloads and Preliminary/Final
  publishing into the STAF assessment library (`apps/library`), with an
  optional DEEP re-bake.
- **Regional Curves** (top bar) — power-function (log-log) bankfull-vs-drainage-area curves.
- **Cross-Sections** (top bar) — on-demand geomorphic cross-sections (NLDI snap + 3DEP terrain).
- **NRSA Explorer** (top bar) — browse every NRSA station across the 2013-14,
  2018-19 and 2023-24 surveys on a map, coloured by which cycles sampled each
  place, filtered by ecoregion and cycle (including "sampled in all of them",
  which finds the places with a real time series). Clicking a station shows its
  visits, the site id EPA gave it in each cycle, and a few metrics side by side
  across cycles. Read-only, and the only tool that works with no project open,
  so its chip is never dimmed.

A headless path runs the same pipeline under the governed methodology
(`config/methodology/`; the version is stamped into every run manifest):
`scripts/run_regional_analysis.py`
screens, builds, and stages a publish for one EPA Level III ecoregion with full
provenance. Recorded human inputs ride as flags: `--reviewer-decisions` (per-item
adjudications, machine-checked against each record's computed evidence),
`--finalize-metric` (the only way a flagged curve publishes), `--remove-metric`
(takes a built, diagnosed curve out of scope for one region), and
`--approve-portfolio` (SELECT-01). The direction registries under `config/` declare each
metric's expectation and its seed geometry (physical domain, signed scale, low-tail
treatment, caveats).

A batch mode (`scripts/run_region_batch.py`) runs the same pipeline for a new
ecoregion with no human gate until the end: `stage` runs the evidence pass once,
applies the standing-decision policy (`config/methodology/standing_decisions.yaml`,
the owner's class decisions from the pilots) to the review queue, publishes into the
run's own staged library root, and writes `review_packet.md` for the owner. `promote`
confirms the staged decisions under the owner's name after the end review and publishes
the identical content into `apps/library`. `replay` proves the policy reproduces the
published pilots' recorded decisions. `stage-many` stages a list of Level III codes in
sequence with the same flags (names from the NRSA site table) and writes
`batch_summary.md`; it never promotes. A stage refuses to proceed when the screen left
more than a share of the candidates unresolved (a service outage, `--max-unresolved-share`,
default 10 percent; `--allow-unresolved` stages anyway on the record), reads its own
`streamcat_cache.json` on a re-stage so the evidence pass reproduces offline, and
screens each site once even where the bundled NRSA table repeats an id.
`--predictor-source` (default `streamcat`, or `site-engine`) selects which source
computes the curve predictors: `site-engine` recomputes them at the training sites
with the vendored site computation engine (about a minute per uncached site) and
stamps the bundle's `predictorSource`; a replay recovers the choice from the run's
own manifest.

The import wizard and cross-sections tab pull from public REST services (USGS
NLDI/3DEP, EPA StreamCAT, USGS StreamStats, and Model My Watershed); each source
fails to NA rather than aborting. The STAF site engine (vendored) is an optional
predictor source beside StreamCat — the wizard and the region builder offer a
Predictor source select, exact-watershed values arrive labeled in column
provenance, and the default stays StreamCat. Model My Watershed needs an API key:
set `MMW_API_KEY`, or put the key in the gitignored `scripts/.mmw_api_key`.

## Deploy (Posit Connect Cloud)

The app deploys as a `python-shiny` content type. `.python-version` pins 3.12 and
`.posit/publish/streamcurves.toml` lists the runtime files (`app.py`,
`requirements.txt`, and the `streamcurves/`, `views/`, `www/`, `data/`, `config/`
directories). Publish with the Posit Publisher (`publisher deploy`) or the
Connect Cloud UI pointed at this repo. Interactive maps need `ipyleaflet` +
`shinywidgets` (both pinned in `requirements.txt`).

## Conventions

Follows the DEEP app's py-shiny conventions: core (non-Express) syntax, pinned
`requirements.txt` for Posit Connect Cloud, shared `www/styles.css` STAF design tokens
under the app-specific `www/curves.css`, per-app copy of `STAF_LINKS` (the "home" entry is the
STAF link in the navbar; the rest drive the DEEP deep links and the desktop overrides).

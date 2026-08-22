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
  engine, exports (OH SQT workbook, DEEP assessment bundles), REST data sources.
  Each module's docstring names the R source file it ports.
- `views/` — py-shiny UI/server modules (one per R `mod_*` module) sharing a typed `AppState`.
- `config/`, `data/` — copied from the R repo (YAML/JSON registries, catalogs, geojson, OH templates).
- `tests/` — pytest; `tests/golden/` holds fixtures generated from the R pipeline
  (`scripts/export_golden.R`) that the Python port must reproduce. The fixtures
  and the workbook they derive from are internal data and are not committed —
  golden-parity tests skip when they are absent.

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

A headless path runs the same pipeline under the governed methodology
(`config/methodology/`, version 0.7-provisional): `scripts/run_regional_analysis.py`
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
published pilots' recorded decisions.

The import wizard and cross-sections tab pull from public REST services (USGS
NLDI/3DEP, EPA StreamCAT, USGS StreamStats, and Model My Watershed); each source
fails to NA rather than aborting. Model My Watershed needs an API key: set
`MMW_API_KEY`, or put the key in the gitignored `scripts/.mmw_api_key`.

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

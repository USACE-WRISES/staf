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

## Tabs

- **Data & Setup** — landing (open a workbook/session), the map-first import
  wizard (region → sites → metrics → compile → classify → build), the editable
  workbook grid, discipline→function mapping, validation/QA.
- **Reference Curves** — the per-metric summary mega-table and the 4-phase
  workspace (explore → verify → confirm → finalize), the curve editor, and the
  export hub (OH List-of-Metrics + SQT Reference Curves workbooks, the Science
  Support HTML, and a DEEP assessment bundle).
- **Regional Curves** — power-function (log-log) bankfull-vs-drainage-area curves.
- **Cross-Sections** — on-demand geomorphic cross-sections (NLDI snap + 3DEP terrain).

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
under the app-specific `www/curves.css`, per-app copy of `STAF_LINKS` + `staf_topnav()` (STAF banner link).

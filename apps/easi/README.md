# EASI — Ecosystem Assessment Screening Index

EASI is a web application that **automates a Screening-tier stream ecosystem
assessment**. From a single clicked point on a map it delineates the upstream
watershed and an assessment reach, computes **20 stream-function metrics from
public, national GIS/hydrology data**, scores them with the **STAF** (Stream
Type Assessment Framework) rollup math, and produces an interactive report — a
metric table, function scores, Physical / Chemical / Biological sub-indices, an
overall **Ecosystem Condition Index (ECI)**, an editable channel cross-section,
and PDF/CSV/GeoJSON exports.

It runs entirely on **free, keyless public data services** (USGS, EPA, USACE) —
no API keys, accounts, or paid subscriptions are required. Built with **Shiny
for Python** and deployable to **Posit Connect Cloud** straight from this repo.

> **Screening tool, not a regulatory determination.** Many metrics are national
> proxies or DEM/regional-curve estimates; each carries a confidence flag and is
> user-overrideable. Results are a desktop screening aid, not a substitute for
> field assessment or jurisdictional delineation. See [Disclaimer](#disclaimer).

---

## What it does

A StreamStats-style, full-screen map with a left workflow pane and a four-step
stepper: **Identify → Basin → Configure → Report**.

1. **Identify** — Pan/zoom a USGS National Map basemap (Topo or Imagery) with an
   NHD hydrography overlay. At zoom ≥ 14, NHD stream vectors load for the view and
   **clicking snaps to the nearest stream line** (or tells you if you missed).
   A type-ahead **address/place search** (Photon + Nominatim) recenters the map.
2. **Basin** — Delineate the contributing **watershed** and an **upstream reach**
   (default ~1,000 ft, adjustable) with staged progress feedback. Shows COMID,
   HUC12, drainage area, watershed area, and reach length.
3. **Configure** — Browse the 20 functions grouped by discipline; toggle any on or
   off and pick the **data source** where alternatives exist. Each metric has an
   ⓘ card with its definition, the calculation used, and the scoring criteria.
4. **Report** — A popup with the **outcome rollup** (ECI + sub-indices + cards),
   a **basin-characteristics** section, an **editable cross-section**, and the
   **metric table** with inline overrides and per-metric notes. Export to
   **PDF / CSV / GeoJSON**.

## How it scores (STAF rollup)

Each metric is rated **Good / Fair / Poor**, mapped to an index (0–1) and a
function score (0–15), then combined with Clean-Water-Act outcome weights into:

- **Physical**, **Chemical**, and **Biological** sub-indices, and
- a single **Ecosystem Condition Index (ECI)**.

The 20 metrics span five disciplines:

| Discipline | Example metrics |
|---|---|
| **Hydrology** | Impervious surface cover · Percent wetlands · Concentrated runoff · Flow alteration |
| **Hydraulics** | Low-flow wetted connectivity · Floodplain engagement frequency · Floodplain access / entrenchment · Hyporheic exchange |
| **Geomorphology** | Channel-evolution stage · Bank erosion & armoring · Sediment supply · Substrate condition |
| **Physicochemistry** | Stream temperature · CPOM / detrital processing · Nitrogen & phosphorus · Regulatory impairment (303(d)/305(b)) |
| **Biology** | In-stream habitat complexity · Biological integrity (IBI surrogate) · Invasive species · Fish-passage barriers |

Every metric produces a value; field- or low-confidence metrics show a confidence
badge and can be **overridden** in the report.

## Cross-section & overrides

- A **representative 3DEP cross-section** is sampled along the reach, re-datumed to
  the channel bottom, with a feet/metres toggle.
- **Edit the bankfull and floodplain heights** to recompute the entrenchment ratio
  (lateral → *Floodplain access / connectivity*) and the bank-height ratio
  (vertical → *High flow dynamics* recurrence); the plot redraws and both metrics
  re-rate live, while a manual rating pick still takes precedence until the next edit.
- **Inline Good/Fair/Poor overrides** on any metric (pick the computed value to
  revert) plus **per-metric notes**, all carried into the exports.

## Data sources (all public, no API keys)

| Source | Used for |
|---|---|
| **NHDPlus** via HyRiver (`pynhd`, NLDI / WaterData) | Stream vectors, point snap, watershed delineation, reach derivation, VAAs |
| **USGS 3DEP** (`py3dep`) | DEM cross-sections → entrenchment, bank-height ratio, slope |
| **EPA StreamCat** | Watershed landscape metrics (impervious, wetlands, roads, dams, riparian, erodibility, …) |
| **NLCD** (via `pygeohydro`) | Land cover (alternative wetlands source) |
| **EPA Water Quality Portal (WQP)** | Observed stream temperature, nutrients |
| **EPA ATTAINS** (keyless `gispub` service) | 303(d)/305(b)/TMDL impaired-waters status (at point + nearby) |
| **USACE National Inventory of Dams (NID)** | Fish-passage barriers |
| **USGS Nonindigenous Aquatic Species (NAS)** | Invasive species presence |
| **USGS National Map** | Topo / Imagery basemaps + NHD overlay |
| **Photon (Komoot) + Nominatim (OSM)** | Address / place geocoding |
| **Bieger et al. (2015)** curves + **Fenneman** physiographic divisions (bundled) | Location-aware bankfull geometry |

Where more than one source exists for a metric, the **Configure** step lets you
choose (wetlands: StreamCat / NLCD · temperature: observed WQP / climate surrogate ·
impairment: ATTAINS / modeled landscape surrogate).

## Tech stack

Shiny for Python (Core) · `shinywidgets` + `ipyleaflet` (map) · HyRiver
(`pynhd` / `py3dep` / `pygeohydro`) · `geopandas` / `shapely` / `pyogrio` /
`pyproj` / `rasterio` / `rioxarray` / `xarray` · `numpy` / `pandas` ·
`matplotlib` (Agg) + `reportlab` (PDF) · `requests`.

## Run locally

```bash
git clone https://github.com/USACE-WRISES/easi.git
cd easi

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

shiny run app.py                     # open http://127.0.0.1:8000
```

Development / tests:

```bash
pip install -r requirements-dev.txt
python -m pytest
python scripts/build_easi_metrics.py # regenerate data/easi-metrics.json from the STAF source
```

Requires **Python 3.12**.

## Deploy (Posit Connect Cloud)

This repo is ready to deploy from GitHub — no build step or manifest required.

1. Push this repository to GitHub (public).
2. In **Posit Connect Cloud** → **Publish** → choose **GitHub**, select the repo
   and branch, and set the primary file to **`app.py`**.
3. Choose **Python 3.12**. Connect Cloud installs `requirements.txt` (pip only;
   all dependencies ship manylinux wheels — no system packages needed) and serves
   the `app` object.
4. **No environment variables or secrets are required** — every data service is
   keyless. The HyRiver request cache is written to the ephemeral temp directory
   automatically (`HYRIVER_CACHE_NAME` defaults to `tempfile.gettempdir()` in
   `app.py`), which is correct for Connect Cloud's ephemeral filesystem.

## Repository layout

```
app.py                     Shiny (Core) UI + server: map, workflow stepper, report modal, exports
easi/
  scoring.py               rating → index → function score → CWA rollup → sub-indices → ECI (STAF math)
  config.py                constants, CWA mapping, data loaders, per-metric registry + definitions
  assessment.py            assemble report; rescore overrides; cross-section build/recompute
  pipeline.py              async orchestration (delineate / assess)
  delineation.py           watershed + upstream-reach derivation
  geomorph.py · bieger.py  cross-section geometry, entrenchment/bank-height, regional bankfull curves
  hydraulics.py · xsplot.py  channel hydraulics + cross-section plot (matplotlib)
  report.py                PDF / CSV / GeoJSON exports
  metrics/                 per-metric adapters (base.py contract) by discipline
  datasources/             thin keyless clients (NHD, 3DEP, StreamCat, NLCD, WQP, ATTAINS, NID, NAS, geocode)
data/
  easi-metrics.json        20 metric defs + Good/Fair/Poor thresholds (generated from STAF)
  functions.json           function metadata · cwa-mapping.json  function → P/C/B weights
  physio_divisions.geojson Fenneman physiographic divisions (Bieger curve selection)
www/                       styles.css + tooltip/report-edit/geocode JS (served as static assets)
scripts/build_easi_metrics.py   regenerates data/easi-metrics.json from the STAF source TSV
scripts/build_docs.py      one-command rebuild of the V&V documentation (assets + Quarto render → www/)
  run_sfari_sites.py · build_doc_assets.py · sfari_data.py   EASI runs, figures, and SFARI data join
docs/EASI_Documentation/   V&V report source (Quarto) → www/documentation.html   (see its README)
tests/                     pytest suite (scoring parity, metric binning, geomorph, report, tooltip)
requirements.txt           pinned runtime deps (Posit Connect Cloud)   ·   requirements-dev.txt  (+ pytest)
```

## Tests

```bash
python -m pytest
```

Covers the STAF scoring rollup parity, per-metric rating bins, cross-section
geometry (`balanced_profile`, entrenchment / bank-height), the report exports, and
the report tooltip rendering.

## Documentation

Extended verification and validation documentation is published as a single
self-contained page at `www/documentation.html`, served by the app and linked from its
header ("Documentation"). The source is a Quarto report in `docs/EASI_Documentation/`.
To rebuild it after editing text, values, or figures, run `python scripts/build_docs.py`
(see [docs/EASI_Documentation/README.md](docs/EASI_Documentation/README.md) for the full
edit-and-rebuild guide).

## Methodology & references

- **STAF — Stream Type Assessment Framework**: the screening method EASI automates
  (metric definitions, Good/Fair/Poor criteria, and the function/outcome rollup).
- **Rosgen** entrenchment ratio and bank-height ratio (channel form & incision).
- **Bieger, Rathjens, Allen & Arnold (2015)** — regional hydraulic-geometry
  (bankfull) curves used for the default cross-section geometry.
- **Fenneman** physiographic divisions — region selection for the bankfull curves.

## Disclaimer

EASI is a **desktop screening tool**. Several metrics are national-scale proxies or
estimates derived from 10 m DEMs and regional regression curves; each is labeled
with a data-confidence level and can be overridden with local/field data. EASI
results are **not** a regulatory determination, a jurisdictional waters delineation,
or a substitute for a field assessment.

## License

[MIT](LICENSE) © 2026 WRISES.

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
stepper: **Identify → Basin → Assessment → Report**.

1. **Identify** — Pan/zoom a USGS National Map basemap (Topo or Imagery) with an
   NHD hydrography overlay. At zoom ≥ 14, two stream layers load for the view:
   **bold lines** have StreamCat data (the NHDPlus V2 network) and **thin
   lines** are the rest of the high-resolution NHD. **Clicking snaps to the
   nearest stream line** (or tells you if you missed). On a bold line the
   StreamCat lookup engine answers the watershed metrics in seconds. On a thin
   line the STAF site engine calculates the exact watershed at the clicked
   point (usually well under a minute, up to about five minutes on a large basin, with a progress line); the three
   reach-keyed metrics (low flow, substrate, biological integrity) come from
   the nearest covered reach downstream, labeled with the routed distance and
   drainage-area ratio, and are unavailable past a 10x ratio. The policy is
   fixed by the framework: nothing asks the user to pick a method, and every
   value says which engine produced it.
   A type-ahead **address/place search** (Photon + Nominatim) recenters the map.
2. **Basin** — Delineate the contributing **watershed** and an **upstream reach**
   (default ~1,000 ft, adjustable) with staged progress feedback. Shows COMID,
   HUC12, drainage area, watershed area, and reach length. On a thin-line
   stream the card names the watershed engine, the exact watershed area and the
   reaches walked, and says where reach-keyed evidence comes from.
3. **Assessment** — All 20 metrics compute automatically, then a worksheet walks
   the functions by discipline. Each card shows the metric, its scoring method
   (inputs, breakpoints, and the resulting rating), and the evidence source, with
   inline overrides, notes, and the editable cross-section.
4. **Report** — A popup with the **outcome rollup** (ECI + sub-indices + cards),
   a **basin-characteristics** section, an **editable cross-section**, and the
   **metric table** with inline overrides and per-metric notes. Export to
   **PDF / CSV / GeoJSON**.

A **Batch** mode runs up to 10 sites from a pasted list and packages the reports
as a ZIP (the engine itself accepts up to 150 sites for programmatic use).

## How it scores (STAF rollup)

Each metric is rated **Good / Fair / Poor**, mapped to an index (0–1) and a
function score (0–15), then combined with Clean-Water-Act outcome weights into:

- **Physical**, **Chemical**, and **Biological** sub-indices, and
- a single **Ecosystem Condition Index (ECI)**.

The 20 metrics span five disciplines:

| Discipline | Automated method |
|---|---|
| **Hydrology** | Land-cover pressure (worse of impervious / agricultural cover) · Wetland extent · Road-density inflow proxy · Degree of regulation (storage ÷ runoff) |
| **Hydraulics** | Low-flow condition (NRSA wetted channel → StreamCat HYD) · Floodplain engagement (BHR) · Floodplain access (ER) · Hyporheic-exchange potential (better of channel gradient / sinuosity) |
| **Geomorphology** | Channel-adjustment susceptibility (FCODE + BHR/ER) · Bank-instability susceptibility (BHR, observed bank evidence supersedes) · Sediment-supply potential (worst of agriculture, soil K-factor, roads) · Substrate condition (NRSA embeddedness → StreamCat SED) |
| **Physicochemistry** | Thermal-regulation vulnerability (worse of woody riparian and impervious) · Organic-matter supply potential · Nutrient condition (WQP vs NRSA regional benchmarks → StreamCat CHEM) · Regulatory impairment (ATTAINS → StreamCat CHEM) |
| **Biology** | Habitat-support potential (woody riparian corridor) · Biological integrity (measured NRSA → prG_BMMI → ICI/IWI) · Invasive-species pressure · Nearby dam proximity |

Every metric produces a value; field- or low-confidence metrics show a confidence
badge and can be **overridden** in the report.

### Evidence hierarchy

Each metric resolves **one fixed automatic hierarchy** — connected observation →
published model → named screening proxy. Users improve the evidence rather than
choosing between competing formulas. The exact inputs, operators, Good/Fair/Poor
boundaries, basis, limitations and citations live in `data/screening-methods.json`,
which `easi/screening_methods.py` evaluates and the worksheet panel renders, so the
displayed criteria cannot drift from what produced the rating.

Missing required data is never scored as zero: a metric with an absent input or a
failed source stays explicitly unavailable, and an outcome with no evidence reports a
dash rather than a red zero. Reports separate **availability coverage** (how many
metrics were rated) from the **evidence profile** (how many came from observations,
published models, or screening proxies), and flag correlated evidence — the same
BHR/ER geometry and StreamCat integrity components feed several metrics, so 20/20 means
complete availability, not 20 independent field observations.

## Cross-section & overrides

- A **representative 3DEP cross-section** is sampled along the reach, re-datumed to
  the channel bottom, with a feet/metres toggle.
- **Edit the bankfull and floodplain heights** to recompute the entrenchment ratio
  (lateral) and the bank-height ratio (vertical); the plot redraws and all four
  geometry-driven metrics re-rate live — floodplain access (ER), floodplain engagement
  (BHR), bank-instability susceptibility (BHR), and channel-adjustment susceptibility
  (BHR + ER). A manual rating pick still takes precedence until the next edit, and where
  observed bank or channel evidence is active it stays the effective result while the
  generated proxy updates underneath it.
- **Inline Good/Fair/Poor overrides** on any metric (pick the computed value to
  revert) plus **per-metric notes**, all carried into the exports.

## Data sources (all public, no API keys)

| Source | Used for |
|---|---|
| **NHDPlus** via HyRiver (`pynhd`, NLDI / WaterData) | Stream vectors, point snap, watershed delineation, reach derivation, VAAs |
| **NHDPlus HR** (hydro.nationalmap.gov MapServer) | Full-resolution stream display, the clicked reach's attributes, and the nearest-covered-reach routing for streams outside the V2 network (`easi/routing.py`, `easi/datasources/nhd_hr.py`) |
| **STAF site engine** (vendored from `libs/site_engine`, `easi/watershed.py`) | The exact point watershed and its land cover, roads, dams, soil K and EROM runoff for streams outside the StreamCat lookup network. Never used on covered streams. Definitions of both engines: `libs/README.md` |
| **USGS 3DEP** (`py3dep`) | DEM cross-sections → entrenchment, bank-height ratio, slope |
| **EPA StreamCat** (the StreamCat lookup engine) | Watershed landscape metrics on the V2 network (impervious, wetlands, roads, dam storage, runoff, riparian, erodibility) plus the published HYD/SED/CHEM/CONN/TEMP/HABT integrity components and prG_BMMI, which exist only per V2 COMID |
| **EPA NRSA 2018–19** (bundled extract) | Connected field evidence: wetted channel, embeddedness, benthic/fish condition |
| **NLCD** (via `pygeohydro`) | Land cover (fallback where StreamCat is absent) |
| **EPA Water Quality Portal (WQP)** | Total N / total P observations (normalized); context-only temperature |
| **EPA NARS nine regions** (bundled) | Regional NRSA nutrient benchmarks |
| **EPA ATTAINS** (keyless `gispub` service) | 303(d)/305(b)/TMDL category from the nearest assessed unit |
| **USACE National Inventory of Dams (NID)** | Mapped dams within a one-mile geodesic radius |
| **USGS Nonindigenous Aquatic Species (NAS)** | Established invasive taxa |
| **USGS National Map** | Topo / Imagery basemaps + NHD overlay |
| **Photon (Komoot) + Nominatim (OSM)** | Address / place geocoding |
| **Bieger et al. (2015)** curves + **Fenneman** physiographic divisions (bundled) | Location-aware bankfull geometry |

Source selection is automatic and fixed per metric (see **Evidence hierarchy** above);
a fallback is recorded in the trace and shown in the Scoring method panel, so it is
always visible which tier produced a rating.

**Two watershed engines, one fixed policy.** The eight watershed metrics read
their inputs from a watershed evidence layer (`easi/watershed.py`) with two
providers. On the NHDPlus V2 network the StreamCat lookup engine supplies them
(precomputed EPA StreamCat summaries keyed by COMID). On any other NHD stream
the STAF site engine delineates the exact watershed at the clicked point and
computes them from NLCD, TIGERweb, NID, SSURGO and EROM. If the engine fails or
refuses (a basin past its budget), the watershed metrics are unavailable with
guidance rather than a proxy. The batch engine exposes the policy as
`BatchConfig.watershed_engine`: `auto` (the default described here) and
`streamcat-legacy` (the pre-2026-09 behavior: every metric on the nearest
covered reach, refused past the 10x ratio), which StreamCurves pins its
reference screen to. Whether the site engine should also answer covered
streams is decided by the score-level equivalence study in
`libs/site_engine/scripts/score_equivalence_study.py`, not by a setting.

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
  screening_methods.py     canonical evaluator: typed operators only, no arbitrary expressions
  methods.py               display projection of the catalog for the "Scoring method" panel
  scoring.py               rating → index → function score → CWA rollup → sub-indices → ECI (STAF math)
  config.py                constants, CWA mapping, data loaders, per-metric registry + definitions
  assessment.py            assemble report; rescore overrides; coverage; cross-section recompute
  pipeline.py              async orchestration (delineate / assess)
  delineation.py           watershed + upstream-reach derivation
  geomorph.py · bieger.py  cross-section geometry, entrenchment/bank-height, regional bankfull curves
  hydraulics.py · xsplot.py  channel hydraulics + cross-section plot (matplotlib)
  report.py                PDF / CSV / GeoJSON exports
  metrics/                 per-metric adapters (base.py contract) by discipline
  datasources/             thin keyless clients (NHD, 3DEP, StreamCat, NLCD, WQP, ATTAINS, NID,
                           NAS, NRSA, geocode)
data/
  screening-methods.json   the automated method catalog: inputs, operators, exact bands, source
                           hierarchy, basis, limitations, citations (single source of truth)
  easi-metrics.json        20 STAF metric defs (names, statements, prose criteria kept as a
                           dormant fallback; generated from the STAF source TSV)
  nrsa-2018-19-evidence.json.gz  deterministic NRSA extract for connected field evidence
                                 (covers 2013-14, 2018-19 and 2023-24 despite the name;
                                  rebuild with scripts/build_nrsa_evidence.py)
  nars-ecoregions-9.geojson.gz   EPA NARS nine regions (regional nutrient benchmarks)
  functions.json           function metadata · cwa-mapping.json  function → P/C/B weights
  physio_divisions.geojson Fenneman physiographic divisions (Bieger curve selection)
www/                       styles.css + tooltip/report-edit/geocode JS (served as static assets)
scripts/build_easi_metrics.py   regenerates data/easi-metrics.json from the STAF source TSV
scripts/build_nrsa_evidence.py  rebuilds the NRSA extract from the four public EPA CSVs
scripts/fetch_nars_ecoregions.py  refreshes the NARS nine-region polygon asset
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

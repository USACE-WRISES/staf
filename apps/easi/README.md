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
   NHD hydrography overlay. At zoom ≥ 14 the map draws one stream network, the
   high-resolution NHD, in one solid blue style. The Layers menu contains
   **Streams** and an optional **StreamCat coverage** checkbox, initially off.
   Enabling coverage distinguishes **StreamCat reaches** in blue from **Other
   streams** in cyan and shows the source-reach highlight and downstream
   connector. The choice lasts for the current page session and only changes
   the display. The assessment point, reach, and watershed remain distinct.
   Detailed engine information stays in per-metric sources and exports.
   **Clicking snaps to the nearest stream line** (or tells you if you missed).
   The point stays visible while its StreamCat source resolves. Delineate and
   screening require that source; temporary routing failures retry up to three
   times, after pauses of 5, 10, and 15 seconds,
   followed by **Retry StreamCat lookup** if still unresolved. The persistent
   status above Delineate distinguishes lookup, retries, no match, and failure.
   A small circle spins beside active lookup messages, including retry pauses;
   it stays stationary when reduced motion is preferred.
   Within 150 ft of an NHDPlus V2 reach, the StreamCat lookup engine answers
   the watershed metrics in seconds. On other streams,
   the STAF site engine calculates the HR reach watershed of the clicked stream
   (usually well under a minute, up to about five minutes on a large basin,
   with a progress line); reach-keyed metrics (including low flow and
   biological integrity) come from the nearest StreamCat reach downstream,
   and each says so with the routed distance and the drainage-area ratio,
   whatever that ratio is. The policy is fixed by the framework: nothing
   asks the user to pick a method, and every value says which engine produced
   it. A type-ahead **address/place search** (Photon + Nominatim) recenters
   the map.
2. **Basin** — Delineate the contributing **watershed** and an **upstream reach**
   (default ~1,000 ft, adjustable) with staged progress feedback. Shows COMID,
   drainage area and reach length, and identifies the StreamCat reach supplying
   evidence. Routine engine names and versions stay out of the Basin card.
3. **Assessment** — All 20 metrics compute automatically, then a worksheet walks
   the functions by discipline. Each card shows the metric, its scoring method
   (inputs, breakpoints, and the resulting rating), and the evidence source, with
   inline overrides, notes, and the editable cross-section.
4. **Report** — A popup with the **outcome rollup** (ECI + sub-indices + cards),
   a **basin-characteristics** section, an **editable cross-section**, and the
   **metric table** with inline overrides and per-metric notes. A dagger marks
   downstream desktop evidence, explained in a small note below the table;
   detailed provenance stays with the source information. Export to
   **PDF / CSV / GeoJSON**.

A **Batch** mode runs up to 10 sites from a pasted list and packages the reports
as a ZIP (the engine itself accepts up to 150 sites for programmatic use).

The **Nationwide screening** switch in the header opens a view-only map over
the precomputed national dataset (`tools/easi-national` builds it chunk by chunk and publishes
it to the rolling `easi-national-current` GitHub prerelease): every NHDPlus V2
reach screened automatically and not reviewed, for fast site screening in
support of an assessment. Reaches draw by condition
band, a coverage layer shows which HUC4 units are published, and a click
rebuilds the reach's full report from its stored evidence with the app's own
adapters (`easi.national`). While the tiles for a new area load, a small cue at
the bottom of the map says so, and a tile the server could not fetch leaves a
note asking for a Refresh. Set `EASI_NATIONAL_BASE` to read another base, such
as the builder's local `staging/` folder.

The screening pane's **Dashboard** view (`easi/national/dashboard.py`) draws the
dataset's statistics asset (`stats.json`, built by the builder's staging step)
as a condition dashboard: box-and-whisker plots of the ECI and the three
sub-indices over the shaded condition bands, the rating shares of the 20
functions by category (or, on a toggle, box plots of their three-valued
scores), a function sensitivity table (spread of the ratings, share not rated,
share from a screening proxy, the range across states) and a state comparison
for any measure, with a scope select over the states screened so far and a
CSV export. A reach belongs to the state containing the midpoint of its
flowline; states with under half their reaches screened are drawn muted.
Everything is precomputed, so the view needs no per-reach reads.

## Local rebuild review

For a local-only review of a rebuilt national dataset, launch from `apps/easi`:

```powershell
$env:EASI_CRITERIA_SET = 'regional'
$env:EASI_NATIONAL_BASE = 'D:/Data/easi-national/staging'
$env:EASI_REVIEW_ROOT = 'D:/Data/easi-national'
$env:EASI_REVIEW_BASELINE = 'D:/Data/easi-national/review/2026-09-15-regional/baseline'
& ..\..\.venv\Scripts\python.exe -m shiny run app.py --host 127.0.0.1 --port 8000
```

`EASI_REVIEW_BASELINE` is optional and identifies the saved pre-rebuild tree.
The **Local review** header link appears only when `EASI_REVIEW_ROOT` is set.
It opens a read-only page with staging and analysis build provenance,
matched-reach comparisons, frozen regional scoring fits, diagnostic fits,
and the local analysis report. The main **Nationwide screening** map and
**Dashboard** read local staging, with reach reports, state comparisons and
statistics CSV export.
The saved baseline for the September 2026 rebuild uses the earlier 13/8/3
scores. It differs from the current `legacy` switch, which retains the previous
criteria with the current 14/8/2 scores. Baseline comparisons therefore include
both criteria and score-anchor changes.

The review page reads the bundled `data/reference-curves.json` for frozen
scoring fits. It reads regenerated fits from
`analysis/curves/curve_registry.csv` separately. Reference-panel quartiles
and fitted knots are shown where available. A diagnostic refresh cannot
replace the frozen scoring artifact through this page. Pending or stale
outputs retain their method and build labels.
Diagnostic fits, field validation and the report remain marked pending until
the analysis values method matches the app and the output file is newer than
those values metadata. A partially finished analysis refresh stays visible.
The final verified label also requires `analysis/local-review/completion.json`
to match the app method, criteria, exact staging build and frozen artifact hash,
with passed analysis, comparison and landscape checks. Until then checks remain
pending even when individual outputs are ready.

Optional `analysis/local-review/comparison.json` provides `provenance`,
`summary_rows` (`measure`, `legacy`, `current`, `change`), `states` and
`functions` from the local comparison script. The page reads only small
JSON/CSV summaries, with no Parquet scans. Report assets are restricted to
HTML, PNG, SVG and CSS within `analysis/report`; routes reject non-loopback
clients. No review routes or filesystem exposure are enabled by default,
and the review page has no publication controls or data-writing actions.

## How it scores (STAF rollup)

Each metric is rated **Good / Fair / Poor**, mapped to an index (0–1) and a
function score (0–15), then combined with Clean-Water-Act outcome weights into:

- **Physical**, **Chemical**, and **Biological** sub-indices, and
- a single **Ecosystem Condition Index (ECI)**.

### EASI rating anchors

| Rating | Index anchor | Function score, rounded from index x 15 |
|---|---:|---:|
| Good | 0.90 | 14 |
| Fair | 0.55 | 8 |
| Poor | 0.10 | 2 |

These are EASI rating anchors, rather than arithmetic band midpoints. Good
and Poor sit nearer the ends of their condition bands so a three-valued
rating can express more of the difference in condition. Fair stays near the
middle of the At-Risk band. Python's half-to-even rounding gives 14 from
13.5 and 2 from 1.5. The twenty functions, CWA weights, rollup formulas and
0.39 / 0.69 outcome boundaries retain their existing definitions. SFARI and
DEEP scoring are unchanged.

### Regional reference criteria

`EASI_CRITERIA_SET=regional` is the default for the app and national builder.
It uses banded Good / Fair / Poor scoring with the following reference rules:

| Functions | Regional criteria |
|---|---|
| Habitat provision; woody input to Light and thermal regime | Woody vegetation in the 100 m watershed corridor, using an EPA Level II reference curve |
| Carbon processing | Natural vegetation in the same corridor, using an EPA Level II reference curve |
| Floodplain connectivity; ER input to Channel evolution | National entrenchment-ratio reference curves for slopes below 0.5%, 0.5% to below 2%, and 2% or greater |
| Low flow and baseflow dynamics | Monthly EROM flow variability, using an EPA Level II reference curve with lower variability rated better |
| Bed composition and bedform dynamics | Watershed agriculture share, with the same 30 / 50 bands as the agriculture input to Catchment hydrology |
| Population support | EPA StreamCat `prg_bmmi0809`, requested in the `other` area of interest: Good at 0.50 or above, Fair from 0.25 to below 0.50, Poor below 0.25 |

A missing or unusable Level II curve resolves to its national curve. Missing
or invalid slope resolves to the pooled national entrenchment curve. The
artifact is `data/reference-curves.json`, with its historical dataset,
reference-screen, panel and curve-engine provenance. `data/ecoregion-crosswalk.json`
maps the existing Level III polygon codes to Level II and Level I.

Curves describe the existing least-disturbed reference panels. A quantity's
interpolated reference index determines its band at 0.39 and 0.69, and the
band maps to the anchors above. The trace and scoring panel identify the
resolved region or national fallback, reference sample size and approximate
physical-value crossings. Displayed crossings are rounded; ratings use curve
interpolation at the exact index edges. Fixed impervious-cover and bank-height-ratio inputs retain their bands.

Low-flow variability is the population standard deviation of all twelve
monthly QE estimates divided by their mean, rounded to six decimals. Missing
or nonfinite flow evidence, or a nonpositive monthly mean, leaves it unknown.
This proxy is **unvalidated for field low-flow condition** and must not be
interpreted as measured baseflow. Bed composition and Catchment hydrology
share agriculture evidence and form a disclosed correlated pair. Where the
benthic model is unavailable, Population support retains the disclosed
catchment/watershed integrity-product fallback.

For live StreamCat GET requests, the model requires the query parameter
`aoi=other`. The 2026-09-15 check found that `areaOfInterest=other` returned
HTTP 200 with only COMID, so successful status alone does not establish that
`prg_bmmi0809` was returned. Standard-AOI requests retain their existing
`areaOfInterest` parameter.

Set `EASI_CRITERIA_SET=legacy` before launching the app or worker to retain
the former criteria and NRSA/integrity adapter tiers. Legacy criteria use
the same current 0.90 / 0.55 / 0.10 anchors. Invalid set names raise an error.
The set is part of `method_version()`, and a national manifest for another
set is flagged by the app. The criteria switch is separate from the
`streamcat-legacy` watershed-routing policy.

After the regional criteria are accepted, revisit removal of the legacy
catalog, legacy adapter branches and NRSA scoring tier together.

The 20 metrics span five disciplines:

| Discipline | Automated method |
|---|---|
| **Hydrology** | Land-cover pressure (worse of impervious / agricultural cover) · Wetland extent · Road-density inflow proxy · Degree of regulation (storage ÷ runoff) |
| **Hydraulics** | EROM monthly flow variability · Floodplain engagement (BHR) · Floodplain access (slope-class ER reference curves) · Hyporheic-exchange potential (better of channel gradient / sinuosity) |
| **Geomorphology** | Channel-adjustment susceptibility (FCODE + BHR/ER) · Bank-instability susceptibility (BHR, observed bank evidence supersedes) · Sediment-supply potential (worst of agriculture, soil K-factor, roads) · Bed-composition proxy (watershed agriculture share) |
| **Physicochemistry** | Thermal-regulation vulnerability (worse of woody riparian and impervious) · Organic-matter supply potential · Nutrient condition (WQP vs NRSA regional benchmarks → StreamCat CHEM) · Regulatory impairment (ATTAINS → StreamCat CHEM) |
| **Biology** | Habitat-support potential (Level II woody-corridor curve) · Population support (published benthic model → ICI/IWI) · Invasive-species pressure · Nearby dam proximity |

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

- **Nine 3DEP cross-sections** are sampled at even stations along the reach from one
  DEM fetch, re-datumed to the channel bottom, with a feet/metres toggle. The four
  geometry metrics score on the reach medians of the entrenchment and bank-height
  ratios; the section nearest both medians is drawn by default, the reach table lists
  every section beside the medians and ranges, and the arrows step through the others
  (a scrolled section re-rates the metrics from itself, labeled as that section).
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
| **NHDPlus V2** via the USGS fabric API (flowlines and attributes; the successor of the retiring WaterData WFS) and HyRiver `pynhd` (NLDI basins, navigation, point snap with a flowtrace fallback) | Stream vectors, point snap, watershed delineation, reach derivation, VAAs and raw mean-annual/monthly EROM QE flows |
| **NHDPlus HR** (hydro.nationalmap.gov MapServer) | Full-resolution stream display, the clicked reach's attributes, and the nearest-covered-reach routing for streams outside the V2 network (`easi/routing.py`, `easi/datasources/nhd_hr.py`) |
| **STAF site engine** (vendored from `libs/site_engine`, `easi/watershed.py`) | The HR reach watershed (the drainage area of the high-resolution reach the click snaps to) and its land cover, roads, dams, soil K and EROM runoff for streams outside the StreamCat lookup network. Never used on covered streams. Definitions of both engines: `libs/README.md` |
| **USGS 3DEP** (`py3dep`) | DEM cross-sections → entrenchment, bank-height ratio, slope |
| **EPA StreamCat** (the StreamCat lookup engine) | Watershed landscape metrics on the V2 network (impervious, wetlands, roads, dam storage, runoff, riparian, erodibility) plus the published HYD/SED/CHEM/CONN/TEMP/HABT integrity components and `prg_bmmi0809` at AOI `other`, which exist only per V2 COMID |
| **EPA NRSA 2018–19** (bundled extract) | Connected field evidence retained by the legacy criteria: wetted channel, embeddedness, benthic/fish condition |
| **EPA ecoregions and stored reference panels** (bundled crosswalk/curves) | Level II corridor/flow expectations and national slope-class entrenchment expectations |
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

**Two watershed engines, one fixed policy.** The watershed metrics read
their inputs from a watershed evidence layer (`easi/watershed.py`) with two
providers. On the NHDPlus V2 network the StreamCat lookup engine supplies them
(precomputed EPA StreamCat summaries keyed by COMID). On any other NHD stream
the STAF site engine delineates the HR reach watershed (the drainage area of the
high-resolution reach the click snaps to, built from NHDPlus HR catchments and
checked against the reach's published drainage area) and
computes them from NLCD, TIGERweb, NID, SSURGO and EROM. If the engine fails or
refuses (a basin past its budget), the watershed metrics are unavailable with
guidance rather than a proxy. The batch engine exposes the policy as
`BatchConfig.watershed_engine`: `auto` (the default described here) and
`streamcat-legacy` (the pre-2026-09 behavior: every metric on the nearest
covered reach, refused past the 10x ratio), which StreamCurves pins its
reference screen to. Whether the site engine should also answer covered
streams was decided by the score-level equivalence study in
`libs/site_engine/scripts/score_equivalence_study.py`, not by a setting: it
reported Outcome B on 2026-09-02 (rating agreement 0.84 against a 0.90 bar,
with the class and the index agreeing), so covered streams stay on the
lookup engine and the approximation is documented.

## Tech stack

Shiny for Python (Core) · `shinywidgets` + `ipyleaflet` (map) · HyRiver
(`pynhd` / `py3dep` / `pygeohydro`) · `geopandas` / `shapely` / `pyogrio` /
`pyproj` / `rasterio` / `rioxarray` / `xarray` · `numpy` / `pandas` ·
`matplotlib` (Agg) + `reportlab` (PDF) · `requests`.

## Report preparation

Report popups keep the current workspace visible while the mini map is prepared
in the background. A small **Preparing report…** status marks the wait; closing
the popup returns focus to its opener. USGS basemap requests allow two attempts
with a 12-second timeout and a one-second retry pause. Only valid PNGs are cached
(up to 32 images), so reopening after a failed request can recover. If the
background remains unavailable, the report retains the watershed/reach outline
and a small **Map background unavailable** note. The PDF uses the same image
fetching and cache. Posit Publisher includes the required modules; the background
is fetched at runtime, rather than bundled as a deployment asset.

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
  screening-methods.json   regional automated methods: inputs, bands/curves, hierarchy and citations
  screening-methods-legacy.json  former criteria, selected with EASI_CRITERIA_SET=legacy
  reference-curves.json    deterministic regional and national reference curves with provenance
  ecoregion-crosswalk.json Level III to Level II/Level I keys and region names
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

The [EASI walkthrough](https://usace-wrises.github.io/staf/walkthroughs/easi/#regional-reference-criteria)
describes the current regional criteria. Its metric reference is generated by
`scripts/build_walkthrough_reference.py` from the active catalog.

The app also serves `www/documentation.html`, a **historical verification and
validation record** with cached cases and the earlier 13 / 8 / 3 mapping. Its
scope notice is dated 2026-09-15. Those results have not been revalidated under
the regional criteria or current anchors. The canonical source is
`docs/EASI_Documentation/easi-vnv.qmd`. For this scope-only update, use a
prose-only Quarto render that preserves the existing figures and tables. See
[the documentation build guide](docs/EASI_Documentation/README.md) before any
full rebuild, which regenerates methods and validation assets.

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

# DEEP — Detailed Evaluation of Ecosystem Processes

The **detailed-tier** assessment tool of the Stream Tiered Assessment Framework
(STAF), sibling to **EASI** (screening) and **SFARI** (rapid). DEEP runs a
site's *measured* metric values through **published reference curves** to
produce function scores, Physical / Chemical / Biological outcome sub-indices,
and an overall Ecosystem Condition Index (ECI).

DEEP is forked from SFARI. The scoring **rollup** (functions → outcomes → ECI)
is reused unchanged, so all three STAF tiers land on one comparable scale. What
differs is the front half: instead of Likert professional judgment, DEEP
interpolates each metric value on its reference curve to get a 0–1 index, then
averages a function's metric indices into a 0–15 function score.

Detailed assessments are **built** in the companion **StreamCurves** app
(`stream-curves`, Shiny for Python) and **run** here. DEEP ships with a
predefined library of state-SQT assessments and also accepts user-uploaded
assessment bundles.

## Status

**Phase 1 — headless scoring core (done):**

- `deep/curves.py` — piecewise-linear reference-curve interpolation + the
  multi-metric-per-function mean.
- `deep/scoring.py` — the SFARI rollup (functions → outcomes → ECI), reused.
- `deep/assessments.py` — load a predefined assessment or an uploaded bundle.
- `deep/config.py`, `deep/models.py` — constants/loaders and run models.
- `scripts/build_deep_data.py` — distills the STAF metric library into `data/`.
- `scripts/build_us_states.py`: builds `data/us_states.geojson.gz`, the state
  boundary layer, from the Census 1:500,000 cartographic boundary file.

**Phase 2 — interactive app (done):**

- `app.py` — Shiny-for-Python app: **Identify → Basin → Region → Assessment →
  Report**. Identify/Basin + the map/delineation engine are reused from SFARI
  (`deep/{delineation,pipeline}.py`, `deep/datasources/`); the Assessment worksheet
  is curve-based (numeric value → live index + computed 0–15 function score).
- `deep/report.py` — CSV / GeoJSON / PDF exports. `deep/session.py` — save/resume
  (delineation + inlined assessment + measured values). `deep/measure.py` —
  measured-value assembly (desktop auto-compute merges here; Phases 3 and 6).
- `www/measure.js`, `www/deep.css` — worksheet interactions + styling.
- `examples/spring-sample.deep.json` — a synthetic SPRING-built assessment bundle
  for exercising the upload path until the real SPRING export exists.

**Phase 3 — desktop auto-compute (done; extended by Phase 6):**

- `deep/metrics/{base,computed}.py` — a registry mapping desktop-derivable
  detailed metricIds to adapters that compute the raw value, reusing EASI's
  datasource code: watershed impervious / land cover (StreamCat, NLCD fallback)
  and reach geomorphic ratios ER / BHR / W:D (nine 3DEP DEM cross-sections along
  the reach, reach-median ER and BHR, Bieger bankfull; the transect code comes
  from the vendored site engine's EASI extracts, only `deep/bieger.py` is DEEP's).
- `deep/measure.py` + `deep/pipeline.py` gain a `compute_metrics_only` stage;
  the app runs it on entering **Assessment** and prefills the computable metrics
  with a source badge (values stay editable — editing flips origin to field).
  Auto-modeled geomorphic ratios are labelled "modeled" (confirm/override).

**Phase 4 — stratified curves + deploy (done):**

- Multi-stratum metrics carry `curveLayers` + `activeStratum`; the Assessment step
  shows a per-metric **stratum dropdown** and the chosen stratum's curve drives
  scoring (`curves.active_points` / `curve_strata`; `MeasuredValue.stratum`).
  `scripts/build_deep_data.py` emits layers from the STAF curves — 79 metrics
  carry real strata (Rosgen stream type, slope, bed material) — and the
  stream-curves exporter emits `curveLayers` for stratified curves too.
- `.posit/publish/deep.toml` configures Posit Connect Cloud deployment
  (mirrors EASI).

The STAF site's Tools page (`staf/docs/_data/apps.yml`) links out to DEEP and
the StreamCurves builder, both hosted on Posit Connect Cloud.

**Phase 5 — what stands behind a score (2026-08-21, after the DEEP project's
adversarial review):**

- Bundles now carry per-metric annotations stamped by StreamCurves:
  `referenceN`, `sampleDisposition`, `metricRole` (`response` or
  `stressor_surrogate`), `curveCaveats`, `confidenceLabel`, `confidenceTotal`,
  `referenceRange`, and (Phase 6) `predictorSource` — which source computed the
  predictors the curves were fitted on (`streamcat` when absent, or
  `site-engine vX`). DEEP retains unknown fields, so older bundles still load.
- The assessment card and detail pane show the **reference tier** the curves were
  drawn at (least disturbed or best available); a best-available bar is never
  mistaken for reference condition.
- The metric information card lists the curve basis (tier, role, reference
  sites, builder confidence band) and the builder's caveats ("Read with care").
- Four scoring advisories compose beside a score (`deep/curves.py:metric_warning`):
  the endpoint clamp, a value outside the reference pool's observed range, a
  thin-sample curve (`sampleDisposition` insufficient) that should be read as a
  band, not a point value, and the train/serve pairing advisory (Phase 6). The
  first three never change the index; the pairing advisory does — an
  engine-computed value against a curve fitted on StreamCat predictors renders
  as labeled reference evidence and is withheld from scoring
  (`curves.metric_index` returns no index for it).
- Curves may be two-sided (`curve.form: optimum`), including a flat-low-tail
  variant; `interp_curve` is shape-agnostic and scores them unchanged.
- Published regional assessments are preliminary until the scientific team
  certifies them; nothing in DEEP implies certification. DEEP lists only
  Preliminary and Final (the display label for stored `certified`) versions:
  drafts (automation output not yet human-reviewed in StreamCurves) are never
  baked into the registry.

**Phase 6 — site-engine auto-pull + the train/serve pairing rule (2026-08-29):**

- The STAF site engine (`libs/site_engine`, vendored at `deep/_vendor/site_engine/`
  by `scripts/vendor_site_engine.py`, drift-gated) joins the auto-compute
  registry: HR reach watershed values (impervious, anthropogenic cover, and the
  engine reach for geomorphic ratios) computed for the assessed reach on the
  full-resolution NHD. Engine adapters run only when the loaded bundle's
  `predictorSource` records engine predictors (or the pairing mode is `label`).
- `MeasuredValue.engine` marks engine-origin values; a user edit clears it.
- The pairing rule is enforced at the scoring layer, not just at pull time:
  `deep/curves.py:metric_index` withholds the index whenever an engine value
  meets a StreamCat-fitted curve, and `metric_warning` explains why. Engine
  values score only against curves whose provenance records engine predictors.
  Until 2026-09 the running app never reached this rule: `measure.py`
  dropped the `engine` flag when it rebuilt values from the worksheet state,
  so the rule could only fire in tests. It now keeps the flag for desktop
  values, and the exports read their indices through the same scoring layer,
  so a withheld value prints as reference only everywhere.
- The regional bundles' landscape metrics auto-pull too (2026-09):
  `spring-pctimp2019ws`, `spring-pctcrop2019ws`, `spring-pctwdwet2019ws`,
  `spring-pcthbwet2019ws`, `spring-rddensws`, `spring-damdensws`,
  `spring-bfiws`, `spring-rdcrsws`. The engine gate is read metric by metric
  (2026-09-07): a curve whose own `predictorSource` records engine predictors
  takes the STAF site engine's value from the HR reach watershed (base flow
  index from the USGS base-flow index grid, crossings counted on the NHDPlus
  HR network, both since engine 0.3.0), and every other curve takes the
  StreamCat lookup engine's value by COMID, the source it was fitted on, so a
  mixed bundle scores both and nothing is withheld. The desktop adapters never
  run the engine themselves: the app runs it once at Delineate and hands the
  record over. When no StreamCat
  reach is known, NLCD over the HR reach watershed polygon stands in for the
  land-cover ids. A StreamCat crossings value carries the API units caution
  in its source label.

**Two watershed engines (2026-09):**

- **STAF site engine** (`deep/engine_prefill.py` over the vendored copy): the
  HR reach watershed, the drainage area of the high-resolution NHD reach the
  click snaps to (NHDPlus HR catchments aggregated and checked against the
  reach's published drainage area; the reach, not the point, is the outlet).
  It is the watershed at every site, and the desktop value source for each
  metric whose curve carries the engine `predictorSource` stamp.
- **StreamCat lookup engine**: EPA StreamCat by NHDPlus V2 COMID, the desktop
  value source for every metric whose curve carries no engine stamp. On a stream outside
  V2 its values are labeled with the nearest StreamCat reach they describe
  (`, describes the nearest StreamCat reach Mink Brook (COMID 9327042), 446 ft
  downstream, which drains 32 times this stream`); the drainage-area ratio is
  reported, never enforced (SFARI's rule, 2026-09-07). NLCD over the HR reach
  watershed polygon answers a land-cover id only when neither engine has a
  value for it.
- Every desktop value carries a `basis` (`site-engine` | `streamcat` | `nlcd` |
  `3dep`), shown as a badge beside the Source row, printed in the CSV, the
  PDF, the GeoJSON (`predictor_source`, `watershed_basis`,
  `engine_values_withheld`), and the field-form packet (desktop values in the
  Value cell, `DESKTOP: <source>` in Notes, `reference only` when withheld).
- **Any NHD stream:** the map draws the high-resolution NHD once in one solid
  blue style. The Layers menu has **Streams** and an optional **StreamCat
  coverage** checkbox, initially off. Enabling it distinguishes **StreamCat
  reaches** in blue from **Other streams** in cyan (`deep/network_display.py`,
  EASI's split within 150 ft of an NHDPlus V2 reach), and reveals the source-reach
  highlight and downstream connector. The choice lasts for the current page
  session and only changes the display. The assessment point, reach, and
  watershed remain distinct. Engine names and downstream-source details stay
  on the source row, basis badge, and exports. The separate **Available
  assessments** panel continues to show assessment applicability.
  Every click, and every typed point, snaps to the HR line
  (`deep/hr_site.py`), the point lands at once, the StreamCat reach resolves
  in the background (`deep/comid_anchor.py`, the vendored engine's shared
  click rule), and Delineate stays disabled until the source resolves. A
  persistent status above it shows lookup, up to three automatic retries after
  pauses of 5, 10, and 15 seconds, and the source or failure. A small circle spins
  beside active lookup messages, including retry pauses; it stays stationary
  when reduced motion is preferred. **Retry StreamCat lookup** starts a fresh bounded
  cycle at the same snapped point. New analysis and metric computation require
  the current source. Imported results stay readable; a missing source is
  resolved before new analysis. Once ready, Delineate runs the STAF site
  engine for the HR reach watershed and the assessment reach at the length the
  assessor typed, at every site (usually
  under a minute, up to about five minutes on a large basin, refused past the
  interactive reach budget). If the engine fails and a valid source already
  exists, the assessor can continue
  with the StreamCat lookup engine (`pipeline.delineate_without_watershed`):
  no watershed is drawn, the basis is the StreamCat reach's NHDPlus V2 basin,
  and every watershed value says so.
- `deep/curves.py:ENGINE_PAIRING_MODE` is the switch the score-level
  equivalence study governs. It reported Outcome B on 2026-09-02 (rating
  agreement 0.84 pooled against a 0.90 bar, class agreement 0.97, median
  index shift 0.013), so the mode stays `refuse`: engine values never score
  against StreamCat-fitted curves, and engine-predictor versions of the
  pilot assessments are built in StreamCurves for them instead. `label`
  remains available should a later study pass the rule.
- Sessions carry `siteAnchor`, `siteEngine` (geometry stripped), and
  `watershedBasis` inside the delineation block; the schema version is
  unchanged.

## Where DEEP gets its assessments

`deep/config.py:_registry_records` merges three sources by `assessmentRef` (`id@vN`), and a
later source wins a ref:

1. **The baked registry**: `data/deep-assessments.json` and `www/calculators/`, written by
   `scripts/bake_library_into_deep.py` and shipped with every deploy. It stays the offline
   fallback: with the other two absent, DEEP serves exactly what was baked.
2. **The remote library** (`deep/remote_library.py`): the rolling `library` prerelease on
   USACE-WRISES/staf. DEEP reads its catalog `library.json` (schema 1) and downloads each
   Preliminary or Final version's `<id>-v<N>.deep.json` and optional
   `<id>-v<N>-calculator.xlsx`, so a version published after the deploy reaches a running DEEP
   without a redeploy. A version the catalog lists with any other status (draft, under review,
   revised, retired) is dropped from the baked records too.
3. **The local library** `apps/library/`, in dev and desktop runs where the folder is present.
   It always wins, and the remote catalog never drops one of its versions.

The remote library never makes a page wait. The first lookup loads the last good catalog and its
assets from the disk cache, with no network. A background thread refreshes once the snapshot is
older than the TTL, one refresh at a time, and downloads only the assets the cache lacks, each
checked against the catalog's size and sha256. Any failure (a 404 while a publish replaces
`library.json`, a network error, a checksum mismatch) keeps the last good snapshot and is
retried a minute later. A catalog whose schema is newer than 1 is never used. One lookup does
wait: a ref DEEP does not know (an `?assessment=id@N` link to a version published minutes ago)
starts a refresh, at most once every 30 seconds, and waits up to 10 seconds for it.

A remote-only version gets the calculator published beside it in the release, under the rule a
baked one follows: the workbook's content digest must equal the loaded bundle's. A baked workbook
for the same content comes first.

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEEP_REMOTE_LIBRARY` | on | `0`, `false` or `off` turns the remote library off (the test suite does; so can an offline dev run) |
| `DEEP_LIBRARY_URL` | `https://github.com/USACE-WRISES/staf/releases/download/library/` | an http(s) base ending in `/`, or a local folder holding the same files |
| `DEEP_LIBRARY_TTL_S` | `600` | seconds between refreshes |
| `DEEP_CACHE_DIR` | `<temp>/deep_remote_library` | the disk cache: the last good `library.json` and the verified assets |

## Field forms, metric list and Excel calculator

**Get Field Forms** on the worksheet opens a dialog copied from SFARI: a **Metrics** tab (every
metric of the chosen assessment with its status), a **Field forms preview** tab, and four
downloads (field forms PDF, metrics PDF, completed workbook, blank workbook).

- `deep/field_form.py` builds the field worksheet with reportlab from whatever bundle is loaded,
  so it always matches the assessment and version in use. Protocol lines come from the bundle's
  `methodContext`.
- `deep/calculator.py` serves the workbook StreamCurves generated at publish. The bake step
  copies it to `www/calculators/<id>@vN.xlsx` (and `<id>.xlsx` for the default version) with an
  `index.json`. A workbook is served only when its `contentDigest` equals the loaded bundle's,
  and the completed workbook is filled at zip level, so DEEP needs no spreadsheet library.
  `DEEP_CALCULATOR_DIR` points a local DEEP at another folder.
- `deep/reference_support.py` reads the bundle's `referenceSupport`, `criteriaBasis`,
  `stratifier` and `insufficientReferenceSupport`. The worksheet shows a "Scored against" line
  on every card, selects the curve set from the delineated slope or drainage area (marked
  auto, user can override), and shows withheld metrics as disabled cards.

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

## Run the app (dev)

Uses the shared repo-root `.venv` (see the monorepo README). From `apps/deep`:

```sh
..\..\.venv\Scripts\python.exe -m shiny run --port 8003 app.py
```

Then open <http://127.0.0.1:8003>. (`.claude/launch.json` encodes this.)

## Data model / contract

DEEP consumes the STAF metric-library schema. `build_deep_data.py` reads the
STAF site data (`staf/docs/assets/data`) and emits, into `data/`:

| File | Contents |
|------|----------|
| `deep-functions.json` | the 20 STAF functions (order injected) |
| `deep-outcome-mapping.json` | per-function Physical/Chemical/Biological D/i/- codes |
| `deep-assessments.json` | the predefined assessment registry, **reference curves inlined** |
| `bundles/<id>.deep.json` | each assessment as a standalone upload bundle |

An uploaded assessment is one object of the same shape as an entry in
`deep-assessments.json` (curves inlined), so predefined and uploaded load
through one path. Bundles carry a top-level `predictorSource` stamped by
StreamCurves at publish (with per-metric stamps inside `metricsByFunction`);
absent means `streamcat`. The pairing rule reads it at scoring time, so
everything the rule needs rides inside the bundle — cloud DEEP never reads
`apps/library/` at runtime.

Two boundary layers back the Region step's site line and the session and report
region stamp (`deep/geo.py`, exact boundary-inclusive point-in-polygon):
`data/ecoregions_l3.geojson` (EPA Level III ecoregions, copied from StreamCurves)
and `data/us_states.geojson.gz` (Census 1:500,000 cartographic state boundaries,
built by `scripts/build_us_states.py`, which downloads the source zip and writes
the gzipped layer). The earlier coarse state layer placed a Hanover NH site in
Vermont, so `tests/test_geo.py` pins near-border points.

## Scoring convention

Matches SFARI/EASI exactly so tiers are comparable: outcome weights
**Direct = 1.0, indirect = 0.10, none = 0**, function scale **0–15**,
`iOutcome = Σ(F·W)/Σ(15·W)`, ECI = mean of the three outcome sub-indices.
(Not the 0.25 / 0–10 variant in `staf/docs/tiered-approach.md`;
`config.validate()` guards against that drift.)

**Coverage restricts the claim.** A function that does not apply leaves both
numerator and denominator, which is correct NA handling. A function the
assessment has no basis to score is a different thing: it is unknown, and
because each sub-index is a *ratio*, dropping it would land a partial
assessment on the same 0–1 scale as a complete one and read as comparable.
Scoring the same site against a 20-function bundle and against one missing four
functions moved the index from 0.68 to 0.87, across a band boundary, on
coverage alone. So where anything is unassessed, `score_assessment` reports
**no point index** and instead gives `ecosystemConditionIndexBounds`, the
interval the index could occupy once those functions are allowed their full
0–15 range, and names a condition band only when the interval stays inside one.
An outcome with no *direct* contributor is reported as `None`, not 0.0, because
0.1-weighted indirect signal from other disciplines is not a measurement of
that outcome. `scoring.index_claim()` is that decision made once, and every
export carries it. `...OverScored` keeps the old arithmetic for the Excel
workbook's cells and the live entry progress; it is a running total, never a
claim.

## Build & test

Uses the Python 3.12 launcher (`py`) on this machine. Requires a sibling
`staf` checkout at `../staf` (or pass `--staf-data`).

```sh
py scripts/build_deep_data.py      # generate data/ from the STAF metric library
py scripts/build_us_states.py      # rebuild data/us_states.geojson.gz from the Census source
py -m pytest                       # run the test suite
```

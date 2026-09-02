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
  and reach geomorphic ratios ER / BHR / W:D (3DEP DEM cross-section + Bieger
  bankfull, via copied `deep/{geomorph,bieger}.py` + `deep/datasources/threedep.py`).
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
  registry: exact-watershed values (impervious, anthropogenic cover, and the
  engine reach for geomorphic ratios) computed at the assessed point on the
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

**Two watershed engines (2026-09):**

- **STAF site engine** (`deep/engine_prefill.py` over the vendored copy): the
  exact watershed at the clicked point on the full-resolution NHD. It is the
  watershed itself for any stream outside the NHDPlus V2 network, and the
  desktop value source for bundles fitted on engine predictors.
- **StreamCat lookup engine**: EPA StreamCat by NHDPlus V2 COMID, the desktop
  value source for bundles fitted on StreamCat predictors. On a stream outside
  V2 its values are labeled with the nearest covered reach they describe
  (`, describes the nearest covered reach, COMID x, ...`) and withheld past a
  10x drainage-area ratio, in which case NLCD runs over the exact polygon.
- Every desktop value carries a `basis` (`site-engine` | `streamcat` | `nlcd` |
  `3dep`), shown as a badge beside the Source row, printed in the CSV, the
  PDF, the GeoJSON (`predictor_source`, `watershed_basis`,
  `engine_values_withheld`), and the field-form packet (desktop values in the
  Value cell, `DESKTOP: <source>` in Notes, `reference only` when withheld).
- **Any NHD stream**: the map draws the V2 network (blue, clickable) over the
  full high-resolution NHD (light blue). An HR-only click is anchored to the
  nearest covered reach (`deep/hr_site.py`, the engine's shared
  classification) and Delineate computes the exact watershed and reach with
  the engine (usually well under a minute, up to about five minutes on a large basin, refused past the interactive reach budget); if
  the engine fails, the covered reach's V2 basin is offered behind a confirm,
  labeled as describing that reach. A covered site runs the engine in the
  background only when its values can enter scoring (an engine-built bundle,
  or `label` mode), so StreamCat bundles never pay the engine's minutes.
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

## Build & test

Uses the Python 3.12 launcher (`py`) on this machine. Requires a sibling
`staf` checkout at `../staf` (or pass `--staf-data`).

```sh
py scripts/build_deep_data.py      # generate data/ from the STAF metric library
py scripts/build_us_states.py      # rebuild data/us_states.geojson.gz from the Census source
py -m pytest                       # run the test suite
```

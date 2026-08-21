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

**Phase 2 — interactive app (done):**

- `app.py` — Shiny-for-Python app: **Identify → Basin → Region → Assessment →
  Report**. Identify/Basin + the map/delineation engine are reused from SFARI
  (`deep/{delineation,pipeline}.py`, `deep/datasources/`); the Assessment worksheet
  is curve-based (numeric value → live index + computed 0–15 function score).
- `deep/report.py` — CSV / GeoJSON / PDF exports. `deep/session.py` — save/resume
  (delineation + inlined assessment + measured values). `deep/measure.py` —
  measured-value assembly (Phase 3 will add desktop auto-compute here).
- `www/measure.js`, `www/deep.css` — worksheet interactions + styling.
- `examples/spring-sample.deep.json` — a synthetic SPRING-built assessment bundle
  for exercising the upload path until the real SPRING export exists.

**Phase 3 — desktop auto-compute (done):**

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
  and `referenceRange`. DEEP retains unknown fields, so older bundles still load.
- The assessment card and detail pane show the **reference tier** the curves were
  drawn at (least disturbed or best available); a best-available bar is never
  mistaken for reference condition.
- The metric information card lists the curve basis (tier, role, reference
  sites, builder confidence band) and the builder's caveats ("Read with care").
- Three scoring advisories compose beside a score (`deep/curves.py:metric_warning`):
  the endpoint clamp, a value outside the reference pool's observed range, and a
  thin-sample curve (`sampleDisposition` insufficient) that should be read as a
  band, not a point value. None changes the index.
- Curves may be two-sided (`curve.form: optimum`), including a flat-low-tail
  variant; `interp_curve` is shape-agnostic and scores them unchanged.
- Published regional assessments are preliminary until the scientific team
  certifies them; nothing in DEEP implies certification.

## Run the app (dev)

Phase 2 reuses EASI's virtualenv (it already has the shiny + HyRiver stack). From
the DEEP repo root:

```sh
D:/Code/Work/easi_claude/.venv/Scripts/python.exe -m shiny run --port 8011 app.py
```

Then open <http://127.0.0.1:8011>. (`.claude/launch.json` encodes this.) A
dedicated DEEP `.venv` from `requirements.txt` can replace the borrowed one later.

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
through one path.

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
py -m pytest                       # run the test suite
```

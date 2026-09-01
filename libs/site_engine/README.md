# STAF site engine

The **STAF site engine** (provenance token `site-engine`) is the second of the
two STAF watershed engines, beside the **StreamCat lookup engine** (token
`streamcat`). Both are defined, with the per-app policy and the gaps table, in
[`libs/README.md`](../README.md) and on the docs site's Computation Engines
page. This package is the site engine: given a point on any NHD High
Resolution stream, it delineates the TRUE contributing watershed at that exact
point and computes exact-watershed and reach-scale GIS metrics from source
data, with per-metric provenance and pinned data vintages.

- **Identity**: `ENGINE_ID = "site-engine"`, `ENGINE_VERSION` (0.2.0). Display
  names and label helpers live in `site_engine/naming.py`; every consuming app
  imports them from its vendored copy so the four apps share one vocabulary.
- **Entry point**: `compute_site(lat, lon, config=None, *, progress=None)`.
  Config keys: `reachLengthFt`, `snapTolFt`, `maxHops`, `maxReaches`,
  `includeGeometry`, `metricFamilies` (a subset of `landcover`, `roads`,
  `dams`, `soils`, `runoff`, `xsection`; None means all), `landcoverBaseline`
  (adds NLCD 2001 impervious cover). `provenance.DEFAULT_CONFIG` is the study
  budget; `provenance.INTERACTIVE_CONFIG` is the tighter budget a web app can
  wait for (about five minutes on a typical site), calibrated by
  `scripts/engine_runtime_profile.py`. `progress` is a callable that receives
  stage events (`{"stage", "hops", "reaches", "family"}`); events never enter
  the record.
- **Delineation**: NHDPlus HR catchment aggregation (the `NHDPlusCatchment`
  layer unioned over the upstream `dnhydroseq` tree), self-validated against
  the published HR `totdasqkm`. The walk is geometry-free (ids only, POST
  chunks); the tree flowlines for the riparian buffer are fetched once at the
  end. Past the budget the engine refuses with a reason, never a truncated
  watershed. Chosen over MMW and StreamStats by the spike study recorded in
  the coverage plan.
- **Metrics**: NLCD 2021 land cover on the watershed and the 100 m riparian
  buffer (2001 impervious optional); TIGERweb road density (primary, secondary
  and local layers); NID dams in the polygon (count, density, normal storage
  as the StreamCat `damnrmstor` analog, NID storage beside it); SSURGO K,
  area-weighted by SDA polygon intersection; EROM mean-annual flow and the
  derived runoff depth (labeled not equivalent to StreamCat `runoffws`); the
  3DEP cross-section through the synced EASI transect machinery.
- **Permanent exclusions**: the EPA modeled indices (hyd, sed, chem, conn,
  temp, habt, prG_BMMI) exist only as per-COMID model outputs on NHDPlus V2.
  They ride every record under `exclusions`, documented, never approximated.
- **Anchoring**: `site_engine/anchor.py` classifies any point the way EASI's
  routing does (V2 within tolerance, else the HR stream with its nearest
  covered downstream reach, routed distance and drainage-area ratio). It never
  refuses; a routing past the 10x bound is `declined` with a code, and the
  consumer decides what a declined routing withholds. Payload parity with
  EASI is tested.
- **Consumers**: EASI (the exact watershed on streams outside NHDPlus V2),
  SFARI (field-form prefill), StreamCurves (selectable predictor source),
  DEEP (auto-pull and the exact watershed on HR-only sites). Scoring against
  StreamCat-fitted curves follows the train/serve pairing rule until the
  score-level equivalence study (`scripts/score_equivalence_study.py`) settles
  it.
- **Vendoring**: each consuming app copies the package with its own
  `scripts/vendor_site_engine.py` into `<pkg>/_vendor/site_engine/` plus a
  drift-gate test. Apps never import `libs/` at runtime. After any change here,
  re-run every consumer's vendor script and commit the copies.
- **Extracted modules**: `site_engine/_extracted/` holds synced copies of the
  EASI transect machinery (`threedep`, `geomorph`, `bieger`) and the physio and
  ecoregion data files. EASI stays canonical; run
  `python scripts/sync_engine_extracts.py` after changing the sources, and the
  `tests/test_extracts_sync.py` gate fails on drift.
- **Determinism**: same inputs + same engine version = same record. The record
  body carries no timestamps; stamp them outside if needed.

## Versioning

Minor bump when record keys, config keys, vintages, or metric definitions
change (consumers record `engineVersion` in their provenance and DEEP bundles
carry `site-engine vX`); patch bump when records stay byte-identical.

0.2.0: `naming` and `anchor` modules; `metricFamilies` and `landcoverBaseline`
config; progress callback; geometry-free POST walk with `INTERACTIVE_CONFIG`;
area-weighted soil K; dam metrics on normal storage plus NID storage and dam
density; lazy `compute_site` import.

## Studies

`scripts/delineation_acceptance.py` (union area vs published area),
`scripts/covered_reach_comparison.py` (engine vs StreamCat at covered reaches,
riparian pairs and a dammed subset), `scripts/engine_runtime_profile.py`
(stage timings and the interactive budget), and
`scripts/score_equivalence_study.py` (StreamCat inputs vs engine inputs
through EASI scoring and the DEEP curves). Outputs go to the gitignored
`scripts/out/`; summaries live under `notes/EASI_Report/analysis/`.

Run the tests from this directory: `python -m pytest`.

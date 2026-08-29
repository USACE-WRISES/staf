# STAF Site Computation Engine

A shared, versioned engine for the higher STAF assessment tiers: given a point
on any NHD High Resolution stream, it delineates the TRUE contributing
watershed at that exact point and computes exact-watershed and reach-scale GIS
metrics from source data, with per-metric provenance and pinned data vintages.

- **Engine id**: `site-engine` (the string recorded in provenance and in DEEP
  bundle `predictorSource` fields). **Version**: `site_engine.ENGINE_VERSION`.
- **Delineation**: NHDPlus HR catchment aggregation (the `NHDPlusCatchment`
  layer unioned over the upstream `dnhydroseq` tree), self-validated against
  the published HR `totdasqkm`. Chosen over MMW and StreamStats by the spike
  study recorded in the coverage plan: snapped MMW reproduces the NHDPlus V2
  basin (wrong network for HR-anchored sites) and unsnapped MMW fails on grid
  mismatch, the same failure class as the previously rejected NLDI
  split-catchment.
- **Consumers**: SFARI (field-form prefill), StreamCurves (selectable
  predictor source), DEEP (automatic metric pulling). EASI does NOT use this
  engine; its scoring stays on published EPA StreamCat.
- **Train/serve rule**: engine-computed values must never score against curves
  fitted on StreamCat predictors. DEEP enforces this via the bundle
  `predictorSource` field.
- **Vendoring**: each consuming app copies the package with its own
  `scripts/vendor_site_engine.py` into `<pkg>/_vendor/site_engine/` plus a
  drift-gate test, following the StreamCurves-vendors-EASI precedent. Apps
  never import `libs/` at runtime.
- **Extracted modules**: `site_engine/_extracted/` holds synced copies of the
  EASI transect machinery (`threedep`, `geomorph`, `bieger`) and the physio /
  ecoregion data files. EASI stays canonical; run
  `python scripts/sync_engine_extracts.py` after changing the sources, and the
  `tests/test_extracts_sync.py` gate fails on drift.
- **Determinism**: same inputs + same engine version = same record. The record
  body carries no timestamps; stamp them outside if needed.

Run the tests from this directory: `python -m pytest`.

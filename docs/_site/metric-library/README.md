# Metric Library

## Overview
The Metric Library is a shared dataset and UI that powers metric browsing across Screening, Rapid, and Detailed tiers. It stores metric definitions, scoring profiles, and reference curves in a single library that can be used by any assessment page.

## Folder layout
```
docs/assets/data/metric-library/
  index.json
  rating-scales.json
  metrics/
    <metricId>.json
  curves/
    <curveSetId>.json
```

## Adding a new metric
1. Create a metric detail file in `docs/assets/data/metric-library/metrics/`.
2. Add one or more scoring profiles in the `profiles` array.
3. If the profile uses curves, create a curve set file in `docs/assets/data/metric-library/curves/` and reference it via `curveIntegration.curveSetRefs`.
4. Rebuild the index:
```
npm run build:metric-index
```

## Adding a new scoring profile (tier variant)
- Add a new object to `profiles` with:
  - `profileId`, `tier`, `status`
  - `scoring` definition
  - `curveIntegration` (enabled + curveSetRefs)
- Rebuild the index so availability and summaries update.

## Scoring types (summary)
- `categorical`: rubric levels (e.g., Optimal/Suboptimal/Marginal/Poor)
- `thresholds`: numeric bands mapped to rating levels
- `curve`: scoring derived from reference curves
- `formula`: expression + variable mapping
- `binary` / `lookup`: minimal support for yes/no or lookup tables

## Curves and counts
- A profile declares curves via `curveIntegration.curveSetRefs`.
- The Metric Library UI counts curves by tier using curve set references.
- If `curveIntegration.enabled = true` but there are no curve set refs, the UI warns and disables Add.

## Rebuild the index
```
npm run build:metric-index
```

## Migrate legacy screening metrics
```
npm run migrate:screening-metrics
```
See `docs/metric-library/MIGRATION.md` for mapping assumptions.


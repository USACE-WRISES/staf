# Migration notes: Metric library

## Current recommended approach
Use the CSV-first compiler:

- Source CSV: `docs/assets/data/metric-library/Metric Library Complete *.csv`
- Build command:
```bash
npm run build:metric-library
```
- Validation:
```bash
npm test
```

This regenerates canonical JSON metric-library files and tier TSV outputs used by widgets.

## Legacy screening migration (reference only)
The older screening-only migration path is retained for backwards compatibility.

### Source
- `docs/assets/data/screening-metrics.tsv`

### Mapping assumptions
- Each TSV row becomes one metric detail JSON in `docs/assets/data/metric-library/metrics/`.
- `metricId` is derived from `Function + Metric` (slugified); duplicates are suffixed.
- The screening profile is created for every metric:
  - `profileId`: `screening-default`
  - `tier`: `screening`
  - `scoring.type`: `categorical`
  - `ratingScaleId`: `fourBand`
  - rubric levels map to Optimal/Suboptimal/Marginal/Poor columns
- A curve set is generated per metric with a single qualitative curve named `Screening`.
- Index values use the standard thresholds: 1.00, 0.69, 0.30, 0.00.

### Script
```bash
npm run migrate:screening-metrics
```

### Notes
- References are split on semicolons.
- Context/Method fields are concatenated into `methodContextMarkdown`.
- This script overwrites any metric files that share the same `metricId`.
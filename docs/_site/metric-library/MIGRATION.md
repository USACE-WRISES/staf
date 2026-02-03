# Migration notes: Screening metrics

## Source
- `docs/assets/data/screening-metrics.tsv`

## Mapping assumptions
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

## Script
```
npm run migrate:screening-metrics
```

## Notes
- References are split on semicolons.
- Context/Method fields are concatenated into `methodContextMarkdown`.
- This script overwrites any metric files that share the same `metricId`.


# Metric Library

## Overview
The metric library is the canonical dataset used by Screening, Rapid, and Detailed widgets.
It stores metric definitions, profile scoring rules, and reference curves in one place.

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

## Primary workflow (recommended)
Use the source CSV as the single input and regenerate all metric-library outputs.

1. Update CSV in:
   - `docs/assets/data/metric-library/Metric Library Complete *.csv`
2. Build library:
```bash
npm run build:metric-library
```
3. Validate:
```bash
npm test
```

The build command runs `scripts/compileMetricLibraryFromCsv.ts` and regenerates:
- Canonical metric library JSON (`index.json`, `metrics/*.json`, `curves/*.json`)
- Tier files (`screening-metrics.tsv`, `rapid-indicators.tsv`, `rapid-criteria.tsv`, `detailed-metrics.tsv`)
- Source-rich TSV variants and `_site` mirrors.

## Alternative run options
Run the compiler directly:
```bash
npx ts-node scripts/compileMetricLibraryFromCsv.ts
```

Use a specific CSV file:
```powershell
$env:METRIC_LIBRARY_CSV_PATH = "docs/assets/data/metric-library/Metric Library Complete 2026-02-10.csv"
npm run build:metric-library
```

## Metric Library download (XLSX)
The in-app **Metric Library download** button exports an Excel workbook with:
- `Metrics` tab
- `Reference Curves` tab

That workbook is generated in the browser from canonical JSON metric-library files (`index.json`, `metrics/*.json`, `curves/*.json`).
It is not rebuilt from TSV files.

## Manual JSON edits (advanced)
If you edit `metrics/*.json` or `curves/*.json` directly, rebuild only the index with:
```bash
npm run build:metric-index
```

## Legacy migration scripts
Older migration scripts remain available:
- `npm run migrate:screening-metrics`
- `npm run migrate:detailed-metrics`

Use them only for legacy conversions; prefer the CSV-first workflow above.
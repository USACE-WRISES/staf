# Stream Tiered Assessment Framework

This repository hosts the documentation site for the Tiered Stream Assessment Approach. The site is built with GitHub Pages using Jekyll and the just-the-docs theme, with content in Markdown and lightweight JS widgets.

## Structure
- `docs/`: GitHub Pages site source 
- `docs/assets/`: CSS, JavaScript, and data files
- `docs/_includes/`: HTML partials for widgets

## Local preview (optional)
If you want to preview locally, you can run Jekyll from the `docs/` folder:

```bash
bundle install
cd docs/
bundle exec jekyll serve
bundle exec jekyll serve --livereload
http://127.0.0.1:4000/staf/
```

If you do not have Jekyll installed, you can rely on GitHub Pages to build the site.

## Configuration notes
- Update `_config.yml` with your GitHub repository URL for the edit links.
- If your site is hosted at a subpath, set `baseurl` in `_config.yml`.

## Data files
Each data file is JSON format and feeds one or more widgets. Field definitions are also documented in `docs/contribute/data-dictionary.md`.

- `docs/assets/data/functions.json`
  - Purpose: list of stream functions and example metrics by tier.
  - Fields: `id`, `category`, `name`, `short_description`, `long_description`, `example_metrics`.
- `docs/assets/data/cwa-mapping.json`
  - Purpose: maps function ids to Clean Water Act outcomes.
  - Fields: `physical`, `chemical`, `biological` values are `D`, `i`, or `-`.
- `docs/assets/data/tier-questions.json`
  - Purpose: drives the tier selector questionnaire and scoring.
  - Fields: `id`, `question`, `answers` with `value`, `label`, `score_screening`, `score_rapid`, `score_detailed`, `rationale_snippet`.
- `docs/assets/data/scoring-example.json`
  - Purpose: starter sample scores used by the scoring sandbox.
  - Fields: `function_id`, `score`.

## Contributing
See `docs/contribute/index.md` for the contribution workflow and content style guidelines.

## Metric library build workflow
The metric library is generated from the source CSV file:

- Source CSV location: `docs/assets/data/metric-library/Metric Library Complete *.csv`
- Generator script: `scripts/compileMetricLibraryFromCsv.ts`
- Package command:

```bash
npm run build:metric-library
```

You can also run the script directly (equivalent behavior):

```bash
npx ts-node scripts/compileMetricLibraryFromCsv.ts
```

Optional: specify an explicit CSV path:

```bash
# PowerShell
$env:METRIC_LIBRARY_CSV_PATH = "docs/assets/data/metric-library/Metric Library Complete 2026-02-10.csv"
npm run build:metric-library
```

After a build, run:

```bash
npm test
```

Generated outputs include:
- Canonical JSON metric library (`docs/assets/data/metric-library/index.json`, `metrics/*.json`, `curves/*.json`)
- Tier datasets (`screening-metrics.tsv`, `rapid-indicators.tsv`, `rapid-criteria.tsv`, `detailed-metrics.tsv`)
- Mirrored `_site` copies for local/docs rendering.

### Metric library download (XLSX)
The in-app **Metric Library download** (left sidebar button) exports an `.xlsx` with:
- Sheet 1: `Metrics`
- Sheet 2: `Reference Curves`

This workbook is built at runtime from the canonical JSON metric library (`index.json` + metric detail JSON + curve-set JSON), not by rebuilding from TSV files.
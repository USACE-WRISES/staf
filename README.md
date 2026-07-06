# Stream Tiered Assessment Framework (STAF)

Monorepo for the STAF documentation site and the four Shiny-for-Python assessment apps.

| Part | Path | Where it runs |
|---|---|---|
| Documentation site & app portal | `docs/` | [usace-wrises.github.io/staf](https://usace-wrises.github.io/staf/) (GitHub Pages) |
| EASI — Screening tier | `apps/easi` | [gtmenichino-easi.share.connect.posit.cloud](https://gtmenichino-easi.share.connect.posit.cloud/) |
| SFARI — Rapid tier | `apps/sfari` | [gtmenichino-sfari.share.connect.posit.cloud](https://gtmenichino-sfari.share.connect.posit.cloud/) |
| DEEP — Detailed tier | `apps/deep` | [gtmenichino-deep.share.connect.posit.cloud](https://gtmenichino-deep.share.connect.posit.cloud/) |
| stream-curves — curve builder for DEEP | `apps/stream-curves` | [gtmenichino-stream-curves.share.connect.posit.cloud](https://gtmenichino-stream-curves.share.connect.posit.cloud/) |

## Repository layout

- `docs/` — Jekyll site source (GitHub Pages builds this folder; just-the-docs remote theme). The Tools page (`docs/tools/`) is the launch portal for the four apps; app URLs live in `docs/_data/apps.yml`.
- `apps/` — the four Shiny for Python apps. Each folder is self-contained (own `requirements.txt`, `www/`, `data/`, tests, and Posit Publisher config) and deploys to its own Posit Connect Cloud content item.
- `libs/` — reserved for `staf-core`, the future shared package for code duplicated across the apps.
- `scripts/`, `src/` — TypeScript build pipeline for the metric library (see below).
- `notes/` — internal working notes; anything outside `docs/` is not published.

## Working on the apps

One shared virtual environment at the repo root covers all four apps (Python 3.12):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

Run an app locally (each gets its own port):

```powershell
cd apps\easi          # or apps\sfari, apps\deep, apps\stream-curves
shiny run app.py --port 8000
```

Run tests **per app, from the app's own directory** (the four suites cannot run together from the repo root):

```powershell
cd apps\easi;          python -m pytest
cd apps\sfari;         python -m pytest
cd apps\deep;          python -m pytest
cd apps\stream-curves; python -m pytest -m "not live"
```

## Deploying an app

Each app deploys **separately** with the Posit Publisher extension (VS Code / Positron) from its `apps/<app>` folder:

- The tracked `.posit/publish/<name>.toml` is the deploy configuration (entrypoint, files, Python version).
- The untracked `.posit/publish/deployments/*.toml` records bind redeploys to the **existing** Connect Cloud content item — they are what keep the public app URLs stable. Never delete or commit them; back them up if you move machines.
- Before deploying, confirm Publisher targets the existing deployment rather than creating a new one.

App URLs are listed in `docs/_data/apps.yml` (used by the site) and in each app's `staf_topnav` (the in-app cross-navigation). Until a shared `staf-core` package exists, a URL change must be mirrored in both places.

## The documentation site

Local preview:

```bash
cd docs
bundle install          # first time
bundle exec jekyll serve
# http://127.0.0.1:4000/staf/
```

GitHub Pages builds the site automatically from `docs/` on every push to `main`. `docs/_site/` is local build output and is not tracked.

### Data files

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

### Metric library build workflow

The metric library is generated from the source CSV file:

- Source CSV location: `docs/assets/data/metric-library/Metric Library Complete *.csv`
- Generator script: `scripts/compileMetricLibraryFromCsv.ts`
- Package command:

```bash
npm run build:metric-library
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

### Metric library download (XLSX)

The Tools-page **Metric Toolbox** button exports an `.xlsx` with:
- Sheet 1: `Metrics`
- Sheet 2: `Reference Curves`

This workbook is built at runtime from the canonical JSON metric library (`index.json` + metric detail JSON + curve-set JSON), not by rebuilding from TSV files.

## Contributing

See `docs/contribute/index.md` for the contribution workflow and content style guidelines.

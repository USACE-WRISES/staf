# Stream Tiered Assessment Framework (STAF)

Monorepo for the STAF documentation site and its STAF web app, the four Shiny-for-Python assessment
apps, and StreamCurves Desktop, the Windows app in which StreamCurves runs.

| Part | Path | Where it runs |
|---|---|---|
| Documentation site & Apply STAF page | `docs/` | [usace-wrises.github.io/staf](https://usace-wrises.github.io/staf/) (GitHub Pages) |
| STAF web app: EASI, SFARI and DEEP in the browser | `apps/staf-web` (built into `docs/app/`) | [usace-wrises.github.io/staf/app](https://usace-wrises.github.io/staf/app/) (GitHub Pages), the web app the site links to; see `apps/staf-web/README.md` |
| EASI: Screening tier | `apps/easi` | Ported to the STAF web app ([`?tool=easi`](https://usace-wrises.github.io/staf/app/?tool=easi)); the Shiny app runs inside STAF and on its own for development |
| SFARI: Rapid tier | `apps/sfari` | Ported to the STAF web app ([`?tool=sfari`](https://usace-wrises.github.io/staf/app/?tool=sfari)); the Shiny app runs inside STAF and on its own for development |
| DEEP: Detailed tier | `apps/deep` | Ported to the STAF web app ([`?tool=deep`](https://usace-wrises.github.io/staf/app/?tool=deep)); the Shiny app runs inside STAF and on its own for development |
| StreamCurves: curve builder for DEEP | `apps/stream-curves` + `desktop/` | StreamCurves Desktop, a Windows app (installer and portable zip on the [latest release](https://github.com/USACE-WRISES/staf/releases/latest)) |
| STAF: EASI, SFARI and DEEP in one Shiny app | `apps/staf` | [gtmenichino-staf.share.connect.posit.cloud](https://gtmenichino-staf.share.connect.posit.cloud/) on Posit Connect Cloud, unlinked since 2026-10-18 and to be retired; see `apps/staf/README.md` |

## Repository layout

- `docs/`: Jekyll site source (GitHub Pages builds this folder; just-the-docs remote theme). The Apply STAF page (`docs/tools/`) offers each tier two ways: its web application (in the STAF web app) and its spreadsheet calculator, downloaded raw from `main` so always the latest. Tool URLs and calculator paths live in `docs/_data/apps.yml`; DEEP's per-ecoregion list is generated into `docs/_data/deep_calculators.json`.
- `apps/`: the four Shiny for Python apps. Each folder is self-contained (own `requirements.txt`, `www/`, `data/`, tests). EASI, SFARI and DEEP deploy together inside the STAF app (`apps/staf`); each tool's `.posit/publish/*.toml` is the file list STAF's assembler copies, and each still runs on its own for development. StreamCurves ships inside StreamCurves Desktop.
- `apps/library/`: the shared, version-controlled **STAF assessment library** of completed detailed assessments that StreamCurves publishes and DEEP runs (draft, preliminary and final versions, each labeled). CI also publishes it as the rolling `library` prerelease, which StreamCurves Desktop's Assessment library and DEEP read. See `apps/library/README.md` and "The assessment library" below.
- `desktop/`: StreamCurves Desktop, a C#/.NET 10 WebView2 shell, modeled on HYPE Desktop, that runs StreamCurves on a self-managed Python runtime (downloaded on first run, auto-updated from this repo's GitHub Releases). `dotnet test desktop\StreamCurves.Desktop.slnx` runs its suite; launching a dev build from a checkout runs the app from the repo `.venv`. Release model: `desktop/RELEASING.md`.
- `libs/`: shared packages consumed by the apps via per-app vendored copies (never imported across app folders at runtime). `libs/site_engine` is the **STAF site engine**: HR reach watershed delineation on the full-resolution NHD (the drainage area of the reach a point snaps to) plus watershed metrics computed from source data. The other watershed engine is the **StreamCat lookup engine** (EPA StreamCat by NHDPlus V2 COMID). EASI uses the lookup engine on covered streams and the site engine on any other NHD stream; SFARI and DEEP use the site engine first with the lookup engine as a labeled fallback; StreamCurves offers the site engine as its one selectable predictor source. Definitions, per-app policy, and the vendoring rule: `libs/README.md` and the site's Computation Engines page.
- `scripts/`, `src/`: TypeScript build pipeline for the metric library (see below).
- `notes/`: internal working notes; anything outside `docs/` is not published.

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

## Deploying the web apps

The site's web app is the STAF web app on GitHub Pages (https://usace-wrises.github.io/staf/app/, `?tool=easi|sfari|deep`). `npm run build` in `apps/staf-web` writes it into `docs/app/`, and pushing `main` publishes it with the rest of the site. See `apps/staf-web/README.md`.

The Shiny apps EASI, SFARI and DEEP deploy together as one Posit Connect Cloud item, the STAF app (`apps/staf`, https://gtmenichino-staf.share.connect.posit.cloud/, `?tool=easi|sfari|deep`). The site stopped linking to it on 2026-10-18, and it stays up until it is retired. Their standalone items were retired on 2026-10-08; never deploy a tool on its own. Until the STAF app is retired, a redeploy goes like this:

- Run `apps/staf/scripts/assemble_tools.py` before every deploy. It copies each tool's files, as listed in its tracked `.posit/publish/<name>.toml`, into the gitignored `apps/staf/_tools/` (`apps/staf/tests/test_assembled.py` checks it).
- Deploy with the Posit Publisher extension (VS Code / Positron) from `apps/staf` opened as its own window, using the tracked `staf` configuration.
- The untracked `apps/staf/.posit/publish/deployments/*.toml` record binds redeploys to the **existing** content item and keeps the public URL stable. Never delete or commit it; back it up if you move machines. Before deploying, confirm Publisher targets the existing deployment rather than creating a new one.
- Runbook: `apps/staf/README.md`.

StreamCurves is not deployed to Posit. It ships as StreamCurves Desktop through this repository's GitHub Releases: a `streamcurves-vX.Y.Z` tag publishes the installer, and an app update is a payload run. See `desktop/RELEASING.md`.

App URLs are listed in `docs/_data/apps.yml` (used by the site) and in each app's `STAF_LINKS` dict (used for the STAF link in each app's header and StreamCurves' DEEP links, which open DEEP in the STAF web app through its `staf` entry; installed StreamCurves Desktop copies get a change with the next payload release). StreamCurves' entry is its latest release page. A URL change must be mirrored in both places; each tool's `tests/test_site_links.py` (DEEP: `test_site_calculators.py`) checks it.

## The assessment library

`apps/library/` is the shared home for **completed detailed assessments** (reference-curve sets built in StreamCurves, run by DEEP). It is version-controlled: each assessment keeps every published version under `assessments/<id>/vN/`, each version Draft, Preliminary or Final. DEEP runs all three, labeled (gray, amber and blue on its map), unless a version's Show in DEEP is off, and opens the newest Final version by default, else the newest Preliminary one, else the newest Draft; StreamCurves' Assessment library lists them all.

The path from a revision to a listed version:

1. **Revise**: anyone opens a version from the Assessment library on StreamCurves Desktop's start page. It downloads as a project of their own; they revise it, then use **Save a copy for the maintainer** on the Publish step and send the `.streamcurves` file.
2. **Publish**: the maintainer opens that file in a STAF checkout with `STAF_LIBRARY_PUBLISH=1` and `STAF_LIBRARY_MAINTAINER` set, and publishes it as the next version (Draft by default, or Preliminary; Show in DEEP on unless turned off). StreamCurves writes `apps/library/`, then runs `apps/deep/scripts/bake_library_into_deep.py` to fold the latest into DEEP's baked registry (`apps/deep/data/deep-assessments.json`). Validate's **Approve as Preliminary**, **Certify as Final** and **Show in DEEP** move a version on later.
3. **Ship**: commit `apps/library/**`, `apps/deep/data/**`, `apps/deep/www/calculators/**` and `docs/_data/deep_calculators.json` (the bake rewrites the site's calculator list too), then push `main`. The `library-release` workflow refreshes the rolling `library` prerelease, so installed StreamCurves copies list the new version and the cloud DEEP picks up a preliminary or final version within 10 minutes, without a redeploy. The baked registry stays DEEP's offline fallback; assemble and redeploy STAF when it should carry the version too.

DEEP lists each library assessment on its assessment step (with region + version + last-updated). To test before publishing, download the `.deep.json` from StreamCurves' Publish step and upload it on DEEP's assessment step; DEEP also accepts `?assessment=<id>` (the default version) and `?assessment=<id>@<N>` (version N) links. The library's release model: `desktop/RELEASING.md`.

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
- The Metric Toolbox workbook (`docs/assets/data/metric-library/STAF_Metric_Toolbox.xlsx`)

### Metric library download (XLSX)

The Apply STAF page's **Metric Toolbox (Excel)** link opens `STAF_Metric_Toolbox.xlsx` in its own tab. The workbook has:
- Sheet 1: `Metrics`
- Sheet 2: `Reference Curves`

`scripts/buildMetricToolbox.ts` builds it from the canonical JSON metric library (`index.json` + metric detail JSON + curve-set JSON) at the end of `npm run build:metric-library`, and rewrites it only when its contents change. To rebuild only the workbook:

```bash
npm run build:metric-toolbox
```

`npm test` fails when the committed workbook no longer matches the library.

## Contributing

See `docs/contribute/index.md` for the contribution workflow and content style guidelines.

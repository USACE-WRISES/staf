# CLAUDE.md — Developer & Agent Onboarding

> See also: `README.md` (overview, per-app commands, data-file field docs) and `AGENTS.md` (working rules).

## What Is STAF?

STAF (Stream Tiered Assessment Framework) is a monorepo with two kinds of deliverables:

1. **Documentation site** (`docs/`): Jekyll + [just-the-docs](https://just-the-docs.com/) remote theme on GitHub Pages, vanilla-JS widgets (IIFE-wrapped), and a TypeScript/Node build pipeline that compiles a source CSV into the metric library (validated with Zod).
2. **Four Shiny for Python apps** (`apps/`), each deployed to its own Posit Connect Cloud content item:

| App | Tier | Purpose |
|---|---|---|
| `apps/easi` | Screening | Automated desktop screening (click a site → delineate → 20 metrics → condition score) |
| `apps/sfari` | Rapid | Function-based rapid field assessment with desktop evidence support |
| `apps/deep` | Detailed | Runs curve-based detailed assessments (predefined or uploaded `.deep.json` bundles) |
| `apps/stream-curves` | Detailed (builder) | Builds reference/regional curves and exports `.deep.json` assessment bundles for DEEP |

The site's Tools page is the app launch portal; app URLs live in `docs/_data/apps.yml` **and** in each app's `staf_topnav` — a URL change must be mirrored in both until a shared `staf-core` package exists (planned home: `libs/`).

## Codebase Structure

```
docs/                              Jekyll site source (GitHub Pages builds this folder)
docs/assets/js/                    Widget JS (vanilla, IIFE-wrapped)
docs/assets/data/                  JSON/TSV data consumed by widgets
docs/assets/data/metric-library/   Generated metric library (index.json, metrics/, curves/, rating-scales.json)
docs/_includes/                    HTML partials (page chrome, apps hub, widgets)
docs/_data/apps.yml                Canonical app names/URLs for the portal

apps/easi | sfari | deep | stream-curves    Shiny for Python apps (own requirements.txt,
                                            www/, data/, tests/, .posit/publish config)
libs/                              Reserved for the future staf-core shared package
scripts/                           TS build scripts (compileMetricLibraryFromCsv, buildMetricIndex, tests)
src/lib/metricLibrary/             TS types, Zod schemas, data loaders
notes/                             Internal dev notes — never published (outside docs/)
```

## Working Commands

```bash
# Site
npm install                        # Node deps for the build pipeline
npm run build:metric-library       # Compile source CSV → JSON/TSV outputs
npm test                           # Validate metric library against Zod schemas
cd docs && bundle exec jekyll serve   # http://127.0.0.1:4000/staf/

# Apps (one shared root venv, Python 3.12)
py -3.12 -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt
cd apps\easi && shiny run app.py --port 8000     # sfari:8001 deep:8003 stream-curves:8012
```

## Deployment

- **Site**: pushed to `main` → GitHub Pages rebuilds from `docs/` automatically. Nothing to deploy manually.
- **Apps**: one repo, four separate deployments. Deploy with Posit Publisher (VS Code/Positron) from the app's folder. The tracked `.posit/publish/<name>.toml` is the config; the **untracked** `.posit/publish/deployments/*.toml` records tie redeploys to the existing Connect Cloud content item and keep the public URLs stable. Always confirm Publisher targets the existing deployment, never a new one.

## Coding Conventions

- **Types**: `PascalCase` · **Functions/variables**: `camelCase` (TS/JS), `snake_case` (Python)
- **JSON fields**: `snake_case` (site data) — app bundle formats like `.deep.json` use `camelCase` keys; match whatever the consumer expects
- **CSS classes**: `kebab-case`
- **Site frontend**: vanilla JS, IIFE enclosures, `window.STAFMetricLibraryStore` singleton, `localStorage`, event-driven; ES modules in the TS pipeline
- **Apps**: Shiny for Python core syntax (not Express); domain logic in the app's package (`easi/`, `sfari/`, `deep/`, `streamcurves/`+`views/`), thin `app.py`

## Guardrails

1. **Never manually edit generated outputs** — run `npm run build:metric-library` instead
2. **Update cache-bust versions** when still-referenced JS/CSS assets change (in the relevant `_includes/` file)
3. **Never commit or write to `docs/_site/`** — untracked Jekyll build output; the pipeline writes only under `docs/assets/data/`
4. **Run pytest per app from that app's directory** — the four suites have colliding module names and per-app `conftest.py`; running from the repo root breaks
5. **Never delete or commit `.posit/publish/deployments/`** — those untracked records are what keep the four public app URLs stable across redeploys
6. **Use surgical edits** — prefer small, targeted changes over broad refactors
7. **Validate after changes** — site: `npm test` + jekyll build; apps: the affected app's pytest suite

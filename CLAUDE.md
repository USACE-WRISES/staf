# CLAUDE.md — Developer & Agent Onboarding

> See also: `README.md` (user-facing overview, data-file field docs).

## What Is STAF?

STAF (Stream Tiered Assessment Framework) is a GitHub Pages/Jekyll documentation site with interactive assessment tools for tiered stream health evaluation. The tech stack is:

- **Site**: Jekyll + [just-the-docs](https://just-the-docs.com/) theme, Markdown content
- **Widgets**: vanilla JavaScript (IIFE-wrapped, no framework)
- **Build pipeline**: TypeScript/Node.js scripts that transform a source CSV into JSON/TSV outputs
- **Schema validation**: Zod schemas enforce metric data integrity

## Project Goals & Key Components

| Component | Purpose |
|---|---|
| Documentation pages | Reference material for the tiered assessment approach |
| Tier selector widget | Questionnaire that recommends a screening/rapid/detailed tier |
| Assessment widgets | Screening, rapid, and detailed assessment tools |
| Metric library workbench | Browse, filter, and export the full metric library |
| Scoring sandbox | Experiment with example function scores |
| Functions explorer | Explore stream functions and CWA mappings |
| Build pipeline | `CSV → JSON/TSV` transformation for the metric library |
| Zod schemas | Validate generated metric data at build and test time |

## Working Commands

```bash
npm install                        # Install Node dependencies
cd docs && bundle install          # Install Jekyll/Ruby dependencies

npm run build:metric-library       # Compile source CSV → JSON/TSV outputs
npm test                           # Validate metric library against Zod schemas

cd docs && bundle exec jekyll serve          # Local preview at http://127.0.0.1:4000/staf/
cd docs && bundle exec jekyll serve --livereload  # Live-reload variant

node --check <file>                # Syntax-check a JS file
```

## Codebase Structure

```
docs/                              Jekyll site source (Markdown, config, layouts)
docs/assets/js/                    Interactive widget JS (vanilla, IIFE-wrapped)
docs/assets/data/                  JSON/TSV data files consumed by widgets
docs/assets/data/metric-library/   Generated metric library:
  ├── index.json                     master index
  ├── metrics/*.json                 per-metric detail
  ├── curves/*.json                  reference curve sets
  └── rating-scales.json             rating scale definitions
docs/_includes/                    HTML partials for widget components
docs/_site/                        Mirrored build artifacts (keep in sync with docs/)

src/lib/metricLibrary/             TypeScript source:
  ├── types.ts                       type definitions
  ├── schemas.ts                     Zod validation schemas
  └── data.ts                        data loaders

scripts/                           Build & migration scripts (TypeScript):
  ├── compileMetricLibraryFromCsv.ts   CSV → JSON/TSV generator
  ├── buildMetricIndex.ts              index builder
  ├── runMetricLibraryTests.ts         schema validation tests
  ├── migrateScreeningMetrics.ts       one-time migration
  └── migrateDetailedMetrics.ts        one-time migration
```

## Coding Conventions

- **Types**: `PascalCase` (e.g., `MetricDetail`)
- **Functions/variables**: `camelCase`
- **JSON fields**: `snake_case` (e.g., `short_description`)
- **CSS classes**: `kebab-case` (e.g., `metric-card`)
- **Frontend pattern**: vanilla JS with IIFE enclosures, `window.STAFMetricLibraryStore` singleton for shared state, `localStorage` persistence, event-driven architecture
- **Module type**: the project uses ES modules (`"type": "module"` in package.json)

## Guardrails

These complement the detailed rules in `AGENTS.md`:

1. **Never manually edit generated outputs** — run `npm run build:metric-library` instead
2. **Update cache-bust versions** when JS or CSS assets change (in the relevant `_includes/` file)
3. **Keep `docs/` and `_site/` mirrors in sync** — the repo expects mirrored copies of data assets
4. **Use surgical edits** — prefer small, targeted changes over broad refactors
5. **Validate after changes** — run the applicable commands from the AGENTS.md validation checklist

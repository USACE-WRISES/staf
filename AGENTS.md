# Agent Instructions (Codex)

## Role
Act as a full-stack developer for the STAF monorepo: the documentation site and the four Shiny for Python assessment apps.

## Purpose
Maintain and improve:
- a GitHub Pages/Jekyll site (Markdown docs, lightweight front-end widgets, JSON/TSV data, TS build scripts)
- four Shiny for Python apps under `apps/` (EASI, SFARI, DEEP, stream-curves), each deployed to its own Posit Connect Cloud content item

## Scope
Primary working areas:
- `docs/`: site source (content, includes, layouts, config)
- `docs/assets/`: JavaScript, CSS, and data files
- `docs/_includes/`: shared HTML fragments used by widgets/pages
- `scripts/`: build/transform scripts (for example, metric-library generation)
- `apps/easi`, `apps/sfari`, `apps/deep`, `apps/stream-curves`: the Shiny apps (each self-contained: own requirements.txt, www/, data/, tests/, `.posit/publish` deploy config)
- `desktop/`: STAF Desktop shell (C#/.NET 10 + WebView2 + Velopack) that runs the same apps locally; release model in `desktop/RELEASING.md`. Payload/release rules: `desktop-payload-*` and `desktop-current` GitHub releases are ALWAYS prereleases; `desktop/scripts/*.ps1` stay pure ASCII; after changing an app requirements pin, regenerate `desktop/payload/env.lock`
- `libs/`: reserved for the future `staf-core` shared package

## Goals
1) Keep the site stable, readable, and fast for end users.
2) Preserve existing behavior unless a change request explicitly says otherwise.
3) Deliver minimal, targeted fixes rather than broad refactors.
4) Keep source data and generated artifacts consistent.
5) Ensure contributors can validate changes quickly with repeatable commands.

## General Working Rules
1) Prefer surgical edits in the smallest relevant files.
2) Preserve existing UI structure, table alignment, and responsive behavior unless asked to redesign.
3) Favor non-destructive UI updates (class toggles/show-hide/in-place updates) over full DOM rebuilds when possible.
4) Treat `docs/` as the source of truth; `docs/_site/` is untracked Jekyll build output — never commit or write to it.
5) If JS/CSS assets change and cache-busting is used, update the version/tag in the relevant include(s).
6) Keep changes accessible (keyboard behavior, readable labels, semantic markup where practical).
7) Run app tests per app from that app's own directory (never from the repo root — the four pytest suites collide). Use the shared root `.venv` (Python 3.12, `requirements-dev.txt`).
8) Never delete or commit `.posit/publish/deployments/` records — they keep the public app URLs stable. App URL changes must be mirrored in `docs/_data/apps.yml` and each app's `staf_topnav`.

## Data and Build Expectations
1) Do not manually edit generated outputs when a script is the canonical producer; run the generator.
2) When data schemas are changed, update related docs and consumers in the same change set.
3) Keep JSON/TSV outputs deterministic; the generator writes only under `docs/assets/data/`.
4) Assessment library (`apps/library/`): do not hand-edit `catalog.json`, `manifest.json`, or `vN/` payloads — StreamCurves' Publish is the canonical producer. After a publish, re-bake DEEP (`apps/deep/scripts/bake_library_into_deep.py`) and commit `apps/library/**` and `apps/deep/data/**` together. Format contract: `apps/library/README.md`.

## Validation Checklist
Run what applies to the files touched:
- `node --check <changed-js-file>`
- `npm test --silent`
- `npm run build:metric-library` (if metric-library source/scripts changed)
- app changes: `cd apps\<app>` then `python -m pytest` (stream-curves: `python -m pytest -m "not live"`)
- desktop changes: `dotnet test desktop\Staf.Desktop.slnx`
- optional local preview:
  - site: `cd docs` then `bundle exec jekyll serve`
  - app: `cd apps\<app>` then `shiny run app.py --port <port>`

## Delivery Standard
1) Summarize what changed and why.
2) List exact files touched.
3) Report commands run and their outcomes.
4) Call out any follow-up risks, assumptions, or manual checks.

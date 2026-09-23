# Agent Instructions (Codex)

## Role
Act as a full-stack developer for the STAF monorepo: the documentation site and the four Shiny for Python assessment apps.

## Purpose
Maintain and improve:
- a GitHub Pages/Jekyll site (Markdown docs, lightweight front-end widgets, JSON/TSV data, TS build scripts)
- four Shiny for Python apps under `apps/` (EASI, SFARI, DEEP, stream-curves). EASI, SFARI and DEEP each deploy to their own Posit Connect Cloud content item; StreamCurves ships as StreamCurves Desktop (`desktop/`), released on this repository's GitHub Releases

## Scope
Primary working areas:
- `docs/`: site source (content, includes, layouts, config)
- `docs/assets/`: JavaScript, CSS, and data files
- `docs/_includes/`: shared HTML fragments used by widgets/pages
- `scripts/`: build/transform scripts (for example, metric-library generation)
- `apps/easi`, `apps/sfari`, `apps/deep`, `apps/stream-curves`: the Shiny apps (each self-contained: own requirements.txt, www/, data/, tests/; the three web apps also carry a `.posit/publish` deploy config)
- `desktop/`: StreamCurves Desktop shell (C#/.NET 10 + WebView2 + Velopack, modeled on HYPE Desktop) that runs StreamCurves locally; release model in `desktop/RELEASING.md`. Release rules: only `streamcurves-v*` shell releases are normal releases (`streamcurves-payload-*`, `streamcurves-current`, `library` and `easi-national-current` are ALWAYS prereleases); every vpk command names the Velopack channel `streamcurves`; `desktop/scripts/*.ps1` stay pure ASCII; after changing a pin in `apps/stream-curves/requirements.txt`, regenerate `desktop/payload/env.lock`
- `libs/`: shared packages vendored per app, never imported across folders at runtime. `libs/site_engine` is the STAF site engine (the HR reach watershed on NHDPlus HR); the StreamCat lookup engine (EPA StreamCat by NHDPlus V2 COMID) is the other watershed engine. Both are defined in `libs/README.md` and `docs/computation-engines.md`

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
8) Never delete or commit `.posit/publish/deployments/` records: they keep the public app URLs stable. App URL changes must be mirrored in `docs/_data/apps.yml` and each app's `STAF_LINKS` dict (StreamCurves' entry is its latest release page).
9) Commit messages and PR descriptions carry no AI co-author or attribution trailers (no `Co-Authored-By` lines for Claude, Codex, or any other agent); GitHub credits co-authors as contributors.
10) Re-vendor after any engine or EASI source change and never hand-edit a `_vendor/` tree (each app's `scripts/vendor_site_engine.py`, then StreamCurves' `vendor_easi_engine.py`; the drift-gate tests stay red until the copies match). Engine display names ("StreamCat lookup engine", "STAF site engine") come from the vendored `naming` module; the tokens `streamcat` / `site-engine` / `streamcat-legacy` in digests, bundles, manifests, the CLI, and YAML are immutable.

## Data and Build Expectations
1) Do not manually edit generated outputs when a script is the canonical producer; run the generator.
2) When data schemas are changed, update related docs and consumers in the same change set.
3) Keep JSON/TSV outputs deterministic; the generator writes only under `docs/assets/data/`.
4) Assessment library (`apps/library/`): do not hand-edit `catalog.json`, `manifest.json`, or `vN/` payloads: StreamCurves' Publish (a maintainer's checkout with `STAF_LIBRARY_PUBLISH=1`) is the canonical producer. After a publish, re-bake DEEP (`apps/deep/scripts/bake_library_into_deep.py`) and commit `apps/library/**`, `apps/deep/data/**` and `apps/deep/www/calculators/**` together; the push to `main` refreshes the rolling `library` prerelease that installed StreamCurves copies and DEEP read. Format contract: `apps/library/README.md`.

## Validation Checklist
Run what applies to the files touched:
- `node --check <changed-js-file>`
- `npm test --silent`
- `npm run build:metric-library` (if metric-library source/scripts changed)
- app changes: `cd apps\<app>` then `python -m pytest` (stream-curves: `python -m pytest -m "not live"`)
- desktop changes: `dotnet test desktop\StreamCurves.Desktop.slnx`, plus `dotnet build desktop\src\StreamCurves.Desktop` for host changes
- optional local preview:
  - site: `cd docs` then `bundle exec jekyll serve`
  - app: `cd apps\<app>` then `shiny run app.py --port <port>`

## Delivery Standard
1) Summarize what changed and why.
2) List exact files touched.
3) Report commands run and their outcomes.
4) Call out any follow-up risks, assumptions, or manual checks.

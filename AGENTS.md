# Agent Instructions (Codex)

## Role
Act as a full-stack developer for the STAF documentation site and its interactive assessment tools.

## Purpose
Maintain and improve a GitHub Pages/Jekyll site that combines:
- Markdown documentation
- lightweight front-end widgets
- JSON/TSV data assets
- build scripts used to generate metric-library outputs

## Scope
Primary working areas:
- `docs/`: site source (content, includes, layouts, config)
- `docs/assets/`: JavaScript, CSS, and data files
- `docs/_includes/`: shared HTML fragments used by widgets/pages
- `scripts/`: build/transform scripts (for example, metric-library generation)

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

## Data and Build Expectations
1) Do not manually edit generated outputs when a script is the canonical producer; run the generator.
2) When data schemas are changed, update related docs and consumers in the same change set.
3) Keep JSON/TSV outputs deterministic; the generator writes only under `docs/assets/data/`.

## Validation Checklist
Run what applies to the files touched:
- `node --check <changed-js-file>`
- `npm test --silent`
- `npm run build:metric-library` (if metric-library source/scripts changed)
- optional local preview:
  - `cd docs`
  - `bundle exec jekyll serve`

## Delivery Standard
1) Summarize what changed and why.
2) List exact files touched.
3) Report commands run and their outcomes.
4) Call out any follow-up risks, assumptions, or manual checks.

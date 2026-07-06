# EASI documentation — how to edit and rebuild

This folder builds the EASI verification and validation report into a single
self-contained web page at `../../www/documentation.html`. The EASI app serves that
file and links to it from its header ("Documentation").

## Prerequisites

- The project virtual environment (`.venv`) with the project dependencies installed.
- Quarto 1.9 or newer on your PATH (https://quarto.org). Check with `quarto --version`.

## Note on this public repository

The repository is public, so the private inputs are not committed: the SFARI source
data (`../Verification Sites/SFARI Summary.xlsx`), the cached EASI runs and CSVs
(`data/`), and the reference documents (the SFARI report, the TN drafts, and the style
PDF). The published report (`../../www/documentation.html`) and the `figures/` and
`_generated/` tables it is built from are committed. For prose-only edits you do not
need the private inputs: edit `easi-vnv.qmd` and run `quarto render`, which reuses the
committed `figures/` and `_generated/`. For a full rebuild, restore the private SFARI
xlsx locally and run `python scripts/build_docs.py --all`.

## The one command

From the repo root, using the venv Python:

```
.venv/Scripts/python.exe scripts/build_docs.py     # Windows
.venv/bin/python scripts/build_docs.py             # macOS / Linux
```

This regenerates the figures and tables, then renders the report to
`www/documentation.html`. There is no manual copy step.

Flags:

- `--site MB`   re-run EASI for one site (MB, CC, MC, ...) before building. Uses the network.
- `--all`       re-run EASI for every site before building. Uses the network, about 10 minutes.
- `--no-render` rebuild the figures and tables only, skip the Quarto render.

For a prose-only change you can also just run `quarto render` inside this folder.

## What to edit for each kind of change

| You want to ...                                   | Edit                                                    | Then run                            |
|---------------------------------------------------|---------------------------------------------------------|-------------------------------------|
| Reword text, headings, or a method table          | `easi-vnv.qmd`                                          | `quarto render` (or `build_docs.py`)|
| Change a SFARI value                              | `../Verification Sites/SFARI Summary.xlsx`              | `build_docs.py`                     |
| Re-run a site, or change which functions it overrides | site coordinates in the xlsx, and `MINK_BROOK_OVERRIDES` in `../../scripts/sfari_data.py` | `build_docs.py --site MB`           |
| Restyle a figure (colors, labels, axes)           | `../../scripts/build_doc_assets.py`                     | `build_docs.py`                     |
| Change page theme or colors                       | `easi-docs.css`                                         | `quarto render`                     |

SFARI values are read fresh from the xlsx on every build, so a value edit does not
require re-running EASI. Re-running EASI (`--site` / `--all`) is only needed when a
site's coordinates or overrides change.

## File map

Hand-edited (source):

- `easi-vnv.qmd` — the report itself: all prose plus the two method tables.
- `easi-docs.css` — page styling, matched to the EASI app.
- `_quarto.yml` — render config that sends output to `../../www/documentation.html`.

Generated, do NOT hand-edit (these are overwritten on every build):

- `figures/` — validation plots, cross-sections, and downsized site photos.
- `_generated/` — the coverage, case-study, validation, and appendix tables that the
  report pulls in with `{{< include >}}`.

Data:

- `../Verification Sites/SFARI Summary.xlsx` — SFARI field results, the source of truth.
- `data/easi_site_reports/*.json` — cached EASI results per site (refresh with `--site` / `--all`).
- `data/*.csv` — flat EASI vs SFARI comparison tables.

Build scripts (in `../../scripts/`):

- `sfari_data.py` — reads the xlsx, holds the EASI/SFARI function crosswalk and the Mink Brook overrides.
- `run_sfari_sites.py` — runs EASI at each site and writes `data/easi_site_reports/`.
- `build_doc_assets.py` — builds `figures/` and `_generated/` from the cached EASI runs plus the xlsx.
- `build_docs.py` — the one command above (assets, then render).

## Notes for editors

- Writing style: plain and clear, no em dashes and no semicolons, to match the technical note.
- Figures and tables are cross-referenced automatically. Keep the `{#fig-...}` and `{#tbl-...}`
  ids when editing, and refer to them in the prose with `@fig-...` and `@tbl-...`.
- The three case studies (Mink Brook, Cowart Creek, Marys Creek) pull their cross-section image
  and field photo from `figures/`. Photos are downsized from `../Verification Sites/` on each build.
- Mink Brook's High flow dynamics, Floodplain connectivity, and Channel evolution are expert
  overrides (see `MINK_BROOK_OVERRIDES`). They show an asterisk in that case-study table.
- `www/documentation.html` is the published output. It is large because it embeds every figure
  and photo, which keeps it a single portable file.

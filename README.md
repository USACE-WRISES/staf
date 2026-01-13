# Stream Tiered Assessment Framework

This repository hosts the documentation site for the Tiered Stream Assessment Approach. The site is built with GitHub Pages using Jekyll and the just-the-docs theme, with content in Markdown and lightweight JavaScript widgets.

## Structure
- `docs/`: GitHub Pages site source
- `docs/assets/`: CSS, JavaScript, and data files
- `docs/_includes/`: HTML partials for widgets

## Local preview (optional)
If you want to preview locally, you can run Jekyll from the `docs/` folder:

```bash
bundle install
bundle exec jekyll serve
cd docs/
bundle exec jekyll serve --livereload
```

If you do not have Jekyll installed, you can rely on GitHub Pages to build the site.

## Configuration notes
- Update `_config.yml` with your GitHub repository URL for the edit links.
- If your site is hosted at a subpath, set `baseurl` in `_config.yml`.

## Data files
Each data file is JSON and feeds one or more widgets. Field definitions are also documented in `docs/contribute/data-dictionary.md`.

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
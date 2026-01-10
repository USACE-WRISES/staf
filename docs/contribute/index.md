---
title: Contribute
nav_order: 10
description: "How to contribute content and keep the site easy to maintain."
---

# Contribute

<div class="button-row">
  <a class="btn" href="{{ site.baseurl }}/">Back to Home</a>
</div>

This repository is designed to be simple, modular, and transparent. Content is written in Markdown, data tables live in JSON, and widgets use vanilla JavaScript.

## How to contribute
- Start with the page you want to update.
- If you need new data, edit the JSON files in `docs/assets/data/`.
- Keep changes small and well documented.

## Adding content
- New pages belong under `docs/` with clear titles and front matter.
- Use existing sections and patterns to keep navigation consistent.
- Link to external references instead of duplicating long technical text.

## CHANGELOG guidance
When you update core data files or scoring logic:
- Record the change in your pull request description.
- Summarize changes to `functions.json`, `cwa-mapping.json`, or `scoring-sandbox.js` here in this section.
- Include the date, a short reason, and any downstream impacts.

## Start here
- [Editing guide](editing-guide)
- [Content style guide](content-style)
- [Data dictionary](data-dictionary)

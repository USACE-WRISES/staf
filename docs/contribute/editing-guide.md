---
title: Editing Guide
parent: Contribute
description: "Step-by-step instructions for editing content and files."
---
{% include staf_page_chrome.html %}

Use this guide for small edits and routine updates.

## Step 1: Find the page
- Content lives under `docs/`.
- Each page has YAML front matter at the top.

## Step 2: Edit the Markdown
- Keep headings short and descriptive.
- Use bullet lists for scannability.
- Avoid long walls of text.

## Step 3: Update downloads
- Place PDFs, spreadsheets, or images under `docs/assets/`.
- Link with a relative path, for example: `../assets/my-file.pdf`.
- If you need a subfolder, add it under `docs/assets/` and keep names short.

## Step 4: Update data files (if needed)
- Widget data lives under `docs/assets/data/`.
- Follow the field definitions in `docs/contribute/data-dictionary.md`.

## Step 5: Check links
- Use relative links to other pages.
- Verify links for downloads and resources.

## Step 6: Commit and publish
- Use clear commit messages.
- GitHub Pages will rebuild the site from the `docs/` folder.

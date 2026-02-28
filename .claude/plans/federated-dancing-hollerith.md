# Plan: Updated Pentagon SVG on Overview Page

## Context

The user modified `docs/assets/images/STAF_Pentagon_Final.svg` to remove its top blue border. They want the overview page to reflect this change.

## Analysis

The overview page (`docs/quick-overview.md`, line 17) already references this exact SVG file:

```html
<img class="factsheet-pentagon-graphic" src="{{ '/assets/images/STAF_Pentagon_Final.svg' | relative_url }}" ...>
```

Since the file was modified in place (same path), **no code changes are needed** — Jekyll will serve the updated SVG automatically.

The factsheet header's blue background (`#2b5a7c` in CSS) is independent of the SVG's removed blue border, so no CSS adjustments are needed either.

## Changes Required

**None.** The updated SVG will render automatically on the next build/serve.

## Verification

1. Run `cd docs && bundle exec jekyll serve` and navigate to `/staf/quick-overview/`
2. Confirm Page 1 shows the pentagon SVG without the blue top border

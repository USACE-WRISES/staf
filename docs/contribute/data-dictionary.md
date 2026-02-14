---
title: Data Dictionary
parent: Contribute
description: "Field definitions for JSON data files used by widgets."
---
{% include staf_page_chrome.html %}

This page defines the fields used by JSON files in `docs/assets/data/`.

## functions.json
- `id`: stable function identifier (string)
- `category`: Hydrology, Hydraulics, Geomorphology, Physicochemistry, Biology
- `name`: display name
- `impact_statement`: one-line impact statement shown by default in the Stream Functions widget
- `function_statement`: function statement used in assessment widgets and detail views
- `assessment_context`: longer assessment context shown in the Stream Functions widget when expanded
- `short_description`: legacy one-line description (kept for backward compatibility)
- `long_description`: legacy longer description (kept for backward compatibility)
- `example_metrics`: object with arrays per tier
  - `screening`: list of starter metrics
  - `rapid`: list of starter metrics
  - `detailed`: list of starter metrics

## cwa-mapping.json
- `id`: function id
- `physical`: mapping code `D`, `i`, or `-`
- `chemical`: mapping code `D`, `i`, or `-`
- `biological`: mapping code `D`, `i`, or `-`

## tier-questions.json
- `id`: question id
- `question`: question text
- `answers`: array of answer objects
  - `value`: machine-readable answer id
  - `label`: answer label shown to the user
  - `score_screening`: numeric score for Screening
  - `score_rapid`: numeric score for Rapid
  - `score_detailed`: numeric score for Detailed
  - `rationale_snippet`: short explanation used in the results

## scoring-example.json
- `function_id`: function id
- `score`: numeric score from 0 to 10

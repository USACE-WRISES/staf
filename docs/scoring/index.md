---
title: Scoring and Condition
nav_order: 8
description: "How metrics roll up to functions, outcomes, and overall condition."
---

# Scoring and Condition

<div class="button-row">
  <a class="btn" href="{{ site.baseurl }}/">Back to Home</a>
</div>

This page explains how metric values roll up to function scores, outcome indices, and an optional overall condition score.

## Scoring pipeline
1. Metrics are scored on a common scale.
2. Metric scores roll up to function scores.
3. Function scores roll up to outcome indices (physical, chemical, biological).
4. Outcome indices are normalized to 0–1 by dividing by 15.
5. The Ecosystem Condition Index is the average of the three indices.

**Function score scale:** 0 to 15.
**Outcome index scale:** 0 to 1 (score divided by 15, rounded to 2 decimals).

## Outcomes
- **Physical integrity** focuses on hydrology, hydraulics, and geomorphic structure.
- **Chemical integrity** focuses on thermal regime, nutrients, and water quality.
- **Biological integrity** focuses on habitat, populations, and community dynamics.

{% include scoring_sandbox_widget.html %}

## Notes on weights and roll-up
- Direct mapping weight: 1.0
- Indirect mapping weight: 0.1
- No mapping: 0

Use sensitivity analysis to test how weights affect outcomes, and document any changes.

## Downloads
- Scoring workbook template (placeholder)
- Calculation guidance (placeholder)

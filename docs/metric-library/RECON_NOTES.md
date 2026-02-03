# Metric Library Recon Notes

## Paths and sources
- Screening metrics source: `docs/assets/data/screening-metrics.tsv`
- Rapid metrics source: `docs/assets/data/rapid-indicators.tsv`
- Detailed metrics source: `docs/assets/data/detailed-metrics.tsv`
- Screening widget: `docs/_includes/screening_assessment_widget.html`
- Screening logic: `docs/assets/js/screening-assessment.js`
- Rapid widget: `docs/_includes/rapid_assessment_widget.html`
- Rapid logic: `docs/assets/js/rapid-assessment.js`
- Detailed widget: `docs/_includes/detailed_assessment_widget.html`
- Detailed logic: `docs/assets/js/detailed-assessment.js`
- Curve builder: embedded modal markup in screening/detailed includes; curve logic in their JS files

## Current formats
- Screening metrics: TSV with Discipline/Function/Metric + criteria columns (Optimal/Suboptimal/Marginal/Poor).
- Rapid metrics: TSV with indicator statements + criteria TSV.
- Detailed metrics: TSV with metric names and metadata; curve data currently generated in-memory per assessment.

## Constraints / risks
- Vanilla JS widgets mean UI changes require DOM-based components (no React/Vue).
- Assessment tables expect different row shapes; adding cross-tier metrics requires mapping.
- Curve builder logic is modal-oriented; embedding requires refactor or delegation.

## Integration plan
- Build a canonical metric library dataset in `docs/assets/data/metric-library/`.
- Add a shared workbench UI (left library + right inspector) across Screening/Rapid/Detailed pages.
- Add a lightweight registry so the workbench can add/remove metrics in the active assessment.
- Keep the assessment tables intact; map library metrics into expected row shapes.


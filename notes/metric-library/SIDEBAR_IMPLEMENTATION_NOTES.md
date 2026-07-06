# Metric Library Workbench - Implementation Notes

## Recon findings
- Screening assessment widget markup: `docs/_includes/screening_assessment_widget.html`
  - Includes a modal `.screening-library-modal` (Screening Metric Toolbox)
  - Includes a modal `.detailed-curve-modal` used for the reference curve builder
- Screening assessment logic: `docs/assets/js/screening-assessment.js`
  - Loads `docs/assets/data/screening-metrics.tsv`
  - Uses an in-memory scenario model (`metricIds`, `ratings`, `curves`, etc.)
  - The toolbox is rendered in-modal via `renderLibraryTable()`
- Rapid assessment widget markup: `docs/_includes/rapid_assessment_widget.html`
  - No toolbox today
- Rapid assessment logic: `docs/assets/js/rapid-assessment.js`
  - Loads `docs/assets/data/rapid-indicators.tsv` and `rapid-criteria.tsv`
- Detailed assessment widget markup: `docs/_includes/detailed_assessment_widget.html`
  - Includes a modal `.detailed-curve-modal` for the curve builder
- Detailed assessment logic: `docs/assets/js/detailed-assessment.js`
  - Loads `docs/assets/data/detailed-metrics.tsv`
  - Uses per-metric curve definitions in `scenario.curves`

## Constraints / risks
- The assessment widgets are vanilla JS (no framework), so the new 3-pane workbench must be DOM + CSS.
- Screening/Detailed curve builder logic is modal-driven; needs refactor or embedding in the right sidebar.
- Screening/Rapid/Detailed tables each expect different metric shapes; adding cross-tier metrics requires a lightweight mapping layer.

## Integration approach
- Introduce a single Metric Library data source in `docs/assets/data/metric-library/`.
- Add a shared workbench UI: left sidebar (library browser), center (existing widget), right sidebar (inspector + curves).
- Replace the screening toolbox modal with the left sidebar toggle; use the same layout wrapper on Rapid and Detailed pages.
- Add a shared state/store (global registry) for:
  - selected metric + profile
  - active inspector tab
  - selected curve
- Wire assessment widgets to register add/remove handlers and expose added-state to the library UI.
- Embed a simplified curve builder UI in the right sidebar using the existing curve table/controls style.



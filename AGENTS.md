# Agent Instructions (Codex)

## Project context
This repo serves a static JS widget app in `docs/` and may mirror built assets in `docs/_site/`.
Primary focus is the Rapid Assessment table behavior.

## Task
Eliminate UI flicker/refresh in production Chrome/Edge when toggling Rapid table options.
Do not change column layout or expansion behavior.

## Hard constraints (must keep)
1) Keep existing column layout exactly as currently working.
2) Metric expand/collapse via expander glyph in the "Metric" column must behave exactly as currently working in Rapid:
   - expand/collapse must NOT scroll page
   - no full page reload
   - no broken column widths or rowspans
3) No layout regressions in:
   - Discipline / Function / Metric columns
   - Function Score column and slider alignment
   - Mapping columns (Physical/Chemical/Biological)

## Likely root cause
`renderTable()` called for option toggles recreates slider DOM nodes, causing default->restored flash.
Goal: stable DOM. Prefer show/hide and class toggles over rebuild.

## Target implementation
A) Replace full `renderTable()` calls for these toggles with non-destructive updates where feasible:
   - Show advanced scoring columns
   - Show Function Mappings
   - Show roll-up at bottom
   - Show Suggested Function Scores
   - Show F/AR/NF labels
   Approach: toggle CSS classes and show/hide existing columns/sections. Update labels in place.

B) Keep slider/input nodes mounted:
   - do not recreate range inputs on toggle
   - update UI via class toggles or existing update logic

C) If a structural re-render is unavoidable:
   - snapshot current slider values
   - restore during construction before first paint (avoid visible flash)

## Files
- docs/assets/js/rapid-assessment.js
- docs/assets/css/custom.css
- docs/_includes/footer_custom.html (cache bust only if JS/CSS changes)
- docs/_site equivalents must mirror final JS/CSS if site serves them

## Acceptance criteria
1) Any rapid toggle does NOT scroll page.
2) Sliders do NOT flash/reset when toggles change.
3) Metric expand/collapse remains stable and widths stay correct.
4) No column misalignment with/without advanced/mapping toggles.
5) Build tag updated so tester can confirm latest script loaded.

## Validation commands
- node --check docs/assets/js/rapid-assessment.js
- node --check docs/_site/assets/js/rapid-assessment.js
- npm test --silent

## Working style
- Make minimal, surgical changes.
- Avoid broad refactors.
- Prefer small commits.
# SFARI field review — task-ordered reorg (revertible)

2026-07-10. The Field review worksheet was reorganized so the center panel
reads in work order: function title → lines of evidence (rate each metric) →
the conclusion card (function statement + 0–15 banded score, white card with
accent left border) → Previous/Next. Supporting changes:

- "Open report" moved from the per-function footer to the bottom of the
  rollup rail (the results column); it turns primary once all 20 functions
  are scored.
- The rail shows "–" instead of a misleading 0.00 until something is scored
  (ECI at zero scored; each Physical/Chemical/Biological bar stays dashed
  until an outcome has a contributing function).
- Metric note/photo toggles appear on hover/keyboard focus only (always
  visible on touch devices and when they hold content).
- Evidence strips lost their boxes (quieter rows).
- styles.css cache-bust bumped to v=10.

Follow-up refinement (same day, per user mockup): numbered section headers —
"1 Evidence" (one card holding the header + metric rows with dividers) and
"2 Score this function" on the conclusion card — plus a justified footer:
Previous (+ hydraulics) left, live "n/m rated · score needed/scored" status
center, "Next function ›" right. Cache-busts v=11 / field-review.js v=6.

Files: `apps/sfari/app.py`, `apps/sfari/www/styles.css`,
`apps/sfari/www/field-review.js` (plus this note).

## To undo the redesign

Revert every commit after the restore point d0adffa (`sfari: field review
polish (dropdown colors, flicker, layout, fonts, banded slider)`) — git
reverts them newest-first automatically:

    git revert --no-edit d0adffa..HEAD

(or `git reset --hard d0adffa` to erase them instead, while unpushed), then
restart the SFARI app / preview server.

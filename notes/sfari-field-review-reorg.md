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

Files: `apps/sfari/app.py`, `apps/sfari/www/styles.css` (plus this note).

## To undo the reorg

Revert the single commit titled
`sfari: field review task-ordered reorg (single revertible commit)`:

    git revert $(git log --format=%H --grep="field review task-ordered reorg" -n 1)

then restart the SFARI app / preview server. That restores the previous
approved state — commit `sfari: field review polish (dropdown colors,
flicker, layout, fonts, banded slider)`.

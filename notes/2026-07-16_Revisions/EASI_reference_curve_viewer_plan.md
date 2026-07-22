# EASI Screening Reference-Curve Viewer — Working Plan (Variation B)

**Status:** DRAFT for revision (not executed). Sibling to Variation A
(`EASI_metric_scoring_transparency_plan.md`). This variation takes the **opposite direction on the
metrics**: instead of stripping composites down to single indicators, it **keeps the multi-input
composites (1-4 values)**, justifies every input and weight, revises the few that are indefensible, and
exposes each metric's scoring as an interactive **reference curve** the reviewer can open from the report.

**Locked decisions (from review):**
1. **Visualize only.** The scoring math is unchanged: value -> Good/Fair/Poor -> fixed index midpoint
   (0.195 / 0.545 / 0.85). The reference curve *illustrates* that mapping and where a site's value lands;
   it does not replace it. (Exception: the handful of composites revised in section 5, which do change a
   score and need a parity-golden refresh.)
2. **Justified weighted sum.** Composites stay linear weighted combinations. Each input's inclusion and
   each weight get a written justification grounded in literature; weights that are screening judgments
   are labeled as such and flagged for SME calibration. The combined value drives one reference curve.
3. **Inline slide-over panel, minimal by default.** The curve opens as a panel *within* the report (a
   right-side drawer that slides in), not a modal-over-modal. The report stays visible and sliders update
   the curve live; the panel leads with the curve and folds all prose/citations into a collapsible, so the
   default view stays uncluttered.

**Grounding (from a full repo scan):** STAF already has (a) a metricId-keyed curve data format
(`docs/assets/data/metric-library/curves/*.json` + `screening-reference-curves.json`), (b) a dependency-
free native curve viewer to model on (DEEP `_curve_svg` + `_criteria_table`, `apps/deep/app.py:107-219`,
which already uses EASI's Good/Fair/Poor pastels and a live value marker), and (c) a shipping precedent
for sliders driving a live Plotly plot inside a report (the cross-section editor,
`_sync_xsection_plot`, `app.py:1917`). We reuse all three rather than invent.

---

## 1. Intent

A skeptical reviewer opening the screening report should be able to click one link on any metric and see:
what inputs feed the score, how they combine (the equation), the breakpoints and what each represents, and
exactly where this site sits on the curve — then drag sliders to see how the rating would move. This makes
the scoring transparent and defensible without cluttering the report itself.

## 2. Scope and non-goals

**In scope (EASI):** per-metric reference-curve data (equation, inputs+weights+justification, breakpoints,
section annotations, citations); the inline slide-over viewer with sliders; the tooltip link; a small set
of composite revisions where the current math is indefensible.

**Non-goals:** we do NOT convert EASI to continuous curve scoring (deferred; see Open Items). We do NOT
touch the rollup engine, sub-indices, or ECI. Most metrics' computed scores are unchanged.

## 3. The reference-curve data model

Author a new EASI-local file **`apps/easi/data/screening-curves.json`**, hand-authored, keyed by
`metricId`, validated at load. One entry per metric:

```json
{
  "metricId": "sediment-continuity-sediment-supply-potential-watershed-banks",
  "kind": "composite",                         // "single" | "composite"
  "xLabel": "Combined sediment-supply score", "xUnit": "", "xMin": 0, "xMax": 1,
  "direction": "higher-worse",
  "inputs": [
    { "key": "ag", "label": "Agricultural cover", "unit": "%",
      "source": "StreamCat pctcrop2019ws + pcthay2019ws", "weight": 0.5, "cap": 50,
      "slider": { "min": 0, "max": 100, "step": 1 },
      "justification": "Cropland is the dominant anthropogenic upland fine-sediment source (Waters 1995; Allan 2004)." },
    { "key": "kf", "label": "Soil erodibility (K-factor)", "unit": "",
      "source": "StreamCat kffactws", "weight": 0.3, "cap": 0.4,
      "slider": { "min": 0, "max": 0.65, "step": 0.01 },
      "justification": "RUSLE K sets how erodible the disturbed soils are (inherent supply modulator)." },
    { "key": "rd", "label": "Road density", "unit": "km/km2",
      "source": "StreamCat rddensws", "weight": 0.2, "cap": 5,
      "slider": { "min": 0, "max": 10, "step": 0.1 },
      "justification": "Roads are a documented fine-sediment source and connect hillslope sediment to channels (Forman & Alexander 1998)." }
  ],
  "combine": "weighted_sum_capped",            // V = sum_i weight_i * min(input_i / cap_i, 1)
  "equation": "V = 0.5·min(ag/50,1) + 0.3·min(K/0.4,1) + 0.2·min(roads/5,1)",
  "breakpoints": [
    { "at": 0.33, "label": "Good | Fair", "description": "Below here, watershed sediment-supply pressure is low." },
    { "at": 0.66, "label": "Fair | Poor", "description": "Above here, combined pressure is high enough to expect chronic fine-sediment loading." }
  ],
  "zones": [
    { "rating": "Good", "range": "< 0.33", "index": 0.85,  "description": "Low anthropogenic sediment supply." },
    { "rating": "Fair", "range": "0.33-0.66", "index": 0.545, "description": "Moderate supply from cultivated land and roads." },
    { "rating": "Poor", "range": "> 0.66", "index": 0.195, "description": "High supply; expect embeddedness and turbidity effects." }
  ],
  "basis": "Inputs cited above; the 0.5/0.3/0.2 weights and 0.33/0.66 breakpoints are screening judgments, provisional pending SME calibration.",
  "weightsProvisional": true
}
```

For `kind:"single"`, omit `inputs`/`combine`/`equation`; the x-axis is the raw metric value and
`breakpoints`/`zones` are on that value (e.g. entrenchment ratio 1.4 / 2.2).

This one file carries everything the viewer needs. It intentionally reuses the *shape* of the existing
`screening-reference-curves.json` points/zones, and adds three fields STAF doesn't populate today:
`equation`, per-`input` weight+justification, and per-breakpoint/zone `description`. (The metric-library
schema already has a dormant `formula` scoring type — `{expression, variables}` in
`src/lib/metricLibrary/schemas.ts` — which is the natural home when we mirror this to the public library
in Phase D.)

**Adapters expose their inputs.** So the viewer can seed the sliders with the site's real values, each
composite adapter returns them on the result (mirrors the existing `detail` pattern):
`detail={"kind":"curve_inputs","inputs":{"ag":41.2,"kf":0.28,"rd":1.1},"combined":0.58}`. Single-value
metrics already carry `value`. No scoring change — this is additive.

## 4. Per-metric composite specs (inputs, weights, justification, revisions)

Current formulas verified against `apps/easi/easi/metrics/*.py`. "Keep" = retain inputs+weights, add
justification + curve. "Revise" = a change I recommend because the current math is indefensible (you asked
me to flag these explicitly).

### KEEP (justify inputs + weights; visualize; no score change)

**Sediment Supply** `geomorphology.py:54` — `V = 0.5·min(ag/50,1)+0.3·min(K/0.4,1)+0.2·min(rd/5,1)`, breaks 0.33/0.66 (higher-worse).
- ag (0.5): dominant anthropogenic upland sediment source (Waters 1995; Allan 2004).
- K-factor (0.3): inherent soil erodibility modulates supply (RUSLE K).
- road density (0.2): fine-sediment source + hillslope-channel connectivity (Forman & Alexander 1998).
- Weights/breaks provisional. 3 sliders.

**Bank Erosion & Armoring** `geomorphology.py:88` — `risk = 0.4·min(K/0.4,1)+0.4·(1-min(rip/100,1))+0.2·min(slope/0.02,1)`, 0.4/0.7.
- **Revision inside a KEEP:** swap riparian FOREST -> natural riparian vegetation (`riparian_natural_veg_pct`) so shrub/grass banks in arid ecoregions aren't penalized (fixes the forest-only bias). Structure/weights unchanged.
- riparian deficit (0.4): root reinforcement is the dominant bank-stability control (Simon & Collison 2002).
- K-factor (0.4): bank-material erodibility. slope (0.2): stream-power surrogate. Weights provisional. 3 sliders. (Score changes only because of the forest->natural-veg swap -> golden refresh.)

**Hyporheic Exchange** `hydraulics.py:133` — `0.6·min(slope/0.01,1)+0.4·clamp((sin-1)/0.5)`, 0.6/0.3 (higher-better).
- slope (0.6): first-order vertical head-gradient driver of exchange (Boano et al. 2014).
- sinuosity (0.4): lateral/meander-driven exchange. Both defensible; weights provisional. 2 sliders.
- Honest caveat in `basis`: the true first-order control (streambed Ksat) isn't in the desktop data; this is a coarse proxy (confidence L).

**Regulatory Impairment surrogate** `physicochemistry.py:56` — `stress = 0.45·min(imp/25,1)+0.35·min(ag/60,1)+0.20·min(rd/5,1)`, `risk = clamp(stress - 0.15·min(rip/60,1))`, 0.25/0.5.
- 4 inputs (imp, ag, roads stress; riparian mitigation). NPS-impairment risk from developed+cultivated land and roads, buffered by riparian. Each input defensible (CWP; Allan 2004); weights provisional. ATTAINS stays the primary path — this surrogate only runs when no assessed water is found, and the curve/`basis` says "modeled NPS risk, not a 303(d) listing." Good 4-slider showcase.

**Stream Temperature surrogate** `physicochemistry.py:165` — `idx = tair - 2.0·min(rip/60,1)`, 12/17.
- air temperature (primary driver; Mohseni & Stefan 1999) minus a riparian-shade credit (Poole & Berman 2001). 2 inputs. The -2 C max shade credit is a judgment; label it. Observed WQP stays primary. 2 sliders.

**Biological Integrity (IBI)** `biology.py:68` — `score = clamp(0.5 + 0.5·min(rip/60,1) - 0.6·[0.45·min(imp/25,1)+0.35·min(ag/60,1)+0.20·min(rd/5,1)])`, 0.66/0.4 (higher-better).
- 4 inputs. Land use is the best-cited landscape predictor of biological integrity (Allan 2004; Booth & Jackson 1997; CWP), with riparian support. Weights provisional; label "modeled, not a measured IBI." 4-slider showcase.

**Flow Alteration** `hydrology.py:111` — single derived ratio `damnrmstorws / DA`, 5/100 ac-ft/km2 (higher-worse). Keep. `kind:"single"`, x = storage ratio. Reframe the axis label to the impoundment ratio (~ mm of impoundable depth); fix the criteria text (today it says "% from baseline"). 1 slider (or a numeric input for the ratio).

**Detrital CPOM** `physicochemistry.py:88` — single, natural riparian veg %, 50/20 (higher-better). Keep. `kind:"single"`. Only the curve + criteria wording are new.

### REVISE (I feel strongly; these change scores -> golden refresh)

**Floodplain Engagement** `hydraulics.py:50` — **drop the fabricated recurrence transform.** Today it maps BHR through `bhr^(5/3)` and a hard-coded growth curve into "recurrence years," then bins 2/5 yr. That transform has no basis. **Bin the measured bank-height ratio directly** (`kind:"single"`, x = BHR): Good <=1.2 / Fair 1.2-1.5 / Poor >1.5 (or align to Channel Evolution's 1.3/1.7). BHR~1 = floodplain-connected, >1.5 = incised. Delete `_GROWTH` and `_recurrence_from_ratio`. (You named this one.)

**Substrate Condition** `geomorphology.py:71` — the current 3-input mix conflates a *natural* driver (channel slope -> grain size) with *anthropogenic* fines (ag, K), so a naturally sand-bedded lowland reach scores as degraded. **Recommend dropping the slope term** and keeping a 2-input anthropogenic fine-sediment-pressure composite `V = 0.6·min(ag/50,1)+0.4·min(K/0.4,1)` (report slope separately as natural-texture context). Alternative (if you'd rather keep it a viz-only KEEP): retain all three but relabel the metric "expected fine-sediment pressure" and annotate the slope term as the natural-texture axis. 2 sliders.

**Habitat Complexity** `biology.py:52` — `0.6·min(rip/60,1)+0.4·min(order/4,1)` (+ an unused slope fetch). **Stream order is a size descriptor, not complexity** — a bigger stream isn't inherently more complex. **Recommend dropping stream order** (and removing the dead slope read). Either single input (riparian forest %, an LWD/cover proxy) or a defensible 2-input `0.6·riparian + 0.4·sinuosity` (planform complexity). 1-2 sliders.

## 5. The inline slide-over viewer (UI/UX)

**Design language: minimal by default, depth on demand.** The panel leads with one thing — the curve — and
keeps everything else quiet or folded away. One accent color, generous whitespace, EASI's own pastels used
*only* for the three rating zones. Nothing on the panel competes with the plot.

**Entry point (report stays clean).** The metric tooltip keeps only its Good/Fair/Poor rows plus one quiet
link at the bottom: `View reference curve ↗`. No other new clutter in the table.

**The panel.** The link slides a drawer in from the right edge of the report (~420px / 30rem; a full-width
expanding section on narrow screens). The report stays visible to its left. The contents are a strict
top-to-bottom hierarchy, not a stack of equal boxes:

```
┌────────────────────────────────────────────────┐
│  Sediment Supply Potential                  ✕   │  title + close
│  Higher watershed pressure lowers the score     │  one-line plain-language subtitle
│                                                  │
│ index │ Good  │  Fair  │   Poor                  │
│ 0.85 ─┤▓▓▓▓▓▓▓│        │                          │  ← the curve (hero):
│       │       └────────┐                         │    shaded zones, stepped index line,
│ 0.55 ─┤       │░░░░●░░░░│      ● this site        │    breakpoint ticks, site marker
│       │       │        └────────┐                │
│ 0.20 ─┤       │        │▒▒▒▒▒▒▒▒▒▒                │
│       └───────┴────────┴────────── V             │
│             0.33     0.66                         │
│  V = 0.5·min(ag/50,1)+0.3·min(K/0.4,1)+…    Fair  │  equation caption + live rating chip
│ ──────────────────────────────────────────────── │
│  Agricultural cover       41 %   ▓▓▓░░     w .5   │  compact input rows (composites only):
│  Soil K-factor           0.28    ▓▓░░░     w .3   │    label · value · slider · weight tag
│  Road density      1.1 km/km²    ▓░░░░     w .2   │
│                                reset to site      │
│ ──────────────────────────────────────────────── │
│  ▸ Breakpoints & basis                            │  collapsed by default
└────────────────────────────────────────────────┘
```

1. **Header** — metric name, a one-line plain-language subtitle ("Higher watershed pressure lowers the
   score"), and a close ✕. One sentence reads cleaner than a row of chips, so the subtitle replaces the chip
   strip.
2. **The curve (the hero)** — full drawer width. x = the raw value (single) or the combined value V
   (composite); y = index. The three rating zones are shaded as vertical bands in EASI's pastels (`#c8d9f2` /
   `#f5e7a6` / `#f5b5b5` at ~0.4 opacity), each with a faint "Good / Fair / Poor" label inside the top of the
   band, so no separate legend is needed. Breakpoint values tick the x-axis; the y-axis is labeled only at
   the three index levels (0.85 / 0.545 / 0.195). A red marker (`#d6453d`) with a short callout ("This site:
   V = 0.58") shows where the site lands. Because scoring is visualize-only, the index is drawn as a light
   **step** line — honest to the math; the shaded zones and breakpoints, not the line, carry the meaning.
3. **Equation caption** (composites) — one line under the plot in the house math style (`_bieger_area_tip_html`:
   `<sup>`, `·`, unicode), with a small live **rating chip** (`.easi-rate-chip.rate-*`) that recolors as the
   sliders move. Not a boxed section — just a caption.
4. **Input rows / sliders** (composites) — one compact row per input: label, current value + unit, a thin
   slider, and a small weight tag (`w .5`). Seeded with the site's real values (from the adapter's
   `curve_inputs`); dragging recomputes V and moves the marker + rating chip live. One quiet "reset to site"
   link. Single-value metrics show **no** sliders — just the marker on the curve.
5. **Breakpoints & basis** — a single `<details>` collapsible, **collapsed by default**. Opened, it shows the
   per-zone and per-breakpoint prose (`zones[].description` / `breakpoints[].description`) and a muted
   citation/basis line including the explicit "weights provisional" note. Folding this is what keeps the
   default view minimal; the reviewer who wants the reasoning opens it.

**Why this is the professional, uncluttered choice.** The default panel is essentially *title + curve + a few
sliders* — three visual elements with a lot of air. The prose, citations, and breakpoint descriptions that
would otherwise crowd it sit one click away in the collapsible. The curve annotates itself (zone labels +
breakpoint ticks + the site callout), so we drop the separate always-on criteria table that would duplicate
it. It reuses DEEP's native curve look and EASI's existing tokens, so it reads as one app, and the slide-over
keeps the report in view without the modal-over-modal problem. A single combined-value curve plus a compact
slider stack stays far calmer than per-input small-multiples.

## 6. Wiring

- **Tooltip link -> panel.** The tooltip HTML (`_metric_tip_html`, `app.py:111`) gains
  `<a class="easi-curve-link" data-mid="{mid}">View reference curve ↗</a>`. A delegated click handler
  (new lines in `www/report-edit.js`, mirroring the `override_set` pattern) posts
  `Shiny.setInputValue("open_curve", {mid, nonce}, {priority:"event"})`. **Tooltip keep-alive:** `www/tooltip.js`
  currently hides the card 90 ms after the icon's `mouseout`, so a link inside it isn't reliably clickable —
  add a small change to cancel the hide timer while the pointer is over the `.easi-tip` card and hide on the
  card's `mouseleave`. (Fallback if we don't want to touch tooltip.js: put the link as a sibling of the
  ⓘ icon in the rating cell, `app.py:462`.)
- **Server.** A `reactive.Value` `curve_mid`; `@reactive.effect @reactive.event(input.open_curve)` sets it
  from `input.open_curve()["mid"]`. The report body gains an `output_ui("curve_panel")` slot (normally
  empty). `@render.ui def curve_panel()` reads `curve_mid()`, loads the metric's entry from
  `screening-curves.json` and the row's `curve_inputs`, and renders the header, plot slot, equation caption
  + rating chip, the dynamically-built `input_slider`s (one per input), and the collapsed `<details>`
  breakpoints/basis block. A close button clears `curve_mid`.
- **Sliders -> marker.** `@reactive.effect` reads the sliders, recomputes V with the same
  `weighted_sum_capped` math the adapter uses (share one helper so the plot and the score can't diverge),
  and updates the plot marker.

## 7. Rendering

- **Live single-site report:** reuse the shipping interactive path — a Plotly `FigureWidget`
  (`output_widget` + `render_widget`) updated in place via `batch_update()`, exactly like
  `_sync_xsection_plot` (`app.py:1917`). Match the xsection styling (`xsplotly.py`): white bg,
  `gridcolor="#eef0f4"`, stripped modebar, no legend. Draw the zones with `add_vrect` (pastel fills, faint
  in-band "Good/Fair/Poor" labels), the breakpoints with `add_vline` + x-axis ticks, the index as a light
  step line with the y-axis labeled only at 0.195 / 0.545 / 0.85, and the site as a single red marker with a
  short callout. The "Breakpoints & basis" prose is plain HTML (`<details>`), not part of the figure, so the
  plot itself stays uncrowded. (Native SVG a la DEEP `_curve_svg` is the dependency-free alternative if we'd
  rather not add a second plotly widget; it can be driven client-side by a small `measure.js`-style script.
  Recommend Plotly for slider smoothness since the pattern already ships.)
- **Read-only batch report** (`_batch_report_modal`, `app.py:743`) has no server reactivity by design, so
  there it shows a **static** curve (matplotlib PNG via an `xsplot`-style renderer, or a static SVG) with no
  sliders, plus the same table/annotations. Note this limitation; live exploration is the single-site
  report.
- **PDF export:** include the static curve PNG per metric (optional; can be phase-2 of the viewer).

## 8. Authoring and storage

- **EASI:** `apps/easi/data/screening-curves.json` (new, hand-authored) + a loader/validator in
  `easi/config.py` (or a small `easi/curves.py`) with a `validate_screening_curves()` gate (every rated
  metric has an entry; weights sum ~1 for weighted_sum; caps > 0). Adapters emit `curve_inputs`.
- **Public metric library + docs:** mirror each screening curve into the source CSV
  (`Metric Library Complete 2026-02-10.csv`) via new columns (equation, input weights/justification,
  breakpoint/zone descriptions), thread them through `src/lib/metricLibrary/schemas.ts` (reuse/extend the
  dormant `formula` scoring type) + `scripts/compileMetricLibraryFromCsv.ts`, `npm run build:metric-library`,
  and surface on `docs/scoring/index.md`. The docs site already has a canvas curve renderer
  (`metric-library-workbench.js renderCurveChart`) that can display these. Never hand-edit generated JSON.
- **StreamCurves:** re-run `apps/stream-curves/scripts/vendor_easi_engine.py` after the EASI changes so the
  vendored engine + data stay in sync (its drift-gate is already red from prior work).

## 9. Propagation (lighter this round)

EASI is the focus. SFARI's advisory evidence could later show the same reference curves in its evidence
popups, but that's optional and deferred — note it, don't build it here. Docs/library gets the curve data
(section 8) so the published methodology matches.

## 10. Phasing (verify after each)

- **A. Curve data + justification.** Author `screening-curves.json` for all ~20 metrics (inputs, weights,
  justifications, breakpoints, zone/annotation prose, equations, citations). Add the loader/validator.
  Adapters emit `curve_inputs`. No UI yet. This is the science/content phase — the bulk of the revisable work.
- **B. Composite revisions.** Floodplain Engagement (BHR direct), Substrate (drop slope), Habitat (drop
  order), Bank Erosion (forest -> natural veg). These change scores -> regenerate the parity golden
  (`EASI_WRITE_GOLDEN=1`) and review the diff.
- **C. Viewer.** Tooltip link + keep-alive; `curve_panel` slide-over; Plotly curve + live marker; dynamic
  sliders; breakpoint table + annotations; CSS (reuse `.easi-tip-*`, `.easi-rate-chip`, `.modal-body
  .recalculating` fix; new `.easi-curve-*`), cache-bust bump.
- **D. Docs/library** curve data + rebuild; re-vendor StreamCurves.

## 11. Verification

1. Per-app pytest from each app dir. New: `screening-curves.json` schema/coverage test; a test that the
   viewer's `weighted_sum_capped` helper reproduces each composite adapter's combined value for sample
   inputs (so the plotted marker equals the scored value). Phase B updates `test_adapters.py` for the
   revised metrics + regenerates `tests/data/parity_golden.json`.
2. Offline render check: build `curve_panel` for a composite (e.g. Sediment Supply) with a synthetic row,
   assert the equation, three zones, breakpoints, slider set, and marker position render (unescape HTML).
3. `npm run build:metric-library` + `npm test` + jekyll build green.
4. Live EASI walkthrough (restart the instance): delineate a site, open the report, click "View reference
   curve" on a composite, confirm the panel slides in with the curve + shaded EASI-pastel zones +
   breakpoints + equation, drag a slider and watch the marker/rating move, confirm the report stays visible
   and the panel closes cleanly. Repeat on a single-value metric (entrenchment) and a revised one
   (floodplain engagement now on BHR).
5. Screenshot the panel to confirm it reads as native (colors, fonts) and uncluttered.

## 12. Open items / for revision

- **Weights and breakpoints are the soft spot.** Every composite weight and every breakpoint currently in
  the code is a screening judgment. The plan documents them honestly and flags `weightsProvisional`; they
  need an SME/calibration pass before this is defensible in review. This is the main thing to work through
  on revision.
- **Substrate / Habitat revisions** (dropping slope / stream order) are recommendations — confirm or keep
  them as viz-only KEEPs with relabeled axes.
- **Floodplain Engagement breakpoints:** BHR 1.2/1.5 vs align to Channel Evolution 1.3/1.7.
- **Deferred:** continuous curve scoring (Variation A-style "curve is the scoring") remains available as a
  later upgrade; this plan is visualize-only by decision.
- **Batch/read-only** gets a static curve (no sliders) — confirm that's acceptable, or invest in a
  client-side JS curve so sliders work there too.
- **Rendering:** Plotly (recommended, reuses xsection sync) vs native SVG (DEEP-style, dependency-free).
- **Dropped the always-on criteria table** in favor of a self-annotating curve (in-band zone labels +
  breakpoint ticks + site callout) plus the collapsible "Breakpoints & basis." Confirm that reads clearly
  enough, or restore a compact 3-row strip under the curve.

## 13. Appendix — reusable assets and anchors

- **Curve viewer to model on:** DEEP `_curve_svg` `apps/deep/app.py:104-219` (curve + zones + marker);
  `_criteria_table` is the model for the collapsible "Breakpoints & basis" content (folded, not always-on);
  CSS `apps/deep/www/deep.css:140-195`; live client update `apps/deep/www/measure.js`.
- **Sliders-drive-live-plot precedent:** `apps/easi/app.py` `_report_modal` (704), `_xsection_section`
  (641), `xsection_plot` render (1890), `_sync_xsection_plot` (1917); figure styling
  `apps/easi/easi/xsplotly.py:66-200`; static twin `easi/xsplot.py`.
- **Equation house style:** `_bieger_area_tip_html` `app.py:173-190` (HTML `<sup>`, `·`, unicode).
- **Tooltip + link wiring:** `_metric_tip_html` (111), `_metric_table` link point (462), `_info` (94);
  `www/tooltip.js` (hover-hide to patch), `www/report-edit.js` (`setInputValue` delegation to copy),
  inline-onclick->modal precedent `app.py:2237-2241`.
- **Nested-modal constraint (why slide-over):** Shiny replaces (not stacks) modals — `ui.modal_show` reuses
  one wrapper; opening a curve modal from the report modal would replace the report.
- **Current step scoring (unchanged):** `RATING_INDEX` `config.py:38`, `scoring.rating_to_index`
  `scoring.py:24`, `function_score` `scoring.py:33`; assembled in `assessment.assess` (126-129).
- **Design tokens:** rating bands Good `#c8d9f2` / Fair `#f5e7a6` / Poor `#f5b5b5` (text `#1f3f6e` /
  `#6b5310` / `#7a2e2e`); accent `#2f4b7c`; marker `#d6453d`; gridcolor `#eef0f4`. Reuse `.easi-tip--html`,
  `.easi-tip-lbl`, `.easi-tip-crit`, `.easi-tip-dot.good/.fair/.poor`, `.easi-rate-chip.rate-*`,
  `.easi-fslider*` band bar, `.easi-facts`/`.easi-fact`, `.modal-dialog.modal-xl`, `.easi-modal-x`,
  `.modal-body .recalculating` flicker fix (`styles.css:7-71,149-171,318-333,446-464,544-545`). En dash for
  ranges (`0.33–0.66`); em dash only for unset values.
- **Existing curve data to extend:** `docs/assets/data/screening-reference-curves.json`,
  `docs/assets/data/metric-library/curves/*.json`, the dormant `formula` type in
  `src/lib/metricLibrary/{types,schemas}.ts`.

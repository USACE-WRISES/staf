# DEEP Assessment Workspace and Library Integration Revision Plan

_Reconciled against the current `apps/deep` + `apps/library` + StreamCurves publisher code
(2026-07-10) and two confirmed decisions: (1) scope this plan to DEEP the **consumer** and delegate
the library schema v2 / lifecycle-writer / fingerprints / embedded scoring contract / SQT migration
to the **StreamCurves (publisher) plan**; (2) keep the lifecycle to a simple **preliminary /
certified** model. Supersedes the earlier draft in this folder, which bundled the upstream
machinery into DEEP._

## Context

`DEEP_brainstorming.md` asks two things: (1) redesign the Assessment tab to follow SFARI's Field
Review style, and (2) integrate the shared assessment library, letting the user find applicable
assessments by site location and Level III ecoregion, pick a preliminary or certified version, load
its reference curves into the workflow, show whether it is preliminary or certified, and save the
assessment name + version so completed assessments stay reproducible.

**Scope reconciliation (confirmed).** The prior draft defined an entire library schema v2 with
immutable versioned content, content fingerprints, a five-state lifecycle, certification gates, and
an embedded scoring contract, plus migrating the eight state SQTs into the library. Verification
shows **StreamCurves is the writer** (its Publish action writes `apps/library/` then subprocess-bakes
DEEP; publishing is local/desktop only), and DEEP is a pure consumer. So this plan covers only the
DEEP app-side consumer work; the upstream machinery is a companion workstream owned by the
StreamCurves plan (Part E), and the lifecycle stays two-state.

**One hard cross-plan sequencing constraint** (see Part E): DEEP's spatial hard-block (require a
covering published polygon, drop the "polygonless applies nationwide" rule) cannot ship until the 8
state SQTs receive applicability polygons, which is upstream. Today only the ECBP demo seed has a
polygon, and it is being removed, so DEEP would be left with nothing selectable. Until the migration
lands, DEEP keeps a clearly-labeled national fallback.

## Current State (grounded in code)

**Workflow** ([app.py:56-59](../../apps/deep/app.py)): 5-step `Identify -> Basin -> Assessment -> Measure -> Report` (`STEP_IDENTIFY/BASIN/ASSESS/MEASURE/REPORT`), Shiny Core `page_fillable`. Identify/Basin/Assessment render in the narrow left pane (`easi-leftpane`); Measure/Report return `None` there and show a **full-width 3-column worksheet** (`worksheet()`, [app.py:1194-1202](../../apps/deep/app.py)) that already reuses SFARI's copied classes `sfari-nav` / `sfari-fnpanel` / `sfari-rollup` inside `sfari-worksheet`. So the Measure area is already SFARI-styled; **the Assessment selector is still in the narrow left pane** ([app.py:907-930](../../apps/deep/app.py)) and the Measure panel needs detailed expansion.

**Assessment loading today (three mechanisms)**:
- Upload a `.deep.json` bundle: `input_file("upload_assessment")` ([app.py:927](../../apps/deep/app.py)), handler `_upload_assessment` -> `assessments.from_bundle` ([app.py:1016-1031](../../apps/deep/app.py)).
- URL handoff opening an arbitrary local path: `_ingest_url_params` reads `?handoff=<local .deep.json path>` and `open(handoff)` ([app.py:433-464](../../apps/deep/app.py)); also `?assessment=<id>` (id only, resolves latest).
- Built-in/baked list: `config.assessments()` = `data/deep-assessments.json` merged with the live library via `_merge_library()` "latest wins" ([config.py:65-102](../../apps/deep/deep/config.py)); cloud gets the baked snapshot only, dev/desktop merges the live library.

**Scoring/curves** ([curves.py](../../apps/deep/deep/curves.py), [scoring.py](../../apps/deep/deep/scoring.py)): `interp_curve` piecewise-linear with **silent endpoint clamp** for out-of-domain x ([curves.py:44-47](../../apps/deep/deep/curves.py)); `function_index` = mean of non-NA indices x 15; ECI = mean of the three sub-indices ([scoring.py:76](../../apps/deep/deep/scoring.py)); bands at 0.39/0.69 and 5/10 ([config.py:29-32](../../apps/deep/deep/config.py)). Bundle validation checks curve `y in [0,1]` but never checks measured x against the curve domain ([assessments.py:229-231](../../apps/deep/deep/assessments.py)).

**Auto-measurement cache** is keyed by `assessmentId` only: `computed_for` reactive ([app.py:424](../../apps/deep/app.py)), guarded at [app.py:1158-1159](../../apps/deep/app.py), set at [app.py:1165](../../apps/deep/app.py). It is not keyed by site/version/digest and is not reset in `_do_reset()` ([app.py:802-816](../../apps/deep/app.py)) or on session load, so re-running the same assessment at a different delineated site skips recompute.

**Sessions** ([session.py](../../apps/deep/deep/session.py)): `SCHEMA_VERSION=1`, no migrations map, `load()` ignores `schemaVersion`. The **assessment bundle is already inlined by value** with its curves, so the session resumes standalone. `measured_values` serialize as plain dicts.

**Reports** ([report.py](../../apps/deep/deep/report.py)): CSV/GeoJSON/PDF carry assessment **name + source citation only**; no version, no lifecycle (none exists), no region.

**DEEP has no ecoregion/state polygons and does no point-to-ecoregion/state resolution.** `find apps/deep -iname '*.geojson'` is empty; `delineation.py` snaps to a COMID but resolves no ecoregion/state. The polygon sets and resolvers live in **StreamCurves**: `apps/stream-curves/data/ecoregions_l3.geojson` (`US_L3CODE`), `us_states.geojson` (`state`), and `streamcurves/geo.py` (`state_at`, `locate_polygon_property`, `region_polygon_geometry`). Assessment applicability today is point-in-polygon against each assessment's own inlined region polygon ([assessments.py:170-179](../../apps/deep/deep/assessments.py)); a **polygonless assessment applies everywhere** ([assessments.py:171-172](../../apps/deep/deep/assessments.py), asserted by [tests/test_region_features.py:71-73](../../apps/deep/tests/test_region_features.py)).

**The 8 state SQTs** are built into DEEP from the STAF metric library source (`docs/assets/data/metric-library/detailed-adapted-assessments.json`) via `scripts/build_deep_data.py`, with `applicability` as a plain string and **no region/polygon**, so they currently apply nationwide. **ECBP v1** is the only published library assessment (author "demo", "seed for map-layer verification") and is the sole polygon in the baked registry; **Northeastern Highlands** is a `latestVersion:0` placeholder (not baked).

**Library + publisher** (context for Part E): `apps/library` is catalog + per-assessment manifest + `vN/{assessment.deep.json, session.streamcurves.json, meta.json}`, all `schemaVersion=1`, with **no lifecycle/status, no fingerprints, no embedded scoring contract**. Versioning is sequential integers; the only hash anywhere is an MD5 of input data stored inside the session file. StreamCurves' `publish_version` ([streamcurves/library.py:184-288](../../apps/stream-curves/streamcurves/library.py)) writes the version and regenerates `catalog.json` from manifests (so any hand-added catalog status is clobbered). `suspend_when_hidden` is a StreamCurves concern, not DEEP. There is no shared `libs/` package (stub only), so cross-app reuse is by copy.

---

## Part A — Assessment selection redesign (full-width, library-only)

**A1. Move the Assessment selector into a full-width SFARI-style workspace** (reuse the `sfari-worksheet` scaffolding DEEP already has for Measure). Layout: site context (location, resolved **state + Level III ecoregion**), matching assessment cards, and a selection detail panel (name, region, version, preliminary/certified, update date, required metrics + functions, source citation), with a clear preliminary warning / certified indicator, and a disabled Continue plus an explanation when nothing matches.

**A2. Ecoregion + state resolution in DEEP (net-new).** Add `apps/deep/data/ecoregions_l3.geojson` + `us_states.geojson` and a `deep/geo.py` mirroring StreamCurves' `state_at` / `locate_polygon_property` point-in-polygon resolvers (boundary-inclusive so a point on an official boundary is not rejected). This is a copy (no shared `libs/` yet), consistent with the repo's mirror-the-contract convention; flag it as a future `staf-core` consolidation candidate.

**A3. Spatial matching + selection.** Require the snapped point be covered by the assessment's published polygon (Level III / state / custom). If multiple cover the point, show all eligible matches and require an explicit selection, ordering **certified first, then preliminary**. Default to the latest certified; if only preliminary matches exist, select the latest preliminary and keep a persistent preliminary-use warning. Block progression (with explanation) when no covering published assessment exists.
- **Sequencing gate:** dropping the "polygonless applies nationwide" rule ([assessments.py:171-172](../../apps/deep/deep/assessments.py)) depends on the SQTs first receiving polygons (Part E, StreamCurves plan). Until then, retain a clearly-labeled "no defined area of applicability" national fallback so DEEP is not left empty.

**A4. Remove upload + arbitrary-path handoff.** Delete `input_file("upload_assessment")` + `_upload_assessment` ([app.py:927,1016-1031](../../apps/deep/app.py)) and the `?handoff=<path>` opener ([app.py:449-452](../../apps/deep/app.py)). Keep and extend the id-based deep link to **assessmentId@version**, applying the same spatial + lifecycle eligibility checks. Consequence to resolve in the StreamCurves plan: this breaks StreamCurves' desktop "Prepare draft for DEEP" (`?handoff=`) preview loop, so a replacement author preview (publish-as-preliminary then open by id@version, or a library-mediated local draft opened by id, never by arbitrary path) is a Part E item.

---

## Part B — Detailed Measure workspace

**B1. Expand the three-area worksheet:** a discipline/function navigator showing complete / provisional / missing / N-A / warning states; metric cards for detailed evidence and scoring; a rollup panel for function scores, completeness, warnings, and ECI.

**B2. Per-metric record** (one scored value per metric, no replicate aggregation): metricId + selected stratum; entered value/unit and normalized scoring value/unit; origin (manual or desktop-generated); method, source/citation, observation date, reviewer note; optional PDF/JPEG/PNG attachments with filename, media type, size, checksum; N/A state + required N/A reason; curve domain, interpolated score, and warning state. Attachments persist in the session, capped at 10 MB per file and 25 MB total; reports list attachment names + checksums rather than embedding contents.

**B3. Live recompute + provenance.** Recalculate metric, function, condition band, and ECI immediately on any value/unit/N-A/stratum change (extend the existing client-side interpolation in [www/measure.js](../../apps/deep/www/measure.js) plus the `scored()` reactive at [app.py:1136-1141](../../apps/deep/app.py)). Function scores come from the assessment contract; no manual function-score override. Show how each score was obtained: active curve, input position, interpolation result, criterion/stratum, and any endpoint clamp.

**B4. Completeness states + report gating.** Distinguish **complete** (every required metric has a value or accepted N/A), **provisional** (at least one scored, required metrics unresolved), and **not_evaluable** (no valid evidence). Permit provisional reports with prominent completeness + missing-evidence warnings; disable Report until at least one metric is scored (an empty assessment is not evaluable and never displays ECI 0.00). Keyboard accessible, text-labeled state changes, `aria-live` recalculation messages, no color-only indicators.

---

## Part C — Scoring correctness fixes

**C1. Endpoint-clamp warning.** Keep clamping out-of-domain values, but return/flag a warning ([curves.py:44-47](../../apps/deep/deep/curves.py)) and surface + export it (mirror in server `curves.py` and client `measure.js`), instead of silently clamping.

**C2. Rescope the auto-measure cache key.** Key `computed_for` by (site/delineation, assessmentId, version, content digest) and reset it in `_do_reset()` ([app.py:802-816](../../apps/deep/app.py)) and on session load ([app.py:1486-1529](../../apps/deep/app.py)), fixing the stale skip at a new site.

**C3. Embedded-contract-preferring reader hook (consumer side).** Keep DEEP's current `curves.py`/`scoring.py` rules as the documented fallback, but design the scoring entry to **prefer an embedded scoring contract when the bundle carries one** (the contract itself is defined and written upstream, Part E). This is only the consumer hook; no contract is authored in this plan.

---

## Part D — Sessions, reports, reproducibility

**D1. Session schema v2 + migration hook.** Bump `SCHEMA_VERSION` to 2 and add the version check + migration path that `load()` currently lacks ([session.py:31-38](../../apps/deep/deep/session.py)). Store: snapped site identity + resolved region (state + Level III); assessmentId, version, lifecycle (preliminary/certified), and a content digest; the exact inlined bundle (already inlined today, keep it); metric evidence, normalized values, strata, attachments, notes, warnings; completeness + provisional/final result state. Since fingerprints are upstream, DEEP computes a local digest over the inlined bundle at save time as its reproducibility stamp, and prefers the publisher's canonical digest once present.

**D2. Migrate v1 sessions** by reconstructing the current scoring rules and preserving the embedded bundle, marking provenance unavailable where legacy data cannot supply it. A v2 session may resume its exact embedded version even if later retired/superseded, clearly labeled historical and not selectable for a new site.

**D3. Enrich reports.** Add assessment name/version/status, region match, content digest, completeness, scoring warnings, metric provenance, and selected-stratum source to PDF/CSV/GeoJSON ([report.py](../../apps/deep/deep/report.py)). Preliminary and provisional designations stay visible in every output.

---

## Part E — Cross-plan dependencies (owned by the StreamCurves / library plan)

Explicitly **not** built here; DEEP consumes these once they exist. Listed so the StreamCurves plan picks them up:

- **Library contract additions** the publisher must write: an assessment **status** field (preliminary/certified) that survives `catalog.json` regeneration ([streamcurves/library.py:146-181](../../apps/stream-curves/streamcurves/library.py)); catalog `latestPreliminary` / `latestCertified` / `defaultVersion` pointers; a canonical content digest on the published bundle; and (if pursued later) an embedded versioned scoring contract. Keep lifecycle two-state per the confirmed decision.
- **Lifecycle writer:** define who sets certified (an explicit publisher action or a reviewed manifest edit). EcoPCX review is external; DEEP only reads the resulting status.
- **Migrate the 8 state SQTs into the library** as preliminary versions with **state applicability polygons** (resolved via `streamcurves/geo.py` from `us_states.geojson`), then remove the built-in polygonless entries in DEEP and the "polygonless = national" rule. **This unblocks DEEP's A3 spatial hard-block.**
- **Remove the ECBP "demo" seed** from the published catalog/manifest and the baked `apps/deep/data`; retain the Northeastern Highlands placeholder until the StreamCurves pilot publishes.
- **Bake changes** ([apps/deep/scripts/bake_library_into_deep.py](../../apps/deep/scripts/bake_library_into_deep.py)): copy all eligible preliminary/certified versions (not just latest) and the required Level III/state geometry into DEEP's isolated deployment.
- **Replacement author draft-preview** to fill the removed `?handoff=` gap (open by id@version or a library-mediated local draft, never an arbitrary path).

**DEEP-side interim stubs so DEEP stays shippable before Part E lands:** read an optional status field defaulting to preliminary; prefer an embedded scoring contract if present else current rules; accept `assessmentId@version` deep links; keep the labeled national fallback for polygonless assessments.

---

## Part F — Test and Acceptance Plan

- **Spatial:** Level III / state / custom polygon, boundary point, overlapping regions, missing polygon, no-match hard block, certified-first ordering, and versioned deep links that cannot bypass region/lifecycle eligibility.
- **Scoring:** deterministic interpolation, unit normalization, endpoint-clamp **warning** (not silent), partial functions, condition bands, ECI; empty assessments remain not_evaluable (never ECI 0.00); version + site + digest cache isolation; immediate recalculation after edits.
- **Workspace/session:** navigation/status behavior, single-value editing, notes, attachments (size caps, checksums), accessibility, report gating; schema v1->v2 migration and v2 round-trip; a historical session stays reproducible after the library default changes or its version is retired.
- **Exports:** PDF/CSV/GeoJSON include version, lifecycle, region match, digest, completeness, provenance, and warnings; preliminary/provisional stay visible.
- **Regression + visual:** existing Identify, Basin, desktop measurement, mapping, curve figures, and exports still work; a repeated library bake is deterministic. Smoke-test certified, preliminary, no-match, partial, complete, and restored-historical sessions in browser and STAF Desktop.
- **Commands:** from `apps/deep` `python -m pytest`; syntax-check changed JS; run applicable root JS tests; verify a repeated bake is deterministic.

## Suggested Sequencing

- **D1** ecoregion/state resolution (`deep/geo.py` + data) + spatial matching + no-match block (with the interim national fallback).
- **D2** Assessment selector redesign (full-width SFARI style) + remove upload/handoff + `assessmentId@version` deep link.
- **D3** Measure workspace expansion (metric records, attachments, provenance, completeness states).
- **D4** Scoring fixes (clamp warning, cache-key rescope, embedded-contract-preferring hook).
- **D5** Session v2 + migration + reproducibility stamp.
- **D6** Reports enrichment.
- **D7** Tests + acceptance.
- Cross-plan Part E items land in the StreamCurves session; DEEP's spatial hard-block flips on once the SQT-polygon migration is baked.

## Verification (when built)

- From `apps/deep`: `python -m pytest`, then `shiny run app.py --port 8003` and drive it via the Browser pane tools: pick a certified vs preliminary assessment by location, confirm the no-match block, enter metrics (manual + desktop), observe an endpoint-clamp warning, save and reload a v2 session, and export reports carrying version/status/region/digest. Confirm a repeated `bake_library_into_deep.py` run is deterministic. Smoke-test in STAF Desktop.

## Assumptions and Exclusions

- EcoPCX review occurs outside DEEP; DEEP only consumes the resulting status. DEEP does not create validation records or certification packages.
- Preliminary assessments are usable for new work but stay conspicuously labeled.
- User-uploaded bundles and arbitrary server-path handoffs are intentionally removed.
- Library schema v2 / lifecycle-writer / fingerprints / embedded scoring contract / SQT migration are owned by the StreamCurves plan (Part E); this plan builds only DEEP's consumer side and stays shippable via the interim stubs.

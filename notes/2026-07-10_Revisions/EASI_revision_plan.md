# EASI Batch Processing + StreamCurves Screening — Detailed Revision Plan

_Supersedes the earlier draft in this folder. Developed from `EASI_brainstorming.md` against the
current `apps/easi` and `apps/stream-curves` code (2026-07-10). SFARI, DEEP, and StreamCurves get
their own plans alongside this one. Integration approach: in-process vendoring (not the earlier
HTTP API)._

## Context

The `EASI_brainstorming.md` asks for two capabilities:

1. **Batch processing** — submit one or more point locations (each with a site ID), run the
   complete EASI assessment for every point, and produce (a) a readable per-site report, (b) a
   combined summary of all points, and (c) a structured, machine-readable output that carries
   results, functional classifications, warnings, and missing-data indicators.
2. **StreamCurves reference-site screening** — let StreamCurves submit candidate reference-site
   locations to the EASI batch processor, get results back in a consistent machine-readable form,
   flag sites meeting reference-condition criteria, auto-retain qualifying sites, and preserve
   excluded sites with their exclusion reasons for review.

**What prompted this / key reconciliation.** An earlier draft of this plan proposed an
authenticated HTTP API between EASI and StreamCurves (`EASI_BATCH_API_TOKEN`, `STAF_APPS_ROOT`).
That approach is dropped. Posit Connect Cloud's bot gate returns HTTP 403 to server-to-server
requests (the `*.share.connect.posit.cloud` URLs 403 any non-browser caller), so cloud-to-cloud
HTTP between the two deployed apps cannot work. **Confirmed decision: integration is in-process.**
StreamCurves vendors the EASI engine as a package and imports it directly, kept honest by a sync
script plus a CI drift gate. No HTTP, no tokens, no `STAF_APPS_ROOT`.

**Intended outcome.** EASI keeps its single-site workflow as the default and gains a batch
workspace and a headless, reusable, vendorable engine. StreamCurves gains an EASI screening step
in its import wizard that runs that same engine locally and retains only qualifying candidates.

## Current State (grounded in code)

**EASI single-site flow** (all reusable orchestration lives in the engine, not in UI reactives):
- Entry points in [pipeline.py](../../apps/easi/easi/pipeline.py): `delineate_only(lat, lon, reach_ft, comid=None)` (line 31), `assess_only(ctx_inputs, metric_ids, sources, overrides, progress)` (line 92), and the one-shot `run_analysis(lat, lon, reach_length_ft, overrides)` (line 108, "kept for scripts/tests"). `run_analysis` does **not** accept `comid` and snaps the point itself; only `delineate_only` takes a COMID.
- `assessment.assess()` ([assessment.py:27](../../apps/easi/easi/assessment.py)) is async: prefetch (streamcat, nlcd, wbd, threedep) via `asyncio.gather`, then run selected metric adapters concurrently, then `_finalize()` rollup. Result `report` dict carries `metricRows`, `functionScores`, `subIndices`, `ecosystemConditionIndex`, `computedCount`, `totalCount`, `crossSection`, `basin`.
- Scoring ([scoring.py](../../apps/easi/easi/scoring.py)): `function_score = round(index*15)` clamped 0-15; `ecosystem_condition_index = mean(physical, chemical, biological sub-indices)`. Bands in [config.py:30-35](../../apps/easi/easi/config.py): index bands at 0.39 / 0.69 (Non-Functioning / Functioning-at-Risk / Functioning); function-score bands at 5 / 10.

**Batch precedent already exists.** [scripts/run_sfari_sites.py](../../apps/easi/scripts/run_sfari_sites.py) runs EASI over many sites: an async loop calling `pipeline.run_analysis(...)` wrapped in `asyncio.wait_for(timeout=220)` with one retry and `sleep(3)` (line 52-67), resumable per-site JSON, and CSV rollups. `easi_functions(rep)` (line 70) reshapes `metricRows` into a per-function dict — a model for the machine-readable output.

**Datasources never raise** ([datasources/__init__.py:5-6](../../apps/easi/easi/datasources/__init__.py), [metrics/base.py:8-9](../../apps/easi/easi/metrics/base.py)): a failed source degrades to a row with `status="unavailable"`; the run always completes. `nas`/`nid_barriers` distinguish "unavailable" (`None`) from "genuinely none present" (`[]`). Retry today is scattered and per-source (streamcat 2 retries + mirror failover; mmw poll backoff; attains fallback chain; others fail-fast). **No shared `requests.Session`**; each call is an independent short-timeout `requests.get/post`.

**Two vendoring hazards, confirmed:**
- [config.py:16](../../apps/easi/easi/config.py): `DATA_DIR = Path(__file__).resolve().parents[1] / "data"`. Vendoring only the `easi/` package into StreamCurves makes `parents[1]` StreamCurves' root, breaking every JSON/GeoJSON load. `bieger._GEOJSON` ([bieger.py:58](../../apps/easi/easi/bieger.py)) has the same parent-relative assumption.
- The HyRiver on-disk cache env (`HYRIVER_CACHE_NAME`, `HYRIVER_CACHE_EXPIRE`) is set in [app.py:20-22](../../apps/easi/app.py), **not** in the engine. Any headless import (batch runner or StreamCurves) loses it and falls back to a `./cache` in the cwd.

**EASI UI** ([app.py](../../apps/easi/app.py), ~1953 lines) is Shiny for Python **Core**: a single full-bleed ipyleaflet map with a floating left pane driven by a 4-step stepper (`STEP_IDENTIFY / BASIN / CONFIGURE / REPORT`). Long work uses `@reactive.extended_task` + `anyio.to_thread.run_sync` (delineate_task line 953, assess_task line 958), paired with a `@reactive.effect` that reads `.status()`/`.result()`. Live progress is a shared plain dict `_assess_prog` polled by `reactive.invalidate_later(0.3)` (line 1145). Map layers are single-slot (`_add_layer` removes the same-key layer first, line 722-734) so there is no multi-marker pattern today. Overlays are added inside post-display reactive effects and forced to paint with a `fit_bounds` nudge (not `on_flushed`). Downloads (`dl_pdf`/`dl_csv`/`dl_geojson`, line 1932-1948) export the current single assessment via [report.py](../../apps/easi/easi/report.py) `build_pdf`/`build_csv`/`build_geojson`. State is single-assessment; there is no save/load and no multi-site memory.

**StreamCurves** ([apps/stream-curves](../../apps/stream-curves)) is a `page_navbar` app; its import wizard ([views/import_map.py](../../apps/stream-curves/views/import_map.py)) is a 7-step flow: **1 Region -> 2 Add data -> 3 Confirm sites -> 4 Choose metrics -> 5 Compile -> 6 Classify -> 7 Review & build** (`N_STEPS=7`, `_next` keyed on `cur==2/4/6`). A "site" is a DataFrame row (no Site class); the external key is the wizard `site_id`. Sessions are JSON at `SCHEMA_VERSION=1` with an empty `_MIGRATIONS` map ([session_io.py:32,325](../../apps/stream-curves/streamcurves/session_io.py)); save is generic over `SESSION_FIELDS`, restore is explicit per field. The existing "preserve all rows, exclude some" mechanism is `site_masks` ([workbook.py:522-635](../../apps/stream-curves/streamcurves/workbook.py)) but it has **no exclusion-reason field**. Long work uses detached `asyncio` tasks + a lock-guarded `task_flush` (no `extended_task`, no cancellation). **StreamCurves imports nothing outside its own directory in production and has zero HyRiver imports** (datasources are pure `requests`). `screening.py` here means stratification screening (Kruskal-Wallis), unrelated to reference-site screening.

**Dependency reality for vendoring.** The two apps' `requirements.txt` share 9 packages at **identical** pins (shiny 1.6.3, shinywidgets 0.8.1, ipyleaflet 0.20.0, anyio 4.13.0, numpy 2.4.6, pandas 3.0.3, matplotlib 3.11.0, plotly 6.8.0, requests 2.34.2) — **no conflicts**. But EASI's engine pulls the entire HyRiver + geospatial stack (pynhd, py3dep, pygeohydro, pygeoutils, pygeoogc, async-retriever, hydrosignatures, geopandas, pyogrio, shapely, pyproj, rasterio, rioxarray, xarray) plus reportlab — roughly 15 heavy binary-wheel deps that StreamCurves currently lacks. StreamCurves' MMW gating already mirrors EASI's (`MMW_API_KEY` env, else gitignored `scripts/.mmw_api_key`).

---

## Part A — EASI Batch Engine (headless, reusable, vendorable)

Goal: a stable, UI-free surface that runs the full assessment for N sites, with structured
results/warnings, bounded concurrency, retry classification, and cancellation. This is what both
the EASI batch UI and StreamCurves call.

**A0. E0 parity harness FIRST (lock current scoring before any refactor).**
- Add `apps/easi/tests/test_batch_parity.py`: record golden `report` outputs for a small fixed set of stubbed sites (reuse the offline stubbing in [tests/test_progress.py](../../apps/easi/tests/test_progress.py) `_stub_offline`), asserting ECI, sub-indices, and per-metric ratings/scores are byte-stable. This is the regression net for A1-A6. Extend the existing scoring goldens in [tests/test_scoring.py](../../apps/easi/tests/test_scoring.py) (all-Good ECI 0.87, all-Fair 0.53, all-Poor 0.20) rather than replacing them.

**A1. Stable `easi.batch` API surface** (new subpackage `apps/easi/easi/batch/`).
- `easi.batch.api` exposes: `capabilities() -> dict` (engine version, metric IDs, source options, band thresholds, criteria vocabulary) and `run_batch(request: BatchRequest, *, on_event=None, cancel=None) -> BatchResult`, plus a single-site `run_site(site: SiteRequest, ...) -> SiteResult`.
- Internally wraps the existing `pipeline.delineate_only` + `assess_only` (preferred over `run_analysis` so a supplied COMID can be honored). Keep `pipeline.run_analysis` working unchanged for existing scripts/tests.
- Version the surface with an `ENGINE_API_VERSION` constant so the vendor drift gate can assert compatibility.

**A2. Path + cache portability (load-bearing for vendoring).**
- Add a resolution chain in [config.py](../../apps/easi/easi/config.py): `EASI_DATA_DIR` env var, else an in-package `easi/data/` if present, else the current sibling `parents[1]/data`. Apply the same chain to `bieger._GEOJSON`. This lets the vendored package carry or point at its data without a sibling `data/` dir.
- Move the HyRiver cache env defaulting out of `app.py` into an engine-level `easi.batch.runtime.ensure_cache()` (idempotent, honors an existing `HYRIVER_CACHE_NAME`) called at the top of `run_batch`/`run_site`, so headless and vendored callers get a writable cache dir. `app.py` keeps its early call for the interactive path.

**A3. Versioned, JSON-serializable contracts** (`easi/batch/contracts.py`, dataclasses with `to_dict`/`from_dict` and a `SCHEMA_VERSION`).
- `SiteRequest`: `site_id` (supplied or generated), `lat`, `lon`, optional `comid`, `reach_length_ft` (default 1000), `snap_tolerance_ft` (default 150, max 1000), `source_choices`, `overrides`, arbitrary passthrough `metadata`.
- `BatchRequest`: ordered `list[SiteRequest]`, a config snapshot (metric set, defaults), and an optional criteria snapshot.
- `MetricResult` (extend the current row): raw + display value, units, generated vs final rating, index, function score + band, confidence, source mode, availability, and a structured `missing_reason`.
- `SiteResult`: `site_id`, processing `state` (queued / running / succeeded / partial / failed / cancelled), delineation summary, metric results, raw (unrounded) ECI + sub-indices for qualification alongside 2-decimal display values, ordered `issues` (structured, not free text), a `completeness` block that counts defaulted evidence separately from genuinely unavailable evidence, and the qualification decision.
- `BatchResult`: ordered `list[SiteResult]`, run diagnostics (timings, retries, timeouts, throttling), the config + criteria snapshots, and generated ID assignments.
- ID rules: retain unique supplied IDs; generate collision-free `SITE-0001` for blanks; reject duplicate supplied IDs. Normalize coordinates to 6 decimals for exact-result reuse; different IDs at an identical normalized location may share computation but each gets a distinct result; nearby (non-identical) locations are never silently deduplicated.

**A4. Diagnostics side channel for retry classification** (`easi/batch/diagnostics.py` or extend an `easi/diagnostics.py`).
- Because datasources never raise, add a `contextvars`-based `ServiceOutcome` recorder the datasources write to (service name, HTTP status, latency, retry count, throttle flag). The batch scheduler reads it to decide whether to retry a site (transient timeout / HTTP 429 / HTTP 5xx) versus accept a partial result. This is additive and must not change scoring.

**A5. Bounded scheduler with retry and cancellation** (`easi/batch/runner.py`).
- Process at most **two sites concurrently**; within that, at most one terrain/raster (3DEP) call and at most two other external calls in flight (a small semaphore set).
- Retry transient timeout / 429 / 5xx **once** with backoff + jitter; honor `Retry-After`; reduce concurrency after repeated throttling.
- Cooperative cancellation via a `cancel` token: stop scheduling new sites, preserve completed results, mark queued sites `cancelled`, and ignore stale late completions.
- Emit `on_event` progress for validation, snapping, delineation, shared-data retrieval, metrics, reporting, and qualification. Reuse the existing shared-`progress`-dict idiom (`assess_only` already updates one; [app.py:1145](../../apps/easi/app.py) already polls one).
- Target: complete 150 sites within about 90 minutes under normal service conditions (mocked in tests; only small live smoke batches).

**A6. Qualification / criteria model** (`easi/batch/qualify.py`).
- Recursive, versioned AND/OR criteria over: raw ECI, raw sub-indices, function scores/bands, per-metric rating/confidence/availability/source mode, and completeness fields.
- Skip unavailable predicates; an empty rule is `not_evaluable`. Impose no fixed evidence floor, but flag prominently when partial evidence qualifies a site.
- Two presets: **Functional** = raw ECI > 0.69; **Reference condition** = raw ECI > 0.69 AND every available sub-index > 0.69 AND every available function score > 10. (0.69 aligns with EASI's own `INDEX_BANDS` upper boundary.)
- Keep automatic decisions (`qualified` / `excluded` / `not_evaluable`) and final decisions (`retained` / `excluded` / `pending`) separate; allow audited reviewer overrides in either direction with an optional explanation.

Critical files: new `apps/easi/easi/batch/` (api, contracts, runner, qualify, diagnostics, runtime); edits to [config.py](../../apps/easi/easi/config.py), [bieger.py](../../apps/easi/easi/bieger.py), and the datasource modules (add ServiceOutcome writes); leave [scoring.py](../../apps/easi/easi/scoring.py) and [assessment.py](../../apps/easi/easi/assessment.py) scoring math untouched.

---

## Part B — EASI Batch UI (single-site stays default)

A new full-width batch workspace, reachable by a header toggle ("Single site" | "Batch") that swaps
the `page_fillable` body; the existing stepper stays the default view.

- Batch steps: **Import -> Configure -> Criteria -> Run -> Results**.
- **Import**: paste-a-table plus file upload (CSV, TSV, Excel), with column mapping (ID / lat / lon / optional COMID / passthrough metadata), validation, and an ipyleaflet preview of all points (this is where EASI gains a genuine multi-marker map, drawn post-display with the existing `fit_bounds` nudge). Cap at 150 sites; reject 151+.
- **Configure**: batch-wide defaults (1000 ft reach, 150 ft snap tolerance configurable to 1000, current primary source choices, all 20 metrics).
- **Criteria**: pick a preset or build an AND/OR rule (Part A6).
- **Run**: launch via `@reactive.extended_task` wrapping `easi.batch.run_batch`; show filterable per-site progress driven by `on_event` + the poll idiom; support cancel.
- **Results**: filterable per-site table (state, ECI, bands, qualification, completeness, warnings) plus a **full per-site editor** (rating overrides, notes, cross-section edits, the ft/m unit toggle, and individual source/reach reruns). "Unit changes" means only the cross-section ft/m toggle plus reach-length reruns; nothing else in EASI has units. Re-evaluate qualification after every edit while preserving revision-zero generated values and subsequent rerun history.
- Keep unfinished batches in the current session only; warn before destructive reset/navigation. Reuse existing CSS patterns (`.easi-tbl`, `.easi-basin-card`, `.easi-summary-plots`, band colors from `scoring.py`).

Critical files: [app.py](../../apps/easi/app.py) (body toggle, batch reactives, new render/download handlers), `apps/easi/www/` (a `batch.js` for table/column-map interactions mirroring the existing `report-edit.js` `Shiny.setInputValue` channel pattern, plus CSS). Bump cache-bust versions for any changed still-referenced JS/CSS per guardrail 2.

---

## Part C — Exports (ZIP)

- A single "Export batch" download producing a ZIP: `manifest.json`, `batch-results.json` (compact: no heavyweight geometries, terrain profiles, or base64 images), `summary.csv`, `metrics.csv`, `exclusions.csv`, `run-diagnostics.json`, and per-site artifacts (JSON + the existing `build_pdf`/`build_csv`/`build_geojson` outputs).
- Include every submitted row, including failures, cancellations, exclusions, and manual overrides.
- Add IDs, generated vs final values, condition bands, completeness, warnings, and missing-data detail to the existing per-site export builders (small additive edits to [report.py](../../apps/easi/easi/report.py)).

---

## Part D — StreamCurves Integration (summary; full detail in the StreamCurves plan)

The mechanism and dependency cost are settled here so the EASI engine is designed to be vendored;
the StreamCurves UI wiring is planned in that app's own session.

- **Vendoring mechanism** (net-new; no precedent in the repo): a `apps/stream-curves/scripts/vendor_easi_engine.py` that copies the `easi/` package (and its data, honoring the A2 resolution chain) into `apps/stream-curves/streamcurves/_vendor/easi/`, plus a drift-gate test modeled on [tests/test_golden_parity.py](../../apps/stream-curves/tests/test_golden_parity.py) that fails when the vendored copy diverges from `apps/easi/easi` or when `ENGINE_API_VERSION` mismatches. Wire it into a new CI check (none exists today).
- **Dependency cost (decision to confirm during SC planning):** vendoring the full engine adds ~15 heavy geospatial deps to `apps/stream-curves/requirements.txt`. On desktop this is free (the payload already ships EASI's env). On StreamCurves' Connect Cloud deployment it materially enlarges the image. Open question for the SC plan: add the deps to StreamCurves' cloud env, or gate EASI screening to local/desktop only (like MMW is key-gated). Not blocking for EASI.
- **Where it lands:** a new wizard step after "Confirm sites" (insert at index 4, bump `N_STEPS` to 8, add a `_STEP_LABELS` entry and `body()` branch, and shift the hard-coded `cur==2/4/6` checks in `_next`). The vendored engine self-delineates, so it does not depend on Compile's COMID snap.
- **Data model:** preserve all candidate rows; enrich only retained rows; derive legacy `site_masks` for excluded/pending rows (adding an exclusion-reason field, which `site_masks` lacks today). Add `easi_screening_sites` / `easi_screening_metrics` / `easi_screening_criteria` tables keyed by the external wizard `site_id`, in both the workbook (specs/columns/normalize + editor support) and the session. Bump `SCHEMA_VERSION` to 2 with a `_MIGRATIONS[1]` that injects empty EASI-screening state; add `AppState` fields, `SESSION_FIELDS` entries, and explicit restore lines.
- Support both directly-run screening and import of a finalized EASI ZIP; re-evaluate imported evidence against current StreamCurves criteria while retaining imported provenance.

---

## Part E — Testing & Acceptance

- **Engine:** parity harness (A0) green throughout; contracts round-trip (JSON schema + golden); ID generation/duplicate rejection; exact-location reuse; 150-site accept / 151 reject; failure isolation; bounded concurrency; retry classification (timeout/429/5xx) via ServiceOutcome; cancellation + stale-task suppression; qualification boundary tests (exact 0.69, nested AND/OR, unavailable predicates, empty rule, partial/defaulted evidence, edit-driven requalification, reviewer overrides).
- **UI:** per-site editor isolation; regression of the unchanged single-site workflow; ZIP layout, filename safety, complete summaries.
- **Integration:** vendor drift gate; pre-Compile retention; retained-only enrichment; stable-ID-derived masks; workbook/session round-trips; schema v1->v2 migration.
- **Run commands (per guardrail 4/7):** EASI tests from `apps/easi`; StreamCurves tests from `apps/stream-curves` with `-m "not live"`; `npm test` + jekyll build only if site data changes (not expected); `dotnet test desktop\Staf.Desktop.slnx` if payload/env changes.
- Use mocked 150-site performance tests; only small live smoke batches; review collected diagnostics before considering higher concurrency or bulk-fetch optimization.

## Suggested Sequencing

- **E0** parity harness (A0) -> **E1** contracts + config/cache portability (A2, A3) -> **E2** `easi.batch.api` wrapping existing pipeline (A1) -> **E3** diagnostics side channel (A4) -> **E4** scheduler + retry + cancel (A5) -> **E5** qualification model (A6) -> **E6** export builders (Part C).
- **U1-U5** the five batch UI steps (Part B), each behind the header toggle so single-site stays shippable.
- **U6** per-site editor + requalify-on-edit. **U7** ZIP export wiring.
- StreamCurves vendoring + screening step is its own plan/session (Part D).

## Dependency & Deployment Notes (guardrails)

- Match StreamCurves' `openpyxl==3.1.5` if EASI's export path needs Excel (today EASI has no Excel dep; `sfari_data.py` reads xlsx via stdlib zipfile). Any new EASI pin triggers `desktop/payload/env.lock` regen (guardrail 10) and a desktop-payload **prerelease** (guardrail 8).
- Preserve all `.posit/publish/deployments/` records (guardrail 5). This plan lives under `notes/`, never `docs/` (GitHub Pages publishes `docs/`).
- No em dashes in any user-visible copy (existing EASI copy already violates this in places; new batch copy should comply).

## Open Questions (non-blocking; resolve during build or SC planning)

1. StreamCurves cloud: add EASI's ~15 geospatial deps to its cloud env, or gate EASI screening to local/desktop only? (Part D.)
2. Batch paste/upload: is Excel input required for EASI batch import, or are CSV/TSV + paste enough (avoids adding openpyxl to EASI)?
3. Should the batch UI reuse the modal report per site, or render per-site reports inline in the Results table? (Leaning inline, with the existing modal reachable per row.)

## Verification (when built)

- Engine: `cd apps/easi && python -m pytest -q` (parity + contract + scheduler + qualify suites); a scripted `run_batch` over the SFARI fixture sites compared against the current `run_sfari_sites.py` outputs for scoring parity.
- Batch UI: `shiny run app.py --port 8000`, drive Import (paste + CSV), Run a 3-site batch, cancel mid-run, edit a site, export the ZIP, and confirm layout via the Browser pane tools.
- StreamCurves (its session): `cd apps/stream-curves && python -m pytest -q -m "not live"` including the vendor drift gate; run the wizard through the new screening step.

# StreamCurves Regional Assessment Automation and Library Publishing Revision Plan

_Reconciled against the current `apps/stream-curves` + `apps/library` + `apps/deep` code
(2026-07-10). This is the convergence plan: it carries StreamCurves' own brainstorming, the EASI
reference-condition screening consumer side (EASI plan Part D), and the library-v2 / lifecycle-writer
/ SQT-migration / ECBP-removal / bake / draft-preview work delegated by the DEEP plan (DEEP Part E).
Confirmed decisions: (1) add the guided workflow **alongside** the retained Advanced 4-phase tools;
(2) **full build** (durable checkpointing, validation records, public/restricted packaging + gating,
full 5-state lifecycle). Supersedes the earlier draft in this folder._

## Context

`StreamCurves_brainstorming.md` asks for four things: streamline the reference-curve dataset-building
workflow and prepare it for automation; add a final reference-condition screening step that sends
candidates to the EASI batch processor and retains only qualifying sites; streamline and automate
regional reference-curve development, focused on Level III ecoregions, and run a Northeastern
Highlands pilot; and add a preliminary/validation/lifecycle model with publishing to the shared
assessment library.

**Convergence.** StreamCurves is the writer/producer in the STAF system, so this plan also owns:
- the EASI screening consumer side (the EASI engine is vendored in-process per the EASI plan);
- the shared library schema v2, the lifecycle **writer**, content fingerprints, the embedded scoring
  contract, the 8-SQT-into-library migration with state polygons, the ECBP-seed removal, the bake
  changes, and a safe replacement for DEEP's removed `?handoff=` draft preview (all delegated by DEEP
  Part E).

**Lifecycle reconciliation with DEEP.** StreamCurves (writer) tracks the full five-state lifecycle
(preliminary / under_review / certified / revised / retired) plus validation state; DEEP (consumer)
displays only preliminary vs certified, and **only preliminary and certified versions are eligible
for new DEEP assessments** (under_review / revised / retired are history/admin). This is consistent
with the DEEP plan's "simple preliminary/certified" consumer decision.

**EASI dependency split (resolves the EASI plan's open question).** The draft already supports both
direct batch screening and import of a finalized EASI ZIP. Direct screening uses the vendored EASI
engine and its ~15 heavy geospatial dependencies, so it runs **local/desktop only** (the shared venv
and desktop payload already carry those deps); the **cloud deploy supports ZIP-import only**, so it
stays lean. The pilot performs the initial direct run once, finalizes, exports the ZIP, and later
reruns import that exact evidence.

## Current State (grounded in code)

**Import wizard** ([views/import_map.py](../../apps/stream-curves/views/import_map.py)): 7 steps `Region -> Add data -> Confirm sites -> Choose metrics -> Compile -> Classify -> Review & build` (`N_STEPS=7`, `_next` keyed on `cur==2/4/6`). A "site" is a DataFrame row; sites dedup within 50 m ([sites.py:58-96](../../apps/stream-curves/streamcurves/sites.py), `tol_m=50`). Region is captured at step 1; `geo.region_polygon_geometry` resolves ecoregion/state polygons from `data/ecoregions_l3.geojson` (`US_L3CODE`) and `us_states.geojson` (`state`). NRSA candidates come from `data/nrsa_sites.csv` (1,908 rows, NRSA 2018-19); `nrsa_in_region` filters by `us_l3code`/state/polygon. **Ecoregion 58 (Northeastern Highlands) has exactly 71 candidates.**

**Analysis** is a 4-phase workflow ([views/state.py:40-45](../../apps/stream-curves/views/state.py)): `Exploratory` (Kruskal-Wallis screening), `Cross-Metric Analysis` (consistency heatmap), `Verification` (LOESS/feasibility), `Reference Curves`. It is driven from the summary-table hub ([views/summary_page.py](../../apps/stream-curves/views/summary_page.py)) via a per-metric modal ([views/analysis_workspace.py](../../apps/stream-curves/views/analysis_workspace.py), navset tabs) with a "Recompute All." Reference curves use the **empirical IQR-seed method** (`build_reference_curve`, 5-point Q25/Q75/IQR geometry, [curves.py:1242-1340](../../apps/stream-curves/streamcurves/curves.py)); `interp_curve` is byte-compatible with DEEP's. `models.py` best-subsets regression is a **separate workstream not used by the curve path**; `regional_curve.py` is the separate hydraulic bankfull feature (leave unchanged).

**Diagnostics** ([precheck.py](../../apps/stream-curves/streamcurves/precheck.py)) give per-metric `pass/caution/fail/no_data`; the summary rolls up to `Incomplete/Failed/Complete with Warnings/Complete`. There is **no persisted per-metric review queue** with the six states the plan needs, and analysis is effectively all-or-nothing (a metric is in `completed_metrics` or not). Configurable thresholds exist but are scattered: `min_sample_size` (per-metric, `config/metric_registry.yaml`, default 10), `min_group_size` (`config/stratification_registry.yaml`, default 5), significance/support sliders (phase 2). Recompute is reactive plus a coarse signature check `{data_fingerprint, config_version, decision_type, selected_strat}` over **in-memory** caches ([summary_state.py:805-822](../../apps/stream-curves/views/summary_state.py)); a whole-dataset fingerprint change invalidates all metrics. **No durable cross-session checkpoints.** The long-task pattern is detached `asyncio` + a lock-guarded `task_flush` with **no cancellation**.

**Publish** ([streamcurves/library.py](../../apps/stream-curves/streamcurves/library.py), [views/library.py:348-429](../../apps/stream-curves/views/library.py)): gate is `writable()` + `app_data_loaded` + `discipline_function_mapping_confirmed` (publish itself is **not** desktop-gated; only the draft-handoff test button is). Versioning is sequential integers; `_regenerate_catalog` rebuilds `catalog.json` from manifests (so a hand-added catalog field is clobbered). **`STAF_LIBRARY_PUBLISH` does not exist.** Exports are single-file only (xlsx list-of-metrics, xlsx SQT curves, HTML science support, `.deep.json` bundle); there is **no ZIP, no redaction, no public/restricted split**. `data_fingerprint` is an MD5 of the input data, stored in the session only. Sessions are `SCHEMA_VERSION=1` with an **empty `_MIGRATIONS`** and 49 `SESSION_FIELDS` (generic save via `dump_session_fields`, explicit restore via `restore_session_into_state` in [views/data_overview.py:405-500](../../apps/stream-curves/views/data_overview.py)). Workbook has 8 required + 3 optional sheets; `site_masks` is row-number keyed with **no exclusion-reason field**. **No validation/lifecycle/EcoPCX concept exists** (net-new). The 8 state SQTs are **not** in stream-curves (they live in `docs/assets/data/metric-library/detailed-adapted-assessments.json`); ECBP + NH placeholders live in `apps/library`.

---

## Part A — Guided workflow + durable run state

- **Guided 5-stage workflow becomes the default**, with the current 7-step wizard + 4 analysis phases retained as an **Advanced** workspace over the same run state: `1 Region & Sources -> 2 Candidate Sites & EASI Screening -> 3 Enrichment, Build & Classification -> 4 Curve Analysis & Flagged Review -> 5 Preliminary Package & Publish`.
- **Versioned `RegionalAssessmentRun`**: run id, timestamps, app/method versions, status; region + source snapshots; ordered candidates + stable external IDs; EASI screening evidence/criteria/decisions; intermediate source results + normalized metric values; classification outputs, curve proposals, diagnostics, reviewer decisions; intended metric scope, publication readiness, and lifecycle draft.
- Processing states `not_started/running/succeeded/partial/failed/cancelled`; structured issues with stable code, severity, stage, source, site/metric id, retryability, message.
- **Fingerprint every stage's material inputs**; reuse a completed checkpoint only when its fingerprint matches. Upgrade the current coarse whole-dataset fingerprint ([data_overview.py:366](../../apps/stream-curves/views/data_overview.py)) to per-stage/per-item fingerprints, with deterministic invalidation: region/source/candidate/screening changes invalidate enrichment and later; enrichment/classification changes invalidate analysis + publication; scope/analysis-setting changes invalidate affected curve decisions + publication; lifecycle-only changes do not alter curve content.
- Cooperative cancellation, targeted retry, resume from the last valid checkpoint; preserve successful site/source results when another item fails. **Persist checkpoints in the session + workbook**, not only reactive memory (the current pattern has no cancellation, so build it in).

## Part B — Region, candidates, EASI screening

- Guided workflow **requires a Level III ecoregion**; state/custom/no-region remain Advanced-only. Boundary-inclusive validation: keep outside-region rows in the candidate table, mark them `rejected` with `outside_region`, and provide **no override**.
- **Remove the 50 m proximity dedup** ([sites.py:58-96](../../apps/stream-curves/streamcurves/sites.py)); preserve distinct sites at identical/nearby coordinates when IDs differ. Retain unique supplied IDs, generate deterministic collision-free IDs for blanks, reject duplicate submitted IDs.
- Freeze resolved source versions, selected columns, metric IDs, region geometry, and input-file digests in the run configuration.
- **EASI Screening step immediately after candidate confirmation**, using the EASI plan's contracts and the **vendored EASI engine** (`vendor_engine.py` + CI drift gate per EASI Part D): direct batch (local/desktop) or import of a finalized EASI ZIP (anywhere, including cloud). Functional preset default (raw ECI > 0.69); optional advanced predicates over subindices, function scores, metrics, availability, source mode, completeness; separate automatic and final reviewer decisions; audited overrides in either direction. **Never auto-weaken the ECI threshold** when too few sites qualify.
- Preserve every candidate and its screening result; only finally retained sites continue to enrichment. Store stable `easi_screening_sites`, `easi_screening_metrics`, `easi_screening_criteria` tables keyed by external site id; derive legacy row masks from stable ids. For pilot reproducibility, perform the initial direct EASI run, finalize decisions, export its ZIP, and permit later reruns to import that exact evidence.

## Part C — Enrichment, classification, intermediate data

- Resolve the pilot source preset to recommended NRSA response metrics + StreamCat context/predictor metrics + required NLDI identifiers + drainage-area fields. Distinguish scored response metrics from context, predictor, identifier, and classification fields.
- Isolate failures by site and source so one unavailable service does not discard the full build. Add **bounded concurrency + source-aware retry** for transient timeout / HTTP 429 / HTTP 5xx (honor `Retry-After`); the current Compile ([import_map.py](../../apps/stream-curves/views/import_map.py) `_run_compile`) offloads each source via `asyncio.to_thread` with `announce()` toasts and no retry. Display progress by source, site count, success, unavailable, retry, and failure.
- Preserve original candidates, screening decisions, raw/normalized source responses (where licensing permits), normalized long-form metric values, classification inputs/results, and structured issues + timing diagnostics.
- Add coordinated **schema-v2 session/workbook tables** for workflow runs, candidates, source results, normalized metrics, EASI screening, curve diagnostics, reviewer decisions, validation records, and lifecycle audit. Migrate schema-v1 sessions to v2 with empty workflow/screening state while preserving the current workbook, masks, configuration, and analysis results (the `_MIGRATIONS` map is empty today; add `_MIGRATIONS[1]`). Continue reading legacy row-number masks but generate new masks from stable external site ids.

## Part D — Automated curve development + review queue

- Use the current empirical reference-distribution method ([curves.py](../../apps/stream-curves/streamcurves/curves.py) IQR seed) and **freeze its method version + settings** per run. Do not introduce a new model-selection method (`models.py` stays out of the curve path, as today).
- Automate data preparation, grouping/stratification, descriptive statistics, curve construction, scoring transformations, figures, and diagnostics. **Separate automated proposals from reviewer decisions**: recomputation may replace a proposal but must not silently overwrite a reviewer's historical decision.
- Give every intended metric one status: `pending / auto_finalized / review_required / reviewer_finalized / blocked / removed_from_scope` (net-new; today a metric is in `completed_metrics` or not). Auto-finalize only when curve generation succeeds and there is no blocking diagnostic. Route to review for stable reasons (insufficient sample, excessive missingness, degenerate distribution, unresolved mapping/stratum, invalid curve shape, unstable classification, analysis failure), mapping to the existing precheck / decision / feasibility / stability outputs.
- Use the existing configurable thresholds (`min_sample_size`, `min_group_size`, significance/support) and **serialize their resolved values**; do not invent new scientific thresholds in the pilot. Flagged-metric reviewer actions: adjust data/config and rerun; accept the proposed curve with a required rationale; or remove the metric from scope with a required rationale and rerun affected stages.
- **Make the intended response-metric list explicit** before analysis (today it is implicit via `eligible_summary_metrics`); removing a metric is a recorded scope change, not an unexplained publication gap. Do not permit publication while any intended metric is pending/blocked/awaiting review. Unchanged fingerprint preserves decisions; a changed proposal fingerprint archives the previous decision and requires a new review. Keep the Advanced 4-phase controls + Recompute All, but drive them through the same proposal/decision states (recompute is not human verification).

## Part E — Validation + lifecycle (writer)

- Add standalone **validation records** per assessment version: record id + version; validation site identity + coordinates; collection date, team/source, protocol; field observations + metric data; findings, limitations, recommended revisions, attachment references.
- Treat exact validation locations, raw data, and attachments as **restricted**; expose only aggregate counts, date range, methods summary, and current validation status in public artifacts.
- Validation states `not_started / in_progress / validated`. A designated maintainer marks a version validated only when at least one validation record exists (actor, timestamp, optional note; no automated adequacy threshold).
- Publish analytical content initially as **preliminary**. Certification is permitted only for a validated version and requires a checked confirmation that external EcoPCX certification is complete, recording actor/timestamp/optional note. Do **not** implement EcoPCX submission packages, contacts, milestones, or remote status tracking.
- Curve/scoring revisions create a new **preliminary** version with `supersedesVersion`; a prior certified version stays available until explicitly retired. The full lifecycle (`preliminary/under_review/certified/revised/retired`) is available for history/admin, but only preliminary and certified versions are eligible for new DEEP assessments.

## Part F — Library schema v2 + public/restricted packages (owns DEEP Part E)

- Upgrade the shared library to **schema v2** while retaining v1 readers. Store immutable per-version analytical artifacts: `assessment.deep.json`, a redacted `session.streamcurves.json`, reference curves in JSON + CSV, analysis summary + diagnostics, redacted reference data, and immutable metadata + checksums.
- Store lifecycle/validation state in a **separate append-only audited status record**; status changes regenerate manifest/catalog pointers and DEEP's baked registry **without changing the analytical content fingerprint**. (This resolves the current issue that `_regenerate_catalog` would clobber any hand-added status field.)
- Extend catalog/manifest records with latest-preliminary, latest-certified, default-version pointers, a validation summary, lifecycle history, a content digest, and supersession links. **Embed a scoring-contract snapshot + fingerprint in the DEEP bundle** (this is the embedded contract DEEP's consumer hook prefers).
- Produce two deterministic export packages: a **public library package** (curves, scoring contract, figures/summaries, generalized provenance, redacted reproducibility session, redacted analytic reference data) and a **restricted review package** (full candidates, identifiers, coordinates, COMIDs, per-site EASI evidence, raw validation data, reviewer logs, source diagnostics, complete editable session/workbook). In public reference data: replace source ids with opaque sequential ids; remove coordinates, COMIDs, original ids, free text, per-site EASI evidence, and raw validation records; retain only analytic metric values, strata/classes, and fields required to reproduce published curves. **Never write the restricted package into `apps/library` or bake it into DEEP** — record only its checksum and a public-safe summary.
- Canonical publishing gate (net-new): enabled only when StreamCurves runs against a verified repository checkout with `STAF_LIBRARY_PUBLISH=1` set, and requires the maintainer's audit name (no hosted authentication). Today's gate is only `writable()` + confirmed mapping. Hosted/ordinary desktop/browser users export packages but cannot mutate the canonical library. Package import is idempotent by assessment id + content fingerprint + intended version.
- On canonical publish: write a new immutable preliminary version, update manifest + catalog, rebuild DEEP's baked registry, and validate public/restricted separation + checksums. On lifecycle-only updates: modify audited status/catalog and rebake DEEP without a new curve version.
- **Migrate the 8 state SQTs** into `apps/library` as preliminary entries with state applicability polygons (from `us_states.geojson` via `geo.py`; source is the STAF metric library's `detailed-adapted-assessments.json`). **Delete the ECBP demonstration seed**; preserve the Northeastern Highlands placeholder until the pilot publishes its first preliminary version.
- **Bake changes** ([apps/deep/scripts/bake_library_into_deep.py](../../apps/deep/scripts/bake_library_into_deep.py)): copy all eligible preliminary/certified versions (not just latest) and the required Level III/state geometry into DEEP's isolated deployment. This unblocks DEEP's spatial hard-block (DEEP plan A3). Provide the replacement author draft-preview for DEEP's removed `?handoff=` (a library-mediated draft opened by `assessmentId@version`, never an arbitrary path).

## Part G — Northeastern Highlands pilot (L3 58)

- Select Level III ecoregion 58; freeze its official polygon and the bundled NRSA 2018-19 input version. Expect ~71 initial candidates (treat a count change as a review signal, not a hard requirement). Reject outside-region candidates and preserve their rejection records.
- Apply the Functional EASI rule (raw ECI > 0.69) without relaxing it for sample size. Enrich retained sites via the NRSA/StreamCat/NLDI/drainage preset. Run the empirical curve method for every intended response metric. Review only flagged metrics, record rationales, and finalize the complete intended set.
- Publish a preliminary library version only if every intended curve is finalized, every diagnostic flag is resolved, function mappings + scoring contracts validate, screening + provenance are complete, and public redaction checks pass. If the retained sample cannot support all intended curves, record the failure + required intervention; do not publish a partial assessment or lower the EASI threshold.
- Produce a pilot retrospective (elapsed time by stage, service failures/retries, exclusions, manual interventions, flagged metrics, reruns, recommended automation). Use the pilot to stabilize reusable Level III presets; nationwide multi-region execution is a subsequent rollout, not part of this work.

## Part H — Test and Acceptance Plan

- **Candidate/screening:** blank/duplicate ids, identical/nearby coordinates, region boundaries, outside-region rejection, stable ordering; direct and imported EASI evidence, Functional boundary behavior, advanced criteria, overrides, failed sites, preservation of every candidate.
- **Workflow:** checkpoint serialization, matching fingerprints, downstream invalidation, resume, cancellation, targeted retry, failure isolation, deterministic ordering; session/workbook v1->v2 migration and v2 round-trips.
- **Analysis:** deterministic empirical curves + diagnostics; auto-finalization of clean metrics; flagged-review decisions with required rationales; archived superseded decisions; publication blocking; explicit scope removal + rerun.
- **Package/privacy:** deterministic public/restricted ZIPs + checksum validation; assert public files contain no coordinates, original ids, COMIDs, per-site EASI evidence, raw validation data, restricted free text, or attachments; confirm the public package reproduces published curves from its redacted analytic data.
- **Lifecycle/library:** preliminary publication, validation audit, certification gate, lifecycle-only changes, material revisions, retirement, default-version pointers; the canonical-publish environment gate and hosted export-only behavior; schema-v1 compatibility and DEEP consumption of v2 preliminary/certified versions.
- **Pilot acceptance:** a mocked L3 58 end-to-end run plus a small live smoke run; every intended metric finalizes or publication stays blocked; the resulting preliminary assessment is discoverable in DEEP only within the L3 58 polygon and remains visibly preliminary.
- **Commands:** `python -m pytest -m "not live"` from `apps/stream-curves`; `python -m pytest` from `apps/deep` for the cross-app contract fixtures + the vendored-EASI drift gate; syntax-check changed JS + run applicable root JS tests; rebuild DEEP's baked registry and verify a repeated build is deterministic; small live API smoke tests only.

## Suggested Sequencing

- **SC1** schema v2 session/workbook + migration + run-state scaffolding (durable checkpoints).
- **SC2** guided 5-stage shell alongside Advanced.
- **SC3** EASI vendoring + screening step (direct local/desktop + ZIP import) + `easi_screening_*` tables.
- **SC4** enrichment failure isolation + retry/concurrency + intermediate persistence.
- **SC5** curve automation + review queue over the existing phase-4 empirical method.
- **SC6** validation records + full lifecycle writer.
- **SC7** library v2 + public/restricted packages + `STAF_LIBRARY_PUBLISH` canonical gate + bake changes + SQT migration + ECBP removal.
- **SC8** NH L3 58 pilot end-to-end + retrospective.
- **SC9** tests + acceptance. (DEEP's Part E consumption unblocks once SC7 lands.)

## Verification (when built)

- `python -m pytest -m "not live"` from `apps/stream-curves`, then `shiny run app.py --port 8012` and drive the guided workflow via the Browser pane tools: region L3 58 -> 71 NRSA candidates -> EASI screening (ZIP-import on cloud, direct on desktop) -> enrichment -> curve automation + flagged review -> preliminary package -> publish (`STAF_LIBRARY_PUBLISH=1`, local) -> confirm `apps/library` v2 + DEEP rebake, and that the assessment is discoverable in DEEP only within L3 58 and marked preliminary. Verify the public package carries no coordinates/ids/COMIDs/per-site evidence and the restricted package never enters `apps/library`. Confirm a repeated bake is deterministic. Smoke-test in STAF Desktop.

## Dependency and Deployment Notes (guardrails)

- Vendoring the EASI engine adds ~15 geospatial deps used only by **direct** screening, kept local/desktop (shared venv + desktop payload already carry them); the cloud deploy stays lean via ZIP-import. Any new dependency pin triggers `desktop/payload/env.lock` regen (guardrail 10) and a desktop-payload **prerelease** (guardrail 8).
- Guardrail 11: publishing writes `apps/library/` and rebakes DEEP; commit `apps/library/**` and `apps/deep/data/**`, then redeploy DEEP. Preserve all `.posit/publish/deployments/` records (guardrail 5). Plans stay under `notes/`, never `docs/`.
- No em dashes in user-visible copy.

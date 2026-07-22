# SFARI Field Forms and Desktop Evidence Revision Plan

_Reconciled against the current `apps/sfari` code (2026-07-10) and two confirmed decisions:
(1) overlay pulled desktop values onto the form pages' Notes rows (not metadata-only), and
(2) relocate the five form JPEGs into `apps/sfari/data/FieldForm/` rather than adding a `/docs/`
subpath to the Posit allowlist. Supersedes the earlier draft in this folder._

## Context

`SFARI_brainstorming.md` asks to rename the **Get desktop metrics** button to **Get Field Forms**
and have it emit one combined, print-ready PDF: a five-page field-form packet first, then the
completed desktop-metrics summary appended at the back, with consistent page numbering, site
identifiers, headers, and formatting throughout.

Developing that out (confirmed with the user): the five form pages are "blank" only of assessor
scoring, they are prefilled with derived site identity **and** with each function's pulled desktop
values printed into that function's Notes/Other Metrics row. The full desktop-metrics table becomes
the appendix. This makes the packet a genuine field aid: the assessor carries the pulled evidence
next to the metric being scored.

Two real defects surfaced during verification and are folded into scope because the feature depends
on them: (a) SFARI's **Save/Open is entirely broken** by a module/parameter name collision, and
(b) the five form JPEGs **do not currently ship to the deployed web app** (the Posit bundle excludes
`/docs/`).

## Current State (grounded in code)

**Workflow** ([app.py:55-57](../../apps/sfari/app.py)): a 4-step stepper `Identify -> Basin -> Field review -> Report` (`STEP_IDENTIFY/BASIN/REVIEW/REPORT`). Identify/Basin render in a left pane beside the map; on `review`/`report` a full-width 3-column worksheet overlay (`sfari-worksheet`, [app.py:1005-1022](../../apps/sfari/app.py)) replaces it: a left nav rail (`sfari-nav`), a center function panel (`sfari-fnpanel`), and a right rollup rail (`sfari-rollup`).

**The button + modal + download** (all to rename):
- Raw `<button>"Get desktop metrics"` with `data-desktop-metrics="1"`, classes `sfari-btn sfari-nav-desktop`, on the left nav rail ([app.py:1013-1017](../../apps/sfari/app.py)). JS bridge `data-desktop-metrics` -> `send("desktop_metrics_evt", {})` ([field-review.js:42-44](../../apps/sfari/www/field-review.js)).
- Handler `@reactive.event(input.desktop_metrics_evt)` -> `ui.modal_show(_desktop_metrics_modal())`, modal title `"Desktop metrics"`, body id `sfari-desktop-metrics`, a static 6-column preview table, footer `ui.download_button("dl_desktop_metrics", "Download PDF")` ([app.py:1470-1522](../../apps/sfari/app.py)).
- `@render.download(filename="sfari-desktop-metrics.pdf")` -> `report.build_desktop_metrics_pdf(delin() or {}, evidence())` ([app.py:1684-1686](../../apps/sfari/app.py)).
- CSS `.sfari-nav-desktop` ([styles.css:604-607](../../apps/sfari/www/styles.css)) and `#sfari-desktop-metrics` sticky header ([styles.css:831-832](../../apps/sfari/www/styles.css)). Cache-busts today: `styles.css?v=15`, `field-review.js?v=6` ([app.py:294,298](../../apps/sfari/app.py)).

**Exports** ([report.py](../../apps/sfari/sfari/report.py), matplotlib-free, ReportLab Platypus, lazily imported): `build_csv` (line 45), `build_geojson` (line 77), full `build_pdf` (line 104, embeds site photos via `reportlab.lib.utils.ImageReader` on base64 JPEG data-URIs), and `build_desktop_metrics_pdf(delin, evidence) -> bytes` (line 214, filters `desktopSupportable`, title "SFARI Desktop Metrics"). **ReportLab is the only PDF lib; no pypdf/Pillow anywhere**, so a combined packet must be one ReportLab document, not a merge.

**Evidence contract** ([models.py:18-32](../../apps/sfari/sfari/models.py)): `EvidenceResult(metric_id, value, value_text, suggested_likert, confidence, source, source_url, status, note)`; `to_dict()` = `asdict`, `from_dict()` classmethod exists but is unused on load. **No compact/short value field exists** (so `field_value_text` is genuinely new). Live state is `evidence = reactive.value({})` keyed by metricId ([app.py:366](../../apps/sfari/app.py)).

**Metric catalog** ([data/sfari-metrics.json](../../apps/sfari/data/sfari-metrics.json), 82 metrics; [sfari-functions.json](../../apps/sfari/data/sfari-functions.json), 20 functions): exactly **26** carry `desktopSupportable:true` (`config.desktop_metrics()`, [config.py:100-101](../../apps/sfari/sfari/config.py); asserted `==26` in [tests/test_scoring.py:81](../../apps/sfari/tests/test_scoring.py)). Disciplines `("Hydrology","Hydraulics","Geomorphology","Physicochemistry","Biology")` ([config.py:108](../../apps/sfari/sfari/config.py)). Of the 26: **18 auto-pulled** by `evidence.REGISTRY` adapters ([evidence.py:322-342](../../apps/sfari/sfari/evidence.py)), **3 from the cross-section tool** (`client:"xscalc"`), and **5 manual/unimplemented**. Each metric also carries `metricStatement`, short `fieldStatement`, `likertCriteria`, `scale` (W/R), and a `desktopSource {adapter,client,field,label,url}`. The paper-form field statements are encoded in `FIELD_STATEMENTS` ([scripts/build_sfari_data.py:95-176](../../apps/sfari/scripts/build_sfari_data.py)); the 26-metric `DESKTOP` table at [build_sfari_data.py:181-208](../../apps/sfari/scripts/build_sfari_data.py). The nutrient metric `nutrient-cycling-n-p-concentrations` is `desktopSupportable:false` (so it is **not** in the 26 and not in the desktop PDF) but is still pulled as `ev_np` from WQP TN/TP.

**Automatic pull** ([app.py:952-990](../../apps/sfari/app.py)): a `@reactive.extended_task` `pull_task` -> `pipeline.pull_evidence_only` -> async `evidence.pull` (fan-out via `asyncio.gather` of `anyio.to_thread` calls, then 19 pure adapters). Progress is a shared `_pull_prog={"done","total"}` dict polled by `reactive.invalidate_later(0.4)`. **It runs at most once** (`_enter_review` gated on `not already`, [app.py:962-963](../../apps/sfari/app.py)); there is **no retry path**, and `_pull_done` does a **full replace** `evidence.set(res.get("evidence") or {})` ([app.py:989](../../apps/sfari/app.py)).

**Cross-section evidence** ([app.py:1629-1665](../../apps/sfari/app.py)): `_xs_attach` writes into the same `evidence` dict via `put(mid, vt, note)` with `source="Native cross-section hydraulics (Manning)"`, `source_url=""` (writes 4 metricIds). **XS entries are distinguished from automatic ones only by that `source` string** (no origin flag). A naive re-pull (full replace) would wipe them, so any Retry must merge and preserve the Manning-source keys.

**The five form assets** ([apps/sfari/docs/FieldForm/Page1-5.jpg](../../apps/sfari/docs/FieldForm)): **JPEG, 1700x2200 px, 200 DPI**, ordered Hydrology / Hydraulics / Geomorphology / Physicochemistry / Biology (verified by each page's section banner). Each has a **baked-in "Page 1 of 1" footer** and, per function, an empty Score cell and a "Notes/Other Metrics:" cell. **No layout manifest exists.** No code loads or renders them today (only a source comment at [build_sfari_data.py:88](../../apps/sfari/scripts/build_sfari_data.py)).

**Packaging** ([.posit/publish/sfari.toml:11-19](../../apps/sfari/.posit/publish/sfari.toml)): `files` allowlists only `/app.py`, `/requirements.txt`, `/.python-version`, `/sfari/`, `/www/`, `/data/`, `/.posit/publish/sfari.toml`. The whole `/docs/` tree (holding the git-ignored confidential `SFARI_Clean.docx`, [.gitignore:23-24](../../apps/sfari/.gitignore)) is excluded, so **the JPEGs do not reach the web app today**. The desktop payload stages from `git archive HEAD:apps` ([desktop/scripts/build-apps-payload.ps1:41](../../desktop/scripts/build-apps-payload.ps1)), so it **includes** the tracked JPEGs and **excludes** the ignored DOCX.

**Save/Open is broken** ([session.py](../../apps/sfari/sfari/session.py) + app): the server signature `def server(input, output, session)` ([app.py:352](../../apps/sfari/app.py)) shadows `from sfari import ... session ...` ([app.py:29](../../apps/sfari/app.py)), so `session.dump(...)` (save, [app.py:1670](../../apps/sfari/app.py)) and `session.load(...)` (open, [app.py:1696](../../apps/sfari/app.py)) call methods on the Shiny Session object and raise `AttributeError`. The **entire** Save and Open flow fails (latent: only fires on click; no round-trip test). Separately, `_load_session` ([app.py:1700-1704](../../apps/sfari/app.py)) never calls `xs_geom.set(...)`, so cross-section state is discarded on load. `SCHEMA_VERSION=1` exists ([session.py:12](../../apps/sfari/sfari/session.py)) but `load()` ignores it.

**Requirements** ([requirements.txt](../../apps/sfari/requirements.txt)): `reportlab==4.5.1`, `matplotlib==3.11.0`, `plotly==6.8.0`, HyRiver 0.19.x + geospatial stack; **no Pillow, no PDF-parse lib**. There is **no `apps/sfari/requirements-dev.txt`** (the report.py comment referencing one is stale). Tests are only `test_likert.py`, `test_scoring.py`, `test_xscalc.py`; no PDF or session tests.

---

## Part A — Evidence contract and readiness

**A1. Add `field_value_text` to `EvidenceResult`** ([models.py:18-32](../../apps/sfari/sfari/models.py)): optional `field_value_text: str = ""`, a concise self-identifying print value (`Impervious 12.3%`, `Roads 1.23 km/km2`, `Flow class intermittent`). Keep full `value_text` for the appendix and the app UI. Additive and backward compatible; do not bump the session schema.
- Populate it in the 18 automatic adapters ([evidence.py](../../apps/sfari/sfari/evidence.py)) and in the cross-section `put()` producer ([app.py:1644-1661](../../apps/sfari/app.py)).
- Legacy sessions lack it: at render time use a deterministic per-metric formatter, then a bounded `value_text` fallback ending `see appendix`.
- Normalize generated PDF text to print-safe glyphs (`km2`, `delta`, `tau`, `->`, ASCII hyphen) at render only, without changing stored evidence.

**A2. Retrieval readiness + states.** Track the pull as `not_started / running / succeeded / failed / loaded` with attempted/available/unavailable counts, derived from the existing extended-task status + `_pull_prog`. Recognize per-metric display states consistently in preview and appendix: **Available**, **Unavailable** (adapter ran, no usable value), **Local review required** (catalog source is manual), **Run cross-section tool** (unpopulated xscalc metric), **Additional hydraulic estimate required** (unpopulated regression metric), **Pending** (still running). Readiness = the automatic task has settled, not that all 26 have values (18 auto + up to 3 XS).

**A3. Retry desktop evidence** (new). A **Retry** action in the modal after failure/partial that re-runs the pull and **merges**: replace automatic entries (including new `unavailable` results) while preserving cross-section entries (keys whose `source == "Native cross-section hydraulics (Manning)"`). This directly fixes the latent full-replace hazard at [app.py:989](../../apps/sfari/app.py) (make the completion handler merge rather than overwrite).

---

## Part B — Combined field-forms PDF

**B1. `build_field_forms_pdf(delineation, evidence) -> bytes`** (new in [report.py](../../apps/sfari/sfari/report.py)). One ReportLab document, no merge dependency:
- Refactor the current desktop table into reusable Platypus flowables; keep `build_desktop_metrics_pdf()` as a thin compatibility wrapper (existing button path and any callers stay green).
- Two page templates: a **full-page form template** (pages 1-5) that draws the corresponding JPEG at exact Letter size via `ImageReader` (embed original JPEG bytes, do not recompress) then paints overlays; and a **flowable appendix template** (page 6+) for the Desktop Metrics Summary.
- Continuous `Page X of Y` via a two-pass **NumberedCanvas** (no new dep).

**B2. Versioned layout manifest** at `apps/sfari/data/FieldForm/manifest.json` (see Part D relocation), carrying: per-asset filename, order, dimensions (1700x2200), DPI (200), and checksum; page-to-discipline assignment; **Page-1 metadata rectangles** (Reach ID, Reach length, Coordinates); **per-function Notes/Other Metrics rectangles** on each page (the overlay targets); the **baked-in footer mask rectangle**; and compact labels + ordering for the 26 desktop metrics keyed to their function. Rectangles are measured against the 200-DPI rasters. **This measurement is the main net-new effort and the primary layout risk.**

**B3. Overlays.**
- Prefill **Page 1**: Reach ID `COMID <id>` (fallback: snapped lat/lon to 5 decimals if COMID missing), Reach length in feet (delineated), Coordinates (snapped lat/lon, 5 decimals). Leave Date and Assessor(s) blank.
- Running identity layer on every page: header `SFARI Field Packet`, stream name, canonical Reach ID; footer snapped coordinates + `Page X of Y`, all inside printer-safe margins. If the stream name overflows, shorten it but always keep the Reach ID; show the full name in the appendix.
- Mask only the baked-in `Page 1 of 1` footer rectangle per page; leave table borders and worksheet content intact.
- For each function **with available desktop evidence**: white-fill only the interior of its Notes/Other Metrics rectangle, redraw its borders, print `DESKTOP: <compact entries joined by " | ">` at 6 to 6.5 pt in the upper portion, then `NOTES:` with a blank writing line beneath. Never below 6 pt; overflow becomes `+N values - see appendix`. Leave Notes rows untouched when no successful evidence exists; missing/manual statuses live only in the appendix.
- Include **no** existing Likert ratings, function scores, or assessor notes on the form pages.

**B4. Appendix** begins on page 6 with **Desktop Metrics Summary** and the same header/footer: all 26 desktop-supported metrics exactly once (discipline, function, metric, desktop method, full value/status, linked source), header repeated on continuation pages, clickable source links preserved as annotations.

**B5. Output metadata + filename.** PDF title `SFARI Field Forms - <Reach ID>`; download `sfari-field-forms-comid-<id>.pdf`, falling back to a safe hemisphere-based coordinate filename when COMID is absent.

---

## Part C — UI rename and reactive modal

**C1. Rename every "desktop metrics" identifier to Field Forms** (button label `"Get Field Forms"`, `data-desktop-metrics` -> `data-field-forms`, event `desktop_metrics_evt` -> `field_forms_evt`, handlers `_open_desktop_metrics`/`_desktop_metrics_modal`, modal title, body id `sfari-desktop-metrics` -> `sfari-field-forms`, download id/label/filename, CSS `.sfari-nav-desktop`/`#sfari-desktop-metrics`, and the `field-review.js` channel). Keep the semantic helper `config.desktop_metrics()` and its test as-is (it names the metric flag, not the button). Bump cache-busts (`styles.css?v=16`, `field-review.js?v=7`).

**C2. Make the modal body reactive** (today it is built once at open): show live evidence progress with labelled text and `aria-live="polite"`; the 26-row preview with values and explicit statuses; a note that available values also appear in the function Notes rows; a disabled **Preparing field forms...** control while retrieval runs; enable **Download Field Forms PDF** after success, partial completion, or task failure; and a warning + **Retry** for incomplete results. Keep it keyboard-operable and avoid color-only status.

---

## Part D — Save/Open fix, asset relocation, packaging

**D1. Fix the Save/Open collision** (prerequisite for evidence round-trip). Alias the import to break the shadow, e.g. `from sfari import ... session as session_io` (or rename the import), and update the two call sites ([app.py:1670](../../apps/sfari/app.py) save, [app.py:1696](../../apps/sfari/app.py) open). This repairs the entire Save/Open flow, not just evidence.

**D2. Restore cross-section state on load.** In `_load_session` ([app.py:1700-1704](../../apps/sfari/app.py)) add `xs_geom.set(st.get("cross_section"))` so attached XS geometry survives a round trip. Support legacy evidence without `field_value_text` via the A1 fallback.

**D3. Relocate the form assets to `data/`.** `git mv apps/sfari/docs/FieldForm/Page{1..5}.jpg apps/sfari/data/FieldForm/` and add `manifest.json` there. This bundles them on both channels with no allowlist edit (`/data/` is already in `files`; desktop `git archive` already ships `data/`). Update the source-comment path at [build_sfari_data.py:88](../../apps/sfari/scripts/build_sfari_data.py). The confidential `SFARI_Clean.docx` stays in `docs/` (git-ignored, excluded from both channels) and is unaffected.

**D4. Compatibility.** Runtime dependencies are unchanged (ReportLab is already pinned), so **do not regenerate `desktop/payload/env.lock`** and no desktop-shell change is needed. Add a **dev-only** `apps/sfari/requirements-dev.txt` (`-r requirements.txt` + `pypdf`) for the structural PDF tests; Poppler stays a manual-only QA tool (no dependency). Update the SFARI deployment doc/README to note the field-form runtime assets now live in `data/FieldForm/`. Keep site identity derived, not persisted; do not bump the session schema.

---

## Part E — Test and Acceptance Plan

- **Asset/layout:** exactly five ordered 1700x2200, 200-DPI Letter images; manifest checksums match; every metadata/Notes rectangle stays within its raster; all 20 functions and 26 desktop metrics map exactly once; validation fails if an asset changes without a manifest review (checksum gate).
- **PDF structural (pypdf, dev-only):** `%PDF` signature, Letter media boxes, zero rotation, five form pages before the appendix; a normal fixture yields five forms plus the appendix and a long fixture paginates safely; identical site identity and continuous `Page X of Y` on every page; Page-1 metadata uses COMID + snapped coords including coordinate-only fallback; all 26 metrics appear once in the appendix; source links remain annotations.
- **Overlay:** successful evidence lands on the correct page and function row; unavailable/manual entries are omitted from form strips but retained in the appendix; compact text fits at >= 6 pt with the defined overflow fallback; assessor ratings, function scores, and notes never enter the packet. Pixel comparison of protected statement/Score-cell regions is a Poppler-rendered manual check.
- **Evidence/UI:** running retrieval blocks download; succeeded/partial/failed/loaded enable the correct actions; Retry merges without discarding cross-section evidence; modal updates while open and reports counts without treating missing metrics as complete; special characters, long stream names, missing COMIDs, and empty evidence render without `None`, clipping, or unsupported glyphs.
- **Regression:** the legacy desktop-only PDF wrapper, full report PDF, CSV, GeoJSON, scoring, Likert, and cross-section exports stay callable; **the newly repaired Save/Open round-trips** (new test), restoring evidence + cross-section + compact fallback.
- **Commands:** from `apps/sfari` `python -m pytest`; `node --check www/field-review.js`; `npm test --silent` where changed JS is covered by root tests; mocked evidence for deterministic PDF tests; a small live smoke via `scripts/acceptance.py`.

## Suggested Sequencing

- **S1** Fix Save/Open + restore XS on load + add a round-trip test (Part D1-D2) — unblocks everything and is a standalone bug fix.
- **S2** Add `field_value_text` + populate adapters/XS + readiness states + Retry-merge (Part A).
- **S3** Relocate assets + author the layout manifest with measured rectangles (Part D3, B2) — the long pole.
- **S4** `build_field_forms_pdf` + overlays + numbered canvas, `build_desktop_metrics_pdf` as wrapper (Part B).
- **S5** Rename to Field Forms + reactive modal (Part C).
- **S6** Tests + fixtures + visual acceptance (Part E).

## Verification (when built)

- Generate complete, partial, empty, and worst-case-long fixtures under `tmp/pdfs/`, render every page to PNG with Poppler, and inspect a contact sheet: form sharpness, masked legacy numbering, readable 6-6.5 pt overlays, intact borders, usable handwriting lines, appendix wrapping, live source links, consistent section transitions. Check Letter print preview at 100% scale (not "Fit to page").
- Smoke-test the renamed button, the reactive modal (progress -> enable -> Retry), and the repaired Save/Open dialog in both the browser deployment and STAF Desktop (drive via the Browser pane tools).

## Assumptions

- "Blank field forms" means assessor scores and assessor-entered content stay blank; derived site metadata and desktop evidence overlays are permitted (confirmed).
- Page numbering is continuous across forms and appendix, not restarting at the summary.
- Date and assessor remain manual paper entries.
- The full 26-metric desktop inventory stays reviewable in the appendix even when only a subset has generated values.

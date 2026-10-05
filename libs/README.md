# libs/

Shared packages that the four apps consume only through their own vendored
copies (`<app>/<pkg>/_vendor/`). Nothing under `libs/` is imported across
folders at runtime: every Posit deployment and the desktop payload stay
self-contained, and a per-app drift gate fails when a vendored copy diverges
from its source. There is no shared runtime package.

- `libs/site_engine/`: the STAF site engine (see below and its own README).
- `libs/staf_workbook/`: the workbook and scenario toolkit of EASI, SFARI and DEEP
  (see "The workbook and scenario toolkit" below).

## The two watershed engines

STAF computes watershed metrics two ways. Both are named here once; the apps,
the docs site (`docs/computation-engines.md`) and every provenance record use
this vocabulary.

| | StreamCat lookup engine | STAF site engine |
|---|---|---|
| Token | `streamcat` (DEEP bundle `predictorSource`, StreamCurves digests and CLI, EASI batch policy `streamcat-legacy`) | `site-engine` (`site_engine.ENGINE_ID`; `site-engine vX` in bundles); YAML source key `site_engine` |
| Version | The StreamCat vintage of each variable (for example NLCD 2019 land cover) | `site_engine.ENGINE_VERSION` (0.4.0), recorded by every consumer |
| Watershed | The NLDI basin of the NHDPlus V2 COMID, which is the watershed StreamCat summarized | The HR reach watershed: the drainage area of the NHDPlus HR reach the point snaps to, by catchment aggregation, area validated against that reach's published drainage area (the reach, not the point, is the outlet) |
| Values | EPA StreamCat precomputed catchment and watershed summaries, plus the EPA modeled integrity indices | Computed from source data: NLCD 2021 on the watershed and the 100 m riparian buffer (2001 impervious optional), TIGERweb roads (primary, secondary, local), NID dams in the polygon (normal storage, NID storage, density), SSURGO K area-weighted by SDA polygon intersection, EROM mean-annual flow and the derived runoff depth, the 3DEP cross-section |
| Coverage | The NHDPlus V2 network (about 1:100k) | Any NHD HR stream in CONUS, within the reach budget |
| Runtime | Seconds (one REST call per COMID, cached) | Usually well under a minute, up to about five minutes on a large basin within the interactive budget (3,000 reaches, 190 hops); every consumer caches per site |
| Reproducibility | A published dataset, citable by vintage | Deterministic for engine version plus pinned vintages, against live services |
| Cannot produce | Anything off the V2 network | The EPA modeled indices (HYD, SED, CHEM, CONN, TEMP, HABT, prG_BMMI; `provenance.PERMANENT_EXCLUSIONS`), NRSA field observations, precipitation and temperature normals, the catchment-scale AOI |
| Produces differently | | Runoff is EROM-derived and labeled not equivalent to StreamCat `runoffws` (`metrics/runoff.py`); the riparian buffer is built from the HR flowline tree, not the V2 `rp100` buffer |

## Which engine each app uses

The policy is fixed by the framework. The only user choice in the program is
the StreamCurves predictor source for assessment builders.

EASI, SFARI, and DEEP use one blue stream style by default, with optional
**StreamCat coverage** in Layers. Each requires a resolved source COMID for the
current selected point before starting new analysis. A failed lookup retains
the point and offers Retry; it cannot fall through as an absent COMID. The
shared `anchor.hydrolocation_snap` routine makes at most four routing requests:
one hydrolocation GET (30 seconds), then up to three flowtrace POSTs (60 seconds
each) after transient failures, with delays of 5, 10, and 15 seconds before the
successive retries. Progress is emitted before each delay.
The captured USGS flowtrace HTTP 400 `InvalidParameterValue` response is also
retryable when its description specifically reports an execution-time read
timeout from `api.water.usgs.gov:443`; ordinary parameter errors remain terminal.
Valid empty or malformed responses stop immediately. Current flowtrace
`nhdFlowline` responses use the supplied COMID and `intersection_point`; legacy Point responses remain
supported. Endpoint/attempt/status/timing and retry-decision diagnostics go to logs. Optional
source-highlight geometry has no effect on readiness. Existing saved results
remain readable when an imported assessment needs its source resolved.

| App | Watershed metrics | User choice | Where the engine shows |
|---|---|---|---|
| EASI | StreamCat lookup engine on covered NHDPlus V2 reaches. STAF site engine on any other NHD stream (batch policy `auto`, the default). The three COMID-keyed metrics on such a stream ride the nearest StreamCat reach whatever the drainage-area ratio, each labeled with the reach, the routed distance, and the ratio. `streamcat-legacy` reproduces the pre-2026-09 surrogate routing with its refusal past the 10x bound. Engine failure or refusal makes the watershed metrics unavailable with guidance, never a proxy. | None | Layer names, the snap card, the progress line, the basin card, the banner, per-row source labels, the Engine column of the exports |
| SFARI | STAF site engine for the HR reach watershed and every watershed value at every site. StreamCat lookup engine by COMID for the EPA modeled indices that exist only per NHDPlus V2 reach (flow permanence, dewatered segments, barriers, nutrients) and, labeled, for a watershed value the engine could not compute. On a stream outside V2 the COMID is the nearest StreamCat reach downstream, and every such value names it with the routed distance and the drainage-area ratio. Direct services answer the rest. The assessor scores; the evidence is labeled. | None | Evidence badges, tooltips, the map legend, the desktop metrics PDF, the report CSV |
| DEEP | Every click snaps to the high-resolution NHD. The STAF site engine computes the HR reach watershed at every site; auto-pulled values come from it for each metric whose own curve carries the engine `predictorSource` stamp, and from the StreamCat lookup engine by COMID for every other curve, the source it was fitted on (the train/serve pairing rule, read per metric). On a stream outside V2 the COMID is the nearest StreamCat reach downstream, and every such value names it with the routed distance and the drainage-area ratio, never withheld. | None, follows the bundle | The source row, the basis badge, the scoring advisory, the map legend, the exports, the field packet |
| StreamCurves | Predictor source: StreamCat lookup engine (default) or STAF site engine, selected in the region builder or with `--predictor-source`. An engine-sourced build also recomputes the scored landscape metrics with an engine analog (impervious, crop, wetland (woody plus herbaceous, one column since 2026-09-07; the two classes stay pickable and are re-sourced when a run selects them), road density, dam density, base-flow index from the USGS grid, road-stream crossings on the NHDPlus HR network) at every retained site and stamps those curves per metric. The reference screen runs on the lookup engine, keyed on each NRSA site's archive COMID (the reach the crew sampled) and falling back to the `streamcat-legacy` routing for a candidate without one; the pin and the COMID mode join the inputs digest under absence semantics. | Predictor source only | Manifest `inputs.predictor_source` (with `resourced_metrics`), bundle `predictorSource` (bundle-level, and per metric on the re-sourced curves), the science support report |

The score-level equivalence study (`libs/site_engine/scripts/score_equivalence_study.py`)
decides whether the two engines are interchangeable for scoring on covered
streams: rating agreement of at least 0.90 over the eight watershed metrics,
condition-class agreement of at least 0.90, and a median DEEP index shift under
0.05. It reported Outcome B: 30 of 30 sites ran on 2026-09-02: watershed-metric rating agreement 0.84 pooled (Eastern Corn Belt Plains 0.78, Northeastern Highlands 0.90) against the 0.90 bar, condition-class agreement 0.97, median DEEP index shift 0.013. The engines are not interchangeable,
so EASI keeps the StreamCat lookup engine on covered streams, DEEP keeps the
pairing rule (`deep/curves.py`, `ENGINE_PAIRING_MODE = "refuse"`), and
StreamCurves builds engine-sourced versions of the pilot assessments
(`--predictor-source site-engine`, which recomputes the predictors and the
scored landscape metrics alike). Summary: `notes/EASI_Report/analysis/
score_equivalence_study_2026-09.md`.

## Labels and tokens

- Display names in user-visible copy: "StreamCat lookup engine" and "STAF site
  engine" (`site_engine/naming.py`, imported from each app's vendored copy).
- Tokens never change: `streamcat`, `site-engine`, `site_engine` (YAML source
  key), `auto` and `streamcat-legacy` (EASI batch policy). DEEP reads an absent
  `predictorSource` as `streamcat`.
- Per-row engine tokens in EASI exports: `streamcat`, `site-engine`,
  `unavailable`, or empty for reach and point evidence no watershed engine
  touches.

## The workbook and scenario toolkit

`libs/staf_workbook` (standard library only, so it adds no runtime pins) gives the three
assessment apps one behavior and one look (owner, 2026-10-03):

- **Download-only workbooks and the leave-page guard** (`web.py`, `assets/unsaved-guard.js`):
  every download is a plain attachment link without `target`, served as
  `application/octet-stream`, so nothing opens a new page or previews a workbook; the page
  warns before it is closed or reloaded with unsaved work.
- **The header's actions** (`web.nav_actions`, `web.new_dialog`, `web.info_dialog`, the nav rules
  in `assets/staf.css`; owner, 2026-10-05): New, Open, Save, About and Help, one markup and one
  behavior in every tool. New asks before it clears work; About and Help close alike.
- **The STAF assessment file** (`assessment_file.py`): what Save writes and Open reads, one JSON
  structure in every tool: `format`, `formatVersion`, `tool`, `savedAt`, `delineation` (the
  site), `toolData` (what the tool brought to the site, shared by every scenario) and
  `scenarios` (every scenario with its own state). A tool opens only its own files and refuses
  a newer format; SFARI's and DEEP's older files open through their own legacy readers.
- **Scenarios** (`model/scenarios.py`, `web.scenario_bar`, `web.add_dialog`, `assets/scenarios.js`):
  "Existing Conditions" (fixed name) plus up to nine alternatives, each with its own copy of the
  app's state; the assessment file keeps each with its state. Add asks where a scenario starts
  (owner, 2026-10-05): a copy of any scenario (Existing Conditions by default) or Blank, which
  each app defines as what a fresh assessment of the site starts with.
- **Comparison and summary** (`model/compare.py`, `model/summary.py`, `web.comparison_table`,
  `web.summary_block`): ECI, sub-indices and every function with the change from Existing
  Conditions (DEEP's intervals compare as intervals); reach, tier, length, drainage area,
  stream order, NARS-9 and EPA Level I to III, named from `data/ecoregions.json`
  (`scripts/build_ecoregion_table.py`).
- **One metric row** (`web.metric_action`, `web.metric_actions`, `web.metric_check`,
  `web.scoring_criteria`, `assets/metric-rows.css`, `assets/metric-rows.js`; owner, 2026-10-04):
  the layout every function page gives its metrics. The name and its (i), a description, the
  evidence, then labeled buttons that are always shown (Scoring, Note, Photo, and DEEP's N/A);
  the rating or value in a 220px right column; the panels the buttons open span the row. The
  script opens and closes panels in the browser alone and keeps the note dot and the photo
  count current (`window.STAFMetricRows.sync(row)` after an app adds or removes a photo). The
  apps keep only their own parts (DEEP's value box and curve, SFARI's photo strip, EASI's
  method tables).
- **One workbook per assessment** (`assemble.py`, `xlsx/`, `sheets.py`): Summary, Existing
  Conditions, one live calculator per alternative, ReferenceCurves, then the template's other
  sheets. The app's calculator template is never changed; the toolkit edits the zip parts
  directly (never openpyxl load and save, which drops charts).

Each app keeps its own glue in `<pkg>/workbook.py` (and EASI's `easi/scenario_state.py`).
EASI lists both, and `_vendor/staf_workbook/`, as presentation code outside its method and
acquisition digests.

## Vendoring

After any change under `libs/site_engine/site_engine/`, re-run every
consumer's `scripts/vendor_site_engine.py` (easi, sfari, deep, stream-curves)
and commit the copies; after any change under `apps/easi/easi` or
`apps/easi/data`, re-run `apps/stream-curves/scripts/vendor_easi_engine.py`
(the vendored EASI carries its own nested engine copy). After any change under
`libs/staf_workbook/`, run `libs/staf_workbook/scripts/vendor_staf_workbook.py` (the package
into `<pkg>/_vendor/staf_workbook/` and the assets into `www/staf/` of easi, sfari and deep),
then `vendor_easi_engine.py`, whose EASI copy carries it. Drift gates:
`tests/test_site_engine_vendor.py` and `tests/test_staf_workbook_vendor.py` in each app and
`apps/stream-curves/tests/test_easi_screening.py`. Never hand-edit a `_vendor/` tree.

Keep this file and `docs/computation-engines.md` in step: both change in the
same commit.

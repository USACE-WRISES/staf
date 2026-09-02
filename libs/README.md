# libs/

Shared packages that the four apps consume only through their own vendored
copies (`<app>/<pkg>/_vendor/`). Nothing under `libs/` is imported across
folders at runtime: every Posit deployment and the desktop payload stay
self-contained, and a per-app drift gate fails when a vendored copy diverges
from its source. There is no shared runtime package.

- `libs/site_engine/`: the STAF site engine (see below and its own README).

## The two watershed engines

STAF computes watershed metrics two ways. Both are named here once; the apps,
the docs site (`docs/computation-engines.md`) and every provenance record use
this vocabulary.

| | StreamCat lookup engine | STAF site engine |
|---|---|---|
| Token | `streamcat` (DEEP bundle `predictorSource`, StreamCurves digests and CLI, EASI batch policy `streamcat-legacy`) | `site-engine` (`site_engine.ENGINE_ID`; `site-engine vX` in bundles); YAML source key `site_engine` |
| Version | The StreamCat vintage of each variable (for example NLCD 2019 land cover) | `site_engine.ENGINE_VERSION` (0.2.1), recorded by every consumer |
| Watershed | The NLDI basin of the NHDPlus V2 COMID, which is the watershed StreamCat summarized | The exact point watershed on NHDPlus HR by catchment aggregation, area validated against the published HR drainage area |
| Values | EPA StreamCat precomputed catchment and watershed summaries, plus the EPA modeled integrity indices | Computed from source data: NLCD 2021 on the watershed and the 100 m riparian buffer (2001 impervious optional), TIGERweb roads (primary, secondary, local), NID dams in the polygon (normal storage, NID storage, density), SSURGO K area-weighted by SDA polygon intersection, EROM mean-annual flow and the derived runoff depth, the 3DEP cross-section |
| Coverage | The NHDPlus V2 network (about 1:100k) | Any NHD HR stream in CONUS, within the reach budget |
| Runtime | Seconds (one REST call per COMID, cached) | Usually well under a minute, up to about five minutes on a large basin within the interactive budget (3,000 reaches, 190 hops); every consumer caches per site |
| Reproducibility | A published dataset, citable by vintage | Deterministic for engine version plus pinned vintages, against live services |
| Cannot produce | Anything off the V2 network | The EPA modeled indices (HYD, SED, CHEM, CONN, TEMP, HABT, prG_BMMI; `provenance.PERMANENT_EXCLUSIONS`), NRSA field observations, the base-flow index, precipitation and temperature normals, the catchment-scale AOI, road-stream crossings |
| Produces differently | | Runoff is EROM-derived and labeled not equivalent to StreamCat `runoffws` (`metrics/runoff.py`); the riparian buffer is built from the HR flowline tree, not the V2 `rp100` buffer |

## Which engine each app uses

The policy is fixed by the framework. The only user choice in the program is
the StreamCurves predictor source for assessment builders.

| App | Watershed metrics | User choice | Where the engine shows |
|---|---|---|---|
| EASI | StreamCat lookup engine on covered NHDPlus V2 reaches. STAF site engine on any other NHD stream (batch policy `auto`, the default). The three COMID-keyed metrics on such a stream ride the labeled nearest covered reach within the 10x drainage-area bound and are unavailable beyond it. `streamcat-legacy` reproduces the pre-2026-09 surrogate routing with its refusal. Engine failure or refusal makes the watershed metrics unavailable with guidance, never a proxy. | None | Layer names, the snap card, the progress line, the basin card, the banner, per-row source labels, the Engine column of the exports |
| SFARI | STAF site engine first for the exact watershed and its evidence, StreamCat lookup engine second (labeled with the reach it describes), direct services third. The assessor scores; the evidence is labeled. | None | Evidence badges, tooltips, the printed field packet, the appendix |
| DEEP | The exact watershed on HR-only sites; auto-pulled values from the StreamCat lookup engine for StreamCat-built bundles and from the STAF site engine when the bundle's `predictorSource` records engine predictors (the train/serve pairing rule). | None, follows the bundle | The source row, the basis badge, the scoring advisory, the exports, the field packet |
| StreamCurves | Predictor source: StreamCat lookup engine (default) or STAF site engine, selected in the region builder or with `--predictor-source`. An engine-sourced build also recomputes the six scored landscape metrics with an engine analog (impervious, crop, woody and herbaceous wetland, road density, dam density) at every retained site and stamps only those curves per metric. Base-flow index and road crossings stay StreamCat. The reference screen is pinned to `streamcat-legacy`. | Predictor source only | Manifest `inputs.predictor_source` (with `resourced_metrics`), bundle `predictorSource` (bundle-level, and per metric on the re-sourced curves), the science support report |

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

## Vendoring

After any change under `libs/site_engine/site_engine/`, re-run every
consumer's `scripts/vendor_site_engine.py` (easi, sfari, deep, stream-curves)
and commit the copies; after any change under `apps/easi/easi` or
`apps/easi/data`, re-run `apps/stream-curves/scripts/vendor_easi_engine.py`
(the vendored EASI carries its own nested engine copy). Drift gates:
`tests/test_site_engine_vendor.py` in each app and
`apps/stream-curves/tests/test_easi_screening.py`. Never hand-edit a
`_vendor/` tree.

Keep this file and `docs/computation-engines.md` in step: both change in the
same commit.

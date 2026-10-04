# HR slim builder

Turns the USGS NHDPlus HR VPU packages into the slim flowline and catchment files
the NHDPlus HR data app (`apps/hr-data`) serves. Local operator tool, never
deployed. Data root `D:\Data\nhdplus-hr\slim` (`HR_SLIM_ROOT`).

It streams each package straight out of its zip on the USGS S3 bucket with GDAL
(`/vsizip//vsicurl/...`): only the four tables it needs cross the network, and
nothing is unzipped or stored. The source is `StagedProducts/Hydrography/NHDPlusHR/VPU/Current/GDB/`,
the release the USGS NHDPlus HR MapServer serves (ids, the 15 attributes and
coordinates were checked equal against the service on five regions).

```bash
python tools/hr-slim/run.py inventory                 # 266 packages, 52 GB of zips
python tools/hr-slim/run.py convert 0710 0108 --workers 4
python tools/hr-slim/run.py report                    # sizes per region, scaled to the nation
python tools/hr-slim/run.py pack --tolerance 20       # copy into apps/hr-data/data
```

`convert` writes `data/lines_<vpu>.parquet`, `data/catchments<tol>_<vpu>.parquet`
(one per tolerance in `HR_SLIM_CAT_TOLS_M`, default 10 and 20 m),
`data/qa_<vpu>.parquet` (original geometry in three small boxes),
`parts/<vpu>.json` (manifest entry, size and accuracy statistics) and
`logs/<vpu>.log`, then merges `data/manifest.json`.

Recipe: network flowlines (`innetwork = 1` joined to their VAA and EROM rows, the
content of USGS layer 3) simplified 5 m with Douglas-Peucker; their catchments
coverage-simplified in EPSG:5070 with the VPU's outer edge left as is; everything
rounded to 1e-5 degree. Field names are matched case-insensitively (older packages
use CamelCase, the 2022 reprocessed ones lower case); dated packages keep their
date in the `.gdb` folder name.

## Version 2 (exact catchments)

Version 2 (`apps/hr-data/hrslim/fmt2.py`) downloads each package once instead of
streaming it (streaming a large zip re-inflates its tables on every seek), into
`D:\Data\nhdplus-hr\zips` (`HR_SLIM_ZIPS`; resumed with HTTP ranges, size checked),
unzips it to `<root>/work/<vpu>` and reads the four tables locally. Data root
`D:\Data\nhdplus-hr\slim2` (`HR_SLIM2_ROOT`).

```bash
python tools/hr-slim/run.py download 0710 0108                  # zips only
python tools/hr-slim/run.py convert2 0710 0108 --workers 2      # download, convert, merge the manifest
python tools/hr-slim/run.py manifest2                           # manifest and links2.parquet again
python tools/hr-slim/run.py --root D:\Data\nhdplus-hr\slim2 pack
python tools/hr-slim/scripts/accept_v2.py sizes                 # also trees, outline, snap
```

Recipe: network flowlines simplified 2 m (an assessment click must snap to the
same flowline as on the original lines); catchments detected on their source grid
(EPSG:5070 10 m at 5 m in the lower 48, EPSG:3338 5 m in Alaska; `hrslim/grid.py`),
encoded as shared borders, and checked to decode to the original polygons, area
and shape, before anything is written (the conversion fails otherwise). A region
takes 1 to 5 minutes and a few GB of memory; two workers suit a 64 GB machine.

## Precomputed values (bundle v2, Phase 3 pilot)

Sources go to `D:\Data\nhdplus-hr\sources` (`HR_SOURCES`; `sources.json` records what was fetched);
values to `<v2 root>\values` (exact) and `<v2 root>\values_lean` (the bundle's form), tables to
`<v2 root>\tables`, NHDPlus V2 to `D:\Data\nhdplus-hr\v2pilot`.

```bash
python tools/hr-slim/run.py sources nlcd nid-fs tiger wbd nwi gnatsgo gnatsgo-tables
python tools/hr-slim/run.py values landcover --workers 3    # land cover, impervious, riparian pieces
python tools/hr-slim/run.py values roads                    # also dams, extras
python tools/hr-slim/run.py values soils-patch              # current SSURGO where 2020 map units retired
python tools/hr-slim/run.py values soils
python tools/hr-slim/run.py values lean                     # encode and check the bundle's value files
python tools/hr-slim/run.py v2                              # NHDPlus V2 regions (EASI covered streams)
python tools/hr-slim/run.py tables streamcat-extra streamcat nid nas nwis wqp-backfill wqp-recent wqp-temp wqp
python tools/hr-slim/scripts/parity.py landcover            # also vectors, points, nas, streamcat, v2, nwi, basins
python tools/hr-slim/scripts/nwis_check.py                  # NWIS with long timeouts and retries
```

| Stage | Module | Output |
|---|---|---|
| land cover, impervious 2021 and 2001, riparian 100 m pieces | `zonal.py` | `lc2_`, `rip2_` |
| roads, road-stream crossings, NID dams (the FeatureServer the engine queries) | `vectors.py` | `roads2_`, `xings2_`, `dams2_` |
| soil K (gNATSGO map units, current SSURGO from Soil Data Access where they retired; K with the engine's SQL) | `soils.py` | `soils2_` |
| sinuosity, ATTAINS, HUC12, NWI 150 m strips, per flowline | `extras.py` | `extras2_`, `au2_` |
| the lean encoding of the value files | `leanpack.py` (`hrslim/lean.py`) | `values_lean/` |
| NHDPlus V2 regions (lines kept whole), gage-adjusted EROM, sinuosity | `v2.py` | V2 `lines2_`, `cats2_`..., `v2attrs2_` |
| StreamCat slices (float64, with the four columns the national pull lacks), NID points, NAS taxa, WQP TN and TP since 2015 and stream temperature since 2016, each result with its WQP identifier | `tables.py` | `streamcat2_`, `nid_points`, `nas_taxa`, `wqp_results`, `wqp_temperature`, `wqp_stations` |
| WQP stream temperature pull (monthly CSVs kept as the reference copy; `record(result_id)` reads a result's full record); EASI's Celsius rule, plus results reported in Fahrenheit kept converted (used only where a station has no Celsius result; conversions outside -1 to 40 C set apart); `screen` sets apart Celsius results outside -1 to 40 C or out of season for the area (winter Fahrenheit numbers labelled Celsius, freezing readings in warm months; hot springs kept), values kept; values in hundredths of a degree | `wqp_temperature.py` | `<sources>/wqp/temperature/` |
| NWIS gage statistics, WQP back-fill and the latest 13 months re-pulled | `nwis.py`, `wqp_backfill.py` | `nwis_gages`, `wqp_backfill`, `wqp_recent` |

A county whose TIGER/Line 2025 file the Census server refuses (36059 and 48001 come back as a
firewall rejection page) is read from its 2024 file; the roads stats name it and `sources.json`
records it. The Canadian units with no US data (0416, 0421, 0422, 0432, 0433) get zero roads, no
soil cells and no assessment units instead of failing.
Every land cover and soil count must cover each catchment cell for cell (the stage refuses otherwise);
the readers are `apps/hr-data/hrslim/values.py` (watershed values, per-flowline extras; exact or lean
files) and `apps/hr-data/hrslim/points.py` (the apps' WQP, NID, NWIS and NAS point rules over the
tables). The WQP rows go through the national builder's own selection and normalization, so EASI's
exclusions and unit factors apply unchanged; `raw` keeps the number SFARI's median reads; months
re-pulled later replace the national pull's (late submissions). NWI wetlands whose vertices times the
strips they meet pass `HEAVY_WORK` are cut into small pieces before the strip intersections
(Maryland's Chesapeake Bay polygon has 4 million vertices); the areas do not change. Line attributes
(`slope`, `lengthkm`, `qama`) and StreamCat values are float64, the numbers the services answer.
`pack` copies `values_lean/` and `tables/` beside the data (`data/values`, `data/tables`).

The apps fetch the bundle from the rolling `staf-data-current` prerelease (always a prerelease):
`release pack` turns a bundle folder into its assets (`release.py`: `core.zip` with the manifests,
links, coverage outlines and 3DEP tile catalogs; a `tables-<file>` per national table; a
`region-<vpu>.zip` per region; `release.json` with every checksum, uploaded last; deterministic
archives, so an unchanged region keeps its checksum) and `release publish --yes` uploads them with
`gh` once the owner approves. The site engine's `delivery.py` reads them.

```bash
python tools/hr-slim/run.py release pack                    # apps/hr-data/data -> D:\Data\nhdplus-hr\release
python tools/hr-slim/run.py release publish --yes           # the owner's go first
python tools/hr-slim/scripts/raindrop_check.py              # the bundle's raindrop against NLDI's
python tools/hr-slim/scripts/v2_check.py attrs              # V2 attributes against the fabric API (also nav)
```

## Scripts

Phase 0 tests:

- `scripts/compare_service.py`: slim vs the live USGS service, feature by feature.
- `scripts/shared_edges.py VPU TOL`: catchments stored as shared edges (arcs).
- `scripts/metric_accuracy.py`: the site engine's watershed metrics on original vs
  slim watersheds for the walk panel and small headwater sites.

Bundle v2:

- `scripts/geometry_research.py`: the measurements behind the v2 design (grids,
  storage options, simplification error, click snapping).
- `scripts/accept_v2.py`: the pilot acceptance checks (sizes, v1 and v2 walks,
  outlines against polygon unions, snapping on the stored lines).

Tests: `cd tools/hr-slim && python -m pytest`.

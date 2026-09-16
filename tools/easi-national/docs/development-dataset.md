# EASI development dataset, 16 states, September 2026

Provenance record written 2026-09-16T20:01:27Z by `scripts/write_dataset_provenance.py` (machine-readable copy: `provenance.json`). This dataset was built to evaluate and refine the EASI screening criteria across a broad range of settings. It is a development dataset, not a nationwide assessment product, and every score in it is an automated, unreviewed screening result.

## Identity

| Item | Value |
|---|---|
| Release | GitHub prerelease `easi-national-current` on USACE-WRISES/staf (always a prerelease) |
| Bundle | build `3d8a4711c5414d4e9e76ca2233815783`, alternative-2 |
| Scoring method digest | `b2e3033116e3` (criteria set regional, first at commit `02f39a8`) |
| Reaches scored | 1,357,265 on 1,169 HUC8s in 145 of 222 HUC4 units (67 complete, 78 partial at the footprint edge), 21 tile partitions |
| Built | 2026-09-16T03:32:28Z to 2026-09-16T04:33:21Z (staging updated 2026-09-16T04:32:56Z) |
| Assets | 192 files, 3.26 GB |
| manifest.json sha256 | `43574e9b71530c49f91cd52e32559d0dfb3e42bf28e342771d197121db9e5016` |
| completion.json sha256 | `42fc11ac9cfb17b1bfa0e72dafd400f2acf4a7e3973355c96460b4856c86793e` |
| Record written at commit | `d93f725` |

## States

Sixteen state batches were requested: AR, CA, FL, IN, KS, KY, MN, MT, NH, NM, NY, OH, OR, TX, VA, WA. Every HUC8 that touches a requested state was processed whole, so the footprint spills into neighbouring states. A reach belongs to the state containing the midpoint of its flowline.

| Batch | HUC8s | Reaches in batch | VPUs | Created |
|---|---:|---:|---|---|
| huc8-02080204 (HUC8 02080204) | 1 | 896 | 02 | 2026-09-10T17:32:55Z |
| state-AR (Arkansas) | 58 | 120,271 | 08, 11 | 2026-09-13T17:01:36Z |
| state-CA (California) | 140 | 159,049 | 15, 16, 17, 18 | 2026-09-12T20:32:01Z |
| state-FL (Florida) | 56 | 53,028 | 03S, 03W | 2026-09-12T16:07:27Z |
| state-IN (Indiana) | 38 | 36,736 | 04, 05, 07 | 2026-09-13T17:04:08Z |
| state-KS (Kansas) | 90 | 130,481 | 10L, 11 | 2026-09-13T09:08:31Z |
| state-KY (Kentucky) | 44 | 65,644 | 05, 06, 08 | 2026-09-13T17:02:00Z |
| state-MN (Minnesota) | 80 | 82,755 | 04, 07, 09, 10U, 10L | 2026-09-13T17:00:49Z |
| state-MT (Montana) | 114 | 162,940 | 10U, 17 | 2026-09-13T17:01:16Z |
| state-NH (New Hampshire) | 17 | 20,662 | 01 | 2026-09-12T14:57:52Z |
| state-NM (New Mexico) | 85 | 85,681 | 11, 12, 13, 14, 15 | 2026-09-12T11:42:26Z |
| state-NY (New York) | 54 | 65,882 | 01, 02, 04, 05 | 2026-09-13T17:02:43Z |
| state-OH (Ohio) | 43 | 66,237 | 04, 05 | 2026-09-12T16:07:16Z |
| state-OR (Oregon) | 91 | 89,013 | 16, 17, 18 | 2026-09-12T00:36:11Z |
| state-TX (Texas) | 209 | 121,879 | 08, 11, 12, 13 | 2026-09-13T17:00:21Z |
| state-VA (Virginia) | 52 | 96,315 | 02, 03N, 05, 06 | 2026-09-11T13:21:37Z |
| state-WA (Washington) | 72 | 68,607 | 17 | 2026-09-13T17:03:20Z |

| State | Scored reaches | Reaches in state | Coverage |
|---|---:|---:|---:|
| AR Arkansas | 84,242 | 84,242 | 1.000 |
| DC District of Columbia | 55 | 55 | 1.000 |
| FL Florida | 36,924 | 36,924 | 1.000 |
| IN Indiana | 20,960 | 20,960 | 1.000 |
| KS Kansas | 99,626 | 99,626 | 1.000 |
| KY Kentucky | 44,339 | 44,339 | 1.000 |
| MN Minnesota | 59,927 | 59,927 | 1.000 |
| MT Montana | 128,105 | 128,105 | 1.000 |
| NM New Mexico | 64,706 | 64,706 | 1.000 |
| OH Ohio | 51,196 | 51,196 | 1.000 |
| OR Oregon | 70,707 | 70,707 | 1.000 |
| TX Texas | 97,446 | 97,446 | 1.000 |
| VA Virginia | 61,615 | 61,615 | 1.000 |
| WA Washington | 57,563 | 57,563 | 1.000 |
| CA California | 141,953 | 141,961 | 1.000 |
| NH New Hampshire | 12,017 | 12,018 | 1.000 |
| NY New York | 50,848 | 51,149 | 0.994 |
| VT Vermont | 2,860 | 5,717 | 0.500 |

States under half coverage are border spill and are listed in `provenance.json`.

## Source datasets

| Source | Role | Cache | Modified | Rows |
|---|---|---|---|---:|
| NHDPlus V2 seamless geodatabase (EPA, NHDPlusV21_NationalData_Seamless_Geodatabase_Lower48_07) | flowline geometry, value-added attributes, EROM monthly flows, HUC12 boundaries | `national/flowlines.parquet` | 2026-09-11T03:01:01Z | 2691339 |
| NHDPlus V2 seamless geodatabase (EPA, NHDPlusV21_NationalData_Seamless_Geodatabase_Lower48_07) | flowline geometry, value-added attributes, EROM monthly flows, HUC12 boundaries | `national/erom.parquet` | 2026-09-15T13:50:04Z | 2691339 |
| NHDPlus V2 seamless geodatabase (EPA, NHDPlusV21_NationalData_Seamless_Geodatabase_Lower48_07) | flowline geometry, value-added attributes, EROM monthly flows, HUC12 boundaries | `national/huc12.parquet` | 2026-09-11T02:59:33Z | 83509 |
| EPA StreamCat API (https://api.epa.gov/StreamCat) | watershed, catchment and riparian-corridor landscape summaries; prg_bmmi0809 at the other area of interest | `national/streamcat.parquet` | 2026-09-15T15:52:52Z | 2647057 |
| EPA ATTAINS assessment geodatabase and MapServer | integrated-report category of the assessment unit at or near the reach | `national/attains.parquet` | 2026-09-11T03:06:10Z | 3497999 |
| USGS Nonindigenous Aquatic Species database (NAS API) | established non-native taxa per HUC12 | `national/nas.parquet` | 2026-09-11T03:29:59Z | 372357 |
| USACE National Inventory of Dams (NID FeatureServer) | mapped dams within one mile of the reach anchor | `national/nid.parquet` | 2026-09-10T17:30:09Z | 92606 |
| Water Quality Portal (WQX3 result search, monthly national pull) | total nitrogen and total phosphorus results within five miles and ten years | `national/wqp/wqp_results.parquet` | 2026-09-12T03:01:39Z | 1877487 |
| USGS 3DEP elevation (1 m lidar projects, 1/9 arc-second quads, 10 m seamless) | reach cross-sections for the bank-height and entrenchment ratios | `national/dem/1m/tiles.parquet` | 2026-09-11T12:31:12Z | 125853 |
| USGS 3DEP elevation (1 m lidar projects, 1/9 arc-second quads, 10 m seamless) | reach cross-sections for the bank-height and entrenchment ratios | `national/dem/19/quads.parquet` | 2026-09-11T12:31:14Z | 8345 |
| Census cartographic state boundaries (1:500k) | the state of every flowline by its midpoint | `national/comid_state.parquet` | 2026-09-12T00:07:10Z | 2691339 |
| EPA NARS nine aggregate ecoregions and Level III ecoregions (app data) | the regional criteria and the reference-curve stratum |  | | |
| WQP window | calendar months pulled | 2016-09 to the month before the pull; 10 years before the assessment date are used per reach | | |

## Processing

Chunk stages per state batch: streamcat, geometry, huc12, wqp, attains, nas. HUC8 stages: derive, xs_sample, xs_derive, joins, score. Scores are computed from the stored evidence with the application's own adapters (`easi.assessment.assess_preloaded`); the national path makes no per-reach network call. Tiles are cut per vector processing unit with tippecanoe in the `staf-tippecanoe` Docker image. The Alternative 2 bundle was scored by `builder.alternative2_rollout` from the unchanged Alternative 1 evidence.

| Assumption | Value |
|---|---|
| reach_length_ft | 1000.0 |
| reach_source | nhdplus_v2_flowline |
| chunk_buffer_mi | 10.0 |
| wqp_radius_mi | 5.0 |
| wqp_years | 10 |
| nid_radius_mi | 1.0 |
| attains_buffer_m | 2000.0 |
| streamcat_aois | ["ws", "cat", "wsrp100", "other (prg_bmmi0809)"] |
| nrsa_as_of | 2026-09-10 |
| state_of_a_reach | the Census state polygon containing the flowline midpoint; nearest polygon for coastal midpoints |
| cross_sections | {"resolutions_m": [1, 3, 10], "rule": "1 m where a 3DEP lidar project covers at least half the reach buffer, else the 1/9 arc-second (3 m) quads where they exist, else the 10 m seamless", "source": "USGS 3DEP"} |
| tier | 2 |
| reference_screen | {"frame": {"fcode_class": ["!=", "canal"], "wadeable": ["==", true]}, "id": "least-disturbed-v1", "relaxed": {"agriculture_ws": ["<=", 25.0], "dor": ["<", 5.0], "mines_ws": ["==", 0.0], "pctimp2019ws": ["<=", 3.0], "rddensws": ["<=", 2.0], "sc__npdesdensws": ["==", 0.0]}, "roadDensityCap": 2.0, "strict": {"agriculture_ws": ["<=", 10.0], "dor": ["<", 2.0], "mines_ws": ["==", 0.0], "pctimp2019ws": ["<=", 1.0], "rddensws": ["<=", 2.0], "sc__nabd_densws": ["==", 0.0], "sc__npdesdensws": ["==", 0.0]}} |
| reference_panel_floors | {"complete": 100, "exploratory": 30, "split": 30} |
| scores_from | stored evidence through easi.assessment.assess_preloaded with the application's adapters; no per-reach network call |

## Method history

- `477bf7771e24`: published to the prerelease 2026-09-14 under the pre-revision criteria (national bands); superseded
- `e9f472b31fe5`: Alternative 1, Level II reference curves (62 curves), local staging 2026-09-15; the controlled study's retained control
- `b2e3033116e3`: Alternative 2, NARS-9 reference curves (34 curves), adopted 2026-09-16; the release method

## Outputs

Per bundle: `manifest.json`, `completion.json`, `stats.json`, `coverage.geojson`, `comid_huc4.parquet`, one `evidence_<huc4>.parquet` per HUC4 unit, one `scores_<vpu>.parquet` and one `tiles_<vpu>.pmtiles` per vector processing unit. The evidence files hold every network-derived input per reach; the scores files hold the 20 function ratings, indices and scores plus the sub-indices and the ECI. Every asset carries its sha256 in the manifest.


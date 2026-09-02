---
title: Computation Engines
nav_order: 11
description: "How the STAF apps compute watershed metrics: the StreamCat lookup engine and the STAF site engine."
---
{% include staf_page_chrome.html %}

Every STAF app needs watershed metrics: impervious cover, wetland extent, road
density, dam storage, riparian vegetation and the like. Two engines produce
them. This page says what each one is, which app uses which, what each cannot
produce, and how to read the labels in a report.

## The two engines

**StreamCat lookup engine.** EPA StreamCat publishes precomputed landscape
summaries for every reach of the NHDPlus V2 network (about 1:100,000 scale).
The lookup engine snaps a point to its V2 reach, delineates that reach's basin
with the USGS NLDI service, and reads the published values for the reach's
COMID. It is fast, reproducible, and citable by data vintage. It also carries
the EPA modeled integrity indices that only exist per V2 reach. Its limit is
coverage: roughly nine of every ten stream miles in the full-resolution NHD are
not on the V2 network, and most of those are headwaters.

**STAF site engine.** The site engine works on the high-resolution NHD. Given a
point on any stream, it delineates the true contributing watershed at that
exact point by aggregating NHDPlus HR catchments, checks the area against the
published drainage area, and computes the watershed metrics from source data:
NLCD land cover on the watershed and a 100 m riparian buffer, TIGERweb roads,
National Inventory of Dams records inside the polygon, SSURGO soil erodibility,
and EROM flow. It usually takes well under a minute, up to about five minutes on a large basin, and
refuses, with a reason, when a basin exceeds its budget. Every value it
produces carries its source, vintage and the engine version.

| | StreamCat lookup engine | STAF site engine |
|---|---|---|
| Works on | NHDPlus V2 reaches only | Any NHD stream in the conterminous United States |
| Watershed | The reach's published basin | The exact watershed at the clicked point |
| Speed | Seconds | Usually under a minute, up to about five on a large basin |
| Cannot produce | Anything off the V2 network | The EPA modeled integrity indices, NRSA field observations, base-flow index, precipitation and temperature normals |
| Produces differently | | Runoff (EROM-derived rather than the StreamCat water-balance grid) and the riparian buffer (built on the high-resolution stream network) |

## Which app uses which

No app asks the user to pick a method. Each applies one fixed policy, and
every value says which engine produced it.

| Tier | App | Policy |
|---|---|---|
| Screening | EASI | Bold stream lines have StreamCat data: the lookup engine answers in seconds. Thin lines are the rest of the NHD: the site engine calculates the exact watershed. On those streams the three reach-keyed metrics (low flow, substrate, biological integrity) describe the nearest covered reach downstream, labeled with the routed distance and the drainage-area ratio, and are unavailable when that reach drains more than ten times the clicked stream. If the engine cannot compute the watershed, the watershed metrics are unavailable with guidance, never a stand-in. |
| Rapid | SFARI | The site engine supplies the exact watershed and its evidence first. StreamCat values remain as labeled fallbacks that name the reach they describe. The assessor keeps every score. |
| Detailed | DEEP | The exact watershed on streams outside the V2 network. Auto-pulled values follow the assessment bundle: curves fitted on StreamCat predictors take lookup-engine values, curves fitted on engine predictors take site-engine values. A value from the other engine is shown as reference and not scored. |
| Detailed (builder) | StreamCurves | The predictor source is the one choice in the program: the StreamCat lookup engine by default, or the site engine, recorded in every build's provenance. The reference screen always runs on the lookup engine. |

## Reading the labels

- EASI reports name the engine on every watershed row and, on a thin-line
  stream, add a banner: the assessed stream, the watershed engine and its
  area, and the reach that supplied the reach-keyed evidence. The CSV and
  GeoJSON exports carry an Engine column on those sites.
- SFARI evidence rows carry a badge: exact watershed (site engine), StreamCat,
  desktop (other services) or field. The printed field packet marks site-engine
  values "(exact watershed)".
- DEEP shows the source and basis of each auto-pulled value beside its input,
  and an advisory when a value is shown as reference only.
- StreamCurves records the predictor source in the run manifest, the published
  bundle and the science support report.

## Equivalence study

Whether exact delineation changes screening scores enough to make the site
engine the standard on covered streams is being tested, not assumed. The
study runs EASI at NRSA sites in two pilot regions with StreamCat inputs and
with site-engine inputs and compares the watershed-metric ratings, the
condition class, and the DEEP curve indices. Status: running (2026-09).

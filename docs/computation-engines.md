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
point on any stream, it snaps the point to the nearest HR reach and delineates
that reach's watershed, the HR reach watershed: the NHDPlus HR catchments
upstream of the reach, aggregated and checked against the reach's published
drainage area. The reach, not the point, is the outlet, so a point partway up
a reach gets the reach's whole upstream area. It then computes the watershed
metrics from source data:
NLCD land cover on the watershed and a 100 m riparian buffer, TIGERweb roads,
National Inventory of Dams records inside the polygon, SSURGO soil erodibility,
and EROM flow. It usually takes well under a minute, up to about five minutes on a large basin, and
refuses, with a reason, when a basin exceeds its budget. Every value it
produces carries its source, vintage and the engine version.

| | StreamCat lookup engine | STAF site engine |
|---|---|---|
| Works on | NHDPlus V2 reaches only | Any NHD stream in the conterminous United States |
| Watershed | The V2 reach's published basin | The HR reach watershed of the reach the point snaps to |
| Speed | Seconds | Usually under a minute, up to about five on a large basin |
| Cannot produce | Anything off the V2 network | The EPA modeled integrity indices, NRSA field observations, precipitation and temperature normals |
| Produces differently | | Runoff (EROM-derived rather than the StreamCat water-balance grid) and the riparian buffer (built on the high-resolution stream network) |

## Which app uses which

No app asks the user to pick a method. Each applies one fixed policy, and
every value says which engine produced it.

| Tier | App | Policy |
|---|---|---|
| Screening | EASI | The map draws one stream network, colored by the engine that answers a click there: dark blue where the StreamCat lookup engine scores the reach in seconds, cyan where the STAF site engine calculates the HR reach watershed. After a click the scored reach is highlighted. On cyan streams the three reach-keyed metrics (low flow, substrate, biological integrity) come from the nearest StreamCat reach downstream, and each says so with the routed distance and the drainage-area ratio. If the engine cannot compute the watershed, the watershed metrics are unavailable with guidance, never a stand-in. |
| Rapid | SFARI | The map draws both networks, dark blue for the NHDPlus V2 reaches the StreamCat lookup engine covers and cyan for every other NHD stream, and every click snaps to the high-resolution NHD. The site engine computes the HR reach watershed and every watershed value at every site. The StreamCat lookup engine supplies, by COMID, the EPA modeled indices that exist only per V2 reach (flow permanence, dewatered segments, barriers, nutrients) and stands in, labeled, for a watershed value the engine could not compute. On a stream outside V2 the COMID is the nearest StreamCat reach downstream, named with the routed distance and the drainage-area ratio. Direct services (gages, water quality, wetlands, dams) answer the rest. The assessor keeps every score. |
| Detailed | DEEP | The map draws both networks as SFARI does, dark blue for the NHDPlus V2 reaches the StreamCat lookup engine covers and cyan for every other NHD stream, and every click snaps to the high-resolution NHD. The site engine computes the HR reach watershed at every site. Auto-pulled values follow the assessment metric by metric: a curve whose own `predictorSource` records engine predictors takes the site-engine value (every landscape metric, base-flow index and road-stream crossings included since engine 0.3.0), and every other curve takes the lookup-engine value by COMID that it was fitted on, so a mixed bundle scores both. On a stream outside V2 the COMID is the nearest StreamCat reach downstream, named with the routed distance and the drainage-area ratio. A value from the other engine is shown as reference and not scored. |
| Detailed (builder) | StreamCurves | The predictor source is the one choice in the program: the StreamCat lookup engine by default, or the site engine, recorded in every build's provenance. An engine-sourced build also recomputes the scored landscape metrics with an engine analog (impervious, crop, wetland, road density, dam density, base-flow index, road-stream crossings) over the HR reach watershed at every training site, and stamps those curves as engine-fitted. Surface water storage is one combined wetland metric, woody plus herbaceous, the same sum EASI scores. The reference screen always runs on the lookup engine, keyed on each NRSA site's own archive COMID, so pool membership is decided on the reach the crew sampled, and the candidate panel is wadeable-only. |

## Reading the labels

- EASI reports name the engine on every watershed row and, on a stream drawn
  cyan, add a banner: the assessed stream, the watershed engine and its
  area, and the reach that supplied the reach-keyed evidence. The CSV and
  GeoJSON exports carry an Engine column on those sites.
- SFARI evidence rows carry a badge: HR reach watershed (site engine),
  StreamCat (by COMID, naming the reach it describes) or desktop (other
  services). The desktop metrics PDF and the report CSV carry the same labels.
- DEEP shows the source and basis of each auto-pulled value beside its input,
  and an advisory when a value is shown as reference only.
- StreamCurves records the predictor source in the run manifest, the published
  bundle and the science support report.

## Equivalence study

Whether HR reach delineation changes screening scores enough to make the site
engine the standard on covered streams was tested, not assumed. The study
ran EASI at 30 NRSA sites in two pilot regions with StreamCat inputs and
with site-engine inputs and compared the watershed-metric ratings, the
condition class, and the DEEP curve indices against a rule fixed before the
run. Outcome (2026-09-02): the engines are not interchangeable. 30 of 30 sites ran on 2026-09-02: watershed-metric rating agreement 0.84 pooled (Eastern Corn Belt Plains 0.78, Northeastern Highlands 0.90) against the 0.90 bar, condition-class agreement 0.97, median DEEP index shift 0.013.
The class and the index agree well; the rating disagreements come mostly
from watershed extent, because an HR reach partway up a V2 reach, or on a
tributary the V2 network does not carry, drains far less than the V2 reach's
whole basin. So EASI keeps the StreamCat
lookup engine on covered streams and documents the approximation, DEEP
keeps refusing engine values against StreamCat-fitted curves, and
StreamCurves builds engine-predictor versions of the pilot assessments so
DEEP can score engine values against curves fitted the same way.

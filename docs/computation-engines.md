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

EASI, SFARI, and DEEP display one solid blue stream network by default. In the
Layers menu, **StreamCat coverage** is initially off. Enabling it distinguishes
StreamCat reaches from other streams and reveals the selected data-source reach
and its downstream connector. This display setting leaves the assessment point,
reach, watershed, and calculation policy unchanged.

Each app resolves a StreamCat source reach before allowing new analysis. The
selected point remains visible while the status above Delineate shows lookup
progress with a small spinning circle while work is active, including retry
pauses. The circle stays stationary for reduced-motion preferences. Temporary
routing failures retry up to three times, after pauses of 5, 10, and 15 seconds;
if unresolved,
**Retry StreamCat lookup** retries the same snapped point. A no-match result or
invalid service response also blocks analysis with an explanation. A resolved
source means a valid COMID; individual metrics are retrieved later and can still
be unavailable. Imported results remain readable while missing source information
is resolved. Coverage toggles do not trigger lookup or affect readiness.

| Tier | App | Policy |
|---|---|---|
| Screening | EASI | Within 150 ft of an NHDPlus V2 reach, the StreamCat lookup engine supplies watershed metrics in seconds. Elsewhere the STAF site engine calculates the HR reach watershed. Reach-keyed metrics, including low flow, substrate, and biological integrity, can use the nearest StreamCat reach downstream; their source details include the routed distance and drainage-area ratio. If the engine cannot compute the watershed, the watershed metrics are unavailable with guidance, never a stand-in. |
| Rapid | SFARI | Every click snaps to the high-resolution NHD. The site engine computes the HR reach watershed and every watershed value at every site. The StreamCat lookup engine supplies, by COMID, the EPA modeled indices that exist only per V2 reach (flow permanence, dewatered segments, barriers, nutrients) and stands in, labeled, for a watershed value the engine could not compute. On a stream outside V2 the COMID is the nearest StreamCat reach downstream, named with the routed distance and the drainage-area ratio. Direct services (gages, water quality, wetlands, dams) answer the rest. The assessor keeps every score. |
| Detailed | DEEP | Every click snaps to the high-resolution NHD. The site engine computes the HR reach watershed at every site. Auto-pulled values follow the assessment metric by metric: a curve whose own `predictorSource` records engine predictors takes the site-engine value (every landscape metric, base-flow index and road-stream crossings included since engine 0.3.0), and every other curve takes the lookup-engine value by COMID that it was fitted on, so a mixed bundle scores both. On a stream outside V2 the COMID is the nearest StreamCat reach downstream, named with the routed distance and the drainage-area ratio. A value from the other engine is shown as reference and not scored. |
| Detailed (builder) | StreamCurves | The predictor source is the one choice in the program: the StreamCat lookup engine by default, or the site engine, recorded in every build's provenance. An engine-sourced build also recomputes the scored landscape metrics with an engine analog (impervious, crop, wetland, road density, dam density, base-flow index, road-stream crossings) over the HR reach watershed at every training site, and stamps those curves as engine-fitted. Surface water storage is one combined wetland metric, woody plus herbaceous, the same sum EASI scores. The reference screen always runs on the lookup engine, keyed on each NRSA site's own archive COMID, so pool membership is decided on the reach the crew sampled, and the candidate panel is wadeable-only. |

## Reading the labels

- EASI reports name the engine on watershed rows and mark downstream desktop
  evidence with a dagger, explained below the metrics table. Detailed source
  information retains the source reach, distance, and drainage-area ratio;
  routine provenance does not add a report-top banner. CSV and GeoJSON exports
  retain engine and reach provenance.
- SFARI evidence rows carry a badge: HR reach watershed (site engine),
  StreamCat (by COMID, naming the reach it describes) or desktop (other
  services). The main report and assessment PDF mark affected desktop evidence
  with a dagger and a short explanation below the metrics table. The desktop
  metrics PDF and report CSV retain their detailed source descriptions.
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

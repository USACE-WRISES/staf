---
title: DEEP Guide
nav_order: 10
description: "How the DEEP detailed assessment app scores a site, where each curve comes from, and how to measure every metric of its regional assessments."
---
{% include staf_page_chrome.html %}

<p>DEEP (Detailed Evaluation of Ecosystem Processes) scores a stream site against a published detailed assessment for its region, in the web application or in that assessment's spreadsheet calculator (both on <a href="{{ site.baseurl }}/tools/#deep">Apply STAF</a>). The web application's Assessment page shows only what you need to enter values. This guide holds the rest: how DEEP turns a value into a score, what each reference curve rests on, and how each metric is measured.</p>

## The Assessment page

DEEP walks the 20 STAF functions in framework order, one page each. The rail on the left lists them all and colors each function by its condition once it is scored.

Each metric is one row, laid out the same way in EASI, SFARI and DEEP:

- **Name and (i).** Hover or focus the (i) to see how to measure the metric.
- **Description.** One line says what the value is and which way is better, for example "Percent of the watershed in impervious surface; higher is worse." The direction is read from the curve that scores the metric.
- **Desktop.** A value DEEP filled in from desktop data is marked Desktop. Type over it to replace it with your own value.
- **Curve set.** Some metrics have separate curves for classes of stream, set by slope or drainage area. DEEP picks the class from the delineated reach and marks it (auto). Hover the selector to see why. You can choose another class.
- **Value and index.** Type the measured value in the box on the right; the unit sits beside it. The pill under it shows the 0 to 1 index and the condition it falls in, and updates as you type.

Under each metric, four buttons are always shown:

- **Scoring** opens the reference curve with your value on it.
- **Note** adds a note to the metric. The button shows a dot once the note holds text.
- **Photo** adds photos; the button counts them.
- **N/A** marks a metric that does not apply at the site. It leaves the function's average.

A row can carry one warning:

- **Outside the curve's range** means the value lies beyond the ends of the curve, so it scores at the nearest end. Check the units.
- **Not scored: shown for reference only** means a desktop value came from a source the curve was not built on. Enter a measured value to score the metric.

A metric marked **Not scored** is one the assessment withholds because no defensible reference supports it. Hover its (i) for the reason. A function the assessment cannot score at all says so on its own page.

**Get Forms** lists every metric of the assessment with how it is measured, whether it is answered in the field (F) or from the desk (D), its status, any desktop value and the data behind it. From there you can download the field forms, the metrics list, and the Excel calculator, blank or completed with the site's values. The report offers the completed calculator too.

## How DEEP scores

1. **Metric index.** The metric's reference curve converts the measured value to an index from 0 to 1. An index of 0.39 or less is Non-Functioning, above 0.39 to 0.69 is Functioning-at-Risk, and above 0.69 is Functioning.
2. **Function score.** DEEP averages the indices of a function's metrics and multiplies by 15. A score of 5 or less is Non-Functioning, above 5 to 10 is Functioning-at-Risk, and above 10 is Functioning. Metrics marked N/A leave the average.
3. **Outcomes and the index.** Function scores roll up to the Physical, Chemical and Biological outcome sub-indices and the Ecosystem Condition Index, as the <a href="{{ site.baseurl }}/scoring/">Scoring and Condition</a> page describes.

When an assessment cannot score a function, DEEP does not treat it as a low score. It reports the Ecosystem Condition Index as an interval that allows for any score the function could have had, and names a condition band only when the interval stays inside one.

## Assessment status

Every published assessment version carries a status, shown in its color on DEEP's map, in the Assessment regions list and on its badge:

| Status | Color | What it means |
|---|---|---|
| Draft | Gray | Built and published, but its curves have not been reviewed yet. |
| Preliminary | Amber | Reviewed and approved for use. |
| Final | Blue | Certified after field validation. |

DEEP uses an assessment's Final version when it has one, else its Preliminary version, else its Draft. **Change** on the Basin step offers the other versions.

## Where each curve comes from

Every curve in a published assessment rests on one of these bases. The report names the basis for each metric at your site.

| Basis | What it means |
|---|---|
| Fixed criteria | Pressure metrics (impervious surface, agriculture, road density, degree of regulation, dams within one mile) use EASI's fixed criteria as a continuous curve, the same in every region. EASI lists them as provisional. |
| Own reference stations | The curve is fitted to least-disturbed stations of the assessment's own ecoregion, screened with EASI's least-disturbed pressure screen on NRSA wadeable data. |
| Borrowed reference stations | The ecoregion has too few comparable stations, so the pool widens to the parent Level II or Level I ecoregion, or the NARS-9 region. Each borrowed curve carries a transfer risk. A transfer the recovery test did not confirm caps the curve's confidence. |
| National reference | Comparable least-disturbed stations drawn from the national pool. |
| Modeled reference | An expectation from a national model, extrapolated to the ecoregion rather than observed there. |
| Published benchmark | A published threshold, such as the NRSA nutrient benchmarks for the NARS-9 region. |
| EASI screening method | The curve EASI's screening method uses, adopted where no better basis exists. |
| Withheld | No basis supports the metric in the ecoregion, so it is not scored. |

For a site, the report carries the detail:

- **The PDF** has a Reference support table with what each metric is scored against and the curve set used.
- **The CSV** adds the curve basis, the reference sample and reviewer priority (Uncertainty), the caveats to read the result with (Read with care), and each function's limitation.

The assessment's technical documentation records how its curves were built.

## Desktop values

DEEP fills in the landscape metrics it can answer from desktop data. The <a href="{{ site.baseurl }}/computation-engines/">Computation Engines</a> page explains how DEEP delineates the watershed and computes each value.

| Data | Metrics |
|---|---|
| NLCD 2021 land cover | Impervious surface, agriculture (crops, hay and pasture), and wetland where the assessment uses EASI's screening curve |
| TIGERweb roads | Road density |
| EPA StreamCat | Wetland on regional curves, base flow index, degree of regulation, natural riparian cover |
| USACE National Inventory of Dams | Dams within one mile of the site |
| 3DEP elevation (modeled cross sections) | Bank height ratio, entrenchment ratio, width to depth ratio, where the cross sections pass the quality checks |

Every desktop value stays editable.

<!--
  The metric reference below is generated. The section between the BEGIN/END GENERATED
  markers is written by
    python apps/deep/scripts/build_guide_reference.py
  from the current regional assessments in apps/deep/data, with the method text in the
  concise wording DEEP shows (apps/deep/deep/method_text.py). Do not hand-edit it.
-->

## Metric reference

<p class="metric-ref-intro">Every metric of the current regional assessments: how to measure it, whether DEEP fills it in from desktop data, the functions it serves, and what its curve rests on across the regional assessments. Pressure metrics show their fixed criteria and sources.</p>

<!-- BEGIN GENERATED METRIC REFERENCE -->
### Hydrology

<details class="metric-ref">
<summary>Agriculture, crop and hay (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Percent of the delineated watershed in cultivated crops plus hay and pasture, computed by DEEP. No field measurement.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Catchment hydrology</p><p class="metric-ref-meta">Curve basis: the fixed criteria below, in every regional assessment.</p></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same in every region</div>
<p class="metric-ref-note">DEEP scores the value on a continuous curve through these bands (index 0.69 and 0.39 at the band edges). EASI lists these criteria as provisional, so the breakpoints may be revised.</p>
<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;30%</span></div><div class="gfp-seg fair"><b>Fair</b><span>30-50%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;50%</span></div></div></figure></div>
<ul class="metric-ref-sources"><li>National Rivers and Streams Assessment 2018-19 Technical Support Document</li><li>Schueler 1994, The importance of imperviousness</li><li>Allan 2004, Landscapes and riverscapes: the influence of land use on stream ecosystems</li><li>Wang et al. 1997, Influences of watershed land use on habitat quality and biotic integrity in Wisconsin streams</li><li>King et al. 2011, Stream community thresholds at exceptionally low levels of catchment urbanization</li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Base flow index (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Percent of streamflow that comes from base flow in the watershed (USGS base flow index grid), read from EPA StreamCat for the reach. No field measurement.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Low flow and baseflow dynamics, Streamflow regime</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Degree of regulation (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Normal storage of upstream dams as a percent of mean annual runoff, both read from EPA StreamCat for the reach. No field measurement.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Streamflow regime</p><p class="metric-ref-meta">Curve basis: the fixed criteria below, in every regional assessment.</p></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same in every region</div>
<p class="metric-ref-note">DEEP scores the value on a continuous curve through these bands (index 0.69 and 0.39 at the band edges).</p>
<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;2%</span></div><div class="gfp-seg fair"><b>Fair</b><span>2-15%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;15%</span></div></div></figure></div>
<ul class="metric-ref-sources"><li>EPA StreamCat Metrics and Definitions</li><li>Lehner et al. 2011, High-resolution mapping of the world&#x27;s reservoirs and dams</li><li>Grill et al. 2019, Mapping the world&#x27;s free-flowing rivers</li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Impervious surface (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Percent of the delineated watershed in impervious surface, computed by DEEP. No field measurement.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Catchment hydrology, High flow dynamics</p><p class="metric-ref-meta">Curve basis: the fixed criteria below, in every regional assessment.</p></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same in every region</div>
<p class="metric-ref-note">DEEP scores the value on a continuous curve through these bands (index 0.69 and 0.39 at the band edges). EASI lists these criteria as provisional, so the breakpoints may be revised.</p>
<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;10%</span></div><div class="gfp-seg fair"><b>Fair</b><span>10-25%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;25%</span></div></div></figure></div>
<ul class="metric-ref-sources"><li>National Rivers and Streams Assessment 2018-19 Technical Support Document</li><li>Schueler 1994, The importance of imperviousness</li><li>Allan 2004, Landscapes and riverscapes: the influence of land use on stream ecosystems</li><li>Wang et al. 1997, Influences of watershed land use on habitat quality and biotic integrity in Wisconsin streams</li><li>King et al. 2011, Stream community thresholds at exceptionally low levels of catchment urbanization</li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Road density (km/sq km)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Road length per square kilometer of the delineated watershed, computed by DEEP. No field measurement.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Reach inflow</p><p class="metric-ref-meta">Curve basis: the fixed criteria below, in every regional assessment.</p></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same in every region</div>
<p class="metric-ref-note">DEEP scores the value on a continuous curve through these bands (index 0.69 and 0.39 at the band edges). EASI lists these criteria as provisional, so the breakpoints may be revised.</p>
<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;1 km/km²</span></div><div class="gfp-seg fair"><b>Fair</b><span>1-&lt;3 km/km²</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥3 km/km²</span></div></div></figure></div>
<ul class="metric-ref-sources"><li>National Rivers and Streams Assessment 2018-19 Technical Support Document</li><li>USDA Forest Service 2011, Watershed Condition Classification Technical Guide (FS-978)</li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Wetland (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Percent of the watershed in woody plus herbaceous wetland (NLCD 2019), read from EPA StreamCat for the reach. No field measurement.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Watershed wetland (%), woody plus herbaceous, both classes required. Wetlands store surface water and attenuate flow peaks; EASI scores Surface water storage on the same sum.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Surface water storage</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations, national reference or EASI screening method.</p></div>
</div></details>

### Hydraulics

<details class="metric-ref">
<summary>Bankfull height above channel (m)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At each transect, measure the bankfull height above the present water surface in m, with a rod at the water&#x27;s edge and a clinometer held level at bankfull. Value: mean of the 11 heights.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Mean bankfull height; both incision (very high banks) and over-widening (very low) depart from reference. Two-sided curve centered on the reference core.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Channel evolution, Floodplain connectivity</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference.</p></div>
</div></details>

<details class="metric-ref">
<summary>Bankfull width:depth ratio</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At each transect, measure bankfull width, bankfull height above the water surface and thalweg depth. Value: mean bankfull width divided by mean bankfull depth (bankfull height plus thalweg depth).</p>
<p class="metric-ref-def"><b>What it indicates.</b> Bankfull width-to-depth ratio; both over-widened (very high) and incised (very low) degrade condition. Two-sided curve centered on the reference core.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Channel evolution, Floodplain connectivity, Low flow and baseflow dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference.</p></div>
</div></details>

<details class="metric-ref">
<summary>Embeddedness (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At 5 evenly spaced points on each of the 11 transects, estimate the percent of the particle buried in sand or finer sediment. Value: mean of the 55 estimates.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent embeddedness; higher means interstitial spaces filled with fines (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Bed composition and bedform dynamics, Hyporheic connectivity</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Residual pool depth (cm)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Measure thalweg depth at 10 or 15 evenly spaced stations between each pair of transects (100 or 150 per reach). Value: mean residual pool depth in cm, from that profile and the reach slope (NRSA physical habitat calculations).</p>
<p class="metric-ref-def"><b>What it indicates.</b> Residual pool depth per 100 m; deeper residual pools generally better habitat.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Low flow and baseflow dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Sand + fines (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Record the particle size class at 5 evenly spaced points on each of the 11 transects and the 10 cross-sections midway between them (105 particles). Value: percent sand or finer.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent sand and fines; higher indicates fine-sediment loading (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Hyporheic connectivity, Sediment continuity</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

### Geomorphology

<details class="metric-ref">
<summary>Bank angle (degrees)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> On both banks of each transect, lay a pole about 1 m long against the bank and read its angle from horizontal with a clinometer, in degrees. Value: mean of the 22 readings.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Mean bank angle; very steep banks indicate incision and very flat banks an over-widened, aggrading channel, so departure from the reference core in either direction departs from reference, as for width-to-depth and bankfull height (owner decision, 2026-08-21). A regraded or naturally gentle bank holds at the Functioning edge rather than being penalized, since restoration evaluation is the framework&#x27;s stated use (owner decision, 2026-08-21).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Channel and floodplain dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Bank height ratio (ratio)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At a representative riffle, divide the height of the lower top of bank above the thalweg by the maximum bankfull depth (stream quantification tool procedure, after Rosgen 2006).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Channel and floodplain dynamics</p><p class="metric-ref-meta">Curve basis: EASI screening method.</p></div>
</div></details>

<details class="metric-ref">
<summary>Large wood volume (m3/100m)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Between each pair of transects, tally every piece of wood at least 10 cm in diameter and 1.5 m long in or above the bankfull channel, by length and diameter class. Value: wood volume per 100 m of channel, from the tally.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Large woody debris volume per 100 m; higher is better.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Bed composition and bedform dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Relative bed stability (log)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Computed: the base 10 log of the geometric mean particle diameter divided by the largest diameter the bankfull flow can move, from the particle count, reach slope, bankfull depth, wood tally and residual pools (NRSA physical habitat calculations).</p>
<p class="metric-ref-def"><b>What it indicates.</b> Log relative bed stability; departure from the reference core in either direction departs from reference: negative means excess fines, positive an armored, sediment-starved bed (the classic below-impoundment failure). Two-sided, so armored beds no longer score 1.0 as on the pilot&#x27;s rising curve (owner decision, 2026-08-21).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Sediment continuity</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Sinuosity</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Take a compass backsight bearing and the distance between each pair of neighboring transects. Value: channel length along those segments divided by the straight-line distance between the reach ends.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Channel sinuosity; higher generally indicates a more natural planform.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Channel and floodplain dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Substrate size (log mm)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the 105-particle size class count (as for sand and fines). Value: base 10 log of the geometric mean particle diameter in mm, from the size classes (NRSA physical habitat calculations).</p>
<p class="metric-ref-def"><b>What it indicates.</b> Log mean substrate diameter; coarser substrate generally better, but valley-type dependent.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Bed composition and bedform dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

### Physicochemistry

<details class="metric-ref">
<summary>Canopy density, mid-channel (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At mid-channel on each transect, read a convex spherical densiometer facing upstream, downstream, left and right (covered points of 17). Value: mean of the 44 readings, as a percent.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Mid-channel canopy density; higher shading generally better for thermal/light regime.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Light &amp; thermal regime</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or borrowed reference stations. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Chlorophyll a (ug/L)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Water-column chlorophyll a (µg/L) from a 2 L mid-channel grab sample at the X-site, collected in an amber bottle, filtered and analyzed in a lab.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Chlorophyll a; higher indicates algal enrichment/eutrophication (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Light &amp; thermal regime</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Natural cover of the riparian corridor (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Percent natural cover (forest, shrub, grassland, wetland) in the watershed&#x27;s 100 m riparian corridor (NLCD 2019, StreamCat riparian buffers), the input EASI&#x27;s organic-matter supply method reads. DEEP fills it in; you can correct it.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Carbon processing</p><p class="metric-ref-meta">Curve basis: EASI screening method.</p></div>
</div></details>

<details class="metric-ref">
<summary>pH (Std. Units)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> pH of the X-site mid-channel grab sample, as NRSA reports it, or a calibrated multi-parameter meter reading in the stream there at 0.5 m depth (mid-depth if shallower).</p>
<p class="metric-ref-def"><b>What it indicates.</b> pH degrades condition at BOTH extremes. Two-sided (optimum) curve: scored 1.0 across the reference interquartile core, falling in both tails.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Water &amp; soil quality</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Riparian veg cover, woody (frac)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> In a 10 m by 10 m plot on each bank at each transect, estimate the areal cover class of woody vegetation in the canopy, understory and ground layer. Value: summed woody cover of the 3 layers, averaged over the 22 plots, as a fraction.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Riparian canopy plus mid-layer plus ground cover; higher is better.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Carbon processing</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or borrowed reference stations. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Specific conductivity (uS/cm)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Specific conductance (µS/cm). NRSA reports the lab value of a mid-channel grab sample at the X-site (the reach center); a calibrated multi-parameter meter reads the same quantity in the stream there.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Specific conductance; higher indicates ionic disturbance (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Water &amp; soil quality</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or published benchmark.</p></div>
</div></details>

<details class="metric-ref">
<summary>Total nitrogen (mg N/L)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Total nitrogen (mg N/L), lab analysis of the unfiltered mid-channel grab sample from the X-site, kept on ice.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Total nitrogen; higher indicates nutrient enrichment (worse). The total fraction is reported in every NRSA cycle and is the one published nitrogen criteria use.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Nutrient cycling</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or published benchmark.</p></div>
</div></details>

<details class="metric-ref">
<summary>Total phosphorus (ug/L)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Total phosphorus (µg/L), lab analysis of the mid-channel grab sample from the X-site, kept on ice.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Total phosphorus; higher indicates nutrient enrichment (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Nutrient cycling</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or published benchmark.</p></div>
</div></details>

<details class="metric-ref">
<summary>Turbidity (NTU)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Turbidity (NTU), lab analysis of the mid-channel grab sample from the X-site. Sample before anyone walks the channel upstream.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Turbidity; higher indicates more suspended sediment (worse).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Water &amp; soil quality, Water and soil quality</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations, national reference or modeled reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

### Biology

<details class="metric-ref">
<summary>Benthic macroinvertebrate MMI (MMI points)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Computed: from the 11-transect composite D-frame sample (500 µm mesh) and its fixed-count lab subsample, score EPA&#x27;s 6 regional benthic metrics against the published floors and ceilings and rescale the sum to 0 to 100 (NRSA 2018-19 technical support document).</p>
<p class="metric-ref-def"><b>What it indicates.</b> NRSA benthic macroinvertebrate multimetric index (0 to 100); higher is better. EPA&#x27;s Wadeable Streams Assessment MMI of six metrics scored per NARS-9 region, the same index in every survey cycle; EPA&#x27;s own condition benchmarks are the published criterion it can be scored against.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Community dynamics</p><p class="metric-ref-meta">Curve basis: published benchmark.</p></div>
</div></details>

<details class="metric-ref">
<summary>Benthic total taxa richness</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the 11-transect composite D-frame sample (500 µm mesh) and its lab subsample. Value: total number of taxa.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Total benthic taxa richness; higher is better.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>EPT taxa richness</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Take a D-frame net sample (500 µm mesh) 1 m downstream of each of the 11 transects at the assigned left, center or right station, and composite them. A lab identifies a fixed-count subsample. Value: number of mayfly, stonefly and caddisfly taxa.</p>
<p class="metric-ref-def"><b>What it indicates.</b> EPT (mayfly/stonefly/caddisfly) taxa richness; more sensitive taxa indicate better condition.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Community dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Fast-water / riffle habitat (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> At each thalweg station, record the channel unit (falls, cascade, rapid, riffle, glide, pool, dry). Value: percent of stations in falls, cascade, rapid or riffle.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent fast-water (riffle/run) habitat; more generally better for many biota. Ecoregion-dependent.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Habitat provision</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Fish MMI (MMI points)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Computed: from the upstream electrofishing pass (every habitat between the transects, every fish identified and counted), score EPA&#x27;s 8 regional fish metrics, several adjusted for watershed area, and rescale to 0 to 100 (NRSA 2018-19 technical support document).</p>
<p class="metric-ref-def"><b>What it indicates.</b> NRSA fish multimetric index (0 to 100); higher is better. Eight metrics per NARS-9 region, several adjusted for watershed area, scored from an electrofishing sample; sites sampled by seining carry no index.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis: published benchmark.</p></div>
</div></details>

<details class="metric-ref">
<summary>Mapped dams within one mile (dams)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Dams in the USACE National Inventory of Dams within 1 mile of the site, counted by DEEP. Note in the field any barrier the inventory does not show.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Desktop: DEEP fills it in from desktop data, and the value stays editable.</p><p class="metric-ref-meta">Serves: Watershed connectivity</p><p class="metric-ref-meta">Curve basis: the fixed criteria below, in every regional assessment.</p></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Fixed criteria, the same in every region</div>
<p class="metric-ref-note">DEEP scores the value on a continuous curve through these bands (index 0.69 and 0.39 at the band edges). EASI lists these criteria as provisional, so the breakpoints may be revised.</p>
<div class="gfp-charts"><figure class="gfp"><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>0 mapped dams</span></div><div class="gfp-seg fair"><b>Fair</b><span>1 mapped dam</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥2 mapped dams</span></div></div></figure></div>
<ul class="metric-ref-sources"><li>USACE National Inventory of Dams</li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Native fish taxa richness</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> Electrofish the reach upstream through every habitat between the transects, identifying and counting every fish. Value: number of native taxa.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Native fish taxa richness; higher is better.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Native non-tolerant fish taxa (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the upstream electrofishing pass over every habitat between the transects (every fish identified and counted). Value: percent of native taxa not classed tolerant.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent of native fish taxa not classed tolerant (TOLERANCE_NRSA); higher is better. As a proportion, it does not scale with a region&#x27;s species pool the way richness does.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or borrowed reference stations.</p></div>
</div></details>

<details class="metric-ref">
<summary>Native non-tolerant fish taxa richness</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the upstream electrofishing pass over every habitat between the transects (every fish identified and counted). Value: number of native taxa not classed tolerant.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis: borrowed reference stations.</p></div>
</div></details>

<details class="metric-ref">
<summary>Natural fish cover (frac)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> In the 10 m of channel centered on each transect, estimate the areal cover class of each fish cover type on the form (wood, brush, overhanging vegetation, undercut banks, boulders, others). Value: summed cover of the natural types, averaged over the transects.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Natural fish cover; higher is better.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Habitat provision</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations or borrowed reference stations.</p></div>
</div></details>

<details class="metric-ref">
<summary>Shannon diversity (H&#x27;)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the 11-transect composite D-frame sample (500 µm mesh) and its lab subsample. Value: Shannon diversity of the taxa counted.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Benthic Shannon diversity; higher diversity indicates better condition.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Community dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations, national reference or modeled reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Simple lithophil fish individuals (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the upstream electrofishing pass over every habitat between the transects (every fish identified and counted). Value: percent of native individuals that are simple lithophils, spawning on clean coarse substrate without a nest.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent of native fish individuals that are simple lithophils, spawning on clean coarse substrate without nest building; higher is better. Reproduction depends on substrate that fine sediment smothers.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis, by assessment: borrowed reference stations or national reference.</p></div>
</div></details>

<details class="metric-ref">
<summary>Tolerant individuals (%) ~ HBI</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the 11-transect composite D-frame sample (500 µm mesh) and its lab subsample. Value: percent of individuals in pollution-tolerant taxa.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent tolerant individuals; a higher share of pollution-tolerant taxa indicates worse condition.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Community dynamics</p><p class="metric-ref-meta">Curve basis, by assessment: own reference stations, borrowed reference stations or national reference. Some assessments withhold it for insufficient reference support.</p></div>
</div></details>

<details class="metric-ref">
<summary>Tolerant native fish individuals (%)</summary>
<div class="metric-ref-body">
<p class="metric-ref-def"><b>How to measure.</b> From the upstream electrofishing pass over every habitat between the transects (every fish identified and counted). Value: percent of native individuals in tolerant taxa.</p>
<p class="metric-ref-def"><b>What it indicates.</b> Percent of native fish individuals classed tolerant (TOLERANCE_NRSA); higher is worse. Dominance by tolerant fish is a standard multimetric-index component of disturbance.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">In DEEP</div><p class="metric-ref-meta">Field: measured at the site.</p><p class="metric-ref-meta">Serves: Population support</p><p class="metric-ref-meta">Curve basis: national reference.</p></div>
</div></details>
<!-- END GENERATED METRIC REFERENCE -->

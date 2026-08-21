---
title: EASI Walkthrough
nav_order: 10
description: "Video walkthrough of the EASI screening app, with the full reference for its 20 automated screening metrics."
---
{% include staf_page_chrome.html %}

<p>A guided tour of running a screening assessment in EASI (Ecosystem Assessment Screening Index).</p>

<video class="walkthrough-video" controls preload="metadata" src="{{ '/assets/videos/easi-walkthrough.mp4' | relative_url }}?v=20260802">
  Your browser cannot play this video. <a href="{{ '/assets/videos/easi-walkthrough.mp4' | relative_url }}">Download the video</a> instead.
</video>

<!--
  Reference content for the Screening tier metrics computed automatically by the EASI app.
  The section between the BEGIN/END GENERATED markers is written by
    python apps/easi/scripts/build_walkthrough_reference.py
  from apps/easi/data/screening-methods.json (the scoring catalog) and
  apps/easi/easi/config.py METRIC_DEFINITIONS. Edit those sources and regenerate.
  Do not hand-edit the generated section.
-->

## Screening metric reference

<p class="metric-ref-intro">The Screening tier rates twenty stream-function metrics automatically from national desktop data, as described on the <a href="{{ site.baseurl }}/scoring/">Scoring and Condition</a> page. The screening app shows the inputs and the Good / Fair / Poor criteria for each metric while you work. The reference below is the fuller detail: what each metric measures, where its scoring boundaries come from, why each input is used, and the limits to keep in mind when reading a result.</p>


<!-- BEGIN GENERATED METRIC REFERENCE -->
### Hydrology

<details class="metric-ref">
<summary>Watershed Land-Cover Pressure</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Runoff and infiltration sustain natural flow regime, carry appropriate sediment and nutrients from uplands, and reliably cue spawning/migration of aquatic life.</p>
<p class="metric-ref-def">Watershed land-cover pressure on catchment hydrology. Scored automatically on the more limiting of two indicators: impervious cover (speeds runoff) or agricultural cover (where farming, not pavement, is the dominant hydrologic alteration).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Watershed impervious cover (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;10%</span></div><div class="gfp-seg fair"><b>Fair</b><span>10-25%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;25%</span></div></div></figure><figure class="gfp"><figcaption>Watershed agricultural cover (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;30%</span></div><div class="gfp-seg fair"><b>Fair</b><span>30-50%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;50%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>10% impervious</td><td>Boundary between the sensitive and impacted classes of the Impervious Cover Model (Schueler 1994).</td></tr><tr><td>25% impervious</td><td>Boundary of the non-supporting class of the Impervious Cover Model (Schueler 1994).</td></tr><tr><td>30% agriculture</td><td>Lower agricultural transition. Fish-index decline and subsidy-stress responses are reported above about 30 percent watershed agriculture (Allan 2004).</td></tr><tr><td>50% agriculture</td><td>Upper agricultural screen. Habitat and biotic integrity decline become apparent above 50 percent watershed agriculture (Wang et al. 1997), aligned with NRSA reference-site screening practice.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Watershed impervious cover:</b> Impervious cover represents altered infiltration, runoff routing, and hydrologic flashiness.</li><li><b>Watershed agricultural cover:</b> Agricultural cover represents a separate soil-disturbance, drainage, and runoff pathway that impervious cover does not capture.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>The worse input governs because hydrologic alteration through one pathway is not offset by low pressure in the other.</li><li>Agricultural bands are national screening tiers, not ecological criteria.</li><li>A result computed from one input is labeled partial.</li><li>Sensitive macroinvertebrate taxa decline well below the 10 percent impervious Good boundary (community thresholds near 0.5 to 2 percent), so a Good rating is not a no-effect finding (King et al. 2011).</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: High &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/system/files/documents/2024-12/nrsa-2018-19-tsd-final-11252024.pdf" target="_blank" rel="noopener noreferrer">National Rivers and Streams Assessment 2018-19 Technical Support Document</a></li><li><a href="https://pinelakedistrict.org/doc/resources/The%20Importance%20of%20Imperviousness.pdf" target="_blank" rel="noopener noreferrer">Schueler 1994, The importance of imperviousness</a></li><li><a href="https://doi.org/10.1146/annurev.ecolsys.35.120202.110122" target="_blank" rel="noopener noreferrer">Allan 2004, Landscapes and riverscapes: the influence of land use on stream ecosystems</a></li><li><a href="https://doi.org/10.1577/1548-8446(1997)022%3C0006:IOWLUO%3E2.0.CO;2" target="_blank" rel="noopener noreferrer">Wang et al. 1997, Influences of watershed land use on habitat quality and biotic integrity in Wisconsin streams</a></li><li><a href="https://doi.org/10.1890/10-1357.1" target="_blank" rel="noopener noreferrer">King et al. 2011, Stream community thresholds at exceptionally low levels of catchment urbanization</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Percent Wetlands in Watershed</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Wetlands and storage features store floodwater, recharge groundwater, sustain baseflow, and provide low-velocity habitat.</p>
<p class="metric-ref-def">Share of the watershed that is wetland, which stores water, buffers peak flows, and sustains baseflow.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Wetland extent (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&gt;5%</span></div><div class="gfp-seg fair"><b>Fair</b><span>1-5%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;1%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>1%</td><td>Lower national screening transition. Watershed wetland extents of 1 to 5 percent are associated with water-quality benefit and about 7 percent with flood attenuation (Mitsch and Gosselink, as reviewed by the Chesapeake Bay Program 2016).</td></tr><tr><td>5%</td><td>Upper national screening transition, the top of the same reviewed 1 to 5 percent benefit range.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Woody wetland cover:</b> Woody wetlands contribute surface storage and hydrologic attenuation.</li><li><b>Herbaceous wetland cover:</b> Herbaceous wetlands contribute storage not represented by woody-wetland cover.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Natural wetland abundance varies substantially among regions.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: provisional STAF screening judgment &middot; Data confidence: High &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-metrics-and-definitions" target="_blank" rel="noopener noreferrer">EPA StreamCat Metrics and Definitions</a></li><li><a href="https://d18lev1ok5leia.cloudfront.net/chesapeakebay/documents/appendix_b_wetlands_expert_panel_literature_review_habitat_benefits_mar2016.pdf" target="_blank" rel="noopener noreferrer">Chesapeake Bay Program 2016, Wetland Expert Panel literature review (Appendix B)</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Concentrated Runoff / Stormwater Inputs</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Quantity + quality of inflow (tributaries, ditches, and pipes) does not provide harmful peaks or pollution, and supports diverse habitat conditions.</p>
<p class="metric-ref-def">Road density in the watershed, a stand-in for concentrated runoff pressure. It does not count actual outfalls or crossings.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Road-density proxy for concentrated inflow pressure (km/km²)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;1 km/km²</span></div><div class="gfp-seg fair"><b>Fair</b><span>1-&lt;3 km/km²</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥3 km/km²</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>1 km/km²</td><td>Lower STAF screening transition. It is not a demonstrated ecological threshold.</td></tr><tr><td>3 km/km²</td><td>Upper transition grounded in NRSA least-disturbed-site road-density screening.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Watershed road density:</b> Road density is a directional proxy for concentrated runoff sources and hillslope-to-channel connectivity.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Road density does not count actual outfalls or establish whether runoff is treated.</li><li>The 1 km/km² breakpoint is a screening judgment.</li><li>USFS watershed-condition guidance classes road density more strictly (good under 1, impaired over 2.4 miles per square mile, about 0.62 and 1.49 km/km²), derived from salmonid watersheds. EASI keeps the NRSA-anchored national screen and discloses the stricter alternative.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/system/files/documents/2024-12/nrsa-2018-19-tsd-final-11252024.pdf" target="_blank" rel="noopener noreferrer">National Rivers and Streams Assessment 2018-19 Technical Support Document</a></li><li><a href="https://www.fs.usda.gov/biology/resources/pubs/watershed/maps/watershed_classification_guide2011FS978.pdf" target="_blank" rel="noopener noreferrer">USDA Forest Service 2011, Watershed Condition Classification Technical Guide (FS-978)</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Flow Alteration (Regulation / Water Use)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Integrates the range of &quot;typical&quot; flows experienced by other processes. Examines the degree to which upstream infrastructure or land uses have fundamentally altered flow regimes (e.g., hydropeaking, dams, withdrawals).</p>
<p class="metric-ref-def">Degree of regulation: upstream reservoir storage relative to the river&#x27;s annual runoff volume. It does not account for how dams are operated or for diversions.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Flow alteration (degree of regulation) (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;2%</span></div><div class="gfp-seg fair"><b>Fair</b><span>2-15%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;15%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>2% DOR</td><td>Onset-of-regulation boundary. Comparative global assessment treats storage above 2 percent of annual flow as regulated (Lehner et al. 2011).</td></tr><tr><td>15% DOR</td><td>Severe-regulation boundary. The free-flowing rivers analysis uses 15 percent as its operational pressure limit, the high end of a published 2 to 15 percent range (Grill et al. 2019). It is not a regulatory standard.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Upstream normalized storage:</b> Upstream storage is the regulated volume available to alter the natural flow regime.</li><li><b>Annual runoff:</b> Annual runoff converts storage to a dimensionless degree of regulation relative to the river&#x27;s annual water volume.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Degree of regulation does not describe operating rules, diversions, seasonality, or dam location on the network.</li><li>Missing or non-positive runoff is not converted to zero and leaves the metric unscored.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published threshold &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-metrics-and-definitions" target="_blank" rel="noopener noreferrer">EPA StreamCat Metrics and Definitions</a></li><li><a href="https://esajournals.onlinelibrary.wiley.com/doi/10.1890/100125" target="_blank" rel="noopener noreferrer">Lehner et al. 2011, High-resolution mapping of the world&#x27;s reservoirs and dams</a></li><li><a href="https://www.nature.com/articles/s41586-019-1111-9" target="_blank" rel="noopener noreferrer">Grill et al. 2019, Mapping the world&#x27;s free-flowing rivers</a></li></ul>
</div>
</div></details>

### Hydraulics

<details class="metric-ref">
<summary>Low-flow Wetted Connectivity</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Ensures habitat availability and water quality during low water levels, and indicates non-storm conditions experienced the majority of the time.</p>
<p class="metric-ref-def">Whether the channel stays wetted and longitudinally connected during low-flow periods (flow permanence).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Low-flow condition (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&gt;75%</span></div><div class="gfp-seg fair"><b>Fair</b><span>25-75%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;25%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>25% wetted</td><td>Marginal-to-poor boundary of the EPA Rapid Bioassessment Protocols Channel Flow Status categories, collapsed to three classes (Barbour et al. 1999). Applies where persistent flow is naturally expected.</td></tr><tr><td>75% wetted</td><td>Suboptimal-to-marginal boundary of the same RBP categories. Applies where persistent flow is naturally expected.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Wetted channel:</b> The NRSA reach survey directly records the dry-channel fraction. Its complement describes wetted-channel continuity during the survey visit.</li><li><b>Expected NHD flow regime:</b> <span class="metric-ref-flag">context only</span> FCODE provides context for natural intermittency but does not itself determine condition.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>NRSA wetted-channel observation:</b> Use an exact COMID first, then a NLDI-confirmed connected mainstem site within five miles and ten years.</li><li><b>StreamCat HYD integrity fallback:</b> Use only when eligible connected NRSA evidence is unavailable.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A connected nearby NRSA visit is not necessarily representative of the selected reach or current season.</li><li>Natural intermittent and ephemeral reaches must be evaluated against their expected flow regime.</li><li>FCODE remains context only and never supplies the rating.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/data-national-aquatic-resource-surveys" target="_blank" rel="noopener noreferrer">EPA National Aquatic Resource Surveys Data</a></li><li><a href="https://www.epa.gov/system/files/documents/2024-12/nrsa-2018-19-tsd-final-11252024.pdf" target="_blank" rel="noopener noreferrer">National Rivers and Streams Assessment 2018-19 Technical Support Document</a></li><li><a href="https://www.epa.gov/sites/production/files/2019-02/documents/rapid-bioassessment-streams-rivers-1999.pdf" target="_blank" rel="noopener noreferrer">EPA Rapid Bioassessment Protocols for Streams and Wadeable Rivers</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Floodplain Engagement Frequency (bankfull recurrence)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Peak flows reshape channel without chronic instability, redistribute wood and sediment, reset habitats, and supply nutrients to floodplains.</p>
<p class="metric-ref-def">How readily high flows reach the floodplain, from the bank-height ratio rather than a modeled recurrence interval.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Floodplain engagement (bank-height ratio) (ratio)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≤1.3</span></div><div class="gfp-seg fair"><b>Fair</b><span>&gt;1.3-≤1.5</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;1.5</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>BHR 1.3</td><td>Upper boundary of the functioning floodplain-connectivity class.</td></tr><tr><td>BHR 1.5</td><td>Boundary above which published stream-quantification guidance identifies non-functioning connectivity.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Bank-height ratio:</b> BHR directly measures the vertical separation between bankfull flow and the low floodplain surface.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A BHR below 1.0 remains Good but requires geometry verification.</li><li>The DEM-derived cross section is a screening estimate and may require surveyed geometry.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published threshold &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://stream-mechanics.com/wp-content/uploads/2020/10/CSQT-v1_Science-Document-1.pdf" target="_blank" rel="noopener noreferrer">Colorado Stream Quantification Tool v1.0 Science Document</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Floodplain Access / Entrenchment</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Frequent overbank flows reduce flood peaks, support riparian vegetation, create off-channel refugia, and extend nutrient processing time.</p>
<p class="metric-ref-def">Whether the channel has lateral access to a floodplain, via the entrenchment ratio (floodprone width ÷ bankfull width).</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Floodplain access (entrenchment ratio) (ratio)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≥2.2</span></div><div class="gfp-seg fair"><b>Fair</b><span>1.4-&lt;2.2</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;1.4</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>ER 1.4</td><td>Published boundary between entrenched and moderately accessible floodplain forms.</td></tr><tr><td>ER 2.2</td><td>Published boundary for broad floodplain access.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Entrenchment ratio:</b> ER directly represents lateral floodplain width available relative to the bankfull channel.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>This is an unstratified confinement/access screen.</li><li>Naturally confined valley settings require field interpretation.</li><li>Published condition standards stratify by stream type. Alluvial C and E channels are functioning above 2.2 and not functioning below 2.0, while confined B channels are functioning above 1.4. This unstratified screen is therefore lenient for alluvial reaches and strict for naturally confined ones.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published threshold &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://stream-mechanics.com/wp-content/uploads/2020/10/CSQT-v1_Science-Document-1.pdf" target="_blank" rel="noopener noreferrer">Colorado Stream Quantification Tool v1.0 Science Document</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Hyporheic Exchange Indicators</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Surface water-groundwater exchange moderates temperature, transforms nutrients, supplies oxygen, and supports aquatic life.</p>
<p class="metric-ref-def">Potential for surface water to exchange with the shallow subsurface (hyporheic zone), screened from channel slope. Sinuosity is shown for context and is not rated.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Hyporheic-exchange potential (channel gradient) (m/m)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≥0.006 m/m</span></div><div class="gfp-seg fair"><b>Fair</b><span>0.003-&lt;0.006 m/m</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;0.003 m/m</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>0.003 m/m</td><td>Lower screening transition, carried over from the prior exchange-potential index. A screening judgment, not a published threshold.</td></tr><tr><td>0.006 m/m</td><td>Upper screening transition, carried over from the prior exchange-potential index. A screening judgment, not a published threshold.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Channel slope:</b> Slope sets the vertical hydraulic gradient that drives bed exchange, the dominant hyporheic pathway in national syntheses.</li><li><b>Reach sinuosity:</b> <span class="metric-ref-flag">context only</span> Sinuosity indicates lateral exchange potential and is shown for context. It is not rated because sinuosity measured over the fixed assessment reach understates planform sinuosity.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Slope screens vertical exchange potential only. Bed hydraulic conductivity is a co-dominant control that is unavailable, so steep reaches over bedrock or fine beds can overpredict exchange.</li><li>Sinuosity is displayed as context and is not rated. Short fixed-length reaches understate planform sinuosity.</li><li>The slope bands are screening judgments, not published thresholds.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: provisional STAF screening judgment &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-metrics-and-definitions" target="_blank" rel="noopener noreferrer">EPA StreamCat Metrics and Definitions</a></li><li><a href="https://www.epa.gov/sites/production/files/2015-08/documents/a_function_based_framework_for_stream_assessment_3.pdf" target="_blank" rel="noopener noreferrer">EPA Function-Based Framework for Stream Assessment and Restoration Projects</a></li><li><a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC8312628/" target="_blank" rel="noopener noreferrer">Harvey et al., How hydrologic connectivity regulates water quality in river corridors (NEXSS synthesis)</a></li></ul>
</div>
</div></details>

### Geomorphology

<details class="metric-ref">
<summary>Channel Evolution Stage &amp; Trends</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Long-term changes in channel size and slope maintain dynamic equilibrium and diverse habitats while interacting with the floodplain.</p>
<p class="metric-ref-def">Whether the channel is stable or actively incising/widening, its stage in the channel-evolution sequence.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Bank-height ratio (ratio)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≤1.3</span></div><div class="gfp-seg fair"><b>Fair</b><span>&gt;1.3-≤1.5</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;1.5</span></div></div></figure><figure class="gfp"><figcaption>Entrenchment ratio (ratio)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≥2.2</span></div><div class="gfp-seg fair"><b>Fair</b><span>1.4-&lt;2.2</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;1.4</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>BHR 1.3</td><td>Upper boundary for functioning floodplain connection.</td></tr><tr><td>BHR 1.5</td><td>Boundary above which incision/disconnection is Poor.</td></tr><tr><td>ER 1.4</td><td>Boundary below which lateral floodplain access is Poor.</td></tr><tr><td>ER 2.2</td><td>Boundary at which broad floodplain access is Good.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Bank-height ratio:</b> BHR is a documented incision and floodplain-disconnection clue used in channel-evolution interpretation.</li><li><b>Entrenchment ratio:</b> ER indicates lateral confinement and access to flood-prone width, another documented clue to adjustment state.</li><li><b>NHD feature classification:</b> <span class="metric-ref-flag">context only</span> A canal/ditch classification is decisive. Other FCODEs are neutral and cannot imply stable condition.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>Documented channel-stage assessment:</b> A user-documented stable/recovered, moderately adjusting, or severely adjusting condition supersedes the proxy.</li><li><b>Canal/ditch classification:</b> A canal/ditch FCODE is directly classified Poor.</li><li><b>BHR and ER susceptibility proxy:</b> For other reaches, the worse BHR or ER index governs.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>BHR and ER indicate susceptibility and provide channel-evolution clues. They do not establish a formal stage.</li><li>Formal interpretation also considers headcuts, bars, width/depth change, bank erosion, and recovery features.</li><li>BHR and ER are reused by other metrics and are therefore correlated evidence.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.mvp.usace.army.mil/Portals/57/docs/regulatory/Mitigation/MN_SQT/Scientific_Support_for_the_MNSQT_v1.0.pdf" target="_blank" rel="noopener noreferrer">Scientific Support for the Minnesota Stream Quantification Tool v1.0</a></li><li><a href="https://www.epa.gov/sites/production/files/2015-08/documents/a_function_based_framework_for_stream_assessment_3.pdf" target="_blank" rel="noopener noreferrer">EPA Function-Based Framework for Stream Assessment and Restoration Projects</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Bank Erosion &amp; Armoring Condition</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Examines the role of bank processes and erosion in channel change. Observes patterns in sinuosity and curvature affecting habitat complexity.</p>
<p class="metric-ref-def">Susceptibility to bank erosion, from the bank-height ratio unless bank observations are entered. The proxy does not detect existing armoring.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Bank erosion and armoring (ratio)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≤1.3</span></div><div class="gfp-seg fair"><b>Fair</b><span>&gt;1.3-≤1.5</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;1.5</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>BHR 1.3</td><td>Upper published functioning boundary, used here as the Good/Fair susceptibility transition.</td></tr><tr><td>BHR 1.5</td><td>Published non-functioning floodplain-connectivity boundary, used here as the Fair/Poor susceptibility transition.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Bank-height ratio:</b> BHR is an established incision and floodplain-connectivity measure used here only as a susceptibility proxy, not as measured erosion or armoring.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>Observed erosion and armoring:</b> Complete user observations of both components supersede the proxy.</li><li><b>BHR susceptibility fallback:</b> Used automatically when complete bank observations are unavailable.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>BHR does not detect existing armoring and does not directly measure erosion rate or eroding-bank extent.</li><li>The DEM-derived cross section is a susceptibility screen and may require surveyed geometry.</li><li>BHR is reused by floodplain engagement and channel adjustment and is therefore correlated evidence.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.mvp.usace.army.mil/Portals/57/docs/regulatory/Mitigation/MN_SQT/Scientific_Support_for_the_MNSQT_v1.0.pdf" target="_blank" rel="noopener noreferrer">Scientific Support for the Minnesota Stream Quantification Tool v1.0</a></li><li><a href="https://www.epa.gov/sites/production/files/2015-08/documents/a_function_based_framework_for_stream_assessment_3.pdf" target="_blank" rel="noopener noreferrer">EPA Function-Based Framework for Stream Assessment and Restoration Projects</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Sediment Supply Potential (watershed/banks)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Balanced sediment supply and transport preserves bed elevations, substrate sizes, spawning/benthic habitats, and supports riparian succession.</p>
<p class="metric-ref-def">Potential for excess sediment from watershed sources, scored on the most limiting of agricultural cover, soil erodibility, and road density. Bank-derived supply is not represented.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Agricultural cover (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;30%</span></div><div class="gfp-seg fair"><b>Fair</b><span>30-50%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;50%</span></div></div></figure><figure class="gfp"><figcaption>Soil erodibility K-factor</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;0.25</span></div><div class="gfp-seg fair"><b>Fair</b><span>0.25-0.40</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;0.40</span></div></div></figure><figure class="gfp"><figcaption>Road density (km/km²)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;1.24 km/km²</span></div><div class="gfp-seg fair"><b>Fair</b><span>1.24-&lt;1.86 km/km²</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥1.86 km/km²</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>30% agriculture</td><td>Lower agricultural transition (Allan 2004), shared with the land-cover pressure metric.</td></tr><tr><td>50% agriculture</td><td>Upper agricultural transition (Wang et al. 1997).</td></tr><tr><td>K 0.25</td><td>Lower boundary of the moderate RUSLE erodibility class (silt loams).</td></tr><tr><td>K 0.40</td><td>Upper boundary of the moderate RUSLE erodibility class. Values above 0.40 mark the most erodible high-silt soils.</td></tr><tr><td>1.24 km/km²</td><td>2 miles per square mile, the properly-functioning limit in NMFS salmonid watershed criteria (as compiled in KRIS).</td></tr><tr><td>1.86 km/km²</td><td>3 miles per square mile, the not-properly-functioning boundary in the same criteria. Fine sediment in spawning gravels increases above about this density (Cedarholm et al. 1981).</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Agricultural cover:</b> Agriculture represents the dominant disturbed upland sediment-source pathway.</li><li><b>Soil erodibility K-factor:</b> K-factor represents inherent soil erodibility, the susceptibility term no other metric screens.</li><li><b>Road density:</b> Roads are both a sediment source and a hillslope-to-channel connectivity pathway.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>The worse input governs. Source indicators are screened separately and are not summed.</li><li>Intrinsic soil erodibility (K-factor) can lower the rating in undisturbed watersheds. It indicates susceptibility rather than an active source.</li><li>The road-density bands derive from salmonid watershed criteria applied here nationally.</li><li>Agricultural cover and road density are also rated by other metrics and are therefore correlated evidence.</li><li>All three inputs are required. Missing inputs are not converted to zero.</li><li>Bank-derived sediment supply is not represented. The method screens watershed sources only.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Moderate &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-metrics-and-definitions" target="_blank" rel="noopener noreferrer">EPA StreamCat Metrics and Definitions</a></li><li><a href="https://doi.org/10.1146/annurev.ecolsys.35.120202.110122" target="_blank" rel="noopener noreferrer">Allan 2004, Landscapes and riverscapes: the influence of land use on stream ecosystems</a></li><li><a href="https://doi.org/10.1577/1548-8446(1997)022%3C0006:IOWLUO%3E2.0.CO;2" target="_blank" rel="noopener noreferrer">Wang et al. 1997, Influences of watershed land use on habitat quality and biotic integrity in Wisconsin streams</a></li><li><a href="https://iwr.msu.edu/rusle/kfactor.htm" target="_blank" rel="noopener noreferrer">RUSLE soil erodibility factor classes (Michigan State University Institute of Water Research)</a></li><li><a href="http://www.krisweb.com/krisrussian/krisdb/html/krisweb/watershd/roads.htm" target="_blank" rel="noopener noreferrer">NMFS 1996 salmonid watershed road-density criteria (as compiled in KRIS)</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Substrate Condition (grain size/embeddedness/fines/consolidation)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Supports aquatic habitats through streambed material and bedform dynamics. Enhances habitat complexity and streambank stability.</p>
<p class="metric-ref-def">Quality of the streambed substrate (grain size, embeddedness, fines) that supports habitat and spawning.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Substrate condition (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;25%</span></div><div class="gfp-seg fair"><b>Fair</b><span>25-75%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;75%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>25% embeddedness</td><td>Optimal-class boundary of the EPA Rapid Bioassessment Protocols embeddedness categories, collapsed to three classes (Barbour et al. 1999).</td></tr><tr><td>75% embeddedness</td><td>Poor-class boundary of the same RBP categories.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Substrate embeddedness:</b> NRSA XEMBED directly estimates the percentage of coarse substrate surfaces embedded by fine material.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>NRSA embeddedness observation:</b> Use an exact COMID first, then a NLDI-confirmed connected mainstem site within five miles and ten years.</li><li><b>StreamCat SED integrity fallback:</b> Use only when eligible connected NRSA embeddedness is unavailable.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A connected nearby NRSA visit may not represent the selected reach&#x27;s bed condition.</li><li>Embeddedness does not fully characterize grain size, fine-sediment depth, consolidation, or large wood.</li><li>The landscape fallback estimates sediment integrity rather than observed substrate condition.</li><li>Embeddedness applies to gravel and coarser beds. The RBP treats it as a high-gradient parameter, and the survey convention scores sand and finer beds as 100 percent embedded, so naturally fine-bedded streams (for example coastal plain channels) can rate Poor by nature. Interpret against the expected natural bed.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/data-national-aquatic-resource-surveys" target="_blank" rel="noopener noreferrer">EPA National Aquatic Resource Surveys Data</a></li><li><a href="https://www.epa.gov/system/files/documents/2024-12/nrsa-2018-19-tsd-final-11252024.pdf" target="_blank" rel="noopener noreferrer">National Rivers and Streams Assessment 2018-19 Technical Support Document</a></li><li><a href="https://www.epa.gov/sites/production/files/2019-02/documents/rapid-bioassessment-streams-rivers-1999.pdf" target="_blank" rel="noopener noreferrer">EPA Rapid Bioassessment Protocols for Streams and Wadeable Rivers</a></li></ul>
</div>
</div></details>

### Physicochemistry

<details class="metric-ref">
<summary>Stream Temperature</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Shade, solar input, and groundwater keep temperature and light within natural ranges for stream metabolism and dissolved oxygen.</p>
<p class="metric-ref-def">Vulnerability to warming, scored on the more limiting of woody riparian shade and watershed impervious cover. It does not estimate stream temperature.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Woody riparian cover (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>≥75%</span></div><div class="gfp-seg fair"><b>Fair</b><span>25-&lt;75%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;25%</span></div></div></figure><figure class="gfp"><figcaption>Watershed impervious cover (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&lt;10%</span></div><div class="gfp-seg fair"><b>Fair</b><span>10-25%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&gt;25%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>25% woody</td><td>Lower provisional shade-potential transition.</td></tr><tr><td>75% woody</td><td>Upper provisional shade-potential transition.</td></tr><tr><td>10% impervious</td><td>Lower impervious-cover pressure transition.</td></tr><tr><td>25% impervious</td><td>Upper impervious-cover pressure transition.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Woody riparian cover:</b> Woody cover represents potential shade and thermal buffering. Grass and herbaceous wetland do not receive thermal-shade credit.</li><li><b>Watershed impervious cover:</b> Impervious cover represents heated runoff and altered runoff routing.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>This method estimates vulnerability to thermal loading and loss of shade. It does not estimate stream temperature.</li><li>Both inputs are required and the specific bands are provisional screening classes.</li><li>Valid WQP temperature observations may be displayed as context but are not scored without an applicable class, season, and exposure rule.</li><li>The 25 and 75 percent woody bands are EASI conventions. Shade studies place intact forest near 85 percent shade, but no published corridor-percent thresholds exist.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/caddis/temperature" target="_blank" rel="noopener noreferrer">EPA CADDIS: Temperature</a></li><li><a href="https://www.epa.gov/caddis/urbanization-stormwater-runoff" target="_blank" rel="noopener noreferrer">EPA CADDIS: Urbanization and Stormwater Runoff</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Detrital Processing (CPOM retention / shredders)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Organic matter is captured and broken down, fueling food webs, balancing production/ respiration, moderating pH/ redox, and supplying nutrients.</p>
<p class="metric-ref-def">Potential supply of organic matter from riparian vegetation (forest, shrub, grassland, and wetland combined). It does not measure litter retention or shredder populations.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Organic-matter supply potential proxy (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&gt;50%</span></div><div class="gfp-seg fair"><b>Fair</b><span>&gt;20-≤50%</span></div><div class="gfp-seg poor"><b>Poor</b><span>≤20%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>20%</td><td>Lower provisional organic-matter supply transition.</td></tr><tr><td>50%</td><td>Upper provisional organic-matter supply transition.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Riparian forest:</b> Forest contributes woody and leaf-litter inputs.</li><li><b>Riparian shrub:</b> Shrub cover contributes litter and low woody material.</li><li><b>Riparian grassland:</b> Grassland contributes organic material in naturally non-forested settings.</li><li><b>Riparian wetland:</b> Wetland vegetation contributes organic matter and avoids a forest-only bias.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>The method estimates organic-matter supply potential, not CPOM retention or shredder condition.</li><li>All expected source classes must be present. Missing classes are not treated as zero.</li><li>Breakpoints are provisional STAF screening judgments.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: provisional STAF screening judgment &middot; Data confidence: Moderate &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-metrics-and-definitions" target="_blank" rel="noopener noreferrer">EPA StreamCat Metrics and Definitions</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Nitrogen &amp; Phosphorus Concentrations</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Nitrogen and phosphorus transformations feed primary production, regulate excess loads, and store or release nutrients.</p>
<p class="metric-ref-def">Whether nutrient (N and P) concentrations stay near reference levels rather than driving enrichment.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div><p class="metric-ref-note">Rated against the NRSA regional benchmarks for the site’s aggregate ecoregion. Each cell gives the Good boundary (at or below) and the Poor boundary (at or above). The worse available analyte governs.</p><table class="metric-ref-table"><thead><tr><th>Region</th><th>Total nitrogen (mg/L)</th><th>Total phosphorus (mg/L)</th></tr></thead><tbody><tr><td>CPL</td><td>0.624 / 1.081</td><td>0.0559 / 0.103</td></tr><tr><td>NAP</td><td>0.345 / 0.482</td><td>0.0171 / 0.0326</td></tr><tr><td>SAP</td><td>0.24 / 0.456</td><td>0.0148 / 0.0244</td></tr><tr><td>UMW</td><td>0.583 / 1.024</td><td>0.0363 / 0.0499</td></tr><tr><td>TPL</td><td>0.7 / 1.274</td><td>0.0886 / 0.143</td></tr><tr><td>NPL</td><td>0.575 / 0.937</td><td>0.064 / 0.107</td></tr><tr><td>SPL</td><td>0.581 / 1.069</td><td>0.0558 / 0.127</td></tr><tr><td>WMT</td><td>0.139 / 0.249</td><td>0.0177 / 0.041</td></tr><tr><td>XER</td><td>0.285 / 0.529</td><td>0.052 / 0.0959</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>Good/Fair</td><td>At or below the applicable NRSA Table 7-1 good/fair value is Good.</td></tr><tr><td>Fair/Poor</td><td>At or above the applicable NRSA Table 7-1 fair/poor value is Poor.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Total nitrogen:</b> TN represents observed nitrogen condition relative to the applicable NRSA region.</li><li><b>Total phosphorus:</b> TP represents observed phosphorus condition relative to the applicable NRSA region.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>Normalized WQP TN/TP observations:</b> Use valid total-fraction observations from stations within five miles and the preceding ten years.</li><li><b>StreamCat CHEM integrity fallback:</b> Use when no qualifying TN or TP observation can be normalized.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Nearby monitoring stations are not necessarily reach-specific.</li><li>Each station is reduced to a median before station medians are combined, preventing heavily sampled stations from dominating.</li><li>One analyte may produce a partial rating.</li><li>When observations are unavailable, the CHEM fallback represents landscape integrity rather than measured nutrient concentration.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published threshold &middot; Data confidence: Moderate-low</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/system/files/documents/2024-12/nrsa-2018-19-tsd-final-11252024.pdf" target="_blank" rel="noopener noreferrer">National Rivers and Streams Assessment 2018-19 Technical Support Document</a></li><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/ecoregions-used-national-aquatic-resource-surveys" target="_blank" rel="noopener noreferrer">Ecoregions Used in the National Aquatic Resource Surveys</a></li><li><a href="https://www.waterqualitydata.us/portal_userguide/" target="_blank" rel="noopener noreferrer">Water Quality Portal User Guide</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Regulatory Impairment Status (305b/303d/TMDL)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Physical and chemical processes limit pollutants, retain beneficial constituents, and protect ecological and human uses.</p>
<p class="metric-ref-def">Whether the reach is on a Clean Water Act impaired-waters list (303(d)/305(b)/TMDL) for water-quality problems.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Regulatory impairment</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>Category 1 · Category 2</span></div><div class="gfp-seg fair"><b>Fair</b><span>Category 4a · Category 4b</span></div><div class="gfp-seg poor"><b>Poor</b><span>Category 4c · Category 5</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>ATTAINS integrated-report category:</b> ATTAINS is the authoritative national source for assessed-water reporting categories.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>Conclusive ATTAINS category:</b> Use an intersecting or qualifying nearby Category 1, 2, 4a, 4b, 4c, or 5 assessment.</li><li><b>StreamCat CHEM condition fallback:</b> Use when ATTAINS is absent or Category 3 is inconclusive. The result is condition context, not a regulatory determination.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Category 3 remains unscored because evidence is insufficient.</li><li>A nearby unit within 2 km is explicitly labeled nearby and may not represent the selected reach.</li><li>When ATTAINS is absent or inconclusive, the CHEM fallback is labeled water-quality condition context and not a regulatory determination.</li><li>The category mapping reflects remedy status, not severity. A Category 4a water is as impaired as a Category 5 water but has an approved TMDL, so 4a and 4b rate Fair as impaired-with-a-management-pathway. Category 2 means no assessed use is failing, which is weaker than Category 1.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: High</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/waterdata/assessing-and-reporting-water-quality-questions-and-answers" target="_blank" rel="noopener noreferrer">EPA ATTAINS Reporting Categories</a></li></ul>
</div>
</div></details>

### Biology

<details class="metric-ref">
<summary>In-stream Habitat Complexity &amp; Cover</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Channel and floodplain structure supply depth, velocity, substrate diversity, and vegetation to support native organisms through all life stages.</p>
<p class="metric-ref-def">Woody riparian corridor cover as a stand-in for habitat support (cover, wood recruitment, bank structure). It is not a field inventory of pools, wood, or bedforms. Sinuosity is shown for context.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Habitat-support potential (woody riparian corridor) (%)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>&gt;70%</span></div><div class="gfp-seg fair"><b>Fair</b><span>50-70%</span></div><div class="gfp-seg poor"><b>Poor</b><span>&lt;50%</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>50% woody cover</td><td>Marginal-to-poor boundary of the EPA Rapid Bioassessment Protocols bank-vegetation condition categories, collapsed to three classes (Barbour et al. 1999).</td></tr><tr><td>70% woody cover</td><td>Suboptimal-to-marginal boundary of the same RBP categories. The RBP bins describe bank plots and are applied here to the 100 m corridor.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Woody riparian cover:</b> Woody riparian cover represents potential cover, wood recruitment, and bank-zone structure, the strongest desktop indicator of habitat support.</li><li><b>Reach sinuosity:</b> <span class="metric-ref-flag">context only</span> Sinuosity indicates planform variability and is shown for context. It is not rated because sinuosity measured over the fixed assessment reach understates planform sinuosity.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>Corridor woody cover is a habitat-support proxy, not a field inventory of pools, wood, cover, or bedforms.</li><li>The 50 and 70 percent bands are collapsed from RBP bank-vegetation categories that describe bank plots, applied here to the 100 m corridor.</li><li>Sinuosity is displayed as context and is not rated. Grass-dominated natural channels can provide habitat this proxy does not credit.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: published directional relationship &middot; Data confidence: Low &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/caddis/physical-habitat" target="_blank" rel="noopener noreferrer">EPA CADDIS Physical Habitat</a></li><li><a href="https://www.epa.gov/sites/production/files/2019-02/documents/rapid-bioassessment-streams-rivers-1999.pdf" target="_blank" rel="noopener noreferrer">EPA Rapid Bioassessment Protocols for Streams and Wadeable Rivers</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Biological Integrity (IBI / community condition)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Physical and chemical conditions enable spawning, juvenile growth, migration, and stress survival of target taxa.</p>
<p class="metric-ref-def">Condition of the aquatic community, from measured benthic or fish results where available, otherwise a labeled model or landscape-integrity estimate.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Biological integrity</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>Measured Good</span></div><div class="gfp-seg fair"><b>Fair</b><span>Measured Fair</span></div><div class="gfp-seg poor"><b>Poor</b><span>Measured Poor</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Governing measured condition class:</b> A measured, method-compatible biological condition class is the strongest evidence. When both communities are available, the worse class governs.</li><li><b>Benthic MMI class:</b> <span class="metric-ref-flag">context only</span> Benthic assemblages integrate habitat and water-quality stress over time.</li><li><b>Fish MMI class:</b> <span class="metric-ref-flag">context only</span> Fish assemblages integrate habitat, water quality, and longitudinal connectivity.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Automatic source hierarchy</div><ul class="metric-ref-list metric-ref-hierarchy"><li><b>Measured NRSA/state condition class:</b> Use compatible measured benthic and fish classes. The worse class governs, and one community alone is partial evidence.</li><li><b>Published BMMI probability:</b> Use StreamCat prG_BMMI where the reach is inside the published prediction frame.</li><li><b>Published ICI/IWI landscape products:</b> Outside the BMMI prediction frame, calculate the published catchment and watershed integrity products and use the lower value.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A connected nearby NRSA visit is not necessarily representative of the selected reach.</li><li>One measured community is partial evidence. When both are available, the worse class governs.</li><li>Predicted probabilities and integrity products remain modeled landscape condition and are never relabeled as measured IBI values.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: Moderate</p>
<ul class="metric-ref-sources"><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/data-national-aquatic-resource-surveys" target="_blank" rel="noopener noreferrer">EPA National Aquatic Resource Surveys Data</a></li><li><a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC5796808/" target="_blank" rel="noopener noreferrer">National model of stream biological condition</a></li><li><a href="https://www.epa.gov/national-aquatic-resource-surveys/streamcat-and-lakecat-updates" target="_blank" rel="noopener noreferrer">EPA StreamCat and LakeCat Updates</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Invasive / Non-native Species Presence</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Species interactions sustain biodiversity, restrain invasives, and build resilience to disturbance.</p>
<p class="metric-ref-def">Extent to which invasive or non-native species are present in the watershed and may displace natives.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Invasive-species pressure (recorded taxa)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>0 recorded</span></div><div class="gfp-seg fair"><b>Fair</b><span>1-2 recorded</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥3 recorded</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>1 taxon</td><td>Transparent STAF transition from no established taxa recorded to recorded pressure.</td></tr><tr><td>3 taxa</td><td>Transparent STAF transition to the upper count tier.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Established NAS taxa count:</b> Established taxa records provide a transparent national indicator of invasive-species pressure.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A zero count means no established taxa were recorded. It does not confirm absence.</li><li>HUC8 fallback results carry lower confidence than HUC12 results.</li><li>Count bands are STAF screening judgments.</li><li>Establishment status often cannot be confirmed from occurrence records, and uneven sampling effort inflates richness where effort is high (Mangiante et al. 2019).</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: Moderate &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://nas.er.usgs.gov/" target="_blank" rel="noopener noreferrer">USGS Nonindigenous Aquatic Species database</a></li><li><a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC6707539/" target="_blank" rel="noopener noreferrer">Mangiante et al. 2019, Trends in nonindigenous aquatic species richness in the United States</a></li></ul>
</div>
</div></details>

<details class="metric-ref">
<summary>Fish Passage &amp; Barrier Effects (longitudinal connectivity)</summary>
<div class="metric-ref-body">
<p class="metric-ref-fn"><span>Stream function</span> Continuous pathways for free movement of water, sediment, and organisms supporting recolonization, maintaining species diversity, and enabling recovery.</p>
<p class="metric-ref-def">Mapped dams within a mile of the reach, as a sign of possible barriers to fish movement. Proximity alone does not establish whether a barrier is passable.</p>
<div class="metric-ref-sec"><div class="metric-ref-label">Good / Fair / Poor</div>
<div class="gfp-charts"><figure class="gfp"><figcaption>Nearby dam-proximity proxy (mapped dams)</figcaption><div class="gfp-strip"><div class="gfp-seg good"><b>Good</b><span>0 mapped dams</span></div><div class="gfp-seg fair"><b>Fair</b><span>1 mapped dam</span></div><div class="gfp-seg poor"><b>Poor</b><span>≥2 mapped dams</span></div></div></figure></div>
</div>
<div class="metric-ref-sec"><div class="metric-ref-label">Breakpoints</div><table class="metric-ref-table"><thead><tr><th>Boundary</th><th>What it marks</th></tr></thead><tbody><tr><td>1 dam</td><td>A single mapped dam within one mile changes the automated result from Good to Fair.</td></tr><tr><td>2 dams</td><td>Two or more mapped dams within one mile rate Poor.</td></tr></tbody></table></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Input rationale</div><ul class="metric-ref-list"><li><b>Mapped NID dams within one mile:</b> Mapped dam proximity identifies potential longitudinal-connectivity pressure.</li></ul></div>
<div class="metric-ref-sec"><div class="metric-ref-label">Known limitations</div><ul class="metric-ref-list"><li>A mapped dam count is a proximity screen and cannot confirm passability or the severity of a barrier.</li><li>NID carries about 91,000 regulated dams while the National Aquatic Barrier Inventory maps over 500,000 barriers of all sizes, so zero mapped dams is frequently a false negative for passage.</li><li>Query failure is unscored, not Good.</li></ul></div>
<div class="metric-ref-sec">
<div class="metric-ref-label">Basis and sources</div>
<p class="metric-ref-meta">Basis: dataset reference &middot; Data confidence: Moderate &middot; Provisional screening thresholds</p>
<ul class="metric-ref-sources"><li><a href="https://nid.sec.usace.army.mil/" target="_blank" rel="noopener noreferrer">USACE National Inventory of Dams</a></li></ul>
</div>
</div></details>
<!-- END GENERATED METRIC REFERENCE -->

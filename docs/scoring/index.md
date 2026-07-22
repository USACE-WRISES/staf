---
title: Scoring and Condition
nav_order: 6
description: "How metrics roll up to functions, outcomes, and overall condition."
---
{% include staf_page_chrome.html %}

<details class="tier-how-it-works">
  <summary>How scoring works</summary>
  <ol>
    <li>Metrics are scored and rolled up into function scores — the method varies by tier (see table below).</li>
    <li>Function scores (0–15) roll up to three outcome sub-indices:
      <ul>
        <li><strong>Physical</strong> — hydrology, hydraulics, geomorphic functions</li>
        <li><strong>Chemical</strong> — thermal regime, nutrients, water quality</li>
        <li><strong>Biological</strong> — habitat, populations, community dynamics</li>
      </ul>
    </li>
    <li>Each sub-index is normalized to 0–1 (÷ 15, rounded to 2 decimals).</li>
    <li>The <strong>Ecosystem Condition Index</strong> (0–1) is the average of the three sub-indices.</li>
  </ol>
  <table class="tier-comparison-table scoring-tier-table">
    <thead>
      <tr>
        <th></th>
        <th>Screening</th>
        <th>Rapid</th>
        <th>Detailed</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <th>Metric Scores</th>
        <td>Qualitative (e.g., Good / Fair / Poor)</td>
        <td>Semi-quantitative (Likert scale)</td>
        <td>Quantitative (measured values)</td>
      </tr>
      <tr>
        <th>Function scores</th>
        <td>User-assigned using recommended ranges</td>
        <td>User-assigned using lines of evidence</td>
        <td>Computed via reference curves</td>
      </tr>
    </tbody>
  </table>
</details>

<details class="tier-how-it-works">
  <summary>Two-indicator scoring (Catchment hydrology)</summary>
  <p>Most functions are assessed with a single indicator per tier. <strong>Catchment hydrology</strong> uses two, because either urban or agricultural land cover can drive the hydrologic alteration: <strong>impervious cover</strong> (Good below 10%, Fair 10 to 25%, Poor above 25%; the Center for Watershed Protection Impervious Cover Model) and <strong>agricultural land cover</strong> (Good below 25%, Fair 25 to 50%, Poor above 50%), for watersheds where farming (tile drainage, ditching, loss of perennial vegetation, soil compaction, diversions), not pavement, is the dominant pressure.</p>
  <p>Both indicators are computed from the delineated watershed, and the function is scored automatically on whichever is <strong>more limiting</strong> (the worse rating), because catchment hydrology is impaired if either pressure is high. The report names the governing driver and shows both values. The agricultural thresholds are provisional and meant to be calibrated regionally with subject-matter-expert review.</p>
</details>

<details class="tier-how-it-works">
  <summary>Natural riparian vegetation (Detrital processing)</summary>
  <p>The screening proxy for <strong>Detrital processing (CPOM retention)</strong> is the percent of the 100 m riparian buffer in <strong>natural vegetation</strong> (forest, shrub, grassland, or wetland), not forest alone. In grassland and arid or xeric ecoregions the natural riparian buffer is non-forest, so counting only forest would wrongly score those streams low. The site's EPA Level III ecoregion is reported with the basin characteristics; verify the buffer on the aerial basemap, especially where the natural cover is non-forest.</p>
</details>

<details class="tier-how-it-works">
  <summary>Understanding outcomes and ecosystem condition</summary>

  <p><strong>Outcomes</strong> are observable or quantifiable results that are linked to how one or more stream functions operate.</p>

  <div class="outcome-cards">
    <article class="outcome-card physical">
      <p class="outcome-card-title">Physical Outcomes</p>
      <p class="outcome-card-def">Physical outcomes are measurable results of hydrologic, hydraulic, geomorphic, and habitat-forming processes. They describe the physical structure of the stream and how water and sediment move through the system.</p>
      <p class="outcome-card-examples"><strong>Examples:</strong> floodplain inundation frequency, channel stability, sediment transport balance, habitat unit distribution, substrate composition, and large wood abundance.</p>
    </article>

    <article class="outcome-card chemical">
      <p class="outcome-card-title">Chemical Outcomes</p>
      <p class="outcome-card-def">Chemical outcomes are measurable results of water chemistry and biogeochemical processes. They describe how chemical conditions support or limit aquatic life and ecosystem processes.</p>
      <p class="outcome-card-examples"><strong>Examples:</strong> dissolved oxygen, nutrient concentrations, temperature regime, pH, contaminant levels, and organic matter decomposition rates.</p>
    </article>

    <article class="outcome-card biological">
      <p class="outcome-card-title">Biological Outcomes</p>
      <p class="outcome-card-def">Biological outcomes are measurable characteristics of aquatic and riparian communities. They describe the presence, abundance, diversity, and functional roles of organisms in the system.</p>
      <p class="outcome-card-examples"><strong>Examples:</strong> fish assemblage composition, macroinvertebrate diversity, species richness, presence of sensitive taxa, and functional feeding group distribution.</p>
    </article>
  </div>

  <article class="ecosystem-condition-card">
    <p class="outcome-card-title">Ecosystem Condition</p>
    <p class="outcome-card-def">The overall state of a stream system, based on the combined performance of physical, chemical, and biological outcomes relative to expected or reference conditions. It indicates how well the system sustains ecological function and resilience over time.</p>
  </article>
</details>

<div class="widget-collapse" data-tier="scoring">
  <div class="widget-collapse-header">
    <div class="widget-collapse-title">Scoring and Condition Sandbox</div>
  </div>
  <div class="widget-collapse-body">
    {% include scoring_sandbox_widget.html %}
  </div>
</div>

## References
- Stepchinski, L. M., McKay, S. K., & Menichino, G. T. (In review). Synthesis and inventory of stream functions. Manuscript submitted for publication.

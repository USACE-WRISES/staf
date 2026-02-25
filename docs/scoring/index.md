---
title: Scoring and Condition
nav_order: 8
description: "How metrics roll up to functions, outcomes, and overall condition."
---
{% include staf_page_chrome.html %}

<details class="tier-how-it-works" open>
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
        <th>Metrics</th>
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

<div class="widget-collapse" data-tier="scoring">
  <div class="widget-collapse-header">
    <div class="widget-collapse-title">Scoring and Condition Sandbox</div>
  </div>
  <div class="widget-collapse-body">
    {% include scoring_sandbox_widget.html %}
  </div>
</div>

## Downloads
- Excel Calculator

## References
- Stepchinski, L. M., McKay, S. K., & Menichino, G. T. (In review). Synthesis and inventory of stream functions. Manuscript submitted for publication.

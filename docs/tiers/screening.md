---
title: Screening Assessment
nav_order: 4
description: "Desktop-first screening for early planning and prioritization."
---
{% include staf_page_chrome.html %}

<details class="pathway-chooser" data-storage-key="staf-pathway-screening-v2" data-tier="screening" open>
  <summary>Choose your workflow</summary>
  <div class="pathway-chooser-cards">
    {% assign easi = site.data.apps | where: "id", "easi" | first %}
    <div class="pathway-card pathway-card-launch" data-action="launch-app">
      <div class="pathway-card-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2f4b7c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
      </div>
      <div class="pathway-card-title">Launch the Screening App</div>
      <ul class="pathway-card-details">
        <li>{{ easi.full_name }} ({{ easi.name }}) — {{ easi.description }}</li>
        <li><strong>Applicability:</strong> Nationwide, wadeable streams</li>
      </ul>
      <a class="pathway-card-launch-action btn btn-primary" href="{{ easi.url }}" target="_blank" rel="noopener">Launch {{ easi.name }} &#8599;</a>
    </div>
    <div class="pathway-card" data-action="build-custom">
      <div class="pathway-card-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2f4b7c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </div>
      <div class="pathway-card-title">Build Your Own Assessment</div>
      <ul class="pathway-card-details">
        <li>Start Blank – Use the Metric Library to select metrics based on project needs and available data</li>
        <li>Full control over which metrics and functions to include</li>
        <li>Score metrics and see automatic roll-ups</li>
        <li>Use a single proxy metric per function. User scores functions based on metric values</li>
      </ul>
      <button type="button" class="pathway-card-action btn btn-primary">Get Started</button>
    </div>
  </div>
</details>

<details class="tier-how-it-works">
  <summary>How to perform the assessment</summary>
  <ol>
    <li>Delineate reaches and catchments.</li>
    <li>Compute or complete screening metrics and convert to metric values (e.g., Good, Fair, Poor) based on scoring criteria for metric.</li>
    <li>Assign a Function Score based on the Metric Values and suggested range.</li>
  </ol>
</details>

<div class="widget-collapse is-collapsed" data-tier="screening">
  <div class="widget-collapse-header">
    <div class="widget-collapse-title">Screening Assessment</div>
    <button class="widget-collapse-download" type="button"
            aria-label="Download assessment as Excel"
            title="Download assessment as Excel (.xlsx)">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M12 4v10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
        <path d="M8 10l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M5 20h14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
      </svg>
    </button>
  </div>
  <div class="widget-collapse-body">
    {% include screening_assessment_widget.html %}
  </div>
</div>

## Downloads / resources
- <a href="#" class="metric-library-download-link" data-tier-metric-library-download-trigger="screening">Metric Toolbox</a>
- <a href="#" class="assessment-widget-download-link" data-tier-download-trigger="screening">Excel Calculator</a>

## References
- Stepchinski, L. M., McKay, S. K., Harris, A. E., & Menichino, G. T. (2025). A Review of Stream Assessment Methods in the United States. JAWRA Journal of the American Water Resources Association, 61(6), e70056.
- Stepchinski, L. M., Menichino, G. T., & McKay, S. K. (2024, December). A Tiered Approach for Assessing Stream Ecosystem Condition. In AGU Fall Meeting Abstracts (Vol. 2024, No. 983, pp. H11X-0983).

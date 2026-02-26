---
title: Screening Assessment
nav_order: 4
description: "Desktop-first screening for early planning and prioritization."
---
{% include staf_page_chrome.html %}

<details class="pathway-chooser" data-storage-key="staf-pathway-screening-v2" data-tier="screening" open>
  <summary>Choose your workflow</summary>
  <div class="pathway-chooser-cards">
    <div class="pathway-card" data-action="use-predefined">
      <div class="pathway-card-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2f4b7c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      </div>
      <div class="pathway-card-title">Use Pre-defined Assessment</div>
      <select class="pathway-card-select"
        ><option value="easi"
          data-notes="Compiles commonly used screening metrics across the United States.&#10;Includes broadly applicable and comprehensive screening metrics."
          data-applicability="Nationwide, wadeable streams"
        >Ecosystem Assessment Screening Index (EASI)</option
      ></select>
      <ul class="pathway-card-details"></ul>
      <button type="button" class="pathway-card-action btn btn-primary">Get Started</button>
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

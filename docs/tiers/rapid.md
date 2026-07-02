---
title: Rapid Assessment
nav_order: 5
description: "Field-based rapid assessment for comparing sites and alternatives."
---
{% include staf_page_chrome.html %}

<details class="pathway-chooser" data-storage-key="staf-pathway-rapid-v2" data-tier="rapid" open>
  <summary>Choose your workflow</summary>
  <div class="pathway-chooser-cards">
    {% assign sfari = site.data.apps | where: "id", "sfari" | first %}
    <div class="pathway-card pathway-card-launch" data-action="launch-app">
      <div class="pathway-card-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2f4b7c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
      </div>
      <div class="pathway-card-title">Launch the Rapid App</div>
      <ul class="pathway-card-details">
        <li>{{ sfari.full_name }} ({{ sfari.name }}) — {{ sfari.description }}</li>
        <li><strong>Applicability:</strong> Nationwide, wadeable streams</li>
      </ul>
      <a class="pathway-card-launch-action btn btn-primary" href="{{ sfari.url }}" target="_blank" rel="noopener">Launch {{ sfari.name }} &#8599;</a>
    </div>
    <div class="pathway-card is-disabled" data-action="build-custom">
      <div class="pathway-card-icon">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#2f4b7c" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </div>
      <div class="pathway-card-title">Build Your Own Assessment</div>
      <div class="pathway-card-unavailable">
        <span class="pathway-card-unavailable-label">Not Available</span>
        <details class="pathway-card-unavailable-details">
          <summary>Why?</summary>
          <p>At the rapid tier, users should apply SFARI, where function scores are user-assigned based on agreement with Function Statements. SFARI provides a flexible, comprehensive, and broadly applicable way to rapidly assess stream condition, so building a custom rapid assessment is not necessary.</p>
        </details>
      </div>
    </div>
  </div>
</details>

<details class="tier-how-it-works">
  <summary>How to perform the assessment</summary>
  <ol>
    <li>User will apply the Stream Functions Assessment and Rapid Index (SFARI) assessment.</li>
    <li>Collect desktop metrics to support rapid assessment.</li>
    <li>Perform field visit and score metrics on the likert scale based on scoring criteria and agreement with Function statements (see expander glyphs).</li>
    <li>User scores functions based on lines of evidence (metric scores) and the Function Statement.</li>
  </ol>
</details>

<div class="widget-collapse is-collapsed" data-tier="rapid">
  <div class="widget-collapse-header">
    <div class="widget-collapse-title">Rapid Assessment</div>
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
    {% include rapid_assessment_widget.html %}
  </div>
</div>

## Downloads
- SFARI Field Form
- <a href="#" class="assessment-widget-download-link" data-tier-download-trigger="rapid">SFARI Excel Calculator</a>

## References
- David, G. C., Stepchinski, L. M., Wiest, S. R., & Menichino, G. T. (In review). Stream Functions Assessment and Rapid Index (SFARI): A nationally applicable, rapid, function-based stream assessment. ERDC/EMRRP Technical Report. Vicksburg, MS: U.S. Army Engineer Research and Development Center.

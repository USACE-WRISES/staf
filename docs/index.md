---
title: Stream Tiered Assessment Framework
nav_order: 1
description: "Landing page for the Stream Tiered Assessment Framework."
---

<div class="landing-page">
  <div class="landing-content">
    <section class="landing-hero">
      <div class="hero-text">
        <p class="hero-kicker reveal">Stream Tiered Assessment Framework</p>
        <h1 class="hero-title reveal">Assess stream function at the right level of effort.</h1>
        <p class="hero-subtitle reveal">
          A functions-based framework to evaluate stream condition across Screening, Rapid, and Detailed tiers.
          Use it to compare sites, guide restoration planning, and report outcomes with consistent scoring.
        </p>
        <div class="hero-actions button-row reveal">
          <a class="btn btn-primary" href="{{ site.baseurl }}/tier-selector/">Start the tier selector</a>
          <a class="btn" href="{{ site.baseurl }}/quick-overview/">Quick Overview</a>
        </div>

      </div>

      <div class="hero-media reveal">
        <div class="hero-carousel image-frame" data-interval="10000">
          <div class="hero-carousel-track">
            {% assign carousel_count = 0 %}
            {% assign sorted_files = site.static_files | sort: "name" %}
            {% assign normalized_source = site.source | replace: '\\', '/' %}
            {% for file in sorted_files %}
              {% assign normalized_path = file.path | replace: '\\', '/' %}
              {% if normalized_path contains '/assets/images/image_viewer/' %}
                {% assign ext = file.extname | downcase %}
                {% if ext == '.jpg' or ext == '.jpeg' or ext == '.png' or ext == '.webp' %}
                  {% if file.name != 'large-image.jpg' and file.name != 'small-image.jpg' %}
                    {% assign carousel_count = carousel_count | plus: 1 %}
                    {% assign relative_image = normalized_path | replace: normalized_source, '' %}
                    <img class="carousel-image{% if carousel_count == 1 %} is-active{% endif %}" src="{{ relative_image | relative_url }}" alt="Stream image {{ carousel_count }}">
                  {% endif %}
                {% endif %}
              {% endif %}
            {% endfor %}
            {% if carousel_count == 0 %}
              <div class="carousel-empty">No images found in assets/images/image_viewer.</div>
            {% endif %}
          </div>
          <button class="carousel-nav prev" type="button" aria-label="Previous image">&#10094;</button>
          <button class="carousel-nav next" type="button" aria-label="Next image">&#10095;</button>
        </div>
      </div>

      <div class="hero-links reveal">
        <a class="link-chip" href="{{ site.baseurl }}/functions/">Stream Functions</a>
        <a class="link-chip" href="{{ site.baseurl }}/scoring/">Scoring and Condition</a>
      </div>

      <div class="card-strip">
        <a class="card reveal" href="{{ site.baseurl }}/tiers/screening/">
          <div class="card-tag">Tier 1</div>
          <h3>Screening</h3>
          <p>Desktop snapshot for early planning and prioritization.</p>
        </a>
        <a class="card reveal" href="{{ site.baseurl }}/tiers/rapid/">
          <div class="card-tag">Tier 2</div>
          <h3>Rapid</h3>
          <p>Field-based assessment for alternatives and design support.</p>
        </a>
        <a class="card reveal" href="{{ site.baseurl }}/tiers/detailed/">
          <div class="card-tag">Tier 3</div>
          <h3>Detailed</h3>
          <p>Intensive methods for compliance, crediting, and monitoring.</p>
        </a>
      </div>
    </section>

  <section class="landing-help" hidden>
    <h2>Not sure where to start?</h2>
    <p>Use the Tier Selector to match your decision context to the right level of effort.</p>
    {% include tier_selector_widget.html %}
  </section>
  </div>
</div>

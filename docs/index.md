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
          <a class="btn" href="{{ site.baseurl }}/tiered-approach/">Read the full guide</a>
        </div>

      </div>

      <div class="hero-media reveal">
        <div class="hero-carousel image-frame" data-interval="10000">
          <div class="hero-carousel-track">
            {% assign image_files = site.static_files | where_exp: "file", "file.relative_path contains '/assets/images/'" %}
            {% assign carousel_images = image_files | where_exp: "file", "file.extname == '.jpg' or file.extname == '.jpeg' or file.extname == '.png' or file.extname == '.webp' or file.extname == '.JPG' or file.extname == '.JPEG' or file.extname == '.PNG' or file.extname == '.WEBP'" | where_exp: "file", "file.name != 'large-image.jpg' and file.name != 'small-image.jpg'" | sort: "name" %}
            {% if carousel_images.size > 0 %}
              {% for image in carousel_images %}
                <img class="carousel-image{% if forloop.first %} is-active{% endif %}" src="{{ image.path | relative_url }}" alt="Stream image {{ forloop.index }}">
              {% endfor %}
            {% else %}
              <div class="carousel-empty">No images found in assets/images.</div>
            {% endif %}
          </div>
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

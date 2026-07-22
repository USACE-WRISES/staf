"""The worksheet method-panel markup.

Three stacked ``<details>`` on each metric card, all collapsed by default: the reference curve +
what-if sliders, the scoring criteria, and the catalog definition/rationale/limitations. Every
criteria row carries the exact automated breakpoint generated from the catalog bands that
produced the rating, so the panel cannot describe a different quantity than the one that was
scored; the docs section explains those same breakpoints without restating anything the method
panel already shows.
See ``easi/methods.py`` / ``easi/method_plot.py``.
"""
from __future__ import annotations

import html

import app
from easi import config, methods, screening_methods as sm
from easi.metrics.geomorphology import SEDIMENT_ID
from easi.metrics.hydraulics import LOW_FLOW_ID
from easi.metrics.hydrology import IMPERVIOUS_ID, REACH_INFLOW_ID


def _row(mid, values, rating, **extra):
    """A report row for ``mid`` carrying the trace the evaluator actually produced."""
    trace = sm.evaluate(mid, values).trace
    method = sm.method_for_trace(mid, trace)
    row = {"metricId": mid, "generatedRating": rating, "rating": rating,
           "scoring": trace, "landCover": None, "ripVeg": None,
           "methodCriteria": sm.criteria_for(method, trace.get("context") or {}),
           "criteriaBands": {}}
    row.update(extra)
    return row, trace


def test_expander_is_three_details_all_collapsed():
    _, trace = _row(SEDIMENT_ID,
                    {"agriculture": 20.0, "kFactor": 0.3, "roadDensity": 1.5}, "Fair")
    m = str(app._method_expander(SEDIMENT_ID, trace))
    assert m.count("<details") == 3
    assert "Scoring method" in m and "Scoring criteria" in m
    assert "Definition, rationale, and limitations" in m
    # the card opens compact: no section carries `open`
    assert "open=" not in m
    assert '<details class="easi-method" data-mid=' in m
    assert '<details class="easi-method easi-method-critsec" data-mid=' in m
    assert '<details class="easi-method easi-method-docsec" data-mid=' in m
    # the what-if sliders + reset live in the (collapsed) method section
    assert "easi-method-reset" in m and "js-range-slider" in m


def test_criteria_rows_carry_the_catalog_breakpoint_and_colour_swatch():
    row, trace = _row(SEDIMENT_ID,
                      {"agriculture": 20.0, "kFactor": 0.3, "roadDensity": 1.5}, "Fair")
    m = str(app._method_criteria_ui(row, methods.resolve(SEDIMENT_ID,
                                                         trace["methodKey"])))
    for band in ("good", "fair", "poor"):
        assert f"easi-tip-dot {band}" in m                     # clear colour coding kept
    assert m.count("easi-method-crit-range") == 3              # one breakpoint per rating
    # the chip states the automated boundary, matching the catalog exactly
    assert "&lt;0.33" in m or "<0.33" in m
    assert "easi-method-crit-title" not in m                   # section owns the title


def test_single_indicator_criteria_show_the_field_profile_beside_the_breakpoint():
    """Where the catalog defines one, field wording accompanies — never replaces — the
    automated breakpoint."""
    row, trace = _row(LOW_FLOW_ID, {"wettedPct": 90.0}, "Good")
    m = str(app._method_criteria_ui(row, methods.resolve(LOW_FLOW_ID,
                                                         trace["methodKey"])))
    assert m.count("easi-method-crit-range") == 3
    assert "wetted" in m                                       # field profile text present


def test_multi_indicator_criteria_split_into_labelled_sub_blocks():
    row, trace = _row(IMPERVIOUS_ID, {"impervious": 2.0, "agriculture": 61.0}, "Poor")
    m = str(app._method_criteria_ui(row, methods.resolve(IMPERVIOUS_ID,
                                                         trace["methodKey"])))
    assert m.count("easi-method-crit-sub") == 2                # one per indicator
    assert "impervious" in m.lower() and "agricultur" in m.lower()
    assert m.count("easi-method-crit-range") == 6              # 2 indicators x 3 ratings
    assert "easi-tip-dot good" in m and "easi-tip-dot poor" in m


def test_criteria_text_describes_what_the_automation_measured():
    """Regression: the panel used to claim road density counted stormwater outfalls."""
    row, trace = _row(REACH_INFLOW_ID, {"roadDensity": 1.2}, "Fair")
    m = str(app._method_criteria_ui(row, methods.resolve(REACH_INFLOW_ID,
                                                         trace["methodKey"])))
    assert "km/km" in m                     # the quantity actually binned
    assert "outfall" not in m.lower()       # not the old field-observation wording


def test_method_body_has_no_provenance_or_static_docs():
    """The method panel shows only how the number is computed: inputs, equation, curve.
    The provenance block was removed, and the static basis, limitations and sources live
    in the docs section, so no metric card states them twice."""
    row, trace = _row(SEDIMENT_ID,
                      {"agriculture": 20.0, "kFactor": 0.3, "roadDensity": 1.5}, "Fair")
    method = methods.resolve(SEDIMENT_ID, trace["methodKey"])
    values = app._trace_values(trace)
    m = str(app._method_body_ui(SEDIMENT_ID, method, row, trace, values, values))
    assert "easi-method-inputs" in m              # the inputs-used table
    assert "Result provenance" not in m
    assert "Confidence" not in m
    for moved in ("Known limitations", "Sources", "ecological cliff", "Basis and provenance"):
        assert moved not in m, f"{moved!r} should live only in the docs section"


def test_docs_section_renders_every_subheading_for_all_twenty_metrics():
    """The reference section is complete for every metric, including the categorical ones with
    no numeric breakpoints — an empty catalog list must degrade to prose, not to an empty <ul>."""
    for mid in config.METRIC_REGISTRY:
        m = str(app._method_docs_ui({"metricId": mid}, {}))
        for heading in ("Definition", "Input rationale", "Breakpoints", "Basis",
                        "Known limitations", "Sources"):
            assert f"<h6>{heading}</h6>" in m, f"{mid} is missing {heading}"
        assert "easi-method-docs" in m
        assert "<ul></ul>" not in m and "<ol></ol>" not in m


def test_every_metric_shows_its_curated_definition():
    for mid in config.METRIC_REGISTRY:
        definition = config.METRIC_DEFINITIONS.get(mid) or ""
        assert definition, f"{mid} has no definition"
        m = str(app._method_docs_ui({"metricId": mid}, {}))
        assert html.escape(definition, quote=False) in m


def test_active_variant_documents_itself_not_the_parent():
    """A fallback tier must describe its own limits; claiming the parent's would misstate what
    the number is. The hierarchy still lists every tier, with the active one marked."""
    mid = "population-support-biological-integrity-ibi-community-condition"
    m = str(app._method_docs_ui({"metricId": mid},
                                {"methodKey": "streamcat-integrity-products"}))
    assert "landscape integrity" in m                      # the variant's own limitation
    assert "connected nearby NRSA visit" not in m          # the parent's, suppressed
    assert m.count("<li>") >= 3                            # all three tiers still listed
    assert "in use" in m                                   # the active tier is marked


def test_sources_render_as_external_links():
    m = str(app._method_docs_ui({"metricId": SEDIMENT_ID}, {}))
    cites = methods.citations_for(methods.catalog_entry(SEDIMENT_ID, None))
    assert cites and m.count('target="_blank"') == len(cites)
    assert 'rel="noopener noreferrer"' in m and 'href="https://' in m

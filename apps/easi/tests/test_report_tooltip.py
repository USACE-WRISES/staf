"""The metric card ⓘ tooltip builds a rich card: definition + calculation + scoring.

``_metric_card_tip`` is the pure helper used by both the worksheet card and the read-only
report row; it wraps ``_metric_tip_html`` (this was ``_metric_table`` before the worksheet rework)."""
from __future__ import annotations

import html
import re

import app
from easi import config


def _row(mid, **over):
    m = config.metrics_by_id()[mid]
    row = {"metricId": mid, "discipline": m["discipline"], "functionName": m["functionName"],
           "name": m["name"], "valueText": "x", "rating": "Good", "generatedRating": "Good",
           "index": 0.85, "functionScore": 13, "confidence": "H",
           "source": "EPA StreamCat pctimp2019 (watershed)", "note": "", "status": "computed"}
    row.update(over)
    return row


def _tip_html(row):
    """Return the ⓘ card markup as the browser would see it (attribute un-escaped once)."""
    h = str(app._metric_card_tip(row))
    m = re.search(r'data-tip-html="(.*?)"', h, re.S)
    assert m, "metric ⓘ is missing a data-tip-html card"
    return html.unescape(m.group(1))


def test_tooltip_has_definition_calculation_scoring():
    mid = "catchment-hydrology-impervious-surface-cover"
    inner = _tip_html(_row(mid))
    assert '<span class="easi-tip-lbl">Definition</span>' in inner
    assert config.METRIC_DEFINITIONS[mid][:30] in inner            # the curated definition
    assert '<span class="easi-tip-lbl">Calculation</span>' in inner
    assert "EPA StreamCat pctimp2019" in inner                     # EASI's actual method (source)
    assert '<span class="easi-tip-lbl">Scoring</span>' in inner
    assert "default: Good" in inner
    for band in ("Good", "Fair", "Poor"):
        assert f"<b>{band}</b>" in inner                            # all three criteria rows
        assert f"easi-tip-dot {band.lower()}" in inner             # band-colored swatch


def test_tooltip_escapes_criteria_threshold():
    # "<10%" must be HTML-escaped in the card so it renders as literal text, not a tag
    inner = _tip_html(_row("catchment-hydrology-impervious-surface-cover"))
    assert "&lt;10%" in inner


def test_tooltip_shows_calculation_note_subline():
    row = _row("streamflow-regime-flow-alteration-regulation-water-use",
               source="EPA StreamCat dam storage (ungaged proxy)",
               note="regulation proxy; gaged NWIS comparison refines")
    inner = _tip_html(row)
    assert 'class="easi-tip-sub">regulation proxy' in inner


def test_every_metric_has_a_definition():
    # parity is also enforced by config.validate_registry(); assert directly here too
    assert set(config.METRIC_REGISTRY) <= set(config.METRIC_DEFINITIONS)
    assert all(config.METRIC_DEFINITIONS.values())                 # no blank definitions


def test_tooltip_land_cover_shows_both_indicators():
    # the land-cover metric replaces the single Scoring block with a two-indicator block that
    # shows impervious + agricultural cover with their thresholds and marks the governing one
    mid = "catchment-hydrology-impervious-surface-cover"
    inner = _tip_html(_row(
        mid, valueText="61.0% agricultural land (watershed)", rating="Poor",
        generatedRating="Poor", source="EPA StreamCat crop+hay (watershed)",
        criteriaBands={"Good": "<25%", "Fair": "25%-50%", "Poor": ">50%"},
        landCover={"governing": "agriculture",
                   "impervious": {"pct": 2.0, "rating": "Good"},
                   "agriculture": {"pct": 61.0, "rating": "Poor"}}))
    assert '<span class="easi-tip-lbl">Land-cover indicators</span>' in inner
    assert "Impervious 2.0%" in inner and "Agricultural 61.0%" in inner
    assert "&lt;25%" in inner and "&gt;50%" in inner        # agriculture thresholds shown
    assert "&lt;10%" in inner                                # impervious thresholds shown too
    assert "[governs]" in inner                              # governing driver marked
    assert 'easi-tip-dot good' in inner                      # impervious (Good) band chip
    assert 'easi-tip-dot poor' in inner                      # governing agriculture (Poor) chip
    assert '<span class="easi-tip-lbl">Scoring</span>' not in inner   # generic block replaced


def test_worksheet_tooltip_omits_scoring_report_keeps_it():
    # the read-only report ⓘ keeps the scoring criteria; the worksheet ⓘ drops them (they move
    # to the card's "Scoring method" panel), keeping definition / source / calculation.
    mid = "catchment-hydrology-impervious-surface-cover"
    report = _tip_html(_row(mid))                       # default include_criteria=True
    ws_raw = str(app._metric_card_tip(_row(mid), include_criteria=False))
    m = re.search(r'data-tip-html="(.*?)"', ws_raw, re.S)
    ws = html.unescape(m.group(1))
    assert '<span class="easi-tip-lbl">Scoring</span>' in report
    assert '<span class="easi-tip-lbl">Scoring</span>' not in ws
    assert '<span class="easi-tip-lbl">Definition</span>' in ws
    assert '<span class="easi-tip-lbl">Calculation</span>' in ws


def test_tooltip_riparian_vegetation_block():
    # the detrital metric adds a natural-vegetation composition block (forest/shrub/grassland/wetland)
    mid = "carbon-processing-detrital-processing-cpom-retention-shredders"
    inner = _tip_html(_row(
        mid, valueText="67.0% natural riparian vegetation (100 m buffer)", rating="Good",
        generatedRating="Good", source="EPA StreamCat riparian vegetation (rp100)",
        note="Natural riparian vegetation as a CPOM proxy; verify the buffer on the aerial basemap.",
        ripVeg={"kind": "riparian_veg", "forest": 2.0, "shrub": 10.0, "grassland": 55.0,
                "wetland": 0.0, "total": 67.0}))
    assert '<span class="easi-tip-lbl">Riparian vegetation</span>' in inner
    assert "Forest 2.0%" in inner and "Shrub 10.0%" in inner and "Grassland 55.0%" in inner
    assert "Natural vegetation 67.0%" in inner
    assert "aerial basemap" in inner            # the aerial-verify note renders in the Source sub-line

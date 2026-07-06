"""The report metric table builds a rich ⓘ tooltip: definition + calculation + scoring."""
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
    h = str(app._metric_table([row], {}))
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

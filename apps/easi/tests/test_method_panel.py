"""The worksheet "Scoring method" / "Scoring criteria" panel markup.

Two stacked ``<details>`` on each metric card: the reference curve + what-if sliders (collapsed by
default) and the scoring criteria (expanded by default, with a numeric value-range chip beside each
Good/Fair/Poor rating). The land-cover metric shows both indicators and skips the chip (its config
text is already the numeric range). See ``easi/methods.py``/``easi/method_plot.py``.
"""
from __future__ import annotations

import app
from easi import config, methods
from easi.metrics.geomorphology import SEDIMENT_ID
from easi.metrics.hydrology import IMPERVIOUS_ID


def _criteria(mid):
    return config.metrics_by_id()[mid].get("criteria")


def test_expander_is_two_details_criteria_open_method_collapsed():
    trace = {"model": "combined",
             "inputs": {"agriculture": 20.0, "kffact": 0.3, "road_density": 1.5}, "value": 0.38}
    m = str(app._method_expander(SEDIMENT_ID, trace))
    assert m.count("<details") == 2
    assert "Scoring method" in m and "Scoring criteria" in m
    # criteria section carries `open`; the method section does not
    assert 'open="" class="easi-method easi-method-critsec"' in m
    assert '<details class="easi-method" data-mid=' in m       # method details, unopened
    # the what-if sliders + reset live in the (collapsed) method section
    assert "easi-method-reset" in m and "js-range-slider" in m


def test_criteria_rows_have_range_chip_and_color_swatch():
    row = {"metricId": SEDIMENT_ID, "generatedRating": "Fair",
           "criteriaBands": _criteria(SEDIMENT_ID), "landCover": None}
    m = str(app._method_criteria_ui(row, methods.resolve(SEDIMENT_ID, "combined")))
    for band in ("good", "fair", "poor"):
        assert f"easi-tip-dot {band}" in m                     # clear color coding kept
    assert m.count("easi-method-crit-range") == 3              # a value range per rating
    assert "Supply risk" in m                                  # the metric-value range text
    assert "easi-method-crit-title" not in m                   # inner title removed (section owns it)


def test_land_cover_criteria_has_no_range_chip():
    row = {"metricId": IMPERVIOUS_ID, "generatedRating": "Poor",
           "criteriaBands": {"Good": "<25%", "Fair": "25%-50%", "Poor": ">50%"},
           "landCover": {"governing": "agriculture",
                         "impervious": {"pct": 2.0, "rating": "Good"},
                         "agriculture": {"pct": 61.0, "rating": "Poor"}}}
    m = str(app._method_criteria_ui(row, methods.resolve(IMPERVIOUS_ID, None)))
    assert "Impervious cover" in m and "Agricultural cover" in m   # two indicator sub-blocks
    assert "easi-method-crit-range" not in m                       # config text is already numeric
    assert "easi-tip-dot good" in m and "easi-tip-dot poor" in m

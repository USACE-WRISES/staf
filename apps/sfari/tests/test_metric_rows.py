"""SFARI's function page in the row design EASI, SFARI and DEEP share (owner, 2026-10-04): the
statement the assessor rates under the name, the evidence without the engine or the watershed
basis, the example scoring in the row's Scoring panel instead of the (i), and labeled buttons
(Scoring, Note, Photo) under every metric that never wait for a hover."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("shiny")
app = importlib.import_module("app")

HERE = Path(__file__).resolve().parents[1]
SRC = (HERE / "app.py").read_text(encoding="utf-8")
ENGINE_OK = {"status": "ok", "origin": "engine", "suggested_likert": "Strongly Agree",
             "value_text": "1.0% impervious, 10.1% agricultural land (HR reach watershed)",
             "source": "STAF site engine v0.5.0 (HR reach watershed)", "confidence": "H",
             "note": ("Land-cover indicators: impervious 1.0% (Strongly Agree), agricultural 10.1% "
                      "(Strongly Agree). The more limiting one is suggested. Watershed of the clicked "
                      "NHD reach, HR catchments aggregated, 30.6392 km2, area agreement 1.0. NLCD 2021.")}


def _metric():
    m = next(mm for mm in app.METRICS_BY_FN["catchment-hydrology"]
             if mm["metricId"] == "catchment-hydrology-impervious-surface-area")
    assert m.get("likertCriteria")
    return m


def test_the_value_loses_its_watershed_basis_wherever_it_sits():
    assert app._ev_value_ws("1.0% impervious (HR reach watershed)") == "1.0% impervious"
    assert app._ev_value_ws("0.9 km/km2 (NHDPlus V2 basin, COMID 9327042)") == "0.9 km/km2"
    assert app._ev_value_ws("2 dams (NHDPlus V2 basin)") == "2 dams"
    twice = "ratio 1.2 (HR reach watershed) · capacity 3.4 (HR reach watershed) ok"
    assert app._ev_value_ws(twice) == "ratio 1.2 · capacity 3.4 ok"
    assert app._ev_value_ws("NID dams in the HR reach watershed") == "NID dams in the watershed"


def test_the_worksheet_tip_keeps_the_reason_and_the_data_not_the_engine():
    tip = app._ev_tip_ws(ENGINE_OK)
    assert tip.startswith("Land-cover indicators: impervious 1.0% (Strongly Agree)")
    assert "The more limiting one is suggested." in tip and tip.endswith("NLCD 2021.")
    for gone in ("STAF site engine", "HR catchments", "area agreement", "Source:", "confidence",
                 "HR reach watershed"):
        assert gone not in tip, gone
    fallback = app._ev_tip_ws({"origin": "streamcat", "note": "rddens", "fallback_reason":
                               "STAF site engine failed: no stream."})
    assert fallback == "Read from EPA StreamCat. rddens"
    unavailable = app._ev_tip_ws({"note": "The STAF site engine did not return dams for this "
                                          "watershed. The StreamCat lookup engine did not answer."})
    assert unavailable == ""
    # the report keeps every detail
    assert "STAF site engine" in app._ev_tip(ENGINE_OK)


def test_the_about_tip_defines_and_the_scoring_panel_holds_the_ladder():
    m = _metric()
    about = app._about_tip_html(m)
    assert "Example scoring" not in about and "easi-tip-crit" not in about
    assert m["metricStatement"][:30] in about
    panel = str(app._scoring_panel(m))
    assert ">Example scoring</div>" in panel and "Illustrative only." in panel
    assert panel.count("staf-crit-dot") == len(app._scoring_rungs(m)) >= 3
    assert app._scoring_panel({"name": "x", "likertCriteria": [{"likert": "Agree", "criteria": ""}]}) is None


def test_a_row_has_the_shared_layout_and_always_visible_buttons():
    m = _metric()
    row = str(app._metric_row(m, {}, ENGINE_OK, False))
    for part in ('class="staf-metric"', 'class="staf-metric-head"', 'class="staf-metric-scale"',
                 'class="staf-metric-desc"', 'class="staf-metric-ev"', 'class="staf-metric-acts"',
                 'class="staf-metric-input"', 'data-panel="scoring"', 'data-panel="note"',
                 'data-panel="photo"', "staf-rate sfari-likert-select"):
        assert part in row, part
    acts = row.split('class="staf-metric-acts"', 1)[1].split('class="staf-metric-input"', 1)[0]
    assert [a for a in ("Scoring", "Note", "Photo") if f">{a}</span>" in acts] == ["Scoring", "Note", "Photo"]
    for gone in ("sfari-metric-toggle", "data-toggle=", "HR reach watershed", "STAF site engine",
                 "use SA", "sfari-ev-tag"):
        assert gone not in row, gone
    assert m["fieldStatement"][:40] in row
    # the suggested rating, written out, with a Use button the page script already handles
    assert "Suggested: <b>Strongly Agree</b>" in row
    assert 'data-val="Strongly Agree"' in row and "staf-suggest-use sfari-suggest-chip" in row
    assert ">1.0% impervious, 10.1% agricultural land</b>" in row


def test_notes_and_photos_open_themselves_when_they_hold_something():
    m = _metric()
    rc = {"likert": "Agree", "note": "culvert upstream",
          "photos": [{"id": "p1", "uri": "data:x"}, {"id": "p2", "uri": "data:y"}]}
    row = str(app._metric_row(m, rc, None, False))
    assert 'class="staf-metric show-note show-photo"' in row
    photo_btn = row.split('data-staf-panel="photo"', 1)[1].split("</button>", 1)[0]
    assert ">Photos</span>" in photo_btn and '<span class="staf-act-count">2</span>' in photo_btn
    assert "staf-rate sfari-likert-select set" in row and 'value="Agree" selected="selected"' in row
    # before the pull a desktop metric names its source; a field-only metric says so
    assert ">Desktop</span>" in row
    field = str(app._metric_row({**m, "desktopSource": None}, {}, None, False))
    assert ">Field</span>" in field and "Field observation only." in field


def test_the_score_card_is_the_function_score_with_a_note_button():
    card = str(app._score_card({"functionStatement": "Runoff and infiltration sustain flow."},
                               "catchment-hydrology", {"score": 12, "note": "why"}))
    assert ">Function score</span>" in card and "Score this function" not in card
    assert 'data-staf-host=""' in card and 'data-staf-panel="fnnote"' in card
    assert "show-fnnote" in card and "sfari-fn-note staf-metric-note" in card
    assert card.index("sfari-fn-statement") < card.index("sfari-fscore-row") < card.index("fn_suggest")


def test_the_page_names_its_sections_like_easi_and_deep_and_loads_the_shared_rows():
    assert 'ui.span("Metrics", class_="sfari-sec-title")' in SRC
    assert '"Evidence", class_="sfari-sec-title"' not in SRC
    assert 'href="staf/metric-rows.css?v=1"' in SRC and 'src="staf/metric-rows.js?v=1"' in SRC
    assert SRC.index("staf/metric-rows.js") < SRC.index("field-review.js?v=8")
    js = (HERE / "www" / "field-review.js").read_text(encoding="utf-8")
    assert "sfari-metric-toggle" not in js and "STAFMetricRows.sync" in js
    css = (HERE / "www" / "styles.css").read_text(encoding="utf-8")
    assert ".sfari-metric-toggle" not in css and "(hover: hover)" not in css

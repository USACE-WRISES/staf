"""EASI's metric card in the row design EASI, SFARI and DEEP share (owner, 2026-10-04): the name
with an (i) on how to measure it, the description, the evidence, then labeled Scoring and Note
buttons; the rating in the right column; the Scoring panel (criteria, then the method) and the
note open under the row. No more always-open note box or two disclosure lines."""
from __future__ import annotations

from pathlib import Path

import app
from easi import config

HERE = Path(app.__file__).parent
SRC = (HERE / "app.py").read_text(encoding="utf-8")
PANEL = SRC.split("def fn_panel():", 1)[1].split("def _cur_row(", 1)[0]
LIVE = SRC.split("def fn_metric_live():", 1)[1].split("def _cur_method", 1)[0]


def test_the_card_is_one_shared_row_with_its_buttons():
    for part in ('ui.span("Metric", class_="sfari-sec-title")', 'class_="sfari-ev-card easi-metric-card"',
                 'class_="staf-metric-head"', 'class_="staf-metric-desc"', 'class_="staf-metric-main"',
                 'staf_web.metric_action("scoring", "Scoring"', 'staf_web.metric_action("note", "Note"',
                 'ui.output_ui("fn_metric_rate", class_="staf-metric-input")',
                 '{"data-panel": "note"}, class_="staf-metric-panel"',
                 'class_="staf-metric" + (" show-note" if has_note else "")'):
        assert part in PANEL, part
    for gone in ("Score this metric", "_method_expander", "Add a note for this metric",
                 "sfari-metric easi-metric-card"):
        assert gone not in SRC, gone
    scoring = SRC.split("def _scoring_panel(", 1)[1].split("\ndef ", 1)[0]
    assert "tags.details" not in PANEL and "tags.details" not in scoring
    # the panels come after the row's two columns; the observed entries and the cross-section
    # editor span the row under them
    assert PANEL.index('"fn_metric_rate"') < PANEL.index("scoring_panel,") \
        < PANEL.index('{"data-panel": "note"}') < PANEL.index("easi-card-wide")


def test_the_evidence_line_and_the_rating_are_separate_slots():
    assert 'class_="staf-metric-ev"' in LIVE and "_rate_select(" not in LIVE.split("def fn_metric_rate")[0]
    assert "return _rate_select(mid, row)" in LIVE.split("def fn_metric_rate", 1)[1]
    # the restore suggestion reads like SFARI's, and keeps the page script's hook
    assert 'ui.span("Desktop rating: ", ui.tags.b(gen)' in LIVE and '"data-suggest": mid' in LIVE
    assert 'class_="staf-suggest-use"' in LIVE and "sfari-suggest-chip" not in SRC


def test_the_rating_select_takes_the_shared_look():
    sel = str(app._rate_select("m", {"rating": "Good"}))
    assert 'class="staf-rate easi-rate-sel set"' in sel


def test_the_about_tip_says_how_to_measure():
    meta = {"name": "Watershed Land-Cover Pressure",
            "howToMeasure": "Delineate the watershed and compute percent impervious (<10% is Good)."}
    tip = app._metric_about_tip(meta)
    assert '<div class="easi-tip-title">Watershed Land-Cover Pressure</div>' in tip
    assert '<span class="easi-tip-lbl">How to measure</span>' in tip and "&lt;10%" in tip
    assert app._metric_about_tip({"name": "x"}) == ""
    # every catalog metric has one
    from easi import metrics as _m  # noqa: F401  (the catalog loads with the app)
    assert all(app._metric_about_tip(m) for m in app._METRIC_BY_FID.values())


def test_the_borrowed_note_is_the_shared_notice():
    row = {"anchorNote": "nearest StreamCat reach Mink Brook", "status": "computed"}
    note = app._borrowed_metric_note(row) if app._is_borrowed(row) else None
    src = SRC.split("def _borrowed_metric_note(row):", 1)[1].split("\ndef ", 1)[0]
    assert 'class_="staf-metric-warn"' in src and "easi-disclaimer" not in src
    assert note is None or "staf-metric-warn" in str(note)


def test_the_page_loads_the_shared_rows_and_matches_the_other_apps_width():
    assert 'href="staf/metric-rows.css?v=1"' in SRC and 'src="staf/metric-rows.js?v=1"' in SRC
    assert SRC.index("staf/metric-rows.js") < SRC.index("worksheet.js?v=")
    css = (HERE / "www" / "styles.css").read_text(encoding="utf-8")
    assert ".sfari-fnpanel-inner { max-width: 1040px;" in css            # SFARI's and DEEP's width
    assert ".easi-card-wide { grid-column: 1 / -1;" in css
    for gone in (".easi-metric-card {", ".sfari-suggest-chip {", ".sfari-evidence {",
                 ".easi-method > summary", ".easi-method-critsec {"):
        assert gone not in css, gone
    assert config is not None

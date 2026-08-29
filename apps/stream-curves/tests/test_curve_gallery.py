"""The curve gallery builders (views/curve_gallery.py): pure UI builders read
as HTML strings, and the headless AppState path on the bundled OSAM workbook."""
from __future__ import annotations

import pytest
from shiny import reactive

from streamcurves import curve_automation as ca
from streamcurves import curve_svg as cs
from streamcurves import run_state as rs
from streamcurves.cleaning import clean_data
from streamcurves.derive import derive_variables
from streamcurves.precheck import run_metric_precheck
from streamcurves.workbook import read_input_workbook
from tests.golden_io import GOLDEN_DIR
from views import curve_gallery as cg
from views import summary_state as ss
from views.state import AppState

FIXTURE = GOLDEN_DIR.parent / "fixtures" / "OSAM_summarydata.xlsx"
CHANNEL = "summary-curve_gallery_action"
# a bare input id here (the app passes a namespaced ResolvedId, which shiny
# accepts with its hyphen; a plain string must be letters, digits, underscores)
FILTER = "summary_gallery_filter"


def _unescape(html: str) -> str:
    # the handlers live in HTML attributes, so their quotes render as entities
    return html.replace("&apos;", "'").replace("&quot;", '"')


def _tile(**over):
    base = {
        "metric": "phab_XEMBED", "display_name": "Embeddedness", "function": "Hyporheic connectivity",
        "units": "%", "in_scope": True, "needs_review": False,
        "review_status": rs.CURVE_STATUS_AUTO_OK, "decision": rs.DECISION_AUTO,
        "reference_range": (20.0, 80.0), "domain": (0.0, 100.0),
        "strata": [{"label": None, "points": [(10.0, 1.0), (40.0, 0.7), (70.0, 0.3), (100.0, 0.0)],
                    "curve_status": "complete", "n_reference": 23, "curve_source": "auto"}],
        "flags": [], "badge": None,
    }
    base.update(over)
    return base


def _n_tiles(html: str) -> int:
    return html.count('class="curve-tile ') + html.count('class="curve-tile"')


def test_gallery_ui_renders_one_tile_per_row_with_counts():
    rows = [_tile(metric=f"m{i}") for i in range(4)]
    rows[1].update(needs_review=True, decision=rs.DECISION_PENDING)
    rows[2].update(in_scope=False, decision=rs.DECISION_REMOVED)
    html = str(cg.gallery_ui(rows, channel_id=CHANNEL, filter_input_id=FILTER))
    assert _n_tiles(html) == 4
    assert "4 curves" in html and "1 flagged, 1 not in scope" in html
    assert f'id="{FILTER}"' in html and "Not in scope" in html
    assert "0.39 and 0.69" in html
    # no mapping given: the tiles' own label places them (Hyporheic connectivity
    # is a Hydraulics function) under one discipline section and one function row
    assert html.count("curve-gallery-section-head") == 1 and "discipline-hydraulics" in html
    assert html.count('class="curve-gallery-fn"') == 1 and "Hyporheic connectivity" in html


def test_gallery_ui_empty_filter_shows_a_message():
    rows = [_tile(metric="a"), _tile(metric="b")]
    html = str(cg.gallery_ui(rows, channel_id=CHANNEL, filter_input_id=FILTER, filter_mode="flagged"))
    assert _n_tiles(html) == 0 and "No curves match this filter." in html
    assert "curve-gallery-section" not in html


def test_filter_rows_modes():
    rows = [_tile(metric="a"),
            _tile(metric="b", needs_review=True, decision=rs.DECISION_PENDING),
            _tile(metric="c", in_scope=False, decision=rs.DECISION_REMOVED),
            _tile(metric="d", strata=[_tile()["strata"][0], dict(_tile()["strata"][0], label="B")])]
    assert [r["metric"] for r in cg.filter_rows(rows, "all")] == ["a", "b", "c", "d"]
    assert [r["metric"] for r in cg.filter_rows(rows, "flagged")] == ["b"]
    assert [r["metric"] for r in cg.filter_rows(rows, "out_of_scope")] == ["c"]
    assert [r["metric"] for r in cg.filter_rows(rows, "stratified")] == ["d"]
    assert cg.gallery_counts(rows) == {"n": 4, "flagged": 1, "out_of_scope": 1, "stratified": 1}


def test_tile_onclick_targets_the_channel_and_escapes_quotes():
    metric = "O'Brien \"x\""
    html = _unescape(str(cg.tile_ui(_tile(metric=metric), channel_id=CHANNEL)))
    assert f"Shiny.setInputValue('{CHANNEL}'" in html
    assert "\\'Brien" in html and '\\"x\\"' in html
    assert '"action": "open"' in html and "priority:'event'" in html


def test_tile_secondary_action_stops_propagation_and_targets_the_table():
    html = _unescape(str(cg.tile_ui(_tile(), channel_id=CHANNEL)))
    idx = html.find("event.stopPropagation();")
    assert idx >= 0 and html.find("Shiny.setInputValue", idx) > idx
    assert '"action": "table"' in html
    assert 'role="button"' in html and 'tabindex="0"' in html and 'data-metric="phab_XEMBED"' in html
    assert "curve-tile-table" in html and "<svg " in html
    assert 'class="curve-tile is-flagged"' in _unescape(str(cg.tile_ui(
        _tile(needs_review=True, decision=rs.DECISION_PENDING), channel_id=CHANNEL)))


def test_tile_recompute_action_targets_the_channel_and_spins_when_busy():
    html = _unescape(str(cg.tile_ui(_tile(), channel_id=CHANNEL)))
    assert '"action": "recompute"' in html and "curve-tile-recompute" in html
    busy = _unescape(str(cg.tile_ui(_tile(), channel_id=CHANNEL, busy=True)))
    assert "streamcurves-inline-spinner" in busy and "disabled" in busy
    # cross-listed copies carry no recompute button (the primary tile owns it)
    cross = _unescape(str(cg.tile_ui(
        _tile(), channel_id=CHANNEL,
        cross={"primary_function_name": "Hyporheic connectivity"}, under="fn")))
    assert "curve-tile-recompute" not in cross


def test_gallery_toolbar_recompute_all_button():
    rows = [_tile(metric="a"), _tile(metric="b")]
    html = str(cg.gallery_ui(rows, channel_id=CHANNEL, filter_input_id=FILTER,
                             recompute_all_id="gal_recompute"))
    assert 'id="gal_recompute"' in html and "Recompute all" in html
    disabled = str(cg.gallery_ui(rows, channel_id=CHANNEL, filter_input_id=FILTER,
                                 recompute_all_id="gal_recompute",
                                 recompute_all_disabled=True,
                                 busy_metrics={"a"}))
    assert "disabled" in disabled and "streamcurves-inline-spinner" in disabled
    # without an id, the toolbar carries no recompute button at all
    plain = str(cg.gallery_ui(rows, channel_id=CHANNEL, filter_input_id=FILTER))
    assert "Recompute all" not in plain


def test_review_queue_breakdown_counts_by_status_label():
    from views.summary_page import review_queue_breakdown

    review = {
        "m1": {"status": rs.CURVE_STATUS_INSUFFICIENT},
        "m2": {"status": rs.CURVE_STATUS_INSUFFICIENT},
        "m3": {"status": rs.CURVE_STATUS_DEGENERATE},
        "m4": {},
    }
    out = review_queue_breakdown(review, ["m1", "m2", "m3", "m4"])
    assert "2 Insufficient data" in out
    assert "1 Degenerate curve" in out
    assert "1 Needs review" in out


def test_review_status_labels_cover_every_status():
    assert set(cg.REVIEW_STATUS_LABELS) == set(rs.CURVE_STATUSES)
    assert cg.DECISION_LABELS[rs.DECISION_REMOVED] == "Removed"


def test_tile_row_accepts_frames_records_and_nothing():
    import pandas as pd
    rec = {"stratum": None, "min_val": 0.0, "max_val": 10.0, "n_reference": 5, "curve_status": "complete",
           "curve_source": "auto", "curve_points": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 1.0}]}
    from_frame = cg.tile_row("m", pd.DataFrame([rec]), metric_entry=None, review_entry=None, function_label="F")
    from_records = cg.tile_row("m", [rec], metric_entry=None, review_entry=None, function_label="F")
    assert from_frame["strata"][0]["points"] == from_records["strata"][0]["points"] == [(0.0, 0.0), (10.0, 1.0)]
    assert from_frame["function"] == "F"
    assert cg.tile_row("m", None, metric_entry=None, review_entry=None, function_label=None)["strata"] == []


# ── headless AppState path ────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def loaded_state() -> AppState:
    if not FIXTURE.is_file():
        pytest.skip("OSAM fixture workbook absent")
    state = AppState.fresh()
    bundle = read_input_workbook(FIXTURE)
    cleaned, _ = clean_data(
        bundle["raw_data"], bundle["metric_config"],
        bundle["strat_config"], bundle["factor_recode_config"],
    )
    dat = derive_variables(
        cleaned, bundle["factor_recode_config"],
        bundle["predictor_config"], bundle["strat_config"],
    )
    state.metric_config.set(bundle["metric_config"])
    state.strat_config.set(bundle["strat_config"])
    state.predictor_config.set(bundle["predictor_config"])
    state.factor_recode_config.set(bundle["factor_recode_config"])
    state.data.set(dat)
    state.precheck_df.set(run_metric_precheck(dat, bundle["metric_config"]))
    state.data_fingerprint.set("test-fp")
    state.current_metric.set("perRiffle")
    state.app_data_loaded.set(True)
    return state


def test_gallery_rows_cover_every_eligible_metric_in_table_order(loaded_state):
    with reactive.isolate():
        mc = loaded_state.metric_config()
    eligible = ss.eligible_summary_metrics(mc)
    rows = cg.gallery_rows(loaded_state)
    assert [r["metric"] for r in rows] == eligible
    # every tile carries its grouping keys (this fixture has no mapping and no
    # column_functions, so they resolve to the Unmapped section)
    assert all("discipline" in r and "also_functions" in r for r in rows)
    # nothing computed yet: every tile is a placeholder, none is dotted
    assert all(r["strata"] == [] for r in rows)
    assert all("curve-tile-empty" in cs.tile_svg(r) and "out-of-scope" not in cs.tile_svg(r) for r in rows)

    ss.set_metric_curve_stratification(loaded_state, "perRiffle", "none")
    ss.recompute_metric_phase4(loaded_state, "perRiffle")
    ca.sync_curve_review_after_recompute(loaded_state, ["perRiffle"])
    tile = cg.gallery_rows(loaded_state, ["perRiffle"])[0]
    assert len(tile["strata"]) == 1 and len(tile["strata"][0]["points"]) >= 2
    assert tile["decision"] is not None and tile["reference_range"][0] is not None
    svg = cs.tile_svg(tile)
    assert svg.count("<polyline") == 1 and "curve-tile-range" in svg


def test_gallery_rows_follow_review_decisions(loaded_state):
    ca.set_review_decision(loaded_state, "perRiffle", rs.DECISION_REMOVED, note="gallery test", actor="tester")
    tile = cg.gallery_rows(loaded_state, ["perRiffle"])[0]
    assert tile["in_scope"] is False and tile["decision"] == rs.DECISION_REMOVED
    assert "out-of-scope" in cs.tile_svg(tile) and "is-removed" in cs.tile_state_classes(tile)
    with reactive.isolate():
        review = loaded_state.curve_review() or {}
    for r in cg.gallery_rows(loaded_state):
        entry = review.get(r["metric"])
        if entry:
            assert r["in_scope"] == rs.is_in_scope(entry)
            assert r["needs_review"] == rs.needs_review(entry)

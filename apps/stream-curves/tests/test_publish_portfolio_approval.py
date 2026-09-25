"""SELECT-01 is answerable in Select final curves and read by the Publish page.

A function carrying more metrics than the portfolio maximum publishes only with a
recorded human approval. The batch runner takes one as ``--approve-portfolio`` and an
opened build carries its own on the origin. Since 2026-09-25 (one decision authority)
the approval is given on the function's row in Select final curves, under initials,
into the session's ``portfolio_approvals``; the Publish page lists what is still
unapproved as a checklist line and writes the field into ``meta['portfolioApprovals']``
(``approvedBy``) for the library's unchanged gate.

These tests pin the pieces that have to agree: the count the page makes without
building a bundle, the count the gate makes from the real bundle, the field's
accessors, and the handler that turns the field into the meta.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

import views.publish as pub
from streamcurves import deep_export, library as lib
from streamcurves.deep_export import build_deep_assessment_bundle, deep_collect_curve_rows
from views import assessment_publish as ap
from views.state import AppState

FUNCTION = "Water & soil quality"
FUNCTION_ID = "water-soil-quality"
TRIPLE = ("chem_COND", "chem_PH", "chem_TURB")


def _row(metric):
    return {"metric": metric, "display_name": np.nan, "higher_is_better": False,
            "curve_status": "complete", "stratum": "",
            "curve_points": pd.DataFrame({"point_order": [1, 2, 3],
                                          "metric_value": [0.0, 10.0, 50.0],
                                          "index_score": [1.0, 0.7, 0.0]})}


def _completed(metrics=TRIPLE):
    out = {}
    for m in metrics:
        rows = pd.DataFrame([_row(m)])
        rows["curve_points"] = [_row(m)["curve_points"]]
        out[m] = {"phase4_curve_rows": rows}
    return out


def _mapping(metrics=TRIPLE):
    return pd.DataFrame(
        [(m, "Physicochemistry", FUNCTION, i) for i, m in enumerate(metrics, 1)],
        columns=["metric_key", "discipline", "function_label", "sort_order"])


def _state(metrics=TRIPLE, *, origin=None) -> AppState:
    st = AppState.fresh()
    st.completed_metrics.set(_completed(metrics))
    st.discipline_function_mapping.set(_mapping(metrics))
    st.assessment_source.set(origin)
    return st


# ---- the count the page makes, without building a bundle --------------------
def test_the_quick_count_matches_the_bundle():
    """The page's count and the gate's count must name the same function, or the
    page asks for an approval the gate does not want, or misses one it does."""
    completed, mapping = _completed(), _mapping()
    quick = deep_export.metrics_per_function_quick(completed, mapping)
    bundle = build_deep_assessment_bundle(deep_collect_curve_rows(completed), mapping, {}, {})
    from_bundle = {str(b["functionId"]): len(b["metrics"]) for b in bundle["metricsByFunction"]}
    assert quick[FUNCTION_ID] == from_bundle[FUNCTION_ID] == 3
    assert lib.functions_over_metric_limit(bundle) == [(FUNCTION_ID, 3)]


def test_the_quick_count_adds_the_metrics_that_join_outside_the_mapping():
    """A pressure-screen build's fixed criteria are in the bundle but not in the
    editable mapping, so a function can reach the maximum on them."""
    counts = deep_export.metrics_per_function_quick(
        _completed(("chem_COND",)), _mapping(("chem_COND",)),
        extra={FUNCTION_ID: 2, "catchment-hydrology": 2})
    assert counts[FUNCTION_ID] == 3 and counts["catchment-hydrology"] == 2


def test_nothing_built_yet_counts_nothing():
    assert deep_export.metrics_per_function_quick({}, _mapping()) == {}


# ---- what the page asks for -------------------------------------------------
def test_the_page_asks_for_an_approval_when_a_function_is_over_the_maximum():
    pending = ap.portfolio_approval_needed(_state())
    assert [p["functionId"] for p in pending] == [FUNCTION_ID]
    assert pending[0]["nMetrics"] == 3
    assert pending[0]["functionName"]                      # named, not just an id


def test_two_metrics_need_no_approval():
    assert ap.portfolio_approval_needed(_state(TRIPLE[:2])) == []


def test_an_origin_approval_is_not_asked_for_twice():
    """An opened agent build already carries the approval its own stage recorded."""
    origin = {"portfolio_approvals": [{"functionId": FUNCTION_ID, "approvedBy": "owner"}]}
    assert ap.portfolio_approval_needed(_state(origin=origin)) == []


def test_a_metric_the_review_removed_is_not_counted():
    """The page counts what the bundle will carry: a removed third metric does not
    put its function over the maximum, so no approval is asked for."""
    st = _state()
    st.curve_review.set({m: {"status": "auto_ok",
                             "decision": "removed_from_scope" if m == TRIPLE[2] else "auto_finalized"}
                         for m in TRIPLE})
    assert ap.portfolio_approval_needed(st) == []


def test_an_approval_without_an_approver_does_not_count():
    origin = {"portfolio_approvals": [{"functionId": FUNCTION_ID, "approvedBy": ""}]}
    assert [p["functionId"] for p in ap.portfolio_approval_needed(_state(origin=origin))] \
        == [FUNCTION_ID]


def test_the_prompt_names_the_function_and_its_count():
    text = pub._portfolio_approval_text(
        [{"functionId": FUNCTION_ID, "functionName": "Water and soil quality", "nMetrics": 3}])
    assert "Water and soil quality (3 metrics)" in text and "SELECT-01" in text
    assert "Select final curves" in text
    assert "—" not in text and ";" not in text            # project style gates


# ---- the field, its accessors, and what reaches the meta --------------------
SRC = Path(pub.__file__).read_text(encoding="utf-8")


def test_an_approval_given_in_select_final_curves_reaches_the_field_and_the_meta():
    from views import state as st
    state = AppState.fresh()
    assert st.portfolio_approvals(state) == []
    with pytest.raises(ValueError):
        st.add_portfolio_approval(state, FUNCTION_ID, approver="   ")
    st.add_portfolio_approval(state, FUNCTION_ID, approver=" GM ", note="Complementary chemistry.")
    field = st.portfolio_approvals(state)
    assert len(field) == 1
    assert field[0]["functionId"] == FUNCTION_ID and field[0]["approver"] == "GM"
    assert field[0]["note"] == "Complementary chemistry." and field[0]["date"]
    assert set(field[0]) == {"functionId", "approver", "note", "date"}
    # replacing, never duplicating
    st.add_portfolio_approval(state, FUNCTION_ID, approver="AB", note="Again.")
    assert [a["approver"] for a in st.portfolio_approvals(state)] == ["AB"]
    assert st.approved_function_ids(st.portfolio_approvals(state)) == {FUNCTION_ID}
    # the meta shape the library gate reads
    meta = st.approvals_for_meta(st.portfolio_approvals(state))
    assert meta[0]["functionId"] == FUNCTION_ID and meta[0]["approvedBy"] == "AB"
    assert meta[0]["note"] == "Again." and meta[0]["approvedAt"]
    completed, mapping = _completed(), _mapping()
    bundle = build_deep_assessment_bundle(deep_collect_curve_rows(completed), mapping, {}, {})
    lib._require_portfolio_approval("t", bundle, {"portfolioApprovals": meta})   # accepted
    with pytest.raises(ValueError, match="SELECT-01"):
        lib._require_portfolio_approval("t", bundle, {"portfolioApprovals": []})
    # withdrawing empties the field back to None (absent reads as no approval)
    st.withdraw_portfolio_approval(state, FUNCTION_ID)
    assert st.portfolio_approvals(state) == []
    with reactive.isolate():
        assert state.portfolio_approvals() is None
    # a blank approver approves nothing, whichever spelling it came in
    assert st.approved_function_ids([{"functionId": "x", "approvedBy": ""}]) == set()
    assert st.approved_function_ids([{"functionId": "x", "approvedBy": "owner"}]) == {"x"}


def test_an_approval_round_trips_through_the_session_and_reset_clears_it():
    """The field is persisted through session_io once WP-A lists it; the accessors
    read either spelling, so a build's meta.portfolioApprovals written into the field
    (approvedBy) counts too."""
    from streamcurves import session_io as sio
    from views import state as st
    state = AppState.fresh()
    st.add_portfolio_approval(state, FUNCTION_ID, approver="GM", note="n")
    with reactive.isolate():
        raw = state.portfolio_approvals()
    if "portfolio_approvals" in sio.SESSION_FIELDS:
        payload = sio.dump_session_fields({"portfolio_approvals": raw}, session_name="t")
        back = sio.decode_session_fields(json.loads(sio.dumps_session(payload)))
        assert back["portfolio_approvals"] == raw
        assert sio.decode_session_fields({}).get("portfolio_approvals") is None
    else:
        pytest.xfail("session_io does not list portfolio_approvals yet (WP-A adds it)")
    written_by_a_build = [{"functionId": FUNCTION_ID, "approvedBy": "owner", "note": "carried"}]
    state.portfolio_approvals.set(written_by_a_build)
    assert st.portfolio_approvals(state)[0]["approver"] == "owner"
    assert st.approvals_for_meta(written_by_a_build)[0]["approvedBy"] == "owner"
    st.reset_app_to_startup(state)
    with reactive.isolate():
        assert state.portfolio_approvals() is None


def test_the_handler_judges_the_real_bundle_and_writes_the_field_into_the_meta():
    """The page's quick count may be stale; the publish itself reads the bundle it
    is about to write, carries the field's approvals as approvedBy, and refuses,
    pointing at Select final curves, while a function is still unapproved."""
    assert "lib.functions_over_metric_limit(bundle)" in SRC
    assert "given = _st.approvals_for_meta(_st.portfolio_approvals(state))" in SRC
    assert '"approvedBy": maintainer' not in SRC, "no approval is invented under the publisher"
    handler = SRC[SRC.index("if unapproved:"):SRC.index("if source_doc:")]
    assert "state.run_stage_status.set(prev_stage_status)" in handler
    assert "return" in handler and "Select final curves" in handler
    # the checklist line reads the same helper the section uses
    assert "fs.unapproved_functions(state, reg)" in SRC
    assert "_portfolio_approval_text(left[\"unapproved\"])" in SRC


def test_the_page_no_longer_carries_the_checkbox():
    from tests.test_publish_page import HANDLER_IDS
    assert "pub_select01" not in HANDLER_IDS
    assert '"pub_select01"' not in SRC and "pub-select01" not in SRC

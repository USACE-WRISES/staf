"""Pure unit tests for streamcurves.run_state (guided-run vocabulary + helpers)."""
from __future__ import annotations

import pandas as pd

from streamcurves import run_state as rs


# --- run meta --------------------------------------------------------------- #
def test_new_run_meta_stamps_versions():
    meta = rs.new_run_meta(region={"code": "58"})
    assert meta["curve_method_version"] == rs.CURVE_METHOD_VERSION
    assert meta["screening_method_version"] == rs.SCREENING_METHOD_VERSION
    assert meta["region"] == {"code": "58"}
    assert meta["created"] == meta["updated"]


def test_touch_run_meta_preserves_created():
    meta = rs.new_run_meta()
    created = meta["created"]
    touched = rs.touch_run_meta(meta)
    assert touched["created"] == created
    assert "updated" in touched


def test_touch_run_meta_from_empty_builds_fresh():
    meta = rs.touch_run_meta(None)
    assert meta["curve_method_version"] == rs.CURVE_METHOD_VERSION


# --- classification --------------------------------------------------------- #
def test_classify_auto_ok():
    rows = [{"curve_status": "complete", "n_reference": 12}]
    status, reasons = rs.classify_curve_proposal(rows, mapping_ok=True, strat_ok=True)
    assert status == rs.CURVE_STATUS_AUTO_OK
    assert reasons == []


def test_classify_error_takes_precedence():
    status, reasons = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], mapping_ok=False, exc=ValueError("boom")
    )
    assert status == rs.CURVE_STATUS_ERROR
    assert "boom" in reasons[0]


def test_classify_unmapped():
    status, _ = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], mapping_ok=False
    )
    assert status == rs.CURVE_STATUS_UNMAPPED


def test_classify_insufficient_data():
    status, _ = rs.classify_curve_proposal([{"curve_status": "insufficient_data"}])
    assert status == rs.CURVE_STATUS_INSUFFICIENT


def test_classify_empty_rows_is_insufficient():
    status, _ = rs.classify_curve_proposal([])
    assert status == rs.CURVE_STATUS_INSUFFICIENT


def test_classify_degenerate():
    for st in ("degenerate_q25", "degenerate_curve"):
        status, reasons = rs.classify_curve_proposal([{"curve_status": st}])
        assert status == rs.CURVE_STATUS_DEGENERATE
        assert reasons


def test_classify_strat_review():
    status, _ = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], strat_ok=False
    )
    assert status == rs.CURVE_STATUS_STRAT_REVIEW


def test_classify_accepts_dataframe():
    df = pd.DataFrame([{"curve_status": "complete", "n_reference": 9}])
    status, _ = rs.classify_curve_proposal(df)
    assert status == rs.CURVE_STATUS_AUTO_OK


# --- fingerprint ------------------------------------------------------------ #
def test_fingerprint_is_stable_and_sensitive():
    rows = [{"stratum": "all", "curve_status": "complete", "q25": 1.0, "points": [[0, 0], [1, 1]]}]
    fp1 = rs.proposal_fingerprint(rows)
    fp2 = rs.proposal_fingerprint(list(rows))
    assert fp1 == fp2
    tweaked = [dict(rows[0], q25=2.0)]
    assert rs.proposal_fingerprint(tweaked) != fp1
    assert rs.proposal_fingerprint(rows, mapping="fnA") != fp1


# --- review entry lifecycle ------------------------------------------------- #
def test_new_entry_auto_ok_is_in_scope():
    entry = rs.new_curve_review_entry(rs.CURVE_STATUS_AUTO_OK, [], "fp", {})
    assert entry["decision"] == rs.DECISION_AUTO
    assert rs.is_in_scope(entry)
    assert not rs.needs_review(entry)


def test_new_entry_flagged_needs_review():
    entry = rs.new_curve_review_entry(rs.CURVE_STATUS_DEGENERATE, ["x"], "fp", {})
    assert entry["decision"] == rs.DECISION_PENDING
    assert rs.needs_review(entry)
    assert not rs.is_in_scope(entry)


def test_reconcile_keeps_finalized_on_noop():
    old = rs.apply_review_decision(
        rs.new_curve_review_entry(rs.CURVE_STATUS_DEGENERATE, [], "fp1", {}),
        rs.DECISION_FINALIZED,
        note="checked",
        actor="me",
    )
    new = rs.reconcile_curve_review_entry(
        old, status=rs.CURVE_STATUS_DEGENERATE, reasons=[], fingerprint="fp1",
        proposal_summary={},
    )
    assert new["decision"] == rs.DECISION_FINALIZED
    assert rs.is_in_scope(new)


def test_reconcile_forces_rereview_on_fingerprint_change():
    old = rs.apply_review_decision(
        rs.new_curve_review_entry(rs.CURVE_STATUS_DEGENERATE, [], "fp1", {}),
        rs.DECISION_FINALIZED,
        note="checked",
    )
    new = rs.reconcile_curve_review_entry(
        old, status=rs.CURVE_STATUS_DEGENERATE, reasons=[], fingerprint="fp2",
        proposal_summary={},
    )
    assert new["decision"] == rs.DECISION_PENDING
    assert len(new["history"]) == 1
    assert new["history"][0]["fingerprint"] == "fp1"


def test_removed_from_scope_is_not_published():
    entry = rs.apply_review_decision(
        rs.new_curve_review_entry(rs.CURVE_STATUS_INSUFFICIENT, [], "fp", {}),
        rs.DECISION_REMOVED,
    )
    assert not rs.is_in_scope(entry)
    assert not rs.needs_review(entry)


# --- scope + review queries ------------------------------------------------- #
def _review_map():
    return {
        "mA": rs.new_curve_review_entry(rs.CURVE_STATUS_AUTO_OK, [], "a", {}),
        "mB": rs.apply_review_decision(
            rs.new_curve_review_entry(rs.CURVE_STATUS_DEGENERATE, [], "b", {}),
            rs.DECISION_FINALIZED,
        ),
        "mC": rs.new_curve_review_entry(rs.CURVE_STATUS_INSUFFICIENT, [], "c", {}),
        "mD": rs.apply_review_decision(
            rs.new_curve_review_entry(rs.CURVE_STATUS_INSUFFICIENT, [], "d", {}),
            rs.DECISION_REMOVED,
        ),
    }


def test_intended_and_flagged_partition():
    cr = _review_map()
    assert rs.intended_metrics_for_publish(cr) == ["mA", "mB"]
    assert rs.flagged_metrics(cr) == ["mC"]


# --- site mask config from exclusions --------------------------------------- #
def test_site_mask_config_from_exclusions_positions():
    raw = pd.DataFrame(
        {"site_id": ["S1", "S2", "S3", "S4"], "label": ["a", "b", "c", "d"]}
    )
    exclusions = [{"site_id": "S2"}, {"site_id": "S4"}]
    cfg = rs.site_mask_config_from_exclusions(
        raw, exclusions, site_id_column="site_id", site_label_column="label"
    )
    assert cfg["masked_site_ids"] == [2, 4]  # 1-based positions
    assert cfg["site_labels"] == ["b", "d"]
    assert cfg["site_label_column"] == "label"


def test_site_mask_config_empty_when_no_exclusions():
    raw = pd.DataFrame({"site_id": ["S1"]})
    cfg = rs.site_mask_config_from_exclusions(raw, [], site_id_column="site_id")
    assert cfg["masked_site_ids"] == []


# --- readiness -------------------------------------------------------------- #
def test_readiness_blocks_until_complete():
    snap = {"has_region": True, "has_screening": True, "n_retained": 5,
            "enriched": True, "curve_review": _review_map()}
    # mC is still pending -> not ready
    assert not rs.is_ready_to_publish(snap)
    cr = dict(_review_map())
    cr["mC"] = rs.apply_review_decision(cr["mC"], rs.DECISION_REMOVED)
    snap["curve_review"] = cr
    assert rs.is_ready_to_publish(snap)


def test_readiness_needs_region_and_retained():
    assert not rs.is_ready_to_publish({})
    checklist = rs.readiness_checklist({})
    assert {i["key"] for i in checklist} == {
        "region", "screening", "enriched", "curves", "review"
    }


# --- stage status ----------------------------------------------------------- #
def test_derive_stage_status_fresh():
    out = rs.derive_stage_status({}, {})
    assert out["region_sources"]["status"] == rs.STAGE_READY
    assert out["candidate_screening"]["status"] == rs.STAGE_BLOCKED
    assert out["publish"]["status"] == rs.STAGE_BLOCKED


def test_derive_stage_status_running_screening():
    out = rs.derive_stage_status(
        {"has_region": True, "n_candidates": 71}, {"candidate_screening": True}
    )
    assert out["candidate_screening"]["status"] == rs.STAGE_RUNNING


def test_derive_stage_status_screening_zero_retained_is_attention():
    out = rs.derive_stage_status(
        {"has_region": True, "n_candidates": 71, "has_screening": True, "n_retained": 0}
    )
    assert out["candidate_screening"]["status"] == rs.STAGE_ATTENTION


def test_derive_stage_status_flagged_review_attention():
    out = rs.derive_stage_status(
        {"enriched": True, "n_retained": 5, "curve_review": _review_map()}
    )
    assert out["curve_review"]["status"] == rs.STAGE_ATTENTION

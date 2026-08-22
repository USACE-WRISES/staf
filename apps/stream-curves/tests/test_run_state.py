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


def test_classify_strat_review_carries_the_named_reason():
    status, reasons = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], strat_ok=False,
        strat_reason="Stratum 'B' has n = 6, below the very-small-stratum rule."
    )
    assert status == rs.CURVE_STATUS_STRAT_REVIEW
    assert "n = 6" in reasons[0]


def test_classify_data_review_routes_high_missingness():
    status, reasons = rs.classify_curve_proposal(
        [{"curve_status": "complete", "n_reference": 30}],
        data_ok=False, data_reason="Missing-data fraction 55% exceeds DATA-03."
    )
    assert status == rs.CURVE_STATUS_DATA_REVIEW
    assert "55%" in reasons[0]
    # And it lands in the review-required set like every non-auto_ok status.
    assert rs.CURVE_STATUS_DATA_REVIEW in rs.CURVE_REVIEW_REQUIRED


def test_classify_data_review_defaults_its_reason():
    status, reasons = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], data_ok=False
    )
    assert status == rs.CURVE_STATUS_DATA_REVIEW
    assert reasons == [rs.REVIEW_REASONS["data_review"]]


# --- DATA-07/08 per-stratum floors (strata_floor_check) --------------------- #
def test_floor_check_passes_unstratified_rows():
    ok, reason = rs.strata_floor_check([{"curve_status": "complete", "n_reference": 12}])
    assert ok and reason is None


def test_floor_check_passes_adequate_strata():
    rows = [
        {"curve_status": "complete", "stratum": "A", "n_reference": 20},
        {"curve_status": "complete", "stratum": "B", "n_reference": 16},
    ]
    ok, reason = rs.strata_floor_check(rows)
    assert ok and reason is None


def test_floor_check_flags_a_stratum_below_the_data07_floor():
    rows = [
        {"curve_status": "complete", "stratum": "A", "n_reference": 20},
        {"curve_status": "complete", "stratum": "B", "n_reference": 12},
    ]
    ok, reason = rs.strata_floor_check(rows)
    assert not ok
    assert "DATA-07" in reason and "'B'" in reason


def test_floor_check_flags_a_very_small_stratum_first():
    rows = [
        {"curve_status": "complete", "stratum": "A", "n_reference": 40},
        {"curve_status": "complete", "stratum": "B", "n_reference": 6},
    ]
    ok, reason = rs.strata_floor_check(rows)
    assert not ok
    assert "DATA-08" in reason


def test_floor_check_flags_a_stratum_under_ten_percent_of_the_pool():
    # n = 9 clears the very-small count (8) but is under 10 percent of 100.
    rows = [
        {"curve_status": "complete", "stratum": "A", "n_reference": 91},
        {"curve_status": "complete", "stratum": "B", "n_reference": 9},
    ]
    ok, reason = rs.strata_floor_check(rows)
    assert not ok
    assert "DATA-08" in reason


# --- CURVE-05 expected shape versus realized shape -------------------------- #
def _pts(pairs):
    return [{"metric_value": x, "index_score": y} for x, y in pairs]


def test_expected_shape_derives_from_the_curated_declaration():
    assert rs.expected_shape_from_entry({"higher_is_better": True}) == "monotone_increasing"
    assert rs.expected_shape_from_entry({"higher_is_better": False}) == "monotone_decreasing"
    assert rs.expected_shape_from_entry(
        {"higher_is_better": None, "curve_form": "optimum"}) == "optimum"
    assert rs.expected_shape_from_entry(
        {"expected_shape": "optimum", "higher_is_better": True}) == "optimum"
    assert rs.expected_shape_from_entry({}) is None


def test_realized_shapes_are_read_from_the_points():
    assert rs.realized_curve_shape(_pts([(0, 0), (5, 0.5), (10, 1)])) == "monotone_increasing"
    assert rs.realized_curve_shape(_pts([(0, 1), (5, 0.5), (10, 0)])) == "monotone_decreasing"
    assert rs.realized_curve_shape(
        _pts([(0, 0), (4, 1), (6, 1), (10, 0)])) == "optimum"
    assert rs.realized_curve_shape(_pts([(0, 0.5), (10, 0.5)])) is None
    assert rs.realized_curve_shape(None) is None


def test_shape_conflict_flags_an_edited_backwards_curve():
    entry = {"higher_is_better": False, "curve_form": "monotone"}
    rows = [{"curve_status": "complete",
             "curve_points": _pts([(0, 0), (10, 1)])}]  # rises, but lower is better
    ok, reason = rs.shape_conflict_check(rows, entry)
    assert not ok
    assert "CURVE-05" in reason and "monotone_decreasing" in reason


def test_shape_conflict_passes_the_matching_curve_and_the_unresolved_metric():
    entry = {"higher_is_better": False}
    rows = [{"curve_status": "complete", "curve_points": _pts([(0, 1), (10, 0)])}]
    assert rs.shape_conflict_check(rows, entry) == (True, None)
    # No approved expectation, no conflict claim (direction review handles it).
    assert rs.shape_conflict_check(rows, {}) == (True, None)


def test_classify_shape_conflict_routes_to_review():
    status, reasons = rs.classify_curve_proposal(
        [{"curve_status": "complete"}], shape_ok=False,
        shape_reason="Realized curve shape 'monotone_increasing' conflicts."
    )
    assert status == rs.CURVE_STATUS_SHAPE_CONFLICT
    assert rs.CURVE_STATUS_SHAPE_CONFLICT in rs.CURVE_REVIEW_REQUIRED


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


# --- workflow navigation vocabulary ------------------------------------------ #
def test_stage_substeps_cover_wizard_steps_once():
    seen = [s for subs in rs.STAGE_SUBSTEPS.values() for s, _ in subs]
    assert sorted(seen) == [1, 2, 3, 4, 5, 6, 7]
    assert set(rs.STAGE_SUBSTEPS) <= set(rs.STAGE_KEYS)


def test_stage_landings_cover_every_stage():
    assert set(rs.STAGE_LANDINGS) == set(rs.STAGE_KEYS)
    # Wizard stages land on their first sub-step; page stages have no step.
    for key, (nav, step) in rs.STAGE_LANDINGS.items():
        if key in rs.STAGE_SUBSTEPS:
            assert nav == "data"
            assert step == rs.STAGE_SUBSTEPS[key][0][0]
        else:
            assert step is None
    assert rs.stage_landing("curve_review") == ("curves", None)
    assert rs.stage_landing("publish") == ("publish", None)


def test_stage_for_wizard_step():
    assert rs.stage_for_wizard_step(1) == "region_sources"
    assert rs.stage_for_wizard_step(2) == "region_sources"
    assert rs.stage_for_wizard_step(3) == "candidate_screening"
    for s in (4, 5, 6, 7):
        assert rs.stage_for_wizard_step(s) == "enrichment_build"
    assert rs.stage_for_wizard_step(None) is None
    assert rs.stage_for_wizard_step(99) is None


def test_current_stage_pages_and_tools():
    assert rs.current_stage("curves", "landing", 1) == "curve_review"
    assert rs.current_stage("publish", "workspace", 7) == "publish"
    # Tool tabs highlight no stage.
    assert rs.current_stage("regional", "workspace", 5) is None
    assert rs.current_stage("xsec", "landing", 1) is None


def test_current_stage_data_views():
    # Wizard (fresh "new" or re-entry "wizard") follows the wizard step.
    assert rs.current_stage("data", "new", 1) == "region_sources"
    assert rs.current_stage("data", "wizard", 3) == "candidate_screening"
    assert rs.current_stage("data", "wizard", 6) == "enrichment_build"
    # Unknown step falls back to the first stage.
    assert rs.current_stage("data", "new", None) == "region_sources"
    # Opened-project workspace is the Refine & map surface.
    assert rs.current_stage("data", "workspace", 7) == "refine_map"
    # Landing: the natural starting point.
    assert rs.current_stage("data", "landing", 1) == "region_sources"


def test_tool_vocabulary_is_labelled_and_separate_from_the_stages():
    assert set(rs.TOOL_LABELS) == set(rs.TOOL_KEYS)
    assert all(rs.TOOL_LABELS[k].strip() for k in rs.TOOL_KEYS)
    # The side analyses are not steps: keeping them out of the stage vocabulary is
    # what stops the strip numbering them, derive_stage_status scoring them, and
    # readiness_checklist gating publish on them.
    assert set(rs.TOOL_KEYS).isdisjoint(rs.STAGE_KEYS)
    assert set(rs.TOOL_KEYS).isdisjoint(rs.STAGE_LANDINGS)
    assert set(rs.TOOL_KEYS).isdisjoint(rs.STAGE_SUBSTEPS)


def test_current_tool_is_the_inverse_of_current_stage():
    for key in rs.TOOL_KEYS:
        assert rs.current_tool(key) == key
        assert rs.current_stage(key, "workspace", 5) is None
    for tab in ("data", "curves", "publish", None):
        assert rs.current_tool(tab) is None


# --- readiness -------------------------------------------------------------- #
def test_readiness_blocks_until_complete():
    snap = {"has_region": True, "has_screening": True, "n_retained": 5,
            "enriched": True, "mapping_confirmed": True,
            "curve_review": _review_map()}
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
        "region", "screening", "enriched", "mapping", "curves", "review",
        "coverage",
    }


def test_readiness_mapping_item_blocks_until_confirmed():
    """The publish page hard-gates on the confirmed flag; the checklist item
    mirrors it so the requirement shows before the click."""
    snap = {"has_region": True, "has_screening": True, "n_retained": 5,
            "enriched": True}
    cr = dict(_review_map())
    cr["mC"] = rs.apply_review_decision(cr["mC"], rs.DECISION_REMOVED)
    snap["curve_review"] = cr
    assert not rs.is_ready_to_publish(snap)
    item = next(i for i in rs.readiness_checklist(snap) if i["key"] == "mapping")
    assert item["ok"] is False
    assert rs.is_ready_to_publish(dict(snap, mapping_confirmed=True))


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


def test_derive_stage_status_refine_map():
    # Blocked until a dataset exists.
    out = rs.derive_stage_status({})
    assert out["refine_map"]["status"] == rs.STAGE_BLOCKED
    base = {"has_region": True, "has_screening": True, "n_retained": 5,
            "enriched": True}
    # Attention: functions with neither a metric nor a documented exception.
    out = rs.derive_stage_status(dict(base, n_unmapped_functions=3))
    assert out["refine_map"]["status"] == rs.STAGE_ATTENTION
    assert "3" in out["refine_map"]["detail"]
    # Ready: everything mapped, confirmation still pending.
    out = rs.derive_stage_status(dict(base, n_unmapped_functions=0))
    assert out["refine_map"]["status"] == rs.STAGE_READY
    # Done: mapping confirmed.
    out = rs.derive_stage_status(
        dict(base, n_unmapped_functions=0, mapping_confirmed=True)
    )
    assert out["refine_map"]["status"] == rs.STAGE_DONE
    # Every stage the strip renders has a status.
    assert set(out) == set(rs.STAGE_KEYS)


# --- workspace resolves to a stage that can show its section chips ---------- #
def test_workspace_view_resolves_to_the_refine_map_stage():
    # The strip marks this stage current for a reopened project sitting on the
    # workspace, so it has to be a stage that HAS sections -- otherwise the
    # current pill is a dead end with nothing under it (which is what users hit
    # back when the workspace resolved to a sub-stepless stage).
    assert rs.current_stage("data", "workspace", 1) == "refine_map"
    assert len(rs.STAGE_SECTIONS["refine_map"]) >= 2


def test_stage_sections_vocabulary():
    # Sections are page panels, never wizard steps: a key carrying sections must
    # be a real stage and must NOT also carry sub-steps, or the strip would
    # render two chip rows and stage_for_wizard_step would lie.
    assert set(rs.STAGE_SECTIONS) <= set(rs.STAGE_KEYS)
    assert set(rs.STAGE_SECTIONS).isdisjoint(rs.STAGE_SUBSTEPS)
    for key, secs in rs.STAGE_SECTIONS.items():
        values = [v for v, _ in secs]
        assert len(values) == len(set(values)), key
        assert all(label.strip() for _, label in secs), key
    # A sectioned stage is a page stage: it lands with no wizard step.
    for key in rs.STAGE_SECTIONS:
        nav, step = rs.stage_landing(key)
        assert step is None, key
    assert rs.stage_landing("refine_map") == ("data", None)


def test_every_stage_the_strip_can_mark_current_is_known():
    for view in ("new", "wizard", "workspace", "landing"):
        key = rs.current_stage("data", view, 1)
        assert key in rs.STAGE_LABELS, (view, key)


def test_readiness_coverage_item_blocks_on_an_undocumented_gap():
    """Mirrors the publish gate so the shortfall shows in the checklist, not only
    as a failed publish."""
    base = {"has_region": True, "has_screening": True, "n_retained": 12,
            "enriched": True, "mapping_confirmed": True, "curve_review": {}}
    cr = dict(_review_map())
    cr["mC"] = rs.apply_review_decision(cr["mC"], rs.DECISION_REMOVED)
    base["curve_review"] = cr

    ok = dict(base, coverage={"total": 20, "covered": 20, "excluded": 0, "missing": 0})
    assert rs.is_ready_to_publish(ok)

    gap = dict(base, coverage={"total": 20, "covered": 12, "excluded": 0, "missing": 8})
    assert not rs.is_ready_to_publish(gap)
    item = next(i for i in rs.readiness_checklist(gap) if i["key"] == "coverage")
    assert item["ok"] is False

    documented = dict(base, coverage={"total": 20, "covered": 12, "excluded": 8, "missing": 0})
    assert rs.is_ready_to_publish(documented)


def test_readiness_coverage_is_ok_before_a_bundle_can_be_built():
    """A run with nothing to judge yet must not report a fake shortfall."""
    base = {"has_region": True, "has_screening": True, "n_retained": 12,
            "enriched": True}
    cr = dict(_review_map())
    cr["mC"] = rs.apply_review_decision(cr["mC"], rs.DECISION_REMOVED)
    base["curve_review"] = cr
    item = next(i for i in rs.readiness_checklist(base) if i["key"] == "coverage")
    assert item["ok"] is True


def test_derive_stage_status_missing_diagnostics_is_attention():
    """The agent stamped this stage done while every analysis tab reported that
    nothing had run. The stage owns the stratifier screening, so a build with no
    diagnostics is attention, not done."""
    base = {"has_region": True, "has_screening": True, "n_retained": 5,
            "enriched": True, "n_enriched": 5}
    out = rs.derive_stage_status(dict(base, n_missing_diagnostics=4))
    assert out["enrichment_build"]["status"] == rs.STAGE_ATTENTION
    assert "4" in out["enrichment_build"]["detail"]

    out = rs.derive_stage_status(dict(base, n_missing_diagnostics=0))
    assert out["enrichment_build"]["status"] == rs.STAGE_DONE


def test_missing_diagnostics_does_not_block_later_stages():
    """derive_stage_status gates stages 4 to 6 on `enriched`, so the attention
    state must not be read as "no dataset" or a complete assessment would show
    "Build a dataset first"."""
    out = rs.derive_stage_status({
        "has_region": True, "has_screening": True, "n_retained": 5,
        "enriched": True, "n_missing_diagnostics": 2,
        "n_unmapped_functions": 0, "mapping_confirmed": True,
    })
    assert out["enrichment_build"]["status"] == rs.STAGE_ATTENTION
    assert out["refine_map"]["status"] == rs.STAGE_DONE


def test_redundancy_is_a_refine_map_section():
    sections = dict(rs.STAGE_SECTIONS["refine_map"])
    assert sections["redundancy"] == "Metric redundancy"
    # It sits beside function mapping, where a flagged pair is actionable.
    keys = [k for k, _ in rs.STAGE_SECTIONS["refine_map"]]
    assert keys.index("redundancy") == keys.index("mapping") + 1


def test_curve_review_sections_are_gallery_then_table():
    # The Reference Curves page lands on the gallery and keeps the table beside it.
    assert [v for v, _ in rs.STAGE_SECTIONS["curve_review"]] == ["gallery", "table"]
    assert rs.stage_landing("curve_review") == ("curves", None)


def test_section_values_are_unique_across_stages():
    # The strip registers one chip input per section value and the section
    # request channel is shared, so a value may belong to one stage only.
    values = [v for secs in rs.STAGE_SECTIONS.values() for v, _ in secs]
    assert len(values) == len(set(values))

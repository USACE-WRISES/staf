"""Select final curves is the one decision authority (campaign Round 1, 2026-09-25).

Every decision a person takes on a version is taken on this section and lands in the
record it always had: REF-15 curve decisions and their undo (the source panel's one
form), CURVE-07 answers (the session's curve review plus the region's
``owner_decisions.json``), the other build items the queue left open (the same file),
documented gaps (``function_coverage_exceptions`` plus ``coverage_exceptions.json``) and
SELECT-01 approvals (the session's ``portfolio_approvals``, written into the publish
meta). The Gallery, the Table, the Function mapping page and the Region builder show
these decisions and no longer take them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import candidates as C
from streamcurves import owner_curves as oc
from streamcurves import region_build as rb
from streamcurves import run_state as rs
from views import final_selection as fs
from views import state as st
from views.state import AppState

VIEWS = Path(__file__).resolve().parents[1] / "views"
# a module namespace as the summary page's session gives it (a ResolvedId, which
# Shiny's input builders accept with its hyphen; a plain string would be refused)
NS = __import__("shiny._namespaces", fromlist=["ResolvedId"]).ResolvedId("summary")


def _html(tag) -> str:
    return str(tag).replace("&apos;", "'").replace("&quot;", '"')


def _cand(key: str, metric: str, kind: str, label: str = "", *, added_by=None):
    c = {"candidateKey": key, "identity": {"sourceKind": kind, "subject": {"id": metric}},
         "label": label or metric, "basisDigest": "b", "buildStatus": "built",
         "eligibility": {"status": "eligible", "reasons": [], "checks": []},
         "tile": {"metric": metric, "display_name": label or metric, "units": None,
                  "strata": [{"label": None, "points": [(0.0, 0.0), (1.0, 1.0)]}],
                  "reference_range": (None, None), "domain": None, "badge": kind}}
    if added_by:
        c["addedBy"] = added_by
    return c


def _row(key: str, fid: str, status: str, *, rule: str, verb: str = "selected", ref=None,
         unresolved: str = ""):
    return {"candidateKey": key, "functionId": fid, "status": status, "unresolved": unresolved,
            "needsReview": False,
            "decision": {"candidateKey": key, "functionId": fid, "decision": verb, "rule": rule,
                         "reason": "why", "decidedBy": C.AUTOMATED, "who": None, "when": None,
                         "basisDigest": None, "decisionRef": ref}}


FID = "water-soil-quality"


# --------------------------------------------------------------------------- #
# the row buttons: CURVE-07 answers and REF-15 on a curve from another source
# --------------------------------------------------------------------------- #
def test_a_curve_held_for_review_offers_accept_and_remove_on_its_row():
    cand = _cand("k1", "phab_PCT_FAST", "fitted", "Fast water")
    row = _row("k1", FID, C.NOT_EVALUATED, rule="CURVE-07", verb="pending", unresolved="review")
    html = _html(fs.alternative_row_ui(row, cand, ns=NS, compare=[], extension_on=False))
    assert " Accept" in html and " Remove" in html
    assert '"action": "review"' in html and '"decision": "accept"' in html and '"decision": "remove"' in html
    assert "summary-final_action" in html
    # the register's own words for the rule stay
    assert fs.rule_words("CURVE-07") in html


def test_a_curve_from_another_source_offers_remove_and_take_out_through_the_one_form():
    """REF-15's form and undo (views.source_panel) are the one handler: the row posts to
    the panel's act input, exactly as the Table's Remove and the mapping chip's x did."""
    from views import source_panel as sp
    cand = _cand("k2", "chem_PTL", "carried", "Total phosphorus")
    row = _row("k2", FID, C.SELECTED, rule="REF-05")
    html = _html(fs.alternative_row_ui(row, cand, ns=NS, compare=[], extension_on=False))
    assert " Remove" in html and " Take out of this function" in html
    assert sp.ACT_INPUT in html
    assert f'"metric": "chem_PTL", "action": "{oc.REMOVE}"' in html
    assert f'"metric": "chem_PTL", "action": "{oc.UNMAP}", "functions": ["{FID}"]' in html
    # a fixed criterion can be removed too; a curve built here cannot (its review decides)
    fixed = _cand("k3", "pctimp2019ws", "fixed", "Impervious cover")
    html = _html(fs.alternative_row_ui(_row("k3", FID, C.SELECTED, rule="CURVE-11"), fixed,
                                       ns=NS, compare=[], extension_on=False))
    assert f'"action": "{oc.REMOVE}"' in html
    fitted = _cand("k4", "chem_COND", "fitted", "Conductivity")
    html = _html(fs.alternative_row_ui(_row("k4", FID, C.SELECTED, rule="SELECT-04"), fitted,
                                       ns=NS, compare=[], extension_on=False))
    assert " Remove" not in html and " Take out of this function" not in html
    # a removed curve keeps its Undo (REF-15's own undo), and nothing else
    removed = _html(fs.alternative_row_ui(
        _row("k2", FID, C.ELIGIBLE, rule="REF-15", verb="not_selected", ref="cd-0000000001"), cand,
        ns=NS, compare=[], extension_on=False))
    assert " Undo" in removed and sp.UNDO_INPUT in removed and " Take out of this function" not in removed


# --------------------------------------------------------------------------- #
# the function row: SELECT-01 approval, the gap, the source
# --------------------------------------------------------------------------- #
def _fn(n_selected: int, *, gap=None, unassessed=False):
    sel = [_row(f"s{i}", FID, C.SELECTED, rule="SELECT-04") for i in range(n_selected)]
    return {"functionId": FID, "functionName": "Water and soil quality", "discipline": "Physicochemistry",
            "selected": [] if unassessed else sel, "alternatives": [], "gap": gap,
            "unassessed": unassessed, "unresolved": 0, "waiting": [], "stale": []}


def test_select01_status_reads_the_sessions_and_the_builds_approvals():
    assert fs.select01_status(_fn(2), limit=2) is None
    s = fs.select01_status(_fn(3), limit=2)
    assert s == {"n": 3, "limit": 2, "approved": False, "by": "", "at_build": False, "note": "", "date": ""}
    mine = fs.select01_status(_fn(3), limit=2, approvals=[{"functionId": FID, "approver": "GM", "note": "ok",
                                                            "date": "2026-09-25"}])
    assert mine["approved"] and mine["by"] == "GM" and not mine["at_build"]
    built = fs.select01_status(_fn(3), limit=2, origin_approvals=[{"functionId": FID, "approvedBy": "owner"}])
    assert built["approved"] and built["at_build"]
    # a blank name approves nothing
    blank = fs.select01_status(_fn(3), limit=2, approvals=[{"functionId": FID, "approver": " "}])
    assert not blank["approved"]


def test_a_function_over_the_maximum_asks_for_its_approval_on_its_row():
    cands = {f"s{i}": _cand(f"s{i}", f"m{i}", "fitted") for i in range(3)}
    html = _html(fs.function_row_ui(_fn(3), cands, ns=NS, compare=[], extension_on=False,
                                    select01=fs.select01_status(_fn(3), limit=2)))
    assert "3 metrics: approve as a complementary set" in html
    assert " Approve as a complementary set" in html and '"action": "approve"' in html
    assert "SELECT-01" in html
    approved = _html(fs.function_row_ui(_fn(3), cands, ns=NS, compare=[], extension_on=False,
                                        select01=fs.select01_status(
                                            _fn(3), limit=2,
                                            approvals=[{"functionId": FID, "approver": "GM"}])))
    assert "3 metrics, approved by GM" in approved and " Withdraw the approval" in approved
    assert '"action": "unapprove"' in approved
    at_build = _html(fs.function_row_ui(_fn(3), cands, ns=NS, compare=[], extension_on=False,
                                        select01=fs.select01_status(
                                            _fn(3), limit=2,
                                            origin_approvals=[{"functionId": FID, "approvedBy": "owner"}])))
    assert "approved by owner" in at_build and "Withdraw the approval" not in at_build
    # under the maximum nothing is asked
    two = _html(fs.function_row_ui(_fn(2), cands, ns=NS, compare=[], extension_on=False, select01=None))
    assert "complementary" not in two


def test_an_unassessed_function_offers_a_gap_and_a_source_and_shows_the_gap_recorded():
    html = _html(fs.function_row_ui(_fn(0, unassessed=True), {}, ns=NS, compare=[], extension_on=False,
                                    can_source=True))
    assert " Document a gap" in html and '"action": "gap"' in html
    assert " Choose a source" in html and "source_dialog-open" in html
    gap = {"functionId": FID, "reason": "no-suitable-metric", "justification": "No metric informs it here.",
           "recordedBy": "GM"}
    own = _html(fs.function_row_ui(_fn(0, unassessed=True, gap=gap), {}, ns=NS, compare=[],
                                   extension_on=False, gap_withdrawable=True))
    assert "Why it is unassessed" in own and "No metric informs it here." in own
    assert "Recorded by GM" in own and " Withdraw the gap" in own and '"action": "withdraw_gap"' in own
    assert " Document a gap" not in own
    carried = _html(fs.function_row_ui(_fn(0, unassessed=True, gap=gap), {}, ns=NS, compare=[],
                                       extension_on=False, gap_withdrawable=False))
    assert "Withdraw the gap" not in carried     # a gap a curve decision carries goes with its undo
    # no source can be chosen for a legacy session
    assert "Choose a source" not in _html(fs.function_row_ui(_fn(0, unassessed=True), {}, ns=NS, compare=[],
                                                              extension_on=False, can_source=False))


def test_every_id_the_section_renders_is_namespaced_without_hyphens():
    """Shiny module ids take letters, digits and underscores after the prefix."""
    cands = {f"s{i}": _cand(f"s{i}", f"m{i}", "fitted") for i in range(3)}
    register = {"candidates": list(cands.values()),
                "rows": [_row(f"s{i}", FID, C.SELECTED, rule="SELECT-04") for i in range(3)],
                "functions": [_fn(3)]}
    def own(text: str) -> list[str]:
        # the ids this module names (Shiny adds its own "-label" twin to a labelled input)
        return [i.split("summary-", 1)[-1].removesuffix("-label")
                for i in re.findall(r'id="(summary-[^"]+)"', text)]

    html = _html(fs.final_selection_ui(register, ns=NS, open_ids=[FID], limit=2, can_source=True))
    ids = own(html)
    assert ids and all("-" not in i and " " not in i for i in ids)
    for dialog in (fs.review_modal("Fast water", "accept", ns=NS),
                   fs.gap_modal("Water and soil quality", ns=NS, by="GM", reasons=rb.coverage_reasons()),
                   fs.approval_modal("Water and soil quality", 3, 2, ns=NS, by="GM")):
        text = _html(dialog)
        ids = own(text)
        assert ids and all("-" not in i for i in ids)
        assert chr(8212) not in text
    assert "fs_review_note" in _html(fs.review_modal("x", "remove", ns=NS))
    assert "fs_gap_why" in _html(fs.gap_modal("x", ns=NS, by="GM", reasons=["no-suitable-metric"]))
    assert "fs_approve_by" in _html(fs.approval_modal("x", 3, 2, ns=NS, by="GM"))


# --------------------------------------------------------------------------- #
# the build items left for you
# --------------------------------------------------------------------------- #
DOC = {"records": [
    {"rule_id": "REF-02", "subject": "reference_screen",
     "computed": {"reference_tier": "best_available", "n_retained": 23}},
    {"rule_id": "RED-01", "subject": "a|b", "computed": {"rho": 0.91}},
    {"rule_id": "CURVE-07", "subject": "phab_PCT_FAST",
     "computed": {"curve_status": "degenerate", "reviewer_decision": "pending"}},
], "reviewQueue": {"items": [
    {"item_id": "REF-02:reference_screen", "rule_ids": ["REF-02"], "subject": "reference_screen",
     "trigger": "reference_tier_fallback", "blocking": True, "status": "open",
     "question": "Accept the best-available tier for this region (REF-02)?",
     "evidence": {"reference_tier": "best_available", "n_retained": 23}},
    {"item_id": "RED-01:a|b", "rule_ids": ["RED-01"], "subject": "a|b", "trigger": "strong_pair",
     "blocking": False, "status": "open", "question": "Keep which one?", "evidence": {"rho": 0.91}},
    {"item_id": "CURVE-07:phab_PCT_FAST", "rule_ids": ["CURVE-07"], "subject": "phab_PCT_FAST",
     "trigger": "curve_needs_review", "blocking": False, "status": "open",
     "question": "Accept, adjust or drop?", "evidence": {"curve_status": "degenerate"}},
    {"item_id": "STRAT-09:DA", "rule_ids": ["STRAT-09"], "subject": "DA", "trigger": "x",
     "blocking": False, "status": "resolved", "question": "Split?", "evidence": {}},
    {"item_id": f"SELECT-01:{FID}", "rule_ids": ["SELECT-01"], "subject": FID,
     "trigger": "more_than_two_metrics", "blocking": False, "status": "open",
     "question": "Approve or trim?", "evidence": {"n_metrics": 3}},
]}}


def test_the_build_items_list_leaves_held_curves_to_their_rows_and_prefills_answers():
    items = fs.build_items(DOC, answered=[{"rule_id": "RED-01", "subject": "a|b", "action": "accept",
                                           "rationale": "Keep both; they measure different things."}])
    assert [i["item_id"] for i in items] == ["REF-02:reference_screen", "RED-01:a|b"]
    assert items[0]["answer"] is None and items[1]["answer"]["action"] == "accept"
    html = _html(fs.build_items_ui(items, ns=NS, writable=True))
    assert "Build items left for you (2)" in html and "Save answers" in html
    assert "summary-fs_act_0" in html and "summary-fs_why_1" in html and "summary-fs_save_answers" in html
    assert "Answered: accept" in html and "Keep both; they measure different things." in html
    assert "BLOCKING" in html and "REF-02" in html
    # every decision the rule offers is the batch runner's vocabulary
    for action in rb.REVIEWER_ACTIONS:
        assert f'value="{action}"' in html
    read_only = _html(fs.build_items_ui(items, ns=NS, writable=False))
    assert "Save answers" not in read_only and "cannot be saved here" in read_only
    assert fs.build_items(None) == [] and fs.build_items_ui([], ns=NS, writable=True) is None
    # one home per decision: a held curve's row answers CURVE-07, a function row's
    # approval answers SELECT-01
    assert fs.ROW_ANSWERED_RULES == ("CURVE-07", "SELECT-01")
    assert [i["rule_id"] for i in rb.open_queue_items(DOC)] == ["REF-02", "RED-01", "CURVE-07", "SELECT-01"]


def test_an_answer_built_from_a_build_item_is_the_one_the_next_build_reads(tmp_path):
    """What the Save button does, without a Shiny session: build_decision over the
    build's record, decision_problems, save_answer into the region's file."""
    items = fs.build_items(DOC)
    d = rb.build_decision(DOC, items[0], "accept", "Best available accepted for this region.", reviewer="GM")
    assert rb.decision_problems(DOC, d) == []
    rb.save_answer(tmp_path, d)
    saved = json.loads((tmp_path / rb.OWNER_DECISIONS_FILE).read_text(encoding="utf-8"))
    assert saved[0]["rule_id"] == "REF-02" and saved[0]["reviewer"] == "GM"
    assert saved[0]["asserts"] == {"reference_tier": "best_available", "n_retained": 23}
    # a CURVE-07 answer from the row lands in the same file, in the same shape
    held = next(i for i in rb.open_queue_items(DOC) if i["rule_id"] == "CURVE-07")
    a = rb.build_decision(DOC, held, "reject", "Degenerate here; the metric goes.", reviewer="GM")
    assert "reviewer_decision" not in a["asserts"]
    rb.save_answer(tmp_path, a)
    assert [x["rule_id"] for x in rb.read_answers(tmp_path)] == ["REF-02", "CURVE-07"]


# --------------------------------------------------------------------------- #
# what the publish page reads from the section
# --------------------------------------------------------------------------- #
def test_the_publish_page_reads_unresolved_items_and_unapproved_functions_from_the_register():
    state = AppState.fresh()
    register = {"candidates": [], "rows": [], "functions": [
        {**_fn(3), "unresolved": 2},
        {**_fn(1), "functionId": "carbon-processing", "functionName": "Carbon processing"},
    ]}
    assert fs.unresolved_count(state, register) == 2
    assert [u["functionId"] for u in fs.unapproved_functions(state, register, limit=2)] == [FID]
    st.add_portfolio_approval(state, FID, approver="GM", note="Complementary.")
    assert fs.unapproved_functions(state, register, limit=2) == []
    state.portfolio_approvals.set(None)
    state.assessment_source.set({"kind": "staged", "portfolio_approvals": [
        {"functionId": FID, "approvedBy": "owner"}]})
    assert fs.unapproved_functions(state, register, limit=2) == []
    # a legacy session (no reference build) has no register to read
    assert fs.unresolved_count(state) is None and fs.unapproved_functions(state) == []


def test_the_section_is_the_one_home_of_every_decision():
    """Source pins: the handlers write the records they always had, and the other pages
    no longer take the decisions."""
    src = (VIEWS / "final_selection.py").read_text(encoding="utf-8")
    review = src[src.index("def _confirm_review():"):src.index("def _confirm_gap():")]
    assert "ca.set_review_decision(state, metric, decision, note=note" in review
    assert '_record_answer(item, "accept" if accepting else "reject", note)' in review
    record = src[src.index("def _record_answer("):src.index("def _confirm_review():")]
    assert "rb.build_decision(doc, item, action, rationale" in record
    assert "rb.decision_problems(doc, decision)" in record and "rb.save_answer(run_dir, decision)" in record
    gap = src[src.index("def _confirm_gap():"):src.index("def _confirm_approval():")]
    assert "rb.build_coverage_exception(" in gap and "rb.coverage_problems([exc])" in gap
    assert "state.function_coverage_exceptions.set(" in gap and "rb.save_gap(run_dir, exc)" in gap
    approval = src[src.index("def _confirm_approval():"):src.index("def _save_answers():")]
    assert "st.add_portfolio_approval(state, p[\"fid\"]" in approval
    answers = src[src.index("def _save_answers():"):src.index("def fs_sqt_results():")]
    assert "_record_answer(item, str(action), str(why or \"\"))" in answers
    # the other pages show, and no longer take
    summary = (VIEWS / "summary_page.py").read_text(encoding="utf-8")
    assert "Decide in Select final curves" in summary
    for gone in ("def _review_confirm", "pending_review", "sp.act_onclick(metric, \"remove\")",
                 "sp.undo_onclick(t[\"removed_decision\"])", "Add a source", "sd.open_onclick"):
        assert gone not in summary, gone
    mapping = (VIEWS / "discipline_map.py").read_text(encoding="utf-8")
    assert "exc_save" not in mapping and "_sp.act_onclick" not in mapping
    builder = (VIEWS / "region_builder.py").read_text(encoding="utf-8")
    assert "def _open_items(" not in builder and "def _undo_decision" not in builder
    # the review vocabulary the row buttons rely on
    assert "rs.DECISION_FINALIZED if accepting else rs.DECISION_REMOVED" in src

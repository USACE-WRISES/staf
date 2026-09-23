"""The candidate register: a projection over the records a DEEP session keeps.

The register never decides anything: SELECT-04, the withheld list, the review and the
owner's REF-15 decisions say what a version scores, and every candidate and function gets
its status from them. What the register adds (curves considered for comparison and the
reasons a person gave for not selecting one) rides in the session and survives a save and
reopen. On every published version checked here, the candidates the register calls
selected are exactly what the version's bundle scores.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from streamcurves import candidates as C
from streamcurves import owner_curves as oc
from streamcurves import pressure_evidence as pe
from streamcurves import session_io as sio

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"


def _tile(metric, fids, points=((0, 0), (1, 1)), **extra):
    t = {"metric": metric, "display_name": metric.upper(), "function_id": fids[0],
         "also_function_refs": [{"id": f} for f in fids[1:]],
         "strata": [{"label": None, "points": [list(p) for p in points]}]}
    t.update(extra)
    return t


def _build(**extra):
    b = {"method": pe.METHOD, "portfolioSelection": {}, "insufficientReferenceSupport": []}
    b.update(extra)
    return b


def _decision(metric, action, functions=(), **kw):
    return oc.new_decision(metric, action, rationale="The owner explains this decision here.",
                           recorded_by="Owner", functions=functions,
                           recorded_at="2026-09-23T00:00:00+00:00", **kw)


def _rows(reg):
    return {(r["candidateKey"], r["functionId"]): r for r in reg["rows"]}


def _key_of(reg, metric, kind=None):
    return next(c["candidateKey"] for c in reg["candidates"]
                if c["identity"]["subject"]["id"] == metric
                and (kind is None or c["identity"]["sourceKind"] == kind))


def test_the_key_names_what_a_candidate_is_and_the_basis_what_it_holds():
    ident = {"assessmentType": "deep", "subject": {"kind": "metric", "id": "m"}, "sourceKind": "fitted"}
    assert C.candidate_key(ident) == C.candidate_key(dict(ident)) != C.candidate_key(
        {**ident, "sourceKind": "sqt"})
    a = C.curve_basis_digest([{"label": None, "points": [(0, 0), (1, 1)]}])
    assert a == C.curve_basis_digest([{"label": None, "points": [(0, 1e-15), (1.0000000000001, 1)]}])
    assert a != C.curve_basis_digest([{"label": None, "points": [(0, 0), (2, 1)]}])
    assert a != C.curve_basis_digest([{"label": None, "points": [(0, 0), (1, 1)]}], higher_is_better=False)
    assert C.curve_basis_digest([{"label": None, "points": []}]) is None


def test_the_deep_register_states_every_record_the_session_keeps():
    tiles = [
        _tile("fit_a", ["habitat-provision", "carbon-processing"]),
        _tile("fit_b", ["habitat-provision"]),
        _tile("held", ["community-dynamics"], needs_review=True),
        _tile("gone", ["community-dynamics"], decision="removed_from_scope"),
        _tile("broken", ["population-support"], points=()),
        _tile("carried", ["light-thermal-regime"], read_only=True, source_kind="carried",
              badge="Carried from v3"),
    ]
    build = _build(
        portfolioSelection={"habitat-provision": {"kept": [], "selected": ["fit_a"], "notSelected": [
            {"metric": "fit_b", "source": "local", "score": 0.61, "reserve": False}]}},
        insufficientReferenceSupport=[{"metricKey": "withheld", "metricName": "Withheld",
                                       "functions": [{"functionId": "population-support"}],
                                       "reason": "insufficient-reference-support",
                                       "statement": "No defensible reference pool."}])
    reg = C.deep_register(tiles=tiles, build=build, region={"code": "50"})
    rows = _rows(reg)
    k = lambda m: _key_of(reg, m)  # noqa: E731
    assert rows[(k("fit_a"), "habitat-provision")]["status"] == C.SELECTED
    assert rows[(k("fit_a"), "carbon-processing")]["status"] == C.SELECTED
    b = rows[(k("fit_b"), "habitat-provision")]
    assert b["status"] == C.ELIGIBLE and b["decision"]["rule"] == "SELECT-04"
    assert b["decision"]["decidedBy"] == C.AUTOMATED and "0.61" in b["decision"]["reason"]
    assert rows[(k("held"), "community-dynamics")]["status"] == C.NOT_EVALUATED
    assert rows[(k("held"), "community-dynamics")]["unresolved"] == "review"
    assert rows[(k("gone"), "community-dynamics")]["status"] == C.EXCLUDED
    assert rows[(k("broken"), "population-support")]["status"] == C.FAILED
    w = rows[(k("withheld"), "population-support")]
    assert w["status"] == C.EXCLUDED and w["decision"]["rule"] == "REF-06"
    assert rows[(k("carried"), "light-thermal-regime")]["decision"]["rule"] == "SELECT-04"
    fns = {f["functionId"]: f for f in reg["functions"]}
    assert len(fns) == 20
    assert [r["candidateKey"] for r in fns["habitat-provision"]["selected"]] == [k("fit_a")]
    assert fns["community-dynamics"]["unassessed"] and fns["community-dynamics"]["unresolved"] == 1
    counts = C.register_counts(reg)
    assert counts["selected"] == 3 and counts["eligible_not_selected"] == 1


def test_the_owners_decisions_read_as_a_persons():
    tiles = [_tile("fit_b", ["habitat-provision"]),
             _tile("carried", ["light-thermal-regime"], read_only=True, source_kind="carried")]
    build = _build(portfolioSelection={"habitat-provision": {"kept": [], "selected": [], "notSelected": [
        {"metric": "fit_b", "source": "local", "score": 0.5, "reserve": False}]}},
        carriedMetrics={"carried": {}})
    include = _decision("fit_b", oc.INCLUDE, ["habitat-provision"])
    reg = C.deep_register(tiles=tiles, build=build, decisions=[include])
    row = _rows(reg)[(_key_of(reg, "fit_b"), "habitat-provision")]
    assert row["status"] == C.SELECTED and row["decision"]["decidedBy"] == C.PERSON
    assert row["decision"]["who"] == "Owner" and row["decision"]["decisionRef"] == include["id"]
    removed = _decision("carried", oc.REMOVE)
    tiles[1]["removed_decision"] = removed["id"]
    reg = C.deep_register(tiles=tiles, build=build, decisions=[removed])
    row = _rows(reg)[(_key_of(reg, "carried"), "light-thermal-regime")]
    assert row["status"] == C.ELIGIBLE and row["decision"]["rule"] == "REF-15"


def test_a_considered_candidate_keeps_its_dispositions_and_refuses_what_it_cannot_hold():
    sqt = {"candidateKey": "cand-000000000001", "label": "MNSQT riffle embeddedness",
           "identity": {"assessmentType": "deep", "subject": {"kind": "metric", "id": "sqt_mn_x"},
                        "sourceKind": "sqt", "sourceRef": {"recordId": "mn-1"},
                        "functions": ["bed-composition-bedform-dynamics"]},
           "basisDigest": "sha256:aa", "eligibility": {"status": "eligible", "reasons": []}}
    with pytest.raises(ValueError, match="name"):
        C.add_considered(None, sqt, by="")
    reg = C.add_considered(None, sqt, by="Reviewer", at="2026-09-23T00:00:00Z")
    view = C.deep_register(tiles=[], build=_build(), register=reg)
    row = _rows(view)[(sqt["candidateKey"], "bed-composition-bedform-dynamics")]
    assert row["status"] == C.NOT_EVALUATED and row["unresolved"] == "undecided"
    with pytest.raises(ValueError, match="at least"):
        C.record_disposition(reg, sqt["candidateKey"], "bed-composition-bedform-dynamics",
                             by="Reviewer", reason="too short")
    reg = C.record_disposition(reg, sqt["candidateKey"], "bed-composition-bedform-dynamics",
                               by="Reviewer", reason="Field protocol differs from what DEEP users measure.",
                               basis_digest="sha256:aa", at="2026-09-23T00:00:00Z")
    view = C.deep_register(tiles=[], build=_build(), register=reg)
    row = _rows(view)[(sqt["candidateKey"], "bed-composition-bedform-dynamics")]
    assert row["status"] == C.ELIGIBLE and row["decision"]["who"] == "Reviewer"
    assert not row["needsReview"]
    # the curve behind it changed: the disposition asks to be looked at again
    changed = C.add_considered(reg, {**sqt, "basisDigest": "sha256:bb"}, by="Reviewer")
    view = C.deep_register(tiles=[], build=_build(), register=changed)
    assert _rows(view)[(sqt["candidateKey"], "bed-composition-bedform-dynamics")]["needsReview"]
    undone = C.withdraw_disposition(reg, reg["decisions"][-1]["decisionId"])
    assert not undone["decisions"]
    with pytest.raises(ValueError, match="newer StreamCurves"):
        C.load_register({"schema": 99})


def test_the_register_exports_and_diffs_without_curve_data():
    tiles = [_tile("fit_a", ["habitat-provision"]), _tile("fit_b", ["habitat-provision"])]
    sel = {"habitat-provision": {"kept": [], "selected": ["fit_a"], "notSelected": [
        {"metric": "fit_b", "source": "local", "score": 0.5, "reserve": False}]}}
    before = C.export_rows(C.deep_register(tiles=tiles, build=_build(portfolioSelection=sel)))
    assert {r["status"] for r in before} == {C.SELECTED, C.ELIGIBLE}
    assert all("points" not in json.dumps(r) for r in before)
    include = _decision("fit_b", oc.INCLUDE, ["habitat-provision"])
    after = C.export_rows(C.deep_register(tiles=tiles, build=_build(portfolioSelection=sel),
                                          decisions=[include]))
    changes = C.diff(before, after)
    assert [c["change"] for c in changes] == ["changed"]
    assert changes[0]["after"]["status"] == C.SELECTED and "decidedBy" in changes[0]["fields"]


def test_the_register_rides_in_the_session_through_a_save_and_reopen():
    assert C.SESSION_FIELD in sio.SESSION_FIELDS
    reg = C.add_considered(None, {"candidateKey": "cand-000000000002", "identity": {}},
                           by="Reviewer", at="2026-09-23T00:00:00Z")
    payload = sio.dump_session_fields({C.SESSION_FIELD: reg}, session_name="t")
    back = sio.decode_session_fields(json.loads(json.dumps(payload)))
    assert C.load_register(back[C.SESSION_FIELD]) == reg


@pytest.mark.parametrize("aid,version,code", [("eastern-corn-belt-plains", 8, "55"),
                                              ("southeastern-plains", 2, "65")])
def test_a_published_version_selects_exactly_what_its_bundle_scores(aid, version, code, monkeypatch):
    """The register over a real published session agrees with the bundle, pair for pair."""
    vdir = LIBRARY / aid / f"v{version}"
    if not (vdir / "session.streamcurves.json").is_file():
        pytest.skip(f"{aid} v{version} is not in this library")
    from shiny import reactive, ui
    from streamcurves.deep_export import deep_slug
    from views import curve_gallery as cg
    from views import data_overview as do
    from views.state import AppState
    monkeypatch.setattr(ui, "notification_show", lambda *a, **k: None)
    state = AppState.fresh()
    with reactive.isolate():
        do.restore_session(state, sio.load_session_payload(
            (vdir / "session.streamcurves.json").read_text(encoding="utf-8")), "t")
        tiles = cg.gallery_rows(state, include_reference=True)
        reg = C.deep_register(tiles=tiles, build=state.reference_build(),
                              decisions=state.owner_curve_decisions() or [],
                              metric_config=state.metric_config() or {},
                              register=state.candidate_register(), region={"code": code})
    cands = {c["candidateKey"]: c for c in reg["candidates"]}
    mine = {("spring-" + deep_slug(cands[r["candidateKey"]]["identity"]["subject"]["id"]),
             r["functionId"]) for r in reg["rows"] if r["status"] == C.SELECTED}
    bundle = json.loads((vdir / "assessment.deep.json").read_text(encoding="utf-8"))
    theirs = {(m["metricId"], f["functionId"]) for f in bundle["metricsByFunction"]
              for m in f.get("metrics") or []}
    assert mine == theirs
    covered = {f["functionId"] for f in reg["functions"] if f["selected"]}
    assert covered == {f["functionId"] for f in bundle["metricsByFunction"] if f.get("metrics")}


# --------------------------------------------------------------------------- #
# a published state SQT curve as a candidate, and the section that shows it
# --------------------------------------------------------------------------- #
MN_CANOPY = "sqt:mn:canopy-cover:woody-vegetation-is-a-natural-component-of-riparian-zone"
NLF = {"function": "light-thermal-regime", "states": ["MI", "MN", "WI"], "scoreScale": "staf"}


def _sqt():
    from streamcurves import sqt_registry
    if not sqt_registry.available():
        pytest.skip("the SQT registry is not built")
    return sqt_registry


def test_an_sqt_candidate_is_frozen_checked_and_refused_when_defective():
    from streamcurves import owner_sources
    reg = _sqt()
    cand = C.sqt_candidate(reg.record(MN_CANOPY), function_id="light-thermal-regime", context=NLF,
                           region={"code": "50"})
    assert cand["identity"]["sourceKind"] == "sqt" and cand["eligibility"]["status"] == "eligible"
    assert cand["verification"] == "verified" and reg.frozen_intact(cand["record"])
    assert {c["id"] for c in cand["eligibility"]["checks"]} >= {"geography", "score-scale", "protocol"}
    src = owner_sources.sqt_source(cand)
    assert src["kind"] == "sqt" and src["curve"]["annotations"]["basisLabel"] == "State SQT"
    assert src["curve"]["annotations"]["basis"] == "published-benchmark"
    assert [p["x"] for p in src["curve"]["points"]] == [p["x"] for p in cand["record"]["normalizedPoints"]]
    assert "0.39 and 0.69" in src["curve"]["annotations"]["basisStatement"]
    elsewhere = C.sqt_candidate(reg.record(MN_CANOPY), function_id="light-thermal-regime",
                                context={**NLF, "states": ["IN", "OH"]}, region={"code": "55"})
    assert elsewhere["eligibility"]["status"] == "excluded"
    with pytest.raises(ValueError, match="excluded"):
        owner_sources.sqt_source(elsewhere)
    defect = next(r for r in reg.load()["records"] if not r["eligible"])
    assert C.sqt_candidate(defect, function_id=defect["function"]["id"])["eligibility"]["status"] == "excluded"


def _html(tag) -> str:
    return str(tag)


def test_the_section_shows_selections_alternatives_and_what_a_person_can_do():
    from views import final_selection as fs
    reg = _sqt()
    cand = C.sqt_candidate(reg.record(MN_CANOPY), function_id="light-thermal-regime", context=NLF,
                           region={"code": "50"})
    tiles = [_tile("fit_a", ["light-thermal-regime"]), _tile("fit_b", ["light-thermal-regime"])]
    build = _build(portfolioSelection={"light-thermal-regime": {"kept": [], "selected": ["fit_a"], "notSelected": [
        {"metric": "fit_b", "source": "local", "score": 0.5, "reserve": False}]}})
    register = C.deep_register(tiles=tiles, build=build, register=C.add_considered(None, cand, by="Reviewer"))
    ns = lambda x: f"summary-{x}"  # noqa: E731
    keys = [c["candidateKey"] for c in register["candidates"]][:2]
    html = _html(fs.final_selection_ui(register, ns=ns, compare=keys, extension_on=False,
                                       open_ids=["light-thermal-regime"], sqt_ready=True))
    assert "Select final curves" in html and "Light &amp; thermal regime" in html
    assert "Use in this function" in html and "Record why not" in html
    assert 'disabled="disabled"' in html and oc.EXTENSION_OFF in html
    assert html.count('class="fs-cmp-col"') == 2 and "<svg" in html
    assert all(" " not in i and "-" not in i.split("summary-", 1)[-1]
               for i in __import__("re").findall(r'id="(summary-fs_[^"]+)"', html))
    on = _html(fs.final_selection_ui(register, ns=ns, extension_on=True, sqt_ready=True))
    assert oc.EXTENSION_OFF not in on


def test_the_picker_says_where_a_curve_applies():
    from views import final_selection as fs
    reg = _sqt()
    recs = reg.records(state="MN", function="light-thermal-regime")
    here = _html(fs.picker_results_ui(recs, ns=lambda x: x, function_id="light-thermal-regime", context=NLF))
    assert "Canopy Cover" in here and "Applies" in here and "Add" in here
    away = _html(fs.picker_results_ui(recs, ns=lambda x: x, function_id="light-thermal-regime",
                                      context={**NLF, "states": ["IN", "OH"]}, have=[MN_CANOPY]))
    assert "Does not apply" in away and "Added" in away
    assert fs.crossings([(0, 0), (10, 0.5), (20, 1)]) == pytest.approx([7.8, 13.8])

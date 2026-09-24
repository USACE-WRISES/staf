"""The REF-15 extension of 2026-09-23: a state SQT curve in place of a curve built here.

Off in the canonical configuration until the owner adopts it. While it is off, a decision
that needs it is refused when made and applies nothing when a session already holds one,
so every published version republishes unchanged. With it on, the owner's chosen curve
takes the fitted curve's place in exactly the functions the decision names; the fitted
curve stays built and the register reads it as supported, not selected, under the
decision. The bundle states the owner's decision, never the curve it replaced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from shiny import reactive

from streamcurves import candidates as C
from streamcurves import library as lib
from streamcurves import methodology
from streamcurves import owner_curves as oc
from streamcurves import session_io as sio
from streamcurves.deep_export import deep_slug
from views import assessment_publish as ap
from views import curve_gallery as cg
from views.state import AppState

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
FIELDS = ("completed_metrics", "curve_review", "discipline_function_mapping", "metric_config",
          "region_of_applicability", "function_coverage_exceptions", "predictor_config",
          "reference_build", "session_name")
WHY = "Owner test: the state SQT curve fits what DEEP users measure here."
SQT_KEY = "sqt_mn_test_embeddedness"


def _opened(version: str):
    vdir = LIBRARY / version
    if not (vdir / lib.SESSION_FILE).is_file():
        pytest.skip(f"{version} is not in this checkout")
    bundle = json.loads((vdir / lib.BUNDLE_FILE).read_text(encoding="utf-8"))
    fields = sio.decode_session_fields(sio.load_session_payload(vdir / lib.SESSION_FILE))
    state = AppState.fresh()
    for name in FIELDS:
        getattr(state, name).set(fields.get(name))
    aid, ver = version.split("/v")
    state.assessment_source.set(ap.build_origin(state, kind="library", library_id=aid,
                                                version=int(ver)))
    return state, bundle, fields


def _blocks(bundle) -> dict:
    return {str(b["functionId"]): sorted(str(m["metricId"]) for m in b.get("metrics") or [])
            for b in bundle.get("metricsByFunction") or [] if b.get("metrics")}


def _republish(state, bundle):
    return ap.build_bundle_from_state(state, meta={"sourceCitation": bundle.get("sourceCitation")})


def _sqt_source():
    cfg = {"display_name": "Riffle embeddedness (test SQT)", "units": "%",
           "higher_is_better": False, "column_name": SQT_KEY}
    return {"kind": "sqt", "title": "Test SQT riffle embeddedness", "citation": "A test SQT, v2.0",
            "ref": {"recordId": "mn-test", "state": "MN", "tool": "MNSQT", "edition": "v2.0",
                    "candidateKey": "cand-0000000000aa", "adoptionVersion": oc.SQT_ADOPTION_VERSION},
            "curve": {"displayName": cfg["display_name"], "curveStatus": "complete", "nReference": None,
                      "stratum": "", "layers": [], "config": cfg,
                      "points": [{"x": 0.0, "y": 1.0}, {"x": 50.0, "y": 0.3}, {"x": 100.0, "y": 0.0}],
                      "annotations": {"basisLabel": "State SQT"}}}


def _fitted_single(state):
    """A fitted curve the bundle scores in exactly one function, and that function."""
    with reactive.isolate():
        reg = C.deep_register(tiles=cg.gallery_rows(state, include_reference=False),
                              build=state.reference_build(), metric_config=state.metric_config())
    cands = {c["candidateKey"]: c for c in reg["candidates"]}
    per: dict = {}
    for r in reg["rows"]:
        if r["status"] == C.SELECTED and cands[r["candidateKey"]]["identity"]["sourceKind"] == "fitted":
            per.setdefault(cands[r["candidateKey"]]["identity"]["subject"]["id"], []).append(r["functionId"])
    mk = sorted(m for m, f in per.items() if len(f) == 1)[0]
    return mk, per[mk][0]


def _decision(fitted, fid):
    return oc.new_decision(SQT_KEY, oc.SOURCE, rationale=WHY, recorded_by="Owner", functions=[fid],
                           source=_sqt_source(), replaces=[{"metric": fitted, "functionId": fid}],
                           recorded_at="2026-09-23T00:00:00+00:00")


def test_the_extension_is_off_in_the_canonical_configuration():
    assert methodology.threshold(oc.EXTENSION_FLAG) is False and not oc.alternatives_enabled()
    with pytest.raises(ValueError, match="not enabled"):
        oc.new_decision(SQT_KEY, oc.SOURCE, rationale=WHY, recorded_by="Owner",
                        functions=["habitat-provision"], source=_sqt_source())
    with pytest.raises(ValueError, match="not enabled"):
        oc.new_decision("carried_x", oc.SOURCE, rationale=WHY, recorded_by="Owner",
                        functions=["habitat-provision"], source={**_sqt_source(), "kind": "entered"},
                        replaces=[{"metric": "fit_x", "functionId": "habitat-provision"}])


def test_while_off_a_held_decision_applies_nothing_and_says_why(monkeypatch):
    state, bundle, fields = _opened("eastern-corn-belt-plains/v8")
    fitted, fid = _fitted_single(state)
    plain = _republish(state, bundle)
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    d = _decision(fitted, fid)
    monkeypatch.undo()
    state.owner_curve_decisions.set([d])
    held = _republish(state, bundle)
    # the whole bundle, every field (two republishes of one state are byte for byte the same)
    assert held == plain
    assert lib.content_digest(held) == bundle["contentDigest"]
    stale = oc.stale([d], fields["reference_build"], built=fields["completed_metrics"])
    assert stale and "not enabled" in stale[0][1]


def test_a_choice_made_before_the_adoption_checks_applies_nothing(monkeypatch):
    """Round 2 (2026-09-24): an SQT choice records the adoption it was made under. One saved
    before (its curve never rebuilt from the frozen record, an open end never completed) is
    listed as stale with the extension on, applies nothing, and cannot be made again."""
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    state, bundle, fields = _opened("eastern-corn-belt-plains/v8")
    fitted, fid = _fitted_single(state)
    plain = _republish(state, bundle)
    d = _decision(fitted, fid)
    old = json.loads(json.dumps(d))
    old["source"]["ref"].pop("adoptionVersion")          # as a session saved before round 2 holds it
    assert oc.usable(d) and not oc.usable(old)
    with pytest.raises(ValueError, match="before the SQT adoption checks"):
        oc.check(old)
    state.owner_curve_decisions.set([old])
    assert _republish(state, bundle) == plain
    stale = oc.stale([old], fields["reference_build"], built=fields["completed_metrics"])
    assert stale and stale[0][1] == oc.PRE_ADOPTION


@pytest.mark.parametrize("version,count", [("southeastern-plains/v2", 1), ("central-great-plains/v1", 5)])
def test_every_published_owner_decision_keeps_applying(version, count):
    """The staleness rule reads state SQT choices only: the six refused sources SEP v2 and
    CGP v1 publish keep applying exactly as they did."""
    _state, _bundle, fields = _opened(version)
    decisions = list(fields.get("owner_curve_decisions") or [])
    assert len(decisions) == count
    assert all(oc.usable(d) and oc.unusable_reason(d) is None for d in decisions)
    assert {(d.get("source") or {}).get("kind") for d in decisions} == {"refused_source"}


def test_the_basis_a_decision_records_is_the_one_the_register_computes(monkeypatch):
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    state, _bundle, _fields = _opened("eastern-corn-belt-plains/v8")
    fitted, fid = _fitted_single(state)
    d = _decision(fitted, fid)
    basis = cg.metric_basis(state, SQT_KEY, oc.merge([], d))
    assert basis and basis.startswith("sha256:")
    d["basisDigest"] = basis
    state.owner_curve_decisions.set([d])
    with reactive.isolate():
        reg = C.deep_register(tiles=cg.gallery_rows(state, include_reference=True),
                              build=state.reference_build(), decisions=[d],
                              metric_config=state.metric_config())
        tile = next(t for t in cg.gallery_rows(state, [fitted]) if t["metric"] == fitted)
        fitted_basis = cg.metric_basis(state, fitted, [])
        config = (state.metric_config() or {}).get(fitted)
    mine = [r for r in reg["rows"] if (r.get("decision") or {}).get("decisionRef") == d["id"]]
    # the SQT curve it selects and the fitted curve it replaces: neither reads Look again
    assert len(mine) == 2 and not any(r["needsReview"] for r in mine)
    # the same with the considered candidate in the session's register: the decision is still
    # compared with the tile it was made on, never with the candidate's own digest
    considered = {"candidateKey": "cand-0000000000aa", "label": "Test SQT riffle embeddedness",
                  "identity": {"assessmentType": "deep", "subject": {"kind": "metric", "id": SQT_KEY},
                               "sourceKind": "sqt", "functionId": fid},
                  "functions": [fid], "basisDigest": "sha256:" + "0" * 64,
                  "eligibility": {"status": "eligible", "reasons": [], "checks": []}}
    with reactive.isolate():
        reg = C.deep_register(tiles=cg.gallery_rows(state, include_reference=True),
                              build=state.reference_build(), decisions=[d],
                              metric_config=state.metric_config(),
                              register=C.add_considered(None, considered, by="AB"))
    mine = [r for r in reg["rows"] if (r.get("decision") or {}).get("decisionRef") == d["id"]]
    assert len(mine) == 2 and not any(r["needsReview"] for r in mine)
    assert any(r["candidateKey"] == "cand-0000000000aa" for r in mine)
    # a fitted curve's basis is its gallery tile's
    assert fitted_basis == C.tile_basis_digest(tile, config)


def test_with_it_on_an_sqt_curve_takes_the_fitted_curves_place_in_that_function(monkeypatch):
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    state, bundle, fields = _opened("eastern-corn-belt-plains/v8")
    fitted, fid = _fitted_single(state)
    d = _decision(fitted, fid)
    oc.validate(d, build=fields["reference_build"], built=fields["completed_metrics"])
    state.owner_curve_decisions.set([d])
    rebuilt = _republish(state, bundle)
    before, after = _blocks(bundle), _blocks(rebuilt)
    sqt_id, fit_id = "spring-" + deep_slug(SQT_KEY), "spring-" + deep_slug(fitted)
    assert sqt_id in after[fid] and fit_id not in after[fid]
    assert {f for f in set(before) | set(after) if before.get(f) != after.get(f)} == {fid}
    assert all(fit_id not in ids for ids in after.values())     # it served only that function
    # the bundle states the owner's decision, never the curve it replaced
    text = json.dumps(rebuilt)
    assert fitted not in text and "replaces" not in text
    assert [x["metric"] for x in rebuilt["ownerCurveDecisions"]] == [SQT_KEY]
    # the register: the fitted curve stays, supported and not selected, under the decision
    with reactive.isolate():
        reg = C.deep_register(tiles=cg.gallery_rows(state, include_reference=True),
                              build=state.reference_build(), decisions=[d],
                              metric_config=state.metric_config())
    cands = {c["candidateKey"]: c for c in reg["candidates"]}
    by = {(cands[r["candidateKey"]]["identity"]["subject"]["id"], r["functionId"]): r for r in reg["rows"]}
    assert by[(fitted, fid)]["status"] == C.ELIGIBLE
    assert by[(fitted, fid)]["decision"]["rule"] == "REF-15" and by[(fitted, fid)]["decision"]["who"] == "Owner"
    assert by[(SQT_KEY, fid)]["status"] == C.SELECTED
    assert cands[by[(SQT_KEY, fid)]["candidateKey"]]["identity"]["sourceKind"] == "sqt"


def test_a_replaced_curve_must_be_one_built_here(monkeypatch):
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    _state, _bundle, fields = _opened("eastern-corn-belt-plains/v8")
    d = oc.new_decision(SQT_KEY, oc.SOURCE, rationale=WHY, recorded_by="Owner",
                        functions=["habitat-provision"], source=_sqt_source(),
                        replaces=[{"metric": "not_a_metric_here", "functionId": "habitat-provision"}])
    with pytest.raises(ValueError, match="not a curve built here"):
        oc.validate(d, build=fields["reference_build"], built=fields["completed_metrics"])
    with pytest.raises(ValueError, match="one of the decision's functions"):
        oc.new_decision(SQT_KEY, oc.SOURCE, rationale=WHY, recorded_by="Owner",
                        functions=["habitat-provision"], source=_sqt_source(),
                        replaces=[{"metric": "x", "functionId": "carbon-processing"}])


def test_only_a_decision_that_replaces_carries_the_key(monkeypatch):
    plain = oc.new_decision("carried_x", oc.REMOVE, rationale=WHY, recorded_by="Owner")
    assert "replaces" not in plain and "replaces" not in oc.requests([plain])[0]
    monkeypatch.setattr(oc, "alternatives_enabled", lambda: True)
    d = _decision("fit_x", "habitat-provision")
    assert "replaces" in oc.requests([d])[0] and "replaces" not in oc.summary(d)

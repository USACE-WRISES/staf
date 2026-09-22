"""A workspace publish of an opened build states what the build stated (2026-09-22).

Two ways the step 6 publish of a staged or published build fell short of the
build's own bundle:

- The build writes, on every curve it fitted, what only it can know: the
  reference sample and its range, the metric's role, the caveats and the
  confidence. The session kept none of it, so a republish dropped all seven keys
  from every fitted curve (seven curves in Central Great Plains, three in ECBP).
  They now ride again on every curve that is still exactly the one they describe.
- A standing decision can record an approval or a documented gap for the owner
  to confirm. Promote confirms them under the promoting owner; step 6 copied the
  pending marker into the published data. It now asks, and confirms by name.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import decisions as dec
from streamcurves import library as lib
from streamcurves import pressure_evidence as pe
from streamcurves import session_io as sio
from views import assessment_publish as ap
from views.state import AppState

APP = Path(__file__).resolve().parents[1]
LIBRARY = APP.parent / "library" / "assessments"
VERSIONS = ("interior-plateau/v6", "northeastern-highlands/v9", "eastern-corn-belt-plains/v7",
            "southeastern-plains/v1")
FIELDS = ("completed_metrics", "curve_review", "discipline_function_mapping", "metric_config",
          "region_of_applicability", "function_coverage_exceptions", "predictor_config",
          "reference_build", "session_name")


def _opened(version: str):
    """The version restored into a fresh state the way the Open dialog restores
    it, with its origin recorded."""
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


def _entries(bundle) -> dict:
    return {str(m["metricId"]): m for b in bundle.get("metricsByFunction") or []
            for m in b.get("metrics") or []}


def _fitted_ids(fields) -> set:
    from streamcurves.deep_export import deep_slug
    reference = set(pe.reference_keys(fields.get("reference_build")))
    return {"spring-" + deep_slug(mk) for mk in fields.get("completed_metrics") or {}
            if mk not in reference}


@pytest.mark.parametrize("version", VERSIONS)
def test_an_untouched_republish_states_every_fitted_annotation_again(version):
    state, published, fields = _opened(version)
    rebuilt = ap.build_bundle_from_state(
        state, meta={"sourceCitation": published.get("sourceCitation")})
    got, want = _entries(rebuilt), _entries(published)
    fitted = _fitted_ids(fields) & set(want)
    if not fitted:
        pytest.skip(f"{version} fitted no curve of its own")
    for mid in fitted:
        for key in pe.FITTED_ANNOTATION_KEYS:
            assert got[mid].get(key) == want[mid].get(key), (mid, key)


@pytest.mark.parametrize("version", VERSIONS)
def test_the_republish_of_an_untouched_version_has_its_content_digest(version):
    """Nothing the build stated is lost, and every curve sits where and in the order
    the build put it (carried curves by the session's rows, fixed criteria in the
    criteria's order, fitted curves sorted), so the content is the published
    content and the publish page can say so."""
    state, published, _fields = _opened(version)
    rebuilt = ap.build_bundle_from_state(
        state, meta={"sourceCitation": published.get("sourceCitation")})
    assert lib.content_digest(rebuilt) == published["contentDigest"]


def test_a_changed_curve_does_not_keep_the_annotations_of_the_one_it_replaced():
    state, published, fields = _opened("interior-plateau/v6")
    stored = pe.fitted_annotations_of(published, fields["completed_metrics"])
    mk = sorted(stored)[0]
    changed = copy.deepcopy(stored)
    changed[mk]["curve"]["points"] = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    bundle = copy.deepcopy(published)
    for m in _entries(bundle).values():
        for key in pe.FITTED_ANNOTATION_KEYS:
            m.pop(key, None)
    carried = pe.carry_fitted_annotations(bundle, changed)
    assert mk not in carried and set(carried) == set(stored) - {mk}
    # and a curve whose review changed since the opening is left alone too
    bundle2 = copy.deepcopy(published)
    for m in _entries(bundle2).values():
        for key in pe.FITTED_ANNOTATION_KEYS:
            m.pop(key, None)
    assert pe.carry_fitted_annotations(bundle2, stored, metrics=set()) == []


def test_a_review_changed_since_opening_is_not_unchanged():
    state, _published, fields = _opened("interior-plateau/v6")
    review = dict(fields["curve_review"])
    assert ap.unchanged_reviews(state) == set(map(str, review))
    mk = sorted(review)[0]
    review[mk] = {**review[mk], "decision": "finalized", "decision_note": "looked again"}
    state.curve_review.set(review)
    assert mk not in ap.unchanged_reviews(state)


def test_a_new_session_keeps_the_fitted_annotations_itself():
    bundle = {"metricsByFunction": [{"functionId": "f", "metrics": [
        {"metricId": "spring-phab-xembed", "curve": {"points": [{"x": 0, "y": 1}]},
         "referenceN": 34, "confidenceLabel": "Low", "curveCaveats": ["c"]}]}]}
    result = {"reference_method": pe.METHOD, "meta": {"metricAnnotations": {}},
              "curve_review": {"phab_XEMBED": {}}, "bundle": bundle}
    build = pe.session_reference_build(result)
    saved = build["fittedAnnotations"]["phab_XEMBED"]
    assert saved["referenceN"] == 34 and saved["curve"]["points"] == [{"x": 0, "y": 1}]
    # and it survives the session file
    back = sio.decode_session_fields(json.loads(sio.dumps_session(sio.dump_session_fields(
        {"reference_build": build}))))
    assert back["reference_build"]["fittedAnnotations"] == build["fittedAnnotations"]


# --------------------------------------------------------------------------- #
# standing decisions pending the owner
# --------------------------------------------------------------------------- #
PENDING = f"standing-policy:cov01-documented-gap {dec.PENDING_SUFFIX}"


def test_the_confirm_helpers_rewrite_only_pending_markers():
    approvals = [{"functionId": "a", "approvedBy": PENDING},
                 {"functionId": "b", "approvedBy": "gtmenichino", "note": "carried"}]
    assert dec.pending_approvals(approvals) == [approvals[0]]
    assert dec.confirm_approvals(approvals, maintainer="owner", date="2026-09-22") == 1
    assert approvals[0]["approvedBy"] == "owner" and approvals[0]["confirmedAt"] == "2026-09-22"
    assert approvals[1] == {"functionId": "b", "approvedBy": "gtmenichino", "note": "carried"}
    gaps = [{"functionId": "c", "recordedBy": PENDING}, {"functionId": "d", "recordedBy": "me"}]
    assert dec.pending_exceptions(gaps) == [gaps[0]]
    assert dec.confirm_exceptions(gaps, maintainer="owner", date="2026-09-22") == 1
    assert gaps[0]["recordedBy"] == gaps[0]["confirmedBy"] == "owner"
    assert gaps[1] == {"functionId": "d", "recordedBy": "me"}


def test_the_publish_form_names_what_it_asks_the_owner_to_confirm():
    state = AppState.fresh()
    state.function_coverage_exceptions.set(
        [{"functionId": "carbon-processing", "recordedBy": PENDING}])
    state.assessment_source.set({"kind": "staged", "portfolio_approvals": [
        {"functionId": "low-flow-baseflow-dynamics", "approvedBy": PENDING}]})
    pending = ap.pending_standing_decisions(state)
    assert len(pending["approvals"]) == 1 and len(pending["exceptions"]) == 1
    text = ap.pending_decisions_text(pending)
    assert "Carbon processing" in text and "Low flow" in text and chr(8212) not in text


def test_step_6_confirms_pending_decisions_by_name_and_refuses_a_marker_left_over():
    """The handler confirms under the publisher, asks first, and runs promote's last
    check (source pins: the handler needs a live session to run)."""
    src = (APP / "views" / "publish.py").read_text(encoding="utf-8")
    assert '"pub_confirm_pending"' in src
    assert "dec.confirm_exceptions(exceptions, maintainer=maintainer" in src
    assert "dec.confirm_approvals(meta[\"portfolioApprovals\"], maintainer=maintainer" in src
    assert "a standing decision is still marked pending owner" in src
    batch = (APP / "scripts" / "run_region_batch.py").read_text(encoding="utf-8")
    assert "dec.confirm_approvals(approvals" in batch and "dec.confirm_exceptions(entries" in batch


def test_a_fitted_curve_left_in_no_function_keeps_what_the_build_stated():
    """A fitted curve SELECT-04 left in no function has no bundle entry, so its
    build-only keys ride in the session without a signature. An owner's include
    (REF-15) published from the workspace states them while the curve's review is
    unchanged, as the build that applies the include does (review of 2026-09-22)."""
    from streamcurves.deep_export import deep_slug
    stated = {"referenceN": 21, "sampleDisposition": "adequate", "metricRole": "response",
              "curveCaveats": ["A caveat."], "confidenceLabel": "Moderate",
              "confidenceTotal": 7, "referenceRange": [0.0, 1.0]}
    result = {"reference_method": pe.METHOD, "meta": {}, "fixed_metrics": {},
              "ladder_metrics": {}, "base_metric_annotations": {"fish_X": dict(stated)},
              "curve_review": {"fish_X": {}}, "bundle": {"metricsByFunction": []}}
    build = pe.session_reference_build(result)
    assert build["fittedAnnotations"]["fish_X"] == {"curve": None, **stated}
    entry = {"metricId": "spring-" + deep_slug("fish_X"),
             "curve": {"points": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]}}
    bundle = {"metricsByFunction": [{"functionId": "population-support", "metrics": [entry]}]}
    # nothing vouches for a curve with no signature unless its review is unchanged
    assert pe.carry_fitted_annotations(bundle, build["fittedAnnotations"]) == []
    assert "confidenceLabel" not in entry
    assert pe.carry_fitted_annotations(bundle, build["fittedAnnotations"],
                                       metrics=["fish_X"]) == ["fish_X"]
    assert {k: entry[k] for k in stated} == stated

"""A region with no least-disturbed station of its own can still publish (2026-09-20).

Methodology 0.12 widens a metric's pool to the Level II and then the Level I parent
when the Level III ecoregion holds too few reference stations (REF-05). The Eastern
Corn Belt Plains is the extreme case: all 45 of its candidates fail the fixed pressure
screen, so every one of its 16 curves is borrowed. The publish readiness item asked for
retained sites inside the region, which refused exactly the assessment borrowing exists
for, with a message about screening that no amount of screening could satisfy.

The item now asks what it means to ask, that the curves rest on screened least-disturbed
stations. These tests pin the wiring, from the reference build through the snapshot to
the checklist.
"""
from __future__ import annotations

from shiny import reactive  # noqa: F401  (AppState needs shiny importable)

from streamcurves import run_state as rs
from views import assessment_publish as ap
from views.state import AppState

ECBP_BUILD = {
    "method": "pressure-screen",
    "referenceMethod": {"nLocalReference": 0, "nCandidates": 45,
                        "nCurvesLocal": 0, "nCurvesBorrowed": 16, "nWithheld": 11},
}


def _state(build=ECBP_BUILD, *, n_excluded=45) -> AppState:
    st = AppState.fresh()
    st.reference_build.set(build)
    st.easi_screening_sites.set(
        [{"site_id": f"INLS-{i}", "final_decision": "excluded"} for i in range(n_excluded)])
    return st


def _screening_item(snap: dict) -> dict:
    return next(i for i in rs.readiness_checklist(snap) if i["key"] == "screening")


def test_the_snapshot_carries_the_borrowed_curve_count():
    snap = ap.run_snapshot(_state())
    assert snap["has_screening"] is True and snap["n_retained"] == 0
    assert snap["n_borrowed_curves"] == 16


def test_a_run_without_a_pressure_build_counts_nothing_borrowed():
    """The easi-eci method leaves no referenceMethod block, and its legacy
    behaviour must not change: no borrowed curves, so the item still wants
    retained sites."""
    snap = ap.run_snapshot(_state(build=None))
    assert snap["n_borrowed_curves"] == 0
    assert _screening_item(snap)["ok"] is False


def test_the_checklist_lets_the_wholly_borrowed_region_through():
    assert _screening_item(ap.run_snapshot(_state()))["ok"] is True


def test_a_region_with_no_reference_at_all_is_still_refused():
    """Nothing retained and nothing borrowed is a run with no reference behind it."""
    build = {"referenceMethod": dict(ECBP_BUILD["referenceMethod"], nCurvesBorrowed=0)}
    assert _screening_item(ap.run_snapshot(_state(build=build)))["ok"] is False


def test_the_label_no_longer_promises_retained_sites():
    """The old label named the one thing this region can never have."""
    assert "retained sites" not in _screening_item(ap.run_snapshot(_state()))["label"]

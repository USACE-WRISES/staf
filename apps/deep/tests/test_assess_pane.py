"""The assessment block on the Basin step.

DEEP used to spend a whole wizard step asking which detailed assessment to use, then
a second panel below the cards restating the same answer. A point resolves to one
candidate almost every time, so Basin now adopts it and states it, and the picker is
only reached when the choice is real.

These cover the two pieces where that can go wrong: the adopt rule (adopting reloads
the bundle, which clears every measured value) and what the block actually says.
"""
from __future__ import annotations

import re

import app
from deep import assessments


def _text(tag) -> str:
    """Rendered text of a UI tag, tags stripped."""
    return re.sub(r"<[^>]+>", " ", str(tag))


def _bundle(*, name="Northeastern Highlands reference assessment", n_functions=20,
            n_metrics=2, version=4, lifecycle=None, tier="least_disturbed",
            region="Northeastern Highlands", declared=None) -> dict:
    b = {
        "assessmentId": "northeastern-highlands",
        "assessmentName": name,
        "referenceTier": tier,
        "region": {"name": region, "code": "58", "kind": "ecoregion"},
        "library": {"version": version, "updatedAt": "2026-08-21T23:32:32Z"},
        "metricsByFunction": [
            {"functionId": f"fn-{i}",
             "metrics": [{"metricId": f"m-{i}-{j}"} for j in range(n_metrics)]}
            for i in range(n_functions)
        ],
    }
    if lifecycle:
        b["library"]["lifecycle"] = lifecycle
    if declared is not None:
        b["functionCoverage"] = declared
    return b


def _loaded(**kw):
    return assessments.LoadedAssessment.from_dict(_bundle(**kw))


def _entry(assessment_id: str, versions: int):
    refs = [f"{assessment_id}@v{v}" for v in range(versions, 0, -1)]
    return {"assessmentId": assessment_id, "assessmentName": assessment_id,
            "regionName": assessment_id, "defaultRef": refs[0], "refs": refs,
            "lifecycleByRef": {r: "preliminary" for r in refs},
            "versionByRef": {r: int(r.split("v")[-1]) for r in refs},
            "referenceTierByRef": {}, "hasCertified": False}


# --------------------------------------------------------------------------- #
# _ref_to_adopt: the guard on wiping measured values
# --------------------------------------------------------------------------- #
def test_the_first_candidate_is_adopted():
    """covering_refs sorts certified-bearing first, so position 0 is the answer."""
    covering = [_entry("northeastern-highlands", 4), _entry("nh-sqt-adapted", 1)]
    assert app._ref_to_adopt(None, covering) == "northeastern-highlands@v4"


def test_an_existing_choice_is_never_overwritten():
    """The rule that keeps a delineation from silently erasing entered values, and
    keeps a ?assessment= link or a restored session from being overridden."""
    covering = [_entry("northeastern-highlands", 4)]
    assert app._ref_to_adopt("nh-sqt-adapted@v1", covering) is None


def test_nothing_is_adopted_when_nothing_covers_the_point():
    assert app._ref_to_adopt(None, []) is None


def test_an_entry_without_a_default_ref_is_not_adopted():
    entry = _entry("northeastern-highlands", 1)
    entry["defaultRef"] = ""
    assert app._ref_to_adopt(None, [entry]) is None


# --------------------------------------------------------------------------- #
# What the resolved block says
# --------------------------------------------------------------------------- #
def test_the_block_states_the_assessment_version_counts_and_tier():
    out = _text(app._assessment_pane_block(
        _loaded(), "northeastern-highlands@v4", can_change=False))
    assert "Northeastern Highlands reference assessment" in out
    assert "v4" in out
    assert "40 metrics" in out
    assert "20 of 20 functions" in out
    assert "Reference tier: Least disturbed" in out
    assert "preliminary" in out


def test_undeclared_coverage_is_labelled_rather_than_implied_complete():
    """A 12-function bundle with no coverage block covers 12 of 20, not 12 of 12."""
    out = _text(app._assessment_pane_block(
        _loaded(n_functions=12), "x@v1", can_change=False))
    assert "12 of 20 functions (not declared)" in out


def test_documented_exclusions_are_counted_as_documented():
    declared = {"framework": "staf-20", "total": 20, "covered": 12, "excluded": 8,
                "missing": 0, "exclusions": [{"functionId": "reach-inflow"}]}
    out = _text(app._assessment_pane_block(
        _loaded(n_functions=12, declared=declared), "x@v1", can_change=False))
    assert "12 of 20 functions (8 documented)" in out


def test_a_certified_bundle_reads_certified():
    out = _text(app._assessment_pane_block(
        _loaded(lifecycle="certified"), "x@v2", can_change=False))
    assert "certified" in out and "preliminary" not in out


def test_the_best_available_caveat_survives_the_move():
    """The old detail panel carried this sentence; losing it would overstate what a
    fallback-tier score means."""
    out = _text(app._assessment_pane_block(
        _loaded(tier="best_available"), "x@v1", can_change=False))
    assert "best remaining streams" in out


def test_the_caveat_is_absent_for_a_normal_tier():
    out = _text(app._assessment_pane_block(_loaded(), "x@v1", can_change=False))
    assert "best remaining streams" not in out


# --------------------------------------------------------------------------- #
# Change, and the site-coverage caution
# --------------------------------------------------------------------------- #
def test_change_is_offered_when_the_choice_is_real():
    out = _text(app._assessment_pane_block(_loaded(), "x@v1", can_change=True))
    assert "Change" in out


def test_change_is_hidden_when_there_is_nothing_to_change_to():
    out = _text(app._assessment_pane_block(_loaded(), "x@v1", can_change=False))
    assert "Change" not in out


def test_a_linked_assessment_that_misses_the_site_is_flagged():
    """A ?assessment= link or a restored session can name a region the site is not
    in; scoring against it silently would be the worse failure."""
    out = _text(app._assessment_pane_block(
        _loaded(), "x@v1", can_change=True, covers_site=False))
    assert "does not contain your site" in out


def test_no_caution_when_the_assessment_covers_the_site():
    out = _text(app._assessment_pane_block(_loaded(), "x@v1", can_change=True))
    assert "does not contain your site" not in out


# --------------------------------------------------------------------------- #
# The blocked state
# --------------------------------------------------------------------------- #
def test_the_blocked_state_says_what_is_wrong_and_where_coverage_is():
    out = _text(app._no_assessment_block())
    assert "No assessment covers this point" in out
    assert "shaded regions on the map" in out


def test_the_blocked_state_names_no_other_tool():
    """StreamCurves is not public, so the empty state must not send anyone there."""
    out = _text(app._no_assessment_block())
    assert "StreamCurves" not in out and "Stream Curves" not in out


def test_the_blocked_state_uses_no_em_dashes():
    assert "—" not in _text(app._no_assessment_block())


def test_a_failed_load_still_offers_the_picker():
    """covering is non-empty but nothing loaded means load_ref raised, not that the
    point is uncovered."""
    out = _text(app._no_assessment_block(has_candidates=True))
    assert "No assessment loaded" in out
    assert "Choose" in out


# --------------------------------------------------------------------------- #
# The step list itself
# --------------------------------------------------------------------------- #
def test_deep_has_the_same_four_steps_as_easi_and_sfari():
    assert [label for _, label in app.STEP_LABELS] == [
        "Identify", "Basin", "Assessment", "Report"]


def test_no_step_id_survives_for_the_retired_region_stage():
    assert not hasattr(app, "STEP_ASSESS")
    assert "assess" not in [key for key, _ in app.STEP_LABELS]


# --------------------------------------------------------------------------- #
# _session_ref: a restored run has to claim its ref, or the next delineation
# adopts something else and wipes the values that were just restored.
# --------------------------------------------------------------------------- #
def test_a_v2_session_names_its_ref_from_provenance():
    state = {"provenance": {"assessmentId": "northeastern-highlands", "version": 4}}
    assert app._session_ref(state, {}) == "northeastern-highlands@v4"


def test_a_migrated_session_falls_back_to_the_embedded_bundle():
    """v1 files carry no provenance block; the bundle still names both parts."""
    raw = {"assessmentId": "interior-plateau", "library": {"version": 1}}
    assert app._session_ref({}, raw) == "interior-plateau@v1"


def test_an_unversioned_bundle_still_yields_an_id():
    raw = {"assessmentId": "interior-plateau"}
    assert app._session_ref({}, raw) == "interior-plateau"


def test_an_unnameable_session_yields_none_rather_than_a_bare_at_sign():
    assert app._session_ref({}, {}) is None
    assert app._session_ref({"provenance": {"version": 3}}, {}) is None


def test_a_restored_ref_blocks_adoption():
    """The two halves have to compose: _session_ref feeds selected_ref, and a set
    selected_ref is what stops _resolve_assessment from replacing the assessment."""
    ref = app._session_ref(
        {"provenance": {"assessmentId": "interior-plateau", "version": 1}}, {})
    assert app._ref_to_adopt(ref, [_entry("northeastern-highlands", 4)]) is None


# --------------------------------------------------------------------------- #
# The step navigator.
#
# _stepper is rendered from two different outputs (the left pane on Identify and
# Basin, the worksheet rail on Assessment and Report). They are mutually exclusive
# in principle, but they are separate Shiny outputs applied independently within one
# flush, so on an Assessment -> Basin move both were briefly in the DOM at once and
# Shiny reported four duplicate input ids. Plain data-step anchors carry no input id
# at all, which is the same fix EASI and SFARI already made.
# --------------------------------------------------------------------------- #
def _steps(active: str) -> str:
    return str(app._stepper(active))


def test_the_stepper_registers_no_shiny_input_ids():
    """The guard on the duplicate-id bug. Reintroducing input_action_link fails here."""
    html = _steps("basin")
    assert "id=" not in html
    assert "action-button" not in html and "action-link" not in html


def test_every_step_is_addressable_by_data_step():
    html = _steps("basin")
    for key, _ in app.STEP_LABELS:
        assert f'data-step="{key}"' in html
    assert html.count("data-step=") == len(app.STEP_LABELS)


def test_steps_before_the_active_one_read_done_and_later_ones_do_not():
    anchors = re.findall(r"<a[^>]*>", _steps("measure"))
    classes = {re.search(r'data-step="([^"]+)"', a).group(1):
               re.search(r'class="([^"]+)"', a).group(1) for a in anchors}
    assert classes == {"identify": "easi-step done", "basin": "easi-step done",
                       "measure": "easi-step active", "report": "easi-step"}


def test_exactly_one_step_is_marked_current_for_screen_readers():
    for key, _ in app.STEP_LABELS:
        html = _steps(key)
        assert html.count('aria-current="step"') == 1
        anchor = next(a for a in re.findall(r"<a[^>]*>", html) if "aria-current" in a)
        assert f'data-step="{key}"' in anchor


def test_the_steps_stay_reachable_from_the_keyboard():
    """input_action_link rendered href="#", which is what made these tab-focusable.
    A bare anchor is not, so the markup has to say so."""
    html = _steps("basin")
    assert html.count('tabindex="0"') == len(app.STEP_LABELS)
    assert html.count('role="button"') == len(app.STEP_LABELS)

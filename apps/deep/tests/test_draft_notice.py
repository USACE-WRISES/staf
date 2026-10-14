"""Drafts hidden unless asked, and a notice before DEEP uses one (owner, 2026-10-14).

A Draft is for research and regional assessment development only. Its regions start hidden on
the Identify map (the legend's Draft row is off), the Basin step adopts one on its own only while
Draft is on, and every way an assessment comes into use (the automatic pick, Use the Draft, the
Change dialog, a link, Open) shows the notice first, once per visit. These compile the server's
own functions from app.py, as test_streamcat_readiness does, so they test the real code.
"""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from deep import session
from deep._vendor.staf_workbook.model.scenarios import ScenarioSet
from test_streamcat_readiness import Value, app, function, import_scope

DRAFT_RAW = {"assessmentId": "zz-draft", "assessmentName": "Zz Draft reference assessment",
             "library": {"version": 2}, "lifecycle": "draft", "contentDigest": "sha256:zz",
             "metricsByFunction": []}


def _entry(aid, life, version=1):
    ref = f"{aid}@v{version}"
    return {"assessmentId": aid, "assessmentName": f"{aid} assessment", "regionName": aid,
            "defaultRef": ref, "refs": [ref], "lifecycleByRef": {ref: life},
            "versionByRef": {ref: version}, "referenceTierByRef": {}}


def _gate_scope(**extra):
    shown, removed, cancelled = [], [], []
    scope = {**vars(app),
             "_draft": {"accepted": False, "pending": None, "asked": None},
             "_cancel_report": lambda: cancelled.append(1),
             "ui": SimpleNamespace(modal_show=lambda m: shown.append(str(m)),
                                   modal_remove=lambda: removed.append(1),
                                   notification_show=lambda *a, **k: None),
             "shown": shown, "removed": removed, "cancelled": cancelled}
    scope.update(extra)
    function("_through_draft_notice", scope)
    function("_draft_accepted", scope)
    return scope


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_a_draft_waits_for_its_notice_and_one_acceptance_lasts_the_visit():
    scope = _gate_scope()
    ran = []
    assert scope["_through_draft_notice"](DRAFT_RAW, lambda: ran.append("first")) is False
    assert ran == [] and len(scope["shown"]) == 1 and scope["cancelled"] == [1]
    notice = scope["shown"][0]
    assert "Draft assessment" in notice and "Zz Draft reference assessment" in notice
    assert session.status_note("draft") in notice and "Use the Draft" in notice
    assert 'data-bs-backdrop="static"' in notice, "Escape and the backdrop do not dismiss it"
    scope["_draft_accepted"]()
    assert ran == ["first"] and scope["removed"] == [1] and scope["_draft"]["accepted"]
    # accepted once: the next Draft goes straight in
    assert scope["_through_draft_notice"](DRAFT_RAW, lambda: ran.append("second")) is True
    assert ran == ["first", "second"] and len(scope["shown"]) == 1


def test_a_preliminary_or_final_assessment_never_asks():
    scope = _gate_scope()
    ran = []
    for life in ("preliminary", "certified"):
        assert scope["_through_draft_notice"]({**DRAFT_RAW, "lifecycle": life},
                                              lambda: ran.append(life)) is True
    assert ran == ["preliminary", "certified"] and scope["shown"] == []


def test_cancel_leaves_nothing_pending_to_run_later():
    scope = _gate_scope()
    ran = []
    scope["_through_draft_notice"](DRAFT_RAW, lambda: ran.append("old"))
    # Cancel is a plain close; the next notice replaces what was pending
    scope["_through_draft_notice"](DRAFT_RAW, lambda: ran.append("new"))
    scope["_draft_accepted"]()
    assert ran == ["new"]


# --------------------------------------------------------------------------- #
# the automatic pick at Basin
# --------------------------------------------------------------------------- #
def test_the_pick_skips_drafts_while_they_are_hidden():
    draft, prelim = _entry("zz-draft", "draft"), _entry("zz-prelim", "preliminary")
    assert app._ref_to_adopt(None, [draft]) is None
    assert app._ref_to_adopt(None, [draft], drafts=True) == "zz-draft@v1"
    assert app._ref_to_adopt(None, [prelim, draft]) == "zz-prelim@v1"
    assert app._ref_to_adopt(None, [draft, prelim]) == "zz-prelim@v1"
    assert app._ref_to_adopt("chosen@v1", [prelim]) is None


def _resolve_scope(covering, *, drafts=False, generation=3):
    loads = []
    scope = {**vars(app),
             "reactive": SimpleNamespace(isolate=nullcontext),
             "_current_delineation": lambda: True, "_covering_here": lambda: covering,
             "selected_ref": Value(None), "drafts_shown": Value(drafts),
             "_map_pick": {"generation": generation},
             "_draft": {"accepted": False, "pending": None, "asked": None},
             "_load_ref_into_state": lambda ref, quiet=False: loads.append(ref), "loads": loads}
    function("_resolve_assessment", scope)
    return scope


def test_a_draft_only_site_adopts_nothing_while_drafts_are_hidden():
    scope = _resolve_scope([_entry("zz-draft", "draft")])
    scope["_resolve_assessment"]()
    assert scope["loads"] == []


def test_a_shown_draft_is_asked_about_once_per_site():
    scope = _resolve_scope([_entry("zz-draft", "draft")], drafts=True)
    scope["_resolve_assessment"]()
    scope["_resolve_assessment"]()                 # the effect runs again: no second notice
    assert scope["loads"] == ["zz-draft@v1"]
    scope["_map_pick"]["generation"] = 4           # a new site asks again
    scope["_resolve_assessment"]()
    assert scope["loads"] == ["zz-draft@v1", "zz-draft@v1"]


def test_a_preliminary_site_is_adopted_whatever_the_legend_shows():
    scope = _resolve_scope([_entry("zz-prelim", "preliminary")])
    scope["_resolve_assessment"]()
    assert scope["loads"] == ["zz-prelim@v1"]


def test_basin_offers_the_draft_when_only_drafts_cover_the_site():
    one = str(app._draft_pending_block(_entry("zz-draft", "draft", 3)))
    assert "zz-draft assessment" in one and "v3" in one and "DRAFT" not in one   # CSS uppercases
    assert ">Draft</span>" in one and session.status_note("draft") in one
    assert "use_draft" in one and "Use the Draft" in one and "is-draft" in one
    several = str(app._draft_pending_block(_entry("zz-draft", "draft"), several=True))
    assert "change_assessment" in several and ">Choose<" in several and "use_draft" not in several


# --------------------------------------------------------------------------- #
# Open: today's status, then the notice
# --------------------------------------------------------------------------- #
def _draft_file(tmp_path, raw=None):
    block = app.assessment_file.scenarios_block(ScenarioSet(), {"measured_values": {"m": {"value": 1}}})
    text = session.dump({"delineation": {"snapped_lat": 43.69, "snapped_lon": -72.2785}},
                        raw or DRAFT_RAW, block)
    path = tmp_path / "draft.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_open_waits_for_the_notice_and_cancel_keeps_the_work_on_screen(tmp_path):
    scope = import_scope(tmp_path, {})
    path = _draft_file(tmp_path)
    shown = []
    scope.update(input=SimpleNamespace(load_session=lambda: [{"datapath": str(path)}]),
                 _draft={"accepted": False, "pending": None, "asked": None},
                 _cancel_report=lambda: None,
                 ui=SimpleNamespace(modal_show=lambda m: shown.append(str(m)), modal_remove=lambda: None,
                                    notification_show=lambda *a, **k: None,
                                    notification_remove=lambda *a: None))
    scope.pop("_through_draft_notice")
    function("_through_draft_notice", scope)
    function("_draft_accepted", scope)
    before = scope["loaded_assessment"]()
    function("_load_session", scope)()
    assert shown and "Draft assessment" in shown[0]
    assert scope["loaded_assessment"]() is before, "nothing changes until the Draft is accepted"
    scope["_draft_accepted"]()
    assert scope["loaded_assessment"]().assessment_id == "zz-draft"


def test_a_file_takes_the_status_its_version_has_today(monkeypatch):
    rec = {**DRAFT_RAW, "lifecycle": "preliminary", "assessmentRef": "zz-draft@v2", "version": 2}
    monkeypatch.setattr(app.config, "load_ref", lambda ref: rec if ref == "zz-draft@v2" else None)
    state = {"assessment": DRAFT_RAW, "provenance": {"assessmentId": "zz-draft", "version": 2}}
    assert app._with_current_lifecycle(state)["assessment"]["lifecycle"] == "preliminary"
    # other content: the file keeps its own status
    other = {**rec, "contentDigest": "sha256:other"}
    monkeypatch.setattr(app.config, "load_ref", lambda ref: other)
    assert app._with_current_lifecycle(state)["assessment"]["lifecycle"] == "draft"


# --------------------------------------------------------------------------- #
# wiring
# --------------------------------------------------------------------------- #
def test_every_way_in_goes_through_the_gate():
    src = (app.Path(app.__file__)).read_text(encoding="utf-8")

    def body(name):
        return src.split(f"def {name}(", 1)[1].split("\n    def ", 1)[0]
    assert "return _through_draft_notice(la.raw, _adopt)" in body("_load_ref_into_state")
    assert "_through_draft_notice(la.raw, _open)" in body("_ingest_url_params")
    assert "_through_draft_notice(st.get(\"assessment\") or {}" in body("_load_session")
    assert "@reactive.event(input.coverage_drafts)" in src
    assert "_load_ref_into_state(drafts[0][\"defaultRef\"])" in body("_use_draft")
    js = (app.Path(app.__file__).parent / "www" / "coverage.js").read_text(encoding="utf-8")
    assert 'NS.id(NS.tool(TOOL), "coverage_drafts")' in js
    assert "—" not in str(app._draft_notice("A", 1))


@pytest.mark.parametrize("life,label", [("draft", "Draft"), ("preliminary", "Preliminary"),
                                         ("certified", "Final")])
def test_the_worksheet_line_names_the_assessment_its_version_and_its_status(life, label):
    name = "Klamath Mountains/California High North Coast Range reference assessment"   # 72 characters
    html = str(app._assessment_line(name, 4, life))
    assert f'title="{name}"' in html and ">v4</span>" in html and f">{label}</span>" in html
    assert f'data-status="{life}"' in html and f'title="{session.status_note(life)}"' in html
    assert "deep-ws-ver" not in str(app._assessment_line("A", None, life))
    heading = str(app._report_heading(SimpleNamespace(assessment_name="A", raw={"lifecycle": life})))
    assert "deep-report-pill" in heading and f">{label}</span>" in heading
    assert "deep-report-pill" not in str(app._report_heading(None))

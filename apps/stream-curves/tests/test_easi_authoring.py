"""Authoring an EASI method in StreamCurves: revisions, origin, decisions, stages, preview.

Only a draft revision is edited; the version it came from is always recoverable from the
project alone (the origin bytes of each changed file ride in the project) and verified by
its package digest. An analytical change flags its function until a person confirms the
selection, and a version never reaches the library with a flag open. The consequences
preview scores the draft and its origin in worker processes. The EASI page's handlers are
guarded and its inputs namespaced, like every other page's.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib

import pytest

from streamcurves import library as lib
from streamcurves import run_state as rs
from streamcurves._vendor.easi import method_package as mp
from streamcurves.easi_method import edit, evaluate, io as eio, register as reg, stages as es
from streamcurves.easi_method.model import EasiProject

APP = pathlib.Path(__file__).resolve().parents[1]
REPO = APP.parent.parent
EASI_APP = REPO / "apps" / "easi"
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"
RELEASE_METHOD = "b2e3033116e3"
ROAD = "road-density-inflow-pressure"


@pytest.fixture(scope="module")
def cases():
    if eio.easi_source(REPO) is None:
        pytest.skip("apps/easi is not present")
    return eio.export_cases(EASI_APP)


@pytest.fixture(scope="module")
def imported(cases):
    return eio.import_from_easi(VENDORED_DATA, imported_by="test", cases=cases)


@pytest.fixture
def plain():
    """The unchanged method without preview cases (fast; no EASI checkout needed)."""
    return eio.import_from_easi(VENDORED_DATA, imported_by="test", cases={"cases": []})


def _edge(project, value=1.5):
    return edit.set_band_edge(project, ROAD, None, 0, value, by="tester",
                              reason="experimental test edit, not a method change")


def test_only_a_draft_revision_is_edited(plain):
    with pytest.raises(edit.EditError, match="start a revision"):
        _edge(plain)
    draft = eio.fork(plain, by="tester")
    assert draft.is_revision() and not plain.is_revision()
    assert _edge(draft).package_digest != plain.package_digest


def test_the_origin_rides_in_the_project_and_verifies(plain):
    draft = eio.fork(plain, by="tester")
    assert draft.base == {} and draft.origin_verified()
    edited = _edge(draft)
    assert set(edited.base) == {"screening-methods.json", "scoring-identity.json"}
    assert edited.origin_files() == plain.files and edited.origin_verified()
    back = EasiProject.from_parts(edited.to_parts())
    assert back.base == edited.base and back.files == edited.files and back.origin_verified()
    # moving the edge back returns every byte, the identity included, to the origin
    undone = _edge(edited, 1)
    assert undone.files == plain.files and undone.base == {}
    assert undone.package_digest == plain.package_digest
    assert undone.method_version() == RELEASE_METHOD


def test_a_damaged_origin_copy_is_refused(plain):
    parts = _edge(eio.fork(plain, by="tester")).to_parts()
    parts["easi/base/screening-methods.json"] = b"{}"
    with pytest.raises(ValueError, match="origin copy damaged"):
        EasiProject.from_parts(parts)


def test_every_stored_crossing_is_where_its_knots_cross_the_breaks(plain):
    """The editor recomputes a changed curve's crossings; this pins that it computes them
    exactly as the 34 frozen curves record them."""
    n = 0
    for name, s in plain.curves()["sets"].items():
        for stratum, c in s["curves"].items():
            for key, t in (("x39", 0.39), ("x69", 0.69)):
                assert edit.crossing(c["points"], t) == pytest.approx(c[key], abs=2e-6), \
                    (name, stratum, key)
            n += 1
    assert n == 34


def test_a_curve_edit_recomputes_its_crossings_and_moves_only_that_curve(plain):
    draft = eio.fork(plain, by="tester")
    c = draft.curves()["sets"]["flow-variability"]["curves"]["CPL"]
    pts = [list(p) for p in c["points"]]
    pts[2][0] = round(pts[2][0] * 1.1, 6)                     # the 0.7 knot moves right
    edited = edit.set_curve_points(draft, "flow-variability", "CPL", pts, by="tester",
                                   reason="experimental test edit")
    new = edited.curves()["sets"]["flow-variability"]["curves"]["CPL"]
    assert new["x69"] == edit.crossing(pts, 0.69) and new["x69"] != c["x69"]
    assert not mp.validate_files(edited.files)
    d = edit.diff(draft, edited)
    assert [(x["set"], x["stratum"]) for x in d["curves"]] == [("flow-variability", "CPL")]
    assert [r["functionId"] for r in reg.needs_review(edited)] == ["low-flow-baseflow-dynamics"]
    with pytest.raises(edit.EditError, match="cross both condition breaks"):
        edit.set_curve_points(draft, "flow-variability", "CPL", [[0, 1.0], [5, 0.5]],
                              by="tester", reason="never reaches 0.39")


def test_a_person_confirms_a_changed_selection_and_the_history_stays(plain):
    edited = _edge(eio.fork(plain, by="tester"))
    assert [r["functionId"] for r in reg.needs_review(edited)] == ["reach-inflow"]
    with pytest.raises(ValueError, match="say why"):
        reg.confirm_selection(edited, "reach-inflow", by="tester", reason=" ", at="2026-09-23T00:00:00Z")
    done = reg.confirm_selection(edited, "reach-inflow", by="tester",
                                 reason="reviewed the preview", at="2026-09-23T00:00:00Z")
    assert not reg.needs_review(done)
    decs = [d for d in done.register["decisions"] if d["functionId"] == "reach-inflow"]
    assert [d["decidedBy"] for d in decs] == ["imported", "person"]
    assert decs[-1]["supersedes"] == decs[0]["decisionId"] and decs[-1]["who"] == "tester"
    assert done.history[-1]["action"] == "confirm_selection"
    assert reg.needs_review(edited)                     # the original is untouched


def test_the_strip_statuses_follow_the_work(plain):
    def status(p, **kw):
        return {k: v["status"] for k, v in es.stage_status(es.snapshot(p, **kw)).items()}

    s = status(plain)
    assert s["curves"] == rs.STAGE_DONE and s["publish"] == rs.STAGE_READY
    draft = eio.fork(plain, by="tester")
    s = status(draft)
    assert s["curves"] == rs.STAGE_READY and s["publish"] == rs.STAGE_BLOCKED
    edited = _edge(draft)
    s = status(edited)
    assert s["curves"] == rs.STAGE_ATTENTION and s["selection"] == rs.STAGE_ATTENTION
    assert s["publish"] == rs.STAGE_BLOCKED
    done = reg.confirm_selection(edited, "reach-inflow", by="tester", reason="ok",
                                 at="2026-09-23T00:00:00Z")
    s = status(done, preview={"packageDigest": done.package_digest})
    assert s["curves"] == rs.STAGE_DONE and s["selection"] == rs.STAGE_DONE
    assert s["publish"] == rs.STAGE_READY
    s = status(done, published=True)
    assert s["publish"] == rs.STAGE_DONE
    assert set(es.STAGE_SHORT) == set(es.STAGE_KEYS) == set(es.STAGE_LABELS)


@pytest.fixture
def library_root(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def test_a_version_never_reaches_the_library_with_a_selection_unconfirmed(plain, library_root):
    assert eio.publish(plain, author="tester") == 1
    reopened = eio.open_version(eio.ASSESSMENT_ID, 1)
    edited = _edge(eio.fork(reopened, by="tester"))
    with pytest.raises(ValueError, match="confirm the changed selections"):
        eio.publish(edited, author="tester")
    done = reg.confirm_selection(edited, "reach-inflow", by="tester", reason="reviewed",
                                 at="2026-09-23T00:00:00Z")
    assert eio.publish(done, author="tester", consequences={"casesChanged": 3}) == 2
    prov = json.loads((lib.version_dir(eio.ASSESSMENT_ID, 2) / lib.PROVENANCE_FILE)
                      .read_text(encoding="utf-8"))
    assert prov["consequences"] == {"casesChanged": 3}
    stored = lib.easi_version_files(eio.ASSESSMENT_ID, 2)["project"]
    assert stored["register"] == done.register and "base" in stored


def test_the_canonical_library_keeps_its_publish_gate(plain, library_root, monkeypatch):
    monkeypatch.setattr(lib, "is_canonical_root", lambda: True)
    monkeypatch.delenv("STAF_LIBRARY_PUBLISH", raising=False)
    with pytest.raises(RuntimeError, match="STAF_LIBRARY_PUBLISH"):
        eio.publish(plain, author="tester")
    assert not (library_root / "assessments").exists()


def test_save_and_reopen_keep_the_origin_the_decisions_and_the_library_record(plain, tmp_path):
    edited = reg.confirm_selection(_edge(eio.fork(plain, by="tester")), "reach-inflow",
                                   by="tester", reason="reviewed", at="2026-09-23T00:00:00Z")
    path = eio.write_project(edited, tmp_path / "EASI.streamcurves", name="EASI",
                             meta={"project_id": "p-1", "ui": {"easi_stage": "curves"}},
                             origin_files={"meta.json": b"{}"})
    proj, back = eio.read_project(path)
    assert proj.format_version == 2 and proj.meta["project_id"] == "p-1"
    assert proj.meta["ui"]["easi_stage"] == "curves" and proj.origin == {"meta.json": b"{}"}
    assert back.files == edited.files and back.base == edited.base
    assert back.register == edited.register and back.history == edited.history
    assert back.origin_verified()


def test_the_preview_scores_a_draft_against_its_origin_in_workers(imported, tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMCURVES_DATA_ROOT", str(tmp_path / "data"))
    draft = _edge(eio.fork(imported, by="tester"))
    out = evaluate.preview(draft)
    assert out["cases"] == len(imported.cases["cases"]) and out["casesChanged"] > 0
    assert set(out["byMetric"]) == {"reach-inflow-concentrated-runoff-stormwater-inputs"}
    assert out["identity"]["base"]["methodVersion"] == RELEASE_METHOD
    assert out["identity"]["draft"]["packageDigest"] == draft.package_digest
    assert out["packageDigest"] == draft.package_digest
    again = evaluate.preview(draft)                     # both results come from the cache
    assert again["byMetric"] == out["byMetric"] and again["seconds"] < out["seconds"]
    cache = list((tmp_path / "data" / "cache" / "easi-previews").glob("*.json"))
    assert len(cache) == 2


def test_the_easi_page_helpers_cover_the_method(plain):
    from views import easi_page as ep
    assert len(ep.curve_list(plain)) == 34
    rules = ep.band_rules(plain)
    assert any(r["methodKey"] == ROAD and r["input"] is None for r in rules)
    assert len(ep.regional_rules(plain)) == 18            # TN and TP in nine regions
    users = ep.curve_users(plain)
    assert set(users) == set(plain.curves()["sets"])
    edited = _edge(eio.fork(plain, by="tester"))
    assert [ep.describe(h, ep.names_of(edited)) for h in ep.history_since_origin(edited)] == [
        "Reach inflow: band edge 1 from 1 to 1.5"]


# --------------------------------------------------------------------------- #
# the page's handlers and inputs, like every other page's
# --------------------------------------------------------------------------- #
_VIEWS = APP / "views"


def test_no_easi_page_handler_can_close_the_session():
    tree = ast.parse(io.open(_VIEWS / "easi_page.py", encoding="utf-8").read())
    unguarded = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            decs = [ast.unparse(d) for d in node.decorator_list]
            if any("reactive.effect" in d for d in decs) and not any(d.startswith("guard(") for d in decs):
                unguarded.append(node.name)
    assert unguarded == []


def test_no_input_built_inside_the_easi_page_has_a_bare_id():
    tree = ast.parse(io.open(_VIEWS / "easi_page.py", encoding="utf-8").read())
    server = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "easi_page_server")
    bare = [node.args[0].value for node in ast.walk(server)
            if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("ui.input_")
            and node.args and isinstance(node.args[0], ast.Constant)]
    assert bare == []


def test_the_strip_routes_the_easi_stages():
    src = io.open(_VIEWS / "stagebar.py", encoding="utf-8").read()
    dispatch = src[src.index("def _jump():"):]
    assert "es.TARGET_PREFIX" in dispatch and "state.easi_stage.set(key)" in dispatch
    assert all(es.stage_target(k).startswith(es.TARGET_PREFIX) for k in es.STAGE_KEYS)


def _road_rule(project):
    m = next(x for x in project.catalog()["methods"] if x["methodKey"] == ROAD)
    return m, sorted(m["bands"], key=lambda b: float("-inf") if b.get("min") is None else b["min"])


def test_a_moved_edge_keeps_the_labels_and_plot_marks_easi_shows(plain):
    draft = eio.fork(plain, by="tester")
    m, bands = _road_rule(_edge(draft))
    assert [b["label"] for b in bands][:2] == ["<1.5 km/km\u00b2", "1.5-<3 km/km\u00b2"]
    assert m["breakpoints"][0]["label"] == "1.5 km/km\u00b2"
    # a new owner rewrites both labels in the catalog's style
    owned = edit.set_band_edge(draft, ROAD, None, 0, 1.5, owner="lower", by="tester",
                               reason="experimental test edit")
    _, bands = _road_rule(owned)
    assert [b["label"] for b in bands][:2] == ["\u22641.5 km/km\u00b2", ">1.5-<3 km/km\u00b2"]
    assert bands[0]["maxInclusive"] and not bands[1]["minInclusive"]


def test_a_count_scale_has_no_shared_edge_to_move(plain):
    draft = eio.fork(plain, by="tester")
    with pytest.raises(edit.EditError, match="do not share an edge"):
        edit.set_band_edge(draft, "nas-established-taxa-count", None, 0, 0.5, by="tester",
                           reason="experimental test edit")


def test_a_revision_of_an_older_version_takes_the_librarys_next_number(plain):
    draft = eio.fork(plain, by="tester", version=3)
    assert draft.meta["version"] == 3 and draft.meta["lineage"]["origin"]["version"] == 1
    assert eio.fork(plain, by="tester", version=1).meta["version"] == 2
    ident = json.loads(_edge(draft).files["scoring-identity.json"].decode("utf-8"))
    assert ident["alternative_id"] == "easi-screening-v3"

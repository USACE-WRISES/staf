"""EASI method projects in StreamCurves: import, export, edit, diff and evaluation.

The unchanged EASI method (release ``b2e3033116e3``) must survive import and export
byte for byte; a draft edit must change exactly what it says; and evaluation of any
method version must happen in a worker process, never in the app's own copy of EASI,
which DEEP builds read (pressure screen, CURVE-11 criteria, CWA mapping).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from streamcurves._vendor.easi import method_package as mp
from streamcurves.easi_method import edit, evaluate, io as eio, register as reg
from streamcurves.easi_method.model import EasiProject

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent.parent
EASI_APP = REPO / "apps" / "easi"
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"
RELEASE_METHOD = "b2e3033116e3"


@pytest.fixture(scope="module")
def cases(tmp_path_factory):
    """The preview case set, exported by EASI's own script (checkout only)."""
    if not (EASI_APP / "scripts" / "export_preview_cases.py").is_file():
        pytest.skip("apps/easi is not present")
    out = tmp_path_factory.mktemp("cases") / "preview-cases.json"
    proc = subprocess.run([sys.executable, "-B", "scripts/export_preview_cases.py", str(out)],
                          cwd=EASI_APP, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(out.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def project(cases):
    return eio.import_from_easi(VENDORED_DATA, imported_by="test", cases=cases)


def test_import_keeps_every_method_file_byte_for_byte(project):
    for name in mp.METHOD_FILES:
        assert project.files[name] == (VENDORED_DATA / name).read_bytes()
        if (EASI_APP / "data" / name).is_file():
            assert project.files[name] == (EASI_APP / "data" / name).read_bytes()
    assert project.method_version() == RELEASE_METHOD
    assert project.meta["lineage"]["importedFrom"]["methodVersion"] == RELEASE_METHOD
    assert project.meta["geography"]["kind"] == "national"


def test_export_is_the_package_easi_loads_and_is_deterministic(project):
    blob, ident = eio.export_zip(project)
    assert eio.export_zip(project)[0] == blob
    pkg = mp.read_package(blob)
    assert pkg.files == project.files
    assert ident["methodVersion"] == RELEASE_METHOD
    assert mp.check_identity(pkg)["methodVersion"] == RELEASE_METHOD


def test_project_parts_round_trip(project):
    back = EasiProject.from_parts(project.to_parts())
    assert back.files == project.files and back.meta == project.meta
    assert back.register == project.register and back.cases == project.cases


def test_the_register_selects_the_operational_method_of_every_function(project):
    rows = reg.status_rows(project)
    assert len(rows) == 20
    assert all(r["selectedCandidate"] and r["decidedBy"] == "imported" for r in rows)
    assert not any(r["needsReview"] for r in rows)


def test_unedited_files_reserialize_to_the_same_bytes(project):
    for name in ("screening-methods.json", "reference-curves.json"):
        obj = json.loads(project.files[name].decode("utf-8"))
        assert edit.dump_like(project.files[name], obj) == project.files[name]


def _road_edit(project):
    cat = project.catalog()
    m = next(x for x in cat["methods"] if x["methodKey"] == "road-density-inflow-pressure")
    edges = edit.band_edges(m["bands"])
    assert edges[0]["value"] == 1
    return edit.set_band_edge(project, "road-density-inflow-pressure", None, 0, 1.5,
                              by="test", reason="experimental test edit, not a method change")


def test_an_analytical_edit_moves_only_its_function_and_flags_review(project):
    draft = _road_edit(project)
    assert draft.package_digest != project.package_digest
    assert draft.method_version() != RELEASE_METHOD
    assert project.method_version() == RELEASE_METHOD  # the base is untouched
    d = edit.diff(project, draft)
    # the catalog, and the identity file restamped to name the draft and its hashes
    assert d["filesChanged"] == ["scoring-identity.json", "screening-methods.json"]
    assert d["analytical"]
    assert not mp.validate_files(draft.files)
    assert [m["methodKey"] for m in d["methods"]] == ["road-density-inflow-pressure"]
    flagged = [r["functionId"] for r in reg.status_rows(draft) if r["needsReview"]]
    assert flagged == ["reach-inflow"]
    assert draft.calculator is None and draft.history[-1]["kind"] == "analytical"


def test_a_display_edit_is_not_analytical(project):
    draft = edit.set_text(project, "road-density-inflow-pressure", "title", "Road density",
                          by="test", reason="wording")
    d = edit.diff(project, draft)
    assert not d["analytical"] and d["methods"][0]["display"]
    assert not any(r["needsReview"] for r in reg.status_rows(draft))


def test_workers_score_the_exported_baseline_like_the_builtin_method(project, cases):
    builtin = evaluate.run_cases(None, cases)
    exported = evaluate.run_cases(eio.consumer_package(project), cases)
    assert builtin["identity"]["methodVersion"] == RELEASE_METHOD
    assert exported["identity"]["methodVersion"] == RELEASE_METHOD
    assert exported["results"] == builtin["results"]
    assert len(builtin["results"]) == len(cases["cases"])


def test_a_draft_preview_changes_only_the_edited_function(project, cases):
    base = evaluate.run_cases(eio.consumer_package(project), cases)
    draft = evaluate.run_cases(eio.consumer_package(_road_edit(project)), cases)
    cmp_ = evaluate.compare(base, draft)
    assert cmp_["casesChanged"] > 0
    assert set(cmp_["byMetric"]) == {"reach-inflow-concentrated-runoff-stormwater-inputs"}
    assert draft["identity"]["methodVersion"] != RELEASE_METHOD


def test_the_apps_own_easi_stays_the_operational_method(project, cases):
    evaluate.run_cases(eio.consumer_package(_road_edit(project)), cases)
    from streamcurves._vendor.easi import config
    from streamcurves._vendor.easi.national import method_version
    assert method_version() == RELEASE_METHOD
    assert Path(config.DATA_DIR).resolve() == VENDORED_DATA.resolve()
    assert mp.active()["source"] == "built-in"


# --------------------------------------------------------------------------- #
# project files, the vendored copy's guard and the process environment
# --------------------------------------------------------------------------- #
def test_an_easi_project_is_format_2_and_round_trips(project, tmp_path):
    from streamcurves import project_file as pf
    path = eio.write_project(project, tmp_path / "EASI.streamcurves", name="EASI")
    proj, back = eio.read_project(path)
    assert proj.format_version == 2 and proj.assessment_type == "easi"
    assert back.files == project.files and back.register == project.register
    assert back.cases == project.cases and back.meta == project.meta
    deep = pf.build_bytes(meta={"project_name": "d"}, session_text=pf.session_text_from_fields(
        {}, session_name="d"))
    dp = pf.read_project(deep)
    assert dp.format_version == 1 and dp.assessment_type == "deep" and not dp.parts


def test_streamcurves_1_0_0_refuses_an_easi_project(project, tmp_path):
    """The reader StreamCurves 1.0.0 shipped (a frozen copy under tests/legacy) must refuse
    a format-2 file with its own "update the app" message, never open it."""
    import importlib.util
    from streamcurves import project_file as pf
    legacy_path = APP / "tests" / "legacy" / "project_file_1_0_0.py"
    spec = importlib.util.spec_from_file_location("streamcurves.legacy_project_file_1_0_0", legacy_path)
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = legacy          # its dataclasses look their module up while defined
    try:
        spec.loader.exec_module(legacy)
    finally:
        sys.modules.pop(spec.name, None)
    path = eio.write_project(project, tmp_path / "EASI.streamcurves", name="EASI")
    with pytest.raises(legacy.ProjectFileError, match="Update the app"):
        legacy.read_project(path)
    assert legacy.read_project(pf.build_bytes(
        meta={"project_name": "d"},
        session_text=pf.session_text_from_fields({}, session_name="d"))).format_version == 1


def _vendored_method_version(env_extra: dict) -> str:
    import os
    env = {k: v for k, v in os.environ.items()
           if k not in ("EASI_DATA_DIR", "EASI_METHOD_PACKAGE", "EASI_CRITERIA_SET",
                        "STREAMCURVES_EASI_WORKER")}
    env.update(env_extra)
    code = ("from streamcurves._vendor.easi.national import method_version; "
            "print(method_version())")
    proc = subprocess.run([sys.executable, "-B", "-c", code], cwd=APP, env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-1500:]
    return proc.stdout.strip().splitlines()[-1]


def test_the_vendored_copy_uses_a_package_only_in_a_worker(project, tmp_path):
    draft = _road_edit(project)
    zpath = tmp_path / "draft.zip"
    zpath.write_bytes(eio.export_zip(draft)[0])
    cache = str(tmp_path / "cache")
    plain = _vendored_method_version({"EASI_METHOD_PACKAGE": str(zpath), "EASI_METHOD_CACHE": cache})
    worker = _vendored_method_version({"EASI_METHOD_PACKAGE": str(zpath), "EASI_METHOD_CACHE": cache,
                                       "STREAMCURVES_EASI_WORKER": "1"})
    assert plain == RELEASE_METHOD
    assert worker == draft.method_version() != RELEASE_METHOD


def test_sanitize_clears_the_easi_switches_except_in_a_worker():
    from streamcurves import easi_env
    env = {"EASI_METHOD_PACKAGE": "x", "EASI_DATA_DIR": "y", "EASI_CRITERIA_SET": "legacy", "OTHER": "1"}
    assert sorted(easi_env.sanitize(env)) == sorted(easi_env.EASI_SWITCHES)
    assert env == {"OTHER": "1"}
    env = {"EASI_METHOD_PACKAGE": "x", easi_env.WORKER_ENV: "1"}
    assert easi_env.sanitize(env) == [] and "EASI_METHOD_PACKAGE" in env


def test_fork_records_its_origin_and_restamps_the_identity(project):
    draft = eio.fork(project, by="test")
    assert draft.meta["version"] == 2 and draft.meta["lineage"]["origin"]["version"] == 1
    assert draft.files == project.files     # an unedited fork is the same method
    edited = _road_edit(draft)
    ident = json.loads(edited.files["scoring-identity.json"].decode("utf-8"))
    assert ident["alternative_id"] == "easi-screening-v2"
    assert ident["derived_from"]["alternative_id"] == "alternative-2"
    assert not mp.validate_files(edited.files)

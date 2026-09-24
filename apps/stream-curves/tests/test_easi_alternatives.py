"""The alternatives an EASI method was chosen from, as candidates a draft can select.

The study's alternatives enter the register only where they differ from the method, with
the study named by the hashes of its receipts and the owner's choice recorded as imported.
A draft adopts one per function; functions that read one curve family move together, and
adopting the method the draft started from puts its exact bytes back. The register keeps
every decision through a save and reopen.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

from streamcurves.easi_method import alternatives as A
from streamcurves.easi_method import io as eio
from streamcurves.easi_method import register as reg

APP = pathlib.Path(__file__).resolve().parents[1]
VENDORED_DATA = APP / "streamcurves" / "_vendor" / "easi" / "data"
REAL_STUDY = pathlib.Path("D:/Data/easi-national/review/alternative-studies/"
                          "2026-09-15-controlled-alternatives")
FILES = ("screening-methods.json", "reference-curves.json", "easi-metrics.json", "cwa-mapping.json")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _shift(curves: dict, name: str, factor: float) -> None:
    for c in curves["sets"][name]["curves"].values():
        c["points"] = [[round(x * factor, 6), y] for x, y in c["points"]]


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    """A small study in the real one's layout: Alternative 2 is the method itself; 1 moves
    the woody and flow curves, 3 the natural corridor curves, 4 only the flow curves."""
    root = tmp_path_factory.mktemp("study")
    base = json.loads((VENDORED_DATA / "reference-curves.json").read_text(encoding="utf-8"))
    edits = {"alternative-1": [("corridor-woody", 1.1), ("flow-variability", 0.9)],
             "alternative-2": [], "alternative-3": [("corridor-natural", 1.2)],
             "alternative-4": [("flow-variability", 0.9)]}
    manifest, hashes = {"alternatives": []}, {}
    for alt, moves in edits.items():
        folder = root / "candidates" / alt / "app-data"
        folder.mkdir(parents=True)
        for n in FILES:
            shutil.copyfile(VENDORED_DATA / n, folder / n)
        curves = json.loads(json.dumps(base))
        for name, factor in moves:
            _shift(curves, name, factor)
        if moves:
            (folder / "reference-curves.json").write_text(json.dumps(curves, indent=1), encoding="utf-8")
        cb, kb = (folder / "reference-curves.json").read_bytes(), (folder / "screening-methods.json").read_bytes()
        hashes[f"candidates/{alt}/app-data/reference-curves.json"] = _sha(cb)
        manifest["alternatives"].append({"id": alt, "label": f"Test {alt}", "artifact_sha256": _sha(cb),
                                         "catalog_sha256": _sha(kb), "curve_count": 34, "changes": "test"})
    (root / "candidates" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "completion.json").write_text(json.dumps({"output_hashes": hashes}), encoding="utf-8")
    (root / "review-report.md").write_text(
        "**Recommendation: alternative-1.** Retain Alternative 1.\n\n"
        "| Candidate | Eligible | Reason or unresolved review |\n| --- | --- | --- |\n"
        "| alternative-3 | No | Test finding three. |\n| alternative-4 | No | Test finding four. |\n",
        encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def plain():
    return eio.import_from_easi(VENDORED_DATA, imported_by="test", cases={"cases": []})


@pytest.fixture(scope="module")
def imported(plain, study):
    return A.import_alternatives(plain, study, imported_by="Maintainer", at="2026-09-23T00:00:00Z",
                                 completion_sha256=None)


def _key(project, fid, source):
    for c in project.register["candidates"]:
        ref = c["identity"]["sourceRef"]
        if c["identity"]["functionId"] == fid and source in (ref.get("alternative"), ref.get("criteriaSet"),
                                                               "origin" if ref.get("importedFrom") else None):
            return c["candidateKey"]
    raise KeyError((fid, source))


def test_an_alternative_is_a_candidate_only_where_it_differs(plain, imported):
    study_cands = [c for c in imported.register["candidates"] if c["identity"]["sourceRef"].get("study")]
    by_alt: dict = {}
    for c in study_cands:
        by_alt.setdefault(c["identity"]["sourceRef"]["alternative"], set()).add(c["identity"]["functionId"])
    assert by_alt == {"alternative-1": {"light-thermal-regime", "habitat-provision",
                                        "low-flow-baseflow-dynamics"},
                      "alternative-3": {"carbon-processing"},
                      "alternative-4": {"low-flow-baseflow-dynamics"}}
    legacy = [c for c in imported.register["candidates"] if c["identity"]["sourceRef"].get("criteriaSet")]
    assert len(legacy) == 8 and all(c["identity"]["sourceKind"] == "imported_alternative" for c in legacy)
    assert imported.package_digest == plain.package_digest      # the register never moves the method


def test_the_import_records_the_owners_choice_as_imported_and_the_study_by_hash(imported, study):
    rows = {r["functionId"]: r for r in reg.status_rows(imported)}
    low = rows["low-flow-baseflow-dynamics"]
    assert low["decidedBy"] == "imported" and not low["needsReview"]
    statuses = {r["candidate"]["identity"]["sourceRef"].get("alternative") or
                r["candidate"]["identity"]["sourceRef"].get("criteriaSet") or "origin": r["status"]
                for r in low["rows"]}
    # the study's report marks Alternative 4 not eligible under its rule: excluded, with the finding
    assert statuses == {"origin": "selected", "alternative-1": "eligible_not_selected",
                        "alternative-4": "excluded", "legacy": "eligible_not_selected"}
    decisions = {d["candidateKey"]: d for d in imported.register["decisions"]}
    a4 = decisions[_key(imported, "low-flow-baseflow-dynamics", "alternative-4")]
    assert a4["decidedBy"] == "imported" and a4["when"] == "2026-09-16" and a4["rule"] == A.STUDY_RULE
    assert "Test finding four." in a4["reason"] and "Alternative 2" in a4["reason"]
    s = imported.register["studies"][0]
    assert s["recommendation"] == "alternative-1" and s["adopted"] == "alternative-2"
    assert s["completionSha256"] == _sha((study / "completion.json").read_bytes())
    assert A.import_alternatives(imported, study, imported_by="Maintainer", at="2026-09-23T00:00:00Z",
                                 completion_sha256=None).register["candidates"] == \
        imported.register["candidates"]


def test_a_study_that_is_not_the_recorded_one_is_refused(plain, study, tmp_path):
    with pytest.raises(A.AlternativeError, match="not the completed"):
        A.import_alternatives(plain, study, imported_by="Maintainer")
    bad = tmp_path / "bad"
    shutil.copytree(study, bad)
    (bad / "candidates" / "alternative-3" / "app-data" / "reference-curves.json").write_text("{}")
    with pytest.raises(A.AlternativeError, match="not the bytes"):
        A.import_alternatives(plain, bad, imported_by="Maintainer", completion_sha256=None)


def test_adopting_moves_the_curve_family_together_and_going_back_restores_every_byte(imported):
    with pytest.raises(A.AlternativeError, match="start a revision"):
        A.adopt(imported, _key(imported, "habitat-provision", "alternative-1"), by="Owner",
                reason="the woody Level II curves for this draft")
    draft = eio.fork(imported, by="Owner")
    woody = _key(draft, "habitat-provision", "alternative-1")
    with pytest.raises(A.AlternativeError, match="name"):
        A.adopt(draft, woody, by="", reason="the woody Level II curves for this draft")
    with pytest.raises(A.AlternativeError, match="at least 20"):
        A.adopt(draft, woody, by="Owner", reason="short")
    moved = [m["functionId"] for m in A.affected(draft, woody)]
    assert sorted(moved) == ["habitat-provision", "light-thermal-regime"]
    adopted = A.adopt(draft, woody, by="Owner", reason="the woody Level II curves for this draft",
                      at="2026-09-23T01:00:00Z")
    rows = {r["functionId"]: r for r in reg.status_rows(adopted)}
    for fid in ("habitat-provision", "light-thermal-regime"):
        assert rows[fid]["decidedBy"] == "person" and rows[fid]["who"] == "Owner"
        assert rows[fid]["selectedCandidate"] == _key(adopted, fid, "alternative-1")
        assert not rows[fid]["needsReview"]
    assert rows["low-flow-baseflow-dynamics"]["decidedBy"] == "imported"
    assert adopted.package_digest != draft.package_digest and adopted.calculator is None
    back = A.adopt(adopted, _key(adopted, "light-thermal-regime", "origin"), by="Owner",
                   reason="back to the method this draft started from", at="2026-09-23T02:00:00Z")
    assert back.package_digest == draft.package_digest and back.base == {}
    assert not reg.needs_review(back)
    kinds = [(d["candidateKey"], d["decision"]) for d in back.register["decisions"]
             if d["functionId"] == "habitat-provision" and d["decidedBy"] == "person"]
    assert [k for _, k in kinds] == ["selected", "not_selected", "selected", "not_selected"]


def test_a_draft_that_adopted_an_alternative_saves_and_reopens_whole(imported, tmp_path):
    draft = eio.fork(imported, by="Owner")
    adopted = A.adopt(draft, _key(draft, "carbon-processing", "alternative-3"), by="Owner",
                      reason="national references for the natural corridor curves", allow_excluded=True)
    path = eio.write_project(adopted, tmp_path / "EASI.streamcurves", name="EASI")
    _, back = eio.read_project(path)
    assert back.register == adopted.register and back.files == adopted.files
    assert back.package_digest == adopted.package_digest and back.origin_verified()
    rows = {r["functionId"]: r for r in reg.status_rows(back)}
    assert rows["carbon-processing"]["selectedCandidate"] == _key(back, "carbon-processing", "alternative-3")


@pytest.mark.skipif(not (REAL_STUDY / "completion.json").is_file(),
                    reason="the 2026-09-15 alternatives study is not on this machine")
def test_the_real_study_offers_three_alternatives_in_the_four_changed_slots(plain):
    got = A.import_alternatives(plain, REAL_STUDY, imported_by="Maintainer", at="2026-09-23T00:00:00Z")
    study = [c for c in got.register["candidates"] if c["identity"]["sourceRef"].get("study")]
    assert len(study) == 12
    assert {c["identity"]["functionId"] for c in study} == {
        "low-flow-baseflow-dynamics", "light-thermal-regime", "carbon-processing", "habitat-provision"}
    s = got.register["studies"][0]
    assert s["completionSha256"] == A.STUDY_COMPLETION_SHA256
    assert s["recommendation"] == "alternative-1" and s["adopted"] == "alternative-2"


def test_an_sqt_curve_for_an_easi_proxy_is_kept_out_as_field_against_desktop(plain):
    from streamcurves import sqt_registry
    if not sqt_registry.available():
        pytest.skip("the SQT registry is not built")
    er = sqt_registry.record("sqt:mn:entrenchment-ratio-er:e-streams")
    with pytest.raises(ValueError, match="name"):
        reg.add_sqt_candidate(plain, er, "floodplain-connectivity", by="", at="2026-09-23T00:00:00Z")
    got = reg.add_sqt_candidate(plain, er, "floodplain-connectivity", by="Reviewer", at="2026-09-23T00:00:00Z")
    assert got.package_digest == plain.package_digest            # the method never changes
    row = {r["functionId"]: r for r in reg.status_rows(got)}["floodplain-connectivity"]
    sqt_row = next(x for x in row["rows"] if x["candidate"]["identity"]["sourceKind"] == "sqt")
    assert sqt_row["status"] == "excluded" and sqt_row["decision"]["rule"] == "field-vs-desktop"
    # the exclusion is the rule's outcome; the person is who added the curve
    assert sqt_row["decision"]["decidedBy"] == "automated" and sqt_row["decision"]["who"] is None
    assert sqt_row["candidate"]["addedBy"] == "Reviewer" and row["decidedBy"] == "imported"
    with pytest.raises(ValueError, match="already"):
        reg.add_sqt_candidate(got, er, "floodplain-connectivity", by="Reviewer", at="2026-09-23T00:00:00Z")


def test_a_published_version_keeps_the_register_in_its_provenance_and_out_of_its_method(
        imported, tmp_path, monkeypatch):
    from streamcurves import library as lib
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    assert eio.publish(imported, author="tester") == 1
    prov = json.loads((lib.version_dir(eio.ASSESSMENT_ID, 1) / lib.PROVENANCE_FILE).read_text(encoding="utf-8"))
    rows = prov["candidateRegister"]["rows"]
    assert {r["status"] for r in rows} <= {"selected", "eligible_not_selected", "excluded"}
    assert {"selected", "eligible_not_selected"} <= {r["status"] for r in rows}
    assert prov["candidateRegister"]["studies"][0]["adopted"] == "alternative-2"
    method = lib.version_dir(eio.ASSESSMENT_ID, 1) / "method"
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in method.rglob("*.json"))
    keys = {r["candidateKey"] for r in rows if r["status"] != "selected"}
    assert keys and not any(k in text for k in keys) and "eligible_not_selected" not in text


@pytest.mark.skipif(not (REAL_STUDY / "completion.json").is_file(),
                    reason="the 2026-09-15 alternatives study is not on this machine")
def test_every_real_alternative_adopted_in_a_draft_builds_a_package_easi_accepts(plain):
    """Alternatives 1 and 4 use Level II curve sets and Alternative 3 national-only sets:
    a draft that adopts any of them must still export (review A, H1)."""
    got = A.import_alternatives(plain, REAL_STUDY, imported_by="Maintainer", at="2026-09-23T00:00:00Z")
    draft = eio.fork(got, by="Owner")
    keys = [c["candidateKey"] for c in draft.register["candidates"]
            if c["identity"]["sourceKind"] == "imported_alternative" and c.get("definition")]
    stratifiers = set()
    for key in keys:
        adopted = A.adopt(draft, key, by="Owner", reason="testing that the adopted draft exports",
                          allow_excluded=True)
        pkg = eio.consumer_package(adopted)          # raises when the files do not validate
        stratifiers |= {s.get("stratifier") for s in json.loads(
            pkg.files["reference-curves.json"].decode("utf-8"))["sets"].values()}
    assert {"l2", "national"} <= stratifiers


@pytest.mark.skipif(not (REAL_STUDY / "completion.json").is_file(),
                    reason="the 2026-09-15 alternatives study is not on this machine")
def test_a_level_ii_and_a_national_alternative_score_in_a_worker(plain):
    from streamcurves.easi_method import evaluate
    if eio.easi_source(APP.parent.parent) is None:
        pytest.skip("apps/easi is not present (the preview cases come from it)")
    cases = {"cases": eio.export_cases(APP.parent / "easi")["cases"][:40]}
    got = A.import_alternatives(plain, REAL_STUDY, imported_by="Maintainer", at="2026-09-23T00:00:00Z")
    draft = eio.fork(got, by="Owner")
    for alt in ("alternative-1", "alternative-3"):
        adopted = A.adopt(draft, _key(draft, "habitat-provision", alt), by="Owner",
                          reason="testing that the adopted draft scores", allow_excluded=True)
        out = evaluate.run_cases(eio.consumer_package(adopted), cases)
        assert out["identity"]["packageDigest"] == adopted.package_digest
        assert len(out["results"]) == 40


@pytest.mark.skipif(not (REAL_STUDY / "completion.json").is_file(),
                    reason="the 2026-09-15 alternatives study is not on this machine")
def test_the_studys_own_eligibility_is_recorded(plain):
    got = A.import_alternatives(plain, REAL_STUDY, imported_by="Maintainer", at="2026-09-23T00:00:00Z")
    rows = {r["functionId"]: r for r in reg.status_rows(got)}
    low = {x["candidate"]["identity"]["sourceRef"].get("alternative")
           or x["candidate"]["identity"]["sourceRef"].get("criteriaSet") or "origin": x
           for x in rows["low-flow-baseflow-dynamics"]["rows"]}
    assert low["alternative-3"]["status"] == low["alternative-4"]["status"] == "excluded"
    assert low["alternative-3"]["decision"]["rule"] == A.STUDY_RULE
    assert low["alternative-1"]["status"] == "eligible_not_selected"
    assert any("not eligible under its own rule" in x for x in low["origin"]["candidate"]["limitations"])
    draft = eio.fork(got, by="Owner")
    with pytest.raises(A.AlternativeError, match="excluded"):
        A.adopt(draft, low["alternative-3"]["candidateKey"], by="Owner", reason="an excluded alternative, refused")

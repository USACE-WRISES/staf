"""The study base registry: the 2026-09-15 base keeps its values, the adopted method is a
base of its own, a manifest names its base, and a study never changes it."""
import shutil

import pytest

from builder import REPO_ROOT
from builder.analysis import alternatives as alts
from builder.analysis.alternatives import bases, study
from builder.analysis.alternatives.io import read_json, sha, write_json

ADOPTED = "alternative-2-b2e3033116e3"


def test_the_legacy_constants_are_the_default_base():
    default = bases.base()
    assert default.id == bases.DEFAULT_BASE_ID == "2026-09-15"
    assert (alts.BASE_METHOD, alts.BASE_COMMIT, alts.BASE_REFERENCE) == (
        "e9f472b31fe5", "6cc77c2c3fbf94d7dc9daf71df9dd3a07b56aaa7",
        "ad100b39313af9259bd0628728d50ab87513e2c07955bf24d78e9d107863abdd")
    assert (default.method_version, default.commit, default.reference_sha256) == (
        alts.BASE_METHOD, alts.BASE_COMMIT, alts.BASE_REFERENCE)
    assert default.alternative_id == "alternative-1" and default.curve_count == 62
    assert default.package_digest is None and default.library_version is None


def test_the_adopted_method_is_a_base_with_verified_values():
    adopted = bases.base(ADOPTED)
    assert adopted.method_version == "b2e3033116e3"
    assert adopted.commit.startswith("02f39a8") and len(adopted.commit) == 40
    assert adopted.reference_sha256.startswith("a824e2c2")
    assert adopted.catalog_sha256.startswith("78c1e292")
    assert adopted.alternative_id == "alternative-2" and adopted.curve_count == 34
    assert adopted.package_digest.startswith("sha256:5b733a6b")
    assert adopted.adopted == "2026-09-16"
    assert all(isinstance(note, str) and note for note in adopted.verified)
    with pytest.raises(KeyError, match="unknown study base"):
        bases.base("2026-09-16")


def test_the_adopted_base_is_the_method_this_checkout_ships():
    data = REPO_ROOT / "apps/easi/data"
    adopted = bases.base(ADOPTED)
    assert sha(data / "reference-curves.json") == adopted.reference_sha256
    assert sha(data / "screening-methods.json") == adopted.catalog_sha256
    identity = read_json(data / "scoring-identity.json")
    assert identity["alternative_id"] == adopted.alternative_id
    assert identity["curve_count"] == adopted.curve_count


def test_the_study_version_and_the_protocol_moved_to_1_1_0():
    assert alts.STUDY_VERSION == "1.1.0"
    assert study.protocol()["version"] == "1.1.0"


def test_a_manifest_names_its_base_and_a_1_0_0_manifest_is_the_legacy_base():
    legacy = {"alternative_1": {"method_version": alts.BASE_METHOD, "source_commit": alts.BASE_COMMIT,
                                "frozen_sha": alts.BASE_REFERENCE}, "protocol": {"version": "1.0.0"}}
    assert bases.study_base(legacy).id == "2026-09-15"
    assert bases.study_base({}).id == "2026-09-15"
    assert bases.study_base({"base_id": ADOPTED}).id == ADOPTED
    with pytest.raises(ValueError, match="not base"):
        bases.study_base({"base_id": ADOPTED, "alternative_1": legacy["alternative_1"]})


def test_study_ids_follow_the_documented_pattern():
    assert bases.study_id_ok("2026-09-15-controlled-alternatives")
    assert bases.study_id_ok("2026-10-03-low-flow-alternatives")
    assert bases.study_id_ok("2026-10-14-woody-proxies-alternatives")
    assert not bases.study_id_ok("2026-10-3-low-flow-alternatives")
    assert not bases.study_id_ok("2026-10-03-low-flow")
    assert not bases.study_id_ok("2026-10-32-low-flow-alternatives")
    assert bases.study_id_ok("2026-09-16-controlled-alternatives")
    assert bases.study_id_ok("2026-11-03-low-flow-alternatives")
    assert not bases.study_id_ok("2026-13-03-low-flow-alternatives")
    assert not bases.study_id_ok("Low-Flow-alternatives")


def _study_root(tmp_path, base, monkeypatch):
    """A root and repo mirror where the active method is ``base``."""
    from easi import config, national
    repo = tmp_path / "repo"
    (repo / "apps/easi/easi").mkdir(parents=True)
    (repo / "apps/easi/easi/engine.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "apps/easi/data").mkdir()
    shutil.copyfile(REPO_ROOT / "apps/easi/data/reference-curves.json",
                    repo / "apps/easi/data/reference-curves.json")
    root = tmp_path / "root"
    (root / "analysis").mkdir(parents=True)
    (root / "staging").mkdir()
    write_json(root / "analysis/local-review/completion.json",
               {"status": "complete", "method_version": base.method_version})
    write_json(root / "state/queue.json", {"items": []})
    monkeypatch.setattr(study, "REPO_ROOT", repo)
    monkeypatch.setattr(config, "criteria_set", lambda: "regional")
    monkeypatch.setattr(national, "method_version", lambda: base.method_version)
    return root


def test_a_snapshot_on_the_adopted_base_binds_the_manifest_to_it(tmp_path, monkeypatch):
    adopted = bases.base(ADOPTED)
    root = _study_root(tmp_path, adopted, monkeypatch)
    folder = root / "review/alternative-studies/2026-10-03-low-flow-alternatives"
    result = study.snapshot(root, folder, base_id=ADOPTED)
    assert result["files"] >= 2
    manifest = read_json(folder / "manifest.json")
    assert manifest["base_id"] == ADOPTED and manifest["base"]["method_version"] == "b2e3033116e3"
    assert manifest["alternative_1"] == {"method_version": "b2e3033116e3", "source_commit": adopted.commit,
                                         "frozen_sha": adopted.reference_sha256}
    assert manifest["parent_binding"]["method_version"] == "b2e3033116e3"
    assert manifest["protocol"]["version"] == "1.1.0"
    assert read_json(folder / "snapshot/manifest.json")["source_commit"] == adopted.commit
    assert bases.study_base(manifest).id == ADOPTED


def _library_rows():
    path = REPO_ROOT / "apps/library/assessments/easi-screening/v1/method.json"
    return read_json(path)["identity"]["validatedUnder"]


def test_the_adopted_base_accepts_the_method_versions_the_library_validated(tmp_path, monkeypatch):
    """The base's method files score the same under a later, validated evaluator: the library
    records that evaluator's method version, the base accepts it, and a study run under it
    records the evaluator beside the base. The 2026-09-15 base has no library version."""
    adopted = bases.base(ADOPTED)
    rows = _library_rows()
    versions = [r["methodVersion"] for r in rows]
    assert adopted.method_version in versions
    assert bases.library_validated_versions(adopted, REPO_ROOT) == tuple(dict.fromkeys(versions))
    assert bases.library_validated_versions(bases.base(), REPO_ROOT) == ()
    assert bases.accepted_method_versions(bases.base()) == (bases.base().method_version,)
    assert all(bases.accepts(adopted, v, REPO_ROOT) for v in versions)
    assert not bases.accepts(adopted, "000000000000", REPO_ROOT) and not bases.accepts(adopted, None, REPO_ROOT)
    # a digest the library never validated is refused, the base's own is accepted without a library
    assert bases.accepts(adopted, adopted.method_version) and not bases.accepts(adopted, versions[-1] + "x")
    later = [v for v in versions if v != adopted.method_version]
    if not later:
        pytest.skip("the library records no later evaluator for the adopted base")
    root = _study_root(tmp_path, adopted, monkeypatch)
    from easi import national
    monkeypatch.setattr(national, "method_version", lambda: later[-1])
    # the repo mirror the snapshot reads carries the library's record of the validation
    target = study.REPO_ROOT / "apps/library/assessments/easi-screening/v1/method.json"
    target.parent.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "apps/library/assessments/easi-screening/v1/method.json", target)
    folder = root / "review/alternative-studies/2026-10-04-low-flow-alternatives"
    study.snapshot(root, folder, base_id=ADOPTED)
    manifest = read_json(folder / "manifest.json")
    assert manifest["base_id"] == ADOPTED and manifest["base"]["method_version"] == adopted.method_version
    assert manifest["alternative_1"]["method_version"] == adopted.method_version
    assert manifest["base_evaluator"]["method_version"] == later[-1]
    assert manifest["base_evaluator"]["validated"] is True
    assert manifest["base_evaluator"]["evaluator_digest"].startswith("sha256:")
    assert later[-1] in manifest["base_evaluator"]["accepted_method_versions"]
    assert bases.study_base(manifest).id == ADOPTED
    # an evaluator the library did not validate is still refused
    monkeypatch.setattr(national, "method_version", lambda: "000000000000")
    with pytest.raises(RuntimeError, match="must match"):
        study.snapshot(root, root / "review/alternative-studies/2026-10-05-low-flow-alternatives", base_id=ADOPTED)


def test_a_snapshot_refuses_a_base_the_checkout_does_not_hold(tmp_path, monkeypatch):
    adopted = bases.base(ADOPTED)
    root = _study_root(tmp_path, adopted, monkeypatch)
    folder = root / "review/alternative-studies/2026-10-03-low-flow-alternatives"
    # the checkout ships the adopted curves, so the legacy base's frozen artifact is gone
    with pytest.raises(RuntimeError, match="must match"):
        study.snapshot(root, folder)
    from easi import national
    monkeypatch.setattr(national, "method_version", lambda: alts.BASE_METHOD)
    with pytest.raises(RuntimeError, match="frozen artifact has changed"):
        study.snapshot(root, folder, base_id="2026-09-15")
    assert not (folder / "manifest.json").exists()

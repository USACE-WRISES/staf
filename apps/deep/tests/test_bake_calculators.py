"""The bake step ships each version's Excel calculator, copied and never built.

StreamCurves generates a workbook at publish and records it in the assessment's
append-only ``artifacts.json``. The bake copies the recorded file into
``www/calculators/`` only when it is what the record says it is and was built
from the very content being baked, and writes the index ``deep.calculator``
reads. The script stays pure stdlib. Runs against tmp folders only.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from deep import calculator
from test_bake_library import (_bundle, _catalog_entry, _load_bake_module, _write_catalog,
                               _write_library)

AID = "ecbp"


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def _digest(version: int) -> str:
    return f"sha256:content-v{version}"


def _add_calculator(root, version: int, payload: bytes, *, digest=None, sha=None):
    """A workbook in the version folder with its artifacts.json record. The
    bundle gets the content digest the record names."""
    vdir = root / "assessments" / AID / f"v{version}"
    bundle_path = vdir / "assessment.deep.json"
    bundle = json.loads(bundle_path.read_text("utf-8"))
    bundle["contentDigest"] = _digest(version)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    (vdir / "calculator.xlsx").write_bytes(payload)
    path = root / "assessments" / AID / "artifacts.json"
    doc = json.loads(path.read_text("utf-8")) if path.is_file() else {"history": []}
    doc["history"].append({
        "version": version, "kind": "excel-calculator", "file": "calculator.xlsx",
        "sha256": sha or "sha256:" + hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload), "contentDigest": digest or _digest(version),
        "generatorVersion": "1.0"})
    path.write_text(json.dumps(doc), encoding="utf-8")


def _library(libroot, versions=(1, 2)):
    _write_library(libroot, AID, "ECBP", list(versions))
    _write_catalog(libroot, [_catalog_entry(AID, "ECBP", latest=max(versions),
                                            default=max(versions), prelim=max(versions))])


def test_recorded_workbooks_are_copied_with_an_index(tmp_path, libroot):
    _library(libroot)
    _add_calculator(libroot, 1, b"PK-one")
    _add_calculator(libroot, 2, b"PK-two")
    bake = _load_bake_module()
    result = bake.bake(out=tmp_path / "data")
    www = tmp_path / "www" / "calculators"
    assert result["calculators"] == {"copied": ["ecbp@v1", "ecbp@v2"], "missing": []}
    assert (www / "ecbp@v1.xlsx").read_bytes() == b"PK-one"
    assert (www / "ecbp@v2.xlsx").read_bytes() == b"PK-two"
    assert (www / "ecbp.xlsx").read_bytes() == b"PK-two"            # the default version
    index = json.loads((www / "index.json").read_text("utf-8"))
    assert index["calculators"]["ecbp@v2"] == {
        "file": "ecbp@v2.xlsx", "sha256": "sha256:" + hashlib.sha256(b"PK-two").hexdigest(),
        "bytes": 6, "contentDigest": _digest(2), "generatorVersion": "1.0"}
    assert index["default"] == {"ecbp": "ecbp.xlsx"}


def test_the_runtime_reads_what_the_bake_wrote(tmp_path, libroot):
    _library(libroot, versions=(1,))
    _add_calculator(libroot, 1, b"PK-one")
    _load_bake_module().bake(out=tmp_path / "data")
    calculator.clear_cache()
    baked = json.loads((tmp_path / "data" / "deep-assessments.json").read_text("utf-8"))
    record = baked["assessments"][0]
    www = tmp_path / "www" / "calculators"
    assert calculator.template_for(record, www) == b"PK-one"
    assert calculator.template_for({**record, "contentDigest": "sha256:other"}, www) is None
    calculator.clear_cache()


def test_a_workbook_that_is_not_what_the_record_says_is_not_shipped(tmp_path, libroot):
    _library(libroot, versions=(1, 2, 3))
    _add_calculator(libroot, 1, b"PK-one", sha="sha256:something-else")     # file changed
    _add_calculator(libroot, 2, b"PK-two", digest="sha256:other-curves")    # other content
    # v3 has no record at all
    result = _load_bake_module().bake(out=tmp_path / "data")
    assert result["calculators"] == {"copied": [], "missing": ["ecbp@v1", "ecbp@v2", "ecbp@v3"]}
    www = tmp_path / "www" / "calculators"
    assert not list(www.glob("*.xlsx")) if www.is_dir() else True
    assert not (www / "index.json").exists()


def test_a_reissue_supersedes_the_earlier_record(tmp_path, libroot):
    _library(libroot, versions=(1,))
    _add_calculator(libroot, 1, b"PK-old")
    _add_calculator(libroot, 1, b"PK-new")            # the last record wins, the file is new
    _load_bake_module().bake(out=tmp_path / "data")
    assert (tmp_path / "www" / "calculators" / "ecbp@v1.xlsx").read_bytes() == b"PK-new"


def test_the_folder_is_swept_and_the_bake_is_idempotent(tmp_path, libroot):
    _library(libroot, versions=(1,))
    _add_calculator(libroot, 1, b"PK-one")
    www = tmp_path / "www" / "calculators"
    www.mkdir(parents=True)
    (www / "retired@v9.xlsx").write_bytes(b"stale")
    (www / "README.txt").write_text("kept", encoding="utf-8")
    bake = _load_bake_module()
    first = bake.bake(out=tmp_path / "data")
    snapshot = {p.name: p.read_bytes() for p in www.iterdir()}
    second = bake.bake(out=tmp_path / "data")
    assert first == second
    assert {p.name: p.read_bytes() for p in www.iterdir()} == snapshot
    assert not (www / "retired@v9.xlsx").exists()
    assert (www / "README.txt").read_text("utf-8") == "kept"       # only its own files


def test_an_explicit_www_folder_is_honoured(tmp_path, libroot):
    _library(libroot, versions=(1,))
    _add_calculator(libroot, 1, b"PK-one")
    elsewhere = tmp_path / "elsewhere"
    _load_bake_module().bake(out=tmp_path / "data", www=elsewhere)
    assert (elsewhere / "ecbp@v1.xlsx").is_file()
    assert not (tmp_path / "www").exists()


def test_the_bake_script_stays_pure_stdlib():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts"
           / "bake_library_into_deep.py").read_text(encoding="utf-8")
    assert "openpyxl" not in src.split('"""', 2)[2]
    assert _bundle(AID, 1, "x")["assessmentId"] == AID

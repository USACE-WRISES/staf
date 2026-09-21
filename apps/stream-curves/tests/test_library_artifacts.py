"""The Excel calculator as a recorded artifact of a published version.

Every publish builds the version's calculator and appends a record to the
assessment's ``artifacts.json`` (append-only, beside status.json). The version's
own files, its ``meta.json`` and its content digest never move, a workbook
failure never blocks a publish, a reissue supersedes without erasing, and the
backfill script builds the workbook of a version published before this existed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from streamcurves import deep_calculator
from streamcurves import library as lib
from test_library import REGION, _bundle, _session_payload

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_deep_calculators.py"
META = {"assessmentName": "Eastern Corn Belt Plains", "region": REGION, "author": "publisher"}


@pytest.fixture
def libroot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    (root / "assessments").mkdir(parents=True)
    monkeypatch.setenv("STAF_LIBRARY_ROOT", str(root))
    return root


def _script():
    spec = importlib.util.spec_from_file_location("build_deep_calculators", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_storage_layer_and_the_generator_agree_on_the_file_name():
    assert lib.CALCULATOR_FILE == deep_calculator.FILE_NAME


def test_a_publish_builds_and_records_the_calculator(libroot):
    v = lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    aid = "eastern-corn-belt-plains"
    path = lib.calculator_path(aid, v)
    assert path.is_file() and path.read_bytes()[:2] == b"PK"
    rec = lib.version_artifact(aid, v)
    assert rec["kind"] == lib.CALCULATOR_KIND and rec["file"] == "calculator.xlsx"
    assert rec["sha256"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert rec["bytes"] == path.stat().st_size
    assert rec["contentDigest"] == lib.version_content_digest(aid, v)
    assert rec["generator"] == deep_calculator.GENERATOR
    assert rec["generatorVersion"] == deep_calculator.GENERATOR_VERSION
    assert lib.calculator_state(aid, v) == "present"
    catalog = json.loads((libroot / "catalog.json").read_text("utf-8"))
    assert catalog["assessments"][0]["calculatorState"] == "present"


def test_the_workbook_is_built_from_the_published_bundle(libroot):
    v = lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    aid = "eastern-corn-belt-plains"
    published = lib.load_version_bundle(aid, v)
    assert lib.calculator_path(aid, v).read_bytes() == deep_calculator.build_calculator(published)


def test_recording_a_calculator_never_touches_the_version(libroot):
    v = lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    aid = "eastern-corn-belt-plains"
    vdir = lib.version_dir(aid, v)
    before = {p.name: p.read_bytes() for p in vdir.iterdir() if p.name != lib.CALCULATOR_FILE}
    digest = lib.version_content_digest(aid, v)
    manifest = (lib.manifest_path(aid)).read_bytes()
    assert lib.write_calculator(aid, v) is None                       # present: left alone
    rec = lib.write_calculator(aid, v, actor="gtmenichino", note="Reissued.", reissue=True)
    assert rec and rec["actor"] == "gtmenichino"
    after = {p.name: p.read_bytes() for p in vdir.iterdir() if p.name != lib.CALCULATOR_FILE}
    assert after == before
    assert lib.version_content_digest(aid, v) == digest
    assert lib.manifest_path(aid).read_bytes() == manifest
    history = lib.read_artifacts(aid)["history"]
    assert len(history) == 2                                         # append-only
    assert lib.version_artifact(aid, v)["note"] == "Reissued."      # the last record wins


def test_a_workbook_failure_never_blocks_a_publish(libroot, monkeypatch):
    def boom(_bundle):
        raise RuntimeError("openpyxl said no")
    monkeypatch.setattr(deep_calculator, "build_calculator", boom)
    v = lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    aid = "eastern-corn-belt-plains"
    assert v == 1 and (lib.version_dir(aid, v) / lib.BUNDLE_FILE).is_file()
    assert lib.calculator_state(aid, v) == "absent"
    assert not lib.calculator_path(aid, v).exists()
    catalog = json.loads((libroot / "catalog.json").read_text("utf-8"))
    assert catalog["assessments"][0]["calculatorState"] == "absent"


def test_a_changed_or_missing_workbook_reads_stale(libroot):
    v = lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    aid = "eastern-corn-belt-plains"
    path = lib.calculator_path(aid, v)
    path.write_bytes(path.read_bytes() + b"x")
    assert lib.calculator_state(aid, v) == "stale"
    path.unlink()
    assert lib.calculator_state(aid, v) == "stale"
    assert lib.write_calculator(aid, v) is not None                  # a stale one is rebuilt
    assert lib.calculator_state(aid, v) == "present"


def test_the_backfill_builds_what_is_missing_and_checks(libroot, monkeypatch, capsys):
    real = deep_calculator.build_calculator
    monkeypatch.setattr(deep_calculator, "build_calculator",
                        lambda b: (_ for _ in ()).throw(RuntimeError("not yet")))
    lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    lib.publish_version("Eastern Corn Belt Plains", META, _session_payload(), _bundle())
    monkeypatch.setattr(deep_calculator, "build_calculator", real)
    script = _script()
    assert script.main(["--all", "--check", "--library-root", str(libroot)]) == 1
    assert script.main(["--all", "--library-root", str(libroot)]) == 0
    out = capsys.readouterr().out
    assert "built 2, left alone 0, failed 0" in out
    assert script.main(["--all", "--check", "--library-root", str(libroot)]) == 0
    assert script.main(["--all", "--library-root", str(libroot)]) == 0
    assert "built 0, left alone 2, failed 0" in capsys.readouterr().out
    assert script.main(["--assessment", "eastern-corn-belt-plains", "--version", "1",
                        "--reissue", "--library-root", str(libroot)]) == 0
    history = lib.read_artifacts("eastern-corn-belt-plains")["history"]
    assert [r["version"] for r in history] == [1, 2, 1]
